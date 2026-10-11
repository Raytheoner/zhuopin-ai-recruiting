"""onboarding-flow U3 前置（tasks 3.1/3.3）：终态要写的字面值先要在状态域里合法。

磁盘真身：application.status 是四值（含 U2 加的 'hired'）、offer.status 七值，
都不含本单元要写的 'refused' / 'abandoned'。本文件只管这两张表；流转事实表
action 的值域在 tests/test_db_stage_history_terminal_action.py（Task 2）。
手法与 tests/test_db_application_status_hired.py（U2 Task 1）逐字同源。
"""
import sqlite3

import pytest

from app.storage.db import get_connection, init_schema


def _seed_job_candidate_resume(conn):
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '嵌入式工程师', 'approved')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('res-1', 'j1', 'synthetic', 'a.docx', 'sha-1', 'hr-1')"
    )
    conn.commit()


def _insert_application(conn, application_id, status):
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status) "
        "VALUES (?, 'cand-1', 'j1', 'res-1', 'hired', ?)",
        (application_id, status),
    )


def _insert_offer(conn, offer_id, application_id, status):
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, report_to, "
        "status, approval_round, created_by) "
        "VALUES (?, ?, 'j1', '研发部', '2026-10-20', 'manager-1', ?, 1, 'hr')",
        (offer_id, application_id, status),
    )


# ── 新库 ────────────────────────────────────────────────────────────────


def test_fresh_schema_accepts_refused_and_abandoned(tmp_path):
    conn = get_connection(str(tmp_path / "app.db"))
    init_schema(conn)
    _seed_job_candidate_resume(conn)
    _insert_application(conn, "app-1", "refused")
    _insert_offer(conn, "offer-1", "app-1", "abandoned")
    conn.commit()
    assert conn.execute(
        "SELECT status FROM application WHERE id='app-1'"
    ).fetchone() == ("refused",)
    assert conn.execute(
        "SELECT status FROM offer WHERE id='offer-1'"
    ).fetchone() == ("abandoned",)


