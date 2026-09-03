"""
合规断言、跨介质对账与拦截统计——**红线被破坏时 CI 直接红**。

⚠️ **本模块全部是只读查询。** ⛔ 不得出现任何 INSERT / UPDATE / DELETE /
commit()：它是审计的观测端，不是写入端。有副作用的动作全部在 L4 编排层的
`effect_*` 节点里（工程铁律 1、2）。reviewer 可以直接 grep 这一条。

⚠️ **"0 命中"不等于"红线守住了"。** 三条断言在空表上全部恒真——同一个绿色
同时兼容"红线守住了"和"断言根本没生效"两种解释。区分这两者的唯一手段是
`tests/test_audit_assertion_effectiveness.py`：故意造违例，断言必须失败。
⛔ 改本模块任何一条判定逻辑时，必须同步确认那份反证仍然会红。

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


# ── 三条一起跑 ──────────────────────────────────────────────────────────

COMPLIANCE_ASSERTIONS: tuple[Callable[[sqlite3.Connection], AssertionResult], ...] = (
    assert_no_ai_score_rejections,
    assert_no_blank_evidence_ref,
    assert_no_unlisted_criterion_key,
)


def run_compliance_assertions(conn: sqlite3.Connection) -> list[AssertionResult]:
    """spec「合规断言在 CI 中执行」：三条全部成立才通过。

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
