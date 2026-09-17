# HR 招聘智能体 · 实施路线图与构建自动化流程

> ⚑ 2026-09-17 21:4x 起，上线推进依据改为任务驱动版 `docs/roadmap/任务驱动上线路线图.md`（本文保留现状与自动化缺口视图）。

> 2026-09-17 12:1x，Cowork·HR业务线-接力0917D 按仓库真身编写（openspec 各包勾选数、`02-系统架构与MVP范围.md`、`docs/session接力.md`、`docs/tech-debt.md`、`.claude/skills/lane-dispatch`、`docs/openers/*`）。
> 本文是**全景视图**：业务上走到哪、构建流水线哪几环已自动、哪几环还缺。逐条待办的真身仍在 `docs/session接力.md`【下一步】，号池在 `docs/openers/号池台账.md`。两处不一致时以那两处为准，并回改本文。

---

## 一、一句话现状

- **两条底座 ready**：① 构建底座——Cowork 远程编排 → launchd 请求文件发车 → 无头泳道（worktree 隔离、哨兵、看护脚本）→ ff 合入 main 已天天在用；② 通信底座——企微 aibot 值守服务收发、归档、入队、回件桥打标、无头拆件会话按章程回灌，2026-09-17 汤丽萍两条真实入站已端到端跑通（5.1–5.4 ✅）。
- **业务主线还停在 M1 收尾**：M1 需求解析与岗位画像代码基本齐（`m1-job-profile-intake` 60/72、`m1-intake-quality-fixes` 68/69），差「10 个历史岗位重跑验收」和两包归档；**M2 简历解析与评分排序尚未立包，M3 未启动**。
- ⇒ 下一阶段重心：**把 AI 专员（汤丽萍）＋ 通信机制 用起来去喂业务**——M1 验收、M2 评测集、M2 需求收敛都要她和业务经理出人出数据，而这条链路今天已经能自动收件、自动拆件。

## 二、业务路线图

| 阶段 | 内容 | 状态（真身） | 卡在哪 | 谁推 |
|---|---|---|---|---|
| **M0 通信底座** | `hr-wecom-aibot-liaison` 值守通道 | 63/67；8.6/8.7 实际已发生（本人私信、汤丽萍入名单并真实入站），未核证勾选；8.8 一周观察窗未开；8.9 归档 | 核证＋开窗 | 泳道（`0917V`） |
| **M0′ 回件自动化** | `liaison-reply-bridge-and-patrol` 回件桥＋打标即开班＋章程＋口径台账 | 33/33；§0–§5 全部 ✅ | ✅ 已归档 2026-09-17（`0917U`，移至 `openspec/changes/archive/2026-09-17-liaison-reply-bridge-and-patrol/`，4 份 delta spec 已同步进 `openspec/specs/`） | 泳道（`0917U`） |
| **M1 需求解析与岗位画像** | 一句话需求 → 追问 → 画像 → 审批 → JD | `m1-job-profile-intake` 60/72、`m1-intake-quality-fixes` 68/69；已部署 `.51`，3 位经理试点反馈已收 | ① 9.1 画像质量验收（10 个**历史**岗位重跑，技术栈字段 ≥80%）需 HR 出岗位与双方评估；② 5.6/6.8 挂起提醒卡 TD-11（无调度器）；③ 企微回调/卡片那 6 条已移出到阶段二 | 验收数据 ⇒ 跟进信给汤丽萍（`人事部#2`，等 #1 闭环）；TD-11 ⇒ 待你定落点 |
| **M2 简历解析与评分排序**（护城河） | 解析管线、200 份私有评测集、硬门槛引擎、BGE-M3 召回＋rubric 精排＋evidence span、HR 工作台、合规验收 #1 | **已立包 0/69**（`0917AD`，`openspec/changes/m2-resume-parse-and-rank/`） | 三个硬前置：① ~~需求 grill → openspec propose~~ ✅ 已立包，下一步 spec-to-plan 从 U0 起；② **200 份评测集要 HR 出人标注**；③ **PIA／合规验收 #1 阻塞真实简历入库** | ① Cowork 起草需求树＋你答；② ③ 跟进信线 |
| **M3 实时语音面试** | prep/live/post | 未启动 | 设计上依赖 M2 真实数据校准题库 | M2 上线后 |

**关键路径**：M0′ 归档 → `人事部#1` 闭环 → `人事部#2`（M1 验收岗位清单＋M2 评测集标注安排）→ M1 9.1 验收 → M1 两包归档 ‖ M2 grill → propose → plan → 分单元 run-build → 合规验收 #1 → M2 上线试运行。

