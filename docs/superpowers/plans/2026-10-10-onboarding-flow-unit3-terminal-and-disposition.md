# 入职流程 · 交付单元 U3（终态与处置登记）Implementation Plan

**Goal:** 为 onboarding-flow 变更包的 **U3 交付单元（tasks.md 第 3 章「终态与处置登记」，tasks 3.1–3.6）** 产出实现计划。范围 = 磁盘状态域放宽（`application.status` 含 `'refused'`、`offer.status` 含 `'abandoned'`、`application_stage_history.action` 含 `'onboarded'/'abandoned'`）＋ 终态节点 `effect_complete_onboarding`（`hired` / `abandoned` 两分支）＋ 同事务的 `effect_enqueue_disposition` ＋ 终态不变式测试 ＋ 终态确认 UI ＋ `app/audit/assertions.py` 三条新断言与 CI 接入。**不写 `effect_execute_disposition`、不写脱敏规则、不接任何 LLM、不产生任何外发、不执行任何删除/脱敏**——处置执行是 U4 的活，且受留存策略签认闸。

**Architecture:** 两个 effect 节点落在既有 `app/graph/onboarding_nodes.py`（U2 已建，形态与 `app/graph/invite_nodes.py` 一致：Web 通道下 HTTP 端点直接调用普通 Python 函数，不建真实 LangGraph `interrupt()`）；幂等走既有 `app/storage/idempotency.py::idempotent_effect`（`effect_key = {thread_id}:{node_name}:{business_key}`，落 `effect_log` 与业务写同一事务）。终态的两个分支同属**一个**被装饰的函数（`business_key` 即结局 `hired`/`abandoned`），这样"一份投递只有一个终态"与"重跑不产生第二条终态事实"由同一把键与同一张事实表共同保证。**同事务登记**通过一个**不 commit**的纯写函数 `_enqueue_disposition_rows()` 实现——⛔ 不能在 `effect_complete_onboarding` 内部再调用另一个被 `@idempotent_effect` 装饰过的节点，那会在中途 commit、把"同事务"打散。终态确认 UI 是清单页（U2 Task 5 产出）上追加的自包含卡片 + 三个 HR 端点；审计三条断言加在 `app/audit/assertions.py`（⛔ 该模块只读、⛔ 不 import `app.config`，故"策略是否签认"由调用方以关键字参数传入）。

**Tech Stack:** Python（项目 `./venv`）· SQLite（标准库 `sqlite3`，WAL + `PRAGMA foreign_keys=ON`）· FastAPI · pytest · **不引入任何新依赖**（`requirements.txt` / `pyproject.toml` diff 必须为空）。

---

## 输入与交付单元划分

本计划为 **U3（tasks.md 第 3 章「终态与处置登记」，tasks 3.1–3.6）** 出计划。输入真源是 `openspec/changes/onboarding-flow/specs/**/spec.md` 与 `design.md`；`tasks.md` **只用于确认 U3 章节边界（3.1–3.6）**，不作为计划输入。

与本单元相关的 spec 能力文件（自行列出，两份，理由如下）：

| 能力文件 | 与本单元的关系 |
|---|---|
| `specs/onboarding-completion/spec.md` | **核心**。「确认已入职」「放弃入职」「终态动作幂等且唯一」「终态由人确认」四条 Requirement 的行为落点全在本单元（tasks 3.1/3.3/3.4/3.5） |
| `specs/post-hire-data-disposition/spec.md` | 「入职完成时登记处置事项」是 tasks 3.2（`effect_enqueue_disposition`：六类各一行、`policy_version=NULL`、`planned_action=pending`）的落点；「留存策略未签认时只登记不执行」在本单元的落点是 tasks 3.6 断言②（执行一侧——到期删除/脱敏、重跑、断言兼容——属 U4，本单元不碰） |

**说明**：`specs/onboarding-checklist/spec.md` 与 `specs/onboarding-visibility/spec.md` 的全部 Requirement 在**前置单元 U2** 已完整兑现（U2 计划「spec 覆盖对照」逐条标注），本单元无落点；本单元只**读**它们产出的清单与条目（`onboarding_checklist` / `onboarding_item`）作为终态前置。

相关 design Decisions：D3（终态由 HR 人工确认、清单未完成只提示不阻止、`abandon` 写 `refused` 且 ⛔ 不写 `rejection_record`、⛔ 无任何自动终态路径）、D4（入职后处置＝**登记与执行分离**；登记与 `confirm_hired` 同事务，策略未签认 ⇒ `policy_version=NULL`、`executed_at` 恒空）、D7（U3 前置 = U2；`offer-generation` U5 是生产上的 `hired` 入口，不阻塞本单元代码）。

### 上游参照的磁盘真身核对（2026-10-11 本会话在 worktree 上逐项核实）

| 项 | 磁盘真身 | 引用 |
|---|---|---|
| 六张入职域表（含 `data_disposition_queue`） | ✅ 均在 `SCHEMA`；`(application_id, category)` 唯一索引已在 | `app/storage/db.py` 714–845 行 |
| `data_disposition_queue.category` 六值 | ✅ `resume_file / parsed_fields / scores / interview / contact / offer_letter` | `app/storage/db.py` 828–833 行 |
| `hr_account.role` | ✅ 三值 `hr / interviewer / dept_manager`，默认 `'hr'` | `app/storage/db.py` 705–715 行 |
| U2 节点 `effect_instantiate_checklist` / `effect_update_item` | ✅ 已落 main（`d6c635f` merge，U2 Task 1–3） | `app/graph/onboarding_nodes.py` |
| U2 幂等基础设施 | ✅ `idempotent_effect(node_name)` ＋ `effect_log`（`effect_key` PRIMARY KEY＋唯一索引） | `app/storage/idempotency.py`；`app/storage/db.py:65-71` |
| U2 页面与纯函数（Task 4–8） | ❌ **未落地**：`app/agents/onboarding_progress.py`、`app/web/static/onboarding_checklist.html`、`tests/test_onboarding_pages.py` 磁盘上都不存在 | `ls app/agents/ app/web/static/ tests/` |
| `application.status` CHECK | ❌ `('active','rejected','withdrawn','hired')`——**不含 `'refused'`**（U2 Task 1 只放宽到 `hired`；`{ongoing,hired,refused}` 全量迁移属 offer-generation U5，未交付） | `app/storage/db.py` 441–451；`_rebuild_application_status_check` 1816–1871 |
| `offer.status` CHECK | ❌ `('pending_approval','needs_revision','approved','exported','accepted','declined','negotiating')`——**不含 `'abandoned'`** | `app/storage/db.py` 1320–1323 |
| `application_stage_history.action` CHECK | ❌ `('scheduled','rescheduled','cancelled','completed','no_show')` + NULL——**不含 `'onboarded'/'abandoned'`** | `app/storage/db.py` 473–475；`_ADDED_COLUMNS` 1473–1474 |
| `stage` 预置终态 | ✅ `('hired','已入职','hired')`、`('rejected','已淘汰','rejected')`；`stage_type` 枚举**没有** `refused` | `app/storage/db.py` 419–432 |
| `rejection_record` | ✅ `reason_type CHECK IN ('hard_rule','human_decision')`（`'ai_score'` 被数据库拒） | `app/storage/db.py` 499–521 |
| 合规断言模块 | ✅ 四条 `COMPLIANCE_ASSERTIONS` ＋ CLI（`--db/--mirror`）；⛔ 该模块**不 import `app.config`**（只读、观测端） | `app/audit/assertions.py`；`app/audit/__init__.py` 分层规矩 |
| `Settings.retention_policy_signed_version` | ✅ 默认 `None`（U1 Task 4 已落） | `app/config.py:84` |

**据此的必要偏离**（详见文末「偏离登记」，逐条有理由）：tasks 3.1/3.3 要写的三个字面值（`application.status='refused'`、`offer.status='abandoned'`、`action='onboarded'`）在**当前磁盘状态域上都写不进去**（会被 CHECK 拒），故 **Task 1 / Task 2 先把三个值域补齐**——手法与 U2 Task 1（`application.status` +hired）逐字同源：SQLite 改不了 CHECK，只能整表重建迁移。

---

## Global Constraints

以下条目从 `CLAUDE.md`「工程铁律」「合规红线」与本变更包 `design.md` **逐字复制**（或标注为「本包约束」）。**每个 Task 的验收隐含包含本节全部内容**；reviewer 会把这一段当注意力透镜。

### 本包合规约束（opener 1001P §零 / 1001T §一 逐字）

> **材料本身不入库**（只存清单条目状态）；进度可见性限「HR 全量／用人部门只读本部门」。

**本单元与这条的关系**：终态确认 UI 只多两个按钮与两个文本框（实际入职日/说明、放弃原因），⛔ 不加任何上传控件；终态节点不新增 `onboarding_item` 列。reviewer 的机械判据：Task 8 的断言①（`onboarding_item` 无内容列）＋ Task 7 的"终态卡片无 `type="file"`"用例。

### 工程铁律（不可违背，与本单元相关条目逐字）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。

> **本单元直接触发这一条**：`effect_complete_onboarding` 独占一个节点（Task 4/5），幂等键 `{application_id}:effect_complete_onboarding:{hired|abandoned}`；`effect_enqueue_disposition` 的幂等键是**每类一把** `{application_id}:effect_enqueue_disposition:{category}`（Task 3）。"同事务"用 `_enqueue_disposition_rows()`（⛔ 不 commit）兑现：`effect_complete_onboarding` 被 `@idempotent_effect` 装饰，装饰器在函数体返回后一次性 `INSERT effect_log` ＋ `commit()`，所以 history、清单 closed、六行处置登记、七条 `effect_log`（1 条终态 + 6 条登记）**全在同一个事务里**。Task 4 的 `test_confirm_hired_rolls_back_everything_on_failure` 在写库中途制造异常，断言业务行一行都没落盘。

2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。

> **本单元与这条的关系**：U3 没有 `compute_*`（终态没有可计算的判断，前置校验是 IO 读 + 比较，属节点内部）；新增的两个函数都是 `effect_*`，都落 `app/graph/onboarding_nodes.py`（L4 层）。`terminal_history_rows()` 是**只读**辅助（SELECT），不是节点，命名不带 `effect_`。

3–7 条（AI 评分持久化、`evidence_ref`、`temperature=0`、企微回调、`langgraph>=1.0.10`）：**本单元不触发**——U3 无 LLM 调用、无评分、无 `criterion_score`、无企微、不改依赖版本。

### 合规红线（本单元相关条目）

- **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。审计断言：`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。

> **本单元直接触发这一条**：放弃入职是**候选人自愿退出，不是我方淘汰**——`abandoned` 分支 ⛔ 不写 `rejection_record`（Task 5 的 `test_abandon_writes_no_rejection_record` 断言行数不变），Task 8 的断言③ 把"`application.status='refused'` 的投递不得有拒绝记录"接进 CI。

- **AI 生成的 JD、拒信、邀约须带标识**、**禁止人脸/表情分析**、**模型全部走境内**、**绝不用历史录用结果做监督信号**、**候选人入口一律用一次性邀请链接**、**主观描述不得进入硬门槛规则**：本单元**均不触发**（无 AI 生成内容、无人脸、无模型调用、无监督信号、无候选人对外通道、无硬门槛规则）；本单元 ⛔ 不对外发送任何东西。

- 🔴 **留存策略未签认前不得执行任何删除或脱敏**（design D4；`CLAUDE.md` 不可代项「真实简历数据处理范围的变更」）：本单元**只登记**（`policy_version=NULL`、`planned_action='pending'`、`due_at=NULL`、`executed_at` 恒空），⛔ 不执行、⛔ 不建定时任务（design D5：本包不做提醒/调度）。Task 8 的断言② 是这条的可归因机器判据。

### 部署约束（本单元相关）

- **路径前缀就绪**：FastAPI `root_path=/hr/recruit-agent`，前端资源与接口调用**一律相对路径**，禁止硬编码 `/static/…` `/api/…`。Task 7 的页面片段沿用 `<!--BASE_HREF-->` + 相对 `fetch`（`app/web/server.py::_substitute_base_href` 把占位符换成 `<base href>`，`root_path=""` 时为 `/`，故相对路径从域根解析）。
- ⛔ 本单元不引 `.51` 服务器、不处理真实简历、不删任何数据、不发任何消息、不建定时任务、不产生不可逆动作。

---

## 明确的范围边界（U3 **不做**什么）

| 不做 | 归属 |
|---|---|
| `effect_execute_disposition`（到期删除/脱敏执行单元） | U4（4.3）；且受 🔴 留存策略签认闸 |
| 脱敏规则 `app/storage/anonymize.py` | U4（4.2） |
| 「脱敏后既有断言按已处置口径通过」 | U4（4.4） |
| `application.status` 的 `{ongoing, hired, refused}` **全量**迁移 | offer-generation U5（`effect_apply_offer_outcome`）；U3 只补 `'refused'`（`'ongoing'` 与磁盘既有 `'active'` 是同一语义的两种写法，⛔ 不并存两值，登记为偏离） |
| U2 的清单页/总览页/部门页主体、`progress()` 纯函数 | U2（2.3–2.6）；U3 只在清单页末尾**追加**终态卡片 |
| 提醒 / 定时任务 / 企微建号 | Non-Goals（design D5）；TD-11 记入 tech-debt |
| `.51` 发版、留存策略签认、处置执行开启 | 不可代项（U5 4.5/5.4，Shao Peishen） |

**U3 合并后系统的可观察行为**：HR 在清单页可以对"入职日已到"的投递点「确认已入职」（可填实际入职日与说明）或「放弃入职」（须填原因）；确认后投递有且只有一条终态流转事实、清单关闭、该候选人**登记**六类待处置事项（策略未签认 ⇒ 一行不执行）；放弃后 `application.status='refused'`、Offer 变"已放弃"、⛔ 不产生拒绝记录；`GET /api/applications/{id}/onboarding/terminal` 给出"待确认"状态供页面显示。**既有投递、简历、评分、排期、Offer 流程零变化**（唯一例外：三个 CHECK 的合法值域变宽，既有行的值与行为一字不改）。

---

## File Structure

| 文件 | 动作 | 责任 |
|---|---|---|
| `app/storage/db.py` | 修改 | `application.status` CHECK +`'refused'`（SCHEMA ＋ 重建迁移）、`offer.status` CHECK +`'abandoned'`（SCHEMA ＋ 重建迁移）、`application_stage_history.action` CHECK +`'onboarded'/'abandoned'`（SCHEMA ＋ `_ADDED_COLUMNS` ＋ 重建迁移）、`init_schema` 调用三个重建（Task 1/2） |
| `app/graph/onboarding_nodes.py` | 修改 | `OnboardingTerminalRejected`、`DISPOSITION_CATEGORIES`、`terminal_history_rows()`、`_enqueue_disposition_rows()`、`effect_enqueue_disposition()`（Task 3）、`effect_complete_onboarding()`（Task 4/5） |
| `app/web/server.py` | 修改 | 三个请求模型 ＋ `GET .../onboarding/terminal` ＋ `POST .../confirm-hired` ＋ `POST .../abandon` ＋ `_terminal_state_payload()`（Task 7） |
| `app/web/static/onboarding_checklist.html` | 修改 | 末尾追加终态卡片（含"待确认"提示）与自包含脚本（Task 7）。**前置：该文件由 U2 Task 5 产出** |
| `app/audit/assertions.py` | 修改 | 三条新断言 ＋ `CONFIG_GATED_ASSERTIONS` ＋ `run_compliance_assertions(..., retention_policy_signed_version=None)` ＋ CLI 新旗标（Task 8） |
| `docs/audit-and-outbound-ops.md` | 修改 | 追加巡检命令与 `--retention-policy-signed-version` 的口径（Task 8） |
| `tests/test_db_terminal_status_migration.py` | 新建 | `application.status`/`offer.status` 值域放宽（新库/老库/幂等）（Task 1） |
| `tests/test_db_stage_history_terminal_action.py` | 新建 | `application_stage_history.action` 值域放宽（新库/老库/幂等）（Task 2） |
| `tests/test_onboarding_terminal_nodes.py` | 新建 | 两个终态节点的单元测试（Task 3/4/5） |
| `tests/test_onboarding_terminal_invariant.py` | 新建 | 终态不变式 ＋ ⛔ 无自动终态路径的反证（Task 6） |
| `tests/test_onboarding_terminal_pages.py` | 新建 | 三个端点的角色/幂等/前置拒绝 ＋ 页面片段断言（Task 7） |
| `tests/test_audit_assertions.py` | 修改 | 断言条数 4→6（`COMPLIANCE_ASSERTIONS`）/4→7（`run_compliance_assertions`）＋ 三条新断言的正向用例（Task 8） |
| `tests/test_audit_assertion_effectiveness.py` | 修改 | 同上条数修正 ＋ 三条"造违例 → 必须失败"的反证（Task 8） |
| `tests/test_compliance_cli.py` | 修改 | `--retention-policy-signed-version` 的退出码契约（Task 8） |
| `tests/test_db_m2_schema.py` | 修改 | `test_application_stage_history_action_check` 的 docstring 措辞跟着值域扩到七个动作（⛔ 断言不动）（Task 2） |

> ⛔ Codex 泳道：本计划各 Task **不写 git add/commit/push 步骤**——worktree 内不能自行提交，收口由执行器 `run-lanes.sh` → `scripts/lane_collect.py` 代做（AGENTS.md §4）。每个 Task 以"测试全绿"收尾即可。

---

### Task 1: `application.status` +`'refused'`、`offer.status` +`'abandoned'`（tasks 3.3 的前置）

**Files:**
- Modify: `app/storage/db.py`（`SCHEMA` 的 `application` 与 `offer` 两张 `CREATE TABLE`；`_application_status_check_allows_hired` → `_application_status_check_allows`；`_rebuild_application_status_check` 四值改五值；新增 `_offer_status_check_allows_abandoned` / `_rebuild_offer_status_check`；`init_schema` 调用）
- Test: `tests/test_db_terminal_status_migration.py`（新建）

**Interfaces:**
- Consumes: `app.storage.db.get_connection` / `init_schema`（签名不改）
- Produces: `application.status` 合法值 `('active','rejected','withdrawn','hired','refused')`；`offer.status` 合法值 `(…,'negotiating','abandoned')`；`init_schema` 对老库自动重建两表（幂等）

- [ ] **Step 1: Write the failing test**

新建 `tests/test_db_terminal_status_migration.py`：

```python
"""onboarding-flow U3 前置（tasks 3.1/3.3）：终态要写的字面值先要在状态域里合法。

磁盘真身：application.status 是四值（含 U2 加的 'hired'）、offer.status 七值，
都不含本单元要写的 'refused' / 'abandoned'。本文件只管这两张表；流转事实表
action 的值域在 tests/test_db_stage_history_terminal_action.py（Task 2）。
手法与 tests/test_db_application_status_hired.py（U2 Task 1）逐字同源。
"""
import sqlite3

