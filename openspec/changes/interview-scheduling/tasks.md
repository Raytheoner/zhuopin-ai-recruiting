**进度：0/42**（2026-09-17 `0917BF` 立包。🔴 = 不可代项（括号内写谁做）；每章 = 一个交付单元 = 一份 superpowers plan = 一条 worktree 分支。涉及副作用的任务已逐条写幂等策略；单条任务 ≤ 2 小时。design.md Open Questions 当前 8 条，均不改变 U1–U3 的任务分解，只影响模板内容／冲突细则／开闸时点。）

## 0. 前置门槛（不写代码；任一未过则对应下游单元不得发车）

- [ ] 0.1 🔴 **登录＋访问留痕就位**（M2 U2 的 3.2／3.9 交付并发版；Shao Peishen 拍发版）。判据：`.51` 上 `reviewer_of()` 返回真实用户名。阻塞 U2 在生产上排真实候选人（U2 代码不阻塞）
- [ ] 0.2 **M2 U5 批量确认页交付**（「进入面试」的唯一来源）。判据：M2 tasks 第 6 章批量确认条目勾选。阻塞 U2 生产入口（U2 代码用夹具不阻塞）
- [ ] 0.3 🔴 **PIA 与留存策略扩展到"面试阶段联系方式"**（Shao Peishen 发起并签认；法务结论）。判据：`docs/compliance/` 下 PIA 扩展版落档并签认。阻塞 U4 的 4.8 开关开启（U4 代码不阻塞）
- [ ] 0.4 **人事部#3 回件：排期流程／面试官清单与轮次／邀约话术 3 条／上周排期表**（汤丽萍，经跟进信线）。判据：四项在 `docs/跟进信/` 归档且 design OQ1–OQ4 回填。阻塞 U3 模板 v1 正式内容（占位模板不阻塞）
- [ ] 0.5 🔴 **企微自建应用注册**（Shao Peishen；仅当将来改为系统直发时才需要，本包默认不需要）。判据：本包内保持"不需要"，留墓碑即可

## 1. U1 面试域模型

- [ ] 1.1 `app/storage/db.py` 新增 `interviewer`（`account_id` 唯一外键 `hr_account`、姓名、部门、可面岗位 JSON、启用状态）、`interviewer_availability`（`interviewer_id, start_at, end_at, note, registered_by, on_behalf BOOL, created_at`；同一面试官时段不重叠由应用层校验＋测试）；`CREATE TABLE IF NOT EXISTS`，⛔ 不进 `_ADDED_COLUMNS`
- [ ] 1.2 新增 `interview_slot`（`application_id, round, start_at, end_at, mode CHECK IN onsite/phone/online, location_or_link, status CHECK IN scheduled/rescheduled/cancelled/completed/no_show, cancel_reason, invitation_status CHECK IN none/drafted/sent/confirmed/declined/reschedule_requested, sent_channel, reminder_sent_count DEFAULT 0, kind CHECK IN ('human'), created_by, updated_by`）与 `interview_slot_interviewer`（多对多）
- [ ] 1.3 新增 `interview_invitation_draft`（`slot_id, version, template_version, body, ai_generated BOOL, authorship_marked_by, authorship_marked_at, analysis_run_id, created_at`；`(slot_id, version)` 唯一）与 `invitation_template`（`version, body, updated_by, updated_at`）
- [ ] 1.4 新增 `candidate_contact`（`application_id` 唯一、`phone_enc BLOB`、`email_enc BLOB`、`registered_by`、`registered_at`、`source CHECK IN hr_manual/candidate_confirmed`、`purged_at`、`purge_reason`）与 `candidate_contact_access_log`（`accessor, application_id, purpose, at`，无明文列）
- [ ] 1.5 `stage` 预置行追加 `interview`（`stage_type=interview`，幂等插入：已存在跳过）；测试：M2 预置三行不变、`interview` 行存在且唯一
- [ ] 1.6 `tests/test_db_interview_schema.py`：新库建表齐全；复制 M2 结构的老库升级后既有表一行不改；全部 CHECK 反证（非法 status／mode／kind 被拒）
- [ ] 1.7 面试官名单维护接口 `GET/POST/PATCH /interviewers`（HR 角色）；幂等：同 `account_id` 重复创建返回既有记录；测试：非 HR 角色 403

## 2. U2 时段登记与排期动作

