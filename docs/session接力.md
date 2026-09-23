# Session 接力 · HR 招聘智能体

> 滚动更新，覆盖旧版。新会话读完本文即可接上。
> 最后更新：2026-09-23 10:2x（Cowork·`0923R` 续棒看护实核）／上一版 2026-09-20 15:4x（Cowork·`0920R` 续棒看护实核）／上上版 2026-09-19 21:5x（Cowork·0918AO 巡检实核：0918V 已收口、M2·U3 建造完成待发布闸 Q-49、M3 U4 已合 main 进度 36/76）｜ 上一次：2026-09-18 20:4x（Cowork·0918G 换道前实核：M2 可见薄片已上线 .51、投递中继上线、无头块铁律立规）｜ 上上次：2026-09-17 06:2x（Cowork·0917D：R-1 实核已完成；体积闸归档接力 61→41 KB、编排 65→12 KB；R-2 拆成 `0917G`→`0917H` 经 launchd 发车（首发 E/F 作废）。此前：Cowork·HR业务线-接力0917D 转场：把 `0916E`–`0917B` 这一大段
> **从未进过本文**的进展补齐——第十七批波次 1＋2 已基本完成、Token 治理线已结项；
> 订正 `tasks.md` 抬头进度行失真 24/33 → 27/33）

---

## 🔴 新 session 先看这一节（2026-09-23 10:2x，Cowork·`0923R` 续棒看护实核）

⚠️ 本节之后的历史节一律以本节为准。推进依据＝任务驱动：`docs/roadmap/任务驱动上线路线图.md`。

### 一、09-23 已落地

1. **`0921E`／`0921E续1`／`0921F` 三条「自报 DONE 但 main 上全空」的旧账已清**：合并重做为 `0923A`，机判 7 条含 3 条新测试全过（`tests/test_attachments_unknown_frame_dump.py`），已确认 ff 合回 `main`（commit `48f346f`，`origin/main` 与本机 `main` 一致），并在主工作区复跑核实——不是只信自报。
2. **附件只走私信口径已生效**：`tools/liaison/frames.py`／`__main__.py::handle_message_frame` 群帧附件一律忽略；单聊（私信）帧才会尝试解析。⚠️ **`ATTACHMENT_FIELD_PATHS_BY_MSGTYPE` 仍故意留空**（fail-closed）——私信帧到达时只落一份结构取证（键路径＋类型名，⛔ 无取值）到 `unknown_attachment_log_dir`，文件本体尚未真正能收。需汤丽萍下次真的私信发一次文件，取证帧到手后再派一条泳道填表，才能真正收到文件内容。已就此口径当面答复过 Shao Peishen（本场对话）。
3. **决策点 c 漏收诊断仍留步**：`人事部#2` 09-20 17:04 那条 `@MAC机器人` 决策点c文本为何未进归档，本机与本 worktree 均无 `data/liaison/logs`，需 `.51` 上的真实入站日志才能排除四个候选成因，⏸ 待具备 `.51` 只读权限的泳道或 Shao Peishen 现场核对。
4. **`0923B`（拆 `人事部#3` 四条 pending）结果 PARTIAL**：4 条全部复核，均不满足拆件章程完整性判据（决策点b缺时数/人名；信件正文A/B/C/D场景仍完全未答），全部折进同一条 `Q-59` 待人，**未新解锁任何一条**，`data/liaison/unpack-signal.json` 里 4 条 pending 数量未变。
5. 已给 Shao Peishen 一份可直接发送的微信私信草稿（发给汤丽萍）：① 请她改走**私信**（非群）重发 `10个岗位需求.doc`；② 决策点b补投入小时数/牵头人姓名；③ 请回信正文 A/B/C/D 场景与路线图——尚未确认他是否已发出。
6. 清理：已删除本场为 `0923A/0923B` 设的一次性回查提醒（`trig_01H1Bf5nvB9qnvL2jxwBmEQo`），因已在本场手工核实完毕，避免重复报告。
7. **`0923R2`（续棒二次核实）**：`queue_pending`／`dispatcher_backlog --dry-run`／最新 `lanes-20260923-094859/results.tsv` 三项与本节结论**逐项核对一致**，本轮未新跑任何泳道。`ready` 集合较写本节时新增 `interview-scheduling/0.2`（共 4 条），已用 `grep 任务台账.yaml` 核实其「阶段」仍为 `gate`——不是可发车项。未新增开口项，未新增决策项。
8. **`Q-59` 已答（Shao Peishen `0923R2` 回 `1a`）**：`人事部#3` 两条部分命中回件（决策点a清单无实体、决策点b缺时数/姓名）均判**不完整**，退回汤丽萍补齐，`人事部#3` 维持整信待齐 A/B/C/D 场景与决策点a/b/c 全部到齐后一次性转闭环——已回填 `docs/roadmap/定夺队列.md` `Q-59` 行状态与答复列。下一步：需一条跟进信泳道向汤丽萍催补（决策点a清单实体＋决策点b时数/姓名），本场未派，留给调度器或下次机制类泳道。

### 二、开口项（截至 `0923R`）

- 台账 `ready` 三条 `offer-generation/0.2`、`offer-generation/0.5`、`onboarding-flow/0.2` **阶段全是 `gate`**（等上游交付），不是可发车。
- 待外部输入、挂起不追问：`Q-01`（附件取证已就位，等汤丽萍真实私信一次文件）、`Q-04`（汤丽萍回件）、`Q-08`、`Q-10`、`Q-16`、`Q-52`（回 LAN 取客户端侧一手证据）、`Q-59`（`人事部#3` 四条 pending，正文完全未答）。
- 🔴 须本人：`Q-53` 语音主机采购与预算（阻塞 M3 `5.11` live e2e）、`Q-F1`～`Q-F4`、决策点c的 `.51` 日志核对、微信草稿是否已发。

### 三、下一步（新 session 直接接着做）

1. **本轮无可发车项**：`ready` 三条全是闸。
2. 若汤丽萍已私信发过文件：先看 `unknown-attachment-frames/` 下是否已落新取证文件，有则派一条泳道据真实结构填 `ATTACHMENT_FIELD_PATHS_BY_MSGTYPE`。
3. 若他已在 `.51` 上核对过决策点c日志：把结论写回 `Q-01` 并把该项状态由「留步」改「已诊断」。
4. 其余按调度器自转（daily 09:00 兜底＋批次收敛事件唤醒）。
5. 续棒口令：新会话打一行 `[Mac]MMDDR 续棒泳道看护`，本文件是接手第一读件。

---

## 开场词（复制即用）

```
[Mac]0918AO-HR业务线接力
【设置】执行环境: Cowork ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（Cowork 无 worktree，只做文档、编排与派单）｜ 审批模式: 逐项询问 ｜ 工作区: 仓库根（/Users/paulshao/Projects/HumanResource）
读 /Users/paulshao/Projects/HumanResource/docs/session接力.md 的「🔴 新 session 先看这一节（2026-09-19 21:5x）」恢复上下文，然后按【四、下一步】继续。
```

> 🔴 **Cowork 接力开场词同样是 opener，头两行＝标题行 + 【设置】行**（CLAUDE.md「CC 与 Cowork 两端同等适用」；09-09 Shao Peishen 指出漏了一次）。本 session 派出去的 opener，`派发` 一律写 `Cowork·HR业务线-接力0909Q`。

> 🔒 **首行只用于给读文档的人对编号，不指望侧边栏**。Cowork 侧的 session 名是摘要生成的，
> 首行无效（08-27 实测：`HR业务线-接力0827B` → 侧边栏 `HR业务线接力`）。⛔ 不要再改首行格式硬试。
>
> 🔴 **出号前必查号池台账**：`docs/openers/OP-0820-全量编排.md` 顶部「🔢 号池台账」。
> **Cowork 与 CC 共用同一号池**，`Z` 固定留给看护者。08-27 一天撞号 5 次，根因是
> 提交类 opener 只在聊天里派、从不落档 ⇒ 下轮 grep 不到 ⇒ 重派。**给出后当场登记**。

---

## 二、下一步

### ✅ 2026-09-20 `0920K` 机器判据闸已上 main（`6b0cdcb`，ff-only）——泳道自报 `OPENER_DONE` 不再算数

今天两次命中同一形状（`0920H`：上下文守卫提醒型无消费者；`0920I`：完成判据提醒型无消费者），本条给完成判据装上消费者。Opus、7 min、worktree `lane-0920k-gate`。

