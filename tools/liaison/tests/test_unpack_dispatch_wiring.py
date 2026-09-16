"""`bridge_dispatch`：把 Task 4 的 `dispatch_headless_unpack` 接成 P0
`bridge.run_bridge(..., dispatch=...)` 的注入点，并把结果转成三态审计
（design D11：`dispatch_started` / `dispatch_skipped_busy` / `dispatch_failed`）。

⚠️ 本文件的用例全部注入 fake `dispatch_headless_unpack`，⛔ 不真实起进程
（起进程的行为已经在 test_unpack_dispatch.py 里覆盖过，这里只测「结果 → 审计」
这一段转换）。

P2（`liaison-unpack-charter`）接入生产路径后，章程正文与 prompt 拼接改由
`unpack/charter.py` 负责——本文件的用例改注入 `repo_root`（章程放在
`<repo_root>/.claude/skills/liaison-unpack/SKILL.md`），不再自己拼一份
`charter_relpath`/`charter_root`。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tools.liaison.storage import db as liaison_db
from tools.liaison.storage.effects import EFFECT_NODE_TO_TABLE
from tools.liaison.unpack import dispatch_wiring, unpack_cli
from tools.liaison.unpack.dispatch import DispatchOutcome
from tools.liaison.unpack.dispatch_wiring import bridge_dispatch
from tools.liaison.unpack.signal import append_signal

NOW = datetime(2026, 9, 10, 14, 3, 0, tzinfo=timezone.utc)


@pytest.fixture()
def conn(tmp_path):
    connection = liaison_db.get_connection(tmp_path / "test.db")
    liaison_db.init_schema(connection)
    yield connection
    connection.close()


def _write_charter(repo_root, text: str = "章程全文") -> None:
    charter_dir = repo_root / ".claude" / "skills" / "liaison-unpack"
    charter_dir.mkdir(parents=True, exist_ok=True)
    (charter_dir / "SKILL.md").write_text(text, encoding="utf-8")


def _audit_rows(conn, msgid: str) -> list[tuple]:
    return conn.execute(
        "SELECT kind FROM liaison_unpack_audit WHERE msgid = ? ORDER BY id", (msgid,)
    ).fetchall()


def test_started_outcome_writes_dispatch_started_audit(conn, monkeypatch, tmp_path):
    _write_charter(tmp_path)
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
        now=NOW,
        repo_root=tmp_path,
    )
    assert outcome.status == "started"
    rows = _audit_rows(conn, "m1")
    assert rows == [("dispatch_started",)]


def test_skipped_busy_outcome_writes_dispatch_skipped_busy_audit(conn, monkeypatch, tmp_path):
    _write_charter(tmp_path)
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch_wiring.dispatch_headless_unpack",
        lambda **kwargs: DispatchOutcome(status="skipped_busy"),
    )
    bridge_dispatch(
        conn, thread_id="t1", msgid="m2", sender_userid="u1", letter_number="人事部#1",
        now=NOW, repo_root=tmp_path,
    )
    assert _audit_rows(conn, "m2") == [("dispatch_skipped_busy",)]


def test_failed_outcome_writes_dispatch_failed_audit_with_reason(conn, monkeypatch, tmp_path):
    _write_charter(tmp_path)
    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch_wiring.dispatch_headless_unpack",
        lambda **kwargs: DispatchOutcome(status="failed", reason="binary_not_found"),
    )
    bridge_dispatch(
        conn, thread_id="t1", msgid="m3", sender_userid="u1", letter_number="人事部#1",
        now=NOW, repo_root=tmp_path,
    )
    assert _audit_rows(conn, "m3") == [("dispatch_failed",)]


def test_charter_missing_still_produces_one_audit_row(conn, monkeypatch, tmp_path):
    """`repo_root` 下没有 `.claude/skills/liaison-unpack/SKILL.md`
    ⇒ `charter.read_charter` 抛 `CharterMissing` ⇒ `dispatch_headless_unpack`
    收到 `charter_text=None` ⇒ `failed(charter_missing)`。"""
    outcome = bridge_dispatch(
        conn, thread_id="t1", msgid="m4", sender_userid="u1", letter_number="人事部#1",
        now=NOW, repo_root=tmp_path,
    )
    assert outcome.status == "failed"
    assert outcome.reason == "charter_missing"
    assert _audit_rows(conn, "m4") == [("dispatch_failed",)]


def test_same_msgid_dispatch_twice_only_one_started_audit_row(conn, monkeypatch, tmp_path):
    """幂等策略：同 msgid 只真的起一次拆件会话（I2 修复）。

    🔴 这条用例此前用 `lambda **kwargs: DispatchOutcome(...)` 接住
    `dispatch_headless_unpack`——lambda 对调用次数结构性失明，即便 P1 两次都真的
    调用了它（=两个真实、billable 的 Claude 会话被起了两次），这条用例照样会绿。
    现在换成会计数的 fake，直接断言「起活」这个动作本身只发生一次，不是只断言
    审计表的行数（那一层的幂等短路发生在起活**之后**，挡不住第二次真的起进程，
    见 finding I2）。
    """
    _write_charter(tmp_path)
    launch_calls: list[dict] = []

    def _counting_dispatch_headless_unpack(**kwargs):
        launch_calls.append(kwargs)
        return DispatchOutcome(status="started", pid=1, log_path="x.log")

    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch_wiring.dispatch_headless_unpack",
        _counting_dispatch_headless_unpack,
    )
    kwargs = dict(
        conn=conn, thread_id="t1", msgid="m5", sender_userid="u1", letter_number="人事部#1",
        now=NOW, repo_root=tmp_path,
    )
    first = bridge_dispatch(**kwargs)
    second = bridge_dispatch(**kwargs)
    assert len(launch_calls) == 1, "第二次同 msgid 调用 ⛔ 不许真的再起一次拆件会话"
    assert first.status == "started"
    assert second.status == "started"
    assert second.reason == "idempotent_replay_skipped_relaunch"
    assert _audit_rows(conn, "m5") == [("dispatch_started",)]


def test_prior_launch_probe_ignores_non_started_kinds(conn, monkeypatch, tmp_path):
    """I2 的探测只认 `dispatch_started`：`skipped_busy`/`dispatch_failed` 都没有
    真的花钱起活，第二次调用允许重试（不该被误拦）。"""
    _write_charter(tmp_path)
    launch_calls: list[dict] = []

    def _counting_dispatch_headless_unpack(**kwargs):
        launch_calls.append(kwargs)
        return DispatchOutcome(status="skipped_busy")

    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch_wiring.dispatch_headless_unpack",
        _counting_dispatch_headless_unpack,
    )
    kwargs = dict(
        conn=conn, thread_id="t1", msgid="m7", sender_userid="u1", letter_number="人事部#1",
        now=NOW, repo_root=tmp_path,
    )
    bridge_dispatch(**kwargs)
    bridge_dispatch(**kwargs)
    assert len(launch_calls) == 2, "非 dispatch_started 的结果不该拦下重试"


def test_signal_root_matches_unpack_cli_data_root():
    """I3：`dispatch_wiring.py`／`unpack_cli.py` 两份独立维护的路径常量必须
    REPO_ROOT 锚定且逐字相等——⛔ 不许再退回裸 `Path("data/liaison")`（部署约束是
    Windows 计划任务，cwd 不保证是仓库根，裸相对路径会静默落到别处）。"""
    assert dispatch_wiring.REPO_ROOT == unpack_cli.REPO_ROOT
    assert dispatch_wiring.DEFAULT_SIGNAL_ROOT == unpack_cli._DATA_ROOT
    assert dispatch_wiring.DEFAULT_LOG_DIR == unpack_cli.DEFAULT_LOG_DIR
    assert dispatch_wiring.DEFAULT_LOCK_PATH == unpack_cli.DEFAULT_LOCK_PATH
    assert dispatch_wiring.DEFAULT_SIGNAL_ROOT.is_absolute()
    assert unpack_cli._DATA_ROOT.is_absolute()


def test_audit_write_failure_is_swallowed_and_outcome_still_returned(conn, monkeypatch, tmp_path, caplog):
    """审计写失败本身吞掉只记日志（spec「审计写入自身失败时也 MUST 被吞掉」）。"""
    _write_charter(tmp_path)
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
        now=NOW, repo_root=tmp_path,
    )
    assert outcome.status == "started"  # dispatch 本身的结果不受审计失败影响


def test_prompt_is_built_from_charter_module_with_full_fields(conn, monkeypatch, tmp_path):
    """P2 接入后，`bridge_dispatch` 传给 `dispatch_headless_unpack` 的 `prompt`
    须由 `charter.compute_prompt` 产出：以「# 拆件会话起活」开头、含 msgid／
    信号文件相对路径／检查点时刻／（从信号文件按 msgid 查到的）信件编号，
    并以章程全文逐字结尾。"""
    charter_text = "章程正文占位\n"
    _write_charter(tmp_path, charter_text)

    signal_path = tmp_path / "unpack-signal.json"
    monkeypatch.setattr(unpack_cli, "DEFAULT_SIGNAL_PATH", signal_path)
    monkeypatch.delenv(unpack_cli.SIGNAL_PATH_ENV, raising=False)
    append_signal(
        signal_path,
        {
            "letter_number": "人事部#7",
            "msgid": "m9",
            "archived_relpath": "x",
            "at": "2026-09-10T14:00:00.000000+08:00",
        },
    )

    captured: dict = {}

    def _capturing(**kwargs):
        captured.update(kwargs)
        return DispatchOutcome(status="started", pid=1, log_path="x.log")

    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch_wiring.dispatch_headless_unpack", _capturing
    )

    bridge_dispatch(
        conn, thread_id="t1", msgid="m9", sender_userid="u1", letter_number=None,
        now=NOW, repo_root=tmp_path,
    )

    prompt = captured["prompt"]
    assert prompt.startswith("# 拆件会话起活")
    assert "m9" in prompt
    assert "unpack-signal.json" in prompt
    assert NOW.isoformat() in prompt
    assert "人事部#7" in prompt
    assert prompt.endswith(charter_text)


def test_prompt_letter_number_falls_back_when_signal_has_no_matching_entry(
    conn, monkeypatch, tmp_path
):
    """`letter_number` 入参为 `None` 且信号文件里也查不到该 msgid ⇒ 写占位
    「（未匹配）」，⛔ 不抛异常、⛔ 不阻断起活。"""
    charter_text = "章程正文占位\n"
    _write_charter(tmp_path, charter_text)

    signal_path = tmp_path / "unpack-signal.json"
    monkeypatch.setattr(unpack_cli, "DEFAULT_SIGNAL_PATH", signal_path)
    monkeypatch.delenv(unpack_cli.SIGNAL_PATH_ENV, raising=False)

    captured: dict = {}

    def _capturing(**kwargs):
        captured.update(kwargs)
        return DispatchOutcome(status="started", pid=1, log_path="x.log")

    monkeypatch.setattr(
        "tools.liaison.unpack.dispatch_wiring.dispatch_headless_unpack", _capturing
    )

    outcome = bridge_dispatch(
        conn, thread_id="t1", msgid="m10", sender_userid="u1", letter_number=None,
        now=NOW, repo_root=tmp_path,
    )

    assert outcome.status == "started"
    assert "（未匹配）" in captured["prompt"]
