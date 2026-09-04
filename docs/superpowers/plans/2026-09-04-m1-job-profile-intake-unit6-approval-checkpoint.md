# 确认断点（m1-job-profile-intake · 交付单元 6）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让业务经理在**看得见画像内容**的前提下做出确认 / 修改 / 放弃三选一的人工决策，每一次决策落一条 `human_review` 留痕，并由一条 CI 断言守住"决策必留痕"。

**Architecture:** 三个分支各自独占一个 `effect_*` 节点（`effect_confirm_profile` / `effect_request_revision` / `effect_abandon_profile`），三者都在**自己的事务里**把业务写与 `human_review` 留痕一次性提交；画像摘要由一个纯函数 `summarize_profile()` 在 `_deliver_node` 里算好、以「中文标签 → 中文值」的形态进 `confirmation_prompt` 的 payload，前端只负责渲染，永远拿不到英文字段名；挂起状态的可恢复性由**真开一个新进程**读同一个 SQLite 文件来验，不用 mock 冒充重启。

**Tech Stack:** Python 3.14 · FastAPI · LangGraph ≥1.0.10（SqliteSaver checkpointer）· SQLite · Pydantic v2 · pytest

---

## Global Constraints

以下每一条都逐字来自 `CLAUDE.md`（工程铁律 / 合规红线 / 部署约束）与本单元 opener。**每个 Task 的验收隐含包含本节全部内容**，reviewer 逐条当注意力透镜用。

### 工程铁律（逐字复制自 CLAUDE.md）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
   *为什么*：业务写失败而幂等记录成功 → 系统判定"已执行"→ 永不重试。**幂等本是防重复的保护，拆开事务后变成永久丢失的保证。** 实证：`.51` 现网 2026-08-10 与 08-12 各丢一轮 `outbox`（幂等记录已落），用户没收到回复且永远不会补发，见 `docs/findings/2026-08-13-sqlite-事务归属冲突.md` §8.5。
2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。

### 本单元特有的九条（逐字来自 opener）

1. 上面铁律 1、2 逐字抄。
2. **确认 / 修改 / 放弃三个分支各自是独立的 `effect_*` 节点，⛔ 不合并成一个"处理确认"节点。**
3. **`human_review` 走 `CREATE TABLE IF NOT EXISTS`（新表），⛔ 不进 `_ADDED_COLUMNS`**；列＝决策人 / 决策类型 / 时间 / 关联画像版本 / 预留 `batch_id`。`.51` 上 17 个真实 job 的既有表**一行不改**。
4. **6.1 只渲染中文字段名**（`index.html:162` 那条既有约束），画像从 payload 的 `profile_patch_accumulated` 取，**⛔ 不另加接口**；前端资源与接口一律相对路径（部署约束 1）。
5. **6.5 每一版草案都保留**（`job_profile` 新 version，不覆盖）；**6.6 上限 5 次**，超限提示转人工编辑；**6.7 `abandoned` 保留内容**。
6. **6.3 / 6.9 的"重启后可恢复"用 LangGraph SQLite checkpointer 的真实重开来验**（新进程 / 新 graph 实例按 `thread_id` 恢复），**⛔ 不用 mock 冒充重启**。
7. **6.8 挂起提醒：无定时基础设施 → ⏸ 留步：等定时基础设施（与 5.6 同源）**，**⛔ 不要自造 sleep 循环或后台线程充数**。
8. **⛔ 不碰 `app/outbound/`、`app/audit/`**——除 `app/audit/assertions.py` 追加 9.3 一条断言外；**⛔ 不改 `effect_deliver_message`、`idempotent_effect`、`effect_log`**。
9. **合规：确认是人工节点，必须留痕；⛔ 不得把 AI 评分或任何自动判定写成决策人。**

### 合规红线（逐字复制自 CLAUDE.md，与本单元相关的三条）

- **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。审计断言：`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。
- **AI 生成的 JD、拒信、邀约须带标识**（《AI 生成合成内容标识办法》2025-09-01 施行）。
- **模型全部走境内**，简历数据不出境。

### 部署约束（逐字复制自 CLAUDE.md，与本单元相关的两条）

1. **路径前缀就绪**：FastAPI `root_path=/hr/recruit-agent`，前端资源与接口调用**一律相对路径**，禁止硬编码 `/static/…` `/api/…`。验收标准是挂到任意子路径下都能正常工作，且有测试覆盖。
3. **鉴权中间件留空壳接入点**，签名对齐未来企微 OAuth SSO；将来只换实现不换调用方。

### 两条工程纪律（本单元反复会撞上，提前钉死）

- **⛔ 不引入任何新的第三方依赖。** `scripts/check_boundary.py` 的依赖 diff 检查会拦下 `requirements.txt` / `pyproject.toml` 的任何新增行（`tests/test_boundary_guard.py` 守着）。**"模拟时间推进"因此不能用 `freezegun`**，只能改库里的时间戳字符串。
- **⛔ 不用 `innerHTML`。** `index.html` 现有代码一律 `textContent` + `createElement`；画像内容是 LLM 自由生成的文本，`innerHTML` 会把它变成一条注入通道。

---

## 覆盖对照

| spec Requirement / Scenario | 落在哪个 Task |
|---|---|
| 画像确认断点 · 推送确认（Web 通道）：页面展示**画像摘要**与"确认/修改/放弃"三个操作 | Task 2 / 3（摘要）、Task 6（三个操作） |
| 画像确认断点 · 推送确认（Web 通道）：状态挂起并持久化，关页面再打开仍能继续 | Task 7 |
| 画像确认断点 · 推送确认（企微通道） | ⏸ **不在本单元**——企微通道已移出到阶段二（tasks.md 文末「已移出」）。本单元只做 Web 通道那一半 |
| 画像确认断点 · 确认后冻结（`approved` + 记录确认人与确认时间 + 进入 JD 生成） | Task 3 |
| 画像确认断点 · 流程长时间挂起（7 天仍能恢复） | Task 7 |
| 画像确认断点 · 流程长时间挂起（第 1、3 天各提醒一次） | ⏸ **留步**，见 Task 9 |
| 修改与重新生成 · 提出修改意见（基于原画像重新产出 + 保留每一版草案） | Task 4 |
| 修改与重新生成 · 修改次数上限（5 次 → 提示转人工） | Task 4 |
| 副作用幂等 · 编排引擎重跑节点 | Task 3 / 4 / 5（三个节点各自幂等） + Task 7 |
| 副作用幂等 · 回调重复到达 | Task 3 / 4 / 5（Web 通道下等价于 POST 重试） |
| 回调可靠接收（企微回调先落库再处理） | ⏸ **不在本单元**——随企微通道移出到阶段二 |
| 决策留痕 · 记录确认决策 | Task 1（表）、Task 3 / 4 / 5（三条写入路径）、Task 8（断言） |

| tasks.md 条目 | 落在哪个 Task |
|---|---|
| 1.4 建表 `human_review` | Task 1 |
| 1.6b 跨进程重启恢复的自动化验证 | Task 7（与 6.3 是同一个缺口，一起补） |
| 6.1 画像摘要渲染 | Task 2（纯函数）+ Task 3（进 payload）+ Task 6（前端渲染） |
| 6.3 挂起状态持久化，验证进程重启后可恢复 | Task 7 |
| 6.4 确认分支：冻结画像、写 version、记 `human_review`、流转下游 | Task 3 |
| 6.5 修改分支 | Task 4 |
| 6.6 修改次数上限 5 次 | Task 4 |
| 6.7 放弃分支 | Task 5 |
| 6.8 挂起提醒 | ⏸ **留步**，Task 9 只登记不实现 |
| 6.9 7 天挂起测试 | Task 7 |
| 9.3 审计断言（`human_review` 那一半） | Task 8 |

---

## File Structure

**新建**

| 文件 | 职责 |
|---|---|
| `tests/test_human_review_schema.py` | `human_review` 建表、约束、老库升级的守卫 |
| `tests/test_profile_summary.py` | `summarize_profile()` 的渲染与"不泄漏英文字段名"守卫 |
| `tests/test_approval_branches.py` | 三个分支的端到端（HTTP 层）行为与幂等 |
| `tests/test_suspend_recovery.py` | 跨进程重启恢复 + 7 天挂起（6.3 / 6.9 / 1.6b） |

**修改**

| 文件 | 改什么 |
|---|---|
| `app/storage/db.py` | `SCHEMA` 末尾追加 `human_review` 建表与唯一索引。⛔ 不动 `_ADDED_COLUMNS` |
| `app/schemas/job_profile.py` | 追加 `summarize_profile()` 与三个私有渲染助手，紧挨 `FIELD_LABELS` |
| `app/middleware/auth.py` | 追加 `UNKNOWN_REVIEWER` 常量与 `reviewer_of(request)` |
| `app/graph/nodes.py` | 追加 `DECISION_*` 常量、`_record_human_review()`、`revision_count()`、`effect_request_revision`、`effect_abandon_profile`；`effect_confirm_profile` 增加 `reviewer` 参数并写留痕 |
| `app/graph/build.py` | `_deliver_node` 的 `confirmation_prompt` payload 增加 `profile_summary` |
| `app/web/server.py` | 新增 `POST /api/jobs/{id}/revise`、`POST /api/jobs/{id}/abandon`；`/reply` `/confirm` `/revise` 加终态守卫；`/confirm` 传 `reviewer` |
| `app/web/static/index.html` | 渲染画像摘要块；确认区补"修改""放弃"两个入口与修改意见输入框 |
| `app/audit/assertions.py` | 追加断言四（9.3）并注册进 `COMPLIANCE_ASSERTIONS` |
| `tests/test_graph_nodes.py` | 两处 `effect_confirm_profile(...)` 调用补 `reviewer=` |
| `tests/test_graph_idempotency.py` | 三处 `effect_confirm_profile(...)` 调用补 `reviewer=` |
| `tests/test_audit_assertions.py` | 三条 `== 3` 的硬编码期望改 `== 4` |
| `tests/test_audit_assertion_effectiveness.py` | 一条 `len(results) == 3` 改 `== 4`；追加断言四的反证 |
| `openspec/changes/m1-job-profile-intake/tasks.md` | 回勾 1.4 / 1.6b / 6.1 / 6.3 / 6.4 / 6.5 / 6.6 / 6.7 / 6.9 / 9.3；6.8 保持未勾并加留步注记 |
| `docs/tech-debt.md` | 登记 6.8 留步与 `UNKNOWN_REVIEWER` 两条 |

---

### Task 1: `human_review` 表（tasks 1.4）

**Files:**
- Modify: `app/storage/db.py`（在 `SCHEMA` 字符串末尾、`idx_pending_approval_status` 之后追加）
- Test: `tests/test_human_review_schema.py`（新建）

**Interfaces:**
- Consumes: 无（本单元第一个 Task）
- Produces: 表 `human_review`，列 `id / job_id / profile_version / decision_type / reviewer / feedback / batch_id / decided_at`；唯一索引 `idx_human_review_decision (job_id, profile_version, decision_type)`。后续 Task 3/4/5 往这张表写，Task 8 查这张表。

- [ ] **Step 1: 写失败的测试**

新建 `tests/test_human_review_schema.py`：

```python
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
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `pytest tests/test_human_review_schema.py -v`
Expected: FAIL，`sqlite3.OperationalError: no such table: human_review`

- [ ] **Step 3: 建表**

在 `app/storage/db.py` 的 `SCHEMA` 字符串里，`CREATE INDEX IF NOT EXISTS idx_pending_approval_status ...;` 那一行**之后**、结尾的 `"""` 之前，追加：

```sql
-- ─────────────────────────────────────────────────────────────────────────
-- 人工决策留痕（m1-job-profile-intake tasks 1.4 / 6.4 / 9.3）。
-- 新表，走 CREATE TABLE IF NOT EXISTS，**不进 _ADDED_COLUMNS**：加列路径只
-- 服务"老库缺列"这一种情况，新表不需要它。.51 上 data/demo.db 的 17 个真实
-- job 与既有表一行不改，无数据迁移。
--
-- ⛔ job_id 上刻意不加外键。与 effect_log.thread_id、pending_approval.thread_id
-- 同一形态：留痕表按 thread 记事实，把它的可写性绑在业务表上，"留痕写不进去"
-- 就会变成"业务动作整个失败"——而留痕孤立远好过留痕丢失。
--
-- decision_type 的三个取值与 app/graph/nodes.py 的 DECISION_* 常量、
-- app/audit/assertions.py 断言四的 TERMINAL_STATUS_DECISIONS 逐字同源。
-- ⛔ 改任何一处都必须同步改另两处，否则留痕会静默落在一个断言查不到的取值上，
-- 而这个故障没有任何症状：不报错、不失败，只是审计那天答不出话。
--
-- reviewer 的 CHECK 是合规红线「淘汰必须有人工确认节点并留痕」在存储层的落点：
-- 决策人为空的留痕等于没留痕，且这条**由数据库强制**。trim 的第二参数显式列出
-- 空格/制表/换行/回车——SQLite 的单参 trim() 只剥空格（与 criterion_score
-- .evidence_ref 的 CHECK 同一理由）。
CREATE TABLE IF NOT EXISTS human_review (
    id TEXT PRIMARY KEY NOT NULL,
    job_id TEXT NOT NULL,
    profile_version INTEGER NOT NULL,
    decision_type TEXT NOT NULL
        CHECK (decision_type IN ('approved', 'revision_requested', 'abandoned')),
    reviewer TEXT NOT NULL CHECK (
        reviewer IS NOT NULL
        AND trim(reviewer, ' ' || char(9) || char(10) || char(13)) != ''
    ),
    feedback TEXT,
    -- M2 批量确认的预留列（tasks 1.4）。现在没有写入方，必须可空。
    batch_id TEXT,
    decided_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 重复留痕的第二道防线（第一道是 idempotent_effect）。粒度与幂等键
-- {job_id}:{node_name}:{profile_version} 完全一致——node_name 与 decision_type
-- 一一对应。两道防线粒度不一致时，宽的那道形同虚设。
-- 这条索引同时也是按 job_id 的查询索引（job_id 是最左前缀），
-- ⛔ 不要再单独建一条 (job_id) 的索引。
CREATE UNIQUE INDEX IF NOT EXISTS idx_human_review_decision
    ON human_review (job_id, profile_version, decision_type);
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_human_review_schema.py tests/test_db.py tests/test_db_migration.py tests/test_db_audit_schema.py -v`
Expected: 全部 PASS（`test_db_migration.py` 的漂移守卫必须仍绿——它盯着 `SCHEMA` 与 `_ADDED_COLUMNS` 的一致性，本次只加新表、不加列，所以它不该有任何变化）

