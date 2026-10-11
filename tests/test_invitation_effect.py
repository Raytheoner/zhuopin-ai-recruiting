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
from pathlib import Path

import pytest

from app.agents.invitation_drafter import InvitationDraft
from app.agents.jd_agent import (
    AI_LABEL_PREFIX,
    enforce_ai_label,
    extract_label_generated_at,
)
from app.audit.recorder import AuditRecorder
from app.audit.sinks import JsonlChainSink, SqliteSink
from app.graph.invitation_nodes import (
    compute_invitation_draft_for_slot,
    draft_body_digest,
    draft_edit_business_key,
    effect_backfill_invitation_outcome,
    effect_edit_draft,
    effect_mark_draft_human_written,
    effect_mark_invitation_sent,
    effect_persist_invitation_draft,
    send_invitation,
    validate_outcome,
)
from app.llm.gateway import LLMGateway
from app.storage.db import get_connection, init_schema
from app.storage.interview_invitation import (
    InvitationNotAllowedError,
    InvitationOutcomeAlreadyRecordedError,
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


def _invitation_fact_snapshot(conn):
    tables = (
        "interview_slot", "interview_invitation_draft", "invitation_outcome_log",
        "invitation_send_log", "effect_log",
    )
    return tuple(
        (table, tuple(conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()))
        for table in tables
    )


def _add_second_slot(conn):
    conn.execute(
        "INSERT INTO interview_slot (id, application_id, round, start_at, end_at, mode) "
        "VALUES ('s2', 'app1', 2, '2026-10-20 06:00', '2026-10-20 07:00', 'onsite')"
    )
    conn.commit()


def test_persist_cache_hit_rejects_rebound_slot_and_preserves_facts(conn):
    _persist(conn)
    _add_second_slot(conn)
    before = _invitation_fact_snapshot(conn)
    with pytest.raises(InvitationNotAllowedError):
        _persist(conn, slot_id="s2")
    assert _invitation_fact_snapshot(conn) == before


def test_persist_fresh_key_rejects_mismatched_run_id_without_fact_changes(conn):
    draft = _draft(run_id="run-B")
    before = _invitation_fact_snapshot(conn)

    with pytest.raises(ValueError):
        effect_persist_invitation_draft(
            conn, thread_id="app1", business_key="run-A", slot_id="s1",
            template_version="v1", draft=draft,
        )

    assert _effect_count(conn, _PERSIST_NODE) == 0
    assert _invitation_fact_snapshot(conn) == before


def test_successful_invitation_effect_keys_replay_after_slot_is_cancelled(conn):
    """缓存重放代表历史事实；场次后来取消不能改变原成功键的结果。"""
    draft_id = _persist(conn)
    edited_body = "先编辑，再标记与发送"
    edit_key = draft_edit_business_key(draft_id, edited_body)
    effect_edit_draft(
        conn, thread_id="app1", business_key=edit_key, draft_id=draft_id,
        edited_body=edited_body,
    )
    effect_mark_draft_human_written(
        conn, thread_id="app1", business_key=draft_id, draft_id=draft_id,
        reviewer="hr-1", marked_at="2026-10-11 09:00:00",
    )
    body = conn.execute(
        "SELECT body FROM interview_invitation_draft WHERE id = ?", (draft_id,)
    ).fetchone()[0]
    digest = draft_body_digest(body)
    effect_mark_invitation_sent(
        conn, thread_id="app1", business_key=f"s1:{draft_id}:{digest}",
        slot_id="s1", draft_id=draft_id, body_digest=digest, actor="hr-1",
    )
    effect_backfill_invitation_outcome(
        conn, thread_id="app1", business_key="s1:confirmed", slot_id="s1",
        status="confirmed", actor="hr-1",
    )
    conn.execute("UPDATE interview_slot SET status = 'cancelled' WHERE id = 's1'")
    conn.commit()
    before = _invitation_fact_snapshot(conn)

    assert _persist(conn) is None
    assert effect_edit_draft(
        conn, thread_id="app1", business_key=edit_key, draft_id=draft_id,
        edited_body=edited_body,
    ) is None
    assert effect_mark_draft_human_written(
        conn, thread_id="app1", business_key=draft_id, draft_id=draft_id,
        reviewer="hr-2", marked_at="2026-10-11 10:00:00",
    ) is None
    assert effect_backfill_invitation_outcome(
        conn, thread_id="app1", business_key="s1:confirmed", slot_id="s1",
        status="confirmed", actor="hr-2",
    ) is None
    assert effect_mark_invitation_sent(
        conn, thread_id="app1", business_key=f"s1:{draft_id}:{digest}",
        slot_id="s1", draft_id=draft_id, body_digest=digest, actor="hr-2",
    ) is None
    assert _invitation_fact_snapshot(conn) == before


def test_persist_existing_run_id_cannot_bind_to_another_slot_in_same_application(conn):
    _add_second_slot(conn)
    conn.execute(
        "INSERT INTO interview_invitation_draft "
        "(id, slot_id, version, template_version, body, ai_generated, analysis_run_id) "
        "VALUES ('legacy-draft', 's1', 1, 'v1', ?, 1, 'run-u3-1')",
        (_draft().body,),
    )
    conn.commit()
    before = _invitation_fact_snapshot(conn)
    with pytest.raises(InvitationNotAllowedError):
        _persist(conn, slot_id="s2")
    assert _invitation_fact_snapshot(conn) == before


def test_persist_rejects_wrong_thread_on_cached_key_without_fact_changes(conn):
    _persist(conn)
    before = _invitation_fact_snapshot(conn)
    with pytest.raises(InvitationNotAllowedError):
        effect_persist_invitation_draft(
            conn, thread_id="wrong-app", business_key="run-u3-1", slot_id="s1",
            template_version="v1", draft=_draft(),
        )
    assert _invitation_fact_snapshot(conn) == before


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


def test_edit_cache_hit_rejects_changed_text_with_original_business_key(conn):
    draft_id = _persist(conn)
    original_text = "原编辑正文"
    key = draft_edit_business_key(draft_id, original_text)
    effect_edit_draft(
        conn, thread_id="app1", business_key=key, draft_id=draft_id,
        edited_body=original_text,
    )
    before = _invitation_fact_snapshot(conn)
    with pytest.raises(ValueError):
        effect_edit_draft(
            conn, thread_id="app1", business_key=key, draft_id=draft_id,
            edited_body="改过的编辑正文",
        )
    assert _invitation_fact_snapshot(conn) == before


def test_edit_fresh_key_rejects_mismatched_body_without_fact_changes(conn):
    draft_id = _persist(conn)
    original_text = "键对应的正文 A"
    before = _invitation_fact_snapshot(conn)

    with pytest.raises(ValueError):
        effect_edit_draft(
            conn, thread_id="app1",
            business_key=draft_edit_business_key(draft_id, original_text),
            draft_id=draft_id, edited_body="实际传入的正文 B",
        )

    assert _effect_count(conn, _EDIT_NODE) == 0
    assert _invitation_fact_snapshot(conn) == before


def test_edit_rejects_wrong_thread_for_fresh_key_without_fact_changes(conn):
    draft_id = _persist(conn)
    before = _invitation_fact_snapshot(conn)
    with pytest.raises(InvitationNotAllowedError):
        effect_edit_draft(
            conn, thread_id="wrong-app", business_key=draft_edit_business_key(draft_id, "x"),
            draft_id=draft_id, edited_body="x",
        )
    assert _invitation_fact_snapshot(conn) == before


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


def test_mark_cache_hit_rejects_rebound_draft_without_fact_changes(conn):
    first = _persist(conn)
    second = _persist(conn, draft=_draft(run_id="run-u3-2"))
    _mark(conn, first)
    before = _invitation_fact_snapshot(conn)
    with pytest.raises(ValueError):
        effect_mark_draft_human_written(
            conn, thread_id="app1", business_key=first, draft_id=second,
            reviewer="hr-2", marked_at="2026-10-11 09:00:00",
        )
    assert _invitation_fact_snapshot(conn) == before


def test_mark_fresh_key_rejects_mismatched_draft_id_without_fact_changes(conn):
    expected_draft_id = _persist(conn)
    actual_draft_id = _persist(conn, draft=_draft(run_id="run-u3-2"))
    assert _effect_count(conn, _MARK_NODE) == 0
    before = _invitation_fact_snapshot(conn)

    with pytest.raises(ValueError):
        effect_mark_draft_human_written(
            conn, thread_id="app1", business_key=expected_draft_id,
            draft_id=actual_draft_id, reviewer="hr-1", marked_at="2026-10-11 09:00:00",
        )

    assert _invitation_fact_snapshot(conn) == before
    assert _effect_count(conn, _MARK_NODE) == 0


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


# ── validate_outcome / effect_backfill_invitation_outcome ─────────────────


_BACKFILL_NODE = "effect_backfill_invitation_outcome"


def _backfill(conn, *, status: str, actor: str = "hr-1",
              channel: str | None = None, reason: str | None = None,
              business_key: str | None = None, thread_id: str = "app1"):
    return effect_backfill_invitation_outcome(
        # 2026-10-11 修正（Spec review）：键口径统一为 {slot_id}:{status}
        # （只带 status 会把第二轮场次的同状态回填静默短路）。
        conn, thread_id=thread_id, business_key=business_key or f"s1:{status}",
        slot_id="s1", status=status, actor=actor, channel=channel, reason=reason,
    )


def _outcome_count(conn, slot_id: str = "s1") -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM invitation_outcome_log WHERE slot_id = ?", (slot_id,)
    ).fetchone()[0]


