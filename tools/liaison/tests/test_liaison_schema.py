"""`data/liaison.db` 的 schema 断言。

核心是一条**机器可判定的同构性**：本服务的 `effect_log` 必须与产品库
`app/storage/db.py:59-67` 逐列、逐索引一致。⛔ 不要把这条改成"人工看一眼"——
产品库将来加一列而这边没跟上，不会有任何症状，直到某天两边的幂等记录对不上。

⛔ 断言失败时不要改断言去迁就实现。失败意味着两份 schema 真的分叉了。
"""

import sqlite3

import pytest

from app.storage.db import SCHEMA as APP_SCHEMA
from tools.liaison.storage import db as liaison_db
from tools.liaison.storage.schema import EFFECT_LOG_SCHEMA, SCHEMA


def _table_shape(conn: sqlite3.Connection, table: str) -> tuple:
    """把一张表的结构压成可比较的值：列定义 + 索引 + 每个索引的列。

    `PRAGMA table_info` 每行是 (cid, name, type, notnull, dflt_value, pk)。
    **刻意丢掉 cid**——它只是序号，两份 schema 里 effect_log 是各自库里的第几张表
    与同构性无关；保留 name/type/notnull/default/pk 五项。
    """
    columns = [tuple(row)[1:] for row in conn.execute(f"PRAGMA table_info({table})")]
    indexes = sorted((row[1], row[2]) for row in conn.execute(f"PRAGMA index_list({table})"))
    index_columns = {
        row[1]: [entry[2] for entry in conn.execute(f"PRAGMA index_info('{row[1]}')")]
        for row in conn.execute(f"PRAGMA index_list({table})")
    }
    return columns, indexes, index_columns


def test_effect_log_is_isomorphic_to_product_schema():
    """同列、同类型、同 NOT NULL、同主键、同唯一索引。"""
    product = sqlite3.connect(":memory:")
    product.executescript(APP_SCHEMA)
    liaison = sqlite3.connect(":memory:")
    liaison.executescript(EFFECT_LOG_SCHEMA)

    assert _table_shape(liaison, "effect_log") == _table_shape(product, "effect_log")


