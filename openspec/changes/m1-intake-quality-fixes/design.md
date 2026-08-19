## Context

动机与三份真实试跑证据见 `proposal.md`「Why」。这里只记与实现方式相关的现状约束：

- 追问在整条链路上是**裸字符串**：`_IntakeTurnSchema.questions: list[str]` → `IntakeTurnResult.questions` → `state["pending_questions"]` → `OutboundMessage.payload["questions"]` → 前端 `questions.join("\n")`。没有任何一层知道"这是几个问题、各自问的哪个字段"。
- 追问预算 `round_count` 不是状态字段，而是每次请求现算的 `SELECT COUNT(*) FROM job_profile WHERE job_id=?`（`app/web/server.py:75-77`），`business_key` 也依赖 `round_count`（`app/graph/build.py:53、81`）。"一轮 = 一个 job_profile 行"这个等式目前是硬绑定的。
- `unspecified_fields` 全程透传模型输出（`app/agents/intake_agent.py:233`），系统侧没有任何推导。
- 重复检测只有 `_repeats_earlier_assistant_turn`（归一化空白后逐字比对整轮问题串），对"换措辞重问"按定义无效。
- `LLMGateway.extract_structured` 已经在算 `latency_ms`（`app/llm/gateway.py:176-178`）并传给 `AuditHook`，默认 `NoopAuditHook` 只打 debug 日志后丢弃。
- 存储是 SQLite，`init_schema` 只有 `CREATE TABLE IF NOT EXISTS`，没有迁移机制；服务器上 `data/demo.db` 已有 15 个 job、真实试跑数据必须保住。
- `profile_patch` 是模型自由生成的**裸 dict**（`app/agents/intake_agent.py` 的字段表注释已经写明这一点），系统侧没有任何"这个值出自用户哪句话"的信息，编造在代码层面完全不可见。
- `LLMGateway.extract_structured` 已经从 API 响应里取回了实际的 `response_model`（`app/llm/gateway.py:184`，铁律 5 的落地），但和 `latency_ms` 一样，交给 `AuditHook` 之后被 `NoopAuditHook` 丢弃，没有落库。
- 前端是单文件无构建的 `app/web/static/index.html`，所有请求走相对路径（部署约束 1）。

## Goals / Non-Goals

**Goals:**

- 让"一个追问"成为贯穿 agent → graph → API → 前端的**一等对象**，后续企微卡片只需换渲染层
- 把四类判定从"靠提示词自觉"改成**确定性代码**：模糊回复识别、已问未答追踪、未指定字段推导、字段来源校验；每一类都能被单元测试直接断言，不需要真实 LLM
- 让"AI 有没有编造"变成一个**可复算的数字**，而不是业务经理对着一份 JD 文案给出的主观印象
- 保住既有 demo 库的数据，不重建库

**Non-Goals（设计层边界，范围边界见 proposal.md「Non-goals」）:**

- 不引入 schema 迁移框架（Alembic 等）——本变更只加列，用 `PRAGMA table_info` + 条件 `ALTER TABLE` 即可
- 不改 `AuditHook` Protocol 的签名——`ai-audit-trail-and-outbound-gate` 正基于现签名设计，改它会制造冲突
- 不把"一轮 = 一个 job_profile 行"这个等式拆掉

## Decisions

### 决策 1：结构化问题的载体是新对象，不是给字符串加约定

**选择**：新增 `IntakeQuestion` 数据结构（`question_id` / `text` / `field` / `options[]` / `allow_free_text` / `is_reask`），`_IntakeTurnSchema.questions` 从 `list[str]` 改为 `list[IntakeQuestion]`，一路透传到 API 响应的 `payload.questions`。

**否决的替代**：在字符串里塞分隔符或 Markdown 约定（如 `[field] 问题？| 选项A | 选项B`）由前端解析。否决理由：模型对格式约定的遵守率不可控，解析失败是静默降级；而 `LLMGateway` 已有 strict 结构化输出能力（`_to_strict_json_schema`），把约束交给 schema 是这个仓库已经验证过的做法。

**代价**：`_repeats_earlier_assistant_turn` 依赖 `history` 里的 assistant 文本，`compute_intake_turn` 写 history 时需要把结构化问题渲染成文本。渲染函数必须是唯一入口，避免两处各渲染一遍导致历史与下发内容不一致。

### 决策 2：`question_id` 由系统按目标字段派生，不由模型自己编

**选择**：`question_id = f"{field}"`（一个字段最多同时挂一个未答问题），模型只负责给出 `field`、`text`、`options`。系统按 `field` 归一化出 id。

