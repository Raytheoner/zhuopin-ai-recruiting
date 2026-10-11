"""审批页与页面数据的渲染、登录跳转与"页面不含 AI 录用结论"反证（tasks 3.4）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account
from app.storage.offer_approval_chain import put_approval_chain

STATIC = Path("app/web/static")

# spec Scenario「页面不含 AI 录用建议」的反证词表。⛔ 页面与详情载荷里
# 一个都不许出现——包括"我们没用到评分"这类自我说明（说明本身也含该词）。
FORBIDDEN_AI_DECISION_WORDS = (
    "建议录用", "录用建议", "评分", "排名", "总分", "score", "ranking", "recommend",
)


def _seed(conn):
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


def _create_offer(client, conn):
    _login(client, conn, "hr1")
    resp = client.post(
        "/api/applications/app1/offer",
        json={
            "department": "研发部",
            "start_date": "2026-11-01",
            "report_to": "李四",
            "note": "面试表现符合岗位要求",
            "request_id": "req-1",
        },
    )
    assert resp.status_code == 201
    return resp.json()["id"]


@pytest.fixture
def env(make_test_client):
    client, conn = make_test_client()
    _seed(conn)
    return client, conn


# ── 页面本身（静态路由 + 窄屏 viewport + 401 跳登录） ────────────


def test_pages_render(env):
    client, conn = env
    offer_id = _create_offer(client, conn)
    pending = client.get("/offers/pending")
    assert pending.status_code == 200
    assert "Offer" in pending.text
    assert "<!--BASE_HREF-->" not in pending.text  # base href 已被替换
    detail = client.get(f"/offers/{offer_id}/approve")
    assert detail.status_code == 200
    assert "审批留痕" in detail.text


def test_pages_redirect_to_login_on_401():
    for name in ("offers_pending.html", "offer_approve.html"):
        html = (STATIC / name).read_text(encoding="utf-8")
        assert 'window.location.href = "login"' in html, (
            f"{name} 缺 401 → 跳登录的分支：页面路由不在 AuthMiddleware 的"
            "PROTECTED_PATH_PREFIXES 里，未登录时页面会照常返回 HTML，"
            "必须由页面 JS 自己把用户送回登录页（与 resume_list.html 同款）"
        )
        assert "width=device-width" in html


def test_pages_have_no_ai_decision_text():
    for name in ("offers_pending.html", "offer_approve.html"):
        flat = (STATIC / name).read_text(encoding="utf-8").lower()
        for word in FORBIDDEN_AI_DECISION_WORDS:
            assert word not in flat, f"{name} 里出现反证词：{word}"


# ── 页面数据（API） ─────────────────────────────────────────────


def test_pending_api_requires_login(make_test_client):
    client, conn = make_test_client()
    _seed(conn)
    assert client.get("/api/offers/pending").status_code == 401


def test_pending_api_lists_only_current_approver(env):
    client, conn = env
    offer_id = _create_offer(client, conn)
    _login(client, conn, "mgr")
    mine = client.get("/api/offers/pending").json()["offers"]
    assert [item["id"] for item in mine] == [offer_id]
    assert mine[0]["current_level"] == 1
    _login(client, conn, "gm")
    assert client.get("/api/offers/pending").json()["offers"] == []
    _login(client, conn, "hr1")
    assert client.get("/api/offers/pending").json()["offers"] == []


def test_detail_api_requires_login_and_returns_fields(env):
    client, conn = env
    offer_id = _create_offer(client, conn)
    client.cookies.clear()
    assert client.get(f"/api/offers/{offer_id}").status_code == 401
    _login(client, conn, "mgr")
    body = client.get(f"/api/offers/{offer_id}").json()
    assert body["department"] == "研发部"
    assert body["start_date"] == "2026-11-01"
    assert body["report_to"] == "李四"
    assert body["status_label"] == "待审批"
    assert body["approvals"] == []


def test_detail_api_unknown_offer_returns_404(env):
    client, conn = env
    _login(client, conn, "mgr")
    assert client.get("/api/offers/no-such-offer").status_code == 404


def test_detail_payload_has_no_ai_decision_text(env):
    client, conn = env
    offer_id = _create_offer(client, conn)
    _login(client, conn, "mgr")
    flat = json.dumps(
        client.get(f"/api/offers/{offer_id}").json(), ensure_ascii=False
    ).lower()
    for word in FORBIDDEN_AI_DECISION_WORDS:
        assert word not in flat, f"详情载荷里出现反证词：{word}"
