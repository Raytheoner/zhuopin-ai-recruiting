# 入职流程 · 交付单元 U1（入职域模型）Implementation Plan

**Goal:** 为 onboarding-flow 变更包的 **U1 交付单元（tasks.md 第 1 章「入职域模型」）** 产出实现计划。范围 = 6 张全新表（`onboarding_template` / `onboarding_checklist` / `onboarding_item` / `onboarding_item_history` / `onboarding_access_log` / `data_disposition_queue`）＋ `hr_account` 加 `role`/`department` 两列＋ `Settings.retention_policy_signed_version` 配置位＋ 一份占位模板 v1＋ 模板维护接口 `GET/PUT`。**不写任何 `effect_*` 节点、不接任何 LLM、不产生任何外发**——这些是 U2–U4 的活。

**Architecture:** 6 张新表全部走 `CREATE TABLE IF NOT EXISTS` 追加进 `app/storage/db.py` 的 `SCHEMA` 常量（**不进 `_ADDED_COLUMNS`**）；`hr_account.role/department` 是**既有表加列**，必须同时出现在 `SCHEMA` 的 `CREATE TABLE hr_account`（新库）与 `_ADDED_COLUMNS`（老库 ALTER）两处。占位模板用 `init_schema` 里的幂等种子函数落库（固定主键 `INSERT OR IGNORE`）。模板维护接口是**直接 HTTP CRUD**（非 LangGraph 节点），幂等靠"同内容 PUT 不新增版本"的内容比较。

**Tech Stack:** Python（项目 `./venv`）· SQLite（标准库 `sqlite3`，WAL + `PRAGMA foreign_keys=ON`）· pydantic-settings · FastAPI · pytest · **不引入任何新依赖**（`requirements.txt` / `pyproject.toml` diff 必须为空）。

---

## 输入与交付单元划分

本计划为 **U1（tasks.md 第 1 章「入职域模型」，tasks 1.1–1.6）** 出计划。输入真源是 `openspec/changes/onboarding-flow/specs/**/spec.md` 与 `design.md`；`tasks.md` **只用于确认 U1 章节边界**，不作为计划输入。

与本单元相关的 spec 能力文件（自行列出，三份，理由如下）：

| 能力文件 | 与本单元的关系 |
|---|---|
| `specs/onboarding-checklist/spec.md` | **核心**。`onboarding_template`（模板版本化）、`onboarding_checklist`（一份投递一份清单）、`onboarding_item`（条目状态）、`onboarding_item_history`（勾选/豁免留痕）四张表是该 spec 的存储层落点 |
| `specs/onboarding-visibility/spec.md` | `hr_account.role/department`（部门经理只读本部门）与 `onboarding_access_log`（查看写留痕）是该 spec 的两处存储层落点 |
| `specs/post-hire-data-disposition/spec.md` | `data_disposition_queue`（登记与执行分离）与 `Settings.retention_policy_signed_version`（签认闸）是该 spec 的两处存储层落点 |

**说明**：U1 只交付上述三份 spec 的**存储层**（表、列、配置位、唯一约束）。spec 里的行为性 Requirement（实例化节点、勾选/豁免节点、终态节点、处置执行节点、页面与过滤、进度纯函数）由 U2–U4 承担——见文末「spec 覆盖对照」逐条标注。

相关 design Decisions：D1（材料不入库）、D2（HR＋部门经理只读本部门）、D4（登记与执行分离）、D6（模板由 HR 维护、AI 不参与）、D7（交付单元顺序：U1 前置 = M2 U1；offer-generation U1 不阻塞 U1，本单元不引用 `offer` 表）。

---

## Global Constraints

以下条目从 `CLAUDE.md`「工程铁律」「合规红线」与本变更包 `design.md` **逐字复制**（或标注为"本包约束"）。**每个 Task 的验收隐含包含本节全部内容**；reviewer 会把这一段当注意力透镜。

### 本包合规约束（opener 1001P §零「🔴 本包合规约束」逐字）

> **材料本身不入库**（只存清单条目状态）；进度可见性限「HR 全量／用人部门只读本部门」。

**reviewer 的机械判据**：`onboarding_item` 无 `content` / `attachment` / `id_number` / `file` / `file_name` / `content_sha256` / `raw_text` / `parsed_json` / `url` 类列（Task 2 的 `test_onboarding_item_has_no_content_columns` 用列名反证锁死）。

### 工程铁律（不可违背，与本单元相关条目逐字）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。

> **本单元与这条的关系**：U1 **不新增任何 `effect_*` 节点、不接 LangGraph、不写 `effect_log`**。但 U1 要为铁律 1 留好**存储层第二道防线**：`onboarding_checklist.application_id` 唯一（一份投递一份清单）、`data_disposition_queue.(application_id, category)` 唯一（六类各一行）、`onboarding_template.(scope_type, scope_id, version)` 唯一（版本递增）。模板维护接口的 PUT 是**直接 HTTP CRUD**（不在 checkpointer 恢复路径上），其幂等靠"同内容 PUT 不新增版本"的内容比较兑现，⛔ 不套 `effect_log`。

2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。

> **本单元与这条的关系**：U1 没有 `compute_*`/`effect_*` 节点（这些是 U2/U3/U4 的活）。U1 只建表、加列、加配置位、提供模板 CRUD 接口。

3–7 条（AI 评分持久化、`evidence_ref`、`temperature=0`、企微回调、`langgraph>=1.0.10`）：**本单元不触发**——U1 无 LLM 调用、无评分、无 `criterion_score`、无企微、不改依赖版本。

### 合规红线（本单元相关条目）

七条红线在本单元**均不触发**（U1 无排序/淘汰、无人脸、无 AI 生成 JD/拒信/邀约、无简历数据出境、无历史录用监督信号、无候选人对外通道、无硬门槛规则）。本单元对应到合规红线的形态只有一条，来自本包约束：**材料不入库**（见上），它服务于「候选人个人信息最小收集」的同一保守方向。

### 部署约束（本单元相关）

- 6 张新表 DDL 是 `CREATE TABLE IF NOT EXISTS`，对既有库幂等、无回填；`hr_account` 加列走 `_ADDED_COLUMNS`（老库 ALTER，`role` 默认 `'hr'`、`department` 可空），既有账号无需回填。
- ⛔ 本单元不引 `.51` 服务器、不处理真实简历、不产生任何不可逆动作（删除/脱敏是 U4 且受签认闸）。

---

## 明确的范围边界（U1 **不做**什么）

| 不做 | 归属 |
|---|---|
| `effect_instantiate_checklist` / `effect_update_item`（实例化/勾选/豁免节点） | U2（2.1/2.2） |
| 进度与逾期纯函数 `progress()`、HR 清单页、经理只读页 | U2（2.3–2.6） |
| `effect_complete_onboarding` / `effect_enqueue_disposition` / 终态不变式 | U3（3.1–3.5） |
| `effect_execute_disposition` / 脱敏规则 / 断言兼容 | U4（4.1–4.4） |
| 模板维护**页面**（HTML 表单） | 不在 U1；U1 只交付 JSON 接口（`/api/onboarding-templates/*`），页面形态归后续单元 |
| `application.status` 的 `{ongoing,hired,refused}` 迁移 | **M2 U1 / offer-generation** 的活，U1 不碰 `application.status` |
| `.51` 发版、真实简历处理、处置执行开启 | 不可代项（U5 4.5/5.4） |

