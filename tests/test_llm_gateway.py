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
    system_fingerprint: str | None = None


class FakeChatCompletions:
    def __init__(
        self,
        responses: list[str],
        response_model: str = "deepseek-chat-241226",
        system_fingerprint: str | None = None,
    ):
        self._responses = list(responses)
        self._response_model = response_model
        self._system_fingerprint = system_fingerprint
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content = self._responses.pop(0)
        return FakeResponse(
            choices=[FakeChoice(message=FakeMessage(content=content))],
            model=self._response_model,
            system_fingerprint=self._system_fingerprint,
        )


class FakeChat:
    def __init__(
        self,
        responses,
        response_model: str = "deepseek-chat-241226",
        system_fingerprint: str | None = None,
    ):
        self.completions = FakeChatCompletions(
            responses, response_model=response_model, system_fingerprint=system_fingerprint
        )


class FakeOpenAIClient:
    def __init__(
        self,
        responses: list[str],
        response_model: str = "deepseek-chat-241226",
        system_fingerprint: str | None = None,
    ):
        self.chat = FakeChat(
            responses, response_model=response_model, system_fingerprint=system_fingerprint
        )


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


def test_audit_hook_records_system_fingerprint_when_present():
    """
    工程铁律 5：response.model 原样回显请求的别名不能证明版本没变——DeepSeek
    换掉别名底下的实际模型时，model 字段照样返回配置里写的那个名字。
    system_fingerprint 会随底层模型/部署变化，是目前唯一能盯出漂移的信号，
    必须和 model / response_model 一起落审计记录。
    """
    client = FakeOpenAIClient(
        [json.dumps({"x": 1, "y": 2})],
        system_fingerprint="fp_9954b31ca7_prod0820_fp8_kvcache_20260402",
    )
    recorded = []

    class RecordingHook:
        def record(self, **kwargs):
            recorded.append(kwargs)

    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-v4-pro",
        supports_json_schema=False,
        client=client,
        audit_hook=RecordingHook(),
    )

    gateway.extract_structured(system_prompt="sys", user_prompt="user", schema=Point)

    assert len(recorded) == 1
    assert recorded[0]["system_fingerprint"] == "fp_9954b31ca7_prod0820_fp8_kvcache_20260402"


def test_audit_hook_records_none_system_fingerprint_when_absent():
    """不是所有供应商都返回 system_fingerprint（响应对象上根本没有这个属性），
    这种情况不能让网关炸掉，只能老实记 None。"""
    client = FakeOpenAIClient([json.dumps({"x": 1, "y": 2})])  # 默认 system_fingerprint=None
    recorded = []

    class RecordingHook:
        def record(self, **kwargs):
            recorded.append(kwargs)

    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="doubao-seed-2.1-turbo-241215",
        supports_json_schema=False,
        client=client,
        audit_hook=RecordingHook(),
    )

    gateway.extract_structured(system_prompt="sys", user_prompt="user", schema=Point)

    assert recorded[0]["system_fingerprint"] is None


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


def test_extract_structured_with_meta_returns_latency_and_response_model():
    class Payload(BaseModel):
        a: int

    client = FakeOpenAIClient(
        [json.dumps({"a": 1})], response_model="deepseek-chat-241226"
    )
    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat",  # 配置里写的是会漂移的别名
        supports_json_schema=False,
        client=client,
    )

    parsed, meta = gateway.extract_structured_with_meta(
        system_prompt="sys", user_prompt="user", schema=Payload
    )

    assert parsed.a == 1
    assert meta.latency_ms >= 0
    # 铁律 5：配置里写的名字不算数，响应返回的才算
    assert meta.response_model == "deepseek-chat-241226"
    assert meta.attempts == 1


def test_meta_latency_accumulates_across_retries(monkeypatch):
    """
    intake-turn-observability「重试计入耗时」：调用方要落库的是"这一轮用户等了
    多久"，不是"最后那次成功的尝试花了多久"。
    """
    from app.llm import gateway as gateway_module

    class Payload(BaseModel):
        a: int

    # (start, end) × 2 次尝试：第一次 1.5s，第二次 2.25s
    ticks = iter([0.0, 1.5, 10.0, 12.25])
    monkeypatch.setattr(gateway_module.time, "monotonic", lambda: next(ticks))

    client = FakeOpenAIClient(["这不是 JSON", json.dumps({"a": 1})])
    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=client,
    )

    _parsed, meta = gateway.extract_structured_with_meta(
        system_prompt="sys", user_prompt="user", schema=Payload
    )

    assert meta.attempts == 2
    assert meta.latency_ms == pytest.approx(3750.0)


def test_audit_hook_records_one_row_per_attempt(monkeypatch):
    """
    "每次尝试记一条"的语义不变（design.md 决策 9 的这一半仍然成立）；**签名在 U3
    扩了三个参数**（temperature / attempt / audit_context），所以本测试从
    ..._with_unchanged_signature 改名——名字里写着"签名不动"而签名已经动了，
    比没有测试更误导。

    理由见 openspec 变更包 ai-audit-trail-and-outbound-gate tasks 3.1 与
    docs/superpowers/plans/2026-08-28-ai-audit-trail-unitU3-recorder-wiring.md
    的偏离登记 1：analysis_run.temperature 是 NOT NULL 而旧签名没有它；
    多次尝试的 input_hash 相同，没有 attempt 会撞主键被静默丢掉。

    ⛔ 不要删下面那条精确的 key 集合断言——它是"有人偷偷再加一个参数"的唯一
    自动判据。
    """
    from app.llm import gateway as gateway_module

    class Payload(BaseModel):
        a: int

    ticks = iter([0.0, 1.5, 10.0, 12.25])
    monkeypatch.setattr(gateway_module.time, "monotonic", lambda: next(ticks))

    class RecordingHook:
        def __init__(self):
            self.calls = []

        def record(self, **kwargs):
            self.calls.append(kwargs)

    hook = RecordingHook()
    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        audit_hook=hook,
        client=FakeOpenAIClient(["这不是 JSON", json.dumps({"a": 1})]),
    )

    _parsed, meta = gateway.extract_structured_with_meta(
        system_prompt="sys", user_prompt="user", schema=Payload
    )

    assert len(hook.calls) == 2  # 每次尝试各一条，语义不变
    # hook 记的是单次尝试耗时；累计只在返回值里，两者不互相污染
    assert [call["latency_ms"] for call in hook.calls] == pytest.approx([1500.0, 2250.0])
    assert meta.latency_ms == pytest.approx(3750.0)
    assert set(hook.calls[0]) == {
        "model",
        "response_model",
        "system_fingerprint",
        "prompt_version",
        "temperature",
        "input_hash",
        "raw_response",
        "token_usage",
        "latency_ms",
        "attempt",
        "audit_context",
    }


