# M1 采集质量修复 · 交付单元 F（字段溯源与编造率度量）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让"AI 有没有编造"从业务经理的主观印象变成系统算得出的数字——模型写进岗位画像的每个业务字段必须带一段**逐字取自用户某一轮原话**的引用，系统用一个不调模型的纯函数做归一化子串判定，指不出出处的字段被标记、计数、按**响应返回的**模型标识落库；**只度量，不拦截**。

**Architecture:** 新增一个零依赖纯函数模块 `app/agents/field_grounding.py`，承担三件事：把模型返回的 `{value, source_quote, source_turn}` **拍平成裸值**、按统一归一化做子串校验、给出该轮的未溯源字段名单。拍平点只有这一处，且位于 `run_intake_turn` 内部——`IntakeTurnResult.profile_patch` 出口起就已经是裸值，结构升级**永远不会到达** `profile_patch_accumulated` 与 `job_profile.profile_json`。未溯源名单、本轮写入字段名单、本轮响应模型标识由 `effect_persist_draft` 写进**同一条 INSERT**（不新增 effect 节点、`business_key` 不变）。

**Tech Stack:** Python 3.14.6（`./venv`）· pydantic 2.13.4 · FastAPI 0.115.6 · SQLite（`app/storage/db.py` 的条件 `ALTER TABLE` 加列路径）· pytest 8.3.4 · 标准库 `unicodedata`（NFKC），**不引入任何新依赖**

---

## Global Constraints

以下条目从 `CLAUDE.md`（2026-08-25 版）、本变更包 `delivery-units.md` §5、以及 OP-0820-12 指令 §3 第 1 条**逐字复制**。**每个 Task 的验收隐含包含本节全部内容**，`subagent-driven-development` 会把这一段原样交给 reviewer 当注意力透镜。

### 本单元的头号约束（OP-0820-12 §3 第 1 条，逐字）

> **7.1 把 profile_patch 的字段从裸值升级为 {value, source_quote, source_turn}，这个结构升级不得穿透到落库层。** 合进 profile_patch_accumulated 与 job_profile.profile_json 的**必须仍然是裸值**，结构升级只允许存在于"模型返回 → 校验"这一段，落库前拍平。不拍平会同时炸三处：
>
>   a. 单元 D 的 derive_unspecified_fields 会把 `{"value": null, ...}` 当成"这个字段有值"，漏报回到今天的故障
>   b. POST /confirm 里的 JobProfile.model_validate 直接 422（headcount 收到一个 dict）
>   c. effect_generate_and_persist_jd / jd_agent 全部按裸值读 profile_dict

**reviewer 的机械判据（三条，缺一不可）**：

1. `run_intake_turn()` 返回的 `IntakeTurnResult.profile_patch` 里，**不存在任何值是"带 `value` 键的 dict"**。这条由 Task 3 的 `test_turn_result_patch_is_flat` 锁死。
2. `app/graph/nodes.py` 与 `app/web/server.py` 的 diff 里**不出现** `source_quote` / `source_turn` / `value` 三个字符串。编排层与 HTTP 层根本不应该知道这个结构存在。
3. 落库后的 `job_profile.profile_json` 反序列化出来，逐个字段值都能通过 `JobProfile.model_validate` 那一关的类型（Task 4 的 `test_profile_json_stays_flat_end_to_end` 直接对着数据库断言）。

### 工程铁律（不可违背）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
   *为什么*：业务写失败而幂等记录成功 → 系统判定"已执行"→ 永不重试。**幂等本是防重复的保护，拆开事务后变成永久丢失的保证。** 实证：`.51` 现网 2026-08-10 与 08-12 各丢一轮 `outbox`（幂等记录已落），用户没收到回复且永远不会补发，见 `docs/findings/2026-08-13-sqlite-事务归属冲突.md` §8.5。

> **本单元与这条的关系**：F 写三列新数据，但**不新增任何 effect 节点、不新增任何写入语句**——三列全部追加进 `effect_persist_draft` 已有的那条 `INSERT INTO job_profile`。理由与 tasks 1.5 完全一致：多一次写入就多一个能失败的地方，而"这一轮的画像"与"这一轮的溯源结果"必须同生共死（spec 的 `Scenario: 来源与画像同生共死` 是正面契约）。`business_key` 仍然是 `round_count`，一个字节都不改。**reviewer 判据：本单元 diff 里不出现新的 `@idempotent_effect`，也不出现第二条 `INSERT INTO job_profile`。**

2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。

> **本单元与这条的关系**：`app/agents/field_grounding.py` 是本单元的核心，必须是**彻底的纯函数模块**——不 import `sqlite3`、不 import `LLMGateway`、不 import `logging`、不读文件、不取时钟。spec 的 `Requirement: 来源校验是确定性的` 明写"校验 SHALL 可以在不调用任何模型的情况下被单元测试直接断言"，纯函数是这条的落地形态。**reviewer 判据：`field_grounding.py` 的 import 段只允许 `from __future__`、`unicodedata`、`dataclasses`、`typing`。**

5. **`temperature=0`；模型版本优先显式锁定**，禁止 `latest` 类别名。
   供应商不提供带版本号快照时（如 DeepSeek 公开 API 只有 `deepseek-chat` 这类会漂移的别名），**必须从 API 响应里取回实际的 `model` 字段并持久化**——配置里写的名字不算数，响应返回的才算。
   *为什么*：铁律的目的是评分可复现、可审计。供应商静默升级模型会让历史评分失去解释力，而 PIPL 的说明权要求你能回答"这条评分是哪个版本打的"。锁不住版本时，至少要记得住版本。

> **本单元与这条的关系有两处，都是硬要求**：
> ① **`SYSTEM_PROMPT` 改了就必须升 `prompt_version`**。B 已占用 `intake-v4`，**F 是 `intake-v5`，不要重号**（`delivery-units.md` §5 约定 3）。
> ② **本单元正是这条铁律"锁不住版本时至少记得住版本"的兑现点**。`llm_response_model` 这一列从 2026-08-19 建好起一直是 NULL，F 是第一个写值的单元。写进去的必须是 `LLMCallMeta.response_model`（`getattr(response, "model", None)`），**不是** `LLMGateway._model` 那个配置别名，两者分开记录、不互相覆盖。

### 合规红线

- **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。

> **本单元的对应形态是「只观测不拦截」**（`design.md` 决策 12）：未溯源字段**照常写入画像**，不丢弃、不清空、不阻断采集流程。这不是"顺便宽松一点"，是本章的定义性约束——`spec.md` 的 `Requirement: 本能力只度量不拦截` 用了 `MUST NOT`。
> **reviewer 判据：本单元的 diff 里不得出现任何以未溯源为条件的分支**——没有 `if ungrounded: return`、没有 `raise`、没有把未溯源字段从 patch 里 `pop` 掉、没有降级到追问、没有 409。未溯源率达到多少才值得拦截，等 ≥20 场真实会话的分布出来再单独开变更（Task 6 把这条登记成技术债）。
> **业务可见性必须为零**：三位业务经理在界面上看不到任何变化。**diff 里不得出现 `app/web/static/index.html`。**

- **模型全部走境内**，简历数据不出境。

> 本单元不新增任何模型调用点，沿用既有的境内网关，无额外动作。

### 明确不适用（reviewer 不必在本单元追这几条）

- 铁律 3（AI 评分持久化）、铁律 4（`evidence_ref` 非空）：本单元不写 `criterion_score`，代码库中亦无该表。*注意*：`source_quote` 在概念上是 `evidence_ref` 的同族物（都要求"指得出原文出处"），但它落在 `job_profile.ungrounded_fields` 而非 `criterion_score.evidence_ref`，不适用铁律 4 的"为空不允许写入"——按 spec，缺来源是**记为未溯源并放行**，不是拒写。
- 铁律 6（企微回调先落库）、铁律 7（`langgraph >= 1.0.10`）：本单元不接企微通道、不动依赖版本。
- 合规红线「AI 生成内容须带标识」：本单元不生成任何对外文案（JD/拒信/邀约），也不改前端。
- 合规红线「禁止人脸/表情分析」「绝不用历史录用结果做监督信号」：本单元不涉及。
- 部署约束 1（相对路径）：本单元不碰前端、不新增 fetch 调用点。部署约束 2/3/4/5：不改端口、不动鉴权中间件、不引入容器、不处理真实简历。

### 跨单元接口约定（`delivery-units.md` §5，第 1、3、5 条逐字）

1. **F 的 `profile_patch` 结构升级不得穿透到 `profile_json`** —— 落库前拍平成裸值，理由见 §2.F

3. **B 与 F 都会改 `SYSTEM_PROMPT` → 各自升 `prompt_version`**（现为 `intake-v3`；B → `v4`，F → `v5`）。铁律 5

5. **每个单元开工前必须 rebase 到最新 main** —— `app/agents/intake_agent.py` 与 `app/graph/nodes.py` 被 B/D/E/F 四个单元连续改动，是本批最热的两个文件

> **F 是这条的最后一个受害者，也是受害最深的一个**：B、D、E 三个单元全部改过 `intake_agent.py` 与 `graph/nodes.py`，F 在它们**全部合并之后**才开工。本计划的「开工前置」一节把 rebase 后必须核对的四个锚点写成了可执行的 grep 命令——**不核对就开工，冲突会以"测试莫名其妙全红"的形式在 Task 3 中途爆出来**。

---

## 交付单元边界

**本单元 = `openspec/changes/m1-intake-quality-fixes/tasks.md` 第 7 章（7.1–7.11），共 11 项。**

对应 `specs/intake-field-grounding/spec.md` 的**全部四条** Requirement。设计依据：`design.md` 决策 11（逐字引用 + substring 校验）、决策 12（只观测不拦截）。

**执行位次：B∥C → D → E → **F** → G。** F 的开工前提是 **B、D、E 都已合并到 main**（`delivery-units.md` §6，2026-08-19 Shao Peishen 拍板）。本计划提前产出**不改变这个顺序**。

### 触碰面（硬边界）

| 文件 | 性质 | 谁还会碰它 |
|---|---|---|
| `app/agents/field_grounding.py` | **新建**，本单元独占 | 无。G 只读不改 |
| `app/agents/intake_agent.py` | 生产代码，本单元是**最后一个**改它的单元 | B/D/E 全部在 F 之前合并完毕 |
| `app/graph/state.py` | 生产代码，加两个 TypedDict 键 | 同上 |
| `app/graph/nodes.py` | 生产代码，`compute_intake_turn` 透传 + `effect_persist_draft` 追加三列 | 同上 |
| `app/storage/db.py` | 生产代码，**加一列** `written_fields`（见下方「两处偏差」） | 同上 |
| `tests/test_field_grounding.py` | **新建**，本单元独占 | 无 |
| `tests/test_intake_agent.py` · `tests/test_graph_nodes.py` · `tests/test_db_migration.py` | 测试，本单元修改 | 同上 |
| `docs/m1-fabrication-rate.md` | **新建**，统计口径真源 | G 的 8.7 往里填第一个真实数字 |
| `docs/tech-debt.md` | 追加一条 | B/E 可能已各追加过，按现有最大编号顺延 |

