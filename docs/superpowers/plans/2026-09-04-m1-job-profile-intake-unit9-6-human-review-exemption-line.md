# 断言四豁免线改用决策发生时刻 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让审计断言四（`assert_every_decision_has_human_review`）的留痕豁免线按「该画像版本进入终态那一刻」判定，而不是按「画像草案创建时刻」，使"线前创建、线后确认/放弃却漏写 `human_review`"的行不再永久隐身。

**Architecture:** 决策真实发生的时刻已经存在于 `effect_log.applied_at`（工程铁律 1 的产物：`effect_confirm_profile` / `effect_abandon_profile` 的业务写与 `effect_log` 行在同一事务里提交），不需要新建任何机制、不需要加列、不需要数据回填。本次改动**只发生在 `app/audit/assertions.py` 一个文件里**：新增一张 `终态 status → effect_* 节点名` 的映射，把「违例判定」与「豁免计数」两处查询都换成通过 `effect_log` 关联取 `applied_at`；查不到对应 `effect_log` 行时按未豁免处理（fail-closed）。

**Tech Stack:** Python 3.11 · sqlite3（`app/storage/db.py` 单连接）· pytest（`pytestmark = pytest.mark.compliance`）

## Global Constraints

以下六条**逐字**来自本交付单元的 opener 与 `CLAUDE.md`「工程铁律 / 合规红线」，每个 Task 的要求都隐含包含本段：

1. ⛔ 只改 `app/audit/assertions.py` 与其测试；⛔ 不改 `human_review` 表结构、不改 `HUMAN_REVIEW_ENFORCED_FROM` 取值、不碰 `nodes.py` / `idempotency.py`
2. `job_profile.version`（INTEGER）与 `effect_log.business_key`（TEXT）比较必须显式 CAST，⛔ 不依赖 SQLite 隐式仿射；单测要覆盖"不 CAST 就错"的反例
3. 终态行查不到对应 `effect_log` → 按未豁免处理（fail-closed），⛔ 不得默认豁免
4. 违例判定与豁免计数用同一套 `effect_log` 关联，⛔ 不许一边 `applied_at` 一边 `created_at`
5. 场景 1 的回归测试必须"改动前红、改动后绿"，plan 里写明先写测试证伪
6. 合规红线：这是「淘汰必须有人工确认节点并留痕」的机器判据，⛔ 不放松、不加旁路

附加铁律（`CLAUDE.md`，与本单元相关的部分，逐字）：

- **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。**幂等记录与业务写必须在同一个事务里提交**。
  → 本单元**不新增任何副作用**，只读 `effect_log`。reviewer 判据：本单元的 diff 里不得出现任何 `INSERT` / `UPDATE` / `DELETE`（测试 fixture 除外）。
- **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。
  → 断言四就是这条红线的机器判据。任何让断言更容易变绿的改动都是放松红线，⛔ 不做。

---

## 背景（实现者必读，读完再动手）

### 缺陷是什么

`app/audit/assertions.py:309-327` 现在这样划豁免线：

```sql
WHERE p.status = ? AND p.created_at >= ?          -- 违例判定
WHERE status IN ('approved','abandoned') AND created_at < ?   -- 豁免计数
```

`created_at` 是**画像草案创建时刻**。而 `effect_confirm_profile`（`app/graph/nodes.py:265-270`）与 `effect_abandon_profile`（`:361-365`）都是就地 `UPDATE job_profile SET status = ...`，**从不推进 `created_at`**。

后果：凡是在 `HUMAN_REVIEW_ENFORCED_FROM`（`2026-09-04 00:00:00`）之前创建、之后才被确认/放弃的草案，永远落在豁免侧。日后它漏写 `human_review`，断言完全看不见——而这条断言的全部意义就是让"谁在什么时候确认了哪一版画像答不出来"这个状态在 CI 里红。

2026-09-04 Shao Peishen 裁决「现在修」。规格已落在 `openspec/changes/m1-job-profile-intake/specs/job-profile-approval/spec.md` 的 Scenario「留痕豁免线按决策发生时刻判定」，设计在 `design.md`「决策七」。