def test_backfill_sent_sets_status_channel_actor_time(conn):
    outcome = _backfill(conn, status="sent", channel="wechat")
    assert outcome["status"] == "sent"
    assert outcome["channel"] == "wechat"
    assert outcome["actor"] == "hr-1"
    assert outcome["at"]
    assert _slot(conn) == ("sent", "wechat", "hr-1")
    assert _effect_count(conn, _BACKFILL_NODE) == 1


def test_backfill_declined_keeps_stage_and_writes_no_rejection_record(conn):
    """design D9：候选人拒绝邀约**不是**淘汰——⛔ 不写 rejection_record、
    ⛔ 不动 current_stage_id、⛔ 不写 application_stage_history。"""
    before_stage = conn.execute(
        "SELECT current_stage_id FROM application WHERE id = 'app1'"
    ).fetchone()[0]
    _backfill(conn, status="declined", reason="已接受其他 offer")
    after_stage = conn.execute(
        "SELECT current_stage_id FROM application WHERE id = 'app1'"
    ).fetchone()[0]
    rejections = conn.execute("SELECT COUNT(*) FROM rejection_record").fetchone()[0]
    assert before_stage == after_stage == "interview"
    assert rejections == 0
    assert _slot(conn)[0] == "declined"
    history = conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE application_id = 'app1'"
    ).fetchone()[0]
    assert history == 0  # ⛔ 回填不写流转事实（U2 的条数守恒不变式不能被破坏）


