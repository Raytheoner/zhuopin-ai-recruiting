> 章节粒度约定（CLAUDE.md「工具链分工」）：**一个章节 = 一份 superpowers plan = 一条 worktree 分支 = 一个可独立测试并合并的交付单元**。章节 checkbox 在该 plan 的 final review 通过后才勾。
>
> 依赖顺序：第 2 章是第 3/4/5 章的地基，必须先合。第 1 章是第 7 章的地基（新增两列），第 7 章的点选来源例外（7.4）依赖第 4 章。第 6 章可与其他章节并行。第 8 章最后。

## 1. 存储地基与逐轮时序留痕

对应 `intake-turn-observability`。设计依据：design.md 决策 9、决策 10。

- [x] 1.1 在 `app/storage/db.py` 的 `init_schema` 里加幂等加列逻辑：读 `PRAGMA table_info(job_profile)`，缺列则 `ALTER TABLE job_profile ADD COLUMN`。本变更需要的新列：`is_productive INTEGER NOT NULL DEFAULT 1`、`turn_started_at TEXT`、`llm_latency_ms REAL`、`derived_unspecified_fields TEXT NOT NULL DEFAULT '[]'`、`ungrounded_fields TEXT NOT NULL DEFAULT '[]'`、`llm_response_model TEXT`（后两列服务第 7 章）。所有新列必须可空或有默认值，既有行不需要回填
- [x] 1.2 加列逻辑的测试：对一个用**旧 schema** 建好并塞了数据的库跑 `init_schema`，断言新列出现、旧行可读、旧行的新列为默认值；重复跑第二次不报错（幂等）
- [x] 1.3 让 `LLMGateway.extract_structured` 把已算出的 `latency_ms`（`app/llm/gateway.py:176-178`）与已取回的 `response_model`（`app/llm/gateway.py:184`）一并透出给调用方。**不得改动 `AuditHook` Protocol 的签名**（design.md 决策 9：`ai-audit-trail-and-outbound-gate` 正基于现签名设计）。含重试时记累计耗时
- [x] 1.4 `IntakeState` 增加 `turn_started_at` / `llm_latency_ms`；`app/web/server.py` 的 `_run_turn` 在 invoke 前打时间戳并放进 state
- [x] 1.5 `effect_persist_draft` 把时序两列与画像草案写在**同一次 INSERT** 里（spec 要求"时序与画像同生共死"）。不新增 effect 节点，不改 `business_key` 语义
- [x] 1.6 测试：一轮采集完成后画像行上带 `turn_started_at` / `llm_latency_ms`；画像写入失败时时序留痕同样不存在；LLM 重试后耗时覆盖重试
- [x] 1.7 在本仓库技术债清单里登记：**这两列在 `ai-audit-trail-and-outbound-gate` 的 `analysis_run` 落地后删除**，触发条件写明。不登记会导致两套时序数据长期并存互相矛盾（design.md 决策 9「边界」）

## 2. 结构化追问对象端到端透传

对应 `intake-guided-options` 的「结构化追问与可选项作答」与 `intake-question-tracking` 的「子问题的稳定标识与拆分」。设计依据：design.md 决策 1、决策 2。**本章只换载体，不把第 3/4/5 章的判定逻辑（`is_vague_reply`、选项填充、`is_productive`/`is_reask` 判定、`derive_unspecified_fields`、可点选控件）提前带进来**——这条约束本身仍然成立，也被本单元遵守了。

但本章不是行为零变化：**2.4 是一处授权的、影响可观察行为的 prompt 级改动**（tasks 2.4 明文要求"一个问题条目只能承载一个可独立作答的子问题"，对应的 spec 要求在覆盖矩阵里已为本单元标 ✅）。把复合问题拆成多条会让单轮消耗的 `MAX_QUESTIONS_PER_ROUND` 槽位变多，从而改变追问预算的消耗节奏——这是拆分规则本身带来的、意料之中的影响，不是判定逻辑被提前带入。该影响与第 3 章 `MAX_TOTAL_ROUNDS` 取值的关系见第 3 章开头的附注。