### 关联 key（已核实，不留给实现阶段猜）

| 事实 | 取值 | 出处 |
|---|---|---|
| 幂等键格式 | `f"{thread_id}:{node_name}:{business_key}"` | `app/storage/idempotency.py:32` |
| `thread_id` | = `job_profile.job_id` | `app/web/server.py:383`（confirm）、`:494`（abandon）均传 `thread_id=job_id` |
| `business_key` | = `str(version)`（TEXT 列） | `app/web/server.py:384`、`:495` |
| `node_name` | `approved → effect_confirm_profile`、`abandoned → effect_abandon_profile` | `app/graph/nodes.py:233`、`:337` 的 `@idempotent_effect(...)` 字面量 |
| 决策时刻 | `effect_log.applied_at`，写入用 `datetime('now')` | `app/storage/idempotency.py:70-73` |

`applied_at` 与 `HUMAN_REVIEW_ENFORCED_FROM` **同为 UTC `'YYYY-MM-DD HH:MM:SS'` 字符串**，可以直接字符串比较，不需要转换。

`effect_log` 表结构（`app/storage/db.py:59-65`）：

```sql
CREATE TABLE IF NOT EXISTS effect_log (
    effect_key TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    node_name TEXT NOT NULL,
    business_key TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
```

### 关于 Global Constraint 2（CAST）——先读这段，否则会写出同义反复的测试

本 plan 在 SQLite 3 上实测过下面三种写法（`p.version` INTEGER = 2，`e.business_key` TEXT 取不同值）：

| `business_key` | `e.business_key = p.version`（裸比，靠隐式仿射） | `CAST(e.business_key AS INTEGER) = p.version` | `'j:'\|\|e.business_key = 'j:'\|\|p.version`（拼 effect_key 字符串） |
|---|---|---|---|
| `"2"` | 命中 | 命中 | 命中 |
| `"02"` | 命中 | 命中 | **漏** |
| `"0002"` | 命中 | 命中 | **漏** |
| `" 2"` | 命中 | 命中 | **漏** |
| `"2.0"` | 命中 | 命中 | **漏** |

结论要说清楚，⛔ 不许在测试注释里含糊过去：

- **裸比在这里恰好与 CAST 等价**——SQLite 的规则是「一侧有 INTEGER/REAL/NUMERIC 亲和性、另一侧是 TEXT 时，给 TEXT 侧套 NUMERIC 亲和性」。所以"不写 CAST 就一定错"这句话，对**列 vs 列**的裸比是**不成立**的。
- **真正会静默出错的是把关联改写成拼 `effect_key` 字符串去比**——那是字符串相等，`business_key` 一旦不是规范十进制写法就漏匹配，而漏匹配在 fail-closed 下会把本该豁免的历史行**报成违例**（假红）。
- 因此 Global Constraint 2 要求的「不 CAST 就错的反例」，在本单元里落地成两条测试：① 一条直接对 SQLite 跑的特征化测试，钉住上表第三列与第四列的差异；② 一条黑盒测试，用非规范 `business_key` 走真正的断言函数，字符串写法下会红、CAST 写法下绿。⛔ 不要写"把 CAST 删掉断言就失败"这种测试——实测证明它写不出来，硬写只会得到一条恒绿的同义反复。
- 显式 CAST 仍然**必须写**：它把"这两列类型不同、这里是按数值比"这个意图钉在代码里，不依赖读者记得 SQLite 的亲和性规则。

### 文件结构

| 文件 | 责任 | 本次动作 |
|---|---|---|
| `app/audit/assertions.py` | 四条合规断言的实现 | **修改**：新增 `TERMINAL_STATUS_EFFECT_NODES` 与 `_DECISION_MOMENT_SQL`，改断言四的两处查询与注释 |
| `tests/test_audit_assertions.py` | 断言的**正向**行为（应该通过的用例）+ 共享 fixture 辅助 | **修改**：新增 `_seed_effect_log` 辅助、更新既有豁免用例、新增豁免计数口径与两条 CAST 相关用例 |
| `tests/test_audit_assertion_effectiveness.py` | 断言**有效性**的反证（造违例 → 必须失败） | **修改**：新增场景 1（回归）与场景 3（fail-closed）两条反证 |

