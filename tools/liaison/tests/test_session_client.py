"""第 7 章·重连接线（7.6 / 7.9）。

opener 约束 4 逐字：用 SDK 内置重连；测试用 fake 连接对象验退避递增与"服务不退出"，
⛔ 单测联真企微。本文件里没有任何一处真实网络调用，也没有一处 sleep。
"""

from __future__ import annotations

import pytest

from tools.liaison import session_client


class FakeClock:
    """每调用一次前进 `step` 秒的单调时钟。⛔ 不用真 time.monotonic：会让用例变慢且不可复现。"""

    def __init__(self, step: float = 0.0) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        current = self.value
        self.value += self.step
        return current


class _StopLoop(Exception):
    """测试专用：让"永不返回"的循环停下来的唯一手段。

    ⛔ 不给 run_forever 加"最多重试 N 次"的生产参数来方便测试——那个参数一旦
    存在，就迟早会有人在生产配置里把它设成一个有限值，服务于是会在某次长时间
    断网后**安静地退出**，而 spec 要的是"自动重试直至成功、服务不退出"。
    """


def make_sleep(stop_after: int):
    delays: list[float] = []

    def sleep(seconds: float) -> None:
        delays.append(seconds)
        if len(delays) >= stop_after:
            raise _StopLoop
    return sleep, delays


@pytest.mark.parametrize(
    ("attempt", "expected"),
    [(1, 1.0), (2, 2.0), (3, 4.0), (4, 8.0), (5, 16.0), (6, 30.0), (7, 30.0), (99, 30.0)],
)
def test_backoff_doubles_then_caps(attempt, expected):
    assert session_client.compute_backoff_delay(attempt) == expected


def test_backoff_never_returns_zero():
    """⛔ 0 就是紧密循环：spec「服务不退出、不占满 CPU」的另一半。"""
    for attempt in range(1, 200):
        assert session_client.compute_backoff_delay(attempt) > 0


def test_backoff_rejects_a_non_positive_attempt():
    with pytest.raises(ValueError):
        session_client.compute_backoff_delay(0)


def test_backoff_does_not_blow_up_on_a_huge_attempt_count():
    """连续失败一整夜也不许把 2 ** attempt 这个大整数算出来。"""
    assert session_client.compute_backoff_delay(10_000) == session_client.MAX_BACKOFF_SECONDS


def test_run_forever_backs_off_between_failed_attempts_and_never_returns():
    """spec Scenario「长时间无法连接」：重试间隔随失败次数增长，服务不退出。"""
    attempts = []

    def connect():
        attempts.append(1)
        raise ConnectionRefusedError("网线没插")

    sleep, delays = make_sleep(stop_after=5)
    with pytest.raises(_StopLoop):
        session_client.run_forever(connect, sleep=sleep, monotonic=FakeClock())

    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0]
    assert len(attempts) == 5
    assert delays == sorted(delays) and len(set(delays)) == 5, "间隔必须严格递增"


def test_run_forever_sleeps_even_when_connect_returns_immediately():
    """连上又立刻掉线（connect 正常返回）同样 ⛔ 不许变成紧密循环。

    这条比"connect 抛异常"更阴险：不抛异常的立即返回看起来像"成功了"，
    没有退避就是一个满速自旋的 while True。
    """
    calls = []

    def connect():
        calls.append(1)  # 立刻返回，不抛异常

    sleep, delays = make_sleep(stop_after=3)
    with pytest.raises(_StopLoop):
        session_client.run_forever(connect, sleep=sleep, monotonic=FakeClock())
    assert delays == [1.0, 2.0, 4.0]
    assert len(calls) == 3


def test_run_forever_resets_the_backoff_after_a_healthy_session():
    """连着跑了足够久再断 ⇒ 退避从头开始。

    ⛔ 不重置的话，一个连了三天的服务在第一次断线时就要等满 30 秒才重连——
    退避是为了保护"一直连不上"的场景，不是惩罚"连得挺好偶尔断一次"。
    """

    def connect():
        raise ConnectionResetError("对端断开")

    sleep, delays = make_sleep(stop_after=4)
    with pytest.raises(_StopLoop):
        session_client.run_forever(
            connect,
            sleep=sleep,
            monotonic=FakeClock(step=session_client.HEALTHY_SESSION_SECONDS + 1),
        )
    assert delays == [1.0, 1.0, 1.0, 1.0]


def test_run_forever_reports_each_failed_attempt():
    seen = []

    def connect():
        raise TimeoutError("超时")

    sleep, _ = make_sleep(stop_after=2)
    with pytest.raises(_StopLoop):
        session_client.run_forever(
            connect,
            sleep=sleep,
            monotonic=FakeClock(),
            on_attempt_failed=lambda attempt, delay, exc: seen.append((attempt, delay, type(exc))),
        )
    assert seen == [(1, 1.0, TimeoutError), (2, 2.0, TimeoutError)]


