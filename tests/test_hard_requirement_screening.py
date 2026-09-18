from __future__ import annotations

import json

import pytest

from app.agents.hard_requirement import HardRequirement
from app.agents.hard_requirement_screening import (
    RULE_FIELD_TO_RESUME_FIELD,
    RuleVerdict,
    _evaluate_operator,
    screen,
)
from app.schemas.resume_fields import (
    EducationField,
    EducationValue,
    ListField,
    NumberField,
    ResumeFields,
    SpanRef,
    TextField,
)


def _fields(**overrides) -> ResumeFields:
    base = dict(
        name=TextField(value="张三", confidence=0.95, spans=[SpanRef(span_id=1, quote="张三", start=0, end=2)]),
        years_of_experience=NumberField(
            value=5.0, confidence=0.9, spans=[SpanRef(span_id=2, quote="5年经验", start=10, end=15)]
        ),
        skills=ListField(
            value=["AUTOSAR CP 开发", "Python"],
            confidence=0.9,
            spans=[SpanRef(span_id=3, quote="AUTOSAR CP 开发", start=20, end=30)],
        ),
        companies=ListField(not_mentioned=True, value=[], confidence=1.0),
        education=EducationField(
            value=EducationValue(degree="本科", school="某大学"),
            confidence=0.9,
            spans=[SpanRef(span_id=4, quote="本科", start=40, end=42)],
        ),
        expected_city=TextField(not_mentioned=True, value=None, confidence=1.0),
    )
    base.update(overrides)
    return ResumeFields(**base)


def _rule(field: str, operator: str, value: str, blocking: bool = True) -> HardRequirement:
    return HardRequirement(
        field=field, operator=operator, value=value, blocking=blocking,
        human_readable=f"{field} {operator} {value}",
    )


# ── _evaluate_operator：五种运算符各自的比较逻辑，不依赖任何字段映射 ──

def test_operator_gte_true_and_false():
    assert _evaluate_operator("gte", "3", 5.0) is True
    assert _evaluate_operator("gte", "6", 5.0) is False


def test_operator_education_gte_true_and_false():
    assert _evaluate_operator("education_gte", "本科", "硕士") is True
    assert _evaluate_operator("education_gte", "硕士", "本科") is False


def test_operator_education_gte_unrecognized_resolved_value_is_false():
    assert _evaluate_operator("education_gte", "本科", "野生学历") is False


def test_operator_contains_true_and_false():
    assert _evaluate_operator("contains", "AUTOSAR", ["AUTOSAR CP 开发", "Python"]) is True
    assert _evaluate_operator("contains", "Rust", ["AUTOSAR CP 开发", "Python"]) is False


def test_operator_equals_true_and_false():
    assert _evaluate_operator("equals", "ASIL-D", "ASIL-D") is True
    assert _evaluate_operator("equals", "ASIL-D", "ASIL-B") is False


def test_operator_is_true_true_and_false():
    assert _evaluate_operator("is_true", "is_mass_production", True) is True
    assert _evaluate_operator("is_true", "is_mass_production", False) is False


def test_operator_unknown_raises():
    with pytest.raises(ValueError):
        _evaluate_operator("no_such_op", "x", "y")


# ── screen()：三个已映射字段的真实判定（pass / fail / skipped 三态）──

def test_screen_experience_years_pass():
    rules = [_rule("experience_years", "gte", "3")]
    verdicts = screen(_fields(), rules, frozenset())
    assert len(verdicts) == 1
    assert verdicts[0].verdict == "pass"
    assert verdicts[0].evidence_ref is None


def test_screen_experience_years_fail_carries_evidence():
    rules = [_rule("experience_years", "gte", "10")]
    verdicts = screen(_fields(), rules, frozenset())
    assert len(verdicts) == 1
    v = verdicts[0]
    assert v.verdict == "fail"
    assert v.evidence_ref is not None
    payload = json.loads(v.evidence_ref)
    assert payload == {"span_id": 2, "start": 10, "end": 15}
    assert v.human_readable
    assert v.reason


def test_screen_education_gte_pass():
    rules = [_rule("education_requirement", "education_gte", "本科")]
    verdicts = screen(_fields(), rules, frozenset())
    assert verdicts[0].verdict == "pass"


