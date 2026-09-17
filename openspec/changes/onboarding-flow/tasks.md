**进度：0/34**（2026-09-17 `0917BF` 立包。🔴 = 不可代项（括号内写谁做）；每章 = 一个交付单元 = 一份 superpowers plan = 一条 worktree 分支。涉及副作用的任务已逐条写幂等策略；单条任务 ≤ 2 小时。design.md Open Questions 当前 7 条，均不改变任务分解。）

## 0. 前置门槛（不写代码；任一未过则对应下游单元不得发车）

- [ ] 0.1 🔴 **登录＋访问留痕就位**（M2 3.2／3.9 交付并发版；Shao Peishen 拍发版）。判据：`.51` 上 `reviewer_of()` 返回真实用户名。阻塞 U2 生产使用（代码不阻塞）
- [ ] 0.2 **`offer-generation` U5 交付**（`application.status=hired` ＋ 入职日的来源）。判据：`offer-generation` tasks 5.1 勾选。阻塞 U3 生产入口（夹具不阻塞）
- [ ] 0.3 🔴 **合规验收 #1 留存策略签认**（Shao Peishen 发起，法务结论，本人签认并配置 `retention_policy_signed_version`）。判据：`docs/compliance/` 留存策略落档签认。阻塞 U4 的 4.5 执行开启（代码不阻塞）
- [ ] 0.4 **人事部#3 回件：现用入职清单／周期与卡点／现用系统建号／异常口径**（汤丽萍，经跟进信线）。判据：四项归档且 design OQ1–OQ4 回填。阻塞模板真值（占位不阻塞）
- [ ] 0.5 🔴 **企微自建应用／ERP 对接账号**（Shao Peishen；本包不需要，Q1a 已定）。⚰️ 墓碑：本包内保持"不需要"，将来 Q1c 另立包重提

## 1. U1 入职域模型

- [ ] 1.1 `app/storage/db.py` 新增 `onboarding_template`（`scope_type CHECK IN job/department, scope_id, version, items JSON[{name, owner_party CHECK IN hr/it/admin/finance/dept, due_offset_days, required}], updated_by, updated_at`；`(scope_type, scope_id, version)` 唯一）；`CREATE TABLE IF NOT EXISTS`
- [ ] 1.2 新增 `onboarding_checklist`（`application_id` 唯一、`template_version, start_date, status CHECK IN open/closed, closed_reason, created_by, created_at`）与 `onboarding_item`（`checklist_id, name, owner_party, due_offset_days, required, status CHECK IN pending/done/waived, reason, acted_by, acted_at`；⛔ 无 `content/attachment/id_number/file` 类列，测试列名反证）
- [ ] 1.3 新增 `onboarding_item_history`（`item_id, from_status, to_status, reason, acted_by, at`）、`onboarding_access_log`（`accessor, application_id, at`）、`data_disposition_queue`（`application_id, candidate_id, category CHECK IN resume_file/parsed_fields/scores/interview/contact/offer_letter, policy_version NULL, planned_action CHECK IN delete/anonymize/pending, due_at, executed_at, executed_by, note`；`(application_id, category)` 唯一）
- [ ] 1.4 `hr_account` 加 `role CHECK IN hr/interviewer/dept_manager`（默认 `hr`）与 `department`（走 `_ADDED_COLUMNS`；若其他包已加则复用不重复）；`Settings.retention_policy_signed_version: str | None = None`
- [ ] 1.5 `tests/test_db_onboarding_schema.py`：新库建表齐全；老库升级既有表不变；CHECK 反证；`onboarding_item` 无内容列断言
- [ ] 1.6 占位模板 v1（部门级默认：签劳动合同／交入职材料／体检报告／配置设备／开通账号／指定带教人；负责方与 `due_offset_days` 按常识占位，0.4 回件到后 HR 在页面改）；模板维护接口 `GET/PUT /onboarding-templates/{scope_type}/{scope_id}`（HR 角色，版本递增）；幂等：同内容 PUT 不新增版本

## 2. U2 清单页与进度

- [ ] 2.1 节点 `effect_instantiate_checklist`：前置 `application.status=hired` 且 `offer.status=accepted`；按"岗位模板优先、否则部门模板"展开条目；写 `onboarding_checklist` ＋ `onboarding_item` 同事务；幂等键 `{application_id}:effect_instantiate_checklist`；重复调用返回既有；测试：`ongoing` 投递被拒、重跑不重复
- [ ] 2.2 节点 `effect_update_item`：`done / waived(reason 必填) / pending(撤回)`；操作人须为 HR 或该条 `owner_party` 对应部门经理；写 `onboarding_item` ＋ `onboarding_item_history` 同事务；幂等键 `{item_id}:effect_update_item:{to_status}:{request_id}`；测试：豁免无原因被拒、重跑无第二条 history
- [ ] 2.3 进度与逾期纯函数 `app/agents/onboarding_progress.py::progress(items, start_date, today)`：`done+waived / required`；逾期＝`start_date + due_offset_days < today` 且 `pending`；⛔ 无 storage 写入、无消息发送（grep 反证）
- [ ] 2.4 HR 清单页 `GET /applications/{id}/onboarding`：生成、逐条勾选／豁免／撤回、进度条、逾期标记；⛔ 无上传控件（HTML 断言）、接口拒绝 multipart（测试）；相对路径，子路径前缀测试
- [ ] 2.5 HR 总览页 `GET /onboarding`：全部 `open` 清单按入职日升序，进度与逾期条目数
- [ ] 2.6 部门经理只读页 `GET /onboarding/department`：按 `hr_account.department` 过滤；跨部门 403；`interviewer`／未登录 403；每次打开写 `onboarding_access_log`，留痕失败不返回内容；页面 ⛔ 无评分／排名／简历／联系方式／复核工作台链接（HTML 反证）；本部门负责条目可勾选（走 2.2 留痕）
- [ ] 2.7 U2 e2e：夹具置 `hired`＋入职日 → 生成清单 → HR 勾两条、豁免一条 → 经理只读看到进度 → 跨部门 403 → 逾期标记出现；`onboarding_item_history` 条数守恒

