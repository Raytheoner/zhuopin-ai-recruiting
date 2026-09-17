## Context

动机见 `proposal.md`「Why」；需求收敛结论见同目录 `intent.md`（D1–D11，G1 Q-33 放行）。本节只写约束现状。

**现有地基（M1／M2 已交付或在建，本包直接消费）**

- 编排：LangGraph 图骨架，`compute_*` / `effect_*` 分离，checkpointer 是 SqliteSaver；幂等键 `{thread_id}:{node_name}:{business_key}` 落 `effect_log`，与业务写同事务（`effect-transaction-integrity` spec）
- ATS 域（M2 U1）：`candidate` / `application` / `stage`（`stage_type` 语义标签）/ `application_stage_history`（`actor_type ∈ {human, agent}`）；候选人手机号只存哈希
- 外发门禁（M1 U5）：`app/outbound/delivery.py::deliver_candidate_message()`，`interview_invitation` 已登记为高风险类型；总开关 `is_candidate_outbound_enabled()` 默认关、每次求值；生产里无调用方（TD-8）
- 鉴权：M2 D12 把 `AuthMiddleware` 换成本地账号，`reviewer_of()` 返回可识别标识；本包的面试官身份复用同一账号表
- AI 生成标识与"标记为人工撰写"留痕形态：M1 JD（TD-JD-2）
- 定时基础设施：无（TD-11）
- 部署：`.51` Windows、无 Docker、无公网；Web 只能相对路径挂在 `root_path` 下
- 规模：年招几十到百人，每周面试量两位数以内，⛔ 不是高并发场景

**外部依赖现状**

- 人事部#3 跟进信在途，现用排期流程／面试官清单／邀约话术／冲突情形四项待专员（`intent.md`「待专员」）
- 合规验收 #1 未启动；本包的联系方式明文入库是它的范围扩展
- M2 U5 批量确认页（「进入面试」来源）尚未交付

## Goals / Non-Goals

**Goals:**

- 在 SQLite ＋ 单进程底座上跑通"进入面试 → 登记时段 → 安排 → 生成邀约 → HR 发出并回填 → 改期／取消／完成"闭环，每步都是流转事实
- 面试阶段联系方式的明文处理在结构上做到"开关不开就落不了库、不留痕就读不出来、终止就删"
- 成为 `interview_invitation` 门禁的第一个真实调用方，且不改门禁一行需求

**Non-Goals:**

- 不做候选人自助入口（一次性链接）
- 不做外部日历同步
- 不做提醒发送（随 TD-11）
- 不做面试评价／ScoreCard（M3 范围）
- 不把 SQLite 换 Postgres

## Decisions

### 决策 D1：范围＝半自动（对应 intent D7）

**做法**：系统负责时段、冲突、场次、文案、回填；发送动作由 HR 复制到微信／邮件完成。系统外发路径保留接口但总开关默认关。

**为什么**：intent Q1b 定；aibot 不能主动私信、`.51` 无公网，系统直发在通道上本来就走不通；HR 复制发送把"对外通道开启"这个不可代项彻底移出本包的关键路径。

**替代方案**：候选人自助选时段（Q1c）。否决——需要一次性链接对外入口，属不可代，且候选人侧 UI 一期成本高。

### 决策 D2：面试官时段来自工作台周视图登记（对应 intent D9）

**做法**：`interviewer_availability` 表按面试官存起止时刻，周视图页登记／撤销；不接企微日历。

**为什么**：intent Q3a 定；企微日历 API 需自建应用（agentid＋secret，注册属不可代）＋公网回调，`.51` 无公网。

**替代方案**：企微日历 API。否决——前置不可代且通道不可达。

### 决策 D3：入口只从 M2 批量确认「进入面试」（对应 intent D10）

**做法**：排期接口先校验 `application.current_stage.stage_type == 'interview'`；无 `application` 的候选人不可排。`stage` 表预置 `interview` 行，M2 批量确认页的"进入下一阶段"动作流转到它。

**为什么**：intent Q4a 定；人才库因此完整，且避免出现"没有简历、没有评分、只有一个名字"的投递破坏报表口径。

### 决策 D4：四个动作四个 `effect_*` 节点，流转事实与场次写同事务（对应 intent D3）

