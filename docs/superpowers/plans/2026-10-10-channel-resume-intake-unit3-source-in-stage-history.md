# channel-resume-intake U3：来源进流转事实 Implementation Plan

> **For agentic workers（Codex 引擎）：** 用 `run-build`（`scripts/codex_sdd_runner.py`）按 `### Task N:` 三级标题逐任务执行；本计划是 spec-to-plan 的唯一输出，⛔ 不在本会话里开始写代码。执行前先读本计划的「架构决策」与「Global Constraints」，reviewer 以它们为注意力透镜。

**Goal:** 投递创建时把该简历的来源写进它的初始阶段流转事实（`application_stage_history.source`），HR 事后改正来源时**追加**一条 `action='source_corrected'` 事实而**不改写**初始记录；来源分布可由只读查询函数 `source_distribution(job_id)` 直接从流转事实表算出（⛔ 不建漏斗报表页）；`app/audit/assertions.py` 新增三条断言（候选人表无明文手机号列／合并留痕的操作人非空／被合并候选人无活跃投递）并随 `COMPLIANCE_ASSERTIONS` 进 CI。

**Architecture:** 本单元是「同一张事实表加一列 + 两条写入路径 + 一条只读查询」。写入路径只有两处：① M2 创建投递写初始流转事实处（现落点 `app/intake/merge.py::_create_application`，由 U2 的 `effect_attach_resume_to_candidate` 调用）带上 `resume.source`；② 来源改正端点（`POST /api/resumes/{resume_id}/source`）在同一事务里追加一条 `source_corrected` 事实。两条路径都不新增 LangGraph 节点：入口是既有 HTTP 处理器与既有 effect 节点内部，不是新的可恢复重跑面，幂等由 U1 已落地的「同值早退」与 U2 既有的 effect 幂等键承担。`action` 列的 CHECK 在磁盘上已是 interview-scheduling U2 落地的五值版本，U3 需要第七个值 ⇒ SQLite 改不了 CHECK，必须**整表重建**（沿用 `_rebuild_stage_table` / `_rebuild_hr_account_role_check` / `_rebuild_application_status_check` 的既有先例），重建目标取七值并集，⛔ 不缩小任何既有约束。

**Tech Stack:** Python 3.14、FastAPI、SQLite（`app/storage/db.py` 的 `SCHEMA`/`_ADDED_COLUMNS` 双轨迁移 ＋ 整表重建迁移）、pydantic v2、pytest、httpx。无新增第三方依赖。

**Spec:**

- `openspec/changes/channel-resume-intake/specs/source-in-stage-history/spec.md`
- `openspec/changes/channel-resume-intake/design.md`（决策 D5；§Migration Plan 第 1 条；Open Questions OQ1–OQ7 仅标记、不阻塞）
- `openspec/changes/channel-resume-intake/tasks.md` 第 3 章（3.1–3.4，**仅用于确认 U3 章节边界，⛔ 不作为计划输入**）
- 上游参照：U1 实现真身（`app/intake/bundle.py`、`app/intake/source.py`、`app/storage/source.py`、`resume.source/source_origin`、`source_correction_log`、`POST /api/resumes/{id}/source`）＋ U2 计划 `docs/superpowers/plans/2026-10-10-channel-resume-intake-unit2-dedup-merge.md`（已在 main；U2 代码**分段落地中**——本计划引用的既有函数/列一律以**磁盘真身**为准，与 U2 计划字面表述冲突处以磁盘真身为准，冲突点见「架构决策」第 1、5、10 条）

## 需求覆盖表

| spec 能力（`source-in-stage-history`） | `### Requirement:` | 覆盖 Task |
|---|---|---|
| source-in-stage-history | 投递初始流转事实携带来源 | Task 1（`source` 列＋`action` CHECK 放宽）、Task 2（初始事实写入 `resume.source`）、Task 3（改正追加事实） |
| source-in-stage-history | 只记字段不做漏斗报表 | Task 1（字段落在既有事实表上）、Task 4（只读 `source_distribution(job_id)` ＋ 无报表页 404 反证） |
| （横切）合规断言与 CI 接入 | tasks.md 3.4 | Task 5 |

## Global Constraints

以下逐字摘自 `CLAUDE.md`「工程铁律」「合规红线」「部署约束」，与本交付单元的适用判断一并列出。**每个 Task 的验收隐含包含本节全部适用条目。**

1. **工程铁律 1**：LangGraph 恢复时节点从头整个重跑。每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。幂等记录与业务写必须在同一个事务里提交——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。✅ **适用，且本单元不新增 `effect_*` 节点**：两条写入路径都不在图的重跑面上——① 初始流转事实写在 U2 既有节点 `effect_attach_resume_to_candidate` 内部（同一事务、同一幂等键 `{resume_id}:effect_attach_resume_to_candidate:once`，U3 只往那条 INSERT 里多带一列）；② 来源改正走 HTTP 端点 `POST /api/resumes/{resume_id}/source`，它不在 LangGraph 里、没有 checkpoint 重放语义，幂等由「同值早退」承担（U1 落地的既有口径，与 `source_correction_log` 同源）。⛔ 不因为本单元而给改正端点新造一个 `effect_*` 节点：那会把一条 HTTP 事务路径改造成图节点，超出本单元边界。

2. **工程铁律 2**：L3 Agent 全部是无副作用纯函数，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。✅ **适用**：`source_distribution`（Task 4）是**只读**查询函数（⛔ 无 INSERT/UPDATE/DELETE、⛔ 无 commit），它是观测端不是写入端（与 `app/audit/assertions.py` 同一纪律）；Task 3 的 `append_source_correction` 不是 L3 Agent、也不是 `compute_*`，它是 HTTP 端点事务内的一次追加写（与 `app/storage/rejection.py`、`app/storage/appeal.py` 里同类流转事实写入同一性质）。

3. **工程铁律 3**：所有 AI 评分必须持久化：模型标识 + 模型版本 + prompt 版本 + temperature + 输入哈希 + rubric 快照 + 原始响应。✅ **适用，且本单元不新增评分**：来源是确定性字段（U1 的 `detect_source` 是纯规则，⛔ 不调模型），本单元不写 `criterion_score`、不调模型。

4. **工程铁律 4**：每条 `criterion_score` 必须有 `evidence_ref`（回指简历原文或面试 turn 的 offset）。`evidence_ref` 为空不允许写入。⛔ **不适用，理由**：本单元不写 `criterion_score`。

5. **工程铁律 5**：`temperature=0`；模型版本优先显式锁定，禁止 `latest` 类别名。供应商不提供带版本号快照时（如 DeepSeek 公开 API 只有 `deepseek-chat` 这类会漂移的别名），必须从 API 响应里取回实际的 `model` 字段并持久化——配置里写的名字不算数，响应返回的才算。⛔ **不适用，理由**：本单元无任何模型调用。

6. **工程铁律 6**：企微回调先落库再处理：只推一次、5 秒无响应即丢弃。回调接口只做签名校验 + 落库 + 返回 200。⛔ **不适用，理由**：本单元没有企微回调路径。

7. **工程铁律 7**：`langgraph >= 1.0.10`（GHSA-g48c-2wqr-h844）。✅ **适用，环境约束**：`requirements.txt` 已锁 `langgraph==1.0.10`，本单元不降级、不新增图节点。