**这些文件之外一律不得出现在本单元的 diff 里。** 特别是：

- ⛔ **`app/web/static/index.html`** —— 本章对业务经理零可见（决策 12），碰前端即违反本单元的定义
- ⛔ **`app/web/server.py`** —— 没有任何 HTTP 契约变更。`GET /api/jobs/{id}` 的响应体不加未溯源字段，`POST /confirm` 不加校验分支
- ⛔ **`app/agents/jd_agent.py`** —— JD 生成读的是裸值 `profile_dict`，正是本单元必须保证不受影响的下游

### 与 `delivery-units.md` §2.F 的两处偏差（**都是刻意的，reviewer 请按这里的理由审**）

#### 偏差一：新增第三列 `job_profile.written_fields`（`delivery-units.md` 只提了 1.1 的两列）

**为什么必须加**：spec 的 `Requirement: 本能力只度量不拦截` 要求「使**未溯源字段数 / 写入字段总数**可以被事后统计出来」，tasks 7.10 要求这个比例是"可复算定义"。分子（`ungrounded_fields`）有列可落，**分母没有**——`profile_json` 存的是**累积**画像，不是本轮 patch。想从累积值反推本轮写了几个字段，只能拿 v 与 v-1 的键集合作差，而**同一字段被修正重写时键数不变**，差集为空 → 分母恒偏小 → 编造率恒偏大。一个算错的编造率比没有编造率更坏：它会被拿去和 `deepseek-v4-pro` 的 1/3 做对比，而对比的结论会指导要不要换模型。

**为什么这样加是安全的**：完全走 `delivery-units.md` §5 约定 4 与 `design.md` 决策 10 已经建立的路径——同时改 `SCHEMA` 的 `CREATE TABLE` 与 `_ADDED_COLUMNS`，`TEXT NOT NULL DEFAULT '[]'` 是常量默认值，`.51` 上 15 个历史 job 的老行不回填、按默认值 `[]` 成立（分母为 0 的行由 `NULLIF` 在统计口径里排除）。`tests/test_db_migration.py` 的漂移守卫测试自动覆盖新库/老库两条路径的一致性。

**被否决的替代**：把 `ungrounded_fields` 这一列从 JSON 数组改成 `{"ungrounded": [...], "written": [...]}` 对象。否决理由：该列的默认值 `'[]'` 与 `tests/test_db_migration.py` 的既有断言都按数组写死，改形状要同时动老行语义（老行的 `[]` 该读成什么？），而这一切只为省一列。

#### 偏差二：7.4 的「点选来源例外」**不单独实现**（核实结论，不是省略）

tasks 7.4 原文含两半：**（a）系统管理字段不参与校验**、**（b）由用户点选候选档位产生的字段以被选中的档位标识作为来源，不要求在自由文本里找片段**。

- **(a) 照常实现**（Task 1 的 `exempt_fields` 参数 + Task 3 传入 `_SYSTEM_MANAGED_FIELDS`）。
- **(b) 经核实不必单独实现**，理由三条，逐条可验证：
  1. **单元 C 已经落地成"点选文本原样拼进回复"**。`app/web/static/index.html` 的 `collectSelections()` 把每个被勾选的档位拼成 `问题原文：档位A、档位B` 一行，与自由文本合并成**一条 `message`** 提交给既有的 `POST /api/jobs/{id}/reply`。被选中的档位文本**逐字出现在该轮用户原话里**，7.3 的归一化子串判定天然命中——不需要任何例外分支。（该文件里的注释已经把这个预期写明，本单元的核实与它一致。）
  2. **后端根本拿不到"哪些是点选的"这个信号**。`delivery-units.md` §5 约定 2 明令「C 的点选提交不改 API 契约」，`ReplyRequest` 里没有 `selected_options`，单元 C 还立了 `test_reply_api_contract_has_no_selected_options` 把它机械锁死。要实现 (b) 的例外，必须先给 API 加回那个字段——**那是推翻一条已生效的跨单元约定**，不是实现细节。
  3. **不实现的代价为零**：(b) 想防的是"点选的字段被误判为未溯源"，而 1 已经保证它命中。
- **但结论必须被测试钉住，不能只写在文档里**：Task 5 用一段**逐字复制自单元 C 拼接格式**的用户原话构造回放用例，断言点选产生的字段判为已溯源。**这个测试将来若失败，说明前端的拼接格式变了、(b) 重新变成真问题——那是一次设计对话，不是一个可以删掉的测试。**

**必须一并如实记下的已知代价**（单元 C 的注释已提出，本单元的处置是"接受并写进统计口径文档"）：`collectSelections()` 会把**问题原文**也拼进用户消息，因此问题文本自身逐字包含某个档位值时（如「ASIL 等级要求（ASIL-B / ASIL-D）？」），该值即使未被勾选也出现在用户原话里，模型引用它可以过校验。
**本单元不收窄搜索范围**，理由：这类作弊让编造被判成"已溯源"，即让未溯源率**偏低**，因此 `design.md` 决策 11 声明的不变式——「真实编造率 ≥ 未溯源率，本批要的是一个**下界**」——依然成立。收窄搜索范围（从用户原话里剔掉问题原文）需要后端持有上一轮的 `pending_questions` 并做字符串剥离，是一条又脆又只影响下界紧度的路。**这条代价写进 Task 6 的统计口径文档，让读数字的人知道它是下界而不是精确值。**

---

## Requirement → Task 覆盖矩阵

| `specs/intake-field-grounding/spec.md` 的 Requirement / Scenario | tasks.md | Task |
|---|---|---|
| **画像字段必须携带可校验的来源** | 7.1 / 7.2 | Task 1（结构与拍平）· Task 2（提示词要求来源）· Task 3（接线） |
| ├ Scenario: 常规字段带来源 | 7.1 / 7.2 | Task 2（正例写进 SYSTEM_PROMPT）· Task 3 |
| ├ Scenario: 点选产生的字段 | 7.4 | **Task 5**（核实 + 回放测试，见「偏差二」） |
| └ Scenario: 来源与画像同生共死 | 7.5 | Task 4（同一条 INSERT，不新增 effect 节点） |
| **来源校验是确定性的** | 7.3 | Task 1 |
| ├ Scenario: 引用能对上 | 7.3 | Task 1 |
| ├ Scenario: 引用对不上 | 7.3 / 7.6 | Task 1 · Task 3 |
| ├ Scenario: 缺少来源 | 7.1 / 7.3 | Task 1 |
| └ Scenario: 归一化后仍算命中 | 7.3 | Task 1 |
| **本能力只度量不拦截** | 7.5 / 7.8 | Task 3（不拦截）· Task 4（落库） |
| ├ Scenario: 未溯源不影响采集 | 7.5 | Task 3 · Task 4 |
| ├ Scenario: 可算出编造率 | 7.10 | Task 4（`written_fields` 分母）· Task 6（口径文档） |
| └ Scenario: 来源结构缺失时降级而非报错 | 7.8 | Task 1 · Task 3 |
| **编造信号可按模型版本归因** | 7.9 | Task 4 |
| ├ Scenario: 记录响应返回的模型标识 | 7.9 | Task 4 |
| └ Scenario: 按模型分组统计 | 7.10 | Task 6 |
| 归纳负例（值规范化、引用逐字） | 7.7 | Task 1 · Task 3 |
| 技术债登记（拦截策略待定） | 7.11 | Task 6 |

**11 项 openspec task 全部有归属**：7.1→T1/T3、7.2→T2、7.3→T1、7.4→T1(a)/T5(b)、7.5→T3/T4、7.6→T3、7.7→T1/T3、7.8→T1/T3、7.9→T4、7.10→T6、7.11→T6。

---

## File Structure

```
app/agents/field_grounding.py      【新建 · 约 110 行】
    纯函数模块，零 I/O。四个职责：
      - is_user_turn / user_turns：用户轮次的唯一口径（prompt 编号与校验共用同一份）
      - normalize_for_grounding：NFKC + 去空白，比对前的唯一归一化入口
      - split_patch_sources：**全流程唯一的拍平点**，{value,...} → 裸值 + 来源表
      - verify_field_grounding：确定性子串判定，返回未溯源字段名

app/agents/intake_agent.py         【修改】
    - SYSTEM_PROMPT 增加【字段来源】段（正例 + 两条反例）
    - prompt_version: "intake-v4" → "intake-v5"
    - _build_user_prompt 的 transcript 给用户轮次编号（user#N）
    - IntakeTurnResult 增加 ungrounded_fields / written_fields
    - run_intake_turn 里拍平 + 校验，出口 profile_patch 恒为裸值

app/graph/state.py                 【修改】加 ungrounded_fields / written_fields /
                                    llm_response_model 三个键
app/graph/nodes.py                 【修改】compute_intake_turn 透传三个值；
                                    effect_persist_draft 把三列追加进**已有的**那条 INSERT
app/storage/db.py                  【修改】SCHEMA + _ADDED_COLUMNS 同步加 written_fields

tests/test_field_grounding.py      【新建 · 21 个用例】纯函数全覆盖
tests/test_intake_agent.py         【修改】7.6/7.7/7.8 + 拍平不穿透 + v5 + 轮次编号
tests/test_graph_nodes.py          【修改】三列落库、端到端裸值、改写 08-19 的那条旧断言
tests/test_db_migration.py         【修改】新列进两条路径的断言

docs/m1-fabrication-rate.md        【新建】编造率的可复算定义 + 分组 SQL + 已知偏差
docs/tech-debt.md                  【修改】追加「拦截策略待定」，触发条件写死
```

---

## 开工前置

**F 的前提是 B、D、E 三个单元都已合并。** 开工第一件事是 rebase 到最新 main，然后跑下面这组命令核对四个锚点。**任何一条与预期不符就停下来问，不要"猜一个差不多的写法"继续**——这四处正是 B/D/E 改过的地方，猜错的表现是 Task 3 中途测试大面积飘红，而那时已经不容易分清是本单元写错了还是 rebase 没接好。

```bash
git fetch origin && git rebase origin/main
./venv/bin/python -m pytest -q            # 预期：全绿。不绿就先修 rebase，别开工

# 锚点 1：prompt_version 现在必须是 intake-v4（B 留下的）。若是 v3 → B 没合，停
grep -n 'prompt_version="intake-' app/agents/intake_agent.py

# 锚点 2：_SYSTEM_MANAGED_FIELDS 还在不在、叫什么、有没有被 D 搬走
grep -rn '_SYSTEM_MANAGED_FIELDS' app/

# 锚点 3：effect_persist_draft 那条 INSERT 现在有哪些列（B 的 is_productive、
#         D 的 derived_unspecified_fields 应该都在里面了）
sed -n '/INSERT INTO job_profile/,/)$/p' app/graph/nodes.py

# 锚点 4：单元 C 的点选拼接格式（Task 5 的测试要逐字复刻它）
grep -n 'dataset.qtext' app/web/static/index.html
```

