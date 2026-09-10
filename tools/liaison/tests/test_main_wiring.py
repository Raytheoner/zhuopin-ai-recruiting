"""第 7 章·服务接线（7.6 的进程侧 + 第 1 章凭据校验的回归）。"""

from __future__ import annotations

import ast
import pathlib
import queue
import re
from datetime import datetime, timedelta

import pytest

from tools.liaison import __main__ as liaison_main
from tools.liaison import session, session_client
from tools.liaison.storage import db as liaison_db

MAIN_SOURCE = pathlib.Path(liaison_main.__file__)
T0 = datetime(2026, 9, 9, 10, 0, 0, tzinfo=session.CHINA_TZ)


class RecordingSink:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def send(self, text: str) -> None:
        self.texts.append(text)


class StoppingEvent:
    """第 `after` 次询问时才说"停"。⛔ 不用真 threading.Event + sleep：那让用例不可复现。"""

    def __init__(self, after: int) -> None:
        self.after = after
        self.calls = 0

    def is_set(self) -> bool:
        self.calls += 1
        return self.calls > self.after


@pytest.fixture
def svc(tmp_path):
    conn = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(conn)
    return session.LiaisonSession(
        conn, RecordingSink(), liveness_path=tmp_path / "liveness.json"
    )


def test_apply_event_routes_connected_and_disconnected(svc):
    liaison_main.apply_connection_event(svc, liaison_main.EVENT_CONNECTED, T0)
    assert svc.state == session.STATE_CONNECTED
    liaison_main.apply_connection_event(
        svc, liaison_main.EVENT_DISCONNECTED, T0 + timedelta(minutes=1)
    )
    assert svc.state == session.STATE_DISCONNECTED


def test_apply_event_logs_and_survives_an_unknown_event(svc, caplog):
    """SDK 换版本多送一种事件 ⛔ 不许把值守线程打死。"""
    with caplog.at_level("ERROR"):
        liaison_main.apply_connection_event(svc, "reconnecting", T0)
    assert "reconnecting" in caplog.text
    assert svc.state == session.STATE_STARTING


def test_worker_uses_the_event_timestamp_not_the_processing_time(svc):
    """⚠️ 断线窗口的起点必须是**回调那一刻**，⛔ 不是值守线程处理它的时刻。"""
    events: queue.Queue = queue.Queue()
    events.put((liaison_main.EVENT_CONNECTED, T0))
    events.put((liaison_main.EVENT_DISCONNECTED, T0 + timedelta(minutes=5)))
    liaison_main.run_session_worker(
        svc,
        events,
        StoppingEvent(after=2),
        tick_interval=0.01,
        clock=lambda: T0 + timedelta(hours=9),  # 处理时刻故意远离事件时刻
    )
    started_at = svc.conn.execute("SELECT started_at FROM liaison_outage_window").fetchone()[0]
    assert started_at == session.format_instant(T0 + timedelta(minutes=5))


def test_worker_ticks_when_the_queue_stays_empty(svc, tmp_path):
    """队列空 = 一切正常 = 该盖存活戳。这条就是"空闲不误判"在进程侧的形式。

    ⚠️ 这里还要证伪 7.1 的红线在 `except queue.Empty:` 分支上的进程侧版本：
    参考服务的生产 bug 就是把"队列一段时间没收到事件"当成断线信号
    （`svc.on_disconnected(clock())` 塞进这个分支）。空转多轮之后，
    ⛔ 不许开出任何中断窗口，⛔ 状态也不许被搬去 STATE_DISCONNECTED——
    否则安静的下午会产生一串假告警，真正的断线反而被淹没在里面。
    """
    svc.on_connected(T0)
    events: queue.Queue = queue.Queue()
    liaison_main.run_session_worker(
        svc,
        events,
        StoppingEvent(after=2),
        tick_interval=0.01,
        clock=lambda: T0 + timedelta(hours=2),
    )
    import json

    payload = json.loads((tmp_path / "liveness.json").read_text(encoding="utf-8"))
    assert payload["stamp_at"] == session.format_instant(T0 + timedelta(hours=2))
    assert session.select_open_windows(svc.conn) == [], (
        "队列空闲不是断线信号，⛔ 不许因为几轮 queue.Empty 就开出中断窗口"
    )
    assert (
        svc.conn.execute("SELECT COUNT(*) FROM liaison_outage_window").fetchone()[0] == 0
    ), "空闲轮询期间 ⛔ 一个窗口都不该出现，不论开着还是已闭合"
    assert svc.state == session.STATE_CONNECTED


