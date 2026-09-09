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

🔴 **三条不许碰的东西**（opener 约束 2；第 1 条已被裁决一改写，见下）：
1. ⛔ **非终态**的 `liaison_task` 行（`pending` / `deferred`）任何情况都不删。
   它们是工作台账。
   🔴 **2026-09-09 裁决一（方案 ②）改写了这一条的原文**（原文＝"任何状态、任何
   情况都不删"）：`send_status='pushed'` 且 `pushed_at` 也超期的**终态**队列行，
   连同它的归档一起清。原文导致名单内发送人（汤丽萍/邵培申）的归档因外键永远
   落进 `blocked_by_queue`、无限期驻留，与 design D13 的 180 天和 proposal 的
   合规说明直接相悖——那是个人信息留存期问题，不是一条可以接受的保守取舍。
   依据：`docs/findings/2026-09-09-Shao-Peishen-裁决-留存冲突B与TD20改法.md`。
2. ⛔ `effect_log` 行一行不删。它是"这条材料被清理过"的唯一审计证据。
3. ⛔ 不加表、不加列——`storage/schema.py` 由并行泳道持有。

🔴 **本模块清两样东西**：归档（消息台账 + 附件文件，`HR_LIAISON_RETENTION_DAYS`，
默认 180）与**轮转日志**（`*.log.*`，`HR_LIAISON_LOG_RETENTION_DAYS`，默认 30，
TD-30 的还债）。⛔ 两个留存期刻意不对齐、⛔ 不许合并成一个参数：D13 明写
"日志是运行证据，归档是工作材料"。

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
from tools.liaison import alerts, archive, logsetup
from tools.liaison.session import CHINA_TZ
from tools.liaison.storage import db as liaison_db
from tools.liaison.storage.schema import SEND_STATUS_PUSHED

logger = logging.getLogger(__name__)

#: 留存期天数的环境变量（design D13 逐字）。
RETENTION_DAYS_ENV: Final[str] = "HR_LIAISON_RETENTION_DAYS"

#: design D13 逐字：默认 180 天。⛔ 改这个数之前先改 design——
#: 它是"归档是工作材料、回溯窗口按季度算"这条论证的结论，不是随手挑的参数。
DEFAULT_RETENTION_DAYS: Final[int] = 180

#: 轮转日志的留存期天数（TD-30 的还债）。**独立的环境变量**。
LOG_RETENTION_DAYS_ENV: Final[str] = "HR_LIAISON_LOG_RETENTION_DAYS"

#: design D13 逐字：日志 30 天。🔴 ⛔ **不许与 `DEFAULT_RETENTION_DAYS`（180）复用
#: 同一个变量或同一个默认值**——D13 明写两者刻意不对齐："日志是运行证据，归档是
#: 工作材料"。把它们并成一个数，等于让"改归档回溯窗口"这件事顺手把含个人信息的
#: 运行日志的暴露面拉长六倍，而且没有任何症状。
DEFAULT_LOG_RETENTION_DAYS: Final[int] = 30

#: 轮转产物的通配。⛔ 只匹配 `*.log.*`（`liaison.log.1`…），**不含当前活动日志
#: `liaison.log` 本体**——它正被一个打开的 `RotatingFileHandler` 攥着，删掉它
#: 之后 handler 仍往那个已被 unlink 的 inode 写，日志会静默进黑洞直到下一次轮转。
ROTATED_LOG_GLOB: Final[str] = "*.log.*"


class RetentionConfigError(ValueError):
    """留存期配置不可用。**fail-closed**：⛔ 不许退回默认值继续跑。

    退回默认值意味着一个手滑（`18` 少写一个 0）看起来生效了、实际按 180 跑；
    而 `0` 会当场删光全部归档。两个方向都不可接受，唯一安全的处置是拒绝执行。
    """


def _load_days(
    env_name: str, default: int, env: Mapping[str, str] | None, *, subject: str
) -> int:
    """读一个"天数"型环境变量。没配（缺失／空串／纯空白）⇒ `default`；配错 ⇒ raise。

    归档与日志两条留存期共用这一段解析，但 ⛔ **不共用默认值也不共用变量名**
    （D13：两者刻意不对齐）。抽出来的只有"怎么解析、错了怎么办"这一层。
    """
    source: Mapping[str, str] = os.environ if env is None else env
    raw = source.get(env_name)
    if raw is None or not str(raw).strip():
        return default
    text = str(raw).strip()
    try:
        days = int(text)
    except ValueError as exc:
        raise RetentionConfigError(
            f"{env_name} 必须是正整数天数，实际是 {text!r}；⛔ 拒绝按默认值继续跑"
        ) from exc
    if days < 1:
        raise RetentionConfigError(
            f"{env_name} 必须 >= 1，实际是 {days}；"
            f"⛔ 0 或负数等于要求立刻删光全部{subject}，几乎必然是打错了"
        )
    return days