**锚点 3 的处理规则（重要）**：本计划 Task 4 给出的 INSERT 代码是**按"D 已合并"的形态**写的。rebase 后实际的列清单以仓库为准——**只允许在末尾追加本单元的三列与三个占位符，不得删改 B/D 已经加进去的任何一列**。若发现实际列清单与本计划的示例不一致，以仓库为准、按同样的方式追加。

---

### Task 1: 溯源校验纯函数模块（tasks 7.3 / 7.1 的结构定义 / 7.4 的系统字段例外）

**Files:**
- Create: `app/agents/field_grounding.py`
- Test: `tests/test_field_grounding.py`（新建）

**Interfaces:**
- Consumes: 无。**这个模块零依赖**——不 import 本项目任何其他模块，只用标准库 `unicodedata` / `dataclasses` / `typing`。这是铁律 2「L3 Agent 全部是无副作用纯函数」在本单元的落地形态，也是 spec「校验 SHALL 可以在不调用任何模型的情况下被单元测试直接断言」的直接兑现。
- Produces:
  - `is_user_turn(turn: dict) -> bool`
  - `user_turns(history: list[dict]) -> list[str]`
  - `normalize_for_grounding(text: Any) -> str`
  - `FieldSource`（frozen dataclass，字段 `quote: str | None` / `turn: int | None`）
  - `split_patch_sources(raw_patch: Any) -> tuple[dict, dict[str, FieldSource]]`
  - `verify_field_grounding(patch: Any, history: list[dict], *, exempt_fields: frozenset[str] | set[str] = frozenset()) -> list[str]`

> **关于 `verify_field_grounding` 的签名**：tasks 7.3 写的是 `verify_field_grounding(patch, history) -> list[str]`，本计划**完全保留这个位置参数形态**，只加了一个**带默认值的关键字参数** `exempt_fields`。为什么不让模块自己 import `_SYSTEM_MANAGED_FIELDS`：那会让 `field_grounding` 依赖 `intake_agent`，而 `intake_agent` 又要依赖 `field_grounding` —— **循环 import**。把豁免集合作为入参传进来，模块保持零依赖、可被单独测试，豁免口径的真源仍然只有 `intake_agent._SYSTEM_MANAGED_FIELDS` 一处（Task 3 负责传）。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_field_grounding.py`：

```python
from app.agents.field_grounding import (
    FieldSource,
    is_user_turn,
    normalize_for_grounding,
    split_patch_sources,
    user_turns,
    verify_field_grounding,
)

# 这段历史是本文件的公共夹具：user#1 与 user#2 是两轮用户原话，
# 中间那条 assistant 不参与编号——source_turn=2 指的是"第 2 轮用户原话"。
HISTORY = [
    {"role": "user", "content": "要招一个嵌入式工程师"},
    {"role": "assistant", "content": "需要熟悉 AUTOSAR 吗？"},
    {"role": "user", "content": "需要熟悉 AUTOSAR CP，量产项目至少两个"},
]


def test_user_turns_skips_assistant():
    assert user_turns(HISTORY) == ["要招一个嵌入式工程师", "需要熟悉 AUTOSAR CP，量产项目至少两个"]


def test_user_turns_defaults_missing_role_to_user():
    """
    与 _build_user_prompt 的 turn.get("role", "user") 口径必须一模一样。
    两处对"没有 role 的那条算不算用户轮"如果答案不同，编号就会错位一格，
    而错位的表现是"引用明明对得上却被判未溯源"——一个只在脏数据上出现、
    从错误信息里完全看不出成因的故障。
    """
    assert user_turns([{"content": "没有 role"}]) == ["没有 role"]
    assert is_user_turn({"content": "x"}) is True
    assert is_user_turn({"role": "assistant", "content": "x"}) is False


def test_normalize_folds_width_and_whitespace():
    assert normalize_for_grounding("ＡＳＩＬ－Ｄ") == normalize_for_grounding("ASIL-D")
    assert normalize_for_grounding("量产 项目\n至少两个") == "量产项目至少两个"


def test_split_returns_bare_values():
    flat, sources = split_patch_sources(
        {"headcount": {"value": 2, "source_quote": "两个", "source_turn": 2}}
    )
    assert flat == {"headcount": 2}
    assert sources["headcount"] == FieldSource(quote="两个", turn=2)


def test_split_tolerates_bare_patch():
    """模型没按新提示词输出（还是老的裸值形态）时不能崩：值原样保留、记为无来源。"""
    flat, sources = split_patch_sources({"headcount": 2})
    assert flat == {"headcount": 2}
    assert sources["headcount"] == FieldSource(quote=None, turn=None)


def test_split_keeps_dict_without_value_key_as_bare():
    """没有 value 键的 dict 不是来源信封，原样当值——判据只认 value 键。"""
    flat, _ = split_patch_sources({"weird": {"a": 1}})
    assert flat == {"weird": {"a": 1}}


def test_split_on_non_dict():
    assert split_patch_sources("garbage") == ({}, {})


def test_grounded_quote_hits():
    patch = {
        "autosar_experience": {
            "value": ["CP"],
            "source_quote": "熟悉 AUTOSAR CP",
            "source_turn": 2,
        }
    }
    assert verify_field_grounding(patch, HISTORY) == []


def test_normalized_hit_counts():
    """spec「归一化后仍算命中」：差异只有空白与全半角时必须算命中。"""
    patch = {
        "autosar_experience": {
            "value": ["CP"],
            "source_quote": "熟悉ＡＵＴＯＳＡＲ  CP",
            "source_turn": 2,
        }
    }
    assert verify_field_grounding(patch, HISTORY) == []


def test_fabricated_quote_is_ungrounded():
    """spec「引用对不上」：模型凭空生成引用。"""
    patch = {
        "mcu_family": {
            "value": ["ARM Cortex-M"],
            "source_quote": "用的是 ARM Cortex-M",
            "source_turn": 2,
        }
    }
    assert verify_field_grounding(patch, HISTORY) == ["mcu_family"]


def test_wrong_turn_is_ungrounded():
    """spec「引用对不上」的另一半：引用是真的，但指错了轮次。逐轮判定，不做全局搜索。"""
    patch = {
        "autosar_experience": {
            "value": ["CP"],
            "source_quote": "熟悉 AUTOSAR CP",
            "source_turn": 1,
        }
    }
    assert verify_field_grounding(patch, HISTORY) == ["autosar_experience"]


def test_out_of_range_turn_is_ungrounded():
    patch = {"headcount": {"value": 2, "source_quote": "两个", "source_turn": 99}}
    assert verify_field_grounding(patch, HISTORY) == ["headcount"]


def test_zero_turn_is_ungrounded():
    """轮次是 1-based。0 与负数一律越界，不许被当成 Python 的反向索引。"""
    patch = {"headcount": {"value": 2, "source_quote": "两个", "source_turn": 0}}
    assert verify_field_grounding(patch, HISTORY) == ["headcount"]


def test_missing_source_is_ungrounded():
    """spec「缺少来源」：不给引用 → 未溯源，而不是被当作已溯源放行。"""
    assert verify_field_grounding({"headcount": 2}, HISTORY) == ["headcount"]


def test_empty_quote_is_ungrounded():
    """空串/纯空白引用在任何原话里都是子串，必须在判定前就拦掉，否则它等于万能通行证。"""
    patch = {"headcount": {"value": 2, "source_quote": "   ", "source_turn": 2}}
    assert verify_field_grounding(patch, HISTORY) == ["headcount"]


def test_garbage_source_structure_degrades():
    """spec「来源结构缺失时降级而非报错」：结构完全不合法时不抛异常，值照留，全计未溯源。"""
    patch = {
        "job_title": {
            "value": "嵌入式工程师",
            "source_quote": {"nested": "dict"},
            "source_turn": [1, 2],
        },
        "headcount": {"value": 2},
    }
    flat, _ = split_patch_sources(patch)
    assert flat == {"job_title": "嵌入式工程师", "headcount": 2}
    assert sorted(verify_field_grounding(patch, HISTORY)) == ["headcount", "job_title"]


def test_string_turn_is_coerced():
    """模型把轮次写成字符串 "2" 是常见退化，能救就救，不因此判未溯源。"""
    patch = {"headcount": {"value": 2, "source_quote": "至少两个", "source_turn": "2"}}
    assert verify_field_grounding(patch, HISTORY) == []


def test_bool_turn_is_not_an_index():
    """Python 里 True == 1。不显式挡掉的话，source_turn=true 会静默变成"第 1 轮"。"""
    patch = {"headcount": {"value": 2, "source_quote": "至少两个", "source_turn": True}}
    assert verify_field_grounding(patch, HISTORY) == ["headcount"]


def test_exempt_fields_skip_verification():
    """tasks 7.4(a)：系统管理字段不参与校验。"""
    patch = {"unspecified_fields": {"value": ["mcu_family"]}}
    assert verify_field_grounding(patch, HISTORY, exempt_fields={"unspecified_fields"}) == []


def test_normalized_value_with_verbatim_quote_is_grounded():
    """
    tasks 7.7 归纳负例：用户说"MISRA C"，字段值写成规范化枚举值，但引用逐字命中
    → 必须判为已溯源。**校验的是引用的真实性，不是值与引用的等价性**（决策 11）。
    """
    history = [{"role": "user", "content": "要求熟悉 MISRA C 规范"}]
    patch = {
        "toolchain": {
            "value": ["MISRA-C:2012"],
            "source_quote": "熟悉 MISRA C 规范",
            "source_turn": 1,
        }
    }
    assert verify_field_grounding(patch, history) == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_field_grounding.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.agents.field_grounding'`（collection error，20 个用例一个都跑不起来）

- [ ] **Step 3: 写实现**

创建 `app/agents/field_grounding.py`：

