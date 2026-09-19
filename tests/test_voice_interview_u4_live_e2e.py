"""U4 live e2e（内部模拟场次，Fake 适配器）。串联：U3 邀约与同意（已交付）
→ compute_session_bundle → 语音主机 worker.run_session（含 1 次打断 1 次
追问）→ .51 侧 run_live_session_sync 轮询拉取 → 断言每维可回指、录音落盘、
语音主机无残留、`rejection_record`/`application.current_stage_id` 不变
（合规红线）。"""
import json

import pytest

from app.graph.invite_nodes import (
    compute_new_invite_token,
    compute_new_session,
    effect_create_interview_session,
    effect_issue_invite,
    effect_record_consent,
    issue_verification_code,
    open_invite,
    verify_phone_code,
)
from app.graph.live_session_nodes import compute_session_bundle, run_live_session_sync
from app.llm.gateway import LLMGateway
from app.storage.db import get_connection, init_schema

from voice_host import queue_store as vh_queue_store
from voice_host.adapters import FakeASRAdapter, FakeTTSAdapter, TranscriptResult
from voice_host.recording import FakeRecordingAdapter
from voice_host.worker import run_session as vh_run_session


class ScriptedClient:
    def __init__(self, bodies: list[str]):
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
            prompt_tokens = 10
            completion_tokens = 10

        class _Resp:
            choices = [_Choice()]
            model = "deepseek-chat-241226"
            system_fingerprint = "fp_1"
            usage = _Usage()

        return _Resp()


def _gateway(bodies):
    return LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=ScriptedClient(bodies),
    )


class InProcessBridgeClient:
    """把 `.51` 侧的 `VoiceHostClient` 接口直接接到语音主机的
    `voice_host.queue_store`，跳过真实 HTTP/HMAC（本 Task 的范围声明）。
    `create_session` 在这个桥接里直接触发 `voice_host.worker.run_session`
    跑完整场次——真实部署中这一步是异步的（POST /sessions 立即返回、worker
    在后台跑），e2e 用同步调用简化时序，不影响所验证的编排逻辑正确性。
    """

    def __init__(self, *, vh_conn, gateway, tts, asr, recorder):
        self.vh_conn = vh_conn
        self.gateway = gateway
        self.tts = tts
        self.asr = asr
        self.recorder = recorder

    def create_session(self, bundle):
        vh_queue_store.open_session(self.vh_conn, session_id=bundle.session_id, bundle_json=bundle.model_dump_json())
        vh_run_session(
            conn=self.vh_conn, bundle=bundle, gateway=self.gateway,
            tts=self.tts, asr=self.asr, recorder=self.recorder,
        )
        return {"accepted": True}

    def poll_events(self, session_id, *, since):
        from app.schemas.live_turn_event import LiveEventsPollResponse, LiveTurnEvent

        events = vh_queue_store.events_since(self.vh_conn, session_id=session_id, since_seq=since)
        status = vh_queue_store.get_session_status(self.vh_conn, session_id=session_id)
        return LiveEventsPollResponse(
            events=[LiveTurnEvent(**e) for e in events], session_status=status,
        )

    def fetch_recording(self, session_id):
        info = vh_queue_store.get_recording_file(self.vh_conn, session_id=session_id)
        with open(info["path"], "rb") as fh:
            content = fh.read()
        return content, info["sha256"]

    def notify_delete_artifacts(self, session_id):
        from pathlib import Path

        info = vh_queue_store.get_recording_file(self.vh_conn, session_id=session_id)
        if info is not None:
            Path(info["path"]).unlink(missing_ok=True)
            vh_queue_store.mark_recording_transferred(self.vh_conn, session_id=session_id)


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(str(tmp_path / "main.db"))
    init_schema(connection)
    return connection


@pytest.fixture
def vh_conn(tmp_path):
    connection = vh_queue_store.get_connection(str(tmp_path / "voice_host_queue.db"))
    vh_queue_store.init_schema(connection)
    return connection


def _seed_frozen_snapshot(conn, *, job_id="job-1", application_id="app-1"):
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
        "INSERT INTO analysis_run (id, configured_model, prompt_version, temperature, input_hash, raw_response) "
        "VALUES ('run-prep', 'deepseek-chat', 'interview-prep-v1', 0, 'h', 'r')"
    )
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id, status) "
        "VALUES ('snap-1', ?, 1, 1, 'run-prep', 'frozen')", (application_id,),
    )
    questions = [
        ("q1", 1, "AUTOSAR CP", "讲讲你的项目经验", '["能展开讲讲分层设计吗"]'),
        ("q2", 2, "C 语言", "讲讲内存管理", "[]"),
        ("q3", 3, "沟通表达", "怎么跟团队协作", "[]"),
        ("q4", 4, "调试能力", "讲个排查过的疑难 bug", "[]"),
        ("q5", 5, "职业规划", "未来三年打算", "[]"),
    ]
    for qid, seq, dim, text, follow_ups_json in questions:
        conn.execute(
            "INSERT INTO prep_question (id, snapshot_id, seq, dimension, difficulty, text, "
            "rubric_json, follow_ups_json, rationale) VALUES (?, 'snap-1', ?, ?, 'medium', ?, '{}', ?, 'r')",
            (qid, seq, dim, text, follow_ups_json),
        )
    conn.commit()
    return application_id


