# 交付单元拆分（第 1-7 章）

> **状态：待 Shao Peishen 确认。** 本文件只出拆分方案，**不含实现计划**——每个单元的 plan 由 `spec-to-plan` 单独出。
> §3.5 的总开关默认值属 CLAUDE.md 决策代理表的**不可代**项，未决前 U4 不开工（其余单元不受阻）。
>
> 粒度约定沿用 CLAUDE.md：**一个交付单元 = 一份 superpowers plan = 一条 worktree 分支 = 一个可独立测试并合并的东西**。
> 单元编号 **U1–U7 直接对应 tasks.md 的第 1–7 章**，与 `m1-intake-quality-fixes` 的 A–G 无关——两批同期在跑，编号刻意不复用字母以免口头指代混淆。

## 零、这个包为什么要紧

两条合规底线目前在本仓库里都**只有壳**：

- **AI 评分留痕**：`app/llm/gateway.py:114` 的 `AuditHook` 只有 `NoopAuditHook`（`gateway.py:129-137`，函数体就一行 `logger.debug`），工程铁律 3、4 **一条都不成立**。模型标识、prompt 版本、输入哈希、rubric 快照、原始响应都没落盘；`criterion_score` 与 `evidence_ref` 连表都不存在。
- **外发人工确认门禁**：`effect_deliver_message`（`app/graph/nodes.py:117-127`）函数体是 `channel.deliver(thread_id, message)` 一行，无条件投递。合规红线「AI 只做排序推荐，不做自动淘汰」目前靠调用方自觉。

**M2 开始处理真实简历前这两条必须就位**——PIPL 第 24 条的说明权要求能回答"这条评分是哪个模型、哪个版本、按哪份 rubric 打的，依据是简历里哪一段"。今天回答不了。

## 一、单元划分表

| 单元 | 覆盖章节 | tasks | 主要触碰文件 | 依赖 | 被谁依赖 | 规模（openspec tasks → 预估 plan Task） |
|---|---|---|---|---|---|---|
| **U1** 数据层与配置位 | 第 1 章 + 两个配置键 | 1.1–1.6 ＋ 3.3/4.5 的配置键位 | `app/storage/db.py`｜`app/config.py`｜`tests/test_db*.py` | 无 | U2–U7 全部 | 6 → **3-4** |
| **U2** `app/audit` 模块 | 第 2 章 | 2.1–2.9 | `app/audit/`（全新）｜`tests/test_audit_*.py`（全新） | U1（三张表） | U3、U5、U6 | 9 → **5-6** |
| **U3** 留痕接线 | 第 3 章 | 3.1–3.7 | `app/llm/gateway.py`｜**`app/main.py`**｜`tests/test_llm_gateway.py` | U1、U2 | U6（6.2/6.3/6.4 要有真实数据） | 7 → **4-5** |
| **U4** `app/outbound` 门禁纯函数 | 第 4 章 | 4.1–4.9 | `app/outbound/`（全新）｜`tests/test_outbound_gate.py`（全新）｜只读 `app/agents/jd_agent.py` | U1（配置键）；**逻辑上不依赖 U2/U3** | U5 | 9 → **4-5** |
| **U5** 队列与图节点接线 | 第 5 章 | 5.1–5.9 | `app/outbound/queue.py`｜**`app/graph/nodes.py`**｜`app/graph/build.py`｜`tests/test_graph_nodes.py`｜`tests/test_transaction_ownership.py` | U1、U2（留痕）、U4（判定） | U6（6.5 拦截统计） | 9 → **5-6** |
| **U6** 合规断言、对账与 CI | 第 6 章 | 6.1–6.7 | `app/audit/assertions.py`｜`tests/`｜`.github/workflows/ci.yml` | U2、U3、U5 | U7 | 7 → **3-4** |
| **U7** 边界守护与文档 | 第 7 章 | 7.1–7.6 | `.github/workflows/ci.yml`｜`docs/`（新增一页）｜`06-企业AI转型资产借鉴清单.md`｜`07-开发环境现状与优化待办.md` | 前面全部 | —— | 6 → **2-3**，见 §2.U7 |

