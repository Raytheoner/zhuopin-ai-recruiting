"""U3 的 HTTP 面：发起 Offer / 逐级审批 / 退回后修改（tasks 3.1–3.3）。"""
from __future__ import annotations

import json

import pytest

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account
from app.storage.offer_approval_chain import put_approval_chain


def _seed(conn, *, chain=True):
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES ('j1','嵌入式工程师','approved')"
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1','张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
        "status, uploaded_by) VALUES ('r1','j1','synthetic','a.pdf','sha','parsed','hr')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1','c1','j1','r1','interview')"
    )
    for username in ("hr1", "mgr", "gm"):
        upsert_account(conn, username=username, password="s3cret!")
    conn.commit()
    if chain:
        put_approval_chain(
            conn,
            job_id="j1",
            chain=[
                {"level": 1, "approver_account_ids": ["mgr"]},
                {"level": 2, "approver_account_ids": ["gm"]},
            ],
            updated_by="hr1",
        )


def _login(client, conn, username):
    row = conn.execute(
        "SELECT id FROM hr_account WHERE username = ?", (username,)
    ).fetchone()
    token = create_session(conn, hr_account_id=row[0])
    client.cookies.set("hr_session", token)


@pytest.fixture
def env(make_test_client):
    client, conn = make_test_client()
    _seed(conn)
    return client, conn


_OFFER_BODY = {
    "department": "研发部",
    "start_date": "2026-11-01",
    "report_to": "李四",
    "note": "面试表现符合岗位要求",
    "request_id": "req-1",
}


def _create(client, conn, *, as_username="hr1", body=None, request_id=None):
    _login(client, conn, as_username)
    payload = dict(body or _OFFER_BODY)
    if request_id is not None:
        payload["request_id"] = request_id
    return client.post("/api/applications/app1/offer", json=payload)


# ── 发起 Offer（tasks 3.1） ─────────────────────────────────────


def test_create_requires_login(make_test_client):
    client, conn = make_test_client()
    _seed(conn)
    assert client.post("/api/applications/app1/offer", json=_OFFER_BODY).status_code == 401


def test_create_offer_success(env):
    client, conn = env
    resp = _create(client, conn)
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending_approval"
    assert body["status_label"] == "待审批"
    assert body["approval_round"] == 1
    assert body["current_level"] == 1
    assert body["total_levels"] == 2
    assert body["approvals"] == []
    assert body["job_title"] == "嵌入式工程师"
    assert body["candidate_name"] == "张三"
    assert conn.execute(
        "SELECT current_stage_id FROM application WHERE id = 'app1'"
    ).fetchone()[0] == "offer"
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE application_id = 'app1'"
    ).fetchone()[0] == 1
    rows = conn.execute("SELECT thread_id, message_type FROM outbox").fetchall()
    assert rows == [(body["id"], "offer_approval_todo")]


def test_create_replay_same_request_id_returns_same_offer(env):
    client, conn = env
    first = _create(client, conn).json()
    second = _create(client, conn).json()
    assert first["id"] == second["id"]
    assert conn.execute("SELECT COUNT(*) FROM offer").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1


def test_create_conflicts_when_offer_exists_with_new_request_id(env):
    client, conn = env
    _create(client, conn)
    resp = _create(client, conn, request_id="req-2")
    assert resp.status_code == 409
    assert "已有 Offer" in resp.json()["detail"]


def test_create_rejected_for_rejected_application(env):
    client, conn = env
    conn.execute("UPDATE application SET status = 'rejected' WHERE id = 'app1'")
    conn.commit()
    resp = _create(client, conn)
    assert resp.status_code == 409
    assert "已终止" in resp.json()["detail"]


def test_create_unknown_application_returns_404(env):
    client, conn = env
    _login(client, conn, "hr1")
    resp = client.post("/api/applications/no-such-app/offer", json=_OFFER_BODY)
    assert resp.status_code == 404


# ── 逐级审批（tasks 3.2） ───────────────────────────────────────


def test_approval_requires_login(env):
    client, conn = env
    offer_id = _create(client, conn).json()["id"]
    client.cookies.clear()
    resp = client.post(f"/api/offers/{offer_id}/approval", json={"decision": "approved"})
    assert resp.status_code == 401


def test_non_approver_gets_403(env):
    client, conn = env
    offer_id = _create(client, conn).json()["id"]
    _login(client, conn, "hr1")
    resp = client.post(f"/api/offers/{offer_id}/approval", json={"decision": "approved"})
    assert resp.status_code == 403
    assert "不是第 1 级审批人" in resp.json()["detail"]


def test_return_without_comment_is_422(env):
    client, conn = env
    offer_id = _create(client, conn).json()["id"]
    _login(client, conn, "mgr")
    resp = client.post(
        f"/api/offers/{offer_id}/approval",
        json={"decision": "returned", "comment": "   "},
    )
    assert resp.status_code == 422
    assert "退回必须填意见" in resp.json()["detail"]


def test_bad_decision_is_422(env):
    client, conn = env
    offer_id = _create(client, conn).json()["id"]
    _login(client, conn, "mgr")
    resp = client.post(f"/api/offers/{offer_id}/approval", json={"decision": "maybe"})
    assert resp.status_code == 422


def test_unconfigured_chain_is_409(make_test_client):
    client, conn = make_test_client()
    _seed(conn, chain=False)
    offer_id = _create(client, conn).json()["id"]
    _login(client, conn, "mgr")
    resp = client.post(f"/api/offers/{offer_id}/approval", json={"decision": "approved"})
    assert resp.status_code == 409
    assert "尚未配置审批人" in resp.json()["detail"]


