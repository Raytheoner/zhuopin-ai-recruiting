"""0930K·群通知**回推**接线（群帧入队后回推一次／私信不回推／重放幂等／缺地址拒发）。

**本文件守的是"那条链路真的有调用点"**。0930J 的现场：`HR_LIAISON_GROUP_WEBHOOK`
已配置，而 `liaison_group_notify` 恒 0 行——因为 `webhook.send_group_notify` 在生产
代码里**零调用点**（值守主程序根本不 import `tools/liaison/notify/*`），于是 8.6 的
第 3 条验收面永远拿不到样本。⛔ 只断言"relay 类存在"等于没接，所以下面既有端到端的
落库断言，也有 `__main__.py` 的结构断言。

⚠️ 全部用既有测试替身（`conftest` 的假时钟／录屏告警 ＋ `test_notify_webhook.py`
的假传输），⛔ 一条真消息都不发；地址一律 `example.invalid` 占位。
"""

from __future__ import annotations

import ast
import pathlib
import queue
from datetime import datetime, timedelta

import pytest

from tools.liaison import __main__ as liaison_main
from tools.liaison import frames, notify, session
from tools.liaison.config import GROUP_WEBHOOK_ENV
from tools.liaison.notify import ratelimit
from tools.liaison.notify.store import STATE_REJECTED, STATE_SENT
from tools.liaison.storage import db as liaison_db
from tools.liaison.tests.conftest import FAKE_WEBHOOK, FakeClock, RecordingSink

# 既有替身：按剧本回 errcode、⛔ 一个字节都不发出去的假传输。
from tools.liaison.tests.test_notify_webhook import FakeTransport

MAIN_SOURCE = pathlib.Path(liaison_main.__file__)
T0 = datetime(2026, 9, 30, 10, 0, 0, tzinfo=session.CHINA_TZ)

ADMITTED_USERID = "tanglp"
OUTSIDER_USERID = "someone-else"
GROUP_CHAT_ID = "fake-chat-id-0930k"

#: 值守通道的 group webhook 台账（`liaison_group_notify`）唯一的 effect 节点名。
GROUP_NOTIFY_NODE = "effect_send_group_notify"


class StoppingEvent:
    """第 `after` 次询问时才说「停」。⛔ 不用真 `threading.Event` + sleep。"""

    def __init__(self, after: int) -> None:
        self.after = after
        self.calls = 0

    def is_set(self) -> bool:
        self.calls += 1
        return self.calls > self.after


class ReplySpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, thread_id: str, text: str) -> None:
        self.calls.append((thread_id, text))


def make_frame(
    *,
    chattype: str,
    msgid: str,
    sender: str = ADMITTED_USERID,
    content: str = "下周要两个嵌入式",
    chatid: str = GROUP_CHAT_ID,
) -> dict:
    """一份**真实帧形状**的帧（键名照 AT-1b 的实测结构，取值全假，见
    `frames.py` 模块 docstring 第二节）。用真实形状而不是占位路径表：本文件要测的是
    **生产接线**，占位表会把 `frames.FIELD_PATHS` 的真实映射一起绕开。
    """
    body: dict = {
        "msgid": msgid,
        "aibotid": "aib-fakeaibotidfakeaibotidfakeaibotidfa",
        "chattype": chattype,
        "from": {"userid": sender},
        "msgtype": "text",
        "response_url": "https://example.invalid/cgi-bin/aibot/response",
        "text": {"content": content},
    }
    if chattype == "group":
        body["chatid"] = chatid
    return {"cmd": frames.MESSAGE_CALLBACK_CMD, "headers": {"req_id": "fake-req-id"}, "body": body}


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def svc(tmp_path):
    conn = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(conn)
    return session.LiaisonSession(
        conn, RecordingSink(), liveness_path=tmp_path / "liveness.json"
    )


@pytest.fixture
def roster(tmp_path) -> pathlib.Path:
    path = tmp_path / "whitelist.yaml"
    path.write_text(
        "members:\n"
        f"  - userid: {ADMITTED_USERID}\n"
        "    name: 汤丽萍\n"
        "    role: HR\n",
        encoding="utf-8",
    )
    return path


