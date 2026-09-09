"""第 6 章·通知台账与幂等落库（6.6 / 6.9 的落库部分）。

这个 effect 不在 EFFECT_NODE_TO_TABLE 里（本章 ⛔ 不碰 storage/effects.py，理由见
本章计划 Architecture 第 5 条），因此第 2 章的 assert_effect_log_identity 覆盖不到
它。恒等判据由本文件的 assert_group_notify_identity 自带——⛔ 不要因为"别处已经
有一条了"就省掉它。
"""

from __future__ import annotations

import re
import sqlite3

import pytest

from tools.liaison import alerts
from tools.liaison.notify import guard, ratelimit, store
from tools.liaison.storage import db as liaison_db


@pytest.fixture
def conn(tmp_path):
    c = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(c)
    yield c
    c.close()


class RecordingSink:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def send(self, text: str) -> None:
        self.texts.append(text)


class BoomSink:
    def send(self, text: str) -> None:
        raise RuntimeError("群通知本身也挂了")


def assert_group_notify_identity(conn: sqlite3.Connection) -> None:
    """铁律 1 的恒等判据，本章版本。

    `effect_send_group_notify` 是本章唯一的 INSERT 型 effect，因此这条等式只比它。
    ⛔ 不许改成总数比较、⛔ 不许约等于——等式一旦松动，"发了没记 / 记了没发"
    这两种状态就再也没有机器判据。
    """
    effect_counts = dict(
        conn.execute(
            "SELECT thread_id, COUNT(*) FROM effect_log WHERE node_name = ? GROUP BY thread_id",
            ("effect_send_group_notify",),
        ).fetchall()
    )
    business_counts = dict(
        conn.execute(
            "SELECT thread_id, COUNT(*) FROM liaison_group_notify GROUP BY thread_id"
        ).fetchall()
    )
    assert effect_counts == business_counts, (
        f"群通知恒等不变式破裂：effect_log={effect_counts} 表={business_counts}"
    )


def make_plan(text="值守通知", *, limit=4096, attachment=True):
    return guard.compute_notify_plan(text, limit_bytes=limit, attachment_supported=attachment)


def stub_deliver(outcome_state, *, attempts=1, errcode=None, error=None):
    """把"发"这一步换成一个桩：本任务只验落库，真投递在 Task 5。"""

    def deliver(plan):
        return store.NotifyRecord(
            state=outcome_state, attempts=attempts, last_errcode=errcode, last_error=error
        )

    return deliver


def test_a_sent_notify_writes_exactly_one_row_and_one_effect_log(conn):
    plan = make_plan()
    sink = RecordingSink()
    state = store.effect_send_group_notify(
        conn,
        thread_id=store.GROUP_NOTIFY_THREAD_ID,
        business_key=plan.digest,
        plan=plan,
        deliver=stub_deliver(store.STATE_SENT),
        alert_sink=sink,
    )
    assert state == store.STATE_SENT
    rows = conn.execute(
        "SELECT digest, state, mode, attempts, sent_at IS NOT NULL FROM liaison_group_notify"
    ).fetchall()
    assert rows == [(plan.digest, store.STATE_SENT, guard.MODE_DIRECT, 1, 1)]
    assert sink.texts == []  # 成功不告警
    assert_group_notify_identity(conn)


def test_the_same_content_is_never_sent_twice(conn):
    """6.6 逐字：幂等键含内容摘要，重试与恢复重跑 ⛔ 不产生第二条通知。"""
    plan = make_plan()
    sent_count = {"n": 0}

    def counting_deliver(p):
        sent_count["n"] += 1
        return store.NotifyRecord(state=store.STATE_SENT, attempts=1, last_errcode=None, last_error=None)

    kwargs = dict(
        thread_id=store.GROUP_NOTIFY_THREAD_ID,
        business_key=plan.digest,
        plan=plan,
        deliver=counting_deliver,
        alert_sink=RecordingSink(),
    )
    assert store.effect_send_group_notify(conn, **kwargs) == store.STATE_SENT
    assert store.effect_send_group_notify(conn, **kwargs) is None  # 幂等命中：装饰器返回 None
    assert sent_count["n"] == 1, "第二次调用 ⛔ 不许再发一遍"
    assert conn.execute("SELECT COUNT(*) FROM liaison_group_notify").fetchone()[0] == 1
    assert_group_notify_identity(conn)