- **取判据**：按条目 id glob `docs/openers/<id>-*.md`，匹配数 ≠ 1 或无 `## 机器判据` 节或无 ` ```bash ` 块 ⇒ **维持现状不拦**（向后兼容是硬要求：拦错一条会停整条泳道，代价远大于漏拦）
- **安全预检先于执行**：块内命中 `git push`／`git commit`／`git reset`／`git clean`／`rm -rf`／`sudo`／`curl`／`wget`／`ssh`／`scp`／`launchctl`／`mkfs` 等 ⇒ **不执行**，记 `GATE-UNSAFE`。判据块的本分是只读核验
- **执行**：写 `LOGDIR/gate-<id>.sh`，在**仓库根**跑（worktree 条收口时已 ff-only 合回并清 worktree，验的是合入后的主工作区），`timeout 60`。退出码 0 ⇒ 状态维持；非 0 或超时 ⇒ `GATE-FAIL`，判据输出末 30 行追进该条日志
- **`GATE-FAIL`／`GATE-UNSAFE` ＝ 与 `FAIL` 同处置**：⛔ 不 `mark_done`、**停本泳道**（后续条会跑在未验收的产出上）
- 收工汇总打 `🚦 机器判据：N 条过 / M 条不过（<id 列表>）`；一条都没有时打 `🚦 本批无机器判据块`。`results.tsv` 仍 **13 列**，⛔ 未加第 14 列
- `.claude/skills/kickoff/SKILL.md` 补了写法节（27,363 B，仍 ≤29 KB 未借机扩写）：新写的 opener 一律要有 `## 机器判据` 节，块以退出码为准、只读无副作用
- 测试 `tests/test_run_lanes_gate.py` 五例（exit0 维持／exit1 GATE-FAIL 且停泳道且未 mark_done／无该节不拦／黑名单命中且**判据块未被执行**／超时 GATE-FAIL），假 opener＋假日志，⛔ 未起真泳道
- **自证**：`0920K` 自己的 §五 判据块由泳道跑过 rc=0，Cowork·0918AO 另行独立复跑一遍同样 7/7 全过
- ⚠️ **本批的 run-lanes 实例是旧拷贝，闸从下一批起生效**

### 📌 2026-09-20 今日机制线小结（`0920D`–`0920K`，九条全部上 main）

`0920D` 建 `lane-watch-relay`（续棒免粘贴 Opener，`[Mac]MMDDR` 一行带出标题与触发词）｜`0920E` `kickoff` 瘦身 35,616→26,368 B，成因段拆进 `docs/kickoff-由来.md`（PARTIAL，剩余为受保护的判据/模板，Shao Peishen 答「认 26.4 KB 为新基线」）｜`0920F` 接力归档 57,835→44,587 B｜`0920G` `results.tsv` 加第 13 列 `upeak`｜`0920H` 上下文硬闸＋自动续棒｜`0920I` 看护技能补续棒状态（**判据未全过却打了 DONE**，即 `0920K` 的起因）｜`0920J` 补 `0920I` 遗漏两处｜`0920K` 机器判据闸。

**今日实测 `upeak`**：`0920H` 125,236 ／ `0920K` 112,695 ／ `0920J` 61,190 ／ `0920I` 52,117——**均未越 150k**。`0920E`/`0920F`/`0920G` 跑在旧脚本上无记录。⇒ 现阶段只能说「有数的四条都没越线」，⛔ 不能说本项目没有 Win 端那个病；下一批起全部有数。


### 🔴 2026-09-20 `0920I` 在判据未全过的情况下打了 `OPENER_DONE`——「完成判据也是提醒型的，没有消费者」

`0920I`（补看护技能续棒状态）报 `OK`／`OPENER_DONE` 并已合 main，`§4` 两行确实加对了；但它自己 opener 写的**四条完成判据里有一条实测为 0**：判据 2「`grep -c 'upeak' SKILL.md` ≥ 1」⇒ 实测 **0**，`§2` 的「13 列／第 13 列 `upeak`」说明与 `§3` 的「越线点名」两处都没做。另有一处措辞与 `0920H` 实际实现不符（写「同泳道队尾」，实现是「本泳道下一条先跑续棒」）。

- ✅ `0920J` 已补齐并上 main（`af6435e`）：六条判据改写成**可直接粘贴执行的命令块**、要求逐条抄输出，实测 `upeak=2`／`同泳道队尾=0`／`本泳道下一条=1`／`CTX-RELAY=1`／`小节=6`／`6648 B ≤ 9216`，全过
- 🔴 **根因与今天治的那个病同族**：`0920H` 治的是「上下文守卫是提醒型、没有消费者」；这次暴露的是「**opener 的完成判据也是提醒型、没有消费者**」——判据只写在正文里靠泳道自觉核对，`run-lanes.sh` 只认哨兵字样，泳道自报 `OPENER_DONE` 就算数。`0920G`/`0920H` 没出事是因为它们的判据落成了 pytest（有消费者），`0920I` 的判据是几条 grep（没有消费者）
- 【谁做】待 Shao Peishen 定夺后派 opener｜【状态】待定夺｜【判据】opener 正文新增固定一节「## 机器判据」写成可执行 shell 块（每行一条、附期望值）；`run-lanes.sh` 收工时**自己跑一遍**该块，任一条不符 ⇒ 状态降为 `PARTIAL`（附不符项），泳道自报的 `OPENER_DONE` 不算数；无「## 机器判据」节的 opener 维持现状不拦｜【不做会怎样】每条 opener 的验收都建立在泳道自评上，`0920I` 这种「判据没过照样 DONE」会继续发生且不报错——与 Win 端七条全越线同一形状


### ✅ 2026-09-20 `0920H` 上下文硬闸＋自动续棒已上 main（`08d2564` A 半 ／ `2f8993e` B 半，ff-only 合入）

Shao Peishen 答「(a) 现在派整套」。两半一次做完，10 min、Opus、worktree `lane-0920h-ctx-relay`（隔离防自噬：本条改的 hook 正管着本条自己）。

- **A 半 触发**：`scripts/hooks/context-guard.py` 按 `hook_event_name` 分流。新增 `PostToolUse` 挂点，**仅 `HR_HEADLESS_LANE` 非空生效**，阈值 `HR_LANE_CONTEXT_LIMIT` 默认 150000，越线注入「把手上这一个里程碑做完并提交，然后顶格输出 `OPENER_PARTIAL: 上下文转场 | 续棒: 分支=… worktree=… 上一条commit=… 已完成=… 待续=…`，⛔ 不要开始新里程碑、⛔ 不许自己再起 session」，同 session 去重档位 `relay`。`UserPromptSubmit` 分支**逻辑原样**（`git diff` 该文件删除行数 0），有人值守路径零回归
- **B 半 消费者**：`run-lanes.sh` 认哨兵含「上下文转场」⇒ 状态 `CTX-RELAY`；`relay_fields()` 抓五字段、`relay_spawn()` 生成 `<原id>续<n>` 正文（沿用原【设置】行 ⇒ 同 worktree/分支）；`HR_LANE_RELAY_MAX` 默认 3，超限 `RELAY-EXHAUSTED` 与 FAIL 同处置（停本泳道）；续棒条跑成才摘**原条**泳道标注；汇总打 `🔁` 行；续棒条进 `results.tsv`、⛔ 不进号池台账
- **测试**：新增 `tests/test_context_guard.py`（6 例）＋ `tests/test_run_lanes_relay.py`（4 例）＝ 10 passed；回归 `test_context_guard_hook.py`＋`test_run_lanes_model.py` 23 passed；`test_doc_size_budget.py` 5 passed。假 transcript／假 claude 桩，⛔ 未起任何真泳道
- 🔴 **实现要点备查**：`PostToolUse` 的**纯 stdout 不进模型**（已在 claude 2.1.263 二进制核实「additionalContext shown to Claude」），注入必须走 JSON `hookSpecificOutput.additionalContext`。⛔ 以后写 PostToolUse hook 不要再用 print 直出
- **两处偏离 opener 字面，均保守处置并登记**：① 自核②要求 `results.tsv` NF=13，实测旧批次文件为 12——因该批次 13:20 起跑、`0920G` 13:32 才合入，必然由旧脚本写出；HEAD 的 `run-lanes.sh:693` 已打 13 列，判定 `0920G` 已生效未停工（本轮 `0920H` 自己那行 `upeak`＝**125,236**，新列已实测生效）② opener 写「同泳道队尾」，实现为「本泳道下一条先跑续棒」——泳道内串行正因触碰区重叠，字面队尾会让后一条跑在半成品上。**此偏离判为更正确，采纳**
- 【谁做】下一条机制类 opener｜【状态】待定夺｜【判据】`.claude/skills/lane-watch-relay/SKILL.md` §4 分流表补 `CTX-RELAY`（＝正常，续棒已自动排，⛔ 不当失败处理）与 `RELAY-EXHAUSTED`（＝已续 3 次仍越线，本泳道已停，须人判任务是否该拆）两个新状态｜【不做会怎样】续棒机制上线但看护技能不认这两个状态，下一场续棒时会把 `CTX-RELAY` 误当失败去重跑


