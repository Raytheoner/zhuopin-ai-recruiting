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
    """spec「领域外的字段也要有兜底」：不得因为知识库未命中而退回空话。"""
    from app.agents.ecu_knowledge import fallback_options_for_field
    from app.agents.intake_question import QUESTION_TARGET_FIELDS

    for name in QUESTION_TARGET_FIELDS:
        options = fallback_options_for_field(name)
        assert 2 <= len(options) <= 3, f"{name} 的兜底档位数为 {len(options)}"


def test_fallback_options_for_unknown_field_include_a_negative_choice():
    """含"无要求 / 不限"这类明确的否定档位，否则用户被逼着在三个"要"里挑一个。"""
    from app.agents.ecu_knowledge import fallback_options_for_field

    options = fallback_options_for_field(None)
    assert 2 <= len(options) <= 3
    assert any("无要求" in option or "不限" in option for option in options)