8. **合规红线·AI 只做排序推荐，不做自动淘汰**：淘汰必须有人工确认节点并留痕。审计断言：`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。✅ **适用**：本单元只给投递加"来源"这一事实字段，⛔ 不淘汰任何投递、⛔ 不写 `rejection_record`、⛔ 不改 `application.status`/`current_stage_id`（Task 3 追加的事实 `from_stage_id=NULL`、`to_stage_id=` 该投递当前阶段，阶段不变）。

9. **合规红线·禁止人脸/表情分析**（《人脸识别技术应用安全管理办法》2025-06-01 施行）。声学情绪信号（语速/停顿/静默）只展示给面试官，不进 `criterion_score`。⛔ **不适用，理由**：本单元不处理任何影像/声学信号。

10. **合规红线·AI 生成的 JD、拒信、邀约须带标识**（《AI 生成合成内容标识办法》2025-09-01 施行）。⛔ **不适用，理由**：本单元不产出任何对外文本。

11. **合规红线·模型全部走境内，简历数据不出境**。✅ **适用**：本单元只读既有库内字段，⛔ 不把简历内容发给任何外部服务；Task 5 的断言 ①（无明文手机号列）是这条红线在存储层的结构守护。

12. **合规红线·绝不用历史录用结果做监督信号**（Amazon 2018 教训），只用显式岗位能力 rubric。⛔ **不适用，理由**：本单元无训练、无监督信号。

13. **合规红线·候选人入口一律用一次性邀请链接**，避免被认定"向境内公众提供"。⛔ **不适用，理由**：本单元是 HR 侧只读统计与表结构约束，不是候选人对外入口。

14. **合规红线·主观描述（"沟通能力强"）不得进入硬门槛规则**，只能作为软技能关键词。⛔ **不适用，理由**：本单元不做硬门槛判定。

15. **部署约束 1·路径前缀就绪**：FastAPI `root_path=/hr/recruit-agent`，前端资源与接口调用**一律相对路径**，禁止硬编码 `/static/…` `/api/…`。✅ **适用**：本单元不新增前端页面与静态资源（Task 4 的测试正是反证"报表页不存在"，所以没有任何新 HTML 需要处理相对路径）；Task 3 只改既有 API 端点的内部逻辑，路由前缀由 `router` 统一承载。

16. **部署约束 4·目标服务器是 Windows，没有 Docker**，部署形态 = Python venv + Windows 计划任务，不引入容器。✅ **适用**：本单元零新增第三方依赖，只用标准库 `json`/`uuid`/`sqlite3`。

17. **部署约束 5·M2 起处理真实简历前必须具备可识别到人的登录 + 简历访问留痕**（PIPL 要求"谁在什么时候看了谁的简历"可查），共享口令不满足。✅ **适用，但本单元不新增真实简历处理能力**：Task 3 的 `actor` 取自既有的 `reviewer_of(request)`（可识别到人的登录会话），本单元不改入库闸、不改访问留痕。

18. **SQLite CHECK 不可 ALTER（本仓库既有工程结论，非 CLAUDE.md 条目）**：`stage`/`hr_account.role`/`application.status` 三处先例都证明"放宽 CHECK 只能整表重建"。✅ **适用**：Task 1 的 `action` CHECK 放宽走整表重建，⛔ 不得改成"新库写七值、老库不管"——那会让 `.51` 的 demo.db 永远写不进 `source_corrected`，且不报错（正是 `_migrate_stage_offer_hired` 注释里点名的故障形态）。

## 架构决策（先读，避免和 `tasks.md`/`design.md` 的字面表述对不上）

**1. `action` 列的 CHECK 在磁盘上不是"无约束"，必须整表重建放宽（与 U2 计划的字面写法冲突，以磁盘真身为准）。**
`app/storage/db.py` 现在（磁盘真身）是 `action TEXT CHECK (action IS NULL OR action IN ('scheduled','rescheduled','cancelled','completed','no_show'))`，由 interview-scheduling U2 落地并**被测试钉住**——`tests/test_db_m2_schema.py::test_application_stage_history_action_check` 要求 `'rescheduled_by_ai'` 被拒、五个排期动作放行；`tests/test_db_migration.py::test_legacy_application_stage_history_action_check_survives_migration` 要求老库 ALTER 出来的 action 也带同一 CHECK。
U2 计划（`2026-10-10-…-unit2-dedup-merge.md` Task 4 Step 2/4）写的是 `action TEXT`（**无 CHECK**）——那是 U2 计划成文时 `action` 列还不存在的口径，现已与磁盘不符。**本计划以磁盘真身为准**：SCHEMA 与 `_ADDED_COLUMNS` 里的 CHECK **保留并扩宽**到七值（五值 + U2 要用的 `closed_by_merge` + U3 要用的 `source_corrected`），⛔ 不退化成无 CHECK 的 `action TEXT`。执行 U2 Task 4 时同样⛔ 不要把它退化——退化的代价是 `test_application_stage_history_action_check` 当场变红，且"多一个取值就说明有别的东西在往流转事实表里写"这条护栏消失。

**2. 七值并集是"前向兼容"的，不假设 U2 先落还是后落。**
重建目标 CHECK = `('scheduled','rescheduled','cancelled','completed','no_show','closed_by_merge','source_corrected')`。它同时满足：U3 写 `source_corrected`、U2 Task 5 写 `closed_by_merge`、interview-scheduling 的五个排期动作与 `NULL` 全部照旧放行。同一份取值清单出现在三处——SCHEMA 的 `CREATE TABLE`、`_ADDED_COLUMNS` 的 `ADD COLUMN`、重建迁移的 `CREATE TABLE … _new`——由 Task 1 的测试同时钉住三处（⛔ 不要再抄第四份）。

**3. 重建迁移只在"需要且能重建"时动手。**
`_stage_history_action_check_width_ok(conn)` 返回 True（= 不需要重建）的四种形态，都必须放行，否则重建会当场炸或白改 DDL：① 表不存在；② `sqlite_master.sql` 里已经出现 `'source_corrected'`（本迁移跑过了，或 SCHEMA 本就七值）；③ 表的列集合不包含 `_STAGE_HISTORY_COLUMNS` 全集——形状不是本表的正常形态（`tests/test_db_m2_u2_schema.py` 等用例会造只有 `id` 的空壳表，重建的 `INSERT … SELECT` 会撞 `no such column`）；④ `action` 列上没有 CHECK（此时任何值都放行，重建只会白改 DDL；U2 Task 4 若按它计划的 `action TEXT` 落地，命中的就是这一支）。
**老库路径**（`.51` 的 demo.db、`tests/test_db_migration.py::_legacy_db`、`tests/test_db_m2_schema.py::_legacy_db`、`tests/fixtures/zp51_demo_db_schema_pre_m3.sql`）全部命中重建；**新库**（SCHEMA 本就是七值）空转。

**4. `source` 列可空、无默认值，"没有来源"用 `NULL` 表达。**
`application_stage_history.source TEXT`（可空）。既有历史行（M2 建的 15 个 job 的流转事实、排期流转事实）来源一律 `NULL`。读取方按 `unknown` 解释 `NULL`——与 U1 的 `resume.source`/`candidate_source()` 同一口径。⛔ 不给默认值 `'unknown'`：那会让"这条事实来自某一个具体渠道"和"这条事实根本没有来源信息"在数据层不可区分。

**5. 初始流转事实的写入落点是 `app/intake/merge.py::_create_application`，U3 只往里多带一列。**
磁盘上"创建投递 + 写初始流转事实"现在只有一处：`app/intake/merge.py::_create_application`（由 U2 的 `effect_attach_resume_to_candidate` 调用；`app/storage/rejection.py`/`appeal.py` 只写阶段流转，不是创建路径）。U3 在同一个函数里 `SELECT source FROM resume WHERE id = ?` 并把值放进那条 INSERT。⚠️ **假设已写清**：若执行本计划时 U2 又重构了这一处（例如 `_create_application` 改名或搬到别处），按"创建 application 的同一条 INSERT 边上写初始流转事实"这条语义定位现场代码，⛔ 不要因为函数改名就跳过这一 Task。`tasks.md` 3.1 的"M2 创建投递写初始流转事实处"指的就是这一处。

**6. 来源改正的追加事实写在 HTTP 端点事务内，`from_stage_id=NULL`、`to_stage_id=` 该投递当前阶段。**
`application_stage_history.to_stage_id` 是 `NOT NULL`，而来源改正**不改阶段**，所以取该投递当前的 `current_stage_id`；`from_stage_id` 留 `NULL`（"没有从哪个阶段来"，与创建投递的初始事实同一手法，语义靠 `action='source_corrected'` 区分）。这条事实**不是**阶段流转，读报表的人按 `action` 过滤即可把"来源改正"与"阶段流转"分开。

**7. 该简历还没有投递时，改正照常生效，但不写流转事实。**
`_ingest_one_resume` 在"解析不出（unreadable）/解析失败（parse_failed）"时会 `return` 在 `effect_attach_resume_to_candidate` **之前**——这类简历没有 `application` 行。此时来源改正仍要成功（`resume.source` + `source_correction_log` 由端点写），只是没有投递可挂事实：`append_source_correction` 返回 `None`、⛔ 不写任何行。测试覆盖见 Task 3 Step 3 的 `test_correction_without_application_skips_the_fact`。

**8. `source_distribution` 是只读函数，不是接口、不是页面。**
spec「只记字段不做漏斗报表」+ design D5「⛔ 不建报表页」：本单元**不新增任何路由**（既无 HTML 页也无 JSON API），只提供 `app/storage/source.py::source_distribution(conn, job_id) -> dict[str, int]`。Task 4 的测试用 HTTP 客户端反证"报表页不存在（404）"——这是 spec 那条 Requirement 的机器判据，⛔ 不要为了"让函数有调用方"顺手加一个页面或接口。

**9. 每条投递恰好计一次：取它最新一条带 `source` 的流转事实。**
来源改正追加的新事实会自然覆盖初始记录的值（所以"改正后分布跟着搬"），没有带 `source` 事实的投递计 `unknown`。实现用相关子查询（`ORDER BY occurred_at DESC, rowid DESC LIMIT 1`），⛔ 不用 `COUNT(*) … GROUP BY` 直接对事实表计数——那会把"一条投递的初始事实 + 一条改正事实"算成两票。

**10. 审计断言 ①②③ 的"缺表/缺列"一律 fail-closed，与既有断言同一口径。**
① `candidate` 无明文手机号列（并钉住 `resume` 的来源列集合）；② `candidate_merge_log` 每行 `merged_by` 非空；③ `candidate.merged_into` 非空的候选人无活跃投递。
② 依赖 `candidate_merge_log`——该表由 **U2 Task 4** 建，**磁盘上此刻还不存在**。若 U3 先落地，断言 ② 会在干净库上红（表真的不在，不是"还没到时候"）。为让 U3 独立可验收，Task 5 的第一个 Step 按 U2 计划 Task 4 Step 3 的**逐字 DDL** 幂等兜底建表（`CREATE TABLE IF NOT EXISTS` + 两个 `CREATE INDEX IF NOT EXISTS`）；U2 Task 4 若已落地，它是空转。⛔ 两侧 DDL 必须逐字一致：U2 那边**任何**对 `candidate_merge_log` DDL 的改动都要同步到 Task 5 Step 1（这条写在 Task 5 的注释里，供后落地的一侧读到）。

**11. CI 接入＝进 `COMPLIANCE_ASSERTIONS` ＋ 反证测试带 `compliance` 标记，⛔ 不改 `.github/workflows/ci.yml`。**
`ci.yml` 的 test job 跑 `python -m pytest -q`（全量）与 `python -m pytest -q -m compliance -v`（可归因的红线门禁）两步，新增断言一旦进 `COMPLIANCE_ASSERTIONS` 并配 `pytestmark = pytest.mark.compliance` 的反证，就被两步同时覆盖。⛔ 不去改 `ci.yml` 的步骤（那条注释里的"22 条"会随新增用例变旧，但不改 ci.yml 的步骤结构是刻意的——本包同期还有别的泳道在跑，改 CI 文件的冲突面最大而收益为零）。

## File Structure

| 文件 | 责任 |
|---|---|
| `app/storage/db.py`（改） | `application_stage_history` 加 `source` 列（SCHEMA ＋ `_ADDED_COLUMNS`）；`action` CHECK 扩到七值（SCHEMA ＋ `_ADDED_COLUMNS`）；新增 `_stage_history_action_check_width_ok` / `_rebuild_stage_history_action_check` 并接进 `init_schema` |
| `app/intake/merge.py`（改） | `_create_application` 的初始流转事实带上 `resume.source` |
| `app/intake/stage_history.py`（新建） | `append_source_correction(conn, *, resume_id, from_source, to_source, actor)`：追加一条 `source_corrected` 事实 |
| `app/web/server.py`（改） | `correct_resume_source` 在同一事务里调用 `append_source_correction` |
| `app/storage/source.py`（改） | 新增只读 `source_distribution(conn, job_id) -> dict[str, int]` |
| `app/audit/assertions.py`（改） | 新增三条断言 ①②③ ＋ 进 `COMPLIANCE_ASSERTIONS`（4 → 7） |
| `tests/test_stage_history_source_schema.py`（新建） | Task 1 测试 |
| `tests/test_stage_history_initial_source.py`（新建） | Task 2 测试 |
| `tests/test_source_correction_fact.py`（新建） | Task 3 测试 |
| `tests/test_source_distribution.py`（新建） | Task 4 测试 |
| `tests/test_channel_audit_assertions.py`（新建） | Task 5 正向/缺表口径测试 |
| `tests/test_audit_assertions.py`（改） | 断言条数 4 → 7 |
| `tests/test_audit_assertion_effectiveness.py`（改） | 三条新断言的反证（造违例 → 必须失败） |
| `tests/test_db_m2_schema.py`（改） | 钉住的列集合加 `source`；`action` 合法值清单加 `closed_by_merge`/`source_corrected` |

---

### Task 1: 数据模型——`application_stage_history.source` 与 `action` CHECK 扩到七值

**Files:**

- Modify: `app/storage/db.py`
- Modify: `tests/test_db_m2_schema.py`
- Test: `tests/test_stage_history_source_schema.py`（新建）

**Interfaces:**

- Schema: `application_stage_history.source TEXT`（可空，走 `_ADDED_COLUMNS`）
- Schema: `application_stage_history.action` 的 CHECK 由五值放宽到七值（`+closed_by_merge` `+source_corrected`）
- Produces: `_stage_history_action_check_width_ok(conn) -> bool`、`_rebuild_stage_history_action_check(conn) -> None`（由 `init_schema` 按既有顺序调用）

- [ ] **Step 1: SCHEMA 的 `application_stage_history` 加 `source` 列并放宽 `action` 的 CHECK**

在 `app/storage/db.py` 里，把 `application_stage_history` 的 `CREATE TABLE` 整段（含它上方到 `-- action / detail_json（…）` 那段注释）替换为：

```sql
-- 流转事实表：所有报表的基础（CLAUDE.md 数据模型要点）。actor_type 区分
-- 人工流转与系统流转（申诉 overturned 恢复阶段、批量确认淘汰流转都会写这里）。
-- from_stage_id 允许 NULL：投递创建时的第一条"进入 initial"没有"从哪来"。
-- action / detail_json（interview-scheduling U2，偏离登记 D-U2-1）：四个排期
-- effect_* 节点把「安排/改期/取消/完成/未出席」作为流转事实写进本表时，阶段
-- 不变（from_stage_id = to_stage_id = 'interview'），靠 action 区分动作、
-- detail_json 存改期的原/新时刻与取消原因。既有 stage 流转行没有动作语义，
-- 故 action 可空。本表是 M2 已建老表，三列（action/detail_json/source）必须
-- 同时登记 SCHEMA 与 _ADDED_COLUMNS（与 hr_account.role 同一先例）。
--
-- source（channel-resume-intake U3 tasks 3.1/3.2）：投递创建时那条初始事实
-- 带上该简历的来源（resume.source）；HR 事后改正来源时**追加**一条
-- action='source_corrected' 的事实（source 记新值），⛔ 绝不更新初始记录
-- （design D5：本表是只追加的事实表，改写会破坏"所有报表的基础"这个前提）。
-- 可空、无默认值：既有历史行与排期事实一律 NULL，读取方按 unknown 解释
-- （与 resume.source / candidate_source() 同一口径）。
--
-- action 的 CHECK 是七值并集：五值是 interview-scheduling U2 的排期动作，
-- closed_by_merge 属 channel-resume-intake U2（合并掉的重复投递），
-- source_corrected 属 U3。⛔ 三处（本 CREATE TABLE、_ADDED_COLUMNS 的那条
-- ADD COLUMN、_rebuild_stage_history_action_check 的重建目标）必须逐字一致，
-- 由 tests/test_stage_history_source_schema.py 同时钉住。
CREATE TABLE IF NOT EXISTS application_stage_history (
    id TEXT PRIMARY KEY NOT NULL,
    application_id TEXT NOT NULL REFERENCES application(id),
    from_stage_id TEXT REFERENCES stage(id),
    to_stage_id TEXT NOT NULL REFERENCES stage(id),
    actor_type TEXT NOT NULL CHECK (actor_type IN ('human', 'agent')),
    actor TEXT,
    action TEXT CHECK (
        action IS NULL OR action IN (
            'scheduled', 'rescheduled', 'cancelled', 'completed', 'no_show',
            'closed_by_merge', 'source_corrected'
        )
    ),
    detail_json TEXT,
    source TEXT,
    occurred_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_application_stage_history_application
    ON application_stage_history (application_id);
```

- [ ] **Step 2: `_ADDED_COLUMNS` 里把 `action` 那条的 DDL 改成七值、并追加 `source`**

在 `app/storage/db.py` 的 `_ADDED_COLUMNS` 元组**末尾**，把现有的这两行

```python
    ("application_stage_history", "action",
     "TEXT CHECK (action IS NULL OR action IN ('scheduled', 'rescheduled', 'cancelled', 'completed', 'no_show'))"),
    ("application_stage_history", "detail_json", "TEXT"),
```

替换为：

```python
    # channel-resume-intake U3 tasks 3.1：action 的 CHECK 扩到七值（+合并/来源
    # 改正两个动作）。⚠️ 这一行只对"老库里连 action 列都没有"的库生效（.51 现网
    # 的 demo.db 已经有五值 CHECK 的 action 列，apply_column_migrations 会因
    # "列已存在"跳过它）——那种老库靠 init_schema 里的
    # _rebuild_stage_history_action_check 整表重建放宽。两条路径的最终 CHECK 必须
    # 一致，由 tests/test_stage_history_source_schema.py 与
    # tests/test_db_migration.py 的漂移守卫共同盯住。
    ("application_stage_history", "action",
     "TEXT CHECK (action IS NULL OR action IN ('scheduled', 'rescheduled', 'cancelled', 'completed', 'no_show', 'closed_by_merge', 'source_corrected'))"),
    ("application_stage_history", "detail_json", "TEXT"),
    # channel-resume-intake U3 tasks 3.1：流转事实带上来源（可空，无默认值——
    # "没有来源信息"与"来源是 unknown"在数据层必须可区分，见本计划架构决策 4）。
    ("application_stage_history", "source", "TEXT"),
```

- [ ] **Step 3: 新增重建迁移的两个函数**

在 `app/storage/db.py` 里 `_rebuild_application_status_check` 函数**之后**、`def init_schema` **之前**插入：

```python
# channel-resume-intake U3 tasks 3.1：application_stage_history.action 的合法取值。
# ⛔ 与 SCHEMA 的 CREATE TABLE、_ADDED_COLUMNS 的那条 ADD COLUMN 逐字同源
# （三处一份清单）；_rebuild_stage_history_action_check 用它拼重建目标。
_STAGE_HISTORY_ACTIONS: tuple[str, ...] = (
    "scheduled",
    "rescheduled",
    "cancelled",
    "completed",
    "no_show",
    "closed_by_merge",
    "source_corrected",
)

# 重建时要原样搬过去的列。⛔ 与 SCHEMA 的列集合逐字同源。
_STAGE_HISTORY_COLUMNS: tuple[str, ...] = (
    "id",
    "application_id",
    "from_stage_id",
    "to_stage_id",
    "actor_type",
    "actor",
    "action",
    "detail_json",
    "source",
    "occurred_at",
)

# action 列上"有没有 CHECK"的判据。手法与 _APPLICATION_STATUS_CHECK_RE 一致：
# 看 sqlite_master 的 DDL 原文。\baction\b 不会误命中 actor_type。
_ACTION_CHECK_RE = re.compile(r"\baction\b[^,]*?\bCHECK\b", re.IGNORECASE)


def _stage_history_action_check_width_ok(conn: sqlite3.Connection) -> bool:
    """application_stage_history.action 的 CHECK 是否已经放行 source_corrected
    （即无需整表重建）。四种"不需要重建"的形态都必须放行，否则重建会当场炸
    或白改 DDL：

    - 表不存在（新库在 SCHEMA 之后这里恒不成立；测试空壳场景可能出现）；
    - DDL 原文里已经出现 'source_corrected'（本迁移已经跑过，或 SCHEMA 本就七值）；
    - 表的列集合不含 _STAGE_HISTORY_COLUMNS 全集——形状不是本表的正常形态
      （tests/test_db_m2_u2_schema.py 造的是只有 id 的空壳表），重建的
      `INSERT … SELECT` 会撞 `no such column: from_stage_id`；
    - action 列上没有 CHECK——没有任何取值被拒，重建只会白改 DDL（U2 Task 4
      若按它计划的 `action TEXT` 落地，命中的就是这一支）。
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='application_stage_history'"
    ).fetchone()
    if row is None or not row[0]:
        return True
    ddl = row[0]
    if "source_corrected" in ddl:
        return True
    if not set(_STAGE_HISTORY_COLUMNS) <= _existing_columns(
        conn, "application_stage_history"
    ):
        return True
    return not _ACTION_CHECK_RE.search(ddl)


def _rebuild_stage_history_action_check(conn: sqlite3.Connection) -> None:
    """把 application_stage_history.action 的 CHECK 从五值放宽到七值。

    SQLite 无法用 ALTER TABLE 修改 CHECK（与 _rebuild_stage_table /
    _rebuild_hr_account_role_check / _rebuild_application_status_check 同一
    结论）：`.51` 现网的 demo.db 里 action 列已存在且带五值 CHECK，
    _ADDED_COLUMNS 因"列已存在"跳过，U3 的 source_corrected 会被旧 CHECK 静默
    拒掉（不报错、行不出现）——只能整表重建。

    行级数据原样搬（含 detail_json），一条不丢；两个前置条件必须成立：
      · 在调用点之前 apply_column_migrations 已经跑过，所以 source 列一定存在；
      · _stage_history_action_check_width_ok 已排除形状异常的表。
    DROP TABLE 会连带删掉 idx_application_stage_history_application，重建后
    必须原样补回。PRAGMA 在事务内是 no-op，try 内 commit、except 里 rollback
    之后，finally 再重开（同 _rebuild_hr_account_role_check）。
    """
    if _stage_history_action_check_width_ok(conn):
        return
    actions_sql = ", ".join(repr(action) for action in _STAGE_HISTORY_ACTIONS)
    columns_sql = ", ".join(_STAGE_HISTORY_COLUMNS)
    conn.commit()  # 事务外才能切 PRAGMA
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN")
        conn.execute(
            f"""
            CREATE TABLE application_stage_history_new (
                id TEXT PRIMARY KEY NOT NULL,
                application_id TEXT NOT NULL REFERENCES application(id),
                from_stage_id TEXT REFERENCES stage(id),
                to_stage_id TEXT NOT NULL REFERENCES stage(id),
                actor_type TEXT NOT NULL CHECK (actor_type IN ('human', 'agent')),
                actor TEXT,
                action TEXT CHECK (action IS NULL OR action IN ({actions_sql})),
                detail_json TEXT,
                source TEXT,
                occurred_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            f"INSERT INTO application_stage_history_new ({columns_sql}) "
            f"SELECT {columns_sql} FROM application_stage_history"
        )
        conn.execute("DROP TABLE application_stage_history")
        conn.execute(
            "ALTER TABLE application_stage_history_new RENAME TO application_stage_history"
        )
        conn.execute(
            "CREATE INDEX idx_application_stage_history_application "
            "ON application_stage_history (application_id)"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        # ⛔ PRAGMA 在事务内是 no-op：必须等上面 commit/rollback 关掉事务后再重开，
        # 否则连接的外键强制会被留在 OFF（1001O seg2 Spec review 实测 FAIL 的根因）。
        conn.execute("PRAGMA foreign_keys = ON")
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise sqlite3.IntegrityError(
            f"application_stage_history 重建后外键不一致: {violations}"
        )
```

- [ ] **Step 4: 把重建迁移接进 `init_schema`**

在 `app/storage/db.py` 的 `init_schema` 里，`_rebuild_application_status_check(conn)` 之后、`_migrate_stage_for_interview(conn)` 之前插入一行：

```python
    # channel-resume-intake U3 tasks 3.1：action 的 CHECK 需要放行
    # source_corrected。⚠️ 必须排在 apply_column_migrations 之后——老库里
    # action 列可能是刚刚才 ALTER 出来的，重建要连它一起搬（source 列同理）。
    # 新库 SCHEMA 本就是七值 ⇒ 空转。
    _rebuild_stage_history_action_check(conn)
```

- [ ] **Step 5: 更新 `tests/test_db_m2_schema.py` 里两处钉住的断言**

① 列集合（`test_application_stage_history_table_exists_with_expected_columns`）的断言语改为：

```python
def test_application_stage_history_table_exists_with_expected_columns(conn):
    assert _table_exists(conn, "application_stage_history")
    # action/detail_json 由 interview-scheduling U2 task 1 加入（排期流转事实的
    # 动作与详情，见偏离登记 D-U2-1）；source 由 channel-resume-intake U3 task 3.1
    # 加入（投递初始事实携带来源）。新库走 CREATE TABLE、老库走 _ADDED_COLUMNS，
    # 两条路径的列集合必须一致。
    assert _columns(conn, "application_stage_history") == {
        "id", "application_id", "from_stage_id", "to_stage_id",
        "actor_type", "actor", "action", "detail_json", "source", "occurred_at",
    }
```

② `test_application_stage_history_action_check` 的 docstring 与合法值循环改为：

```python
    """action 的值域由 CHECK 钉死：五个排期动作（interview-scheduling U2 task 1，
    偏离登记 D-U2-1）+ channel-resume-intake 的 closed_by_merge（U2）/
    source_corrected（U3），NULL 放行（既有 stage 流转行没有动作语义），别的一律
    拒——多一个取值就说明有别的东西在往流转事实表里写。
    """
    _seed_job_candidate_resume(conn)
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app-1', 'c1', 'j1', 'r1', 'initial')"
    )
    for action in (
        "scheduled", "rescheduled", "cancelled", "completed", "no_show",
        "closed_by_merge", "source_corrected", None,
    ):
        conn.execute(
            "INSERT INTO application_stage_history "
            "(id, application_id, to_stage_id, actor_type, actor, action) "
            "VALUES (?, 'app-1', 'interview', 'human', 'hr-1', ?)",
            (f"h-{action}", action),
        )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO application_stage_history "
            "(id, application_id, to_stage_id, actor_type, actor, action) "
            "VALUES ('h-bad', 'app-1', 'interview', 'human', 'hr-1', 'rescheduled_by_ai')"
        )
