from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from openai import APIConnectionError, APIStatusError, OpenAI
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class SchemaExtractionFailed(Exception):
    """重试耗尽后仍未拿到符合 Schema 的结构化输出。"""


class LLMProviderUnavailable(Exception):
    """
    主备供应商都答不上来（传输层故障）。

    ⛔ 与 `SchemaExtractionFailed` **刻意分成两个类型**：那个是"模型答了、但没按
    schema 答"（内容问题，重试有意义、切供应商没意义），这个是"根本没答上"
    （供应商问题，切供应商有意义、在同一家重试没意义）。合成一个异常之后，
    编排层就再也分不出"转人工的原因是模型不听话"还是"供应商挂了"——而这两件事
    的人工处置完全不同。
    """


# 供应商角色。⛔ 两个字面量只在这里定义：analysis_run 的切换事件行、日志、
# 测试断言全部引用它们，散落成字符串就会出现"日志里写 backup、断言里写
# fallback"这种查不出来的不一致。
PROVIDER_PRIMARY = "primary"
PROVIDER_FALLBACK = "fallback"

# 切换事件写进 `analysis_run.raw_response` 的标记（2.3「切换事件记入
# analysis_run」）。⛔ 不新建日志表、不给 analysis_run 加列：那张表已经带齐
# 工程铁律 3 的全部字段，切换事件只是"这次调用没拿到响应"的一行留痕。
PROVIDER_SWITCH_EVENT = "provider_switch"

# 切换判据的分界线，**写死**。5xx = 供应商自己的问题，切；4xx = 请求本身的
# 问题（鉴权、参数、配额），换一家照样错，⛔ 不切。
_SWITCHABLE_STATUS_FLOOR = 500


def _rejects_latest_alias(model: str) -> None:
    """工程铁律 5：模型版本显式锁定，禁止 `latest` 类别名。主备一视同仁——
    备用供应商上漂了版本，历史评分照样失去解释力。"""
    if model == "latest" or model.endswith(":latest") or model.endswith("-latest"):
        raise ValueError(f"禁止使用 latest 类别名锁定模型版本，收到: {model!r}")


def _is_switchable(exc: Exception) -> bool:
    """
    这个异常该不该切备用供应商。**判据写死在这里，⛔ 不做可配置**——
    "什么算供应商挂了"是一条业务契约，配置化之后 .51 上改一行 .env 就能让
    4xx 也去打备用供应商，把一个鉴权配置错误放大成两家供应商的账单。

    切：HTTP 5xx、超时、连接错误（`APITimeoutError` 是 `APIConnectionError`
        的子类，一条 isinstance 就都盖住了）
    ⛔ 不切：4xx（鉴权/参数/配额——换一家照样错）、schema 校验失败
        （那是内容问题，由 max_retries 在**同一家**上重试）
    """
    if isinstance(exc, APIConnectionError):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code >= _SWITCHABLE_STATUS_FLOOR
    return False


# OpenAI strict 结构化输出规范（以及照抄该规范的 OpenAI 兼容供应商）不接受这些
# 校验关键字，带着它们发过去会被直接拒绝。pydantic 的 Field(ge=1)、字段默认值
# 等都会产出其中的项（例如 JobProfile.headcount 的 "minimum": 1）。
# 丢掉它们不会放松校验：extract_structured 拿到响应后仍然会用完整的 pydantic
# 模型 model_validate 一次，不合法就走重试。
_STRICT_UNSUPPORTED_KEYWORDS = frozenset(
    {
        "default",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
        "uniqueItems",
    }
)


def _strictify_node(node: Any, defs: dict) -> Any:
    """递归把一个 JSON Schema 节点改写成 strict 规范形态。"""
    if isinstance(node, list):
        return [_strictify_node(item, defs) for item in node]
    if not isinstance(node, dict):
        return node

    if "$ref" in node:
        # 内联引用：把 $defs 里的定义整个展开到引用点。同级的其他关键字
        # （例如 description）覆盖被引用定义里的同名项。
        target = defs[node["$ref"].rsplit("/", 1)[-1]]
        siblings = {k: v for k, v in node.items() if k != "$ref"}
        return _strictify_node({**target, **siblings}, defs)

    out: dict = {}
    for key, value in node.items():
        if key in _STRICT_UNSUPPORTED_KEYWORDS or key == "$defs":
            continue
        if key == "properties" and isinstance(value, dict):
            # properties 的键是字段名，不是 schema 关键字——必须按映射处理，
            # 否则名叫 "type" / "properties" 的字段会把下面的判断带偏。
            out[key] = {name: _strictify_node(sub, defs) for name, sub in value.items()}
        else:
            out[key] = _strictify_node(value, defs)

    if out.get("type") == "object":
        out["additionalProperties"] = False
        # strict 规范要求所有属性都列进 required，可选性用 nullable 类型
        # （pydantic 对 `X | None` 产出的 anyOf[..., {"type":"null"}]）表达。
        out["required"] = list(out.get("properties", {}).keys())
    return out