```python
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any

# 模型返回的来源信封的三个键。判定一个值"是不是信封"只认 _VALUE_KEY 的存在，
# 不认另外两个——模型经常只给 value 而漏掉引用，那属于"缺少来源"（照样是信封、
# 记为未溯源），不属于"这个字段的值本来就是个 dict"。
_VALUE_KEY = "value"
_QUOTE_KEY = "source_quote"
_TURN_KEY = "source_turn"


def is_user_turn(turn: dict) -> bool:
    """
    一条历史记录算不算"业务经理的一轮原话"。

    默认值 "user" 与 intake_agent._build_user_prompt 的 turn.get("role", "user")
    **必须保持一致**：prompt 里给用户轮次编号用的是那个口径，校验按编号取原话
    用的是这个口径，两边差一条记录，编号就整体错位一格，表现为"引用明明对得上
    却被判未溯源"。这个故障只在脏数据（缺 role 的历史行）上出现，且错误信息里
    看不出任何线索——所以两处共用这一个谓词，不各写各的。
    """
    return turn.get("role", "user") == "user"


def user_turns(history: list[dict]) -> list[str]:
    """按出场顺序取出用户原话。source_turn 是这个列表的 **1-based** 下标。"""
    return [str(turn.get("content", "")) for turn in history if is_user_turn(turn)]


def normalize_for_grounding(text: Any) -> str:
    """
    比对前的唯一归一化入口（spec「归一化后仍算命中」）。

    NFKC 统一全半角（ＡＳＩＬ→ASIL、（）→()、－→-），随后去掉**全部**空白字符。
    去空白而不是折叠成单空格：中文里空格的有无本来就随手而变，"AUTOSAR CP" 与
    "AUTOSARCP" 在语义上没有区别，折叠成单空格反而会因为一个多余空格判失败。

    代价（写明，不粉饰）：去空白会让英文的词边界消失，"C  A" 能匹配上 "CA"。
    这个方向的误判会把编造判成"已溯源"，即让未溯源率**偏低**——与决策 11 声明的
    「本批要的是一个下界」同向，可接受。
    """
    return "".join(unicodedata.normalize("NFKC", str(text)).split())


@dataclass(frozen=True)
class FieldSource:
    """一个字段声明的来源。两个字段都可空——缺失即未溯源，不是校验失败。"""

    quote: str | None
    turn: int | None


def _coerce_turn(raw: Any) -> int | None:
    """轮次容错：接受 int 与能转成 int 的字符串，其余一律 None（记为未溯源）。"""
    # bool 必须排在 int 之前：Python 里 isinstance(True, int) 为真，
    # 不挡掉的话 source_turn=true 会静默变成"第 1 轮"。
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw.strip())
        except ValueError:
            return None
    return None


def _coerce_quote(raw: Any) -> str | None:
    """引用容错：只接受非空白字符串。空串是任何字符串的子串，等于万能通行证。"""
    if isinstance(raw, str) and raw.strip():
        return raw
    return None


def split_patch_sources(raw_patch: Any) -> tuple[dict, dict[str, FieldSource]]:
    """
    **全流程唯一的拍平点。** 把模型返回的
    {字段: {value, source_quote, source_turn}} 拆成（裸值 patch，来源表）。

    这个函数的返回值第一项是本单元最重要的不变式的载体：它之后的一切——
    profile_patch_accumulated、job_profile.profile_json、JobProfile.model_validate、
    jd_agent 读的 profile_dict——**全部只见得到裸值**。结构升级到此为止。
    不拍平会同时炸三处，见本计划 Global Constraints 第一段。

    容错是刻意的（spec「来源结构缺失时降级而非报错」）：模型没按新提示词输出、
    还给裸值时，值原样保留、来源记空 → 该字段计未溯源，采集照常完成。
    这条路径在提示词刚升到 v5 的头几天一定会被走到。
    """
    if not isinstance(raw_patch, dict):
        return {}, {}

    flat: dict = {}
    sources: dict[str, FieldSource] = {}
    for name, raw in raw_patch.items():
        key = str(name)
        if isinstance(raw, dict) and _VALUE_KEY in raw:
            flat[key] = raw[_VALUE_KEY]
            sources[key] = FieldSource(
                quote=_coerce_quote(raw.get(_QUOTE_KEY)),
                turn=_coerce_turn(raw.get(_TURN_KEY)),
            )
        else:
            flat[key] = raw
            sources[key] = FieldSource(quote=None, turn=None)
    return flat, sources


def verify_field_grounding(
    patch: Any,
    history: list[dict],
    *,
    exempt_fields: frozenset[str] | set[str] = frozenset(),
) -> list[str]:
    """
    返回本轮**未溯源**的字段名列表。确定性，不调模型（design.md 决策 11）。

    判据：引用片段（归一化后）必须能在它**自己声明的那一轮**用户原话里原样找到。
    刻意不做"在全部轮次里搜一遍"的兜底——spec 的 Scenario「指错了轮次」明写
    这种情况判未溯源。放宽成全局搜索会让"模型随便填个轮次号"变成免费通行证。

    exempt_fields 用于系统管理字段（tasks 7.4）。之所以走入参而不是在这里
    import intake_agent._SYSTEM_MANAGED_FIELDS：那会形成循环 import，且会让
    这个模块从"零依赖纯函数"退化成"依赖 agent 的模块"。
    """
    _, sources = split_patch_sources(patch)
    turns = [normalize_for_grounding(text) for text in user_turns(history)]

    ungrounded: list[str] = []
    for name, source in sources.items():
        if name in exempt_fields:
            continue
        if source.quote is None or source.turn is None:
            ungrounded.append(name)
            continue
        if not 1 <= source.turn <= len(turns):
            ungrounded.append(name)
            continue
        needle = normalize_for_grounding(source.quote)
        if not needle or needle not in turns[source.turn - 1]:
            ungrounded.append(name)
    return ungrounded
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_field_grounding.py -q`
Expected: PASS，20 passed

- [ ] **Step 5: 全量回归**

Run: `./venv/bin/python -m pytest -q`
Expected: 全绿。本 Task 只新增文件、不改任何既有模块，既有用例数不应变化。

- [ ] **Step 6: 提交**

```bash
git add app/agents/field_grounding.py tests/test_field_grounding.py
git commit -m "feat(intake): 溯源校验纯函数模块（tasks 7.3/7.4a）"
```

---

### Task 2: `SYSTEM_PROMPT` 要求逐字来源 + 用户轮次编号 + `intake-v5`（tasks 7.2）

**Files:**
- Modify: `app/agents/intake_agent.py`（`SYSTEM_PROMPT`、`_build_user_prompt`、`prompt_version`）
- Test: `tests/test_intake_agent.py`

**Interfaces:**
- Consumes: Task 1 的 `is_user_turn`
- Produces: `_render_transcript(history: list[dict]) -> str`（模块私有）；`SYSTEM_PROMPT` 里出现 `source_quote` / `source_turn` / `user#`；`prompt_version="intake-v5"`

> **为什么轮次编号和提示词是同一个 Task**：`source_turn` 要求模型报出"引用在第几轮用户原话里"，而今天的 transcript 渲染成 `user: xxx`，**没有编号可报**——模型只能猜。提示词要求与编号必须同时上线，先上哪个另一个都无效。

> **rebase 提醒**：`SYSTEM_PROMPT` 在 B（3.5）与可能的 D（第 6 章）之后已经变过。本 Task 是**追加一段**，不是重写整个提示词。定位到现有 `【追问的字段形状】…不要输出 question_id…` 那段之后、最后的 `"输出 JSON，字段：…"` 之前插入。

- [ ] **Step 1: 写失败测试**

在 `tests/test_intake_agent.py` 末尾追加：

```python
from app.agents.intake_agent import _build_user_prompt  # 文件顶部已有的 import 里补上
from app.agents.field_grounding import user_turns


def test_prompt_version_is_v5():
    """
    铁律 5：SYSTEM_PROMPT 改了就必须升版本，否则 input_hash 与历史评分对不上。
    v4 是单元 B 占用的，F 是 v5，**不要重号**（delivery-units.md §5 约定 3）。
    """
    gateway = make_gateway([json.dumps({"is_job_related": True, "questions": [], "profile_patch": {}})])
    run_intake_turn(gateway, history=[{"role": "user", "content": "要招人"}], round_count=0)
    assert gateway._client.chat.completions.calls  # 确实调过模型
    # prompt_version 不进请求体，只进 AuditHook；这里直接对着源码常量断言。
    import app.agents.intake_agent as mod
    import inspect

    assert 'prompt_version="intake-v5"' in inspect.getsource(mod.run_intake_turn)


def test_system_prompt_demands_verbatim_source():
    """7.2：来源要求 + 正例 + 反例都必须在提示词里。"""
    assert "source_quote" in SYSTEM_PROMPT
    assert "source_turn" in SYSTEM_PROMPT
    assert "user#" in SYSTEM_PROMPT
    assert "逐字" in SYSTEM_PROMPT
    assert "正例" in SYSTEM_PROMPT
    assert "反例" in SYSTEM_PROMPT


def test_transcript_numbers_user_turns_consistently_with_verifier():
    """
    prompt 里的 user#N 编号必须与 field_grounding.user_turns 的下标严格对齐。
    这是本单元最容易静默错的地方：错位一格的表现是"引用对得上却判未溯源"，
    从错误信息里完全看不出成因。所以这里不测"格式好看"，测的是**两边同源**。
    """
    history = [
        {"role": "user", "content": "第一句"},
        {"role": "assistant", "content": "助手插一句"},
        {"role": "user", "content": "第二句"},
        {"content": "没有 role 的一句"},
    ]
    prompt = _build_user_prompt(history, {}, [])
    for index, text in enumerate(user_turns(history), start=1):
        assert f"user#{index}: {text}" in prompt
    assert "assistant: 助手插一句" in prompt
    # 助手轮次不占编号：三条用户原话，编号只到 3
    assert "user#4" not in prompt
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_intake_agent.py -q -k "v5 or verbatim_source or numbers_user_turns"`
Expected: FAIL —— 三条全红（`prompt_version="intake-v4"`、`source_quote` 不在提示词里、transcript 里没有 `user#1`）

- [ ] **Step 3: 写实现（三处改动）**

**改动 1** —— `app/agents/intake_agent.py` 顶部 import 段追加：

```python
from app.agents.field_grounding import (
    is_user_turn,
    split_patch_sources,
    verify_field_grounding,
)
```

（`split_patch_sources` / `verify_field_grounding` 在 Task 3 才用到，一次 import 到位，避免 Task 3 再动 import 段。）

**改动 2** —— `SYSTEM_PROMPT` 里，在 `"不要输出 question_id，那个由系统按 field 派生；你自己编的 id 会被丢弃。\n"` 这一行**之后**、`"输出 JSON，字段：..."` 这一行**之前**，插入：

```python
    "\n"
    "【字段来源 · 本轮起强制】profile_patch 的值不再是裸值，而是一个对象：\n"
    "- value：字段的值，规范同上（枚举原样、类型正确）\n"
    "- source_quote：**逐字**取自业务经理某一轮原话的片段，用来证明这个值有出处\n"
    "- source_turn：该片段所在的用户轮次编号，就是【对话历史】里 user#N 的那个 N（从 1 开始）\n"
    "正例：业务经理在 user#2 说「需要熟悉 AUTOSAR CP，量产项目至少两个」→\n"
    '  {"autosar_experience": {"value": ["CP"], "source_quote": "熟悉 AUTOSAR CP", "source_turn": 2}}\n'
    "  片段逐字来自 user#2；value 是它的规范化形式，这是允许的——被检查的是引用的真实性，"
    "不是值与引用的字面相等。\n"
    "反例一（复述自己上一轮的问题）：\n"
    '  {"mcu_family": {"value": ["TriCore"], "source_quote": "请问用的是哪一系列 MCU？", "source_turn": 2}}\n'
    "  这句是 assistant 说的。**只有 user#N 才是来源**，你自己问过的话不是。\n"
    "反例二（拼接不存在的句子）：业务经理从没提过 MCU，却写\n"
    '  {"mcu_family": {"value": ["ARM Cortex-M"], "source_quote": "我们用 ARM Cortex-M", "source_turn": 1}}\n'
    "  这句话在 user#1 里根本不存在。\n"
    "指不出逐字出处的字段，宁可不写进 profile_patch。确实要写又给不出引用时，"
    "source_quote 与 source_turn 留 null——系统会把它记为未溯源，这不会中断采集；"
    "但**编造一段引用比留 null 严重得多**。\n"
```

