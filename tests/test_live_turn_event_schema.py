"""app/schemas/live_turn_event.py：GET /sessions/{id}/events 的响应体 schema。"""
import pytest
from pydantic import ValidationError

from app.schemas.live_turn_event import LiveEventsPollResponse, LiveTurnEvent


def test_minimal_voice_event_parses():
    event = LiveTurnEvent(
        seq=1, question_id="q1", question_text="讲讲你的项目", answer_text="做过三年",
        answer_mode="voice", audio_start_ms=0, audio_end_ms=4200, asr_confidence=0.92,
        latency={"endpoint_detection_ms": 300.0, "asr_ms": 150.0, "tts_first_frame_ms": 80.0, "end_to_end_ms": 530.0},
    )
    assert event.follow_up_of_seq is None
    assert event.interrupted_at_ms is None


def test_unknown_answer_mode_is_rejected():
    with pytest.raises(ValidationError):
        LiveTurnEvent(
            seq=1, question_id="q1", question_text="t", answer_text="a", answer_mode="video",
        )


def test_poll_response_requires_known_session_status():
    with pytest.raises(ValidationError):
        LiveEventsPollResponse(events=[], session_status="abandoned")


def test_poll_response_round_trips():
    payload = LiveEventsPollResponse(
        events=[
            LiveTurnEvent(seq=1, question_id="q1", question_text="t", answer_text="a", answer_mode="text"),
        ],
        session_status="in_progress",
    )
    restored = LiveEventsPollResponse.model_validate_json(payload.model_dump_json())
    assert restored == payload