### 🆕 2026-09-20 `0920F`/`0920G` 机制落档 ＋ 上下文守卫缺口结论（`0920G` 只做了可观测，硬闸待定夺）

Shao Peishen 2026-09-20 二次带来姐妹项目（Win 端）实况：七条无头泳道上下文峰值 151k–244k，**全部越过 150k 转场线、无一条按规则收尾**；根因判为「守卫是提醒型、没有消费者」，治法是改成有消费者的硬闸（越线截断 ＋ 按 `OPENER_PARTIAL: 上下文转场` 记账 ＋ 自动排续棒）。要求本项目一并杜绝。

**本项目核对结论——同族但更隐蔽：我们不是「越线不收尾」，是「越没越线都不知道」**：

- `results.tsv` 原 12 列（lane／id／status／mins／log／model／cost／in／out／cache_read／cache_write／turns）**没有上下文峰值**。今天 `0920E` 那行的 129,144 是 `cache_write`，不是峰值——峰值从来没记过
- `scripts/hooks/context-guard.py` 挂在 `UserPromptSubmit`。无头泳道一辈子只有开场那一条 user prompt（彼时上下文≈0）⇒ **运行中永远不可能触发**；且函数开头写死 `if HR_HEADLESS_LANE: return 0`，是 0916K 的**显式豁免**（当时 Win 端 #584/#585 泳道被 150k 提醒叫停、半途收尾）
- 已有的 `scripts/opener_split_check.py`（plan ≥5 Task 必须拆段）是**开工前静态拆分**，不是运行中越线截断，覆盖不到本缺口

处置分两步，本轮只做第一步：

- ✅ `0920F` 接力归档瘦身（commit 见 reflog）：57,835 B → **44,587 B**，5 节标【已闭环】搬进归档，守恒校验通过，保留区（「新 session 先看这一节」／09-19 及以后条目／含待派发·待发车·待答·留步字样的条目）未动
- ✅ `0920G` 上下文峰值入账（commit `fc575f5`）：`results.tsv` 加第 13 列 `upeak`＝该 session 所有 assistant 轮 `input+cache_creation+cache_read` 的最大值；五处 printf 同步补位；收工汇总打峰值，任一条 ≥150k 时多打一行 `⚠️ 本批有 N 条越过 150k 转场线（仅记账，未中止）`。**只记账、不改行为**
- 【谁做】待 Shao Peishen 定夺后派 opener｜【状态】待定夺｜【判据】第二步硬闸＝`context-guard.py` 改挂 `PostToolUse`、去掉 `HR_HEADLESS_LANE` 豁免、越线注入「完成当前里程碑即以 `OPENER_PARTIAL: 上下文转场` 收尾」，**并同时**让 `run-lanes.sh` 识别该 PARTIAL 原因、自动把续棒条追加到同泳道队尾（交接复用 run-build 拆段那套：分支名／worktree 路径／上一条 commit hash 写文件，⛔ 不传对话）｜【不做会怎样】峰值可见但无人消费，等于 Win 端「提醒型守卫」的翻版，只是换了个位置
- ⚠️ 两件**不可拆**：只截断不自动续棒 ＝ 回到 0916K「半途收尾」的老路；只自动续棒不截断 ＝ 没有触发点

参考：本轮三条泳道的均摊上下文（`cache_read ÷ turns`）`0920E`≈108k／`0920F`≈97k／`0920G`≈71k——下一批起 `upeak` 列会给出真峰值，届时才能判我们到底越没越线。


### 🆕 2026-09-20 `0920E` kickoff 瘦身（派发中）＋ `Q-52` 转远期

Shao Peishen 2026-09-20 指出姐妹项目（Win 端）的 token 浪费形状：`zhuopin-lane-watch` 正本 44,445 B，成因段在正文反复展开，装载器又明写「读不到就停、不许凭摘要跑」⇒ 不能跳。本项目核对结论：**skill 正文不常驻上下文**（每轮只带 15 份 description 共 4,562 B，这块无病），病在**尖峰**——`.claude/skills/kickoff/SKILL.md` **35,616 B** 为本仓库最大单份，且 `CLAUDE.md` 三处指向它（强制格式模板／引用式 opener／编号与抬头）⇒ **凡出一份 opener 就整读一次**。次大 `requirement-grill` 21,897、`lane-dispatch` 20,345。开场四件：`CLAUDE.md` 23,296（唯一真·每轮常驻）／`session接力.md` 55,997／`号池台账.md` 42,264／`OP-0820` 22,755，后三件按规则 grep 或分段读，不整读。

- `0920E` 已派（泳道「机制落档」，Sonnet，worktree ❌）：成因/实证/历史归因段**原文整块**搬进新建 `docs/kickoff-由来.md`，正文各留一行指针，判据/模板/禁令/机器闸一律留正文。四条完成判据：体积 ≤15,360 B、守恒 `cat 两文件 | wc -c` ≥33,800 B（防删）、`🔴`/`⛔` 计数不得减少（搬走的须逐条列出供复核）、章节数仍为 16
- 已知未修的同形缺口：`lane-watch-relay` §4 真要发车时仍整读 `lane-dispatch` 20 KB（只在发车时付，本轮判为可接受，未动）
- `Q-52`（`0920A` 后「岗位列表」客户端侧一手证据）Shao Peishen 答「挂起到回 LAN」——本机探针实测 `.51:8095` 不可达（周末 off-LAN），已从「待答」移入「远期定夺」，前置＝回 LAN 在网＋普通浏览器一次刷新+截图，交 `zhuopin-lan-closeout` 收口。⛔ 不再催答


### 🆕 2026-09-20 `0920D` 续棒泳道看护机制落档——Shao Peishen 定两条口径，已建 skill 待提交

Shao Peishen 2026-09-20 定：**①本机只构建本地项目，GitHub 只是存储，⛔ 永不直接调 GitHub 上的 skill，⛔ 与其他项目（「企业AI转型」线）的 skill 无关**——只有他明确指示「参考 XX 项目做法」时才去只读参考，且只模仿、在本仓库自建实现。触发实证：本轮 Cowork 开场误取了「企业AI转型」线的 `zhuopin-lane-watch`（正本在 Win 端 `C:\Dev\zhuopin-ai\...`，本机不可及），白跑一轮才回到本项目 `lane-dispatch`。**②续棒免粘贴 Opener**：新会话只要说「续棒泳道看护」即接上，⛔ 不再出转场 Opener 让他手工粘；会话标题沿用 Opener 前缀形状 ＝ 端口（`[Mac]` 本机 Mac Studio／`[Win]` 他的笔记本）＋日期＋字母编号，**续棒固定 `R` 系列**，他开场打一行 `[Mac]MMDDR 续棒泳道看护` 即同时带出标题与触发词。

- 已建 `.claude/skills/lane-watch-relay/SKILL.md`（102 行）：§0 边界（只用本仓库资产／两侧仓库路径与 git 可碰性）→ §1 编号与标题（`R` 系列，Cowork 无 `set_session_title`，标题只能由他第一句话带出）→ §2 只读接手一次跑完的固定命令组（接力卡新节＋`queue_pending`＋`dispatcher_backlog --dry-run`＋`pgrep run-lanes`＋最新 `results.tsv`＋调度器 `.done`；⛔ 禁 cat 日志正文／全量队列／整目录列举）→ §3 两栏汇报格式 → §4 分流表（ready 全是 `gate 待开` ＝ 闸未满足、不是可发车）→ §5 落档与提交
- 【谁做】`0920D` 无头泳道（`commit_request` 白名单拒 `.claude/**`，只能走 CC 侧）｜【状态】待发车（等 Shao Peishen 一句授权）｜【判据】五路径一次提交上 main 并 push 成功，`git log --oneline -1` 为本次 commit｜【不做会怎样】skill 只存在于工作区未追踪状态，下次 `git clean` 或换机即丢，两条口径回到「只在会话里说过」


