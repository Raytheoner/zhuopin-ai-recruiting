"""G4·Mac 侧调度 tick（0917AA）：`python -m tools.liaison tick` 的行为断言。

裁决（Shao Peishen 2026-09-17 答 2a）：先做 Mac 侧调度。TD-11 口径：提醒是带幂等键
（含「第几次」）的 `effect_*`，⛔ 不用 sleep 循环／后台线程充数——tick 是**一次性进程**，
由 launchd `StartCalendarInterval` 按日历拉起（0917AE 起，此前是 `StartInterval` 300 秒），跑完即退。

0917AE 裁决（Shao Peishen 2026-09-17 12:5x）：事件驱动替代轮询。泳道结果由 `run-lanes.sh`
收敛时直接 `owner-notify --dedupe-key lanes-<STAMP>` 入队，tick **不再扫** `lanes-*/summary.txt`。

本文件钉死四件事：
- 两类扫描各自幂等：同一份输入跑两次，发件箱只多一行；
- 跟进信提醒只看「✅ 已推送」起头且交期列没写「不催」的行，第 2 天不提醒、第 3 天与第 7 天各一次；
- 观察窗到期日解析失败 ⇒ 告警跳过，⛔ 不拖死另一类扫描；
- tick **只入队**且**不轮询泳道**：源码里没有发送口、没有线程、没有 sleep、不读 handoff 目录，
  跑完 `sent_at` 全空。
"""

from __future__ import annotations

import ast
import datetime as dt
import pathlib
import re

import pytest

from tools.liaison import owner_notify, tick
from tools.liaison.storage import db as liaison_db
from tools.liaison.tests.test_liaison_effects import assert_effect_log_identity

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
LIAISON_ROOT = REPO_ROOT / "tools" / "liaison"

LEDGER_HEADER = (
    "## 清单\n\n"
    "| 编号 | 日期 | 收信人 | 主要事项 | 交期要点 | 发送状态 |\n"
    "|---|---|---|---|---|---|\n"
)


def ledger_row(number: str, *, status: str, deadline: str = "周五前回复") -> str:
    return f"| `{number}` | 2026-09-09 | 汤丽萍 | 事项 | {deadline} | {status} |\n"


def outbox_keys(conn) -> list[str]:
    return [r[0] for r in conn.execute("SELECT dedupe_key FROM owner_notify_outbox ORDER BY id")]


@pytest.fixture
def conn(tmp_path):
    c = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(c)
    yield c
    c.close()


@pytest.fixture
def world(tmp_path):
    """一套最小的仓库形状：跟进信台账、观察窗文档。全部在 tmp 里。"""
    ledger = tmp_path / "docs" / "跟进信" / "README-跟进信清单.md"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(LEDGER_HEADER, encoding="utf-8")
    obs = tmp_path / "docs" / "findings" / "2026-09-17-值守通道一周观察窗.md"
    obs.parent.mkdir(parents=True)
    obs.write_text(
        "# 值守通道一周观察窗（8.8）\n\n> 开窗：2026-09-17 12:29 CST ｜ 到期：2026-09-24\n",
        encoding="utf-8",
    )
    return tick.TickPaths(ledger_path=ledger, observation_path=obs)


def run(conn, world, *, today: dt.date) -> tick.TickReport:
    return tick.run_tick(conn, paths=world, today=today)


# ── ① 泳道结果：⛔ 不轮询（0917AE）────────────────────────────────────────────


def test_tick_does_not_poll_lane_batches():
    """泳道结果由 run-lanes.sh 收敛事件入队（0917Y），tick 不再扫 handoff 目录——
    看代码不看注释：剥掉 docstring 后源码里不该再有 handoff / lanes-* / summary.txt / lane_digest。"""
    tree = ast.parse((LIAISON_ROOT / "tick.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "tools.liaison.lane_digest" not in imported, "tick.py 不该再 import lane_digest（泳道结果不轮询）"
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and ast.get_docstring(node):
            node.body = node.body[1:]
    code = ast.unparse(tree)
    for banned in ("handoff", "lanes-", "summary.txt", "results.tsv", "lane_digest", "LANE_LOOKBACK"):
        assert banned not in code, f"tick.py 不该出现 {banned!r}（泳道结果由 run-lanes.sh 事件入队，不轮询）"
    assert not hasattr(tick, "scan_lane_batches")
    assert "handoff_root" not in tick.TickPaths.__dataclass_fields__


def test_tick_paths_have_exactly_the_two_time_event_sources():
    assert set(tick.TickPaths.__dataclass_fields__) == {"ledger_path", "observation_path"}


# ── ② 跟进信超期提醒 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (dt.date(2026, 9, 11), []),  # 第 2 天：不提醒
        (dt.date(2026, 9, 12), ["followup:人事部#2:1"]),  # 第 3 天：第 1 次
        (dt.date(2026, 9, 15), ["followup:人事部#2:1"]),  # 第 6 天：仍只有第 1 次
        (dt.date(2026, 9, 16), ["followup:人事部#2:1", "followup:人事部#2:2"]),  # 第 7 天：第 2 次
    ],
)
def test_followup_reminder_fires_on_day_3_and_day_7(conn, world, today, expected):
    world.ledger_path.write_text(
        LEDGER_HEADER + ledger_row("人事部#2", status="✅ 已推送 2026-09-09"), encoding="utf-8"
    )
    run(conn, world, today=today)
    assert outbox_keys(conn) == expected


