from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class SchemaExtractionFailed(Exception):
    """重试耗尽后仍未拿到符合 Schema 的结构化输出。"""


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
    def record(
        self,
        *,
        model: str,
        response_model: str | None,
        system_fingerprint: str | None,
        prompt_version: str,
        input_hash: str,
        raw_response: str,
        token_usage: dict[str, Any],
        latency_ms: float,
    ) -> None: ...


class NoopAuditHook:
    """
    默认审计钩子：只打日志，不落库。
    完整的 analysis_run 持久化是技术债（见计划开头 技术债），
    这里保留可插拔的调用点，接线时只需替换这一个实现。
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


class LLMGateway:
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
    ) -> None:
        if model == "latest" or model.endswith(":latest") or model.endswith("-latest"):
            raise ValueError(f"禁止使用 latest 类别名锁定模型版本，收到: {model!r}")

        self._model = model
        self._supports_json_schema = supports_json_schema
        self._max_retries = max_retries
        self._audit_hook = audit_hook or NoopAuditHook()
        self._client = client or OpenAI(api_key=api_key, base_url=base_url)

    def extract_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[T],
        prompt_version: str = "v1",
    ) -> T:
        """原签名保留：不关心时序的调用方（jd_agent、scripts/compare_models.py）继续用这个。"""
        parsed, _meta = self.extract_structured_with_meta(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            prompt_version=prompt_version,
        )
        return parsed

    def extract_structured_with_meta(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[T],
        prompt_version: str = "v1",
    ) -> tuple[T, LLMCallMeta]:
        input_hash = hashlib.sha256(
            f"{system_prompt}\n{user_prompt}".encode("utf-8")
        ).hexdigest()

        last_error: Exception | None = None
        attempts = self._max_retries + 1
        total_latency_ms = 0.0

        for attempt_index in range(attempts):
            started = time.monotonic()
            response = self._call_model(system_prompt, user_prompt, schema)
            latency_ms = (time.monotonic() - started) * 1000
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
                model=self._model,
                response_model=response_model,
                system_fingerprint=system_fingerprint,
                prompt_version=prompt_version,
                input_hash=input_hash,
                raw_response=raw_content,
                token_usage=token_usage,
                latency_ms=latency_ms,
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
                attempts=attempt_index + 1,
            )

        raise SchemaExtractionFailed(
            f"{attempts} 次尝试后仍未通过 Schema 校验（{schema.__name__}）: {last_error}"
        ) from last_error

    def _call_model(self, system_prompt: str, user_prompt: str, schema: type[BaseModel]):
        strict_schema = _to_strict_json_schema(schema) if self._supports_json_schema else None
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

        return self._client.chat.completions.create(
            model=self._model,
            temperature=0,
            messages=messages,
            response_format=response_format,
        )