def test_run_forever_lets_keyboard_interrupt_out():
    """⛔ 不许吞 BaseException：Ctrl-C 与 launchd 的 SIGTERM 必须能停下服务。"""

    def connect():
        raise KeyboardInterrupt

    sleep, _ = make_sleep(stop_after=99)
    with pytest.raises(KeyboardInterrupt):
        session_client.run_forever(connect, sleep=sleep, monotonic=FakeClock())


# ─────────────────────────────────────────────────────────────────────────
# SDK 表面：装了 SDK 的 venv 里才跑；根 venv 里 skip 是预期行为（design D10）
# ─────────────────────────────────────────────────────────────────────────


def test_ws_options_disable_the_reconnect_attempt_ceiling():
    """findings 2026-09-08 实测：max_reconnect_attempts 默认 10，-1 才是无限。

    spec「断线后自动恢复接收」要求"自动重试**直至成功**"，⛔ 不许用默认值 10——
    10 次退避封顶 30 秒 ≈ 5 分钟后彻底放弃，之后服务还活着但永远不再连上，
    而这个状态**没有任何症状**。
    """
    pytest.importorskip("aibot", reason="aibot SDK 只装在 tools/liaison/.venv，根 venv skip 是预期")
    from tools.liaison.config import LiaisonCredentials

    options = session_client.build_ws_options(
        LiaisonCredentials(bot_id="fake-bot", bot_secret="fake-secret")
    )
    assert options.max_reconnect_attempts == session_client.UNLIMITED_RECONNECT_ATTEMPTS
    assert options.heartbeat_interval == session_client.DEFAULT_HEARTBEAT_SECONDS
    assert options.bot_id == "fake-bot"


def test_make_sdk_connect_refuses_a_client_missing_the_expected_surface():
    """SDK 换版本／表面对不上时 ⛔ 必须当场炸，不许"看起来接上了"。

    静默的错接线在这里的后果是：服务起来了、日志一片正常，但断线事件永远
    不会到达 LiaisonSession——于是中断窗口一条都不会有，"没有告警"被当成
    "一切正常"。
    """

    class BareClient:
        pass

    with pytest.raises(session_client.SdkSurfaceUnverifiedError) as excinfo:
        session_client.make_sdk_connect(
            BareClient(), on_connected=lambda: None, on_disconnected=lambda: None
        )
    for attr in session_client.REQUIRED_CLIENT_ATTRS:
        assert attr in str(excinfo.value)


def test_make_sdk_connect_refuses_a_coroutine_function_connect():
    """🔴 controller ruling（docs/findings/2026-09-09-aibot-wsclient-表面实测.md）：

    真实 SDK 的 `WSClient.connect` 是 `async def`。同步调用它只会返回一个协程
    对象、不执行任何网络动作——`run_forever` 会把"刚连上"误判成"立刻又断开
    了"，从此永远退避重连，⛔ 不报错、⛔ 没有任何症状。`make_sdk_connect` 必须
    在接线阶段就当场拒绝这种表面，而不是把一个不满足"阻塞到断开为止"契约的
    callable 交给 run_forever。
    """
    events = {}

    class AsyncConnectClient:
        def on(self, event, handler):
            events[event] = handler

        async def connect(self):  # pragma: no cover - 不应被真的调用到
            raise AssertionError("协程 connect 必须在接线阶段就被拒绝，不应被调用")

    with pytest.raises(session_client.SdkSurfaceUnverifiedError) as excinfo:
        session_client.make_sdk_connect(
            AsyncConnectClient(), on_connected=lambda: None, on_disconnected=lambda: None
        )
    assert "client.run()" in str(excinfo.value)
    assert events == {}, "⛔ 表面拒绝之前不许先订阅事件——半接线比不接线更危险"


def test_make_sdk_connect_subscribes_both_events_and_returns_a_blocking_callable():
    """用 fake 连接对象验接线形状，⛔ 不联真企微。"""
    events = {}
    ran = []

    class FakeClient:
        def on(self, event, handler):
            events[event] = handler

        def connect(self):
            ran.append(1)

    connect = session_client.make_sdk_connect(
        FakeClient(),
        on_connected=lambda: events.setdefault("_called_connected", True),
        on_disconnected=lambda: events.setdefault("_called_disconnected", True),
    )
    assert set(events) == {session_client.EVENT_CONNECTED, session_client.EVENT_DISCONNECTED}
    connect()
    assert ran == [1]