def test_fresh_schema_still_rejects_unknown_status(tmp_path):
    conn = get_connection(str(tmp_path / "app.db"))
    init_schema(conn)
    _seed_job_candidate_resume(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_application(conn, "app-bad", "ghost")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_offer(conn, "offer-bad", "app-bad", "auto_sent")


# ── 老库（.51 现网形态：四值 application + 七值 offer）────────────────────

_LEGACY_DDL = """
CREATE TABLE stage (id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL, stage_type TEXT NOT NULL);
CREATE TABLE job (id TEXT PRIMARY KEY, title TEXT NOT NULL, department TEXT,
                  status TEXT NOT NULL DEFAULT 'drafting',
                  created_at TEXT NOT NULL DEFAULT (datetime('now')),
                  parse_confidence_threshold REAL NOT NULL DEFAULT 0.7);
CREATE TABLE candidate (id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL, phone_hash TEXT,
                        created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE resume (id TEXT PRIMARY KEY NOT NULL, job_id TEXT NOT NULL REFERENCES job(id),
                     sample_class TEXT NOT NULL, file_name TEXT NOT NULL,
                     content_sha256 TEXT NOT NULL, uploaded_by TEXT NOT NULL,
                     status TEXT NOT NULL DEFAULT 'pending',
                     uploaded_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE application (
    id TEXT PRIMARY KEY NOT NULL,
    candidate_id TEXT NOT NULL REFERENCES candidate(id),
    job_id TEXT NOT NULL REFERENCES job(id),
    resume_id TEXT NOT NULL REFERENCES resume(id),
    current_stage_id TEXT NOT NULL REFERENCES stage(id),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'rejected', 'withdrawn', 'hired')),
    kanban_state TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX idx_application_resume ON application (resume_id);
CREATE INDEX idx_application_job ON application (job_id);
CREATE INDEX idx_application_candidate ON application (candidate_id);
CREATE TABLE offer (
    id TEXT PRIMARY KEY NOT NULL,
    application_id TEXT NOT NULL UNIQUE REFERENCES application(id),
    job_id TEXT NOT NULL REFERENCES job(id),
    department TEXT NOT NULL,
    start_date TEXT NOT NULL,
    report_to TEXT NOT NULL,
    note TEXT,
    status TEXT NOT NULL DEFAULT 'pending_approval' CHECK (
        status IN ('pending_approval', 'needs_revision', 'approved', 'exported',
                   'accepted', 'declined', 'negotiating')
    ),
    approval_round INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_by TEXT,
    updated_at TEXT
);
CREATE TABLE offer_approval (
    id TEXT PRIMARY KEY NOT NULL,
    offer_id TEXT NOT NULL REFERENCES offer(id),
    round INTEGER NOT NULL,
    level INTEGER NOT NULL,
    approver TEXT NOT NULL,
    decision TEXT NOT NULL,
    comment TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_offer_approval_offer ON offer_approval (offer_id);
INSERT INTO stage (id, name, stage_type) VALUES ('initial', '初筛', 'initial');
INSERT INTO stage (id, name, stage_type) VALUES ('hired', '已入职', 'hired');
INSERT INTO job (id, title, status) VALUES ('j1', '嵌入式工程师', 'approved');
INSERT INTO candidate (id, name) VALUES ('cand-1', '张三');
INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by)
       VALUES ('res-1', 'j1', 'synthetic', 'a.docx', 'sha-1', 'hr-1');
INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status)
       VALUES ('app-old', 'cand-1', 'j1', 'res-1', 'hired', 'hired');
INSERT INTO offer (id, application_id, job_id, department, start_date, report_to, status,
                   approval_round, created_by)
       VALUES ('offer-old', 'app-old', 'j1', '研发部', '2026-10-20', 'manager-1', 'accepted', 1, 'hr');
INSERT INTO offer_approval (id, offer_id, round, level, approver, decision)
       VALUES ('appr-1', 'offer-old', 1, 1, 'boss', 'approved');
"""


def _legacy_conn(tmp_path):
    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(path)
    raw.executescript(_LEGACY_DDL)
    raw.commit()
    raw.close()
    return get_connection(str(path))


def test_legacy_status_checks_are_widened_and_rows_survive(tmp_path):
    conn = _legacy_conn(tmp_path)
    init_schema(conn)

    assert conn.execute("SELECT status FROM application WHERE id='app-old'").fetchone() == ("hired",)
    assert conn.execute("SELECT status FROM offer WHERE id='offer-old'").fetchone() == ("accepted",)
    conn.execute("UPDATE application SET status='refused' WHERE id='app-old'")
    conn.execute("UPDATE offer SET status='abandoned' WHERE id='offer-old'")
    conn.commit()
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    # offer 被 offer_approval 外键引用：重建后引用行必须还在
    assert conn.execute(
        "SELECT COUNT(*) FROM offer_approval WHERE offer_id='offer-old'"
    ).fetchone()[0] == 1
    for idx in ("idx_application_resume", "idx_application_job",
                "idx_application_candidate", "idx_offer_approval_offer"):
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (idx,)
        ).fetchone() is not None, f"{idx} 应在重建后存在"


def test_init_schema_is_idempotent_for_status_widening(tmp_path):
    conn = _legacy_conn(tmp_path)
    init_schema(conn)
    init_schema(conn)  # 第二次必须空转：不报错、不丢行
    assert conn.execute("SELECT COUNT(*) FROM application").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_legacy_application_without_status_column_is_left_alone(tmp_path):
    """M2 U1 之前形态的老库照样要跳过（同 U2 用例）：重建的
    `INSERT ... SELECT ... status ...` 会撞 `no such column: status`。"""
    path = tmp_path / "legacy_no_status.db"
    raw = sqlite3.connect(path)
    raw.executescript(
        """
        CREATE TABLE stage (id TEXT PRIMARY KEY, name TEXT NOT NULL, stage_type TEXT);
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
            id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL REFERENCES candidate(id),
            job_id TEXT NOT NULL REFERENCES job(id),
            resume_id TEXT NOT NULL REFERENCES resume(id),
            current_stage_id TEXT NOT NULL REFERENCES stage(id)
        );
        INSERT INTO stage (id, name, stage_type) VALUES ('initial', '初筛', 'initial');
        INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师');
        INSERT INTO candidate (id, name) VALUES ('cand-1', '张三');
        INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by)
               VALUES ('res-1', 'j1', 'synthetic', 'a.docx', 'sha-1', 'hr-1');
        INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id)
               VALUES ('app-old', 'cand-1', 'j1', 'res-1', 'initial');
        """
    )
    raw.commit()
    raw.close()

    conn = get_connection(str(path))
    init_schema(conn)

    assert conn.execute(
        "SELECT current_stage_id FROM application WHERE id='app-old'"
    ).fetchone() == ("initial",)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
