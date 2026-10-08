"""interview-scheduling U1 · Task 2 的 schema 回归（TDD 先写测试）。

覆盖 `interview_slot` 与 `interview_slot_interviewer` 两张表：
- 新库 `init_schema()` 后两表齐全、列齐、索引在；
- 列默认值（status='scheduled' / invitation_status='none' /
  reminder_sent_count=0 / kind='human'）是 spec 枚举在存储层的落点；
- 全部 CHECK 反证：非法 status/mode/invitation_status/kind 被拒；
- 复合主键天然保证同一场次同一面试官只出现一次；
- 两个外键在 `PRAGMA foreign_keys=ON` 下被强制。

⛔ 不改动既有表；本文件只测 Task 2 引入的两张新表。
"""
from __future__ import annotations

import sqlite3

import pytest

from app.storage.db import get_connection, init_schema


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _index_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)
    ).fetchone()
    return row is not None


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "slot.db"))
    init_schema(c)
    return c


def _seed_application(conn: sqlite3.Connection, application_id: str = "app1") -> str:
    """建一条可供 interview_slot 外键引用的投递（走既有 job→candidate→resume→application 链）。"""
    conn.execute(
        "INSERT INTO job (id, title) VALUES ('j1', '嵌入式软件工程师')"
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'sha-1', 'hr-1')"
    )
    # current_stage_id 用既有三值 stage('initial') 之一——`interview` 行由 Task 5 加，
    # 本任务的 schema 反证不应依赖它。
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, 'c1', 'j1', 'r1', 'initial')",
        (application_id,),
    )
    conn.commit()
    return application_id


def _seed_interviewer(conn: sqlite3.Connection, interviewer_id: str = "iv1") -> str:
    conn.execute(
        "INSERT INTO hr_account (id, username, password_hash, password_salt) "
        "VALUES ('acct1', 'alice', 'h', 's')"
    )
    conn.execute(
        "INSERT INTO interviewer (id, account_id, name) VALUES (?, 'acct1', '汤丽萍')",
        (interviewer_id,),
    )
    conn.commit()
    return interviewer_id


def _insert_slot(
    conn: sqlite3.Connection,
    *,
    slot_id: str = "s1",
    application_id: str = "app1",
    status: str = "scheduled",
    mode: str = "onsite",
    kind: str = "human",
    invitation_status: str = "none",
) -> None:
    conn.execute(
        "INSERT INTO interview_slot "
        "(id, application_id, round, start_at, end_at, mode, status, kind, invitation_status) "
        "VALUES (?, ?, 1, '2026-10-09 14:00', '2026-10-09 15:00', ?, ?, ?, ?)",
        (slot_id, application_id, mode, status, kind, invitation_status),
    )


# ── 建表齐全 ──────────────────────────────────────────────────────────────


def test_interview_slot_tables_exist(conn):
    assert _table_exists(conn, "interview_slot")
    assert _table_exists(conn, "interview_slot_interviewer")


def test_interview_slot_has_expected_columns(conn):
    assert _columns(conn, "interview_slot") == {
        "id",
        "application_id",
        "round",
        "start_at",
        "end_at",
        "mode",
        "location_or_link",
        "status",
        "cancel_reason",
        "invitation_status",
        "sent_channel",
        "reminder_sent_count",
        "kind",
        "created_by",
        "updated_by",
        "created_at",
        "updated_at",
    }


def test_interview_slot_interviewer_has_expected_columns(conn):
    assert _columns(conn, "interview_slot_interviewer") == {
        "interview_slot_id",
        "interviewer_id",
    }


def test_interview_slot_indexes_exist(conn):
    assert _index_exists(conn, "idx_interview_slot_application")
    assert _index_exists(conn, "idx_interview_slot_interviewer_interviewer")


# ── 默认值 ────────────────────────────────────────────────────────────────