def load_retention_days(env: Mapping[str, str] | None = None) -> int:
    """读归档留存期天数。没配 ⇒ 默认 180；配错 ⇒ raise。"""
    return _load_days(RETENTION_DAYS_ENV, DEFAULT_RETENTION_DAYS, env, subject="归档")


def load_log_retention_days(env: Mapping[str, str] | None = None) -> int:
    """读**日志**留存期天数（TD-30）。没配 ⇒ 默认 30；配错 ⇒ raise。

    🔴 ⛔ 不许在读不到 `HR_LIAISON_LOG_RETENTION_DAYS` 时退回读
    `HR_LIAISON_RETENTION_DAYS`——那正是 D13 禁止的"两者对齐"，且退回是静默的。
    """
    return _load_days(
        LOG_RETENTION_DAYS_ENV, DEFAULT_LOG_RETENTION_DAYS, env, subject="轮转日志"
    )


@dataclass(frozen=True)
class MessageRow:
    """从 `liaison_message` 读出来的一行 + **它那条队列行的状态与推送时间**。

    队列侧的两列由 SQL 一次连出来（见 `load_message_rows`），⛔ 不要在纯函数里
    再去查库、也 ⛔ 不要在 Python 里逐行回查——那会让 `compute_expired` 不再是
    纯函数，也会让行数上去之后变成 N+1 次查询。

    🔴 `queue_send_status is None` 表示**没有队列行**（LEFT JOIN 未命中）。
    ⛔ 不要用空串表示这件事：`liaison_task.send_status` 有 NOT NULL + 三态 CHECK，
    空串在库里不可能出现，但一旦代码里用空串充当"没有"，"没有队列行"与"队列行
    状态未知"这两件事就再也分不开了。
    """

    msgid: str
    thread_id: str
    archived_at: str
    attachments_json: str
    queue_send_status: str | None = None
    queue_pushed_at: str | None = None

    @property
    def has_queue_row(self) -> bool:
        """有没有队列行。⛔ 判据只能是 `queue_send_status`——`queue_pushed_at`
        在 `pending` / `deferred` 两态下必然为 NULL（表级等式 CHECK），拿它判
        "有没有行"会把待发条目一律看成没有队列行，那正是冲突 B 要防的误删。"""
        return self.queue_send_status is not None


@dataclass(frozen=True)
class ExpiredMessage:
    """一条判定为超期的消息，连同它引用的归档文件相对路径。

    `deletes_task_row=True` 表示这条消息**连它的队列行一起删**（裁决一 / 方案 ②：
    终态且超期的队列行不再是活台账）。这个标记跟着数据走、⛔ 不做成
    `delete_expired_ledger_rows` 的一个整批参数——两个桶会在同一轮里被一起处理，
    整批参数意味着"这一批到底该不该删队列行"要靠调用方记得分开传，记错一次就是
    误删一条活台账或漏删一条终态行。
    """

    msgid: str
    thread_id: str
    relative_paths: tuple[str, ...]
    deletes_task_row: bool = False


@dataclass(frozen=True)
class SkippedItem:
    """一条"没处理、且必须被看见"的记录。

    ⛔ 不要把它降级成一行 debug 日志：8.2 逐字要求"⛔ 不静默跳过"。
    """

    subject: str
    reason: str


@dataclass(frozen=True)
class ExpirySplit:
    """`compute_expired` 的**四桶**输出。四个桶互斥且穷尽。

    🔴 裁决一（2026-09-09，`docs/findings/2026-09-09-Shao-Peishen-裁决-留存冲突B与TD20改法.md`）
    把原来的 `blocked_by_queue` 一分为二：
    - `deletable_with_task`：队列行已到**终态**（`pushed`）且**两个时间都过期**
      ⇒ 连队列行一起清；
    - `blocked_by_queue`：`pending` / `deferred` ⇒ 仍是**活台账**，⛔ 不删。
    """

    deletable: tuple[ExpiredMessage, ...]
    deletable_with_task: tuple[ExpiredMessage, ...]
    blocked_by_queue: tuple[ExpiredMessage, ...]
    undecidable: tuple[SkippedItem, ...]

    @property
    def all_deletable(self) -> tuple[ExpiredMessage, ...]:
        """本轮要删的全部消息（两个可删桶合并）。顺序：先无队列行的，再带队列行的。"""
        return self.deletable + self.deletable_with_task


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