同时把上面【profile_patch 字段规范】那段的首句由

```python
    "【profile_patch 字段规范】键必须取自下面这份岗位画像字段表，值必须符合对应类型；"
```

改为

```python
    "【profile_patch 字段规范】键必须取自下面这份岗位画像字段表，"
    "值写在下面【字段来源】说明的 value 里、必须符合对应类型；"
```

**改动 3** —— `_build_user_prompt` 的 transcript 渲染。把现有的

```python
    transcript = "\n".join(
        f"{turn.get('role', 'user')}: {turn.get('content', '')}" for turn in history
    )
```

替换为对新私有函数的调用，并在 `_build_user_prompt` **上方**加入该函数：

```python
def _render_transcript(history: list[dict]) -> str:
    """
    渲染给模型看的对话历史，**给用户轮次编号**。

    编号是 source_turn 的唯一口径：模型报"这段引用来自 user#2"，
    field_grounding.verify_field_grounding 就按 user_turns(history)[1] 去核。
    两边共用 field_grounding.is_user_turn 这一个谓词，不各写各的判断——
    错位一格的表现是"引用对得上却被判未溯源"，从错误信息里看不出成因。
    """
    lines = []
    user_index = 0
    for turn in history:
        content = turn.get("content", "")
        if is_user_turn(turn):
            user_index += 1
            lines.append(f"user#{user_index}: {content}")
        else:
            lines.append(f"{turn.get('role')}: {content}")
    return "\n".join(lines)
```

```python
    transcript = _render_transcript(history)
```

**改动 4** —— `run_intake_turn` 里的版本号：

```python
        prompt_version="intake-v5",
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_intake_agent.py -q`
Expected: PASS。

⚠️ **既有用例可能连带红两类**，都属预期、都要修而不是删：
1. 断言 `prompt_version == "intake-v4"` 的用例（B 留下的）→ 改成 `intake-v5`，并在该用例里补一句注释说明"v4→v5 由单元 F 的 7.2 触发"。
2. 逐字断言 transcript 形如 `"user: xxx"` 的用例 → 改成 `"user#1: xxx"`。

- [ ] **Step 5: 全量回归**

Run: `./venv/bin/python -m pytest -q`
Expected: 全绿。

- [ ] **Step 6: 提交**

```bash
git add app/agents/intake_agent.py tests/test_intake_agent.py
git commit -m "feat(intake): 提示词要求逐字来源、用户轮次编号，prompt_version 升到 intake-v5（tasks 7.2）"
```

---

### Task 3: `run_intake_turn` 拍平 + 校验，出口恒为裸值（tasks 7.1 / 7.5 前半 / 7.6 / 7.7 / 7.8）

**Files:**
- Modify: `app/agents/intake_agent.py`（`IntakeTurnResult`、`run_intake_turn`）
- Test: `tests/test_intake_agent.py`

**Interfaces:**
- Consumes: Task 1 的 `split_patch_sources` / `verify_field_grounding`；Task 2 已加好的 import
- Produces: `IntakeTurnResult` 新增两个字段
  - `ungrounded_fields: list[str]`（默认 `[]`）——本轮未溯源的业务字段名
  - `written_fields: list[str]`（默认 `[]`）——本轮写入的业务字段名（**含**未溯源的那些，**不含**系统管理字段）
  - 不变式：`set(ungrounded_fields) ⊆ set(written_fields)`，由测试锁死
  - `profile_patch` 的类型不变（`dict`），但**语义收紧**：值恒为裸值

- [ ] **Step 1: 写失败测试**

在 `tests/test_intake_agent.py` 追加：

```python
def _grounded_turn_response(patch: dict) -> str:
    return json.dumps({"is_job_related": True, "questions": [], "profile_patch": patch})


def test_turn_result_patch_is_flat():
    """
    Global Constraints 第一条的机械判据：run_intake_turn 的出口 profile_patch
    里**不存在任何"带 value 键的 dict"**。这是"结构升级不得穿透到落库层"的
    第一道也是最重要的一道闸——它之后的 compute_intake_turn / effect_persist_draft /
    JobProfile.model_validate / jd_agent 全都按裸值读。
    """
    gateway = make_gateway(
        [
            _grounded_turn_response(
                {
                    "headcount": {"value": 2, "source_quote": "要两个人", "source_turn": 1},
                    "job_title": {"value": "嵌入式工程师", "source_quote": "嵌入式工程师", "source_turn": 1},
                }
            )
        ]
    )
    result = run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "要两个人，做嵌入式工程师"}],
        round_count=0,
    )
    assert result.profile_patch == {"headcount": 2, "job_title": "嵌入式工程师"}
    for value in result.profile_patch.values():
        assert not (isinstance(value, dict) and "value" in value)
    assert result.ungrounded_fields == []
    assert sorted(result.written_fields) == ["headcount", "job_title"]


def test_fabricated_field_is_reported_ungrounded():
    """
    tasks 7.6 编造正例：用户一个字都没提 MCU 型号，模型却写了 ARM Cortex-M。
    无论它给不给引用、引用是否为编的，该字段都必须被判为未溯源——
    而且**照常写进画像**（只观测不拦截）。
    """
    gateway = make_gateway(
        [
            _grounded_turn_response(
                {
                    "mcu_family": {
                        "value": ["ARM Cortex-M"],
                        "source_quote": "我们用的是 ARM Cortex-M",
                        "source_turn": 1,
                    }
                }
            )
        ]
    )
    result = run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "要招一个做车身控制器的工程师"}],
        round_count=0,
    )
    assert result.ungrounded_fields == ["mcu_family"]
    assert result.profile_patch == {"mcu_family": ["ARM Cortex-M"]}  # 照常写入，不拦截
    assert result.written_fields == ["mcu_family"]


def test_normalized_value_with_real_quote_is_grounded():
    """tasks 7.7 归纳负例：值被规范化成枚举、引用逐字命中 → 已溯源。"""
    gateway = make_gateway(
        [
            _grounded_turn_response(
                {
                    "functional_safety": {
                        "value": "ASIL-D",
                        "source_quote": "功能安全要 ASIL D",
                        "source_turn": 1,
                    }
                }
            )
        ]
    )
    result = run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "功能安全要 ASIL D，量产项目两个"}],
        round_count=0,
    )
    assert result.ungrounded_fields == []
    assert result.profile_patch == {"functional_safety": "ASIL-D"}


def test_malformed_source_structure_degrades_not_raises():
    """
    tasks 7.8 / spec「来源结构缺失时降级而非报错」：来源结构完全不合法时
    采集仍然完成、值照留、该轮业务字段全计未溯源、**不抛异常**。
    这条路径在提示词刚升到 v5 的头几天一定会被走到。
    """
    gateway = make_gateway(
        [
            _grounded_turn_response(
                {
                    "job_title": {"value": "嵌入式工程师", "source_quote": {"x": 1}, "source_turn": [1]},
                    "department": "研发部",  # 老形态裸值，也算缺来源
                }
            )
        ]
    )
    result = run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "研发部要个嵌入式工程师"}],
        round_count=0,
    )
    assert result.is_job_related is True
    assert result.profile_patch == {"job_title": "嵌入式工程师", "department": "研发部"}
    assert sorted(result.ungrounded_fields) == ["department", "job_title"]


def test_ungrounded_is_subset_of_written():
    """不变式：未溯源字段必然是本轮写入字段的子集。分子不可能大于分母。"""
    gateway = make_gateway(
        [
            _grounded_turn_response(
                {
                    "headcount": {"value": 2, "source_quote": "两个人", "source_turn": 1},
                    "mcu_family": {"value": ["TriCore"], "source_quote": "编的", "source_turn": 1},
                }
            )
        ]
    )
    result = run_intake_turn(
        gateway, history=[{"role": "user", "content": "要两个人"}], round_count=0
    )
    assert set(result.ungrounded_fields) <= set(result.written_fields)


def test_system_managed_fields_are_exempt_and_not_counted():
    """
    tasks 7.4(a)：系统管理字段不参与来源校验，也不计入写入字段总数
    （它不是业务经理提供的信息，把它算进分母会让编造率虚低）。
    """
    gateway = make_gateway(
        [
            _grounded_turn_response(
                {
                    "unspecified_fields": {"value": ["mcu_family"]},
                    "headcount": {"value": 2, "source_quote": "两个人", "source_turn": 1},
                }
            )
        ]
    )
    result = run_intake_turn(
        gateway, history=[{"role": "user", "content": "要两个人"}], round_count=0
    )
    assert result.ungrounded_fields == []
    assert result.written_fields == ["headcount"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_intake_agent.py -q -k "flat or ungrounded or grounded or malformed or system_managed"`
Expected: FAIL —— `AttributeError: 'IntakeTurnResult' object has no attribute 'ungrounded_fields'`，以及 `profile_patch` 里仍是 dict 信封

- [ ] **Step 3: 写实现**

**改动 1** —— `IntakeTurnResult` 追加两个字段（放在 `llm_response_model` 之前）：

```python
    # 本轮未溯源的业务字段名（tasks 7.5）。只观测不拦截：这些字段照常在
    # profile_patch 里，这里只是把"指不出出处"这件事记下来。
    ungrounded_fields: list[str] = field(default_factory=list)
    # 本轮写入的业务字段名，含未溯源的那些、不含系统管理字段。
    # 它是编造率的**分母**——profile_json 存的是累积画像，反推不出本轮写了几个
    # （同一字段被修正重写时键数不变），所以必须在这里算好、逐轮落库。
    written_fields: list[str] = field(default_factory=list)
```

**改动 2** —— `run_intake_turn` 里，把 `at_round_limit` 那一段**之前**插入拍平与校验（即紧跟在 `if not parsed.is_job_related:` 那个 early return 之后）：

```python
    # 拍平点：从这一行往下，profile_patch 里只有裸值。结构升级
    # （{value, source_quote, source_turn}）到此为止，不进 IntakeTurnResult、
    # 不进 profile_patch_accumulated、不进 job_profile.profile_json。
    # 不拍平会同时炸三处，见 delivery-units.md §2.F。
    flat_patch, _sources = split_patch_sources(parsed.profile_patch)
    ungrounded_fields = verify_field_grounding(
        parsed.profile_patch, history, exempt_fields=_SYSTEM_MANAGED_FIELDS
    )
    written_fields = [name for name in flat_patch if name not in _SYSTEM_MANAGED_FIELDS]
```

**改动 3** —— 末尾的 `return IntakeTurnResult(...)` 里，`profile_patch=parsed.profile_patch` 改为 `profile_patch=flat_patch`，并补两个新字段：

```python
    return IntakeTurnResult(
        is_job_related=True,
        questions=questions,
        profile_patch=flat_patch,
        is_complete=give_up or not questions,
        unspecified_fields=parsed.unspecified_fields if give_up else [],
        questions_text=render_questions_text(questions),
        llm_latency_ms=meta.latency_ms,
        llm_response_model=meta.response_model,
        ungrounded_fields=ungrounded_fields,
        written_fields=written_fields,
    )
```

