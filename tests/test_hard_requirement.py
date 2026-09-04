"""硬门槛规则草案提取（tasks 5.8）。

spec「硬门槛规则草案提取」/ Scenario「提取硬门槛」：
    画像产出完成 → 每条规则包含字段名、比较运算符、比较值、是否阻断，
    并附一句人类可读的说明。

⛔ 提取是**确定性纯函数**，不调模型：草案要能被人复核、被回放对比，
一次模型调用就把它变成不可复算的东西。本文件的
test_extraction_is_deterministic 是这条约束的机器判据。
"""

import copy

from app.agents.hard_requirement import (
    EXTRACTABLE_FIELDS,
    OPERATORS,
    HardRequirement,
    extract_hard_requirements,
)


def _profile(**overrides) -> dict:
    profile = {
        "job_title": "嵌入式软件工程师",
        "department": "电子研发部",
        "headcount": 2,
        "education_requirement": "本科及以上",
        "experience_years": "3-5年",
        "core_skills": [
            {"name": "C 语言", "required": True},
            {"name": "Python 脚本", "required": False},
        ],
        "project_experience_requirement": "有 ECU 量产项目经历",
        "soft_skill_keywords": ["沟通能力强", "有责任心"],
        "autosar_experience": ["CP"],
        "functional_safety": "ASIL-B",
        "mcu_family": ["英飞凌 Aurix"],
        "diag_stack": ["UDS（ISO 14229）"],
        "sop_projects": [
            {
                "vehicle_model": "A 车型",
                "sop_date": "2024-06",
                "role": "软件负责人",
                "is_mass_production": True,
            }
        ],
        "toolchain": ["Vector（CANoe/CANape）"],
        "unspecified_fields": [],
    }
    profile.update(overrides)
    return profile


def _by_field(rules, field):
    return [r for r in rules if r.field == field]


def test_every_rule_carries_the_four_required_parts_plus_a_sentence():
    """spec 的四件套 + 一句人类可读说明，一条都不能缺。"""
    rules = extract_hard_requirements(_profile())

    assert rules, "完整画像必须提取出至少一条规则"
    for rule in rules:
        assert isinstance(rule, HardRequirement)
        assert rule.field in EXTRACTABLE_FIELDS
        assert rule.operator in OPERATORS
        assert rule.value.strip() != ""
        assert isinstance(rule.blocking, bool)
        assert rule.human_readable.strip() != ""


def test_education_lower_bound():
    rules = _by_field(extract_hard_requirements(_profile()), "education_requirement")
    assert len(rules) == 1
    assert (rules[0].operator, rules[0].value, rules[0].blocking) == (
        "education_gte",
        "本科",
        True,
    )


def test_education_takes_the_lowest_level_mentioned():
    """"本科及以上，硕士优先" 的硬门槛是本科，不是硕士。

    取**最低**被提到的档位是刻意的保守方向：门槛取高了会把合格的人挡在外面，
    而这条规则将来要用来向候选人解释淘汰原因。
    """
    rules = _by_field(
        extract_hard_requirements(_profile(education_requirement="本科及以上，硕士优先")),
        "education_requirement",
    )
    assert [r.value for r in rules] == ["本科"]


def test_education_without_a_recognizable_level_yields_no_rule():
    for text in ("不限", "学历不限", "未指定", ""):
        assert _by_field(
            extract_hard_requirements(_profile(education_requirement=text)),
            "education_requirement",
        ) == []


def test_experience_lower_bound():
    rules = _by_field(extract_hard_requirements(_profile()), "experience_years")
    assert len(rules) == 1
    assert (rules[0].operator, rules[0].value, rules[0].blocking) == ("gte", "3", True)


def test_experience_upper_bound_is_not_a_hard_gate():
    """"3 年以下" 是上限，不是下限。⛔ 不得把它当成 gte 3 提取出来。"""
    for text in ("3 年以下", "5年以内"):
        assert _by_field(
            extract_hard_requirements(_profile(experience_years=text)),
            "experience_years",
        ) == []


def test_experience_without_a_number_yields_no_rule():
    for text in ("不限", "应届亦可", "未指定", ""):
        assert _by_field(
            extract_hard_requirements(_profile(experience_years=text)), "experience_years"
        ) == []


