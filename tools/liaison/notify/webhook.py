"""群通知通道的装配：节流 → 守卫 → 降级 → 发送 → 退避重试 → 落库。

⛔ 本目录禁止写 `with X:`（第 2 章事务扫描器）。⛔ 本模块不 import `time`：
`sleep` 与令牌桶都以参数注入。
"""

from __future__ import annotations

import sqlite3
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from tools.liaison.alerts import AlertSink
from tools.liaison.config import load_group_webhook
from tools.liaison.notify.guard import (
    GROUP_WEBHOOK_CHANNEL,
    MODE_DEGRADED,
    MODE_REJECT,
    NotifyPlan,
    compute_notify_plan,
)
from tools.liaison.notify.ratelimit import (
    MAX_RETRIES,
    RATE_LIMIT_ERRCODE,
    TokenBucket,
    compute_backoff_delay,
)
from tools.liaison.notify.store import (
    GROUP_NOTIFY_THREAD_ID,
    STATE_PENDING_RESEND,
    STATE_SENT,
    NotifyRecord,
    effect_send_group_notify,
)
from tools.liaison.notify.transport import (
    WEBHOOK_TIMEOUT_SECONDS,
    Transport,
    UrllibTransport,
    WebhookResponse,
    WebhookTransportError,
)

_SEND_PATH_SUFFIX = "/send"
_UPLOAD_PATH_SUFFIX = "/upload_media"

#: 企微群机器人的附件上限。**提前拒绝的判据**，⛔ 不是"发出去再看错误码"。
#: 附件是降级投递的第一步，它失败之后提要那条根本不会发——所以一次超限换来的
#: 是整条通知彻底没发出去，而现场只留下一个企微的数字错误码，看不出是"文件太大"。
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


def compute_upload_url(webhook_url: str) -> str:
    """把发送地址换成附件上传地址。**纯函数**。

    企微群机器人的附件上传与发送共用同一个 `key`，只差路径与一个 `type` 参数。
    ⛔ 路径不是以 `/send` 结尾就报错、**不硬拼**——拼错的地址会把附件发去一个
    未知端点，而那是一次带着完整正文的外发。
    ⏸ 该端点本章**未实测**（真发是 8.6，由 Shao Peishen 亲自做）。本章代码只到
    "给了 URL 就能发"为止。
    """
    parsed = urllib.parse.urlsplit(webhook_url)
    if not parsed.path.endswith(_SEND_PATH_SUFFIX):
        raise ValueError(f"群 webhook 地址的路径不是以 {_SEND_PATH_SUFFIX} 结尾，⛔ 不猜")
    path = parsed.path[: -len(_SEND_PATH_SUFFIX)] + _UPLOAD_PATH_SUFFIX
    query = f"{parsed.query}&type=file" if parsed.query else "type=file"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, query, ""))


def compute_markdown_payload(body: str) -> dict:
    """markdown 消息负载。**纯函数**。⛔ 里面没有、也永远不许有收件对象字段。"""
    return {"msgtype": "markdown", "markdown": {"content": body}}


def compute_file_payload(media_id: str) -> dict:
    """文件消息负载。**纯函数**。⛔ 里面没有、也永远不许有收件对象字段。"""
    return {"msgtype": "file", "file": {"media_id": media_id}}


class GroupWebhookSender:
    """本通道**唯一**的外发出口。

    🔴 **结构上没有"发给谁"这个参数。** 构造时只吃一个 webhook 地址，而地址只可能
    来自 `HR_LIAISON_GROUP_WEBHOOK`（内部值守群）；`send_markdown` / `send_file`
    都不接收收件对象。spec 的「不存在以候选人标识为收件对象的参数或调用路径」
    因此不是一句承诺，是一个签名事实——由
    tests/test_notify_boundaries.py 的 AST 断言持续守着。
    """

    def __init__(
        self,
        *,
        webhook_url: str,
        transport: Transport,
        attachment_supported: bool = True,
        timeout: float = WEBHOOK_TIMEOUT_SECONDS,
    ) -> None:
        self._webhook_url = webhook_url
        self._transport = transport
        self._timeout = timeout
        self._attachment_supported = attachment_supported
        self._upload_url = compute_upload_url(webhook_url) if attachment_supported else None

    @property
    def attachment_supported(self) -> bool:
        return self._attachment_supported

    def send_markdown(self, body: str) -> WebhookResponse:
        return self._transport.post_json(
            self._webhook_url, compute_markdown_payload(body), timeout=self._timeout
        )

    def send_file(self, media_id: str) -> WebhookResponse:
        return self._transport.post_json(
            self._webhook_url, compute_file_payload(media_id), timeout=self._timeout
        )

    def publish_attachment(self, *, filename: str, content: str | bytes) -> str:
        """把一份附件上传上去，返回 `media_id`。`content` 收 `str` 与 `bytes` 两种。

        ⛔ 上传失败一律抛 `WebhookTransportError`：上传没成功却继续发提要，
        群里就会出现一条声称"完整正文见附件"却没有附件的通知——那是一句谎。

        ⚠️ `bytes` 那条路上 ⛔ 不许有任何 decode/encode 往返：docx 是 zip，
        往返一次就毁了，而毁掉的 zip 在企微那头只会得到一个语焉不详的错误码。
        """
        if self._upload_url is None:
            raise WebhookTransportError("本通道没有配置附件承载方式")
        # 按**字节数**量，⛔ 不按 len(str)：中文一个字 3 字节，按字符数算会让一份
        # 60MB 的中文正文一路溜到企微那头。
        size = len(content.encode("utf-8") if isinstance(content, str) else content)
        if size > MAX_ATTACHMENT_BYTES:
            raise WebhookTransportError(
                f"附件 {filename} 有 {size} 字节，超过企微上限 "
                f"{MAX_ATTACHMENT_BYTES} 字节（20 MiB），⛔ 提前拒绝、不发出去再看错误码"
            )
        response = self._transport.post_multipart(
            self._upload_url, filename=filename, content=content, timeout=self._timeout
        )
        if not response.ok:
            raise WebhookTransportError(
                f"附件上传失败：errcode={response.errcode} {response.errmsg}"
            )
        media_id = response.payload.get("media_id")
        if not media_id:
            raise WebhookTransportError("附件上传的响应里没有 media_id，⛔ 不当作成功")
        return str(media_id)