### ✅ 2026-09-20 `0920A` M2·U3 硬门槛引擎发版 `.51`——`Q-51` 放行，全部冒烟通过，未回滚

`0920A`（无头，`run-lanes` 起）按 `Q-51`（Shao Peishen 2026-09-20 答「发」）执行。开工自核五条全过（`a6df909` 在祖先链、tasks.md 4.1–4.6 全勾、重跑全量 pytest **3716 passed／0 failed／10 skipped** 与授权数字一致、`write_rejection`/`COMPLIANCE_ASSERTIONS` 逐行核对、`live_resume_intake_enabled` 默认 `False` 无覆盖）。发版 commit `8596829`，现网原 commit `67772ba`（发版三 `0918AM`，哈希+文件计数实测复核吻合）。携带范围 28 文件 / +3168 −6：M2·U3 申诉接线（本次目标，新增 `POST /api/applications/{application_id}/appeal`、`POST /api/rejections/{rejection_id}/appeal/transition`）＋ M3 语音面试测试覆盖补齐（`0919T`）＋调度器脚本修复，均核实不改变现网既有行为（除 U3 本身）。无新增 pip 依赖。

标准五阶段全过：快照 `C:\apps\backups\20260920-0844` → 无需装依赖 → `sync-to-server.sh` 成功 → 闸 G-e 三条全过（`LastTaskResult=267009`／`:8095 Listen`=1／`Application startup complete` 无 `Traceback`）→ 冒烟①–④＋两条额外全过（首页 200、`/api/jobs` 200+JSON、appeal 两端点均命中=1、合规断言 CLI `EXIT=0` 6/6、`assert_no_ai_score_rejections` 隔离结果 0 违例、`appeal_event` 表已建且 `row_count=0`）。红线复验：`live_resume_intake_enabled` 发版前后均 `False`、无 `.env`/机器级环境变量覆盖。**未触发回滚**。执行记录：`docs/releases/2026-09-20-发版四-M2U3硬门槛引擎执行记录.md`。

### 🆕 2026-09-19 `0919R` 泳道收敛 `0919P`/`0919Q` 回 main 尝试——ff-only 失败，两分支均未合并

`0919R`（无头，`run-lanes` 起）按 `0919P`/`0919Q` 收工报告执行收敛，实测 `git merge --ff-only` 对两个分支均 `fatal: Not possible to fast-forward`。根因：merge-base(main, 两分支) 均为 `8bfe582`，但 main 在此之后已推进 4 个提交（`9c10a03`／`54225ec`／`1bb055a`／`da34e94`，均为 task-dispatcher 台账刷新与 `Q-50` 答复），与 `0919P`（`53ed69a`／`bc8b0d3`）、`0919Q`（`41222b3`）各自独立分叉，非快进关系（`git merge-base --is-ancestor` 双向皆 NO）。按红线③「不改用 `--no-ff`、不 rebase、不追问」，本条到此为止，main 未变（仍为 `da34e94`），两分支代码改动（`scripts/dispatcher_backlog.py`／`scripts/queue_pending.py`／`scripts/handoff_digest.py` 等）仍只存在于各自远端分支，**尚未进入 main**。

- 【谁做】下一条收敛 opener（需人工判断合并策略：三方合并或重新 rebase 到当前 main）｜【状态】待派发｜【判据】`git merge origin/lane-0919p-tag4-regex` 与 `origin/lane-0919q-queue-digest`（非 ff-only）分别验证无冲突（两分支改动路径与 main 新增的 4 个提交路径已核对无交集，见下方文件清单）后合并、测试 0 failed、push 成功｜【不做会怎样】`0919P`（TAG4 正则修复）与 `0919Q`（定夺队列过滤工具）持续游离在 main 之外，`Q-47` 的修复成果实际未生效
- 附本次核对的文件差集：main 独有 4 个提交只动 `docs/openers/**`／`docs/roadmap/任务台账.yaml`／`docs/roadmap/定夺队列.md`／`docs/session接力.md`（滚动台账类）；`0919P` 独有改动 `scripts/dispatcher_backlog.py`／`tests/test_dispatcher_backlog.py` ＋ 同类滚动台账文件；`0919Q` 独有改动 `scripts/queue_pending.py`／`scripts/handoff_digest.py`／两个新 test 文件 ＋ 同类滚动台账文件——代码文件三方各不相同，冲突大概率只出现在滚动台账文件（`session接力.md`／`定夺队列.md`／`任务台账.yaml`）内部，需人工核对合并

### ✅ 2026-09-19 `0919S` 泳道收敛完成——`0919P`/`0919Q` 已用 `--no-ff` 真合并回 main，两次 push 均成功

`0919S`（无头，`run-lanes` 起）接续 `0919R` 的精确定位结果，改用 `--no-ff` 真合并（不再尝试 `ff-only`）。开工先核发现 main 已比 `0919R` 收工时（`695170d`）多 2 个提交（`da1dcfa`／`d05b3c5`，均只动 `docs/openers/**`），重新核对确认与两分支改动路径无交集，预判不变，按原计划执行：

- **Step 1**（`lane-0919q-queue-digest` → `41222b3`）：`git merge --no-ff`，**零冲突**（与预判一致）。`tests/test_queue_pending.py`／`tests/test_handoff_digest.py` 8 passed，`tests/test_doc_size_budget.py` 5 passed。merge commit `b17c2a1`，`git push origin main` 成功（`d05b3c5..b17c2a1`）。
- **Step 2**（`lane-0919p-tag4-regex` → `bc8b0d3`）：`git merge --no-ff`，**唯一冲突**恰好命中预判的 `docs/roadmap/定夺队列.md` `Q-47` 行（`docs/session接力.md` 按预判自动合并、无冲突），已按预案整体取分支侧（incoming，0919P 完整核实报告）解决。`tests/test_dispatcher_backlog.py`／`tests/test_dispatcher_answers.py` 68 passed，`tests/test_doc_size_budget.py` 5 passed。merge commit `e5d3441`，`git push origin main` 成功（`b17c2a1..e5d3441`）。
- `Q-47`／`Q-48` 修复成果（`TAG4_RE` 双格式兼容、定夺队列过滤+handoff摘要工具）现已生效于 main，不再游离。两条 worktree／远端分支按红线未删除，留待例行清理。

### ✅ 2026-09-19 `0919T` M3 语音面试 effect 测试覆盖补齐——`0919O` 登记的四条测试已转绿，全量 pytest 0 failed

`0919T`（无头，`run-lanes` 起，worktree）接续 `0919O` 登记的两项待派发，逐条修复：`_ADDED_COLUMNS` 护栏集合放宽到含 `job_prep_config`／`interview_session`（两表均已有 `CREATE TABLE IF NOT EXISTS`，只是护栏预期表集合漏更新）；`tests/test_db_m2_u2_schema.py` 补两张空壳表并顺带修正一处与本条同根因的陈旧断言（`apply_column_migrations` 返回值早改成 `table.column` 格式，该断言仍用裸列名，U3 改格式那次漏改）；`effect_send_verification_code`（OQ-10 未决占位桩）补 `@idempotent_effect` 装饰器（不改签名/行为）；`EFFECT_NODE_MANIFEST` 补齐 M3 U3/U4/U5 合入的 18 个 effect 节点＋各自崩溃-恢复配方（`invite_nodes.py` 11 个、`live_session_nodes.py` 4 个、`interview_scoring_nodes.py` 3 个）。

补装饰器后连带发现 `effect_send_verification_code` 这个永久占位桩（函数体恒 raise NotImplementedError，走不到 `effect_log` INSERT/commit）结构上无法套用两条通用崩溃-恢复协议——已在 `tests/test_effect_idempotency_suite.py` 新增 `_PERMANENTLY_UNIMPLEMENTED_STUBS` 常量把它排除在通用 parametrize 外，改用一条专属用例固化"异常原样透传、不留痕"，并同步修了 `tests/test_invite_nodes.py` 里一处因装饰器改变调用签名而报错的既有测试（零参调用改传 `conn`/`thread_id`/`business_key`，行为断言不变）。全部改动只在 `tests/` 与一行装饰器，未碰任何生产逻辑。

