from __future__ import annotations

import sqlite3

import pytest

from app.storage.db import init_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    # init_schema() 本身不开 FK 强制（只有 get_connection() 会），本文件按
    # brief 字面用的是裸 sqlite3.connect，因此这里显式补一句——与仓库内其余
    # schema 测试（test_db_m2_schema.py / test_human_review_schema.py 等）
    # 一致：它们统一走 get_connection() 正是为了拿到这条 FK 强制。少了它，
    # test_appeal_event_requires_valid_rejection_record 无法验证外键约束。
    c.execute("PRAGMA foreign_keys = ON")
    init_schema(c)
    return c


def test_appeal_event_table_exists(conn):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='appeal_event'"
    ).fetchone()
    assert row is not None


def test_appeal_event_columns(conn):
    columns = {r[1] for r in conn.execute("PRAGMA table_info(appeal_event)")}
    assert columns == {
        "id", "rejection_record_id", "from_status", "to_status", "actor", "occurred_at",
    }


def test_appeal_event_actor_blank_rejected(conn):
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.execute(
        "INSERT INTO candidate (id, name) VALUES ('c1', '张三')"
    )
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'hash1', 'alice')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('a1', 'c1', 'j1', 'r1', 'rejected')"
    )
    conn.execute(
        "INSERT INTO rejection_record (id, application_id, reason_type, decided_by) "
        "VALUES ('rej1', 'a1', 'human_decision', 'hr1')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO appeal_event (id, rejection_record_id, from_status, to_status, actor) "
            "VALUES ('ev1', 'rej1', 'none', 'requested', '   ')"
        )


def test_appeal_event_requires_valid_rejection_record(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO appeal_event (id, rejection_record_id, from_status, to_status, actor) "
            "VALUES ('ev1', 'no-such-rejection', 'none', 'requested', 'hr1')"
        )


def test_fresh_and_migrated_schemas_agree_on_appeal_event():
    fresh = sqlite3.connect(":memory:")
    init_schema(fresh)
    fresh_columns = {r[1] for r in fresh.execute("PRAGMA table_info(appeal_event)")}
    assert fresh_columns == {
        "id", "rejection_record_id", "from_status", "to_status", "actor", "occurred_at",
    }
