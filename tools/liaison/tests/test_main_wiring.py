"""第 7 章·服务接线（7.6 的进程侧 + 第 1 章凭据校验的回归）。"""

from __future__ import annotations

import ast
import pathlib
import queue
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
    """队列空 = 一切正常 = 该盖存活戳。这条就是"空闲不误判"在进程侧的形式。"""
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

    def boom(_credentials):
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
        liaison_main.main(client_builder=lambda _c: BareClient(), runner=lambda c: ran.append(1))
        == liaison_main.EXIT_SDK_SURFACE_UNVERIFIED
    )
    assert ran == [], "⛔ 表面对不上时不许硬着头皮跑起来"


def test_main_exits_when_the_sdk_connect_is_a_coroutine_function(credentials_in_env, capsys):
    """🔴 controller ruling：真实 SDK 的 `WSClient.connect` 是 `async def`。

    同步调用它只会拿到一个协程对象、什么网络动作都不执行——`run_forever` 会把
    "刚连上"误判成"立刻又断开了"，然后永远退避重连、⛔ 不报错、⛔ 没有任何
    症状（docs/findings/2026-09-09-aibot-wsclient-表面实测.md「遗留发现」）。
    这条测试证明 `make_sdk_connect` 在 `main()` 里对这种表面**当场拒绝启动**，
    ⛔ 不静默进入自旋重连。
    """

    class AsyncConnectClient:
        def on(self, event, handler):
            pass

        async def connect(self):  # pragma: no cover - 不应被真的调用到
            raise AssertionError("不应该走到这里——协程 connect 必须被提前拒绝")

    ran = []
    assert (
        liaison_main.main(
            client_builder=lambda _c: AsyncConnectClient(),
            runner=lambda connect: ran.append(1),
        )
        == liaison_main.EXIT_SDK_SURFACE_UNVERIFIED
    )
    assert ran == [], "⛔ 协程 connect 对不上契约时不许硬着头皮跑起来"
    err = capsys.readouterr().err
    assert "client.run()" in err
    assert "2026-09-09-aibot-wsclient-表面实测.md" in err


def test_main_wires_both_callbacks_into_the_queue(credentials_in_env, tmp_path):
    """happy path：回调只往队列里放 (事件, 时间)，⛔ 不碰库。"""
    handlers = {}

    class FakeClient:
        def on(self, event, handler):
            handlers[event] = handler

        def connect(self):
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
            client_builder=lambda _c: FakeClient(),
            runner=fake_runner,
        )
        == 0
    )
    assert set(handlers) == {session_client.EVENT_CONNECTED, session_client.EVENT_DISCONNECTED}
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