def test_same_content_on_two_threads_each_writes_its_own_row(conn):
    """主键 **(thread_id, digest)** 与幂等键同域：同内容换一条 thread 照样落行。

    ⛔ 主键退回成全局 `digest TEXT PRIMARY KEY` 本条必红：幂等预检按 thread 分域
    ⇒ 第二次不命中 ⇒ `deliver()` **真把消息发进群** ⇒ INSERT 撞主键 ⇒ 事务回滚 ⇒
    群里多一条通知而台账与 effect_log 各 0 行。那种状态下恒等式因为 `0 == 0`
    仍是绿的，机器判据抓不到——正是本章要消灭的那类谎。
    """
    plan = make_plan("同一条内容")
    sent = []

    def counting_deliver(p):
        sent.append(p.digest)
        return store.NotifyRecord(state=store.STATE_SENT, attempts=1)

    for thread_id in ("thread-A", "thread-B"):
        state = store.effect_send_group_notify(
            conn,
            thread_id=thread_id,
            business_key=plan.digest,
            plan=plan,
            deliver=counting_deliver,
            alert_sink=RecordingSink(),
        )
        assert state == store.STATE_SENT, f"{thread_id} 这一条应当落行，⛔ 不许抛 IntegrityError"

    assert sent == [plan.digest, plan.digest], "两条 thread 各发一次"
    rows = conn.execute(
        "SELECT thread_id, COUNT(*) FROM liaison_group_notify GROUP BY thread_id "
        "ORDER BY thread_id"
    ).fetchall()
    assert rows == [("thread-A", 1), ("thread-B", 1)], "两条 thread 各 1 行"
    assert_group_notify_identity(conn)

    # 同 thread 再来一次仍然幂等：分域 ⛔ 不等于放宽。
    assert (
        store.effect_send_group_notify(
            conn,
            thread_id="thread-A",
            business_key=plan.digest,
            plan=plan,
            deliver=counting_deliver,
            alert_sink=RecordingSink(),
        )
        is None
    )
    assert len(sent) == 2, "同 thread 同内容 ⛔ 不许再发一遍"
    assert_group_notify_identity(conn)


def test_effect_key_is_thread_node_digest(conn):
    """幂等键形态逐字：{thread_id}:effect_send_group_notify:{内容摘要}。"""
    plan = make_plan()
    store.effect_send_group_notify(
        conn,
        thread_id=store.GROUP_NOTIFY_THREAD_ID,
        business_key=plan.digest,
        plan=plan,
        deliver=stub_deliver(store.STATE_SENT),
        alert_sink=RecordingSink(),
    )
    key = conn.execute("SELECT effect_key FROM effect_log").fetchone()[0]
    assert key == f"{store.GROUP_NOTIFY_THREAD_ID}:effect_send_group_notify:{plan.digest}"
    assert_group_notify_identity(conn)


def test_exhausted_retries_persist_as_pending_resend_and_alert(conn):
    """spec 场景「重试耗尽」：持久化为待重发记录 **且** 发出告警，⛔ 不静默丢弃。"""
    plan = make_plan()
    sink = RecordingSink()
    state = store.effect_send_group_notify(
        conn,
        thread_id=store.GROUP_NOTIFY_THREAD_ID,
        business_key=plan.digest,
        plan=plan,
        deliver=stub_deliver(store.STATE_PENDING_RESEND, attempts=5, errcode=45009, error="limit"),
        alert_sink=sink,
    )
    assert state == store.STATE_PENDING_RESEND
    row = conn.execute(
        "SELECT state, attempts, last_errcode, sent_at, body FROM liaison_group_notify"
    ).fetchone()
    assert row[0] == store.STATE_PENDING_RESEND
    assert row[1] == 5
    assert row[2] == 45009
    assert row[3] is None
    assert row[4] == plan.full_text, "待重发行必须存**完整原文**，⛔ 不是提要"
    assert len(sink.texts) == 1
    assert "45009" in sink.texts[0]
    assert_group_notify_identity(conn)


