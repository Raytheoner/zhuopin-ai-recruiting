# 卓品智能 AI 招聘智能体 · 系统架构与 MVP 范围

> 日期：2026-08-06 ｜ 前置文档：`01-开源调研与技术选型.md`
> **已决策**：完全自研（不买 ATS）· Python/LangGraph · **分两段交付**（W1-8 简历闭环上线，W9-12 实时语音）· 5-6 人
> 状态：可进入 `/opsx:propose` 拆解实现

---

## 0. 已定事项速查

| 事项 | 决定 | 依据 |
|---|---|---|
| ATS 底座 | **不买**，一期手工导出批量上传 | HR 现状是 Excel+微信/邮件；年招几十到百人，导入成本极低；避免过早锁定供应商 |
| 技术栈 | Python + LangGraph | 编排层唯一为"持久化中断"原生设计的框架 |
| 工期与人力 | **W1-8 阶段一 + W9-12 阶段二**；4 人起，W3 加到 5-6 人 | 分段交付优于压缩：第 8 周就拿到真实收益 |
| 实时语音面试 | 做，但放**阶段二**（W9-12），prep/live/post 三段 | LiveKit Agents + FunASR + CosyVoice 级联；出题依赖阶段一跑出的真实数据 |
| 表情/情绪分析 | **不做** | 有效性不成立（面部动作↛情绪状态）+ 人脸识别办法合规风险 |
| 身份核验防替考 | 做，但与评分完全隔离 | 活体+证件比对，本地处理，单独同意，不参与任何打分 |
| 自动淘汰 | **规则硬门槛自动 + AI 排序后批量确认** | 规则可解释可申诉；AI 评分不做不可逆决策（PIPL 24 条） |

---

## 1. 架构分层

```
┌──────────────────────────────────────────────────────────────┐
│ L5 交互层                                                      │
│   业务经理提需求（企微对话）· HR 工作台（Web，含批量确认）        │
│   面试官评价页 · 候选人端（一次性邀请链接：补充信息/面试/材料）    │
├──────────────────────────────────────────────────────────────┤
│ L4 编排层  LangGraph ≥1.0.10 + Postgres Checkpointer           │
│   每个招聘流程 = 一个 thread_id                                 │
│   人工断点 = interrupt() → 落库挂起 → 企微卡片回调唤醒           │
│   铁律：每个有副作用的动作独占一个节点 + 幂等键                   │
├──────────────────────────────────────────────────────────────┤
│ L3 Agent 层（5 个，全部无副作用纯函数）                          │
│   ①需求解析 ②简历解析 ③匹配评分 ④面试出题(prep) ⑤面试评分(post) │
├──────────────────────────────────────────────────────────────┤
│ L2 能力层                                                      │
│   文档解析(PaddleOCR/MinerU) · LLM 网关(多供应商+版本锁定)       │
│   Embedding(BGE-M3) · 技能词表/ECU 知识库                       │
│   实时语音：LiveKit SFU · FunASR(ASR) · CosyVoice(TTS)          │
│   身份核验：活体检测 + 证件比对（本地，与评分链路物理隔离）        │
├──────────────────────────────────────────────────────────────┤
│ L1 数据层  Postgres + pgvector + 对象存储                       │
│   ATS 域模型 · 评分审计表 · 面试会话与转写 · checkpoint 表        │
├──────────────────────────────────────────────────────────────┤
│ L0 外部系统                                                     │
│   企业微信（通知+审批） · 日历 · （二期）招聘渠道对接              │
└──────────────────────────────────────────────────────────────┘
```

**核心设计原则**：L3 Agent 全部是无副作用的纯函数（输入 → 结构化输出），所有副作用（发消息、写库、建工单）由 L4 的独立节点执行。这样 LangGraph 从头重跑节点时不会重复发送。