def test_interview_slot_defaults(conn):
    _seed_application(conn)
    conn.execute(
        "INSERT INTO interview_slot (id, application_id, round, start_at, end_at, mode) "
        "VALUES ('s1', 'app1', 1, '2026-10-09 14:00', '2026-10-09 15:00', 'onsite')"
    )
    conn.commit()
    row = conn.execute(
        "SELECT status, invitation_status, reminder_sent_count, kind "
        "FROM interview_slot WHERE id='s1'"
    ).fetchone()
    assert row == ("scheduled", "none", 0, "human")


# ── CHECK 正反证 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "status", ["scheduled", "rescheduled", "cancelled", "completed", "no_show"]
)
def test_interview_slot_accepts_each_valid_status(conn, status):
    _seed_application(conn)
    _insert_slot(conn, status=status)
    conn.commit()


def test_interview_slot_rejects_invalid_status(conn):
    _seed_application(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_slot(conn, status="bogus")


@pytest.mark.parametrize("mode", ["onsite", "phone", "online"])
def test_interview_slot_accepts_each_valid_mode(conn, mode):
    _seed_application(conn)
    _insert_slot(conn, mode=mode)
    conn.commit()


def test_interview_slot_rejects_invalid_mode(conn):
    _seed_application(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_slot(conn, mode="bogus")


@pytest.mark.parametrize(
    "invitation_status",
    ["none", "drafted", "sent", "confirmed", "declined", "reschedule_requested"],
)
def test_interview_slot_accepts_each_valid_invitation_status(conn, invitation_status):
    _seed_application(conn)
    _insert_slot(conn, invitation_status=invitation_status)
    conn.commit()


def test_interview_slot_rejects_invalid_invitation_status(conn):
    _seed_application(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_slot(conn, invitation_status="bogus")


def test_interview_slot_rejects_invalid_kind(conn):
    """kind 一期只有 'human'，M3 若纳入自动排期再加 'ai_live'，本包不预建。"""
    _seed_application(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_slot(conn, kind="ai_live")


# ── 多对多与主键 ──────────────────────────────────────────────────────────


def test_interview_slot_interviewer_allows_multiple_per_slot(conn):
    _seed_application(conn)
    _insert_slot(conn)
    conn.execute(
        "INSERT INTO hr_account (id, username, password_hash, password_salt) "
        "VALUES ('acct1', 'alice', 'h', 's')"
    )
    conn.execute(
        "INSERT INTO hr_account (id, username, password_hash, password_salt) "
        "VALUES ('acct2', 'bob', 'h', 's')"
    )
    conn.execute(
        "INSERT INTO interviewer (id, account_id, name) VALUES ('iv1', 'acct1', '甲')"
    )
    conn.execute(
        "INSERT INTO interviewer (id, account_id, name) VALUES ('iv2', 'acct2', '乙')"
    )
    conn.execute(
        "INSERT INTO interview_slot_interviewer (interview_slot_id, interviewer_id) "
        "VALUES ('s1', 'iv1'), ('s1', 'iv2')"
    )
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM interview_slot_interviewer WHERE interview_slot_id='s1'"
    ).fetchone()[0]
    assert count == 2


def test_interview_slot_interviewer_primary_key_rejects_duplicate(conn):
    _seed_application(conn)
    _insert_slot(conn)
    _seed_interviewer(conn)
    conn.execute(
        "INSERT INTO interview_slot_interviewer (interview_slot_id, interviewer_id) "
        "VALUES ('s1', 'iv1')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_slot_interviewer (interview_slot_id, interviewer_id) "
            "VALUES ('s1', 'iv1')"
        )


# ── 外键强制 ──────────────────────────────────────────────────────────────


def test_interview_slot_application_fk_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _insert_slot(conn, application_id="no-such-application")


def test_interview_slot_interviewer_fk_enforced(conn):
    _seed_application(conn)
    _insert_slot(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_slot_interviewer (interview_slot_id, interviewer_id) "
            "VALUES ('s1', 'no-such-interviewer')"
        )
