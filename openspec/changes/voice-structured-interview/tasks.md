**进度：36/76**（2026-09-18 `0918B` 立包。🔴 = 不可代项（括号内写谁做）；⏸ = 待 Shao Peishen 裁决或待专员／外部依赖，对应 `design.md` Open Questions（当前 11 条）；每章 = 一个交付单元 = 一份 superpowers plan = 一条 worktree 分支；物理顺序 0→U0→U1→U2→U3→U5(第 6 章)→U4(第 5 章)→U6→U7，章号不改。涉及副作用的任务已逐条写幂等策略。）

## 0. 前置门槛（不写代码；任一未过则对应下游单元不得发车）

- [x] 0.1 🔴 **Q-27 `.51` VC++ 运行库升级（`relay:R-9`）**（Shao Peishen 放行 2026-09-17 21:5x；`0917BB` 已装 v14.27→v14.44，torch/FlagEmbedding 可导入）。判据：`docs/m2-model-comparison.md`「BGE-M3 本地 CPU 召回」有 `.51` 耗时行 ✅。阻塞 U0 在 `.51` 侧的 P4/P5
- [x] 0.2 **X5 探针五项结论落档**（由第 1 章 U0 执行）。判据：`docs/m3-voice-probe.md` 对 P1–P5 各有「通过／阻塞＋原因」结论行。阻塞 U4 发车（P2/P3 任一阻塞 ⇒ U4 留步，其余单元不受影响）
- [ ] 0.3 🔴 **合规验收 #2 启动**（Shao Peishen 发起法务通道，与 X3 同一通道；AI 面试单独同意条款 ＋ 身份核验单独同意 ＋ 录音留存期限终值 ＋ 人脸识别办法适用性结论）。判据：四份结论落 `docs/compliance/` 并由本人签认。阻塞 U7 的 8.9 开闸；⛔ 不阻塞代码
- [ ] 0.4 🔴 **语音主机采购与预算**（Shao Peishen；规格依 1.2/1.3 资源占用实测）。判据：主机可 ssh、公网 IP 与域名可用、`docs/m3-voice-probe.md`「目标机」节填入。阻塞 U4 的 5.1 起
- [ ] 0.5 ⏸ **待专员四项**（`人事部#3` 闭环后成信：面试流程／现用面试题／面试官人选／面试间现状，design OQ-1–4）。判据：`docs/口径点台账.md` 登记四个 `HR-G-NN` 并有回件。不阻塞任何代码单元；影响 3.2 few-shot 种子与 8.7 一致性评估人选

## 1. U0 技术探针（X5，P1–P5；结论决定 live 段做不做、在哪做）

- [x] 1.1 `scripts/probe_m3_voice.py` 骨架：五项探针各一个子命令，输出统一 JSON（项／环境指纹／结论／耗时／阻塞点），结果追加写 `docs/m3-voice-probe.md`（幂等：同环境指纹＋同项覆盖同一行）
- [x] 1.2 P1 LiveKit server 可运行性：目标机（未采购前在开发机，结论标「非目标机」）下载 Go 二进制、起单节点、两个浏览器页建连；TURN 需求评估（从公司 Wi-Fi 与手机 4G 各测一次）。结论落档
- [x] 1.3 P2 FunASR 可装性与流式 ASR 首字延迟：目标机 venv 实装（记录 Python 版本、wheel 来源），用 30 s 中文样本测首字延迟中位／P95 与 CPU 占用；不可装则记录阻塞点（wheel 缺失／编译错误）
- [x] 1.4 P3 CosyVoice 可装性与 TTS 首帧延迟：同 1.3 方法；记录模型文件哈希与版本号
- [x] 1.5 P4 追问选择 LLM TTFT：在 `.51` 上经现网关对 DeepSeek 跑 20 次追问选择 prompt（只含本题＋本轮转写），记 TTFT 中位／P95；与 D17 枚举 schema 一起测输出合规率
- [x] 1.6 P5 `livekit-agents` SDK 兼容性：按 `docs/findings/2026-09-08-aibot-sdk-py314-兼容性.md` 方法测 3.14 与 3.12；结论写明语音主机选用的 Python 版本
- [x] 1.7 探针总结：`docs/m3-voice-probe.md`「结论」节填「live 段：可做／留步」与「目标机规格建议（CPU/GPU、内存、带宽）」，作为 0.4 采购输入；tasks 0.2 回勾

## 2. U1 面试域数据模型（建表 ＋ 评分审计接线 ＋ 留存字段）