合计 53 项 openspec task（不含配置键位那两条搬家项）。

## 二、各单元详情

### U1 · 数据层与配置位（第 1 章）

**为什么它必须第一个、且必须很小**：三张表是 U2–U6 全部的地基，`db.py` 的 `SCHEMA` 又是本仓库唯一的 DDL 真源。把它做成一个只改建表脚本的小单元，后面五个单元才能真并行。

**两条硬约束（写进 U1 的 plan 的 Global Constraints）**：

1. **`analysis_run` 的业务关联列与 rubric 列必须全部可空**——`application_id` / `job_id` / `rubric_snapshot` / `system_fingerprint` / `token_usage` 一律允许 NULL。理由不是"以防万一"：U3 一旦把 `RecorderAuditHook` 接到 `_gateway_factory()` 上，**M1 现有的岗位画像采集调用会立刻开始写 `analysis_run`**，而采集期没有投递、没有 rubric。任何一列 NOT NULL 都会在 U3 合并当天把 M1 的采集流程打挂。只有 `id` / `configured_model` / `prompt_version` / `temperature` / `input_hash` / `raw_response` / `created_at` 是 NOT NULL。
2. **新表全部走 `CREATE TABLE IF NOT EXISTS`，不碰 `_ADDED_COLUMNS` 那条加列路径**（`db.py:71-108`）。三张表都是新的，`.51` 上 15 个真实 job 的既有表一行不改，无数据迁移。1.6 的回归测试就是这条的守护。

**顺带搬进来的两条**：`app/config.py` 的两个新配置键——审计 JSONL 路径（原 3.3）与 `CANDIDATE_OUTBOUND_ENABLED`（原 4.5）。**搬家理由见 §4 约定 1**：一次加齐，换来 U3 与 U4 不再共写 `config.py`，两条分支可真并行。默认值取 tasks 4.5 已写的「关闭」，§3.5 若改判只改一行常量，不返工。

### U2 · `app/audit` 模块（第 2 章）

**性质**：全新目录，此时**尚未接线**，现有行为完全不变。与整个仓库零文件重叠——本批里最干净的一个单元，可与 M1 的任何单元并行。

**依赖（具体到符号）**：只依赖 U1 的三张表与 `app/storage/db.py` 的连接约定（`SqliteSink.write` 不自行 `commit`，与 `effect_persist_draft` 同一约定，见 `nodes.py:61` 那段注释）。

**本单元含金量最高的两条是 2.5 与 2.6**（hash-chain 的两个已知绕过），平台侧已经踩过，本仓库一次做对。2.5 的第四个场景「删光全部 `prev_hash` 字段后重写」必须在第 2 行判断链——不是"多写一个用例"，它是这条防线成不成立的分水岭。

**API 形状的一条硬约束**（原因见 §3.4，必须在 U2 定死，否则 U3/U5 接线时返工）：`AuditRecorder` 的对外 API **必须是两段式**——写 SQLite 与 append JSONL 是两次可分别调用的动作，不得打包在一个 `record()` 里同步完成。tasks 2.8 写的"先 SQLite 后 JSONL"是**顺序**要求，两段式满足它；打包成一次调用则会在事务回滚时产生"JSONL 有、SQLite 无"这个 design D1 明令更糟的偏差方向。

### U3 · 留痕接线（第 3 章）

**合并后铁律 3、4 从"钩子留着"变成真实生效。**

**一处必须纠正 tasks.md 的地方**：3.3 写的是「生产装配处（`create_app()`）注入 `RecorderAuditHook`」。**实际构造 `LLMGateway` 的不是 `create_app()`，是 `app/main.py:18` 的 `_gateway_factory()`**（`create_app` 只接收一个 `gateway_factory: Callable`，自己不 new gateway，见 `app/web/server.py:47,59,210`）。

这不是措辞问题，它直接决定并行性：**在 `app/main.py` 注入，U3 完全不碰 `app/web/server.py`，于是可与 M1 的 B、D 并行**；若真去改 `create_app()` 的签名，立刻与 M1 那两个单元串行（见 §3.2）。**U3 的注入点写死在 `app/main.py:_gateway_factory()`，不改 `create_app` 签名。** 回滚 = 换回一行，与 design 迁移计划第 3 步一致。

