"""onboarding-flow U2 前置（tasks 2.1）：application.status CHECK 放宽到含 'hired'。

磁盘真身：application.status 仍是 ('active','rejected','withdrawn')（offer-generation
U5 的 {ongoing,hired,refused} 迁移未交付）。U2 的实例化前置要求 status='hired'，
测试夹具要能直接置位，故本 Task 只放宽到含 'hired'（表重建，同
_rebuild_hr_account_role_check 先例）。ongoing/refused 仍归 offer-generation U5。
"""
import sqlite3

import pytest

from app.storage.db import get_connection, init_schema


def _seed_referenced_rows(conn):
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '嵌入式工程师', 'approved')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('res-1', 'j1', 'synthetic', 'a.docx', 'sha-1', 'hr-1')"
    )
    conn.commit()


def test_fresh_schema_accepts_hired_status(tmp_path):
    conn = get_connection(str(tmp_path / "app.db"))
    init_schema(conn)
    _seed_referenced_rows(conn)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status) "
        "VALUES ('app-1', 'cand-1', 'j1', 'res-1', 'hired', 'hired')"
    )
    conn.commit()
    assert conn.execute("SELECT status FROM application WHERE id='app-1'").fetchone() == ("hired",)


def test_fresh_schema_still_rejects_bogus_status(tmp_path):
    conn = get_connection(str(tmp_path / "app.db"))
    init_schema(conn)
    _seed_referenced_rows(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status) "
            "VALUES ('app-bad', 'cand-1', 'j1', 'res-1', 'initial', 'bogus')"
        )


def test_legacy_three_value_status_check_is_widened(tmp_path):
    """老库（三值 CHECK）升级后 'hired' 可写、既有行不变、索引复原、FK 复验通过。"""
    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(path)
    raw.executescript(
        """
        CREATE TABLE stage (
            id TEXT PRIMARY KEY NOT NULL,
            name TEXT NOT NULL,
            stage_type TEXT NOT NULL
        );
        CREATE TABLE job (id TEXT PRIMARY KEY, title TEXT NOT NULL, department TEXT,
                          status TEXT NOT NULL DEFAULT 'drafting',
                          created_at TEXT NOT NULL DEFAULT (datetime('now')),
                          parse_confidence_threshold REAL NOT NULL DEFAULT 0.7);
        CREATE TABLE candidate (id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL,
                                phone_hash TEXT,
                                created_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE resume (id TEXT PRIMARY KEY NOT NULL,
                             job_id TEXT NOT NULL REFERENCES job(id),
                             sample_class TEXT NOT NULL,
                             file_name TEXT NOT NULL,
                             content_sha256 TEXT NOT NULL,
                             status TEXT NOT NULL DEFAULT 'pending',
                             uploaded_by TEXT NOT NULL,
                             uploaded_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE application (
            id TEXT PRIMARY KEY NOT NULL,
            candidate_id TEXT NOT NULL REFERENCES candidate(id),
            job_id TEXT NOT NULL REFERENCES job(id),
            resume_id TEXT NOT NULL REFERENCES resume(id),
            current_stage_id TEXT NOT NULL REFERENCES stage(id),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'rejected', 'withdrawn')),
            kanban_state TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX idx_application_resume ON application (resume_id);
        CREATE INDEX idx_application_job ON application (job_id);
        CREATE INDEX idx_application_candidate ON application (candidate_id);
        INSERT INTO stage (id, name, stage_type) VALUES ('initial', '初筛', 'initial');
        INSERT INTO stage (id, name, stage_type) VALUES ('hired', '已入职', 'hired');
        INSERT INTO job (id, title, status) VALUES ('j1', '嵌入式工程师', 'approved');
        INSERT INTO candidate (id, name) VALUES ('cand-1', '张三');
        INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by)
               VALUES ('res-1', 'j1', 'synthetic', 'a.docx', 'sha-1', 'hr-1');
        INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status)
               VALUES ('app-old', 'cand-1', 'j1', 'res-1', 'initial', 'active');
        """
    )
    raw.commit()
    raw.close()

    conn = get_connection(str(path))
    init_schema(conn)

    assert conn.execute("SELECT status FROM application WHERE id='app-old'").fetchone() == ("active",)
    conn.execute(
        "UPDATE application SET status='hired', current_stage_id='hired' WHERE id='app-old'"
    )
    conn.commit()
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    for idx in ("idx_application_resume", "idx_application_job", "idx_application_candidate"):
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (idx,)
        ).fetchone()
        assert row is not None, f"{idx} 应在重建后存在"


def test_legacy_application_without_status_column_is_left_alone(tmp_path):
    """M2 U1 之前形态的老库（application 是老五列、连 status 都没有）：本次只
    「放宽 CHECK」，不补列——这类库必须原样跳过重建，否则重建的
    `INSERT ... SELECT ... status ...` 会撞 `no such column: status`
    （tests/test_db_migration.py 的 _legacy_db 夹具正是这一形态，实测炸过一次）。
    """
    path = tmp_path / "legacy_no_status.db"
    raw = sqlite3.connect(path)
    raw.executescript(
        """
        CREATE TABLE stage (id TEXT PRIMARY KEY, name TEXT NOT NULL, stage_type TEXT);
        CREATE TABLE job (id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'drafting',
                          created_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE candidate (id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL,
                                phone_hash TEXT,
                                created_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE resume (id TEXT PRIMARY KEY NOT NULL,
                             job_id TEXT NOT NULL REFERENCES job(id),
                             sample_class TEXT NOT NULL,
                             file_name TEXT NOT NULL,
                             content_sha256 TEXT NOT NULL,
                             uploaded_by TEXT NOT NULL,
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

    assert conn.execute("SELECT current_stage_id FROM application WHERE id='app-old'").fetchone() == (
        "initial",
    )
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
