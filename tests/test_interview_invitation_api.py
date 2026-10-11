"""U3 路由契约（tasks 3.1、3.4–3.7）。

生成阶段的 LLM 调用在**路由边界**打桩（monkeypatch `app.web.server.
compute_invitation_draft_for_slot`）——节点级行为由 tests/test_invitation_effect.py
与 tests/test_invitation_drafter.py 覆盖，本文件只验接口契约、权限、幂等与页面。
"""
from __future__ import annotations

import pytest

from app.agents.invitation_drafter import InvitationDraft
from app.agents.jd_agent import AI_LABEL_PREFIX, enforce_ai_label
from app.middleware.auth import SESSION_COOKIE_NAME
from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account
from app.web import server as server_module
from app.web.server import create_app

GENERATED_AT = "2026-10-10T00:00:00+00:00"


def _seed(conn) -> None:
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式软件工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT INTO resume "
        "(id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'sha-1', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1', 'c1', 'j1', 'r1', 'interview')"
    )
    conn.execute(
        "INSERT INTO interview_slot (id, application_id, round, start_at, end_at, mode) "
        "VALUES ('s1', 'app1', 1, '2026-10-12 14:00', '2026-10-12 15:00', 'onsite')"
    )
    conn.execute(
        "INSERT INTO interview_slot (id, application_id, round, start_at, end_at, mode, status) "
        "VALUES ('s2', 'app1', 1, '2026-10-13 14:00', '2026-10-13 15:00', 'onsite', 'cancelled')"
    )
    conn.commit()


def _login(conn, client, *, role: str = "hr") -> None:
    account_id = upsert_account(conn, username="tester", password="testpass123")
    conn.execute("UPDATE hr_account SET role = ? WHERE id = ?", (role, account_id))
    conn.commit()
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set(SESSION_COOKIE_NAME, token)


@pytest.fixture
def client(make_test_client, monkeypatch):
    c, conn = make_test_client()
    _seed(conn)
    _login(conn, c)
    monkeypatch.setattr(
        server_module, "compute_invitation_draft_for_slot", _fake_compute()
    )
    return c, conn


def _fake_compute(run_ids=("run-u3-1",)):
    """路由边界的生成打桩：按顺序返回不同的 run_id（版本递增用例需要两个）。"""
    remaining = list(run_ids)

    def _compute(conn, *, slot_id, gateway, contact_hint=None):
        run_id = remaining.pop(0) if remaining else run_ids[-1]
        return (
            InvitationDraft(
                body=enforce_ai_label("您好，邀请您参加面试。", generated_at=GENERATED_AT),
                run_id=run_id,
                response_model="deepseek-chat-actual-v1",
                prompt_version="invite-v1",
            ),
            "v1",
        )

    return _compute


def _generate(client, slot_id: str = "s1"):
    return client.post(f"/api/interview-slots/{slot_id}/invitation/generate", json={})


def _draft_id(client, slot_id: str = "s1") -> str:
    return client.get(f"/api/interview-slots/{slot_id}/invitation").json()["drafts"][-1]["id"]


# ── 模板接口 ──────────────────────────────────────────────────────────────


def test_put_template_requires_login(make_test_client):
    c, conn = make_test_client()
    _seed(conn)
    res = c.put("/api/invitation-templates", json={"body": "您好 {job_title}"})
    assert res.status_code == 401


def test_put_template_requires_hr_role(make_test_client):
    c, conn = make_test_client()
    _seed(conn)
    _login(conn, c, role="interviewer")
    res = c.put("/api/invitation-templates", json={"body": "您好 {job_title}"})
    assert res.status_code == 403


def test_put_template_bumps_version(client):
    c, _conn = client
    first = c.put("/api/invitation-templates", json={"body": "第一版 {job_title}"})
    second = c.put("/api/invitation-templates", json={"body": "第二版 {job_title}"})
    assert first.status_code == 200 and first.json()["version"] == "v2"
    assert second.status_code == 200 and second.json()["version"] == "v3"
    assert second.json()["updated_by"] == "tester"


def test_put_template_rejects_forbidden_placeholder(client):
    c, _conn = client
    res = c.put("/api/invitation-templates", json={"body": "您好 {total_score}"})
    assert res.status_code == 422
    assert "评分" in res.json()["detail"] or "未登记" in res.json()["detail"]


# ── 详情与生成 ────────────────────────────────────────────────────────────


def test_detail_returns_seeded_template_and_empty_drafts(client):
    c, _conn = client
    body = c.get("/api/interview-slots/s1/invitation").json()
    assert body["slot"]["slot_id"] == "s1"
    assert body["slot"]["application_id"] == "app1"
    assert body["template"]["version"] == "v1"
    assert body["drafts"] == []
    assert body["outcomes"] == []