全量 `pytest -q`：**3716 passed, 0 failed, 10 skipped**（`0919O` 记录的基线 3662 passed／4 failed／10 skipped）。`git merge --no-ff` 回 main（merge commit `a6df909`），`git push origin main` 一次成功。

- 【谁做】下一次 `Q-49` 重新授权前｜【状态】待派发｜【判据】在定夺队列新增一行请 Shao Peishen 针对「本条重新核实过的 0 failed 全量结果」再答一次「发」，⇛ 才可派下一条 `deploy-51` 执行 opener｜【不做会怎样】`Q-49` 现有「发」的答复文本引用的判据已知失真，继续按它直接发版等于绕过自核（本条已在下方定夺队列登记）

### 🆕 2026-09-19 `0919O` M2·U3 发版执行自核拦停——`Q-49` 引用的「5 failed 已知无关」名单本身失真，未发版、`.51` 未动

`0919O`（无头，`run-lanes` 起）执行发版前置自核第一步「全量 pytest 重跑」，main HEAD 为本次执行时刻（`182383d` 及其后合入的 M3 语音面试 U2/U3/U4 与调度器修复均已在内）：**3662 passed／4 failed／10 skipped**（85.59s，`venv` 内 `python -m pytest -q`）。逐条比对 `0918AB` 记录的原始「5 failed」名单（`docs/session接力.md` 09-18 条目，本节上方保留）：4 条新失败**没有一条**在原名单里，且原名单里唯一被标「真回归」的 `test_manifest_matches_the_source_tree` 从未修过、这次仍在失败列表里（换了报错内容——现在缺 18 个 M3 节点，当时缺 1 个 `effect_persist_flags`），另 4 条环境缺口已消失（`funasr`／`livekit` 大概率已补装）。按 `0919O` 预案「逐条比对，任一条不在原名单 ⇒ 停，不得发版」，本条到此为止，**未进入锁定发版 commit／sync/冒烟任何一步，`.51` 现网未受影响，无需回滚**。四条失败详情：

- `tests/test_db_m2_schema.py::test_added_columns_tuple_still_only_touches_job_profile` — `_ADDED_COLUMNS` 里出现了护栏不认识的 `job_prep_config`／`interview_session`（M3 语音面试合入带进的列迁移，未同步更新这条机械判据的预期表集合）
- `tests/test_db_m2_u2_schema.py::test_old_job_table_gains_parse_confidence_threshold_via_migration` — 直接因上一条同根因报错：`job_prep_config` 只进了 `_ADDED_COLUMNS` 没进 `SCHEMA` 的 `CREATE TABLE IF NOT EXISTS`，老库跑迁移会 500
- `tests/test_effect_idempotency_suite.py::test_manifest_matches_the_source_tree` — `EFFECT_NODE_MANIFEST` 漏登 M3 语音面试合入的 18 个 effect 节点（`effect_close_session` 等，见 `0919O` 完整报错）
- `tests/test_effect_idempotency_suite.py::test_every_effect_named_function_is_decorated_with_idempotent_effect` — `effect_send_verification_code`@`app/graph/invite_nodes.py:573` 缺 `@idempotent_effect` 装饰器，铁律 1 要求的幂等键/`effect_log` 落空

四条**均指向 M3 语音面试包（U2/U3/U4）合并带入的既有缺口，不是 M2·U3 自身回归**，但既不在 `Q-49` 授权文本引用的「已知无关」名单内，也不属于 `0919O` 有权自行判定"确认无关就放行"的范围（预案要求逐条比对不在名单即停，不得自行扩大解释）——按无人值守预案，登记后原地停，不追问、不代拍。两项登记待派发：

- 【谁做】M3 语音面试 U2/U3/U4 后续 lane 或专门修复 opener｜【状态】待派发｜【判据】`_ADDED_COLUMNS` 涉及的新表（`job_prep_config`／`interview_session`）补齐 `SCHEMA` 里的 `CREATE TABLE IF NOT EXISTS`，`EFFECT_NODE_MANIFEST` 补上 18 个新节点＋各自崩溃-恢复配方，`effect_send_verification_code` 补 `@idempotent_effect` 装饰器（缺幂等键，铁律 1 直接命中），四条测试转绿｜【不做会怎样】铁律1对这批 M3 新节点失去覆盖，全量 pytest 持续非 0 failed 掩盖后续真回归信号，且 `Q-49` 无法重新核实通过
- 【谁做】下一次 `Q-49` 重新授权前｜【状态】待派发｜【判据】上一条四测试转绿后，重跑全量 pytest 确认 0 failed（或新失败清单逐条核实无关）后，在定夺队列新增一行请 Shao Peishen 针对「重新核实过的失败名单」再答一次「发」，⇛ 才可派下一条 `deploy-51` 执行 opener｜【不做会怎样】`Q-49` 现有「发」的答复文本引用的判据已知失真，继续按它直接发版等于绕过自核

### 🆕 2026-09-18 S-M3 立包 `voice-structured-interview`（G1 Q-37 放行后，无头）〔原派车号 `0918B`，因号池体积闸登记被撤回（Q-43），该号已归 M2U1 建造；本条不占号〕
- 包已立并过 `openspec validate --strict`：6 能力（`interview-prep-question-engine`／`interview-invite-and-consent`／`live-voice-interview-session`／`interview-scorecard`／`interview-recording-retention`／`m3-compliance-assertions`）／tasks 76 条（0.1 R-9 已勾，1/76）／🔴 7／design Open Questions **11**（OQ-1–4 待专员 `HR-G-NN` 四条、OQ-5 X5 探针、OQ-6 合规验收 #2、OQ-7 X6、OQ-8 对外通道、OQ-9 语音主机采购、OQ-10 短信通道与验证码门禁口径、OQ-11 内部模拟录音留存）。路线图 §二 M3 行改「已立包 1/76」。commit hash＝本行所在提交（`git log --oneline -1 -- openspec/changes/voice-structured-interview/proposal.md`）
- ✅ 已闭环（`0919P` 2026-09-19 核实：`定夺队列` `Q-46` 已答「定」，M3 语音面试 U2/U3/U4 已合 main，见上方 09-19 `0919O` 条目，本行「待 G2」描述已过时）【谁做】task-dispatcher【状态】待 G2（Open Questions 全部是待专员＋外部依赖原样转入，不阻塞 spec-to-plan；U0 探针与 U1–U3、U5 不依赖任何 OQ）【判据】Shao Peishen 在定夺队列对本包 G2 答「定」后派 spec-to-plan（从 U0 探针起）【不做会怎样】M3 停在 propose；X5 探针继续空等
- ⚠️ **重复派发实证**：`0918B` 同一 opener 在 `4299785` 合入后又被无头起了一次（本行所在提交），后者开工自检发现包已在 main、四项交付物全在，按「让位给进度靠前的」规则未重跑、未覆盖，只登记本行。【谁做】task-dispatcher【状态】待查【判据】调度器派发前对号池台账查「已完成」标记并跳过【不做会怎样】每次重复派发白烧一份预算，且未跟踪产出可能互相冲掉
- ⚠️ 本文件追加后 ≈55 KB，闸 58 KB（`tests/test_doc_size_budget.py` 现值，5 passed）——余量 < 3 KB，下一条追加前宜先打「【已闭环】」跑 `scripts/archive_docs.py --apply`

### 🆕 2026-09-17 `0917Y` G3 泳道结果私信本人——代码已合，待重启值守服务后观察首条真发

裁决 1a（只私信本人、⛔ 不进群）已做成结构：`owner_notify_outbox` 发件箱 ＋ `python -m tools.liaison owner-notify` 入队 CLI ＋ 值守线程空闲 tick 消费（`owner_notify.py`）；`run-lanes.sh` 收敛后自动入队 `lanes-<STAMP>`。收件人只从 `config/whitelist.yaml` 按 `name == 邵培申` 解析，无收件人参数。发送口 = SDK `client.send_message(userid, markdown)`（1.0.2 表面只读核过，**未真发过**）。

