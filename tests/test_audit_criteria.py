"""
criterion_key 白名单。合规红线：声学情绪信号（语速/停顿/静默）只展示给面试官，
不进 criterion_score；人脸/表情类维度禁止出现在任何评分项中
（《人脸识别技术应用安全管理办法》2025-06-01 施行）。

⚠️ 本文件里的维度名**全部写成字面量**，⛔ 不从 CRITERION_KEY_WHITELIST 里派生。
从常量派生的测试会随常量一起变——把 "facial_expression" 加进白名单，派生式断言
跟着放宽、全绿，而红线已经破了。字面量是这条测试与常量之间唯一的独立支点。
"""

import pytest

from app.audit.criteria import (
    CRITERION_KEY_WHITELIST,
    ForbiddenCriterionKey,
    validate_criterion_key,
)
from app.audit.events import CriterionScore


@pytest.mark.parametrize(
    "key",
    ["speech_rate", "pause_duration", "silence_ratio", "speech_tempo", "voice_emotion"],
)
def test_acoustic_emotion_dimensions_are_rejected(key):
    """合规红线：声学情绪信号只展示给面试官，不进评分项。"""
    with pytest.raises(ForbiddenCriterionKey):
        validate_criterion_key(key)


@pytest.mark.parametrize(
    "key",
    ["facial_expression", "micro_expression", "face_match", "emotion_score", "gaze_stability"],
)
def test_biometric_dimensions_are_rejected(key):
    """合规红线：生物特征类维度（人脸、表情）禁止出现在任何评分项中。"""
    with pytest.raises(ForbiddenCriterionKey):
        validate_criterion_key(key)


def test_an_unregistered_dimension_is_rejected_too():
    """
    ⭐ fail-closed 的分水岭。上面两条只证明"已知的坏维度被拦下"——那是黑名单
    也能做到的事。这条用一个**既不在白名单、也不在任何黑名单示例里**的编造维度，
    断言它同样被拒：只有"未登记即拒绝"的实现能让它变红，黑名单实现会放行。

    红线要防的正是"没想到的新维度"——想得到的那些本来就写在文档里了。
    """
    with pytest.raises(ForbiddenCriterionKey):
        validate_criterion_key("candidate_vibe_index_v3")


def test_a_registered_dimension_passes_and_is_returned_unchanged():
    assert validate_criterion_key("skill_match") == "skill_match"


@pytest.mark.parametrize(
    "red_line_key",
    [
        "speech_rate",
        "pause_duration",
        "silence_ratio",
        "facial_expression",
        "micro_expression",
        "emotion_score",
    ],
)
def test_whitelist_itself_contains_no_red_line_dimension(red_line_key):
    """
    直接钉常量的内容，不经过 validate_criterion_key()。
    将来有人"为了跑通某个 demo"把 facial_expression 加进白名单，
    validate_criterion_key() 会老老实实放行、上面的用例全部变绿——只有这条会红。
    """
    assert red_line_key not in CRITERION_KEY_WHITELIST


def test_rejection_happens_at_construction_not_at_write_time():
    """
    强制点在 CriterionScore 构造期，不是 sink 的写入期。这决定了**所有**写入
    路径（U5 的 queue、U6 的断言、将来 M2 的评分器）都绕不过去——它们连一个
    非法的 CriterionScore 对象都造不出来。
    """
    with pytest.raises(ForbiddenCriterionKey):
        CriterionScore(
            criterion_key="facial_expression",
            score=0.9,
            evidence_ref="interview-1#10-20",
        )


def test_a_legal_criterion_score_still_constructs():
    """阴性对照：别把校验写成"所有 key 都拒"，那样上面全部变绿而功能全死。"""
    score = CriterionScore(
        criterion_key="skill_match",
        score=0.8,
        evidence_ref="resume-1#120-180",
    )

    assert score.criterion_key == "skill_match"