**否决的替代**：让模型自己生成并复用 id。否决理由：跨轮复用同一个 id 要求模型记住历史 id 表，这正是 temperature=0 下也不可靠的那类要求；而"稳定标识"是 `intake-question-tracking` 的地基，地基不能建在模型自觉上。

**代价**：同一字段的两个不同角度的问题（"要不要 ISO 26262" 与 "要哪个 ASIL 等级"）会撞 id。处理：这两个问题在语义上确实是同一个字段的递进，撞 id 后按"重问"处理是可接受的近似；若后续需要区分，再引入 `field + aspect` 的复合 id，不在本批做。

### 决策 3：模糊回复识别用确定性规则，提示词只作第二道

**选择**：在 `intake_agent` 里加一个纯函数，对用户本轮原文做两类判定——(a) 命中模糊表态词表（不知道 / 不太了解 / 你决定 / 随便 / 你看着办 / 你有什么建议 / 都行……）；(b) 用户回复以问号结尾且不含任何本轮问题的目标字段线索（反问模式）。命中即强制走"兜底档位"分支：本轮下发的问题 MUST 带 `options`，`options` 从领域选项库取，取不到就用该字段的通用档位（含明确的"无要求 / 不限"）。

**否决的替代**：只靠 2026-08-18 已加进 `SYSTEM_PROMPT` 的那段「回答模糊/不知道时怎么办」（`app/agents/intake_agent.py:92-100`）。否决理由：该段目前**没有任何测试覆盖，也无法在不调真实模型的情况下验证**——而这次事故本身就是"提示词说了、模型没做"。提示词保留，但判定与兜底注入必须由代码保证：代码检测到模糊回复而模型返回的问题不带 `options` 时，由系统补上领域选项库里的档位。

**代价**：词表是硬编码的中文规则，会有漏判（用户用没收录的说法表达"不知道"）。漏判的后果是退回今天的行为，不会更差。误判（用户说"随便哪个 MCU 都行"其实是有效答案）的后果是多给一组选项，也不致命——但必须保证误判时**不覆盖**模型已提取到的字段。

### 决策 4：`ecu_knowledge.FOLLOWUP_RULES` 从"问题列表"升级为"问题 + 字段 + 选项"

**选择**：值类型从 `list[str]` 改为 `list[FollowupSpec]`（`text` / `field` / `options`）。新增非 ECU 通用领域条目（一般材料、办公采购、非标产品）——姚祖怡那场卡死在"一般材料"上，正是因为知识库只覆盖嵌入式/功能安全，采购侧一个词条都没有。

**否决的替代**：让模型现场生成档位。否决理由：档位是要被写进岗位硬性要求的内容，来源必须可追溯、可评审；且模型生成档位正是"编造 MCU 型号"那类风险（见 `docs/m1-demo-pilot-feedback.md` 的编造检查）的高发面。模型可以生成**问题**，档位优先取知识库；知识库未命中时模型可给档位，但 UI 必须标明是 AI 建议（合规要求，见 proposal.md「合规影响说明」）。

### 决策 5：轮次预算改为"有产出轮计数"，用 `job_profile` 的现有列判定，不加状态机

**选择**：保留"一轮 = 一个 job_profile 行"的等式（`business_key` 依赖它，动了就要动幂等键语义，风险远大于收益）。改的是**取数口径**：
- `MAX_ROUNDS`（5）改为对"有产出轮"计数 —— `SELECT COUNT(*) FROM job_profile WHERE job_id=? AND is_productive=1`
- 新增 `MAX_TOTAL_ROUNDS`（建议 8）对总行数计数，任一命中即收尾
- `is_productive` 在 `compute_intake_turn` 里判定：本轮 `profile_patch` 相对上一轮有新字段，**或**本轮问出了此前未问过的 `question_id`

**否决的替代**：在 `IntakeState` 里维护一个预算计数器。否决理由：`IntakeState` 没有 reducer，真源是数据库（见 `app/graph/state.py` 的说明），把预算放进 state 会引入第二个真源。

**代价**：新增一列 `is_productive`。`business_key` 仍用总行数（`round_count`），幂等语义完全不变。

### 决策 6：未指定字段 = `JobProfile` 字段表 − 已确定字段，模型输出降级为参考