def make_relay(clock: FakeClock, fake: FakeTransport, *, env: dict) -> notify.GroupNotifyRelay:
    """按 0930K 的接线形态造 relay：桶与时钟注入（notify 包结构上不 import time）。"""
    return notify.GroupNotifyRelay(
        bucket=ratelimit.make_group_webhook_bucket(
            monotonic=clock.monotonic, sleep=clock.sleep
        ),
        sleep=clock.sleep,
        transport=fake,
        env=env,
    )


def make_ports(tmp_path, roster, *, group_notify=None, reply=None) -> liaison_main.InboundPorts:
    return liaison_main.InboundPorts(
        archive_root=tmp_path / "archive",
        whitelist_path=roster,
        reply=reply if reply is not None else ReplySpy(),
        ledger_path=tmp_path / "README-跟进信清单.md",
        # ⛔ 不留默认值：默认指向真实 `data/liaison/logs/`，未知附件帧取证会在仓库里
        # 真的落一份文件（同 test_inbound_wiring 的口径）。
        unknown_attachment_log_dir=tmp_path / "unknown-attachment-frames",
        group_notify=group_notify,
    )


def run_frames(svc, ports, incoming, *, tick_interval=0.01) -> None:
    events: queue.Queue = queue.Queue()
    for moment, frame in incoming:
        events.put((liaison_main.EVENT_MESSAGE, moment, frame))
    liaison_main.run_session_worker(
        svc,
        events,
        StoppingEvent(after=len(incoming)),
        tick_interval=tick_interval,
        ports=ports,
    )


def count(conn, sql: str, params=()) -> int:
    return conn.execute(sql, params).fetchone()[0]


# ─────────────────────────────────────────────────────────────────────────
# ① 群帧入队后回推**恰好一次**
# ─────────────────────────────────────────────────────────────────────────


def test_a_group_message_is_pushed_back_exactly_once(svc, roster, tmp_path, clock):
    fake = FakeTransport([0])
    ports = make_ports(
        tmp_path,
        roster,
        group_notify=make_relay(clock, fake, env={GROUP_WEBHOOK_ENV: FAKE_WEBHOOK}),
    )

    run_frames(svc, ports, [(T0, make_frame(chattype="group", msgid="MSG-G-1"))])

    rows = svc.conn.execute(
        "SELECT thread_id, channel, state, mode FROM liaison_group_notify"
    ).fetchall()
    print(
        "[①群帧] 台账行=", rows,
        " json_calls=", len(fake.json_calls),
        " 实发正文=", fake.sent_markdown_bodies(),
        " effect_log=", count(
            svc.conn,
            "SELECT COUNT(*) FROM effect_log WHERE node_name = ?",
            (GROUP_NOTIFY_NODE,),
        ),
        sep="",
    )
    # 会话标识取**来源群**的 chatid（store 的 docstring：能指出源自哪条会话就传它，
    # 台账主键是 (thread_id, digest) 复合键），通道是群 webhook 那条。
    assert rows == [(GROUP_CHAT_ID, "group_webhook", STATE_SENT, "direct")]
    assert len(fake.json_calls) == 1, "回推必须恰好发一次"
    assert fake.sent_msgtypes() == ["markdown"], "回推走既有的 send_markdown 通道"
    body = fake.sent_markdown_bodies()[0]
    assert "新任务已登记" in body
    assert "MSG-G-1" in body, "短追踪号进群消息"
    assert "汤丽萍（群消息）" in body, "来源写人名＋渠道（2026-10-01 可读化）"
    assert "2026-09-30 10:00" in body, "时间写 CST 分钟"
    assert GROUP_CHAT_ID not in body, "⛔ 完整来源会话标识不进群消息"
    assert "下周要两个嵌入式" in body, "回推内容是任务摘要"
    # 铁律 1 恒等式：effect_log 条数 == 业务表行数
    assert count(
        svc.conn, "SELECT COUNT(*) FROM effect_log WHERE node_name = ?", (GROUP_NOTIFY_NODE,)
    ) == 1
    # 先落库后通知：待办那条也在
    assert count(svc.conn, "SELECT COUNT(*) FROM liaison_task") == 1


# ─────────────────────────────────────────────────────────────────────────
# ② 私信帧⛔ 不回推
# ─────────────────────────────────────────────────────────────────────────


