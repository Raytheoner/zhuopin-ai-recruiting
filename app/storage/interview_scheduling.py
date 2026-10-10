"""面试排期（U2）存储层：面试官可用时段 + 场次查询 + 冲突检查输入装配。

只做两件事：
1. 可用时段 CRUD 的 SQL 与业务校验（重叠拒绝、被占用不可撤、代登记留痕字段）；
2. 冲突检查纯函数（app/agents/conflict_check.py）所需的 DB 输入装配（只读）。

⛔ 本模块不写 interview_slot / application_stage_history —— 那些由
app/graph/scheduling_nodes.py 的四个 effect_* 节点在同一事务里写。
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta

from app.agents.conflict_check import CandidateWindow, ExistingSlot, check
from app.storage.db import sqlite_utc_now


class AvailabilityOverlapError(ValueError):
    """与既有可用时段重叠。"""


class AvailabilityOccupiedError(ValueError):
    """时段已被某场面试占用，不可撤销。"""


class AvailabilityNotFoundError(ValueError):
    """availability_id 不存在。"""


class InterviewerNotInRosterError(ValueError):
    """interviewer_id 不在面试官名单内。"""


class SlotNotFoundError(ValueError):
    """slot_id 不存在。"""


class SlotStateError(ValueError):
    """场次状态不允许当前动作。"""


class NotInterviewStageError(ValueError):
    """投递当前阶段不是 interview。"""


class CompletionBeforeStartError(ValueError):
    """面试开始时刻之前不可标记完成/未出席。"""


def _overlaps(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    return a_start < b_end and b_start < a_end


def interviewer_id_for_username(conn: sqlite3.Connection, username: str) -> str | None:
    row = conn.execute(
        "SELECT i.id FROM interviewer i JOIN hr_account a ON a.id = i.account_id "
        "WHERE a.username = ?",
        (username,),
    ).fetchone()
    return row[0] if row else None


def interviewer_exists(conn: sqlite3.Connection, interviewer_id: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM interviewer WHERE id = ?", (interviewer_id,)
        ).fetchone()
        is not None
    )


def _availability_dict(row) -> dict:
    (
        availability_id, interviewer_id, start_at, end_at, note,
        registered_by, on_behalf, created_at,
    ) = row
    return {
        "id": availability_id,
        "interviewer_id": interviewer_id,
        "start_at": start_at,
        "end_at": end_at,
        "note": note,
        "registered_by": registered_by,
        "on_behalf": bool(on_behalf),
        "created_at": created_at,
    }


def _availability_row(conn: sqlite3.Connection, availability_id: str) -> dict:
    row = conn.execute(
        "SELECT id, interviewer_id, start_at, end_at, note, registered_by, on_behalf, created_at "
        "FROM interviewer_availability WHERE id = ?",
        (availability_id,),
    ).fetchone()
    assert row is not None
    return _availability_dict(row)


def register_availability(
    conn: sqlite3.Connection,
    *,
    interviewer_id: str,
    start_at: str,
    end_at: str,
    note: str | None,
    registered_by: str,
    on_behalf: bool,
) -> dict:
    if not interviewer_exists(conn, interviewer_id):
        raise InterviewerNotInRosterError(f"interviewer_id={interviewer_id} 不在名单内")
    if start_at >= end_at:
        raise ValueError("start_at 必须早于 end_at")

    # 幂等：同面试官同起止时刻重复登记返回既有行（design D2 / tasks 2.2）。
    existing = conn.execute(
        "SELECT id FROM interviewer_availability "
        "WHERE interviewer_id = ? AND start_at = ? AND end_at = ?",
        (interviewer_id, start_at, end_at),
    ).fetchone()
    if existing is not None:
        return _availability_row(conn, existing[0])

    rows = conn.execute(
        "SELECT start_at, end_at FROM interviewer_availability WHERE interviewer_id = ?",
        (interviewer_id,),
    ).fetchall()
    for (s, e) in rows:
        if _overlaps(start_at, end_at, s, e):
            raise AvailabilityOverlapError(f"与既有可用时段 {s}–{e} 重叠")

    availability_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO interviewer_availability "
        "(id, interviewer_id, start_at, end_at, note, registered_by, on_behalf) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            availability_id,
            interviewer_id,
            start_at,
            end_at,
            note,
            registered_by,
            1 if on_behalf else 0,
        ),
    )
    conn.commit()
    return _availability_row(conn, availability_id)


def delete_availability(
    conn: sqlite3.Connection, *, availability_id: str
) -> None:
    row = conn.execute(
        "SELECT interviewer_id, start_at, end_at FROM interviewer_availability WHERE id = ?",
        (availability_id,),
    ).fetchone()
    if row is None:
        raise AvailabilityNotFoundError(f"availability_id={availability_id} 不存在")
    interviewer_id, start_at, end_at = row

    # 被占用不可撤：该时段与任一未取消场次（scheduled/rescheduled）重叠且该场次含该面试官。
    occupied = conn.execute(
        "SELECT 1 FROM interview_slot s "
        "JOIN interview_slot_interviewer x ON x.interview_slot_id = s.id "
        "WHERE x.interviewer_id = ? AND s.status IN ('scheduled', 'rescheduled') "
        "AND s.start_at < ? AND s.end_at > ?",
        (interviewer_id, end_at, start_at),
    ).fetchone()
    if occupied is not None:
        raise AvailabilityOccupiedError(
            "该时段已被某场面试占用，请先由 HR 改期或取消该面试"
        )

    conn.execute(
        "DELETE FROM interviewer_availability WHERE id = ?", (availability_id,)
    )
    conn.commit()


def list_availability(
    conn: sqlite3.Connection, *, interviewer_id: str
) -> list[dict]:
    rows = conn.execute(
        "SELECT id, interviewer_id, start_at, end_at, note, registered_by, on_behalf, created_at "
        "FROM interviewer_availability WHERE interviewer_id = ? ORDER BY start_at, id",
        (interviewer_id,),
    ).fetchall()
    return [_availability_dict(r) for r in rows]


def _needs_invitation_followup(
    status: str, invitation_status: str, created_at: str
) -> bool:
    """已安排但邀约结果未回填超过 2 天的醒目标记（页面级计算，无定时任务，design 风险表）。"""
    if status not in ("scheduled", "rescheduled"):
        return False
    if invitation_status != "none":
        return False
    created = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
    now = datetime.strptime(sqlite_utc_now(), "%Y-%m-%d %H:%M:%S")
    return now >= created + timedelta(days=2)


def slot_for(conn: sqlite3.Connection, slot_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, application_id, round, start_at, end_at, mode, location_or_link, "
        "status, cancel_reason, invitation_status, created_by, updated_by, created_at, updated_at "
        "FROM interview_slot WHERE id = ?",
        (slot_id,),
    ).fetchone()
    if row is None:
        return None
    (
        id_, application_id, round_, start_at, end_at, mode, location_or_link,
        status, cancel_reason, invitation_status, created_by, updated_by,
        created_at, updated_at,
    ) = row
    interviewer_ids = [
        r[0]
        for r in conn.execute(
            "SELECT interviewer_id FROM interview_slot_interviewer "
            "WHERE interview_slot_id = ? ORDER BY interviewer_id",
            (slot_id,),
        ).fetchall()
    ]
    return {
        "slot_id": id_,
        "application_id": application_id,
        "round": round_,
        "start_at": start_at,
        "end_at": end_at,
        "mode": mode,
        "location_or_link": location_or_link,
        "status": status,
        "cancel_reason": cancel_reason,
        "invitation_status": invitation_status,
        "created_by": created_by,
        "updated_by": updated_by,
        "created_at": created_at,
        "updated_at": updated_at,
        "interviewer_ids": interviewer_ids,
        "needs_invitation_followup": _needs_invitation_followup(
            status, invitation_status, created_at
        ),
    }


def list_slots_for_application(
    conn: sqlite3.Connection, application_id: str
) -> list[dict]:
    rows = conn.execute(
        "SELECT id FROM interview_slot WHERE application_id = ? ORDER BY created_at, id",
        (application_id,),
    ).fetchall()
    return [slot_for(conn, r[0]) for r in rows]


def _overlapping_active_slots(
    conn: sqlite3.Connection,
    start_at: str,
    end_at: str,
    *,
    exclude_slot_id: str | None = None,
) -> list[ExistingSlot]:
    sql = (
        "SELECT s.id, s.application_id, s.start_at, s.end_at FROM interview_slot s "
        "WHERE s.status IN ('scheduled', 'rescheduled') AND s.start_at < ? AND s.end_at > ?"
    )
    params: list[object] = [end_at, start_at]
    if exclude_slot_id is not None:
        sql += " AND s.id != ?"
        params.append(exclude_slot_id)
    rows = conn.execute(sql, params).fetchall()
    slots: list[ExistingSlot] = []
    for slot_id, application_id, s, e in rows:
        interviewer_ids = [
            r[0]
            for r in conn.execute(
                "SELECT interviewer_id FROM interview_slot_interviewer "
                "WHERE interview_slot_id = ? ORDER BY interviewer_id",
                (slot_id,),
            ).fetchall()
        ]
        slots.append(
            ExistingSlot(
                slot_id=slot_id,
                application_id=application_id,
                start_at=s,
                end_at=e,
                interviewer_ids=interviewer_ids,
            )
        )
    return slots


def load_conflict_inputs(
    conn: sqlite3.Connection,
    *,
    application_id: str,
    interviewer_ids: list[str],
    start_at: str,
    end_at: str,
    exclude_slot_id: str | None = None,
) -> tuple[CandidateWindow, dict[str, list[tuple[str, str]]], list[ExistingSlot]]:
    availability_by_interviewer: dict[str, list[tuple[str, str]]] = {}
    for interviewer_id in interviewer_ids:
        rows = conn.execute(
            "SELECT start_at, end_at FROM interviewer_availability "
            "WHERE interviewer_id = ? ORDER BY start_at",
            (interviewer_id,),
        ).fetchall()
        availability_by_interviewer[interviewer_id] = [(r[0], r[1]) for r in rows]
    existing = _overlapping_active_slots(
        conn, start_at, end_at, exclude_slot_id=exclude_slot_id
    )
    candidate = CandidateWindow(
        application_id=application_id, start_at=start_at, end_at=end_at
    )
    return candidate, availability_by_interviewer, existing


def current_stage_type(conn: sqlite3.Connection, application_id: str) -> str | None:
    row = conn.execute(
        "SELECT st.stage_type FROM application a JOIN stage st ON st.id = a.current_stage_id "
        "WHERE a.id = ?",
        (application_id,),
    ).fetchone()
    return row[0] if row else None


def active_slot_for_round(
    conn: sqlite3.Connection, application_id: str, round_: int
) -> dict | None:
    row = conn.execute(
        "SELECT id FROM interview_slot WHERE application_id = ? AND round = ? "
        "AND status IN ('scheduled', 'rescheduled')",
        (application_id, round_),
    ).fetchone()
    return slot_for(conn, row[0]) if row else None


def day_schedule(
    conn: sqlite3.Connection, *, interviewer_id: str, days: int = 7
) -> list[dict]:
    now = sqlite_utc_now()
    end_str = (
        datetime.strptime(now, "%Y-%m-%d %H:%M:%S") + timedelta(days=days)
    ).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        "SELECT s.id FROM interview_slot s "
        "JOIN interview_slot_interviewer x ON x.interview_slot_id = s.id "
        "WHERE x.interviewer_id = ? AND s.status IN ('scheduled', 'rescheduled') "
        "AND s.end_at > ? AND s.start_at < ? ORDER BY s.start_at, s.id",
        (interviewer_id, now, end_str),
    ).fetchall()
    out: list[dict] = []
    for (slot_id,) in rows:
        slot = slot_for(conn, slot_id)
        assert slot is not None
        app = conn.execute(
            "SELECT candidate_id, job_id FROM application WHERE id = ?",
            (slot["application_id"],),
        ).fetchone()
        candidate_id, job_id = app
        candidate_name = conn.execute(
            "SELECT name FROM candidate WHERE id = ?", (candidate_id,)
        ).fetchone()[0]
        job_title = conn.execute(
            "SELECT title FROM job WHERE id = ?", (job_id,)
        ).fetchone()[0]
        out.append(
            {
                "slot_id": slot["slot_id"],
                "application_id": slot["application_id"],
                "candidate_name": candidate_name,
                "job_title": job_title,
                "round": slot["round"],
                "start_at": slot["start_at"],
                "end_at": slot["end_at"],
                "mode": slot["mode"],
                "status": slot["status"],
            }
        )
    return out


def application_schedule_data(
    conn: sqlite3.Connection, application_id: str
) -> dict | None:
    app = conn.execute(
        "SELECT a.id, c.name, j.title, st.stage_type FROM application a "
        "JOIN candidate c ON c.id = a.candidate_id "
        "JOIN job j ON j.id = a.job_id "
        "JOIN stage st ON st.id = a.current_stage_id WHERE a.id = ?",
        (application_id,),
    ).fetchone()
    if app is None:
        return None
    interviewers = [
        {"id": r[0], "name": r[1], "department": r[2]}
        for r in conn.execute(
            "SELECT id, name, department FROM interviewer WHERE enabled = 1 "
            "ORDER BY name COLLATE NOCASE, id"
        ).fetchall()
    ]
    return {
        "application_id": app[0],
        "candidate_name": app[1],
        "job_title": app[2],
        "stage_type": app[3],
        "interviewers": interviewers,
        "slots": list_slots_for_application(conn, application_id),
    }
