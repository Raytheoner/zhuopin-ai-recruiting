## Context

动机见 `proposal.md`「Why」；需求收敛结论见同目录 `intent.md`（D1–D10）。本节只写约束现状。

**现有地基（M1 已交付，本包直接消费）**

- 编排：LangGraph 图骨架，`compute_*` / `effect_*` 节点分离，checkpointer 是 **SqliteSaver**（不是设计文档里写的 Postgres——M1 选型对齐现实后走 SQLite + Web 通道，见 `m1-job-profile-intake/tasks.md` 2026-08-20 对齐说明）。幂等键 `{thread_id}:{node_name}:{business_key}` 落 `effect_log`，与业务写同事务（`effect-transaction-integrity` spec）
- LLM 网关：双供应商切换、版本锁定、`temperature=0`、json_schema / json_object 两条路径、`AuditHook` 留痕到 `analysis_run`（含 `response_model` 与 `system_fingerprint`）
- 审计域：`analysis_run` / `criterion_score`（`evidence_ref` 非空由 CHECK 强制）/ `human_review`（已预留 `batch_id` 列）；`app/audit/assertions.py` 四条断言，`REJECTION_TABLE` 常量已预留、缺表当前放行
- 硬门槛：`hard_requirement` 表按 `(job_id, profile_version)` 存规则，operator 集合 `gte / education_gte / contains / equals / is_true`，**M1 明令该表只存不执行**——执行逻辑第一次出现就是本包
- 鉴权：`app/middleware/auth.py` 空壳，`reviewer_of()` 返回 `unknown:web-session`；部署约束 5 要求 M2 处理真实简历前换成可识别到人
- 部署：`.51` Windows、无 Docker、Python venv + 计划任务；新依赖必须在 Windows 上可 pip 装
- 数据规模：年招几十到百人，单岗投递量两位数到低三位数；评测集 200 份。**不是高并发场景**

**外部依赖现状**

- 评测集回件链路：G1 私信附件真实帧尚未确认（TD-51，`0917W` 已接好 fail-closed 骨架），表不填附件进不了归档
- 合规验收 #1（PIA ＋ 同意条款 ＋ 留存策略）未启动，是真实简历入库的硬前置

## Goals / Non-Goals

**Goals:**

- 在 SQLite + 单进程的现实底座上跑通"上传 → 解析 → 标记 → 排序 → 挂起 → 人工批量确认"闭环，评测集四项指标可一键复算
- 首次触碰候选人个人信息时，把"谁能上传真实简历、谁看过哪份简历、谁淘汰了谁、依据是什么"四问全部变成可查询、可断言的事实
- 让 M1 的 `hard_requirement` 表第一次被"执行"，且执行方式在结构上无法产生淘汰

**Non-Goals:**

- 不做水平扩展、不做异步任务队列——单进程顺序处理一批上传在当前规模下够用
- 不把 SQLite 换成 Postgres——那是独立的基础设施变更，本包在 SQLite 上把行为做对，迁移只换存储实现
- 不做候选人自助申诉入口——申诉由 HR 代登记（候选人对外通道属不可代项，本包不开）
- 不做人才库的检索与二次推荐页

## Decisions

### 决策 D1：输入只有 Web 手工批量上传（对应 intent D1）

**做法**：一个上传接口，多文件、指定岗位与样本类别。⛔ 不接渠道、⛔ 不经企微机器人私信收简历。

**为什么**：年招几十到百人，HR 从渠道手工导出的成本极低（§0 已定事项）；接渠道要签 API、锁供应商、且把候选人信息导入路径扩大到我们无法审计的第三方。值守通道不收简历是章程红线 ⑨——值守归档一旦含候选人个人信息，整条通信线的合规等级就变了。

**替代方案**：企微私信直接发简历给机器人。否决——违反章程红线，且把 PIPL 处理边界扩到值守服务。

### 决策 D2：真实简历入库闸 = 独立布尔开关 + 两个结构性前置（对应 intent D2）

**做法**：`Settings.live_resume_intake_enabled`，默认 `False`，每次上传时求值（环境变量 > 配置文件 > 默认），⛔ 不缓存。求值时再 AND 两个前置：① 当前请求鉴权上下文 `authenticated=True` 且 `user_id` 非 `unknown:*`；② 访问留痕表存在且写入探针通过。任一不成立即视为关闭。开关文件的修改权只在 Shao Peishen。