def test_followup_reminder_is_idempotent_per_stage(conn, world):
    world.ledger_path.write_text(
        LEDGER_HEADER + ledger_row("人事部#2", status="✅ 已推送 2026-09-09"), encoding="utf-8"
    )
    run(conn, world, today=dt.date(2026, 9, 12))
    run(conn, world, today=dt.date(2026, 9, 13))
    assert outbox_keys(conn) == ["followup:人事部#2:1"]
    run(conn, world, today=dt.date(2026, 9, 16))
    run(conn, world, today=dt.date(2026, 9, 16))
    assert outbox_keys(conn) == ["followup:人事部#2:1", "followup:人事部#2:2"]


def test_followup_reminder_body_names_the_letter_and_the_days(conn, world):
    world.ledger_path.write_text(
        LEDGER_HEADER + ledger_row("人事部#2", status="✅ 已推送 2026-09-09"), encoding="utf-8"
    )
    run(conn, world, today=dt.date(2026, 9, 13))
    body = conn.execute("SELECT body FROM owner_notify_outbox").fetchone()[0]
    assert "人事部#2" in body and "已推送 4 天未回" in body


def test_followup_reminder_skips_rows_marked_do_not_chase(conn, world):
    world.ledger_path.write_text(
        LEDGER_HEADER
        + ledger_row("人事部#1", status="✅ 已推送 2026-09-09", deadline="决策点 a 无硬截止，不催"),
        encoding="utf-8",
    )
    run(conn, world, today=dt.date(2026, 9, 20))
    assert outbox_keys(conn) == []


@pytest.mark.parametrize(
    "status",
    [
        "🆕 待发",
        "⏸ 暂缓",
        "📥 已回件并回灌 2026-09-10",
        "📨 回件已到，待拆件 2026-09-10 14:00",
        "✅ 无需回复",
        "📨 已确认闭环 2026-09-10",
        "❌ 已作废",
    ],
)
def test_followup_reminder_only_looks_at_pushed_rows(conn, world, status):
    world.ledger_path.write_text(LEDGER_HEADER + ledger_row("人事部#3", status=status), encoding="utf-8")
    run(conn, world, today=dt.date(2026, 9, 30))
    assert [k for k in outbox_keys(conn) if k.startswith("followup:")] == []


def test_followup_reminder_without_a_push_date_is_a_warning_not_a_crash(conn, world):
    world.ledger_path.write_text(LEDGER_HEADER + ledger_row("人事部#4", status="✅ 已推送"), encoding="utf-8")
    report = run(conn, world, today=dt.date(2026, 9, 30))
    assert [k for k in outbox_keys(conn) if k.startswith("followup:")] == []
    assert any("人事部#4" in w for w in report.warnings)


def test_compute_followup_reminders_is_pure_and_documented_by_the_real_ledger():
    """真实台账里唯一一行写着「不催」——纯函数对它必须返回空，且不碰时钟。"""
    text = (REPO_ROOT / "docs" / "跟进信" / "README-跟进信清单.md").read_text(encoding="utf-8")
    assert tick.compute_followup_reminders(text, today=dt.date(2026, 12, 31)) == ([], [])


# ── ③ 观察窗到期 ──────────────────────────────────────────────────────────────


def test_observation_window_reminder_fires_once_on_or_after_the_due_date(conn, world):
    run(conn, world, today=dt.date(2026, 9, 23))
    assert outbox_keys(conn) == []
    run(conn, world, today=dt.date(2026, 9, 24))
    run(conn, world, today=dt.date(2026, 9, 24))
    run(conn, world, today=dt.date(2026, 9, 25))
    assert outbox_keys(conn) == ["observation:2026-09-17-值守通道一周观察窗.md:2026-09-24"]
    body = conn.execute("SELECT body FROM owner_notify_outbox").fetchone()[0]
    assert "2026-09-24" in body and "观察窗" in body


def test_observation_window_parse_failure_warns_and_does_not_block_other_scans(conn, world):
    world.observation_path.write_text("# 值守通道一周观察窗\n\n没有到期日这一行\n", encoding="utf-8")
    world.ledger_path.write_text(
        LEDGER_HEADER + ledger_row("人事部#2", status="✅ 已推送 2026-09-09"), encoding="utf-8"
    )
    report = run(conn, world, today=dt.date(2026, 9, 24))
    assert report.enqueued == ["followup:人事部#2:1", "followup:人事部#2:2"]
    assert any("到期" in w for w in report.warnings)


