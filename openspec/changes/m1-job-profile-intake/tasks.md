**进度：42/71**（2026-09-04 第 6 章确认断点交付单元合入）

> ## 2026-08-20 对齐现实（执行于 2026-08-25，OP-0820-10）
>
> **改了什么**：本文件第 1-9 章原是**写于选型前的 M1 全量 WBS**，58 项未勾里大量内容
> （Postgres 建库、pgvector、企微自建应用与回调、Postgres checkpointer）与实际走通的
> 实现路径（**SQLite + Web 通道**）已经脱节。按现状这个包永远归不了档，而每次对账都会
> 被它带偏一次。本次把 69 项逐条对着 `app/` 与 `tests/` 的真实代码过了一遍，分四类处理。
>
> **判据**：A 类看**行为是否已由现实路径交付**（不是名字相同）；B 类是行为本身已不再需要；
> C 类是行为仍需要但不属本包；D 类是仍属本包且确实没做。**存疑一律归 D**——错判成 D
> 的代价是多留一条待办，错判成 A 的代价是把没做的事标成做完了。
>
> **四类条数**（含 3 条拆分新增项，69 → 72）：
>
> | 类别 | 条数 | 含义 |
> |---|---|---|
> | 已勾（0.x 内网 Demo） | 11 | 本次未改动 |
> | **A 已用其他方式完成** | **20** | 本次勾上，条目后注明实际落在哪 |
> | **B 被选型变更作废** | **1** | 原位划掉留墓碑 |
> | **C 移出到别的变更包** | **12** | 划出去，明细见文末「已移出，另开变更包」 |
> | **D 仍属本包、仍未做** | **28** | 保持未勾 |
>
> **拆分说明**（原条目一句话里混了已完成与未完成两件事，不拆就只能整条误判）：
> `1.2` → `1.2` + `1.2b`；`1.5` → `1.5` + `1.5b`；`1.6` → `1.6` + `1.6b`。
>
> **归档还差什么** = 上表的 28 条 D。按性质归拢成 5 组：
>
> 1. **确认断点没做完**（6.1 / 6.3 / 6.4 / 6.5 / 6.6 / 6.7 / 6.8 / 6.9，8 条）——这是最大的一块。
>    其中 **6.1 是现网真实缺陷**：前端 `index.html` 的 `confirmation_prompt` 分支只渲染
>    「画像已收集完整，请确认。」加一行未指定字段，**从不渲染画像本身**（payload 里
>    `profile_patch_accumulated` 有值但没有任何代码读它）——业务经理是在看不见画像的情况下
>    点的「确认」。
> 2. **人工决策留痕缺失**（1.4 / 9.3，2 条；6.4 同属这一组，已计入第 1 组不重复计）——
>    `human_review` 表与写入路径都不存在。合规相关，不宜跟着包一起归档掉。
> 3. **JD 侧的溯源与标识保护**（7.3 / 7.4 / 7.5 / 7.7，4 条）。
> 4. **Web 界面只有单页会话**（8.1 / 8.2 / 8.3 / 8.4，4 条）——无岗位列表、无版本历史页、
>    无 `needs_manual` 队列、无一键复制。
> 5. **其余散项**（1.2b / 1.6b / 2.5 / 4.3 / 4.4 / 5.3 / 5.6 / 5.8 / 5.9 / 9.1，10 条）。
>
> 8 + 2 + 4 + 4 + 10 = 28 ✓
>
> ~~**两条待人判定**见文末「⏳ 待 Shao Peishen 判定的归类」，已按预案先归 D。~~
> **✅ 2026-08-26 Shao Peishen 已判定，两条均改判 A 类并回勾**，见文末「✅ 已判定的归类」。
> 7.4 括号里"元数据记录模型"半条随判定移出到 `ai-audit-trail-and-outbound-gate`。
>
> ⚠️ 本次**只理 WBS**，未动 `specs/` 与 `design.md`——那两份是行为契约。
>
> ⚠️ 归档顺序：`m1-intake-quality-fixes` 的 tasks 8.9 要求
> **本包先归档、`m1-intake-quality-fixes` 后归档**。

---

## 0. 内网 Demo（3-5 天，优先交付）

目标：HR 与业务经理能在浏览器里输入一句话需求，拿到岗位画像和 JD。**不碰候选人个人信息，合规风险为零，可立刻上内网。**

这不是一次性 demo —— 它就是本变更的 `job-profile-intake` + `job-description` 两个 capability，只是先跳过企微通道与 Postgres。架构（LangGraph 图结构、`compute_*`/`effect_*` 节点划分、通道抽象）从第一天就按正式版做，后面换基础设施只改配置。

- [x] 0.1 **模型对比实测**（与 2.1 是同一件事，提前到这里做，阻塞后续全部 LLM 工作）—— DeepSeek 单供应商实测完成（含 flash vs pro 对比，见 `docs/m1-model-comparison.md`）；2026-08-11 决策者明确拍板不等 doubao/qwen 补测账号，M1 demo 阶段直接定 DeepSeek——不是数据缺失下的权宜之计，是显式决策，故勾选
- [x] 0.2 画像 Pydantic Schema（同 5.1，正式版直接沿用）
- [x] 0.3 LLM 网关最小版：单供应商、版本锁定、`temperature=0`、schema 校验 + 重试
- [x] 0.4 LangGraph 图骨架，节点命名区分 `compute_*` / `effect_*`；checkpointer 先用 SqliteSaver
- [x] 0.5 **通道抽象层**：定义发起/追问/确认三个动作的通道无关接口，Web 是第一个实现
- [x] 0.6 需求解析 Agent + ECU 术语追问规则（同 5.2 / 5.4）
- [x] 0.7 JD 生成 + **AI 生成内容标识** + 歧视性表述拦截（同 7.x，标识与拦截不可省）
- [x] 0.8 单页 Web 界面：输入 → 追问对话 → 画像确认 → JD 展示与复制
- [x] 0.9 页面显著位置标注「演示环境，不进入正式招聘流程」
- [x] 0.10 Docker 化 + 部署到 51 服务器（部署方式见 `04-部署与门户挂载.md`）—— 已改为 Windows venv + 计划任务方案（04-部署与门户挂载.md §7 已作废 Docker）。2026-08-11 完成实际同步：`sync-to-server.sh` 推代码 → 计划任务重启 → 真实浏览器/curl 端到端验证通过（路径前缀、演示环境横幅、真实 LLM 调用、ECU 领域追问全部在 192.168.100.51:8095 上确认）。过程中顺带修了两个真实 bug：`sync-to-server.sh` 黑名单遍历中文文件名触发 scp 编码崩溃（改成白名单）、服务器 `.env` 非 UTF-8 编码导致重启即崩溃（见 `docs/deploy-51-server.md` 故障排查）
- [x] 0.11 请 3 位业务经理各跑一个真实岗位，收集反馈 —— 3/3 完成：姚祖怡（供应链总监）+ 2 位业务经理（底层软件工程师岗、非标产品采购员岗，Excel 表回复）。反馈汇总与真实数据核对见 `docs/m1-demo-pilot-feedback.md`；暴露的真实问题（"不知道/你有什么建议"式回答无兜底、`unspecified_fields` 提示不够显著等）已记录，待排进 1.x 之前的技术债优先级

