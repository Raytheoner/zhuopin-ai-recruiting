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

import datetime
import json
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

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