**U1 合并后系统的可观察行为**：新增 6 张表（其中 1 张带一份占位模板）、`hr_account` 多两列、`Settings` 多一个默认 `None` 的键、多一个仅 HR 可用的模板读写接口。**既有投递/简历/评分流程零变化。**

---

## File Structure

| 文件 | 动作 | 责任 |
|---|---|---|
| `app/storage/db.py` | 修改 | 追加 6 张新表 DDL＋ 改 `hr_account` CREATE TABLE＋ `_ADDED_COLUMNS` 加 2 条＋ `import json`＋ 占位模板种子函数＋ `init_schema` 调种子 |
| `app/config.py` | 修改 | `Settings` 加 `retention_policy_signed_version: str \| None = None` |
| `app/web/server.py` | 修改 | `OnboardingTemplateItem` / `OnboardingTemplateUpdateRequest` 两个请求模型＋ `_require_role` 辅助＋ `_canonical_items` 辅助＋ `GET/PUT /api/onboarding-templates/{scope_type}/{scope_id}` 两条路由 |
| `tests/test_db_onboarding_schema.py` | 新建 | U1 新表结构/CHECK 反证/无内容列/角色列/配置位（Task 1–4） |
| `tests/test_onboarding_template_api.py` | 新建 | 模板接口的 HR 角色/版本递增/同内容幂等/参数校验（Task 5） |
| `tests/test_db_migration.py` | 修改 | `_LEGACY_HR_ACCOUNT_DDL`＋ `_legacy_db` 建 `hr_account`＋ `_DRIFT_GUARDED_TABLES` 加 `hr_account`＋ 集合断言加 `hr_account`＋ 老库 hr_account 迁移默认值测试（Task 4） |
| `tests/test_db_m2_schema.py` | 修改 | `test_hr_account_table_exists_with_expected_columns` 加 `role/department`；`test_added_columns_tuple_still_only_touches_job_profile` 集合断言加 `hr_account`（Task 4） |
| `tests/test_db_m2_u2_schema.py` | 修改 | `test_old_job_table_gains_parse_confidence_threshold_via_migration` 补 `hr_account` 空壳表（Task 4） |

> ⛔ Codex 泳道：本计划各 Task **不写 git add/commit/push 步骤**——worktree 内不能自行提交，收口由执行器 `run-lanes.sh` → `scripts/lane_collect.py` 代做（AGENTS.md §4）。每个 Task 以"测试全绿"收尾即可。

---

### Task 1: `onboarding_template` 表（tasks 1.1）

**Files:**
- Modify: `app/storage/db.py`（`SCHEMA` 字符串末尾、`hr_session` 索引之后、"voice-structured-interview" 注释块之前追加）
- Test: `tests/test_db_onboarding_schema.py`（新建）

**Interfaces:**
- Consumes: `app.storage.db.get_connection(db_path) -> sqlite3.Connection`、`app.storage.db.init_schema(conn) -> None`（均已存在，签名不改）
- Produces: 表 `onboarding_template`（`id`/`scope_type`/`scope_id`/`version`/`items`/`updated_by`/`updated_at`）＋ 唯一索引 `idx_onboarding_template_scope_version (scope_type, scope_id, version)`

- [ ] **Step 1: Write the failing test**

新建 `tests/test_db_onboarding_schema.py`（本 Task 只先建文件头与 Task 1 用例，后续 Task 追加）：

```python
"""onboarding-flow U1 入职域模型：新库建表齐全、CHECK 反证、onboarding_item 无内容列、
hr_account 角色列、留存策略配置位。老库升级回归在 tests/test_db_migration.py（Task 4）。"""
import sqlite3

import pytest

from app.config import Settings
from app.storage.db import get_connection, init_schema

_ONBOARDING_TABLES = (
    "onboarding_template",
    "onboarding_checklist",
    "onboarding_item",
    "onboarding_item_history",
    "onboarding_access_log",
    "data_disposition_queue",
)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "onboarding.db"))
    init_schema(c)
    return c


def _seed_application(conn: sqlite3.Connection, application_id: str = "app-1") -> None:
    """为带 application/candidate 外键的断言准备一份投递。"""
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES ('j1', '嵌入式工程师', 'approved')"
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('res-1', 'j1', 'synthetic', 'a.docx', 'sha-1', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, 'cand-1', 'j1', 'res-1', 'initial')",
        (application_id,),
    )
    conn.commit()


# ── Task 1：onboarding_template ──────────────────────────────────────────


def test_onboarding_tables_all_exist(conn):
    for table in _ONBOARDING_TABLES:
        assert _table_exists(conn, table), f"{table} 应该在 init_schema 后出现"


def test_onboarding_template_columns(conn):
    assert _columns(conn, "onboarding_template") == {
        "id", "scope_type", "scope_id", "version", "items", "updated_by", "updated_at",
    }


def test_onboarding_template_scope_type_check(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO onboarding_template "
            "(id, scope_type, scope_id, version, items, updated_by) "
            "VALUES ('t-bad', 'team', 't', 1, '[]', 'hr')"
        )
    for scope_type in ("job", "department"):
        conn.execute(
            "INSERT INTO onboarding_template "
            "(id, scope_type, scope_id, version, items, updated_by) "
            "VALUES (?, ?, ?, 1, '[]', 'hr')",
            (f"t-{scope_type}", scope_type, "t"),
        )
    conn.commit()


def test_onboarding_template_scope_version_unique(conn):
    conn.execute(
        "INSERT INTO onboarding_template "
        "(id, scope_type, scope_id, version, items, updated_by) "
        "VALUES ('t-1', 'department', 'd', 1, '[]', 'hr')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO onboarding_template "
            "(id, scope_type, scope_id, version, items, updated_by) "
            "VALUES ('t-2', 'department', 'd', 1, '[]', 'hr')"
        )


def test_onboarding_template_updated_by_cannot_be_blank(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO onboarding_template "
            "(id, scope_type, scope_id, version, items, updated_by) "
            "VALUES ('t-blank', 'department', 'd', 1, '[]', '   ')"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_db_onboarding_schema.py -q`
Expected: FAIL —— `sqlite3.OperationalError: no such table: onboarding_template`（`test_onboarding_template_columns` 阶段即红）。

- [ ] **Step 3: Write minimal implementation**

`app/storage/db.py` 顶部加 `import json`（本文件当前无 `json` import）。在 `SCHEMA` 字符串里 `hr_session` 的索引语句之后、`-- 以下 8 张表属变更包 voice-structured-interview` 注释之前追加：