def test_rejected_notify_is_recorded_and_alerted_and_never_sent(conn):
    """spec 场景「无法降级时拒发」：不发送 + 告警说明拒发原因。"""
    plan = make_plan("中" * 2000, limit=4096, attachment=False)
    assert plan.mode == guard.MODE_REJECT
    sink = RecordingSink()
    attempted = {"n": 0}

    def never_called(p):
        attempted["n"] += 1
        raise AssertionError("拒发的通知 ⛔ 不许进投递路径")

    state = store.effect_send_group_notify(
        conn,
        thread_id=store.GROUP_NOTIFY_THREAD_ID,
        business_key=plan.digest,
        plan=plan,
        deliver=never_called,
        alert_sink=sink,
    )
    assert state == store.STATE_REJECTED
    assert attempted["n"] == 0
    assert len(sink.texts) == 1
    assert guard.REJECT_NO_ATTACHMENT_CHANNEL in sink.texts[0]
    assert_group_notify_identity(conn)


def _record(conn, text, state, *, errcode=None):
    """落一行台账，返回 plan。errcode 只在 pending_resend 上有意义。"""
    plan = make_plan(text)
    store.effect_send_group_notify(
        conn,
        thread_id=store.GROUP_NOTIFY_THREAD_ID,
        business_key=plan.digest,
        plan=plan,
        deliver=stub_deliver(state, errcode=errcode, error=None if errcode is None else "boom"),
        alert_sink=RecordingSink(),
    )
    return plan


def test_pending_resends_are_listable(conn):
    """给第 8 章的重发驱动器留的把手。本章 ⏸ 不实现驱动器本身。"""
    _record(conn, "a", store.STATE_SENT)
    _record(conn, "b", store.STATE_PENDING_RESEND, errcode=ratelimit.RATE_LIMIT_ERRCODE)
    rows = store.select_pending_resends(conn)
    assert [r["body"] for r in rows] == ["b"]
    assert_group_notify_identity(conn)


# --------------------------------------------------------------------------
# TD-25：`select_pending_resends` 按 errcode 收窄，被滤掉的行必须仍可被取到。
# --------------------------------------------------------------------------


def test_retryable_errcodes_contains_only_the_documented_rate_limit_code(conn):
    """口径常量本身就是判据：⛔ 不许"看起来像瞬时"就往里加。"""
    assert store.RETRYABLE_ERRCODES == frozenset({ratelimit.RATE_LIMIT_ERRCODE})
    assert store.RETRYABLE_ERRCODES, "集合为空会把 IN () 拼成非法 SQL"


def test_rate_limited_row_is_offered_for_automatic_resend(conn):
    """`45009` = 限流，是唯一有明文依据的瞬时错误，可自动重发。"""
    _record(conn, "limited", store.STATE_PENDING_RESEND, errcode=ratelimit.RATE_LIMIT_ERRCODE)
    assert [r["body"] for r in store.select_pending_resends(conn)] == ["limited"]
    assert store.select_manual_intervention_resends(conn) == []


@pytest.mark.parametrize(
    ("body", "errcode"),
    [("bot-not-in-group", 93000), ("bad-credentials", 40001)],
)
def test_permanent_errcodes_are_never_offered_for_automatic_resend(conn, body, errcode):
    """`93000`/`40001` 是配置/权限问题，重试多少次都是同一结果——TD-25 的死行。

    🔴 它们 ⛔ 不许因此消失：人工介入口径必须原样取得它们。
    """
    _record(conn, body, store.STATE_PENDING_RESEND, errcode=errcode)
    assert store.select_pending_resends(conn) == []
    manual = store.select_manual_intervention_resends(conn)
    assert [(r["body"], r["last_errcode"]) for r in manual] == [(body, errcode)]


def test_missing_errcode_counts_as_manual_intervention(conn):
    """传输层抛异常时没有业务码。⛔ 不猜它可重试——保守方向交人工。"""
    _record(conn, "transport-blew-up", store.STATE_PENDING_RESEND, errcode=None)
    assert store.select_pending_resends(conn) == []
    manual = store.select_manual_intervention_resends(conn)
    assert [r["body"] for r in manual] == ["transport-blew-up"]
    assert manual[0]["last_errcode"] is None


