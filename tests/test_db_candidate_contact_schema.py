"""interview-scheduling U1 · Task 4 的 schema 回归（TDD 先写测试）。

覆盖 `candidate_contact` 与 `candidate_contact_access_log` 两张表：
- 新库 `init_schema()` 后两表齐全、列齐、留痕索引在；
- `candidate_contact.application_id` 是主键——一份投递只有一条联系方式记录
  （spec「登记覆盖＝更新同一行」）；
- `phone_enc`/`email_enc` 是 BLOB 密文列，表内**无任何明文列**（部署约束 5
  与 design D6 的存储层落点）——列集合逐字比对即是反证；
- `source` 的 CHECK 只收 'hr_manual'/'candidate_confirmed'（spec 枚举的存储层
  落点），非法值被拒；`registered_by` 非空、`registered_at` 默认 now；
  `purged_at`/`purge_reason` 可空（登记覆盖与删除留痕共用同一行）；
- `candidate_contact_access_log` 有 accessor/application_id/purpose/at 且
  **无内容列**（spec「留痕本身 MUST NOT 含明文」），`at` 默认 now；
- `candidate_contact.application_id` 外键在 `PRAGMA foreign_keys=ON` 下被强制，
  而留痕表的 `application_id` **刻意无外键**（与 `resume_access_log` 同一形态：
  留痕按事件记事实，不把可写性绑在业务表上）。

⛔ 不改动既有表；本文件只测 Task 4 引入的两张新表。
"""
from __future__ import annotations

import sqlite3

import pytest

from app.storage.db import get_connection, init_schema


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _column_types(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {row[1]: row[2] for row in conn.execute(f"PRAGMA table_info({table})")}


def _not_null_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})") if row[3]}


def _primary_key_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [
        row[1]
        for row in sorted(
            (row for row in conn.execute(f"PRAGMA table_info({table})") if row[5]),
            key=lambda r: r[5],
        )
    ]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _index_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)
    ).fetchone()
    return row is not None


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "candidate_contact.db"))
    init_schema(c)
    return c


def _seed_application(conn: sqlite3.Connection, application_id: str = "app1") -> str:
    """建一条可供 `candidate_contact.application_id` 外键引用的投递
    （走既有 job→candidate→resume→application 链）。"""
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式软件工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'sha-1', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, 'c1', 'j1', 'r1', 'initial')",
        (application_id,),
    )
    conn.commit()
    return application_id


def _insert_contact(
    conn: sqlite3.Connection,
    *,
    application_id: str = "app1",
    source: str = "hr_manual",
    registered_by: str = "hr-1",
) -> None:
    conn.execute(
        "INSERT INTO candidate_contact "
        "(application_id, phone_enc, email_enc, registered_by, source) "
        "VALUES (?, X'0102', X'0304', ?, ?)",
        (application_id, registered_by, source),
    )


# ── 建表齐全 ──────────────────────────────────────────────────────────────


def test_candidate_contact_tables_exist(conn):
    assert _table_exists(conn, "candidate_contact")
    assert _table_exists(conn, "candidate_contact_access_log")


def test_candidate_contact_has_expected_columns(conn):
    assert _columns(conn, "candidate_contact") == {
        "application_id",
        "phone_enc",
        "email_enc",
        "registered_by",
        "registered_at",
        "source",
        "purged_at",
        "purge_reason",
    }


def test_candidate_contact_access_log_has_expected_columns(conn):
    assert _columns(conn, "candidate_contact_access_log") == {
        "id",
        "accessor",
        "application_id",
        "purpose",
        "at",
    }


def test_candidate_contact_access_log_index_exists(conn):
    assert _index_exists(conn, "idx_candidate_contact_access_log_application")


# ── 无明文列（部署约束 5 / design D6 的存储层反证）────────────────────────


def test_candidate_contact_has_no_plaintext_phone_or_email_column(conn):
    """⛔ 除 `*_enc` 外不得有任何 phone/email 明文列。"""
    plaintextish = {
        col
        for col in _columns(conn, "candidate_contact")
        if ("phone" in col or "email" in col) and not col.endswith("_enc")
    }
    assert plaintextish == set()


