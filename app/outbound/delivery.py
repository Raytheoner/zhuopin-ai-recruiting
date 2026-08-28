"""
候选人外发的唯一入口。**编排，不判定、不写库**——判定在 `gate.py`（纯函数），
写库在 `app/graph/nodes.py` 的三个 `effect_*` 节点。

⛔ 本模块不提供任何"跳过门禁"的参数、开关或环境变量（design.md 迁移计划回滚
策略：关闭 `CANDIDATE_OUTBOUND_ENABLED` 是更安全的方向；真要恢复无门禁投递必须
显式移除门禁节点）。守护见 `tests/test_outbound_delivery.py::test_no_bypass_parameter_exists`。
"""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)


def _audit_event(
    thread_id: str,
    message: CandidateOutboundMessage,
    decision: GateDecision,
    content_hash: str,
) -> DecisionEvent:
    """
    把判定结果折成留痕事件。**`evidence` 原样带走**（design D4）：⛔ 这里不重新
    读消息的任何属性，那会制造"判定时未知、留痕时又变成已知"的不一致。

    `content_hash` 由调用方传入，⛔ 不在这里重新算一遍——`deliver_candidate_message`
    自己已经算过一次用来喂 `effect_*` 的 business_key，两处各算一遍是在制造"两个
    调用点必须自己保持一致"的隐性耦合（review round 2 minor 2）。
    """
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

    event = _audit_event(thread_id, message, decision, content_hash)
    result = effect_record_outbound_audit(
        conn,
        thread_id=thread_id,
        business_key=f"{content_hash}:{decision.allowed}",
        recorder=recorder,
        event=event,
    )

    # 第二段：镜像。**在这里而不是在 effect_* 函数体内**——此时装饰器已 commit
    # （delivery-units.md §3.4 第 3 条：允许的偏差只有单向「SQLite 有、JSONL 缺行」）。
    #
    # ⚠️ `result is None` 与 `result is False` 是两件完全不同的事，⛔ 不要合并
    # 处理（2026-08-28 review round 2 Important 定级、对残留 B 的拍板订正）：
    #
    #   - `None`  → `idempotent_effect` 装饰器（app/graph/nodes.py:309）判定这个
    #     `(thread_id, business_key)` 已经在 effect_log 里，函数体**没有真的执行
    #     一遍**（app/storage/idempotency.py:36-37），直接短路。镜像里已经有这条
    #     记录了——这是重放（replay），⛔ 不能再 append 一遍，否则同一个 event.id
    #     在 JSONL 里出现 N 次。外发事件在 SqliteSink 里没有真身
    #     （SUPPORTED_EVENT_TYPES 排除它，见 app/audit/sinks.py），JSONL 这一行是
    #     它**唯一**的记录，把它写重是在腐蚀唯一真源；而 reconcile() 比的是 id
    #     集合差集，看不出"同一个 id 出现了两次"（app/audit/hook.py:184 已经踩过
    #     同一个坑并留了实测证据：SQLite 1 行、JSONL 2 行，reconcile().ok 仍为
    #     True）。本函数体这里复刻同一处理：`stored`/`result is None` → 跳过
    #     mirror，仅 WARNING。
    #   - `False` → 函数体**真的执行了**，`recorder.record()` 也真的被调用了，
    #     只是外发事件在这个 sink 里没有真身（不是"已经写过"）。这次是
    #     `deliver_candidate_message` 第一次处理这个决策，镜像里还没有这一行，
    #     ⛔ 不能因为它是 `False` 就跳过——那会让外发决策**永远**没有留痕
    #     （2026-08-28 对残留 B 的既有拍板，仍然成立）。
    #   - `True`  → 有真身的事件类型（本模块目前不会产出，留作形状完整）。
    #
    # 判据不是布尔真值性，是"是不是 None"——`False` 与 `True` 都要 mirror，
    # 只有 `None` 不要。precedent: app/audit/hook.py:184-218 的 `if stored:` /
    # `else:` 分支就是这个判据的既有实现，本处照抄同一判据（对象不同：那边判
    # `record()` 的返回值，这边判 `effect_record_outbound_audit()` 的返回值，
    # 但两者语义同源——`effect_record_outbound_audit` 内部就是调用 `record()`）。
    if result is None:
        logger.warning(
            "外发留痕已存在（重放），跳过镜像 append（id=%s）。同一 id 被写第二次"
            "通常意味着 deliver_candidate_message 用同一个 (thread_id, "
            "business_key) 被重复调用，而确定性 id 分辨不出来。",
            event.id,
        )
        return decision

    try:
        recorder.mirror(event)
    except Exception:  # noqa: BLE001 —— 镜像失败⛔ 不抛，理由同 app/audit/hook.py
        logger.error(
            "外发留痕镜像 append 失败（id=%s）。这是被允许的单向偏差，"
            "由对账检出、链尾补录；⛔ 不要改成抛异常。",
            event.id,
            exc_info=True,
        )

    return decision
