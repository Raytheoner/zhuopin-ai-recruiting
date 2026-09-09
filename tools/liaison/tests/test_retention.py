"""留存期清理（tasks 8.1–8.2）。

⛔ 全部用 fake 时钟与 `tmp_path`：⛔ 不 sleep、⛔ 不碰真实的 `data/`
（opener 约束 3）。任何一条用例跑完之后仓库里不许多出一个文件。
"""

from __future__ import annotations

import datetime

import pytest

from tools.liaison import retention
from tools.liaison.retention import MessageRow, RetentionConfigError

CHINA_TZ = datetime.timezone(datetime.timedelta(hours=8))
NOW = datetime.datetime(2026, 9, 9, 12, 0, 0, tzinfo=CHINA_TZ)


def _row(msgid, *, archived_at, has_queue_row=False, attachments_json="[]", thread_id="u1"):
    return MessageRow(
        msgid=msgid,
        thread_id=thread_id,
        archived_at=archived_at,
        attachments_json=attachments_json,
        has_queue_row=has_queue_row,
    )


def test_default_retention_days_is_180():
    """design D13 逐字：默认 180。⛔ 不许悄悄改成别的数。"""
    assert retention.DEFAULT_RETENTION_DAYS == 180
    assert retention.load_retention_days({}) == 180


def test_retention_days_comes_from_env_when_set():
    assert retention.load_retention_days({"HR_LIAISON_RETENTION_DAYS": "30"}) == 30
    assert retention.load_retention_days({"HR_LIAISON_RETENTION_DAYS": " 45 "}) == 45


def test_blank_env_falls_back_to_default():
    """空串 = 没配（与 config.py 的 `_is_blank` 同口径），⛔ 不算 0。"""
    assert retention.load_retention_days({"HR_LIAISON_RETENTION_DAYS": "   "}) == 180


@pytest.mark.parametrize("bad", ["abc", "180.5", "0", "-1", "1e3"])
def test_invalid_retention_days_is_fail_closed(bad):
    """配错就拒绝跑，⛔ 不许静默退回默认值。

    退回默认值意味着一个手滑写成 `18` 少一个 0 的配置**看起来生效了**，
    实际按 180 跑；反过来 `0` 会当场删光全部归档。两个方向都不可接受，
    唯一安全的处置是拒绝执行。
    """
    with pytest.raises(RetentionConfigError):
        retention.load_retention_days({"HR_LIAISON_RETENTION_DAYS": bad})


def test_parse_archived_at_treats_sqlite_now_as_utc():
    """🔴 冲突 C：`datetime('now')` 给的是 UTC 且不带后缀。

    ⛔ 不许当本地时间解析——本机在 EDT，差 12 小时，且不会报错。
    """
    moment = retention.parse_archived_at("2026-09-09 10:00:00")
    assert moment == datetime.datetime(2026, 9, 9, 10, 0, tzinfo=datetime.timezone.utc)


def test_parse_archived_at_keeps_explicit_offset():
    moment = retention.parse_archived_at("2026-09-09T10:00:00+08:00")
    assert moment.utcoffset() == datetime.timedelta(hours=8)


@pytest.mark.parametrize("bad", ["", "   ", "not-a-time", None, 20260909])
def test_parse_archived_at_rejects_garbage(bad):
    with pytest.raises(ValueError):
        retention.parse_archived_at(bad)


def test_compute_expired_deletes_only_rows_older_than_cutoff():
    """8.2 逐字要求的「超期被清理一条」在纯函数层的形态。"""
    fresh = _row("m-fresh", archived_at="2026-09-01 00:00:00")
    expired = _row("m-old", archived_at="2026-01-01 00:00:00")
    split = retention.compute_expired(NOW, 180, [fresh, expired])
    assert [item.msgid for item in split.deletable] == ["m-old"]
    assert split.blocked_by_queue == ()
    assert split.undecidable == ()


def test_compute_expired_keeps_the_row_exactly_on_the_boundary():
    """恰好等于 cutoff 的行**保留**（判据是严格小于）。

    边界上多留一天永远是安全方向，少留一天是不可逆的删除。
    """
    cutoff = retention.compute_cutoff(NOW, 180)
    on_boundary = _row("m-edge", archived_at=cutoff.astimezone(datetime.timezone.utc)
                       .strftime("%Y-%m-%d %H:%M:%S"))
    split = retention.compute_expired(NOW, 180, [on_boundary])
    assert split.deletable == ()