```

- [ ] **Step 6: 创建 `tests/test_stage_history_source_schema.py`**

```python
"""U3 tasks 3.1：流转事实表的来源列与 action CHECK 的七值放宽。

⚠️ 这是"两条建库路径"的交叉守护：新库（SCHEMA 的 CREATE TABLE）／老库
（_ADDED_COLUMNS 的 ADD COLUMN）／老库且 action 列已带五值 CHECK（整表重建）。
三者最终必须给出同一个 CHECK，且重建不能丢行、不能丢索引。
"""
from __future__ import annotations

import sqlite3

import pytest

from app.storage.db import (
    _ADDED_COLUMNS,
    _STAGE_HISTORY_ACTIONS,
    _existing_columns,
    init_schema,
)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    init_schema(c)
    return c


def _stage_parents(conn: sqlite3.Connection) -> str:
    """建 application_stage_history 的外键父行，返回一个可用的 application_id。

    ⚠️ 必须真的建：_rebuild_stage_history_action_check 结束时会跑
    PRAGMA foreign_key_check，父行缺失会让迁移自己抛错（这正是我们要的严格性，
    测试夹具得先满足它）。
    """
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT INTO resume "
        "(id, job_id, sample_class, file_name, content_sha256, uploaded_by, source) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'h1', 'alice', 'boss')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app-1', 'c1', 'j1', 'r1', 'initial')"
    )
    conn.commit()
    return "app-1"


