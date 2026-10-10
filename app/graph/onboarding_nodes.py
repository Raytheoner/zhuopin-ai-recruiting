"""onboarding-flow U2 清单页与进度的 L4 编排层节点（tasks 2.1/2.2）。

与 app/graph/invite_nodes.py 同一形态：Web 通道下"挂起等人确认"由 HTTP 端点
直接调用普通 Python 函数达成，不建真实 LangGraph interrupt()（2026-08-26 判例）。

thread_id 语义：实例化取 application_id（一份投递一份清单）；条目更新取 item_id
（tasks 2.2 幂等键 {item_id}:effect_update_item:{to_status}:{request_id}）。
"""
from __future__ import annotations

import json
import sqlite3
import uuid

from app.storage.idempotency import idempotent_effect


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
