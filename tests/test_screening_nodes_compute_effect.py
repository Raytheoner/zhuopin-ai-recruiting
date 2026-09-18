from __future__ import annotations

import json
import sqlite3

import pytest

from app.graph.screening_nodes import compute_screen, effect_persist_flags, screen_and_persist
from app.schemas.resume_fields import (
    EducationField,
    EducationValue,
    ListField,
    NumberField,
    ResumeFields,
    SpanRef,
    TextField,
)
from app.storage.db import init_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    c.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    c.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
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
    c.commit()
    return c


def _fields_json() -> str:
    fields = ResumeFields(
        name=TextField(value="张三", confidence=0.95, spans=[SpanRef(span_id=1, quote="张三", start=0, end=2)]),
        years_of_experience=NumberField(
            value=2.0, confidence=0.9, spans=[SpanRef(span_id=2, quote="2年经验", start=10, end=15)]
        ),
        skills=ListField(not_mentioned=True, value=[], confidence=1.0),
        companies=ListField(not_mentioned=True, value=[], confidence=1.0),
        education=EducationField(
            value=EducationValue(degree="本科", school="某大学"), confidence=0.9,
            spans=[SpanRef(span_id=3, quote="本科", start=20, end=22)],
        ),
        expected_city=TextField(not_mentioned=True, value=None, confidence=1.0),
    )
    return fields.model_dump_json()


def _insert_rule(conn, *, profile_version, field, operator, value, blocking=1):
    conn.execute(
        "INSERT INTO hard_requirement "
        "(job_id, profile_version, field, operator, value, blocking, human_readable) "
        "VALUES ('j1', ?, ?, ?, ?, ?, ?)",
        (profile_version, field, operator, value, blocking, f"{field} {operator} {value}"),
    )
    conn.commit()


def test_compute_screen_returns_verdicts(conn):
    _insert_rule(conn, profile_version=1, field="experience_years", operator="gte", value="5")
    verdicts = compute_screen(conn, resume_id="r1", job_id="j1", profile_version=1)
    assert len(verdicts) == 1
    assert verdicts[0].verdict == "fail"


def test_compute_screen_respects_pending_review_queue(conn):
    _insert_rule(conn, profile_version=1, field="experience_years", operator="gte", value="5")
    conn.execute(
        "INSERT INTO field_review_queue (id, resume_id, field, status) "
        "VALUES ('q1', 'r1', 'years_of_experience', 'pending')"
    )
    conn.commit()
    verdicts = compute_screen(conn, resume_id="r1", job_id="j1", profile_version=1)
    assert verdicts[0].verdict == "skipped"
    assert verdicts[0].reason == "待校对"


def test_effect_persist_flags_writes_rows_and_effect_log(conn):
    _insert_rule(conn, profile_version=1, field="experience_years", operator="gte", value="5")
    verdicts = compute_screen(conn, resume_id="r1", job_id="j1", profile_version=1)
    written = effect_persist_flags(
        conn, thread_id="a1", business_key="1:v1",
        application_id="a1", profile_version=1, verdicts=verdicts,
    )
    assert written == 1
    flag_count = conn.execute(
        "SELECT COUNT(*) FROM screening_flag WHERE application_id = 'a1'"
    ).fetchone()[0]
    effect_log_count = conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE effect_key = 'a1:effect_persist_flags:1:v1'"
    ).fetchone()[0]
    assert flag_count == 1 == effect_log_count


def test_effect_persist_flags_writes_evidence_ref_for_fail(conn):
    _insert_rule(conn, profile_version=1, field="experience_years", operator="gte", value="5")
    verdicts = compute_screen(conn, resume_id="r1", job_id="j1", profile_version=1)
    effect_persist_flags(
        conn, thread_id="a1", business_key="1:v1",
        application_id="a1", profile_version=1, verdicts=verdicts,
    )
    row = conn.execute(
        "SELECT verdict, evidence_ref FROM screening_flag WHERE application_id = 'a1'"
    ).fetchone()
    assert row[0] == "fail"
    assert row[1] is not None
    assert json.loads(row[1]) == {"span_id": 2, "start": 10, "end": 15}


def test_screen_and_persist_is_idempotent(conn):
    _insert_rule(conn, profile_version=1, field="experience_years", operator="gte", value="5")
    first = screen_and_persist(
        conn, application_id="a1", resume_id="r1", job_id="j1",
        profile_version=1, parse_version="v1",
    )
    second = screen_and_persist(
        conn, application_id="a1", resume_id="r1", job_id="j1",
        profile_version=1, parse_version="v1",
    )
    assert first == 1
    assert second is None
    flag_count = conn.execute(
        "SELECT COUNT(*) FROM screening_flag WHERE application_id = 'a1'"
    ).fetchone()[0]
    assert flag_count == 1


def test_screen_and_persist_profile_upgrade_keeps_old_flags_and_adds_new(conn):
    """spec Scenario「画像升版后重判」+ tasks 4.3「三种触发点」的第三种
    （架构决策 9）：同一 application 用两个不同 profile_version 各判一次，
    旧组保留、新组新增，互不覆盖。"""
    _insert_rule(conn, profile_version=1, field="experience_years", operator="gte", value="1")
    _insert_rule(conn, profile_version=2, field="experience_years", operator="gte", value="10")

    screen_and_persist(
        conn, application_id="a1", resume_id="r1", job_id="j1",
        profile_version=1, parse_version="v1",
    )
    screen_and_persist(
        conn, application_id="a1", resume_id="r1", job_id="j1",
        profile_version=2, parse_version="v1",
    )

    rows = conn.execute(
        "SELECT profile_version, verdict FROM screening_flag "
        "WHERE application_id = 'a1' ORDER BY profile_version"
    ).fetchall()
    assert rows == [(1, "pass"), (2, "fail")]


def test_screen_and_persist_reparse_creates_new_set_via_new_parse_version(conn):
    """第二种触发点：字段校对完成后用新的 parser_version 重判——旧
    parse_version 的 flags 也应保留（同一 profile_version 下按 parse_version
    再区分一组）。"""
    _insert_rule(conn, profile_version=1, field="experience_years", operator="gte", value="1")
    screen_and_persist(
        conn, application_id="a1", resume_id="r1", job_id="j1",
        profile_version=1, parse_version="v1",
    )
    screen_and_persist(
        conn, application_id="a1", resume_id="r1", job_id="j1",
        profile_version=1, parse_version="v2",
    )
    count = conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE thread_id = 'a1' "
        "AND node_name = 'effect_persist_flags'"
    ).fetchone()[0]
    assert count == 2
