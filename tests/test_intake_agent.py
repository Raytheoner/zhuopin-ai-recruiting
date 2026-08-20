import json
from dataclasses import dataclass, field

from app.agents.intake_agent import SYSTEM_PROMPT, run_intake_turn
from app.llm.gateway import LLMGateway


@dataclass
class FakeMessage:
    content: str


@dataclass
class FakeChoice:
    message: FakeMessage


@dataclass
class FakeResponse:
    choices: list[FakeChoice]
    usage: object = None
    model: str | None = None


class FakeChatCompletions:
    def __init__(self, responses: list[str], response_model: str | None = None):
        self._responses = list(responses)
        self._response_model = response_model
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content = self._responses.pop(0)
        return FakeResponse(
            choices=[FakeChoice(message=FakeMessage(content=content))],
            model=self._response_model,
        )


class FakeChat:
    def __init__(self, responses, response_model: str | None = None):
        self.completions = FakeChatCompletions(responses, response_model=response_model)


class FakeOpenAIClient:
    def __init__(self, responses, response_model: str | None = None):
        self.chat = FakeChat(responses, response_model=response_model)


def make_gateway(responses: list[str], response_model: str | None = None) -> LLMGateway:
    return LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=FakeOpenAIClient(responses, response_model=response_model),
    )


def test_unrelated_message_returns_guidance_and_not_complete():
    gateway = make_gateway(
        [json.dumps({"is_job_related": False, "questions": [], "profile_patch": {}})]
    )

    result = run_intake_turn(gateway, history=[{"role": "user", "content": "今天天气不错"}], round_count=0)

    assert result.is_job_related is False
    assert result.is_complete is False
    assert result.questions  # 引导语非空
    # 离题轮没有任何产出（profile_patch 恒为 {}），不能落成默认值
    # is_productive=True，否则连续几条离题消息就能耗光 MAX_ROUNDS
    # （Task 4 review 发现1）。引导语确实下发给了用户，已问台账必须记它。
    assert result.is_productive is False
    assert result.asked_questions == result.questions


def test_job_related_message_returns_followup_questions():
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": ["是否涉及 AUTOSAR？"],
                    "profile_patch": {"job_title": "嵌入式软件工程师"},
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "要个做嵌入式开发的"}],
        round_count=0,
    )

    assert result.is_job_related is True
    assert [q.text for q in result.questions] == ["是否涉及 AUTOSAR？"]
    assert result.profile_patch == {"job_title": "嵌入式软件工程师"}
    assert result.is_complete is False


def test_round_limit_forces_completion_with_unspecified_fields():
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": ["还差 mcu_family"],
                    "profile_patch": {},
                    "unspecified_fields": ["mcu_family"],
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "要个嵌入式的"}],
        round_count=5,
    )

    assert result.is_complete is True
    assert result.questions == []
    assert "mcu_family" in result.unspecified_fields


def test_questions_capped_at_three_even_if_model_returns_more():
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": ["Q1", "Q2", "Q3", "Q4", "Q5"],
                    "profile_patch": {},
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway, history=[{"role": "user", "content": "要个嵌入式的"}], round_count=1
    )

    assert len(result.questions) == 3


def test_repeating_previous_question_forces_completion_before_round_limit():
    """
    2026-08-10 真实环境试跑发现：用户对"CP 还是 AP"这种二选一问题回答"是的"时，
    profile_patch 提不出任何字段，ECU 知识库的追问建议又逐轮原样重新注入，模型在
    temperature=0 下生成了和上一轮几乎一字不差的问题——不能等到 MAX_ROUNDS（5）轮
    才发现自己在打转，那之前每一轮都是把同一组问题原样再发一次给用户。

    这里断言：当这一轮生成的问题和上一轮 assistant 说的内容只有空白差异地相同时，
    不应该把它再发一次——应该提前判定 is_complete，把没问出来的字段留给
    unspecified_fields，而不是重复发问。
    """
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": ["AUTOSAR具体需要CP还是AP？", "MCU平台族是？"],
                    "profile_patch": {},
                    "unspecified_fields": ["autosar_experience", "mcu_family"],
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway,
        history=[
            {"role": "user", "content": "要个懂AUTOSAR的"},
            {
                "role": "assistant",
                "content": "AUTOSAR 具体需要 CP 还是 AP？\nMCU 平台族是？",
            },
            {"role": "user", "content": "是的"},
        ],
        round_count=1,
    )

    assert result.is_complete is True
    assert result.questions == []
    assert "autosar_experience" in result.unspecified_fields