## 三、构建自动化流程（现状）

```
① 需求来源 ──┬─ 你在 Cowork 提出 / 裁决
             └─ 专员回件（企微）→ 值守服务归档入队 → 回件桥打标 → 无头拆件会话按章程回灌结论
                                                                  │（台账转态、口径点台账、接力登记）
② 需求收敛   zhuopin-requirement-grill → openspec propose（intent/proposal/specs/design/tasks）
③ 计划       spec-to-plan（CC，worktree ❌）→ docs/superpowers/plans/*.md
④ 编排       lane-dispatch：判触碰区分泳道 → 写无头块进 OP-0820 待执行区 → 号池登记 → dry-run
⑤ 发车       Cowork 写 .claude/handoff/launch/<ts>.request → launchd WatchPaths → lane-launcher.sh → run-lanes.sh
⑥ 执行       无头 claude -p：脚本强制建 worktree ＋ worktree-guard hook、superpowers TDD/SDD、哨兵 OPENER_DONE/PARTIAL
⑦ 收口       泳道内 ff-only 合 main ＋ push ＋ rev-list/cherry 真合判据 ＋ mark_done 摘标注
⑧ 核验       Cowork 读 results.tsv/日志尾 ＋ 查仓库真身 → 汇报 → 下一批
⑨ 发布       .51 发版（sync-to-server.sh + 快照/冒烟/回滚）🔴 不可代，你拍「发」
⑩ 业务验收   跟进信（send-followup，群发＋docx）→ 专员回件 → 回到 ①
```

| 环节 | 自动化程度 | 说明 |
|---|---|---|
| ① 专员回件 → 拆件回灌 | ✅ 全自动（09-17 真实跑通） | TD-47/48/50 已还；剩 TD-43 三条边角、附件链路未通（见缺口 G1） |
| ② 需求收敛 | 🟡 半自动 | grill 必须有你答；propose 可无头 |
| ③ 计划 | ✅ 可无头 | 已多次泳道化 |
| ④ 编排 | 🟡 Cowork 手工 | 每批由 Cowork 写块、登记、dry-run |
| ⑤ 发车 | ✅ 请求文件路（09-09 修好 plist 后稳定） | `lane-launch-armed-scan` 那套「定时扫＋武装令牌」**已撤包 2026-09-17**（`0917AC`），请求文件路已够用 |
| ⑥ 执行 | ✅ | worktree 强制、hook、体积闸、拆段检查 |
| ⑦ 收口 | ✅ | |
| ⑧ 核验 | 🟡 Cowork 用 send_later 定时回来查 | 无主动推送（缺口 G3） |
| ⑨ 发布 | 🔴 人工（刻意保留） | |
| ⑩ 业务验收发信 | 🟡 CLI 自动发，但你审稿；私信附件未通 | |

## 四、自动化缺口与补齐计划（按「能开尽开」排）

| # | 缺口 | 后果 | 补法 | 状态 |
|---|---|---|---|---|
| **G1** | **私信附件不进归档**：`ArchiveOutcome.attachments` 在生产里恒为空（`bridge.py` 偏离 1），拆件会话只读正文 | 判例批改表、评测集标注、历史岗位资料这些**业务真正要回的文件**全部白发——M1 验收与 M2 评测集都靠它 | 实测真实文件帧字段（fail-closed，同 TD-19/43 处置）→ 接 `store_attachment` → 章程已允许读归档件 | 🚀 `0917W`（worktree） |
| **G2** | **Cowork 改的文档要等下一条泳道顺带提交**：Cowork 碰 `.git/` 会留锁，只能写文件 | 未提交态滞留、ff 合并被挡（09-17 `0917K` 实证）、别的 session `git show` 不到 | 仿发车请求文件：新增「提交请求」通道（`.claude/handoff/commit/<ts>.request` 列路径＋提交信息 → launchd 触发 → 白名单路径校验 → add/commit/push），⛔ 不改 `run-lanes.sh`／`lane-launcher.sh` | 🚀 `0917X`（worktree）；⚠️ 装 LaunchAgent 属安全配置，需你回 Mac 在 Terminal 跑一次安装命令 |
| **G3** | 泳道结束无主动通知，靠 Cowork 定时回查 | 延迟最多 20 分钟；你不在 Cowork 时看不到 | run-lanes 收敛后经值守通道**私信你本人**一条摘要（不进群） | ⏸ 待你定（需求见文末 1） |
| **G4** | **无调度基础设施**（TD-11） | M1 挂起提醒 5.6/6.8、跟进信超期提醒、8.8 观察窗到期提醒都做不了 | 带幂等键的 `effect_*` 提醒节点＋外部调度器；M1 在 `.51`（Windows 计划任务）、值守在 Mac（launchd）——落点不同 | ⏸ 待你定落点（文末 2） |
| **G5** | 拆件结论不会自动变成新任务 | 回件说「要改 X」，要等 Cowork 下次读接力才派 | 章程 §四 已登记待人；下一步是「待人行 → Cowork 编排」的机器可读前缀，先观察一周真实回件再定（避免空转设计） | ⏸ 观察（8.8 窗口内收集样本） |
| G6 | TD-43 三条边角（台账双写者无锁、审计 UTC、串行冲突 detail 空） | 并发时台账可能被覆盖 | 一条小泳道 | ⏸ 与 G1 触碰区重叠（`bridge.py`），排在 `0917W` 之后 |
| G7 | `lane-launch-armed-scan` 0/35 搁置包悬挂 | 进度表长期挂一个不做的包 | 撤包或正式延后 | ✅ **已完成**（`0917AC` 撤包，答 `3a`） |