- [x] 2.1 定义 `IntakeQuestion`：`question_id` / `text` / `field` / `options: list[str]` / `allow_free_text: bool` / `is_reask: bool`。除 `text` 外全部可空或有默认值（design.md 风险表第 1 条：模型退化时降级成纯文本问题，不报错）
- [x] 2.2 `_IntakeTurnSchema.questions` 改为 `list[IntakeQuestion]`；确认 `_to_strict_json_schema` 能正确处理嵌套对象数组，必要时补 `app/llm/gateway.py` 的 schema 转换测试
- [x] 2.3 `question_id` 由系统按 `field` 派生（**不让模型自己编 id**，design.md 决策 2）；模型只给 `field` / `text` / `options`。`field` 缺失时的兜底 id 规则要有测试
- [x] 2.4 `SYSTEM_PROMPT` 增加"一个问题条目只能承载一个可独立作答的子问题"的约束，并给出反例（"是否需要熟悉 IATF 16949 或 ISO 26262？"必须拆成两条）
- [x] 2.5 唯一的问题→文本渲染函数：`compute_intake_turn` 写 history 的 assistant 内容与下发给通道的文本必须出自同一个函数（design.md 决策 1「代价」）。两处各渲染一遍会让 `_repeats_earlier_assistant_turn` 比对到与实际下发不一致的文本
- [x] 2.6 `OutboundMessage` 的 `question` payload 从 `{"questions": [str]}` 改为携带结构化问题列表；`app/web/server.py` 的响应体同步
- [x] 2.7 前端 `index.html` 的 `renderMessage` 适配新 payload，本章仍只渲染文本（选项控件在第 4 章）
- [x] 2.8 回归测试：既有采集流程测试全绿；一轮追问的端到端响应体结构符合新契约

## 3. 模糊回复兜底与领域选项库

对应 `intake-guided-options` 的「模糊回复与反问的兜底档位」「候选档位不得代替用户做决定」「零产出轮不消耗追问预算」。设计依据：design.md 决策 3、4、5。依赖第 2 章。

> **附注（unit A 收尾复核记录，写给 3.10 取 `MAX_TOTAL_ROUNDS` 值的人）**：第 2 章 2.4 引入了"一个问题条目只能承载一个可独立作答的子问题"的拆分规则——像"是否需要熟悉 IATF 16949 或 ISO 26262？"这类复合问题会被拆成两条，各占一个 `MAX_QUESTIONS_PER_ROUND` 槽位。合并后同一个话题平均消耗的问题槽位数会增加，`MAX_ROUNDS`（有产出轮计数）固定的情况下，收尾前能覆盖的独立话题数会变少，更多字段被推入"未指定"。取 `MAX_TOTAL_ROUNDS`（3.10）时请把这一层消耗算进去，不要只按拆分前的历史会话估算。

> **实施记录（2026-08-19，单元 B）**：已问台账落在**新增列 `job_profile.asked_questions`**（`IntakeQuestion.to_payload()` 的 JSON 数组），走 1.1 的 `init_schema` 幂等加列路径。否决 `profile_json` 内部键的理由：`profile_json` 每轮被读回来当 `profile_patch_accumulated` 送进 prompt，台账放进去会每轮泄漏进 prompt 并污染 `input_hash`；`_jd_text` 能用那个位置是因为它只在 `confirm` 那一刻写、从不进 prompt。单元 E 的 5.1 直接在 `IntakeState.asked_question_ids_before` / `previous_questions` 两个键上扩。

- [x] 3.1 `app/agents/ecu_knowledge.py` 的 `FOLLOWUP_RULES` 从 `list[str]` 升级为 `list[FollowupSpec]`（`text` / `field` / `options`），既有 4 个词条补齐 `field` 与档位
- [x] 3.2 新增采购/非 ECU 侧词条：一般材料、办公采购、非标产品、供应商开发。**姚祖怡那场就是卡死在"一般材料"上**——知识库当时一个采购词条都没有（design.md 决策 4）
- [x] 3.3 纯函数 `is_vague_reply(text) -> bool`：模糊表态词表（不知道 / 不太了解 / 你决定 / 随便 / 你看着办 / 你有什么建议 / 都行…）+ 反问模式（以问号结尾且不含目标字段线索）。**确定性判定，不调模型**
- [x] 3.4 命中模糊回复时的强制兜底：本轮下发的问题必须带 `options`；模型没给就由系统从领域选项库补；库里没有就用该字段的通用档位（必须含"无要求 / 不限"这类明确否定档位）
- [x] 3.5 `SYSTEM_PROMPT` 现有的「回答模糊/不知道时怎么办」段（`app/agents/intake_agent.py:92-100`）保留为第二道，但**判定与注入由代码保证**——这次事故本身就是"提示词说了、模型没做"（design.md 决策 3）
- [x] 3.6 测试（真实回放）：喂入 `19b6ec6d` 第 4 轮的"这些我不太了解，你有什么建议"与 `a478499c` 第 5 轮的"一般材料是什么，你都不知道吗"，断言产出的问题**必定带 2-3 个具体档位**，且回复中不出现无档位的"我来帮您整理"式内容
- [x] 3.7 测试（合规红线）：用户回"你决定吧"时，画像对应字段**保持未确定**，任何候选档位都不得进入 `profile_patch`（proposal.md「合规影响说明」：AI 不得代替业务经理做决定）
- [x] 3.8 测试（误判安全）：`is_vague_reply` 误判一条有效回答时，模型已提取到的字段**不被清空**（design.md 风险表第 2 条）
- [x] 3.9 零产出轮判定：`compute_intake_turn` 算出 `is_productive`（本轮 `profile_patch` 相对上轮有新字段 **或** 问出了未问过的 `question_id`），落进 1.1 加的列
- [x] 3.10 预算取数改口径：`app/web/server.py` 的 `round_count` 拆成两个 —— `MAX_ROUNDS`(5) 对 `is_productive=1` 计数，新增 `MAX_TOTAL_ROUNDS`(8) 对总行数计数，任一命中即收尾。**`business_key` 继续用总行数，幂等语义不变**（design.md 决策 5）（实施记录：`MAX_TOTAL_ROUNDS=8`，已把 2.4 拆分规则带来的槽位消耗算进去；口径为 `job_profile` 总行数，`MAX_ROUNDS` 改为对 `is_productive=1` 计数，`business_key` 不变）
- [x] 3.11 测试：空转轮不减预算；有产出轮正常减预算；连续空转触顶 `MAX_TOTAL_ROUNDS` 后正常收尾进确认流程

