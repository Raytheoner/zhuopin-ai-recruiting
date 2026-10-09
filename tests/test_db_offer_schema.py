"""Offer 域模型（offer-generation U1）：新库建表齐全、CHECK 反证、offer 无薪资列、
stage 预置 offer/hired 与老库重建迁移。

写法对齐 tests/test_db_m3_schema.py：schema 反证全部直接 INSERT 绕过应用层，
由数据库 CHECK 强制拒绝。

⚠️ 本文件随交付单元 U1 的任务逐条长大：Task 1 先落 `letter_template` /
`candidate_letter` 两张表的用例，其余表与 stage 迁移的用例在对应任务落地时补。
"""
import sqlite3
from pathlib import Path

import pytest

from app.storage.db import _ADDED_COLUMNS, get_connection, init_schema


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "offer.db"))
    init_schema(c)
    return c


def _seed_parents(conn):
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '底层软件工程师', 'approved')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'sha-1', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1', 'c1', 'j1', 'r1', 'initial')"
    )
    conn.execute(
        "INSERT INTO analysis_run (id, configured_model, prompt_version, temperature, "
        "input_hash, raw_response) VALUES ('run1', 'deepseek-chat', 'letter-offer-v1', 0.0, 'h', '{}')"
    )
    conn.commit()


# ── 新库建表齐全 ────────────────────────────────────────────────


def test_letter_template_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "letter_template")
    assert _columns(conn, "letter_template") == {
        "kind", "version", "body", "updated_by", "updated_at",
    }


def test_candidate_letter_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "candidate_letter")
    assert _columns(conn, "candidate_letter") == {
        "id", "application_id", "kind", "version", "template_version", "body",
        "ai_generated", "authorship_marked_by", "authorship_marked_at",
        "authorship_from_version", "analysis_run_id", "sent_status", "sent_channel",
        "created_by", "created_at",
    }


def test_offer_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "offer")
    assert _columns(conn, "offer") == {
        "id", "application_id", "job_id", "department", "start_date", "report_to",
        "note", "status", "approval_round", "created_by", "created_at",
        "updated_by", "updated_at",
    }


def test_offer_approval_chain_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "offer_approval_chain")
    assert _columns(conn, "offer_approval_chain") == {
        "job_id", "level", "approver_account_ids", "updated_by", "updated_at",
    }


def test_offer_approval_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "offer_approval")
    assert _columns(conn, "offer_approval") == {
        "id", "offer_id", "round", "level", "approver", "decision", "comment", "at",
    }


def test_letter_access_log_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "letter_access_log")
    assert _columns(conn, "letter_access_log") == {
        "id", "accessor", "application_id", "letter_id", "access_type", "at",
    }


def test_offer_table_has_no_salary_columns(conn):
    """本包合规红线断言：offer 表列名不匹配薪资关键词。"""
    forbidden = ("salary", "pay", "compensation", "bonus", "薪")
    offending = [
        col
        for col in _columns(conn, "offer")
        if any(k in col.lower() or k in col for k in forbidden)
    ]
    assert offending == []


def test_offer_approval_unique_on_offer_round_level(conn):
    _seed_parents(conn)
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, "
        "report_to, created_by) VALUES ('o1', 'app1', 'j1', 'd', '2026-10-08', 'r', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO offer_approval (id, offer_id, round, level, approver, decision) "
        "VALUES ('oa1', 'o1', 1, 1, 'alice', 'approved')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO offer_approval (id, offer_id, round, level, approver, decision) "
            "VALUES ('oa2', 'o1', 1, 1, 'bob', 'approved')"
        )


def test_letter_access_log_access_type_check(conn):
    _seed_parents(conn)
    conn.execute(
        "INSERT INTO candidate_letter (id, application_id, kind, version, "
        "template_version, body, ai_generated, created_by) "
        "VALUES ('l1', 'app1', 'offer', 1, 1, 'b', 1, 'hr-1')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO letter_access_log (id, accessor, application_id, letter_id, access_type) "
            "VALUES ('log1', 'alice', 'app1', 'l1', 'download')"
        )


