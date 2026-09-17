## Why

M2 把候选人排出顺序、HR 批量确认「进入面试」之后，链条在这里断掉：面试时间靠 HR 在 Excel 和微信里来回对，面试官的空档只有 HR 自己记得，改期一次就要重发一遍消息，而"这个人什么时候面的、面了没有、谁改的期"没有任何一处事实记录，报表算不出面试环节的耗时。本包把这一段接到既有的 `application` / `application_stage_history` 上，让排期、改期、取消、完成变成可查的流转事实。

为什么是现在：M2 试运行验证后（路线图波次 5）主链路已经能把人推到「进入面试」，面试环节是下一个断点；邀约外发门禁（`interview_invitation` 类型）在 M1 就建好了但至今没有调用方（TD-8），本包是它的第一个真实调用方。需求收敛结论见同目录 `intent.md`（D1–D11，Shao Peishen 2026-09-17 G1 放行 Q-33），design.md 的 Decisions 逐条对应。

## What Changes

- 新增 **面试官可用时段登记**：面试官在 Web 工作台按周视图登记／撤销可用时段，并能看自己当日安排；⛔ 不接企微日历、不接任何外部日历（D9）
- 新增 **面试安排与冲突检查**：HR 从「进入面试」的投递中选人、选面试官、选时段，系统检查面试官时段冲突与同一候选人重叠；安排／改期／取消／完成四个动作各自写 `application_stage_history`（`stage_type=interview`，`actor_type=human`）（D3、D7）
- 新增 **邀约文案生成**：系统按模板生成邀约文案（带 AI 生成标识），HR 复制到微信／邮件发出后在工作台回填「已发出／候选人已确认／候选人拒绝」；若走系统外发，一律经 `interview_invitation` 门禁，总开关默认关（D2、D7）
- 新增 **候选人联系方式保管**：投递进入面试阶段后，允许 HR 登记候选人明文手机号／邮箱，字段级加密入库、每次读取留痕、随留存策略到期删除；M2 的"手机号只哈希去重"口径在面试阶段前保持不变（D8）
- 新增 **进入排期的唯一入口**：只有 M2 批量确认为「进入面试」的 `application` 可被排期；没有 `application` 的候选人必须先走 M2 上传（D10）
- 预留 **面试提醒**：本包不实现提醒；提醒节点随定时基础设施（TD-11）同批建，本包只在数据模型上留出"提醒次数"字段与幂等键约定（D5）

## Capabilities

### New Capabilities

- `interviewer-availability`：面试官可用时段的登记、撤销、周视图查看、当日安排；时段属于面试官不属于投递
- `interview-slot-scheduling`：面试安排／改期／取消／完成的动作与状态机；冲突检查；进入排期的入口约束；每个动作写流转事实；提醒字段预留
- `interview-invitation-drafting`：邀约文案的模板生成、AI 生成标识、HR 复制发送与结果回填；系统外发路径必经既有门禁
- `candidate-contact-vault`：面试阶段候选人明文联系方式的加密入库、读取留痕、到期删除；阶段前拒收明文

### Modified Capabilities

（无。`outbound-approval-gate` 已登记 `interview_invitation` 为高风险类型，本包只是它的第一个调用方，不改门禁的任何要求；`ai-decision-audit` 与 `effect-transaction-integrity` 原样约束本包的 `effect_*` 节点。）

## Non-goals（不做什么）

以下逐条对应 `intent.md`「不做」小节：

- **不做候选人自助选时段／改期**——一期不开候选人对外入口（一次性链接属对外通道，开启是不可代项）
- **不做企微卡片**——面试官与 HR 都只在 Web 工作台操作；aibot 是被动应答形态，不能主动私信
- **不做自动外发**——邀约由 HR 复制发送为主；系统外发路径的总开关默认关，开启属不可代
- **不让 AI 决定面试官人选**——面试官由 HR 指定，系统只查冲突
- **不排 AI 语音面试场次**——是否纳入待 S-M3 Q1 定
- **不接外部日历**（企微日历／Outlook）——面试官在工作台登记时段
- **不实现提醒**——随 TD-11 定时基础设施同批建，本包只预留字段

