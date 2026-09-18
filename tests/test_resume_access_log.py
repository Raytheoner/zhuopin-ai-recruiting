from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

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
    # ⚠️ 同 Task 4/8 踩过的坑：仅 "张三" 两个有效字符低于
    # app/parsing/extract_text.py::MIN_EFFECTIVE_CHARS=50，会被判成
    # unreadable，raw_text/spans/parsed_json 全部留空——本用例要验证的是"读接口
    # 真的返回了内容且被记了访问日志"，不是空壳，所以补一段占位正文把有效字符数
    # 撑过阈值，不改路由代码。
    filler = (
        "这是一段用于测试的简历正文占位文字，确保解析有效字符数超过五十个字符阈值，"
        "多写几句话把有效字符数字凑够。"
    )
    files = [("files", ("a.docx", _docx_bytes(["张三", filler]),
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]
    resp = client.post(
        "/api/resumes/upload", data={"job_id": "j1", "sample_class": "synthetic"}, files=files
    )
    result = resp.json()["results"][0]
    assert result["parse_status"] == "parsed", result
    resume_id = result["resume_id"]
    return client, conn, resume_id


@pytest.mark.parametrize(
    "path_suffix,access_type",
    [("text", "raw_text"), ("spans", "spans"), ("parsed", "parsed_result")],
)
def test_each_read_endpoint_writes_access_log(uploaded_resume, path_suffix, access_type):
    client, conn, resume_id = uploaded_resume
    resp = client.get(f"/api/resumes/{resume_id}/{path_suffix}")
    assert resp.status_code == 200
    row = conn.execute(
        "SELECT accessor, access_type FROM resume_access_log "
        "WHERE resume_id = ? AND access_type = ?",
        (resume_id, access_type),
    ).fetchone()
    assert row == ("alice", access_type)


def test_download_writes_access_log_and_returns_file(uploaded_resume):
    client, conn, resume_id = uploaded_resume
    resp = client.get(f"/api/resumes/{resume_id}/download")
    assert resp.status_code == 200
    row = conn.execute(
        "SELECT 1 FROM resume_access_log WHERE resume_id = ? AND access_type = 'download'",
        (resume_id,),
    ).fetchone()
    assert row is not None


def test_access_log_write_failure_blocks_the_read(uploaded_resume, monkeypatch):
    client, _conn, resume_id = uploaded_resume
    import app.web.server as server_mod

    def _boom(*_args, **_kwargs):
        raise sqlite3_error()

    def sqlite3_error():
        import sqlite3
        return sqlite3.OperationalError("disk full (模拟)")

    monkeypatch.setattr(server_mod, "record_resume_access", _boom)
    # ⚠️ 默认 TestClient(raise_server_exceptions=True) 会把 ServerErrorMiddleware
    # 已经发送出去的 500 响应吞掉、转成 pytest 里的一次异常重新抛出（Starlette
    # 的 ServerErrorMiddleware 发完响应后仍会 re-raise，供 ASGI 服务器记日志）。
    # 这里要断言的是"路由确实返回了 500"，不是"异常确实发生过"，所以要用同一个
    # app 另建一个 raise_server_exceptions=False 的客户端，并带上原客户端的
    # 登录态 cookie（同一个 app / 同一个 conn，只是不同的 httpx 客户端外壳）。
    no_raise_client = TestClient(client.app, raise_server_exceptions=False)
    no_raise_client.cookies.update(client.cookies)
    resp = no_raise_client.get(f"/api/resumes/{resume_id}/text")
    assert resp.status_code == 500
