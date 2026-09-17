import pytest
from pydantic import ValidationError

from app.schemas.rank_result import CriterionEvidence, CriterionScoreOut, RankResult


def _score(key: str, score: float = 3.0) -> CriterionScoreOut:
    return CriterionScoreOut(
        key=key,
        score=score,
        rationale="有 AUTOSAR 量产经验",
        evidence=CriterionEvidence(span_id=6, quote="负责 AUTOSAR CP 平台"),
    )


def test_total_score_is_mean_of_dimensions():
    result = RankResult(scores=[_score("skill_match", 4), _score("experience_depth", 2)])
    assert result.total_score() == 3.0


def test_key_outside_whitelist_rejected():
    with pytest.raises(ValidationError, match="白名单"):
        RankResult(scores=[_score("facial_expression")])


def test_duplicate_key_rejected():
    with pytest.raises(ValidationError, match="重复"):
        RankResult(scores=[_score("skill_match"), _score("skill_match")])


def test_evidence_is_required_and_quote_non_empty():
    with pytest.raises(ValidationError):
        CriterionScoreOut(key="skill_match", score=3, rationale="", evidence=None)
    with pytest.raises(ValidationError):
        CriterionEvidence(span_id=1, quote="")


def test_empty_scores_rejected():
    with pytest.raises(ValidationError):
        RankResult(scores=[])