**实时语音的延迟设计**：`prep` 阶段离线预生成题目、难度曲线、rubric 与**预埋追问**，因此 `live` 段只需跑轻模型做追问选择，不做重推理。这是把语音回路压进 800ms 的关键——延迟不由框架决定，由 live 段跑多重的模型决定。

---

## 2. 核心数据模型

借鉴自 OpenCATS（application 实体 + 流转事实表）、Odoo（stage 池 + job_ids）、Horilla（stage_type 语义标签）、Reqcore（AI 评分三件套 + JSON 自定义字段）。

### 2.1 ATS 域

```
job                    岗位
  ├─ id, title, dept, headcount, status
  └─ profile_json      岗位画像（需求解析 Agent 产出，人工确认后冻结）

candidate              候选人（全局唯一，人才库主体）
  ├─ id, name, phone, email, source
  └─ 注意：不要把状态挂在这里 ← Horilla 的坑

resume                 简历文件（一个候选人可有多份）
  ├─ id, candidate_id, file_uri, mime, uploaded_at
  ├─ parsed_json       结构化抽取结果
  ├─ text_spans        带 offset 的原文分片 ← evidence 回指的基础
  └─ parse_confidence, parser_version

application            投递（状态属于投递，不属于人）
  ├─ id, candidate_id, job_id, current_stage_id
  ├─ kanban_state      阶段内红黄绿
  └─ status            ongoing / hired / refused

stage                  阶段（全局池）
  ├─ id, name, sequence
  ├─ stage_type        initial|screening|test|interview|offer|hired|rejected
  └─ job_ids           空=全局 / 非空=限定岗位

application_stage_history   流转事实表 ← 所有报表的基础
  └─ application_id, stage_from, stage_to, at, actor_type(human|agent), actor_id

property_definition / property_value   ECU 行业特化字段（JSON，非 EAV）
```

### 2.2 评分与审计域（合规核心）

```
scoring_criterion      评分维度（按岗定义 rubric）
  └─ job_id, name, weight, max_score, description

hard_requirement       硬门槛规则（可自动过滤，必须可解释）
  ├─ job_id, field, operator, value, is_blocking
  └─ 例：{education, >=, 本科} {autosar_experience, contains, CP}

analysis_run           一次 AI 评估的完整快照
  ├─ application_id, run_type(screening|interview)
  ├─ model_id, model_version, prompt_version, temperature
  ├─ input_hash, raw_response, token_usage, created_at
  └─ rubric_snapshot   当时的 rubric 全量快照

criterion_score        逐维度得分
  ├─ analysis_run_id, criterion_id, score
  ├─ evidence_text     证据原文
  ├─ evidence_ref      {type: resume|interview_turn, id, start, end}
  ├─ confidence, strengths[], gaps[]
  └─ 约束：evidence_ref 非空才允许写入 ← 开源界都缺这一环

human_review           人工复核记录 ← PIPL 24 条留痕
  ├─ analysis_run_id, reviewer_id, decision, override_reason, at
  └─ batch_id          批量确认时同一批共用，用于区分逐份复核与批量决策

rejection_record       淘汰记录（区分两种来源）
  ├─ application_id, reason_type(hard_rule|human_decision)
  ├─ rule_id           硬门槛淘汰时指向具体规则，用于申诉时说明
  └─ appeal_status     申诉通道状态
```

> **绝不允许出现的一种记录**：`reason_type = ai_score`。AI 评分只能排序，不能成为淘汰依据。

### 2.3 面试域

```
interview_session
  ├─ id, application_id, mode(text|voice)
  ├─ prep_snapshot     预生成的题目/难度曲线/rubric/预埋追问
  ├─ invite_token, expires_at
  ├─ consent_at, consent_version   ← AI 面试单独同意留痕
  ├─ recording_uri, retention_until
  └─ status

interview_turn
  ├─ session_id, seq, question_id, question_text
  ├─ answer_text, audio_span(起止ms), latency_ms
  ├─ follow_up_of      追问指向哪个 turn
  └─ asr_confidence

identity_check         身份核验（与评分链路物理隔离）
  ├─ session_id, result(pass|fail|skipped), checked_at
  └─ 不存储人脸图像；不产生任何进入 criterion_score 的信号
```

