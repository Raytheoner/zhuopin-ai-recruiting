# tests/test_live_resume_gate.py
from __future__ import annotations

import sqlite3

import pytest

from app.middleware.auth import AuthContext
from app.storage.db import init_schema
from app.storage.live_resume_gate import is_live_resume_intake_enabled


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    return c


AUTHED = AuthContext(user_id="alice", authenticated=True)
UNKNOWN = AuthContext(user_id=None, authenticated=False)


def test_default_off_even_when_authed(conn, monkeypatch):
    monkeypatch.delenv("LIVE_RESUME_INTAKE_ENABLED", raising=False)
    assert is_live_resume_intake_enabled(auth=AUTHED, conn=conn) is False


def test_env_on_but_identity_unknown_stays_off(conn, monkeypatch):
    """闸开启但登录身份不可识别 ⇒ 关（spec Scenario）。"""
    monkeypatch.setenv("LIVE_RESUME_INTAKE_ENABLED", "true")
    assert is_live_resume_intake_enabled(auth=UNKNOWN, conn=conn) is False


def test_env_on_and_identity_known_and_access_log_ok(conn, monkeypatch):
    monkeypatch.setenv("LIVE_RESUME_INTAKE_ENABLED", "true")
    assert is_live_resume_intake_enabled(auth=AUTHED, conn=conn) is True


def test_env_off_blocks_even_with_identity(conn, monkeypatch):
    monkeypatch.setenv("LIVE_RESUME_INTAKE_ENABLED", "false")
    assert is_live_resume_intake_enabled(auth=AUTHED, conn=conn) is False


def test_missing_access_log_table_blocks(monkeypatch):
    """访问留痕表不存在 ⇒ 探针失败 ⇒ 闸求值为关（两个结构性前置之二）。"""
    monkeypatch.setenv("LIVE_RESUME_INTAKE_ENABLED", "true")
    bare_conn = sqlite3.connect(":memory:")  # 没跑 init_schema，表都不存在
    assert is_live_resume_intake_enabled(auth=AUTHED, conn=bare_conn) is False


def test_runtime_toggle_takes_effect_without_restart(conn, monkeypatch):
    """运行期间开关变化：改环境变量立刻生效，不缓存（spec Scenario）。"""
    monkeypatch.delenv("LIVE_RESUME_INTAKE_ENABLED", raising=False)
    assert is_live_resume_intake_enabled(auth=AUTHED, conn=conn) is False
    monkeypatch.setenv("LIVE_RESUME_INTAKE_ENABLED", "true")
    assert is_live_resume_intake_enabled(auth=AUTHED, conn=conn) is True


def test_never_raises_on_broken_state(conn):
    """auth=None 这种调用方传错类型的情况，⛔ 不许抛异常——未知即拦截。"""
    assert is_live_resume_intake_enabled(auth=None, conn=conn) is False  # type: ignore[arg-type]
