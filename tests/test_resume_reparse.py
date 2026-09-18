from __future__ import annotations

import pytest

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account
from tests.test_resume_upload import _docx_bytes, _stub_llm_success


@pytest.fixture
def uploaded_resume(make_test_client, monkeypatch):
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.commit()
    _stub_llm_success(monkeypatch)
    # 内容需 ≥50 有效字符（MIN_EFFECTIVE_CHARS，app/parsing/extract_text.py），
    # 否则 ingest_resume_text 判 unreadable、_ingest_one_resume 提前返回、
    # 上传阶段不落 v1，reparse 只会产出 1 条版本而非 2 条——与本用例的验证意图
    # （上传产生 v1、重解析追加 v2）不符，单用 "张三" 两字凑不出这条前提。
    files = [("files", ("a.docx", _docx_bytes([
        "张三",
        "工作经历：某某公司嵌入式工程师，负责车身控制器固件开发，具有五年嵌入式软件"
        "开发经验，熟悉C语言与RTOS，参与过多个ECU项目的固件设计与测试工作。",
    ]), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]
    resp = client.post(
        "/api/resumes/upload", data={"job_id": "j1", "sample_class": "synthetic"}, files=files
    )
    resume_id = resp.json()["results"][0]["resume_id"]
    assert resp.json()["results"][0]["parse_status"] == "parsed"
    return client, conn, resume_id


def test_reparse_creates_new_version_and_updates_resume(uploaded_resume, monkeypatch):
    client, conn, resume_id = uploaded_resume
    resp = client.post(f"/api/resumes/{resume_id}/reparse")
    assert resp.status_code == 200
    versions = conn.execute(
        "SELECT parser_version FROM resume_parse_version WHERE resume_id = ? ORDER BY parser_version",
        (resume_id,),
    ).fetchall()
    assert len(versions) == 2


def test_reparse_nonexistent_resume_404(uploaded_resume):
    client, _conn, _resume_id = uploaded_resume
    resp = client.post("/api/resumes/does-not-exist/reparse")
    assert resp.status_code == 404
