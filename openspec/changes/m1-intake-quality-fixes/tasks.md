> 章节粒度约定（CLAUDE.md「工具链分工」）：**一个章节 = 一份 superpowers plan = 一条 worktree 分支 = 一个可独立测试并合并的交付单元**。章节 checkbox 在该 plan 的 final review 通过后才勾。
>
> 依赖顺序：第 2 章是第 3/4/5 章的地基，必须先合。第 1 章是第 7 章的地基（新增两列），第 7 章的点选来源例外（7.4）依赖第 4 章。第 6 章可与其他章节并行。第 8 章最后。

## 1. 存储地基与逐轮时序留痕

对应 `intake-turn-observability`。设计依据：design.md 决策 9、决策 10。

- [ ] 1.1 在 `app/storage/db.py` 的 `init_schema` 里加幂等加列逻辑：读 `PRAGMA table_info(job_profile)`，缺列则 `ALTER TABLE job_profile ADD COLUMN`。本变更需要的新列：`is_productive INTEGER NOT NULL DEFAULT 1`、`turn_started_at TEXT`、`llm_latency_ms REAL`、`derived_unspecified_fields TEXT NOT NULL DEFAULT '[]'`、`ungrounded_fields TEXT NOT NULL DEFAULT '[]'`、`llm_response_model TEXT`（后两列服务第 7 章）。所有新列必须可空或有默认值，既有行不需要回填
- [ ] 1.2 加列逻辑的测试：对一个用**旧 schema** 建好并塞了数据的库跑 `init_schema`，断言新列出现、旧行可读、旧行的新列为默认值；重复跑第二次不报错（幂等）
- [ ] 1.3 让 `LLMGateway.extract_structured` 把已算出的 `latency_ms`（`app/llm/gateway.py:176-178`）与已取回的 `response_model`（`app/llm/gateway.py:184`）一并透出给调用方。**不得改动 `AuditHook` Protocol 的签名**（design.md 决策 9：`ai-audit-trail-and-outbound-gate` 正基于现签名设计）。含重试时记累计耗时
- [ ] 1.4 `IntakeState` 增加 `turn_started_at` / `llm_latency_ms`；`app/web/server.py` 的 `_run_turn` 在 invoke 前打时间戳并放进 state
- [ ] 1.5 `effect_persist_draft` 把时序两列与画像草案写在**同一次 INSERT** 里（spec 要求"时序与画像同生共死"）。不新增 effect 节点，不改 `business_key` 语义
- [ ] 1.6 测试：一轮采集完成后画像行上带 `turn_started_at` / `llm_latency_ms`；画像写入失败时时序留痕同样不存在；LLM 重试后耗时覆盖重试
- [ ] 1.7 在本仓库技术债清单里登记：**这两列在 `ai-audit-trail-and-outbound-gate` 的 `analysis_run` 落地后删除**，触发条件写明。不登记会导致两套时序数据长期并存互相矛盾（design.md 决策 9「边界」）

## 2. 结构化追问对象端到端透传

对应 `intake-guided-options` 的「结构化追问与可选项作答」与 `intake-question-tracking` 的「子问题的稳定标识与拆分」。设计依据：design.md 决策 1、决策 2。**本章只换载体，不把第 3/4/5 章的判定逻辑（`is_vague_reply`、选项填充、`is_productive`/`is_reask` 判定、`derive_unspecified_fields`、可点选控件）提前带进来**——这条约束本身仍然成立，也被本单元遵守了。

但本章不是行为零变化：**2.4 是一处授权的、影响可观察行为的 prompt 级改动**（tasks 2.4 明文要求"一个问题条目只能承载一个可独立作答的子问题"，对应的 spec 要求在覆盖矩阵里已为本单元标 ✅）。把复合问题拆成多条会让单轮消耗的 `MAX_QUESTIONS_PER_ROUND` 槽位变多，从而改变追问预算的消耗节奏——这是拆分规则本身带来的、意料之中的影响，不是判定逻辑被提前带入。该影响与第 3 章 `MAX_TOTAL_ROUNDS` 取值的关系见第 3 章开头的附注。