**为什么**：口径与 M1 `candidate_outbound_enabled` 完全一致（默认关、每次求值、开启是人工决定），reviewer 与 HR 都不用学第二套。两个结构性前置是把部署约束 5 变成代码而不是流程——"登录没换成真实身份就开闸"在结构上不可能发生。

**替代方案**：用样本类别字段自证（HR 自己标 `anonymized` 就放行）。否决——闸的意义是防"误传真实简历"，自证等于没闸；但样本类别仍要收，它是评测集来源合规的记录。

### 决策 D3：评测集经跟进信线发、私信附件回件，导入格式由本包定义（对应 intent D3）

**做法**：「判例批改表」是一份固定列的 xlsx 模板（样本标识、岗位、六字段人工值、人工排序名次、标注人、时刻），每批 ≤20 份；导入脚本 `scripts/eval_m2.py import <file>` 逐行校验、幂等入库；归档件路径与批次对应关系落表。归档件留存期：导入完成且指标落档后 **90 天**删除，以合规验收 #1 的留存策略为准（✅ 已裁决 2026-09-17，原 Q7）。

**为什么**：≤20 份是汤丽萍单次能认真标完的量；模板固定列是让"回件能被机器读"而不是拆件会话猜格式。导入放本包而不放 liaison 包，因为格式随 M2 字段 schema 演进，归属应跟 schema 走。

**依赖**：G1 附件真实帧确认（TD-51）——未销账前批改表回不来。本包 §0 前置门槛登记，⛔ 不在本包里修 liaison。

### 决策 D4：首期六字段，ECU 特化字段二期（对应 intent D4）

**做法**：`ResumeFields` Pydantic schema 只含 姓名／工作年限／技能列表／公司经历／教育／期望城市，每字段 `{value, confidence, spans[]}` 三元组；`property_definition / property_value` 表本包不建。

**为什么**：验收指标 D9 的"关键字段准确率"就是按这六个算；ECU 特化字段（AUTOSAR 层、功能安全等级）抽取难度高一档、评测标注也难一档，混进一期会把 90% 的门槛拖垮。M1 的 `hard_requirement` 里已有 `autosar` / `functional_safety` 类规则——本期这些规则的依赖字段"未提及"⇒ `skipped`，不误判。

### 决策 D5：置信度阈值是岗位级配置，低置信度字段"隔离"而不是"丢弃"（对应 intent D5）

**做法**：`job.parse_confidence_threshold`（默认 **0.7 起步**，U0 实测后定——Q3 未向 Shao Peishen 提问，按推荐值起步，由 U0 数据定终值）；置信度来源 = 模型自报置信度 × span 可定位性（无 span 直接判低）。低于阈值 ⇒ 写 `field_review_queue` 一行；硬门槛引擎读取字段时先查队列，命中即 `skipped(待校对)`。校对完成 ⇒ 队列行关闭 ⇒ 重新触发该投递的判定。

**为什么**：D5 的本意是"机器没把握就交给人"，不是"机器没把握就当没有"。隔离到队列同时保住两件事：不因低置信度值误判 fail（合规），也不因丢弃而漏掉可校对的信息（准确率）。

**替代方案**：全局阈值。否决——岗位间简历质量差异大（供应链总监 vs 底层软件工程师），一个阈值必然一边过严一边过松。

### 决策 D6：硬门槛引擎是纯函数，输出只有标记；淘汰是 effect 节点里的人工动作（对应 intent D6）

**做法**：`app/agents/hard_requirement_screening.py::screen(fields, rules) -> list[RuleVerdict]`，纯函数，`pass/fail/skipped` 三态，`fail` 必带 `evidence_ref`。标记落 `screening_flag` 表（`effect_persist_flags` 节点）。拒绝记录只由 `effect_apply_batch_decision` 节点写，且写之前校验 `reason_type ∈ {hard_rule, human_decision}`；表上 CHECK 二次强制。申诉状态机 `none → requested → under_review → upheld | overturned`，`overturned` 触发一次阶段恢复流转。

