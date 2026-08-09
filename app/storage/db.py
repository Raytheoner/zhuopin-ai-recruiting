import sqlite3
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
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
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


def get_connection(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI dispatches sync route handlers into a
    # worker threadpool (a different thread per request), but create_app()
    # holds one shared connection created on the startup thread. Demo scope
    # has no concurrent-write requirement (design.md 非目标: 不追求高并发);
    # M2's move to Postgres replaces this with per-request pooled connections.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
