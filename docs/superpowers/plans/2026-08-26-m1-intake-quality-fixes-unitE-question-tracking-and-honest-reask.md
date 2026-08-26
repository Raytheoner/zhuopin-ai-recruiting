# M1 采集质量修复 · 交付单元 E（已问未答追踪与诚实重问）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让系统知道每一个子问题"问过几次、答没答"，重问时诚实地告诉用户"这个你刚才没答"（文本与界面两个通道都看得见），并在同一个子问题问到第 3 次仍无回答时停止追问、把它交给单元 D 的缺口警示——而不是换个措辞把旧问题伪装成新问题继续烧轮次。

**Architecture:** 已问台账**不新增任何存储**：它是一个由 `job_profile.asked_questions`（按轮的问题 payload，单元 B 已落库）+ 画像现值**推导**出来的纯函数结果 `build_question_ledger()`。"这个子问题答没答"的判据直接复用单元 D 的 `derive_unspecified_fields()`——两边同口径，5.5 的「重问超限 → 目标字段计入未指定字段」因此**自动成立**，E 不写、也不得写第二套标记逻辑。`run_intake_turn` 在拿到本轮 `profile_patch` 之后建台账，用它给未答的重问打 `is_reask`、把超限的重问从本轮问题列表里摘掉；摘除只发生在本来就要发生的那一轮之内，**不新增 `job_profile` 行、不改动 `MAX_ROUNDS` / `MAX_TOTAL_ROUNDS` 任何一个口径**。前端把重问从"文本前缀"升级为"徽标 + 左边框"的视觉区分，用词与后端常量逐字同一份。

**Tech Stack:** Python 3.14.6（`./venv`）· pydantic 2.13.4 · FastAPI 0.115.6 · LangGraph ≥ 1.0.10 + SqliteSaver · SQLite（`data/demo.db`）· pytest 8.3.4 · 原生 DOM 单文件前端（无构建、无 npm）

---

## Global Constraints

以下条目从 `CLAUDE.md`（2026-08-26 版）的「工程铁律」「合规红线」「部署约束」与 `openspec/changes/m1-intake-quality-fixes/delivery-units.md` §5 **逐字复制**。**每个 Task 的验收隐含包含本节全部内容**，`subagent-driven-development` 会把这一段原样交给 reviewer 当注意力透镜。

### 工程铁律（不可违背）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
   *为什么*：业务写失败而幂等记录成功 → 系统判定"已执行"→ 永不重试。**幂等本是防重复的保护，拆开事务后变成永久丢失的保证。** 实证：`.51` 现网 2026-08-10 与 08-12 各丢一轮 `outbox`（幂等记录已落），用户没收到回复且永远不会补发，见 `docs/findings/2026-08-13-sqlite-事务归属冲突.md` §8.5。

> **本单元与这条的关系**：E **不新增 `effect_*` 节点、不新增任何一次写入、不新增任何一列**。已问台账全部由 `job_profile.asked_questions`（B 已经在 `effect_persist_draft` 的同一条 INSERT 里落库）与画像现值推导得到。reviewer 判据：E 的 diff 里 `@idempotent_effect` 装饰器数量、`conn.execute("INSERT` 的条数、`app/storage/db.py` 的 DDL 与 `_ADDED_COLUMNS` 清单**与 main 上逐字相同**；`tests/test_graph_idempotency.py`、`tests/test_transaction_ownership.py`、`tests/test_db_migration.py` 全绿。
> **台账为什么不落成新列**：台账的两个输入（按轮问过什么、画像现在有什么值）都已经落库且同生共死。再存一份"答没答/重问几次"就是第二个真源——它会和 `asked_questions` 漂移，而漂移**没有任何症状**（不报错、不失败，只是重问次数悄悄算错）。这与 design.md 决策 5 否决"把预算计数器放进 state"是同一条理由。

2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。

> **本单元与这条的关系**：`build_question_ledger()`（`app/agents/intake_question.py`）与 `_apply_question_ledger()`、`_answered_fields()`（`app/agents/intake_agent.py`）**全部是纯函数**——不读库、不写库、不打日志、同一输入必然同一输出。台账所需的库内数据由 `app/web/server.py` 的 `_run_turn` 查出来放进 `IntakeState`，`compute_intake_turn` 只透传，**不得**在 agent 层或 compute 节点里加任何一次 `conn.execute`。

5. **`temperature=0`；模型版本优先显式锁定**，禁止 `latest` 类别名。
   供应商不提供带版本号快照时（如 DeepSeek 公开 API 只有 `deepseek-chat` 这类会漂移的别名），**必须从 API 响应里取回实际的 `model` 字段并持久化**——配置里写的名字不算数，响应返回的才算。

> **本单元与这条的关系**：E **不改 `SYSTEM_PROMPT` 一个字**，因此 **`prompt_version` 保持单元 B 留下的 `intake-v4` 不动**（`intake-v5` 是单元 F 的，`delivery-units.md` §5 约定 3）。重问的判定与标注**全部在系统侧做**，不靠提示词告诉模型"这是重问"——那正是 design.md 决策 2 否决的"把地基建在模型自觉上"。reviewer 判据：E 的 diff 里不出现 `SYSTEM_PROMPT` 与 `prompt_version` 的任何改动；`tests/test_intake_agent.py` 里断言 `prompt_version="intake-v4"` 的用例保持绿。

### 合规红线

- **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。

> **本单元与这条的关系**：重问超限后系统**停止追问**，但**不得**因此替业务经理填任何字段值。摘掉一个子问题只意味着"不再问"，该字段随后由 `derive_unspecified_fields()` 列进未指定、由单元 D 的警示块摆到人面前——⛔ 不得用候选档位的第一项、模型的猜测或任何默认值把它悄悄填上。

- **AI 生成的 JD、拒信、邀约须带标识**（《AI 生成合成内容标识办法》2025-09-01 施行）。

> **本单元与这条的关系**：重问徽标（"（这个你刚才没答）"）是**系统按台账算出来的确定性事实**，不是 AI 生成内容，**不加**"AI 建议"标识。单元 C 的 `AI_OPTIONS_HINT` 只属于 `options`，⛔ 不得被复制到重问徽标上；反过来，重问问题若带 `options`，那组 `options` 的 AI 标识**必须照常渲染**——不能因为这个问题被标成重问就把标识吞掉。

- **模型全部走境内**，简历数据不出境。

> **本单元与这条的关系**：5.6 的 `2494103e` 回放测试**不去 `.51` 取 `conversation` 表的对话原文**（单元 D 的 Global Constraints 已把 conversation 原文排除在取数范围外）。回放序列按本仓库已逐字记载的事实重建，出处与已知局限写在测试的 docstring 里，见 Task 5 Step 1。

### 部署约束

1. **路径前缀就绪**：FastAPI `root_path=/hr/recruit-agent`，前端资源与接口调用**一律相对路径**，禁止硬编码 `/static/…` `/api/…`。验收标准是挂到任意子路径下都能正常工作，且有测试覆盖。

> `tests/test_static_frontend.py::test_index_html_has_no_absolute_paths` 扫描 `index.html` 里**每一个**引号/反引号字符串字面量，任何一段以 `/` 开头即失败——Task 4 新增的 CSS 类名（`reask` / `reask-badge`）与徽标文案同样受这条约束。

### 跨单元接口约定（`delivery-units.md` §5，逐字）

1. **F 的 `profile_patch` 结构升级不得穿透到 `profile_json`** —— 落库前拍平成裸值，理由见 §2.F

> 反向读：**E 的台账"已答"判定经由 `derive_unspecified_fields()` 只认裸值。** F 排在 E 之后且 §5.1 要求 F 自己拍平，E 不为它预留兼容分支，但 `_answered_fields()` 的 docstring 必须写明这个前提。

2. **C 的点选提交不改 API 契约** —— 否则失去 B ∥ C 的并行，理由见 §2.C

> C 已合并。E 会碰 `app/web/server.py` 与 `index.html`，**但仍然不得给 `ReplyRequest` 加字段**——重问信息是**下行**的（`payload.questions[].is_reask`，单元 A 已在契约里），上行请求体逐字不变。`tests/test_static_frontend.py::test_reply_api_contract_has_no_selected_options` 必须保持绿。

3. **B 与 F 都会改 `SYSTEM_PROMPT` → 各自升 `prompt_version`**（现为 `intake-v3`；B → `v4`，F → `v5`）。铁律 5

> E 不在这份名单里，见上方铁律 5 段。

4. **B 若为已问台账新增列，走 1.1 已建立的 `init_schema` 幂等加列路径**，不另起迁移机制（决策 10）；所有新列必须可空或有默认值，既有 15 个 job 的历史行不回填

> B 已经加完 `asked_questions` 列（`docs/findings/2026-08-26-unitB-已问台账列加列演练.md` 是它的加列演练记录）。**E 不加列**，因此这条对 E 退化成一条约束：⛔ 不许"顺手"再加一个 `question_ledger` 列。

5. **每个单元开工前必须 rebase 到最新 main** —— `app/agents/intake_agent.py` 与 `app/graph/nodes.py` 被 B/D/E/F 四个单元连续改动，是本批最热的两个文件

> **这是 E 的第一等约束，见下方「开工前置检查」。** E 依赖 D 的 `derive_unspecified_fields()`（5.5 的口径靠它成立）与 B 的 `MAX_ROUNDS` / `MAX_TOTAL_ROUNDS` / `asked_questions` 列。**D 未合并即开工，5.5 就没有落脚点**——那时唯一的写法是自己写一套平行标记逻辑，等 D 落地再拆掉，正是 `delivery-units.md` §3.1 把 D 排在 E 前面所要避免的返工。

### 明确不适用（reviewer 不必在本单元追这几条）

- 铁律 3（AI 评分持久化）、铁律 4（`evidence_ref` 非空）：本单元不写 `criterion_score`，代码库中亦无该表。
- 铁律 6（企微回调先落库）、铁律 7（`langgraph >= 1.0.10`）：本单元不接企微通道、不动依赖版本。
- 部署约束 2（8095 端口）、3（鉴权空壳）、4（Windows + venv，不引入容器）、5（M2 简历门槛）：本单元不改端口、不动鉴权中间件、不引入任何新依赖、不处理简历。
- 合规红线「禁止人脸/表情分析」「绝不用历史录用结果做监督信号」「候选人一次性邀请链接」：本单元不涉及。

---

## 开工前置检查（Task 1 之前，做不到就停下报告）

- [ ] `git fetch origin && git rebase origin/main`，工作区干净
- [ ] **确认单元 D 已合并**：`grep -n "def derive_unspecified_fields" app/agents/intake_agent.py`
      - 有输出 → D 已合并，按本计划执行
      - 无输出 → **D 还没合并，⛔ 停下报告，不要开工。** `delivery-units.md` §6 的执行顺序是 `B∥C → D → E`；提前开工的代价不是"多一次合并"，是 5.5 会被迫写一套等 D 落地就要拆掉的平行标记逻辑
