"""onboarding-flow U2 清单页与进度的 L4 编排层节点（tasks 2.1/2.2）。

本模块同时承载 U2 清单节点与 U3 终态、处置登记节点。

与 app/graph/invite_nodes.py 同一形态：Web 通道下"挂起等人确认"由 HTTP 端点
直接调用普通 Python 函数达成，不建真实 LangGraph interrupt()（2026-08-26 判例）。

thread_id 语义：实例化取 application_id（一份投递一份清单）；条目更新取 item_id
（tasks 2.2 幂等键 {item_id}:effect_update_item:{to_status}:{request_id}）。
"""
from __future__ import annotations

from datetime import date
import json
import logging
import sqlite3
import uuid

from app.storage.idempotency import idempotent_effect

logger = logging.getLogger(__name__)


class ChecklistInstantiateRejected(Exception):
    """投递不满足实例化前置（application.status=hired 且 offer.status=accepted）。"""


class ChecklistTemplateMissing(Exception):
    """岗位与部门（含 default 兜底）都没有清单模板。"""


def _resolve_template(conn: sqlite3.Connection, *, job_id: str) -> dict | None:
    """模板选择（design D1）：岗位模板优先，否则部门模板（job.department），
    再否则部门 default 兜底模板。返回 {'version': int, 'items': list[dict]}。
    """
    row = conn.execute(
        "SELECT version, items FROM onboarding_template "
        "WHERE scope_type='job' AND scope_id=? ORDER BY version DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    if row is not None:
        return {"version": row[0], "items": json.loads(row[1])}

    dept = conn.execute("SELECT department FROM job WHERE id=?", (job_id,)).fetchone()
    department = dept[0] if dept is not None else None
    for scope_id in (department, "default"):
        if not scope_id:
            continue
        row = conn.execute(
            "SELECT version, items FROM onboarding_template "
            "WHERE scope_type='department' AND scope_id=? ORDER BY version DESC LIMIT 1",
            (scope_id,),
        ).fetchone()
        if row is not None:
            return {"version": row[0], "items": json.loads(row[1])}
    return None


@idempotent_effect("effect_instantiate_checklist")
def effect_instantiate_checklist(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str, created_by: str
) -> str:
    """effect_* 节点：写 onboarding_checklist + onboarding_item，同事务。

    前置 = application.status='hired' AND offer.status='accepted'；入职日读
    offer.start_date（offer-generation U1 已落盘的唯一入职日列）。
    幂等键 = {application_id}:effect_instantiate_checklist:{business_key}；
    命中 effect_log 由装饰器短路返回 None，调用方回查既有清单。
    """
    application_id = thread_id
    row = conn.execute(
        "SELECT a.status, a.job_id, o.status AS offer_status, o.start_date "
        "FROM application a LEFT JOIN offer o ON o.application_id = a.id "
        "WHERE a.id = ?",
        (application_id,),
    ).fetchone()
    if row is None or row[0] != "hired" or row[2] != "accepted":
        raise ChecklistInstantiateRejected("仅 hired 且 Offer 已接受的投递可生成清单")
    if row[3] is None:
        raise ChecklistInstantiateRejected("Offer 缺少入职日期")

    template = _resolve_template(conn, job_id=row[1])
    if template is None:
        raise ChecklistTemplateMissing("岗位与部门均无清单模板")

    checklist_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO onboarding_checklist "
        "(id, application_id, template_version, start_date, created_by) "
        "VALUES (?, ?, ?, ?, ?)",
        (checklist_id, application_id, template["version"], row[3], created_by),
    )
    for item in template["items"]:
        conn.execute(
            "INSERT INTO onboarding_item "
            "(id, checklist_id, name, owner_party, due_offset_days, required) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                checklist_id,
                item["name"],
                item["owner_party"],
                int(item["due_offset_days"]),
                1 if item["required"] else 0,
            ),
        )
    return checklist_id


