"""voice_host/worker.py：语音主机 agents worker 核心编排。LLM 用脚本化假
客户端（追问选择），ASR/TTS 用 Fake 适配器，事件队列用真实 voice_host.
queue_store（tmp_path 下的独立 SQLite）。"""
import json

import pytest

from app.llm.gateway import LLMGateway
from app.schemas.session_bundle import SessionBundle, SessionBundleQuestion
from voice_host import queue_store
from voice_host.adapters import FakeASRAdapter, FakeTTSAdapter, TranscriptResult
from voice_host.recording import FakeRecordingAdapter, RecordingStartFailed
from voice_host.worker import run_session


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


@pytest.fixture
def conn(tmp_path):
    connection = queue_store.get_connection(str(tmp_path / "queue.db"))
    queue_store.init_schema(connection)
    return connection


def _bundle():
    return SessionBundle(
        session_id="s1", prep_curve="easy_to_hard", follow_up_limit=2,
        questions=[
            SessionBundleQuestion(question_id="q1", seq=1, text="讲讲你的 AUTOSAR 项目", follow_ups=["具体分层设计是怎么做的"]),
            SessionBundleQuestion(question_id="q2", seq=2, text="怎么跟团队协作", follow_ups=[]),
        ],
    )


def test_run_session_no_follow_up_two_questions(conn, tmp_path):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    next_question_body = json.dumps({"decision": "next_question"}, ensure_ascii=False)
    gateway = _gateway([next_question_body])

    tts = FakeTTSAdapter(first_frame_ms_sequence=[80])
    asr = FakeASRAdapter(results=[
        TranscriptResult(text="做过三年分层开发", confidence=0.9, endpoint_detection_ms=300, asr_ms=150),
        TranscriptResult(text="每周同步进度", confidence=0.85, endpoint_detection_ms=280, asr_ms=140),
    ])

    status = run_session(
        conn=conn, bundle=_bundle(), gateway=gateway, tts=tts, asr=asr,
        recorder=FakeRecordingAdapter(data_dir=tmp_path),
    )

    assert status == "completed"
    events = queue_store.events_since(conn, session_id="s1", since_seq=0)
    assert [e["seq"] for e in events] == [1, 2]
    assert events[0]["question_id"] == "q1"
    assert events[1]["question_id"] == "q2"
    assert queue_store.get_session_status(conn, session_id="s1") == "completed"


def test_run_session_triggers_one_follow_up(conn, tmp_path):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    follow_up_body = json.dumps({"decision": "follow_up", "follow_up_index": 0}, ensure_ascii=False)
    next_question_body = json.dumps({"decision": "next_question"}, ensure_ascii=False)
    gateway = _gateway([follow_up_body])  # 第 2 题没有预埋追问，不会再调网关

    tts = FakeTTSAdapter(first_frame_ms_sequence=[80])
    asr = FakeASRAdapter(results=[
        TranscriptResult(text="做过三年", confidence=0.9, endpoint_detection_ms=300, asr_ms=150),  # 题1
        TranscriptResult(text="分层设计是这样的", confidence=0.88, endpoint_detection_ms=290, asr_ms=145),  # 追问
        TranscriptResult(text="每周同步", confidence=0.85, endpoint_detection_ms=280, asr_ms=140),  # 题2
    ])

    status = run_session(
        conn=conn, bundle=_bundle(), gateway=gateway, tts=tts, asr=asr,
        recorder=FakeRecordingAdapter(data_dir=tmp_path),
    )

    assert status == "completed"
    events = queue_store.events_since(conn, session_id="s1", since_seq=0)
    assert [e["seq"] for e in events] == [1, 2, 3]
    assert events[1]["question_text"] == "具体分层设计是怎么做的"
    assert events[1]["follow_up_of_seq"] == 1
    assert events[2]["question_id"] == "q2"  # 追问后正确推进到下一题


def test_run_session_records_interrupted_offset(conn, tmp_path):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    gateway = _gateway([json.dumps({"decision": "next_question"}, ensure_ascii=False)])
    tts = FakeTTSAdapter(first_frame_ms_sequence=[80])
    asr = FakeASRAdapter(results=[
        TranscriptResult(text="被打断前的话", confidence=0.7, endpoint_detection_ms=200, asr_ms=100, interrupted_offset_ms=180),
        TranscriptResult(text="正常回答", confidence=0.9, endpoint_detection_ms=300, asr_ms=150),
    ])

    run_session(
        conn=conn, bundle=_bundle(), gateway=gateway, tts=tts, asr=asr,
        recorder=FakeRecordingAdapter(data_dir=tmp_path),
    )

    events = queue_store.events_since(conn, session_id="s1", since_seq=0)
    assert events[0]["interrupted_at_ms"] == 180


