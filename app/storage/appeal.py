"""拒绝记录的申诉状态机（hard-requirement-screening spec「淘汰只由人确认
并可申诉」，tasks 4.5/4.6）。

状态机：none → requested → under_review → upheld | overturned。非法跳转
拒绝；同一记录对同一目标状态重复提交视为幂等（无第二条流转事件）；
overturned 让投递恢复到淘汰前阶段并写入流转事实；原拒绝记录不删除，
appeal_status 原地更新。
"""
from __future__ import annotations

import sqlite3
import uuid

_LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "none": frozenset({"requested"}),
    "requested": frozenset({"under_review"}),
    "under_review": frozenset({"upheld", "overturned"}),
    "upheld": frozenset(),
    "overturned": frozenset(),
}


class IllegalAppealTransition(ValueError):
    """状态机不允许的跳转。⛔ 不静默忽略、不自动纠正到"最近的合法状态"。"""


class AppealRecordNotFound(ValueError):
    """rejection_record_id 不存在。"""


def transition_appeal(
    conn: sqlite3.Connection,
    *,
    rejection_record_id: str,
    to_status: str,
    actor: str,
) -> dict:
    """流转一条拒绝记录的申诉状态。返回
    {"appeal_status": str, "already_applied": bool}。

    幂等：当前 appeal_status 已经等于 to_status ⇒ 不产生新的 appeal_event、
    不重复更新，返回 already_applied=True（tasks 4.5「同一记录同一目标
    状态重复提交无第二条流转」）。
    """
    if not actor or not actor.strip():
        raise ValueError("actor 不能为空")

    row = conn.execute(
        "SELECT application_id, appeal_status FROM rejection_record WHERE id = ?",
        (rejection_record_id,),
    ).fetchone()
    if row is None:
        raise AppealRecordNotFound(f"rejection_record_id={rejection_record_id} 不存在")
    application_id, current_status = row

    if current_status == to_status:
        return {"appeal_status": current_status, "already_applied": True}

    legal_targets = _LEGAL_TRANSITIONS.get(current_status, frozenset())
    if to_status not in legal_targets:
        raise IllegalAppealTransition(
            f"申诉状态不能从 {current_status!r} 跳到 {to_status!r}"
        )

    try:
        conn.execute(
            "UPDATE rejection_record SET appeal_status = ? WHERE id = ?",
            (to_status, rejection_record_id),
        )
        conn.execute(
            "INSERT INTO appeal_event "
            "(id, rejection_record_id, from_status, to_status, actor) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), rejection_record_id, current_status, to_status, actor),
        )

        if to_status == "overturned":
            history_row = conn.execute(
                "SELECT from_stage_id FROM application_stage_history "
                "WHERE application_id = ? AND to_stage_id = 'rejected' "
                "ORDER BY occurred_at DESC LIMIT 1",
                (application_id,),
            ).fetchone()
            pre_rejection_stage_id = history_row[0] if history_row and history_row[0] else "initial"
            conn.execute(
                "UPDATE application SET status = 'active', current_stage_id = ? WHERE id = ?",
                (pre_rejection_stage_id, application_id),
            )
            conn.execute(
                "INSERT INTO application_stage_history "
                "(id, application_id, from_stage_id, to_stage_id, actor_type, actor) "
                "VALUES (?, ?, 'rejected', ?, 'human', ?)",
                (str(uuid.uuid4()), application_id, pre_rejection_stage_id, actor),
            )
    except Exception:
        # 同 rejection.py::write_rejection 的理由：conn 是全应用共享的单连接，
        # 写到一半失败会把前面几条语句留在隐式打开的事务里，被之后任何一次
        # *不相关*的 conn.commit() 悄悄落盘，精确审计链出现窟窿。
        conn.rollback()
        raise

    conn.commit()
    return {"appeal_status": to_status, "already_applied": False}