**ScoreCard 复用 `analysis_run` + `criterion_score`**（`run_type=interview`），`evidence_ref` 指向具体 `interview_turn`。简历评分与面试评分共用一套审计与证据回指机制。

### 2.4 ECU 行业特化字段

所有开源项目都没有，必须结构化，不能塞进全文让 LLM 硬猜。

| 字段 | 类型 | 示例 |
|---|---|---|
| autosar_experience | enum[] | CP / AP / 无 |
| functional_safety | enum | ASIL-A/B/C/D / 无；FuSa 工程师认证 |
| mcu_family | enum[] | 英飞凌 Aurix TC3xx / NXP S32K / TI |
| diag_stack | enum[] | Bootloader / UDS / CAN-FD / LIN |
| sop_projects | object[] | {车型, SOP时间, 角色, 是否量产} |
| toolchain | enum[] | CANoe / CANape / IAR / Simulink / Vector |

---

## 3. MVP 范围

三个闭环全部进一期：**需求解析 → 简历评分排序 → 实时语音面试**。

### M1 · 需求解析与岗位画像

```
业务经理企微发起 →「一句话需求」
  → 需求解析 Agent 多轮追问（内置 ECU 术语知识库）
  → 产出结构化《岗位画像》草案 + 硬门槛规则草案
  → interrupt() 挂起，企微卡片推给业务经理
  → 确认 → 冻结画像 → 生成 JD（含 AI 生成标识）
```

**交付**：画像 schema、追问策略、硬门槛规则生成、企微审批闭环、JD 生成。
**验收**：10 个真实历史岗位重跑，画像技术栈字段人工评估准确率 ≥80%。
**为什么先做**：不依赖简历数据、不碰个人信息、能立刻验证 LangGraph 人工断点这条主链路，风险最低。

### M2 · 简历解析与评分排序

```
简历批量上传 → 解析 Agent(OCR + LLM schema 抽取)
  → 校验，低置信度进人工队列
  → 硬门槛规则自动过滤（可解释、写 rejection_record、开放申诉）
  → 匹配 Agent：BGE-M3 召回 → LLM rubric 精排（带 evidence span）
  → interrupt() 挂起 → HR 工作台复核
  → HR 逐份处理 top N ／ 批量勾选确认淘汰
```

**交付**：解析管线、200 份私有评测集、ECU 技能词表、硬门槛引擎、三段式匹配、审计全链路、HR 工作台（含批量确认）。
**验收**：
- 解析：私有评测集关键字段（姓名/年限/技能/公司）准确率 ≥90%
- 匹配：与 HR 人工排序的 Spearman 相关系数 ≥0.7；Top-10 召回率 ≥85%
- 合规：100% 评分可回溯到原文 span；`rejection_record` 中 `reason_type=ai_score` 的记录数必须为 0

**这一期是重头戏，也是唯一的差异化护城河。** 附带收益：人才库从零建起——现在候选人信息散落在 HR 个人微信和邮箱里，本身就是资产流失。

### M3 · 实时语音结构化面试（阶段二，W9-12）

**为什么不塞进前 8 周**：`prep` 阶段的出题质量依赖 M2 跑出来的真实数据。先用简历评分跑两周真实招聘，你会清楚知道哪些能力维度在简历上根本看不出来、必须靠面试问——那时候生成的题库才准。硬塞进阶段一，题库是拍脑袋的，而题库质量直接决定整个面试环节的价值。

三段式（prep / live / post），共享 `InterviewContext`。

```
prep（离线）  画像 + 简历弱点 → 预生成题目、难度曲线、rubric、预埋追问
              ↓
live（实时）  候选人点一次性链接 → 同意确认 → 身份核验（隔离）
              → LiveKit 房间：FunASR ↔ 轻模型追问选择 ↔ CosyVoice
              → 全程录制（证据链）
              ↓
post（离线）  转写对齐 → rubric 评分器 → ScoreCard（每条回指 interview_turn）
              → 面试官拿到「面试要点提示」进人工终面
```

