**进度：0/28**（2026-09-17 `0917BF` 立包。🔴 = 不可代项（括号内写谁做）；每章 = 一个交付单元 = 一份 superpowers plan = 一条 worktree 分支。涉及副作用的任务已逐条写幂等策略；单条任务 ≤ 2 小时。design.md Open Questions 当前 7 条，均不改变任务分解。）

## 0. 前置门槛（不写代码；任一未过则对应下游单元不得发车）

- [ ] 0.1 **M2 U2 上传接口与 U1 `candidate` 表交付**。判据：M2 tasks 2.1／3.3／3.4 勾选。阻塞 U1 全部（本包叠加在其上，无夹具替代）
- [ ] 0.2 **人事部#3 回件：渠道清单与占比／各平台脱敏导出样例／邮箱现状／重复口径**（汤丽萍，经跟进信线；样例只收脱敏件，⛔ 不经值守通道）。判据：四项归档且 design OQ1–OQ4 回填。阻塞 U4 的规则表真值（占位规则不阻塞）
- [ ] 0.3 🔴 **合规验收 #1 入库闸开启**（Shao Peishen 亲自，M2 8.8）。判据：M2 tasks 8.8 勾选。阻塞真实导出包入库（开发期用 `synthetic/anonymized/departed`）
- [ ] 0.4 🔴 **邮箱凭据／ATS 采购**（Shao Peishen；本包不需要，Q1a 已定）。⚰️ 墓碑：本包内保持"不需要"，Q1b／Q1c 重提时另立包

## 1. U1 导出包解包与来源识别

- [ ] 1.1 `app/intake/bundle.py::unpack_bundle(data: bytes, limits) -> list[FileEntry]` 纯函数：标准库 `zipfile`；一层子目录；白名单 pdf/docx；超限（文件数／总大小／压缩比）抛 `BundleTooLarge`；损坏条目标 `unreadable`；两层以上目录标 `too_deep`；测试覆盖 spec 四个 Scenario ＋ ZIP 炸弹夹具；模块内无 storage 写入
- [ ] 1.2 `app/intake/source.py::detect_source(filename, first_page_text) -> Source | None` 纯函数：`Source` 枚举 `boss/liepin/51job/zhaopin/referral/other/unknown`；规则表 `SOURCE_RULES`（文件名正则＋首页关键词）占位版；⛔ 不抽取水印之外字段（测试：输出只有枚举值）；夹具目录 `tests/fixtures/source_rules/`
- [ ] 1.3 `app/storage/db.py`：`resume` 加 `source`（可空）与 `source_origin CHECK IN detected/default/corrected`（走 `_ADDED_COLUMNS`）；新增 `source_correction_log`（`resume_id, from_source, to_source, corrected_by, at`）；`candidate.source` 重算为最早简历来源的查询函数；测试：老库加列后既有行 `source` 为 NULL 视为 `unknown`
- [ ] 1.4 上传接口 ZIP 分支：`POST /resumes/upload` 遇 `.zip` ⇒ `live` 先求值入库闸（关 ⇒ 整包拒收＋留痕尝试不存内容）⇒ 1.1 展开 ⇒ 逐文件调既有单文件接收 ⇒ 每文件 `source` ＝ 1.2 识别 ∥ `default_source` 参数 ∥ `unknown`；包内同哈希只入一次标"包内重复"；节点 `effect_ingest_bundle` 幂等键 `{job_id}:effect_ingest_bundle:{bundle_sha256}`（重跑返回上次逐文件结果）；不传 ZIP／不传 `default_source` 时行为与 M2 完全一致（回归测试）
- [ ] 1.5 来源改正接口 `POST /resumes/{id}/source`：写 `source_correction_log`，`source_origin=corrected`；幂等：同值重复提交无第二条留痕；测试
- [ ] 1.6 上传页增量：接受 `.zip`、默认来源下拉（值域固定）、逐文件结果表新增"来源／包内重复／不可读"列；相对路径，子路径前缀测试
- [ ] 1.7 U1 e2e：合成样本打成 ZIP（含 1 个 xlsx、1 个损坏 PDF、2 个相同 PDF、1 个两层目录文件）→ 上传 → 逐文件结果符合 spec → 重传同 ZIP 返回相同结果且简历数不变

## 2. U2 去重合并

