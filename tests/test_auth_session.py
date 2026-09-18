# tests/test_auth_session.py
from __future__ import annotations

import sqlite3

import pytest

from app.storage.auth_session import create_session, delete_session, resolve_session
from app.storage.db import init_schema
from app.storage.hr_account import upsert_account


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    return c


@pytest.fixture
def account_id(conn):
    return upsert_account(conn, username="alice", password="s3cret!")


def test_create_then_resolve_returns_username(conn, account_id):
    token = create_session(conn, hr_account_id=account_id)
    assert resolve_session(conn, token) == "alice"


def test_resolve_unknown_token_returns_none(conn):
    assert resolve_session(conn, "not-a-real-token") is None


def test_resolve_expired_session_returns_none(conn, account_id, monkeypatch):
    import app.storage.auth_session as mod

    monkeypatch.setattr(mod, "SESSION_TTL_SECONDS", -1)  # 立刻过期
    token = create_session(conn, hr_account_id=account_id)
    assert resolve_session(conn, token) is None


def test_delete_session_invalidates_it(conn, account_id):
    token = create_session(conn, hr_account_id=account_id)
    delete_session(conn, token)
    assert resolve_session(conn, token) is None


def test_each_call_returns_a_distinct_high_entropy_token(conn, account_id):
    a = create_session(conn, hr_account_id=account_id)
    b = create_session(conn, hr_account_id=account_id)
    assert a != b
    assert len(a) >= 32
