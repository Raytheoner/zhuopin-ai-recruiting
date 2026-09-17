**进度：0/40**（2026-09-17 `0917BF` 立包。🔴 = 不可代项（括号内写谁做）；每章 = 一个交付单元 = 一份 superpowers plan = 一条 worktree 分支。涉及副作用的任务已逐条写幂等策略；单条任务 ≤ 2 小时。design.md Open Questions 当前 7 条，均不改变任务分解，只影响模板／审批链数据与口径。）

## 0. 前置门槛（不写代码；任一未过则对应下游单元不得发车）

- [ ] 0.1 🔴 **登录＋访问留痕就位**（M2 3.2／3.9 交付并发版；Shao Peishen 拍发版）。判据：`.51` 上 `reviewer_of()` 返回真实用户名。阻塞 U3 在生产上审批（代码不阻塞）
- [ ] 0.2 **M2 U1 `rejection_record` 与 U5 批量确认交付**。判据：M2 tasks 2.3 与第 6 章批量确认条目勾选。阻塞 U4 拒信生成的生产路径（测试夹具不阻塞）
- [ ] 0.3 **人事部#3 回件：Offer 模板／审批链／拒信话术与时机／答复现状**（汤丽萍，经跟进信线）。判据：四项归档且 design OQ1–OQ4 回填。阻塞模板与审批链正式数据（占位不阻塞）
- [ ] 0.4 🔴 **电子签／短信采购**（Shao Peishen；本包不需要，Q1b／Q4a 已定）。⚰️ 墓碑：本包内保持"不需要"，将来改系统发送并签收时另立包重提
- [ ] 0.5 **`interview-scheduling` 的 `candidate-contact-vault` 交付**（系统外发收件对象来源；HR 自行发送不依赖）。判据：`interview-scheduling` tasks 4.4 勾选

## 1. U1 Offer 域模型

- [ ] 1.1 `app/storage/db.py` 新增 `letter_template`（`kind CHECK IN offer/rejection, version, body, updated_by, updated_at`；`(kind, version)` 唯一）、`candidate_letter`（`application_id, kind, version, template_version, body, ai_generated BOOL, authorship_marked_by, authorship_marked_at, authorship_from_version, analysis_run_id, sent_status CHECK IN none/exported/copied/sent/system_queued, sent_channel, created_by, created_at`；`(application_id, kind, version)` 唯一）；`CREATE TABLE IF NOT EXISTS`，⛔ 不进 `_ADDED_COLUMNS`
- [ ] 1.2 新增 `offer`（`application_id` 唯一、`job_id, department, start_date, report_to, note, status CHECK IN pending_approval/needs_revision/approved/exported/accepted/declined/negotiating, approval_round, created_by, updated_by`；⛔ 无任何薪资类列，测试用列名关键词反证 `salary/pay/compensation/bonus/薪`）
- [ ] 1.3 新增 `offer_approval_chain`（`job_id, level, approver_account_ids JSON, updated_by`）与 `offer_approval`（`offer_id, round, level, approver, decision CHECK IN approved/returned, comment, at`；`(offer_id, round, level)` 唯一）
- [ ] 1.4 新增 `letter_access_log`（`accessor, application_id, letter_id, access_type CHECK IN view/export, at`，无正文列）
- [ ] 1.5 `stage` 预置行幂等追加 `offer`（`stage_type=offer`）与 `hired`（`stage_type=hired`）；测试：M2 预置三行不变、`interview` 行（若 `interview-scheduling` 已建）不重复
- [ ] 1.6 `tests/test_db_offer_schema.py`：新库建表齐全；老库升级既有表不变；全部 CHECK 反证；`offer` 表无薪资列断言
- [ ] 1.7 审批链维护接口 `GET/PUT /jobs/{id}/offer-approval-chain`（HR 角色；默认一级＝业务经理）；幂等：同内容重复 PUT 不产生新版本；审批人必须是可识别账号

## 2. U2 文书引擎（Offer／拒信共用）

