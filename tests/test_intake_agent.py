import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.agents import intake_agent
from app.agents.field_grounding import user_turns
from app.agents.intake_agent import (
    MAX_ASKS_PER_QUESTION,
    MAX_REASKS,
    SYSTEM_PROMPT,
    _build_user_prompt,
    derive_unspecified_fields,
    run_intake_turn,
)
from app.agents.intake_question import render_questions_text
from app.llm.gateway import LLMGateway
from app.schemas.job_profile import JobProfile


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
    """铁律 5：SYSTEM_PROMPT 改了就必须升版本。v4→v5 由单元 F 的 7.2 触发（见下方 test_prompt_version_is_v5）。"""
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

    assert captured["prompt_version"] == "intake-v5"


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


def test_budget_counts_productive_rounds_not_total_rounds():
    """
    7 个总轮次但只有 3 轮有产出时不该收尾——这正是"空转轮不消耗预算"要买到的
    东西（spec「空转轮不计入预算」）。
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
        history=[{"role": "user", "content": "继续"}],
        round_count=7,
        productive_round_count=3,
    )

    assert result.questions
    assert result.is_complete is False


def test_total_round_cap_forces_wrap_up_even_with_no_productive_rounds():
    """spec「总轮次硬上限兜底」：连续零产出轮不能把对话拖成无限。"""
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "工具链上有什么要求？", "field": "toolchain"}],
                    "profile_patch": {},
                    "unspecified_fields": ["toolchain"],
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "不知道"}],
        round_count=MAX_TOTAL_ROUNDS,
        productive_round_count=0,
    )

    assert result.questions == []
    assert result.is_complete is True
    # tasks 6.2 起 unspecified_fields 的语义换人：从"模型说的"变成"系统按字段表
    # 推导的"。这一轮画像是空的，因此推导给出**全部业务字段**，不再等于模型那份
    # 单元素列表。模型自称的那份原样留在对照字段里，仍然可断言。
    # ⛔ 不许为了让这条老断言原样通过而给 derive_unspecified_fields 加 if give_up
    # 分支——那等于把刚修好的漏报又装回去。
    assert "toolchain" in result.unspecified_fields
    assert set(result.unspecified_fields) == set(JobProfile.model_fields) - {
        "unspecified_fields"
    }
    assert result.model_claimed_unspecified_fields == ["toolchain"]


def test_productive_round_limit_still_wraps_up():
    """MAX_ROUNDS 的既有行为不变：有产出轮吃满照样收尾。"""
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
        history=[{"role": "user", "content": "继续"}],
        round_count=MAX_ROUNDS,
        productive_round_count=MAX_ROUNDS,
    )

    assert result.questions == []
    assert result.is_complete is True


# --- .51 真实回放基准（tasks 6.3） -----------------------------------------

_REPLAY_PATH = Path(__file__).parent / "fixtures" / "pilot-replay-profiles.json"


def _replay(prefix: str) -> dict:
    """读取 .51 真实会话画像快照。取数出处见文件里的 _provenance 段。"""
    return json.loads(_REPLAY_PATH.read_text(encoding="utf-8"))["sessions"][prefix]


def test_replay_fixture_carries_provenance_and_no_dialogue_text():
    """
    这份基准的价值全部来自"它是真的"。没有出处的快照与手写的假数据无法区分，
    半年后没人说得清它是从哪来的——那时 6.3 就退化成"用自己编的答案验证自己
    写的推导"。同时守住脱敏边界：只允许画像字段进仓库，对话原文与人名不进。
    """
    raw = _REPLAY_PATH.read_text(encoding="utf-8")
    payload = json.loads(raw)

    provenance = payload["_provenance"]
    assert "192.168.100.51" in provenance["source"]
    assert provenance["captured_at"]
    assert "job_profile" in provenance["table"]

    assert set(payload["sessions"]) == {"a478499c", "19b6ec6d"}
    for prefix, session in payload["sessions"].items():
        assert session["job_id"].startswith(prefix)
        assert isinstance(session["profile_json"], dict)
        assert isinstance(session["model_unspecified_fields"], list)

    # 对话原文与人名一律不得进仓库
    assert "history_json" not in raw
    assert "姚祖怡" not in raw


# --- derive_unspecified_fields：未指定字段的唯一真源（tasks 6.1 / 6.3） -------


def test_derive_lists_every_business_field_for_empty_profile():
    """空画像 = 所有业务字段都未指定；系统管理字段不算业务字段。"""
    derived = derive_unspecified_fields({})

    assert "unspecified_fields" not in derived
    assert set(derived) == set(JobProfile.model_fields) - {"unspecified_fields"}


def test_answered_field_does_not_enter_unspecified():
    """spec Scenario: 已答字段不进未指定。"""
    derived = derive_unspecified_fields({"functional_safety": "ASIL-B"})

    assert "functional_safety" not in derived


def test_unanswered_field_is_never_missed():
    """spec Scenario: 未答字段不被遗漏。空容器、None、空白串、占位符都算未答。"""
    derived = derive_unspecified_fields(
        {
            "toolchain": [],
            "mcu_family": None,
            "project_experience_requirement": "   ",
            "department": "未指定",
            "job_title": "底层软件开发工程师",
        }
    )

    assert "toolchain" in derived
    assert "mcu_family" in derived
    assert "project_experience_requirement" in derived
    assert "department" in derived
    assert "job_title" not in derived


def test_derivation_is_stable_across_repeated_calls():
    """spec Scenario: 推导结果稳定。顺序也必须稳定——下游要直接渲染这个列表。"""
    accumulated = {
        "job_title": "嵌入式软件工程师",
        "toolchain": ["CANoe"],
        "core_skills": [],
    }

    first = derive_unspecified_fields(accumulated)
    second = derive_unspecified_fields(dict(accumulated))

    assert first == second
    assert first == sorted(first, key=list(JobProfile.model_fields).index)


def test_internal_underscore_keys_are_ignored():
    """profile_json 里混着 _jd_text / _gap_acknowledgement 这类内部键，
    它们不在字段表里，既不该被当成已答字段，也不该被列进未指定。"""
    derived = derive_unspecified_fields(
        {"_jd_text": "岗位职责……", "_gap_acknowledgement": {}}
    )

    assert "_jd_text" not in derived
    assert "_gap_acknowledgement" not in derived
    assert "job_title" in derived


def test_derive_catches_what_the_model_underreported_in_a478499c():
    """
    真实回放反证（tasks 6.3 的第一半）：`a478499c` 强制收尾时，模型给的
    unspecified_fields 是**空数组**——它宣称这份画像什么都不缺。系统推导必须给出
    非空结果，否则本章等于什么都没修。
    """
    session = _replay("a478499c")

    assert session["model_unspecified_fields"] == [], (
        "前置事实变了：这个会话模型当时给的不再是空数组。停下报告，不要改这条断言去迁就"
    )

    derived = derive_unspecified_fields(session["profile_json"])

    assert derived, "模型说没缺口，系统推导也说没缺口——漏报没有被修掉"
    # 这份画像真实缺的就是这几项，逐个钉住，避免"非空即过"退化成弱断言
    assert {"core_skills", "functional_safety", "mcu_family", "sop_projects"} <= set(
        derived
    )


def test_derive_lists_every_field_the_model_flagged_in_19b6ec6d():
    """
    真实回放（tasks 6.3 的第二半）。

    ⚠️ **与 plan / design.md 决策 6 的举证不符，已如实按真值写。** 那两份文档称
    `19b6ec6d` 里模型把用户**已经答过**的 functional_safety / sop_projects 列进了
    未指定（"虚报"）。核对 `.51` 真值后不成立：这两个字段在该会话**全部 6 个版本
    里都是 None**，用户从未答过——模型列它们是**对的**，不是虚报。

    因此本用例断言真值形态：模型标出的 5 个字段确实一个都没答，系统推导必须
    **全部列出**，一个都不许漏。

    ⛔ 不要把这条改回"断言 functional_safety / sop_projects 不在结果里"——那会
    强迫推导漏掉两个真实缺口，与 6.1（不再漏报）直接矛盾。6.3 的措辞需要决策人
    裁决，未裁决前 `tasks.md` 6.3 保持未勾。
    """
    session = _replay("19b6ec6d")
    profile = session["profile_json"]
    flagged = session["model_unspecified_fields"]

    # 前置事实：模型标出的这几项，在最终画像里确实一个值都没有
    assert flagged, "前置事实变了：模型当时并没有标出任何未指定字段。停下报告"
    for name in flagged:
        assert not profile.get(name), (
            f"前置事实变了：{name} 在最终画像里有值了，模型这次才算虚报。停下报告"
        )

    derived = derive_unspecified_fields(profile)

    assert set(flagged) <= set(derived), (
        "系统推导漏掉了模型都发现了的真实缺口——比不修还糟"
    )


# --- 模型自称值降级为对照 + loggable_summary 首个生产上岗点（tasks 6.2） ------


def test_model_claimed_unspecified_never_becomes_the_result():
    """
    tasks 6.2：模型自称的未指定字段不再进结果。这里模型虚报 functional_safety
    （用户本轮刚答了 ASIL-B），推导结果必须不含它；模型那份原样保留在对照字段里。
    """
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [],
                    "profile_patch": {"functional_safety": "ASIL-B"},
                    "unspecified_fields": ["functional_safety", "sop_projects"],
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "要 ASIL-B"}],
        round_count=1,
        profile_patch_accumulated={"job_title": "底层软件开发工程师"},
    )

    assert "functional_safety" not in result.unspecified_fields
    assert "toolchain" in result.unspecified_fields  # 真的没答的字段照样列出来
    assert result.model_claimed_unspecified_fields == [
        "functional_safety",
        "sop_projects",
    ]


def test_derivation_uses_the_patch_that_actually_gets_persisted():
    """
    ⚠️ 与计划正文的一处偏差，故意的：计划写的是用 `parsed.profile_patch` 推导，
    但真正落库的是经 `_drop_unchosen_candidate_values` 摘掉未选中候选档位之后的
    `profile_patch`。用 parsed 那份推导，会把"模型塞进来、但用户没选"的候选值
    当成已答字段——**漏报当场回来**，而且只在模糊回复那条路径上漏，最难发现。

    这里让用户给一句模糊回复、模型顺手塞一个候选 mcu_family：落库的画像里这个
    字段会被摘掉，因此推导必须仍然把它列为未指定。
    """
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [
                        {
                            "text": "MCU 平台族倾向哪一类？",
                            "field": "mcu_family",
                            "options": ["英飞凌 TC3xx", "恩智浦 S32K"],
                        }
                    ],
                    "profile_patch": {"mcu_family": ["英飞凌 TC3xx"]},
                    "unspecified_fields": [],
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway,
        history=[
            {"role": "assistant", "content": "MCU 平台族倾向哪一类？"},
            {"role": "user", "content": "这个我不太了解"},
        ],
        round_count=1,
        profile_patch_accumulated={"job_title": "底层软件开发工程师"},
        previous_questions=[
            IntakeQuestion(
                text="MCU 平台族倾向哪一类？",
                question_id="mcu_family",
                field="mcu_family",
                options=("英飞凌 TC3xx", "恩智浦 S32K"),
            )
        ],
    )

    assert "mcu_family" not in result.profile_patch, "前置事实变了：候选值没有被摘掉"
    assert "mcu_family" in result.unspecified_fields, (
        "推导读的是 parsed.profile_patch 而不是真正落库的那份——未选中的候选值被当成已答"
    )


def test_unspecified_comparison_log_goes_through_loggable_summary(monkeypatch, caplog):
    """
    delivery-units.md §3.3 的验收要求：断言这条日志路径**确实调用了**
    loggable_summary()。只断言"日志里没泄漏"是不够的——没有调用点时"0 命中"
    同时兼容"脱敏有效"和"脱敏根本没上岗"两种解释（findings §8.3.1 更正段）。
    """
    calls = []
    real = intake_agent.loggable_summary

    def spy(obj, **kwargs):
        calls.append((dict(obj), kwargs))
        return real(obj, **kwargs)

    monkeypatch.setattr(intake_agent, "loggable_summary", spy)

    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [],
                    "profile_patch": {"job_title": "底层软件开发工程师"},
                    "unspecified_fields": ["toolchain", "根本不存在的字段"],
                }
            )
        ]
    )

    with caplog.at_level(logging.DEBUG, logger="app.agents.intake_agent"):
        run_intake_turn(
            gateway, history=[{"role": "user", "content": "招人"}], round_count=1
        )

    # 1) 确实调用了，而且是带 known_fields 的那种调用（键名本身也要过滤，
    #    因为模型可能幻觉出一个不存在的字段名）
    assert len(calls) == 2, "推导结果与模型自称各要过一次脱敏，一次都不能省"
    assert all("known_fields" in kwargs for _obj, kwargs in calls)

    # 2) 落到日志里的是摘要形态，不是业务对象本体
    text = caplog.text
    assert "field_count" in text and "unknown_field_count" in text
    assert "底层软件开发工程师" not in text
    # 3) 模型幻觉出的字段名只贡献计数，不贡献名字
    assert "根本不存在的字段" not in text


# ---------------------------------------------------------------------------
# 第 5 章：已问台账接线 —— 重问标注、重问上限、轮次口径对齐
# ---------------------------------------------------------------------------


def _q(text: str, field: str | None = None) -> dict:
    """构造一个问题 payload，省得每条用例都手写一遍 dict。"""
    return {"text": text, "field": field, "options": [], "allow_free_text": True}


def _turn(responses, **kwargs):
    """跑一轮采集，默认参数取“第一轮”的形状，用例只覆盖自己关心的那几个。"""
    gateway = make_gateway(responses)
    params = {
        "history": [{"role": "user", "content": "要个嵌入式工程师"}],
        "round_count": 0,
        "productive_round_count": 0,
        "profile_patch_accumulated": {},
        "asked_question_ids_before": [],
        "previous_questions": [],
        "asked_question_rounds": [],
    }
    params.update(kwargs)
    return run_intake_turn(gateway, **params)


def test_unanswered_question_asked_again_is_marked_as_a_reask():
    """spec「重问必须显式标注」：重问同一个未答子问题，is_reask 必须为 True，
    渲染出来的文本必须带重问提示。"""
    result = _turn(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "ASIL 这块到底要不要？", "field": "functional_safety"}],
                    "profile_patch": {},
                }
            )
        ],
        round_count=1,
        productive_round_count=1,
        profile_patch_accumulated={"job_title": "嵌入式软件工程师"},
        asked_question_rounds=[[_q("功能安全等级（ASIL）上有什么要求？", "functional_safety")]],
    )

    (question,) = result.questions
    assert question.question_id == "functional_safety"
    assert question.is_reask is True
    assert "（这个你刚才没答）" in render_questions_text(result.questions)


def test_a_brand_new_question_is_not_marked_as_a_reask():
    result = _turn(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "招几个人？", "field": "headcount"}],
                    "profile_patch": {},
                }
            )
        ],
        round_count=1,
        productive_round_count=1,
        asked_question_rounds=[[_q("功能安全等级（ASIL）上有什么要求？", "functional_safety")]],
    )

    (question,) = result.questions
    assert question.is_reask is False


def test_answered_question_asked_again_is_not_a_reask():
    """spec 的重问标注是“这个你刚才没答”。字段已经有值了还问，那是**递进
    提问**（design.md 决策 2 接受的撞 id 近似），不是重问——打上重问标记会
    对用户撒谎。"""
    result = _turn(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "要哪个 ASIL 等级？", "field": "functional_safety"}],
                    "profile_patch": {},
                }
            )
        ],
        round_count=1,
        productive_round_count=1,
        profile_patch_accumulated={"functional_safety": "ASIL-B"},
        asked_question_rounds=[[_q("是否需要 ISO 26262 功能安全经验？", "functional_safety")]],
    )

    (question,) = result.questions
    assert question.is_reask is False


def test_reask_stops_after_the_cap_and_the_field_lands_in_unspecified():
    """
    spec「重问超限转未指定」+ tasks 5.5。

    上限取 2（问 1 次 + 重问 2 次 = 出现在 3 轮里）。第 4 次再问就必须被摘掉。
    “计入未指定字段”这一半**不是这里写的一段标记逻辑**——字段没值，单元 D 的
    derive_unspecified_fields 自然把它列进去，本轮的 unspecified_fields 也就是
    它算出来的那一份，正是为了钉死“E 不许写第二套标记”（delivery-units.md §2.D）。

    ⚠️ 断言必须落在 `result.unspecified_fields` 上，**不能**在这里另外调一次
    `derive_unspecified_fields(accumulated)`：后者算的是本用例自己搓的那个
    fixture dict，与 `result` 毫无关系——把 run_intake_turn 的 unspecified_fields
    改成恒空、甚至把摘除与未指定推导之间的接线整个剪断，那种写法都照样绿
    （2026-08-27 whole-branch review 判定为同义反复断言）。5.5 是本分支的头号
    主张，也是“不新增任何存储”的全部理由，它必须被本轮的**输出**观测到。
    """
    assert MAX_REASKS == 2
    assert MAX_ASKS_PER_QUESTION == 3

    asked = [[_q("功能安全等级？", "functional_safety")]] * MAX_ASKS_PER_QUESTION
    accumulated = {"job_title": "嵌入式软件工程师"}

    result = _turn(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "ASIL 到底要不要？", "field": "functional_safety"}],
                    "profile_patch": {},
                }
            )
        ],
        round_count=3,
        productive_round_count=1,
        profile_patch_accumulated=accumulated,
        asked_question_rounds=asked,
    )

    assert [q.question_id for q in result.questions] == []
    assert "functional_safety" in result.unspecified_fields


def test_progressive_questions_on_an_answered_field_are_not_cut_off_early():
    """
    tasks 5.7 + design.md Risks 第 3 条：question_id = field 撞 id 的递进提问
    （“要不要 26262” → “要哪个 ASIL”）不能被上限过早掐断。

    这里给它问满 MAX_ASKS_PER_QUESTION 轮**且字段已有值**，仍然不摘——
    上限只对**未答**的子问题计数（本计划「关键设计决定 4」）。
    """
    asked = [[_q("是否需要 ISO 26262 功能安全经验？", "functional_safety")]] * MAX_ASKS_PER_QUESTION

    result = _turn(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "要哪个 ASIL 等级？", "field": "functional_safety"}],
                    "profile_patch": {},
                }
            )
        ],
        round_count=3,
        productive_round_count=2,
        profile_patch_accumulated={"functional_safety": "ASIL-B"},
        asked_question_rounds=asked,
    )

    assert [q.question_id for q in result.questions] == ["functional_safety"]
    assert result.questions[0].is_reask is False


def test_a_question_answered_in_this_very_turn_is_not_reasked():
    """
    用户这一轮刚答完的子问题，不能在同一轮的回复里被当成“你刚才没答”重问一遍。
    台账的 answered_fields 必须用**合并本轮 patch 之后**的画像算，不能只用
    上一轮的累积值——这是接线顺序错了就会当场对用户撒谎的一处。
    """
    result = _turn(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "要哪个 ASIL 等级？", "field": "functional_safety"}],
                    "profile_patch": {"functional_safety": "ASIL-B"},
                }
            )
        ],
        round_count=1,
        productive_round_count=1,
        asked_question_rounds=[[_q("功能安全等级？", "functional_safety")]],
    )

    (question,) = result.questions
    assert question.is_reask is False


def test_dropping_an_exhausted_reask_does_not_make_the_turn_productive():
    """
    轮次口径对齐（tasks 5.5 ↔ 3.10）：摘掉超限重问之后本轮没有任何新问题、
    也没有新画像内容，那就是一轮空转——is_productive 必须为 False，不吃
    MAX_ROUNDS 的有产出轮预算。判定式一个字没改，这条只是把它钉住。
    """
    asked = [[_q("功能安全等级？", "functional_safety")]] * MAX_ASKS_PER_QUESTION

    result = _turn(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "ASIL 到底要不要？", "field": "functional_safety"}],
                    "profile_patch": {},
                }
            )
        ],
        round_count=3,
        productive_round_count=1,
        profile_patch_accumulated={"job_title": "嵌入式软件工程师"},
        asked_question_rounds=asked,
    )

    assert result.is_productive is False
    assert result.is_complete is True  # 没有问题可问了，转确认，交给单元 D 的缺口警示


def test_a_plain_reask_within_the_cap_still_does_not_consume_the_productive_budget():
    """重问在上限之内照样下发，但它的 question_id 早在台账里，
    has_new_question 不成立——单元 B 落地时就是这个口径，E 不许破坏它。"""
    result = _turn(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "ASIL 这块到底要不要？", "field": "functional_safety"}],
                    "profile_patch": {},
                }
            )
        ],
        round_count=1,
        productive_round_count=1,
        profile_patch_accumulated={"job_title": "嵌入式软件工程师"},
        asked_question_rounds=[[_q("功能安全等级？", "functional_safety")]],
    )

    assert result.questions[0].is_reask is True
    assert result.is_productive is False


def test_off_topic_guidance_is_never_dropped_by_the_reask_cap():
    """
    离题轮的引导语走 is_job_related=False 的早返回分支，**不经过台账摘除**。
    没有这条保护，连说 3 句离题的话之后引导语会被当成“问到第 4 次的子问题”
    摘掉，用户拿到一个空气泡——比不改还糟。
    """
    # 逐字取常量而不是抄一份字面量：引导语没有 field，question_id 走文本哈希
    # （free:<hash>），文本差一个字 id 就不撞、引导语根本进不了台账，这条用例
    # 会变成"永远通过但什么也没保护"。
    from app.agents.intake_agent import _GUIDANCE_TEXT

    guidance_round = [[_q(_GUIDANCE_TEXT)]]

    result = _turn(
        [json.dumps({"is_job_related": False, "questions": [], "profile_patch": {}})],
        round_count=3,
        asked_question_rounds=guidance_round * MAX_ASKS_PER_QUESTION,
    )

    assert result.is_job_related is False
    assert len(result.questions) == 1
    assert result.questions[0].is_reask is False


def test_ledger_is_ignored_when_the_caller_does_not_pass_the_rounds():
    """向后兼容：没接上按轮台账的调用方（老测试、别的入口）行为与今天逐字一致
    ——不打重问标记、不摘任何问题。"""
    result = _turn(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "功能安全等级？", "field": "functional_safety"}],
                    "profile_patch": {},
                }
            )
        ],
        round_count=1,
        productive_round_count=1,
        asked_question_ids_before=["functional_safety"],
        asked_question_rounds=[],
    )

    (question,) = result.questions
    assert question.is_reask is False


def test_fallback_synthesis_skips_a_field_that_already_hit_the_reask_cap():
    """
    模糊回复那一轮模型一个问题都没给、系统合成兜底问题时，**已问满重问上限的
    字段必须跳过**：合成出来也会被 _apply_question_ledger 当场摘掉，那一轮就
    白跑，用户拿到一个空气泡。

    构造刻意让"优先挑没问过的字段"那条捷径走不通（每个兜底字段都问过一遍），
    逼合成走"退回第一个候选"的分支——超限过滤只在这条分支上看得出来。
    """
    from app.agents.ecu_knowledge import FALLBACK_FIELD_ORDER, FALLBACK_QUESTION_TEXT

    asked = [[_q(FALLBACK_QUESTION_TEXT[name], name) for name in FALLBACK_FIELD_ORDER]]
    asked += [[_q(FALLBACK_QUESTION_TEXT["job_title"], "job_title")]] * MAX_REASKS

    result = _turn(
        [json.dumps({"is_job_related": True, "questions": [], "profile_patch": {}})],
        history=[{"role": "user", "content": "你决定吧"}],
        round_count=3,
        productive_round_count=1,
        asked_question_rounds=asked,
    )

    # job_title 已经问满 MAX_ASKS_PER_QUESTION 轮，顺位落到下一个还没超限的字段
    assert [q.question_id for q in result.questions] == ["department"]
    assert result.questions[0].is_reask is True


def test_the_reask_badge_is_applied_before_the_verbatim_repeat_defense():
    """
    ⑤ 打重问标记必须发生在 ⑥ 的逐字防线**之前**（design.md 决策 1「代价」）：
    防线要比对的是**真正下发的那一版**文本。顺序反过来的话，一个"用户确实没
    答、带着「这个你刚才没答」诚实再问一次"的重问，会被防线当成模型在原样复读
    而整轮吞掉，用户拿到空气泡——而重问标注（tasks 5.4）的全部意义就在这里。
    """
    repeated = "功能安全等级（ASIL）上有什么要求？"

    result = _turn(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": repeated, "field": "functional_safety"}],
                    "profile_patch": {},
                }
            )
        ],
        history=[
            {"role": "user", "content": "要个嵌入式工程师"},
            {"role": "assistant", "content": repeated},
            {"role": "user", "content": "这个先放一放"},
        ],
        round_count=1,
        productive_round_count=1,
        profile_patch_accumulated={"job_title": "嵌入式软件工程师"},
        asked_question_rounds=[[_q(repeated, "functional_safety")]],
    )

    (question,) = result.questions
    assert question.is_reask is True
    assert render_questions_text(result.questions) != repeated


def test_a_field_without_a_value_is_never_counted_as_answered():
    """
    超限集合 exhausted 用 `not entry.is_answered`（口径来自
    derive_unspecified_fields），而 _synthesize_fallback_question 的 missing 用
    `_has_value`——两个不同的判据。今天它们同向：**一个字段进 missing（没值）
    就一定不算已答**，所以超限过滤永远不会去碰一个"已答"的字段，两处口径不会
    打架。这条把该前提钉住：判据一旦分叉，重问上限就会开始掐断本该继续递进的
    已答字段（tasks 5.7 要保住的正是那条路）。

    取值矩阵刻意比 `_has_value` 今天的那条元组宽：只遍历 `(None, "", [], {}, ())`
    的话，`_has_value` 哪天多收一个成员（`0` 是最现实的那个——`_is_unspecified_value`
    把标量 0 判为**已指定**）本条用例根本不会去算那个值，前提塌了它照样绿。
    这里断言的是**蕴含**而非等价：`not _has_value(v) ⟹ v 不算已答`；一个"有值"的
    取值不受本条约束，所以矩阵可以放心加料。
    """
    values = (None, "", "  ", "未指定", "ASIL-B", [], (), {}, set(), ["CAN-FD"], {"a": 1},
              0, 1, 0.0, 3.5, False, True)

    for value in values:
        if intake_agent._has_value(value):
            continue  # 有值的取值不在本蕴含式的约束范围内
        assert "functional_safety" not in intake_agent._answered_fields(
            {"functional_safety": value}
        ), f"{value!r} 没值却被算成已答，超限过滤与兜底合成的口径已经分叉"

    # 字段整个缺席也是"没值"的一种，同样不能算已答
    assert "functional_safety" not in intake_agent._answered_fields({})


def test_replay_2494103e_iatf_and_iso26262_sequence():
    """
    tasks 5.6 · 真实回放：`2494103e`（采购岗）第 3-4 轮的 IATF 16949 /
    ISO 26262 序列。

    **前置事实的出处**（本仓库内已逐字记载，不需要也不去 .51 取对话原文）：
    - `openspec/changes/m1-intake-quality-fixes/proposal.md` 第 7 行：
      "第 3 轮把「IATF 16949 / ISO 26262」打包成一个问题串，用户只答了前者；
      第 4 轮系统把 ISO 26262 拆出来重问，措辞不同、话题相同。2026-08-11 上线
      的逐字重复检测（_repeats_earlier_assistant_turn）按定义抓不到——原文本来
      就不一样。"
    - `docs/m1-demo-pilot-feedback.md`：该会话自身的两条原子性不变式都是绿的、
      **没有查到**丢消息——同一份文档同时写明"绿是弱证据，不是证明"（两边恰好
      一起失败时那一轮会整体消失、计数仍然相等，数据上不可见），其净结论把换
      措辞重问称为"**最可能的**解释"，同时明确"'这是追问策略的固有代价'这句话
      **不能再当成唯一解释**"——被从"唯一解释"里除名的是**追问策略**这一边，
      理由恰恰是投递丢失这条替代解释仍然活着（已证实存在一个会产生完全相同
      表象的基础设施故障）。所以本用例的立场是：换措辞重问是"用户体感重复"
      最可能的成因，投递丢失那一层另由 fix-sqlite-transaction-ownership 处理，
      两者不互斥。

    **本用例的边界（如实写在这里，不要在别处宣称更强的结论）**：它回放的是
    那次事故的**形状**（打包提问 → 部分回答 → 换措辞重问），不是生产库里逐
    字节的原始 turn 文本——`.51` 的 conversation 原文不在取数范围内（单元 D
    的 Global Constraints）。它证明的是"这个形状现在会被正确追踪"，不是"这
    段字节序列被原样重放过"。
    """
    accumulated = {"job_title": "采购工程师", "department": "采购部"}

    # 第 3 轮：SYSTEM_PROMPT 的拆分规则要求两个议题拆成两条（spec Scenario
    # 「多个议题必须拆分」）。这一轮两条都是新问题，都不带重问标记。
    round3 = _turn(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [
                        {"text": "是否要求熟悉 IATF 16949？", "field": "core_skills"},
                        {"text": "是否要求熟悉 ISO 26262？", "field": "functional_safety"},
                    ],
                    "profile_patch": {},
                }
            )
        ],
        round_count=2,
        productive_round_count=2,
        profile_patch_accumulated=accumulated,
        asked_question_rounds=[[], []],
    )
    assert [q.question_id for q in round3.questions] == ["core_skills", "functional_safety"]
    assert [q.is_reask for q in round3.questions] == [False, False]

    # 第 4 轮：用户只答了 IATF 16949。系统换措辞重问 ISO 26262——question_id
    # 必须与首问一致（换措辞不改 id），且必须带重问标注。
    #
    # 这一轮的模型输出刻意把**两条都**换措辞再抛一遍（这正是 proposal 第 7 行
    # 描述的"跟别的问题捆在一起重新问"）：只抛未答的那一条，"已答的不打重问
    # 标记"就无从谈起——那条断言会因为候选里根本没有它而恒真，看着有覆盖、
    # 其实一个字节都没验（2026-08-27 review 判定为空洞断言）。
    asked_after_round3 = [[], [], [q.to_payload() for q in round3.asked_questions]]
    accumulated_after_round3 = {
        **accumulated,
        "core_skills": [{"name": "IATF 16949", "required": True}],
    }
    round4 = _turn(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [
                        {"text": "16949 那边还有别的硬性要求吗？", "field": "core_skills"},
                        {"text": "功能安全 ISO 26262 这块有硬性要求吗？", "field": "functional_safety"},
                    ],
                    "profile_patch": {},
                }
            )
        ],
        round_count=3,
        productive_round_count=3,
        profile_patch_accumulated=accumulated_after_round3,
        asked_question_rounds=asked_after_round3,
    )

    by_id = {q.question_id: q for q in round4.questions}
    assert list(by_id) == ["core_skills", "functional_safety"]

    reasked = by_id["functional_safety"]
    assert reasked.question_id == "functional_safety"  # 换措辞不改 id
    assert reasked.is_reask is True                     # 重问带标注

    # 已答的那一条**不打**重问标记：用户答过 IATF 之后再问它是**递进提问**
    # （决定 4 / tasks 5.7 明确保住的那条路），给它挂上"这个你刚才没答"是对
    # 用户撒谎。注意它照常下发、不被摘除——"已答"只影响标记与上限，不影响下发。
    assert by_id["core_skills"].is_reask is False
    # 整轮文本里"这个你刚才没答"只能出现一次，就挂在真正没答的那一条上
    assert round4.questions_text.count("（这个你刚才没答）") == 1
    assert "（这个你刚才没答）功能安全 ISO 26262 这块有硬性要求吗？" in round4.questions_text
    # 这一轮既没有新画像内容也没有新 question_id → 不吃有产出轮预算
    assert round4.is_productive is False


def test_replay_2494103e_stops_reasking_iso26262_after_the_cap():
    """
    tasks 5.5 在 `2494103e` 那条序列上的收口：ISO 26262 问到第 3 轮仍无回答，
    第 4 次不再问；它的目标字段由单元 D 的 derive_unspecified_fields 自然列进
    未指定——E 这边没有、也不该有任何一行"标记为超限未答"的代码。

    **前置事实的出处与本用例的边界**与上一条用例
    （`test_replay_2494103e_iatf_and_iso26262_sequence`）逐字相同，不再复述：
    回放的是那次事故的**形状**，不是生产库里逐字节的原始 turn 文本，`.51` 的
    conversation 原文不在取数范围内。另外"问满 3 轮"这一段本身**不在**已记载
    的事实里——proposal 只记到第 4 轮的换措辞重问；把它续到上限是**按 5.5 的
    规则外推的假设序列**，用来验规则，不作为对那次会话的事实主张。

    台账的轮数写成字面量 3、而不是 `[[…]] * MAX_ASKS_PER_QUESTION`：跟着常量
    一起长的话，常量被改大时构造的台账也跟着变长，"第 4 次不再问"这条断言会
    自己放宽到"第 5 次不再问"仍然绿——变异实测过，把
    `MAX_ASKS_PER_QUESTION` 改成 `2 + MAX_REASKS` 时原写法一声不吭。绝对口径
    （问 1 次 + 重问 2 次 = 出现在 3 轮里）在下面那行单独钉住。
    """
    assert MAX_ASKS_PER_QUESTION == 3  # 问 1 次 + 重问 MAX_REASKS(2) 次

    accumulated = {
        "job_title": "采购工程师",
        "department": "采购部",
        "core_skills": [{"name": "IATF 16949", "required": True}],
    }
    asked = [[_q("是否要求熟悉 ISO 26262？", "functional_safety")]] * 3
    reask_response = json.dumps(
        {
            "is_job_related": True,
            "questions": [
                {"text": "26262 的事还得确认一下，有要求吗？", "field": "functional_safety"}
            ],
            "profile_patch": {},
        }
    )

    result = _turn(
        [reask_response],
        round_count=3,
        productive_round_count=2,
        profile_patch_accumulated=accumulated,
        asked_question_rounds=asked,
    )

    assert result.questions == []
    assert result.is_productive is False
    # 观测点是**本轮的输出**，不是拿 accumulated 再调一次 derive_unspecified_fields
    # ——后者只证明单元 D 的纯函数会列出这个字段，与本轮跑没跑通毫无关系
    # （2026-08-27 whole-branch review：同义反复断言）。
    assert "functional_safety" in result.unspecified_fields

    # 上限**之下**（只问过 2 轮）照常重问，只是带标注：摘除是"到上限才发生"，
    # 不是"问过就摘"。没有这一半，把上限改小到 1 次就问也能让上面三条全绿。
    within_cap = _turn(
        [reask_response],
        round_count=2,
        productive_round_count=2,
        profile_patch_accumulated=accumulated,
        asked_question_rounds=asked[:2],
    )

    assert [q.question_id for q in within_cap.questions] == ["functional_safety"]
    assert within_cap.questions[0].is_reask is True


def test_verbatim_repeat_detection_still_guards_jobs_with_an_empty_ledger():
    """
    tasks 5.8 的结论（保留逐字防线）在测试里的形态。

    `.51` 现网的既有 job（`delivery-units.md` §5 约定 4 记作 15 个）在第 8 章
    8.3/8.4 升级到单元 B 的新列之后，历史行的 `asked_questions` 全是列默认值
    `'[]'`——加列时按约定**不回填历史行**（同条约定；本机 demo 库的加列演练
    实测 22 行全部拿到默认台账，见
    `docs/findings/2026-08-26-unitB-已问台账列加列演练.md` 结论 3）。于是
    **升级前问过的那些子问题在台账里一个都看不见**：续聊的第一轮台账整个为空
    （下面构造的正是这一轮），此后也只看得见升级后新写的那几行。模型把升级前
    问过的问题原样再抛一次时，重问标记打不出来、重问上限也触发不了，兜得住的
    **只有** _repeats_earlier_assistant_turn。

    这条用例红了就说明有人把那道防线删了，而删除的症状只在台账看不见历史轮次
    的 job 上出现——本地新建的测试库每一轮都有台账，日常测试根本走不到。
    """
    text = "具体车型与量产时间是怎么安排的？"
    result = _turn(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": text}],
                    "profile_patch": {},
                }
            )
        ],
        history=[
            {"role": "user", "content": "要个嵌入式工程师"},
            {"role": "assistant", "content": text},
            {"role": "user", "content": "嗯"},
        ],
        round_count=1,
        productive_round_count=1,
        # 历史行的形态：有这一轮，但那一列是 '[]'
        asked_question_rounds=[[]],
    )

    assert result.questions == []      # 逐字重复 → stuck → 当场收尾
    assert result.is_complete is True


# ---------------------------------------------------------------------------
# 交付单元 F（tasks 7.2）：SYSTEM_PROMPT 要求逐字来源 + 用户轮次编号 + intake-v5
# ---------------------------------------------------------------------------


def test_prompt_version_is_v5():
    """
    铁律 5：SYSTEM_PROMPT 改了就必须升版本，否则 input_hash 与历史评分对不上。
    v4 是单元 B 占用的，F 是 v5，**不要重号**（delivery-units.md §5 约定 3）。
    """
    gateway = make_gateway([json.dumps({"is_job_related": True, "questions": [], "profile_patch": {}})])
    run_intake_turn(gateway, history=[{"role": "user", "content": "要招人"}], round_count=0)
    assert gateway._client.chat.completions.calls  # 确实调过模型
    # prompt_version 不进请求体，只进 AuditHook；这里直接对着源码常量断言。
    import app.agents.intake_agent as mod
    import inspect

    assert 'prompt_version="intake-v5"' in inspect.getsource(mod.run_intake_turn)


def test_system_prompt_demands_verbatim_source():
    """7.2：来源要求 + 正例 + 反例都必须在提示词里。"""
    assert "source_quote" in SYSTEM_PROMPT
    assert "source_turn" in SYSTEM_PROMPT
    assert "user#" in SYSTEM_PROMPT
    assert "逐字" in SYSTEM_PROMPT
    assert "正例" in SYSTEM_PROMPT
    assert "反例" in SYSTEM_PROMPT


def test_transcript_numbers_user_turns_consistently_with_verifier():
    """
    prompt 里的 user#N 编号必须与 field_grounding.user_turns 的下标严格对齐。
    这是本单元最容易静默错的地方：错位一格的表现是"引用对得上却判未溯源"，
    从错误信息里完全看不出成因。所以这里不测"格式好看"，测的是**两边同源**。
    """
    history = [
        {"role": "user", "content": "第一句"},
        {"role": "assistant", "content": "助手插一句"},
        {"role": "user", "content": "第二句"},
        {"content": "没有 role 的一句"},
    ]
    prompt = _build_user_prompt(history, {}, [])
    for index, text in enumerate(user_turns(history), start=1):
        assert f"user#{index}: {text}" in prompt
    assert "assistant: 助手插一句" in prompt
    # 助手轮次不占编号：三条用户原话，编号只到 3
    assert "user#4" not in prompt