**Demo 阶段可以暂缺、但正式接入前必须补上的**：
`analysis_run` 审计留痕（合规刚需）、Postgres checkpointer、企微通道、多轮修改历史、`needs_manual` 队列。
**在 0.x 完成后、进入 1.x 之前，把这份欠账列进技术债并排期**，不要让 demo 悄悄变成正式版。

> **2026-08-20 回看这段欠账的实际去向**：`analysis_run` → 已移出到
> `ai-audit-trail-and-outbound-gate`（本文件 1.3 / 2.6）；Postgres checkpointer →
> 已由 SqliteSaver 交付等价行为，Postgres 本身推迟到 M2 迁移（1.1 / 1.6）；
> 企微通道 → 已移出到阶段二（3.1-3.6）；多轮修改历史与 `needs_manual` 队列
> → **仍是欠账，且仍在本包**（6.5 / 6.6 / 8.4，D 类）。这段话当时担心的
> "demo 悄悄变成正式版"，在这三条上确实发生了。

---

## 1. 地基：数据层

- ~~[ ] 1.1 Postgres 建库，启用 pgvector 扩展（M1 不用，但一次建好省得后面停机）~~ ⚰️ **已作废**，原因：M1 选型改为 SQLite（`app/storage/db.py`），本条要交付的行为在 M1 一次都没被用到。Postgres + pgvector 仍在技术栈里（`CLAUDE.md` 技术栈一节），但届时是随 **M2 迁移变更包**按 M2 的表结构重新立项，不是照着这条做——保留它只会让人以为 M1 欠了一笔其实不存在的债
- [x] 1.2 建表 `job`、`job_profile`（含 version/status） → **已用 SQLite 实现**，见 `app/storage/db.py` 的 `SCHEMA`：`job`（id/title/department/status/created_at）、`job_profile`（含 `version` 与 `status`，另有 `unspecified_fields` 等 6 个后加列，由 `apply_column_migrations` 幂等补齐）。测试 `tests/test_db.py` / `tests/test_db_migration.py`
- [ ] 1.2b 建表 `hard_requirement`（**2026-08-20 从原 1.2 拆出**）—— 该表至今不存在，且它是 5.8「硬门槛规则提取」的载体，两条一起做才有意义。拆出来是因为原 1.2 一句话里三张表两张已建、一张没建，整条勾或整条不勾都是错的
- [ ] 1.3 建表 `analysis_run`（模型标识/版本/prompt版本/temperature/输入哈希/原始响应/token用量）
      ⤷ **已移出**到 `ai-audit-trail-and-outbound-gate`，见文末「已移出」清单
- [x] 1.4 建表 `human_review`（决策人/决策类型/时间/关联画像版本，预留 batch_id 供 M2 批量确认用） → **已实现**，见 `app/storage/db.py` 的 `SCHEMA`：`human_review(id/job_id/profile_version/decision_type/reviewer/feedback/batch_id/decided_at)`，两条 CHECK（decision_type 三值白名单、reviewer 非空白）+ 唯一索引 `idx_human_review_decision`。新表走 CREATE TABLE IF NOT EXISTS，⛔ 未进 `_ADDED_COLUMNS`。测试 `tests/test_human_review_schema.py`
- [x] 1.5 建表 `effect_log`（幂等键唯一索引） → **已用 SQLite 实现**，见 `app/storage/db.py`：`effect_log(effect_key PRIMARY KEY, thread_id, node_name, business_key, applied_at)` + `CREATE UNIQUE INDEX idx_effect_log_key`。幂等键格式与写入路径见 4.2
- [ ] 1.5b 建表 `wecom_callback`（回调落库）（**2026-08-20 从原 1.5 拆出**）
      ⤷ **已移出**到阶段二·企微通道，见文末「已移出」清单
- [x] 1.6 接入 LangGraph checkpointer → **已用 SqliteSaver 实现**，见 `app/graph/build.py:124-126`：checkpointer 拿一个**指向同一个数据库文件但完全独立**的连接（方向 A，修 `docs/findings/2026-08-13-sqlite-事务归属冲突.md` 的事务归属冲突），checkpoint 按 `thread_id` 分区落盘。原文写的「Postgres checkpointer」推迟到 M2 迁移（同 1.1）
- [ ] 1.6b 跨进程重启恢复的自动化验证（**2026-08-20 从原 1.6 的"验证进程重启后能按 thread_id 恢复"拆出**）—— 现有 `tests/test_graph_idempotency.py::test_graph_replay_from_scratch_does_not_duplicate_effects` 验的是**同进程内同 thread_id 重复 invoke** 不重复产生副作用，**不是**进程重启后按 thread_id 续上。checkpoint 确实落在 SQLite 文件里、结构上重启可恢复，但这件事至今没有任何测试断言过。6.3 的「挂起状态重启后可恢复」指向的是同一个缺口
      ⚠️ **2026-09-04 补**：这条要的覆盖实际已经落地，见 `tests/test_suspend_recovery.py::test_a_brand_new_process_recovers_the_suspended_thread`（真开一个新操作系统进程，只给数据库路径，断言按 thread_id 读回 checkpoint）。**保持未勾，等 Shao Peishen 确认**——本次交付指令逐条列了要回勾的九条（6.1/6.3/6.4/6.5/6.6/6.7/6.9 + 1.4 + 9.3），未列出 1.6b，回勾与否不由代理人代拍