- [ ] 2.1 定义 `IntakeQuestion`：`question_id` / `text` / `field` / `options: list[str]` / `allow_free_text: bool` / `is_reask: bool`。除 `text` 外全部可空或有默认值（design.md 风险表第 1 条：模型退化时降级成纯文本问题，不报错）
- [ ] 2.2 `_IntakeTurnSchema.questions` 改为 `list[IntakeQuestion]`；确认 `_to_strict_json_schema` 能正确处理嵌套对象数组，必要时补 `app/llm/gateway.py` 的 schema 转换测试
- [ ] 2.3 `question_id` 由系统按 `field` 派生（**不让模型自己编 id**，design.md 决策 2）；模型只给 `field` / `text` / `options`。`field` 缺失时的兜底 id 规则要有测试
- [ ] 2.4 `SYSTEM_PROMPT` 增加"一个问题条目只能承载一个可独立作答的子问题"的约束，并给出反例（"是否需要熟悉 IATF 16949 或 ISO 26262？"必须拆成两条）
- [ ] 2.5 唯一的问题→文本渲染函数：`compute_intake_turn` 写 history 的 assistant 内容与下发给通道的文本必须出自同一个函数（design.md 决策 1「代价」）。两处各渲染一遍会让 `_repeats_earlier_assistant_turn` 比对到与实际下发不一致的文本
- [ ] 2.6 `OutboundMessage` 的 `question` payload 从 `{"questions": [str]}` 改为携带结构化问题列表；`app/web/server.py` 的响应体同步
- [ ] 2.7 前端 `index.html` 的 `renderMessage` 适配新 payload，本章仍只渲染文本（选项控件在第 4 章）
- [ ] 2.8 回归测试：既有采集流程测试全绿；一轮追问的端到端响应体结构符合新契约

## 3. 模糊回复兜底与领域选项库

对应 `intake-guided-options` 的「模糊回复与反问的兜底档位」「候选档位不得代替用户做决定」「零产出轮不消耗追问预算」。设计依据：design.md 决策 3、4、5。依赖第 2 章。

> **附注（unit A 收尾复核记录，写给 3.10 取 `MAX_TOTAL_ROUNDS` 值的人）**：第 2 章 2.4 引入了"一个问题条目只能承载一个可独立作答的子问题"的拆分规则——像"是否需要熟悉 IATF 16949 或 ISO 26262？"这类复合问题会被拆成两条，各占一个 `MAX_QUESTIONS_PER_ROUND` 槽位。合并后同一个话题平均消耗的问题槽位数会增加，`MAX_ROUNDS`（有产出轮计数）固定的情况下，收尾前能覆盖的独立话题数会变少，更多字段被推入"未指定"。取 `MAX_TOTAL_ROUNDS`（3.10）时请把这一层消耗算进去，不要只按拆分前的历史会话估算。

