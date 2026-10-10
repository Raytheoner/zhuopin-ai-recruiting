"""U3 tasks 3.1：投递创建的初始流转事实携带来源。

两条路径都验：① 直接调 effect_attach_resume_to_candidate（单元级）；
② 走 POST /api/resumes/upload 的 ZIP 分支（端到端，来源由 U1 的确定性规则识别）。
"""
from __future__ import annotations

import io
import sqlite3
import zipfile

import docx

from app.intake.merge import effect_attach_resume_to_candidate
from app.storage.auth_session import create_session
from app.storage.db import init_schema
from app.storage.hr_account import upsert_account


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    init_schema(c)
    c.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    return c


def _resume(conn: sqlite3.Connection, rid: str, source: str | None = None) -> None:
    conn.execute(
        "INSERT INTO resume "
        "(id, job_id, sample_class, file_name, content_sha256, uploaded_by, source) "
        "VALUES (?, 'j1', 'synthetic', ?, ?, 'alice', ?)",
        (rid, rid + ".pdf", "h-" + rid, source),
    )


def _attach(conn: sqlite3.Connection, *, resume_id: str, name: str = "张三"):
    return effect_attach_resume_to_candidate(
        conn,
        thread_id=resume_id,
        business_key="once",
        resume_id=resume_id,
        job_id="j1",
        name=name,
        phone_hash=None,
    )


def _initial_history(conn: sqlite3.Connection, application_id: str) -> tuple:
    return conn.execute(
        "SELECT from_stage_id, to_stage_id, actor_type, action, source "
        "FROM application_stage_history WHERE application_id = ?",
        (application_id,),
    ).fetchone()


def test_initial_history_carries_resume_source():
    conn = _conn()
    _resume(conn, "r1", source="boss")
    result = _attach(conn, resume_id="r1")
    assert _initial_history(conn, result["application_id"]) == (
        None, "initial", "agent", None, "boss",
    )


def test_initial_history_source_is_null_when_resume_has_none():
    conn = _conn()
    _resume(conn, "r1", source=None)
    result = _attach(conn, resume_id="r1")
    assert _initial_history(conn, result["application_id"])[4] is None


def test_replay_does_not_add_a_second_initial_history_row():
    """幂等由 effect_attach_resume_to_candidate 承担：重跑不产生第二条初始事实。"""
    conn = _conn()
    _resume(conn, "r1", source="liepin")
    first = _attach(conn, resume_id="r1")
    second = _attach(conn, resume_id="r1")
    assert first is not None and second is None
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history"
    ).fetchone()[0] == 1


def _docx_bytes(paragraphs: list[str]) -> bytes:
    document = docx.Document()
    for p in paragraphs:
        document.add_paragraph(p)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _fake_compute_parse(gateway, *, spans, audit_context):
    from app.schemas.resume_fields import (
        EducationField, ListField, NumberField, ResumeFields, SpanRef, TextField,
    )

    fields = ResumeFields(
        name=TextField(
            value="张三", confidence=0.9,
            spans=[SpanRef(span_id=1, quote="张三", start=0, end=2)],
        ),
        years_of_experience=NumberField(
            value=3.0, confidence=0.9,
            spans=[SpanRef(span_id=2, quote="3年", start=3, end=5)],
        ),
        skills=ListField(not_mentioned=True, value=[], confidence=1.0),
        companies=ListField(not_mentioned=True, value=[], confidence=1.0),
        education=EducationField(not_mentioned=True, value=None, confidence=1.0),
        expected_city=TextField(not_mentioned=True, value=None, confidence=1.0),
    )

    class _Meta:
        response_model = "test-model"

    return fields, _Meta()


def test_upload_zip_initial_history_carries_detected_source(make_test_client, monkeypatch):
    """端到端：ZIP 里文件名可识别为 boss ⇒ 该份简历的投递初始事实 source='boss'。
    ⛔ 来源识别本身（U1）不在这里复验，只验它一路传到了流转事实表。"""
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.commit()
    monkeypatch.setattr("app.web.server.compute_parse", _fake_compute_parse)

    zdata = _zip({
        "候选人-张三-boss直聘.docx": _docx_bytes(["张三，3年工作经验。"] * 6),
    })
    resp = client.post(
        "/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "synthetic"},
        files=[("files", ("bundle.zip", zdata, "application/zip"))],
    )
    results = resp.json()["results"]
    assert results[0]["status"] == "accepted"
    assert results[0]["source"] == "boss"

    row = conn.execute(
        "SELECT h.source FROM application_stage_history h "
        "JOIN application a ON a.id = h.application_id "
        "WHERE a.resume_id = ?",
        (results[0]["resume_id"],),
    ).fetchone()
    assert row == ("boss",)
