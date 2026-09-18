from __future__ import annotations

import sqlite3

import pytest

from app.storage.db import init_schema
from app.storage.rejection import InvalidRejectionReason, write_rejection


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    c.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    c.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    c.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'hash1', 'alice')"
    )
    c.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('a1', 'c1', 'j1', 'r1', 'initial')"
    )
    c.commit()
    return c


def test_write_rejection_hard_rule_succeeds(conn):
    rejection_id = write_rejection(
        conn, application_id="a1", reason_type="hard_rule",
        rule_ref="experience_years:gte:5", human_readable="工作年限不满足",
        decided_by="hr1",
    )
    row = conn.execute(
        "SELECT reason_type, rule_ref, decided_by FROM rejection_record WHERE id = ?",
        (rejection_id,),
    ).fetchone()
    assert row == ("hard_rule", "experience_years:gte:5", "hr1")


def test_write_rejection_moves_application_to_rejected_stage(conn):
    write_rejection(conn, application_id="a1", reason_type="human_decision", decided_by="hr1")
    row = conn.execute(
        "SELECT status, current_stage_id FROM application WHERE id = 'a1'"
    ).fetchone()
    assert row == ("rejected", "rejected")


def test_write_rejection_writes_stage_history_with_from_stage(conn):
    write_rejection(conn, application_id="a1", reason_type="human_decision", decided_by="hr1")
    row = conn.execute(
        "SELECT from_stage_id, to_stage_id, actor_type, actor FROM application_stage_history "
        "WHERE application_id = 'a1' AND to_stage_id = 'rejected'"
    ).fetchone()
    assert row == ("initial", "rejected", "human", "hr1")


def test_write_rejection_human_decision_without_rule_ref_ok(conn):
    write_rejection(conn, application_id="a1", reason_type="human_decision", decided_by="hr1")


def test_write_rejection_hard_rule_without_rule_ref_raises(conn):
    with pytest.raises(InvalidRejectionReason):
        write_rejection(conn, application_id="a1", reason_type="hard_rule", decided_by="hr1")


def test_write_rejection_ai_score_rejected_at_application_layer(conn):
    with pytest.raises(InvalidRejectionReason):
        write_rejection(conn, application_id="a1", reason_type="ai_score", decided_by="hr1")


def test_write_rejection_blank_decided_by_raises(conn):
    with pytest.raises(InvalidRejectionReason):
        write_rejection(conn, application_id="a1", reason_type="human_decision", decided_by="   ")


def test_ai_score_rejected_at_check_constraint_layer_bypassing_application(conn):
    """合规红线第二道防线：绕过 write_rejection() 直接 INSERT，CHECK 约束
    仍然拒绝。"""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO rejection_record (id, application_id, reason_type, decided_by) "
            "VALUES ('rej-bad', 'a1', 'ai_score', 'hr1')"
        )