**交付**：prep 出题引擎、LiveKit 语音链路、打断处理、ScoreCard、面试要点提示、身份核验、同意流程与录音留存策略。
**验收**：
- 语音回路端到端延迟中位 <800ms（分段预算：endpointing+ASR 150-300ms、LLM TTFT ~500ms、TTS 首帧 100-200ms）
- 候选人完成率 ≥70%
- 3 名面试官对同一批 ScoreCard 做一致性评估

**🚫 明确不做**：表情/情绪分析、声学情绪信号进评分、AI 自动淘汰。
> 语速、停顿、静默这类声学信号可以**作为参考信息展示给面试官**，但不进入 `criterion_score`。理由同表情分析：与能力的映射关系不成立，进了评分就是给噪声赋予权重。

### 明确不在 MVP 内

渠道对接与简历自动汇总、寻源发布、简历爬取、面试排期、Offer 生成、入职流程。
排期/Offer/入职是集成活不是 AI 活，等主链路验证完再接。

---

## 4. 开发顺序（分两段交付）

轨道：**A 编排 · B 解析 · C 匹配与评分 · D 数据与合规 · E 前端（W3 加入） · F 语音（阶段二加入）**

新增的 1-2 人**从 W3 开始收益最大** —— 前两周是地基期，并行度本来就有限，新人此时进来只会增加沟通开销。

### 阶段一 · W1-8：简历闭环上线

| 周 | A 编排 | B 解析 | C 匹配/评分 | D 数据/合规 | E 前端 |
|---|---|---|---|---|---|
| W1 | LangGraph 骨架、LLM 网关 | **模型对比测试**(1天) → 评测集标注启动 | ECU 技能词表 | 数据模型、PIA 启动 | — |
| W2 | 企微应用+回调、幂等框架 | 评测集完成（HR 出人标注） | 硬门槛规则引擎 | 审计表三件套、同意书条款 | — |
| W3 | **M1** 需求解析闭环 | OCR + schema 抽取 | BGE-M3 召回 | 邀请链接框架 | HR 工作台骨架 |
| W4 | M1 收口（含 JD 生成+AI标识） | 解析管线 | rubric 精排框架 | 合规文档 | 工作台主体 |
| W5 | M2 流程编排接线 | 解析调优 | **evidence span 回指** | 复核界面后端 | **批量确认 UI** |
| W6 | 编排收口 | 解析准确率 ≥90% | 排序调优 | 申诉通道 | 工作台完成 |
| W7 | 幂等与节点重跑专项测试 | 集成联调 | Spearman ≥0.7 验收 | **合规验收 #1**（阻塞上线） | 试运行准备 |
| **W8** | **🚀 真实岗位上线试运行 + 修** —— 第一个真实价值交付点 | | | | |

### 阶段二 · W9-12：实时语音面试

阶段一上线后，M2 在真实使用中持续积累评分数据——**这批数据用来校准阶段二的题库**。

| 周 | A 编排 | B+F 语音 | C 评分 | D 合规 | E 前端 |
|---|---|---|---|---|---|
| W9 | prep 出题引擎（用 W8 真实数据校准） | LiveKit/FunASR/CosyVoice 部署打通 | ScoreCard 评分器（复用 rubric 引擎） | 同意流程、录音留存策略 | 候选人答题端 |
| W10 | live 段追问策略 | 语音链路联调、打断处理 | 面试要点提示 | 身份核验（隔离链路） | 面试官界面 |
| W11 | 三段串接 | **延迟调优**（目标中位 <800ms） | 一致性回归 | **合规验收 #2** | 网络降级通道 |
| W12 | 全链路联调 | 压测 | 效果复盘 | 法务终审 | 真实岗位试运行 |