**顺带要处置的一条跨变更技术债**：M1 的 1.7 已登记「`job_profile.turn_started_at` / `llm_latency_ms` 两列在本包的 `analysis_run` 落地后删除」。**U3 合并即满足该触发条件**。U3 的范围**不含删列**（改 `.51` 现网库的表结构属生产决定，不可代），但 U3 必须在 `07-开发环境现状与优化待办.md` 里把那条技术债标为"触发条件已满足，删列另开变更"——否则两套时序数据长期并存互相矛盾，正是 M1 1.7 想避免的。

### U4 · `app/outbound` 门禁纯函数（第 4 章）

**性质**：全新目录 + 只读引用 `app/agents/jd_agent.py` 的 `AI_LABEL_TEMPLATE`（4.4 复用现有标识机制，不另写一套）。此时**尚未插入外发路径**，与仓库零写入重叠。

**逻辑上不依赖 U2/U3**：门禁判定是纯函数，`GateDecision` 只是个返回值，留痕发生在 U5。所以 **U2 ∥ U4 成立**，两条分支同时开。

**唯一的开工前置是 §3.5 的总开关默认值**——4.5 与 4.8 都直接建在它上面。

**fail-closed 的守护测试落在这里**，见 §3.3。

### U5 · 队列与图节点接线（第 5 章）

**门禁真正插入外发路径的那一步，也是本批唯一与 M1 硬冲突的单元**（`app/graph/nodes.py`，见 §3.2）。

**依赖（具体到符号）**：
- `app.storage.idempotency.idempotent_effect`（`idempotency.py:20`）——新增的两个 `effect_*` 是它的**使用方**，不改装饰器、不改 `effect_log` 表结构、不改幂等键格式
- `app.graph.nodes.message_business_key`（`nodes.py:201`）——5.3 的 `content_hash` 复用它的做法
- `app.graph.nodes.effect_deliver_message`（`nodes.py:117`）——**只在它前面分流，不改其函数体、不改 `Channel` Protocol**
- U4 的 `compute_outbound_gate` 与 `GateDecision.evidence`；U2 的 `AuditRecorder`

**tasks.md 的依赖行要补一笔**：开头写的是「1 → 2 → 3，1 → 4 → 5」，但 **5.4 的 `effect_record_outbound_audit` 要用 `AuditRecorder`，所以第 5 章依赖的是 {1, 2, 4}，不只 {1, 4}**。第 5 章不依赖第 3 章（`RecorderAuditHook` 是 LLM 网关那侧的适配器，与外发留痕无关）——**这正是 U3 ∥ U5 能成立的原因**。

**合并时 `CANDIDATE_OUTBOUND_ENABLED` 保持关闭**（全拦），观察拦截留痕符合预期后再开，与 design 迁移计划第 4 步一致。

**5.9 是回归条款不是新功能**：岗位画像确认卡片（M1 现有的唯一外发路径）**不经候选人门禁**。U5 最容易出的事故形状是"门禁插得太靠上，把内部通知也拦了"——demo 当场哑掉。这条要有测试。

### U6 · 合规断言、对账与 CI（第 6 章）

**红线被破坏时 CI 直接红。** 依赖 U2（`verify_chain`）、U3（`analysis_run` 有真实数据）、U5（拦截留痕有真实数据）。

**6.7 是本单元真正的价值所在**：故意插入一条 `reason_type='ai_score'` 的拒绝记录、一条白名单外的 `criterion_key`，断言必须失败。没有 6.7 的话，三条断言在空表上全部恒真——**"0 命中"同时兼容"红线守住了"和"断言根本没生效"两种解释**，那不叫验证（这条判据 M1 的 §3.3 已经确立过，同一形状）。

**6.4 的对账查询与 2.4 的 `verify_chain()` 是两条不同的断言，不可互相替代**：`verify_chain()` 只证明"链自身没被改"，证明不了"该留的痕都留了"。两条都要有独立测试。

