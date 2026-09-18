# 任务驱动的端到端 workflow · 缺口与机制设计

> 2026-09-17 15:3x，Cowork·HR业务线-接力0917D，按 Shao Peishen 15:2x 口径编写：
> 「有任务可做就不应需要人守决定下一步；断点只有两种——需我决策、等外部信息；其余每个场景从 intent 到发布自动循环；能开尽开；瓶颈只应是我或 AI 专员的人力。」

## 一、目标形态

```
                ┌──────── 定夺队列（需 Shao Peishen）◄──────────────┐
                │                                                  │
场景 intent → grill → propose → plan → build(拆段) → 合 main → 发布(.51) → 业务验收(跟进信) → 回件 → 拆件 → 口径/新需求 ─┐
    ▲                                                                                                               │
    └─────────────────────────────────────── 调度器（事件触发）读任务台账，挑「无前置阻碍」任务能开尽开 ◄──────────────┘
```

每一段完成都产生**事件**；调度器被事件唤醒，读**任务台账**与仓库真身，把所有 ready 任务编排发车；遇到只有人能给的东西，写进**定夺队列**然后继续发别的。

## 二、现状对照：已有的环 vs 缺的环

| 环 | 现状 | 缺 |
|---|---|---|
| 发车/执行/合入 | ✅ launchd 请求文件 → run-lanes → worktree 泳道 → ff 合 main | 发车器被占时**拒绝**而非排队（R1） |
| 回件 → 拆件 → 台账/口径 | ✅ 事件驱动（09-17 两次真实跑通） | — |
| 发信、重启服务、提交文档 | ✅ 动作通道 / 提交通道（Cowork 直驱） | 动作通道缺测试（R1 同批补） |
| **决定下一步做什么** | ❌ 靠 Cowork 被人或定时器唤醒后编排 | **R2 调度器**（事件唤醒的无头会话）＋ **R3 任务台账**（机器可读） |
| **阶段自动推进** | ❌ propose 完 → 谁派 plan？plan 完 → 谁拆段派 build？合 main → 谁起草验收信？ | **R4 阶段推进规则**（写进调度器：每个阶段的完成判据 → 下一阶段任务） |
| **需人决策的停点** | 🟡 散在聊天与接力文件 | **R5 定夺队列**：一个文件；调度器只追加；Shao Peishen 在本线时 Cowork 先报；答复落档即产生事件 |
| 等外部信息 | 🟡 散在 TD 与接力 | 并入 R5（类型＝外部输入，谁提供、等什么） |
| 发布 `.51` | 🔴 人工 | 保持决策停点：进 R5 待「发」；「发」后由动作通道 `deploy-51` 执行（R6，后续） |

⇒ **还缺 5 环（R1–R5）才能自转，R6 是发布段的自动执行，排在其后。**

## 三、对口径的两处修正

1. **「在环时在本线提醒」只能在你打开本线时成立**：Cowork 会话不能被 Mac 上的事件唤醒去主动推送到聊天里。所以机制是——定夺项先落**定夺队列文件**，你一回到本线，我第一件事就报队列。私信口径（15:4x 定）：泳道批次**全部 OK 不私信、有 PARTIAL/FAIL 等异常才提醒**（已直改 `owner_notify.py`，待泳道甲补测试）；拆件完成**不私信**。
2. **「编排下一批」不能再由 Cowork 承担**：Cowork 只在有人说话时活着，靠它编排就必然需要人守或定时器。编排必须下沉到 Mac 上被事件唤醒的**调度器**；Cowork 退回「人机界面」：报定夺、收答复、改口径。

## 四、机制细节

- **R1 发车排队**：`lane-launcher.sh` 遇占用 ⇒ 请求移入 `launch/queue/`；`run-lanes.sh` 收敛末尾写 `.claude/handoff/events/lanes-done-<批次>`；事件监听器出队下一条。
- **受限会话投递路径**（0918H）：`.claude/**` 对远端文件工具只读（`device_commit_files` 直接拒），提交/发车/事件三条通道的投递口全在 `.claude/` 下 ⇒ 没有本机 shell 的受限会话一件也投不进来。破法：投递口搬到仓库根 `handoff-inbox/`（受限会话能写），`com.zhuopin.hr.handoff-relay`（launchd，WatchPaths `handoff-inbox/` ＋ 300 秒兜底）跑 `scripts/handoff_relay.py` 按文件名前缀分派到真正的通道目录、去掉前缀、沿用既有命名：
  - `commit-<ts>[-来源].request`／`.action` → `.claude/handoff/commit/`（`.request` 的 `paths` 复用 `commit_request.validate_path` 同一份白名单）
  - `launch-<ts>[-来源].request` → `.claude/handoff/launch/`（整行须精确匹配 `^--full-auto --yes --only [0-9A-Za-z]+(,[0-9A-Za-z]+)*$`）
  - `event-<事件名>` → `.claude/handoff/events/`（须 0 字节）
  五条校验（文件名、mtime<30s 跳过防半截、以上三条内容闸）任一不过 ⇒ 移入 `handoff-inbox/rejected/`、`relay.log` 追加一行。**只搬运不放行**：不解析 G1–G5、不改台账／定夺队列状态。
