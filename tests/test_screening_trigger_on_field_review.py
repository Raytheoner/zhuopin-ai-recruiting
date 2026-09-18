from __future__ import annotations

import sqlite3

import pytest

from app.graph.resume_nodes import queue_reapplication_screening
from app.storage.db import init_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    c.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    c.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    c.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
        "VALUES ('p1', 'j1', 1, 'approved', '{}')"
    )
    c.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
        "uploaded_by, status, parsed_json, parser_version) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'hash1', 'alice', 'parsed', ?, 'v1')",
        (_fields_json(),),
    )
    c.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('a1', 'c1', 'j1', 'r1', 'initial')"
    )
    c.execute(
        "INSERT INTO hard_requirement "
        "(job_id, profile_version, field, operator, value, blocking, human_readable) "
        "VALUES ('j1', 1, 'experience_years', 'gte', '5', 1, '工作年限要求：5 年及以上')"
    )
    c.commit()
    return c


def _fields_json() -> str:
    from app.schemas.resume_fields import (
        EducationField, ListField, NumberField, ResumeFields, SpanRef, TextField,
    )
    fields = ResumeFields(
        name=TextField(value="张三", confidence=0.9, spans=[SpanRef(span_id=1, quote="张三", start=0, end=2)]),
        years_of_experience=NumberField(
            value=2.0, confidence=0.9, spans=[SpanRef(span_id=2, quote="2年", start=5, end=7)]
        ),
        skills=ListField(not_mentioned=True, value=[], confidence=1.0),
        companies=ListField(not_mentioned=True, value=[], confidence=1.0),
        education=EducationField(not_mentioned=True, value=None, confidence=1.0),
        expected_city=TextField(not_mentioned=True, value=None, confidence=1.0),
    )
    return fields.model_dump_json()


def test_queue_reapplication_screening_persists_flags(conn):
    queue_reapplication_screening(conn, "r1")
    row = conn.execute(
        "SELECT verdict FROM screening_flag WHERE application_id = 'a1'"
    ).fetchone()
    assert row is not None
    assert row[0] == "fail"


def test_queue_reapplication_screening_no_op_when_no_approved_profile(conn):
    conn.execute("UPDATE job_profile SET status = 'pending' WHERE id = 'p1'")
    conn.commit()
    queue_reapplication_screening(conn, "r1")
    count = conn.execute(
        "SELECT COUNT(*) FROM screening_flag WHERE application_id = 'a1'"
    ).fetchone()[0]
    assert count == 0


def test_queue_reapplication_screening_no_op_when_resume_missing(conn):
    queue_reapplication_screening(conn, "no-such-resume")  # 不应抛异常
    count = conn.execute("SELECT COUNT(*) FROM screening_flag").fetchone()[0]
    assert count == 0
