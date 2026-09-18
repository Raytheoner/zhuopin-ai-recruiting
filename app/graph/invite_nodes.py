"""U3 邀约与同意流程 L4 编排层（voice-structured-interview tasks 4.1-4.11,
design D5/D6/D13/D14/D20）。

与 app/graph/interview_prep_nodes.py 同一形态：Web 通道下"挂起等人确认"由
HTTP 端点直接调用普通 Python 函数达成，不建真实 LangGraph interrupt()
（2026-08-26 判例，见 interview_prep_nodes.py 模块 docstring）。

thread_id 统一取 session_id（design D20 invite 子图定义）。本文件按 tasks.md
4.1→4.11 顺序组织：场次创建/令牌签发/令牌校验/续入令牌/邀约投递/同意/
验证码/HR 展示。
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.agents.jd_agent import AI_LABEL_TEMPLATE
from app.outbound.delivery import deliver_candidate_message
from app.outbound.messages import CandidateOutboundMessage
from app.storage.contact_source import is_contact_vault_available
from app.storage.idempotency import idempotent_effect
from app.storage.live_interview_gate import is_live_interview_enabled

logger = logging.getLogger(__name__)

TOKEN_BYTES = 32
DEFAULT_INVITE_EXPIRY_DAYS = 7
RETENTION_DAYS = 90
RETENTION_POLICY_VERSION = "v1-90d"  # design D18 起步值


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# ── 4.1 前置：场次创建 ──────────────────────────────────────────────

class PrepSnapshotNotFrozenForInviteError(Exception):
    """选中的投递没有已冻结的 prep 快照，不能签发邀约（tasks 4.11 前置）。"""


def compute_new_session(
    conn: sqlite3.Connection, *, application_id: str, prep_snapshot_version: int, sample_class: str
) -> dict[str, Any]:
    """纯计算：组装新场次的字段，不写库。retention_until 在这里算好——铁律
    要求留存期限必须在场次建立那一刻由应用层写入，不允许留空（U1 已有的
    NOT NULL 约束）。"""
    row = conn.execute(
        "SELECT status FROM prep_snapshot WHERE application_id = ? AND version = ?",
        (application_id, prep_snapshot_version),
    ).fetchone()
    if row is None or row[0] != "frozen":
        raise PrepSnapshotNotFrozenForInviteError(
            f"投递 {application_id!r} 版本 {prep_snapshot_version} 的 prep 快照未冻结"
        )
    retention_until = (_utcnow() + timedelta(days=RETENTION_DAYS)).isoformat()
    return {
        "id": str(uuid.uuid4()),
        "application_id": application_id,
        "prep_snapshot_version": prep_snapshot_version,
        "sample_class": sample_class,
        "retention_until": retention_until,
        "retention_policy_version": RETENTION_POLICY_VERSION,
    }


@idempotent_effect("effect_create_interview_session")
def effect_create_interview_session(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str, session: dict[str, Any]
) -> str:
    """effect_* 节点：写 interview_session 一行，status='pending'。business_key
    由调用方传 HR 点击签发时生成的 request_id（每次点击必须产生一次意图，即使
    参数逐字相同——与 effect_regenerate_prep_question 的 request_id 用法同一
    先例）。"""
    conn.execute(
        "INSERT INTO interview_session (id, application_id, prep_snapshot_version, "
        "retention_until, retention_policy_version, sample_class, status) "
        "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
        (
            session["id"], session["application_id"], session["prep_snapshot_version"],
            session["retention_until"], session["retention_policy_version"], session["sample_class"],
        ),
    )
    return session["id"]


# ── 4.9 / 4.10：签发前置闸 ───────────────────────────────────────────

class LiveInterviewNotEnabledError(Exception):
    """真实候选人开闸未开启，live 场次签发被拒（design D14，tasks 4.9）。"""


class ContactVaultUnavailableError(Exception):
    """candidate-contact-vault 未开启/未交付，live 场次签发被拒（tasks 4.10）。"""


def assert_invite_issuance_allowed(conn: sqlite3.Connection, *, sample_class: str) -> None:
    """签发前的结构性前置校验。internal_sim 场次不受这两道闸约束（spec
    「开关关闭时只允许为内部模拟场次签发」）；live 场次必须两道闸都通过。
    ⛔ 不在这里捕获异常——调用方（Web 路由）据异常类型返回 4xx 并留痕。"""
    if sample_class != "live":
        return
    if not is_live_interview_enabled():
        raise LiveInterviewNotEnabledError("真实候选人开闸未开启")
    if not is_contact_vault_available():
        raise ContactVaultUnavailableError("candidate-contact-vault 未开启，live 场次签发被拒")


# ── 4.1：令牌签发 ───────────────────────────────────────────────────

def generate_invite_token() -> str:
    """32 字节随机、URL-safe 编码（spec「MUST 不可猜测」）。"""
    return secrets.token_urlsafe(TOKEN_BYTES)


def load_invite_expiry_days(conn: sqlite3.Connection, job_id: str) -> int:
    row = conn.execute(
        "SELECT invite_expiry_days FROM job_prep_config WHERE job_id = ?", (job_id,)
    ).fetchone()
    if row is None or row[0] is None:
        return DEFAULT_INVITE_EXPIRY_DAYS
    return row[0]


def compute_new_invite_token(conn: sqlite3.Connection, *, job_id: str) -> tuple[str, str, str]:
    """纯计算：生成明文令牌、其哈希、到期时刻（ISO8601 UTC）。不写库。
    返回 (明文令牌, 哈希, 到期时刻字符串)——明文令牌只在这一次调用里出现，
    调用方负责把它拼进候选人链接，之后系统只认哈希。"""
    token = generate_invite_token()
    token_hash = _hash_token(token)
    days = load_invite_expiry_days(conn, job_id)
    expires_at = (_utcnow() + timedelta(days=days)).isoformat()
    return token, token_hash, expires_at


@idempotent_effect("effect_issue_invite")
def effect_issue_invite(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str,
    session_id: str, token_hash: str, expires_at: str,
) -> None:
    """effect_* 节点：business_key = token_hash（tasks 4.1 字面幂等键公式）。
    "同一场次重复签发 ⇒ 旧令牌作废" 由 UPDATE 覆盖旧哈希实现——旧哈希一旦被
    覆盖，任何用旧明文令牌算出的哈希都查不到匹配行，天然作废，不需要额外
    的"已作废"标记。"并留痕" 由 interview_invite_event 承担。"""
    prior = conn.execute(
        "SELECT invite_token_hash FROM interview_session WHERE id = ?", (session_id,)
    ).fetchone()
    was_reissue = prior is not None and prior[0] is not None
    conn.execute(
        "UPDATE interview_session SET invite_token_hash = ?, invite_expires_at = ? WHERE id = ?",
        (token_hash, expires_at, session_id),
    )
    conn.execute(
        "INSERT INTO interview_invite_event (id, session_id, event_type, detail) VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), session_id, "reissued" if was_reissue else "issued", token_hash),
    )


# ── 4.2：令牌校验端点 ────────────────────────────────────────────────

class InviteTokenInvalidError(Exception):
    """统一失效页情形：令牌未知、已用、已过期。⛔ 三种原因对候选人展示同一个
    页面文案（spec「MUST NOT 泄露场次或候选人信息」），区分只在留痕里。"""


def find_session_by_token(conn: sqlite3.Connection, token: str) -> str | None:
    token_hash = _hash_token(token)
    row = conn.execute(
        "SELECT id FROM interview_session WHERE invite_token_hash = ?", (token_hash,)
    ).fetchone()
    return row[0] if row else None


@idempotent_effect("effect_log_invite_access_denied")
def effect_log_invite_access_denied(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str, session_id: str, reason: str
) -> None:
    conn.execute(
        "INSERT INTO interview_invite_event (id, session_id, event_type) VALUES (?, ?, ?)",
        (str(uuid.uuid4()), session_id, reason),
    )


@idempotent_effect("effect_open_invite")
def effect_open_invite(conn: sqlite3.Connection, *, thread_id: str, business_key: str, session_id: str) -> None:
    """首次打开：status pending → in_progress。这一步状态转移本身就是"令牌
    已使用"的落点——第二次打开时 open_invite() 会看到 status != 'pending'
    并拒绝，等价于 spec 要求的"打开即失效"，不需要额外的 used_at 列。"""
    conn.execute(
        "UPDATE interview_session SET status = 'in_progress' WHERE id = ? AND status = 'pending'",
        (session_id,),
    )
    conn.execute(
        "INSERT INTO interview_invite_event (id, session_id, event_type) VALUES (?, ?, 'opened')",
        (str(uuid.uuid4()), session_id),
    )


def open_invite(conn: sqlite3.Connection, token: str) -> str:
    """L4 编排：查找 → 校验过期 → 校验未用 → 标记已用，四步必须在同一次
    请求内顺序发生（单连接 SQLite，无并发行锁问题，见 app/storage/db.py
    的单连接模型）。返回 session_id；任何一步不满足抛 InviteTokenInvalidError。
    """
    session_id = find_session_by_token(conn, token)
    if session_id is None:
        raise InviteTokenInvalidError("令牌无效")

    row = conn.execute(
        "SELECT invite_expires_at, status FROM interview_session WHERE id = ?", (session_id,)
    ).fetchone()
    expires_at_raw, status = row

    if _utcnow() > _parse_iso(expires_at_raw):
        effect_log_invite_access_denied(
            conn, thread_id=session_id, business_key=f"expired:{uuid.uuid4().hex}",
            session_id=session_id, reason="expired_access",
        )
        raise InviteTokenInvalidError("令牌已过期")

    if status != "pending":
        effect_log_invite_access_denied(
            conn, thread_id=session_id, business_key=f"reused:{uuid.uuid4().hex}",
            session_id=session_id, reason="reused_access",
        )
        raise InviteTokenInvalidError("令牌已使用")

    effect_open_invite(conn, thread_id=session_id, business_key="open", session_id=session_id)
    return session_id


# ── 4.3：续入令牌 ───────────────────────────────────────────────

MAX_RESUME_ISSUANCES = 3


class ResumeTokenLimitExceededError(Exception):
    """续入令牌签发次数已达上限（tasks 4.3：一次性、上限 3 次）。"""


def resume_issuance_count(conn: sqlite3.Connection, session_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM interview_invite_event WHERE session_id = ? AND event_type = 'resume_issued'",
        (session_id,),
    ).fetchone()
    return row[0]


def compute_new_resume_token(conn: sqlite3.Connection, *, session_id: str) -> tuple[str, str]:
    """纯计算：生成续入令牌明文与哈希。不写库；上限校验在这里抛出——签发
    次数是只读查询，不需要等到 effect 节点才发现超限。"""
    if resume_issuance_count(conn, session_id) >= MAX_RESUME_ISSUANCES:
        raise ResumeTokenLimitExceededError(
            f"场次 {session_id!r} 续入令牌已达上限 {MAX_RESUME_ISSUANCES} 次"
        )
    token = generate_invite_token()
    token_hash = _hash_token(token)
    return token, token_hash


@idempotent_effect("effect_issue_resume_token")
def effect_issue_resume_token(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str, session_id: str, token_hash: str
) -> None:
    """business_key = token_hash。旧续入令牌（若有）同样被覆盖作废——同一
    场次同一时刻只有一枚有效续入令牌，与主令牌同一手法。"""
    conn.execute(
        "UPDATE interview_session SET resume_token_hash = ? WHERE id = ?", (token_hash, session_id)
    )
    conn.execute(
        "INSERT INTO interview_invite_event (id, session_id, event_type, detail) "
        "VALUES (?, ?, 'resume_issued', ?)",
        (str(uuid.uuid4()), session_id, token_hash),
    )


@idempotent_effect("effect_consume_resume_token")
def effect_consume_resume_token(conn: sqlite3.Connection, *, thread_id: str, business_key: str, session_id: str) -> None:
    """一次性消费：清空 resume_token_hash，场次回到 in_progress。
    business_key 由调用方传 token_hash——同一枚令牌只能被消费一次，
    第二次打开同一 token 时 open_resume() 在查找阶段就已经因
    resume_token_hash 已被清空而查不到 session，不会重复走到这里。"""
    conn.execute(
        "UPDATE interview_session SET resume_token_hash = NULL, status = 'in_progress' WHERE id = ?",
        (session_id,),
    )


def open_resume(conn: sqlite3.Connection, token: str) -> tuple[str, int]:
    """L4 编排：校验续入令牌并返回 (session_id, next_seq)。next_seq = 该场次
    已落库 interview_turn 的最大 seq + 1（没有 turn 时为 1）——⛔ 不重复
    出题：出题内容仍是冻结快照里原来的题，本函数只决定从第几题继续
    （与 5.9 联动，本单元只交付这个查询本身）。"""
    token_hash = _hash_token(token)
    row = conn.execute(
        "SELECT id, status FROM interview_session WHERE resume_token_hash = ?", (token_hash,)
    ).fetchone()
    if row is None:
        raise InviteTokenInvalidError("续入令牌无效")
    session_id, status = row
    if status not in ("interrupted", "in_progress"):
        raise InviteTokenInvalidError("续入令牌已失效")

    turn_row = conn.execute(
        "SELECT MAX(seq) FROM interview_turn WHERE session_id = ?", (session_id,)
    ).fetchone()
    next_seq = (turn_row[0] or 0) + 1

    effect_consume_resume_token(conn, thread_id=session_id, business_key=token_hash, session_id=session_id)
    return session_id, next_seq


# ── 4.4：邀约投递（经既有外发门禁） ──────────────────────────────────

def render_invite_body(*, candidate_link: str, job_title: str) -> str:
    """草稿正文，复用 jd_agent 的 AI 生成标识模板（design D6：不另写一套）。"""
    generated_at = _utcnow().isoformat()
    label = AI_LABEL_TEMPLATE.format(generated_at=generated_at)
    return (
        f"您好，您已进入「{job_title}」岗位的 AI 结构化面试环节。\n"
        f"请点击以下链接开始（链接仅可使用一次，请勿转发给他人）：\n{candidate_link}\n\n"
        f"{label}"
    )


def compose_invite_draft(*, candidate_link: str, job_title: str) -> tuple[str, str]:
    """返回 (draft_id, body)。draft_id 是这次拟稿的稳定标识，用作
    effect_deliver_invitation 的幂等键（tasks 4.4 字面公式
    `{session_id}:effect_deliver_invitation:{draft_id}`）——同一次拟稿只投递
    一次，重新拟稿（如改了文案）产生新 draft_id，允许重新走一次门禁。"""
    draft_id = uuid.uuid4().hex
    body = render_invite_body(candidate_link=candidate_link, job_title=job_title)
    return draft_id, body


@idempotent_effect("effect_deliver_invitation")
def effect_deliver_invitation(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str,
    session_id: str, recipient: str, body: str, channel, recorder,
    outbound_enabled, confirmed_by: str | None = None,
) -> None:
    """effect_* 节点：调既有门禁唯一入口 deliver_candidate_message()。
    ⛔ 本函数不绕过门禁直连任何通道实现的投递方法——反证测试
    test_no_direct_channel_deliver_import 会源码级扫描本文件确认不出现
    绕开 deliver_candidate_message 的直连 import。

    总开关关闭 ⇒ 门禁拒绝（REASON_OUTBOUND_DISABLED）⇒ 留 'manual_handoff'
    事件，链接由调用方（Web 路由）已经拿在手里、直接展示在 HR 工作台，不需要
    本函数额外处理；总开关开启且 confirmed_by 非空 ⇒ 门禁放行 ⇒ 留
    'delivered' 事件。"""
    message = CandidateOutboundMessage(
        message_type="interview_invitation",
        recipient=recipient,
        body=body,
        confirmed_by=confirmed_by,
    )
    decision = deliver_candidate_message(
        conn, thread_id=thread_id, message=message, channel=channel,
        recorder=recorder, outbound_enabled=outbound_enabled,
    )
    conn.execute(
        "INSERT INTO interview_invite_event (id, session_id, event_type, detail) VALUES (?, ?, ?, ?)",
        (
            str(uuid.uuid4()), session_id,
            "delivered" if decision.allowed else "manual_handoff",
            decision.reason or "",
        ),
    )
