from __future__ import annotations

import sqlite3

import pytest

from app.agents.hard_requirement import SubjectiveRequirementError
from app.graph.screening_nodes import latest_approved_profile_version, load_active_rules
from app.storage.db import init_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    c.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    return c


def _insert_rule(conn, *, profile_version=1, field="experience_years", operator="gte",
                  value="3", blocking=1, human_readable="工作年限要求：3 年及以上"):
    conn.execute(
        "INSERT INTO hard_requirement "
        "(job_id, profile_version, field, operator, value, blocking, human_readable) "
        "VALUES ('j1', ?, ?, ?, ?, ?, ?)",
        (profile_version, field, operator, value, blocking, human_readable),
    )
    conn.commit()


def test_loads_rules_for_job_and_version(conn):
    _insert_rule(conn, profile_version=1)
    _insert_rule(conn, profile_version=1, field="core_skills", operator="contains", value="C 语言")
    rules = load_active_rules(conn, job_id="j1", profile_version=1)
    assert len(rules) == 2
    assert {r.field for r in rules} == {"experience_years", "core_skills"}


def test_loader_ignores_other_profile_versions(conn):
    _insert_rule(conn, profile_version=1)
    _insert_rule(conn, profile_version=2, field="core_skills", operator="contains", value="Python")
    rules = load_active_rules(conn, job_id="j1", profile_version=1)
    assert len(rules) == 1
    assert rules[0].field == "experience_years"


def test_loader_empty_ruleset_returns_empty_list(conn):
    assert load_active_rules(conn, job_id="j1", profile_version=99) == []


def test_loader_is_deterministically_ordered(conn):
    _insert_rule(conn, profile_version=1, field="experience_years", operator="gte", value="3")
    _insert_rule(conn, profile_version=1, field="core_skills", operator="contains", value="C 语言")
    _insert_rule(conn, profile_version=1, field="core_skills", operator="contains", value="Python")
    first = load_active_rules(conn, job_id="j1", profile_version=1)
    second = load_active_rules(conn, job_id="j1", profile_version=1)
    assert [(r.field, r.operator, r.value) for r in first] == [
        (r.field, r.operator, r.value) for r in second
    ]


def test_loader_rejects_blocking_subjective_rule(conn):
    _insert_rule(
        conn, profile_version=1, field="core_skills", operator="contains", value="沟通",
        blocking=1, human_readable="沟通能力强（不满足则不通过硬门槛）",
    )
    with pytest.raises(SubjectiveRequirementError):
        load_active_rules(conn, job_id="j1", profile_version=1)


def test_latest_approved_profile_version_none_when_no_approved(conn):
    assert latest_approved_profile_version(conn, "j1") is None


def test_latest_approved_profile_version_picks_max_approved(conn):
    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
        "VALUES ('p1', 'j1', 1, 'approved', '{}')"
    )
    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
        "VALUES ('p2', 'j1', 2, 'pending', '{}')"
    )
    conn.commit()
    assert latest_approved_profile_version(conn, "j1") == 1
