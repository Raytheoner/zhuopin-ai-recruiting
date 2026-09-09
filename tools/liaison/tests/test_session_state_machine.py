"""第 7 章·会话状态机（7.2 / 7.3 / 7.5 / 7.7 / 7.8）。

这里的每条用例都是 spec liaison-channel-session 的一个 Scenario 的可执行形式。
时间全部由参数注入，⛔ 没有一处 sleep、⛔ 没有一处 datetime.now()——两小时的
空闲要在毫秒内跑完，且结果必须逐次可复现。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from tools.liaison import alerts, session
from tools.liaison.storage import db as liaison_db

T0 = datetime(2026, 9, 9, 10, 0, 0, tzinfo=session.CHINA_TZ)


class RecordingSink:
    """记下每一条被送出的告警。⛔ 不做任何网络动作。"""

    def __init__(self) -> None:
        self.texts: list[str] = []

    def send(self, text: str) -> None:
        self.texts.append(text)


class BoomSink:
    """永远送不出去的通道，用来验 7.5「失败只记日志、不中止接收」。"""

    def __init__(self) -> None:
        self.attempts = 0

    def send(self, text: str) -> None:
        self.attempts += 1
        raise RuntimeError("告警通道 502")


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "liaison.db"


@pytest.fixture
def liveness_path(tmp_path):
    return tmp_path / "liveness.json"


def make_session(db_path, liveness_path, sink):
    """新建一个会话对象 = 模拟一次**进程启动**（新连接、新内存状态、同一个库与存活戳）。"""
    conn = liaison_db.get_connection(db_path)
    liaison_db.init_schema(conn)
    return session.LiaisonSession(conn, sink, liveness_path=liveness_path)


def windows(conn):
    return conn.execute(
        "SELECT started_at, detected_by, recovered_at, closed_by, alerted_at "
        "FROM liaison_outage_window ORDER BY started_at"
    ).fetchall()


def test_two_idle_hours_produce_no_window_and_no_alert(db_path, liveness_path):
    """spec Scenario「长时间无消息但连接健康」：存活戳持续更新、不产生断线告警。

    ⛔ 本用例里一条消息都没有——这正是重点：连接健康与消息往来是两件事。
    """
    sink = RecordingSink()
    svc = make_session(db_path, liveness_path, sink)
    svc.start(T0)
    svc.on_connected(T0)
    for minute in range(1, 121):
        svc.tick(T0 + timedelta(minutes=minute))
    payload = json.loads(liveness_path.read_text(encoding="utf-8"))
    assert payload["state"] == session.STATE_CONNECTED
    assert payload["stamp_at"] == session.format_instant(T0 + timedelta(hours=2))
    assert payload["since"] == session.format_instant(T0)
    assert windows(svc.conn) == []
    assert sink.texts == []


def test_disconnect_opens_a_window_and_freezes_the_stamp(db_path, liveness_path):
    """spec Scenario「连接真的断开」：存活戳停止更新并记录断线时刻。"""
    sink = RecordingSink()
    svc = make_session(db_path, liveness_path, sink)
    svc.start(T0)
    svc.on_connected(T0)
    down_at = T0 + timedelta(minutes=5)
    svc.on_disconnected(down_at)

    frozen = json.loads(liveness_path.read_text(encoding="utf-8"))
    assert frozen["state"] == session.STATE_DISCONNECTED
    assert frozen["stamp_at"] == session.format_instant(down_at)

    # 断线期间继续 tick：存活戳 ⛔ 不许再动。
    for minute in range(6, 20):
        svc.tick(T0 + timedelta(minutes=minute))
    assert json.loads(liveness_path.read_text(encoding="utf-8")) == frozen

    rows = windows(svc.conn)
    assert len(rows) == 1
    assert rows[0][0] == session.format_instant(down_at)
    assert rows[0][1] == session.DETECTED_BY_DISCONNECT
    assert rows[0][2] is None, "还没恢复，⛔ 不许提前闭合"
    assert sink.texts == [], "窗口没闭合就 ⛔ 不许告警——告警文本必须含恢复时间"


def test_repeated_disconnect_events_do_not_open_a_second_window(db_path, liveness_path):
    """SDK 可能对同一次断线回调多次。⛔ 一次中断只许有一个窗口。"""
    svc = make_session(db_path, liveness_path, RecordingSink())
    svc.start(T0)
    svc.on_connected(T0)
    svc.on_disconnected(T0 + timedelta(minutes=5))
    svc.on_disconnected(T0 + timedelta(minutes=6))
    assert len(windows(svc.conn)) == 1


def test_reconnect_closes_the_window_and_alerts_once(db_path, liveness_path):
    """spec Scenario「断线后恢复」：存在一条起止时间完整的中断窗口记录 + 一条告警。"""
    sink = RecordingSink()
    svc = make_session(db_path, liveness_path, sink)
    svc.start(T0)
    svc.on_connected(T0)
    down_at = T0 + timedelta(minutes=5)
    up_at = T0 + timedelta(minutes=8)
    svc.on_disconnected(down_at)
    svc.on_connected(up_at)

    rows = windows(svc.conn)
    assert len(rows) == 1
    assert rows[0][2] == session.format_instant(up_at)
    assert rows[0][3] == session.CLOSED_BY_RECONNECT
    assert rows[0][4] is not None, "告警送出成功后必须落 alerted_at"

    assert len(sink.texts) == 1
    assert "2026-09-09 10:05:00" in sink.texts[0]
    assert "2026-09-09 10:08:00" in sink.texts[0]
    assert alerts.ALERT_RESEND_SENTENCE in sink.texts[0]

    # 恢复后存活戳重新开始更新。
    svc.tick(up_at + timedelta(minutes=1))
    payload = json.loads(liveness_path.read_text(encoding="utf-8"))
    assert payload["state"] == session.STATE_CONNECTED
    assert payload["stamp_at"] == session.format_instant(up_at + timedelta(minutes=1))


def test_restart_after_a_kill_during_an_outage_keeps_the_window_open(db_path, liveness_path):
    """spec Scenario「进程被杀后重启」：上一次未闭合的窗口 MUST NOT 丢弃，但 ⛔ 也不在
    启动时闭合——它保持开启，等下一次真正连上（2026-09-09 裁决二·改法 ①，TD-20）。
    """
    first = make_session(db_path, liveness_path, RecordingSink())
    first.start(T0)
    first.on_connected(T0)
    down_at = T0 + timedelta(minutes=5)
    first.on_disconnected(down_at)
    first.conn.close()  # 进程被 kill -9：没有任何优雅收尾

    sink = RecordingSink()
    restart_at = T0 + timedelta(hours=1)
    second = make_session(db_path, liveness_path, sink)
    second.start(restart_at)

    rows = windows(second.conn)
    assert len(rows) == 1, "上一次未闭合的窗口 MUST NOT 丢弃"
    assert rows[0][0] == session.format_instant(down_at)
    assert rows[0][2] is None, "⛔ 不许在启动时闭合——那一刻还不知道恢复时间"
    assert rows[0][3] is None
    assert sink.texts == [], "窗口没闭合就 ⛔ 不许告警"

    up_at = T0 + timedelta(hours=3)
    second.on_connected(up_at)
    rows = windows(second.conn)
    assert rows[0][2] == session.format_instant(up_at)
    assert rows[0][3] == session.CLOSED_BY_RECONNECT
    assert len(sink.texts) == 1
    assert "2026-09-09 10:05:00" in sink.texts[0] and "2026-09-09 13:00:00" in sink.texts[0]


def test_restarting_again_does_not_open_a_second_window_or_alert_twice(db_path, liveness_path):
    """7.3 幂等：按窗口起始时间去重，重复启动不重复开窗，也不重复告警。

    改法 ① 之后这条更强了：连着重启多次，那一个窗口从头到尾**只有一个**、
    始终开着，且直到真正连上才发出**唯一**一条覆盖全程的告警——⛔ 不会像旧实现
    那样被第一次重启切成一段短的、后面几段无人记录。
    """
    first = make_session(db_path, liveness_path, RecordingSink())
    first.start(T0)
    first.on_connected(T0)
    first.on_disconnected(T0 + timedelta(minutes=5))
    first.conn.close()

    second = make_session(db_path, liveness_path, RecordingSink())
    second.start(T0 + timedelta(hours=1))
    second.conn.close()

    third_sink = RecordingSink()
    third = make_session(db_path, liveness_path, third_sink)
    third.start(T0 + timedelta(hours=2))

    rows = windows(third.conn)
    assert len(rows) == 1, "⛔ 不许补出第二条窗口"
    assert rows[0][0] == session.format_instant(T0 + timedelta(minutes=5))
    assert rows[0][2] is None, "重启多少次都 ⛔ 不许闭合"
    assert third_sink.texts == []

    third.on_connected(T0 + timedelta(hours=4))
    rows = windows(third.conn)
    assert len(rows) == 1
    assert rows[0][2] == session.format_instant(T0 + timedelta(hours=4))
    assert len(third_sink.texts) == 1, "全程只发一条，覆盖 10:05–14:00 整段"
    assert "2026-09-09 10:05:00" in third_sink.texts[0]
    assert "2026-09-09 14:00:00" in third_sink.texts[0]


def test_restart_after_a_kill_while_connected_records_the_gap_from_the_last_stamp(
    db_path, liveness_path
):
    """连接健康时被杀：库里没有任何未闭合窗口，但那段停机时间同样收不到消息。

    spec Requirement 正文是「为**每一次连接中断**记录一个中断窗口」——进程不在了
    也是一种中断，只是两条 Scenario 没有单独举它。存活戳的最后一次盖戳时间正是
    这个窗口的起点，⛔ 不许因为"Scenario 没写"就让这段停机静默地过去。

    ⚠️ 本用例只管**起点**（`startup_gap` 开窗这一件事）。窗口怎么闭、告警文案是
    什么，由 test_starting_while_still_offline_does_not_underreport_the_outage
    单独覆盖——改法 ① 之后启动时 ⛔ 不再闭合（TD-20）。
    """
    first = make_session(db_path, liveness_path, RecordingSink())
    first.start(T0)
    first.on_connected(T0)
    last_stamp_at = T0 + timedelta(minutes=10)
    first.tick(last_stamp_at)
    first.conn.close()  # 连着的时候被 kill -9

    sink = RecordingSink()
    restart_at = T0 + timedelta(minutes=40)
    second = make_session(db_path, liveness_path, sink)
    second.start(restart_at)

    rows = windows(second.conn)
    assert len(rows) == 1
    assert rows[0][0] == session.format_instant(last_stamp_at)
    assert rows[0][1] == session.DETECTED_BY_STARTUP_GAP
    assert rows[0][2] is None, "开了窗就够了，闭合归 on_connected 管（TD-20）"
    assert sink.texts == []


def test_clean_restart_after_a_disconnected_stamp_records_no_gap(db_path, liveness_path):
    """上一次的存活戳是 disconnected/starting ⇒ ⛔ 不许凭空造一个 startup_gap 窗口。

    那段时间的账已经由"未闭合窗口补记"这条路径管着了，两条路径都记 = 一次中断
    被记成两个窗口 = 收信人收到两条内容重叠的告警。
    """
    first = make_session(db_path, liveness_path, RecordingSink())
    first.start(T0)
    first.conn.close()

    second = make_session(db_path, liveness_path, RecordingSink())
    second.start(T0 + timedelta(hours=3))
    assert windows(second.conn) == []


def test_a_stamp_from_the_future_is_ignored_instead_of_making_a_negative_window(
    db_path, liveness_path
):
    """机器时钟被往回调过：存活戳比"现在"还新。⛔ 不许造出一个负数长度的窗口。"""
    first = make_session(db_path, liveness_path, RecordingSink())
    first.start(T0)
    first.on_connected(T0)
    first.tick(T0 + timedelta(hours=5))
    first.conn.close()

    sink = RecordingSink()
    second = make_session(db_path, liveness_path, sink)
    second.start(T0 + timedelta(hours=1))
    assert windows(second.conn) == []
    assert sink.texts == []


def test_alert_failure_neither_stops_the_session_nor_marks_the_window(db_path, liveness_path):
    """7.5 / 7.8：告警发送失败 ⇒ 只记日志、不中止；alerted_at 保持 NULL。"""
    boom = BoomSink()
    svc = make_session(db_path, liveness_path, boom)
    svc.start(T0)
    svc.on_connected(T0)
    svc.on_disconnected(T0 + timedelta(minutes=5))
    svc.on_connected(T0 + timedelta(minutes=8))  # ⛔ 不许因为告警失败而抛出

    rows = windows(svc.conn)
    assert rows[0][2] is not None, "窗口该闭合的还是要闭合"
    assert rows[0][4] is None, "告警没送出去就 ⛔ 不许标记已告警"
    assert boom.attempts == 1

    # 接收照常继续：存活戳仍在更新。
    svc.tick(T0 + timedelta(minutes=9))
    assert json.loads(liveness_path.read_text(encoding="utf-8"))["stamp_at"] == (
        session.format_instant(T0 + timedelta(minutes=9))
    )


def test_a_failed_alert_is_retried_on_the_next_start(db_path, liveness_path):
    """宁可重复告警，⛔ 不静默丢告警：alerted_at 还是 NULL，下次启动重发。

    ⚠️ 本用例只想单独验证"重发"这一件事，⛔ 不想同时撞上 7.7 的另一条规则——
    「重启时若上一次的存活戳停在 connected，按最后盖戳时间补一个 startup_gap
    窗口」（见 test_restart_after_a_kill_while_connected_records_the_gap_from_
    the_last_stamp，那条规则已被独立验证为正确行为）。
    这里让"重启"发生在与上一次盖戳**完全相同的时刻**（模拟进程重启零耗时、
    没有真实停机），这样 `start()` 里 `gap_started_at < format_instant(now)`
    这个判据不成立，⛔ 不会凭空多开一个窗口、多发一条不属于本用例范围的告警——
    否则两条规则的正确行为会在断言里互相干扰，而不是任何一条规则本身有问题。
    """
    boom = BoomSink()
    first = make_session(db_path, liveness_path, boom)
    first.start(T0)
    first.on_connected(T0)
    first.on_disconnected(T0 + timedelta(minutes=5))
    reconnected_at = T0 + timedelta(minutes=8)
    first.on_connected(reconnected_at)
    first.conn.close()

    sink = RecordingSink()
    second = make_session(db_path, liveness_path, sink)
    second.start(reconnected_at)
    assert len(sink.texts) == 1, "上次没送出去的告警必须补发"
    assert windows(second.conn) == [
        (
            session.format_instant(T0 + timedelta(minutes=5)),
            session.DETECTED_BY_DISCONNECT,
            session.format_instant(reconnected_at),
            session.CLOSED_BY_RECONNECT,
            windows(second.conn)[0][4],
        )
    ], "⛔ 不许因为本用例的重启时刻而多开一个 startup_gap 窗口"
    assert windows(second.conn)[0][4] is not None


# ── TD-20 回归：启动时不闭合旧窗口（2026-09-09 Shao Peishen 裁决二·改法 ①）────


def test_starting_while_still_offline_does_not_underreport_the_outage(db_path, liveness_path):
    """TD-20 复现场景：重启时网络**仍未恢复**，⛔ 不许把恢复时间记成启动时间。

    reviewer 实测的原始时间线，逐字照搬：末次存活戳 `connected@10:10` → 进程被杀
    → `10:40` 网络仍未恢复时重启 → `12:00` 才第一次真正连上。

    改动前：`start()` 在 10:40 就把窗口按"恢复时间 = 本次启动时间"闭合并告警，
    收信人只被告知补发 10:10–10:40 的 30 分钟，而实际收不到消息的是 110 分钟——
    低报 80 分钟，落在低报区间外的消息永远补不回来。

    改动后：10:40 的启动**只开窗、不闭合、不告警**；闭合交给 `on_connected`，
    于是告警自然带上 12:00 这个**真实**恢复时间。
    """
    first = make_session(db_path, liveness_path, RecordingSink())
    first.start(T0)
    first.on_connected(T0)
    last_stamp_at = T0 + timedelta(minutes=10)  # 10:10 最后一次盖戳
    first.tick(last_stamp_at)
    first.conn.close()  # kill -9：连接健康时被杀

    sink = RecordingSink()
    restart_at = T0 + timedelta(minutes=40)  # 10:40 网络仍未恢复
    second = make_session(db_path, liveness_path, sink)
    second.start(restart_at)

    rows = windows(second.conn)
    assert len(rows) == 1
    assert rows[0][0] == session.format_instant(last_stamp_at)
    assert rows[0][1] == session.DETECTED_BY_STARTUP_GAP
    assert rows[0][2] is None, "还没连上就 ⛔ 不许闭合窗口（TD-20）"
    assert rows[0][3] is None
    assert sink.texts == [], "启动时一条告警都 ⛔ 不许发——恢复时间还不知道（TD-20）"
    assert second.state == session.STATE_STARTING

    up_at = T0 + timedelta(hours=2)  # 12:00 第一次真正连上
    second.on_connected(up_at)

    rows = windows(second.conn)
    assert len(rows) == 1, "⛔ 不许多开一个窗口"
    assert rows[0][2] == session.format_instant(up_at), "恢复时间 = 真正连上的时间"
    assert rows[0][3] == session.CLOSED_BY_RECONNECT
    assert rows[0][4] is not None

    assert len(sink.texts) == 1
    assert "2026-09-09 10:10:00" in sink.texts[0]
    assert "2026-09-09 12:00:00" in sink.texts[0]
    assert "持续 1 小时 50 分 0 秒" in sink.texts[0], "110 分钟，⛔ 不是 30 分钟"
    assert "2026-09-09 10:40:00" not in sink.texts[0], "启动时间 ⛔ 不许出现在告警里"
    assert alerts.ALERT_RESEND_SENTENCE in sink.texts[0]


def test_starting_with_no_open_window_behaves_exactly_as_before(db_path, liveness_path):
    """裁决二的另一半：启动时本来就没有未闭合窗口 ⇒ 行为与改动前**完全一致**。

    改法 ① 只拿掉"启动时闭合"这一步，⛔ 不许顺带改变别的启动语义：已闭合已告警的
    窗口不许被重开、不许被重复告警，随后的首次 `on_connected` 也不许去动它。
    ⚠️ 重启时刻与上一次盖戳时刻取同一瞬间，避免撞上 7.7 的 `startup_gap` 规则
    （那条规则由 test_starting_while_still_offline… 单独覆盖）。
    """
    first = make_session(db_path, liveness_path, RecordingSink())
    first.start(T0)
    first.on_connected(T0)
    first.on_disconnected(T0 + timedelta(minutes=5))
    reconnected_at = T0 + timedelta(minutes=8)
    first.on_connected(reconnected_at)  # 窗口已闭合、已告警
    first.conn.close()

    sink = RecordingSink()
    second = make_session(db_path, liveness_path, sink)
    second.start(reconnected_at)

    before = windows(second.conn)
    assert len(before) == 1
    assert before[0][2] == session.format_instant(reconnected_at)
    assert before[0][3] == session.CLOSED_BY_RECONNECT
    assert sink.texts == [], "已告警过的窗口 ⛔ 不许再告警一次"

    second.on_connected(T0 + timedelta(minutes=20))
    assert windows(second.conn) == before, "首次连上 ⛔ 不许改动一个已闭合的窗口"
    assert sink.texts == []
