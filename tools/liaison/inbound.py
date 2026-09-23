"""一条入站消息的分支接线（tasks 4.10）。

本章只接四件事：
1. 用第 3 章的 `admit()` 判准入；
2. **两条分支都归档**——spec 原文：名单外的消息"SHALL 仍然归档
   （以便事后可查'谁在什么时候发过什么'）"；
3. **名单内的消息入队**——`InboundRoute.should_enqueue` 为真时调 `queue.enqueue_task`；
   🔴 ⛔ **不加 `newly_archived` 门槛**，理由见 `handle_inbound_message` 里的注释。
4. 名单外回一条礼貌说明，且 ⛔ **MUST NOT 生成任何值守任务队列条目**。
5. （2026-09-17 `[Mac]0917W`）**归档之前**把帧里的附件句柄变成字节：
   `fetch_inbound_attachment` 经注入的 `download` 口取回、⛔ 不写 0 字节文件、
   同 `msgid` 已归档就 ⛔ 不再下载。下载失败只告警，⛔ 不阻断正文归档与入队。

⚠️ **礼貌回复是 at-most-once，这是一个已登记的缺口**（见 docs/tech-debt.md）：
它走注入的 reply port，不是 `effect_*`。台账已提交、回复还没发出去时进程被杀，
这条回复永久丢失（重投时归档幂等命中，不会再触发回复）。
⛔ 不要在本模块里"顺手"补一个 `effect_reply_*`——那需要一张 outbox 表，
加表是 design 层的偏离，且真实外发通道本来就在第 6／7 章。
丢的是一条告知，不丢材料、不丢待办，方向安全。
"""

from __future__ import annotations

import logging
import pathlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tools.liaison import alerts
from tools.liaison.archive import (
    DEFAULT_ARCHIVE_ROOT,
    ArchiveOutcome,
    InboundAttachment,
    archive_message,
)
from tools.liaison.frames import InboundAttachmentRef, _guess_attachment_filename
from tools.liaison.queue import compute_task_summary, enqueue_task
from tools.liaison.whitelist import admit

logger = logging.getLogger(__name__)

#: 名单外发送人收到的固定回复。
#:
#: **⛔ 不透露名单里有谁**——那是不必要的个人信息披露，且会让这个内部工具
#: 变成一个可以拿来探测"谁有权限"的接口。
#: **必须带"自动发送"标识**：这条不是 AI 生成合成内容（固定模板，不落入
#: 《AI 生成合成内容标识办法》的适用范围），但收件人不能误以为它是
#: Shao Peishen 本人的回复。
POLITE_NOTICE = (
    "您好，这是 Shao Peishen 的 HR 值守助手自动发送的回复。"
    "您的消息与材料已收到并留档，但当前未在本助手的受理范围内，不会转成待办事项。"
    "如需处理，请直接联系 Shao Peishen。"
)


@dataclass(frozen=True)
class InboundRoute:
    """一条消息该怎么处置。纯数据，由 `compute_inbound_route` 算出。"""

    admitted: bool
    should_enqueue: bool
    reply_text: str | None


@dataclass(frozen=True)
class InboundResult:
    route: InboundRoute
    outcome: ArchiveOutcome
    replied: bool
    enqueued: bool = False


def compute_inbound_route(admitted: bool) -> InboundRoute:
    """纯函数（工程铁律 2 的形状）：只由"准入与否"决定处置。

    ⛔ 不读名单文件（那是 `admit()` 的事）、不读时钟、不记日志。
    """
    if admitted:
        return InboundRoute(admitted=True, should_enqueue=True, reply_text=None)
    return InboundRoute(admitted=False, should_enqueue=False, reply_text=POLITE_NOTICE)


#: 下载口的形状：对齐 SDK `client.download_file(url, aes_key)` 的返回 `(bytes, filename|None)`
#: （`aibot/client.py:304-330`）。⛔ 本模块不 import aibot；真实适配器在接线层注入。
AttachmentDownloader = Callable[[InboundAttachmentRef], "tuple[bytes, str | None]"]


def _already_archived(conn, msgid: str) -> bool:
    """同一 `msgid` 的台账行已在 ⇒ 材料早就落定（design D3：台账行只在材料之后写）。
    重投时据此 ⛔ 不再下载——下载是网络调用，`store_attachment` 的路径幂等挡不住它。"""
    return (
        conn.execute("SELECT 1 FROM liaison_message WHERE msgid = ?", (msgid,)).fetchone()
        is not None
    )