def test_backfill_same_status_twice_writes_one_row_and_one_effect_log(conn):
    """同状态重复提交被 effect_log 短路：`invitation_outcome_log` 只一行、
    幂等键也只一行（键与 UNIQUE(slot_id, status) 两层保险）。"""
    first = _backfill(conn, status="sent", channel="wechat")
    second = _backfill(conn, status="sent", channel="wechat")
    assert first["id"]
    assert second is None
    assert _outcome_count(conn) == 1
    assert _effect_count(conn, _BACKFILL_NODE) == 1


def test_backfill_cache_hit_rejects_changed_status_with_original_business_key(conn):
    _backfill(conn, status="sent", channel="wechat")
    before = _invitation_fact_snapshot(conn)
    with pytest.raises(ValueError):
        effect_backfill_invitation_outcome(
            conn, thread_id="app1", business_key="s1:sent", slot_id="s1",
            status="confirmed", actor="hr-2",
        )
    assert _invitation_fact_snapshot(conn) == before


def test_backfill_fresh_key_rejects_mismatched_status_without_fact_changes(conn):
    before = _invitation_fact_snapshot(conn)

    with pytest.raises(ValueError):
        effect_backfill_invitation_outcome(
            conn, thread_id="app1", business_key="s1:sent", slot_id="s1",
            status="confirmed", actor="hr-1",
        )

    assert _effect_count(conn, _BACKFILL_NODE) == 0
    assert _invitation_fact_snapshot(conn) == before


def test_backfill_rejects_wrong_thread_for_fresh_key_without_fact_changes(conn):
    before = _invitation_fact_snapshot(conn)
    with pytest.raises(InvitationNotAllowedError):
        effect_backfill_invitation_outcome(
            conn, thread_id="wrong-app", business_key="s1:confirmed", slot_id="s1",
            status="confirmed", actor="hr-1",
        )
    assert _invitation_fact_snapshot(conn) == before


