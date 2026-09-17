## Why

面试是招聘流程里最贵、最依赖面试官个人经验、也最不可追溯的环节：业务经理每场花 40–60 分钟，问什么全凭当场发挥，同一岗位不同候选人拿到的题目难度不一致，面完只留一张主观印象分。M1 已冻结岗位画像与 rubric，M2 正在给每份简历产出带 evidence 的弱点清单——把这两样接到面试侧，就能在**人工终面之前**用一场结构化的 AI 语音面试把"简历上看不出来的能力"问出来，并给面试官一份逐条回指原话的 ScoreCard 与要点提示。

为什么是现在：Shao Peishen 2026-09-17 改答 Q-F4「是」，按任务驱动口径 M3 不再等 M2 上线两周；题库首版用画像＋脱敏样本起步、M2 试运行数据到位后再校准（X6 改口径）；X5 技术探针不依赖 M2 数据现在就能做；`.51` 的 VC++ 运行库已升级（R-9 ✅ `0917BB`），torch 可导入，语音组件的可装性探针有了前提。需求收敛结论见同目录 `intent.md`（D1–D16，D12–D16 为 Shao Peishen 2026-09-17 23:0x 逐题答复），design.md 的 Decisions 逐条对应。

## What Changes

- 新增 **prep 出题引擎**：以冻结画像＋M2 简历弱点为输入，LLM 生成题目、难度曲线、逐题 rubric 与预埋追问；业务经理在 Web 逐题确认后冻结为 `prep_snapshot`，**未确认不得开场**（D16）
- 新增 **邀约与同意流程**：一次性邀请链接经既有外发门禁 `deliver_candidate_message()` 送达（类型 `interview_invitation`，D6）；候选人打开链接后分别对「AI 面试」与「身份核验」各自单独同意并留痕（D5）；一期身份核验只做**手机号验证码弱核验**，不做活体／证件比对，`identity_check.result` 一律 `skipped`（D13）
- 新增 **live 语音链路**：LiveKit 房间 ＋ 自托管 FunASR（ASR）↔ 轻模型追问选择 ↔ 自托管 CosyVoice（TTS）级联（D1、D15）；打断处理；全程录制；候选人网络差时切**文本作答**降级通道；live 段跑在**另立的境内主机**上，`.51` 只与之交换 prep 快照与转写，简历数据不出 `.51`（D12）
- 新增 **post 转写对齐与 ScoreCard**：转写按 `interview_turn` 对齐 → rubric 逐维评分，每条 `criterion_score.evidence_ref` 回指具体 `interview_turn` 的偏移（空不允许写入）→ ScoreCard ＋「面试要点提示」，结论只进人工终面（D3、D4）
- 新增 **面试官 Web 视图**：ScoreCard、要点提示、逐 turn 原话回放与转写、声学参考信号（语速／停顿／静默，**只展示不计分**，D2、D9）；HR 看场次完成率与进度
- 新增 **面试域数据模型**：`interview_session`／`interview_turn`／`identity_check` 按 `02` §2.3 落表；ScoreCard 复用 `analysis_run`（`run_type=interview`）＋`criterion_score`（D4）
- 新增 **录音留存策略**：`retention_until` 到期自动删除并留痕；删除动作幂等、可断言
- 新增 **M3 合规断言**：`evidence_ref` 100% 指向存在的 `interview_turn`、`rejection_record.reason_type='ai_score'` 恒 0 延用到面试评分、`identity_check` 不含任何进评分的列、录音到期删除率 100%、库内不存在任何人脸图像列
- 新增 **X5 技术探针**（U0）：LiveKit／FunASR／CosyVoice／livekit-agents SDK 可装性与延迟基线、DeepSeek TTFT 实测，结论落 `docs/m3-voice-probe.md`；探针不过则 live 段延后，prep／post 段照做（D15）

## Capabilities

### New Capabilities

