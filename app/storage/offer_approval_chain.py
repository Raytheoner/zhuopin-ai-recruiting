"""Offer 审批链的岗位级配置读写（offer-generation U1 tasks 1.7，design D6）。

审批链是岗位级配置：job_id → 有序的 (level, approver_account_ids[]) 列表。
⛔ 审批链 MUST 由 HR 维护，MUST NOT 由 AI 生成或推荐审批人（offer-record-and-
approval spec「内部审批链」）。approver_account_ids 的元素必须是可识别账号
（hr_account.username）。

幂等语义：同内容重复 PUT 不产生新版本——put_approval_chain 先与既有内容比对，
完全一致则一行不写直接返回（updated_at 不动）。
"""
from __future__ import annotations

import json
import logging
import sqlite3

logger = logging.getLogger(__name__)


class UnknownApproverError(ValueError):
    """approver_account_ids 里出现不存在的账号名。⛔ 不静默降级、不猜默认值。"""


def _job_exists(conn: sqlite3.Connection, job_id: str) -> bool:
    return (
        conn.execute("SELECT 1 FROM job WHERE id = ?", (job_id,)).fetchone()
        is not None
    )


def get_approval_chain(conn: sqlite3.Connection, job_id: str) -> list[dict]:
    """返回该岗位的审批链（按 level 升序）。未配置时返回默认一级（业务经理占位）。"""
    rows = conn.execute(
        "SELECT level, approver_account_ids, updated_by, updated_at "
        "FROM offer_approval_chain WHERE job_id = ? ORDER BY level",
        (job_id,),
    ).fetchall()
    if not rows:
        # 默认一级＝业务经理（design D6「默认一级＝业务经理」）：审批人账号待
        # 人事部#3 回件（OQ2）回填，空列表表示"尚未指定具体审批人"。
        return [{"level": 1, "approver_account_ids": [], "default": True}]
    return [
        {
            "level": level,
            "approver_account_ids": json.loads(approver_account_ids),
            "updated_by": updated_by,
            "updated_at": updated_at,
        }
        for level, approver_account_ids, updated_by, updated_at in rows
    ]


def _validate_approvers(
    conn: sqlite3.Connection, approver_account_ids: list[str]
) -> None:
    if not approver_account_ids:
        return
    if not isinstance(approver_account_ids, list):
        raise ValueError("approver_account_ids 必须是数组")
    placeholders = ",".join("?" for _ in approver_account_ids)
    found = {
        row[0]
        for row in conn.execute(
            f"SELECT username FROM hr_account WHERE username IN ({placeholders})",
            tuple(approver_account_ids),
        ).fetchall()
    }
    missing = set(approver_account_ids) - found
    if missing:
        raise UnknownApproverError(f"审批人账号不存在: {sorted(missing)}")


def put_approval_chain(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    chain: list[dict],
    updated_by: str,
) -> None:
    """全量替换该岗位的审批链。同内容重复调用是幂等 no-op。"""
    if not _job_exists(conn, job_id):
        raise ValueError(f"job_id={job_id} 不存在")
    if not updated_by or not updated_by.strip():
        raise ValueError("updated_by 不能为空")
    if not isinstance(chain, list):
        raise ValueError("chain 必须是数组")

    normalized: list[tuple[int, list[str]]] = []
    for item in chain:
        level = item.get("level")
        approvers = item.get("approver_account_ids", [])
        if not isinstance(level, int) or level < 1:
            raise ValueError(f"level 必须是正整数: {level!r}")
        _validate_approvers(conn, approvers)
        normalized.append((level, sorted(set(approvers))))
    normalized.sort(key=lambda pair: pair[0])
    levels = [pair[0] for pair in normalized]
    if levels != list(range(1, len(levels) + 1)):
        raise ValueError("level 必须从 1 开始连续且不重复")

    existing = {
        row[0]: sorted(set(json.loads(row[1])))
        for row in conn.execute(
            "SELECT level, approver_account_ids FROM offer_approval_chain WHERE job_id = ?",
            (job_id,),
        ).fetchall()
    }
    new_map = {level: approvers for level, approvers in normalized}
    if existing == new_map:
        return  # 同内容重复 PUT：不产生新版本

    try:
        conn.execute("DELETE FROM offer_approval_chain WHERE job_id = ?", (job_id,))
        conn.executemany(
            "INSERT INTO offer_approval_chain "
            "(job_id, level, approver_account_ids, updated_by) VALUES (?, ?, ?, ?)",
            [
                (job_id, level, json.dumps(approvers, ensure_ascii=False), updated_by)
                for level, approvers in normalized
            ],
        )
    except Exception:
        # 共享单连接：半截写入必须回滚，否则会被之后一次不相关的 commit 悄悄落盘。
        try:
            conn.rollback()
        except Exception as rollback_exc:
            logger.error(
                "rollback failed while cleaning up after put_approval_chain "
                "raised for job_id=%s",
                job_id,
                exc_info=rollback_exc,
            )
        raise
    conn.commit()
