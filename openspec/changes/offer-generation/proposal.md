## Why

面试通过之后，Offer 文书今天靠 HR 从旧 Word 里改，谁批过、批的是哪一版、候选人什么时候答复的，都只在微信记录里；拒信更是想起来才发。系统里 `stage_type` 早就预留了 `offer` / `hired`，外发门禁也已登记了 `rejection_letter`，但两者至今没有任何调用方（TD-8）。本包把"生成候选人文书 → 内部审批留痕 → HR 导出发送 → 回填答复 → 流转到 `hired` 或回退"接到既有 `application` 上，并让拒信与 Offer 共用同一套文书引擎与门禁接线。

为什么是现在：路线图波次 5，顺序在 S-排期之后、S-入职之前（Offer 接受是入职的入口）；M2 已把淘汰确认与 `rejection_record` 建好，拒信只差"生成文书"这一步。需求收敛结论见同目录 `intent.md`（D1–D11，Shao Peishen 2026-09-17 G1 放行 Q-34），design.md 的 Decisions 逐条对应。

## What Changes

- 新增 **候选人文书引擎**：Offer 与拒信共用模板引擎，按模板与投递信息生成草稿（带 AI 生成标识、留痕、版本化），导出 docx（D10、D3）
- 新增 **Offer 记录**：挂在 `application` 上，只存岗位／部门／入职日期／汇报对象／模板版本／审批状态／答复；**薪资等敏感字段不入库**，由 HR 在导出的 docx 上手填（D9）
- 新增 **内部审批流**：业务经理／决策人在 Web 工作台审批，审批通过并留痕后才可导出；⛔ 不做企微卡片审批（D8、D6）
- 新增 **答复回填与流转**：HR 人工回填接受／拒绝／谈判中；接受 ⇒ `stage_type=hired`、`application.status=hired`；拒绝 ⇒ 回 `ongoing` 或按 HR 决定 `refused`，均写 `application_stage_history`（D11、D5）
- 新增 **门禁类型登记 `offer_letter`**：若系统对候选人发送 Offer，先在外发门禁类型清单登记 `offer_letter`（高风险、需人工确认），与既有 `rejection_letter` 一起经 `deliver_candidate_message()`；总开关默认关（D2）
- 新增 **拒信生成接线**：对 M2 批量确认淘汰的投递生成拒信草稿（带标识），HR 复制发送或经门禁系统外发（D10）

## Capabilities

### New Capabilities

- `candidate-letter-engine`：Offer／拒信共用的模板管理、草稿生成、AI 生成标识与"人工撰写"留痕、docx 导出、版本化
- `offer-record-and-approval`：Offer 记录字段（敏感字段不入库）、内部审批链与留痕、审批通过才可导出
- `offer-outcome-and-transition`：候选人答复的人工回填、`hired`／回退流转事实、AI 不做录用决定
- `candidate-letter-outbound`：Offer／拒信经既有外发门禁的接线；`offer_letter` 类型登记；默认 HR 复制发送

### Modified Capabilities

- `outbound-approval-gate`：「门禁覆盖范围」要求新增 `offer_letter` 为第三类受门禁的候选人外发动作（与 `rejection_letter` / `interview_invitation` 同为高风险）。这是 spec 级行为变化（已登记类型清单扩大），需要 delta spec

## Non-goals（不做什么）

以下逐条对应 `intent.md`「不做」小节：

- **不让 AI 决定薪资或录用**——系统只生成文书与流转，录用与薪资由人定并留痕
- **不做企微卡片审批**——无公网回调、aibot 被动；审批只在 Web 工作台
- **不做自动对外发送**——默认 HR 导出 docx／复制拒信发送；系统外发总开关默认关，开启属不可代
- **不做电子签**——一期不采购（采购属不可代）；候选人签收由 HR 回填
- **不做候选人自助答复入口**——一次性链接属对外通道，一期不开
- **不做薪资等敏感字段入库**——只留岗位／部门／入职日期／汇报对象

## 合规影响说明

**本变更处理候选人个人信息并首次产生对候选人的正式文书**：

- **薪资不入库**（intent D9）：Offer 记录不含薪资、股权、签字费等任何报酬字段；导出的 docx 模板留空由 HR 手填；系统日志与留痕 MUST NOT 含薪资。PIA 范围因此不因本包扩大到报酬信息。
- **AI 生成内容标识**：Offer／拒信由模型生成或润色即带标识（《AI 生成合成内容标识办法》）；HR 改写后按 TD-JD-2 形态留"人工撰写"痕；导出 docx 内同样带标识文字。
- **AI 只排序不淘汰**：拒信只对已有 `rejection_record`（`reason_type ∈ {hard_rule, human_decision}`）的投递生成；本包不新增任何淘汰路径；候选人拒绝 Offer 不写 `rejection_record`。审计断言 `reason_type='ai_score'` 恒 0 原样沿用。
- **录用决定由人做并留痕**：审批记录含审批人可识别标识、时刻、意见；`hired` 流转的 `actor_type=human`。
- **外发门禁不绕过**：Offer／拒信系统外发一律经 `deliver_candidate_message()`，`offer_letter` 登记为高风险类型；总开关默认关。
- **候选人联系方式**：沿 S-排期 D8 的联系方式保管口径（`candidate-contact-vault`），本包不另建联系方式存储。
- **可识别到人＋访问留痕**：审批人与 HR 均须为可识别账号；查看 Offer 记录与文书写访问留痕。

## Impact

**新增代码**
- 表：`letter_template`（`kind ∈ {offer, rejection}`、版本）、`candidate_letter`（草稿版本、AI 标识、authorship、`analysis_run_id`）、`offer`（挂 `application`，非敏感字段、审批状态、答复）、`offer_approval`（审批链步骤与留痕）、`letter_access_log`
- `stage` 预置行追加 `offer`、`hired`（`refused` 语义走 `application.status`）
- 节点：`compute_letter_draft`（纯函数）→ `effect_persist_letter`；`effect_record_approval`；`effect_apply_offer_outcome`（`hired`／回退，写 history）；`effect_enqueue_letter` 接既有门禁
- docx 导出：`python-docx`（M2 已引入）
- Web：Offer 发起页、审批页（业务经理／决策人）、文书页（生成／编辑／导出／标记人工）、答复回填页；相对路径
- 合规断言：`offer` 表不存在薪资类列；拒信生成前置 `rejection_record` 存在

**新增依赖**
- 无新增（`python-docx` 已由 M2 引入）

**外部依赖／人**
- 人事部#3：现用 Offer 模板与必填字段、审批链（谁签几级）、现用拒信话术与发送时机、答复现状（`intent.md`「待专员」四项）
- 登录＋访问留痕（M2）——审批人可识别到人的前置
- S-排期 `candidate-contact-vault`——若走系统外发需收件对象
- 电子签／短信采购——本包不需要（Q1b／Q4a），留墓碑

**既有代码触碰**
- `app/storage/db.py`：新表 `CREATE TABLE IF NOT EXISTS`；`stage` 预置行追加
- `app/outbound/contracts.py`：已登记类型集合从两类扩为三类（`offer_letter`）；门禁判定逻辑不改
- `app/audit/assertions.py`：新增两条断言
- `.51` 发版：不可代项（G3）
