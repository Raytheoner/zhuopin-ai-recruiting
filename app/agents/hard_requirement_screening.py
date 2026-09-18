"""硬门槛引擎（hard-requirement-screening spec「逐条判定只标记不淘汰」，
design D6）。

⛔ 本模块是 L3 纯函数：不调模型、不写库、不持有任何数据库连接（工程铁律 2）。
判定必须确定性：同一简历解析结果与同一规则集重复判定结果相同（spec 原文）。

⛔ 本模块只产出标记，不执行淘汰——没有任何一行代码会把候选人筛掉或写
rejection_record（合规红线：AI 只做排序推荐，不做自动淘汰）。淘汰记录的
唯一写入路径是 app/storage/rejection.py::write_rejection()。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.agents.hard_requirement import HardRequirement
from app.audit.evidence_ref import format_evidence_ref
from app.schemas.resume_fields import EducationValue, ResumeFields

# 规则字段 → 简历字段映射（架构决策 1）。⛔ 不在此映射表中的规则字段一律
# skipped("简历未提及")，不管 blocking——那些字段（ECU 专属：
# functional_safety / autosar_experience / mcu_family / diag_stack /
# toolchain / sop_projects）本期简历侧完全不抽取（design D4：ECU 特化
# 字段二期），不判定是唯一正确行为，不是遗漏。
RULE_FIELD_TO_RESUME_FIELD: dict[str, str] = {
    "education_requirement": "education",
    "experience_years": "years_of_experience",
    "core_skills": "skills",
}

# 学历档位（架构决策 2）。⛔ 不导入 app/agents/hard_requirement.py 里的私有
# _EDUCATION_LEVELS——那张表服务"画像自由文本→门槛"的三层子句判定，语义更
# 宽；这里要的是"简历侧已抽取的单一学历词→档位"，语义更窄，独立维护不会
# 被对方的子句拆分逻辑意外牵动。
_DEGREE_RANK: dict[str, int] = {
    "大专": 0,
    "专科": 0,
    "本科": 1,
    "学士": 1,
    "硕士": 2,
    "研究生": 2,
    "博士": 3,
}


def _degree_rank(text: str | None) -> int | None:
    if not text:
        return None
    for alias, rank in _DEGREE_RANK.items():
        if alias in text:
            return rank
    return None


@dataclass(frozen=True)
class RuleVerdict:
    """一条规则对一份简历的判定结果。"""

    rule_ref: str
    verdict: str  # 'pass' | 'fail' | 'skipped'
    reason: str | None
    evidence_ref: str | None
    human_readable: str
    blocking: bool


def _rule_ref(rule: HardRequirement) -> str:
    """架构决策 7：确定性、人可读，与 hard_requirement 复合主键同源。"""
    return f"{rule.field}:{rule.operator}:{rule.value}"


def _evaluate_operator(operator: str, rule_value: str, resolved_value) -> bool:
    """五种 operator 的裸值比较（tasks 4.1）。⛔ 本函数不知道也不关心
    resolved_value 来自哪个简历字段——纯粹的值比较，可独立于任何字段映射
    被测试（架构决策 8：equals/is_true 当前没有真实映射字段，就靠这个
    函数直接测）。"""
    if operator == "gte":
        return float(resolved_value) >= float(rule_value)
    if operator == "education_gte":
        resolved_rank = _degree_rank(str(resolved_value))
        required_rank = _degree_rank(rule_value)
        if resolved_rank is None or required_rank is None:
            return False
        return resolved_rank >= required_rank
    if operator == "contains":
        if isinstance(resolved_value, list):
            return any(rule_value in item for item in resolved_value)
        return rule_value in str(resolved_value)
    if operator == "equals":
        return str(resolved_value) == rule_value
    if operator == "is_true":
        return bool(resolved_value) is True
    raise ValueError(f"未知 operator：{operator!r}")


def _first_resolved_span(spans) -> tuple[int, int, int] | None:
    """取第一个已完成反查偏移量的 span（架构决策 4）。"""
    for span in spans:
        if span.start is not None and span.end is not None:
            return span.span_id, span.start, span.end
    return None


def _resolved_field_value(fields: ResumeFields, resume_field_name: str):
    field = getattr(fields, resume_field_name)
    if resume_field_name == "education":
        value = field.value.degree if isinstance(field.value, EducationValue) else None
    else:
        value = field.value
    return field, value


def _skipped(rule: HardRequirement, rule_ref: str, reason: str) -> RuleVerdict:
    return RuleVerdict(
        rule_ref=rule_ref,
        verdict="skipped",
        reason=reason,
        evidence_ref=None,
        human_readable=rule.human_readable,
        blocking=rule.blocking,
    )


def screen(
    fields: ResumeFields,
    rules: list[HardRequirement],
    review_queue: frozenset[str],
) -> list[RuleVerdict]:
    """按 rules 逐条判定 fields，产出 pass/fail/skipped 三态标记列表
    （spec「逐条判定只标记不淘汰」）。规则集为空 ⇒ 返回空列表（调用方按
    "无硬门槛"处理，spec Scenario「规则集为空」）。

    review_queue：该简历当前处于 field_review_queue.status='pending' 的
    **简历字段名**集合（如 {"education"}）——⛔ 用简历字段名，不是规则
    字段名，两者词表不同（架构决策 1）。
    """
    verdicts: list[RuleVerdict] = []
    for rule in rules:
        rule_ref = _rule_ref(rule)
        resume_field_name = RULE_FIELD_TO_RESUME_FIELD.get(rule.field)

        if resume_field_name is None:
            verdicts.append(_skipped(rule, rule_ref, "简历未提及"))
            continue

        if resume_field_name in review_queue:
            verdicts.append(_skipped(rule, rule_ref, "待校对"))
            continue

        field, resolved_value = _resolved_field_value(fields, resume_field_name)

        if field.not_mentioned:
            verdicts.append(_skipped(rule, rule_ref, "简历未提及"))
            continue

        if rule.operator == "education_gte" and _degree_rank(str(resolved_value)) is None:
            verdicts.append(_skipped(rule, rule_ref, "学历文本无法识别为标准学历档位"))
            continue

        satisfied = _evaluate_operator(rule.operator, rule.value, resolved_value)

        if satisfied:
            verdicts.append(
                RuleVerdict(
                    rule_ref=rule_ref, verdict="pass", reason=None, evidence_ref=None,
                    human_readable=rule.human_readable, blocking=rule.blocking,
                )
            )
            continue

        resolved_span = _first_resolved_span(field.spans)
        if resolved_span is None:
            verdicts.append(_skipped(rule, rule_ref, "无原文依据"))
            continue

        span_id, start, end = resolved_span
        verdicts.append(
            RuleVerdict(
                rule_ref=rule_ref,
                verdict="fail",
                reason=rule.human_readable,
                evidence_ref=format_evidence_ref(span_id, start, end),
                human_readable=rule.human_readable,
                blocking=rule.blocking,
            )
        )
    return verdicts
