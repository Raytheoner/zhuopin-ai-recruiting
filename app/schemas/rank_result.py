"""
精排（rubric 逐维打分）的 LLM 输出 schema（design D7、spec「逐维评分带证据回指」）。

evidence 在 schema 层强制而不是事后校验——事后校验只能丢结果，schema 强制能让模型
第一次就给出位置。start/end 由 spans.resolve_span_ref 反查填充；反查失败 ⇒ 整次评分不可用。
"""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.audit.criteria import CRITERION_KEY_WHITELIST


class CriterionEvidence(BaseModel):
    span_id: int = Field(ge=1)
    quote: str = Field(min_length=1)
    start: int | None = None
    end: int | None = None


class CriterionScoreOut(BaseModel):
    key: str
    score: float = Field(ge=0.0, le=5.0)
    rationale: str = ""
    evidence: CriterionEvidence


class RankResult(BaseModel):
    scores: list[CriterionScoreOut] = Field(min_length=1)

    @model_validator(mode="after")
    def _keys_whitelisted_and_unique(self):
        keys = [s.key for s in self.scores]
        bad = [k for k in keys if k not in CRITERION_KEY_WHITELIST]
        if bad:
            raise ValueError(f"评分维度不在白名单: {bad}；已登记: {sorted(CRITERION_KEY_WHITELIST)}")
        if len(set(keys)) != len(keys):
            raise ValueError(f"评分维度重复: {keys}")
        return self

    def total_score(self) -> float:
        return sum(s.score for s in self.scores) / len(self.scores)
