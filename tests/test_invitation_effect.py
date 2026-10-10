"""U3 邀约文案的节点级测试（interview-scheduling U3；本 Task 建首段）。

本文件由 Task 4 起建，覆盖 `app/graph/invitation_nodes.py` 的
`compute_invitation_draft_for_slot` 与 `effect_persist_invitation_draft`
（⚠️ 计划里这个节点叫 `effect_persist_draft`，但那名字已被 M1 画像泳道占用，
重名会撞仓库级铁律 1 守卫 ⇒ 加前缀，见模块 docstring 的偏离登记 D-U3-8）；
Tasks 5–7 在同一文件里续写编辑／「标记为人工撰写」／回填／外发四个节点的用例。

集中覆盖三条最容易静默失效的约束：
- 版本递增不覆盖：同一场次重复生成 MUST 产生新版本、旧版永久保留（spec
  「同一场次重复生成 MUST 产生新版本而不覆盖旧版本」）；
- 前置校验 fail-fast：已取消／不存在的场次在 compute 侧就被拒，⛔ 不为一个必定被拒
  的请求白烧一次 LLM 调用；
- 幂等：同一次真实 LLM 调用重放只落一版草稿、`effect_log` 只一行（工程铁律 1）。

LLM 网关用与 tests/test_invitation_drafter.py 同款的 scripted client 打桩
（⛔ 不联网、⛔ 不真调模型）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from app.agents.invitation_drafter import InvitationDraft
from app.agents.jd_agent import AI_LABEL_PREFIX, enforce_ai_label
from app.graph.invitation_nodes import (
    compute_invitation_draft_for_slot,
    effect_persist_invitation_draft,
)
from app.llm.gateway import LLMGateway
from app.storage.db import get_connection, init_schema
from app.storage.interview_invitation import (
    InvitationNotAllowedError,
    InvitationSlotNotFoundError,
    InvitationTemplateMissingError,
)
from app.storage.invitation_template import get_invitation_template

GENERATED_AT = "2026-10-10T00:00:00+00:00"


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


class _ScriptedClient:
    def __init__(self, responses):
        self.chat = type("_Chat", (), {})()
        self.chat.completions = _ScriptedCompletions(responses)


def _gateway(scripted) -> LLMGateway:
    return LLMGateway(
        api_key="k", base_url="https://example.invalid", model="deepseek-chat",
        supports_json_schema=True, client=scripted,
    )


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "invitation_effect.db"))
    init_schema(c)
    _seed(c)
    return c


def _seed(conn) -> None:
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式软件工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT INTO resume "
        "(id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'sha-1', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1', 'c1', 'j1', 'r1', 'interview')"
    )
    conn.execute(
        "INSERT INTO interview_slot (id, application_id, round, start_at, end_at, mode) "
        "VALUES ('s1', 'app1', 1, '2026-10-12 14:00', '2026-10-12 15:00', 'onsite')"
    )
    conn.commit()


def _draft(*, run_id: str = "run-u3-1", body: str | None = None) -> InvitationDraft:
    return InvitationDraft(
        body=body or enforce_ai_label("您好，邀请您参加面试。", generated_at=GENERATED_AT),
        run_id=run_id,
        response_model="deepseek-chat-actual-v1",
        prompt_version="invite-v1",
    )


def _persist(conn, *, draft: InvitationDraft | None = None, slot_id: str = "s1") -> str | None:
    d = draft or _draft()
    return effect_persist_invitation_draft(
        conn, thread_id="app1", business_key=d.run_id, slot_id=slot_id,
        template_version="v1", draft=d,
    )


def _draft_count(conn, slot_id: str = "s1") -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM interview_invitation_draft WHERE slot_id = ?", (slot_id,)
    ).fetchone()[0]


def _effect_count(conn, node_name: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = ?", (node_name,)
    ).fetchone()[0]


_PERSIST_NODE = "effect_persist_invitation_draft"


def _slot(conn, slot_id: str = "s1") -> tuple:
    return conn.execute(
        "SELECT invitation_status, sent_channel, updated_by FROM interview_slot WHERE id = ?",
        (slot_id,),
    ).fetchone()


def _user_prompt(scripted: _ScriptedClient) -> str:
    return scripted.chat.completions.calls[0]["messages"][-1]["content"]


# ── compute_invitation_draft_for_slot ────────────────────────────────────


def test_compute_uses_latest_template_and_returns_bound_version(conn):
    scripted = _ScriptedClient([json.dumps({"body": "您好"})])
    template = get_invitation_template(conn)
    draft, template_version = compute_invitation_draft_for_slot(
        conn, slot_id="s1", gateway=_gateway(scripted),
    )
    assert template_version == "v1" == template["version"]
    assert template["body"] in _user_prompt(scripted)
    assert draft.prompt_version == "invite-v1"
    assert draft.response_model == "deepseek-chat-actual-v1"


def test_compute_returns_ai_labelled_body(conn):
    scripted = _ScriptedClient([json.dumps({"body": "您好"})])
    draft, _version = compute_invitation_draft_for_slot(
        conn, slot_id="s1", gateway=_gateway(scripted),
    )
    assert AI_LABEL_PREFIX in draft.body


def test_compute_reads_slot_facts_into_prompt(conn):
    scripted = _ScriptedClient([json.dumps({"body": "您好"})])
    compute_invitation_draft_for_slot(conn, slot_id="s1", gateway=_gateway(scripted))
    prompt = _user_prompt(scripted)
    assert "张三" in prompt
    assert "嵌入式软件工程师" in prompt


def test_compute_raises_for_missing_slot_without_calling_llm(conn):
    scripted = _ScriptedClient([json.dumps({"body": "您好"})])
    with pytest.raises(InvitationSlotNotFoundError):
        compute_invitation_draft_for_slot(
            conn, slot_id="nope", gateway=_gateway(scripted),
        )
    assert scripted.chat.completions.calls == []


def test_compute_rejects_cancelled_slot_without_calling_llm(conn):
    conn.execute("UPDATE interview_slot SET status = 'cancelled' WHERE id = 's1'")
    conn.commit()
    scripted = _ScriptedClient([json.dumps({"body": "您好"})])
    with pytest.raises(InvitationNotAllowedError):
        compute_invitation_draft_for_slot(
            conn, slot_id="s1", gateway=_gateway(scripted),
        )
    assert scripted.chat.completions.calls == []


def test_compute_raises_when_no_template_without_calling_llm(conn):
    conn.execute("DELETE FROM invitation_template")
    conn.commit()
    scripted = _ScriptedClient([json.dumps({"body": "您好"})])
    with pytest.raises(InvitationTemplateMissingError):
        compute_invitation_draft_for_slot(
            conn, slot_id="s1", gateway=_gateway(scripted),
        )
    assert scripted.chat.completions.calls == []


def test_consecutive_computes_bind_distinct_run_ids(conn):
    scripted = _ScriptedClient([json.dumps({"body": "您好"}), json.dumps({"body": "您好"})])
    gateway = _gateway(scripted)
    first, _ = compute_invitation_draft_for_slot(conn, slot_id="s1", gateway=gateway)
    second, _ = compute_invitation_draft_for_slot(conn, slot_id="s1", gateway=gateway)
    assert first.run_id != second.run_id


# ── effect_persist_draft ─────────────────────────────────────────────────


def test_persist_draft_inserts_version_one_and_sets_drafted(conn):
    draft_id = _persist(conn)
    row = conn.execute(
        "SELECT version, template_version, ai_generated, analysis_run_id "
        "FROM interview_invitation_draft WHERE id = ?",
        (draft_id,),
    ).fetchone()
    assert row == (1, "v1", 1, "run-u3-1")
    assert _slot(conn)[0] == "drafted"
    assert _effect_count(conn, _PERSIST_NODE) == 1


def test_persist_draft_replay_returns_none_and_writes_no_second_row(conn):
    _persist(conn)
    assert _persist(conn) is None
    assert _draft_count(conn) == 1
    assert _effect_count(conn, _PERSIST_NODE) == 1


def test_persist_draft_versions_increment_on_new_run_id(conn):
    _persist(conn, draft=_draft(run_id="run-1"))
    _persist(conn, draft=_draft(run_id="run-2"))
    versions = [
        r[0]
        for r in conn.execute(
            "SELECT version FROM interview_invitation_draft WHERE slot_id = 's1' "
            "ORDER BY version"
        )
    ]
    assert versions == [1, 2]


def test_persist_draft_rejects_cancelled_slot(conn):
    conn.execute("UPDATE interview_slot SET status = 'cancelled' WHERE id = 's1'")
    conn.commit()
    with pytest.raises(InvitationNotAllowedError):
        _persist(conn)
    assert _draft_count(conn) == 0
    assert _effect_count(conn, _PERSIST_NODE) == 0


def test_persist_draft_does_not_downgrade_confirmed_status(conn):
    conn.execute(
        "UPDATE interview_slot SET invitation_status = 'confirmed' WHERE id = 's1'"
    )
    conn.commit()
    _persist(conn, draft=_draft(run_id="run-9"))
    assert _slot(conn)[0] == "confirmed"


def test_persist_draft_records_effect_log_alongside_business_row(conn):
    """工程铁律 1 的恒等式：每个持久化 effect 的 effect_log 条数与业务表行数按
    thread 恒等。"""
    _persist(conn, draft=_draft(run_id="run-1"))
    _persist(conn, draft=_draft(run_id="run-2"))
    assert _effect_count(conn, _PERSIST_NODE) == _draft_count(conn) == 2


def test_compute_then_persist_twice_yields_two_versions(conn):
    scripted = _ScriptedClient([json.dumps({"body": "您好"}), json.dumps({"body": "您好"})])
    gateway = _gateway(scripted)
    for _ in range(2):
        draft, template_version = compute_invitation_draft_for_slot(
            conn, slot_id="s1", gateway=gateway,
        )
        effect_persist_invitation_draft(
            conn, thread_id="app1", business_key=draft.run_id, slot_id="s1",
            template_version=template_version, draft=draft,
        )
    bodies = [
        r[0]
        for r in conn.execute(
            "SELECT body FROM interview_invitation_draft WHERE slot_id = 's1' "
            "ORDER BY version"
        )
    ]
    assert len(bodies) == 2
    assert all("AI 生成" in b for b in bodies)
