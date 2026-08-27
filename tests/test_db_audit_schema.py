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


# ── criterion_score ─────────────────────────────────────────────────────


def test_criterion_score_accepts_row_with_evidence(conn):
    _insert_run(conn)
    conn.execute(
        "INSERT INTO criterion_score (id, analysis_run_id, criterion_key, score, evidence_ref) "
        "VALUES ('cs-1', 'run-1', 'embedded_c', 4.0, 'resume:cand-7#120-186')"
    )
    conn.commit()

    row = conn.execute(
        "SELECT analysis_run_id, evidence_ref FROM criterion_score WHERE id='cs-1'"
    ).fetchone()
    assert row == ("run-1", "resume:cand-7#120-186")


@pytest.mark.parametrize(
    "evidence",
    [
        pytest.param(None, id="null"),
        pytest.param("", id="empty"),
        pytest.param("   ", id="spaces"),
        pytest.param("\t", id="tab"),
        pytest.param("\n", id="newline"),
        pytest.param(" \t\r\n ", id="mixed-whitespace"),
    ],
)
def test_criterion_score_rejects_blank_evidence_at_storage_layer(conn, evidence):
    """
    铁律 4 由存储层强制：这里是**直接执行 INSERT**，完全绕过任何应用层校验，
    照样必须被拒。纯制表符/换行那几个参数是 trim 字符集的守护——单参 trim()
    只剥空格，写成 trim(evidence_ref) 的话这几条会通过，铁律 4 就有了缺口。
    """
    _insert_run(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO criterion_score (id, analysis_run_id, criterion_key, score, evidence_ref) "
            "VALUES ('cs-blank', 'run-1', 'embedded_c', 4.0, ?)",
            (evidence,),
        )
        conn.commit()
    conn.rollback()


def test_criterion_score_requires_existing_analysis_run(conn):
    """评分与调用快照双向可追溯：外键保证不会出现指向空气的评分项。"""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO criterion_score (id, analysis_run_id, criterion_key, score, evidence_ref) "
            "VALUES ('cs-orphan', 'no-such-run', 'embedded_c', 4.0, 'resume:x#1-2')"
        )
        conn.commit()
    conn.rollback()


def test_criterion_score_is_reachable_from_its_analysis_run(conn):
    _insert_run(conn, "run-join", application_id="app-9")
    conn.execute(
        "INSERT INTO criterion_score (id, analysis_run_id, criterion_key, score, evidence_ref) "
        "VALUES ('cs-join', 'run-join', 'autosar', 3.0, 'resume:cand-9#4-40')"
    )
    conn.commit()

    row = conn.execute(
        "SELECT r.application_id, s.criterion_key FROM criterion_score s "
        "JOIN analysis_run r ON r.id = s.analysis_run_id WHERE s.id='cs-join'"
    ).fetchone()
    assert row == ("app-9", "autosar")


def test_criterion_score_has_run_index(conn):
    indexes = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }
    assert "idx_criterion_score_run" in indexes


# ── pending_approval ────────────────────────────────────────────────────


def _enqueue(conn: sqlite3.Connection, row_id: str, content_hash: str, **overrides) -> None:
    row = {
        "id": row_id,
        "thread_id": "job-1",
        "message_type": "rejection_letter",
        "recipient": "cand-7",
        "payload_json": '{"body": "..."}',
        "blocked_reason": "等待人工确认",
        "content_hash": content_hash,
    }
    row.update(overrides)
    conn.execute(
        "INSERT INTO pending_approval (id, thread_id, message_type, recipient, "
        "payload_json, blocked_reason, content_hash) "
        "VALUES (:id, :thread_id, :message_type, :recipient, :payload_json, "
        ":blocked_reason, :content_hash)",
        row,
    )
    conn.commit()


def test_pending_approval_defaults_to_pending(conn):
    _enqueue(conn, "pa-1", "hash-1")

    row = conn.execute(
        "SELECT status, confirmed_by, resolved_at, enqueued_at FROM pending_approval WHERE id='pa-1'"
    ).fetchone()
    assert row[0] == "pending"
    assert row[1] is None
    assert row[2] is None
    assert row[3] is not None


@pytest.mark.parametrize("status", ["pending", "approved", "abandoned"])
def test_pending_approval_accepts_the_three_legal_states(conn, status):
    _enqueue(conn, f"pa-{status}", f"hash-{status}")
    conn.execute("UPDATE pending_approval SET status=? WHERE id=?", (status, f"pa-{status}"))
    conn.commit()

    assert conn.execute(
        "SELECT status FROM pending_approval WHERE id=?", (f"pa-{status}",)
    ).fetchone()[0] == status


@pytest.mark.parametrize("status", ["sent", "PENDING", "", "deleted"])
def test_pending_approval_rejects_illegal_status(conn, status):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO pending_approval (id, thread_id, message_type, recipient, "
            "payload_json, blocked_reason, content_hash, status) "
            "VALUES ('pa-bad', 'job-1', 'rejection_letter', 'cand-7', '{}', 'x', 'h-bad', ?)",
            (status,),
        )
        conn.commit()
    conn.rollback()


def test_pending_approval_rejects_duplicate_content_in_same_thread(conn):
    """重复入队的第二道防线（第一道是 U5 的 idempotent_effect）。"""
    _enqueue(conn, "pa-a", "same-hash")
    with pytest.raises(sqlite3.IntegrityError):
        _enqueue(conn, "pa-b", "same-hash")
    conn.rollback()


def test_pending_approval_allows_same_content_in_different_threads(conn):
    """
    唯一索引按 (thread_id, content_hash)，不是单列 content_hash——粒度与 U5 的
    幂等键 {thread_id}:effect_enqueue_pending_approval:{content_hash} 一致。
    单列唯一会让两个不同 thread 的同内容草稿撞 IntegrityError，把"拦下来排队"
    变成异常。
    """
    _enqueue(conn, "pa-t1", "same-hash", thread_id="job-1")
    _enqueue(conn, "pa-t2", "same-hash", thread_id="job-2")

    assert conn.execute(
        "SELECT count(*) FROM pending_approval WHERE content_hash='same-hash'"
    ).fetchone()[0] == 2


def test_pending_approval_accepts_malformed_draft_with_unknown_type_and_recipient(conn):
    """
    fail-closed 的一部分：草稿被拦下的常见原因正是这些字段缺失。message_type
    或 recipient 设成 NOT NULL，就会把"拦下一条畸形消息"变成 IntegrityError，
    异常穿透到调用方 → 一个 except 就是 fail-open。
    """
    _enqueue(conn, "pa-weird", "hash-weird", message_type=None, recipient=None)

    row = conn.execute(
        "SELECT message_type, recipient, status, blocked_reason FROM pending_approval WHERE id='pa-weird'"
    ).fetchone()
    assert row[0] is None
    assert row[1] is None
    assert row[2] == "pending"
    assert row[3] == "等待人工确认"


def test_pending_approval_notnull_set(conn):
    assert _notnull_columns(conn, "pending_approval") == {
        "id",
        "thread_id",
        "payload_json",
        "blocked_reason",
        "content_hash",
        "status",
        "enqueued_at",
    }


def test_pending_approval_has_status_index(conn):
    indexes = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }
    assert "idx_pending_approval_status" in indexes


def test_pending_approval_is_not_outbox(conn):
    """design D5：两张表各自独立，读错表必须是 no such table/column 级的显性错误。"""
    outbox_columns = {row[1] for row in conn.execute("PRAGMA table_info(outbox)")}
    assert "status" not in outbox_columns
    assert "confirmed_by" not in outbox_columns
