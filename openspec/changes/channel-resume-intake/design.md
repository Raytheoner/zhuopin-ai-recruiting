## Context

动机见 `proposal.md`「Why」；需求收敛结论见同目录 `intent.md`（D1–D10，G1 Q-36 放行）。本节只写约束现状。

**现有地基（M2，本包直接叠加）**

- 上传入口：`POST /resumes/upload` 多文件、`job_id`、`sample_class`、类型白名单 pdf/docx、内容 SHA-256＋`job_id` 幂等（M2 tasks 3.3）；真实简历入库闸 `is_live_resume_intake_enabled()` 默认关、每次求值（M2 D2）
- 解析管线：文件 → 文本（文本型直抽／Word／扫描件 PaddleOCR，`.51` 装不上则"不可读"队列）→ 六字段抽取（M2 D4、D14）；六字段**不含手机号**——手机号只在去重键上用（M2 D11：`candidate` 按姓名＋手机号哈希唯一）
- `candidate.source` 列已在 `02` §2.1 数据模型里，M2 U1 建表时带；`resume` 一人多份
- `application_stage_history` 是所有报表基础；`actor_type ∈ {human, agent}`
- 值守通道红线 ⑨；私信附件链路生产恒空
- `.51` Windows、无 Docker；`zipfile` 标准库可用
- 规模：年招几十到百人，单次导出包几十份

**外部依赖现状**

- 人事部#3 在途：渠道清单与占比、各平台脱敏导出样例、招聘邮箱现状、重复口径
- 合规验收 #1 入库闸未开；本包开发期只用 `synthetic/anonymized/departed` 样本

## Goals / Non-Goals

**Goals:**

- HR 一次传一个 ZIP，系统拆包、标来源、按手机号哈希挂人，全部走 M2 既有路径，不复制第二套上传逻辑
- 去重决定在结构上"机器只挂确定的、人合不确定的"，且合并可撤销
- 来源进流转事实，报表随时能算

**Non-Goals:**

- 不做爬取、ATS、邮箱收件、寻源发布
- 不做漏斗报表页
- 不为渠道另建 OCR
- 不改 M2 的手机号哈希口径

## Decisions

### 决策 D1：ZIP 解包是 M2 上传接口的一个分支，不是第二个接口（对应 intent D8）

**做法**：`POST /resumes/upload` 遇 `.zip` ⇒ `unpack_bundle(bytes) -> list[FileEntry]`（纯函数，标准库 `zipfile`，一层子目录，上限 200 文件／200 MB，可配）⇒ 逐文件调既有单文件接收函数；`effect_ingest_bundle` 幂等键 `{job_id}:effect_ingest_bundle:{bundle_sha256}`，包内逐文件仍按 M2 的文件哈希幂等。`live` 类别在展开前先求值入库闸。

**为什么**：M2 的闸、留痕、去重、白名单都在单文件路径上，复制一份等于把闸也复制一份——两份闸迟早不一致。

**替代方案**：独立的"渠道导入"页面与接口。否决——同上。

### 决策 D2：来源识别是确定性纯函数，不用模型（对应 intent D8、合规）

**做法**：`detect_source(filename, first_page_text) -> Source | None`，规则表（文件名正则＋首页水印／页眉关键词）随人事部#3 的脱敏样例迭代，规则表进版本库带测试夹具；识别不出 ⇒ 上传参数 `default_source`（HR 为整包指定）⇒ 否则 `unknown`。`resume.source` 每份自带；`candidate.source` ＝ 最早一份简历的来源（视图或触发式重算）。改正走 `source_correction_log`。⛔ 不提取平台用户 ID 之类水印之外的字段。

**为什么**：来源识别的错误代价是"报表少一条"，用 LLM 判会引入不可解释的错误且多一次留痕负担；确定性规则可测、可改。

### 决策 D3：手机号哈希从解析文本里取，⛔ 不新增明文存储（对应 intent D5）

**做法**：在 M2 `compute_parse` 之后加一个纯函数 `extract_phone_hash(text_spans) -> str | None`（正则找手机号 → 立即哈希 → 明文不出函数）；`effect_attach_resume_to_candidate` 按（姓名规范化＋哈希）查 `candidate`，命中挂接、不命中新建；幂等键 `{resume_id}:effect_attach_resume_to_candidate`。M2 U1 的 `candidate` 唯一键不改。

**为什么**：M2 Q6 裁决"手机号只用于去重、哈希存储、明文不落库"；本包只是把哈希的来源从"HR 手填"变成"从简历文本抽"，口径不变。

### 决策 D4：疑似重复只提示，合并由人做且可撤销（对应 intent D5）

**做法**：`detect_suspected_duplicates(candidate, existing_same_job) -> list[CandidateRef]`（纯函数：姓名规范化——去空白、全半角——相同且同岗位）；列表页标记；`effect_merge_candidates(primary, secondary, reason)` 写 `candidate_merge_log`（含 secondary 快照 JSON），把 `resume` / `application` 的 `candidate_id` 改到 primary，同岗位双投递时要求 HR 选保留哪份，另一份写 `application_stage_history(action=closed_by_merge)` 不删；`effect_unmerge_candidates` 按快照恢复。幂等键 `{primary}:{secondary}:effect_merge_candidates:{request_id}` / `{merge_log_id}:effect_unmerge_candidates`。⛔ 不用 LLM 判同一人。