def test_repeating_a_question_from_an_earlier_non_adjacent_round_is_detected():
    """
    2026-08-16 姚祖怡试跑反馈"重复问了同一件事情"——`_repeats_last_assistant_turn`
    只跟"上一轮" assistant 内容比对，一旦中间隔了一轮问了别的问题，第 1 轮问过的
    问题在第 3 轮被模型重新问出来，跟"上一轮"（第 2 轮）文本不同，检测不到，会
    被原样再发一次给用户——这正是"重复问同一件事"的另一种真实成因，不是只有
    "连续两轮一字不差"这一种。

    这里断言：即使重复的是更早一轮（不是紧邻上一轮）问过的问题，也要判定为卡住，
    提前完成而不是把已经问过的问题再发一次。
    """
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": ["AUTOSAR 具体需要 CP 还是 AP？"],
                    "profile_patch": {},
                    "unspecified_fields": ["autosar_experience"],
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway,
        history=[
            {"role": "user", "content": "要个懂AUTOSAR的"},
            {"role": "assistant", "content": "AUTOSAR 具体需要 CP 还是 AP？"},
            {"role": "user", "content": "是的"},
            {"role": "assistant", "content": "招聘人数是？"},
            {"role": "user", "content": "3人"},
        ],
        round_count=2,
    )

    assert result.is_complete is True
    assert result.questions == []
    assert "autosar_experience" in result.unspecified_fields


def test_new_question_different_from_previous_turn_is_not_treated_as_stuck():
    """反向证明：只要这轮问题和上一轮不同，就不该被误判为卡住而提前结束。"""
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": ["招聘人数是？"],
                    "profile_patch": {"autosar_experience": ["CP"]},
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway,
        history=[
            {"role": "user", "content": "要个懂AUTOSAR的"},
            {"role": "assistant", "content": "AUTOSAR 具体需要 CP 还是 AP？"},
            {"role": "user", "content": "CP"},
        ],
        round_count=1,
    )

    assert result.is_complete is False
    assert [q.text for q in result.questions] == ["招聘人数是？"]


def _sent_prompt(gateway: LLMGateway) -> str:
    """把这次调用真正发给模型的 system+user 文本拼起来，用于断言"某段内容确实进了 prompt"。"""
    call = gateway._client.chat.completions.calls[0]
    return "\n".join(m["content"] for m in call["messages"])


def test_matched_ecu_terms_inject_curated_followups_into_prompt():
    """
    回归测试（review Important 发现3）：ecu_knowledge.py 里的 FOLLOWUP_RULES 是本项目
    唯一沉淀下来的 ECU 领域专家知识（CP/AP、Aurix/S32K、ASIL、UDS），修复前
    run_intake_turn 压根没 import 过它——prompt 只写了一句"基于 ECU 行业知识"，
    实际全靠模型的通用常识猜，整个知识库是死代码。

    这里断言：用户消息命中"嵌入式开发"时，FOLLOWUP_RULES["嵌入式开发"] 里的问题
    原文必须出现在真正发给模型的 prompt 里——证明接线是真的，不是"存在但没人用"。
    """
    from app.agents.ecu_knowledge import FOLLOWUP_RULES

    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": ["是否涉及 AUTOSAR（CP/AP）？"],
                    "profile_patch": {},
                }
            )
        ]
    )

    run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "要个做嵌入式开发的工程师"}],
        round_count=0,
    )

    prompt = _sent_prompt(gateway)
    for spec in FOLLOWUP_RULES["嵌入式开发"]:
        assert spec.text in prompt, f"命中术语的领域追问 {spec.text!r} 没有进入 prompt"
        # 3.1 起 prompt 里还要带上目标字段与候选档位，否则模型只看得到问题文本、
        # 档位仍然要自己编——那正是决策 4 要堵的编造面。
        assert spec.field is None or spec.field in prompt
        for option in spec.options:
            assert option in prompt


