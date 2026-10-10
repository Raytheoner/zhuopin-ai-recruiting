# 入职流程 · 交付单元 U2（清单页与进度）Implementation Plan

**Goal:** 为 onboarding-flow 变更包的 **U2 交付单元（tasks.md 第 2 章「清单页与进度」，tasks 2.1–2.7）** 产出实现计划。范围 = 实例化节点 `effect_instantiate_checklist` ＋ 勾选／豁免节点 `effect_update_item` ＋ 进度与逾期纯函数 `progress()` ＋ HR 清单页 ＋ HR 总览页 ＋ 部门经理只读页 ＋ U2 e2e。**不写 `effect_complete_onboarding` / `effect_enqueue_disposition` / `effect_execute_disposition`、不接任何 LLM、不产生任何外发**——这些是 U3–U4 的活。

**Architecture:** 两个 effect 节点落在新建 `app/graph/onboarding_nodes.py`（形态与 `app/graph/invite_nodes.py` 一致：Web 通道下 HTTP 端点直接调用普通 Python 函数，不建真实 LangGraph interrupt()）；幂等走既有 `app/storage/idempotency.py::idempotent_effect`（effect_key = `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 与业务写同事务）。进度纯函数落在 `app/agents/onboarding_progress.py`（只 import 标准库，⛔ 无任何 IO）。三个页面是静态 HTML（`app/web/static/`，`<!--BASE_HREF-->` + 相对路径 fetch）+ JSON 端点（`app/web/server.py`）。

**Tech Stack:** Python（项目 `./venv`）· SQLite（标准库 `sqlite3`，WAL + `PRAGMA foreign_keys=ON`）· FastAPI · pytest · **不引入任何新依赖**（`requirements.txt` / `pyproject.toml` diff 必须为空）。

---

## 输入与交付单元划分

本计划为 **U2（tasks.md 第 2 章「清单页与进度」，tasks 2.1–2.7）** 出计划。输入真源是 `openspec/changes/onboarding-flow/specs/**/spec.md` 与 `design.md`；`tasks.md` **只用于确认 U2 章节边界（2.1–2.7）**，不作为计划输入。

与本单元相关的 spec 能力文件（自行列出，两份，理由如下）：

| 能力文件 | 与本单元的关系 |
|---|---|
| `specs/onboarding-checklist/spec.md` | **核心**。实例化（按投递生成清单）、条目勾选与豁免留痕、进度与逾期标记、清单动作幂等四条 Requirement 的行为落点全在本单元 |
| `specs/onboarding-visibility/spec.md` | 部门经理只读本部门、进度页不含招聘数据、查看写访问留痕三条 Requirement 的行为落点（页面过滤 + `onboarding_access_log` 写入）在本单元 |

**说明**：`specs/onboarding-completion/spec.md`（终态确认）与 `specs/post-hire-data-disposition/spec.md`（处置执行）的**行为层**分别属于 U3/U4，U2 不碰（本单元只消费 U1 已建好的 `data_disposition_queue` 空表结构，不写入）。

相关 design Decisions：D1（材料不入库→页面无上传、接口拒 multipart）、D2（HR＋部门经理只读本部门→`hr_account.role/department` 过滤 + 本部门 dept 条目可勾选）、D5（不做提醒→逾期标记仅页面打开时计算）、D7（U2 前置 = U1；`offer-generation` U5 不阻塞 U2，夹具置 `hired`）。

**上游 U1 已合 main 的真实实现核对**（本会话在 worktree 磁盘上逐项核实）：

| 项 | 磁盘真身 | 引用 |
|---|---|---|
| 六张入职域表 | ✅ `onboarding_template` / `onboarding_checklist` / `onboarding_item` / `onboarding_item_history` / `onboarding_access_log` / `data_disposition_queue` 均在 `SCHEMA` | `app/storage/db.py` 714–824 行 |
| `hr_account.role/department` | ✅ `role CHECK IN ('hr','interviewer','dept_manager')` 默认 `'hr'`；`department` 可空；老库经 `_ADDED_COLUMNS` + `_rebuild_hr_account_role_check` | `app/storage/db.py` 686–700 行 |
| 模板接口 | ✅ `/api/onboarding-templates/{scope_type}/{scope_id}`（**带 `/api` 前缀**；tasks 1.6 字面 `/onboarding-templates/...` 无前缀，以磁盘为准） | `app/web/server.py` 1916–1996 行 |
| 占位模板 v1 | ✅ `scope_type='department', scope_id='default', version=1`，六条（合同/材料/体检/设备/账号/带教人） | `app/storage/db.py` `_seed_onboarding_default_template` |
| `offer` 表 | ✅ `offer.status='accepted'` 可表达；`start_date NOT NULL` | `app/storage/db.py` 1287–1312 行 |
| `application.status='hired'` | ❌ **磁盘仍是 `CHECK IN ('active','rejected','withdrawn')`，不含 `'hired'`**（`{ongoing,hired,refused}` 迁移属 offer-generation U5，未交付） | `app/storage/db.py` 432–447 行 |
| `stage` 预置 `hired` | ✅ `INSERT OR IGNORE ... ('hired','已入职','hired')` | `app/storage/db.py` 423 行 |

**据此的必要偏离**：spec 与 tasks 2.1 的实例化前置是 `application.status='hired'`，而磁盘 CHECK 尚不容纳该值 ⇒ 本计划 **Task 1** 把 CHECK 放宽到含 `'hired'`（表重建迁移，同 `_rebuild_hr_account_role_check` 先例），让"夹具置 `hired`"可执行；`ongoing/refused` 仍留给 offer-generation U5（见文末「偏离登记」与「留步登记」）。

---

## Global Constraints

以下条目从 `CLAUDE.md`「工程铁律」「合规红线」逐字复制（或标注为"本包约束"）。**每个 Task 的验收隐含包含本节全部内容**；reviewer 会把这一段当注意力透镜。

### 工程铁律（不可违背，与本单元相关条目逐字）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。

2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。

> 本单元直接触发这两条：`effect_instantiate_checklist` / `effect_update_item` 是 L4 副作用节点，各自独占、各自带幂等键（Task 2/3）；`progress()` 是 L3 纯函数（Task 4）。铁律 3–7（AI 评分持久化、`evidence_ref`、`temperature=0`、企微回调、`langgraph>=1.0.10`）**本单元不触发**——无 LLM、无评分、无企微、不改依赖版本。

### 合规红线（本单元相关条目）

七条红线在本单元**均不触发**（无排序/淘汰、无人脸、无 AI 生成 JD/拒信/邀约、无简历数据出境、无历史录用监督信号、无候选人对外通道、无硬门槛规则）。本单元对应到合规的形态只有一条，来自**本包约束**：

> **材料本身不入库**（只存清单条目状态）；进度可见性限「HR 全量／用人部门只读本部门」。

**reviewer 的机械判据**：`onboarding_item` 无 `content` / `attachment` / `id_number` / `file` 类列（U1 已由 `test_onboarding_item_has_no_content_columns` 锁死，U2 不新增列）；页面无上传控件、接口拒 multipart（Task 5）；部门进度页 JSON 与 HTML 均无评分/排名/简历/联系方式/复核工作台链接（Task 7 HTML＋JSON 反证）。

### 部署约束（本单元相关）

- **路径前缀就绪**：页面资源与接口调用一律相对路径，禁止硬编码 `/static/…` `/api/…`。验收标准是挂到任意子路径下都能正常工作，且有测试覆盖（Task 5/6/7 的 HTML 用 `<!--BASE_HREF-->` + 相对 fetch；子路径前缀测试沿用 `tests/test_job_views_api.py::test_all_view_endpoints_and_assets_work_under_any_root_path` 同款判据）。
- ⛔ 本单元不引 `.51` 服务器、不处理真实简历、不产生任何不可逆动作、不建定时任务、不发任何消息。

---

## 明确的范围边界（U2 **不做**什么）

| 不做 | 归属 |
|---|---|
| `effect_complete_onboarding`（终态） / `effect_enqueue_disposition` / 终态不变式 | U3（3.1–3.5） |
| `effect_execute_disposition` / 脱敏规则 / 断言兼容 | U4（4.1–4.4） |
| `application.status` 的 `{ongoing, hired, refused}` 全量迁移 | offer-generation U5 / M2 U1；U2 只放宽到含 `'hired'`（Task 1） |
| 模板维护**页面**（HTML 表单） | 不在本单元；模板维护接口 U1 已交付（JSON），页面形态不属 U2 范围 |
| 提醒 / 定时任务 / 企微建号 | Non-Goals（design D5）；TD-11 记入 tech-debt |
| 新员工自助入口、材料收集与存储 | Non-Goals（design D2/D1） |

**U2 合并后系统的可观察行为**：对 `hired`＋Offer 已接受的投递，HR 可生成清单、逐条勾选／豁免／撤回、看进度与逾期；HR 总览页看到全部 `open` 清单；部门经理只读本部门清单进度且每次查看留痕；清单页无上传控件；无任何自动提醒或自动终态。

---

## File Structure

| 文件 | 动作 | 责任 |
|---|---|---|
| `app/storage/db.py` | 修改 | `application` 表 CREATE TABLE 的 status CHECK 加 `'hired'`；新增 `_application_status_check_allows_hired` / `_rebuild_application_status_check`；`init_schema` 调用重建（Task 1） |
| `app/graph/onboarding_nodes.py` | 新建 | `effect_instantiate_checklist`（Task 2）＋ `effect_update_item`（Task 3）＋ 异常类型与模板解析辅助 |
| `app/agents/onboarding_progress.py` | 新建 | `progress(items, start_date, today)` 纯函数（Task 4） |
| `app/web/server.py` | 修改 | 请求模型 ＋ `_require_hr_or_dept_manager` ＋ 清单/总览/部门 JSON 端点 ＋ 三个 HTML 页面路由（Task 5/6/7） |
| `app/web/static/onboarding_checklist.html` | 新建 | HR 清单页（Task 5） |
| `app/web/static/onboarding_overview.html` | 新建 | HR 总览页（Task 6） |
| `app/web/static/onboarding_department.html` | 新建 | 部门经理只读页（Task 7） |
| `tests/test_db_application_status_hired.py` | 新建 | `application.status` CHECK 放宽（Task 1） |
| `tests/test_onboarding_nodes.py` | 新建 | 实例化/勾选豁免节点的单元测试（Task 2/3） |
| `tests/test_onboarding_progress.py` | 新建 | 进度与逾期纯函数 + 无副作用 grep 反证（Task 4） |
| `tests/test_onboarding_pages.py` | 新建 | 三个页面的 API/HTML/上传反证/访问留痕（Task 5/6/7） |
| `tests/test_onboarding_e2e.py` | 新建 | U2 e2e（Task 8） |

> ⛔ Codex 泳道：本计划各 Task **不写 git add/commit/push 步骤**——worktree 内不能自行提交，收口由执行器 `run-lanes.sh` → `scripts/lane_collect.py` 代做（AGENTS.md §4）。每个 Task 以"测试全绿"收尾即可。

---

### Task 1: `application.status` 前置迁移——CHECK 放宽到含 `'hired'`

**Files:**
- Modify: `app/storage/db.py`（`SCHEMA` 的 `application` CREATE TABLE + `init_schema`）
- Test: `tests/test_db_application_status_hired.py`（新建）

**Interfaces:**
- Consumes: `app.storage.db.get_connection(db_path)`、`app.storage.db.init_schema(conn)`（均已存在，签名不改）
- Produces: `application.status` CHECK = `('active','rejected','withdrawn','hired')`（新库与老库一致）；老库重建后既有行与三个索引不变

- [ ] **Step 1: Write the failing test**

新建 `tests/test_db_application_status_hired.py`：

```python
"""onboarding-flow U2 前置（tasks 2.1）：application.status CHECK 放宽到含 'hired'。

磁盘真身：application.status 仍是 ('active','rejected','withdrawn')（offer-generation
U5 的 {ongoing,hired,refused} 迁移未交付）。U2 的实例化前置要求 status='hired'，
测试夹具要能直接置位，故本 Task 只放宽到含 'hired'（表重建，同
_rebuild_hr_account_role_check 先例）。ongoing/refused 仍归 offer-generation U5。
"""
import sqlite3