### U7 · 边界守护与文档（第 7 章）

**性质与前六个不同，不建议整章当一份 TDD plan 跑 `run-build`**（形状与 M1 的 G 同）。建议拆两段：

1. **7.1 / 7.2 走 plan** —— 这两条是往 `.github/workflows/ci.yml` 加机器检查（禁止 `from zhuopin_platform` / 禁止 `sys.path` 指向 OneDrive / 依赖文件 diff 为空），是代码，有测试面
2. **7.3–7.6 按清单执行** —— 运维文档一页（`docs/`）、`06-企业AI转型资产借鉴清单.md` 追加借鉴记录、两条技术债登记（`operator_id` 不可信、JSONL 仅进程内锁）。只改文档不改代码

## 三、拆分必须回答的五件事

### 3.1 两条能力能不能各自独立交付 → **不能。依赖方向单向：ai-decision-audit → outbound-approval-gate**

**判据是 spec 原文，不是推测**。`specs/outbound-approval-gate/spec.md`「外发与拦截动作强制留痕」写着：

> 留痕 MUST 使用与 AI 评分留痕相同的机制，落入同一份可校验的记录中。

门禁的**每一次拦截**都要留痕（fail-closed 误拦的唯一观测手段就是拦截留痕，见 design 风险表第 2 条）。所以：

- **留痕必须先做**：`AuditRecorder`（U2）是门禁接线（U5）的前置。反过来不成立——`ai-decision-audit` 的任何一条 spec 都不需要门禁的任何东西。
- **可以独立交付的只有门禁的纯函数那一半**：U4 的 `compute_outbound_gate` 返回 `GateDecision`，不写库、不发消息，完全不需要 `AuditRecorder` 在场。所以 **U2 ∥ U4 是真并行**，但 U5 必须等 U2。
- **唯一的双向耦合点是 U1**：`pending_approval`（门禁的表）与 `analysis_run` / `criterion_score`（留痕的表）在同一次 `SCHEMA` 变更里。技术上可以拆成两次建表，**不建议**——U1 只有 6 项、改的是同一个 `SCHEMA` 常量，拆开只会制造两次 `db.py` 冲突。

**结论一句话**：两条能力共用 `AuditRecorder`，留痕先于门禁；门禁的判定层可以先于留痕独立开发，接线层不行。

### 3.2 与 `m1-intake-quality-fixes` 剩余单元（B/D/E/F/G）的触碰区重叠

M1 现状：第 1、2、4 章已合并（A、C 已交付），剩 **B**（第 3 章）、**D**（第 6 章）、**E**（第 5 章）、**F**（第 7 章）、**G**（第 8 章），执行顺序 B → D → E → F → G。

**逐文件对照**（判据＝触碰文件是否重叠）：

| 文件 | M1 哪些单元写 | 本包哪些单元写 | 结论 |
|---|---|---|---|
| `app/graph/nodes.py` | **B、D、E、F** | **U5** | ⛔ **U5 与 B/D/E/F 全部串行** |
| `app/web/server.py` | B（3.10）、D（6.5-6.8） | **无**（见下方两条裁定） | ✅ 全并行 |
| `app/config.py` | 无 | U1 | ✅ 全并行 |
| `app/storage/db.py` | 无（M1 1.1 已合并） | U1 | ✅ 全并行 |
| `app/llm/gateway.py` | 无（M1 1.3 已合并） | U3 | ✅ 全并行 |
| `app/main.py` | 无 | U3 | ✅ 全并行 |
| `app/agents/*`、`app/schemas/*`、`index.html`、`graph/state.py` | B、D、E、F | **无** | ✅ 全并行 |
| `app/audit/`、`app/outbound/` | 无 | U2、U4、U5、U6 | ✅ 全新目录 |
| `.github/workflows/ci.yml` | 无 | U6、U7 | ✅ 与 M1 全并行；**U6 与 U7 之间串行**（本来就有依赖） |
| `docs/` | F（7.10）、G（8.6-8.8） | U7（新增一页 + `06`） | ✅ 文件各不相同 |
| `tests/` | G（新回放文件） | U1–U6（各自新文件/各自模块的测试文件） | ⚠️ 见下方 |

