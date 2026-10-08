"""interview-scheduling U1 · Task 3 的 schema 回归（TDD 先写测试）。

覆盖 `interview_invitation_draft` 与 `invitation_template` 两张表：
- 新库 `init_schema()` 后两表齐全、列齐；
- `(slot_id, version)` 唯一——同场次重复生成产生新版本、旧版永久保留（spec
  「按场次生成邀约文案」）；
- `ai_generated` 只收 0/1，是合规红线「AI 生成的邀约须带标识」的存储层落点；
  `authorship_marked_by/at` 是「标记为人工撰写」的留痕列（可空）；
- `invitation_template.version` 是主键，一版一行、不覆盖（spec「文案模板的
  来源与版本」）；
- `slot_id` 外键在 `PRAGMA foreign_keys=ON` 下被强制。

⛔ 不改动既有表；本文件只测 Task 3 引入的两张新表。
"""
from __future__ import annotations

import sqlite3

import pytest

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
    c = get_connection(str(tmp_path / "invitation_draft.db"))
    init_schema(c)
    return c


def _seed_slot(conn: sqlite3.Connection, slot_id: str = "s1") -> str:
    """建一条可供 `slot_id` 外键引用的场次（走既有 job→candidate→resume→
    application→interview_slot 链）。"""
    # 同一用例里可能要建多条场次，前半段种子链用 OR IGNORE 复用同一 job/投递。
    conn.execute("INSERT OR IGNORE INTO job (id, title) VALUES ('j1', '嵌入式软件工程师')")
    conn.execute("INSERT OR IGNORE INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT OR IGNORE INTO resume "
        "(id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'sha-1', 'hr-1')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO application "
        "(id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1', 'c1', 'j1', 'r1', 'initial')"
    )
    conn.execute(
        "INSERT INTO interview_slot (id, application_id, round, start_at, end_at, mode) "
        "VALUES (?, 'app1', 1, '2026-10-09 14:00', '2026-10-09 15:00', 'onsite')",
        (slot_id,),
    )
    conn.commit()
    return slot_id


def _insert_draft(
    conn: sqlite3.Connection,
    *,
    draft_id: str = "d1",
    slot_id: str = "s1",
    version: int = 1,
    ai_generated: int = 1,
) -> None:
    conn.execute(
        "INSERT INTO interview_invitation_draft "
        "(id, slot_id, version, template_version, body, ai_generated) "
        "VALUES (?, ?, ?, 'v1', '您好，邀请您参加面试。', ?)",
        (draft_id, slot_id, version, ai_generated),
    )
    conn.commit()


# ── 建表齐全 ──────────────────────────────────────────────────────────────


def test_invitation_draft_tables_exist(conn):
    assert _table_exists(conn, "interview_invitation_draft")
    assert _table_exists(conn, "invitation_template")


def test_interview_invitation_draft_has_expected_columns(conn):
    assert _columns(conn, "interview_invitation_draft") == {
        "id",
        "slot_id",
        "version",
        "template_version",
        "body",
        "ai_generated",
        "authorship_marked_by",
        "authorship_marked_at",
        "analysis_run_id",
        "created_at",
    }


def test_invitation_template_has_expected_columns(conn):
    assert _columns(conn, "invitation_template") == {
        "version",
        "body",
        "updated_by",
        "updated_at",
    }


# ── `(slot_id, version)` 唯一：重复生成产生新版本、旧版永久保留 ────────────


def test_draft_version_is_unique_per_slot(conn):
    _seed_slot(conn)
    _insert_draft(conn, draft_id="d1", version=1)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_draft(conn, draft_id="d2", version=1)


def test_draft_new_version_keeps_old_row(conn):
    _seed_slot(conn)
    _insert_draft(conn, draft_id="d1", version=1)
    _insert_draft(conn, draft_id="d2", version=2)
    versions = [
        row[0]
        for row in conn.execute(
            "SELECT version FROM interview_invitation_draft WHERE slot_id='s1' ORDER BY version"
        )
    ]
    assert versions == [1, 2]


