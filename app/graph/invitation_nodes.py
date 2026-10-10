"""邀约文案（U3）的 L4 编排层（interview-scheduling U3 tasks 3.2–3.6）。

`compute_invitation_draft_for_slot` 只读查库装配场次事实 + 模板，调 L3 纯函数生成
（工程铁律 2：compute_* 可以查库做输入组装）；写库全部在下面五个 effect_* 节点里，
每个独占、各带幂等键（铁律 1），由 `@idempotent_effect` 装饰器与业务写同事务提交——
⛔ 节点内一律不 `conn.commit()`。

thread_id 约定＝该场次所属 application_id（与 U2 `app/graph/scheduling_nodes.py` 同一
口径：一个投递一条 thread）。business_key 约定：

- persist = draft.run_id（同一次真实 LLM 调用只落一版草稿；再次生成是新 run_id ⇒ 版本递增）
- edit    = f"{draft_id}:{正文 sha256 前 16 位}"（同一次编辑重放短路，改了正文算新编辑）
- mark    = draft_id（一份草稿的「标记为人工撰写」是终态，至多一次）
- backfill= target_status（**同状态重复提交无第二条留痕**由这条键与
  `invitation_outcome_log` 的 UNIQUE(slot_id, status) 双保险）
- send    = f"{draft_id}:{正文 sha256 前 16 位}"（同一份文案至多尝试外发一次；
  改了正文重走门禁。⛔ 真正防重复投递的是内容哈希键
  `effect_deliver_message` / `effect_record_outbound_audit` 各自的 business_key，
  本节点的键只保证"同一次尝试不重复执行"）

⚠️ 偏离登记 D-U3-8（节点名字面量）：持久化节点在计划里叫 `effect_persist_draft`，
但**这个名字已被 M1 画像泳道占用**（`app/graph/nodes.py::effect_persist_draft`，落
job_profile 草案）。仓库级铁律 1 守卫
`tests/test_effect_idempotency_suite.py::test_no_duplicate_effect_node_name_literals`
禁止同一个 `@idempotent_effect(...)` 字面量出现在两处——两个节点共享 node_name 时，
只要 thread_id + business_key 也相同，幂等键 `{thread_id}:{node_name}:{business_key}`
就会撞车、后一个节点被静默短路；更隐蔽的是
`test_manifest_matches_the_source_tree` 会因重名而**误判为"清单已覆盖"**，新节点
从此躲过全部崩溃-恢复用例。⇒ 本节点改名 `effect_persist_invitation_draft`，
与仓库既有先例同构（`effect_persist_letter`、`effect_persist_prep_draft`，
后者正是为绕开同一个 `effect_persist_draft` 而加的前缀）。
⛔ 业务语义、幂等键口径、落库内容一律不变，只有字面量改了一个词。
"""
from __future__ import annotations

import hashlib
import sqlite3
import uuid

from app.agents.invitation_drafter import InvitationDraft, compute_invitation_draft
from app.agents.jd_agent import (
    UNKNOWN_GENERATED_AT,
    enforce_ai_label,
    extract_label_generated_at,
    strip_ai_label,
)
from app.storage.idempotency import idempotent_effect
from app.storage.interview_invitation import (
    InvitationNotAllowedError,
    InvitationSlotNotFoundError,
    InvitationTemplateMissingError,
    load_draft,
    next_draft_version,
    slot_application_id,
    slot_facts,
    slot_status,
)
from app.storage.invitation_template import get_invitation_template


def _assert_slot_invitable(conn: sqlite3.Connection, slot_id: str) -> str:
    """场次可发邀约的前置校验（spec「对已取消场次生成被拒」）。

    ⚠️ 事务内**重做**一遍：compute 与 persist 之间场次可能被改期或取消，
    只在 compute 侧校验等于没校验（重放路径根本不走 compute）。
    """
    status = slot_status(conn, slot_id)  # 场次不存在即抛 InvitationSlotNotFoundError
    if status == "cancelled":
        raise InvitationNotAllowedError("已取消的场次不可生成或回填邀约")
    return status


def compute_invitation_draft_for_slot(
    conn: sqlite3.Connection,
    *,
    slot_id: str,
    gateway,
    contact_hint: str | None = None,
) -> tuple[InvitationDraft, str]:
    """L4 compute_* 节点：先过前置校验（fail-fast，⛔ 不为一个必定被拒的请求白烧一次
    LLM 调用），再读模板 + 场次事实，调 L3 纯函数。返回 (草稿, 生成时绑定的模板版本)。"""
    facts = slot_facts(conn, slot_id)
    if facts is None:
        raise InvitationSlotNotFoundError(f"场次不存在: {slot_id!r}")
    _assert_slot_invitable(conn, slot_id)
    template = get_invitation_template(conn)
    if template is None:
        raise InvitationTemplateMissingError("还没有邀约模板，请先在模板维护页创建")

    application_id = slot_application_id(conn, slot_id)
    job_id = conn.execute(
        "SELECT job_id FROM application WHERE id = ?", (application_id,)
    ).fetchone()[0]
    draft = compute_invitation_draft(
        gateway,
        facts=facts,
        template_body=template["body"],
        template_version=template["version"],
        contact_hint=contact_hint,
        audit_context={
            "thread_id": f"{application_id}:invitation",
            "node": "compute_invitation_draft",
            "application_id": application_id,
            "job_id": job_id,
        },
    )
    return draft, template["version"]


@idempotent_effect("effect_persist_invitation_draft")
def effect_persist_invitation_draft(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    slot_id: str,
    template_version: str,
    draft: InvitationDraft,
) -> str:
    """effect_* 节点：把一版草稿落成 `interview_invitation_draft`（版本递增），
    独占、幂等。business_key = draft.run_id，幂等键 =
    {application_id}:effect_persist_invitation_draft:{analysis_run_id}——同一次真实
    LLM 调用只落一版；再次生成（新 run_id）得到新版本，旧版永久保留（spec
    「同一场次重复生成 MUST 产生新版本而不覆盖旧版本」）。
    ⚠️ 名字面量偏离计划原字面（见模块 docstring 的 D-U3-8）：`effect_persist_draft`
    已被 M1 画像泳道占用，重名会被仓库级铁律 1 守卫判红。

    ⚠️ 版本号在**本节点内部**算（`next_draft_version`），⛔ 不由调用方传入：版本号在
    路由里算、在事务里写，两者之间有窗口——同一场次并发两次生成会算出同一个版本号，
    撞 `(slot_id, version)` 唯一索引变成 500。放进事务里算，单连接串行化把它消掉。
    `invitation_status` 只在还是 'none' 时推进到 'drafted'：重新生成一份草稿⛔ 不得把
    已经回填过的 'sent'/'confirmed' 打回去。
    前置校验在事务内重做（见 `_assert_slot_invitable`）。⛔ 不在这里 conn.commit()。
    """
    _assert_slot_invitable(conn, slot_id)
    version = next_draft_version(conn, slot_id)
    draft_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO interview_invitation_draft "
        "(id, slot_id, version, template_version, body, ai_generated, analysis_run_id) "
        "VALUES (?, ?, ?, ?, ?, 1, ?)",
        (draft_id, slot_id, version, template_version, draft.body, draft.run_id),
    )
    conn.execute(
        "UPDATE interview_slot SET invitation_status = 'drafted', updated_at = datetime('now') "
        "WHERE id = ? AND invitation_status = 'none'",
        (slot_id,),
    )
    return draft_id
