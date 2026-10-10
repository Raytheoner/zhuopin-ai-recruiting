"""letter_nodes 的 effect_* 幂等与前置校验（U2 tasks 2.3）。"""
from __future__ import annotations

import uuid

import pytest

from app.agents.letter_drafter import LetterDraft
from app.graph.letter_nodes import (
    OfferNotApprovedError,
    RejectionRecordMissingError,
    effect_mark_letter_human_written,
    effect_persist_letter,
)
from app.storage.db import get_connection, init_schema


def _seed(conn):
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1','嵌入式工程师','approved')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1','张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
        "status, uploaded_by) VALUES ('r1','j1','synthetic','a.pdf','sha','parsed','hr')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1','c1','j1','r1','offer')"
    )
    conn.commit()


def _draft(kind="offer", run_id="run1"):
    return LetterDraft(
        kind=kind, body="【AI 生成】正文", run_id=run_id,
        response_model="deepseek-chat", prompt_version=f"letter-{kind}-v1",
    )


def _analysis_run(conn, run_id):
    conn.execute(
        "INSERT INTO analysis_run (id, configured_model, prompt_version, temperature, "
        "input_hash, raw_response) VALUES (?, 'deepseek-chat', 'letter-offer-v1', 0.0, 'h', '{}')",
        (run_id,),
    )


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "n.db"))
    init_schema(c)
    _seed(c)
    return c


def _offer(conn, status="approved"):
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, "
        "report_to, status, created_by) VALUES (?, 'app1','j1','研发部','2026-10-20',"
        "'李四', ?, 'hr')",
        (str(uuid.uuid4()), status),
    )


def test_effect_persist_increments_version(conn):
    _offer(conn)
    _analysis_run(conn, "run1")
    _analysis_run(conn, "run2")
    conn.commit()
    effect_persist_letter(
        conn, thread_id="app1", business_key="offer:run1",
        application_id="app1", kind="offer", version=1, template_version=1,
        draft=_draft(run_id="run1"), created_by="hr",
    )
    effect_persist_letter(
        conn, thread_id="app1", business_key="offer:run2",
        application_id="app1", kind="offer", version=2, template_version=1,
        draft=_draft(run_id="run2"), created_by="hr",
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM candidate_letter WHERE application_id='app1'"
    ).fetchone()[0] == 2


def test_effect_persist_same_run_id_is_noop(conn):
    _offer(conn)
    _analysis_run(conn, "run1")
    conn.commit()
    effect_persist_letter(
        conn, thread_id="app1", business_key="offer:run1",
        application_id="app1", kind="offer", version=1, template_version=1,
        draft=_draft(run_id="run1"), created_by="hr",
    )
    result = effect_persist_letter(
        conn, thread_id="app1", business_key="offer:run1",
        application_id="app1", kind="offer", version=1, template_version=1,
        draft=_draft(run_id="run1"), created_by="hr",
    )
    assert result is None
    assert conn.execute(
        "SELECT COUNT(*) FROM candidate_letter WHERE application_id='app1'"
    ).fetchone()[0] == 1


def test_offer_requires_approved_status(conn):
    _offer(conn, status="pending_approval")
    _analysis_run(conn, "run1")
    conn.commit()
    with pytest.raises(OfferNotApprovedError):
        effect_persist_letter(
            conn, thread_id="app1", business_key="offer:run1",
            application_id="app1", kind="offer", version=1, template_version=1,
            draft=_draft(run_id="run1"), created_by="hr",
        )


def test_rejection_requires_rejection_record(conn):
    _analysis_run(conn, "run1")
    conn.commit()
    with pytest.raises(RejectionRecordMissingError):
        effect_persist_letter(
            conn, thread_id="app1", business_key="rejection:run1",
            application_id="app1", kind="rejection", version=1, template_version=1,
            draft=_draft(kind="rejection", run_id="run1"), created_by="hr",
        )


def test_rejection_succeeds_with_rejection_record(conn):
    conn.execute(
        "INSERT INTO rejection_record (id, application_id, reason_type, decided_by) "
        "VALUES ('rr1','app1','human_decision','hr')"
    )
    _analysis_run(conn, "run1")
    conn.commit()
    effect_persist_letter(
        conn, thread_id="app1", business_key="rejection:run1",
        application_id="app1", kind="rejection", version=1, template_version=1,
        draft=_draft(kind="rejection", run_id="run1"), created_by="hr",
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM candidate_letter WHERE kind='rejection'"
    ).fetchone()[0] == 1


def test_mark_human_written_records_authorship(conn):
    _offer(conn)
    _analysis_run(conn, "run1")
    conn.commit()
    letter_id = effect_persist_letter(
        conn, thread_id="app1", business_key="offer:run1",
        application_id="app1", kind="offer", version=1, template_version=1,
        draft=_draft(run_id="run1"), created_by="hr",
    )
    effect_mark_letter_human_written(
        conn, thread_id="app1", business_key=f"{letter_id}:mark-human",
        letter_id=letter_id, reviewer="alice", marked_at="2026-10-10 00:00:00",
    )
    row = conn.execute(
        "SELECT body, ai_generated, authorship_marked_by, authorship_from_version "
        "FROM candidate_letter WHERE id = ?",
        (letter_id,),
    ).fetchone()
    assert "【AI 生成】" not in row[0]
    assert row[1] == 0
    assert row[2] == "alice"
    assert row[3] == 1