- [ ] 3.1 `app/agents/ecu_knowledge.py` 的 `FOLLOWUP_RULES` 从 `list[str]` 升级为 `list[FollowupSpec]`（`text` / `field` / `options`），既有 4 个词条补齐 `field` 与档位
- [ ] 3.2 新增采购/非 ECU 侧词条：一般材料、办公采购、非标产品、供应商开发。**姚祖怡那场就是卡死在"一般材料"上**——知识库当时一个采购词条都没有（design.md 决策 4）
- [ ] 3.3 纯函数 `is_vague_reply(text) -> bool`：模糊表态词表（不知道 / 不太了解 / 你决定 / 随便 / 你看着办 / 你有什么建议 / 都行…）+ 反问模式（以问号结尾且不含目标字段线索）。**确定性判定，不调模型**
- [ ] 3.4 命中模糊回复时的强制兜底：本轮下发的问题必须带 `options`；模型没给就由系统从领域选项库补；库里没有就用该字段的通用档位（必须含"无要求 / 不限"这类明确否定档位）
- [ ] 3.5 `SYSTEM_PROMPT` 现有的「回答模糊/不知道时怎么办」段（`app/agents/intake_agent.py:92-100`）保留为第二道，但**判定与注入由代码保证**——这次事故本身就是"提示词说了、模型没做"（design.md 决策 3）
- [ ] 3.6 测试（真实回放）：喂入 `19b6ec6d` 第 4 轮的"这些我不太了解，你有什么建议"与 `a478499c` 第 5 轮的"一般材料是什么，你都不知道吗"，断言产出的问题**必定带 2-3 个具体档位**，且回复中不出现无档位的"我来帮您整理"式内容
- [ ] 3.7 测试（合规红线）：用户回"你决定吧"时，画像对应字段**保持未确定**，任何候选档位都不得进入 `profile_patch`（proposal.md「合规影响说明」：AI 不得代替业务经理做决定）
- [ ] 3.8 测试（误判安全）：`is_vague_reply` 误判一条有效回答时，模型已提取到的字段**不被清空**（design.md 风险表第 2 条）
- [ ] 3.9 零产出轮判定：`compute_intake_turn` 算出 `is_productive`（本轮 `profile_patch` 相对上轮有新字段 **或** 问出了未问过的 `question_id`），落进 1.1 加的列
- [ ] 3.10 预算取数改口径：`app/web/server.py` 的 `round_count` 拆成两个 —— `MAX_ROUNDS`(5) 对 `is_productive=1` 计数，新增 `MAX_TOTAL_ROUNDS`(8) 对总行数计数，任一命中即收尾。**`business_key` 继续用总行数，幂等语义不变**（design.md 决策 5）
- [ ] 3.11 测试：空转轮不减预算；有产出轮正常减预算；连续空转触顶 `MAX_TOTAL_ROUNDS` 后正常收尾进确认流程

## 4. 前端可点选选项

对应 `intake-guided-options` 的「结构化追问与可选项作答」在 Web 通道的落地。依赖第 2、3 章。

- [ ] 4.1 `index.html` 按 `options` 渲染可点选控件（原生 DOM，不引入框架），保留自由文本输入；`options` 为空时只渲染自由文本，不渲染空控件
- [ ] 4.2 点选结果构造该轮回复文本并提交，**不要求用户复制粘贴或改写系统的问题文本**（这是经理原话抱怨的点）
- [ ] 4.3 选项区标明"以下为 AI 建议选项"——《AI 生成合成内容标识办法》要求（proposal.md「合规影响说明」）
- [ ] 4.4 所有请求继续走相对路径，不硬编码 `/static/…` `/api/…`（部署约束 1）；验证挂在 `root_path` 子路径下仍正常
- [ ] 4.5 测试：点选提交（无文字输入）能推进对话且选中内容进入画像；只写自由文本不点选也能推进，不因"未点选"被拒

## 5. 已问未答追踪与诚实重问

对应 `intake-question-tracking` 的「已问未答的判定」「重问必须显式标注」「重问次数上限」。设计依据：design.md 决策 2。依赖第 2 章。

- [ ] 5.1 `IntakeState` 增加已问子问题台账（`question_id` → 已问轮次 / 是否已答 / 重问次数）；真源随画像落库，不只活在 checkpoint（对齐 `app/graph/state.py` 既有约定）
- [ ] 5.2 已答判定：用户回复后判定本轮覆盖了哪些已问 `question_id`；未覆盖的保持"已问未答"
- [ ] 5.3 空转轮的状态处理：整轮无字段产出时，本轮之前已问的子问题**全部保持已问未答**，不得被标记为已答
- [ ] 5.4 重问时置 `is_reask=true`，渲染层加显式重问提示（"这个你刚才没答"一类）；重问条目与新问题在界面上可区分，**不得混编成看起来是新问题的一句话**
- [ ] 5.5 重问次数上限取 2（问 1 次 + 重问 2 次）；超限即停止追问该子问题并把目标字段计入未指定字段，不再消耗追问轮次
- [ ] 5.6 测试（真实回放）：用 `2494103e` 第 3-4 轮的 IATF 16949 / ISO 26262 序列，断言 ISO 26262 被判为已问未答、重问时带重问标注、且 `question_id` 与首问一致（换措辞不改 id）
- [ ] 5.7 测试：`question_id = field` 撞 id 的递进提问（"要不要 26262" → "要哪个 ASIL"）在上限 2 之内不会被过早掐断（design.md 风险表第 3 条）
- [ ] 5.8 `_repeats_earlier_assistant_turn` 与新机制的关系交代清楚：保留作为最后一道逐字防线，还是由 `question_id` 追踪取代——在实现里给出结论并写进注释