- [x] 2.1 `app/storage/db.py` 新增 `prep_snapshot`（`application_id, version, profile_version, resume_run_id, gen_run_id, confirmed_by, confirmed_at, status CHECK IN draft/frozen/expired`，`(application_id, version)` 唯一）与 `prep_question`（`snapshot_id, seq, dimension, difficulty, text, rubric_json, follow_ups_json, rationale, origin CHECK IN ai/ai_edited, ai_text`）；`CREATE TABLE IF NOT EXISTS`，⛔ 不进 `_ADDED_COLUMNS`
- [x] 2.2 新增 `interview_session`（D4 列清单；`invite_token_hash` 唯一；`sample_class CHECK IN internal_sim/live`；`status CHECK IN pending/in_progress/completed/interrupted/abandoned/locked`；`retention_until` NOT NULL；`retention_policy_version` NOT NULL）
- [x] 2.3 新增 `interview_consent`（`session_id, kind CHECK IN ai_interview/identity_check, result CHECK IN accepted/declined, consent_version, at`；`(session_id, kind)` 唯一）与 `identity_check`（`session_id, result CHECK IN pass/fail/skipped, checked_at`；**无图像列**）；反证测试：向 `identity_check` 加图像列的迁移被测试拒绝
- [x] 2.4 新增 `interview_turn`（`session_id, seq, question_id, question_text, answer_text, answer_mode CHECK IN voice/text, audio_start_ms, audio_end_ms, latency_json, follow_up_of, interrupted_at_ms, asr_confidence, acoustic_ref`；`(session_id, seq)` 唯一；`answer_mode='text'` 时 `audio_*` 必空的 CHECK）
- [x] 2.5 新增 `interview_recording_deletion`（`session_id, deleted_at, scope, reason CHECK IN expired/withdrawn/terminated, actor`；`session_id` 唯一）与 `interview_access_log`（`accessor, session_id, access_type, at`，无内容列）
- [x] 2.6 `analysis_run.run_type` 增加 `interview` 取值约定；`criterion_score.evidence_ref` 解析工具函数支持 `{type:'interview_turn', id, start, end, quote}`，并校验 turn 存在与偏移合法；测试覆盖 resume 与 interview_turn 两种类型
- [x] 2.7 `interview_session` 的 AI 输入 schema（Pydantic）：`PrepInput`／`FollowUpInput`／`ScoreInput` 三个模型，字段白名单里**没有**姓名／手机号／核验字段／声学字段——测试用反射断言字段集合
- [x] 2.8 `tests/test_db_m3_schema.py`：新库建表齐全、老库（复制 `.51` demo.db 结构）升级后既有表一行不改、全部 CHECK 反证、`retention_until` 空值被拒

## 3. U2 prep 出题引擎（生成 → 业务经理确认 → 冻结）

- [x] 3.1 `app/agents/interview_prep.py::generate(profile, rubric, resume_scores) -> PrepDraft`（纯函数，经 LLM 网关，`temperature=0`，输出 schema 强制每题带 `dimension/difficulty/rubric/follow_ups[≥1]/rationale`）；维度不在白名单的题丢弃并计数；全丢判失败
- [x] 3.2 prompt 版本 v1：只用画像＋简历弱点；预留 few-shot 槽位（待 OQ-2 现用面试题回件后升 v2，⛔ 不硬编码题库）；无简历评分时走"通用题"分支并在 `rationale` 标注
- [x] 3.3 难度曲线策略：岗位级配置 `prep_curve ∈ {easy_to_hard, by_dimension}`，默认 `easy_to_hard`；题量岗位级配置默认 10；测试：同输入同配置输出题序稳定
- [x] 3.4 LangGraph prep 子图：`compute_prep` → `interrupt()` → `effect_freeze_prep`（幂等键 `{application_id}:effect_freeze_prep:{version}`，与 `prep_snapshot`/`prep_question` 写同事务）；测试：重跑不重复建快照
- [x] 3.5 `effect_persist_prep_draft` 节点：把 draft 落 `prep_snapshot(status=draft)`＋`prep_question`（幂等键 `{application_id}:effect_persist_prep_draft:{gen_run_id}`）；`analysis_run` 留痕关联
- [x] 3.6 题目确认页（业务经理，Web）：逐题看／改题面与 rubric／删／重生成／"全部采纳"；改过的题 `origin=ai_edited` 且 `ai_text` 保留；页面带 AI 生成标识；接口相对路径
- [x] 3.7 冻结与开场校验：`freeze(snapshot_id, confirmed_by)` 记确认人与时刻；开场入口校验 `status='frozen'` 否则 4xx 并留痕；画像升版 ⇒ 旧快照 `expired`、可生成新版本；测试穷举 draft/frozen/expired 三态转移
- [x] 3.8 prep e2e 测试：合成画像＋M2 合成样本评分 → 生成 → 确认页改一题 → 冻结 → 开场校验通过；断言 `PrepInput` 不含身份字段