def test_backfill_duplicate_with_fresh_canonical_key_raises_without_orphan_effect_log(conn):
    """已有业务事实但该状态的节点键尚未用过时，规范业务键仍抛领域异常，
    且**不写孤儿 effect_log**（恒等式守恒）。"""
    _backfill(conn, status="confirmed")
    conn.execute(
        "INSERT INTO invitation_outcome_log (id, slot_id, status, channel, actor) "
        "VALUES ('prior-outcome', 's1', 'sent', 'wechat', 'hr-1')"
    )
    conn.commit()
    with pytest.raises(InvitationOutcomeAlreadyRecordedError) as excinfo:
        _backfill(conn, status="sent", channel="wechat")
    assert excinfo.value.outcome["id"]
    assert _outcome_count(conn) == 2
    assert _effect_count(conn, _BACKFILL_NODE) == 1


def test_backfill_same_status_across_slots_is_not_short_circuited(conn):
    """2026-10-11 修正（Spec review）：幂等键必须带 slot_id——同一投递**第二轮
    场次**的同状态回填不能被 {status} 相同的 effect_log 键静默短路（无痕、场次
    状态不更新、接口却返回成功形状）。"""
    conn.execute(
        "INSERT INTO interview_slot (id, application_id, round, start_at, end_at, mode) "
        "VALUES ('s2', 'app1', 2, '2026-10-20 06:00', '2026-10-20 07:00', 'onsite')"
    )
    conn.commit()
    first = effect_backfill_invitation_outcome(
        conn, thread_id="app1", business_key="s1:confirmed", slot_id="s1",
        status="confirmed", actor="hr-1",
    )
    second = effect_backfill_invitation_outcome(
        conn, thread_id="app1", business_key="s2:confirmed", slot_id="s2",
        status="confirmed", actor="hr-1",
    )
    assert first is not None and second is not None
    assert _outcome_count(conn, "s1") == 1
    assert _outcome_count(conn, "s2") == 1
    assert _slot(conn, "s2")[0] == "confirmed"
    assert _effect_count(conn, _BACKFILL_NODE) == 2


def test_backfill_confirmed_after_sent_keeps_channel(conn):
    """同场次**不同状态**是两次回填：各留一条痕，且非 sent 行不带渠道、
    场次上的 sent_channel 保留先前那次的外发渠道。"""
    _backfill(conn, status="sent", channel="email")
    _backfill(conn, status="confirmed")
    assert _slot(conn)[:2] == ("confirmed", "email")
    assert _outcome_count(conn) == 2
    assert _effect_count(conn, _BACKFILL_NODE) == 2


def test_backfill_sent_requires_channel(conn):
    with pytest.raises(ValueError):
        _backfill(conn, status="sent")
    assert _outcome_count(conn) == 0
    assert _effect_count(conn, _BACKFILL_NODE) == 0


def test_backfill_declined_requires_reason(conn):
    with pytest.raises(ValueError):
        _backfill(conn, status="declined")
    assert _outcome_count(conn) == 0


def test_backfill_rejects_unknown_status(conn):
    with pytest.raises(ValueError):
        _backfill(conn, status="rejected")
    assert _outcome_count(conn) == 0


def test_backfill_requires_actor(conn):
    """回填 MUST 记下操作人（spec「回填 MUST 记录操作人与时刻」）。"""
    with pytest.raises(ValueError):
        _backfill(conn, status="sent", channel="wechat", actor="   ")
    assert _outcome_count(conn) == 0
    assert _effect_count(conn, _BACKFILL_NODE) == 0


def test_backfill_rejects_cancelled_slot(conn):
    conn.execute("UPDATE interview_slot SET status = 'cancelled' WHERE id = 's1'")
    conn.commit()
    with pytest.raises(InvitationNotAllowedError):
        _backfill(conn, status="sent", channel="wechat")
    assert _outcome_count(conn) == 0


def test_validate_outcome_rejects_channel_on_non_sent_status(conn):
    """只有「已发出」可以带渠道——'confirmed' 带 channel 即拒（接口层与节点层
    共用这一份判据，⛔ 不各写一套）。"""
    with pytest.raises(ValueError):
        validate_outcome(status="confirmed", channel="wechat", reason=None)
    with pytest.raises(ValueError):
        validate_outcome(status="declined", channel="wechat", reason="已接受其他 offer")


def test_validate_outcome_does_not_normalize(conn):
    """⛔ 不做归一化：未知取值即拒绝，不 strip 渠道、不大写化状态。
    归一化会让「微信」与「wechat」变成两种渠道、报表当场分叉。"""
    for status, channel in (
        ("SENT", "wechat"),      # 状态大小写不归一
        ("sent", "WECHAT"),      # 渠道大小写不归一
        ("sent", "微信"),         # 中文渠道不折算成 wechat
        ("sent", " wechat "),    # 渠道两侧空白不 strip
    ):
        with pytest.raises(ValueError):
            validate_outcome(status=status, channel=channel, reason=None)
    with pytest.raises(ValueError):
        validate_outcome(status="declined", channel=None, reason="   ")