- [ ] **确认单元 B 的两个预算常量与实际名字一致**：`grep -n "^MAX_ROUNDS\|^MAX_TOTAL_ROUNDS\|^MAX_QUESTIONS_PER_ROUND" app/agents/intake_agent.py`
      - 期望三行：`MAX_ROUNDS = 5`、`MAX_TOTAL_ROUNDS = 8`、`MAX_QUESTIONS_PER_ROUND = 3`
      - 名字或取值与这里不同 → **以仓库现状为准**，把本计划里引用这两个常量的地方按实际名字改掉，并在收尾时把这处差异写进交付报告。⛔ 不要反过来去改 B 已落地的常量
- [ ] **确认已问台账的落库载体形态**：`grep -n "asked_questions" app/storage/db.py app/graph/nodes.py app/web/server.py`
      - 期望：`job_profile.asked_questions TEXT NOT NULL DEFAULT '[]'`、`effect_persist_draft` 把它写进与画像草案**同一条 INSERT**、`_run_turn` 按 `ORDER BY version ASC` 读回
      - 与这里不同 → 停下报告。本计划的整个 Task 1/3 建立在"按轮的问题 payload 已经在库里"这个事实上
- [ ] 确认单元 C 已合并（Task 4 要改它的渲染函数）：`grep -c "renderQuestionBlock" app/web/static/index.html` 应为 ≥ 2
- [ ] 基线全绿：`./venv/bin/python -m pytest -q`，记下用例总数（E 的每个 Task 结束时只许增不许减）

---

## 交付单元边界

**本单元 = `openspec/changes/m1-intake-quality-fixes/tasks.md` 第 5 章（5.1–5.8），共 8 项。**
对应 `specs/intake-question-tracking/spec.md` 的全部四条 Requirement。设计依据：`design.md` 决策 2 与 Risks 第 3 条。

**Task 数量说明**：`delivery-units.md` §1 预估 4-5 个 plan Task，本计划实际 **5 个**。边界按"reviewer 能独立否决"划：台账纯函数（Task 1）与用它做判定的策略层（Task 2）失败模式不同——前者错了是数据算错，后者错了是该问的没问/不该问的还在问；编排透传（Task 3）改的是 state 与 SQL 读取，前端（Task 4）改的是渲染，5.8 的结论与 5.6 的真实回放（Task 5）是全链路验收。

### 触碰面（硬边界）

| 文件 | 性质 | 谁还会碰它 |
|---|---|---|
| `app/agents/intake_question.py` | 新增 `QuestionLedgerEntry` / `build_question_ledger()` | F（7.x 只读问题对象） |
| `app/agents/intake_agent.py` | 新增 `MAX_REASKS` / `_answered_fields()` / `_apply_question_ledger()`，`run_intake_turn` 接台账 | F（7.x），排在 E 之后 |
| `app/graph/state.py` | 新增一个 state 键 `asked_question_rounds` | F |
| `app/graph/nodes.py` | `compute_intake_turn` 多透传一个入参 | F |
| `app/web/server.py` | `_run_turn` 在既有那次查询里多攒一份按轮列表 | 无（D 已改完 confirm） |
| `app/web/static/index.html` | 重问徽标 + 左边框，替换掉内联文本前缀 | 无 |
| `tests/test_intake_question.py` `tests/test_intake_agent.py` `tests/test_graph_nodes.py` `tests/test_web_api.py` `tests/test_static_frontend.py` | 测试 | F |
| `openspec/changes/m1-intake-quality-fixes/tasks.md` | 第 5 章回勾 | G |

### 本单元不做的事

| 不做 | 属于谁 / 为什么 |
|---|---|
| 给"重问超限的字段"另起一套标记（state 键、列、`profile_json` 内部键） | **谁都不做。** D 合并后这条自动成立：字段没值，`derive_unspecified_fields()` 自然把它列进未指定（`delivery-units.md` §2.D）。多一套标记就多一个真源 |
| 新增 `job_profile` 列 / 新增 `effect_*` 节点 / 新增一次写入 | 谁都不做，见 Global Constraints 铁律 1 段 |
| 改 `SYSTEM_PROMPT` / 升 `prompt_version` | 谁都不做（铁律 5）。`intake-v5` 属单元 F |
| 给 `_repeats_earlier_assistant_turn` 换算法 | 谁都不做。5.8 的结论是**保留**并收窄职责，见 Task 5 |
| 把 `question_id` 从 `field` 改成 `field + aspect` 复合 id | 不在本批（design.md 决策 2「代价」段）。撞 id 按重问处理是已接受的近似，`MAX_REASKS=2` 就是给它留的余量 |
| 判断"用户这句回答是不是敷衍" | 谁都不做（design.md 决策 6「代价」）。E 的"已答"只看字段有没有值 |
| 从 `.51` 取 `conversation` 表原文做回放 | 不做，见 Global Constraints 合规红线段与 Task 5 Step 1 |

---

## 关键设计决定（reviewer 请重点看这五条）

### 决定 1：台账是**推导**出来的，不是**存**出来的

台账 = `build_question_ledger(asked_question_rounds, answered_fields=...)`，两个输入都已经落库：

- `asked_question_rounds` ← `job_profile.asked_questions` 按 `version` 升序的每一行（单元 B 落库，与画像草案同一条 INSERT）
- `answered_fields` ← 业务字段表 − `derive_unspecified_fields(本轮合并后的画像)`（单元 D 的函数）

tasks 5.1 要求"真源随画像落库，不只活在 checkpoint"——两个输入都随画像落在同一条 INSERT 里，这条要求由此满足，且**不需要新增任何存储**。相反，若把"答没答/重问几次"另存一份，它与 `asked_questions` 之间就会有漂移空间，而漂移没有任何症状。

### 决定 2：「已答」的判据**复用 D 的 `derive_unspecified_fields()`，不另写一套**

这是 5.5 能"自动成立"的全部技术理由。同一个函数同时回答两个问题：

- 这个子问题答没答（E 用它决定要不要重问）
- 这个字段进不进缺口警示（D 用它决定要不要拦确认）

两边同口径 ⇒ 一个子问题被重问上限摘掉之后，它的目标字段没有值，`derive_unspecified_fields()` 自然把它列进未指定，单元 D 的警示块自然把它摆到业务经理面前。**⛔ 不许在 E 里写"标记这个字段为超限未答"之类的平行逻辑**——那是 `delivery-units.md` §3.1 把 D 排在 E 前面所要避免的东西。

### 决定 3：轮次口径与 3.10 的对齐（⚠️ 最容易写错的一处）

单元 B 已落地**两个**口径，任一命中即收尾：`MAX_ROUNDS=5` 数**有产出轮**（`is_productive=1` 的行数），`MAX_TOTAL_ROUNDS=8` 数**总行数**（`round_count`）。E 与它们的关系必须逐字按下面这样成立：

1. **重问不会把一轮判成"有产出"。** `is_productive` 的判定式（`有新画像内容 or 问出了未问过的 question_id`）**一个字不改**。重问的 `question_id` 按定义已在 `asked_before` 里，因此不满足 `has_new_question`——这条在 B 落地时就成立了，E 只是不许破坏它。
2. **摘除超限重问不新增、也不消灭任何一轮。** 摘除发生在本来就要发生的那一轮**之内**，`job_profile` 仍然只写一行，`round_count` / `business_key` 语义逐字不变。⛔ 不得为"降级"另插一轮、⛔ 不得跳过写行。
3. **spec 的「不再消耗追问轮次」在本设计里的准确含义**：被摘掉的子问题不再产生任何问题条目，因此它既不能让本轮变成"有产出轮"（不吃 `MAX_ROUNDS`），也不会促使系统再开一轮去问它。它仍然占用它所在那一轮的 `round_count`（那一轮本来就要发生，用户已经说了话）。这样才不会出现"这个子问题不再问了，但轮次照扣"。
4. **副作用要预期到**：摘除后本轮 `questions` 可能变空，`is_complete = give_up or not questions` 于是为 True，会话提前进入确认。**这是想要的行为**（停止追问 + 交给 D 的缺口警示），但它改变了"什么时候进确认"，Task 2 必须有一条测试把它钉住。

### 决定 4：重问上限只对**未答**的子问题计数

摘除判据 = `not entry.is_answered and entry.ask_count >= MAX_ASKS_PER_QUESTION(3)`。

已答字段上的递进提问（`question_id = field` 撞 id，"要不要 ISO 26262" → "要哪个 ASIL 等级"）**不打重问标记、也不受上限约束**——它不是"你刚才没答"。这比 design.md Risks 第 3 条要求的"上限取 2 留余量"更安全，5.7 的测试两种口径下都通过。

### 决定 5：重问的视觉区分取「徽标 + 左边框」，且**不与文本前缀并存**

`_REASK_PREFIX`（"（这个你刚才没答）"）继续是**文本通道**的形态：它写进 `conversation.history_json`、也用于前端的纯文本降级气泡。Web 结构化渲染改成徽标 + 左边框，徽标文案**逐字取自同一个常量**——两个通道对同一件事必须是同一种说法。⛔ 不得让内联前缀与徽标同时出现（用户会看到两遍）。`tests/test_static_frontend.py::test_reask_prefix_stays_in_sync_with_backend` 的第一条断言（前后端常量不漂移）**保持不动**，第二条断言（使用点还在）按 Task 4 改成指向徽标使用点——那条断言的注释本身就写明"交付单元 E 会为了「重问问题的视觉区分」去改这一行"。

---

## File Structure

| 文件 | 职责 |
|---|---|
| `app/agents/intake_question.py` | 问题一等对象 + `question_id` 派生（A/B 已有）+ **`QuestionLedgerEntry` / `build_question_ledger()`（E 新增，纯数据推导，不含策略）** |
| `app/agents/intake_agent.py` | L3 策略层：**`MAX_REASKS` 上限常量、`_answered_fields()`（口径桥接到 D）、`_apply_question_ledger()`（打标记 + 摘超限）**，以及 `run_intake_turn` 里的接线 |
| `app/graph/state.py` | 新增 `asked_question_rounds`：按轮的问题 payload 列表，真源是库 |
| `app/graph/nodes.py` | `compute_intake_turn` 把新键透传给 `run_intake_turn`，不查库 |
| `app/web/server.py` | `_run_turn` 在**既有那一次** `asked_questions` 查询里多攒一份按轮列表，不加新查询 |
| `app/web/static/index.html` | 重问徽标与左边框；纯文本降级路径不变 |

---

### Task 1: 已问台账的纯函数（5.1 数据结构 / 5.2 已答判定 / 5.3 空转轮口径）

**Files:**
- Modify: `app/agents/intake_question.py`（在 `IntakeQuestion` 类定义**之后**、`render_questions_text()` **之前**插入）
- Test: `tests/test_intake_question.py`