- [ ] **Step 5: 提交**

```bash
git add app/storage/db.py tests/test_human_review_schema.py
git commit -m "feat(storage): 建 human_review 表，人工决策留痕的载体（tasks 1.4）"
```

---

### Task 2: `summarize_profile()` 纯函数（tasks 6.1 的计算半边）

**Files:**
- Modify: `app/schemas/job_profile.py`（在 `field_labels()` 之后追加）
- Test: `tests/test_profile_summary.py`（新建）

**Interfaces:**
- Consumes: `FIELD_LABELS`（同文件既有）
- Produces: `summarize_profile(profile: dict) -> list[dict]`，返回 `[{"label": 中文名, "value": 中文值}, ...]`，按 `FIELD_LABELS` 声明序，只含有值字段。Task 3 在 `_deliver_node` 里调它，Task 6 的前端直接渲染它的输出。

- [ ] **Step 1: 写失败的测试**

新建 `tests/test_profile_summary.py`：

```python
"""画像摘要渲染（tasks 6.1）。

**这是在修一处现网真实缺陷**：`confirmation_prompt` 的 payload 里
`profile_patch_accumulated` 一直有值，但前端从头到尾没有任何代码读它——
业务经理是在**看不见画像内容**的情况下点的「确认画像，生成 JD」。

本文件的核心断言只有两条，其余都是围着它们的守卫：
  ① 填满的画像必须**每个字段都渲染得出来**（漏一个 = 业务经理又看不见一个）
  ② 输出里**不许出现任何英文字段标识**（payload 里没有它，界面上就不会有它）
"""

import json

import pytest

from app.schemas.job_profile import FIELD_LABELS, summarize_profile

# 每个 JobProfile 字段都给了值的一份画像。⛔ 不要在这里省字段——它正是断言 ①
# 的输入，少一个字段就少验一个字段。
FULL_PROFILE = {
    "job_title": "底层软件工程师",
    "department": "电子电器研发部",
    "headcount": 2,
    "education_requirement": "本科及以上",
    "experience_years": "3-5年",
    "core_skills": [
        {"name": "CAN 驱动开发", "required": True},
        {"name": "Python 脚本", "required": False},
    ],
    "project_experience_requirement": "至少一个量产 ECU 项目",
    "soft_skill_keywords": ["沟通", "抗压"],
    "autosar_experience": ["CP"],
    "functional_safety": "ASIL-B",
    "mcu_family": ["TC3xx", "S32K"],
    "diag_stack": ["UDS", "CANoe"],
    "sop_projects": [
        {
            "vehicle_model": "A05 纯电",
            "sop_date": "2024-06",
            "role": "BSW 负责人",
            "is_mass_production": True,
        }
    ],
    "toolchain": ["Vector DaVinci", "Tasking"],
}


def test_every_profile_field_renders(**_):
    """断言 ①：填满的画像里，FIELD_LABELS 的每一个字段都要出现在摘要里。

    这条测试与 test_job_profile_schema.py 的标签完整性测试配成一对：那条保证
    「新字段有中文名」，这条保证「新字段真的被渲染出来」。少了这条，一个加了
    标签却渲染成空串的字段会静默从确认页上消失。
    """
    summary = summarize_profile(FULL_PROFILE)
    assert [item["label"] for item in summary] == list(FIELD_LABELS.values())
    assert all(item["value"] for item in summary), "有字段渲染成了空串"


def test_no_english_field_identifier_leaks_into_the_output():
    """断言 ②：输出里不许出现任何英文字段标识。

    前端只渲染它拿到的东西。payload 里没有英文 snake_case，界面上就不可能
    出现英文 snake_case——这比"叮嘱前端别渲染"可靠得多
    （index.html:162 那条既有约束的同一条思路）。
    """
    blob = json.dumps(summarize_profile(FULL_PROFILE), ensure_ascii=False)
    for field_name in FIELD_LABELS:
        assert field_name not in blob, f"英文字段名 {field_name} 泄漏进了摘要"


def test_empty_and_missing_fields_are_dropped():
    """"卡片可读，不堆字段"（tasks 6.1 原话）：没值的字段不占版面。"""
    summary = summarize_profile(
        {
            "job_title": "嵌入式工程师",
            "department": "",
            "mcu_family": [],
            "project_experience_requirement": None,
            "sop_projects": [],
        }
    )
    assert summary == [{"label": "岗位名称", "value": "嵌入式工程师"}]


def test_internal_keys_never_appear():
    """`_jd_text` / `_gap_acknowledgement` 是内部键，不是给人看的画像内容。

    这条不靠"记得跳过下划线"成立——`summarize_profile` 遍历的是 FIELD_LABELS
    而不是 profile 的键，所以任何不在字段表里的键**结构上**进不来。
    """
    summary = summarize_profile(
        {
            "job_title": "嵌入式工程师",
            "_jd_text": "【AI 生成】…",
            "_gap_acknowledgement": {"acknowledged": True},
            "unspecified_fields": ["toolchain"],
            "某个模型幻觉出来的键": "值",
        }
    )
    assert summary == [{"label": "岗位名称", "value": "嵌入式工程师"}]


def test_core_skills_render_with_required_marker():
    summary = summarize_profile({"core_skills": FULL_PROFILE["core_skills"]})
    assert summary == [
        {"label": "核心技能", "value": "CAN 驱动开发（必会）、Python 脚本（加分）"}
    ]


def test_sop_projects_render_as_readable_sentence():
    summary = summarize_profile({"sop_projects": FULL_PROFILE["sop_projects"]})
    assert summary == [
        {"label": "量产项目经历", "value": "A05 纯电 · SOP 2024-06 · BSW 负责人 · 已量产"}
    ]


def test_boolean_and_number_render_in_chinese():
    summary = summarize_profile({"headcount": 2})
    assert summary == [{"label": "招聘人数", "value": "2"}]


@pytest.mark.parametrize(
    "profile",
    [
        {"core_skills": "CAN 驱动"},               # 该是列表，模型给了字符串
        {"core_skills": [{"required": True}]},      # 缺 name
        {"headcount": "两个人"},                    # 该是整数，模型给了中文
        {"sop_projects": [{"vehicle_model": None}]},
        {"autosar_experience": [None, "CP"]},
        {"toolchain": {"a": 1}},                    # 该是列表，模型给了对象
    ],
)
def test_malformed_llm_output_never_raises(profile):
    """⚠️ 这个函数跑在 `_deliver_node` 里，抛异常就是**整轮对话当场失败**。

    输入是 LLM 自由生成的裸 dict，还没撞过 JobProfile 的类型约束（那要到
    POST /confirm 才发生）。任何形状都必须能渲染成字符串——渲染得难看可以接受，
    抛异常不行。
    """
    assert isinstance(summarize_profile(profile), list)
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `pytest tests/test_profile_summary.py -v`
Expected: FAIL，`ImportError: cannot import name 'summarize_profile' from 'app.schemas.job_profile'`

- [ ] **Step 3: 实现**

在 `app/schemas/job_profile.py` 末尾（`field_labels()` 之后）追加：

```python
# ── 画像摘要渲染（tasks 6.1）──────────────────────────────────────────────
#
# ⛔ 这段必须留在本文件、与 JobProfile 和 FIELD_LABELS 待在一起，理由与
# FIELD_LABELS 的注释逐字相同：加字段时，前端不会跟着改，摘要里就会少一个
# 字段——而"业务经理看不见画像内容"正是本章要修的那个故障。放在这里，漏改会被
# tests/test_profile_summary.py::test_every_profile_field_renders 当场抓到。
#
# ⛔ 输出里不含英文字段名。遍历的是 FIELD_LABELS 而不是 profile 的键：不在
# 字段表里的键（`_jd_text`、`_gap_acknowledgement`、模型幻觉出来的键）**结构上**
# 进不来，不靠"记得跳过下划线"成立。
#
# ⚠️ 本函数跑在 app/graph/build.py 的 _deliver_node 里，入参是 LLM 自由生成的
# 裸 dict（还没撞过 JobProfile 的类型约束，那要到 POST /confirm 才发生）。
# **任何形状都不许抛异常**——抛了就是整轮对话当场失败。渲染得难看可以接受。


def _render_scalar(value) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if value is None:
        return ""
    return str(value)


def _render_skill_item(item: dict) -> str:
    name = _render_scalar(item.get("name")).strip()
    if not name:
        return ""
    return f"{name}（{'必会' if item.get('required') else '加分'}）"


def _render_sop_project(item: dict) -> str:
    parts = [_render_scalar(item.get("vehicle_model")).strip() or "未命名车型"]
    sop_date = _render_scalar(item.get("sop_date")).strip()
    if sop_date:
        parts.append(f"SOP {sop_date}")
    role = _render_scalar(item.get("role")).strip()
    if role:
        parts.append(role)
    parts.append("已量产" if item.get("is_mass_production") else "未量产")
    return " · ".join(parts)


# 对象列表字段的逐项渲染器。⛔ 加了新的对象列表字段就要在这里加一行，否则会
# 落到下面那条 _render_pairs 兜底路径上——那条路径会把英文键名摆到业务经理
# 面前，是降级不是设计。
_ITEM_RENDERERS = {
    "core_skills": _render_skill_item,
    "sop_projects": _render_sop_project,
}


def _render_pairs(item: dict) -> str:
    """未登记渲染器的对象兜底。⛔ 不 str(dict)——那会把 Python 字面量
    （含引号与花括号）原样摆到业务经理面前。"""
    return "，".join(f"{key}：{_render_scalar(val)}" for key, val in item.items())


def _render_value(name: str, value) -> str:
    if isinstance(value, list):
        item_renderer = _ITEM_RENDERERS.get(name)
        rendered = []
        for item in value:
            if isinstance(item, dict):
                text = item_renderer(item) if item_renderer else _render_pairs(item)
            else:
                text = _render_scalar(item)
            text = text.strip()
            if text:
                rendered.append(text)
        return "、".join(rendered)
    if isinstance(value, dict):
        return _render_pairs(value)
    return _render_scalar(value).strip()


def summarize_profile(profile: dict) -> list[dict]:
    """画像 → `[{"label": 中文名, "value": 中文值}]`，按 FIELD_LABELS 声明序。

    只输出**有值**的字段（tasks 6.1 原话"卡片可读，不堆字段"）。
    """
    summary: list[dict] = []
    for name, label in FIELD_LABELS.items():
        if name not in profile:
            continue
        rendered = _render_value(name, profile[name])
        if not rendered:
            continue
        summary.append({"label": label, "value": rendered})
    return summary
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_profile_summary.py tests/test_job_profile_schema.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add app/schemas/job_profile.py tests/test_profile_summary.py
git commit -m "feat(schema): summarize_profile() 把画像渲染成中文标签值对（tasks 6.1）"
```

---

### Task 3: 确认分支——画像进 payload + 写 `human_review`（tasks 6.1 后端、6.4）

**Files:**
- Modify: `app/graph/build.py:58-106`（`_deliver_node` 的 `is_complete` 分支）
- Modify: `app/graph/nodes.py`（追加 `DECISION_*` 常量与 `_record_human_review()`；`effect_confirm_profile` 加 `reviewer` 参数）
- Modify: `app/middleware/auth.py`（追加 `UNKNOWN_REVIEWER` 与 `reviewer_of()`）
- Modify: `app/web/server.py:182-309`（`confirm` 取 `reviewer` 并传下去）
- Modify: `tests/test_graph_nodes.py:140`、`:796`；`tests/test_graph_idempotency.py:86`、`:89`、`:346`（补 `reviewer=`）
- Test: `tests/test_approval_branches.py`（新建，本 Task 只写确认分支那部分）

**Interfaces:**
- Consumes: `summarize_profile()`（Task 2）、`human_review` 表（Task 1）
- Produces:
  - `app.graph.nodes.DECISION_APPROVED = "approved"` / `DECISION_REVISION_REQUESTED = "revision_requested"` / `DECISION_ABANDONED = "abandoned"`
  - `app.graph.nodes._record_human_review(conn, *, job_id, profile_version, decision_type, reviewer, feedback=None) -> None`（**只能在 `effect_*` 节点函数体内调用**）
  - `app.middleware.auth.UNKNOWN_REVIEWER: str` 与 `reviewer_of(request) -> str`
  - `effect_confirm_profile(conn, *, thread_id, business_key, profile_dict, reviewer)`——**新增必填 `reviewer`**
  - `confirmation_prompt` payload 新键 `profile_summary: list[dict]`
  - Task 4 / 5 复用 `_record_human_review` 与三个常量；Task 6 渲染 `profile_summary`

- [ ] **Step 1: 写失败的测试**

新建 `tests/test_approval_branches.py`（本 Task 先写这一段，Task 4/5 往同一文件追加）：

```python
"""确认 / 修改 / 放弃三个分支的行为与留痕（tasks 6.1 / 6.4 / 6.5 / 6.6 / 6.7）。

⚠️ 三个分支**各自独占一个 effect_* 节点**，⛔ 不合并成一个"处理确认"节点。
理由是工程铁律 1 的直接推论：三条路径的对外后果完全不同（冻结并触发一次真实的
LLM 调用 / 什么都不冻结只记一笔 / 终止），合成一个节点后，"恢复时节点从头整个
重跑"会带着一个分支参数走进另一条路径，而幂等键只有一个。
"""