⛔ 不新建文件。两份测试文件的分工是既有纪律（见各自文件头 docstring）：「必须失败」的用例进 effectiveness，「应该通过」的进 assertions，⛔ 不许混。

---

### Task 1: 违例判定改用决策发生时刻（含 fail-closed 分支）

**Files:**
- Modify: `app/audit/assertions.py:253-338`
- Test: `tests/test_audit_assertions.py:205-260`（新增辅助 + 更新既有豁免用例）
- Test: `tests/test_audit_assertion_effectiveness.py`（文件末尾追加两条反证）

**Interfaces:**
- Consumes: `_rows(conn, sql, params) -> list[dict]`（`app/audit/assertions.py:74`）、`TERMINAL_STATUS_DECISIONS`（`:258`）、`HUMAN_REVIEW_ENFORCED_FROM`（`:264`）、`JOB_PROFILE_TABLE` / `HUMAN_REVIEW_TABLE`（`:253-254`）
- Produces（Task 2、Task 3 依赖这些确切名字）：
  - `TERMINAL_STATUS_EFFECT_NODES: dict[str, str]` —— 模块级常量，`{"approved": "effect_confirm_profile", "abandoned": "effect_abandon_profile"}`
  - `_DECISION_MOMENT_SQL: str` —— 模块级常量，一段带**一个** `?` 占位符（`node_name`）的标量子查询 SQL 片段，可嵌进任何以 `p` 为 `job_profile` 别名的查询
  - `tests/test_audit_assertions.py::_seed_effect_log(conn, job_id="j1", version=1, node_name="effect_confirm_profile", applied_at=None) -> None`
  - `assert_every_decision_has_human_review` 返回的每条 violation 新增一个 `decided_at` 键（`str | None`，`None` = 查不到 `effect_log`）

- [ ] **Step 1: 写测试辅助 `_seed_effect_log`**

在 `tests/test_audit_assertions.py` 里，紧跟在 `_seed_review`（约 `:217-224`）之后追加：

```python
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
```

- [ ] **Step 2: 写场景 1 的回归反证（本次要修的真实缺口）**

在 `tests/test_audit_assertion_effectiveness.py` **文件末尾**追加：

```python
def test_pre_cutoff_draft_confirmed_after_cutoff_is_not_exempt(conn):
    """场景 1（本次要修的真实缺口）：草案创建于豁免线之前、确认动作发生在
    豁免线之后、缺 human_review → 必须报违例。

    修复前这条是**红**的：旧实现拿 job_profile.created_at 与豁免线比，
    而 effect_confirm_profile 是就地 UPDATE status、从不推进 created_at，
    所以这一行会被判成"历史行"永久豁免——日后漏写留痕，断言完全看不见
    （design.md 决策七 / spec Scenario「留痕豁免线按决策发生时刻判定」）。
    """
    from app.audit.assertions import assert_every_decision_has_human_review
    from tests.test_audit_assertions import _seed_effect_log, _seed_terminal_profile

    _seed_terminal_profile(conn, status="approved", created_at="2026-08-01 10:00:00")
    _seed_effect_log(
        conn,
        node_name="effect_confirm_profile",
        applied_at="2026-09-04 09:00:00",
    )

    result = assert_every_decision_has_human_review(conn)

    assert result.ok is False
    assert result.violations, "断言失败时必须指出违例记录，⛔ 不许只报一个 False"
    assert result.violations[0]["job_id"] == "j1"
    assert result.violations[0]["decided_at"] == "2026-09-04 09:00:00"


def test_terminal_row_without_effect_log_is_not_exempt(conn):
    """场景 3（fail-closed）：终态行在 effect_log 里查不到对应决策时刻 →
    按**未豁免**处理，缺留痕就报违例。

    ⛔ 不得反过来把"查不到"当成"证明它发生在豁免线之前"。这与断言四
    "表不存在 → 判失败"的取向一致：宁可多报一条需要人核实的违例，不可漏判。
    """
    from app.audit.assertions import assert_every_decision_has_human_review
    from tests.test_audit_assertions import _seed_terminal_profile

    _seed_terminal_profile(conn, status="abandoned", created_at="2026-08-01 10:00:00")

    result = assert_every_decision_has_human_review(conn)

    assert result.ok is False
    assert result.violations[0]["job_id"] == "j1"
    assert result.violations[0]["decided_at"] is None
```

