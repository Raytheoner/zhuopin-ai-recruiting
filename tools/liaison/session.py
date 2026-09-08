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
from tools.liaison import alerts as liaison_alerts

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


#: tools/liaison/session.py → parents[0]=liaison, [1]=tools, [2]=仓库根
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: 存活戳落点。`data/` 已被 .gitignore:11 覆盖，⛔ 不会误入版本管理。
#: ⛔ 不落 data/liaison.db：存活戳是每次心跳都覆写的诊断信息，落库意味着每次心跳
#: 都要一次事务提交，而本模块**不许**自己提交（提交归 idempotent_effect 独占）。
DEFAULT_LIVENESS_PATH = REPO_ROOT / "data" / "liaison" / "liveness.json"

STATE_STARTING = "starting"
STATE_CONNECTED = "connected"
STATE_DISCONNECTED = "disconnected"

#: 存活戳 JSON 的字段契约。少任何一个都当作"读不到上一次的记录"。
LIVENESS_KEYS = ("state", "stamp_at", "since")


def effect_write_liveness_stamp(
    path: pathlib.Path,
    *,
    state: str,
    now: datetime,
    since: datetime | None = None,
) -> None:
    """盖一次存活戳（覆写语义，末次写入即真相）。

    **⛔ 本函数刻意不挂 @idempotent_effect，这不是漏了。**
    幂等键的语义是"这件事做过就不再做"。心跳恰恰要求每次都做——挂上装饰器，
    存活戳会**只写一次然后永远不再更新**，而外部看到的现象是"服务好像十分钟前
    就死了"。幂等在这里不是保护，是把功能反过来关掉。
    它也不需要幂等：覆写不累积、重复执行的结果与执行一次完全相同，本来就没有
    "重复"这个失败模式。

    ⛔ 不做 fsync：这是心跳，掉电丢掉最后一次的代价是"看起来早停了一秒"；
    `os.replace` 已经保证读者永远读到完整的一份。这与第 4 章附件落盘**必须**
    fsync 是两回事——那边丢的是不可恢复的材料。

    ⛔ 不用 `with open(...)`：见模块 docstring 第 1 条。
    """
    moment = format_instant(now)
    payload = {
        "state": state,
        "stamp_at": moment,
        "since": format_instant(since) if since is not None else moment,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    # 原子替换：外部读到的要么是上一份完整的，要么是这一份完整的，⛔ 不会是半截。
    os.replace(tmp_path, path)


def read_liveness_stamp(path: pathlib.Path) -> dict | None:
    """读上一次的存活戳。读不到／坏了／缺字段一律返回 None。

    ⛔ 不许因为存活戳坏了就拒绝启动：中断窗口的账在库里，存活戳只是诊断信息。
    为一个坏掉的温度计停工，代价与收益完全不成比例。
    """
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("存活戳读取失败，按「没有上一次记录」处理：%s", path, exc_info=True)
        return None
    if not isinstance(payload, dict) or any(key not in payload for key in LIVENESS_KEYS):
        logger.warning("存活戳字段不完整，按「没有上一次记录」处理：%s", path)
        return None
    return payload


class LiaisonSession:
    """连接生命周期的状态机。**唯一持有"现在是连着还是断着"这个判断的地方。**

    ⛔ 本类里不存在"上一条消息什么时候来的"这种状态——`tick()` 的判据只有
    `self._state` 一个变量。7.1 逐字：存活戳只跟连接健康走，
    "一段时间无消息" ≠ 断线。tests/test_session_liveness.py 的 AST 断言守着这条。

    时间一律**由调用方传入**，⛔ 类内部不调 `datetime.now()`：两小时的空闲要在
    单测里毫秒跑完，且每次跑出来的时间线必须一模一样。
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        alert_sink: "liaison_alerts.AlertSink",
        *,
        liveness_path: pathlib.Path = DEFAULT_LIVENESS_PATH,
    ) -> None:
        self.conn = conn
        self.alert_sink = alert_sink
        self.liveness_path = liveness_path
        self._state = STATE_STARTING
        self._since: datetime | None = None

    @property
    def state(self) -> str:
        return self._state

    # ── 启动 ──────────────────────────────────────────────────────────
    def start(self, now: datetime) -> None:
        """一次进程启动要做的全部补记（7.3）。顺序是钉死的，⛔ 不许调换：

        1. 读上一次的存活戳；若上次死在**连接健康**的状态，用最后一次盖戳的时间
           开一个 `startup_gap` 窗口——那段停机时间同样收不到消息；
        2. 把**所有**未闭合窗口（含上一步刚开的那个）按"恢复时间 = 本次启动时间"
           闭合。一条闭合路径管两种成因，⛔ 不要为 gap 单独写一条；
        3. 补发所有"已闭合但还没告警"的窗口；
        4. 最后才写本次启动的存活戳——写早了，第 1 步就读不到上一次的了。
        """
        previous = read_liveness_stamp(self.liveness_path)
        if previous is not None and previous["state"] == STATE_CONNECTED:
            gap_started_at = previous["stamp_at"]
            if gap_started_at < format_instant(now):
                effect_open_outage_window(
                    self.conn,
                    thread_id=CONNECTION_THREAD_ID,
                    business_key=gap_started_at,
                    detected_by=DETECTED_BY_STARTUP_GAP,
                )
            else:
                # 存活戳比"现在"还新 ⇒ 机器时钟被往回调过。造窗口只会得到一段
                # 负数长度的中断，⛔ 记一笔日志就过去，不猜也不编。
                logger.warning(
                    "存活戳时间 %s 不早于本次启动时间 %s，跳过停机窗口补记",
                    gap_started_at,
                    format_instant(now),
                )
        self._backfill_open_windows(now)
        self._flush_pending_alerts(now)
        self._state = STATE_STARTING
        self._since = now
        effect_write_liveness_stamp(
            self.liveness_path, state=STATE_STARTING, now=now, since=now
        )

    # ── 连接事件 ──────────────────────────────────────────────────────
    def on_connected(self, now: datetime) -> None:
        """连上了（首次或重连）。把还开着的窗口闭合并告警。"""
        for started_at in select_open_windows(self.conn):
            effect_close_outage_window(
                self.conn,
                thread_id=CONNECTION_THREAD_ID,
                business_key=started_at,
                recovered_at=format_instant(now),
                closed_by=CLOSED_BY_RECONNECT,
            )
        self._flush_pending_alerts(now)
        self._state = STATE_CONNECTED
        self._since = now
        effect_write_liveness_stamp(
            self.liveness_path, state=STATE_CONNECTED, now=now, since=now
        )

    def on_disconnected(self, now: datetime) -> None:
        """断了。开窗 + 把存活戳定格在断线时刻，此后 `tick()` ⛔ 不再更新它。

        已经是断开状态时直接返回：SDK 对同一次断线回调多次是常态，
        ⛔ 一次中断只许有一个窗口。
        """
        if self._state == STATE_DISCONNECTED:
            return
        effect_open_outage_window(
            self.conn,
            thread_id=CONNECTION_THREAD_ID,
            business_key=format_instant(now),
            detected_by=DETECTED_BY_DISCONNECT,
        )
        self._state = STATE_DISCONNECTED
        self._since = now
        effect_write_liveness_stamp(
            self.liveness_path, state=STATE_DISCONNECTED, now=now, since=now
        )

    # ── 心跳 ──────────────────────────────────────────────────────────
    def tick(self, now: datetime) -> None:
        """周期性盖存活戳。**判据只有连接状态**（7.1 逐字）。

        ⛔ 不许在这里加任何"距离上一条消息多久"的判断——那正是参考服务踩过的
        那个生产 bug：把空闲当成断线，于是安静的下午会收到一串假的中断告警，
        而真正的断线反而被淹没在里面。
        """
        if self._state != STATE_CONNECTED:
            return
        effect_write_liveness_stamp(
            self.liveness_path,
            state=STATE_CONNECTED,
            now=now,
            since=self._since if self._since is not None else now,
        )

    # ── 内部 ──────────────────────────────────────────────────────────
    def _backfill_open_windows(self, now: datetime) -> None:
        for started_at in select_open_windows(self.conn):
            effect_close_outage_window(
                self.conn,
                thread_id=CONNECTION_THREAD_ID,
                business_key=started_at,
                recovered_at=format_instant(now),
                closed_by=CLOSED_BY_STARTUP_BACKFILL,
            )

    def _flush_pending_alerts(self, now: datetime) -> None:
        """把"已闭合但还没告警"的窗口逐条补发。

        送出**成功**才标记 `alerted_at`。失败 ⇒ 留着 ⇒ 下次启动再来一遍。
        于是失败模式是"可能重复告警"，⛔ 不是"静默丢告警"——这个方向是刻意选的：
        重复的告警看得见，丢掉的告警看不见。
        """
        for started_at, recovered_at in select_unalerted_closed_windows(self.conn):
            text = liaison_alerts.compute_outage_alert_text(started_at, recovered_at)
            if not liaison_alerts.effect_emit_outage_alert(self.alert_sink, text):
                continue
            effect_mark_window_alerted(
                self.conn,
                thread_id=CONNECTION_THREAD_ID,
                business_key=started_at,
                alerted_at=format_instant(now),
            )
