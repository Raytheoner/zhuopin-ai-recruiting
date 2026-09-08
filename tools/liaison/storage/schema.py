"""`data/liaison.db` 的全部 DDL。

DDL 以字面量常量的形式集中在本文件，**不散落在建表函数里**：Task 1 的同构断言要把
`EFFECT_LOG_SCHEMA` 单独建进一个内存库去和产品库比对，散在函数里就比不了。
"""

from __future__ import annotations

#: 与 `app/storage/db.py:59-67` **逐字同构**。同列、同类型、同 NOT NULL、同主键、
#: 同唯一索引——由 tests/test_liaison_schema.py 的同构断言机器判定。
#:
#: ⛔ 改这段之前先想清楚：产品库那份是不是也要改？两边分叉不会报错，
#: 只会让"复用同一个 idempotent_effect"这句话在某天悄悄变成假的。
EFFECT_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS effect_log (
    effect_key TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    node_name TEXT NOT NULL,
    business_key TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_effect_log_key ON effect_log (effect_key);
"""

#: 本服务的全量 DDL。Task 2 会在这里追加 liaison_message 与 liaison_task。
SCHEMA = EFFECT_LOG_SCHEMA
