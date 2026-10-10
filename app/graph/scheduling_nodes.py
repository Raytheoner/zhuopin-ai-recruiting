"""面试排期（U2）的四个 effect_* 节点（interview-slot-scheduling spec
「安排/改期/取消/完成」；design D4）。

每个有副作用的动作独占一个节点、各带幂等键（工程铁律 1/2）。
幂等键由 @idempotent_effect 装饰器按 {thread_id}:{node_name}:{business_key} 落
effect_log，并与业务写同事务提交（装饰器统一 commit，节点内⛔不 commit）。

thread_id = application_id（design D4 的 {application_id}:{node_name}:{...}）。
business_key 约定：
- schedule   = slot_id（slot 主键即客户端生成的 request_id，见偏离 D-U2-2）
- reschedule = f"{slot_id}:{request_id}"（同一场次可多次改期，每次改期是新动作）
- cancel     = slot_id（终态，每场次至多一次）
- complete   = f"{slot_id}:{target_status}"（终态，每场次每目标至多一次）

⛔ 不在节点内 conn.commit()——写入与 effect_log 记录必须由 idempotent_effect
装饰器在同一事务里一次性提交（工程铁律 1）。
"""
from __future__ import annotations

import json
import sqlite3
import uuid

from app.agents.conflict_check import ConflictError, check
from app.storage.db import sqlite_utc_now
from app.storage.idempotency import idempotent_effect
from app.storage.interview_scheduling import (
    CompletionBeforeStartError,
    NotInterviewStageError,
    SlotNotFoundError,
    SlotStateError,
    active_slot_for_round,
    current_stage_type,
    load_conflict_inputs,
    slot_for,
)

HISTORY_ACTION_SCHEDULED = "scheduled"
HISTORY_ACTION_RESCHEDULED = "rescheduled"
HISTORY_ACTION_CANCELLED = "cancelled"

_SCHEDULABLE_STATUSES = ("scheduled", "rescheduled")


class ScheduleInputError(ValueError):
    """排期节点入参校验失败（0 面试官 / 反向时刻）。

    2026-10-11 修正（1001G，Spec review 实测）：spec「指定面试官一至多位」与
    start_at < end_at 必须在节点层强制——路由层校验可以被别的调用方绕过。
    """


def _assert_interview_stage(conn: sqlite3.Connection, application_id: str) -> None:
    stage_type = current_stage_type(conn, application_id)
    if stage_type is None:
        raise SlotNotFoundError(f"application_id={application_id} 不存在")
    if stage_type != "interview":
        raise NotInterviewStageError(
            "仅对当前阶段为 interview 的投递可安排面试（design D3）"
        )


def _assert_no_conflicts(
    conn: sqlite3.Connection,
    *,
    application_id: str,
    interviewer_ids: list[str],
    start_at: str,
    end_at: str,
    exclude_slot_id: str | None = None,
) -> None:
    candidate, availability_by_interviewer, existing = load_conflict_inputs(
        conn,
        application_id=application_id,
        interviewer_ids=interviewer_ids,
        start_at=start_at,
        end_at=end_at,
        exclude_slot_id=exclude_slot_id,
    )
    conflicts = check(
        candidate=candidate,
        interviewer_ids=interviewer_ids,
        availability_by_interviewer=availability_by_interviewer,
        existing_slots=existing,
    )
    if conflicts:
        raise ConflictError(conflicts)


def _insert_history(
    conn: sqlite3.Connection,
    *,
    application_id: str,
    action: str,
    actor: str,
    detail: dict | None,
) -> None:
    conn.execute(
        "INSERT INTO application_stage_history "
        "(id, application_id, from_stage_id, to_stage_id, actor_type, actor, action, detail_json) "
        "VALUES (?, ?, 'interview', 'interview', 'human', ?, ?, ?)",
        (
            str(uuid.uuid4()),
            application_id,
            actor,
            action,
            json.dumps(detail, ensure_ascii=False) if detail is not None else None,
        ),
    )


