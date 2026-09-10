"""8.5bis·SDK `message` 事件接线（订阅 → 回调只放帧 → 值守线程落库）。

**本文件守的是"运行期入口真的接上了"**。第 4 章（归档）与第 5 章（入队）各自
6/6、10/10 全绿了整整一天，而现网 `liaison_message` 恒为 0 行——因为 `message`
根本没被订阅，且漏订阅**不报错**。⛔ 这里的每一条都是那个缺口的判据，删任何一条
都会让"接线被拆掉"重新变成无症状故障。

⚠️ 本文件 ⛔ 不 `import aibot`：SDK 只装在 `tools/liaison/.venv`，模块层 import 会让
根 venv 的全量 pytest 在 collect 阶段整个红掉（见 `session_client.build_ws_options`
的 docstring）。所有 SDK 行为都用替身复刻，形状依据写在替身自己的 docstring 里。
"""

from __future__ import annotations

import ast
import logging
import pathlib
import queue
from datetime import datetime, timedelta

import pytest

from tools.liaison import __main__ as liaison_main
from tools.liaison import frames, session, session_client
from tools.liaison.storage import db as liaison_db

MAIN_SOURCE = pathlib.Path(liaison_main.__file__)
T0 = datetime(2026, 9, 10, 10, 0, 0, tzinfo=session.CHINA_TZ)

ADMITTED_USERID = "tanglp"
OUTSIDER_USERID = "someone-else"

#: ⚠️ **构造的占位路径，⛔ 不是真实企微帧的形状证据。**
#:
#: 键名刻意取成 `stand_in_*` 这种一眼就不是企微字段的名字：真实映射至今没有依据
#: （见 `frames.py` 模块 docstring 第二节），而一份"看起来很像真的"的占位映射，
#: 下一个人很容易顺手抄进 `FIELD_PATHS` 当成实测结论——那正是本条明令禁止的
#: "先写个猜的映射让它静默跑错"。
STAND_IN_PATHS = {
    "thread_id": ("body", "stand_in_thread"),
    "msgid": ("body", "stand_in_msgid"),
    "sender_userid": ("body", "stand_in_sender"),
    "content": ("body", "stand_in_text", "stand_in_content"),
}


def make_frame(*, msgid="MSGID0001", sender=ADMITTED_USERID, content="下周要两个嵌入式"):
    """一份符合**已实测部分**（cmd/headers/body.msgtype）的帧，其余键用占位名。"""
    return {
        "cmd": frames.MESSAGE_CALLBACK_CMD,
        "headers": {"req_id": "req-1"},
        "body": {
            "msgtype": "text",
            "stand_in_thread": "threadA",
            "stand_in_msgid": msgid,
            "stand_in_sender": sender,
            "stand_in_text": {"stand_in_content": content},
        },
    }


class StoppingEvent:
    """第 `after` 次询问时才说「停」。⛔ 不用真 threading.Event + sleep：那让用例不可复现。"""

    def __init__(self, after: int) -> None:
        self.after = after
        self.calls = 0

    def is_set(self) -> bool:
        self.calls += 1
        return self.calls > self.after


class RecordingSink:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def send(self, text: str) -> None:
        self.texts.append(text)


class ReplySpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, thread_id: str, text: str) -> None:
        self.calls.append((thread_id, text))


class PyeeLikeClient:
    """复刻 SDK 的事件表面（`aibot/client.py` 继承 `pyee.asyncio.AsyncIOEventEmitter`）。

    `emit("message", frame)` 逐字对齐 `aibot/message_handler.py:55`：**一个位置参数、
    载荷是整个帧**。⛔ 不要改成关键字参数——那样测的就不是 SDK 的实际调法了。
    """

    def __init__(self) -> None:
        self.handlers: dict[str, list] = {}

    def on(self, event, handler=None):
        self.handlers.setdefault(event, []).append(handler)
        return handler

    def emit(self, event, *args, **kwargs) -> bool:
        funcs = list(self.handlers.get(event, []))
        if not funcs:
            return False
        for func in funcs:
            func(*args, **kwargs)
        return True

    def run(self):  # pragma: no cover - 本文件不跑阻塞入口
        pass


@pytest.fixture
def svc(tmp_path):
    conn = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(conn)
    return session.LiaisonSession(
        conn, RecordingSink(), liveness_path=tmp_path / "liveness.json"
    )