**为什么**：这是"AI 只排序不淘汰"红线在 M2 的落点。把"判定"和"淘汰"拆成两个不同层的东西（纯函数 vs effect 节点），reviewer 只需 grep：`screening` 模块里不得 import 任何 storage 写入。CHECK 是第二道防线，断言是第三道。

**替代方案**：`blocking=1` 的规则 fail 后自动流转到"已淘汰"。否决——§0 已定事项写"规则硬门槛自动 + AI 排序后批量确认"，但 intent Q6a 收敛为"只标记，淘汰一律 HR 批量确认"，后者更严，取后者。

### 决策 D7：BGE-M3 召回 + LLM rubric 精排；U0 先定型（对应 intent D7）

**做法**：召回段 = BGE-M3 dense 向量（岗位画像文本 vs 简历全文），cosine 相似度取 top-K（岗位级配置，默认 30）；精排段 = LLM 按 rubric 逐维打分，输出 schema 强制每维带 `evidence: {span_id, start, end, quote}`，缺证据整次判失败不落分。U0 用 `scripts/compare_models.py` 的方法对 ≥3 个境内模型跑同一批样本，产出四项指标 + 延迟 + 成本，结论落 `docs/m2-model-comparison.md`，正式模型标识以对比文档为准。

**为什么**：召回段解决"LLM 逐份打分太贵太慢"；精排段解决"向量相似度不可解释"。evidence 强制在输出 schema 而不是事后校验——事后校验只能丢结果，schema 强制能让模型第一次就给出位置。

**向量存储**（本包决定，不留 Open Question）：在 SQLite 上用进程内实现——向量以 BLOB 存 `resume_embedding` 表，召回时全量读入 numpy 算 cosine。单岗投递量 ≤ 低三位数、维度 1024，全量算是毫秒级。⛔ 不引入 pgvector / FAISS——那会把"换 Postgres"提前绑进本包。

**BGE-M3 运行位置**（✅ 已裁决 2026-09-17，原 Q1）：**`.51` 本地 CPU 推理**，简历全文不离机。U0 只实测本地 CPU 的装包体积与召回耗时，⛔ 不再比选境内托管 API；实测过慢的处置是离线批跑＋登记技术债，切托管 API 须重新裁决（不可代：真实简历处理范围变更）。

### 决策 D8：沿用 M1 Web，四个新页面，无前端框架（对应 intent D8）

**做法**：`app/web/static/` 新增 4 个页面（候选人列表／字段校对／复核／批量确认），FastAPI 路由挂在 `root_path` 下，接口与资源一律相对路径（部署约束 1）。字段校对页的原文高亮用 `resume_text_span` 的偏移直接切字符串。

**为什么**：M1 的 Web 已在 `.51` 门户挂载并验证过路径前缀；企微卡片承载不了"看原文＋逐字段校对"这种密度的交互，D8 明确不做。

### 决策 D9：四项指标的算法口径在本包锁死（对应 intent D9）

**做法**：
- 字段准确率：逐字段 exact-match 经归一化（去空白、全半角、年限取整、公司名去"有限公司"后缀）；技能列表按集合 Jaccard ≥0.8 算对；总体 = 六字段平均
- Spearman：系统总分排名 vs 人工排序名次，同岗位内计算，样本 <10 不出结论
- Top-10 召回：人工前 10 中出现在系统前 10 的比例（样本 <10 时按 Top-⌈n/2⌉）
- span 可回溯率：评分项中回指能解析到存在分片且偏移不越界的比例，门槛 100%

**为什么**：指标口径不锁死，"过没过"就会变成解释权之争。归一化规则写死是为了让"上海某某科技有限公司" vs "上海某某科技"不算错——那不是解析错误。

### 决策 D10：试运行岗位由 Shao Peishen 从三岗中选（对应 intent D10）

**做法**：tasks §7 留一条 🔴 不可代项；代码层面岗位是参数，不写死。选定后该岗位的冻结画像版本作为 U6 评测集与 U7 试运行的 `job_id`。

**为什么**：哪个岗位"仍在招"是业务事实，只有本人知道。

### 决策 D11：ATS 域数据模型按 §2.1 落，`application` 与 `candidate` 分开

