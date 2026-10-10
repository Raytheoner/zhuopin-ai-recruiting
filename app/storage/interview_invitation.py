"""邀约文案（U3）存储层：草稿版本号、场次事实装配、回填留痕读取、收件对象解析。

只做两件事：**只读**的输入装配 与 **纯计算**（下一版版本号、版本比较）。写入一律在
app/graph/invitation_nodes.py 的 effect_* 节点里——⛔ 本模块不做任何业务写，下面几个
异常类只是错误分类。

收件对象解析是**唯一**读 vault 的地方：U4（candidate-contact-vault）尚未交付，所以走
惰性 import + fail-closed（模块不存在 / 开关关闭 / 读取异常 ⇒ None ⇒ 门禁按「收件对象
缺失或为空」拦截）。与 app/storage/contact_source.py 的既有做法同款；⛔ 不在模块顶部
import vault，那会让 U3 整条 import 链在 U4 交付前直接 ImportError。
"""
from __future__ import annotations

import logging
import sqlite3

from app.agents.invitation_drafter import InvitationSlotFacts

logger = logging.getLogger(__name__)

# 已登记的回填状态（与 interview_slot.invitation_status 的 CHECK 取值对齐）。
OUTCOME_STATUSES: tuple[str, ...] = (
    "sent", "confirmed", "declined", "reschedule_requested",
)

# 人工发出渠道（spec「已发出（含渠道：微信／邮件／电话）」）。
SENT_CHANNELS: tuple[str, ...] = ("wechat", "email", "phone")


class InvitationSlotNotFoundError(ValueError):
    """slot_id 不存在。"""


class InvitationNotAllowedError(ValueError):
    """场次状态不允许当前动作（已取消的场次不可生成邀约／回填）。"""


class InvitationOutcomeAlreadyRecordedError(ValueError):
    """该 (slot_id, status) 已有回填记录（用**不同**幂等键重复提交）。

    2026-10-11 修正（Spec review F1）：回填节点命中「已有行」时必须抛本异常——
    ⛔ 不能返回成功形状：那是零业务写，而 `@idempotent_effect` 仍会写一行
    effect_log 并提交，「effect_log 条数 ↔ 业务表行数按 thread 恒等」当场被破坏。
    调用方（路由）捕获后把 `outcome` 原样返回即可（幂等成功响应）。
    """

    def __init__(self, message: str, *, outcome: dict):
        super().__init__(message)
        self.outcome = outcome


class InvitationTemplateMissingError(ValueError):
    """还没有任何邀约模板。"""


class InvitationDraftNotFoundError(ValueError):
    """draft_id 不存在。"""


def next_draft_version(conn: sqlite3.Connection, slot_id: str) -> int:
    """该场次的下一个草稿版本号（同场次内单调递增，⛔ 不覆盖旧版）。"""
    row = conn.execute(
        "SELECT MAX(version) FROM interview_invitation_draft WHERE slot_id = ?",
        (slot_id,),
    ).fetchone()
    return (row[0] or 0) + 1


_DRAFT_COLUMNS = (
    "id, slot_id, version, template_version, body, ai_generated, "
    "authorship_marked_by, authorship_marked_at, analysis_run_id, created_at"
)


def _draft_dict(row) -> dict:
    (
        draft_id, slot_id, version, template_version, body, ai_generated,
        authorship_marked_by, authorship_marked_at, analysis_run_id, created_at,
    ) = row
    return {
        "id": draft_id,
        "slot_id": slot_id,
        "version": version,
        "template_version": template_version,
        "body": body,
        "ai_generated": bool(ai_generated),
        "authorship_marked_by": authorship_marked_by,
        "authorship_marked_at": authorship_marked_at,
        "analysis_run_id": analysis_run_id,
        "created_at": created_at,
    }


def load_draft(conn: sqlite3.Connection, draft_id: str) -> dict:
    row = conn.execute(
        f"SELECT {_DRAFT_COLUMNS} FROM interview_invitation_draft WHERE id = ?",
        (draft_id,),
    ).fetchone()
    if row is None:
        raise InvitationDraftNotFoundError(f"草稿不存在: {draft_id!r}")
    return _draft_dict(row)


