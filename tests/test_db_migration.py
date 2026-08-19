import json
import sqlite3

import pytest

from app.storage.db import _ADDED_COLUMNS, apply_column_migrations, get_connection, init_schema

# 2026-08-18 及之前 .51 现网 data/demo.db 里 job / job_profile 的真实形态。
# 刻意硬编码而不是从 SCHEMA 裁剪：这两条 DDL 代表"服务器上已经存在的那个库长
# 什么样"，是一个历史事实，不能随 SCHEMA 一起演进——否则这个测试会跟着新代码
# 一起漂移，永远测不出"老库升级不了"这个真正要防的故障。
_LEGACY_JOB_DDL = """
CREATE TABLE job (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    department TEXT,
    status TEXT NOT NULL DEFAULT 'drafting',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_LEGACY_JOB_PROFILE_DDL = """
CREATE TABLE job_profile (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES job(id),
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    unspecified_fields TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _legacy_db(tmp_path) -> sqlite3.Connection:
    """建一个"老 schema + 已有数据"的库，模拟 .51 上的 data/demo.db。"""
    conn = get_connection(str(tmp_path / "legacy.db"))
    conn.executescript(_LEGACY_JOB_DDL + _LEGACY_JOB_PROFILE_DDL)
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES ('old-job', '采购工程师', 'approved')"
    )
    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json, unspecified_fields) "
        "VALUES ('old-job-v1', 'old-job', 1, 'approved', ?, ?)",
        (
            json.dumps({"job_title": "采购工程师"}, ensure_ascii=False),
            json.dumps(["toolchain"], ensure_ascii=False),
        ),
    )
    conn.commit()
    return conn


def test_init_schema_adds_new_columns_to_legacy_db(tmp_path):
    conn = _legacy_db(tmp_path)
    assert "turn_started_at" not in _columns(conn, "job_profile")

    init_schema(conn)

    expected = {column for _table, column, _ddl in _ADDED_COLUMNS}
    assert expected <= _columns(conn, "job_profile")


def test_legacy_rows_survive_migration_with_defaults(tmp_path):
    """既有 15 个 job 的历史行不需要回填：新列必须可空或有常量默认值。"""
    conn = _legacy_db(tmp_path)

    init_schema(conn)

    row = conn.execute(
        "SELECT profile_json, unspecified_fields, is_productive, turn_started_at, "
        "llm_latency_ms, derived_unspecified_fields, ungrounded_fields, llm_response_model "
        "FROM job_profile WHERE id='old-job-v1'"
    ).fetchone()
    assert json.loads(row[0])["job_title"] == "采购工程师"  # 老数据一字不动
    assert json.loads(row[1]) == ["toolchain"]
    assert row[2] == 1  # is_productive 默认按"有产出"算，语义与今天一致
    assert row[3] is None  # 历史行没有时序留痕，留 NULL 而不是编一个
    assert row[4] is None
    assert json.loads(row[5]) == []
    assert json.loads(row[6]) == []
    assert row[7] is None


def test_apply_column_migrations_is_idempotent(tmp_path):
    conn = _legacy_db(tmp_path)

    first = apply_column_migrations(conn)
    second = apply_column_migrations(conn)

    assert set(first) == {column for _table, column, _ddl in _ADDED_COLUMNS}
    assert second == []  # 第二次一列都不加，且不抛 "duplicate column name"

    init_schema(conn)  # 重复跑整个 init_schema 同样不能报错
    init_schema(conn)


def test_fresh_and_migrated_schemas_have_identical_columns(tmp_path):
    """
    漂移守卫：SCHEMA 的 CREATE TABLE 与 _ADDED_COLUMNS 是同一件事的两种表达
    （新库走 CREATE、老库走 ALTER）。只改一边是这类迁移最经典的错法——本地
    新建的库全绿，服务器上的老库缺列，而两者都不会报错。
    """
    fresh = get_connection(str(tmp_path / "fresh.db"))
    init_schema(fresh)

    migrated = _legacy_db(tmp_path)
    init_schema(migrated)

    assert _columns(fresh, "job_profile") == _columns(migrated, "job_profile")


def test_every_added_column_is_nullable_or_has_constant_default(tmp_path):
    """
    "既有行不需要回填"这个承诺的机器判据：notnull=1 的列必须带默认值。
    另外 SQLite 明确拒绝 ALTER TABLE ADD COLUMN 带非常量默认值
    （"Cannot add a column with non-constant default"），所以 DDL 里不能写
    DEFAULT (datetime('now'))——这条测试顺带把那个坑钉死。
    """
    conn = get_connection(str(tmp_path / "fresh.db"))
    init_schema(conn)

    added = {column for _table, column, _ddl in _ADDED_COLUMNS}
    for row in conn.execute("PRAGMA table_info(job_profile)"):
        name, notnull, default = row[1], row[3], row[4]
        if name not in added:
            continue
        if notnull:
            assert default is not None, f"{name} 是 NOT NULL 却没有默认值，老行无法回填"
        assert "datetime(" not in str(default or ""), f"{name} 用了非常量默认值，ALTER TABLE 会被 SQLite 拒绝"
