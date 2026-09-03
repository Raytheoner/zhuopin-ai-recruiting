"""6.7 —— **断言本身有效**的反证。

三条合规断言在空表上全部恒真。"0 命中"这个绿色，同时兼容两种完全相反的解释：

    ① 红线守住了
    ② 断言根本没生效（写成了恒真、查错了表、SQL 拼错了、白名单读的是空集）

本文件是唯一能把这两者分开的东西：**故意造出违例，断言必须失败**。

⛔ 不要把这些用例并进 tests/test_audit_assertions.py。那份文件全是"应该通过"的
用例，这份全是"必须失败"的；混在一起，读的人会在几十个绿色里读丢这几条，而这
几条才是前一份文件的效力证明。

判据（reviewer 逐条查）：每一条断言都有一条对应的"造违例 → ok is False"用例，
且断言失败时 violations 非空（spec：任一条不成立时判定为失败**并指出违例记录**）。
"""

import pytest

from app.audit.assertions import (
    AI_SCORE_REASON,
    REJECTION_REASON_COLUMN,
    REJECTION_TABLE,
    assert_no_ai_score_rejections,
    assert_no_blank_evidence_ref,
    assert_no_unlisted_criterion_key,
    run_compliance_assertions,
)
from app.audit.criteria import CRITERION_KEY_WHITELIST, RED_LINE_EXAMPLES
from app.storage.db import get_connection, init_schema
from tests.test_audit_assertions import (
    create_rejection_table,
    insert_run,
    insert_score,
)

pytestmark = pytest.mark.compliance


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "audit.db"))
    init_schema(c)
    return c


# ── 反证一：AI 评分理由的拒绝记录（6.1 + 合规红线）─────────────────────

def test_ai_score_rejection_is_detected(conn):
    """故意插一条 reason_type='ai_score' 的拒绝记录 → 断言必须失败。

    这条记录代表的现实是"AI 自己把候选人淘汰了"，是本项目最重的一条红线。
    断言在这里不红，红线就没有任何机器守护。
    """
    create_rejection_table(conn)
    conn.execute(
        f"INSERT INTO {REJECTION_TABLE} (id, application_id, {REJECTION_REASON_COLUMN}) "
        f"VALUES ('rej-bad', 'app-9', ?)",
        (AI_SCORE_REASON,),
    )
    conn.commit()

    result = assert_no_ai_score_rejections(conn)

    assert result.ok is False, "插了 ai_score 拒绝记录，断言仍然通过 = 断言恒真"
    assert len(result.violations) == 1
    assert result.violations[0]["id"] == "rej-bad"
    assert result.violations[0][REJECTION_REASON_COLUMN] == AI_SCORE_REASON


def test_ai_score_rejection_assertion_ignores_other_reasons(conn):
    """反向对照：非 ai_score 的拒绝记录**不该**触发这条断言。

    没有这条，一个"表里有任何行就报错"的假实现也能让上一条用例变红——
    上一条就证明不了断言真的在看 reason_type。
    """
    create_rejection_table(conn)
    for row_id, reason in (("r-1", "manual_review"), ("r-2", "candidate_withdrew")):
        conn.execute(
            f"INSERT INTO {REJECTION_TABLE} (id, application_id, {REJECTION_REASON_COLUMN}) "
            f"VALUES (?, 'app-9', ?)",
            (row_id, reason),
        )
    conn.commit()

    assert assert_no_ai_score_rejections(conn).ok is True


# ── 反证二：白名单外的 criterion_key（6.3 + 合规红线）──────────────────

@pytest.mark.parametrize(
    "forbidden_key",
    sorted(RED_LINE_EXAMPLES),
    ids=sorted(RED_LINE_EXAMPLES),
)
def test_red_line_criterion_key_is_detected(conn, forbidden_key):
    """把 criteria.py 里登记的**每一个**红线维度轮流插进库，断言逐个必须失败。

    参数化遍历 RED_LINE_EXAMPLES 而不是抽一个代表：那个常量是本仓库对
    "什么叫红线维度"的现有清单，将来往里加一个（比如新的声学信号），
    这条用例自动覆盖它，不需要有人记得回来补测试。

    ⚠️ 走**直接 INSERT**，绕过 CriterionScore.__post_init__ 的白名单强制——
    应用层那道闸测的是"造不出非法对象"，本条测的是"库里真出现了非法行时
    断言能不能查出来"。两件事，两道防线。
    """
    run_id = insert_run(conn)
    insert_score(
        conn, run_id=run_id, score_id="s-bad",
        criterion_key=forbidden_key, evidence_ref="interview-1#30-45",
    )

    result = assert_no_unlisted_criterion_key(conn)

    assert result.ok is False, f"{forbidden_key} 在库里，断言仍然通过 = 断言恒真"
    assert len(result.violations) == 1
    assert result.violations[0]["criterion_key"] == forbidden_key


