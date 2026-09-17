**进度：7/72**（2026-09-17 `0917BA` 可见薄片重排：章节顺序 0→U1→U2→U2.5(第 9 章)→U3→U0→U4→U5 其余→U6→U7，1.4 按第七节口径完成，6.2 移入 9.3；2026-09-17 `0917AD` 立包；同日 `0917AF` 回填六问裁决：0.5／0.6 已定，8.7 移出本包留墓碑。🔴 = 不可代项（括号内写谁做）；⏸ = 待 Shao Peishen 裁决，对应 `design.md` Open Questions（当前 0 条）；每章 = 一个交付单元 = 一份 superpowers plan = 一条 worktree 分支。涉及副作用的任务已逐条写幂等策略。）

## 0. 前置门槛（不写代码；任一未过则对应下游单元不得发车）

- [ ] 0.1 🔴 **G1 私信附件真实帧确认（TD-51）**（Shao Peishen 或汤丽萍私信机器人一个测试文件 → 按日志键结构填 `ATTACHMENT_FIELD_PATHS_BY_MSGTYPE` → 重启值守）。判据：`docs/tech-debt.md` TD-51 销账。阻塞 U6 的回件导入路径（U6 代码不阻塞，见 6.4 本地路径）
- [ ] 0.2 🔴 **合规验收 #1 启动**（Shao Peishen 发起；PIA 报告 ＋ 候选人同意条款单列 AI 评估 ＋ 留存与删除策略；法务结论）。判据：三份文档落 `docs/compliance/` 并由本人签认。阻塞 U7 的 7.8 开闸
- [x] 0.3 🔴 **试运行岗位选定**（Shao Peishen 从 供应链总监／底层软件工程师／非标产品采购员 中选 1 个仍在招的）。判据：`intent.md` 末尾补一行「试运行岗位：<岗位> job_id=<…> profile_version=<n>」。阻塞 U6 的样本征集与 U7 试运行
- [ ] 0.4 🔴 **评测集标注安排**（汤丽萍牵头；按 0.3 岗位分批 ≤20 份，脱敏或历史离职样本；模板由 6.1 提供）。判据：`人事部#2` 跟进信里有排期回复
- [x] 0.5 ✅ 已裁决 2026-09-17（Shao Peishen 答 `1a，2a`）：Q1 BGE-M3 在 `.51` 本地 CPU 推理；Q2 扫描件用 PaddleOCR。U0 冒烟清单据此定（design D7／D14）
- [x] 0.6 ✅ 已裁决 2026-09-17（Shao Peishen 答 `3是，4b，5是，6是`）：Q4 申诉由 HR 代登记、Q5 bias 夹具另立包（8.7 墓碑）、Q6 手机号哈希不落明文、Q7 归档件留存 90 天。specs 与推荐项一致，无需回改

## 2. U1 数据模型（ATS 域 ＋ 评分审计域接线）

> 可见薄片（第 9 章 U2.5）的 U1 **必需子集**＝2.1／2.2／2.4／2.5 的 `hr_account`／2.7／2.8；其余（2.3、2.5 的 embedding 与 eval 表、2.6）建表成本低，与必需子集同一 plan 一次做完，⛔ 不拆成两个单元（design「交付单元与顺序」）。