## 五、本轮（2026-09-17 午）已发车

| 泳道 | 编号 | 做什么 | 环境 |
|---|---|---|---|
| 收尾落档（串行） | `0917T` → `0917U`✅ → `0917V` | T：提交本文与 Cowork 未提交文档；U：回件包 5.5 落档＋`openspec-archive-change`（✅ 已完成：33/33，已归档，全量 pytest 2466 passed/0 failed）；V：值守包 8.6/8.7 按实测证据核证勾选、8.8 观察窗开窗登记（到期 09-24） | 主工作区 |
| 附件归档（G1） | `0917W` | 真实文件帧字段实测 → 附件落归档 → 回件桥用附件路径 | worktree，Opus |
| 提交请求通道（G2） | `0917X` | 新提交请求脚本＋安装器＋测试（不装） | worktree，Opus |

触碰区：T/U/V 只动文档与 `openspec/changes/{liaison-reply-bridge-and-patrol,hr-wecom-aibot-liaison}`；W 只动 `tools/liaison/`（不含 `unpack/dispatch.py`）与其测试；X 只动新脚本、`scripts/`、`tests/` 新文件。三条泳道零重叠，并行。

## 六、Shao Peishen 裁决（2026-09-17 12:3x 答 `1a，2a，3a，4a`）

1. **G3 ⇒ 做**：泳道批次收敛后经值守通道**私信 Shao Peishen 本人**一条摘要，⛔ 不进群
2. **G4 ⇒ 先做 Mac 侧调度**：服务跟进信超期提醒、观察窗到期提醒；`.51` 侧 M1 挂起提醒后续随发版
3. **G7 ⇒ 撤包** `lane-launch-armed-scan`（请求文件发车路已够用）
4. **M2 ⇒ Cowork 起草需求树**（grill 题），Shao Peishen 逐题答后立包：`docs/roadmap/M2-需求树草稿.md`

排期：G3→G4 同触碰 `tools/liaison/__main__.py`，与 `0917W`（附件）串行，**等本批（`0917T`–`X`）收敛后**下一批发车（launchd 发车器不允许两批并发）；G7 撤包为纯文档，同批并行。

## 七、优先级调整（Shao Peishen 2026-09-17 21:3x）

- **人事部可见优先**：让人事部尽快看到新东西以提高积极性。调度器排序在同级内优先派「人事部在 `.51` 页面上能看到/用到」的条目：① M1 收尾（Q-22/23 已放行 → plan/build → 9.1 画像质量验收 → 试点）② M2 提前切出「可见薄片」：U1 数据模型 → U2 上传与解析 → U5 工作台的上传＋解析结果＋校对展示（只收脱敏／历史离职样本，真实简历入库闸保持关闭），排在 U3 硬门槛、U4 召回精排之前。薄片的 design／tasks 重排须过 G2。
- **LLM 只用 DeepSeek 起步**：近期 M2 全程只用 DeepSeek（抽取＝deepseek-flash 已定型），火山方舟／阿里百炼以后再补，⛔ 不阻塞主航道；tasks 1.4「≥3 家」改为「DeepSeek 两款先行，第 3／4 家后补（非阻塞）」。