```sql
-- ─────────────────────────────────────────────────────────────────────────
-- 以下 6 张表属变更包 onboarding-flow（交付单元 U1 入职域模型）。全部新表，
-- 走 CREATE TABLE IF NOT EXISTS，**不进 _ADDED_COLUMNS**（新表不需要加列路径）。
-- ⛔ 材料不入库：onboarding_item 无 content / attachment / id_number / file 类列
-- （tests/test_db_onboarding_schema.py 用列名反证）。
-- ─────────────────────────────────────────────────────────────────────────

-- 清单模板（design D1/D6）：按岗位（scope_type='job'）或部门（'department'）维护，
-- 版本化（(scope_type, scope_id, version) 唯一）。items 存 JSON 数组，每个元素
-- {name, owner_party, due_offset_days, required}——只跟踪状态，不存材料内容。
CREATE TABLE IF NOT EXISTS onboarding_template (
    id TEXT PRIMARY KEY NOT NULL,
    scope_type TEXT NOT NULL CHECK (scope_type IN ('job', 'department')),
    scope_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    items TEXT NOT NULL,
    updated_by TEXT NOT NULL CHECK (
        updated_by IS NOT NULL
        AND trim(updated_by, ' ' || char(9) || char(10) || char(13)) != ''
    ),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_onboarding_template_scope_version
    ON onboarding_template (scope_type, scope_id, version);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_db_onboarding_schema.py -q`
Expected: 通过（其余 Task 的用例此刻尚未写出，文件里只有 Task 1 的用例）。

---

### Task 2: `onboarding_checklist` + `onboarding_item` 表（tasks 1.2）

**Files:**
- Modify: `app/storage/db.py`（在 Task 1 追加的块之后继续追加）
- Test: `tests/test_db_onboarding_schema.py`（追加）

**Interfaces:**
- Produces: `onboarding_checklist`（`application_id` 唯一）、`onboarding_item`（无内容列）＋ 索引 `idx_onboarding_item_checklist`

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_db_onboarding_schema.py`：

```python
# ── Task 2：onboarding_checklist / onboarding_item ───────────────────────


def test_onboarding_checklist_columns(conn):
    assert _columns(conn, "onboarding_checklist") == {
        "id", "application_id", "template_version", "start_date",
        "status", "closed_reason", "created_by", "created_at",
    }


def test_onboarding_checklist_application_id_unique(conn):
    _seed_application(conn, "app-1")
    conn.execute(
        "INSERT INTO onboarding_checklist "
        "(id, application_id, template_version, start_date, created_by) "
        "VALUES ('cl-1', 'app-1', 1, '2026-10-20', 'hr')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO onboarding_checklist "
            "(id, application_id, template_version, start_date, created_by) "
            "VALUES ('cl-2', 'app-1', 1, '2026-10-20', 'hr')"
        )


def test_onboarding_checklist_status_check(conn):
    _seed_application(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO onboarding_checklist "
            "(id, application_id, template_version, start_date, status, created_by) "
            "VALUES ('cl-bad', 'app-1', 1, '2026-10-20', 'bogus', 'hr')"
        )


def test_onboarding_item_columns(conn):
    assert _columns(conn, "onboarding_item") == {
        "id", "checklist_id", "name", "owner_party", "due_offset_days",
        "required", "status", "reason", "acted_by", "acted_at",
    }


def test_onboarding_item_has_no_content_columns(conn):
    """⛔ 材料不入库：清单条目只跟踪状态，不得出现内容/附件/证件号类列。"""
    cols = _columns(conn, "onboarding_item")
    forbidden = {
        "content", "attachment", "id_number", "file", "file_name",
        "content_sha256", "raw_text", "parsed_json", "url",
    }
    assert not (forbidden & cols)


def test_onboarding_item_owner_party_check(conn):
    _seed_application(conn)
    conn.execute(
        "INSERT INTO onboarding_checklist "
        "(id, application_id, template_version, start_date, created_by) "
        "VALUES ('cl-1', 'app-1', 1, '2026-10-20', 'hr')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO onboarding_item "
            "(id, checklist_id, name, owner_party, due_offset_days, required) "
            "VALUES ('it-bad', 'cl-1', 'n', 'bogus', 0, 1)"
        )


def test_onboarding_item_status_check(conn):
    _seed_application(conn)
    conn.execute(
        "INSERT INTO onboarding_checklist "
        "(id, application_id, template_version, start_date, created_by) "
        "VALUES ('cl-1', 'app-1', 1, '2026-10-20', 'hr')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO onboarding_item "
            "(id, checklist_id, name, owner_party, due_offset_days, required, status) "
            "VALUES ('it-bad', 'cl-1', 'n', 'hr', 0, 1, 'bogus')"
        )