- [ ] 2.1 模板维护接口 `GET/PUT /letter-templates/{kind}`：版本递增不覆盖；保存校验 ⛔ 评分／排名／硬门槛占位符、Offer ⛔ 薪资类占位符（正则＋关键词表），命中即拒并指出；占位模板 v1 各一份（0.3 回件到后只换内容）；测试两个 Scenario
- [ ] 2.2 `app/agents/letter_drafter.py::compute_letter_draft(kind, template, facts) -> Draft` 纯函数：LLM 网关 json_schema 路径，`temperature=0`，`prompt_version=letter-{kind}-v1`，模型标识取 API 响应 `model` 字段，`AuditHook` 留痕（含模板版本）；输出带 AI 生成标识（与 M1 JD 同一字符串）；`facts` 不含薪资字段（schema 层不存在该键）；模块内 grep 无 storage 写入
- [ ] 2.3 节点 `effect_persist_letter`：写 `candidate_letter`（版本递增）；幂等键 `{application_id}:effect_persist_letter:{kind}:{analysis_run_id}`；Offer 类前置 `offer.status=approved`；拒信类前置该投递存在 `rejection_record`（缺则拒并提示先批量确认）；测试：重复生成新版本、重跑不重复、两条前置反证
- [ ] 2.4 编辑与"标记为人工撰写"：`PATCH /letters/{id}`（编辑后 `ai_generated` 仍为真）、`POST /letters/{id}/mark-human`（记 `authorship_marked_by/at/from_version`）；测试
- [ ] 2.5 docx 导出 `GET /letters/{id}/export.docx`：`python-docx` 渲染；未标记人工撰写 ⇒ 页眉含 AI 标识文字；Offer 薪资处留空段落；写 `letter_access_log(export)`；`candidate_letter.sent_status=exported`；测试：docx 解析回读含标识、薪资段为空
- [ ] 2.6 查看留痕：`GET /letters/{id}` 先写 `letter_access_log(view)` 再返回正文，留痕失败 ⇒ 不返回正文；测试
- [ ] 2.7 文书页 `GET /applications/{id}/letters`：生成、各版本、编辑、标记人工、导出／复制、AI 标识可见；相对路径，子路径前缀测试
- [ ] 2.8 在 `.51` 同款 Windows 环境冒烟导出一份 docx 并用 Word 打开确认（记录到 tasks 本条）

## 3. U3 内部审批流

- [ ] 3.1 发起 Offer `POST /applications/{id}/offer`：前置投递阶段 ≥ `interview`、无 `rejection_record`、`status=ongoing`；写 `offer(pending_approval, round=1)` ＋ 流转到 `offer` 阶段 ＋ history，同事务；幂等键 `{application_id}:effect_create_offer:{request_id}`；测试：已淘汰投递被拒、重跑不重复
- [ ] 3.2 节点 `effect_record_approval`：校验操作人在该 `(job, level)` 审批人列表；写 `offer_approval`；最后一级 `approved` ⇒ `offer.status=approved`；任一级 `returned` ⇒ `needs_revision` 且后续级不触发；幂等键 `{offer_id}:effect_record_approval:{round}:{level}:{approver}:{decision}`；测试覆盖 spec 三个 Scenario ＋ 重跑
- [ ] 3.3 退回后修改 `PATCH /offers/{id}`（仅 `needs_revision` 可改；改后 `round+1`、`status=pending_approval`）；测试：链从第一级重走
- [ ] 3.4 审批页 `GET /offers/pending`（本人待审列表）与 `GET /offers/{id}/approve`：显示 Offer 非敏感字段与投递基本信息；⛔ 不显示 AI 评分／排名／"建议录用"类文本（测试反证页面无相关字符串）；通过／退回＋意见
- [ ] 3.5 审批待办的内部通知：复用既有内部通知通道（`question`／`confirmation_prompt` 同类，非候选人门禁范围）；幂等键含 `offer_id/round/level`；无通道时只靠列表页（不阻塞）
- [ ] 3.6 导出门槛：2.5 的导出接口对 `offer.status ∉ {approved, exported, accepted, declined, negotiating}` 拒绝；测试：`pending_approval` 导出被拒
- [ ] 3.7 U3 e2e：发起 → 一级退回 → 修改 → 两级通过 → 生成文书 → 导出；审批留痕条数守恒

