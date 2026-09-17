# task-dispatcher · 阶段推进规则（R4）

> 2026-09-17 `[Mac]0917AL` 按 `docs/roadmap/任务驱动workflow设计.md` §四 R4 写定；被 `task-dispatcher` skill（`0917AM` 段）引用。
> 真源只此一份：改规则改本文件，⛔ 不要在 SKILL.md 里复述一遍。
> 输入 = `docs/roadmap/任务台账.yaml`（R3，由 `scripts/dispatcher_backlog.py` 合并生成）＋ 仓库真身；
> 输出 = 泳道 opener（走 `lane-dispatch` 规则）＋ 台账状态更新 ＋ `docs/roadmap/定夺队列.md`（R5）追加。

## 0. 三条总则

1. **台账状态是机器判据，不是记录。** 调度器写「完成」＝对下游宣布这件事已发生；写早了会让该发生的事再也不发生（09-09 跟进信教训）。每个「完成」都必须有本文 §2 的真身判据支撑。
2. **断点只有两种**：需 Shao Peishen 决策、等外部输入。两者都写进定夺队列然后**继续发别的**；⛔ 不输出问句、不停在原地。其余歧义取保守方向（留一条待办，不把没做的事标成做完）。
3. **⛔ 不整读台账**（> 40 KB）。用 `python3 scripts/dispatcher_backlog.py --show ready|blocked|running|conflicts`，或 `python3 -c` 按 id 取。先 `python3 scripts/dispatcher_backlog.py` 重新合并一次（真身变了台账才跟上），`conflicts` 非空的条目逐条核真身后再决定。

## 1. ready 判据（四条全中才 ready）

| # | 判据 | 怎么查 |
|---|---|---|
| ① | **依赖全完成**：`依赖` 里每个 id 在台账里状态＝完成；依赖 id 不在台账（`summary.未知依赖`）⇒ 不 ready | `compute_ready` 已实现；未知依赖须先核真身补状态 |
| ② | **非阻塞**：`状态＝待开` 且 `阻塞类型＝无` | 同上 |
| ③ | **触碰区不与在跑泳道重叠**：本条 `触碰区` ∩（所有 `状态＝在跑` 条目的 `触碰区` ∪ 当前 `.claude/handoff/lanes-*/manifest.tsv` 未收敛批次里各块自报触碰区）＝ ∅。触碰区为空的条目视为「未知」，按重叠处理（串行）——⛔ 不凭「感觉像独立的」并行 | 在跑集合 = `--show running` ＋ `ls .claude/handoff/lanes-*/results.tsv` 缺失的批次 |
| ④ | **不属不可代项**：合规红线七条、候选人淘汰规则例外、候选人对外通道开关（一次性邀请链接、拒信／邀约、跟进信真发）、真实简历数据处理范围、`.51` 发版决定、预算与外部采购。文本命中 🔴／不可代／Shao Peishen／待裁决 的条目生成时已判「决策」；调度器再按本条复核一遍标题与产出判据 | 命中 ⇒ 状态改「阻塞／决策」并进定夺队列 |

**聚合条目永不 ready**：`<change>/U<n>`（单元）、`change:<name>`（变更包）、`scene:<M>`（场景）只在其依赖全完成时被判「完成」，⛔ 不为它们发车。调度单元是 `plan:<stem>/seg<k>`（拆段）、`<change>/U<n>/plan`（spec-to-plan）、`gate` 条目、`relay:*`／`opener:*`。

## 2. 阶段推进（每个阶段的完成判据取自真身 → 下一阶段任务）