def test_backfill_confirmed_needs_no_reason(conn):
    """只有 declined 必填原因；confirmed 无原因、无渠道照样放行。"""
    outcome = _backfill(conn, status="confirmed")
    assert outcome["status"] == "confirmed"
    assert outcome["channel"] is None
    assert _slot(conn)[:2] == ("confirmed", None)


# ── send_invitation（门禁接线，Task 7 落地；Task 10 的门禁 Scenario）─────


_SEND_NODE = "effect_mark_invitation_sent"


class FakeChannel:
    """记录投递次数的假通道。⛔ 不联网、不发真消息。"""

    def __init__(self):
        self.delivered = []

    def deliver(self, thread_id, message):
        self.delivered.append((thread_id, message))

    def latest(self, thread_id):
        return None


@pytest.fixture
def wired(conn, tmp_path):
    chain_path = tmp_path / "decisions.jsonl"
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))
    return FakeChannel(), recorder, chain_path


def _send(conn, wired, *, recipient="13800000000", enabled=False, confirmed_by="hr-1"):
    channel, recorder, _chain_path = wired
    draft = conn.execute(
        "SELECT id, body FROM interview_invitation_draft WHERE slot_id = 's1' "
        "ORDER BY version DESC LIMIT 1"
    ).fetchone()
    draft_id, body = draft
    return send_invitation(
        conn, thread_id="app1",
        slot_id="s1", draft_id=draft_id, recipient=recipient, channel=channel,
        recorder=recorder, outbound_enabled=lambda: enabled, confirmed_by=confirmed_by,
    )


def _mirror_lines(chain_path: Path) -> list[dict]:
    text = chain_path.read_text(encoding="utf-8").strip()
    return [json.loads(line) for line in text.splitlines()] if text else []


def test_send_blocked_when_switch_off_and_audit_recorded(conn, wired):
    """spec 第一个 Scenario：外发总开关关闭 ⇒ 拦下并留痕，⛔ 不动邀约状态。"""
    _persist(conn)
    decision = _send(conn, wired, enabled=False)
    channel, _recorder, chain_path = wired
    assert decision.allowed is False
    assert decision.reason == "外发总开关关闭"
    assert channel.delivered == []
    lines = _mirror_lines(chain_path)
    assert [line["event_type"] for line in lines] == ["outbound_blocked"]
    assert lines[0]["blocked_reason"] == "外发总开关关闭"
    assert _slot(conn)[0] == "drafted"  # ⛔ 拦截不动邀约状态
    assert _effect_count(conn, _SEND_NODE) == 0


def test_send_blocked_when_ai_label_missing(conn, wired):
    """spec 第二个 Scenario：缺 AI 生成标识 ⇒ 门禁第 ④ 条拦截。"""
    conn.execute(
        "INSERT INTO interview_invitation_draft "
        "(id, slot_id, version, template_version, body, ai_generated) "
        "VALUES ('d-nolabel', 's1', 1, 'v1', '您好，邀请您参加面试。', 1)"
    )
    conn.commit()
    decision = _send(conn, wired, enabled=True)
    channel, _recorder, chain_path = wired
    assert decision.allowed is False
    assert decision.reason == "缺少 AI 生成标识"
    assert channel.delivered == []
    assert _mirror_lines(chain_path)[0]["blocked_reason"] == "缺少 AI 生成标识"


def test_send_blocked_when_recipient_unknown(conn, wired):
    """U4 vault 关着时就是这条路：`candidate_recipient_for_invitation()` 返回 None，
    调用方喂空串 ⇒ 门禁第 ⑤ 条拦截（⛔ 节点不替门禁做判断）。"""
    _persist(conn)
    decision = _send(conn, wired, recipient=None, enabled=True)
    assert decision.allowed is False
    assert decision.reason == "收件对象缺失或为空"


def test_send_allowed_delivers_and_marks_sent(conn, wired):
    _persist(conn)
    decision = _send(conn, wired, enabled=True)
    channel, _recorder, chain_path = wired
    assert decision.allowed is True
    assert len(channel.delivered) == 1
    assert channel.delivered[0][1].type == "interview_invitation"
    assert _slot(conn) == ("sent", "system", "hr-1")
    assert _mirror_lines(chain_path)[0]["event_type"] == "outbound_delivered"
    assert _effect_count(conn, _SEND_NODE) == 1


