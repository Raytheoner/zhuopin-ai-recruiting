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


def _touch(root, relative_path, payload=b"x"):
    target = root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


def test_expired_unreferenced_file_is_deleted(tmp_path):
    """spec Scenario「超期数据被清理」在文件那一层的形态。"""
    root = tmp_path / "archive"
    old = _touch(root, "u1/20260101/m-old__a.xlsx")
    fresh = _touch(root, "u1/20260901/m-new__b.xlsx")
    files = retention.iter_archive_files(root)
    deletable, skipped = retention.compute_deletable_files(NOW, 180, files, frozenset())
    assert deletable == ("u1/20260101/m-old__a.xlsx",)
    assert skipped == ()
    deleted, failures = retention.delete_archive_files(root, deletable)
    assert deleted == ("u1/20260101/m-old__a.xlsx",)
    assert failures == ()
    assert not old.exists()
    assert fresh.exists()


def test_referenced_file_is_never_deleted_even_if_expired(tmp_path):
    """🔴 台账还指着的文件一律不删——删了就是 design D3 禁止的
    「台账已记、材料缺失」。这是文件那一遍唯一的结构性保险。"""
    root = tmp_path / "archive"
    _touch(root, "u1/20260101/m-old__a.xlsx")
    referenced = frozenset({"u1/20260101/m-old__a.xlsx"})
    deletable, skipped = retention.compute_deletable_files(
        NOW, 180, retention.iter_archive_files(root), referenced
    )
    assert deletable == ()


def test_orphan_temp_file_is_swept(tmp_path):
    """`attachments.py` 崩溃时留下的 `.tmp-*.part` 从不进台账 ⇒ 永远"未被引用"
    ⇒ 超期后被这一遍顺带扫掉。attachments.py 的注释把这件事指给了第 8 章。"""
    root = tmp_path / "archive"
    _touch(root, "u1/20260101/.tmp-abc.part")
    deletable, _ = retention.compute_deletable_files(
        NOW, 180, retention.iter_archive_files(root), frozenset()
    )
    assert deletable == ("u1/20260101/.tmp-abc.part",)


def test_unexpected_path_shape_is_reported_not_deleted(tmp_path):
    """⛔ 形态不认识的路径一律不删——不认识就说明我们的假设错了，
    这时候正确的动作是把它报出来，不是把它删掉。"""
    root = tmp_path / "archive"
    _touch(root, "stray.txt")
    _touch(root, "u1/notadate/x.bin")
    deletable, skipped = retention.compute_deletable_files(
        NOW, 180, retention.iter_archive_files(root), frozenset()
    )
    assert deletable == ()
    assert {item.subject for item in skipped} == {"stray.txt", "u1/notadate/x.bin"}


def test_file_pass_is_idempotent(tmp_path):
    """「重复执行安全」：第二遍什么也扫不到，且不报失败。"""
    root = tmp_path / "archive"
    _touch(root, "u1/20260101/m-old__a.xlsx")
    first = retention.compute_deletable_files(
        NOW, 180, retention.iter_archive_files(root), frozenset()
    )[0]
    retention.delete_archive_files(root, first)
    second = retention.compute_deletable_files(
        NOW, 180, retention.iter_archive_files(root), frozenset()
    )[0]
    assert first and second == ()


def test_delete_failure_is_collected_and_does_not_stop_the_round(tmp_path, monkeypatch):
    """单条失败不中止整轮（opener 约束 1 逐字）。"""
    root = tmp_path / "archive"
    _touch(root, "u1/20260101/a__1.bin")
    _touch(root, "u1/20260101/b__2.bin")
    targets = ("u1/20260101/a__1.bin", "u1/20260101/b__2.bin")

    def boom(path):
        if path.name.startswith("a__"):
            raise PermissionError("模拟：没有删除权限")
        path.unlink()

    monkeypatch.setattr(retention, "_unlink", boom)
    deleted, failures = retention.delete_archive_files(root, targets)
    assert deleted == ("u1/20260101/b__2.bin",)
    assert [f.stage for f in failures] == ["file"]
    assert failures[0].subject == "u1/20260101/a__1.bin"


def test_prune_empty_dirs_removes_only_empty_ones_and_keeps_the_root(tmp_path):
    root = tmp_path / "archive"
    _touch(root, "u1/20260101/a__1.bin")
    (root / "u2" / "20260101").mkdir(parents=True)
    pruned = retention.prune_empty_dirs(root)
    assert set(pruned) == {"u2/20260101", "u2"}
    assert root.is_dir()                       # ⛔ 归档根本身永不删
    assert (root / "u1" / "20260101").is_dir()  # 非空目录不动


def test_expiry_is_computed_in_china_tz_not_utc(tmp_path):
    """🔴 冲突 C 的靶心之一：`_day_expiry_instant` 把 `tzinfo=CHINA_TZ` 换成
    `tzinfo=datetime.timezone.utc` 必须让这条测试变红。两种解读相差 8 小时，
    本机在 EDT，这类回退不报错、不崩溃，只是悄悄把过期时刻挪 8 小时——
    cutoff 卡在两种解读中间（留 4 小时以上余量防抖动）：
    CHINA_TZ 解读下 2026-01-01 23:59:59.999999+08:00 == 2026-01-01 15:59:59.999999Z，
    早于 cutoff（2026-01-01 20:00:00Z）⇒ 超期；UTC 解读下同一时刻记成
    2026-01-01 23:59:59.999999Z，晚于 cutoff ⇒ 未超期。两者结论相反。
    """
    root = tmp_path / "archive"
    _touch(root, "u1/20260101/m-x__a.bin")
    now = datetime.datetime(2026, 1, 2, 20, 0, 0, tzinfo=datetime.timezone.utc)
    deletable, skipped = retention.compute_deletable_files(
        now, 1, retention.iter_archive_files(root), frozenset()
    )
    assert deletable == ("u1/20260101/m-x__a.bin",)
    assert skipped == ()


def test_expiry_uses_end_of_day_not_midnight(tmp_path):
    """🔴 冲突 C 的靶头之二：`_day_expiry_instant` 把
    `hour=23, minute=59, second=59, microsecond=999999` 换成
    `hour=0, minute=0, second=0, microsecond=0` 必须让这条测试变红。
    取当天最晚一刻是刻意的保守方向；取零点会让同一天的文件提前近 24 小时
    过期。cutoff 卡在当天正午（留 12 小时余量防抖动）：
    「最晚一刻」语义下 2026-01-01 23:59:59.999999+08:00 晚于 cutoff
    （2026-01-01 12:00:00+08:00）⇒ 未超期；「零点」语义下同一天记成
    2026-01-01 00:00:00+08:00，早于 cutoff ⇒ 超期。两者结论相反。
    """
    root = tmp_path / "archive"
    _touch(root, "u1/20260101/m-y__b.bin")
    now = datetime.datetime(2026, 1, 2, 12, 0, 0, tzinfo=CHINA_TZ)
    deletable, skipped = retention.compute_deletable_files(
        now, 1, retention.iter_archive_files(root), frozenset()
    )
    assert deletable == ()
    assert skipped == ()