## 4. 前端可点选选项

对应 `intake-guided-options` 的「结构化追问与可选项作答」在 Web 通道的落地。依赖第 2 章的 payload 契约（已随单元 A 合并）。

> **并行性更正（2026-08-19，`delivery-units.md` §2.C）**：本章＝交付单元 **C**，**不依赖第 3 章的代码**，与第 3 章（单元 B）**可真并行**——B 全在后端、C 只碰 `index.html`，零文件重叠。B 未合并时 `options` 基本为空，本章的渲染分支自然退化成今天的纯文本。并行成立的前提：点选提交采用「选中档位文本原样拼进该轮回复、POST 既有 `/reply`、**不改 API 契约**」的最简形态（§5 约定 2）。

- [x] 4.1 `index.html` 按 `options` 渲染可点选控件（原生 DOM，不引入框架），保留自由文本输入；`options` 为空时只渲染自由文本，不渲染空控件
- [x] 4.2 点选结果构造该轮回复文本并提交，**不要求用户复制粘贴或改写系统的问题文本**（这是经理原话抱怨的点）
- [x] 4.3 选项区标明"以下为 AI 建议选项"——《AI 生成合成内容标识办法》要求（proposal.md「合规影响说明」）
- [x] 4.4 所有请求继续走相对路径，不硬编码 `/static/…` `/api/…`（部署约束 1）；验证挂在 `root_path` 子路径下仍正常
- [x] 4.5 测试：点选提交（无文字输入）能推进对话且选中内容进入画像；只写自由文本不点选也能推进，不因"未点选"被拒

## 5. 已问未答追踪与诚实重问

对应 `intake-question-tracking` 的「已问未答的判定」「重问必须显式标注」「重问次数上限」。设计依据：design.md 决策 2。依赖第 2 章。

- [x] 5.1 `IntakeState` 增加已问子问题台账（`question_id` → 已问轮次 / 是否已答 / 重问次数）；真源随画像落库，不只活在 checkpoint（对齐 `app/graph/state.py` 既有约定）
- [x] 5.2 已答判定：用户回复后判定本轮覆盖了哪些已问 `question_id`；未覆盖的保持"已问未答"
- [x] 5.3 空转轮的状态处理：整轮无字段产出时，本轮之前已问的子问题**全部保持已问未答**，不得被标记为已答
- [x] 5.4 重问时置 `is_reask=true`，渲染层加显式重问提示（"这个你刚才没答"一类）；重问条目与新问题在界面上可区分，**不得混编成看起来是新问题的一句话**
- [x] 5.5 重问次数上限取 2（问 1 次 + 重问 2 次）；超限即停止追问该子问题并把目标字段计入未指定字段，不再消耗追问轮次
      ⚠️ **实施偏离交付计划的逐字代码，2026-08-27 Shao Peishen 拍板批准。** 计划给交付单元 E Task 2 的逐字代码把"超限"谓词（`not entry.is_answered and entry.ask_count >= MAX_ASKS_PER_QUESTION`）写了**两遍**——一处在 `_apply_question_ledger` 的摘除分支、一处在 `run_intake_turn` 算兜底合成要跳过的 `exhausted` 集合。Task 2 review 判为 Important：两处只改一处就会漂移，而漂移**没有任何症状**——兜底合成挑中一个"它以为还能问、摘除侧认为已超限"的字段，合成出来的问题被当场摘掉，本轮 `questions` 变空，**用户收到一个空气泡**，不抛异常、不失败、没有任何既有断言会红。
      落地：抽成 `app/agents/intake_agent.py::_is_exhausted(entry)`，行为逐字节等价，**未改动任何计划规定的常量与函数签名**（`MAX_REASKS` / `MAX_ASKS_PER_QUESTION` / `run_intake_turn` 入参一字未动）。commit `93cefeb`。
