"""留存期清理（tasks 8.1–8.2）。

⛔ 全部用 fake 时钟与 `tmp_path`：⛔ 不 sleep、⛔ 不碰真实的 `data/`
（opener 约束 3）。任何一条用例跑完之后仓库里不许多出一个文件。
"""

from __future__ import annotations

import datetime
import os
import pathlib
import sys

import pytest

from tools.liaison import retention
from tools.liaison.retention import MessageRow, RetentionConfigError

CHINA_TZ = datetime.timezone(datetime.timedelta(hours=8))
NOW = datetime.datetime(2026, 9, 9, 12, 0, 0, tzinfo=CHINA_TZ)


def _row(
    msgid,
    *,
    archived_at,
    queue_send_status=None,
    queue_pushed_at=None,
    attachments_json="[]",
    thread_id="u1",
):
    """构造一行台账。

    🔴 裁决一之后队列侧有**两列**：`queue_send_status is None` ＝ 没有队列行；
    `"pending"` / `"deferred"` ＝ 活台账；`"pushed"` 必须同时给 `queue_pushed_at`
    （表级等式 CHECK 在库里就是这么钉的，替身也照这个形状给，⛔ 不许只给状态
    不给时间——那会让用例在一个库里不可能出现的形状上通过）。
    """
    return MessageRow(
        msgid=msgid,
        thread_id=thread_id,
        archived_at=archived_at,
        attachments_json=attachments_json,
        queue_send_status=queue_send_status,
        queue_pushed_at=queue_pushed_at,
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


@pytest.mark.parametrize("status", ["pending", "deferred"])
def test_compute_expired_keeps_non_terminal_queue_rows_in_their_own_bucket(status):
    """🔴 裁决一：**非终态**（🆕 待发 / ⏸ 暂缓）队列行仍是活台账，⛔ 不删，
    但也 ⛔ 不静默——单独成桶。"""
    queued = _row("m-queued", archived_at="2026-01-01 00:00:00", queue_send_status=status)
    split = retention.compute_expired(NOW, 180, [queued])
    assert split.deletable == ()
    assert split.deletable_with_task == ()
    assert [item.msgid for item in split.blocked_by_queue] == ["m-queued"]


def test_compute_expired_deletes_terminal_queue_row_together_with_its_archive():
    """🔴 裁决一 / 方案 ②：终态（✅ 已推送）且**两个时间都过期** ⇒ 连队列行一起清。"""
    row = _row(
        "m-pushed",
        archived_at="2026-01-01 00:00:00",
        queue_send_status="pushed",
        queue_pushed_at="2026-01-02T08:00:00+08:00",
    )
    split = retention.compute_expired(NOW, 180, [row])
    assert split.deletable == ()
    assert split.blocked_by_queue == ()
    assert [item.msgid for item in split.deletable_with_task] == ["m-pushed"]
    assert split.deletable_with_task[0].deletes_task_row is True
    # 两个可删桶合起来才是"本轮要删的全部"。
    assert [item.msgid for item in split.all_deletable] == ["m-pushed"]


def test_compute_expired_needs_both_timestamps_expired_before_touching_the_queue_row():
    """🔴 裁决一逐字：新桶要求**两个时间都过期**，少一个就不删。

    这一条是新桶唯一的收窄判据，⛔ 不要"简化"成只看 `archived_at`：材料旧了
    只说明材料旧了，`pushed_at` 还在回溯窗口内意味着"这份材料几时递出去过"
    仍是活信息，删掉它就是让这件事当场失忆。
    """
    # archived_at 过期、pushed_at 没过期（昨天才推送）⇒ 仍 blocked。
    fresh_push = _row(
        "m-fresh-push",
        archived_at="2026-01-01 00:00:00",
        queue_send_status="pushed",
        queue_pushed_at="2026-09-08T08:00:00+08:00",
    )
    split = retention.compute_expired(NOW, 180, [fresh_push])
    assert split.deletable_with_task == ()
    assert [item.msgid for item in split.blocked_by_queue] == ["m-fresh-push"]

    # 反向：pushed_at 过期、archived_at 没过期 ⇒ 连"超期"都不成立，四桶全空。
    fresh_archive = _row(
        "m-fresh-archive",
        archived_at="2026-09-01 00:00:00",
        queue_send_status="pushed",
        queue_pushed_at="2026-01-02T08:00:00+08:00",
    )
    split = retention.compute_expired(NOW, 180, [fresh_archive])
    assert split.deletable == ()
    assert split.deletable_with_task == ()
    assert split.blocked_by_queue == ()
    assert split.undecidable == ()


def test_compute_expired_boundary_pushed_at_exactly_on_cutoff_is_kept():
    """`pushed_at` 恰好落在 cutoff 上 ⇒ 保留（与 `archived_at` 同一条严格小于口径）。

    ⛔ 不许改成 `<=`：边界上多留一天是安全方向，少留一天是不可逆的删除。
    """
    cutoff = retention.compute_cutoff(NOW, 180)
    row = _row(
        "m-boundary",
        archived_at="2026-01-01 00:00:00",
        queue_send_status="pushed",
        queue_pushed_at=cutoff.isoformat(),
    )
    split = retention.compute_expired(NOW, 180, [row])
    assert split.deletable_with_task == ()
    assert [item.msgid for item in split.blocked_by_queue] == ["m-boundary"]


def test_compute_expired_reports_unparseable_pushed_at_instead_of_deleting():
    """终态却读不出 `pushed_at`（库里被表级等式 CHECK 挡着，但坏数据仍要有出口）
    ⇒ 进 `undecidable`、⛔ 不删。

    🔴 刻意**不**落进 `blocked_by_queue`：那个桶的语义是"一条正常的活台账"，
    一条坏数据混进去就再也不会有任何信号被看见（`blocked_by_queue` 只在有失败
    时才进告警文本，而 `undecidable` ⇒ `skipped` 每轮都会独立触发一条告警）。
    """
    row = _row(
        "m-bad-push",
        archived_at="2026-01-01 00:00:00",
        queue_send_status="pushed",
        queue_pushed_at="前天",
    )
    split = retention.compute_expired(NOW, 180, [row])
    assert split.deletable == ()
    assert split.deletable_with_task == ()
    assert split.blocked_by_queue == ()
    assert [item.subject for item in split.undecidable] == ["m-bad-push"]


def test_compute_expired_treats_an_unknown_queue_status_as_a_live_ledger():
    """将来真多出第四个状态时，默认必须落到**保留**那一侧。

    ⛔ 不许把判据写成"等于 pending 或 deferred 才保留"——那会让一个未知状态
    默认走进删除路径，而删除是不可逆的。
    """
    row = _row("m-future", archived_at="2026-01-01 00:00:00", queue_send_status="escalated")
    split = retention.compute_expired(NOW, 180, [row])
    assert split.deletable_with_task == ()
    assert [item.msgid for item in split.blocked_by_queue] == ["m-future"]


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
from tools.liaison import logsetup, queue
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


def test_message_with_a_non_terminal_queue_row_is_never_deleted(conn):
    """🔴 裁决一：队列行还是 `pending`（活台账）⇒ 超期消息 ⛔ 不删，队列行一行不少。"""
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


def _enqueue(conn, msgid, *, thread_id="u1"):
    effect_enqueue_task(
        conn, thread_id=thread_id, business_key=msgid, sender_userid=thread_id,
        received_at="2026-01-01T08:00:00+08:00", summary="s",
    )
    conn.commit()


def _push(conn, msgid, *, thread_id="u1", pushed_at="2026-01-02T08:00:00+08:00"):
    """把队列行推进终态。⛔ 不直接 UPDATE：走 `queue.mark_task_pushed` 才会同时
    受到三态 CHECK、`pushed_at` 等式 CHECK 与幂等装饰器的约束，用例里的形状
    因此和生产里可能出现的形状一致。"""
    assert queue.mark_task_pushed(conn, thread_id=thread_id, msgid=msgid, pushed_at=pushed_at)
    conn.commit()


def test_the_test_connection_really_enforces_foreign_keys(conn):
    """🔴 下面那批"删除顺序 FK 安全"的用例全部依赖外键**真的在生效**。

    `PRAGMA foreign_keys` 默认是 **OFF**，是 `storage/db.py` 显式打开的。这一条
    钉住它——否则"先删 task 再删 message 能跑通"这件事可能只是因为根本没人检查
    外键，那批用例会变成一组无声通过的空壳。
    """
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_terminal_queue_row_is_deleted_together_with_its_message(conn):
    """🔴 裁决一 / 方案 ②：终态且两个时间都超期 ⇒ 队列行与台账行一起没了。

    **删除顺序的 FK 安全性就由这一条证**：`liaison_task.msgid` 外键指向
    `liaison_message(msgid)`，且上一条用例已钉死本连接真的在执行外键。若顺序
    反了（先删 message），SQLite 会在这里抛 `IntegrityError`（正是
    `test_foreign_key_is_the_second_line_of_defence` 演示的那个异常），本用例
    当场变红。它能绿，只可能是因为顺序是 task → message。
    """
    _archive(conn, "m-pushed", archived_at=OLD)
    _enqueue(conn, "m-pushed")
    _push(conn, "m-pushed")
    split = retention.compute_expired(NOW, 180, retention.load_message_rows(conn))
    assert [item.msgid for item in split.deletable_with_task] == ["m-pushed"]
    deleted, failures = retention.delete_expired_ledger_rows(conn, split.all_deletable)
    assert deleted == ("m-pushed",)
    assert failures == ()
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 0
    # ⛔ effect_log 一行不删：入队与归档那两行都必须还在，另外多出清理那一行。
    nodes = sorted(r[0] for r in conn.execute("SELECT node_name FROM effect_log"))
    assert nodes == [
        "effect_archive_message",
        "effect_delete_expired_message",
        "effect_enqueue_task",
        "effect_mark_task_pushed",
    ]
    assert_retention_accounting(conn)


def test_identity_assertion_now_accounts_for_a_cleaned_queue_row(conn):
    """TD-35 已还：`assert_effect_log_identity` 现在认得「连带清掉的队列行」。

    **本条是 `0909T` 留下的缺口用例改写而来**（原名
    `test_identity_assertion_does_not_yet_account_for_a_cleaned_queue_row`，
    原写法是 `pytest.raises(AssertionError)` ——⛔ 不是在庆祝断言变红，而是把一个
    已登记的缺口钉成可见的）。缺口的由来：终审 finding 2 把「清理会让业务表行数
    变少」的豁免**刻意收窄**到只对 `liaison_message` 成立，理由逐字是
    「`liaison_task` 从不被清理删除（opener 约束 2）」；裁决一（2026-09-09）
    推翻了那个前提。

    TD-35 还债后的判据变了：豁免不再是「把清理过的 thread 整个排除出比对」，
    而是**逐条抵扣**——`effect_log` 行数 == 业务表存活行数 ＋ 能由留存清理解释的
    那部分差额（同 `thread_id` 同 `business_key` 既有本节点的 effect 行、又有
    `RETENTION_DELETE_NODE` 行的条数）。所以这里必须**正向通过**。

    ⛔ 不许把本条删掉了事——删掉就等于把这个缺口重新变成静默的。下半段的证伪是
    本条的另一半：解释不掉的差额仍然必须红。
    """
    _archive(conn, "m-pushed", archived_at=OLD)
    _enqueue(conn, "m-pushed")
    _push(conn, "m-pushed")
    retention.delete_expired_ledger_rows(
        conn, retention.compute_expired(NOW, 180, retention.load_message_rows(conn)).all_deletable
    )
    assert_retention_accounting(conn)          # 队列侧的账目是平的
    assert_effect_log_identity(conn)           # 🔴 TD-35 还债后：豁免范围已放开，正向通过

    # 🔴 证伪（TD-35 的「⛔ 不许放宽成行数对不上一律豁免」）：在**同一个已被清理过的
    # thread** 上再制造一处**解释不掉**的差额——凭空补一条没有任何 effect 行的队列行。
    # 旧的「整个 thread 排除」写法在这里是瞎的；逐条抵扣必须当场抓住它。
    conn.execute(
        "INSERT INTO liaison_message (msgid, thread_id, sender_userid, received_at, msgtype) "
        "VALUES ('ghost', 'u1', 'u1', '2026-09-08T10:00:00+08:00', 'text')"
    )
    conn.execute(
        "INSERT INTO liaison_task (msgid, thread_id, sender_userid, received_at, summary) "
        "VALUES ('ghost', 'u1', 'u1', '2026-09-08T10:00:00+08:00', 's')"
    )
    conn.commit()
    with pytest.raises(AssertionError, match="恒等不变式破裂"):
        assert_effect_log_identity(conn)


def test_no_new_effect_node_name_was_introduced(conn):
    """🔴 裁决一逐字：幂等键沿用 `RETENTION_DELETE_NODE`，⛔ 不新增 effect 节点名。

    那个名字同时是 `assert_effect_log_identity` 判断"哪些 thread 被清理过"的依据
    （冲突 A 方案 2），新增一个名字会让连带删队列行的那些 thread 从排除集合里
    漏出去——两处同时断，且都是静默的。
    """
    _archive(conn, "m-pushed", archived_at=OLD)
    _enqueue(conn, "m-pushed")
    _push(conn, "m-pushed")
    retention.delete_expired_ledger_rows(
        conn, retention.compute_expired(NOW, 180, retention.load_message_rows(conn)).all_deletable
    )
    keys = [r[0] for r in conn.execute(
        "SELECT effect_key FROM effect_log WHERE node_name = ?",
        (retention.RETENTION_DELETE_NODE,),
    )]
    assert keys == ["u1:effect_delete_expired_message:m-pushed"]


def test_queue_row_delete_is_rolled_back_when_the_message_delete_fails(conn):
    """🔴 铁律 1：两条删除在**同一个事务**里——消息行那一条失败时，队列行那一条
    必须被一起回滚，且 ⛔ 不许留下幂等记录（留下就等于"已执行"，永不重试）。

    ⚠️ 构造方式说明：`liaison_task` 的外键只约束 `msgid`，**不约束 `thread_id`**，
    所以可以造出"队列行记在 u1、消息行记在 u2"这种库里合法但业务上不该出现的
    形状。它让 `delete_task_row` 那条 DELETE 命中 1 行、紧随其后的消息行 DELETE
    命中 0 行（`rowcount != 1` ⇒ `RetentionLedgerError`）。这是本模块唯一能在
    不打桩 sqlite 的前提下让"第二条删除失败"真实发生的路径——⛔ 不要改成
    monkeypatch `conn.execute`，那样测到的就不再是真实的事务边界了。
    """
    _archive(conn, "m-x", thread_id="u2", archived_at=OLD)
    conn.execute(
        "INSERT INTO liaison_task (msgid, thread_id, sender_userid, received_at, summary) "
        "VALUES ('m-x', 'u1', 'u1', '2026-01-01T08:00:00+08:00', 's')"
    )
    conn.commit()
    with pytest.raises(retention.RetentionLedgerError):
        retention.effect_delete_expired_message(
            conn, thread_id="u1", business_key="m-x", delete_task_row=True
        )
    # 队列行必须还在（被回滚回来了），消息行也还在，且没有任何幂等记录。
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = ?",
        (retention.RETENTION_DELETE_NODE,),
    ).fetchone()[0] == 0