- 【谁做】Paul（CC opener，落档提交类）｜【状态】⏸ 待做｜【判据】`launchctl kickstart` 重启值守服务后，下一批 run-lanes 收敛 ⇒ 企微单聊收到「泳道批次收敛」私信，且 `owner_notify_outbox` 该行 `sent_at` 非空、`attempts=0`｜【不做会怎样】旧进程没有消费者代码，发件箱只积不发、无任何症状
- 首条真发若 `attempts` 到 3 停发（看 `data/liaison/logs` 里「本人通知已停发」），最可能是单聊 chatid 口径或 markdown body 形状与真实 SDK 不符 ⇒ 按 `last_error` 修 `owner_notify.SdkSendPort`，⛔ 不改收件人解析

## 三、待决策 / 悬置

| # | 事项 | 状态 |
|---|---|---|
| 1 | 🔴 **`git push` 被 auto mode classifier 拦** | 反复出现（08-28、08-30、09-03 K/L 又两次）。09-03 `0903Z` 实测：发车前那次被**直接拒绝**（非挂起待点击），收尾那次成功——同一 session 内两次结果不同，机制仍不明。两条路：**(a)** 每次在能批准的 session 里点放行；**(b)** 给 `.claude/settings.json` 加 `"permissions": {"allow": ["Bash(git push:*)"]}`——项目级、可提交、对所有 session 生效。**Shao Peishen 09-03 拍板走 (b)**。Cowork 侧改 `settings.json` 被 classifier 拦（改权限配置本就该在 CC 里人眼过一遍），✅ `0903M` 已加（`365e5fa`）。⚠️ 白名单对**新开的** session 生效。**09-03 17:xx 又撞一层**：Auto Mode 分类器拦 `0903Y` 的 `nohup run-lanes.sh`（判「无人值守起子 session」高风险），看护者自己改 settings.json 也被拦 ⇒ Shao Peishen 手工加 `Bash(bash docs/openers/run-lanes.sh:*)` 与 `Bash(nohup bash …:*)` 两条。已写进 lane-dispatch skill ④ 与看护者前置自检第 0 条。**09-04 `0904Z` 推翻「白名单能解决」的归因**：白名单三条确认已在 `.claude/settings.json` 里（`grep -c`=2，`scripts/allow_run_lanes.py` 复跑也确认无新增可加），但 `nohup run-lanes.sh` 与 `./sync-to-server.sh` 仍各被拦两次——说明白名单从未是真正生效的机制，09-03 的"解除"很可能是巧合归因，不是因果。**唯一验证有效的路径**：把启动命令原样贴成 ` ```bash ` 代码块发给 Shao Peishen，他在 CC Desktop 对话里点 Run 按钮直接执行——用户直接动作不经过 AI 的 Bash 工具调用，不触发该分类器；看护者自己反复重试大概率无效。已写回 `lane-dispatch` skill。**✅ 09-08 20:3x 根治**：`0909G` 建 launchd WatchPaths 触发器（`docs/openers/lane-launcher.sh` + `scripts/install_lane_launcher.py`），Shao Peishen 在 Terminal 装好（`bootstrap rc=0`，`state = not running` 是 WatchPaths 型的常态，有请求文件才起）。从第十四批起看护者写 `.claude/handoff/launch/<ts>.request` 即发车。**09-09 `0909Y` 首跑：只成一半**——六秒内触发+白名单+`.started` 全通，但 run-lanes 被 launchd 连坐杀（plist 缺 `AbandonProcessGroup`；另潜伏 `PATH` 无 `~/.local/bin`）。修法在【二】⑮ C 第一行；修好前退路仍是点 Run（无引号版命令，已写进 lane-dispatch skill）。发版 `sync-to-server.sh` 仍走点 Run（不可代项，人点一下本身就是授权留痕） |
| 2 | 🔴 **worktree 被未落档地清理，已发生两次** | 08-30 11:38 扫掉 u2/unitE/unitF/u1 四条（判据＝真未合 0，代码零损失）；**09-03 前 u5 也被移除**——而 08-30 那份报告刚评估过「u5 真未合 11，同样的清理不会碰它」。⇒ **判据变了或用了 `--force`，机制不明**。代码没丢（分支 `worktree-audit-u5-queue-and-wiring` 与 `19ab503`/`f899c98` 都在），丢的是 worktree 内 git-ignored 的 `.superpowers/sdd/` 台账。**要不要查清是谁在清、加个护栏？** |
| 3 | **TD-9**：同一草稿第二次拦截零留痕 | U6（0903G）已**坐实**："放行后复发又被拦"路径系统性缺席。修复要改已过审的 `approve()` 签名 + 5.4 幂等键公式，属契约层变更。✅ **已修复合入**（`bf45370`，0903Q），TD-9 销账行已写；只差包归档（`0904A`） |
| 4 | `.51` 留步清单**只剩一项** | §5-1 备份任务确认/新增（`0903D` 只做了一次性快照 `C:\apps\backups\20260903-1003`，不等于常态化备份任务）。§5-2 链校验与 §5-3 四步已于 09-03 闭合。见 `docs/audit-and-outbound-ops.md` 第五节 |
| 5 | `.51` 整机重启 | 阻断已清、只差窗口。⚠️ 爆炸半径 **7 个服务**（含门户网关本体），`CBS RebootPending=True` + 已 85 天未重启，停机时长不可按常规估。opener＝编排文件 `[Mac] 0820-9R` |
| 6 | 阶段 C 门户导航 | 需在 Win 笔记本上改门户 HTML。板块名「HR·招聘智能体」，外链 `http://192.168.100.51:8095/hr/recruit-agent/`。与 `.51` 服务无关，不影响运行中的服务 |
| 7 | 决策代理人 | `CLAUDE.md` 框架已建，**2026-08-28 决定继续不设**。「可代」项在无代理人期间同样一律挂起等本人 |
| 8 | 06 清单剩余 | 3.3 企微 webhook（无挂载点）、9.1/9.2/9.3 沟通线。7.1/7.4 合规条款已随 audit 包推进 |
| 10 | 8.4 ＋ U6 巡检 CLI 上 `.51` | 8.4 要人在页面跑通"模糊回复→点选→带缺口确认"；U6 的巡检 CLI 从未对 `.51` 真实 `demo.db`/`decisions.jsonl` 跑过——且 U6 代码**还没部署到 `.51`**（当时现网 `d104249`，早于 U6；**2026-09-17 七次发版后现网 = `f81cc9d`**）。✅ **已闭合**（`feb49d6`）：二次发版含 U6，巡检 `EXIT=2`＝镜像尚不存在，8.4 本人跑通回勾 |
| 11 | `data/replay/` 快照随 worktree 自删 | H 拉回的 `.51` 一致快照（2.4 MB）随其 finishing 流程一起没了，分析结论已在 `2239b90` 的 findings 里，丢的是原始输入、无法逐字节复核。**Shao Peishen 09-03 裁决：立规矩**，已写进 run-build / lane-dispatch 两个 skill（`0903K` 提交） |
| 9 | 🧪 `claude -p -n` 是否真给 session 起名 | **未实测**。`run_lane()` 一直在传 `-n`，但那条注释原来引的"实证"已被推翻。留着无害，⛔ 不要写成"脚本这条路能保住编号"。5 分钟可验 |

---

## 四、绕不开的环境事实

- 🔴 **`.51` 上 venv 的目录名是 `.venv`（带点）**。真源＝`deploy-server.ps1:31`
  `$venvPath = Join-Path $AppDir ".venv"`；旁证＝`docs/findings/2026-08-20-51整机重启验证-重启前采集.md`
  里从实机取的计划任务 `Execute` = `...\.venv\Scripts\uvicorn.exe`。
  **08-31 就因为文档少写这个点，整条 §5-3 验证被误判成「服务器缺 venv」，白等了三天。**
  📌 教训：报错说"找不到 X"时，**先核 X 的拼写与真源是否一致，再去查环境**——
  真源就在本仓库里，一条 grep 的事。
- 🔴 **pytest 基线⛔ 不要写死进 opener**。`222` → `356` → `487` → `675` 已经飘过四轮，
  **写死的数字必然过期，且过期时毫无症状**（判据退化成恒真，等于没在验）。
  看护者 opener 已改成「开跑前自测一次当本批基线，判据＝跑完 ≥ 开跑前且 0 失败」。
- 🔴 **`set_session_title` 在远程编排器派发的 session 里不可用**（08-30 实测，报
  `unavailable in sessions dispatched by a remote orchestrator`）。这类 session 侧边栏会丢编号，
  **不是故障、也不是漏调**，如实登记即可。
