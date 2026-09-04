"""JD 文案对画像字段的溯源校验（tasks 7.3）。

⚠️ 与 tests/test_field_grounding.py 是**两件事**，⛔ 不要合并：
那一份校验的是「画像字段」对「用户原话」的溯源（m1-intake-quality-fixes 第 7 章），
这一份校验的是「JD 文案」对「画像字段」的溯源。对象不同，一份绿不能替另一份作证。
"""

import pytest

from app.agents.jd_grounding import (
    JD_TECHNICAL_TERMS,
    profile_grounding_haystack,
    verify_jd_grounding,
)

PROFILE = {
    "job_title": "底层软件工程师",
    "department": "电子电器研发部",
    "headcount": 2,
    "education_requirement": "本科及以上",
    "experience_years": "3-5年",
    "core_skills": [{"name": "CAN-FD 驱动开发", "required": True}],
    "autosar_experience": ["CP"],
    "functional_safety": "ASIL-B",
    "mcu_family": ["英飞凌 Aurix"],
    "diag_stack": ["UDS"],
    "toolchain": ["CANoe"],
}


def test_terms_present_in_profile_are_grounded():
    jd = "岗位职责：基于 AUTOSAR CP 开发 CAN-FD 驱动，满足 ASIL-B，使用 CANoe 验证。"
    assert verify_jd_grounding(jd, PROFILE) == []


def test_terms_absent_from_profile_are_reported():
    """v4-pro 那次编造的正是这一类：画像里一个字都没有，文案里冒出来一串。"""
    jd = "任职要求：熟悉 FlexRay 与 SOME/IP，有 Lauterbach 调试经验。"
    assert verify_jd_grounding(jd, PROFILE) == ["FlexRay", "SOME/IP", "Lauterbach"]


def test_result_is_deterministic_and_declaration_ordered():
    """同一份输入重跑必须同一个结果、同一个顺序——否则这个数字不可复算，
    也就没有决策价值（design 决策 11 否决模型判官的同一条理由）。"""
    jd = "要求熟悉 Lauterbach、FlexRay。"
    first = verify_jd_grounding(jd, PROFILE)
    second = verify_jd_grounding(jd, PROFILE)
    assert first == second == ["FlexRay", "Lauterbach"]


def test_normalization_is_shared_with_field_grounding():
    """全半角与空白差异不算未溯源（复用 normalize_for_grounding 的同一口径）。"""
    jd = "要求 ＡＳＩＬ-Ｂ 与 CAN - FD 经验。"
    assert verify_jd_grounding(jd, PROFILE) == []


def test_jd_text_inside_profile_never_grounds_itself():
    """⛔ 最重要的一条：haystack 必须排除下划线内部键。

    _jd_text 就存在同一个 profile_json 里；把它算进 haystack，文案就会拿自己
    当证据，verify 永远返回空——这条校验会变成一个**永远不会红的摆设**，
    而且没有任何症状。
    """
    profile = {**PROFILE, "_jd_text": "熟悉 FlexRay 与 SOME/IP。"}
    assert verify_jd_grounding("熟悉 FlexRay 与 SOME/IP。", profile) == [
        "FlexRay",
        "SOME/IP",
    ]


def test_haystack_excludes_underscore_keys_and_booleans():
    haystack = profile_grounding_haystack(
        {"toolchain": ["CANoe"], "_jd_text": "FlexRay", "_jd_needs_manual": True}
    )
    assert "CANoe" in haystack
    assert "FlexRay" not in haystack


def test_terms_never_span_two_fields():
    """两个字段拼在一起不得凑出第三个术语。

    画像里有 "CAN" 和 "oe" 两个不相干的值时，⛔ 不许拼成 "CANoe" 把一个真正
    未溯源的工具链判成已溯源。字段之间用 NUL 隔开，子串跨不过去。
    """
    profile = {"diag_stack": ["CAN"], "toolchain": ["oe"]}
    assert verify_jd_grounding("使用 CANoe 验证。", profile) == ["CANoe"]


def test_empty_and_malformed_profile_do_not_raise():
    """画像是 LLM 自由生成的裸 dict，任何形状都不许抛——抛了就是一次 500。"""
    assert verify_jd_grounding("熟悉 FlexRay。", {}) == ["FlexRay"]
    assert verify_jd_grounding("熟悉 FlexRay。", None) == ["FlexRay"]
    assert verify_jd_grounding("", PROFILE) == []


def test_no_term_matches_nothing():
    assert verify_jd_grounding("岗位职责：负责团队日常协作与文档撰写。", PROFILE) == []


@pytest.mark.parametrize("term", sorted(JD_TECHNICAL_TERMS))
def test_every_term_grounds_itself(term):
    """词表的自洽守卫：把术语本身放进画像，它必须判为已溯源。

    加词条时最容易犯的错是别名写错（例如把 'ASIL B' 写进 aliases 却把主键写成
    'ASIL-B '），那会让这个词条**永远判未溯源**——加一个词条就多一条恒假的噪声，
    而噪声正是这个观测指标唯一怕的东西。
    """
    assert verify_jd_grounding(term, {"core_skills": [term]}) == []
