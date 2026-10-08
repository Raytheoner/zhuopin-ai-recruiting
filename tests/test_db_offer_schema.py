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


def test_offer_new_tables_never_enter_the_add_column_path():
    tables_touched = {table for table, _column, _ddl in _ADDED_COLUMNS}
    new_tables = {"letter_template", "candidate_letter"}
    assert not (new_tables & tables_touched)
