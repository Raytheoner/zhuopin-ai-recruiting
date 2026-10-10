"""U3 tasks 3.4 三条新断言的**正向**行为：干净库通过、缺表/缺列时 fail-closed。

⚠️ 与既有纪律一致：恒真的断言在这里也会全绿，反证在
tests/test_audit_assertion_effectiveness.py（造违例 → 必须失败），两个文件必须
成对存在。
"""
from __future__ import annotations

import sqlite3

import pytest

from app.audit.assertions import (
    assert_merge_log_rows_have_actor,
    assert_merged_candidates_have_no_active_application,
    assert_no_plaintext_phone_column,
)
from app.storage.db import get_connection, init_schema

pytestmark = pytest.mark.compliance


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "channel_audit.db"))
    init_schema(c)
    return c


def test_all_three_pass_on_a_clean_db(conn):
    for assertion in (
        assert_no_plaintext_phone_column,
        assert_merge_log_rows_have_actor,
        assert_merged_candidates_have_no_active_application,
    ):
        result = assertion(conn)
        assert result.ok is True, (result.name, result.violations)
        assert result.violations == ()


def test_plaintext_phone_assertion_fails_closed_without_candidate_table():
    bare = sqlite3.connect(":memory:")
    result = assert_no_plaintext_phone_column(bare)
    assert result.ok is False
    assert result.violations == ({"table": "candidate", "issue": "table_missing"},)


def test_plaintext_phone_assertion_fails_closed_without_resume_table():
    bare = sqlite3.connect(":memory:")
    bare.execute("CREATE TABLE candidate (id TEXT PRIMARY KEY, name TEXT, phone_hash TEXT)")
    result = assert_no_plaintext_phone_column(bare)
    assert result.ok is False
    assert result.violations == ({"table": "resume", "issue": "table_missing"},)


def test_merge_log_assertion_fails_closed_when_table_is_absent():
    bare = sqlite3.connect(":memory:")
    result = assert_merge_log_rows_have_actor(bare)
    assert result.ok is False
    assert result.violations == ({"table": "candidate_merge_log", "issue": "table_missing"},)


def test_merged_candidate_assertion_fails_closed_without_merged_into(conn):
    conn.execute("ALTER TABLE candidate RENAME TO candidate_old")
    conn.execute("CREATE TABLE candidate (id TEXT PRIMARY KEY, name TEXT, phone_hash TEXT)")
    conn.commit()
    result = assert_merged_candidates_have_no_active_application(conn)
    assert result.ok is False
    assert result.violations == ({"table": "candidate", "missing_column": "merged_into"},)
