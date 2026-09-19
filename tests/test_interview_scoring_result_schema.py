"""app/schemas/interview_scoring_result.py 的 schema 校验测试。"""
import pytest
from pydantic import ValidationError

from app.schemas.interview_scoring_result import ScoreCardOut, ScoreDimensionOut, ScoreEvidenceOut


def _dim(dimension="AUTOSAR CP", score=4.0, turn_id="t1", quote="做过三年"):
    return {
        "dimension": dimension, "score": score, "rationale": "回答扎实",
        "evidence": {"turn_id": turn_id, "quote": quote},
    }


def test_score_card_out_accepts_well_formed_payload():
    card = ScoreCardOut(dimensions=[_dim()], overall_summary="整体表现良好")
    assert card.dimensions[0].evidence.turn_id == "t1"
    assert card.dimensions[0].evidence.start is None


def test_score_card_out_rejects_duplicate_dimensions():
    with pytest.raises(ValidationError, match="评分维度重复"):
        ScoreCardOut(dimensions=[_dim(), _dim()], overall_summary="x")


def test_score_card_out_rejects_score_out_of_range():
    with pytest.raises(ValidationError):
        ScoreCardOut(dimensions=[_dim(score=5.5)], overall_summary="x")


def test_score_evidence_out_rejects_empty_quote():
    with pytest.raises(ValidationError):
        ScoreEvidenceOut(turn_id="t1", quote="")


def test_score_dimension_out_accepts_explicit_offsets():
    dim = ScoreDimensionOut(**_dim())
    assert dim.evidence.start is None and dim.evidence.end is None