- [x] 5.6 测试（真实回放）：用 `2494103e` 第 3-4 轮的 IATF 16949 / ISO 26262 序列，断言 ISO 26262 被判为已问未答、重问时带重问标注、且 `question_id` 与首问一致（换措辞不改 id）
      ℹ️ 取数边界：回放重建的是那次事故的**形状**（打包提问 → 部分回答 → 换措辞重问），前置事实取自本仓库已逐字记载的 `proposal.md` 第 7 行与 `docs/m1-demo-pilot-feedback.md`，**没有也不去 `.51` 取 `conversation` 原文**（合规红线「模型全部走境内」段、单元 D 的取数范围）。它不是逐字节的生产 turn 重放。出处与局限逐字写在 `test_replay_2494103e_iatf_and_iso26262_sequence` 的 docstring 里，`test_replay_2494103e_stops_reasking_iso26262_after_the_cap` 指回同一段并额外说明"问满 3 轮"是按 5.5 规则外推的假设序列、不是已记载的事实。
- [x] 5.7 测试：`question_id = field` 撞 id 的递进提问（"要不要 26262" → "要哪个 ASIL"）在上限 2 之内不会被过早掐断（design.md 风险表第 3 条）
- [x] 5.8 `_repeats_earlier_assistant_turn` 与新机制的关系交代清楚：保留作为最后一道逐字防线，还是由 `question_id` 追踪取代——在实现里给出结论并写进注释
      结论：**保留，职责收窄为兜底**（三条理由逐字写进该函数 docstring）。台账为空的 job（历史行不回填，§5 约定 4）上它是唯一防线，由 `tests/test_intake_agent.py::test_verbatim_repeat_detection_still_guards_jobs_with_an_empty_ledger` 钉住。

## 6. 未指定字段推导与确认前警示

对应 `intake-completeness-warning` 全部三条要求。设计依据：design.md 决策 6、7、8。

> **并行性更正（2026-08-19，`delivery-units.md` §3.1）**：原写「可与第 3-5 章并行」**不成立**。本章要改 `intake_agent.py` / `graph/nodes.py` / `web/server.py` / `index.html`，与第 3、4、5 章四个文件全线重叠。按「触碰文件重叠即须串行」判据，本章＝交付单元 **D**，排在 B∥C 之后、E 之前。

- [x] 6.1 纯函数 `derive_unspecified_fields(accumulated: dict) -> list[str]`：遍历 `JobProfile.model_json_schema()` 属性（排除 `_SYSTEM_MANAGED_FIELDS`），值缺失 / 为 `None` / 为空容器 / 等于占位符（"未指定"）即列为未指定。同一输入必须每次得到相同结果
- [x] 6.2 **停止透传** `parsed.unspecified_fields`（`app/agents/intake_agent.py:233`）。模型输出降级为 debug 日志对照，不进结果
- [x] 6.3 测试（真实数据反证，**2026-08-27 按 `.51` 真值订正后重述**）：用 `a478499c` 收尾时的已累积画像断言推导结果**不为空**（模型当时给的是空数组，漏报）；用 `19b6ec6d` 断言模型当时标出的 5 个字段（`headcount` / `functional_safety` / `soft_skill_keywords` / `toolchain` / `sop_projects`）**全部出现在**推导结果里——这一场模型判对了，系统推导一个都不许漏
      ~~原文：用 `19b6ec6d` 的断言 `functional_safety` / `sop_projects` **不在**结果里（模型当时虚报了用户已答的字段）~~
      ⚠️ **原文的「虚报」举证与真值不符，2026-08-27 Shao Peishen 拍板订正。** 核对 `.51` 真值：`19b6ec6d` 的 `functional_safety` / `sop_projects` 在**全部 6 个版本里都是 `None`**，用户从未答过——模型把它们列进未指定是**正确行为**，不是虚报。按原文写「断言这两个字段不在结果里」会强迫 `derive_unspecified_fields` 漏掉两个真实缺口，**与 6.1 直接矛盾**。
      落地：`tests/test_intake_agent.py::test_derive_catches_what_the_model_underreported_in_a478499c`（前半）与 `::test_derive_lists_every_field_the_model_flagged_in_19b6ec6d`（后半）。取数出处见 `tests/fixtures/pilot-replay-profiles.json` 的 `_provenance` 段。`design.md` 决策 6 的同一处举证已一并订正
