"""scripts/run_interview_live_sync.py：轮询批处理入口。数据库用真实 SQLite
（tmp_path），两机客户端用手写 Fake（与 tests/test_live_session_nodes.py 的
FakeVoiceHostClient 同一形状，本文件独立不 import 它，避免测试间耦合）。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.run_interview_live_sync import _sessions_ready_for_live_sync, run_loop, run_once
from app.schemas.live_turn_event import LiveEventsPollResponse
from app.storage.db import get_connection, init_schema


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(str(tmp_path / "test.db"))
    init_schema(connection)
    return connection


def _seed_session(conn, *, session_id, status="in_progress", phone_verified=True, consents=2):
    conn.execute("INSERT INTO job (id, title) VALUES ('job-1', 't') ON CONFLICT(id) DO NOTHING")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三') ON CONFLICT(id) DO NOTHING")
    conn.execute(
        f"INSERT OR IGNORE INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        f"VALUES ('resume-{session_id}', 'job-1', 'synthetic', 'a.pdf', '{session_id}hash', 'tester')"
    )
    conn.execute(
        f"INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        f"VALUES ('app-{session_id}', 'cand-1', 'job-1', 'resume-{session_id}', 'initial')"
    )
    conn.execute(
        "INSERT INTO analysis_run (id, configured_model, prompt_version, temperature, input_hash, raw_response) "
        f"VALUES ('run-{session_id}', 'deepseek-chat', 'v1', 0, 'h', 'r')"
    )
    conn.execute(
        f"INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id, status) "
        f"VALUES ('snap-{session_id}', 'app-{session_id}', 1, 1, 'run-{session_id}', 'frozen')"
    )
    conn.execute(
        "INSERT INTO prep_question (id, snapshot_id, seq, dimension, difficulty, text, rubric_json, "
        "follow_ups_json, rationale, origin) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (f"q-{session_id}", f"snap-{session_id}", 1, "tech", "medium", "Test question?",
         "[]", "[]", "Test rationale", "ai")
    )
    phone_verified_at = "datetime('now')" if phone_verified else "NULL"
    conn.execute(
        f"INSERT INTO interview_session (id, application_id, prep_snapshot_version, phone_verified_at, "
        f"retention_until, retention_policy_version, sample_class, status) "
        f"VALUES ('{session_id}', 'app-{session_id}', 1, {phone_verified_at}, "
        f"'2099-01-01', 'v1', 'internal_sim', '{status}')"
    )
    for i in range(consents):
        kind = "ai_interview" if i == 0 else "identity_check"
        conn.execute(
            "INSERT INTO interview_consent (session_id, kind, result, consent_version) VALUES (?, ?, 'accepted', 'v1')",
            (session_id, kind),
        )
    conn.commit()


def test_ready_sessions_requires_status_phone_and_both_consents(conn):
    _seed_session(conn, session_id="ready")
    _seed_session(conn, session_id="not_verified", phone_verified=False)
    _seed_session(conn, session_id="one_consent", consents=1)
    assert _sessions_ready_for_live_sync(conn) == ["ready"]


class FakeClient:
    def __init__(self):
        self.created = []

    def create_session(self, bundle):
        self.created.append(bundle.session_id)
        return {"accepted": True}

    def poll_events(self, session_id, *, since):
        return LiveEventsPollResponse(events=[], session_status="in_progress")

    def fetch_recording(self, session_id):
        raise AssertionError("不应该在 in_progress 阶段拉录音")

    def notify_delete_artifacts(self, session_id):
        raise AssertionError("不应该在 in_progress 阶段通知删除")


def test_run_once_processes_all_ready_sessions(conn, tmp_path):
    _seed_session(conn, session_id="s1")
    _seed_session(conn, session_id="s2")
    client = FakeClient()

    processed = run_once(conn, client=client, recording_dir=str(tmp_path))

    assert processed == 2
    assert set(client.created) == {"s1", "s2"}


def test_run_once_swallows_per_session_exception_and_continues(conn, tmp_path, monkeypatch):
    _seed_session(conn, session_id="s1")
    _seed_session(conn, session_id="s2")

    import scripts.run_interview_live_sync as mod

    calls = []

    def _boom(*args, **kwargs):
        calls.append(kwargs.get("session_id"))
        if kwargs.get("session_id") == "s1":
            raise RuntimeError("模拟出站请求超时")
        return "in_progress"

    monkeypatch.setattr(mod, "run_live_session_sync", _boom)
    processed = run_once(conn, client=FakeClient(), recording_dir=str(tmp_path))

    assert processed == 2
    assert calls == ["s1", "s2"]


def test_run_loop_stops_after_max_iterations(conn, tmp_path, monkeypatch):
    import scripts.run_interview_live_sync as mod

    call_count = {"n": 0}

    def _fake_run_once(*args, **kwargs):
        call_count["n"] += 1
        return 0

    monkeypatch.setattr(mod, "run_once", _fake_run_once)
    run_loop(conn, client=FakeClient(), recording_dir=str(tmp_path), interval=0.0, max_iterations=3)

    assert call_count["n"] == 3
