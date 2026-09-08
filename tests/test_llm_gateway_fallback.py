"""WBS 2.3：双供应商切换与降级。切换事件记入 analysis_run（这里断言的是喂给
AuditHook 的那一行，落库那一段由 tests/test_audit_hook.py 覆盖）。"""

import json
from dataclasses import dataclass, field

import httpx
import pytest
from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import BaseModel

from app.llm.gateway import (
    PROVIDER_SWITCH_EVENT,
    LLMGateway,
    LLMProviderUnavailable,
    SchemaExtractionFailed,
)


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
    model: str
    usage: FakeUsage = field(default_factory=FakeUsage)
    system_fingerprint: str | None = None


class ScriptedCompletions:
    """按脚本逐次返回：字符串 = 正常响应体，异常实例 = 这次调用抛出去。"""

    def __init__(self, script, response_model):
        self._script = list(script)
        self._response_model = response_model
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResponse(
            choices=[FakeChoice(message=FakeMessage(content=item))],
            model=self._response_model,
        )


class ScriptedClient:
    def __init__(self, script, response_model):
        self.chat = type("Chat", (), {})()
        self.chat.completions = ScriptedCompletions(script, response_model)


class RecordingHook:
    def __init__(self):
        self.records = []

    def record(self, **kwargs):
        self.records.append(kwargs)


def _request():
    return httpx.Request("POST", "https://primary.example.com/v1/chat/completions")


def _status_error(code: int) -> APIStatusError:
    return APIStatusError(
        f"HTTP {code}", response=httpx.Response(code, request=_request()), body=None
    )


def _gateway(primary_script, fallback_script=None, hook=None, **kwargs):
    return LLMGateway(
        api_key="k",
        base_url="https://primary.example.com/v1",
        model="primary-model-241226",
        supports_json_schema=True,
        audit_hook=hook,
        client=ScriptedClient(primary_script, "primary-model-241226-actual"),
        fallback_model="fallback-model-250101" if fallback_script is not None else "",
        fallback_client=(
            ScriptedClient(fallback_script, "fallback-model-250101-actual")
            if fallback_script is not None
            else None
        ),
        fallback_supports_json_schema=True,
        **kwargs,
    )


def _extract(gateway):
    return gateway.extract_structured(
        system_prompt="s", user_prompt="u", schema=Point, prompt_version="v9"
    )


def _switch_rows(hook):
    rows = []
    for record in hook.records:
        raw = record["raw_response"]
        if raw and raw.startswith("{"):
            payload = json.loads(raw)
            if payload.get("event") == PROVIDER_SWITCH_EVENT:
                rows.append((record, payload))
    return rows


def test_primary_5xx_switches_to_fallback_and_returns_its_answer():
    hook = RecordingHook()
    gateway = _gateway([_status_error(503)], ['{"x": 1, "y": 2}'], hook=hook)

    assert _extract(gateway) == Point(x=1, y=2)

    # 两行 analysis_run：切换事件 + 备用方的真实调用。
    assert len(hook.records) == 2
    (switch_record, switch_payload), = _switch_rows(hook)
    assert switch_record["model"] == "primary-model-241226"
    assert switch_record["response_model"] is None
    assert switch_record["token_usage"] == {}
    assert switch_payload["failed_role"] == "primary"
    assert switch_payload["error_type"] == "APIStatusError"
    assert switch_payload["switched_to_model"] == "fallback-model-250101"

    # 铁律 5：切过去之后记的必须是**备用方响应返回的 model**，不是配置里的名字。
    answered = hook.records[1]
    assert answered["model"] == "fallback-model-250101"
    assert answered["response_model"] == "fallback-model-250101-actual"


def test_timeout_and_connection_errors_also_switch():
    for exc in (
        APITimeoutError(request=_request()),
        APIConnectionError(message="boom", request=_request()),
    ):
        gateway = _gateway([exc], ['{"x": 3, "y": 4}'], hook=RecordingHook())
        assert _extract(gateway) == Point(x=3, y=4)


def test_4xx_does_not_switch_and_never_touches_the_fallback():
    hook = RecordingHook()
    gateway = _gateway([_status_error(401)], ['{"x": 1, "y": 2}'], hook=hook)

    with pytest.raises(APIStatusError):
        _extract(gateway)

    assert gateway._fallback.client.chat.completions.calls == []
    assert hook.records == []  # ⛔ 4xx 不是切换事件，不记切换行


def test_schema_validation_failure_retries_on_the_same_provider():
    """2.5 的重试与 2.3 的切换是两回事：模型答得不对，换一家没有意义。"""
    hook = RecordingHook()
    gateway = _gateway(
        ["not json", '{"x": "bad"}', "still not json"], ['{"x": 1, "y": 2}'], hook=hook
    )

    with pytest.raises(SchemaExtractionFailed):
        _extract(gateway)

    assert len(gateway._primary.client.chat.completions.calls) == 3  # max_retries=2 → 3 次
    assert gateway._fallback.client.chat.completions.calls == []
    assert _switch_rows(hook) == []


