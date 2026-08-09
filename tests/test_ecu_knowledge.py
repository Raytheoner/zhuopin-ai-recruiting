from app.agents.ecu_knowledge import FOLLOWUP_RULES, match_ambiguous_terms


def test_matches_known_ambiguous_term():
    matches = match_ambiguous_terms("要个做嵌入式开发的，能写驱动")
    assert "嵌入式开发" in matches


def test_no_match_for_unrelated_text():
    assert match_ambiguous_terms("今天天气不错") == []


def test_every_rule_has_at_most_three_questions():
    for term, questions in FOLLOWUP_RULES.items():
        assert 1 <= len(questions) <= 3, f"{term} 的追问数超过每轮上限"