class ItemUpdateRejected(Exception):
    """条目状态流转非法（状态机不合法 / 豁免缺原因）。"""


class ItemOperatorForbidden(Exception):
    """操作人无权修改该条目（非 HR、非对应部门经理）。"""


_ALLOWED_ITEM_TRANSITIONS = {
    ("pending", "done"),
    ("pending", "waived"),
    ("done", "pending"),
    ("waived", "pending"),
}


@idempotent_effect("effect_update_item")
def effect_update_item(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    to_status: str,
    reason: str | None,
    operator_username: str,
) -> None:
    """effect_* 节点：写 onboarding_item + onboarding_item_history，同事务。

    操作人限 HR 或"该条目对应部门经理"：HR 无条件；dept_manager 仅当
    owner_party='dept' 且 manager.department == job.department（design D2）。
    幂等键 = {item_id}:effect_update_item:{to_status}:{request_id}（business_key
    由调用方拼 {to_status}:{request_id}）。
    """
    item_id = thread_id
    item = conn.execute(
        "SELECT i.id, i.checklist_id, i.owner_party, i.status, c.application_id "
        "FROM onboarding_item i JOIN onboarding_checklist c ON c.id = i.checklist_id "
        "WHERE i.id = ?",
        (item_id,),
    ).fetchone()
    if item is None:
        raise ItemUpdateRejected("条目不存在")
    checklist_id, owner_party, from_status, application_id = item[1], item[2], item[3], item[4]

    if to_status not in ("done", "waived", "pending"):
        raise ItemUpdateRejected("非法目标状态")
    if (from_status, to_status) not in _ALLOWED_ITEM_TRANSITIONS:
        raise ItemUpdateRejected(f"非法状态流转: {from_status} -> {to_status}")
    if to_status == "waived" and (not reason or not reason.strip()):
        raise ItemUpdateRejected("豁免必须填写原因")

    operator = conn.execute(
        "SELECT role, department FROM hr_account WHERE username = ?", (operator_username,)
    ).fetchone()
    if operator is None:
        raise ItemOperatorForbidden("操作人不存在")
    role, operator_department = operator[0], operator[1]
    if role == "hr":
        pass
    elif role == "dept_manager":
        if owner_party != "dept":
            raise ItemOperatorForbidden("部门经理只能修改本部门负责的条目")
        job_department = conn.execute(
            "SELECT j.department FROM application a JOIN job j ON j.id = a.job_id "
            "WHERE a.id = ?",
            (application_id,),
        ).fetchone()
        if job_department is None or job_department[0] != operator_department:
            raise ItemOperatorForbidden("跨部门条目不可修改")
    else:
        raise ItemOperatorForbidden("仅 HR 或部门经理可修改条目")

    conn.execute(
        "UPDATE onboarding_item SET status=?, reason=?, acted_by=?, acted_at=datetime('now') "
        "WHERE id=?",
        (to_status, reason, operator_username, item_id),
    )
    conn.execute(
        "INSERT INTO onboarding_item_history "
        "(id, item_id, from_status, to_status, reason, acted_by, at) "
        "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
        (str(uuid.uuid4()), item_id, from_status, to_status, reason, operator_username),
    )


# ── 以下属 onboarding-flow U3（终态与处置登记，tasks 3.1–3.3）─────────────

# 六类待处置数据。⛔ 与 app/storage/db.py 的 data_disposition_queue.category CHECK
# 逐字同源（post-hire-data-disposition spec「入职完成时登记处置事项」原话：
# 简历文件与原文、解析字段、评分与证据、面试场次与邀约文书、联系方式、Offer 文书）。
DISPOSITION_CATEGORIES = (
    "resume_file",
    "parsed_fields",
    "scores",
    "interview",
    "contact",
    "offer_letter",
)

# 六条登记各自的幂等键，category 是此处的 business_key。
_ENQUEUE_NODE_NAME = "effect_enqueue_disposition"


