"""面试官名单维护（interviewer-availability spec「面试官记录来自 HR 维护的
名单」，tasks 1.7）。

⛔ 名单 MUST NOT 由 AI 生成或推荐：本模块只有 HR 手工维护的 CRUD，没有任何
模型调用，也没有任何按候选人/评分做推荐的分支。

幂等：同一 account_id 重复创建返回既有记录（design D7「能登记时段的账号＝
名单内账号」，account_id 有 UNIQUE 约束，账号与名单行一一对应）。
"""
from __future__ import annotations

import json
import sqlite3
import uuid


class InterviewerAccountMissing(ValueError):
    """account_id 在 hr_account 中不存在。"""


class InterviewerNotFound(ValueError):
    """interviewer_id 不存在。"""


_SELECT = (
    "SELECT i.id, i.account_id, i.name, i.department, i.interviewable_jobs, "
    "i.enabled, i.created_at, a.username "
    "FROM interviewer i JOIN hr_account a ON a.id = i.account_id"
)


def _row_to_dict(row) -> dict:
    (
        interviewer_id, account_id, name, department, interviewable_jobs,
        enabled, created_at, username,
    ) = row
    return {
        "id": interviewer_id,
        "account_id": account_id,
        "username": username,
        "name": name,
        "department": department,
        "interviewable_jobs": json.loads(interviewable_jobs or "[]"),
        "enabled": bool(enabled),
        "created_at": created_at,
    }


def list_interviewers(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(_SELECT + " ORDER BY i.name COLLATE NOCASE, i.id").fetchall()
    return [_row_to_dict(row) for row in rows]


def _find_by_account_id(conn: sqlite3.Connection, account_id: str) -> dict | None:
    row = conn.execute(_SELECT + " WHERE i.account_id = ?", (account_id,)).fetchone()
    return _row_to_dict(row) if row else None


def _find_by_id(conn: sqlite3.Connection, interviewer_id: str) -> dict | None:
    row = conn.execute(_SELECT + " WHERE i.id = ?", (interviewer_id,)).fetchone()
    return _row_to_dict(row) if row else None


def create_interviewer(
    conn: sqlite3.Connection,
    *,
    account_id: str,
    name: str,
    department: str | None,
    interviewable_jobs: list[str],
    enabled: bool,
) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("姓名不能为空")
    account_id = (account_id or "").strip()
    if not account_id:
        raise ValueError("account_id 不能为空")

    existing = _find_by_account_id(conn, account_id)
    if existing is not None:
        return existing  # 幂等：同 account_id 返回既有记录

    account = conn.execute(
        "SELECT id FROM hr_account WHERE id = ?", (account_id,)
    ).fetchone()
    if account is None:
        raise InterviewerAccountMissing(f"account_id={account_id} 不存在")

    interviewer_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO interviewer "
        "(id, account_id, name, department, interviewable_jobs, enabled) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            interviewer_id,
            account_id,
            name,
            department,
            json.dumps(interviewable_jobs, ensure_ascii=False),
            1 if enabled else 0,
        ),
    )
    conn.commit()
    created = _find_by_id(conn, interviewer_id)
    assert created is not None
    return created


def update_interviewer(
    conn: sqlite3.Connection,
    *,
    interviewer_id: str,
    updates: dict,
) -> dict:
    existing = _find_by_id(conn, interviewer_id)
    if existing is None:
        raise InterviewerNotFound(f"interviewer_id={interviewer_id} 不存在")

    # 列名只来自下面的固定白名单，⛔ 不来自用户输入，不存在注入面。
    set_clauses: list[str] = []
    params: list[object] = []

    if "name" in updates:
        name = (updates["name"] or "").strip()
        if not name:
            raise ValueError("姓名不能为空")
        set_clauses.append("name = ?")
        params.append(name)
    if "department" in updates:
        set_clauses.append("department = ?")
        params.append(updates["department"])
    if "interviewable_jobs" in updates:
        set_clauses.append("interviewable_jobs = ?")
        params.append(json.dumps(updates["interviewable_jobs"], ensure_ascii=False))
    if "enabled" in updates:
        set_clauses.append("enabled = ?")
        params.append(1 if updates["enabled"] else 0)

    if set_clauses:
        params.append(interviewer_id)
        conn.execute(
            f"UPDATE interviewer SET {', '.join(set_clauses)} WHERE id = ?",
            params,
        )
        conn.commit()

    return _find_by_id(conn, interviewer_id)