def test_compute_expired_puts_queued_rows_in_their_own_bucket():
    """🔴 冲突 B：有队列行的消息 ⛔ 不删，但也 ⛔ 不静默——单独成桶。"""
    queued = _row("m-queued", archived_at="2026-01-01 00:00:00", has_queue_row=True)
    split = retention.compute_expired(NOW, 180, [queued])
    assert split.deletable == ()
    assert [item.msgid for item in split.blocked_by_queue] == ["m-queued"]


def test_compute_expired_reports_unparseable_rows_instead_of_guessing():
    """坏数据不猜、不删、不吞——进 undecidable 桶，由编排层告警。"""
    bad_time = _row("m-bad-time", archived_at="昨天")
    bad_json = _row("m-bad-json", archived_at="2026-01-01 00:00:00", attachments_json="{")
    split = retention.compute_expired(NOW, 180, [bad_time, bad_json])
    assert split.deletable == ()
    assert {item.subject for item in split.undecidable} == {"m-bad-time", "m-bad-json"}


def test_compute_expired_extracts_relative_paths():
    row = _row(
        "m-att",
        archived_at="2026-01-01 00:00:00",
        attachments_json='[{"filename": "a.xlsx", "relative_path": "u1/20260101/m-att__a.xlsx",'
                         ' "byte_length": 3, "sha256": "x"}]',
    )
    split = retention.compute_expired(NOW, 180, [row])
    assert split.deletable[0].relative_paths == ("u1/20260101/m-att__a.xlsx",)


class _ClockReadForbidden(datetime.datetime):
    """`datetime.datetime` 的替身：`now()` / `utcnow()` 一被调用就炸。

    其余方法（`fromisoformat`、算术、比较……）原样继承自真实的
    `datetime.datetime`，所以只要 `compute_expired` 没有偷偷读真实时钟，
    它的行为应当与替换前完全一致。
    """

    @classmethod
    def now(cls, tz=None):  # noqa: D102 - 见类 docstring
        raise AssertionError("compute_expired 不许读真实时钟（now()）")

    @classmethod
    def utcnow(cls):  # noqa: D102 - 见类 docstring
        raise AssertionError("compute_expired 不许读真实时钟（utcnow()）")


def test_compute_expired_is_pure_and_takes_no_clock(monkeypatch):
    """时钟注入（opener 约束 3）：把 `datetime.datetime` 换成会炸的替身，
    纯函数仍必须能跑完——它一次都不许读真实时钟。"""
    row = _row("m-old", archived_at="2026-01-01 00:00:00")
    monkeypatch.setattr(retention.datetime, "datetime", _ClockReadForbidden)
    split = retention.compute_expired(NOW, 180, [row])
    assert [item.msgid for item in split.deletable] == ["m-old"]
    assert retention.compute_expired(NOW, 180, [row]) == split  # 同输入同输出


import sqlite3

from tools.liaison.storage import db as liaison_db
from tools.liaison.storage.effects import effect_archive_message, effect_enqueue_task
from tools.liaison.tests.test_liaison_effects import assert_effect_log_identity

OLD = "2026-01-01 00:00:00"   # 相对 NOW 已超 180 天
NEW = "2026-09-01 00:00:00"   # 相对 NOW 未超期


@pytest.fixture()
def conn(tmp_path):
    """⛔ 不用真 data/：库落在 tmp_path 里，用例跑完随 tmp_path 一起消失。"""
    connection = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(connection)
    yield connection
    connection.close()


def _archive(conn, msgid, *, thread_id="u1", archived_at=OLD, attachments_json="[]"):
    effect_archive_message(
        conn,
        thread_id=thread_id,
        business_key=msgid,
        sender_userid=thread_id,
        received_at="2026-01-01T08:00:00+08:00",
        msgtype="text",
        content="",
        attachments_json=attachments_json,
    )
    # `archived_at` 由列默认值写成"现在"，测试要的是"很久以前"——直接改这一列。
    # ⛔ 不许在生产代码里提供"改 archived_at"的入口，那等于给留存期开后门。
    conn.execute("UPDATE liaison_message SET archived_at = ? WHERE msgid = ?", (archived_at, msgid))
    conn.commit()


def test_expired_message_row_is_deleted(conn):
    """8.2 逐字：「超期被清理一条」。"""
    _archive(conn, "m-old", archived_at=OLD)
    _archive(conn, "m-new", archived_at=NEW)
    rows = retention.load_message_rows(conn)
    split = retention.compute_expired(NOW, 180, rows)
    deleted, failures = retention.delete_expired_ledger_rows(conn, split.deletable)
    assert deleted == ("m-old",)
    assert failures == ()
    remaining = [r[0] for r in conn.execute("SELECT msgid FROM liaison_message")]
    assert remaining == ["m-new"]