## 4. U3 邀约与同意流程（一次性链接 ＋ 门禁接线 ＋ 双同意 ＋ 验证码）

- [x] 4.1 令牌签发：32 字节随机、库存哈希、`invite_expires_at` 岗位级配置默认 7 天；`effect_issue_invite`（幂等键 `{session_id}:effect_issue_invite:{token_hash}`）；同一场次重复签发 ⇒ 旧令牌作废并留痕
- [x] 4.2 令牌校验端点：首次打开置 `used_at`；重复／过期 ⇒ 统一失效页（不泄露场次信息）＋留痕；测试三种路径
- [x] 4.3 续入令牌：中断时签发 `resume_token`（一次性、上限 3 次、随场次过期）；测试续入不重复出题（与 5.9 联动）
- [x] 4.4 `effect_deliver_invitation`：调 `deliver_candidate_message(type='interview_invitation')`，草稿带 AI 生成标识；总开关关 ⇒ 返回「人工转达」并把链接展示在 HR 工作台；幂等键 `{session_id}:effect_deliver_invitation:{draft_id}`；反证测试：代码中不得 import `channel.deliver`
- [x] 4.5 同意条款文件 `config/consent/ai_interview-v1.md`、`identity_check-v1.md`（占位文本，标「待法务 #2 定稿」）；版本号解析与展示；升版后新场次用新版本、旧记录不变
- [x] 4.6 同意页（候选人端）：两个独立勾选、告知留存期限／AI 只作参考／可申请人工面试；提交写两条 `interview_consent`；任一拒绝 ⇒ 场次 `abandoned`＋留痕＋HR 工作台提示改约；幂等键 `{session_id}:effect_record_consent:{kind}:{version}`
- [x] 4.7 验证码：6 位、5 分钟、错 5 次锁场次（`status=locked`＋留痕）；通过写 `phone_verified_at`＋`identity_check(result=skipped)`；幂等键 `{session_id}:effect_verify_phone:{attempt_no}`
- [x] 4.8 验证码送达：短信通道未配置 ⇒ 候选人请求时生成并在 HR 工作台展示（展示留痕）；配置了 ⇒ `effect_send_verification_code` 节点（⏸ 门禁口径 OQ-10 未定前该节点只留接口、默认不启用）
- [x] 4.9 真实候选人开闸：`Settings.live_interview_enabled` 默认 `False`，每次签发求值，AND 合规验收 #2 签认文件存在；关闭时只允许 `sample_class=internal_sim`；测试：关闭时签发 live 被拒并留痕
- [x] 4.10 手机号来源接线：`live` 场次从 `candidate-contact-vault` 读（vault 开关关 ⇒ 签发被拒）；`internal_sim` 场次不经 vault；本包任何表不存明文手机号（grep 断言）
- [x] 4.11 HR 签发页（Web）：选投递（须有 frozen 快照）→ 签发 → 展示链接／验证码（人工转达模式）→ 状态；e2e：签发 → 打开 → 双同意 → 验证码 → 场次 `pending→in_progress` 前置校验通过

## 6. U5 post 转写对齐 ＋ rubric 评分 ＋ ScoreCard（物理顺序在第 5 章 U4 之前；先用文本作答场次验证）

