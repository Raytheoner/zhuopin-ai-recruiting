"""连接工厂与 schema 初始化。

**本模块是本服务唯一的事务管理者入口**：除 `init_schema` 之外，`tools/liaison/`
下的非测试代码一律 ⛔ 不许调用 `conn.commit()` / `conn.rollback()`——提交由
`app.storage.idempotency.idempotent_effect` 独占负责，这样"业务写与幂等记录同一个
BEGIN"才是结构上成立的，而不是靠每个调用点自觉。
这条约束由 tests/test_liaison_effects.py 的 AST 断言守着（白名单只有 init_schema）。
"""

from __future__ import annotations

import os
import pathlib
import sqlite3

from tools.liaison.storage.schema import SCHEMA

# tools/liaison/storage/db.py → parents[0]=storage, [1]=liaison, [2]=tools, [3]=仓库根
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

#: ⛔ 不是 data/demo.db。独立库是 design D5 的结论，不是可调参数。
#: `data/` 已被 .gitignore:11 覆盖，库文件不会误入版本管理。
DEFAULT_DB_PATH = REPO_ROOT / "data" / "liaison.db"


def get_connection(db_path: str | os.PathLike[str] | None = None) -> sqlite3.Connection:
    """开一个连接。

    ⛔ **不要传 `isolation_level=None`、也不要设 `autocommit=True`。** 那会让每条语句
    各自提交，业务写与 `effect_log` 写从此分处两个事务——工程铁律 1 当场破掉，
    且**不报错、无症状**。默认的 LEGACY_TRANSACTION_CONTROL 正是这里需要的语义。
    """
    path = DEFAULT_DB_PATH if db_path is None else pathlib.Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    # 外键：liaison_task.msgid → liaison_message.msgid 的引用完整性靠它生效。
    # SQLite 默认关闭外键，⛔ 不设这条则 Task 2「条目缺少来源信息不允许写入」形同虚设。
    conn.execute("PRAGMA foreign_keys = ON")
    # 本服务是单进程单连接，但库文件可能被只读的导出/排查命令同时打开。
    # WAL 让读不阻塞写；busy_timeout 是纵深防御，不是并发写的许可。
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """幂等建表。

    这是**本服务唯一被允许 commit 的非 effect 路径**（见模块 docstring 的白名单）。
    建表发生在任何 effect 之前，此时连接上没有未提交的业务写，因此这次 commit
    不可能把半截业务事务带下去。
    """
    conn.executescript(SCHEMA)
    conn.commit()