def _enqueue_disposition_rows(
    conn: sqlite3.Connection, *, application_id: str, candidate_id: str
) -> int:
    """登记六类待处置事项（tasks 3.2），返回该投递的登记行数。

    - 每行：`policy_version=NULL`（待策略）、`planned_action='pending'`、
      `due_at=NULL`、`executed_at=NULL`——🔴 留存策略未签认前不执行任何删除/脱敏。
    - 幂等两道：`data_disposition_queue(application_id, category)` 唯一索引（结构）
      ＋ 每类一条 `effect_log`（`{application_id}:effect_enqueue_disposition:{category}`）。
    - **⛔ 本函数不 commit**：它既被 `effect_enqueue_disposition`（独立入口）调用，
      也被 `effect_complete_onboarding` 在**同一事务**里调用（tasks 3.1 原话
      「写 history ＋ 清单 closed ＋ 3.2 登记，同事务」）。在这里 commit 会把
      "同事务"打散成两个事务——清单关了、登记没落，或反过来。
    """
    for category in DISPOSITION_CATEGORIES:
        conn.execute(
            "INSERT OR IGNORE INTO data_disposition_queue "
            "(id, application_id, candidate_id, category, policy_version, planned_action) "
            "VALUES (?, ?, ?, ?, NULL, 'pending')",
            (str(uuid.uuid4()), application_id, candidate_id, category),
        )
        conn.execute(
            "INSERT OR IGNORE INTO effect_log "
            "(effect_key, thread_id, node_name, business_key, applied_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (
                f"{application_id}:{_ENQUEUE_NODE_NAME}:{category}",
                application_id,
                _ENQUEUE_NODE_NAME,
                category,
            ),
        )
    return conn.execute(
        "SELECT COUNT(*) FROM data_disposition_queue WHERE application_id = ?",
        (application_id,),
    ).fetchone()[0]


def effect_enqueue_disposition(
    conn: sqlite3.Connection, *, application_id: str, candidate_id: str
) -> int:
    """effect_* 节点（tasks 3.2）的独立入口：登记六类处置事项并提交。

    ⛔ 刻意**不套** `@idempotent_effect`：本单元的幂等粒度是"每类一把键"（六把，
    tasks 3.2 字面），外层再套一把单键装饰器会让粒度混淆（装饰器会往 effect_log
    写一条 `{application_id}:effect_enqueue_disposition:{business_key}`，与六把
    键并存两套语义）。幂等由上面两道兑现，重复调用返回 6、不新增行。

    生产主路径是 `effect_complete_onboarding` 内部同事务调用，本入口供补登记/
    重放使用（例如为历史"已入职"投递补登记）。
    """
    try:
        count = _enqueue_disposition_rows(
            conn, application_id=application_id, candidate_id=candidate_id
        )
        conn.commit()
        return count
    except Exception:
        try:
            conn.rollback()
        except Exception:
            logger.exception(
                "rollback failed while cleaning up effect_enqueue_disposition "
                "for application_id=%s",
                application_id,
            )
        raise


# U3 终态动作与结局。
TERMINAL_OUTCOMES = ("hired",)  # Task 5 扩为 ("hired", "abandoned")
TERMINAL_ACTION_ONBOARDED = "onboarded"
TERMINAL_ACTION_ABANDONED = "abandoned"
TERMINAL_ACTIONS = (TERMINAL_ACTION_ONBOARDED, TERMINAL_ACTION_ABANDONED)


class OnboardingTerminalRejected(Exception):
    """终态动作不满足前置条件。"""


def terminal_history_rows(
    conn: sqlite3.Connection, application_id: str
) -> list[sqlite3.Row]:
    """只读返回该投递的终态流转事实；终态由 action 值定义。"""
    return conn.execute(
        "SELECT id, action, actor_type, actor, occurred_at FROM application_stage_history "
        "WHERE application_id = ? AND action IN (?, ?) ORDER BY occurred_at, id",
        (application_id, TERMINAL_ACTION_ONBOARDED, TERMINAL_ACTION_ABANDONED),
    ).fetchall()