import pytest

from app.storage.db import get_connection, init_schema


def _seed_job_candidate_resume(conn):
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '嵌入式工程师', 'approved')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('res-1', 'j1', 'synthetic', 'a.docx', 'sha-1', 'hr-1')"
    )
    conn.commit()


def _insert_application(conn, application_id, status):
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status) "
        "VALUES (?, 'cand-1', 'j1', 'res-1', 'hired', ?)",
        (application_id, status),
    )


def _insert_offer(conn, offer_id, application_id, status):
    conn.execute(
        "INSERT INTO offer (id, application_id, job_id, department, start_date, report_to, "
        "status, approval_round, created_by) "
        "VALUES (?, ?, 'j1', '研发部', '2026-10-20', 'manager-1', ?, 1, 'hr')",
        (offer_id, application_id, status),
    )


# ── 新库 ────────────────────────────────────────────────────────────────


def test_fresh_schema_accepts_refused_and_abandoned(tmp_path):
    conn = get_connection(str(tmp_path / "app.db"))
    init_schema(conn)
    _seed_job_candidate_resume(conn)
    _insert_application(conn, "app-1", "refused")
    _insert_offer(conn, "offer-1", "app-1", "abandoned")
    conn.commit()
    assert conn.execute(
        "SELECT status FROM application WHERE id='app-1'"
    ).fetchone() == ("refused",)
    assert conn.execute(
        "SELECT status FROM offer WHERE id='offer-1'"
    ).fetchone() == ("abandoned",)


def test_fresh_schema_still_rejects_unknown_status(tmp_path):
    conn = get_connection(str(tmp_path / "app.db"))
    init_schema(conn)
    _seed_job_candidate_resume(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_application(conn, "app-bad", "ghost")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_offer(conn, "offer-bad", "app-bad", "auto_sent")


# ── 老库（.51 现网形态：四值 application + 七值 offer）────────────────────

_LEGACY_DDL = """
CREATE TABLE stage (id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL, stage_type TEXT NOT NULL);
CREATE TABLE job (id TEXT PRIMARY KEY, title TEXT NOT NULL, department TEXT,
                  status TEXT NOT NULL DEFAULT 'drafting',
                  created_at TEXT NOT NULL DEFAULT (datetime('now')),
                  parse_confidence_threshold REAL NOT NULL DEFAULT 0.7);
CREATE TABLE candidate (id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL, phone_hash TEXT,
                        created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE resume (id TEXT PRIMARY KEY NOT NULL, job_id TEXT NOT NULL REFERENCES job(id),
                     sample_class TEXT NOT NULL, file_name TEXT NOT NULL,
                     content_sha256 TEXT NOT NULL, uploaded_by TEXT NOT NULL,
                     status TEXT NOT NULL DEFAULT 'pending',
                     uploaded_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE application (
    id TEXT PRIMARY KEY NOT NULL,
    candidate_id TEXT NOT NULL REFERENCES candidate(id),
    job_id TEXT NOT NULL REFERENCES job(id),
    resume_id TEXT NOT NULL REFERENCES resume(id),
    current_stage_id TEXT NOT NULL REFERENCES stage(id),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'rejected', 'withdrawn', 'hired')),
    kanban_state TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX idx_application_resume ON application (resume_id);
CREATE INDEX idx_application_job ON application (job_id);
CREATE INDEX idx_application_candidate ON application (candidate_id);
CREATE TABLE offer (
    id TEXT PRIMARY KEY NOT NULL,
    application_id TEXT NOT NULL UNIQUE REFERENCES application(id),
    job_id TEXT NOT NULL REFERENCES job(id),
    department TEXT NOT NULL,
    start_date TEXT NOT NULL,
    report_to TEXT NOT NULL,
    note TEXT,
    status TEXT NOT NULL DEFAULT 'pending_approval' CHECK (
        status IN ('pending_approval', 'needs_revision', 'approved', 'exported',
                   'accepted', 'declined', 'negotiating')
    ),
    approval_round INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_by TEXT,
    updated_at TEXT
);
CREATE TABLE offer_approval (
    id TEXT PRIMARY KEY NOT NULL,
    offer_id TEXT NOT NULL REFERENCES offer(id),
    round INTEGER NOT NULL,
    level INTEGER NOT NULL,
    approver TEXT NOT NULL,
    decision TEXT NOT NULL,
    comment TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_offer_approval_offer ON offer_approval (offer_id);
INSERT INTO stage (id, name, stage_type) VALUES ('initial', '初筛', 'initial');
INSERT INTO stage (id, name, stage_type) VALUES ('hired', '已入职', 'hired');
INSERT INTO job (id, title, status) VALUES ('j1', '嵌入式工程师', 'approved');
INSERT INTO candidate (id, name) VALUES ('cand-1', '张三');
INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by)
       VALUES ('res-1', 'j1', 'synthetic', 'a.docx', 'sha-1', 'hr-1');
INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status)
       VALUES ('app-old', 'cand-1', 'j1', 'res-1', 'hired', 'hired');
INSERT INTO offer (id, application_id, job_id, department, start_date, report_to, status,
                   approval_round, created_by)
       VALUES ('offer-old', 'app-old', 'j1', '研发部', '2026-10-20', 'manager-1', 'accepted', 1, 'hr');
INSERT INTO offer_approval (id, offer_id, round, level, approver, decision)
       VALUES ('appr-1', 'offer-old', 1, 1, 'boss', 'approved');
"""


def _legacy_conn(tmp_path):
    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(path)
    raw.executescript(_LEGACY_DDL)
    raw.commit()
    raw.close()
    return get_connection(str(path))


def test_legacy_status_checks_are_widened_and_rows_survive(tmp_path):
    conn = _legacy_conn(tmp_path)
    init_schema(conn)

    assert conn.execute("SELECT status FROM application WHERE id='app-old'").fetchone() == ("hired",)
    assert conn.execute("SELECT status FROM offer WHERE id='offer-old'").fetchone() == ("accepted",)
    conn.execute("UPDATE application SET status='refused' WHERE id='app-old'")
    conn.execute("UPDATE offer SET status='abandoned' WHERE id='offer-old'")
    conn.commit()
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    # offer 被 offer_approval 外键引用：重建后引用行必须还在
    assert conn.execute(
        "SELECT COUNT(*) FROM offer_approval WHERE offer_id='offer-old'"
    ).fetchone()[0] == 1
    for idx in ("idx_application_resume", "idx_application_job",
                "idx_application_candidate", "idx_offer_approval_offer"):
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (idx,)
        ).fetchone() is not None, f"{idx} 应在重建后存在"


