"""letter_drafter 纯函数（U2 tasks 2.2）的单元测试。"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path

from app.agents.jd_agent import AI_LABEL_PREFIX
from app.agents.letter_drafter import LetterFacts, compute_letter_draft
from app.llm.gateway import LLMGateway


@dataclass
class _Msg:
    content: str


@dataclass
class _Choice:
    message: _Msg


@dataclass
class _Usage:
    prompt_tokens: int = 1
    completion_tokens: int = 1


@dataclass
class _Resp:
    choices: list
    model: str
    usage: object
    system_fingerprint: object = None


class _ScriptedCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Resp(
            choices=[_Choice(message=_Msg(content=self._responses.pop(0)))],
            model="deepseek-chat-actual-v1",
            usage=_Usage(),
        )


class _ScriptedChat:
    def __init__(self, responses):
        self.completions = _ScriptedCompletions(responses)


class _ScriptedClient:
    def __init__(self, responses):
        self.chat = _ScriptedChat(responses)


def _gateway(scripted):
    return LLMGateway(
        api_key="k",
        base_url="https://example.invalid",
        model="deepseek-chat",
        supports_json_schema=True,
        client=scripted,
    )


def test_facts_schema_has_no_salary_keys():
    props = set(LetterFacts.model_json_schema()["properties"])
    forbidden = ("salary", "pay", "compensation", "bonus", "薪")
    offenders = [p for p in props if any(k in p.lower() or k in p for k in forbidden)]
    assert offenders == []


def test_compute_uses_json_schema_prompt_version_and_temperature_zero():
    scripted = _ScriptedClient([json.dumps({"body": "张三：您好"})])
    draft = compute_letter_draft(
        _gateway(scripted),
        kind="offer",
        template_body="{candidate_name}：您好",
        template_version=1,
        facts=LetterFacts(candidate_name="张三", job_title="嵌入式工程师"),
    )
    call = scripted.chat.completions.calls[0]
    assert call["temperature"] == 0
    assert call["response_format"]["type"] == "json_schema"
    assert call["response_format"]["json_schema"]["strict"] is True
    assert draft.prompt_version == "letter-offer-v1"


def test_response_model_taken_from_api_response():
    scripted = _ScriptedClient([json.dumps({"body": "您好"})])
    draft = compute_letter_draft(
        _gateway(scripted),
        kind="rejection",
        template_body="{candidate_name}：您好",
        template_version=1,
        facts=LetterFacts(candidate_name="张三", job_title="嵌入式工程师"),
    )
    assert draft.response_model == "deepseek-chat-actual-v1"
    assert draft.prompt_version == "letter-rejection-v1"


def test_body_carries_ai_label_same_as_jd():
    scripted = _ScriptedClient([json.dumps({"body": "您好"})])
    draft = compute_letter_draft(
        _gateway(scripted),
        kind="offer",
        template_body="{candidate_name}：您好",
        template_version=1,
        facts=LetterFacts(candidate_name="张三", job_title="嵌入式工程师"),
    )
    assert AI_LABEL_PREFIX in draft.body


def test_consecutive_generations_have_distinct_run_ids():
    scripted = _ScriptedClient([
        json.dumps({"body": "您好"}),
        json.dumps({"body": "您好"}),
    ])
    gateway = _gateway(scripted)
    facts = LetterFacts(candidate_name="张三", job_title="嵌入式工程师")
    first = compute_letter_draft(
        gateway, kind="offer", template_body="{candidate_name}：您好",
        template_version=1, facts=facts,
    )
    second = compute_letter_draft(
        gateway, kind="offer", template_body="{candidate_name}：您好",
        template_version=1, facts=facts,
    )
    assert first.run_id != second.run_id


def _imported_module_names(source: str) -> set[str]:
    """扫真正的 import 语句（`ast.Import` 与 `ast.ImportFrom` 都要覆盖）。

    ⚠️ **不要**用 `"app.storage" not in src` 这种子串扫描：本模块 docstring 里
    就写着「不 import app.storage」这条规则，子串版会被自己的注释绊倒；而且它
    漏得掉 `from app import storage` 这种写法（拿不到 "app.storage" 这四个字），
    真写库了反而全绿。同款先例与说明见 tests/test_audit_recorder.py 的
    `_modules_importing_config_or_graph`。
    """
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_module_has_no_storage_write():
    src = Path("app/agents/letter_drafter.py").read_text(encoding="utf-8")
    imported = _imported_module_names(src)
    assert not [m for m in imported if m == "app.storage" or m.startswith("app.storage.")]
    assert "conn.execute" not in src
