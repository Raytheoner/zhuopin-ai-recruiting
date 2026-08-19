import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS job (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    department TEXT,
    status TEXT NOT NULL DEFAULT 'drafting',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS job_profile (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES job(id),
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    unspecified_fields TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    -- 本轮是否有产出（新字段或新问题）。追问预算按有产出轮计数，判定在
    -- compute_intake_turn 里做（m1-intake-quality-fixes 第 3 章）。默认 1
    -- 保证历史行与"未接入判定前"的行为与今天完全一致。
    is_productive INTEGER NOT NULL DEFAULT 1,
    -- 本轮起始时刻（HTTP 请求进入、尚未调模型）。轮次**结束**时刻沿用
    -- created_at，不另加列。两者格式必须一致，见 sqlite_utc_now()。
    turn_started_at TEXT,
    -- 本轮 LLM 累计耗时（含重试），单位毫秒。
    llm_latency_ms REAL,
    -- 系统按画像字段表推导出的未指定字段（第 6 章写）。与上面那列 LLM
    -- 自由生成的 unspecified_fields 并存，前者是真源、后者降级为对照。
    derived_unspecified_fields TEXT NOT NULL DEFAULT '[]',
    -- 本轮未通过来源校验的字段清单（第 7 章写）。
    ungrounded_fields TEXT NOT NULL DEFAULT '[]',
    -- 本轮 API 响应里实际返回的模型标识（第 7 章写，铁律 5）。
    llm_response_model TEXT
);

-- 每个 job（thread_id）一行，存该会话到目前为止的完整对话记录。
-- 对话历史必须落在持久层而不是只活在 LangGraph checkpoint 里：IntakeState.history
-- 没有 reducer，每次 invoke 的输入会覆盖 checkpoint 里的旧值，靠 checkpoint 记不住
-- 多轮上下文（见 app/graph/state.py 的说明）。由 effect_persist_draft 在写画像草案
-- 的同一个事务里 UPSERT，保证画像与对话同生共死。
CREATE TABLE IF NOT EXISTS conversation (
    thread_id TEXT PRIMARY KEY,
    history_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS effect_log (
    effect_key TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    node_name TEXT NOT NULL,
    business_key TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_effect_log_key ON effect_log (effect_key);

CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    message_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


# 2026-08-19 起新增的列必须同时出现在两处：上面的 CREATE TABLE（新库）与下面的
# _ADDED_COLUMNS（老库）。CREATE TABLE IF NOT EXISTS 对已存在的表完全无效，
# .51 上 data/demo.db 有 15 个真实 job、部署脚本不重建库——只改 CREATE TABLE
# 的话新列在服务器上永远不会出现，而且不报错（design.md 决策 10）。
# tests/test_db_migration.py 的漂移守卫测试盯着这两处的一致性。
#
# DDL 片段里的 DEFAULT 必须是常量：SQLite 拒绝 ALTER TABLE ADD COLUMN 带
# 非常量默认值（"Cannot add a column with non-constant default"），所以这里
# 不能写 DEFAULT (datetime('now'))。
#
# turn_started_at / llm_latency_ms 是过渡形态，见 docs/tech-debt.md TD-1
# （analysis_run 落地即删）。
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("job_profile", "is_productive", "INTEGER NOT NULL DEFAULT 1"),
    ("job_profile", "turn_started_at", "TEXT"),
    ("job_profile", "llm_latency_ms", "REAL"),
    ("job_profile", "derived_unspecified_fields", "TEXT NOT NULL DEFAULT '[]'"),
    ("job_profile", "ungrounded_fields", "TEXT NOT NULL DEFAULT '[]'"),
    ("job_profile", "llm_response_model", "TEXT"),
)


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def apply_column_migrations(conn: sqlite3.Connection) -> list[str]:
    """
    幂等加列：逐列独立判断、缺哪列补哪列，返回本次真的加上的列名。

    逐列独立是刻意的（design.md 风险表「服务器 SQLite 加列失败或部分成功」）：
    一列失败不影响其余列，重跑一次会把上次没加上的补齐。
    """
    added: list[str] = []
    for table, column, ddl in _ADDED_COLUMNS:
        if column in _existing_columns(conn, table):
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        added.append(column)
    return added


def sqlite_utc_now() -> str:
    """
    与 SQLite `datetime('now')` 完全一致的 UTC 时间串（秒级、无时区后缀）。

    为什么不用 datetime.now().isoformat()：job_profile.created_at 由
    `datetime('now')`（UTC，格式 "YYYY-MM-DD HH:MM:SS"）写入，代表轮次结束
    时刻；turn_started_at 由 Python 侧写入，代表轮次开始时刻。两者格式必须
    一模一样，否则"结束 − 开始"这个减法要先做时区与格式对齐，而这类对齐
    迟早会有人做错——最省事的做法是从一开始就不给人做错的机会。
    """
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def get_connection(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI dispatches sync route handlers into a
    # worker threadpool (a different thread per request), but create_app()
    # holds one shared connection created on the startup thread. Demo scope
    # has no concurrent-write requirement (design.md 非目标: 不追求高并发);
    # M2's move to Postgres replaces this with per-request pooled connections.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL: 方向 A 让 checkpointer 与 effect 层各自持有独立连接后，两个连接
    # 写同一个数据库文件；默认 rollback-journal 模式下，一个连接持有写锁时
    # 另一个连接的写操作会立刻收到 database is locked（SQLITE_BUSY）。WAL
    # 是文件级设置，任一连接设置一次即对整个文件生效（design.md 方向 A 代价
    # 分析）。busy_timeout 是纵深防御：已证伪并发写入假设（本图严格线性），
    # 理论上两个连接不会真正竞争同一把写锁，这条只是防御未来假设被打破时
    # 表现为短暂阻塞重试而不是立刻报错崩溃。
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # 新库走 CREATE TABLE 就已经带全新列，这里是空转；老库（.51 的 demo.db）
    # 靠这一步补列。两条路径的结果必须一致，由 tests/test_db_migration.py 的
    # test_fresh_and_migrated_schemas_have_identical_columns 守着。
    apply_column_migrations(conn)
    conn.commit()