## 3. U3 终态与处置登记

- [ ] 3.1 节点 `effect_complete_onboarding(confirm_hired, actual_start_date, note)`：前置今日 ≥ 入职日、清单 `open`、该投递无终态；未完成必需条目 ⇒ 要求 `note` 并把未完成清单写进留痕；写 `application_stage_history(stage_type=hired, action=onboarded, actor_type=human)` ＋ 清单 `closed` ＋ 3.2 登记，同事务；幂等键 `{application_id}:effect_complete_onboarding:hired`；测试：入职日前被拒、有未完成条目需 note、重跑不重复
- [ ] 3.2 节点 `effect_enqueue_disposition`（由 3.1 同事务调用）：六类各一行，`policy_version=NULL`、`planned_action=pending`；幂等键 `{application_id}:effect_enqueue_disposition:{category}`；测试：六行、重跑不重复
- [ ] 3.3 节点 `effect_complete_onboarding(abandon, reason)`：`application.status=refused`、`offer.status=abandoned`、history 一条、清单 `closed(abandoned)`；⛔ 不写 `rejection_record`（测试行数不变）；幂等键 `{application_id}:effect_complete_onboarding:abandoned`；已 `hired` 终态后拒绝
- [ ] 3.4 不变式测试 `tests/test_onboarding_terminal_invariant.py`：任一投递终态 history ≤ 1 条且与 `application.status`／`offer.status` 一致；穷举含重跑序列；⛔ 无自动终态路径（grep 反证无调度／无条件触发）
- [ ] 3.5 终态确认 UI：清单页"确认已入职"（实际入职日、说明）与"放弃入职"（原因）；入职日已过且未确认显示"待确认"
- [ ] 3.6 `app/audit/assertions.py` 新增：① `onboarding_item` 无内容列；② `retention_policy_signed_version` 为空时 `data_disposition_queue.executed_at` 全空；③ `application.status='refused'` 的投递无本包写入的 `rejection_record`；CI 接入

## 4. U4 处置执行（受签认闸）

- [ ] 4.1 `is_retention_policy_signed()` 每次求值（环境变量 > 配置 > 默认空）；为空 ⇒ 执行节点直接返回"未签认"并留痕一次尝试；测试
- [ ] 4.2 脱敏规则 `app/storage/anonymize.py`：`resume.parsed_json` 去姓名／公司／联系方式保留年限与技能集合、`criterion_score` 保留分数与维度去 evidence 文本（`evidence_ref` 置 `disposed` 标记）、`analysis_run` 输入哈希保留原始响应删除、`candidate` 姓名置占位；流转事实与 `human_review` 只去可识别字段不删行；单元测试逐类
- [ ] 4.3 节点 `effect_execute_disposition`：签认且 `due_at` 到期 ⇒ 按 `planned_action` 删除（简历文件与原文、联系方式密文、邀约与 Offer 文书正文）或脱敏（解析字段、评分、面试场次）；写 `executed_at/executed_by`；幂等键 `{application_id}:effect_execute_disposition:{category}:{policy_version}`；测试：未签认不执行、到期执行、重跑不重复、流转事实表行数不变
- [ ] 4.4 断言兼容：既有 `ai-decision-audit` 断言与 M2 断言在脱敏数据上按"已处置"口径通过（`evidence_ref='disposed'` 不算缺失）；测试：脱敏后全量断言绿
- [ ] 4.5 🔴 **签认后配置与首轮执行**（Shao Peishen 在 `.51` 配置 `RETENTION_POLICY_SIGNED_VERSION`，并对首个到期事项亲自触发执行、核对留痕）。判据：`intent.md` 末尾记「处置执行开启 <日期> 策略版本 <v>」
- [ ] 4.6 处置触发方式：签认后由 HR 在总览页"执行到期处置"按钮手动触发（⛔ 本包不建定时任务；TD-11 落地后可改为定时，幂等键不变）

## 5. U5 交付与发版

- [ ] 5.1 HR 一页操作说明 `docs/onboarding-flow-guide.md`：模板维护、生成清单、勾选／豁免、经理怎么看、确认入职／放弃、处置执行；明写材料不上传、豁免原因不写证件号
- [ ] 5.2 `docs/tech-debt.md`：TD-11「怎么还」追加入职提醒幂等键约定 `{item_id}:effect_send_reminder:{nth}` 与 4.6 手动触发改定时；登记"豁免原因自由文本可能含材料内容，无内容审查"残余风险
- [ ] 5.3 `requirements.txt` 无增量确认；`tests/test_doc_size_budget.py` 通过
- [ ] 5.4 🔴 **`.51` 发版**（Shao Peishen 拍「发」，G3）。判据：`05-发布运行手册.md` 流程跑完，处置执行保持未签认状态