def _parse_timestamp(value: Any, column: str) -> datetime.datetime:
    """解析库里的一个时间戳列。**裸时间戳按 UTC 理解。**

    🔴 `archived_at` 的列默认值是 SQLite 的 `datetime('now')`，它给的是 **UTC**、
    形如 `2026-09-09 10:00:00`、**不带时区后缀**。没有后缀就补 UTC，
    ⛔ 绝不许按本机时区理解——本机在 EDT，差 12 小时且不报错。

    ⚠️ `pushed_at` 由写入方给字符串（`queue.mark_task_pushed` 的入参），今天的
    调用方给的是带 `+08:00` 的 ISO 串。万一某天写进来一个裸串，"按 UTC 理解"
    对留存判定恰好是**保守方向**：一个本意为 `10:00+08:00` 的裸串会被读成
    `10:00Z`，比真实时刻**晚** 8 小时，于是更不容易越过 cutoff ⇒ 更倾向于保留。
    ⛔ 不要"顺手"改成按 `CHINA_TZ` 补——那会把这条保守性反过来。
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{column} 必须是非空字符串，实际是 {value!r}")
    try:
        moment = datetime.datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"{column} 不是可解析的时间戳：{value!r}") from exc
    if moment.tzinfo is None:
        return moment.replace(tzinfo=datetime.timezone.utc)
    return moment


def parse_archived_at(value: Any) -> datetime.datetime:
    """解析 `liaison_message.archived_at`。裸时间戳按 UTC 补，见 `_parse_timestamp`。"""
    return _parse_timestamp(value, "archived_at")


def parse_pushed_at(value: Any) -> datetime.datetime:
    """解析 `liaison_task.pushed_at`。裸时间戳按 UTC 补，见 `_parse_timestamp`。"""
    return _parse_timestamp(value, "pushed_at")


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

    四个桶（🔴 裁决一 / 方案 ②，见 `ExpirySplit`）：
    - `deletable`：超期、且**没有队列行** ⇒ 可以删；
    - `deletable_with_task`：超期、队列行已到终态（`send_status='pushed'`）、
      且 `pushed_at` 也早于 cutoff ⇒ 连队列行一起删。🔴 **两个时间都必须过期**，
      少一个就不删——`archived_at` 过期只说明材料旧了，`pushed_at` 未过期说明
      这条推送还在人的回溯窗口内，删掉它等于让"这份材料几时递出去过"当场失忆；
    - `blocked_by_queue`：超期、但队列行还是 `pending` / `deferred`（或终态但
      `pushed_at` 未过期）⇒ ⛔ 不删。那两态是**活台账**，删掉就是把一件还没
      办完的事从队列里抹掉；
    - `undecidable`：`archived_at` / `attachments_json` / `pushed_at` 坏了
      ⇒ ⛔ 不猜、不删，交给编排层告警。

    ⚠️ 判据里**依然只有年龄**（tasks 8.1）：新桶多看的那一列是队列行的
    `pushed_at`，仍是一个时间；`send_status` 只用来区分"活台账 / 终态"，
    ⛔ 不看发送人、不看内容、不看消息类型。
    """
    cutoff = compute_cutoff(now, retention_days)
    deletable: list[ExpiredMessage] = []
    deletable_with_task: list[ExpiredMessage] = []
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
        if not row.has_queue_row:
            deletable.append(item)
            continue
        if row.queue_send_status != SEND_STATUS_PUSHED:
            # `pending` / `deferred`：活台账。⛔ 不删，且必须被看见（单独成桶）。
            # ⚠️ 这里用"不等于终态"而不是"等于 pending 或 deferred"：将来若真
            # 多出第四个状态，默认落到"保留"这一侧才是安全方向。
            blocked.append(item)
            continue
        try:
            pushed_at = parse_pushed_at(row.queue_pushed_at)
        except ValueError as exc:
            # 终态却读不出推送时间（理论上被表级等式 CHECK 挡着）。⛔ 不猜、不删，
            # 进 `undecidable` 好让它触发告警——落进 `blocked_by_queue` 会让一条
            # 坏数据看起来像一条正常的活台账，从此无人知晓。
            undecidable.append(SkippedItem(row.msgid, f"pushed_at 不可解析：{exc}"))
            continue
        if pushed_at < cutoff:
            deletable_with_task.append(
                ExpiredMessage(
                    msgid=row.msgid,
                    thread_id=row.thread_id,
                    relative_paths=paths,
                    deletes_task_row=True,
                )
            )
        else:
            blocked.append(item)

    return ExpirySplit(
        deletable=tuple(deletable),
        deletable_with_task=tuple(deletable_with_task),
        blocked_by_queue=tuple(blocked),
        undecidable=tuple(undecidable),
    )


#: 清理动作的 effect 节点名。**这个名字有两个用途**，改它会同时打断两处：
#: ① 幂等键的中段；② `assert_effect_log_identity` 判断"哪些 thread 被清理过"
#: 的依据（冲突 A 的方案 2）。
RETENTION_DELETE_NODE: Final[str] = "effect_delete_expired_message"


class RetentionLedgerError(RuntimeError):
    """删台账行时行数对不上（既不是 1 也不是外键拒绝）。"""


