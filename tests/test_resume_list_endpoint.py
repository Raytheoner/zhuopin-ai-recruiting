from __future__ import annotations

import pytest

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account
from tests.test_resume_upload import _docx_bytes, _stub_llm_success


@pytest.fixture
def logged_in_client_with_job(make_test_client):
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '嵌入式工程师', 'approved')")
    conn.commit()
    return client, conn


def _upload_one(client, filler_paragraphs):
    files = [("files", ("a.docx", _docx_bytes(filler_paragraphs),
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]
    resp = client.post(
        "/api/resumes/upload", data={"job_id": "j1", "sample_class": "synthetic"}, files=files
    )
    return resp.json()["results"][0]


def test_list_resumes_for_job_returns_parsed_summary(logged_in_client_with_job, monkeypatch):
    client, _conn = logged_in_client_with_job
    _stub_llm_success(monkeypatch)
    filler = "这是一段用于测试的简历正文占位文字，确保解析有效字符数超过五十个字符阈值，多写几句话把有效字符数字凑够。"
    result = _upload_one(client, ["张三", filler])
    resume_id = result["resume_id"]

    resp = client.get("/api/resumes/by-job/j1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == "j1"
    assert len(body["resumes"]) == 1
    item = body["resumes"][0]
    assert item["resume_id"] == resume_id
    assert item["candidate_display_name"] == "张三"
    assert item["sample_class"] == "synthetic"
    assert item["parse_status"] == "parsed"
    assert item["parser_version"] == "v1"
    assert "score" not in item
    assert "rank" not in item

    field_by_name = {f["field"]: f for f in item["fields"]}
    assert field_by_name["name"]["value_display"] == "张三"
    assert field_by_name["name"]["review_status"] == "not_queued"
    assert field_by_name["years_of_experience"]["value_display"] == "未提及"
    assert field_by_name["years_of_experience"]["review_status"] == "not_queued"


def test_list_resumes_for_job_records_access_log_once_per_resume(logged_in_client_with_job, monkeypatch):
    client, conn = logged_in_client_with_job
    _stub_llm_success(monkeypatch)
    filler = "这是一段用于测试的简历正文占位文字，确保解析有效字符数超过五十个字符阈值，多写几句话把有效字符数字凑够。"
    _upload_one(client, ["张三", filler])

    client.get("/api/resumes/by-job/j1")

    count = conn.execute(
        "SELECT COUNT(*) FROM resume_access_log WHERE access_type = 'parsed_result'"
    ).fetchone()[0]
    assert count == 1


def test_list_resumes_for_job_shows_pending_then_reviewed_status(make_test_client, monkeypatch):
    from app.llm.gateway import LLMCallMeta
    from app.schemas.resume_fields import (
        EducationField, ListField, NumberField, ResumeFields, TextField,
    )
    import app.web.server as server_mod

    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '嵌入式工程师', 'approved')")
    conn.commit()

    def _fake_compute_parse(_gateway, *, spans, prompt_version="parse-v1", audit_context=None):
        fields = ResumeFields(
            name=TextField(value="张三", confidence=0.2,
                            spans=[{"span_id": 1, "quote": "张三", "start": 0, "end": 2}]),
            years_of_experience=NumberField(not_mentioned=True, value=None, confidence=1.0),
            skills=ListField(not_mentioned=True, value=[], confidence=1.0),
            companies=ListField(not_mentioned=True, value=[], confidence=1.0),
            education=EducationField(not_mentioned=True, value=None, confidence=1.0),
            expected_city=TextField(not_mentioned=True, value=None, confidence=1.0),
        )
        return fields, LLMCallMeta(latency_ms=1.0, response_model="deepseek-chat", attempts=1)

    monkeypatch.setattr(server_mod, "compute_parse", _fake_compute_parse)

    filler = "这是一段用于测试的简历正文占位文字，确保解析有效字符数超过五十个字符阈值，多写几句话把有效字符数字凑够。"
    result = _upload_one(client, ["张三", filler])
    resume_id = result["resume_id"]

    resp = client.get("/api/resumes/by-job/j1")
    field_by_name = {f["field"]: f for f in resp.json()["resumes"][0]["fields"]}
    assert field_by_name["name"]["review_status"] == "pending"
    assert resp.json()["resumes"][0]["pending_review_count"] == 1

    client.post(f"/api/resumes/{resume_id}/fields/name/review", json={"human_value": "张三三"})

    resp2 = client.get("/api/resumes/by-job/j1")
    item2 = resp2.json()["resumes"][0]
    field_by_name2 = {f["field"]: f for f in item2["fields"]}
    assert field_by_name2["name"]["review_status"] == "reviewed"
    assert field_by_name2["name"]["reviewed_by"] == "alice"
    assert field_by_name2["name"]["value_display"] == "张三"  # 摘要仍取 parsed_json 的机器值，不是校对值
    assert item2["pending_review_count"] == 0


def test_list_resumes_requires_login(make_test_client):
    client, conn = make_test_client()
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '嵌入式工程师', 'approved')")
    conn.commit()
    resp = client.get("/api/resumes/by-job/j1")
    assert resp.status_code == 401


def test_list_resumes_unknown_job_404(logged_in_client_with_job):
    client, _conn = logged_in_client_with_job
    resp = client.get("/api/resumes/by-job/does-not-exist")
    assert resp.status_code == 404