- [ ] **Step 3: 跑这两条，确认它们红**

Run:
```bash
python -m pytest tests/test_audit_assertion_effectiveness.py::test_pre_cutoff_draft_confirmed_after_cutoff_is_not_exempt tests/test_audit_assertion_effectiveness.py::test_terminal_row_without_effect_log_is_not_exempt -v
```
Expected: 两条都 **FAIL**，失败信息形如 `assert True is False`（旧实现把这两行都判成豁免，`result.ok` 仍是 `True`）。

⛔ 这一步不许跳过。它们不红就说明测试没造出缺口，后面无论怎么改都证明不了修复有效。

- [ ] **Step 4: 新增 `TERMINAL_STATUS_EFFECT_NODES` 常量**

在 `app/audit/assertions.py` 里，紧跟在 `TERMINAL_STATUS_DECISIONS`（`:258-261`）之后插入：

```python
# 终态 → 写下这条决策的 effect_* 节点名。⛔ 与 app/graph/nodes.py 两个
# @idempotent_effect(...) 的字面量参数（:233 effect_confirm_profile、
# :337 effect_abandon_profile）逐字同源，改一处必须同步改另一处——纪律与上面
# TERMINAL_STATUS_DECISIONS ↔ nodes.py 的 DECISION_* 常量（nodes.py:20-24）相同。
# 端到端守卫见 tests/test_audit_assertions.py::test_terminal_status_effect_nodes_match_the_real_effect_nodes
TERMINAL_STATUS_EFFECT_NODES: dict[str, str] = {
    "approved": "effect_confirm_profile",
    "abandoned": "effect_abandon_profile",
}
```

- [ ] **Step 5: 更新 `HUMAN_REVIEW_ENFORCED_FROM` 上方注释**

把 `app/audit/assertions.py:263` 那一行注释整体替换（⛔ **常量名与取值一个字都不改**）：

```python
# 留痕上线日（UTC，与 datetime('now') 同格式）。早于此刻**做出决策**
# （effect_log.applied_at，非画像草案创建时刻）的画像版本豁免。
#
# ⛔ 不要改回拿 job_profile.created_at 比：effect_confirm_profile /
#    effect_abandon_profile 都是就地 UPDATE status，从不推进 created_at。
#    拿 created_at 比，等于让"线前创建、线后确认"的草案永久落在豁免侧——
#    它日后漏写 human_review，断言完全看不见（design.md 决策七）。
HUMAN_REVIEW_ENFORCED_FROM = "2026-09-04 00:00:00"
```

- [ ] **Step 6: 新增 `_DECISION_MOMENT_SQL` 片段**

在 `_REQUIRED_HUMAN_REVIEW_COLUMNS`（`:266-268`）之后、`ASSERTION_HUMAN_REVIEW_PRESENT` 之前插入：

```python
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
```

- [ ] **Step 7: 改违例查询**

把 `app/audit/assertions.py:306-320` 的循环整段替换成：

```python
    violations: list[dict[str, Any]] = []
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
```

⚠️ 参数顺序是 `(node_name, status, HUMAN_REVIEW_ENFORCED_FROM, decision)`——`node_name` 排第一，因为 `_DECISION_MOMENT_SQL` 的 `?` 出现在 `WHERE p.status = ?` 之前。写反了不会报错，只会静默匹配不到任何 `effect_log` 行。

- [ ] **Step 8: 更新函数 docstring 的分支清单**

把 `app/audit/assertions.py:276-283` 的 docstring 替换成：

```python
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
```

- [ ] **Step 9: 更新既有豁免用例，让它按决策时刻造数据**

`tests/test_audit_assertions.py:247-260` 的 `test_human_review_assertion_exempts_rows_written_before_the_cutoff` 现在只造 `created_at`、不造 `effect_log`，改动后会撞上 fail-closed 分支而变红。这**不是回归，是它的前提变了**——整条替换成（含改名）：