**做法**：新表 `candidate`（全局唯一，无状态）、`resume`（一人多份，含 `parsed_json` / `parse_confidence` / `parser_version` / `sample_class`）、`resume_text_span`（分片 + offset）、`application`（`candidate_id + job_id + current_stage_id + status`）、`stage`（全局池 + `stage_type` 语义标签，M2 预置 `initial / screening / rejected`）、`application_stage_history`（`actor_type ∈ {human, agent}`）、`rejection_record`（`reason_type` CHECK IN `('hard_rule','human_decision')`，`rule_ref`，`appeal_status`）、`resume_access_log`、`field_review_queue`、`screening_flag`、`resume_embedding`、`eval_sample` / `eval_annotation` / `eval_import_batch`。全部 `CREATE TABLE IF NOT EXISTS`，⛔ 不进 `_ADDED_COLUMNS`（新表不需要加列路径）。

**候选人去重**：`candidate` 按（姓名 + 手机号哈希）唯一；手机号本期只用于去重，以哈希存储，明文不落库（✅ 已裁决 2026-09-17，原 Q6；工作台看联系方式约面试属 M3，届时须纳入 PIA 范围再议）。

**为什么**：状态挂在投递不挂在人（Horilla 的坑，CLAUDE.md 数据模型要点）；`rejection_record` 的 CHECK 是红线的存储层落点；手机号哈希是最小必要原则。

### 决策 D12：鉴权从空壳换成本地账号，签名不变

**做法**：`AuthMiddleware.dispatch` 内部改为读会话 cookie → 查 `hr_account` 表（用户名 + 盐哈希口令，账号由脚本创建，每人一个）；`AuthContext` 与 `reviewer_of()` 签名不变。未登录访问 `/candidates*` `/resumes*` 一律 401。企微 OAuth SSO 仍是将来只换 dispatch 内部。

**为什么**：部署约束 5 说"共享口令不满足"，说的是可识别到人；本地账号每人一个即满足，且不引入企微 OAuth 的公网回调依赖（那是 `hr-wecom-aibot-liaison` 之外的另一条线）。

### 决策 D13：LangGraph 子图形态与幂等键

**做法**：每份投递一个 thread（`thread_id = application_id`）。节点序列：`compute_parse` → `effect_persist_parse` → `compute_screen` → `effect_persist_flags` → `compute_recall_rank`（召回+精排，纯计算）→ `effect_persist_scores` → `interrupt()` 等复核 → `effect_apply_batch_decision`。幂等键 `{application_id}:{node_name}:{parser_version|profile_version|analysis_run_id|batch_id}`。批量确认是跨 thread 的一次人工动作：先落 `human_review`（`batch_id`）再逐 thread 唤醒各自的 `effect_apply_batch_decision`，每个节点自己按 `batch_id` 幂等。

**为什么**：工程铁律 1、2 的直接落地。批量确认跨 thread 是 M2 与 M1 最大的形态差异——M1 一岗一 thread，人工动作也在同一 thread；M2 一次确认涉及 N 个 thread，幂等必须按 `batch_id` 而不是按 thread。

### 决策 D14：扫描件识别引擎 = PaddleOCR（✅ 已裁决 2026-09-17，原 Q2）

**做法**：扫描型 PDF 走 PaddleOCR（Windows 可 pip 装、中文好）；文本型 PDF 直抽、Word 走 `python-docx`。U0 在 `.51` 同款 Windows venv 上做可装性冒烟与识别质量实测。

**为什么**：MinerU 版面更好但依赖重，在无 Docker 的 Windows 上可装性风险高；扫描件在本项目样本里是少数，PaddleOCR 够用。

**退路**：U0 冒烟装不上或识别质量不可用 ⇒ 一期扫描件进"不可读"人工队列并登记技术债——这是实施退路，⛔ 不是换 MinerU（换引擎须重新裁决）。

### 决策 D15：bias 回归夹具另立包（✅ 已裁决 2026-09-17，原 Q5）

**做法**：§6 清单项"改造 `re-cinq/hiring-bias` 纳入 CI，含盲筛对照轴"移出本包，另立 OpenSpec 变更包；本包 tasks 8.7 留墓碑。

**为什么**：夹具需要真实分布样本才有意义，真实简历入库闸开启前做不了；绑进本包只会让 U7 悬空。

