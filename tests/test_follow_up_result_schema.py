"""app/schemas/follow_up_result.py：追问选择 LLM 输出 schema。"""
import pytest
from pydantic import ValidationError

from app.schemas.follow_up_result import FollowUpChoiceOut


def test_next_question_decision_does_not_require_index():
    parsed = FollowUpChoiceOut(decision="next_question")
    assert parsed.follow_up_index is None


def test_follow_up_decision_requires_index():
    with pytest.raises(ValidationError):
        FollowUpChoiceOut(decision="follow_up")


def test_follow_up_decision_with_index_is_valid():
    parsed = FollowUpChoiceOut(decision="follow_up", follow_up_index=1)
    assert parsed.follow_up_index == 1


def test_unknown_decision_literal_is_rejected():
    with pytest.raises(ValidationError):
        FollowUpChoiceOut(decision="something_else")