**选择**：新增纯函数 `derive_unspecified_fields(accumulated: dict) -> list[str]`，遍历 `JobProfile.model_json_schema()` 的属性（排除 `_SYSTEM_MANAGED_FIELDS`），把值缺失、为 `None`、为空容器、或等于占位符（"未指定"）的字段列为未指定。`parsed.unspecified_fields` 不再进入结果，只在 debug 日志里保留作对照。

**为什么必须换掉**：真实数据两个方向都错过——`a478499c` 强制收尾时模型给的是**空数组**（漏报），`19b6ec6d` 却把用户已经答过的 `functional_safety` / `sop_projects` 列了进去（虚报）。一个既会漏报又会虚报的列表，比没有更糟：它让人以为"系统说没问题"。

**代价**：`derive_*` 只能看出"字段有没有值"，看不出"值是不是敷衍"（如 `experience_years="不限"`）。可接受——本变更的目标是不再漏报，不是判断质量。

### 决策 7：中文字段名映射放在后端，不放前端

**选择**：新增 `field → 中文名` 映射（与 `JobProfile` 同一模块，紧邻字段定义），API 返回 `unspecified_fields` 时同时返回中文名。

**否决的替代**：前端硬编码一份中文映射表。否决理由：`JobProfile` 加字段时前端不会同步更新，用户会看到漏网的英文 snake_case——这正是今天的故障现象。映射与字段定义放在一起，加字段时漏改会被字段表的完整性测试直接抓到。

### 决策 8：知情确认走请求体标记，不建新表

**选择**：`POST /api/jobs/{job_id}/confirm` 请求体新增 `acknowledged_gaps: bool`。存在未指定字段而该标记为 false 时返回 409 并附上未指定字段（含中文名）。确认成功时，把"确认时的未指定字段列表 + 知情标记"写进已有的 `job_profile.profile_json`（与 `_jd_text` 同一位置的下划线前缀内部键）。

**否决的替代**：新建 `profile_confirmation` 表。否决理由：本变更的留痕目的只是"事后能查明确认时是否知情"，一条随画像走的记录就够；建表会与 `ai-audit-trail-and-outbound-gate` 的留痕设计撞车。

**代价**：混在 `profile_json` 里不便查询。M2 迁 Postgres 时随留痕体系一起规整。

### 决策 9：时序留痕加两列到 `job_profile`，明确登记为技术债

**选择**：`job_profile` 加 `turn_started_at`（本轮 HTTP 请求进入时刻）与 `llm_latency_ms`（本轮 LLM 累计耗时）。`extract_structured` 把已算出的 `latency_ms` 通过返回值或调用方可读的方式透出（**不改 `AuditHook` 签名**），经 `IntakeState` 传到 `effect_persist_draft`，与画像草案同一事务写入（满足 `intake-turn-observability` 的"同生共死"要求）。

**边界**：`analysis_run` 落地后（`ai-audit-trail-and-outbound-gate` tasks 1.1 已包含 `latency_ms` 与 `created_at`），这两列成为冗余，届时删除。**必须在 `tasks.md` 里登记这条技术债**，否则两套时序数据会长期并存并互相矛盾。

**否决的替代**：等 `ai-audit-trail-and-outbound-gate` 落地再说。否决理由：本批的 P0/P1 改动必须能被验证——"兜底档位是否真的减少了空转轮、有没有把单轮延迟拖长"——而那个变更范围大得多、尚未排期，等它意味着本批的效果只能靠感觉判断。

### 决策 10：SQLite 加列用条件 `ALTER TABLE`，不重建库

**选择**：`init_schema` 在 `executescript(SCHEMA)` 之后跑一段幂等的加列逻辑：读 `PRAGMA table_info(job_profile)`，缺哪列补哪列（`ALTER TABLE ... ADD COLUMN ... DEFAULT ...`）。所有新列必须可空或有默认值，使既有 15 个 job 的历史行不需要回填。

**否决的替代**：改 `CREATE TABLE` 语句了事。否决理由：`CREATE TABLE IF NOT EXISTS` 对已存在的表完全无效，服务器上的库不会拿到新列，而部署脚本不会重建库——这是一个上线后才会炸、且只在服务器上炸的静默故障。

### 决策 11：编造度量用"逐字引用 + substring 校验"，不用模型自评也不用语义比对

**选择**：`profile_patch` 的每个字段从裸值升级为 `{value, source_quote, source_turn}`。系统侧的校验是一个纯函数：把 `source_quote` 与第 `source_turn` 轮的用户原话都做统一归一化（空白折叠、全半角统一）后做子串判定，找不到即标记未溯源。`value` 本身允许是模型对原话的归纳（用户说"MISRA C"，字段值写成规范化的枚举值），**被校验的是引用的真实性，不是值与引用的等价性**。

