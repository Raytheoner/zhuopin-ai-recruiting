"""onboarding-flow U2 进度与逾期纯函数（tasks 2.3）。

⛔ 无 storage 写入、无消息发送——本模块只 import 标准库，不做任何 IO
（tests/test_onboarding_progress.py 用源码 grep 反证）。

进度口径：必需条目里已完成/已豁免数 ÷ 必需条目数（非必需条目不进入分子，
避免"全勾完却 >100%"）。逾期口径：status='pending' 且 start_date + due_offset_days
< today（due_offset_days 可为负，负 = 入职日前）。
"""
from __future__ import annotations

from datetime import date, timedelta


def _iso_date(value) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def progress(items, start_date, today) -> dict:
    """计算进度百分比与逾期条目 id 列表。纯函数，无副作用。

    items 元素为 dict：{'id': str, 'status': 'pending'|'done'|'waived',
    'due_offset_days': int, 'required': bool}。
    返回 {'progress_percent': int, 'overdue_item_ids': list[str]}。
    """
    start = _iso_date(start_date)
    today_d = _iso_date(today)
    required = [it for it in items if it.get("required")]
    completed = [it for it in required if it.get("status") in ("done", "waived")]
    progress_percent = 0 if not required else round(100 * len(completed) / len(required))

    overdue_item_ids: list[str] = []
    for it in items:
        if it.get("status") != "pending":
            continue
        due = start + timedelta(days=int(it.get("due_offset_days", 0)))
        if due < today_d:
            overdue_item_ids.append(it["id"])
    return {"progress_percent": progress_percent, "overdue_item_ids": overdue_item_ids}