class Delivery(Protocol):
    """一次通知要发的消息序列。**带断点**：`send_next` 只推进已送达的那一步。"""

    @property
    def done(self) -> bool: ...

    @property
    def pending_requests(self) -> int:
        """下一次 `send_next()` 会打出去**几次** HTTP 请求。

        🔴 由投递对象**自报**，⛔ 不由 `effect_deliver_with_backoff` 去猜（TD-26 ②）。
        外层猜的写法在降级投递上就错了：第一步实际发两次（上传附件 + 发文件消息），
        外层只扣 1 个令牌，实际以约 1.5 倍配额打服务端。谁发的谁知道发了几次。
        """
        ...

    def send_next(self) -> WebhookResponse: ...


class DirectDelivery:
    """不超限的通知：一条 markdown，发完就完。"""

    def __init__(self, sender: GroupWebhookSender, plan: NotifyPlan) -> None:
        self._sender = sender
        self._plan = plan
        self._index = 0

    @property
    def done(self) -> bool:
        return self._index >= 1

    @property
    def pending_requests(self) -> int:
        """恒为 1：一条 markdown 就是一次 `post_json`。"""
        return 1

    def send_next(self) -> WebhookResponse:
        response = self._sender.send_markdown(self._plan.body)
        if response.ok:
            self._index += 1
        return response


class DegradedDelivery:
    """超限的通知：先附件、后提要，**两条消息、一个断点**。

    🔴 **顺序钉死为「先附件、后提要」，⛔ 不许反。** 反过来一旦附件那条永久失败，
    群里就留下一条声称"完整正文见附件"却没有附件的提要——那是一句谎，而且看不出来。
    先发附件、提要失败，留下的是一份没有说明的文件：难看，但不骗人，且待重发记录
    加告警会把它接住。

    断点：`_index` 只在某一步真的送达之后才前进，因此被限流后的重试 ⛔ 不会把已经
    送达的那条再发一遍（spec 逐字：「不产生重复的通知」）。附件也只上传一次。
    """

    def __init__(self, sender: GroupWebhookSender, plan: NotifyPlan) -> None:
        self._sender = sender
        self._plan = plan
        self._index = 0
        self._media_id: str | None = None

    @property
    def done(self) -> bool:
        return self._index >= 2

    @property
    def pending_requests(self) -> int:
        """第一步 2 次（`post_multipart` 上传 + `post_json` 发文件消息），其余 1 次。

        ⛔ **不许写死"降级永远 2"**：附件只上传一次（断点在 `_media_id` 上），
        重试时那一步只剩 `send_file`。还按 2 扣会白吃掉一半配额，症状是"发得比
        配置的还慢"——没有任何报错，没人会去查。
        """
        if self._index == 0 and self._media_id is None:
            return 2
        return 1

    def send_next(self) -> WebhookResponse:
        if self._index == 0:
            if self._media_id is None:
                self._media_id = self._sender.publish_attachment(
                    filename=self._plan.attachment_filename or "",
                    content=self._plan.attachment_content or "",
                )
            response = self._sender.send_file(self._media_id)
        else:
            response = self._sender.send_markdown(self._plan.body)
        if response.ok:
            self._index += 1
        return response