def test_the_two_pending_views_partition_every_pending_row(conn):
    """并集恒等于全部 `pending_resend`，交集为空——"过滤"⛔ 不等于"丢弃"。"""
    _record(conn, "sent-one", store.STATE_SENT)
    _record(conn, "limited", store.STATE_PENDING_RESEND, errcode=ratelimit.RATE_LIMIT_ERRCODE)
    _record(conn, "not-in-group", store.STATE_PENDING_RESEND, errcode=93000)
    _record(conn, "no-code", store.STATE_PENDING_RESEND, errcode=None)

    auto = {r["digest"] for r in store.select_pending_resends(conn)}
    manual = {r["digest"] for r in store.select_manual_intervention_resends(conn)}
    all_pending = {
        row[0]
        for row in conn.execute(
            "SELECT digest FROM liaison_group_notify WHERE state = ?",
            (store.STATE_PENDING_RESEND,),
        )
    }
    assert auto | manual == all_pending
    assert auto & manual == set()
    assert len(all_pending) == 3


def test_manual_intervention_query_does_not_touch_the_shared_row_factory(conn):
    """与 `select_pending_resends` 同一条约束：⛔ 不污染共享连接的 row_factory。"""
    before = conn.row_factory
    _record(conn, "not-in-group", store.STATE_PENDING_RESEND, errcode=93000)
    rows = store.select_manual_intervention_resends(conn)
    assert rows[0]["body"] == "not-in-group"
    assert conn.row_factory is before


def test_alert_channel_failure_does_not_abort_the_record(conn, caplog):
    """opener 约束 6：alerts 通道自身失败只记日志、⛔ 不中止。"""
    plan = make_plan()
    state = store.effect_send_group_notify(
        conn,
        thread_id=store.GROUP_NOTIFY_THREAD_ID,
        business_key=plan.digest,
        plan=plan,
        deliver=stub_deliver(store.STATE_PENDING_RESEND, attempts=5, errcode=45009),
        alert_sink=BoomSink(),
    )
    assert state == store.STATE_PENDING_RESEND
    assert conn.execute("SELECT COUNT(*) FROM liaison_group_notify").fetchone()[0] == 1
    assert_group_notify_identity(conn)


def test_state_enum_is_enforced_by_the_table(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO liaison_group_notify "
            "(digest, thread_id, channel, state, mode, byte_length, limit_bytes, body) "
            "VALUES ('d','t','group_webhook','半成功','direct',1,4096,'x')"
        )


def test_sent_requires_a_timestamp_and_others_must_not_have_one(conn):
    """「已送达必带时间戳」与「未送达必不带」由一条表约束同时钉住。"""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO liaison_group_notify "
            "(digest, thread_id, channel, state, mode, byte_length, limit_bytes, body, sent_at) "
            "VALUES ('d1','t','group_webhook','sent','direct',1,4096,'x',NULL)"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO liaison_group_notify "
            "(digest, thread_id, channel, state, mode, byte_length, limit_bytes, body, sent_at) "
            "VALUES ('d2','t','group_webhook','rejected','reject',1,4096,'x','2026-09-09')"
        )


def test_mode_enum_is_enforced_by_the_table(conn):
    """`mode` 只有 direct/degraded/reject 三种，⛔ 不许写进第四种。

    删掉 schema 里那条 `CHECK (mode IN (...))` 本条必红。mode 是 8.x 分流与统计的
    依据（比如"降级了多少条"），混进一个未知值不会报错，只会让统计悄悄漏掉它。
    """
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO liaison_group_notify "
            "(digest, thread_id, channel, state, mode, byte_length, limit_bytes, body) "
            "VALUES ('dm','t','group_webhook','pending_resend','半降级',1,4096,'x')"
        )
    assert conn.execute("SELECT COUNT(*) FROM liaison_group_notify").fetchone()[0] == 0