- [ ] 1.7 写 checkpoint 清理任务（按流程完成时间归档），M1 不启用但代码就位
      ⤷ **已移出**到 M2 Postgres 迁移，见文末「已移出」清单

## 2. 地基：LLM 网关

- [x] 2.1 **模型对比实测**（阻塞后续所有 LLM 相关任务） → **与 0.1 是同一件事，已完成**，产出 `docs/m1-model-comparison.md`（DeepSeek flash vs pro 的 `json_schema` 遵循度、抽取准确率、延迟、单价对比）。⚠️ 原文要求「至少三家供应商」，实际只做了 DeepSeek 一家——这是 2026-08-11 决策者的**显式拍板**（不等 doubao/qwen 补测账号），不是漏做；同一件事在 0.1 已按该决策勾选，这里保持一致
- [x] 2.2 网关薄封装：统一调用入口，强制 `temperature=0`，模型版本显式锁定（禁止 `latest` 类别名） → **已实现**，见 `app/llm/gateway.py`：`LLMGateway.__init__` 对 `latest` / `*:latest` / `*-latest` 直接 `raise ValueError`（:172-173），`_call_model` 硬编码 `temperature=0`（:322）。铁律 5 的"取回响应实际 `model` 字段"也已落地（:227 `response_model` + :233 `system_fingerprint`）。测试 `tests/test_llm_gateway.py`
- [ ] 2.3 双供应商切换与降级：主供应商失败自动切备用，切换事件记入 `analysis_run`
      ⤷ **已移出**到「多供应商接入」，见文末「已移出」清单
- [x] 2.4 结构化输出：`json_schema` 优先、`json_object` + Pydantic 本地校验降级，两条路径都实现 → **已实现**，见 `app/llm/gateway.py:273-325` 的 `_call_model`：`_to_strict_json_schema` 把 pydantic schema 转成 strict 形态走 `json_schema`；`_has_free_form_object` 命中自由 dict 字段（如 `_IntakeTurnSchema.profile_patch`）时降级为 `json_object` 并把 schema 写进 system prompt。两条路径末端都过 `schema.model_validate`（:258）
- [ ] 2.5 校验失败重试至多 2 次，仍失败转 `needs_manual`，**不产出半成品**
      ⚠️ 重试与"不产出半成品"两半**已实现**（`max_retries=2`、`attempts = max_retries + 1`、失败抛 `SchemaExtractionFailed` 而不返回半成品），但**"转 `needs_manual`" 完全没有实现**——`JobStatus.NEEDS_MANUAL` 只是个枚举值，没有任何代码写它，也没有队列承接（见 8.4）。保持未勾
- [ ] 2.6 每次调用自动写 `analysis_run`，无需业务代码显式调用
      ⤷ **已移出**到 `ai-audit-trail-and-outbound-gate`，见文末「已移出」清单

## 3. 地基：企业微信通道

> **整章已移出到阶段二**，逐条理由见文末「已移出，另开变更包」。M1 走 Web 通道，
> `Channel` 抽象（`app/channels/base.py`）已就位，将来加 `WeComChannel` 不用改 graph 节点。

- [ ] 3.1 自建应用申请与配置（外部依赖，尽早启动） ⤷ **已移出**
- [ ] 3.2 回调接口：签名校验 + 落 `wecom_callback` + 5 秒内返回 200 ⤷ **已移出**
- [ ] 3.3 回调去重：同一回调重复投递只处理一次 ⤷ **已移出**
- [ ] 3.4 后台任务消费 `wecom_callback`，异步唤醒对应 LangGraph thread ⤷ **已移出**
- [ ] 3.5 `template_card` 交互卡片发送封装（确认/修改/放弃三按钮） ⤷ **已移出**
- [ ] 3.6 普通文本消息发送封装（追问对话用） ⤷ **已移出**

## 4. 地基：编排骨架与幂等约定

- [x] 4.1 LangGraph 图骨架，节点命名区分 `compute_*` / `effect_*` → **已实现**（同 0.4），见 `app/graph/build.py` 的 `build_intake_graph`：`compute_intake_turn` → `effect_persist_draft` → `effect_deliver_message` → END；节点函数在 `app/graph/nodes.py`，命名严格区分两类。另有两个 effect 节点走 HTTP 直调（`effect_confirm_profile` / `effect_generate_and_persist_jd`）
- [x] 4.2 幂等装饰器：`effect_*` 节点执行前查 `effect_log`，命中即跳过；幂等键 `{thread_id}:{node_name}:{business_key}` → **已实现**，见 `app/storage/idempotency.py` 的 `idempotent_effect`：幂等键格式**逐字一致**（`f"{thread_id}:{node_name}:{business_key}"`），命中即返回 None 跳过；业务写与 `effect_log` 行由装饰器**在同一个事务里一次提交**（铁律 1），函数体抛异常时先 rollback 再上抛。测试 `tests/test_idempotency.py` / `tests/test_transaction_ownership.py`
- [x] 4.3 `interrupt()` 挂起与 `Command(resume=...)` 恢复的最小闭环打通 → **已用其他方式实现（2026-08-26 Shao Peishen 判定行为等价）**：本图**刻意没有使用 `interrupt()`**（`tests/test_graph_idempotency.py:104` 注释原文："本图没有用 interrupt"）。Web 通道下"挂起等人"由「HTTP 请求/响应 + 状态落 SQLite + 独立 `/confirm` 端点」达成，本条要的"最小闭环"目的已达成。
      ⚠️ **企微通道那批要重新审视这条**：消息异步推送、用户可能几小时后才回，那时才需要"图挂起在节点 → 回调到达 → `Command(resume=...)` 续上"。判定为等价的是**Web 通道下**的闭环，不等于企微通道也不需要 `interrupt()`