def test_deleting_a_terminal_row_twice_is_a_no_op(conn):
    """重复执行安全（tasks 8.1）在新桶上同样成立：第二遍既不再删、也不报失败。"""
    _archive(conn, "m-pushed", archived_at=OLD)
    _enqueue(conn, "m-pushed")
    _push(conn, "m-pushed")
    first = retention.delete_expired_ledger_rows(
        conn, retention.compute_expired(NOW, 180, retention.load_message_rows(conn)).all_deletable
    )
    second = retention.delete_expired_ledger_rows(
        conn, retention.compute_expired(NOW, 180, retention.load_message_rows(conn)).all_deletable
    )
    assert first[0] == ("m-pushed",)
    assert second == ((), ())


def test_load_message_rows_brings_back_the_queue_status_and_push_time(conn):
    """🔴 队列侧两列由一条 `LEFT JOIN` 一次取回，⛔ 不在 Python 里逐行回查。

    同时钉住"一条消息最多一行"——`liaison_task.msgid` 的 UNIQUE 掉了的话，
    `LEFT JOIN` 会静默把一行放大成多行，进而把同一条消息删两次。
    """
    _archive(conn, "m-plain", archived_at=OLD)
    _archive(conn, "m-pending", archived_at=OLD)
    _enqueue(conn, "m-pending")
    _archive(conn, "m-pushed", archived_at=OLD)
    _enqueue(conn, "m-pushed")
    _push(conn, "m-pushed")
    rows = {row.msgid: row for row in retention.load_message_rows(conn)}
    assert len(rows) == 3
    assert rows["m-plain"].queue_send_status is None
    assert rows["m-plain"].queue_pushed_at is None
    assert rows["m-plain"].has_queue_row is False
    assert rows["m-pending"].queue_send_status == "pending"
    assert rows["m-pending"].queue_pushed_at is None
    assert rows["m-pending"].has_queue_row is True
    assert rows["m-pushed"].queue_send_status == "pushed"
    assert rows["m-pushed"].queue_pushed_at == "2026-01-02T08:00:00+08:00"


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
    # 🔴 finding 6（终审）：铁律 1 的核心断言——业务写失败 ⇒ 幂等记录必须
    # 不存在——此前只在别处静态成立，这里从没被断言到。装饰器会先
    # rollback 再抛异常，因此这一行是本单元唯一 effect 的"业务写失败留下
    # 幂等记录"这条永久丢失的反面场景的直接证据。
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = ?",
        (retention.RETENTION_DELETE_NODE,),
    ).fetchone()[0] == 0


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
    """被清理过的 thread 上，铁律 1 的恒等式换成这两条**更强**的等式：

        归档 effect 行数 == 台账行数 + 清理 effect 行数
        入队 effect 行数 == 队列行数 + 「被连带清理掉的队列行数」

    差额必须被"清理过多少条"逐条解释干净，⛔ 不许有解释不掉的余数。

    🔴 第二条是裁决一（连带删终态队列行）逼出来的。`effect_log` 一行不删，所以
    "被连带清理掉的队列行数"完全可以从 `effect_log` 自己推出来：既留下过
    `effect_enqueue_task` 又留下过 `RETENTION_DELETE_NODE` 的那些 `business_key`。
    ⛔ 不许改成"差额 >= 0"之类的宽松判据——那等于放弃这条不变式。
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
        enqueued = conn.execute(
            "SELECT COUNT(*) FROM effect_log WHERE node_name = 'effect_enqueue_task' "
            "AND thread_id = ?", (thread_id,)
        ).fetchone()[0]
        alive_tasks = conn.execute(
            "SELECT COUNT(*) FROM liaison_task WHERE thread_id = ?", (thread_id,)
        ).fetchone()[0]
        cleaned_tasks = conn.execute(
            "SELECT COUNT(*) FROM effect_log AS q WHERE q.node_name = 'effect_enqueue_task' "
            "AND q.thread_id = ? AND EXISTS(SELECT 1 FROM effect_log AS d "
            "WHERE d.node_name = ? AND d.thread_id = q.thread_id "
            "AND d.business_key = q.business_key)",
            (thread_id, retention.RETENTION_DELETE_NODE),
        ).fetchone()[0]
        assert enqueued == alive_tasks + cleaned_tasks, (
            f"队列侧记账不平：thread={thread_id} 入队 {enqueued} != 存活 {alive_tasks} "
            f"+ 连带已清 {cleaned_tasks}"
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
    (root / "u2" / "20260101").mkdir(parents=True)  # 20260101 相对 NOW 早已过期
    pruned = retention.prune_empty_dirs(root, NOW, 180)
    assert set(pruned) == {"u2/20260101", "u2"}
    assert root.is_dir()                       # ⛔ 归档根本身永不删
    assert (root / "u1" / "20260101").is_dir()  # 非空目录不动


def test_prune_empty_dirs_never_touches_a_non_expired_day_directory(tmp_path):
    """finding 5(b)（终审）：今天的 `<thread_id>/<yyyymmdd>` 目录哪怕暂时空着
    也不许删——`store_attachment` 是先 `mkdir` 出这层目录、再 `mkstemp`
    写文件，撞上这个窗口会让清理把正在写的材料的父目录端掉，随之而来的
    `mkstemp` 会抛 `FileNotFoundError`。"""
    root = tmp_path / "archive"
    today_dir = root / "u1" / "20260909"  # 相对 NOW=2026-09-09 12:00 是"今天"
    today_dir.mkdir(parents=True)
    pruned = retention.prune_empty_dirs(root, NOW, 180)
    assert pruned == ()
    assert today_dir.is_dir()


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


class RecordingSink:
    """记下送出去的告警。⛔ 不做网络调用——真实群通知是第 6 章。"""

    def __init__(self, explode=False):
        self.texts = []
        self.explode = explode

    def send(self, text):
        self.texts.append(text)
        if self.explode:
            raise RuntimeError("模拟：告警通道自己也挂了")


def test_run_cleanup_deletes_ledger_row_then_file(conn, tmp_path):
    """spec Scenario「超期数据被清理」端到端：台账行没了、文件也没了。"""
    root = tmp_path / "archive"
    _touch(root, "u1/20260101/m-old__a.xlsx")
    _archive(
        conn, "m-old", archived_at=OLD,
        attachments_json='[{"filename": "a.xlsx", "relative_path": "u1/20260101/m-old__a.xlsx",'
                         ' "byte_length": 1, "sha256": "x"}]',
    )
    sink = RecordingSink()
    report = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=root, log_dir=root.parent / "logs", log_retention_days=30, sink=sink
    )
    assert report.deleted_messages == ("m-old",)
    assert report.deleted_files == ("u1/20260101/m-old__a.xlsx",)
    assert report.failures == ()
    assert sink.texts == []          # 没失败就 ⛔ 不发告警
    assert not (root / "u1/20260101/m-old__a.xlsx").exists()
    assert conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 0


def test_run_cleanup_keeps_files_of_surviving_rows(conn, tmp_path):
    """未超期的消息，它的材料一个字节都不许动。"""
    root = tmp_path / "archive"
    _touch(root, "u1/20260901/m-new__a.xlsx")
    _archive(
        conn, "m-new", archived_at=NEW,
        attachments_json='[{"filename": "a.xlsx", "relative_path": "u1/20260901/m-new__a.xlsx",'
                         ' "byte_length": 1, "sha256": "x"}]',
    )
    retention.run_cleanup(conn, now=NOW, retention_days=180, archive_root=root, log_dir=root.parent / "logs", log_retention_days=30, sink=RecordingSink())
    assert (root / "u1/20260901/m-new__a.xlsx").exists()


def test_ledger_row_committed_after_the_scan_still_protects_its_file(conn, tmp_path, monkeypatch):
    """finding 5(a)（终审）的**能咬住**的回归测试 —— TD-34 的还债。

    ⚠️ **旧用例（`test_file_appearing_after_the_scan_is_not_deleted`）咬不住**：
    它的 `fake_iter` 先调 `real_iter` 拿到列表、**之后**才把"迟到"文件写到盘上
    再返回那份旧列表，所以那个文件**根本不在返回值里**。不在候选快照里的文件在
    任何读顺序下都不会被删，于是把它原样丢进修复前的 checkout（`1f2d018`）跑
    **也通过**——它测的是"不在列表里的文件不会被删"，而那是恒真的。

    🔴 本用例改成对读顺序敏感，靠的是**两件事同时成立**：
      ① 那个文件**包含在 `fake_iter` 的返回列表里**（模拟"扫盘时它已在盘上"，
         也就是 design D3 允许的中间态"材料已在、台账未记"）；
      ② 它的台账行在**扫盘之后**才提交（模拟 `archive_message` 的真实顺序：
         先写文件、后写台账行；`_archive` 内部会 commit）。

    于是两种读顺序被真正区分开：
      - 修复后（先扫盘、后读台账）：读 `referenced` 时台账行已提交 ⇒ 文件被判
        "仍被引用" ⇒ 受保护。本用例绿。
      - 修复前（先读台账建 `referenced`、后扫盘）：读 `referenced` 时台账行还
        没提交 ⇒ 判定"无人引用"；而它**在**扫盘快照里，路径日期 `20260101`
        早已过期 ⇒ 被删。本用例红，且红在 design D3 明令禁止的那个中间态上
        （台账已记、材料缺失）。

    ⛔ 不要把 `archived_at` 改成 `OLD`：那样这条新台账行自己就成了本轮的清理
    对象，`surviving` 里没有它，文件照样会被删——用例会因为一个与读顺序无关的
    理由变红，从此再也说不清它在测什么。
    """
    root = tmp_path / "archive"
    real_iter = retention.iter_archive_files
    attachments_json = (
        '[{"filename": "c.bin", "relative_path": "u1/20260101/late__c.bin",'
        ' "byte_length": 1, "sha256": "x"}]'
    )
    # ① 扫盘之前文件就已经在盘上，因此它**会**进候选快照。
    _touch(root, "u1/20260101/late__c.bin")

    def fake_iter(archive_root):
        listing = real_iter(archive_root)
        assert "u1/20260101/late__c.bin" in [item.relative_path for item in listing], (
            "本用例的全部咬合力来自'这个文件在候选快照里'——不在快照里就退化成旧用例"
        )
        # ② 扫盘完成之后，daemon 才把台账行提交（design D3 的真实写入顺序）。
        _archive(conn, "late", archived_at=NEW, attachments_json=attachments_json)
        return listing

    monkeypatch.setattr(retention, "iter_archive_files", fake_iter)
    report = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=root,
        log_dir=tmp_path / "logs", log_retention_days=30, sink=RecordingSink(),
    )
    assert "u1/20260101/late__c.bin" not in report.deleted_files
    assert (root / "u1/20260101/late__c.bin").exists()
    # 台账行也还在：这条断言把"文件被保住"与"台账指向它"绑在一起，
    # 否则一个"顺手把 late 也删掉"的实现也能让上面两条通过。
    assert conn.execute(
        "SELECT COUNT(*) FROM liaison_message WHERE msgid = 'late'"
    ).fetchone()[0] == 1


def test_file_appearing_after_the_scan_is_not_deleted(conn, tmp_path, monkeypatch):
    """扫盘**之后**才落地的文件根本没进这一轮的候选快照，因此天然不是候选。

    ⚠️ 这一条**不区分修复前后的读顺序**（TD-34 的登记内容），保留它的理由只有
    一个：它守的是另一件事——"不在快照里的文件不会被删"这条自愈性质。会咬住
    读顺序的那一条是上面的
    `test_ledger_row_committed_after_the_scan_still_protects_its_file`。
    ⛔ 不要把这条当成 finding 5(a) 的回归测试。
    """
    root = tmp_path / "archive"
    real_iter = retention.iter_archive_files

    def fake_iter(archive_root):
        listing = real_iter(archive_root)
        # 模拟：扫描完成之后，daemon 才把这份材料写到盘上。
        _touch(archive_root, "u1/20260101/late__c.bin")
        return listing

    monkeypatch.setattr(retention, "iter_archive_files", fake_iter)
    report = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=root, log_dir=root.parent / "logs", log_retention_days=30, sink=RecordingSink()
    )
    assert "u1/20260101/late__c.bin" not in report.deleted_files
    assert (root / "u1/20260101/late__c.bin").exists()


def test_file_deletion_failure_raises_exactly_one_alert(conn, tmp_path, monkeypatch):
    """8.2 逐字：「清理失败告警一条」+ spec Scenario「清理失败 → 发出告警、该失败被记录」。

    两条一起断言：告警**恰好一条**（不是每个失败一条），且失败进了 report。
    """
    root = tmp_path / "archive"
    _touch(root, "u1/20260101/a__1.bin")
    _touch(root, "u1/20260101/b__2.bin")
    monkeypatch.setattr(
        retention, "_unlink", lambda path: (_ for _ in ()).throw(PermissionError("模拟"))
    )
    sink = RecordingSink()
    report = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=root, log_dir=root.parent / "logs", log_retention_days=30, sink=sink
    )
    assert len(report.failures) == 2
    assert len(sink.texts) == 1


def test_alert_text_carries_no_personal_information(conn, tmp_path, monkeypatch):
    """🔴 合规：告警将来会经第 6 章送进企微群。

    ⛔ 文本里不许出现 msgid、发送人 userid、文件名、相对路径或消息正文——
    否则等于把"谁发过什么材料"广播出去。只许带计数与阶段名。
    """
    root = tmp_path / "archive"
    _touch(root, "tangliping/20260101/msg-9527__身份证扫描件.pdf")
    monkeypatch.setattr(
        retention, "_unlink", lambda path: (_ for _ in ()).throw(PermissionError("模拟"))
    )
    sink = RecordingSink()
    retention.run_cleanup(conn, now=NOW, retention_days=180, archive_root=root, log_dir=root.parent / "logs", log_retention_days=30, sink=sink)
    text = sink.texts[0]
    for forbidden in ("tangliping", "msg-9527", "身份证扫描件", ".pdf", "20260101"):
        assert forbidden not in text, f"告警文本泄露了 {forbidden!r}：{text}"


def test_alert_channel_failure_never_breaks_the_round(conn, tmp_path, monkeypatch):
    """告警通道自己挂掉 ⇒ 记本地日志，⛔ 不抛给调用方（与第 7 章
    `effect_emit_outage_alert` 同一条口径）。"""
    root = tmp_path / "archive"
    _touch(root, "u1/20260101/a__1.bin")
    monkeypatch.setattr(
        retention, "_unlink", lambda path: (_ for _ in ()).throw(PermissionError("模拟"))
    )
    report = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=root, log_dir=root.parent / "logs", log_retention_days=30, sink=RecordingSink(explode=True)
    )
    assert report.failures  # 轮次照常跑完并返回


def test_blocked_and_skipped_items_are_reported_not_swallowed(conn, tmp_path):
    """⛔ 不静默跳过：被队列行挡住的、以及形态不认识的，都必须出现在 report 里。"""
    root = tmp_path / "archive"
    _touch(root, "stray.txt")
    _archive(conn, "m-queued", archived_at=OLD)
    effect_enqueue_task(
        conn, thread_id="u1", business_key="m-queued", sender_userid="u1",
        received_at="2026-01-01T08:00:00+08:00", summary="s",
    )
    conn.commit()
    report = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=root, log_dir=root.parent / "logs", log_retention_days=30, sink=RecordingSink()
    )
    assert report.blocked_by_queue == ("m-queued",)
    assert [item.subject for item in report.skipped] == ["stray.txt"]
    assert "m-queued" in retention.render_report(report)


def test_skipped_ledger_row_triggers_exactly_one_alert_with_count_only(conn, tmp_path):
    """finding 3（终审）：`archived_at` 解析不了的台账行只进 `SkippedItem`，
    此前从不触发告警——8.3/8.4 会从 launchd 跑，没人看 stdout，告警是唯一
    通道，`SkippedItem` docstring 的「⛔ 不静默跳过」此前只对日志成立。
    """
    root = tmp_path / "archive"
    _archive(conn, "m-bad-time", archived_at="不是时间")
    sink = RecordingSink()
    report = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=root, log_dir=root.parent / "logs", log_retention_days=30, sink=sink
    )
    assert report.failures == ()
    assert [item.subject for item in report.skipped] == ["m-bad-time"]
    assert len(sink.texts) == 1
    text = sink.texts[0]
    assert f"跳过 {len(report.skipped)} 项" in text
    assert "m-bad-time" not in text          # 🔴 合规：告警只带计数，不带 msgid


def test_skipped_archive_file_triggers_exactly_one_alert_with_count_only(conn, tmp_path):
    """归档树形态不认识（层数不对，`<thread_id>/<yyyymmdd>/<叶子>` 不成立）
    同样只进 `SkippedItem`，同样必须触发告警——否则退役后这棵树会悄悄
    变成整片清不掉的死角，且没有任何信号能被看见。"""
    root = tmp_path / "archive"
    _touch(root, "stray.txt")
    sink = RecordingSink()
    report = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=root, log_dir=root.parent / "logs", log_retention_days=30, sink=sink
    )
    assert report.failures == ()
    assert [item.subject for item in report.skipped] == ["stray.txt"]
    assert len(sink.texts) == 1
    text = sink.texts[0]
    assert f"跳过 {len(report.skipped)} 项" in text
    assert "stray.txt" not in text           # 🔴 合规：告警只带计数，不带文件名


def test_dry_run_changes_nothing(conn, tmp_path):
    """`--dry-run` 只报不动：库里一行不少、盘上一个文件不少、⛔ 一条告警都不发。

    finding 1（终审）：告警通道也是"动"的一种——预览阶段不许触发它，否则
    第 6 章接上真实企微群通道之后，`--dry-run` 会真的向群里广播一条消息。
    """
    root = tmp_path / "archive"
    _touch(root, "u1/20260101/m-old__a.xlsx")
    _archive(conn, "m-old", archived_at=OLD)
    sink = RecordingSink()
    report = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=root, log_dir=root.parent / "logs", log_retention_days=30, sink=sink, dry_run=True
    )
    assert report.dry_run is True
    assert report.deleted_messages == ("m-old",)          # 报"会删这些"
    assert conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 1
    assert (root / "u1/20260101/m-old__a.xlsx").exists()
    assert conn.execute("SELECT COUNT(*) FROM effect_log WHERE node_name = ?",
                        (retention.RETENTION_DELETE_NODE,)).fetchone()[0] == 0
    assert sink.texts == []


def test_dry_run_with_unparseable_attachments_json_sends_zero_alerts(conn, tmp_path):
    """finding 1 的证伪：预览阶段命中 `scan` 阶段的失败（一条读不出来的
    `attachments_json`）也**不许**发出告警——`--dry-run` 的契约是「只报不动」，
    这条失败在生产真实运行时会告警，但预览时它只是"如果真跑会发现的问题"，
    ⛔ 不该真的把它送进企微群。修复前：`emit_retention_alert` 挂在
    `dry_run` 判断之外，这条用例会发出恰好一条告警；修复后必须是零条。
    """
    root = tmp_path / "archive"
    _archive(conn, "m-broken", archived_at=NEW, attachments_json="{")
    sink = RecordingSink()
    report = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=root, log_dir=root.parent / "logs", log_retention_days=30, sink=sink, dry_run=True
    )
    assert any(f.stage == "scan" for f in report.failures)  # 预览确实撞上了这条失败
    assert sink.texts == []                                 # 但 ⛔ 一条告警都不许发


def test_dry_run_preview_agrees_with_real_run_for_self_owned_attachment(conn, tmp_path):
    """review round 1 · Important 修复的回归覆盖。

    一条消息自己拥有自己的附件、且已超期 180+ 天——这是最常见的真实场景。
    dry_run 不许因为"没真的删台账行，重新读库时那一行还在"就把它自己的
    附件误判成"仍被引用"从而漏报——那样预览和真实运行的 `deleted_files`
    就对不上，而 plan line 1809 的运维安全步骤（先 `--dry-run` 看一遍再跑
    真的）正是靠这份预览可信才成立。

    两轮各建一套独立的 conn/root（真实运行会删东西，不能共用），断言两边
    `deleted_files` 逐字相同；再单独确认 dry_run 那一边库和盘一个字节没动。
    """
    attachments_json = (
        '[{"filename": "a.xlsx", "relative_path": "u1/20260101/m-old__a.xlsx",'
        ' "byte_length": 1, "sha256": "x"}]'
    )

    root_preview = tmp_path / "preview"
    _touch(root_preview, "u1/20260101/m-old__a.xlsx")
    _archive(conn, "m-old", archived_at=OLD, attachments_json=attachments_json)
    preview = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=root_preview,
        log_dir=root_preview.parent / "logs", log_retention_days=30,
        sink=RecordingSink(), dry_run=True,
    )

    # dry_run 只报不动：库里的行、盘上的文件都必须原封不动。
    assert conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 1
    assert (root_preview / "u1/20260101/m-old__a.xlsx").exists()

    real_conn = liaison_db.get_connection(tmp_path / "real.db")
    liaison_db.init_schema(real_conn)
    root_real = tmp_path / "real"
    _touch(root_real, "u1/20260101/m-old__a.xlsx")
    _archive(real_conn, "m-old", archived_at=OLD, attachments_json=attachments_json)
    real = retention.run_cleanup(
        real_conn, now=NOW, retention_days=180, archive_root=root_real,
        log_dir=root_real.parent / "logs", log_retention_days=30, sink=RecordingSink(),
    )
    real_conn.close()

    assert preview.deleted_messages == real.deleted_messages == ("m-old",)
    assert preview.deleted_files == real.deleted_files == ("u1/20260101/m-old__a.xlsx",)


def test_emit_retention_alert_reports_success_and_failure_directly():
    """`emit_retention_alert` 自身的返回值要被直接断言到——不能只靠间接场景。"""
    ok_sink = RecordingSink()
    assert retention.emit_retention_alert(ok_sink, "text") is True
    assert ok_sink.texts == ["text"]

    broken_sink = RecordingSink(explode=True)
    assert retention.emit_retention_alert(broken_sink, "text") is False


def test_unparseable_ledger_row_stops_the_file_pass(conn, tmp_path):
    """台账里有一行 `attachments_json` 读不出来 ⇒ 我们不知道哪些文件仍被引用
    ⇒ ⛔ 整个文件那一遍不许跑。宁可这一轮不清文件，也不能误删一份还被
    指着的材料。"""
    root = tmp_path / "archive"
    _touch(root, "u1/20260101/old__a.bin")
    _archive(conn, "m-broken", archived_at=NEW, attachments_json="{")
    report = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=root, log_dir=root.parent / "logs", log_retention_days=30, sink=RecordingSink()
    )
    assert report.deleted_files == ()
    assert any(f.stage == "scan" for f in report.failures)
    assert (root / "u1/20260101/old__a.bin").exists()


def test_alert_text_includes_blocked_by_queue_count_when_round_has_failures(conn, tmp_path, monkeypatch):
    """plan「冲突 B」逐字：blocked_by_queue 的计数在本轮有失败时也必须进告警文本
    （⛔ 只带计数，不带其中的 msgid——那些明细留给 `render_report`）。"""
    root = tmp_path / "archive"
    _touch(root, "u1/20260101/a__1.bin")
    _archive(conn, "m-queued", archived_at=OLD)
    effect_enqueue_task(
        conn, thread_id="u1", business_key="m-queued", sender_userid="u1",
        received_at="2026-01-01T08:00:00+08:00", summary="s",
    )
    conn.commit()
    monkeypatch.setattr(
        retention, "_unlink", lambda path: (_ for _ in ()).throw(PermissionError("模拟"))
    )
    sink = RecordingSink()
    report = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=root, log_dir=root.parent / "logs", log_retention_days=30, sink=sink
    )
    assert report.blocked_by_queue == ("m-queued",)
    assert len(sink.texts) == 1
    text = sink.texts[0]
    assert f"保留 {len(report.blocked_by_queue)} 条" in text
    assert "m-queued" not in text


# ---------------------------------------------------------------------------
# TD-30 · 轮转日志的留存期（时间维度）。容量上界由 RotatingFileHandler 早已满足，
# 欠的是"超过留存期的日志 MUST 被清理"这一条。
# ---------------------------------------------------------------------------

LOG_OLD = datetime.datetime(2026, 7, 1, 12, 0, tzinfo=CHINA_TZ)    # 距 NOW > 30 天
LOG_FRESH = datetime.datetime(2026, 9, 8, 12, 0, tzinfo=CHINA_TZ)  # 距 NOW < 30 天


def _log(log_dir, name, *, mtime):
    """造一个日志文件并把 mtime 钉到给定时刻。⛔ 不 sleep、⛔ 不用真实时钟。"""
    log_dir.mkdir(parents=True, exist_ok=True)
    target = log_dir / name
    target.write_bytes(b"x")
    stamp = mtime.timestamp()
    os.utime(target, (stamp, stamp))
    return target


def test_log_retention_days_defaults_to_30_not_180():
    """🔴 D13：日志 30 天、归档 180 天，两者**刻意不对齐**。"""
    assert retention.DEFAULT_LOG_RETENTION_DAYS == 30
    assert retention.load_log_retention_days({}) == 30


def test_log_retention_days_has_its_own_env_var():
    """⛔ 不复用 `HR_LIAISON_RETENTION_DAYS`：只配归档那个，日志仍走自己的默认 30。"""
    assert retention.LOG_RETENTION_DAYS_ENV == "HR_LIAISON_LOG_RETENTION_DAYS"
    env = {"HR_LIAISON_RETENTION_DAYS": "365"}
    assert retention.load_log_retention_days(env) == 30
    assert retention.load_retention_days(env) == 365
    env2 = {"HR_LIAISON_LOG_RETENTION_DAYS": "7"}
    assert retention.load_log_retention_days(env2) == 7
    assert retention.load_retention_days(env2) == 180


@pytest.mark.parametrize("bad", ["0", "-1", "三十", "30天", "30.5"])
def test_invalid_log_retention_days_is_fail_closed(bad):
    """非法值走 `RetentionConfigError` 同一套，⛔ 不许"只是日志"就退回默认值继续跑。"""
    with pytest.raises(RetentionConfigError) as excinfo:
        retention.load_log_retention_days({"HR_LIAISON_LOG_RETENTION_DAYS": bad})
    assert "HR_LIAISON_LOG_RETENTION_DAYS" in str(excinfo.value)


def test_compute_deletable_logs_only_takes_the_ones_older_than_the_cutoff():
    """纯函数：判据只有 mtime 一条，严格小于 cutoff。"""
    logs = (
        retention.RotatedLog("liaison.log.1", LOG_OLD),
        retention.RotatedLog("liaison.log.2", LOG_FRESH),
    )
    assert retention.compute_deletable_logs(NOW, 30, logs) == ("liaison.log.1",)


def test_compute_deletable_logs_keeps_the_file_exactly_on_the_boundary():
    """恰好落在 cutoff 上的保留——与归档同一条口径，⛔ 不许改成 `<=`。"""
    cutoff = retention.compute_cutoff(NOW, 30)
    logs = (retention.RotatedLog("liaison.log.1", cutoff),)
    assert retention.compute_deletable_logs(NOW, 30, logs) == ()


def test_iter_rotated_logs_never_sees_the_active_log_file(tmp_path):
    """🔴 ⛔ 当前活动日志 `liaison.log` 本体不在候选里。

    它正被一个打开的 `RotatingFileHandler` 攥着；unlink 之后 handler 仍往那个
    已消失的 inode 写，日志会静默进黑洞直到下一次轮转——为了"清干净"制造出的
    观测盲区，代价远大于收益。
    """
    log_dir = tmp_path / "logs"
    _log(log_dir, "liaison.log", mtime=LOG_OLD)         # 活动日志，⛔ 不许出现
    _log(log_dir, "liaison.log.1", mtime=LOG_OLD)       # 轮转产物
    _log(log_dir, "other.txt", mtime=LOG_OLD)           # 不是日志，⛔ 不碰
    (log_dir / "sub").mkdir()
    _log(log_dir / "sub", "liaison.log.9", mtime=LOG_OLD)  # ⛔ 不递归子目录
    assert [item.name for item in retention.iter_rotated_logs(log_dir)] == ["liaison.log.1"]


def test_iter_rotated_logs_returns_empty_when_the_directory_is_absent(tmp_path):
    """日志目录还不存在（首次运行、或降级成只有 stderr）⇒ 空元组，⛔ 不报错。"""
    assert retention.iter_rotated_logs(tmp_path / "nope") == ()


def test_iter_rotated_logs_skips_symlinks(tmp_path):
    """⛔ 跟着符号链接删会删到日志目录之外去。"""
    log_dir = tmp_path / "logs"
    outside = _log(tmp_path / "outside", "secret.log.1", mtime=LOG_OLD)
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "liaison.log.1").symlink_to(outside)
    assert retention.iter_rotated_logs(log_dir) == ()


def test_run_cleanup_deletes_expired_rotated_logs_by_mtime(conn, tmp_path):
    """TD-30 端到端：超期的轮转日志没了、未超期的与活动日志一个字节不动。"""
    root = tmp_path / "archive"
    log_dir = tmp_path / "logs"
    _log(log_dir, "liaison.log", mtime=LOG_OLD)
    _log(log_dir, "liaison.log.1", mtime=LOG_OLD)
    _log(log_dir, "liaison.log.2", mtime=LOG_FRESH)
    report = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=root,
        log_dir=log_dir, log_retention_days=30, sink=RecordingSink(),
    )
    assert report.deleted_logs == ("liaison.log.1",)
    assert report.log_retention_days == 30
    assert report.failures == ()
    assert not (log_dir / "liaison.log.1").exists()
    assert (log_dir / "liaison.log.2").exists()
    assert (log_dir / "liaison.log").exists()


def test_log_cleanup_uses_the_log_retention_days_not_the_archive_one(conn, tmp_path):
    """🔴 两个留存期是两个数：归档 180 天的日子里，一份 40 天前的日志仍必须被清掉。

    ⛔ 这一条变红时的正确修法**不是**把两个参数并成一个——D13 明写两者刻意不对齐。
    """
    log_dir = tmp_path / "logs"
    _log(log_dir, "liaison.log.1", mtime=datetime.datetime(2026, 7, 31, 12, 0, tzinfo=CHINA_TZ))
    report = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=tmp_path / "archive",
        log_dir=log_dir, log_retention_days=30, sink=RecordingSink(),
    )
    assert report.deleted_logs == ("liaison.log.1",)


def test_log_cleanup_still_runs_when_the_ledger_scan_failed(conn, tmp_path):
    """日志与台账之间没有任何引用关系 ⇒ `scan` 阶段失败 ⛔ 不该连坐日志那一遍。

    反过来做会让"一条读不出来的 attachments_json"无限期地把含个人信息的历史
    日志留在盘上，而那条坏数据和日志毫无关系。
    """
    root = tmp_path / "archive"
    log_dir = tmp_path / "logs"
    _touch(root, "u1/20260101/old__a.bin")
    _log(log_dir, "liaison.log.1", mtime=LOG_OLD)
    _archive(conn, "m-bad", archived_at=NEW, attachments_json="{")
    report = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=root,
        log_dir=log_dir, log_retention_days=30, sink=RecordingSink(),
    )
    assert any(f.stage == "scan" for f in report.failures)
    assert report.deleted_files == ()                      # 归档那一遍照旧被停住
    assert report.deleted_logs == ("liaison.log.1",)       # 日志那一遍照跑
    assert not (log_dir / "liaison.log.1").exists()


def test_dry_run_never_deletes_a_log_file(conn, tmp_path):
    """`--dry-run` 只报不动，日志也一样。"""
    log_dir = tmp_path / "logs"
    _log(log_dir, "liaison.log.1", mtime=LOG_OLD)
    report = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=tmp_path / "archive",
        log_dir=log_dir, log_retention_days=30, sink=RecordingSink(), dry_run=True,
    )
    assert report.deleted_logs == ("liaison.log.1",)       # "本来会删这些"
    assert (log_dir / "liaison.log.1").exists()            # 但一个字节没动


def test_log_deletion_failure_is_collected_and_does_not_stop_the_round(conn, tmp_path, monkeypatch):
    """单条失败不中止整轮，且进 `CleanupFailure(stage="log")`、⛔ 不静默。"""
    log_dir = tmp_path / "logs"
    _log(log_dir, "liaison.log.1", mtime=LOG_OLD)
    _log(log_dir, "liaison.log.2", mtime=LOG_OLD)
    monkeypatch.setattr(
        retention, "_unlink", lambda path: (_ for _ in ()).throw(PermissionError("模拟"))
    )
    sink = RecordingSink()
    report = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=tmp_path / "archive",
        log_dir=log_dir, log_retention_days=30, sink=sink,
    )
    assert report.deleted_logs == ()
    assert [f.subject for f in report.failures] == ["liaison.log.1", "liaison.log.2"]
    assert {f.stage for f in report.failures} == {"log"}
    assert len(sink.texts) == 1                            # 一轮一条，⛔ 不是每个失败一条
    assert "阶段：log" in sink.texts[0]


def test_setup_logging_and_the_cleanup_look_at_the_same_directory(tmp_path, monkeypatch):
    """🔴 清理清的必须是 `setup_logging` 真正写日志的那个目录。

    两处各读一遍 `HR_LIAISON_LOG_DIR` 就会有"改一处漏一处"的分叉，症状是
    "清理跑得很成功，清的却是一个没人往里写的空目录"——毫无报错。
    """
    monkeypatch.setenv("HR_LIAISON_LOG_DIR", str(tmp_path / "logs"))
    status = logsetup.setup_logging()
    try:
        assert status.log_file is not None
        assert pathlib.Path(status.log_file).parent == logsetup.resolve_log_dir()
    finally:
        logsetup.teardown_logging()


# ---------------------------------------------------------------------------
# 裁决一 · run_cleanup 端到端 + 三处计数口径一致
# ---------------------------------------------------------------------------


def test_run_cleanup_deletes_a_terminal_queue_row_with_its_archive_file(conn, tmp_path):
    """🔴 裁决一端到端：名单内发送人的归档不再无限期驻留。

    这正是冲突 B 的原始症状——`liaison_task.msgid` 的外键让"有队列行的消息"
    永远删不掉，于是汤丽萍/邵培申（名单内、必然入队）的归档全部落进
    `blocked_by_queue`，只有名单外的会被 180 天清掉。与 D13 直接相悖。
    """
    root = tmp_path / "archive"
    _touch(root, "u1/20260101/m-pushed__a.xlsx")
    _archive(
        conn, "m-pushed", archived_at=OLD,
        attachments_json='[{"filename": "a.xlsx", "relative_path": '
                         '"u1/20260101/m-pushed__a.xlsx", "byte_length": 1, "sha256": "x"}]',
    )
    _enqueue(conn, "m-pushed")
    _push(conn, "m-pushed")
    sink = RecordingSink()
    report = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=root,
        log_dir=tmp_path / "logs", log_retention_days=30, sink=sink,
    )
    assert report.deleted_messages == ("m-pushed",)
    assert report.deleted_with_task == ("m-pushed",)
    assert report.blocked_by_queue == ()
    assert report.deleted_files == ("u1/20260101/m-pushed__a.xlsx",)
    assert report.failures == ()
    assert sink.texts == []                                 # 没失败没跳过 ⇒ ⛔ 不发告警
    assert not (root / "u1/20260101/m-pushed__a.xlsx").exists()
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 0


def test_run_cleanup_keeps_a_pending_queue_row_and_its_archive_file(conn, tmp_path):
    """反面：队列行还是 🆕 待发 ⇒ 台账行、队列行、归档文件三样都不动。"""
    root = tmp_path / "archive"
    _touch(root, "u1/20260101/m-pending__a.xlsx")
    _archive(
        conn, "m-pending", archived_at=OLD,
        attachments_json='[{"filename": "a.xlsx", "relative_path": '
                         '"u1/20260101/m-pending__a.xlsx", "byte_length": 1, "sha256": "x"}]',
    )
    _enqueue(conn, "m-pending")
    report = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=root,
        log_dir=tmp_path / "logs", log_retention_days=30, sink=RecordingSink(),
    )
    assert report.deleted_messages == ()
    assert report.deleted_with_task == ()
    assert report.blocked_by_queue == ("m-pending",)
    assert report.deleted_files == ()
    assert (root / "u1/20260101/m-pending__a.xlsx").exists()
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 1


def test_deleted_with_task_is_a_subset_of_deleted_messages(conn, tmp_path):
    """`deleted_with_task` ⊂ `deleted_messages`，⛔ 不是与它并列的第二批。

    两个字段相加会把这些条目算两遍——报告里的"本轮清了几条"从此虚高。
    """
    root = tmp_path / "archive"
    _archive(conn, "m-plain", archived_at=OLD)
    _archive(conn, "m-pushed", archived_at=OLD)
    _enqueue(conn, "m-pushed")
    _push(conn, "m-pushed")
    report = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=root,
        log_dir=tmp_path / "logs", log_retention_days=30, sink=RecordingSink(),
    )
    assert sorted(report.deleted_messages) == ["m-plain", "m-pushed"]
    assert report.deleted_with_task == ("m-pushed",)
    assert set(report.deleted_with_task) <= set(report.deleted_messages)


def test_report_and_alert_agree_on_every_bucket_count(conn, tmp_path, monkeypatch):
    """🔴 三处口径必须一致：`CleanupReport` 的计数、`render_report` 的明细行、
    `compute_retention_alert_text` 的计数。

    ⛔ 只改其中一处会让"报告说清了 N 条、告警说清了 M 条"这种静默偏差活下来，
    而告警是 launchd 下唯一会被人看见的通道。
    """
    root = tmp_path / "archive"
    log_dir = tmp_path / "logs"
    _touch(root, "stray.txt")                       # ⇒ skipped（形态不认识）
    _log(log_dir, "liaison.log.1", mtime=LOG_OLD)   # ⇒ deleted_logs
    _archive(conn, "m-plain", archived_at=OLD)      # ⇒ deletable
    _archive(conn, "m-pushed", archived_at=OLD)     # ⇒ deletable_with_task
    _enqueue(conn, "m-pushed")
    _push(conn, "m-pushed")
    _archive(conn, "m-pending", archived_at=OLD)    # ⇒ blocked_by_queue
    _enqueue(conn, "m-pending")
    sink = RecordingSink()
    report = retention.run_cleanup(
        conn, now=NOW, retention_days=180, archive_root=root,
        log_dir=log_dir, log_retention_days=30, sink=sink,
    )
    assert len(report.deleted_messages) == 2
    assert len(report.deleted_with_task) == 1
    assert len(report.blocked_by_queue) == 1
    assert len(report.deleted_logs) == 1

    rendered = retention.render_report(report)
    assert "其中连队列行一起已删（裁决一：终态且超期的队列行） 1 条：m-pushed" in rendered
    assert "轮转日志 已删 1 个（留存期 30 天）：liaison.log.1" in rendered
    assert "不参与自动清理） 1 条：m-pending" in rendered

    assert len(sink.texts) == 1
    text = sink.texts[0]
    assert "留存期 180 天（日志 30 天）" in text
    assert "其中连队列行一起清 1 条" in text
    assert "轮转日志 1 个" in text
    assert "保留 1 条" in text
    # 🔴 合规：告警只带计数，⛔ 不带 msgid、发送人、文件名。
    for secret in ("m-plain", "m-pushed", "m-pending", "stray.txt", "liaison.log.1"):
        assert secret not in text


def test_cleanup_main_runs_without_any_credentials(tmp_path, monkeypatch, capsys):
    """🔴 清理 ⛔ 不需要企微凭据——它不建连接。

    这条同时守住了 `__main__.py` 里那个分支的位置：它必须在 `load_credentials()`
    之前短路，否则一台还没配 BOT_ID 的机器上永远清理不了。
    """
    monkeypatch.delenv("HR_LIAISON_BOT_ID", raising=False)
    monkeypatch.delenv("HR_LIAISON_BOT_SECRET", raising=False)
    # 🔴 TD-30 起 `cleanup_main` 多了**第三个**真实数据接缝：日志目录。
    # `tools/liaison/tests/conftest.py` 的 autouse fixture 已经把它顶到了
    # tmp_path，这里再显式顶一次是按本文件既有的 finding 7 口径办——每条用例
    # 自己把它碰得到的真实数据接缝全部 patch 掉，⛔ 不依赖别处的护栏还在。
    monkeypatch.setenv("HR_LIAISON_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(liaison_db, "DEFAULT_DB_PATH", tmp_path / "liaison.db")
    monkeypatch.setattr(
        retention.archive, "DEFAULT_ARCHIVE_ROOT", tmp_path / "archive"
    )
    assert retention.cleanup_main([]) == retention.EXIT_OK
    assert "留存清理" in capsys.readouterr().out


def test_cleanup_main_rejects_unknown_arguments(tmp_path, monkeypatch, capsys):
    """🔴 finding 7（终审）：两个真实数据接缝必须一起 patch，即使今天这条用例
    因为未知参数检查是 `cleanup_main` 的第一条语句而"安全"——那是语句顺序
    的偶然，不是任何断言在守。Task 5 review 已经因为同一条理由拒绝过它的
    姊妹用例（`test_cleanup_main_fails_closed_on_bad_config`），同一份文件
    里不一致地适用同一条理由是更差的结果：日后谁把未知参数检查挪到
    `load_retention_days()` 之后，这条测试会在毫无提示的情况下开始碰
    真实 `data/`。
    """
    monkeypatch.setenv("HR_LIAISON_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(liaison_db, "DEFAULT_DB_PATH", tmp_path / "liaison.db")
    monkeypatch.setattr(
        retention.archive, "DEFAULT_ARCHIVE_ROOT", tmp_path / "archive"
    )
    assert retention.cleanup_main(["--force"]) == retention.EXIT_BAD_ARGS
    assert "未知参数" in capsys.readouterr().err


def test_cleanup_main_fails_closed_on_bad_config(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HR_LIAISON_RETENTION_DAYS", "0")
    monkeypatch.setenv("HR_LIAISON_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(liaison_db, "DEFAULT_DB_PATH", tmp_path / "liaison.db")
    # 🔴 controller fix-round-1 override（覆盖 plan line 1641-1646 的原样例）：
    # 两个真实数据接缝（DB 路径 + 归档根）在每条新测试里都必须一起 patch，
    # 不能靠"这条路径在当前实现里读不到"这种依赖语句顺序的偶然安全——
    # 今天 load_retention_days() 先炸、archive.DEFAULT_ARCHIVE_ROOT 根本没被读到，
    # 但这只是巧合，不是任何断言在守；日后若把日志/建连接挪到配置校验之前，
    # 这条测试会在毫无提示的情况下开始删真实 data/liaison/archive。
    monkeypatch.setattr(
        retention.archive, "DEFAULT_ARCHIVE_ROOT", tmp_path / "archive"
    )
    assert retention.cleanup_main([]) == retention.EXIT_BAD_CONFIG
    assert "HR_LIAISON_RETENTION_DAYS" in capsys.readouterr().err


def test_cleanup_main_returns_5_when_the_round_had_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("HR_LIAISON_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(liaison_db, "DEFAULT_DB_PATH", tmp_path / "liaison.db")
    root = tmp_path / "archive"
    _touch(root, "u1/20260101/a__1.bin")
    monkeypatch.setattr(retention.archive, "DEFAULT_ARCHIVE_ROOT", root)
    monkeypatch.setattr(
        retention, "_unlink", lambda path: (_ for _ in ()).throw(PermissionError("模拟"))
    )
    assert retention.cleanup_main([]) == retention.EXIT_RETENTION_FAILED


def test_main_module_cleanup_branch_is_guarded_and_appended():
    """守住 opener 约束 4 的两半：

    ① 子命令分支挂在 `__name__ == "__main__"` 上 ⇒ 被 import 时完全惰性；
    ② 文件最后两行（既有入口）**一字节未变**。
    """
    source = (
        pathlib.Path(__file__).resolve().parents[1] / "__main__.py"
    ).read_text(encoding="utf-8")
    assert 'if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "cleanup":' in source
    assert source.rstrip().endswith(
        'if __name__ == "__main__":\n    raise SystemExit(main())'
    )
    # 分支必须排在既有入口之前，否则 `raise SystemExit(main())` 会先跑掉。
    assert source.index('sys.argv[1] == "cleanup"') < source.rindex('if __name__ == "__main__":')


def test_importing_main_module_does_not_run_cleanup(monkeypatch):
    """被 import（测试、工具）时那段必须一动不动。"""
    monkeypatch.setattr(sys, "argv", ["pytest", "cleanup"])
    import importlib

    import tools.liaison.__main__ as liaison_main

    importlib.reload(liaison_main)  # 不抛 SystemExit 即为通过


def test_exit_codes_are_disjoint_from_main_modules_exit_codes():
    """Minor fix（controller fix-round-1）：退出码互不相撞没有任何断言守着。

    `retention.EXIT_*` 从 5 开始是因为 2/3/4 被 `__main__.py` 的
    `EXIT_MISSING_CREDENTIALS` / `EXIT_SDK_UNAVAILABLE` /
    `EXIT_SDK_SURFACE_UNVERIFIED` 占用；这条守的是"以后改错了会立刻红"，
    而不是重新硬编码一遍 2/3/4——那样两边改了同一个数字也测不出来。
    ⚠️ 只 import 模块（不带 `cleanup` 参数运行 pytest 自身），
    该模块顶层没有任何副作用，见 `test_importing_main_module_does_not_run_cleanup`。
    """
    import tools.liaison.__main__ as liaison_main

    main_codes = {
        liaison_main.EXIT_MISSING_CREDENTIALS,
        liaison_main.EXIT_SDK_UNAVAILABLE,
        liaison_main.EXIT_SDK_SURFACE_UNVERIFIED,
    }
    retention_codes = {
        retention.EXIT_RETENTION_FAILED,
        retention.EXIT_BAD_CONFIG,
        retention.EXIT_BAD_ARGS,
    }
    assert main_codes.isdisjoint(retention_codes)