**为什么**：合并是对个人信息记录的不可逆变更，必须有人签字、有快照、能回退。同名不同人（工程师里"张伟"很多）自动合并的代价是把两个人的评分混在一起。

### 决策 D5：来源进 `application_stage_history` 初始记录，改正追加不改写（对应 intent D10）

**做法**：M2 创建投递写初始流转事实时带 `source` 字段（history 表加列走 `_ADDED_COLUMNS`，可空）；来源改正 ⇒ 追加一条 `action=source_corrected` 记录。⛔ 不建报表页。

**为什么**：流转事实表是只追加的事实表，改写会破坏"所有报表的基础"这个前提；漏斗报表按 Q3a 后置。

### 决策 D6：扫描型导出件走 M2 退路，本包不建 OCR（对应 intent D6）

**做法**：解包后逐文件交给 M2 文件→文本路径，判定"不可读"的进 M2 的人工队列；本包在上传结果里按平台统计"不可读比例"，作为技术探针数据落 `docs/m2-model-comparison.md`「环境」节。

**为什么**：M2 D14 已裁决 PaddleOCR 与退路；渠道场景另建 OCR 等于绕过那次裁决。

### 决策 D7：交付单元与顺序

| 顺序 | 单元（tasks 章） | 内容 | 前置 |
|---|---|---|---|
| 1 | U1 解包与来源识别（第 1 章） | `unpack_bundle`、`detect_source`、上传接口 ZIP 分支、`resume.source`、改正留痕 | M2 U2 上传接口 |
| 2 | U2 去重合并（第 2 章） | `extract_phone_hash`、挂接节点、疑似重复、合并／撤销节点与页面 | U1；M2 U1 `candidate` |
| 3 | U3 来源进流转事实（第 3 章） | history 加列、初始记录带来源、改正追加、断言 | U1；M2 投递创建路径 |
| 4 | U4 交付与发版（第 4 章） | 规则表随样例迭代、操作说明、探针数据、发版（🔴 G3） | U1–U3；人事部#3 样例 |

## Risks / Trade-offs

- [各平台导出格式无样例，识别规则空转] → U1 规则表先按公开可见的文件名模式起步（占位），识别不出全部落 `default_source`；样例到后迭代规则并补夹具
- [Boss直聘／猎聘导出为图片型，文本抽不出 ⇒ 手机号哈希取不到 ⇒ 去重退化为疑似重复] → 这正是 D6 的已知代价；探针数据量化不可读比例，若过高再议是否重开 OCR 裁决（不在本包）
- [ZIP 炸弹／超大包] → 展开前检查压缩比与声明大小，上限可配，超限整包拒收
- [合并后又撤销，中间产生的评分／流转归属混乱] → 合并期间对 primary 新增的记录在撤销时保留在 primary；快照只恢复 secondary 原有归属；测试覆盖"合并→新增→撤销"
- [同一 ZIP 反复上传触发大量"重复"结果] → 包级幂等（bundle 哈希）直接返回上次结果，不逐文件重跑
- [`application_stage_history` 加列影响 M2 报表查询] → 可空列，既有查询不受影响；测试固定 M2 既有断言绿

## Migration Plan

1. `resume.source`、`application_stage_history.source` 走 `_ADDED_COLUMNS`（可空，默认 NULL，既有行视为 `unknown`）；新表 `CREATE TABLE IF NOT EXISTS`
2. 上传接口向后兼容：不传 ZIP、不传 `default_source` 时行为与 M2 完全一致（测试固定）
3. 发版：U1–U3 合并后发一次；入库闸状态不变；🔴 G3
4. 回滚：代码回退；加列不删；合并留痕保留

## Open Questions

> 以下为 `intent.md`「待专员」表与「外部依赖」小节里尚未闭环的项，原样转入；⛔ 不阻塞 U1–U3 代码，只影响识别规则真值、去重口径与真实包入库时点。**当前条数：7。**

- **OQ1 现用渠道清单与年简历量占比**（待专员）：影响 U1 规则表优先级；判据＝近 1 年各渠道数量到位
- **OQ2 各平台导出格式样例（是否图片型、水印、字段版式）**（待专员）：影响 U1 识别规则与夹具、D6 退路命中比例；判据＝每平台 1–2 份脱敏导出件到位（⛔ 不经值守通道）
- **OQ3 招聘邮箱现状：有没有专用邮箱、谁收、日均量**（待专员）：本包不做邮箱收件（Q1a），只影响将来是否另立包；判据＝回件说明
- **OQ4 重复简历现状：多平台投递频率、现在怎么发现（判「重复」的口径）**（待专员）：影响 U2 疑似重复规则是否加"同公司＋同学校"类条件；判据＝近 1 个月 ≤10 例（脱敏）到位
- **OQ5 合规验收 #1 入库闸开启**（外部依赖，🔴 不可代）：真实导出包入库前置；判据＝M2 tasks 8.8 勾选
- **OQ6 各平台导出 PDF 文本层可读率抽样**（技术探针，随 M2 U2 顺带）：决定 D6 退路命中比例；判据＝`docs/m2-model-comparison.md`「环境」节有按平台的可读率数据
- **OQ7 邮箱凭据／ATS 采购**（外部依赖，🔴 不可代）：本包不需要（Q1a）；仅当将来 Q1b／Q1c 重提时另立包；本包留墓碑
