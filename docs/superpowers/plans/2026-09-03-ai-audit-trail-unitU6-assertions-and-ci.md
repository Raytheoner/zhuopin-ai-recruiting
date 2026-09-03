# U6 · 合规断言、对账与 CI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让「AI 不做自动淘汰」「评分必须带证据」「评分维度不越白名单」这三条合规红线，从写在文档里的约定变成 CI 里跑得起来、破了就红、且**被证明过确实会红**的机器断言，并补上跨介质对账与拦截统计两条观测手段。

**Architecture:** 新增单文件模块 `app/audit/assertions.py`，对外是一组**纯查询函数**——每个函数吃一条 `sqlite3.Connection`（或一个 `AuditRecorder`），吐一个 `AssertionResult`，不写库、不发消息、不改任何既有模块。三条断言直接查 U1 的 `criterion_score` 与（M2 才建的）`rejection_record`；对账与链校验各自薄封装 U2 已落地的 `AuditRecorder.reconcile()` / `verify_integrity()`；拦截统计读 U5 写下的 JSONL 镜像。模块另带一个 `main(argv) -> int` 的 CLI 入口供 `.51` 上机巡检，CI 侧只在既有 `test` job 里加一个可归因的步骤，**不另起一套 CI**。

**Tech Stack:** Python 3.14（`requires-python = ">=3.14,<3.15"`）· 标准库 `sqlite3` / `argparse` / `dataclasses` · pytest · GitHub Actions（既有 `.github/workflows/ci.yml`，`test` job 跑在 windows-latest）

**范围：** `openspec/changes/ai-audit-trail-and-outbound-gate/tasks.md` 第 6 章 6.1–6.7 共 7 项。
**输入（真源）：** `specs/ai-decision-audit/spec.md`「留痕可查询与合规断言」「评分项白名单约束」「逐项评分必须带证据回指」；`specs/outbound-approval-gate/spec.md`「外发与拦截动作强制留痕」的第三个场景；`design.md` D1 / D3 与 Risks 段。
**依赖：** U1（三张表）、U2（`app/audit/`）、U3（`analysis_run` 有真实数据）、U5（拦截留痕有真实数据）—— 均已合入 main。

---

## Global Constraints

**每一条都是 reviewer 的注意力透镜。违反其中任何一条即判 reject，不进入下一个 Task。**

### 一、本单元专属（违反即重写）

1. **6.7 是本单元的价值所在，⛔ 不许写成"可选"**：三条断言在空表上全部恒真，"0 命中"同时兼容"红线守住了"和"断言没生效"两种解释。计划里 6.7 必须是独立 Task，判据＝故意插入一条 `reason_type='ai_score'` 的拒绝记录 / 一条白名单外的 `criterion_key` → 对应断言**必须失败**；不失败 ＝ 断言写成了恒真 ＝ 重写。

2. **6.4 对账查询与 U2 的 `verify_chain()` 是两条不同的断言，不可互相替代**：`verify_chain()` 只证"链没被改"，证不了"该留的痕都留了"。两条各有独立测试。⛔ 不改 `verify_chain()`、⛔ 不改 `app/audit/sinks.py`。

3. **6.1 的 `rejection_record` 表在 M1 尚不存在**：断言实现为「表不存在即通过、表存在则计数必须为 0」，M2 建表后自动生效。⛔ 不在本单元建这张表。

4. **`CANDIDATE_OUTBOUND_ENABLED` 默认关闭是不可代项，本单元只读、⛔ 不改开关读取代码。** 即 ⛔ 不改 `app/config.py` 的 `is_candidate_outbound_enabled()` / `_evaluate_candidate_outbound_switch()`，⛔ 不改 `app/outbound/delivery.py`。

### 二、跨单元接口约定（`delivery-units.md` §4，逐字抄录）

5. **`AuditRecorder` 是两段式 API**：写 SQLite（进事务）与 append JSONL（提交后）分开。⛔ 禁止在任何 `effect_*` 函数体内 append JSONL。理由见 §3.4。

6. **本包三条硬边界**（全部单元）：不新增 `zhuopin_platform` 依赖、不跨仓库 import、不拷贝参考文件。U7 的 7.1/7.2 把它变成 CI 可查。

7. **每个单元开工前必须 rebase 到最新 main**——本包与 `m1-intake-quality-fixes` 同期在跑，`app/graph/nodes.py` 是两批共同的最热文件。

### 三、工程铁律（`CLAUDE.md`，逐字复制）

8. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。

9. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。

10. **所有 AI 评分必须持久化**：模型标识 + 模型版本 + prompt 版本 + temperature + 输入哈希 + rubric 快照 + 原始响应。

11. **每条 `criterion_score` 必须有 `evidence_ref`**（回指简历原文或面试 turn 的 offset）。`evidence_ref` 为空不允许写入。

12. **`temperature=0`；模型版本优先显式锁定**，禁止 `latest` 类别名。

> **本单元对铁律 1/2 的落点**：`assertions.py` 全部是**只读查询**，⛔ 不得出现任何 `INSERT` / `UPDATE` / `DELETE` / `commit()`。因此本单元**不新增任何 `effect_*` 节点**，铁律 1 的幂等键要求在本单元没有落点——这不是豁免，是"没有副作用所以没有幂等问题"。reviewer 判据可直接 grep：`app/audit/assertions.py` 里出现任一写语句即 reject。

### 四、合规红线（`CLAUDE.md`，逐字复制）