def test_main_still_refuses_to_start_without_credentials(tmp_path, monkeypatch, capsys):
    """第 1 章「凭据缺失时拒绝启动」的回归。⛔ 本章的接线不许绕过它。"""
    monkeypatch.setenv(liaison_main.DOTENV_PATH_ENV, str(tmp_path / "nope.env"))
    monkeypatch.delenv("HR_LIAISON_BOT_ID", raising=False)
    monkeypatch.delenv("HR_LIAISON_BOT_SECRET", raising=False)
    assert liaison_main.main() == liaison_main.EXIT_MISSING_CREDENTIALS
    assert "HR_LIAISON_BOT_ID" in capsys.readouterr().err


@pytest.fixture
def credentials_in_env(tmp_path, monkeypatch):
    monkeypatch.setenv(liaison_main.DOTENV_PATH_ENV, str(tmp_path / "nope.env"))
    monkeypatch.setenv("HR_LIAISON_BOT_ID", "fake-bot")
    monkeypatch.setenv("HR_LIAISON_BOT_SECRET", "fake-secret")


def test_main_exits_with_a_dedicated_code_when_the_sdk_is_missing(credentials_in_env, capsys):
    """SDK 装不上是配置问题，⛔ 不许进重试循环装作在等网络。"""

    def boom(_credentials, **_kwargs):
        raise ImportError("No module named 'aibot'")

    ran = []
    assert (
        liaison_main.main(client_builder=boom, runner=lambda connect: ran.append(1))
        == liaison_main.EXIT_SDK_UNAVAILABLE
    )
    assert ran == [], "⛔ 不许在 SDK 缺失时进入 run_forever"
    assert "aibot" in capsys.readouterr().err


def test_main_exits_when_the_sdk_surface_does_not_match(credentials_in_env, capsys):
    class BareClient:
        pass

    ran = []
    assert (
        liaison_main.main(client_builder=lambda _c, **_: BareClient(), runner=lambda c: ran.append(1))
        == liaison_main.EXIT_SDK_SURFACE_UNVERIFIED
    )
    assert ran == [], "⛔ 表面对不上时不许硬着头皮跑起来"


def test_main_exits_when_the_sdk_run_is_a_coroutine_function(credentials_in_env, capsys):
    """🔴 **护栏仍在的进程侧证据**（TD-19 落地后的新形态）。

    适配写好之后本模块调的是 `client.run()`，护栏就跟着挪到 `run` 上——⛔ 没有被
    删掉、没有被降级成警告。协程 `run()` 同步调用只返回一个协程对象、不执行任何
    网络动作，`run_forever` 会把它当成"连上后立刻断开"而永远退避重连，⛔ 不报错、
    ⛔ 没有任何症状。这条证明 `main()` 对这种表面**当场拒绝启动**。
    """

    class AsyncRunClient:
        def on(self, event, handler):
            pass

        async def run(self):  # pragma: no cover - 不应被真的调用到
            raise AssertionError("不应该走到这里——协程 run 必须被提前拒绝")

    ran = []
    assert (
        liaison_main.main(
            client_builder=lambda _c, **_: AsyncRunClient(),
            runner=lambda connect: ran.append(1),
        )
        == liaison_main.EXIT_SDK_SURFACE_UNVERIFIED
    )
    assert ran == [], "⛔ 协程 run 对不上契约时不许硬着头皮跑起来"
    assert "async def" in capsys.readouterr().err


