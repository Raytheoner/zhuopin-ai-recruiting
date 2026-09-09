"""长度守卫与降级决策——**本模块全是纯函数**（工程铁律 2）。

⛔ 不读环境、不碰网络、不碰数据库、不写日志。它只回答两个问题：
「这段文字对**这条**通道超限了吗」与「超限了该怎么办」。

⛔ 本目录（`tools/liaison/`，测试除外）禁止写 `with X:`：第 2 章的事务扫描器
把 `with <名字|属性>:` 无条件判为隐式提交违规，`with <调用>:` 只放行一份正面
白名单。本模块用不到 `with`，写在这里是给后来改动的人看的。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

#: aibot 通道的服务端限额，**字节**（design D9：Windows 侧 2026-08-28 实测）。
AIBOT_CHANNEL_LIMIT_BYTES = 20480

#: 群机器人 webhook 的 markdown 限额，**字节**。
#:
#: design D9 的官方口径是"约 4096 字符"。⚠️ 这里刻意取 **4096 字节**，而不是
#: "4096 字符换算出的 12288 字节"——两种读法都可能对，选小的那个：判过严只会多
#: 降级一次（群里看得见提要 + 附件，可回退）；判过松会被服务端静默打回，而
#: "我已经通知过了"当场变成假话。
#:
#: ⛔ **这两个常量永远是两个数、两个来源，⛔ 不许合并成一个"通用上限"。**
#: 单位与出处都不同（一个是实测字节数，一个是官方文档的字符口径），
#: 合并等于给其中一条通道埋一个静默截断（D9 逐字）。
GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES = 4096


@dataclass(frozen=True)
class ChannelLimit:
    """一条外发通道的长度配置。**每条通道各自一份。**"""

    name: str
    limit_bytes: int


AIBOT_CHANNEL = ChannelLimit(name="aibot", limit_bytes=AIBOT_CHANNEL_LIMIT_BYTES)
GROUP_WEBHOOK_CHANNEL = ChannelLimit(
    name="group_webhook", limit_bytes=GROUP_WEBHOOK_CHANNEL_LIMIT_BYTES
)

#: 摘要取 SHA-256 的前多少位十六进制（tasks 6.6：幂等键含"通知内容摘要"）。
DIGEST_HEX_LENGTH = 16

MODE_DIRECT = "direct"
MODE_DEGRADED = "degraded"
MODE_REJECT = "reject"

REJECT_NO_ATTACHMENT_CHANNEL = "该通道没有可用的附件承载方式，超限内容无法降级"
REJECT_SUMMARY_STILL_OVER = "提要本身仍超过通道上限，无法降级"

ATTACHMENT_FILENAME_TEMPLATE = "liaison-notify-{digest}.md"

#: 提要开头的声明。**这句话是"不静默截断"的可执行形式**：正文被裁过这件事
#: 必须写在发出去的内容里，⛔ 不许只写在日志里——收信人看不见日志。
SUMMARY_NOTICE_TEMPLATE = (
    "【内容超长已降级】完整正文见附件 {filename}（共 {byte_length} 字节）。"
    "以下为开头节选：\n\n"
)


@dataclass(frozen=True)
class LengthVerdict:
    byte_length: int
    limit_bytes: int
    over_limit: bool


@dataclass(frozen=True)
class NotifyPlan:
    """一条通知"怎么发"的完整决定。**纯数据，不含任何通道对象。**"""

    mode: str
    digest: str
    body: str
    full_text: str
    attachment_filename: str | None
    attachment_content: str | None
    reject_reason: str | None
    byte_length: int
    limit_bytes: int

    @property
    def is_send(self) -> bool:
        return self.mode in (MODE_DIRECT, MODE_DEGRADED)


def compute_byte_length(text: str) -> int:
    """UTF-8 编码后的**字节数**。⛔ 不是 `len(text)`。"""
    return len(text.encode("utf-8"))


def compute_length_guard(text: str, *, limit_bytes: int) -> LengthVerdict:
    """判定这段文字对**这条**通道是否超限。

    `limit_bytes` 是**必传关键字参数、⛔ 没有默认值**——这是"两条通道不共用阈值"
    的结构形态：忘了传当场 `TypeError`，⛔ 不会静默套用另一条通道的数。
    ⛔ 不要"为了方便"给它加默认值，那一行就是 D9 明令禁止的共用常量。
    """
    if limit_bytes <= 0:
        raise ValueError(f"通道上限必须为正：{limit_bytes}")
    byte_length = compute_byte_length(text)
    return LengthVerdict(
        byte_length=byte_length,
        limit_bytes=limit_bytes,
        over_limit=byte_length > limit_bytes,
    )


def compute_notify_digest(text: str) -> str:
    """通知内容摘要，幂等键 `{thread_id}:effect_send_group_notify:{digest}` 的第三段。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:DIGEST_HEX_LENGTH]