def test_letter_access_log_accessor_must_not_be_blank(conn):
    _seed_parents(conn)
    conn.execute(
        "INSERT INTO candidate_letter (id, application_id, kind, version, "
        "template_version, body, ai_generated, created_by) "
        "VALUES ('l1', 'app1', 'offer', 1, 1, 'b', 1, 'hr-1')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO letter_access_log (id, accessor, application_id, letter_id, access_type) "
            "VALUES ('log1', '  ', 'app1', 'l1', 'view')"
        )


def test_offer_new_tables_never_enter_the_add_column_path():
    tables_touched = {table for table, _column, _ddl in _ADDED_COLUMNS}
    new_tables = {
        "letter_template", "candidate_letter", "offer",
        "offer_approval_chain", "offer_approval", "letter_access_log",
    }
    assert not (new_tables & tables_touched)


# ── stage 预置 offer/hired 与老库重建迁移（Task 5）──────────────────


def test_stage_has_offer_and_hired_presets(conn):
    rows = {
        row[0]: row[1]
        for row in conn.execute("SELECT stage_type, name FROM stage").fetchall()
    }
    assert rows == {
        "initial": "初筛",
        "screening": "评估中",
        "rejected": "已淘汰",
        "interview": "面试",
        "offer": "Offer",
        "hired": "已入职",
    }


def test_stage_check_accepts_offer_and_hired(conn):
    """预置行之外的 offer/hired 行也必须被 CHECK 接受（同 stage_type、不同 id/name）。"""
    conn.execute(
        "INSERT INTO stage (id, name, stage_type) VALUES ('offer-2', '发 Offer', 'offer')"
    )
    conn.execute(
        "INSERT INTO stage (id, name, stage_type) VALUES ('hired-2', '正式入职', 'hired')"
    )


def test_stage_check_rejects_unknown_stage_type(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO stage (id, name, stage_type) VALUES ('bad', 'bad', 'bad')"
        )


_LEGACY_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "zp51_demo_db_schema_pre_m3.sql"
)


def _legacy_pre_m3_db(tmp_path):
    """.51 上 M2 时期的老库：stage 的 CHECK 还是三值、只有三条种子行。"""
    c = get_connection(str(tmp_path / "legacy_offer.db"))
    c.executescript(_LEGACY_FIXTURE_PATH.read_text(encoding="utf-8"))
    c.execute("INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('initial', '初筛', 'initial')")
    c.execute("INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('screening', '评估中', 'screening')")
    c.execute("INSERT OR IGNORE INTO stage (id, name, stage_type) VALUES ('rejected', '已淘汰', 'rejected')")
    c.commit()
    return c


def _seed_application_on_stage(c) -> None:
    """给老库灌一条投递 + 流转事实：它们的外键指向 stage，重建 stage 时最容易
    被顺手带丢（DROP TABLE 会级联删掉引用行的风险）。"""
    c.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式软件工程师')")
    c.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    c.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'sha-1', 'hr-1')"
    )
    c.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app1', 'c1', 'j1', 'r1', 'screening')"
    )
    c.execute(
        "INSERT INTO application_stage_history "
        "(id, application_id, from_stage_id, to_stage_id, actor_type, actor) "
        "VALUES ('h1', 'app1', 'initial', 'screening', 'human', 'hr-1')"
    )
    c.commit()


def test_legacy_db_stage_rebuild_keeps_original_rows_and_adds_offer_hired(tmp_path):
    conn = _legacy_pre_m3_db(tmp_path)
    _seed_application_on_stage(conn)
    assert {r[0] for r in conn.execute("SELECT id FROM stage")} == {
        "initial", "screening", "rejected",
    }
    # 老库的 CHECK 仍是三值：offer 此刻还不合法（正是本迁移要修的状态）。
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO stage (id, name, stage_type) VALUES ('offer', 'Offer', 'offer')"
        )

    init_schema(conn)

    rows = {r[0]: r[1] for r in conn.execute("SELECT id, stage_type FROM stage")}
    assert rows == {
        "initial": "initial",
        "screening": "screening",
        "rejected": "rejected",
        "interview": "interview",
        "offer": "offer",
        "hired": "hired",
    }
    assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    # 同 test_legacy_db_stage_rebuild_restores_foreign_key_enforcement：四值老库
    # （.51 实测状态）重建后连接的外键强制也必须回到 ON。
    assert conn.execute("PRAGMA foreign_keys").fetchone() == (1,)
    assert conn.execute("PRAGMA foreign_keys").fetchone() == (1,)
    # 依赖 stage 的既有行一条不丢，外键仍指得着重建后的新表。
    assert conn.execute("SELECT current_stage_id FROM application WHERE id='app1'").fetchone() == (
        "screening",
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE application_id='app1'"
    ).fetchone() == (1,)


