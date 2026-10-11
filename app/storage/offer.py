"""Offer 记录与内部审批流的存储层判定真源（offer-generation U3，tasks 3.1–3.3/3.6）。

本模块只做**读 + 判定 + 抛具名异常**，⛔ 不写任何业务行：
- `offer` / `offer_approval` 的写入是 `app/graph/offer_nodes.py` 四个 effect_* 节点的
  独占职责（工程铁律 1：每个副作用独占一个节点、带幂等键、与 effect_log 同事务）。
- 本模块的读者有三个：`app/graph/offer_nodes.py`（节点内重做前置校验）、
  `app/web/server.py`（HTTP 面 4xx 映射）、`app/graph/letter_nodes.py`（导出门槛卡点）。

⛔ 本模块不 import 任何 `app/agents/**`：U3 一行 AI 调用都没有——这是 spec
「录用决定不由 AI 做」在当前代码面上的结构性保证。
"""
from __future__ import annotations

import sqlite3

from app.storage.offer_approval_chain import get_approval_chain

# 阶段语义序（CLAUDE.md 数据模型要点：stage_type 是语义标签，逻辑只认类型）。
# ⚠️ 'rejected' 刻意不在序里：淘汰阶段与主链不是同一条线，发起 Offer 的前置由
# application.status（active）与 rejection_record 两条判据负责，⛔ 不靠阶段位次推断。
STAGE_ORDER: tuple[str, ...] = ("initial", "screening", "interview", "offer", "hired")

# tasks 3.1「前置投递阶段 ≥ interview」。
OFFER_CREATABLE_MIN_STAGE: str = "interview"

# ⚠️ tasks 3.1 写「status=ongoing」，磁盘真身是
# application.status CHECK IN ('active','rejected','withdrawn','hired')
# （app/storage/db.py）。⛔ 本单元不改这个 CHECK（SQLite 改 CHECK 要整表重建，且
# 那是 M2 的契约）："投递未终止"在现网词表里就是 'active'。
ACTIVE_APPLICATION_STATUS: str = "active"

# 导出门槛（tasks 3.6；spec「审批通过才可导出」）：只有这五种状态可导出文书。
# pending_approval / needs_revision 一律拒绝。
EXPORTABLE_OFFER_STATUSES: frozenset[str] = frozenset(
    {"approved", "exported", "accepted", "declined", "negotiating"}
)

OFFER_STATUS_LABELS: dict[str, str] = {
    "pending_approval": "待审批",
    "needs_revision": "待修改",
    "approved": "已审批",
    "exported": "已导出",
    "accepted": "已接受",
    "declined": "已拒绝",
    "negotiating": "谈判中",
}


class OfferNotFoundError(LookupError):
    """投递没有 Offer 记录，或 offer_id 不存在。"""


class OfferNotCreatableError(ValueError):
    """发起 Offer 的前置不满足（阶段过早／投递已终止／已有淘汰记录／已有 Offer）。"""


class OfferNotEditableError(ValueError):
    """只有 needs_revision 的 Offer 可以修改（tasks 3.3）。"""


class OfferNotPendingApprovalError(ValueError):
    """Offer 不在待审批状态，不接受审批动作。"""


class ApprovalChainNotConfiguredError(ValueError):
    """该岗位该级没有配置审批人——fail-closed。

    ⛔ 不猜默认审批人、⛔ 不放行任意登录账号（spec「审批链 MUST 由 HR 维护，
    MUST NOT 由 AI 生成或推荐审批人」）。
    """


class NotAnApproverError(PermissionError):
    """操作人不在该级审批人列表内（spec Scenario「非审批人操作被拒」）。"""


class ApprovalOutOfOrderError(ValueError):
    """跳级审批：本轮更低级尚未通过（spec「逐级审批」）。"""


class ApprovalAlreadyRecordedError(ValueError):
    """该 (offer, round, level) 已有留痕——唯一索引告诉你这件事已经做完了。"""


class OfferNotExportableError(ValueError):
    """Offer 状态不在可导出白名单内（spec「审批通过才可导出」）。"""