- [ ] 2.1 `app/storage/db.py` 新增 `candidate`（姓名＋手机号哈希唯一）、`resume`（`sample_class` CHECK IN synthetic/anonymized/departed/live——`synthetic` 为合成替身样本（`scripts/gen_pilot_samples.py`），薄片期与脱敏样本同等可入库、`parser_version`、`parse_confidence`）、`resume_text_span`（`resume_id, span_id, start, end, text`）；全部 `CREATE TABLE IF NOT EXISTS`，⛔ 不进 `_ADDED_COLUMNS`
- [ ] 2.2 新增 `application`、`stage`（预置 `initial/screening/rejected` 三行，`stage_type` 语义标签）、`application_stage_history`（`actor_type` CHECK IN human/agent）；测试：状态不挂在 `candidate` 上
- [ ] 2.3 新增 `rejection_record`：`reason_type` CHECK IN `('hard_rule','human_decision')`、`rule_ref`、`appeal_status` CHECK IN `('none','requested','under_review','upheld','overturned')`、`decided_by`、`batch_id`；反证测试：直接 INSERT `ai_score` 被 CHECK 拒绝
- [ ] 2.4 新增 `resume_access_log`（`accessor, resume_id, access_type, at`，无内容列）、`field_review_queue`（`resume_id, field, machine_value, confidence, status, reviewed_by, reviewed_at, human_value`）、`screening_flag`（`application_id, profile_version, rule_ref, verdict CHECK IN pass/fail/skipped, reason, evidence_ref`；`fail` 时 `evidence_ref` 非空 CHECK）
- [ ] 2.5 新增 `resume_embedding`（`resume_id, model, dim, vector BLOB`）、`eval_sample` / `eval_annotation` / `eval_import_batch`（含「禁止训练用途」表注释，同 `analysis_run` 口径）、`hr_account`（用户名唯一、盐哈希口令）
- [ ] 2.6 `analysis_run` 增加 `run_type` 语义约定（`parse/rank`，可空列已存在的用 `prompt_version` 前缀区分，⛔ 不加列）；`criterion_score.evidence_ref` 的 JSON 形态 `{span_id,start,end}` 定为约定并加解析工具函数 + 测试
- [ ] 2.7 `tests/test_db_m2_schema.py`：新库建表齐全、老库（复制 `.51` 的 demo.db 结构）升级后既有表一行不改、全部 CHECK 反证
- [ ] 2.8 `scripts/create_hr_account.py`（建账号，幂等：同用户名重复运行只更新口令并提示）

## 3. U2 上传与解析管线（含置信度与校对队列）

- [ ] 3.1 `Settings.live_resume_intake_enabled` 默认 False；`is_live_resume_intake_enabled()` 每次求值（环境变量 > 配置 > 默认）AND 鉴权可识别 AND 访问留痕探针；测试覆盖 spec 四个 Scenario（含"配置开但身份未知 ⇒ 关"）
- [ ] 3.2 `AuthMiddleware.dispatch` 换成会话 cookie → `hr_account` 校验；`AuthContext` / `reviewer_of()` 签名不变；`/candidates* /resumes* /applications*` 未登录 401；登录页与登出接口；测试：`reviewer_of()` 返回真实用户名而非 `unknown:*`
- [ ] 3.3 上传接口 `POST /resumes/upload`（多文件、`job_id`、`sample_class` 必填且 ∈ `synthetic/anonymized/departed/live`；类型白名单 pdf/docx；逐文件结果；`live` 且闸关 ⇒ 整批拒 + 留痕尝试不存内容）；幂等：文件内容 SHA-256 + `job_id` 唯一，重复返回既有 `resume_id` 不重解析
- [ ] 3.4 文件 → 文本：文本型 PDF 直抽、Word 走 `python-docx`、扫描件走 PaddleOCR（design D14）；有效字符 < 阈值 ⇒ `resume.status='unreadable'` 进人工队列；分片器产出 `resume_text_span`（按段落，带 offset）；测试三种文件各一
- [ ] 3.5 `ResumeFields` Pydantic schema（六字段 × `{value, confidence, spans[]}`，缺失 = `not_mentioned`）；`app/agents/resume_parser.py::compute_parse(text_spans) -> ResumeFields` 纯函数，走 LLM 网关 json_schema 路径，抽取模型＝deepseek-flash（1.7 已定型行；模型标识以 API 响应 `model` 字段为准），走 `AuditHook` 留痕（`prompt_version=parse-v1`）
- [ ] 3.6 置信度合成：模型自报 × span 可定位性（`quote` 反查校正偏移，反查失败 ⇒ 无 span ⇒ 低置信度）；阈值读 `job.parse_confidence_threshold`（默认 Q3 值）
- [ ] 3.7 LangGraph 节点 `effect_persist_parse`：写 `resume.parsed_json` ＋ `field_review_queue` 低置信度行；幂等键 `{application_id}:effect_persist_parse:{parser_version}`，`effect_log` 与业务写同事务；测试：节点重跑不产生第二份解析版本
- [ ] 3.8 重解析：新 `parser_version` ⇒ 新版本并存、旧版保留、工作台默认最新；测试
- [ ] 3.9 简历访问留痕：读取原文／分片／解析结果／下载的接口统一经 `record_resume_access()`，留痕失败 ⇒ 读取失败不返回内容；测试覆盖四种 access_type 与失败路径
- [ ] 3.10 校对接口 `POST /resumes/{id}/fields/{field}/review`：写 `field_review_queue.human_value/reviewed_by/at`、关闭队列行、触发该投递重判（发一个内部事件，⛔ 不在接口里同步跑判定）；幂等：同字段同值重复提交不产生第二行