def test_a_private_message_is_never_pushed_back(svc, roster, tmp_path, clock):
    """私信照旧归档＋入队（那是第 4／5 章的既有契约），但⛔ 一条群通知都不发——
    回推只对群帧（Shao Peishen 2026-09-30 答 1a 的口径）。"""
    fake = FakeTransport([0])
    ports = make_ports(
        tmp_path,
        roster,
        group_notify=make_relay(clock, fake, env={GROUP_WEBHOOK_ENV: FAKE_WEBHOOK}),
    )

    run_frames(svc, ports, [(T0, make_frame(chattype="single", msgid="MSG-S-1"))])

    assert count(svc.conn, "SELECT COUNT(*) FROM liaison_message") == 1
    assert count(svc.conn, "SELECT COUNT(*) FROM liaison_task") == 1, "私信仍要入队"
    assert count(svc.conn, "SELECT COUNT(*) FROM liaison_group_notify") == 0
    assert fake.json_calls == [] and fake.multipart_calls == []


def test_an_outsider_group_message_is_archived_but_never_pushed_back(svc, roster, tmp_path, clock):
    """名单外：归档照旧（spec 原文），⛔ 不入队也就⛔ 不回推——回推的前提是"待办真的
    建起来了"，对一条没建待办的消息说"新任务已登记"就是一句谎。"""
    fake = FakeTransport([0])
    reply = ReplySpy()
    ports = make_ports(
        tmp_path,
        roster,
        reply=reply,
        group_notify=make_relay(clock, fake, env={GROUP_WEBHOOK_ENV: FAKE_WEBHOOK}),
    )

    run_frames(
        svc,
        ports,
        [(T0, make_frame(chattype="group", msgid="MSG-G-2", sender=OUTSIDER_USERID))],
    )

    assert count(svc.conn, "SELECT COUNT(*) FROM liaison_message") == 1
    assert count(svc.conn, "SELECT COUNT(*) FROM liaison_task") == 0
    assert count(svc.conn, "SELECT COUNT(*) FROM liaison_group_notify") == 0
    assert fake.json_calls == []
    assert reply.calls, "名单外仍回一条礼貌说明（既有契约）"


# ─────────────────────────────────────────────────────────────────────────
# ③ 同 msgid 重放 ⇒ 幂等命中
# ─────────────────────────────────────────────────────────────────────────


def test_replaying_the_same_group_msgid_sends_only_once(svc, roster, tmp_path, clock):
    """重连后 SDK 重投同一 `msgid`（design D3 明列的成因）：归档不出第二份、待办不增、
    群通知**也不许再发一遍**。幂等由既有的 `effect_send_group_notify` 承担，
    ⛔ 本层不另造一套。"""
    fake = FakeTransport([0])
    ports = make_ports(
        tmp_path,
        roster,
        group_notify=make_relay(clock, fake, env={GROUP_WEBHOOK_ENV: FAKE_WEBHOOK}),
    )
    frame = make_frame(chattype="group", msgid="MSG-G-3")

    run_frames(svc, ports, [(T0, frame), (T0 + timedelta(minutes=3), frame)])

    print(
        "[③重放] 台账行数=", count(svc.conn, "SELECT COUNT(*) FROM liaison_group_notify"),
        " json_calls=", len(fake.json_calls),
        " 待办行数=", count(svc.conn, "SELECT COUNT(*) FROM liaison_task"),
        sep="",
    )
    assert count(svc.conn, "SELECT COUNT(*) FROM liaison_message") == 1
    assert count(svc.conn, "SELECT COUNT(*) FROM liaison_task") == 1
    assert count(svc.conn, "SELECT COUNT(*) FROM liaison_group_notify") == 1
    assert len(fake.json_calls) == 1, "重放⛔ 不许产生第二次发送"


# ─────────────────────────────────────────────────────────────────────────
# ④ 地址缺失 ⇒ 拒发落行＋告警，值守线程不被带走
# ─────────────────────────────────────────────────────────────────────────