- [ ] 2.1 `app/intake/phone_hash.py::extract_phone_hash(text_spans) -> str | None` 纯函数：中国大陆手机号正则 → 取第一个 → 立即按 M2 既定哈希函数哈希 → 返回哈希；明文不出函数（测试：返回值不匹配手机号正则、日志无明文）
- [ ] 2.2 节点 `effect_attach_resume_to_candidate`：在 M2 `effect_persist_parse` 之后；按（姓名规范化＋哈希）查 `candidate`：命中 ⇒ `resume.candidate_id` 指向既有；不命中 ⇒ 新建 `candidate`（`source`＝该简历来源）；幂等键 `{resume_id}:effect_attach_resume_to_candidate`；与业务写同事务；测试：命中挂接、无手机号新建、重跑不重复
- [ ] 2.3 `app/intake/duplicates.py::detect_suspected_duplicates(candidate, same_job_candidates) -> list` 纯函数：姓名规范化（去空白、全半角）相同且同岗位 ⇒ 疑似；⛔ 无模型调用（grep 反证）；测试
- [ ] 2.4 `app/storage/db.py` 新增 `candidate_merge_log`（`primary_id, secondary_id, reason, secondary_snapshot JSON, merged_by, merged_at, unmerged_by, unmerged_at`）；`candidate` 加 `merged_into`（可空，走 `_ADDED_COLUMNS`）
- [ ] 2.5 节点 `effect_merge_candidates(primary, secondary, reason, keep_application_per_job)`：写快照 → 改 `resume.candidate_id` / `application.candidate_id` → 同岗位双投递时按 HR 选择保留一份、另一份写 `application_stage_history(action=closed_by_merge, actor_type=human)` 不删 → `secondary.merged_into=primary`；同事务；幂等键 `{primary}:{secondary}:effect_merge_candidates:{request_id}`；测试：合并、双投递提示、重跑不重复
- [ ] 2.6 节点 `effect_unmerge_candidates(merge_log_id)`：按快照恢复 secondary 原有 `resume`／`application` 归属；合并期间 primary 新增记录保留在 primary；写 `unmerged_by/at`；幂等键 `{merge_log_id}:effect_unmerge_candidates`；测试："合并→新增→撤销"三段
- [ ] 2.7 候选人列表页增量：来源列、"疑似重复"标记与跳转；合并页 `GET /candidates/{id}/merge`（选主、填依据、双投递选择、撤销按钮）；相对路径
- [ ] 2.8 U2 e2e：两份同手机号不同来源 ZIP → 自动挂接同一候选人 → 一份无手机号同名同岗 → 疑似重复 → HR 合并 → 撤销 → 归属恢复；`candidate_merge_log` 条数守恒

## 3. U3 来源进流转事实

- [ ] 3.1 `application_stage_history` 加 `source`（可空，走 `_ADDED_COLUMNS`）；M2 创建投递写初始流转事实处带上 `resume.source`；测试：既有 M2 断言与查询不受影响
- [ ] 3.2 1.5 来源改正 ⇒ 追加 `application_stage_history(action=source_corrected, source=<new>)`，⛔ 不改写初始记录；同事务；幂等由 1.5 承担；测试：初始记录保持原值
- [ ] 3.3 查询函数 `source_distribution(job_id)`（只读，从流转事实表按来源计数）＋测试；⛔ 无报表页（HTML 路由不存在，测试反证 404）
- [ ] 3.4 `app/audit/assertions.py` 新增：① `candidate` 无明文手机号列（M2 断言沿用并扩到 `resume.source_*`）；② `candidate_merge_log` 每行 `merged_by` 非空；③ `candidate.merged_into` 非空的候选人无活跃投递；CI 接入

## 4. U4 交付与发版

- [ ] 4.1 规则表迭代：0.2 样例到后按平台补 `SOURCE_RULES` 与夹具，识别率目标每平台 ≥ 90%（在脱敏样例上）；记录到 tasks 本条
- [ ] 4.2 技术探针数据：上传结果按来源统计"不可读比例"，落 `docs/m2-model-comparison.md`「环境」节（design OQ6）
- [ ] 4.3 HR 一页操作说明 `docs/channel-resume-intake-guide.md`：怎么导出、传 ZIP、指定默认来源、疑似重复怎么合并／撤销、哪些不能传（`live` 闸关时）
- [ ] 4.4 `docs/tech-debt.md`：登记"图片型导出件手机号取不到 ⇒ 去重退化为疑似重复"的已知代价（随 M2 D14 退路）
- [ ] 4.5 🔴 **`.51` 发版**（Shao Peishen 拍「发」，G3）。判据：`05-发布运行手册.md` 流程跑完，入库闸状态不变