- [ ] 4.4 **幂等专项测试**：对每个 `effect_*` 节点强制中断并恢复，断言副作用只发生一次
      ⚠️ 4 个 effect 节点里**覆盖了 3 个**（`effect_persist_draft` / `effect_deliver_message` / `effect_confirm_profile`，见 `tests/test_graph_idempotency.py`），**`effect_generate_and_persist_jd` 一条都没有**。而它恰恰是唯一一个在重放时会**重复触发真实付费 LLM 调用**的节点（`app/graph/nodes.py:149-198`），漏的正好是代价最大的那个。保持未勾
- [x] 4.5 写入 `AGENTS.md` / `CLAUDE.md`：副作用节点铁律，让后续变更自动继承 → **已用 `CLAUDE.md` 实现**，见「工程铁律」第 1、2 条（副作用节点独占 + 幂等键格式 + 幂等记录与业务写同事务 + `compute_*`/`effect_*` 命名）。本仓库不使用 `AGENTS.md` 格式；`CLAUDE.md` 每会话自动加载，本条"让后续变更自动继承"的目的已达成

## 5. 需求解析 Agent（capability: job-profile-intake）

- [x] 5.1 定义岗位画像 Pydantic Schema：通用字段 + ECU 特化字段（autosar_experience / functional_safety / mcu_family / diag_stack / sop_projects / toolchain） → **已实现**（同 0.2），见 `app/schemas/job_profile.py`：6 个 ECU 特化字段**逐个对上**，另有 `AutosarLayer` / `FunctionalSafetyLevel` 枚举与 `SkillItem` / `SopProject` 子模型。测试 `tests/test_job_profile_schema.py`
- [x] 5.2 ECU 领域知识库：术语表与追问触发规则（"嵌入式开发"→ 追问 MCU 平台族/AUTOSAR/功能安全） → **已实现**（同 0.6），见 `app/agents/ecu_knowledge.py`（`FOLLOWUP_RULES` + `match_ambiguous_terms`）与 `app/agents/intake_agent.py:178-194` 的 `suggested_followups`（只看 `role="user"` 轮次，避免规则自我触发）。测试 `tests/test_ecu_knowledge.py`。⚠️ 词条只有 4 条、且全是 ECU 侧无采购侧——**扩充词条不属本条**，已在 `m1-intake-quality-fixes` 3.1/3.2 立项
- [ ] 5.3 需求识别：区分"是用人需求"与"无关消息"，后者回引导语且不建岗位记录
      ⚠️ 前半**已实现**（`_IntakeTurnSchema.is_job_related` + `_guidance_question()`，`app/agents/intake_agent.py:262-284`），但**"不建岗位记录"被违反**：`app/web/server.py:127-131` 的 `create_job` 在跑这一轮之前就 `INSERT INTO job`，`effect_persist_draft` 也不看 `is_job_related` 照写 `job_profile` 草案行。所以随便发一句无关消息就会在库里留下一个岗位。保持未勾
- [x] 5.4 多轮追问 Agent（纯函数）：每轮至多 3 个问题，上限 5 轮 → **已实现**（同 0.6），见 `app/agents/intake_agent.py`：`MAX_QUESTIONS_PER_ROUND = 3`、`MAX_ROUNDS = 5`，`run_intake_turn` 是纯函数（只调 gateway，不写库不发消息），截断在 :323-325。测试 `tests/test_intake_agent.py`。⚠️ `m1-intake-quality-fixes` 3.10 会把预算口径改成「有产出轮」+ `MAX_TOTAL_ROUNDS`，那是对本条的**改进**，不影响本条当前已达成
- [x] 5.5 追问超限降级：用"未指定"填充并在确认卡片显式列出缺口 → **已实现**：`at_round_limit`/`stuck` 触发 `give_up` 并透出 `unspecified_fields`（`app/agents/intake_agent.py:322-341`）；`app/graph/build.py:61-67` 把它放进 `confirmation_prompt` payload；前端 `index.html:163-167` 渲染「以下字段未指定：…」；`app/web/server.py:170-174` 在确认时用 `"未指定"` 填充必填字段。⚠️ 这个提示**不够显著**（对话流里的一行文字），显著化属 `m1-intake-quality-fixes` 6.6，不是本条
- [ ] 5.6 业务经理超时：3 个工作日提醒一次，再 3 天置 `abandoned` 并保留已采集内容
      ⚠️ 完全未实现，且本仓库**没有任何定时/后台任务基础设施**（Web 通道是同步请求/响应）。`JobStatus.ABANDONED` 只是枚举值，无写入路径。保持未勾
- [x] 5.7 画像产出与 Schema 校验接线 → **已实现**，见 `app/web/server.py:161-204`：确认时先 `JobProfile.model_validate` 再落 approved（顺序刻意——校验失败时画像不能已被标成 approved，否则既拿不到 JD 又回不去追问）；失败返回 422 并逐字段说明「期望什么、当前值是什么」，不让 `ValidationError` 裸奔成 500。测试 `tests/test_web_api.py`
- [ ] 5.8 硬门槛规则提取：字段/运算符/值/是否阻断 + 一句人类可读说明
      ⚠️ 完全未实现——没有 `hard_requirement` 表（见 1.2b）、没有运算符/阻断建模。画像里只有 `core_skills[].required`（布尔）与 `soft_skill_keywords`，表达不了"运算符 + 值 + 是否阻断"。保持未勾
- [ ] 5.9 **主观描述拦截**：断言"沟通能力强"这类表述不得进入 `hard_requirement`，只留在软技能关键词
      ⚠️ 保持未勾，且**不能靠 5.2 的 prompt 约束替代**：`hard_requirement` 表根本不存在（1.2b/5.8），"不得进入"这条断言目前**无处可断**。现状只有 `SYSTEM_PROMPT` 里一句"不能因为用户说你决定就自己写进 profile_patch"和 `tests/test_intake_agent.py:378` 那个**只断言 prompt 文本里含某几个关键词**的测试——那验的是提示词写了什么，不是行为。随 5.8 一起做

## 6. 确认断点（capability: job-profile-approval）