> `is_job_related=False` 的那个 early return **不改**：它返回 `profile_patch={}`，两个新字段走默认空列表即可——非用人需求的那一轮没有业务字段可溯源。

- [ ] **Step 4: 跑测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_intake_agent.py -q`
Expected: PASS

- [ ] **Step 5: 全量回归 + 拦截逻辑自查**

Run: `./venv/bin/python -m pytest -q`
Expected: 全绿。

再跑一遍这条自查，**必须无输出**（决策 12「只观测不拦截」的机械判据）：

```bash
git diff origin/main -- app/ | grep -nE '^\+.*(ungrounded|未溯源)' | grep -E 'raise|return |pop\(|del |409|if not '
```

- [ ] **Step 6: 提交**

```bash
git add app/agents/intake_agent.py tests/test_intake_agent.py
git commit -m "feat(intake): profile_patch 带来源、落库前拍平、逐轮算未溯源清单（tasks 7.1/7.5/7.6/7.7/7.8）"
```

---

### Task 4: 三列落进同一条 INSERT + 按响应模型归因（tasks 7.5 后半 / 7.9）

**Files:**
- Modify: `app/storage/db.py`（`SCHEMA` 的 `CREATE TABLE job_profile` + `_ADDED_COLUMNS`）
- Modify: `app/graph/state.py`（三个 TypedDict 键）
- Modify: `app/graph/nodes.py`（`compute_intake_turn` 透传 + `effect_persist_draft` 追加三列）
- Test: `tests/test_graph_nodes.py` · `tests/test_db_migration.py`

**Interfaces:**
- Consumes: Task 3 的 `IntakeTurnResult.ungrounded_fields` / `.written_fields` / `.llm_response_model`
- Produces: `IntakeState` 三个新键 `ungrounded_fields: list[str]` / `written_fields: list[str]` / `llm_response_model: str | None`；`job_profile` 表新列 `written_fields TEXT NOT NULL DEFAULT '[]'`

> **⛔ 本 Task 的三条红线**：① **不新增 `effect_*` 节点**；② **不新增第二条 `INSERT INTO job_profile`**；③ **`business_key` 仍然是 `round_count`，不改**。三列全部追加进 `effect_persist_draft` 已有的那条 INSERT——理由与 tasks 1.5 完全一致：多一次写入就多一个能失败的地方，而 spec 的 `Scenario: 来源与画像同生共死`（画像写失败则来源信息同样不存在）是正面契约，同一条 INSERT 是它唯一自然成立的形态。

- [ ] **Step 1: 写失败测试**

在 `tests/test_graph_nodes.py` 追加：

```python
def test_grounding_columns_land_in_same_insert(tmp_path):
    """
    tasks 7.5 / 7.9 + spec「来源与画像同生共死」：未溯源清单、写入字段清单、
    响应模型标识与画像草案在**同一条 INSERT** 里落库，不新增 effect 节点、
    business_key 不变。effect_log 条数与 job_profile 行数按 thread 恒等（铁律 1）。
    """
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.commit()

    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [],
                    "profile_patch": {
                        "headcount": {"value": 2, "source_quote": "要两个人", "source_turn": 1},
                        "mcu_family": {
                            "value": ["TriCore"],
                            "source_quote": "我们一直用 TriCore",
                            "source_turn": 1,
                        },
                    },
                }
            )
        ],
        response_model="deepseek-chat-20260801",
    )
    state = compute_intake_turn(
        {
            "job_id": "job1",
            "history": [{"role": "user", "content": "要两个人"}],
            "round_count": 0,
            "profile_patch_accumulated": {},
            "turn_started_at": "2026-08-25 01:02:03",
        },
        gateway=gateway,
    )
    effect_persist_draft(conn, thread_id="job1", business_key="0", state=state)

    row = conn.execute(
        "SELECT ungrounded_fields, written_fields, llm_response_model, profile_json "
        "FROM job_profile WHERE job_id='job1'"
    ).fetchone()
    assert json.loads(row[0]) == ["mcu_family"]          # 引用是编的 → 未溯源
    assert sorted(json.loads(row[1])) == ["headcount", "mcu_family"]
    # 铁律 5：记的是**响应返回的**标识，不是配置里的别名 deepseek-chat-241226
    assert row[2] == "deepseek-chat-20260801"
    assert row[2] != "deepseek-chat-241226"

    counts = conn.execute(
        "SELECT (SELECT COUNT(*) FROM job_profile WHERE job_id='job1'), "
        "(SELECT COUNT(*) FROM effect_log WHERE thread_id='job1' "
        " AND effect_key LIKE '%effect_persist_draft%')"
    ).fetchone()
    assert counts[0] == counts[1] == 1


def test_profile_json_stays_flat_end_to_end(tmp_path):
    """
    Global Constraints 第一条的**终点判据**：落库后的 profile_json 反序列化出来
    必须是裸值，且能直接喂进 JobProfile.model_validate（headcount 收到 int 而
    不是 dict）。这条炸了就是 POST /confirm 的 422，以及 jd_agent 读到一堆 dict。
    """
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.commit()

    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [],
                    "profile_patch": {
                        "job_title": {"value": "嵌入式工程师", "source_quote": "嵌入式工程师", "source_turn": 1},
                        "department": {"value": "研发部", "source_quote": "研发部", "source_turn": 1},
                        "headcount": {"value": 2, "source_quote": "两个", "source_turn": 1},
                        "education_requirement": {"value": "本科", "source_quote": "本科", "source_turn": 1},
                        "experience_years": {"value": "3-5年", "source_quote": "3-5年", "source_turn": 1},
                    },
                }
            )
        ]
    )
    state = compute_intake_turn(
        {
            "job_id": "job1",
            "history": [
                {"role": "user", "content": "研发部要两个嵌入式工程师，本科，3-5年经验"}
            ],
            "round_count": 0,
            "profile_patch_accumulated": {},
            "turn_started_at": "2026-08-25 01:02:03",
        },
        gateway=gateway,
    )
    # 累积态本身也必须是裸值——它是下一轮 prompt 的输入，信封会污染下一轮
    assert state["profile_patch_accumulated"]["headcount"] == 2

    effect_persist_draft(conn, thread_id="job1", business_key="0", state=state)
    stored = json.loads(
        conn.execute("SELECT profile_json FROM job_profile WHERE job_id='job1'").fetchone()[0]
    )
    assert stored["headcount"] == 2
    JobProfile.model_validate(stored)  # 不抛 = POST /confirm 那一步不会 422
```

`tests/test_graph_nodes.py` 顶部按需补 `from app.schemas.job_profile import JobProfile`，并确认本文件的 `make_gateway` 支持 `response_model=` 关键字（`tests/test_intake_agent.py` 里的同名 helper 已支持；若本文件是自己的一份副本，照 `test_intake_agent.py` 的形态补上这个参数）。

在 `tests/test_db_migration.py` 中，把既有那条列出全部新列的 SELECT 扩上 `written_fields` 并断言其默认值：

```python
        "SELECT profile_json, unspecified_fields, is_productive, turn_started_at, "
        "llm_latency_ms, derived_unspecified_fields, ungrounded_fields, written_fields, "
        "llm_response_model "
```

```python
    assert json.loads(row[7]) == []   # written_fields 老行按默认值 [] 成立，不回填
```

（下标以 rebase 后的实际 SELECT 顺序为准。）

- [ ] **Step 2: 跑测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_graph_nodes.py tests/test_db_migration.py -q`
Expected: FAIL —— `sqlite3.OperationalError: no such column: written_fields`

- [ ] **Step 3: 写实现（四处改动）**

**改动 1** —— `app/storage/db.py`，`SCHEMA` 的 `CREATE TABLE job_profile` 里，紧挨 `ungrounded_fields` 那一行之后加：

```sql
    written_fields TEXT NOT NULL DEFAULT '[]',
```

`_ADDED_COLUMNS` 里，紧挨 `("job_profile", "ungrounded_fields", ...)` 之后加：

```python
    # 编造率的分母（第 7 章）。profile_json 存的是累积画像，反推不出"本轮写了
    # 几个字段"——同一字段被修正重写时键数不变，差集恒为空、分母恒偏小、
    # 编造率恒偏大。所以逐轮把写入字段名单单独落一列。
    # 默认值必须是常量（SQLite 拒绝非常量默认值的 ALTER TABLE ADD COLUMN）。
    ("job_profile", "written_fields", "TEXT NOT NULL DEFAULT '[]'"),
```

⚠️ **两处必须同时改**：只改 `CREATE TABLE` 的话，`.51` 上那 15 个真实 job 的老库永远不会出现这一列，**而且不报错**（`CREATE TABLE IF NOT EXISTS` 对已存在的表完全无效，design.md 决策 10）。

**改动 2** —— `app/graph/state.py` 末尾追加：

```python
    # 本轮未溯源的业务字段名（第 7 章 intake-field-grounding）。
    # 只观测不拦截：这几个键的存在不影响图的任何分支判断。
    ungrounded_fields: list[str]

    # 本轮写入的业务字段名，编造率的分母。
    written_fields: list[str]

    # API 响应里实际返回的模型标识（铁律 5）。与配置里的别名分开记录、
    # 不互相覆盖——配置里写的名字不算数，响应返回的才算。
    llm_response_model: str | None
```

**改动 3** —— `app/graph/nodes.py` 的 `compute_intake_turn` 返回体，在 `"llm_latency_ms": result.llm_latency_ms,` 之后追加：

```python
        # 溯源三件套（第 7 章）。时序留痕不承担审计职责，所以模型标识不走
        # llm_latency_ms 那条线，而是随未溯源清单一起、按 intake-field-grounding
        # 的「编造信号可按模型版本归因」落库。
        "ungrounded_fields": result.ungrounded_fields,
        "written_fields": result.written_fields,
        "llm_response_model": result.llm_response_model,
```

**改动 4** —— `app/graph/nodes.py` 的 `effect_persist_draft`。**只在既有 INSERT 的列清单与占位符末尾追加三项**（下面按"D 已合并"的形态给出；rebase 后以仓库实际列清单为准，B 的 `is_productive`、D 的 `derived_unspecified_fields` 一律保留在原位）：

```python
    conn.execute(
        "INSERT INTO job_profile "
        "(id, job_id, version, status, profile_json, unspecified_fields, "
        "turn_started_at, llm_latency_ms, "
        "ungrounded_fields, written_fields, llm_response_model) "
        "VALUES (?, ?, ?, 'drafting', ?, ?, ?, ?, ?, ?, ?)",
        (
            f"{thread_id}-v{version}",
            thread_id,
            version,
            profile_json,
            unspecified_json,
            state.get("turn_started_at"),
            state.get("llm_latency_ms"),
            json.dumps(state.get("ungrounded_fields", []), ensure_ascii=False),
            json.dumps(state.get("written_fields", []), ensure_ascii=False),
            state.get("llm_response_model"),
        ),
    )
```

并把该函数 docstring 末尾那段"这几列本单元不写值"更新为：