def test_init_schema_is_idempotent_for_status_widening(tmp_path):
    conn = _legacy_conn(tmp_path)
    init_schema(conn)
    init_schema(conn)  # 第二次必须空转：不报错、不丢行
    assert conn.execute("SELECT COUNT(*) FROM application").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_legacy_application_without_status_column_is_left_alone(tmp_path):
    """M2 U1 之前形态的老库照样要跳过（同 U2 用例）：重建的
    `INSERT ... SELECT ... status ...` 会撞 `no such column: status`。"""
    path = tmp_path / "legacy_no_status.db"
    raw = sqlite3.connect(path)
    raw.executescript(
        """
        CREATE TABLE stage (id TEXT PRIMARY KEY, name TEXT NOT NULL, stage_type TEXT);
        CREATE TABLE job (id TEXT PRIMARY KEY, title TEXT NOT NULL,
                          status TEXT NOT NULL DEFAULT 'drafting',
                          created_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE candidate (id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL,
                                phone_hash TEXT,
                                created_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE resume (id TEXT PRIMARY KEY NOT NULL, job_id TEXT NOT NULL REFERENCES job(id),
                             sample_class TEXT NOT NULL, file_name TEXT NOT NULL,
                             content_sha256 TEXT NOT NULL, uploaded_by TEXT NOT NULL,
                             uploaded_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE application (
            id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL REFERENCES candidate(id),
            job_id TEXT NOT NULL REFERENCES job(id),
            resume_id TEXT NOT NULL REFERENCES resume(id),
            current_stage_id TEXT NOT NULL REFERENCES stage(id)
        );
        INSERT INTO stage (id, name, stage_type) VALUES ('initial', '初筛', 'initial');
        INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师');
        INSERT INTO candidate (id, name) VALUES ('cand-1', '张三');
        INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by)
               VALUES ('res-1', 'j1', 'synthetic', 'a.docx', 'sha-1', 'hr-1');
        INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id)
               VALUES ('app-old', 'cand-1', 'j1', 'res-1', 'initial');
        """
    )
    raw.commit()
    raw.close()

    conn = get_connection(str(path))
    init_schema(conn)

    assert conn.execute(
        "SELECT current_stage_id FROM application WHERE id='app-old'"
    ).fetchone() == ("initial",)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_db_terminal_status_migration.py -q`
Expected: FAIL —— `test_fresh_schema_accepts_refused_and_abandoned` 与老库用例撞 `CHECK constraint failed`。

- [ ] **Step 3: Write minimal implementation**

① `app/storage/db.py` 的 `SCHEMA` 里 `application` 表（441–451 行）改成：

```sql
CREATE TABLE IF NOT EXISTS application (
    id TEXT PRIMARY KEY NOT NULL,
    candidate_id TEXT NOT NULL REFERENCES candidate(id),
    job_id TEXT NOT NULL REFERENCES job(id),
    resume_id TEXT NOT NULL REFERENCES resume(id),
    current_stage_id TEXT NOT NULL REFERENCES stage(id),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'rejected', 'withdrawn', 'hired', 'refused')),
    kanban_state TEXT CHECK (kanban_state IS NULL OR kanban_state IN ('pending_reject')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

同步改该表上方注释：`'refused'` 由 onboarding-flow U3 加入（候选人放弃入职的终态，tasks 3.3）；`'ongoing'` 仍只以既有 `'active'` 表达（design 的 `{ongoing,hired,refused}` 里 `ongoing` 与磁盘 `active` 是同一语义——⛔ 不并存两值，见「偏离登记」3）。

② `SCHEMA` 里 `offer` 表（1306–1327 行）的 `status` 分支改成：

```sql
    status TEXT NOT NULL DEFAULT 'pending_approval' CHECK (
        status IN ('pending_approval', 'needs_revision', 'approved', 'exported',
                   'accepted', 'declined', 'negotiating', 'abandoned')
    ),
```

③ 把 U2 落地的 `_application_status_check_allows_hired`（1786–1813 行）替换为取值参数化版本，并把 `_rebuild_application_status_check` 的 CHECK 由四值改五值：

```python
_APPLICATION_STATUS_CHECK_RE = re.compile(r"\bstatus\b[^,]*?\bCHECK\b", re.IGNORECASE)


def _application_status_check_allows(
    conn: sqlite3.Connection, *, values: tuple[str, ...]
) -> bool:
    """application.status 上是否「没有会拒掉 values 任一取值的 CHECK」——即无需整表重建。

    SQLite 改不了 CHECK，本判断看 sqlite_master.sql 原文，手法与
    _stage_type_check_complete / _role_check_allows_dept_manager 一致。除
    「CHECK 已含全部取值」外，下面几种形态同样不需要重建，必须一并放行，否则
    重建会当场炸（tests/test_db_migration.py 的 _legacy_db 夹具实测踩到过）：

    - application 表不存在（调用点在 SCHEMA 之后，新库这里恒不成立）；
    - 表里没有 status 列：M2 U1 之前形态的老库就是这样；
    - status 列上没有 CHECK：没有任何枚举被拒，重建是空转。
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='application'"
    ).fetchone()
    if row is None or not row[0]:
        return True
    ddl = row[0]
    if all(f"'{value}'" in ddl for value in values):
        return True
    has_status_column = any(
        info[1] == "status" for info in conn.execute("PRAGMA table_info(application)")
    )
    if not has_status_column:
        return True
    return not _APPLICATION_STATUS_CHECK_RE.search(ddl)


def _rebuild_application_status_check(conn: sqlite3.Connection) -> None:
    """把 application.status 的 CHECK 放宽到五值（hired + refused）。

    U2（Task 1）先放宽到含 'hired'：实例化前置需要它。U3（tasks 3.3）再补
    'refused'：放弃入职把 application.status 置为 refused。⛔ 'ongoing' 不在
    这次放宽里——它与磁盘既有 'active' 是同一语义的两种写法，并存两值会让
    "同一个状态两处真源"，登记为偏离（见计划「偏离登记」3）。

    application 被 application_stage_history / rejection_record / offer /
    onboarding_checklist / onboarding_access_log / data_disposition_queue 外键引用，
    重建期间 PRAGMA foreign_keys=OFF，完成后 foreign_key_check 复验；三个索引
    （idx_application_resume / idx_application_job / idx_application_candidate）
    随 DROP TABLE 一起消失，必须原样重建。PRAGMA 在事务内是 no-op，try 内 commit、
    except 里 rollback 之后，finally 再重开（同 _rebuild_hr_account_role_check）。
    """
    if _application_status_check_allows(conn, values=("hired", "refused")):
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
                    CHECK (status IN ('active', 'rejected', 'withdrawn', 'hired', 'refused')),
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


_OFFER_STATUS_CHECK_RE = re.compile(r"\bstatus\b[^,]*?\bCHECK\b", re.IGNORECASE)


def _offer_status_check_allows_abandoned(conn: sqlite3.Connection) -> bool:
    """offer.status 上是否已放行 'abandoned'（无需重建）。三档放行形态同 application。"""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='offer'"
    ).fetchone()
    if row is None or not row[0]:
        return True
    ddl = row[0]
    if "'abandoned'" in ddl:
        return True
    has_status_column = any(
        info[1] == "status" for info in conn.execute("PRAGMA table_info(offer)")
    )
    if not has_status_column:
        return True
    return not _OFFER_STATUS_CHECK_RE.search(ddl)


def _rebuild_offer_status_check(conn: sqlite3.Connection) -> None:
    """把 offer.status 的 CHECK 放宽到八值（+abandoned，tasks 3.3）。

    offer 被 offer_approval.offer_id 外键引用，重建期间 PRAGMA foreign_keys=OFF
    并在完成后 foreign_key_check 复验。offer 上没有额外索引（UNIQUE
    application_id 写在表内，随重建保留）；idx_offer_approval_offer 建在
    offer_approval 上，不受影响。
    """
    if _offer_status_check_allows_abandoned(conn):
        return
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN")
        conn.execute(
            """
            CREATE TABLE offer_new (
                id TEXT PRIMARY KEY NOT NULL,
                application_id TEXT NOT NULL UNIQUE REFERENCES application(id),
                job_id TEXT NOT NULL REFERENCES job(id),
                department TEXT NOT NULL,
                start_date TEXT NOT NULL,
                report_to TEXT NOT NULL,
                note TEXT,
                status TEXT NOT NULL DEFAULT 'pending_approval' CHECK (
                    status IN ('pending_approval', 'needs_revision', 'approved', 'exported',
                               'accepted', 'declined', 'negotiating', 'abandoned')
                ),
                approval_round INTEGER NOT NULL DEFAULT 1,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_by TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO offer_new "
            "(id, application_id, job_id, department, start_date, report_to, note, status, "
            " approval_round, created_by, created_at, updated_by, updated_at) "
            "SELECT id, application_id, job_id, department, start_date, report_to, note, status, "
            "       approval_round, created_by, created_at, updated_by, updated_at FROM offer"
        )
        conn.execute("DROP TABLE offer")
        conn.execute("ALTER TABLE offer_new RENAME TO offer")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise sqlite3.IntegrityError(f"offer 重建后外键不一致: {violations}")
```

④ `init_schema`（1873–1891 行）在 `_rebuild_application_status_check(conn)` 之后插入一行：

```python
    _rebuild_offer_status_check(conn)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_db_terminal_status_migration.py tests/test_db_application_status_hired.py tests/test_db_offer_schema.py tests/test_db_migration.py -q`
Expected: 全绿（U2 的 `hired` 用例与 Offer 包的值域用例都不得回归）。

---

### Task 2: `application_stage_history.action` +`'onboarded'` / `'abandoned'`（tasks 3.1/3.3 的前置）

**Files:**
- Modify: `app/storage/db.py`（`SCHEMA` 的 `application_stage_history`；`_ADDED_COLUMNS` 的 action 条目；新增 `_stage_history_action_check_allows_terminal` / `_rebuild_application_stage_history_action_check`；`init_schema` 调用）
- Modify: `tests/test_db_m2_schema.py`（`test_application_stage_history_action_check` 的 docstring 措辞；⛔ 断言不动）
- Test: `tests/test_db_stage_history_terminal_action.py`（新建）

**Interfaces:**
- Consumes: `init_schema`（签名不改）
- Produces: `action ∈ ('scheduled','rescheduled','cancelled','completed','no_show','onboarded','abandoned', NULL)`；Task 6 的终态不变式用它区分"终态事实"与其它流转行

- [ ] **Step 1: Write the failing test**

新建 `tests/test_db_stage_history_terminal_action.py`：

```python
"""onboarding-flow U3 前置（tasks 3.1/3.3）：终态流转事实靠 action 承载语义。

两个终态都**不改阶段**（from_stage_id = to_stage_id）：投靠在 Offer 接受时就已
流转到 hired 阶段，入职日确认是"同一阶段上的动作"；放弃入职是候选人自愿退出，
⛔ 不能写成流转到 'rejected'（那会把自愿退出污染进淘汰口径）。所以 action
必须能表达这两个动作——值域由 CHECK 钉死。
"""
import sqlite3

import pytest

from app.storage.db import get_connection, init_schema

_TERMINAL_ACTIONS = ("onboarded", "abandoned")
_SCHEDULING_ACTIONS = ("scheduled", "rescheduled", "cancelled", "completed", "no_show")


def _seed_application(conn):
    conn.execute("INSERT INTO job (id, title, status) VALUES ('j1', '嵌入式工程师', 'approved')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('res-1', 'j1', 'synthetic', 'a.docx', 'sha-1', 'hr-1')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id, status) "
        "VALUES ('app-1', 'cand-1', 'j1', 'res-1', 'hired', 'hired')"
    )
    conn.commit()


def _insert_history(conn, history_id, action, stage="hired"):
    conn.execute(
        "INSERT INTO application_stage_history "
        "(id, application_id, from_stage_id, to_stage_id, actor_type, actor, action) "
        "VALUES (?, 'app-1', ?, ?, 'human', 'hr-user', ?)",
        (history_id, stage, stage, action),
    )


def test_fresh_schema_accepts_terminal_actions(tmp_path):
    conn = get_connection(str(tmp_path / "app.db"))
    init_schema(conn)
    _seed_application(conn)
    for index, action in enumerate(_TERMINAL_ACTIONS):
        _insert_history(conn, f"h-{index}", action)
    conn.commit()
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history "
        "WHERE action IN ('onboarded','abandoned')"
    ).fetchone()[0] == 2


def test_fresh_schema_still_rejects_unknown_action(tmp_path):
    conn = get_connection(str(tmp_path / "app.db"))
    init_schema(conn)
    _seed_application(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_history(conn, "h-bad", "onboarded_by_ai")


def test_legacy_action_check_is_widened(tmp_path):
    """老库（interview-scheduling U2 那份五值 CHECK）升级后：两个终态动作可写、
    既有排期动作照旧、非法值照拒、既有行不变、索引复原、外键复验通过。"""
    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(path)
    raw.executescript(
        """
        CREATE TABLE stage (id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL, stage_type TEXT);
        CREATE TABLE job (id TEXT PRIMARY KEY, title TEXT NOT NULL,
                          status TEXT NOT NULL DEFAULT 'drafting',
                          created_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE candidate (id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL,
                                phone_hash TEXT,
                                created_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE resume (id TEXT PRIMARY KEY NOT NULL, job_id TEXT NOT NULL REFERENCES job(id),
                             sample_class TEXT NOT NULL, file_name TEXT NOT NULL,
                             content_sha256 TEXT NOT NULL, uploaded_by TEXT NOT NULL,
                             uploaded_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE application (
            id TEXT PRIMARY KEY NOT NULL,
            candidate_id TEXT NOT NULL REFERENCES candidate(id),
            job_id TEXT NOT NULL REFERENCES job(id),
            resume_id TEXT NOT NULL REFERENCES resume(id),
            current_stage_id TEXT NOT NULL REFERENCES stage(id),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','rejected','withdrawn','hired','refused')),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE application_stage_history (
            id TEXT PRIMARY KEY NOT NULL,
            application_id TEXT NOT NULL REFERENCES application(id),
            from_stage_id TEXT REFERENCES stage(id),
            to_stage_id TEXT NOT NULL REFERENCES stage(id),
            actor_type TEXT NOT NULL CHECK (actor_type IN ('human','agent')),
            actor TEXT,
            action TEXT CHECK (action IS NULL OR action IN
                ('scheduled','rescheduled','cancelled','completed','no_show')),
            detail_json TEXT,
            occurred_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX idx_application_stage_history_application
            ON application_stage_history (application_id);
        INSERT INTO stage (id, name, stage_type) VALUES ('initial', '初筛', 'initial');
        INSERT INTO stage (id, name, stage_type) VALUES ('interview', '面试', 'interview');
        INSERT INTO stage (id, name, stage_type) VALUES ('hired', '已入职', 'hired');
        INSERT INTO job (id, title, status) VALUES ('j1', '嵌入式工程师', 'approved');
        INSERT INTO candidate (id, name) VALUES ('cand-1', '张三');
        INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by)
               VALUES ('res-1', 'j1', 'synthetic', 'a.docx', 'sha-1', 'hr-1');
        INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id)
               VALUES ('app-1', 'cand-1', 'j1', 'res-1', 'hired');
        INSERT INTO application_stage_history (id, application_id, to_stage_id, actor_type, action)
               VALUES ('h-old', 'app-1', 'interview', 'human', 'rescheduled');
        """
    )
    raw.commit()
    raw.close()

    conn = get_connection(str(path))
    init_schema(conn)

    assert conn.execute(
        "SELECT action FROM application_stage_history WHERE id='h-old'"
    ).fetchone() == ("rescheduled",)
    for index, action in enumerate(_SCHEDULING_ACTIONS + _TERMINAL_ACTIONS):
        _insert_history(conn, f"h-{index}", action)
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        _insert_history(conn, "h-bad", "onboarded_by_ai")
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' "
        "AND name='idx_application_stage_history_application'"
    ).fetchone() is not None


def test_init_schema_is_idempotent_for_action_widening(tmp_path):
    conn = get_connection(str(tmp_path / "app.db"))
    init_schema(conn)
    init_schema(conn)
    _seed_application(conn)
    _insert_history(conn, "h-1", "onboarded")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM application_stage_history").fetchone()[0] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_db_stage_history_terminal_action.py -q`
Expected: FAIL —— `CHECK constraint failed: action`。

- [ ] **Step 3: Write minimal implementation**

① `SCHEMA` 的 `application_stage_history`（466–478 行）的 action 分支改成七值 + NULL：

```sql
    action TEXT CHECK (
        action IS NULL OR action IN ('scheduled', 'rescheduled', 'cancelled', 'completed',
                                     'no_show', 'onboarded', 'abandoned')
    ),
```

② `_ADDED_COLUMNS`（1473–1474 行）的同一条目同步改成同一份值域：

```python
    ("application_stage_history", "action",
     "TEXT CHECK (action IS NULL OR action IN ('scheduled', 'rescheduled', 'cancelled', "
     "'completed', 'no_show', 'onboarded', 'abandoned'))"),
```

③ 在 `_rebuild_offer_status_check` 之后新增两个函数：

```python
_STAGE_HISTORY_TERMINAL_ACTIONS = ("onboarded", "abandoned")


def _stage_history_action_check_allows_terminal(conn: sqlite3.Connection) -> bool:
    """application_stage_history.action 是否已放行两个终态动作（无需重建）。

    放行形态三档（表/列不存在、CHECK 已含全部取值、列上没有 CHECK），同
    _application_status_check_allows；这里改用「action 定义里有没有 CHECK」
    来判断第三档——本表的 action 定义在 to_stage_id 与 detail_json 之间，
    用列名切片比正则更不容易看错。
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='application_stage_history'"
    ).fetchone()
    if row is None or not row[0]:
        return True
    ddl = row[0]
    if all(f"'{action}'" in ddl for action in _STAGE_HISTORY_TERMINAL_ACTIONS):
        return True
    has_action_column = any(
        info[1] == "action"
        for info in conn.execute("PRAGMA table_info(application_stage_history)")
    )
    if not has_action_column:
        return True
    action_definition = ddl.split("action", 1)[-1].split("detail_json", 1)[0]
    return "CHECK" not in action_definition


def _rebuild_application_stage_history_action_check(conn: sqlite3.Connection) -> None:
    """把 application_stage_history.action 的 CHECK 扩到七值（+onboarded/+abandoned）。

    ⚠️ 必须在 apply_column_migrations **之后**调用：M2 形态的老库连 action 列都
    没有，是上一步用 _ADDED_COLUMNS 补出来的（补出来的 CHECK 已是新值域，此时
    本函数空转）；只有"列已存在、CHECK 是排期包那份五值"的老库才真正重建——
    这也是重建的 SELECT 列表能直接点到 action/detail_json 两列的前提。
    重建期间 PRAGMA foreign_keys=OFF（本表有指向 application/stage 的外键），
    完成后 foreign_key_check 复验；索引 idx_application_stage_history_application
    随 DROP TABLE 消失，必须原样重建。
    """
    if _stage_history_action_check_allows_terminal(conn):
        return
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN")
        conn.execute(
            """
            CREATE TABLE application_stage_history_new (
                id TEXT PRIMARY KEY NOT NULL,
                application_id TEXT NOT NULL REFERENCES application(id),
                from_stage_id TEXT REFERENCES stage(id),
                to_stage_id TEXT NOT NULL REFERENCES stage(id),
                actor_type TEXT NOT NULL CHECK (actor_type IN ('human', 'agent')),
                actor TEXT,
                action TEXT CHECK (
                    action IS NULL OR action IN ('scheduled', 'rescheduled', 'cancelled',
                                                 'completed', 'no_show', 'onboarded', 'abandoned')
                ),
                detail_json TEXT,
                occurred_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "INSERT INTO application_stage_history_new "
            "(id, application_id, from_stage_id, to_stage_id, actor_type, actor, action, "
            " detail_json, occurred_at) "
            "SELECT id, application_id, from_stage_id, to_stage_id, actor_type, actor, action, "
            "       detail_json, occurred_at FROM application_stage_history"
        )
        conn.execute("DROP TABLE application_stage_history")
        conn.execute("ALTER TABLE application_stage_history_new RENAME TO application_stage_history")
        conn.execute(
            "CREATE INDEX idx_application_stage_history_application "
            "ON application_stage_history (application_id)"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise sqlite3.IntegrityError(f"application_stage_history 重建后外键不一致: {violations}")
```

④ `init_schema` 里 `_rebuild_offer_status_check(conn)` 之后插入一行：

```python
    _rebuild_application_stage_history_action_check(conn)
```

⑤ `tests/test_db_m2_schema.py::test_application_stage_history_action_check` 的 docstring 里「只有四个排期动作 + no_show」改成「七个动作（五个排期动作 + onboarding-flow U3 加的 `onboarded`/`abandoned`）+ NULL，别的一律拒」；⛔ 断言与非法样例（`'rescheduled_by_ai'`）不动。

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_db_stage_history_terminal_action.py tests/test_db_m2_schema.py tests/test_db_migration.py -q`
Expected: 全绿。

---

### Task 3: `effect_enqueue_disposition`（tasks 3.2，六类登记 + 每类一把幂等键）

**Files:**
- Modify: `app/graph/onboarding_nodes.py`（追加 `DISPOSITION_CATEGORIES` / `_enqueue_disposition_rows` / `effect_enqueue_disposition`）
- Test: `tests/test_onboarding_terminal_nodes.py`（新建，本 Task 先建文件头与登记用例）

**Interfaces:**
- Consumes: `data_disposition_queue`（U1 已建，`(application_id, category)` 唯一）、`effect_log`
- Produces:
  - `DISPOSITION_CATEGORIES: tuple[str, ...]`（六类，与 DB CHECK 逐字同源）
  - `_enqueue_disposition_rows(conn, *, application_id: str, candidate_id: str) -> int`——**⛔ 不 commit**，供同事务调用（Task 4）
  - `effect_enqueue_disposition(conn, *, application_id: str, candidate_id: str) -> int`——独立入口，自带 `conn.commit()`，返回该投递的登记行数

- [ ] **Step 1: Write the failing test**

新建 `tests/test_onboarding_terminal_nodes.py`（本 Task 只先建文件头与 Task 3 用例，后续 Task 追加）：

```python
"""onboarding-flow U3 终态节点：effect_complete_onboarding（3.1/3.3）与
effect_enqueue_disposition（3.2）。

夹具与 helpers 与 tests/test_onboarding_nodes.py（U2）刻意分开：那份文件按 U2 的
Task 分节长大，本文件按 U3 的任务分节；两份互不修改，避免并发泳道互相踩。
"""
import json
import sqlite3

import pytest

from app.graph.onboarding_nodes import (
    DISPOSITION_CATEGORIES,
    OnboardingTerminalRejected,
    effect_complete_onboarding,
    effect_enqueue_disposition,
    effect_instantiate_checklist,
    effect_update_item,
    terminal_history_rows,
)
from app.storage.db import get_connection, init_schema

TODAY = "2026-10-20"
START_DATE = "2026-10-20"


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "onboarding.db"))
    init_schema(c)
    return c


def _seed_hired_application(
    conn, application_id="app-1", department="研发部", start_date=START_DATE
):
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
        "INSERT INTO offer (id, application_id, job_id, department, start_date, report_to, "
        "status, approval_round, created_by) "
        "VALUES (?, ?, 'j1', ?, ?, 'manager-1', 'accepted', 1, 'hr')",
        (f"offer-{application_id}", application_id, department, start_date),
    )
    conn.commit()


def _instantiated(conn, application_id="app-1"):
    """生成清单并返回 checklist_id（走 U2 的真节点，不手写 INSERT）。"""
    effect_instantiate_checklist(
        conn, thread_id=application_id, business_key="instantiate", created_by="hr-user"
    )
    return conn.execute(
        "SELECT id FROM onboarding_checklist WHERE application_id = ?", (application_id,)
    ).fetchone()[0]


def _finish_all_required(conn, checklist_id):
    conn.execute(
        "UPDATE onboarding_item SET status='done' WHERE checklist_id=? AND required=1",
        (checklist_id,),
    )
    conn.commit()


def _confirm(conn, application_id="app-1", **kwargs):
    kwargs.setdefault("actor_username", "hr-user")
    kwargs.setdefault("today", TODAY)
    return effect_complete_onboarding(
        conn, thread_id=application_id, business_key="hired", **kwargs
    )


def _abandon(conn, application_id="app-1", **kwargs):
    kwargs.setdefault("actor_username", "hr-user")
    return effect_complete_onboarding(
        conn, thread_id=application_id, business_key="abandoned", **kwargs
    )


# ── Task 3：effect_enqueue_disposition ──────────────────────────────────


def test_enqueue_disposition_writes_six_rows(conn):
    _seed_hired_application(conn)

    count = effect_enqueue_disposition(conn, application_id="app-1", candidate_id="cand-1")

    assert count == 6
    rows = conn.execute(
        "SELECT category, candidate_id, policy_version, planned_action, due_at, "
        "       executed_at, executed_by "
        "FROM data_disposition_queue WHERE application_id='app-1' ORDER BY category"
    ).fetchall()
    assert [r[0] for r in rows] == sorted(DISPOSITION_CATEGORIES)
    for category, candidate_id, policy_version, planned_action, due_at, executed_at, executed_by in rows:
        assert candidate_id == "cand-1"
        assert policy_version is None      # 策略未签认 ⇒ 待策略
        assert planned_action == "pending"
        assert due_at is None
        assert executed_at is None         # 🔴 未签认 ⇒ 执行时刻恒空
        assert executed_by is None


def test_enqueue_disposition_writes_one_effect_log_key_per_category(conn):
    _seed_hired_application(conn)

    effect_enqueue_disposition(conn, application_id="app-1", candidate_id="cand-1")

    keys = {
        row[0]
        for row in conn.execute(
            "SELECT effect_key FROM effect_log WHERE node_name='effect_enqueue_disposition'"
        ).fetchall()
    }
    assert keys == {
        f"app-1:effect_enqueue_disposition:{category}" for category in DISPOSITION_CATEGORIES
    }


def test_enqueue_disposition_rerun_keeps_six_rows(conn):
    _seed_hired_application(conn)

    effect_enqueue_disposition(conn, application_id="app-1", candidate_id="cand-1")
    second = effect_enqueue_disposition(conn, application_id="app-1", candidate_id="cand-1")

    assert second == 6
    assert conn.execute(
        "SELECT COUNT(*) FROM data_disposition_queue WHERE application_id='app-1'"
    ).fetchone()[0] == 6
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE effect_key LIKE "
        "'app-1:effect_enqueue_disposition:%'"
    ).fetchone()[0] == 6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_onboarding_terminal_nodes.py -q -k enqueue`
Expected: FAIL —— `ImportError: cannot import name 'effect_enqueue_disposition'`。

- [ ] **Step 3: Write minimal implementation**

`app/graph/onboarding_nodes.py`：模块 docstring 追加一句（本模块此后同时承载 U2 与 U3 的节点），并在文件末尾追加：

```python
# ── 以下属 onboarding-flow U3（终态与处置登记，tasks 3.1–3.3）─────────────

# 六类待处置数据。⛔ 与 app/storage/db.py 的 data_disposition_queue.category CHECK
# 逐字同源（post-hire-data-disposition spec「入职完成时登记处置事项」原话：
# 简历文件与原文、解析字段、评分与证据、面试场次与邀约文书、联系方式、Offer 文书）。
DISPOSITION_CATEGORIES = (
    "resume_file",
    "parsed_fields",
    "scores",
    "interview",
    "contact",
    "offer_letter",
)

# 六条登记各自的幂等键（effect_key = {application_id}:{node_name}:{business_key}）。
_ENQUEUE_NODE_NAME = "effect_enqueue_disposition"