def test_unmatched_text_does_not_inject_followups():
    """反向证明：没命中术语时不应该硬塞无关追问，避免把知识库变成噪音。"""
    from app.agents.ecu_knowledge import FOLLOWUP_RULES

    gateway = make_gateway(
        [json.dumps({"is_job_related": True, "questions": ["岗位名称是？"], "profile_patch": {}})]
    )

    run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "要招个人"}],
        round_count=0,
    )

    prompt = _sent_prompt(gateway)
    for spec in FOLLOWUP_RULES["驱动开发"]:
        assert spec.text not in prompt


def test_only_user_turns_are_matched_for_ambiguous_terms():
    """
    助手自己问出的"是否有功能安全等级（ASIL）要求？"里含有"功能安全"这个术语；
    如果匹配范围包含 assistant 轮次，助手问过一次之后就会永远自我触发同一条规则。
    只匹配 user 轮次，避免这种自激。
    """
    from app.agents.ecu_knowledge import FOLLOWUP_RULES

    gateway = make_gateway(
        [json.dumps({"is_job_related": True, "questions": ["还有别的要求吗？"], "profile_patch": {}})]
    )

    run_intake_turn(
        gateway,
        history=[
            {"role": "user", "content": "要招个人"},
            {"role": "assistant", "content": "是否有功能安全等级（ASIL）要求？"},
            {"role": "user", "content": "暂时没有"},
        ],
        round_count=1,
    )

    prompt = _sent_prompt(gateway)
    assert FOLLOWUP_RULES["功能安全"][1].text not in prompt


def test_accumulated_profile_is_visible_to_model():
    """
    SYSTEM_PROMPT 要求"不要重复历史已有字段"，那么已经确定的字段就必须真的出现在
    prompt 里，否则这条指令模型无从遵守（review Critical 发现1 的另一半）。
    """
    gateway = make_gateway(
        [json.dumps({"is_job_related": True, "questions": ["Q"], "profile_patch": {}})]
    )

    run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "要个嵌入式的"}],
        round_count=1,
        profile_patch_accumulated={"job_title": "嵌入式软件工程师"},
    )

    assert "嵌入式软件工程师" in _sent_prompt(gateway)


def test_prompt_declares_job_profile_field_names_and_enum_values():
    """
    回归测试（review Important 发现2 的根因侧）：profile_patch 的键值最终要过
    JobProfile 的校验，但修复前 prompt 里从没出现过 JobProfile 的字段名、类型和
    枚举取值——模型只能靠猜，猜错就在 confirm 那一步炸掉。
    """
    gateway = make_gateway(
        [json.dumps({"is_job_related": True, "questions": ["Q"], "profile_patch": {}})]
    )

    run_intake_turn(
        gateway, history=[{"role": "user", "content": "要个嵌入式的"}], round_count=0
    )

    prompt = _sent_prompt(gateway)
    assert "headcount" in prompt
    assert "functional_safety" in prompt
    assert "ASIL-B" in prompt  # 枚举取值要原样列出，避免模型写成 "ASIL B"
    assert "core_skills" in prompt