```python
    2026-08-25（第 7 章 tasks 7.5/7.9）：ungrounded_fields / written_fields /
    llm_response_model 三列写在**同一条 INSERT** 里，不新增 effect 节点、
    business_key 语义不变（仍是 round_count）。理由与上面的时序两列完全一致：
    intake-field-grounding 的「来源与画像同生共死」要求"画像有这一轮、来源没有
    这一轮"不可能出现，同一条 INSERT 是这条契约唯一自然成立的形态。
```

**改动 5** —— 改写 `tests/test_graph_nodes.py::test_timing_trace_records_no_model_identity`。

这条测试是 2026-08-19 单元 A 立的，断言 `llm_response_model IS NULL` 且 `"llm_response_model" not in state`。**F 正是那条注释里写明的"第 7 章"**，所以它现在必须改——但**不许删**，要把它改成守住"时序留痕本身不承担审计职责"这个仍然有效的契约：

```python
def test_timing_trace_carries_no_model_identity_of_its_own(tmp_path):
    """
    intake-turn-observability「时序留痕不承担审计职责」：**时序那两列**里只有
    时间与耗时，不含模型标识。

    2026-08-25 更新（第 7 章 intake-field-grounding 上岗）：llm_response_model
    这一列从此**有值**了，但它是溯源归因写的，不是时序留痕写的——原断言
    `row[0] is None` 记录的是"第 7 章还没做"这个事实，不是一条永久契约
    （单元 A 在原注释里就写明了"它归第 7 章"）。这里改为守住真正的契约：
    时序两列不因模型标识而改变，模型标识来自 result.llm_response_model 这条
    独立通道。**这不是把测试改松，是把它改准。**
    """
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.commit()

    gateway = make_gateway(
        [json.dumps({"is_job_related": True, "questions": [], "profile_patch": {"headcount": 1}})],
        response_model="deepseek-chat-20260801",
    )
    state = compute_intake_turn(
        {
            "job_id": "job1",
            "history": [{"role": "user", "content": "要一个人"}],
            "round_count": 0,
            "profile_patch_accumulated": {},
            "turn_started_at": "2026-08-25 01:02:03",
        },
        gateway=gateway,
    )
    effect_persist_draft(conn, thread_id="job1", business_key="0", state=state)

    row = conn.execute(
        "SELECT turn_started_at, llm_latency_ms, llm_response_model "
        "FROM job_profile WHERE job_id='job1'"
    ).fetchone()
    assert row[0] == "2026-08-25 01:02:03"
    assert row[1] is not None
    # 模型标识经溯源通道落库，与时序两列互不干涉
    assert row[2] == "deepseek-chat-20260801"
    assert state["llm_response_model"] == "deepseek-chat-20260801"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_graph_nodes.py tests/test_db_migration.py -q`
Expected: PASS

- [ ] **Step 5: 全量回归 + 老库迁移路径专测**

```bash
./venv/bin/python -m pytest -q
./venv/bin/python -m pytest tests/test_db_migration.py -q -v
```
Expected: 全绿。特别确认漂移守卫 `test_fresh_and_migrated_schemas_have_identical_columns` 通过——它是"新库/老库两条路径列清单一致"的唯一保证。

- [ ] **Step 6: 提交**

```bash
git add app/storage/db.py app/graph/state.py app/graph/nodes.py \
        tests/test_graph_nodes.py tests/test_db_migration.py
git commit -m "feat(graph): 未溯源清单/写入字段/响应模型标识落进同一条 INSERT（tasks 7.5/7.9）"
```

---

### Task 5: 点选来源的核实与回归钉子（tasks 7.4 的 (b) 半）

**Files:**
- Test: `tests/test_field_grounding.py`（追加，不改生产代码）

**Interfaces:**
- Consumes: Task 1 的 `verify_field_grounding`；单元 C 在 `app/web/static/index.html::collectSelections()` 里定下的拼接格式
- Produces: 无生产代码。**本 Task 的产出是一个结论 + 一颗钉住它的钉子。**

> **本 Task 为什么存在**：tasks 7.4 的后半要求"点选产生的字段以被选中的档位标识作为来源，不要求在自由文本里找片段"。经核实**不必单独实现**（完整理由见本计划「偏差二」，三条依据 + 一条已知代价）。但"不必实现"这个结论如果只写在文档里，前端哪天改了拼接格式就没人知道它失效了——所以必须落成一个对着**真实拼接格式**的回放测试。

- [ ] **Step 1: 先核实前端的拼接格式仍然成立**

```bash
grep -n 'dataset.qtext' app/web/static/index.html
grep -n 'selected_options' app/web/server.py    # 预期：无输出（API 契约未变）
grep -rn 'test_reply_api_contract_has_no_selected_options' tests/
```

Expected：第一条打印出 `lines.push(block.dataset.qtext + "：" + picked.join("、"));`（或 rebase 后语义等价的一行）；第二条**无输出**；第三条能找到单元 C 立下的那条契约测试。
**三条里任何一条不符 → 停下来，(b) 重新变成真问题，需要与 Shao Peishen 确认是否重开 API 契约。**

- [ ] **Step 2: 写测试（这次是先写、预期直接通过——它是回归钉子，不是 TDD 的红灯）**

在 `tests/test_field_grounding.py` 追加：

```python
def test_selected_option_is_grounded_without_a_special_case():
    """
    tasks 7.4(b) 的核实结论，钉成测试。

    单元 C 的 collectSelections() 把点选拼成 `问题原文：档位A、档位B` 一行、
    与自由文本合并成一条 message 提交给既有的 POST /reply（API 契约未变，
    §5 约定 2）。于是**被选中的档位文本逐字出现在该轮用户原话里**，
    7.3 的子串判定天然命中——不需要任何"点选例外"分支。

    **这个测试将来若失败，说明前端的拼接格式变了、(b) 重新变成真问题。
    那是一次设计对话（要不要给 ReplyRequest 加回 selected_options），
    不是一个可以删掉的测试。**
    """
    # 逐字复刻 collectSelections() 的输出形态：问题原文 + "：" + 档位、顿号分隔
    history = [
        {"role": "user", "content": "要招个做 ECU 的"},
        {"role": "assistant", "content": "是否有功能安全等级要求？"},
        {
            "role": "user",
            "content": "是否有功能安全等级要求？：ASIL-D\n量产项目要求几个？：2 个及以上",
        },
    ]
    patch = {
        "functional_safety": {"value": "ASIL-D", "source_quote": "ASIL-D", "source_turn": 2},
        "project_experience_requirement": {
            "value": "2 个及以上量产项目",
            "source_quote": "2 个及以上",
            "source_turn": 2,
        },
    }
    assert verify_field_grounding(patch, history) == []


def test_free_text_mixed_with_selection_still_grounds():
    """点选 + 自由文本混合提交（单元 C 支持的第三条路径）同样天然命中。"""
    history = [
        {"role": "user", "content": "MCU 用哪个系列？：Infineon TriCore\n另外要会 CAPL 脚本"}
    ]
    patch = {
        "mcu_family": {"value": ["TriCore"], "source_quote": "Infineon TriCore", "source_turn": 1},
        "toolchain": {"value": ["CAPL"], "source_quote": "会 CAPL 脚本", "source_turn": 1},
    }
    assert verify_field_grounding(patch, history) == []
```

- [ ] **Step 3: 跑测试**

Run: `./venv/bin/python -m pytest tests/test_field_grounding.py -q`
Expected: PASS，22 passed（Task 1 的 20 条 + 本 Task 的 2 条）

若这两条**红了**，说明"(b) 不必单独实现"的结论不成立 —— ⛔ **不要在 `verify_field_grounding` 里加一个点选特判把它调绿**（后端拿不到"哪些是点选的"信号，任何特判都只能靠猜）。停下来，把红灯的具体形态报给 Shao Peishen，按「偏差二」的三条依据重新判断。

- [ ] **Step 4: 全量回归**

Run: `./venv/bin/python -m pytest -q`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add tests/test_field_grounding.py
git commit -m "test(intake): 钉住 7.4 点选来源天然命中的核实结论（tasks 7.4）"
```

---

### Task 6: 编造率的可复算定义与技术债登记（tasks 7.10 / 7.11）

**Files:**
- Create: `docs/m1-fabrication-rate.md`
- Modify: `docs/tech-debt.md`

**Interfaces:**
- Consumes: Task 4 落库的 `ungrounded_fields` / `written_fields` / `llm_response_model` 三列
- Produces: `docs/m1-fabrication-rate.md` —— 编造率的**唯一**可复算定义，G 的 8.7 往里填第一个真实数字

- [ ] **Step 1: 先把 SQL 在真库上跑通，再写进文档**

**不要先写文档后验证。** 先建一个临时库跑一遍，确认 `json_array_length` 在本机 SQLite 上可用（它需要 JSON1 扩展；Python 3.14 自带的 SQLite 一般已编译进去，但这是必须验证而不是假设的事）：

```bash
./venv/bin/python - <<'PY'
import sqlite3, json
from app.storage.db import init_schema
conn = sqlite3.connect(":memory:")
init_schema(conn)
conn.execute("INSERT INTO job (id, title, status) VALUES ('j1','t','drafting')")
rows = [
    ("j1-v1","j1",1,'{}',json.dumps(["mcu_family"]),json.dumps(["headcount","mcu_family"]),"deepseek-chat-A"),
    ("j1-v2","j1",2,'{}',json.dumps([]),json.dumps(["job_title"]),"deepseek-chat-A"),
    ("j1-v3","j1",3,'{}',json.dumps(["toolchain","diag_stack"]),json.dumps(["toolchain","diag_stack"]),"deepseek-chat-B"),
    ("j1-v4","j1",4,'{}','[]','[]',None),   # 老行：两列走默认值，分母为 0
]
conn.executemany(
    "INSERT INTO job_profile (id,job_id,version,status,profile_json,"
    "ungrounded_fields,written_fields,llm_response_model) "
    "VALUES (?,?,?,'drafting',?,?,?,?)", rows)
print(conn.execute("""
SELECT COALESCE(llm_response_model,'(未记录)') AS model,
       COUNT(*) AS turns,
       SUM(json_array_length(written_fields))     AS written,
       SUM(json_array_length(ungrounded_fields))  AS ungrounded,
       ROUND(1.0 * SUM(json_array_length(ungrounded_fields))
             / NULLIF(SUM(json_array_length(written_fields)), 0), 4) AS rate
FROM job_profile
GROUP BY COALESCE(llm_response_model,'(未记录)')
ORDER BY model
""").fetchall())
PY
```

Expected 输出（三组，第三组分母为 0 → rate 为 None 而不是除零报错）：
```
[('(未记录)', 1, 0, 0, None), ('deepseek-chat-A', 2, 3, 1, 0.3333), ('deepseek-chat-B', 1, 2, 2, 1.0)]
```

- [ ] **Step 2: 写 `docs/m1-fabrication-rate.md`**

```markdown
# 编造率的可复算定义（M1 采集）