def test_legacy_db_stage_rebuild_is_idempotent(tmp_path):
    """重建只在 CHECK 缺 offer/hired 时发生；跑第二遍 init_schema 不得再重建。"""
    conn = _legacy_pre_m3_db(tmp_path)
    init_schema(conn)
    rebuilt_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='stage'"
    ).fetchone()[0]
    rows_before = conn.execute("SELECT id, name, stage_type FROM stage ORDER BY id").fetchall()

    init_schema(conn)

    assert conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='stage'"
    ).fetchone()[0] == rebuilt_sql
    assert conn.execute(
        "SELECT id, name, stage_type FROM stage ORDER BY id"
    ).fetchall() == rows_before
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_legacy_db_stage_rebuild_restores_foreign_key_enforcement(tmp_path):
    """老库重建后，连接级 `PRAGMA foreign_keys` 必须回到 1。

    `_rebuild_stage_table` 期间要关外键（DROP/RENAME 引用表），但 PRAGMA 在事务内
    是 no-op——重建 DML 未提交就把 `foreign_keys = ON` 写进 finally，等于没开；
    连接的外键强制会被留在 OFF，后续所有写入的引用完整性裸奔。故这里既断言
    PRAGMA 取值，也用一次真实的非法外键写入复核强制确实生效。
    """
    conn = _legacy_pre_m3_db(tmp_path)

    init_schema(conn)

    assert conn.execute("PRAGMA foreign_keys").fetchone() == (1,)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    # 行为级复核：外键真的在强制，指向不存在 stage 的投递写不进去。
    conn.execute("INSERT INTO job (id, title) VALUES ('j2', '嵌入式软件工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c2', '李四')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r2', 'j2', 'synthetic', 'b.pdf', 'sha-2', 'hr-1')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
            "VALUES ('app2', 'c2', 'j2', 'r2', 'no-such-stage')"
        )
    conn.rollback()


_LEGACY_FOUR_VALUE_STAGE_SQL = """
CREATE TABLE stage (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    stage_type TEXT NOT NULL CHECK (stage_type IN ('initial', 'screening', 'rejected', 'interview'))
);
INSERT INTO stage (id, name, stage_type) VALUES ('initial', '初筛', 'initial');
INSERT INTO stage (id, name, stage_type) VALUES ('screening', '评估中', 'screening');
INSERT INTO stage (id, name, stage_type) VALUES ('rejected', '已淘汰', 'rejected');
INSERT INTO stage (id, name, stage_type) VALUES ('interview', '面试', 'interview');
"""


def test_four_value_stage_db_gains_offer_and_hired_without_losing_interview(tmp_path):
    """.51 实测状态是「M2 三值 + interview 四值」（1001R 已合并），重建后必须变成
    六值：interview 行保留，另补 offer/hired 两行。"""
    conn = get_connection(str(tmp_path / "four_value.db"))
    conn.executescript(_LEGACY_FOUR_VALUE_STAGE_SQL)
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO stage (id, name, stage_type) VALUES ('offer', 'Offer', 'offer')"
        )

    init_schema(conn)

    rows = {r[0]: r[1] for r in conn.execute("SELECT id, stage_type FROM stage")}
    assert rows == {
        "initial": "initial",
        "screening": "screening",
        "rejected": "rejected",
        "interview": "interview",
        "offer": "offer",
        "hired": "hired",
    }
    assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
