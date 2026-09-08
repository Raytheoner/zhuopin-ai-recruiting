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