def test_effect_log_key_is_unique():
    """唯一索引是幂等的唯一权威（见 app/storage/idempotency.py 里那段注释）。"""
    conn = sqlite3.connect(":memory:")
    conn.executescript(EFFECT_LOG_SCHEMA)
    conn.execute(
        "INSERT INTO effect_log VALUES ('u1:effect_archive_message:m1', 'u1', "
        "'effect_archive_message', 'm1', datetime('now'))"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO effect_log VALUES ('u1:effect_archive_message:m1', 'u1', "
            "'effect_archive_message', 'm1', datetime('now'))"
        )


def test_liaison_db_does_not_contain_product_tables(tmp_path):
    """D5：独立库。产品表出现在这里，说明有人把两个库混了。

    `job`（M1 画像任务）与 `analysis_run`（AI 审计）是产品库的标志性表，
    任一出现即失败。
    """
    conn = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(conn)
    names = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert "job" not in names
    assert "analysis_run" not in names


def test_default_db_path_is_liaison_not_demo():
    """⛔ 不与 data/demo.db 混库（design D5）。"""
    assert liaison_db.DEFAULT_DB_PATH.name == "liaison.db"
    assert liaison_db.DEFAULT_DB_PATH.parent.name == "data"


def test_init_schema_is_idempotent(tmp_path):
    """建表走 CREATE TABLE IF NOT EXISTS，重复初始化必须无害。"""
    path = tmp_path / "liaison.db"
    conn = liaison_db.get_connection(path)
    liaison_db.init_schema(conn)
    liaison_db.init_schema(conn)  # ⛔ 不许抛
    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 0


def test_connection_keeps_legacy_transaction_control(tmp_path):
    """事务归属的地基：连接必须留在隐式事务模式。

    `autocommit = sqlite3.LEGACY_TRANSACTION_CONTROL`（值 -1）且
    `isolation_level == ""` 时，一次 `conn.execute("INSERT ...")` 会开启一个隐式
    事务，直到有人 `commit()`。`idempotent_effect` 正是靠这一点让业务写与
    `effect_log` 写落在**同一个 BEGIN** 里。

    ⛔ 谁把 isolation_level 改成 None（或 autocommit=True），每条语句都会自动提交，
    业务写与幂等记录就变成两个事务——铁律 1 当场破掉，而且**不报错、无症状**，
    只有在崩溃恢复时才以"消息永远收不到"的形式暴露。这条断言就是那道闸。
    """
    conn = liaison_db.get_connection(tmp_path / "liaison.db")
    assert conn.autocommit == sqlite3.LEGACY_TRANSACTION_CONTROL
    assert conn.isolation_level == ""


def test_schema_contains_effect_log_schema():
    """SCHEMA 是全量 DDL，必须包含 effect_log 那一段（Task 2 会往 SCHEMA 追加两张表）。"""
    assert EFFECT_LOG_SCHEMA.strip() in SCHEMA


# ─────────────────────────────────────────────────────────────────────────
# Task 2：liaison_message（消息台账）与 liaison_task（队列真身）
# ─────────────────────────────────────────────────────────────────────────

from tools.liaison.storage.schema import (  # noqa: E402
    SEND_STATUS_DEFERRED,
    SEND_STATUS_PENDING,
    SEND_STATUS_PUSHED,
    SEND_STATUSES,
)


@pytest.fixture
def conn(tmp_path):
    c = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(c)
    yield c
    c.close()


def _insert_message(conn, msgid="m1", thread_id="u1", content="hello"):
    conn.execute(
        "INSERT INTO liaison_message (msgid, thread_id, sender_userid, received_at, msgtype, content) "
        "VALUES (?, ?, ?, ?, 'text', ?)",
        (msgid, thread_id, thread_id, "2026-09-08T10:00:00+08:00", content),
    )
    return msgid


def _insert_task(conn, msgid="m1", thread_id="u1"):
    conn.execute(
        "INSERT INTO liaison_task (msgid, thread_id, sender_userid, received_at) "
        "VALUES (?, ?, ?, ?)",
        (msgid, thread_id, thread_id, "2026-09-08T10:00:00+08:00"),
    )


def test_message_key_is_msgid_not_date(conn):
    """归档键细到单条消息：同人同天三条消息 = 三行，互不覆盖。

    对应 liaison-message-archive「归档键细到单条消息 / 同人同天多条消息」。
    ⛔ 任何"按天一个键"的设计都会让第三条把第一条盖掉——参考服务的生产 bug。
    """
    for msgid in ("m1", "m2", "m3"):
        _insert_message(conn, msgid=msgid, thread_id="u1", content=f"body-{msgid}")
    conn.commit()
    rows = conn.execute(
        "SELECT msgid, content FROM liaison_message WHERE thread_id = 'u1' ORDER BY msgid"
    ).fetchall()
    assert rows == [("m1", "body-m1"), ("m2", "body-m2"), ("m3", "body-m3")]


def test_message_content_survives_pipes_newlines_and_control_chars(conn):
    """任意消息内容都只是列里的一个字符串值，不可能破坏结构。

    对应 liaison-task-queue「队列真身为结构化存储」的「内容含竖线」「内容含换行」。
    竖线归一化在这个形态下**根本不需要**——这正是 design D11 放弃 Markdown 当真身
    换来的收益。
    """
    nasty = "a|b|c\n第二行\t制表\x07响铃|" + "长" * 5000
    _insert_message(conn, msgid="m9", content=nasty)
    conn.commit()
    got = conn.execute("SELECT content FROM liaison_message WHERE msgid = 'm9'").fetchone()[0]
    assert got == nasty
    assert conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 1


def test_task_requires_existing_source_message(conn):
    """条目缺少来源信息不允许写入（liaison-task-queue「每条队列条目可回指来源消息」）。"""
    with pytest.raises(sqlite3.IntegrityError):
        _insert_task(conn, msgid="does-not-exist")


@pytest.mark.parametrize("null_column", ["thread_id", "sender_userid", "received_at"])
def test_task_source_columns_are_not_null(conn, null_column):
    """来源四要素（发送人、来源会话、来源消息、接收时间）一个都不许为空。

    第四要素 msgid 由 NOT NULL + FK 覆盖，见 test_task_requires_existing_source_message。
    """
    _insert_message(conn)
    conn.commit()
    values = {"thread_id": "u1", "sender_userid": "u1", "received_at": "t"}
    values[null_column] = None
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO liaison_task (msgid, thread_id, sender_userid, received_at) "
            "VALUES ('m1', ?, ?, ?)",
            (values["thread_id"], values["sender_userid"], values["received_at"]),
        )
    conn.rollback()


def test_task_can_be_traced_back_to_its_message(conn):
    """从一条队列条目能定位到来源消息。"""
    _insert_message(conn, msgid="m1", content="原文")
    _insert_task(conn, msgid="m1")
    conn.commit()
    row = conn.execute(
        "SELECT m.content, m.sender_userid FROM liaison_task t "
        "JOIN liaison_message m ON m.msgid = t.msgid WHERE t.msgid = 'm1'"
    ).fetchone()
    assert row == ("原文", "u1")