@idempotent_effect("effect_schedule_slot")
def effect_schedule_slot(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    slot_id: str,
    application_id: str,
    interviewer_ids: list[str],
    round_: int,
    start_at: str,
    end_at: str,
    mode: str,
    location_or_link: str | None,
    actor: str,
) -> str:
    _assert_interview_stage(conn, application_id)
    if not interviewer_ids:
        raise ScheduleInputError("至少指定一位面试官（spec「指定面试官一至多位」）")
    if not start_at < end_at:
        raise ScheduleInputError(f"开始时刻必须早于结束时刻：{start_at} → {end_at}")
    if active_slot_for_round(conn, application_id, round_) is not None:
        raise SlotStateError(
            f"该投递第 {round_} 轮已存在未取消场次，请先改期或取消既有场次"
        )
    _assert_no_conflicts(
        conn,
        application_id=application_id,
        interviewer_ids=interviewer_ids,
        start_at=start_at,
        end_at=end_at,
    )
    conn.execute(
        "INSERT INTO interview_slot "
        "(id, application_id, round, start_at, end_at, mode, location_or_link, created_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (slot_id, application_id, round_, start_at, end_at, mode, location_or_link, actor),
    )
    for interviewer_id in interviewer_ids:
        conn.execute(
            "INSERT INTO interview_slot_interviewer (interview_slot_id, interviewer_id) "
            "VALUES (?, ?)",
            (slot_id, interviewer_id),
        )
    _insert_history(
        conn,
        application_id=application_id,
        action=HISTORY_ACTION_SCHEDULED,
        actor=actor,
        detail={
            "round": round_,
            "start_at": start_at,
            "end_at": end_at,
            "mode": mode,
        },
    )
    return slot_id


@idempotent_effect("effect_reschedule_slot")
def effect_reschedule_slot(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    slot_id: str,
    start_at: str,
    end_at: str,
    actor: str,
) -> None:
    slot = slot_for(conn, slot_id)
    if slot is None:
        raise SlotNotFoundError(f"slot_id={slot_id} 不存在")
    if slot["status"] not in _SCHEDULABLE_STATUSES:
        raise SlotStateError("仅已安排（scheduled/rescheduled）的场次可改期")
    if not start_at < end_at:
        raise ScheduleInputError(f"开始时刻必须早于结束时刻：{start_at} → {end_at}")
    _assert_no_conflicts(
        conn,
        application_id=slot["application_id"],
        interviewer_ids=slot["interviewer_ids"],
        start_at=start_at,
        end_at=end_at,
        exclude_slot_id=slot_id,
    )
    original_start, original_end = slot["start_at"], slot["end_at"]
    conn.execute(
        "UPDATE interview_slot SET start_at = ?, end_at = ?, status = 'rescheduled', "
        "updated_by = ?, updated_at = ? WHERE id = ?",
        (start_at, end_at, actor, sqlite_utc_now(), slot_id),
    )
    _insert_history(
        conn,
        application_id=slot["application_id"],
        action=HISTORY_ACTION_RESCHEDULED,
        actor=actor,
        detail={
            "original_start_at": original_start,
            "original_end_at": original_end,
            "new_start_at": start_at,
            "new_end_at": end_at,
        },
    )


@idempotent_effect("effect_cancel_slot")
def effect_cancel_slot(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    slot_id: str,
    cancel_reason: str,
    actor: str,
) -> None:
    slot = slot_for(conn, slot_id)
    if slot is None:
        raise SlotNotFoundError(f"slot_id={slot_id} 不存在")
    if slot["status"] not in _SCHEDULABLE_STATUSES:
        raise SlotStateError("仅已安排的场次可取消")
    reason = (cancel_reason or "").strip()
    if not reason:
        raise ValueError("取消原因不能为空")
    conn.execute(
        "UPDATE interview_slot SET status = 'cancelled', cancel_reason = ?, "
        "updated_by = ?, updated_at = ? WHERE id = ?",
        (reason, actor, sqlite_utc_now(), slot_id),
    )
    _insert_history(
        conn,
        application_id=slot["application_id"],
        action=HISTORY_ACTION_CANCELLED,
        actor=actor,
        detail={"cancel_reason": reason},
    )


@idempotent_effect("effect_complete_slot")
def effect_complete_slot(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    slot_id: str,
    target_status: str,
    actor: str,
) -> None:
    if target_status not in ("completed", "no_show"):
        raise ValueError("target_status 只能是 completed 或 no_show")
    slot = slot_for(conn, slot_id)
    if slot is None:
        raise SlotNotFoundError(f"slot_id={slot_id} 不存在")
    if slot["status"] not in _SCHEDULABLE_STATUSES:
        raise SlotStateError("仅已安排的场次可标记完成/未出席")
    if sqlite_utc_now() < slot["start_at"]:
        raise CompletionBeforeStartError("面试开始时刻之前不可标记完成/未出席")
    conn.execute(
        "UPDATE interview_slot SET status = ?, updated_by = ?, updated_at = ? WHERE id = ?",
        (target_status, actor, sqlite_utc_now(), slot_id),
    )
    _insert_history(
        conn,
        application_id=slot["application_id"],
        action=target_status,
        actor=actor,
        detail=None,
    )
