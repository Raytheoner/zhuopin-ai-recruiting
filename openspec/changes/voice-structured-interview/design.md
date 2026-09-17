## Context

动机见 `proposal.md`「Why」；需求收敛结论见同目录 `intent.md`（D1–D16）。本节只写约束现状。

**现有地基（M1／M2 已交付或已立包，本包直接消费）**

- 编排：LangGraph 图骨架，`compute_*` / `effect_*` 节点分离，checkpointer 是 **SqliteSaver**（M1 对齐现实后走 SQLite，见 `m1-job-profile-intake/tasks.md` 2026-08-20 说明）。幂等键 `{thread_id}:{node_name}:{business_key}` 落 `effect_log`，与业务写同事务（`effect-transaction-integrity` spec）
- LLM 网关：双供应商切换、版本锁定、`temperature=0`、`AuditHook` 留痕到 `analysis_run`（含 `response_model`）
- 审计域：`analysis_run` / `criterion_score`（`evidence_ref` 非空由 CHECK 强制，JSON 形态 `{type, id, start, end}`，`type` 已预留 `interview_turn`）/ `human_review`
- 外发门禁：`deliver_candidate_message()` 已覆盖 `interview_invitation`，fail-closed，`CANDIDATE_OUTBOUND_ENABLED` 默认关（TD-8 登记"有通道无调用方"——本包是第一个调用方）
- 岗位画像：M1 冻结画像含 rubric 维度白名单与 `hard_requirement`；业务经理 Web 确认→冻结的交互形态已有先例
- 简历评分：M2 `candidate-ranking` 产出逐维 `criterion_score` 带 resume span——本包 prep 段的"简历弱点"输入就是它
- 联系方式：M2 只存手机号哈希；`interview-scheduling` 包的 `candidate-contact-vault` 提供面试阶段的加密明文（开关默认关、不可代）——本包不另存手机号
- 鉴权：M2 U1 起 `hr_account` 本地账号可识别到人；面试官账号沿用同一表
- 部署：`.51` Windows Server 2019、Python 3.14.5、CPU、无 Docker、无公网、无反代；VC++ 已升级（R-9 ✅ `0917BB`），torch 可导入；`paddlepaddle` 无 cp314 wheel（与本包无关，但说明 3.14 上 wheel 缺失是常态）
- 出网：`.51` 到境内 LLM 三家域名全通

**外部依赖现状**

- 语音主机（D12）尚未采购；LiveKit／FunASR／CosyVoice 在目标环境的可装性与延迟基线未测（X5）
- 合规验收 #2 未启动：AI 面试同意条款、身份核验同意、录音留存期限、人脸识别办法适用性
- 短信验证码通道未采购
- `人事部#3` 在途，四条待专员项（面试流程／现用面试题／面试官人选／面试间现状）未回件
- M2 试运行数据（X6）未产生——题库首版不等它

## Goals / Non-Goals

**Goals:**

- 在"`.51` 无公网 ＋ 另立语音主机"的双机形态下，把 prep（`.51`）→ live（语音主机）→ post（`.51`）三段串成闭环，且**简历数据与候选人身份字段一步都不进语音主机**
- 让每条面试评分都能点回候选人原话并回放音频；让"谁看过哪场录音、录音何时删"全部成为可断言事实
- 内部模拟批次上测出延迟中位 <800 ms、3 名面试官一致性，再谈真实候选人
- 探针先行：live 段能不能做、在哪做，由 U0 的五项探针决定，⛔ 不用"应该能装"推进

**Non-Goals:**

- 不做活体／证件比对（D13）；不做视频；不做说话人识别与声纹
- 不做候选人自助排期／改期（`interview-scheduling`）
- 不做多语言；一期只中文
- 不做水平扩展——年招几十到百人，同一时刻并发场次按 ≤3 设计
- 不把 SQLite 换 Postgres；不引入容器

## Decisions

### 决策 D1：级联架构与三段式（对应 intent D1）

**做法**：prep 在 `.51` 离线跑重推理（题目／rubric／预埋追问）；live 在语音主机跑 LiveKit Agents worker：FunASR 流式 ASR → 追问选择（DeepSeek，现网关，只在预埋集合里选）→ CosyVoice TTS；post 在 `.51` 离线跑转写对齐与评分。三段共享 `InterviewContext`（场次标识、题目快照版本、追问上限），⛔ 不共享简历。