def test_business_key_must_be_the_content_digest(conn):
    """铁律 1 的幂等键契约在这里唯一强制一次：`business_key` 必须 == `plan.digest`。

    删掉 `store.py` 里那三行 `raise ValueError` 本条必红。传错了不会当场报错，
    只会让幂等键指向一条**与内容无关**的键——从此"这条通知发没发过"的预检答案
    与内容脱钩，重复与丢失都可能发生，而两者都没有症状。
    ⛔ 断言里同时要求"什么都没发出去"：契约违规必须在 `deliver()` **之前**拦下。
    """
    plan = make_plan()
    attempted = {"n": 0}

    def never_called(p):
        attempted["n"] += 1
        raise AssertionError("契约违规时 ⛔ 不许进投递路径")

    with pytest.raises(ValueError) as exc:
        store.effect_send_group_notify(
            conn,
            thread_id=store.GROUP_NOTIFY_THREAD_ID,
            business_key="不是摘要",
            plan=plan,
            deliver=never_called,
            alert_sink=RecordingSink(),
        )
    assert plan.digest in str(exc.value)
    assert attempted["n"] == 0
    assert conn.execute("SELECT COUNT(*) FROM liaison_group_notify").fetchone()[0] == 0


def test_select_pending_resends_does_not_touch_the_shared_row_factory(conn):
    """只读函数 ⛔ 不许改整条共享连接的 `row_factory`。

    连接是全应用共享的：一旦被改成 `sqlite3.Row`，它之后**所有**按位置取值的
    `fetchone()[0]` 都会静默改变含义。行类型只设在本次查询的 cursor 上
    （同包范例：`tools/liaison/queue.py::list_tasks`）。
    把实现改回 `conn.row_factory = sqlite3.Row` 本条必红。
    """
    plan = make_plan("待重发")
    store.effect_send_group_notify(
        conn,
        thread_id=store.GROUP_NOTIFY_THREAD_ID,
        business_key=plan.digest,
        plan=plan,
        deliver=stub_deliver(store.STATE_PENDING_RESEND, attempts=5, errcode=45009),
        alert_sink=RecordingSink(),
    )

    before = conn.row_factory
    assert before is None, "前置条件：本 fixture 的连接不设 row_factory"
    rows = store.select_pending_resends(conn)
    assert conn.row_factory is before, "select_pending_resends 污染了共享连接的 row_factory"

    # 返回的行仍然按列名可取——行类型设在 cursor 上，功能一点没少。
    assert [r["body"] for r in rows] == ["待重发"]
    assert rows[0]["state"] == store.STATE_PENDING_RESEND
    # 连接本身仍是元组行：按位置取值的既有调用方不受影响。
    assert isinstance(
        conn.execute("SELECT digest FROM liaison_group_notify").fetchone(), tuple
    )


#: SQLite `datetime('now')` 的字面量形状：UTC、秒级、无时区后缀、日期与时间用空格分隔。
_SQLITE_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def test_sent_at_and_created_at_share_one_timestamp_format(conn):
    """`sent_at` 与同表 `created_at` 必须**逐字同口径**。

    `created_at` 由 `DEFAULT (datetime('now'))` 写入。`_now_text()` 一旦改回
    `isoformat()`，两列格式就不同（`'2026-...T01:05:56.269858+00:00'` vs
    `'2026-... 01:05:56'`），本条必红。不是洁癖：8.1-8.2 的留存期清理要在这张表上
    按时间范围比较，而跨列排序也会静默出错——`'T'` = 0x54 > `' '` = 0x20。
    """
    plan = make_plan()
    store.effect_send_group_notify(
        conn,
        thread_id=store.GROUP_NOTIFY_THREAD_ID,
        business_key=plan.digest,
        plan=plan,
        deliver=stub_deliver(store.STATE_SENT),
        alert_sink=RecordingSink(),
    )
    created_at, sent_at = conn.execute(
        "SELECT created_at, sent_at FROM liaison_group_notify"
    ).fetchone()

    # 同一个正则同时匹配两者。
    assert _SQLITE_DATETIME_RE.match(created_at), f"created_at 形状变了：{created_at!r}"
    assert _SQLITE_DATETIME_RE.match(sent_at), f"sent_at 与 datetime('now') 不同口径：{sent_at!r}"

    # SQLite 的 datetime() 能原样解析，且解析结果与原串逐字相等（没有被"纠正"过）。
    parsed = conn.execute("SELECT datetime(?), datetime(?)", (created_at, sent_at)).fetchone()
    assert parsed == (created_at, sent_at)

    # 字符串比较即时间比较：留存期清理靠的就是这条。
    assert min(created_at, sent_at) == created_at