def test_a_missing_webhook_is_rejected_and_alerted_without_killing_the_thread(
    svc, roster, tmp_path, clock
):
    """`HR_LIAISON_GROUP_WEBHOOK` 未配置（6.10 的既有语义：拒发并点名变量）。

    三件事一起断言：
    ① 落一行 `rejected`（⛔ 不静默跳过、⛔ 不假装成功）＋ 一条告警；
    ② transport 一次都没被调用（没地址就是没地址）；
    ③ **值守线程照常往下跑**——第三条事件是同一条帧的重放，它之后还有一条新帧，
       两条都处理完了才说明第一条的拒发没有把线程带走。
    """
    fake = FakeTransport([0])
    ports = make_ports(tmp_path, roster, group_notify=make_relay(clock, fake, env={}))
    replay = make_frame(chattype="group", msgid="MSG-G-4")

    run_frames(
        svc,
        ports,
        [
            (T0, replay),
            (T0 + timedelta(minutes=1), replay),
            (T0 + timedelta(minutes=2), make_frame(chattype="group", msgid="MSG-G-5")),
        ],
    )

    states = svc.conn.execute(
        "SELECT state, mode, thread_id FROM liaison_group_notify ORDER BY created_at, digest"
    ).fetchall()
    alerts = svc.alert_sink.texts
    print(
        "[④缺地址] 台账行=", states,
        " 告警条数=", len(alerts),
        " 告警原文=", alerts[0] if alerts else None,
        " json_calls=", len(fake.json_calls),
        " 归档行数=", count(svc.conn, "SELECT COUNT(*) FROM liaison_message"),
        " 待办行数=", count(svc.conn, "SELECT COUNT(*) FROM liaison_task"),
        sep="",
    )
    # 两条不同的 msgid、各自一次拒发；同一条重放⛔ 不产生第二行（幂等）
    assert states == [
        (STATE_REJECTED, "reject", GROUP_CHAT_ID),
        (STATE_REJECTED, "reject", GROUP_CHAT_ID),
    ]
    assert len(alerts) == 2, "每次拒发恰好告警一条"
    assert GROUP_WEBHOOK_ENV in alerts[0], "告警必须点名缺失的变量，⛔ 不打取值"
    assert FAKE_WEBHOOK not in alerts[0]
    assert fake.json_calls == [] and fake.multipart_calls == []
    # ③ 线程没被带走：三条事件全部处理完（重放不增行）
    assert count(svc.conn, "SELECT COUNT(*) FROM liaison_message") == 2
    assert count(svc.conn, "SELECT COUNT(*) FROM liaison_task") == 2


# ─────────────────────────────────────────────────────────────────────────
# ⑤ 文案：可读（人名＋渠道／分钟／短追踪号），⛔ 无正文、⛔ 无不透明标识、⛔ 无凭据
# ─────────────────────────────────────────────────────────────────────────


def test_the_relay_text_is_readable_and_never_carries_the_whole_body():
    """回推文案**可读化**（2026-10-01，Shao Peishen 要求）：`来源`写人名＋渠道、
    `时间`写 CST 分钟、`追踪号`只留 `msgid` 前 8 位；内容是**任务摘要**（与队列视图
    同一个字符串），⛔ 不是正文。

    正文可能是简历材料：把它整段发进群里，既越过了"只回推摘要"的口径，也让
    "完整正文见附件"那条降级路在群通知里活过来。摘要由 `compute_task_summary`
    机械截断（默认 ≤120 字符），本条用 300 字正文把这条边界钉住；同时把
    **完整 `msgid` 与完整 `thread_id` 都不进文案**（只留 `e3015852` 短追踪号）钉住——
    它们是内部不透明标识，直接甩给群里的人不可读。
    """
    content = "甲" * 300
    summary = liaison_main.compute_task_summary(content, msgtype="text")
    msgid = "e30158528d8f959cc0a3511dd7960319"
    thread_id = "wrvDL_DAAAnkeGLkk1_bu2Ne1oQfc4BA"
    text = notify.compute_task_relay_text(
        sender_label="邵培申",
        channel_label="群消息",
        occurred_at="2026-10-01 00:02",
        msgid=msgid,
        summary=summary,
    )

    print("[⑤文案] 摘要长度=", len(summary), " 文案长度=", len(text), sep="")
    print(text)
    assert summary in text
    assert content not in text, "⛔ 正文整段不许进群消息"
    assert "邵培申（群消息）" in text, "来源写人名＋渠道"
    assert "2026-10-01 00:02" in text, "时间写 CST 分钟"
    assert "e3015852" in text and msgid not in text, "⛔ 只留短追踪号，完整 msgid 不进文案"
    assert thread_id not in text, "⛔ 完整来源会话标识不进群消息"
    assert FAKE_WEBHOOK not in text, "⛔ webhook 地址（本身即凭据）不许出现在文案里"