**为什么**：`01` §2.4 与 `02` §1 已决策；级联每段延迟可单测、可替换，端到端 speech-to-speech 既不可解释也没有境内自托管选项。

**替代方案**：端到端语音模型。否决——不可解释、无自托管、评分证据无法回指文本。

### 决策 D2：声学信号只展示不计分（对应 intent D2）

**做法**：语速／停顿／静默由 post 段从 turn 音频起止与转写字数算出，落 `interview_turn.acoustic_ref`（JSON，只读展示字段）；评分 Agent 的输入 schema 中**没有**这些字段（结构上进不去），断言再查一遍。

**为什么**：合规红线；与能力无映射关系，进评分即给噪声赋权。

### 决策 D3：AI 不淘汰（对应 intent D3）

**做法**：post 段的输出只写 `analysis_run`／`criterion_score`／`interview_scorecard`，不碰 `application.current_stage_id`、不写 `rejection_record`、不调外发。终面结论由面试官在既有 ATS 流转里人工操作。M2 断言 `ai_score=0` 延用并加"拒绝记录不得引用面试评分 run"。

### 决策 D4：数据模型复用 `02` §2.3，ScoreCard 复用评分审计三件套（对应 intent D4）

**做法**：新表 `interview_session`（`application_id, mode, prep_snapshot_version, invite_token_hash, invite_expires_at, resume_token_hash, phone_verified_at, phone_attempts, recording_uri, retention_until, retention_policy_version, sample_class ∈ {internal_sim, live}, status`）、`interview_consent`（`session_id, kind ∈ {ai_interview, identity_check}, result, consent_version, at`——两条独立行，不合并进 session）、`interview_turn`（`session_id, seq, question_id, question_text, answer_text, answer_mode ∈ {voice, text}, audio_start_ms, audio_end_ms, latency_json, follow_up_of, interrupted_at_ms, asr_confidence, acoustic_ref`）、`identity_check`（`session_id, result CHECK IN (pass,fail,skipped), checked_at`——**无图像列**，CHECK 由断言守）、`prep_snapshot`（`application_id, version, profile_version, resume_run_id, gen_run_id, confirmed_by, confirmed_at, status ∈ {draft, frozen, expired}`）、`prep_question`（`snapshot_id, seq, dimension, difficulty, text, rubric_json, follow_ups_json, rationale, origin ∈ {ai, ai_edited}, ai_text`）、`interview_recording_deletion`（删除留痕）、`interview_access_log`。评分：`analysis_run.run_type='interview'`，`criterion_score.evidence_ref={type:'interview_turn', id, start, end, quote}`。全部 `CREATE TABLE IF NOT EXISTS`。

**为什么**：`02` §2.3 已设计；同意拆成独立表是因为"两项各自单独同意"要求两条记录各带版本，塞进 session 两组列会在条款升版时对不齐。

### 决策 D5：一次性链接 ＋ 双同意（对应 intent D5）

**做法**：令牌 32 字节随机，库内只存哈希；打开即置 `used_at`，续入走独立 `resume_token`（同样一次性，中断时签发，上限 3 次）。同意页两个独立勾选框、两条独立记录；条款文本放 `config/consent/<kind>-v<N>.md`，版本号进记录。

**为什么**：合规红线"候选人入口一律一次性链接"；双同意是 `02` §6 清单原文。续入令牌单独发是为了让"一次性"在断线场景下仍成立，而不是把主令牌改成"可重复"。

### 决策 D6：邀约只经 `deliver_candidate_message()`（对应 intent D6）

**做法**：`effect_deliver_invitation` 节点调既有门禁，类型 `interview_invitation`，草稿带 AI 生成标识；总开关关时节点返回"人工转达"并把链接展示在 HR 工作台。⛔ 代码里不得 import `channel.deliver` 直连。

**为什么**：TD-8 登记的空通道由本包成为第一个调用方；门禁 fail-closed 语义本包不改。

### 决策 D7：模型治理沿用网关（对应 intent D7）

**做法**：prep 出题、追问选择、post 评分三处 LLM 调用全部经 LLM 网关，`temperature=0`，响应侧 `model` 回存；ASR／TTS 自托管无版本漂移问题，但模型文件哈希与版本号落 `docs/m3-voice-probe.md` 并写进场次 `latency_json.engine_versions`。

### 决策 D8：题库起步数据（对应 intent D8）