- [ ] 2.1 `app/agents/conflict_check.py::check(candidate_window, interviewer_windows, existing_slots) -> list[Conflict]` 纯函数：面试官无可用时段、面试官时段被占、候选人同时刻另有场次三类，全部列出；测试覆盖 spec 三个 Scenario；模块内 grep 不得出现 storage 写入
- [ ] 2.2 时段登记接口 `POST/DELETE /interviewers/me/availability`：只写本人；重叠拒绝；被场次占用的时段不可撤销；HR `on_behalf` 代登记留痕；幂等：同起止时刻重复登记返回既有行。测试：面试官 A 读 B 的时段 403
- [ ] 2.3 节点 `effect_schedule_slot`：先跑 2.1 过冲突再写 `interview_slot` ＋ `interview_slot_interviewer` ＋ `application_stage_history(action=scheduled, actor_type=human)`，三写同事务；幂等键 `{application_id}:effect_schedule_slot:{request_id}`；入口校验投递当前阶段 `stage_type=interview`。测试：重跑不产生第二场次；`screening` 阶段投递被拒
- [ ] 2.4 节点 `effect_reschedule_slot`：新时刻过 2.1；更新场次并写 history（含原时刻／新时刻）；幂等键 `{application_id}:effect_reschedule_slot:{slot_id}:{request_id}`；测试
- [ ] 2.5 节点 `effect_cancel_slot`（须 `cancel_reason`，记录保留）与 `effect_complete_slot`（`completed/no_show`，开始时刻前拒绝；HR 或该场面试官可操作）；各写 history；幂等键含 `slot_id` 与目标状态；测试：取消后记录仍在、开始前标完成被拒
- [ ] 2.6 不变式测试 `tests/test_interview_history_invariant.py`：对任一投递，`application_stage_history` 中面试相关条数 ＝ 该投递成功执行的排期动作次数（安排／改期／取消／完成），穷举含重跑的序列
- [ ] 2.7 面试官周视图页 `GET /interviewers/me/availability`（登记／撤销）与当日安排页 `GET /interviewers/me/schedule`（今日＋7 日，⛔ 不显示评分／排名／硬门槛／联系方式）；相对路径，子路径前缀测试
- [ ] 2.8 HR 排期页 `GET /applications/{id}/schedule`：面试官多选、轮次、时刻、形式；冲突原因全部展示；改期／取消／完成按钮；"已安排但邀约结果未回填 > 2 天"醒目标记（页面计算，无定时任务）
- [ ] 2.9 U2 e2e（httpx＋HTML 断言）：夹具把投递流转到 `interview` → 面试官登记时段 → HR 安排 → 冲突拒绝 → 改期 → 完成；全程子路径前缀；history 条数守恒

## 3. U3 邀约文案生成与回填

- [ ] 3.1 `invitation_template` v1 占位模板（岗位／轮次／时刻／形式／面试官称谓／联系人占位符；⛔ 无评分／排名／淘汰理由占位符，测试反证）；模板维护接口 `PUT /invitation-templates`（版本递增，不覆盖）；0.4 回件到后只换内容
- [ ] 3.2 `app/agents/invitation_drafter.py::compute_invitation_draft(slot, template, contact_hint) -> Draft` 纯函数：走 LLM 网关 json_schema 路径，`temperature=0`，`prompt_version=invite-v1`，模型标识取 API 响应 `model` 字段，`AuditHook` 留痕到 `analysis_run`；输出带 AI 生成标识（与 M1 JD 同一标识字符串）
- [ ] 3.3 节点 `effect_persist_draft`：写 `interview_invitation_draft`（版本递增）；幂等键 `{slot_id}:effect_persist_draft:{analysis_run_id}`；对 `cancelled` 场次拒绝生成。测试：重复生成产生新版本、旧版保留；重跑不重复
- [ ] 3.4 编辑与"标记为人工撰写"：编辑后仍带标识；标记动作记录 `authorship_marked_by/at` 与原 AI 版本；测试两个 Scenario
- [ ] 3.5 回填接口 `POST /interview-slots/{id}/invitation/outcome`：`sent(channel)/confirmed/declined(reason)/reschedule_requested`；记录操作人时刻；`declined` ⛔ 不写 `rejection_record`、投递阶段不动（测试断言 `rejection_record` 行数不变）；幂等：同状态重复提交无第二条留痕
- [ ] 3.6 系统外发接线（默认不可用）：`POST /interview-slots/{id}/invitation/send` 调 `deliver_candidate_message(type='interview_invitation', recipient=<从 U4 读取的联系方式拍平字符串，U4 关闭时为空 ⇒ 门禁按收件对象未知拦截>, confirmed_by=<HR>)`；⛔ 不新增外发路径；测试：总开关关 ⇒ 拦截入队并留痕"总开关关闭"；缺 AI 标识 ⇒ 拦截
- [ ] 3.7 邀约页 `GET /interview-slots/{id}/invitation`：生成、查看各版本、复制、编辑、标记人工、回填结果；AI 标识可见；相对路径
- [ ] 3.8 U3 e2e：安排 → 生成 → 复制 → 回填已发出 → 回填确认；`declined` 路径投递阶段不变