def test_prompt_instructs_offering_concrete_options_for_vague_replies():
    """
    2026-08-18 试运行反馈复盘发现的真实产品缺口：业务经理回"这些我不太了解，
    你有什么建议"这类模糊表态时，模型只回了句"您可以按建议补充……我来帮您
    整理"——没给出任何具体选项，profile_patch 也是空的，白白浪费一整轮
    （round_count 照常 +1，但没有任何新信息进来），导致后面轮次不够用，
    toolchain / soft_skill_keywords / functional_safety 这些字段最终都
    进了 unspecified_fields。

    修复是 prompt 层面的：要求模型遇到这种模糊回复时，必须在 questions 里给
    2-3 个具体可选项（而不是空话），同时明确禁止模型因为用户说"你决定"就
    自己把猜的值写进 profile_patch——画像里的硬性要求必须由用户明确选定，
    不能是模型代替业务经理做的决定（合规红线：主观判断不能变成硬门槛）。
    """
    prompt = SYSTEM_PROMPT
    assert "不知道" in prompt or "模糊" in prompt
    assert "具体" in prompt and ("可选项" in prompt or "选项" in prompt)
    assert "不能" in prompt and "profile_patch" in prompt


def test_vague_reply_with_concrete_options_response_is_not_treated_as_stuck():
    """
    回归测试：模型改进后遇到模糊回复会给出一组新的具体选项（而不是空话）。
    这组新问题不该被 `_repeats_earlier_assistant_turn` 误判成"卡住了"——
    它们是新内容，只是回应的是同一个"用户没给出具体信息"的困境。
    """
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [
                        "行业内常见档位供参考：ASIL-B（多数岗位）或 ASIL-C 及以上（核心安全岗位），选哪个？",
                        "工具链常见组合是 Keil + Lauterbach，或者你们有指定工具链？",
                    ],
                    "profile_patch": {},
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway,
        history=[
            {"role": "user", "content": "要个懂功能安全的"},
            {"role": "assistant", "content": "功能安全等级要求是？工具链用什么？"},
            {"role": "user", "content": "这些我不太了解，你有什么建议"},
        ],
        round_count=1,
    )

    assert result.is_complete is False
    assert len(result.questions) == 2


def test_plain_string_questions_degrade_to_text_only_questions():
    """
    模型退化成只给一句文本时降级，而不是校验失败重试三次
    （design.md 风险表第 1 条）。这条路径是真实会走到的：本 schema 含自由
    dict（profile_patch），网关始终走 json_object 模式，供应商不校验形状。
    """
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": ["是否涉及 AUTOSAR？"],
                    "profile_patch": {},
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway, history=[{"role": "user", "content": "要个做嵌入式开发的"}], round_count=0
    )

    assert len(result.questions) == 1
    question = result.questions[0]
    assert question.text == "是否涉及 AUTOSAR？"
    assert question.field is None
    assert question.options == ()
    assert question.allow_free_text is True
    assert question.is_reask is False
    assert question.question_id.startswith("free:")


def test_structured_question_carries_field_and_options():
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [
                        {
                            "text": "要哪个 ASIL 等级？",
                            "field": "functional_safety",
                            "options": ["ASIL-B", "ASIL-D", "无"],
                            "allow_free_text": True,
                        }
                    ],
                    "profile_patch": {},
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway, history=[{"role": "user", "content": "要个做功能安全的"}], round_count=0
    )

    question = result.questions[0]
    assert question.question_id == "functional_safety"
    assert question.field == "functional_safety"
    assert question.options == ("ASIL-B", "ASIL-D", "无")


def test_question_id_is_derived_by_system_even_if_model_supplies_one():
    """模型自己编的 id 必须被丢弃（design.md 决策 2）。"""
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [
                        {
                            "text": "招几个人？",
                            "field": "headcount",
                            "question_id": "模型自己编的-q1",
                        }
                    ],
                    "profile_patch": {},
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway, history=[{"role": "user", "content": "要个嵌入式"}], round_count=0
    )

    assert result.questions[0].question_id == "headcount"