- [x] 6.4 `field → 中文名` 映射放在 `app/schemas/job_profile.py`，紧邻字段定义（**不放前端**，design.md 决策 7）；补一条完整性测试：`JobProfile` 每个字段都必须有中文名，加字段漏改即失败
- [x] 6.5 API 返回未指定字段时同时返回中文名
- [x] 6.6 前端：确认按钮**上方**渲染视觉显著的警示块（不是对话流里的一行小字），列中文字段名，说明"留空则这些要求不会出现在 JD 里"；无未指定字段时不出现
- [x] 6.7 知情确认：`POST /api/jobs/{job_id}/confirm` 请求体加 `acknowledged_gaps: bool`；有未指定字段而该标记为 false 时返回 409 并附未指定字段（含中文名）。前端提供"回去补答"与"知道有缺口，仍然确认"两个动作
- [x] 6.8 "回去补答"使会话回到可继续作答状态，已采集内容保留
- [x] 6.9 知情确认留痕：确认时的未指定字段列表 + 知情标记写进 `job_profile.profile_json` 的下划线前缀内部键（与 `_jd_text` 同一位置，**不新建表**，design.md 决策 8）
- [x] 6.10 测试：未做知情选择不放行（409）；无缺口时确认流程与今天完全一致（不多一步点击）；知情确认后可从库里查回"确认时业务经理知道缺哪些字段"

## 7. 字段溯源与编造率度量（只观测不拦截）

对应 `intake-field-grounding` 全部四条要求。设计依据：design.md 决策 11、决策 12。依赖第 1 章（新增两列）。

> **并行性更正（2026-08-19，`delivery-units.md` §3.1）**：原写「可与第 3-6 章并行」**不成立**——本章同样要改 `intake_agent.py` / `graph/nodes.py`，与第 3、5、6 章重叠。本章＝交付单元 **F**，Shao Peishen 已定**排在 E 之后、G 之前**（按业务感受排；接受编造率 20 场样本的时钟晚开始一轮）。

**这一章存在的理由**：三位业务经理的编造检查全部答"无编造"，但反馈 2、3 贴的是 JD 文本截图而非"确认画像"页截图，反馈 1 连看的是哪一页都没记录——**这个核对动作一次都没有针对正确对象做过**（proposal.md「Why」第 5 条）。`deepseek-v4-pro` 实测 1/3 编造率，现已换 flash，flash 的真实编造率至今未知。本章把这件事从"靠人凭印象看"改成"系统算得出来"。

- [x] 7.1 `profile_patch` 的字段从裸值升级为「值 + 来源引用 + 来源轮次」（`value` / `source_quote` / `source_turn`）；来源字段全部可空，缺失即计未溯源，**不得**因缺失而抛校验失败（spec「来源结构缺失时降级而非报错」）
- [x] 7.2 `SYSTEM_PROMPT` 增加来源引用要求，给出正例（引用用户原话逐字片段）与反例（复述自己上一轮的问题、拼接不存在的句子）
- [x] 7.3 纯函数 `verify_field_grounding(patch, history) -> list[str]`：把 `source_quote` 与第 `source_turn` 轮用户原话都做统一归一化（空白折叠 + 全半角统一）后做子串判定，返回未溯源字段名列表。**确定性，不调模型**（design.md 决策 11）
- [x] 7.4 例外处理：系统管理字段（`_SYSTEM_MANAGED_FIELDS`）不参与校验；由用户点选候选档位产生的字段以被选中的档位标识作为来源，不要求在自由文本里找片段（依赖第 4 章的点选回传）
  - **(a) 系统管理字段豁免＝已实现**：`verify_field_grounding` 的 `exempt_fields` 参数，由 `run_intake_turn` 传入 `_SYSTEM_MANAGED_FIELDS`
  - **(b) 点选来源例外＝经核实不必单独实现**（不是漏做）。依据三条，均已实测复核：① 第 4 章落地形态是「点选文本原样拼进回复」——`app/web/static/index.html` 的 `collectSelections()` 拼成 `问题原文：档位A、档位B` 并入同一条 `message`，被选档位文本**逐字出现在该轮用户原话里**，7.3 的归一化子串判定天然命中；② 后端拿不到「哪些是点选的」信号——`ReplyRequest` 无 `selected_options`，第 4 章已立 `test_reply_api_contract_has_no_selected_options`（断言 `model_fields == {"message"}`）机械锁死，实现 (b) 必须先推翻这条已生效的跨单元约定；③ 不实现的代价为零。
  - 结论已用回归钉子钉住，**不是只写在文档里**：`tests/test_field_grounding.py::test_selected_option_is_grounded_without_a_special_case`（逐字复刻拼接格式的回放用例）+ `tests/test_static_frontend.py::test_collect_selections_format_still_matches_the_fixture`（只读探针，在 `collectSelections()` 函数体内断言 `block.dataset.qtext` / `"："` / `picked.join("、")` 三要素并存）。**探针将来若红，说明前端拼接格式变了、(b) 重新变成真问题——那是一次设计对话，不是一个可以删掉的测试。**
