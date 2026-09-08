"""5.3 / 5.9：三态、非法取值被拒、非法转移被拒、已推送必带时间戳。

**真源是存储层**（`storage/schema.py` 的两条 CHECK + defer 触发器）。
本文件同时从两侧验：直接写库（证明表自己会拒）与走 `queue.py`（证明翻译层没吞掉）。
"""

import sqlite3

import pytest

from tools.liaison.queue import (
    TaskNotFound,
    TaskTransitionRejected,
    defer_task,
    enqueue_task,
    list_tasks,
    mark_task_pushed,
)
from tools.liaison.storage import db as liaison_db
from tools.liaison.storage.effects import effect_archive_message
from tools.liaison.storage.schema import SEND_STATUSES
from tools.liaison.tests.test_liaison_effects import assert_effect_log_identity

RECEIVED_AT = "2026-09-09T10:00:00+08:00"
PUSHED_AT = "2026-09-09T11:30:00+08:00"


@pytest.fixture
def conn(tmp_path):
    c = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(c)
    yield c
    c.close()


@pytest.fixture
def task(conn):
    effect_archive_message(
        conn,
        thread_id="u_zhang",
        business_key="msg-1",
        sender_userid="u_zhang",
        received_at=RECEIVED_AT,
        msgtype="text",
        content="报价单已发",
    )
    enqueue_task(
        conn,
        thread_id="u_zhang",
        msgid="msg-1",
        sender_userid="u_zhang",
        received_at=RECEIVED_AT,
        summary="报价单已发",
    )
    return "msg-1"


def _status(conn, msgid):
    return conn.execute(
        "SELECT send_status, pushed_at FROM liaison_task WHERE msgid = ?", (msgid,)
    ).fetchone()


def test_the_three_states_are_exactly_these(conn):
    assert SEND_STATUSES == ("pending", "deferred", "pushed")


def test_new_task_starts_pending(conn, task):
    assert _status(conn, task) == ("pending", None)


def test_defer_from_pending_is_allowed(conn, task):
    assert defer_task(conn, thread_id="u_zhang", msgid=task) is None
    assert _status(conn, task) == ("deferred", None)
    assert_effect_log_identity(conn)


def test_mark_pushed_carries_the_timestamp(conn, task):
    """spec：「已推送」SHALL 携带推送时间戳。"""
    assert mark_task_pushed(
        conn, thread_id="u_zhang", msgid=task, pushed_at=PUSHED_AT
    ) is True
    assert _status(conn, task) == ("pushed", PUSHED_AT)
    assert_effect_log_identity(conn)


def test_defer_from_pushed_is_rejected(conn, task):
    """spec 逐字场景：把一条「已推送」条目改为「暂缓」⇒ 该转移被拒绝。"""
    mark_task_pushed(conn, thread_id="u_zhang", msgid=task, pushed_at=PUSHED_AT)

    with pytest.raises(TaskTransitionRejected):
        defer_task(conn, thread_id="u_zhang", msgid=task)

    assert _status(conn, task) == ("pushed", PUSHED_AT), "被拒的转移 ⛔ 不许留下任何痕迹"
    assert_effect_log_identity(conn)


def test_defer_from_deferred_is_rejected(conn, task):
    """触发器的条件是 `OLD.send_status <> 'pending'`——deferred → deferred 同样被拒。

    ⚠️ 这也意味着 `defer_task` 对同一条已暂缓的条目**不是**幂等的：第二次会抛。
    这是存储层刻意的形状（「暂缓」只能来自「待发」），⛔ 不许为了"看起来更幂等"
    去改触发器或在应用层吞掉这个异常。
    """
    defer_task(conn, thread_id="u_zhang", msgid=task)
    with pytest.raises(TaskTransitionRejected):
        defer_task(conn, thread_id="u_zhang", msgid=task)
    assert _status(conn, task) == ("deferred", None)


def test_the_defer_rejection_actually_comes_from_the_trigger_not_a_check(conn, task):
    """5.9：证明「暂缓只能来自待发」这条拒绝真的来自
    `trg_liaison_task_defer_only_from_pending`，而不是巧合地撞上了别的 CHECK。

    直接绕过 `queue.py` 的翻译层，对一条「已推送」条目发 UPDATE：
    `RAISE(ABORT, 'send_status: deferred may only be entered from pending')`
    的原始文本必须原样出现在 `sqlite3.IntegrityError` 里，且**不含**
    `CHECK constraint failed`——那是三态枚举 CHECK 与等式 CHECK 的报错形状，
    如果这条测试意外撞上了它们中的一个而不是触发器，说明测的根本不是这条转移规则。
    """
    mark_task_pushed(conn, thread_id="u_zhang", msgid=task, pushed_at=PUSHED_AT)

    with pytest.raises(sqlite3.IntegrityError) as excinfo:
        conn.execute(
            "UPDATE liaison_task SET send_status = 'deferred' WHERE msgid = ?", (task,)
        )

    detail = str(excinfo.value)
    assert "send_status: deferred may only be entered from pending" in detail, detail
    assert "CHECK constraint failed" not in detail, detail
    assert _status(conn, task) == ("pushed", PUSHED_AT)


