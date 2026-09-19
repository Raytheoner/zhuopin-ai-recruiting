"""app/graph/live_session_nodes.py：U4 live 子图 L4 编排层。数据库用真实
SQLite（tmp_path），两机接口用手写 Fake 客户端（不依赖 httpx.MockTransport，
本文件只关心 live_session_nodes 自己怎么调用客户端，不重复 Task 4 已经测过
的签名/序列化细节）。"""
import json

import pytest

from app.graph.live_session_nodes import (
    compute_session_bundle,
    effect_close_session,
    effect_fetch_recording,
    effect_open_session,
    effect_persist_turn,
    run_live_session_sync,
)
from app.schemas.live_turn_event import LiveEventsPollResponse, LiveTurnEvent
from app.storage.db import get_connection, init_schema


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(str(tmp_path / "test.db"))
    init_schema(connection)
    return connection


def _seed_ready_session(conn, *, job_id="job-1", application_id="app-1", session_id="sess-1"):
    conn.execute("INSERT INTO job (id, title) VALUES (?, '嵌入式软件工程师')", (job_id,))
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('resume-1', ?, 'synthetic', 'a.pdf', 'hash', 'tester')", (job_id,),
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, 'cand-1', ?, 'resume-1', 'initial')", (application_id, job_id),
    )
    conn.execute(
        "INSERT INTO analysis_run (id, configured_model, prompt_version, temperature, "
        "input_hash, raw_response) VALUES ('run-prep', 'deepseek-chat', 'interview-prep-v1', 0, 'h', 'r')"
    )
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id, status) "
        "VALUES ('snap-1', ?, 1, 1, 'run-prep', 'frozen')", (application_id,),
    )
    conn.execute(
        "INSERT INTO prep_question (id, snapshot_id, seq, dimension, difficulty, text, "
        "rubric_json, follow_ups_json, rationale) VALUES "
        "('q1', 'snap-1', 1, 'AUTOSAR CP', 'easy', '讲讲你的项目', '{}', '[\"能展开讲讲分层设计吗\"]', 'r')"
    )
    conn.execute(
        "INSERT INTO interview_session (id, application_id, prep_snapshot_version, phone_verified_at, "
        "retention_until, retention_policy_version, sample_class, status) "
        "VALUES (?, ?, 1, datetime('now'), '2099-01-01', 'v1', 'internal_sim', 'in_progress')",
        (session_id, application_id),
    )
    conn.execute(
        "INSERT INTO interview_consent (session_id, kind, result, consent_version) VALUES "
        "(?, 'ai_interview', 'accepted', 'v1'), (?, 'identity_check', 'accepted', 'v1')",
        (session_id, session_id),
    )
    conn.commit()


def test_compute_session_bundle_excludes_scoring_fields(conn):
    _seed_ready_session(conn)
    bundle = compute_session_bundle(conn, session_id="sess-1")
    assert bundle.session_id == "sess-1"
    assert bundle.follow_up_limit == 2  # 默认值
    assert bundle.questions[0].question_id == "q1"
    assert bundle.questions[0].follow_ups == ["能展开讲讲分层设计吗"]
    assert "dimension" not in bundle.model_dump()["questions"][0]


class FakeVoiceHostClient:
    def __init__(self, *, poll_responses, recording_content=b"rec", recording_sha=None):
        import hashlib
        self.poll_responses = list(poll_responses)
        self.created_sessions = []
        self.deleted_artifacts = []
        self.recording_content = recording_content
        self.recording_sha = recording_sha or hashlib.sha256(recording_content).hexdigest()

    def create_session(self, bundle):
        self.created_sessions.append(bundle.session_id)
        return {"accepted": True}

    def poll_events(self, session_id, *, since):
        return self.poll_responses.pop(0)

    def fetch_recording(self, session_id):
        return self.recording_content, self.recording_sha

    def notify_delete_artifacts(self, session_id):
        self.deleted_artifacts.append(session_id)


def test_effect_open_session_is_idempotent_and_calls_client_once(conn):
    _seed_ready_session(conn)
    bundle = compute_session_bundle(conn, session_id="sess-1")
    client = FakeVoiceHostClient(poll_responses=[])

    effect_open_session(conn, thread_id="sess-1", business_key="1", session_id="sess-1", bundle=bundle, client=client)
    effect_open_session(conn, thread_id="sess-1", business_key="1", session_id="sess-1", bundle=bundle, client=client)

    assert client.created_sessions == ["sess-1"]  # 第二次调用被幂等短路
    count = conn.execute(
        "SELECT COUNT(*) FROM interview_live_event WHERE session_id = 'sess-1' AND event_type = 'opened'"
    ).fetchone()[0]
    assert count == 1