_OLD_FIVE_VALUE_DDL = """
DROP TABLE application_stage_history;
CREATE TABLE application_stage_history (
    id TEXT PRIMARY KEY NOT NULL,
    application_id TEXT NOT NULL REFERENCES application(id),
    from_stage_id TEXT REFERENCES stage(id),
    to_stage_id TEXT NOT NULL REFERENCES stage(id),
    actor_type TEXT NOT NULL CHECK (actor_type IN ('human', 'agent')),
    actor TEXT,
    action TEXT CHECK (
        action IS NULL OR action IN ('scheduled', 'rescheduled', 'cancelled', 'completed', 'no_show')
    ),
    detail_json TEXT,
    occurred_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_application_stage_history_application
    ON application_stage_history (application_id);
"""


def test_fresh_stage_history_has_source_column():
    conn = _conn()
    assert "source" in _existing_columns(conn, "application_stage_history")


def test_source_is_registered_for_the_add_column_path():
    """老库路径：source 必须登记在 _ADDED_COLUMNS 里，否则 .51 的 demo.db 永远
    不会有这一列，而 CREATE TABLE IF NOT EXISTS 对已存在的表是彻底 no-op。"""
    assert ("application_stage_history", "source") in {
        (table, column) for table, column, _ddl in _ADDED_COLUMNS
    }


def test_action_check_accepts_every_listed_action_and_null():
    conn = _conn()
    app_id = _stage_parents(conn)
    for action in list(_STAGE_HISTORY_ACTIONS) + [None]:
        conn.execute(
            "INSERT INTO application_stage_history "
            "(id, application_id, to_stage_id, actor_type, action) "
            "VALUES (?, ?, 'initial', 'agent', ?)",
            (f"h-{action}", app_id, action),
        )
    conn.commit()
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history"
    ).fetchone()[0] == len(_STAGE_HISTORY_ACTIONS) + 1


def test_action_check_still_rejects_unknown_actions():
    conn = _conn()
    app_id = _stage_parents(conn)
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO application_stage_history "
            "(id, application_id, to_stage_id, actor_type, action) "
            "VALUES ('h-bad', ?, 'initial', 'agent', 'promoted')",
            (app_id,),
        )


def test_legacy_five_value_check_is_rebuilt_without_losing_rows():
    """`.51` 现网的形态：action 列已存在且是五值 CHECK，source 列还不存在。
    整表重建后：CHECK 放宽到七值、历史行一条不丢、索引补回、source 列就位。"""
    conn = _conn()
    app_id = _stage_parents(conn)
    conn.executescript(_OLD_FIVE_VALUE_DDL)
    conn.execute(
        "INSERT INTO application_stage_history "
        "(id, application_id, to_stage_id, actor_type, action, detail_json) "
        "VALUES ('h-old', ?, 'initial', 'human', 'rescheduled', '{\"to\": \"2026-10-12\"}')",
        (app_id,),
    )
    conn.commit()
    assert "source" not in _existing_columns(conn, "application_stage_history")

    init_schema(conn)

    assert "source" in _existing_columns(conn, "application_stage_history")
    row = conn.execute(
        "SELECT application_id, action, detail_json, source FROM application_stage_history "
        "WHERE id = 'h-old'"
    ).fetchone()
    assert row == (app_id, "rescheduled", '{"to": "2026-10-12"}', None)
    # 索引随 DROP TABLE 一起消失过，必须补回。
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' "
        "AND name='idx_application_stage_history_application'"
    ).fetchone() is not None
    # 旧 CHECK 拒掉的值现在放行；不在七值里的照样拒。
    conn.execute(
        "INSERT INTO application_stage_history "
        "(id, application_id, to_stage_id, actor_type, action, source) "
        "VALUES ('h-new', ?, 'initial', 'human', 'source_corrected', 'referral')",
        (app_id,),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO application_stage_history "
            "(id, application_id, to_stage_id, actor_type, action) "
            "VALUES ('h-bad', ?, 'initial', 'human', 'promoted')",
            (app_id,),
        )


def test_init_schema_is_idempotent_after_the_rebuild():
    """重建是幂等的：第二次 init_schema 不再重建（DDL 里已有 source_corrected），
    也不报错。"""
    conn = _conn()
    app_id = _stage_parents(conn)
    conn.executescript(_OLD_FIVE_VALUE_DDL)
    conn.execute(
        "INSERT INTO application_stage_history "
        "(id, application_id, to_stage_id, actor_type, action) "
        "VALUES ('h-old', ?, 'initial', 'agent', 'no_show')",
        (app_id,),
    )
    conn.commit()

    init_schema(conn)
    ddl_after_first = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='application_stage_history'"
    ).fetchone()[0]
    init_schema(conn)
    ddl_after_second = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='application_stage_history'"
    ).fetchone()[0]

    assert ddl_after_first == ddl_after_second
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history"
    ).fetchone()[0] == 1
```

- [ ] **Step 7: 验证**

```bash
python -m pytest tests/test_stage_history_source_schema.py tests/test_db_m2_schema.py tests/test_db_migration.py tests/test_db_m3_schema.py -q
```

预期输出：全部 passed（新增 6 条 + 既有钉列/迁移守卫），exit code 0。
⚠️ 本步骤最容易假绿：`tests/test_db_migration.py` 的漂移守卫（`_DRIFT_GUARDED_TABLES` 已含 `application_stage_history`）、`tests/test_db_m2_schema.py` 的 `_M2_U1_NEW_TABLES` 与钉列断言、`tests/test_db_m3_schema.py` 的 legacy 升级断言三者必须同时绿——它们分别盯"老库补列""新老列集合一致""重建不丢行"。

> **落地说明 D-U3-1a（与计划字面的三处偏差，均以磁盘真身为准；⛔ 无需返工）。**
>
> ① **净增比计划字面窄**：计划成文时 U2 尚未合入（磁盘是"五值、无重建函数"），
> 落地时 U2 已合入 main——`closed_by_merge` 与 `_rebuild_application_stage_history_
> action_check` 早就位。所以本 Task 的实际净增只有两样：`STAGE_HISTORY_ACTIONS`
> 追加 `source_corrected`（六值 → 七值）、`source` 列（SCHEMA ＋ `_ADDED_COLUMNS`
> ＋ 重建 DDL/`INSERT … SELECT`）。计划 Step 1/2/3/4 的其余内容在磁盘上已存在，
> 不重复落地。
>
> ② **命名沿用磁盘真身，⛔ 不造第二套名字**：计划 Interfaces 段写的
> `_STAGE_HISTORY_ACTIONS` / `_stage_history_action_check_width_ok` /
> `_rebuild_stage_history_action_check` 在磁盘上分别是公开常量
> `STAGE_HISTORY_ACTIONS`（已被 `tests/test_candidate_merge_schema.py`、
> `tests/test_db_migration.py` 引用）与 `_stage_history_action_check_is_current` /
> `_rebuild_application_stage_history_action_check`（U2 落地）。同一概念两套名字会
> 破坏"取值清单只有一处真源"，故新测试文件 `tests/test_stage_history_source_schema.py`
> 直接引用磁盘真身名，并在其模块 docstring 里写清这条偏差。新增的
> `_STAGE_HISTORY_COLUMNS` / `_ACTION_CHECK_RE` 两个符号按计划字面名落地。
>
> ③ **判据函数顺带补齐计划架构决策 3 的另两支早退**（原函数只有"表不存在"与
> "action 列不存在"两支）：现在是「表不存在 ／ 列集合不含 `_STAGE_HISTORY_COLUMNS`
> 全集（空壳表，重建会撞 `no such column`）／ action 列上没有 CHECK（无值被拒，
> 重建是白改 DDL）」三支早退 ＋ 值清单齐全判定。重建的列清单与 `INSERT … SELECT`
> 改为从 `_STAGE_HISTORY_COLUMNS` 生成（此前是硬编码 set ＋ 手写列名）。
>
> **验证证据**：`tests/test_stage_history_source_schema.py tests/test_db_m2_schema.py
> tests/test_db_m3_schema.py tests/test_db_migration.py -q` ⇒ **174 passed**（新增 7
> 条：计划 6 条 + 一条 M2 U1 形态老库的交叉守护）；全量 `pytest -q` ⇒
> **4343 passed, 12 skipped, 1 failed**，唯一失败是同源环境观察项
> `tests/test_commit_launcher.py::test_red_doc_size_test_rejects_before_commit`
> （泳道 worktree 无 venv ⇒ 体积闸被跳过；已在 HEAD 净土副本复现，与 D-U2-11a
> 同一观察项，⛔ 不在本任务里修）。另用 `tests/fixtures/zp51_demo_db_schema_pre_m3.sql`
> 做端到端复刻：迁移后 `source` 列在、CHECK 含 `source_corrected`、既有事实行数与
> `detail_json` 原样保留、索引补回、`PRAGMA foreign_key_check` 空且
> `foreign_keys` 已重开、重复 `init_schema` 行数不变。

---

### Task 2: 投递的初始流转事实带上 `resume.source`

**Files:**

- Modify: `app/intake/merge.py`
- Test: `tests/test_stage_history_initial_source.py`（新建）

**Interfaces:**

- Consumes: `application_stage_history.source`（Task 1）
- Produces: `_create_application(conn, *, candidate_id, job_id, resume_id) -> str` 行为变更（写入的初始事实带上 `resume.source`），**签名不变**

- [ ] **Step 1: `_create_application` 带上来源**

在 `app/intake/merge.py` 里，把 `_create_application` 整个函数替换为：

```python
def _create_application(
    conn: sqlite3.Connection, *, candidate_id: str, job_id: str, resume_id: str
) -> str:
    application_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, ?, ?, ?, 'initial')",
        (application_id, candidate_id, job_id, resume_id),
    )
    # channel-resume-intake U3 task 3.1：投递创建的初始流转事实带上来源
    # （resume.source）。来源改正走"追加"而不是"改写"（task 3.2，见
    # app/intake/stage_history.py），所以这条初始行此后永不更新。
    # 未标来源的简历（U1 的单文件上传路径会留 NULL）原样落 NULL——与
    # candidate_source() 的「NULL 视为 unknown」同一口径，⛔ 不回填 'unknown'。
    source_row = conn.execute(
        "SELECT source FROM resume WHERE id = ?", (resume_id,)
    ).fetchone()
    conn.execute(
        "INSERT INTO application_stage_history "
        "(id, application_id, from_stage_id, to_stage_id, actor_type, source) "
        "VALUES (?, ?, NULL, 'initial', 'agent', ?)",
        (str(uuid.uuid4()), application_id, source_row[0] if source_row else None),
    )
    return application_id