**做法**：prep 首版只用 M1 冻结画像 ＋ M2 脱敏／合成样本的评分结果；`prep_question.rationale` 必须指向画像维度或具体 `criterion_score`。待专员的"各岗现用面试题"回件后作为 few-shot 种子进 prompt（版本升号），⛔ 不作为硬编码题库。X6 校准＝按试运行数据调整维度权重与难度分布，非阻塞。

**为什么**：任务驱动路线图改口径；等 M2 两周数据会让整包空转。

### 决策 D9：面试官侧沿用 Web 工作台（对应 intent D9）

**做法**：四组页面挂在既有 FastAPI `root_path` 下：题目确认页（业务经理）、候选人答题端（同意／验证码／语音房间／文本降级，静态引入 LiveKit 浏览器 SDK）、ScoreCard 页（面试官）、场次进度页（HR）。一律相对路径。

**为什么**：企微卡片已移阶段二、aibot 被动；"看原话＋回放音频"的交互密度只有 Web 能承载。

### 决策 D10：部署形态（对应 intent D10）

**做法**：`.51` 侧仍是 venv ＋ Windows 计划任务（post 评分与到期删除各一个计划任务）；语音主机形态由 D12 定。⛔ 不引入容器。

### 决策 D11：现在启动（对应 intent D11）

已由 Q-F4 改答定；本包不等 M2 上线。

### 决策 D12：live 段跑在另立的境内主机，`.51` 只出站（对应 intent D12，Q1a）

**做法**：新增一台境内主机（云主机优先，Linux，具体规格由 U0 探针 P1–P3 的资源占用定）跑 LiveKit server ＋ TURN ＋ agents worker ＋ FunASR ＋ CosyVoice。`.51` 与语音主机之间只有两类交互，**全部由 `.51` 主动发起**（`.51` 无公网入站）：① `.51` 出站 POST 下发场次快照（题目文本／题序／预埋追问／追问上限／场次标识）并领取候选人房间令牌；② `.51` 出站轮询拉取 turn 事件与场次结束后的录音，拉取成功即通知语音主机删副本。接口鉴权：预共享密钥 ＋ 请求签名（HMAC）＋ 时间戳防重放；TLS 由语音主机反代终结。候选人浏览器直连语音主机（公网），⛔ 候选人不接触 `.51`。

**为什么**：Shao Peishen 答 Q1a——远程面试成立、延迟目标可达。`.51` 无公网无反代，硬开公网入口会把整台简历库暴露出去；把语音链路搬出去后，`.51` 上没有任何新的入站面。

**代价（已登记）**：🔴 主机预算与采购不可代；合规验收 #2 范围扩到第二台机（录音在语音主机短暂驻留）；两机接口多一套鉴权要维护。

**替代方案**：`.51` 直接暴露公网 ＋ 反代。否决——简历库直接暴露、`.51` 是 Windows CPU 机跑 ASR/TTS 也不现实。

### 决策 D13：一期身份核验＝手机号验证码，`identity_check.result` 一律 `skipped`（对应 intent D13，Q2a）

**做法**：候选人打开链接→双同意→输入验证码。验证码 6 位、5 分钟有效、错 5 次锁场次。验证码送达与链接**分离**：短信通道采购前，验证码在候选人请求时生成并展示在 HR 工作台，HR 经该手机号对应渠道（短信／微信）人工转告；采购后由 `effect_send_verification_code` 节点发送（见 Open Questions Q3 门禁口径）。验证通过写 `interview_session.phone_verified_at`；`identity_check` 写一行 `result='skipped'`，保留给活体／证件比对。手机号来源：真实候选人取 `candidate-contact-vault`（不可代开关）；内部模拟场次由 HR 现场转告，不经 vault。

**为什么**：Shao Peishen 答 Q2a——替考风险由人工终面兜底；活体／证件比对触及人脸识别办法，法务结论未出前不做。把弱核验记录在 session 而不是 `identity_check.result`，是为了让 `identity_check` 表在语义上只表达"强核验"，法务复核时不混淆。

### 决策 D14：先内部模拟，真实候选人开闸另走 G3 前定夺（对应 intent D14，Q3a）

**做法**：`interview_session.sample_class` 默认 `internal_sim`；`live` 场次的签发受 `Settings.live_interview_enabled`（默认 `False`，每次求值，⛔ 不缓存）约束，且再 AND：合规验收 #2 签认文件存在 ＋ `CANDIDATE_OUTBOUND_ENABLED` 或人工转达路径可用。内部模拟批次的判据：≥10 场、延迟中位 <800 ms、3 名面试官一致性评估完成并落档。

