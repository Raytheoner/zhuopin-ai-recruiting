from app.agents.intake_question import (
    IntakeQuestion,
    derive_question_id,
    normalize_question_payload,
    render_questions_text,
)


def test_question_id_is_the_target_field():
    """一个字段最多同时挂一个未答问题，所以 id 就是字段名（design.md 决策 2）。"""
    assert derive_question_id("functional_safety", "要哪个 ASIL 等级？") == "functional_safety"


def test_question_id_ignores_wording_when_field_present():
    """换措辞不改标识（intake-question-tracking「换措辞不改变标识」）。"""
    first = derive_question_id("functional_safety", "是否有功能安全等级要求？")
    second = derive_question_id("functional_safety", "这个岗位需要 ASIL 几？")
    assert first == second


def test_question_id_falls_back_to_text_hash_when_field_missing():
    question_id = derive_question_id(None, "具体车型与量产时间是？")
    assert question_id.startswith("free:")
    assert derive_question_id("", "具体车型与量产时间是？") == question_id


def test_fallback_id_ignores_whitespace_differences():
    assert derive_question_id(None, "具体车型与量产时间是？") == derive_question_id(
        None, " 具体车型 与量产时间是？ "
    )


def test_fallback_id_changes_when_wording_changes():
    """
    降级不是等价方案，这条测试把代价写下来：field 缺失时换措辞就追踪不到了。
    第 5 章的重问追踪只对拿得到 field 的问题成立。
    """
    assert derive_question_id(None, "车型是？") != derive_question_id(None, "哪个车型？")


def test_payload_round_trip():
    question = IntakeQuestion(
        text="要哪个 ASIL 等级？",
        question_id="functional_safety",
        field="functional_safety",
        options=("ASIL-B", "ASIL-D", "无要求"),
        allow_free_text=True,
        is_reask=True,
    )

    restored = IntakeQuestion.from_payload(question.to_payload())

    assert restored == question
    assert question.to_payload()["options"] == ["ASIL-B", "ASIL-D", "无要求"]  # JSON 友好


def test_from_payload_fills_missing_id_and_defaults():
    restored = IntakeQuestion.from_payload({"text": "招几个人？", "field": "headcount"})

    assert restored.question_id == "headcount"
    assert restored.options == ()
    assert restored.allow_free_text is True
    assert restored.is_reask is False


def test_render_questions_text_joins_with_newline():
    questions = [
        IntakeQuestion(text="A？", question_id="a"),
        IntakeQuestion(text="B？", question_id="b"),
    ]
    assert render_questions_text(questions) == "A？\nB？"


def test_render_questions_text_marks_reask():
    """
    重问标注的渲染在这里就位，但本单元不会有 is_reask=True 的问题产生
    （判定属第 5 章 tasks 5.4）。先放渲染是为了让第 5 章只改判定、不动渲染。
    """
    questions = [IntakeQuestion(text="是否需要 ISO 26262？", question_id="functional_safety", is_reask=True)]
    rendered = render_questions_text(questions)
    assert "是否需要 ISO 26262？" in rendered
    assert rendered != "是否需要 ISO 26262？"  # 带了可见的重问前缀


def test_render_empty_questions_is_empty_string():
    assert render_questions_text([]) == ""


def test_normalize_question_payload_upgrades_legacy_string_list():
    """
    .51 现网 data/demo.db 的 outbox 里存着 2026-08-18 及之前写下的裸字符串问题。
    GET /api/jobs/{id} 会把这些历史行原样读回来当响应，新前端按对象访问
    q.text 会在真实数据上直接崩——和 design.md 决策 10 同一类"只在服务器上炸"
    的坑：本地测试库全是新写的行，永远走不到这条路径。
    """
    legacy = {"questions": ["是否涉及 AUTOSAR？", "MCU 平台族是？"]}

    normalized = normalize_question_payload(legacy)

    assert [q["text"] for q in normalized["questions"]] == [
        "是否涉及 AUTOSAR？",
        "MCU 平台族是？",
    ]
    assert all(q["question_id"] for q in normalized["questions"])
    assert all(q["options"] == [] for q in normalized["questions"])
    assert normalized["questions_text"] == "是否涉及 AUTOSAR？\nMCU 平台族是？"


def test_normalize_question_payload_preserves_other_keys_and_is_idempotent():
    payload = {
        "questions": [{"text": "招几个人？", "field": "headcount", "options": ["1", "2-3"]}],
        "unspecified_fields": ["toolchain"],
    }

    once = normalize_question_payload(payload)
    twice = normalize_question_payload(once)

    assert once == twice
    assert once["unspecified_fields"] == ["toolchain"]
    assert once["questions"][0]["question_id"] == "headcount"
    assert once["questions"][0]["options"] == ["1", "2-3"]


def test_normalize_question_payload_handles_missing_questions_key():
    normalized = normalize_question_payload({"type": "confirmation_prompt"})
    assert normalized["questions"] == []
    assert normalized["questions_text"] == ""