def test_no_fallback_configured_raises_provider_unavailable_and_still_records_it():
    hook = RecordingHook()
    gateway = _gateway([_status_error(500)], hook=hook)

    with pytest.raises(LLMProviderUnavailable):
        _extract(gateway)

    (record, payload), = _switch_rows(hook)
    assert payload["switched_to_role"] is None  # 无处可切，事件照记
    assert record["temperature"] == 0
    assert record["prompt_version"] == "v9"


def test_both_providers_down_raises_provider_unavailable_with_two_events():
    hook = RecordingHook()
    gateway = _gateway([_status_error(502)], [_status_error(503)], hook=hook)

    with pytest.raises(LLMProviderUnavailable):
        _extract(gateway)

    rows = _switch_rows(hook)
    assert [payload["failed_role"] for _record, payload in rows] == ["primary", "fallback"]


def test_fallback_4xx_after_switch_raises_provider_unavailable_not_the_raw_error():
    """review I-1：已切到备用之后，备用家的 4xx 语义是"主备两家都没答上"
    （LLMProviderUnavailable 的定义），⛔ 不是"这次请求本身有问题"。

    ⛔ 不违反"4xx 不许触发切换"：这里根本没有发生任何切换（已经是最后一家），
    _is_switchable 的早抛只在"尚未切换"时才适用。"""
    hook = RecordingHook()
    gateway = _gateway([_status_error(503)], [_status_error(401)], hook=hook)

    with pytest.raises(LLMProviderUnavailable):
        _extract(gateway)

    rows = _switch_rows(hook)
    assert len(rows) == 2
    (first_record, first_payload), (second_record, second_payload) = rows
    assert first_payload["switched_to_role"] == "fallback"
    assert second_payload["switched_to_role"] is None
    assert second_payload["failed_role"] == "fallback"
    assert second_payload["error_type"] == "APIStatusError"
    # 备用家从没被真正调用出结果，⛔ 不应该多出第三行「成功应答」记录。
    assert len(hook.records) == 2


def test_attempt_numbers_are_unique_across_the_switch():
    """app/audit/hook.py 的 _event_id 拼了 attempt。重号 = 第二行被主键静默丢掉。"""
    hook = RecordingHook()
    gateway = _gateway([_status_error(503)], ["not json", '{"x": 1, "y": 2}'], hook=hook)

    assert _extract(gateway) == Point(x=1, y=2)
    attempts = [record["attempt"] for record in hook.records]
    assert attempts == [1, 2, 3]
    assert len(set(attempts)) == len(attempts)


def test_input_hash_is_identical_across_the_switch():
    """同一次调用的所有留痕行必须能按 input_hash 串起来，否则查不出这是一次调用。"""
    hook = RecordingHook()
    gateway = _gateway([_status_error(503)], ['{"x": 1, "y": 2}'], hook=hook)
    _extract(gateway)
    assert len({record["input_hash"] for record in hook.records}) == 1


def test_fallback_model_rejects_latest_alias():
    with pytest.raises(ValueError, match="latest"):
        LLMGateway(
            api_key="k",
            base_url="https://p/v1",
            model="primary-model-241226",
            supports_json_schema=False,
            fallback_model="deepseek-chat-latest",
            fallback_client=ScriptedClient([], "x"),
        )


def test_partially_configured_fallback_runs_as_no_fallback(caplog):
    gateway = LLMGateway(
        api_key="k",
        base_url="https://p/v1",
        model="primary-model-241226",
        supports_json_schema=False,
        client=ScriptedClient([_status_error(503)], "p"),
        fallback_base_url="https://backup/v1",
        fallback_model="fallback-model-250101",
        # ⛔ 缺 fallback_api_key：⛔ 不复用主家的 key
    )
    assert gateway._fallback is None
    # T2-a：半填 .env 是最可能的首次部署错误，这条 WARNING 是运维在 .51 上
    # 唯一的信号——⛔ 不能被"删掉整块 logger.warning 也全绿"这种改动吃掉。
    assert "配置不全" in caplog.text
    with pytest.raises(LLMProviderUnavailable):
        _extract(gateway)


def test_unconfigured_fallback_leaves_behaviour_byte_for_byte_unchanged():
    hook = RecordingHook()
    gateway = _gateway(['{"x": 7, "y": 8}'], hook=hook)
    assert gateway._fallback is None
    assert _extract(gateway) == Point(x=7, y=8)
    assert [record["attempt"] for record in hook.records] == [1]
