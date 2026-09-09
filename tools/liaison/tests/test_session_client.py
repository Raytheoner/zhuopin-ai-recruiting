"""第 7 章·重连接线（7.6 / 7.9）。

opener 约束 4 逐字：用 SDK 内置重连；测试用 fake 连接对象验退避递增与"服务不退出"，
⛔ 单测联真企微。本文件里没有任何一处真实网络调用，也没有一处 sleep。
"""

from __future__ import annotations

import ast
import pathlib

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
    # ⛔ 不许写成 `== session_client.DEFAULT_HEARTBEAT_MS`——那是拿传进去的值跟它自己比，
    # 单位错成什么样都绿。TD-38 这个 bug 正是这样活到真实建连才暴露的（2026-09-09，
    # 心跳 ×1000、44 秒被企微 45009 判死）。这里必须是**绝对值**：SDK 的
    # `heartbeat_interval` 单位是毫秒（aibot/types.py:49），30 秒 = 30000。
    assert options.heartbeat_interval == 30_000
    assert options.bot_id == "fake-bot"


def test_ws_options_source_pins_the_reconnect_ceiling_to_unlimited():
    """AST 级别的钉子：即使根 venv 没装 aibot 也要能挡住"删掉这一行"。

    上面那条 `test_ws_options_disable_the_reconnect_attempt_ceiling` 靠
    `importorskip("aibot")` 才能跑，而根 venv 按 design D10 故意不装 SDK——
    全量 pytest 走的正是根 venv，那条测试在这里永远 skip，删掉
    `max_reconnect_attempts=` 那一行不会让根 venv 的套件变红。这条断言不依赖
    SDK：直接解析源码，钉死 `aibot.WSClientOptions(...)` 调用点上必须显式传
    `max_reconnect_attempts=UNLIMITED_RECONNECT_ATTEMPTS`——是本章其它地方已经
    在用的同一手法（AST 结构断言，见 test_session_liveness.py）。⛔ 不装 SDK、
    不 fake、不删掉上面那条 importorskip 用例——这条是**额外**的一道岗。
    """
    source_path = pathlib.Path(session_client.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "WSClientOptions"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "aibot"
    ]
    assert len(calls) == 1, (
        f"期望恰好一处 aibot.WSClientOptions(...) 调用，实际找到 {len(calls)} 处"
    )
    call = calls[0]

    ceiling_kwargs = [kw for kw in call.keywords if kw.arg == "max_reconnect_attempts"]
    assert len(ceiling_kwargs) == 1, (
        "aibot.WSClientOptions(...) 必须显式传 max_reconnect_attempts=—— "
        "没传就是走 SDK 默认值 10，5 分钟退避耗尽后服务活着但永不重连，且没有任何症状"
    )
    value_node = ceiling_kwargs[0].value
    assert isinstance(value_node, ast.Name) and value_node.id == "UNLIMITED_RECONNECT_ATTEMPTS", (
        "max_reconnect_attempts= 必须传本模块的 UNLIMITED_RECONNECT_ATTEMPTS 常量（值 -1），"
        "⛔ 不许改成别的字面量或换个名字的变量"
    )


