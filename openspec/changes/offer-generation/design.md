## Context

动机见 `proposal.md`「Why」；需求收敛结论见同目录 `intent.md`（D1–D11，G1 Q-34 放行）。本节只写约束现状。

**现有地基**

- 编排：LangGraph，`compute_*` / `effect_*` 分离，SqliteSaver；幂等键落 `effect_log` 与业务写同事务（`effect-transaction-integrity`）
- ATS 域（M2 U1）：`application.status ∈ {ongoing, hired, refused}`、`stage.stage_type` 含 `offer / hired`（预置行本包补）、`application_stage_history`、`rejection_record`（`reason_type` CHECK IN `hard_rule / human_decision`）
- 外发门禁（M1 U5）：`app/outbound/contracts.py` 已登记类型集合 `{rejection_letter, interview_invitation}`；未登记类型 fail-closed 拦截；总开关默认关；生产无调用方（TD-8）
- AI 标识与"标记为人工撰写"：M1 JD 形态（TD-JD-2，留痕在 `profile_json._jd_authorship`，`human_review.decision_type` CHECK 改不了）
- `python-docx`：M2 已引入
- 鉴权：M2 D12 本地账号，可识别到人
- 联系方式：M2 只哈希；面试阶段明文保管由 `interview-scheduling` 的 `candidate-contact-vault` 提供
- 规模：年招几十到百人，Offer 量两位数／年

**外部依赖现状**

- 人事部#3 在途：Offer 模板与必填字段、审批链、拒信话术与时机、答复现状
- 电子签／短信通道：不采购（Q1b／Q4a 已定），本包不需要

## Goals / Non-Goals

**Goals:**

- Offer 与拒信共用一套"模板 → 生成 → 标识 → 导出／复制 → 回填"引擎，一个单元做完
- 录用决定在结构上只能由人做：审批链留痕、`hired` 流转 `actor_type=human`、页面无 AI 录用建议
- 成为 `rejection_letter` 的第一个真实调用方，并把 `offer_letter` 登记进门禁

**Non-Goals:**

- 不做薪资计算、薪酬带宽校验
- 不做电子签、短信
- 不做候选人自助答复入口
- 不做入职清单（S-入职）

## Decisions

### 决策 D1：范围＝文书生成＋内部审批（对应 intent D8）

**做法**：Offer 记录 → 审批链（工作台）→ 通过后生成文书 → 导出 docx → HR 发送 → 回填。系统外发路径保留但总开关默认关。

**为什么**：intent Q1b 定；对候选人发送与签收（Q1c）需要电子签采购与对外通道，两者都是不可代项，绑进来会让整包挂起。

### 决策 D2：薪资等敏感字段不入库（对应 intent D9）

**做法**：`offer` 表只有 `job_id / department / start_date / report_to / note`；docx 模板薪资处留空；模板保存时校验不得含薪资类占位符；断言：`offer` 表列名不匹配薪资关键词。`note` 自由文本页面提示"不得填薪资"，⛔ 不做内容审查（做不准）。

**为什么**：intent Q2a 定；报酬信息一旦入库，PIA 范围、访问控制与留存策略都要升一档，而年 Offer 量两位数，HR 手填成本可忽略。

**替代方案**：加密入库（同 S-排期联系方式）。否决——联系方式是"发出邀约必需"，薪资对系统流程不是必需，最小必要原则下不收。

### 决策 D3：拒信并入本包，与 Offer 共用文书引擎（对应 intent D10）

**做法**：`letter_template.kind ∈ {offer, rejection}`，`candidate_letter.kind` 同；`compute_letter_draft(kind, template, facts)` 一个纯函数；拒信生成前置校验该投递存在 `rejection_record`；拒信 ⛔ 不在批量确认时自动生成（HR 显式触发）。

**为什么**：intent Q3a 定；两类文书的生成、标识、留痕、导出、门禁接线完全同构，分两包只会重复。拒信不自动生成是因为"哪些情形不发拒信"是业务口径（待专员），系统不替 HR 决定。

### 决策 D4：候选人答复 HR 人工回填（对应 intent D11）

**做法**：`POST /offers/{id}/outcome`，`accepted(start_date) / declined(reason) / negotiating(note)`；`negotiating` 可再转 `accepted/declined`；前置＝已导出过 docx。

**为什么**：intent Q4a 定；一次性链接自助答复属对外通道，不可代。

### 决策 D5：流转与终态（对应 intent D5、D4）

**做法**：`effect_apply_offer_outcome` 节点：`accepted` ⇒ `stage=hired`、`application.status=hired`；`declined` ⇒ `stage=interview`、`status=ongoing`；HR 另点"终止投递" ⇒ `status=refused`（不写 `rejection_record`——那是我方淘汰）。三写（`offer`、`application`、`history`）同事务，幂等键 `{application_id}:effect_apply_offer_outcome:{offer_id}:{outcome}:{request_id}`。

**为什么**：`refused` 在 `02` §2.1 的语义就是候选人拒绝；`rejection_record` 语义是我方淘汰，混用会污染"AI 不淘汰"断言。

### 决策 D6：审批链是岗位级配置，审批在 Web 工作台（对应 intent D6、D8）

**做法**：`offer_approval_chain`（`job_id → [level, approver_account_ids[]]`），HR 维护；`offer_approval` 每级一行；`effect_record_approval` 幂等键 `{offer_id}:effect_record_approval:{level}:{approver}:{decision}`；退回 ⇒ `offer.status=needs_revision`，HR 改后链从第一级重走（新一轮 `round` 号）。审批待办用工作台列表页 ＋ 既有内部通知（不受候选人门禁约束），⛔ 不做企微卡片。