**为什么**：Shao Peishen 答 Q3a；口径与 M2 真实简历入库闸一致，reviewer 不用学第二套。

### 决策 D15：ASR／TTS 只允许自托管，探针不过则 live 延后（对应 intent D15，Q4a）

**做法**：U0 探针 P2／P3 在目标环境实装 FunASR／CosyVoice 并测首字／首帧延迟；不可装或延迟超预算 ⇒ 记录阻塞点、live 段（U4）标「⏸ 留步」，prep（U2）／post（U5）照做且 post 可先用**文本作答场次**验证评分链路。⛔ 不切境内云 ASR／TTS API。

**为什么**：Shao Peishen 答 Q4a——录音不出自家主机。文本作答本来就是必做的降级通道（`02` §5），用它先跑通评分链路不是绕路。

### 决策 D16：prep 题目须业务经理确认后冻结（对应 intent D16，Q5a）

**做法**：`compute_prep` → `interrupt()` → 业务经理在题目确认页逐题看／改／删／重生成 → `effect_freeze_prep` 写 `prep_snapshot.status='frozen'`（幂等键 `{application_id}:effect_freeze_prep:{version}`）。开场校验 `status='frozen'`，否则拒绝。人工改过的题 `origin='ai_edited'`、`ai_text` 保留原文。

**为什么**：Shao Peishen 答 Q5a；与 M1 画像冻结同形态。

### 决策 D17：追问选择是纯函数且只在预埋集合内选

**做法**：`app/agents/follow_up_selector.py::select(question, follow_ups, transcript) -> FollowUpChoice`，输出 schema 是枚举（`follow_up_index | next_question`），模型输出不在枚举内按 `next_question` 处理并计数。语音主机上的 worker 只调这个纯函数 ＋ 网关，不写库；turn 事件回传 `.51` 由 `effect_persist_turn` 落库。

**为什么**：铁律 2；也是"AI 不会临场生成新题"的结构性保证——题目集合在冻结时已定，候选人之间可比。

### 决策 D18：录音留存起步值 90 天，终值以合规验收 #2 为准

**做法**：`retention_policy` v1＝场次结束后 **90 天**（与 M2 评测集归档留存对齐，Q7 先例）；到期删除由 `.51` 计划任务每日跑 `scripts/purge_interview_recordings.py`，幂等（按 `interview_recording_deletion` 是否已有行判断）。转写文本与评分项随录音同期删除，`analysis_run` 留痕保留但 `raw_response` 里的转写段脱敏。法务定终值后只改策略版本号与天数，⛔ 不改机制。

**为什么**：intent 与 `02` §6 要求"留存期限与删除机制"是交付项；起步值不能空着等法务，否则 U1 建表就没值可写。90 天是本项目已有的裁决先例，不是新拍的数。

### 决策 D19：语音主机上的 worker 无库访问、无简历

**做法**：语音主机只有一个进程组：LiveKit server、TURN、agents worker。worker 的输入只有 `.51` 下发的场次快照 JSON；worker 的输出是 turn 事件队列（本地 SQLite 队列，被 `.51` 拉走后标记）与录音文件。下发 JSON 的 schema 有测试断言：不含 `resume`、`candidate`、`name`、`phone` 等键。

**为什么**：D12 的"简历数据不出 `.51`"要有结构性保证，不能靠"记得别传"。

### 决策 D20：LangGraph 子图形态与幂等键

**做法**：每个场次一个 thread（`thread_id = session_id`）；prep 子图按投递（`thread_id = application_id:prep:{version}`）。节点序列：
- prep：`compute_prep` → `interrupt()` → `effect_freeze_prep`
- invite：`effect_issue_invite`（键 `{session_id}:effect_issue_invite:{token_hash}`）→ `effect_deliver_invitation`（键 `{session_id}:effect_deliver_invitation:{draft_id}`，经门禁）
- live：`effect_open_session` → `effect_persist_turn`（键 `{session_id}:effect_persist_turn:{seq}`）×N → `effect_close_session` → `effect_fetch_recording`（键 `{session_id}:effect_fetch_recording:{recording_sha}`）
- post：`compute_align` → `compute_score` → `effect_persist_scorecard`（键 `{session_id}:effect_persist_scorecard:{analysis_run_id}`）
- 删除：`effect_purge_recording`（键 `{session_id}:effect_purge_recording:{retention_policy_version}`）
全部与业务写同事务、同连接（`effect-transaction-integrity`）。