**两条把串行变并行的裁定（都写进对应单元的 plan）**：

1. **U3 的注入点在 `app/main.py:_gateway_factory()`，不是 `create_app()`**（理由见 §2.U3）。这一条让 U3 避开 `web/server.py`，与 M1 的 B、D 并行。
2. **U5 不加 approve 的 HTTP 端点**。proposal「Non-goals」已写明不做审批流 UI；5.1 的"查询/放行接口"落成 `app/outbound/queue.py` 的**模块级函数**即可。一旦加 HTTP 端点就要改 `app/web/server.py`，U5 立刻再多背上与 M1 B/D 的冲突。审批端点留给将来的审批 UI 变更。

**`tests/` 的重叠是伪重叠**：`tests/test_graph_nodes.py` 与 `tests/test_transaction_ownership.py` 会被 U5 与 M1 的 B/D/E/F 同时改（都在测 `nodes.py`）——但那已经被 `nodes.py` 这条串行覆盖了，不是额外约束。其余测试文件各自独立。

**能并行的完整清单**：**U1、U2、U3、U4、U6、U7 与 M1 剩余全部单元可并行；只有 U5 必须与 M1 的 B/D/E/F 串行。**

**U5 的排期建议**：U5 的前置是 U1+U2+U4 三个单元，本身就排在中后段；M1 的 B → D → E → F 是四个连续单元。最省事的插空点是 **M1 的 F 合并之后、G 之前**（G 只碰 `tests/` 新文件与 `docs/`，与 U5 零重叠）。若要提前，只能在 M1 两个单元合并的间隙插入，且 U5 开工前必须 rebase 到最新 main。

**发版层面**：U1 的三张新表是 `CREATE TABLE IF NOT EXISTS`（对既有库幂等、无回填），M1 的是 `ALTER TABLE ADD COLUMN`，两批 DDL 互不干扰，可同批推 `.51`。M1 8.3 的备份（含 `-wal` / `-shm`）照做不变。

### 3.3 fail-closed 语义怎么测 → 守护测试落在 **U4**，端到端反证在 **U5**，长期观测在 **U6**

这类"缺字段就拦"的语义最典型的腐化形状不是有人故意改，而是**后来者写一句 `getattr(msg, "requires_confirmation", False)` 当作"合理的默认值"**——一行看起来无害的重构，fail-closed 当场变 fail-open，而且所有现有用例照样全绿（因为现有用例喂的都是字段齐全的消息）。

**三层守护，缺一层就守不住**：

1. **U4 的结构性守护（主防线，4.6 的加强版）**：不能只写六个"各缺一个字段"的用例。必须有一条断言**属性根本不存在**的用例——直接把一个裸对象（连 `message_type` 属性都没有）喂进 `compute_outbound_gate`，断言 `allowed is False`。这条用例是唯一能在 `getattr(..., False)` 式重构下变红的。**并且用参数化把「已登记类型 × {字段缺失, 字段为 None, 字段为空串}」的笛卡尔积铺满**——新增一个消息类型时，参数化会强制作者面对每一种未知取值。
2. **U4 的异常路径（4.3）**：门禁内部抛错按拦截处理。测试要用 mock 让判定过程中途抛异常，断言返回拦截而不是异常穿透——异常穿透到调用方，调用方一个 `except: pass` 就是 fail-open。
3. **U5 的端到端反证（5.6）**：断言 `channel.deliver` **零调用**（用 spy/fake channel 计数），而不是只断言"队列里有一条 pending"。队列有记录 + 消息也发出去了，这两件事可以同时成立。
4. **U6 的长期观测（6.5）**：按 `message_type` 与拦截原因统计，让"某类消息一直在被拦"可被发现。这是 fail-closed 误拦的兜底，不是守护测试，但缺了它误拦只能等业务方投诉。

