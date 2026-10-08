"""候选人来源查询（channel-resume-intake U1 tasks 1.3）。

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