def test_mark_pushed_twice_is_an_idempotent_no_op(conn, task):
    """同一 msgid 第二次标记推送 ⇒ 幂等命中返回 False，时间戳 ⛔ 不许被覆盖。"""
    assert mark_task_pushed(conn, thread_id="u_zhang", msgid=task, pushed_at=PUSHED_AT) is True
    assert mark_task_pushed(
        conn, thread_id="u_zhang", msgid=task, pushed_at="2026-09-09T23:59:59+08:00"
    ) is False
    assert _status(conn, task) == ("pushed", PUSHED_AT)
    assert_effect_log_identity(conn)


def test_mark_pushed_without_a_timestamp_is_rejected(conn, task):
    """`CHECK ((send_status='pushed') = (pushed_at IS NOT NULL))` 的一半。"""
    with pytest.raises(TaskTransitionRejected):
        mark_task_pushed(conn, thread_id="u_zhang", msgid=task, pushed_at=None)
    assert _status(conn, task) == ("pending", None)


def test_storage_rejects_a_status_outside_the_three(conn, task):
    """从存储层直接验：⛔ 绕过应用层也拒。这条证明真源确实在表上。"""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE liaison_task SET send_status = 'archived' WHERE msgid = ?", (task,))
    assert _status(conn, task) == ("pending", None)


def test_storage_rejects_a_pending_row_carrying_a_pushed_at(conn, task):
    """等式 CHECK 的另一半：未推送 ⛔ 不许带时间戳。"""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE liaison_task SET pushed_at = ? WHERE msgid = ?", (PUSHED_AT, task)
        )


def test_status_change_on_a_missing_task_raises_and_leaves_no_effect_log(conn):
    """🔴 `UPDATE ... WHERE msgid = ?` 匹配 0 行时 SQLite **不报错**。

    不检查 rowcount，这次调用会安静"成功"并留下 effect_log 记录，
    从此这个 msgid 的推送标记永远不会重试——铁律 1 的失效方向本尊。
    """
    with pytest.raises(TaskNotFound):
        mark_task_pushed(
            conn, thread_id="u_zhang", msgid="msg-does-not-exist", pushed_at=PUSHED_AT
        )
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = 'effect_mark_task_pushed'"
    ).fetchone()[0] == 0

    with pytest.raises(TaskNotFound):
        defer_task(conn, thread_id="u_zhang", msgid="msg-does-not-exist")
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = 'effect_defer_task'"
    ).fetchone()[0] == 0


def test_update_effects_are_not_registered_in_effect_node_to_table(conn):
    """⛔ UPDATE 型 effect 永远不许登记进 EFFECT_NODE_TO_TABLE。

    它们不产生新行，登记进去会让「effect_log 条数 == 业务表行数」这条恒等式
    因为一个完全正当的理由变红——而那时最顺手的"修法"是削弱恒等断言本身，
    也就是把铁律 1 唯一的机器守卫拆掉。
    """
    from tools.liaison.storage.effects import EFFECT_NODE_TO_TABLE

    assert "effect_defer_task" not in EFFECT_NODE_TO_TABLE
    assert "effect_mark_task_pushed" not in EFFECT_NODE_TO_TABLE


def test_list_tasks_orders_by_received_at_then_id(conn):
    for index, msgid in enumerate(("msg-b", "msg-a"), start=1):
        effect_archive_message(
            conn,
            thread_id="u_zhang",
            business_key=msgid,
            sender_userid="u_zhang",
            received_at=f"2026-09-09T1{index}:00:00+08:00",
            msgtype="text",
            content=msgid,
        )
        enqueue_task(
            conn,
            thread_id="u_zhang",
            msgid=msgid,
            sender_userid="u_zhang",
            received_at=f"2026-09-09T1{index}:00:00+08:00",
            summary=msgid,
        )
    assert [row["msgid"] for row in list_tasks(conn)] == ["msg-b", "msg-a"]
