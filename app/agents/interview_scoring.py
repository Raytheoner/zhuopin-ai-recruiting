"""post 评分 L3 Agent（voice-structured-interview U5 tasks 6.2）。

纯函数：只调用 LLM 网关与做数据转换，不写库、不发消息（工程铁律 2）。写库
是 app/graph/interview_scoring_nodes.py 的 effect_* 节点的事；证据反查校正
（tasks 6.3）也不在这里做——反查需要 turn 原文，那是 L4 已经查出来的数据
（app/graph/interview_scoring_nodes.py::compute_align 的返回值），本模块不
import app.storage，拿不到。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.llm.gateway import LLMGateway
from app.schemas.interview_ai_input import ScoreInput
from app.schemas.interview_scoring_result import ScoreCardOut, ScoreDimensionOut

SCORE_PROMPT_VERSION = "interview-score-v1"

_SCORE_SYSTEM_PROMPT_TEMPLATE = (
    "你是资深技术面试官。以下是一场结构化面试的全部问答记录（turn_id/题号/题面/"
    "回答原文）。请按给定的 rubric 维度白名单逐维评分，0.0-5.0 分。"
    "每个维度 MUST 给出：dimension（必须是白名单中的原样值，不得发明白名单外的"
    "维度）、score、rationale、evidence（turn_id 必须是给定问答记录里出现过的 "
    "turn_id 之一，quote 必须是该 turn 回答原文中的一段连续文字，逐字摘录，不得"
    "改写或概括）。"
    "rubric 维度白名单：{dimensions}。"
    "另外给出 overall_summary：整场面试的一段简要总体评价（100 字以内）。"
    "输出 JSON，字段：dimensions(array)、overall_summary(string)。"
)


class ScoringGenerationFailed(Exception):
    """模型返回的维度评分全部越界丢弃（不在白名单内）后，本次评分判失败（可重
    试），与 app/agents/interview_prep.py::PrepGenerationFailed 同一判据。"""


@dataclass(frozen=True)
class ScoredDimensionDraft:
    dimension: str
    score: float
    rationale: str
    turn_id: str
    quote: str


@dataclass(frozen=True)
class ScoreCardDraft:
    dimensions: list[ScoredDimensionDraft]
    overall_summary: str
    dropped_count: int
    run_id: str
    response_model: str | None


def _build_system_prompt(score_input: ScoreInput) -> str:
    return _SCORE_SYSTEM_PROMPT_TEMPLATE.format(
        dimensions="、".join(score_input.rubric_dimensions)
    )


def _to_draft(item: ScoreDimensionOut) -> ScoredDimensionDraft:
    return ScoredDimensionDraft(
        dimension=item.dimension, score=item.score, rationale=item.rationale,
        turn_id=item.evidence.turn_id, quote=item.evidence.quote,
    )


def _filter_whitelisted(
    items: list[ScoreDimensionOut], *, dimensions: list[str]
) -> tuple[list[ScoredDimensionDraft], int]:
    allowed = set(dimensions)
    kept: list[ScoredDimensionDraft] = []
    dropped = 0
    seen: set[str] = set()
    for item in items:
        if item.dimension in allowed and item.dimension not in seen:
            seen.add(item.dimension)
            kept.append(_to_draft(item))
        else:
            dropped += 1
    return kept, dropped


def score(
    gateway: LLMGateway,
    score_input: ScoreInput,
    *,
    max_retries: int = 2,
    audit_context: dict | None = None,
) -> ScoreCardDraft:
    """L3 Agent：纯函数，只调 LLM 网关。

    重试判据与 app/agents/interview_prep.py::generate 同一判据："本轮维度评分
    全部越界丢弃"才判失败重试，单条越界只丢弃计数、其余维度保留。
    """
    system_prompt = _build_system_prompt(score_input)
    last_dropped = 0

    for _ in range(max_retries):
        parsed, meta = gateway.extract_structured_with_meta(
            system_prompt=system_prompt,
            user_prompt=score_input.model_dump_json(),
            schema=ScoreCardOut,
            prompt_version=SCORE_PROMPT_VERSION,
            audit_context=audit_context,
        )
        kept, dropped = _filter_whitelisted(
            parsed.dimensions, dimensions=score_input.rubric_dimensions
        )
        last_dropped = dropped

        if kept:
            return ScoreCardDraft(
                dimensions=kept,
                overall_summary=parsed.overall_summary,
                dropped_count=dropped,
                run_id=meta.run_id,
                response_model=meta.response_model,
            )

    raise ScoringGenerationFailed(
        f"{max_retries} 次尝试后评分维度全部越界丢弃（最近一次丢弃 {last_dropped} 条）"
    )
