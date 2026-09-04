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


def test_exemption_count_follows_the_decision_moment_not_the_draft_time(conn):
    """场景 4：豁免计数与"只豁免真正发生在线前的决策"这条口径一致。

    ⛔ 违例判定与豁免计数必须用**同一套** effect_log 关联。一边 applied_at
    一边 created_at，报出来的数字会自相矛盾——巡检看到"0 违例 / 豁免 2 条"，
    没法判断这个绿色是"都留痕了"还是"都被误豁免了"。

    造数据：两行都在豁免线**之前**创建，但决策时刻一前一后。
      j1  线前创建 · 线前确认 · 无留痕 → 豁免（计入 N）
      j2  线前创建 · 线后确认 · 有留痕 → 不豁免、也不违例（不计入 N）
    旧口径（按 created_at）会把两行都算成豁免，报 "豁免 2 条"。
    """
    from app.audit.assertions import assert_every_decision_has_human_review

    _seed_terminal_profile(
        conn, job_id="j1", status="approved", created_at="2026-08-01 10:00:00"
    )
    _seed_effect_log(conn, job_id="j1", applied_at="2026-08-01 10:00:01")

    _seed_terminal_profile(
        conn, job_id="j2", status="approved", created_at="2026-08-02 10:00:00"
    )
    _seed_effect_log(conn, job_id="j2", applied_at="2026-09-04 09:00:00")
    _seed_review(conn, job_id="j2", decision_type="approved")

    result = assert_every_decision_has_human_review(conn)

    assert result.ok and result.violations == ()
    assert "豁免 1 条" in result.detail, result.detail


# ── Task 3：节点名同源与关联写法反例 ──────────────────────────────────


def test_terminal_status_effect_nodes_match_the_real_effect_nodes(conn):
    """守卫：TERMINAL_STATUS_EFFECT_NODES 的字面量必须与 nodes.py 里两个
    @idempotent_effect(...) 落进 effect_log.node_name 的值一致。

    ⛔ 不许改成 `assert TERMINAL_STATUS_EFFECT_NODES["approved"] ==
       "effect_confirm_profile"` 这种字面量对字面量——那是同义反复，
       nodes.py 那边改了名字它照样绿，而断言四会因为关联恒空把整库判成
       "查不到决策时刻"，巡检变成一片假红，没人能从红里读出真实缺口。

    做法：真的跑一遍那两个 effect_* 节点，回读 effect_log.node_name。
    ⛔ 本用例只 import 与调用 nodes.py，不修改它（本单元只改 assertions.py）。
    """
    from app.audit.assertions import TERMINAL_STATUS_EFFECT_NODES
    from app.graph.nodes import effect_abandon_profile, effect_confirm_profile

    _seed_terminal_profile(conn, job_id="j1", version=1, status="drafting")
    effect_confirm_profile(
        conn,
        thread_id="j1",
        business_key="1",
        profile_dict={},
        reviewer="someone",
    )

    _seed_terminal_profile(conn, job_id="j2", version=1, status="drafting")
    effect_abandon_profile(
        conn,
        thread_id="j2",
        business_key="1",
        reviewer="someone",
    )

    logged = dict(
        conn.execute("SELECT thread_id, node_name FROM effect_log").fetchall()
    )
    assert logged["j1"] == TERMINAL_STATUS_EFFECT_NODES["approved"]
    assert logged["j2"] == TERMINAL_STATUS_EFFECT_NODES["abandoned"]