def test_send_replay_same_content_is_noop(conn, wired):
    """重放仍返回实时门禁判定，内容键保证不重复投递。"""
    _persist(conn)
    _send(conn, wired, enabled=True)
    assert _send(conn, wired, enabled=True).allowed is True
    channel, _recorder, _chain_path = wired
    assert len(channel.delivered) == 1
    assert _effect_count(conn, _SEND_NODE) == 1


def test_send_does_not_downgrade_confirmed_status(conn, wired):
    """⛔ 不把已回填的 confirmed 打回去（只在 none/drafted/sent 时推进）。"""
    _persist(conn)
    effect_backfill_invitation_outcome(
        conn, thread_id="app1", business_key="s1:confirmed", slot_id="s1",
        status="confirmed", actor="hr-1",
    )
    _send(conn, wired, enabled=True)
    assert _slot(conn)[0] == "confirmed"


def test_send_rejects_cancelled_slot(conn, wired):
    """已取消的场次不可外发（`_assert_slot_invitable` 在事务内重做一遍）。"""
    _persist(conn)
    conn.execute("UPDATE interview_slot SET status = 'cancelled' WHERE id = 's1'")
    conn.commit()
    with pytest.raises(InvitationNotAllowedError):
        _send(conn, wired, enabled=True)
    channel, _recorder, _chain_path = wired
    assert channel.delivered == []
    assert _effect_count(conn, _SEND_NODE) == 0


def test_send_module_has_no_direct_channel_deliver():
    """源码级反证：唯一入口是 `deliver_candidate_message()`，本文件不出现
    任何 `channel.deliver(...)` 直连（绕过门禁的第二条外发路径）。"""
    src = Path("app/graph/invitation_nodes.py").read_text(encoding="utf-8")
    assert "channel.deliver" not in src
    assert "deliver_candidate_message(" in src

@pytest.mark.parametrize("target", ["s2", "missing"])
def test_send_validates_slot_and_draft_before_gate(conn, wired, monkeypatch, target):
    import app.graph.invitation_nodes as nodes
    draft_id = _persist(conn)
    conn.execute(
        "INSERT INTO interview_slot (id, application_id, round, start_at, end_at, mode) "
        "VALUES ('s2', 'app1', 2, '2026-10-13 14:00', '2026-10-13 15:00', 'onsite')"
    )
    conn.commit()
    before = conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0]
    def forbidden(*args, **kwargs):
        pytest.fail("invalid ownership reached delivery")
    monkeypatch.setattr(nodes, "deliver_candidate_message", forbidden)
    with pytest.raises((InvitationNotAllowedError, InvitationSlotNotFoundError)):
        send_invitation(
            conn, thread_id="app1",
            slot_id=target, draft_id=draft_id, recipient="fake",
            channel=wired[0], recorder=wired[1], outbound_enabled=lambda: True,
            confirmed_by="hr-1",
        )
    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == before
    assert wired[0].delivered == []
    assert _slot(conn)[0] == "drafted"


def test_blocked_draft_can_retry_after_switch_opens(conn, wired):
    _persist(conn)
    assert not _send(conn, wired, enabled=False).allowed
    assert _send(conn, wired, enabled=True).allowed
    assert len(wired[0].delivered) == 1

