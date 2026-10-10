"""U3 新增表 `invitation_outcome_log` 的 schema 回归（TDD 先写测试）。

反证三条结构性保证：`(slot_id, status)` 唯一；sent 必带渠道；declined 必带原因。
`rejection_record` 与阶段列**不在本表里**——候选人拒绝邀约不是淘汰（design D9）。"""
from __future__ import annotations

import sqlite3

import pytest

from app.storage.db import get_connection, init_schema


def _columns(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "u3_schema.db"))
    init_schema(c)
    return c


def _seed_slot(conn, slot_id: str = "s1") -> str:
    conn.execute("INSERT OR IGNORE INTO job (id, title) VALUES ('j1', '嵌入式软件工程师')")
    conn.execute("INSERT OR IGNORE INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT OR IGNORE INTO resume "
        "(id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'sha-1', 'hr-1')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO application "
        "(id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1', 'c1', 'j1', 'r1', 'initial')"
    )
    conn.execute(
        "INSERT INTO interview_slot (id, application_id, round, start_at, end_at, mode) "
        "VALUES (?, 'app1', 1, '2026-10-12 14:00', '2026-10-12 15:00', 'onsite')",
        (slot_id,),
    )
    conn.commit()
    return slot_id


def _insert(
    conn, *, outcome_id="o1", slot_id="s1", status="sent", channel="wechat",
    reason=None, actor="hr-1",
) -> None:
    conn.execute(
        "INSERT INTO invitation_outcome_log (id, slot_id, status, channel, reason, actor) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (outcome_id, slot_id, status, channel, reason, actor),
    )
    conn.commit()


def test_outcome_table_columns(conn):
    assert _columns(conn, "invitation_outcome_log") == {
        "id", "slot_id", "status", "channel", "reason", "actor", "at",
    }


def test_outcome_has_no_stage_or_rejection_columns(conn):
    """「候选人拒绝不产生淘汰记录、阶段不动」的结构保证：本表没有这些列，
    也没有指向 rejection_record 的任何引用。"""
    columns = _columns(conn, "invitation_outcome_log")
    assert "current_stage_id" not in columns
    assert "stage_id" not in columns
    assert "rejection_record_id" not in columns


def test_outcome_unique_per_slot_and_status(conn):
    _seed_slot(conn)
    _insert(conn, outcome_id="o1", status="sent", channel="wechat")
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, outcome_id="o2", status="sent", channel="email")


def test_outcome_same_status_allowed_across_slots(conn):
    _seed_slot(conn, slot_id="s1")
    _seed_slot(conn, slot_id="s2")
    _insert(conn, outcome_id="o1", slot_id="s1", status="sent", channel="wechat")
    _insert(conn, outcome_id="o2", slot_id="s2", status="sent", channel="wechat")
    assert conn.execute("SELECT COUNT(*) FROM invitation_outcome_log").fetchone()[0] == 2


def test_outcome_sent_requires_channel(conn):
    _seed_slot(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, status="sent", channel=None)


def test_outcome_declined_requires_reason(conn):
    _seed_slot(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, status="declined", channel=None, reason=None)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, status="declined", channel=None, reason="   ")


def test_outcome_declined_with_reason_is_accepted(conn):
    _seed_slot(conn)
    _insert(conn, status="declined", channel=None, reason="已接受其他 offer")
    row = conn.execute(
        "SELECT status, channel, reason FROM invitation_outcome_log WHERE id='o1'"
    ).fetchone()
    assert row == ("declined", None, "已接受其他 offer")


def test_outcome_rejects_unknown_status(conn):
    _seed_slot(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, status="rejected", channel=None)


def test_outcome_rejects_unknown_channel(conn):
    _seed_slot(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, status="sent", channel="carrier_pigeon")


def test_outcome_rejects_channel_on_non_sent(conn):
    _seed_slot(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, status="confirmed", channel="wechat")


def test_outcome_slot_fk_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, slot_id="no-such-slot")


def test_outcome_actor_required(conn):
    _seed_slot(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO invitation_outcome_log (id, slot_id, status, channel, actor) "
            "VALUES ('o9', 's1', 'sent', 'wechat', NULL)"
        )


def test_outcome_at_defaults_to_now(conn):
    _seed_slot(conn)
    _insert(conn)
    at = conn.execute("SELECT at FROM invitation_outcome_log WHERE id='o1'").fetchone()[0]
    assert at and at != ""