- [x] 6.1 画像摘要渲染（卡片可读，不堆字段） → **已实现**，`app/schemas/job_profile.py:summarize_profile()` 产出中文标签值对、`app/graph/build.py:_deliver_node` 把它放进 `profile_summary`、`app/web/static/index.html:renderProfileSummary()` 渲染。⛔ payload 里没有英文字段名，界面上就不可能出现英文 snake_case。测试 `tests/test_profile_summary.py` + `tests/test_approval_branches.py`
- [x] 6.2 `effect_send_approval_card` 节点（独占、幂等） → **已用 `effect_deliver_message` 实现**：`app/graph/build.py:58-97` 的 `_deliver_node` 在 `is_complete` 时构造 `type="confirmation_prompt"` 的 `OutboundMessage` 走同一个节点投递。行为等价成立——**独占一个节点** ✅、**带幂等键** ✅（`business_key = f"{round_count}:{内容哈希}"`，前缀带轮次是为了不把"两轮问题恰好相同"的合法投递误杀成重放）。"卡片"是企微 `template_card` 的形态，Web 通道下的等价物就是这条消息
- [x] 6.3 挂起状态持久化，验证进程重启后可恢复 → **已实现**，见 `tests/test_suspend_recovery.py`：跨进程恢复 + 7 天时间推进后仍能确认 + 幂等键不因时间流逝而过期
- [x] 6.4 确认分支：冻结画像、写 version、记 `human_review`、流转下游 → **已实现**，`effect_confirm_profile` 在**同一个事务**里同时完成 status='approved'、job.status 同步与 `human_review` 留痕（工程铁律 1）。恒等不变式测试 `tests/test_approval_branches.py::test_human_review_row_count_equals_effect_log_count_per_thread`
- [x] 6.5 修改分支：基于原画像 + 修改意见重新生成，保留每一版草案 → **已实现**，`effect_request_revision`（独立 effect 节点）+ `POST /api/jobs/{id}/revise`；每一版草案保留（新 version，⛔ 不覆盖）；上限 5 次由 `revision_count()` 从 human_review 现算，⛔ 无计数列
- [x] 6.6 修改次数上限 5 次，超限提示转人工编辑（随 6.5） → **已实现**，`effect_request_revision`（独立 effect 节点）+ `POST /api/jobs/{id}/revise`；每一版草案保留（新 version，⛔ 不覆盖）；上限 5 次由 `revision_count()` 从 human_review 现算，⛔ 无计数列
- [x] 6.7 放弃分支：置 `abandoned`，保留内容 → **已实现**，`effect_abandon_profile` + `POST /api/jobs/{id}/abandon`：置 abandoned、内容一字不改，且 `/reply` `/confirm` `/revise` 三个入口都拒绝已放弃的岗位
- [ ] 6.8 挂起提醒：第 1 天、第 3 天各一次（无定时基础设施，同 5.6）
      ⏸ **留步：等定时基础设施（与 5.6 同源）。** 本系统至今没有任何定时/调度
      基础设施——发提醒是一个有副作用的动作，必须落在 effect_* 节点里，由一个
      真正的调度器按时触发。⛔ 不用 sleep 循环或后台线程充数：那种东西进程一
      重启就没了，而这条 spec 要的恰恰是"挂起 7 天不丢"。判定口径（第 1 天、
      第 3 天各一次）已写在 spec 的「流程长时间挂起」Scenario 里，调度器落地时
      直接照抄。已登记 `docs/tech-debt.md` TD-11。
- [x] 6.9 **7 天挂起测试**：模拟时间推进，断言挂起状态不丢失且能正常恢复（随 6.3 / 1.6b） → **已实现**，见 `tests/test_suspend_recovery.py`：跨进程恢复 + 7 天时间推进后仍能确认 + 幂等键不因时间流逝而过期

### 6.x 落地偏离登记

> 交付执行期间与计划出现的偏离、需要终审 triage 的次要缺口、以及裁决记录，逐条
> 摘自 `.superpowers/sdd/2026-09-04-m1-job-profile-intake-unit6-approval-checkpoint/progress.md`。
> 本节是「哪里没按计划走、为什么」的记录，⛔ 不做概括性总结抹平细节。