@idempotent_effect("effect_complete_onboarding")
def effect_complete_onboarding(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    actor_username: str,
    actual_start_date: str | None = None,
    note: str | None = None,
    reason: str | None = None,
    today: str | None = None,
) -> str:
    """确认入职终态，并在同一事务中关闭清单、登记六类待处置事项。"""
    application_id = thread_id
    outcome = business_key
    if outcome not in TERMINAL_OUTCOMES:
        raise OnboardingTerminalRejected(f"未知终态结局: {outcome!r}")
    if not actor_username or not actor_username.strip():
        raise OnboardingTerminalRejected("终态必须由可识别的人确认")

    row = conn.execute(
        "SELECT a.status, a.current_stage_id, a.candidate_id, "
        "c.id, c.status, o.status, o.start_date "
        "FROM application a "
        "LEFT JOIN onboarding_checklist c ON c.application_id = a.id "
        "LEFT JOIN offer o ON o.application_id = a.id WHERE a.id = ?",
        (application_id,),
    ).fetchone()
    if row is None:
        raise OnboardingTerminalRejected("投递不存在")
    _, current_stage, candidate_id, checklist_id, checklist_status, offer_status, offer_start_date = row

    if terminal_history_rows(conn, application_id):
        raise OnboardingTerminalRejected("该投递已有终态事实")
    if offer_status != "accepted":
        raise OnboardingTerminalRejected("Offer 尚未确认接受")
    if outcome == "hired":
        if checklist_id is None:
            raise OnboardingTerminalRejected("该投递尚无入职清单")
        if checklist_status != "open":
            raise OnboardingTerminalRejected(f"清单状态为 {checklist_status}，不能确认已入职")
        effective_start_date = actual_start_date or offer_start_date
        if not effective_start_date:
            raise OnboardingTerminalRejected("缺少实际入职日")
        today_str = today or date.today().isoformat()
        if effective_start_date > today_str:
            raise OnboardingTerminalRejected("入职日尚未到达，不能确认已入职")
        incomplete = conn.execute(
            "SELECT name, owner_party FROM onboarding_item "
            "WHERE checklist_id = ? AND required = 1 AND status = 'pending' ORDER BY rowid",
            (checklist_id,),
        ).fetchall()
        if incomplete and not (note and note.strip()):
            raise OnboardingTerminalRejected("仍有未完成的必需条目，必须填写说明")
        detail = {
            "outcome": TERMINAL_ACTION_ONBOARDED,
            "actual_start_date": effective_start_date,
            "offer_start_date": offer_start_date,
            "note": note or "",
            "incomplete_required_items": [
                {"name": item[0], "owner_party": item[1]} for item in incomplete
            ],
        }
        history_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO application_stage_history "
            "(id, application_id, from_stage_id, to_stage_id, actor_type, actor, action, detail_json) "
            "VALUES (?, ?, ?, 'hired', 'human', ?, ?, ?)",
            (
                history_id, application_id, current_stage, actor_username,
                TERMINAL_ACTION_ONBOARDED, json.dumps(detail, ensure_ascii=False),
            ),
        )
        conn.execute(
            "UPDATE application SET status = 'hired', current_stage_id = 'hired' WHERE id = ?",
            (application_id,),
        )
        conn.execute(
            "UPDATE onboarding_checklist SET status = 'closed', closed_reason = 'hired' WHERE id = ?",
            (checklist_id,),
        )
        _enqueue_disposition_rows(
            conn, application_id=application_id, candidate_id=candidate_id
        )
        return history_id

    # Task 5 实现 abandoned 分支。
    raise OnboardingTerminalRejected(f"未知终态结局: {outcome!r}")