def test_two_level_approval_via_http(env):
    client, conn = env
    offer_id = _create(client, conn).json()["id"]
    _login(client, conn, "mgr")
    first = client.post(
        f"/api/offers/{offer_id}/approval", json={"decision": "approved", "level": 1}
    )
    assert first.status_code == 200
    assert first.json()["status"] == "pending_approval"
    _login(client, conn, "gm")
    second = client.post(
        f"/api/offers/{offer_id}/approval", json={"decision": "approved", "level": 2}
    )
    assert second.status_code == 200
    assert second.json()["status"] == "approved"
    assert [a["level"] for a in second.json()["approvals"]] == [1, 2]
    assert [a["approver"] for a in second.json()["approvals"]] == ["mgr", "gm"]
    assert conn.execute(
        "SELECT COUNT(*) FROM outbox WHERE message_type = 'offer_approval_todo'"
    ).fetchone()[0] == 2


def test_replay_approval_is_idempotent_over_http(env):
    client, conn = env
    offer_id = _create(client, conn).json()["id"]
    _login(client, conn, "mgr")
    body = {"decision": "approved", "level": 1}
    assert client.post(f"/api/offers/{offer_id}/approval", json=body).status_code == 200
    assert client.post(f"/api/offers/{offer_id}/approval", json=body).status_code == 200
    assert conn.execute(
        "SELECT COUNT(*) FROM offer_approval WHERE offer_id = ?", (offer_id,)
    ).fetchone()[0] == 1


def test_replay_without_level_after_advance_is_rejected(env):
    client, conn = env
    offer_id = _create(client, conn).json()["id"]
    _login(client, conn, "mgr")
    assert client.post(
        f"/api/offers/{offer_id}/approval", json={"decision": "approved", "level": 1}
    ).status_code == 200
    assert client.post(
        f"/api/offers/{offer_id}/approval", json={"decision": "approved"}
    ).status_code == 403
    assert conn.execute(
        "SELECT COUNT(*) FROM offer_approval WHERE offer_id = ?", (offer_id,)
    ).fetchone()[0] == 1


# ── 退回后修改（tasks 3.3） ─────────────────────────────────────


def test_return_then_revise_then_two_levels(env):
    client, conn = env
    offer_id = _create(client, conn).json()["id"]
    _login(client, conn, "mgr")
    returned = client.post(
        f"/api/offers/{offer_id}/approval",
        json={"decision": "returned", "comment": "入职日期待与候选人确认"},
    )
    assert returned.status_code == 200
    assert returned.json()["status"] == "needs_revision"
    _login(client, conn, "hr1")
    revised = client.patch(
        f"/api/offers/{offer_id}",
        json={
            "department": "研发部",
            "start_date": "2026-12-01",
            "report_to": "李四",
            "note": "入职日期已确认",
            "request_id": "req-revise-1",
        },
    )
    assert revised.status_code == 200
    assert revised.json()["approval_round"] == 2
    assert revised.json()["status"] == "pending_approval"
    assert revised.json()["current_level"] == 1
    _login(client, conn, "mgr")
    assert client.post(
        f"/api/offers/{offer_id}/approval", json={"decision": "approved"}
    ).json()["status"] == "pending_approval"
    _login(client, conn, "gm")
    assert client.post(
        f"/api/offers/{offer_id}/approval", json={"decision": "approved"}
    ).json()["status"] == "approved"
    assert conn.execute(
        "SELECT COUNT(*) FROM offer_approval WHERE offer_id = ?", (offer_id,)
    ).fetchone()[0] == 3


def test_revise_replay_same_request_id_is_idempotent(env):
    client, conn = env
    offer_id = _create(client, conn).json()["id"]
    _login(client, conn, "mgr")
    client.post(
        f"/api/offers/{offer_id}/approval",
        json={"decision": "returned", "comment": "改"},
    )
    _login(client, conn, "hr1")
    body = {
        "department": "研发部",
        "start_date": "2026-12-01",
        "report_to": "李四",
        "note": None,
        "request_id": "req-revise-1",
    }
    assert client.patch(f"/api/offers/{offer_id}", json=body).json()["approval_round"] == 2
    assert client.patch(f"/api/offers/{offer_id}", json=body).json()["approval_round"] == 2
    assert conn.execute(
        "SELECT approval_round FROM offer WHERE id = ?", (offer_id,)
    ).fetchone()[0] == 2


def test_revise_requires_needs_revision_status(env):
    client, conn = env
    offer_id = _create(client, conn).json()["id"]
    _login(client, conn, "hr1")
    resp = client.patch(
        f"/api/offers/{offer_id}",
        json={
            "department": "研发部",
            "start_date": "2026-12-01",
            "report_to": "李四",
            "note": None,
            "request_id": "req-revise-1",
        },
    )
    assert resp.status_code == 409
    assert "待修改" in resp.json()["detail"]


def test_revise_unknown_offer_returns_404(env):
    client, conn = env
    _login(client, conn, "hr1")
    resp = client.patch(
        "/api/offers/no-such-offer",
        json={
            "department": "研发部",
            "start_date": "2026-12-01",
            "report_to": "李四",
            "note": None,
            "request_id": "req-revise-1",
        },
    )
    assert resp.status_code == 404


# ── 反证：载荷里没有 AI 录用结论（spec「录用决定不由 AI 做」） ──


def test_offer_payload_has_no_ai_decision_text(env):
    client, conn = env
    offer_id = _create(client, conn).json()["id"]
    _login(client, conn, "mgr")
    payload = client.post(
        f"/api/offers/{offer_id}/approval", json={"decision": "approved"}
    ).json()
    flat = json.dumps(payload, ensure_ascii=False)
    for word in (
        "建议录用", "录用建议", "评分", "排名", "总分", "score", "ranking", "recommend"
    ):
        assert word not in flat