def _to_strict_json_schema(schema: type[BaseModel]) -> dict:
    """
    把 pydantic 的 model_json_schema() 输出转成 OpenAI strict 结构化输出能接受的形态：
    $defs/$ref 全部内联、每个 object 层级都 additionalProperties=false、
    每个 object 的所有属性都列进 required、剔除 strict 不支持的校验关键字。

    为什么选"完全内联"而不是"保留 $ref、给每个 $defs 定义也加上 additionalProperties"：
    各家 OpenAI 兼容供应商对 $ref 的支持深浅不一（有的只支持同文档一层引用，有的
    干脆不解析），内联后的 schema 是所有实现的交集，最不容易在真实调用里被拒。
    代价只是 payload 变大一点，对本项目的调用量可以忽略。

    代价二（目前不影响任何调用方）：递归模型（自己引用自己的 Schema）没法内联，
    会栈溢出。真需要递归结构时得改回保留 $ref 的路线，那时必须给每个 $defs 定义
    也补上 additionalProperties=false 和完整的 required。
    """
    raw = schema.model_json_schema()
    defs = raw.get("$defs", {})
    return _strictify_node(raw, defs)


def _has_free_form_object(node: Any) -> bool:
    """
    检测 schema 里是否存在"任意键值的自由 object"（pydantic 对裸 `dict` 字段的产出：
    type=object 但没有 properties）。strict 模式表达不了这种形状——给它加上
    additionalProperties=false 等于告诉模型"只准返回空对象"，这比被供应商拒绝更糟：
    模型会一声不吭地一直返回 {}。
    """
    if isinstance(node, list):
        return any(_has_free_form_object(item) for item in node)
    if not isinstance(node, dict):
        return False
    if node.get("type") == "object" and not node.get("properties"):
        return True
    return any(_has_free_form_object(value) for value in node.values())


class AuditHook(Protocol):
    """
    一次 LLM 调用的留痕落点。**每次尝试各调一次**——重试的每一次都是一次真实的、
    花了钱的 API 调用，都要留痕。

    ⚠️ 网关只负责把参数交出去，⛔ 不解释 `audit_context` 的内容：业务语义
    （application_id / job_id / rubric 快照）由适配层理解，网关继续对业务无知
    （design.md D6）。守护见 `tests/test_llm_gateway.py`
    `test_gateway_never_reads_inside_audit_context`。
    """

    def record(
        self,
        *,
        model: str,
        response_model: str | None,
        system_fingerprint: str | None,
        prompt_version: str,
        temperature: float,
        input_hash: str,
        raw_response: str | None,
        token_usage: dict[str, Any],
        latency_ms: float,
        attempt: int,
        audit_context: dict[str, Any] | None = None,
    ) -> None: ...


class NoopAuditHook:
    """
    ⚠️ **测试专用**（design.md D6）。生产装配处注入的是 `RecorderAuditHook`
    （见 `app/main.py`）——注入点只有一处，回滚 = 换回一行。

    留着它的理由：`LLMGateway` 的单元测试与 `scripts/compare_models.py` 不需要
    一个真实的数据库连接。⛔ 不要在生产路径上用它：它只 `logger.debug`，
    工程铁律 3 在它身上一条都不成立。
    """

    def record(self, **kwargs: Any) -> None:
        logger.debug("audit_hook(noop): %s", kwargs)


@dataclass(frozen=True)
class LLMCallMeta:
    """
    一次 extract_structured 调用的可观测元数据。

    为什么走返回值而不是扩展 AuditHook：AuditHook 的签名不能动
    （design.md 决策 9——ai-audit-trail-and-outbound-gate 正基于现签名设计），
    而调用方（compute_intake_turn → effect_persist_draft）需要在**同一个事务**
    里把耗时和画像一起写下去，hook 是单向的、拿不回来。

    只承载"这次调用花了多久、真正回答的是哪个模型"。prompt 版本、input_hash、
    原始响应仍然只经 AuditHook 走——intake-turn-observability 明确要求时序留痕
    不承担审计职责。
    """

    latency_ms: float
    response_model: str | None
    attempts: int