**Interfaces:**
- Consumes: `app.agents.intake_question.IntakeQuestion.from_payload()`（已有）
- Produces:
  - `QuestionLedgerEntry`（frozen dataclass）：`question_id: str`、`field: str | None`、`ask_count: int`、`first_asked_round: int`、`last_asked_round: int`、`is_answered: bool`，只读属性 `reask_count -> int`
  - `build_question_ledger(asked_question_rounds: list[list[dict]], *, answered_fields: frozenset[str] | set[str]) -> dict[str, QuestionLedgerEntry]` —— Task 2 唯一的台账来源，**不许有第二份实现**

- [ ] **Step 1: 写失败的测试**

在 `tests/test_intake_question.py` 顶部的 import 区追加：

```python
from app.agents.intake_question import QuestionLedgerEntry, build_question_ledger
```

在文件末尾追加：

```python
def test_empty_ledger_for_a_job_that_has_never_asked_anything():
    assert build_question_ledger([], answered_fields=frozenset()) == {}
    assert build_question_ledger([[]], answered_fields=frozenset()) == {}


def test_ledger_counts_one_ask_per_round_the_question_appeared_in():
    """同一个 question_id 在第 0、2 轮各问过一次 = 问了 2 次、重问 1 次。"""
    rounds = [
        [{"text": "功能安全等级？", "field": "functional_safety"}],
        [{"text": "招几个人？", "field": "headcount"}],
        [{"text": "ASIL 这块到底要不要？", "field": "functional_safety"}],
    ]

    ledger = build_question_ledger(rounds, answered_fields=frozenset({"headcount"}))

    fs = ledger["functional_safety"]
    assert fs.ask_count == 2
    assert fs.reask_count == 1
    assert fs.first_asked_round == 0
    assert fs.last_asked_round == 2
    assert fs.is_answered is False
    assert ledger["headcount"].ask_count == 1
    assert ledger["headcount"].reask_count == 0
    assert ledger["headcount"].is_answered is True


def test_partially_answered_round_marks_only_the_answered_subquestion():
    """spec Scenario「部分回答」：问了两个，只答了一个，另一个保持已问未答。

    这正是 2494103e 第 3-4 轮的形状（IATF 16949 答了、ISO 26262 没答）。
    """
    rounds = [
        [
            {"text": "是否要求熟悉 IATF 16949？", "field": "core_skills"},
            {"text": "是否要求熟悉 ISO 26262？", "field": "functional_safety"},
        ]
    ]

    ledger = build_question_ledger(rounds, answered_fields=frozenset({"core_skills"}))

    assert ledger["core_skills"].is_answered is True
    assert ledger["functional_safety"].is_answered is False


def test_idle_round_never_flips_anything_to_answered():
    """spec Scenario「空转轮不改变已答状态」。

    空转轮的定义是"本轮未产出任何字段"，也就是 answered_fields 相对上一轮
    一个都没多。台账只从 answered_fields 读"答没答"，所以这条不是靠一段
    "如果是空转轮就……"的分支成立的，而是**结构上不可能违反**：没有新字段
    进来，就没有任何 entry 会翻成已答。
    """
    rounds = [
        [{"text": "功能安全等级？", "field": "functional_safety"}],
        [{"text": "ASIL 这块要不要？", "field": "functional_safety"}],
    ]
    answered = frozenset()  # 两轮都没产出任何字段

    ledger = build_question_ledger(rounds, answered_fields=answered)

    assert ledger["functional_safety"].is_answered is False
    assert ledger["functional_safety"].ask_count == 2


def test_free_text_question_is_never_marked_answered():
    """没有 field 的问题拿到的是 free:<hash> id，它不对应任何画像字段，
    因此永远判不出"已答"。这是 derive_question_id 已写明的降级代价：
    重问追踪只对拿得到 field 的问题成立。"""
    rounds = [[{"text": "具体车型与量产时间是怎么安排的？"}]]

    ledger = build_question_ledger(rounds, answered_fields=frozenset({"sop_projects"}))

    (entry,) = ledger.values()
    assert entry.question_id.startswith("free:")
    assert entry.field is None
    assert entry.is_answered is False


def test_duplicate_ids_inside_one_round_count_as_one_ask():
    """同一轮内撞 id 只能算问了一次。

    今天 _to_intake_questions 会在同一轮内去重，走不到这条路径；但历史行
    （.51 上 2026-08-19 之前写的）不受那个去重保护，台账必须自己扛住，
    否则一行脏数据就能让某个子问题凭空多出一次"重问"、提前触顶。
    """
    rounds = [
        [
            {"text": "招几个人？", "field": "headcount"},
            {"text": "这次计划招几位？", "field": "headcount"},
        ]
    ]

    ledger = build_question_ledger(rounds, answered_fields=frozenset())

    assert ledger["headcount"].ask_count == 1


def test_legacy_bare_string_rows_do_not_crash_the_ledger():
    """.51 现网 2026-08-18 之前的 outbox/台账行里 questions 是裸字符串数组。
    台账走 IntakeQuestion.from_payload 的同一条归一化路径，历史行照样能算。"""
    rounds = [["功能安全等级（ASIL）上有什么要求？"]]

    ledger = build_question_ledger(rounds, answered_fields=frozenset())

    (entry,) = ledger.values()
    assert entry.question_id.startswith("free:")
    assert entry.ask_count == 1


def test_ledger_entry_is_frozen():
    """台账条目会被多处读到，可变对象会让"谁改了它"变成一个要排查的问题
    ——与 IntakeQuestion 用 frozen=True 是同一条理由。"""
    import dataclasses

    entry = QuestionLedgerEntry(
        question_id="headcount",
        field="headcount",
        ask_count=1,
        first_asked_round=0,
        last_asked_round=0,
        is_answered=False,
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.ask_count = 2
```