- **无头 session 取不到 `superpowers:*`** —— ✅ **2026-09-09 `0909AB` 已修复**；以下旧结论保留为成因记录：
  插件装在 `projectPath: /Users/paulshao/Projects`
  项目作用域，`.claude/settings.json` 里 `enabledPlugins` 开着也解析不到。
  ⇒ `run-build` 的前置检查 1「调不到就停」在无头下**恒定失效**。
  08-27 与 08-30 两次都是执行者照磁盘上的 `SKILL.md` 手工走完协议的——**那是运气，不是机制**。
  - **修法＝改 user 作用域**：`claude plugin install superpowers@claude-plugins-official --scope user`
    （user 作用域对任何 cwd 生效，含 `/private/tmp/wt-*` worktree）。实测：修前 `cd 仓库根` 与 `cd /private/tmp`
    两条无头探针都回 `Unknown skill: superpowers:writing-plans`，修后都回 `LOADED`；
    `subagent-driven-development` 在 `/private/tmp` 另验一次 `SDD-LOADED`。
    **`run-lanes.sh` 的 `--plugin-dir` 退路没用上，脚本一行未改。**
  - ⚠️ **user 作用域装到的是 6.3.0**，不再是六批手工走时读的 6.2.0（两版 `skills/` 目录同为 14 个、名字逐一相同）。
    引用磁盘 SKILL.md 的历史路径写死 `6.2.0` 的地方，读到的已不是现在生效的那份。
  - ⛔ **`claude plugin disable <id> --scope project` 是按插件 id 全局生效的**：09-09 实测它把 user 作用域那份
    一起置成 `enabled: false`（三条记录全灭），已从备份回滚并复验 `LOADED`。要清 `/Users/paulshao/Projects`
    那份旧 project 记录，只能直接编辑那个 `settings.json`，⛔ 不要用 `plugin disable`。
  - 🔴 口径（Shao Peishen 09-09 定）：**修好以后一律走规范流程**——`Skill(superpowers:…)` 回 `Unknown skill`
    ＝环境故障，泳道登记「⏸ 留步：superpowers 不可达」并停，⛔ 不再手工走、⛔ 执行者不自己装插件。
- **`--max-budget-usd` 在 Max 订阅下不是钱闸**，是"跑飞保险丝"。真正的天花板是 5 小时滚动 +
  每周用量窗口。并行两条 run-build 用量翻倍，开跑前看 `/usage` 比看美元数有意义。默认已提到 25。
- **GitHub Actions**：private 仓库 Free 计划 2000 分钟/月，**Windows runner 按 2x 扣**，撞过一次额度。
- **日志路径耦合**：`log_file` 是相对路径 `logs\app.log`，靠计划任务的
  `WorkingDirectory` 解析——**改工作目录会把日志静默挪走**。
- **脱敏层尚未被真实数据检验**：日志里 0 个标记词，但 `<redacted>` 也是 0 命中，
  说明脱敏根本没被触发（`loggable_summary()` 至今无生产调用点）。这条验证成立的是
  「没有泄漏」，**不是「脱敏被证明有效」**。

---

## 五、绕不开的约束（每次都要记得）

- 🔴 **正文 > 500 字的 opener 走引用式**（Shao Peishen 09-03 定）：正文写 `docs/openers/<MMDDX>-<主题短名>.md`，
  聊天只贴 4 行引用块（头两行 + set_session_title + 「读该文件逐节执行，文件不存在即停」）。文件随任务提交＝留痕＋号池可 grep。
  模板与理由见 `.claude/skills/kickoff/SKILL.md`「引用式 Opener」。
- **给 Paul 的每条指令，代码块头两行固定**：`[Mac]MMDDX-<主题短名>` + 【设置】单行（五项 ｜ 分隔）。
  **CC 的 opener 第 3 行必须调 `set_session_title`**。`MMDD` 实跑 `TZ=Asia/Shanghai date +%m%d` 取
  ——本机在 EDT，照本机日期编会集体差一天且不报错。判据表见 `CLAUDE.md`。
- 🔴 **他在 Desktop 用 CC，不开终端。要他执行的东西一律包成 opener 代码块，⛔ 不给裸 bash。**
  泳道发车只给**看护者 opener 一整块**（那块的【三】自己会启动脚本），⛔ 不另给发车命令，
  也⛔ 不要写「去编排文件第 N 行整块复制」——要把正文原样贴进回话里。
- **一次给 ≥2 个 opener 时，必须说明次序与能否并行**（判据＝触碰区是否重叠）。
- **在 `.51` 上跑的命令**要写明"在 .51 上跑"并给 **ssh 包装形式**，⛔ 不给裸命令。
- 🔴 **他说的【xx】大概率指 session 名**，不是文件名。⛔ 别去 grep 文件找它——
  跨界面看不到对方会话，正确反应是走文件系统盘点真身（`git log` / `.claude/handoff/*` /
  各 `tasks.md` 的回勾），然后说明「那条 session 我看不到，以下是从文件系统盘出来的」。
- **git 相关只能在 CC**——Cowork 的 bash 在隔离 VM 里，对 `.git/` 只能写不能删。
  Cowork 侧只读核查用 `git --no-optional-locks`，不会留锁。
- **「企业AI转型」已迁出 OneDrive**，唯一入口＝GitHub 公开仓库
  `Raytheoner/zhuopin-ai-transformation`，**分支 master**，WebFetch 读
  `raw.githubusercontent.com/.../master/<路径>`（中文路径要 percent-encode）。
  没有本地副本 → **grep 不了，引用必须给文件级 URL**。

## 六、已固化进 skill 的判据（此处只留指针）

1. **计划里的任务标题必须是三级 `### Task N:`**——二级会让 `scripts/task-brief` 静默返回空。
   已写进 `.claude/skills/spec-to-plan/SKILL.md`。
2. **合并后要单独验 `git rev-list --count main..<分支>`**——`finishing-a-development-branch`
   被跳过时毫无症状。已写进 `.claude/skills/run-build/SKILL.md`。
3. **`rev-list` 非 0 时先别下结论**——main 被 rebase 过时会假阳性。再跑 `git cherry -v main <分支>`：
   全 `-` 是内容已在 main，有 `+` 才是真没合。
4. **说「开始泳道看护」即触发 `lane-dispatch` skill**——扫待办 → 判触碰区分泳道 → 写 opener
   进编排 → dry-run 核对 → **只给看护者 opener 一整块**。
5. **`run-lanes.sh` 开跑前自检**：无头块内含 `set_session_title` → `exit 13` 拒跑；
   块外缺豁免注明 → 只 WARN。方向别看反——无头块的**正确状态是不带那一行**。

### 🈸 2026-09-20 拆件会话 · 人事部#3 回件判「待人」——仅覆盖人事部#2决策点a，未覆盖场景A/B/C/D与决策点b/c

拆件会话（liaison-unpack 章程）探到信号，回件（msgid `7ed6bf11e1b21d321182ac22f0be5935`，归档
`data/liaison/archive/wrvDL_DAAAnkeGLkk1_bu2Ne1oQfc4BA/20260920/7ed6bf11e1b21d321182ac22f0be5935__正文.txt`）
全文仅三行：「人事部#2 决策点a 答复／10个岗位需求清单」，无附件、正文里没有实际的清单内容（只有标题字样）。
`人事部#3` 合并了多项决策点（两份讨论稿 A/B/C/D 场景与路线图回应 ＋ 人事部#2 未答决策点 a/b/c），
本回件字面上只指向其中一项（人事部#2 决策点a），且看不出实际清单内容是否随附件另发、还是本就没写。
章程 §二 只给「实质回件⇒转闭环」「非实质回件⇒还原」两种整信处置，没有「合并信里只答了一项，其余仍空」
这种部分命中的判据，属红线③新下裁决，转待人，未做任何回灌与台账转态。

【谁做：Shao Peishen】【状态：待人】【判据：Shao Peishen 判定该回件是否已构成对人事部#2决策点a的完整答复
（是否另有附件/后续消息补全"10个岗位需求清单"的实际内容），并明确人事部#3 应整信待齐 A/B/C/D 场景与
决策点b/c 后一次性转闭环，还是允许按决策点分批部分关闭】【不做会怎样：`data/liaison/unpack-signal.json`
中该条 pending 信号未清，`docs/跟进信/README-跟进信清单.md` 里人事部#3 行维持
「📨 回件已到，待拆件 2026-09-20 17:04 CST」原状态不变，下一轮拆件会话探测仍会再探到同一条，不会丢】