> 立于 2026-08-25，`m1-intake-quality-fixes` 第 7 章（`intake-field-grounding`）。
> 本文件是**编造率这个数字的唯一定义**。任何地方引用"编造率"都必须指回这里，
> 否则两个人报的数字不可比，而这个数字要被拿去做换不换模型的决定。

## 定义

**编造率（下界）= 未溯源字段数 ÷ 写入字段总数**，逐轮统计、按**响应返回的**模型标识分组。

- **分子** `job_profile.ungrounded_fields`：该轮写进画像、但引用片段过不了确定性子串
  判定的业务字段名。判定规则见 `app/agents/field_grounding.py::verify_field_grounding`
  ——引用（NFKC + 去空白归一化后）必须在它自己声明的那一轮用户原话里原样找到。
- **分母** `job_profile.written_fields`：该轮写进画像的业务字段名，**含**未溯源的那些，
  **不含**系统管理字段（`unspecified_fields`）。
  *为什么单独存一列而不是从 `profile_json` 数*：`profile_json` 是**累积**画像，
  同一字段被修正重写时键数不变，反推出来的分母恒偏小、编造率恒偏大。
- **分组键** `job_profile.llm_response_model`：API 响应里实际返回的模型标识
  （铁律 5），**不是**配置里写的别名。别名经 `AuditHook` 单独记录，两者不互相覆盖。

## 口径 SQL

```sql
SELECT COALESCE(llm_response_model, '(未记录)')        AS model,
       COUNT(*)                                        AS turns,
       SUM(json_array_length(written_fields))          AS written_fields,
       SUM(json_array_length(ungrounded_fields))       AS ungrounded_fields,
       ROUND(1.0 * SUM(json_array_length(ungrounded_fields))
             / NULLIF(SUM(json_array_length(written_fields)), 0), 4) AS fabrication_rate_lower_bound
FROM job_profile
GROUP BY COALESCE(llm_response_model, '(未记录)')
ORDER BY model;
```

在 `.51` 上跑（口令与路径见 `05-发布运行手册.md`）：

```bash
sqlite3 data/demo.db < docs/sql/fabrication-rate.sql
```

`NULLIF` 不是装饰：2026-08-25 之前写下的历史行两列都是默认 `[]`，分母为 0，
没有 `NULLIF` 会直接除零报错，整份统计拿不到任何结果。这些行的 `model` 会归到
`(未记录)` 组，**不要把它们算进任何一个模型的比例里**。

## 这个数字是**下界**，不是精确值

`design.md` 决策 11 已经声明这一点，这里补齐全部三条已知的低估来源。三条都让
真实编造率**大于等于**这里算出的数字，方向一致，因此"编造率下降了"这个结论是可信的，
"编造率只有 X%"这个绝对值不可信。

1. **抄一段无关的原话来凑引用**：引用是真的、与字段值无关。过得了子串判定。
   （决策 11 已评估并接受。）
2. **点选提交会把问题原文一起拼进用户消息**：单元 C 的 `collectSelections()` 拼的是
   `问题原文：档位A、档位B`，因此问题文本自身逐字包含某个档位值时
   （如「ASIL 等级要求（ASIL-B / ASIL-D）？」），该值即使**未被勾选**也出现在
   用户原话里，模型引用它可以过校验。本批**不收窄搜索范围**——收窄需要后端持有
   上一轮的 `pending_questions` 并做字符串剥离，又脆又只影响下界的紧度。
3. **归一化去掉了全部空白**：英文词边界因此消失，`"C  A"` 能匹配 `"CA"`。

## 怎么读这个数字

- **和什么比**：`docs/m1-model-comparison.md` 记录的 `deepseek-v4-pro` 实测 1/3 编造率
  是人工核对得出的，与本口径**不可直接相减**（一个是人判、一个是引用判）。
  可比的是**同口径的前后两次**：换模型前后、改提示词前后。
- **样本量门槛**：`design.md` 决策 12 定死了触发条件——累计 ≥ 20 场真实采集会话
  拿到分布之后，才单独开变更定拦截阈值。**在此之前不要据此改任何拦截逻辑。**
- **人工抽查仍然必要**：`design.md` 风险表第 1 条要求回放真实会话时人工抽查若干
  未溯源字段，区分"模型不会引用"与"模型真编造"。第一次抽查结论由第 8 章 8.7 填在下方。

## 首次真实测量（第 8 章 8.7 回填）

| 日期 | 模型标识 | 轮数 | 写入字段 | 未溯源 | 未溯源率 | 人工抽查结论 |
|---|---|---|---|---|---|---|
| 待填 | | | | | | |
```

同时建 `docs/sql/fabrication-rate.sql`，内容就是上面那段 SQL（让运行手册里的那条命令是真能跑的，而不是一段要人现拷的文本）。

- [ ] **Step 3: 登记技术债（tasks 7.11）**

先看现有最大编号，再往 `docs/tech-debt.md` **末尾追加**（写作本计划时文件里只有 `TD-1`，故新增为 `TD-2`；若 B/E 已先落一条，顺延到下一个未用编号）：

```bash
grep -n '^## TD-' docs/tech-debt.md
```

```markdown
## TD-2 · 未溯源字段只观测、拦截策略未定

**欠的是什么**：`intake-field-grounding` 只度量不拦截（`design.md` 决策 12）。
未溯源字段**照常写进岗位画像**，系统只把清单与该轮响应模型标识落库。
换句话说，上线后画像里仍可能有编造内容，与今天一样——区别只是它从这一批起
**可见且可数**。

**触发条件**：本批上线后累计 **≥ 20 场真实采集会话**，按
`docs/m1-fabrication-rate.md` 的口径拿到未溯源率分布之后，**单独开一个变更**
定拦截阈值与降级方式（退回追问 vs 记为未指定）。
这个条件是写死的，不是"有空再说"——`design.md` 决策 12 原文。

**怎么还**：新变更里定三件事：① 阈值取多少；② 命中阈值后怎么降级；
③ 降级动作对业务经理是否可见。**注意**：拦截会改变「AI 不得代替业务经理做决定」
这条红线附近的行为，属决策代理表里 Shao Peishen 本人拍板的范围。

**不还的后果**：编造率被年复一年地"观测"下去，没有任何一次真正拦住编造。
这条债的全部价值在于那个触发条件——删掉触发条件，这条就等于没登记。

**为什么当时要欠**：`deepseek-chat`（flash）的真实编造率是未知数。接近 0 时拦截
几乎无成本；像 `v4-pro` 一样是 1/3 时，直接拦截会把三分之一的字段挡在画像外，
采集直接不可用。而"模型不擅长给逐字引用"与"模型在编造"这两种情况，在没有数据
之前分不开。**先量再拦**是唯一负责任的顺序；先拦会用一次线上事故换来同一个数字。
```

- [ ] **Step 4: 验证文档里的东西真的能跑**

```bash
ls docs/sql/fabrication-rate.sql
./venv/bin/python -m pytest -q          # 文档 Task 不该动测试结果，确认仍全绿
grep -c '^## TD-' docs/tech-debt.md     # 应比改动前多 1
```

- [ ] **Step 5: 提交**

```bash
git add docs/m1-fabrication-rate.md docs/sql/fabrication-rate.sql docs/tech-debt.md
git commit -m "docs: 编造率可复算口径 + 拦截策略技术债登记（tasks 7.10/7.11）"
```

---

## 完成判据（final review 用）

**功能**

- [ ] `specs/intake-field-grounding/spec.md` 四条 Requirement、全部 12 个 Scenario 都能指到测试（见覆盖矩阵）
- [ ] tasks.md 7.1–7.11 十一项全部有归属，7.4(b) 的"不单独实现"有 Task 5 的核实与钉子，不是省略
- [ ] `./venv/bin/python -m pytest -q` 全绿

**Global Constraints 的机械判据（逐条跑）**

- [ ] `run_intake_turn` 出口的 `profile_patch` 无信封 → `test_turn_result_patch_is_flat`
- [ ] `git diff origin/main -- app/graph/ app/web/ | grep -E 'source_quote|source_turn'` **无输出**
- [ ] 落库 `profile_json` 能过 `JobProfile.model_validate` → `test_profile_json_stays_flat_end_to_end`
- [ ] `git diff origin/main -- app/ | grep -c 'idempotent_effect'` 为 **0**（不新增 effect 节点）
- [ ] `grep -c 'INSERT INTO job_profile' app/graph/nodes.py` 为 **1**（不新增第二条写入）
- [ ] `grep -n 'prompt_version="intake-' app/agents/intake_agent.py` 显示 **v5**，且仓库里搜不到第二个 v5 占用方
- [ ] 落库的模型标识 == 响应返回值 ≠ 配置别名 → `test_grounding_columns_land_in_same_insert`
- [ ] **无拦截**：`git diff origin/main -- app/ | grep -nE '^\+.*(ungrounded|未溯源)' | grep -E 'raise|pop\(|del |409'` **无输出**
- [ ] **零业务可见**：`git diff --name-only origin/main` 里**不含** `app/web/static/index.html`、`app/web/server.py`
- [ ] `field_grounding.py` 的 import 段只有 `__future__` / `unicodedata` / `dataclasses` / `typing`
- [ ] 新列同时出现在 `SCHEMA` 与 `_ADDED_COLUMNS`，`test_fresh_and_migrated_schemas_have_identical_columns` 通过

**回勾**

- [ ] `openspec/changes/m1-intake-quality-fixes/tasks.md` 第 7 章 7.1–7.11 全部勾选（final review 通过**之后**才勾）

## 已知遗留（不在本单元修，写明去向）

1. **`suggested_followups` 用的是 `turn.get("role") == "user"`（严格相等），与本单元的 `is_user_turn`（缺 role 默认算用户）口径不同。** 本单元**不动它**——改它会改变 ECU 知识库追问的触发条件，那是单元 B 的地盘，而这个差异只在"历史行缺 role"这种脏数据上才现形，且对 followup 的影响是少注入一条建议、无害。若日后统一，统一到 `field_grounding.is_user_turn` 这一个谓词上。
2. **归一化去空白导致英文词边界消失**（`"C  A"` 匹配 `"CA"`）。方向是让编造率偏低，与"取下界"同向，已写进 `docs/m1-fabrication-rate.md` 的偏差清单。要收紧需要区分中英文分别处理，等真实数据显示这类误判确实发生了再说。
3. **点选提交把问题原文一起拼进用户消息**，导致问题文本里逐字出现的档位值可被引用。同上，写进偏差清单、不在本批收窄，理由见「偏差二」。
4. **`written_fields` 与 `job_profile.turn_started_at` / `llm_latency_ms` 同属"画像行兼职承载度量数据"的形态**，`ai-audit-trail-and-outbound-gate` 的 `analysis_run` 表落地时应一并重新安置（见 `docs/tech-debt.md` TD-1 的同类问题）。本批不动。
5. **未溯源信息不进 `GET /api/jobs/{id}` 的响应体、不进前端。** 这是决策 12「本章只观测不拦截、业务可见性为零」的直接后果，不是遗漏。要看数字走 `docs/m1-fabrication-rate.md` 的 SQL。