def test_ws_options_source_pins_the_heartbeat_interval_to_milliseconds():
    """AST 级别的钉子：把心跳常量钉在 **30000（毫秒）**，⛔ 挡住有人把它改回 30。

    ⚠️ **为什么是 30000 而不是 30**：SDK 的 `heartbeat_interval` 单位是**毫秒**
    （`aibot/types.py:49`：`heartbeat_interval: int = 30000`，docstring 明写「心跳间隔
    （毫秒）」；`aibot/ws.py:299` 实际 `asyncio.sleep(interval / 1000)`）。这个数字
    读起来像"30 秒"，所以每一个只扫一眼的人都会想把它"修"成 30——那正是 TD-38：
    2026-09-09 首次真实建连时传进去的 30 被解读成 **30 毫秒**，心跳频率 ×1000
    （44 秒内 1399 次），被企微以 45009 限流判死，服务保持不了在线。

    上面那条值断言靠 `importorskip("aibot")`，根 venv 按 design D10 不装 SDK ⇒ 恒 skip，
    全量 pytest 走的正是根 venv。所以那条**挡不住回退**，这条才挡得住——同
    `test_ws_options_source_pins_the_reconnect_ceiling_to_unlimited` 的手法。
    """
    source_path = pathlib.Path(session_client.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))

    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in ([node.target] if isinstance(node, ast.AnnAssign) else node.targets)
        if isinstance(target, ast.Name) and target.id == "DEFAULT_HEARTBEAT_MS"
    ]
    assert len(assignments) == 1, (
        f"期望恰好一处 DEFAULT_HEARTBEAT_MS = ... 赋值，实际找到 {len(assignments)} 处。"
        "⛔ 常量名必须带 _MS：叫 _SECONDS 而存毫秒值正是 TD-38 的成因"
    )
    value_node = assignments[0].value
    assert isinstance(value_node, ast.Constant) and value_node.value == 30_000, (
        "DEFAULT_HEARTBEAT_MS 必须是字面量 30_000。SDK 的 heartbeat_interval 单位是毫秒，"
        "写 30 会被解读成 30 毫秒 ⇒ 心跳 ×1000 ⇒ 企微 45009 限流、连接 44 秒即死（TD-38）"
    )

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "WSClientOptions"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "aibot"
    ]
    assert len(calls) == 1
    heartbeat_kwargs = [kw for kw in calls[0].keywords if kw.arg == "heartbeat_interval"]
    assert len(heartbeat_kwargs) == 1, (
        "aibot.WSClientOptions(...) 必须显式传 heartbeat_interval=——不传就是走 SDK 默认，"
        "本模块对心跳口径的控制权就没了"
    )


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
            BareClient, on_connected=lambda: None, on_disconnected=lambda: None
        )
    for attr in session_client.REQUIRED_CLIENT_ATTRS:
        assert attr in str(excinfo.value)


def test_make_sdk_connect_refuses_a_coroutine_function_run():
    """🔴 **护栏仍在的证据**（TD-19 落地后的新形态）。

    TD-19 之前守的是"`connect` 是协程 ⇒ 拒绝"；适配写好之后本模块调的是
    `client.run()`，护栏就必须跟着挪到 `run` 上——⛔ 而不是被删掉或降级成警告。
    协程 `run()` 同步调用只返回一个协程对象、不执行任何网络动作，`run_forever`
    会把它当成"连上后立刻断开"，从此满速退避重连：⛔ 不报错、⛔ 没有任何症状。

    ⚠️ 同时断言"表面拒绝之前一个事件都没订阅"：半接线比不接线更危险。
    """
    events = {}

    class AsyncRunClient:
        def on(self, event, handler):
            events[event] = handler

        async def run(self):  # pragma: no cover - 不应被真的调用到
            raise AssertionError("协程 run 必须在接线阶段就被拒绝，不应被调用")

    with pytest.raises(session_client.SdkSurfaceUnverifiedError) as excinfo:
        session_client.make_sdk_connect(
            AsyncRunClient, on_connected=lambda: None, on_disconnected=lambda: None
        )
    assert "async def" in str(excinfo.value)
    assert events == {}, "⛔ 表面拒绝之前不许先订阅事件——半接线比不接线更危险"


def test_make_sdk_connect_refuses_a_run_that_needs_arguments():
    """`run` 将来若要参数，`run()` 会抛 TypeError——而 run_forever 会把它当"没连上"

    吞掉重试。一个永不自愈的接口变更会伪装成"网络一直不好"，⛔ 必须当场拒绝启动。
    """

    class NeedsArgsClient:
        def on(self, event, handler):
            pass

        def run(self, forever):  # pragma: no cover - 不应被真的调用到
            raise AssertionError("不应被调用")

    with pytest.raises(session_client.SdkSurfaceUnverifiedError) as excinfo:
        session_client.make_sdk_connect(
            NeedsArgsClient, on_connected=lambda: None, on_disconnected=lambda: None
        )
    assert "零参数" in str(excinfo.value)


def test_make_sdk_connect_accepts_the_real_sdk_shape_async_connect_plus_sync_run():
    """TD-19 的正面判据：真实 SDK 的形状（`async def connect` + 同步 `run`）必须被接受。

    ⚠️ `connect` 是协程**不再**是拒绝理由——本模块从此不调它。⛔ 但这不等于放宽了
    护栏：护栏挪到了真正被调用的 `run` 上（见上面两条）。
    """
    events = {}
    ran = []

    class RealShapeClient:
        def on(self, event, handler):
            events[event] = handler

        async def connect(self):  # pragma: no cover - 本模块 ⛔ 不调它
            raise AssertionError("⛔ 不该调 connect——阻塞入口是 run()")

        def run(self):
            ran.append(1)

    connect = session_client.make_sdk_connect(
        RealShapeClient, on_connected=lambda: None, on_disconnected=lambda: None
    )
    assert set(events) == set(session_client.SUBSCRIBED_EVENTS)
    connect()
    assert ran == [1], "交给 run_forever 的 callable 必须真的调到 client.run()"


