"""六字段抽取（resume-parsing spec「首期字段抽取」「原文分片与字段回指」
「字段级置信度与人工校对队列」，design D4/D5）。纯函数：只调 LLM 网关、做
数据转换，不写库（工程铁律 2）。
"""
from __future__ import annotations

from typing import Any

from app.llm.gateway import LLMCallMeta, LLMGateway
from app.parsing.spans import TextSpan, render_for_prompt, resolve_span_ref
from app.schemas.resume_fields import FIELD_LABELS, FIELD_NAMES, ResumeFields

PARSE_PROMPT_VERSION = "parse-v1"

_SYSTEM_PROMPT = (
    "你是简历结构化抽取助手。下面会给出一份简历原文，按行分片并标好编号"
    "（形如 [#3] 这一行原文）。请抽取以下六个字段：\n"
    + "\n".join(f"- {name}（{label}）" for name, label in FIELD_LABELS.items())
    + "\n\n规则：\n"
    "1. 每个字段必须给出 confidence（0~1，你对这次抽取结果的把握程度）。\n"
    "2. 简历中确实没有提到的字段，把 not_mentioned 设为 true，value 留空，"
    "⛔ 不要编造。\n"
    "3. 非未提及的字段，spans 至少给一条：span_id 填原文分片编号，quote 填"
    "从该分片**逐字摘录**（不得改写、不得省略号）的原文片段，用于人工核对。"
    "⛔ 不要给出 start/end（由系统计算，你留空即可）。\n"
    "4. education 字段的 value 是 {degree, school} 两个子字段，同样允许"
    "not_mentioned。\n"
    "5. skills、companies 是字符串列表。"
)


def compute_parse(
    gateway: LLMGateway,
    *,
    spans: list[TextSpan],
    prompt_version: str = PARSE_PROMPT_VERSION,
    audit_context: dict[str, Any] | None = None,
) -> tuple[ResumeFields, LLMCallMeta]:
    user_prompt = render_for_prompt(spans)
    fields, meta = gateway.extract_structured_with_meta(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        schema=ResumeFields,
        prompt_version=prompt_version,
        audit_context=audit_context,
    )
    resolved = _resolve_spans_and_synthesize_confidence(fields, spans)
    return resolved, meta


def _resolve_spans_and_synthesize_confidence(
    fields: ResumeFields, spans: list[TextSpan]
) -> ResumeFields:
    """design D5：最终置信度 = 模型自报置信度 × span 可定位性。

    可定位性是 0/1（不是连续值）：只要任意一条 span 的 quote 能在原文里反查到
    位置，就认为"有回指"，置信度维持模型自报值；一条都反查不到，置信度直接
    归零——spec「抽取结果无回指」：这种情况必须进人工校对队列，归零能保证
    "无论岗位阈值设多低，这个字段都会落进队列"（阈值默认 0.7，任何非负阈值
    都大于 0）。
    """
    for name in FIELD_NAMES:
        field = getattr(fields, name)
        if field.not_mentioned:
            continue
        located = False
        for span_ref in field.spans:
            resolved = resolve_span_ref(spans, span_ref.span_id, span_ref.quote)
            if resolved is not None:
                span_ref.start, span_ref.end = resolved
                located = True
        if not located:
            field.confidence = 0.0
    return fields