import json

import pytest

from tests.test_web_api import make_app, make_app_with_scripted_client

COMPLETE_PROFILE_RESPONSE = json.dumps(
    {
        "is_job_related": True,
        "is_complete": True,
        "questions": [],
        "profile_patch": {
            "job_title": "底层软件工程师",
            "department": "电子电器研发部",
            "headcount": 2,
            "education_requirement": "本科及以上",
            "experience_years": "3-5年",
            "core_skills": [{"name": "CAN 驱动开发", "required": True}],
            "project_experience_requirement": "至少一个量产 ECU 项目",
            "soft_skill_keywords": ["沟通"],
            "autosar_experience": ["CP"],
            "functional_safety": "ASIL-B",
            "mcu_family": ["TC3xx"],
            "diag_stack": ["UDS"],
            "sop_projects": [
                {
                    "vehicle_model": "A05 纯电",
                    "sop_date": "2024-06",
                    "role": "BSW 负责人",
                    "is_mass_production": True,
                }
            ],
            "toolchain": ["Vector DaVinci"],
        },
        "unspecified_fields": [],
    },
    ensure_ascii=False,
)

JD_RESPONSE = json.dumps(
    {"jd_text": "岗位职责：负责 ECU 底层软件开发。", "discriminatory_hits": []},
    ensure_ascii=False,
)


def _db_path(tmp_path) -> str:
    return str(tmp_path / "web.db")