- `interview-prep-question-engine`：prep 段——按画像＋简历弱点生成题目／难度曲线／rubric／预埋追问；业务经理逐题确认后冻结 `prep_snapshot`；未冻结不得开场；生成留痕与版本
- `interview-invite-and-consent`：一次性邀请链接（签发、过期、单次使用）；经门禁送达；AI 面试与身份核验各自单独同意留痕；手机号验证码弱核验；`identity_check` 与评分链路物理隔离
- `live-voice-interview-session`：live 段——房间建立、ASR／追问选择／TTS 回路、打断处理、全程录制、文本作答降级、延迟观测；简历数据不进语音主机；异常中断可续
- `interview-scorecard`：post 段——转写按 turn 对齐、rubric 逐维评分带 `interview_turn` 回指、ScoreCard 与要点提示、声学参考信号只展示不计分、面试官视图、HR 进度视图
- `interview-recording-retention`：录音与转写的留存期限、到期删除、访问留痕、删除幂等与可断言
- `m3-compliance-assertions`：M3 新增的可自动执行合规断言与 CI 接入；合规验收 #2 的机器判据

### Modified Capabilities

（无。`outbound-approval-gate` 已覆盖 `interview_invitation` 类型，本包只做调用方，不改门禁需求；`ai-decision-audit` 的留痕与 `evidence_ref` 约束原样沿用，本包只把 `evidence_ref` 的指向扩到 `interview_turn`——这是既有 spec 已预留的取值（`{type: resume|interview_turn}`），不构成需求变更；`effect-transaction-integrity` 原样约束本包全部 `effect_*` 节点。）

## Non-goals（不做什么）

以下逐条对应 `intent.md`「不做」小节：

- **不做表情／情绪分析**——合规红线；本包不采集视频、不做任何面部处理
- **不让声学情绪信号进评分**——语速／停顿／静默只在面试官视图作参考展示，⛔ 不进 `criterion_score`、不影响排序
- **不做 AI 自动淘汰**——post 段只产 ScoreCard 与要点提示，结论进人工终面；`rejection_record.reason_type='ai_score'` 恒 0
- **不走端到端 speech-to-speech**——级联架构已决策（`01` §2.4），可观测、可替换、每段延迟可单测
- **不存储人脸图像**——`identity_check` 无图像列；一期连活体／证件比对都不做（D13）
- **不做企微卡片**——面试官与 HR 只在 Web 工作台；aibot 被动形态不能主动私信
- **不做候选人自助改期**——属 `interview-scheduling` 包（S-排期）
- **不做候选人公开入口**——一律一次性邀请链接，避免被认定"向境内公众提供"
- **不用境内云 ASR／TTS API**——只允许自托管（D15）；探针不过则 live 段延后，⛔ 不切云 API
- **不在本包里采购主机／短信通道**——预算与采购是不可代项，本包只登记需求与规格

## 合规影响说明

**本变更是本项目首次处理候选人语音（录音）与手机号验证码，且 AI 面试评分构成自动化决策的组成环节（PIPL 第 24 条）。** 录音属个人信息，声纹可能被认定为生物识别信息（敏感个人信息）——本包不提取声纹、不做说话人识别，但录音的留存与删除口径须由法务在合规验收 #2 复核。约束如下：

