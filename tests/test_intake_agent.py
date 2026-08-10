import json
from dataclasses import dataclass, field

from app.agents.intake_agent import run_intake_turn
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


class FakeChatCompletions:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content = self._responses.pop(0)
        return FakeResponse(choices=[FakeChoice(message=FakeMessage(content=content))])


class FakeChat:
    def __init__(self, responses):
        self.completions = FakeChatCompletions(responses)


class FakeOpenAIClient:
    def __init__(self, responses):
        self.chat = FakeChat(responses)


def make_gateway(responses: list[str]) -> LLMGateway:
    return LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=FakeOpenAIClient(responses),
    )


def test_unrelated_message_returns_guidance_and_not_complete():
    gateway = make_gateway(
        [json.dumps({"is_job_related": False, "questions": [], "profile_patch": {}})]
    )

    result = run_intake_turn(gateway, history=[{"role": "user", "content": "今天天气不错"}], round_count=0)

    assert result.is_job_related is False
    assert result.is_complete is False
    assert result.questions  # 引导语非空


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
    assert result.questions == ["是否涉及 AUTOSAR？"]
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
    assert result.questions == ["招聘人数是？"]


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
    for question in FOLLOWUP_RULES["嵌入式开发"]:
        assert question in prompt, f"命中术语的领域追问 {question!r} 没有进入 prompt"


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
    for question in FOLLOWUP_RULES["驱动开发"]:
        assert question not in prompt


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
    assert FOLLOWUP_RULES["功能安全"][1] not in prompt


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
