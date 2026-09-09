"""群通知的**唯一写库路径**。

⛔ 本模块不许出现 `conn.commit()` / `conn.rollback()` / `conn.executescript()`：
提交由 `app.storage.idempotency.idempotent_effect` 独占——它在同一个隐式事务里写
业务行与 `effect_log` 行然后一次性提交（工程铁律 1）。
`tests/test_liaison_effects.py::test_no_second_transaction_manager_in_source` 守着这条。
⛔ 同理不许写 `with X:`。

**顺序钉死：先发送、后写台账行。** 发送在 `@idempotent_effect` 的**函数体内部**
执行——装饰器的 `effect_log` 预检是"这条通知发没发过"的唯一权威，把发送挪到装饰器
外面，预检就形同虚设。反过来（先记后发）会让"记录说发了、其实没发"成为可能，
而那正是本章要消灭的那类谎。代价是"已发出但落库前进程被杀"⇒ 下次可能重复通知
一次：重复看得见（群里多一条），丢失看不见。这个方向与第 7 章告警的选择一致。

⚠️ 装饰器的预检 `SELECT` 在 LEGACY_TRANSACTION_CONTROL 下**不开启写事务**，
写事务从函数体最后那条 `INSERT` 才开始——所以 HTTP 期间**不持有 SQLite 写锁**。
⛔ 不要把 `INSERT` 挪到发送之前"图省事"。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from app.storage.idempotency import idempotent_effect
from tools.liaison.alerts import AlertSink, effect_emit_alert
from tools.liaison.notify import guard
from tools.liaison.notify.ratelimit import RATE_LIMIT_ERRCODE

#: 群通知不挂在任何一条会话上，取固定哨兵值——与第 7 章连接窗口用
#: `__liaison_connection__` 同一手法。调用方若能指出通知源自哪条会话，
#: 传那条会话的 thread_id 更好：台账主键是 **(thread_id, digest)** 复合键，
#: 与幂等键 `{thread_id}:effect_send_group_notify:{digest}` 同域，因此
#: 「同内容、不同 thread_id」两条都能各自落行，恒等式按 thread_id 分组照样成立。
GROUP_NOTIFY_THREAD_ID = "__liaison_group_notify__"

STATE_SENT = "sent"
STATE_PENDING_RESEND = "pending_resend"
STATE_REJECTED = "rejected"

#: 可以**自动重发**的 errcode。TD-25 的还债动作取方案 ②（按 errcode 过滤），
#: ⛔ 刻意**不加列、不写迁移**：「这一行能不能自动重发」完全由 `last_errcode`
#: 派生得出，而 `last_errcode` 已经在表里。加一列就等于把同一个事实存两遍，
#: 从此要靠写入方两处都记得同步——多出来的那一列迟早会跟 errcode 说的不是一回事，
#: 而且不一致时没有任何症状。派生得出来的东西不该落列。
#:
#: 依据：`45009` 是企微「接口调用超频」（D9 逐字，见 `ratelimit.RATE_LIMIT_ERRCODE`），
#: 是本仓库唯一有明文依据的**瞬时**错误——退避重试正是为它而写的。
#: ⛔ 集合刻意只有这一个成员，⛔ 不凭「看起来像瞬时的」往里加：
#: 误判「永久错误 = 可重发」正是 TD-25 要消灭的那条永远刷屏的死行；反过来误判
#: 「瞬时错误 = 需人工」只是多让人看一眼。两类错误的代价不对称，所以取窄不取宽。
#: 扩充时必须带上企微文档里该码的原文语义，并同步补一条过滤单测。
#:
#: 明确**不**在此列的：`93000`（机器人不在群）、`40001`（凭据失效）——这两类是
#: 配置/权限问题，重试多少次都是同一个结果，只能人工介入。
RETRYABLE_ERRCODES: frozenset[int] = frozenset({RATE_LIMIT_ERRCODE})

#: 供 SQL 展开用的定序元组。frozenset 的迭代顺序不保证稳定，占位符与参数必须同序。
#: ⚠️ 若 `RETRYABLE_ERRCODES` 被清空，下面拼出的 `IN ()` 是非法 SQL，两个查询会
#: **当场报错**而不是静默返回空集——这是刻意的：静默空集会让第 8 章的驱动器
#: 看起来"没有待重发"，那是最坏的一种失败。
_RETRYABLE_PARAMS: tuple[int, ...] = tuple(sorted(RETRYABLE_ERRCODES))
_RETRYABLE_PLACEHOLDERS = ", ".join("?" * len(_RETRYABLE_PARAMS))


@dataclass(frozen=True)
class NotifyRecord:
    """一次投递尝试的结果。**纯数据**，由 Task 5 的投递器产出。"""

    state: str
    attempts: int
    last_errcode: int | None = None
    last_error: str | None = None


def compute_alert_text(plan: guard.NotifyPlan, record: NotifyRecord) -> str:
    """拒发／重试耗尽时告警说什么。**纯函数**（铁律 2）。

    ⛔ 文本里不许出现"已由幂等保障""无需重发"这类说法——沿用 `alerts.py` 的判断：
    幂等防的是重复，补不回没发出去的东西；一句让人安心的话会让收信人不去补发。
    """
    if plan.mode == guard.MODE_REJECT:
        return (
            "【HR 值守通道·群通知拒发】"
            f"内容 {plan.byte_length} 字节超过本通道上限 {plan.limit_bytes} 字节，"
            f"且无法降级：{plan.reject_reason}。"
            f"该通知未发送（摘要 {plan.digest}），请人工处理。"
        )
    return (
        "【HR 值守通道·群通知未送达】"
        f"已尝试 {record.attempts} 次仍未成功"
        f"（errcode={record.last_errcode}，{record.last_error}）。"
        f"该通知已落为待重发记录（摘要 {plan.digest}），请人工确认后重发。"
    )


def _now_text() -> str:
    """送达时间戳。**与 SQLite `datetime('now')` 逐字同口径**：UTC、秒级、无时区后缀。

    格式必须是 `%Y-%m-%d %H:%M:%S`，⛔ 不是 `isoformat()`：同表的 `created_at` 由
    `DEFAULT (datetime('now'))` 写入，两列格式一旦不同，跨列排序就会静默出错
    （`'T'` = 0x54 > `' '` = 0x20），而 8.1-8.2 的留存期清理要在这张表上按时间范围
    比较。口径来源见 `app/storage/db.py::sqlite_utc_now`——⛔ 不从那里导入：
    本章 Tech Stack 只放行 `app.storage.idempotency.idempotent_effect` 一个 app.* 导入。
    """
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@idempotent_effect("effect_send_group_notify")
def effect_send_group_notify(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    plan: guard.NotifyPlan,
    deliver: Callable[[guard.NotifyPlan], NotifyRecord],
    alert_sink: AlertSink,
    channel: str = guard.GROUP_WEBHOOK_CHANNEL.name,
) -> str:
    """发一条群通知并把结果落成**恰好一行**。返回终局状态。

    `business_key` 必须是 `plan.digest`（内容摘要）——幂等键因此是
    `{thread_id}:effect_send_group_notify:{摘要}`（tasks 6.6 逐字）。

    `deliver` 是投递器，**以参数注入**（Task 5 提供真实实现，单测用桩）：
    ⛔ 本模块不认识 HTTP、不认识令牌桶、不认识退避——它只负责"把结果记下来"。
    """
    if business_key != plan.digest:
        raise ValueError(
            f"business_key 必须是内容摘要：business_key={business_key} plan.digest={plan.digest}"
        )

    if plan.mode == guard.MODE_REJECT:
        record = NotifyRecord(state=STATE_REJECTED, attempts=0)
    else:
        record = deliver(plan)

    sent_at = _now_text() if record.state == STATE_SENT else None
    conn.execute(
        "INSERT INTO liaison_group_notify "
        "(digest, thread_id, channel, state, mode, byte_length, limit_bytes, "
        " attempts, last_errcode, last_error, body, sent_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            plan.digest,
            thread_id,
            channel,
            record.state,
            plan.mode,
            plan.byte_length,
            plan.limit_bytes,
            record.attempts,
            record.last_errcode,
            record.last_error,
            # **完整原文**，⛔ 不是提要：待重发靠这一列重发。
            plan.full_text,
            sent_at,
        ),
    )

    if record.state != STATE_SENT:
        # ⛔ 告警失败不许中止：effect_emit_alert 永不抛异常（opener 约束 6）。
        # 台账行必须照落——"没告警成功"和"没记下来"是两码事，后者才是丢失。
        effect_emit_alert(alert_sink, compute_alert_text(plan, record))

    return record.state


def _select_pending(
    conn: sqlite3.Connection, errcode_predicate: str, params: tuple[object, ...]
) -> list[sqlite3.Row]:
    """`pending_resend` 的两个取数口径共用的查询骨架。**只读**。

    ⛔ **绝不写 `conn.row_factory = sqlite3.Row`**：连接是全应用共享的，一个声称
    "只读"的函数把整条连接的行类型永久改掉，会让它之后**所有**按位置取值的
    `fetchone()[0]` 静默改变含义。行类型只设在本次查询的 cursor 上——同包的
    `tools/liaison/queue.py::list_tasks` 就是这个写法，`app/audit/sinks.py`、
    `app/audit/assertions.py`、`app/outbound/queue.py` 三处都带着"⛔ 刻意不设
    conn.row_factory"的注释。
    """
    cursor = conn.execute(
        f"SELECT * FROM liaison_group_notify WHERE state = ? AND {errcode_predicate} "
        # thread_id 进排序键：主键是 (thread_id, digest) 复合键，同内容可以在
        # 两条 thread 上各有一行，只按 digest 排序不再是全序。
        "ORDER BY created_at, thread_id, digest",
        (STATE_PENDING_RESEND, *params),
    )
    cursor.row_factory = sqlite3.Row
    return cursor.fetchall()


def select_pending_resends(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """列出**可自动重发**的通知。**只读**。

    ⏸ **本章不提供重发驱动器**——把 `pending_resend` 真的重发出去属第 8 章。
    这个函数是留给它的把手，也是"重试耗尽的通知没有被静默丢弃"的可查证据。

    🔴 口径是「`last_errcode` ∈ `RETRYABLE_ERRCODES`」，⛔ **不是全部
    `pending_resend`**（TD-25）：`93000`（机器人不在群）、`40001`（凭据失效）这类
    重试多少次都是同一个结果，混进来只会被驱动器反复取出、反复失败、反复告警，
    变成一条永远刷屏的死行。`last_errcode IS NULL`（传输层抛异常，没拿到业务码）
    同样**不**进这里——⛔ 不猜它可重试。
    ⚠️ 被这里滤掉的行 ⛔ 不是消失了：它们由 `select_manual_intervention_resends`
    原样取得。两个口径的并集恒等于全部 `pending_resend` 行。
    """
    return _select_pending(
        conn, f"last_errcode IN ({_RETRYABLE_PLACEHOLDERS})", _RETRYABLE_PARAMS
    )


def select_manual_intervention_resends(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """列出**需人工介入**的未送达通知（`pending_resend` 里自动重发够不着的那些）。**只读**。

    🔴 这个函数存在的唯一理由：`select_pending_resends` 收窄口径之后，被滤掉的行
    ⛔ **不许变成看不见的沉默行**。没有这个把手，"过滤"就等于"丢弃"——而 spec 只
    允许"被记录且不被当作成功"，不允许"被记录然后没人再看得见"。报表与巡检从这里取数。
    ⏸ 本函数**不驱动任何重发**，也不接告警链路：驱动器不是 spec 要求，不在本批范围。

    `last_errcode IS NULL` 归这里：传输层异常没带回业务码，无从判断是否瞬时，
    按保守方向交人工，⛔ 不猜。

    ⏸ 本函数**未**进 `tools/liaison/notify/__init__.py` 的 `__all__`——还 TD-25 的那批
    opener 把可动文件限定在 `store.py` / `webhook.py` / `test_notify_*.py`，⛔ 没放行
    `__init__.py`。按包内路径 `store.select_manual_intervention_resends` 取数一样可用；
    要补成对外导出时，与 `select_pending_resends` 相邻加一行即可。
    """
    return _select_pending(
        conn,
        f"(last_errcode IS NULL OR last_errcode NOT IN ({_RETRYABLE_PLACEHOLDERS}))",
        _RETRYABLE_PARAMS,
    )