- [x] 7.5 **只观测不拦截**：未溯源字段照常写入画像，不丢弃、不清空、不阻断采集（design.md 决策 12）。未溯源清单与该轮 `response_model` 落进 1.1 加的两列
- [x] 7.6 测试（编造正例）：构造一段用户输入完全没提到 MCU 型号的会话，模型返回带 `ARM Cortex-M` 的字段——无论它给不给引用、引用是否为编的，该字段都必须被判为未溯源
- [x] 7.7 测试（归纳负例）：用户说"MISRA C"，字段值写成规范化枚举值但 `source_quote` 逐字命中——必须判为已溯源。**校验的是引用的真实性，不是值与引用的等价性**（design.md 决策 11）
- [x] 7.8 测试（降级）：模型返回的来源结构完全不合法时，采集仍然完成，该轮所有业务字段计入未溯源，不抛异常
- [x] 7.9 测试（归因）：留痕里记的是 API 响应返回的模型标识，不是配置里的别名；两者分开记录不互相覆盖（铁律 5）
- [x] 7.10 出一个统计口径（脚本或 SQL 片段即可）：按 `llm_response_model` 分组算「未溯源字段数 / 写入字段总数」。**这就是编造率的可复算定义**，写进 `docs/` 供后续对比
- [x] 7.11 登记技术债：**拦截策略待定**——本批上线后累计 ≥ 20 场真实采集会话、拿到未溯源率分布后，单独开变更定拦截阈值与降级方式（design.md 决策 12「触发条件」）。触发条件写死，避免这条永远悬着

## 8. 真实会话回放验证与上线

> **本章进度（2026-09-03 `0903I` 收工时）：67/69**（本变更包全量 69 条，已勾 67）。剩 **8.4**（⏸ 留步，需 Shao Peishen 在 `.51` 页面手工跑通）与 **8.9**（归档，须等 `m1-job-profile-intake` 先归档）。
>
> ⏸ **留步：`docs/session接力.md:40` 的进度行仍写着 `60/69`，实际已是 `67/69`。** 该文件不在 `0903H`／`0903I` 两条 opener 的 `git add` 白名单内，并行泳道也在动它，⛔ 两轮都未越界改。留给看护者或下一条一并订正（0903H 当时登记的是 64/69，此后 `0903I` 又勾了 8.6/8.7/8.8）。

- [x] 8.1 三段真实会话（`19b6ec6d` / `2494103e` / `a478499c`）的用户输入序列做端到端回放，对比修复前后：空转轮数、最终未指定字段数、总轮数、单轮 LLM 延迟
  - **2026-09-03 完成**（`0903H`）。脚本 `scripts/replay_pilot_sessions.py`，测试 `tests/test_replay_pilot_sessions.py`（只测解析与统计，真回放走 `REPLAY_LIVE` 门，默认 pytest 不联网）。对比表与 18 轮逐轮原始输出见 `docs/findings/2026-09-03-pilot会话回放对比.md`。三段各 6 轮全部跑通，模型响应回显 `deepseek-v4-flash` ×18。
  - ⚠️ **对比表里有三列标了「不可直接比」，不是偷懒**：① `question_id` 降级计数按上面的告警**根本没采**；② 「最终未指定字段数」修复前读 `unspecified_fields`（模型自称）、修复后读 `derived_unspecified_fields`（系统推导），定义不同源；③ 「空转轮数」修复前是**推导代理**（历史行的 `is_productive` 是加列常量默认 1，不是当时判出来的），代理偏严。
  - 🔴 **告警：`question_id_metrics()` 的计数不能拿来直接比（2026-08-28 Shao Peishen 裁决，明确挂在本条下、⛔ 不新开技术债条目）。** 单元 E 起 `build_question_ledger()` 会对**每一轮历史里的每一条问题**调一次 `IntakeQuestion.from_payload`，而 `from_payload` 一律丢弃传入的 `question_id`、重新走 `derive_question_id` 重派生（那一步是刻意的，⛔ 不要为这个计数器去改它）。于是**每一轮都会把整部问题史重新数一遍**。
    - **实测证据**：6 轮历史 × 每轮 3 条 ＋ 本轮 1 次真实派生 → `question_id_metrics()` 报 `total=19`，而这一轮真实发生的派生事件只有 **1** 次。
    - **两条后果，第二条更严重**：① 量级——R 轮会话累计 `O(q·R²/2)` 而不是 `O(q·R)`，绝对值整体虚高；② **权重**——第 i 轮问出的问题会被数 (R−i) 遍，越早的轮次被重复计得越多。
    - ⛔ **因此本条回放不得跨单元 E 直接比较 `total` / `null_field` / `unknown_field` 的原始计数，也不得直接比较它们的比例**——比例会被「降级发生在早轮还是晚轮」按**轮次位置加权**带偏，不是被一个常数放大，单元 E 之前与之后的数字不同源。
    - **真修法归属单元 G**：给 `derive_question_id` 加一个 `record_metrics=False` 入口（只给台账推导用）。现场说明见 `app/agents/intake_question.py:38-64`。
