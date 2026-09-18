from __future__ import annotations

import sqlite3

from app.storage.db import _existing_columns, apply_column_migrations, init_schema


def test_fresh_db_has_resume_parse_version_table():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    cols = _existing_columns(conn, "resume_parse_version")
    assert cols == {
        "resume_id",
        "parser_version",
        "parsed_json",
        "confidence",
        "model_configured",
        "model_response",
        "prompt_version",
        "parsed_at",
    }


def test_resume_table_has_raw_text_column():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    assert "raw_text" in _existing_columns(conn, "resume")


def test_old_job_table_gains_parse_confidence_threshold_via_migration():
    """模拟老库：先建一份没有新列的 job 表，再跑迁移，必须补上且默认 0.7。"""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE job (id TEXT PRIMARY KEY, title TEXT NOT NULL, "
        "department TEXT, status TEXT NOT NULL DEFAULT 'drafting', "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    # apply_column_migrations 逐条遍历 _ADDED_COLUMNS，job_profile 的历史条目排
    # 在 job 的新条目之前；这张表必须存在（即便只是空壳），否则会在遍历到
    # job_profile 时先炸 "no such table"，与本测试要验证的 job 迁移无关。
    conn.execute("CREATE TABLE job_profile (id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', 't')")
    added = apply_column_migrations(conn)
    assert "parse_confidence_threshold" in added
    value = conn.execute(
        "SELECT parse_confidence_threshold FROM job WHERE id = 'j1'"
    ).fetchone()[0]
    assert value == 0.7


def test_resume_parse_version_unique_per_resume_and_version():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', 't')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'hash1', 'alice')"
    )
    conn.execute(
        "INSERT INTO resume_parse_version "
        "(resume_id, parser_version, parsed_json, confidence, model_configured, "
        "model_response, prompt_version) VALUES ('r1', 'v1', '{}', 0.9, 'deepseek-chat', "
        "'deepseek-chat', 'parse-v1')"
    )
    conn.commit()
    with_conflict = sqlite3.IntegrityError
    try:
        conn.execute(
            "INSERT INTO resume_parse_version "
            "(resume_id, parser_version, parsed_json, confidence, model_configured, "
            "model_response, prompt_version) VALUES ('r1', 'v1', '{}', 0.1, 'x', 'x', 'x')"
        )
        raised = False
    except with_conflict:
        raised = True
    assert raised