13. **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。审计断言：`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。

14. **禁止人脸/表情分析**（《人脸识别技术应用安全管理办法》2025-06-01 施行）。声学情绪信号（语速/停顿/静默）只展示给面试官，不进 `criterion_score`。

15. **绝不用历史录用结果做监督信号**（Amazon 2018 教训），只用显式岗位能力 rubric。

### 五、单一真源（违反即分叉，分叉的那一侧就是红线的缺口）

16. **白名单只有一份**：`criterion_key` 的合法集合是 `app.audit.criteria.CRITERION_KEY_WHITELIST`。⛔ 本单元不得在 `assertions.py`、测试或 CI 脚本里重新列举任何一个 key，必须 `import` 那个常量。

17. **空白判定与 DDL 逐字同源**：6.2 的空 `evidence_ref` 判据必须与 `app/storage/db.py` 里那条 `CHECK` 用**同一个 trim 字符集** `' ' || char(9) || char(10) || char(13)`。⛔ 不许写成单参 `trim()`——SQLite 单参 `trim()` 只剥空格，一个纯制表符的 `evidence_ref` 会从断言底下溜过去，而它恰恰是 U1 偏离登记 1 已经堵过的那个缺口。

18. **拦截原因的封闭集合在 `app.outbound.gate.ALL_BLOCK_REASONS`**，⛔ 本单元不重列。但 `assertions.py` **模块内 ⛔ 不 import `app.outbound`**（分层：audit 是下层，outbound 是上层，反向依赖会把 `app.agents.jd_agent` 拖进审计路径）；需要用到那个常量的地方只在 `tests/` 里 import。

### 六、层次与文件边界

19. **本单元只允许触碰这三处**：`app/audit/assertions.py`（新建）、`tests/`（新建文件）、`.github/workflows/ci.yml` + `pyproject.toml`（各加几行）。⛔ 不改 `app/audit/__init__.py`（保持 U2 交付面不动，调用方直接 `from app.audit.assertions import ...`）、⛔ 不改 `app/audit/sinks.py` / `recorder.py` / `criteria.py` / `events.py`、⛔ 不改 `app/outbound/*`、⛔ 不改 `app/storage/db.py`。

20. **`app/audit` 包不 import `app.config` 与 `app.graph`**（U2 的 `__init__.py` docstring 已立此规矩）：路径与连接一律由调用方传入。`assertions.py` 的 CLI 入口也不例外——数据库路径与镜像路径走 `argparse` 参数，⛔ 不去读 `Settings`。

---

## File Structure

| 文件 | 状态 | 职责 |
|---|---|---|
| `app/audit/assertions.py` | **新建** | 全部对外 API：`AssertionResult` / 三条断言 / 对账断言 / 链断言 / 拦截统计 / `main()` CLI |
| `tests/test_audit_assertions.py` | **新建** | 三条断言的**正向**行为（干净库通过、表缺失的口径、结果结构） |
| `tests/test_audit_assertion_effectiveness.py` | **新建** | **6.7 反证**：故意造违例，断言必须失败。这是本单元唯一能证明前一份测试不是恒真的文件 |
| `tests/test_audit_reconciliation.py` | **新建** | 6.4：对账与链校验各自独立、且**互相不可替代**（两个方向都测） |
| `tests/test_outbound_block_stats.py` | **新建** | 6.5：按 `message_type` × 拦截原因统计 |
| `tests/test_compliance_cli.py` | **新建** | 6.6：CLI 退出码——全绿 0、有违例 1、库不存在 2 |
| `pyproject.toml` | 改 3 行 | 注册 `compliance` marker |
| `.github/workflows/ci.yml` | 改 4 行 | 在既有 `test` job 里加一个可归因的合规断言步骤 |

**为什么断言测试拆成两个文件而不是一个**：`test_audit_assertions.py` 全部是"应该通过"的用例，`test_audit_assertion_effectiveness.py` 全部是"必须失败"的用例。放一个文件里，后者会在几十个前者中间被读丢；而 6.7 的全部意义就是让人一眼看见"这些断言被证伪过"。文件名本身就是那份证据的索引。

---

## 已知的落地口径（实现前先读，避免走回头路）

- **`rejection_record` 的列名**：M1 没有这张表，M2 才建。本单元按 `CLAUDE.md` 合规红线的原话锁定两个名字：表名 `rejection_record`、列名 `reason_type`、违例取值 `'ai_score'`。三者都提成模块级常量，M2 建表时若改名，改常量一行即可。
- **表存在但没有 `reason_type` 列 → 判 fail，不是判 pass。** 这是刻意的 fail-closed：验不了红线就不算守住了红线。这条有测试。
- **6.5 的数据源是 JSONL 镜像，不是 `pending_approval` 表。** `app/outbound/gate.py:50` 那句注释写的是「U6 的 6.5 直接 GROUP BY 这一列（`pending_approval.blocked_reason`）」——**本计划有意不按那句走**，理由三条：① 外发事件在 `SqliteSink` 里**没有真身**（`SUPPORTED_EVENT_TYPES` 只含 `ai_analysis`，见 `app/audit/sinks.py:85`），JSONL 是它唯一的记录；② 放行复发路径被拦时**不入队**（`app/outbound/delivery.py:93-96` 的死锁防线），只查 `pending_approval` 会系统性漏掉这一整类拦截；③ spec 的原话是"依据**留痕**检索"，留痕就是镜像。**这条偏离要登记进 tasks.md 的落地偏离节**，见 Task 5 Step 7。⛔ 本单元不去改 `gate.py` 那句注释（越界改 U4 的文件），只登记。

---

### Task 1: 三条合规断言与 `AssertionResult`（6.1 / 6.2 / 6.3）

**Files:**
- Create: `app/audit/assertions.py`
- Test: `tests/test_audit_assertions.py`

**Interfaces:**
- Consumes: `app.audit.criteria.CRITERION_KEY_WHITELIST`（frozenset[str]，唯一真源）；`app.storage.db.get_connection` / `init_schema`（仅测试用）
- Produces:
  - `AssertionResult(name: str, ok: bool, violations: tuple[dict[str, Any], ...] = (), detail: str = "")` —— frozen dataclass
  - `assert_no_ai_score_rejections(conn: sqlite3.Connection) -> AssertionResult`
  - `assert_no_blank_evidence_ref(conn: sqlite3.Connection) -> AssertionResult`
  - `assert_no_unlisted_criterion_key(conn: sqlite3.Connection) -> AssertionResult`
  - `COMPLIANCE_ASSERTIONS: tuple[Callable[[sqlite3.Connection], AssertionResult], ...]`
  - `run_compliance_assertions(conn: sqlite3.Connection) -> list[AssertionResult]`
  - `REJECTION_TABLE = "rejection_record"` / `REJECTION_REASON_COLUMN = "reason_type"` / `AI_SCORE_REASON = "ai_score"`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_audit_assertions.py`：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_audit_assertions.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.audit.assertions'`（收集阶段就报，7 条用例全部 error）

- [ ] **Step 3: 写最小实现**

创建 `app/audit/assertions.py`：

```python
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
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from app.audit.criteria import CRITERION_KEY_WHITELIST

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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_audit_assertions.py -q`
Expected: PASS —— 7 passed

- [ ] **Step 5: 跑全量回归**

Run: `python -m pytest -q`
Expected: 全绿，通过数 = 合并前基线 + 7。⛔ 本 Task 不改任何既有模块，既有用例一条都不该变红；变红就说明碰了不该碰的文件。

- [ ] **Step 6: 提交**

```bash
git add app/audit/assertions.py tests/test_audit_assertions.py
git commit -m "feat(audit): U6 三条合规断言（6.1/6.2/6.3）"
```

---

### Task 2: 断言有效性反证（6.7）

> **这个 Task 是本单元的价值所在，⛔ 不是可选的。** Task 1 的十条用例在一个恒
> 返回 `ok=True` 的假实现下同样全绿——那份测试证明不了任何东西。本 Task 存在的
> 唯一目的，是让"三条断言确实会因为违例而变红"这件事本身被一条会红的测试咬住。
> 判据：把 `assertions.py` 里任意一条断言的 `ok=not rows` 改成 `ok=True`，本文件
> 必须至少有一条用例失败。实现完成后**必须实际做一遍这个改动验证**（Step 5）。

**Files:**
- Test: `tests/test_audit_assertion_effectiveness.py`（新建）
- Modify: 不改任何生产代码。若本 Task 发现断言写成了恒真，回到 Task 1 的文件修，⛔ 不在测试里迁就

**Interfaces:**
- Consumes: Task 1 产出的 `assert_no_ai_score_rejections` / `assert_no_blank_evidence_ref` / `assert_no_unlisted_criterion_key` / `run_compliance_assertions` / `AssertionResult`；`app.audit.criteria.RED_LINE_EXAMPLES`
- Produces: 无生产代码。产出的是"断言非恒真"这一事实的机器证据

- [ ] **Step 1: 写失败测试**

创建 `tests/test_audit_assertion_effectiveness.py`：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_audit_assertion_effectiveness.py -q`
Expected: FAIL —— `ImportError: cannot import name 'create_rejection_table' from 'tests.test_audit_assertions'`（若 Task 1 尚未合入）；Task 1 已合入时应当**全部通过**。

⚠️ 这个 Task 的 TDD 形状与别处不同：被测代码在 Task 1 就写完了，本 Task 写的是**证伪用例**。所以"先看到红"的正确做法是 Step 5 的注入式验证，⛔ 不是先让实现缺失。

- [ ] **Step 3: 跑测试确认通过**

Run: `python -m pytest tests/test_audit_assertion_effectiveness.py -q`
Expected: PASS —— 22 passed（10 个红线维度 + 1 未知维度 + 6 个空白形状 + 1 反向对照 + 2 拒绝记录 + 2 汇总）

若有失败：**回 Task 1 改 `assertions.py`，⛔ 不许改本文件去迁就实现。** 本文件的每一条都是 spec 直接落下来的判据。

- [ ] **Step 4: 修 `tests/test_audit_assertions.py` 的可复用性**

本 Task 从 `tests/test_audit_assertions.py` import 了三个 helper。确认它们是模块级函数（不是 fixture、不带下划线前缀），否则 import 会失败或触发 pytest 的 fixture 收集告警。若 Task 1 写成了 fixture，现在把它们改成普通函数，Task 1 的用例改为直接调用。

Run: `python -m pytest tests/test_audit_assertions.py tests/test_audit_assertion_effectiveness.py -q`
Expected: PASS —— 29 passed（7 + 22），无 warning

- [ ] **Step 5: 注入式验证（本 Task 的验收判据，⛔ 不许跳过）**

依次做三次**临时**改动，每次确认对应的反证会红，然后**立刻还原**：

```bash
# ① 把断言一改成恒真
python - <<'PY'
import pathlib
p = pathlib.Path("app/audit/assertions.py")
s = p.read_text(encoding="utf-8")
p.write_text(s.replace(
    'name=ASSERTION_NO_AI_SCORE_REJECTION,\n        ok=not rows,',
    'name=ASSERTION_NO_AI_SCORE_REJECTION,\n        ok=True,', 1), encoding="utf-8")
PY
python -m pytest tests/test_audit_assertion_effectiveness.py -q
# 期望：FAILED test_ai_score_rejection_is_detected（以及汇总那两条）
git checkout app/audit/assertions.py

# ② 把断言二的 trim 改成单参（模拟"看起来等价"的简化）
python - <<'PY'
import pathlib
p = pathlib.Path("app/audit/assertions.py")
s = p.read_text(encoding="utf-8")
p.write_text(s.replace(
    "trim(evidence_ref, ' ' || char(9) || char(10) || char(13)) = ''",
    "trim(evidence_ref) = ''", 1), encoding="utf-8")
PY
python -m pytest tests/test_audit_assertion_effectiveness.py -q
# 期望：FAILED ...[tab] / [newline] / [carriage-return] / [mixed-whitespace]
git checkout app/audit/assertions.py

# ③ 把断言三的白名单换成"黑名单式"判定
python - <<'PY'
import pathlib
p = pathlib.Path("app/audit/assertions.py")
s = p.read_text(encoding="utf-8")
p.write_text(s.replace(
    "from app.audit.criteria import CRITERION_KEY_WHITELIST",
    "from app.audit.criteria import CRITERION_KEY_WHITELIST, RED_LINE_EXAMPLES", 1
).replace(
    "whitelist = sorted(CRITERION_KEY_WHITELIST)",
    "whitelist = sorted(set(CRITERION_KEY_WHITELIST) | {'handwriting_style'})", 1),
    encoding="utf-8")
PY
python -m pytest tests/test_audit_assertion_effectiveness.py -q
# 期望：FAILED test_unknown_criterion_key_is_detected
git checkout app/audit/assertions.py
```

三次都必须看到对应的 FAILED。**任何一次是全绿，说明那条断言在这个维度上是恒真的，回 Task 1 重写。**

还原后确认工作区干净：

Run: `git status --short app/audit/assertions.py`
Expected: 无输出

- [ ] **Step 6: 把这三次注入的结论写进模块 docstring**

在 `app/audit/assertions.py` 的模块 docstring 末尾追加一段（⛔ 不改任何代码逻辑）：

```python
# ── 在模块 docstring 的最后一段之后追加 ──
"""
...（既有内容保持不变）...

**反证已实测（2026-09-03，U6 实施）**：把断言一的 `ok=not rows` 改成 `ok=True`、
把断言二的 trim 改成单参、把断言三的白名单里塞进一个本该被拒的 key——三次注入
分别让 `tests/test_audit_assertion_effectiveness.py` 的对应用例变红。这段记录
存在的意义：下一个想"简化"这里的人，能看到简化会撞上哪条测试。
"""
```

- [ ] **Step 7: 跑全量回归**

Run: `python -m pytest -q`
Expected: 全绿，通过数 = Task 1 之后 + 22

- [ ] **Step 8: 提交**

```bash
git add tests/test_audit_assertion_effectiveness.py app/audit/assertions.py
git commit -m "test(audit): U6 6.7 断言有效性反证——三次注入实测断言非恒真"
```

---

### Task 3: 对账断言与链断言（6.4）

> **6.4 与 U2 的 `verify_chain()` 是两条不同的断言，不可互相替代。** `verify_chain()`
> 只证"链没被改"，证不了"该留的痕都留了"。本 Task 的验收判据不是"两条都能跑"，
> 而是**两个方向的反例各有一条用例**：链完好但镜像缺行（对账红、链绿）、镜像齐全
> 但被篡改（链红、对账绿）。缺任一方向，"不可互相替代"这句话就没有机器证据。
> ⛔ 不改 `verify_chain()`、⛔ 不改 `app/audit/sinks.py`。

**Files:**
- Modify: `app/audit/assertions.py`（追加两个函数，⛔ 不动 Task 1 已有的任何一行）
- Test: `tests/test_audit_reconciliation.py`（新建）

**Interfaces:**
- Consumes: `app.audit.recorder.AuditRecorder`（`reconcile() -> Reconciliation`、`verify_integrity() -> ChainVerification`、`backfill(missing_id, *, reason)`）；`app.audit.sinks.SqliteSink` / `JsonlChainSink`（仅测试构造用）
- Produces:
  - `reconciliation_assertion(recorder: AuditRecorder) -> AssertionResult`
  - `chain_assertion(recorder: AuditRecorder) -> AssertionResult`
  - `ASSERTION_RECONCILED = "SQLite 真身与 JSONL 镜像无未解释的差集"`
  - `ASSERTION_CHAIN_INTACT = "JSONL 镜像的哈希链完整"`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_audit_reconciliation.py`：

```python
"""6.4 —— 跨介质对账，以及它与 verify_chain() 的**不可互相替代性**。

verify_chain() 回答的是"链自身有没有被改"；reconcile() 回答的是"该留的痕都
留了没有"。两个问题，两条断言。本文件的核心是那两条**交叉反例**：

    链完好 + 镜像缺行  →  chain_assertion 绿、reconciliation_assertion 红
    镜像齐全 + 被篡改  →  chain_assertion 红、reconciliation_assertion 绿

任何一个方向缺了用例，"不可互相替代"就只是一句注释。
"""

import json

import pytest

from app.audit.assertions import chain_assertion, reconciliation_assertion
from app.audit.events import AI_ANALYSIS, CriterionScore, DecisionEvent
from app.audit.recorder import AuditRecorder
from app.audit.sinks import JsonlChainSink, SqliteSink
from app.storage.db import get_connection, init_schema


@pytest.fixture(autouse=True)
def _clear_chain_class_state():
    """JsonlChainSink 的锁与游标是**类级、按绝对路径共享**的
    （app/audit/sinks.py:271-273）。不清掉，上一条用例的游标会跟着进下一条，
    新文件的第一行拿到一个来自别的文件的 prev_hash，链从那行起永久断裂。
    tests/test_audit_recorder.py 已有同形状的 fixture，此处照同一做法。"""
    yield
    JsonlChainSink._CURSORS.clear()
    JsonlChainSink._LOCKS.clear()


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "audit.db"))
    init_schema(c)
    return c


@pytest.fixture
def chain_path(tmp_path):
    return tmp_path / "audit" / "decisions.jsonl"


@pytest.fixture
def recorder(conn, chain_path):
    return AuditRecorder(store=SqliteSink(conn), mirror_sink=JsonlChainSink(chain_path))


def make_event(run_id: str) -> DecisionEvent:
    return DecisionEvent(
        id=run_id,
        event_type=AI_ANALYSIS,
        thread_id="thread-1",
        application_id="app-1",
        configured_model="deepseek-chat",
        response_model="deepseek-chat",
        prompt_version="score-v1",
        temperature=0.0,
        input_hash=f"sha256:{run_id}",
        raw_response="{}",
        scores=(CriterionScore("skill_match", 3.0, "resume-1#120-180"),),
    )


def record_both(recorder: AuditRecorder, conn, run_id: str) -> DecisionEvent:
    """正常路径：先真身（进事务、提交），再镜像（提交之后）。

    顺序不能反——design D1：允许的偏差只有单向「SQLite 有、JSONL 缺行」。
    """
    event = make_event(run_id)
    recorder.record(conn, event)
    conn.commit()
    recorder.mirror(event)
    return event


# ── 正常态：两条断言都绿 ────────────────────────────────────────────────

def test_both_assertions_pass_on_a_consistent_pair(recorder, conn):
    for run_id in ("run-1", "run-2", "run-3"):
        record_both(recorder, conn, run_id)

    reconciled = reconciliation_assertion(recorder)
    chained = chain_assertion(recorder)

    assert reconciled.ok is True
    assert reconciled.violations == ()
    assert chained.ok is True
    assert chained.violations == ()


def test_both_assertions_pass_on_an_empty_pair(recorder):
    """空库空文件：两条都通过。

    ⚠️ 这个绿色**不代表系统在正常留痕**——它和"什么都没发生过"是同一个
    颜色。真正的效力证据在下面那两条交叉反例，这条只是基线。
    """
    assert reconciliation_assertion(recorder).ok is True
    assert chain_assertion(recorder).ok is True


# ── 交叉反例一：链完好、镜像缺行 → 对账红，链绿 ────────────────────────

def test_missing_mirror_row_is_caught_by_reconcile_but_not_by_chain(recorder, conn):
    """崩溃窗口的真实形状：SQLite 写了、进程死在 append 之前。

    这时候链一点问题都没有（少写的那一行从来没进过链），verify_chain()
    永远是绿的。只有对账能发现"该留的痕少了一条"。
    这条用例就是 delivery-units.md §3.4「两条不可互相替代」的机器证据。
    """
    record_both(recorder, conn, "run-1")
    # run-2 只写真身，不写镜像——模拟两段之间崩溃
    orphan = make_event("run-2")
    recorder.record(conn, orphan)
    conn.commit()

    chained = chain_assertion(recorder)
    reconciled = reconciliation_assertion(recorder)

    assert chained.ok is True, "链本身没被改，verify_chain 不该红——它看不见这类问题"
    assert reconciled.ok is False, "镜像缺了一行，对账必须红"
    assert any("run-2" in str(v) for v in reconciled.violations)


def test_backfilled_missing_row_stops_being_reported(recorder, conn):
    """链尾补录之后，那条缺行不再算违例（Reconciliation.unexplained_missing）。

    已知且已登记的缺行一直算成违例，这条断言就会长期红着——红久了就没人
    看了，等于没有断言。补录走链尾 type=backfill，⛔ 不插回原位（插回必然断链）。
    """
    orphan = make_event("run-2")
    recorder.record(conn, orphan)
    conn.commit()
    assert reconciliation_assertion(recorder).ok is False

    recorder.backfill("run-2", reason="两段之间进程崩溃，镜像缺行")

    assert reconciliation_assertion(recorder).ok is True
    # 补录本身没有破坏链
    assert chain_assertion(recorder).ok is True


# ── 交叉反例二：镜像齐全但被篡改 → 链红，对账绿 ────────────────────────

def test_tampered_mirror_is_caught_by_chain_but_not_by_reconcile(
    recorder, conn, chain_path
):
    """改的是记录**内容**，id 集合一点没变——对账比的是 id 差集，看不见。

    只有哈希链能发现"这一行的字节被动过"。这是上一条反例的镜像方向：
    两条断言各自守着对方守不到的那一半。
    """
    for run_id in ("run-1", "run-2", "run-3"):
        record_both(recorder, conn, run_id)

    lines = chain_path.read_bytes().split(b"\n")
    record = json.loads(lines[0].decode("utf-8"))
    record["raw_response"] = "被人改过的响应"
    lines[0] = json.dumps(
        record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    chain_path.write_bytes(b"\n".join(lines))

    chained = chain_assertion(recorder)
    reconciled = reconciliation_assertion(recorder)

    assert chained.ok is False, "中间一行被改，链必须红"
    assert chained.violations, "链断了必须指出断在哪一行"
    assert any("broken_at" in v for v in chained.violations)
    assert reconciled.ok is True, "id 集合没变，对账看不见内容篡改——这正是它的盲区"


def test_deleted_mirror_line_breaks_the_chain(recorder, conn, chain_path):
    """整行删除：链红（后继的 prev_hash 对不上），对账也红（id 少了一个）。

    两条同时红是正常的——"不可互相替代"说的是各有盲区，不是互斥。
    """
    for run_id in ("run-1", "run-2", "run-3"):
        record_both(recorder, conn, run_id)

    lines = [line for line in chain_path.read_bytes().split(b"\n") if line.strip()]
    chain_path.write_bytes(b"\n".join(lines[:1] + lines[2:]) + b"\n")

    assert chain_assertion(recorder).ok is False
    assert reconciliation_assertion(recorder).ok is False


# ── 结构 ───────────────────────────────────────────────────────────────

def test_two_assertions_have_distinct_names(recorder):
    """名字不同不是洁癖：CI 报告里靠 name 区分"链断了"和"痕少了"，
    两者的处置完全不同——前者要查谁改了文件，后者要查哪次写入没落地。"""
    assert reconciliation_assertion(recorder).name != chain_assertion(recorder).name
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_audit_reconciliation.py -q`
Expected: FAIL —— `ImportError: cannot import name 'chain_assertion' from 'app.audit.assertions'`

- [ ] **Step 3: 写最小实现**

在 `app/audit/assertions.py` 末尾追加（⛔ 不改已有内容）：

```python
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
```

同时把文件头部的 import 段补上（TYPE_CHECKING 形式，避免与 `recorder.py` 形成运行期循环 import——`recorder.py` 不 import 本模块，所以其实无环，用 TYPE_CHECKING 只是把依赖方向表述清楚）：

```python
from typing import TYPE_CHECKING, Any, Callable, Sequence

if TYPE_CHECKING:  # pragma: no cover - 仅供类型标注
    from app.audit.recorder import AuditRecorder
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_audit_reconciliation.py -q`
Expected: PASS —— 7 passed

- [ ] **Step 5: 跑全量回归**

Run: `python -m pytest -q`
Expected: 全绿，通过数 = Task 2 之后 + 7

- [ ] **Step 6: 确认没碰 U2 的文件**

Run: `git status --short app/audit/`
Expected: 只有 `M app/audit/assertions.py`（Task 1 之后本文件已入库）。若出现 `sinks.py` / `recorder.py` / `criteria.py` / `events.py` / `__init__.py`，⛔ 立刻 `git checkout` 还原——Global Constraints 2 与 19。

- [ ] **Step 7: 提交**

```bash
git add app/audit/assertions.py tests/test_audit_reconciliation.py
git commit -m "feat(audit): U6 6.4 对账断言与链断言，两方向交叉反例证明不可互相替代"
```

---

### Task 4: 拦截统计（6.5）

> **数据源是 JSONL 镜像，不是 `pending_approval` 表。** 理由见本计划「已知的落地口
> 径」第三条（外发事件在 SqliteSink 里没有真身；放行复发被拦时不入队）。这是相对
> `app/outbound/gate.py:50` 那句注释的一处有意偏离，Task 5 Step 7 会把它登记进
> tasks.md。⛔ 本 Task 不去改 `gate.py`。

**Files:**
- Modify: `app/audit/assertions.py`（追加一个 dataclass + 一个函数）
- Test: `tests/test_outbound_block_stats.py`（新建）

**Interfaces:**
- Consumes: `app.audit.sinks.AuditSink`（只用 `read_all()`）；`app.audit.events.OUTBOUND_BLOCKED` / `OUTBOUND_DELIVERED`
- Produces:
  - `OutboundBlockStats` —— frozen dataclass，字段：`blocked_by_type_and_reason: dict[str, dict[str, int]]`、`blocked_by_type: dict[str, int]`、`blocked_by_reason: dict[str, int]`、`delivered_by_type: dict[str, int]`、`always_blocked_types: tuple[str, ...]`
  - `outbound_block_stats(mirror: AuditSink) -> OutboundBlockStats`
  - `UNKNOWN_MESSAGE_TYPE = "<未知类型>"` / `UNRECORDED_REASON = "<未记录原因>"`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_outbound_block_stats.py`：

```python
"""6.5 —— 按 message_type × 拦截原因统计，让"某类消息一直在被拦"可被发现。

这是 fail-closed 误拦的**兜底观测**，不是守护测试（design.md Risks 第 2 条）。
没有它，一个新增的消息类型忘了登记就会被静默拦下，只能等业务方投诉。

数据源是 JSONL 镜像：外发事件在 SqliteSink 里没有真身（SUPPORTED_EVENT_TYPES
只含 ai_analysis），镜像是它唯一的记录；而 pending_approval 表会漏掉"放行复发
被拦"那一整类（app/outbound/delivery.py:93-96 只对首道拦截入队）。
"""

import pytest

from app.audit.assertions import (
    UNKNOWN_MESSAGE_TYPE,
    UNRECORDED_REASON,
    outbound_block_stats,
)
from app.audit.events import (
    OUTBOUND_BLOCKED,
    OUTBOUND_DELIVERED,
    AI_ANALYSIS,
    DecisionEvent,
)
from app.audit.sinks import JsonlChainSink
from app.outbound.gate import ALL_BLOCK_REASONS  # ⚠️ 只在测试里 import 上层


@pytest.fixture(autouse=True)
def _clear_chain_class_state():
    yield
    JsonlChainSink._CURSORS.clear()
    JsonlChainSink._LOCKS.clear()


@pytest.fixture
def mirror(tmp_path):
    return JsonlChainSink(tmp_path / "audit" / "decisions.jsonl")


def blocked(mirror, *, index: int, message_type, reason):
    mirror.write(
        DecisionEvent(
            id=f"t-1:effect_record_outbound_audit:h{index}:False",
            event_type=OUTBOUND_BLOCKED,
            thread_id="t-1",
            message_type=message_type,
            recipient="user-1",
            blocked_reason=reason,
        )
    )


def delivered(mirror, *, index: int, message_type):
    mirror.write(
        DecisionEvent(
            id=f"t-1:effect_record_outbound_audit:h{index}:True",
            event_type=OUTBOUND_DELIVERED,
            thread_id="t-1",
            message_type=message_type,
            recipient="user-1",
            confirmed_by="shao",
        )
    )


def test_counts_by_type_and_reason(mirror):
    blocked(mirror, index=1, message_type="rejection_letter", reason="等待人工确认")
    blocked(mirror, index=2, message_type="rejection_letter", reason="等待人工确认")
    blocked(mirror, index=3, message_type="rejection_letter", reason="外发总开关关闭")
    blocked(mirror, index=4, message_type="interview_invitation", reason="外发总开关关闭")

    stats = outbound_block_stats(mirror)

    assert stats.blocked_by_type == {"rejection_letter": 3, "interview_invitation": 1}
    assert stats.blocked_by_reason == {"等待人工确认": 2, "外发总开关关闭": 2}
    assert stats.blocked_by_type_and_reason == {
        "rejection_letter": {"等待人工确认": 2, "外发总开关关闭": 1},
        "interview_invitation": {"外发总开关关闭": 1},
    }


def test_delivered_events_are_counted_separately(mirror):
    """光有拦截数回答不了"这类消息是不是**一直**在被拦"——要跟放行数对照。"""
    blocked(mirror, index=1, message_type="rejection_letter", reason="等待人工确认")
    delivered(mirror, index=2, message_type="rejection_letter")
    blocked(mirror, index=3, message_type="interview_invitation", reason="未登记的消息类型")

    stats = outbound_block_stats(mirror)

    assert stats.delivered_by_type == {"rejection_letter": 1}
    assert stats.blocked_by_type == {"rejection_letter": 1, "interview_invitation": 1}


def test_always_blocked_types_is_the_actionable_signal(mirror):
    """拦过、且**一次都没发出去过**的类型 —— 这才是要人去看的那一列。

    原始计数表放在运维面前，人得自己做减法；这个字段替他做完。
    """
    blocked(mirror, index=1, message_type="rejection_letter", reason="等待人工确认")
    delivered(mirror, index=2, message_type="rejection_letter")
    blocked(mirror, index=3, message_type="interview_invitation", reason="未登记的消息类型")
    blocked(mirror, index=4, message_type="interview_invitation", reason="未登记的消息类型")

    stats = outbound_block_stats(mirror)

    assert stats.always_blocked_types == ("interview_invitation",)


def test_missing_type_and_reason_get_explicit_buckets(mirror):
    """字段缺失的事件 ⛔ 不许丢弃——被拦下的草稿最常见的原因**正是**这些字段缺失。

    丢弃等于让最该被看见的那一类从统计里消失。
    """
    blocked(mirror, index=1, message_type=None, reason="未登记的消息类型")
    blocked(mirror, index=2, message_type="rejection_letter", reason=None)

    stats = outbound_block_stats(mirror)

    assert stats.blocked_by_type[UNKNOWN_MESSAGE_TYPE] == 1
    assert stats.blocked_by_reason[UNRECORDED_REASON] == 1


def test_ai_analysis_events_are_ignored(mirror):
    """同一条链上还躺着 AI 评分事件，⛔ 不能把它们算进外发统计。"""
    mirror.write(
        DecisionEvent(
            id="run-1",
            event_type=AI_ANALYSIS,
            configured_model="deepseek-chat",
            prompt_version="score-v1",
            temperature=0.0,
            input_hash="sha256:abc",
            raw_response="{}",
        )
    )
    blocked(mirror, index=1, message_type="rejection_letter", reason="等待人工确认")

    stats = outbound_block_stats(mirror)

    assert sum(stats.blocked_by_type.values()) == 1
    assert stats.delivered_by_type == {}


def test_every_registered_block_reason_survives_the_stats_path(mirror):
    """门禁那边登记的**每一条**拦截原因都要能在统计里出现。

    ⚠️ 这条用例是 ALL_BLOCK_REASONS（app/outbound/gate.py）与本统计之间的
    唯一绑定。gate.py 那边加一条新原因、忘了加进 ALL_BLOCK_REASONS 时，本条
    不会红——它守的是另一半：统计路径不会把任何一条已登记原因吃掉（比如被
    某个"过滤掉不认识的原因"的实现悄悄丢弃）。

    ⛔ assertions.py 模块内不 import app.outbound（分层：audit 是下层）。
    这个 import 只出现在测试里。
    """
    for index, reason in enumerate(sorted(ALL_BLOCK_REASONS)):
        blocked(mirror, index=index, message_type="rejection_letter", reason=reason)

    stats = outbound_block_stats(mirror)

    assert set(stats.blocked_by_reason) == set(ALL_BLOCK_REASONS)
    assert all(count == 1 for count in stats.blocked_by_reason.values())


def test_empty_mirror_yields_empty_stats(mirror):
    stats = outbound_block_stats(mirror)

    assert stats.blocked_by_type == {}
    assert stats.blocked_by_reason == {}
    assert stats.blocked_by_type_and_reason == {}
    assert stats.delivered_by_type == {}
    assert stats.always_blocked_types == ()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_outbound_block_stats.py -q`
Expected: FAIL —— `ImportError: cannot import name 'outbound_block_stats' from 'app.audit.assertions'`

- [ ] **Step 3: 写最小实现**

在 `app/audit/assertions.py` 末尾追加：

```python
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
```

在文件头部的 import 段补上事件类型常量：

```python
from app.audit.events import OUTBOUND_BLOCKED, OUTBOUND_DELIVERED
```

并把 `AuditSink` 加进 TYPE_CHECKING 块：

```python
if TYPE_CHECKING:  # pragma: no cover - 仅供类型标注
    from app.audit.recorder import AuditRecorder
    from app.audit.sinks import AuditSink
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_outbound_block_stats.py -q`
Expected: PASS —— 7 passed

- [ ] **Step 5: 确认没引入反向依赖**

Run: `python -c "import ast,sys; src=open('app/audit/assertions.py',encoding='utf-8').read(); mods=[n.module or '' for n in ast.walk(ast.parse(src)) if isinstance(n,ast.ImportFrom)]; bad=[m for m in mods if m.startswith(('app.outbound','app.config','app.graph','zhuopin_platform'))]; print('BAD:',bad); sys.exit(1 if bad else 0)"`
Expected: `BAD: []`，退出码 0

（Global Constraints 6、18、20：audit 是下层，⛔ 不得 import `app.outbound` / `app.config` / `app.graph`；`zhuopin_platform` 是本包三条硬边界之一。）

- [ ] **Step 6: 跑全量回归**

Run: `python -m pytest -q`
Expected: 全绿，通过数 = Task 3 之后 + 7

- [ ] **Step 7: 提交**

```bash
git add app/audit/assertions.py tests/test_outbound_block_stats.py
git commit -m "feat(audit): U6 6.5 拦截统计——按类型×原因，标出一直被拦的类型"
```

---

### Task 5: CLI 入口与 CI 接入（6.6）

**Files:**
- Modify: `app/audit/assertions.py`（追加 `main()` 与 `format_report()`）
- Modify: `pyproject.toml`（注册 `compliance` marker，3 行）
- Modify: `.github/workflows/ci.yml`（在既有 `test` job 里加一个步骤，⛔ 不另起 job）
- Modify: `openspec/changes/ai-audit-trail-and-outbound-gate/tasks.md`（回勾 6.1–6.7 + 登记偏离）
- Test: `tests/test_compliance_cli.py`（新建）

**Interfaces:**
- Consumes: 前四个 Task 的全部产出
- Produces:
  - `format_report(results: Sequence[AssertionResult]) -> str`
  - `main(argv: Sequence[str] | None = None) -> int` —— 退出码：`0` 全绿 / `1` 有违例 / `2` 库或镜像路径不存在

- [ ] **Step 1: 写失败测试**

创建 `tests/test_compliance_cli.py`：

```python
"""6.6 —— 合规断言接入测试套件与 CI。

CI 侧的接法：既有 test job 里加一个**可归因**的步骤（不另起一套 CI）。
本文件测的是 CLI 的退出码契约——CI 靠退出码判红绿，`.51` 上机巡检也靠它。

⚠️ 退出码 2（库不存在）**不能折成 0**。一个指错路径的巡检命令若安静地返回
0，读的人会以为"三条红线都守住了"，而实际上它一行数据都没查过——这跟空表
恒真是同一种谎，只是更隐蔽。
"""

import json

import pytest

from app.audit.assertions import AssertionResult, format_report, main
from app.audit.events import AI_ANALYSIS, CriterionScore, DecisionEvent
from app.audit.recorder import AuditRecorder
from app.audit.sinks import JsonlChainSink, SqliteSink
from app.storage.db import get_connection, init_schema
from tests.test_audit_assertions import insert_run, insert_score

pytestmark = pytest.mark.compliance


@pytest.fixture(autouse=True)
def _clear_chain_class_state():
    yield
    JsonlChainSink._CURSORS.clear()
    JsonlChainSink._LOCKS.clear()


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "demo.db"
    conn = get_connection(str(path))
    init_schema(conn)
    conn.close()
    return path


@pytest.fixture
def mirror_path(tmp_path):
    path = tmp_path / "audit" / "decisions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_exit_zero_when_everything_is_clean(db_path, mirror_path, capsys):
    code = main(["--db", str(db_path), "--mirror", str(mirror_path)])

    assert code == 0
    out = capsys.readouterr().out
    assert "全部通过" in out


def test_exit_one_when_a_red_line_is_broken(db_path, mirror_path, capsys):
    conn = get_connection(str(db_path))
    run_id = insert_run(conn)
    insert_score(
        conn, run_id=run_id, score_id="s-face",
        criterion_key="facial_expression", evidence_ref="video-1#0-10",
    )
    conn.close()

    code = main(["--db", str(db_path), "--mirror", str(mirror_path)])

    assert code == 1
    out = capsys.readouterr().out
    # spec：任一条不成立时判定为失败**并指出违例记录**。
    assert "facial_expression" in out
    assert "s-face" in out


def test_exit_two_when_db_missing(tmp_path, mirror_path, capsys):
    """⛔ 不许折成 0。指错路径的巡检安静返回 0 = 一行没查过却报"红线守住了"。"""
    code = main(["--db", str(tmp_path / "nope.db"), "--mirror", str(mirror_path)])

    assert code == 2
    assert "不存在" in capsys.readouterr().err


def test_exit_two_when_mirror_missing(db_path, tmp_path, capsys):
    code = main(["--db", str(db_path), "--mirror", str(tmp_path / "nope.jsonl")])

    assert code == 2
    assert "不存在" in capsys.readouterr().err


def test_exit_one_when_chain_is_broken(db_path, mirror_path, capsys):
    """链校验也接进 CLI（tasks 6.6：三条断言 + 链校验）。"""
    conn = get_connection(str(db_path))
    recorder = AuditRecorder(
        store=SqliteSink(conn), mirror_sink=JsonlChainSink(mirror_path)
    )
    for run_id in ("run-1", "run-2"):
        event = DecisionEvent(
            id=run_id, event_type=AI_ANALYSIS, thread_id="t-1",
            configured_model="deepseek-chat", prompt_version="score-v1",
            temperature=0.0, input_hash=f"sha256:{run_id}", raw_response="{}",
            scores=(CriterionScore("skill_match", 3.0, "resume-1#1-9"),),
        )
        recorder.record(conn, event)
        conn.commit()
        recorder.mirror(event)
    conn.close()

    lines = mirror_path.read_bytes().split(b"\n")
    record = json.loads(lines[0].decode("utf-8"))
    record["raw_response"] = "被改过"
    lines[0] = json.dumps(
        record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    mirror_path.write_bytes(b"\n".join(lines))

    code = main(["--db", str(db_path), "--mirror", str(mirror_path)])

    assert code == 1
    assert "哈希链" in capsys.readouterr().out


def test_report_lists_violations_not_just_counts():
    """报告必须带违例记录本身。只报数字的话，CI 红了还得有人本地重跑一遍才知道红在哪。"""
    results = [
        AssertionResult(name="甲", ok=True),
        AssertionResult(
            name="乙", ok=False,
            violations=({"id": "s-1", "criterion_key": "face_match"},),
            detail="红线维度",
        ),
    ]

    report = format_report(results)

    assert "甲" in report and "乙" in report
    assert "s-1" in report
    assert "face_match" in report
    assert "红线维度" in report


def test_report_says_all_passed_when_nothing_is_broken():
    report = format_report([AssertionResult(name="甲", ok=True)])

    assert "全部通过" in report
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_compliance_cli.py -q`
Expected: FAIL —— `ImportError: cannot import name 'format_report' from 'app.audit.assertions'`

- [ ] **Step 3: 注册 pytest marker**

修改 `pyproject.toml` 的 `[tool.pytest.ini_options]` 段，追加三行：

```toml
[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
# 合规断言（U6）。CI 在跑完全量之后**再单独跑一遍**打了这个标记的用例，
# 让「红线被破坏」变成一个可归因的失败步骤，而不是埋在几百条用例里的一行红。
# 重复执行是刻意的成本，⛔ 不要为了省几秒把全量那次改成 -m "not compliance"。
markers = [
    "compliance: 合规红线断言与其反证（tasks.md 第 6 章）",
]
```

- [ ] **Step 4: 给三份断言测试打标记**

在下列三个文件的 import 段之后各加一行（`tests/test_compliance_cli.py` 已在 Step 1 加过）：

```python
pytestmark = pytest.mark.compliance
```

- `tests/test_audit_assertions.py`
- `tests/test_audit_assertion_effectiveness.py`
- `tests/test_audit_reconciliation.py`

⚠️ `tests/test_outbound_block_stats.py` **不打标记**：拦截统计是观测手段，不是红线断言，它红了不代表红线破了。混进去会稀释这个标记的含义。

Run: `python -m pytest -q -m compliance --collect-only | tail -3`
Expected: 收集到 4 个文件、共 43 条用例（7 + 22 + 7 + 7）。以实际为准，关键是**不含** `tests/test_outbound_block_stats.py`

- [ ] **Step 5: 写 CLI 实现**

在 `app/audit/assertions.py` 末尾追加：

```python
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

    # 局部 import：这三个只有 CLI 路径用得到，模块被当库 import 时不该
    # 顺带把 argparse 拖进来。
    import argparse
    import sqlite3 as _sqlite3

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

    conn = _sqlite3.connect(str(db_path))
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
```

- [ ] **Step 6: 跑测试确认通过**

Run: `python -m pytest tests/test_compliance_cli.py -q`
Expected: PASS —— 7 passed

Run: `python -m pytest -q`
Expected: 全绿，通过数 = Task 4 之后 + 7

Run: `python -m pytest -q -m compliance`
Expected: PASS，43 passed（含 `test_compliance_cli.py` 的 7 条）

- [ ] **Step 7: 接进 CI（⛔ 不另起 job）**

修改 `.github/workflows/ci.yml`，在既有 `test` job 的 `pytest` 步骤**之后**插入一个步骤（其余内容一行不改）：

```yaml
      - name: pytest
        run: python -m pytest -q

      # 合规红线的可归因门禁。三条断言 + 链校验 + 对账已经在上一步的全量里
      # 跑过一遍了，这里**故意再跑一遍**：红线被破坏时，CI 页面上要能看见一个
      # 名字就叫「合规断言」的红色步骤，而不是让人从几百条用例里翻。
      #
      # ⚠️ 这些断言在 CI 的空库上**恒真**。CI 的效力不来自它们返回绿色，来自
      # tests/test_audit_assertion_effectiveness.py 里那 22 条"造违例 → 必须
      # 失败"的反证（tasks.md 6.7）。⛔ 不要把反证从这个标记里摘出去。
      #
      # 真实数据上的巡检在 .51 上手工跑：
      #   python -m app.audit.assertions --db data/demo.db --mirror data/audit/decisions.jsonl
      # 口径见 docs/audit-and-outbound-ops.md。
      - name: 合规断言（红线守护）
        run: python -m pytest -q -m compliance -v
```

Run: `python -c "import yaml,sys; d=yaml.safe_load(open('.github/workflows/ci.yml',encoding='utf-8')); names=[s.get('name') for s in d['jobs']['test']['steps']]; print(names); sys.exit(0 if '合规断言（红线守护）' in names else 1)"`
Expected: 打印步骤名列表且退出码 0。⚠️ **本仓库的 venv 里没有 `pyyaml`**（2026-09-03 实测），所以主判据用 grep：

Run: `grep -c '合规断言（红线守护）' .github/workflows/ci.yml`
Expected: `1`

- [ ] **Step 8: 回勾 tasks.md 并登记偏离**

修改 `openspec/changes/ai-audit-trail-and-outbound-gate/tasks.md`：

1. 把 6.1–6.7 七条的 `- [ ]` 改成 `- [x]`
2. 把顶部「进度」行从 `44/53` 改成 `51/53`，未归档依据改为「第 7 章 4/6 未完」
3. 在第 6 章末尾追加一节：

```markdown
### 6.x 落地偏离登记（U6 实施，2026-09-03）

本章按 `docs/superpowers/plans/2026-09-03-ai-audit-trail-unitU6-assertions-and-ci.md`
实施，落地时相对本文件与既有注释字面有三条偏离。**三条方向都是"更严"或"更准"。**

| # | 字面 | 实际落地 | 判据（哪条测试咬住它） |
|---|---|---|---|
| 1 | 6.5「按 `message_type` 与拦截原因统计」，`app/outbound/gate.py:50` 注释指向 `pending_approval.blocked_reason` | 数据源改为 **JSONL 镜像**，不查 `pending_approval` | 只查 `pending_approval` 会系统性漏掉两类：① 外发**放行**事件（根本不入队）；② 放行复发被拦（`app/outbound/delivery.py` 的死锁防线只对首道拦截入队）。而"某类消息是不是一直在被拦"恰恰要拿拦截数和放行数对照才答得出。`test_delivered_events_are_counted_separately` / `test_always_blocked_types_is_the_actionable_signal` |
| 2 | 6.1「表不存在即通过、表存在则计数必须为 0」 | 多一条分支：**表存在但缺 `reason_type` 列 → 判失败** | 字面只写了两条分支，第三种情况（M2 建表时列名与本仓库常量不一致）会落进"查不到违例"从而静默通过。fail-closed：验不了红线不算守住了红线。`test_ai_score_rejection_assertion_fails_when_reason_column_missing` |
| 3 | 6.6「三条断言 + 链校验接入测试套件与 CI」 | 除接入外，另加 `python -m app.audit.assertions` CLI 入口，退出码 0/1/**2** | CI 的库是空的，三条断言在那儿恒真——真正有数据可查的是 `.51`。退出码 2（路径不存在）单列是关键：指错路径的巡检若返回 0，读的人会以为红线守住了。`test_exit_two_when_db_missing` / `test_exit_two_when_mirror_missing` |

**⏸ 留步（本单元不闭合，需 `.51` 上机）**：CLI 从未对着 `.51` 的真实
`data/demo.db` 与 `data/audit/decisions.jsonl` 跑过。首次上机巡检属发版动作
（生产服务器 `.51` 的发版决定为不可代项），登记在此，待 Shao Peishen 安排。
```

- [ ] **Step 9: 跑全量回归并核对自查清单**

```bash
python -m pytest -q
python -m pytest -q -m compliance -v
grep -c '^### Task ' docs/superpowers/plans/2026-09-03-ai-audit-trail-unitU6-assertions-and-ci.md
grep -c 'Global Constraints' docs/superpowers/plans/2026-09-03-ai-audit-trail-unitU6-assertions-and-ci.md
git status --short
```

Expected：全量全绿；compliance 43 passed；`### Task` 计数为 5；`Global Constraints` 计数 ≥ 1；`git status` 里只有本单元的文件（别的泳道的改动出现是正常的，⛔ 不要顺手提交）。

- [ ] **Step 10: 提交**

```bash
git add app/audit/assertions.py tests/test_compliance_cli.py \
        tests/test_audit_assertions.py tests/test_audit_assertion_effectiveness.py \
        tests/test_audit_reconciliation.py \
        pyproject.toml .github/workflows/ci.yml \
        openspec/changes/ai-audit-trail-and-outbound-gate/tasks.md
git commit -m "feat(audit): U6 6.6 合规断言接入 CI + 巡检 CLI，第 6 章 7/7"
```

⛔ 只 add 上面列出的路径。禁止 `git add -A` / `git add .` / `git commit -a`——本仓库同期有别的泳道在跑，这是并行成立的唯一前提。

---

## Self-Review（写完计划后的三项自查，已执行）

**1. Spec coverage**

| spec Requirement | 落在哪个 Task |
|---|---|
| ai-decision-audit「留痕可查询与合规断言」· 三条断言 | Task 1 |
| ai-decision-audit「留痕可查询与合规断言」· 场景「合规断言在 CI 中执行」 | Task 5（CI 步骤 + CLI 退出码） |
| ai-decision-audit「留痕可查询与合规断言」· 场景「有人以 AI 评分为理由写入拒绝记录」 | Task 2（反证） |
| ai-decision-audit「逐项评分必须带证据回指」· 存储层之上的纵深防御 | Task 1（6.2）+ Task 2（六种空白形状） |
| ai-decision-audit「评分项白名单约束」· 声学/生物特征维度 | Task 1（6.3）+ Task 2（遍历 `RED_LINE_EXAMPLES`） |
| ai-decision-audit「留痕不可无痕篡改」· 链校验接入 | Task 3（`chain_assertion`）+ Task 5（CLI） |
| outbound-approval-gate「外发与拦截动作强制留痕」· 场景「查询某类消息的拦截情况」 | Task 4 |
| design D1 · 跨介质对账与链尾补录 | Task 3 |

**未覆盖（有意）**：`verify_chain()` 自身的实现与测试属 U2，已交付，本单元只调用不改（Global Constraints 2）。`rejection_record` 建表属 M2，本单元不建（Global Constraints 3）。

**2. Placeholder scan**：全文无 TBD / TODO / "适当处理错误" / "similar to Task N"。每个代码步骤都给了可直接落盘的完整代码块，每个命令步骤都给了确切命令与预期输出。

**3. Type consistency**：`AssertionResult` 的四个字段（`name` / `ok` / `violations` / `detail`）在 Task 1、3、4、5 中用法一致；`outbound_block_stats` 在 Task 4 定义、Task 5 调用，参数同为 `AuditSink`；`run_compliance_assertions` 在 Task 1 定义、Task 2 与 Task 5 调用，均返回 `list[AssertionResult]`；`main(argv)` 的退出码契约在实现与测试两处逐字一致。

---

## 交付后的下一步

1. `run-build` 执行本计划（CC / 新开 session / **勾 worktree**）。
2. 全部 Task 的 final review 通过后，第 6 章 7/7，`tasks.md` 达 51/53。
3. 剩余阻塞归档的只有第 7 章的 7.1/7.2/7.5/7.6（U7）。
4. **⏸ 留步项**：CLI 对 `.51` 真实数据的首次巡检——属生产服务器发版动作，不可代，等 Shao Peishen 安排。
