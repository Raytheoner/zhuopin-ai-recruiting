"""U3 邀约与同意流程 e2e（voice-structured-interview tasks 4.11）：
签发 → 打开 → 双同意 → 验证码 → in_progress。

沿用 tests/conftest.py::make_test_client（工厂 fixture，调用后返回
(client, conn) 二元组——⛔ 不能直接解包 fixture 本身）。`/api/applications/*`
挂了登录中间件（app/middleware/auth.py），HR 侧端点需要先建账号 + session
才能调用，候选人侧端点必须在没有 hr_session cookie 的情况下也能访问。
"""
from __future__ import annotations

import re

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account


def _seed_application(conn, *, application_id="app1", job_id="j1", version=1) -> None:
    conn.execute("INSERT INTO job (id, title) VALUES (?, 'ECU 工程师')", (job_id,))
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', ?, 'synthetic', 'a.pdf', 'hash', 'tester')",
        (job_id,),
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, 'cand-1', ?, 'r1', 'initial')",
        (application_id, job_id),
    )
    conn.execute(
        "INSERT INTO analysis_run (id, configured_model, prompt_version, temperature, "
        "input_hash, raw_response) VALUES ('run-1', 'deepseek-chat', 'interview-prep-v1', 0, 'h', 'r')"
    )
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id, status) "
        "VALUES ('snap1', ?, ?, 1, 'run-1', 'frozen')",
        (application_id, version),
    )
    conn.commit()


def _login_as_hr(conn, client) -> None:
    account_id = upsert_account(conn, username="tester", password="testpass123")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)


def _issue_session(client, *, application_id="app1", sample_class="internal_sim", request_id="req-1"):
    return client.post(
        f"/api/applications/{application_id}/interview-sessions",
        json={
            "prep_snapshot_version": 1,
            "sample_class": sample_class,
            "request_id": request_id,
        },
    )


def _token_from_candidate_link(candidate_link: str) -> str:
    m = re.search(r"/interview/([^/]+)$", candidate_link)
    assert m, candidate_link
    return m.group(1)


def test_full_invite_to_in_progress_e2e(make_test_client):
    client, conn = make_test_client()
    _seed_application(conn)
    _login_as_hr(conn, client)

    resp = _issue_session(client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    session_id = body["session_id"]
    token = _token_from_candidate_link(body["candidate_link"])
    assert body["delivery_mode"] == "manual_handoff"  # 总开关默认关闭

    # 候选人打开链接：不带 HR 登录态——候选人端必须公开访问
    client.cookies.clear()
    opened = client.get(f"/interview/invite/{token}")
    assert opened.status_code == 200, opened.text
    opened_body = opened.json()
    assert opened_body["session_id"] == session_id
    kinds = {term["kind"] for term in opened_body["consent_terms"]}
    assert kinds == {"ai_interview", "identity_check"}
    assert all(term["text"] for term in opened_body["consent_terms"])

    for kind in ("ai_interview", "identity_check"):
        consent = client.post(
            f"/interview/sessions/{session_id}/consent",
            json={"kind": kind, "result": "accepted"},
        )
        assert consent.status_code == 200, consent.text

    requested = client.post(f"/interview/sessions/{session_id}/verification-code/request")
    assert requested.status_code == 200, requested.text

    # HR 工作台读取明文验证码：无登录态应被拒绝
    unauthorized_view = client.get(f"/api/interview-sessions/{session_id}/verification-code")
    assert unauthorized_view.status_code == 401

    _login_as_hr(conn, client)
    hr_view = client.get(f"/api/interview-sessions/{session_id}/verification-code")
    assert hr_view.status_code == 200, hr_view.text
    code = hr_view.json()["code"]
    assert len(code) == 6

    client.cookies.clear()
    verified = client.post(
        f"/interview/sessions/{session_id}/verification-code/verify", json={"code": code}
    )
    assert verified.status_code == 200, verified.text

    status_resp = client.get(f"/api/interview-sessions/{session_id}")
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "in_progress"

    row = conn.execute(
        "SELECT phone_verified_at FROM interview_session WHERE id = ?", (session_id,)
    ).fetchone()
    assert row[0] is not None
    identity = conn.execute(
        "SELECT result FROM identity_check WHERE session_id = ?", (session_id,)
    ).fetchone()
    assert identity == ("skipped",)


def test_reopen_used_invite_link_returns_unified_invalid_page(make_test_client):
    client, conn = make_test_client()
    _seed_application(conn)
    _login_as_hr(conn, client)

    resp = _issue_session(client, request_id="req-2")
    token = _token_from_candidate_link(resp.json()["candidate_link"])

    client.cookies.clear()
    first = client.get(f"/interview/invite/{token}")
    assert first.status_code == 200

    second = client.get(f"/interview/invite/{token}")
    assert second.status_code == 410
    assert "失效" in second.json()["detail"]


def test_decline_consent_abandons_session(make_test_client):
    client, conn = make_test_client()
    _seed_application(conn)
    _login_as_hr(conn, client)

    resp = _issue_session(client, request_id="req-3")
    session_id = resp.json()["session_id"]
    token = _token_from_candidate_link(resp.json()["candidate_link"])

    client.cookies.clear()
    client.get(f"/interview/invite/{token}")

    declined = client.post(
        f"/interview/sessions/{session_id}/consent",
        json={"kind": "ai_interview", "result": "declined"},
    )
    assert declined.status_code == 200

    status_resp = client.get(f"/api/interview-sessions/{session_id}")
    assert status_resp.json()["status"] == "abandoned"


def test_live_session_rejected_without_open_gates(make_test_client):
    client, conn = make_test_client()
    _seed_application(conn)
    _login_as_hr(conn, client)

    resp = _issue_session(client, sample_class="live", request_id="req-4")
    assert resp.status_code == 403


def test_issue_without_frozen_snapshot_returns_409(make_test_client):
    client, conn = make_test_client()
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', 'ECU 工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'hash', 'tester')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1', 'cand-1', 'j1', 'r1', 'initial')"
    )
    conn.commit()
    _login_as_hr(conn, client)

    resp = _issue_session(client, request_id="req-5")
    assert resp.status_code == 409