def test_candidate_contact_access_log_has_no_content_column(conn):
    """留痕表只记事实，不得含任何联系方式内容列。"""
    forbidden = {"phone", "email", "phone_enc", "email_enc", "content", "body"}
    assert _columns(conn, "candidate_contact_access_log") & forbidden == set()


def test_enc_columns_are_blob(conn):
    types = _column_types(conn, "candidate_contact")
    assert types["phone_enc"].upper() == "BLOB"
    assert types["email_enc"].upper() == "BLOB"


# ── application_id 唯一：一份投递一条记录 ─────────────────────────────────


def test_candidate_contact_application_id_is_primary_key(conn):
    assert _primary_key_columns(conn, "candidate_contact") == ["application_id"]


def test_candidate_contact_rejects_second_row_for_same_application(conn):
    _seed_application(conn)
    _insert_contact(conn, source="hr_manual")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_contact(conn, source="candidate_confirmed")


# ── source CHECK ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("source", ["hr_manual", "candidate_confirmed"])
def test_candidate_contact_accepts_each_valid_source(conn, source):
    _seed_application(conn)
    _insert_contact(conn, source=source)
    conn.commit()
    assert (
        conn.execute("SELECT source FROM candidate_contact").fetchone()[0] == source
    )


def test_candidate_contact_rejects_invalid_source(conn):
    _seed_application(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_contact(conn, source="guessed")


# ── 非空与默认值 ──────────────────────────────────────────────────────────


def test_candidate_contact_registered_by_is_not_null(conn):
    assert "registered_by" in _not_null_columns(conn, "candidate_contact")
    _seed_application(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO candidate_contact (application_id, registered_by, source) "
            "VALUES ('app1', NULL, 'hr_manual')"
        )


def test_candidate_contact_registered_at_defaults_to_now(conn):
    _seed_application(conn)
    _insert_contact(conn)
    conn.commit()
    registered_at = conn.execute(
        "SELECT registered_at FROM candidate_contact WHERE application_id='app1'"
    ).fetchone()[0]
    assert registered_at


def test_candidate_contact_purge_columns_default_to_null(conn):
    _seed_application(conn)
    _insert_contact(conn)
    conn.commit()
    purged_at, purge_reason = conn.execute(
        "SELECT purged_at, purge_reason FROM candidate_contact WHERE application_id='app1'"
    ).fetchone()
    assert purged_at is None
    assert purge_reason is None


def test_candidate_contact_enc_columns_are_nullable(conn):
    """"HR 只登记了手机、邮件留空"这类登记应可写（两列都可空）。"""
    _seed_application(conn)
    conn.execute(
        "INSERT INTO candidate_contact (application_id, registered_by, source) "
        "VALUES ('app1', 'hr-1', 'hr_manual')"
    )
    conn.commit()


# ── 留痕表非空与默认值 ────────────────────────────────────────────────────


def test_access_log_at_defaults_to_now(conn):
    conn.execute(
        "INSERT INTO candidate_contact_access_log (id, accessor, application_id, purpose) "
        "VALUES ('log-1', 'hr-1', 'app1', 'interview_prep')"
    )
    conn.commit()
    at = conn.execute(
        "SELECT at FROM candidate_contact_access_log WHERE id='log-1'"
    ).fetchone()[0]
    assert at


@pytest.mark.parametrize("column", ["accessor", "application_id", "purpose"])
def test_access_log_key_columns_are_not_null(conn, column):
    assert column in _not_null_columns(conn, "candidate_contact_access_log")


# ── 外键：业务表强制、留痕表刻意不设 ──────────────────────────────────────


def test_candidate_contact_application_fk_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _insert_contact(conn, application_id="no-such-application")


def test_access_log_has_no_application_fk(conn):
    """留痕按事件记事实：`foreign_keys=ON` 下写一条引用了不存在投递的留痕
    也必须成功（否则「留痕写不进去」会变成「读取整个失败」）。"""
    fks = list(conn.execute("PRAGMA foreign_key_list(candidate_contact_access_log)"))
    assert fks == []
    conn.execute(
        "INSERT INTO candidate_contact_access_log (id, accessor, application_id, purpose) "
        "VALUES ('log-2', 'hr-1', 'no-such-application', 'interview_prep')"
    )
    conn.commit()
