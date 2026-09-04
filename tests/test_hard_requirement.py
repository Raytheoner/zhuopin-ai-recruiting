"""硬门槛规则草案提取（tasks 5.8）。

spec「硬门槛规则草案提取」/ Scenario「提取硬门槛」：
    画像产出完成 → 每条规则包含字段名、比较运算符、比较值、是否阻断，
    并附一句人类可读的说明。

⛔ 提取是**确定性纯函数**，不调模型：草案要能被人复核、被回放对比，
一次模型调用就把它变成不可复算的东西。本文件的
test_extraction_is_deterministic 是这条约束的机器判据。
"""

import copy

import pytest

from app.agents.hard_requirement import (
    EXTRACTABLE_FIELDS,
    OPERATORS,
    HardRequirement,
    SubjectiveRequirementError,
    assert_no_subjective_requirements,
    extract_hard_requirements,
    is_subjective,
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


def test_education_negated_or_bounded_phrasing_is_not_a_hard_gate():
    """"学历不限，X优先" 一类否定/上限表述⛔ 不得凭空造出 blocking=True 门槛。

    覆盖 final review 复现的四种表述：裸别名匹配对"不限""以下""优先""亦可"
    没有任何否定或上限守卫，命中即造出一条硬门槛——这比经验年限漏判更坏，
    经验年限漏判是丢了一条规则（安全方向），这里是**造出**一条规则，而且
    这条规则将来会原样念给被拒的候选人听。
    """
    for text in (
        "学历不限，本科优先",
        "本科以下",
        "大专以下学历亦可",
        "不限，硕士优先",
    ):
        assert _by_field(
            extract_hard_requirements(_profile(education_requirement=text)),
            "education_requirement",
        ) == []


def test_education_preference_clause_is_not_promoted_to_a_gate():
    """final review round 2：round 1 的 `"以上" not in text` 逃生舱是整段级的，

    "本科以上优先" 这类最常见的招聘偏好写法里，"以上"和"优先"落在**同一个
    子句**，整段级判定会被直接击穿。这组用例是 re-reviewer 探针复现的九句，
    加上四句自查补充的边界样例——子句级判定必须逐句区分"门槛子句"与
    "偏好/上限子句"，⛔ 不能靠整段扫一遍某个词。
    """
    no_gate_cases = (
        "本科以上优先",
        "本科以上学历优先",
        "硕士以上优先，本科亦可",
        "学历不限，如有本科以上学历者优先考虑",
        "本科、硕士以上学历不限，能力优先",
        "大专以上学历优先，条件优秀者可放宽",
        "以上学历均不限",
        # 自查补充：子句边界的压力样例。
        "本科学历",  # 裸学历词，没有显式下限词陪着，不算门槛
    )
    for text in no_gate_cases:
        assert (
            _by_field(
                extract_hard_requirements(_profile(education_requirement=text)),
                "education_requirement",
            )
            == []
        ), f"{text!r} 不该产出门槛"

    gated_cases = (
        ("本科及以上", "本科"),
        ("本科及以上，硕士优先", "本科"),
        ("硕士及以上", "硕士"),
        ("大专及以上", "大专"),
        # 自查补充：门槛子句与偏好子句顺序颠倒，结果不该变。
        ("硕士优先，本科及以上", "本科"),
        # 自查补充：整句没有子句分隔符，仍要能识别出门槛。
        ("本科及以上学历要求", "本科"),
        # 自查补充："以上"以外的下限词（起步/最低）同样要被认作门槛。
        ("本科起步", "本科"),
    )
    for text, expected in gated_cases:
        rules = _by_field(
            extract_hard_requirements(_profile(education_requirement=text)),
            "education_requirement",
        )
        assert [r.value for r in rules] == [expected], f"{text!r} 应产出 {expected!r}"
        assert rules[0].blocking is True


def test_experience_lower_bound():
    rules = _by_field(extract_hard_requirements(_profile()), "experience_years")
    assert len(rules) == 1
    assert (rules[0].operator, rules[0].value, rules[0].blocking) == ("gte", "3", True)


def test_experience_upper_bound_is_not_a_hard_gate():
    """"3 年以下" 是上限，不是下限。⛔ 不得把它当成 gte 3 提取出来。

    覆盖 fix round 1 reviewer 复现的四种漏判表述（"不超过3年" "少于3年"
    "不到3年" "3年封顶"），加上原有的两种（"以下" "以内"）。
    """
    for text in (
        "3 年以下",
        "5年以内",
        "不超过3年",
        "少于3年",
        "不到3年",
        "3年封顶",
        "最多3年",
        "不足3年",
    ):
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


# ── tasks 5.9：主观描述拦截 ──────────────────────────────────────────────
#
# 合规红线（逐字）：主观描述（"沟通能力强"）不得进入硬门槛规则，只能作为软技能
# 关键词。下面这组用例就是这条红线的机器判据——在此之前它"无处可断"（tasks 5.9
# 原话），只有一个断言 prompt 文本含某几个关键词的测试，那验的是提示词写了什么，
# 不是行为。


def test_soft_skill_keywords_field_is_structurally_excluded():
    """第一道防线：整个字段进不来，不靠词表兜底。"""
    assert "soft_skill_keywords" not in EXTRACTABLE_FIELDS


def test_subjective_description_in_core_skills_is_filtered_out():
    """第二道防线：模型把主观描述塞进 core_skills 是真实会发生的。"""
    profile = _profile(
        core_skills=[
            {"name": "沟通能力强", "required": True},
            {"name": "有责任心", "required": True},
            {"name": "C 语言", "required": True},
        ]
    )
    rules = _by_field(extract_hard_requirements(profile), "core_skills")
    assert [r.value for r in rules] == ["C 语言"]


def test_subjective_description_stays_in_the_profile_as_soft_skills():
    """spec：这类描述只作为软技能关键词保留在画像中。

    "保留"= 提取过程一个字节都不改画像（画像冻结后不可变，改动走新版本）。
    """
    profile = _profile(soft_skill_keywords=["沟通能力强", "有责任心"])
    before = copy.deepcopy(profile)
    rules = extract_hard_requirements(profile)

    assert profile["soft_skill_keywords"] == ["沟通能力强", "有责任心"]
    assert profile == before
    assert all("沟通" not in r.value for r in rules)


def test_assert_rejects_a_hand_built_subjective_rule():
    """反证：绕过提取直接构造一条违规规则，落库前的断言必须报违例。"""
    rogue = HardRequirement(
        field="core_skills",
        operator="contains",
        value="沟通能力强",
        blocking=True,
        human_readable="必会技能：沟通能力强（不满足则不通过硬门槛）",
    )
    with pytest.raises(SubjectiveRequirementError) as exc:
        assert_no_subjective_requirements([rogue])
    assert "沟通" in str(exc.value)


def test_assert_rejects_a_rule_on_the_soft_skill_field_itself():
    """字段本身就是软技能关键词时，即便值看起来中性也必须被拒。"""
    rogue = HardRequirement(
        field="soft_skill_keywords",
        operator="contains",
        value="跨部门推动",
        blocking=False,
        human_readable="软技能：跨部门推动",
    )
    with pytest.raises(SubjectiveRequirementError):
        assert_no_subjective_requirements([rogue])


def test_assert_passes_on_a_clean_rule_set():
    assert_no_subjective_requirements(extract_hard_requirements(_profile()))


def test_extraction_output_always_passes_the_assert():
    """提取与断言必须自洽：正常路径永远不该在落库前被自己的断言拦下。"""
    profile = _profile(
        core_skills=[
            {"name": "沟通能力强", "required": True},
            {"name": "抗压能力强", "required": True},
            {"name": "AUTOSAR MCAL 配置", "required": True},
        ],
        soft_skill_keywords=["有责任心", "团队合作"],
    )
    assert_no_subjective_requirements(extract_hard_requirements(profile))


def test_the_guard_is_not_vacuous(monkeypatch):
    """有效性测试：把词表清空后，同一份画像必须能产出一条被断言抓到的规则。

    没有这一条，上面所有绿灯都可能只是因为断言什么都没查（与
    tests/test_audit_assertion_effectiveness.py 同一思路）。
    """
    import app.agents.hard_requirement as module

    monkeypatch.setattr(module, "SUBJECTIVE_TERMS", ())
    monkeypatch.setattr(module, "SUBJECTIVE_FIELDS", frozenset())
    # ⚠️ 必须按 core_skills 过滤：extract 返回的是**整份**草案（学历/年限/平台
    # 等等都在里面），不过滤的话这条断言比的是全量列表，永远不等。
    leaked = _by_field(
        module.extract_hard_requirements(
            _profile(core_skills=[{"name": "沟通能力强", "required": True}])
        ),
        "core_skills",
    )
    assert [r.value for r in leaked] == ["沟通能力强"]

    monkeypatch.undo()
    with pytest.raises(SubjectiveRequirementError):
        assert_no_subjective_requirements(leaked)


def test_is_subjective_covers_the_documented_examples():
    """CLAUDE.md 与 spec 里点名的两个例子必须被识别。"""
    assert is_subjective("沟通能力强")
    assert is_subjective("有责任心")
    assert not is_subjective("C 语言")
    assert not is_subjective("UDS（ISO 14229）")
    assert not is_subjective("AUTOSAR MCAL 配置")


# ── fix round 1：word-list 过滤覆盖面缺口（functional_safety / autosar_experience）──
#
# reviewer 发现：_extract_autosar 与 _extract_functional_safety 同样从不可控的
# 画像自由文本构造 value/human_readable，却没有接 is_subjective 过滤，而且两者
# 都恒为 blocking=True——比 core_skills/mcu_family 等字段风险更高。且这两个
# 字段一旦漏判，只能靠 assert_no_subjective_requirements 在确认时整体报错，
# 与 extract_hard_requirements 自己声明的"形状不对的部分静默跳过，保守方向是
# 少一条规则而不是整个确认失败"相矛盾。


def test_subjective_functional_safety_yields_no_rule():
    rules = _by_field(
        extract_hard_requirements(_profile(functional_safety="需要良好的沟通能力")),
        "functional_safety",
    )
    assert rules == []


def test_subjective_autosar_entry_is_filtered_but_legitimate_entry_survives():
    rules = _by_field(
        extract_hard_requirements(
            _profile(autosar_experience=["CP", "需要良好的沟通能力"])
        ),
        "autosar_experience",
    )
    assert [r.value for r in rules] == ["CP"]