def test_unknown_criterion_key_is_detected(conn):
    """不在红线清单里、但也不在白名单里的"没想到的新维度"同样必须被查出来。

    白名单的意义就是对"没想到的那些"默认拒绝。只测 RED_LINE_EXAMPLES 的话，
    一个"黑名单式"的假实现（只查那十个词）也能全绿。
    """
    run_id = insert_run(conn)
    novel_key = "handwriting_style"
    assert novel_key not in CRITERION_KEY_WHITELIST
    assert novel_key not in RED_LINE_EXAMPLES
    insert_score(
        conn, run_id=run_id, score_id="s-novel",
        criterion_key=novel_key, evidence_ref="resume-1#1-9",
    )

    result = assert_no_unlisted_criterion_key(conn)

    assert result.ok is False
    assert result.violations[0]["criterion_key"] == novel_key


# ── 反证三：空 evidence_ref（6.2 + 工程铁律 4）─────────────────────────

@pytest.mark.parametrize(
    ("label", "blank_value"),
    [
        ("empty", ""),
        ("space", " "),
        ("tab", "\t"),
        ("newline", "\n"),
        ("carriage-return", "\r"),
        ("mixed-whitespace", " \t\r\n "),
    ],
)
def test_blank_evidence_is_detected_for_every_whitespace_shape(conn, label, blank_value):
    """六种"看起来是空"的取值全部必须被查出来。

    纯制表符那一条是重点：SQLite 的**单参** trim() 只剥空格，断言若写成
    trim(evidence_ref) 就会漏掉 tab/newline/CR——那正是 U1 偏离登记 1 在
    CHECK 上堵过的缺口，断言不能比它守护的 CHECK 还弱。

    写入用 PRAGMA ignore_check_constraints 绕过 CHECK：design.md Risks 段
    点名这个 pragma 能把 CHECK 关掉，本断言存在的理由就是罩住那种情况。
    """
    run_id = insert_run(conn)
    insert_score(
        conn, run_id=run_id, score_id=f"s-{label}",
        criterion_key="skill_match", evidence_ref=blank_value,
        ignore_checks=True,
    )

    result = assert_no_blank_evidence_ref(conn)

    assert result.ok is False, f"evidence_ref={blank_value!r} 被断言放行 = 铁律 4 有缺口"
    assert len(result.violations) == 1
    assert result.violations[0]["id"] == f"s-{label}"


def test_nonblank_evidence_with_surrounding_whitespace_passes(conn):
    """反向对照：两端带空白但**有实质内容**的证据回指不该被误杀。

    没有这条，一个"只要含空白就报错"的假实现也能让上面六条变红。
    """
    run_id = insert_run(conn)
    insert_score(
        conn, run_id=run_id, score_id="s-ok",
        criterion_key="skill_match", evidence_ref="  resume-1#120-180\n",
    )

    assert assert_no_blank_evidence_ref(conn).ok is True


# ── 汇总：一次跑三条时，红的必须红 ──────────────────────────────────────

def test_run_compliance_assertions_reports_every_broken_line_at_once(conn):
    """三条红线同时被破坏 → 三条断言**全部**红，⛔ 不许在第一条就短路。

    短路会让一次修复只看到一条违例，第二条要等下一轮 CI 才现形。
    """
    create_rejection_table(conn)
    conn.execute(
        f"INSERT INTO {REJECTION_TABLE} (id, application_id, {REJECTION_REASON_COLUMN}) "
        f"VALUES ('rej-bad', 'app-9', ?)",
        (AI_SCORE_REASON,),
    )
    run_id = insert_run(conn)
    insert_score(
        conn, run_id=run_id, score_id="s-face",
        criterion_key="facial_expression", evidence_ref="video-1#0-10",
    )
    insert_score(
        conn, run_id=run_id, score_id="s-blank",
        criterion_key="skill_match", evidence_ref="\t",
        ignore_checks=True,
    )

    results = run_compliance_assertions(conn)

    assert len(results) == 3
    assert [r.ok for r in results] == [False, False, False]
    # spec：任一条不成立时判定为失败**并指出违例记录**。
    assert all(r.violations for r in results)


def test_failing_assertion_always_carries_violations(conn):
    """结构性守护：任何 ok=False 的结果都必须带 violations。

    ok=False 而 violations 为空的结果，人拿到手里没法往下查——CI 红了却
    不知道红在哪一行，等价于没有断言。
    """
    create_rejection_table(conn, with_reason_column=False)
    results = run_compliance_assertions(conn)

    for result in results:
        if not result.ok:
            assert result.violations, f"{result.name} 失败但没有指出违例记录"