```python
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
```

⚠️ 同文件的 `test_human_review_assertion_passes_when_every_decision_left_a_trace`（`:227`）与 `test_human_review_assertion_ignores_drafts`（`:239`）**一个字都不用改**：前者两行都有 `human_review`，无论豁免与否都不违例；后者是 `drafting`，根本不进终态循环。`tests/test_audit_assertion_effectiveness.py::test_missing_human_review_is_caught`（`:330`）也不用改——它造的行没有 `effect_log`，改动后走 fail-closed 分支，照样红。

- [ ] **Step 10: 跑断言四相关的全部用例**

Run:
```bash
python -m pytest tests/test_audit_assertions.py tests/test_audit_assertion_effectiveness.py -v
```
Expected: **全部 PASS**。特别确认 Step 2 加的两条已由红转绿。

- [ ] **Step 11: 跑全量 compliance 标记，确认没有别处依赖旧口径**

Run:
```bash
python -m pytest -m compliance -q
```
Expected: 全部 PASS，0 failed。

若有别的文件红了：⛔ 不许放松断言让它变绿。按同样的方式给那条用例补上 `effect_log` fixture（它红的原因只会是"造了终态行却没造决策时刻"）。

- [ ] **Step 12: Commit**

```bash
git add app/audit/assertions.py tests/test_audit_assertions.py tests/test_audit_assertion_effectiveness.py
git commit -m "fix(audit): 断言四违例判定改用 effect_log.applied_at，查不到决策时刻按未豁免处理"
```

---

### Task 2: 豁免计数改用同一套 effect_log 关联

**Files:**
- Modify: `app/audit/assertions.py:322-338`（Task 1 改完后的行号会有位移，按 `exempted = _rows(` 定位）
- Test: `tests/test_audit_assertions.py`（文件末尾追加一条）

**Interfaces:**
- Consumes: Task 1 产出的 `TERMINAL_STATUS_EFFECT_NODES`、`_DECISION_MOMENT_SQL`、`_seed_effect_log`
- Produces: `assert_every_decision_has_human_review` 的 `detail` 文案改为「豁免 N 条决策发生在 … 之前的历史画像版本」，`N` 与违例判定同一套时间基准

**为什么单独一个 Task**：违例判定与豁免计数是两处独立的查询，各有各的红绿判据（Global Constraint 4 要的是"两处最终用同一套关联"，不是"必须一次改完"）。拆开后，Task 1 的 reviewer 能只盯 fail-closed 语义，Task 2 的 reviewer 能只盯"报出来的数字对不对"。

- [ ] **Step 1: 写场景 4 的失败测试**

在 `tests/test_audit_assertions.py` **文件末尾**追加：

```python
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
```

- [ ] **Step 2: 跑它，确认它红**

Run:
```bash
python -m pytest "tests/test_audit_assertions.py::test_exemption_count_follows_the_decision_moment_not_the_draft_time" -v
```
Expected: **FAIL**，`AssertionError: 豁免 2 条早于 2026-09-04 00:00:00 的历史画像版本…`（旧计数按 `created_at`，两行都算进去了）。

- [ ] **Step 3: 把豁免计数并进终态循环**

把 Task 1 改完后的违例循环与其后的 `exempted = _rows(...)` 一起替换成下面这段（循环体内累加，⛔ 不要在循环外另起一条查询——那正是"两套时间基准"的来源）：

```python
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
        # decided_at IS NOT NULL 是必须的——NULL 在上面按"未豁免"处理了，
        # 这里若漏掉，同一行会既被判违例又被算成豁免。
        exempted += _rows(
            conn,
            "SELECT COUNT(*) AS n FROM ("
            f"  SELECT {_DECISION_MOMENT_SQL} AS decided_at"
            f"  FROM {JOB_PROFILE_TABLE} p WHERE p.status = ?"
            ") t WHERE t.decided_at IS NOT NULL AND t.decided_at < ?",
            (node_name, status, HUMAN_REVIEW_ENFORCED_FROM),
        )[0]["n"]
```