def test_screen_education_gte_fail():
    rules = [_rule("education_requirement", "education_gte", "硕士")]
    verdicts = screen(_fields(), rules, frozenset())
    assert verdicts[0].verdict == "fail"


def test_screen_core_skills_contains_pass():
    rules = [_rule("core_skills", "contains", "AUTOSAR")]
    verdicts = screen(_fields(), rules, frozenset())
    assert verdicts[0].verdict == "pass"


def test_screen_core_skills_contains_fail():
    rules = [_rule("core_skills", "contains", "Rust")]
    verdicts = screen(_fields(), rules, frozenset())
    assert verdicts[0].verdict == "fail"


# ── skipped 的三种来源：待校对 / 未提及 / 无原文依据 ──

def test_screen_skipped_when_field_in_review_queue():
    rules = [_rule("experience_years", "gte", "10")]
    verdicts = screen(_fields(), rules, frozenset({"years_of_experience"}))
    assert verdicts[0].verdict == "skipped"
    assert verdicts[0].reason == "待校对"


def test_screen_skipped_when_field_not_mentioned():
    rules = [_rule("core_skills", "contains", "AUTOSAR")]
    fields = _fields(skills=ListField(not_mentioned=True, value=[], confidence=1.0))
    verdicts = screen(fields, rules, frozenset())
    assert verdicts[0].verdict == "skipped"
    assert verdicts[0].reason == "简历未提及"


def test_screen_skipped_when_education_text_unrecognized():
    rules = [_rule("education_requirement", "education_gte", "本科")]
    fields = _fields(
        education=EducationField(
            value=EducationValue(degree="野生学历", school=None),
            confidence=0.9,
            spans=[SpanRef(span_id=4, quote="野生学历", start=40, end=44)],
        )
    )
    verdicts = screen(fields, rules, frozenset())
    assert verdicts[0].verdict == "skipped"
    assert verdicts[0].reason == "学历文本无法识别为标准学历档位"


def test_screen_fail_downgrades_to_skipped_when_no_resolved_span():
    """字段已提及但没有任何 span 拿到反查偏移量（LLM 给的 quote 反查失败）——
    这条规则本应 fail，但 fail 必须带 evidence_ref，没有可用偏移量就只能
    skipped，不能写一条 evidence_ref 为空的 fail（架构决策 4）。"""
    rules = [_rule("core_skills", "contains", "Rust")]
    fields = _fields(
        skills=ListField(
            value=["AUTOSAR CP 开发"],
            confidence=0.9,
            spans=[SpanRef(span_id=3, quote="AUTOSAR CP 开发", start=None, end=None)],
        )
    )
    verdicts = screen(fields, rules, frozenset())
    assert verdicts[0].verdict == "skipped"
    assert verdicts[0].reason == "无原文依据"


# ── 未映射的 ECU 专属字段：恒 skip，不管 blocking（架构决策 1／8）──

@pytest.mark.parametrize(
    "field,operator,value",
    [
        ("functional_safety", "equals", "ASIL-D"),
        ("autosar_experience", "contains", "CP"),
        ("mcu_family", "contains", "TC3xx"),
        ("diag_stack", "contains", "UDS"),
        ("toolchain", "contains", "Vector"),
        ("sop_projects", "is_true", "is_mass_production"),
    ],
)
def test_screen_unmapped_fields_always_skipped(field, operator, value):
    for blocking in (True, False):
        rules = [_rule(field, operator, value, blocking=blocking)]
        verdicts = screen(_fields(), rules, frozenset())
        assert verdicts[0].verdict == "skipped"
        assert verdicts[0].reason == "简历未提及"
        assert set(RULE_FIELD_TO_RESUME_FIELD) == {
            "education_requirement", "experience_years", "core_skills",
        }


def test_screen_empty_ruleset_returns_empty_list():
    assert screen(_fields(), [], frozenset()) == []


def test_screen_rule_ref_format():
    rules = [_rule("experience_years", "gte", "3")]
    verdicts = screen(_fields(), rules, frozenset())
    assert verdicts[0].rule_ref == "experience_years:gte:3"


def test_screen_is_deterministic():
    rules = [
        _rule("experience_years", "gte", "3"),
        _rule("education_requirement", "education_gte", "本科"),
    ]
    fields = _fields()
    first = screen(fields, rules, frozenset())
    second = screen(fields, rules, frozenset())
    assert first == second
