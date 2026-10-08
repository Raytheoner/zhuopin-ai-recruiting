"""onboarding-flow U1 入职域模型：新库建表齐全、CHECK 反证、onboarding_item 无内容列、
hr_account 角色列、留存策略配置位。老库升级回归在 tests/test_db_migration.py（Task 4）。"""
# 本文件随交付单元 U1 的任务逐条长大（Task 1→4）。
# ⚠️ plan Task 1 代码块里的 `_ONBOARDING_TABLES` + `test_onboarding_tables_all_exist`
#   在 Task 1 未写入：Task 1 只建 onboarding_template，其余五张表由 Task 2/3 建，
#   六表齐全的冒烟断言提前放会让 Task 1/2 的「全跑全绿」判据不成立。该断言应在
#   Task 3（六表全部建齐）时补回，或由 final review 统一收口。
import sqlite3

import pytest

from app.config import Settings
from app.storage.db import get_connection, init_schema


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "onboarding.db"))
    init_schema(c)
    return c


def _seed_application(conn: sqlite3.Connection, application_id: str = "app-1") -> None:
    """为带 application/candidate 外键的断言准备一份投递。"""
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES ('j1', '嵌入式工程师', 'approved')"
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('res-1', 'j1', 'synthetic', 'a.docx', 'sha-1', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, 'cand-1', 'j1', 'res-1', 'initial')",
        (application_id,),
    )
    conn.commit()


# ── Task 1：onboarding_template ──────────────────────────────────────────


def test_onboarding_template_columns(conn):
    assert _columns(conn, "onboarding_template") == {
        "id", "scope_type", "scope_id", "version", "items", "updated_by", "updated_at",
    }


def test_onboarding_template_scope_type_check(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO onboarding_template "
            "(id, scope_type, scope_id, version, items, updated_by) "
            "VALUES ('t-bad', 'team', 't', 1, '[]', 'hr')"
        )
    for scope_type in ("job", "department"):
        conn.execute(
            "INSERT INTO onboarding_template "
            "(id, scope_type, scope_id, version, items, updated_by) "
            "VALUES (?, ?, ?, 1, '[]', 'hr')",
            (f"t-{scope_type}", scope_type, "t"),
        )
    conn.commit()


def test_onboarding_template_scope_version_unique(conn):
    conn.execute(
        "INSERT INTO onboarding_template "
        "(id, scope_type, scope_id, version, items, updated_by) "
        "VALUES ('t-1', 'department', 'd', 1, '[]', 'hr')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO onboarding_template "
            "(id, scope_type, scope_id, version, items, updated_by) "
            "VALUES ('t-2', 'department', 'd', 1, '[]', 'hr')"
        )


def test_onboarding_template_updated_by_cannot_be_blank(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO onboarding_template "
            "(id, scope_type, scope_id, version, items, updated_by) "
            "VALUES ('t-blank', 'department', 'd', 1, '[]', '   ')"
        )