def test_generate_creates_version_one_with_ai_label(client):
    c, conn = client
    res = _generate(c)
    assert res.status_code == 201
    payload = res.json()
    assert len(payload["drafts"]) == 1
    assert payload["drafts"][0]["version"] == 1
    assert payload["drafts"][0]["ai_generated"] is True
    assert AI_LABEL_PREFIX in payload["drafts"][0]["body"]
    assert payload["slot"]["invitation_status"] == "drafted"


def test_generate_twice_appends_new_version_and_keeps_old(client, monkeypatch):
    c, _conn = client
    monkeypatch.setattr(
        server_module, "compute_invitation_draft_for_slot", _fake_compute(("run-1", "run-2"))
    )
    _generate(c)
    res = _generate(c)
    versions = [d["version"] for d in res.json()["drafts"]]
    assert versions == [1, 2]


def test_generate_rejects_cancelled_slot(client):
    c, _conn = client
    res = _generate(c, slot_id="s2")
    assert res.status_code == 409


# ── 编辑 / 标记人工撰写 ───────────────────────────────────────────────────


def test_edit_endpoint_keeps_ai_label(client):
    c, _conn = client
    _generate(c)
    draft_id = _draft_id(c)
    res = c.post(
        f"/api/interview-slots/s1/invitation/drafts/{draft_id}/edit",
        json={"body": "改了两句话"},
    )
    assert res.status_code == 200
    assert AI_LABEL_PREFIX in res.json()["body"]


def test_mark_human_written_endpoint_strips_label_and_records_actor(client):
    c, _conn = client
    _generate(c)
    draft_id = _draft_id(c)
    res = c.post(
        f"/api/interview-slots/s1/invitation/drafts/{draft_id}/mark-human-written"
    )
    assert res.status_code == 200
    assert AI_LABEL_PREFIX not in res.json()["body"]
    stored = c.get("/api/interview-slots/s1/invitation").json()["drafts"][0]
    assert stored["ai_generated"] is False
    assert stored["authorship_marked_by"] == "tester"
    assert stored["authorship_marked_at"]


# ── 回填 ──────────────────────────────────────────────────────────────────


def test_outcome_sent_sets_status_and_channel(client):
    c, _conn = client
    res = c.post(
        "/api/interview-slots/s1/invitation/outcome",
        json={"status": "sent", "channel": "wechat", "reason": None},
    )
    assert res.status_code == 200
    assert res.json()["slot"]["invitation_status"] == "sent"
    assert res.json()["slot"]["sent_channel"] == "wechat"
    assert res.json()["outcome"]["actor"] == "tester"


def test_outcome_declined_keeps_stage_and_creates_no_rejection_record(client):
    c, conn = client
    res = c.post(
        "/api/interview-slots/s1/invitation/outcome",
        json={"status": "declined", "channel": None, "reason": "已接受其他 offer"},
    )
    assert res.status_code == 200
    assert res.json()["slot"]["invitation_status"] == "declined"
    stage = conn.execute(
        "SELECT current_stage_id FROM application WHERE id = 'app1'"
    ).fetchone()[0]
    assert stage == "interview"
    assert conn.execute("SELECT COUNT(*) FROM rejection_record").fetchone()[0] == 0


def test_outcome_sent_without_channel_is_422(client):
    c, _conn = client
    res = c.post(
        "/api/interview-slots/s1/invitation/outcome",
        json={"status": "sent", "channel": None, "reason": None},
    )
    assert res.status_code == 422


# ── 系统外发（默认不可用） ────────────────────────────────────────────────


def test_send_endpoint_blocked_and_reports_reason(client):
    """默认环境下：外发总开关关 + vault 未开启（收件对象未知）⇒ 一律拦截，
    响应体把门禁给的原因原样带回页面。"""
    c, _conn = client
    _generate(c)
    res = c.post("/api/interview-slots/s1/invitation/send")
    assert res.status_code == 200
    assert res.json()["delivery_mode"] == "blocked"
    assert res.json()["blocked_reason"]


# ── 页面与子路径前缀 ──────────────────────────────────────────────────────


def test_invitation_page_works_under_subpath(tmp_path):
    db_path = str(tmp_path / "subpath.db")
    resume_dir = str(tmp_path / "resumes")

    def _gateway_factory():
        from app.llm.gateway import LLMGateway

        return LLMGateway(
            api_key="test", base_url="https://example.invalid",
            model="deepseek-chat", supports_json_schema=False, client=object(),
        )

    app = create_app(
        db_path=db_path, gateway_factory=_gateway_factory,
        root_path="/hr/recruit-agent", resume_storage_dir=resume_dir,
    )
    from app.storage.db import get_connection
    from fastapi.testclient import TestClient

    _seed(get_connection(db_path))
    with TestClient(app) as c:
        res = c.get("/hr/recruit-agent/interview-slots/s1/invitation")
        assert res.status_code == 200
        assert '<base href="/hr/recruit-agent/">' in res.text
        assert "api/interview-slots/" in res.text  # 相对路径，⛔ 无硬编码 /api/…