## 4. U4 候选人联系方式保管

- [ ] 4.1 `Settings.candidate_contact_vault_enabled` 默认 False；`is_candidate_contact_vault_enabled()` 每次求值（环境变量 > 配置 > 默认）AND 密钥 `CANDIDATE_CONTACT_KEY` 已注入；测试："配置开但密钥缺 ⇒ 关"
- [ ] 4.2 `app/storage/contact_vault.py`：AES-GCM 字段级加解密（优先复用 `requirements.txt` 已有依赖，无则登记新增依赖待 `.51` 冒烟）；密钥不落库不入版本库；日志脱敏测试：异常信息不含明文
- [ ] 4.3 登记接口 `POST /applications/{id}/contact`：开关关 ⇒ 拒绝并留痕尝试（不含明文）；投递阶段 < `interview` ⇒ 拒绝；成功 ⇒ 密文入库、记录登记人／时刻／来源；幂等：同投递重复登记覆盖密文并留痕一次"更新"
- [ ] 4.4 `read_contact(application_id, accessor, purpose)`：先写 `candidate_contact_access_log` 再解密；留痕失败 ⇒ 抛错不返回明文；已删除 ⇒ 返回 `purged`；测试覆盖 spec 四个 Scenario
- [ ] 4.5 节点 `effect_purge_contact`：投递终止（M2 批量确认淘汰／3.5 `declined` 且 HR 确认终止／入职完成）或留存到期触发；删密文、写 `purged_at/purge_reason`；幂等键 `{application_id}:effect_purge_contact:{reason}`；测试：重跑不报错不重复
- [ ] 4.6 存储层断言测试：直接 SELECT 联系方式列不匹配手机号／邮箱正则；去重路径不调用解密（M2 哈希口径不变，测试 mock 解密函数断言未被调用）
- [ ] 4.7 排期页与邀约页"显示手机号／邮箱"按钮：每次点击一条留痕；开关关时显示"功能未开启"
- [ ] 4.8 🔴 **开关开启**（Shao Peishen 亲自；前置 0.3 PIA 扩展签认）。判据：`.51` 环境变量 `CANDIDATE_CONTACT_VAULT_ENABLED=1` 且密钥已注入，由本人执行并在 `intent.md` 末尾记「联系方式保管开启 <日期>」

## 5. U5 合规断言与发版

- [ ] 5.1 `app/audit/assertions.py` 新增：① `candidate_contact` 表存在时明文列不得匹配手机号／邮箱正则；② `candidate_contact_access_log` 条数 ≥ 明文读取调用计数（以测试夹具计数器验证）；③ `rejection_record` 中不存在 `reason_type` 以外任何与"候选人拒绝邀约"相关的写入（`ai_score` 恒 0 断言原样沿用）
- [ ] 5.2 CI 接入三条断言；`tests/test_doc_size_budget.py` 通过；`requirements.txt` 与 `sync-to-server.sh` 白名单增量
- [ ] 5.3 HR 一页操作说明 `docs/interview-scheduling-guide.md`：面试官怎么登记时段、HR 怎么排期／改期、邀约怎么复制回填、哪些不能做（不自助、不自动发）
- [ ] 5.4 🔴 **`.51` 发版**（Shao Peishen 拍「发」，G3）。判据：`05-发布运行手册.md` 流程跑完，联系方式保管开关保持关闭（4.8 另行）
- [ ] 5.5 `docs/tech-debt.md`：TD-8 追加"首个调用方＝interview-scheduling 3.6"；TD-11「怎么还」追加 `effect_send_reminder` 幂等键约定 `{slot_id}:effect_send_reminder:{nth}`；登记"面试官代登记时段真实性"技术债