@dataclass(frozen=True)
class _Provider:
    """一家供应商的三件套。role 只用于留痕与日志，⛔ 不参与任何判定。"""

    role: str
    client: Any
    model: str
    supports_json_schema: bool


class LLMGateway:
    # 铁律 5：temperature 恒为 0。发给 API 的值与记进留痕的值必须是**同一个**
    # 来源——写成两处字面量，改了一处忘了另一处，留痕就开始撒谎且没人发现。
    # 守护：test_recorded_temperature_is_the_temperature_actually_sent 比对的是
    # "真正发出去的" 与 "记下来的" 两侧，不是各自跟字面量比。
    TEMPERATURE = 0

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        supports_json_schema: bool,
        max_retries: int = 2,
        audit_hook: AuditHook | None = None,
        client: Any = None,
        fallback_api_key: str = "",
        fallback_base_url: str = "",
        fallback_model: str = "",
        fallback_supports_json_schema: bool = False,
        fallback_client: Any = None,
    ) -> None:
        _rejects_latest_alias(model)

        self._model = model
        self._supports_json_schema = supports_json_schema
        self._max_retries = max_retries
        self._audit_hook = audit_hook or NoopAuditHook()
        self._client = client or OpenAI(api_key=api_key, base_url=base_url)
        self._primary = _Provider(
            role=PROVIDER_PRIMARY,
            client=self._client,
            model=model,
            supports_json_schema=supports_json_schema,
        )
        self._fallback = self._build_fallback(
            api_key=fallback_api_key,
            base_url=fallback_base_url,
            model=fallback_model,
            supports_json_schema=fallback_supports_json_schema,
            client=fallback_client,
        )

    @staticmethod
    def _build_fallback(
        *,
        api_key: str,
        base_url: str,
        model: str,
        supports_json_schema: bool,
        client: Any,
    ) -> _Provider | None:
        """
        备用供应商是**可选**的：一个都不配就返回 None，网关行为与配它之前逐字
        一致（2.3 的默认值要求）。

        ⛔ 配不全时不猜、更不复用主供应商的 api_key：备用是**另一家**供应商，
        主家的 key 拿去打它只会得到 401，而 401 是 4xx——按 `_is_switchable`
        不切、直接抛，等于把"配置漏了一项"变成"整条采集链路挂掉"。配不全时
        一律按「无备用」运行并打 WARNING，方向是保守的那一侧。
        """
        if client is not None:
            # 测试注入路径：给了 client 就必须给 model，否则留痕里记不出这是谁答的。
            if not model:
                raise ValueError("注入了 fallback_client 就必须同时给出 fallback_model")
            _rejects_latest_alias(model)
            return _Provider(
                role=PROVIDER_FALLBACK,
                client=client,
                model=model,
                supports_json_schema=supports_json_schema,
            )

        configured = [bool(api_key), bool(base_url), bool(model)]
        if not any(configured):
            return None
        if not all(configured):
            logger.warning(
                "备用供应商配置不全（LLM_FALLBACK_API_KEY / LLM_FALLBACK_BASE_URL / "
                "LLM_FALLBACK_MODEL 必须同时给全，当前缺失: %s），本进程按「无备用」"
                "运行——主供应商故障时不会切换，会直接抛 LLMProviderUnavailable。",
                [
                    name
                    for name, present in zip(
                        ("api_key", "base_url", "model"), configured
                    )
                    if not present
                ],
            )
            return None

        _rejects_latest_alias(model)
        return _Provider(
            role=PROVIDER_FALLBACK,
            client=OpenAI(api_key=api_key, base_url=base_url),
            model=model,
            supports_json_schema=supports_json_schema,
        )

    def extract_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[T],
        prompt_version: str = "v1",
        audit_context: dict[str, Any] | None = None,
    ) -> T:
        """原签名保留：不关心时序的调用方（jd_agent、scripts/compare_models.py）继续用这个。"""
        parsed, _meta = self.extract_structured_with_meta(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            prompt_version=prompt_version,
            audit_context=audit_context,
        )
        return parsed

    def extract_structured_with_meta(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[T],
        prompt_version: str = "v1",
        audit_context: dict[str, Any] | None = None,
    ) -> tuple[T, LLMCallMeta]:
        input_hash = hashlib.sha256(
            f"{system_prompt}\n{user_prompt}".encode("utf-8")
        ).hexdigest()

        last_error: Exception | None = None
        attempts = self._max_retries + 1
        total_latency_ms = 0.0

        provider = self._primary
        switched = False
        # 每调一次 AuditHook.record 就 +1，**跨供应商单调递增**。
        # ⛔ 不要退回"用循环下标当 attempt"：切换事件行与紧随其后的重试行会拿到
        # 同一个 attempt，而 app/audit/hook.py 的 _event_id 是
        # {thread_id}:{node}:{input_hash}:{attempt}——撞 id 的第二行会被
        # SqliteSink 当成"已写过"静默丢掉，切换事件就此消失且不报错。
        record_seq = 0
        # schema 校验失败才消耗重试预算（2.5「校验失败重试至多 2 次」）。
        # ⛔ 供应商故障不计入：它由"至多切一次"独立封顶，两个预算混用会让
        # "主家超时一次"白白吃掉一次本该留给模型的重试。
        schema_attempts_used = 0

        while schema_attempts_used < attempts:
            started = time.monotonic()
            try:
                response = self._call_model(provider, system_prompt, user_prompt, schema)
            except Exception as exc:
                latency_ms = (time.monotonic() - started) * 1000
                total_latency_ms += latency_ms
                switchable = _is_switchable(exc)
                if not switchable and not switched:
                    # 4xx / 其他，且尚未切换过：换一家照样错，⛔ 不切、⛔ 不重试，
                    # 原样抛给调用方——这条分支管的是"4xx 不许触发切换"。
                    raise
                # 走到这里，要么这次异常本身可切换，要么已经切到备用之后——
                # 已经是最后一家了，无论备用家的异常是 4xx 还是 5xx，都不再有
                # "切换"这个动作可做。switch_to 同时承担两层判断：本次异常是否
                # 可切换、以及是否还有下一家可切。任何一层为否，这次调用的终局
                # 都是同一句话——"主备两家都没答上"（review I-1）。
                switch_to = (
                    None if (not switchable or self._fallback is None or switched) else self._fallback
                )
                record_seq += 1
                self._record_provider_switch(
                    provider=provider,
                    exc=exc,
                    prompt_version=prompt_version,
                    input_hash=input_hash,
                    latency_ms=latency_ms,
                    attempt=record_seq,
                    audit_context=audit_context,
                    switched_to=switch_to,
                )
                if switch_to is None:
                    raise LLMProviderUnavailable(
                        f"供应商不可用且已无可切换的备用（最后一家: {provider.role}/"
                        f"{provider.model}）: {exc!r}"
                    ) from exc
                logger.warning(
                    "主供应商 %s 调用失败（%s），切换到备用供应商 %s；"
                    "切换事件已记入 analysis_run（raw_response 含 %s 标记）。",
                    provider.model,
                    type(exc).__name__,
                    self._fallback.model,
                    PROVIDER_SWITCH_EVENT,
                )
                provider = self._fallback
                switched = True
                continue

            latency_ms = (time.monotonic() - started) * 1000
            schema_attempts_used += 1
            record_seq += 1
            # 累计而不是覆盖：调用方落库的是"这一轮用户等了多久"，重试的时间
            # 用户也在等（intake-turn-observability「重试计入耗时」）。
            # AuditHook 那边继续按单次尝试记录，两个口径互不污染。
            total_latency_ms += latency_ms
            raw_content = response.choices[0].message.content

            # 铁律 5（2026-08-09 现行版）：response.model 是 API 实际返回的模型标识，
            # 与构造函数传入的配置值 self._model 分开记录——配置里写的名字不算数，
            # 供应商静默升级 deepseek-chat 这类别名时，只有响应里的值可信。
            response_model = getattr(response, "model", None)

            # response.model 只是回显请求里的别名，供应商换掉别名底下的实际模型时
            # 它照样原样返回，证明不了版本没变。system_fingerprint（OpenAI 兼容
            # API 的惯例字段，随底层模型/部署变化）才是目前唯一能盯出漂移的信号。
            # 不是所有供应商都带这个字段，缺失时老实记 None，不能让网关炸掉。
            system_fingerprint = getattr(response, "system_fingerprint", None)

            usage = getattr(response, "usage", None)
            token_usage = (
                {
                    "prompt_tokens": getattr(usage, "prompt_tokens", None),
                    "completion_tokens": getattr(usage, "completion_tokens", None),
                }
                if usage is not None
                else {}
            )

            self._audit_hook.record(
                # 配置侧记的是**这一次实际用的那家**的模型名，切到备用之后
                # 就是备用方的名字——记主供应商的名字等于让留痕撒谎。
                model=provider.model,
                response_model=response_model,
                system_fingerprint=system_fingerprint,
                prompt_version=prompt_version,
                temperature=self.TEMPERATURE,
                input_hash=input_hash,
                raw_response=raw_content,
                token_usage=token_usage,
                latency_ms=latency_ms,
                # 每次尝试各记一条，attempt 让它们在 analysis_run.id 上区分得开：
                # 同一次 extract_structured 的多次尝试 input_hash 完全相同，不带
                # attempt 就会互撞，第 2 次起会被主键短路当成"已写过"静默丢掉。
                attempt=record_seq,
                # ⛔ 原样透传，不读、不拷、不改（design.md D6）。
                audit_context=audit_context,
            )

            try:
                data = json.loads(raw_content)
                parsed = schema.model_validate(data)
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
                continue

            return parsed, LLMCallMeta(
                latency_ms=total_latency_ms,
                response_model=response_model,
                attempts=schema_attempts_used,
            )

        raise SchemaExtractionFailed(
            f"{attempts} 次尝试后仍未通过 Schema 校验（{schema.__name__}）: {last_error}"
        ) from last_error

    def _record_provider_switch(
        self,
        *,
        provider: _Provider,
        exc: Exception,
        prompt_version: str,
        input_hash: str,
        latency_ms: float,
        attempt: int,
        audit_context: dict[str, Any] | None,
        switched_to: _Provider | None,
    ) -> None:
        """
        把一次供应商故障（以及随之发生的切换）记成 `analysis_run` 的**一行**。

        ⛔ 不新建表、不加列（2.3「切换事件记入 analysis_run」；AuditHook 的签名
        也不能动，见 LLMCallMeta 的说明）。这一行的判据是自洽的：
        `raw_response` 是一段 JSON，`event` 字段恒为 `provider_switch`；
        `response_model` 为 None（根本没拿到响应）；`token_usage` 为空。
        紧随其后那一行的 `configured_model` 就是切过去的那家。

        ⛔ raw_response 里只放异常类型名与角色/模型名，**不放异常文本**：
        供应商的错误体可能回显请求内容，而 spec 禁止在留痕里存原文。
        """
        self._audit_hook.record(
            model=provider.model,
            response_model=None,
            system_fingerprint=None,
            prompt_version=prompt_version,
            temperature=self.TEMPERATURE,
            input_hash=input_hash,
            raw_response=json.dumps(
                {
                    "event": PROVIDER_SWITCH_EVENT,
                    "failed_role": provider.role,
                    "failed_model": provider.model,
                    "error_type": type(exc).__name__,
                    "switched_to_role": None if switched_to is None else switched_to.role,
                    "switched_to_model": None if switched_to is None else switched_to.model,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            token_usage={},
            latency_ms=latency_ms,
            attempt=attempt,
            audit_context=audit_context,
        )

    def _call_model(
        self,
        provider: _Provider,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
    ):
        strict_schema = _to_strict_json_schema(schema) if provider.supports_json_schema else None
        # 自由 object（裸 dict 字段）在 strict 模式下无法表达，只能降级回
        # json_object 模式，否则模型会被 additionalProperties=false 锁死成只能
        # 返回 {}——静默返回空结果比被供应商拒绝更难排查。
        use_json_schema = strict_schema is not None and not _has_free_form_object(strict_schema)
        if strict_schema is not None and not use_json_schema:
            logger.warning(
                "%s 含有自由 object 字段（裸 dict），strict json_schema 模式表达不了，"
                "本次调用降级为 json_object 模式",
                schema.__name__,
            )

        if use_json_schema:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": strict_schema,
                    "strict": True,
                },
            }
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        else:
            # json_object 模式下供应商只保证"是合法 JSON"，不校验形状，所以必须把
            # Schema 本身写进 system prompt——否则模型只能靠猜字段名、类型和枚举
            # 取值（scripts/compare_models.py 的 EXTRACTION_SYSTEM_PROMPT 写着
            # "字段需符合给定 Schema"，但在修复前根本没有把 Schema 给出去）。
            # 这里用 pydantic 的原始 schema 而不是 strict 版：strict 版会把裸 dict
            # 字段写成 additionalProperties=false / required=[]，等于告诉模型
            # "这个字段只能是空对象"，正好和实际语义相反。
            response_format = {"type": "json_object"}
            messages = [
                {
                    "role": "system",
                    "content": (
                        f"{system_prompt}\n只输出合法 JSON，不要输出任何其他文字。\n"
                        "输出必须符合以下 JSON Schema（字段名、类型、枚举取值原样使用）：\n"
                        f"{json.dumps(schema.model_json_schema(), ensure_ascii=False)}"
                    ),
                },
                {"role": "user", "content": user_prompt},
            ]

        return provider.client.chat.completions.create(
            model=provider.model,
            temperature=self.TEMPERATURE,
            messages=messages,
            response_format=response_format,
        )