def test_main_accepts_the_real_sdk_shape_and_hands_run_to_the_runner(credentials_in_env, tmp_path):
    """TD-19 的正面判据：真实 SDK 形状（`async def connect` + 同步 `run`）能起来，

    且交给 `run_forever` 的那个 callable 调到的是 **`client.run()`**——
    ⛔ 不是 `client.connect`（协程，同步调它什么都不会发生）。
    """
    ran = []

    class RealShapeClient:
        def on(self, event, handler):
            pass

        async def connect(self):  # pragma: no cover - ⛔ 不该被调
            raise AssertionError("⛔ 不该调 connect——阻塞入口是 run()")

        def run(self):
            ran.append(1)
            raise KeyboardInterrupt  # 停下 main()，⛔ 不真的驻留

    def session_builder():
        conn = liaison_db.get_connection(tmp_path / "liaison.db")
        liaison_db.init_schema(conn)
        return session.LiaisonSession(
            conn, RecordingSink(), liveness_path=tmp_path / "liveness.json"
        )

    assert (
        liaison_main.main(
            session_builder=session_builder,
            client_builder=lambda _c, **_: RealShapeClient(),
            runner=lambda connect: connect(),
        )
        == 0
    )
    assert ran == [1], "交给 runner 的 callable 必须真的调到 client.run()"


def test_self_check_runs_the_whole_startup_path_then_stops_before_connecting(
    credentials_in_env, capsys
):
    """TD-36：`--self-check` 把校验全跑完，⛔ 在建连前停下。

    ⛔ 它不是"跳过校验的开关"——下面两条断言一起才成立：
    ① 表面对不上时它**照样**以 `EXIT_SDK_SURFACE_UNVERIFIED` 拒绝（校验没被跳过）；
    ② 表面对得上时它返回 0 且 `runner` / `client.run()` 一次都没被调（没碰网络）。
    """

    class BareClient:
        pass

    assert (
        liaison_main.main(
            client_builder=lambda _c, **_: BareClient(),
            runner=lambda connect: connect(),
            self_check=True,
        )
        == liaison_main.EXIT_SDK_SURFACE_UNVERIFIED
    ), "⛔ 自检模式不许跳过 SDK 表面校验"

    ran = []
    ran_runner = []

    class RealShapeClient:
        def on(self, event, handler):
            pass

        def run(self):
            ran.append(1)

    assert (
        liaison_main.main(
            client_builder=lambda _c, **_: RealShapeClient(),
            runner=lambda connect: ran_runner.append(1),
            self_check=True,
        )
        == 0
    )
    assert ran == [], "⛔ 自检模式不许调 client.run()——那是唯一会碰网络的一步"
    assert ran_runner == [], "⛔ 自检模式不许进 run_forever"
    assert "--self-check" in capsys.readouterr().err


def test_main_wires_both_callbacks_into_the_queue(credentials_in_env, tmp_path):
    """happy path：回调只往队列里放 (事件, 时间)，⛔ 不碰库。"""
    handlers = {}

    class FakeClient:
        def on(self, event, handler):
            handlers[event] = handler

        def run(self):
            raise AssertionError("本用例不应真的建连")

    captured: dict = {}

    def fake_runner(connect):
        captured["connect"] = connect
        raise KeyboardInterrupt

    def session_builder():
        conn = liaison_db.get_connection(tmp_path / "liaison.db")
        liaison_db.init_schema(conn)
        return session.LiaisonSession(
            conn, RecordingSink(), liveness_path=tmp_path / "liveness.json"
        )

    assert (
        liaison_main.main(
            session_builder=session_builder,
            client_builder=lambda _c, **_: FakeClient(),
            runner=fake_runner,
        )
        == 0
    )
    # ⚠️ 2026-09-09（TD-39）从「恰好两个」改成「恰好等于 SUBSCRIBED_EVENTS」：
    # `error` 事件必须一并接上，漏掉它 pyee 会直接 raise、打断整条重连链。
    # ⛔ 这不是放宽——判据仍是**相等**，多接一个没登记的事件同样红。
    assert set(handlers) == set(session_client.SUBSCRIBED_EVENTS)
    assert callable(captured["connect"])