**写进 U4 的 plan 的 Global Constraints**：`compute_outbound_gate` 内**禁止出现带默认值的属性读取**（`getattr(x, k, <default>)` / `dict.get(k, <default>)`）——取不到就是未知，未知就是拦截，默认值这个概念本身与 fail-closed 互斥。reviewer 判据可以直接 grep。

### 3.4 `verify_chain()` 归 **U2**；JSONL 的不一致窗口 → **不消除，锁死方向**

**归属**：`verify_chain()` 是 `JsonlChainSink` 的自校验方法，与写入侧同生共死（写入侧的 `prev_hash` 计算规则一改，校验规则必须同步改），必须与 2.3 同单元。跨介质对账（SQLite ↔ JSONL 差集）是另一件事，归 U6 的 6.4——它要等两边都有真实数据才有意义。**两者不可互相替代**（见 §2.U6）。

**与铁律 1 的关系，结论如下**：

1. **JSONL 不在事务里，不违反铁律 1。** 铁律 1 约束的是**幂等记录（`effect_log`）与业务写必须同一事务、同一连接、同一个 `BEGIN`**。本包的落法是：`SqliteSink.write` 不自行 `commit`（tasks 2.2 已写），由 `idempotent_effect` 装饰器在 `effect_log` 插入之后统一 `conn.commit()`（`idempotency.py:69-75`）——`analysis_run` / `pending_approval` 的业务写与 `effect_log` 天然同一个 `BEGIN`。✅ 这条不变式在 U5 要有测试（`tests/test_transaction_ownership.py` 已有同形状的用例可仿）。
2. **真正会违反铁律 1 精神的写法是"把 JSONL append 放进 `effect_*` 函数体内"**。那样事务回滚时 SQLite 的行没了、JSONL 的行还在，**镜像里出现一条数据库里查不到的记录**——design D1 明确把这个方向定为更糟的一侧（"JSONL 有、SQLite 无会让审计查不到记录"）。⛔ **禁止在 `effect_*` 函数体内 append JSONL**，写进 U2 与 U5 的 Global Constraints，reviewer 直接查调用点。
3. **结论：不一致窗口接受，但只允许单向。** 允许的偏差是「SQLite 有、JSONL 缺行」（真身完整、镜像缺证据）；禁止反向。落地形态＝ `AuditRecorder` 两段式 API（§2.U2 的硬约束）：写 SQLite 的那段进 `effect_*` 函数体，append JSONL 的那段在 `effect_*` **返回之后**由调用点触发——此时装饰器已 `commit`，事务已落地。**这不需要改 `idempotent_effect` 装饰器**（硬边界之一），因为 append 发生在装饰器之外。
4. **代价与兜底**：调用点可能忘记触发镜像写。兜底是 U6 的 6.4 对账（差集非空即报告）+ U5 的一条测试断言"一次外发/拦截后镜像行数 +1"。补齐仍按 design D1 走链尾 `type=backfill`，**不插回原位**（插回必然断链）。

### 3.5 `CANDIDATE_OUTBOUND_ENABLED` 的默认值 → **不可代，待 Shao Peishen 拍板**

按 CLAUDE.md 决策代理表，「候选人对外通道的开关：一次性邀请链接发放、拒信/邀约对外发送」属**不可代**项。tasks 4.5 现写的是"默认关闭"，但那只回答了常量取值，**没回答口径**。下面三项一并请他拍：

**（一）代码默认值**

| 选项 | 后果 |
|---|---|
| **A. 代码默认关闭，`.env` 显式开启**（tasks 4.5 现有口径） | 新环境、忘配 `.env`、`.51` 重装 → 一律全拦。漏发一封邀约可以补，未审批发出一封拒信不能撤。代价：门禁上线初期会有一段"什么都发不出去"的观察期，需要运维知道去哪儿开 |
| **B. 代码默认关闭 + 开启需本人单次授权并留痕** | 在 A 之上再加一道人的流程。最强，但把"开关"变成需要人在场的动作；`.51` 上重启后若 `.env` 已写开启，实际仍是 A 的行为——除非规定 `.env` 里也不写、每次开启都手工改。需要他确认愿不愿意付这个操作成本 |
| **C. 代码默认开启，只留人工确认单闸** | 失去第二道结构闸。proposal 明确要堵的"approve 路径不查总开关"旁路会回来；spec「第二道结构性总开关」这条 Requirement 实质落空。⛔ 与本包的立意冲突，列在这里只为让选项完整 |

