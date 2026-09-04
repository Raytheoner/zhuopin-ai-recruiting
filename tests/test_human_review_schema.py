"""`human_review` 建表守卫（tasks 1.4）。

这张表是**合规留痕**的载体：spec「决策留痕」要求"记录决策人标识、决策类型、
决策时间、关联的画像版本"。表上的两条 CHECK 不是装饰——它们是"⛔ 不得把 AI
评分或任何自动判定写成决策人"这条红线在存储层的落点，绕过应用层直接 INSERT
同样被拒。
"""

import json
import sqlite3

import pytest

from app.storage.db import _ADDED_COLUMNS, get_connection, init_schema

# 2026-08-18 及之前 .51 现网 data/demo.db 里 job / job_profile 的真实形态。
# 与 tests/test_db_migration.py 同一份硬编码，理由相同：它代表"服务器上已经
# 存在的那个库长什么样"，是历史事实，⛔ 不能随 SCHEMA 一起演进。
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


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "hr.db"))
    init_schema(c)
    return c


def _insert(conn: sqlite3.Connection, **overrides) -> None:
    row = {
        "id": "job-1-v2-approved",
        "job_id": "job-1",
        "profile_version": 2,
        "decision_type": "approved",
        "reviewer": "unknown:web-session",
        "feedback": None,
        "batch_id": None,
    }
    row.update(overrides)
    conn.execute(
        "INSERT INTO human_review "
        "(id, job_id, profile_version, decision_type, reviewer, feedback, batch_id) "
        "VALUES (:id, :job_id, :profile_version, :decision_type, :reviewer, "
        ":feedback, :batch_id)",
        row,
    )


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_table_has_the_five_columns_the_spec_names(conn):
    """spec「决策留痕」点名的四样 + tasks 1.4 点名的预留列，一个都不能少。"""
    assert {
        "reviewer",
        "decision_type",
        "decided_at",
        "profile_version",
        "batch_id",
    } <= _columns(conn, "human_review")


def test_decided_at_defaults_to_now(conn):
    _insert(conn)
    decided_at = conn.execute("SELECT decided_at FROM human_review").fetchone()[0]
    # 与 job_profile.created_at 同格式（datetime('now')，UTC 秒级、无时区后缀），
    # 两者要能直接比大小——断言四（9.3）就靠这个比法划豁免线。
    assert len(decided_at) == 19 and decided_at[4] == "-" and decided_at[10] == " "


def test_batch_id_is_nullable(conn):
    """M2 批量确认的预留列。现在没有写入方，插入时必须允许留空。"""
    _insert(conn)
    assert conn.execute("SELECT batch_id FROM human_review").fetchone()[0] is None


@pytest.mark.parametrize("bad", ["ai_score", "auto", "", "APPROVED"])
def test_decision_type_check_rejects_anything_outside_the_three_branches(conn, bad):
    """⛔ 合规红线：不得把 AI 评分或任何自动判定写成一次人工决策。

    `ai_score` 这个取值被显式拿来做参数，不是随手举例——它就是
    `rejection_record.reason_type='ai_score'` 那条红线的同一个字面量。
    """
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, decision_type=bad)


@pytest.mark.parametrize("blank", ["", " ", "\t", "\n", " \t\n\r "])
def test_reviewer_check_rejects_blank(conn, blank):
    """决策人为空的留痕等于没留痕。

    trim 的第二参数显式列出空格/制表/换行/回车：SQLite 的单参 trim() 只剥空格，
    只写 trim(reviewer) 的话一个纯制表符的 reviewer 会通过——那就等于这条约束
    有一个静默缺口（与 criterion_score.evidence_ref 的 CHECK 同一形状）。
    """
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, reviewer=blank)


def test_same_decision_on_same_version_cannot_be_recorded_twice(conn):
    """第二道防线（第一道是 idempotent_effect）。

    粒度必须与幂等键 `{job_id}:{node_name}:{version}` 一致——node_name 与
    decision_type 一一对应，所以唯一索引是 (job_id, profile_version,
    decision_type)。两道防线粒度不一致时，宽的那道形同虚设。
    """
    _insert(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, id="another-id")


def test_different_branches_on_the_same_version_coexist(conn):
    """同一版画像可以先被打回、再被确认——两条痕都要留得下。"""
    _insert(conn, id="job-1-v2-revision_requested", decision_type="revision_requested")
    _insert(conn, id="job-1-v2-approved", decision_type="approved")
    assert conn.execute("SELECT COUNT(*) FROM human_review").fetchone()[0] == 2


def test_human_review_is_a_new_table_not_an_added_column(conn):
    """⛔ 新表不走 `_ADDED_COLUMNS`：加列路径只服务"老库缺列"这一种情况。"""
    assert all(table != "human_review" for table, _column, _ddl in _ADDED_COLUMNS)


def test_legacy_db_gets_the_table_and_keeps_every_existing_row(tmp_path):
    """.51 上 data/demo.db 的既有表**一行不改**（opener 约束 3）。"""
    c = get_connection(str(tmp_path / "legacy.db"))
    c.executescript(_LEGACY_JOB_DDL + _LEGACY_JOB_PROFILE_DDL)
    c.execute("INSERT INTO job (id, title, status) VALUES ('old', '采购工程师', 'approved')")
    c.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
        "VALUES ('old-v1', 'old', 1, 'approved', ?)",
        (json.dumps({"job_title": "采购工程师"}, ensure_ascii=False),),
    )
    c.commit()
    before = c.execute("SELECT id, title, status FROM job").fetchall()
    profile_before = c.execute("SELECT id, version, status, profile_json FROM job_profile").fetchall()

    init_schema(c)

    assert "human_review" in {
        row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert c.execute("SELECT id, title, status FROM job").fetchall() == before
    assert (
        c.execute("SELECT id, version, status, profile_json FROM job_profile").fetchall()
        == profile_before
    )
