"""app/schemas/session_bundle.py 的结构性测试：确保下发快照 schema 在字段
层面就不可能携带简历/候选人身份/评分字段（live-voice-interview-session spec
「简历数据不进语音主机」；design D19）。"""
import pytest
from pydantic import ValidationError

from app.schemas.session_bundle import SessionBundle, SessionBundleQuestion

FORBIDDEN_SUBSTRINGS = ("resume", "candidate", "name", "phone", "dimension", "difficulty", "rubric")


def _all_field_names() -> set[str]:
    names = set(SessionBundle.model_fields.keys())
    names |= set(SessionBundleQuestion.model_fields.keys())
    return names


def test_no_forbidden_field_names_anywhere_in_schema():
    for field_name in _all_field_names():
        for forbidden in FORBIDDEN_SUBSTRINGS:
            assert forbidden not in field_name.lower(), (
                f"字段 {field_name!r} 命中禁止子串 {forbidden!r}"
            )


def test_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        SessionBundle(
            session_id="s1", prep_curve="easy_to_hard", follow_up_limit=2,
            questions=[{"question_id": "q1", "seq": 1, "text": "讲讲你的项目"}],
            candidate_name="张三",
        )


def test_valid_bundle_round_trips_through_json():
    bundle = SessionBundle(
        session_id="s1", prep_curve="easy_to_hard", follow_up_limit=2,
        questions=[
            SessionBundleQuestion(question_id="q1", seq=1, text="讲讲你的项目", follow_ups=["能展开讲讲分层设计吗"]),
        ],
    )
    restored = SessionBundle.model_validate_json(bundle.model_dump_json())
    assert restored == bundle


def test_questions_must_not_be_empty():
    with pytest.raises(ValidationError):
        SessionBundle(session_id="s1", prep_curve="easy_to_hard", follow_up_limit=2, questions=[])
