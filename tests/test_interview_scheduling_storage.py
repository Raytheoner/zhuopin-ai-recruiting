"""排期存储层 `app/storage/interview_scheduling.py` 的单元测试（U2 Task 3）。

覆盖 tasks 2.2–2.5 的存储侧契约：可用时段的三条业务校验（重叠拒绝、被场次占用
不可撤销、只写本人/代登记留痕字段）、同窗口重复登记的幂等，以及冲突检查的
DB 输入装配与两个页面（当日安排页 / HR 排期页）的数据装配。

⛔ 本文件只测存储层读写，不测 `effect_*` 节点（那在
`tests/test_interview_scheduling_effect.py`）。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.agents.conflict_check import CandidateWindow
from app.storage.db import get_connection, init_schema
from app.storage.interview_scheduling import (
    AvailabilityNotFoundError,
    AvailabilityOccupiedError,
    AvailabilityOverlapError,
    InterviewerNotInRosterError,
    active_slot_for_round,
    application_schedule_data,
    current_stage_type,
    day_schedule,
    delete_availability,
    interviewer_id_for_username,
    list_availability,
    load_conflict_inputs,
    register_availability,
    slot_for,
)


def _conn(tmp_path):
    conn = get_connection(str(tmp_path / "sched.db"))
    init_schema(conn)
    return conn


def _seed(conn, *, stage_id: str = "interview", second_application: bool = False):
    """最小可用域数据：两个 HR 账号 → 两个面试官；两个岗位投递（可选）。"""
    conn.execute(
        "INSERT INTO hr_account (id, username, password_hash, password_salt) "
        "VALUES ('h1', 'hr-1', 'h', 's')"
    )
    conn.execute(
        "INSERT INTO hr_account (id, username, password_hash, password_salt) "
        "VALUES ('h2', 'iv-1', 'h', 's')"
    )
    conn.execute(
        "INSERT INTO hr_account (id, username, password_hash, password_salt) "
        "VALUES ('h3', 'iv-2', 'h', 's')"
    )
    conn.execute(
        "INSERT INTO interviewer (id, account_id, name, department, interviewable_jobs, enabled) "
        "VALUES ('iv1', 'h2', '汤丽萍', '人事部', '[]', 1)"
    )
    conn.execute(
        "INSERT INTO interviewer (id, account_id, name, department, interviewable_jobs, enabled) "
        "VALUES ('iv2', 'h3', '李明', '研发部', '[]', 1)"
    )
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式软件工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'sha1', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1', 'c1', 'j1', 'r1', ?)",
        (stage_id,),
    )
    if second_application:
        conn.execute("INSERT INTO candidate (id, name) VALUES ('c2', '李四')")
        conn.execute(
            "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
            "VALUES ('r2', 'j1', 'synthetic', 'b.pdf', 'sha2', 'hr-1')"
        )
        conn.execute(
            "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
            "VALUES ('app2', 'c2', 'j1', 'r2', 'interview')"
        )
    conn.commit()


def _availability(
    conn,
    *,
    availability_id: str = "av1",
    interviewer_id: str = "iv1",
    start_at: str = "2026-10-14 06:00",
    end_at: str = "2026-10-14 08:00",
    note: str | None = None,
    registered_by: str = "hr-1",
    on_behalf: int = 0,
) -> None:
    conn.execute(
        "INSERT INTO interviewer_availability "
        "(id, interviewer_id, start_at, end_at, note, registered_by, on_behalf) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (availability_id, interviewer_id, start_at, end_at, note, registered_by, on_behalf),
    )
    conn.commit()


def _slot(
    conn,
    *,
    slot_id: str = "s1",
    application_id: str = "app1",
    round_: int = 1,
    start_at: str = "2026-10-14 06:00",
    end_at: str = "2026-10-14 07:00",
    mode: str = "onsite",
    status: str = "scheduled",
    invitation_status: str = "none",
    created_at: str | None = None,
    created_by: str = "hr-1",
    interviewer_ids: tuple[str, ...] = ("iv1",),
) -> None:
    conn.execute(
        "INSERT INTO interview_slot "
        "(id, application_id, round, start_at, end_at, mode, status, "
        "invitation_status, created_by, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')))",
        (
            slot_id,
            application_id,
            round_,
            start_at,
            end_at,
            mode,
            status,
            invitation_status,
            created_by,
            created_at,
        ),
    )
    for interviewer_id in interviewer_ids:
        conn.execute(
            "INSERT INTO interview_slot_interviewer (interview_slot_id, interviewer_id) "
            "VALUES (?, ?)",
            (slot_id, interviewer_id),
        )
    conn.commit()


# ── 可用时段：登记 ───────────────────────────────────────────────────────


def test_register_availability_persists_and_lists(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)

    row = register_availability(
        conn,
        interviewer_id="iv1",
        start_at="2026-10-14 06:00",
        end_at="2026-10-14 08:00",
        note="下午可以",
        registered_by="hr-1",
        on_behalf=False,
    )

    assert row["interviewer_id"] == "iv1"
    assert row["start_at"] == "2026-10-14 06:00"
    assert row["end_at"] == "2026-10-14 08:00"
    assert row["note"] == "下午可以"
    assert row["registered_by"] == "hr-1"
    assert row["on_behalf"] is False
    assert row["created_at"]

    assert [r["id"] for r in list_availability(conn, interviewer_id="iv1")] == [row["id"]]
    assert list_availability(conn, interviewer_id="iv2") == []


def test_register_same_window_twice_returns_existing_row(tmp_path):
    """同面试官同起止重复登记＝幂等（design D2 / tasks 2.2，不报重叠错）。"""
    conn = _conn(tmp_path)
    _seed(conn)

    first = register_availability(
        conn, interviewer_id="iv1", start_at="2026-10-14 06:00",
        end_at="2026-10-14 08:00", note=None, registered_by="hr-1", on_behalf=False,
    )
    second = register_availability(
        conn, interviewer_id="iv1", start_at="2026-10-14 06:00",
        end_at="2026-10-14 08:00", note="换个备注也不生效", registered_by="hr-1",
        on_behalf=False,
    )

    assert second["id"] == first["id"]
    assert len(list_availability(conn, interviewer_id="iv1")) == 1


def test_register_overlapping_window_rejected(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    _availability(conn, start_at="2026-10-14 06:00", end_at="2026-10-14 08:00")

    with pytest.raises(AvailabilityOverlapError):
        register_availability(
            conn, interviewer_id="iv1", start_at="2026-10-14 07:00",
            end_at="2026-10-14 09:00", note=None, registered_by="hr-1", on_behalf=False,
        )

    assert len(list_availability(conn, interviewer_id="iv1")) == 1


def test_register_adjacent_window_allowed(tmp_path):
    """半开区间：上一段 08:00 结束、下一段 08:00 开始合法。"""
    conn = _conn(tmp_path)
    _seed(conn)
    _availability(conn, start_at="2026-10-14 06:00", end_at="2026-10-14 08:00")

    register_availability(
        conn, interviewer_id="iv1", start_at="2026-10-14 08:00",
        end_at="2026-10-14 09:00", note=None, registered_by="hr-1", on_behalf=False,
    )

    assert len(list_availability(conn, interviewer_id="iv1")) == 2


@pytest.mark.parametrize(
    "start_at,end_at",
    [
        ("2026-10-14 08:00", "2026-10-14 08:00"),
        ("2026-10-14 09:00", "2026-10-14 08:00"),
    ],
)
def test_register_rejects_non_positive_window(tmp_path, start_at, end_at):
    conn = _conn(tmp_path)
    _seed(conn)

    with pytest.raises(ValueError):
        register_availability(
            conn, interviewer_id="iv1", start_at=start_at, end_at=end_at,
            note=None, registered_by="hr-1", on_behalf=False,
        )

    assert list_availability(conn, interviewer_id="iv1") == []


def test_register_unknown_interviewer_rejected(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)

    with pytest.raises(InterviewerNotInRosterError):
        register_availability(
            conn, interviewer_id="no-such-interviewer",
            start_at="2026-10-14 06:00", end_at="2026-10-14 08:00",
            note=None, registered_by="hr-1", on_behalf=False,
        )

    assert conn.execute("SELECT COUNT(*) FROM interviewer_availability").fetchone()[0] == 0


def test_register_on_behalf_records_operator(tmp_path):
    """HR 代登记：on_behalf 落 1，registered_by 记操作人（tasks 2.2 留痕）。"""
    conn = _conn(tmp_path)
    _seed(conn)

    row = register_availability(
        conn, interviewer_id="iv1", start_at="2026-10-14 06:00",
        end_at="2026-10-14 08:00", note=None, registered_by="hr-1", on_behalf=True,
    )

    assert row["on_behalf"] is True
    stored = conn.execute(
        "SELECT registered_by, on_behalf FROM interviewer_availability WHERE id = ?",
        (row["id"],),
    ).fetchone()
    assert stored == ("hr-1", 1)


# ── 可用时段：撤销 ───────────────────────────────────────────────────────


def test_delete_availability_removes_row(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    _availability(conn, availability_id="av1")

    delete_availability(conn, availability_id="av1")

    assert list_availability(conn, interviewer_id="iv1") == []


def test_delete_unknown_availability_rejected(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)

    with pytest.raises(AvailabilityNotFoundError):
        delete_availability(conn, availability_id="nope")


def test_delete_rejected_when_slot_occupies_the_window(tmp_path):
    """被未取消场次占用不可撤销（tasks 2.3）。"""
    conn = _conn(tmp_path)
    _seed(conn)
    _availability(conn, availability_id="av1", start_at="2026-10-14 06:00", end_at="2026-10-14 08:00")
    _slot(
        conn, slot_id="s1", start_at="2026-10-14 06:30", end_at="2026-10-14 07:00",
        status="scheduled", interviewer_ids=("iv1",),
    )

    with pytest.raises(AvailabilityOccupiedError):
        delete_availability(conn, availability_id="av1")

    assert [r["id"] for r in list_availability(conn, interviewer_id="iv1")] == ["av1"]


def test_delete_allowed_when_occupying_slot_is_cancelled(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    _availability(conn, availability_id="av1", start_at="2026-10-14 06:00", end_at="2026-10-14 08:00")
    _slot(
        conn, slot_id="s1", start_at="2026-10-14 06:30", end_at="2026-10-14 07:00",
        status="cancelled", interviewer_ids=("iv1",),
    )

    delete_availability(conn, availability_id="av1")

    assert list_availability(conn, interviewer_id="iv1") == []


def test_delete_allowed_when_overlapping_slot_is_another_interviewers(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    _availability(conn, availability_id="av1", start_at="2026-10-14 06:00", end_at="2026-10-14 08:00")
    _slot(
        conn, slot_id="s1", start_at="2026-10-14 06:30", end_at="2026-10-14 07:00",
        status="scheduled", interviewer_ids=("iv2",),
    )

    delete_availability(conn, availability_id="av1")

    assert list_availability(conn, interviewer_id="iv1") == []


# ── 身份解析 ─────────────────────────────────────────────────────────────


def test_interviewer_id_for_username_maps_account_to_roster_row(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)

    assert interviewer_id_for_username(conn, "iv-1") == "iv1"
    assert interviewer_id_for_username(conn, "hr-1") is None
    assert interviewer_id_for_username(conn, "no-such-user") is None


# ── 冲突检查输入装配 ─────────────────────────────────────────────────────


def test_load_conflict_inputs_assembles_candidate_availability_and_active_slots(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn, second_application=True)
    _availability(conn, availability_id="av1", interviewer_id="iv1",
                  start_at="2026-10-14 06:00", end_at="2026-10-14 08:00")
    _slot(conn, slot_id="s1", application_id="app2",
          start_at="2026-10-14 06:30", end_at="2026-10-14 06:45",
          status="scheduled", interviewer_ids=("iv1",))
    _slot(conn, slot_id="s2", application_id="app2",
          start_at="2026-10-14 07:00", end_at="2026-10-14 07:30",
          status="cancelled", interviewer_ids=("iv1",))

    candidate, availability, existing = load_conflict_inputs(
        conn, application_id="app1", interviewer_ids=["iv1"],
        start_at="2026-10-14 06:00", end_at="2026-10-14 07:00",
    )

    assert candidate == CandidateWindow(
        application_id="app1", start_at="2026-10-14 06:00", end_at="2026-10-14 07:00"
    )
    assert availability == {"iv1": [("2026-10-14 06:00", "2026-10-14 08:00")]}
    assert [s.slot_id for s in existing] == ["s1"]  # 已取消场次不进冲突输入
    assert existing[0].application_id == "app2"
    assert existing[0].interviewer_ids == ["iv1"]


def test_load_conflict_inputs_excludes_the_slot_being_edited(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    _slot(conn, slot_id="s1", start_at="2026-10-14 06:30", end_at="2026-10-14 06:45",
          status="scheduled", interviewer_ids=("iv1",))

    _candidate, _availability, existing = load_conflict_inputs(
        conn, application_id="app1", interviewer_ids=["iv1"],
        start_at="2026-10-14 06:00", end_at="2026-10-14 07:00",
        exclude_slot_id="s1",
    )

    assert existing == []


def test_load_conflict_inputs_reports_missing_availability_as_empty_list(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)

    _candidate, availability, existing = load_conflict_inputs(
        conn, application_id="app1", interviewer_ids=["iv1", "iv2"],
        start_at="2026-10-14 06:00", end_at="2026-10-14 07:00",
    )

    assert availability == {"iv1": [], "iv2": []}
    assert existing == []


# ── 场次查询 ─────────────────────────────────────────────────────────────


def test_slot_for_returns_none_for_unknown_slot(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)

    assert slot_for(conn, "nope") is None


def test_slot_for_returns_slot_with_interviewers(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    _slot(conn, slot_id="s1", interviewer_ids=("iv2", "iv1"))

    slot = slot_for(conn, "s1")

    assert slot["slot_id"] == "s1"
    assert slot["application_id"] == "app1"
    assert slot["round"] == 1
    assert slot["mode"] == "onsite"
    assert slot["status"] == "scheduled"
    assert slot["invitation_status"] == "none"
    assert slot["interviewer_ids"] == ["iv1", "iv2"]
    assert slot["needs_invitation_followup"] is False


@pytest.mark.parametrize(
    "status,invitation_status,expected",
    [
        ("scheduled", "none", True),
        ("rescheduled", "none", True),
        ("scheduled", "sent", False),
        ("cancelled", "none", False),
        ("completed", "none", False),
    ],
)
def test_needs_invitation_followup_flag(tmp_path, status, invitation_status, expected):
    """已安排但邀约结果未回填超过 2 天 → 页面级醒目标记（design 风险表）。"""
    conn = _conn(tmp_path)
    _seed(conn)
    old = (datetime.now(UTC) - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    _slot(conn, slot_id="s1", status=status, invitation_status=invitation_status,
          created_at=old)

    assert slot_for(conn, "s1")["needs_invitation_followup"] is expected


def test_needs_invitation_followup_false_within_two_days(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    fresh = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    _slot(conn, slot_id="s1", status="scheduled", invitation_status="none",
          created_at=fresh)

    assert slot_for(conn, "s1")["needs_invitation_followup"] is False


def test_active_slot_for_round_skips_cancelled_slots(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    _slot(conn, slot_id="s1", round_=1, status="scheduled")
    _slot(conn, slot_id="s2", round_=2, status="cancelled")

    assert active_slot_for_round(conn, "app1", 1)["slot_id"] == "s1"
    assert active_slot_for_round(conn, "app1", 2) is None
    assert active_slot_for_round(conn, "nope", 1) is None


def test_current_stage_type_reads_the_application_stage(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn, stage_id="screening")

    assert current_stage_type(conn, "app1") == "screening"
    assert current_stage_type(conn, "nope") is None


# ── 页面数据装配 ─────────────────────────────────────────────────────────


def test_day_schedule_returns_only_own_slots_inside_the_window(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    now = datetime.now(UTC)
    soon_start = (now + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    soon_end = (now + timedelta(days=1, hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    far_start = (now + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    far_end = (now + timedelta(days=30, hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    _slot(conn, slot_id="s1", start_at=soon_start, end_at=soon_end,
          interviewer_ids=("iv1",))
    _slot(conn, slot_id="s2", start_at=far_start, end_at=far_end,
          interviewer_ids=("iv1",))
    _slot(conn, slot_id="s3", start_at=soon_start, end_at=soon_end,
          interviewer_ids=("iv2",))
    _slot(conn, slot_id="s4", start_at=soon_start, end_at=soon_end, status="cancelled",
          interviewer_ids=("iv1",))

    rows = day_schedule(conn, interviewer_id="iv1")

    assert [r["slot_id"] for r in rows] == ["s1"]
    assert rows[0]["candidate_name"] == "张三"
    assert rows[0]["job_title"] == "嵌入式软件工程师"
    assert rows[0]["start_at"] == soon_start
    assert rows[0]["mode"] == "onsite"
    assert rows[0]["status"] == "scheduled"


def test_application_schedule_data_assembles_page_payload(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    conn.execute(
        "INSERT INTO hr_account (id, username, password_hash, password_salt) "
        "VALUES ('h4', 'iv-off', 'h', 's')"
    )
    conn.execute(
        "INSERT INTO interviewer (id, account_id, name, department, interviewable_jobs, enabled) "
        "VALUES ('iv3', 'h4', '停用面试官', '研发部', '[]', 0)"
    )
    _slot(conn, slot_id="s1", application_id="app1", interviewer_ids=("iv1",))
    conn.commit()

    data = application_schedule_data(conn, "app1")

    assert data["application_id"] == "app1"
    assert data["candidate_name"] == "张三"
    assert data["job_title"] == "嵌入式软件工程师"
    assert data["stage_type"] == "interview"
    assert sorted(i["id"] for i in data["interviewers"]) == ["iv1", "iv2"]  # 停用的不入列
    assert {"id": "iv1", "name": "汤丽萍", "department": "人事部"} in data["interviewers"]
    assert [s["slot_id"] for s in data["slots"]] == ["s1"]


def test_application_schedule_data_returns_none_for_unknown_application(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)

    assert application_schedule_data(conn, "nope") is None