## 9. U2.5 可见薄片（上传入口＋解析结果列表＋逐字段 evidence 高亮＋校对确认；顺序位于 U2 之后、U3 之前——章号 9 只为保持 4.x–8.x 既有编号稳定）

> 2026-09-17 `0917BA` 按路线图第七节「人事部可见优先」切出。范围＝人事部第一眼能看到的最小闭环：传简历 → 看到解析出的六字段 → 点字段看原文依据 → 校对确认。前置＝U1 必需子集（见第 2 章注）＋ U2 全部（3.1–3.10）＋ U0 已定型的抽取模型行。只收 `sample_class ∈ {synthetic, anonymized, departed}`；真实简历入库闸保持关闭（design D2 不变，8.8 仍由本人亲自开）。⛔ 不含评分、排序、硬门槛、淘汰——那些在 U3／U4／U5 其余。

- [ ] 9.1 上传入口页（`app/web/static/` 新页 + `GET /resumes/upload`）：多文件选择、岗位下拉（已审批画像）、样本类别单选**只列** `synthetic/anonymized/departed`（`live` 不出现在页面选项，接口侧仍由 3.3 的闸拦）；逐文件结果表（成功／重复返回既有 `resume_id`／不可读／拒收原因）；相对路径（部署约束 1，测试在子路径前缀下可用）
- [ ] 9.2 解析结果列表页 + `GET /jobs/{id}/resumes`：按上传时间倒序，每行＝候选人姓名（缺则文件名）、`sample_class`、解析状态（`parsed/unreadable/pending`）、六字段摘要、待校对字段数、`parser_version`；AI 生成标识与"仅供参考"说明（合规红线）；读取经 3.9 留痕；⛔ 不显示分数与排名（那是 6.1）
- [ ] 9.3 字段校对页（原 6.2 移入）：左原文右六字段，每字段显示 `value/confidence`，选中字段 ⇒ 原文按 `spans[]` offset 逐段高亮（evidence 高亮，按 `resume_text_span` 偏移切字符串）；低置信度／队列字段醒目；「确认」「改为…」提交走 3.10；确认后 9.2 列表该字段显示"已校对（谁／何时）"
- [ ] 9.4 薄片 e2e（httpx＋HTML 断言）：上传 3 份合成样本（文本 PDF／Word／扫描件各一）→ 9.2 列表可见 → 9.3 高亮命中 span → 确认后列表更新；全程在子路径前缀下；`live` 在页面不可选且接口拒收；每次读取在 `resume_access_log` 各一条
- [ ] 9.5 薄片交付件：HR 一页操作说明 `docs/m2-visible-slice-guide.md`（人事部看什么、怎么传、怎么校对、哪些不能传）、`requirements.txt` 与 `sync-to-server.sh` 白名单增量、`scripts/create_hr_account.py` 建账号步骤。发版本身走 G3（8.9），⛔ 不在本条

## 4. U3 硬门槛引擎（标记＋依据＋申诉）

- [ ] 4.1 `app/agents/hard_requirement_screening.py::screen(fields, rules, review_queue) -> list[RuleVerdict]` 纯函数：五种 operator 各实现；依赖字段在队列 ⇒ `skipped(待校对)`、`not_mentioned` ⇒ `skipped(未提及)`；`fail` 必带 `evidence_ref` 与 `human_readable`。测试：每个 operator × 三态；模块内 grep 不得出现 storage 写入
- [ ] 4.2 规则集加载器：按 `(job_id, profile_version)` 读 `hard_requirement`；命中 `is_subjective()` 的规则被标 blocking ⇒ 拒绝加载并可观测（`candidate-ranking` spec「主观描述不入硬门槛」）；空规则集 ⇒ 全 pass 并注明
- [ ] 4.3 节点 `compute_screen` → `effect_persist_flags`：幂等键 `{application_id}:effect_persist_flags:{profile_version}:{parse_version}`，同事务；重判（校对完成／画像升版）产生新一组 flags，旧组保留带版本；测试：重跑不重复、三种触发点各一
- [ ] 4.4 拒绝记录写入路径唯一：`app/storage/rejection.py::write_rejection(...)`，应用层校验 `reason_type ∈ {hard_rule, human_decision}` 且 `hard_rule` 必带 `rule_ref`；测试：传 `ai_score` 在应用层被拒，绕过应用层在 CHECK 被拒
- [ ] 4.5 申诉状态机：`none → requested → under_review → upheld | overturned`，非法跳转拒绝；`overturned` ⇒ 投递恢复到淘汰前阶段 ＋ 写 `application_stage_history(actor_type=human)`；幂等：同记录同目标状态重复提交无第二条流转；原拒绝记录不删
- [ ] 4.6 接口 `POST /applications/{id}/appeal`（登记）与 `POST /rejections/{id}/appeal/transition`（流转），均记操作人；Q4 ✅ 已裁决 2026-09-17：HR 代候选人登记，本期不开候选人自助入口

