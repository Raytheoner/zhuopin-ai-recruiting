from __future__ import annotations

import sqlite3

from app.intake.merge import effect_attach_resume_to_candidate
from app.intake.phone_hash import hash_phone
from app.storage.db import init_schema
from app.storage.source import candidate_source


def _conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    c.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    return c


def _resume(conn, rid):
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES (?, 'j1', 'synthetic', ?, ?, 'alice')",
        (rid, rid + ".pdf", "h-" + rid),
    )


def _attach(conn, *, resume_id, name, phone_hash=None, business_key="once"):
    return effect_attach_resume_to_candidate(
        conn,
        thread_id=resume_id,
        business_key=business_key,
        resume_id=resume_id,
        job_id="j1",
        name=name,
        phone_hash=phone_hash,
    )


def test_phone_hash_hit_attaches_to_existing_candidate():
    conn = _conn()
    conn.execute("INSERT INTO candidate (id, name, phone_hash) VALUES ('c1', '张三', ?)",
                 (hash_phone("13800138000"),))
    _resume(conn, "r1")
    result = _attach(conn, resume_id="r1", name="张三", phone_hash=hash_phone("13800138000"))
    assert result["candidate_id"] == "c1"
    assert result["candidate_created"] is False


def test_no_phone_creates_new_candidate():
    conn = _conn()
    _resume(conn, "r1")
    result = _attach(conn, resume_id="r1", name="张三", phone_hash=None)
    assert result["candidate_created"] is True
    row = conn.execute("SELECT name, phone_hash FROM candidate WHERE id = ?",
                       (result["candidate_id"],)).fetchone()
    assert row == ("张三", None)


def test_same_phone_different_sources_attach_same_candidate():
    conn = _conn()
    _resume(conn, "r-old")
    _resume(conn, "r-new")
    _attach(conn, resume_id="r-old", name="张三", phone_hash=hash_phone("13800138000"))
    conn.execute("UPDATE resume SET source = 'boss', uploaded_at = '2026-01-01 00:00:00' WHERE id = 'r-old'")
    result = _attach(conn, resume_id="r-new", name="张三", phone_hash=hash_phone("13800138000"))
    assert result["candidate_created"] is False
    conn.execute("UPDATE resume SET source = 'liepin', uploaded_at = '2026-01-02 00:00:00' WHERE id = 'r-new'")
    assert candidate_source(conn, result["candidate_id"]) == "boss"


def test_replay_is_short_circuited():
    conn = _conn()
    _resume(conn, "r1")
    first = _attach(conn, resume_id="r1", name="张三", phone_hash=None)
    second = _attach(conn, resume_id="r1", name="张三", phone_hash=None)
    assert first is not None and second is None
    assert conn.execute("SELECT COUNT(*) FROM candidate").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM application").fetchone()[0] == 1
