"""TD-39·重连链：`error` 事件监听器 + 存活戳看门狗。

**这个文件钉的是一个无症状故障。** 连接断开后 SDK 只重连一次，那一次失败时
`on_error` 抛出的异常会把唯一还能触发重连的 task 杀死，于是永不再试：进程仍活着、
日志一片正常、`liveness.json` 永远停在 `disconnected`、中断窗口永不闭合、恢复告警
永不发出。⛔ 没有任何一处会报错。

⚠️ 现成的反面教材：TD-38 的 `assert options.heartbeat_interval == DEFAULT_HEARTBEAT_MS`
是同义反复（拿传进去的值跟它自己比），单位错成什么样都绿——那个 bug 因此活到真实
建连才暴露。本文件的每一条断言都必须在**修复前真的红**，红的原文见 0909AH 的报告。

⛔ 本文件不碰网络、不 import aibot（根 venv 按 design D10 不装 SDK）。

⚠️ **文末的「N-0 端到端」一节是 2026-09-09（`0909AQ`）加的，它有两处与上面不同**，
两处都是必要的、⛔ 不许当成可以照抄进别处的宽松口径：
- 它跑**真的线程与真的 asyncio 事件循环**（`main()` 的三线程接线是被验对象本身），
  因此有毫秒级的真实等待（看门狗轮询 10 毫秒、等值守线程消化事件封顶 5 秒）。
  ⛔ 仍然没有"等时间过去"式的 sleep：跨 180 秒阈值靠**假时钟往前跳**，
  产品阈值 `STALE_LIVENESS_SECONDS` 一个字没改。
- 它 import `tools.liaison.__main__`：要验的正是"看门狗触发之后接线那一段"，
  而那段只存在于 `main()` 里。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest

from tools.liaison import __main__ as liaison_main
from tools.liaison import session, session_client
from tools.liaison.storage import db as liaison_db

CHINA_TZ = timezone(timedelta(hours=8))


class PyeeLikeError(RuntimeError):
    """替身版的 `pyee.base.PyeeError`（非 Exception 载荷时抛的那个）。"""


class PyeeLikeClient:
    """替身 SDK 连接对象，**逐条复刻 pyee 的 `error` 语义**。

    关键的一条（`pyee/base.py:178-184` `_emit_handle_potential_error`）：
    `error` 事件**没有监听器时直接 `raise` 载荷本身**。⛔ 不许把它简化成
    "没监听器就什么也不做"——那正好会把本文件要钉的故障喂绿：
    真实 SDK 里 `aibot/client.py:76` 把 `on_error` 接成 `self.emit("error", error)`，
    没监听器 ⇒ 抛 ⇒ `aibot/ws.py:152` 炸 ⇒ 下一行的 `_schedule_reconnect()`
    **永远走不到**。
    """

    def __init__(self) -> None:
        self.handlers: dict[str, list] = {}

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)
        return handler

    def emit(self, event, *args, **kwargs) -> bool:
        funcs = list(self.handlers.get(event, []))
        if not funcs:
            if event == "error":
                payload = args[0] if args else None
                if isinstance(payload, Exception):
                    raise payload
                raise PyeeLikeError(f"Uncaught, unspecified 'error' event: {payload}")
            return False
        for func in funcs:
            func(*args, **kwargs)
        return True

    def run(self):  # pragma: no cover - 本文件不跑阻塞入口
        pass


# ─────────────────────────────────────────────────────────────────────────
# 改法 1（根因）：`error` 事件必须有监听器
# ─────────────────────────────────────────────────────────────────────────


def test_error_event_is_subscribed_so_pyee_does_not_reraise():
    """🔴 **回归钉子**：接线之后，触发 `error` 事件 ⛔ 不许把异常抛出去。

    修复前这条必红——`_prepare_client` 只接了 connected/disconnected 两个事件，
    pyee 对没有监听器的 `error` 直接 raise 载荷。
    """
    client = PyeeLikeClient()
    session_client.make_sdk_connect(
        lambda: client, on_connected=lambda: None, on_disconnected=lambda: None
    )
    # ⛔ 不许写成 pytest.raises(...)：本条要的正是"不抛"。
    handled = client.emit("error", ConnectionRefusedError("网线没插"))
    assert handled is True, "`error` 事件必须被本模块接住，⛔ 不许让 pyee 走 raise 分支"


def test_error_event_listener_is_wired_at_make_time_not_at_first_connect():
    """接线要在 `make` 的那一刻完成——⛔ 不许拖到第一次建连。

    第一次建连尝试发生时 `run_forever` 已经会吞异常了，接线出错在那里就没有症状。
    """
    client = PyeeLikeClient()
    session_client.make_sdk_connect(
        lambda: client, on_connected=lambda: None, on_disconnected=lambda: None
    )
    assert session_client.EVENT_ERROR in client.handlers


def test_every_event_in_the_contract_is_actually_subscribed():
    """`SUBSCRIBED_EVENTS` 是事件接线的唯一清单：列了就必须真的接上。

    ⛔ 挡的是"加了常量却忘了接线"——那种漏接不报错，只是某一类事件永远到不了
    本模块，与 TD-39 同形。
    """
    client = PyeeLikeClient()
    session_client.make_sdk_connect(
        lambda: client, on_connected=lambda: None, on_disconnected=lambda: None
    )
    assert set(client.handlers) == set(session_client.SUBSCRIBED_EVENTS)
    assert session_client.EVENT_ERROR in session_client.SUBSCRIBED_EVENTS


def test_error_event_is_logged_loudly_and_says_reconnect_was_handed_to_the_sdk(caplog):
    """⛔ 不许吞掉当没事发生：至少 WARNING，且写清"已交给 SDK 重连"。

    静默地咽下一个连接错误，与本条要修的 bug 是同一种病：看起来一切正常。
    """
    client = PyeeLikeClient()
    session_client.make_sdk_connect(
        lambda: client, on_connected=lambda: None, on_disconnected=lambda: None
    )
    with caplog.at_level(logging.WARNING, logger=session_client.__name__):
        client.emit("error", ConnectionResetError("对端断开"))
    records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert records, "`error` 事件必须留下至少一条 WARNING，⛔ 不许静默"
    assert "重连" in records[0].getMessage()


def test_error_listener_never_lets_a_handler_failure_break_the_chain():
    """监听器自己出问题也 ⛔ 不许把异常放回 SDK——那等于没修。"""
    client = PyeeLikeClient()
    session_client.make_sdk_connect(
        lambda: client, on_connected=lambda: None, on_disconnected=lambda: None
    )
    # 非 Exception 载荷（pyee 对它抛的是 PyeeError）同样必须被接住。
    assert client.emit("error", "字符串载荷") is True


# ─────────────────────────────────────────────────────────────────────────
# 改法 1 的行为证据：重连链不被 error 打断（复刻 aibot/ws.py:149-153）
# ─────────────────────────────────────────────────────────────────────────


class FakeWsManager:
    """复刻 `aibot/ws.py:143-153` 的 `connect()` 失败分支，逐行对齐：

        except Exception as e:
            self._logger.error(...)
            if self.on_error:
                self.on_error(e)              # ← 152 行，修复前在这里炸
            await self._schedule_reconnect()  # ← 153 行，修复前永远走不到
    """

    def __init__(self, on_error) -> None:
        self.on_error = on_error
        self.scheduled_reconnects: list[BaseException] = []

    def connect_and_fail(self, exc: BaseException) -> None:
        try:
            raise exc
        except Exception as e:  # noqa: BLE001 —— 复刻 SDK 的裸捕获
            self.on_error(e)
            self.scheduled_reconnects.append(e)


def test_a_failed_connect_still_reaches_schedule_reconnect():
    """🔴 钉死"152 行炸掉 153 行"那个具体形态。

    修复前：`on_error` → `client.emit("error", e)` → pyee 没有监听器 → raise，
    异常从 `connect_and_fail` 冒出去，`scheduled_reconnects` 永远是空的。
    真实 SDK 里这一抛还会顺带杀死 `_receive_loop` 那个 task——它是唯一还会调
    `_schedule_reconnect` 的地方，于是"只重连一次"。
    """
    client = PyeeLikeClient()
    session_client.make_sdk_connect(
        lambda: client, on_connected=lambda: None, on_disconnected=lambda: None
    )
    # aibot/client.py:76 把 SDK 的 on_error 接成 emit("error", error)。
    manager = FakeWsManager(on_error=lambda error: client.emit("error", error))

    failure = ConnectionRefusedError("首次建连失败")
    manager.connect_and_fail(failure)  # ⛔ 不许抛出来

    assert manager.scheduled_reconnects == [failure], (
        "on_error 之后必须走到 _schedule_reconnect——它是重连链上唯一的下一步"
    )


def test_repeated_failures_keep_scheduling_reconnects():
    """不是"第一次不抛"就够了：重连链必须**每一次**都走得通。"""
    client = PyeeLikeClient()
    session_client.make_sdk_connect(
        lambda: client, on_connected=lambda: None, on_disconnected=lambda: None
    )
    manager = FakeWsManager(on_error=lambda error: client.emit("error", error))
    for index in range(5):
        manager.connect_and_fail(TimeoutError(f"第 {index} 次"))
    assert len(manager.scheduled_reconnects) == 5


# ─────────────────────────────────────────────────────────────────────────
# 改法 2（N-0 兜底）：存活戳看门狗
# ─────────────────────────────────────────────────────────────────────────


def stamp(seconds_ago: float, *, base: datetime) -> str:
    return (base - timedelta(seconds=seconds_ago)).isoformat(timespec="microseconds")


def liveness(seconds_ago: float, *, base: datetime) -> dict:
    """一份 `connected` 的存活戳，`stamp_at` 与 `last_event_at` 都在 `seconds_ago` 秒前。"""
    return {
        "state": "connected",
        "stamp_at": stamp(seconds_ago, base=base),
        "since": stamp(seconds_ago, base=base),
        session.LIVENESS_EVENT_KEY: stamp(seconds_ago, base=base),
    }


def watchdog_started_long_ago(base: datetime):
    """看门狗的 `clock`：构造时（取 `started_at`）回一小时前，之后回 `base`。

    ⚠️ 2026-09-10（TD-42）起早于 `started_at` 的存活戳算上一次运行的残留、⛔ 不算证据
    （冷启动误报的修法），所以本节的假戳必须落在「看门狗起来之后」。
    """
    calls = [0]

    def clock():
        calls[0] += 1
        return base - timedelta(hours=1) if calls[0] == 1 else base

    return clock


def test_watchdog_threshold_is_clearly_above_the_measured_death_latency():
    """阈值必须**明显大于**实测判死时延 55.75 秒（心跳 30s × 2 次未回 pong）。

    取值不够大 ⇒ 把正常的判死过程误判成假死 ⇒ 无谓重建，反而制造断线。
    """
    assert session_client.STALE_LIVENESS_SECONDS > 55.75 * 2


def test_liveness_is_not_stale_within_the_threshold():
    """证伪的另一半：阈值内 ⛔ 不许触发——否则看门狗就是个定时炸弹。"""
    base = datetime(2026, 9, 9, 12, 0, 0, tzinfo=CHINA_TZ)
    inside = session_client.STALE_LIVENESS_SECONDS - 1
    assert not session_client.compute_liveness_is_stale(stamp(inside, base=base), base)


def test_liveness_is_stale_beyond_the_threshold():
    base = datetime(2026, 9, 9, 12, 0, 0, tzinfo=CHINA_TZ)
    outside = session_client.STALE_LIVENESS_SECONDS + 1
    assert session_client.compute_liveness_is_stale(stamp(outside, base=base), base)


def test_a_missing_or_unparsable_stamp_is_not_treated_as_stale():
    """读不到／坏了 ⇒ ⛔ 不猜。

    把"没有存活戳"当成"连接死了"会让**每一次启动**都先炸一轮重建：值守线程
    还没来得及盖第一个戳，看门狗就已经把连接拆了。
    """
    base = datetime(2026, 9, 9, 12, 0, 0, tzinfo=CHINA_TZ)
    assert not session_client.compute_liveness_is_stale(None, base)
    assert not session_client.compute_liveness_is_stale("不是时间", base)
    assert not session_client.compute_liveness_is_stale("", base)


def test_a_stamp_from_the_future_is_not_treated_as_stale():
    """时钟被往回调过 ⇒ 戳比"现在"还新。⛔ 不许因此判死一个健康的连接。"""
    base = datetime(2026, 9, 9, 12, 0, 0, tzinfo=CHINA_TZ)
    future = (base + timedelta(hours=1)).isoformat(timespec="microseconds")
    assert not session_client.compute_liveness_is_stale(future, base)


def test_watchdog_requests_a_rebuild_when_the_stamp_goes_stale():
    """🔴 **N-0 的钉子**：戳停更超阈值 ⇒ 必须真的请求重建。

    修复前 `check_liveness_once` 根本不存在——两层重连被同一个异常一并打掉，
    `client.run()` 不返回，外层 `run_forever` 一次都轮不到。
    """
    base = datetime(2026, 9, 9, 12, 0, 0, tzinfo=CHINA_TZ)
    rebuilds = []
    verdict = session_client.LivenessWatchdog(
        read_liveness=lambda: liveness(session_client.STALE_LIVENESS_SECONDS + 30, base=base),
        clock=watchdog_started_long_ago(base),
        request_rebuild=lambda: rebuilds.append(1) or True,
        terminate=lambda code: None,
    ).check_once()
    assert verdict.is_stale
    assert rebuilds == [1]


def test_watchdog_stays_quiet_within_the_threshold():
    base = datetime(2026, 9, 9, 12, 0, 0, tzinfo=CHINA_TZ)
    rebuilds = []
    verdict = session_client.LivenessWatchdog(
        read_liveness=lambda: liveness(1.0, base=base),
        clock=watchdog_started_long_ago(base),
        request_rebuild=lambda: rebuilds.append(1) or True,
        terminate=lambda code: None,
    ).check_once()
    assert not verdict.is_stale
    assert rebuilds == []


def test_watchdog_loop_uses_the_injected_clock_and_never_sleeps_for_real():
    """⛔ 不许用真实 `sleep` 等时间过去——注入时钟 + 注入 sleep。

    同时钉住"看门狗自己会停"：`should_stop` 一给真值就必须退出，
    否则 `stop_event.set()` 之后它还挂着。
    """
    base = datetime(2026, 9, 9, 12, 0, 0, tzinfo=CHINA_TZ)
    naps: list[float] = []
    rebuilds: list[int] = []
    rounds = iter([False, False, False, True])

    session_client.run_liveness_watchdog(
        read_liveness=lambda: liveness(session_client.STALE_LIVENESS_SECONDS + 5, base=base),
        clock=watchdog_started_long_ago(base),
        request_rebuild=lambda: rebuilds.append(1) or True,
        sleep=naps.append,
        should_stop=lambda: next(rounds),
        terminate=lambda code: None,
    )
    # ⚠️ 2026-09-10（TD-42）从「每一轮陈旧都请求一次」改成「一段假死只请求一次」：
    # 时钟不动 ⇒ 三轮都在同一段假死里 ⇒ 只请求一次；后续由宽限期与终止路径接手
    # （见 test_session_client_td42.py）。三轮都要真的跑到（三次 sleep）。
    assert len(rebuilds) == 1, "一段假死只请求一次重建，⛔ 不许每 15 秒把新连接再停一次"
    assert naps == [session_client.WATCHDOG_POLL_SECONDS] * 3
    assert all(nap > 0 for nap in naps), "⛔ 间隔为 0 就是满速自旋"


def test_watchdog_survives_a_failing_stamp_reader_but_leaves_a_symptom(caplog):
    """看门狗自己 ⛔ 不许成为新的静默故障源：读戳炸了要有症状、且循环不许死。

    ⛔ 不许写成"异常就 pass"——那样看门狗死了没人知道，兜底层等于不存在。
    """
    rounds = iter([False, True])
    rebuilds: list[int] = []

    def exploding_reader():
        raise OSError("磁盘炸了")

    with caplog.at_level(logging.ERROR, logger=session_client.__name__):
        session_client.run_liveness_watchdog(
            read_liveness=exploding_reader,
            clock=lambda: datetime(2026, 9, 9, 12, 0, 0, tzinfo=CHINA_TZ),
            request_rebuild=lambda: rebuilds.append(1) or True,
            sleep=lambda _: None,
            should_stop=lambda: next(rounds),
            terminate=lambda code: None,
        )
    assert rebuilds == [], "读不到戳 ⛔ 不许当成「连接死了」去拆连接"
    assert [r for r in caplog.records if r.levelno >= logging.ERROR], (
        "看门狗自己出错必须留下 ERROR，⛔ 不许静默 pass"
    )


# ─────────────────────────────────────────────────────────────────────────
# 重建把手：跨线程把 `client.run()` 逼返回
# ─────────────────────────────────────────────────────────────────────────


class FakeLoop:
    def __init__(self, *, closed: bool = False) -> None:
        self.closed = closed
        self.calls: list = []

    def is_closed(self) -> bool:
        return self.closed

    def call_soon_threadsafe(self, callback, *args):
        if self.closed:
            raise RuntimeError("Event loop is closed")
        self.calls.append(callback)

    def stop(self):  # pragma: no cover - 只作为被排程的 callback 传递
        pass


def test_loop_stopper_stops_a_captured_loop():
    stopper = session_client.LoopStopper()
    loop = FakeLoop()
    stopper.remember(loop)
    assert stopper.request_stop() is True
    assert loop.calls == [loop.stop]


def test_loop_stopper_reports_failure_when_nothing_was_captured():
    """⛔ 不许假装停成功了：没有把手时必须返回 False，好让调用方留症状。"""
    assert session_client.LoopStopper().request_stop() is False


def test_loop_stopper_reports_failure_on_an_already_closed_loop():
    stopper = session_client.LoopStopper()
    stopper.remember(FakeLoop(closed=True))
    assert stopper.request_stop() is False


def test_loop_stopper_forgets_the_previous_loop_on_a_new_attempt():
    """每次建连都是**全新**的对象与全新的 loop，旧把手 ⛔ 不许留着。

    留着的后果：看门狗去停一个早就关掉的 loop，真正卡住的那个反而没人动。
    """
    stopper = session_client.LoopStopper()
    old = FakeLoop()
    stopper.remember(old)
    stopper.forget()
    assert stopper.request_stop() is False
    assert old.calls == []


class ExplodingRepr:
    """`__repr__` 自己抛异常的载荷。⛔ 不是杜撰的刁难——见下面用例的 docstring。"""

    def __repr__(self):
        raise ValueError("repr 炸了")


def test_error_listener_survives_a_payload_whose_repr_explodes():
    """🔴 监听器必须是**全函数**：任何输入都不许让异常回到 SDK。

    ⚠️ 这条守的是一个**死循环**，不只是一次崩溃。`pyee/asyncio.py:78-81`：

        try:
            coro = f(*args, **kwargs)
        except Exception as exc:
            self.emit("error", exc)     # ← 监听器抛了，pyee 再 emit 一次 error

    监听器一旦抛异常，pyee 会拿这个异常**再触发一次 `error`**，于是又进同一个
    监听器……无限递归。修好了 TD-39 却在这里挂死，比原来的 bug 更难查。
    日志调用要格式化载荷（`%r`），载荷的 `__repr__` 抛异常就会走到这条路上。
    """
    client = PyeeLikeClient()
    session_client.make_sdk_connect(
        lambda: client, on_connected=lambda: None, on_disconnected=lambda: None
    )
    assert client.emit("error", ExplodingRepr()) is True


# ─────────────────────────────────────────────────────────────────────────
# N-0 端到端（0909AQ 补）：看门狗 → `client.run()` 返回 → `run_forever` 重建
#                            → 中断窗口闭合、存活戳恢复刷新
# ─────────────────────────────────────────────────────────────────────────
#
# 🔴 **上面那一整段验的是 `run_liveness_watchdog` 这个函数自己的判定逻辑；
# 接线那一段——「触发之后真的把 loop 停掉、`run()` 真的返回、外层真的重建」——
# 在 0909AQ 之前从未被端到端跑过。** `0909AJ` 的真实断网复验只证到了改法 1：
# `error` 监听器一上，SDK 每次重连失败都刷一次存活戳（退避封顶 30 秒），
# 存活戳最大间隔 30 秒 ⇒ **永远达不到 180 秒的阈值** ⇒ 看门狗按设计保持静默。
# 两层是**串联兜底**（改法 1 修好则 N-0 静默），不是并联触发。
#
# ⇒ 要触发 N-0 就必须构造改法 1 覆盖不到的形态：**「SDK 事件完全静默，但 loop
#   还活着」**（`0909AG` 撞到的原始故障）。⛔ 断网、拔网线、关代理都造不出来
#   （`0909AJ` 断了 245 秒，远超阈值，看门狗依然没触发，且那是**正确行为**）。
#   只能在这里用假 SDK 构造。
#
# ⚠️ 已知且**刻意不修**的一条（`0909AJ` 发现，Shao Peishen 2026-09-09 答 `3b`）：
# 冷启动瞬间看门狗可能在第一次建连之前就查一轮，那时 `LoopStopper` 还没抓到
# 任何 loop，`request_stop()` 返回 False 并记一条 ERROR。本节的假时钟让第一次
# 检查落在"存活戳刚盖下"的时刻，从而**绕开**这个形态，⛔ 不顺手修它。


#: 假 SDK 的兜底：看门狗**没**把 loop 停掉时，多久后由测试自己把它停掉。
#: 🔴 它存在的唯一理由是**让红的时候是断言失败、而不是挂死**——一条会挂死的
#: 测试没有判据力。它一响就等于宣告"看门狗这一层没干活"，本节每条用例都断言
#: 它 ⛔ 没响过。
SAFETY_NET_SECONDS = 5.0

#: 看门狗两次检查之间在本节里等多久。⚠️ 只压这一个参数，**阈值仍用产品默认的
#: `STALE_LIVENESS_SECONDS`**（180 秒）——跨过阈值靠假时钟往前跳，⛔ 不靠把阈值
#: 改小。压 poll 是因为 `main()` 传给看门狗的 `sleep` 是真的 `stop_event.wait`，
#: 默认 15 秒会让每条用例真等一刻钟。
POLL_SECONDS_IN_TEST = 0.01

#: 等值守线程把"重建后的 connected"处理完的上限（真实时间）。到点不 raise，
#: 由用例的断言去报——⛔ 不许把超时藏成一个 pytest 内部错误。
RECOVERY_WAIT_SECONDS = 5.0


class _AdvanceableClock:
    """可手工推进的时钟。⛔ 不用真实时间：本节要跨过 180 秒的阈值。

    ⚠️ 跨线程共享（主线程推进，看门狗线程与值守线程读），但只有一次属性赋值，
    GIL 下读写都是原子的，⛔ 不需要锁（也就不需要 `with`）。
    """

    def __init__(self, base: datetime) -> None:
        self._now = base

    def __call__(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


class SilentlyDeadSdkClient:
    """替身 SDK 连接对象，**复刻 `aibot/client.py:345-362` 的 `run()`**：

        loop = asyncio.new_event_loop()
        ...
        loop.run_until_complete(self.connect())
        loop.run_forever()        # ← 接收 task 被打死之后，它照样挂着

    握手在 loop **内部**触发一次 `connected`（第一个实例再补一次 `disconnected`，
    复刻"断线了、而重连链已经被打断"），此后**再不触发任何事件**、`loop` 却一直
    活着。这就是 TD-39·N-0 的形态：外层 `run_forever` 永远等不到 `run()` 返回。

    ⚠️ 握手必须在 loop 里触发：`LoopStopper.capture()` 靠 `asyncio.get_running_loop()`
    抓把手，SDK 的事件回调是**唯一**抓得到那个 loop 的时机。⛔ 挪到 `run()` 外面
    就抓不到，看门狗于是停不掉任何东西——而且没有任何症状。
    """

    def __init__(self, *, index: int, clock: _AdvanceableClock, drop_after_connect: bool) -> None:
        self.index = index
        self.clock = clock
        self.drop_after_connect = drop_after_connect
        self.handlers: dict[str, list] = {}
        self.run_calls = 0
        self.run_returned = False
        self.safety_net_fired = False

    # ── SDK 表面（`verify_client_surface` 要求 `on` 与同步零参 `run`）──
    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)
        return handler

    def emit(self, event, *args):
        for handler in list(self.handlers.get(event, [])):
            handler(*args)

    async def _handshake(self) -> None:
        self.emit(session_client.EVENT_CONNECTED)
        if self.drop_after_connect:
            self.clock.advance(30.0)
            self.emit(session_client.EVENT_DISCONNECTED)

    def _safety_net(self, loop) -> None:
        self.safety_net_fired = True
        loop.stop()

    def run(self) -> None:
        self.run_calls += 1
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        idle = None
        try:
            loop.run_until_complete(self._handshake())
            # 一个永不完成的等待：loop 有事可等、活得好好的，但**永远不会再有
            # 任何事件**——存活戳因此停更，这正是看门狗要认出来的形态。
            idle = loop.create_future()
            self.clock.advance(session_client.STALE_LIVENESS_SECONDS + 60.0)
            loop.call_later(SAFETY_NET_SECONDS, self._safety_net, loop)
            loop.run_forever()
        finally:
            if idle is not None:
                idle.cancel()
            loop.close()
            asyncio.set_event_loop(None)
        self.run_returned = True


class _RecordingSink:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def send(self, text: str) -> None:
        self.texts.append(text)


def _closed_outage_windows(db_path) -> list[tuple]:
    """已闭合的中断窗口（**另开一条只读连接**：值守线程那条 sqlite 连接只属于它）。"""
    if not db_path.is_file():
        return []
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT started_at, recovered_at, closed_by FROM liaison_outage_window "
            "WHERE recovered_at IS NOT NULL ORDER BY started_at"
        ).fetchall()
    except sqlite3.OperationalError:
        # 值守线程还没建表 ⇒ 当作"还没有窗口"，⛔ 不当成失败（调用方在轮询）。
        return []
    finally:
        conn.close()


class N0Scenario:
    """一次端到端跑动留下的全部观察值。"""

    def __init__(self, *, exit_code, clients, naps, stamp_path, db_path) -> None:
        self.exit_code = exit_code
        self.clients = clients
        self.naps = naps
        self.stamp_path = stamp_path
        self.db_path = db_path

    @property
    def safety_nets_fired(self) -> list[int]:
        return [c.index for c in self.clients if c.safety_net_fired]

    def closed_windows(self) -> list[tuple]:
        return _closed_outage_windows(self.db_path)

    def liveness(self) -> dict | None:
        return session.read_liveness_stamp(self.stamp_path)


def run_n0_scenario(tmp_path, monkeypatch, *, connections: int = 2) -> N0Scenario:
    """把整条 N-0 链路真的跑一遍，跑满 `connections` 次建连后停下。

    **被跑到的都是产品代码**：`main()` 的三线程接线、真的 `make_sdk_connect` /
    `_prepare_client` / `LoopStopper`、真的 `run_liveness_watchdog`、真的
    `run_forever`、真的 `LiaisonSession`。注入的只有三样：
    ① 假 SDK（唯一能构造"事件静默但 loop 活着"的办法，见本节抬头）；
    ② 假时钟（跨 180 秒阈值要在毫秒里发生）；
    ③ 看门狗的 `read_liveness` 指到 tmp 的存活戳、`poll_seconds` 压到 10 毫秒。
       ⚠️ `read_liveness` 仍旧调**产品的** `liaison_main.read_liveness_payload`，
       只换路径——⛔ 不另写一份读法。阈值 ⛔ 没被调小。
    ④ 🔴 看门狗的 `terminate` 换成记录器（TD-42 起它默认是 `os._exit`，在 pytest 里
       真的调会把整个测试进程杀掉）、台账指到 tmp。本节验的是 TD-39 的「停 loop」
       路径；终止路径由 `test_session_client_td42.py` 单独验。
    """
    base = datetime(2026, 9, 9, 12, 0, 0, tzinfo=CHINA_TZ)
    clock = _AdvanceableClock(base)
    stamp_path = tmp_path / "liveness.json"
    db_path = tmp_path / "liaison.db"

    # 假凭据只为让 `main()` 走完启动路径。⛔ 不碰网络：conftest 的 netguard 罩着，
    # 而假 SDK 根本不建 socket。
    monkeypatch.setenv(liaison_main.DOTENV_PATH_ENV, str(tmp_path / "nope.env"))
    monkeypatch.setenv("HR_LIAISON_BOT_ID", "fake-bot")
    monkeypatch.setenv("HR_LIAISON_BOT_SECRET", "fake-secret")
    # 值守线程的 `start()`、两个连接事件回调、看门狗的 `clock` 全走这一个假时钟。
    monkeypatch.setattr(liaison_main, "now", clock)

    clients: list[SilentlyDeadSdkClient] = []

    def client_builder(_credentials, **_kwargs):
        client = SilentlyDeadSdkClient(
            index=len(clients),
            clock=clock,
            # 只在第一次连接之后补一次 `disconnected`：窗口就此开着，此后
            # 事件完全静默——中断窗口"永不闭合"正是 TD-39 的可见形态。
            drop_after_connect=not clients,
        )
        clients.append(client)
        return client

    def session_builder():
        conn = liaison_db.get_connection(db_path)
        liaison_db.init_schema(conn)
        return session.LiaisonSession(conn, _RecordingSink(), liveness_path=stamp_path)

    def watchdog(**kwargs):
        kwargs["read_liveness"] = lambda: liaison_main.read_liveness_payload(stamp_path)
        kwargs["poll_seconds"] = POLL_SECONDS_IN_TEST
        kwargs["terminate"] = terminations.append
        kwargs["ledger_path"] = tmp_path / "watchdog.json"
        session_client.run_liveness_watchdog(**kwargs)

    terminations: list[int] = []

    naps: list[float] = []

    def sleep_between_attempts(delay: float) -> None:
        """`run_forever` 的退避。⛔ 不真的等——它是本用例停下无限循环的唯一出口
        （`run_forever` 刻意没有"最多重试 N 次"的参数，见其 docstring）。"""
        naps.append(delay)
        if len(naps) < connections:
            return
        # 最后一轮：等值守线程把"重建后的 connected"消化掉（窗口闭合＋存活戳刷新）
        # 再停。⛔ 不能一 raise 了事——`main()` 的 finally 会 set stop_event，
        # 值守线程当场退出，E-3 要看的那两样东西就永远不会出现。
        deadline = time.monotonic() + RECOVERY_WAIT_SECONDS
        while time.monotonic() < deadline and not _closed_outage_windows(db_path):
            time.sleep(0.01)
        raise KeyboardInterrupt

    exit_code = liaison_main.main(
        session_builder=session_builder,
        client_builder=client_builder,
        runner=lambda connect: session_client.run_forever(
            connect, sleep=sleep_between_attempts
        ),
        watchdog=watchdog,
    )
    return N0Scenario(
        exit_code=exit_code,
        clients=clients,
        naps=naps,
        stamp_path=stamp_path,
        db_path=db_path,
    )


def test_e2e_stale_liveness_makes_the_watchdog_stop_the_loop_so_run_returns(
    tmp_path, monkeypatch
):
    """**E-1**：存活戳陈旧 ⇒ 看门狗**真的**停掉事件循环，`client.run()` **真的**返回。

    🔴 判据是**"谁"停的**，不只是"停没停"：一个 loop 只有两个人能停它——看门狗
    （经 `LoopStopper.request_stop`）与本节的兜底安全网。所以
    「`run()` 返回了 ⋀ 安全网没响」= 看门狗这一层真的干了活。
    ⛔ 不许把安全网那条断言删掉：删掉之后本条在看门狗缺席时会变成**挂死 5 秒
    然后照样绿**——那正是它要守的那种无症状。

    先红的造法：把 `__main__.py` 里 `threading.Thread(target=watchdog, ...)`
    那段接线摘掉（或摘掉 `main()` 的 `watchdog=` 形参）。
    """
    scenario = run_n0_scenario(tmp_path, monkeypatch)

    assert scenario.clients, "假 SDK 一个都没被造出来，本用例什么都没测到"
    first = scenario.clients[0]
    assert first.run_calls == 1
    assert first.run_returned is True, (
        "`client.run()` 没有返回——事件循环还挂着，外层 run_forever 一次都接不上手。"
        "这正是 TD-39·N-0 的形态：进程活着、日志正常、⛔ 没有任何报错"
    )
    assert scenario.safety_nets_fired == [], (
        "事件循环是被本用例的兜底安全网停的，⛔ 不是看门狗——N-0 兜底层没有生效"
    )


def test_e2e_after_run_returns_the_outer_loop_really_rebuilds_the_connection(
    tmp_path, monkeypatch
):
    """**E-2**：`client.run()` 返回之后，外层 `run_forever` **真的重建了一次连接**。

    ⚠️ 判据是"又造了一个**全新的**连接对象"，⛔ 不是"又调了一次 run()"：SDK 的
    `_started` 闩锁让同一个对象第二次 `connect()` 直接返回，`loop.run_forever()`
    会挂在一个空转的事件循环上——进程活着、日志正常、**永远不再连上**
    （见 `make_sdk_connect` 的 docstring）。复用对象与不重建一样糟，且同样无症状。

    先红的造法：把 `run_forever` 的重建分支改成 `pass`（`while True` 改成只跑一轮
    就 `return`），或摘掉看门狗接线让第一次 `run()` 根本不返回。
    """
    scenario = run_n0_scenario(tmp_path, monkeypatch)

    assert len(scenario.clients) >= 2, (
        f"外层 run_forever 没有重建连接（只造了 {len(scenario.clients)} 个连接对象）"
    )
    assert scenario.clients[0] is not scenario.clients[1], (
        "重建必须拿一个**全新**的连接对象：SDK 的 `_started` 闩锁让同一个对象"
        "第二次建连什么都不做，⛔ 且没有任何症状"
    )
    assert scenario.clients[1].run_calls == 1, "重建出来的连接必须真的被 run 起来"
    assert scenario.naps[0] == session_client.compute_backoff_delay(1), (
        "两次建连之间必须走退避，⛔ 不许满速自旋"
    )
    assert scenario.safety_nets_fired == [], "重建必须由看门狗驱动，⛔ 不是兜底安全网"


def test_e2e_the_rebuilt_connection_closes_the_outage_window_and_refreshes_the_stamp(
    tmp_path, monkeypatch
):
    """**E-3**：重建成功后存活戳恢复刷新、中断窗口被闭合（口径同 `0909AJ` ⑤⑥）。

    这条盯的是 TD-39 唯一**对外可见**的症状：`liveness.json` 永远停在
    `disconnected`、中断窗口永不闭合、恢复告警永不发出。链路修好之后，这三样
    必须自己恢复，⛔ 不需要任何人工干预。
    """
    scenario = run_n0_scenario(tmp_path, monkeypatch)

    assert scenario.safety_nets_fired == [], "恢复必须由看门狗驱动，⛔ 不是兜底安全网"

    closed = scenario.closed_windows()
    assert len(closed) == 1, (
        f"断线开出的中断窗口没有被重建后的连接闭合：{scenario.closed_windows()}"
    )
    started_at, recovered_at, closed_by = closed[0]
    assert closed_by == session.CLOSED_BY_RECONNECT
    assert recovered_at > started_at, "恢复时间必须晚于断线时间"

    payload = scenario.liveness()
    assert payload is not None, "存活戳文件不见了"
    assert payload["state"] == session.STATE_CONNECTED, (
        "存活戳还停在断线状态——存活戳没有恢复刷新，"
        "外部看到的仍然是「服务早就死了」"
    )
    assert payload["stamp_at"] == recovered_at, (
        "恢复后的存活戳必须盖在重建连上的那一刻，⛔ 不许停在断线时刻"
    )
    assert scenario.exit_code == 0
