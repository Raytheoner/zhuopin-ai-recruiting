"""三条合规断言的**正向**行为：干净库通过、表缺失时的口径、结果结构。

⚠️ 本文件全部是"应该通过"的用例，它**证明不了断言不是恒真**——恒真的断言在
这里也会全绿。反证在 tests/test_audit_assertion_effectiveness.py，两个文件必须
成对存在（tasks.md 6.7 / delivery-units.md §2.U6）。
"""

import sqlite3

import pytest

from app.audit.assertions import (
    AI_SCORE_REASON,
    COMPLIANCE_ASSERTIONS,
    REJECTION_REASON_COLUMN,
    REJECTION_TABLE,
    AssertionResult,
    assert_no_ai_score_rejections,
    assert_no_blank_evidence_ref,
    assert_no_unlisted_criterion_key,
    run_compliance_assertions,
)
from app.audit.criteria import CRITERION_KEY_WHITELIST
from app.storage.db import get_connection, init_schema


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "audit.db"))
    init_schema(c)
    return c


def insert_run(conn: sqlite3.Connection, run_id: str = "run-1") -> str:
    """criterion_score 有外键指向 analysis_run（PRAGMA foreign_keys = ON，
    见 app/storage/db.py:244），所以任何评分项测试都要先有一行父记录。"""
    conn.execute(
        "INSERT INTO analysis_run "
        "(id, configured_model, prompt_version, temperature, input_hash, raw_response) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, "deepseek-chat", "score-v1", 0.0, "sha256:abc", "{}"),
    )
    conn.commit()
    return run_id


def insert_score(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    score_id: str,
    criterion_key: str,
    evidence_ref: str,
    ignore_checks: bool = False,
) -> None:
    """直接写库，**绕过应用层**。

    ignore_checks=True 时先开 PRAGMA ignore_check_constraints——design.md
    Risks 段点名的那条：SQLite 的 CHECK 可以被这个 pragma 关掉，所以
    assertions.py 的事后断言是 CHECK 之上的纵深防御，不是重复劳动。
    """
    if ignore_checks:
        conn.execute("PRAGMA ignore_check_constraints = ON")
    try:
        conn.execute(
            "INSERT INTO criterion_score "
            "(id, analysis_run_id, criterion_key, score, evidence_ref) "
            "VALUES (?, ?, ?, ?, ?)",
            (score_id, run_id, criterion_key, 3.0, evidence_ref),
        )
        conn.commit()
    finally:
        if ignore_checks:
            conn.execute("PRAGMA ignore_check_constraints = OFF")


def create_rejection_table(conn: sqlite3.Connection, *, with_reason_column: bool = True) -> None:
    """模拟 M2 才会建的 rejection_record。

    ⛔ 本单元不在 app/storage/db.py 里建这张表（Global Constraints 3）——
    只有测试里临时建，用来验证"表存在"那条分支。
    """
    if with_reason_column:
        conn.execute(
            f"CREATE TABLE {REJECTION_TABLE} ("
            f"  id TEXT PRIMARY KEY NOT NULL,"
            f"  application_id TEXT,"
            f"  {REJECTION_REASON_COLUMN} TEXT NOT NULL,"
            f"  created_at TEXT NOT NULL DEFAULT (datetime('now'))"
            f")"
        )
    else:
        conn.execute(
            f"CREATE TABLE {REJECTION_TABLE} ("
            f"  id TEXT PRIMARY KEY NOT NULL,"
            f"  application_id TEXT"
            f")"
        )
    conn.commit()


# ── 6.1 ────────────────────────────────────────────────────────────────

def test_ai_score_rejection_assertion_passes_when_table_absent(conn):
    """M1 现状：表还没建。断言通过，但 detail 必须说清"这不是红线守住了"。"""
    result = assert_no_ai_score_rejections(conn)

    assert result.ok is True
    assert result.violations == ()
    # 判据不是"有没有 detail"，是"读的人能不能分辨这两种通过"。
    assert REJECTION_TABLE in result.detail
    assert "尚不存在" in result.detail


def test_ai_score_rejection_assertion_passes_on_clean_table(conn):
    create_rejection_table(conn)
    conn.execute(
        f"INSERT INTO {REJECTION_TABLE} (id, application_id, {REJECTION_REASON_COLUMN}) "
        f"VALUES ('rej-1', 'app-1', 'manual_review')"
    )
    conn.commit()

    result = assert_no_ai_score_rejections(conn)

    assert result.ok is True
    assert result.violations == ()


def test_ai_score_rejection_assertion_fails_when_reason_column_missing(conn):
    """表建了但没有 reason_type 列 → 判失败，⛔ 不判通过。

    验不了红线就不算守住了红线。判通过等于把"M2 建表时列名改了"这件事
    静默折成"零违例"，而这正是本断言要防的那种解释歧义。
    """
    create_rejection_table(conn, with_reason_column=False)

    result = assert_no_ai_score_rejections(conn)

    assert result.ok is False
    assert result.violations != ()
    assert REJECTION_REASON_COLUMN in str(result.violations)


# ── 6.2 ────────────────────────────────────────────────────────────────

def test_blank_evidence_assertion_passes_on_clean_db(conn):
    run_id = insert_run(conn)
    insert_score(
        conn, run_id=run_id, score_id="s-1",
        criterion_key="skill_match", evidence_ref="resume-1#120-180",
    )

    result = assert_no_blank_evidence_ref(conn)

    assert result.ok is True
    assert result.violations == ()


# ── 6.3 ────────────────────────────────────────────────────────────────

def test_unlisted_criterion_assertion_passes_for_every_whitelisted_key(conn):
    """白名单里的**每一个** key 都必须被断言放行。

    参数化成一条遍历全集的用例而不是抽一个代表：将来往
    app/audit/criteria.py 加维度时，这条用例自动覆盖新增的那个，
    不需要有人记得回来补测试。
    """
    run_id = insert_run(conn)
    for index, key in enumerate(sorted(CRITERION_KEY_WHITELIST)):
        insert_score(
            conn, run_id=run_id, score_id=f"s-{index}",
            criterion_key=key, evidence_ref=f"resume-1#{index}-{index + 10}",
        )

    result = assert_no_unlisted_criterion_key(conn)

    assert result.ok is True
    assert result.violations == ()


# ── 结构 ───────────────────────────────────────────────────────────────

def test_run_compliance_assertions_returns_all_three(conn):
    results = run_compliance_assertions(conn)

    assert len(results) == 3
    assert len(COMPLIANCE_ASSERTIONS) == 3
    assert all(isinstance(r, AssertionResult) for r in results)
    # 名字必须两两不同：报告里靠 name 定位是哪条红线破了。
    assert len({r.name for r in results}) == 3
    assert all(r.ok for r in results)


def test_assertion_result_is_frozen():
    """结果对象是事实记录，⛔ 不允许调用方改一下 ok 再往下传。"""
    result = AssertionResult(name="x", ok=True)
    with pytest.raises(Exception):
        result.ok = False  # type: ignore[misc]