def test_this_chapter_wires_no_message_handling():
    """opener：本章 ⛔ 不实现归档／入队／群通知。这条断言让"顺手接上"当场变红。

    判据只看 `tools.liaison.` 开头的导入——⛔ 不能只匹配模块名里有没有 "queue"：
    标准库 `queue` 是本文件自己要用的，那样写会把它误伤成违规。
    """
    tree = ast.parse(MAIN_SOURCE.read_text(encoding="utf-8"), filename=str(MAIN_SOURCE))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    own = [name for name in imported if name.startswith("tools.liaison")]
    forbidden = [name for name in own if "archive" in name or "queue" in name]
    assert forbidden == [], f"__main__.py 接了本章范围外的模块：{forbidden}"


def test_main_module_never_uses_a_with_statement():
    tree = ast.parse(MAIN_SOURCE.read_text(encoding="utf-8"), filename=str(MAIN_SOURCE))
    assert not [n for n in ast.walk(tree) if isinstance(n, (ast.With, ast.AsyncWith))]


# ─────────────────────────────────────────────────────────────────────────
# 结构断言：让"按无消息判断线"这个 bug 在 __main__.py 里也写不出来
# ─────────────────────────────────────────────────────────────────────────
#
# session.py 结构上表达不了消息时序（那条断言见
# test_session_liveness.py::test_session_module_has_no_message_timing_state），
# 但能表达它的恰恰是本文件：`run_session_worker` 的 `except queue.Empty:`
# 分支字面意思就是"一段时间没有事件"。参考服务的生产 bug 正是在这个位置把
# 它当成了断线信号。口径与 test_session_liveness.py 保持一致（只看标识符，
# ⛔ 不看注释与字符串——上面这段说明性文字合法，把它变成变量名才是问题）。

_FORBIDDEN_LIVENESS_IDENTIFIER = re.compile(r"(?i)(last_?message|no_?message|idle|silence|quiet)")