def _enqueue_disposition_rows(
    conn: sqlite3.Connection, *, application_id: str, candidate_id: str
) -> int:
    """登记六类待处置事项（tasks 3.2），返回该投递的登记行数。

    - 每行：`policy_version=NULL`（待策略）、`planned_action='pending'`、
      `due_at=NULL`、`executed_at=NULL`——🔴 留存策略未签认前不执行任何删除/脱敏。
    - 幂等两道：`data_disposition_queue(application_id, category)` 唯一索引（结构）
      ＋ 每类一条 `effect_log`（`{application_id}:effect_enqueue_disposition:{category}`）。
    - **⛔ 本函数不 commit**：它既被 `effect_enqueue_disposition`（独立入口）调用，
      也被 `effect_complete_onboarding` 在**同一事务**里调用（tasks 3.1 原话
      「写 history ＋ 清单 closed ＋ 3.2 登记，同事务」）。在这里 commit 会把
      "同事务"打散成两个事务——清单关了、登记没落，或反过来。
    """
    for category in DISPOSITION_CATEGORIES:
        conn.execute(
            "INSERT OR IGNORE INTO data_disposition_queue "
            "(id, application_id, candidate_id, category, policy_version, planned_action) "
            "VALUES (?, ?, ?, ?, NULL, 'pending')",
            (str(uuid.uuid4()), application_id, candidate_id, category),
        )
        conn.execute(
            "INSERT OR IGNORE INTO effect_log "
            "(effect_key, thread_id, node_name, business_key, applied_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (f"{application_id}:{_ENQUEUE_NODE_NAME}:{category}", application_id, _ENQUEUE_NODE_NAME, category),
        )
    return conn.execute(
        "SELECT COUNT(*) FROM data_disposition_queue WHERE application_id = ?",
        (application_id,),
    ).fetchone()[0]


def effect_enqueue_disposition(
    conn: sqlite3.Connection, *, application_id: str, candidate_id: str
) -> int:
    """effect_* 节点（tasks 3.2）的独立入口：登记六类处置事项并提交。

    ⛔ 刻意**不套** `@idempotent_effect`：本单元的幂等粒度是"每类一把键"（六把，
    tasks 3.2 字面），外层再套一把单键装饰器会让粒度混淆（装饰器会往 effect_log
    写一条 `{application_id}:effect_enqueue_disposition:{business_key}`，与六把
    键并存两套语义）。幂等由上面两道兑现，重复调用返回 6、不新增行。

    生产主路径是 `effect_complete_onboarding` 内部同事务调用，本入口供补登记/
    重放使用（例如为历史"已入职"投递补登记）。
    """
    count = _enqueue_disposition_rows(
        conn, application_id=application_id, candidate_id=candidate_id
    )
    conn.commit()
    return count
```

（`uuid` / `json` 已在模块顶部 import；本 Task 不需要新增 import。）

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_onboarding_terminal_nodes.py -q -k enqueue`
Expected: 全绿（三个用例）。

---

### Task 4: `effect_complete_onboarding` 的 `hired` 分支（tasks 3.1）

**Files:**
- Modify: `app/graph/onboarding_nodes.py`（追加 `OnboardingTerminalRejected` / `terminal_history_rows` / 两个 action 常量 / `effect_complete_onboarding`；顶部 `from datetime import date`）
- Test: `tests/test_onboarding_terminal_nodes.py`（追加 Task 4 用例）

**Interfaces:**
- Consumes: `_enqueue_disposition_rows()`（Task 3）、`onboarding_checklist` / `onboarding_item` / `offer` / `application_stage_history`
- Produces:
  - `TERMINAL_ACTION_ONBOARDED = "onboarded"`、`TERMINAL_ACTION_ABANDONED = "abandoned"`、`TERMINAL_ACTIONS`、`TERMINAL_OUTCOMES = ("hired", "abandoned")`
  - `terminal_history_rows(conn, application_id) -> list[sqlite3.Row]`（只读辅助，Task 6 与节点共用）
  - `effect_complete_onboarding(conn, *, thread_id, business_key, actor_username, actual_start_date=None, note=None, reason=None, today=None) -> str`——`business_key` 即结局；返回 `application_stage_history.id`；命中幂等键返回 `None`

- [ ] **Step 1: Write the failing test**

`tests/test_onboarding_terminal_nodes.py` 顶部 import 区追加一行：

```python
from app.graph import onboarding_nodes
```

文件末尾追加：

```python
# ── Task 4：effect_complete_onboarding(confirm_hired) ──────────────────


def test_confirm_hired_writes_history_checklist_and_disposition(conn):
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)

    history_id = _confirm(conn)

    assert history_id is not None
    row = conn.execute(
        "SELECT from_stage_id, to_stage_id, actor_type, actor, action, detail_json "
        "FROM application_stage_history WHERE id = ?",
        (history_id,),
    ).fetchone()
    assert row[0] == "hired" and row[1] == "hired"      # 阶段不变：入职日确认是同一阶段上的动作
    assert row[2] == "human" and row[3] == "hr-user"    # 终态由人确认
    assert row[4] == "onboarded"
    detail = json.loads(row[5])
    assert detail["actual_start_date"] == START_DATE
    assert detail["incomplete_required_items"] == []
    assert detail["note"] == ""

    assert conn.execute(
        "SELECT status, closed_reason FROM onboarding_checklist WHERE id = ?", (checklist_id,)
    ).fetchone() == ("closed", "hired")
    assert conn.execute(
        "SELECT status, current_stage_id FROM application WHERE id='app-1'"
    ).fetchone() == ("hired", "hired")
    # 同事务登记六类（tasks 3.1「＋ 3.2 登记，同事务」）
    assert conn.execute(
        "SELECT COUNT(*) FROM data_disposition_queue WHERE application_id='app-1'"
    ).fetchone()[0] == 6
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE effect_key='app-1:effect_complete_onboarding:hired'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE effect_key LIKE 'app-1:effect_enqueue_disposition:%'"
    ).fetchone()[0] == 6


def test_confirm_hired_records_incomplete_items_and_note(conn):
    """spec「有未完成条目时确认」：不阻止确认，但未完成清单与 HR 说明必须留痕。"""
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)
    conn.execute(
        "UPDATE onboarding_item SET status='done' WHERE checklist_id=? AND required=1 "
        "AND name != '体检报告'",
        (checklist_id,),
    )
    conn.commit()

    history_id = _confirm(conn, note="体检报告下周补，人已到岗")

    detail = json.loads(
        conn.execute(
            "SELECT detail_json FROM application_stage_history WHERE id=?", (history_id,)
        ).fetchone()[0]
    )
    assert [item["name"] for item in detail["incomplete_required_items"]] == ["体检报告"]
    assert detail["note"] == "体检报告下周补，人已到岗"


def test_confirm_hired_requires_note_when_required_items_are_pending(conn):
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)   # 六个必需条目全是 pending

    with pytest.raises(OnboardingTerminalRejected):
        _confirm(conn)

    assert conn.execute("SELECT COUNT(*) FROM application_stage_history").fetchone()[0] == 0
    assert conn.execute(
        "SELECT status FROM onboarding_checklist WHERE id=?", (checklist_id,)
    ).fetchone() == ("open",)


def test_confirm_hired_rejects_before_start_date(conn):
    """spec「入职日前确认被拒」：入职日为明天、今天确认 ⇒ 拒绝且不留任何痕迹。"""
    _seed_hired_application(conn, start_date="2026-10-21")
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)

    with pytest.raises(OnboardingTerminalRejected):
        _confirm(conn)          # today = 2026-10-20

    assert conn.execute("SELECT COUNT(*) FROM application_stage_history").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM data_disposition_queue").fetchone()[0] == 0


def test_confirm_hired_rejects_without_accepted_offer(conn):
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)
    conn.execute("UPDATE offer SET status='exported' WHERE application_id='app-1'")
    conn.commit()

    with pytest.raises(OnboardingTerminalRejected):
        _confirm(conn)


def test_confirm_hired_rejects_without_checklist(conn):
    _seed_hired_application(conn)

    with pytest.raises(OnboardingTerminalRejected):
        _confirm(conn)


def test_confirm_hired_rejects_when_terminal_fact_already_exists(conn):
    """终态事实已存在 ⇒ 拒绝。

    ⚠️ 这一条**不能**用"先 `_confirm` 一次再 `_confirm`"来写：同一把幂等键的第二次调用
    由 `@idempotent_effect` 直接短路返回 None（那是幂等命中，不是拒绝）。要验
    "已有终态事实即拒"这个前置，得让事实存在**而 effect_log 里没有对应的键**——
    也就是被人绕过节点手工写过、或键丢了的情形（正是这条前置要兜的洞）。
    """
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)
    conn.execute(
        "INSERT INTO application_stage_history "
        "(id, application_id, from_stage_id, to_stage_id, actor_type, actor, action) "
        "VALUES ('h-manual', 'app-1', 'hired', 'hired', 'human', 'hr-user', 'onboarded')"
    )
    conn.commit()

    with pytest.raises(OnboardingTerminalRejected):
        _confirm(conn)


def test_confirm_hired_rerun_is_idempotent(conn):
    """重跑必须返回 None、不产生第二条终态事实、不重复登记（spec「终态节点重跑」）。"""
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)
    first = _confirm(conn)

    second = _confirm(conn)

    assert first is not None and second is None
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE action='onboarded'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM data_disposition_queue WHERE application_id='app-1'"
    ).fetchone()[0] == 6


def test_confirm_hired_requires_actor(conn):
    """终态由人确认：没有可识别操作人就拒绝（⛔ 不给默认值）。"""
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)

    with pytest.raises(OnboardingTerminalRejected):
        effect_complete_onboarding(
            conn, thread_id="app-1", business_key="hired", actor_username="  ", today=TODAY
        )


def test_complete_onboarding_rejects_unknown_outcome(conn):
    """business_key 只认两个结局——别的取值一律拒（免得写出第三条终态语义）。"""
    _seed_hired_application(conn)

    with pytest.raises(OnboardingTerminalRejected):
        effect_complete_onboarding(
            conn, thread_id="app-1", business_key="ghost", actor_username="hr-user", today=TODAY
        )

    assert conn.execute("SELECT COUNT(*) FROM application_stage_history").fetchone()[0] == 0


def test_confirm_hired_rolls_back_everything_when_registration_fails(conn, monkeypatch):
    """工程铁律 1「同事务」的反证：登记失败 ⇒ history / 清单关闭 / effect_log 全回滚。"""
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)

    def _boom(*args, **kwargs):
        raise RuntimeError("模拟登记失败")

    monkeypatch.setattr(onboarding_nodes, "_enqueue_disposition_rows", _boom)
    with pytest.raises(RuntimeError):
        _confirm(conn)

    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE action='onboarded'"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT status FROM onboarding_checklist WHERE id=?", (checklist_id,)
    ).fetchone() == ("open",)
    assert conn.execute("SELECT COUNT(*) FROM data_disposition_queue").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE effect_key='app-1:effect_complete_onboarding:hired'"
    ).fetchone()[0] == 0


def test_terminal_history_rows_only_counts_terminal_actions(conn):
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)
    assert terminal_history_rows(conn, "app-1") == []

    _confirm(conn)

    actions = [row[1] for row in terminal_history_rows(conn, "app-1")]
    assert actions == ["onboarded"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_onboarding_terminal_nodes.py -q -k confirm`
Expected: FAIL —— `ImportError: cannot import name 'effect_complete_onboarding'`。

- [ ] **Step 3: Write minimal implementation**

`app/graph/onboarding_nodes.py`：顶部加 `from datetime import date`（放在 `from __future__ import annotations` 之后、`import json` 之前），并在 Task 3 追加的段落末尾继续追加：

```python
TERMINAL_OUTCOMES = ("hired",)   # Task 5 扩为 ("hired", "abandoned")
TERMINAL_ACTION_ONBOARDED = "onboarded"
TERMINAL_ACTION_ABANDONED = "abandoned"
TERMINAL_ACTIONS = (TERMINAL_ACTION_ONBOARDED, TERMINAL_ACTION_ABANDONED)


class OnboardingTerminalRejected(Exception):
    """终态动作不满足前置：入职日未到 / 有未完成必需条目且未填说明 / 已有终态 /
    缺清单 / Offer 未确认接受 / 缺可识别操作人 / 未知结局。"""


def terminal_history_rows(conn: sqlite3.Connection, application_id: str) -> list[tuple]:
    """该投递的终态流转事实（只读辅助，⛔ 不带 effect_ 前缀——它不是节点）。

    「终态事实」的判据是 action ∈ {onboarded, abandoned}（Task 2 把这两个值
    加进了 CHECK）。⛔ 不按 `to_stage_id='hired'` 判：投靠在 Offer 接受时
    就已经在 hired 阶段，那个阶段的流转行不是终态事实。
    """
    return conn.execute(
        "SELECT id, action, actor_type, actor, occurred_at FROM application_stage_history "
        "WHERE application_id = ? AND action IN (?, ?) ORDER BY occurred_at, id",
        (application_id, TERMINAL_ACTION_ONBOARDED, TERMINAL_ACTION_ABANDONED),
    ).fetchall()


@idempotent_effect("effect_complete_onboarding")
def effect_complete_onboarding(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    actor_username: str,
    actual_start_date: str | None = None,
    note: str | None = None,
    reason: str | None = None,
    today: str | None = None,
) -> str:
    """effect_* 节点：入职终态（tasks 3.1 / 3.3），独占、幂等。

    `business_key` 就是结局（`hired` / `abandoned`）——幂等键因此是
    `{application_id}:effect_complete_onboarding:{hired|abandoned}`，与
    tasks 3.1/3.3 的字面逐字一致。两个结局同属一个函数：它们是同一件事的
    两个互斥出口，共用"该投递不得已有终态事实"这一条前置。

    - `hired`：写流转事实（`action='onboarded'`、`to_stage_id='hired'`、
      `actor_type='human'`）＋ 清单 `closed(hired)` ＋ **同事务**登记六类处置
      （`_enqueue_disposition_rows`，⛔ 它不 commit，装饰器统一 commit）。
      需要清单存在且 `open`；未完成的必需条目**不阻止**确认，但必须填 `note`，
      未完成清单与说明一起写进 `detail_json` 留痕（spec「有未完成条目时确认」）。
    - `abandoned` 分支由 Task 5 落地（`TERMINAL_OUTCOMES` 此刻只含 `hired`）。

    ⛔ 无自动终态路径：`actor_username` 没有默认值，空操作人直接拒绝——没有
    可识别的人，这个节点在结构上就调不动（design D3「终态由人确认」）。
    """
    application_id = thread_id
    outcome = business_key
    if outcome not in TERMINAL_OUTCOMES:
        raise OnboardingTerminalRejected(f"未知终态结局: {outcome!r}")
    if not actor_username or not actor_username.strip():
        raise OnboardingTerminalRejected("终态必须由可识别的人确认（actor_username 为空）")

    row = conn.execute(
        "SELECT a.status, a.current_stage_id, a.candidate_id, "
        "       c.id, c.status, o.status, o.start_date "
        "FROM application a "
        "LEFT JOIN onboarding_checklist c ON c.application_id = a.id "
        "LEFT JOIN offer o ON o.application_id = a.id "
        "WHERE a.id = ?",
        (application_id,),
    ).fetchone()
    if row is None:
        raise OnboardingTerminalRejected("投递不存在")
    (
        _app_status, current_stage, candidate_id,
        checklist_id, checklist_status, offer_status, offer_start_date,
    ) = row

    if terminal_history_rows(conn, application_id):
        raise OnboardingTerminalRejected("该投递已有终态事实，不能再次写终态")
    if offer_status != "accepted":
        raise OnboardingTerminalRejected("Offer 尚未确认接受，不能登记入职终态")

    if outcome == "hired":
        if checklist_id is None:
            raise OnboardingTerminalRejected("该投递尚无入职清单，不能确认已入职")
        if checklist_status != "open":
            raise OnboardingTerminalRejected(
                f"清单状态为 {checklist_status}，不能确认已入职"
            )
        effective_start_date = actual_start_date or offer_start_date
        if not effective_start_date:
            raise OnboardingTerminalRejected("缺少实际入职日")
        today_str = today or date.today().isoformat()
        if effective_start_date > today_str:
            raise OnboardingTerminalRejected(
                f"入职日 {effective_start_date} 尚未到达（今天 {today_str}），不能确认已入职"
            )
        incomplete = conn.execute(
            "SELECT name, owner_party FROM onboarding_item "
            "WHERE checklist_id = ? AND required = 1 AND status = 'pending' ORDER BY rowid",
            (checklist_id,),
        ).fetchall()
        if incomplete and not (note and note.strip()):
            raise OnboardingTerminalRejected(
                "仍有未完成的必需条目，必须填写说明后才能确认已入职（未完成："
                + "、".join(item[0] for item in incomplete)
                + "）"
            )
        detail = {
            "outcome": TERMINAL_ACTION_ONBOARDED,
            "actual_start_date": effective_start_date,
            "offer_start_date": offer_start_date,
            "note": note or "",
            "incomplete_required_items": [
                {"name": item[0], "owner_party": item[1]} for item in incomplete
            ],
        }
        history_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO application_stage_history "
            "(id, application_id, from_stage_id, to_stage_id, actor_type, actor, action, detail_json) "
            "VALUES (?, ?, ?, 'hired', 'human', ?, ?, ?)",
            (
                history_id, application_id, current_stage, actor_username,
                TERMINAL_ACTION_ONBOARDED, json.dumps(detail, ensure_ascii=False),
            ),
        )
        conn.execute(
            "UPDATE application SET status = 'hired', current_stage_id = 'hired' WHERE id = ?",
            (application_id,),
        )
        conn.execute(
            "UPDATE onboarding_checklist SET status = 'closed', closed_reason = 'hired' WHERE id = ?",
            (checklist_id,),
        )
        _enqueue_disposition_rows(
            conn, application_id=application_id, candidate_id=candidate_id
        )
        return history_id

    # Task 4 只落 hired：`abandoned` 由 Task 5 在这之前插入分支体。
    raise OnboardingTerminalRejected(f"未知终态结局: {outcome!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_onboarding_terminal_nodes.py -q -k "confirm or terminal_history"`
Expected: 全绿（12 个用例；`abandon` 用例属 Task 5，此处尚未存在）。

---

### Task 5: `effect_complete_onboarding` 的 `abandoned` 分支（tasks 3.3）

**Files:**
- Modify: `app/graph/onboarding_nodes.py`（`TERMINAL_OUTCOMES` 扩为 `("hired", "abandoned")`；在兜底 `raise` 之前插入 `abandoned` 分支体）
- Test: `tests/test_onboarding_terminal_nodes.py`（追加 Task 5 用例）

