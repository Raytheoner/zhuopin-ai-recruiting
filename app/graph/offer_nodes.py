"""Offer 内部审批流的 L4 编排层（offer-generation U3 tasks 3.1–3.3/3.5）。

四个 effect_* 节点，每个独占一类副作用、各带幂等键（工程铁律 1）：

| 节点 | 副作用 | 幂等键 |
|---|---|---|
| `effect_create_offer` | 写 Offer ＋ 流转 offer 阶段 ＋ 流转事实 | `{application_id}:effect_create_offer:{request_id}` |
| `effect_record_approval` | 写审批行 ＋ 推进状态 | `{offer_id}:effect_record_approval:{round}:{level}:{approver}:{decision}` |
| `effect_revise_offer` | 改字段 ＋ round+1 ＋ 待审批 | `{offer_id}:effect_revise_offer:{request_id}` |
| `effect_publish_approval_todo` | 写内部 outbox 待办 | `{offer_id}:effect_publish_approval_todo:{round}:{level}` |

节点内不 conn.commit()：业务写与 effect_log 由 idempotent_effect 同事务提交。
本模块不 import app/agents/** 或 app/outbound/**：没有 AI 调用或候选人外发。
"""
from __future__ import annotations

import logging
import sqlite3
import uuid

from app.channels.base import Channel, OutboundMessage
from app.storage.idempotency import idempotent_effect
from app.storage.offer import (
    ApprovalAlreadyRecordedError,
    ApprovalChainNotConfiguredError,
    ApprovalOutOfOrderError,
    NotAnApproverError,
    OfferNotEditableError,
    OfferNotPendingApprovalError,
    approvers_for_level,
    assert_offer_creatable,
    current_approval_level,
    get_offer,
    total_approval_levels,
)

logger = logging.getLogger(__name__)


def _require_non_empty(**fields: str | None) -> None:
    for name, value in fields.items():
        if not value or not str(value).strip():
            raise ValueError(f"{name} 不能为空")


def _validate_approval_key_binding(
    _conn: sqlite3.Connection,
    *,
    business_key: str,
    level: int,
    approver: str,
    decision: str,
    comment: str | None = None,
    **_kwargs,
) -> None:
    """Validate request fields and bind them to the key without consulting DB state.

    The first key component is the original approval round. Keep it intact for
    valid old-round replays after an Offer revision; only the remaining key
    components are compared with the arguments on this invocation.
    """
    if decision not in ("approved", "returned"):
        raise ValueError(f"decision 只能是 approved/returned，收到: {decision!r}")
    _require_non_empty(approver=approver)
    if decision == "returned" and not (comment or "").strip():
        raise ValueError("退回必须填意见（spec Scenario「一级退回：退回并填意见」）")

    round_part, separator, request_part = business_key.partition(":")
    expected_request_part = f"{level}:{approver}:{decision}"
    if (
        not separator
        or not round_part.isdecimal()
        or request_part != expected_request_part
    ):
        raise ValueError("business_key 必须匹配审批轮次格式、级别、审批人与决定")


@idempotent_effect("effect_create_offer", begin_immediate=True, thread_arg="application_id")
def effect_create_offer(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    application_id: str,
    department: str,
    start_date: str,
    report_to: str,
    note: str | None,
    created_by: str,
) -> str:
    """发起 Offer：Offer、投递阶段、人工流转事实三写同事务。

    thread_id=application_id、business_key=request_id；同请求重跑返回 None。
    投递 status 保持 active，历史 action 留 NULL（该列只允许排期类动作）。
    在 effect 面重做前置校验，防止路由校验后投递状态已经改变。
    """
    _require_non_empty(
        business_key=business_key,
        created_by=created_by,
        department=department,
        start_date=start_date,
        report_to=report_to,
    )
    # 装饰器在 effect_log 预检前取得写锁，覆盖创建资格读取与业务写。
    assert_offer_creatable(conn, application_id=application_id)
    row = conn.execute(
        "SELECT job_id, current_stage_id FROM application WHERE id = ?",
        (application_id,),
    ).fetchone()
    job_id, from_stage_id = row
    offer_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, "
        "report_to, note, status, approval_round, created_by, updated_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_approval', 1, ?, ?)",
        (
            offer_id, application_id, job_id, department, start_date,
            report_to, note, created_by, created_by,
        ),
    )
    conn.execute(
        "UPDATE application SET current_stage_id = 'offer' WHERE id = ?",
        (application_id,),
    )
    conn.execute(
        "INSERT INTO application_stage_history (id, application_id, from_stage_id, "
        "to_stage_id, actor_type, actor) VALUES (?, ?, ?, 'offer', 'human', ?)",
        (str(uuid.uuid4()), application_id, from_stage_id, created_by),
    )
    return offer_id


