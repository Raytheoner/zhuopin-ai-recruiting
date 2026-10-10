"""来源查询：候选人来源 + 岗位来源分布（channel-resume-intake U1 tasks 1.3 /
U3 tasks 3.3）。

candidate.source 不做物理列：它恒等于该候选人最早一份简历的来源（design D2
「视图或触发式重算」）。本函数是唯一真源，任何要展示/统计候选人来源的调用方
都读它，⛔ 不要各自写一遍 JOIN。老库既有简历 source 为 NULL 视为 unknown。
"""
from __future__ import annotations

import sqlite3


def candidate_source(conn: sqlite3.Connection, candidate_id: str) -> str:
    row = conn.execute(
        "SELECT r.source FROM application a "
        "JOIN resume r ON r.id = a.resume_id "
        "WHERE a.candidate_id = ? "
        "ORDER BY r.uploaded_at ASC, r.id ASC LIMIT 1",
        (candidate_id,),
    ).fetchone()
    if row is None:
        return "unknown"
    return row[0] or "unknown"


def source_distribution(conn: sqlite3.Connection, job_id: str) -> dict[str, int]:
    """按来源统计某岗位的投递数（channel-resume-intake U3 tasks 3.3）。

    ⚠️ **只读**：本函数 ⛔ 不得出现任何 INSERT/UPDATE/DELETE/commit——它是报表
    的数据来源，是观测端不是写入端（与 app/audit/assertions.py 同一纪律）。

    数据源就是流转事实表：本包 ⛔ 不做漏斗报表页（spec「只记字段不做漏斗报表」
    + design D5），渠道分布完全由既有事实表算出。

    语义（本计划架构决策 9）：**每条投递恰好计一次**，取它**最新一条带 source 的
    流转事实**——来源改正追加的新事实会自然覆盖初始记录的值，所以"改正后分布跟着
    搬"。没有带 source 事实的投递计 unknown（老库既有行 source 为 NULL，与
    candidate_source() 同一口径）。

    ⛔ 不要改成对事实表 COUNT(*) GROUP BY source：那会把"一条投递的初始事实 +
    一条改正事实"算成两票，分布直接翻倍。
    """
    rows = conn.execute(
        "SELECT COALESCE(("
        "  SELECT h.source FROM application_stage_history h "
        "  WHERE h.application_id = a.id AND h.source IS NOT NULL "
        "  ORDER BY h.occurred_at DESC, h.rowid DESC LIMIT 1"
        "), 'unknown') AS source, COUNT(*) AS n "
        "FROM application a WHERE a.job_id = ? "
        "GROUP BY source ORDER BY source",
        (job_id,),
    ).fetchall()
    return {row[0]: row[1] for row in rows}
