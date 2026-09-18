# tests/test_auth_routes.py
from __future__ import annotations

from app.storage.hr_account import upsert_account


def _client_with_account(make_test_client):
    client, conn = make_test_client()
    upsert_account(conn, username="alice", password="s3cret!")
    return client


def test_login_wrong_password_401(make_test_client):
    client = _client_with_account(make_test_client)
    resp = client.post("/api/auth/login", json={"username": "alice", "password": "wrong"})
    assert resp.status_code == 401


def test_login_then_protected_route_then_logout(make_test_client):
    client = _client_with_account(make_test_client)
    resp = client.post("/api/auth/login", json={"username": "alice", "password": "s3cret!"})
    assert resp.status_code == 200
    assert "hr_session" in resp.cookies

    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200
    # 登出后同一个 client 的 cookie 已被清掉，受保护路径应再次 401
    resp = client.get("/api/resumes/nonexistent/text")
    assert resp.status_code == 401
