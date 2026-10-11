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


@idempotent_effect("effect_create_offer", require_no_transaction=True)
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
        created_by=created_by,
        department=department,
        start_date=start_date,
        report_to=report_to,
    )
    # 首次业务读取前取得写锁；提交与异常回滚仍由装饰器唯一管理。
    conn.execute("BEGIN IMMEDIATE")
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