### 🈸 2026-09-21 拆件会话（0921C）· 人事部#3 第二条回件同判「待人」——决策点b 缺时数/人名，且仍未覆盖场景A/B/C/D与决策点c

`unpack-signal --probe` 本轮探到两条 pending：上条（msgid `7ed6bf11e1b21d321182ac22f0be5935`，决策点a）
自 2026-09-20 起仍待 Shao Peishen 裁决，未变化，沿用上条登记不重复处理。新增第二条
（msgid `79ec288dcc53c75df9fa72f3dd1612cd`，归档
`data/liaison/archive/wrvDL_DAAAnkeGLkk1_bu2Ne1oQfc4BA/20260920/79ec288dcc53c75df9fa72f3dd1612cd__正文.txt`），
全文三行：「人事部#2 决策点b 答复／选择：1」。决策点b 原问法要求「填1、2或3，并写小时数或人名」
（见 `docs/跟进信/人事部-汤丽萍-跟进-2026-09-17-M1验收岗位清单与M2评测集安排.md`），本回件只给了选项号，
未写投入小时数或牵头人姓名，回答不完整；且 `人事部#3` 仍是整信合并 A/B/C/D 场景 ＋ 决策点a/b/c 一并回收，
本条同样只覆盖其中一项且不完整。与上条同一失败模式（章程 §二 无「部分命中」判据），属红线③新下裁决，
转待人，未做任何回灌与台账转态，两条 pending 信号均未清。

【谁做：Shao Peishen】【状态：待人】【判据：Shao Peishen 一并裁决两条回件（决策点a、决策点b）是否已足够
完整、是否需要汤丽萍补投入小时数/人名与决策点a清单实际内容，并明确人事部#3 是整信待齐 A/B/C/D+a/b/c 后
一次性转闭环、还是允许按决策点分批部分关闭——两条回件都受此同一个裁决约束，不需要分别裁决】
【不做会怎样：`data/liaison/unpack-signal.json` 中两条 pending 信号均未清，
`docs/跟进信/README-跟进信清单.md` 里人事部#3 行维持「📨 回件已到，待拆件 2026-09-20 17:04 CST」
原状态不变，下一轮拆件会话探测仍会再探到同两条，不会丢】

### 🈸 2026-09-21 拆件会话 · 人事部#3 新增两条回件（决策点a 清单标题＋空文本），仍无实际清单内容，归入 Q-59 同一待人项，未新下裁决

`unpack-signal --probe` 本轮新增两条 pending（另两条 `7ed6bf...`/`79ec288d...` 已列入 `docs/roadmap/定夺队列.md`
Q-59，自 0921C 起未变化，沿用不重复处理）：

- msgid `7b37bb24af440a172f6ae89b6534289f`（归档 `data/liaison/archive/TangLiPing/20260921/7b37bb24af440a172f6ae89b6534289f__正文.txt`，
  2026-09-21 11:08:39 CST）：全文两行「人事部#2／决策点a 答复中的清单」，同样只有标题字样，正文里没有实际清单内容。
- msgid `459b735f7df8204551564a25007329eb`（归档 `data/liaison/archive/TangLiPing/20260921/459b735f7df8204551564a25007329eb__正文.txt`，
  2026-09-21 11:08:40 CST，紧随上一条 1 秒）：归档文件存在但内容为空。

归档目录已从群 ID 哈希（`wrvDL_DAAAnkeGLkk1_bu2Ne1oQfc4BA`）变为 `TangLiPing`，判断这两条是汤丽萍改走私信
再次尝试发送决策点a 的实际清单，但清单内容仍未落进归档正文（与 0920 那条「只有标题、无清单」失败模式一致，
疑似附件未随文本一起归档，`0921E`/`0921F` 已在诊断同类附件接收缺口，但那是建造侧修复，本会话红线②不得
触碰）。章程 §二 仍然只有「实质回件⇒转闭环」「非实质回件⇒还原」两种整信判据，这两条既不是完整答复也不是
纯寒暄，与 Q-59 是同一个「决策点a 清单内容缺失」的延续证据，不构成需要另行拍板的新问题，因此不另开
定夺队列条目，仅在此登记新证据，未做任何回灌与台账转态，两条 pending 信号均未清。

【谁做：Shao Peishen】【状态：待人（沿用 `Q-59`，本条只是新增证据，不需要单独裁决）】
【判据：Q-59 一经裁决（决策点a/b 是否已足够完整，`人事部#3` 是否按决策点分批部分关闭），
本条与之前两条回件同批处理，无需为这两条新回件单开决策】
【不做会怎样：`data/liaison/unpack-signal.json` 中四条 pending 信号均未清，
`docs/跟进信/README-跟进信清单.md` 里人事部#3 行维持「📨 回件已到，待拆件 2026-09-20 17:04 CST」
原状态不变，下一轮拆件会话探测仍会再探到全部四条，不会丢】

### ⏸ 2026-09-21 `0921F` 决策点 c 漏收诊断——环境不可达，留步

`0921E`/`0921F` 两次核查：本机与本 worktree 均无 `data/liaison/logs`，无法读入站日志判定
2026-09-20 17:04:39 之后「人事部#2 决策点 c 答复／选择：2」这条 `@MAC机器人` 文本为何未进归档。
四个候选成因（时间窗截断／msgid 去重键碰撞／群帧过滤误判单聊为群／值守服务在该时刻掉线重启）
任一都需要 `.51` 上的真实入站日志才能排除，⛔ 本条只诊断不改判定逻辑。

【谁做：下一条具备 `.51` 入站日志只读访问权限的泳道，或 Shao Peishen 现场在 `.51` 上核对
`data/liaison/logs` 后转述结论】【状态：⏸ 留步】【判据：拿到该时间窗的 `.51` 入站日志，
四个候选成因逐一排除或坐实，诊断结论写出后本条状态由「留步」改「已诊断」】
【不做会怎样：`Q-01` 队列里并存的漏收现象无法定位是代码逻辑漏收还是服务瞬断，下一次专员回件
再发生同类漏收时仍会被当新问题重新排查一次，也无法判断当前 fail-closed 附件口径与本次漏收是否同一成因】

### 🈸 2026-09-23 拆件会话（`0923B`）· 人事部#3 四条 pending 复核确认，Q-59 仍待答，无新下裁决

按 `liaison-unpack` 章程逐条重新读取归档件（非沿用摘要），四条内容与既往登记一致，无新增证据：

- `7ed6bf11e1b21d321182ac22f0be5935`（决策点a，「10个岗位需求清单」标题，无实际清单）
- `79ec288dcc53c75df9fa72f3dd1612cd`（决策点b，「选择：1」，缺投入小时数/人名）
- `7b37bb24af440a172f6ae89b6534289f`（决策点a 第二次尝试，「决策点a 答复中的清单」标题，仍无实际清单）
- `459b735f7df8204551564a25007329eb`（0 字节空文本）

`docs/roadmap/定夺队列.md` Q-59 本次核对（2026-09-23）仍为「待答」，答复列为空，未见 Shao Peishen 新裁决。
章程 §二 对四条逐一判定：均落入既有「合并信部分命中，无判据」失败模式（红线③新下裁决），继续折入 Q-59，
不单开新决策、不写回灌结论、不改台账。`data/liaison/unpack-signal.json` 四条 pending 均未清（工具只支持
`--clear --before <单一时间戳>` 前缀清除，四条中最早一条即为待人项，无法只清已判定项而保留待人项，
因此本轮不执行清除，避免误清未决项）。

【谁做：Shao Peishen】【状态：待人（沿用 `Q-59`，本条为复核确认，不构成新裁决）】
【判据：同 `Q-59` 现有判据——一并裁决四条回件（决策点a 两次尝试、决策点b、空文本）是否已足够完整，
并明确 `人事部#3` 是整信待齐 A/B/C/D+a/b/c 后一次性转闭环、还是允许按决策点分批部分关闭】
【不做会怎样：`data/liaison/unpack-signal.json` 中四条 pending 信号继续保留，
`docs/跟进信/README-跟进信清单.md` 里人事部#3 行维持「📨 回件已到，待拆件 2026-09-20 17:04 CST」
原状态不变，下一轮拆件会话探测仍会再探到全部四条，不会丢；机器判据里 `pending<4` 断言本轮预期不过，
是待人的正常结果，不代表处理失败】
