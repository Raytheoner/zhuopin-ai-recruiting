"""post 评分（app/agents/interview_scoring.py::score，U5 tasks 6.2）的 LLM
输出 schema。

interview-scorecard spec「逐维评分带 turn 回指」：每条评分项 MUST 携带非空
的证据回指（turn 标识＋起止偏移＋原话摘录）。start/end 允许 None——由
app/graph/interview_scoring_nodes.py 的证据反查校正（tasks 6.3）用 quote 在
turn 原文里反查填充/校正，模型给的 start/end 不直接采信（design D4「回指校正」
Scenario：模型给出的偏移与原话摘录不一致时以反查为准）。

维度白名单校验（是否在冻结快照的 rubric 维度集合内）不在这个 schema 里做——
白名单是运行时才知道的动态集合（每个场次的 prep_snapshot 不同），不能写成
Pydantic 的静态 Literal，校验放在 app/agents/interview_scoring.py::score()
里做（与 app/agents/interview_prep.py 的「越界丢弃」同一处理位置）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class ScoreEvidenceOut(BaseModel):
    turn_id: str
    quote: str = Field(min_length=1)
    start: int | None = None
    end: int | None = None


class ScoreDimensionOut(BaseModel):
    dimension: str
    score: float = Field(ge=0.0, le=5.0)
    rationale: str = ""
    evidence: ScoreEvidenceOut


class ScoreCardOut(BaseModel):
    dimensions: list[ScoreDimensionOut] = Field(min_length=1)
    overall_summary: str = Field(min_length=1)

    @model_validator(mode="after")
    def _dimensions_unique(self):
        keys = [d.dimension for d in self.dimensions]
        if len(set(keys)) != len(keys):
            raise ValueError(f"评分维度重复: {keys}")
        return self