import pytest

from app.storage.db import get_connection, init_schema


def _seed_referenced_rows(conn):
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '嵌入式工程师', 'approved')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('res-1', 'j1', 'synthetic', 'a.docx', 'sha-1', 'hr-1')"
    )
    conn.commit()


def test_fresh_schema_accepts_hired_status(tmp_path):
    conn = get_connection(str(tmp_path / "app.db"))
    init_schema(conn)
    _seed_referenced_rows(conn)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status) "
        "VALUES ('app-1', 'cand-1', 'j1', 'res-1', 'hired', 'hired')"
    )
    conn.commit()
    assert conn.execute("SELECT status FROM application WHERE id='app-1'").fetchone() == ("hired",)


def test_fresh_schema_still_rejects_bogus_status(tmp_path):
    conn = get_connection(str(tmp_path / "app.db"))
    init_schema(conn)
    _seed_referenced_rows(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status) "
            "VALUES ('app-bad', 'cand-1', 'j1', 'res-1', 'initial', 'bogus')"
        )


def test_legacy_three_value_status_check_is_widened(tmp_path):
    """老库（三值 CHECK）升级后 'hired' 可写、既有行不变、索引复原、FK 复验通过。"""
    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(path)
    raw.executescript(
        """
        CREATE TABLE stage (
            id TEXT PRIMARY KEY NOT NULL,
            name TEXT NOT NULL,
            stage_type TEXT NOT NULL
        );
        CREATE TABLE job (id TEXT PRIMARY KEY, title TEXT NOT NULL, department TEXT,
                          status TEXT NOT NULL DEFAULT 'drafting',
                          created_at TEXT NOT NULL DEFAULT (datetime('now')),
                          parse_confidence_threshold REAL NOT NULL DEFAULT 0.7);
        CREATE TABLE candidate (id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL,
                                phone_hash TEXT,
                                created_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE resume (id TEXT PRIMARY KEY NOT NULL,
                             job_id TEXT NOT NULL REFERENCES job(id),
                             sample_class TEXT NOT NULL,
                             file_name TEXT NOT NULL,
                             content_sha256 TEXT NOT NULL,
                             status TEXT NOT NULL DEFAULT 'pending',
                             uploaded_by TEXT NOT NULL,
                             uploaded_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE application (
            id TEXT PRIMARY KEY NOT NULL,
            candidate_id TEXT NOT NULL REFERENCES candidate(id),
            job_id TEXT NOT NULL REFERENCES job(id),
            resume_id TEXT NOT NULL REFERENCES resume(id),
            current_stage_id TEXT NOT NULL REFERENCES stage(id),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'rejected', 'withdrawn')),
            kanban_state TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX idx_application_resume ON application (resume_id);
        CREATE INDEX idx_application_job ON application (job_id);
        CREATE INDEX idx_application_candidate ON application (candidate_id);
        INSERT INTO stage (id, name, stage_type) VALUES ('initial', '初筛', 'initial');
        INSERT INTO stage (id, name, stage_type) VALUES ('hired', '已入职', 'hired');
        INSERT INTO job (id, title, status) VALUES ('j1', '嵌入式工程师', 'approved');
        INSERT INTO candidate (id, name) VALUES ('cand-1', '张三');
        INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by)
               VALUES ('res-1', 'j1', 'synthetic', 'a.docx', 'sha-1', 'hr-1');
        INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status)
               VALUES ('app-old', 'cand-1', 'j1', 'res-1', 'initial', 'active');
        """
    )
    raw.commit()
    raw.close()

    conn = get_connection(str(path))
    init_schema(conn)

    assert conn.execute("SELECT status FROM application WHERE id='app-old'").fetchone() == ("active",)
    conn.execute(
        "UPDATE application SET status='hired', current_stage_id='hired' WHERE id='app-old'"
    )
    conn.commit()
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    for idx in ("idx_application_resume", "idx_application_job", "idx_application_candidate"):
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (idx,)
        ).fetchone()
        assert row is not None, f"{idx} 应在重建后存在"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_db_application_status_hired.py -q`
Expected: FAIL —— `test_fresh_schema_accepts_hired_status` 在 `INSERT ... status='hired'` 处 `sqlite3.IntegrityError`（CHECK 拒绝 `'hired'`）；`test_legacy_three_value_status_check_is_widened` 在 `UPDATE ... status='hired'` 处同样失败。

- [ ] **Step 3: Write minimal implementation**

① `app/storage/db.py` 的 `SCHEMA` 里 `application` CREATE TABLE 的 status CHECK 从：

```sql
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'rejected', 'withdrawn')),
```

改为：

```sql
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'rejected', 'withdrawn', 'hired')),
```

② 在 `_rebuild_hr_account_role_check` 之后、`init_schema` 之前追加两个函数：

```python
def _application_status_check_allows_hired(conn: sqlite3.Connection) -> bool:
    """application.status 的 CHECK 是否已含 hired。SQLite 改不了 CHECK，本判断
    与 _stage_type_check_complete / _role_check_allows_dept_manager 同一手法。"""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='application'"
    ).fetchone()
    return row is not None and "'hired'" in row[0]