def test_extract_structured_still_returns_bare_model():
    """jd_agent 与 scripts/compare_models.py 不关心时序，旧签名必须原样可用。"""

    class Payload(BaseModel):
        a: int

    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=FakeOpenAIClient([json.dumps({"a": 1})]),
    )

    parsed = gateway.extract_structured(
        system_prompt="sys", user_prompt="user", schema=Payload
    )

    assert parsed.a == 1
    assert not isinstance(parsed, tuple)


# ── U3：AuditHook 扩参与网关透传（tasks 3.1）────────────────────────────


def test_audit_context_reaches_the_hook_as_the_very_same_object():
    """
    design D6：网关**原样透传**，不解释内容。断言的是对象同一性（is），不是相等
    ——相等允许网关中途把它拷一份、顺手改几个键再传下去，同一性不允许。
    """
    client = FakeOpenAIClient([json.dumps({"x": 1, "y": 2})])
    seen = []

    class RecordingHook:
        def record(self, **kwargs):
            seen.append(kwargs)

    context = {"thread_id": "job-1", "node": "compute_intake_turn"}
    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=client,
        audit_hook=RecordingHook(),
    )

    gateway.extract_structured(
        system_prompt="sys", user_prompt="user", schema=Point, audit_context=context
    )

    assert seen[0]["audit_context"] is context


def test_gateway_never_reads_inside_audit_context():
    """
    ⭐ "不解释内容"的机械判据。喂一个"一被读就炸"的 context：网关只要写了
    audit_context.get("job_id") / ["thread_id"] / **audit_context 之类的一行，
    这条立刻变红。没有它，"原样透传"只是一句注释。
    """

    class Explosive(dict):
        def __getitem__(self, key):
            raise AssertionError(f"网关读了 audit_context[{key!r}]——它不该解释内容")

        def get(self, *args, **kwargs):
            raise AssertionError("网关调了 audit_context.get()——它不该解释内容")

        def keys(self):
            raise AssertionError("网关展开了 **audit_context——它不该解释内容")

    client = FakeOpenAIClient([json.dumps({"x": 1, "y": 2})])
    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=client,
    )

    result = gateway.extract_structured(
        system_prompt="sys", user_prompt="user", schema=Point, audit_context=Explosive()
    )

    assert result.x == 1


def test_recorded_temperature_is_the_temperature_actually_sent():
    """
    铁律 3 要求 temperature 进留痕，铁律 5 要求它是 0。这条同时钉两件事：

    1. 记下来的值 == 真正发出去的值（比对两侧，不是各自跟字面量比）——
       只跟字面量比的话，有人把发送侧改成 0.7、把记录侧也改成 0.7，两条断言
       一起改完照样全绿，而留痕就开始撒谎了。
    2. 这个值是 0（铁律 5 的字面要求）。
    """
    client = FakeOpenAIClient([json.dumps({"x": 1, "y": 2})])
    seen = []

    class RecordingHook:
        def record(self, **kwargs):
            seen.append(kwargs)

    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=client,
        audit_hook=RecordingHook(),
    )

    gateway.extract_structured(system_prompt="sys", user_prompt="user", schema=Point)

    sent = client.chat.completions.calls[0]["temperature"]
    assert seen[0]["temperature"] == sent
    assert sent == 0


def test_attempt_number_is_one_based_and_increments_per_retry():
    """
    attempt 存在的唯一理由：analysis_run.id 要靠它区分同一次 extract_structured
    里的多次尝试。两次尝试的 input_hash 完全相同，没有 attempt 就会撞主键，
    U2 的短路逻辑会把第 2 次尝试当成"已写过"静默丢掉（sinks.py:156-168）。
    """
    seen = []

    class RecordingHook:
        def record(self, **kwargs):
            seen.append(kwargs)

    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        audit_hook=RecordingHook(),
        client=FakeOpenAIClient(["这不是 JSON", json.dumps({"x": 1, "y": 2})]),
    )

    gateway.extract_structured(system_prompt="sys", user_prompt="user", schema=Point)

    assert [call["attempt"] for call in seen] == [1, 2]


def test_call_sites_that_pass_no_audit_context_still_work():
    """tasks 3.1 逐字：现有调用点不传也能跑。jd_agent 与 compare_models.py 就是这种。"""
    seen = []

    class RecordingHook:
        def record(self, **kwargs):
            seen.append(kwargs)

    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        audit_hook=RecordingHook(),
        client=FakeOpenAIClient([json.dumps({"x": 1, "y": 2})]),
    )

    gateway.extract_structured(system_prompt="sys", user_prompt="user", schema=Point)

    assert seen[0]["audit_context"] is None
