"""留存期清理（tasks 8.1–8.2、design D13、spec liaison-message-archive
「归档数据的留存期有上限」）。

**本模块分三层，⛔ 不许混在一起：**

- `compute_*` 是纯函数：不读时钟（`now` 由参数注入）、不读环境变量、不碰
  文件系统、不碰数据库。同一份输入在任何时刻必须给出同一个答案——否则
  "重复执行安全"这句话没有任何依据。
- `effect_delete_expired_message` 是**唯一**的库副作用，挂 `@idempotent_effect`
  （工程铁律 1），台账行与 `effect_log` 行在同一个事务里提交。
- `delete_archive_files` / `prune_empty_dirs` / `emit_retention_alert` 是
  文件系统与告警通道的副作用，它们**不在事务里**（文件系统不参与 SQL 事务，
  硬凑只会让一次 I/O 失败把事务处理路径也拖进来——design D3）。

🔴 **三条不许碰的东西**（opener 约束 2）：
1. ⛔ `liaison_task` 行**任何状态、任何情况都不删**。它是工作台账。
2. ⛔ `effect_log` 行一行不删。它是"这条材料被清理过"的唯一审计证据。
3. ⛔ 不加表、不加列——`storage/schema.py` 由并行泳道持有。

🔴 **顺序：先删台账行、后删归档文件。** 反过来会在两步之间留下"台账指向
不存在的文件"，那正是 design D3 明令禁止的中间态。文件那一遍**不靠台账
指路**（走"扫目录 + 排除仍被引用的路径"），因此它对上一遍的失败是自愈的。

⛔ 本模块不许写 `with <连接或调用>:`——`tests/test_liaison_effects.py` 的事务
扫描器会把它判成隐式提交（白名单只有 `open` / `os.fdopen` / `io.open` /
`contextlib.suppress` / `tempfile.NamedTemporaryFile` / `tempfile.TemporaryDirectory`）。

本文件（Task 1）只交付纯函数层：留存期配置加载 + "删哪些"的判定
（`compute_cutoff` / `compute_expired`）。effect 层与编排层由 Task 2/4 交付。
"""

from __future__ import annotations

import contextlib
import datetime
import json
import logging
import os
import pathlib
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from app.storage.idempotency import idempotent_effect
from tools.liaison import alerts, archive
from tools.liaison.session import CHINA_TZ
from tools.liaison.storage import db as liaison_db

logger = logging.getLogger(__name__)

#: 留存期天数的环境变量（design D13 逐字）。
RETENTION_DAYS_ENV: Final[str] = "HR_LIAISON_RETENTION_DAYS"

#: design D13 逐字：默认 180 天。⛔ 改这个数之前先改 design——
#: 它是"归档是工作材料、回溯窗口按季度算"这条论证的结论，不是随手挑的参数。
DEFAULT_RETENTION_DAYS: Final[int] = 180


class RetentionConfigError(ValueError):
    """留存期配置不可用。**fail-closed**：⛔ 不许退回默认值继续跑。

    退回默认值意味着一个手滑（`18` 少写一个 0）看起来生效了、实际按 180 跑；
    而 `0` 会当场删光全部归档。两个方向都不可接受，唯一安全的处置是拒绝执行。
    """


def load_retention_days(env: Mapping[str, str] | None = None) -> int:
    """读留存期天数。没配（缺失／空串／纯空白）⇒ 默认 180；配错 ⇒ raise。"""
    source: Mapping[str, str] = os.environ if env is None else env
    raw = source.get(RETENTION_DAYS_ENV)
    if raw is None or not str(raw).strip():
        return DEFAULT_RETENTION_DAYS
    text = str(raw).strip()
    try:
        days = int(text)
    except ValueError as exc:
        raise RetentionConfigError(
            f"{RETENTION_DAYS_ENV} 必须是正整数天数，实际是 {text!r}；"
            f"⛔ 拒绝按默认值继续跑"
        ) from exc
    if days < 1:
        raise RetentionConfigError(
            f"{RETENTION_DAYS_ENV} 必须 >= 1，实际是 {days}；"
            f"⛔ 0 或负数等于要求立刻删光全部归档，几乎必然是打错了"
        )
    return days


