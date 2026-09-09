"""TD-39·重连链：`error` 事件监听器 + 存活戳看门狗。

**这个文件钉的是一个无症状故障。** 连接断开后 SDK 只重连一次，那一次失败时
`on_error` 抛出的异常会把唯一还能触发重连的 task 杀死，于是永不再试：进程仍活着、
日志一片正常、`liveness.json` 永远停在 `disconnected`、中断窗口永不闭合、恢复告警
永不发出。⛔ 没有任何一处会报错。

⚠️ 现成的反面教材：TD-38 的 `assert options.heartbeat_interval == DEFAULT_HEARTBEAT_MS`
是同义反复（拿传进去的值跟它自己比），单位错成什么样都绿——那个 bug 因此活到真实
建连才暴露。本文件的每一条断言都必须在**修复前真的红**，红的原文见 0909AH 的报告。

⛔ 本文件不碰网络、不 sleep、不 import aibot（根 venv 按 design D10 不装 SDK）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from tools.liaison import session_client

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
    triggered = session_client.check_liveness_once(
        read_stamp_at=lambda: stamp(session_client.STALE_LIVENESS_SECONDS + 30, base=base),
        clock=lambda: base,
        request_rebuild=lambda: rebuilds.append(1) or True,
    )
    assert triggered is True
    assert rebuilds == [1]


def test_watchdog_stays_quiet_within_the_threshold():
    base = datetime(2026, 9, 9, 12, 0, 0, tzinfo=CHINA_TZ)
    rebuilds = []
    triggered = session_client.check_liveness_once(
        read_stamp_at=lambda: stamp(1.0, base=base),
        clock=lambda: base,
        request_rebuild=lambda: rebuilds.append(1) or True,
    )
    assert triggered is False
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
        read_stamp_at=lambda: stamp(session_client.STALE_LIVENESS_SECONDS + 5, base=base),
        clock=lambda: base,
        request_rebuild=lambda: rebuilds.append(1) or True,
        sleep=naps.append,
        should_stop=lambda: next(rounds),
    )
    assert len(rebuilds) == 3, "每一轮陈旧都要请求一次重建"
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
            read_stamp_at=exploding_reader,
            clock=lambda: datetime(2026, 9, 9, 12, 0, 0, tzinfo=CHINA_TZ),
            request_rebuild=lambda: rebuilds.append(1) or True,
            sleep=lambda _: None,
            should_stop=lambda: next(rounds),
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
