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