**做法**：`effect_schedule_slot` / `effect_reschedule_slot` / `effect_cancel_slot` / `effect_complete_slot`，幂等键 `{application_id}:{node_name}:{slot_id|request_id}`；每个节点在同一事务里写 `interview_slot` 与 `application_stage_history`，`effect_log` 同事务。`compute_conflict_check` 是纯函数，输入＝候选时刻＋面试官时段集合＋既有场次集合，输出＝冲突原因列表。

**为什么**：工程铁律 1、2；"流转事实条数＝成功动作次数"这条不变式只有在同事务下才能成立并被测试。

### 决策 D5：邀约文案生成是纯函数，草稿版本化，系统外发接既有门禁（对应 intent D2）

**做法**：`compute_invitation_draft(slot, template, contact_hint)` 走 LLM 网关（`temperature=0`，模型标识取响应字段，`prompt_version=invite-v1`），输出带 AI 标识；`effect_persist_draft` 落 `interview_invitation_draft`（版本递增，不覆盖）。"由系统发送"按钮调用 `deliver_candidate_message(type='interview_invitation', to=<拍平字符串>, confirmed_by=<HR>)`，⛔ 不新增外发路径。文案模板存表带版本，种子来自人事部#3 回件的话术样例；回件未到前用占位模板并在 tasks 留待办。

**为什么**：门禁 fail-closed 的收件对象条款要求调用方拍平收件人；本包是 TD-8 的第一个调用方，接线方式必须与门禁 spec 一致。模板版本化是让"哪一版话术生成了这封"可回溯。

### 决策 D6：联系方式明文入库＝独立开关 ＋ 阶段门槛 ＋ 字段级加密 ＋ 读取留痕 ＋ 终止删除（对应 intent D8）

**做法**：`Settings.candidate_contact_vault_enabled` 默认 `False`、每次求值；`candidate_contact` 表存 `phone_enc` / `email_enc`（AES-GCM，密钥 `CANDIDATE_CONTACT_KEY` 环境变量注入）；登记前校验投递阶段 ≥ `interview`；`read_contact()` 先写 `candidate_contact_access_log` 再解密，留痕失败抛错；`effect_purge_contact` 节点在投递终止或留存到期时删除密文，幂等键 `{application_id}:effect_purge_contact:{reason}`。

**为什么**：intent Q2b 定"允许明文加密入库"，但 M2 的哈希口径是 PIA 已覆盖的范围，扩展必须先改 PIA——开关默认关是把"PIA 没签就不能存"变成代码而不是流程。字段级加密与密钥不落库是最小必要下的存储安全；留痕失败即读取失败与 M2 简历访问留痕同一口径。

**替代方案**：在 M2 `candidate` 表上直接加明文列。否决——去重哈希与明文混在一张表会让"阶段前不得有明文"无法用表级断言证明。

### 决策 D7：面试官身份复用 `hr_account`，加 `interviewer` 名单表

**做法**：`interviewer` 表（`account_id` 外键到 `hr_account`、姓名、部门、可面岗位）；能登记时段的账号＝名单内账号；HR 角色只读他人时段，"代登记"带标识留痕。

**为什么**：不引入第二套账号；面试官名单是业务事实（待专员「面试官清单与轮次」），由 HR 维护而不由系统推断。

### 决策 D8：提醒只预留字段，不建调度（对应 intent D5）

**做法**：`interview_slot.reminder_sent_count` 默认 0；本包不写任何定时逻辑。TD-11 落地时的 `effect_send_reminder` 幂等键约定为 `{slot_id}:effect_send_reminder:{nth}`，写进 `docs/tech-debt.md` TD-11 的"怎么还"。

**为什么**：TD-11 明令不用 sleep 线程充数；单独为排期建半吊子调度器会得到只服务一个调用方的东西。

### 决策 D9：候选人拒绝邀约不是淘汰

**做法**：回填"候选人拒绝"只改场次邀约状态，投递阶段不动，⛔ 不写 `rejection_record`。HR 若要终止该投递，走 M2 复核工作台的批量确认路径。

**为什么**：`rejection_record` 语义是"我方淘汰"，混入候选人自愿退出会污染"AI 只排序不淘汰"断言的分母。

### 决策 D10：交付单元与顺序