def test_observation_window_missing_file_is_a_warning(conn, world):
    world.observation_path.unlink()
    report = run(conn, world, today=dt.date(2026, 9, 24))
    assert outbox_keys(conn) == []
    assert report.warnings


def test_real_observation_doc_parses_to_the_documented_due_date():
    text = (REPO_ROOT / "docs" / "findings" / "2026-09-17-值守通道一周观察窗.md").read_text(encoding="utf-8")
    assert tick.compute_observation_due_date(text) == dt.date(2026, 9, 24)


# ── 横切：只入队、恒等式、CLI 接线 ──────────────────────────────────────────


def test_tick_never_opens_the_send_channel(conn, world):
    world.ledger_path.write_text(
        LEDGER_HEADER + ledger_row("人事部#2", status="✅ 已推送 2026-09-09"), encoding="utf-8"
    )
    run(conn, world, today=dt.date(2026, 9, 24))
    rows = conn.execute("SELECT sent_at, attempts FROM owner_notify_outbox").fetchall()
    assert len(rows) == 3 and all(r == (None, 0) for r in rows)
    assert_effect_log_identity(conn)


def test_tick_source_has_no_send_port_threads_or_sleep():
    """看代码不看注释：把 docstring 剥掉后再扫，注释里"⛔ 不 import aibot"这种话不算违规。"""
    tree = ast.parse((LIAISON_ROOT / "tick.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
            imported |= {f"{node.module}.{a.name}" for a in node.names}
    for banned in ("aibot", "threading", "asyncio", "time"):
        assert banned not in imported, f"tick.py 不该 import {banned!r}（tick 只入队、一次性进程）"
    assert "tools.liaison.owner_notify.SdkSendPort" not in imported
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and ast.get_docstring(node):
            node.body = node.body[1:]
    code = ast.unparse(tree)
    for banned in ("SdkSendPort", "send_markdown", ".drain(", "sleep("):
        assert banned not in code, f"tick.py 不该出现 {banned!r}（tick 只入队、一次性进程）"


def test_a_failing_scan_does_not_stop_the_others(conn, world, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("台账读不了")

    monkeypatch.setattr(tick, "scan_followup_reminders", boom)
    report = run(conn, world, today=dt.date(2026, 9, 24))
    assert report.enqueued == ["observation:2026-09-17-值守通道一周观察窗.md:2026-09-24"]
    assert any("台账读不了" in w for w in report.warnings)


def test_tick_main_runs_end_to_end_against_injected_paths(tmp_path, world, monkeypatch, capsys):
    monkeypatch.setattr(liaison_db, "DEFAULT_DB_PATH", tmp_path / "liaison.db")
    monkeypatch.setattr(tick, "default_paths", lambda: world)
    # 推送日固定在 2026-09-09：真实时钟只会往前走，满 3 天与满 7 天两次提醒恒成立。
    world.ledger_path.write_text(
        LEDGER_HEADER + ledger_row("人事部#2", status="✅ 已推送 2026-09-09"), encoding="utf-8"
    )
    assert tick.tick_main([]) == 0
    out = capsys.readouterr().out
    assert "followup:人事部#2:1" in out and "followup:人事部#2:2" in out
    assert tick.tick_main([]) == 0
    assert "入队 0，已存在 2" in capsys.readouterr().out
    conn = liaison_db.get_connection(tmp_path / "liaison.db")
    assert conn.execute("SELECT COUNT(*) FROM owner_notify_outbox").fetchone()[0] == 2
    conn.close()


def test_tick_main_rejects_unknown_arguments(tmp_path, world, monkeypatch):
    monkeypatch.setattr(liaison_db, "DEFAULT_DB_PATH", tmp_path / "liaison.db")
    monkeypatch.setattr(tick, "default_paths", lambda: world)
    assert tick.tick_main(["--send"]) == tick.EXIT_BAD_ARGS
    assert not (tmp_path / "liaison.db").exists()


def test_default_paths_point_at_the_repo_truth_sources():
    paths = tick.default_paths()
    assert paths.ledger_path == REPO_ROOT / "docs" / "跟进信" / "README-跟进信清单.md"
    assert paths.observation_path == REPO_ROOT / "docs" / "findings" / "2026-09-17-值守通道一周观察窗.md"


def test_main_dispatches_tick_before_credentials_are_loaded():
    """子命令必须短路在 main() 的 load_credentials() 之前（与 cleanup/owner-notify 同一纪律）。"""
    src = (LIAISON_ROOT / "__main__.py").read_text(encoding="utf-8")
    assert 'sys.argv[1] == "tick"' in src
    assert src.index('sys.argv[1] == "tick"') < src.index("sys.argv[1] == SELF_CHECK_ARG")
    assert re.search(r"tick_main\(sys\.argv\[2:\]\)", src)