若 `tests/test_intake_question.py` 顶部还没有 `import pytest`，一并补上（最后一条用例要用）。

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_intake_question.py -k "ledger" -v`
Expected: FAIL — `ImportError: cannot import name 'QuestionLedgerEntry' from 'app.agents.intake_question'`

- [ ] **Step 3: 写最小实现**

`app/agents/intake_question.py` 顶部的 import 改成（补一个 `replace`）：

```python
from dataclasses import dataclass, replace
```

在 `IntakeQuestion` 类定义结束之后、`def render_questions_text(` 之前插入：

```python
# 一个子问题最多重问几次，是**策略**，放在 app/agents/intake_agent.py 的
# MAX_REASKS 里与 MAX_ROUNDS / MAX_TOTAL_ROUNDS 并排。本模块只算**事实**
# （问了几轮、答没答），不做"够不够、要不要停"的判断——事实与策略分开，
# 才能在不动台账的前提下调上限。


@dataclass(frozen=True)
class QuestionLedgerEntry:
    """
    已问台账里的一条：这个子问题被问过几轮、第一次/最后一次在哪一轮、答了没有。

    frozen=True 与 IntakeQuestion 同理：台账会被判定层、渲染层、测试同时读到，
    可变对象会让"谁改了它"变成一个要排查的问题。
    """

    question_id: str
    field: str | None
    ask_count: int
    first_asked_round: int
    last_asked_round: int
    is_answered: bool

    @property
    def reask_count(self) -> int:
        """重问次数 = 问过的轮数 − 1（第一次问不算重问）。"""
        return max(self.ask_count - 1, 0)


def build_question_ledger(
    asked_question_rounds: list[list[dict]],
    *,
    answered_fields: frozenset[str] | set[str],
) -> dict[str, QuestionLedgerEntry]:
    """
    按轮的问题台账 → `question_id → QuestionLedgerEntry` 的纯推导（tasks 5.1）。

    **不新增任何存储。** 两个入参都来自已经落库的事实：
    - `asked_question_rounds`：`job_profile.asked_questions` 按 version 升序的
      每一行（单元 B 与画像草案写在同一条 INSERT 里），外层一项 = 一轮
    - `answered_fields`：业务字段表 − `derive_unspecified_fields(画像现值)`，
      由调用方算好传入（见 `app/agents/intake_agent.py` 的 `_answered_fields`）

    为什么"答没答"要由调用方传进来而不是这里自己算：那份判据是单元 D 的
    `derive_unspecified_fields()`，它住在 `intake_agent`，而 `intake_agent`
    已经 import 了本模块——本模块反向 import 会成环。更要紧的是口径：E 的
    "这个子问题答没答"与 D 的"这个字段进不进缺口警示"**必须是同一个函数的
    两面**，5.5 的「重问超限 → 目标字段计入未指定字段」才会自动成立，不需要
    第二套标记逻辑。

    `is_answered` 按 `question_id` 判，不按 payload 里的 `field` 判：
    `derive_question_id` 对野 field（模型拼错、幻觉字段名）会降级成
    `free:<hash>`，那时 payload 里的 `field` 仍是那个野名字。用 id 判就天然
    不会把一个不存在的字段名和真实字段表对上。

    返回值的键序 = 各 question_id **首次**被问到的顺序，因此
    `list(build_question_ledger(...))` 就是"此前问过的 question_id 并集"、
    且顺序稳定——同一份台账重放必然得到逐位相同的结果（工程铁律 1 要求节点
    从头重跑不产生差异）。
    """
    answered = frozenset(answered_fields)
    ledger: dict[str, QuestionLedgerEntry] = {}
    for round_index, payloads in enumerate(asked_question_rounds or []):
        for payload in payloads or []:
            question = IntakeQuestion.from_payload(
                {"text": payload} if isinstance(payload, str) else payload
            )
            question_id = question.question_id
            existing = ledger.get(question_id)
            if existing is None:
                ledger[question_id] = QuestionLedgerEntry(
                    question_id=question_id,
                    field=question.field if question_id == question.field else None,
                    ask_count=1,
                    first_asked_round=round_index,
                    last_asked_round=round_index,
                    is_answered=question_id in answered,
                )
            elif existing.last_asked_round != round_index:
                # 同一轮内撞 id 只算问了一次：历史行没有 _to_intake_questions
                # 的同轮去重保护，一行脏数据不该让某个子问题凭空多一次重问。
                ledger[question_id] = replace(
                    existing,
                    ask_count=existing.ask_count + 1,
                    last_asked_round=round_index,
                )
    return ledger
```

- [ ] **Step 4: 运行测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_intake_question.py -v`
Expected: PASS（含新增 8 条）

- [ ] **Step 5: 跑全量，确认没碰坏别人**

Run: `./venv/bin/python -m pytest -q`
Expected: PASS，用例总数 = 前置检查记下的基线 + 8

- [ ] **Step 6: 提交**

```bash
git add app/agents/intake_question.py tests/test_intake_question.py
git commit -m "feat(intake): 已问台账纯函数 build_question_ledger（tasks 5.1/5.2/5.3）"
```

---

### Task 2: `run_intake_turn` 接台账 —— 重问标注、重问上限、轮次口径对齐（5.4 后端 / 5.5 / 5.7）

**Files:**
- Modify: `app/agents/intake_agent.py`
- Test: `tests/test_intake_agent.py`

**Interfaces:**
- Consumes: Task 1 的 `build_question_ledger()` / `QuestionLedgerEntry`；单元 D 的 `derive_unspecified_fields(accumulated: dict) -> list[str]`；单元 B 的 `MAX_ROUNDS` / `MAX_TOTAL_ROUNDS` / `MAX_QUESTIONS_PER_ROUND`
- Produces:
  - `MAX_REASKS: int = 2`、`MAX_ASKS_PER_QUESTION: int = 3`（模块级常量，Task 5 的回放测试要 import）
  - `_answered_fields(accumulated: dict) -> frozenset[str]`
  - `_apply_question_ledger(questions: list[IntakeQuestion], ledger: dict[str, QuestionLedgerEntry]) -> tuple[list[IntakeQuestion], list[str]]`
  - `run_intake_turn(..., asked_question_rounds: list[list[dict]] | None = None)` —— Task 3 的编排层按这个名字传参

- [ ] **Step 1: 写失败的测试**

在 `tests/test_intake_agent.py` 顶部 import 区追加：

```python
from app.agents.intake_agent import (
    MAX_ASKS_PER_QUESTION,
    MAX_REASKS,
    derive_unspecified_fields,
)
from app.agents.intake_question import render_questions_text
```

（`derive_unspecified_fields` 若已被单元 D 的用例 import 过，就不要重复写这一行。）

在文件末尾追加：

```python
def _q(text: str, field: str | None = None) -> dict:
    """构造一个问题 payload，省得每条用例都手写一遍 dict。"""
    return {"text": text, "field": field, "options": [], "allow_free_text": True}


def _turn(responses, **kwargs):
    """跑一轮采集，默认参数取"第一轮"的形状，用例只覆盖自己关心的那几个。"""
    gateway = make_gateway(responses)
    params = {
        "history": [{"role": "user", "content": "要个嵌入式工程师"}],
        "round_count": 0,
        "productive_round_count": 0,
        "profile_patch_accumulated": {},
        "asked_question_ids_before": [],
        "previous_questions": [],
        "asked_question_rounds": [],
    }
    params.update(kwargs)
    return run_intake_turn(gateway, **params)


def test_unanswered_question_asked_again_is_marked_as_a_reask():
    """spec「重问必须显式标注」：重问同一个未答子问题，is_reask 必须为 True，
    渲染出来的文本必须带重问提示。"""
    result = _turn(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "ASIL 这块到底要不要？", "field": "functional_safety"}],
                    "profile_patch": {},
                }
            )
        ],
        round_count=1,
        productive_round_count=1,
        profile_patch_accumulated={"job_title": "嵌入式软件工程师"},
        asked_question_rounds=[[_q("功能安全等级（ASIL）上有什么要求？", "functional_safety")]],
    )

    (question,) = result.questions
    assert question.question_id == "functional_safety"
    assert question.is_reask is True
    assert "（这个你刚才没答）" in render_questions_text(result.questions)


def test_a_brand_new_question_is_not_marked_as_a_reask():
    result = _turn(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "招几个人？", "field": "headcount"}],
                    "profile_patch": {},
                }
            )
        ],
        round_count=1,
        productive_round_count=1,
        asked_question_rounds=[[_q("功能安全等级（ASIL）上有什么要求？", "functional_safety")]],
    )

    (question,) = result.questions
    assert question.is_reask is False


def test_answered_question_asked_again_is_not_a_reask():
    """spec 的重问标注是"这个你刚才没答"。字段已经有值了还问，那是**递进
    提问**（design.md 决策 2 接受的撞 id 近似），不是重问——打上重问标记会
    对用户撒谎。"""
    result = _turn(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "要哪个 ASIL 等级？", "field": "functional_safety"}],
                    "profile_patch": {},
                }
            )
        ],
        round_count=1,
        productive_round_count=1,
        profile_patch_accumulated={"functional_safety": "ASIL-B"},
        asked_question_rounds=[[_q("是否需要 ISO 26262 功能安全经验？", "functional_safety")]],
    )

    (question,) = result.questions
    assert question.is_reask is False


def test_reask_stops_after_the_cap_and_the_field_lands_in_unspecified():
    """
    spec「重问超限转未指定」+ tasks 5.5。

    上限取 2（问 1 次 + 重问 2 次 = 出现在 3 轮里）。第 4 次再问就必须被摘掉。
    "计入未指定字段"这一半**不是这里写的一段标记逻辑**——字段没值，单元 D 的
    derive_unspecified_fields 自然把它列进去。本条用例直接拿 D 的函数断言这一点，
    正是为了钉死"E 不许写第二套标记"（delivery-units.md §2.D）。
    """
    assert MAX_REASKS == 2
    assert MAX_ASKS_PER_QUESTION == 3

    asked = [[_q("功能安全等级？", "functional_safety")]] * MAX_ASKS_PER_QUESTION
    accumulated = {"job_title": "嵌入式软件工程师"}

    result = _turn(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "ASIL 到底要不要？", "field": "functional_safety"}],
                    "profile_patch": {},
                }
            )
        ],
        round_count=3,
        productive_round_count=1,
        profile_patch_accumulated=accumulated,
        asked_question_rounds=asked,
    )

    assert [q.question_id for q in result.questions] == []
    assert "functional_safety" in derive_unspecified_fields(accumulated)


def test_progressive_questions_on_an_answered_field_are_not_cut_off_early():
    """
    tasks 5.7 + design.md Risks 第 3 条：question_id = field 撞 id 的递进提问
    （"要不要 26262" → "要哪个 ASIL"）不能被上限过早掐断。

    这里给它问满 MAX_ASKS_PER_QUESTION 轮**且字段已有值**，仍然不摘——
    上限只对**未答**的子问题计数（本计划「关键设计决定 4」）。
    """
    asked = [[_q("是否需要 ISO 26262 功能安全经验？", "functional_safety")]] * MAX_ASKS_PER_QUESTION

    result = _turn(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "要哪个 ASIL 等级？", "field": "functional_safety"}],
                    "profile_patch": {},
                }
            )
        ],
        round_count=3,
        productive_round_count=2,
        profile_patch_accumulated={"functional_safety": "ASIL-B"},
        asked_question_rounds=asked,
    )

    assert [q.question_id for q in result.questions] == ["functional_safety"]
    assert result.questions[0].is_reask is False


def test_a_question_answered_in_this_very_turn_is_not_reasked():
    """
    用户这一轮刚答完的子问题，不能在同一轮的回复里被当成"你刚才没答"重问一遍。
    台账的 answered_fields 必须用**合并本轮 patch 之后**的画像算，不能只用
    上一轮的累积值——这是接线顺序错了就会当场对用户撒谎的一处。
    """
    result = _turn(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "要哪个 ASIL 等级？", "field": "functional_safety"}],
                    "profile_patch": {"functional_safety": "ASIL-B"},
                }
            )
        ],
        round_count=1,
        productive_round_count=1,
        asked_question_rounds=[[_q("功能安全等级？", "functional_safety")]],
    )

    (question,) = result.questions
    assert question.is_reask is False


def test_dropping_an_exhausted_reask_does_not_make_the_turn_productive():
    """
    轮次口径对齐（tasks 5.5 ↔ 3.10）：摘掉超限重问之后本轮没有任何新问题、
    也没有新画像内容，那就是一轮空转——is_productive 必须为 False，不吃
    MAX_ROUNDS 的有产出轮预算。判定式一个字没改，这条只是把它钉住。
    """
    asked = [[_q("功能安全等级？", "functional_safety")]] * MAX_ASKS_PER_QUESTION

    result = _turn(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "ASIL 到底要不要？", "field": "functional_safety"}],
                    "profile_patch": {},
                }
            )
        ],
        round_count=3,
        productive_round_count=1,
        profile_patch_accumulated={"job_title": "嵌入式软件工程师"},
        asked_question_rounds=asked,
    )

    assert result.is_productive is False
    assert result.is_complete is True  # 没有问题可问了，转确认，交给单元 D 的缺口警示


def test_a_plain_reask_within_the_cap_still_does_not_consume_the_productive_budget():
    """重问在上限之内照样下发，但它的 question_id 早在台账里，
    has_new_question 不成立——单元 B 落地时就是这个口径，E 不许破坏它。"""
    result = _turn(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "ASIL 这块到底要不要？", "field": "functional_safety"}],
                    "profile_patch": {},
                }
            )
        ],
        round_count=1,
        productive_round_count=1,
        profile_patch_accumulated={"job_title": "嵌入式软件工程师"},
        asked_question_rounds=[[_q("功能安全等级？", "functional_safety")]],
    )

    assert result.questions[0].is_reask is True
    assert result.is_productive is False


def test_off_topic_guidance_is_never_dropped_by_the_reask_cap():
    """
    离题轮的引导语走 is_job_related=False 的早返回分支，**不经过台账摘除**。
    没有这条保护，连说 3 句离题的话之后引导语会被当成"问到第 4 次的子问题"
    摘掉，用户拿到一个空气泡——比不改还糟。
    """
    guidance_round = [[_q("没听懂是不是用人需求，可以试试：'要招一个做XX的工程师'")]]

    result = _turn(
        [json.dumps({"is_job_related": False, "questions": [], "profile_patch": {}})],
        round_count=3,
        asked_question_rounds=guidance_round * MAX_ASKS_PER_QUESTION,
    )

    assert result.is_job_related is False
    assert len(result.questions) == 1
    assert result.questions[0].is_reask is False


def test_ledger_is_ignored_when_the_caller_does_not_pass_the_rounds():
    """向后兼容：没接上按轮台账的调用方（老测试、别的入口）行为与今天逐字一致
    ——不打重问标记、不摘任何问题。"""
    result = _turn(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "功能安全等级？", "field": "functional_safety"}],
                    "profile_patch": {},
                }
            )
        ],
        round_count=1,
        productive_round_count=1,
        asked_question_ids_before=["functional_safety"],
        asked_question_rounds=[],
    )

    (question,) = result.questions
    assert question.is_reask is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_intake_agent.py -k "reask or ledger or progressive or exhausted" -v`
Expected: FAIL — `ImportError: cannot import name 'MAX_REASKS'`（以及 `run_intake_turn() got an unexpected keyword argument 'asked_question_rounds'`）

- [ ] **Step 3: 写最小实现（四处外科手术式改动，不要整函数重写）**

**3a. 常量。** 在 `MAX_QUESTIONS_PER_ROUND = 3` 这一行**之后**插入：

```python
# 同一个子问题的重问上限（spec「重问次数上限」、tasks 5.5）：问 1 次 + 重问
# 2 次 = 最多出现在 3 轮里。取 2 是给 question_id = field 撞 id 的递进提问
# （"要不要 ISO 26262" → "要哪个 ASIL 等级"）留的余量，见 design.md Risks
# 第 3 条。上限只对**未答**的子问题计数，已答字段上的递进提问不受约束。
#
# 与 MAX_ROUNDS / MAX_TOTAL_ROUNDS 的关系（tasks 5.5 ↔ 3.10，别搞混）：
# 这三个数管的是三件不同的事——MAX_ROUNDS 管"有产出轮"能烧几轮，
# MAX_TOTAL_ROUNDS 管总共能有几轮，MAX_REASKS 管"同一个子问题"能问几次。
# 超限摘除只发生在本来就要发生的那一轮**之内**：不新增 job_profile 行、
# 不改 round_count、不改 is_productive 的判定式。被摘掉的子问题因此既不吃
# 有产出轮预算，也不会促使系统再开一轮去问它。
MAX_REASKS = 2
MAX_ASKS_PER_QUESTION = 1 + MAX_REASKS
```

**3b. import。** 把 `from app.agents.intake_question import ...` 那一行改成（在现有名字后追加两个，其余逐字不动）：

```python
from app.agents.intake_question import (
    IntakeQuestion,
    QuestionLedgerEntry,
    build_question_ledger,
    derive_question_id,
    render_questions_text,
)
```

**3c. 两个纯函数。** 在 `def _repeats_earlier_assistant_turn(` 这一行**之前**插入：

```python
def _answered_fields(accumulated: dict) -> frozenset[str]:
    """
    已答字段 = 业务字段表 − `derive_unspecified_fields(accumulated)`（单元 D）。

    **刻意复用 D 的那一个函数，不另写一套"这个字段算不算答过"的判据。**
    5.5 的「重问超限 → 目标字段计入未指定字段」靠的正是两边同口径：一个子
    问题被重问上限摘掉之后，它的目标字段没有值，`derive_unspecified_fields`
    自然把它列进未指定、单元 D 的缺口警示自然把它摆到业务经理面前。E 因此
    **不需要、也不得**再写一条平行的标记逻辑（delivery-units.md §2.D）。

    入参必须是**拍平后的裸值画像**（`{"headcount": 3}`，不是
    `{"headcount": {"value": 3, "source_quote": ...}}`）——这一条是
    `derive_unspecified_fields` 的前提，delivery-units.md §5 约定 1 要求
    第 7 章在落库前拍平，E 不为它预留兼容分支。
    """
    unspecified = set(derive_unspecified_fields(accumulated))
    return frozenset(
        name
        for name in JobProfile.model_json_schema()["properties"]
        if name not in _SYSTEM_MANAGED_FIELDS and name not in unspecified
    )


def _apply_question_ledger(
    questions: list[IntakeQuestion], ledger: dict[str, QuestionLedgerEntry]
) -> tuple[list[IntakeQuestion], list[str]]:
    """
    按已问台账处理本轮问题：给未答的重问打 `is_reask`，把超限的重问摘掉。

    返回 `(保留下来的问题, 被摘掉的 question_id 列表)`。第二个返回值目前只用于
    测试与将来的观测，**不进 IntakeTurnResult**——摘除这件事在持久层的唯一表征
    就是"那个字段仍然没有值"，多存一份就多一个会漂移的真源（tasks 5.1 的落库
    真源约定）。

    三条分支，顺序不能换：
      1. 台账里没有 → 全新问题，原样保留、不打标记
      2. 已答（字段有值）→ **递进提问**，不打重问标记（打了就是对用户撒谎：
         他刚才明明答了），也不受重问上限约束（design.md 决策 2 接受的撞 id
         近似，见 tasks 5.7）
      3. 未答且已问满 MAX_ASKS_PER_QUESTION 轮 → 摘掉，不再问；否则打 is_reask

    ⛔ 这里只摘问题，不碰 profile_patch、不填任何字段值。停止追问不等于
    系统可以替业务经理把这个字段定下来（合规红线「AI 不做自动淘汰/不替人决定」）。
    """
    kept: list[IntakeQuestion] = []
    dropped: list[str] = []
    for question in questions:
        entry = ledger.get(question.question_id)
        if entry is None or entry.is_answered:
            kept.append(question)
            continue
        if entry.ask_count >= MAX_ASKS_PER_QUESTION:
            dropped.append(question.question_id)
            continue
        kept.append(replace(question, is_reask=True))
    return kept, dropped
```

**3d. `_synthesize_fallback_question` 跳过已超限的字段。** 把它的签名与选目标那两行改成：

```python
def _synthesize_fallback_question(
    accumulated: dict,
    patch: dict,
    asked_question_ids_before: list[str],
    matched_terms: tuple[str, ...] = (),
    *,
    exhausted_question_ids: frozenset[str] = frozenset(),
) -> IntakeQuestion | None:
```

并把函数体里的这两行：

```python
    asked = set(asked_question_ids_before)
    target = next((name for name in missing if name not in asked), missing[0])
```

替换为：

```python
    # 已经问满重问上限的字段不再合成问题：合成出来也会被
    # _apply_question_ledger 当场摘掉，白跑一轮还给不出任何问题。
    candidates = [name for name in missing if name not in exhausted_question_ids]
    if not candidates:
        return None
    asked = set(asked_question_ids_before)
    target = next((name for name in candidates if name not in asked), candidates[0])
```

同时在它的 docstring 末尾追加一段：

```
    exhausted_question_ids 是已问满重问上限的 question_id 集合（第 5 章）。
    全部候选字段都超限时返回 None——那一轮就没有问题可发，会被判成零产出、
    转入确认，由单元 D 的缺口警示接手。
```

**3e. `run_intake_turn` 的接线。** 签名末尾追加一个关键字参数：

```python
    previous_questions: list[IntakeQuestion] | None = None,
    asked_question_rounds: list[list[dict]] | None = None,
) -> IntakeTurnResult:
```

在它的 docstring 末尾追加：

```
    asked_question_rounds = job_profile.asked_questions 按 version 升序的**每一
    行**（外层一项 = 一轮）。第 5 章的已问台账全部由它 + 画像现值推导，不另存
    状态。省略时台账为空，行为与接上之前逐字一致（不打重问标记、不摘任何问题）。
```

紧接函数体开头那几行局部变量，把

```python
    prior_questions = list(previous_questions or [])
```

改成

```python
    prior_questions = list(previous_questions or [])
    asked_rounds = [list(item or []) for item in (asked_question_rounds or [])]
```

然后把 `if not parsed.is_job_related:` 早返回分支**之后**、原来那段从
`# 两个口径任一命中即收尾` 到 `return IntakeTurnResult(` 之前的代码，按下面的**顺序**重排。逐条对照着改，⛔ 不要整段覆盖——单元 D 已经改过 `unspecified_fields=` 那一行与它上方的对照日志，那些改动必须原样留着：

```python
    # ① 先算本轮的 profile_patch。台账的"已答"判定必须包含用户**这一轮刚
    #    答上来**的字段，否则会把他刚答完的子问题当成"你刚才没答"再问一遍。
    reply_text = _last_user_text(history)
    vague = is_vague_reply(reply_text, asked_questions=prior_questions)
    profile_patch = (
        _drop_unchosen_candidate_values(
            parsed.profile_patch, reply_text=reply_text, previous_questions=prior_questions
        )
        if vague
        else parsed.profile_patch
    )

    # ② 建台账。answered_fields 用合并本轮 patch 之后的画像算（见 ①）。
    ledger = build_question_ledger(
        asked_rounds, answered_fields=_answered_fields({**accumulated, **profile_patch})
    )
    # 台账在手时，"此前问过的 question_id 并集"直接取它的键序（首问顺序），
    # 不再另用一份 asked_question_ids_before——同一个事实两份来源就有漂移空间。
    # 没传按轮台账的调用方仍走老入参，行为与今天逐字一致。
    asked_before = list(ledger) if asked_rounds else list(asked_question_ids_before or [])
    exhausted = frozenset(
        question_id
        for question_id, entry in ledger.items()
        if not entry.is_answered and entry.ask_count >= MAX_ASKS_PER_QUESTION
    )

    # ③ 轮次预算（口径与单元 B 逐字不变，任一命中即收尾）。
    at_round_limit = productive_rounds >= MAX_ROUNDS or round_count >= MAX_TOTAL_ROUNDS
    capped_questions = (
        [] if at_round_limit else _to_intake_questions(parsed.questions)[:MAX_QUESTIONS_PER_ROUND]
    )

    # ④ 模糊回复的强制兜底档位（单元 B，逻辑不变，只多传一个 exhausted）。
    if vague and not at_round_limit:
        matched_terms = _matched_terms_by_recency(history)
        capped_questions = _fill_missing_options(capped_questions, matched_terms)
        if not capped_questions:
            synthesized = _synthesize_fallback_question(
                accumulated,
                profile_patch,
                asked_before,
                matched_terms,
                exhausted_question_ids=exhausted,
            )
            capped_questions = [synthesized] if synthesized else []

    # ⑤ 台账落到本轮问题上：打重问标记、摘掉超限重问（tasks 5.4 / 5.5）。
    #    必须在 ⑥ 的逐字防线**之前**——下发文本会因为重问前缀而改变，防线要
    #    比对的是真正下发的那一版（design.md 决策 1「代价」）。
    capped_questions, _dropped_question_ids = _apply_question_ledger(capped_questions, ledger)

    # ⑥ 最后一道逐字防线（tasks 5.8 的结论：保留，职责收窄，见其 docstring）。
    stuck = not at_round_limit and _repeats_earlier_assistant_turn(
        render_questions_text(capped_questions), history
    )
    give_up = at_round_limit or stuck
    questions = [] if give_up else capped_questions

    # ⑦ 零产出轮判定（design.md 决策 5）。判定式**一个字未改**：重问的
    #    question_id 按定义已在 asked_before 里，因此不满足 has_new_question，
    #    重问轮不吃 MAX_ROUNDS 的有产出轮预算。
    has_new_profile_content = any(
        name not in accumulated or accumulated[name] != value
        for name, value in profile_patch.items()
    )
    has_new_question = any(question.question_id not in asked_before for question in questions)
```

> ⚠️ 两处**必须保留 main 上现状**、不要用本计划的文字覆盖：
> - `return IntakeTurnResult(...)` 里 `unspecified_fields=` 那一行 —— 单元 D 已把模型输出降级为对照，照 main 上的写法留着
> - D 加进来的那条经 `loggable_summary()` 脱敏的对照 debug 日志 —— 它的位置与内容一律不动

- [ ] **Step 4: 运行测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_intake_agent.py -v`
Expected: PASS

> **若单元 B/D 的既有用例在这一步变红，⛔ 不要改那条用例去迁就。** 本 Task 唯一可能影响既有行为的口径修正是 ④ 里 `_synthesize_fallback_question` 的第二个入参从 `parsed.profile_patch` 换成了摘除未选档位之后的 `profile_patch`（一个被摘掉的候选值不该算作"这个字段已经填上了"）。**停下报告**，把变红的用例名、它断言的旧行为、以及你判断该改代码还是该改用例的理由写进交付报告，交 Shao Peishen 定夺。

- [ ] **Step 5: 跑全量**

Run: `./venv/bin/python -m pytest -q`
Expected: PASS，用例总数 = Task 1 结束时的数 + 10

- [ ] **Step 6: 提交**

```bash
git add app/agents/intake_agent.py tests/test_intake_agent.py
git commit -m "feat(intake): 重问标注与重问次数上限，轮次口径与 3.10 对齐（tasks 5.4/5.5/5.7）"
```

---

### Task 3: 编排层与 API 把按轮台账透传下去（5.1 的落库真源接线）

**Files:**
- Modify: `app/graph/state.py`、`app/graph/nodes.py`、`app/web/server.py`
- Test: `tests/test_graph_nodes.py`、`tests/test_web_api.py`

**Interfaces:**
- Consumes: Task 2 的 `run_intake_turn(..., asked_question_rounds=...)`
- Produces: `IntakeState["asked_question_rounds"]: list[list[dict]]` —— 外层一项 = 一轮，内层是该轮 `IntakeQuestion.to_payload()` 的列表，由 `_run_turn` 从 `job_profile.asked_questions` 按 `version ASC` 读出

- [ ] **Step 1: 写失败的测试**

在 `tests/test_graph_nodes.py` 末尾追加：

```python
def test_compute_passes_the_per_round_ledger_through_to_the_agent(tmp_path):
    """
    compute_intake_turn 是 compute_* 节点：只透传，不查库（工程铁律 2）。
    按轮台账由 _run_turn 查出来放进 state，这里断言它真的到达了 agent——
    没到达的话重问标注会静默失效（不报错、不失败，只是从来不打标记）。
    """
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "ASIL 到底要不要？", "field": "functional_safety"}],
                    "profile_patch": {},
                }
            )
        ]
    )

    state = compute_intake_turn(
        {
            "job_id": "job1",
            "history": [{"role": "user", "content": "再说说"}],
            "round_count": 1,
            "productive_round_count": 1,
            "profile_patch_accumulated": {"job_title": "嵌入式软件工程师"},
            "asked_question_ids_before": ["functional_safety"],
            "previous_questions": [],
            "asked_question_rounds": [
                [{"text": "功能安全等级（ASIL）上有什么要求？", "field": "functional_safety"}]
            ],
        },
        gateway=gateway,
    )

    (question,) = state["pending_questions"]
    assert question["is_reask"] is True