- **两阶段对象范围**（D14）：先用员工扮演候选人的**内部模拟**跑通全链路与 3 名面试官一致性评估；真实候选人开闸是**不可代项**，前置＝合规验收 #2 通过 ＋ `CANDIDATE_OUTBOUND_ENABLED` 开启（不可代）＋ Shao Peishen 亲自拍板
- **双同意单独留痕**（D5）：AI 面试同意与身份核验同意各自一条记录（`consent_at`／`consent_version`），条款文本版本化；候选人拒绝任一同意 ⇒ 不开场、留痕、由 HR 改约人工面试
- **身份核验隔离**（D13、`02` §2.3）：`identity_check` 与评分链路物理隔离——不产生任何进入 `criterion_score` 的信号；一期只做手机号验证码弱核验，`result` 一律 `skipped`，替考风险由人工终面兜底
- **AI 只评分不淘汰**（D3）：延用 M2 断言 `count(reason_type='ai_score')=0`；ScoreCard 页面带 AI 生成标识与"仅供参考，最终决定由面试官作出"
- **说明权**（D4）：每条评分回指 `interview_turn` 偏移，面试官与候选人（应请求时）都能对应到原话
- **录音留存**：`retention_until` 由合规验收 #2 的留存策略定终值（design 起步值见 D18），到期自动删除并留痕；语音主机上的录音在回传 `.51` 后即删，不留副本
- **数据不出 `.51`**（D12）：语音主机只拿到 `prep_snapshot` 的题目文本与追问策略，**不拿简历、不拿候选人姓名**；回传只有转写与录音；两机接口鉴权；`.51` 无公网，只允许 `.51` 主动出站拉取，语音主机不得入站连 `.51`
- **模型境内、版本锁定、留痕全量**（D7）：LLM 走既有网关；ASR／TTS 自托管；每次评分持久化模型标识（响应侧）＋prompt 版本＋rubric 快照＋原始响应
- **AI 生成内容标识**：邀约文案、题目、ScoreCard、要点提示全部带标识，与 M1 JD 同一口径
- **不用历史录用结果做监督信号**：题库校准只用 M2 试运行数据里的"简历看不出的能力维度"分布，⛔ 不用录用与否做标签

## Impact

**新增代码**
- 面试域表：`interview_session`／`interview_turn`／`identity_check`／`interview_consent`／`interview_recording`／`prep_question`（`prep_snapshot` 的可查询展开）
- prep Agent（纯函数）、追问选择 Agent（纯函数，live 段轻模型）、转写对齐与 rubric 评分 Agent（纯函数）
- LangGraph 子图：prep（`compute_prep` → `interrupt()` 业务经理确认 → `effect_freeze_prep`）、invite（`effect_issue_invite` → `effect_deliver_invitation`（经门禁））、live（`effect_open_session` → `effect_persist_turn`×N → `effect_close_session`）、post（`compute_align` → `compute_score` → `effect_persist_scorecard`），全部 `effect_*` 节点带幂等键并与业务写同事务
- 语音主机侧 agent 进程（livekit-agents）：只依赖 prep 快照与追问策略，无 `.51` 库访问
- Web：题目确认页、候选人答题端（同意页／验证码页／语音房间／文本降级）、面试官 ScoreCard 页、HR 场次进度页；接口与资源一律相对路径（部署约束 1）
- 合规断言：`app/audit/assertions.py` 新增 M3 五条
- 探针脚本：`scripts/probe_m3_voice.py`（P1–P5），结论落 `docs/m3-voice-probe.md`

**新增依赖**（U0 冒烟后锁定版本）
- `livekit-server`（Go 二进制）＋ TURN（`coturn` 或 LiveKit 内置）——语音主机
- `livekit-agents` Python SDK（3.14 兼容性由 P5 定）
- FunASR（流式 ASR）、CosyVoice（TTS）——自托管；不可装则 live 段延后
- 前端 LiveKit 客户端 SDK（浏览器端，静态引入，无构建链）

**外部依赖／人**
- 🔴 境内语音主机（候选机或云主机）采购与预算——不可代
- 🔴 合规验收 #2：AI 面试同意条款、身份核验同意、录音留存期限与删除机制、人脸识别办法适用性结论——法务，不可代
- 🔴 短信验证码通道采购（若不采购则一期由 HR 人工转告验证码）——不可代
- 待专员（`人事部#3` 闭环后成信）：现用面试流程与打分表、各岗现用面试题、3 名面试官人选与时间、面试间设备网络现状
- X6：M2 试运行数据校准题库（非阻塞）

**既有代码触碰**
- `app/storage/db.py`：新表走 `CREATE TABLE IF NOT EXISTS`；`analysis_run.run_type` 开始出现 `interview` 值
- `app/audit/assertions.py`：`evidence_ref` 断言扩展到 `interview_turn` 类型
- `app/outbound/`：新增 `interview_invitation` 的调用方（门禁本身不改）
- `.51` 发版：新增依赖需在 Windows venv 上验证可装（🔴 发版决定不可代）
