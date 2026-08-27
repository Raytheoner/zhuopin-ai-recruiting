import pytest

from app.agents.intake_question import (
    IntakeQuestion,
    QuestionLedgerEntry,
    build_question_ledger,
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


def test_from_payload_ignores_conflicting_supplied_question_id_when_field_present():
    """
    from_payload 必须重新派生 question_id，不能采信 payload 里带来的值——
    否则模型（或任何不可信来源）就能通过这个公共入口伪造 id，
    而这正是 question_id 由系统派生这条地基要堵的口子。
    """
    restored = IntakeQuestion.from_payload(
        {"text": "招几个人？", "field": "headcount", "question_id": "模型自己编的-q1"}
    )
    assert restored.question_id == "headcount"


def test_from_payload_ignores_conflicting_supplied_question_id_when_field_missing():
    text = "具体车型与量产时间是？"
    expected = derive_question_id(None, text)
    restored = IntakeQuestion.from_payload({"text": text, "question_id": "模型自己编的-q2"})
    assert restored.question_id == expected
    assert restored.question_id != "模型自己编的-q2"


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


def test_unknown_field_degrades_to_text_hash():
    """
    模型幻觉出一个不存在的字段名时按"无 field"降级。
    不降级的后果不是脏数据，是判定失效：每轮一个新 id → 每轮都被判成有产出
    → MAX_ROUNDS 的有产出轮计数当场归零（第 3 章 3.9）。
    """
    from app.agents.intake_question import derive_question_id

    text = "要哪个 ASIL 等级？"
    assert derive_question_id("functional_safety_level", text) == derive_question_id(None, text)


def test_unknown_field_does_not_raise():
    """降级而非报错——单元 A 已确立的基调，一个野字段不该炸掉整轮采集。"""
    from app.agents.intake_question import derive_question_id

    assert derive_question_id("完全不存在的字段", "随便问一句？").startswith("free:")


def test_system_managed_field_is_not_a_valid_question_target():
    """unspecified_fields 由系统填，不该成为追问目标。"""
    from app.agents.intake_question import QUESTION_TARGET_FIELDS, derive_question_id

    assert "unspecified_fields" not in QUESTION_TARGET_FIELDS
    assert derive_question_id("unspecified_fields", "哪些字段没定？").startswith("free:")


def test_metrics_count_null_and_unknown_fields():
    """8.1 回放要看降级比例，所以计数必须真的在累计。"""
    from app.agents.intake_question import (
        derive_question_id,
        question_id_metrics,
        reset_question_id_metrics,
    )

    reset_question_id_metrics()
    derive_question_id("headcount", "招几个人？")
    derive_question_id(None, "车型是？")
    derive_question_id("mcu_familly", "MCU 平台族是？")  # 拼错

    metrics = question_id_metrics()
    assert metrics["total"] == 3
    assert metrics["null_field"] == 1
    assert metrics["unknown_field"] == 1
    assert metrics["unknown_field:mcu_familly"] == 1
    reset_question_id_metrics()


def test_unknown_field_metric_breakdown_caps_distinct_names():
    """
    常驻进程 + 模型自由生成的野字段名 = 无界内存增长点。明细超过上限后必须
    落进溢出桶，而不是继续按字段名开新 key；聚合计数 unknown_field 不受
    这个上限影响，必须保持精确。
    """
    from app.agents import intake_question
    from app.agents.intake_question import (
        derive_question_id,
        question_id_metrics,
        reset_question_id_metrics,
    )

    reset_question_id_metrics()
    cap = intake_question._MAX_UNKNOWN_FIELD_NAMES
    overflow_key = intake_question._UNKNOWN_FIELD_OVERFLOW_KEY

    for i in range(cap + 5):
        derive_question_id(f"野字段_{i}", "随便问一句？")

    metrics = question_id_metrics()
    assert metrics["unknown_field"] == cap + 5
    assert metrics[overflow_key] == 5
    distinct_name_keys = [
        key
        for key in metrics
        if key.startswith("unknown_field:") and key != overflow_key
    ]
    assert len(distinct_name_keys) == cap
    reset_question_id_metrics()


def test_render_questions_text_includes_options_with_ai_disclosure():
    """
    档位在纯文本通道里也要看得见，且必须带 AI 建议标识
    （《AI 生成合成内容标识办法》）。第 4 章的可点选控件合并之前，这是用户
    唯一能看到档位的地方。
    """
    questions = [
        IntakeQuestion(
            text="要哪个 ASIL 等级？",
            question_id="functional_safety",
            field="functional_safety",
            options=("ASIL-B", "ASIL-D", "无要求"),
        )
    ]

    rendered = render_questions_text(questions)

    assert rendered.splitlines()[0] == "要哪个 ASIL 等级？"
    assert "AI 建议选项" in rendered
    for option in ("ASIL-B", "ASIL-D", "无要求"):
        assert option in rendered


def test_render_questions_text_omits_options_line_when_empty():
    questions = [IntakeQuestion(text="具体车型与量产时间是？", question_id="free:x")]
    assert render_questions_text(questions) == "具体车型与量产时间是？"


def test_empty_ledger_for_a_job_that_has_never_asked_anything():
    assert build_question_ledger([], answered_fields=frozenset()) == {}
    assert build_question_ledger([[]], answered_fields=frozenset()) == {}


def test_ledger_counts_one_ask_per_round_the_question_appeared_in():
    """同一个 question_id 在第 0、2 轮各问过一次 = 问了 2 次、重问 1 次。"""
    rounds = [
        [{"text": "功能安全等级？", "field": "functional_safety"}],
        [{"text": "招几个人？", "field": "headcount"}],
        [{"text": "ASIL 这块到底要不要？", "field": "functional_safety"}],
    ]

    ledger = build_question_ledger(rounds, answered_fields=frozenset({"headcount"}))

    fs = ledger["functional_safety"]
    assert fs.ask_count == 2
    assert fs.reask_count == 1
    assert fs.first_asked_round == 0
    assert fs.last_asked_round == 2
    assert fs.is_answered is False
    assert ledger["headcount"].ask_count == 1
    assert ledger["headcount"].reask_count == 0
    assert ledger["headcount"].is_answered is True


def test_partially_answered_round_marks_only_the_answered_subquestion():
    """spec Scenario「部分回答」：问了两个，只答了一个，另一个保持已问未答。

    这正是 2494103e 第 3-4 轮的形状（IATF 16949 答了、ISO 26262 没答）。
    """
    rounds = [
        [
            {"text": "是否要求熟悉 IATF 16949？", "field": "core_skills"},
            {"text": "是否要求熟悉 ISO 26262？", "field": "functional_safety"},
        ]
    ]

    ledger = build_question_ledger(rounds, answered_fields=frozenset({"core_skills"}))

    assert ledger["core_skills"].is_answered is True
    assert ledger["functional_safety"].is_answered is False


def test_idle_round_never_flips_anything_to_answered():
    """spec Scenario「空转轮不改变已答状态」。

    空转轮的定义是"本轮未产出任何字段"，也就是 answered_fields 相对上一轮
    一个都没多。台账只从 answered_fields 读"答没答"，所以这条不是靠一段
    "如果是空转轮就……"的分支成立的，而是**结构上不可能违反**：没有新字段
    进来，就没有任何 entry 会翻成已答。
    """
    rounds = [
        [{"text": "功能安全等级？", "field": "functional_safety"}],
        [{"text": "ASIL 这块要不要？", "field": "functional_safety"}],
    ]
    answered = frozenset()  # 两轮都没产出任何字段

    ledger = build_question_ledger(rounds, answered_fields=answered)

    assert ledger["functional_safety"].is_answered is False
    assert ledger["functional_safety"].ask_count == 2


def test_free_text_question_is_never_marked_answered():
    """没有 field 的问题拿到的是 free:<hash> id，它不对应任何画像字段，
    因此永远判不出"已答"。这是 derive_question_id 已写明的降级代价：
    重问追踪只对拿得到 field 的问题成立。"""
    rounds = [[{"text": "具体车型与量产时间是怎么安排的？"}]]

    ledger = build_question_ledger(rounds, answered_fields=frozenset({"sop_projects"}))

    (entry,) = ledger.values()
    assert entry.question_id.startswith("free:")
    assert entry.field is None
    assert entry.is_answered is False


def test_first_asked_round_is_the_round_a_question_first_appears_in_not_always_zero():
    """一个子问题若不是从第 0 轮就开始问，first_asked_round 必须记它真正
    第一次出现的那一轮，不能被硬编码成 0。"""
    rounds = [
        [{"text": "招几个人？", "field": "headcount"}],
        [{"text": "要求几年经验？", "field": "experience_years"}],
    ]

    ledger = build_question_ledger(rounds, answered_fields=frozenset())

    assert ledger["experience_years"].first_asked_round == 1
    assert ledger["experience_years"].last_asked_round == 1


def test_ledger_entry_field_is_none_when_question_id_degrades_for_unknown_field():
    """payload 里的 field 是野字段（不在 QUESTION_TARGET_FIELDS）时，
    derive_question_id 会降级成 free:<hash>，但 IntakeQuestion.from_payload
    原样保留那个野 field 名。台账的 entry.field 必须跟着 question_id 走、
    不能把这个不存在的字段名当成真实字段记下来——否则「是否已答」会拿一个
    野名字去和真实字段表对，产生假阳性。"""
    rounds = [[{"text": "这是个模型幻觉出来的问题", "field": "totally_made_up_field"}]]

    ledger = build_question_ledger(rounds, answered_fields=frozenset())

    (entry,) = ledger.values()
    assert entry.question_id.startswith("free:")
    assert entry.field is None


def test_duplicate_ids_inside_one_round_count_as_one_ask():
    """同一轮内撞 id 只能算问了一次。

    今天 _to_intake_questions 会在同一轮内去重，走不到这条路径；但历史行
    （.51 上 2026-08-19 之前写的）不受那个去重保护，台账必须自己扛住，
    否则一行脏数据就能让某个子问题凭空多出一次"重问"、提前触顶。
    """
    rounds = [
        [
            {"text": "招几个人？", "field": "headcount"},
            {"text": "这次计划招几位？", "field": "headcount"},
        ]
    ]

    ledger = build_question_ledger(rounds, answered_fields=frozenset())

    assert ledger["headcount"].ask_count == 1


def test_legacy_bare_string_rows_do_not_crash_the_ledger():
    """.51 现网 2026-08-18 之前的 outbox/台账行里 questions 是裸字符串数组。
    台账走 IntakeQuestion.from_payload 的同一条归一化路径，历史行照样能算。"""
    rounds = [["功能安全等级（ASIL）上有什么要求？"]]

    ledger = build_question_ledger(rounds, answered_fields=frozenset())

    (entry,) = ledger.values()
    assert entry.question_id.startswith("free:")
    assert entry.ask_count == 1


def test_ledger_entry_is_frozen():
    """台账条目会被多处读到，可变对象会让"谁改了它"变成一个要排查的问题
    ——与 IntakeQuestion 用 frozen=True 是同一条理由。"""
    import dataclasses

    entry = QuestionLedgerEntry(
        question_id="headcount",
        field="headcount",
        ask_count=1,
        first_asked_round=0,
        last_asked_round=0,
        is_answered=False,
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.ask_count = 2
