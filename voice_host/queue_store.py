"""语音主机本地事件队列（voice-structured-interview U4 tasks 5.1-5.9，
design D19"worker 的输出是 turn 事件队列（本地 SQLite 队列，被 .51 拉走后
标记）与录音文件"）。

⛔ 不 import app.storage、不 import 任何 `.51` 专属模块——语音主机结构上
不可能访问 `.51` 的数据库（本计划「Global Constraints 铁律 1 的适用说明」）。
全部用 stdlib sqlite3，独立 schema、独立连接。
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
    id TEXT PRIMARY KEY NOT NULL,
    bundle_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'in_progress'
        CHECK (status IN ('in_progress', 'completed', 'interrupted')),
    current_answer_mode TEXT NOT NULL DEFAULT 'voice' CHECK (current_answer_mode IN ('voice', 'text')),
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS turn_event (
    id TEXT PRIMARY KEY NOT NULL,
    session_id TEXT NOT NULL REFERENCES session(id),
    seq INTEGER NOT NULL,
    question_id TEXT NOT NULL,
    question_text TEXT NOT NULL,
    answer_text TEXT NOT NULL,
    answer_mode TEXT NOT NULL CHECK (answer_mode IN ('voice', 'text')),
    audio_start_ms INTEGER,
    audio_end_ms INTEGER,
    asr_confidence REAL,
    follow_up_of_seq INTEGER,
    interrupted_at_ms INTEGER,
    latency_json TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_turn_event_session_seq ON turn_event (session_id, seq);

CREATE TABLE IF NOT EXISTS recording_file (
    session_id TEXT PRIMARY KEY NOT NULL REFERENCES session(id),
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    recorded_at REAL NOT NULL,
    transferred_at REAL
);

CREATE TABLE IF NOT EXISTS voice_host_event (
    id TEXT PRIMARY KEY NOT NULL,
    session_id TEXT NOT NULL REFERENCES session(id),
    event_type TEXT NOT NULL CHECK (event_type IN (
        'mode_switched_manual', 'mode_switched_after_prompt', 'network_quality_prompted'
    )),
    detail TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_text_answer (
    session_id TEXT PRIMARY KEY NOT NULL REFERENCES session(id),
    text TEXT NOT NULL,
    submitted_at REAL NOT NULL
);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def open_session(conn: sqlite3.Connection, *, session_id: str, bundle_json: str) -> None:
    """`INSERT OR IGNORE`：同一 `session_id` 重复 POST /sessions（`.51` 每轮
    轮询都会调用一次 create_session）天然幂等，不需要额外的幂等键机制——
    `session_id` 本身就是这一侧的天然幂等键。"""
    conn.execute(
        "INSERT OR IGNORE INTO session (id, bundle_json, created_at) VALUES (?, ?, ?)",
        (session_id, bundle_json, time.time()),
    )
    conn.commit()


def get_session_status(conn: sqlite3.Connection, *, session_id: str) -> str | None:
    row = conn.execute("SELECT status FROM session WHERE id = ?", (session_id,)).fetchone()
    return row[0] if row else None


def get_current_answer_mode(conn: sqlite3.Connection, *, session_id: str) -> str:
    row = conn.execute("SELECT current_answer_mode FROM session WHERE id = ?", (session_id,)).fetchone()
    return row[0] if row else "voice"


def set_current_answer_mode(conn: sqlite3.Connection, *, session_id: str, mode: str) -> None:
    conn.execute("UPDATE session SET current_answer_mode = ? WHERE id = ?", (mode, session_id))
    conn.commit()


def close_session(conn: sqlite3.Connection, *, session_id: str, status: str) -> None:
    conn.execute("UPDATE session SET status = ? WHERE id = ?", (status, session_id))
    conn.commit()


def append_turn_event(
    conn: sqlite3.Connection, *, session_id: str, seq: int, question_id: str, question_text: str,
    answer_text: str, answer_mode: str, audio_start_ms: int | None, audio_end_ms: int | None,
    asr_confidence: float | None, follow_up_of_seq: int | None, interrupted_at_ms: int | None,
    latency: dict,
) -> None:
    """`(session_id, seq)` 唯一索引是幂等边界——worker.py 每题只调用一次，
    但重跑/重试时用 `INSERT OR IGNORE` 兜底，不因重复调用报错。"""
    conn.execute(
        "INSERT OR IGNORE INTO turn_event (id, session_id, seq, question_id, question_text, "
        "answer_text, answer_mode, audio_start_ms, audio_end_ms, asr_confidence, "
        "follow_up_of_seq, interrupted_at_ms, latency_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(uuid.uuid4()), session_id, seq, question_id, question_text, answer_text, answer_mode,
            audio_start_ms, audio_end_ms, asr_confidence, follow_up_of_seq, interrupted_at_ms,
            json.dumps(latency, ensure_ascii=False),
        ),
    )
    conn.commit()


def events_since(conn: sqlite3.Connection, *, session_id: str, since_seq: int) -> list[dict]:
    rows = conn.execute(
        "SELECT seq, question_id, question_text, answer_text, answer_mode, audio_start_ms, "
        "audio_end_ms, asr_confidence, follow_up_of_seq, interrupted_at_ms, latency_json "
        "FROM turn_event WHERE session_id = ? AND seq > ? ORDER BY seq",
        (session_id, since_seq),
    ).fetchall()
    return [
        {
            "seq": r[0], "question_id": r[1], "question_text": r[2], "answer_text": r[3],
            "answer_mode": r[4], "audio_start_ms": r[5], "audio_end_ms": r[6], "asr_confidence": r[7],
            "follow_up_of_seq": r[8], "interrupted_at_ms": r[9], "latency": json.loads(r[10]),
        }
        for r in rows
    ]


def record_recording_file(conn: sqlite3.Connection, *, session_id: str, path: str, sha256: str) -> None:
    conn.execute(
        "INSERT INTO recording_file (session_id, path, sha256, recorded_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(session_id) DO UPDATE SET path = excluded.path, sha256 = excluded.sha256, "
        "recorded_at = excluded.recorded_at",
        (session_id, path, sha256, time.time()),
    )
    conn.commit()


def get_recording_file(conn: sqlite3.Connection, *, session_id: str) -> dict | None:
    row = conn.execute(
        "SELECT path, sha256, transferred_at FROM recording_file WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is None:
        return None
    return {"path": row[0], "sha256": row[1], "transferred": row[2] is not None}


def mark_recording_transferred(conn: sqlite3.Connection, *, session_id: str) -> None:
    conn.execute(
        "UPDATE recording_file SET transferred_at = ? WHERE session_id = ?", (time.time(), session_id)
    )
    conn.commit()


def sweep_expired_intermediate_files(conn: sqlite3.Connection, *, now: float, max_age_hours: int = 24) -> list[str]:
    """回传前的中间文件过期兜底清理（interview-recording-retention spec
    「语音主机不留副本」：回传成功即删由 Task 12 的正常路径处理；本函数是
    "`.51` 从未成功回传"这种异常情况的独立安全网）。只删
    `transferred_at IS NULL` 且 `recorded_at` 早于 `max_age_hours` 之前的行，
    已转移的文件（`transferred_at` 非空）不受影响。"""
    cutoff = now - max_age_hours * 3600
    rows = conn.execute(
        "SELECT session_id, path FROM recording_file WHERE transferred_at IS NULL AND recorded_at < ?",
        (cutoff,),
    ).fetchall()
    deleted: list[str] = []
    for session_id, path in rows:
        Path(path).unlink(missing_ok=True)
        conn.execute("DELETE FROM recording_file WHERE session_id = ?", (session_id,))
        deleted.append(path)
    conn.commit()
    return deleted


def record_voice_host_event(
    conn: sqlite3.Connection, *, session_id: str, event_type: str, detail: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO voice_host_event (id, session_id, event_type, detail, created_at) VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), session_id, event_type, detail, time.time()),
    )
    conn.commit()


def submit_text_answer(conn: sqlite3.Connection, *, session_id: str, text: str) -> None:
    """候选人切到文本作答后提交一条答案（tasks 5.7）。`ON CONFLICT` 覆盖旧值
    ——同一时刻只有一条"待消费"的文本答案，worker.py 的 `TextAnswerAdapter`
    轮询消费它（`pop_pending_text_answer`）。"""
    conn.execute(
        "INSERT INTO pending_text_answer (session_id, text, submitted_at) VALUES (?, ?, ?) "
        "ON CONFLICT(session_id) DO UPDATE SET text = excluded.text, submitted_at = excluded.submitted_at",
        (session_id, text, time.time()),
    )
    conn.commit()


def pop_pending_text_answer(conn: sqlite3.Connection, *, session_id: str) -> str | None:
    row = conn.execute(
        "SELECT text FROM pending_text_answer WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM pending_text_answer WHERE session_id = ?", (session_id,))
    conn.commit()
    return row[0]
