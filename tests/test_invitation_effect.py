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
from app.agents.jd_agent import (
    AI_LABEL_PREFIX,
    enforce_ai_label,
    extract_label_generated_at,
)
from app.graph.invitation_nodes import (
    compute_invitation_draft_for_slot,
    draft_edit_business_key,
    effect_edit_draft,
    effect_mark_draft_human_written,
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


# ── effect_edit_draft / effect_mark_draft_human_written ───────────────────


_EDIT_NODE = "effect_edit_draft"
_MARK_NODE = "effect_mark_draft_human_written"


def _edit(conn, draft_id: str, text: str, *, thread_id: str = "app1"):
    return effect_edit_draft(
        conn, thread_id=thread_id,
        business_key=draft_edit_business_key(draft_id, text),
        draft_id=draft_id, edited_body=text,
    )


def _mark(conn, draft_id: str, *, reviewer: str = "hr-1", thread_id: str = "app1"):
    return effect_mark_draft_human_written(
        conn, thread_id=thread_id, business_key=draft_id, draft_id=draft_id,
        reviewer=reviewer, marked_at="2026-10-10 09:00:00",
    )


def _draft_row(conn, draft_id: str) -> tuple:
    return conn.execute(
        "SELECT version, ai_generated, authorship_marked_by, authorship_marked_at, body "
        "FROM interview_invitation_draft WHERE id = ?",
        (draft_id,),
    ).fetchone()


def test_edit_keeps_ai_label_and_original_generated_at(conn):
    """编辑不去标，且重贴的是**回读出来的原生成时间**（⛔ 不是"现在"）。"""
    draft_id = _persist(conn)
    body = _edit(conn, draft_id, "改了两句话")
    assert AI_LABEL_PREFIX in body
    assert extract_label_generated_at(body) == GENERATED_AT
    stored = conn.execute(
        "SELECT body FROM interview_invitation_draft WHERE id = ?", (draft_id,)
    ).fetchone()[0]
    assert stored == body


def test_edit_reattaches_label_even_if_hr_stripped_it(conn):
    """HR 提交的正文里没有标识也照样只有一行标识——⛔ 服务端不信任客户端提交的标识。"""
    draft_id = _persist(conn)
    body = _edit(conn, draft_id, "无标识正文")
    assert body.count(AI_LABEL_PREFIX) == 1


def test_edit_business_key_is_stable_per_text(conn):
    """同一份草稿 + 同一段正文 = 同一次编辑：重放短路，effect_log 只一行（铁律 1）。"""
    draft_id = _persist(conn)
    key = draft_edit_business_key(draft_id, "同一段正文")
    effect_edit_draft(
        conn, thread_id="app1", business_key=key, draft_id=draft_id,
        edited_body="同一段正文",
    )
    assert (
        effect_edit_draft(
            conn, thread_id="app1", business_key=key, draft_id=draft_id,
            edited_body="同一段正文",
        )
        is None
    )
    assert _effect_count(conn, _EDIT_NODE) == 1


def test_edit_does_not_create_a_new_version(conn):
    """编辑是就地改这一版正文，⛔ 不产生新版本、不覆盖版本递增语义。"""
    draft_id = _persist(conn)
    _edit(conn, draft_id, "改一句")
    assert _draft_count(conn) == 1
    assert _draft_row(conn, draft_id)[0] == 1


def test_edit_different_text_is_a_second_edit(conn):
    """改了正文算新编辑（business_key 里带正文哈希），两行 effect_log。"""
    draft_id = _persist(conn)
    _edit(conn, draft_id, "第一版")
    _edit(conn, draft_id, "第二版")
    assert _effect_count(conn, _EDIT_NODE) == 2
    stored = _draft_row(conn, draft_id)[4]
    assert AI_LABEL_PREFIX in stored
    assert "第二版" in stored
    assert "第一版" not in stored


def test_mark_human_written_strips_label_and_records_who_and_when(conn):
    """唯一去标路径：去标 + 留痕（谁、何时）在同一次 UPDATE 落地，原 AI 版本＝本行 version。"""
    draft_id = _persist(conn)
    body = _mark(conn, draft_id)
    assert AI_LABEL_PREFIX not in body
    assert _draft_row(conn, draft_id)[:4] == (1, 0, "hr-1", "2026-10-10 09:00:00")
    assert _effect_count(conn, _MARK_NODE) == 1


def test_mark_requires_reviewer(conn):
    """决策人只能是人：空白 reviewer 直接拒（铁律外的合规红线），且不落任何写。"""
    draft_id = _persist(conn)
    with pytest.raises(ValueError):
        _mark(conn, draft_id, reviewer="   ")
    assert _draft_row(conn, draft_id)[:2] == (1, 1)
    assert _effect_count(conn, _MARK_NODE) == 0


def test_mark_is_terminal_and_keeps_first_reviewer(conn):
    """一份草稿的「标记为人工撰写」是终态：business_key = draft_id，重放短路，
    留痕里保留第一个按下按钮的人。"""
    draft_id = _persist(conn)
    _mark(conn, draft_id, reviewer="hr-1")
    assert _mark(conn, draft_id, reviewer="hr-2") is None
    assert _draft_row(conn, draft_id)[:4] == (1, 0, "hr-1", "2026-10-10 09:00:00")
    assert _effect_count(conn, _MARK_NODE) == 1


def test_edit_after_mark_does_not_reattach_label(conn):
    """已标记人工撰写的草稿，其作者已经是人：后续编辑只剥不贴。"""
    draft_id = _persist(conn)
    _mark(conn, draft_id)
    body = _edit(conn, draft_id, "人手写的内容")
    assert body == "人手写的内容"
    assert AI_LABEL_PREFIX not in body


def test_effect_log_matches_business_rows_for_edit_and_mark(conn):
    """铁律 1 恒等式：edit / mark 的 effect_log 条数与各自落地的业务写恒等
    （edit 只改行、不经手新行 ⇒ 用「本行是否已带标识／已置人工」回指）。"""
    draft_id = _persist(conn)
    _edit(conn, draft_id, "改一句")
    _mark(conn, draft_id)
    assert _effect_count(conn, _EDIT_NODE) == 1
    assert _effect_count(conn, _MARK_NODE) == 1
    assert _draft_row(conn, draft_id)[:4] == (1, 0, "hr-1", "2026-10-10 09:00:00")