- [x] 8.2 本地在 `data/demo.db` 的**副本**上跑加列逻辑，确认既有 15 个 job 可正常读写
  - **2026-09-03 完成**（`0903H`），按下面那条登记的判据（「加列前后 job 数相等且断言不抛」）验收，⛔ 未追求字面上的「15 个」。副本＝`.51` `demo.db` 的 sqlite backup API 一致快照（带 4.2 MB `-wal`，直接 scp 会漏数据）。结果：**本次真正加上的列 `[]`**（`.51` 已发版 `d104249` 并启动过新代码，八列在服务器上早已补齐，副本上再跑只能空转——这同时验证了幂等）；**job 数 17，加列前后相等**；**17/17 个 job 的 `job_profile` 逐行读取通过、0 失败**，六个 JSON 列全部 `json.loads` 成功。
  - ⚠️ 实测 **17** 个 job，不是 15 —— 试点期间 `.51` 又新建了 2 个。⛔ 未改下面那条里的数字，订正仍归那条已登记的待办。
  - **⚠️ 与实测不符，待订正（2026-08-27 登记，不在本轮处置）**：本条把 `.51` 的 job 数安到了**本地库**头上。2026-08-27 只读实测本机 `data/demo.db`＝**5 个 job / 22 个 job_profile**；`15` 是 `.51` 现网的数字。单元 B 演练时实测同样是 5（`docs/superpowers/plans/2026-08-19-...unitA-storage-and-structured-questions.md` 已写明"本机 5 个 job、`.51` 上 15 个"）。⛔ **本条的验收判据本就不该是具体数字**，而是"加列前后 job 数相等且断言不抛"——照字面追求"15 个"会让本地这步永远过不了。订正时一并核 `delivery-units.md` 与 `design.md` 里同一数字的上下文（那几处指的是 `.51`，多数是对的，别误改）