@idempotent_effect(
    "effect_record_approval",
    begin_immediate=True,
    thread_arg="offer_id",
    validate_before_cache=_validate_approval_key_binding,
)
def effect_record_approval(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    offer_id: str,
    level: int,
    approver: str,
    decision: str,
    comment: str | None = None,
) -> dict:
    """记一级审批并推进状态；状态读取、留痕、状态更新与 effect_log 同事务。"""
    if decision not in ("approved", "returned"):
        raise ValueError(f"decision 只能是 approved/returned，收到: {decision!r}")
    _require_non_empty(approver=approver)
    if decision == "returned" and not (comment or "").strip():
        raise ValueError("退回必须填意见（spec Scenario「一级退回：退回并填意见」）")

    # 装饰器在 effect_log 预检前取得写锁，覆盖后续状态读取与业务写。
    offer = get_offer(conn, offer_id)
    expected_key = f"{offer['approval_round']}:{level}:{approver}:{decision}"
    if business_key != expected_key:
        raise ValueError("business_key 必须匹配当前审批轮次、级别、审批人与决定")
    if offer["status"] != "pending_approval":
        raise OfferNotPendingApprovalError(
            f"Offer 当前状态为「{offer['status_label']}」，不接受审批动作"
        )
    round_no = offer["approval_round"]
    expected_level = current_approval_level(conn, offer_id)
    if level != expected_level:
        raise ApprovalOutOfOrderError(
            f"第 {expected_level} 级尚未通过，不能直接审批第 {level} 级"
        )
    approvers = approvers_for_level(conn, offer["job_id"], level)
    if not approvers:
        raise ApprovalChainNotConfiguredError(
            f"岗位 {offer['job_id']!r} 第 {level} 级尚未配置审批人，请先由 HR 维护该岗位的审批链"
        )
    if approver not in approvers:
        raise NotAnApproverError(f"{approver!r} 不是第 {level} 级审批人")

    try:
        conn.execute(
            "INSERT INTO offer_approval (id, offer_id, round, level, approver, decision, comment) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), offer_id, round_no, level, approver, decision, comment),
        )
    except sqlite3.IntegrityError as exc:
        raise ApprovalAlreadyRecordedError(
            f"第 {level} 级审批已记录（offer={offer_id} round={round_no}）"
        ) from exc

    if decision == "returned":
        new_status = "needs_revision"
    elif level >= total_approval_levels(conn, offer["job_id"]):
        new_status = "approved"
    else:
        new_status = "pending_approval"
    conn.execute(
        "UPDATE offer SET status = ?, updated_by = ?, updated_at = datetime('now') WHERE id = ?",
        (new_status, approver, offer_id),
    )
    return {
        "offer_id": offer_id,
        "round": round_no,
        "level": level,
        "approver": approver,
        "decision": decision,
        "status": new_status,
    }


@idempotent_effect("effect_revise_offer", begin_immediate=True, thread_arg="offer_id")
def effect_revise_offer(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    offer_id: str,
    department: str,
    start_date: str,
    report_to: str,
    note: str | None,
    updated_by: str,
) -> int:
    """退回后修改 Offer，保留旧轮审批事实并从新一轮重新审批。"""
    _require_non_empty(
        business_key=business_key,
        updated_by=updated_by,
        department=department,
        start_date=start_date,
        report_to=report_to,
    )
    offer = get_offer(conn, offer_id)
    if offer["status"] != "needs_revision":
        raise OfferNotEditableError(
            f"Offer 当前状态为「{offer['status_label']}」，只有「待修改」可以修改"
        )
    new_round = offer["approval_round"] + 1
    conn.execute(
        "UPDATE offer SET department = ?, start_date = ?, report_to = ?, note = ?, "
        "status = 'pending_approval', approval_round = ?, updated_by = ?, "
        "updated_at = datetime('now') WHERE id = ?",
        (department, start_date, report_to, note, new_round, updated_by, offer_id),
    )
    return new_round


@idempotent_effect(
    "effect_publish_approval_todo",
    begin_immediate=True,
    thread_arg="offer_id",
    business_key_args=("round_no", "level"),
)
def effect_publish_approval_todo(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    channel: Channel,
    offer_id: str,
    application_id: str,
    job_title: str,
    round_no: int,
    level: int,
    approvers: list[str],
) -> None:
    """把内部审批待办写入通知通道；outbox 与 effect_log 同事务提交。

    装饰器在事务与缓存预检前校验 business_key 与 round_no/level 绑定，防止
    错误参数借已存在的键命中缓存而漏发通知。
    """
    if channel is None:
        raise ValueError(
            "channel 不能为空——无通道的情形由 publish_approval_todo() 在调用本节点"
            "之前处置（tasks 3.5：无通道时只靠列表页，且不写 effect_log）"
        )
    channel.deliver(
        offer_id,
        OutboundMessage(
            type="offer_approval_todo",
            payload={
                "offer_id": offer_id,
                "application_id": application_id,
                "job_title": job_title,
                "round": round_no,
                "level": level,
                "approvers": list(approvers),
                "approve_url": f"/offers/{offer_id}/approve",
            },
        ),
    )


def publish_approval_todo(
    conn: sqlite3.Connection, *, offer: dict, channel: Channel | None
) -> None:
    """通知本轮当前级审批人；无通道时由待办列表提供权威视图。"""
    if channel is None:
        logger.warning(
            "内部通知通道未装配：Offer %s 第 %s 轮的审批待办仅靠列表页",
            offer["id"],
            offer["approval_round"],
        )
        return
    level = current_approval_level(conn, offer["id"])
    effect_publish_approval_todo(
        conn,
        thread_id=offer["id"],
        business_key=f"{offer['approval_round']}:{level}",
        channel=channel,
        offer_id=offer["id"],
        application_id=offer["application_id"],
        job_title=offer["job_title"],
        round_no=offer["approval_round"],
        level=level,
        approvers=approvers_for_level(conn, offer["job_id"], level),
    )
