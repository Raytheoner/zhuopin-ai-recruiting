from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Protocol, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class SchemaExtractionFailed(Exception):
    """重试耗尽后仍未拿到符合 Schema 的结构化输出。"""


class AuditHook(Protocol):
    def record(
        self,
        *,
        model: str,
        response_model: str | None,
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
        input_hash = hashlib.sha256(
            f"{system_prompt}\n{user_prompt}".encode("utf-8")
        ).hexdigest()

        last_error: Exception | None = None
        attempts = self._max_retries + 1

        for _ in range(attempts):
            started = time.monotonic()
            response = self._call_model(system_prompt, user_prompt, schema)
            latency_ms = (time.monotonic() - started) * 1000
            raw_content = response.choices[0].message.content

            # 铁律 5（2026-08-09 现行版）：response.model 是 API 实际返回的模型标识，
            # 与构造函数传入的配置值 self._model 分开记录——配置里写的名字不算数，
            # 供应商静默升级 deepseek-chat 这类别名时，只有响应里的值可信。
            response_model = getattr(response, "model", None)

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
                prompt_version=prompt_version,
                input_hash=input_hash,
                raw_response=raw_content,
                token_usage=token_usage,
                latency_ms=latency_ms,
            )

            try:
                data = json.loads(raw_content)
                return schema.model_validate(data)
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
                continue

        raise SchemaExtractionFailed(
            f"{attempts} 次尝试后仍未通过 Schema 校验（{schema.__name__}）: {last_error}"
        ) from last_error

    def _call_model(self, system_prompt: str, user_prompt: str, schema: type[BaseModel]):
        if self._supports_json_schema:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                    "strict": True,
                },
            }
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        else:
            response_format = {"type": "json_object"}
            messages = [
                {
                    "role": "system",
                    "content": system_prompt + "\n只输出合法 JSON，不要输出任何其他文字。",
                },
                {"role": "user", "content": user_prompt},
            ]

        return self._client.chat.completions.create(
            model=self._model,
            temperature=0,
            messages=messages,
            response_format=response_format,
        )