- [x] 8.3 服务器上线前备份 `C:\apps\zhuopin-recruit-agent\data\demo.db`（含 `-wal` / `-shm`）
  - **已满足**：`0903D` 发版前已整目录快照 `C:\apps\backups\20260903-1003`（含 `data\`，`-wal` / `-shm` 在内）。本条另有一份 `0903H` 用 sqlite backup API 出的**一致快照**（`-wal` 已并入主库），已拉回本地 `data/replay/demo-51-20260903.db` 供回放用，服务器上的临时快照文件当场删除。
- [ ] 8.4 推送并重启后，用一个新 job 手工跑通"模糊回复 → 拿到档位 → 点选 → 带缺口确认"整条路径
  - ⏸ **留步（`0903H` 登记）**：需 Shao Peishen 在 `.51` 页面手工跑通，无人值守 session 做不了。⛔ 不勾。
- [x] 8.5 用 1.x 加的时序留痕核对：单轮 LLM 延迟没有因本批改动明显变差
  - **2026-09-03 已核对，结论是「⏸ 不可比」，⛔ 不是「通过」**（`0903H`）。修复前那三段**没有延迟基线**：`llm_latency_ms` 正是第 1 章新增的列，`ALTER TABLE ADD COLUMN` 对既有行只能填 `NULL`，08-13/08-18 跑的是加列前的代码。⛔ 不把「没有基线」折成「没有变差」——`latency_verdict()` 对这种情况硬返回「不可比」，并有测试钉住。
  - 回放侧实测（**可作为今后的基线**）：单轮均值 33.9 s / 48.5 s / 65.1 s，最大 132.1 s，18 轮全部单次尝试成功无重试。详见 `docs/findings/2026-09-03-pilot会话回放对比.md` §四。
- [x] 8.6 回填 `docs/m1-demo-pilot-feedback.md`：把「反馈 2、3 的调查」三条发现标注为已修复，附回放对比数据
  - **2026-09-03 完成**（`0903I`）。新增「回填（2026-09-03）：三条发现的处置结论」一节：三条发现逐条给「处置 → 归属章节 → 回放侧实测佐证」，数字全部照抄 `docs/findings/2026-09-03-pilot会话回放对比.md`，⛔ 未重算。
  - ⚠️ **"已修复"的判据写死在该节抬头了：代码已合入 main 且有测试钉住，⛔ 不等于"在真实经理手里验证过"**——那要等 8.4（仍留步，需 `.51` 手工跑）与下一轮 pilot。三条里只有 2b（空转轮）和 3（未指定字段）拿到了回放实测佐证；2a（换措辞重问）**回放刻意没采计数**（`question_id` 跨单元不可比，见 8.1 告警块）；1（截图对象错）是人的操作路径，回放证不了。
  - 对比表原样带上了 8.1 的三条不可比口径与 8.5 的「⏸ 不可比」判定，⛔ 未把"没有基线"折成"没有变差"。
  - 顺带登记一条回放暴露的新观察（**只登记不处置**）：反馈 1 全程没写进 `job_title`，首轮"一般材料采购"没被落成岗位名，6 轮 `written_fields` 合计只有 6 个字段。与 3.2 补的采购词条是两件事。
- [x] 8.7 用第 7 章的统计口径跑出本批的**首个真实编造率数字**（按 `llm_response_model` 分组），写进 `docs/m1-demo-pilot-feedback.md`——这是 flash 编造率从"未知"变成"已知"的那一步
  - **2026-09-03 完成**（`0903I`）。按 `docs/m1-fabrication-rate.md` 口径（分子 `ungrounded_fields` 计数 ÷ 分母 `written_fields` 计数，按**响应回显**的 `llm_response_model` 分组）：**`deepseek-v4-flash`，3 段会话 / 18 轮 / 写入 26 / 未溯源 0 → 未溯源率下界 0.00 %**（分会话 0/6、0/11、0/9）。⛔ 未用 `profile_json` 键数反推分母。
  - **只有 flash 一组**；`.51` 上 2026-08-19 之前的历史行分母为 0、`llm_response_model` 为 `NULL`，按口径 `NULLIF` 排除在比例之外，⛔ 未为它们编分母。
  - 🔴 **写进文档的四条限定，缺一条这个 0 就会被误读**：① 样本 3 场，决策 12 的门槛是 ≥20 场，⛔ 不可外推、⛔ 不得据此改拦截逻辑；② 0 是"下界为 0"，⛔ 不等于"没有编造"（三条已知偏低来源：去空白抹掉英文词边界、点选把全部档位原文拼进用户消息、单字引用无最小长度门槛）；③ **人工抽查这次没有对象**（分子为 0），⏸ 顺延到下批拿到非零分子时做，⛔ 不记功；④ 与 `deepseek-v4-pro` 的 1/3（人工核对口径）⛔ 不可相减，本表是同口径对比的"前"。
  - ⏸ **留步**：`docs/m1-fabrication-rate.md` §「首次真实测量（第 8 章 8.7 回填）」那张表仍是「待填」。该文件不在 `0903I` opener 的 `git add` 白名单内，⛔ 未越界改。回填内容＝上面那一行（`2026-09-03 | deepseek-v4-flash | 18 轮 | 26 | 0 | 0.00 % | 分子为 0，无对象可抽查`），留给下一条或看护者。
- [x] 8.8 改 `docs/m1-demo-manager-guide.md` 第 2 题：把"确认画像"整页截图的提示挪到追问对话最后一步旁边，不靠陪同人员口头提醒；截图对象说明要更难跳过（两位经理都贴成了 JD 截图，导致编造核对对象错了）。**只改文档，不改代码**
  - **2026-09-03 完成**（`0903I`），**只改了文档，⛔ 未碰代码**。三处改动：① 截图提示从文末「发送前必填①」**挪进正文「怎么用」**，成为新的第 4 步、紧贴追问对话结束那一刻，并写明"点了确认再回头截就补不回来了"；② 第 4 步与第 2 题各给一组 ✅要贴/❌不要贴 的**外观判据**（画像页＝逐条字段清单 + 确认按钮；JD＝有"岗位职责/任职要求"小标题的成段文案），并写出**为什么较这个真**（连贯文案里挑不出字段级编造）；③「发送前必填①」改为"首选经理自己在第 4 步截"，陪同人员只保留一件不可省的事——**收到表单先核实第 2 题那张图不是 JD，贴错当场退回重截**。
  - ⚠️ 效果**无法在本轮验证**：这条改的是人的操作路径，回放证不了，要等下一轮 pilot 有经理照新版跑一遍。⛔ 不因文档改完就认为核对动作已经做对。
  - ⏸ **顺带发现，⛔ 未改（超出本条"只动第 2 题"的范围）**：该文档「怎么用」第 3 步仍写"AI 会追问几个问题（**最多 5 轮**）"，而 3.10 已把预算改成 `MAX_ROUNDS=5`（只数**有产出轮**）＋ `MAX_TOTAL_ROUNDS=8`（数总轮），经理实际可能经历多于 5 轮。与 8.9 归档时要核的「"5 轮" vs "5 个有产出轮"两种矛盾表述」是同一个坑，一并订正。
- [ ] 8.9 归档顺序提醒：**`m1-job-profile-intake` 必须先归档，本变更后归档**；归档时人工核对「多轮追问补全 / 追问达到上限」两处，确保活文档里不留"5 轮"与"5 个有产出轮"两种矛盾表述（design.md「与 m1-job-profile-intake 的关系」）
