"""面试域 AI 调用输入 schema 的字段白名单反射测试（tasks 2.7）。

判据是结构性的：扫描每个模型的 model_fields，字段名不得命中任何一个禁止
子串（身份/核验/声学关键词）。这条测试守护的不是"今天没传错值"，而是
"这几个模型的字段集合本身就不可能长出这些字段"——加字段的人会在这里被
直接拦下来，而不是等到评分输出里混进声学噪声才被发现。
"""
import pytest
from pydantic import ValidationError

from app.schemas.interview_ai_input import (
    FollowUpInput,
    PrepInput,
    ResumeScoreItem,
    ScoreInput,
    ScoreInputTurn,
)

_FORBIDDEN_SUBSTRINGS = (
    "name", "phone", "candidate", "identity", "verified", "verification",
    "acoustic", "face", "image", "photo", "audio", "biometric",
)

_ALL_MODELS = (PrepInput, ResumeScoreItem, FollowUpInput, ScoreInput, ScoreInputTurn)


@pytest.mark.parametrize("model", _ALL_MODELS)
def test_ai_input_schema_has_no_forbidden_fields(model):
    for field_name in model.model_fields:
        lowered = field_name.lower()
        hit = [bad for bad in _FORBIDDEN_SUBSTRINGS if bad in lowered]
        assert not hit, f"{model.__name__}.{field_name} 命中禁止字段模式 {hit}"


def test_prep_input_accepts_well_formed_payload():
    p = PrepInput(
        profile={"job_title": "底层软件工程师", "diag_stack": ["UDS", "CAN"]},
        rubric_dimensions=["diag_stack", "toolchain"],
        resume_scores=[
            ResumeScoreItem(criterion_key="diag_stack", score=3.0, evidence_excerpt="曾用 UDS 协议调试"),
        ],
    )
    assert p.rubric_dimensions == ["diag_stack", "toolchain"]
    assert p.resume_scores[0].criterion_key == "diag_stack"


def test_prep_input_allows_empty_resume_scores():
    """spec「简历评分尚未完成」场景：只按画像生成通用题目。"""
    p = PrepInput(profile={"job_title": "底层软件工程师"}, rubric_dimensions=["diag_stack"])
    assert p.resume_scores == []


def test_prep_input_forbids_extra_fields():
    with pytest.raises(ValidationError):
        PrepInput(
            profile={}, rubric_dimensions=["x"], resume_scores=[],
            candidate_name="张三",  # type: ignore[call-arg]
        )


def test_follow_up_input_accepts_well_formed_payload():
    f = FollowUpInput(
        question_text="请描述一次你排查 CAN 总线故障的经历",
        follow_ups=["具体用了哪些诊断工具？", "故障最终怎么定位到的？"],
        transcript="我用示波器抓了波形，后来发现是终端电阻的问题",
    )
    assert len(f.follow_ups) == 2


def test_follow_up_input_forbids_extra_fields():
    with pytest.raises(ValidationError):
        FollowUpInput(
            question_text="q", follow_ups=["a"], transcript="t",
            phone_verified=True,  # type: ignore[call-arg]
        )


def test_score_input_accepts_well_formed_payload():
    s = ScoreInput(
        rubric_dimensions=["diag_stack"],
        turns=[
            ScoreInputTurn(turn_id="turn-1", seq=1, question_text="问题", answer_text="回答"),
        ],
    )
    assert s.turns[0].turn_id == "turn-1"


def test_score_input_turn_has_no_acoustic_or_audio_fields():
    """合规红线「声学信号只展示不计分」在评分输入契约层的结构性保证：
    interview_turn 表里的 asr_confidence/acoustic_ref/audio_start_ms/
    audio_end_ms 一个都不出现在 ScoreInputTurn 里。"""
    forbidden = {"asr_confidence", "acoustic_ref", "audio_start_ms", "audio_end_ms"}
    assert not (forbidden & set(ScoreInputTurn.model_fields))


def test_score_input_forbids_extra_fields():
    with pytest.raises(ValidationError):
        ScoreInputTurn(
            turn_id="t1", seq=1, question_text="q", answer_text="a",
            asr_confidence=0.9,  # type: ignore[call-arg]
        )