@pytest.fixture
def roster(tmp_path):
    path = tmp_path / "whitelist.yaml"
    path.write_text(
        "members:\n"
        f"  - userid: {ADMITTED_USERID}\n"
        "    name: 汤丽萍\n"
        "    role: HR\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def ports(tmp_path, roster):
    return liaison_main.InboundPorts(
        archive_root=tmp_path / "archive", whitelist_path=roster, reply=ReplySpy()
    )


@pytest.fixture
def mapped(monkeypatch):
    """把占位路径表装上，⛔ 只在本文件的用例里有效。

    ⚠️ 生产表 `frames.FIELD_PATHS` 仍然是空的（fail-closed）：用例要验的是**接线**，
    ⛔ 不是"我们已经知道真实帧长什么样"。
    """
    monkeypatch.setattr(frames, "FIELD_PATHS", dict(STAND_IN_PATHS))


# ─────────────────────────────────────────────────────────────────────────
# ①② 订阅清单与表面校验
# ─────────────────────────────────────────────────────────────────────────


def test_message_is_in_the_subscribed_contract():
    """①：`message` 必须在清单里。⛔ 不在＝入站消息永远到不了本进程、且无症状。"""
    assert session_client.EVENT_MESSAGE == "message"
    assert session_client.EVENT_MESSAGE in session_client.SUBSCRIBED_EVENTS


def test_surface_check_refuses_to_start_when_message_is_not_in_the_contract(monkeypatch):
    """②：照 `error` 那条的形状——清单里没有 `message` 就拒绝启动。

    ⛔ 这条不是"防手滑"的装饰：漏订阅时 SDK 照常 emit、pyee 直接返回 False，
    ⛔ 连一行日志都不会有。没有本检查，缺口只会在"一周了一条消息都没收到"时
    被人肉发现。
    """
    monkeypatch.setattr(
        session_client,
        "SUBSCRIBED_EVENTS",
        (
            session_client.EVENT_CONNECTED,
            session_client.EVENT_DISCONNECTED,
            session_client.EVENT_ERROR,
            session_client.EVENT_AUTHENTICATED,
        ),
    )
    with pytest.raises(session_client.SdkSurfaceUnverifiedError) as excinfo:
        session_client.verify_client_surface(PyeeLikeClient())
    assert "message" in str(excinfo.value)


def test_every_subscribed_event_including_message_is_actually_wired():
    """清单即契约：列了就必须真的接上（与 TD-39 那条同源，多守一个事件）。"""
    client = PyeeLikeClient()
    session_client.make_sdk_connect(
        lambda: client, on_connected=lambda: None, on_disconnected=lambda: None
    )
    assert set(client.handlers) == set(session_client.SUBSCRIBED_EVENTS)


# ─────────────────────────────────────────────────────────────────────────
# ③ 回调只放帧
# ─────────────────────────────────────────────────────────────────────────


def test_the_message_callback_hands_the_whole_frame_to_on_message():
    client = PyeeLikeClient()
    seen: list[object] = []
    session_client.make_sdk_connect(
        lambda: client,
        on_connected=lambda: None,
        on_disconnected=lambda: None,
        on_message=seen.append,
    )
    frame = make_frame()
    assert client.emit(session_client.EVENT_MESSAGE, frame) is True
    assert seen == [frame], "载荷必须原样透传：SDK 给的是整个帧（message_handler.py:55）"


def test_a_failing_message_callback_never_escapes_back_into_the_sdk(caplog):
    """⛔ 回调里的异常不许回抛：pyee 会把它再 emit 成 `error`，而 `error` 那条链是
    重连的命门（TD-39）。一条畸形消息 ⛔ 不配打断整条连接。"""
    client = PyeeLikeClient()

    def boom(_frame):
        raise ValueError("解析炸了")

    session_client.make_sdk_connect(
        lambda: client,
        on_connected=lambda: None,
        on_disconnected=lambda: None,
        on_message=boom,
    )
    with caplog.at_level(logging.ERROR, logger=session_client.__name__):
        client.emit(session_client.EVENT_MESSAGE, make_frame())
    assert [r for r in caplog.records if r.levelno >= logging.ERROR], "⛔ 不许静默吞掉"


def test_an_unwired_message_event_is_loud_not_silent(caplog):
    """没接 `on_message` 时至少留一条 WARNING——静默默认值＝把本条要修的 bug 复制一份。"""
    client = PyeeLikeClient()
    session_client.make_sdk_connect(
        lambda: client, on_connected=lambda: None, on_disconnected=lambda: None
    )
    with caplog.at_level(logging.WARNING, logger=session_client.__name__):
        client.emit(session_client.EVENT_MESSAGE, make_frame())
    assert [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_the_sdk_message_callback_body_only_puts_the_frame_on_the_queue():
    """🔴 **工程铁律 1 的结构闸**：`main()` 里那个回调体里只许有 `events.put`。

    回调跑在 SDK 的事件循环线程上。在那里碰库＝跨线程用值守线程独占的 sqlite 连接，
    幂等记录与业务写的事务归属当场破掉（`docs/findings/2026-08-13-sqlite-事务归属冲突.md`
    同族），而且**失败方向是"幂等记录在、业务写没了"＝永久丢失**。
    ⛔ 红了不要把断言放宽，先问"这一句为什么非得在回调里做"。
    """
    tree = ast.parse(MAIN_SOURCE.read_text(encoding="utf-8"), filename=str(MAIN_SOURCE))
    callbacks = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "on_message"
    ]
    assert len(callbacks) == 1, "main() 里应当有且只有一个 on_message 回调"
    callees = set()
    for node in ast.walk(callbacks[0]):
        if isinstance(node, ast.Call):
            func = node.func
            callees.add(func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "?"))
    # `now` 是允许的第二个调用，且**必须**在回调里调：事件自带的时间戳要取"帧到达
    # 那一刻"，挪到值守线程去取会晚最多一个心跳间隔（连接事件回调同一口径）。
    assert callees <= {"put", "now"}, (
        f"⛔ 回调里只许 events.put 与 now()，实际还调了 {sorted(callees - {'put', 'now'})}"
    )
    assert "put" in callees, "回调必须把帧放进队列，否则帧到不了值守线程"


