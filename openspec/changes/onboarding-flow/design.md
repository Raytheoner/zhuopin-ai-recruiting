## Context

动机见 `proposal.md`「Why」；需求收敛结论见同目录 `intent.md`（D1–D11，G1 Q-35 放行）。本节只写约束现状。

**现有地基**

- 编排：LangGraph，`compute_*` / `effect_*` 分离，SqliteSaver；幂等键落 `effect_log` 与业务写同事务
- ATS 域（M2 U1）：`application.status ∈ {ongoing, hired, refused}`、`stage_type=hired`、`application_stage_history`、`rejection_record`；`resume` / `resume_text_span` / `criterion_score` / `analysis_run` 是本包处置钩子要登记的数据类别
- Offer（`offer-generation`）：`offer.status=accepted` ＋ `application.status=hired` 是本包唯一入口；入职日来自 Offer 回填
- 鉴权：M2 本地账号可识别到人；本包新增"部门经理"角色，账号仍在 `hr_account`（M1 业务经理试点账号可复用）
- 审计留痕用途限制（`ai-decision-audit`「留痕数据的用途限制」）：处置＝删除／脱敏是允许的，训练用途仍禁止
- 值守通道红线 ⑨：候选人个人信息不进值守归档／无头 prompt——本包材料不入库，天然满足
- 定时基础设施：无（TD-11）；本包不做提醒
- 规模：年招几十到百人，同时在途入职者个位数

**外部依赖现状**

- 人事部#3 在途：现用入职清单、周期与卡点、现用系统建号、异常口径
- 合规验收 #1 留存策略（法务，X3）未签认——处置钩子执行的硬前置

## Goals / Non-Goals

**Goals:**

- 一份按岗位／部门生成的入职清单，HR 勾、经理看、入职日 HR 确认终态，`application_stage_history` 在 `hired` 之后不再断
- 材料"只跟踪不存储"在结构上成立：表无内容列、页面无上传
- 入职完成即登记处置事项，策略签认后才执行，删错不可逆的动作永远不自动发生

**Non-Goals:**

- 不做材料收集与存储（Q1b）
- 不做企微建号／考勤／ERP 对接（Q1c）
- 不做新员工自助入口（Q2c）
- 不做提醒（Q4a）
- 不做员工档案

## Decisions

### 决策 D1：范围＝清单跟踪，材料不入库（对应 intent D8）

**做法**：`onboarding_template` / `onboarding_checklist` / `onboarding_item` 三表；`onboarding_item` 只有 `name / owner_party / due_offset_days / required / status / reason / acted_by / acted_at`，⛔ 无 `content / attachment / id_number` 类列（测试用列名反证）；页面无上传控件、接口不接受 multipart。

**为什么**：intent Q1a 定；材料含身份证、银行卡、社保等敏感个人信息，一旦入库 PIA、加密、留存全要升档；年招几十到百人，材料在线下交给 HR 的成本可忽略。

**替代方案**：材料收集（Q1b）。否决——须先改 PIA（不可代），且与红线 ⑨ 的边界更难守。

### 决策 D2：使用者＝HR ＋ 用人部门经理只读本部门（对应 intent D9）

**做法**：`hr_account` 增 `role ∈ {hr, interviewer, dept_manager}`（若 M2／S-排期已加 `role` 列则复用）＋ `department`；部门进度页按 `department` 过滤；经理对本部门负责的条目可以负责方身份勾选（留痕），其余只读；跨部门 403。经理页面 ⛔ 无简历／评分／联系方式，⛔ 无指向复核工作台的链接（测试反证）。

**为什么**：intent Q2b 定；经理需要知道"我的人什么时候能来、还差什么"，但不需要也不应看到招聘评估数据——那是最小必要。

**替代方案**：新员工本人自助（Q2c）。否决——对外通道，不可代。

### 决策 D3：终态由 HR 人工确认，清单未完成只提示不阻止（对应 intent D2）

**做法**：`effect_complete_onboarding`：`confirm_hired(actual_start_date)` 写 `application_stage_history(stage_type=hired, action=onboarded, actor_type=human)`，清单 `closed`；未完成必需条目 ⇒ 页面提示，HR 填说明后仍可确认，说明与未完成清单一起留痕；入职日前拒绝。`abandon(reason)` ⇒ `application.status=refused`、`offer.status=abandoned`、history 一条、⛔ 不写 `rejection_record`。幂等键 `{application_id}:effect_complete_onboarding:{outcome}`；同一投递只允许一个终态（应用层＋唯一约束）。⛔ 无任何自动终态路径。

**为什么**：`02` §2.1 `hired` 是终态之一；"清单没勾完但人已经来了"是现实，阻止只会逼 HR 乱勾；放弃入职是候选人自愿退出，语义同 S-Offer D5 的 `refused`。

### 决策 D4：入职后招聘数据处置＝登记与执行分离（对应 intent D10）

**做法**：`data_disposition_queue`（`application_id, candidate_id, category, policy_version, planned_action, due_at, executed_at, executed_by, note`）。`effect_enqueue_disposition` 在 3 的 `confirm_hired` 同事务里登记六类（简历文件与原文、解析字段、评分与证据、面试场次与邀约、联系方式、Offer 文书），`policy_version=NULL` 表示"待策略"；幂等键 `{application_id}:effect_enqueue_disposition:{category}`。`effect_execute_disposition` 只在 `Settings.retention_policy_signed_version` 非空且事项 `due_at` 到期时执行：文件与原文删除、评分与解析字段脱敏（去可识别字段，保留岗位／阶段／时刻／分数分布）、流转事实与审计留痕表只脱敏不删；幂等键 `{application_id}:effect_execute_disposition:{category}:{policy_version}`。策略未签认 ⇒ `executed_at` 恒空（断言）。