def offer_status_label(status: str) -> str:
    return OFFER_STATUS_LABELS.get(status, status)


def stage_rank(stage_type: str) -> int:
    """阶段语义序的位次；未知（含 'rejected'）返回 -1。"""
    try:
        return STAGE_ORDER.index(stage_type)
    except ValueError:
        return -1


_OFFER_SELECT = (
    "SELECT o.id, o.application_id, o.job_id, o.department, o.start_date, o.report_to, "
    "o.note, o.status, o.approval_round, o.created_by, o.created_at, o.updated_by, "
    "o.updated_at, j.title, c.name "
    "FROM offer o "
    "JOIN job j ON j.id = o.job_id "
    "JOIN application a ON a.id = o.application_id "
    "JOIN candidate c ON c.id = a.candidate_id "
)


def _offer_row_to_dict(row) -> dict:
    return {
        "id": row[0],
        "application_id": row[1],
        "job_id": row[2],
        "department": row[3],
        "start_date": row[4],
        "report_to": row[5],
        "note": row[6],
        "status": row[7],
        "status_label": offer_status_label(row[7]),
        "approval_round": row[8],
        "created_by": row[9],
        "created_at": row[10],
        "updated_by": row[11],
        "updated_at": row[12],
        "job_title": row[13],
        "candidate_name": row[14],
    }


def get_offer(conn: sqlite3.Connection, offer_id: str) -> dict:
    row = conn.execute(_OFFER_SELECT + "WHERE o.id = ?", (offer_id,)).fetchone()
    if row is None:
        raise OfferNotFoundError(f"Offer 不存在: {offer_id!r}")
    return _offer_row_to_dict(row)


def get_offer_by_application(
    conn: sqlite3.Connection, application_id: str
) -> dict | None:
    row = conn.execute(
        _OFFER_SELECT + "WHERE o.application_id = ?", (application_id,)
    ).fetchone()
    return None if row is None else _offer_row_to_dict(row)


def assert_offer_creatable(conn: sqlite3.Connection, *, application_id: str) -> None:
    """tasks 3.1 的发起前置，逐条给出可执行的拒绝理由（调用方各映射成一个 4xx）。"""
    row = conn.execute(
        "SELECT a.status, s.stage_type FROM application a "
        "JOIN stage s ON s.id = a.current_stage_id WHERE a.id = ?",
        (application_id,),
    ).fetchone()
    if row is None:
        raise OfferNotFoundError(f"投递不存在: {application_id!r}")
    status, stage_type = row
    if status != ACTIVE_APPLICATION_STATUS:
        raise OfferNotCreatableError(
            f"投递状态为 {status!r}，已终止的投递不能发起 Offer"
        )
    if stage_rank(stage_type) < stage_rank(OFFER_CREATABLE_MIN_STAGE):
        raise OfferNotCreatableError(
            f"投递当前阶段为 {stage_type!r}，只有 {OFFER_CREATABLE_MIN_STAGE} 及之后的"
            "阶段才能发起 Offer"
        )
    if (
        conn.execute(
            "SELECT 1 FROM rejection_record WHERE application_id = ?",
            (application_id,),
        ).fetchone()
        is not None
    ):
        raise OfferNotCreatableError("该投递已有淘汰记录，不能发起 Offer")
    if (
        conn.execute(
            "SELECT 1 FROM offer WHERE application_id = ?", (application_id,)
        ).fetchone()
        is not None
    ):
        raise OfferNotCreatableError("该投递已有 Offer 记录")


def approval_levels(conn: sqlite3.Connection, job_id: str) -> list[int]:
    """该岗位审批链的级号序列（升序）。未配置 ⇒ U1 的默认一级占位 [1]。"""
    return [item["level"] for item in get_approval_chain(conn, job_id)]


def total_approval_levels(conn: sqlite3.Connection, job_id: str) -> int:
    """最后一级的级号——「最后一级 approved ⇒ offer.status=approved」的判据。"""
    levels = approval_levels(conn, job_id)
    return max(levels) if levels else 1