- Task 2 minor（deferred）：`tests/test_profile_summary.py` 有一个未用的 `**_` 形参（plan 逐字给定）
- Task 2 minor（deferred）：`task-2-report.md` 写"143 行"，diff stat 是 141 行——报告笔误，无代码影响
- Task 2 ⚠️ 已由控制器解决：`FIELD_LABELS` 顺序与断言一致，由 GREEN 实跑证明，非缺口
- Task 3 计划偏离（已由 reviewer 独立复核判为「正确且必要」）：brief 的 `JD_RESPONSE` fixture 形状 `{"jd_text","discriminatory_hits"}` 与真实 `_JDBodySchema`（只有 `body`）不符，会让 3 条测试耗尽脚本化 LLM 队列并 500。已改为 `{"body": "..."}`，与仓库既有 JD 测试同形。仅测试 fixture，未改生产代码
- Task 4 minor（deferred）：`test_revise_keeps_every_draft_version` 只断言 `(version, status)` 元组，未复核 v1 的 `profile_json` 内容未变。结构上不可能被违反（`job_profile.id` 是 PK，`effect_persist_draft` 只做纯 INSERT，撞了会报 PK 错而非静默覆盖）。留给终审 triage
- Task 4 裁决（控制器，无人可问）：reviewer 报 Important —— `/reply` 只有 abandoned 守卫、没有 approved 守卫，画像冻结后仍可经 `/reply` 复活出新草案版本，与 spec 6.4「确认后冻结」冲突。reviewer 标为 plan-mandated（Task 4 brief 只给了 abandoned）。裁决：**是真缺口，不 park**。依据＝plan 自己的 File Structure 行逐字写着「`/reply` `/confirm` `/revise` 加终态守卫」，approved 属终态。已并入 Task 5 一起做（Task 5 本来就在改这三个入口的守卫），登记为范围追加偏离
- Task 5 范围追加已交付：`/reply` 补 approved 终态守卫（409 + `_APPROVED_DETAIL`，与 revise 同形），测试 `test_reply_rejects_an_approved_job_and_creates_no_new_draft` 断言拒绝后 `job_profile` 版本行未变
- Task 5 minor（deferred）：`/abandon` 没有「已 approved 不得放弃」的守卫，可把 approved 翻成 abandoned。不在 plan 的 File Structure 授权范围（只点名 `/reply` `/confirm` `/revise`），留终审 triage
- Task 5 minor（deferred）：`/abandon` 在 job 与 profile 都不存在时返回 404 "no profile draft yet" 而非 "job not found"，与其他路由文案不一致（brief Step 3b 逐字给定）
- Task 6 6.1 渲染断言实际落点 = `tests/test_approval_branches.py:78-99`（Task 3 建）：`assert "岗位名称" in labels and "核心技能" in labels` / `assert {"label": "招聘人数", "value": "2"} in summary`。Task 6 自己的前端测试是字符串结构断言（本仓库无 jsdom，既有前端测试一律此形态）
- Task 6 偏离（已复核为正确）：brief Step 3b 的注释里含 `innerHTML` 字面量，与 brief Step 1「`index.html` 全文不得出现 `innerHTML`」自相矛盾。改写注释措辞，逻辑一字未动；`index.html` 实际 `innerHTML` 用法为 0 处
- Task 6 裁决（控制器）：「前端从 `profile_patch_accumulated` 取」的约束，其约束力条款是「不另加接口」。Task 3 已把 `profile_summary` 放进同一个 `confirmation_prompt` payload，读它未新增任何接口 —— 遵守约束，非违反
- Task 6 minor（deferred）：新增的 revise/abandon fetch 无 try/catch 网络失败处理，与既有 `doConfirm` 同形，非本次引入
- Task 7 变异验证已做并被 reviewer 独立复核 —— 把 `app/graph/build.py` 的 checkpointer 连接指向 `tempfile.mktemp()` 的一次性路径（而非真实 `db_path`），`test_a_brand_new_process_recovers_the_suspended_thread` 转红，报错正是 `AssertionError`：新进程按 thread_id 读不回 checkpoint。已还原，committed 代码里 `checkpointer_conn = get_connection(db_path)`，diff 只含新测试文件
- Task 7 重启是真重启 —— `subprocess.run([sys.executable, probe, db_path, job_id])` 真开新操作系统进程，探针里 LLM 客户端是一调用就抛的 `_ExplodingClient`，排除了「恢复出来的状态其实是现编的」
- Task 7 TDD 缺口（已登记）：4 条测试首跑即绿，未经历 RED 阶段。以变异验证补偿；reviewer 逐条复核 4 条均非空断言
- Task 7 裁决（控制器）：reviewer 报 Important（plan-mandated）—— `test_revise_and_abandon_also_survive_a_restart` 的 docstring 写「三个分支」，函数体（brief 逐字给定）只跑了 abandon，revise 分支的重启存活实际未测。**不 park，但也不追加覆盖**（超出本单元范围）：改为把 docstring 改成与实测一致，并入 Task 9 一起做（已在本次交付完成）。理由＝docstring 过度宣称正是「把没做的事标成做完了」，必须消除
- Task 8 parked：断言四的豁免线用 `job_profile.created_at`（草案创建时间）与 `HUMAN_REVIEW_ENFORCED_FROM` 比，而 `effect_confirm_profile` / `effect_abandon_profile` 是就地 UPDATE status，`created_at` 决策时不推进。后果：部署时刻已存在的所有 `job_profile` 行（含在途未决草案）永久落在豁免侧，日后被确认/放弃却漏写 `human_review` 时，断言四看不见。ruling：**真实且已确认，但不在本轮改**——① brief 逐字给定了这套比法，属 plan-mandated；② 它是合规红线的机器判据，改它的语义属 CLAUDE.md「不可代」范围（合规红线的任何变更或单次例外一律等 Shao Peishen）；③ 本单元没有任何下游依赖它。已列入交付报告的待裁决项
- Task 8 偏离（已复核为正确且必要）：brief 的字面代码片段有真实 `SyntaxError`（f-string 里嵌套未转义双引号），改用中文弯引号，语义不变
- Task 8 偏离（已复核为「非削弱」）：`test_audit_assertion_effectiveness.py` 的 `[False,False,False]` 改 `[False,False,False,True]`，`all(r.violations for r in results)` 改为 `if not r.ok` 限定。reviewer 判定：仍强制既有三条必须被违反，且仍要求每条失败结果都带 violations；原写法在加入第 4 条「本场景下合法不违反」的断言后机械上不可能成立

## 7. JD 生成（capability: job-description）

- [x] 7.1 JD 生成 Agent（纯函数），输入为冻结画像 → **已实现**（同 0.7），见 `app/agents/jd_agent.py` 的 `generate_jd`：纯函数（只调 gateway，不写库），入参就是已通过 `JobProfile.model_validate` 的画像对象。副作用（落库）单独放在 `effect_generate_and_persist_jd` 里。测试 `tests/test_jd_agent.py`
- [x] 7.2 画像未冻结时拒绝生成 → **已实现**，见 `app/web/server.py:152-154`：最新消息不是 `confirmation_prompt` 时返回 409「画像还在追问中，未到可确认状态」；且 JD **只在 `/confirm` 这一条路径上生成**，而该路径里 `effect_confirm_profile`（冻结）严格排在 `effect_generate_and_persist_jd`（生成）之前。测试 `tests/test_web_api.py`
- [ ] 7.3 **溯源校验**：断言文案中的技术要求都能追溯到画像字段，不得凭空出现
      ⚠️ 目前**只有 prompt 里一句要求**（`JD_SYSTEM_PROMPT`："文案中出现的技术要求必须能追溯到画像字段，不得凭空新增"），**没有任何校验代码、没有任何测试**——"提示词说了、模型没做"正是 `m1-intake-quality-fixes` 记录过的事故模式。
      ⚠️ **不要与 `m1-intake-quality-fixes` 第 7 章混为一谈**：那一章做的是**画像字段**对**用户原话**的溯源（`intake-field-grounding`），本条是 **JD 文案**对**画像字段**的溯源，两者对象不同，不能算已覆盖。保持未勾
- [x] 7.4 AI 生成内容标识注入（文案内显式提示 + 元数据记录模型与时间） → **已完成（2026-08-26 Shao Peishen 判定）**：文案内显式标识与生成时间已实现（`app/agents/jd_agent.py` 的 `AI_LABEL_TEMPLATE` + `_compose_with_label`，测试见 `tests/test_jd_agent.py`）。《AI 生成合成内容标识办法》要求的**对外标识**这一层已达成。
      ⤷ **括号里"元数据记录模型"这半条未实现，已移出**到 `ai-audit-trail-and-outbound-gate`（见文末「已移出」清单）。JD 落库时（`app/graph/nodes.py:183-197`）只写 `_jd_text` / `_jd_needs_manual`，不记模型标识——"这份 JD 是哪个模型哪一版生成的"目前答不出来。该包的 `analysis_run` 正是做模型标识持久化的，同向，不另起