## 6. 未指定字段推导与确认前警示

对应 `intake-completeness-warning` 全部三条要求。设计依据：design.md 决策 6、7、8。可与第 3-5 章并行。

- [ ] 6.1 纯函数 `derive_unspecified_fields(accumulated: dict) -> list[str]`：遍历 `JobProfile.model_json_schema()` 属性（排除 `_SYSTEM_MANAGED_FIELDS`），值缺失 / 为 `None` / 为空容器 / 等于占位符（"未指定"）即列为未指定。同一输入必须每次得到相同结果
- [ ] 6.2 **停止透传** `parsed.unspecified_fields`（`app/agents/intake_agent.py:233`）。模型输出降级为 debug 日志对照，不进结果
- [ ] 6.3 测试（真实数据反证）：用 `a478499c` 收尾时的已累积画像断言推导结果**不为空**（模型当时给的是空数组，漏报）；用 `19b6ec6d` 的断言 `functional_safety` / `sop_projects` **不在**结果里（模型当时虚报了用户已答的字段）
- [ ] 6.4 `field → 中文名` 映射放在 `app/schemas/job_profile.py`，紧邻字段定义（**不放前端**，design.md 决策 7）；补一条完整性测试：`JobProfile` 每个字段都必须有中文名，加字段漏改即失败
- [ ] 6.5 API 返回未指定字段时同时返回中文名
- [ ] 6.6 前端：确认按钮**上方**渲染视觉显著的警示块（不是对话流里的一行小字），列中文字段名，说明"留空则这些要求不会出现在 JD 里"；无未指定字段时不出现
- [ ] 6.7 知情确认：`POST /api/jobs/{job_id}/confirm` 请求体加 `acknowledged_gaps: bool`；有未指定字段而该标记为 false 时返回 409 并附未指定字段（含中文名）。前端提供"回去补答"与"知道有缺口，仍然确认"两个动作
- [ ] 6.8 "回去补答"使会话回到可继续作答状态，已采集内容保留
- [ ] 6.9 知情确认留痕：确认时的未指定字段列表 + 知情标记写进 `job_profile.profile_json` 的下划线前缀内部键（与 `_jd_text` 同一位置，**不新建表**，design.md 决策 8）
- [ ] 6.10 测试：未做知情选择不放行（409）；无缺口时确认流程与今天完全一致（不多一步点击）；知情确认后可从库里查回"确认时业务经理知道缺哪些字段"

## 7. 字段溯源与编造率度量（只观测不拦截）

对应 `intake-field-grounding` 全部四条要求。设计依据：design.md 决策 11、决策 12。依赖第 1 章（新增两列）。可与第 3-6 章并行。

**这一章存在的理由**：三位业务经理的编造检查全部答"无编造"，但反馈 2、3 贴的是 JD 文本截图而非"确认画像"页截图，反馈 1 连看的是哪一页都没记录——**这个核对动作一次都没有针对正确对象做过**（proposal.md「Why」第 5 条）。`deepseek-v4-pro` 实测 1/3 编造率，现已换 flash，flash 的真实编造率至今未知。本章把这件事从"靠人凭印象看"改成"系统算得出来"。