## 交付单元与顺序

| 顺序 | 单元（tasks.md 章） | 内容 | 前置 | 人事部可见 |
|---|---|---|---|---|
| 1 | U0 技术探针（第 1 章） | P1–P5，结论落 `docs/m3-voice-probe.md`；决定 live 段做不做、在哪做 | §0 R-9 ✅ | 否 |
| 2 | U1 面试域数据模型（第 2 章） | 建表、CHECK、评分审计接线、留存字段 | G2 放行 | 否 |
| 3 | U2 prep 出题引擎（第 3 章） | 生成、确认页、冻结 | U1 | **是——业务经理第一眼看到的东西** |
| 4 | U3 邀约与同意（第 4 章） | 令牌、门禁接线、双同意、验证码 | U1 | 是（HR 签发） |
| 5 | U5 post 评分与 ScoreCard（第 6 章） | 对齐、评分、ScoreCard、要点提示；先用文本作答场次验证 | U2、U3 | 否 |
| 6 | U4 live 语音链路（第 5 章） | 语音主机、房间、ASR/TTS、打断、录制、降级、回传 | U0 通过 ＋ 🔴 主机到位 | 是（候选人端） |
| 7 | U6 面试官与 HR 视图（第 7 章） | ScoreCard 页、回放、进度、一致性导出 | U5 | 是 |
| 8 | U7 合规断言、留存删除与开闸（第 8 章） | 断言、purge、内部模拟批次验收、🔴 开闸、🔴 发版 | U6；合规验收 #2 | 是（开闸后） |

**为什么 U5 排在 U4 前**：U4 依赖主机采购与探针，是全包最不确定的一段；post 评分链路用文本作答场次即可验证（D15），先做能让"每条评分回指原话"这个核心价值在没有语音的情况下先被看到。章号与物理顺序：第 6 章排在第 5 章前，编号不改。

## Risks / Trade-offs

- [FunASR／CosyVoice 在目标主机装不上或 CPU 延迟超预算] → U0 首周实测；不过则 U4 留步，U2/U3/U5/U6 照做，post 用文本作答场次验证；🔴 是否上 GPU 云主机属预算不可代，登记不替拍
- [`livekit-agents` 不支持 Python 3.14] → 语音主机 Python 版本不必与 `.51` 一致（D19 worker 无 `.51` 库依赖），可用 3.12；P5 记录结论
- [`.51` 出站轮询拉 turn 事件有秒级延迟，面试官"实时观战"做不到] → 一期不做实时观战（非目标）；轮询间隔 2 s 只影响 HR 进度页刷新
- [候选人浏览器到语音主机的 WebRTC 被企业网络／运营商拦] → TURN 必配（P1 评估）；仍失败走文本作答降级
- [录音在语音主机短暂驻留，合规范围扩大] → 回传即删＋短期过期清理＋断言；合规验收 #2 明确写第二台机
- [ASR 转写错误导致评分证据"原话"不准] → 低置信度 turn 标注、面试官可回放原音频；评分证据 `quote` 反查校正
- [追问选择 TTFT ~500 ms 占满预算] → 追问选择输入只含本题与本轮转写（不带全程上下文）；P4 实测；超预算则预埋追问按规则触发（关键词命中）作退路，登记技术债
- [业务经理不愿逐题确认、题目长期停在 draft] → 确认页支持"全部采纳"一键冻结（仍留确认人与时刻）；HR 进度页显示待确认积压
- [内部模拟员工的录音也是个人信息] → 内部模拟走同一套同意与留存策略，不因是员工而豁免
- [验证码人工转告期间 HR 看到验证码] → 验证码只在候选人请求时生成、5 分钟失效、HR 工作台展示留痕；短信通道采购后关闭展示路径
- [两机接口密钥泄露] → 密钥只在两机环境变量；签名带时间戳 ±60 s；轮换脚本；密钥不进仓库

## Migration Plan