def test_questions_text_comes_from_the_single_renderer():
    """
    history 里的 assistant 文本与下发给通道的文本必须同源
    （design.md 决策 1「代价」）。这条测试锁住"result 自带渲染结果"，
    让 compute_intake_turn 没有理由自己再 join 一遍。
    """
    from app.agents.intake_question import render_questions_text

    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [
                        {"text": "要哪个 ASIL 等级？", "field": "functional_safety"},
                        {"text": "招几个人？", "field": "headcount"},
                    ],
                    "profile_patch": {},
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway, history=[{"role": "user", "content": "要个嵌入式"}], round_count=0
    )

    assert result.questions_text == render_questions_text(result.questions)
    assert result.questions_text == "要哪个 ASIL 等级？\n招几个人？"


def test_result_carries_llm_latency_and_response_model():
    """
    铁律 5：配置里写的名字不算数，响应返回的才算。本单元把它透到 agent 层
    （第 7 章才落库），时序则由第 6 章落库。
    """
    gateway = make_gateway(
        [
            json.dumps(
                {"is_job_related": True, "questions": [], "profile_patch": {"headcount": 2}}
            )
        ],
        response_model="deepseek-chat-241226",
    )

    result = run_intake_turn(
        gateway, history=[{"role": "user", "content": "要两个嵌入式"}], round_count=0
    )

    assert result.llm_latency_ms >= 0
    assert result.llm_response_model == "deepseek-chat-241226"


def test_system_prompt_requires_one_answerable_subquestion_per_item():
    """
    tasks 2.4：一个问题条目只能承载一个可独立作答的子问题，且要给反例。
    反例用真实事故里的那一对（2494103e 第 3 轮把 IATF 16949 与 ISO 26262
    打包成一句，用户只答了前者，第 4 轮被换措辞重问）。
    """
    assert "只能承载一个" in SYSTEM_PROMPT
    assert "IATF 16949" in SYSTEM_PROMPT
    assert "ISO 26262" in SYSTEM_PROMPT
    # 问题对象的形状要用中文写清楚：json_object 模式下嵌套模型在 schema 里是
    # $ref，不能只指望模型自己解引用
    assert "allow_free_text" in SYSTEM_PROMPT
    assert "question_id" in SYSTEM_PROMPT  # 明确告诉模型不要自己编 id


# ---------------------------------------------------------------------------
# 第 3 章：模糊回复兜底与预算口径
# ---------------------------------------------------------------------------

from app.agents.intake_agent import MAX_ROUNDS, MAX_TOTAL_ROUNDS, is_vague_reply  # noqa: E402
from app.agents.intake_question import IntakeQuestion  # noqa: E402


def test_is_vague_reply_hits_marker_words():
    assert is_vague_reply("这些我不太了解，你有什么建议")
    assert is_vague_reply("你决定吧")
    assert is_vague_reply("随便")
    assert is_vague_reply("不理解你想问的问题，我不知道怎么回答")


def test_is_vague_reply_accepts_real_answers():
    """误判的代价是多给一组选项，但真答案不该被判成模糊。"""
    assert not is_vague_reply("要 ASIL-D，必须有 AUTOSAR CP 经验")
    assert not is_vague_reply("招 2 个人，本科以上")


def test_is_vague_reply_empty_text_is_not_vague():
    """第一轮之前没有用户发言，不该被当成模糊回复而提前塞档位。"""
    assert not is_vague_reply("")
    assert not is_vague_reply("   ")


def test_is_vague_reply_detects_counter_question_without_clues():
    asked = [IntakeQuestion(text="要哪个 ASIL 等级？", question_id="functional_safety")]
    assert is_vague_reply("你们公司是干嘛的？", asked_questions=asked)


def test_is_vague_reply_does_not_flag_follow_up_question_that_shares_clues():
    """追着上一轮问细节是有信息的，不是反问。"""
    asked = [IntakeQuestion(text="要哪个 ASIL 等级？", question_id="functional_safety")]
    assert not is_vague_reply("ASIL 等级和量产项目有关系吗？", asked_questions=asked)