def make_group_webhook_delivery(sender: GroupWebhookSender, plan: NotifyPlan) -> Delivery:
    """按 plan 的 mode 选投递形态。⛔ `MODE_REJECT` 到不了这里（store 提前短路）。

    🔴 真到了这里就 `raise`，⛔ **不静默降级成 `DirectDelivery`**（TD-27）：reject
    模式下 `plan.body` 为空，静默降级的结果是往群里发一条空 markdown——那比拒发更糟，
    因为它**看起来成功了**。「到不了这里」这个前提原先只由调用方保证；本函数是公开的，
    第二个调用方一出现就没人替它守。现在改由结构自己守住。
    ⛔ 不要为了"容错"把这里改回返回一个投递对象：拒发的正确处置是**根本不投递**，
    由 `store.effect_send_group_notify` 提前短路成 `rejected` 并告警（那条正路不动）。
    """
    if plan.mode == MODE_REJECT:
        raise ValueError(
            "拒发模式不产生投递对象，调用方必须提前短路："
            f"mode={plan.mode} digest={plan.digest} reject_reason={plan.reject_reason}"
        )
    if plan.mode == MODE_DEGRADED:
        return DegradedDelivery(sender, plan)
    return DirectDelivery(sender, plan)


@dataclass(frozen=True)
class DeliveryOutcome:
    delivered: bool
    attempts: int
    last_errcode: int | None = None
    last_error: str | None = None


def effect_deliver_with_backoff(
    delivery: Delivery,
    *,
    bucket: TokenBucket,
    sleep: Callable[[float], None],
    max_retries: int = MAX_RETRIES,
) -> DeliveryOutcome:
    """把一个投递序列发完，被限流就退避重试。

    三条出口：
    - 全部送达 → `delivered=True`；
    - 收到 `RATE_LIMIT_ERRCODE` 且重试次数已用满 → `delivered=False` + 最后的 errcode；
    - 收到**其它** errcode、或传输层抛异常 → `delivered=False`，⛔ **不重试**。

    ⛔ 非限流错误不许重试：那类错误（负载不合法、机器人被移出群、key 失效）重试
    多少次都是同一个结果，重试只会把一次可诊断的失败拖成一串噪音。
    ⚠️ 重试次数按**整条通知**计，不是每条消息各算一份——否则一条降级通知的
    退避上限会悄悄翻倍。
    """
    attempts = 0
    retries = 0
    while not delivery.done:
        # 节流在**发送前**生效（spec 逐字），且**按本步真正要发的请求数**取令牌
        # （TD-26 ②）：降级投递第一步发两次 HTTP，只扣 1 个就是在偷配额。
        for _ in range(delivery.pending_requests):
            bucket.acquire()
        attempts += 1
        try:
            response = delivery.send_next()
        except WebhookTransportError as exc:
            return DeliveryOutcome(
                delivered=False, attempts=attempts, last_errcode=None, last_error=str(exc)
            )
        if response.ok:
            continue
        if response.errcode != RATE_LIMIT_ERRCODE:
            return DeliveryOutcome(
                delivered=False,
                attempts=attempts,
                last_errcode=response.errcode,
                last_error=response.errmsg,
            )
        if retries >= max_retries:
            return DeliveryOutcome(
                delivered=False,
                attempts=attempts,
                last_errcode=response.errcode,
                last_error=response.errmsg,
            )
        retries += 1
        sleep(compute_backoff_delay(retries))
    return DeliveryOutcome(delivered=True, attempts=attempts)


def build_group_webhook_sender(
    *, env=None, transport: Transport | None = None
) -> GroupWebhookSender:
    """从环境读地址造发送器。

    地址缺失 → `MissingCredentialsError`（6.10：拒发并报告缺失的变量名，
    ⛔ 不静默跳过后报成功）。⛔ 不要在这里 try/except 把它吞掉换成"降级到只写日志"
    ——那正是"静默跳过后报成功"。
    """
    return GroupWebhookSender(
        webhook_url=load_group_webhook(env),
        transport=transport if transport is not None else UrllibTransport(),
    )


def send_group_notify(
    conn: sqlite3.Connection,
    *,
    text: str,
    sender: GroupWebhookSender,
    bucket: TokenBucket,
    alert_sink: AlertSink,
    sleep: Callable[[float], None],
    thread_id: str = GROUP_NOTIFY_THREAD_ID,
) -> str | None:
    """本章的入口：算 plan → 发 → 记。返回终局状态；幂等命中时返回 `None`。

    ⛔ **本函数没有"发给谁"这个参数**，也 ⛔ 不许加——收件对象由 `sender` 的
    webhook 地址决定，而地址只能来自 `HR_LIAISON_GROUP_WEBHOOK`（6.7）。
    """
    plan = compute_notify_plan(
        text,
        limit_bytes=GROUP_WEBHOOK_CHANNEL.limit_bytes,
        attachment_supported=sender.attachment_supported,
    )

    def deliver(prepared: NotifyPlan) -> NotifyRecord:
        outcome = effect_deliver_with_backoff(
            make_group_webhook_delivery(sender, prepared), bucket=bucket, sleep=sleep
        )
        return NotifyRecord(
            state=STATE_SENT if outcome.delivered else STATE_PENDING_RESEND,
            attempts=outcome.attempts,
            last_errcode=outcome.last_errcode,
            last_error=outcome.last_error,
        )

    return effect_send_group_notify(
        conn,
        thread_id=thread_id,
        business_key=plan.digest,
        plan=plan,
        deliver=deliver,
        alert_sink=alert_sink,
        channel=GROUP_WEBHOOK_CHANNEL.name,
    )
