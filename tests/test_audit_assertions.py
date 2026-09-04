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

pytestmark = pytest.mark.compliance


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

    assert len(results) == 4
    assert len(COMPLIANCE_ASSERTIONS) == 4
    assert all(isinstance(r, AssertionResult) for r in results)
    # 名字必须两两不同：报告里靠 name 定位是哪条红线破了。
    assert len({r.name for r in results}) == 4
    assert all(r.ok for r in results)


def test_assertion_result_is_frozen():
    """结果对象是事实记录，⛔ 不允许调用方改一下 ok 再往下传。"""
    result = AssertionResult(name="x", ok=True)
    with pytest.raises(Exception):
        result.ok = False  # type: ignore[misc]


# ── 9.3：每次人工决策都有 human_review 记录 ─────────────────────────────


def _seed_terminal_profile(conn, job_id="j1", version=1, status="approved", created_at=None):
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES (?, 'x', ?)", (job_id, status)
    )
    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json, created_at) "
        "VALUES (?, ?, ?, ?, '{}', COALESCE(?, datetime('now')))",
        (f"{job_id}-v{version}", job_id, version, status, created_at),
    )
    conn.commit()


def _seed_review(conn, job_id="j1", version=1, decision_type="approved"):
    conn.execute(
        "INSERT INTO human_review (id, job_id, profile_version, decision_type, reviewer) "
        "VALUES (?, ?, ?, ?, 'someone')",
        (f"{job_id}-v{version}-{decision_type}", job_id, version, decision_type),
    )
    conn.commit()


def _seed_effect_log(
    conn,
    job_id="j1",
    version=1,
    node_name="effect_confirm_profile",
    applied_at=None,
):
    """造一条 effect_log 行，代表"这一版画像在 applied_at 那一刻进入了终态"。

    ⛔ effect_key 必须用 f"{thread_id}:{node_name}:{business_key}" 这个格式拼
    （与 app/storage/idempotency.py:32 逐字同源），不要手写字面量——手写的话，
    幂等键格式哪天变了，这些 fixture 会继续绿着，而断言四会在现网静默失效。

    ⛔ business_key 存 str(version)：现网就是 TEXT 列存 str(version)
    （app/web/server.py:384、:495），fixture 存 INTEGER 会把"两列类型不同"
    这个本次要修的关键点从测试里抹掉。
    """
    effect_key = f"{job_id}:{node_name}:{version}"
    conn.execute(
        "INSERT INTO effect_log (effect_key, thread_id, node_name, business_key, applied_at) "
        "VALUES (?, ?, ?, ?, COALESCE(?, datetime('now')))",
        (effect_key, job_id, node_name, str(version), applied_at),
    )
    conn.commit()


def test_human_review_assertion_passes_when_every_decision_left_a_trace(conn):
    from app.audit.assertions import assert_every_decision_has_human_review

    _seed_terminal_profile(conn, status="approved")
    _seed_review(conn, decision_type="approved")
    _seed_terminal_profile(conn, job_id="j2", status="abandoned")
    _seed_review(conn, job_id="j2", decision_type="abandoned")

    result = assert_every_decision_has_human_review(conn)
    assert result.ok and result.violations == ()


def test_human_review_assertion_ignores_drafts(conn):
    """drafting 不是终态，没有人做过决策，当然不该有留痕。"""
    from app.audit.assertions import assert_every_decision_has_human_review

    _seed_terminal_profile(conn, status="drafting")
    assert assert_every_decision_has_human_review(conn).ok


def test_human_review_assertion_exempts_rows_decided_before_the_cutoff(conn):
    """场景 2：草案创建与确认动作**均**发生在豁免线之前 → 豁免，行为与既有一致。

    .51 上的历史行（留痕上线之前确认的）豁免，但**豁免条数必须报出来**。
    ⛔ 静默跳过是不行的：那样"0 违例"这个绿色会同时兼容"都留痕了"和
    "全被豁免了"，而这两者的处置完全相反。

    2026-09-04（tasks 9.6）：本用例原先只造 created_at。豁免线改按决策发生
    时刻判定后，必须同时造出那一刻的 effect_log 行——不造就落进 fail-closed
    分支（"查不到决策时刻 = 未豁免"），那是场景 3 在测的东西。
    """
    from app.audit.assertions import assert_every_decision_has_human_review

    _seed_terminal_profile(conn, status="approved", created_at="2026-08-01 10:00:00")
    _seed_effect_log(
        conn,
        node_name="effect_confirm_profile",
        applied_at="2026-08-01 10:00:01",
    )

    result = assert_every_decision_has_human_review(conn)
    assert result.ok
    assert "1" in result.detail and "豁免" in result.detail