```

- [ ] **Step 2: 创建 `tests/test_stage_history_initial_source.py`**

```python
"""U3 tasks 3.1：投递创建的初始流转事实携带来源。

两条路径都验：① 直接调 effect_attach_resume_to_candidate（单元级）；
② 走 POST /api/resumes/upload 的 ZIP 分支（端到端，来源由 U1 的确定性规则识别）。
"""
from __future__ import annotations

import io
import sqlite3
import zipfile

import docx

from app.intake.merge import effect_attach_resume_to_candidate
from app.storage.auth_session import create_session
from app.storage.db import init_schema
from app.storage.hr_account import upsert_account


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    init_schema(c)
    c.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    return c


def _resume(conn: sqlite3.Connection, rid: str, source: str | None = None) -> None:
    conn.execute(
        "INSERT INTO resume "
        "(id, job_id, sample_class, file_name, content_sha256, uploaded_by, source) "
        "VALUES (?, 'j1', 'synthetic', ?, ?, 'alice', ?)",
        (rid, rid + ".pdf", "h-" + rid, source),
    )


def _attach(conn: sqlite3.Connection, *, resume_id: str, name: str = "张三"):
    return effect_attach_resume_to_candidate(
        conn,
        thread_id=resume_id,
        business_key="once",
        resume_id=resume_id,
        job_id="j1",
        name=name,
        phone_hash=None,
    )


def _initial_history(conn: sqlite3.Connection, application_id: str) -> tuple:
    return conn.execute(
        "SELECT from_stage_id, to_stage_id, actor_type, action, source "
        "FROM application_stage_history WHERE application_id = ?",
        (application_id,),
    ).fetchone()


def test_initial_history_carries_resume_source():
    conn = _conn()
    _resume(conn, "r1", source="boss")
    result = _attach(conn, resume_id="r1")
    assert _initial_history(conn, result["application_id"]) == (
        None, "initial", "agent", None, "boss",
    )


def test_initial_history_source_is_null_when_resume_has_none():
    conn = _conn()
    _resume(conn, "r1", source=None)
    result = _attach(conn, resume_id="r1")
    assert _initial_history(conn, result["application_id"])[4] is None


def test_replay_does_not_add_a_second_initial_history_row():
    """幂等由 effect_attach_resume_to_candidate 承担：重跑不产生第二条初始事实。"""
    conn = _conn()
    _resume(conn, "r1", source="liepin")
    first = _attach(conn, resume_id="r1")
    second = _attach(conn, resume_id="r1")
    assert first is not None and second is None
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history"
    ).fetchone()[0] == 1


def _docx_bytes(paragraphs: list[str]) -> bytes:
    document = docx.Document()
    for p in paragraphs:
        document.add_paragraph(p)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _fake_compute_parse(gateway, *, spans, audit_context):
    from app.schemas.resume_fields import (
        EducationField, ListField, NumberField, ResumeFields, SpanRef, TextField,
    )

    fields = ResumeFields(
        name=TextField(
            value="张三", confidence=0.9,
            spans=[SpanRef(span_id=1, quote="张三", start=0, end=2)],
        ),
        years_of_experience=NumberField(
            value=3.0, confidence=0.9,
            spans=[SpanRef(span_id=2, quote="3年", start=3, end=5)],
        ),
        skills=ListField(not_mentioned=True, value=[], confidence=1.0),
        companies=ListField(not_mentioned=True, value=[], confidence=1.0),
        education=EducationField(not_mentioned=True, value=None, confidence=1.0),
        expected_city=TextField(not_mentioned=True, value=None, confidence=1.0),
    )

    class _Meta:
        response_model = "test-model"

    return fields, _Meta()


def test_upload_zip_initial_history_carries_detected_source(make_test_client, monkeypatch):
    """端到端：ZIP 里文件名可识别为 boss ⇒ 该份简历的投递初始事实 source='boss'。
    ⛔ 来源识别本身（U1）不在这里复验，只验它一路传到了流转事实表。"""
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.commit()
    monkeypatch.setattr("app.web.server.compute_parse", _fake_compute_parse)

    zdata = _zip({
        "候选人-张三-boss直聘.docx": _docx_bytes(["张三，3年工作经验。"] * 6),
    })
    resp = client.post(
        "/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "synthetic"},
        files=[("files", ("bundle.zip", zdata, "application/zip"))],
    )
    results = resp.json()["results"]
    assert results[0]["status"] == "accepted"
    assert results[0]["source"] == "boss"

    row = conn.execute(
        "SELECT h.source FROM application_stage_history h "
        "JOIN application a ON a.id = h.application_id "
        "WHERE a.resume_id = ?",
        (results[0]["resume_id"],),
    ).fetchone()
    assert row == ("boss",)
```

- [ ] **Step 3: 验证**

```bash
python -m pytest tests/test_stage_history_initial_source.py tests/test_attach_resume.py tests/test_channel_bundle_e2e.py tests/test_screening_trigger_on_upload.py -q
```

预期输出：全部 passed（新增 4 条 + 既有挂接/包上传/触发点回归），exit code 0。

---

### Task 3: 来源改正**追加**一条 `source_corrected` 流转事实

**Files:**

- Create: `app/intake/stage_history.py`
- Modify: `app/web/server.py`
- Test: `tests/test_source_correction_fact.py`（新建）

**Interfaces:**

- Produces: `append_source_correction(conn, *, resume_id: str, from_source: str | None, to_source: str, actor: str) -> str | None`（返回受影响的 `application_id`；该简历没有投递时返回 `None` 且不写行）
- Consumes: `application_stage_history.source` 与七值 `action` CHECK（Task 1）、既有的 `POST /api/resumes/{resume_id}/source` 与 `SourceCorrectionRequest`（U1）

- [ ] **Step 1: 创建 `app/intake/stage_history.py`**

```python
"""来源改正的流转事实追加（channel-resume-intake U3 tasks 3.2）。

来源改正在流转事实表里留的是**追加**的一条 action='source_corrected' 事实，
source 记新值——⛔ 绝不更新投递创建时那条初始记录：本表是只追加的事实表，
改写会破坏"所有报表的基础"这个前提（design D5）。

幂等由调用方承担：POST /api/resumes/{resume_id}/source 的"同值早退"在到达本
函数之前就把重复提交挡住了（与 source_correction_log 同一口径，U1 已落地）。
"""
from __future__ import annotations

import json
import sqlite3
import uuid


def append_source_correction(
    conn: sqlite3.Connection,
    *,
    resume_id: str,
    from_source: str | None,
    to_source: str,
    actor: str,
) -> str | None:
    """追加一条 source_corrected 流转事实，返回受影响的 application_id。

    该简历还没有投递（解析不出/解析失败，application 行不存在）时返回 None 且
    ⛔ 不写任何行——来源改正本身仍然成立（resume.source 与 source_correction_log
    由端点写），只是没有投递可挂事实（本计划架构决策 7）。

    from_stage_id 留 NULL、to_stage_id 取该投递当前阶段：来源改正**不改阶段**，
    这条事实不在阶段流里，语义靠 action 区分（架构决策 6）。
    detail_json 存原值/新值，让这条事实自足（与 source_correction_log 也能交叉
    复验）。
    """
    row = conn.execute(
        "SELECT id, current_stage_id FROM application WHERE resume_id = ?",
        (resume_id,),
    ).fetchone()
    if row is None:
        return None
    application_id, current_stage_id = row[0], row[1]
    conn.execute(
        "INSERT INTO application_stage_history "
        "(id, application_id, from_stage_id, to_stage_id, actor_type, actor, action, "
        " detail_json, source) "
        "VALUES (?, ?, NULL, ?, 'human', ?, 'source_corrected', ?, ?)",
        (
            str(uuid.uuid4()),
            application_id,
            current_stage_id,
            actor,
            json.dumps(
                {"from_source": from_source, "to_source": to_source},
                ensure_ascii=False,
            ),
            to_source,
        ),
    )
    return application_id
```

- [ ] **Step 2: `correct_resume_source` 在同一事务里追加事实**

① 在 `app/web/server.py` 的 import 区（与 `from app.intake.source import SOURCE_VALUES` 相邻）加一行：

```python
from app.intake.stage_history import append_source_correction
```

② 把 `correct_resume_source` 里 `INSERT INTO source_correction_log …` 起、到 `return {"resume_id": resume_id, "source": new_source, "already_corrected": False}` 止的整段替换为：

```python
        conn.execute(
            "INSERT INTO source_correction_log (id, resume_id, from_source, to_source, corrected_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), resume_id, current, new_source, corrected_by),
        )
        conn.execute(
            "UPDATE resume SET source = ?, source_origin = 'corrected' WHERE id = ?",
            (new_source, resume_id),
        )
        # channel-resume-intake U3 task 3.2：来源改正**追加**一条流转事实，
        # ⛔ 不改写投递创建时那条初始记录。三句话共用一个 conn、一段未提交事务，
        # 由下面这次 commit 一起提交（铁律 1 的"同事务"）。
        # 幂等由上面的"同值早退"承担：同值重复提交根本走不到这里，所以不会追加
        # 第二条（与 source_correction_log 完全同一口径）。
        append_source_correction(
            conn,
            resume_id=resume_id,
            from_source=current,
            to_source=new_source,
            actor=corrected_by,
        )
        conn.commit()
        return {"resume_id": resume_id, "source": new_source, "already_corrected": False}
```

- [ ] **Step 3: 创建 `tests/test_source_correction_fact.py`**

```python
"""U3 tasks 3.2：来源改正追加 source_corrected 事实，⛔ 不改写初始记录。"""
from __future__ import annotations

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account