@dataclass(frozen=True)
class MessageRow:
    """从 `liaison_message` 读出来的一行 + "它有没有队列行"。

    `has_queue_row` 由 SQL 一次算出（见 `load_message_rows`），⛔ 不要在纯
    函数里再去查库——那会让 `compute_expired` 不再是纯函数。
    """

    msgid: str
    thread_id: str
    archived_at: str
    attachments_json: str
    has_queue_row: bool


@dataclass(frozen=True)
class ExpiredMessage:
    """一条判定为超期的消息，连同它引用的归档文件相对路径。"""

    msgid: str
    thread_id: str
    relative_paths: tuple[str, ...]


@dataclass(frozen=True)
class SkippedItem:
    """一条"没处理、且必须被看见"的记录。

    ⛔ 不要把它降级成一行 debug 日志：8.2 逐字要求"⛔ 不静默跳过"。
    """

    subject: str
    reason: str


@dataclass(frozen=True)
class ExpirySplit:
    """`compute_expired` 的三桶输出。三个桶互斥且穷尽。"""

    deletable: tuple[ExpiredMessage, ...]
    blocked_by_queue: tuple[ExpiredMessage, ...]
    undecidable: tuple[SkippedItem, ...]


def compute_cutoff(now: datetime.datetime, retention_days: int) -> datetime.datetime:
    """算出"早于这一刻的都算超期"的那一刻。纯函数。

    ⛔ `now` 必须带时区。裸的 naive 时间会在下面与带时区的 `archived_at`
    比较时抛 `TypeError`——那还算好的；真正危险的是它悄悄按本机时区被理解，
    而本机在 EDT，与 `+08:00` 差 12 小时。
    """
    if now.tzinfo is None:
        raise ValueError("now 必须带时区（tz-aware），⛔ 不接受 naive datetime")
    if retention_days < 1:
        raise ValueError(f"retention_days 必须 >= 1，实际是 {retention_days}")
    return now - datetime.timedelta(days=retention_days)