def test_make_sdk_connect_subscribes_both_events_and_returns_a_blocking_callable():
    """用 fake 连接对象验接线形状，⛔ 不联真企微。

    ⚠️ 事件订阅发生在 **make 的那一刻**、不是第一次 connect 的时候——接线出错要在
    启动时就现形，⛔ 不许拖到第一次建连尝试（那时 run_forever 已经会吞异常了）。
    """
    events = {}
    fired = []
    ran = []

    class FakeClient:
        def on(self, event, handler):
            events[event] = handler

        def run(self):
            ran.append(1)

    connect = session_client.make_sdk_connect(
        FakeClient,
        on_connected=lambda: fired.append("connected"),
        on_disconnected=lambda: fired.append("disconnected"),
    )
    # ⚠️ 2026-09-09（TD-39）：清单从两个变成三个（补 `error`）。判据仍是**相等**，
    # ⛔ 不是"至少包含"——多接一个没登记的事件同样必须红。
    assert set(events) == set(session_client.SUBSCRIBED_EVENTS)
    events[session_client.EVENT_CONNECTED]()
    events[session_client.EVENT_DISCONNECTED]("对端断开")  # SDK 会带 reason 参数
    assert fired == ["connected", "disconnected"]
    connect()
    assert ran == [1]


def test_make_sdk_connect_builds_a_fresh_client_for_every_attempt():
    """🔴 每次建连尝试都必须拿一个**全新**的连接对象。

    实测 `aibot==1.0.2`：`WSClient.connect` 开头是 `if self._started: return self`，
    而 `_started` 只有 `disconnect()` 会清。同一个对象第二次 `run()` ⇒ connect 立刻
    返回 ⇒ `loop.run_forever()` 挂在一个空转的事件循环上 ⇒ 进程活着、日志正常、
    **永远不再连上**，⛔ 没有任何症状。这条断言就是防它。
    """
    built = []
    wired = []

    class OneShotClient:
        def __init__(self) -> None:
            self.runs = 0
            built.append(self)

        def on(self, event, handler):
            wired.append((id(self), event))

        def run(self):
            self.runs += 1

    connect = session_client.make_sdk_connect(
        OneShotClient, on_connected=lambda: None, on_disconnected=lambda: None
    )
    connect()
    connect()
    connect()
    assert len(built) == 3, f"三次尝试应当造出三个连接对象，实际 {len(built)}"
    assert [c.runs for c in built] == [1, 1, 1], "⛔ 同一个对象不许被 run 两次"
    expected = 3 * len(session_client.SUBSCRIBED_EVENTS)
    assert len(wired) == expected, (
        f"每一个新对象都要重新订阅 SUBSCRIBED_EVENTS 里的全部事件（期望 {expected} 次）"
    )
    assert len({ident for ident, _ in wired}) == 3


def test_make_sdk_connect_verifies_the_surface_before_run_forever_can_swallow_it():
    """护栏必须在 `make` 时就响，⛔ 不许挪进返回的闭包。

    `SdkSurfaceUnverifiedError` 是 `RuntimeError`，而 `run_forever` 把任何
    `Exception` 都当成"这次没连上"吞掉重试。校验一旦挪进闭包，服务会安静地
    每隔几秒重试一个**永远不会成功**的接线——护栏等于被拆掉。
    """
    calls = []

    class BareClient:
        pass

    def factory():
        calls.append(1)
        return BareClient()

    with pytest.raises(session_client.SdkSurfaceUnverifiedError):
        session_client.make_sdk_connect(
            factory, on_connected=lambda: None, on_disconnected=lambda: None
        )
    assert calls == [1], "校验应当在 make 时就跑掉，⛔ 不许等到第一次 connect"


def test_verify_client_surface_passes_a_client_that_matches_the_contract():
    """证伪的另一半：合契约的表面 ⛔ 不许被误杀（否则上面几条只是"永远拒绝"）。"""

    class GoodClient:
        def on(self, event, handler):
            pass

        def run(self):
            pass

    session_client.verify_client_surface(GoodClient())  # ⛔ 不抛