**Interfaces:**
- Consumes: Task 4 的同一个节点函数（签名不变）
- Produces: `business_key="abandoned"` 的完整语义——`application.status='refused'`、`offer.status='abandoned'`、一条 `action='abandoned'` 的流转事实、清单 `closed(abandoned)`（若已生成）；⛔ `rejection_record` 一行不写

- [ ] **Step 1: Write the failing test**

`tests/test_onboarding_terminal_nodes.py` 末尾追加：

```python
# ── Task 5：effect_complete_onboarding(abandon) ────────────────────────


def test_abandon_writes_terminal_facts(conn):
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)

    history_id = _abandon(conn, reason="接受其他公司")

    assert history_id is not None
    row = conn.execute(
        "SELECT from_stage_id, to_stage_id, actor_type, actor, action, detail_json "
        "FROM application_stage_history WHERE id = ?",
        (history_id,),
    ).fetchone()
    # 阶段不变（⛔ 不写成流转到 'rejected'：自愿退出不是我方淘汰）
    assert row[0] == "hired" and row[1] == "hired"
    assert row[2] == "human" and row[3] == "hr-user"
    assert row[4] == "abandoned"
    assert json.loads(row[5])["reason"] == "接受其他公司"

    assert conn.execute(
        "SELECT status FROM application WHERE id='app-1'"
    ).fetchone() == ("refused",)
    assert conn.execute(
        "SELECT status, updated_by FROM offer WHERE application_id='app-1'"
    ).fetchone() == ("abandoned", "hr-user")
    assert conn.execute(
        "SELECT status, closed_reason FROM onboarding_checklist WHERE id = ?", (checklist_id,)
    ).fetchone() == ("closed", "abandoned")


def test_abandon_writes_no_rejection_record(conn):
    """合规红线：候选人自愿退出不是我方淘汰 ⇒ 拒绝记录表行数不变。"""
    _seed_hired_application(conn)
    _instantiated(conn)
    before = conn.execute("SELECT COUNT(*) FROM rejection_record").fetchone()[0]

    _abandon(conn, reason="接受其他公司")

    assert conn.execute("SELECT COUNT(*) FROM rejection_record").fetchone()[0] == before == 0


def test_abandon_registers_no_disposition(conn):
    """登记只挂在"已入职"上（spec：入职完成时登记处置事项）——放弃不登记。"""
    _seed_hired_application(conn)
    _instantiated(conn)

    _abandon(conn, reason="接受其他公司")

    assert conn.execute(
        "SELECT COUNT(*) FROM data_disposition_queue WHERE application_id='app-1'"
    ).fetchone()[0] == 0


def test_abandon_requires_reason(conn):
    _seed_hired_application(conn)
    _instantiated(conn)

    with pytest.raises(OnboardingTerminalRejected):
        _abandon(conn, reason="   ")

    assert conn.execute("SELECT COUNT(*) FROM application_stage_history").fetchone()[0] == 0
    assert conn.execute("SELECT status FROM application WHERE id='app-1'").fetchone() == ("hired",)


def test_abandon_after_hired_confirmation_is_rejected(conn):
    """spec「已入职后再标放弃」：拒绝。"""
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)
    _confirm(conn)

    with pytest.raises(OnboardingTerminalRejected):
        _abandon(conn, reason="反悔")

    assert conn.execute("SELECT status FROM application WHERE id='app-1'").fetchone() == ("hired",)
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE action IN ('onboarded','abandoned')"
    ).fetchone()[0] == 1


def test_abandon_without_checklist_is_allowed(conn):
    """清单还没生成人就不来了——这是现实里最常见的时点，⛔ 不能因为"没有清单
    可关闭"就拒绝登记（spec 放弃入职 Requirement 是 SHALL 允许）。"""
    _seed_hired_application(conn)

    history_id = _abandon(conn, reason="入职前一周告知不来了")

    assert history_id is not None
    assert conn.execute("SELECT status FROM application WHERE id='app-1'").fetchone() == ("refused",)
    assert conn.execute("SELECT COUNT(*) FROM onboarding_checklist").fetchone()[0] == 0


def test_abandon_rejects_unsigned_offer(conn):
    _seed_hired_application(conn)
    _instantiated(conn)
    conn.execute("UPDATE offer SET status='exported' WHERE application_id='app-1'")
    conn.commit()

    with pytest.raises(OnboardingTerminalRejected):
        _abandon(conn, reason="不来了")


def test_abandon_rerun_is_idempotent(conn):
    _seed_hired_application(conn)
    _instantiated(conn)
    first = _abandon(conn, reason="接受其他公司")

    second = _abandon(conn, reason="接受其他公司")

    assert first is not None and second is None
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE action='abandoned'"
    ).fetchone()[0] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_onboarding_terminal_nodes.py -q -k abandon`
Expected: FAIL —— `OnboardingTerminalRejected: 未知终态结局: 'abandoned'`。

- [ ] **Step 3: Write minimal implementation**

`app/graph/onboarding_nodes.py` 两处改动：

① 常量扩到两个结局：

```python
TERMINAL_OUTCOMES = ("hired", "abandoned")
```

② 把 Task 4 末尾那句兜底 `raise OnboardingTerminalRejected(f"未知终态结局: {outcome!r}")` **整句替换**成下面这段：

```python
    # outcome == "abandoned"：候选人自愿退出，⛔ 不是我方淘汰（合规红线）
    if not reason or not reason.strip():
        raise OnboardingTerminalRejected("放弃入职必须填写原因")
    detail = {
        "outcome": TERMINAL_ACTION_ABANDONED,
        "reason": reason,
        "offer_status_before": offer_status,
    }
    history_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO application_stage_history "
        "(id, application_id, from_stage_id, to_stage_id, actor_type, actor, action, detail_json) "
        "VALUES (?, ?, ?, ?, 'human', ?, ?, ?)",
        (
            history_id, application_id, current_stage, current_stage, actor_username,
            TERMINAL_ACTION_ABANDONED, json.dumps(detail, ensure_ascii=False),
        ),
    )
    conn.execute(
        "UPDATE application SET status = 'refused' WHERE id = ?", (application_id,)
    )
    conn.execute(
        "UPDATE offer SET status = 'abandoned', updated_by = ?, updated_at = datetime('now') "
        "WHERE application_id = ?",
        (actor_username, application_id),
    )
    if checklist_id is not None:
        conn.execute(
            "UPDATE onboarding_checklist SET status = 'closed', closed_reason = 'abandoned' "
            "WHERE id = ?",
            (checklist_id,),
        )
    return history_id
```

⚠️ 替换后函数在 `return history_id` 结束，**不留任何死代码**：未知取值已由顶部的 `TERMINAL_OUTCOMES` 校验拦住（`test_complete_onboarding_rejects_unknown_outcome`，Task 4），`if outcome == "hired"` 之外的取值只可能是 `abandoned`。

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_onboarding_terminal_nodes.py -q`
Expected: 全绿（Task 3/4/5 全部用例）。

---

### Task 6: 终态不变式 ＋ ⛔ 无自动终态路径（tasks 3.4）

**Files:**
- Test: `tests/test_onboarding_terminal_invariant.py`（新建）

**Interfaces:**
- Consumes: `terminal_history_rows` / `effect_complete_onboarding` / `effect_enqueue_disposition`（Task 3–5）
- Produces: 一组"任何驱动顺序下都成立"的不变式断言；后续 U4/U5 改动若破坏它们，本文件先红

- [ ] **Step 1: Write the failing test**

新建 `tests/test_onboarding_terminal_invariant.py`：

```python
"""onboarding-flow U3 tasks 3.4：终态不变式 ＋ ⛔ 无自动终态路径的反证。

本文件与 tests/test_onboarding_terminal_nodes.py 的分工：那份测"每个动作对不对"，
这份测"**任何顺序**驱动下都成立的那几条恒等式"——包括失败步骤（被拒的 abandon /
被拒的 confirm）夹在中间时。顺序是穷举的（见 SEQUENCES），每次驱动后复查全部不变式。
"""
import inspect
from pathlib import Path

import pytest

from app.graph import onboarding_nodes
from app.graph.onboarding_nodes import (
    OnboardingTerminalRejected,
    effect_complete_onboarding,
    effect_enqueue_disposition,
    effect_instantiate_checklist,
    terminal_history_rows,
)
from app.storage.db import get_connection, init_schema

TODAY = "2026-10-20"
START_DATE = "2026-10-20"
ROOT = Path(__file__).resolve().parents[1]

# 终态动作 → （application.status 必须是什么，offer.status 必须是什么）
_TERMINAL_CONSISTENCY = {
    "onboarded": ("hired", "accepted"),
    "abandoned": ("refused", "abandoned"),
}


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "onboarding.db"))
    init_schema(c)
    return c


def _seed_hired_application(conn, application_id="app-1", start_date=START_DATE):
    conn.execute(
        "INSERT INTO job (id, title, department, status) "
        "VALUES ('j1', '嵌入式工程师', '研发部', 'approved')"
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
        "INSERT INTO offer (id, application_id, job_id, department, start_date, report_to, "
        "status, approval_round, created_by) "
        "VALUES (?, ?, 'j1', '研发部', ?, 'manager-1', 'accepted', 1, 'hr')",
        (f"offer-{application_id}", application_id, start_date),
    )
    conn.commit()


def _instantiated(conn, application_id="app-1"):
    effect_instantiate_checklist(
        conn, thread_id=application_id, business_key="instantiate", created_by="hr-user"
    )
    return conn.execute(
        "SELECT id FROM onboarding_checklist WHERE application_id = ?", (application_id,)
    ).fetchone()[0]


def _finish_all_required(conn, checklist_id):
    conn.execute(
        "UPDATE onboarding_item SET status='done' WHERE checklist_id=? AND required=1",
        (checklist_id,),
    )
    conn.commit()


def _confirm(conn, note=None, application_id="app-1"):
    return effect_complete_onboarding(
        conn, thread_id=application_id, business_key="hired",
        actor_username="hr-user", note=note, today=TODAY,
    )


def _abandon(conn, application_id="app-1"):
    return effect_complete_onboarding(
        conn, thread_id=application_id, business_key="abandoned",
        actor_username="hr-user", reason="接受其他公司",
    )


def _assert_invariants(conn, application_id="app-1"):
    """任何一步之后都必须成立的恒等式。"""
    terminal = terminal_history_rows(conn, application_id)
    app_status = conn.execute(
        "SELECT status FROM application WHERE id = ?", (application_id,)
    ).fetchone()[0]
    offer_status = conn.execute(
        "SELECT status FROM offer WHERE application_id = ?", (application_id,)
    ).fetchone()[0]
    checklist = conn.execute(
        "SELECT id, status FROM onboarding_checklist WHERE application_id = ?",
        (application_id,),
    ).fetchone()
    queue_count = conn.execute(
        "SELECT COUNT(*) FROM data_disposition_queue WHERE application_id = ?",
        (application_id,),
    ).fetchone()[0]

    # ① 终态唯一：至多一条终态事实
    assert len(terminal) <= 1, f"同一投递出现 {len(terminal)} 条终态事实"
    # ② 放弃入职不是我方淘汰：refused 的投递不得有拒绝记录
    assert conn.execute(
        "SELECT COUNT(*) FROM rejection_record r JOIN application a ON a.id = r.application_id "
        "WHERE a.status = 'refused'"
    ).fetchone()[0] == 0

    if not terminal:
        # 没有终态事实 ⇒ 没有登记、清单不会因为终态而关闭
        assert queue_count == 0
        assert app_status != "refused"
        return

    action = terminal[0][1]
    expected_app, expected_offer = _TERMINAL_CONSISTENCY[action]
    # ③ 终态事实与 application.status / offer.status 一致
    assert app_status == expected_app, f"{action} 后 application.status={app_status}"
    assert offer_status == expected_offer, f"{action} 后 offer.status={offer_status}"
    # ④ 有清单就必须已关闭
    if checklist is not None:
        assert checklist[1] == "closed"
    # ⑤ 登记只挂"已入职"，且是六类
    assert queue_count == (6 if action == "onboarded" else 0)
    # ⑥ 反向：refused ⇔ 恰一条 abandoned
    if app_status == "refused":
        assert [row[1] for row in terminal] == ["abandoned"]


_STEPS = {
    "confirm": lambda conn: _confirm(conn),
    "confirm_with_note": lambda conn: _confirm(conn, note="体检报告后补"),
    "abandon": lambda conn: _abandon(conn),
}

# 穷举驱动顺序（含重跑与被拒步骤）。被拒步骤由驱动循环捕获，不变式仍要成立。
SEQUENCES = {
    "confirm": ("confirm",),
    "confirm_rerun": ("confirm", "confirm"),
    "confirm_then_abandon": ("confirm", "abandon"),
    "confirm_with_note": ("confirm_with_note",),
    "confirm_with_note_then_abandon": ("confirm_with_note", "abandon"),
    "abandon": ("abandon",),
    "abandon_rerun": ("abandon", "abandon"),
    "abandon_then_confirm": ("abandon", "confirm"),
}


@pytest.mark.parametrize("name", sorted(SEQUENCES))
def test_terminal_invariants_hold_across_every_sequence(conn, name):
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)

    for step in SEQUENCES[name]:
        try:
            _STEPS[step](conn)
        except OnboardingTerminalRejected:
            pass
        _assert_invariants(conn)


def test_invariants_hold_when_abandoning_without_checklist(conn):
    _seed_hired_application(conn)

    _abandon(conn)

    _assert_invariants(conn)


def test_invariants_hold_when_confirm_is_rejected_before_start_date(conn):
    _seed_hired_application(conn, start_date="2099-01-01")
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)

    with pytest.raises(OnboardingTerminalRejected):
        _confirm(conn)

    _assert_invariants(conn)


def test_enqueue_never_changes_terminal_facts(conn):
    _seed_hired_application(conn)
    checklist_id = _instantiated(conn)
    _finish_all_required(conn, checklist_id)
    _confirm(conn)

    effect_enqueue_disposition(conn, application_id="app-1", candidate_id="cand-1")

    _assert_invariants(conn)
    assert conn.execute("SELECT COUNT(*) FROM data_disposition_queue").fetchone()[0] == 6


# ── ⛔ 无自动终态路径（grep 与签名反证）──────────────────────────────────

_AUTO_TRIGGER_MARKERS = (
    "apscheduler",
    "BackgroundTasks",
    "add_job",
    "schedule.every",
    "threading.Timer",
    "asyncio.create_task",
)


def _app_sources() -> dict:
    return {
        path: path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "app").rglob("*.py"))
    }


def test_only_the_terminal_node_writes_the_onboarded_fact():
    """'onboarded' 这个字面量在 app/ 下只允许出现在两处：节点自己写它、
    db.py 的 CHECK 认它。别处出现＝有人绕开节点在别的地方写终态事实。"""
    hits = {
        path.relative_to(ROOT)
        for path, text in _app_sources().items()
        if "onboarded" in text
    }
    assert hits == {
        Path("app/graph/onboarding_nodes.py"),
        Path("app/storage/db.py"),
    }, f"终态事实的字面量出现在预期外的文件: {sorted(str(p) for p in hits)}"


def test_terminal_calls_are_limited_to_the_hr_surface():
    """终态节点只允许被 HR 工作面调用：app/ 下的调用文件必须是节点模块本身，
    或（U3 Task 7 之后的）Web 层。⛔ 不许第三个文件出现。"""
    callers = {
        path.relative_to(ROOT)
        for path, text in _app_sources().items()
        if "effect_complete_onboarding(" in text
    }
    assert callers <= {
        Path("app/graph/onboarding_nodes.py"),
        Path("app/web/server.py"),
    }, f"终态节点被预期外的文件调用: {sorted(str(p) for p in callers)}"


def test_no_scheduling_construct_in_terminal_callers():
    """⛔ 无自动终态路径：调用终态节点的文件里不得出现调度/后台任务构造——
    终态只能由 HR 的一次点击触发（design D3）。"""
    for path, text in _app_sources().items():
        if "effect_complete_onboarding(" not in text:
            continue
        for marker in _AUTO_TRIGGER_MARKERS:
            assert marker not in text, f"{path} 出现调度构造 {marker}"