- **R2 调度器**：`.claude/handoff/events/` 下出现事件文件 ⇒ launchd 起一个无头 CC 会话（Sonnet，单实例锁，预算上限）执行 skill `task-dispatcher`：读 R3 台账＋仓库真身 → 计算 ready 集（前置已合 main、非决策/外部阻塞、触碰区不与在跑泳道重叠）→ 按 lane-dispatch 规则写块、登记号池、dry-run、写 launch request → 更新台账状态 → 需人的写 R5。事件来源：泳道批次收敛、提交通道完成一次「定夺答复」提交、拆件会话收口、每日一次日历兜底（09:00，防事件丢失）。
- **R3 任务台账** `docs/roadmap/任务台账.yaml`：每条 `id / 场景 / 阶段 / 依赖 / 触碰区 / 状态(待开|在跑|完成|阻塞) / 阻塞类型(决策|外部|无) / 产出判据`；初始由调度器从路线图＋各 openspec tasks.md＋plans 生成，之后以台账为准、真身核对。
- **R4 阶段推进规则**（调度器内置）：intent 有且无未答题 ⇒ **G1 定夺「intent 定稿」**，答复后 ⇒ propose；包 validate 过且无 Open Questions ⇒ **G2 定夺「design／spec 定稿」**，答复后 ⇒ spec-to-plan（逐单元）；plan Task≥5 ⇒ 拆段 run-build；单元合 main ⇒ 下一单元；全部单元合 main ⇒ 发布定夺（R5）；发布后 ⇒ 起草验收跟进信（待你审进 R5）；回件拆件 ⇒ 口径签认定夺（R5）或新需求 ⇒ intent。
- **R5 定夺队列** `docs/roadmap/定夺队列.md`：`编号 / 场景 / 问题 / 选项与代价 / 推荐 / 来源 / 阻塞了哪些任务`；Cowork 在本线收到答复 ⇒ 写回并经提交通道提交 ⇒ 触发 R2。

## 五、执行

一条批次「任务驱动自转」：泳道甲 R1＋动作通道 TDD（`scripts/`、`docs/openers/lane-launcher.sh`、事件监听器）；泳道乙 R2–R5（新 skill `task-dispatcher`、`docs/roadmap/任务台账.yaml` 初始生成、`定夺队列.md`、调度器 LaunchAgent 安装器——安装经动作通道 `install-agent` 执行，不需要人开终端）。改 `run-lanes.sh` 只允许在收敛末尾加一行写事件文件，超出即停。

## 在环闸门（Shao Peishen 2026-09-17 17:0x 定，⛔ 调度器与任何无头会话不可越过）

自动化只推进两个闸门之间的活；到闸门一律写定夺队列并停在该场景（其它场景照常能开尽开）。放行判据只认 `docs/roadmap/定夺队列.md` 对应行「状态＝已答」且答复为放行字样，⛔ 不认聊天记忆、不认台账状态自改。

| 闸 | 何时到闸 | 放行字样 | 放行后 |
|---|---|---|---|
| G1 intent 定稿 | grill 产出 `docs/roadmap/intents/<场景>-intent.md` 且无未答题 | 「定」（或逐条改后「定」） | propose |
| G2 design／spec 定稿 | 包 `openspec validate` 过、无 Open Questions | 「定」 | spec-to-plan → build |
| G3 发布 | 场景全部单元合 main | 「发」 | 动作通道 `deploy-51`（后续）或无头 `.51` 发版 |
| G4 发信 | 跟进信 md＋docx 起草完、自检过 | 先「审核通过」再「发」（可同条答复） | Cowork 经动作通道 `send-followup`；⛔ 调度器永不调用发信动作 |
| G5 口径签认 | 回件拆件判为口径点 | 「签」 | 口径点台账转已签认 |

intent／design／spec 在闸前允许无头会话起草与修订；「定稿」本身（状态字样、归档、进下一阶段）只在放行后发生。
