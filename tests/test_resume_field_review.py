from __future__ import annotations

import pytest

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account
from tests.test_resume_upload import _docx_bytes, _stub_llm_success


@pytest.fixture
def low_confidence_resume(make_test_client, monkeypatch):
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.commit()

    import app.web.server as server_mod
    from app.llm.gateway import LLMCallMeta
    from app.schemas.resume_fields import (
        EducationField, ListField, NumberField, ResumeFields, TextField,
    )

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

    # ⚠️ 同 Task 4/8/9 踩过的坑：仅 "张三" 两个有效字符低于
    # app/parsing/extract_text.py::MIN_EFFECTIVE_CHARS=50，会被判成
    # unreadable，导致 field_review_queue 不会有 pending 行——本用例要验证的是
    # 字段校对接口本身，不是解析逻辑，所以补一段占位正文把有效字符数撑过阈值，
    # 不改路由代码。
    filler = (
        "这是一段用于测试的简历正文占位文字，确保解析有效字符数超过五十个字符阈值，"
        "多写几句话把有效字符数字凑够。"
    )
    files = [("files", ("a.docx", _docx_bytes(["张三", filler]),
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]
    resp = client.post(
        "/api/resumes/upload", data={"job_id": "j1", "sample_class": "synthetic"}, files=files
    )
    resume_id = resp.json()["results"][0]["resume_id"]
    return client, conn, resume_id


def test_review_updates_queue_and_records_reviewer(low_confidence_resume):
    client, conn, resume_id = low_confidence_resume
    resp = client.post(
        f"/api/resumes/{resume_id}/fields/name/review",
        json={"human_value": "张三三"},
    )
    assert resp.status_code == 200
    row = conn.execute(
        "SELECT status, human_value, reviewed_by FROM field_review_queue "
        "WHERE resume_id = ? AND field = 'name'",
        (resume_id,),
    ).fetchone()
    assert row == ("reviewed", "张三三", "alice")


def test_review_is_idempotent_for_same_value(low_confidence_resume):
    client, conn, resume_id = low_confidence_resume
    client.post(f"/api/resumes/{resume_id}/fields/name/review", json={"human_value": "张三三"})
    resp = client.post(
        f"/api/resumes/{resume_id}/fields/name/review", json={"human_value": "张三三"}
    )
    assert resp.status_code == 200
    count = conn.execute(
        "SELECT COUNT(*) FROM field_review_queue WHERE resume_id = ? AND field = 'name'",
        (resume_id,),
    ).fetchone()[0]
    assert count == 1


def test_review_unknown_field_404(low_confidence_resume):
    client, _conn, resume_id = low_confidence_resume
    resp = client.post(
        f"/api/resumes/{resume_id}/fields/name/review", json={"human_value": "x"}
    )
    assert resp.status_code == 200
    resp2 = client.post(
        f"/api/resumes/{resume_id}/fields/years_of_experience/review", json={"human_value": "5"}
    )
    assert resp2.status_code == 404  # 该字段没有 pending 队列行