def test_effect_key_string_form_would_miss_what_cast_finds(conn):
    """特征化：把关联写成拼 effect_key 字符串去比，会漏掉 CAST 能匹配上的行。

    这条钉的是 assertions.py::_DECISION_MOMENT_SQL 里那句 ⛔ 注释背后的事实，
    直接对 SQLite 跑，不经过断言函数：

      业务上 business_key 恒为 str(version)，规范十进制下三种写法都命中；
      一旦不是规范写法（"02" / " 2" / "2.0"），只有**字符串**相等那种写法会漏。
      漏匹配在 fail-closed 下不是"少报"，是把本该豁免的历史行**报成违例**（假红）。

    ⚠️ 诚实说明：列 vs 列的裸比（e.business_key = p.version）在 SQLite 里会被
    套上 NUMERIC 亲和性，行为与 CAST 一致——所以"把 CAST 删掉就会红"这种测试
    是写不出来的，⛔ 不要硬写一条恒绿的同义反复来充数。显式写 CAST 的价值是
    把"这里按数值比"钉在代码里，不依赖读者记得亲和性规则。
    """
    conn.execute("DELETE FROM effect_log")
    conn.execute(
        "INSERT INTO effect_log (effect_key, thread_id, node_name, business_key, applied_at) "
        "VALUES ('j1:effect_confirm_profile:02', 'j1', 'effect_confirm_profile', "
        "'02', '2026-08-01 10:00:01')"
    )
    _seed_terminal_profile(conn, job_id="j1", version=2, status="approved")

    cast_hits = conn.execute(
        "SELECT COUNT(*) FROM job_profile p, effect_log e "
        "WHERE e.thread_id = p.job_id AND CAST(e.business_key AS INTEGER) = p.version"
    ).fetchone()[0]
    string_hits = conn.execute(
        "SELECT COUNT(*) FROM job_profile p, effect_log e "
        "WHERE e.effect_key = p.job_id || ':' || e.node_name || ':' || p.version"
    ).fetchone()[0]

    assert cast_hits == 1
    assert string_hits == 0, (
        "拼 effect_key 字符串去比会漏匹配——⛔ 不要把 _DECISION_MOMENT_SQL 改成这种写法"
    )


def test_non_canonical_business_key_is_still_matched_by_the_assertion(conn):
    """黑盒：business_key 写法不规范时，断言仍按数值匹配上决策时刻并正确豁免。

    这是上一条的行为面：若哪天有人把 _DECISION_MOMENT_SQL 改成拼字符串，
    这一行会匹配不上、落进 fail-closed，被报成违例——本用例当场变红。
    """
    from app.audit.assertions import assert_every_decision_has_human_review

    _seed_terminal_profile(
        conn, job_id="j1", version=2, status="approved", created_at="2026-08-01 10:00:00"
    )
    conn.execute(
        "INSERT INTO effect_log (effect_key, thread_id, node_name, business_key, applied_at) "
        "VALUES ('j1:effect_confirm_profile:0002', 'j1', 'effect_confirm_profile', "
        "'0002', '2026-08-01 10:00:01')"
    )
    conn.commit()

    result = assert_every_decision_has_human_review(conn)

    assert result.ok, result.violations
    assert "豁免 1 条" in result.detail, result.detail


def test_abandoned_terminal_rows_are_exempted_and_counted_with_their_own_effect_node(conn):
    """9.6 全分支 review Important 项：终态循环遍历 approved / abandoned 两个
    status，但在本用例之前，没有任何一条用例造出「abandoned 终态行 + 匹配的
    effect_log」——两条独立变异因此都能在 43 条既有测试下全绿：

      变异 a：`exempted +=` 退化成 `exempted =`
        → 循环第二轮（abandoned）把第一轮（approved）累的计数覆盖掉，
          少报豁免数。本用例造两行（一 approved、一 abandoned）都该被豁免，
          `exempted =` 会把最终计数压成 1，"豁免 2 条" 断言当场落空。

      变异 b：`node_name = TERMINAL_STATUS_EFFECT_NODES[status]`
        写死成 `TERMINAL_STATUS_EFFECT_NODES["approved"]`
        → abandoned 行的决策时刻永远用 effect_confirm_profile 去关联，而
          本用例的 abandoned 行只有 effect_abandon_profile 那条 effect_log，
          关联查不到 → decided_at 为 NULL → 按 fail-closed 落成"未豁免"→
          NOT EXISTS human_review 为真 → 整行判违例，result.ok 变假。

    造数据：两行都在豁免线之前创建、在豁免线之前做出决策，且都不配
    human_review（本就该被豁免，不该有留痕）：
      j1  approved · 线前创建 · 线前 effect_confirm_profile
      j2  abandoned · 线前创建 · 线前 effect_abandon_profile
    """
    from app.audit.assertions import assert_every_decision_has_human_review

    _seed_terminal_profile(
        conn, job_id="j1", status="approved", created_at="2026-08-01 10:00:00"
    )
    _seed_effect_log(
        conn,
        job_id="j1",
        node_name="effect_confirm_profile",
        applied_at="2026-08-01 10:00:01",
    )

    _seed_terminal_profile(
        conn, job_id="j2", status="abandoned", created_at="2026-08-01 10:00:00"
    )
    _seed_effect_log(
        conn,
        job_id="j2",
        node_name="effect_abandon_profile",
        applied_at="2026-08-01 10:00:01",
    )

    result = assert_every_decision_has_human_review(conn)

    assert result.ok, result.violations
    assert "豁免 2 条" in result.detail, result.detail