@pytest.mark.parametrize("window", ["before_mark", "mark_commit"])
def test_send_recovers_after_delivery_commit(conn, wired, tmp_path, monkeypatch, window):
    import sqlite3
    import app.graph.invitation_nodes as nodes
    _persist(conn)
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    channel = wired[0]
    original = nodes.effect_mark_invitation_sent

    class MarkCommitCrash(sqlite3.Connection):
        armed = False
        def execute(self, sql, parameters=()):
            result = super().execute(sql, parameters)
            if sql.startswith("INSERT INTO effect_log") and parameters[2] == _SEND_NODE:
                self.armed = True
            return result
        def commit(self):
            if self.armed:
                raise RuntimeError("mark commit crash")
            return super().commit()

    if window == "before_mark":
        def crash(*args, **kwargs):
            # Both existing delivery effects have already durably committed.
            assert _effect_count(args[0], "effect_deliver_message") == 1
            assert _effect_count(args[0], "effect_record_outbound_audit") == 1
            raise RuntimeError("before mark")
        monkeypatch.setattr(nodes, "effect_mark_invitation_sent", crash)
        active = conn
    else:
        active = sqlite3.connect(db_path, factory=MarkCommitCrash)
    recorder = AuditRecorder(SqliteSink(active), JsonlChainSink(wired[2]))
    with pytest.raises(RuntimeError):
        _send(active, (channel, recorder, wired[2]), enabled=True)
    if window == "mark_commit":
        assert not active.in_transaction
        assert active.execute("SELECT COUNT(*) FROM invitation_send_log").fetchone()[0] == 0
        assert _effect_count(active, _SEND_NODE) == 0
        active.armed = False
        active.execute("INSERT INTO job (id, title) VALUES ('unrelated-after-mark', 'unrelated')")
        active.commit()  # failed writes must not leak through this later commit
    active.close()
    monkeypatch.setattr(nodes, "effect_mark_invitation_sent", original)
    fresh = get_connection(db_path)
    try:
        assert len(channel.delivered) == 1
        assert _slot(fresh)[0] == "drafted"
        assert _effect_count(fresh, _SEND_NODE) == 0
        assert fresh.execute("SELECT COUNT(*) FROM invitation_send_log").fetchone()[0] == 0
        assert _effect_count(fresh, "effect_deliver_message") == 1
        recorder = AuditRecorder(SqliteSink(fresh), JsonlChainSink(wired[2]))
        assert _send(fresh, (channel, recorder, wired[2]), enabled=True).allowed
        assert _send(fresh, (channel, recorder, wired[2]), enabled=True).allowed
        assert len(channel.delivered) == 1
        assert _slot(fresh) == ("sent", "system", "hr-1")
        assert _effect_count(fresh, _SEND_NODE) == 1
        assert fresh.execute("SELECT COUNT(*) FROM invitation_send_log").fetchone()[0] == 1
    finally:
        fresh.close()


@pytest.mark.parametrize("status", ["confirmed", "declined", "reschedule_requested"])
def test_mark_rejects_terminal_state_without_orphan_log(conn, status):
    from app.graph.invitation_nodes import effect_mark_invitation_sent
    draft_id = _persist(conn)
    conn.execute("UPDATE interview_slot SET invitation_status = ? WHERE id = 's1'", (status,))
    conn.commit()
    body = conn.execute("SELECT body FROM interview_invitation_draft WHERE id = ?", (draft_id,)).fetchone()[0]
    digest = draft_body_digest(body)
    with pytest.raises(InvitationNotAllowedError):
        effect_mark_invitation_sent(
            conn, thread_id="app1", business_key=f"s1:{draft_id}:{digest}",
            slot_id="s1", draft_id=draft_id, body_digest=digest, actor="hr-1",
        )
    assert _slot(conn)[0] == status
    assert _effect_count(conn, _SEND_NODE) == 0
    assert conn.execute("SELECT COUNT(*) FROM invitation_send_log").fetchone()[0] == 0


def test_send_rechecks_gate_on_allowed_replay(conn, wired):
    _persist(conn)
    assert _send(conn, wired, enabled=True).allowed
    decision = _send(conn, wired, enabled=False)
    assert not decision.allowed
    assert decision.reason == "外发总开关关闭"
    assert len(wired[0].delivered) == 1
    assert _effect_count(conn, _SEND_NODE) == 1


def test_send_fact_tracks_each_draft_version_and_replay(conn, wired):
    _persist(conn)
    assert _send(conn, wired, enabled=True).allowed
    assert _send(conn, wired, enabled=True).allowed
    assert conn.execute("SELECT COUNT(*) FROM invitation_send_log").fetchone()[0] == 1
    conn.execute(
        "INSERT INTO interview_invitation_draft "
        "(id, slot_id, version, template_version, body, ai_generated) "
        "SELECT 'draft-v2', slot_id, 2, template_version, body || ' 新版本', ai_generated "
        "FROM interview_invitation_draft WHERE slot_id = 's1' AND version = 1"
    )
    conn.commit()
    assert _send(conn, wired, enabled=True).allowed
    assert _send(conn, wired, enabled=True).allowed
    rows = conn.execute(
        "SELECT thread_id, slot_id, draft_id, body_digest, actor, at FROM invitation_send_log"
    ).fetchall()
    assert len(rows) == _effect_count(conn, _SEND_NODE) == 2
    assert len({row[2] for row in rows}) == 2
    assert all(row[0] == 'app1' and row[1] == 's1' and row[4] == 'hr-1' and row[5] for row in rows)
    assert len(wired[0].delivered) == 2


