"""一条入站消息的分支接线（tasks 4.10）。

本章只接三件事：
1. 用第 3 章的 `admit()` 判准入；
2. **两条分支都归档**——spec 原文：名单外的消息"SHALL 仍然归档
   （以便事后可查'谁在什么时候发过什么'）"；
3. 名单外回一条礼貌说明，且 ⛔ **MUST NOT 生成任何值守任务队列条目**。

⛔ **本模块不入队。** `InboundRoute.should_enqueue` 是留给第 5 章的接线位——
那一章在这个布尔值后面接上真正的入队 effect 即可（函数名见第 5 章 plan，
本文件故意不提，见下）。
tests/test_inbound_routing.py::test_inbound_module_never_references_the_enqueue_effect
用纯文本扫描源码（含本 docstring）钉住"这个名字现在一次都不许出现"，
把"现在还没写"钉成断言。

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

from tools.liaison.archive import (
    DEFAULT_ARCHIVE_ROOT,
    ArchiveOutcome,
    InboundAttachment,
    archive_message,
)
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


def compute_inbound_route(admitted: bool) -> InboundRoute:
    """纯函数（工程铁律 2 的形状）：只由"准入与否"决定处置。

    ⛔ 不读名单文件（那是 `admit()` 的事）、不读时钟、不记日志。
    """
    if admitted:
        return InboundRoute(admitted=True, should_enqueue=True, reply_text=None)
    return InboundRoute(admitted=False, should_enqueue=False, reply_text=POLITE_NOTICE)


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
    """归档 → （名单外）礼貌回复。⛔ 本章不入队。

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

    return InboundResult(route=route, outcome=outcome, replied=replied)