- [ ] 7.5 标识保护：常规编辑不可删除；提供"标记为人工撰写"显式操作并留痕
      ⚠️ 完全未实现——JD 目前根本没有编辑功能，也就无所谓"编辑时保护"；"标记为人工撰写"操作与其留痕都不存在。保持未勾
- [x] 7.6 **歧视性表述拦截**：性别/年龄/婚育/地域/民族/健康状况关键词检测，命中则重新生成，连续 2 次转人工 → **已实现**（同 0.7），见 `app/agents/jd_agent.py`：`DISCRIMINATORY_PATTERNS` **六类逐个对上**，`generate_jd` 命中即重新生成、连续 2 次仍命中则 `needs_manual=True` 并回传 `blocked_categories`；前端 `index.html:265-267` 提示已转人工。测试 `tests/test_jd_agent.py`
- [ ] 7.7 纯文本一键复制导出
      ⚠️ 未实现——前端只有 `send-btn` 与 `confirm-btn` 两个按钮，JD 用 `textContent` 平铺在 `#jd-output` 里，**没有复制按钮、没有 clipboard 调用**（全文件 grep `clipboard` 零命中，仅有一处无关注释提到"复制"）。用户只能手工选中。⚠️ 0.8 那条勾选里写的"JD 展示与复制"，实际只交付了"展示"。保持未勾

## 8. 最小 Web 界面

> 现状：只有**一个单页会话界面**（`app/web/static/index.html`，271 行）——输入框、
> 追问对话、确认按钮、JD 展示。本章四条要的都是**会话之外**的视图，一条都没有。

- [ ] 8.1 岗位列表与状态视图 —— 无列表接口（只有 `GET /api/jobs/{job_id}` 查单个）、无列表页
- [ ] 8.2 画像详情页（含版本历史与生成快照）—— `job_profile` 逐版落库了，但没有任何页面或接口把版本历史读出来
- [ ] 8.3 JD 查看与复制 —— 查看已有（`#jd-output`），复制没有（同 7.7）
- [ ] 8.4 `needs_manual` 队列（HR 处理转人工的岗位）—— 无队列。前端只在单次 JD 生成 `needs_manual` 时提示一句，页面一关就没了；`JobStatus.NEEDS_MANUAL` 至今无人写入（同 2.5）

## 9. 验收与交付

- [ ] 9.1 **画像质量验收**：10 个真实历史岗位重跑，HR 与业务经理双方评估技术栈字段准确率，目标 ≥80%
      ⚠️ 未做。0.11 的试点是**另一件事**（3 位经理各跑 1 个**新**岗位、收集主观反馈），不是 10 个**历史**岗位重跑 + 双方评估准确率。保持未勾
- [ ] 9.2 端到端测试：从企微发起到 JD 产出的完整链路
      ⤷ **已移出**到阶段二·企微通道（Web 通道的等价链路已由 `tests/test_web_api.py` 覆盖），见文末「已移出」清单
- [x] 9.3 审计断言：每个画像都能追溯到 `analysis_run`；每次人工决策都有 `human_review` 记录 → **`human_review` 那一半已实现**：`app/audit/assertions.py` 断言四 `assert_every_decision_has_human_review`，已注册进 `COMPLIANCE_ASSERTIONS`（3 条 → 4 条），反证在 `tests/test_audit_assertion_effectiveness.py`。⚠️ `analysis_run` 那一半随 1.3/2.6 已移出到 `ai-audit-trail-and-outbound-gate`，不在本包
- [x] 9.4 灰度：1 位业务经理试用 2 个真实岗位 → **已由 0.11 完成，且覆盖面更大**：3 位业务经理各跑 1 个真实岗位（姚祖怡·供应链总监、底层软件工程师岗、非标产品采购员岗），反馈汇总见 `docs/m1-demo-pilot-feedback.md`。⚠️ 与原文的差异是"3 人 × 1 岗"而非"1 人 × 2 岗"——样本人数更多、同一人的连续两次体验没覆盖到；灰度目的（真实用户在真实岗位上跑通并给出反馈）已达成
- [x] 9.5 编写运行手册（部署、配置、故障排查、回滚） → **已实现**，四项逐个对上：`05-发布运行手册.md`（部署 + 配置，含 §「回滚」一节：本服务不影响任何现有流程，停用即回滚，无数据迁移负担）+ `docs/deploy-51-server.md`（故障排查，含 `.env` 非 UTF-8、scp 中文文件名两个真实故障的处置）

---

## 📤 已移出，另开变更包（2026-08-20 登记）

> **本节只做登记，不新建任何变更包。** 每条写明移到哪、为什么不属本包。
> ⛔ 移出**不等于**不做——下面 12 条都是"确实还要做"，只是不在本包的归档门槛里。

### → `ai-audit-trail-and-outbound-gate`（已存在的变更包，未归档）

| 原条目 | 内容 | 依据 |
|---|---|---|
| 1.3 | 建表 `analysis_run` | 该包 proposal 的 What Changes 明确写「新增 SQLite 表 `analysis_run`（一次 AI 调用的完整可复现快照）与 `criterion_score`」，字段比本条列的更全（多 rubric 快照、`evidence_ref`） |
| 2.6 | 每次调用自动写 `analysis_run` | 同上。调用点**已经在本包里就位**了——`app/llm/gateway.py:245` 的 `self._audit_hook.record(...)`，目前接的是 `NoopAuditHook`（只打 debug 日志）。该包的工作就是把这个钩子换成真实实现，属**接线**不属新建 |

⚠️ 连带关系：`docs/tech-debt.md` 的 **TD-1** 已登记「`job_profile` 的 `turn_started_at` /
`llm_latency_ms` 两列是过渡形态，`analysis_run` 落地即删」。这两列是本包之后由
`m1-intake-quality-fixes` 第 1 章加的，不影响本包归档。

