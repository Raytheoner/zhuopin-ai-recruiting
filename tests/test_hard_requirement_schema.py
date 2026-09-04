"""`hard_requirement` 建表守卫（tasks 1.2b）。

这张表是硬门槛规则草案的载体：spec「硬门槛规则草案提取」要求每条规则包含
字段名、比较运算符、比较值、是否阻断，并附一句人类可读说明。

表上的 CHECK 不是装饰：
- `operator` 的取值集合与 app/agents/hard_requirement.OPERATORS 逐字同源，
  绕过应用层直接 INSERT 一个野运算符同样被拒；
- `human_readable` 非空是 spec「每条规则附一句人类可读的说明（用于将来向
  候选人解释淘汰原因）」在存储层的落点——说明为空的规则等于没有说明，而
  将来要拿它向候选人解释淘汰原因。

⛔ 本表只**存**规则、不**执行**规则（合规红线：AI 只做排序推荐，不做自动淘汰）。
"""

import sqlite3

import pytest

from app.storage.db import _ADDED_COLUMNS, get_connection, init_schema

_EXPECTED_COLUMNS = {
    "job_id",
    "profile_version",
    "field",
    "operator",
    "value",
    "blocking",
    "human_readable",
    "created_at",
}


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "hr.db"))
    init_schema(c)
    yield c
    c.close()


def _columns(c: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in c.execute(f"PRAGMA table_info({table})")}


def _insert(c: sqlite3.Connection, **overrides):
    row = {
        "job_id": "job-1",
        "profile_version": 1,
        "field": "education_requirement",
        "operator": "education_gte",
        "value": "本科",
        "blocking": 1,
        "human_readable": "学历要求：本科及以上（不满足则不通过硬门槛）",
    }
    row.update(overrides)
    c.execute(
        "INSERT INTO hard_requirement "
        "(job_id, profile_version, field, operator, value, blocking, human_readable, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (
            row["job_id"],
            row["profile_version"],
            row["field"],
            row["operator"],
            row["value"],
            row["blocking"],
            row["human_readable"],
        ),
    )


def test_table_exists_with_exactly_the_agreed_columns(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "hard_requirement" in tables
    assert _columns(conn, "hard_requirement") == _EXPECTED_COLUMNS


def test_new_table_never_enters_the_add_column_path(conn):
    """新表不走 _ADDED_COLUMNS：加列路径只服务"老库缺列"这一种情况。"""
    assert "hard_requirement" not in {table for table, _c, _d in _ADDED_COLUMNS}


def test_init_schema_is_idempotent(tmp_path):
    """重跑三次不报错——CREATE TABLE 与主键都必须带 IF NOT EXISTS 的幂等性。"""
    c = get_connection(str(tmp_path / "again.db"))
    init_schema(c)
    init_schema(c)
    init_schema(c)
    tables = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "hard_requirement" in tables
    c.close()


def test_unknown_operator_is_rejected_by_the_database(conn):
    """野运算符绕过应用层直接 INSERT 同样被拒。"""
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, operator="regex_match")


def test_blank_human_readable_is_rejected_by_the_database(conn):
    """空白说明 = 没有说明。纯制表符也要被拒（SQLite 单参 trim() 只剥空格）。"""
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, human_readable="   ")
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, human_readable="\t\n")


def test_blocking_only_accepts_zero_or_one(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, blocking=2)


def test_same_rule_cannot_be_written_twice_for_one_profile_version(conn):
    """复合主键 = 同一版画像内规则去重的第二道防线（第一道是 effect_log）。"""
    _insert(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn)


def test_different_profile_versions_keep_their_own_rules(conn):
    """画像改动走新版本（design 决策四），两版的规则各自成立、互不覆盖。"""
    _insert(conn, profile_version=1, value="本科")
    _insert(conn, profile_version=2, value="硕士")
    rows = conn.execute(
        "SELECT profile_version, value FROM hard_requirement "
        "WHERE job_id = 'job-1' ORDER BY profile_version"
    ).fetchall()
    assert [tuple(r) for r in rows] == [(1, "本科"), (2, "硕士")]
