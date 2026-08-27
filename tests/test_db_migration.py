import json
import sqlite3

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


def test_asked_questions_column_defaults_to_empty_list_on_legacy_rows(tmp_path):
    """
    已问台账走的是 1.1 已经建立的幂等加列路径（delivery-units §5 约定 4）。
    历史行拿到 '[]'，读台账的代码不需要为老库写特例。
    """
    conn = _legacy_db(tmp_path)

    init_schema(conn)

    row = conn.execute(
        "SELECT asked_questions FROM job_profile WHERE id='old-job-v1'"
    ).fetchone()
    assert json.loads(row[0]) == []


# ── ai-audit-trail-and-outbound-gate · U1 的回归守护 ──────────────────────


_AUDIT_TABLES = ("analysis_run", "criterion_score", "pending_approval")


def _seed_effect_log_and_outbox(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO effect_log (effect_key, thread_id, node_name, business_key, applied_at) "
        "VALUES ('old-job:effect_persist_draft:1', 'old-job', 'effect_persist_draft', '1', "
        "datetime('now'))"
    )
    conn.execute(
        "INSERT INTO outbox (thread_id, message_type, payload_json) "
        "VALUES ('old-job', 'profile_card', '{\"body\": \"确认卡片\"}')"
    )
    conn.commit()


def test_audit_tables_are_created_on_a_legacy_db(tmp_path):
    """.51 的老库拿到三张新表，走的是 CREATE TABLE IF NOT EXISTS，无数据迁移。"""
    conn = _legacy_db(tmp_path)

    init_schema(conn)

    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(_AUDIT_TABLES) <= tables


def test_existing_tables_and_rows_are_untouched_by_audit_schema(tmp_path):
    """
    U1 的第二条硬约束的守护：既有表一行不改、一列不加。effect_log 与 outbox
    的列集合与行数在 init_schema 前后必须完全相同。
    """
    conn = _legacy_db(tmp_path)
    init_schema(conn)  # 老库先补齐到今天的形态
    _seed_effect_log_and_outbox(conn)

    before = {
        "effect_log_columns": _columns(conn, "effect_log"),
        "outbox_columns": _columns(conn, "outbox"),
        "effect_log_rows": conn.execute("SELECT count(*) FROM effect_log").fetchone()[0],
        "outbox_rows": conn.execute("SELECT count(*) FROM outbox").fetchone()[0],
        "job_rows": conn.execute("SELECT count(*) FROM job").fetchone()[0],
        "job_profile_columns": _columns(conn, "job_profile"),
    }

    init_schema(conn)

    after = {
        "effect_log_columns": _columns(conn, "effect_log"),
        "outbox_columns": _columns(conn, "outbox"),
        "effect_log_rows": conn.execute("SELECT count(*) FROM effect_log").fetchone()[0],
        "outbox_rows": conn.execute("SELECT count(*) FROM outbox").fetchone()[0],
        "job_rows": conn.execute("SELECT count(*) FROM job").fetchone()[0],
        "job_profile_columns": _columns(conn, "job_profile"),
    }
    assert before == after


def test_init_schema_stays_idempotent_with_audit_tables(tmp_path):
    """重跑三次不报错——UNIQUE INDEX 与 CHECK 都必须带 IF NOT EXISTS 的幂等性。"""
    conn = _legacy_db(tmp_path)

    init_schema(conn)
    init_schema(conn)
    init_schema(conn)

    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(_AUDIT_TABLES) <= tables


def test_audit_tables_never_enter_the_add_column_path(tmp_path):
    """
    U1 的第二条硬约束本身：三张全新表不走 _ADDED_COLUMNS。加列路径只服务
    "老库缺列"，把新表塞进去会让 apply_column_migrations 对着一张不存在的表
    执行 ALTER TABLE。
    """
    assert {table for table, _column, _ddl in _ADDED_COLUMNS} == {"job_profile"}


def test_add_column_path_is_a_noop_after_audit_schema(tmp_path):
    """三张新表建好之后，加列路径依然一列都不加。"""
    conn = _legacy_db(tmp_path)
    init_schema(conn)

    assert apply_column_migrations(conn) == []


def test_outbox_and_effect_log_columns_are_pinned(tmp_path):
    """
    加固：上面 test_existing_tables_and_rows_are_untouched_by_audit_schema
    跑两次 init_schema 比较前后，测不出"表在本测试里第一次被创建时就已经
    带了新列"这类错法——CREATE TABLE IF NOT EXISTS 对已存在的表是彻底
    no-op，两次都在同一个（已经变异的）起点上比较，天然测不出创建时机的
    问题。这里直接在全新库上钉住 effect_log / outbox 的列集合：任何人往
    SCHEMA 里这两张已有表的定义加列，都会在这里现形。
    """
    conn = get_connection(str(tmp_path / "fresh.db"))
    init_schema(conn)

    assert _columns(conn, "effect_log") == {
        "effect_key",
        "thread_id",
        "node_name",
        "business_key",
        "applied_at",
    }
    assert _columns(conn, "outbox") == {
        "id",
        "thread_id",
        "message_type",
        "payload_json",
        "created_at",
    }