## 1. U0 模型对比定型（剩余项只前置 U4 召回精排；抽取模型已定型——本章在顺序链上排在 U3 之后、U4 之前，`0917BA`）

> 2026-09-17 `0917BA` 按路线图第七节重排：抽取模型＝deepseek-flash 已定型（1.7 部分确认），这是 U2／U2.5 可见薄片唯一需要的 U0 产出；1.2 真实样本、1.6 精排模型与阈值终值、1.7 其余行只前置 U4 精排，⛔ 不再阻塞 U1→U2→U2.5→U3。章号 1 不改（既有 1.x 编号稳定），只是物理位置后移。

- [x] 1.1 在 `.51` 同款 Windows venv 上冒烟安装：`python-docx`、PDF 文本抽取库、PaddleOCR（Q2 已裁决）、本地 CPU BGE-M3（Q1 已裁决，`FlagEmbedding` 或 `sentence-transformers` 二选一按可装性定）；记录可装性与体积到 `docs/m2-model-comparison.md`「环境」节 ✅ 2026-09-17 Mac＋`.51` 隔离 venv 两侧均已跑（`0917AX`）：轻依赖六项可装；paddlepaddle／paddleocr 不可装（cp314 无 wheel、paddlex 钉 PyYAML==6.0.2）；FlagEmbedding 可装但 `.51` 需升级 VC++ 运行库才能 import（后续单独 opener，见 docs/m2-model-comparison.md「待裁决」#5）
- [ ] 1.2 准备对比样本：从已有脱敏样本中取 ≥20 份（含 ≥3 份扫描件、≥3 份 Word），人工标注六字段与一次人工排序，存 `data/eval/m2-pilot/`（不进版本库，`.gitignore` 登记）⏸ 留步：合成替身已到位（scripts/gen_pilot_samples.py，20 份 ×4 形态），真实脱敏样本待 U0 计划待裁决 #2
- [x] 1.3 扩展 `scripts/compare_models.py` 方法为 `scripts/compare_models_m2.py`：对每个候选模型跑「抽取 → 精排」，输出字段准确率、Spearman、Top-10 召回、span 可回溯率、P50/P95 延迟、每份成本
- [x] 1.4 ⚑ 口径调整（Shao Peishen 2026-09-17 21:3x，路线图第七节）：「DeepSeek 两款先行即满足主航道，第 3／4 家后补（非阻塞）」。DeepSeek 两款已对 1.3 实跑 20/20（模型标识取 API 响应 `model` 字段；json_schema／json_object 支持与 evidence 位置质量数据见 `docs/m2-model-comparison.md`）⇒ 按新口径本条已完成 ✅ 2026-09-17 `0917BA`。原文「≥3 个境内 LLM」作废，⛔ 不再作为任何单元的前置
- 后补（非阻塞、不计进度、不阻塞任何单元）：火山方舟（doubao）／阿里百炼（qwen）各跑一次 1.3 对比并追加到 `docs/m2-model-comparison.md`；前置＝定夺队列 Q-07 拿到 `ARK_API_KEY`／`DASHSCOPE_API_KEY`。⛔ 结果不改已定型的抽取模型
- [x] 1.5 对本地 CPU BGE-M3 测召回：以人工排序前 10 为真值，测 top-30 召回率与单份耗时（Mac CPU 合成样本：单份 ~58 ms、recall@10=40%、n=20 故 top-30 无意义；.51 Windows CPU 与真实样本复算见留步）
- [ ] 1.6 写 `docs/m2-model-comparison.md`「决策」节：抽取模型、精排模型、embedding 方案、置信度阈值起步值（Q3）、扫描件路径；判据：每项都有数据支撑，模型标识非别名。薄片前置仅「抽取模型」一行（已定型 deepseek-flash）；精排／embedding／阈值行只前置 U4 ⏸ 留步：决策节已建、抽取/精排/阈值三行待真实脱敏样本数据
- [ ] 1.7 🔴 **定型确认**（Shao Peishen 签认 1.6 的决策节）。判据：文档末尾有「已确认 <日期>」 ⏸ 2026-09-17 部分确认：抽取模型＝deepseek-flash（1.7b）——此行即 U2／U2.5 薄片的全部前置，已满足；其余行待真实脱敏样本复算后签，只前置 U4