**为什么**：intent Q3a 定"按留存策略到期删除或脱敏归档"，但留存期数字与"脱敏"定义属法务口径（X3 在途）；删错不可逆，且是真实数据处理范围变更（不可代）。登记与执行分离让"入职当天就登记"和"没签认就不删"同时成立。

**替代方案**：策略未定前先按 180 天默认值执行。否决——替法务拍板，且不可逆。

### 决策 D5：不做提醒（对应 intent D11、D5）

**做法**：逾期标记在页面打开时按 `due_offset_days` 与入职日计算；⛔ 无定时任务、无消息。将来纳入提醒随 TD-11，幂等键约定 `{item_id}:effect_send_reminder:{nth}` 写进 TD-11。

**为什么**：intent Q4a 定；同时在途入职者个位数，HR 看一眼清单页够用。

### 决策 D6：清单模板由 HR 维护，AI 不参与

**做法**：模板 CRUD 页面，版本化；⛔ 无"AI 生成清单"功能；本包无任何 LLM 调用（intent D7 的"入职文书若 AI 生成带标识"本包不触发，留在 Non-Goals）。

**为什么**：入职清单是行政事实（待专员），AI 生成只会编造条目。

### 决策 D7：交付单元与顺序

| 顺序 | 单元（tasks 章） | 内容 | 前置 |
|---|---|---|---|
| 1 | U1 入职域模型（第 1 章） | 三表 ＋ 访问留痕 ＋ 处置队列；`hr_account.role/department` | M2 U1；`offer-generation` U1（`offer` 表） |
| 2 | U2 清单页与进度（第 2 章） | 模板维护、实例化节点、勾选／豁免节点、HR 清单页、经理只读页 | U1；登录可识别 |
| 3 | U3 终态与处置登记（第 3 章） | `effect_complete_onboarding`、`effect_enqueue_disposition`、不变式、断言 | U2；`offer-generation` U5（`hired` 入口） |
| 4 | U4 处置执行（第 4 章） | `effect_execute_disposition`、脱敏规则、断言兼容 | U3；🔴 留存策略签认（代码不阻塞，执行阻塞） |
| 5 | U5 交付与发版（第 5 章） | 操作说明、TD 登记、发版（🔴 G3） | U2–U4 |

## Risks / Trade-offs

- [人事部#3 回件未到，模板缺真值] → U1 用一份通用占位模板（合同／材料／体检／设备／账号／带教人六条）上线，回件到后 HR 在页面改
- [留存策略长期不签认，处置队列越积越多] → 队列是登记不是负担；断言只查"未签认时 `executed_at` 空"；路线图 X3 是它的推手，本包不催
- [脱敏后既有断言误报"证据缺失"] → U4 给 `criterion_score` 加 `disposed_at` 列或同义标记，断言口径识别"已处置"；测试覆盖脱敏后跑全部断言
- [经理账号复用 M1 业务经理试点账号，角色混淆] → `role` 显式列，页面按角色路由；测试：`interviewer` 角色访问部门进度页 403
- [`offer-generation` 尚未交付，`hired` 入口缺] → U2 用夹具直接置 `application.status=hired` 与入职日；生产等 `offer-generation` U5
- [HR 在豁免原因里写材料内容（如证件号）] → 页面提示；不做内容审查；登记残余风险

## Migration Plan

1. U1 新表 `CREATE TABLE IF NOT EXISTS`；`hr_account` 加列走 `_ADDED_COLUMNS`（若列已由其他包加则跳过）
2. `Settings.retention_policy_signed_version` 默认空；签认后由 Shao Peishen 在 `.51` 配置
3. 发版：U1–U3 合并后发一次（清单可用、处置只登记）；U4 合并后再发（执行仍受签认闸）；每次 🔴 G3
4. 回滚：代码回退；新表不删；处置已执行的不可逆（这正是执行前必须签认的原因）

## Open Questions

> 以下为 `intent.md`「待专员」表与「外部依赖」小节里尚未闭环的项，原样转入；⛔ 不阻塞 U1–U3 代码，只影响模板真值、异常口径与处置执行时点。**当前条数：7。**

- **OQ1 现用入职清单：材料、步骤、涉及部门、谁负责**（待专员）：影响 U1 占位模板换真值；判据＝现用清单 1 份＋近 3 次入职实例（脱敏）到位
- **OQ2 入职周期现状：Offer 接受到入职日平均几天、常见卡点**（待专员）：影响模板 `due_offset_days` 默认值；判据＝近 1 年 ≤10 例到位
- **OQ3 现用系统：考勤／ERP／企微通讯录由谁建号、何时建**（待专员）：本包不对接，只影响模板里"账号"条目的负责方；判据＝回件说明
- **OQ4 哪些情形算「入职异常」（放弃入职、延期、材料不齐）**（待专员）：影响 U3 放弃入职原因的枚举与页面提示；判据＝近 1 年异常例 ≤10 到位
- **OQ5 合规验收 #1 留存策略签认**（外部依赖，🔴 不可代）：U4 执行的硬前置；判据＝`docs/compliance/` 留存策略由 Shao Peishen 签认并配置 `retention_policy_signed_version`
- **OQ6 登录＋访问留痕就位时点**（外部依赖）：经理账号可识别的前置；判据＝M2 tasks 3.2／3.9 勾选并发版
- **OQ7 `offer-generation` 交付时点**（外部依赖）：`hired` 入口来源；判据＝`offer-generation` tasks 5.1 勾选