@dataclass(frozen=True)
class CleanupFailure:
    """一条清理失败。`stage` ∈ {"ledger", "file", "scan", "prune", "log"}。

    ⛔ 不要把它和 `SkippedItem` 合并：`SkippedItem` 是"按规矩不该处理"，
    `CleanupFailure` 是"该处理却没处理成"。前者是正常路径，后者要告警。
    """

    stage: str
    subject: str
    reason: str


@idempotent_effect(RETENTION_DELETE_NODE)
def effect_delete_expired_message(
    conn, *, thread_id: str, business_key: str, delete_task_row: bool = False
) -> str:
    """删掉一条超期的台账行（`delete_task_row=True` 时连它的队列行一起删）。
    **本服务唯一的删库动作。**

    幂等键 `{thread_id}:effect_delete_expired_message:{msgid}`，与业务删除
    在同一个事务里提交（工程铁律 1）。留下的这行 `effect_log` 有两个作用：
    ① 审计——"这条材料是什么时候按留存期清掉的"的唯一凭据；
    ② 让 `assert_effect_log_identity` 知道哪些 thread 被清理过（冲突 A 方案 2）。

    🔴 **删除顺序钉死：先 `liaison_task` 行、后 `liaison_message` 行。**
    `liaison_task.msgid REFERENCES liaison_message (msgid)`，反过来删父行必然被
    外键拒绝（那正是冲突 B 的成因）。两条删除与 `effect_log` 那一行在**同一个
    事务**里——⛔ 不许拆成两个 effect 节点：拆开就可能出现"队列行已删、消息行
    还在"的悬空中间态，而幂等记录会让它永不重试（铁律 1）。

    🔴 `delete_task_row` 是**裁决一 / 方案 ②**开的那道口子，仅对
    `send_status='pushed'` 且 `pushed_at` 也超期的终态队列行成立。⛔ 默认 `False`：
    调用方不显式要求就绝不碰 `liaison_task`（模块 docstring 那条禁令对
    `pending` / `deferred` 依然逐字有效）。⛔ 不删 `effect_log`。

    ⛔ 不 `commit()` 也不 `rollback()`：提交由 `idempotent_effect` 独占。
    """
    if delete_task_row:
        # 先删子表。⚠️ 这里**不检查 rowcount**：判定用的是清理前那一瞬的快照，
        # 而"队列行此刻已经不在了"（人在别处删过、或上一轮删到一半）对本轮的
        # 目标——把这条超期材料清干净——是**已达成**，不是失败。这一条与下面
        # 消息行的 `rowcount != 1` 严格检查刻意不同：那一行不见了意味着判定
        # 依据整个失效，必须停下来查。
        conn.execute(
            "DELETE FROM liaison_task WHERE msgid = ? AND thread_id = ?",
            (business_key, thread_id),
        )
    cursor = conn.execute(
        "DELETE FROM liaison_message WHERE msgid = ? AND thread_id = ?",
        (business_key, thread_id),
    )
    if cursor.rowcount != 1:
        # 装饰器会先 rollback 再把异常抛出去，所以这一行不会留下半截状态
        # ——包括上面那条 `liaison_task` 的删除，它和这一条在同一个事务里。
        raise RetentionLedgerError(
            f"删除 msgid={business_key} thread_id={thread_id} 影响了 {cursor.rowcount} 行"
            f"（期望恰好 1 行）；⛔ 不继续删，先查清楚"
        )
    return business_key


