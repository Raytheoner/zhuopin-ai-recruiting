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
    """过滤白名单后，`kept` 未能覆盖 rubric 全部维度（含全部越界丢弃导致
    `kept` 为空、以及仅覆盖部分维度两种情形）后，本次评分判失败（可重试）。"""


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

    重试判据（spec `interview-scorecard` 需求"逐维评分带 turn 回指"，场景"一
    个场次的评分"：每个 rubric 维度各有一条评分项）：过滤白名单去重后的
    `kept` 必须覆盖 `score_input.rubric_dimensions` 全部维度，覆盖不全——无论
    是全部越界丢弃（`kept` 为空）还是仅覆盖部分维度——都判失败重试，不允许
    带着不完整的 ScoreCard 直接成功。`_filter_whitelisted` 已按 `dimension`
    去重，故 `kept` 中同一维度不会出现两次，覆盖判定用集合相等即可。
    """
    system_prompt = _build_system_prompt(score_input)
    required = set(score_input.rubric_dimensions)
    last_dropped = 0
    last_missing: set[str] = required

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
        kept_dimensions = {d.dimension for d in kept}
        last_missing = required - kept_dimensions

        if kept_dimensions == required:
            return ScoreCardDraft(
                dimensions=kept,
                overall_summary=parsed.overall_summary,
                dropped_count=dropped,
                run_id=meta.run_id,
                response_model=meta.response_model,
            )

    raise ScoringGenerationFailed(
        f"{max_retries} 次尝试后评分维度仍未覆盖全部 rubric 维度"
        f"（最近一次缺失 {sorted(last_missing)}，丢弃 {last_dropped} 条）"
    )
