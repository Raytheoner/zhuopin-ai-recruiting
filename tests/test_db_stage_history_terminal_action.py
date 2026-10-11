"""onboarding-flow U3 前置（tasks 3.1/3.3）：终态流转事实靠 action 承载语义。

两个终态都不改阶段（from_stage_id = to_stage_id）：投递在 Offer 接受时就已
流转到 hired 阶段，入职日确认是同一阶段上的动作；放弃入职是候选人自愿退出，
不能写成流转到 rejected，避免把自愿退出污染进淘汰口径。
action 必须能表达这两个动作，值域由 CHECK 钉死。
"""
import sqlite3

import pytest

from app.storage.db import get_connection, init_schema

_TERMINAL_ACTIONS = ("onboarded", "abandoned")
_SCHEDULING_ACTIONS = ("scheduled", "rescheduled", "cancelled", "completed", "no_show")


def _seed_application(conn):
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '嵌入式工程师', 'approved')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('res-1', 'j1', 'synthetic', 'a.docx', 'sha-1', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status) "
        "VALUES ('app-1', 'cand-1', 'j1', 'res-1', 'hired', 'hired')"
    )
    conn.commit()


def _insert_history(conn, history_id, action, stage="hired"):
    conn.execute(
        "INSERT INTO application_stage_history "
        "(id, application_id, from_stage_id, to_stage_id, actor_type, actor, action) "
        "VALUES (?, 'app-1', ?, ?, 'human', 'hr-user', ?)",
        (history_id, stage, stage, action),
    )


def test_fresh_schema_accepts_terminal_actions(tmp_path):
    conn = get_connection(str(tmp_path / "app.db"))
    init_schema(conn)
    _seed_application(conn)
    for index, action in enumerate(_TERMINAL_ACTIONS):
        _insert_history(conn, f"h-{index}", action)
    conn.commit()
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history "
        "WHERE action IN ('onboarded','abandoned')"
    ).fetchone()[0] == 2


def test_fresh_schema_still_rejects_unknown_action(tmp_path):
    conn = get_connection(str(tmp_path / "app.db"))
    init_schema(conn)
    _seed_application(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_history(conn, "h-bad", "onboarded_by_ai")


def test_legacy_action_check_is_widened(tmp_path):
    """五值 CHECK 升级：既有行、索引保留，动作放宽，非法值拒绝，外键通过。"""
    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(path)
    raw.executescript("""
        CREATE TABLE stage (id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL, stage_type TEXT);
        CREATE TABLE job (id TEXT PRIMARY KEY, title TEXT NOT NULL,
                          status TEXT NOT NULL DEFAULT 'drafting',
                          created_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE candidate (id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL,
                                phone_hash TEXT,
                                created_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE resume (id TEXT PRIMARY KEY NOT NULL, job_id TEXT NOT NULL REFERENCES job(id),
                             sample_class TEXT NOT NULL, file_name TEXT NOT NULL,
                             content_sha256 TEXT NOT NULL, uploaded_by TEXT NOT NULL,
                             uploaded_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE application (
            id TEXT PRIMARY KEY NOT NULL,
            candidate_id TEXT NOT NULL REFERENCES candidate(id),
            job_id TEXT NOT NULL REFERENCES job(id),
            resume_id TEXT NOT NULL REFERENCES resume(id),
            current_stage_id TEXT NOT NULL REFERENCES stage(id),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','rejected','withdrawn','hired','refused')),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE application_stage_history (
            id TEXT PRIMARY KEY NOT NULL,
            application_id TEXT NOT NULL REFERENCES application(id),
            from_stage_id TEXT REFERENCES stage(id),
            to_stage_id TEXT NOT NULL REFERENCES stage(id),
            actor_type TEXT NOT NULL CHECK (actor_type IN ('human','agent')),
            actor TEXT,
            action TEXT CHECK (action IS NULL OR action IN
                ('scheduled','rescheduled','cancelled','completed','no_show')),
            detail_json TEXT,
            occurred_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX idx_application_stage_history_application
            ON application_stage_history (application_id);
        INSERT INTO stage (id, name, stage_type) VALUES ('initial', '初筛', 'initial');
        INSERT INTO stage (id, name, stage_type) VALUES ('interview', '面试', 'interview');
        INSERT INTO stage (id, name, stage_type) VALUES ('hired', '已入职', 'hired');
        INSERT INTO job (id, title, status) VALUES ('j1', '嵌入式工程师', 'approved');
        INSERT INTO candidate (id, name) VALUES ('cand-1', '张三');
        INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by)
               VALUES ('res-1', 'j1', 'synthetic', 'a.docx', 'sha-1', 'hr-1');
        INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id)
               VALUES ('app-1', 'cand-1', 'j1', 'res-1', 'hired');
        INSERT INTO application_stage_history (id, application_id, to_stage_id, actor_type, action)
               VALUES ('h-old', 'app-1', 'interview', 'human', 'rescheduled');
    """)
    raw.commit()
    raw.close()
    conn = get_connection(str(path))
    init_schema(conn)
    assert conn.execute(
        "SELECT action FROM application_stage_history WHERE id='h-old'"
    ).fetchone() == ("rescheduled",)
    for index, action in enumerate(_SCHEDULING_ACTIONS + _TERMINAL_ACTIONS):
        _insert_history(conn, f"h-{index}", action)
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        _insert_history(conn, "h-bad", "onboarded_by_ai")
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' "
        "AND name='idx_application_stage_history_application'"
    ).fetchone() is not None


def test_init_schema_is_idempotent_for_action_widening(tmp_path):
    conn = get_connection(str(tmp_path / "app.db"))
    init_schema(conn)
    init_schema(conn)
    _seed_application(conn)
    _insert_history(conn, "h-1", "onboarded")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM application_stage_history").fetchone()[0] == 1