def test_generic_alert_emitter_never_raises():
    """`effect_emit_alert` ⛔ 永不抛异常，返回是否送成功。"""
    assert alerts.effect_emit_alert(RecordingSink(), "hello") is True
    assert alerts.effect_emit_alert(BoomSink(), "hello") is False


# ---------------------------------------------------------------------------
# TD-28：跨字段的荒唐组合必须被**表结构**挡住
# ---------------------------------------------------------------------------


def _insert_notify_row(conn: sqlite3.Connection, **overrides) -> None:
    """绕开 `effect_send_group_notify` 直连 SQL 写一行。

    ⚠️ **刻意绕过代码路径**：本组用例要验的是「表结构自己守不守得住」，不是
    「现有代码路径产不产得出这些行」。走代码路径等于用被测对象证明被测对象——
    TD-28 登记时就是用直连 SQL 逐条**实证写入成功**的，还债也必须用同一把尺子量。
    """
    row = {
        "digest": "d-1",
        "thread_id": "group-notify",
        "channel": "group_webhook",
        "state": "pending_resend",
        "mode": "direct",
        "byte_length": 10,
        "limit_bytes": 4096,
        "attempts": 1,
        "last_errcode": None,
        "last_error": None,
        "body": "正文",
        "sent_at": None,
    }
    row.update(overrides)
    conn.execute(
        "INSERT INTO liaison_group_notify"
        " (digest, thread_id, channel, state, mode, byte_length, limit_bytes,"
        "  attempts, last_errcode, last_error, body, sent_at)"
        " VALUES (:digest, :thread_id, :channel, :state, :mode, :byte_length, :limit_bytes,"
        "  :attempts, :last_errcode, :last_error, :body, :sent_at)",
        row,
    )


@pytest.mark.parametrize(
    "overrides, why",
    [
        (
            {"state": "sent", "mode": "reject", "sent_at": "2026-09-09 12:00:00"},
            "被拒发的通知不可能已送达",
        ),
        (
            {"state": "rejected", "mode": "direct", "attempts": 99},
            "拒发的行不可能是 direct 模式，更不可能试了 99 次",
        ),
        (
            {"state": "pending_resend", "mode": "reject"},
            "reject 模式只能配 rejected 状态，⛔ 不许待重发",
        ),
        ({"byte_length": -5}, "正文长度不可能是负数"),
        ({"limit_bytes": -1}, "阈值不可能是负数"),
        ({"limit_bytes": 0}, "阈值为 0 意味着任何正文都超限，那不是阈值是死锁"),
        ({"attempts": -3}, "尝试次数不可能是负数"),
    ],
    ids=[
        "sent-but-rejected-mode",
        "rejected-but-direct-mode",
        "pending-but-reject-mode",
        "negative-byte-length",
        "negative-limit-bytes",
        "zero-limit-bytes",
        "negative-attempts",
    ],
)
def test_absurd_group_notify_rows_are_refused_by_the_schema(conn, overrides, why):
    """结构自己守住，⛔ 不指望"现有代码路径产不出这些行"。

    "产不出"是**调用方**保证的，不是结构保证的——第二个调用方一出现就没人替它守，
    而这张表是台账：一行"被拒发但已送达"的记录会让所有基于它的报表悄悄说谎。
    """
    with pytest.raises(sqlite3.IntegrityError):
        _insert_notify_row(conn, **overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"state": "rejected", "mode": "reject", "attempts": 0},
        {"state": "sent", "mode": "direct", "sent_at": "2026-09-09 12:00:00"},
        {"state": "pending_resend", "mode": "degraded", "attempts": 5},
        {"byte_length": 0},
    ],
    ids=["rejected-reject", "sent-direct", "pending-degraded", "empty-body-length"],
)
def test_legitimate_group_notify_rows_still_get_through(conn, overrides):
    """对照组：新增的 CHECK ⛔ 不许误伤正路。

    收紧约束最常见的失败模式不是"没挡住"，是"顺手把合法的也挡了"——而那会在
    真发时才炸。`byte_length=0` 必须放行（空正文长度是 0，⛔ 不是非法值）。
    """
    _insert_notify_row(conn, **overrides)
    assert conn.execute("SELECT COUNT(*) FROM liaison_group_notify").fetchone()[0] == 1
