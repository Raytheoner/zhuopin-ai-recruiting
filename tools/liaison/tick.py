"""Mac 侧调度 tick（0917AA，G4；0917AE 去轮询）：`python -m tools.liaison tick`。

**一次性进程**：由 launchd `StartCalendarInterval` 按日历拉起（`scripts/install_liaison_tick.py`，
每日 09:00、14:00 两个时点），跑一遍、退出。⛔ 不是 sleep 循环、⛔ 不开后台线程——TD-11 的口径是
"进程一重启就没了的东西不算调度"，所以调度权交给操作系统，本模块只负责"这一次该做什么"。

**只处理时间事件**（Shao Peishen 2026-09-17 裁决：事件驱动替代轮询），**全部只入队**
（写 `owner_notify_outbox`，由值守服务进程私信本人）：
1. **跟进信超期提醒**：台账里「✅ 已推送 <日期>」起头、交期列没写「不催」的行，
   推送满 3 天、满 7 天各提醒一次。幂等键＝`followup:<编号>:<第几次>`（TD-11：
   键里必须含"第几次"，否则调度器重跑会重复发）。⛔ 只提醒本人，⛔ 不给专员发。
2. **观察窗到期**：读观察窗文档的「到期：YYYY-MM-DD」，到期当天（或之后首次跑到）
   入队一次。幂等键＝`observation:<文件名>:<到期日>`。解析失败 ⇒ 记告警、跳过。

泳道结果 ⛔ 不在这里：`run-lanes.sh` 收敛时直接 `owner-notify --dedupe-key lanes-<STAMP>` 入队
（0917Y，事件驱动），本模块不再轮询 `.claude/handoff`。守护断言：
tests/test_tick.py::test_tick_does_not_poll_lane_batches。

两类扫描各自 try/except：一类炸了只记进 `TickReport.warnings`，⛔ 不拖死另一类。

分层（工程铁律 2 的形状）：`compute_*` 是纯函数（吃文本、吃 `today`，不碰文件与时钟），
`scan_*` 负责读文件并调 `effect_enqueue_owner_notify`，`run_tick` 串两段，`tick_main`
是唯一读真实时钟、真实路径的地方。

⛔ 本模块不 import aibot、不碰 `SdkSendPort`、不调 `drain`——发送是值守线程的事。
守护断言：tests/test_tick.py::test_tick_source_has_no_send_port_threads_or_sleep。
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import re
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from tools.liaison import logsetup
from tools.liaison.owner_notify import OWNER_NOTIFY_THREAD_ID
from tools.liaison.session import CHINA_TZ
from tools.liaison.storage import db as liaison_db
from tools.liaison.storage.effects import effect_enqueue_owner_notify

logger = logging.getLogger(f"{logsetup.PACKAGE_LOGGER_NAME}.tick")

EXIT_OK = 0
EXIT_BAD_ARGS = 2

#: tools/liaison/tick.py → parents[0]=liaison, [1]=tools, [2]=仓库根
REPO_ROOT = Path(__file__).resolve().parents[2]

#: 跟进信提醒的两个时点（自然日）。第 1 次＝满 3 天，第 2 次＝满 7 天。
#: 判据是 `days >= 阈值`（不是 `==`）：机器那天没开机也不该把这一次永远漏掉，
#: 幂等键含"第几次"保证补发也只补一次。
FOLLOWUP_REMINDER_DAYS: tuple[int, ...] = (3, 7)
#: 台账「发送状态」列的在途前缀（与 followup.py::_ALREADY_SENT / bridge.py::ALREADY_PUSHED_PREFIX
#: 同一枚举值的又一份拷贝——那两处各管各的语义，这里管"该不该催"，独立维护）。
PUSHED_PREFIX = "✅ 已推送"
#: 交期列写了它 ⇒ 这封信本来就不等回复，⛔ 不催。
DO_NOT_CHASE = "不催"

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_DUE_RE = re.compile(r"到期[：:]\s*(\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class TickPaths:
    """tick 读的两个真源。默认值见 `default_paths()`；单测整份替换成 tmp 路径。"""

    ledger_path: Path
    observation_path: Path


def default_paths() -> TickPaths:
    """仓库真源路径。独立成函数是为了让 `tick_main` 的单测能 monkeypatch 整份。"""
    return TickPaths(
        ledger_path=REPO_ROOT / "docs" / "跟进信" / "README-跟进信清单.md",
        observation_path=REPO_ROOT / "docs" / "findings" / "2026-09-17-值守通道一周观察窗.md",
    )


@dataclass(frozen=True)
class Reminder:
    """一条待入队的提醒：幂等键 ＋ 正文。两类扫描的公共输出形状。"""

    dedupe_key: str
    body: str


@dataclass
class TickReport:
    enqueued: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ── 纯函数 ────────────────────────────────────────────────────────────────────


def _split_table_row(line: str) -> list[str] | None:
    """`| a | b | c |` → ["a", "b", "c"]；不是表格行 ⇒ None。"""
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    cells = [c.strip() for c in stripped.strip("|").split("|")]
    return cells


def compute_followup_reminders(
    ledger_text: str, *, today: dt.date
) -> tuple[list[Reminder], list[str]]:
    """台账全文 ＋ 今天 → (待入队提醒, 告警)。**纯函数**。

    只认六列表格行；列序照抄台账表头：编号 ｜ 日期 ｜ 收信人 ｜ 主要事项 ｜ 交期要点 ｜ 发送状态。
    """
    reminders: list[Reminder] = []
    warnings: list[str] = []
    for line in ledger_text.splitlines():
        cells = _split_table_row(line)
        if cells is None or len(cells) < 6:
            continue
        number = cells[0].strip("`").strip()
        deadline, status = cells[4], cells[5]
        if not number or number == "编号" or set(number) <= {"-"}:
            continue
        if not status.startswith(PUSHED_PREFIX):
            continue
        if DO_NOT_CHASE in deadline:
            continue
        match = _DATE_RE.search(status[len(PUSHED_PREFIX):])
        if match is None:
            warnings.append(f"跟进信 {number} 状态「{status}」缺推送日期，无法判超期，跳过")
            continue
        try:
            pushed = dt.date.fromisoformat(match.group(1))
        except ValueError:
            warnings.append(f"跟进信 {number} 推送日期「{match.group(1)}」不是合法日期，跳过")
            continue
        days = (today - pushed).days
        for stage, threshold in enumerate(FOLLOWUP_REMINDER_DAYS, start=1):
            if days >= threshold:
                reminders.append(
                    Reminder(
                        dedupe_key=f"followup:{number}:{stage}",
                        body=(
                            f"⏰ 跟进信 {number} 已推送 {days} 天未回（{pushed.isoformat()} 推送，"
                            f"第 {stage} 次提醒）。台账：docs/跟进信/README-跟进信清单.md"
                        ),
                    )
                )
    return reminders, warnings


def compute_observation_due_date(doc_text: str) -> dt.date | None:
    """观察窗文档 → 到期日；找不到「到期：YYYY-MM-DD」或日期非法 ⇒ None。**纯函数**。"""
    match = _DUE_RE.search(doc_text)
    if match is None:
        return None
    try:
        return dt.date.fromisoformat(match.group(1))
    except ValueError:
        return None


def compute_observation_reminder(
    doc_text: str, *, file_name: str, today: dt.date
) -> Reminder | None:
    """到期当天（或之后）给一条；未到期 ⇒ None。解析失败由调用方按 None 判别后记告警。"""
    due = compute_observation_due_date(doc_text)
    if due is None or today < due:
        return None
    return Reminder(
        dedupe_key=f"observation:{file_name}:{due.isoformat()}",
        body=(
            f"📅 观察窗到期：{file_name}（到期 {due.isoformat()}）。"
            "请收观察结论、判 8.8 是否可勾，并触发 hr-wecom-aibot-liaison 归档流程。"
        ),
    )


# ── 扫描（读文件 ＋ 入队）──────────────────────────────────────────────────────


def _enqueue(conn: sqlite3.Connection, reminder: Reminder, report: TickReport) -> None:
    result = effect_enqueue_owner_notify(
        conn,
        thread_id=OWNER_NOTIFY_THREAD_ID,
        business_key=reminder.dedupe_key,
        body=reminder.body,
    )
    if result is None:
        report.skipped.append(reminder.dedupe_key)
    else:
        report.enqueued.append(reminder.dedupe_key)


def scan_followup_reminders(
    conn: sqlite3.Connection, ledger_path: Path, *, today: dt.date, report: TickReport
) -> None:
    if not ledger_path.is_file():
        report.warnings.append(f"跟进信台账不存在：{ledger_path}")
        return
    reminders, warnings = compute_followup_reminders(
        ledger_path.read_text(encoding="utf-8"), today=today
    )
    report.warnings.extend(warnings)
    for reminder in reminders:
        _enqueue(conn, reminder, report)


def scan_observation_window(
    conn: sqlite3.Connection, observation_path: Path, *, today: dt.date, report: TickReport
) -> None:
    if not observation_path.is_file():
        report.warnings.append(f"观察窗文档不存在：{observation_path}")
        return
    text = observation_path.read_text(encoding="utf-8")
    if compute_observation_due_date(text) is None:
        report.warnings.append(f"观察窗文档解析不出「到期：YYYY-MM-DD」，跳过：{observation_path.name}")
        return
    reminder = compute_observation_reminder(text, file_name=observation_path.name, today=today)
    if reminder is not None:
        _enqueue(conn, reminder, report)


def run_tick(conn: sqlite3.Connection, *, paths: TickPaths, today: dt.date) -> TickReport:
    """跑一遍两类扫描。一类抛异常只记告警，另一类照跑。"""
    report = TickReport()
    steps = (
        ("跟进信超期", lambda: scan_followup_reminders(conn, paths.ledger_path, today=today, report=report)),
        ("观察窗到期", lambda: scan_observation_window(conn, paths.observation_path, today=today, report=report)),
    )
    for name, step in steps:
        try:
            step()
        except Exception as exc:  # noqa: BLE001 —— 见 docstring：一类失败不拖死另一类
            logger.error("tick 扫描「%s」失败", name, exc_info=True)
            report.warnings.append(f"{name}扫描失败：{exc}")
    return report


def render_report(report: TickReport) -> str:
    lines = [f"tick：入队 {len(report.enqueued)}，已存在 {len(report.skipped)}，告警 {len(report.warnings)}"]
    lines += [f"  + {k}" for k in report.enqueued]
    lines += [f"  ⚠️ {w}" for w in report.warnings]
    return "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="python -m tools.liaison tick",
        description="一次性调度 tick：扫跟进信超期／观察窗到期两类时间事件，只入队，跑完即退。无参数。",
    )


def tick_main(argv: Sequence[str]) -> int:
    """`python -m tools.liaison tick` 的实现。**这是本模块唯一读真实时钟与真实路径的地方。**

    `argv` 没有默认值（与 `retention.cleanup_main` 同一理由）。库路径取
    `liaison_db.DEFAULT_DB_PATH` 的属性访问、真源路径取 `default_paths()`，单测都能替换。
    """
    parser = _build_parser()
    try:
        parser.parse_args(list(argv))
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else EXIT_BAD_ARGS

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    conn = liaison_db.get_connection()
    try:
        liaison_db.init_schema(conn)
        # 「自然日」按中国日历算（台账里的日期全是 CST），⛔ 不用本机时区——本机在 EDT，
        # 与中国差 12 小时，照本机日期算会集体差一天且不报错（CLAUDE.md「MMDD 必须实跑」同源）。
        report = run_tick(conn, paths=default_paths(), today=dt.datetime.now(CHINA_TZ).date())
    finally:
        conn.close()
    print(render_report(report))
    return EXIT_OK