### 关键路径依赖（加人不会变快）

1. **W1 模型对比测试阻塞 M2 全部工作** —— 调研发现子代理给的型号已过时（Qwen3.5-Plus 已是旧版），且"豆包在 `json_schema` 下守字段接近 100%、DeepSeek/Qwen 仅支持 `json_object`"这条说法未经验证。选错型号会推倒重来。**必须第一周实测定型。**
2. **W2 私有评测集阻塞 M2 验收** —— 没有基线就无法判断解析器好坏。200 份简历标注是人工活，**要拉 HR 出人，别让工程师干**。
3. **W7 合规验收阻塞真实简历入库** —— PIA 必须事前做。
4. **W8 上线是硬节点** —— 若阶段一未能按时交付可用版本，**推迟阶段二，不要压缩阶段一的质量**。阶段二的价值本来就依赖阶段一跑出的真实数据。

---

## 5. 风险登记

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| W1 模型实测发现无合适型号，解析管线要改设计 | 中 | **高** | 第一周就测；备选切商用文档理解 API |
| LangGraph 节点重跑导致重复发送 | **高** | 高 | 铁律：副作用动作独占节点 + 幂等键；W7 专项测试 |
| 简历解析对 Boss/猎聘导出格式不达标 | 中 | 高 | W2 评测集提前暴露；备选切 MinerU 或商用 API |
| LLM 评分不稳定（同简历多次跑分不同） | 高 | 高 | temperature=0 + 版本锁定 + 一致性回归测试进 CI |
| **语音延迟压不到 800ms** | **中** | 高 | live 段只跑轻模型（prep 已预生成）；W10 起专项调优；降级预案=切文本异步 |
| 候选人网络差导致语音面试体验崩 | 中 | 中 | 必须有"切换到文本作答"的降级通道 |
| 候选人针对 JD 改写简历（AIHawk 类工具） | 中 | 中 | 压低关键词维度权重，加重可追问的具体项目细节；面试环节交叉验证 |
| PIPL 合规缺口被查 | 低 | **极高** | W7/W12 两道阻塞式合规验收 + 法务复核 |
| 多轨并行，集成期爆炸 | 中 | 中 | W1 先定接口契约；W7 强制集成节点 |
| 新人 W3 加入拖慢而非加快 | 中 | 中 | 新人接 E 前端轨（依赖最少、边界最清）；不让新人碰 C 匹配层 |
| 阶段一延期，阶段二被迫压缩 | 中 | 中 | 预设规则：**推迟阶段二，不压缩阶段一**；语音层可降级为文本异步 |

---

## 6. 合规执行清单（W7 / W12 验收项）

- [ ] PIA（个人信息保护影响评估）报告完成并存档，保存 3 年
- [ ] 候选人同意书：AI 自动化评估条款单列、AI 面试单独同意、身份核验单独同意、敏感信息独立勾选
- [ ] 人工复核申请入口（申诉通道）上线，`rejection_record.appeal_status` 可流转
- [ ] AI 生成的 JD、拒信、邀约话术带标识（《AI 生成合成内容标识办法》2025-09-01 施行）
- [ ] 全部模型调用走境内，无简历数据出境
- [ ] bias 回归夹具（改造 `re-cinq/hiring-bias`）纳入 CI，含盲筛对照轴
- [ ] 审计断言：`SELECT count(*) FROM rejection_record WHERE reason_type='ai_score'` 恒为 0
- [ ] 审计断言：`criterion_score` 中 `evidence_ref IS NULL` 的记录数恒为 0
- [ ] 录音留存期限与删除机制落实
- [ ] 候选人入口全部走一次性邀请链接（避免被认定"向境内公众提供"）
- [ ] **不使用历史录用结果做监督信号**（Amazon 2018 教训）
- [ ] 法务复核：人脸识别办法适用性、PIPL 24 条落地方式、（若有欧洲客户）EU AI Act 情绪识别禁令的供应链影响
