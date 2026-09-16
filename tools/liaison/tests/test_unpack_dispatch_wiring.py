"""`bridge_dispatch`：把 Task 4 的 `dispatch_headless_unpack` 接成 P0
`bridge.run_bridge(..., dispatch=...)` 的注入点，并把结果转成三态审计
（design D11：`dispatch_started` / `dispatch_skipped_busy` / `dispatch_failed`）。

⚠️ 本文件的用例全部注入 fake `dispatch_headless_unpack`，⛔ 不真实起进程
（起进程的行为已经在 test_unpack_dispatch.py 里覆盖过，这里只测「结果 → 审计」
这一段转换）。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tools.liaison.storage import db as liaison_db
from tools.liaison.storage.effects import EFFECT_NODE_TO_TABLE
from tools.liaison.unpack.dispatch import DispatchOutcome
from tools.liaison.unpack.dispatch_wiring import bridge_dispatch

NOW = datetime(2026, 9, 10, 14, 3, 0, tzinfo=timezone.utc)


@pytest.fixture()
def conn(tmp_path):
    connection = liaison_db.get_connection(tmp_path / "test.db")
    liaison_db.init_schema(connection)
    yield connection
    connection.close()


def _audit_rows(conn, msgid: str) -> list[tuple]:
    return conn.execute(
        "SELECT kind FROM liaison_unpack_audit WHERE msgid = ? ORDER BY id", (msgid,)
    ).fetchall()


def test_started_outcome_writes_dispatch_started_audit(conn, monkeypatch, tmp_path):
    charter_path = tmp_path / "charter.md"
    charter_path.write_text("章程全文", encoding="utf-8")
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch_wiring.dispatch_headless_unpack",
        lambda **kwargs: DispatchOutcome(status="started", pid=1, log_path="x.log"),
    )
    outcome = bridge_dispatch(
        conn,
        thread_id="t1",
        msgid="m1",
        sender_userid="u1",
        letter_number="人事部#1",
        charter_relpath=str(charter_path.relative_to(charter_path.parents[0])),
        charter_root=charter_path.parent,
        now=NOW,
    )
    assert outcome.status == "started"
    rows = _audit_rows(conn, "m1")
    assert rows == [("dispatch_started",)]


def test_skipped_busy_outcome_writes_dispatch_skipped_busy_audit(conn, monkeypatch, tmp_path):
    charter_path = tmp_path / "charter.md"
    charter_path.write_text("章程全文", encoding="utf-8")
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch_wiring.dispatch_headless_unpack",
        lambda **kwargs: DispatchOutcome(status="skipped_busy"),
    )
    bridge_dispatch(
        conn, thread_id="t1", msgid="m2", sender_userid="u1", letter_number="人事部#1",
        charter_relpath=charter_path.name, charter_root=charter_path.parent, now=NOW,
    )
    assert _audit_rows(conn, "m2") == [("dispatch_skipped_busy",)]


def test_failed_outcome_writes_dispatch_failed_audit_with_reason(conn, monkeypatch, tmp_path):
    charter_path = tmp_path / "charter.md"
    charter_path.write_text("章程全文", encoding="utf-8")
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch_wiring.dispatch_headless_unpack",
        lambda **kwargs: DispatchOutcome(status="failed", reason="binary_not_found"),
    )
    bridge_dispatch(
        conn, thread_id="t1", msgid="m3", sender_userid="u1", letter_number="人事部#1",
        charter_relpath=charter_path.name, charter_root=charter_path.parent, now=NOW,
    )
    assert _audit_rows(conn, "m3") == [("dispatch_failed",)]


def test_charter_missing_still_produces_one_audit_row(conn, monkeypatch, tmp_path):
    """章程文件真的不存在（本 Task 自己解析，⛔ 不 import P2 的 charter.py）
    ⇒ `dispatch_headless_unpack` 收到 `charter_text=None` ⇒ `failed`。"""
    outcome = bridge_dispatch(
        conn, thread_id="t1", msgid="m4", sender_userid="u1", letter_number="人事部#1",
        charter_relpath="不存在.md", charter_root=tmp_path, now=NOW,
    )
    assert outcome.status == "failed"
    assert outcome.reason == "charter_missing"
    assert _audit_rows(conn, "m4") == [("dispatch_failed",)]


def test_same_msgid_dispatch_twice_only_one_started_audit_row(conn, monkeypatch, tmp_path):
    """幂等策略：同 msgid 同 kind 只落一行（design D11 / 铁律1）。"""
    charter_path = tmp_path / "charter.md"
    charter_path.write_text("章程全文", encoding="utf-8")
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch_wiring.dispatch_headless_unpack",
        lambda **kwargs: DispatchOutcome(status="started", pid=1, log_path="x.log"),
    )
    kwargs = dict(
        conn=conn, thread_id="t1", msgid="m5", sender_userid="u1", letter_number="人事部#1",
        charter_relpath=charter_path.name, charter_root=charter_path.parent, now=NOW,
    )
    bridge_dispatch(**kwargs)
    bridge_dispatch(**kwargs)
    assert _audit_rows(conn, "m5") == [("dispatch_started",)]


def test_audit_write_failure_is_swallowed_and_outcome_still_returned(conn, monkeypatch, tmp_path, caplog):
    """审计写失败本身吞掉只记日志（spec「审计写入自身失败时也 MUST 被吞掉」）。"""
    charter_path = tmp_path / "charter.md"
    charter_path.write_text("章程全文", encoding="utf-8")
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch_wiring.dispatch_headless_unpack",
        lambda **kwargs: DispatchOutcome(status="started", pid=1, log_path="x.log"),
    )

    def _raising_effect(*args, **kwargs):
        raise RuntimeError("磁盘满")

    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch_wiring.effects.effect_unpack_audit", _raising_effect
    )
    outcome = bridge_dispatch(
        conn, thread_id="t1", msgid="m6", sender_userid="u1", letter_number="人事部#1",
        charter_relpath=charter_path.name, charter_root=charter_path.parent, now=NOW,
    )
    assert outcome.status == "started"  # dispatch 本身的结果不受审计失败影响