def approvers_for_level(
    conn: sqlite3.Connection, job_id: str, level: int
) -> list[str]:
    """该岗位该级的审批人列表；未配置该级或该级无审批人 ⇒ 空列表（fail-closed）。"""
    for item in get_approval_chain(conn, job_id):
        if item["level"] == level:
            return list(item["approver_account_ids"])
    return []


def current_approval_level(conn: sqlite3.Connection, offer_id: str) -> int:
    """本轮**当前待审**的级号 = 本轮已通过的最大级号 + 1（无通过记录 ⇒ 1）。

    退回后 `round+1`，新一轮没有通过记录 ⇒ 自然回到第 1 级——这就是
    design D6「HR 改后链从第一级重走」的落点，⛔ 不额外写"重置"语句。
    """
    offer = get_offer(conn, offer_id)
    row = conn.execute(
        "SELECT COALESCE(MAX(level), 0) FROM offer_approval "
        "WHERE offer_id = ? AND round = ? AND decision = 'approved'",
        (offer_id, offer["approval_round"]),
    ).fetchone()
    return int(row[0]) + 1


def approval_history(conn: sqlite3.Connection, offer_id: str) -> list[dict]:
    """全部轮次的审批留痕（升序）。⛔ 无 AI 字段——只有人、时刻、结论、意见。"""
    rows = conn.execute(
        "SELECT round, level, approver, decision, comment, at FROM offer_approval "
        "WHERE offer_id = ? ORDER BY round ASC, level ASC",
        (offer_id,),
    ).fetchall()
    return [
        {
            "round": row[0],
            "level": row[1],
            "approver": row[2],
            "decision": row[3],
            "comment": row[4],
            "at": row[5],
        }
        for row in rows
    ]


def list_pending_offers_for(conn: sqlite3.Connection, approver: str) -> list[dict]:
    """「本人待审」列表（tasks 3.4 的 `GET /offers/pending`）。

    判据不是"岗位审批链里有这个人"，而是**他此刻正好站在本轮当前级上**：
    取 `status='pending_approval'` 的 Offer，逐条算本轮当前级，再取该级审批人
    列表比对。年 Offer 量两位数（design「规模」），逐条算的开销可忽略；
    ⛔ 不在 SQL 里塞 JSON 解析（`approver_account_ids` 是 JSON 文本，
    在应用层走 U1 的 `get_approval_chain` 才是唯一真源）。
    """
    rows = conn.execute(
        _OFFER_SELECT
        + "WHERE o.status = 'pending_approval' ORDER BY o.created_at ASC, o.id ASC"
    ).fetchall()
    items: list[dict] = []
    for row in rows:
        offer = _offer_row_to_dict(row)
        level = current_approval_level(conn, offer["id"])
        approvers = approvers_for_level(conn, offer["job_id"], level)
        if approver in approvers:
            offer["current_level"] = level
            offer["total_levels"] = total_approval_levels(conn, offer["job_id"])
            offer["current_level_approvers"] = approvers
            items.append(offer)
    return items


def assert_offer_exportable(conn: sqlite3.Connection, application_id: str) -> None:
    """tasks 3.6 的导出门槛：offer.status ∉ EXPORTABLE_OFFER_STATUSES ⇒ 拒绝。

    两个读者共用本函数（唯一真源，⛔ 不许任何调用点自己再判一遍状态）：
    `app/graph/letter_nodes.py::record_letter_access`（导出路径的唯一卡点）与
    `app/web/server.py` 的导出路由（把异常映射成 409 + 可读提示）。
    """
    offer = get_offer_by_application(conn, application_id)
    if offer is None:
        raise OfferNotFoundError(f"投递 {application_id!r} 尚无 Offer 记录")
    if offer["status"] not in EXPORTABLE_OFFER_STATUSES:
        raise OfferNotExportableError(
            f"Offer 当前状态为「{offer['status_label']}」，审批通过后才可导出文书"
        )
