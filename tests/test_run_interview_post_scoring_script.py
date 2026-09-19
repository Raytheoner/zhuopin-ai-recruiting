"""scripts/run_interview_post_scoring.py 的批处理选取与错误隔离测试。
LLM 用脚本化假客户端，数据库用真实 SQLite（tmp_path）。"""
import json

import pytest

from app.llm.gateway import LLMGateway
from app.storage.db import get_connection, init_schema
from scripts.run_interview_post_scoring import _due_sessions, run_batch


class _ScriptedClient:
    def __init__(self, bodies):
        self._bodies = list(bodies)
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        body = self._bodies.pop(0)

        class _Msg:
            content = body

        class _Choice:
            message = _Msg()

        class _Usage:
            prompt_tokens = 1
            completion_tokens = 1

        class _Resp:
            choices = [_Choice()]
            model = "deepseek-chat-241226"
            system_fingerprint = "fp_1"
            usage = _Usage()

        return _Resp()


class _RecordingHook:
    def __init__(self, conn):
        self._conn = conn
        self._seq = 0

    def record(self, **kwargs):
        self._seq += 1
        run_id = f"run-{self._seq}"
        audit_context = kwargs.get("audit_context") or {}
        self._conn.execute(
            "INSERT INTO analysis_run (id, application_id, job_id, configured_model, "
            "prompt_version, temperature, input_hash, raw_response, created_at) "
            "VALUES (?, ?, ?, 'deepseek-chat', ?, 0, 'hash', ?, datetime('now'))",
            (run_id, audit_context.get("application_id"), audit_context.get("job_id"),
             kwargs["prompt_version"], kwargs["raw_response"]),
        )
        self._conn.commit()
        return run_id


def _seed_session(conn, *, session_id, status, post_scoring_status, job_id="job-1", application_id="app-1"):
    conn.execute("INSERT OR IGNORE INTO job (id, title) VALUES (?, 'ECU 工程师')", (job_id,))
    conn.execute("INSERT OR IGNORE INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT OR IGNORE INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES (?, ?, 'synthetic', 'a.pdf', ?, 'tester')", (f"resume-{application_id}", job_id, f"hash-{application_id}"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, 'cand-1', ?, ?, 'initial')", (application_id, job_id, f"resume-{application_id}"),
    )
    conn.execute(
        "INSERT INTO interview_session (id, application_id, prep_snapshot_version, "
        "retention_until, retention_policy_version, sample_class, status, post_scoring_status) "
        "VALUES (?, ?, 1, '2099-01-01', 'v1', 'internal_sim', ?, ?)",
        (session_id, application_id, status, post_scoring_status),
    )
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(str(tmp_path / "test.db"))
    init_schema(connection)
    return connection


def test_due_sessions_selects_completed_and_pending_or_failed_retry_only(conn):
    _seed_session(conn, session_id="s1", status="completed", post_scoring_status="pending")
    _seed_session(conn, session_id="s2", status="completed", post_scoring_status="scored", application_id="app-2")
    _seed_session(conn, session_id="s3", status="in_progress", post_scoring_status="pending", application_id="app-3")
    _seed_session(conn, session_id="s4", status="completed", post_scoring_status="failed_retry", application_id="app-4")

    due = _due_sessions(conn)

    assert set(due) == {"s1", "s4"}


def test_run_batch_continues_after_one_session_raises(conn, monkeypatch):
    _seed_session(conn, session_id="s1", status="completed", post_scoring_status="pending")
    _seed_session(conn, session_id="s2", status="completed", post_scoring_status="pending", application_id="app-2")

    calls = []

    def _fake_run_post_scoring(connection, *, session_id, gateway):
        calls.append(session_id)
        if session_id == "s1":
            raise RuntimeError("模拟未预期异常")
        return "scored"

    monkeypatch.setattr("scripts.run_interview_post_scoring.run_post_scoring", _fake_run_post_scoring)

    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=_ScriptedClient([]), audit_hook=_RecordingHook(conn),
    )
    exit_code = run_batch(conn.execute("PRAGMA database_list").fetchone()[2], gateway)

    assert calls == ["s1", "s2"]  # s1 抛异常后照常处理 s2，不中断整批
    assert exit_code == 1  # 出现未预期异常，退出码非 0