## 5. U4 召回＋rubric 精排＋evidence span

- [ ] 5.1 embedding 适配器（本地 CPU BGE-M3，Q1 已裁决；具体装包按 1.6 定型），接口 `embed(texts) -> vectors`；写 `resume_embedding`，幂等键 `{resume_id}:embed:{model}`；上传后离线批算，不在页面请求路径
- [ ] 5.2 召回：岗位画像文本向量 vs 该岗全部通过／待决投递的简历向量，numpy cosine 取 top-K（`job.recall_top_k` 默认 30）；未召回投递标 `not_recalled`；测试：K 边界、空集、全量 100 份耗时 < 1s
- [ ] 5.3 rubric 派生：从冻结画像生成 `scoring_criterion` 快照（维度 key 必在 `CRITERION_KEY_WHITELIST`）；软技能只进 rubric 不进规则；测试：白名单外 key 拒绝
- [ ] 5.4 `app/agents/ranker.py::compute_rank(fields, spans, rubric) -> RankResult` 纯函数：输出 schema 每维 `{score, evidence:{span_id,start,end,quote}}` 强制；`quote` 反查校正偏移；任一维缺证据 ⇒ 整次不可用；走 `AuditHook`（`prompt_version=rank-v1`，`temperature=0`，rubric 快照）
- [ ] 5.5 节点 `compute_recall_rank` → `effect_persist_scores`：写 `analysis_run` 关联 + N 条 `criterion_score`；幂等键 `{application_id}:effect_persist_scores:{analysis_run_id}`，同事务；评分失败 ⇒ 投递标 `rank_failed` 可重试，不落分；测试：重跑不重复、留痕失败 ⇒ 不进排序
- [ ] 5.6 手动加入精排接口 `POST /applications/{id}/rank`：留痕"人工加入"；幂等：已有当前版本评分则返回既有
- [ ] 5.7 总分与排名只入列表查询（`app/storage/candidate_queries.py`），⛔ 不写任何阶段流转；测试：排名末位投递状态不变、无拒绝记录

## 6. U5 工作台页面·其余（列表／复核／批量确认；字段校对页已随 `0917BA` 移入第 9 章 U2.5 可见薄片）

- [ ] 6.1 候选人列表页 + `GET /jobs/{id}/applications`：按总分降序、未评分排末并注明原因；筛选（阶段／硬门槛状态／待校对）；AI 标识与"仅供参考"说明；相对路径（部署约束 1，测试在非根前缀下可用）
- ~~6.2 字段校对页：左原文右六字段，置信度与来源高亮（按 span offset 切字符串）；队列字段醒目；提交走 3.10~~ ⚰️ 已移入 9.3（`0917BA` 可见薄片），不是漏跑
- [ ] 6.3 逐份复核页：硬门槛逐条（依据高亮）、逐维评分（证据摘录高亮）、原文；动作：进入下一阶段／标记淘汰（待确认）／加入精排／登记申诉，每个动作写 `human_review`
- [ ] 6.4 "标记淘汰"接口：投递进入待确认清单（`application.kanban_state='pending_reject'` + 理由类型），⛔ 不产生拒绝记录、阶段不变；幂等：重复标记无第二条
- [ ] 6.5 批量确认页 + `POST /rejections/batch-confirm`：服务端按勾选集合哈希生成 `batch_id`；先写 `human_review(batch_id)`，再逐 thread 唤醒 `effect_apply_batch_decision`；无理由条目拒绝并提示其余照常；⛔ 不提供任何按分数线的批量动作（测试：接口不接受 score 参数）
- [ ] 6.6 节点 `effect_apply_batch_decision`：写 `rejection_record` + `application_stage_history` + 阶段更新，幂等键 `{application_id}:effect_apply_batch_decision:{batch_id}`，同事务；测试：同批重复提交无重复、节点重跑无重复、`human_review` 条数与 `rejection_record` 条数按 batch 恒等
- [ ] 6.7 流程挂起：`effect_persist_scores` 后 `interrupt()`，SqliteSaver 持久化；测试：进程重启后从挂起点恢复、三天后仍可继续
- [ ] 6.8 页面 e2e（Playwright 或 httpx+HTML 断言）：上传 → 校对 → 复核 → 批量确认 全链路在子路径前缀下跑通