def compute_text_prefix_within_bytes(text: str, budget_bytes: int) -> str:
    """按 UTF-8 字节预算取前缀，⛔ 不切碎多字节字符。

    ⛔ 不用 `text.encode()[:n].decode(errors="ignore")`：那是"先切碎再把碎片掩盖
    掉"，掩盖手段（`ignore`）本身就是一种静默。这里逐字符累加，装不下就停。
    """
    if budget_bytes < 0:
        raise ValueError(f"字节预算不可能是负数：{budget_bytes}")
    chunks: list[str] = []
    used = 0
    for ch in text:
        size = len(ch.encode("utf-8"))
        if used + size > budget_bytes:
            break
        chunks.append(ch)
        used += size
    return "".join(chunks)


def _reject(text: str, *, digest: str, verdict: LengthVerdict, reason: str) -> NotifyPlan:
    return NotifyPlan(
        mode=MODE_REJECT,
        digest=digest,
        body="",
        full_text=text,
        attachment_filename=None,
        attachment_content=None,
        reject_reason=reason,
        byte_length=verdict.byte_length,
        limit_bytes=verdict.limit_bytes,
    )


def compute_notify_plan(
    text: str, *, limit_bytes: int, attachment_supported: bool
) -> NotifyPlan:
    """决定这条通知直接发、降级发、还是拒发。**纯函数，不发任何东西。**

    三条出口，⛔ 没有第四条（尤其没有"截断后照发"）：
    - 不超限 → `MODE_DIRECT`，原文一字不改地发；
    - 超限且有附件承载 → `MODE_DEGRADED`，提要（含降级声明）+ 附件（完整原文）；
    - 超限但没有附件承载、或连降级声明都装不下 → `MODE_REJECT` + 拒发原因。
    """
    digest = compute_notify_digest(text)
    verdict = compute_length_guard(text, limit_bytes=limit_bytes)
    if not verdict.over_limit:
        return NotifyPlan(
            mode=MODE_DIRECT,
            digest=digest,
            body=text,
            full_text=text,
            attachment_filename=None,
            attachment_content=None,
            reject_reason=None,
            byte_length=verdict.byte_length,
            limit_bytes=limit_bytes,
        )
    if not attachment_supported:
        return _reject(text, digest=digest, verdict=verdict, reason=REJECT_NO_ATTACHMENT_CHANNEL)

    filename = ATTACHMENT_FILENAME_TEMPLATE.format(digest=digest)
    notice = SUMMARY_NOTICE_TEMPLATE.format(filename=filename, byte_length=verdict.byte_length)
    budget = limit_bytes - compute_byte_length(notice)
    if budget <= 0:
        return _reject(text, digest=digest, verdict=verdict, reason=REJECT_SUMMARY_STILL_OVER)

    summary = notice + compute_text_prefix_within_bytes(text, budget)
    if compute_byte_length(summary) > limit_bytes:
        # 到不了这里（预算是按字节算好的）。留着是因为这条不变式一旦破掉，
        # 后果是"发出一条超限内容"而不是"报个错"，⛔ 不许删。
        return _reject(text, digest=digest, verdict=verdict, reason=REJECT_SUMMARY_STILL_OVER)

    return NotifyPlan(
        mode=MODE_DEGRADED,
        digest=digest,
        body=summary,
        full_text=text,
        attachment_filename=filename,
        attachment_content=text,
        reject_reason=None,
        byte_length=verdict.byte_length,
        limit_bytes=limit_bytes,
    )
