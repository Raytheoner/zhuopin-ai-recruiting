import json
from dataclasses import dataclass, field

import pytest
from pydantic import BaseModel

from app.llm.gateway import LLMGateway, SchemaExtractionFailed


class Point(BaseModel):
    x: int
    y: int


@dataclass
class FakeMessage:
    content: str


@dataclass
class FakeChoice:
    message: FakeMessage


@dataclass
class FakeUsage:
    prompt_tokens: int = 10
    completion_tokens: int = 5


@dataclass
class FakeResponse:
    choices: list[FakeChoice]
    model: str = "deepseek-chat-241226"
    usage: FakeUsage = field(default_factory=FakeUsage)


class FakeChatCompletions:
    def __init__(self, responses: list[str], response_model: str = "deepseek-chat-241226"):
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
    def __init__(self, responses, response_model: str = "deepseek-chat-241226"):
        self.completions = FakeChatCompletions(responses, response_model=response_model)


class FakeOpenAIClient:
    def __init__(self, responses: list[str], response_model: str = "deepseek-chat-241226"):
        self.chat = FakeChat(responses, response_model=response_model)


def test_rejects_latest_model_alias():
    with pytest.raises(ValueError, match="latest"):
        LLMGateway(
            api_key="k",
            base_url="https://example.com",
            model="latest",
            supports_json_schema=False,
            client=FakeOpenAIClient([]),
        )


def test_extracts_valid_json_on_first_try():
    client = FakeOpenAIClient([json.dumps({"x": 1, "y": 2})])
    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=client,
    )

    result = gateway.extract_structured(
        system_prompt="sys", user_prompt="user", schema=Point
    )

    assert result == Point(x=1, y=2)
    assert client.chat.completions.calls[0]["temperature"] == 0
    assert client.chat.completions.calls[0]["model"] == "deepseek-chat-241226"


def test_retries_on_invalid_json_then_succeeds():
    client = FakeOpenAIClient(
        ["not json", json.dumps({"x": 3, "y": 4})]
    )
    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=client,
    )

    result = gateway.extract_structured(
        system_prompt="sys", user_prompt="user", schema=Point
    )

    assert result == Point(x=3, y=4)
    assert len(client.chat.completions.calls) == 2


def test_raises_after_max_retries_exhausted():
    client = FakeOpenAIClient(["not json", "still not json", "nope"])
    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        max_retries=2,
        client=client,
    )

    with pytest.raises(SchemaExtractionFailed):
        gateway.extract_structured(system_prompt="sys", user_prompt="user", schema=Point)

    assert len(client.chat.completions.calls) == 3  # 首次 + 2 次重试


def test_audit_hook_called_with_expected_fields():
    client = FakeOpenAIClient([json.dumps({"x": 1, "y": 2})])
    recorded = []

    class RecordingHook:
        def record(self, **kwargs):
            recorded.append(kwargs)

    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=client,
        audit_hook=RecordingHook(),
    )

    gateway.extract_structured(
        system_prompt="sys", user_prompt="user", schema=Point, prompt_version="v2"
    )

    assert len(recorded) == 1
    call = recorded[0]
    assert call["model"] == "deepseek-chat-241226"
    assert call["response_model"] == "deepseek-chat-241226"
    assert call["prompt_version"] == "v2"
    assert "raw_response" in call and "latency_ms" in call and "token_usage" in call


def test_audit_hook_records_actual_response_model_separately_from_configured():
    """
    工程铁律 5（2026-08-09 现行版）：DeepSeek 这类供应商只给会漂移的别名
    （如 deepseek-chat），配置里写的名字不算数，必须记住 API 响应实际
    返回的 model 字段——且要和配置值分开存，不能互相代替。
    """
    client = FakeOpenAIClient(
        [json.dumps({"x": 1, "y": 2})],
        response_model="deepseek-chat-241226",
    )
    recorded = []

    class RecordingHook:
        def record(self, **kwargs):
            recorded.append(kwargs)

    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat",  # 配置里写的是会漂移的别名
        supports_json_schema=False,
        client=client,
        audit_hook=RecordingHook(),
    )

    gateway.extract_structured(system_prompt="sys", user_prompt="user", schema=Point)

    assert len(recorded) == 1
    call = recorded[0]
    assert call["model"] == "deepseek-chat"                   # 配置值
    assert call["response_model"] == "deepseek-chat-241226"   # API 实际返回值
    assert call["model"] != call["response_model"]


def test_json_schema_mode_sets_response_format():
    client = FakeOpenAIClient([json.dumps({"x": 1, "y": 2})])
    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="doubao-seed-2.1-turbo-241215",
        supports_json_schema=True,
        client=client,
    )

    gateway.extract_structured(system_prompt="sys", user_prompt="user", schema=Point)

    response_format = client.chat.completions.calls[0]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == "Point"
    # strict=true 的 schema 必须是 strict 规范形态，不能是 pydantic 的原始输出
    sent_schema = response_format["json_schema"]["schema"]
    assert sent_schema["additionalProperties"] is False
    assert sorted(sent_schema["required"]) == ["x", "y"]


