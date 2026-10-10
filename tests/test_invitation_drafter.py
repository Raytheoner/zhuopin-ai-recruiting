"""invitation_drafter 纯函数（U3 tasks 3.2）的单元测试。

用与 tests/test_letter_drafter.py 同款的 scripted client 打桩 LLM 网关
（⛔ 不联网、⛔ 不真调模型）。"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path

from app.agents.invitation_drafter import (
    DEFAULT_CONTACT_HINT,
    INVITATION_PROMPT_VERSION,
    _INVITATION_SYSTEM_PROMPT,
    InvitationSlotFacts,
    compute_invitation_draft,
)
from app.agents.jd_agent import AI_LABEL_PREFIX
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
        self.calls: list[dict] = []

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
        api_key="k", base_url="https://example.invalid", model="deepseek-chat",
        supports_json_schema=True, client=scripted,
    )


def _facts() -> InvitationSlotFacts:
    return InvitationSlotFacts(
        candidate_name="张三", job_title="嵌入式软件工程师", round=1,
        start_at="2026-10-12 14:00", end_at="2026-10-12 15:00",
        mode="onsite", location_or_link="无锡市新吴区××路 1 号",
        interviewer_names=["汤丽萍"],
    )


def test_uses_json_schema_prompt_version_and_temperature_zero():
    scripted = _ScriptedClient([json.dumps({"body": "您好"})])
    draft = compute_invitation_draft(
        _gateway(scripted), facts=_facts(),
        template_body="{candidate_name} 您好", template_version="v1",
    )
    call = scripted.chat.completions.calls[0]
    assert call["temperature"] == 0
    assert call["response_format"]["type"] == "json_schema"
    assert call["response_format"]["json_schema"]["strict"] is True
    assert draft.prompt_version == INVITATION_PROMPT_VERSION == "invite-v1"


def test_response_model_taken_from_api_response():
    scripted = _ScriptedClient([json.dumps({"body": "您好"})])
    draft = compute_invitation_draft(
        _gateway(scripted), facts=_facts(),
        template_body="{candidate_name} 您好", template_version="v1",
    )
    assert draft.response_model == "deepseek-chat-actual-v1"


def test_body_carries_ai_label_same_as_jd():
    scripted = _ScriptedClient([json.dumps({"body": "您好"})])
    draft = compute_invitation_draft(
        _gateway(scripted), facts=_facts(),
        template_body="{candidate_name} 您好", template_version="v1",
    )
    assert AI_LABEL_PREFIX in draft.body


def test_consecutive_generations_have_distinct_run_ids():
    scripted = _ScriptedClient([json.dumps({"body": "您好"}), json.dumps({"body": "您好"})])
    gateway = _gateway(scripted)
    first = compute_invitation_draft(
        gateway, facts=_facts(), template_body="模板", template_version="v1"
    )
    second = compute_invitation_draft(
        gateway, facts=_facts(), template_body="模板", template_version="v1"
    )
    assert first.run_id != second.run_id


def test_contact_hint_falls_back_to_placeholder_contact():
    scripted = _ScriptedClient([json.dumps({"body": "您好"})])
    compute_invitation_draft(
        _gateway(scripted), facts=_facts(), template_body="模板", template_version="v1",
        contact_hint="   ",
    )
    user_prompt = scripted.chat.completions.calls[0]["messages"][-1]["content"]
    assert DEFAULT_CONTACT_HINT in user_prompt


def test_system_prompt_forbids_scores_ranks_and_rejection_reasons():
    assert "MUST NOT 输出任何评分、排名、硬门槛命中、淘汰理由" in _INVITATION_SYSTEM_PROMPT
    assert "不得编造" in _INVITATION_SYSTEM_PROMPT


def test_slot_facts_schema_has_no_score_fields():
    props = set(InvitationSlotFacts.model_json_schema()["properties"])
    forbidden = ("score", "rank", "reject", "评分", "排名", "淘汰")
    assert [p for p in props if any(k in p.lower() or k in p for k in forbidden)] == []


def _imported_module_names(source: str) -> set[str]:
    """扫真正的 import 语句（`ast.Import` 与 `ast.ImportFrom` 都覆盖）。
    ⛔ 不用子串扫描：本模块 docstring 里就写着「不 import app.storage」，
    子串版会被自己的注释绊倒。同款先例见 tests/test_letter_drafter.py。"""
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_module_has_no_storage_write():
    src = Path("app/agents/invitation_drafter.py").read_text(encoding="utf-8")
    imported = _imported_module_names(src)
    assert not [m for m in imported if m == "app.storage" or m.startswith("app.storage.")]
    assert not [m for m in imported if m == "app.graph" or m.startswith("app.graph.")]
    assert "conn.execute" not in src