def _rows(tmp_path, sql, params=()):
    from app.storage.db import get_connection

    conn = get_connection(_db_path(tmp_path))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _start_job(tmp_path, extra_responses=()):
    """跑到 confirmation_prompt 为止，返回 (client, job_id, payload)。"""
    client = make_app(tmp_path, [COMPLETE_PROFILE_RESPONSE, *extra_responses])
    resp = client.post("api/jobs", json={"message": "要个做 ECU 底层软件的"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["message"]["type"] == "confirmation_prompt"
    return client, body["job_id"], body["message"]["payload"]


# ── tasks 6.1：确认页上必须看得见画像 ────────────────────────────────────


def test_confirmation_payload_carries_the_profile_summary(tmp_path):
    """现网真实缺陷的回归测试。

    修复前：payload 里 profile_patch_accumulated 有值，但没有任何**可渲染的**
    形态，前端从头到尾没读过它——业务经理在看不见画像的情况下点了确认。
    """
    _client, _job_id, payload = _start_job(tmp_path)
    summary = payload["profile_summary"]
    assert summary, "confirmation_prompt 必须带上画像摘要"
    labels = [item["label"] for item in summary]
    assert "岗位名称" in labels and "核心技能" in labels
    assert {"label": "招聘人数", "value": "2"} in summary


def test_confirmation_payload_has_no_english_field_identifier_in_the_summary(tmp_path):
    """⛔ 摘要里不许出现英文字段标识（index.html:162 那条既有约束的同一条）。"""
    from app.schemas.job_profile import FIELD_LABELS

    _client, _job_id, payload = _start_job(tmp_path)
    blob = json.dumps(payload["profile_summary"], ensure_ascii=False)
    for name in FIELD_LABELS:
        assert name not in blob


def test_profile_patch_accumulated_is_still_in_the_payload(tmp_path):
    """⛔ 新增 profile_summary，**不删** profile_patch_accumulated。

    它是 GET /api/jobs/{id} 读回历史行时唯一的原始数据，删掉会让 .51 上
    17 个真实 job 的历史 confirmation_prompt 行少一块内容。
    """
    _client, _job_id, payload = _start_job(tmp_path)
    assert payload["profile_patch_accumulated"]["job_title"] == "底层软件工程师"


# ── tasks 6.4：确认分支写 human_review ───────────────────────────────────


def test_confirm_records_one_human_review(tmp_path):
    client, job_id, _payload = _start_job(tmp_path, extra_responses=[JD_RESPONSE])
    assert client.post(f"api/jobs/{job_id}/confirm").status_code == 200

    rows = _rows(
        tmp_path,
        "SELECT decision_type, reviewer, profile_version, feedback, batch_id "
        "FROM human_review WHERE job_id = ?",
        (job_id,),
    )
    assert len(rows) == 1
    decision_type, reviewer, profile_version, feedback, batch_id = rows[0]
    assert decision_type == "approved"
    assert reviewer == "unknown:web-session"  # 鉴权是空壳，见 UNKNOWN_REVIEWER
    assert profile_version == 1
    assert feedback is None and batch_id is None


def test_human_review_row_count_equals_effect_log_count_per_thread(tmp_path):
    """铁律 1 的 reviewer 判据：**每个 effect_* 节点的 effect_log 条数与其业务表
    行数按 thread 恒等**，且这条不变式有测试覆盖。这就是那个覆盖。"""
    client, job_id, _payload = _start_job(tmp_path, extra_responses=[JD_RESPONSE])
    client.post(f"api/jobs/{job_id}/confirm")

    effects = _rows(
        tmp_path,
        "SELECT COUNT(*) FROM effect_log "
        "WHERE thread_id = ? AND node_name = 'effect_confirm_profile'",
        (job_id,),
    )[0][0]
    reviews = _rows(
        tmp_path,
        "SELECT COUNT(*) FROM human_review WHERE job_id = ? AND decision_type = 'approved'",
        (job_id,),
    )[0][0]
    assert effects == reviews == 1


def test_confirm_retried_does_not_duplicate_human_review(tmp_path):
    """副作用幂等 · 回调重复到达：Web 通道下等价于 POST 被重试。"""
    client, scripted = make_app_with_scripted_client(
        tmp_path, [COMPLETE_PROFILE_RESPONSE, JD_RESPONSE]
    )
    job_id = client.post("api/jobs", json={"message": "要个做 ECU 底层软件的"}).json()["job_id"]
    assert client.post(f"api/jobs/{job_id}/confirm").status_code == 200
    calls_after_first = scripted.chat.completions.call_count
    assert client.post(f"api/jobs/{job_id}/confirm").status_code == 200

    assert scripted.chat.completions.call_count == calls_after_first, "第二次确认又调了一次 LLM"
    assert (
        _rows(tmp_path, "SELECT COUNT(*) FROM human_review WHERE job_id = ?", (job_id,))[0][0]
        == 1
    )


def test_review_and_status_share_one_transaction(tmp_path):
    """铁律 1：**幂等记录与业务写必须在同一个事务里提交**。

    造法：让留痕这一半失败（reviewer 空串撞上表上的 CHECK），断言业务写那一半
    也没有留下——status 仍是 drafting，effect_log 里没有这一条。

    ⚠️ 拆开事务的后果不是"少一条痕"，是**永久丢失**：幂等记录一旦先落，重试会
    被判定为"已执行"，那条留痕再也不会补上（`.51` 2026-08-10 / 08-12 两次丢
    outbox 就是这个形状）。
    """
    import sqlite3

    from app.graph.nodes import effect_confirm_profile
    from app.storage.db import get_connection, init_schema

    conn = get_connection(str(tmp_path / "tx.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', 'x', 'drafting')")
    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
        "VALUES ('j1-v1', 'j1', 1, 'drafting', '{}')"
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        effect_confirm_profile(
            conn, thread_id="j1", business_key="1", profile_dict={}, reviewer="   "
        )

    assert conn.execute("SELECT status FROM job_profile").fetchone()[0] == "drafting"
    assert conn.execute("SELECT status FROM job").fetchone()[0] == "drafting"
    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM human_review").fetchone()[0] == 0
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `pytest tests/test_approval_branches.py -v`
Expected: FAIL，`KeyError: 'profile_summary'` 与 `TypeError: effect_confirm_profile() missing 1 required keyword-only argument: 'reviewer'`

- [ ] **Step 3a: 鉴权层给出"决策人"**

在 `app/middleware/auth.py` 的 `AuthContext` 定义**之后**、`AuthMiddleware` 之前追加：

```python
# 决策人未知时写进 human_review.reviewer 的显式标记。
#
# 鉴权是空壳（部署约束 3），AuthContext.user_id 恒为 None。留痕必须写一个
# "决策人"，这时唯一诚实的写法是显式标注"身份未知"：
#   ⛔ 不写 NULL —— 分不清"没有决策人"和"这条漏写了"，而这两者的处置相反
#   ⛔ 不编一个人名 —— 那是伪造留痕，比不留痕更糟
#
# ⚠️ 部署约束 5：M2 起处理真实简历前，必须具备可识别到人的登录 + 访问留痕。
# 这个值出现在留痕里，就是"这条痕还追不到人"的诚实标记，已登记在
# docs/tech-debt.md。SSO 落地后 user_id 变成真实企微 userid，本常量自动不再
# 被取用，human_review 表结构与所有调用方一行不改。
UNKNOWN_REVIEWER = "unknown:web-session"


def reviewer_of(request: Request) -> str:
    """当前请求的决策人标识。SSO 落地后本函数不用改。

    ⛔ 绝不返回空串：human_review.reviewer 上有 CHECK，空串会让整条人工决策
    连同业务写一起回滚（同一事务），业务经理点确认会当场看到失败。
    """
    auth = getattr(request.state, "auth", None)
    return getattr(auth, "user_id", None) or UNKNOWN_REVIEWER
```

- [ ] **Step 3b: 留痕写入的公共函数与三个常量**

在 `app/graph/nodes.py` 的 import 之后、`compute_intake_turn` 之前追加：

```python
# 人工决策的三种类型（tasks 6.4 / 6.5 / 6.7）。
# ⛔ 三个字面量与 app/storage/db.py 的 human_review.decision_type CHECK、
# app/audit/assertions.py 断言四的 TERMINAL_STATUS_DECISIONS 逐字同源。改一处
# 必须同步改另两处——不同步的后果没有任何症状：不报错、不失败，只是留痕落在
# 一个断言查不到的取值上，审计那天才发现。
DECISION_APPROVED = "approved"
DECISION_REVISION_REQUESTED = "revision_requested"
DECISION_ABANDONED = "abandoned"


def _record_human_review(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    profile_version: int,
    decision_type: str,
    reviewer: str,
    feedback: str | None = None,
) -> None:
    """把一次人工决策写进 human_review（spec「决策留痕」）。

    ⛔ **本函数不 commit、不开事务**，只能在某个 effect_* 节点的函数体内被调用，
    与该节点的业务写落在同一个事务里、由 idempotent_effect 装饰器统一提交一次
    （工程铁律 1）。分开提交会出现"画像已确认但查不到谁确认的"，而更糟的是：
    幂等记录一旦先落，重试会被判定为"已执行"，那条留痕**永远不会补上**。

    ⛔ reviewer 不接受空白（表上有 CHECK 兜底）；**更不得写入任何自动判定的
    产物**——决策人只能是人（合规红线：AI 只做排序推荐，淘汰必须有人工确认
    节点并留痕）。

    id 取 `{job_id}-v{version}-{decision_type}`，与唯一索引
    (job_id, profile_version, decision_type) 和幂等键
    {job_id}:{node_name}:{version} **同粒度**。两道防线粒度不一致时，宽的那道
    形同虚设（与 pending_approval 的 (thread_id, content_hash) 同一理由）。
    """
    conn.execute(
        "INSERT INTO human_review "
        "(id, job_id, profile_version, decision_type, reviewer, feedback, decided_at) "
        "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
        (
            f"{job_id}-v{profile_version}-{decision_type}",
            job_id,
            profile_version,
            decision_type,
            reviewer,
            feedback,
        ),
    )
```

- [ ] **Step 3c: `effect_confirm_profile` 写留痕**

把 `app/graph/nodes.py:184-205` 的 `effect_confirm_profile` 整个替换为：

```python
@idempotent_effect("effect_confirm_profile")
def effect_confirm_profile(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    profile_dict: dict,
    reviewer: str,
) -> None:
    """
    effect_* 节点：把最新画像草案冻结为 approved，同步更新 job.status，并记一条
    human_review。business_key = 冻结的 version 号，防止同一版本被重复确认两次。

    不在这里 conn.commit() —— 理由同 effect_persist_draft：写入与 effect_log
    记录必须由 idempotent_effect 装饰器在同一个事务里一次性提交。

    2026-08-27（tasks 6.9）：profile_dict 这个入参此前只是收下不用，现在承载知情
    确认留痕（`_gap_acknowledgement`）。留痕与 status='approved' 必须落在同一条
    事务里（铁律 1）——分开写会出现"画像已确认但查不到确认时是否知情"，而这正是
    spec「使事后可以查明确认时业务经理是否知情」要杜绝的状态。

    2026-09-04（tasks 6.4）：human_review 那一条痕也进**同一个事务**。它回答的是
    另一个问题——"谁、在什么时候、确认了哪一版画像"（spec「决策留痕」）。
    ⛔ 不新增一个 effect_record_confirmation 节点去单独写它：多一个节点就多一个
    幂等键，而两个幂等键意味着"状态已 approved、留痕却缺席"是一个可达状态，
    合规上答不出话。

    profile_version 直接取 int(business_key)：调用方（app/web/server.py 的
    confirm）传的 business_key 就是被冻结的 version（与 effect_persist_draft
    用 int(business_key) 推 version 是同一条约定）。⛔ 不另加一个 version 参数
    ——同一个事实有两个入参，迟早会有一个调用点把它们传得不一致。
    """
    conn.execute(
        "UPDATE job_profile SET status = 'approved', profile_json = ? "
        "WHERE job_id = ? AND version = (SELECT MAX(version) FROM job_profile WHERE job_id = ?)",
        (json.dumps(profile_dict, ensure_ascii=False), thread_id, thread_id),
    )
    conn.execute("UPDATE job SET status = 'approved' WHERE id = ?", (thread_id,))
    _record_human_review(
        conn,
        job_id=thread_id,
        profile_version=int(business_key),
        decision_type=DECISION_APPROVED,
        reviewer=reviewer,
    )
```

- [ ] **Step 3d: 画像摘要进 payload**

在 `app/graph/build.py` 的 `_deliver_node` 里：

把 `from app.schemas.job_profile import field_labels` 改成：

```python
            from app.schemas.job_profile import field_labels, summarize_profile
```

把 `payload = {...}` 那个字面量改成：

```python
            unspecified = state.get("unspecified_fields", [])
            profile = state.get("profile_patch_accumulated", {})
            payload = {
                "type": "confirmation_prompt",
                # 原始画像照旧带上：它是 GET /api/jobs/{id} 读回历史行时唯一的
                # 原始数据，⛔ 不能因为新增了 profile_summary 就把它删掉。
                "profile_patch_accumulated": profile,
                # tasks 6.1：画像本身必须**可渲染地**出现在确认页上。
                # 修复前 profile_patch_accumulated 在 payload 里躺着但没有任何
                # 代码读它（index.html 全文 grep `profile` 零命中）——业务经理是
                # 在看不见画像内容的情况下点的「确认画像，生成 JD」。
                # 摘要在这里算好、以中文标签值对的形态下发，⛔ 不另加接口：
                # 前端拿不到英文字段名，界面上就不可能出现英文 snake_case。
                "profile_summary": summarize_profile(profile),
                "unspecified_fields": unspecified,
                "unspecified_field_labels": field_labels(unspecified),
            }
```

⚠️ **本改动会改变 `message_business_key(payload)` 的取值**（payload 多了一个键，内容哈希跟着变）。影响面已核过：`business_key` 只用于"同一轮的同一条消息不重复投递"，跨部署的重放不在既有场景里（`.51` 每次请求跑完一整轮就返回，不存在跨部署的 in-flight 轮次）。⛔ 不要为了保住旧哈希而把 `profile_summary` 排除在哈希之外——那会让"内容变了、键没变"成为可能，而那正是幂等键要防的事。

- [ ] **Step 3e: HTTP 层把决策人传下去**

`app/web/server.py`：

import 段加上（与既有 `from fastapi import APIRouter, FastAPI, HTTPException` 合并）：

```python
from fastapi import APIRouter, FastAPI, HTTPException, Request
```

```python
from app.middleware.auth import AuthMiddleware, reviewer_of
```

把 `def confirm(job_id: str, req: ConfirmRequest | None = None):` 改成：

```python
    def confirm(job_id: str, request: Request, req: ConfirmRequest | None = None):
```

把 `effect_confirm_profile(...)` 那次调用改成：

```python
        effect_confirm_profile(
            conn,
            thread_id=job_id,
            business_key=str(version),
            profile_dict=profile_dict,
            # 决策人由鉴权层给（部署约束 3 的空壳接入点）。SSO 落地后这里
            # 一行不改，reviewer_of 自动返回真实的企微 userid。
            reviewer=reviewer_of(request),
        )
```

- [ ] **Step 3f: 补齐既有测试的调用点**

`effect_confirm_profile` 新增了必填关键字参数，五处既有调用要补 `reviewer=`。**⛔ 不要给 `reviewer` 加默认值来绕过这一步**——默认值会让某个调用方将来悄悄写出一条无主留痕。

```bash
# 逐个打开改，⛔ 不要用 sed 批量替换（两个文件里的调用形态不一致）
grep -n "effect_confirm_profile(" tests/test_graph_nodes.py tests/test_graph_idempotency.py
```

- `tests/test_graph_nodes.py:140` 与 `:796`：在参数列表末尾加 `reviewer="tester"`
- `tests/test_graph_idempotency.py:86`、`:89`、`:346`：把
  `effect_confirm_profile(conn, thread_id="job1", business_key="1", profile_dict={})`
  改成
  `effect_confirm_profile(conn, thread_id="job1", business_key="1", profile_dict={}, reviewer="tester")`

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_approval_branches.py tests/test_graph_nodes.py tests/test_graph_idempotency.py tests/test_web_api.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add app/graph/build.py app/graph/nodes.py app/middleware/auth.py app/web/server.py tests/test_approval_branches.py tests/test_graph_nodes.py tests/test_graph_idempotency.py
git commit -m "feat(approval): 确认页带上画像摘要，确认分支写 human_review（tasks 6.1/6.4）"
```

---

### Task 4: 修改分支与 5 次上限（tasks 6.5 / 6.6）

**Files:**
- Modify: `app/graph/nodes.py`（追加 `MAX_REVISIONS`、`effect_request_revision`、`revision_count`）
- Modify: `app/web/server.py`（追加 `ReviseRequest` 与 `POST /api/jobs/{id}/revise`）
- Test: `tests/test_approval_branches.py`（追加）

**Interfaces:**
- Consumes: `_record_human_review` / `DECISION_REVISION_REQUESTED`（Task 3）
- Produces:
  - `app.graph.nodes.MAX_REVISIONS: int = 5`
  - `effect_request_revision(conn, *, thread_id, business_key, reviewer, feedback) -> None`
  - `revision_count(conn, job_id: str) -> int`
  - `POST /api/jobs/{job_id}/revise`，请求体 `{"feedback": "..."}`，200 返回 `{"job_id", "message"}`（形状与 `/reply` 一致）；409 超限；422 空意见
  - Task 6 的前端调这个接口

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_approval_branches.py`：

```python
# ── tasks 6.5 / 6.6：修改分支 ────────────────────────────────────────────


def test_revise_records_the_decision_and_reruns_the_turn(tmp_path):
    """修改 = 记一笔留痕 + 把修改意见当作用户这一轮的原话重跑一次采集。"""
    client, job_id, _payload = _start_job(
        tmp_path, extra_responses=[COMPLETE_PROFILE_RESPONSE]
    )
    resp = client.post(f"api/jobs/{job_id}/revise", json={"feedback": "人数改成 3 个"})
    assert resp.status_code == 200
    assert resp.json()["message"]["type"] == "confirmation_prompt"

    rows = _rows(
        tmp_path,
        "SELECT decision_type, feedback, profile_version FROM human_review WHERE job_id = ?",
        (job_id,),
    )
    assert rows == [("revision_requested", "人数改成 3 个", 1)]


def test_revise_keeps_every_draft_version(tmp_path):
    """tasks 6.5 后半：**保留每一版草案**（新 version，⛔ 不覆盖旧行）。"""
    client, job_id, _payload = _start_job(
        tmp_path, extra_responses=[COMPLETE_PROFILE_RESPONSE]
    )
    client.post(f"api/jobs/{job_id}/revise", json={"feedback": "人数改成 3 个"})

    versions = _rows(
        tmp_path, "SELECT version, status FROM job_profile WHERE job_id = ? ORDER BY version", (job_id,)
    )
    assert versions == [(1, "drafting"), (2, "drafting")]


def test_revise_sends_the_previous_profile_back_to_the_model(tmp_path):
    """tasks 6.5 前半：**基于原画像 + 修改意见**重新产出，不是从零重来。"""
    client, scripted = make_app_with_scripted_client(
        tmp_path, [COMPLETE_PROFILE_RESPONSE, COMPLETE_PROFILE_RESPONSE]
    )
    job_id = client.post("api/jobs", json={"message": "要个做 ECU 底层软件的"}).json()["job_id"]
    client.post(f"api/jobs/{job_id}/revise", json={"feedback": "人数改成 3 个"})

    last_call = json.dumps(scripted.chat.completions.calls[-1], ensure_ascii=False)
    assert "底层软件工程师" in last_call, "上一版画像没有随 prompt 一起送进去"
    assert "人数改成 3 个" in last_call, "修改意见没有进 prompt"


def test_revise_rejects_blank_feedback(tmp_path):
    """"以自然语言描述要改什么"——没写内容就不是一次修改意见。"""
    client, job_id, _payload = _start_job(tmp_path)
    assert client.post(f"api/jobs/{job_id}/revise", json={"feedback": "   "}).status_code == 422


def test_revise_retried_at_the_same_version_records_one_review(tmp_path):
    """幂等键 = {job_id}:effect_request_revision:{version}。"""
    from app.graph.nodes import effect_request_revision
    from app.storage.db import get_connection, init_schema

    conn = get_connection(str(tmp_path / "idem.db"))
    init_schema(conn)
    for _ in range(3):
        effect_request_revision(
            conn, thread_id="j1", business_key="1", reviewer="tester", feedback="改一下"
        )

    assert conn.execute("SELECT COUNT(*) FROM human_review").fetchone()[0] == 1
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM effect_log WHERE node_name = 'effect_request_revision'"
        ).fetchone()[0]
        == 1
    )


def test_revision_limit_blocks_the_sixth_request(tmp_path):
    """tasks 6.6：修改次数达到 5 次 → 提示转人工，⛔ 不再受理第 6 次。"""
    from app.graph.nodes import MAX_REVISIONS
    from app.storage.db import get_connection

    assert MAX_REVISIONS == 5

    client, job_id, _payload = _start_job(tmp_path)
    conn = get_connection(_db_path(tmp_path))
    for version in range(1, MAX_REVISIONS + 1):
        conn.execute(
            "INSERT INTO human_review "
            "(id, job_id, profile_version, decision_type, reviewer) VALUES (?, ?, ?, ?, ?)",
            (f"{job_id}-v{version}-revision_requested", job_id, version,
             "revision_requested", "tester"),
        )
    conn.commit()
    conn.close()

    resp = client.post(f"api/jobs/{job_id}/revise", json={"feedback": "再改一次"})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["revision_count"] == 5 and detail["max_revisions"] == 5
    assert "HR" in detail["message"], "超限提示必须说清下一步是转人工编辑"


def test_revision_limit_still_allows_confirming(tmp_path):
    """超限只关掉"再改一次"这条路，⛔ 不把人锁死在页面上。

    spec：「系统提示转人工，由 HR 直接编辑画像后提交确认」——确认这条路必须还在。
    """
    from app.graph.nodes import MAX_REVISIONS
    from app.storage.db import get_connection

    client, job_id, _payload = _start_job(tmp_path, extra_responses=[JD_RESPONSE])
    conn = get_connection(_db_path(tmp_path))
    for version in range(1, MAX_REVISIONS + 1):
        conn.execute(
            "INSERT INTO human_review "
            "(id, job_id, profile_version, decision_type, reviewer) VALUES (?, ?, ?, ?, ?)",
            (f"{job_id}-v{version}-revision_requested", job_id, version,
             "revision_requested", "tester"),
        )
    conn.commit()
    conn.close()

    assert client.post(f"api/jobs/{job_id}/confirm").status_code == 200


def test_cannot_revise_a_frozen_profile(tmp_path):
    """决策四：画像冻结后不可修改，如需变更必须创建新版本（＝新建岗位）。"""
    client, job_id, _payload = _start_job(tmp_path, extra_responses=[JD_RESPONSE])
    client.post(f"api/jobs/{job_id}/confirm")

    resp = client.post(f"api/jobs/{job_id}/revise", json={"feedback": "再改一下"})
    assert resp.status_code == 409
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `pytest tests/test_approval_branches.py -v -k revis`
Expected: FAIL，`404` / `ImportError: cannot import name 'effect_request_revision'`

- [ ] **Step 3a: 修改分支的 effect 节点**

在 `app/graph/nodes.py` 的 `effect_confirm_profile` **之后**追加：

```python
# 同一岗位的画像修改次数上限（tasks 6.6 / spec「修改次数上限」）。
# 超限不是失败，是**换一条路**：提示转人工，由 HR 直接编辑画像后提交确认。
# ⚠️ 上限的意义是护住业务经理的耐心（design.md 决策六：一个追问到第 8 轮的
# 机器人，下次就没人用了），⛔ 不要因为"多改几次也没坏处"把它调大。
MAX_REVISIONS = 5


@idempotent_effect("effect_request_revision")
def effect_request_revision(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    reviewer: str,
    feedback: str,
) -> None:
    """
    effect_* 节点：记一次「修改」决策，独占、幂等。
    business_key = 被打回的那一版画像 version。

    ⛔ **本节点不改画像、不改任何状态。** 修改意见的落地是下一轮采集：
    app/web/server.py 的 revise 把 feedback 当作用户这一轮的原话交给 _run_turn，
    原画像作为 profile_patch_accumulated 一起送进 prompt，产出的是 job_profile
    的**新一版**（design.md 决策四：画像改动走新版本，每一版草案都保留）。
    ⛔ 绝不 UPDATE 已有的 job_profile 行——那会让"当时按什么标准筛的"答不上来。

    ⛔ 不与 effect_confirm_profile / effect_abandon_profile 合并成一个"处理确认"
    节点。三条路径的对外后果完全不同（冻结并触发一次真实的 LLM 调用 / 什么都不
    冻结只记一笔 / 终止），合成一个节点后，"恢复时节点从头整个重跑"会带着一个
    分支参数走进另一条路径，而幂等键只有一个（工程铁律 1、2）。

    不在这里 conn.commit() —— 理由同 effect_persist_draft。
    """
    _record_human_review(
        conn,
        job_id=thread_id,
        profile_version=int(business_key),
        decision_type=DECISION_REVISION_REQUESTED,
        reviewer=reviewer,
        feedback=feedback,
    )


def revision_count(conn: sqlite3.Connection, job_id: str) -> int:
    """已提出的修改次数。**真源 = human_review 行数**，⛔ 不另存计数列。

    另存一列计数器就多一个会漂移的真源，而漂移**没有任何症状**：不报错、
    不失败，只是上限悄悄算错——业务经理要么在第 3 次就被拦下，要么改到第 8 轮
    还没人拦。这与 app/graph/state.py 里"预算计数器不放进 state 自增"是同一条
    理由。
    """
    return conn.execute(
        "SELECT COUNT(*) FROM human_review WHERE job_id = ? AND decision_type = ?",
        (job_id, DECISION_REVISION_REQUESTED),
    ).fetchone()[0]
```

- [ ] **Step 3b: `/revise` 接口**

`app/web/server.py`：

import 补上：

```python
from app.graph.nodes import (
    MAX_REVISIONS,
    effect_confirm_profile,
    effect_generate_and_persist_jd,
    effect_request_revision,
    revision_count,
)
```

在 `ConfirmRequest` 之后追加：

```python
class ReviseRequest(BaseModel):
    # 业务经理"以自然语言描述要改什么"（spec Scenario：提出修改意见）。
    feedback: str
```

在 `create_app` 内部、`_run_turn` 定义**之后**追加一个共用的终态守卫：

```python
    # 终态说明文案。⛔ 不要在这里写"请联系管理员"这类无动作的话——业务经理
    # 需要知道**下一步能做什么**，而不是知道自己撞墙了。
    _ABANDONED_DETAIL = "这个岗位已经放弃，内容保留但不再流转；如需重开请新建一个岗位。"
    _APPROVED_DETAIL = (
        "这个岗位的画像已经确认冻结，不能再修改；"
        "如需变更请新建一个岗位（画像冻结后不可原地修改，改动一律走新版本）。"
    )

    def _job_status(job_id: str) -> str:
        row = conn.execute("SELECT status FROM job WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="job not found")
        return row[0]

    def _reject_if_abandoned(job_id: str) -> None:
        """放弃是终态：⛔ 不允许继续作答、也不允许再确认。

        少了这道守卫，一个已放弃的岗位可以被 POST /reply 复活、再被确认——
        "放弃"就变成了一个只影响显示的标签，而 human_review 里那条 abandoned
        留痕会与最终 approved 的状态直接矛盾。
        """
        if _job_status(job_id) == "abandoned":
            raise HTTPException(status_code=409, detail=_ABANDONED_DETAIL)
```

`reply` 路由体开头（`if job is None` 之后）加一行 `_reject_if_abandoned(job_id)`；`confirm` 路由体开头（读 profile 行之前）也加一行 `_reject_if_abandoned(job_id)`。

⚠️ **⛔ 不要给 `/confirm` 加 `approved` 的守卫。** 重复 POST `/confirm` 返回同一份 JD 是既有契约（`effect_generate_and_persist_jd` 的幂等短路 + 从库里读回结果），加了 `approved` 守卫会把它变成 409，破坏 `tests/test_web_api.py` 里已有的重试测试。

在 `confirm` 路由**之后**追加：

```python
    @router.post("/api/jobs/{job_id}/revise")
    def revise(job_id: str, request: Request, req: ReviseRequest):
        """修改分支（tasks 6.5 / 6.6）：记一笔留痕，然后把修改意见当作用户
        这一轮的原话重跑一次采集。

        ⛔ 留痕先于重跑，且顺序不可换：先跑再记的话，_run_turn 抛异常时这次
        修改就查不到了；先记再跑，_run_turn 失败后重试会命中同一个幂等键，
        留痕不重复、采集照常补上（自愈）。
        """
        status = _job_status(job_id)
        if status == "abandoned":
            raise HTTPException(status_code=409, detail=_ABANDONED_DETAIL)
        if status == "approved":
            raise HTTPException(status_code=409, detail=_APPROVED_DETAIL)

        row = conn.execute(
            "SELECT MAX(version) FROM job_profile WHERE job_id=?", (job_id,)
        ).fetchone()
        if row is None or row[0] is None:
            raise HTTPException(status_code=404, detail="no profile draft yet")
        version = row[0]

        latest_message = channel.latest(job_id)
        if latest_message is None or latest_message.type != "confirmation_prompt":
            raise HTTPException(status_code=409, detail="画像还在追问中，直接回复即可，不必走修改")

        feedback = req.feedback.strip()
        if not feedback:
            raise HTTPException(
                status_code=422, detail="请写明要改什么，修改意见不能为空"
            )

        already = revision_count(conn, job_id)
        if already >= MAX_REVISIONS:
            # tasks 6.6：超限**不是失败**，是换一条路。⛔ 不在这里改任何状态：
            # needs_manual 队列是第 8 章的事，本单元不铺那条线。确认这条路
            # 仍然开着（spec：由 HR 直接编辑画像后提交确认）。
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        f"这个岗位的画像已经改过 {already} 次，达到上限 {MAX_REVISIONS} 次。"
                        "请由 HR 直接编辑画像后再提交确认。"
                    ),
                    "revision_count": already,
                    "max_revisions": MAX_REVISIONS,
                },
            )

        effect_request_revision(
            conn,
            thread_id=job_id,
            business_key=str(version),
            reviewer=reviewer_of(request),
            feedback=feedback,
        )
        # 重跑一轮采集。retry 语义与 POST /reply 完全一致（同一个 _run_turn）：
        # 重复提交会各自产生一版草案，这是既有行为，本单元不改。留痕那一半
        # 不受影响——它有幂等键，重复提交只记一条。
        message = _run_turn(job_id, feedback)
        return {"job_id": job_id, "message": message}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_approval_branches.py tests/test_web_api.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add app/graph/nodes.py app/web/server.py tests/test_approval_branches.py
git commit -m "feat(approval): 修改分支与 5 次上限（tasks 6.5/6.6）"
```

---

### Task 5: 放弃分支（tasks 6.7）

**Files:**
- Modify: `app/graph/nodes.py`（追加 `effect_abandon_profile`）
- Modify: `app/web/server.py`（追加 `AbandonRequest` 与 `POST /api/jobs/{id}/abandon`）
- Test: `tests/test_approval_branches.py`（追加）

**Interfaces:**
- Consumes: `_record_human_review` / `DECISION_ABANDONED`（Task 3）
- Produces:
  - `effect_abandon_profile(conn, *, thread_id, business_key, reviewer, feedback=None) -> None`
  - `POST /api/jobs/{job_id}/abandon`，请求体可选 `{"reason": "..."}`，200 返回 `{"job_id", "status": "abandoned"}`
  - Task 6 的前端调这个接口

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_approval_branches.py`：

```python
# ── tasks 6.7：放弃分支 ──────────────────────────────────────────────────


def test_abandon_sets_status_and_keeps_every_byte_of_content(tmp_path):
    """tasks 6.7 原话：置 abandoned，**保留内容**。

    放弃不是撤销——事后要能查明"当时放弃的是哪一版画像、内容长什么样"。
    """
    client, job_id, _payload = _start_job(tmp_path)
    before = _rows(
        tmp_path, "SELECT profile_json FROM job_profile WHERE job_id = ?", (job_id,)
    )

    resp = client.post(f"api/jobs/{job_id}/abandon", json={"reason": "岗位取消了"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "abandoned"

    assert (
        _rows(tmp_path, "SELECT profile_json FROM job_profile WHERE job_id = ?", (job_id,))
        == before
    ), "放弃动作动了画像内容"
    assert _rows(tmp_path, "SELECT status FROM job WHERE id = ?", (job_id,)) == [("abandoned",)]
    assert _rows(
        tmp_path, "SELECT status FROM job_profile WHERE job_id = ?", (job_id,)
    ) == [("abandoned",)]


def test_abandon_records_the_decision_with_the_reason(tmp_path):
    client, job_id, _payload = _start_job(tmp_path)
    client.post(f"api/jobs/{job_id}/abandon", json={"reason": "岗位取消了"})

    assert _rows(
        tmp_path,
        "SELECT decision_type, feedback, profile_version FROM human_review WHERE job_id = ?",
        (job_id,),
    ) == [("abandoned", "岗位取消了", 1)]


def test_abandon_without_a_reason_is_allowed(tmp_path):
    """⛔ 不强制填理由：强制填理由的表单会得到"1"和"。"。留痕的必填项是
    决策人、决策类型、时间、画像版本——理由是加分项，不是门槛。"""
    client, job_id, _payload = _start_job(tmp_path)
    assert client.post(f"api/jobs/{job_id}/abandon").status_code == 200
    assert _rows(
        tmp_path, "SELECT feedback FROM human_review WHERE job_id = ?", (job_id,)
    ) == [(None,)]


def test_abandon_is_idempotent(tmp_path):
    """重复 POST（双击、客户端超时重发、反向代理重试）只留一条痕。"""
    client, job_id, _payload = _start_job(tmp_path)
    assert client.post(f"api/jobs/{job_id}/abandon").status_code == 200
    assert client.post(f"api/jobs/{job_id}/abandon").status_code == 200

    assert _rows(
        tmp_path, "SELECT COUNT(*) FROM human_review WHERE job_id = ?", (job_id,)
    ) == [(1,)]


def test_abandoned_job_cannot_be_replied_to_or_confirmed(tmp_path):
    """放弃是终态。少了这道守卫，"放弃"就只是一个影响显示的标签——
    岗位能被 /reply 复活、再被确认，而 human_review 里那条 abandoned 留痕
    会与最终 approved 的状态直接矛盾。"""
    client, job_id, _payload = _start_job(tmp_path)
    client.post(f"api/jobs/{job_id}/abandon")

    assert client.post(f"api/jobs/{job_id}/reply", json={"message": "再改改"}).status_code == 409
    assert client.post(f"api/jobs/{job_id}/confirm").status_code == 409
    assert client.post(f"api/jobs/{job_id}/revise", json={"feedback": "改"}).status_code == 409
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `pytest tests/test_approval_branches.py -v -k abandon`
Expected: FAIL，`404 Not Found`

- [ ] **Step 3a: 放弃分支的 effect 节点**

在 `app/graph/nodes.py` 的 `revision_count` **之后**追加：

```python
@idempotent_effect("effect_abandon_profile")
def effect_abandon_profile(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    reviewer: str,
    feedback: str | None = None,
) -> None:
    """
    effect_* 节点：把岗位与当前画像版本置为 abandoned，独占、幂等。
    business_key = 被放弃的那一版画像 version。

    ⛔ **内容一字不改**（tasks 6.7 原话"置 abandoned，保留内容"）：不删
    job_profile 行、不清 profile_json、不动 conversation。放弃不是撤销——
    事后要能查明"当时放弃的是哪一版画像、内容长什么样"。

    ⛔ 不与 effect_confirm_profile / effect_request_revision 合并（理由见
    effect_request_revision 的 docstring）。

    不在这里 conn.commit() —— 理由同 effect_persist_draft：两条 UPDATE 与
    human_review 留痕、effect_log 记录必须由 idempotent_effect 装饰器在同一个
    事务里一次性提交（工程铁律 1）。
    """
    conn.execute(
        "UPDATE job_profile SET status = 'abandoned' WHERE job_id = ? AND version = ?",
        (thread_id, int(business_key)),
    )
    conn.execute("UPDATE job SET status = 'abandoned' WHERE id = ?", (thread_id,))
    _record_human_review(
        conn,
        job_id=thread_id,
        profile_version=int(business_key),
        decision_type=DECISION_ABANDONED,
        reviewer=reviewer,
        feedback=feedback,
    )
```

- [ ] **Step 3b: `/abandon` 接口**

`app/web/server.py`：import 补 `effect_abandon_profile`；在 `ReviseRequest` 之后追加：

```python
class AbandonRequest(BaseModel):
    # ⛔ 可选，不强制填：强制填理由的表单只会得到"1"和"。"。留痕的必填项是
    # 决策人 / 决策类型 / 时间 / 画像版本，理由是加分项。
    reason: str | None = None
```

在 `revise` 路由之后追加：

```python
    @router.post("/api/jobs/{job_id}/abandon")
    def abandon(job_id: str, request: Request, req: AbandonRequest | None = None):
        """放弃分支（tasks 6.7）：置 abandoned，内容一字不改。

        ⛔ 这里刻意**没有**终态守卫：重复 POST 应当幂等地返回 200（双击、
        客户端超时重发都会打到这里），由 effect_abandon_profile 的幂等键短路。
        返回 409 会让一次无害的重试在业务经理眼里变成一个错误。
        """
        row = conn.execute(
            "SELECT MAX(version) FROM job_profile WHERE job_id=?", (job_id,)
        ).fetchone()
        if row is None or row[0] is None:
            raise HTTPException(status_code=404, detail="no profile draft yet")

        reason = (req.reason or "").strip() if req else ""
        effect_abandon_profile(
            conn,
            thread_id=job_id,
            business_key=str(row[0]),
            reviewer=reviewer_of(request),
            feedback=reason or None,
        )
        return {"job_id": job_id, "status": "abandoned"}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_approval_branches.py tests/test_web_api.py tests/test_graph_idempotency.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add app/graph/nodes.py app/web/server.py tests/test_approval_branches.py
git commit -m "feat(approval): 放弃分支置 abandoned 并保留内容（tasks 6.7）"
```

---

### Task 6: 前端——画像摘要 + 三个操作（tasks 6.1 前端、6.5 / 6.7 的入口）

**Files:**
- Modify: `app/web/static/index.html`
- Test: `tests/test_static_frontend.py`（追加）

**Interfaces:**
- Consumes: `confirmation_prompt` payload 的 `profile_summary`（Task 3）、`POST .../revise`（Task 4）、`POST .../abandon`（Task 5）
- Produces: 无（终端）

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_static_frontend.py`：

```python
def test_confirmation_branch_renders_the_profile_summary():
    """tasks 6.1：确认页必须渲染画像本身。

    修复前 index.html 全文 grep `profile` 零命中——payload 里画像躺着，
    界面上一个字都没有。这条断言盯着"前端真的读了那份数据"。
    """
    assert "profile_summary" in INDEX_HTML
    assert "renderProfileSummary" in INDEX_HTML


def test_profile_summary_is_rendered_with_textcontent_only():
    """⛔ 画像内容是 LLM 自由生成的文本，innerHTML 会把它变成一条注入通道。"""
    assert "innerHTML" not in INDEX_HTML


def test_all_three_approval_actions_have_an_entry():
    """spec：页面展示画像摘要与"确认/修改/放弃"三个操作。"""
    for element_id in ("confirm-btn", "revise-btn", "abandon-btn"):
        assert f'id="{element_id}"' in INDEX_HTML


def test_revise_and_abandon_use_relative_paths():
    """部署约束 1：接口调用一律相对路径。

    test_index_html_has_no_absolute_paths 已经全局扫过一遍字符串字面量，
    这条是针对本次两个新调用点的定点复核——两条一起红比只有一条红更好排查。
    """
    assert "api/jobs/${jobId}/revise" in INDEX_HTML
    assert "api/jobs/${jobId}/abandon" in INDEX_HTML
    assert "/api/jobs/${jobId}/revise" not in INDEX_HTML.replace("`api/jobs", "`X")
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `pytest tests/test_static_frontend.py -v`
Expected: FAIL，`assert 'profile_summary' in INDEX_HTML`

- [ ] **Step 3a: 样式与骨架**

在 `<style>` 块末尾（`.gap-warning button { … }` 之后）追加：

```css
  .profile-summary { border: 1px solid #0d6efd; border-radius: 8px; padding: 14px 16px; margin: 16px 0 8px; }
  .profile-summary h3 { margin: 0 0 10px; font-size: 15px; }
  .profile-summary dl { margin: 0; display: grid; grid-template-columns: max-content 1fr; gap: 6px 16px; }
  .profile-summary dt { font-weight: 600; color: #495057; }
  .profile-summary dd { margin: 0; white-space: pre-wrap; }
  .approval-actions { margin-top: 8px; }
  .approval-actions button { margin: 0 8px 0 0; }
  #revise-box { display: none; margin-top: 8px; }
```

把 `<div id="gap-warning" …>` 与 `<button id="confirm-btn" …>` 那两行替换为：

```html
  <div id="profile-summary" class="profile-summary" style="display:none;"></div>
  <div id="gap-warning" class="gap-warning" style="display:none;"></div>
  <div id="approval-actions" class="approval-actions" style="display:none;">
    <button id="confirm-btn" style="display:none;">确认画像，生成 JD</button>
    <button id="revise-btn">我要改一下</button>
    <button id="abandon-btn">放弃这个岗位</button>
  </div>
  <div id="revise-box">
    <textarea id="revise-input" rows="2" placeholder="要改什么？例如：人数改成 3 个，学历放宽到大专"></textarea>
    <button id="revise-submit">提交修改意见</button>
  </div>
```

- [ ] **Step 3b: 渲染画像摘要**

在 `renderGapWarning` 函数**之前**插入：

```js
    // 画像摘要（tasks 6.1）。**这是在修一处现网真实缺陷**：修复前这个分支只渲染
    // 「画像已收集完整，请确认。」加一行未指定字段，从头到尾没有任何代码读画像
    // 内容——业务经理是在看不见画像的情况下点的「确认画像，生成 JD」。
    //
    // ⛔ 全程 textContent + createElement，不用 innerHTML：画像内容是 LLM 自由
    // 生成的文本。
    // ⛔ 也不从 payload.profile_patch_accumulated 自己拼——那份是英文键名的原始
    // dict，前端一旦读它，界面上迟早会出现英文 snake_case（index.html:162 那条
    // 既有约束）。中文标签值对由后端 summarize_profile() 算好下发。
    const PROFILE_SUMMARY_MISSING_HINT =
      "这条确认记录是旧版本产生的，没有随消息带上画像内容。请在下方输入框补一句话，" +
      "让系统重新整理一遍画像，再确认。";

    function renderProfileSummary(summary) {
      const box = document.getElementById("profile-summary");
      box.textContent = "";

      if (!summary || summary.length === 0) {
        // 历史 confirmation_prompt 行（.51 上 2026-09-04 之前写的）没有
        // profile_summary 这个键，GET /api/jobs/{id} 会把它们原样读回新前端。
        // ⛔ 不静默留白：留白会让业务经理以为"这就是全部内容"，那恰恰是他在
        // 看不见画像的情况下点确认的原样重演。
        const hint = document.createElement("div");
        hint.className = "consequence";
        hint.textContent = PROFILE_SUMMARY_MISSING_HINT;
        box.appendChild(hint);
        box.style.display = "block";
        return;
      }

      const title = document.createElement("h3");
      title.textContent = "这是即将冻结的岗位画像，请逐条核对：";
      box.appendChild(title);

      const list = document.createElement("dl");
      summary.forEach((item) => {
        const dt = document.createElement("dt");
        dt.textContent = item.label;
        const dd = document.createElement("dd");
        dd.textContent = item.value;
        list.appendChild(dt);
        list.appendChild(dd);
      });
      box.appendChild(list);
      box.style.display = "block";
    }

    function hideApprovalUi() {
      ["profile-summary", "gap-warning", "approval-actions", "revise-box"].forEach((id) => {
        document.getElementById(id).style.display = "none";
      });
      document.getElementById("confirm-btn").style.display = "none";
    }
```

- [ ] **Step 3c: 接进 `renderMessage`**

把 `renderMessage` 里两个分支的收尾改成：

```js
    function renderMessage(message) {
      if (message.type === "question") {
        const questions = message.payload.questions || [];
        if (questions.length === 0) {
          appendTurn("assistant", message.payload.questions_text || "");
          activeQuestions = null;
        } else {
          const turn = appendNode("assistant");
          questions.forEach((q) => renderQuestionBlock(turn, q));
          activeQuestions = turn;
        }
        hideApprovalUi();
      } else if (message.type === "confirmation_prompt") {
        appendTurn("assistant", "画像已收集完整，请逐条核对后确认。");
        renderProfileSummary(message.payload.profile_summary);
        renderGapWarning(
          message.payload.unspecified_fields || [],
          message.payload.unspecified_field_labels || []
        );
        // 三个操作始终一起出现（spec：确认/修改/放弃）。⛔ 有缺口时也不能只留
        // 确认一条路——renderGapWarning 只管收起「确认」按钮，"修改"和"放弃"
        // 任何时候都在，否则业务经理会被卡在一个没有出口的页面上。
        document.getElementById("approval-actions").style.display = "block";
        document.getElementById("revise-box").style.display = "none";
      }
    }
```

`renderGapWarning` 里的两处 `confirmBtn.style.display` 逐字不变（它只管确认按钮）；「回去补答」那个分支里的 `box.style.display = "none"` 之后，补一行把画像摘要也收起来：

```js
        document.getElementById("profile-summary").style.display = "none";
        document.getElementById("approval-actions").style.display = "none";
```

- [ ] **Step 3d: 修改与放弃两个动作**

在文件末尾 `document.getElementById("confirm-btn").addEventListener(...)` 那一行**之前**插入：

```js
    // ── 修改分支（tasks 6.5 / 6.6）──────────────────────────────────────
    document.getElementById("revise-btn").addEventListener("click", () => {
      const box = document.getElementById("revise-box");
      box.style.display = "block";
      document.getElementById("revise-input").focus();
    });

    document.getElementById("revise-submit").addEventListener("click", async () => {
      const input = document.getElementById("revise-input");
      const feedback = input.value.trim();
      if (!feedback) return;

      appendTurn("user", feedback);
      input.value = "";
      hideApprovalUi();

      const resp = await fetch(`api/jobs/${jobId}/revise`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ feedback: feedback }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        // 409 超限时 detail 是对象（带 revision_count / max_revisions），
        // 422 空意见时 detail 是字符串。两种都要说人话。
        const detail = data && data.detail;
        const reason =
          typeof detail === "string"
            ? detail
            : (detail && detail.message) || "修改提交失败，请稍后重试。";
        appendTurn("assistant", "⚠️ " + reason);
        // 超限之后**确认那条路仍然开着**（spec：由 HR 直接编辑画像后提交确认），
        // 所以把确认区放回来，⛔ 不要把人锁死在一个没有出口的页面上。
        document.getElementById("approval-actions").style.display = "block";
        return;
      }
      renderMessage(data.message);
    });

    // ── 放弃分支（tasks 6.7）────────────────────────────────────────────
    document.getElementById("abandon-btn").addEventListener("click", async () => {
      // 放弃是终态且不可撤销，二次确认不是多余的一步。⛔ 不做成"点一下就放弃"。
      if (!window.confirm("放弃后这个岗位不再流转（内容会保留）。确定放弃吗？")) return;

      const resp = await fetch(`api/jobs/${jobId}/abandon`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "" }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        const detail = data && data.detail;
        appendTurn("assistant", "⚠️ " + (typeof detail === "string" ? detail : "放弃失败，请稍后重试。"));
        return;
      }
      hideApprovalUi();
      appendTurn("assistant", "已放弃这个岗位。已采集的内容保留在系统里，可供事后查阅。");
      document.getElementById("send-btn").disabled = true;
      document.getElementById("input").disabled = true;
    });
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_static_frontend.py tests/test_web_api.py -v`
Expected: 全部 PASS（`test_index_html_has_no_absolute_paths` 必须仍绿——两个新 `fetch` 用的都是不带开头 `/` 的相对路径）

- [ ] **Step 5: 提交**

```bash
git add app/web/static/index.html tests/test_static_frontend.py
git commit -m "feat(web): 确认页渲染画像摘要，补齐修改与放弃两个入口（tasks 6.1/6.5/6.7）"
```

---

### Task 7: 挂起状态跨进程恢复与 7 天挂起（tasks 6.3 / 6.9 / 1.6b）

**Files:**
- Test: `tests/test_suspend_recovery.py`（新建）

**Interfaces:**
- Consumes: 前六个 Task 的全部产物
- Produces: 无（纯验证 Task）

⚠️ **本 Task 只写测试，不改产品代码。** 6.3 / 1.6b 的原文都写明"持久化那一半是成立的，缺的是**没有任何测试断言过**"。如果测试写出来是红的，说明持久化那一半其实没成立——那时**停下来登记，⛔ 不要顺手改 checkpointer 或 `_run_turn` 来把测试变绿**，那已经超出本单元范围。

- [ ] **Step 1: 写失败的测试**

新建 `tests/test_suspend_recovery.py`：

```python
"""挂起状态的可恢复性（tasks 6.3 / 6.9 / 1.6b）。

⚠️ **这三条缺的从来不是实现，是断言。** tasks.md 原话：持久化那一半是成立的
（画像草案、对话记录、outbox 全在 SQLite），但"验证进程重启后可恢复"没有任何
测试断言过。checkpoint 结构上重启可恢复——"结构上可以"和"验过了"是两件事。

⛔ 不用 mock 冒充重启（opener 约束 6）。两个层次各验一遍：
  ① 真开一个**新的操作系统进程**，只给它数据库路径，看它能不能把挂起的
     thread 读回来（LLM 网关换成一调用就炸的假货 —— 状态必须完全来自磁盘）
  ② 同一进程内**新建一套 app / conn / graph / checkpointer**，走完整的 HTTP
     路径把确认做完（这一层验的是"用户关掉页面第二天再打开"）

⛔ 不引 freezegun 之类的时间库做"7 天推进"：scripts/check_boundary.py 的依赖
diff 检查会拦下 requirements.txt 的任何新增行。改库里的时间戳字符串就够了，
而且更接近真实——真实场景里变老的正是这些行。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from app.storage.db import get_connection
from tests.test_approval_branches import COMPLETE_PROFILE_RESPONSE, JD_RESPONSE
from tests.test_web_api import make_app_with_scripted_client

REPO_ROOT = Path(__file__).resolve().parents[1]

# 新进程里跑的探针。它**只读磁盘**：网关是一调用就抛 AssertionError 的假货，
# 所以"能读回挂起状态"这个结论不可能是 LLM 又跑了一遍伪造出来的。
_RECOVERY_PROBE = '''
import json
import sys

from app.channels.web_channel import WebChannel
from app.graph.build import build_intake_graph
from app.llm.gateway import LLMGateway
from app.storage.db import get_connection, init_schema


class _ExplodingCompletions:
    def create(self, **kwargs):
        raise AssertionError("恢复路径不该调用 LLM：挂起状态必须完全来自磁盘")


class _ExplodingChat:
    completions = _ExplodingCompletions()


class _ExplodingClient:
    chat = _ExplodingChat()


db_path, job_id = sys.argv[1], sys.argv[2]
conn = get_connection(db_path)
init_schema(conn)
graph = build_intake_graph(
    db_path,
    gateway=LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=_ExplodingClient(),
    ),
    conn=conn,
    channel=WebChannel(conn),
)
snapshot = graph.get_state({"configurable": {"thread_id": job_id}})
values = snapshot.values or {}
latest = WebChannel(conn).latest(job_id)
print(
    json.dumps(
        {
            "has_checkpoint": bool(values),
            "is_complete": bool(values.get("is_complete")),
            "job_title": values.get("profile_patch_accumulated", {}).get("job_title"),
            "history_len": len(values.get("history", [])),
            "latest_type": None if latest is None else latest.type,
            "summary_labels": [
                item["label"]
                for item in (latest.payload.get("profile_summary", []) if latest else [])
            ],
        },
        ensure_ascii=False,
    )
)
'''


def _suspend_a_job(tmp_path) -> str:
    """跑到 confirmation_prompt 就停手，然后把整个应用关干净（触发 lifespan
    shutdown，checkpointer 的独立连接在那里关闭）。返回 job_id。"""
    client, _scripted = make_app_with_scripted_client(tmp_path, [COMPLETE_PROFILE_RESPONSE])
    with client:
        body = client.post("api/jobs", json={"message": "要个做 ECU 底层软件的"}).json()
        assert body["message"]["type"] == "confirmation_prompt"
        return body["job_id"]


def _rewind_seven_days(db_path: str) -> None:
    """把所有业务时间戳往前推 7 天，模拟"挂起了一周没人管"。

    ⛔ 不碰 LangGraph 自己的 checkpoint 表：那些是编排引擎的内部结构，
    伪造它的时间等于在测一个我们没有契约的东西。业务侧变老就够了——
    spec 关心的是"挂起状态不因超时而丢失"，而超时判定要看的正是这些行。
    """
    conn = get_connection(db_path)
    for table, column in (
        ("job_profile", "created_at"),
        ("job_profile", "turn_started_at"),
        ("conversation", "updated_at"),
        ("effect_log", "applied_at"),
        ("outbox", "created_at"),
    ):
        conn.execute(
            f"UPDATE {table} SET {column} = datetime({column}, '-7 days') "
            f"WHERE {column} IS NOT NULL"
        )
    conn.commit()
    conn.close()


def test_a_brand_new_process_recovers_the_suspended_thread(tmp_path):
    """tasks 1.6b / 6.3：**跨进程**按 thread_id 恢复。

    既有的 test_graph_replay_from_scratch_does_not_duplicate_effects 验的是
    "同进程内同 thread_id 重复 invoke 不重复产生副作用"，**不是**这件事。
    """
    job_id = _suspend_a_job(tmp_path)
    probe = tmp_path / "probe.py"
    probe.write_text(_RECOVERY_PROBE, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(probe), str(tmp_path / "web.db"), job_id],
        cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        capture_output=True,
        text=True,
        check=True,
    )
    recovered = json.loads(result.stdout.strip().splitlines()[-1])

    assert recovered["has_checkpoint"], "新进程按 thread_id 读不回 checkpoint"
    assert recovered["is_complete"] is True
    assert recovered["job_title"] == "底层软件工程师"
    assert recovered["history_len"] >= 2, "对话历史没跨进程活下来"
    assert recovered["latest_type"] == "confirmation_prompt"
    assert "岗位名称" in recovered["summary_labels"], "画像摘要没跨进程活下来"


def test_reopening_the_page_after_seven_days_still_confirms(tmp_path):
    """tasks 6.9 / spec「流程长时间挂起」：挂起 7 天后仍能正确恢复，
    挂起状态不因超时而丢失。"""
    job_id = _suspend_a_job(tmp_path)
    _rewind_seven_days(str(tmp_path / "web.db"))

    # 全新一套 app / conn / graph / checkpointer，指向同一个数据库文件。
    client, _scripted = make_app_with_scripted_client(tmp_path, [JD_RESPONSE])
    with client:
        got = client.get(f"api/jobs/{job_id}")
        assert got.status_code == 200
        assert got.json()["message"]["type"] == "confirmation_prompt"
        assert got.json()["message"]["payload"]["profile_summary"], "7 天后画像摘要丢了"

        confirmed = client.post(f"api/jobs/{job_id}/confirm")
        assert confirmed.status_code == 200
        assert confirmed.json()["jd_text"]

    conn = get_connection(str(tmp_path / "web.db"))
    try:
        assert conn.execute(
            "SELECT decision_type FROM human_review WHERE job_id = ?", (job_id,)
        ).fetchall() == [("approved",)]
    finally:
        conn.close()


def test_idempotency_survives_seven_days(tmp_path):
    """幂等键**不因时间流逝而过期**。

    effect_log 若被按时间清理（1.7 那条清理任务将来会做），7 天后的重试就会
    重新执行一次副作用。这条断言把"清理任务不得动未完结流程的 effect_log"
    这个约束提前钉住。
    """
    job_id = _suspend_a_job(tmp_path)
    _rewind_seven_days(str(tmp_path / "web.db"))

    client, scripted = make_app_with_scripted_client(tmp_path, [JD_RESPONSE])
    with client:
        assert client.post(f"api/jobs/{job_id}/confirm").status_code == 200
        calls = scripted.chat.completions.call_count
        assert client.post(f"api/jobs/{job_id}/confirm").status_code == 200
        assert scripted.chat.completions.call_count == calls, "7 天后的重试又调了一次 LLM"


def test_revise_and_abandon_also_survive_a_restart(tmp_path):
    """三个分支都要能在重启后走完，⛔ 不只验确认那一条。"""
    job_id = _suspend_a_job(tmp_path)

    client, _scripted = make_app_with_scripted_client(tmp_path, [])
    with client:
        assert client.post(f"api/jobs/{job_id}/abandon").status_code == 200

    conn = get_connection(str(tmp_path / "web.db"))
    try:
        assert conn.execute("SELECT status FROM job WHERE id = ?", (job_id,)).fetchone()[0] == (
            "abandoned"
        )
    finally:
        conn.close()
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `pytest tests/test_suspend_recovery.py -v`
Expected: FAIL —— 第一次跑时因为 `tests/test_approval_branches.py` 的导入或探针路径还没就位；把失败信息读完再往下走。

⚠️ **这一步与其他 Task 不同：这里的"失败"不是预期设计，是一次真实检验。** 如果失败原因是"新进程读不回 checkpoint"或"7 天后状态丢了"，**停下来登记，⛔ 不要改产品代码把它变绿**——那说明 6.3 的前半（持久化）其实不成立，属于本单元范围之外的新发现，要单独立项。

- [ ] **Step 3: 让它跑通**

只允许调整测试自身（探针脚本的 import 路径、`PYTHONPATH`、`with client:` 的生命周期）。⛔ 产品代码一行不改。

如果 `subprocess.run(..., check=True)` 报非零退出码，先看 `result.stderr`：

```bash
pytest tests/test_suspend_recovery.py::test_a_brand_new_process_recovers_the_suspended_thread -v -s
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_suspend_recovery.py tests/test_graph_idempotency.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add tests/test_suspend_recovery.py
git commit -m "test(recovery): 跨进程重启与 7 天挂起的真实恢复断言（tasks 6.3/6.9/1.6b）"
```

---

### Task 8: 审计断言「每次人工决策都有 `human_review` 记录」（tasks 9.3）

**Files:**
- Modify: `app/audit/assertions.py`（追加断言四并注册）
- Modify: `tests/test_audit_assertions.py`（三条 `== 3` 改 `== 4`，追加正例）
- Modify: `tests/test_audit_assertion_effectiveness.py`（一条 `== 3` 改 `== 4`，追加反证）
- Test: 上面两份

**Interfaces:**
- Consumes: `human_review` 表（Task 1）、三条写入路径（Task 3/4/5）
- Produces: `assert_every_decision_has_human_review(conn) -> AssertionResult`；`COMPLIANCE_ASSERTIONS` 从 3 条变 4 条

⚠️ **⛔ 本 Task 只碰 `app/audit/assertions.py` 一个文件**（opener 约束 8）。`app/outbound/`、`app/audit/` 的其余模块一行不动。

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_audit_assertions.py`（并把 `:188` `:189` `:192` 三处 `== 3` 改成 `== 4`）：

```python
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


def test_human_review_assertion_exempts_rows_written_before_the_cutoff(conn):
    """.51 上的历史行（留痕上线之前确认的）豁免，但**豁免条数必须报出来**。

    ⛔ 静默跳过是不行的：那样"0 违例"这个绿色会同时兼容"都留痕了"和
    "全被豁免了"，而这两者的处置完全相反。
    """
    from app.audit.assertions import assert_every_decision_has_human_review

    _seed_terminal_profile(conn, status="approved", created_at="2026-08-01 10:00:00")
    result = assert_every_decision_has_human_review(conn)
    assert result.ok
    assert "1" in result.detail and "豁免" in result.detail
```

追加到 `tests/test_audit_assertion_effectiveness.py`（并把 `:306` 的 `== 3` 改成 `== 4`）：

```python
# ── 反证四（9.3）：决策留痕缺失必须被抓到 ────────────────────────────────


def test_missing_human_review_is_caught(conn):
    """造违例：画像已 approved，human_review 里一条都没有 → 断言必须失败。

    这正是本单元开工前 `.51` 的真实状态——"谁在什么时候确认了哪一版画像"
    答不出来。断言存在的全部意义就是让这个状态在 CI 里红。
    """
    from app.audit.assertions import assert_every_decision_has_human_review
    from tests.test_audit_assertions import _seed_terminal_profile

    _seed_terminal_profile(conn, status="approved")
    result = assert_every_decision_has_human_review(conn)

    assert result.ok is False
    assert result.violations, "断言失败时必须指出违例记录，⛔ 不许只报一个 False"
    assert result.violations[0]["job_id"] == "j1"


def test_missing_human_review_table_fails_closed(conn):
    """表不存在 → **失败**，⛔ 不是"还没到能验证的时候"。

    与断言一（rejection_record）刻意相反：那张表要到 M2 才建，human_review 是
    m1-job-profile-intake 本包建的。它不存在只有一个解释——留痕路径没上线。
    """
    from app.audit.assertions import assert_every_decision_has_human_review

    conn.execute("DROP TABLE human_review")
    result = assert_every_decision_has_human_review(conn)
    assert result.ok is False and result.violations


def test_missing_reviewer_column_fails_closed(conn):
    """缺列 → 失败：验不了红线不算守住了红线（与断言一的缺列分支同一处置）。"""
    from app.audit.assertions import assert_every_decision_has_human_review

    conn.execute("DROP TABLE human_review")
    conn.execute(
        "CREATE TABLE human_review (id TEXT PRIMARY KEY, job_id TEXT, "
        "profile_version INTEGER, decision_type TEXT)"
    )
    result = assert_every_decision_has_human_review(conn)
    assert result.ok is False
    assert "reviewer" in str(result.violations)
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `pytest tests/test_audit_assertions.py tests/test_audit_assertion_effectiveness.py -v`
Expected: FAIL，`ImportError: cannot import name 'assert_every_decision_has_human_review'` 与 `assert 3 == 4`

- [ ] **Step 3: 实现断言四**

在 `app/audit/assertions.py` 的「── 三条一起跑 ──」分隔线**之前**插入：

```python
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

# 留痕上线日（UTC，与 datetime('now') 同格式）。早于此刻创建的画像版本豁免。
HUMAN_REVIEW_ENFORCED_FROM = "2026-09-04 00:00:00"

_REQUIRED_HUMAN_REVIEW_COLUMNS = frozenset(
    {"job_id", "profile_version", "decision_type", "reviewer"}
)

ASSERTION_HUMAN_REVIEW_PRESENT = "每一个进入终态的画像版本都有对应的 human_review 记录"


def assert_every_decision_has_human_review(
    conn: sqlite3.Connection,
) -> AssertionResult:
    """合规红线「淘汰必须有人工确认节点并留痕」+ spec「决策留痕」的机器判据。

    四条分支，处置各不相同：
      表不存在        → **失败**（本包建的表，缺表 = 留痕路径没上线）
      表存在、缺列    → **失败**（fail-closed：验不了红线不算守住了红线）
      有终态无留痕    → 失败，violations 逐条给出 job_id 与 version
      全部有留痕      → 通过，detail 里报出被豁免的历史行条数
    """
    if not _table_exists(conn, HUMAN_REVIEW_TABLE):
        return AssertionResult(
            name=ASSERTION_HUMAN_REVIEW_PRESENT,
            ok=False,
            violations=(
                {"table": HUMAN_REVIEW_TABLE, "problem": "表不存在，人工决策留痕路径没有上线"},
            ),
            detail=(
                f"{HUMAN_REVIEW_TABLE} 表不存在。⛔ 这**不是**"还没到能验证的时候"——"
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
    # ⛔ 按 sorted 遍历而不是按 dict 顺序：违例清单的顺序要稳定，否则两次巡检
    # 的输出 diff 里会混进无意义的行序变化。
    for status, decision in sorted(TERMINAL_STATUS_DECISIONS.items()):
        rows = _rows(
            conn,
            f"SELECT p.job_id, p.version, p.status, p.created_at FROM {JOB_PROFILE_TABLE} p "
            "WHERE p.status = ? AND p.created_at >= ? AND NOT EXISTS ("
            f"  SELECT 1 FROM {HUMAN_REVIEW_TABLE} h "
            "  WHERE h.job_id = p.job_id AND h.profile_version = p.version "
            "    AND h.decision_type = ?"
            ")",
            (status, HUMAN_REVIEW_ENFORCED_FROM, decision),
        )
        violations.extend({**row, "expected_decision": decision} for row in rows)

    exempted = _rows(
        conn,
        f"SELECT COUNT(*) AS n FROM {JOB_PROFILE_TABLE} "
        "WHERE status IN ('approved', 'abandoned') AND created_at < ?",
        (HUMAN_REVIEW_ENFORCED_FROM,),
    )[0]["n"]

    return AssertionResult(
        name=ASSERTION_HUMAN_REVIEW_PRESENT,
        ok=not violations,
        violations=tuple(violations),
        detail=(
            f"豁免 {exempted} 条早于 {HUMAN_REVIEW_ENFORCED_FROM} 的历史画像版本"
            "（留痕上线之前确认的，不可能有记录）。"
            "⚠️ 这些行**不代表红线守住了**，只代表它们产生于留痕存在之前。"
        ),
    )
```

把 `COMPLIANCE_ASSERTIONS` 改成：

```python
COMPLIANCE_ASSERTIONS: tuple[Callable[[sqlite3.Connection], AssertionResult], ...] = (
    assert_no_ai_score_rejections,
    assert_no_blank_evidence_ref,
    assert_no_unlisted_criterion_key,
    assert_every_decision_has_human_review,
)
```

`run_compliance_assertions` 的 docstring 里「三条全部成立才通过」改成「四条全部成立才通过」，模块 docstring 里「三条断言在空表上全部恒真」那段补一句：

```
**断言四是例外**：它在空表上恒真，但**表不存在时判失败**——human_review 由
m1-job-profile-intake 建，缺表就是留痕没上线，不是"还没到能验证的时候"。
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_audit_assertions.py tests/test_audit_assertion_effectiveness.py tests/test_compliance_cli.py -v -m ""`
Expected: 全部 PASS

再单独跑一次合规标记那一路，确认 CI 的两条路径都绿：

Run: `pytest -m compliance -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add app/audit/assertions.py tests/test_audit_assertions.py tests/test_audit_assertion_effectiveness.py
git commit -m "feat(audit): 断言四——每次人工决策都有 human_review 记录（tasks 9.3）"
```

---

### Task 9: 回勾 WBS、6.8 留步登记、全量验证

**Files:**
- Modify: `openspec/changes/m1-job-profile-intake/tasks.md`
- Modify: `docs/tech-debt.md`
- Test: 全量

**Interfaces:**
- Consumes: Task 1-8 的全部产物
- Produces: 无（终端）

- [ ] **Step 1: 跑全量测试**

Run: `pytest -v`
Expected: 全部 PASS，**⛔ 一条都不许 skip 掉**（skip 掉的用例在报告里和通过长得一模一样）

Run: `pytest -m compliance -v`
Expected: 全部 PASS

Run: `python -m scripts.check_boundary` （若该脚本有独立入口；否则 `pytest tests/test_boundary_guard.py -v`）
Expected: 依赖 diff 零行——本单元**没有引入任何新依赖**

- [ ] **Step 2: 回勾 tasks.md**

在 `openspec/changes/m1-job-profile-intake/tasks.md` 里把下面这些条目从 `- [ ]` 改成 `- [x]`，并在条目后追加一句说明落在哪：

- `1.4` → `→ **已实现**，见 `app/storage/db.py` 的 `SCHEMA`：`human_review(id/job_id/profile_version/decision_type/reviewer/feedback/batch_id/decided_at)`，两条 CHECK（decision_type 三值白名单、reviewer 非空白）+ 唯一索引 `idx_human_review_decision`。新表走 CREATE TABLE IF NOT EXISTS，⛔ 未进 `_ADDED_COLUMNS`。测试 `tests/test_human_review_schema.py``
- `1.6b` → `→ **已实现**，见 `tests/test_suspend_recovery.py::test_a_brand_new_process_recovers_the_suspended_thread`：真开一个新操作系统进程（LLM 网关换成一调用就抛的假货），只给数据库路径，断言按 thread_id 读回 checkpoint、对话历史与画像摘要`
- `6.1` → `→ **已实现**，`app/schemas/job_profile.py:summarize_profile()` 产出中文标签值对、`app/graph/build.py:_deliver_node` 把它放进 `profile_summary`、`app/web/static/index.html:renderProfileSummary()` 渲染。⛔ payload 里没有英文字段名，界面上就不可能出现英文 snake_case。测试 `tests/test_profile_summary.py` + `tests/test_approval_branches.py``
- `6.3` / `6.9` → `→ **已实现**，见 `tests/test_suspend_recovery.py`：跨进程恢复 + 7 天时间推进后仍能确认 + 幂等键不因时间流逝而过期`
- `6.4` → `→ **已实现**，`effect_confirm_profile` 在**同一个事务**里同时完成 status='approved'、job.status 同步与 `human_review` 留痕（工程铁律 1）。恒等不变式测试 `tests/test_approval_branches.py::test_human_review_row_count_equals_effect_log_count_per_thread``
- `6.5` / `6.6` → `→ **已实现**，`effect_request_revision`（独立 effect 节点）+ `POST /api/jobs/{id}/revise`；每一版草案保留（新 version，⛔ 不覆盖）；上限 5 次由 `revision_count()` 从 human_review 现算，⛔ 无计数列`
- `6.7` → `→ **已实现**，`effect_abandon_profile` + `POST /api/jobs/{id}/abandon`：置 abandoned、内容一字不改，且 `/reply` `/confirm` `/revise` 三个入口都拒绝已放弃的岗位`
- `9.3` → `→ **`human_review` 那一半已实现**：`app/audit/assertions.py` 断言四 `assert_every_decision_has_human_review`，已注册进 `COMPLIANCE_ASSERTIONS`（3 条 → 4 条），反证在 `tests/test_audit_assertion_effectiveness.py`。⚠️ `analysis_run` 那一半随 1.3/2.6 已移出到 `ai-audit-trail-and-outbound-gate`，不在本包`

**`6.8` 保持未勾**，在条目后追加：

```
      ⏸ **留步：等定时基础设施（与 5.6 同源）。** 本系统至今没有任何定时/调度
      基础设施——发提醒是一个有副作用的动作，必须落在 effect_* 节点里，由一个
      真正的调度器按时触发。⛔ 不用 sleep 循环或后台线程充数：那种东西进程一
      重启就没了，而这条 spec 要的恰恰是"挂起 7 天不丢"。判定口径（第 1 天、
      第 3 天各一次）已写在 spec 的「流程长时间挂起」Scenario 里，调度器落地时
      直接照抄。已登记 `docs/tech-debt.md` TD-11。
```

- [ ] **Step 3: 登记技术债**

`docs/tech-debt.md` 的既有格式是 `## TD-N · 标题` + **欠的是什么 / 触发条件 / 怎么还 / 不还的后果**四段。当前最大编号是 **TD-10**，所以新条目是 **TD-11**。**只加一条新的**——另一件事并进既有的 TD-6。

**（a）在文件末尾追加 TD-11：**

```markdown
## TD-11 · 挂起提醒（第 1 天 / 第 3 天）缺定时基础设施

**欠的是什么**：`m1-job-profile-intake` tasks 6.8。spec「流程长时间挂起」要求挂起后
第 1 天与第 3 天各发一次提醒，本系统至今没有任何定时 / 调度基础设施，这条一次都
没实现过。判定口径（第 1 天、第 3 天各一次）在 spec 的 Scenario 里写着，调度器落地
时直接照抄。

**触发条件**：定时基础设施落地。**与 tasks 5.6 同源**（那条也卡在同一件事上），
两条一起做——只做一条会得到一个只服务一个调用方的半吊子调度器。

**怎么还**：发提醒是有副作用的动作，必须是一个带幂等键的 `effect_*` 节点
（幂等键须含"第几次提醒"，否则调度器重跑会重复发）。⛔ **不用 sleep 循环或后台
线程充数**：那种东西进程一重启就没了，而这条 spec 要的恰恰是"挂起 7 天不丢"——
用它充数等于把"没做"标成"做完了"。

**不还的后果**：业务经理挂起后无人提醒，靠自己想起来回来确认；挂起越久越可能
被彻底遗忘，一个岗位就这么无声无息地停在半路。⚠️ 挂起状态**本身不丢**
（`tests/test_suspend_recovery.py` 已验 7 天），丢的只是提醒——所以这条债的代价是
流程变慢，不是数据损坏。
```

**（b）在既有的 TD-6 里补一句**，⛔ 不新开一条——TD-6「`operator_id` 现阶段不可信（鉴权是空壳）」讲的就是这件事，同一条债多开一个编号只会让人以为是两笔。

在 TD-6 的「欠的是什么」段末尾追加：

```markdown
**2026-09-04 补**：`human_review.reviewer` 加入同一份清单
（`m1-job-profile-intake` tasks 6.4）。人工确认 / 修改 / 放弃三个分支的决策人
现阶段一律写 `app/middleware/auth.py` 的 `UNKNOWN_REVIEWER = "unknown:web-session"`
——⛔ 不写 NULL（分不清"没有决策人"和"这条漏写了"）、⛔ 不编人名（伪造留痕比不
留痕更糟），显式标注"身份未知"是这个阶段唯一诚实的写法。SSO 落地后 `reviewer_of()`
自动返回真实 userid，`human_review` 表结构与所有调用方**一行不改**。
⚠️ 审计断言四（`app/audit/assertions.py`）能验"有没有留痕"，**验不了"追不追得到
人"**——后者正是这条债。
```

- [ ] **Step 4: 最后自查**

```bash
# 三级标题数（scripts/task-brief 按它抽取任务全文，二级会静默失败）
grep -c '^### Task ' docs/superpowers/plans/2026-09-04-m1-job-profile-intake-unit6-approval-checkpoint.md
# Global Constraints 段
grep -c 'Global Constraints' docs/superpowers/plans/2026-09-04-m1-job-profile-intake-unit6-approval-checkpoint.md
# 没有残留的占位符
grep -n 'TBD\|TODO\|待补\|适当处理' docs/superpowers/plans/2026-09-04-m1-job-profile-intake-unit6-approval-checkpoint.md
# 全量
pytest -q && pytest -m compliance -q
```

- [ ] **Step 5: 提交**

```bash
git add openspec/changes/m1-job-profile-intake/tasks.md docs/tech-debt.md
git commit -m "docs(m1): 回勾第 6 章确认断点 9 条 + 6.8 留步登记（TD-11，reviewer 并进 TD-6）"
```

---

## 出计划时做过的验证（与没做的）

**做过的**：Task 2 的 `summarize_profile()` 连同它的私有渲染助手，已被**原样提取**到一个隔离目录、配上同样原样提取的 8 条测试跑过一遍，**全绿**（含 6 个畸形 LLM 输出的参数化用例）。计划里那两条逐字断言——

- `"CAN 驱动开发（必会）、Python 脚本（加分）"`
- `"A05 纯电 · SOP 2024-06 · BSW 负责人 · 已量产"`

——是**实跑出来的字符串**，不是照着代码心算的。这一段挑出来验，是因为它是整份计划里唯一一块"逐字比对字符串"的断言，也是最容易写对代码却写错期望值的地方。

**没做的（`spec-to-plan` SKILL.md §6 的完整端到端提取验证）**：本单元的其余代码全部是对既有仓库的**增量修改**（改 `_deliver_node` 的一个 payload、给 `effect_confirm_profile` 加一个参数、往 `SCHEMA` 里加一张表），提取到空目录里跑不起来——它们依赖 FastAPI 应用、LangGraph 编译图、既有的 20 个 effect 与 50 份测试。**⛔ 不要把这段说成"验证通过"**：这些代码的第一次真实执行发生在 `run-build` 的 Task 1 Step 4，那才是它们第一次被跑。

**因此 `run-build` 执行时要额外留意的三处**（是提取验证本该抓、这次没抓的那类问题）：

1. **Task 3 Step 3f 的五个既有调用点**。`effect_confirm_profile` 加了必填关键字参数，漏改一处就是 `TypeError`——好在这类失败很响，一跑就现形。
2. **Task 8 的三处 `== 3` 改 `== 4`**。漏改会红在一个与本单元看似无关的文件里，容易被误判成"别人的测试坏了"。
3. **Task 7 的子进程探针**。`PYTHONPATH` 与 `cwd` 是它唯一容易出错的地方，且 `subprocess.run(check=True)` 的报错信息不含子进程 stderr——先看 `result.stderr` 再改别的。

---

## 交付后必须留在报告里的三句话

`run-build` 的最终报告里逐条写明，**⛔ 不许省略**：

1. **6.8 未实现，⏸ 留步：等定时基础设施（与 5.6 同源）。** `tasks.md` 里该条保持未勾。本单元交付的是 8 条 D 类里的 7 条 + 1.4 + 1.6b + 9.3（`human_review` 那一半）。
2. **企微通道那两条 Scenario（推送确认·企业微信、回调可靠接收）不在本单元**，已随企微通道移出到阶段二。本单元只做 Web 通道那一半，`job-profile-approval` 的 spec 归档时这两条要单独说明。
3. **`human_review.reviewer` 现阶段全部是 `unknown:web-session`**（鉴权空壳，已并进 TD-6）。审计断言四能验"有没有留痕"，**验不了"追不追得到人"**——后者要等 SSO，且它是部署约束 5 那道硬门槛的一部分。