def _walk_schema_nodes(node):
    """深度遍历 schema 里的每个 dict 节点（含 properties/items/anyOf 里的嵌套）。"""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_schema_nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_schema_nodes(item)


def test_to_strict_json_schema_inlines_refs_and_closes_every_object():
    """
    回归测试（review Important 发现4）：JobProfile.model_json_schema() 会产出
    $defs/$ref 引用，且既不设 additionalProperties:false，也不把所有属性列进
    required——而 OpenAI strict 结构化输出规范（以及照抄它的国内 OpenAI 兼容
    供应商，比如 scripts/compare_models.py 里 supports_json_schema=True 的
    doubao）三条都要求。修复前 _call_model 把原始输出直接配上 "strict": true
    发出去，真实供应商会直接拒绝——而现有测试全都用 supports_json_schema=False，
    这条路径从来没有被覆盖过。

    这里选的是"完全内联"路线（$defs/$ref 全部展开），理由见 gateway 里
    _to_strict_json_schema 的注释：内联后不依赖供应商对 $ref 的支持程度，
    是所有实现的交集，最不容易被拒。
    """
    from app.llm.gateway import _to_strict_json_schema
    from app.schemas.job_profile import JobProfile

    strict = _to_strict_json_schema(JobProfile)

    serialized = json.dumps(strict, ensure_ascii=False)
    assert "$ref" not in serialized, "strict 模式下引用必须全部内联"
    assert "$defs" not in serialized

    object_nodes = [n for n in _walk_schema_nodes(strict) if n.get("type") == "object"]
    # JobProfile 本身 + SkillItem + SopProject 至少三个对象层级
    assert len(object_nodes) >= 3
    for node in object_nodes:
        assert node.get("additionalProperties") is False, (
            f"对象层级没有关闭 additionalProperties: {node.get('title') or node}"
        )
        assert sorted(node.get("required", [])) == sorted(node.get("properties", {}).keys()), (
            "strict 规范要求每个属性都列进 required（可选性用 nullable 表达），"
            f"不一致的节点: {node.get('title') or node}"
        )

    # 嵌套模型和枚举取值都要被真的展开出来，而不是只剩一个引用壳子
    assert "vehicle_model" in serialized  # SopProject 的字段
    assert "ASIL-B" in serialized  # FunctionalSafetyLevel 的枚举取值

    # pydantic 的 Field(ge=1) 会产出 "minimum"，strict 模式不支持这类校验关键字
    assert "minimum" not in serialized
    assert '"default"' not in serialized


def test_free_form_dict_schema_falls_back_to_json_object_mode():
    """
    strict 模式表达不了"任意键值的自由 object"（_IntakeTurnSchema.profile_patch
    就是 dict）：给一个没有 properties 的 object 加上 additionalProperties:false，
    语义上等于"只准返回空对象"，比被供应商拒绝更糟——模型会一直返回 {}，而且
    没人会发现。所以检测到自由 object 时降级回 json_object 模式。
    """
    from app.agents.intake_agent import _IntakeTurnSchema

    client = FakeOpenAIClient(
        [json.dumps({"is_job_related": True, "questions": [], "profile_patch": {"a": 1}})]
    )
    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="doubao-seed-2.1-turbo-241215",
        supports_json_schema=True,
        client=client,
    )

    gateway.extract_structured(
        system_prompt="sys", user_prompt="user", schema=_IntakeTurnSchema
    )

    response_format = client.chat.completions.calls[0]["response_format"]
    assert response_format == {"type": "json_object"}


def test_json_object_mode_embeds_schema_in_system_prompt():
    """
    json_object 模式下供应商只保证"是合法 JSON"、不校验形状，所以 Schema 必须写进
    system prompt。修复前 scripts/compare_models.py 的 EXTRACTION_SYSTEM_PROMPT 写着
    "字段需符合给定 Schema"，但网关根本没有把 Schema 给出去——对 deepseek / qwen
    这两个 supports_json_schema=False 的候选供应商来说，模型只能靠猜字段名。
    """
    from app.schemas.job_profile import JobProfile

    client = FakeOpenAIClient(
        [
            json.dumps(
                {
                    "job_title": "嵌入式软件工程师",
                    "department": "研发部",
                    "headcount": 1,
                    "education_requirement": "本科",
                    "experience_years": "3-5年",
                }
            )
        ]
    )
    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=client,
    )

    gateway.extract_structured(
        system_prompt="sys", user_prompt="要个做嵌入式开发的", schema=JobProfile
    )

    system_content = client.chat.completions.calls[0]["messages"][0]["content"]
    assert "education_requirement" in system_content
    assert "ASIL-B" in system_content  # 枚举取值要原样出现，否则模型会写成 "ASIL B"