def test_terminal_node_requires_a_human_actor():
    """结构判据：没有可识别的人就调不动——`actor_username` 是必填关键字参数。"""
    parameters = inspect.signature(onboarding_nodes.effect_complete_onboarding).parameters
    assert "actor_username" in parameters
    assert parameters["actor_username"].default is inspect.Parameter.empty
    assert parameters["actor_username"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["business_key"].default is inspect.Parameter.empty
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_onboarding_terminal_invariant.py -q`
Expected: **应当全绿**（15 个用例）——本 Task 的产物是"把不变式钉住"的测试，它守护的行为
在 Task 4/5 已实现。若此时有红，说明 Task 4/5 的实现漏了不变式，先修实现再继续。

- [ ] **Step 3: 反证本文件不是恒真（临时改坏 → 必须变红 → 改回）**

为确认本文件不是恒真：把 `app/graph/onboarding_nodes.py` 里 abandon 分支的
`UPDATE application SET status = 'refused'` 临时改成 `'hired'`，跑
`./venv/bin/python -m pytest tests/test_onboarding_terminal_invariant.py -q`，
Expected: FAIL（`③ … application.status=hired`）；确认后改回。

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_onboarding_terminal_invariant.py -q`
Expected: 全绿（15 个用例：8 条顺序 + 3 条补充 + 4 条反证）。

---

### Task 7: 终态确认 UI（tasks 3.5）

**Files:**
- Modify: `app/web/server.py`（`from datetime import date`；两个请求模型；`_terminal_state_payload()`；三个路由；import 两个终态符号）
- Modify: `app/web/static/onboarding_checklist.html`（末尾追加终态卡片 + 自包含脚本）
- Test: `tests/test_onboarding_terminal_pages.py`（新建）

**Interfaces:**
- Consumes: `effect_complete_onboarding` / `terminal_history_rows` / `OnboardingTerminalRejected`（Task 4/5）；`_require_role(request, "hr")`（U1 已落）
- Produces:
  - `GET /api/applications/{application_id}/onboarding/terminal`（HR）→ 终态状态字典
  - `POST /api/applications/{application_id}/onboarding/confirm-hired`（HR）→ 200 ＋ 状态字典；前置不满足 422
  - `POST /api/applications/{application_id}/onboarding/abandon`（HR）→ 200 ＋ 状态字典；前置不满足 422

**前置检查（U2 Task 5 未落地时本 Task 无法开工）**

```bash
test -f app/web/static/onboarding_checklist.html && grep -q 'id="checklist-card"' app/web/static/onboarding_checklist.html
```

不满足 ⇒ 按文末「留步登记」处置：登记 `⏸ 留步：U2 Task 5（清单页）未落地`，**本 Task 停下**；
⛔ 不要另建一份清单页副本，⛔ 其余 Task 不受影响（Task 1–6/8 与本 Task 无依赖）。

- [ ] **Step 1: Write the failing test**

新建 `tests/test_onboarding_terminal_pages.py`：

```python
"""onboarding-flow U3 终态确认 UI（tasks 3.5）：三个 HR 端点 + 清单页终态卡片。

夹具只依赖 U1（建表/接口基座）与 U2 的**节点**（已落 main），⛔ 不依赖 U2 的
HTTP 端点——真实执行顺序上 U2 Task 5 可能晚于本 Task 落地，测试不该因此红。
"""
from pathlib import Path

import pytest

from app.graph.onboarding_nodes import effect_instantiate_checklist
from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account

STATIC = Path("app/web/static")
# ⚠️ 必须是一个**永远早于运行日期**的日子：端点的"入职日已到"用服务端真实时钟
# （date.today()），写死一个 2026 年的日子会让本文件在 2026 年之前全红、之后又能过。
PAST_START_DATE = "2020-01-01"


def _login(client, conn, username, role, department=None):
    account_id = upsert_account(conn, username=username, password="s3cret!")
    conn.execute(
        "UPDATE hr_account SET role=?, department=? WHERE id=?", (role, department, account_id)
    )
    conn.commit()
    client.cookies.set("hr_session", create_session(conn, hr_account_id=account_id))
    return account_id


def _seed_hired(conn, application_id="app-1", start_date=PAST_START_DATE):
    conn.execute(
        "INSERT INTO job (id, title, department, status) "
        "VALUES ('j1', '嵌入式工程师', '研发部', 'approved')"
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
        "INSERT INTO offer (id, application_id, job_id, department, start_date, report_to, "
        "status, approval_round, created_by) "
        "VALUES (?, ?, 'j1', '研发部', ?, 'manager-1', 'accepted', 1, 'hr')",
        (f"offer-{application_id}", application_id, start_date),
    )
    conn.commit()
    effect_instantiate_checklist(
        conn, thread_id=application_id, business_key="instantiate", created_by="hr-user"
    )


def _finish_all_required(conn, application_id="app-1"):
    conn.execute(
        "UPDATE onboarding_item SET status='done' WHERE required=1 AND checklist_id="
        "(SELECT id FROM onboarding_checklist WHERE application_id=?)",
        (application_id,),
    )
    conn.commit()


# ── 端点：访问控制 ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/applications/app-1/onboarding/terminal"),
        ("post", "/api/applications/app-1/onboarding/confirm-hired"),
        ("post", "/api/applications/app-1/onboarding/abandon"),
    ],
)
def test_terminal_endpoints_require_hr(make_test_client, method, path):
    client, conn = make_test_client()
    _seed_hired(conn)

    call = getattr(client, method)
    payload = {"json": {}} if method == "post" else {}
    assert call(path, **payload).status_code == 401

    _login(client, conn, "iv", "interviewer")
    assert call(path, **payload).status_code == 403


def test_terminal_state_reports_pending_confirmation(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn)
    _login(client, conn, "hr", "hr")

    body = client.get("/api/applications/app-1/onboarding/terminal").json()

    assert body["checklist_status"] == "open"
    assert body["terminal_outcome"] is None
    assert body["pending_confirmation"] is True
    assert body["can_confirm_hired"] is True
    assert body["can_abandon"] is True


# ── 端点：确认已入职 ───────────────────────────────────────────────────


def test_confirm_hired_endpoint_writes_terminal_state(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn)
    _finish_all_required(conn)
    _login(client, conn, "hr", "hr")

    resp = client.post(
        "/api/applications/app-1/onboarding/confirm-hired",
        json={"actual_start_date": PAST_START_DATE, "note": None},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["terminal_outcome"] == "onboarded"
    assert body["application_status"] == "hired"
    assert body["checklist_status"] == "closed"
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE action='onboarded'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM data_disposition_queue WHERE application_id='app-1'"
    ).fetchone()[0] == 6


def test_confirm_hired_endpoint_is_idempotent(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn)
    _finish_all_required(conn)
    _login(client, conn, "hr", "hr")

    first = client.post("/api/applications/app-1/onboarding/confirm-hired", json={})
    second = client.post("/api/applications/app-1/onboarding/confirm-hired", json={})

    assert first.status_code == 200 and second.status_code == 200
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE action='onboarded'"
    ).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM data_disposition_queue").fetchone()[0] == 6


def test_confirm_hired_endpoint_rejects_before_start_date(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn, start_date="2099-01-01")
    _finish_all_required(conn)
    _login(client, conn, "hr", "hr")

    resp = client.post("/api/applications/app-1/onboarding/confirm-hired", json={})

    assert resp.status_code == 422
    assert conn.execute("SELECT COUNT(*) FROM application_stage_history").fetchone()[0] == 0


def test_confirm_hired_endpoint_requires_note_when_items_pending(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn)          # 必需条目全 pending
    _login(client, conn, "hr", "hr")

    assert client.post(
        "/api/applications/app-1/onboarding/confirm-hired", json={}
    ).status_code == 422
    assert client.post(
        "/api/applications/app-1/onboarding/confirm-hired",
        json={"note": "体检报告后补"},
    ).status_code == 200


# ── 端点：放弃入职 ─────────────────────────────────────────────────────


def test_abandon_endpoint_writes_refused_and_no_rejection_record(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn)
    _login(client, conn, "hr", "hr")

    resp = client.post(
        "/api/applications/app-1/onboarding/abandon", json={"reason": "接受其他公司"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["application_status"] == "refused"
    assert body["offer_status"] == "abandoned"
    assert body["terminal_outcome"] == "abandoned"
    assert conn.execute("SELECT COUNT(*) FROM rejection_record").fetchone()[0] == 0


def test_abandon_endpoint_requires_reason(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn)
    _login(client, conn, "hr", "hr")

    resp = client.post("/api/applications/app-1/onboarding/abandon", json={"reason": "  "})

    assert resp.status_code == 422
    assert conn.execute("SELECT status FROM application WHERE id='app-1'").fetchone() == ("hired",)


def test_abandon_endpoint_rejected_after_confirm(make_test_client):
    client, conn = make_test_client()
    _seed_hired(conn)
    _finish_all_required(conn)
    _login(client, conn, "hr", "hr")
    client.post("/api/applications/app-1/onboarding/confirm-hired", json={})

    resp = client.post(
        "/api/applications/app-1/onboarding/abandon", json={"reason": "反悔"}
    )

    assert resp.status_code == 422
    assert conn.execute("SELECT status FROM application WHERE id='app-1'").fetchone() == ("hired",)


# ── 页面片段 ───────────────────────────────────────────────────────────


def test_terminal_block_is_present_in_checklist_page():
    html = (STATIC / "onboarding_checklist.html").read_text(encoding="utf-8")
    assert 'id="terminal-card"' in html
    assert 'id="confirm-hired-btn"' in html
    assert 'id="abandon-btn"' in html
    assert 'id="actual-start-date"' in html
    assert "onboarding/confirm-hired" in html
    assert "onboarding/abandon" in html
    # 本包合规约束：终态卡片也不接受文件
    terminal_block = html.split('id="terminal-card"', 1)[1]
    assert 'type="file"' not in terminal_block
    assert "multipart/form-data" not in terminal_block
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_onboarding_terminal_pages.py -q`
Expected: FAIL —— 端点 404（路由未注册）、`onboarding_checklist.html` 里没有终态卡片。

- [ ] **Step 3: Write minimal implementation**

① `app/web/server.py` 顶部 import 区加 `from datetime import date`（放在 `from contextlib import asynccontextmanager` 之前；U2 Task 5 若已加则本条空转，⛔ 不重复加），并在 `from app.graph.invite_nodes import (...)` 块之后追加：

```python
from app.graph.onboarding_nodes import (
    OnboardingTerminalRejected,
    effect_complete_onboarding,
    terminal_history_rows,
)
```

② 模块级请求模型区（`OnboardingTemplateUpdateRequest` 之后）追加：

```python
class OnboardingConfirmHiredRequest(BaseModel):
    """终态确认的请求体。⛔ 无 `request_id`：幂等键由结局决定
    （`{application_id}:effect_complete_onboarding:hired`），同一投递只允许一个终态，
    与 U2 条目更新（同一动作可合法重复、需要 request_id 区分）不是一回事。"""

    actual_start_date: str | None = None
    note: str | None = None


class OnboardingAbandonRequest(BaseModel):
    reason: str
```

③ 在 `create_app` 内、U1 的模板两条路由之后追加辅助函数与三条路由（⛔ 不依赖 U2 的清单端点是否已落地——本 Task 的三个路由自成一组）：

```python
    def _terminal_state_payload(application_id: str) -> dict:
        """终态状态（清单页用它渲染"待确认"与两个按钮的可用性）。只读。"""
        app_row = conn.execute(
            "SELECT status FROM application WHERE id = ?", (application_id,)
        ).fetchone()
        if app_row is None:
            raise HTTPException(status_code=404, detail="投递不存在")
        offer_row = conn.execute(
            "SELECT status, start_date FROM offer WHERE application_id = ?",
            (application_id,),
        ).fetchone()
        checklist_row = conn.execute(
            "SELECT id, status, closed_reason, start_date FROM onboarding_checklist "
            "WHERE application_id = ?",
            (application_id,),
        ).fetchone()
        terminal = terminal_history_rows(conn, application_id)
        outcome = terminal[0][1] if terminal else None
        today = date.today().isoformat()
        start_date = (
            checklist_row[3] if checklist_row else (offer_row[1] if offer_row else None)
        )
        offer_accepted = offer_row is not None and offer_row[0] == "accepted"
        return {
            "application_id": application_id,
            "application_status": app_row[0],
            "offer_status": offer_row[0] if offer_row else None,
            "checklist_status": checklist_row[1] if checklist_row else None,
            "closed_reason": checklist_row[2] if checklist_row else None,
            "start_date": start_date,
            "terminal_outcome": outcome,
            "today": today,
            # spec「入职日到达无人操作 ⇒ 清单页显示"待确认"」
            "pending_confirmation": bool(
                checklist_row
                and checklist_row[1] == "open"
                and outcome is None
                and start_date is not None
                and start_date <= today
            ),
            "can_confirm_hired": bool(
                checklist_row
                and checklist_row[1] == "open"
                and outcome is None
                and offer_accepted
                and start_date is not None
                and start_date <= today
            ),
            "can_abandon": bool(offer_accepted and outcome is None),
        }

    @router.get("/api/applications/{application_id}/onboarding/terminal")
    def onboarding_terminal_state(application_id: str, request: Request):
        _require_role(request, "hr")
        return _terminal_state_payload(application_id)

    @router.post("/api/applications/{application_id}/onboarding/confirm-hired")
    def onboarding_confirm_hired(
        application_id: str, req: OnboardingConfirmHiredRequest, request: Request
    ):
        username = _require_role(request, "hr")
        try:
            effect_complete_onboarding(
                conn,
                thread_id=application_id,
                business_key="hired",
                actor_username=username,
                actual_start_date=req.actual_start_date,
                note=req.note,
            )
        except OnboardingTerminalRejected as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # 幂等命中时节点返回 None、什么都不写；照常回状态（重复点击不报错）
        return _terminal_state_payload(application_id)

    @router.post("/api/applications/{application_id}/onboarding/abandon")
    def onboarding_abandon(
        application_id: str, req: OnboardingAbandonRequest, request: Request
    ):
        username = _require_role(request, "hr")
        try:
            effect_complete_onboarding(
                conn,
                thread_id=application_id,
                business_key="abandoned",
                actor_username=username,
                reason=req.reason,
            )
        except OnboardingTerminalRejected as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _terminal_state_payload(application_id)
```

④ `app/web/static/onboarding_checklist.html`（U2 Task 5 产出）：在 `</main>` 之前追加终态卡片、在 `</body>` 之前追加自包含脚本：

```html
    <div class="card" id="terminal-card" style="display:none">
      <h2 class="page-title">入职终态</h2>
      <p id="terminal-status-line"></p>
      <div id="terminal-actions" style="display:none">
        <p>
          <label>实际入职日
            <input type="date" id="actual-start-date">
          </label>
        </p>
        <p>
          <label>说明（有未完成必需条目时必填）<br>
            <textarea id="terminal-note" rows="2"></textarea>
          </label>
        </p>
        <p><button id="confirm-hired-btn">确认已入职</button></p>
        <hr>
        <p>
          <label>放弃原因（必填）<br>
            <textarea id="abandon-reason" rows="2"></textarea>
          </label>
        </p>
        <p><button id="abandon-btn">放弃入职</button></p>
      </div>
    </div>
  </main>
```

```html
  <script>
    // 终态卡片：自包含，⛔ 不依赖 U2 脚本块里的变量（相对路径同 U2：<base href> 已由
    // 服务端注入，故从域根解析）。清单主体刷新走 load()（U2 的脚本块），有则调用。
    const terminalApplicationId = location.pathname.split("/").filter(Boolean).pop();
    const onboardingBase = `api/applications/${terminalApplicationId}/onboarding`;
    const terminalUrl = `${onboardingBase}/terminal`;
    const confirmHiredUrl = `${onboardingBase}/confirm-hired`;
    const abandonUrl = `${onboardingBase}/abandon`;

    function renderTerminal(state) {
      const card = document.getElementById("terminal-card");
      if (!state || !state.checklist_status) { card.style.display = "none"; return; }
      card.style.display = "block";
      const line = document.getElementById("terminal-status-line");
      const actions = document.getElementById("terminal-actions");
      if (state.terminal_outcome === "onboarded") {
        line.textContent = `已确认入职（${state.start_date}），清单已关闭。`;
        actions.style.display = "none";
        return;
      }
      if (state.terminal_outcome === "abandoned") {
        line.textContent = "已登记放弃入职，清单已关闭。";
        actions.style.display = "none";
        return;
      }
      line.textContent = state.pending_confirmation
        ? `入职日 ${state.start_date} 已到，待确认。`
        : "入职日到达后可在此确认终态。";
      actions.style.display = state.can_abandon ? "block" : "none";
      document.getElementById("actual-start-date").value = state.start_date || "";
      document.getElementById("confirm-hired-btn").disabled = !state.can_confirm_hired;
    }

    async function loadTerminal() {
      const resp = await fetch(terminalUrl);
      if (!resp.ok) return;
      renderTerminal(await resp.json());
    }

    function refreshChecklist() {
      if (typeof load === "function") load();
    }

    document.getElementById("confirm-hired-btn").addEventListener("click", async () => {
      const resp = await fetch(confirmHiredUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          actual_start_date: document.getElementById("actual-start-date").value || null,
          note: document.getElementById("terminal-note").value || null,
        }),
      });
      if (!resp.ok) { alert("确认失败"); return; }
      renderTerminal(await resp.json());
      refreshChecklist();
    });

    document.getElementById("abandon-btn").addEventListener("click", async () => {
      const reason = document.getElementById("abandon-reason").value.trim();
      if (!reason) { alert("放弃原因必填"); return; }
      const resp = await fetch(abandonUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason }),
      });
      if (!resp.ok) { alert("操作失败"); return; }
      renderTerminal(await resp.json());
      refreshChecklist();
    });

    loadTerminal();
  </script>
```

（`</main>` / `</body>` 两处是**在已有行之前插入**，⛔ 不要重复写出这两个闭合标签。）

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_onboarding_terminal_pages.py -q`
Expected: 全绿（12 个用例：3 条访问控制参数化 + 8 条端点行为 + 1 条页面片段）。

---

### Task 8: `app/audit/assertions.py` 三条新断言 ＋ CI 接入（tasks 3.6）

**Files:**
- Modify: `app/audit/assertions.py`（`import os`；三条新断言与常量；`COMPLIANCE_ASSERTIONS` 4→6；新增 `CONFIG_GATED_ASSERTIONS`；`run_compliance_assertions` 加关键字参数；CLI 加旗标）
- Modify: `docs/audit-and-outbound-ops.md`（追加巡检命令与旗标口径）
- Modify: `.github/workflows/ci.yml`（合规步骤的注释跟着条数改；⛔ 命令不动——它按 `-m compliance` 收用例）
- Modify: `tests/test_audit_assertions.py`（条数 4→6 / 4→7 ＋ 三条正向用例）
- Modify: `tests/test_audit_assertion_effectiveness.py`（条数修正 ＋ 三条反证）
- Modify: `tests/test_compliance_cli.py`（旗标退出码契约）

**Interfaces:**
- Consumes: `onboarding_item` / `data_disposition_queue` / `rejection_record` / `application`（U1 与 M2 已建）
- Produces:
  - `COMPLIANCE_ASSERTIONS`（6 条，全部 `Callable[[sqlite3.Connection], AssertionResult]`）
  - `CONFIG_GATED_ASSERTIONS`（1 条，签名 `(conn, *, retention_policy_signed_version=None)`）
  - `run_compliance_assertions(conn, *, retention_policy_signed_version: str | None = None) -> list[AssertionResult]`（返回 7 条）
  - CLI：`--retention-policy-signed-version`（默认取 `os.environ.get("RETENTION_POLICY_SIGNED_VERSION")`）

- [ ] **Step 1: Write the failing test**

① `tests/test_audit_assertions.py`：`test_run_compliance_assertions_returns_all_three` 里的四行数字改成：

```python
    assert len(results) == 7
    assert len(COMPLIANCE_ASSERTIONS) == 6
    assert len({r.name for r in results}) == 7
```

并在文件末尾追加三条正向用例：

```python
# ── onboarding-flow U3：三条新断言的正向行为 ──────────────────────────


def test_onboarding_item_without_content_columns_passes(conn):
    from app.audit.assertions import assert_onboarding_item_has_no_content_columns

    assert assert_onboarding_item_has_no_content_columns(conn).ok is True


def test_refused_application_without_rejection_passes(conn):
    from app.audit.assertions import assert_refused_application_has_no_rejection

    assert assert_refused_application_has_no_rejection(conn).ok is True


def test_disposition_queue_never_executed_passes(conn):
    from app.audit.assertions import (
        assert_disposition_not_executed_without_signed_policy,
    )

    # 未签认（默认 None）：队列为空 ⇒ 通过
    assert assert_disposition_not_executed_without_signed_policy(conn).ok is True
    # 已签认：执行行为空 ⇒ 仍然通过
    assert assert_disposition_not_executed_without_signed_policy(
        conn, retention_policy_signed_version="v1-180d"
    ).ok is True
```

② `tests/test_audit_assertion_effectiveness.py`：把汇总用例的期望改成七条：

```python
    assert len(results) == 7
    # 第五/六/七条（材料无内容列、未签认不得执行、refused 无拒绝记录）本次没被破坏
    assert [r.ok for r in results] == [False, False, False, True, True, True, True]
```

并在文件末尾追加三条反证：

```python
# ── 反证五：onboarding_item 出现内容列（本包合规约束）──────────────────


def test_content_column_on_onboarding_item_is_detected(conn):
    from app.audit.assertions import assert_onboarding_item_has_no_content_columns

    conn.execute("ALTER TABLE onboarding_item ADD COLUMN content TEXT")
    conn.commit()

    result = assert_onboarding_item_has_no_content_columns(conn)

    assert result.ok is False
    assert [v["forbidden_column"] for v in result.violations] == ["content"]


# ── 反证六：未签认时执行了处置（红线：不可逆动作不自动发生）────────────


def _insert_disposition_row(conn, *, executed_at=None, policy_version=None):
    _ensure_application_exists(conn, "app-9")
    conn.execute(
        "INSERT INTO data_disposition_queue "
        "(id, application_id, candidate_id, category, policy_version, planned_action, executed_at) "
        "VALUES ('dq-1', 'app-9', 'cand-9', 'resume_file', ?, 'pending', ?)",
        (policy_version, executed_at),
    )
    conn.commit()


def test_executed_disposition_without_signed_policy_is_detected(conn):
    from app.audit.assertions import (
        assert_disposition_not_executed_without_signed_policy,
    )

    _insert_disposition_row(conn, executed_at="2026-10-20 10:00:00", policy_version="v1-180d")

    # ① 调用方说"策略未签认"（默认 None）⇒ 任何已执行行都是违例
    assert assert_disposition_not_executed_without_signed_policy(conn).ok is False
    # ② 调用方给出签认版本、执行行带版本 ⇒ 通过
    assert assert_disposition_not_executed_without_signed_policy(
        conn, retention_policy_signed_version="v1-180d"
    ).ok is True


def test_executed_disposition_without_policy_version_is_detected(conn):
    from app.audit.assertions import (
        assert_disposition_not_executed_without_signed_policy,
    )

    _insert_disposition_row(conn, executed_at="2026-10-20 10:00:00", policy_version=None)

    result = assert_disposition_not_executed_without_signed_policy(
        conn, retention_policy_signed_version="v1-180d"
    )

    assert result.ok is False
    assert result.violations[0]["issue"] == "executed_without_policy_version"


# ── 反证七：refused 的投递却有拒绝记录（自愿退出被写成了淘汰）─────────


def test_rejection_record_on_refused_application_is_detected(conn):
    from app.audit.assertions import assert_refused_application_has_no_rejection

    _ensure_application_exists(conn, "app-9")
    conn.execute("UPDATE application SET status='refused' WHERE id='app-9'")
    insert_rejection_record(
        conn, row_id="rej-refused", application_id="app-9", reason_type="human_decision"
    )

    result = assert_refused_application_has_no_rejection(conn)

    assert result.ok is False
    assert result.violations[0]["id"] == "rej-refused"
```

⚠️ 上面复用 `_ensure_application_exists` / `insert_rejection_record`（`tests/test_audit_assertions.py` 已导出，效果测试文件顶部已 import 前者）：落盘时以这两个 helper 的**真实签名**为准传参（`insert_rejection_record` 若不吃 `application_id`，按它的既有形态传），⛔ 不新造第三套夹具。

③ `tests/test_compliance_cli.py` 顶部 import 区补 `from tests.test_audit_assertions import _ensure_application_exists`，文件末尾追加：

```python
def test_signed_policy_flag_lets_executed_rows_pass(db_path, mirror_path, capsys):
    """给出签认版本时，带 policy_version 的已执行行不算违例；不给出时算——
    退出码是 CI 与 .51 巡检唯一的判据，⛔ 两者不能折成同一个数。"""
    conn = get_connection(str(db_path))
    _ensure_application_exists(conn, "app-9")
    conn.execute(
        "INSERT INTO data_disposition_queue "
        "(id, application_id, candidate_id, category, policy_version, planned_action, executed_at) "
        "VALUES ('dq-1', 'app-9', 'cand-9', 'resume_file', 'v1-180d', 'delete', "
        "'2026-10-20 10:00:00')"
    )
    conn.commit()
    conn.close()

    without_flag = main(["--db", str(db_path), "--mirror", str(mirror_path)])
    without_out = capsys.readouterr().out
    with_flag = main(
        ["--db", str(db_path), "--mirror", str(mirror_path),
         "--retention-policy-signed-version", "v1-180d"]
    )

    assert without_flag == 1
    assert "未签认" in without_out
    assert with_flag == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_audit_assertions.py tests/test_audit_assertion_effectiveness.py tests/test_compliance_cli.py -q`
Expected: FAIL —— `ImportError: cannot import name 'assert_onboarding_item_has_no_content_columns'`；条数断言 4≠6。

---

- [ ] **Step 3: Write minimal implementation**

① `app/audit/assertions.py` 顶部 import 区加 `import os`（放 `import sqlite3` 之前）。⛔ 仍然 **不 import `app.config`**——CLI 旗标的默认值直接读环境变量，分层规矩见 `app/audit/__init__.py`。

② 在「四条一起跑」小节**之前**整段新增三条断言：

```python
# ── 断言五（onboarding-flow U3 tasks 3.6①）：材料不入库 ──────────────────

ONBOARDING_ITEM_TABLE = "onboarding_item"

# ⛔ 与 tests/test_db_onboarding_schema.py::test_onboarding_item_has_no_content_columns
# 的集合逐字同源（那份是建表期的列名反证，本条是 CI 里可归因的红线）。
ONBOARDING_ITEM_FORBIDDEN_COLUMNS = frozenset(
    {
        "content", "attachment", "id_number", "file", "file_name",
        "content_sha256", "raw_text", "parsed_json", "url",
    }
)

ASSERTION_ONBOARDING_ITEM_HAS_NO_CONTENT_COLUMNS = (
    "onboarding_item 不存在材料内容/附件/证件号类列"
)


def assert_onboarding_item_has_no_content_columns(conn: sqlite3.Connection) -> AssertionResult:
    """本包合规约束「材料本身不入库」的事后断言。

    表不存在 ⇒ **失败**（判据同断言四：这张表由 onboarding-flow U1 建，缺表意味着
    「材料不入库」这条约束在现网完全没有机器守护；fail-closed——验不了不算守住）。
    """
    if not _table_exists(conn, ONBOARDING_ITEM_TABLE):
        return AssertionResult(
            name=ASSERTION_ONBOARDING_ITEM_HAS_NO_CONTENT_COLUMNS,
            ok=False,
            violations=({"table": ONBOARDING_ITEM_TABLE, "issue": "table_missing"},),
            detail=(
                f"{ONBOARDING_ITEM_TABLE} 表不存在。它由 onboarding-flow U1 建，"
                "缺表＝材料不入库这条约束没有机器守护，fail-closed 判失败。"
            ),
        )
    found = sorted(
        ONBOARDING_ITEM_FORBIDDEN_COLUMNS & _columns(conn, ONBOARDING_ITEM_TABLE)
    )
    return AssertionResult(
        name=ASSERTION_ONBOARDING_ITEM_HAS_NO_CONTENT_COLUMNS,
        ok=not found,
        violations=tuple(
            {"table": ONBOARDING_ITEM_TABLE, "forbidden_column": column} for column in found
        ),
        detail=(
            ""
            if not found
            else f"清单条目表出现内容类列 {found}——材料一旦入库，PIA、加密、留存全要升档。"
            "⛔ 不要把它们加进白名单：正确处置是删列（清单只跟踪状态）。"
        ),
    )


# ── 断言六（onboarding-flow U3 tasks 3.6②）：未签认不得执行处置 ──────────

DISPOSITION_QUEUE_TABLE = "data_disposition_queue"

ASSERTION_DISPOSITION_NOT_EXECUTED_WITHOUT_SIGNED_POLICY = (
    "留存策略未签认时 data_disposition_queue.executed_at 全空"
)


def assert_disposition_not_executed_without_signed_policy(
    conn: sqlite3.Connection, *, retention_policy_signed_version: str | None = None
) -> AssertionResult:
    """🔴 不可代项「真实简历数据处理范围的变更」＋ design D4 的机器判据。

    ⚠️ 参数为什么必须由调用方传：`app/audit` **不 import `app.config`**（分层规矩，
    见 `app/audit/__init__.py`）——签认版本是配置事实，不是库事实。CLI 用
    `--retention-policy-signed-version` 传（默认取同名环境变量）。

    判据两条：
      ① `retention_policy_signed_version` 为空（未签认）⇒ 任何 `executed_at` 非空
         的行都是违例（删错不可逆，这正是"未签认一行都不许动"的落点）；
      ② 给了签认版本 ⇒ 已执行行必须带非空 `policy_version`（执行单元必须能指回
         一份签认的策略版本，否则这条留痕在事后查不清依据）。
    """
    if not _table_exists(conn, DISPOSITION_QUEUE_TABLE):
        return AssertionResult(
            name=ASSERTION_DISPOSITION_NOT_EXECUTED_WITHOUT_SIGNED_POLICY,
            ok=False,
            violations=({"table": DISPOSITION_QUEUE_TABLE, "issue": "table_missing"},),
            detail=(
                f"{DISPOSITION_QUEUE_TABLE} 表不存在（onboarding-flow U1 建）。"
                "缺表＝处置钩子的登记与执行都无迹可查，fail-closed 判失败。"
            ),
        )

    executed = _rows(
        conn,
        "SELECT id, application_id, category, policy_version, executed_at, executed_by "
        f"FROM {DISPOSITION_QUEUE_TABLE} WHERE executed_at IS NOT NULL",
    )
    signed_version = (retention_policy_signed_version or "").strip()
    violations: list[dict[str, Any]] = []
    for row in executed:
        if not signed_version:
            violations.append({**row, "issue": "executed_while_policy_unsigned"})
        elif not (row["policy_version"] or "").strip():
            violations.append({**row, "issue": "executed_without_policy_version"})

    return AssertionResult(
        name=ASSERTION_DISPOSITION_NOT_EXECUTED_WITHOUT_SIGNED_POLICY,
        ok=not violations,
        violations=tuple(violations),
        detail=(
            ""
            if not violations
            else "发现留存策略未签认（或已执行行缺 policy_version）的处置执行记录。"
            "删除不可逆：必须先由决策人签认留存策略并配置 RETENTION_POLICY_SIGNED_VERSION。"
        ),
    )


# ── 断言七（onboarding-flow U3 tasks 3.6③）：自愿退出不是淘汰 ────────────

ASSERTION_REFUSED_APPLICATION_HAS_NO_REJECTION = (
    "application.status='refused' 的投递没有本包写入的 rejection_record"
)


def assert_refused_application_has_no_rejection(conn: sqlite3.Connection) -> AssertionResult:
    """合规红线「放弃入职＝候选人自愿退出，不是我方淘汰」的事后断言。

    判据：`application.status='refused'` 的投递不得有任何 `rejection_record` 行——
    这些投递的终态是 `effect_complete_onboarding(abandon)` 写的，它 ⛔ 不写拒绝记录
    （onboarding-completion spec 原话「MUST NOT 写入淘汰记录」）。表缺失一律 fail-closed。
    """
    for table in (REJECTION_TABLE, "application"):
        if not _table_exists(conn, table):
            return AssertionResult(
                name=ASSERTION_REFUSED_APPLICATION_HAS_NO_REJECTION,
                ok=False,
                violations=({"table": table, "issue": "table_missing"},),
                detail=f"{table} 表不存在，本条断言无法验证。fail-closed：验不了不算通过。",
            )

    rows = _rows(
        conn,
        f"SELECT r.id, r.application_id, r.{REJECTION_REASON_COLUMN}, r.decided_by, r.decided_at "
        f"FROM {REJECTION_TABLE} r JOIN application a ON a.id = r.application_id "
        "WHERE a.status = 'refused'",
    )
    return AssertionResult(
        name=ASSERTION_REFUSED_APPLICATION_HAS_NO_REJECTION,
        ok=not rows,
        violations=tuple(rows),
        detail=(
            ""
            if not rows
            else f"发现 {len(rows)} 条挂在 refused 投递上的拒绝记录：候选人自愿退出被写成了"
            "我方淘汰。查一下是不是有人绕开 effect_complete_onboarding 直接写了拒绝记录。"
        ),
    )
```

③ 标题「四条一起跑」整段替换成下面这段（标题、两个元组、`run_compliance_assertions` 一起改，⛔ 别只改元组）：

```python
# ── 七条一起跑 ──────────────────────────────────────────────────────────

COMPLIANCE_ASSERTIONS: tuple[Callable[[sqlite3.Connection], AssertionResult], ...] = (
    assert_no_ai_score_rejections,
    assert_no_blank_evidence_ref,
    assert_no_unlisted_criterion_key,
    assert_every_decision_has_human_review,
    assert_onboarding_item_has_no_content_columns,
    assert_refused_application_has_no_rejection,
)

# 需要调用方补一个上下文参数的断言（不是"只看库"能判的）。单独成组，是因为
# COMPLIANCE_ASSERTIONS 的元素签名必须齐平（run_compliance_assertions 统一调用）。
CONFIG_GATED_ASSERTIONS: tuple[Callable[..., AssertionResult], ...] = (
    assert_disposition_not_executed_without_signed_policy,
)


def run_compliance_assertions(
    conn: sqlite3.Connection, *, retention_policy_signed_version: str | None = None
) -> list[AssertionResult]:
    """spec「合规断言在 CI 中执行」：全部成立才通过。

    ⚠️ **全部跑完再返回，⛔ 不短路。** 第一条红了就返回的话，一次修复只能
    看到一条违例，第二条要等下一轮 CI 才现形。

    `retention_policy_signed_version` 只喂给 CONFIG_GATED_ASSERTIONS（断言六的
    说明：签认版本是配置事实，app/audit 不 import app.config，只能由调用方传）。
    """
    results = [assertion(conn) for assertion in COMPLIANCE_ASSERTIONS]
    results += [
        assertion(conn, retention_policy_signed_version=retention_policy_signed_version)
        for assertion in CONFIG_GATED_ASSERTIONS
    ]
    return results
```

④ CLI：`main()` 里 `parser.add_argument("--mirror", ...)` 之后加旗标，并把调用处改成传参（两处如下）：

```python
    parser.add_argument(
        "--retention-policy-signed-version",
        default=os.environ.get("RETENTION_POLICY_SIGNED_VERSION") or None,
        help=(
            "已签认的留存策略版本（Settings.retention_policy_signed_version 的值）。"
            "不传且环境变量也没有 ⇒ 按「未签认」口径校验：队列里任何 executed_at 非空都判失败。"
            "⚠️ 策略配置在 .env 里时 pydantic-settings 不会导出到进程环境，巡检必须显式传本参数；"
            "⛔ 不要因为「报红了但明明签认过」就把这条断言摘掉。"
        ),
    )
```

```python
        results = run_compliance_assertions(
            conn, retention_policy_signed_version=args.retention_policy_signed_version
        )
```

⑤ `docs/audit-and-outbound-ops.md`：在末节 `## 关联` 之前插入：

```markdown
## 六、合规断言巡检命令（含留存策略版本）

    .venv\Scripts\python.exe -m app.audit.assertions --db data\demo.db --mirror data\audit\decisions.jsonl --retention-policy-signed-version <已签认版本>

七条断言里有一条（「留存策略未签认时 `data_disposition_queue.executed_at` 全空」）
需要知道**当前是否已签认**：不传本参数即按「未签认」口径校验——此时处置队列里
任何 `executed_at` 非空都判失败。

⚠️ 策略版本配置在 `.env` 文件里时不会被导出到进程环境，**必须显式传参**；
配置在进程环境里时命令行可省略（CLI 会读同名环境变量）。
⛔ 报红时不要摘断言、也不要把参数写死成某个旧版本——先确认 `.51` 上签认到哪一版。
```

⑥ `.github/workflows/ci.yml` 的「合规断言（红线守护）」步骤注释：把「三条断言 + 链校验 + 对账」改成「六条断言（＋一条需签认上下文的断言）+ 链校验 + 对账」，「22 条反证」改成本次实测条数（`grep -c '^def test_' tests/test_audit_assertion_effectiveness.py`）；⛔ `run:` 命令一个字都不改（`-m compliance` 按标记收用例，新用例已带 `pytestmark = pytest.mark.compliance`）。

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_audit_assertions.py tests/test_audit_assertion_effectiveness.py tests/test_compliance_cli.py -q`
Expected: 全绿（含三条反证）。

---

## 交付前自查

- [ ] **全量测试全绿**（基线 + 本单元新增，0 failed / 0 skipped）

```bash
./venv/bin/python -m pytest -q 2>&1 | tail -3
```

- [ ] **机器判据四条全绿**（在 worktree 根执行）

```bash
test -n "$(ls docs/superpowers/plans/2026-10-10-onboarding-flow-unit3*.md 2>/dev/null)"
grep -q '^### Task ' docs/superpowers/plans/2026-10-10-onboarding-flow-unit3*.md
grep -q 'Global Constraints' docs/superpowers/plans/2026-10-10-onboarding-flow-unit3*.md
grep -q 'effect_complete_onboarding' docs/superpowers/plans/2026-10-10-onboarding-flow-unit3*.md
```

Expected: 四条命令各自退出码 0（本文件满足）。

- [ ] **Task 标题为三级 `### Task `，数量 = 8**

```bash
grep -c '^### Task ' docs/superpowers/plans/2026-10-10-onboarding-flow-unit3-terminal-and-disposition.md
```

Expected: `8`（`scripts/codex_sdd_runner.py` 按三级标题抽取，⛔ 不能用二级）。

- [ ] **无 TBD/TODO/占位符**

```bash
grep -nE 'TBD|TODO|FIXME|适当处理错误|待补' docs/superpowers/plans/2026-10-10-onboarding-flow-unit3-terminal-and-disposition.md || true
```

Expected: 无输出。

- [ ] **依赖文件 diff 为空**

```bash
git diff origin/main --stat -- requirements.txt pyproject.toml
```

Expected: 无输出。

- [ ] **终态唯一性 + 无自动终态路径**

```bash
./venv/bin/python -m pytest tests/test_onboarding_terminal_invariant.py -q
```

Expected: 全绿（15 个）。

- [ ] **合规断言七条（含反证）全绿**

```bash
./venv/bin/python -m pytest -q -m compliance
```

Expected: 全绿（CI 的同一个标记）。

---

## spec 覆盖对照

### `specs/onboarding-completion/spec.md`

| spec Requirement | 本单元落点 | 覆盖度 |
|---|---|---|
| 确认已入职 | Task 4（`effect_complete_onboarding(hired)`：前置今日 ≥ 入职日、写 `actor_type='human'` 的终态流转事实、清单 closed、未完成必需条目需 note 且未完成清单进 `detail_json` 留痕）＋ Task 7（`POST .../confirm-hired`） | **完整兑现** |
| 放弃入职 | Task 5（`application.status='refused'`、`offer.status='abandoned'`、终态流转事实一条、清单 closed、⛔ 不写 `rejection_record`）＋ Task 7（`POST .../abandon`）＋ Task 8 断言七 | **完整兑现** |
| 终态动作幂等且唯一 | Task 3/4/5（幂等键 `{application_id}:effect_complete_onboarding:{hired\|abandoned}`；"已有终态事实即拒"；重跑返回 None）＋ Task 6（不变式：≤1 条终态事实、与 `application.status`/`offer.status` 一致） | **完整兑现** |
| 终态由人确认 | Task 4/5（`actor_username` 必填、`actor_type='human'` 硬编码、空操作人拒绝）＋ Task 6（签名反证 + 调用面反证 + 调度构造反证）＋ Task 7（三个端点全走 `_require_role(request, "hr")`） | **完整兑现** |
| （页面）"待确认"显示 | Task 7（`pending_confirmation` 字段 + 卡片文案） | **完整兑现** |

### `specs/post-hire-data-disposition/spec.md`

| spec Requirement | 本单元落点 | 覆盖度 |
|---|---|---|
| 入职完成时登记处置事项 | Task 3（六类各一行、`policy_version=NULL`、`planned_action='pending'`、每类一把幂等键）＋ Task 4（`confirm_hired` **同事务**调用，反证见 `test_confirm_hired_rolls_back_everything_when_registration_fails`） | **完整兑现** |
| 留存策略未签认时只登记不执行 | Task 3（登记行 `due_at`/`executed_at` 恒空）＋ Task 8 断言六（未签认时 `executed_at` 非空即违例） | 本单元兑现"登记 + 断言"；**执行单元**在 U4（4.1/4.3） |
| 策略签认后按策略执行 | ⛔ 本单元无落点 | U4（4.3）；🔴 前置＝合规验收 #1 签认（不可代） |
| 处置不影响合规断言 | ⛔ 本单元无落点（断言六是"执行前"的口径） | U4（4.4） |

### 其它 spec

`specs/onboarding-checklist/spec.md` 与 `specs/onboarding-visibility/spec.md`：**本单元无落点**（U2 已完整兑现，见 U2 计划同名小节）；本单元只读它们产出的清单/条目。

---

## 本计划相对 `tasks.md` / `design.md` 的偏离登记

| # | 偏离 | 位置 | 方向 | 理由 |
|---|---|---|---|---|
| 1 | 新增三处 CHECK 值域放宽（`application.status` +`'refused'`、`offer.status` +`'abandoned'`、`application_stage_history.action` +`'onboarded'/'abandoned'`，均整表重建迁移） | Task 1/2 | 补磁盘缺口 | tasks 3.1/3.3 的字面值在当前磁盘状态域上**写不进去**（会被 CHECK 拒）；`{ongoing,hired,refused}` 全量迁移与 offer 状态机归 offer-generation U5，未交付。手法同 U2 Task 1 先例（SQLite 改不了 CHECK）。⛔ 三个重建都是**加法**：既有行的值与行为一字不改 |
| 2 | 终态流转事实**不改阶段**（`from_stage_id = to_stage_id = current_stage_id`），语义由 `action` 承载 | Task 4/5 | 对可行性 | tasks 3.1 写的是 `stage_type=hired`（本表没有 `stage_type` 列，那是 `stage` 表的列），且 `abandon` ⛔ 不能写成流转到 `'rejected'`——spec 明写"候选人自愿退出，不是我方淘汰"，写 rejected 会把自愿退出污染进淘汰口径。`stage` 枚举里也没有 `refused`（加它要再改一次 `stage_type` CHECK，且动的是其它包的域）。这种做法与 interview-scheduling U2 的既定先例（`db.py` 对 `application_stage_history` 的注释：阶段不变、靠 `action` 区分动作）逐字同源 |
| 3 | `application.status` **不加** `'ongoing'` | Task 1 | 保守最小 | design 写 `{ongoing, hired, refused}`，磁盘写 `active/rejected/withdrawn`——`ongoing` 与 `active` 是同一语义的两种写法。同时存在两值会让"同一个状态两处真源"，且 `'ongoing'` 的写入方是 offer-generation U5（本单元只需要 `'refused'`）。⛔ 本单元不替它拍这个别名 |
| 4 | `offer.status` 用 `'abandoned'` 而不是既有 `'declined'` | Task 1/5 | 对 spec 字面 | onboarding-completion spec 原话「Offer 记录状态变为**已放弃**」；`declined` 的语义是"候选人在 Offer 阶段拒绝 Offer"（offer-generation 已用它），把"接受后放弃入职"并进去会丢信息 |
| 5 | `abandon` **不要求**存在清单（`hired` 要求） | Task 5 | 对 spec 字面 | spec 放弃入职 Requirement 是 `SHALL 允许`，且"人还没来就不来了"最常见于清单生成之前；要求清单等于在这条路径上加了 spec 没有的前置。清单存在时仍会关它（`closed_reason='abandoned'`） |
| 6 | 两个终态都要求 `offer.status='accepted'` | Task 4/5 | 补全前置 | tasks 未写；但"入职终态"的域前置就是 Offer 已接受（design：Offer 接受＝`application.status=hired` 是本包唯一入口），且没有 Offer 时 `UPDATE offer SET status='abandoned'` 会静默影响 0 行 |
| 7 | 审计断言六把"是否已签认"做成**调用方传参**（CLI `--retention-policy-signed-version`，默认读同名环境变量） | Task 8 | 守分层规矩 | `app/audit` 不 import `app.config`（`__init__.py` 明文规矩）。不传即按"未签认"口径 fail-closed——方向上宁可多红一次，也不让"验不了"折成"守住了" |
| 8 | 新增 `docs/audit-and-outbound-ops.md` 一节（旗标口径） | Task 8 ⑤ | 配套文档 | 新旗标不写进运维口径，等于让 `.51` 上的人永远按"未签认"跑巡检（一条会长期假红的红线）；这一节是它的唯一说明处 |
| 9 | 三条迁移的测试文件独立成两份（`test_db_terminal_status_migration.py` / `test_db_stage_history_terminal_action.py`），且 Task 2 只改 `tests/test_db_m2_schema.py` 的 docstring 措辞 | Task 1/2 | 复用与最小侵入 | `tests/test_db_migration.py` 已持有 `${HR_GATE_MAIN}` 级的老库夹具，但那两份是**新库+本包老库**的一次性回归，放进大文件会把本包的变化淹掉；`test_db_m2_schema.py` 的那句 docstring 会因值域扩充而**变成假话**，必须同步改（⛔ 断言不动） |

前 6 条属"不改变外部可观察行为的技术方案/补前置"范畴（新增取值不会让任何既有行为改变），由 `run-build` 的 final review 确认；第 7/8 条涉及一条新接口（CLI 旗标），也在 final review 的范围内。

---

## 提取验证记录（`spec-to-plan` 第 6 步，2026-10-11 实测）

本 worktree **无 `./venv`**（泳道只产出 plan 文档，运行环境在 run-build 的隔离 worktree 里由执行器装配），
且 opener 边界明写「⛔ 不改 `app/**` / `tests/**`」——不能把计划里的代码落盘再跑全量 pytest。

已做的等价验证（`/opt/homebrew/bin/python3.12`，**只读** import 仓库代码 + 内存库，⛔ 未改仓库任何文件）：

做法：用脚本把本计划里的代码块**按块原样提取**到 `/tmp/u3verify/blocks/`，再把它们接到仓库真身
（`app/storage/db.py` 的 `SCHEMA`/`_ADDED_COLUMNS` 按计划的三处替换打补丁、`app/graph/onboarding_nodes.py`
按 Task 3/4/5 追加）上，在临时库里逐条跑本计划里的用例。**实测：53 条用例全绿（FAIL 0）。**

| 组 | 用例数 | 结果 |
|---|---|---|
| Task 1（`application.status`/`offer.status` 值域：新库/老库/幂等/无 status 列老库） | 5 | 全绿 |
| Task 2（`action` 值域：新库/老库/幂等） | 4 | 全绿 |
| Task 3（`effect_enqueue_disposition`：六行/六把键/重跑） | 3 | 全绿 |
| Task 4（`confirm_hired`：正常写事实/未完成条目+note/需 note/入职日前拒/无 Offer/无清单/已有终态/重跑幂等/无操作人/未知结局/登记失败全回滚/history 判据） | 12 | 全绿 |
| Task 5（`abandon`：事实与状态/不写拒绝记录/不登记处置/需原因/已入职后拒/无清单可行/Offer 未接受拒/重跑幂等） | 8 | 全绿 |
| Task 6（8 条驱动顺序 + 3 条补充 + 签名反证） | 12 | 全绿 |
| Task 8（三条断言：干净库 + 四处造违例） | 8 | 全绿 |

**本轮揪出并已修掉的 4 个真实缺陷**（都在 Task 1/2/4 的用例或夹具里，若不做这一步要到 run-build 中途才爆）：

1. Task 1/2 两份老库夹具的 `candidate` 表漏了 `phone_hash` 列，而 `SCHEMA` 里有
   `CREATE UNIQUE INDEX … ON candidate (name, phone_hash)` ⇒ `executescript(SCHEMA)` 当场
   `no such column: phone_hash`（U2 的老库夹具带这列，抄的时候漏了）。已补齐。
2. `test_confirm_hired_rejects_when_already_terminal` 原写法是"先确认一次、再确认一次 ⇒ 期望被拒"，
   实测**不会抛**：同一把幂等键的第二次调用由 `@idempotent_effect` 短路返回 `None`（幂等命中不是拒绝）。
   已改写成"事实已存在但 `effect_log` 里没有对应键"（手工写入）来验那条前置——那才是它要兜的洞。
3. Task 5 的 `abandon` 分支替换说明原先写成"兜底 `raise` 仍留在其后"，实测那会留下**不可达的死代码**；
   已改成"整句替换、函数在 `return history_id` 结束"（未知取值由顶部 `TERMINAL_OUTCOMES` 拦住）。
4. Task 4 的用例数标注为 9，实际 12（漏数了 `requires_actor` / 未知结局 / 回滚 / history 判据四条）。

**本验证未覆盖、也⛔ 不可在本会话覆盖的部分**：

- **Task 6 里 3 条按文件路径判的**（`only_the_terminal_node_writes_onboarded_fact` /
  `terminal_calls_are_limited_to_the_hr_surface` / `no_scheduling_construct_in_terminal_callers`）：
  它们读 `app/**` 的**磁盘真身**，而本泳道 ⛔ 不能落盘 U3 的代码块 ⇒ 本会话无法跑。这三条的正确性
  依赖"U3 只有 `onboarding_nodes.py` 与 `web/server.py` 会写/调用终态"这一条设计约束，由 run-build 的
  review 与实现共同保证。
- **Task 7 的三个 HTTP 端点与页面片段**：本机无 `fastapi`（无 `./venv`），无法起 `TestClient`；
  本会话只做了静态核对（路由签名与 `_require_role` / `_render_static_page` / `<!--BASE_HREF-->`
  三处既有约定逐条对齐）。端点的角色/幂等/前置拒绝由 run-build 的 pytest 兜。
- **`electron`/`sqlite` 之外的运行时差异**：本验证跑在 `python3.12` 上，而生产与 CI 是 Windows + Py3.14
  （CI 见 `.github/workflows/ci.yml`）；SQLite 版本与文件系统差异由 CI 兜。

**边界**：测试与被测代码出自同一份文档、同一个作者，全通只证明**代码可执行且内部自洽**，不证明**符合 spec**。
spec 合规由 `run-build` 的两阶段 review 负责，本验证不是它的替代品。

---

## 完成判据（`tasks.md` 第 3 章 checkbox 在这些全部成立后才勾）

1. `application.status` 新库与老库都能写 `'refused'`、`offer.status` 能写 `'abandoned'`、`action` 能写 `'onboarded'/'abandoned'`，既有行/索引/外键一字不变（Task 1/2）
2. `effect_enqueue_disposition`：六类各一行（`policy_version=NULL`、`planned_action='pending'`、`executed_at` 空）、每类一把幂等键、重跑仍是六行（Task 3）
3. `effect_complete_onboarding(hired)`：入职日前被拒；有未完成必需条目时无 note 被拒、有 note 则确认成功且未完成清单与说明进 `detail_json`；写 history ＋ 清单 closed ＋ 六类登记**同事务**；重跑返回 None、无第二条终态（Task 4）
4. `effect_complete_onboarding(abandoned)`：须填原因；`application.status='refused'`、`offer.status='abandoned'`、history 一条、清单 closed(abandoned)；⛔ `rejection_record` 行数不变；已确认"已入职"后再标放弃被拒（Task 5）
5. 不变式：任一投递终态事实 ≤ 1 条、与 `application.status`/`offer.status` 一致；穷举顺序（含重跑与被拒步骤）全绿；⛔ 无自动终态路径（调用面/签名/调度构造三条反证）（Task 6）
6. 终态确认 UI：三个 HR 端点（匿名 401、`interviewer` 403）、"待确认"状态、确认/放弃的幂等与前置拒绝、清单页终态卡片在位且无上传控件（Task 7）
7. 三条新断言在干净库上通过、在造违例时必须失败；`CI` 的 `-m compliance` 步骤覆盖它们（Task 8）
8. 全量 pytest 全绿、依赖文件 diff 为空

**合并后立刻解锁**：U4（处置执行，`effect_execute_disposition` / 脱敏规则 / 断言兼容）可出一份 `spec-to-plan` 并行开工——但 U4 的 4.5（签认后配置与首轮执行）与 U5 的 5.4（`.51` 发版）是不可代项，必须回到 Shao Peishen。

**下一步**：用 `run-build` 执行本计划。⛔ 不要在本会话里开始实现。

---

## 留步登记

1. ⏸ 留步：**U2 Task 5（HR 清单页 `app/web/static/onboarding_checklist.html`）尚未落地**（本会话磁盘核实：该文件、`app/agents/onboarding_progress.py`、`tests/test_onboarding_pages.py` 都不存在；U2 只有 Task 1–3 落了 main）。影响面**只有 Task 7 的页面片段**：`app/graph/onboarding_nodes.py` 与 `app/web/server.py` 的三个端点不受影响，Task 1–6/8 也不受影响。执行顺序上 U2 先于 U3（design D7），⛔ 不自建清单页副本；执行到 Task 7 且前置检查不过时按该 Task 的说明停在该点并登记。
2. ⏸ 留步：**`application.status='hired'` 的生产写入来源（offer-generation U5）仍未交付**——与本包 U2 的留步同因。U3 的代码与单测用夹具直接置位即可（Task 1 已让 `hired` 与 `refused` 都可写），生产上"过 Offer → 生成清单 → 确认入职"的端到端链路要等 offer-generation U5。
3. 🔴 **不可代项，本单元不触碰、也不默认生效**：`application.status='refused'` 与 `offer.status='abandoned'` 只是**取值变宽**，不构成"候选人数据处理范围变更"；本单元 ⛔ 不执行任何删除/脱敏、⛔ 不对外发送、⛔ 不碰 `.51` 发版。U4 的 4.5（签认后配置 `RETENTION_POLICY_SIGNED_VERSION` 与首轮执行）与 U5 的 5.4（`.51` 发版）属 Shao Peishen，本计划只登记不推进。
4. 需人定夺（不可代，已登记不默认生效）：**无**（本单元不新增需要 Shao Peishen 拍板的分叉；Task 8 的 CLI 旗标属"不改变外部可观察行为"的技术方案，由 final review 确认）。
