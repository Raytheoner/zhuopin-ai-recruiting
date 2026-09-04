"""
合规断言、跨介质对账与拦截统计——**红线被破坏时 CI 直接红**。

⚠️ **本模块全部是只读查询。** ⛔ 不得出现任何 INSERT / UPDATE / DELETE /
commit()：它是审计的观测端，不是写入端。有副作用的动作全部在 L4 编排层的
`effect_*` 节点里（工程铁律 1、2）。reviewer 可以直接 grep 这一条。

⚠️ **"0 命中"不等于"红线守住了"。** 前三条断言在空表上全部恒真——同一个绿色
同时兼容"红线守住了"和"断言根本没生效"两种解释。区分这两者的唯一手段是
`tests/test_audit_assertion_effectiveness.py`：故意造违例，断言必须失败。
⛔ 改本模块任何一条判定逻辑时，必须同步确认那份反证仍然会红。

**断言四是例外**：它在空表上恒真，但**表不存在时判失败**——human_review 由
m1-job-profile-intake 建，缺表就是留痕没上线，不是"还没到能验证的时候"。

**分层**：本模块不 import `app.config`、不 import `app.graph`、不 import
`app.outbound`（`app/audit/__init__.py` 的既有规矩 + 分层方向）。数据库连接与
镜像路径一律由调用方传入。

**反证已实测（2026-09-03，U6 实施）**：把断言一的 `ok=not rows` 改成 `ok=True`、
把断言二的 trim 改成单参、把断言三的白名单里塞进一个本该被拒的 key——三次注入
分别让 `tests/test_audit_assertion_effectiveness.py` 的对应用例变红。这段记录
存在的意义：下一个想"简化"这里的人，能看到简化会撞上哪条测试。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Sequence

from app.audit.criteria import CRITERION_KEY_WHITELIST
from app.audit.events import OUTBOUND_BLOCKED, OUTBOUND_DELIVERED

if TYPE_CHECKING:  # pragma: no cover - 仅供类型标注
    from app.audit.recorder import AuditRecorder
    from app.audit.sinks import AuditSink

# ── M2 才会建的拒绝记录表 ────────────────────────────────────────────────
# ⛔ 本单元不建这张表（delivery-units.md：M1 尚不存在，M2 建表后本断言自动
# 生效）。三个名字提成常量：M2 建表时若与这里不一致，改这三行即可，⛔ 不要
# 把新名字散到查询语句里。取值来自 CLAUDE.md 合规红线的原话：
# 「审计断言：rejection_record 中 reason_type='ai_score' 的记录数恒为 0」。
REJECTION_TABLE = "rejection_record"
REJECTION_REASON_COLUMN = "reason_type"
AI_SCORE_REASON = "ai_score"

# 空 evidence_ref 的判据。**与 app/storage/db.py 的 CHECK 逐字同源**：
# SQLite 的单参 trim() 只剥空格，写成 trim(evidence_ref) 的话一个纯制表符
# 的 evidence_ref 会从断言底下溜过去——那正是 U1 偏离登记 1 堵过的缺口，
# 断言不能比它守护的 CHECK 还弱。
_BLANK_EVIDENCE_SQL = (
    "evidence_ref IS NULL "
    "OR trim(evidence_ref, ' ' || char(9) || char(10) || char(13)) = ''"
)


@dataclass(frozen=True)
class AssertionResult:
    """一条合规断言的结论。

    `violations` 不是可选的装饰：spec「合规断言在 CI 中执行」要求
    "任一条不成立时判定为失败**并指出违例记录**"。ok=False 而
    violations 为空的结果，人拿到手里没法往下查——除非失败原因本身就
    不是"查到了违例行"（如表缺列），那种情况也要造一条描述性的违例项。
    """

    name: str
    ok: bool
    violations: tuple[dict[str, Any], ...] = ()
    detail: str = ""


def _rows(
    conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()
) -> list[dict[str, Any]]:
    """⚠️ 刻意不设 `conn.row_factory`：conn 是全应用共享的一条连接
    （`app/storage/db.py:get_connection`），换掉它会让所有按下标取值的既有
    代码静默改变行为（与 `app/audit/sinks.py:_rows_as_dicts`、
    `app/outbound/queue.py:_row_to_dict` 同一理由）。"""
    cursor = conn.execute(sql, tuple(params))
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, raw)) for raw in cursor.fetchall()]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    # 表名不能参数化，只能拼进 SQL。这里拼的是模块级常量，不是外部输入。
    return {row["name"] for row in _rows(conn, f"PRAGMA table_info({table})")}


# ── 断言一（6.1）：AI 不做自动淘汰 ──────────────────────────────────────

ASSERTION_NO_AI_SCORE_REJECTION = "以 AI 评分为理由的拒绝记录数恒为 0"


def assert_no_ai_score_rejections(conn: sqlite3.Connection) -> AssertionResult:
    """合规红线「AI 只做排序推荐，不做自动淘汰」的机器判据。

    三条分支，**处置各不相同**：
      表不存在        → 通过，但 detail 明说"还没到能验证的时候"
      表存在、缺列    → **失败**（fail-closed：验不了红线不算守住了红线）
      表存在、有违例  → 失败，violations 带上违例行全文
    """
    if not _table_exists(conn, REJECTION_TABLE):
        return AssertionResult(
            name=ASSERTION_NO_AI_SCORE_REJECTION,
            ok=True,
            detail=(
                f"{REJECTION_TABLE} 表尚不存在（M1 现状）。"
                "⚠️ 这个通过**不代表红线守住了**，只代表还没到能验证的时候——"
                "M2 建表后本条自动开始真正生效。"
            ),
        )

    columns = _columns(conn, REJECTION_TABLE)
    if REJECTION_REASON_COLUMN not in columns:
        return AssertionResult(
            name=ASSERTION_NO_AI_SCORE_REJECTION,
            ok=False,
            violations=(
                {
                    "table": REJECTION_TABLE,
                    "missing_column": REJECTION_REASON_COLUMN,
                    "actual_columns": sorted(columns),
                },
            ),
            detail=(
                f"{REJECTION_TABLE} 已建表但没有 {REJECTION_REASON_COLUMN} 列，"
                "本条断言无法验证红线。fail-closed：验不了就算不通过。"
                "⛔ 不要改成跳过——跳过会把「列名改了」静默折成「零违例」。"
            ),
        )

    rows = _rows(
        conn,
        f"SELECT * FROM {REJECTION_TABLE} WHERE {REJECTION_REASON_COLUMN} = ?",
        (AI_SCORE_REASON,),
    )
    return AssertionResult(
        name=ASSERTION_NO_AI_SCORE_REJECTION,
        ok=not rows,
        violations=tuple(rows),
        detail=(
            ""
            if not rows
            else f"发现 {len(rows)} 条以 AI 评分为理由的拒绝记录，违反合规红线"
            "「AI 只做排序推荐，不做自动淘汰」。淘汰必须有人工确认节点并留痕。"
        ),
    )


# ── 断言二（6.2）：evidence_ref 非空 ────────────────────────────────────

ASSERTION_NO_BLANK_EVIDENCE = "criterion_score 中 evidence_ref 为空的记录数恒为 0"


def assert_no_blank_evidence_ref(conn: sqlite3.Connection) -> AssertionResult:
    """工程铁律 4 的纵深防御。

    U1 已经把它做成了数据库 CHECK，本条是 CHECK **之上**的事后断言，不是
    重复劳动：`PRAGMA ignore_check_constraints` 能把 CHECK 整个关掉
    （design.md Risks 段点名），关掉之后只剩这一条守着。
    """
    rows = _rows(
        conn,
        "SELECT id, analysis_run_id, criterion_key, evidence_ref "
        f"FROM criterion_score WHERE {_BLANK_EVIDENCE_SQL}",
    )
    return AssertionResult(
        name=ASSERTION_NO_BLANK_EVIDENCE,
        ok=not rows,
        violations=tuple(rows),
        detail=(
            ""
            if not rows
            else f"发现 {len(rows)} 条证据回指为空的评分项，违反工程铁律 4。"
            "这类记录只可能来自绕过 CHECK 的写入路径（如 "
            "PRAGMA ignore_check_constraints），需要查清写入来源。"
        ),
    )


# ── 断言三（6.3）：criterion_key 白名单 ─────────────────────────────────

ASSERTION_NO_UNLISTED_CRITERION = "criterion_score.criterion_key 不存在白名单外的取值"


def assert_no_unlisted_criterion_key(conn: sqlite3.Connection) -> AssertionResult:
    """合规红线「禁止人脸/表情分析」「声学情绪信号不进 criterion_score」的机器判据。

    白名单是 `app.audit.criteria.CRITERION_KEY_WHITELIST`——**唯一真源**。
    ⛔ 不在这里重列任何一个 key：散成两份就会出现"一处放行一处拒绝"的分叉，
    而分叉的那一侧就是红线的缺口（criteria.py 模块 docstring 的原话）。
    """
    whitelist = sorted(CRITERION_KEY_WHITELIST)
    if not whitelist:
        # 空白名单会让 NOT IN () 变成语法错误，或（在别的方言里）退化成恒真。
        # 白名单被清空本身就是红线事故，直接判失败。
        return AssertionResult(
            name=ASSERTION_NO_UNLISTED_CRITERION,
            ok=False,
            violations=({"error": "CRITERION_KEY_WHITELIST 为空"},),
            detail="白名单为空——未登记即拒绝的闸门已失效。",
        )

    placeholders = ", ".join("?" * len(whitelist))
    rows = _rows(
        conn,
        "SELECT id, analysis_run_id, criterion_key, evidence_ref "
        "FROM criterion_score "
        # criterion_key IS NULL 要单独列：SQL 的 NOT IN 对 NULL 求值为 NULL，
        # 不是 TRUE，一条 NULL 的 key 会从 NOT IN 底下溜过去。DDL 里它是
        # NOT NULL，但绕过约束的写入路径正是本断言存在的理由。
        f"WHERE criterion_key IS NULL OR criterion_key NOT IN ({placeholders})",
        whitelist,
    )
    return AssertionResult(
        name=ASSERTION_NO_UNLISTED_CRITERION,
        ok=not rows,
        violations=tuple(rows),
        detail=(
            ""
            if not rows
            else f"发现 {len(rows)} 条白名单外的评分维度。若涉及语速/停顿/静默"
            "或人脸/表情，这不是漏配，是红线——⛔ 不要把它加进白名单。"
            f"已登记维度：{whitelist}"
        ),
    )


# ── 断言四（9.3）：每次人工决策都有 human_review 记录 ────────────────────
#
# ⚠️ **本条与上面三条有两处刻意的不同，改之前先读完这段。**
#
# ① **表不存在 → 失败**（上面断言一是"通过 + detail 说明"）。rejection_record
#    要守的行为到 M2 才有，human_review 是 m1-job-profile-intake 本包建的表，
#    它不存在只有一个解释：留痕路径没上线。
#
# ② **有一条按时间划的豁免线**。`.51` 上 17 个真实 job 里，已 approved 的画像
#    版本是在本表存在之前确认的，它们不可能有留痕。豁免的**条数必须出现在
#    detail 里**——静默跳过会让"0 违例"这个绿色同时兼容"都留痕了"和"全被
#    豁免了"，而这两者的处置完全相反。
#
#    ⛔ **不要因为巡检报红就把这个日期往后挪。** 豁免线之后仍然缺痕的行是
#    真的缺痕（本单元上线前那几天产生的），正确处置是登记，不是改常量。
HUMAN_REVIEW_TABLE = "human_review"
JOB_PROFILE_TABLE = "job_profile"

# 终态 → 应当留下的决策类型。⛔ 与 app/storage/db.py 的 decision_type CHECK、
# app/graph/nodes.py 的 DECISION_* 常量逐字同源。
TERMINAL_STATUS_DECISIONS: dict[str, str] = {
    "approved": "approved",
    "abandoned": "abandoned",
}

# 终态 → 写下这条决策的 effect_* 节点名。⛔ 与 app/graph/nodes.py 两个
# @idempotent_effect(...) 的字面量参数（:233 effect_confirm_profile、
# :337 effect_abandon_profile）逐字同源，改一处必须同步改另一处——纪律与上面
# TERMINAL_STATUS_DECISIONS ↔ nodes.py 的 DECISION_* 常量（nodes.py:20-24）相同。
# 端到端守卫见 tests/test_audit_assertions.py::test_terminal_status_effect_nodes_match_the_real_effect_nodes
TERMINAL_STATUS_EFFECT_NODES: dict[str, str] = {
    "approved": "effect_confirm_profile",
    "abandoned": "effect_abandon_profile",
}

# 留痕上线日（UTC，与 datetime('now') 同格式）。早于此刻**做出决策**
# （effect_log.applied_at，非画像草案创建时刻）的画像版本豁免。
#
# ⛔ 不要改回拿 job_profile.created_at 比：effect_confirm_profile /
#    effect_abandon_profile 都是就地 UPDATE status，从不推进 created_at。
#    拿 created_at 比，等于让"线前创建、线后确认"的草案永久落在豁免侧——
#    它日后漏写 human_review，断言完全看不见（design.md 决策七）。
HUMAN_REVIEW_ENFORCED_FROM = "2026-09-04 00:00:00"

_REQUIRED_HUMAN_REVIEW_COLUMNS = frozenset(
    {"job_id", "profile_version", "decision_type", "reviewer"}
)

# 「这一版画像进入终态那一刻」的标量子查询。嵌进任何以 p 为 job_profile 别名的
# 查询，带**一个** ? 占位符（node_name），结果为 NULL 表示查不到决策时刻。
#
# 为什么是 effect_log.applied_at：铁律 1 要求业务写与 effect_log 行在同一个事务里
# 提交，所以 applied_at 就是决策真实发生的时刻，不是新造的机制。
#
# 关联 key 与 app/storage/idempotency.py:32 的幂等键
# f"{thread_id}:{node_name}:{business_key}" 同源，这里按三列展开：
#   thread_id   = job_profile.job_id   （app/web/server.py:383、:494）
#   node_name   = TERMINAL_STATUS_EFFECT_NODES[status]
#   business_key= str(version)         （app/web/server.py:384、:495）
#
# ⛔ CAST 不许去掉：business_key 是 TEXT 列、version 是 INTEGER 列，这里是按
#    **数值**比。（诚实说明：列 vs 列的裸比在 SQLite 里会被套上 NUMERIC 亲和性、
#    行为恰好一致，所以删掉 CAST 测试不会红——写 CAST 是为了把"按数值比"这个
#    意图钉在代码里，不依赖读者记得亲和性规则。）
# ⛔ 更不要改写成拼 effect_key 字符串去比
#    （p.job_id || ':' || ? || ':' || p.version）——那是**字符串**相等，
#    business_key 一旦不是规范十进制写法（"02" / " 2" / "2.0"）就静默漏匹配，
#    而漏匹配在 fail-closed 下会把本该豁免的历史行报成违例。反例见
#    tests/test_audit_assertions.py::test_effect_key_string_form_would_miss_what_cast_finds
#
# MIN(...)：effect_key 是主键、正常至多一行；取 MIN 有两个作用——万一有多行时取
# **最早**那次决策（保守方向），以及避免写成 JOIN 把 job_profile 行乘出来。
_DECISION_MOMENT_SQL = (
    "(SELECT MIN(e.applied_at) FROM effect_log e "
    "  WHERE e.thread_id = p.job_id "
    "    AND e.node_name = ? "
    "    AND CAST(e.business_key AS INTEGER) = p.version)"
)

ASSERTION_HUMAN_REVIEW_PRESENT = "每一个进入终态的画像版本都有对应的 human_review 记录"


def assert_every_decision_has_human_review(
    conn: sqlite3.Connection,
) -> AssertionResult:
    """合规红线「淘汰必须有人工确认节点并留痕」+ spec「决策留痕」的机器判据。

    豁免线按**决策发生时刻**（effect_log.applied_at）判定，不是按画像草案
    创建时刻——见 HUMAN_REVIEW_ENFORCED_FROM 上方注释与 design.md 决策七。

    五条分支，处置各不相同：
      表不存在              → **失败**（本包建的表，缺表 = 留痕路径没上线）
      表存在、缺列          → **失败**（fail-closed：验不了红线不算守住了红线）
      查不到决策时刻        → **按未豁免处理**（fail-closed），缺留痕即违例
      决策发生在豁免线之后、无留痕 → 失败，violations 逐条给出 job_id 与 version
      全部有留痕            → 通过，detail 里报出被豁免的历史行条数
    """
    if not _table_exists(conn, HUMAN_REVIEW_TABLE):
        return AssertionResult(
            name=ASSERTION_HUMAN_REVIEW_PRESENT,
            ok=False,
            violations=(
                {"table": HUMAN_REVIEW_TABLE, "problem": "表不存在，人工决策留痕路径没有上线"},
            ),
            detail=(
                f"{HUMAN_REVIEW_TABLE} 表不存在。⛔ 这**不是**“还没到能验证的时候”——"
                "这张表由 m1-job-profile-intake 建，缺表意味着每一次人工确认都没有留痕。"
            ),
        )

    missing = _REQUIRED_HUMAN_REVIEW_COLUMNS - _columns(conn, HUMAN_REVIEW_TABLE)
    if missing:
        return AssertionResult(
            name=ASSERTION_HUMAN_REVIEW_PRESENT,
            ok=False,
            violations=({"table": HUMAN_REVIEW_TABLE, "missing_columns": sorted(missing)},),
            detail=f"{HUMAN_REVIEW_TABLE} 缺列 {sorted(missing)}，无法验证决策留痕。",
        )

    violations: list[dict[str, Any]] = []
    exempted = 0
    # ⛔ 按 sorted 遍历而不是按 dict 顺序：违例清单的顺序要稳定，否则两次巡检
    # 的输出 diff 里会混进无意义的行序变化。
    for status, decision in sorted(TERMINAL_STATUS_DECISIONS.items()):
        node_name = TERMINAL_STATUS_EFFECT_NODES[status]
        rows = _rows(
            conn,
            "SELECT t.job_id, t.version, t.status, t.created_at, t.decided_at FROM ("
            "  SELECT p.job_id AS job_id, p.version AS version, p.status AS status,"
            "         p.created_at AS created_at,"
            f"         {_DECISION_MOMENT_SQL} AS decided_at"
            f"  FROM {JOB_PROFILE_TABLE} p WHERE p.status = ?"
            ") t "
            # decided_at IS NULL = 查不到这一版的决策时刻 → fail-closed，按未豁免处理。
            # ⛔ 不得反过来当成"证明它发生在豁免线之前"（design.md 决策七）。
            "WHERE (t.decided_at IS NULL OR t.decided_at >= ?) AND NOT EXISTS ("
            f"  SELECT 1 FROM {HUMAN_REVIEW_TABLE} h "
            "  WHERE h.job_id = t.job_id AND h.profile_version = t.version "
            "    AND h.decision_type = ?"
            ")",
            (node_name, status, HUMAN_REVIEW_ENFORCED_FROM, decision),
        )
        violations.extend({**row, "expected_decision": decision} for row in rows)

        # 豁免计数走**同一段** _DECISION_MOMENT_SQL：与上面的违例判定共用一套
        # 时间基准，两个数字才不会自相矛盾（Global Constraint 4）。
        # （诚实说明：decided_at IS NOT NULL 删掉不会让任何测试变红——SQLite
        # 里 NULL < '...' 求值为 NULL，WHERE 本就会把这类行滤掉，带不带这个
        # 守卫 COUNT 结果相同。写出来是为了把"NULL 不算豁免"这个意图钉在
        # 代码里，不依赖读者记得 SQL 的三值逻辑。）
        exempted += _rows(
            conn,
            "SELECT COUNT(*) AS n FROM ("
            f"  SELECT {_DECISION_MOMENT_SQL} AS decided_at"
            f"  FROM {JOB_PROFILE_TABLE} p WHERE p.status = ?"
            ") t WHERE t.decided_at IS NOT NULL AND t.decided_at < ?",
            (node_name, status, HUMAN_REVIEW_ENFORCED_FROM),
        )[0]["n"]

    return AssertionResult(
        name=ASSERTION_HUMAN_REVIEW_PRESENT,
        ok=not violations,
        violations=tuple(violations),
        detail=(
            f"豁免 {exempted} 条决策发生在 {HUMAN_REVIEW_ENFORCED_FROM} 之前的"
            "历史画像版本（留痕上线之前确认/放弃的，不可能有记录）。"
            "⚠️ 这些行**不代表红线守住了**，只代表它们产生于留痕存在之前。"
            "⛔ 查不到决策时刻的终态行不在此列——它们按未豁免处理，缺留痕即违例。"
        ),
    )


# ── 四条一起跑 ──────────────────────────────────────────────────────────

COMPLIANCE_ASSERTIONS: tuple[Callable[[sqlite3.Connection], AssertionResult], ...] = (
    assert_no_ai_score_rejections,
    assert_no_blank_evidence_ref,
    assert_no_unlisted_criterion_key,
    assert_every_decision_has_human_review,
)


def run_compliance_assertions(conn: sqlite3.Connection) -> list[AssertionResult]:
    """spec「合规断言在 CI 中执行」：四条全部成立才通过。

    ⚠️ **全部跑完再返回，⛔ 不短路。** 第一条红了就返回的话，一次修复只能
    看到一条违例，第二条要等下一轮 CI 才现形。
    """
    return [assertion(conn) for assertion in COMPLIANCE_ASSERTIONS]


# ── 对账与链校验（6.4）──────────────────────────────────────────────────
#
# ⚠️ 这两条是**不同的断言，不可互相替代**（delivery-units.md §3.4 / §2.U6）：
#
#     chain_assertion()          链自身有没有被改        —— 检不出"少留了一条痕"
#     reconciliation_assertion() 该留的痕都留了没有      —— 检不出"内容被改过"
#
# 两条各有盲区，恰好互补。⛔ 不要因为"都跑一遍太啰嗦"把其中一条删掉。
# ⛔ 也不要去改 verify_chain() 或 app/audit/sinks.py：本模块只是它们的调用方。

ASSERTION_RECONCILED = "SQLite 真身与 JSONL 镜像无未解释的差集"
ASSERTION_CHAIN_INTACT = "JSONL 镜像的哈希链完整"


def reconciliation_assertion(recorder: "AuditRecorder") -> AssertionResult:
    """design D1 的检出手段：按 `analysis_run.id` 比对两侧记录集合。

    两类差集的严重程度不同，但都算违例：

      `unexplained_missing` —— 镜像缺行且链尾没有补录事件。这是崩溃窗口的
        正常残留（允许的单向偏差），但**必须被看见并补录**，不能一直挂着。
      `missing_in_store`    —— 镜像有、真身没有。这是 design D1 明令更糟的
        那一侧（审计能查到一条数据库里不存在的记录），出现即说明有人违反了
        「⛔ 禁止在 effect_* 函数体内 append JSONL」。

    已 backfill 的缺行**不算违例**（`Reconciliation.unexplained_missing` 已
    扣掉）：已知且已登记的东西一直报红，红久了就没人看了。
    """
    report = recorder.reconcile()
    violations: list[dict[str, Any]] = [
        {"kind": "missing_in_mirror", "analysis_run_id": run_id}
        for run_id in sorted(report.unexplained_missing)
    ]
    violations += [
        {"kind": "missing_in_store", "analysis_run_id": run_id}
        for run_id in sorted(report.missing_in_store)
    ]

    detail = ""
    if report.missing_in_store:
        detail = (
            "镜像里存在真身查不到的记录——design D1 明令更糟的偏差方向。"
            "查一遍是不是有人在 effect_* 函数体内 append 了 JSONL。"
        )
    elif report.unexplained_missing:
        detail = (
            "镜像缺行（允许的单向偏差：真身完整、镜像缺证据）。"
            "补齐方式是 AuditRecorder.backfill() 在链尾追加补录事件，"
            "⛔ 不要插回原位——插回必然断链。"
        )
    if report.backfilled:
        detail += f"（已补录 {len(report.backfilled)} 条，不计入违例）"

    return AssertionResult(
        name=ASSERTION_RECONCILED,
        ok=report.ok,
        violations=tuple(violations),
        detail=detail,
    )


def chain_assertion(recorder: "AuditRecorder") -> AssertionResult:
    """spec「留痕不可无痕篡改」：能检出任意一行被删除、插入或修改。

    ⚠️ 已知边界（`verify_chain()` 的 docstring 已声明）：检不出**最后一行**
    被修改——它没有后继来暴露它。这是哈希链的固有性质，不是本条断言的缺陷；
    外部锚定不在本单元范围内。
    """
    verification = recorder.verify_integrity()
    violations: tuple[dict[str, Any], ...] = ()
    if not verification.ok:
        violations = (
            {
                "broken_at": verification.broken_at,
                "total": verification.total,
                "error": verification.error,
            },
        )
    return AssertionResult(
        name=ASSERTION_CHAIN_INTACT,
        ok=verification.ok,
        violations=violations,
        detail=(
            ""
            if verification.ok
            else f"链在第 {verification.broken_at} 行断开（共 {verification.total} 行）："
            f"{verification.error}。⚠️ 断链意味着镜像文件被改过，"
            "先查文件权限与访问记录，⛔ 不要直接重建文件——重建会毁掉唯一的证据。"
        ),
    )


# ── 拦截统计（6.5）──────────────────────────────────────────────────────
#
# fail-closed 误拦的**兜底观测**（design.md Risks 第 2 条）：漏发一封邀约可以
# 补，未审批发出一封拒信不能撤——所以门禁刻意拦得更多。代价是新增消息类型忘
# 登记就会被静默拦下，本统计让"某类消息一直在被拦"可被发现，而不是等业务方
# 投诉。
#
# **数据源是 JSONL 镜像，不是 pending_approval 表。** 三条理由：
#   ① 外发事件在 SqliteSink 里没有真身（SUPPORTED_EVENT_TYPES 只含
#      ai_analysis，app/audit/sinks.py），镜像是它唯一的记录；
#   ② 放行复发被拦时**不入队**（app/outbound/delivery.py 的死锁防线），只查
#      pending_approval 会系统性漏掉这一整类拦截；
#   ③ spec 的原话是"依据**留痕**检索出该类型的拦截次数与拦截原因分布"。

UNKNOWN_MESSAGE_TYPE = "<未知类型>"
UNRECORDED_REASON = "<未记录原因>"


@dataclass(frozen=True)
class OutboundBlockStats:
    """按 `message_type` × 拦截原因的计数，外加一个替人做完减法的字段。

    `always_blocked_types` 才是要人去看的那一列：拦过、且一次都没发出去过的
    类型。把原始计数表摊在运维面前，他得自己做这个减法——那一步就是"发现得
    太晚"的来源。
    """

    blocked_by_type_and_reason: dict[str, dict[str, int]]
    blocked_by_type: dict[str, int]
    blocked_by_reason: dict[str, int]
    delivered_by_type: dict[str, int]
    always_blocked_types: tuple[str, ...]


def outbound_block_stats(mirror: "AuditSink") -> OutboundBlockStats:
    """spec「查询某类消息的拦截情况」。

    ⚠️ 字段缺失的事件 ⛔ 不丢弃，折进 `<未知类型>` / `<未记录原因>` 两个桶：
    被拦下的草稿最常见的原因**正是**这些字段缺失（fail-closed），丢掉它们等
    于让最该被看见的那一类从统计里消失。
    """
    by_type_and_reason: dict[str, dict[str, int]] = {}
    by_type: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    delivered: dict[str, int] = {}

    for record in mirror.read_all():
        event_type = record.get("event_type")
        if event_type not in (OUTBOUND_BLOCKED, OUTBOUND_DELIVERED):
            continue

        message_type = record.get("message_type") or UNKNOWN_MESSAGE_TYPE
        if event_type == OUTBOUND_DELIVERED:
            delivered[message_type] = delivered.get(message_type, 0) + 1
            continue

        reason = record.get("blocked_reason") or UNRECORDED_REASON
        by_type[message_type] = by_type.get(message_type, 0) + 1
        by_reason[reason] = by_reason.get(reason, 0) + 1
        bucket = by_type_and_reason.setdefault(message_type, {})
        bucket[reason] = bucket.get(reason, 0) + 1

    always_blocked = tuple(
        sorted(name for name in by_type if not delivered.get(name))
    )
    return OutboundBlockStats(
        blocked_by_type_and_reason=by_type_and_reason,
        blocked_by_type=by_type,
        blocked_by_reason=by_reason,
        delivered_by_type=delivered,
        always_blocked_types=always_blocked,
    )


# ── CLI 入口（6.6）──────────────────────────────────────────────────────
#
# 用途有两个，⛔ 不要合并理解：
#   ① CI：断言接进 test job，红线破了直接红（见 .github/workflows/ci.yml）。
#      ⚠️ CI 里的库是空的——三条断言在那儿**恒真**，所以 CI 的效力来自
#      tests/test_audit_assertion_effectiveness.py 的反证，不是来自这个 CLI。
#   ② .51 上机巡检：对着真实的 data/demo.db 与 data/audit/decisions.jsonl 跑，
#      这才是断言真正有数据可查的地方。运维口径见 docs/audit-and-outbound-ops.md。
#
# ⛔ 不读 Settings：app/audit 包不 import app.config（__init__.py 的既有规矩），
# 路径一律由参数传入。


def format_report(results: Sequence[AssertionResult]) -> str:
    """人可读的报告。**违例记录逐条列出**，⛔ 不许只报数字——CI 红了却要人
    本地重跑一遍才知道红在哪，等于把排查成本转嫁给下一个人。"""
    lines: list[str] = []
    failed = [r for r in results if not r.ok]

    for result in results:
        mark = "✅" if result.ok else "❌"
        lines.append(f"{mark} {result.name}")
        if result.detail:
            lines.append(f"     {result.detail}")
        for violation in result.violations:
            lines.append(f"     · {violation}")

    lines.append("")
    if failed:
        lines.append(f"合规断言未通过：{len(failed)} / {len(results)} 条不成立。")
    else:
        lines.append(f"合规断言全部通过（{len(results)} 条）。")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """退出码契约：0 全绿 · 1 有违例 · 2 路径不存在。

    ⚠️ **2 不能折成 0。** 一个指错路径的巡检命令若安静地返回 0，读的人会以为
    "三条红线都守住了"，而它一行数据都没查过——比空表恒真更隐蔽的同一种谎。
    """
    import sys
    from pathlib import Path

    # 局部 import：这两个只有 CLI 路径用得到，模块被当库 import 时不该
    # 顺带把 argparse 拖进来。sqlite3 本身模块级已 import，这里不重复引入。
    import argparse

    from app.audit.recorder import AuditRecorder
    from app.audit.sinks import JsonlChainSink, SqliteSink

    parser = argparse.ArgumentParser(
        prog="python -m app.audit.assertions",
        description="合规断言与对账巡检（tasks.md 第 6 章）",
    )
    parser.add_argument("--db", required=True, help="SQLite 库路径，如 data/demo.db")
    parser.add_argument(
        "--mirror", required=True, help="JSONL 镜像路径，如 data/audit/decisions.jsonl"
    )
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    mirror_path = Path(args.mirror)
    for label, path in (("数据库", db_path), ("JSONL 镜像", mirror_path)):
        if not path.exists():
            print(
                f"{label}不存在：{path}。⛔ 巡检未执行——路径错了就是没查过，"
                "不要把这种情况当成通过。",
                file=sys.stderr,
            )
            return 2

    conn = sqlite3.connect(str(db_path))
    try:
        # 镜像 sink 先存局部变量再分别传给两处：⛔ 不要写成
        # outbound_block_stats(recorder._mirror) 去掏私有属性——那是在给
        # AuditRecorder 的内部结构加一个没人知道的调用方。
        mirror_sink = JsonlChainSink(mirror_path)
        recorder = AuditRecorder(store=SqliteSink(conn), mirror_sink=mirror_sink)

        results = run_compliance_assertions(conn)
        results.append(chain_assertion(recorder))
        results.append(reconciliation_assertion(recorder))
        print(format_report(results))

        stats = outbound_block_stats(mirror_sink)
        if stats.always_blocked_types:
            # ⚠️ 只是**提示**，⛔ 不参与退出码：门禁刻意拦得更严，"某类一直
            # 被拦"是需要人去看的信号，不是断言失败。把它算进红绿会让 CI 因
            # 一个正常的观察期结论而长期红着。
            print(
                f"\n⚠️ 以下消息类型拦过、且一次都没发出去过：{stats.always_blocked_types}。"
                "确认是不是新增类型忘了登记（design.md Risks 第 2 条）。"
            )
    finally:
        conn.close()

    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":  # pragma: no cover - 入口薄壳，行为由 main() 覆盖
    raise SystemExit(main())
