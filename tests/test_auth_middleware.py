# tests/test_auth_middleware.py
from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.middleware.auth import AuthMiddleware, reviewer_of
from app.storage.auth_session import create_session
from app.storage.db import init_schema
from app.storage.hr_account import upsert_account


def _make_app(conn: sqlite3.Connection) -> FastAPI:
    app = FastAPI()
    app.add_middleware(AuthMiddleware, conn=conn, root_path="")

    @app.get("/api/resumes/1")
    def get_resume():
        return {"ok": True}

    @app.get("/api/jobs")
    def list_jobs():
        return {"ok": True}

    @app.get("/whoami")
    def whoami(request: Request):
        return {"reviewer": reviewer_of(request)}

    return app


@pytest.fixture
def conn():
    # check_same_thread=False：TestClient 把同步路由处理函数派到 anyio 的
    # worker 线程执行（与 app/storage/db.py::get_connection 的既有约定一致），
    # 这条连接会跨线程被 AuthMiddleware 读取。
    c = sqlite3.connect(":memory:", check_same_thread=False)
    init_schema(c)
    return c


def test_protected_path_without_cookie_is_401(conn):
    client = TestClient(_make_app(conn))
    resp = client.get("/api/resumes/1")
    assert resp.status_code == 401


def test_unprotected_path_without_cookie_passes(conn):
    client = TestClient(_make_app(conn))
    resp = client.get("/api/jobs")
    assert resp.status_code == 200


def test_protected_path_with_valid_cookie_passes(conn):
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client = TestClient(_make_app(conn))
    client.cookies.set("hr_session", token)
    resp = client.get("/api/resumes/1")
    assert resp.status_code == 200


def test_reviewer_of_returns_real_username_not_unknown(conn):
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client = TestClient(_make_app(conn))
    client.cookies.set("hr_session", token)
    resp = client.get("/whoami")
    assert resp.json()["reviewer"] == "alice"