def test_substantive_reply_ending_in_a_question_is_still_treated_as_vague():
    """
    2026-08-20 review 定论（未改算法，钉住当前行为）：这是刻意接受的权衡，不是
    潜藏 bug。反问判定不检查回复是否已带实质信息，所以一句本身有实质内容、
    但顺带反问了一句的回复也会被判成 True。宁可多给一组档位，也不漏给——
    漏给正是本单元要修的那个故障。这条误判不占用追问预算：is_productive
    (Task 4/6) 只看新字段与新 question_id，不读 is_vague_reply 的结果。
    未来若收紧这条启发式，预期本测试会变红——那条红是信号，不是回归。
    """
    asked = [IntakeQuestion(text="计划招几个人？", question_id="headcount")]
    assert is_vague_reply(
        "是要社招还是校招都可以，你说的是哪种？", asked_questions=asked
    )


def test_turn_with_new_profile_field_is_productive():
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "招几个人？", "field": "headcount"}],
                    "profile_patch": {"job_title": "嵌入式工程师"},
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "要个嵌入式工程师"}],
        round_count=0,
    )

    assert result.is_productive is True
    assert [q.question_id for q in result.asked_questions] == ["headcount"]


def test_turn_with_nothing_new_is_not_productive():
    """画像与上一轮完全相同、问出的问题此前都问过 = 空转轮。"""
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "招几个人？", "field": "headcount"}],
                    "profile_patch": {"job_title": "嵌入式工程师"},
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "嗯"}],
        round_count=2,
        profile_patch_accumulated={"job_title": "嵌入式工程师"},
        asked_question_ids_before=["headcount"],
    )

    assert result.is_productive is False


def test_new_question_alone_makes_a_turn_productive():
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "工具链上有什么要求？", "field": "toolchain"}],
                    "profile_patch": {"job_title": "嵌入式工程师"},
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "没别的了"}],
        round_count=2,
        profile_patch_accumulated={"job_title": "嵌入式工程师"},
        asked_question_ids_before=["headcount"],
    )

    assert result.is_productive is True


def test_duplicate_question_ids_in_one_round_are_deduped():
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [
                        {"text": "要哪个 ASIL 等级？", "field": "functional_safety"},
                        {"text": "功能安全有要求吗？", "field": "functional_safety"},
                    ],
                    "profile_patch": {},
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "招个功能安全工程师"}],
        round_count=0,
    )

    assert [q.question_id for q in result.questions] == ["functional_safety"]


# ---------------------------------------------------------------------------
# Task 5：兜底档位强制注入、候选档位不入画像、prompt_version 升 v4
# ---------------------------------------------------------------------------


def test_vague_reply_forces_options_onto_questions():
    """
    真实回放：`19b6ec6d` 第 4 轮。模型给了问题但没给档位（今天的行为），
    系统必须补上 2-3 个具体档位（spec「用户说不知道，系统给档位」）。
    """
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "工具链上有什么要求？", "field": "toolchain"}],
                    "profile_patch": {},
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway,
        history=[
            {"role": "user", "content": "要个底层软件开发工程师"},
            {"role": "assistant", "content": "工具链上有什么要求？"},
            {"role": "user", "content": "这些我不太了解，你有什么建议"},
        ],
        round_count=1,
    )

    assert result.questions
    for question in result.questions:
        assert 2 <= len(question.options) <= 3
        assert question.allow_free_text is True
    assert "AI 建议选项" in result.questions_text


def test_vague_reply_synthesizes_question_when_model_returns_nothing():
    """
    真实回放：`19b6ec6d` 第 4 轮模型实际回的是"我来帮您整理"式空话、一个问题
    都没问。这一轮必须由系统合成一个带档位的问题，否则 spec 的兜底在最需要它
    的那次直接落空。
    """
    gateway = make_gateway(
        [json.dumps({"is_job_related": True, "questions": [], "profile_patch": {}})]
    )

    result = run_intake_turn(
        gateway,
        history=[
            {"role": "user", "content": "要个底层软件开发工程师"},
            {"role": "user", "content": "这些我不太了解，你有什么建议"},
        ],
        round_count=1,
        profile_patch_accumulated={"job_title": "底层软件开发工程师"},
    )

    assert len(result.questions) == 1
    assert 2 <= len(result.questions[0].options) <= 3
    assert result.is_complete is False
    assert "我来帮您整理" not in result.questions_text