def test_a_missing_name_falls_back_to_the_userid(svc, tmp_path, clock):
    """人名取不到（名单里该成员 `name` 为空）⇒ 回退 `sender_userid`，⛔ 不许抛、
    ⛔ 不许把这条通知整条吞掉。准入不受影响——`load_whitelist` 只看 userid，
    姓名缺失只在 `load_whitelist_names` 那一侧反映（两者对脏数据的容忍度不同是刻意的）。"""
    roster = tmp_path / "whitelist.yaml"
    roster.write_text(
        "members:\n"
        f"  - userid: {ADMITTED_USERID}\n"
        "    name: ''\n"
        "    role: HR\n",
        encoding="utf-8",
    )
    fake = FakeTransport([0])
    ports = make_ports(
        tmp_path,
        roster,
        group_notify=make_relay(clock, fake, env={GROUP_WEBHOOK_ENV: FAKE_WEBHOOK}),
    )

    run_frames(svc, ports, [(T0, make_frame(chattype="group", msgid="MSG-G-7"))])

    bodies = fake.sent_markdown_bodies()
    print("[②回退] 实发正文=", bodies, sep="")
    assert len(bodies) == 1, "回退 userid 也要真发出去，⛔ 不许静默吞掉"
    assert f"来源：{ADMITTED_USERID}（群消息）" in bodies[0], "取不到姓名 ⇒ 回退 userid"


def test_a_private_channel_is_labelled():
    """渠道标签：`私信` 也要能被文案原样渲染（渠道由调用方按 `frame_chattype` 判定后
    传入，纯函数只负责拼）。顺带钉住调用点的映射——⛔ 不许拿 `thread_id` 的形状去猜。"""
    text = notify.compute_task_relay_text(
        sender_label="汤丽萍",
        channel_label="私信",
        occurred_at="2026-10-01 09:05",
        msgid="0123456789abcdef0123456789abcdef",
        summary="回件已收",
    )
    print("[③私信] 文案=", text, sep="")
    assert "来源：汤丽萍（私信）" in text
    assert liaison_main.compute_channel_label(frames.SINGLE_CHAT_CHATTYPE) == "私信"


# ─────────────────────────────────────────────────────────────────────────
# ⑥ 结构：守护进程真的装了 relay、帧处理路径真的调它
# ─────────────────────────────────────────────────────────────────────────


def test_the_daemon_installs_the_relay_and_the_frame_path_calls_it():
    """🔴 正向判据：⛔ 只断言"relay 类存在"等于没接线。

    本条要挡住的就是 0930J 那个形状：门面函数齐备、测试全绿，而**生产代码零调用点**，
    于是台账恒 0 行且没有任何症状。两条一起才算接上——
    ① `main()` 造的 `InboundPorts` 带 `group_notify=`；
    ② `__main__.py` 里真的出现 `.send_group_notify(...)` 调用。
    """
    tree = ast.parse(MAIN_SOURCE.read_text(encoding="utf-8"), filename=str(MAIN_SOURCE))

    installed = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "InboundPorts"
        and any(keyword.arg == "group_notify" for keyword in node.keywords)
    ]
    assert installed, "main() 的 InboundPorts(...) 必须带 group_notify= ——⛔ 不装就是零调用点"

    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "send_group_notify" in called, "⛔ 装了 relay 却不调它等于没接线"
    assert "compute_task_relay_text" in called, "⛔ 文案必须走 notify 包的纯函数"
    # 令牌桶必须走**进程级单例**：两个桶＝两份 20 条/分钟的配额，而 D9 的额度是
    # 服务端的（TD-26 ①）。⛔ 这里用 `make_group_webhook_bucket` 就当场红。
    assert "get_group_webhook_bucket" in called, "群 webhook 的桶必须取进程级单例"
