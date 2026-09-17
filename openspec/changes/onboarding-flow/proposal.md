## Why

Offer 接受之后到入职日之间，合同、材料、体检、设备、账号、带教人这些事今天散在 HR 的备忘录和各部门的微信群里，"这个人还差什么、谁在等谁"没有一处能看；入职日当天投递也没有一个正式的终态记录，`application_stage_history` 在 `hired` 之后就断了。本包用一份按岗位／部门生成的入职待办清单把这段接上：HR 勾选、用人部门经理看本部门进度、入职完成写终态——**材料本身不入库**。

为什么是现在：路线图波次 5，顺序在 S-Offer 之后（Offer 接受＝`application.status=hired` 是本包唯一入口）。需求收敛结论见同目录 `intent.md`（D1–D11，Shao Peishen 2026-09-17 G1 放行 Q-35），design.md 的 Decisions 逐条对应。

## What Changes

- 新增 **入职清单模板**：按岗位／部门维护待办条目模板（条目名、负责方 HR／IT／行政／财务／用人部门、建议完成期限相对入职日），由 HR 维护（D8）
- 新增 **清单实例化**：Offer 回填"接受"后，HR 一键为该投递生成入职清单实例；条目状态 `pending / done / waived`，勾选与豁免均留痕（D8）
- 新增 **进度视图**：HR 看全部；用人部门经理只读本部门待入职者的清单进度，⛔ 看不到材料内容（本来就不入库）、评分、简历（D9）
- 新增 **入职完成终态**：HR 确认"已入职"或"放弃入职"，写 `application_stage_history` 终态事实；放弃入职回退 `application.status`（D2）
- 新增 **入职后招聘数据处置钩子**：入职完成触发按留存策略的到期删除／脱敏归档动作（简历、评分、面试录音等招聘数据不长期留在人才库）；策略参数以合规验收 #1 留存策略为准，策略未签认前钩子只登记待处置不执行删除（D10）
- **不做提醒**：HR 看清单页（D11）；不做材料收集、不做人脸、不做 HRIS／ERP 对接

## Capabilities

### New Capabilities

- `onboarding-checklist`：清单模板维护、按投递实例化、条目勾选／豁免留痕、进度计算
- `onboarding-visibility`：HR 全量与用人部门经理只读本部门的可见性边界；⛔ 不含材料内容与招聘评分
- `onboarding-completion`：已入职／放弃入职的终态流转事实、幂等、与 Offer 状态的一致性
- `post-hire-data-disposition`：入职完成后招聘数据按留存策略到期删除或脱敏归档的钩子；策略未签认前只登记不执行

### Modified Capabilities

（无。`ai-decision-audit` 的「留痕数据的用途限制」原样约束本包对评分数据的处置——删除／脱敏是允许的处置，训练用途仍禁止。）

## Non-goals（不做什么）

以下逐条对应 `intent.md`「不做」小节：

- **不做员工档案／HRIS**——候选人转员工后的档案不由本系统承担，L0 无 HRIS 决策
- **不做人脸类任何功能**——入职打卡／建档不引入人脸采集
- **不做材料经值守通道收集**——身份证、银行卡、社保等材料不经 aibot／私信，不进无头会话 prompt（红线 ⑨）；本包**材料本身不入库**，只跟踪"已交／未交"
- **不做企微卡片**——只在 Web 工作台
- **不做自动外发**——对新员工本人不发任何系统消息
- **不做提醒**——一期 HR 看清单页（Q4a）
- **不做企微建号／考勤／ERP 对接**——Q1a 清单跟踪档不含系统对接

## 合规影响说明

**本变更不新增个人信息采集**（材料不入库），但触及两处既有个人信息的处置：

- **材料只跟踪不存储**：清单条目只记"已交／未交／豁免、谁勾的、何时"，MUST NOT 有材料内容、附件、证件号字段；页面不提供上传。这是本包与 Q1b（材料收集）的边界，Q1b 若将来纳入须改 PIA（不可代）。
- **入职后招聘数据处置**（intent D10）：在职员工的简历、评分、面试记录按留存策略到期删除或脱敏归档，⛔ 不长期留在人才库。具体留存期与"脱敏归档"的定义以合规验收 #1 留存策略（法务，X3）为准；策略未签认前本包的处置钩子只登记"待处置"、⛔ 不执行删除——删错了不可逆，属真实数据处理范围的变更，不可代。
- **可见性最小化**：用人部门经理只读本部门待入职者的清单进度，MUST NOT 看到简历、评分、硬门槛标记、联系方式。
- **可识别到人＋访问留痕**：勾选／豁免／终态确认均记操作人；部门经理的进度查看写访问留痕。
- **不做人脸**；**不经值守通道**；**AI 不参与**本包任何决定（清单模板由 HR 维护，无 AI 生成内容；若将来引入 AI 生成入职文书须带标识——intent D7 预留，本包不实现）。

## Impact

**新增代码**
- 表：`onboarding_template`（岗位／部门 × 条目）、`onboarding_checklist`（挂 `application`）、`onboarding_item`（条目实例、状态、操作人）、`onboarding_access_log`、`data_disposition_queue`（待处置登记）
- 节点：`effect_instantiate_checklist`、`effect_update_item`、`effect_complete_onboarding`（终态 ＋ history）、`effect_enqueue_disposition`（只登记）；后续 `effect_execute_disposition` 在留存策略签认后启用
- Web：模板维护页、清单页（HR）、部门进度页（经理只读）、终态确认；相对路径
- 合规断言：`onboarding_item` 无内容列；`data_disposition_queue` 在策略未签认时 `executed_at` 恒空

**新增依赖**
- 无

**外部依赖／人**
- 人事部#3：现用入职清单、周期与卡点、现用系统由谁建号、入职异常口径（`intent.md`「待专员」四项）
- 合规验收 #1 留存策略（法务，X3）——处置钩子执行的前置（不可代）
- 登录＋访问留痕（M2）——部门经理账号可识别的前置
- S-Offer `offer-outcome-and-transition`——`hired` 入口

**既有代码触碰**
- `app/storage/db.py`：新表 `CREATE TABLE IF NOT EXISTS`
- `app/audit/assertions.py`：新增两条
- `.51` 发版：不可代项（G3）