def _client_and_resume(make_test_client, *, with_application: bool, source="boss"):
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.execute(
        "INSERT INTO resume "
        "(id, job_id, sample_class, file_name, content_sha256, uploaded_by, source) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'h', 'alice', ?)",
        (source,),
    )
    if with_application:
        conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
        conn.execute(
            "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
            "VALUES ('app-1', 'c1', 'j1', 'r1', 'screening')"
        )
        conn.execute(
            "INSERT INTO application_stage_history "
            "(id, application_id, from_stage_id, to_stage_id, actor_type, source) "
            "VALUES ('h-init', 'app-1', NULL, 'initial', 'agent', ?)",
            (source,),
        )
    conn.commit()
    return client, conn


def _facts(conn):
    return conn.execute(
        "SELECT actor_type, actor, action, source, detail_json FROM application_stage_history "
        "WHERE action = 'source_corrected' ORDER BY rowid"
    ).fetchall()


def test_correction_appends_a_fact_and_keeps_the_initial_record(make_test_client):
    client, conn = _client_and_resume(make_test_client, with_application=True, source="boss")

    resp = client.post("/api/resumes/r1/source", json={"source": "referral"})
    assert resp.status_code == 200
    assert resp.json() == {"resume_id": "r1", "source": "referral", "already_corrected": False}

    # 初始记录一字未动
    row = conn.execute(
        "SELECT from_stage_id, to_stage_id, actor_type, action, source "
        "FROM application_stage_history WHERE id = 'h-init'"
    ).fetchone()
    assert row == (None, "initial", "agent", None, "boss")

    # 新事实：human / 改正人 / source_corrected / 新来源 / 原-新值详情
    facts = _facts(conn)
    assert len(facts) == 1
    actor_type, actor, action, source, detail_json = facts[0]
    assert (actor_type, actor, action, source) == (
        "human", "alice", "source_corrected", "referral",
    )
    assert '"from_source": "boss"' in detail_json
    assert '"to_source": "referral"' in detail_json

    # 阶段没有被这次改正改动：事实的 to_stage_id = 该投递当前阶段
    assert conn.execute(
        "SELECT to_stage_id FROM application_stage_history WHERE action = 'source_corrected'"
    ).fetchone()[0] == "screening"
    assert conn.execute(
        "SELECT current_stage_id FROM application WHERE id = 'app-1'"
    ).fetchone()[0] == "screening"


def test_same_value_correction_appends_no_second_fact(make_test_client):
    client, conn = _client_and_resume(make_test_client, with_application=True)
    client.post("/api/resumes/r1/source", json={"source": "referral"})
    resp = client.post("/api/resumes/r1/source", json={"source": "referral"})
    assert resp.json()["already_corrected"] is True
    assert len(_facts(conn)) == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM source_correction_log WHERE resume_id = 'r1'"
    ).fetchone()[0] == 1


def test_second_correction_appends_a_second_fact(make_test_client):
    """幂等的是"同值"，不是"只能改一次"：前后两次不同值的改正是两条事实。"""
    client, conn = _client_and_resume(make_test_client, with_application=True)
    client.post("/api/resumes/r1/source", json={"source": "referral"})
    client.post("/api/resumes/r1/source", json={"source": "51job"})
    facts = _facts(conn)
    assert [f[3] for f in facts] == ["referral", "51job"]


def test_correction_without_application_skips_the_fact(make_test_client):
    """解析不出/解析失败的简历没有投递：改正照常生效，但没有事实可追加。"""
    client, conn = _client_and_resume(make_test_client, with_application=False)
    resp = client.post("/api/resumes/r1/source", json={"source": "referral"})
    assert resp.status_code == 200
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history"
    ).fetchone()[0] == 0
    assert conn.execute("SELECT source FROM resume WHERE id = 'r1'").fetchone()[0] == "referral"
    assert conn.execute(
        "SELECT COUNT(*) FROM source_correction_log WHERE resume_id = 'r1'"
    ).fetchone()[0] == 1


def test_invalid_source_is_still_rejected_before_any_write(make_test_client):
    """回归：非法取值 422，且不留下任何事实/留痕。"""
    client, conn = _client_and_resume(make_test_client, with_application=True)
    assert client.post("/api/resumes/r1/source", json={"source": "unknown"}).status_code == 422
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history WHERE action = 'source_corrected'"
    ).fetchone()[0] == 0
```

- [ ] **Step 4: 验证**

```bash
python -m pytest tests/test_source_correction_fact.py tests/test_resume_source_correction.py tests/test_db_m2_schema.py -q
```

预期输出：全部 passed（新增 5 条 + U1 改正端点回归 + 钉列），exit code 0。

---

### Task 4: 只读 `source_distribution(job_id)` 与"没有报表页"的反证

**Files:**

- Modify: `app/storage/source.py`
- Test: `tests/test_source_distribution.py`（新建）

**Interfaces:**

- Produces: `source_distribution(conn: sqlite3.Connection, job_id: str) -> dict[str, int]`
- ⛔ 不新增任何路由（既无 HTML 页也无 JSON API）

- [ ] **Step 1: 在 `app/storage/source.py` 追加查询函数**

把该模块的 docstring 第一行责任说明扩成"来源查询：候选人来源 + 岗位来源分布"，并在函数之后追加：

```python
def source_distribution(conn: sqlite3.Connection, job_id: str) -> dict[str, int]:
    """按来源统计某岗位的投递数（channel-resume-intake U3 tasks 3.3）。

    ⚠️ **只读**：本函数 ⛔ 不得出现任何 INSERT/UPDATE/DELETE/commit——它是报表
    的数据来源，是观测端不是写入端（与 app/audit/assertions.py 同一纪律）。

    数据源就是流转事实表：本包 ⛔ 不做漏斗报表页（spec「只记字段不做漏斗报表」
    + design D5），渠道分布完全由既有事实表算出。

    语义（本计划架构决策 9）：**每条投递恰好计一次**，取它**最新一条带 source 的
    流转事实**——来源改正追加的新事实会自然覆盖初始记录的值，所以"改正后分布跟着
    搬"。没有带 source 事实的投递计 unknown（老库既有行 source 为 NULL，与
    candidate_source() 同一口径）。

    ⛔ 不要改成对事实表 COUNT(*) GROUP BY source：那会把"一条投递的初始事实 +
    一条改正事实"算成两票，分布直接翻倍。
    """
    rows = conn.execute(
        "SELECT COALESCE(("
        "  SELECT h.source FROM application_stage_history h "
        "  WHERE h.application_id = a.id AND h.source IS NOT NULL "
        "  ORDER BY h.occurred_at DESC, h.rowid DESC LIMIT 1"
        "), 'unknown') AS source, COUNT(*) AS n "
        "FROM application a WHERE a.job_id = ? "
        "GROUP BY source ORDER BY source",
        (job_id,),
    ).fetchall()
    return {row[0]: row[1] for row in rows}
```

- [ ] **Step 2: 创建 `tests/test_source_distribution.py`**

```python
"""U3 tasks 3.3：只读来源分布 + spec「不做漏斗报表」的反证。"""
from __future__ import annotations

import sqlite3

from app.storage.auth_session import create_session
from app.storage.db import init_schema
from app.storage.hr_account import upsert_account
from app.storage.source import source_distribution


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    init_schema(c)
    c.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    c.execute("INSERT INTO job (id, title) VALUES ('j2', '采购工程师')")
    return c


def _application(conn, *, app_id, resume_id, job_id="j1", source=None):
    if conn.execute("SELECT 1 FROM candidate WHERE id = 'c1'").fetchone() is None:
        conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT INTO resume "
        "(id, job_id, sample_class, file_name, content_sha256, uploaded_by, source) "
        "VALUES (?, ?, 'synthetic', ?, ?, 'alice', ?)",
        (resume_id, job_id, resume_id + ".pdf", "h-" + resume_id, source),
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, 'c1', ?, ?, 'initial')",
        (app_id, job_id, resume_id),
    )
    if source is not None:
        conn.execute(
            "INSERT INTO application_stage_history "
            "(id, application_id, from_stage_id, to_stage_id, actor_type, source) "
            "VALUES (?, ?, NULL, 'initial', 'agent', ?)",
            ("h-init-" + app_id, app_id, source),
        )
    conn.commit()


def test_counts_by_source_from_the_fact_table():
    conn = _conn()
    _application(conn, app_id="app-1", resume_id="r1", source="boss")
    _application(conn, app_id="app-2", resume_id="r2", source="boss")
    _application(conn, app_id="app-3", resume_id="r3", source="referral")
    assert source_distribution(conn, "j1") == {"boss": 2, "referral": 1}


def test_correction_moves_the_count_to_the_new_source():
    """来源改正追加的新事实覆盖初始值 ⇒ 分布跟着搬，而不是两边各记一票。"""
    conn = _conn()
    _application(conn, app_id="app-1", resume_id="r1", source="boss")
    conn.execute(
        "INSERT INTO application_stage_history "
        "(id, application_id, from_stage_id, to_stage_id, actor_type, actor, action, source) "
        "VALUES ('h-corr', 'app-1', NULL, 'initial', 'human', 'alice', 'source_corrected', 'referral')"
    )
    conn.commit()
    assert source_distribution(conn, "j1") == {"referral": 1}


def test_application_without_a_source_fact_counts_as_unknown():
    conn = _conn()
    _application(conn, app_id="app-1", resume_id="r1", source=None)
    assert source_distribution(conn, "j1") == {"unknown": 1}


def test_distribution_is_scoped_to_the_job():
    conn = _conn()
    _application(conn, app_id="app-1", resume_id="r1", job_id="j1", source="boss")
    _application(conn, app_id="app-2", resume_id="r2", job_id="j2", source="liepin")
    assert source_distribution(conn, "j1") == {"boss": 1}
    assert source_distribution(conn, "j2") == {"liepin": 1}
    assert source_distribution(conn, "nope") == {}


def test_distribution_is_read_only():
    conn = _conn()
    _application(conn, app_id="app-1", resume_id="r1", source="boss")
    before = conn.execute(
        "SELECT COUNT(*) FROM application_stage_history"
    ).fetchone()[0]
    source_distribution(conn, "j1")
    assert conn.execute(
        "SELECT COUNT(*) FROM application_stage_history"
    ).fetchone()[0] == before


def test_there_is_no_funnel_report_page(make_test_client):
    """spec「只记字段不做漏斗报表」的机器判据：报表页不存在。⛔ 不要为了"让
    source_distribution 有调用方"顺手加一个页面或接口——那条 Requirement 会当场
    失去守护，而它正是本单元唯一的 Non-Goal。"""
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.commit()

    assert client.get("/reports/source-distribution?job_id=j1").status_code == 404
    assert client.get("/jobs/j1/source-distribution").status_code == 404
    assert client.get("/jobs/j1/channel-funnel").status_code == 404
