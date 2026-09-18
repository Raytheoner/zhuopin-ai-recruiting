from __future__ import annotations

import json
import sqlite3

import pytest

from app.graph.resume_nodes import effect_persist_parse
from app.schemas.resume_fields import (
    EducationField,
    ListField,
    NumberField,
    ResumeFields,
    TextField,
)
from app.storage.db import init_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    c.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    c.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'hash1', 'alice')"
    )
    c.commit()
    return c


def _fields(*, name_confidence: float = 0.9, years_confidence: float = 0.9) -> ResumeFields:
    return ResumeFields(
        name=TextField(value="张三", confidence=name_confidence),
        years_of_experience=NumberField(value=5, confidence=years_confidence),
        skills=ListField(not_mentioned=True, value=[], confidence=1.0),
        companies=ListField(not_mentioned=True, value=[], confidence=1.0),
        education=EducationField(not_mentioned=True, value=None, confidence=1.0),
        expected_city=TextField(not_mentioned=True, value=None, confidence=1.0),
    )


def _persist(conn, **overrides):
    kwargs = dict(
        thread_id="r1",
        business_key="v1",
        resume_id="r1",
        job_id="j1",
        fields=_fields(),
        parser_version="v1",
        model_configured="deepseek-chat",
        model_response="deepseek-chat",
        prompt_version="parse-v1",
        confidence_threshold=0.7,
    )
    kwargs.update(overrides)
    return effect_persist_parse(conn, **kwargs)


def test_first_parse_creates_candidate_and_application(conn):
    application_id = _persist(conn)
    assert application_id is not None
    app_row = conn.execute(
        "SELECT candidate_id, job_id, resume_id, current_stage_id FROM application WHERE id = ?",
        (application_id,),
    ).fetchone()
    assert app_row == (app_row[0], "j1", "r1", "initial")
    candidate_row = conn.execute(
        "SELECT name FROM candidate WHERE id = ?", (app_row[0],)
    ).fetchone()
    assert candidate_row[0] == "张三"


def test_first_parse_writes_stage_history_and_resume_columns(conn):
    application_id = _persist(conn)
    history = conn.execute(
        "SELECT from_stage_id, to_stage_id, actor_type FROM application_stage_history "
        "WHERE application_id = ?",
        (application_id,),
    ).fetchone()
    assert history == (None, "initial", "agent")
    resume_row = conn.execute(
        "SELECT status, parser_version, parse_confidence FROM resume WHERE id = 'r1'"
    ).fetchone()
    assert resume_row[0] == "parsed"
    assert resume_row[1] == "v1"


def test_low_confidence_field_enters_review_queue(conn):
    _persist(conn, fields=_fields(name_confidence=0.3))
    row = conn.execute(
        "SELECT status FROM field_review_queue WHERE resume_id = 'r1' AND field = 'name'"
    ).fetchone()
    assert row is not None
    assert row[0] == "pending"


def test_high_confidence_field_does_not_enter_review_queue(conn):
    _persist(conn, fields=_fields(name_confidence=0.9))
    row = conn.execute(
        "SELECT 1 FROM field_review_queue WHERE resume_id = 'r1' AND field = 'name'"
    ).fetchone()
    assert row is None


def test_rerun_same_effect_key_does_not_duplicate(conn):
    first = _persist(conn)
    second = _persist(conn)
    assert second is None
    count = conn.execute("SELECT COUNT(*) FROM application").fetchone()[0]
    assert count == 1
    history_count = conn.execute(
        "SELECT COUNT(*) FROM application_stage_history"
    ).fetchone()[0]
    assert history_count == 1


def test_reparse_new_version_updates_resume_and_keeps_old_version_row(conn):
    _persist(conn, parser_version="v1", business_key="v1")
    application_id = _persist(
        conn,
        parser_version="v2",
        business_key="v2",
        fields=_fields(name_confidence=0.95),
    )
    versions = conn.execute(
        "SELECT parser_version FROM resume_parse_version WHERE resume_id = 'r1' "
        "ORDER BY parser_version"
    ).fetchall()
    assert [v[0] for v in versions] == ["v1", "v2"]
    resume_row = conn.execute(
        "SELECT parser_version FROM resume WHERE id = 'r1'"
    ).fetchone()
    assert resume_row[0] == "v2"
    application_count = conn.execute("SELECT COUNT(*) FROM application").fetchone()[0]
    assert application_count == 1  # 重解析不产生第二个 application
    assert application_id is not None