**为什么这样就能抓到编造**：`deepseek-v4-pro` 那次编出 `ARM Cortex-M / Infineon TriCore / Keil / IAR` 时，输入里根本没有任何相关文字——模型要么给不出引用，要么只能连引用一起编，而编出来的引用过不了子串判定。编造在这个校验下没有藏身处。

**否决的替代 A**：让第二个模型当判官，判定"这个字段是否有用户输入支撑"。否决理由：判官本身会编，且不可复算——同一份数据两次跑出两个编造率，这个数字就没有决策价值。而本变更要的正是一个**能拿去对比 flash 与 v4-pro、能拿去看"改完之后编造率有没有变"的数字**。

**否决的替代 B**：用嵌入相似度做语义比对。否决理由：阈值不可解释，且会把"模型正确归纳"与"模型编造"混在同一个连续分数里，反而失去二值信号。

**代价**：模型可能"抄一段无关的原话来凑引用"——引用是真的，但与字段值无关。这类作弊过得了本校验。可接受：它比凭空编造要难得多，而且本批的目标是拿到一个**下界**（真实编造率 ≥ 未溯源率），不是精确值。

### 决策 12：本批只观测不拦截，拦截阈值等真实数据

**选择**：未溯源字段照常写入画像，只把清单与该轮实际模型标识落库。

**为什么不直接拦**：flash 的真实编造率是未知数。如果它接近 0，拦截几乎无成本；如果它像 v4-pro 一样是 1/3，直接拦截会把三分之一的字段挡在画像外，采集直接不可用——而"模型不擅长给逐字引用"和"模型在编造"这两种情况，在没有数据之前分不开。**先量再拦**是这里唯一负责任的顺序；先拦会用一次线上事故换来同一个数字。

**触发条件（写死，避免这条永远悬着）**：本批上线后累计 ≥ 20 场真实采集会话，拿到未溯源率分布，再单独开一个变更定拦截策略。**这条必须登记进技术债**，与决策 9 的两列并列。

**代价**：本批上线后画像里仍可能有编造内容，与今天一样——但从这一批起它是**可见且可数的**，而不是像三份试跑反馈那样，靠业务经理对着一份 JD 文案凭印象说"没有编造"。

### 与 `m1-job-profile-intake` 的关系

本变更在行为上收紧了该变更 delta spec 里的两条要求：

- 「多轮追问补全 / 追问达到上限」：`intake-guided-options` 把"追问轮次"重定义为有产出轮并加了总轮次硬上限；`intake-question-tracking` 追加了重问标注与重问次数上限
- 「追问达到上限 → 用'未指定'填充缺失字段并标记 / 确认时显式列出未指定项」：`intake-completeness-warning` 把"标记"从模型输出改为系统推导，并把"显式列出"升级为"必须显式知情"

`m1-job-profile-intake` 尚未归档（`openspec/specs/` 为空），因此无法在本变更里以 `MODIFIED Requirements` 形式声明。**归档顺序必须是 `m1-job-profile-intake` 先、本变更后**；归档本变更时须人工核对上述两处，确保折进 `openspec/specs/` 的活文档里没有留下"5 轮"与"5 个有产出轮"两种互相矛盾的表述。

## Risks / Trade-offs

