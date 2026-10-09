"""Offer 域模型（offer-generation U1）：新库建表齐全、CHECK 反证、offer 无薪资列、
stage 预置 offer/hired 与老库重建迁移。

写法对齐 tests/test_db_m3_schema.py：schema 反证全部直接 INSERT 绕过应用层，
由数据库 CHECK 强制拒绝。

⚠️ 本文件随交付单元 U1 的任务逐条长大：Task 1 先落 `letter_template` /
`candidate_letter` 两张表的用例，其余表与 stage 迁移的用例在对应任务落地时补。
"""
import sqlite3

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