| 阶段 | 完成判据（真身） | 完成 ⇒ 生成的下一任务 |
|---|---|---|
| **intent** | `docs/roadmap/intents/<场景>.md` 存在，且末尾「待答题」节为空或全部有答 | `propose`：`openspec-propose`（无头可跑，Sonnet） |
| **propose** | `openspec/changes/<pkg>/` 齐 proposal／specs／design／tasks；`openspec validate <pkg> --strict` 通过；`design.md` Open Questions 为 0 条（有 ⇒ 每条进定夺队列，阶段停在 propose） | `plan`：按 tasks.md 章节逐单元生成 `<pkg>/U<n>/plan`（`spec-to-plan`，CC，worktree ❌，Sonnet）。⛔ 前置门槛章（`## 0.`）不出 plan |
| **plan** | `docs/superpowers/plans/` 下存在文件名含 `unit<n>` 且含 `<pkg>`（或其首段前缀，如 `m2-unit0`）的 plan，`grep -c '^### Task '` ≥ 1，且含 Global Constraints 段 | `build`：Task < 5 ⇒ 一条 run-build；Task ≥ 5 ⇒ 按 plan「建议拆段点」拆成 `seg1..k` 串行（无拆段节 ⇒ 每 3 Task 一段），每段一条 worktree 泳道，段间交接只靠上一段的 commit hash |
| **build（拆段）** | 该段分支已合 main：`git cherry -v main <分支> \| grep -c '^+'` ＝ 0（⛔ 不只看 `rev-list`，main 被 rebase 过会假阳性）；对应 tasks.md 条目已勾 | 下一段；末段完成 ⇒ **merge** 判据 |
| **merge（单元合 main）** | 该单元全部拆段合 main ＋ 章节 checkbox 全勾（`grep -c '^- \[ \]'` 在该章为 0，墓碑 `~~` 不计） | 下一单元的 `plan`（同包按章节先后）；全部单元合 main ⇒ **release** 定夺 |
| **release** | 🔴 不可代：定夺队列一条「是否发版 `.51`」（`Q-F3` 型），答「发」后由动作通道 `deploy-51`（R6，后续）执行；发版记录落 `docs/deploy-51-server.md` | **acceptance**：起草业务验收跟进信（`docs/跟进信/`，`send-followup --dry-run`），进定夺队列「待审发」——⛔ 真发属对外通道，不可代 |
| **acceptance** | 跟进信台账 `README-跟进信清单.md` 该行 `✅ 已推送` ⇒ 等回件（外部输入，登记「谁提供／等什么／期限」）；回件到达 ⇒ 拆件会话自动回灌（已有环，不归本调度器） | 拆件结论：口径点 ⇒ 定夺队列「口径签认」；新需求 ⇒ §4 规则 ② |
| **archive** | `tasks.md` 全勾（墓碑不计）⇒ **当场** `openspec-archive-change`，⛔ 不得跨越一个工作 session（CLAUDE.md 归档时限）；`openspec/changes/<pkg>/` 移入 `archive/`、delta spec 已同步 | 场景条目 `scene:<M>` 判完成（该场景所有包归档） |
| **gate**（前置门槛章 `## 0.`） | 条目文本「判据：…」子句成立（如 TD 销账、文档落位并签认、`intent.md` 末尾补行） | 解除其 `阻塞 U<n>` 指向单元的依赖 |
| **gap**（路线图缺口） | 路线图 §四 状态列 ✅ | — |

**依赖不完成 ⇒ 不推进**；推进只在「完成判据成立」那一刻由调度器写台账状态，⛔ 不用泳道自报的 `OPENER_DONE` 代替真身判据（`OPENER_DONE` 只说明会话跑完，合没合 main 要查 cherry）。

## 3. 能开尽开的上限与排序

- **max-parallel 3**：同时在跑的泳道（含正在收敛的批次）≤ 3；**单批 ≤ 6 条**块（含串行段），超出留到下一事件。
- **排序**：场景优先级 M0 > M1 > M2 > M3（M0′ 归 M0；「构建自动化」条目与 M0 同级——它们决定所有场景的吞吐）；同场景按阶段先后（gate → plan → build 段号 → merge → archive）；同阶段按 id 字典序。`compute_ready` 已按此排序输出。
- **触碰区重叠即同泳道串行、零重叠才跨泳道并行**（`lane-dispatch` ② 规则原文）。历来最热：`app/agents/intake_agent.py`、`app/graph/nodes.py`、`tools/liaison/__main__.py`、`docs/session接力.md`、`docs/openers/号池台账.md`——碰它们的一律串行。
- **发车器被占**（`lane-launcher.sh` 拒绝）⇒ 写入 `launch/queue/`（R1，`0917AK`），⛔ 不重试轰炸、不等人。
- **模型**：泳道默认 Sonnet；只有 propose／design／需求收敛、疑难状态机或并发调试的 opener 才标 `｜ 模型: Opus`（CLAUDE.md 模型分级）。