def load_message_rows(conn) -> tuple[MessageRow, ...]:
    """读全量台账 + 每行队列行的 `send_status` 与 `pushed_at`。

    🔴 队列侧两列由**一条 `LEFT JOIN`** 一次取回（裁决一：新桶要同时看
    `send_status` 与 `pushed_at`，原来的 `EXISTS` 只能回答有无）。⛔ 不要在
    Python 里逐行回查——那会让行数上去之后变成 N+1 次查询，也会让
    `compute_expired` 有理由去碰连接（模块 docstring 的禁令）。

    ⚠️ `LEFT JOIN` 不会把一行放大成多行，靠的是 `liaison_task.msgid` 上的
    **UNIQUE**（schema「一条消息最多一条队列条目」）。⛔ 那个 UNIQUE 掉了这里
    就会静默出现重复行，进而把同一条消息删两次——所以 ⛔ 不许改成普通索引。
    """
    return tuple(
        MessageRow(
            msgid=row[0],
            thread_id=row[1],
            archived_at=row[2],
            attachments_json=row[3],
            queue_send_status=row[4],
            queue_pushed_at=row[5],
        )
        for row in conn.execute(
            "SELECT m.msgid, m.thread_id, m.archived_at, m.attachments_json, "
            "       t.send_status, t.pushed_at "
            "FROM liaison_message AS m "
            "LEFT JOIN liaison_task AS t ON t.msgid = m.msgid "
            "ORDER BY m.msgid"
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
                conn,
                thread_id=item.thread_id,
                business_key=item.msgid,
                delete_task_row=item.deletes_task_row,
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


def prune_empty_dirs(
    archive_root: pathlib.Path, now: datetime.datetime, retention_days: int
) -> tuple[str, ...]:
    """自底向上删掉空目录。**⛔ 归档根本身永不删。**

    ⛔ 只用 `rmdir`（目录非空时它自己会失败），⛔ 绝不许用 `shutil.rmtree`
    ——`rmtree` 会把一个"我以为是空的"目录连同里面的材料一起端掉。

    🔴 finding 5(b)（终审）：`<thread_id>/<yyyymmdd>` 这一层目录，只有它自己
    的日期段也过期了才允许被 `rmdir`——哪怕它此刻恰好是空的。原因是
    `store_attachment` 的写入顺序是先 `mkdir` 出这层日期目录、再
    `mkstemp` 写文件；如果这一遍恰好落在两者之间的窗口里，"今天"的目录
    会被判定为空并端掉，随后 `mkstemp` 就会撞上 `FileNotFoundError`——
    对一条正在处理的实时消息来说这是一次不该发生的归档失败。
    ⛔ `now` 不许在本函数体内读真实时钟；由调用方注入（唯一允许调
    `datetime.now()` 的地方是 `cleanup_main`）。
    """
    if not archive_root.is_dir():
        return ()
    cutoff = compute_cutoff(now, retention_days)
    pruned: list[str] = []
    candidates = sorted(archive_root.rglob("*"), key=lambda p: len(p.parts), reverse=True)
    for path in candidates:
        if path.is_symlink() or not path.is_dir():
            continue
        if any(path.iterdir()):
            continue
        parts = path.relative_to(archive_root).parts
        if len(parts) == _ARCHIVE_PATH_DEPTH - 1:
            # `<thread_id>/<yyyymmdd>` 形态：只删过期的那些，未过期的哪怕
            # 空着也不碰——日期段解析不出来（形态不认识）时不特殊保护，
            # 按下面的通用空目录逻辑处理。
            try:
                expires_at = _day_expiry_instant(parts[1])
            except ValueError:
                pass
            else:
                if not expires_at < cutoff:
                    continue
        # `contextlib.suppress` 在事务扫描器的正面白名单里（TD-18 的还债形态），
        # ⛔ 不要改成 `with path:` 之类的写法。
        with contextlib.suppress(OSError):
            path.rmdir()
            pruned.append(path.relative_to(archive_root).as_posix())
    return tuple(pruned)


@dataclass(frozen=True)
class RotatedLog:
    """一个轮转日志产物。`name` 是 `log_dir` 下的文件名，`mtime` 带时区。"""

    name: str
    mtime: datetime.datetime


def iter_rotated_logs(log_dir: pathlib.Path) -> tuple[RotatedLog, ...]:
    """列出 `log_dir` 下的轮转日志产物（`*.log.*`）。目录不存在 ⇒ 空元组。

    🔴 ⛔ **只 glob 本层、不递归**，且 ⛔ **不匹配当前活动日志 `liaison.log`**：
    活动日志正被一个打开的 `RotatingFileHandler` 攥着，unlink 之后 handler 仍
    往那个已消失的 inode 写，日志静默进黑洞直到下一次轮转——一个为了"清干净"
    而制造出来的观测盲区，代价远大于收益。

    ⛔ 跳过符号链接与目录：跟着符号链接删会删到 `log_dir` 之外去。
    `mtime` 一律折成带时区的 UTC——裸的 naive 时间会在与 cutoff 比较时抛
    `TypeError`，或更糟：悄悄按本机 EDT 理解，差 12 小时。
    """
    if not log_dir.is_dir():
        return ()
    found: list[RotatedLog] = []
    for path in sorted(log_dir.glob(ROTATED_LOG_GLOB)):
        if path.is_symlink() or not path.is_file():
            continue
        mtime = datetime.datetime.fromtimestamp(
            path.stat().st_mtime, datetime.timezone.utc
        )
        found.append(RotatedLog(path.name, mtime))
    return tuple(found)


def compute_deletable_logs(
    now: datetime.datetime, log_retention_days: int, logs: Sequence[RotatedLog]
) -> tuple[str, ...]:
    """决定"删哪些轮转日志"。纯函数：不碰文件系统、不读时钟、不读环境变量。

    判据只有 mtime 一条（TD-30：欠的是时间维度，容量上界由
    `RotatingFileHandler(maxBytes, backupCount)` 早已满足）。严格小于 cutoff
    ——边界上多留一天是安全方向。

    🔴 cutoff 用的是 `log_retention_days`（默认 30），⛔ **不是**归档的
    `retention_days`（默认 180）。D13 明写两者刻意不对齐：日志是运行证据，
    归档是工作材料。⛔ 不许把两个参数并成一个。
    """
    cutoff = compute_cutoff(now, log_retention_days)
    return tuple(item.name for item in logs if item.mtime < cutoff)


def delete_rotated_logs(
    log_dir: pathlib.Path, names: Sequence[str]
) -> tuple[tuple[str, ...], tuple[CleanupFailure, ...]]:
    """删轮转日志。单条失败不中止整轮（沿用 `delete_archive_files` 的写法）。

    删之前再确认一次目标就在 `log_dir` **本层之内**：名字是自己 glob 出来的，
    这道检查在正常路径上永远为真——它挡的是将来某天名字来源变成外部输入的
    那一次，那时一个 `../../` 就能删到仓库里去。
    """
    root = log_dir.resolve()
    deleted: list[str] = []
    failures: list[CleanupFailure] = []
    for name in names:
        target = log_dir / name
        try:
            if target.resolve().parent != root:
                raise ValueError(f"目标不在日志目录本层之内：{target}")
            _unlink(target)
        except Exception as exc:  # noqa: BLE001 —— 单条失败不中止整轮
            logger.error("留存清理：删轮转日志失败 %s：%s", name, exc, exc_info=True)
            failures.append(CleanupFailure("log", name, str(exc)))
            continue
        deleted.append(name)
    return tuple(deleted), tuple(failures)


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
    #: 🔴 裁决一的新桶：连队列行一起清掉的那些 msgid。**是 `deleted_messages`
    #: 的子集**，⛔ 不是与它并列的第二批——两个字段相加会把这些条目算两遍。
    deleted_with_task: tuple[str, ...]
    blocked_by_queue: tuple[str, ...]
    deleted_files: tuple[str, ...]
    pruned_dirs: tuple[str, ...]
    #: TD-30：按 mtime 清掉的轮转日志文件名（`*.log.*`）。
    deleted_logs: tuple[str, ...]
    #: TD-30：日志留存期天数。⛔ 与 `retention_days` 是两个数，不许合并。
    log_retention_days: int
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

    🔴 裁决一：新桶 `deleted_with_task`（连队列行一起清掉的条数）与轮转日志的
    清理条数**同样只带计数**，且必须与 `render_report` 一起改——报告说清了、
    告警没说，两处口径分叉是没有任何症状的静默偏差（真身核验会抓）。

    🔴 finding 3（终审）：`report.skipped`（`SkippedItem`，例如 `archived_at`
    解析不了、或归档路径形态不认识）此前从不进这条文本——8.3/8.4 会在
    launchd 下跑，没人看 stdout，告警是唯一通道，说不出口就等于永久失联。
    这里只加计数，⛔ 不带 `subject`（那就是文件名/msgid，仍受合规红线管）。
    标题按"有没有失败"二选一，让"清理失败"这个措辞对"只有跳过、没有失败"
    的那一轮仍然准确。
    """
    stages = "、".join(sorted({failure.stage for failure in report.failures})) or "无"
    header = (
        "【HR 值守通道·留存清理失败】"
        if report.failures
        else "【HR 值守通道·留存清理有跳过项】"
    )
    return (
        header
        + f"留存期 {report.retention_days} 天（日志 {report.log_retention_days} 天）；"
        f"本轮清理台账 {len(report.deleted_messages)} 行"
        f"（其中连队列行一起清 {len(report.deleted_with_task)} 条）、"
        f"归档文件 {len(report.deleted_files)} 个、轮转日志 {len(report.deleted_logs)} 个；"
        f"失败 {len(report.failures)} 项（阶段：{stages}）；"
        f"跳过 {len(report.skipped)} 项；"
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
    verb = "将删" if report.dry_run else "已删"
    lines = [
        f"留存清理{'（DRY-RUN，未做任何修改）' if report.dry_run else ''}："
        f"留存期 {report.retention_days} 天，cutoff={report.cutoff}",
        f"  台账行 {verb} {len(report.deleted_messages)} 条："
        f"{'、'.join(report.deleted_messages) or '无'}",
        f"  其中连队列行一起{verb}（裁决一：终态且超期的队列行）"
        f" {len(report.deleted_with_task)} 条：{'、'.join(report.deleted_with_task) or '无'}",
        f"  归档文件 {verb} {len(report.deleted_files)} 个",
        f"  空目录清理 {len(report.pruned_dirs)} 个",
        f"  轮转日志 {verb} {len(report.deleted_logs)} 个"
        f"（留存期 {report.log_retention_days} 天）：{'、'.join(report.deleted_logs) or '无'}",
        f"  ⛔ 因队列行仍是活台账而保留（design D13：**非终态**队列行不参与自动清理）"
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
    log_dir: pathlib.Path,
    log_retention_days: int,
    sink,
    dry_run: bool = False,
) -> CleanupReport:
    """跑一轮留存期清理（归档 + 轮转日志）。

    顺序（opener 约束 1 逐字）：**先删台账行、后删归档文件**。⛔ 不许调换。
    反过来会在两步之间留下"台账指向不存在的文件"，那是 design D3 禁止的。
    单条台账行内部的顺序是 **`liaison_task` → `liaison_message`**（外键方向），
    见 `effect_delete_expired_message`。

    🔴 `log_dir` / `log_retention_days` **没有默认值**（TD-30）。理由与 finding 4
    给 `argv` 去掉默认值时逐字相同：一个"默认跳过日志清理"的缺省值意味着将来
    某次接线漏传参数时，日志留存期会**静默失效**——没有报错、没有告警，只有一
    堆含个人信息的历史日志按 PIPL 的口径躺在盘上。让漏传在写下来的那一刻就报
    `TypeError`。

    文件那一遍的"仍被引用"集合**必须在台账那一遍之后重新读一次**——
    用清理前的台账去算，刚删掉的那些行还"引用"着它们的文件，那些文件就
    永远删不掉了。

    单条失败不中止整轮；全部跑完之后**汇总成一条**告警（⛔ 不是每个失败一条：
    一次目录权限问题能刷出几百条告警，那等于没有告警）。
    """
    cutoff = compute_cutoff(now, retention_days)
    rows = load_message_rows(conn)
    split = compute_expired(now, retention_days, rows)
    # 🔴 裁决一：两个可删桶在这一轮里被**一起**处理。⛔ 不许分两次调
    # `delete_expired_ledger_rows`——那会让"先删台账行、后删归档文件"这条顺序
    # 在两批之间被劈开，第二批的文件那一遍就会看到第一批已经删掉的台账。
    to_delete = split.all_deletable
    with_task_msgids = {item.msgid for item in split.deletable_with_task}

    failures: list[CleanupFailure] = []
    skipped: list[SkippedItem] = list(split.undecidable)

    if dry_run:
        deleted_messages = tuple(item.msgid for item in to_delete)
        deleted_msgids = set(deleted_messages)
    else:
        deleted_messages, ledger_failures = delete_expired_ledger_rows(conn, to_delete)
        failures.extend(ledger_failures)

    # 🔴 finding 5(a)（终审）：文件那一遍内部的两次读，顺序钉死为
    # **先扫盘、后读"仍被引用"的台账**。⛔ 这不是把台账那一遍与文件那一遍
    # 调换（那条仍然是先删台账行、后删文件，design D3）——这里只是文件
    # 那一遍内部两个只读动作的先后。
    #
    # 原因：`archive_message` 先写文件、后写台账行（design D3 允许的中间态
    # 是"材料已在、台账未记"）。若先读台账建 `referenced`、再去扫盘，一条
    # 在两次读之间才落盘的新文件会被扫描到、却因为读 `referenced` 时它的
    # 台账行还没提交而被判定"无人引用"，只剩年龄一条线保它——backlog 重放
    # 或调小 `HR_LIAISON_RETENTION_DAYS` 时这条线可能不够，会把它删掉，
    # 制造出 D3 明令禁止的「台账已记、材料缺失」。先扫盘、后读台账则让
    # 扫描之后才出现的文件天然不是本轮候选（不在快照里），把窗口留给
    # 扫描之前已存在、随后被"重新读台账"看见的那部分。
    archive_files = iter_archive_files(archive_root)

    if dry_run:
        # 🔴 controller 裁决（覆盖 brief 原实现，landing deviation，见 fix 报告）：
        # dry_run 不许碰库，因此不能靠"重新读库"拿"清理之后"的台账——那样读
        # 到的还是没删掉的行，会把它自己的附件误判成"仍被引用"，导致预览
        # 报告 `deleted_messages` 里有它、`deleted_files` 里却没有它引用的
        # 文件，与真实运行的结果不一致（plan line 1809：dry_run 是上线前
        # 检查的唯一依据，预览必须可信）。改为**模拟**：假设本轮会全部成功，
        # 直接从这一轮读到的 `rows` 里剔除 `split.deletable` 的 msgid 集合。
        surviving = tuple(row for row in rows if row.msgid not in deleted_msgids)
    else:
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
            now, retention_days, archive_files, frozenset(referenced)
        )
        skipped.extend(file_skipped)
        if dry_run:
            deleted_files = candidates
        else:
            deleted_files, file_failures = delete_archive_files(archive_root, candidates)
            failures.extend(file_failures)
            pruned_dirs = prune_empty_dirs(archive_root, now, retention_days)
    else:
        logger.error("留存清理：台账里有读不出来的 attachments_json，⛔ 本轮跳过全部文件清理")

    # TD-30：轮转日志按 mtime 清。⚠️ **与归档那两遍完全解耦**——日志与台账之间
    # 没有任何引用关系，所以 `scan_ok` 为假时它照样该跑（一条读不出来的
    # `attachments_json` 说不出"哪些日志还有人引用"这种话）。
    log_candidates = compute_deletable_logs(
        now, log_retention_days, iter_rotated_logs(log_dir)
    )
    if dry_run:
        deleted_logs: tuple[str, ...] = log_candidates
    else:
        deleted_logs, log_failures = delete_rotated_logs(log_dir, log_candidates)
        failures.extend(log_failures)

    report = CleanupReport(
        retention_days=retention_days,
        cutoff=cutoff.isoformat(),
        dry_run=dry_run,
        deleted_messages=tuple(deleted_messages),
        deleted_with_task=tuple(
            msgid for msgid in deleted_messages if msgid in with_task_msgids
        ),
        blocked_by_queue=tuple(item.msgid for item in split.blocked_by_queue),
        deleted_files=tuple(deleted_files),
        pruned_dirs=tuple(pruned_dirs),
        deleted_logs=tuple(deleted_logs),
        log_retention_days=log_retention_days,
        skipped=tuple(skipped),
        failures=tuple(failures),
    )

    # 🔴 finding 1（终审）：`--dry-run` 的契约是「只报不动」，告警通道不是
    # "动"的对象也不该被它触发——`scan` 阶段的失败（例如一条读不出来的
    # `attachments_json`）在预览时也会真实产生，若不按 `dry_run` gate 住，
    # 6 章接上真实企微群通道之后，一次纯预览会向群里广播一条内容和真实
    # 失败一模一样、却没有任何标记区分预览与生产的"清理失败"消息。
    # 🔴 finding 3（终审）：`report.skipped` 同样必须触发告警——它此前从不
    # 进 `compute_retention_alert_text`，而 8.3/8.4 在 launchd 下跑，没人
    # 看 stdout，告警是唯一通道；"⛔ 不静默跳过"的承诺此前只对着日志成立。
    if not dry_run and (report.failures or report.skipped):
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


def cleanup_main(argv: Sequence[str]) -> int:
    """`python -m tools.liaison cleanup` 的实现。

    ⚠️ **这是本模块唯一允许读真实时钟与真实环境变量的地方**，其余全部靠注入。
    ⚠️ 路径取的是 `liaison_db.DEFAULT_DB_PATH`、`archive.DEFAULT_ARCHIVE_ROOT`
    与 `logsetup.resolve_log_dir()` 的**属性访问**（不是 `from ... import` 的
    绑定），这样单测能 monkeypatch 到临时目录上——opener 约束 3：⛔ 单测不许碰
    真实 `data/`。

    🔴 finding 4（终审）：`argv` **没有默认值**，调用方必须显式传。此前的默认
    `sys.argv[1:] if argv is None else argv` 在两个方向上都是错的——
    `__main__.py` 的真实接线传的是 `sys.argv[2:]`（`cleanup` 之后的那一段），
    唯一会触发默认分支的调用方式是"某处漏传 `argv`"，那时它读的是
    `sys.argv[1:]`（多带了字面量 `"cleanup"`，会被当成未知参数拒绝）或者
    是完全不相关的调用者自己的命令行，两种情况都不是"安全的兜底"，而是
    "悄悄跑错的 data/"——已经实测会真的打开生产的 `data/liaison.db` 与
    `data/liaison/archive` 跑一整轮。删掉默认值，让这类调用在写下来的那一刻
    就因为缺参数报错，而不是在生产机器上删数据的那一刻才被发现。
    """
    args = list(argv)
    dry_run = _DRY_RUN_FLAG in args
    unknown = [item for item in args if item != _DRY_RUN_FLAG]
    if unknown:
        print(f"未知参数：{' '.join(unknown)}。{_USAGE}", file=sys.stderr)
        return EXIT_BAD_ARGS

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    try:
        retention_days = load_retention_days()
        # TD-30：日志留存期走**自己**的环境变量与默认值（30），配错同样 fail-closed
        # 走 `RetentionConfigError` ⇒ EXIT_BAD_CONFIG。⛔ 不许因为"只是日志"就
        # 退回默认值继续跑——`HR_LIAISON_LOG_RETENTION_DAYS=3` 少打一个 0 会当场
        # 把三十天的排障证据删到三天。
        log_retention_days = load_log_retention_days()
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
        # ⚠️ 属性访问而不是 `from ... import`：与 `liaison_db` / `archive` 同一手法，
        # 让单测能 monkeypatch 到 tmp_path 上（opener 约束 3：⛔ 单测不许碰真实 `data/`）。
        log_dir=logsetup.resolve_log_dir(),
        log_retention_days=log_retention_days,
        sink=alerts.LoggingAlertSink(),
        dry_run=dry_run,
    )
    print(render_report(report))
    return EXIT_RETENTION_FAILED if report.failures else EXIT_OK