- [ ] **Step 4: 更新 detail 文案**

把返回语句里的 `detail=` 换成：

```python
        detail=(
            f"豁免 {exempted} 条决策发生在 {HUMAN_REVIEW_ENFORCED_FROM} 之前的"
            "历史画像版本（留痕上线之前确认/放弃的，不可能有记录）。"
            "⚠️ 这些行**不代表红线守住了**，只代表它们产生于留痕存在之前。"
            "⛔ 查不到决策时刻的终态行不在此列——它们按未豁免处理，缺留痕即违例。"
        ),
```

- [ ] **Step 5: 跑测试确认转绿**

Run:
```bash
python -m pytest tests/test_audit_assertions.py tests/test_audit_assertion_effectiveness.py -v
```
Expected: **全部 PASS**，Step 1 那条已由红转绿。

- [ ] **Step 6: 跑全量 compliance**

Run:
```bash
python -m pytest -m compliance -q
```
Expected: 全部 PASS，0 failed。

- [ ] **Step 7: Commit**

```bash
git add app/audit/assertions.py tests/test_audit_assertions.py
git commit -m "fix(audit): 断言四豁免计数改用同一套 effect_log 关联，与违例判定同一时间基准"
```

---

### Task 3: 两道守卫——节点名同源与关联写法反例

**Files:**
- Test: `tests/test_audit_assertions.py`（文件末尾追加三条）

**Interfaces:**
- Consumes: Task 1 产出的 `TERMINAL_STATUS_EFFECT_NODES`、`_seed_effect_log`；`app/graph/nodes.py` 的 `effect_confirm_profile` / `effect_abandon_profile`（**只 import 调用，⛔ 不改那个文件**）
- Produces: 无新接口，只加测试

**为什么需要这个 Task**：Task 1/2 的正确性有两个隐藏前提，都不在它们自己的测试覆盖里——① `TERMINAL_STATUS_EFFECT_NODES` 的字面量与 `nodes.py` 装饰器参数真的一致（不一致则关联恒空，全库落进 fail-closed，巡检变成一片假红）；② 关联是按数值比而不是按字符串比（Global Constraint 2）。

- [ ] **Step 1: 写节点名端到端守卫**

在 `tests/test_audit_assertions.py` **文件末尾**追加：

```python
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
```

- [ ] **Step 2: 写关联写法的两条反例**

继续在 `tests/test_audit_assertions.py` 末尾追加：

```python
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
```

- [ ] **Step 3: 跑这三条**

Run:
```bash
python -m pytest tests/test_audit_assertions.py -k "effect_nodes or effect_key_string or non_canonical" -v
```
Expected: 3 passed。

若 `test_terminal_status_effect_nodes_match_the_real_effect_nodes` 因为 `nodes.py` 的导入链（LLM gateway 之类）报 ImportError，**⛔ 不要删掉这条测试改成字面量比较**——按需在测试里补上 `pytest.importorskip` 之外的最小依赖，或把 import 移进函数体内（本 plan 已经这么写了）。真删了就等于把这道守卫从零变成同义反复。

- [ ] **Step 4: 跑全量测试套件**

Run:
```bash
python -m pytest -q
```
Expected: 全部 PASS，0 failed。

- [ ] **Step 5: Commit**

```bash
git add tests/test_audit_assertions.py
git commit -m "test(audit): 断言四补两道守卫——节点名与 nodes.py 端到端同源、关联按数值比不按字符串比"
```

---

## Self-Review

**1. Spec coverage** —— `specs/job-profile-approval/spec.md` Scenario「留痕豁免线按决策发生时刻判定」四条 bullet 逐条对应：

| spec bullet | 覆盖它的 Task / 测试 |
|---|---|
| MUST 以「进入终态动作实际发生的时刻」判定，MUST NOT 以「草案创建时刻」判定 | Task 1 Step 6-7（`_DECISION_MOMENT_SQL`）+ Task 2 Step 3（计数同源） |
| 线前创建、线后确认且缺留痕 → MUST 报违例 | Task 1 Step 2 `test_pre_cutoff_draft_confirmed_after_cutoff_is_not_exempt`（先红后绿） |
| 创建与确认均在线前 → SHALL 豁免，行为不变 | Task 1 Step 9 `test_human_review_assertion_exempts_rows_decided_before_the_cutoff` |
| 查不到决策时刻 → MUST 按未豁免处理，不得默认豁免 | Task 1 Step 2 `test_terminal_row_without_effect_log_is_not_exempt` |