| 顺序 | 单元（tasks 章） | 内容 | 前置 |
|---|---|---|---|
| 1 | U1 面试域模型（第 1 章） | `interviewer` / `interviewer_availability` / `interview_slot` / `interview_invitation_draft` / `candidate_contact` / 访问留痕；`stage` 预置 `interview` | M2 U1 建表 |
| 2 | U2 时段与排期（第 2 章） | 冲突纯函数、四个 effect 节点、面试官周视图与当日安排页、HR 排期页 | U1；M2 U5 批量确认页（入口） |
| 3 | U3 邀约文案（第 3 章） | 模板表、生成纯函数、草稿版本、复制与回填、门禁接线 | U2 |
| 4 | U4 联系方式保管（第 4 章） | 开关、加密、留痕、终止删除、断言 | U1；🔴 PIA 扩展签认（开闸前置，代码不阻塞） |
| 5 | U5 断言与发版（第 5 章） | 合规断言进 CI、`.51` 发版（🔴 G3） | U2–U4 |

## Risks / Trade-offs

- [人事部#3 回件迟迟不到，面试官名单与话术样例缺失] → U1 名单表由 HR 在页面维护，不依赖回件；U3 用占位模板上线，回件到后只换模板内容不改代码
- [M2 U5 批量确认页未交付，排期入口没有来源] → U2 用测试夹具直接把投递流转到 `interview` 阶段验证；生产上等 M2 U5，⛔ 不为此加"手工新增候选人"后门
- [密钥管理在 Windows 计划任务下的注入方式不明] → 计划任务以 SYSTEM 账户运行，环境变量走任务定义或 `.env` 文件（文件权限只 SYSTEM 可读）；U4 首日在 `.51` 同款环境冒烟，装不通则联系方式保管功能保持关闭发版
- [HR 复制发送后忘记回填，状态失真] → 排期页对"已安排但未回填邀约结果超过 2 天"的场次醒目标出（页面级提示，不是定时任务）
- [面试官不登记时段，HR 排不了] → 允许 HR"代登记"并留痕（D7），代价是时段真实性下降，报表上区分代登记
- [`interview` 阶段与 M3 live 场次的关系未定] → 本包场次表留 `kind ∈ {human}`，M3 若纳入自动排期再加 `ai_live` 值，⛔ 本包不预建

## Migration Plan

1. U1 新表全部 `CREATE TABLE IF NOT EXISTS`；`stage` 表追加 `interview` 预置行走幂等插入（已存在则跳过）
2. `CANDIDATE_CONTACT_KEY` 在 `.51` 计划任务环境中配置；未配置时 U4 功能自动视为关闭
3. 发版顺序：U1–U3 合并后可先发一次（联系方式保管关闭，HR 用工作台排期与复制邀约）；U4 合并且 PIA 扩展签认后由 Shao Peishen 决定开启开关；每次发版均为 🔴 G3
4. 回滚：开关关回即停收明文；新表不删（含留痕）；代码回退上一版

## Open Questions

> 以下为 `intent.md`「待专员」表与「外部依赖」小节里尚未闭环的项，原样转入。答案由人事部#3 跟进信回件与相应外部依赖闭环后回填；⛔ 本包不因此阻塞 U1–U3 代码，只影响模板内容、冲突规则细节与开闸时点。**当前条数：8。**

- **OQ1 现用排期流程**（待专员）：谁定时间、用什么（Excel/微信）、通知怎么发、改期怎么处理。影响 U2 页面流程的默认值；判据＝人事部#3 回件含近 1 个月脱敏排期记录 ≤10 条
- **OQ2 面试官清单与各岗面试轮次**（待专员）：几轮、每轮谁。影响 U1 名单初始数据与 U2 轮次下拉；判据＝M1 试点三岗的清单到位
- **OQ3 现用邀约话术样例**（待专员）：生成模板的种子。影响 U3 模板 v1 内容；判据＝现用微信/邮件邀约 3 条（脱敏）到位
- **OQ4 一周实际面试量与冲突情形**（待专员）：哪种算异常。影响 U2 冲突检查是否要加"同日场次上限"类规则；判据＝上周排期表到位
- **OQ5 定时基础设施（TD-11）落点**（外部依赖）：提醒节点随其建；本包只留字段。判据＝TD-11 销账
- **OQ6 M2 U5 批量确认页交付时点**（外部依赖）：排期入口来源。判据＝M2 tasks 第 6 章批量确认相关条目勾选
- **OQ7 登录＋访问留痕就位时点**（外部依赖）：排真实候选人前置。判据＝M2 tasks 3.2／3.9 勾选并发版
- **OQ8 PIA 与留存策略扩展到面试阶段联系方式**（外部依赖，🔴 不可代）：U4 开关开启前置。判据＝`docs/compliance/` 下 PIA 扩展版由 Shao Peishen 签认