### → 阶段二·企微通道（尚无变更包，待立项）

| 原条目 | 内容 |
|---|---|
| 1.5b | 建表 `wecom_callback`（回调落库） |
| 3.1 | 自建应用申请与配置 |
| 3.2 | 回调接口：签名校验 + 落库 + 5 秒内返回 200 |
| 3.3 | 回调去重 |
| 3.4 | 后台任务消费回调，异步唤醒 LangGraph thread |
| 3.5 | `template_card` 交互卡片发送封装 |
| 3.6 | 普通文本消息发送封装 |
| 9.2 | 端到端测试：从企微发起到 JD 产出 |

**依据**：M1 实际走的是 Web 通道。`Channel` 抽象（`app/channels/base.py` 的
`deliver` / `latest` 两个方法）与第一个实现 `WebChannel` 已经交付（0.5 已勾），
将来加 `WeComClannel` 时 graph 节点侧代码不需要改——通道抽象**本来就是为这次
移出准备的**，不是事后找的理由。9.2 一并移出是因为它的起点写死了"从企微发起"；
Web 通道的等价端到端链路已由 `tests/test_web_api.py` 覆盖。

⚠️ 工程铁律 6（企微回调先落库再处理、只推一次、5 秒无响应即丢弃）随这批一起走，
立项时逐字带过去。

### → M2 Postgres 迁移（尚无变更包，待立项）

| 原条目 | 内容 | 依据 |
|---|---|---|
| 1.7 | checkpoint 清理任务（按流程完成时间归档） | 清理任务必须贴着 checkpointer 的具体表结构写。现在按 SqliteSaver 的 schema 写一份，M2 迁到 Postgres checkpointer 时整份作废重写——先做就是先扔。M1 数据量（`.51` 现网 15 个 job）离需要清理还很远 |

### → 多供应商接入（尚无变更包，待立项）

| 原条目 | 内容 | 依据 |
|---|---|---|
| 2.3 | 双供应商切换与降级，切换事件记入 `analysis_run` | 本条的前提是 2.1 的"至少三家供应商对比"，而 2026-08-11 决策者**显式拍板** M1 只用 DeepSeek 单供应商、不等 doubao/qwen 补测账号。只有一家供应商时"切备用"无处可切。⚠️ 另有依赖：切换事件要记进 `analysis_run`，而那张表在 `ai-audit-trail-and-outbound-gate` 里，**该包要先落地** |

---

## ✅ 已判定的归类（2026-08-20 登记，2026-08-26 Shao Peishen 判定）

> **两条均改判为 A 类（已完成），已回勾。** 本节保留原始论证供追溯，不要再当待办看。
>
> | 条目 | 判定 | 附带处置 |
> |---|---|---|
> | 4.3 `interrupt()` 闭环 | **A · 行为等价**——Web 通道下由 HTTP + SQLite 状态 + 独立 `/confirm` 端点达成 | 企微通道那批要重新审视：异步推送场景下仍可能真需要 `interrupt()` |
> | 7.4 AI 标识注入 | **A · 已完成**——对外标识层达成 | 括号里"元数据记录模型"半条**移出**到 `ai-audit-trail-and-outbound-gate` |
>
> 下面是判定前的原始论证。

### 1. 条目 4.3 —— `interrupt()` 挂起与 `Command(resume=...)` 恢复的最小闭环

**原文**：`4.3 interrupt() 挂起与 Command(resume=...) 恢复的最小闭环打通`

**我倾向**：**C 类**（移出到阶段二·企微通道）

**存疑的具体点**：本图**刻意没有使用 `interrupt()`**——`tests/test_graph_idempotency.py:104`
的注释原文是"本图没有用 interrupt，所以对这个架构而言，'恢复'落地为的真实场景是：
调用方因超时、进程重启等原因，对同一个 `thread_id` 用同一份输入再 `invoke()` 一次"。
在 Web 通道下这是对的：HTTP 是同步请求/响应，每次请求跑完一整轮就返回，"等人回答"
天然由「浏览器停在那里 + 状态落 SQLite」承担，`interrupt()` 没有用武之地。

但**换到企微通道就不一样了**：消息是异步推送的，用户可能几小时后才回，那时才需要
"图挂起在某个节点 → 回调到达 → `Command(resume=...)` 续上"。所以这条更像是
**企微通道的前置技术能力**，而不是本包欠的债。

**为什么不敢直接判 C**：`interrupt()` 也是 6.3「挂起状态持久化」的一种实现路径，
如果将来确认断点要做成"图真的挂在那里"而不是"另开一个 `/confirm` 端点"，
这条就仍属本包。**这是架构取向问题，不是我能替决策人拍的。**

### 2. 条目 7.4 —— AI 生成内容标识注入

**原文**：`7.4 AI 生成内容标识注入（文案内显式提示 + 元数据记录模型与时间）`

**我倾向**：**A 类**（已完成），但缺口需转登记

**存疑的具体点**：括号里两件事只完成了一件半。
- ✅ **文案内显式提示**：`app/agents/jd_agent.py` 的 `AI_LABEL_TEMPLATE`
  （"【AI 生成】本文案由系统基于岗位画像自动生成，生成时间 {generated_at}"）
  由 `_compose_with_label` 无条件拼在正文后，测试覆盖在 `tests/test_jd_agent.py`
- ✅ **元数据记录时间**：`generated_at` 是 UTC ISO 时间戳，随文案落库
- ❌ **元数据记录模型**：JD 落库时（`app/graph/nodes.py:183-197`）只写
  `_jd_text` / `_jd_needs_manual`，**不记模型标识**。合规上"这份 JD 是哪个模型
  哪一版生成的"目前答不出来

**判定要点**：缺的这一半（模型标识持久化）**正是 `ai-audit-trail-and-outbound-gate`
的 `analysis_run` 要解决的事**（铁律 5：从 API 响应取回实际 `model` 字段并持久化）。
所以合理处置可能是「7.4 判 A + 把模型标识缺口并入该包」而不是整条挂在本包上。
但《AI 生成合成内容标识办法》（2025-09-01 施行）是**合规红线**，把一条红线相关的
条目判成"已完成"需要决策人点头，**我不替这种判断做主**——先归 D。
