## Why

HR 今天筛简历靠 Excel＋微信／邮箱人肉翻阅：候选人信息散落在个人微信里（资产流失），每份简历读一遍、硬条件靠记忆核对、排序全凭印象，且没有任何"为什么把这个人排在前面"的可追溯记录。M1 已经把岗位画像与硬门槛规则草案结构化并冻结，M2 把它们接到简历侧——**这一期是唯一的差异化护城河，也是首次触碰候选人个人信息**。

为什么是现在：M1 的编排骨架、LLM 网关、审计留痕（`analysis_run` / `criterion_score` / `human_review`）、外发门禁、合规断言 CI 已经就位，M2 不再需要造地基；三个试点岗位的冻结画像已经存在可以直接消费；评测集所需的通信链路（跟进信线发、私信附件回件归档 `0917W`）刚打通。

本包的需求收敛结论见同目录 `intent.md`（D1–D10，Shao Peishen 2026-09-17 全部取推荐项），design.md 的 Decisions 逐条对应。

## What Changes

- 新增 **HR 手工批量上传** PDF/Word 简历的 Web 入口，带**真实简历入库闸**：开关默认关，关闭时只收脱敏／历史离职样本，真实在招简历一律拒收（D1、D2）
- 新增 **简历解析管线**：版面解析 → LLM schema 约束抽取首期六字段（姓名／工作年限／技能／公司／教育／期望城市），每个字段带**置信度与原文 span**；低置信度字段进人工校对队列且不参与硬门槛判定（D4、D5）
- 新增 **硬门槛引擎**：消费 M1 冻结画像的 `hard_requirement` 规则，对每份投递逐条判定，只产出"不符合＋原文依据"的**标记**，⛔ 不淘汰；淘汰一律由 HR 在工作台批量确认并留痕，开放申诉（D6）
- 新增 **三段式匹配**：BGE-M3 召回 → LLM rubric 精排，每条 `criterion_score` 回指 evidence span；模型对比实测定型是开工第一个单元（D7）
- 新增 **HR 复核工作台**四个页面：候选人列表／字段校对／复核／批量确认，沿用 M1 Web（D8）
- 新增 **评测集导入与指标脚本**：200 份私有评测集按「判例批改表」分批标注（每批 ≤20）、导入格式与校验、四项验收指标可一键计算（D3、D9）
- 新增 **ATS 域数据模型**：`candidate` / `resume` / `application` / `stage` / `application_stage_history` / `rejection_record` / 简历访问留痕，并接通既有评分审计域
- 新增 **M2 合规断言**：`rejection_record.reason_type='ai_score'` 恒 0（建表后表缺失即判失败）、评分 100% 可回溯 span、真实简历闸默认关、简历访问留痕不可缺
- 新增 **可识别到人的登录＋简历访问留痕**——部署约束 5 的 M2 门槛：真实简历入库闸开启的前置条件之一

## Capabilities

### New Capabilities

- `resume-upload-and-gate`：HR 手工批量上传简历文件；真实简历入库闸（默认关、每次求值、开启需人工决定）；上传去重与样本类别标注；简历访问留痕（谁在什么时候看了谁的简历）
- `resume-parsing`：文件 → 结构化字段抽取；字段级置信度与原文 span；低置信度进人工校对队列；解析器版本与留痕
- `hard-requirement-screening`：按冻结画像的硬门槛规则逐条判定，只标记不淘汰；每条标记带原文依据；低置信度字段跳过判定；申诉通道可流转
- `candidate-ranking`：BGE-M3 召回 → LLM rubric 精排；每条评分带 evidence span；排序只做推荐；模型对比定型的输入输出契约
- `review-workbench`：候选人列表／字段校对／复核／批量确认四页；批量确认淘汰的人工决策留痕（`human_review.batch_id`）；工作台上所有 AI 产出带标识
- `eval-set-and-metrics`：评测集样本合规类别；「判例批改表」标注格式与导入校验；四项验收指标（字段准确率、Spearman、Top-10 召回、span 可回溯率）的计算与门槛
- `m2-compliance-assertions`：M2 新增的可自动执行合规断言（含"表缺失即失败"升级）、真实简历闸与访问留痕的断言、CI 接入

### Modified Capabilities

（无。`ai-decision-audit` 的三条既有断言与 `evidence_ref` 约束原样沿用，M2 只**新增**断言，不改既有需求；`effect-transaction-integrity` 与 `outbound-approval-gate` 原样约束本包的 `effect_*` 节点与任何对候选人的外发。）

## Non-goals（不做什么）

- **不接招聘渠道**（Boss直聘／猎聘／智联 API）、**不做简历爬取**——一期只做 HR 手工导出后批量上传
- **不经值守通道（企微机器人私信）收简历**——值守归档保持不含候选人个人信息，章程红线 ⑨ 不变
- **不做企微卡片交互**——工作台只在 Web
- **不做表情／情绪／人脸分析**——合规红线
- **不做 AI 自动淘汰**——AI 只产出标记与排序，淘汰必须经 HR 批量确认
- **不做 ECU 特化字段抽取**（§2.4 `property_definition` / `property_value`）——第二期
- **不做对候选人的任何外发**（拒信／邀约）——外发走既有 `outbound-approval-gate`，本包不新增外发动作
- **不做面试相关功能**——M3
- **不做人才库检索／二次推荐**——本包只建表不建检索页
- **不做 bias 回归夹具**（`re-cinq/hiring-bias` 改造、盲筛对照轴）——另立包（Shao Peishen 2026-09-17 裁决，真实简历入库闸开启前做不了）