def test_counter_question_about_uncovered_domain_still_gets_options():
    """
    真实回放：`a478499c` 第 5 轮"一般材料是什么，你都不知道吗"。
    采购不在 ECU 知识库覆盖范围内，仍然必须给出档位——spec「领域外的字段也要
    有兜底」不允许因为知识库未命中而退回空话。
    """
    gateway = make_gateway(
        [json.dumps({"is_job_related": True, "questions": [], "profile_patch": {}})]
    )

    result = run_intake_turn(
        gateway,
        history=[
            {"role": "user", "content": "招个采购员"},
            {"role": "assistant", "content": "该岗位采购的「一般材料」指哪些品类？"},
            {"role": "user", "content": "一般材料是什么，你都不知道吗"},
        ],
        round_count=2,
        profile_patch_accumulated={"job_title": "采购员"},
    )

    assert result.questions
    assert all(2 <= len(q.options) <= 3 for q in result.questions)


def test_candidate_option_is_not_written_into_profile_when_user_defers():
    """
    合规红线：AI 不做决定。用户回"你决定吧"时，模型顺手把上一轮的候选档位写进
    profile_patch 的，必须被摘掉（spec「候选档位不得代替用户做决定」）。
    """
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [],
                    "profile_patch": {"functional_safety": "ASIL-D"},
                }
            )
        ]
    )
    previous = [
        IntakeQuestion(
            text="要哪个 ASIL 等级？",
            question_id="functional_safety",
            field="functional_safety",
            options=("ASIL-B", "ASIL-D", "无要求"),
        )
    ]

    result = run_intake_turn(
        gateway,
        history=[
            {"role": "user", "content": "招个功能安全工程师"},
            {"role": "assistant", "content": "要哪个 ASIL 等级？"},
            {"role": "user", "content": "你决定吧"},
        ],
        round_count=1,
        profile_patch_accumulated={"job_title": "功能安全工程师"},
        previous_questions=previous,
    )

    assert "functional_safety" not in result.profile_patch


def test_user_typed_option_is_kept_even_on_a_vague_turn():
    """"你决定吧，ASIL-D 也行"——用户自己打出了档位就是选定，不能摘。"""
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [],
                    "profile_patch": {"functional_safety": "ASIL-D"},
                }
            )
        ]
    )
    previous = [
        IntakeQuestion(
            text="要哪个 ASIL 等级？",
            question_id="functional_safety",
            field="functional_safety",
            options=("ASIL-B", "ASIL-D", "无要求"),
        )
    ]

    result = run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "你决定吧，ASIL-D 也行"}],
        round_count=1,
        previous_questions=previous,
    )

    assert result.profile_patch["functional_safety"] == "ASIL-D"


def test_misjudged_vague_reply_does_not_clear_extracted_fields():
    """
    design.md 风险表第 2 条：误判只影响"是否额外给一组选项"，绝不允许影响
    profile_patch 的写入。这里 is_vague_reply 会命中"都行"，但模型提取到的
    字段和上一轮给的候选档位无关，必须原样保留。
    """
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [],
                    "profile_patch": {"headcount": 2, "mcu_family": ["英飞凌 Aurix"]},
                }
            )
        ]
    )
    previous = [
        IntakeQuestion(
            text="要哪个 ASIL 等级？",
            question_id="functional_safety",
            field="functional_safety",
            options=("ASIL-B", "ASIL-D", "无要求"),
        )
    ]

    result = run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "随便哪个 MCU 都行，招 2 个"}],
        round_count=1,
        previous_questions=previous,
    )

    assert result.profile_patch == {"headcount": 2, "mcu_family": ["英飞凌 Aurix"]}


