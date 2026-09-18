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


def test_reparse_unreadable_resume_is_rejected_with_409(uploaded_resume):
    """
    不可读简历（TD-52 的扫描件退路）重解析必须被拒。放行的话 compute_parse 拿到
    空 span 列表，六个字段全 not_mentioned、置信度合成出 1.0（"满分"），
    effect_persist_parse 会把它标 parsed、零条人工校对、还建出字段全空的
    candidate/application——隔离区里的简历被静默送进后续筛选。
    """
    client, conn, _resume_id = uploaded_resume
    # "张三" 两个有效字符低于 MIN_EFFECTIVE_CHARS=50，ingest_resume_text 判
    # unreadable——与 tests/test_resume_upload.py 同一造法。
    files = [("files", ("scan.docx", _docx_bytes(["张三"]),
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]
    result = client.post(
        "/api/resumes/upload", data={"job_id": "j1", "sample_class": "synthetic"}, files=files
    ).json()["results"][0]
    assert result["parse_status"] == "unreadable", result
    unreadable_id = result["resume_id"]

    resp = client.post(f"/api/resumes/{unreadable_id}/reparse")

    assert resp.status_code == 409, resp.text
    assert "无法重新解析" in resp.json()["detail"]
    # 隔离区状态一点没动：没有投递、没有候选人、没有解析版本，状态仍是 unreadable
    assert conn.execute(
        "SELECT COUNT(*) FROM application WHERE resume_id = ?", (unreadable_id,)
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM resume_parse_version WHERE resume_id = ?", (unreadable_id,)
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT status FROM resume WHERE id = ?", (unreadable_id,)
    ).fetchone()[0] == "unreadable"
    # 这份简历没有带出任何新候选人（上传夹具那份 parsed 简历的候选人不算）
    assert conn.execute(
        "SELECT COUNT(*) FROM candidate WHERE id IN "
        "(SELECT candidate_id FROM application WHERE resume_id = ?)", (unreadable_id,)
    ).fetchone()[0] == 0
