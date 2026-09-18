from __future__ import annotations

import sqlite3

import pytest

from app.storage.appeal import AppealRecordNotFound, IllegalAppealTransition, transition_appeal
from app.storage.db import init_schema
from app.storage.rejection import write_rejection


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
        "VALUES ('a1', 'c1', 'j1', 'r1', 'screening')"
    )
    c.commit()
    return c


@pytest.fixture
def rejection_id(conn):
    return write_rejection(
        conn, application_id="a1", reason_type="hard_rule",
        rule_ref="experience_years:gte:5", human_readable="工作年限不满足",
        decided_by="hr1",
    )


def test_none_to_requested_is_legal(conn, rejection_id):
    result = transition_appeal(conn, rejection_record_id=rejection_id, to_status="requested", actor="hr2")
    assert result == {"appeal_status": "requested", "already_applied": False}
    row = conn.execute(
        "SELECT appeal_status FROM rejection_record WHERE id = ?", (rejection_id,)
    ).fetchone()
    assert row[0] == "requested"


def test_every_transition_records_an_appeal_event(conn, rejection_id):
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="requested", actor="hr2")
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="under_review", actor="hr3")
    rows = conn.execute(
        "SELECT from_status, to_status, actor FROM appeal_event "
        "WHERE rejection_record_id = ? ORDER BY occurred_at",
        (rejection_id,),
    ).fetchall()
    assert rows == [("none", "requested", "hr2"), ("requested", "under_review", "hr3")]


def test_full_legal_path_to_upheld(conn, rejection_id):
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="requested", actor="hr2")
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="under_review", actor="hr3")
    result = transition_appeal(conn, rejection_record_id=rejection_id, to_status="upheld", actor="hr3")
    assert result["appeal_status"] == "upheld"


def test_illegal_transition_skipping_a_state_raises(conn, rejection_id):
    with pytest.raises(IllegalAppealTransition):
        transition_appeal(conn, rejection_record_id=rejection_id, to_status="under_review", actor="hr2")


def test_illegal_transition_from_terminal_state_raises(conn, rejection_id):
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="requested", actor="hr2")
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="under_review", actor="hr3")
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="upheld", actor="hr3")
    with pytest.raises(IllegalAppealTransition):
        transition_appeal(conn, rejection_record_id=rejection_id, to_status="overturned", actor="hr3")


def test_repeat_submit_same_target_is_idempotent_no_second_event(conn, rejection_id):
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="requested", actor="hr2")
    result = transition_appeal(conn, rejection_record_id=rejection_id, to_status="requested", actor="hr2")
    assert result == {"appeal_status": "requested", "already_applied": True}
    count = conn.execute(
        "SELECT COUNT(*) FROM appeal_event WHERE rejection_record_id = ?", (rejection_id,)
    ).fetchone()[0]
    assert count == 1


def test_overturned_restores_application_to_pre_rejection_stage(conn, rejection_id):
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="requested", actor="hr2")
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="under_review", actor="hr3")
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="overturned", actor="hr3")

    app_row = conn.execute(
        "SELECT status, current_stage_id FROM application WHERE id = 'a1'"
    ).fetchone()
    assert app_row == ("active", "screening")

    history_row = conn.execute(
        "SELECT from_stage_id, to_stage_id, actor_type, actor FROM application_stage_history "
        "WHERE application_id = 'a1' AND from_stage_id = 'rejected'"
    ).fetchone()
    assert history_row == ("rejected", "screening", "human", "hr3")


def test_overturned_does_not_delete_original_rejection_record(conn, rejection_id):
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="requested", actor="hr2")
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="under_review", actor="hr3")
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="overturned", actor="hr3")
    row = conn.execute(
        "SELECT reason_type, rule_ref FROM rejection_record WHERE id = ?", (rejection_id,)
    ).fetchone()
    assert row == ("hard_rule", "experience_years:gte:5")


def test_unknown_rejection_record_raises_not_found(conn):
    with pytest.raises(AppealRecordNotFound):
        transition_appeal(conn, rejection_record_id="no-such-id", to_status="requested", actor="hr2")


def test_blank_actor_raises(conn, rejection_id):
    with pytest.raises(ValueError):
        transition_appeal(conn, rejection_record_id=rejection_id, to_status="requested", actor="  ")