def test_effect_log_is_never_deleted(conn):
    """opener 约束 2：⛔ 不删 effect_log——它是审计证据。

    删掉一行台账之后，`effect_archive_message` 那一行**必须**还在，
    并且多出一行 `effect_delete_expired_message`。
    """
    _archive(conn, "m-old", archived_at=OLD)
    retention.delete_expired_ledger_rows(
        conn, retention.compute_expired(NOW, 180, retention.load_message_rows(conn)).deletable
    )
    nodes = sorted(r[0] for r in conn.execute("SELECT node_name FROM effect_log"))
    assert nodes == ["effect_archive_message", "effect_delete_expired_message"]


def test_message_with_queue_row_is_never_deleted(conn):
    """🔴 冲突 B：有队列行的超期消息 ⛔ 不删，且队列行一行不少。"""
    _archive(conn, "m-queued", archived_at=OLD)
    effect_enqueue_task(
        conn,
        thread_id="u1",
        business_key="m-queued",
        sender_userid="u1",
        received_at="2026-01-01T08:00:00+08:00",
        summary="s",
    )
    conn.commit()
    split = retention.compute_expired(NOW, 180, retention.load_message_rows(conn))
    deleted, failures = retention.delete_expired_ledger_rows(conn, split.deletable)
    assert deleted == ()
    assert [item.msgid for item in split.blocked_by_queue] == ["m-queued"]
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 1


def test_foreign_key_is_the_second_line_of_defence(conn):
    """就算前面的判定被绕过，外键也必须拦住删父行这件事。

    ⛔ 这条变红时的正确修法**不是**去关 `PRAGMA foreign_keys`。
    """
    _archive(conn, "m-queued", archived_at=OLD)
    effect_enqueue_task(
        conn, thread_id="u1", business_key="m-queued", sender_userid="u1",
        received_at="2026-01-01T08:00:00+08:00", summary="s",
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        retention.effect_delete_expired_message(conn, thread_id="u1", business_key="m-queued")
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 1


def test_running_twice_is_a_no_op(conn):
    """tasks 8.1 逐字：「重复执行安全」。

    第二遍既不该再删出东西，也不该报失败——按年龄判定时第一遍已经把行删掉了，
    第二遍根本扫不到它。
    """
    _archive(conn, "m-old", archived_at=OLD)
    first = retention.delete_expired_ledger_rows(
        conn, retention.compute_expired(NOW, 180, retention.load_message_rows(conn)).deletable
    )
    second = retention.delete_expired_ledger_rows(
        conn, retention.compute_expired(NOW, 180, retention.load_message_rows(conn)).deletable
    )
    assert first[0] == ("m-old",)
    assert second == ((), ())


def assert_retention_accounting(conn):
    """被清理过的 thread 上，铁律 1 的恒等式换成这条**更强**的等式：

        归档 effect 行数 == 台账行数 + 清理 effect 行数

    差额必须被"清理过多少条"逐条解释干净，⛔ 不许有解释不掉的余数。
    """
    for (thread_id,) in conn.execute(
        "SELECT DISTINCT thread_id FROM effect_log WHERE node_name = ?",
        (retention.RETENTION_DELETE_NODE,),
    ).fetchall():
        archived = conn.execute(
            "SELECT COUNT(*) FROM effect_log WHERE node_name = 'effect_archive_message' "
            "AND thread_id = ?", (thread_id,)
        ).fetchone()[0]
        alive = conn.execute(
            "SELECT COUNT(*) FROM liaison_message WHERE thread_id = ?", (thread_id,)
        ).fetchone()[0]
        cleaned = conn.execute(
            "SELECT COUNT(*) FROM effect_log WHERE node_name = ? AND thread_id = ?",
            (retention.RETENTION_DELETE_NODE, thread_id),
        ).fetchone()[0]
        assert archived == alive + cleaned, (
            f"留存清理记账不平：thread={thread_id} 归档 {archived} != 存活 {alive} + 已清 {cleaned}"
        )


def test_identity_still_holds_after_cleanup(conn):
    """冲突 A：清理过的 thread 走记账式等式，没清理过的 thread 仍走严格恒等。"""
    _archive(conn, "m-old", thread_id="u1", archived_at=OLD)
    _archive(conn, "m-new", thread_id="u1", archived_at=NEW)
    _archive(conn, "m-other", thread_id="u2", archived_at=NEW)
    retention.delete_expired_ledger_rows(
        conn, retention.compute_expired(NOW, 180, retention.load_message_rows(conn)).deletable
    )
    assert_effect_log_identity(conn)     # u2 未被清理，仍严格恒等
    assert_retention_accounting(conn)    # u1 被清理过，走记账等式