## 7. U6 评测集导入与指标脚本

- [ ] 7.1 「判例批改表」xlsx 模板（固定列：样本标识、岗位、六字段人工值、人工排序名次、标注人、标注时刻）与填写说明，存 `docs/templates/m2-判例批改表.xlsx` + `.md`；给 0.4 用
- [ ] 7.2 `scripts/eval_m2.py import <file> --job <id> --source <archive_path|local>`：逐行校验（必填列、类型、样本存在、类别非 live、≤20 行），任一失败整批拒并列出行；幂等：同批次重复导入不重复，值变化以最新为准保留历史；记录归档件路径 ↔ 批次
- [ ] 7.3 指标计算 `scripts/eval_m2.py report --job <id>`：字段准确率（design D9 归一化口径）、Spearman、Top-10 召回、span 可回溯率；门槛与通过/不通过；样本 < 10 ⇒ "不足"；记录模型／prompt／解析器版本；只读（测试：跑前后业务表哈希一致）
- [ ] 7.4 本地路径导入兜底（不依赖 G1 链路）：`--source local` 直接读文件；测试用 3 份合成样本跑通 import → report
- [ ] 7.5 评测集目录 `data/eval/` 加 `.gitignore` 与 README（访问控制、留存期 90 天——Q7 已裁决、禁止训练用途）；`tests/test_eval_no_training_use.py`：grep 训练／微调相关 import 不得出现在 `scripts/eval_m2.py`
- [ ] 7.6 🔴 **首批标注回件并导入**（汤丽萍标注；Shao Peishen 确认导入结果）。判据：`report` 输出非"样本不足"
- [ ] 7.7 用全部已标注样本跑 `report`，结果落 `docs/m2-eval-report.md`；未达标项回到对应单元返工并登记

## 8. U7 合规断言与真实简历入库闸

- [ ] 8.1 `app/audit/assertions.py`：`REJECTION_TABLE` 缺表分支从"M1 现状放行"改为判失败；反证测试同步改；⚠️ 与 U1 建表在同一交付单元合并，不跨 session
- [ ] 8.2 新增断言「评分 100% 可回溯」：`criterion_score.evidence_ref` 解析后分片存在且偏移不越界；反证：造一条越界记录
- [ ] 8.3 新增断言「真实简历入库闸默认关闭」：无配置环境下 `is_live_resume_intake_enabled()` 为 False；反证：临时改默认值
- [ ] 8.4 新增断言「简历访问留痕不可缺」：表缺失判失败；被读取过的简历至少一条留痕；反证
- [ ] 8.5 `-m compliance` 标记覆盖 8.1–8.4，CI 接入；`tests/test_audit_assertion_effectiveness.py` 扩展四条反证
- [ ] 8.6 合规文档接线：`docs/compliance/` 放 PIA／同意条款／留存策略的落档位置与索引（内容由 0.2 产出，本条只建目录与索引）
- ~~8.7 bias 回归夹具纳入本单元（改造 `re-cinq/hiring-bias`，盲筛对照轴）~~ ⚰️ 已移出本包，不是漏跑：Q5 裁决 2026-09-17 答 (b) 另立包（design D15；真实简历入库闸开启前做不了）
- [ ] 8.8 🔴 **真实简历入库闸开启**（Shao Peishen 亲自改配置；前置：0.2 通过 + 3.2 上线 + 8.4 绿）。判据：`.51` 上 `live` 上传被接收且访问留痕可查
- [ ] 8.9 🔴 **`.51` 发版决定**（Shao Peishen；含 U0 冒烟通过的新依赖、`sync-to-server.sh` 白名单更新）。判据：`docs/deploy-51-server.md` 记录本次发版与依赖
- [ ] 8.10 试运行：0.3 选定岗位在闸开启后跑一批真实简历，用真实样本复算一次四项指标落 `docs/m2-eval-report.md`「真实样本」节；🔴 试运行期间的任何淘汰确认由 HR 执行、Shao Peishen 抽查留痕