def test_effect_persist_turn_resolves_follow_up_of_seq_to_turn_id(conn):
    _seed_ready_session(conn)
    first = LiveTurnEvent(
        seq=1, question_id="q1", question_text="讲讲你的项目", answer_text="做过三年", answer_mode="voice",
        audio_start_ms=0, audio_end_ms=3000, asr_confidence=0.9,
    )
    effect_persist_turn(conn, thread_id="sess-1", business_key="1", session_id="sess-1", event=first)

    follow_up = LiveTurnEvent(
        seq=2, question_id="q1", question_text="能展开讲讲分层设计吗", answer_text="是这样分层的",
        answer_mode="voice", audio_start_ms=3200, audio_end_ms=6000, asr_confidence=0.85, follow_up_of_seq=1,
    )
    effect_persist_turn(conn, thread_id="sess-1", business_key="2", session_id="sess-1", event=follow_up)

    first_turn_id = conn.execute("SELECT id FROM interview_turn WHERE session_id='sess-1' AND seq=1").fetchone()[0]
    follow_up_of = conn.execute("SELECT follow_up_of FROM interview_turn WHERE session_id='sess-1' AND seq=2").fetchone()[0]
    assert follow_up_of == first_turn_id


def test_effect_persist_turn_is_idempotent_on_repeat_seq(conn):
    _seed_ready_session(conn)
    event = LiveTurnEvent(
        seq=1, question_id="q1", question_text="t", answer_text="a", answer_mode="text",
    )
    effect_persist_turn(conn, thread_id="sess-1", business_key="1", session_id="sess-1", event=event)
    effect_persist_turn(conn, thread_id="sess-1", business_key="1", session_id="sess-1", event=event)
    count = conn.execute("SELECT COUNT(*) FROM interview_turn WHERE session_id='sess-1' AND seq=1").fetchone()[0]
    assert count == 1


def test_effect_persist_turn_raises_when_follow_up_of_seq_not_found(conn):
    _seed_ready_session(conn)
    event = LiveTurnEvent(
        seq=1, question_id="q1", question_text="t", answer_text="a", answer_mode="text",
        follow_up_of_seq=99,  # no turn with seq=99 exists yet
    )
    with pytest.raises(ValueError):
        effect_persist_turn(conn, thread_id="sess-1", business_key="1", session_id="sess-1", event=event)


def test_effect_close_session_updates_status(conn):
    _seed_ready_session(conn)
    effect_close_session(conn, thread_id="sess-1", business_key="close", session_id="sess-1", final_status="completed")
    status = conn.execute("SELECT status FROM interview_session WHERE id='sess-1'").fetchone()[0]
    assert status == "completed"


def test_effect_fetch_recording_writes_file_and_notifies_delete(conn, tmp_path):
    _seed_ready_session(conn)
    client = FakeVoiceHostClient(poll_responses=[], recording_content=b"hello-recording")
    effect_fetch_recording(
        conn, thread_id="sess-1", business_key=client.recording_sha, session_id="sess-1",
        content=client.recording_content, recording_sha256=client.recording_sha,
        recording_dir=str(tmp_path / "recordings"), client=client,
    )
    row = conn.execute("SELECT recording_uri, recording_sha256 FROM interview_session WHERE id='sess-1'").fetchone()
    assert row[1] == client.recording_sha
    assert (tmp_path / "recordings" / "sess-1.rec").read_bytes() == b"hello-recording"
    assert client.deleted_artifacts == ["sess-1"]


def test_run_live_session_sync_full_cycle(conn, tmp_path):
    _seed_ready_session(conn)
    poll_first = LiveEventsPollResponse(
        events=[LiveTurnEvent(seq=1, question_id="q1", question_text="t", answer_text="a", answer_mode="text")],
        session_status="completed",
    )
    client = FakeVoiceHostClient(poll_responses=[poll_first])

    result = run_live_session_sync(
        conn, session_id="sess-1", client=client, recording_dir=str(tmp_path / "recordings"),
    )

    assert result == "completed"
    assert client.created_sessions == ["sess-1"]
    assert client.deleted_artifacts == ["sess-1"]
    turn_count = conn.execute("SELECT COUNT(*) FROM interview_turn WHERE session_id='sess-1'").fetchone()[0]
    assert turn_count == 1
    status = conn.execute("SELECT status FROM interview_session WHERE id='sess-1'").fetchone()[0]
    assert status == "completed"


def test_run_live_session_sync_skips_when_snapshot_expired(conn):
    _seed_ready_session(conn)
    conn.execute("UPDATE prep_snapshot SET status='expired' WHERE id='snap-1'")
    conn.commit()
    client = FakeVoiceHostClient(poll_responses=[])
    result = run_live_session_sync(conn, session_id="sess-1", client=client, recording_dir="unused")
    assert result == "blocked_snapshot_not_frozen"
    assert client.created_sessions == []