```

- [ ] **Step 3: 验证**

```bash
python -m pytest tests/test_source_distribution.py tests/test_resume_source_schema.py -q
```

预期输出：`6 passed`（新增 6 条）＋既有来源列/候选人来源回归，exit code 0。

---

### Task 5: 三条审计断言 ①②③ 与 CI 接入

**Files:**

- Modify: `app/storage/db.py`（仅当 `candidate_merge_log` 尚不存在时兜底建表）
- Modify: `app/audit/assertions.py`
- Modify: `tests/test_audit_assertions.py`
- Modify: `tests/test_audit_assertion_effectiveness.py`
- Test: `tests/test_channel_audit_assertions.py`（新建）

**Interfaces:**

- Produces: `assert_no_plaintext_phone_column(conn)`、`assert_merge_log_rows_have_actor(conn)`、`assert_merged_candidates_have_no_active_application(conn)`
- Produces: `COMPLIANCE_ASSERTIONS` 由 4 条扩到 7 条（`run_compliance_assertions` 自动跟着扩，`python -m app.audit.assertions` CLI 与 CI 的 `-m compliance` 步骤因此同时覆盖）

- [ ] **Step 1: 兜底确保 `candidate_merge_log` 存在（U2 Task 4 已落地时是空转）**

⚠️ **先检查**：`rg -n 'candidate_merge_log' app/storage/db.py`。若 SCHEMA 里已有它的 `CREATE TABLE`，**跳过本 Step**（U2 Task 4 已落地）。若没有，按 U2 计划 `docs/superpowers/plans/2026-10-10-channel-resume-intake-unit2-dedup-merge.md` Task 4 Step 3 的 DDL **逐字**插入 `app/storage/db.py` 的 SCHEMA（`resume` 表定义之前）：

```sql
-- 合并留痕（channel-resume-intake U2 tasks 2.4）。一行 = 一次合并：primary 保留、
-- secondary 被并入。secondary_snapshot 存被合并方合并前全部 application 的 JSON
-- 快照（撤销按它恢复）。merged_by / reason 的 CHECK 与 source_correction_log 同
-- 一手法：空操作人 / 空依据等于没留痕。unmerged_by/at 可空——未撤销为 NULL。
--
-- ⚠️ 本段由 U3 Task 5 兜底落地（U3 的审计断言 ② 读它）。U2 Task 4 落地时是
-- CREATE TABLE IF NOT EXISTS 的空转。⛔ 两侧 DDL 必须逐字一致：任何一侧要改
-- 这段 DDL（列名、CHECK、索引），必须同步另一侧，否则"U3 先落"与"U2 先落"会
-- 在同一份 SCHEMA 上给出两种形状，而第二条 CREATE TABLE 是静默 no-op。
CREATE TABLE IF NOT EXISTS candidate_merge_log (
    id TEXT PRIMARY KEY NOT NULL,
    primary_id TEXT NOT NULL REFERENCES candidate(id),
    secondary_id TEXT NOT NULL REFERENCES candidate(id),
    reason TEXT NOT NULL CHECK (
        reason IS NOT NULL
        AND trim(reason, ' ' || char(9) || char(10) || char(13)) != ''
    ),
    secondary_snapshot TEXT NOT NULL,
    merged_by TEXT NOT NULL CHECK (
        merged_by IS NOT NULL
        AND trim(merged_by, ' ' || char(9) || char(10) || char(13)) != ''
    ),
    merged_at TEXT NOT NULL DEFAULT (datetime('now')),
    unmerged_by TEXT,
    unmerged_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_candidate_merge_log_secondary
    ON candidate_merge_log (secondary_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_candidate_merge_log_active_secondary
    ON candidate_merge_log (secondary_id) WHERE unmerged_at IS NULL;
```

⚠️ 这是**新表**，⛔ 不进 `_ADDED_COLUMNS`（加列路径只服务"老库缺列"；`tests/test_db_migration.py::test_audit_tables_never_enter_the_add_column_path` 钉着 `_ADDED_COLUMNS` 的表集合，往里面塞新表会让 `apply_column_migrations` 对着不存在的表 `ALTER TABLE`）。

- [ ] **Step 2: `app/audit/assertions.py` 新增三条断言**

在 `assert_every_decision_has_human_review` 之后、`── 四条一起跑 ──`（Step 3 会改名）之前插入：

```python
# ── 断言五（channel-resume-intake U3 tasks 3.4①）：候选人表无明文手机号列 ────
#
# 合规红线「模型全部走境内，简历数据不出境」与 M2 D11「手机号只用于去重、以哈希
# 存储、明文不落库」的结构守护。
# ⚠️ 它验的是**表结构**，不是行内容：明文手机号一旦有列可落，写入方迟早会出现，
# 而等它出现再查数据，数据已经进库了。
# 「扩到 resume.source_*」= 同一手法钉住简历的来源列集合：只许 source /
# source_origin 两个取值，⛔ 不许出现 source_text 这类"明文来源文本"列（来源一旦
# 有自由文本列，确定性规则与值域约束就会被绕过）。

ASSERTION_NO_PLAINTEXT_PHONE = "candidate 无明文手机号列（resume 的来源列集合同时被钉住）"

# 「疑似手机号列」的判据是列名标记，不是数据内容。⚠️ 白名单是显式的：phone_hash
# 命中标记但必须放行——哈希列是"手机号可以落库的唯一形态"（M2 D11）。
_PHONE_COLUMN_MARKERS = ("phone", "mobile", "cell", "tel")
_CANDIDATE_PHONE_COLUMNS_ALLOWED = frozenset({"phone_hash"})
_RESUME_SOURCE_COLUMNS_ALLOWED = frozenset({"source", "source_origin"})


def assert_no_plaintext_phone_column(conn: sqlite3.Connection) -> AssertionResult:
    """候选人与简历两张表的结构判据。

    两张表缺一不可：candidate 是"手机号有没有明文列"的唯一归属地，resume 是
    "来源有没有变成自由文本"的唯一归属地。缺表一律 fail-closed——验不了就算不
    通过（与断言一/断言四同一口径）。
    """
    for table in ("candidate", "resume"):
        if not _table_exists(conn, table):
            return AssertionResult(
                name=ASSERTION_NO_PLAINTEXT_PHONE,
                ok=False,
                violations=({"table": table, "issue": "table_missing"},),
                detail=(
                    f"{table} 表不存在。本条断言是「手机号明文不落库」与「来源不得"
                    "变成自由文本」的结构守卫，缺表等于验不了——fail-closed："
                    "验不了就算不通过。"
                ),
            )

    violations: list[dict[str, Any]] = []
    for column in sorted(_columns(conn, "candidate")):
        if column in _CANDIDATE_PHONE_COLUMNS_ALLOWED:
            continue
        lowered = column.lower()
        if any(marker in lowered for marker in _PHONE_COLUMN_MARKERS):
            violations.append({"table": "candidate", "column": column})
    for column in sorted(_columns(conn, "resume")):
        if column in _RESUME_SOURCE_COLUMNS_ALLOWED:
            continue
        if column.lower().startswith("source"):
            violations.append({"table": "resume", "column": column})

    return AssertionResult(
        name=ASSERTION_NO_PLAINTEXT_PHONE,
        ok=not violations,
        violations=tuple(violations),
        detail=(
            ""
            if not violations
            else "发现疑似明文手机号列或来源自由文本列。手机号只允许哈希列"
            "（candidate.phone_hash，M2 D11）；简历来源只允许 resume.source /"
            " resume.source_origin 两列（值域由应用层 Source 枚举约束）。"
            "⛔ 不要把这些列加进白名单——那是把红线缺口登记成合规。"
        ),
    )


# ── 断言六（U3 tasks 3.4②）：合并留痕的操作人非空 ────────────────────────
#
# 合规红线「淘汰必须有人工确认节点并留痕」在合并路径上的对应物：合并是不可逆的
# 个人信息记录变更（design D4），没有操作人的合并行等于没人负责。
# ⚠️ candidate_merge_log 的 DDL 自带 CHECK，所以本断言的违例行只可能来自绕过
# CHECK 的写入路径（PRAGMA ignore_check_constraints）——与断言二同一性质：是
# CHECK 之上的纵深防御，不是重复劳动。

MERGE_LOG_TABLE = "candidate_merge_log"

ASSERTION_MERGE_LOG_HAS_ACTOR = "candidate_merge_log 每行 merged_by 非空"


def assert_merge_log_rows_have_actor(conn: sqlite3.Connection) -> AssertionResult:
    if not _table_exists(conn, MERGE_LOG_TABLE):
        return AssertionResult(
            name=ASSERTION_MERGE_LOG_HAS_ACTOR,
            ok=False,
            violations=({"table": MERGE_LOG_TABLE, "issue": "table_missing"},),
            detail=(
                f"{MERGE_LOG_TABLE} 表不存在。合并留痕是「谁把两个人合并成一个人」"
                "的唯一记录，缺表等于这条红线完全没有机器守护——fail-closed："
                "验不了就算不通过。"
            ),
        )
    if "merged_by" not in _columns(conn, MERGE_LOG_TABLE):
        return AssertionResult(
            name=ASSERTION_MERGE_LOG_HAS_ACTOR,
            ok=False,
            violations=({"table": MERGE_LOG_TABLE, "missing_column": "merged_by"},),
            detail=(
                f"{MERGE_LOG_TABLE} 缺 merged_by 列。fail-closed：验不了就算不通过，"
                "⛔ 不要改成跳过——跳过会把「列名改了」静默折成「零违例」。"
            ),
        )

    rows = _rows(
        conn,
        f"SELECT * FROM {MERGE_LOG_TABLE} "
        "WHERE merged_by IS NULL "
        "OR trim(merged_by, ' ' || char(9) || char(10) || char(13)) = ''",
    )
    return AssertionResult(
        name=ASSERTION_MERGE_LOG_HAS_ACTOR,
        ok=not rows,
        violations=tuple(rows),
        detail=(
            ""
            if not rows
            else f"发现 {len(rows)} 条没有操作人的合并留痕，违反合规红线"
            "「淘汰必须有人工确认节点并留痕」在合并路径上的对应要求"
            "（合并是对个人信息记录的不可逆变更，必须有人签字）。"
            "这类记录只可能来自绕过 CHECK 的写入路径，需要查清来源。"
        ),
    )


# ── 断言七（U3 tasks 3.4③）：被合并的候选人没有活跃投递 ──────────────────
#
# 合并的结构不变式：secondary 的 resume / application 一律改挂到 primary
# （design D4），所以 merged_into 非空的候选人**不该**再持有任何活跃投递。
# 它若成立，意味着有一条写入路径把新投递挂到了已合并候选人身上——那会让这个
# 候选人在列表里"复活"，并在后续合并/撤销时产生归属混乱（design Risks 第 4 条）。

ASSERTION_MERGED_CANDIDATE_HAS_NO_ACTIVE_APPLICATION = (
    "merged_into 非空的候选人没有活跃投递"
)


def assert_merged_candidates_have_no_active_application(
    conn: sqlite3.Connection,
) -> AssertionResult:
    if not _table_exists(conn, "candidate"):
        return AssertionResult(
            name=ASSERTION_MERGED_CANDIDATE_HAS_NO_ACTIVE_APPLICATION,
            ok=False,
            violations=({"table": "candidate", "issue": "table_missing"},),
            detail="candidate 表不存在，合并标记无从校验——fail-closed：验不了就算不通过。",
        )
    if "merged_into" not in _columns(conn, "candidate"):
        return AssertionResult(
            name=ASSERTION_MERGED_CANDIDATE_HAS_NO_ACTIVE_APPLICATION,
            ok=False,
            violations=({"table": "candidate", "missing_column": "merged_into"},),
            detail=(
                "candidate 缺 merged_into 列（channel-resume-intake U2 tasks 2.4）。"
                "fail-closed：验不了就算不通过。"
            ),
        )

    rows = _rows(
        conn,
        "SELECT c.id AS candidate_id, c.merged_into AS merged_into, a.id AS application_id "
        "FROM candidate c JOIN application a ON a.candidate_id = c.id "
        "WHERE c.merged_into IS NOT NULL AND a.status = 'active'",
    )
    return AssertionResult(
        name=ASSERTION_MERGED_CANDIDATE_HAS_NO_ACTIVE_APPLICATION,
        ok=not rows,
        violations=tuple(rows),
        detail=(
            ""
            if not rows
            else f"发现 {len(rows)} 条挂到已合并候选人身上的活跃投递。合并的约定是"
            "被合并方的投递全部改挂到保留方（design D4）——这些行说明有写入路径把"
            "投递挂给了已合并候选人，撤销合并时归属会乱。"
        ),
    )
```

- [ ] **Step 3: 三条断言进 `COMPLIANCE_ASSERTIONS`**

同文件里，把 `── 四条一起跑 ──` 那段替换为：

```python
# ── 七条一起跑 ──────────────────────────────────────────────────────────

COMPLIANCE_ASSERTIONS: tuple[Callable[[sqlite3.Connection], AssertionResult], ...] = (
    assert_no_ai_score_rejections,
    assert_no_blank_evidence_ref,
    assert_no_unlisted_criterion_key,
    assert_every_decision_has_human_review,
    # channel-resume-intake U3 tasks 3.4：三条结构性红线（明文手机号 / 合并留痕
    # 的操作人 / 被合并候选人的活跃投递）。
    assert_no_plaintext_phone_column,
    assert_merge_log_rows_have_actor,
    assert_merged_candidates_have_no_active_application,
)


def run_compliance_assertions(conn: sqlite3.Connection) -> list[AssertionResult]:
    """spec「合规断言在 CI 中执行」：七条全部成立才通过。

    ⚠️ **全部跑完再返回，⛔ 不短路。** 第一条红了就返回的话，一次修复只能
    看到一条违例，第二条要等下一轮 CI 才现形。
    """
    return [assertion(conn) for assertion in COMPLIANCE_ASSERTIONS]
```

（模块 docstring 里"前三条断言在空表上全部恒真"那段⛔ 不动——它讲的是当时那三条。新增三条在初始化的空库上不是恒真：它们查的是**表结构**与**跨表不变式**，缺表/缺列直接失败。）

- [ ] **Step 4: 更新 `tests/test_audit_assertions.py` 的条数断言**

把 `test_run_compliance_assertions_returns_all_three` 整个替换为：

```python
def test_run_compliance_assertions_covers_every_assertion(conn):
    results = run_compliance_assertions(conn)

    assert len(results) == 7
    assert len(COMPLIANCE_ASSERTIONS) == 7
    assert all(isinstance(r, AssertionResult) for r in results)
    # 名字必须两两不同：报告里靠 name 定位是哪条红线破了。
    assert len({r.name for r in results}) == 7
    assert all(r.ok for r in results)
```

- [ ] **Step 5: 创建 `tests/test_channel_audit_assertions.py`（正向 + 缺表口径）**

```python
"""U3 tasks 3.4 三条新断言的**正向**行为：干净库通过、缺表/缺列时 fail-closed。

⚠️ 与既有纪律一致：恒真的断言在这里也会全绿，反证在
tests/test_audit_assertion_effectiveness.py（造违例 → 必须失败），两个文件必须
成对存在。
"""
from __future__ import annotations

import sqlite3

import pytest

from app.audit.assertions import (
    assert_merge_log_rows_have_actor,
    assert_merged_candidates_have_no_active_application,
    assert_no_plaintext_phone_column,
)
from app.storage.db import get_connection, init_schema

pytestmark = pytest.mark.compliance


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "channel_audit.db"))
    init_schema(c)
    return c