**为什么**：企微卡片审批需自建应用＋公网回调，`.51` 无公网；审批链是待专员项，先做成配置让回件到后只改数据。

### 决策 D7：AI 标识与"人工撰写"留痕沿 TD-JD-2 形态，但落在本包自己的表

**做法**：`candidate_letter.ai_generated BOOL`、`authorship_marked_by / at / from_version`；⛔ 不碰 `human_review.decision_type` CHECK（SQLite 改不了）。docx 导出在页眉写标识文字。

**为什么**：TD-JD-2 的教训是 CHECK 改不动；本包新表自己带列，不再往 JSON 内部键里塞。

### 决策 D8：门禁类型登记 `offer_letter`（对应 intent D2）

**做法**：`app/outbound/contracts.py` 的已登记集合加 `offer_letter`，风险等级最高级、需确认；门禁判定逻辑一行不改；delta spec 修改「门禁覆盖范围」。调用方 `effect_enqueue_letter` 用 `deliver_candidate_message(type, recipient=<contact-vault 拍平字符串>, confirmed_by)`。

**为什么**：门禁 spec 明写"未登记类型即拦截"——不登记就永远发不出，登记是让门禁按已知高风险处理而不是按畸形处理。

### 决策 D9：交付单元与顺序

| 顺序 | 单元（tasks 章） | 内容 | 前置 |
|---|---|---|---|
| 1 | U1 Offer 域模型（第 1 章） | `letter_template` / `candidate_letter` / `offer` / `offer_approval_chain` / `offer_approval` / `letter_access_log`；`stage` 预置 `offer` / `hired` | M2 U1 |
| 2 | U2 文书引擎（第 2 章） | 模板维护、生成纯函数、标识、导出 docx、访问留痕 | U1 |
| 3 | U3 审批流（第 3 章） | 审批链配置、逐级审批节点、退回重走、审批页 | U1；登录可识别 |
| 4 | U4 门禁接线（第 4 章） | `offer_letter` 登记、delta spec、`effect_enqueue_letter`、拒信页 | U2；M2 `rejection_record` |
| 5 | U5 答复与流转（第 5 章） | 回填、`hired`／回退节点、不变式测试、断言、发版（🔴 G3） | U3、U4 |

## Risks / Trade-offs

- [人事部#3 回件未到，模板与审批链缺业务真值] → U1 用占位模板与"一级审批＝业务经理"默认链上线，回件到后只改数据不改代码
- [`stage` 预置 `offer` / `hired` 与 M2 预置三行的顺序冲突] → 幂等插入，按 `stage_type` 判存在；测试固定 M2 三行不变
- [HR 在 `note` 里填了薪资] → 页面提示 ＋ 操作说明明写；不做内容审查；登记为已知残余风险
- [拒信"哪些情形不发"口径未定，HR 误发] → 系统不自动生成，每封拒信 HR 显式触发；拒信页显示该投递的淘汰理由类型供 HR 判断
- [`refused` 与 `rejection_record` 的语义被后续开发混淆] → 断言：`application.status='refused'` 的投递不因本包新增 `rejection_record` 行；操作说明明写
- [docx 导出在 Windows 上字体／模板差异] → U2 在 `.51` 同款环境冒烟一份导出件

## Migration Plan

1. U1 新表 `CREATE TABLE IF NOT EXISTS`；`stage` 幂等追加 `offer` / `hired`
2. `contracts.py` 类型集合扩为三类——纯代码变更，无数据迁移；既有门禁测试全部原样通过
3. 发版：U1–U3 合并后可先发一次（HR 能发起、审批、导出）；U4–U5 合并后再发；系统外发总开关保持关闭；每次 🔴 G3
4. 回滚：代码回退上一版；新表不删；`offer_letter` 若回退则门禁重新按未知类型拦截，安全方向

## Open Questions

> 以下为 `intent.md`「待专员」表与「外部依赖」小节里尚未闭环的项，原样转入；⛔ 不阻塞 U1–U3 代码，只影响模板内容、审批链数据、拒信时机口径与开闸。**当前条数：7。**

- **OQ1 现用 Offer 模板与必填字段**（待专员）：影响 U2 模板 v1 内容与 `offer` 表是否需要补非敏感字段；判据＝现用模板（脱敏薪资）1–2 份到位
- **OQ2 审批链：谁签、几级、哪些岗位要总经理签**（待专员）：影响 U3 审批链初始配置；判据＝近 1 年 ≤10 个 Offer 审批路径（脱敏）到位
- **OQ3 现用拒信话术与发送时机（哪些情形不发拒信）**（待专员）：影响 U4 拒信模板 v1 与拒信页的提示口径；判据＝现用拒信 2–3 条（脱敏）到位
- **OQ4 Offer 答复现状：接受率、谈判常见项（判「异常」的口径）**（待专员）：影响 U5 回填页"谈判中"的备注结构；判据＝近 1 年统计到位
- **OQ5 登录＋访问留痕就位时点**（外部依赖）：审批人可识别到人的前置；判据＝M2 tasks 3.2／3.9 勾选并发版
- **OQ6 S-排期 `candidate-contact-vault` 交付与开启时点**（外部依赖）：系统外发路径的收件对象来源；未就位时系统外发按收件对象未知拦截，HR 自行发送不受影响；判据＝`interview-scheduling` tasks 4.8 勾选
- **OQ7 电子签／短信通道采购**（外部依赖，🔴 不可代）：本包不需要（Q1b／Q4a）；仅当将来改为对候选人系统发送并签收时重提；本包留墓碑