def test_prompt_version_is_intake_v4():
    """铁律 5：SYSTEM_PROMPT 改了就必须升版本。"""
    gateway = make_gateway(
        [json.dumps({"is_job_related": True, "questions": [], "profile_patch": {}})]
    )
    captured = {}
    original = gateway.extract_structured_with_meta

    def _spy(**kwargs):
        captured.update(kwargs)
        return original(**kwargs)

    gateway.extract_structured_with_meta = _spy
    run_intake_turn(gateway, history=[{"role": "user", "content": "要个工程师"}], round_count=0)

    assert captured["prompt_version"] == "intake-v4"


# ---------------------------------------------------------------------------
# Task 5 review 修复：2026-08-20
# Finding 1：matched_terms 排序改按最后出现位置，堵住"提过又被否掉的领域"
#            靠 FOLLOWUP_RULES 声明顺序抢先的串档
# Finding 2：候选档位守卫处理 list 值时只摘命中未选档位的元素，不再连用户
#            自己打出的同字段其它元素一起摘掉
# ---------------------------------------------------------------------------


def test_matched_terms_prefer_the_domain_mentioned_most_recently():
    """
    2026-08-20 review：`match_ambiguous_terms` 按 FOLLOWUP_RULES 声明顺序返回
    术语，不是对话里出现的顺序。"先说要驱动开发的，算了改成供应商开发的"——
    用户最终选定的是供应商开发，但"驱动开发"在声明顺序里排在前面，如果不按
    对话里的先后排序，core_skills 会拿到已经被否掉的驱动总线档位
    （CAN-FD/LIN/车载以太网），而不是供应商开发的档位。
    """
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "这块最看重哪项能力？", "field": "core_skills"}],
                    "profile_patch": {},
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway,
        history=[
            {
                "role": "user",
                "content": "先说下，我们要个驱动开发的，算了，改成招供应商开发的",
            },
            {"role": "user", "content": "你决定吧"},
        ],
        round_count=1,
    )

    assert result.questions
    supplier_options = {"新供应商导入与审核", "供应商绩效与降本", "供应商质量改善"}
    driver_options = {"CAN-FD", "LIN", "车载以太网"}
    assert set(result.questions[0].options) == supplier_options
    assert set(result.questions[0].options) != driver_options


def test_matched_terms_ordering_is_deterministic_on_replay():
    """同一份对话重放两次，域判定与最终档位必须完全一致——排序只能依赖文本
    内容本身，不能引入任何非确定性来源（如集合迭代顺序）。"""
    history = [
        {
            "role": "user",
            "content": "先说下，我们要个驱动开发的，算了，改成招供应商开发的",
        },
        {"role": "user", "content": "你决定吧"},
    ]

    def _run():
        gateway = make_gateway(
            [
                json.dumps(
                    {
                        "is_job_related": True,
                        "questions": [{"text": "这块最看重哪项能力？", "field": "core_skills"}],
                        "profile_patch": {},
                    }
                )
            ]
        )
        return run_intake_turn(gateway, history=history, round_count=1)

    first = _run()
    second = _run()

    assert first.questions[0].options == second.questions[0].options


def test_typed_list_item_survives_while_unchosen_sibling_is_dropped():
    """
    2026-08-20 review：字段值是 list（如 core_skills: ["CAN-FD", "LIN"]）时，
    原实现只要任一元素命中未选档位就删掉整个字段——用户自己打出的"CAN-FD"会
    跟着一起被摘掉。现在只摘掉命中未选档位的那个元素（"LIN"），用户自己打出
    的"CAN-FD"必须保留。
    """
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [],
                    "profile_patch": {"core_skills": ["CAN-FD", "LIN"]},
                }
            )
        ]
    )
    previous = [
        IntakeQuestion(
            text="驱动对接的总线类型是？",
            question_id="core_skills",
            field="core_skills",
            options=("CAN-FD", "LIN", "车载以太网"),
        )
    ]

    result = run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "你决定吧，CAN-FD 肯定要的"}],
        round_count=1,
        previous_questions=previous,
    )

    assert result.profile_patch["core_skills"] == ["CAN-FD"]
