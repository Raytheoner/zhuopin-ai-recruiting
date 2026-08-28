"""
候选人外发的唯一入口。**编排，不判定、不写库**——判定在 `gate.py`（纯函数），
写库在 `app/graph/nodes.py` 的三个 `effect_*` 节点。

⛔ 本模块不提供任何"跳过门禁"的参数、开关或环境变量（design.md 迁移计划回滚
策略：关闭 `CANDIDATE_OUTBOUND_ENABLED` 是更安全的方向；真要恢复无门禁投递必须
显式移除门禁节点）。守护见 `tests/test_outbound_delivery.py::test_no_bypass_parameter_exists`。
"""

from __future__ import annotations

import sqlite3
from typing import Any, Callable

from app.audit.events import OUTBOUND_BLOCKED, OUTBOUND_DELIVERED, DecisionEvent
from app.audit.recorder import AuditRecorder
from app.graph.nodes import (
    effect_deliver_message,
    effect_enqueue_pending_approval,
    effect_record_outbound_audit,
)
from app.outbound.gate import GateDecision, compute_outbound_gate
from app.outbound.messages import CandidateOutboundMessage


def _audit_event(
    thread_id: str, message: CandidateOutboundMessage, decision: GateDecision
) -> DecisionEvent:
    """
    把判定结果折成留痕事件。**`evidence` 原样带走**（design D4）：⛔ 这里不重新
    读消息的任何属性，那会制造"判定时未知、留痕时又变成已知"的不一致。
    """
    content_hash = message.content_hash()
    return DecisionEvent(
        id=f"{thread_id}:effect_record_outbound_audit:{content_hash}:{decision.allowed}",
        event_type=OUTBOUND_DELIVERED if decision.allowed else OUTBOUND_BLOCKED,
        thread_id=thread_id,
        message_type=message.message_type,
        recipient=message.recipient,
        content_hash=content_hash,
        confirmed_by=message.confirmed_by,
        blocked_reason=decision.reason,
        evidence=decision.evidence,
        error=decision.error,
    )


def deliver_candidate_message(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    message: CandidateOutboundMessage,
    channel: Any,
    recorder: AuditRecorder,
    outbound_enabled: Callable[[], bool],
) -> GateDecision:
    """
    受门禁保护的候选人外发。返回门禁判定，调用方据 `.allowed` 得知结果。

    顺序是刻意的：**判一次 → 分流 → 留痕（都在事务里）→ 提交后镜像**。
    """
    decision = compute_outbound_gate(message, outbound_enabled)
    content_hash = message.content_hash()

    if decision.allowed:
        effect_deliver_message(
            conn,
            thread_id=thread_id,
            business_key=content_hash,
            channel=channel,
            message=message.to_outbound_message(),
        )
    elif not (message.confirmed_by or "").strip():
        # ⛔ 只有**首道拦截**（没带签名）才入队。放行复发被拦时它已经在队列里，
        # 重入会撞自己的唯一索引，把"暂时发不出去"变成 IntegrityError
        # （design D5 的死锁防线，平台侧踩过）。判据就是"是否携带 confirmed_by"。
        effect_enqueue_pending_approval(
            conn,
            thread_id=thread_id,
            business_key=content_hash,
            message=message,
            blocked_reason=decision.reason or "",
        )

    event = _audit_event(thread_id, message, decision)
    effect_record_outbound_audit(
        conn,
        thread_id=thread_id,
        business_key=f"{content_hash}:{decision.allowed}",
        recorder=recorder,
        event=event,
    )

    # 第二段：镜像。**在这里而不是在 effect_* 函数体内**——此时装饰器已 commit
    # （delivery-units.md §3.4 第 3 条：允许的偏差只有单向「SQLite 有、JSONL 缺行」）。
    #
    # ⚠️ ⛔ 不要因为上面 effect_record_outbound_audit 返回 False 就跳过这一步：
    # 外发事件在 analysis_run 里本来就没有真身（真身是 pending_approval），
    # 镜像里这一行是它**唯一的**留痕。调用点按自己写的 event_type 决定行为，
    # 不从 False 反推原因（2026-08-28 对残留 B 的拍板）。
    try:
        recorder.mirror(event)
    except Exception:  # noqa: BLE001 —— 镜像失败⛔ 不抛，理由同 app/audit/hook.py
        import logging

        logging.getLogger(__name__).error(
            "外发留痕镜像 append 失败（id=%s）。这是被允许的单向偏差，"
            "由对账检出、链尾补录；⛔ 不要改成抛异常。",
            event.id,
            exc_info=True,
        )

    return decision