- [x] 6.1 `compute_align`：把 `interview_turn` 整理为证据单元（turn id、文本、字符偏移、音频起止、`asr_confidence`）；低置信度阈值岗位级配置默认 0.6，低于则标「转写待复核」；纯函数＋测试
- [x] 6.2 `app/agents/interview_scoring.py::score(snapshot, turns) -> ScoreCardDraft`（纯函数，经网关，`temperature=0`）；输出 schema 每维强制 `evidence:{turn_id, start, end, quote}`；缺证据整次判不可用不落分；`ScoreInput` 不含核验／声学／身份字段（2.7 断言）
- [x] 6.3 证据反查校正：用 `quote` 在 turn 文本里反查偏移，不一致以反查为准；反查失败判该维无证据；测试覆盖偏移错位与摘录不存在两种
- [x] 6.4 要点提示生成：从各维低分项与低置信度 turn 派生「建议终面追问」列表（规则派生，不再调 LLM），每条指向维度与 turn
- [x] 6.5 声学参考：从 turn 音频起止与转写字数算语速／停顿／静默比例，写 `interview_turn.acoustic_ref`（只读展示字段）；文本作答 turn 为空；测试：评分输入中不出现该字段
- [x] 6.6 `effect_persist_scorecard`：写 `analysis_run(run_type=interview)`＋`criterion_score`（`evidence_ref` type=interview_turn）＋要点提示，同事务；幂等键 `{session_id}:effect_persist_scorecard:{analysis_run_id}`；反证：`evidence_ref` 空或悬空被 CHECK／校验拒绝
- [x] 6.7 post 子图与触发：场次 `completed` 后由 `.51` 计划任务批处理触发 `compute_align → compute_score → effect_persist_scorecard`；失败标「评分失败待重试」可观测；重试不重复落分
- [x] 6.8 post e2e（文本作答场次）：用 U3 签发的内部模拟场次以文本作答走完 10 题 → post 评分 → 每维有 turn 回指且可定位 → `rejection_record` 无新增、`application` 阶段不变

## 5. U4 live 语音链路（语音主机；前置：0.2 P1–P3 通过 ＋ 0.4 主机到位；任一不满足 ⇒ 本章「⏸ 留步」，⛔ 不判整包失败）

- [ ] 5.1 `scripts/provision_voice_host.sh`：幂等安装 LiveKit server／TURN／agents worker／FunASR／CosyVoice（版本按 `docs/m3-voice-probe.md` 锁定）；密钥经环境变量；无害预检（端口／磁盘／Python 版本）不足即退出并说明
- [ ] 5.2 两机接口：`.51` 出站 `POST /sessions`（下发快照、领房间令牌）与 `GET /sessions/{id}/events?since=`、`GET /sessions/{id}/recording`、`DELETE /sessions/{id}/artifacts`；HMAC 签名＋时间戳 ±60 s 防重放；语音主机无任何入站到 `.51` 的代码路径（grep 断言）
- [ ] 5.3 下发快照 schema `SessionBundle`（题目文本／题序／预埋追问／追问上限／场次标识／曲线）；测试用反射断言不含 `resume`/`candidate`/`name`/`phone` 键（D19）
- [ ] 5.4 agents worker：按题序播报（CosyVoice）→ 端点检测＋FunASR 流式转写 → `follow_up_selector.select()`（D17，枚举 schema，越界按下一题）→ 追问或下一题；追问上限岗位级配置默认 2；每轮记分段延迟与端到端延迟到本地事件队列
- [ ] 5.5 打断处理：候选人开口 ⇒ 300 ms 内停播报、记截断毫秒、转写不丢；「再说一遍」／重听按钮重播不计追问；测试用录制音频回放模拟
- [ ] 5.6 全程录制：双方音频写场次文件，每 turn 记录起止毫秒；录制失败 ⇒ 场次 `interrupted`＋候选人可续入提示；回传后语音主机删副本并回报；未回传中间文件 24 h 过期清理（幂等）
- [ ] 5.7 文本作答降级：候选人主动切换或连续两轮丢包超阈值时提示（不自动切）；切换留痕；混合场次每 turn `answer_mode`；文本 turn 无音频但保留评分资格
- [ ] 5.8 候选人答题端（Web，静态引入 LiveKit 浏览器 SDK）：房间加入／题目文字同显（带 AI 标识）／打断／重听／切文本；相对路径；无 `.51` 访问
- [ ] 5.9 `.51` 侧 live 子图：`effect_open_session`（键 `{session_id}:effect_open_session:{snapshot_version}`）→ 轮询拉事件 `effect_persist_turn`（键 `{session_id}:effect_persist_turn:{seq}`，`(session_id, seq)` 唯一）→ `effect_close_session` → `effect_fetch_recording`（键 `{session_id}:effect_fetch_recording:{recording_sha}`，校验哈希后通知删副本）；续入不重复出题（与 4.3 联动测试）
- [ ] 5.10 延迟报表：`scripts/report_m3_latency.py` 按场次／批次输出端到端中位与 P95、分段中位；输出进 `docs/m3-voice-probe.md`「内部模拟批次」节
- [ ] 5.11 live e2e（内部模拟 1 场）：签发 → 双同意 → 验证码 → 语音走完 ≥5 题含 1 次打断 1 次追问 → 录音回传 → 语音主机无残留 → post 评分每维回指可回放

## 7. U6 面试官与 HR 视图（Web）