# ─────────────────────────────────────────────────────────────────────────
# ④⑤ 值守线程消费 ＋ 帧映射
# ─────────────────────────────────────────────────────────────────────────


def test_worker_archives_and_enqueues_an_admitted_message(svc, ports, mapped):
    """④：队列里的一条 message 事件 ⇒ 归档一份 ＋ 入队一条。"""
    events: queue.Queue = queue.Queue()
    events.put((liaison_main.EVENT_MESSAGE, T0, make_frame()))
    liaison_main.run_session_worker(
        svc, events, StoppingEvent(after=1), tick_interval=0.01, ports=ports
    )
    assert svc.conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 1
    assert svc.conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 1


def test_worker_uses_the_frame_arrival_moment_as_received_at(svc, ports, mapped):
    """⚠️ `received_at` 必须是**回调那一刻**（事件自带），⛔ 不是处理时刻——
    归档目录按它分天，用处理时刻会让跨零点的消息落错日期目录。"""
    events: queue.Queue = queue.Queue()
    events.put((liaison_main.EVENT_MESSAGE, T0, make_frame()))
    liaison_main.run_session_worker(
        svc,
        events,
        StoppingEvent(after=1),
        tick_interval=0.01,
        clock=lambda: T0 + timedelta(hours=9),
        ports=ports,
    )
    stored = svc.conn.execute("SELECT received_at FROM liaison_message").fetchone()[0]
    assert stored == session.format_instant(T0)


def test_an_inbound_message_counts_as_sdk_activity(svc, ports, mapped):
    """TD-42 的第二条判据看的是"SDK 多久没送来东西"。一条真实入站消息是对端在
    应答的最强证据——⛔ 不许只算"消息"不算"活动"，否则消息流量正常的连接会被
    看门狗当假死拆掉。"""
    events: queue.Queue = queue.Queue()
    # ⚠️ 先连上：`tick()` 只在 connected 状态下盖戳（断线期间存活戳定格是本章契约）。
    events.put((liaison_main.EVENT_CONNECTED, T0 - timedelta(minutes=1)))
    events.put((liaison_main.EVENT_MESSAGE, T0, make_frame()))
    liaison_main.run_session_worker(
        svc, events, StoppingEvent(after=2), tick_interval=0.01, ports=ports
    )
    payload = session.read_liveness_stamp(svc.liveness_path)
    assert payload[session.LIVENESS_EVENT_KEY] == session.format_instant(T0)


def test_connection_events_still_flow_through_the_same_queue(svc, ports):
    """二元组（连接事件）与三元组（消息）共用一条队列，⛔ 不许因为加了消息就把
    连接事件的入队形状改掉。"""
    events: queue.Queue = queue.Queue()
    events.put((liaison_main.EVENT_CONNECTED, T0))
    liaison_main.run_session_worker(
        svc, events, StoppingEvent(after=1), tick_interval=0.01, ports=ports
    )
    assert svc.state == session.STATE_CONNECTED


def test_an_outsider_is_archived_but_never_enqueued(svc, ports, mapped):
    """名单外：仍然归档（spec 原文），⛔ MUST NOT 生成任何队列条目。"""
    events: queue.Queue = queue.Queue()
    events.put((liaison_main.EVENT_MESSAGE, T0, make_frame(sender=OUTSIDER_USERID)))
    liaison_main.run_session_worker(
        svc, events, StoppingEvent(after=1), tick_interval=0.01, ports=ports
    )
    assert svc.conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 1
    assert svc.conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 0
    assert ports.reply.calls, "名单外必须回一条礼貌说明"