def test_draft_same_version_allowed_across_slots(conn):
    """唯一约束是 (slot_id, version)，不是全局 version。"""
    _seed_slot(conn, slot_id="s1")
    _seed_slot(conn, slot_id="s2")
    _insert_draft(conn, draft_id="d1", slot_id="s1", version=1)
    _insert_draft(conn, draft_id="d2", slot_id="s2", version=1)
    count = conn.execute(
        "SELECT COUNT(*) FROM interview_invitation_draft WHERE version=1"
    ).fetchone()[0]
    assert count == 2


# ── CHECK / 默认值 ────────────────────────────────────────────────────────


@pytest.mark.parametrize("ai_generated", [0, 1])
def test_draft_accepts_boolean_ai_generated(conn, ai_generated):
    _seed_slot(conn)
    _insert_draft(conn, ai_generated=ai_generated)


def test_draft_rejects_non_boolean_ai_generated(conn):
    _seed_slot(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_draft(conn, ai_generated=2)


def test_draft_human_authorship_mark_columns_are_nullable(conn):
    """AI 草稿刚生成时是 ai_generated=1、无人工标记；标记为人工撰写后写
    authorship_marked_by/at（spec「人工改写后的标识处置」）。"""
    _seed_slot(conn)
    _insert_draft(conn)
    row = conn.execute(
        "SELECT ai_generated, authorship_marked_by, authorship_marked_at, analysis_run_id "
        "FROM interview_invitation_draft WHERE id='d1'"
    ).fetchone()
    assert row == (1, None, None, None)
    conn.execute(
        "UPDATE interview_invitation_draft "
        "SET authorship_marked_by='hr-1', authorship_marked_at='2026-10-08 10:00:00' "
        "WHERE id='d1'"
    )
    conn.commit()
    assert conn.execute(
        "SELECT authorship_marked_by FROM interview_invitation_draft WHERE id='d1'"
    ).fetchone()[0] == "hr-1"


def test_draft_created_at_defaults_to_now(conn):
    _seed_slot(conn)
    _insert_draft(conn)
    created_at = conn.execute(
        "SELECT created_at FROM interview_invitation_draft WHERE id='d1'"
    ).fetchone()[0]
    assert created_at is not None
    assert created_at != ""


# ── 模板：一版一行、不覆盖 ────────────────────────────────────────────────


def test_invitation_template_version_is_primary_key(conn):
    conn.execute(
        "INSERT INTO invitation_template (version, body, updated_by) "
        "VALUES ('v1', '您好，邀请您参加面试。', 'hr-1')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO invitation_template (version, body, updated_by) "
            "VALUES ('v1', '改写后的文案', 'hr-2')"
        )


def test_invitation_template_keeps_multiple_versions(conn):
    conn.execute(
        "INSERT INTO invitation_template (version, body, updated_by) "
        "VALUES ('v1', '第一版', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO invitation_template (version, body, updated_by) "
        "VALUES ('v2', '第二版', 'hr-1')"
    )
    conn.commit()
    rows = [
        row[0]
        for row in conn.execute("SELECT version FROM invitation_template ORDER BY version")
    ]
    assert rows == ["v1", "v2"]


def test_invitation_template_requires_updated_by(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO invitation_template (version, body, updated_by) "
            "VALUES ('v1', '第一版', NULL)"
        )


def test_invitation_template_updated_at_defaults_to_now(conn):
    conn.execute(
        "INSERT INTO invitation_template (version, body, updated_by) "
        "VALUES ('v1', '第一版', 'hr-1')"
    )
    conn.commit()
    updated_at = conn.execute(
        "SELECT updated_at FROM invitation_template WHERE version='v1'"
    ).fetchone()[0]
    assert updated_at is not None
    assert updated_at != ""


# ── 外键强制 ──────────────────────────────────────────────────────────────


def test_draft_slot_fk_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _insert_draft(conn, slot_id="no-such-slot")