- [ ] 7.1 `profile_patch` 的字段从裸值升级为「值 + 来源引用 + 来源轮次」（`value` / `source_quote` / `source_turn`）；来源字段全部可空，缺失即计未溯源，**不得**因缺失而抛校验失败（spec「来源结构缺失时降级而非报错」）
- [ ] 7.2 `SYSTEM_PROMPT` 增加来源引用要求，给出正例（引用用户原话逐字片段）与反例（复述自己上一轮的问题、拼接不存在的句子）
- [ ] 7.3 纯函数 `verify_field_grounding(patch, history) -> list[str]`：把 `source_quote` 与第 `source_turn` 轮用户原话都做统一归一化（空白折叠 + 全半角统一）后做子串判定，返回未溯源字段名列表。**确定性，不调模型**（design.md 决策 11）
- [ ] 7.4 例外处理：系统管理字段（`_SYSTEM_MANAGED_FIELDS`）不参与校验；由用户点选候选档位产生的字段以被选中的档位标识作为来源，不要求在自由文本里找片段（依赖第 4 章的点选回传）
- [ ] 7.5 **只观测不拦截**：未溯源字段照常写入画像，不丢弃、不清空、不阻断采集（design.md 决策 12）。未溯源清单与该轮 `response_model` 落进 1.1 加的两列
- [ ] 7.6 测试（编造正例）：构造一段用户输入完全没提到 MCU 型号的会话，模型返回带 `ARM Cortex-M` 的字段——无论它给不给引用、引用是否为编的，该字段都必须被判为未溯源
- [ ] 7.7 测试（归纳负例）：用户说"MISRA C"，字段值写成规范化枚举值但 `source_quote` 逐字命中——必须判为已溯源。**校验的是引用的真实性，不是值与引用的等价性**（design.md 决策 11）
- [ ] 7.8 测试（降级）：模型返回的来源结构完全不合法时，采集仍然完成，该轮所有业务字段计入未溯源，不抛异常
- [ ] 7.9 测试（归因）：留痕里记的是 API 响应返回的模型标识，不是配置里的别名；两者分开记录不互相覆盖（铁律 5）
- [ ] 7.10 出一个统计口径（脚本或 SQL 片段即可）：按 `llm_response_model` 分组算「未溯源字段数 / 写入字段总数」。**这就是编造率的可复算定义**，写进 `docs/` 供后续对比
- [ ] 7.11 登记技术债：**拦截策略待定**——本批上线后累计 ≥ 20 场真实采集会话、拿到未溯源率分布后，单独开变更定拦截阈值与降级方式（design.md 决策 12「触发条件」）。触发条件写死，避免这条永远悬着

## 8. 真实会话回放验证与上线

- [ ] 8.1 三段真实会话（`19b6ec6d` / `2494103e` / `a478499c`）的用户输入序列做端到端回放，对比修复前后：空转轮数、最终未指定字段数、总轮数、单轮 LLM 延迟
- [ ] 8.2 本地在 `data/demo.db` 的**副本**上跑加列逻辑，确认既有 15 个 job 可正常读写
- [ ] 8.3 服务器上线前备份 `C:\apps\zhuopin-recruit-agent\data\demo.db`（含 `-wal` / `-shm`）
- [ ] 8.4 推送并重启后，用一个新 job 手工跑通"模糊回复 → 拿到档位 → 点选 → 带缺口确认"整条路径
- [ ] 8.5 用 1.x 加的时序留痕核对：单轮 LLM 延迟没有因本批改动明显变差
- [ ] 8.6 回填 `docs/m1-demo-pilot-feedback.md`：把「反馈 2、3 的调查」三条发现标注为已修复，附回放对比数据
- [ ] 8.7 用第 7 章的统计口径跑出本批的**首个真实编造率数字**（按 `llm_response_model` 分组），写进 `docs/m1-demo-pilot-feedback.md`——这是 flash 编造率从"未知"变成"已知"的那一步
- [ ] 8.8 改 `docs/m1-demo-manager-guide.md` 第 2 题：把"确认画像"整页截图的提示挪到追问对话最后一步旁边，不靠陪同人员口头提醒；截图对象说明要更难跳过（两位经理都贴成了 JD 截图，导致编造核对对象错了）。**只改文档，不改代码**
- [ ] 8.9 归档顺序提醒：**`m1-job-profile-intake` 必须先归档，本变更后归档**；归档时人工核对「多轮追问补全 / 追问达到上限」两处，确保活文档里不留"5 轮"与"5 个有产出轮"两种矛盾表述（design.md「与 m1-job-profile-intake 的关系」）
