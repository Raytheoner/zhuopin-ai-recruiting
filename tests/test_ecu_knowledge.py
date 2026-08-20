from app.agents.ecu_knowledge import FOLLOWUP_RULES, match_ambiguous_terms


def test_matches_known_ambiguous_term():
    matches = match_ambiguous_terms("要个做嵌入式开发的，能写驱动")
    assert "嵌入式开发" in matches


def test_no_match_for_unrelated_text():
    assert match_ambiguous_terms("今天天气不错") == []


def test_every_rule_has_at_most_three_questions():
    for term, questions in FOLLOWUP_RULES.items():
        assert 1 <= len(questions) <= 3, f"{term} 的追问数超过每轮上限"


def test_every_spec_targets_a_real_profile_field_or_none():
    """
    field 写错不会报错，只会被 derive_question_id 静默降级成文本哈希 id——
    第 5 章的重问追踪就跟丢了。守卫测试是这个静默故障唯一的探测器。
    """
    from app.agents.intake_question import QUESTION_TARGET_FIELDS

    for term, specs in FOLLOWUP_RULES.items():
        for spec in specs:
            assert spec.field is None or spec.field in QUESTION_TARGET_FIELDS, (
                f"{term} 的 {spec.text!r} 指向了不存在的字段 {spec.field!r}"
            )


def test_every_spec_has_zero_or_two_to_three_options():
    """spec「模糊回复与反问的兜底档位」写死 2-3 个：1 个不算选择。"""
    for term, specs in FOLLOWUP_RULES.items():
        for spec in specs:
            assert len(spec.options) == 0 or 2 <= len(spec.options) <= 3, (
                f"{term} 的 {spec.text!r} 档位数为 {len(spec.options)}"
            )


def test_procurement_terms_are_covered():
    """姚祖怡那场卡死在"一般材料"上——知识库当时一个采购词条都没有。"""
    for term in ("一般材料", "办公采购", "非标产品", "供应商开发"):
        assert term in FOLLOWUP_RULES
    assert match_ambiguous_terms("招个采购，主要管一般材料") == ["一般材料"]


def test_fallback_options_never_empty_for_any_profile_field():
    """spec「领域外的字段也要有兜底」：不得因为知识库未命中而退回空话。

    覆盖两条路径：不带 matched_terms（Task 5 之前的调用方式）与带
    matched_terms（每个字段用自己在 FOLLOWUP_RULES 里能匹配到的术语）都必须
    落在 2-3 之间——term-aware 之后契约不能只对一条路径成立。
    """
    from app.agents.ecu_knowledge import fallback_options_for_field
    from app.agents.intake_question import QUESTION_TARGET_FIELDS

    for name in QUESTION_TARGET_FIELDS:
        options = fallback_options_for_field(name)
        assert 2 <= len(options) <= 3, f"{name} 的兜底档位数为 {len(options)}"

        matched = [
            term for term, specs in FOLLOWUP_RULES.items() if any(s.field == name for s in specs)
        ]
        options_with_terms = fallback_options_for_field(name, tuple(matched))
        assert 2 <= len(options_with_terms) <= 3, (
            f"{name} 带 matched_terms={matched!r} 时兜底档位数为 {len(options_with_terms)}"
        )


def test_library_options_are_scoped_to_the_matched_term_not_declaration_order():
    """
    review 发现：core_skills 有三个词条竞争（驱动开发/算法开发/供应商开发），
    不看 matched_terms 只按 FOLLOWUP_RULES 声明顺序取第一条命中的，会把供应商
    开发相关的对话错配成"CAN-FD / LIN / 车载以太网"这类驱动总线档位——域选
    错了，不是没有选项。这里证明按 matched_terms 限定后能取到供应商开发自己
    的档位，而不是声明顺序在"驱动开发"里第一条命中的档位。
    """
    from app.agents.ecu_knowledge import fallback_options_for_field, library_options_for_field

    supplier_options = tuple(
        spec.options for spec in FOLLOWUP_RULES["供应商开发"] if spec.field == "core_skills"
    )[0]
    driver_options = tuple(
        spec.options for spec in FOLLOWUP_RULES["驱动开发"] if spec.field == "core_skills"
    )[0]
    assert supplier_options != driver_options

    assert (
        library_options_for_field("core_skills", ("供应商开发",)) == supplier_options
    )
    assert (
        fallback_options_for_field("core_skills", ("供应商开发",)) == supplier_options
    )


def test_library_options_scoped_to_each_procurement_term_for_project_experience():
    """
    project_experience_requirement 同样有三个词条竞争（一般材料/办公采购/
    非标产品）。之前只有"一般材料"能被取到（声明顺序第一），"办公采购"与
    "非标产品"的档位永远够不到——这里逐一证明三个词条各自的档位都可达。
    """
    from app.agents.ecu_knowledge import library_options_for_field

    for term in ("一般材料", "办公采购", "非标产品"):
        expected = tuple(
            spec.options
            for spec in FOLLOWUP_RULES[term]
            if spec.field == "project_experience_requirement"
        )[0]
        actual = library_options_for_field("project_experience_requirement", (term,))
        assert actual == expected, f"{term} 命中时应取到自己的档位，实际取到 {actual!r}"


def test_library_options_fall_through_to_generic_without_matched_terms():
    """
    没有 matched_terms、或匹配到的术语里没有登记这个字段时，不得回退到跨术语
    的第一条命中——那正是本次要修的坑。必须落回 GENERIC_FIELD_OPTIONS，
    而不是任意一个域的"确信但答错"的档位。
    """
    from app.agents.ecu_knowledge import (
        GENERIC_FIELD_OPTIONS,
        fallback_options_for_field,
        library_options_for_field,
    )

    assert library_options_for_field("core_skills") == ()
    assert library_options_for_field("core_skills", ()) == ()
    assert library_options_for_field("project_experience_requirement", ("嵌入式开发",)) == ()

    assert fallback_options_for_field("core_skills") == GENERIC_FIELD_OPTIONS["core_skills"]
    assert (
        fallback_options_for_field("project_experience_requirement")
        == GENERIC_FIELD_OPTIONS["project_experience_requirement"]
    )


def test_fallback_options_for_unknown_field_include_a_negative_choice():
    """含"无要求 / 不限"这类明确的否定档位，否则用户被逼着在三个"要"里挑一个。"""
    from app.agents.ecu_knowledge import fallback_options_for_field

    options = fallback_options_for_field(None)
    assert 2 <= len(options) <= 3
    assert any("无要求" in option or "不限" in option for option in options)
