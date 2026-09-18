from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from app.agents.resume_parser import compute_parse
from app.llm.gateway import LLMGateway
from app.parsing.spans import split_into_spans


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


@dataclass
class _FakeUsage:
    prompt_tokens: int = 10
    completion_tokens: int = 10


class _FakeResponse:
    def __init__(self, content: str, model: str = "deepseek-chat") -> None:
        self.choices = [_FakeChoice(content)]
        self.model = model
        self.system_fingerprint = None
        self.usage = _FakeUsage()


class _FakeCompletions:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def create(self, **_kwargs):
        return _FakeResponse(json.dumps(self._payload, ensure_ascii=False))


class _FakeChat:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.completions = _FakeCompletions(payload)


class _FakeClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.chat = _FakeChat(payload)


def _gateway(payload: dict[str, Any]) -> LLMGateway:
    return LLMGateway(
        api_key="x",
        base_url="https://example.invalid",
        model="deepseek-chat",
        supports_json_schema=False,
        client=_FakeClient(payload),
    )


SPANS_TEXT = "张三\n工作年限：5年\n技能：Python、Rust\n"


def _field(value, span_id=None, quote=None, not_mentioned=False, confidence=0.9):
    return {
        "not_mentioned": not_mentioned,
        "confidence": confidence,
        "value": value,
        "spans": [] if span_id is None else [{"span_id": span_id, "quote": quote}],
    }


def test_field_with_resolvable_span_keeps_model_confidence():
    spans = split_into_spans(SPANS_TEXT)
    payload = {
        "name": _field("张三", span_id=1, quote="张三", confidence=0.95),
        "years_of_experience": _field(5, span_id=2, quote="5年", confidence=0.8),
        "skills": {**_field([], not_mentioned=False, confidence=0.7),
                   "value": ["Python", "Rust"], "spans": [{"span_id": 3, "quote": "Python、Rust"}]},
        "companies": _field([], not_mentioned=True, confidence=1.0),
        "education": {**_field(None, not_mentioned=True, confidence=1.0)},
        "expected_city": _field(None, not_mentioned=True, confidence=1.0),
    }
    fields, meta = compute_parse(_gateway(payload), spans=spans)
    assert fields.name.value == "张三"
    assert fields.name.confidence == 0.95
    assert fields.name.spans[0].start == 0
    assert fields.name.spans[0].end == 2
    assert meta.response_model == "deepseek-chat"


def test_field_without_resolvable_quote_gets_zero_confidence():
    spans = split_into_spans(SPANS_TEXT)
    payload = {
        "name": _field("李四", span_id=1, quote="这句话原文里没有", confidence=0.9),
        "years_of_experience": _field(None, not_mentioned=True, confidence=1.0),
        "skills": _field([], not_mentioned=True, confidence=1.0),
        "companies": _field([], not_mentioned=True, confidence=1.0),
        "education": _field(None, not_mentioned=True, confidence=1.0),
        "expected_city": _field(None, not_mentioned=True, confidence=1.0),
    }
    fields, _meta = compute_parse(_gateway(payload), spans=spans)
    assert fields.name.confidence == 0.0


def test_not_mentioned_field_is_untouched():
    spans = split_into_spans(SPANS_TEXT)
    payload = {
        "name": _field("张三", span_id=1, quote="张三", confidence=0.9),
        "years_of_experience": _field(None, not_mentioned=True, confidence=1.0),
        "skills": _field([], not_mentioned=True, confidence=1.0),
        "companies": _field([], not_mentioned=True, confidence=1.0),
        "education": _field(None, not_mentioned=True, confidence=1.0),
        "expected_city": _field(None, not_mentioned=True, confidence=1.0),
    }
    fields, _meta = compute_parse(_gateway(payload), spans=spans)
    assert fields.expected_city.not_mentioned is True
    assert fields.expected_city.confidence == 1.0  # 未提及字段不做置信度改写