## 合规影响说明

**本变更首次处理候选人个人信息**（姓名、联系方式、教育与工作经历），属 PIPL 意义上的个人信息处理，且 AI 评分构成"利用个人信息进行自动化决策"的组成环节（PIPL 第 24 条）。约束如下：

- **两阶段数据范围**：闸关闭期（开发与评测）只处理脱敏或历史离职候选人样本；真实在招简历入库须先通过「合规验收 #1」（PIA 报告存档 ＋ 候选人同意条款单列 AI 评估 ＋ 留存与删除策略）并由 Shao Peishen 亲自开启入库闸。闸的开启是**不可代项**。
- **可识别到人 ＋ 访问留痕**（部署约束 5）：闸开启的前置条件是登录可识别到具体 HR，且每次查看简历原文／解析结果写访问留痕。共享口令不满足。
- **AI 只排序不淘汰**：`rejection_record.reason_type` 取值只有 `hard_rule` 与 `human_decision`；断言 `count(reason_type='ai_score')=0` 进 CI，且本包起 `rejection_record` 表缺失即判失败。硬门槛淘汰也必须经人工批量确认——引擎只标记。
- **说明权与申诉**：每条评分与每条硬门槛标记都回指原文 span；`rejection_record.appeal_status` 可流转；淘汰记录指向具体规则以便向候选人解释。
- **不用历史录用结果做监督信号**：评测集的"人工排序"只用于离线指标计算，⛔ 不进任何训练／微调／prompt 自动优化；rubric 只来自显式岗位画像。
- **AI 生成内容标识**：工作台上所有 AI 产出（解析字段、标记、评分、摘要）带标识，与 M1 JD 标识同一口径。
- **模型境内、数据不出境**：沿用 M1 LLM 网关的供应商白名单；BGE-M3 在 `.51` 本地 CPU 推理，简历全文不离机（design D7，2026-09-17 裁决）。
- **评测集标注的个人信息**：判例批改表经跟进信线发、私信附件回件归档，归档件含候选人个人信息（脱敏或离职样本）——归档目录的访问控制与留存期由本包 `eval-set-and-metrics` 明确。

## Impact

**新增代码**
- ATS 域表：`candidate` / `resume` / `resume_text_span` / `application` / `stage` / `application_stage_history` / `rejection_record` / `resume_access_log` / `field_review_queue` / 评测集表
- 解析 Agent（纯函数）、硬门槛引擎（纯函数，消费 M1 `hard_requirement` 表）、匹配 Agent（召回 + 精排，纯函数）
- LangGraph 子图：`compute_parse` → `effect_persist_parse` → `compute_screen` → `effect_persist_flags` → `compute_rank` → `effect_persist_scores` → `interrupt()` → `effect_apply_batch_decision`，全部 `effect_*` 节点带幂等键并与业务写同事务
- Web：上传页、候选人列表、字段校对、复核、批量确认；鉴权中间件从空壳换成可识别到人的实现
- 评测脚本：`scripts/eval_m2.py`（导入判例批改表 → 算四项指标 → 出报告）；模型对比脚本复用 `scripts/compare_models.py` 方法
- 合规断言：`app/audit/assertions.py` 新增 M2 四条

**新增依赖**（引擎已裁决 2026-09-17；具体包名与版本由 U0 在 Windows 上冒烟后锁定）
- 版面解析：PaddleOCR（design D14；Windows 无 Docker 环境可装性是硬约束，装不上则扫描件进"不可读"队列）
- Word 解析：`python-docx`
- 向量：BGE-M3 `.51` 本地 CPU 推理（design D7）；向量索引在现有 SQLite 上用进程内 numpy 实现，⛔ 不引入 pgvector / FAISS

**外部依赖／人**
- 汤丽萍牵头 200 份评测集标注（分批 ≤20 份，经跟进信线发、私信附件回件）——依赖 G1 附件链路真实帧确认（TD-51）
- 合规验收 #1：PIA 与法务结论（不可代）
- 试运行岗位从 M1 试点三岗中选一个仍在招的（Shao Peishen 定）

**既有代码触碰**
- `app/storage/db.py`：新表走 `CREATE TABLE IF NOT EXISTS`，`analysis_run.application_id` 开始有真实写入方
- `app/audit/assertions.py`：`REJECTION_TABLE` 缺表分支从"M1 现状放行"改为"判失败"
- `app/middleware/auth.py`：空壳换实现，`reviewer_of()` 签名不变
- `.51` 发版：新增依赖需在 Windows venv 上验证可装（不可代项：发版决定）