**（二）谁有权开**：按决策代理表，这个开关的开启属不可代项 → 建议明确写成"只有 Shao Peishen 本人可开，运维执行需拿到当次授权并留痕"。请他确认是否就是这个口径。

**（三）配置形态**：spec 要求总开关**每次外发时求值、不得启动时缓存一次**。落成 `.env` 变量则改值需重启（与"每次求值"的实际收益打折），落成可热改的配置文件/环境读取则支持不重启生效。tasks 4.5 已写"支持传 callable"，形态上两种都能承载。请他定：**改这个开关允不允许不重启**。

**在他拍板前**：U4 不开工（4.5、4.8 直接建在这上面）。**U1 不受阻**——U1 只是把配置键位加进 `app/config.py`，默认值先按 tasks 4.5 的「关闭」写，改判只改一行常量。

## 四、跨单元接口约定（各自 plan 的 Global Constraints 里逐条抄进去）

1. **两个配置键在 U1 一次加齐**（审计 JSONL 路径 + `CANDIDATE_OUTBOUND_ENABLED`），U3 与 U4 只读不写 `app/config.py`。否则两个单元共写同一文件，U2 ∥ U4、U3 ∥ U4 的并行全部作废。
2. **`AuditRecorder` 是两段式 API**：写 SQLite（进事务）与 append JSONL（提交后）分开。⛔ 禁止在任何 `effect_*` 函数体内 append JSONL。理由见 §3.4。
3. **U3 的注入点是 `app/main.py:_gateway_factory()`，不改 `create_app()` 签名。** 理由见 §2.U3、§3.2。
4. **U5 不加 HTTP 端点、不改 `effect_deliver_message` 函数体、不改 `Channel` Protocol、不改 `idempotent_effect` 装饰器、不改 `effect_log` 表结构。** 新增的 `effect_*` 是装饰器的使用方。
5. **`analysis_run` 的业务关联列与 rubric 列全部可空**（U1），否则 U3 接线当天打挂 M1 的采集流程。理由见 §2.U1。
6. **`compute_outbound_gate` 内禁止带默认值的属性读取**（U4）。理由见 §3.3。
7. **本包三条硬边界**（全部单元）：不新增 `zhuopin_platform` 依赖、不跨仓库 import、不拷贝参考文件。U7 的 7.1/7.2 把它变成 CI 可查。
8. **每个单元开工前必须 rebase 到最新 main**——本包与 `m1-intake-quality-fixes` 同期在跑，`app/graph/nodes.py` 是两批共同的最热文件。

## 五、推荐执行顺序

```
① U1（第 1 章：三张表 + 两个配置键）        ← 很小，最先合，解锁全部
       ↓
② U2（第 2 章：app/audit）  ∥  U4（第 4 章：app/outbound 纯函数）
       ↓                              ↓
③ U3（第 3 章：留痕接线）    ∥      U5（第 5 章：队列与图节点接线）
   （U3 与 M1 全并行）              （U5 必须与 M1 的 B/D/E/F 串行，
                                     建议插在 M1 的 F 合并之后）
       ↓                              ↓
④ U6（第 6 章：合规断言、对账与 CI）
       ↓
⑤ U7（第 7 章：7.1/7.2 走 plan，7.3-7.6 按清单执行）
```

**与 M1 的合流**：除 U5 外全部可与 M1 剩余单元同时开分支。U5 的插空点见 §3.2。

**开工前的唯一阻塞**：§3.5 的总开关三项口径（不可代，等 Shao Peishen）。U1 可以立刻开工，不等这个决定。

**下一步**（他确认本方案后）：U1 出一份 `spec-to-plan`（CC / 新开 session / main / 不勾 worktree）。U1 合并后，U2 与 U4 各出一份，两份可并行。
