"""onboarding-flow U2 进度与逾期纯函数（tasks 2.3）：无 storage 写入、无消息发送。"""
from pathlib import Path

from app.agents.onboarding_progress import progress


def test_progress_percent_counts_required_only():
    items = [
        {"id": "i1", "status": "done", "due_offset_days": -3, "required": True},
        {"id": "i2", "status": "done", "due_offset_days": -3, "required": True},
        {"id": "i3", "status": "pending", "due_offset_days": -3, "required": True},
        {"id": "i4", "status": "done", "due_offset_days": 0, "required": False},
    ]
    result = progress(items, start_date="2026-10-20", today="2026-10-19")
    assert result["progress_percent"] == 67


def test_progress_zero_required_yields_zero():
    items = [{"id": "i1", "status": "done", "due_offset_days": 0, "required": False}]
    result = progress(items, start_date="2026-10-20", today="2026-10-19")
    assert result["progress_percent"] == 0


def test_overdue_marks_only_pending_past_due():
    items = [
        {"id": "overdue-pending", "status": "pending", "due_offset_days": -3, "required": True},
        {"id": "not-yet-due", "status": "pending", "due_offset_days": 5, "required": True},
        {"id": "done-past-due", "status": "done", "due_offset_days": -10, "required": True},
    ]
    result = progress(items, start_date="2026-10-20", today="2026-10-20")
    assert result["overdue_item_ids"] == ["overdue-pending"]


def test_overdue_negative_offset_before_start_date():
    """入职日前 3 天应完成、入职日前 1 天仍待办 ⇒ 逾期（spec Scenario）。"""
    items = [{"id": "contract", "status": "pending", "due_offset_days": -3, "required": True}]
    result = progress(items, start_date="2026-10-20", today="2026-10-19")
    assert result["overdue_item_ids"] == ["contract"]


def test_progress_module_has_no_storage_or_outbound_side_effects():
    src = Path("app/agents/onboarding_progress.py").read_text(encoding="utf-8")
    for forbidden in (
        "conn.execute", "sqlite3", "deliver_", "effect_", "requests.", "httpx", "smtplib",
        "app.storage", "app.outbound",
    ):
        assert forbidden not in src, f"progress 模块不应出现副作用调用: {forbidden}"