def test_run_session_falls_back_to_next_question_on_schema_extraction_failure(conn, tmp_path):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    # 三次都返回非法 JSON，网关重试耗尽抛 SchemaExtractionFailed——worker 必须
    # 兜底成"进入下一题"，不能让整场面试卡死（本计划「设计决策 11」）。
    gateway = _gateway(["not json", "still not json", "nope"])
    tts = FakeTTSAdapter(first_frame_ms_sequence=[80])
    asr = FakeASRAdapter(results=[
        TranscriptResult(text="做过三年", confidence=0.9, endpoint_detection_ms=300, asr_ms=150),
        TranscriptResult(text="每周同步", confidence=0.85, endpoint_detection_ms=280, asr_ms=140),
    ])

    status = run_session(
        conn=conn, bundle=_bundle(), gateway=gateway, tts=tts, asr=asr,
        recorder=FakeRecordingAdapter(data_dir=tmp_path),
    )

    assert status == "completed"
    events = queue_store.events_since(conn, session_id="s1", since_seq=0)
    assert [e["seq"] for e in events] == [1, 2]  # 没有触发追问，直接进入下一题


def test_run_session_replays_question_without_counting_as_follow_up(conn, tmp_path):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    gateway = _gateway([json.dumps({"decision": "next_question"}, ensure_ascii=False)])
    tts = FakeTTSAdapter(first_frame_ms_sequence=[80])
    asr = FakeASRAdapter(results=[
        TranscriptResult(text="", confidence=0.0, endpoint_detection_ms=0, asr_ms=0, replay_requested=True),  # 候选人说"再说一遍"
        TranscriptResult(text="做过三年", confidence=0.9, endpoint_detection_ms=300, asr_ms=150),  # 重播后的真实回答
        TranscriptResult(text="每周同步", confidence=0.85, endpoint_detection_ms=280, asr_ms=140),
    ])

    status = run_session(
        conn=conn, bundle=_bundle(), gateway=gateway, tts=tts, asr=asr,
        recorder=FakeRecordingAdapter(data_dir=tmp_path),
    )

    assert status == "completed"
    # 重听不产生新 turn、不计入追问：仍然只有 2 条 turn（两道题各一条）
    events = queue_store.events_since(conn, session_id="s1", since_seq=0)
    assert [e["seq"] for e in events] == [1, 2]
    assert events[0]["answer_text"] == "做过三年"
    # 题目被播报了两次（首次 + 重听一次）
    assert tts.played.count("讲讲你的 AUTOSAR 项目") == 2


def test_run_session_gives_up_after_max_replay_attempts(conn, tmp_path):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    gateway = _gateway([json.dumps({"decision": "next_question"}, ensure_ascii=False)])
    tts = FakeTTSAdapter(first_frame_ms_sequence=[80])
    # 连续 3 次重听请求耗尽 MAX_REPLAY_ATTEMPTS 上限，第 4 次调用返回真实回答
    # 也会被当作最终答案采纳（防御性上限，本计划「设计决策 15」）。
    asr = FakeASRAdapter(results=[
        TranscriptResult(text="", confidence=0.0, endpoint_detection_ms=0, asr_ms=0, replay_requested=True),
        TranscriptResult(text="", confidence=0.0, endpoint_detection_ms=0, asr_ms=0, replay_requested=True),
        TranscriptResult(text="", confidence=0.0, endpoint_detection_ms=0, asr_ms=0, replay_requested=True),
        TranscriptResult(text="仍然是重听请求但已达上限直接采纳", confidence=0.5, endpoint_detection_ms=100, asr_ms=50, replay_requested=True),
        TranscriptResult(text="每周同步", confidence=0.85, endpoint_detection_ms=280, asr_ms=140),
    ])

    status = run_session(
        conn=conn, bundle=_bundle(), gateway=gateway, tts=tts, asr=asr,
        recorder=FakeRecordingAdapter(data_dir=tmp_path),
    )

    assert status == "completed"
    assert tts.played.count("讲讲你的 AUTOSAR 项目") == 4  # 1 次首播 + 3 次重听
    events = queue_store.events_since(conn, session_id="s1", since_seq=0)
    assert events[0]["answer_text"] == "仍然是重听请求但已达上限直接采纳"


def test_run_session_finalizes_recording_on_success(conn, tmp_path):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    gateway = _gateway([json.dumps({"decision": "next_question"}, ensure_ascii=False)])
    tts = FakeTTSAdapter(first_frame_ms_sequence=[80])
    asr = FakeASRAdapter(results=[
        TranscriptResult(text="做过三年", confidence=0.9, endpoint_detection_ms=300, asr_ms=150),
        TranscriptResult(text="每周同步", confidence=0.85, endpoint_detection_ms=280, asr_ms=140),
    ])
    recorder = FakeRecordingAdapter(data_dir=tmp_path)

    status = run_session(conn=conn, bundle=_bundle(), gateway=gateway, tts=tts, asr=asr, recorder=recorder)

    assert status == "completed"
    assert recorder.started == ["s1"]
    info = queue_store.get_recording_file(conn, session_id="s1")
    assert info is not None


def test_run_session_marks_interrupted_when_recording_fails_to_start(conn, tmp_path):
    queue_store.open_session(conn, session_id="s1", bundle_json="{}")
    gateway = _gateway([])
    tts = FakeTTSAdapter()
    asr = FakeASRAdapter(results=[])
    recorder = FakeRecordingAdapter(data_dir=tmp_path, fail_on_start=True)

    status = run_session(conn=conn, bundle=_bundle(), gateway=gateway, tts=tts, asr=asr, recorder=recorder)

    assert status == "interrupted"
    assert queue_store.get_session_status(conn, session_id="s1") == "interrupted"
    assert queue_store.events_since(conn, session_id="s1", since_seq=0) == []