def fetch_inbound_attachment(
    conn,
    ref: InboundAttachmentRef | None,
    *,
    thread_id: str,
    msgid: str,
    download: AttachmentDownloader | None,
    alert_sink: alerts.AlertSink,
) -> InboundAttachment | None:
    """把附件句柄变成 `InboundAttachment`（字节）。**永不上抛**：任何失败都返回 `None`
    并留下症状（告警／日志），让正文归档与入队照常进行——材料还在企微侧，待办生成后
    人能去要；反过来让一次下载失败把整条消息拦在库外，才是丢材料。

    三条方向（按顺序）：
    1. `ref is None`（不带媒体项）⇒ `None`，静默。
    2. 同 `msgid` 已归档（重投）⇒ `None`，⛔ 不下载：`archive_message` 会幂等命中，
       台账里记的仍是第一次落的那份材料。
    3. `download` 未接入 ⇒ WARNING（⛔ 不静默——"没接下载口"与"这条消息没附件"必须能分辨）。
    4. 下载抛异常、或返回**空字节** ⇒ 告警 ＋ `None`。空字节 ⛔ 不落盘：`b""` 写进去
       就是 TD-41 那种"0 字节附件全链路无症状"——归档层分不开"真空文件"与"下载静默失败"，
       只有这里（下载侧）分得开，所以在这里挡。
    """
    if ref is None:
        return None

    if _already_archived(conn, msgid):
        logger.info(
            "同一 msgid 已归档，附件 ⛔ 不重复下载（幂等）：thread_id=%s msgid=%s",
            thread_id,
            msgid,
        )
        return None

    if download is None:
        logger.warning(
            "入站附件未落盘：下载口未接入（InboundPorts.download 为 None，TD-51），"
            "正文照常归档。thread_id=%s msgid=%s msgtype=%s",
            thread_id,
            msgid,
            ref.msgtype,
        )
        return None

    try:
        payload, response_filename = download(ref)
    except Exception:  # noqa: BLE001 —— 下载失败 ⛔ 不阻断正文归档
        logger.error(
            "入站附件下载失败，正文照常归档、附件未落盘。thread_id=%s msgid=%s msgtype=%s",
            thread_id,
            msgid,
            ref.msgtype,
            exc_info=True,
        )
        alerts.effect_emit_alert(
            alert_sink,
            f"【HR 值守通道·附件未落盘】msgid={msgid} 的 {ref.msgtype} 附件下载失败，"
            "正文已归档、待办已生成，请人工向发送人索取该文件。",
        )
        return None

    if not payload:
        logger.error(
            "入站附件下载返回空字节，⛔ 不写 0 字节文件（TD-41 同类）；正文照常归档。"
            "thread_id=%s msgid=%s msgtype=%s",
            thread_id,
            msgid,
            ref.msgtype,
        )
        alerts.effect_emit_alert(
            alert_sink,
            f"【HR 值守通道·附件未落盘】msgid={msgid} 的 {ref.msgtype} 附件下载返回空内容，"
            "未落盘（⛔ 不写 0 字节文件），正文已归档、待办已生成，请人工向发送人索取该文件。",
        )
        return None

    # 文件名优先级（TD-51 b/c 支，2026-09-23）：帧里给的 > 下载响应头解出的（SDK 从
    # Content-Disposition 解）> 按 msgid + 内容魔数猜（_guess_attachment_filename）。
    filename = ref.filename if ref.filename else response_filename
    if not filename:
        filename = _guess_attachment_filename(msgid, bytes(payload))
    return InboundAttachment(filename=filename, payload=bytes(payload))


def handle_inbound_message(
    conn,
    *,
    thread_id: str,
    msgid: str,
    sender_userid: str,
    received_at: str,
    msgtype: str,
    content: str = "",
    attachment: InboundAttachment | None = None,
    archive_root: pathlib.Path = DEFAULT_ARCHIVE_ROOT,
    whitelist_path: pathlib.Path | None = None,
    reply: Callable[[str, str], Any] | None = None,
) -> InboundResult:
    """归档 → （名单内）入队 → （名单外）礼貌回复。

    顺序是**先归档、后回复**：材料落定了才告知对方"收到了但不受理"。
    反过来会出现"回复已发、材料没留住"，事后无从查证。

    回复只在 `newly_archived` 为真时发出——重投同一 `msgid` 时归档会幂等命中，
    这时候再发一遍就是骚扰。⛔ 不要改成"查台账里有没有这一行"来判断：
    那是另一次查询、另一个时刻。
    """
    route = compute_inbound_route(admit(sender_userid, whitelist_path))

    outcome = archive_message(
        conn,
        thread_id=thread_id,
        msgid=msgid,
        sender_userid=sender_userid,
        received_at=received_at,
        msgtype=msgtype,
        content=content,
        attachment=attachment,
        archive_root=archive_root,
    )

    enqueued = False
    if route.should_enqueue:
        # 🔴 ⛔ 这里**不许**加 `and outcome.newly_archived`。
        # 归档已提交、入队之前进程被杀 ⇒ 重投时归档幂等命中 ⇒ 跟着它走
        # 这条待办就永远不会出现，且没有任何症状（工程铁律 1 的失效方向）。
        # 重复由 effect_enqueue_task 的幂等键与 liaison_task.msgid 的 UNIQUE
        # 两道防线挡住，代价只是一次空转。
        # 礼貌回复相反（下面那段仍然跟 newly_archived 走）：丢一条告知可接受，
        # 丢一条待办不可接受。
        enqueued = enqueue_task(
            conn,
            thread_id=thread_id,
            msgid=msgid,
            sender_userid=sender_userid,
            received_at=received_at,
            summary=compute_task_summary(content, msgtype=msgtype),
        )

    replied = False
    if route.reply_text is not None and outcome.newly_archived:
        if reply is None:
            # 第 7 章接通道之前会走到这里。⛔ 不静默吞——"没人回我"和
            # "系统压根没打算回"是两件事，排障时必须能分辨。
            logger.warning(
                "礼貌回复未能发出：没有接入 reply 通道（第 6／7 章补）。"
                "thread_id=%s msgid=%s",
                thread_id,
                msgid,
            )
        else:
            try:
                reply(thread_id, route.reply_text)
                replied = True
            except Exception:  # noqa: BLE001
                # 回复失败 ⛔ 不许回滚归档——材料已经落定，那是本章更重要的产出。
                logger.error(
                    "礼貌回复发送失败，材料已归档、不回滚。thread_id=%s msgid=%s",
                    thread_id,
                    msgid,
                    exc_info=True,
                )

    return InboundResult(route=route, outcome=outcome, replied=replied, enqueued=enqueued)
