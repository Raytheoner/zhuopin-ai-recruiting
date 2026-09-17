"""Mac 侧调度 tick（0917AA，G4）：`python -m tools.liaison tick`。

**一次性进程**：由 launchd `StartInterval`（`scripts/install_liaison_tick.py`，300 秒）拉起，
跑一遍、退出。⛔ 不是 sleep 循环、⛔ 不开后台线程——TD-11 的口径是"进程一重启就没了的
东西不算调度"，所以调度权交给操作系统，本模块只负责"这一次该做什么"。

每次做三件事，**全部只入队**（写 `owner_notify_outbox`，由值守服务进程私信本人）：
1. **泳道结果**：`.claude/handoff/lanes-*/summary.txt` 存在 ⇒ 批次已收敛 ⇒
   `compute_lane_digest` ⇒ 入队。只看回看窗内（`LANE_LOOKBACK_DAYS`）的批次，
   首次安装不会把几十个历史批次一口气私信出去。幂等键＝批次目录名（与 `run-lanes.sh` 收敛时自己那条
   `owner-notify --dedupe-key lanes-<STAMP>` **同一把键**，两条路不会各发一次）。
2. **跟进信超期提醒**：台账里「✅ 已推送 <日期>」起头、交期列没写「不催」的行，
   推送满 3 天、满 7 天各提醒一次。幂等键＝`followup:<编号>:<第几次>`（TD-11：
   键里必须含"第几次"，否则调度器重跑会重复发）。⛔ 只提醒本人，⛔ 不给专员发。
3. **观察窗到期**：读观察窗文档的「到期：YYYY-MM-DD」，到期当天（或之后首次跑到）
   入队一次。幂等键＝`observation:<文件名>:<到期日>`。解析失败 ⇒ 记告警、跳过。

三类扫描各自 try/except：一类炸了只记进 `TickReport.warnings`，⛔ 不拖死另外两类。

分层（工程铁律 2 的形状）：`compute_*` 是纯函数（吃文本、吃 `today`，不碰文件与时钟），
`scan_*` 负责读文件并调 `effect_enqueue_owner_notify`，`run_tick` 串三段，`tick_main`
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
from tools.liaison.lane_digest import compute_lane_digest
from tools.liaison.owner_notify import OWNER_NOTIFY_THREAD_ID
from tools.liaison.session import CHINA_TZ
from tools.liaison.storage import db as liaison_db
from tools.liaison.storage.effects import effect_enqueue_owner_notify

logger = logging.getLogger(f"{logsetup.PACKAGE_LOGGER_NAME}.tick")

EXIT_OK = 0
EXIT_BAD_ARGS = 2

#: tools/liaison/tick.py → parents[0]=liaison, [1]=tools, [2]=仓库根
REPO_ROOT = Path(__file__).resolve().parents[2]

#: run-lanes.sh 的批次目录名形状（`LOGDIR="$REPO/.claude/handoff/lanes-$STAMP"`）。
#: 同目录下还有 `launch/`、服务器取证 `.log` 等，⛔ 不能拿"是个目录"当判据。
LANE_BATCH_GLOB = "lanes-*"
#: 批次收敛的判据文件：run-lanes.sh 只在末尾 `tee "$LOGDIR/summary.txt"`，跑到一半没有它。
LANE_SUMMARY_NAME = "summary.txt"
LANE_RESULTS_NAME = "results.tsv"
#: 批次目录名里的时间戳：`lanes-YYYYMMDD-HHMMSS`。对不上形状的目录不是 run-lanes 写的，跳过。
_LANE_STAMP_RE = re.compile(r"^lanes-(\d{8})-\d{6}$")
#: 只看最近这几天收敛的批次（按目录名日期，含今天）。首次装 tick 时 handoff 下已有
#: 几十个历史批次（2026-09-17 实数 37 个带 summary.txt），不设回看窗会一口气私信几十条。
#: 历史批次不入队也不记告警——它们早已在看护报告里收过口。
LANE_LOOKBACK_DAYS = 1

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
    """tick 读的三个真源。默认值见 `default_paths()`；单测整份替换成 tmp 路径。"""

    handoff_root: Path
    ledger_path: Path
    observation_path: Path


def default_paths() -> TickPaths:
    """仓库真源路径。独立成函数是为了让 `tick_main` 的单测能 monkeypatch 整份。"""
    return TickPaths(
        handoff_root=REPO_ROOT / ".claude" / "handoff",
        ledger_path=REPO_ROOT / "docs" / "跟进信" / "README-跟进信清单.md",
        observation_path=REPO_ROOT / "docs" / "findings" / "2026-09-17-值守通道一周观察窗.md",
    )


@dataclass(frozen=True)
class Reminder:
    """一条待入队的提醒：幂等键 ＋ 正文。三类扫描的公共输出形状。"""

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


def compute_lane_batch_date(dir_name: str) -> dt.date | None:
    """`lanes-20260917-101500` → 2026-09-17；形状不对或日期非法 ⇒ None。**纯函数**。"""
    match = _LANE_STAMP_RE.match(dir_name)
    if match is None:
        return None
    try:
        return dt.datetime.strptime(match.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def compute_lane_batch_in_window(dir_name: str, *, today: dt.date) -> bool:
    """批次是否落在回看窗内（今天往前 LANE_LOOKBACK_DAYS 天，含今天）。**纯函数**。"""
    stamp = compute_lane_batch_date(dir_name)
    return stamp is not None and 0 <= (today - stamp).days <= LANE_LOOKBACK_DAYS


def scan_lane_batches(
    conn: sqlite3.Connection, handoff_root: Path, *, today: dt.date, report: TickReport
) -> None:
    """回看窗内、已收敛且尚未通知过的批次 ⇒ 入队。"已通知过"由 effect 的幂等键说了算。"""
    if not handoff_root.is_dir():
        return
    for batch in sorted(handoff_root.glob(LANE_BATCH_GLOB)):
        if not batch.is_dir() or not (batch / LANE_SUMMARY_NAME).is_file():
            continue
        if not compute_lane_batch_in_window(batch.name, today=today):
            continue
        # 先查一眼发件箱，省得每 5 分钟把历史批次的 results.tsv 全部重读一遍。
        # 这只是省 IO，⛔ 不是幂等防线——防线是下面 effect 的 effect_log 键。
        exists = conn.execute(
            "SELECT 1 FROM owner_notify_outbox WHERE dedupe_key = ?", (batch.name,)
        ).fetchone()
        if exists is not None:
            report.skipped.append(batch.name)
            continue
        results = batch / LANE_RESULTS_NAME
        body = compute_lane_digest(
            results.read_text(encoding="utf-8") if results.is_file() else "",
            (batch / LANE_SUMMARY_NAME).read_text(encoding="utf-8"),
            batch_label=batch.name,
        )
        if not body.strip():
            report.warnings.append(f"批次 {batch.name} 摘要为空，跳过")
            continue
        _enqueue(conn, Reminder(dedupe_key=batch.name, body=body), report)


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
    """跑一遍三类扫描。一类抛异常只记告警，其余照跑。"""
    report = TickReport()
    steps = (
        ("泳道结果", lambda: scan_lane_batches(conn, paths.handoff_root, today=today, report=report)),
        ("跟进信超期", lambda: scan_followup_reminders(conn, paths.ledger_path, today=today, report=report)),
        ("观察窗到期", lambda: scan_observation_window(conn, paths.observation_path, today=today, report=report)),
    )
    for name, step in steps:
        try:
            step()
        except Exception as exc:  # noqa: BLE001 —— 见 docstring：一类失败不拖死其余两类
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
        description="一次性调度 tick：扫泳道结果／跟进信超期／观察窗到期，只入队，跑完即退。无参数。",
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
