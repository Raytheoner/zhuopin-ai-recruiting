from __future__ import annotations

import sqlite3

import pytest

from app.storage.db import _existing_columns, init_schema
from app.storage.source import candidate_source


def _conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    return c


def test_fresh_resume_has_source_columns():
    conn = _conn()
    cols = _existing_columns(conn, "resume")
    assert {"source", "source_origin"} <= cols


def test_source_origin_check_rejects_invalid():
    conn = _conn()
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', 't')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'h', 'alice')"
    )
    conn.execute("UPDATE resume SET source_origin = 'detected' WHERE id = 'r1'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE resume SET source_origin = 'bogus' WHERE id = 'r1'")


def test_source_correction_log_and_bundle_ingest_result_tables_exist():
    conn = _conn()
    assert _existing_columns(conn, "source_correction_log") >= {
        "id", "resume_id", "from_source", "to_source", "corrected_by", "at",
    }
    assert _existing_columns(conn, "bundle_ingest_result") >= {
        "id", "job_id", "bundle_sha256", "status", "result_json", "created_at",
    }


def test_candidate_source_returns_earliest_resume_source():
    conn = _conn()
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', 't')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute("INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('initial', '初筛', 'initial')")
    for rid, source, at in [("r-old", "boss", "2026-01-01 00:00:00"),
                            ("r-new", "liepin", "2026-01-02 00:00:00")]:
        conn.execute(
            "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
            "uploaded_by, source, uploaded_at) VALUES (?, 'j1', 'synthetic', ?, ?, 'alice', ?, ?)",
            (rid, rid + ".pdf", "h-" + rid, source, at),
        )
        conn.execute(
            "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
            "VALUES (?, 'c1', 'j1', ?, 'initial')",
            ("app-" + rid, rid),
        )
    assert candidate_source(conn, "c1") == "boss"


def test_candidate_source_treats_null_and_missing_as_unknown():
    conn = _conn()
    assert candidate_source(conn, "missing") == "unknown"
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', 't')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute("INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('initial', '初筛', 'initial')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'h', 'alice')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('a1', 'c1', 'j1', 'r1', 'initial')"
    )
    assert candidate_source(conn, "c1") == "unknown"