def test_onboarding_item_required_check(conn):
    _seed_application(conn)
    conn.execute(
        "INSERT INTO onboarding_checklist "
        "(id, application_id, template_version, start_date, created_by) "
        "VALUES ('cl-1', 'app-1', 1, '2026-10-20', 'hr')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO onboarding_item "
            "(id, checklist_id, name, owner_party, due_offset_days, required) "
            "VALUES ('it-bad', 'cl-1', 'n', 'hr', 0, 2)"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_db_onboarding_schema.py -q -k "checklist or item"`
Expected: FAIL —— `no such table: onboarding_checklist`。

- [ ] **Step 3: Write minimal implementation**

在 `SCHEMA` 里 Task 1 追加的块之后继续追加：

```sql
-- 清单实例（design D3）：一份投递一份清单（application_id 唯一），按模板版本展开。
-- status 两态：open（进行中）/ closed（已关闭，关闭原因落 closed_reason）。
CREATE TABLE IF NOT EXISTS onboarding_checklist (
    id TEXT PRIMARY KEY NOT NULL,
    application_id TEXT NOT NULL UNIQUE REFERENCES application(id),
    template_version INTEGER NOT NULL,
    start_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    closed_reason TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 清单条目：只跟踪状态，⛔ 无材料内容/附件/证件号字段。status 三态：
-- pending（待办）/ done（已完成）/ waived（豁免，reason 必填由应用层校验）。
-- required 用 INTEGER 0/1（SQLite 无原生 BOOLEAN）。
CREATE TABLE IF NOT EXISTS onboarding_item (
    id TEXT PRIMARY KEY NOT NULL,
    checklist_id TEXT NOT NULL REFERENCES onboarding_checklist(id),
    name TEXT NOT NULL,
    owner_party TEXT NOT NULL CHECK (
        owner_party IN ('hr', 'it', 'admin', 'finance', 'dept')
    ),
    due_offset_days INTEGER NOT NULL,
    required INTEGER NOT NULL CHECK (required IN (0, 1)),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'waived')),
    reason TEXT,
    acted_by TEXT,
    acted_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_onboarding_item_checklist
    ON onboarding_item (checklist_id);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_db_onboarding_schema.py -q`
Expected: 通过（Task 1 + Task 2 用例全绿）。

---

### Task 3: `onboarding_item_history` + `onboarding_access_log` + `data_disposition_queue`（tasks 1.3）

**Files:**
- Modify: `app/storage/db.py`（继续追加）
- Test: `tests/test_db_onboarding_schema.py`（追加）

**Interfaces:**
- Produces: `onboarding_item_history`、`onboarding_access_log`、`data_disposition_queue` ＋ 索引 `idx_onboarding_item_history_item` / `idx_onboarding_access_log_application` / 唯一索引 `idx_data_disposition_queue_app_category`

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_db_onboarding_schema.py`：

```python
# ── Task 3：onboarding_item_history / onboarding_access_log / data_disposition_queue ──


def test_onboarding_item_history_columns(conn):
    assert _columns(conn, "onboarding_item_history") == {
        "id", "item_id", "from_status", "to_status", "reason", "acted_by", "at",
    }


def test_onboarding_access_log_columns(conn):
    assert _columns(conn, "onboarding_access_log") == {
        "id", "accessor", "application_id", "at",
    }


def test_onboarding_access_log_accessor_cannot_be_blank(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO onboarding_access_log (id, accessor, application_id) "
            "VALUES ('log-1', '  ', 'app-x')"
        )


def test_data_disposition_queue_columns(conn):
    assert _columns(conn, "data_disposition_queue") == {
        "id", "application_id", "candidate_id", "category", "policy_version",
        "planned_action", "due_at", "executed_at", "executed_by", "note",
    }


def test_data_disposition_queue_category_check(conn):
    _seed_application(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO data_disposition_queue (id, application_id, candidate_id, category) "
            "VALUES ('dq-bad', 'app-1', 'cand-1', 'bogus')"
        )
    for category in (
        "resume_file", "parsed_fields", "scores",
        "interview", "contact", "offer_letter",
    ):
        conn.execute(
            "INSERT INTO data_disposition_queue (id, application_id, candidate_id, category) "
            "VALUES (?, 'app-1', 'cand-1', ?)",
            (f"dq-{category}", category),
        )
    conn.commit()


def test_data_disposition_queue_planned_action_check(conn):
    _seed_application(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO data_disposition_queue "
            "(id, application_id, candidate_id, category, planned_action) "
            "VALUES ('dq-bad', 'app-1', 'cand-1', 'resume_file', 'archive')"
        )


def test_data_disposition_queue_app_category_unique(conn):
    _seed_application(conn)
    conn.execute(
        "INSERT INTO data_disposition_queue (id, application_id, candidate_id, category) "
        "VALUES ('dq-1', 'app-1', 'cand-1', 'resume_file')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO data_disposition_queue (id, application_id, candidate_id, category) "
            "VALUES ('dq-2', 'app-1', 'cand-1', 'resume_file')"
        )


def test_data_disposition_queue_defaults_pending_and_null_policy(conn):
    _seed_application(conn)
    conn.execute(
        "INSERT INTO data_disposition_queue (id, application_id, candidate_id, category) "
        "VALUES ('dq-1', 'app-1', 'cand-1', 'resume_file')"
    )
    conn.commit()
    row = conn.execute(
        "SELECT policy_version, planned_action FROM data_disposition_queue WHERE id='dq-1'"
    ).fetchone()
    assert row == (None, "pending")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_db_onboarding_schema.py -q -k "history or access_log or disposition"`
Expected: FAIL —— `no such table: onboarding_item_history`。

- [ ] **Step 3: Write minimal implementation**

在 `SCHEMA` 里 Task 2 追加的块之后继续追加：

```sql
-- 条目状态变更留痕（spec「条目勾选与豁免留痕」）：每次变更写一行，from/to 两态。
CREATE TABLE IF NOT EXISTS onboarding_item_history (
    id TEXT PRIMARY KEY NOT NULL,
    item_id TEXT NOT NULL REFERENCES onboarding_item(id),
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    reason TEXT,
    acted_by TEXT,
    at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_onboarding_item_history_item
    ON onboarding_item_history (item_id);

-- 部门经理进度查看留痕（spec「查看写访问留痕」）：只记谁/何时/哪个投递，
-- ⛔ 不含清单内容本身。
CREATE TABLE IF NOT EXISTS onboarding_access_log (
    id TEXT PRIMARY KEY NOT NULL,
    accessor TEXT NOT NULL CHECK (
        accessor IS NOT NULL
        AND trim(accessor, ' ' || char(9) || char(10) || char(13)) != ''
    ),
    application_id TEXT NOT NULL REFERENCES application(id),
    at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_onboarding_access_log_application
    ON onboarding_access_log (application_id);

-- 入职后招聘数据处置队列（design D4）：登记与执行分离。策略未签认时
-- policy_version NULL、planned_action='pending'、executed_at 恒空（断言在 U3/U4）。
-- (application_id, category) 唯一是 effect_enqueue_disposition 幂等键的结构性
-- 第二道防线。
CREATE TABLE IF NOT EXISTS data_disposition_queue (
    id TEXT PRIMARY KEY NOT NULL,
    application_id TEXT NOT NULL REFERENCES application(id),
    candidate_id TEXT NOT NULL REFERENCES candidate(id),
    category TEXT NOT NULL CHECK (
        category IN (
            'resume_file', 'parsed_fields', 'scores',
            'interview', 'contact', 'offer_letter'
        )
    ),
    policy_version TEXT,
    planned_action TEXT NOT NULL DEFAULT 'pending'
        CHECK (planned_action IN ('delete', 'anonymize', 'pending')),
    due_at TEXT,
    executed_at TEXT,
    executed_by TEXT,
    note TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_data_disposition_queue_app_category
    ON data_disposition_queue (application_id, category);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_db_onboarding_schema.py -q`
Expected: 通过（Task 1–3 用例全绿）。

---

### Task 4: `hr_account.role/department` + `Settings.retention_policy_signed_version`（tasks 1.4）

**Files:**
- Modify: `app/storage/db.py`（改 `hr_account` CREATE TABLE＋ `_ADDED_COLUMNS` 追加 2 条）
- Modify: `app/config.py`（`Settings` 加一字段）
- Modify: `tests/test_db_migration.py`、`tests/test_db_m2_schema.py`、`tests/test_db_m2_u2_schema.py`（修 `_ADDED_COLUMNS` 扩表引发的既有测试）
- Test: `tests/test_db_onboarding_schema.py`（追加 hr_account 角色列 + Settings 用例）

**Interfaces:**
- Produces: `hr_account.role`（`CHECK IN ('hr','interviewer','dept_manager')`，默认 `'hr'`）、`hr_account.department`（可空）、`Settings.retention_policy_signed_version: str | None = None`

⚠️ **本 Task 会打破 3 处既有测试，必须在本 Task 内一并修复**，否则全量套件在 Task 4 之后是红的：
1. `tests/test_db_migration.py::test_apply_column_migrations_is_idempotent` —— 直接调 `apply_column_migrations(conn)`，而 `_legacy_db` 没有 `hr_account` 表 ⇒ `no such table: hr_account`。修法：`_legacy_db` 里建 `hr_account` 历史 DDL。
2. `tests/test_db_m2_u2_schema.py::test_old_job_table_gains_parse_confidence_threshold_via_migration` —— 内存库直接 `apply_column_migrations`，同样缺 `hr_account` 表。修法：补一张 `hr_account` 空壳。
3. `tests/test_db_migration.py::test_audit_tables_never_enter_the_add_column_path` 与 `tests/test_db_m2_schema.py::test_added_columns_tuple_still_only_touches_job_profile` —— 断言 `_ADDED_COLUMNS` 表集合等于旧值。修法：集合加 `hr_account`。
4. `tests/test_db_m2_schema.py::test_hr_account_table_exists_with_expected_columns` —— 断言 `hr_account` 列集合等于旧值。修法：加 `role`/`department`。

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_db_onboarding_schema.py`：

```python
# ── Task 4：hr_account.role/department + Settings.retention_policy_signed_version ──


def test_hr_account_gains_role_and_department_columns(conn):
    assert {"role", "department"} <= _columns(conn, "hr_account")


def test_hr_account_role_defaults_to_hr_and_department_nullable(conn):
    conn.execute(
        "INSERT INTO hr_account (id, username, password_hash, password_salt) "
        "VALUES ('acc-1', 'alice', 'h', 's')"
    )
    conn.commit()
    assert conn.execute("SELECT role FROM hr_account WHERE id='acc-1'").fetchone()[0] == "hr"
    assert (
        conn.execute("SELECT department FROM hr_account WHERE id='acc-1'").fetchone()[0]
        is None
    )


def test_hr_account_role_check(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO hr_account (id, username, password_hash, password_salt, role) "
            "VALUES ('acc-bad', 'bob', 'h', 's', 'bogus')"
        )


def test_retention_policy_signed_version_defaults_none():
    assert Settings().retention_policy_signed_version is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_db_onboarding_schema.py -q -k "hr_account or retention"`
Expected: FAIL —— `AssertionError: {'role', 'department'} <= {旧 hr_account 列集合}` 不成立。

- [ ] **Step 3: Write minimal implementation**

① `app/storage/db.py` 里 `hr_account` 的 CREATE TABLE 从：

```sql
CREATE TABLE IF NOT EXISTS hr_account (
    id TEXT PRIMARY KEY NOT NULL,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

改为：

```sql
CREATE TABLE IF NOT EXISTS hr_account (
    id TEXT PRIMARY KEY NOT NULL,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    -- onboarding-flow U1（design D2）：role 区分 HR/面试官/部门经理，默认 hr；
    -- department 用于部门经理只读本部门过滤，历史账号可空。
    role TEXT NOT NULL DEFAULT 'hr'
        CHECK (role IN ('hr', 'interviewer', 'dept_manager')),
    department TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

② `app/storage/db.py` 的 `_ADDED_COLUMNS` 元组**末尾**追加：

```python
    # onboarding-flow U1 tasks 1.4：hr_account 加 role/department。hr_account 是
    # M2 U1 建的表，早已存在于 .51 老库，必须走加列路径（与 job/resume 同先例）。
    # role 默认 hr、department 可空（历史账号无部门）。CHECK 在 ALTER TABLE
    # ADD COLUMN 里同样生效（SQLite 支持）。
    ("hr_account", "role", "TEXT NOT NULL DEFAULT 'hr' CHECK (role IN ('hr', 'interviewer', 'dept_manager'))"),
    ("hr_account", "department", "TEXT"),
```

③ `app/config.py` 的 `Settings` 里，`m3_compliance_signoff_path` 之后插入：

```python
    # 合规验收 #1 留存策略签认版本（onboarding-flow design D4）。默认空 = 未签认，
    # U4 的 effect_execute_disposition 据此拒绝执行（登记与执行分离的"签认闸"）。
    # 签认后由 Shao Peishen 在 .51 的 .env 配置 RETENTION_POLICY_SIGNED_VERSION。
    retention_policy_signed_version: str | None = None
```

④ 修 `tests/test_db_migration.py`：

文件头追加历史 DDL 常量（`_LEGACY_INTERVIEW_SESSION_DDL` 之后）：

```python
# M2 U1 建表的 hr_account，不含 role/department——这两列由 onboarding-flow U1
# 通过 _ADDED_COLUMNS 加入老库。
_LEGACY_HR_ACCOUNT_DDL = """
CREATE TABLE hr_account (
    id TEXT PRIMARY KEY NOT NULL,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""
```

`_legacy_db` 里 `conn.executescript(_LEGACY_INTERVIEW_SESSION_DDL)` 之后追加 `conn.executescript(_LEGACY_HR_ACCOUNT_DDL)`，并在 `_DRIFT_GUARDED_TABLES` 里加 `"hr_account"`：

```python
_DRIFT_GUARDED_TABLES = ("job_profile", "resume", "job_prep_config", "interview_session", "hr_account")
```

`test_audit_tables_never_enter_the_add_column_path` 的断言改为：

```python
    assert {table for table, _column, _ddl in _ADDED_COLUMNS} == {
        "job_profile", "job", "resume", "job_prep_config", "interview_session", "hr_account"
    }
```

文件末尾追加一条老库 hr_account 迁移默认值测试：

```python
def test_legacy_hr_account_gains_role_and_department_with_defaults(tmp_path):
    """.51 老库的 hr_account 补列后：role 默认 hr、department 空，历史行不改。"""
    conn = _legacy_db(tmp_path)
    conn.execute(
        "INSERT INTO hr_account (id, username, password_hash, password_salt) "
        "VALUES ('acc-old', 'alice', 'h', 's')"
    )
    conn.commit()
    assert "role" not in _columns(conn, "hr_account")

    init_schema(conn)

    assert "role" in _columns(conn, "hr_account")
    assert "department" in _columns(conn, "hr_account")
    row = conn.execute("SELECT role, department FROM hr_account WHERE id='acc-old'").fetchone()
    assert row == ("hr", None)
```

⑤ 修 `tests/test_db_m2_schema.py`：

`test_hr_account_table_exists_with_expected_columns` 的断言改为：

```python
    assert _columns(conn, "hr_account") == {
        "id", "username", "password_hash", "password_salt",
        "role", "department", "created_at",
    }
```

`test_added_columns_tuple_still_only_touches_job_profile` 的断言改为：

```python
    assert tables_in_added_columns == {
        "job_profile", "job", "resume", "job_prep_config", "interview_session", "hr_account"
    }
```

⑥ 修 `tests/test_db_m2_u2_schema.py`：

`test_old_job_table_gains_parse_confidence_threshold_via_migration` 里 `conn.execute("CREATE TABLE interview_session (id TEXT PRIMARY KEY)")` 之后追加：

```python
    # onboarding-flow U1 往 _ADDED_COLUMNS 加了 hr_account 的 role/department，
    # 本测试直接调 apply_column_migrations，必须给 hr_account 一张空壳，否则
    # 遍历到 hr_account 条目时炸 "no such table"。
    conn.execute("CREATE TABLE hr_account (id TEXT PRIMARY KEY)")
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
./venv/bin/python -m pytest tests/test_db_onboarding_schema.py tests/test_db_migration.py tests/test_db_m2_schema.py tests/test_db_m2_u2_schema.py -q
```
Expected: 全绿。

---

### Task 5: 占位模板 v1 + 模板维护接口（tasks 1.6）

**Files:**
- Modify: `app/storage/db.py`（占位模板种子函数 + `init_schema` 调种子）
- Modify: `app/web/server.py`（请求模型 + 辅助函数 + GET/PUT 路由）
- Test: `tests/test_onboarding_template_api.py`（新建）

**Interfaces:**
- Consumes: `app.storage.hr_account.upsert_account`、`app.storage.auth_session.create_session`、既有 `make_test_client` 夹具（`tests/conftest.py`）
- Produces: 种子行 `('department', 'default', version=1)`（固定主键 `template-department-default-v1`）；`GET/PUT /api/onboarding-templates/{scope_type}/{scope_id}`（HR 角色；GET 返回最新版，PUT 版本递增、同内容不新增版本）

> ⛔ 本 Task 的 PUT 是**直接 HTTP CRUD**，不在 LangGraph 恢复路径上，因此**不套 `effect_log` 幂等键**。其幂等语义是 tasks 1.6 的「同内容 PUT 不新增版本」，靠内容比较＋ `(scope_type, scope_id, version)` 唯一索引兑现。

- [ ] **Step 1: Write the failing test**

新建 `tests/test_onboarding_template_api.py`：

```python
"""onboarding-flow U1 模板维护接口（tasks 1.6）：HR 角色、版本递增、同内容幂等。"""
from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account


def _account_with_role(conn, username: str, role: str) -> str:
    account_id = upsert_account(conn, username=username, password="s3cret!")
    conn.execute("UPDATE hr_account SET role = ? WHERE id = ?", (role, account_id))
    conn.commit()
    return account_id


def _login(client, conn, username: str, role: str) -> str:
    account_id = _account_with_role(conn, username, role)
    client.cookies.set("hr_session", create_session(conn, hr_account_id=account_id))
    return account_id


_SEED_ITEMS = [
    {"name": "签劳动合同", "owner_party": "hr", "due_offset_days": -3, "required": True},
    {"name": "交入职材料", "owner_party": "hr", "due_offset_days": -3, "required": True},
    {"name": "体检报告", "owner_party": "hr", "due_offset_days": -5, "required": True},
    {"name": "配置设备", "owner_party": "it", "due_offset_days": -2, "required": True},
    {"name": "开通账号", "owner_party": "it", "due_offset_days": -1, "required": True},
    {"name": "指定带教人", "owner_party": "dept", "due_offset_days": 0, "required": False},
]


def test_get_template_requires_hr_role(make_test_client):
    client, conn = make_test_client()
    assert client.get("/api/onboarding-templates/department/default").status_code == 401

    _login(client, conn, "interviewer", "interviewer")
    assert client.get("/api/onboarding-templates/department/default").status_code == 403

    _login(client, conn, "manager", "dept_manager")
    assert client.get("/api/onboarding-templates/department/default").status_code == 403

    _login(client, conn, "hr", "hr")
    resp = client.get("/api/onboarding-templates/department/default")
    assert resp.status_code == 200
    body = resp.json()
    assert body["scope_type"] == "department"
    assert body["scope_id"] == "default"
    assert body["version"] == 1
    assert len(body["items"]) == 6


def test_get_template_404_for_unknown_scope(make_test_client):
    client, conn = make_test_client()
    _login(client, conn, "hr", "hr")
    assert client.get("/api/onboarding-templates/department/nonexistent").status_code == 404


def test_get_template_rejects_invalid_scope_type(make_test_client):
    client, conn = make_test_client()
    _login(client, conn, "hr", "hr")
    assert client.get("/api/onboarding-templates/team/x").status_code == 422


def test_put_template_creates_new_version(make_test_client):
    client, conn = make_test_client()
    _login(client, conn, "hr", "hr")
    resp = client.put(
        "/api/onboarding-templates/department/default",
        json={"items": [_SEED_ITEMS[0]]},
    )
    assert resp.status_code == 200
    assert resp.json()["version"] == 2
    assert resp.json()["unchanged"] is False
    count = conn.execute(
        "SELECT COUNT(*) FROM onboarding_template "
        "WHERE scope_type='department' AND scope_id='default'"
    ).fetchone()[0]
    assert count == 2


def test_put_template_same_content_no_new_version(make_test_client):
    client, conn = make_test_client()
    _login(client, conn, "hr", "hr")
    resp = client.put(
        "/api/onboarding-templates/department/default",
        json={"items": _SEED_ITEMS},
    )
    assert resp.status_code == 200
    assert resp.json()["version"] == 1
    assert resp.json()["unchanged"] is True
    count = conn.execute(
        "SELECT COUNT(*) FROM onboarding_template "
        "WHERE scope_type='department' AND scope_id='default'"
    ).fetchone()[0]
    assert count == 1


def test_put_template_rejects_invalid_owner_party(make_test_client):
    client, conn = make_test_client()
    _login(client, conn, "hr", "hr")
    resp = client.put(
        "/api/onboarding-templates/department/default",
        json={"items": [{"name": "x", "owner_party": "bogus", "due_offset_days": 0, "required": True}]},
    )
    assert resp.status_code == 422


def test_put_template_rejects_blank_name(make_test_client):
    client, conn = make_test_client()
    _login(client, conn, "hr", "hr")
    resp = client.put(
        "/api/onboarding-templates/department/default",
        json={"items": [{"name": "  ", "owner_party": "hr", "due_offset_days": 0, "required": True}]},
    )
    assert resp.status_code == 422


def test_put_template_requires_hr_role(make_test_client):
    client, conn = make_test_client()
    _login(client, conn, "manager", "dept_manager")
    resp = client.put(
        "/api/onboarding-templates/department/default",
        json={"items": []},
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_onboarding_template_api.py -q`
Expected: FAIL —— `AssertionError: 404 != 200`（路由不存在）。

- [ ] **Step 3: Write minimal implementation**

① `app/storage/db.py`：模块级加占位模板种子常量与函数（放在 `_ADDED_COLUMNS` 定义之后、`_existing_columns` 之前）：

```python
# onboarding-flow U1 占位模板 v1（design.md 风险表：人事部#3 回件未到，先给一份
# 通用部门级默认，回件到后 HR 在页面改）。due_offset_days 相对入职日（负=入职前）。
# 六条负责方与期限均为常识占位，⛔ 非真值。
_ONBOARDING_DEFAULT_TEMPLATE_ITEMS = [
    {"name": "签劳动合同", "owner_party": "hr", "due_offset_days": -3, "required": True},
    {"name": "交入职材料", "owner_party": "hr", "due_offset_days": -3, "required": True},
    {"name": "体检报告", "owner_party": "hr", "due_offset_days": -5, "required": True},
    {"name": "配置设备", "owner_party": "it", "due_offset_days": -2, "required": True},
    {"name": "开通账号", "owner_party": "it", "due_offset_days": -1, "required": True},
    {"name": "指定带教人", "owner_party": "dept", "due_offset_days": 0, "required": False},
]


def _seed_onboarding_default_template(conn: sqlite3.Connection) -> None:
    """幂等种子：部门级默认模板，固定主键，重复调用不产生第二行。"""
    items_json = json.dumps(_ONBOARDING_DEFAULT_TEMPLATE_ITEMS, ensure_ascii=False)
    conn.execute(
        "INSERT OR IGNORE INTO onboarding_template "
        "(id, scope_type, scope_id, version, items, updated_by) "
        "VALUES ('template-department-default-v1', 'department', 'default', 1, ?, 'system')",
        (items_json,),
    )
```

并把 `init_schema` 改为：

```python
def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    apply_column_migrations(conn)
    _seed_onboarding_default_template(conn)
    conn.commit()
```

② `app/web/server.py`：在模块级请求模型区（`VerifyCodeRequest` 之后、`TurnOutcome` 之前）追加：

```python
class OnboardingTemplateItem(BaseModel):
    name: str
    owner_party: str
    due_offset_days: int
    required: bool


class OnboardingTemplateUpdateRequest(BaseModel):
    items: list[OnboardingTemplateItem]
```

在 `create_app` 内、`_require_hr_login` 定义之后追加辅助函数与两条路由（`conn`/`uuid`/`json`/`HTTPException`/`Request` 已在作用域内）：

```python
    _ALLOWED_OWNER_PARTIES = frozenset({"hr", "it", "admin", "finance", "dept"})

    def _require_role(request: Request, required_role: str) -> str:
        """校验登录态 + 角色，返回账号 username。非登录 401、登录但角色不符 403。"""
        auth = getattr(request.state, "auth", None)
        if not getattr(auth, "authenticated", False):
            raise HTTPException(status_code=401, detail="未登录")
        username = getattr(auth, "user_id", None)
        row = conn.execute(
            "SELECT role FROM hr_account WHERE username = ?", (username,)
        ).fetchone()
        if row is None or row[0] != required_role:
            raise HTTPException(status_code=403, detail="无权限")
        return username

    def _canonical_items(items: list[dict]) -> str:
        normalized = [
            {
                "name": item["name"].strip(),
                "owner_party": item["owner_party"].strip(),
                "due_offset_days": int(item["due_offset_days"]),
                "required": bool(item["required"]),
            }
            for item in items
        ]
        return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))

    @router.get("/api/onboarding-templates/{scope_type}/{scope_id}")
    def get_onboarding_template(scope_type: str, scope_id: str, request: Request):
        _require_role(request, "hr")
        if scope_type not in ("job", "department"):
            raise HTTPException(status_code=422, detail="scope_type 必须是 job 或 department")
        row = conn.execute(
            "SELECT version, items, updated_by, updated_at FROM onboarding_template "
            "WHERE scope_type = ? AND scope_id = ? ORDER BY version DESC LIMIT 1",
            (scope_type, scope_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="模板不存在")
        return {
            "scope_type": scope_type,
            "scope_id": scope_id,
            "version": row[0],
            "items": json.loads(row[1]),
            "updated_by": row[2],
            "updated_at": row[3],
        }

    @router.put("/api/onboarding-templates/{scope_type}/{scope_id}")
    def put_onboarding_template(
        scope_type: str, scope_id: str, req: OnboardingTemplateUpdateRequest, request: Request
    ):
        username = _require_role(request, "hr")
        if scope_type not in ("job", "department"):
            raise HTTPException(status_code=422, detail="scope_type 必须是 job 或 department")
        for item in req.items:
            if not item.name or not item.name.strip():
                raise HTTPException(status_code=422, detail="条目名称不能为空")
            if item.owner_party not in _ALLOWED_OWNER_PARTIES:
                raise HTTPException(status_code=422, detail=f"非法负责方: {item.owner_party}")

        new_items_json = _canonical_items([i.model_dump() for i in req.items])
        latest = conn.execute(
            "SELECT version, items FROM onboarding_template "
            "WHERE scope_type = ? AND scope_id = ? ORDER BY version DESC LIMIT 1",
            (scope_type, scope_id),
        ).fetchone()
        if latest is not None and _canonical_items(json.loads(latest[1])) == new_items_json:
            return {
                "scope_type": scope_type,
                "scope_id": scope_id,
                "version": latest[0],
                "items": json.loads(latest[1]),
                "unchanged": True,
            }

        new_version = (latest[0] + 1) if latest else 1
        conn.execute(
            "INSERT INTO onboarding_template "
            "(id, scope_type, scope_id, version, items, updated_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), scope_type, scope_id, new_version, new_items_json, username),
        )
        conn.commit()
        return {
            "scope_type": scope_type,
            "scope_id": scope_id,
            "version": new_version,
            "items": json.loads(new_items_json),
            "unchanged": False,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_onboarding_template_api.py -q`
Expected: 全绿。

---

## 交付前自查

- [ ] **全量测试全绿**（基线数 + 本单元新增，0 failed / 0 skipped）

```bash
./venv/bin/python -m pytest -q 2>&1 | tail -3
```

- [ ] **机器判据四条全绿**（在 worktree 根执行）

```bash
test -n "$(ls docs/superpowers/plans/*onboarding-flow*unit1*.md 2>/dev/null)"
grep -q '^### Task ' docs/superpowers/plans/*onboarding-flow*unit1*.md
grep -q 'Global Constraints' docs/superpowers/plans/*onboarding-flow*unit1*.md
grep -q 'onboarding_checklist' docs/superpowers/plans/*onboarding-flow*unit1*.md
```

Expected: 四条命令各自退出码 0（本文件满足）。

- [ ] **依赖文件 diff 为空**

```bash
git diff origin/main --stat -- requirements.txt pyproject.toml
```
Expected: 无输出。

- [ ] **6 张新表不进 `_ADDED_COLUMNS`；只有 `hr_account` 进**

```bash
grep -n "hr_account" app/storage/db.py | grep "_ADDED_COLUMNS" -A2
```
Expected: `_ADDED_COLUMNS` 里只有 `hr_account` 两条（`role`/`department`），6 张新表一个都没有。

- [ ] **`onboarding_item` 无内容列**（本包合规约束的机器判据）

```bash
./venv/bin/python -m pytest tests/test_db_onboarding_schema.py -q -k "no_content"
```
Expected: 绿。

- [ ] **模板接口 HR 角色 + 同内容幂等**

```bash
./venv/bin/python -m pytest tests/test_onboarding_template_api.py -q
```
Expected: 全绿。

---

## spec 覆盖对照

| spec Requirement | U1 的落点 | 完整兑现于 |
|---|---|---|
| `onboarding-checklist` · 清单模板由 HR 维护 | Task 1（`onboarding_template` 表）＋ Task 5（GET/PUT 接口、版本递增） | **U1 完整兑现存储层与接口** |
| `onboarding-checklist` · 按投递实例化清单 | Task 2（`onboarding_checklist.application_id` 唯一 = 一份投递一份清单的结构保证） | 实例化节点在 U2（2.1） |
| `onboarding-checklist` · 条目勾选与豁免留痕 | Task 2（`onboarding_item`）＋ Task 3（`onboarding_item_history`） | 勾选/豁免节点在 U2（2.2） |
| `onboarding-checklist` · 进度与逾期标记 | ⛔ U1 无落点 | U2（2.3 纯函数 / 2.4 页面） |
| `onboarding-checklist` · 清单动作幂等 | Task 2/3（唯一约束是 `effect_*` 幂等键的第二道防线） | 节点在 U2/U3 |
| `onboarding-visibility` · 用人部门经理只读本部门 | Task 4（`hr_account.role/department`） | 页面过滤在 U2（2.6） |
| `onboarding-visibility` · 进度页不含招聘数据 | ⛔ U1 无落点 | U2（2.6 HTML 反证） |
| `onboarding-visibility` · 查看写访问留痕 | Task 3（`onboarding_access_log` 表） | 写留痕行为在 U2（2.6） |
| `post-hire-data-disposition` · 入职完成时登记处置事项 | Task 3（`data_disposition_queue` ＋ `(application_id, category)` 唯一） | 登记节点在 U3（3.2） |
| `post-hire-data-disposition` · 留存策略未签认时只登记不执行 | Task 4（`Settings.retention_policy_signed_version` 默认 `None`） | 执行拒绝在 U4（4.1） |
| `post-hire-data-disposition` · 策略签认后按策略执行 | ⛔ U1 无落点 | U4（4.3） |
| `post-hire-data-disposition` · 处置不影响合规断言 | ⛔ U1 无落点 | U4（4.4） |

---

## 本计划相对 `tasks.md` / `design.md` 的偏离登记

| # | 偏离 | 位置 | 方向 | 理由 |
|---|---|---|---|---|
| 1 | 模板接口路径用 `/api/onboarding-templates/{scope_type}/{scope_id}`（tasks 1.6 字面 `/onboarding-templates/...` 无 `/api`） | Task 5 | 对齐代码库 | 全库 JSON 端点统一在 `/api/*` 下（`/api/resumes`、`/api/applications`…）；无 `/api` 的路径留给 HTML 页面命名空间 |
| 2 | 6 张新表各加 `id TEXT PRIMARY KEY` 代理主键（tasks 1.1–1.3 只列业务列，未写 `id`） | Task 1–3 | 对齐代码库 | `onboarding_item_history.item_id`、`data_disposition_queue` 等需要被引用或按行寻址；全库主表统一代理主键 |
| 3 | `onboarding_item.required` 用 `INTEGER CHECK (0,1)`（SQLite 无原生 BOOLEAN） | Task 2 | 等价实现 | 与 `screening_flag` 等既有表一致 |
| 4 | 占位模板 `scope_id='default'`（tasks 1.6 未定部门级默认的具体 `scope_id`） | Task 5 | 补全 | `scope_type='department'` 的默认回退模板需要一个稳定标识；HR 后续在页面按真实部门改 |
| 5 | `onboarding_item_history.at` / `onboarding_access_log.at` 沿用 tasks 1.3 字面列名（非 `occurred_at`/`accessed_at`） | Task 3 | 保一致 | 保 U2/U3 下游 plan 读同一份 tasks.md 时字段名不漂移 |
| 6 | tasks 1.5 字面把「老库升级既有表不变」写在 `tests/test_db_onboarding_schema.py`，本计划把它放进 `tests/test_db_migration.py`（Task 4） | Task 4 | 复用夹具 | `test_db_migration.py` 已持有 `_legacy_db`（模拟 .51 老库）这个权威夹具，回归守护复用它而非另起一套；`test_db_onboarding_schema.py` 只管新库结构/CHECK 反证 |

前 4 条均属「不改变外部可观察行为」的技术方案范畴，由 `run-build` 的 final review 确认。

---

## 提取验证记录（`spec-to-plan` 第 6 步，2026-10-08 实测）

本 worktree **无 `./venv`**（泳道只产出 plan 文档，运行环境在 run-build 的隔离 worktree 里由执行器装配），故本会话无法跑全量 pytest。已做的等价验证：

- **DDL/迁移静态验证**：把 6 张新表 DDL 与 `hr_account` 加列 DDL 用 `sqlite3`（系统 Python 3.9.6）在内存库原样跑过——6 张表建齐、`CHECK` 全部按预期拒绝非法值、`INSERT OR IGNORE` 种子幂等（重跑不产生第二行）、`ALTER TABLE ADD COLUMN ... CHECK` 生效（`role` 默认 `'hr'`、非法 `role` 被拒）、`at` 列名可用。
- **迁移顺序验证**：确认 `init_schema` 先 `executescript(SCHEMA)`（建 `hr_account` 含 `role/department`）再 `apply_column_migrations`，因此 `init_schema` 不会因 `hr_account` 进 `_ADDED_COLUMNS` 而炸；只有**直接调 `apply_column_migrations`** 的两个既有测试（`test_apply_column_migrations_is_idempotent`、`test_db_m2_u2_schema` 的 `test_old_job_table_gains_parse_confidence_threshold_via_migration`）会被打破，Task 4 已显式修复。
- **边界**：全量 pytest 与 spec 合规由 `run-build` 的 TDD + 两阶段 review 负责，本验证只证明代码可执行且内部自洽。

---

## 完成判据（`tasks.md` 第 1 章 checkbox 在这些全部成立后才勾）

1. 6 张新表在**新库**上建得出来，列集合与本节各 Task 断言一致
2. `hr_account` 在**老库**上经 `_ADDED_COLUMNS` 补出 `role`/`department`，历史行 `role='hr'`、`department=NULL`，其余列/行不变
3. `onboarding_item` 无内容/附件/证件号列（`test_onboarding_item_has_no_content_columns` 绿）
4. 直接 `INSERT` 非法 `scope_type`/`owner_party`/`status`/`required`/`category`/`planned_action` 全部被数据库 `CHECK` 拒
5. `Settings().retention_policy_signed_version is None`
6. `GET/PUT /api/onboarding-templates/{scope_type}/{scope_id}`：匿名 401、`interviewer`/`dept_manager` 403、HR 200；PUT 版本递增、同内容不新增版本
7. 全量 pytest 全绿、依赖文件 diff 为空

**合并后立刻解锁**：U2（清单页与进度，`effect_instantiate_checklist` / `effect_update_item` / 页面）可出一份 `spec-to-plan` 并行开工。

**下一步**：用 `run-build` 执行本计划。⛔ 不要在本会话里开始实现。
