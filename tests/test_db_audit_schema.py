import sqlite3

import pytest

from app.storage.db import get_connection, init_schema


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "audit.db"))
    init_schema(c)
    return c


def _notnull_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})") if row[3]}


def _nullable_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})") if not row[3]}


def _table_sql(conn: sqlite3.Connection, name: str) -> str:
    return conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()[0]


def _insert_run(conn: sqlite3.Connection, run_id: str = "run-1", **overrides) -> None:
    row = {
        "id": run_id,
        "application_id": None,
        "job_id": None,
        "configured_model": "deepseek-chat",
        "response_model": "deepseek-chat-241226",
        "system_fingerprint": "fp_abc",
        "prompt_version": "score-v1",
        "temperature": 0.0,
        "input_hash": "sha256:deadbeef",
        "rubric_snapshot": '{"criteria": []}',
        "raw_response": '{"score": 3}',
        "token_usage": '{"total_tokens": 12}',
        "latency_ms": 812.5,
    }
    row.update(overrides)
    conn.execute(
        "INSERT INTO analysis_run (id, application_id, job_id, configured_model, "
        "response_model, system_fingerprint, prompt_version, temperature, input_hash, "
        "rubric_snapshot, raw_response, token_usage, latency_ms) "
        "VALUES (:id, :application_id, :job_id, :configured_model, :response_model, "
        ":system_fingerprint, :prompt_version, :temperature, :input_hash, "
        ":rubric_snapshot, :raw_response, :token_usage, :latency_ms)",
        row,
    )
    conn.commit()


# ── analysis_run ────────────────────────────────────────────────────────


def test_audit_tables_exist(conn):
    tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"analysis_run", "criterion_score", "pending_approval"} <= tables


def test_analysis_run_notnull_set_is_exactly_the_seven_reproducibility_columns(conn):
    """
    U1 的头号约束的机器判据。这七列是"复现一次调用"的最小集合，其余全部可空。

    多一列 NOT NULL，U3 把 RecorderAuditHook 接到 _gateway_factory() 的当天，
    M1 的岗位画像采集就会开始写这张表并撞上约束——采集期没有投递、没有 rubric。
    """
    assert _notnull_columns(conn, "analysis_run") == {
        "id",
        "configured_model",
        "prompt_version",
        "temperature",
        "input_hash",
        "raw_response",
        "created_at",
    }


def test_analysis_run_business_and_rubric_columns_are_nullable(conn):
    assert _nullable_columns(conn, "analysis_run") == {
        "application_id",
        "job_id",
        "response_model",
        "system_fingerprint",
        "rubric_snapshot",
        "token_usage",
        "latency_ms",
    }


def test_analysis_run_accepts_intake_shaped_row_without_application_or_rubric(conn):
    """U3 合并当天 M1 采集流程会写的就是这个形状：没投递、没 rubric、没指纹。"""
    _insert_run(
        conn,
        "run-intake",
        application_id=None,
        job_id=None,
        rubric_snapshot=None,
        system_fingerprint=None,
        token_usage=None,
        latency_ms=None,
        response_model=None,
    )

    row = conn.execute(
        "SELECT application_id, rubric_snapshot, system_fingerprint, created_at "
        "FROM analysis_run WHERE id='run-intake'"
    ).fetchone()
    assert row[0] is None
    assert row[1] is None
    assert row[2] is None
    assert row[3] is not None  # created_at 由数据库补，调用方不必给


def test_analysis_run_keeps_configured_and_response_model_apart(conn):
    """铁律 5：配置侧别名与响应实际返回的标识分两字段，不互相覆盖。"""
    _insert_run(
        conn,
        "run-models",
        configured_model="deepseek-chat",
        response_model="deepseek-chat-241226",
    )

    row = conn.execute(
        "SELECT configured_model, response_model FROM analysis_run WHERE id='run-models'"
    ).fetchone()
    assert row == ("deepseek-chat", "deepseek-chat-241226")


def test_analysis_run_carries_no_training_use_marker(conn):
    """
    spec「留痕数据的用途限制」：开发者查看表定义时必须看得到禁止训练的标注。
    注释写在 CREATE TABLE 的括号内部，才会被 sqlite_master.sql 保留下来——
    写在语句外面的注释 sqlite3 .schema 看不到，等于没写。
    """
    sql = _table_sql(conn, "analysis_run")
    assert "禁止用作任何模型的训练" in sql
    assert "Amazon 2018" in sql


def test_analysis_run_has_application_index(conn):
    indexes = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }
    assert "idx_analysis_run_application" in indexes