def test_sent_cache_hit_rejects_changed_digest_with_original_business_key(conn, wired):
    draft_id = _persist(conn)
    assert _send(conn, wired, enabled=True).allowed
    body = conn.execute(
        "SELECT body FROM interview_invitation_draft WHERE id = ?", (draft_id,)
    ).fetchone()[0]
    digest = draft_body_digest(body)
    before = _invitation_fact_snapshot(conn)
    with pytest.raises(ValueError):
        effect_mark_invitation_sent(
            conn, thread_id="app1", business_key=f"s1:{draft_id}:{digest}",
            slot_id="s1", draft_id=draft_id, body_digest="wrong-digest", actor="hr-1",
        )
    assert _invitation_fact_snapshot(conn) == before


@pytest.mark.parametrize("mismatch", ["draft_id", "body_digest"])
def test_sent_fresh_key_rejects_mismatched_parameters_without_fact_changes(
    conn, mismatch,
):
    expected_draft_id = _persist(conn)
    actual_draft_id = _persist(
        conn,
        draft=_draft(
            run_id="run-u3-2",
            body=enforce_ai_label("另一份邀约正文。", generated_at=GENERATED_AT),
        ),
    )
    expected_body = conn.execute(
        "SELECT body FROM interview_invitation_draft WHERE id = ?",
        (expected_draft_id,),
    ).fetchone()[0]
    expected_digest = draft_body_digest(expected_body)
    key = f"s1:{expected_draft_id}:{expected_digest}"
    assert _effect_count(conn, _SEND_NODE) == 0
    before = _invitation_fact_snapshot(conn)
    if mismatch == "draft_id":
        draft_id, body_digest = actual_draft_id, expected_digest
    else:
        draft_id, body_digest = expected_draft_id, "0" * 16

    with pytest.raises(ValueError):
        effect_mark_invitation_sent(
            conn, thread_id="app1", business_key=key, slot_id="s1",
            draft_id=draft_id, body_digest=body_digest, actor="hr-1",
        )

    assert _invitation_fact_snapshot(conn) == before
    assert _effect_count(conn, _SEND_NODE) == 0


def test_sent_cache_hit_rejects_draft_from_another_slot(conn, wired):
    draft_id = _persist(conn)
    _add_second_slot(conn)
    before = _invitation_fact_snapshot(conn)
    digest = draft_body_digest(_draft().body)
    with pytest.raises(InvitationNotAllowedError):
        effect_mark_invitation_sent(
            conn, thread_id="app1", business_key=f"s2:{draft_id}:{digest}",
            slot_id="s2", draft_id=draft_id, body_digest=digest, actor="hr-1",
        )
    assert _invitation_fact_snapshot(conn) == before


def test_sent_success_key_replays_after_current_draft_body_is_edited(conn, wired):
    draft_id = _persist(conn)
    assert _send(conn, wired, enabled=True).allowed
    original_body = conn.execute(
        "SELECT body FROM interview_invitation_draft WHERE id = ?", (draft_id,)
    ).fetchone()[0]
    original_digest = draft_body_digest(original_body)
    original_key = f"s1:{draft_id}:{original_digest}"
    _edit(conn, draft_id, "发送成功后当前草稿发生了新编辑")
    before = _invitation_fact_snapshot(conn)

    assert effect_mark_invitation_sent(
        conn, thread_id="app1", business_key=original_key,
        slot_id="s1", draft_id=draft_id, body_digest=original_digest, actor="hr-2",
    ) is None
    assert _invitation_fact_snapshot(conn) == before


def test_send_fact_schema_migrates_idempotently_without_changing_drafts(conn):
    draft_id = _persist(conn)
    before = conn.execute("SELECT * FROM interview_invitation_draft").fetchall()
    conn.execute("DROP TABLE invitation_send_log")  # simulate a pre-Task-7 database
    conn.commit()
    init_schema(conn)
    init_schema(conn)
    assert conn.execute("SELECT * FROM interview_invitation_draft").fetchall() == before
    columns = {r[1] for r in conn.execute("PRAGMA table_info(invitation_send_log)")}
    assert columns == {'thread_id', 'slot_id', 'draft_id', 'body_digest', 'actor', 'at'}
    conn.execute(
        "INSERT INTO invitation_send_log (thread_id, slot_id, draft_id, body_digest, actor) "
        "VALUES ('app1', 's1', ?, 'digest', 'hr-1')", (draft_id,),
    )
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO invitation_send_log (thread_id, slot_id, draft_id, body_digest, actor) "
            "VALUES ('app1', 's1', ?, 'digest', 'hr-2')", (draft_id,),
        )
    conn.rollback()