- **[模型不再返回合法的结构化问题，整个采集挂掉]** → `IntakeQuestion` 的所有字段除 `text` 外都可空/有默认；`options` 缺失时退化成今天的纯文本问题而不是报错。strict schema 校验失败已有 `SchemaExtractionFailed` 重试路径覆盖。
- **[模糊回复词表误判，把有效回答当成"不知道"]** → 误判只影响"是否额外给一组选项"，绝不允许影响 `profile_patch` 的写入；用一条测试锁死"误判时已提取字段不被清空"。
- **[`question_id = field` 撞 id，导致递进提问被当成重问并很快触顶]** → 重问次数上限取 2（问 1 次 + 重问 2 次 = 3 次），给递进提问留余量；实施时用真实的 `2494103e` 会话回放验证 IATF/26262 那段的行为符合预期。
- **[有产出轮计数让对话变长，业务经理更烦]** → `MAX_TOTAL_ROUNDS=8` 兜底；同时新增的时序留痕正是用来量这件事的，上线后拿真实数据复核。
- **[知情确认多加一步点击，被当成新的摩擦]** → 只在存在未指定字段时出现；无缺口时确认流程与今天完全一致。
- **[服务器 SQLite 加列失败或部分成功]** → 加列逻辑逐列独立、幂等；上线前先在本地 `data/demo.db` 的副本上跑一遍，验证 15 个 job 的历史数据读写正常。
- **[本变更的两列与将来的 `analysis_run` 长期并存、互相矛盾]** → 在 `tasks.md` 显式登记技术债并写明触发条件（`ai-audit-trail-and-outbound-gate` 落地即删）。
- **[模型给不出合法的逐字引用，未溯源率虚高]** → 这正是本批只观测不拦截的原因（决策 12）：虚高的未溯源率是一个需要解释的数字，不是一次线上故障。实施时在 `SYSTEM_PROMPT` 里给出引用的正反例；回放三段真实会话时人工抽查若干未溯源字段，区分"模型不会引用"与"模型真编造"，结论写进 7.x 的验证记录。
- **[`profile_patch` 结构升级把 strict schema 撑大，抽取失败率上升]** → 来源字段全部可空，缺失即计未溯源而不是校验失败（spec 的"来源结构缺失时降级而非报错"）；上线前对比改动前后的 `SchemaExtractionFailed` 发生率。

## Migration Plan

1. 本地：`init_schema` 加列逻辑先在 `data/demo.db` 的**副本**上跑通，确认既有行可读、新列为空不影响现有查询
2. 本地：全量测试 + 用三段真实会话（`19b6ec6d` / `2494103e` / `a478499c`）的用户输入序列做回放，对比修复前后的空转轮数与最终未指定字段
3. 服务器：`sync-to-server.sh` 推送前**先备份** `C:\apps\zhuopin-recruit-agent\data\demo.db`（含 `-wal` / `-shm`）
4. 服务器：重启服务，`init_schema` 自动加列；用一个新 job 跑通"模糊回复 → 拿到档位 → 点选 → 带缺口确认"整条路径
5. 回滚：代码回滚即可，新增列对旧代码是惰性的（旧代码不读这些列，`INSERT` 不带它们也能成功，因为都可空/有默认）——**回滚不需要回退数据库**

## Coverage Gaps（阻塞归档，不同于下面的 Open Questions）

- **`intake-turn-observability` 的「逐轮时序留痕」，"LLM 调用失败或重试时，留痕 SHALL 记录直到本轮结束为止的累计耗时" 这句 SHALL 的"全部重试都失败"这一半未实现**（unit A 收尾复核，2026-08-19）。已实现并有测试覆盖的是"重试后成功"：耗时按重试次数累加，最终落进画像行。未实现的是"重试全部失败"：这种情况下 `SchemaExtractionFailed` 从 `run_intake_turn` 抛出，`effect_persist_draft` 不会跑，本轮既不写 `job_profile` 行也不写时序——不违反同一条 Requirement 里"时序与画像同生共死"那句（两者同为空，满足"不允许画像有、时序没有"），但确实没有满足"记录累计耗时"这句 SHALL。
  - **为什么先不做**：要满足这句 SHALL，唯一的办法是在整轮失败时也写一行东西来承载耗时——也就是为一个没有产出画像草案的轮次专门写一条草案/时序行，这会消耗追问预算并改变现有行为（这轮到底算不算"发生过"），已经超出本单元「只换载体」的范围，需要与第 3 章的 `is_productive` 判定一起设计，不能在本单元单独定。
  - **归档前置条件**：`m1-intake-quality-fixes` 变更包在这句 SHALL 被实现、或被人工把 spec 这句窄化为只覆盖"重试后成功"之前，**不得归档**。narrowing 是决策者的判断，不是实现者能替代做的，本次复核只记录不改 spec 原文。

## Open Questions

- `MAX_TOTAL_ROUNDS` 取 8 是拍的：5 轮有产出 + 最多 3 轮空转。上线后拿真实的空转轮分布复核，不影响本批的 spec 与任务拆分。
- 兜底档位在企微卡片上的呈现形态（按钮 / 快捷回复 / 选人控件）留到企微通道那一批定；本变更只保证数据结构够用。
- 未溯源率达到多少才值得拦截、拦截后如何降级（退回追问 vs 记为未指定），本批不定——按决策 12，等 ≥ 20 场真实会话的分布出来再单独开变更。
- `derive_question_id` 未校验 `field` 是否属于 `JobProfile` schema，也没有 null-`field` 比例的监控指标——unit A 收尾复核记下，明确"第 5 章之前修"，本单元不实现。