## 4. U4 门禁接线与拒信

- [ ] 4.1 `app/outbound/contracts.py` 已登记类型加 `offer_letter`（风险最高级、需确认）；既有门禁测试全部原样通过；新增测试：`offer_letter` 不再按未知类型拦截、无确认人仍拦截
- [ ] 4.2 节点 `effect_enqueue_letter`：`POST /letters/{id}/send` 调 `deliver_candidate_message(type=offer_letter|rejection_letter, recipient=<contact-vault 读取拍平字符串；未开启 ⇒ 空 ⇒ 门禁按收件对象未知拦截>, confirmed_by=<HR>)`；⛔ 不新增外发路径；`sent_status=system_queued`；幂等由门禁既有 `effect_*` 承担，本节点幂等键 `{letter_id}:effect_enqueue_letter:{request_id}`；测试：总开关关 ⇒ 拦截入队留痕"总开关关闭"；缺标识 ⇒ 拦截；收件对象空 ⇒ 拦截
- [ ] 4.3 拒信页 `GET /rejections/letters`：列出有 `rejection_record` 且未生成拒信的投递，显示淘汰理由类型（`hard_rule` 指向规则／`human_decision`），HR 勾选后显式"生成"；⛔ 批量确认淘汰不自动生成（测试：批量确认后 `candidate_letter` 无新行）
- [ ] 4.4 复制／发送回填 `POST /letters/{id}/sent`（`channel`）：`sent_status=copied|sent`，记录 HR 与时刻；幂等：同状态重复无第二条留痕
- [ ] 4.5 U4 e2e：批量确认淘汰（夹具）→ 拒信页可见 → 生成 → 复制回填；`send` 在总开关关时拦截

## 5. U5 答复回填、流转、断言与发版

- [ ] 5.1 节点 `effect_apply_offer_outcome`：前置 `sent_status ∈ {exported, copied, sent, system_queued}`；`accepted(start_date)` ⇒ `offer.status=accepted`、`stage=hired`、`application.status=hired`；`declined(reason)` ⇒ `offer.status=declined`、`stage=interview`、`status=ongoing`；`negotiating(note)` ⇒ 只改 `offer.status`；三写同事务；幂等键 `{application_id}:effect_apply_offer_outcome:{offer_id}:{outcome}:{request_id}`；测试：未导出回填被拒、`negotiating→accepted`、重跑不重复、`rejection_record` 行数不变
- [ ] 5.2 "终止投递" `POST /applications/{id}/refuse`（仅 `offer.status=declined` 后可用）：`application.status=refused` ＋ history（`actor_type=human`）；⛔ 不写 `rejection_record`；幂等键 `{application_id}:effect_refuse_application:{request_id}`
- [ ] 5.3 不变式测试 `tests/test_offer_history_invariant.py`：任一投递 `offer/hired/回退` 相关 history 条数 ＝ 成功回填与终止次数，穷举含重跑序列
- [ ] 5.4 回填页 `GET /offers/{id}/outcome`：接受（入职日期）／拒绝（原因）／谈判中（备注）；相对路径
- [ ] 5.5 `app/audit/assertions.py` 新增：① `offer` 表列名不含薪资关键词；② `candidate_letter.kind='rejection'` 的每行对应投递存在 `rejection_record`；③ `application.status='refused'` 的投递中由本包写入的 `rejection_record` 行数为 0（`ai_score` 恒 0 原样沿用）；CI 接入
- [ ] 5.6 HR 一页操作说明 `docs/offer-generation-guide.md`：发起／审批／导出／手填薪资／发送／回填；明写 `note` 不得填薪资、拒信由 HR 显式生成
- [ ] 5.7 `docs/tech-debt.md`：TD-8 追加"调用方＝offer-generation 4.2（`rejection_letter` 首个调用方）"；登记"`note` 自由文本可能被填入薪资，无内容审查"残余风险
- [ ] 5.8 🔴 **`.51` 发版**（Shao Peishen 拍「发」，G3）。判据：`05-发布运行手册.md` 流程跑完，系统外发总开关保持关闭