def test_a_broken_frame_never_kills_the_session_thread(svc, ports, mapped, caplog):
    """一条畸形帧只丢它自己：值守线程死掉＝存活戳停更＝看门狗重启整个进程。"""
    events: queue.Queue = queue.Queue()
    events.put((liaison_main.EVENT_MESSAGE, T0, {"cmd": "x", "body": {}}))
    events.put((liaison_main.EVENT_MESSAGE, T0, make_frame()))
    with caplog.at_level(logging.ERROR, logger=liaison_main.__name__):
        liaison_main.run_session_worker(
            svc, events, StoppingEvent(after=2), tick_interval=0.01, ports=ports
        )
    assert svc.conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 1


# ─────────────────────────────────────────────────────────────────────────
# ⑤ fail-closed：映射未经真实帧确认之前，⛔ 不许落库
# ─────────────────────────────────────────────────────────────────────────


def test_production_field_paths_are_still_unverified_so_mapping_refuses():
    """🔴 现状钉子：`FIELD_PATHS` 没被真实帧填之前，`compute_inbound_frame` **必抛**。

    ⛔ 不许改成"取不到就给个默认值"：落一条 `thread_id`／`msgid` 取错的归档，
    比不落这条难查一个数量级——台账有行、材料在错的地方、幂等键从此错位，
    且**没有任何症状**。AT-1b 拿到真实帧填上路径表后，本条自然转为验真实映射。
    """
    with pytest.raises(frames.InboundFrameUnverifiedError):
        frames.compute_inbound_frame(make_frame())


def test_an_unmapped_frame_is_not_archived_and_logs_the_field_structure(svc, ports, caplog):
    """fail-closed 的两半：**一行都不落库** ＋ 把帧的键结构打出来给 AT-1b 用。"""
    events: queue.Queue = queue.Queue()
    events.put((liaison_main.EVENT_MESSAGE, T0, make_frame()))
    with caplog.at_level(logging.ERROR, logger=liaison_main.__name__):
        liaison_main.run_session_worker(
            svc, events, StoppingEvent(after=1), tick_interval=0.01, ports=ports
        )
    assert svc.conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 0
    assert svc.conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 0
    assert not ports.reply.calls, "⛔ 没落库就不许回复：那会变成「回了但没留档」"
    text = caplog.text
    assert "stand_in_msgid" in text, "必须打出键名，否则 AT-1b 没法照它填路径表"


def test_the_field_structure_never_leaks_a_single_value():
    """🔴 日志＝个人信息的第二份拷贝。结构图只许有键名与类型，⛔ 一个取值都不许有。"""
    frame = make_frame(sender="LiSi", content="我的手机号是 13800000000")
    shape = frames.describe_frame_shape(frame)
    assert "13800000000" not in shape
    assert "LiSi" not in shape
    assert "MSGID0001" not in shape
    assert "stand_in_sender" in shape and "str(长度=" in shape


# ─────────────────────────────────────────────────────────────────────────
# ⑦ 接线层幂等：同一条 msgid 重投
# ─────────────────────────────────────────────────────────────────────────


def test_replaying_the_same_msgid_adds_no_second_row_anywhere(svc, ports, mapped):
    """⑦：重连后 SDK 重投同一 `msgid`（design D3 明列的成因）⇒ 归档不出第二份、
    队列不增第二行、⛔ 回复也不许再发一次。

    ⛔ 幂等**不在本层新增 `effect_*`**：`handle_inbound_message` 里的
    `archive_message` 已按 `{thread_id}:…:{msgid}` 幂等，本条只钉接线层的恒等断言。
    """
    events: queue.Queue = queue.Queue()
    frame = make_frame(sender=OUTSIDER_USERID)  # 名单外：三条链路（归档/不入队/回复）一次全覆盖
    events.put((liaison_main.EVENT_MESSAGE, T0, frame))
    events.put((liaison_main.EVENT_MESSAGE, T0 + timedelta(minutes=3), frame))
    liaison_main.run_session_worker(
        svc, events, StoppingEvent(after=2), tick_interval=0.01, ports=ports
    )
    assert svc.conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 1
    assert svc.conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 0
    assert len(ports.reply.calls) == 1, "重投 ⛔ 不许再回一遍——那是骚扰"


def test_replaying_an_admitted_message_adds_no_second_task(svc, ports, mapped):
    events: queue.Queue = queue.Queue()
    frame = make_frame()
    events.put((liaison_main.EVENT_MESSAGE, T0, frame))
    events.put((liaison_main.EVENT_MESSAGE, T0 + timedelta(minutes=3), frame))
    liaison_main.run_session_worker(
        svc, events, StoppingEvent(after=2), tick_interval=0.01, ports=ports
    )
    assert svc.conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 1
    assert svc.conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 1