def parse_archived_at(value: Any) -> datetime.datetime:
    """解析 `liaison_message.archived_at`。

    🔴 该列的默认值是 SQLite 的 `datetime('now')`，它给的是 **UTC**、
    形如 `2026-09-09 10:00:00`、**不带时区后缀**。没有后缀就补 UTC，
    ⛔ 绝不许按本机时区理解——本机在 EDT，差 12 小时且不报错。
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"archived_at 必须是非空字符串，实际是 {value!r}")
    try:
        moment = datetime.datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"archived_at 不是可解析的时间戳：{value!r}") from exc
    if moment.tzinfo is None:
        return moment.replace(tzinfo=datetime.timezone.utc)
    return moment


def parse_relative_paths(attachments_json: Any) -> tuple[str, ...]:
    """从 `attachments_json` 取出相对归档根的路径清单。

    解析不了就 raise：调用方把它记成 `SkippedItem`。⛔ 不许当成"没有附件"
    静默返回空元组——那会让一条本该保留的材料被误判成"无文件可删"，
    而它引用的文件随后会在文件那一遍被当成孤儿删掉。
    """
    try:
        entries = json.loads(attachments_json)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"attachments_json 不是合法 JSON：{exc}") from exc
    if not isinstance(entries, list):
        raise ValueError(f"attachments_json 必须是数组，实际是 {type(entries).__name__}")
    paths: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("attachments_json 的条目必须是对象")
        path = entry.get("relative_path")
        if not isinstance(path, str) or not path:
            raise ValueError("attachments_json 的条目缺少非空 relative_path")
        paths.append(path)
    return tuple(paths)


def compute_expired(
    now: datetime.datetime, retention_days: int, rows: Sequence[MessageRow]
) -> ExpirySplit:
    """决定"删哪些"。**纯函数**（opener 约束 1 逐字给的签名）。

    判据只有年龄一条（tasks 8.1「按年龄判定，重复执行安全」）：
    ⛔ 不看发送人是谁、不看内容、不看消息类型——那会把一个机械的留存动作
    变成一条按人区别对待的规则。

    三个桶：
    - `deletable`：超期、且没有队列行 ⇒ 可以删；
    - `blocked_by_queue`：超期、但有队列行 ⇒ ⛔ 不删（opener 约束 2：
      `liaison_task` 行任何情况不删，而外键使得删父行必然失败）。见冲突 B；
    - `undecidable`：`archived_at` 或 `attachments_json` 坏了 ⇒ ⛔ 不猜、不删，
      交给编排层告警。
    """
    cutoff = compute_cutoff(now, retention_days)
    deletable: list[ExpiredMessage] = []
    blocked: list[ExpiredMessage] = []
    undecidable: list[SkippedItem] = []

    for row in rows:
        try:
            archived_at = parse_archived_at(row.archived_at)
        except ValueError as exc:
            undecidable.append(SkippedItem(row.msgid, f"archived_at 不可解析：{exc}"))
            continue
        # 严格小于：恰好落在 cutoff 上的行保留。边界上多留一天是安全方向，
        # 少留一天是不可逆的删除。
        if not archived_at < cutoff:
            continue
        try:
            paths = parse_relative_paths(row.attachments_json)
        except ValueError as exc:
            undecidable.append(SkippedItem(row.msgid, f"attachments_json 不可解析：{exc}"))
            continue
        item = ExpiredMessage(msgid=row.msgid, thread_id=row.thread_id, relative_paths=paths)
        if row.has_queue_row:
            blocked.append(item)
        else:
            deletable.append(item)

    return ExpirySplit(tuple(deletable), tuple(blocked), tuple(undecidable))


#: 清理动作的 effect 节点名。**这个名字有两个用途**，改它会同时打断两处：
#: ① 幂等键的中段；② `assert_effect_log_identity` 判断"哪些 thread 被清理过"
#: 的依据（冲突 A 的方案 2）。
RETENTION_DELETE_NODE: Final[str] = "effect_delete_expired_message"


class RetentionLedgerError(RuntimeError):
    """删台账行时行数对不上（既不是 1 也不是外键拒绝）。"""


@dataclass(frozen=True)
class CleanupFailure:
    """一条清理失败。`stage` ∈ {"ledger", "file", "scan", "prune"}。

    ⛔ 不要把它和 `SkippedItem` 合并：`SkippedItem` 是"按规矩不该处理"，
    `CleanupFailure` 是"该处理却没处理成"。前者是正常路径，后者要告警。
    """

    stage: str
    subject: str
    reason: str


@idempotent_effect(RETENTION_DELETE_NODE)
def effect_delete_expired_message(
    conn, *, thread_id: str, business_key: str
) -> str:
    """删掉一条超期的台账行。**本服务唯一的删库动作。**

    幂等键 `{thread_id}:effect_delete_expired_message:{msgid}`，与业务删除
    在同一个事务里提交（工程铁律 1）。留下的这行 `effect_log` 有两个作用：
    ① 审计——"这条材料是什么时候按留存期清掉的"的唯一凭据；
    ② 让 `assert_effect_log_identity` 知道哪些 thread 被清理过（冲突 A 方案 2）。

    ⛔ 只删 `liaison_message` 一张表的一行。⛔ 不删 `liaison_task`
    （opener 约束 2；外键也会拦住），⛔ 不删 `effect_log`。

    ⛔ 不 `commit()` 也不 `rollback()`：提交由 `idempotent_effect` 独占。
    """
    cursor = conn.execute(
        "DELETE FROM liaison_message WHERE msgid = ? AND thread_id = ?",
        (business_key, thread_id),
    )
    if cursor.rowcount != 1:
        # 装饰器会先 rollback 再把异常抛出去，所以这一行不会留下半截状态。
        raise RetentionLedgerError(
            f"删除 msgid={business_key} thread_id={thread_id} 影响了 {cursor.rowcount} 行"
            f"（期望恰好 1 行）；⛔ 不继续删，先查清楚"
        )
    return business_key


def load_message_rows(conn) -> tuple[MessageRow, ...]:
    """读全量台账 + 每行"有没有队列行"。

    `has_queue_row` 用一条 `EXISTS` 子查询一次算出来，⛔ 不要在 Python 里
    逐行回查——那会让行数上去之后变成 N+1 次查询，也会让 `compute_expired`
    有理由去碰连接。
    """
    return tuple(
        MessageRow(
            msgid=row[0],
            thread_id=row[1],
            archived_at=row[2],
            attachments_json=row[3],
            has_queue_row=bool(row[4]),
        )
        for row in conn.execute(
            "SELECT m.msgid, m.thread_id, m.archived_at, m.attachments_json, "
            "       EXISTS(SELECT 1 FROM liaison_task t WHERE t.msgid = m.msgid) "
            "FROM liaison_message AS m ORDER BY m.msgid"
        ).fetchall()
    )


def delete_expired_ledger_rows(
    conn, expired: Sequence[ExpiredMessage]
) -> tuple[tuple[str, ...], tuple[CleanupFailure, ...]]:
    """逐条删台账行。**单条失败不中止整轮**（opener 约束 1 逐字）。

    ⛔ 不许改成一条 `DELETE ... WHERE msgid IN (...)`：批量删会让一条外键
    冲突把整批一起回滚掉，"单条失败不中止整轮"当场失效，而且失败时你
    分不清是哪一条挡住的。
    """
    deleted: list[str] = []
    failures: list[CleanupFailure] = []
    for item in expired:
        try:
            applied = effect_delete_expired_message(
                conn, thread_id=item.thread_id, business_key=item.msgid
            )
        except Exception as exc:  # noqa: BLE001 —— 单条失败不中止整轮
            # ⛔ 捕获 Exception 而不是 BaseException：KeyboardInterrupt 必须能停下来。
            logger.error("留存清理：删台账行失败 msgid=%s：%s", item.msgid, exc, exc_info=True)
            failures.append(CleanupFailure("ledger", item.msgid, str(exc)))
            continue
        if applied is None:
            # 幂等命中：这条之前就删过了。正常路径，⛔ 不算失败也不算本轮删除。
            logger.info("留存清理：msgid=%s 之前已清理过，跳过", item.msgid)
            continue
        deleted.append(item.msgid)
    return tuple(deleted), tuple(failures)


#: 归档路径的层数：`<thread_id>/<yyyymmdd>/<叶子>`（design D4）。
#: ⛔ 层数不对的路径一律不删——不认识的形态说明假设错了，那时候要报不要删。
_ARCHIVE_PATH_DEPTH: Final[int] = 3


@dataclass(frozen=True)
class ArchiveFile:
    """归档目录下的一个文件。`day` 是路径里的 `<yyyymmdd>` 段，形态不对时为空串。"""

    relative_path: str
    day: str


def iter_archive_files(archive_root: pathlib.Path) -> tuple[ArchiveFile, ...]:
    """列出归档根下的全部普通文件。归档根不存在 ⇒ 空元组（首次运行的正常情况）。

    ⛔ **跳过符号链接**：跟着符号链接删会把归档根之外的东西删掉。
    """
    if not archive_root.is_dir():
        return ()
    found: list[ArchiveFile] = []
    for path in sorted(archive_root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        parts = path.relative_to(archive_root).parts
        day = parts[1] if len(parts) == _ARCHIVE_PATH_DEPTH else ""
        found.append(ArchiveFile(path.relative_to(archive_root).as_posix(), day))
    return tuple(found)


def _day_expiry_instant(day: str) -> datetime.datetime:
    """把 `<yyyymmdd>` 段折成"那一天最晚的一刻（+08:00）"。

    取当天 23:59:59.999999 而不是 00:00:00 是刻意的**保守方向**：晚一点过期
    意味着边界上多留、不会早删。日期来自 `received_at`，那是发送人看到的
    那个时刻，所以时区取 `+08:00`（design D4：⛔ 不做时区换算）。
    """
    if len(day) != 8 or not day.isdigit():
        raise ValueError(f"不是 yyyymmdd 形态：{day!r}")
    moment = datetime.datetime.strptime(day, "%Y%m%d")
    return moment.replace(
        hour=23, minute=59, second=59, microsecond=999999, tzinfo=CHINA_TZ
    )


def compute_deletable_files(
    now: datetime.datetime,
    retention_days: int,
    files: Sequence[ArchiveFile],
    referenced: frozenset[str],
) -> tuple[tuple[str, ...], tuple[SkippedItem, ...]]:
    """决定"删哪些文件"。纯函数：不碰文件系统、不读时钟。

    两道判据，缺一不可：
    1. **仍被台账引用的一律不删**——删了就是 design D3 禁止的
       「台账已记、材料缺失」。这一条让这一遍与台账那一遍的顺序无关，
       也让它对台账那一遍的失败自愈；
    2. 路径里的 `<yyyymmdd>` 早于 cutoff。

    形态不认识的路径（层数不对、日期段不是 8 位数字）进 `SkippedItem`，
    ⛔ 不删——不认识就说明我们的假设错了，那时候要报不要删。
    """
    cutoff = compute_cutoff(now, retention_days)
    deletable: list[str] = []
    skipped: list[SkippedItem] = []
    for item in files:
        if item.relative_path in referenced:
            continue
        try:
            expires_at = _day_expiry_instant(item.day)
        except ValueError as exc:
            skipped.append(
                SkippedItem(
                    item.relative_path,
                    f"路径不是 <thread_id>/<yyyymmdd>/<文件> 形态，⛔ 不删：{exc}",
                )
            )
            continue
        if expires_at < cutoff:
            deletable.append(item.relative_path)
    return tuple(deletable), tuple(skipped)


def _unlink(path: pathlib.Path) -> None:
    """删一个文件。**单独抽出来是为了让测试能注入一次确定性的失败**——
    ⛔ 这不是配置项，⛔ 不许给它加环境变量开关，⛔ 生产代码不许再包一层。"""
    path.unlink()


def delete_archive_files(
    archive_root: pathlib.Path, relative_paths: Sequence[str]
) -> tuple[tuple[str, ...], tuple[CleanupFailure, ...]]:
    """删文件。单条失败不中止整轮。

    删之前再确认一次目标落在归档根**之内**：路径是自己扫出来的，这道检查
    在正常路径上永远为真——它挡的是将来某次改动让路径来源变成"台账里的
    字符串"的那一天，那时候一个 `../../` 就能删到仓库外面去。
    """
    root = archive_root.resolve()
    deleted: list[str] = []
    failures: list[CleanupFailure] = []
    for relative_path in relative_paths:
        target = archive_root / relative_path
        try:
            if root not in target.resolve().parents:
                raise ValueError(f"目标不在归档根之内：{target}")
            _unlink(target)
        except Exception as exc:  # noqa: BLE001 —— 单条失败不中止整轮
            logger.error("留存清理：删归档文件失败 %s：%s", relative_path, exc, exc_info=True)
            failures.append(CleanupFailure("file", relative_path, str(exc)))
            continue
        deleted.append(relative_path)
    return tuple(deleted), tuple(failures)


def prune_empty_dirs(archive_root: pathlib.Path) -> tuple[str, ...]:
    """自底向上删掉空目录。**⛔ 归档根本身永不删。**

    ⛔ 只用 `rmdir`（目录非空时它自己会失败），⛔ 绝不许用 `shutil.rmtree`
    ——`rmtree` 会把一个"我以为是空的"目录连同里面的材料一起端掉。
    """
    if not archive_root.is_dir():
        return ()
    pruned: list[str] = []
    candidates = sorted(archive_root.rglob("*"), key=lambda p: len(p.parts), reverse=True)
    for path in candidates:
        if path.is_symlink() or not path.is_dir():
            continue
        if any(path.iterdir()):
            continue
        # `contextlib.suppress` 在事务扫描器的正面白名单里（TD-18 的还债形态），
        # ⛔ 不要改成 `with path:` 之类的写法。
        with contextlib.suppress(OSError):
            path.rmdir()
            pruned.append(path.relative_to(archive_root).as_posix())
    return tuple(pruned)


@dataclass(frozen=True)
class CleanupReport:
    """一轮清理的完整结果。

    ⚠️ `deleted_*` 在 `dry_run=True` 时表示"**本来会**删这些"，⛔ 不表示已删。
    渲染与调用方都必须先看 `dry_run`。
    """

    retention_days: int
    cutoff: str
    dry_run: bool
    deleted_messages: tuple[str, ...]
    blocked_by_queue: tuple[str, ...]
    deleted_files: tuple[str, ...]
    pruned_dirs: tuple[str, ...]
    skipped: tuple[SkippedItem, ...]
    failures: tuple[CleanupFailure, ...]


def compute_retention_alert_text(report: CleanupReport) -> str:
    """拼一条清理失败告警。纯函数：不读库、不写日志、不发送（铁律 2）。

    🔴 **只许带计数与阶段名。** ⛔ 不许带 msgid、发送人 userid、文件名、
    相对路径或任何消息内容——这条告警将来会经第 6 章送进企微群，带上它们
    等于把"谁发过什么材料"广播出去。明细只进本机运行日志。

    ⚠️ 冲突 B（plan「前置状态与冲突处置」逐字）：`blocked_by_queue` 的计数
    ——⛔ 只是计数，不带其中任何 msgid——必须在本轮有失败时也进这条告警，
    否则"仍有队列条目而永久留存"这件事只能靠人去翻本机日志才发现。
    """
    stages = "、".join(sorted({failure.stage for failure in report.failures})) or "无"
    return (
        "【HR 值守通道·留存清理失败】"
        f"留存期 {report.retention_days} 天；"
        f"本轮清理台账 {len(report.deleted_messages)} 行、归档文件 {len(report.deleted_files)} 个；"
        f"失败 {len(report.failures)} 项（阶段：{stages}）；"
        f"因队列未清而保留 {len(report.blocked_by_queue)} 条。"
        "明细见本机运行日志，⛔ 告警不带发送人、文件名与消息内容。"
    )


def emit_retention_alert(sink, text: str) -> bool:
    """把告警送出去。**⛔ 永不抛异常**，返回是否送成功。

    与第 7 章 `alerts.effect_emit_outage_alert` 同一条口径：告警通道失败只记
    本地日志，⛔ 不许因此中止清理这一轮——两件事毫无关系。
    ⛔ 捕获 `Exception` 而不是 `BaseException`：`KeyboardInterrupt` 必须能停下来。
    """
    try:
        sink.send(text)
    except Exception:
        logger.error("留存清理告警发送失败，原文：%s", text, exc_info=True)
        return False
    return True


def render_report(report: CleanupReport) -> str:
    """给人看的一屏摘要（本机 stdout / 日志）。

    ⚠️ 这一份**带明细**，因此 ⛔ 不许原样贴进企微群——对外的那一条是
    `compute_retention_alert_text`。
    """
    lines = [
        f"留存清理{'（DRY-RUN，未做任何修改）' if report.dry_run else ''}："
        f"留存期 {report.retention_days} 天，cutoff={report.cutoff}",
        f"  台账行 {'将删' if report.dry_run else '已删'} {len(report.deleted_messages)} 条："
        f"{'、'.join(report.deleted_messages) or '无'}",
        f"  归档文件 {'将删' if report.dry_run else '已删'} {len(report.deleted_files)} 个",
        f"  空目录清理 {len(report.pruned_dirs)} 个",
        f"  ⛔ 因仍有队列条目而保留（design D13：队列行不参与自动清理）"
        f" {len(report.blocked_by_queue)} 条：{'、'.join(report.blocked_by_queue) or '无'}",
    ]
    for item in report.skipped:
        lines.append(f"  跳过 {item.subject}：{item.reason}")
    for failure in report.failures:
        lines.append(f"  ❌ 失败[{failure.stage}] {failure.subject}：{failure.reason}")
    return "\n".join(lines)


def run_cleanup(
    conn,
    *,
    now: datetime.datetime,
    retention_days: int,
    archive_root: pathlib.Path,
    sink,
    dry_run: bool = False,
) -> CleanupReport:
    """跑一轮留存期清理。

    顺序（opener 约束 1 逐字）：**先删台账行、后删归档文件**。⛔ 不许调换。
    反过来会在两步之间留下"台账指向不存在的文件"，那是 design D3 禁止的。

    文件那一遍的"仍被引用"集合**必须在台账那一遍之后重新读一次**——
    用清理前的台账去算，刚删掉的那些行还"引用"着它们的文件，那些文件就
    永远删不掉了。

    单条失败不中止整轮；全部跑完之后**汇总成一条**告警（⛔ 不是每个失败一条：
    一次目录权限问题能刷出几百条告警，那等于没有告警）。
    """
    cutoff = compute_cutoff(now, retention_days)
    rows = load_message_rows(conn)
    split = compute_expired(now, retention_days, rows)

    failures: list[CleanupFailure] = []
    skipped: list[SkippedItem] = list(split.undecidable)

    if dry_run:
        deleted_messages = tuple(item.msgid for item in split.deletable)
        # 🔴 controller 裁决（覆盖 brief 原实现，landing deviation，见 fix 报告）：
        # dry_run 不许碰库，因此不能靠"重新读库"拿"清理之后"的台账——那样读
        # 到的还是没删掉的行，会把它自己的附件误判成"仍被引用"，导致预览
        # 报告 `deleted_messages` 里有它、`deleted_files` 里却没有它引用的
        # 文件，与真实运行的结果不一致（plan line 1809：dry_run 是上线前
        # 检查的唯一依据，预览必须可信）。改为**模拟**：假设本轮会全部成功，
        # 直接从这一轮读到的 `rows` 里剔除 `split.deletable` 的 msgid 集合。
        deleted_msgids = set(deleted_messages)
        surviving = tuple(row for row in rows if row.msgid not in deleted_msgids)
    else:
        deleted_messages, ledger_failures = delete_expired_ledger_rows(conn, split.deletable)
        failures.extend(ledger_failures)
        # ⚠️ 重新读一次：这一遍要的是**清理之后**还活着的台账指向了哪些文件。
        # ⛔ 不与上面的 dry_run 分支合并复用：这里的正确性来自"真的重新读
        # 库"——某条删除若失败，它仍会出现在重新读出来的结果里，从而继续
        # 保护它的文件不被误删（design D3 的"禁止台账已记、材料缺失"由此
        # 成立）。改成共享逻辑会削弱这条保证，⛔ 不许为了省一点重复而合并。
        surviving = load_message_rows(conn)
    referenced: set[str] = set()
    scan_ok = True
    for row in surviving:
        try:
            referenced.update(parse_relative_paths(row.attachments_json))
        except ValueError as exc:
            # 不知道哪些文件仍被引用 ⇒ ⛔ 整个文件那一遍不许跑。宁可这一轮
            # 不清文件，也不能误删一份还被台账指着的材料（design D3）。
            scan_ok = False
            failures.append(CleanupFailure("scan", row.msgid, f"attachments_json 不可解析：{exc}"))

    deleted_files: tuple[str, ...] = ()
    pruned_dirs: tuple[str, ...] = ()
    if scan_ok:
        candidates, file_skipped = compute_deletable_files(
            now, retention_days, iter_archive_files(archive_root), frozenset(referenced)
        )
        skipped.extend(file_skipped)
        if dry_run:
            deleted_files = candidates
        else:
            deleted_files, file_failures = delete_archive_files(archive_root, candidates)
            failures.extend(file_failures)
            pruned_dirs = prune_empty_dirs(archive_root)
    else:
        logger.error("留存清理：台账里有读不出来的 attachments_json，⛔ 本轮跳过全部文件清理")

    report = CleanupReport(
        retention_days=retention_days,
        cutoff=cutoff.isoformat(),
        dry_run=dry_run,
        deleted_messages=tuple(deleted_messages),
        blocked_by_queue=tuple(item.msgid for item in split.blocked_by_queue),
        deleted_files=tuple(deleted_files),
        pruned_dirs=tuple(pruned_dirs),
        skipped=tuple(skipped),
        failures=tuple(failures),
    )

    if report.failures:
        # ⛔ 不静默跳过（8.2 逐字）。一轮一条，⛔ 不是每个失败一条。
        emit_retention_alert(sink, compute_retention_alert_text(report))
    logger.info("%s", render_report(report))
    return report


#: 退出码。⚠️ 2/3/4 已被 `__main__.py` 占用（缺凭据 / SDK 不可用 / SDK 表面未验），
#: ⛔ 不许复用——排障的人靠退出码一眼分辨是哪一类问题。
EXIT_OK: Final[int] = 0
EXIT_RETENTION_FAILED: Final[int] = 5
EXIT_BAD_CONFIG: Final[int] = 6
EXIT_BAD_ARGS: Final[int] = 7

_DRY_RUN_FLAG: Final[str] = "--dry-run"
_USAGE: Final[str] = "用法：python -m tools.liaison cleanup [--dry-run]"


def cleanup_main(argv: Sequence[str] | None = None) -> int:
    """`python -m tools.liaison cleanup` 的实现。

    ⚠️ **这是本模块唯一允许读真实时钟与真实环境变量的地方**，其余全部靠注入。
    ⚠️ 路径取的是 `liaison_db.DEFAULT_DB_PATH` 与 `archive.DEFAULT_ARCHIVE_ROOT`
    的**属性访问**（不是 `from ... import` 的绑定），这样单测能 monkeypatch 到
    临时目录上——opener 约束 3：⛔ 单测不许碰真实 `data/`。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    dry_run = _DRY_RUN_FLAG in args
    unknown = [item for item in args if item != _DRY_RUN_FLAG]
    if unknown:
        print(f"未知参数：{' '.join(unknown)}。{_USAGE}", file=sys.stderr)
        return EXIT_BAD_ARGS

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    try:
        retention_days = load_retention_days()
    except RetentionConfigError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_BAD_CONFIG

    conn = liaison_db.get_connection()
    liaison_db.init_schema(conn)
    report = run_cleanup(
        conn,
        now=datetime.datetime.now(CHINA_TZ),
        retention_days=retention_days,
        archive_root=archive.DEFAULT_ARCHIVE_ROOT,
        sink=alerts.LoggingAlertSink(),
        dry_run=dry_run,
    )
    print(render_report(report))
    return EXIT_RETENTION_FAILED if report.failures else EXIT_OK