def test_compute_still_works_without_the_per_round_ledger_key(tmp_path):
    """老调用方（没放这个键）行为与今天逐字一致，不打重问标记。"""
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "功能安全等级？", "field": "functional_safety"}],
                    "profile_patch": {},
                }
            )
        ]
    )

    state = compute_intake_turn(
        {
            "job_id": "job1",
            "history": [{"role": "user", "content": "再说说"}],
            "round_count": 1,
            "productive_round_count": 1,
            "profile_patch_accumulated": {},
            "asked_question_ids_before": ["functional_safety"],
            "previous_questions": [],
        },
        gateway=gateway,
    )

    (question,) = state["pending_questions"]
    assert question["is_reask"] is False
```

在 `tests/test_web_api.py` 末尾追加：

```python
def test_reask_is_marked_end_to_end_from_the_persisted_ledger(tmp_path):
    """
    端到端：第 1 轮问功能安全、用户答别的，第 2 轮再问同一个子问题，
    API 响应里那条问题必须带 is_reask=true。

    这条用例走的是真实取数路径（job_profile.asked_questions → _run_turn →
    state → agent → payload），它是"台账真源随画像落库"（tasks 5.1）唯一
    的端到端证明——前面几条都是拿手搓的 state 喂进去的。
    """
    responses = [
        json.dumps(
            {
                "is_job_related": True,
                "questions": [{"text": "功能安全等级（ASIL）上有什么要求？", "field": "functional_safety"}],
                "profile_patch": {"job_title": "嵌入式软件工程师"},
            }
        ),
        json.dumps(
            {
                "is_job_related": True,
                "questions": [{"text": "ASIL 这块到底要不要？", "field": "functional_safety"}],
                "profile_patch": {"headcount": 2},
            }
        ),
    ]
    client = make_app(tmp_path, responses)

    job_id = client.post("/api/jobs", json={"message": "要个嵌入式工程师"}).json()["job_id"]
    body = client.post(f"/api/jobs/{job_id}/reply", json={"message": "招 2 个"}).json()

    (question,) = body["message"]["payload"]["questions"]
    assert question["question_id"] == "functional_safety"
    assert question["is_reask"] is True
    assert "（这个你刚才没答）" in body["message"]["payload"]["questions_text"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_graph_nodes.py tests/test_web_api.py -k "reask or per_round" -v`
Expected: FAIL — `assert False is True`（`is_reask` 还是 False，因为按轮台账没有被传下去）

- [ ] **Step 3: 写最小实现**

**3a. `app/graph/state.py`**：在 `previous_questions: list[dict]` 那一段**之后**插入：

```python
    # 每一轮问出的问题（`IntakeQuestion.to_payload()` 的列表），外层一项 = 一轮，
    # 按 version 升序。由 _run_turn 从 job_profile.asked_questions 读出，与
    # asked_question_ids_before 同源——后者是它拍平后的并集。
    #
    # 第 5 章的已问台账（问了几轮 / 答没答 / 重问几次）全部由它 + 画像现值
    # **推导**，不在 state 或库里另存一份状态：多存一份就多一个会漂移的真源，
    # 而漂移没有任何症状（不报错、不失败，只是重问次数悄悄算错）。这与本文件
    # 开头"真源是数据库、checkpoint 只是执行过程快照"是同一条理由。
    asked_question_rounds: list[list[dict]]
```

**3b. `app/graph/nodes.py`**：在 `compute_intake_turn` 调用 `run_intake_turn` 的参数表里，紧跟 `previous_questions=previous_questions,` 之后加一行：

```python
        # 第 5 章的已问台账（重问标注与重问上限）由它推导。compute_* 是纯函数，
        # 不自己查库——这份数据由 app/web/server.py 的 _run_turn 放进 state。
        asked_question_rounds=list(state.get("asked_question_rounds", [])),
```

**3c. `app/web/server.py`**：`_run_turn` 里读 `asked_rows` 的那个循环改成（**不新增查询**，就在既有那一次里多攒一份）：

```python
        asked_question_ids_before: list[str] = []
        previous_questions: list[dict] = []
        asked_question_rounds: list[list[dict]] = []
        for (raw,) in asked_rows:
            # 历史行（.51 上 2026-08-19 之前写的）这一列是默认值 '[]'；老库补列
            # 时也拿到 '[]'。两条路径都不需要回填。
            payloads = json.loads(raw or "[]")
            # 按轮保留一份：第 5 章的重问台账要知道"这个子问题出现在几轮里"，
            # 拍平后的并集算不出次数。空轮也要占一项，轮次下标才对得上。
            asked_question_rounds.append(payloads)
            previous_questions = payloads
            for payload in payloads:
                question_id = payload.get("question_id")
                if question_id and question_id not in asked_question_ids_before:
                    asked_question_ids_before.append(question_id)
```

并在下面 `state = {` 字典里，紧跟 `"previous_questions": previous_questions,` 之后加一行：

```python
            "asked_question_rounds": asked_question_rounds,
```

- [ ] **Step 4: 运行测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_graph_nodes.py tests/test_web_api.py -v`
Expected: PASS

- [ ] **Step 5: 跑全量**

Run: `./venv/bin/python -m pytest -q`
Expected: PASS，用例总数 = Task 2 结束时的数 + 3

- [ ] **Step 6: 提交**

```bash
git add app/graph/state.py app/graph/nodes.py app/web/server.py tests/test_graph_nodes.py tests/test_web_api.py
git commit -m "feat(intake): 按轮已问台账从库透传到 agent，重问标注端到端成立（tasks 5.1）"
```

---

### Task 4: 重问在界面上与新问题可区分（5.4 前端）

**Files:**
- Modify: `app/web/static/index.html`
- Test: `tests/test_static_frontend.py`

**Interfaces:**
- Consumes: `payload.questions[].is_reask`（单元 A 的契约，Task 2/3 开始真正置 True）、后端常量 `app.agents.intake_question._REASK_PREFIX`
- Produces: CSS 类 `qblock reask` 与 `reask-badge`；⛔ 不改任何请求体契约

- [ ] **Step 1: 写失败的测试**

先**替换** `tests/test_static_frontend.py::test_reask_prefix_stays_in_sync_with_backend` 里第二条断言那一段（从 `# 光有常量声明不够` 的注释块到该断言结束），换成：

```python
    # 光有常量声明不够：常量声明可以在使用点被删掉之后仍然留在文件里，
    # 上面那条断言照样通过，却已经不再对用户可见（is_reask 不再触发任何
    # 重问提示）。这条断言锁住的是使用点本身，不是常量声明。
    #
    # 2026-08-26（交付单元 E，tasks 5.4）：使用点从"拼进 textContent 的内联
    # 前缀"改成"徽标节点的文案"。改的是形态不是用词——徽标文案仍然逐字取自
    # 同一个常量，上面那条防漂移断言一个字没动。⛔ 内联前缀与徽标不得并存，
    # 否则用户会看到两遍。
    assert "badge.textContent = REASK_PREFIX;" in INDEX_HTML, (
        "REASK_PREFIX 的使用点（重问徽标的文案）不见了——"
        "常量还在文件里不代表还在生效，用户可能已经看不到重问提示了。"
    )
    assert (
        'line.textContent = (q && q.is_reask ? REASK_PREFIX : "") + questionText(q);'
        not in INDEX_HTML
    ), "内联前缀与徽标同时存在，用户会看到两遍重问提示。"
```

再在文件末尾追加：

```python
def test_reask_question_is_visually_distinguishable_from_a_new_question():
    """
    spec「重问必须显式标注」：重问 SHALL 与新问题在界面上可区分。

    光有文本前缀不够——它和问题正文同字号同颜色，混在两三条新问题里一眼看
    不出来，那正是 tasks 5.4 说的"混编成看起来是新问题的表述"。这里锁住的是
    **结构性**的区分：重问块多一个 class、多一个徽标节点。
    """
    assert 'block.classList.add("reask");' in INDEX_HTML
    assert 'badge.className = "reask-badge";' in INDEX_HTML
    # 样式必须真的存在，否则 class 加了也看不出区别
    assert ".qblock.reask" in INDEX_HTML
    assert ".reask-badge" in INDEX_HTML


def test_reask_badge_is_not_labelled_as_ai_generated_content():
    """
    重问徽标是系统按已问台账算出来的确定性事实，不是 AI 生成内容，
    ⛔ 不加"AI 建议"标识——那会把一条事实伪装成建议。AI_OPTIONS_HINT
    只属于 options（单元 C），两者不得串台。
    """
    badge_block = INDEX_HTML.split('badge.className = "reask-badge";', 1)[1].split(
        "block.appendChild", 1
    )[0]

    assert "AI_OPTIONS_HINT" not in badge_block


def test_options_disclosure_still_renders_on_a_reask_question():
    """重问问题若带 options，那组 options 的 AI 标识必须照常渲染——
    不能因为这个问题被标成重问就把标识吞掉（合规红线）。
    锁的是"标识分支与重问分支互不嵌套"这个结构事实。"""
    options_branch = INDEX_HTML.split("if (options.length > 0) {", 1)[1]

    assert "hint.textContent = AI_OPTIONS_HINT;" in options_branch
    assert "q.is_reask" not in options_branch.split("block.appendChild(group);", 1)[0]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_static_frontend.py -v`
Expected: FAIL — `assert 'block.classList.add("reask");' in INDEX_HTML`

- [ ] **Step 3: 写最小实现**

**3a. 样式。** 在 `<style>` 里 `.qblock + .qblock { ... }` 这一行**之后**插入：

```css
  /* 重问的视觉区分（tasks 5.4）：左边框 + 徽标。光靠文本前缀不够——它和
     问题正文同字号同颜色，混在两三条新问题里一眼看不出来。 */
  .qblock.reask { border-left: 3px solid #fd7e14; padding-left: 10px; }
  .reask-badge { display: inline-block; background: #fff3cd; border: 1px solid #ffe69c; color: #664d03; border-radius: 4px; padding: 1px 6px; margin-right: 6px; font-size: 12px; vertical-align: middle; }
```

**3b. 渲染。** 把 `renderQuestionBlock` 里这三行：

```js
      const line = document.createElement("div");
      // 重问前缀与后端 render_questions_text() 保持逐字一致：用户看到的问题文本
      // 必须与写进 conversation history 的那一份相同。判定 is_reask 属第 5 章
      // （tasks 5.4），这里只读不写，也不做更强的视觉区分。
      line.textContent = (q && q.is_reask ? REASK_PREFIX : "") + questionText(q);
      block.appendChild(line);
```

替换为：

```js
      const line = document.createElement("div");
      // 重问的视觉区分（tasks 5.4）：Web 通道用「徽标 + 左边框」，纯文本通道
      // （conversation history、下面那条 questions_text 降级气泡）仍用后端
      // render_questions_text() 拼的内联前缀。两个通道形态不同，**用词是同一
      // 份**——徽标文案逐字取自 REASK_PREFIX，后端改了这个常量而这里没跟，
      // tests/test_static_frontend.py 的强断言会失败。
      // ⛔ 徽标与内联前缀不得并存：并存会让用户看到两遍重问提示。
      if (q && q.is_reask) {
        block.classList.add("reask");
        const badge = document.createElement("span");
        badge.className = "reask-badge";
        badge.textContent = REASK_PREFIX;
        line.appendChild(badge);
      }
      line.appendChild(document.createTextNode(questionText(q)));
      block.appendChild(line);
```

> `block.dataset.qtext` 那一行**不动**：拼回复文本用的仍然是不带任何提示的问题原文（否则会污染第 7 章的来源子串判定）。

- [ ] **Step 4: 运行测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_static_frontend.py -v`
Expected: PASS（含 `test_index_html_has_no_absolute_paths`、`test_no_option_is_pre_selected`、`test_reply_api_contract_has_no_selected_options` 三条既有强断言）

- [ ] **Step 5: 手工验证（这一步不可省，见 `delivery-units.md` §2.C 关于前端"可独立测试成色最弱"的说明）**

```bash
./venv/bin/python -m uvicorn app.web.server:app --port 8095
```

浏览器开 `http://127.0.0.1:8095/`，跑一遍：提一个用人需求 → 对某个问题不作答、只答另一个 → 下一轮看那条未答的子问题：**有橙色左边框 + 黄色徽标，且徽标文案只出现一次**。截图或记录观察结果写进交付报告。

> ⏸ **若本机起不了服务或没有可用浏览器**：如实登记「留步：5.4 手工验证未做，原因 <具体原因>」，**不要把这一步标成已完成**。自动化断言只能锁住结构，锁不住"看起来真的能区分"。

- [ ] **Step 6: 提交**

```bash
git add app/web/static/index.html tests/test_static_frontend.py
git commit -m "feat(web): 重问以徽标与左边框与新问题区分（tasks 5.4）"
```

---

### Task 5: `_repeats_earlier_assistant_turn` 的去留结论（5.8）+ `2494103e` 真实回放（5.6）+ 收尾

**Files:**
- Modify: `app/agents/intake_agent.py`（只改 `_repeats_earlier_assistant_turn` 的 docstring）
- Modify: `openspec/changes/m1-intake-quality-fixes/tasks.md`（第 5 章回勾）
- Test: `tests/test_intake_agent.py`

**Interfaces:**
- Consumes: Task 1–4 的全部产出
- Produces: 无新接口。本 Task 的产出是一个**结论**与一条全链路回放用例

- [ ] **Step 1: 写 5.6 的真实回放测试**

在 `tests/test_intake_agent.py` 末尾追加：

```python
def test_replay_2494103e_iatf_and_iso26262_sequence():
    """
    tasks 5.6 · 真实回放：`2494103e`（采购岗）第 3-4 轮的 IATF 16949 /
    ISO 26262 序列。

    **前置事实的出处**（本仓库内已逐字记载，不需要也不去 .51 取对话原文）：
    - `openspec/changes/m1-intake-quality-fixes/proposal.md` 第 7 行：
      "第 3 轮把「IATF 16949 / ISO 26262」打包成一个问题串，用户只答了前者；
      第 4 轮系统把 ISO 26262 拆出来重问，措辞不同、话题相同。2026-08-11 上线
      的逐字重复检测（_repeats_earlier_assistant_turn）按定义抓不到——原文本来
      就不一样。"
    - `docs/m1-demo-pilot-feedback.md`：该会话自身的两条原子性不变式都是绿的，
      没有丢消息，所以"用户体感重复"确实来自换措辞重问，而不是投递丢失。

    **本用例的边界（如实写在这里，不要在别处宣称更强的结论）**：它回放的是
    那次事故的**形状**（打包提问 → 部分回答 → 换措辞重问），不是生产库里逐
    字节的原始 turn 文本——`.51` 的 conversation 原文不在取数范围内（单元 D
    的 Global Constraints）。它证明的是"这个形状现在会被正确追踪"，不是"这
    段字节序列被原样重放过"。
    """
    accumulated = {"job_title": "采购工程师", "department": "采购部"}

    # 第 3 轮：SYSTEM_PROMPT 的拆分规则要求两个议题拆成两条（spec Scenario
    # 「多个议题必须拆分」）。这一轮两条都是新问题，都不带重问标记。
    round3 = _turn(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [
                        {"text": "是否要求熟悉 IATF 16949？", "field": "core_skills"},
                        {"text": "是否要求熟悉 ISO 26262？", "field": "functional_safety"},
                    ],
                    "profile_patch": {},
                }
            )
        ],
        round_count=2,
        productive_round_count=2,
        profile_patch_accumulated=accumulated,
        asked_question_rounds=[[], []],
    )
    assert [q.question_id for q in round3.questions] == ["core_skills", "functional_safety"]
    assert [q.is_reask for q in round3.questions] == [False, False]

    # 第 4 轮：用户只答了 IATF 16949。系统换措辞重问 ISO 26262——question_id
    # 必须与首问一致（换措辞不改 id），且必须带重问标注。
    asked_after_round3 = [[], [], [q.to_payload() for q in round3.asked_questions]]
    accumulated_after_round3 = {
        **accumulated,
        "core_skills": [{"name": "IATF 16949", "required": True}],
    }
    round4 = _turn(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [
                        {"text": "功能安全 ISO 26262 这块有硬性要求吗？", "field": "functional_safety"}
                    ],
                    "profile_patch": {},
                }
            )
        ],
        round_count=3,
        productive_round_count=3,
        profile_patch_accumulated=accumulated_after_round3,
        asked_question_rounds=asked_after_round3,
    )

    (reasked,) = round4.questions
    assert reasked.question_id == "functional_safety"  # 换措辞不改 id
    assert reasked.is_reask is True                     # 重问带标注
    assert "（这个你刚才没答）" in round4.questions_text
    # 已答的那一条没有被重问：用户答过 IATF 之后系统不再问它
    assert "core_skills" not in [q.question_id for q in round4.questions]
    # 这一轮既没有新画像内容也没有新 question_id → 不吃有产出轮预算
    assert round4.is_productive is False


def test_replay_2494103e_stops_reasking_iso26262_after_the_cap():
    """
    tasks 5.5 在真实序列上的收口：ISO 26262 问到第 3 轮仍无回答，第 4 次
    不再问；它的目标字段由单元 D 的 derive_unspecified_fields 自然列进未指定
    ——E 这边没有、也不该有任何一行"标记为超限未答"的代码。
    """
    accumulated = {
        "job_title": "采购工程师",
        "department": "采购部",
        "core_skills": [{"name": "IATF 16949", "required": True}],
    }
    asked = [[_q("是否要求熟悉 ISO 26262？", "functional_safety")]] * MAX_ASKS_PER_QUESTION

    result = _turn(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [
                        {"text": "26262 的事还得确认一下，有要求吗？", "field": "functional_safety"}
                    ],
                    "profile_patch": {},
                }
            )
        ],
        round_count=MAX_ASKS_PER_QUESTION,
        productive_round_count=2,
        profile_patch_accumulated=accumulated,
        asked_question_rounds=asked,
    )

    assert result.questions == []
    assert result.is_productive is False
    assert "functional_safety" in derive_unspecified_fields(accumulated)


def test_verbatim_repeat_detection_still_guards_jobs_with_an_empty_ledger():
    """
    tasks 5.8 的结论（保留逐字防线）在测试里的形态。

    `.51` 现网既有 15 个 job 的 `asked_questions` 全是列默认值 `'[]'`——单元 B
    加列时按约定**不回填历史行**（delivery-units.md §5 约定 4）。这些会话继续
    对话时台账恒为空，一个重问标记都不会打、重问上限一次都不会触发，兜住
    "模型 temperature=0 下原样重放上一轮"的**只有** _repeats_earlier_assistant_turn。

    这条用例红了就说明有人把那道防线删了，而删除的症状只在台账为空的 job 上
    出现——本地新建的测试库每一轮都有台账，日常测试根本走不到。
    """
    text = "具体车型与量产时间是怎么安排的？"
    result = _turn(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": text}],
                    "profile_patch": {},
                }
            )
        ],
        history=[
            {"role": "user", "content": "要个嵌入式工程师"},
            {"role": "assistant", "content": text},
            {"role": "user", "content": "嗯"},
        ],
        round_count=1,
        productive_round_count=1,
        # 历史行的形态：有这一轮，但那一列是 '[]'
        asked_question_rounds=[[]],
    )

    assert result.questions == []      # 逐字重复 → stuck → 当场收尾
    assert result.is_complete is True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_intake_agent.py -k "replay or verbatim" -v`
Expected: 三条回放用例应当在 Task 1–4 完成后**直接通过**（它们验证的是已实现的行为）。若有任何一条红：⛔ **不要改这些断言去迁就实现**——它们逐条对应 spec 的 Scenario 与 tasks 5.6/5.5/5.8。停下、定位实现里的偏差、改实现。

- [ ] **Step 3: 把 5.8 的结论写进代码注释**

把 `app/agents/intake_agent.py` 里 `_repeats_earlier_assistant_turn` 的 docstring **最后一段**（以"这道防线**保留不动**。"开头、到"本单元不动它的判定逻辑。"结束的那一段）整段替换为：

```
    **tasks 5.8 的结论（2026-08-26，交付单元 E）：保留，职责收窄为兜底。**

    结论是"保留"而不是"由 question_id 追踪取代"，理由有三条，任一条单独成立
    就足以保留它：

    1. **台账为空的 job 上，本函数是唯一的防线。** `.51` 现网既有 15 个 job 的
       `asked_questions` 全是列默认值 `'[]'`——单元 B 加列时按约定不回填历史行
       （delivery-units.md §5 约定 4）。这些会话继续对话时台账恒为空，一个重问
       标记都不会打、重问上限一次都不会触发。删掉本函数，它们当场退回 2026-08-11
       之前的行为。
    2. **两者判定的对象不同。** 台账管"同一个子问题被问了几次"，本函数管"整轮
       assistant 文本是否逐字重放"——后者包含问题以外的内容（引导语、客套话），
       也覆盖"每个问题各自都没到上限、但整轮说辞与之前一模一样"这种组合。另外
       没有 field 的问题走 `derive_question_id` 的文本哈希降级分支
       （`free:<hash>`），换措辞就换 id，台账认不出那是同一个问题——那一段在
       `derive_question_id` 的 docstring 里写明是"降级，不是等价方案"。
    3. **代价极低。** 一次字符串归一化比对，命中即收尾，已有测试覆盖。

    **职责边界（改这里之前先读完）**：本函数现在是**最后一道**防线，不是第一道。
    执行顺序是「台账摘除 + 打重问标记」在前、本函数在后（见 `run_intake_turn`
    的步骤 ⑤⑥）。由此带来一个必须知道的行为变化：**被标成重问的问题会带上
    `_REASK_PREFIX` 前缀，与历史里那条不带前缀的原文不再逐字相同，本函数对它
    天然不再命中**。这不是回归——重问从此由重问次数上限（`MAX_REASKS`）管，
    本函数只管台账管不着的那些情况。若有人想"修好"这一点（比如比对时剥掉
    前缀），先想清楚：那会让重问在第 2 次就被当成 stuck 当场收尾，
    `MAX_REASKS=2` 给递进提问留的余量当场作废。

    同期取证（docs/findings/2026-08-13-sqlite-事务归属冲突.md §8.5）证明"用户
    体感重复"还有第三种成因：投递丢失导致用户没收到上一轮回复，模型从
    checkpoint 读到自己问过、便道歉并换措辞重问。那一层已由
    fix-sqlite-transaction-ownership 修复，与本函数无关。
```

- [ ] **Step 4: 跑全量**

Run: `./venv/bin/python -m pytest -q`
Expected: PASS，用例总数 = Task 4 结束时的数 + 3

- [ ] **Step 5: 回勾 OpenSpec 的 WBS**

把 `openspec/changes/m1-intake-quality-fixes/tasks.md` 第 5 章的 8 个 checkbox 全部从 `- [ ]` 改成 `- [x]`（5.1–5.8）。

⛔ **只勾第 5 章**，其它章节一个字不动（并发协议：别人的改动出现在 `git status` 里是正常的）。
⛔ **不要在本单元跑 `openspec-archive-change`**：归档要等全部章节勾完，属交付单元 G 的 8.9，且归档顺序是 `m1-job-profile-intake` 先、本包后。

- [ ] **Step 6: 提交**

```bash
git add app/agents/intake_agent.py tests/test_intake_agent.py openspec/changes/m1-intake-quality-fixes/tasks.md
git commit -m "docs(intake): 5.8 逐字防线保留结论 + 2494103e 回放，第 5 章回勾（tasks 5.6/5.8）"
```

- [ ] **Step 7: 交付报告**

写清楚：新增/修改文件清单、每个 Task 的 commit hash、用例总数从多少涨到多少、Task 4 Step 5 的手工验证结果（做了 / ⏸ 留步及原因）、以及 Task 2 Step 4 若有既有用例变红时的处置。

---

## Self-Review：spec 覆盖矩阵

`specs/intake-question-tracking/spec.md` 四条 Requirement 逐条落到 Task：

| spec Requirement / Scenario | 落在哪 |
|---|---|
| **子问题的稳定标识与拆分** | 地基由单元 A/B 交付（`derive_question_id`、`SYSTEM_PROMPT` 的拆分规则）。E 的 Task 5 回放用例 `test_replay_2494103e_iatf_and_iso26262_sequence` 断言"两个议题拆成两条、各有独立标识" |
| ↳ Scenario: 多个议题必须拆分 | Task 5 回放第 3 轮：`[q.question_id for q in round3.questions] == ["core_skills", "functional_safety"]` |
| ↳ Scenario: 换措辞不改标识 | Task 5 回放第 4 轮：`reasked.question_id == "functional_safety"`；另有单元 A 的 `test_question_id_ignores_wording_when_field_present` |
| **已问未答的判定** | Task 1 `build_question_ledger()`（判定本体）+ Task 2 的接线（用合并本轮 patch 之后的画像算 answered_fields） |
| ↳ Scenario: 部分回答 | Task 1 `test_partially_answered_round_marks_only_the_answered_subquestion` |
| ↳ Scenario: 空转轮不改变已答状态 | Task 1 `test_idle_round_never_flips_anything_to_answered` |
| **重问必须显式标注** | Task 2（后端置 `is_reask`）+ Task 4（界面徽标与左边框） |
| ↳ Scenario: 重问带标注 | Task 2 `test_unanswered_question_asked_again_is_marked_as_a_reask` |
| ↳ Scenario: 重问不伪装成新问题 | Task 4 `test_reask_question_is_visually_distinguishable_from_a_new_question` + Task 2 `test_a_brand_new_question_is_not_marked_as_a_reask` |
| **重问次数上限** | Task 2 `MAX_REASKS` / `_apply_question_ledger()` |
| ↳ Scenario: 重问超限转未指定 | Task 2 `test_reask_stops_after_the_cap_and_the_field_lands_in_unspecified` + Task 5 `test_replay_2494103e_stops_reasking_iso26262_after_the_cap`（两条都直接拿单元 D 的 `derive_unspecified_fields` 断言，不另写标记） |

## Self-Review：`tasks.md` 第 5 章逐项映射

| tasks | 落在哪 | 备注 |
|---|---|---|
| 5.1 台账（已问轮次 / 是否已答 / 重问次数），真源随画像落库 | Task 1（数据结构与推导）+ Task 3（`asked_question_rounds` 从库到 state 的接线） | **不新增列、不新增 effect、不新增写入**：两个输入都已随画像落在同一条 INSERT 里 |
| 5.2 已答判定 | Task 1 `is_answered` + Task 2 步骤 ①② 的顺序（用合并本轮 patch 之后的画像） | 判据复用单元 D 的 `derive_unspecified_fields` |
| 5.3 空转轮全部保持已问未答 | Task 1 `test_idle_round_never_flips_anything_to_answered` | 结构上不可能违反：没有新字段就没有 entry 会翻 |
| 5.4 置 `is_reask` + 渲染层显式提示 + 界面可区分 | Task 2（后端）+ Task 4（徽标 + 左边框，且不与内联前缀并存） | |
| 5.5 上限取 2，超限停止追问并计入未指定、不再消耗追问轮次 | Task 2（`MAX_REASKS` / `_apply_question_ledger`） | "计入未指定"由 D 自动成立；轮次口径见「关键设计决定 3」 |
| 5.6 `2494103e` 回放 | Task 5 两条 `test_replay_2494103e_*` | 出处与局限逐字写在用例 docstring 里 |
| 5.7 撞 id 的递进提问不被过早掐断 | Task 2 `test_progressive_questions_on_an_answered_field_are_not_cut_off_early` | 上限只对未答的子问题计数，比"留余量"更安全 |
| 5.8 `_repeats_earlier_assistant_turn` 去留结论 | Task 5 Step 3（结论：**保留**，职责收窄为兜底）+ `test_verbatim_repeat_detection_still_guards_jobs_with_an_empty_ledger` | 三条理由与新的职责边界逐字写进 docstring，单元 A 挂在该函数上的待办就此销号 |

## Self-Review：三处请 reviewer 特别看的取舍

1. **台账不落新列，是推导出来的。** 好处是没有第二个真源、重放天然一致；代价是每一轮都要把该 job 的全部 `asked_questions` 行读回来解析（`_run_turn` 里那一次查询本来就在读，没有新增 I/O，但解析量随轮数线性增长）。`MAX_TOTAL_ROUNDS=8` 给它封了顶，8 行 JSON 的解析成本可以忽略。若将来放开总轮数上限，这里要重新评估。
2. **重问上限只对未答的子问题计数**（`not is_answered and ask_count >= 3`）。这比 design.md Risks 第 3 条要求的更宽松——已答字段上的递进提问永不被摘。风险是模型在一个已答字段上无限打转；兜底是 `MAX_TOTAL_ROUNDS` 与 `is_productive`（重复的 question_id 不算新问题，那些轮次不吃有产出轮预算）。若真实数据里出现这种打转，收紧成"已答字段也计数、上限另设"即可，改动面在 `_apply_question_ledger` 一个函数里。
3. **摘除超限重问会让会话更早进入确认。** 这是 5.5 想要的（停止追问 + 交给 D 的缺口警示），但它改变了业务经理的体感节奏：以前是被反复问同一件事，以后是更快看到带缺口警示的确认页。`test_dropping_an_exhausted_reask_does_not_make_the_turn_productive` 把这个行为钉住了。8.1 回放时要对比的正是这一点——总轮数下降、最终未指定字段数上升，两个数一起看才说明修对了；只看其中一个都会得出错误结论。