`tasks.md` 9.6 的第四条测试要求（豁免计数口径）由 Task 2 Step 1 覆盖；`HUMAN_REVIEW_ENFORCED_FROM` 注释更新由 Task 1 Step 5 覆盖；`TERMINAL_STATUS_EFFECT_NODES` 与 `nodes.py` 同源的纪律由 Task 3 Step 1 覆盖（不只是注释，是可执行的守卫）。

**2. Placeholder scan** —— 无 TBD / TODO / "适当处理错误"。每个代码步骤都给了完整可粘贴的代码块与确切的 `pytest` 命令、预期输出。

**3. Type consistency** —— `TERMINAL_STATUS_EFFECT_NODES: dict[str, str]`、`_DECISION_MOMENT_SQL: str`（一个 `?` 占位符，位置在最前）、`_seed_effect_log(conn, job_id, version, node_name, applied_at)` 五参签名，Task 1/2/3 三处引用一致。violation 字典新增的键统一叫 `decided_at`。

**4. 铁律自查** —— 本单元不新增任何副作用节点，diff 里 `app/` 侧只有 `SELECT`；`effect_log` 只读不写。不涉及 AI 评分，`evidence_ref` 不适用。

## 未做的验证（如实登记）

`spec-to-plan` 技能第 6 步的「端到端提取验证」（把计划里的代码块提取到临时目录、装独立 venv 跑全量）**本次没按原样做**：本单元的改动是在既有文件里做原位替换、代码块无法脱离仓库独立成套，提取出来跑不成。

**实际做了的验证**（在仓库 venv 里对临时库跑，⛔ 没有改动任何仓库文件）：

| 验证项 | 结果 |
|---|---|
| SQLite 亲和性三种写法的差异表（「关于 Global Constraint 2」那张表） | 实跑得出，非凭记忆 |
| 场景 1 在**现有实现**下是否真的红 | 真的红：造出「线前创建 · 线后确认 · 缺留痕」后，现有 `assert_every_decision_has_human_review` 返回 `ok=True`、detail 报「豁免 1 条」 |
| Task 1 Step 7 的违例查询 SQL | 实跑，返回 `{'job_id': 'j1', 'version': 1, 'status': 'approved', 'created_at': '2026-08-01 10:00:00', 'decided_at': '2026-09-04 09:00:00'}` |
| Task 2 Step 3 的豁免计数 SQL | 实跑，同一份数据下返回 `0`（旧口径返回 1） |
| Task 3 Step 1 守卫是否跑得起来（`profile_dict={}` 会不会炸、导入链是否可用） | 实跑通过，`effect_log` 回读到 `{'j1': 'effect_confirm_profile', 'j2': 'effect_abandon_profile'}` |

**没验证的**：三个 Task 改完后的全量套件（`pytest -q`）——那要真的动代码，属 `run-build` 的事。关联 key 的四项事实（`thread_id` / `business_key` / `node_name` / `applied_at` 格式）逐条回查了源码行号但未跑过端到端 HTTP 路径。剩余风险由 `run-build` 的两阶段 review 与每个 Task 的 `pytest -m compliance` 门槛承接。

## Execution Handoff

计划已保存到 `docs/superpowers/plans/2026-09-04-m1-job-profile-intake-unit9-6-human-review-exemption-line.md`。

下一步用本项目的 `run-build` 技能执行（内部走 `superpowers:subagent-driven-development`，每个 Task 一个新鲜 subagent + 两阶段 review）。全部 Task 完成后回勾 `openspec/changes/m1-job-profile-intake/tasks.md` 的 9.6。

⛔ 本单元只改 `app/audit/assertions.py` 与两份测试，**不需要 worktree 之外的任何前置**，也不涉及 `.51` 服务器发版。