1. U1 新表全部 `CREATE TABLE IF NOT EXISTS`，`.51` 既有表一行不改；`analysis_run.run_type` 出现新值 `interview` 无需迁移
2. 语音主机：U0 探针通过 ＋ 🔴 采购到位后，`scripts/provision_voice_host.sh`（幂等、可重跑）装 LiveKit／TURN／worker／ASR／TTS；密钥经环境变量注入
3. `.51` 侧新依赖只有 HTTP 客户端与 LiveKit 房间令牌签发库（纯 Python），无重依赖；`requirements.txt` 与 `sync-to-server.sh` 白名单同步
4. 发版顺序：U1→U2→U3 合并后先发一次（业务经理可确认题目、HR 可签发内部模拟链接，🔴 G3 由 Shao Peishen 拍「发」）；U5→U6 合并后再发一次（文本作答场次的 ScoreCard 可见）；U4 合并且语音主机就位后第三次；U7 合并且合规验收 #2 通过后由 Shao Peishen 决定开闸
5. 回滚：`live_interview_enabled` 关回即停签真实场次；语音主机可整机停用，`.51` 侧只影响 live 段；新表不删（含留痕）；代码回退上一版

## Open Questions

> 以下各项在 propose 阶段**不能也不应**由本会话闭环：待专员项等 `人事部#3` 闭环后成信；外部依赖等法务／采购。本包停在 propose 等 G2，Open Questions 非零是允许的最终状态。**当前条数：11。**

**待专员（`HR-G-NN` 四条，原样转自 intent「待专员」表，登台账时现取编号；判例批改硬规则照 `requirement-grill` SKILL §三）**

- **OQ-1 `HR-G-NN` 现用面试流程**：几轮、谁面、每轮多长、是否有结构化打分表。计划取近 1 年 3 个岗位的面试记录（脱敏）。影响 D1/D3 的人工终面衔接与 U2 题量。未答前按"一轮 AI 语音面试（≤30 分钟、8–12 题）＋ 一轮人工终面"起步
- **OQ-2 `HR-G-NN` 各岗现用面试题与「必问项」**：M1 试点三岗各 ≤10 题，作 prep 的 few-shot 种子（D8）。未答前 prep 只按画像＋简历弱点生成
- **OQ-3 `HR-G-NN` 3 名面试官一致性评估人选与可用时间**：验收（`02` §3 M3）。未答前 U7 的一致性评估任务标 ⏸
- **OQ-4 `HR-G-NN` 到访面试的房间／设备／网络现状**：内部模拟要用（D14）；也决定候选人在公司现场作答时的网络路径。未答前内部模拟按"员工自带笔记本＋公司 Wi-Fi 直连语音主机公网地址"起步

**外部依赖与技术探针（原样转自 intent「外部依赖与技术探针」）**

- **OQ-5 X5 探针 P1–P5 结论**：LiveKit 可运行性与 TURN 需求（P1）、FunASR 可装性与首字延迟（P2）、CosyVoice 可装性与首帧延迟（P3）、DeepSeek TTFT（P4）、`livekit-agents` 对 3.14 兼容性（P5）。由 U0 执行，结论落 `docs/m3-voice-probe.md`；P2/P3 不过 ⇒ U4 留步（D15）。前置 R-9 ✅ 已完成（`0917BB`）
- **OQ-6 合规验收 #2（法务）**：AI 面试单独同意条款文本、身份核验单独同意文本、录音留存期限终值（D18 起步 90 天）、人脸识别办法适用性结论（一期不做活体，但需法务确认"手机号验证码"不落入该办法）。🔴 不可代；未过 ⇒ 真实候选人不开闸
- **OQ-7 X6 M2 试运行数据校准题库**：非阻塞；到位后升 prompt 版本
- **OQ-8 候选人对外通道**：`CANDIDATE_OUTBOUND_ENABLED` 开启与真实候选人开闸——G3 前单独定夺，🔴 不可代
- **OQ-9 语音主机采购与规格**：D12 定"另立境内主机"，但云厂商／规格（CPU 还是 GPU）／预算取决于 P2/P3 的资源占用实测。🔴 预算与采购不可代；未采购前 U0 可在开发机做可装性预探针（结论标"非目标机"）
- **OQ-10 短信验证码通道**：采购与否、供应商。采购前走 HR 人工转告（D13）。🔴 采购不可代。另：自动发送验证码是否作为"已人工确认邀约的从属动作"免逐次门禁确认——若须逐次确认则验证码时效做不到，需要 `outbound-approval-gate` delta 新增类型；本包保守按"人工转告"起步，⛔ 不擅自加 delta，G2 时定
- **OQ-11 内部模拟场次的录音留存**：员工扮演候选人产生的录音是否沿用 90 天、还是评估完成即删。本包保守按同一策略（不豁免），法务可在 #2 里一并定
