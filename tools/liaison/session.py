"""连接生命周期：存活戳、中断窗口、启动期补记（tasks.md 第 7 章）。

本模块的三条硬约束，改动前先读：

1. ⛔ **不许写 `with open(...)`**（连 `contextlib.suppress` 也不行）。
   tests/test_liaison_effects.py::test_no_second_transaction_manager_in_source 扫
   tools/liaison 下所有非测试 .py，把任何 `with <名字|属性|调用>:` 判为"隐式提交
   事务边界"违规——它认形状不认语义。文件读写一律用 Path.write_text/read_text +
   os.replace。⛔ 不要为了写 with 去改那个扫描器或往白名单里加本文件。
2. ⛔ **不许 conn.commit()/rollback()/executescript()**。提交由 idempotent_effect
   独占（storage/db.py 的模块 docstring 已写死），本模块的每一次库写入都必须
   挂在 @idempotent_effect 上。
3. ⛔ **本模块里不许出现"上一条消息什么时候来的"这种状态**。存活戳只跟连接健康走
   （spec「存活戳区分空闲与断线」、design D3）。参考服务把"一段时间没消息"当断线，
   是一个真实发生过的生产 bug（06-企业AI转型资产借鉴清单.md §三）。
   tests/test_session_liveness.py 有一条 AST 断言守着这一点。
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import sqlite3
from datetime import datetime, timedelta, timezone

from app.storage.idempotency import idempotent_effect

logger = logging.getLogger(__name__)

#: 固定 +08:00 偏移，⛔ 不用 ZoneInfo("Asia/Shanghai")：那要依赖系统 tzdata，
#: 而本服务的时间语义只需要一个固定偏移，多一个环境依赖就多一种"在别的机器上不一样"。
CHINA_TZ = timezone(timedelta(hours=8))

#: 幂等键需要一个 thread_id 分量，但**连接不是一个会话**——它不属于任何私聊或群。
#: 用一个固定哨兵值，双下划线包裹是为了不可能与真实的 userid / chatid 撞上。
CONNECTION_THREAD_ID = "__liaison_connection__"

DETECTED_BY_DISCONNECT = "disconnect_event"
DETECTED_BY_STARTUP_GAP = "startup_gap"
CLOSED_BY_RECONNECT = "reconnect"
CLOSED_BY_STARTUP_BACKFILL = "startup_backfill"


class OutageWindowStateError(RuntimeError):
    """要闭合／要标记的窗口不在预期状态。这是真 bug，⛔ 不许吞。"""


def format_instant(moment: datetime) -> str:
    """统一的时间字面量：ISO8601、+08:00、**精确到微秒**。

    ⛔ 不许截到秒：起始时间是窗口的主键与幂等键，同一秒内两次断线截到秒就会撞键，
    第二个窗口被幂等**静默**吃掉——而"静默吃掉一个中断窗口"正是本章要消灭的东西。

    ⛔ 不接受 naive datetime：没有时区的时间戳落进库里，将来没人能确定它是哪个
    时区的，而告警文本要把这个时间直接给人看。
    """
    if moment.tzinfo is None:
        raise ValueError("format_instant 需要带时区的 datetime，⛔ 不接受 naive 时间")
    return moment.astimezone(CHINA_TZ).isoformat(timespec="microseconds")


@idempotent_effect("effect_open_outage_window")
def effect_open_outage_window(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    detected_by: str,
) -> str:
    """开一个中断窗口。`business_key` = 窗口起始时间，也是表的主键。

    幂等命中（同一起始时间再开一次）由装饰器短路，返回 None——7.3 的
    「重复启动不重复补记」就是这条。
    """
    conn.execute(
        "INSERT INTO liaison_outage_window (started_at, thread_id, detected_by) "
        "VALUES (?, ?, ?)",
        (business_key, thread_id, detected_by),
    )
    return business_key


@idempotent_effect("effect_close_outage_window")
def effect_close_outage_window(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    recovered_at: str,
    closed_by: str,
) -> str:
    """闭合一个中断窗口。幂等键仍按**起始时间**去重（7.3 逐字）。

    ⛔ 本函数不许登记进 storage/effects.py 的 EFFECT_NODE_TO_TABLE：它是 UPDATE，
    不产生新行，登记进去会让"effect_log 条数 == 业务表行数"这条恒等式因为一个
    正当理由变红。恒等式只算 INSERT 型的 effect_open_outage_window。

    `WHERE recovered_at IS NULL` 与幂等键是两道独立的防线：一道在库里、一道在
    机制里。先被正常重连闭合过的窗口，之后的启动期补记会在**装饰器那一层**就被
    短路，因此 ⛔ 补记不可能覆盖掉真实的恢复时间。
    """
    cursor = conn.execute(
        "UPDATE liaison_outage_window SET recovered_at = ?, closed_by = ? "
        "WHERE started_at = ? AND recovered_at IS NULL",
        (recovered_at, closed_by, business_key),
    )
    if cursor.rowcount != 1:
        # 抛出去 ⇒ 装饰器回滚并原样上抛 ⇒ **不留幂等记录**。留了就等于宣称这件事
        # 做过了，真正该闭的那次从此永远不会再执行。
        raise OutageWindowStateError(
            f"要闭合的中断窗口不存在或已闭合：started_at={business_key}"
        )
    return business_key


@idempotent_effect("effect_mark_window_alerted")
def effect_mark_window_alerted(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    alerted_at: str,
) -> str:
    """标记"这个窗口的告警已经送出去了"。

    ⚠️ **只有在告警真的送出成功之后才允许调用**（alerts.effect_emit_outage_alert
    返回 True）。提前调用 = 宣称一条可能根本没送出的告警已经送到，
    而下一次启动的补发扫描正是靠 alerted_at IS NULL 找回这批漏发的。
    """
    cursor = conn.execute(
        "UPDATE liaison_outage_window SET alerted_at = ? "
        "WHERE started_at = ? AND alerted_at IS NULL",
        (alerted_at, business_key),
    )
    if cursor.rowcount != 1:
        raise OutageWindowStateError(
            f"要标记告警的窗口不存在或已标记：started_at={business_key}"
        )
    return business_key


def select_open_windows(conn: sqlite3.Connection) -> list[str]:
    """未闭合窗口的起始时间，**旧的在前**（只读，不进事务）。"""
    return [
        row[0]
        for row in conn.execute(
            "SELECT started_at FROM liaison_outage_window "
            "WHERE recovered_at IS NULL ORDER BY started_at"
        )
    ]


def select_unalerted_closed_windows(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """已闭合但告警还没送出去的窗口 `(started_at, recovered_at)`，旧的在前。

    这是"宁可重复告警、⛔ 不静默丢告警"的落点：告警发失败、或送出后进程被杀，
    这批行都还在，下一次启动会重新扫到。
    """
    return [
        (row[0], row[1])
        for row in conn.execute(
            "SELECT started_at, recovered_at FROM liaison_outage_window "
            "WHERE recovered_at IS NOT NULL AND alerted_at IS NULL ORDER BY started_at"
        )
    ]