def test_send_status_enum_values_are_ascii_not_emoji():
    """存枚举值，⛔ 不存 emoji（opener 约束 3 / design D11）。

    emoji 只允许出现在第 5 章的渲染层。存展示串意味着改一次文案就要迁一次数据，
    且任何一个拼写偏差都变成一个悄悄多出来的新状态。
    """
    assert SEND_STATUSES == (SEND_STATUS_PENDING, SEND_STATUS_DEFERRED, SEND_STATUS_PUSHED)
    assert SEND_STATUSES == ("pending", "deferred", "pushed")
    for value in SEND_STATUSES:
        assert value.isascii(), f"send_status 枚举值必须是 ASCII，实际 {value!r}"


def test_send_status_defaults_to_pending(conn):
    _insert_message(conn)
    _insert_task(conn)
    conn.commit()
    assert (
        conn.execute("SELECT send_status FROM liaison_task WHERE msgid = 'm1'").fetchone()[0]
        == SEND_STATUS_PENDING
    )


@pytest.mark.parametrize("bad", ["🆕 待发", "⏸ 暂缓", "✅ 已推送", "done", "PENDING", ""])
def test_storage_rejects_status_outside_the_three(conn, bad):
    """非法状态被拒绝（liaison-task-queue「非法状态被拒绝」）。

    注意 'PENDING' 也必须被拒——大小写变体是个真实的失手方式。
    """
    _insert_message(conn)
    _insert_task(conn)
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE liaison_task SET send_status = ? WHERE msgid = 'm1'", (bad,))
    conn.rollback()


def test_pushed_requires_timestamp(conn):
    """已推送带时间戳（liaison-task-queue「已推送带时间戳」）。"""
    _insert_message(conn)
    _insert_task(conn)
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE liaison_task SET send_status = ? WHERE msgid = 'm1'", (SEND_STATUS_PUSHED,)
        )
    conn.rollback()
    conn.execute(
        "UPDATE liaison_task SET send_status = ?, pushed_at = datetime('now') WHERE msgid = 'm1'",
        (SEND_STATUS_PUSHED,),
    )
    conn.commit()
    assert conn.execute("SELECT pushed_at FROM liaison_task WHERE msgid = 'm1'").fetchone()[0]


def test_non_pushed_must_not_carry_timestamp(conn):
    """反向也要钉住：待发/暂缓带着推送时间戳是自相矛盾的状态。"""
    _insert_message(conn)
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO liaison_task (msgid, thread_id, sender_userid, received_at, "
            "send_status, pushed_at) VALUES ('m1', 'u1', 'u1', 't', ?, datetime('now'))",
            (SEND_STATUS_PENDING,),
        )
    conn.rollback()


def test_deferred_only_from_pending(conn):
    """暂缓只能来自待发（liaison-task-queue「暂缓只能来自待发」）。

    钉在存储层（TRIGGER）而不是只在业务层判：业务层的判断可以被绕过，
    TRIGGER 不能。第 5 章的入队/改状态逻辑仍应自己先判一次，这里是兜底。
    """
    _insert_message(conn)
    _insert_task(conn)
    conn.commit()
    # pending → deferred 允许
    conn.execute(
        "UPDATE liaison_task SET send_status = ? WHERE msgid = 'm1'", (SEND_STATUS_DEFERRED,)
    )
    conn.commit()
    # deferred → pushed 允许
    conn.execute(
        "UPDATE liaison_task SET send_status = ?, pushed_at = datetime('now') WHERE msgid = 'm1'",
        (SEND_STATUS_PUSHED,),
    )
    conn.commit()
    # pushed → deferred 被拒
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE liaison_task SET send_status = ?, pushed_at = NULL WHERE msgid = 'm1'",
            (SEND_STATUS_DEFERRED,),
        )
    conn.rollback()
    assert (
        conn.execute("SELECT send_status FROM liaison_task WHERE msgid = 'm1'").fetchone()[0]
        == SEND_STATUS_PUSHED
    )


def test_one_task_per_message(conn):
    """重复投递不产生第二条队列条目——UNIQUE(msgid) 是最后一道结构性保险。

    幂等装饰器是第一道（Task 3）。两道都要有：装饰器防的是"同一路径重复执行"，
    UNIQUE 防的是"别的路径也来写一条"。
    """
    _insert_message(conn)
    _insert_task(conn)
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        _insert_task(conn)
    conn.rollback()
