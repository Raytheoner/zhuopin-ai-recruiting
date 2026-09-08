"""第 7 章·中断窗口的库层断言（7.2 / 7.3）。

这三个 effect 不在 EFFECT_NODE_TO_TABLE 里（本章 ⛔ 不碰 storage/effects.py），
因此第 2 章的 assert_effect_log_identity 覆盖不到它们。恒等判据由本文件的
assert_outage_identity 自带——⛔ 不要因为"第 2 章已经有一条了"就省掉这条。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from tools.liaison import session
from tools.liaison.storage import db as liaison_db

T0 = datetime(2026, 9, 9, 10, 0, 0, tzinfo=session.CHINA_TZ)


@pytest.fixture
def conn(tmp_path):
    c = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(c)
    yield c
    c.close()


def assert_outage_identity(conn: sqlite3.Connection) -> None:
    """铁律 1 的恒等判据，本章版本：只比 INSERT 型的那个节点。

    ⛔ 不要把 effect_close_outage_window / effect_mark_window_alerted 也算进来——
    它们是 UPDATE，不产生新行，算进来这条等式会因为一个完全正当的理由变红，
    而那时最顺手的"修法"是削弱本断言。⛔ 明确写死：不许改成总数比较、不许约等于。
    """
    effect_counts = dict(
        conn.execute(
            "SELECT thread_id, COUNT(*) FROM effect_log WHERE node_name = ? GROUP BY thread_id",
            ("effect_open_outage_window",),
        ).fetchall()
    )
    business_counts = dict(
        conn.execute(
            "SELECT thread_id, COUNT(*) FROM liaison_outage_window GROUP BY thread_id"
        ).fetchall()
    )
    assert effect_counts == business_counts, (
        f"中断窗口恒等不变式破裂：effect_log={effect_counts} 表={business_counts}"
    )


def test_opening_a_window_writes_one_row_and_one_effect_log(conn):
    started = session.format_instant(T0)
    returned = session.effect_open_outage_window(
        conn,
        thread_id=session.CONNECTION_THREAD_ID,
        business_key=started,
        detected_by=session.DETECTED_BY_DISCONNECT,
    )
    assert returned == started
    rows = conn.execute(
        "SELECT started_at, detected_by, recovered_at, closed_by, alerted_at "
        "FROM liaison_outage_window"
    ).fetchall()
    assert rows == [(started, session.DETECTED_BY_DISCONNECT, None, None, None)]
    assert_outage_identity(conn)


def test_opening_the_same_started_at_twice_is_a_no_op(conn):
    """7.3 的幂等策略：按窗口起始时间去重。重复启动不重复补记。"""
    started = session.format_instant(T0)
    first = session.effect_open_outage_window(
        conn,
        thread_id=session.CONNECTION_THREAD_ID,
        business_key=started,
        detected_by=session.DETECTED_BY_STARTUP_GAP,
    )
    second = session.effect_open_outage_window(
        conn,
        thread_id=session.CONNECTION_THREAD_ID,
        business_key=started,
        detected_by=session.DETECTED_BY_STARTUP_GAP,
    )
    assert first == started
    assert second is None, "幂等命中必须返回 None，⛔ 不许再插一行"
    assert conn.execute("SELECT COUNT(*) FROM liaison_outage_window").fetchone()[0] == 1
    assert_outage_identity(conn)


def test_effect_key_format_matches_the_ironclad_rule(conn):
    """幂等键必须是 {thread_id}:{node_name}:{business_key}，business_key = 窗口起始时间。"""
    started = session.format_instant(T0)
    session.effect_open_outage_window(
        conn,
        thread_id=session.CONNECTION_THREAD_ID,
        business_key=started,
        detected_by=session.DETECTED_BY_DISCONNECT,
    )
    key = conn.execute("SELECT effect_key FROM effect_log").fetchone()[0]
    assert key == f"{session.CONNECTION_THREAD_ID}:effect_open_outage_window:{started}"


def test_format_instant_keeps_microseconds_and_the_china_offset():
    """⛔ 不许截到秒：同一秒内两次断线会撞主键，第二个窗口被幂等静默吃掉。"""
    text = session.format_instant(T0.replace(microsecond=123456))
    assert text == "2026-09-09T10:00:00.123456+08:00"


def test_format_instant_rejects_naive_datetime():
    with pytest.raises(ValueError):
        session.format_instant(datetime(2026, 9, 9, 10, 0, 0))


def _open(conn, moment, detected_by=session.DETECTED_BY_DISCONNECT):
    started = session.format_instant(moment)
    session.effect_open_outage_window(
        conn,
        thread_id=session.CONNECTION_THREAD_ID,
        business_key=started,
        detected_by=detected_by,
    )
    return started


def test_closing_a_window_sets_both_recovered_and_closed_by(conn):
    started = _open(conn, T0)
    recovered = session.format_instant(T0 + timedelta(minutes=3))
    returned = session.effect_close_outage_window(
        conn,
        thread_id=session.CONNECTION_THREAD_ID,
        business_key=started,
        recovered_at=recovered,
        closed_by=session.CLOSED_BY_RECONNECT,
    )
    assert returned == started
    row = conn.execute(
        "SELECT recovered_at, closed_by, alerted_at FROM liaison_outage_window"
    ).fetchone()
    assert row == (recovered, session.CLOSED_BY_RECONNECT, None)
    assert_outage_identity(conn)


def test_closing_the_same_window_twice_keeps_the_first_close(conn):
    """先被正常重连闭合过的窗口，下一次启动的补记 ⛔ 不许覆盖它。"""
    started = _open(conn, T0)
    first = session.format_instant(T0 + timedelta(minutes=3))
    session.effect_close_outage_window(
        conn,
        thread_id=session.CONNECTION_THREAD_ID,
        business_key=started,
        recovered_at=first,
        closed_by=session.CLOSED_BY_RECONNECT,
    )
    again = session.effect_close_outage_window(
        conn,
        thread_id=session.CONNECTION_THREAD_ID,
        business_key=started,
        recovered_at=session.format_instant(T0 + timedelta(hours=5)),
        closed_by=session.CLOSED_BY_STARTUP_BACKFILL,
    )
    assert again is None
    row = conn.execute("SELECT recovered_at, closed_by FROM liaison_outage_window").fetchone()
    assert row == (first, session.CLOSED_BY_RECONNECT)


def test_closing_a_window_that_was_never_opened_raises_and_leaves_no_trace(conn):
    """闭一个不存在的窗口是真 bug，必须炸，且 ⛔ 不许留下幂等记录。

    留下了幂等记录 = 系统认定"这件事做过了" = 真正该闭的那次永远不会再执行。
    """
    with pytest.raises(session.OutageWindowStateError):
        session.effect_close_outage_window(
            conn,
            thread_id=session.CONNECTION_THREAD_ID,
            business_key=session.format_instant(T0),
            recovered_at=session.format_instant(T0 + timedelta(minutes=1)),
            closed_by=session.CLOSED_BY_RECONNECT,
        )
    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 0
    assert_outage_identity(conn)


def test_mark_alerted_is_recorded_once_and_only_after_recovery(conn):
    started = _open(conn, T0)
    recovered = session.format_instant(T0 + timedelta(minutes=3))
    session.effect_close_outage_window(
        conn,
        thread_id=session.CONNECTION_THREAD_ID,
        business_key=started,
        recovered_at=recovered,
        closed_by=session.CLOSED_BY_RECONNECT,
    )
    alerted = session.format_instant(T0 + timedelta(minutes=4))
    assert (
        session.effect_mark_window_alerted(
            conn,
            thread_id=session.CONNECTION_THREAD_ID,
            business_key=started,
            alerted_at=alerted,
        )
        == started
    )
    assert (
        session.effect_mark_window_alerted(
            conn,
            thread_id=session.CONNECTION_THREAD_ID,
            business_key=started,
            alerted_at=session.format_instant(T0 + timedelta(minutes=9)),
        )
        is None
    )
    assert conn.execute("SELECT alerted_at FROM liaison_outage_window").fetchone()[0] == alerted


def test_select_open_windows_returns_only_unclosed_ones_oldest_first(conn):
    older = _open(conn, T0)
    newer = _open(conn, T0 + timedelta(hours=1))
    session.effect_close_outage_window(
        conn,
        thread_id=session.CONNECTION_THREAD_ID,
        business_key=newer,
        recovered_at=session.format_instant(T0 + timedelta(hours=2)),
        closed_by=session.CLOSED_BY_RECONNECT,
    )
    assert session.select_open_windows(conn) == [older]


def test_select_unalerted_closed_windows_skips_open_and_already_alerted(conn):
    open_one = _open(conn, T0)
    closed_unalerted = _open(conn, T0 + timedelta(hours=1))
    closed_alerted = _open(conn, T0 + timedelta(hours=2))
    for started, offset in ((closed_unalerted, 90), (closed_alerted, 150)):
        session.effect_close_outage_window(
            conn,
            thread_id=session.CONNECTION_THREAD_ID,
            business_key=started,
            recovered_at=session.format_instant(T0 + timedelta(minutes=offset)),
            closed_by=session.CLOSED_BY_RECONNECT,
        )
    session.effect_mark_window_alerted(
        conn,
        thread_id=session.CONNECTION_THREAD_ID,
        business_key=closed_alerted,
        alerted_at=session.format_instant(T0 + timedelta(minutes=151)),
    )
    pending = session.select_unalerted_closed_windows(conn)
    assert [row[0] for row in pending] == [closed_unalerted]
    assert open_one not in [row[0] for row in pending]


def test_table_rejects_half_closed_rows(conn):
    """CHECK 约束：recovered_at 与 closed_by 必须同时有值或同时为空。"""
    started = _open(conn, T0)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE liaison_outage_window SET recovered_at = ? WHERE started_at = ?",
            (session.format_instant(T0 + timedelta(minutes=1)), started),
        )
    conn.rollback()


def test_identity_scaffold_actually_catches_a_break(conn):
    """证伪：绕过 effect 直接插一行，恒等断言必须红。⛔ 不许只写断言不验它会红。"""
    _open(conn, T0)
    conn.execute(
        "INSERT INTO liaison_outage_window (started_at, thread_id, detected_by) "
        "VALUES (?, ?, ?)",
        ("2026-09-09T23:00:00.000000+08:00", session.CONNECTION_THREAD_ID, "disconnect_event"),
    )
    with pytest.raises(AssertionError):
        assert_outage_identity(conn)
    conn.rollback()