def _issue_and_ready_session(conn, *, application_id, job_id="job-1"):
    session = compute_new_session(conn, application_id=application_id, prep_snapshot_version=1, sample_class="internal_sim")
    session_id = effect_create_interview_session(
        conn, thread_id=session["id"], business_key="create", session=session,
    )
    token, token_hash, expires_at = compute_new_invite_token(conn, job_id=job_id)
    effect_issue_invite(
        conn, thread_id=session_id, business_key=token_hash,
        session_id=session_id, token_hash=token_hash, expires_at=expires_at,
    )
    open_invite(conn, token)

    effect_record_consent(
        conn, thread_id=session_id, business_key="ai_interview:v1",
        session_id=session_id, kind="ai_interview", result="accepted", version="v1",
    )
    effect_record_consent(
        conn, thread_id=session_id, business_key="identity_check:v1",
        session_id=session_id, kind="identity_check", result="accepted", version="v1",
    )

    code = issue_verification_code(conn, session_id=session_id)
    verify_phone_code(conn, session_id=session_id, submitted_code=code)

    return session_id


def test_live_e2e_internal_simulation_with_interrupt_and_follow_up(conn, vh_conn, tmp_path):
    application_id = _seed_frozen_snapshot(conn)
    session_id = _issue_and_ready_session(conn, application_id=application_id)

    bundle = compute_session_bundle(conn, session_id=session_id)
    assert len(bundle.questions) == 5

    gateway = _gateway([json.dumps({"decision": "follow_up", "follow_up_index": 0}, ensure_ascii=False)])
    tts = FakeTTSAdapter(first_frame_ms_sequence=[80])
    asr = FakeASRAdapter(results=[
        TranscriptResult(text="做过三年分层开发", confidence=0.9, endpoint_detection_ms=300, asr_ms=150, interrupted_offset_ms=180),  # 题1：含打断
        TranscriptResult(text="分层设计细节回答", confidence=0.85, endpoint_detection_ms=280, asr_ms=140),  # 题1追问
        TranscriptResult(text="内存管理回答", confidence=0.92, endpoint_detection_ms=310, asr_ms=160),  # 题2
        TranscriptResult(text="团队协作回答", confidence=0.88, endpoint_detection_ms=290, asr_ms=145),  # 题3
        TranscriptResult(text="调试经历回答", confidence=0.91, endpoint_detection_ms=305, asr_ms=155),  # 题4
        TranscriptResult(text="职业规划回答", confidence=0.87, endpoint_detection_ms=295, asr_ms=148),  # 题5
    ])
    recorder = FakeRecordingAdapter(data_dir=tmp_path / "vh_recordings")

    client = InProcessBridgeClient(vh_conn=vh_conn, gateway=gateway, tts=tts, asr=asr, recorder=recorder)

    result = run_live_session_sync(
        conn, session_id=session_id, client=client, recording_dir=str(tmp_path / "main_recordings"),
    )

    assert result == "completed"

    turns = conn.execute(
        "SELECT id, seq, interrupted_at_ms, follow_up_of, answer_mode FROM interview_turn "
        "WHERE session_id = ? ORDER BY seq", (session_id,),
    ).fetchall()
    assert [t[1] for t in turns] == [1, 2, 3, 4, 5, 6]
    assert turns[0][2] == 180  # 第一题被打断，截断点被记录
    assert turns[1][3] == turns[0][0]  # 第二条 turn 是追问，follow_up_of 精确指向第一题的 id
    assert all(t[3] is None for t in turns if t != turns[1])  # 其余 5 条不是追问
    assert all(t[4] == "voice" for t in turns)

    session_row = conn.execute(
        "SELECT status, recording_uri, recording_sha256 FROM interview_session WHERE id = ?", (session_id,)
    ).fetchone()
    assert session_row[0] == "completed"
    assert session_row[1] is not None
    assert session_row[2] is not None

    # 语音主机无残留（interview-recording-retention spec「语音主机不留副本」）
    vh_recording_info = vh_queue_store.get_recording_file(vh_conn, session_id=session_id)
    assert vh_recording_info["transferred"] is True
    import os
    assert not os.path.exists(vh_recording_info["path"])

    # 合规红线：本单元不触发淘汰、不改动阶段流转
    stage = conn.execute("SELECT current_stage_id FROM application WHERE id = ?", (application_id,)).fetchone()[0]
    assert stage == "initial"
    rejection_count = conn.execute("SELECT COUNT(*) FROM rejection_record").fetchone()[0]
    assert rejection_count == 0