def test_all_three_pass_on_a_clean_db(conn):
    for assertion in (
        assert_no_plaintext_phone_column,
        assert_merge_log_rows_have_actor,
        assert_merged_candidates_have_no_active_application,
    ):
        result = assertion(conn)
        assert result.ok is True, (result.name, result.violations)
        assert result.violations == ()


def test_plaintext_phone_assertion_fails_closed_without_candidate_table():
    bare = sqlite3.connect(":memory:")
    result = assert_no_plaintext_phone_column(bare)
    assert result.ok is False
    assert result.violations == ({"table": "candidate", "issue": "table_missing"},)


def test_plaintext_phone_assertion_fails_closed_without_resume_table():
    bare = sqlite3.connect(":memory:")
    bare.execute("CREATE TABLE candidate (id TEXT PRIMARY KEY, name TEXT, phone_hash TEXT)")
    result = assert_no_plaintext_phone_column(bare)
    assert result.ok is False
    assert result.violations == ({"table": "resume", "issue": "table_missing"},)


def test_merge_log_assertion_fails_closed_when_table_is_absent():
    bare = sqlite3.connect(":memory:")
    result = assert_merge_log_rows_have_actor(bare)
    assert result.ok is False
    assert result.violations == ({"table": "candidate_merge_log", "issue": "table_missing"},)


def test_merged_candidate_assertion_fails_closed_without_merged_into(conn):
    conn.execute("ALTER TABLE candidate RENAME TO candidate_old")
    conn.execute("CREATE TABLE candidate (id TEXT PRIMARY KEY, name TEXT, phone_hash TEXT)")
    conn.commit()
    result = assert_merged_candidates_have_no_active_application(conn)
    assert result.ok is False
    assert result.violations == ({"table": "candidate", "missing_column": "merged_into"},)
```

- [ ] **Step 6: 往 `tests/test_audit_assertion_effectiveness.py` 追加反证**

在文件末尾追加（该文件已有模块级 `pytestmark = pytest.mark.compliance`，⛔ 不要再写一遍）：

```python
# ── channel-resume-intake U3 tasks 3.4：三条新断言的反证 ────────────────
#
# "0 命中"同样兼容"红线守住了"与"断言根本没生效"两种解释。下面这几条各自造一次
# 违例，断言必须变红——这是新增三条断言唯一的效力来源。

def test_plaintext_phone_column_is_detected(conn):
    from app.audit.assertions import assert_no_plaintext_phone_column

    conn.execute("ALTER TABLE candidate RENAME TO candidate_old")
    conn.execute(
        "CREATE TABLE candidate "
        "(id TEXT PRIMARY KEY, name TEXT, phone TEXT, phone_hash TEXT)"
    )
    conn.commit()

    result = assert_no_plaintext_phone_column(conn)

    assert result.ok is False
    assert {"table": "candidate", "column": "phone"} in result.violations


def test_resume_source_text_column_is_detected(conn):
    from app.audit.assertions import assert_no_plaintext_phone_column

    conn.execute("ALTER TABLE resume ADD COLUMN source_text TEXT")
    conn.commit()

    result = assert_no_plaintext_phone_column(conn)

    assert result.ok is False
    assert {"table": "resume", "column": "source_text"} in result.violations


@pytest.mark.parametrize("blank", ["", "   ", "\t", "\n"])
def test_merge_log_blank_actor_is_detected(conn, blank):
    """DDL 自带 CHECK，所以违例行只能靠 PRAGMA ignore_check_constraints 造出来
    ——这正是本条断言存在的理由（CHECK 之上的纵深防御）。"""
    from app.audit.assertions import assert_merge_log_rows_have_actor

    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c2', '李四')")
    conn.execute("PRAGMA ignore_check_constraints = ON")
    conn.execute(
        "INSERT INTO candidate_merge_log "
        "(id, primary_id, secondary_id, reason, secondary_snapshot, merged_by) "
        "VALUES ('m1', 'c1', 'c2', '同一人', '{}', ?)",
        (blank,),
    )
    conn.commit()

    result = assert_merge_log_rows_have_actor(conn)

    assert result.ok is False
    assert [row["id"] for row in result.violations] == ["m1"]


def test_merged_candidate_with_active_application_is_detected(conn):
    from app.audit.assertions import assert_merged_candidates_have_no_active_application

    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute("INSERT INTO candidate (id, name, merged_into) VALUES ('c2', '李四', 'c1')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r2', 'j1', 'synthetic', 'b.pdf', 'h2', 'alice')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app-2', 'c2', 'j1', 'r2', 'initial')"
    )
    conn.commit()

    result = assert_merged_candidates_have_no_active_application(conn)

    assert result.ok is False
    assert [row["candidate_id"] for row in result.violations] == ["c2"]


def test_merged_candidate_without_active_applications_passes(conn):
    """反向对照：同一行改成 rejected 就该放过——否则这条断言会把"合并后正常被
    淘汰的历史投递"报成违例。"""
    from app.audit.assertions import assert_merged_candidates_have_no_active_application

    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute("INSERT INTO candidate (id, name, merged_into) VALUES ('c2', '李四', 'c1')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r2', 'j1', 'synthetic', 'b.pdf', 'h2', 'alice')"
    )
    conn.execute(
        "INSERT INTO application "
        "(id, candidate_id, job_id, resume_id, current_stage_id, status) "
        "VALUES ('app-2', 'c2', 'j1', 'r2', 'initial', 'rejected')"
    )
    conn.commit()

    assert assert_merged_candidates_have_no_active_application(conn).ok is True
```

- [ ] **Step 7: 验证**

```bash
python -m pytest \
  tests/test_channel_audit_assertions.py \
  tests/test_audit_assertions.py \
  tests/test_audit_assertion_effectiveness.py \
  tests/test_db_migration.py \
  tests/test_db_m2_schema.py -q
```

预期输出：全部 passed（新增 5 条正向 + 8 条反证 + 既有断言/迁移守卫），exit code 0。

```bash
python -m pytest -q -m compliance
```

预期输出：全部 passed（CI 的「合规断言（红线守护）」步骤跑的就是这一条），exit code 0。⛔ 不改 `.github/workflows/ci.yml`：全量与 `-m compliance` 两个 pytest 步骤已经覆盖新增断言与新增反证（本计划架构决策 11）。

---

## 收口自检（run-build 前）

```bash
python -m pytest \
  tests/test_stage_history_source_schema.py \
  tests/test_stage_history_initial_source.py \
  tests/test_source_correction_fact.py \
  tests/test_source_distribution.py \
  tests/test_channel_audit_assertions.py \
  tests/test_audit_assertions.py \
  tests/test_audit_assertion_effectiveness.py \
  tests/test_db_migration.py \
  tests/test_db_m2_schema.py \
  tests/test_db_m3_schema.py \
  tests/test_attach_resume.py \
  tests/test_channel_bundle_e2e.py \
  tests/test_resume_source_correction.py \
  tests/test_screening_trigger_on_upload.py \
  tests/test_static_frontend.py -q
```

预期：全部 passed，exit code 0。

```bash
python -m pytest -q -m compliance
```

预期：全部 passed（红线门禁），exit code 0。

**格式自查（⛔ 不靠肉眼）：**

```bash
grep -c '^### Task ' docs/superpowers/plans/2026-10-10-channel-resume-intake-unit3-source-in-stage-history.md
grep -c 'Global Constraints' docs/superpowers/plans/2026-10-10-channel-resume-intake-unit3-source-in-stage-history.md
grep -c 'source_distribution' docs/superpowers/plans/2026-10-10-channel-resume-intake-unit3-source-in-stage-history.md
```

预期：`5`、`1`、非零（`source_distribution` 在需求覆盖表、File Structure 与 Task 4 里都出现）。

**下一步：** 用 `run-build` 执行本计划（`scripts/codex_sdd_runner.py` 按 `### Task N:` 抽取任务、两阶段 review）。本计划含全部实现与测试代码，run-build 会先提取到临时目录做端到端提取验证（spec-to-plan 第 6 节的动作后移到执行期，因为本会话边界禁止写 `app/**`/`tests/**`）。