def _liveness_identifiers(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def test_main_module_has_no_message_timing_state():
    """7.1 逐字，姊妹版：⛔ 不许在 __main__.py 里出现与消息时序有关的标识符。

    红了不要往正则里加豁免——先问"这个模块为什么需要知道消息的时间"。
    """
    tree = ast.parse(MAIN_SOURCE.read_text(encoding="utf-8"), filename=str(MAIN_SOURCE))
    offenders = sorted(
        n for n in _liveness_identifiers(tree) if _FORBIDDEN_LIVENESS_IDENTIFIER.search(n)
    )
    assert offenders == [], f"__main__.py 出现了与消息时序有关的标识符：{offenders}"


def test_the_main_module_structural_guard_actually_catches_a_break():
    """证伪：喂一段带 idle_since 的源码，上面那条判据必须抓到。"""
    tree = ast.parse(
        "def run(events, idle_since):\n"
        "    if events.empty() and idle_since:\n"
        "        return 'disconnected'\n"
    )
    offenders = sorted(
        n for n in _liveness_identifiers(tree) if _FORBIDDEN_LIVENESS_IDENTIFIER.search(n)
    )
    assert offenders == ["idle_since"]


# ─────────────────────────────────────────────────────────────────────────
# TD-39·N-0 兜底：存活戳看门狗必须真的被接进 main()
# ─────────────────────────────────────────────────────────────────────────


def test_main_starts_the_liveness_watchdog_wired_to_the_same_loop_stopper(
    credentials_in_env, tmp_path
):
    """🔴 **没有这条，看门狗就是一段没人调的死代码。**

    TD-39 的第二层（N-0）是：接收 task 死掉之后 `loop.run_forever()` 照样挂着 ⇒
    `client.run()` 永不返回 ⇒ 外层 `run_forever` **一次都不会触发**。补看门狗只有
    在它①真的被起起来、②`request_rebuild` 真的接到**同一个** `LoopStopper`
    （就是 `make_sdk_connect` 用的那个）时才有意义。接错对象 ⇒ 停的是 `None`，
    兜底层看起来在跑、实际什么都停不了，且 ⛔ 没有任何症状。
    """
    stoppers = []
    watchdog_kwargs = {}

    class FakeClient:
        def on(self, event, handler):
            pass

        def run(self):
            raise AssertionError("本用例不应真的建连")

    real_make = session_client.make_sdk_connect

    def spying_make(factory, *, on_connected, on_disconnected, loop_stopper=None, on_activity=None):
        stoppers.append(loop_stopper)
        return real_make(
            factory,
            on_connected=on_connected,
            on_disconnected=on_disconnected,
            loop_stopper=loop_stopper,
            on_activity=on_activity,
        )

    def fake_watchdog(**kwargs):
        watchdog_kwargs.update(kwargs)

    def session_builder():
        conn = liaison_db.get_connection(tmp_path / "liaison.db")
        liaison_db.init_schema(conn)
        return session.LiaisonSession(
            conn, RecordingSink(), liveness_path=tmp_path / "liveness.json"
        )

    def fake_runner(connect):
        raise KeyboardInterrupt

    original = session_client.make_sdk_connect
    session_client.make_sdk_connect = spying_make
    try:
        assert (
            liaison_main.main(
                session_builder=session_builder,
                client_builder=lambda _c, **_: FakeClient(),
                runner=fake_runner,
                watchdog=fake_watchdog,
            )
            == 0
        )
    finally:
        session_client.make_sdk_connect = original

    assert len(stoppers) == 1 and isinstance(stoppers[0], session_client.LoopStopper), (
        "main() 必须把一个 LoopStopper 传给 make_sdk_connect，⛔ 不许让它自己造一个"
        "——自己造的那个外面拿不到，看门狗就停不了任何东西"
    )
    assert watchdog_kwargs, "看门狗必须被真的起起来，⛔ 不许只定义不调用"
    assert watchdog_kwargs["request_rebuild"] == stoppers[0].request_stop, (
        "看门狗的 request_rebuild 必须是**同一个** LoopStopper 的 request_stop"
    )
    assert callable(watchdog_kwargs["read_liveness"])
    assert callable(watchdog_kwargs["clock"])
    assert callable(watchdog_kwargs["should_stop"])
    # TD-42：终止路径的三样也要接上（`terminate` 用默认的 `terminate_process`，
    # ⛔ 不在这里替换——本用例的假看门狗根本不调它）
    assert "alert_sink" in watchdog_kwargs
    assert watchdog_kwargs["ledger_path"] == liaison_main.DEFAULT_WATCHDOG_LEDGER_PATH


def test_main_reads_the_stamp_the_session_thread_actually_writes(credentials_in_env, tmp_path):
    """看门狗读的戳必须是值守线程**真的在写**的那一份。

    ⛔ 挡的是两处路径各写各的：看门狗盯着一个永远不更新的文件 ⇒ 每 3 分钟拆一次
    健康连接；或盯着一个不存在的文件 ⇒ 永远不触发。两种都无症状。
    """
    stamp_path = tmp_path / "liveness.json"
    session.effect_write_liveness_stamp(stamp_path, state=session.STATE_CONNECTED, now=T0)
    assert liaison_main.read_liveness_payload(stamp_path)["stamp_at"] == session.format_instant(T0)
    assert liaison_main.read_liveness_payload(tmp_path / "缺席.json") is None


def test_watchdog_default_path_matches_the_default_liveness_path():
    """默认路径两边必须是**同一个常量**，⛔ 不许各写各的字面量。"""
    source = MAIN_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MAIN_SOURCE))
    funcs = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "read_liveness_payload"
    ]
    assert len(funcs) == 1, "期望恰好一处 read_liveness_payload 定义"
    default = funcs[0].args.defaults[-1]
    assert isinstance(default, ast.Attribute) and default.attr == "DEFAULT_LIVENESS_PATH", (
        "默认存活戳路径必须直接引用 session.DEFAULT_LIVENESS_PATH，"
        "⛔ 不许在本文件里另写一份路径字面量——两处真源必然漂移，且漂移无症状"
    )