- [ ] 7.1 ScoreCard 页（面试官）：逐维得分＋证据摘录、总体摘要、要点提示、AI 生成标识与"仅供参考"说明；只显示分配到该面试官岗位的场次（鉴权沿用 `hr_account`）
- [ ] 7.2 证据跳转与回放：点证据 ⇒ 定位 turn 原话并回放该段音频（按起止毫秒切片流式）；每次回放／查看转写写 `interview_access_log`，留痕失败 ⇒ 读取失败（fail-closed）
- [ ] 7.3 声学参考区：语速／停顿／静默独立展示，注明"与能力无映射关系，仅供参考"；低置信度 turn 标记并可回放
- [ ] 7.4 HR 场次进度页：按岗位／批次状态分布与完成率（已完成÷已签发）；待确认题目积压；批次完成率导出 CSV
- [ ] 7.5 一致性评估导出：按批次导出去身份字段的 ScoreCard 集合（zip 内 JSON＋可读 md）；导出留痕；测试：导出内容不含姓名／手机号／场次令牌
- [ ] 7.6 视图 e2e：用 6.8 的文本场次 ScoreCard 走 7.1–7.5；断言访问留痕条数 = 查看＋回放次数

## 8. U7 合规断言、留存删除、内部模拟验收与开闸

- [ ] 8.1 `app/audit/assertions.py` 新增 M3 五条：面试评分证据 100% 可回溯（turn 存在＋偏移合法）、拒绝记录不引用面试评分 run、`identity_check` 无图像列＋评分输入无核验字段、评分输入无声学字段、录音到期（宽限 24 h）删除率 100%；接入既有入口与 CI，可按名单独跑
- [ ] 8.2 断言：真实候选人开闸关闭时无 `live` 场次；开启时 `docs/compliance/` 存在 #2 签认记录
- [ ] 8.3 `scripts/purge_interview_recordings.py`：每日扫 `retention_until` 到期场次，删录音＋转写＋评分项（`analysis_run` 保留、`raw_response` 转写段脱敏），写 `interview_recording_deletion`；幂等（已有删除行即跳过）；失败可观测下次重试；`effect_purge_recording` 键 `{session_id}:effect_purge_recording:{retention_policy_version}`
- [ ] 8.4 提前删除：HR 登记候选人撤回／投递终止 ⇒ 走 8.3 同一路径，`reason` 记 withdrawn/terminated
- [ ] 8.5 留存策略 v1（90 天）配置与同意页期限一致性测试；策略升版不缩短已告知期限（测试）
- [ ] 8.6 `.51` 计划任务定义（post 评分批处理、每日 purge）：SYSTEM 账户＋AtStartup＋失败重启 3 次；写进 `docs/deploy-51-server.md`（⏸ 实际安装随 8.10 发版）
- [ ] 8.7 ⏸ **内部模拟批次**（≥10 场，员工扮演候选人；OQ-3 面试官人选未答前 ⏸）：完成率、延迟中位 <800 ms（5.10）、3 名面试官对同批 ScoreCard 独立打分（7.5 导出）→ 一致性结果落 `docs/m3-eval-report.md`
- [ ] 8.8 🔴 **合规验收 #2 通过**（法务结论 ＋ Shao Peishen 签认；前置 0.3）。判据：`docs/compliance/` 四份结论有签认行；4.5 条款文本换正式版并升版
- [ ] 8.9 🔴 **真实候选人开闸**（Shao Peishen 亲自改 `live_interview_enabled`；前置：8.8 ＋ 8.7 达标 ＋ `candidate-contact-vault` 开关开启（另一不可代）＋ 候选人对外通道口径定（OQ-8：`CANDIDATE_OUTBOUND_ENABLED` 开启或明确走人工转达））。判据：首个 `live` 场次签发成功且 8.2 断言绿
- [ ] 8.10 🔴 **`.51` 发版决定**（Shao Peishen；三次发版节奏见 design Migration Plan 4；含 `sync-to-server.sh` 白名单与计划任务安装）。判据：`docs/deploy-51-server.md` 记录本次发版
- [ ] 8.11 🔴 **首批真实场次**（M2 试运行岗位 底层软件工程师 的通过初筛候选人；Shao Peishen 拍板对象名单；HR 签发；面试官终面）。判据：≥3 场 `live` 完成，ScoreCard 进人工终面，`rejection_record` 无 `ai_score`、无引用面试 run；真实批次延迟与完成率复算落 `docs/m3-eval-report.md`「真实批次」节
- [ ] 8.12 归档：`tasks.md` 全勾后当场跑 `openspec-archive-change`（`03` §4 时限）
