from __future__ import annotations

import io

import docx
import pytest

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account


def _docx_bytes(paragraphs: list[str]) -> bytes:
    document = docx.Document()
    for p in paragraphs:
        document.add_paragraph(p)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


@pytest.fixture
def logged_in_client(make_test_client):
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.commit()
    return client, conn


def test_missing_job_id_or_sample_class_rejects_whole_batch(logged_in_client):
    client, _conn = logged_in_client
    files = [("files", ("a.docx", _docx_bytes(["张三"]),
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]
    resp = client.post("/api/resumes/upload", data={"job_id": "j1"}, files=files)
    assert resp.status_code == 422


def test_unsupported_type_is_rejected_others_accepted(logged_in_client, monkeypatch):
    client, _conn = logged_in_client
    _stub_llm_success(monkeypatch)
    files = [
        ("files", ("a.docx", _docx_bytes(["张三"]),
                   "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
        ("files", ("b.xlsx", b"not really xlsx",
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
    ]
    resp = client.post(
        "/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "synthetic"},
        files=files,
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert results[0]["status"] == "accepted"
    assert results[1]["status"] == "rejected"
    assert "不支持的类型" in results[1]["reason"]


def test_live_sample_class_rejected_when_gate_closed(logged_in_client):
    client, _conn = logged_in_client
    files = [("files", ("a.docx", _docx_bytes(["张三"]),
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]
    resp = client.post(
        "/api/resumes/upload", data={"job_id": "j1", "sample_class": "live"}, files=files
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"][0]["status"] == "rejected"
    assert "入库闸未开启" in body["results"][0]["reason"]


def test_duplicate_content_hash_returns_existing_resume_id(logged_in_client, monkeypatch):
    client, _conn = logged_in_client
    _stub_llm_success(monkeypatch)
    content = _docx_bytes(["张三"])
    files = [("files", ("a.docx", content,
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]
    first = client.post(
        "/api/resumes/upload", data={"job_id": "j1", "sample_class": "synthetic"}, files=files
    ).json()["results"][0]
    second = client.post(
        "/api/resumes/upload", data={"job_id": "j1", "sample_class": "synthetic"}, files=files
    ).json()["results"][0]
    assert second["status"] == "duplicate"
    assert second["resume_id"] == first["resume_id"]


def _stub_llm_success(monkeypatch):
    import app.web.server as server_mod
    from app.llm.gateway import LLMCallMeta
    from app.schemas.resume_fields import (
        EducationField,
        ListField,
        NumberField,
        ResumeFields,
        TextField,
    )

    def _fake_compute_parse(_gateway, *, spans, prompt_version="parse-v1", audit_context=None):
        fields = ResumeFields(
            name=TextField(value="张三", confidence=0.95,
                            spans=[{"span_id": 1, "quote": "张三", "start": 0, "end": 2}]),
            years_of_experience=NumberField(not_mentioned=True, value=None, confidence=1.0),
            skills=ListField(not_mentioned=True, value=[], confidence=1.0),
            companies=ListField(not_mentioned=True, value=[], confidence=1.0),
            education=EducationField(not_mentioned=True, value=None, confidence=1.0),
            expected_city=TextField(not_mentioned=True, value=None, confidence=1.0),
        )
        return fields, LLMCallMeta(latency_ms=1.0, response_model="deepseek-chat", attempts=1, run_id="run-id-stub")

    monkeypatch.setattr(server_mod, "compute_parse", _fake_compute_parse)