def list_drafts(conn: sqlite3.Connection, slot_id: str) -> list[dict]:
    rows = conn.execute(
        f"SELECT {_DRAFT_COLUMNS} FROM interview_invitation_draft "
        "WHERE slot_id = ? ORDER BY version",
        (slot_id,),
    ).fetchall()
    return [_draft_dict(r) for r in rows]


def latest_draft(conn: sqlite3.Connection, slot_id: str) -> dict | None:
    drafts = list_drafts(conn, slot_id)
    return drafts[-1] if drafts else None


def slot_facts(conn: sqlite3.Connection, slot_id: str) -> InvitationSlotFacts | None:
    """装配生成邀约所需的场次事实（候选人/岗位/轮次/时刻/形式/面试官称谓）。
    ⛔ 不读任何评分、排名、硬门槛——本函数的 SELECT 里就没有这些表。"""
    row = conn.execute(
        "SELECT c.name, j.title, s.round, s.start_at, s.end_at, s.mode, s.location_or_link "
        "FROM interview_slot s "
        "JOIN application a ON a.id = s.application_id "
        "JOIN candidate c ON c.id = a.candidate_id "
        "JOIN job j ON j.id = a.job_id "
        "WHERE s.id = ?",
        (slot_id,),
    ).fetchone()
    if row is None:
        return None
    candidate_name, job_title, round_, start_at, end_at, mode, location_or_link = row
    interviewer_names = [
        r[0]
        for r in conn.execute(
            "SELECT i.name FROM interview_slot_interviewer x "
            "JOIN interviewer i ON i.id = x.interviewer_id "
            "WHERE x.interview_slot_id = ? ORDER BY i.name COLLATE NOCASE, i.id",
            (slot_id,),
        ).fetchall()
    ]
    return InvitationSlotFacts(
        candidate_name=candidate_name,
        job_title=job_title,
        round=round_,
        start_at=start_at,
        end_at=end_at,
        mode=mode,
        location_or_link=location_or_link,
        interviewer_names=interviewer_names,
    )


def slot_application_id(conn: sqlite3.Connection, slot_id: str) -> str:
    row = conn.execute(
        "SELECT application_id FROM interview_slot WHERE id = ?", (slot_id,)
    ).fetchone()
    if row is None:
        raise InvitationSlotNotFoundError(f"场次不存在: {slot_id!r}")
    return row[0]


def slot_status(conn: sqlite3.Connection, slot_id: str) -> str:
    row = conn.execute(
        "SELECT status FROM interview_slot WHERE id = ?", (slot_id,)
    ).fetchone()
    if row is None:
        raise InvitationSlotNotFoundError(f"场次不存在: {slot_id!r}")
    return row[0]


def list_outcomes(conn: sqlite3.Connection, slot_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, slot_id, status, channel, reason, actor, at "
        "FROM invitation_outcome_log WHERE slot_id = ? ORDER BY at, id",
        (slot_id,),
    ).fetchall()
    return [
        {
            "id": r[0], "slot_id": r[1], "status": r[2], "channel": r[3],
            "reason": r[4], "actor": r[5], "at": r[6],
        }
        for r in rows
    ]


def candidate_recipient_for_invitation(
    conn: sqlite3.Connection, *, application_id: str
) -> str | None:
    """系统外发用的收件对象（拍平字符串）。vault 不可用一律 None——U4 开关默认关、
    模块可能还没交付，两种情况结论都是"收件对象未知 ⇒ 门禁拦"，⛔ 绝不回退到
    "用 application_id 拼一个假收件人"，那等于给门禁喂一个假地址。"""
    try:
        # 惰性 import：理由见模块 docstring（U4 交付前本模块必须能 import 成功）。
        from app.storage.contact_vault import (
            is_candidate_contact_vault_enabled,
            read_contact,
        )
    except ImportError:
        return None
    try:
        if not is_candidate_contact_vault_enabled():
            return None
        contact = read_contact(
            conn,
            application_id=application_id,
            accessor="system:interview-invitation-send",
            purpose="interview_invitation_outbound",
        )
    except Exception:  # noqa: BLE001 —— 未知即不可用（fail-closed）
        logger.exception(
            "application_id=%s 读取 candidate-contact-vault 失败，按不可用处理",
            application_id,
        )
        return None
    for attr in ("phone", "email"):
        value = getattr(contact, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    return None