## 4. 新场景发现（intent 的前一棒）

三个来源各一条规则，产物都是**定夺队列一条「是否启动场景 X 的需求收敛（grill）」**（阻塞类型＝决策），⛔ 调度器不替他启动：

| # | 来源 | 触发判据 | 动作 |
|---|---|---|---|
| ① | 路线图 `docs/roadmap/HR项目实施路线图与构建自动化流程.md` §二 | 某场景状态列为「未启动」（台账 `scene:<M>` 状态＝待开）且**前置场景已达启动条件**：M2 各单元依次（上一单元合 main ⇒ 下一单元 plan，见 §2）；M3 在 M2 上线后（`scene:M2` 完成或 M2 `release` 已答「发」）；「明确不在 MVP」清单（`02-系统架构与MVP范围.md`）在主链路验证后（M1 9.1 验收通过 ＋ M2 8.10 试运行落档） | 定夺队列追加 `Q-F<n>`「是否启动场景 X 的需求收敛（grill）」，来源写路线图行号；已有同题条目（含远期节）⇒ 不重复追加，只把它从「远期」移入「待答」 |
| ② | 拆件会话回灌（`docs/跟进信/回件/*.md`、`docs/跟进信/口径点台账.md`） | 回灌结论判为「新需求」，或口径点提出**新能力**（不是对既有 spec 的口径修正） | 同上，并在「来源」附回件文件路径与 msgid；口径修正 ⇒ 走「口径签认」定夺，不算新场景 |
| ③ | Shao Peishen 在本线（Cowork）直接提出 | 本线出现「我想要／能不能做／加一个…」且不属既有包范围 | Cowork 当场写 intent 草稿进 `docs/roadmap/intents/<场景短名>.md`（结构同 `M2-intent.md`：背景／目标／不做／待答题），并追加定夺队列「是否启动」；⛔ 不直接 propose |

**启动获批**（定夺队列该条「已答」且答复为启动）⇒ 调度器生成 grill 任务：台账新增 `intent:<场景>`（阶段 intent，阻塞类型＝决策——grill 要他逐题答，由 `requirement-grill` skill（`0917AN`）**在本线执行**，不进无头泳道）⇒ intent 落档且待答题清零 ⇒ §2 `intent` 完成 ⇒ propose。

## 5. 定夺队列的写法契约（调度器只追加）

- 文件 `docs/roadmap/定夺队列.md`「一、待答」表，列：编号／场景／阻塞类型／问题／选项与代价／推荐／来源／阻塞的任务 id／状态／答复。编号 `Q-<两位递增>`；远期（前置未齐）进「二、远期定夺」用 `Q-F<n>`。
- 每条**必须可直接作答**：`(a)/(b)` 各写选它会发生什么与代价；有推荐给推荐，属「须他明确答复」（哪个岗位仍在招、生产窗口、真实数据来源）写「无默认」。⛔ 不写「请看情况」。
- **阻塞类型**：`决策` ＝ 只有 Shao Peishen 能答；`外部输入` ＝ 写清「谁提供／等什么／期限」，到期未到 ⇒ 调度器追加「超期」备注并起草催办稿进待审（⛔ 不代发）。
- 答复落档（Cowork 填「答复」列、状态改「已答」）⇒ 提交通道提交 ⇒ 事件唤醒调度器 ⇒ 调度器把「阻塞的任务 id」对应条目 `阻塞类型` 改 `无`、`状态` 改 `待开`（或按答复改「作废」⇒ 台账条目状态改完成并加备注「作废：Q-xx」）。
- 同一问题⛔ 不重复入队：追加前 `grep -n "<任务 id>" docs/roadmap/定夺队列.md`。