def test_required_skill_blocks_and_optional_skill_does_not():
    rules = _by_field(extract_hard_requirements(_profile()), "core_skills")
    assert [(r.operator, r.value, r.blocking) for r in rules] == [
        ("contains", "C 语言", True),
        ("contains", "Python 脚本", False),
    ]


def test_functional_safety_none_yields_no_rule():
    assert _by_field(
        extract_hard_requirements(_profile(functional_safety="无")), "functional_safety"
    ) == []


def test_functional_safety_level_blocks():
    rules = _by_field(extract_hard_requirements(_profile()), "functional_safety")
    assert [(r.operator, r.value, r.blocking) for r in rules] == [
        ("equals", "ASIL-B", True)
    ]


def test_autosar_none_yields_no_rule():
    assert _by_field(
        extract_hard_requirements(_profile(autosar_experience=["无"])),
        "autosar_experience",
    ) == []


def test_transferable_platform_fields_are_not_blocking():
    """MCU 平台 / 诊断栈 / 工具链是可迁移经验，提成规则但不阻断。"""
    rules = extract_hard_requirements(_profile())
    for field in ("mcu_family", "diag_stack", "toolchain"):
        found = _by_field(rules, field)
        assert found, f"{field} 应产出一条规则"
        assert all(r.blocking is False for r in found)
        assert all(r.operator == "contains" for r in found)


def test_mass_production_sop_yields_one_blocking_rule():
    rules = _by_field(extract_hard_requirements(_profile()), "sop_projects")
    assert [(r.operator, r.value, r.blocking) for r in rules] == [
        ("is_true", "is_mass_production", True)
    ]


def test_non_mass_production_sop_yields_no_rule():
    profile = _profile()
    profile["sop_projects"][0]["is_mass_production"] = False
    assert _by_field(extract_hard_requirements(profile), "sop_projects") == []


def test_unspecified_fields_never_become_gates():
    """追问超限用"未指定"填充的字段⛔ 不得变成硬门槛（spec「追问达到上限」）。"""
    profile = _profile(unspecified_fields=["education_requirement", "experience_years"])
    rules = extract_hard_requirements(profile)
    assert _by_field(rules, "education_requirement") == []
    assert _by_field(rules, "experience_years") == []


def test_non_gateable_fields_are_structurally_excluded():
    """岗位名称/部门/编制数/项目经验自由文本都不是候选人可自动判定的门槛。"""
    for field in (
        "job_title",
        "department",
        "headcount",
        "project_experience_requirement",
    ):
        assert field not in EXTRACTABLE_FIELDS


def test_rules_are_ordered_by_the_field_whitelist():
    rules = extract_hard_requirements(_profile())
    positions = [EXTRACTABLE_FIELDS.index(r.field) for r in rules]
    assert positions == sorted(positions)


def test_extraction_is_deterministic():
    """同一份画像重跑必须逐条相同、顺序相同——⛔ 不调模型的机器判据。"""
    profile = _profile()
    assert extract_hard_requirements(profile) == extract_hard_requirements(profile)


def test_extraction_does_not_mutate_the_profile():
    """纯函数（工程铁律 2）：入参画像一个字节都不许改。"""
    profile = _profile()
    before = copy.deepcopy(profile)
    extract_hard_requirements(profile)
    assert profile == before


def test_duplicate_skills_collapse_into_one_rule():
    """同名技能出现两次只产出一条——复合主键容不下第二条，重复即 IntegrityError。"""
    profile = _profile(
        core_skills=[
            {"name": "C 语言", "required": True},
            {"name": "C 语言", "required": True},
        ]
    )
    assert len(_by_field(extract_hard_requirements(profile), "core_skills")) == 1


def test_empty_and_malformed_profile_never_raises():
    """画像形状不可信时也不许抛——抛了就是业务经理点确认的那一刻炸成 500。"""
    assert extract_hard_requirements({}) == []
    assert extract_hard_requirements({"core_skills": "不是列表"}) == []
    assert extract_hard_requirements({"core_skills": [None, 42, {"required": True}]}) == []