## 合规影响说明

**本变更扩大候选人个人信息的处理范围**：M2 只以哈希形式保存手机号用于去重；本包在投递进入面试阶段后**允许明文手机号／邮箱加密入库**（intent D8，Shao Peishen 2026-09-17 G1 定）。约束如下：

- **处理范围变更须改 PIA**：合规验收 #1 的 PIA 报告与留存策略必须扩展到"面试阶段联系方式"这一处理目的；扩展文档未由 Shao Peishen 签认前，联系方式保管功能保持关闭（配置开关默认关、每次求值），仅接受占位测试数据。属不可代项（真实简历数据处理范围的变更）。
- **最小必要**：只在 `stage_type=interview` 及之后的投递上允许登记；投递终止（淘汰／候选人拒绝）或留存期到期即删除明文，只留"曾登记过"的事实。
- **可识别到人＋访问留痕**（部署约束 5）：每次读取明文联系方式写访问留痕（谁、何时、哪个候选人、用途），留痕失败则读取失败；共享口令不满足。
- **AI 生成内容标识**：邀约文案由模型生成或润色即带标识，与 M1 JD 标识同一口径；HR 改写后按 TD-JD-2 形态留"人工撰写"痕。
- **外发门禁不绕过**：任何系统外发一律经 `deliver_candidate_message()`；`interview_invitation` 是已登记的高风险类型，未经人工确认不外发，总开关默认关。
- **AI 只做排序推荐不做淘汰**：本包不产生任何淘汰动作；候选人拒绝邀约由 HR 人工回填，不写 `rejection_record`（那是淘汰记录，不是候选人自愿退出）。
- **不做人脸／表情分析**：本包无任何面试内容处理，只处理时间与联系方式。

## Impact

**新增代码**
- 面试域表：`interviewer` / `interviewer_availability` / `interview_slot` / `interview_invitation_draft` / `candidate_contact`（加密列）/ `candidate_contact_access_log`
- `stage` 表预置 `interview` 行（`stage_type=interview`）
- LangGraph 节点：`compute_conflict_check`（纯函数）→ `effect_schedule_slot` / `effect_reschedule_slot` / `effect_cancel_slot` / `effect_complete_slot`（各自独占节点、带幂等键、与流转事实同事务）；`compute_invitation_draft`（纯函数）→ `effect_persist_draft`；系统外发时 `effect_enqueue_invitation` 接既有门禁
- Web：面试官时段周视图页、当日安排页、HR 排期页、邀约文案页、联系方式登记页，全部相对路径（部署约束 1）
- 合规断言：`candidate_contact` 明文列不得以非加密形式出现；访问留痕条数 ≥ 明文读取次数

**新增依赖**
- 字段级加密：优先 Python 标准库或已有依赖（`cryptography` 若已在 `requirements.txt`）；密钥由环境变量注入，⛔ 不入库不入版本库

**外部依赖／人**
- 人事部#3 跟进信：现用排期流程、面试官清单与轮次、邀约话术样例、一周面试量与冲突情形（`intent.md`「待专员」四项）
- 合规验收 #1 扩展：PIA 与留存策略纳入面试阶段联系方式（Shao Peishen，不可代）
- 登录＋访问留痕（M2 U1/U2 交付）——排真实候选人的前置
- M2 U5 批量确认页——「进入面试」的唯一来源

**既有代码触碰**
- `app/storage/db.py`：新表走 `CREATE TABLE IF NOT EXISTS`；`stage` 预置行追加 `interview`
- `app/outbound/`：不改门禁，只新增调用方；`interview_invitation` 的载荷字段（收件对象拍平成字符串标识）由本包接线
- `app/audit/assertions.py`：新增两条本包断言
- `.51` 发版：不可代项（G3）