def _rebuild_application_status_check(conn: sqlite3.Connection) -> None:
    """把 application.status 的 CHECK 从三值放宽到四值（+hired）。

    只放宽到含 'hired'——U2 实例化前置需要它，且 design.md 风险表明写「U2 用夹具
    直接置 application.status=hired」。⛔ 不加 ongoing/refused：那两值连同三值语义
    迁移一起，归 offer-generation U5（effect_apply_offer_outcome）。

    application 被 application_stage_history / rejection_record / offer /
    onboarding_checklist / onboarding_access_log / data_disposition_queue 外键引用，
    重建期间 PRAGMA foreign_keys=OFF，完成后 foreign_key_check 复验；三个索引
    （idx_application_resume / idx_application_job / idx_application_candidate）
    随 DROP TABLE 一起消失，必须原样重建。PRAGMA 在事务内是 no-op，try 内 commit、
    except 里 rollback 之后，finally 再重开（同 _rebuild_hr_account_role_check）。
    """
    if _application_status_check_allows_hired(conn):
        return
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN")
        conn.execute(
            """
            CREATE TABLE application_new (
                id TEXT PRIMARY KEY NOT NULL,
                candidate_id TEXT NOT NULL REFERENCES candidate(id),
                job_id TEXT NOT NULL REFERENCES job(id),
                resume_id TEXT NOT NULL REFERENCES resume(id),
                current_stage_id TEXT NOT NULL REFERENCES stage(id),
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'rejected', 'withdrawn', 'hired')),
                kanban_state TEXT CHECK (kanban_state IS NULL OR kanban_state IN ('pending_reject')),
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "INSERT INTO application_new "
            "(id, candidate_id, job_id, resume_id, current_stage_id, status, kanban_state, created_at) "
            "SELECT id, candidate_id, job_id, resume_id, current_stage_id, status, kanban_state, created_at "
            "FROM application"
        )
        conn.execute("DROP TABLE application")
        conn.execute("ALTER TABLE application_new RENAME TO application")
        conn.execute("CREATE UNIQUE INDEX idx_application_resume ON application (resume_id)")
        conn.execute("CREATE INDEX idx_application_job ON application (job_id)")
        conn.execute("CREATE INDEX idx_application_candidate ON application (candidate_id)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise sqlite3.IntegrityError(f"application 重建后外键不一致: {violations}")
```

③ `init_schema` 里，在 `_rebuild_hr_account_role_check(conn)` 之后追加一行：

```python
    _rebuild_hr_account_role_check(conn)
    _rebuild_application_status_check(conn)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_db_application_status_hired.py -q`
Expected: 全绿（fresh 与 legacy 两条路径都通过）。

---

### Task 2: `effect_instantiate_checklist` 节点（tasks 2.1）

**Files:**
- New: `app/graph/onboarding_nodes.py`
- Test: `tests/test_onboarding_nodes.py`（新建）

**Interfaces:**
- Consumes: `app.storage.idempotency.idempotent_effect`（已存在）；表 `application` / `offer` / `job` / `onboarding_template` / `onboarding_checklist` / `onboarding_item`（均已存在）
- Produces: `effect_instantiate_checklist(conn, *, thread_id, business_key, created_by) -> str | None`（幂等命中返回 `None`，调用方据此回查既有清单）；异常 `ChecklistInstantiateRejected` / `ChecklistTemplateMissing`

- [ ] **Step 1: Write the failing test**

新建 `tests/test_onboarding_nodes.py`（本 Task 只先建文件头、夹具与 Task 2 用例，Task 3 追加）：

```python
"""onboarding-flow U2 节点：effect_instantiate_checklist（2.1）与 effect_update_item（2.2）。"""
import json

import pytest

from app.graph.onboarding_nodes import (
    ChecklistInstantiateRejected,
    ChecklistTemplateMissing,
    effect_instantiate_checklist,
)
from app.storage.db import get_connection, init_schema
from app.storage.hr_account import upsert_account


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "onboarding.db"))
    init_schema(c)
    return c


def _seed_job_candidate_resume(conn, department="研发部"):
    conn.execute(
        "INSERT INTO job (id, title, department, status) VALUES ('j1', '嵌入式工程师', ?, 'approved')",
        (department,),
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('res-1', 'j1', 'synthetic', 'a.docx', 'sha-1', 'hr-1')"
    )
    conn.commit()


def _seed_hired_application(conn, application_id="app-1", department="研发部", start_date="2026-10-20"):
    _seed_job_candidate_resume(conn, department=department)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status) "
        "VALUES (?, 'cand-1', 'j1', 'res-1', 'hired', 'hired')",
        (application_id,),
    )
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, report_to, status, approval_round, created_by) "
        "VALUES (?, ?, 'j1', ?, ?, 'manager-1', 'accepted', 1, 'hr')",
        (f"offer-{application_id}", application_id, department, start_date),
    )
    conn.commit()


def _seed_account(conn, username, role, department=None):
    account_id = upsert_account(conn, username=username, password="s3cret!")
    conn.execute("UPDATE hr_account SET role=?, department=? WHERE id=?", (role, department, account_id))
    conn.commit()
    return account_id


# ── Task 2：effect_instantiate_checklist ────────────────────────────────


def test_instantiate_creates_checklist_and_items(conn):
    _seed_hired_application(conn)
    checklist_id = effect_instantiate_checklist(
        conn, thread_id="app-1", business_key="instantiate", created_by="hr-user"
    )
    assert checklist_id is not None
    cl = conn.execute(
        "SELECT application_id, template_version, start_date, status FROM onboarding_checklist WHERE id=?",
        (checklist_id,),
    ).fetchone()
    assert cl == ("app-1", 1, "2026-10-20", "open")
    items = conn.execute(
        "SELECT name, owner_party, status FROM onboarding_item WHERE checklist_id=? ORDER BY rowid",
        (checklist_id,),
    ).fetchall()
    assert len(items) == 6
    assert all(status == "pending" for _, _, status in items)


def test_instantiate_rejects_non_hired_application(conn):
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status) "
        "VALUES ('app-1', 'cand-1', 'j1', 'res-1', 'initial', 'active')"
    )
    conn.commit()
    with pytest.raises(ChecklistInstantiateRejected):
        effect_instantiate_checklist(
            conn, thread_id="app-1", business_key="instantiate", created_by="hr-user"
        )


def test_instantiate_rejects_hired_without_accepted_offer(conn):
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status) "
        "VALUES ('app-1', 'cand-1', 'j1', 'res-1', 'hired', 'hired')"
    )
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, report_to, status, approval_round, created_by) "
        "VALUES ('offer-1', 'app-1', 'j1', '研发部', '2026-10-20', 'manager-1', 'pending_approval', 1, 'hr')"
    )
    conn.commit()
    with pytest.raises(ChecklistInstantiateRejected):
        effect_instantiate_checklist(
            conn, thread_id="app-1", business_key="instantiate", created_by="hr-user"
        )


def test_instantiate_rerun_returns_none_and_keeps_single_checklist(conn):
    _seed_hired_application(conn)
    first = effect_instantiate_checklist(
        conn, thread_id="app-1", business_key="instantiate", created_by="hr-user"
    )
    second = effect_instantiate_checklist(
        conn, thread_id="app-1", business_key="instantiate", created_by="hr-user"
    )
    assert first is not None
    assert second is None
    assert conn.execute("SELECT COUNT(*) FROM onboarding_checklist WHERE application_id='app-1'").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM onboarding_item i JOIN onboarding_checklist c ON c.id=i.checklist_id "
        "WHERE c.application_id='app-1'"
    ).fetchone()[0] == 6


def test_instantiate_prefers_job_template_over_department(conn):
    _seed_hired_application(conn)
    conn.execute(
        "INSERT INTO onboarding_template (id, scope_type, scope_id, version, items, updated_by) "
        "VALUES ('job-tpl', 'job', 'j1', 1, ?, 'hr')",
        (json.dumps([{"name": "岗位专属条目", "owner_party": "hr", "due_offset_days": 0, "required": True}], ensure_ascii=False),),
    )
    conn.commit()
    checklist_id = effect_instantiate_checklist(
        conn, thread_id="app-1", business_key="instantiate", created_by="hr-user"
    )
    items = conn.execute("SELECT name FROM onboarding_item WHERE checklist_id=?", (checklist_id,)).fetchall()
    assert items == [("岗位专属条目",)]


def test_instantiate_missing_template_rejected(conn):
    _seed_hired_application(conn)
    conn.execute("DELETE FROM onboarding_template")
    conn.commit()
    with pytest.raises(ChecklistTemplateMissing):
        effect_instantiate_checklist(
            conn, thread_id="app-1", business_key="instantiate", created_by="hr-user"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_onboarding_nodes.py -q -k "instantiate"`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.graph.onboarding_nodes'`。

- [ ] **Step 3: Write minimal implementation**

新建 `app/graph/onboarding_nodes.py`：

```python
"""onboarding-flow U2 清单页与进度的 L4 编排层节点（tasks 2.1/2.2）。

与 app/graph/invite_nodes.py 同一形态：Web 通道下"挂起等人确认"由 HTTP 端点
直接调用普通 Python 函数达成，不建真实 LangGraph interrupt()（2026-08-26 判例）。

thread_id 语义：实例化取 application_id（一份投递一份清单）；条目更新取 item_id
（tasks 2.2 幂等键 {item_id}:effect_update_item:{to_status}:{request_id}）。
"""
from __future__ import annotations

import json
import sqlite3
import uuid

from app.storage.idempotency import idempotent_effect


class ChecklistInstantiateRejected(Exception):
    """投递不满足实例化前置（application.status=hired 且 offer.status=accepted）。"""


class ChecklistTemplateMissing(Exception):
    """岗位与部门（含 default 兜底）都没有清单模板。"""


def _resolve_template(conn: sqlite3.Connection, *, job_id: str) -> dict | None:
    """模板选择（design D1）：岗位模板优先，否则部门模板（job.department），
    再否则部门 default 兜底模板。返回 {'version': int, 'items': list[dict]}。
    """
    row = conn.execute(
        "SELECT version, items FROM onboarding_template "
        "WHERE scope_type='job' AND scope_id=? ORDER BY version DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    if row is not None:
        return {"version": row[0], "items": json.loads(row[1])}

    dept = conn.execute("SELECT department FROM job WHERE id=?", (job_id,)).fetchone()
    department = dept[0] if dept is not None else None
    for scope_id in (department, "default"):
        if not scope_id:
            continue
        row = conn.execute(
            "SELECT version, items FROM onboarding_template "
            "WHERE scope_type='department' AND scope_id=? ORDER BY version DESC LIMIT 1",
            (scope_id,),
        ).fetchone()
        if row is not None:
            return {"version": row[0], "items": json.loads(row[1])}
    return None


@idempotent_effect("effect_instantiate_checklist")
def effect_instantiate_checklist(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str, created_by: str
) -> str:
    """effect_* 节点：写 onboarding_checklist + onboarding_item，同事务。

    前置 = application.status='hired' AND offer.status='accepted'；入职日读
    offer.start_date（offer-generation U1 已落盘的唯一入职日列）。
    幂等键 = {application_id}:effect_instantiate_checklist:{business_key}；
    命中 effect_log 由装饰器短路返回 None，调用方回查既有清单。
    """
    application_id = thread_id
    row = conn.execute(
        "SELECT a.status, a.job_id, o.status AS offer_status, o.start_date "
        "FROM application a LEFT JOIN offer o ON o.application_id = a.id "
        "WHERE a.id = ?",
        (application_id,),
    ).fetchone()
    if row is None or row[0] != "hired" or row[2] != "accepted":
        raise ChecklistInstantiateRejected("仅 hired 且 Offer 已接受的投递可生成清单")
    if row[3] is None:
        raise ChecklistInstantiateRejected("Offer 缺少入职日期")

    template = _resolve_template(conn, job_id=row[1])
    if template is None:
        raise ChecklistTemplateMissing("岗位与部门均无清单模板")

    checklist_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO onboarding_checklist "
        "(id, application_id, template_version, start_date, created_by) "
        "VALUES (?, ?, ?, ?, ?)",
        (checklist_id, application_id, template["version"], row[3], created_by),
    )
    for item in template["items"]:
        conn.execute(
            "INSERT INTO onboarding_item "
            "(id, checklist_id, name, owner_party, due_offset_days, required) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                checklist_id,
                item["name"],
                item["owner_party"],
                int(item["due_offset_days"]),
                1 if item["required"] else 0,
            ),
        )
    return checklist_id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_onboarding_nodes.py -q -k "instantiate"`
Expected: 全绿（instantiate 六个用例）。

---

### Task 3: `effect_update_item` 节点（tasks 2.2）

**Files:**
- Modify: `app/graph/onboarding_nodes.py`（追加）
- Test: `tests/test_onboarding_nodes.py`（追加）

**Interfaces:**
- Produces: `effect_update_item(conn, *, thread_id, business_key, to_status, reason, operator_username) -> None`（幂等命中返回 `None`）；异常 `ItemUpdateRejected` / `ItemOperatorForbidden`

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_onboarding_nodes.py`（并把顶部 import 的 `ItemOperatorForbidden` / `ItemUpdateRejected` / `effect_update_item` 加进 `from app.graph.onboarding_nodes import`）：

```python
# ── Task 3：effect_update_item ─────────────────────────────────────────


def _instantiate(conn, application_id="app-1"):
    return effect_instantiate_checklist(
        conn, thread_id=application_id, business_key="instantiate", created_by="hr-user"
    )


def _item(conn, checklist_id, owner_party):
    row = conn.execute(
        "SELECT id FROM onboarding_item WHERE checklist_id=? AND owner_party=? LIMIT 1",
        (checklist_id, owner_party),
    ).fetchone()
    return row[0]


def test_update_item_done_writes_history(conn):
    _seed_hired_application(conn)
    _seed_account(conn, "hr-user", "hr")
    checklist_id = _instantiate(conn)
    item_id = _item(conn, checklist_id, "hr")
    effect_update_item(
        conn, thread_id=item_id, business_key="done:r1",
        to_status="done", reason=None, operator_username="hr-user",
    )
    assert conn.execute("SELECT status FROM onboarding_item WHERE id=?", (item_id,)).fetchone()[0] == "done"
    hist = conn.execute(
        "SELECT from_status, to_status, acted_by FROM onboarding_item_history WHERE item_id=?",
        (item_id,),
    ).fetchall()
    assert hist == [("pending", "done", "hr-user")]


def test_update_item_waived_requires_reason(conn):
    _seed_hired_application(conn)
    _seed_account(conn, "hr-user", "hr")
    checklist_id = _instantiate(conn)
    item_id = _item(conn, checklist_id, "hr")
    with pytest.raises(ItemUpdateRejected):
        effect_update_item(
            conn, thread_id=item_id, business_key="waived:r1",
            to_status="waived", reason=None, operator_username="hr-user",
        )
    assert conn.execute("SELECT status FROM onboarding_item WHERE id=?", (item_id,)).fetchone()[0] == "pending"


def test_update_item_waived_with_reason(conn):
    _seed_hired_application(conn)
    _seed_account(conn, "hr-user", "hr")
    checklist_id = _instantiate(conn)
    item_id = _item(conn, checklist_id, "hr")
    effect_update_item(
        conn, thread_id=item_id, business_key="waived:r1",
        to_status="waived", reason="体检报告无需提交", operator_username="hr-user",
    )
    assert conn.execute("SELECT status, reason FROM onboarding_item WHERE id=?", (item_id,)).fetchone() == ("waived", "体检报告无需提交")


def test_update_item_pending_withdraw(conn):
    _seed_hired_application(conn)
    _seed_account(conn, "hr-user", "hr")
    checklist_id = _instantiate(conn)
    item_id = _item(conn, checklist_id, "hr")
    effect_update_item(
        conn, thread_id=item_id, business_key="done:r1",
        to_status="done", reason=None, operator_username="hr-user",
    )
    effect_update_item(
        conn, thread_id=item_id, business_key="pending:r2",
        to_status="pending", reason=None, operator_username="hr-user",
    )
    assert conn.execute("SELECT status FROM onboarding_item WHERE id=?", (item_id,)).fetchone()[0] == "pending"


def test_update_item_dept_manager_can_operate_own_dept_item(conn):
    _seed_hired_application(conn, department="研发部")
    _seed_account(conn, "mgr-rd", "dept_manager", department="研发部")
    checklist_id = _instantiate(conn)
    item_id = _item(conn, checklist_id, "dept")
    effect_update_item(
        conn, thread_id=item_id, business_key="done:r1",
        to_status="done", reason=None, operator_username="mgr-rd",
    )
    assert conn.execute("SELECT status FROM onboarding_item WHERE id=?", (item_id,)).fetchone()[0] == "done"


def test_update_item_dept_manager_cannot_operate_other_party_item(conn):
    _seed_hired_application(conn, department="研发部")
    _seed_account(conn, "mgr-rd", "dept_manager", department="研发部")
    checklist_id = _instantiate(conn)
    item_id = _item(conn, checklist_id, "hr")
    with pytest.raises(ItemOperatorForbidden):
        effect_update_item(
            conn, thread_id=item_id, business_key="done:r1",
            to_status="done", reason=None, operator_username="mgr-rd",
        )


def test_update_item_cross_department_manager_rejected(conn):
    _seed_hired_application(conn, department="研发部")
    _seed_account(conn, "mgr-proc", "dept_manager", department="采购部")
    checklist_id = _instantiate(conn)
    item_id = _item(conn, checklist_id, "dept")
    with pytest.raises(ItemOperatorForbidden):
        effect_update_item(
            conn, thread_id=item_id, business_key="done:r1",
            to_status="done", reason=None, operator_username="mgr-proc",
        )


def test_update_item_rerun_no_second_history(conn):
    _seed_hired_application(conn)
    _seed_account(conn, "hr-user", "hr")
    checklist_id = _instantiate(conn)
    item_id = _item(conn, checklist_id, "hr")
    effect_update_item(
        conn, thread_id=item_id, business_key="done:r1",
        to_status="done", reason=None, operator_username="hr-user",
    )
    second = effect_update_item(
        conn, thread_id=item_id, business_key="done:r1",
        to_status="done", reason=None, operator_username="hr-user",
    )
    assert second is None
    assert conn.execute("SELECT COUNT(*) FROM onboarding_item_history WHERE item_id=?", (item_id,)).fetchone()[0] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_onboarding_nodes.py -q -k "update_item"`
Expected: FAIL —— `ImportError: cannot import name 'effect_update_item' from 'app.graph.onboarding_nodes'`。

- [ ] **Step 3: Write minimal implementation**

追加到 `app/graph/onboarding_nodes.py`（Task 2 的模块之后）：

```python
class ItemUpdateRejected(Exception):
    """条目状态流转非法（状态机不合法 / 豁免缺原因）。"""


class ItemOperatorForbidden(Exception):
    """操作人无权修改该条目（非 HR、非对应部门经理）。"""


_ALLOWED_ITEM_TRANSITIONS = {
    ("pending", "done"),
    ("pending", "waived"),
    ("done", "pending"),
    ("waived", "pending"),
}


@idempotent_effect("effect_update_item")
def effect_update_item(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    to_status: str,
    reason: str | None,
    operator_username: str,
) -> None:
    """effect_* 节点：写 onboarding_item + onboarding_item_history，同事务。

    操作人限 HR 或"该条目对应部门经理"：HR 无条件；dept_manager 仅当
    owner_party='dept' 且 manager.department == job.department（design D2）。
    幂等键 = {item_id}:effect_update_item:{to_status}:{request_id}（business_key
    由调用方拼 {to_status}:{request_id}）。
    """
    item_id = thread_id
    item = conn.execute(
        "SELECT i.id, i.checklist_id, i.owner_party, i.status, c.application_id "
        "FROM onboarding_item i JOIN onboarding_checklist c ON c.id = i.checklist_id "
        "WHERE i.id = ?",
        (item_id,),
    ).fetchone()
    if item is None:
        raise ItemUpdateRejected("条目不存在")
    checklist_id, owner_party, from_status, application_id = item[1], item[2], item[3], item[4]

    if to_status not in ("done", "waived", "pending"):
        raise ItemUpdateRejected("非法目标状态")
    if (from_status, to_status) not in _ALLOWED_ITEM_TRANSITIONS:
        raise ItemUpdateRejected(f"非法状态流转: {from_status} -> {to_status}")
    if to_status == "waived" and (not reason or not reason.strip()):
        raise ItemUpdateRejected("豁免必须填写原因")

    operator = conn.execute(
        "SELECT role, department FROM hr_account WHERE username = ?", (operator_username,)
    ).fetchone()
    if operator is None:
        raise ItemOperatorForbidden("操作人不存在")
    role, operator_department = operator[0], operator[1]
    if role == "hr":
        pass
    elif role == "dept_manager":
        if owner_party != "dept":
            raise ItemOperatorForbidden("部门经理只能修改本部门负责的条目")
        job_department = conn.execute(
            "SELECT j.department FROM application a JOIN job j ON j.id = a.job_id "
            "WHERE a.id = ?",
            (application_id,),
        ).fetchone()
        if job_department is None or job_department[0] != operator_department:
            raise ItemOperatorForbidden("跨部门条目不可修改")
    else:
        raise ItemOperatorForbidden("仅 HR 或部门经理可修改条目")

    conn.execute(
        "UPDATE onboarding_item SET status=?, reason=?, acted_by=?, acted_at=datetime('now') "
        "WHERE id=?",
        (to_status, reason, operator_username, item_id),
    )
    conn.execute(
        "INSERT INTO onboarding_item_history "
        "(id, item_id, from_status, to_status, reason, acted_by, at) "
        "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
        (str(uuid.uuid4()), item_id, from_status, to_status, reason, operator_username),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_onboarding_nodes.py -q`
Expected: 全绿（Task 2 + Task 3 用例）。

---

### Task 4: 进度与逾期纯函数 `progress()`（tasks 2.3）

**Files:**
- New: `app/agents/onboarding_progress.py`
- Test: `tests/test_onboarding_progress.py`（新建）

**Interfaces:**
- Produces: `progress(items, start_date, today) -> dict`，返回 `{"progress_percent": int, "overdue_item_ids": list[str]}`；⛔ 无 storage 写入、无消息发送

- [ ] **Step 1: Write the failing test**

新建 `tests/test_onboarding_progress.py`：

```python
"""onboarding-flow U2 进度与逾期纯函数（tasks 2.3）：无 storage 写入、无消息发送。"""
from pathlib import Path

from app.agents.onboarding_progress import progress


def test_progress_percent_counts_required_only():
    items = [
        {"id": "i1", "status": "done", "due_offset_days": -3, "required": True},
        {"id": "i2", "status": "done", "due_offset_days": -3, "required": True},
        {"id": "i3", "status": "pending", "due_offset_days": -3, "required": True},
        {"id": "i4", "status": "done", "due_offset_days": 0, "required": False},
    ]
    result = progress(items, start_date="2026-10-20", today="2026-10-19")
    assert result["progress_percent"] == 67


def test_progress_zero_required_yields_zero():
    items = [{"id": "i1", "status": "done", "due_offset_days": 0, "required": False}]
    result = progress(items, start_date="2026-10-20", today="2026-10-19")
    assert result["progress_percent"] == 0


def test_overdue_marks_only_pending_past_due():
    items = [
        {"id": "overdue-pending", "status": "pending", "due_offset_days": -3, "required": True},
        {"id": "not-yet-due", "status": "pending", "due_offset_days": 5, "required": True},
        {"id": "done-past-due", "status": "done", "due_offset_days": -10, "required": True},
    ]
    result = progress(items, start_date="2026-10-20", today="2026-10-20")
    assert result["overdue_item_ids"] == ["overdue-pending"]


def test_overdue_negative_offset_before_start_date():
    """入职日前 3 天应完成、入职日前 1 天仍待办 ⇒ 逾期（spec Scenario）。"""
    items = [{"id": "contract", "status": "pending", "due_offset_days": -3, "required": True}]
    result = progress(items, start_date="2026-10-20", today="2026-10-19")
    assert result["overdue_item_ids"] == ["contract"]


def test_progress_module_has_no_storage_or_outbound_side_effects():
    src = Path("app/agents/onboarding_progress.py").read_text(encoding="utf-8")
    for forbidden in (
        "conn.execute", "sqlite3", "deliver_", "effect_", "requests.", "httpx", "smtplib",
        "app.storage", "app.outbound",
    ):
        assert forbidden not in src, f"progress 模块不应出现副作用调用: {forbidden}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_onboarding_progress.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.agents.onboarding_progress'`。

- [ ] **Step 3: Write minimal implementation**

新建 `app/agents/onboarding_progress.py`：

```python
"""onboarding-flow U2 进度与逾期纯函数（tasks 2.3）。

⛔ 无 storage 写入、无消息发送——本模块只 import 标准库，不做任何 IO
（tests/test_onboarding_progress.py 用源码 grep 反证）。

进度口径：必需条目里已完成/已豁免数 ÷ 必需条目数（非必需条目不进入分子，
避免"全勾完却 >100%"）。逾期口径：status='pending' 且 start_date + due_offset_days
< today（due_offset_days 可为负，负 = 入职日前）。
"""
from __future__ import annotations

from datetime import date, timedelta


def _iso_date(value) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def progress(items, start_date, today) -> dict:
    """计算进度百分比与逾期条目 id 列表。纯函数，无副作用。

    items 元素为 dict：{'id': str, 'status': 'pending'|'done'|'waived',
    'due_offset_days': int, 'required': bool}。
    返回 {'progress_percent': int, 'overdue_item_ids': list[str]}。
    """
    start = _iso_date(start_date)
    today_d = _iso_date(today)
    required = [it for it in items if it.get("required")]
    completed = [it for it in required if it.get("status") in ("done", "waived")]
    progress_percent = 0 if not required else round(100 * len(completed) / len(required))

    overdue_item_ids: list[str] = []
    for it in items:
        if it.get("status") != "pending":
            continue
        due = start + timedelta(days=int(it.get("due_offset_days", 0)))
        if due < today_d:
            overdue_item_ids.append(it["id"])
    return {"progress_percent": progress_percent, "overdue_item_ids": overdue_item_ids}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_onboarding_progress.py -q`
Expected: 全绿（progress 五个用例）。

---

### Task 5: HR 清单页（HTML + JSON 端点，tasks 2.4）

**Files:**
- Modify: `app/web/server.py`（请求模型 + 实例化/详情/条目更新端点 + 页面路由）
- New: `app/web/static/onboarding_checklist.html`
- Test: `tests/test_onboarding_pages.py`（新建，本 Task 先建文件头与清单页用例）

**Interfaces:**
- Produces: `GET /applications/{application_id}/onboarding`（HTML）、`GET /api/applications/{application_id}/onboarding`（HR 详情 JSON）、`POST /api/applications/{application_id}/onboarding/instantiate`（HR 生成）、`POST /api/onboarding/items/{item_id}`（HR 或部门经理，勾选/豁免/撤回，共用 Task 7 经理页）

- [ ] **Step 1: Write the failing test**

新建 `tests/test_onboarding_pages.py`：

```python
"""onboarding-flow U2 页面与 JSON 端点（tasks 2.4/2.5/2.6）。"""
from pathlib import Path

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account

STATIC = Path("app/web/static")


def _login(client, conn, username, role, department=None):
    account_id = upsert_account(conn, username=username, password="s3cret!")
    conn.execute("UPDATE hr_account SET role=?, department=? WHERE id=?", (role, department, account_id))
    conn.commit()
    client.cookies.set("hr_session", create_session(conn, hr_account_id=account_id))
    return account_id


def _seed_hired(conn, application_id="app-1", department="研发部", start_date="2026-10-20"):
    conn.execute(
        "INSERT INTO job (id, title, department, status) VALUES ('j1', '嵌入式工程师', ?, 'approved')",
        (department,),
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('res-1', 'j1', 'synthetic', 'a.docx', 'sha-1', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status) "
        "VALUES (?, 'cand-1', 'j1', 'res-1', 'hired', 'hired')",
        (application_id,),
    )
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, report_to, status, approval_round, created_by) "
        "VALUES (?, ?, 'j1', ?, ?, 'manager-1', 'accepted', 1, 'hr')",
        (f"offer-{application_id}", application_id, department, start_date),
    )
    conn.commit()


def _instantiate(client, application_id="app-1", request_id="r1"):
    return client.post(
        f"/api/applications/{application_id}/onboarding/instantiate",
        json={"request_id": request_id},
    )


# ── Task 5：HR 清单页 ──────────────────────────────────────────────────


def test_checklist_page_html_has_no_upload_control():
    html = (STATIC / "onboarding_checklist.html").read_text(encoding="utf-8")
    assert 'type="file"' not in html
    assert "multipart/form-data" not in html
    assert 'enctype="multipart/form-data"' not in html


def test_checklist_page_extracts_application_id_from_middle_segment():
    """2026-10-11 修正：⛔ 不能取 URL 末段（那是字面量 "onboarding"）。"""
    html = (STATIC / "onboarding_checklist.html").read_text(encoding="utf-8")
    assert "match(/\\/applications\\/([^/]+)\\/onboarding\\/?$/)" in html
    assert 'split("/").filter(Boolean).pop()' not in html


def test_checklist_page_is_served(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn)
    _login(client, conn, "hr", "hr")
    assert client.get("/applications/app-1/onboarding").status_code == 200


def test_instantiate_endpoint_rejects_multipart(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn)
    _login(client, conn, "hr", "hr")
    resp = client.post(
        "/api/applications/app-1/onboarding/instantiate",
        data={"request_id": "r1"},
        files={"file": ("a.txt", b"x", "text/plain")},
    )
    assert resp.status_code in (415, 422)
    assert conn.execute("SELECT COUNT(*) FROM onboarding_checklist WHERE application_id='app-1'").fetchone()[0] == 0


def test_instantiate_requires_hr(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn)
    assert _instantiate(client).status_code == 401
    _login(client, conn, "iv", "interviewer")
    assert _instantiate(client).status_code == 403
    _login(client, conn, "mgr", "dept_manager", department="研发部")
    assert _instantiate(client).status_code == 403


def test_hr_checklist_detail_endpoint(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn)
    _login(client, conn, "hr", "hr")
    resp = _instantiate(client)
    assert resp.status_code == 200
    detail = client.get("/api/applications/app-1/onboarding")
    assert detail.status_code == 200
    body = detail.json()
    assert body["application_id"] == "app-1"
    assert body["progress_percent"] == 0
    assert len(body["items"]) == 6
    assert all(it["status"] == "pending" for it in body["items"])


def test_hr_checklist_detail_requires_hr(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn)
    _login(client, conn, "hr", "hr")
    _instantiate(client)
    _login(client, conn, "iv", "interviewer")
    assert client.get("/api/applications/app-1/onboarding").status_code == 403


def test_hr_item_update_endpoint(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn)
    _login(client, conn, "hr", "hr")
    _instantiate(client)
    item_id = conn.execute(
        "SELECT id FROM onboarding_item WHERE checklist_id="
        "(SELECT id FROM onboarding_checklist WHERE application_id='app-1') AND owner_party='hr' LIMIT 1"
    ).fetchone()[0]
    resp = client.post(
        f"/api/onboarding/items/{item_id}",
        json={"to_status": "done", "reason": None, "request_id": "r-done"},
    )
    assert resp.status_code == 200
    assert conn.execute("SELECT status FROM onboarding_item WHERE id=?", (item_id,)).fetchone()[0] == "done"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_onboarding_pages.py -q -k "checklist or instantiate or item_update"`
Expected: FAIL —— `FileNotFoundError`（`onboarding_checklist.html` 不存在）或端点 404。

- [ ] **Step 3: Write minimal implementation**

① `app/web/server.py` 模块级请求模型区（`OnboardingTemplateUpdateRequest` 之后）追加：

```python
class OnboardingInstantiateRequest(BaseModel):
    request_id: str


class OnboardingItemUpdateRequest(BaseModel):
    to_status: str
    reason: str | None = None
    request_id: str
```

② `app/web/server.py` 顶部 import 区补 `date`（本文件当前无 datetime 类 import；在 `from pathlib import Path` 之前加 `from datetime import date`），并在 `from app.graph.invite_nodes import` 块之后追加：

```python
from app.agents.onboarding_progress import progress
from app.graph.onboarding_nodes import (
    ChecklistInstantiateRejected,
    ChecklistTemplateMissing,
    ItemOperatorForbidden,
    ItemUpdateRejected,
    effect_instantiate_checklist,
    effect_update_item,
)
```

③ 在 `create_app` 内、模板两条路由之后，追加辅助函数与端点（`json`/`uuid`/`HTTPException`/`Request`/`BaseModel` 已在作用域内）：

```python
    def _require_hr_or_dept_manager(request: Request) -> tuple[str, str, str | None]:
        """登录态 + 角色校验，返回 (username, role, department)。非登录 401、角色不符 403。"""
        auth = getattr(request.state, "auth", None)
        if not getattr(auth, "authenticated", False):
            raise HTTPException(status_code=401, detail="未登录")
        username = getattr(auth, "user_id", None)
        row = conn.execute(
            "SELECT role, department FROM hr_account WHERE username = ?", (username,)
        ).fetchone()
        if row is None or row[0] not in ("hr", "dept_manager"):
            raise HTTPException(status_code=403, detail="无权限")
        return username, row[0], row[1]

    def _checklist_payload(application_id: str) -> dict | None:
        cl = conn.execute(
            "SELECT id, template_version, start_date, status, closed_reason, created_at "
            "FROM onboarding_checklist WHERE application_id = ?",
            (application_id,),
        ).fetchone()
        if cl is None:
            return None
        rows = conn.execute(
            "SELECT id, name, owner_party, due_offset_days, required, status, reason, acted_by, acted_at "
            "FROM onboarding_item WHERE checklist_id = ? ORDER BY rowid",
            (cl[0],),
        ).fetchall()
        items = [
            {
                "item_id": r[0], "name": r[1], "owner_party": r[2],
                "due_offset_days": r[3], "required": bool(r[4]), "status": r[5],
                "reason": r[6], "acted_by": r[7], "acted_at": r[8],
            }
            for r in rows
        ]
        prog = progress(
            [
                {"id": it["item_id"], "status": it["status"],
                 "due_offset_days": it["due_offset_days"], "required": it["required"]}
                for it in items
            ],
            start_date=cl[2],
            today=date.today().isoformat(),
        )
        overdue = set(prog["overdue_item_ids"])
        for it in items:
            it["overdue"] = it["item_id"] in overdue
        return {
            "application_id": application_id,
            "template_version": cl[1],
            "start_date": cl[2],
            "status": cl[3],
            "closed_reason": cl[4],
            "progress_percent": prog["progress_percent"],
            "overdue_count": len(overdue),
            "items": items,
        }

    @router.get("/api/applications/{application_id}/onboarding")
    def onboarding_checklist_detail(application_id: str, request: Request):
        _require_role(request, "hr")
        payload = _checklist_payload(application_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="该投递尚无入职清单")
        return payload

    @router.post("/api/applications/{application_id}/onboarding/instantiate")
    def onboarding_instantiate(
        application_id: str, req: OnboardingInstantiateRequest, request: Request
    ):
        username = _require_role(request, "hr")
        try:
            effect_instantiate_checklist(
                conn, thread_id=application_id,
                business_key="instantiate", created_by=username,
            )
        except ChecklistInstantiateRejected as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ChecklistTemplateMissing as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        payload = _checklist_payload(application_id)
        if payload is None:
            raise HTTPException(status_code=500, detail="清单缺失")
        return payload

    @router.post("/api/onboarding/items/{item_id}")
    def onboarding_item_update(
        item_id: str, req: OnboardingItemUpdateRequest, request: Request
    ):
        username, _, _ = _require_hr_or_dept_manager(request)
        try:
            effect_update_item(
                conn, thread_id=item_id,
                business_key=f"{req.to_status}:{req.request_id}",
                to_status=req.to_status, reason=req.reason, operator_username=username,
            )
        except ItemUpdateRejected as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ItemOperatorForbidden as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        row = conn.execute(
            "SELECT id, status, reason, acted_by, acted_at FROM onboarding_item WHERE id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="条目不存在")
        return {
            "item_id": row[0], "status": row[1], "reason": row[2],
            "acted_by": row[3], "acted_at": row[4],
        }

    @router.get("/applications/{application_id}/onboarding")
    def onboarding_checklist_page(application_id: str):
        return _render_static_page("onboarding_checklist.html", root_path)
```

④ 新建 `app/web/static/onboarding_checklist.html`：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <!--BASE_HREF-->
  <link rel="stylesheet" href="static/app.css?v=20261010">
  <title>入职清单 · 卓品智能招聘助手</title>
</head>
<body>
  <header class="topbar">
    <div class="topbar-inner">
      <span class="brand">卓品智能招聘助手</span>
      <span class="brand-sub">HR 工作台 · 入职清单</span>
    </div>
  </header>
  <main class="page">
    <h1 class="page-title">入职清单</h1>
    <div class="card" id="checklist-card">
      <p id="progress-line"></p>
      <p><button id="instantiate-btn">生成入职清单</button></p>
      <table class="data-table">
        <thead>
          <tr>
            <th>条目</th><th>负责方</th><th>期限（相对入职日）</th><th>状态</th><th>原因</th><th>操作</th>
          </tr>
        </thead>
        <tbody id="item-body"></tbody>
      </table>
      <p id="empty-hint" class="empty-hint" style="display:none">该投递尚未生成入职清单。</p>
    </div>
  </main>
  <script>
    // 2026-10-11 修正（1001G，Spec review 实测 blocker）：⛔ 不能用
    // split("/").pop()——本页 URL 末段是字面量 "onboarding"，取到的是它而不是
    // application_id（清单页会永远去拉一个不存在的投递）。用与 letters.html
    // 同款正则从路径中段取（部署约束 1：不硬编码前缀，正则不含前导 "/"）。
    const applicationId =
      location.pathname.match(/\/applications\/([^/]+)\/onboarding\/?$/)[1];
    const detailUrl = `api/applications/${applicationId}/onboarding`;
    const instantiateUrl = `api/applications/${applicationId}/onboarding/instantiate`;
    const itemUrl = "api/onboarding/items";

    function render(payload) {
      document.getElementById("empty-hint").style.display = payload ? "none" : "block";
      if (!payload) return;
      document.getElementById("progress-line").textContent =
        `进度：${payload.progress_percent}% · 逾期 ${payload.overdue_count} 条`;
      const body = document.getElementById("item-body");
      body.innerHTML = "";
      for (const it of payload.items) {
        const tr = document.createElement("tr");
        const overdue = it.overdue ? " ⚠️逾期" : "";
        const actions = ["pending", "done", "waived"].map((target) => {
          if (target === it.status) return "";
          return `<button data-item="${it.item_id}" data-target="${target}">` +
            (target === "done" ? "完成" : target === "waived" ? "豁免" : "撤回") +
            `</button>`;
        }).join("");
        tr.innerHTML = `<td>${it.name}${overdue}</td><td>${it.owner_party}</td>` +
          `<td>${it.due_offset_days}</td><td>${it.status}</td>` +
          `<td>${it.reason || ""}</td><td>${actions}</td>`;
        body.appendChild(tr);
      }
    }

    async function load() {
      const resp = await fetch(detailUrl);
      if (resp.status === 404) { render(null); return; }
      if (!resp.ok) throw new Error("加载失败");
      render(await resp.json());
    }

    async function updateItem(itemId, toStatus) {
      const reason = toStatus === "waived" ? prompt("豁免原因（必填）：") : "";
      if (toStatus === "waived" && !reason) return;
      const resp = await fetch(`${itemUrl}/${itemId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ to_status: toStatus, reason, request_id: crypto.randomUUID() }),
      });
      if (!resp.ok) { alert("操作失败"); return; }
      load();
    }

    document.getElementById("instantiate-btn").addEventListener("click", async () => {
      const resp = await fetch(instantiateUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ request_id: crypto.randomUUID() }),
      });
      if (!resp.ok) { alert("生成失败"); return; }
      load();
    });
    document.getElementById("item-body").addEventListener("click", (ev) => {
      const btn = ev.target.closest("button[data-item]");
      if (btn) updateItem(btn.dataset.item, btn.dataset.target);
    });
    load();
  </script>
</body>
</html>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_onboarding_pages.py -q -k "checklist or instantiate or item_update"`
Expected: 全绿（清单页七个用例）。

---

### Task 6: HR 总览页（HTML + JSON 端点，tasks 2.5）

**Files:**
- Modify: `app/web/server.py`（总览 JSON 端点 + 页面路由）
- New: `app/web/static/onboarding_overview.html`
- Test: `tests/test_onboarding_pages.py`（追加）

**Interfaces:**
- Produces: `GET /onboarding`（HTML）、`GET /api/onboarding`（HR，全部 `open` 清单按入职日升序）

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_onboarding_pages.py`：

```python
# ── Task 6：HR 总览页 ──────────────────────────────────────────────────


def _seed_hired_x(conn, application_id, job_id, candidate_name, department, start_date):
    conn.execute(
        "INSERT INTO job (id, title, department, status) VALUES (?, '工程师', ?, 'approved')",
        (job_id, department),
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES (?, ?)", (f"cand-{application_id}", candidate_name))
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES (?, ?, 'synthetic', 'a.docx', ?, 'hr-1')",
        (f"res-{application_id}", job_id, f"sha-{application_id}"),
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status) "
        "VALUES (?, ?, ?, ?, 'hired', 'hired')",
        (application_id, f"cand-{application_id}", job_id, f"res-{application_id}"),
    )
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, report_to, status, approval_round, created_by) "
        "VALUES (?, ?, ?, ?, ?, 'manager-1', 'accepted', 1, 'hr')",
        (f"offer-{application_id}", application_id, job_id, department, start_date),
    )
    conn.commit()


def test_hr_overview_lists_open_checklists_sorted_by_start_date(make_test_client):
    client, conn = make_test_client()
    _seed_hired_x(conn, "app-late", "j-late", "张三", "研发部", "2026-10-20")
    _seed_hired_x(conn, "app-early", "j-early", "李四", "研发部", "2026-10-15")
    _login(client, conn, "hr", "hr")
    _instantiate(client, "app-late", request_id="r-late")
    _instantiate(client, "app-early", request_id="r-early")
    resp = client.get("/api/onboarding")
    assert resp.status_code == 200
    checklists = resp.json()["checklists"]
    assert [c["application_id"] for c in checklists] == ["app-early", "app-late"]
    assert checklists[0]["candidate_name"] == "李四"
    assert checklists[0]["progress_percent"] == 0
    assert checklists[0]["overdue_count"] == 0


def test_hr_overview_requires_hr(make_test_client):
    client, conn = make_test_client()
    assert client.get("/api/onboarding").status_code == 401
    _login(client, conn, "iv", "interviewer")
    assert client.get("/api/onboarding").status_code == 403
    _login(client, conn, "mgr", "dept_manager", department="研发部")
    assert client.get("/api/onboarding").status_code == 403


def test_overview_page_is_served(make_test_client):
    client, conn = make_test_client()
    _login(client, conn, "hr", "hr")
    assert client.get("/onboarding").status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_onboarding_pages.py -q -k "overview"`
Expected: FAIL —— `FileNotFoundError`（`onboarding_overview.html` 不存在）或 `/api/onboarding` 404。

- [ ] **Step 3: Write minimal implementation**

① `app/web/server.py` 在 Task 5 追加的辅助函数之后继续追加（`_checklist_payload` 已存在）：

```python
    def _checklist_summary(application_id: str) -> dict:
        cl = conn.execute(
            "SELECT id, start_date FROM onboarding_checklist WHERE application_id = ?",
            (application_id,),
        ).fetchone()
        if cl is None:
            return {"progress_percent": 0, "overdue_count": 0}
        rows = conn.execute(
            "SELECT id, status, due_offset_days, required FROM onboarding_item "
            "WHERE checklist_id = ?",
            (cl[0],),
        ).fetchall()
        prog = progress(
            [
                {"id": r[0], "status": r[1], "due_offset_days": r[2], "required": bool(r[3])}
                for r in rows
            ],
            start_date=cl[1],
            today=date.today().isoformat(),
        )
        return {"progress_percent": prog["progress_percent"], "overdue_count": len(prog["overdue_item_ids"])}

    def _onboarding_overview_rows(department: str | None = None):
        sql = (
            "SELECT c.name, a.id, j.department, cl.start_date "
            "FROM onboarding_checklist cl "
            "JOIN application a ON a.id = cl.application_id "
            "JOIN candidate c ON c.id = a.candidate_id "
            "JOIN job j ON j.id = a.job_id "
            "WHERE cl.status = 'open'"
        )
        params: list = []
        if department is not None:
            sql += " AND j.department = ?"
            params.append(department)
        sql += " ORDER BY cl.start_date ASC, a.id ASC"
        return conn.execute(sql, params).fetchall()

    @router.get("/api/onboarding")
    def onboarding_overview(request: Request):
        _require_role(request, "hr")
        rows = _onboarding_overview_rows()
        checklists = []
        for r in rows:
            summary = _checklist_summary(r[1])
            checklists.append(
                {
                    "candidate_name": r[0],
                    "application_id": r[1],
                    "department": r[2],
                    "start_date": r[3],
                    "progress_percent": summary["progress_percent"],
                    "overdue_count": summary["overdue_count"],
                }
            )
        return {"checklists": checklists}

    @router.get("/onboarding")
    def onboarding_overview_page():
        return _render_static_page("onboarding_overview.html", root_path)
```

② 新建 `app/web/static/onboarding_overview.html`：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <!--BASE_HREF-->
  <link rel="stylesheet" href="static/app.css?v=20261010">
  <title>入职总览 · 卓品智能招聘助手</title>
</head>
<body>
  <header class="topbar">
    <div class="topbar-inner">
      <span class="brand">卓品智能招聘助手</span>
      <span class="brand-sub">HR 工作台 · 入职总览</span>
    </div>
  </header>
  <main class="page">
    <h1 class="page-title">入职总览</h1>
    <div class="card">
      <table class="data-table">
        <thead>
          <tr><th>候选人</th><th>部门</th><th>入职日</th><th>进度</th><th>逾期条目</th><th>操作</th></tr>
        </thead>
        <tbody id="overview-body"></tbody>
      </table>
      <p id="empty-hint" class="empty-hint" style="display:none">暂无进行中的入职清单。</p>
    </div>
  </main>
  <script>
    async function load() {
      const resp = await fetch("api/onboarding");
      if (!resp.ok) throw new Error("加载失败");
      const data = await resp.json();
      const body = document.getElementById("overview-body");
      body.innerHTML = "";
      document.getElementById("empty-hint").style.display = data.checklists.length ? "none" : "block";
      for (const c of data.checklists) {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${c.candidate_name}</td><td>${c.department || ""}</td>` +
          `<td>${c.start_date}</td><td>${c.progress_percent}%</td><td>${c.overdue_count}</td>` +
          `<td><a href="applications/${c.application_id}/onboarding">查看清单</a></td>`;
        body.appendChild(tr);
      }
    }
    load();
  </script>
</body>
</html>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_onboarding_pages.py -q -k "overview"`
Expected: 全绿（总览三个用例）。

---

### Task 7: 部门经理只读页（HTML + JSON 端点，tasks 2.6）

**Files:**
- Modify: `app/web/server.py`（部门列表/详情 JSON 端点 + 页面路由）
- New: `app/web/static/onboarding_department.html`
- Test: `tests/test_onboarding_pages.py`（追加）

**Interfaces:**
- Produces: `GET /onboarding/department`（HTML）、`GET /api/onboarding/department`（经理，本部门列表，先写访问留痕再返回）、`GET /api/onboarding/department/{application_id}`（经理，本部门详情，跨部门 403，先写访问留痕再返回）；本部门 `owner_party='dept'` 条目经 `POST /api/onboarding/items/{item_id}` 勾选

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_onboarding_pages.py`：

```python
# ── Task 7：部门经理只读页 ─────────────────────────────────────────────


def _manager_item(conn, application_id, owner_party):
    row = conn.execute(
        "SELECT i.id FROM onboarding_item i "
        "JOIN onboarding_checklist c ON c.id = i.checklist_id "
        "WHERE c.application_id=? AND i.owner_party=? LIMIT 1",
        (application_id, owner_party),
    ).fetchone()
    return row[0]


def test_department_list_filters_by_department(make_test_client):
    client, conn = make_test_client()
    _seed_hired_x(conn, "app-rd", "j-rd", "张三", "研发部", "2026-10-20")
    _seed_hired_x(conn, "app-proc", "j-proc", "李四", "采购部", "2026-10-21")
    _login(client, conn, "hr", "hr")
    _instantiate(client, "app-rd", request_id="r-rd")
    _instantiate(client, "app-proc", request_id="r-proc")
    _login(client, conn, "mgr-rd", "dept_manager", department="研发部")
    resp = client.get("/api/onboarding/department")
    assert resp.status_code == 200
    apps = [c["application_id"] for c in resp.json()["checklists"]]
    assert apps == ["app-rd"]


def test_department_detail_cross_department_403(make_test_client):
    client, conn = make_test_client()
    _seed_hired_x(conn, "app-rd", "j-rd", "张三", "研发部", "2026-10-20")
    _login(client, conn, "hr", "hr")
    _instantiate(client, "app-rd", request_id="r-rd")
    _login(client, conn, "mgr-proc", "dept_manager", department="采购部")
    resp = client.get("/api/onboarding/department/app-rd")
    assert resp.status_code == 403


def test_department_detail_writes_access_log(make_test_client):
    client, conn = make_test_client()
    _seed_hired_x(conn, "app-rd", "j-rd", "张三", "研发部", "2026-10-20")
    _login(client, conn, "hr", "hr")
    _instantiate(client, "app-rd", request_id="r-rd")
    _login(client, conn, "mgr-rd", "dept_manager", department="研发部")
    resp = client.get("/api/onboarding/department/app-rd")
    assert resp.status_code == 200
    assert conn.execute(
        "SELECT COUNT(*) FROM onboarding_access_log WHERE accessor='mgr-rd' AND application_id='app-rd'"
    ).fetchone()[0] == 1


def test_department_detail_log_failure_returns_no_content(make_test_client):
    client, conn = make_test_client()
    _seed_hired_x(conn, "app-rd", "j-rd", "张三", "研发部", "2026-10-20")
    _login(client, conn, "hr", "hr")
    _instantiate(client, "app-rd", request_id="r-rd")
    _login(client, conn, "mgr-rd", "dept_manager", department="研发部")
    conn.execute("DROP TABLE onboarding_access_log")
    conn.commit()
    resp = client.get("/api/onboarding/department/app-rd")
    assert resp.status_code == 503
    assert "张三" not in resp.text


def test_department_page_and_json_have_no_recruiting_data(make_test_client):
    client, conn = make_test_client()
    _seed_hired_x(conn, "app-rd", "j-rd", "张三", "研发部", "2026-10-20")
    _login(client, conn, "hr", "hr")
    _instantiate(client, "app-rd", request_id="r-rd")
    _login(client, conn, "mgr-rd", "dept_manager", department="研发部")
    page = client.get("/onboarding/department")
    html = page.text
    for forbidden in ("评分", "排名", "简历原文", "联系方式", "复核", "总分"):
        assert forbidden not in html
    detail = client.get("/api/onboarding/department/app-rd").json()
    for key in ("score", "rank", "resume", "contact", "phone", "email"):
        assert key not in detail
        for it in detail["items"]:
            assert key not in it


def test_department_requires_manager_role(make_test_client):
    client, conn = make_test_client()
    assert client.get("/api/onboarding/department").status_code == 401
    _login(client, conn, "iv", "interviewer")
    assert client.get("/api/onboarding/department").status_code == 403
    _login(client, conn, "hr", "hr")
    assert client.get("/api/onboarding/department").status_code == 403


def test_manager_can_toggle_own_dept_item(make_test_client):
    client, conn = make_test_client()
    _seed_hired_x(conn, "app-rd", "j-rd", "张三", "研发部", "2026-10-20")
    _login(client, conn, "hr", "hr")
    _instantiate(client, "app-rd", request_id="r-rd")
    _login(client, conn, "mgr-rd", "dept_manager", department="研发部")
    dept_item = _manager_item(conn, "app-rd", "dept")
    resp = client.post(
        f"/api/onboarding/items/{dept_item}",
        json={"to_status": "done", "reason": None, "request_id": "r-mgr-done"},
    )
    assert resp.status_code == 200
    hr_item = _manager_item(conn, "app-rd", "hr")
    resp = client.post(
        f"/api/onboarding/items/{hr_item}",
        json={"to_status": "done", "reason": None, "request_id": "r-mgr-bad"},
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_onboarding_pages.py -q -k "department or manager"`
Expected: FAIL —— `FileNotFoundError`（`onboarding_department.html` 不存在）或 `/api/onboarding/department` 404。

- [ ] **Step 3: Write minimal implementation**

① `app/web/server.py` 在 Task 6 追加的总览路由之后继续追加：

```python
    def _record_department_access(username: str, application_ids) -> None:
        """写访问留痕；任一条失败抛异常并回滚（调用方转 503，不返回内容）。"""
        try:
            for aid in application_ids:
                conn.execute(
                    "INSERT INTO onboarding_access_log (id, accessor, application_id) "
                    "VALUES (?, ?, ?)",
                    (str(uuid.uuid4()), username, aid),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    @router.get("/api/onboarding/department")
    def onboarding_department_list(request: Request):
        username, role, department = _require_hr_or_dept_manager(request)
        if role != "dept_manager":
            raise HTTPException(status_code=403, detail="仅部门经理可访问部门进度页")
        if not department:
            raise HTTPException(status_code=403, detail="账号未分配部门")
        rows = _onboarding_overview_rows(department=department)
        try:
            _record_department_access(username, [r[1] for r in rows])
        except Exception:
            raise HTTPException(status_code=503, detail="访问留痕失败") from None
        checklists = []
        for r in rows:
            summary = _checklist_summary(r[1])
            checklists.append(
                {
                    "candidate_name": r[0],
                    "application_id": r[1],
                    "department": r[2],
                    "start_date": r[3],
                    "progress_percent": summary["progress_percent"],
                    "overdue_count": summary["overdue_count"],
                }
            )
        return {"checklists": checklists}

    @router.get("/api/onboarding/department/{application_id}")
    def onboarding_department_detail(application_id: str, request: Request):
        username, role, department = _require_hr_or_dept_manager(request)
        if role != "dept_manager":
            raise HTTPException(status_code=403, detail="仅部门经理可访问部门进度页")
        if not department:
            raise HTTPException(status_code=403, detail="账号未分配部门")
        row = conn.execute(
            "SELECT j.department FROM application a JOIN job j ON j.id = a.job_id "
            "WHERE a.id = ?",
            (application_id,),
        ).fetchone()
        if row is None or row[0] != department:
            raise HTTPException(status_code=403, detail="跨部门查看被拒")
        try:
            _record_department_access(username, [application_id])
        except Exception:
            raise HTTPException(status_code=503, detail="访问留痕失败") from None
        payload = _checklist_payload(application_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="该投递尚无入职清单")
        cand = conn.execute(
            "SELECT c.name FROM application a JOIN candidate c ON c.id = a.candidate_id "
            "WHERE a.id = ?",
            (application_id,),
        ).fetchone()
        payload["candidate_name"] = cand[0] if cand else ""
        payload["department"] = row[0]
        return payload

    @router.get("/onboarding/department")
    def onboarding_department_page():
        return _render_static_page("onboarding_department.html", root_path)
```

② 新建 `app/web/static/onboarding_department.html`：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <!--BASE_HREF-->
  <link rel="stylesheet" href="static/app.css?v=20261010">
  <title>部门入职进度 · 卓品智能招聘助手</title>
</head>
<body>
  <header class="topbar">
    <div class="topbar-inner">
      <span class="brand">卓品智能招聘助手</span>
      <span class="brand-sub">用人部门 · 入职进度（只读）</span>
    </div>
  </header>
  <main class="page">
    <h1 class="page-title">本部门入职进度</h1>
    <div class="card" id="list-card">
      <table class="data-table">
        <thead>
          <tr><th>待入职者</th><th>入职日</th><th>进度</th><th>逾期条目</th><th>操作</th></tr>
        </thead>
        <tbody id="list-body"></tbody>
      </table>
    </div>
    <div class="card" id="detail-card" style="display:none">
      <p id="detail-head"></p>
      <table class="data-table">
        <thead>
          <tr><th>条目</th><th>负责方</th><th>状态</th><th>原因</th><th>操作</th></tr>
        </thead>
        <tbody id="detail-body"></tbody>
      </table>
    </div>
  </main>
  <script>
    async function loadList() {
      const resp = await fetch("api/onboarding/department");
      if (!resp.ok) { alert("加载失败（可能留痕失败）"); return; }
      const data = await resp.json();
      const body = document.getElementById("list-body");
      body.innerHTML = "";
      for (const c of data.checklists) {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${c.candidate_name}</td><td>${c.start_date}</td>` +
          `<td>${c.progress_percent}%</td><td>${c.overdue_count}</td>` +
          `<td><button data-app="${c.application_id}">查看</button></td>`;
        body.appendChild(tr);
      }
    }

    async function openDetail(applicationId) {
      const resp = await fetch(`api/onboarding/department/${applicationId}`);
      if (!resp.ok) { alert("无法查看（可能跨部门或留痕失败）"); return; }
      const payload = await resp.json();
      document.getElementById("detail-card").style.display = "block";
      document.getElementById("detail-head").textContent =
        `${payload.candidate_name} · 入职日 ${payload.start_date} · 进度 ${payload.progress_percent}%`;
      const body = document.getElementById("detail-body");
      body.innerHTML = "";
      for (const it of payload.items) {
        const overdue = it.overdue ? " ⚠️逾期" : "";
        const canEdit = it.owner_party === "dept" && it.status !== "done" && it.status !== "waived";
        const btn = canEdit
          ? `<button data-item="${it.item_id}">本部门完成</button>`
          : "";
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${it.name}${overdue}</td><td>${it.owner_party}</td>` +
          `<td>${it.status}</td><td>${it.reason || ""}</td><td>${btn}</td>`;
        body.appendChild(tr);
      }
    }

    async function updateItem(itemId) {
      const resp = await fetch(`api/onboarding/items/${itemId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ to_status: "done", reason: null, request_id: crypto.randomUUID() }),
      });
      if (!resp.ok) { alert("操作失败"); return; }
      location.reload();
    }

    document.getElementById("list-body").addEventListener("click", (ev) => {
      const btn = ev.target.closest("button[data-app]");
      if (btn) openDetail(btn.dataset.app);
    });
    document.getElementById("detail-body").addEventListener("click", (ev) => {
      const btn = ev.target.closest("button[data-item]");
      if (btn) updateItem(btn.dataset.item);
    });
    loadList();
  </script>
</body>
</html>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_onboarding_pages.py -q`
Expected: 全绿（Task 5/6/7 全部页面用例）。

---

### Task 8: U2 e2e（tasks 2.7）

**Files:**
- Test: `tests/test_onboarding_e2e.py`（新建）

**Interfaces:**
- Consumes: Task 2/3 节点 + Task 5/6/7 端点（`POST /api/applications/{id}/onboarding/instantiate`、`POST /api/onboarding/items/{item_id}`、`GET /api/onboarding/department/{application_id}`）

- [ ] **Step 1: Write the failing test**

新建 `tests/test_onboarding_e2e.py`（自包含夹具，不 import 其它测试模块）：

```python
"""onboarding-flow U2 e2e（tasks 2.7）：生成清单 → 勾选/豁免 → 经理只读 → 跨部门 403 → 逾期。"""
from datetime import date

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account


def _login(client, conn, username, role, department=None):
    account_id = upsert_account(conn, username=username, password="s3cret!")
    conn.execute("UPDATE hr_account SET role=?, department=? WHERE id=?", (role, department, account_id))
    conn.commit()
    client.cookies.set("hr_session", create_session(conn, hr_account_id=account_id))


def _seed_hired(conn, application_id, job_id, candidate_name, department, start_date):
    conn.execute(
        "INSERT INTO job (id, title, department, status) VALUES (?, '工程师', ?, 'approved')",
        (job_id, department),
    )
    conn.execute("INSERT INTO candidate (id, name) VALUES (?, ?)", (f"cand-{application_id}", candidate_name))
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES (?, ?, 'synthetic', 'a.docx', ?, 'hr-1')",
        (f"res-{application_id}", job_id, f"sha-{application_id}"),
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status) "
        "VALUES (?, ?, ?, ?, 'hired', 'hired')",
        (application_id, f"cand-{application_id}", job_id, f"res-{application_id}"),
    )
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, report_to, status, approval_round, created_by) "
        "VALUES (?, ?, ?, ?, ?, 'manager-1', 'accepted', 1, 'hr')",
        (f"offer-{application_id}", application_id, job_id, department, start_date),
    )
    conn.commit()


def test_u2_e2e(make_test_client):
    client, conn = make_test_client()
    start_date = date.today().isoformat()  # 入职日=今天 ⇒ 负 offset 的待办条目逾期
    _seed_hired(conn, "app-1", "j1", "张三", "研发部", start_date)

    _login(client, conn, "hr", "hr")
    assert client.post(
        "/api/applications/app-1/onboarding/instantiate", json={"request_id": "r-instantiate"}
    ).status_code == 200

    items = conn.execute(
        "SELECT i.id, i.owner_party FROM onboarding_item i "
        "JOIN onboarding_checklist c ON c.id = i.checklist_id "
        "WHERE c.application_id='app-1' ORDER BY i.rowid"
    ).fetchall()
    hr_items = [iid for iid, party in items if party == "hr"]
    assert len(hr_items) >= 3
    for i, to_status, reason in [
        (hr_items[0], "done", None),
        (hr_items[1], "done", None),
        (hr_items[2], "waived", "体检报告无需提交"),
    ]:
        resp = client.post(
            f"/api/onboarding/items/{i}",
            json={"to_status": to_status, "reason": reason, "request_id": f"r-{i}"},
        )
        assert resp.status_code == 200

    _login(client, conn, "mgr-rd", "dept_manager", department="研发部")
    detail = client.get("/api/onboarding/department/app-1")
    assert detail.status_code == 200
    body = detail.json()
    assert body["progress_percent"] > 0
    assert body["candidate_name"] == "张三"

    _login(client, conn, "mgr-proc", "dept_manager", department="采购部")
    assert client.get("/api/onboarding/department/app-1").status_code == 403

    _login(client, conn, "mgr-rd", "dept_manager", department="研发部")
    body = client.get("/api/onboarding/department/app-1").json()
    assert any(it["overdue"] for it in body["items"])

    assert conn.execute("SELECT COUNT(*) FROM onboarding_item_history").fetchone()[0] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_onboarding_e2e.py -q`
Expected: FAIL —— 端点 404 或 `/api/onboarding/items/{id}` 不存在（Task 5–7 已交付则可能直接绿，属预期内）。

- [ ] **Step 3: Run full U2 test suite**

Run: `./venv/bin/python -m pytest tests/test_onboarding_nodes.py tests/test_onboarding_progress.py tests/test_onboarding_pages.py tests/test_onboarding_e2e.py -q`
Expected: 全绿（本单元全部新增用例）。

---

## 交付前自查

- [ ] **全量测试全绿**（基线 + 本单元新增，0 failed / 0 skipped）

```bash
./venv/bin/python -m pytest -q 2>&1 | tail -3
```

- [ ] **机器判据四条全绿**（在 worktree 根执行）

```bash
test -n "$(ls docs/superpowers/plans/2026-10-10-onboarding-flow-unit2*.md 2>/dev/null)"
grep -q '^### Task ' docs/superpowers/plans/2026-10-10-onboarding-flow-unit2*.md
grep -q 'Global Constraints' docs/superpowers/plans/2026-10-10-onboarding-flow-unit2*.md
grep -q 'effect_instantiate_checklist' docs/superpowers/plans/2026-10-10-onboarding-flow-unit2*.md
```

Expected: 四条命令各自退出码 0（本文件满足）。

- [ ] **Task 标题为三级 `### Task `，数量 = 8**

```bash
grep -c '^### Task ' docs/superpowers/plans/2026-10-10-onboarding-flow-unit2-checklist-and-progress.md
```

Expected: `8`（`scripts/codex_sdd_runner.py` 按三级标题抽取，⛔ 不能用二级）。

- [ ] **无 TBD/TODO/占位符**

```bash
grep -nE 'TBD|TODO|FIXME|适当处理错误|待补' docs/superpowers/plans/2026-10-10-onboarding-flow-unit2-checklist-and-progress.md || true
```

Expected: 无输出。

- [ ] **依赖文件 diff 为空**

```bash
git diff origin/main --stat -- requirements.txt pyproject.toml
```

Expected: 无输出。

---

## spec 覆盖对照

| spec Requirement | U2 的落点 | 完整兑现于 |
|---|---|---|
| `onboarding-checklist` · 清单模板由 HR 维护 | ⛔ 本单元无落点（存储层与接口 U1 已交付） | U1 |
| `onboarding-checklist` · 按投递实例化清单 | Task 2（`effect_instantiate_checklist`：前置 `hired`＋`offer.accepted`、岗位模板优先否则部门模板、同事务写清单+条目、重复调用返回既有） | **U2 完整兑现** |
| `onboarding-checklist` · 条目勾选与豁免留痕 | Task 3（`effect_update_item`：豁免必填原因、同事务写 item+history、操作人限 HR/对应部门经理） | **U2 完整兑现** |
| `onboarding-checklist` · 进度与逾期标记 | Task 4（`progress()` 纯函数）＋ Task 5/6/7（页面显示） | **U2 完整兑现** |
| `onboarding-checklist` · 清单动作幂等 | Task 2/3（`idempotent_effect` 幂等键；重跑不产生第二份/第二条留痕） | **U2 完整兑现** |
| `onboarding-visibility` · 用人部门经理只读本部门 | Task 7（`/api/onboarding/department*` 按 `hr_account.department` 过滤、跨部门 403；本部门 dept 条目可勾选走 Task 3 留痕） | **U2 完整兑现** |
| `onboarding-visibility` · 进度页不含招聘数据 | Task 7（JSON/HTML 反证无评分/排名/简历/联系方式/复核工作台链接）＋ Task 5（清单页无上传控件） | **U2 完整兑现** |
| `onboarding-visibility` · 查看写访问留痕 | Task 7（每次打开写 `onboarding_access_log`；留痕失败 503 不返回内容） | **U2 完整兑现** |

`specs/onboarding-completion`（终态）与 `specs/post-hire-data-disposition`（处置）的所有 Requirement：**本单元无落点**，归 U3/U4。

---

## 本计划相对 `tasks.md` / `design.md` 的偏离登记

| # | 偏离 | 位置 | 方向 | 理由 |
|---|---|---|---|---|
| 1 | 新增 `application.status` CHECK 放宽（含 `'hired'`，表重建） | Task 1 | 补磁盘缺口 | tasks.md/design 把 `{ongoing,hired,refused}` 迁移归 M2 U1 / offer-generation U5，但磁盘真身仍是 `('active','rejected','withdrawn')`；design 风险表明写「U2 用夹具直接置 `application.status=hired`」，不改 CHECK 夹具无从置位。U2 只加 `'hired'`（保守最小），`ongoing/refused` 仍归 offer-generation U5 |
| 2 | 进度分子取「必需条目中的 done/waived」 | Task 4 | 语义收敛 | spec 字面「已完成或豁免条目数 / 必需条目数」会把非必需条目计入分子（全勾完 >100%）；按"必需条目完成数 / 必需条目数"实现并写进测试 |
| 3 | 入职日读 `offer.start_date` | Task 2 | 对磁盘 | `offer.start_date` 是 offer-generation U1 已落盘的唯一入职日列；其 U5 `accepted(start_date)` 是否回写该列由其自身决定，U2 只读当前值 |
| 4 | 条目更新端点路径 `/api/onboarding/items/{item_id}` | Task 5 | 对齐代码库 | tasks 2.2/2.4 未给路径字面；全库 JSON 端点统一 `/api/*`，且条目→清单→投递可自证，无需在路径再带 application_id |
| 5 | 总览/部门页显示 `candidate_name`（姓名） | Task 6/7 | 最小必要 | 姓名不在「禁止展示」清单（禁的是简历/解析字段/评分/排名/面试记录/联系方式）；不显示姓名则列表无法区分待入职者 |

前 4 条属「不改变外部可观察行为」的技术方案范畴，由 `run-build` 的 final review 确认。

---

## 提取验证记录（`spec-to-plan` 第 6 步，2026-10-10 实测）

本 worktree **无 `./venv`**，且 opener 边界明写「⛔ 不改 `app/**` / `tests/**`」——泳道只产出 plan 文档，不能把计划里的代码落盘再跑全量 pytest。已做的等价静态验证（系统 Python 3，内存库，不触碰仓库文件）：

- **Task 1 表重建 SQL 验证**：用系统 `python3` 在内存库跑 `_rebuild_application_status_check` 的完整 SQL（老库三值 CHECK → `application_new` → INSERT SELECT → DROP → RENAME → 重建三索引），实测 `PRAGMA foreign_key_check` 为空、老行 `('active',)` 原样保留、`UPDATE status='hired'` 成功、`'bogus'` 被 CHECK 拒、三索引重建齐全。
- **Task 4 纯函数验证**：把 `progress()` 的实现体在内存里跑过四组输入——67% 进度、负 offset 逾期（`contract`）、零必需条目、非必需条目不进分子，结果与测试断言一致。
- **边界**：全量 pytest 与 spec 合规由 `run-build` 的 TDD + 两阶段 review 负责，本验证只证明两处最易错逻辑可执行且内部自洽。

---

## 完成判据（`tasks.md` 第 2 章 checkbox 在这些全部成立后才勾）

1. `application.status` 新库与老库均能写 `'hired'`，既有三值与其他行/列/索引不变（Task 1）
2. `effect_instantiate_checklist`：`ongoing` 或 `hired` 无 `accepted` Offer 被拒；生成一份清单、条目与模板一致；重跑返回既有、不产生第二份（Task 2）
3. `effect_update_item`：豁免无原因被拒、撤回生效、HR 无条件、部门经理仅本部门 `dept` 条目、跨部门/非本部门条目 403；重跑无第二条 history（Task 3）
4. `progress()` 纯函数：进度 = 必需条目完成数/必需条目数；逾期 = pending 且 `start_date+offset<today`；源码 grep 无 storage/outbound 副作用（Task 4）
5. HR 清单页：生成/勾选/豁免/撤回/进度/逾期；HTML 无上传控件、接口拒 multipart（Task 5）
6. HR 总览页：全部 `open` 清单按入职日升序、每行进度与逾期数（Task 6）
7. 部门经理只读页：按部门过滤、跨部门 403、interviewer/未登录 403、每次打开写 `onboarding_access_log`、留痕失败 503 不返回内容、无评分/排名/简历/联系方式/复核工作台链接、本部门 dept 条目可勾选（Task 7）
8. U2 e2e 全绿、`onboarding_item_history` 条数守恒（Task 8）

**合并后立刻解锁**：U3（终态与处置登记，`effect_complete_onboarding` / `effect_enqueue_disposition`）可出一份 `spec-to-plan` 并行开工。

**下一步**：用 `run-build` 执行本计划。⛔ 不要在本会话里开始实现。

---

## 留步登记

1. ⏸ 留步：**`application.status='hired'` 的生产写入来源（offer-generation U5 `effect_apply_offer_outcome`）尚未交付**。U2 已照做代码与单测（Task 1 把 CHECK 放宽到含 `'hired'`，测试夹具可直接置位；Task 2 前置校验 `hired`＋`offer.accepted`），但生产上「点回填 Offer → `application.status=hired` → 生成清单」的端到端链路要等 offer-generation U5 合入并交付后才真正贯通。⛔ 不因此判 U2 失败——design D7 明定 U2 前置 = U1，offer-generation U5 不阻塞 U2。

2. 需人定夺（不可代，已登记不默认生效）：无。本单元不触碰合规红线七条、候选人淘汰规则、候选人对外通道、真实简历处理范围、`.51` 发版、预算与外部采购——均不在 U2 范围内。