## Risks / Trade-offs

- [PaddleOCR 在 Windows 无 Docker 上装不上或体积过大] → U0 首日在 `.51` 同款 Windows 上做可装性冒烟；装不上则一期只收文本型 PDF/Word，扫描件进"不可读"人工队列，并登记技术债（D14 退路）
- [BGE-M3 本地 CPU 推理在 `.51` 上慢] → 召回段离线批跑（上传后一次算完存 `resume_embedding`），不在页面请求路径上；仍慢则登记技术债，⛔ 不自行切托管 API（D7 已裁决本地）
- [LLM 给的 evidence 偏移对不上原文] → 输出 schema 同时要 `quote`，落库前用 `quote` 在分片里反查校正偏移；反查失败判该次评分不可用。U0 把"span 可回溯率"列为模型对比指标之一
- [G1 附件链路迟迟不通，评测集回不来] → U6 的导入脚本同时支持从本地路径导入（Shao Peishen 拿到文件后手工放入），⛔ 不因链路未通阻塞 U6 代码
- [合规验收 #1 未过，只能用脱敏样本，与真实简历分布有偏] → 验收指标在脱敏集上算；试运行前在闸开启后用真实样本复算一次并落档
- [HR 校对后重判、画像升版后重判，三处触发点导致状态机复杂] → 重判统一走"新建 analysis 版本"路径，旧结果不覆盖；状态机用测试穷举三种触发
- [`rejection_record` 缺表放行改为判失败，会让 M1 分支上的 CI 红] → 本包 U1 建表与 U7 改断言在同一交付单元合并，中间不跨 session
- [批量确认跨 thread 的幂等只按 `batch_id`，重复提交同一批次但勾选集合不同] → `batch_id` 由服务端按勾选集合哈希生成，集合不同即不同批次

## Migration Plan

1. U1 新表全部 `CREATE TABLE IF NOT EXISTS`，`.51` 上 `demo.db` 既有表一行不改，无数据迁移
2. `hr_account` 表由 `scripts/create_hr_account.py` 建账号；上线前为每位 HR 建一个
3. 新依赖先在 Windows 冒烟（U0），`requirements.txt` 变更与 `sync-to-server.sh` 白名单同步
4. 发版顺序：U1–U5 合并后可先发一次（闸关、只收脱敏样本，供 HR 熟悉工作台）；U7 合并且合规验收 #1 通过后才由 Shao Peishen 决定开闸
5. 回滚：开关关回即停收真实简历；新表不删（含留痕），代码回退到上一版

## Open Questions

> 2026-09-17 13:3x Shao Peishen 对 `docs/roadmap/M2-intent.md`「立包后裁决」六题答 `1a，2a，3是，4b，5是，6是`（六题对应本节 Q1／Q2／Q4／Q5／Q6／Q7；Q3 未提问，按推荐起步）。已裁决条目已回填至 Decisions 相应位置，本节只留裁决记录。**当前待裁决条数：0。**

- **Q1 BGE-M3 运行位置**：✅ 已裁决 2026-09-17：(a) `.51` 本地 CPU 推理，简历全文不离机 → D7
- **Q2 扫描件识别引擎**：✅ 已裁决 2026-09-17：(a) PaddleOCR；U0 冒烟装不上则退到"不可读"队列，⛔ 不换 MinerU → D14
- **Q3 置信度阈值默认值**：未向 Shao Peishen 提问（属实施参数，可代）。按推荐 **0.7 起步**，U0 实测后定终值，spec 只要求"岗位级可配" → D5
- **Q4 申诉登记人**：✅ 已裁决 2026-09-17：是——HR 代候选人登记，本期不开候选人自助入口 → Non-Goals、`hard-requirement-screening` spec
- **Q5 bias 回归夹具**：✅ 已裁决 2026-09-17：(b) 另立包 → D15、tasks 8.7 墓碑、proposal「不做」
- **Q6 手机号处理**：✅ 已裁决 2026-09-17：是——只用于去重、哈希存储、明文不落库；M3 再议明文 → D11
- **Q7 评测集归档件留存期**：✅ 已裁决 2026-09-17：是——导入完成且指标落档后 90 天删除，与合规验收 #1 留存策略对齐后以后者为准 → D3
