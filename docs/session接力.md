# Session 接力 · HR 招聘智能体

> 滚动更新，覆盖旧版。新会话读完本文即可接上。
> 最后更新：2026-09-10 14:2x（Cowork·HR业务线-接力0909AU：`0910F` 已落地 `541cac1`；机制侧六环节核真身；编排第十七批波次 1 四条并行 plan 泳道 `0910G`–`0910J` ＋ 落档 `0910K`）

---

## 🆕 第十七批 · `liaison-reply-bridge-and-patrol` 建造批（2026-09-10 14:2x，Cowork·0909AU 编排）

### 🔴 先答一个常被问错的问题：「回灌自动落档、自动拆件」建成了吗？—— **没有。链路六环，四环是零。**

按读法纪律核的**真身**（只看函数体、调度配置、测试三处，⛔ 不读 proposal 的问题陈述当现状）：

| # | 环节 | 状态 | 真身判据（2026-09-10 14:0x 实核） |
|---|---|---|---|
| ① | 回件到达 → 值守服务收到 | 🔴 **零** | `session_client.SUBSCRIBED_EVENTS` 只有 4 个连接事件、无 `message`；`test_this_chapter_wires_no_message_handling` 明令不接 |
| ② | 落档（消息归档＋附件） | 🟡 **代码齐，从未真跑过** | `inbound.handle_inbound_message` 归档→入队→礼貌回复三步齐全（第 4 章 6/6），但**产品代码里零调用方**——只有测试调它 |
| ③ | 入队（待办台账） | 🟡 **代码齐，零消费方** | `enqueue_task` 只被 `inbound.py` 调；`defer_task` / `mark_task_pushed` 在产品代码里**零调用方**（只在 `queue_view` docstring 与 `retention` 注释里被提到） |
| ④ | **回件 → 跟进信 那座桥** | 🔴 **零** | `followup.py` 里 grep `liaison_message` / `inbound` / `msgid` **零命中** ⇒ 发信侧与收信侧完全不通 |
| ⑤ | **打标即开班（自动拆件）** | 🔴 **零** | 产品代码无 `unpack` / `dispatch` / `bridge` / `patrol` 任何模块；`install_launchd.py` 只装**一个** job（`com.zhuopin.hr.liaison` 值守服务），**无巡检 job** |
| ⑥ | 章程正本／口径点台账 | 🔴 **零** | 同上 |

⇒ **不是「差最后一公里」，是中间断了两处**：入口没接（AT-1a／`0910B`）、出口没桥（P0）。
> **2026-09-10 订正（`[Mac]0910B` 落地，commit `7636613`）**：入口那半的**接线**已接上（`SUBSCRIBED_EVENTS` 含 `message`、回调只放帧、值守线程调 `handle_inbound_message`、幂等断言齐全）。⚠️ 但 `liaison_message` **现在仍会是 0**——帧字段映射未经真实帧确认，已按 TD-19 同一处置 fail-closed（**TD-43**），收口＝AT-1b。⛔ 不要据此把入口标成「已通」。
变更包 `liaison-reply-bridge-and-patrol` ＝ **0/33**，§0 三门槛一条未勾。
📌 **数据佐证**：`data/liaison.db` 现在 `liaison_message` ＝ 0、`liaison_task` ＝ 0、`effect_log` ＝ 15。

### 编排：波次 1（四条并行）→ 波次 2（四条串行）

🔴 **整批前置＝§0 三门槛全勾**（TD-42 真实验证／SDK `message` 接线／一条真实入站落库），
由 `[Mac]0910B` → `[Mac]0910A` 清。⛔ **门槛没全勾不许开工**，本包 `tasks.md` 抬头写死了这条。

**波次 1 · 四条并行 plan 泳道**（`spec-to-plan`，worktree ❌，各写一份 plan 文件，**触碰区零重叠**）：

| 号 | 单元 | 输入 spec | 产出 |
|---|---|---|---|
| `[Mac]0910G` | P0 · 回件桥＋第九态 | `specs/liaison-reply-bridge/spec.md` | `docs/superpowers/plans/2026-09-10-liaison-reply-bridge.md` |
| `[Mac]0910H` | P1 · 信号与打标即开班 | `specs/liaison-unpack-dispatch/spec.md` | `…-liaison-unpack-dispatch.md` |
| `[Mac]0910I` | P2 · 拆件章程正本 | `specs/liaison-unpack-charter/spec.md` | `…-liaison-unpack-charter.md` |
| `[Mac]0910J` | P3 · 口径点台账 | `specs/liaison-criteria-ledger/spec.md` | `…-liaison-criteria-ledger.md` |

⚠️ **四条可并行的理由是文件级零重叠**（各写一个 plan 文件），⛔ 不是「看起来无关」。
四份 opener 正文里都逐字内嵌了并发四条与同伴触碰区。**手动贴四个 CC session 即可，⛔ 不必走 `run-lanes.sh` 看护者**。

**波次 2 · 四条串行 run-build 泳道**（worktree ☑，从 **`[Mac]0910N`** 起连号取）：
🔴 **2026-09-10 两次订正**：原写「从 `0910L` 起」——`0910L` 已被「判据订正提交」、`0910M` 已被其「合入 main」占用，⛔ 两者都不再是波次 2 的起点。
🔴 **P0→P1→P2→P3 全串行，⛔ 不可并行**——不是文件撞车，是**真依赖**：
P1 的 2.7 要改 P0 的 `bridge.run_bridge`；P2 的 3.2/3.4 要 P1 的 `dispatch` 与 `HEADLESS_ARGV_TEMPLATE`；
`__main__.py` 被 P0（1.7 值守线程接线）／P1（2.8 子命令）／P3（4.2 子命令）三方触碰。
🔴 **波次 2 的 opener 正文等波次 1 的 plan 文件真的存在之后再写**，⛔ 不预先写（预先写＝引用死链，AS-1 已演过一次）。

**§5 验收 · 真实起活实测** ＝ 不可代，Shao Peishen 本人（⛔ 单测全绿不算，本包 `tasks.md` 抬头写死）。

| # | 事项 | 谁做 | 状态 | 判据（怎样算完） | 不做会怎样 |
|---|---|---|---|---|---|
| **Q-1** | 跑 `[Mac]0910K` 落档提交（四份 opener 正文 ＋ 号池四行 ＋ 本节） | Shao Peishen 贴内联 opener → CC | ⏸ **待派发，⛔ 必须先于 `0910G`–`0910J`** | `git show --stat` 里四份 opener 与 `OP-0820` 同时在场 | 四份正文与号池行停在未提交态 ⇒ 贴引用块时接手方 `git show` 不到，重演 AS-2 那条死链 |
| **Q-2** | 波次 1 四条并行发车 | Shao Peishen 贴四个引用块 → 四个 CC session | ⏸ 等 §0 三门槛全勾（`0910B`→`0910A`）。🔴 **2026-09-10 答 `1a` 已裁决：⛔ 不放行到门槛之前**，理由＝`0910B` 收工报告才会给出「帧字段映射走实际字段还是 fail-closed」，那个答案直接决定 P0 第九态与 P1 dispatch 计划里的字段名；门槛没清就写计划＝在猜。**裁决已逐字落进四份 opener 正文【零】门槛 1**，⛔ 不要再当待决项重提 | 四份 plan 文件都存在，且各自 `grep -c '^### Task '` 不为 0 | 波次 2 无输入 |
| **Q-3** | 波次 2 四条 opener 正文与取号（**`0910N`** 起，⚠️ 2026-09-10 两次订正，原为 `0910L`→`0910M`） | Cowork 编排 | ⏸ 等波次 1 出 plan | 四份 run-build opener 落档 ＋ 号池登记 ＋ 同一条 commit | 同 Q-1 |
| **Q-4** | 🔴 **⛔ 汤丽萍现在不得发任何测试消息** | — | 🔴 **仍然锁着**（2026-09-10 14:1x Shao Peishen 问过一次，已答「不行」） | 解锁判据＝`0910A` 跑完后**他本人**在群里 @ 一条，`liaison_message` 由 **0 变 1**。看到 1 之后才请她**私信**一次（私信才有单聊 chatid，第 4 章附件归档链路只能在带附件的私信上验） | 她已白发 4 条；现在再发是第 5 条，且现象与前 4 次一模一样（服务显示 connected、库里 0 行），**分不清是假死还是没接线**——正是 TD-42 末段那条「不可区分」 |

---

## 🆕 判据订正：前置判据只落内容，⛔ 不落历史窗口（2026-09-10，云端 CC `[Mac]0910L` 落档）

**现象**：`0910B` opener 的开工前自检② 要求「`git log --oneline -3` 里应能看到那条追加 8.5bis 的 commit」。
到 2026-09-10 下午，那条 commit（`ba61a6d`）已被后续提交挤到**第 16 位**——判据当场假失败，
而前提（8.5bis 已上 main、已在 `tasks.md` 勾成 `[x]`）**好好地成立着**。

**为什么危险**：假失败**不报错**。接手 session 会照 opener 的兜底动作白 `git pull --rebase` 一轮，
仍看不到，然后规规矩矩「停下报」——一条泳道零产出，且报出的理由（「前提还没上 main」）是**错的**，
派发方会去查一个根本不存在的问题。

**订正**（本条已落地）：
- `docs/openers/0910B-SDK-message事件接线.md` 自检② → 换成内容判据
  `grep -n '^- \[.\] 8.5bis' openspec/changes/hr-wecom-aibot-liaison/tasks.md`；原句只作为 ⚠️ 段里的反例留存
- `.claude/skills/kickoff/SKILL.md` 新增 `### 🔴 前置判据只落内容，⛔ 不落历史窗口（2026-09-10 定）`
  ——含 ⛔/✅ 对照表：认某条 commit 时用 `git merge-base --is-ancestor <sha> HEAD`，⛔ 不用 `-N` 窗口

⚠️ **本条实际在 claude.ai 云端 session 执行**（分支 `claude/criteria-correction-commit-3kqfgz`），
⛔ **未推 `main`**。成因：opener 按 `[Mac]` 写、工作区写的是 `/Users/paulshao/Projects/HumanResource`，
但被投进云端容器（全新 clone、工作区干净）⇒ 那批未提交改动在云端根本不存在，三条自检当场不过、停下报。
Shao Peishen 2026-09-10 答 `1a` 改为在云端重做订正、答 `3b` **Mac 主工作区那四条未提交改动丢弃重来，
一律以本条为准**。

| # | 事项 | 谁做 | 状态 | 判据（怎样算完） | 不做会怎样 |
|---|---|---|---|---|---|
| **L-1** | 把 `claude/criteria-correction-commit-3kqfgz`（`28499f1`）合进 `main` | Shao Peishen 贴 `[Mac]0910M` 引用块 → CC | 📮 **opener 正文已落档，待派发**（`docs/openers/0910M-判据订正合入.md`，答 `1a`）。🔴 独占 main，⛔ 不与泳道并行 | `main` 上 `grep -c "会随时间漂移" .claude/skills/kickoff/SKILL.md` ≥ 1，且 `0910B` 自检② 已是内容判据 | `0910B` 的坏判据继续在线；worktree 从 `main` 拉，读到的还是旧自检②，下一个跑它的 session 照样假失败 |
| **L-2** | Mac 主工作区那四条未提交改动逐条 `git checkout --` 丢弃（⛔ 不 `checkout -- .`／不 `reset --hard`／不 `stash`，会卷走别的泳道） | `[Mac]0910M` 的【二】 | 📮 已写进 `0910M` 正文，待派发（答 `3b`） | Mac 侧 `git status --short` 里这四条消失（`_to_delete/` 不动） | 两份订正并存、内容还不一定一致，合入时冲突且分不清哪份是准的 |

🔴 **2026-09-10 Shao Peishen 答 `2a` 裁决：`0910B` ⛔ 不重跑，视为已完成。** 依据＝三条机器判据在 `main` 上全部成立：
① `tools/liaison/session_client.py:108` `SUBSCRIBED_EVENTS` 已含 `EVENT_MESSAGE`；
② `tools/liaison/__main__.py:234` 值守线程已调 `inbound.handle_inbound_message`；
③ `test_this_chapter_wires_no_message_handling` 函数定义已不存在，由
`test_main_wiring.py:306` 的正向测试 `test_this_chapter_wires_message_handling` 取代。
⇒ **`liaison-reply-bridge-and-patrol` 的 §0 门槛 0.2 已勾 `[x]`**（本条一并落）。
订正后的自检② 只是拦住「再跑一次」，⛔ 不是说这条没做完。

⚠️ **帧字段映射的答案已经有了：走 fail-closed**（`__main__.py:225-231`——「帧字段映射未经真实帧确认，
fail-closed」，只记 ERROR ＋ `describe_frame_shape` 打键名与类型、⛔ 不打取值，然后 `return False`）。
Q-2 当初等的就是这个答案 ⇒ 波次 1 四份 plan 里的字段名**按 fail-closed 那条路径写**，⛔ 不要再等 `0910B` 的收工报告。

🔴 **但 §0 三门槛仍未全勾，波次 1 继续等**：0.1 未清（`docs/tech-debt.md:1982` 写着
`~~TD-42~~ ✅ 已还（20a2848，⏸ 真实验证待 Shao Peishen）`——销账行**没有**真实「假死→自终止→launchd 拉起」
实测记录，⛔ 单测不算）；0.3 未清（`liaison_message` 尚无真实行）。两条都归 `[Mac]0910A`。

---

## 🆕 `0910A` 派号未落档 → 接手 session 扑空（2026-09-10 11:4x，云端 CC `[Mac]0910A` 落档）

> 🔴 **2026-09-10 13:5x 更新（Cowork·0909AU）：AS-1／AS-1b 均已就位，本段其余内容留作判据由来。**
> **派发链＝ `[Mac]0910F` → `[Mac]0910B` → `[Mac]0910A`，三条串行、⛔ 不可并行。**
>   - `0910F`（**引用式**，正文 `docs/openers/0910F-落档提交与解两把git孤儿锁.md`，worktree ❌）＝落档提交 ＋ 删 **两把** `.git` 孤儿锁（`ORIG_HEAD.lock` ＋ `index.lock`，见下方 ④）
>     ＋ 给 CLAUDE.md「git 相关只能在 CC」那条补一句 Cowork `git status` 会埋锁。
>     🔴 **必须排在 `0910B` 之前**：`index.lock` 挡住一切 commit、`ORIG_HEAD.lock` 挡住 merge ⇒ `0910B` 收工合回 `main` 会当场失败。
> - **`0910F` 的 `git add` 是六条路径**（含它自己的正文文件 ＋ H-5 的 `docs/跟进信/README-跟进信清单.md`），清单以正文【三】为准，⛔ 别按聊天里早先那版四条／五条抄。
> - ~~`0910B`（引用式，worktree ☑）＝AT-1a 接线~~ ✅ **已跑完（2026-09-10，commit `7636613`）**：七件事中 ①②③④⑥⑦ 落地并有测试守护，⑤ 帧字段映射走 fail-closed（TD-43）。⛔ **不表示入站已通**——真实入站落库一条仍归 AT-1b／`[Mac]0910A`。
> - `0910A`（引用式，worktree ❌，主工作区）＝TD-42 真实验证 ＋ AT-1b 真实入站，**正文已重写并改名**为
>   `docs/openers/0910A-TD42真实验证与AT1b真实入站.md`（随 `0910F` 提交）。
>
> 🔴 **`[Mac]0910A` 的引用块（`0910F` 提交完成后才可贴，⛔ 提交前不许贴）** —— 依据 kickoff skill
> 「被引文件必须与派号同一次 commit 落档」：正文与号池行都还在未提交状态，此刻贴出去接手方 `git show` 不到。
>
> ```
> [Mac]0910A-TD42真实验证与AT1b真实入站
> 【设置】执行环境: CC ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（要用主工作区的 .env、tools/liaison/.venv 与真实库 data/liaison.db，还要重启真实值守服务）｜ 工作区: 仓库根（/Users/paulshao/Projects/HumanResource）｜ 派发: Cowork·HR业务线-接力0909AU
> 开工第一件事：调 mcp__ccd_session_mgmt__set_session_title（session_id 传字面量 "self"），标题：[Mac]0910A-TD42真实验证与AT1b真实入站
> 第二件事：读 docs/openers/0910A-TD42真实验证与AT1b真实入站.md 全文并逐节执行，本 session 的全部指令以该文件为准。文件不存在、或其首行编号与本块不一致 → 停下报我，⛔ 不要凭标题猜任务
> ```
>
> ⚠️ **贴之前先确认 `[Mac]0910B` 已合回 `main`**（`0910A` 门槛第 2 条会自己再核一遍：`SUBSCRIBED_EVENTS` 必须含 `message`）。
>
> 🔴 **2026-09-10 13:4x 现场实测三条（`0910A` 的开工依据）**：
> ① 值守进程自 09:38:26 起 `state=connected`、`stamp_at` 每 30 秒推进，但 `liveness.json`
> **没有 `last_event_at` 键** ⇒ 跑的是 `20a2848`（TD-42 修复）**之前**的旧进程，新判据根本没上场；
> ② `data/liaison.db`：`liaison_message` ＝ **0**、`liaison_task` ＝ **0**、`effect_log` ＝ 15；
> ③ `.git/ORIG_HEAD.lock` **仍在**（0 字节、mtime 2026-09-10 07:54 CST），孤儿锁判据①② 已成立，③ 须在 macOS 侧现场核。
>
> 🔴 **④ 新查明：`.git/` 下现有 **两把** 孤儿锁，且 Cowork 一把都删不掉。**
> `.git/ORIG_HEAD.lock`（0 字节，mtime 2026-09-10 **07:54** CST）＋ `.git/index.lock`（0 字节，mtime 2026-09-10 **13:52** CST）。
> **成因已实证：Cowork 侧每跑一次 `git status`，git 都会新建 `.git/index.lock`，随后 `unlink` 被拒**
> （`warning: unable to unlink …/.git/index.lock: Operation not permitted`），锁就此留下；
> Cowork 自己 `rm` 同样 `Operation not permitted`。⇒ **这是 Cowork「对 `.git/` 只能写不能删」的直接后果，
> 不是别的 session 在用锁**，且**全程不报错**——直到 Mac 侧下一次 commit／merge 当场失败为止（`0909AS` 撞的就是这个）。
> ⚠️ 判据②「mtime > 10 分钟」对 `index.lock` **本次不成立**（刚生成），但**持锁方身份已确定**＝本 Cowork VM 的 `git status`，
> ⇒ `[Mac]0910F` 按 `docs/findings/2026-08-26-index-lock-孤儿锁判据.md` §3 **三项判据**在 macOS 侧现场核一遍，核过再删。
> 🔴 **§5 的 VM 补充判据（`PPID = 1`）本次给不出结论**——那正是 §5 末句写的「VM 活着但 VM 内 git 已死」形态，其处方是**进 VM 里查**。
> ✅ **已查**：Cowork·0909AU 2026-09-10 13:5x 在该 VM 内实测四条——`pgrep -x git` **空（exit 1）**；`ps | grep -i git` **零命中**（连误报都没有）；
> 扫 `/proc/*/fd/` **无任何进程持有这两把锁**；VM 内进程总数 **7 个**（`bwrap`／`bash`／`socat`×2／`bash` ＋ 采样的 `ps`/`head`）。
> ⇒ **VM 侧那一半已闭**：`PPID ≠ 1` 在本次只说明「Cowork 会话还开着」，⛔ **不说明 VM 内有活着的 git**，⛔ 不要卡在那一步。
> 📌 **顺带的操作纪律**：Cowork 侧**能不跑 git 就不跑**，尤其 `git status`——每跑一次就给 Mac 侧埋一把锁。
>
> 🔴 **⑤ `[Mac]0909AR` 的【三】已作废（Cowork·0909AU 2026-09-10 查出），⛔ 不要再跑那一节。**
> 两条理由，各自独立成立：
> ① **触碰区撞车**——【三】要提交 `docs/openers/OP-0820-全量编排.md` 的未提交尾巴，而 `[Mac]0910F` 正是提交它的那一条（且带内容改动）。谁后跑谁扑空。
> ② **在 worktree 里物理上做不到**——`0909AR` 的【设置】是 worktree ☑，而【三】操作的是**主工作区**的未提交改动；
> worktree 与主检出共用 `.git` 但**各有各的工作树**，主检出未提交的改动在 worktree 里根本看不见（`0909AJ`／`0909AH` 立过的同一条判据）。
> ⇒ `0909AR` **只跑【一】【二】**（TD-29 剩 2 条 ＋ 名单外归档口径订正），`OP-0820` 的提交归 `0910F`。
> 派发 `0909AR` 时**在引用块里补一句**「⛔ 跳过【三】，OP-0820 已由 `[Mac]0910F` 提交」。




`[Mac]0909AS` 跑完 TD-42（`20a2848` 修复 ＋ `32d0cbc` 销账，两条**均已在 `origin/main`**）后，在聊天里派出 `[Mac]0910A`，
且派的是**引用式 opener**（正文指向 `docs/openers/0910A-解锁追平与TD42真实验证.md`——⚠️ **该文件名已于 13:5x 作废改名**，见本段顶部更新块），
但**那个文件从未创建**，号也**没登记进 `OP-0820` 号池台账** ⇒ 接手的云端 CC session 读不到正文，
按 CLAUDE.md「文件不存在、或其首行编号与本块不一致 → 停下报我，⛔ 不要凭标题猜任务」停下，**零改动、零提交**。

⚠️ **顺带查明：`0910A` 那三条 worktree 理由在云端容器一条都不成立**——
① 无 `.git/*.lock`（全新克隆，没有孤儿锁可删）；
② `origin/main` 早已 ＝ `32d0cbc` ＝ 接手分支 HEAD，**远端无需追平**，只有容器里没人用的本地 `main` ref 落后 5 个提交；
③ 无 `.env`、无 `tools/liaison/.venv`、不在内网 ⇒ **真实服务起不来**。
**TD-42 真实验证只能在 Mac 主检出做**（`0909AJ` 已立判据「必须在主工作区，worktree 里没有 `.env`」，云端容器比 worktree 还远一层）。

| # | 事项 | 谁做 | 状态 | 判据（怎样算完） | 不做会怎样 |
|---|---|---|---|---|---|
| **AS-1** | `[Mac]0910A` 正文落档后重开同号 —— ✅ **正文已由 Cowork·0909AU 重写并改名**为 `docs/openers/0910A-TD42真实验证与AT1b真实入站.md`（原名含「解锁追平」，那两步已不成立：锁交 `0910F`、`main` 已追平），提交交 `[Mac]0910F` | **Mac Desktop CC**（⛔ 云端 CC 做不了，见上方三条。⚠️ 答 `1a` 的「正文现场给」已由 Cowork 代写掉，他只需贴引用块） | ✅ **正文就位**；⏸ 待 `0910F` 提交 ＋ `0910B` 合回 `main` 后派发 | ✅ 判据已随改名订正：该文件存在、首行 ＝ `[Mac]0910A-TD42真实验证与AT1b真实入站`（⛔ **不再是**旧名 `…-解锁追平与TD42真实验证`，那份已挪进 `_to_delete/`）；`liaison_message` 由 0 变 1（AT-1b）；TD-42 断网六项判读跑完并回填 `docs/tech-debt.md` 或落 `docs/findings/`。**答 `1b` 的「串行接 AT-1」已按答 `1a` 拆成：AT-1a ⇒ `0910B`、AT-1b ⇒ 本号**，worktree 矛盾**已解**，⛔ 不再是待决项 | TD-42 真实验证继续悬着 ⇒ `docs/tech-debt.md` TD-42 那条「⛔ 在它还上**并真实验证通过**之前，不得再请任何专员发消息」一直挂着，汤丽萍发的每一条继续静默落空（**截至 2026-09-10 已白发 4 条**） |
| **AS-1b** | 主工作区两份**未跟踪**的旧稿 opener 处置：`docs/openers/0910A-解锁追平与TD42真实验证.md`（`0909AS` 出，号池 0910A 行仍写「正文未写」）＋ `docs/openers/0910B-SDK消息事件接线.md`（`0909AS` 出的旧稿，与已跟踪正本 `0910B-SDK-message事件接线.md` **同题不同名**） | ~~`[Mac]0910A` 接手时自决~~ ⇒ **Cowork·0909AU 已代办**（2026-09-10 13:5x） | ✅ **已处置**：两份旧稿均已 `mv` 进 `_to_delete/0910旧稿-20260910/`（Cowork 删不了文件，只能挪），`docs/openers/` 下 `0910*` 只剩 `0910B-SDK-message事件接线.md` 正本 ＋ `0910A` 新正文 | `0910A` 正文：要么提交它并把号池 0910A 行「正文未写」改掉，要么删掉重写；`0910B` 旧稿：**删**（正本已在 `main`）。`git status` 里这两份不再出现 | 两份同题 opener 并存，下一个人贴错一份就白跑一轮（`0910C` 撞号已演过一次） |
| **AS-2** | **引用式 opener 的被引文件必须与派号同一次落档并提交**——`docs/openers/<MMDDX>-<主题短名>.md` ＋ `OP-0820` 号池台账两处同时写 | 所有派发方（CC／Cowork 两端同等适用） | ✅ **已写进真源**（2026-09-10 答 `1a`）：`.claude/skills/kickoff/SKILL.md`「引用式 Opener」段新增判据「被引文件必须与派号同一次 commit 落档」＋ 本次实证 | 派号的那一条 commit 里同时出现 opener 正文文件与号池台账行；接手 session 不再出现「引用式 opener 指向不存在的文件」 | 引用式 opener 退化成**死链**：接手方既读不到正文、又在台账里查不到号 ⇒ 下轮必被重派或再扑空，且**全程不报错**（08-27 撞号 5 次是同一根因的第一次发作，本次是第二次） |

---

## 🆕 第十六批已收敛 ＋ Shao Peishen 五项裁决（2026-09-10 00:2x，`[Mac]0909AZ` 落档）

**第十六批六条泳道（`0909AK`–`AP`）全部合回 `main`，零红灯。**
报告：`.claude/handoff/lanes-20260909-234309-第十六批看护报告.md`（gitignore 内，未提交）。
销账 +7：TD-23 / 24 / 26 / 28 / 31 / 32 / 35；TD-29 部分已还。pytest 2066 → **2155，0 失败**。
⚠️ `0909AK` 的 `NO-SENTINEL` 是**哨兵漏打**，非失败（commit 已在 main、cherry 判真合 0），⛔ 不需重跑。

🔢 **号池**：`0909AP` 归第十六批·入站预演泳道；G-12·N-0 看门狗**已改判为 `0909AQ`**
（原取号时 AP 那行在 `OP-0820` 未提交段里，`0909AJ` grep 不到才误判空号）。**下一个可用号是 `0909AR`**。

### 五项裁决（Shao Peishen 2026-09-10 答 `1a，2a，3a，4a，5a`）

| # | 事项 | 谁做 | 状态 | 判据（怎样算完） | 不做会怎样 |
|---|---|---|---|---|---|
| **H-1** | `.51` 现网 `liaison_group_notify` **重建表**补上跨字段 CHECK（答 `1a`） | **另起 session**（触碰 `.51`，属发版决定·不可代） | ⏸ **待派发**。已登记 `docs/tech-debt.md` TD-28 条目 | 灰度真发**前**，`.51` 上该表建表语句含 `5ca1f50` 那两条 CHECK；当前 0 行则直接重建，有行则先导出再灌回 | 灰度真发时现网继续以旧结构收行，**不会有任何报错**；TD-28 在现网实际没还 |
| **H-2** | 名单外发送人的消息**仍归档**（答 `2a`） | — | ✅ **已闭合**，无代码动作 | 裁决已落进 `docs/findings/2026-09-09-入站带附件链路预演.md` §4.2 上方；`0909AP` opener 里「名单外 ⇒ ⛔ 不归档」那句**作废** | 下一轮有人按作废的 opener 口径去改 `inbound.py`，把合规裁定反向推翻 |
| **H-3** | 0 字节附件全链路无症状 → **登记 TD，随第 7 章通道层一起做**（答 `3a`） | 第 7 章通道层泳道 | ✅ **已登记 TD-41** | 通道层开工时一并处理；⛔ 在此之前不单独占泳道。8.6 当天人工兜底＝`find data/liaison/archive -type f -size 0` | 下载环节静默失败返回空文件＝**材料丢失**，而它与「用户真发空文件」长得一模一样、核对器报空清单 |
| **H-4** | TD-29 剩两条：**下一批单独派一条**「测试脚手架收编 ＋ `config.py` 文案」（答 `4a`） | 下一批泳道 | ⏸ **待派发**（已登记 TD-29 条目） | `conftest` 收编完成；`config.py:75` 不再复用 `MissingCredentialsError` | 服务正常运行时，运维会收到一句说「拒绝启动」的告警 |
| **H-5** | 订正 `docs/跟进信/README-跟进信清单.md` 里「`bf63e7a` 入库时把本行写成终态」那句失真（答 `5a`） | ~~另起 session~~ ⇒ **Cowork·0909AU 已代办**（2026-09-10 Shao Peishen 答 `1a` 授权动他的触碰区） | ✅ **已改，随 `[Mac]0910F` 提交** | 已改为「来源不明的未提交工作区状态」，台账与版本库口径归一。🔴 **顺带查实**：`git show bf63e7a:docs/跟进信/README-跟进信清单.md` 里本行是 **`🆕 待发`**、⛔ **不是**终态 ⇒ 原归因整句失真，版本库从未有过那个终态。已在 README 同处加第二条教训「给失真定的因，本身也要核 git 真身」 | 下次按这句去查 `bf63e7a` 会扑空；且「台账状态是机器判据」那条教训的**依据本身是错的** |

⚠️ **H-1 尚无 opener**（H-4 已并进 `[Mac]0909AR`；**H-5 已由 Cowork·0909AU 于 2026-09-10 做掉、随 `[Mac]0910F` 提交**）。派发 H-1 时从 **`[Mac]0910G`** 起取号，**取号当场登记进 `OP-0820` 号池台账并 commit**。

### 🆕 `0909AT` 打标即开班：需求已收敛、已立包（2026-09-10 10:2x CST，`[Mac]0909AT`）

**包**：`openspec/changes/liaison-reply-bridge-and-patrol/`（`intent.md` ＋ proposal／4 specs／design／tasks 0/33，`openspec validate --strict` ✅）。
grill 八问 Shao Peishen 当场全按推荐答（Q1 无在途不标不起活／Q2 多在途拒标告警／Q3 权限层 `acceptEdits`＋白名单，⛔ 不用 `--dangerously-skip-permissions`／Q3b 红线八项照单／Q4 主工作区／Q5 消息接线归 liaison 包／Q6 只 add 列出路径＋commit 不 push／Q7 该发送人任何入站算回件）。
🔴 **M2 查出的硬事实**：值守服务**根本没订阅 SDK `message` 事件**（`SUBSCRIBED_EVENTS` 只有三个连接事件，`test_this_chapter_wires_no_message_handling` 明令不接）⇒ 即使 TD-42 还了，`liaison_message` 仍是 0 行。这是比 TD-42 更靠前的前置，此前未登记。

| # | 事项 | 谁做 | 状态 | 判据（怎样算完） | 不做会怎样 |
|---|---|---|---|---|---|
| **AT-1** | `hr-wecom-aibot-liaison` 追加任务 **8.5bis：SDK `message` 事件接线**（订 `message`、回调只往队列放帧、值守线程调 `handle_inbound_message`、删 `test_this_chapter_wires_no_message_handling`）＋ 真实入站落库一条 | ✅ **已拆两条**（2026-09-10 答 `1a`）：**AT-1a** 代码接线 ⇒ `[Mac]0910B`（worktree ☑）；**AT-1b** 真实入站落库一条 ⇒ `[Mac]0910A`（主工作区，串行在 `0910B` 合回 main 之后） | ✅ **AT-1a 已落地**（2026-09-10 `[Mac]0910B`，commit `7636613`；`tasks.md` 8.5bis 已勾，进度 62/67→63/67，`validate --strict` 过；测试 934→952）／⏸ **AT-1b 待 `[Mac]0910A` 在主工作区验**。⚠️ **AT-1a 里的帧字段映射走了 fail-closed 支**（**TD-43**）：SDK 只命名 `body.msgtype` 与 `headers.req_id`，`msgid`／发送人 userid／会话 id 落在哪个键上无真实帧依据 ⇒ `frames.FIELD_PATHS` 留空、本帧不落库、只打一行**无取值**的帧键结构。⇒ **AT-1b 多一步**：照日志里那行结构把 `FIELD_PATHS` 四个键填上，`liaison_message` 才会由 0 变 1 | `SUBSCRIBED_EVENTS` 含 `message`（✅ 已满足，`7636613`）；`liaison_message` 有一条真实企微消息行（⏸ 未满足，归 AT-1b；⛔ 不许因为前半条已勾就把整条 AT-1 标成完成） | 8.6 灰度与本包 §0 门槛都过不了；汤丽萍发的每一条继续静默落空 |
| **AT-2** | design Open Question ①：章程 §〇 **第 ⑨ 条「回件附件疑似含候选人个人信息 ⇒ 不读入 prompt、只登记转人」的措辞与判据** | Shao Peishen | ✅ **已答 `1a`**（10:3x）：按起草措辞定稿，落 design D15 | P2 3.1 写章程时逐字取 D15 | — |
| **AT-3** | `HR_LIAISON_UNPACK_BUDGET_USD` 默认值（起草建议 5） | Shao Peishen | ✅ **已答 `2a`**：默认 5，落 design D16 | P1 2.9 写进 `.env.example` 注释 | — |
| **AT-4** | 本包开工顺序：§0 三门槛（TD-42 已还＋AT-1 接通＋真实入站一条）全勾 → P0→P1→P2→P3 各一条 worktree 泳道 → §5 真实起活实测（他重启服务＋等汤丽萍下一条入站） | Cowork 编排 → 泳道 | ⏸ 等 §0 | tasks §5 实测记录落 `docs/findings/` 后归档 | — |

#### ~~⚠️ AT-1 的 worktree 矛盾~~ ✅ **已解，⛔ 不必再拍**（`[Mac]0910A` 查出，同日由它自己出的 `docs/openers/0910B-SDK-message事件接线.md` 按下表拆法解掉）

> 🔴 **结论先行**：`0910B` 的 opener 正文已采用下表的拆法——**AT-1a 在 worktree 做**（其【设置】行 worktree ☑，【八】明令「⛔ 不做真实建连、不等真实入站」），**AT-1b 交回主工作区**（【九】收工要求单列一行「⏸ 下一步：AT-1b 真实入站落库一条（主工作区、worktree ❌，归 `[Mac]0910A`）」）。下面的分析保留作**判据由来**，⛔ 不要再当成待决项重提。

AT-1 原写「另起 session，worktree ☑」，但它的判据是两件**性质不同**的事：

| 子项 | 内容 | 需要什么 | 能不能在 worktree 做 |
|---|---|---|---|
| **AT-1a** | 代码接线：`SUBSCRIBED_EVENTS` 加 `message`、回调只往队列放帧、值守线程调 `handle_inbound_message`、删 `test_this_chapter_wires_no_message_handling` | 只要源码 ＋ 单测 | ✅ 能，且 CLAUDE.md 固定判据「写代码 ⇒ worktree ✅」要求就在 worktree |
| **AT-1b** | 判据「`liaison_message` 有一条**真实**企微消息行」 | `.env` ＋ `tools/liaison/.venv` ＋ 真实库 ＋ 值守服务真在跑 | ❌ **不能**——三者都被 gitignore 挡着，git 不带进 worktree（`0909AJ`／`0909AH` 已立此判据） |

⇒ 答 `1b`「`0910A` 串行接 AT-1」在 **AT-1b 上成立**（`0910A` 本就在主工作区、本就要重启真实服务），
但在 **AT-1a 上与「写代码走 worktree」相冲**：`0910A` 的【设置】是 worktree ❌ 不勾。
✅ **2026-09-10 Shao Peishen 答 `1a` 已拍：拆两条。** AT-1a ⇒ `[Mac]0910B`（worktree ☑，不需 `.env`）→ 合回 `main` → AT-1b ⇒ `[Mac]0910A` 在主工作区重启服务时顺带验。

**另两条同日裁决**：

- **流程口径（答 `1a`）**：`0910B` 走**单条直接 TDD**，⛔ 不走 `spec-to-plan` → `run-build`。
  已落真源 `03-工具链协作规则.md`「轻量通道」**第 4 条**：补已批准 spec 与代码之间的缺口 ⇒ 按「未改行为」走。
  ⚠️ 反向不成立——要动 spec 文本的一律回全流程。
- **排序（答 `2a`）**：🔴 **`0910B` → `0910A`，有依赖，⛔ 不可并行**。
  `0910B` 在 worktree 写代码并**合回 `main`** 之后，`0910A` 才能在主工作区拿到这份代码；
  `0910A` 一次重启服务把 **TD-42 真实验证**与 **AT-1b 真实入站**两件一起验完（⛔ 不重启两次）。


---

## 开场词（复制即用）

```
[Mac]0909Q-HR业务线接力
【设置】执行环境: Cowork ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（Cowork 无 worktree，只做文档、编排与派单）｜ 工作区: 仓库根（/Users/paulshao/Projects/HumanResource）｜ 派发: Cowork·HR业务线-接力0903B
读 /Users/paulshao/Projects/HumanResource/docs/session接力.md 恢复上下文，然后按【下一步】继续。
```

> 🔴 **Cowork 接力开场词同样是 opener，头两行＝标题行 + 【设置】行**（CLAUDE.md「CC 与 Cowork 两端同等适用」；09-09 Shao Peishen 指出漏了一次）。本 session 派出去的 opener，`派发` 一律写 `Cowork·HR业务线-接力0909Q`。

> 🔒 **首行只用于给读文档的人对编号，不指望侧边栏**。Cowork 侧的 session 名是摘要生成的，
> 首行无效（08-27 实测：`HR业务线-接力0827B` → 侧边栏 `HR业务线接力`）。⛔ 不要再改首行格式硬试。
>
> 🔴 **出号前必查号池台账**：`docs/openers/OP-0820-全量编排.md` 顶部「🔢 号池台账」。
> **Cowork 与 CC 共用同一号池**，`Z` 固定留给看护者。08-27 一天撞号 5 次，根因是
> 提交类 opener 只在聊天里派、从不落档 ⇒ 下轮 grep 不到 ⇒ 重派。**给出后当场登记**。

---

## 🔴 新 session 先看这三件（2026-09-10 10:1x 转场时的真实状态）

1. **唯一阻断项＝TD-42 连接假死**（详见【⑲】）。`0909AS` 正在跑，还它。
   🔴 **判服务在不在收，⛔ 不看仪表**——`state=connected` ＋ `stamp_at` 推进 ＋ `effect_log` 增长
   三个全绿也可能是死的（09-10 实证）。唯一判据＝**让白名单内的人发一条，看 `liaison_message` 是否 +1**。
   当前 `liaison_message=0`。
2. ⛔ **不得再请汤丽萍发消息**，直到 TD-42 还上并真实验证通过。她已白发 4 条。
3. **待跑的 opener**：`0909AR`（TD-29 收尾＋名单外归档口径订正＋提交 OP-0820 尾巴）、
   ~~`0909AT`~~ ✅ 已跑完（10:2x 立包，见上方「🆕 `0909AT`」段）。`0909AS` 在跑。

## 一、状态快照（2026-09-09 11:50，第十三、十四批跑完后）

| 项 | 现状 |
|---|---|
| main | `5e2573f`，**与 origin 同步（ahead 0）**。第十三批（`0909A–F`）与第十四批（`0909H–O`）共 60+ commit 全合入 |
| 工作区 | `0909R` 转场提交 ✅ `de4a32e`（含 CLAUDE.md `git stash` 禁令、两个 skill、09-09 裁决 findings）。之后 Cowork 侧又改了两处**未提交**：本文开场词补【设置】行、`.claude/skills/kickoff/SKILL.md` 加「Cowork 接力开场词模板」——**新 session 派出的第一条 CC opener 一并 add 提交**。➕ 09-09 12:2x 第十五批编排又落了三件未提交：`OP-0820-全量编排.md`（第十五批一节 + 号池 S–X 六行 + 看护者指针）、`docs/openers/0909X-泳道批次看护.md`（新）、`docs/findings/2026-09-09-Shao-Peishen-裁决-留存冲突B与TD20改法.md`（新）——`0909X` 的【二】已把五条路径写全（含 kickoff skill）。⚠️ `.claude/handoff/` 在 `.gitignore` 里，看护报告只在本机 |
| `.51` 代码 | ✅ **已发版 `0aa1af4`**（`0909P` 2026-09-09 **六次发版成功**）。依据＝Shao Peishen 2026-09-09 23:32 CST 回「发」。范围**发车前现算** `95b298c..main` ＝ **5 文件 / +276 −7**：`.env.example` 9 ／`app/outbound/delivery.py` 2（TD-17）／`app/storage/idempotency.py` **94**（撞键短路 `8ea7bd2`+`2853116` ＋ TD-33 `25d8715`）／`pyproject.toml` 12 ／`scripts/install_lane_launcher.py` 166（Windows 上惰性），与 `0909AJ` 代跑现算逐项一致、`app/` 无多出项。快照 `C:\apps\backups\20260909-2341`（只 `app\`）。前置 5 项全过（含 `requirements.txt` diff 为空 ⇒ 纯 sync，⛔ 未装依赖、未重跑 `deploy-server.ps1`）。冒烟 ①–⑤ **全过**：首页 200 ｜ `GET /api/jobs` JSON 列表 200（非 405）｜ 新进程 PID `1480` 于 `23:42:43` 启动、其后**零 Traceback**（本次尾 60 行字面亦通过）｜ 🔴 目标验收 `Select-String 'short-circuiting duplicate effect_key'` ＝ **1** ⇒ 撞键短路修复现网生效 ｜ 裸跑巡检 **`EXIT=0`**。🔴 **⛔ 但「重复 confirm」未彻底闭合**——`0908K` 终审三条观察项逐条仍成立（单连接 rollback 是连接级／`human_review` 主键冲突走另一路径仍 500／只认 `sqlite3.IntegrityError`，迁 Postgres 须换 `psycopg.errors.UniqueViolation`）。详见 `docs/audit-and-outbound-ops.md` §五「六次发版记录」。<br>——以下为 `0908K` 五次发版历史记录，保留不覆盖——⚠️ **实际范围远大于该轮 opener 所述的「只发巡检 CLI 编码修复」**——`7a48a19..95b298c` 对 `app/ scripts/` ＝ 15 文件 / +905 −118，一并上线 **WBS 2.3 备用供应商切换**、**WBS 2.5 重试耗尽转人工**（两个新 `effect_*` 节点）、**tasks 5.3 离题轮不留岗位记录/丢弃**；⇒ 今后发版 opener 的「这次发什么」必须由 `git diff <现网 HEAD>..main` 现算。快照 `C:\apps\backups\20260908-1420`（`app\` 126 文件完整；`data\` 因 `demo.db-shm` 被占用中断，不影响回滚——回滚只用 `app\`）。纯 sync，⛔ 未装依赖、未重跑 `deploy-server.ps1`。冒烟 4 项：首页 200 ｜ `GET /api/jobs` JSON 列表 ｜ 新进程 PID 9600 于 `14:22:36` 启动、**其后零 Traceback**（尾 60 行里那段 `Traceback` 时间戳为 `13:18:32`，属上一版进程的历史错误，已取证非本次回归，未回滚）｜ 🔴 **不带 `PYTHONIOENCODING` 裸跑巡检 `EXIT=0`**、6 条断言全过、断言四豁免 7 条 ⇒ `19b8937` 修复现网生效。⇒ **`0908J` 那条「加 `PYTHONIOENCODING=utf-8` 才得真结果」已作废，⛔ 不要再加环境变量**。🔴 查实遗留缺陷（早于本次发版、未修）：重复 `confirm` 时 `idempotent_effect` 抛 `sqlite3.IntegrityError: UNIQUE constraint failed: effect_log.effect_key` 而非短路，用户可见 500（恒等式未破）→ ✅ 第十二批 `0908S/T` 已修合入（撞键即 rollback 业务写、按已执行返回），**已随 `0aa1af4` 于 09-09 上线 `.51`**。ℹ️ 口径订正：`.51` 应用日志真实路径是 `logs\app.log`，**不是** `data\logs\app.log`。详见 `docs/audit-and-outbound-ops.md` §五 ｜ `0908B` 失败回滚见 `docs/findings/2026-09-08-51四次发版回滚.md` |
| pytest | main 侧 **1941 passed / 4 skipped / 0 failed**（09-09 10:2x `0909Y` 真身核验；4 条 skip 全是刻意离线/SDK 只装在 tools venv）。⛔ 别抄进 opener 当基线，见【四】 |
| 生产 | `.51:8095`，`/hr/recruit-agent`，服务正常 |
| worktree | `/private/tmp/wt-0908M`、`/private/tmp/wt-0909B`（分支均真未合 0，可安全清理；两批看护者按 opener 都没清）；旧分支若干、真未合 0 |

**变更包进度**

| 变更包 | 进度 | 剩什么 |
|---|---|---|
| ~~`ai-audit-trail-and-outbound-gate`~~ | **53/53 ✅ 已归档** | `openspec/changes/archive/2026-09-04-ai-audit-trail-and-outbound-gate`；specs 折进 `ai-decision-audit` + `outbound-approval-gate` |
| `m1-intake-quality-fixes` | **68/69** | 8.4 ✅（Shao Peishen 09-03 页面实跑，job `51b225f1`，0903L 取证）；只剩 **8.9**（归档，须 `m1-job-profile-intake` 先归档） |
| ~~`outbound-retry-audit-trace`~~ | **15/15 ✅ 已归档** | `archive/2026-09-04-outbound-retry-audit-trace`；delta 已合进 `outbound-approval-gate/spec.md`；修复已随 `3f59842` 上 `.51` |
| `hr-wecom-aibot-liaison` | **62/66** | 第 1–7 章 ✅ ＋ 8.1–8.5 ✅（第十三批：4/5/7 章；第十四批：6 章 + 8.1–8.5 + TD-14/15/16/18）。**只剩 8.6 单机灰度 / 8.7 加汤丽萍 / 8.8 一周观察 / 8.9 归档——全部要 Shao Peishen 亲自做**。灰度前置（见【二】⑮）：新 aibot 凭据进 `.env`（他 09-09 说去建）、名单 userid、`tools/liaison/.venv`、TD-19 真实建连表面实测、TD-20 改法拍板、launchd 安装（他 Terminal 跑 `tools/liaison/scripts/install_launchd.py`）。⚠️ 全部代码路径全程 fake，**尚无一次真实建连、真实投递** |
| `m1-job-profile-intake` | **60/72** | 第十批：9.6 ✅、硬门槛 1.2b/5.8/5.9 ✅、账目对齐 4 条 ✅；0905A：Web 8.1/8.2/8.4 ✅（`05e90cc`）；**第十一批：2.3+2.5 ✅（`7ef4bb2`）、5.3 ✅、4.4 ✅**。剩 12 条，🔴 **已全部有去向，本包内无待做代码**：**C 类 11 条已移出**——8 条企微（1.5b/3.x/9.2）→ `hr-wecom-aibot-liaison`（`0908A` 已立包），3 条调度（1.7/5.6/6.8）→ **调度基础设施（待立项）**（`0908I` 09-08 落档，依据 Shao Peishen「按推荐」裁决）；**D 类只剩 9.1**（10 个真实岗位重跑、HR 与业务经理双方评估，**要真人参与**）。⇒ **归档判定权在 Shao Peishen**，⛔ 代理人不代拍 |

**已跑完的批次**：第四至第十四批。第十三批报告 `lanes-20260908-200757-第十三批看护报告.md`，第十四批报告 `lanes-20260909-081142-第十四批看护报告.md`（`lanes-20260908-235021-*` 是同批 launchd 发车失败那半份）。
看护报告都在 `.claude/handoff/lanes-*-看护报告.md`。

---

## 二、下一步

### ~~① 提交工作区 2 文件~~ ✅ `0903C` 已跑完（`3101c99`）

### ~~② 发 `[Mac]0903A`~~ ⏸ 已跑，**仍未闭合**——不是路径，是 `.51` 没部署过 08-27 后的代码

`0903A` 结果（`e8ef150`）：`.venv` 路径订正有效；四步第 2/4 步报
`ImportError: cannot import name 'is_candidate_outbound_enabled'`；§2.2 链校验报
`ModuleNotFoundError: app.audit`。产线开关已绕开应用层直接读文件确认为 `false`（安全）。
**结论：§5-3 在这份部署上无论怎么跑都不可能过，先发版。**

### ~~② `[Mac]0903D`：`.51` 阶段 D 日常发版 ＋ 发版后补跑 §5-3 四步~~ ✅ 已跑完，**已闭合**（`d104249`）

结果：发版 `HTTP 200`，冒烟 4 项全过，§5-3 四步与 §2.2 链校验全过（详见
`docs/audit-and-outbound-ops.md` §五第 2/3 项，均已标 ✅）。第 1 项（备份任务）
仍 ⏸，本轮只做一次性快照 `C:\apps\backups\20260903-1003`。以下为发版前的原始判据，留作记录：

🔴 发版与开关四步均为不可代项。**Shao Peishen 2026-09-03 09:55 已在 0903B 会话回「发」**，
opener 全文在该会话里；若 CC 侧还没跑，去 0903B 会话复制整块。发版判据（09-03 Cowork 侧核过）：

- `requirements.txt` 自 08-19 起无改动 ⇒ 只需 `sync-to-server.sh`，⛔ 不用重跑 `deploy-server.ps1`
- 新配置字段全有默认值，`.env.example` 无新增 ⇒ 服务器 `.env` 不用动
- SQLite 加列走 `_ADDED_COLUMNS` 幂等迁移，启动时自动补 ⇒ `data/demo.db` 不用手工迁
- 发版前先快照 `app\` 与 `data\` 到 `C:\apps\backups\<时间戳>\`，不过冒烟即回滚
- 影响面：3 位 pilot 业务经理会看到 `m1-intake-quality-fixes` 60/69 的新行为（这是设计目的，
  第 8 章"真实会话回放与上线"本来就要它上 `.51`）

### ~~③ `0903D` 报「§5-3 已闭合」后，再发 `[Mac]0903E` U5 收口~~ ✅ 已跑完（合并 commit `06a55d2c`，pytest 675→720）

结果：`finishing-a-development-branch` 把 `worktree-audit-u5-queue-and-wiring` 合回 `main`
（`--no-ff`，merge commit `06a55d2c`）。`git rev-list --count main..<分支>` = 0，确认真合
非 rebase 假阳性。合并后全量 `pytest` **720 passed / 0 failed**（合并前基线 675）。
第 5 章 9/9 已回勾，**未归档**（第 6 章 0/7、第 7 章 4/6 未完，见 `tasks.md` 顶部进度行）。
分支已 `git branch -d` 删除（worktree 本就已不在）。

### ~~④ 第七批~~ ✅ 已跑完（4/4 合入 main 并已推送）

结果：F plan 5 Task（`18f85ad`）；G U6 7/7 合入（`e5e8e33`，终审 5 次变异抓到 2 处真缺口后才闭合）；
H 三段真实会话回放 18 轮全通、未溯源字段 0/18（`2239b90`）；I 8.6/8.7/8.8 回填（`d19625f`）。
superpowers 两条都**没调到**，均按磁盘 SKILL.md 手工走完（第 4 次靠运气）。
🔴 回放实测**单轮 LLM 延迟均值 33.9 / 48.5 / 65.1 s，最大 132 s**——pilot 抱怨的"等待"有了第一个数，修复前无基线不可比。

### ~~⑤ `0903J` 收尾~~ ✅ 已跑完（`b86db65`，已推）

### ~~⑥ 三条裁决~~ ✅ K/L 都跑完（`a15862d` / `feb49d6`，**未推**）

| 裁决 | 落点 |
|---|---|
| TD-9 **走 `openspec-propose`** | `[Mac]0903K`：立正式变更包（只出 proposal/specs/design/tasks，不写代码），顺带提交真源改动。正文 `docs/openers/0903K-TD9立变更包.md` |
| 8.4 ＋ U6 巡检 **都要上 `.51`** | `[Mac]0903L`：再发一次版（main 当前 HEAD，含 U6）→ 巡检 CLI 对真实库跑 → 他在页面跑 8.4 → session 从库里取证回勾。🔴 发版不可代，本条裁决即授权。正文 `docs/openers/0903L-51二次发版与U6巡检与8.4取证.md` |
| 回放类任务收口前**拷走 `data/` 产物** | ✅ 已落真源：`.claude/skills/run-build/SKILL.md` 收口第 2 步、`.claude/skills/lane-dispatch/SKILL.md` ③ 第 4 条。`0903K` 提交 |

结果：K 立了 `outbound-retry-audit-trace`（2 章）并提交全部真源改动；L 二次发版 `b86db65`（含 U6，快照 `backups\20260903-1428`，冒烟 4/4），
巡检 CLI 首跑 `EXIT=2`＝JSONL 镜像尚不存在（现网无外发调用方，不是失败），8.4 由 Shao Peishen 本人页面跑通、取证三判据全中。

### ~~⑦ 第八批~~ ✅ 跑完（N/O/P OK，Q PARTIAL＝归档顺序反了、正确刹车）

结果：U7 53/53（`6e1272c`）；TD-9 修复全部合入（`bf45370`），pytest 786→842；两条 plan 落档。
过程：O 两次撞 Anthropic 529 上游事故（status 页有 incident），事故解后重跑收敛；Auto Mode 分类器拦 `nohup`，
Shao Peishen 亲自跑 `scripts/allow_run_lanes.py` 加白名单后解除。superpowers 四条全没调到、全手工走完（第 7–10 次）。
🔴 **归档没做**：retry 包的 delta 是对 ai-audit 包 ADDED 需求的 MODIFIED，主 spec 要等 ai-audit 归档才存在——opener 顺序写反了，Q 没产半成品。已触发「归档时限」规则，`0904A` 当场补。规则已写进 run-build skill 收口第 5 步。

### ~~⑧ `0904A`~~ ✅ 跑完（`7faf682`）：两包归档、TD-10 登记、wt-U7 删；三处真源改动一并入库

### ~~⑨ 第九批~~ ✅ 跑完（B/C/D/E 全合入，pytest 842→1008，`.51` 三次发版 `3f59842` 含 TD-9 修复）＋ `0904F` 9.6 规格更新完

结果：第 6 章确认断点 9 条 + 1.4 + 9.3 合入（`be6322a`），第 7 章 7.3/7.5/7.7 合入（`6ae57bf`），45/72。
过程：Auto Mode 分类器**在白名单齐全时仍拦** `nohup run-lanes.sh` 与 `sync-to-server.sh`——推翻 09-03「白名单能解决」的归因；
唯一验证有效的路径＝把命令贴成 bash 块、Shao Peishen 在 CC Desktop 点 Run（已写进 lane-dispatch skill）。C/E 各撞一次预算上限（$25）跳过全分支终审。
C 发现断言四豁免线用 `created_at` 有洞 → Shao Peishen 裁决「现在修」→ `0904F` 出了规格（tasks 9.6）。

### ~~⑩ 第十批~~ ✅ 跑完（G/H/I/J/K/L OK，M 预算耗尽）＋ ~~`0905A`~~ ✅ 续跑收口（`05e90cc`，09-07）

结果：9.6 合入（`3b3aac7`）、硬门槛 1.2b/5.8/5.9 合入（`46b84b9`，含 hard_requirement 新表）、账目对齐 4 条、Web 8.1/8.2/8.4 合入（`05e90cc`，登记 19 条落地偏离与 parked）。56/72，pytest 1008→1061+。
🔴 0904I 留步：9.6 上 `.51` 后，留痕上线前的历史行会从"被豁免"翻成断言四违例（巡检 EXIT=1）——**预期结果**，处置＝人工逐条核实，⛔ 不挪豁免线。✅ **已闭合（`0908J` 09-08）：9.6 已随 `7a48a19` 上线，巡检 `EXIT=0`，预告的违例未发生**——`0904F` 把豁免线改成决策时间戳后，7 条历史行被正确豁免而非翻成违例，故无「待核实清单」可抄（⛔ 不是漏跑）。7 条明细存证于 `docs/audit-and-outbound-ops.md` §五。
另一条线：`0908A` HR 企微值守机器人 `openspec-propose`（09-08 09:31，由别的 session 派）——它承接的正是本包 8 条 C 类企微条目。

### ~~⑪ 09-08 上午~~ ✅ 全清：第十一批 6/6 合入（2.3/2.5/5.3/4.4，intake 60/72）；`0908B` 缺 tzdata 回滚 → `0908J` 重发成功 `7a48a19` → `0908K` 五次发版 `95b298c`（巡检裸跑 EXIT=0）；`0908I` 三条移出 C 类
新发现：① 现网缺陷——重复 confirm 时 `idempotent_effect` 撞 UNIQUE 抛 IntegrityError 而非短路（500，恒等式未破）→ 第十二批修复泳道；② `run-lanes.sh` 静默错配（awk UTF-8 locale）已由 `2f14d2c` 钉 `LC_ALL=C`，护栏交第十二批机制泳道；③ 🔴 **备用 LLM 供应商选型（采购，不可代）**——2.3 链路已合入但 `LLM_FALLBACK_*` 全空等同未启用，等 Shao Peishen

### ~~⑫ 第十二批~~ ✅ 跑完（9/9，值守 18/66，pytest 1233→1378，幂等撞键修复合入，run-lanes 条目自检上线）

🔴 事故：`0908R` 在泳道里改 `run-lanes.sh`，运行中的 bash 按旧字节偏移续读新文件 → 编排器自我损坏、六条被派两遍（产出未受损，泳道自己识别成复核）。
根治＝脚本开跑先自拷贝到临时目录再 exec（`0908U`，永不进泳道，发车前单独跑）。规矩：**改 `run-lanes.sh` 的 opener 永远不进它自己驱动的泳道**（已写进 lane-dispatch skill）。
SDK 结论：`wecom-aibot-python-sdk 1.0.2` 在 3.14 判据 A/B/C 全过，走路线①，第 4–7 章按 SDK 正路排。

### ~~⑬ 第十三批~~ ✅ 跑完（6/6，值守 18→47/66，pytest 1378→1663；B 的 BUDGET-HIT 是虚惊）

- `0908U`：run-lanes.sh 自拷贝执行 + 零条目路径自检可达 + TD-17（`delivery.py:12` 非法转义）。CC 新开、不勾、单独跑。正文 `docs/openers/0908U-run-lanes自拷贝执行与TD17.md`
- 第十三批（编号按发车日 0909，0908 字母池只剩 U–X）：归档队列泳道 `0909A→B→C→D`（第 4 章消息归档 → 第 5 章任务队列，同写 `storage/effects.py` 故串行）∥ 连接泳道 `0909E→F`（第 7 章连接生命周期，独占 `session*` `alerts*` `__main__.py`）。dry-run 预期 A20/B23/C19/D23/E20/F23，Σ=6。正文 `docs/openers/0909Z-泳道批次看护.md`
- 🔴 09-08 晚 Shao Peishen 问「每批都要点 Run？」→ 是（09-04 起分类器拦看护者起脚本，白名单无效）。根治＝`0909G` launchd WatchPaths 触发器：看护者写 `.claude/handoff/launch/<ts>.request` 文件即发车，起进程的是 launchd 不是 Claude。装 launchd 那一步他在 Terminal 跑一次 `scripts/install_lane_launcher.py`
- 📅 **09-09 Shao Peishen 说三件明天给**：备用 LLM 供应商 key / 企微 aibot BotID+Secret / 群 webhook。🔴 接收口径：**凭据不进聊天、不进 git**——他自己写进 `.env`（Mac 本机 `.env`：`HR_LIAISON_BOT_ID` `HR_LIAISON_BOT_SECRET` `HR_LIAISON_GROUP_WEBHOOK`；`.51` 的 `.env`：`LLM_FALLBACK_*` 四项，名字以 `.env.example` 为准），Cowork/CC 只出「验证存在性 + 连通性」的 opener，⛔ 不读值、不回显、不写进任何被跟踪文件
- ~~等 Shao Peishen 的三件~~ **09-09 已裁决**（落档 `docs/findings/2026-09-09-Shao-Peishen-裁决-无备用LLM与aibot独立注册.md`）：① 备用 LLM **不要了，只用 DeepSeek**——`LLM_FALLBACK_*` 四项两台机器都留空，代码零改动（不配＝无备用，主家故障走 2.5 转人工）；② aibot **⛔ 不与 Windows 共用**——官方文档「连接数量限制」：同一机器人同一时间只能一条长连接，新连接踢旧连接（design D1 坐实）→ 他在企微后台**新建**一个 aibot 给 Mac，**用超级管理员账号建**（`from.userid` 才是明文，否则是加密 userid）；③ 群 webhook 他自己写进本机 `.env`。🔴 09-09 他把 Windows 侧 BotID/Secret 与 webhook 贴进了聊天——Cowork 侧未复述、未落档；那对凭据 Mac 侧⛔ 不用
- 准入名单 `userid`：他 09-09 说「汤丽萍的 chatid 好像已经存档」（企业AI转型仓 `6-人才与组织/部门AI专员跟进/README-跟进机制与命名约定.md`《企微 chatid 名录》，三种格式并存）。⚠️ 名单认的是新 Mac 机器人看到的 `from.userid`，不是 Windows 机器人的单聊 chatid；**以企微管理后台通讯录「账号」为准**填 `tools/liaison/config/whitelist.yaml`，存档值只作核对（相同即一致，不同以通讯录为准）。两人（汤丽萍、邵培申）都要填，留空＝fail-closed
- 第十三批 09-08 20:07 发车（点 Run，PID 见 `0909Z` 那个 session）；20:3x 时 A（第 4 章计划）OK、E（第 7 章计划）PARTIAL 已摘标注，B/C/D/F 在跑。报告等 `0909Z`

### ~~⑭ 第十四批~~ ✅ 跑完（8/8，值守 47→62/66，pytest 1663→1941；launchd 发车只成一半、退路点 Run）

- Shao Peishen 09-08「第十四批任务可以现在先理出来吗？能开尽开，不要被泳道上限束缚」→ 五条零重叠泳道：**群通知** `0909H→I`（第 6 章，真实投递开关不在内）∥ **留存** `0909J→K`（8.1–8.2）∥ **日志** `0909L→M`（8.4）∥ **运行守护** `0909N`（8.3 launchd 模板 + 8.5 守护测试，轻量单条）∥ **技术债** `0909O`（TD-14/15/16/18，轻量单条）。dry-run 已在 Cowork VM 核过：5 泳道 8 条，Σ=N=8（`--only` 生效）
- 发车参数（看护者写进 `.claude/handoff/launch/<ts>.request`）：`--full-auto --yes --only 0909H,0909I,0909J,0909K,0909L,0909M,0909N,0909O --max-parallel 5`。`--only` 隔开第十三批可能残留的标注；`0909Y` 会自己等第十三批 `run-lanes.sh` 退出再发车，**现在就能贴**
- 🔴 **TD-15 裁决**：按「同一份名单内容只记一次 ERROR（内容哈希去重），内容变了再记」处理，不降级、不缓存名单。他贴 `0909Y` 即认可；不认可则从 `--only` 去掉 `0909O`
- 唯一共享文件 `tools/liaison/__main__.py`：留存加末尾子命令、日志加 main() 首行，各一处；真身核验第 8 条查"两头都在"
- 不进本批：8.6–8.9（他亲自）、第 6 章真实投递（8.6 时验）、~~`.51` 六次发版（`0909P`）~~ ✅ **已于 2026-09-09 23:43 发版完成，现网 `0aa1af4`**、明天三件凭据的验证 opener（他写进 `.env` 后另出）
- 正文：`docs/openers/0909Y-泳道批次看护.md`（看护）、`docs/openers/0909P-51六次发版幂等撞键修复.md`（发版）；块与号池在 `OP-0820-全量编排.md` 第十四批节

---

### ⑮ 09-09 起：等他三样东西，然后第十五批 ＋ 8.6 灰度

**A. 要 Shao Peishen 拍的（不可代）** —— ✅ **两条 2026-09-09 中午已拍**，落档 `docs/findings/2026-09-09-Shao-Peishen-裁决-留存冲突B与TD20改法.md`：冲突 B 取方案 ②（终态队列行连归档一起清）；TD-20 取改法 ①（启动不闭旧窗，`on_connected` 才闭）。两条均已排进第十五批（`0909T` / `0909U`），下面原文留作判据记录。

1. 🔴 **冲突 B：名单内消息的归档在 FK 下永远清不掉**（`0909K` 登记 P1，未替他拍）。`liaison_task.msgid → liaison_message(msgid)` 无 `ON DELETE`，opener 又写死队列行永不删 ⇒ 汤丽萍/邵培申的归档全进 `blocked_by_queue` 桶，只有名单外发送人的归档会被 180 天清理。属个人信息留存期问题。
   - ① 保持现状（名单内归档永久保留，只报计数）——**不推荐**，与 D13「180 天」和 proposal 合规说明相悖
   - ② **推荐**：队列行到终态（`✅ 已推送`）且超 180 天的，连同其归档一起清；`🆕 待发` / `⏸ 暂缓` 的不动（仍是活台账）。D13「队列行不参与自动清理」改成「非终态队列行不参与」，spec + design 同步改一句，代码在 `retention.py` 加一个桶
   - ③ 台账行删除前把 `liaison_task.msgid` 置空（去掉 FK 依赖）——队列行失去回指，5.5「回指来源消息」被打破，不推荐
2. 🔴 **TD-20 三选一**（`0909F` 留步；8.6 前必须定）：① 启动时不闭旧窗口、等 `on_connected` 真连上再闭 ② 启动另开新窗专等 `on_connected` ③ 接 `run_forever` 的 `on_attempt_failed`。**推荐 ①**：改动最小、告警自然带真实恢复时间、不新增窗口类型；③ 可作为 ① 之上的加强，不单独选。

**B. 他自己要做的三样（09-09 已说去做）** —— 进度 09-09 12:3x：
- ✅ **Mac 端 aibot 已建**，`HR_LIAISON_BOT_ID` / `HR_LIAISON_BOT_SECRET` 已写进本机 `.env`（权限 600，`.gitignore:2` 忽略，`git status` 零条目）。🔴 **口径变更**：他 09-09 12:3x 本人放宽了「凭据不进聊天、只由他本人写」那一条（原话「这个不是敏感信息，只为公司内部群沟通，不用担心」）⇒ **Cowork 可代写进本机 `.env`**。⛔ **不变的仍然不变**：凭据不进 git、不进任何被跟踪文件、不回显取值、不写进 `.env.example` 与 opener 正文
- ✅ `HR_LIAISON_GROUP_WEBHOOK`（「人力AI保障群」）已于 09-09 12:4x 一并写进 `.env`。三个变量齐（len 35/43/89）。守卫已核：`.env` 权限 600、`gitignore:2` 命中、被跟踪文件里真实 `qyapi.weixin.qq.com` webhook **0 命中**（`tools/liaison/tests/test_notify_*.py` 里那两处是 `example.invalid` 假值，判据要认真实域名——与第十四批报告分歧②一致）
- ⏸ `whitelist.yaml` 两人 `userid` **仍是空串**（留空＝fail-closed，谁都进不来）；以企微管理后台通讯录「账号」为准
- ⏸ `tools/liaison/.venv` **仍不存在**（`install_launchd.py` 对此 fail-closed）。🔴 **Cowork 代建不了**：device_bash 跑在 Mac 上的一个独立 Linux VM 里，在挂载目录建 venv 会往 macOS 路径里塞 Linux 解释器。⇒ 必须由 macOS 上的 CC 建，已写成 `0909AA`（引用式，`docs/openers/0909AA-凭据验证与liaison-venv.md`）
- ℹ️ **口径订正**：`probe_ws_surface.py` 的 docstring 明写「⛔ 只做内省：不建连、不发消息、不需要任何真实凭据」。本文此前写的「真实建连一次」**是错的**，⛔ 不要据此改脚本或给它加凭据参数。TD-19 的真正销账要等 8.6 用真实凭据把 `client.run()` 端到端跑通

- 企微后台**新建** aibot（超管账号建；API 模式选长连接；加进「人力AI保障群」），BotID/Secret 与群 webhook 写进本机 `.env`（`HR_LIAISON_BOT_ID` / `HR_LIAISON_BOT_SECRET` / `HR_LIAISON_GROUP_WEBHOOK`）。⛔ 凭据不进聊天不进 git；Cowork 只出「验存在性 + 只读探针」opener
- 通讯录里抄两人「账号」，填 `tools/liaison/config/whitelist.yaml` 的 `userid`（留空＝fail-closed）
- 他回「写好了」后 → 出 opener：验三个变量非空（不回显）→ `tools/liaison/.venv` 建起来装 `requirements.txt` → 跑 `tools/liaison/scripts/probe_ws_surface.py`（TD-19 只读探针，真实建连一次，新机器人不影响 Windows）→ 跑通则改常量 + 落 findings，跑不通则留步（`SdkSurfaceUnverifiedError` 就是设计的拒启动）

**C. 第十五批候选** —— ✅ **已编排发车（见下面 ⑯）**。下表留作取舍记录：六条候选里五条进了本批；队列债（TD-23/24）因「零生产调用方、8.x 推送接线前还即可」推到第十六批。

| 泳道 | 内容 | 触碰区 |
|---|---|---|
| launchd 修复 | `scripts/install_lane_launcher.py` 生成的 plist 加 `AbandonProcessGroup=true` + `EnvironmentVariables.PATH`（含 `~/.local/bin`）；单测断言两键在位；他 Terminal 重装一次。验收＝写一份 `.request` 后 `lanes-*` 目录真建出来 | `scripts/`、`tests/test_lane_launcher.py`（⛔ 不碰 run-lanes.sh） |
| 留存收口 | 冲突 B 按裁决落地 ＋ TD-30 日志时间维度（独立 `HR_LIAISON_LOG_RETENTION_DAYS` 默认 30，⛔ 不复用 180） | `tools/liaison/retention.py`、`logsetup.py`、spec/design 一句 |
| 会话 | TD-20 按裁决改 `session.py` 状态机 ＋ TD-21 礼貌回复 at-most-once | `tools/liaison/session*`、`inbound.py` |
| 队列债 | TD-23（`defer_task` 读状态）＋ TD-24（`mark_task_pushed` 第二道防线）——当前零生产调用方，8.x 推送接线前必须还 | `tools/liaison/queue.py` |
| 群通知债 | TD-25（非限流错误不落 `pending_resend`）＋ TD-27（`MODE_REJECT` 不发空 markdown）＋ 待重发驱动器（`0909H` 留步说属第 8 章，但 tasks.md 无此条 → 先 openspec 补一条 8.x 再建） | `tools/liaison/notify/` |
| 幂等基座 | TD-33：`app/storage/idempotency.py` 非 `IntegrityError` 也回滚——影响所有 effect，铁律 1 近亲；改完随 `0909P` 一起上 `.51` ✅ **已于 09-09 随 `0aa1af4` 上线** | `app/storage/idempotency.py`、`tests/` |

⛔ 不进泳道：8.6–8.9（他亲自）；`0909P` 发版（等「发」；建议等幂等基座泳道合入后再发，一次带上 TD-33，「这次发什么」现算）；launchd 重装与 liaison launchd 安装（他 Terminal）。
下一批看护者判据要收窄两处（`0909Y` 报告分歧①②）：`.env.example` 允许注释行、只禁取值；webhook grep 只认 `qyapi.weixin.qq.com` 真实域名。

### ⑯ 第十五批（`0909S–W`，看护者 `0909X`）· ✅ **已跑完（5/5，3 OK ／ 2 PARTIAL，零 FAIL）**

报告 `.claude/handoff/lanes-20260909-124030-第十五批看护报告.md`；摘牌提交 `21db086`。
**六条 TD 全销**：TD-20（`08d8784`）／TD-25+TD-27（`26986e8`）／TD-30+TD-34（`ac686d1`）／TD-33（`25d8715`）。两条 PARTIAL：`0909S` 等他重装 launchd（未闭）；~~`0909W` 等 `0909P` 上 `.51`~~ ✅ **已闭合**——TD-33（`25d8715`）随 `0aa1af4` 于 2026-09-09 23:43 上线 `.51`。

🔴 **本批产出的三件要接着处理的**：
1. **新 TD-35**（`0909T` 当场登记）：`assert_effect_log_identity` 对 `liaison_task` 的严格恒等与裁决一冲突——终态队列行被连带清掉后，`effect_log` 行数必然大于 `liaison_task` 行数，机器守卫会因**完全正当**的理由变红。`0909T` 没擅改共享守卫（该文件归别的泳道），改为加 `assert_retention_accounting` 第二条记账等式兜底并用 `pytest.raises` 钉成可见。不阻塞 8.6
2. 🔴 **发车机制的新实证**：分类器拦的是 `.claude/handoff/launch/` **这个路径本身**（同一行内容写别处成功）⇒ `0909S` 修好 plist 并不能让 A 路通，**装完之后看护者仍然写不了 `.request`**。⛔ 不要再把「装 launchd」当作根治
3. **未提交**：`openspec/changes/lane-launch-armed-scan/`（proposal + design + specs，某条泳道/看护者起草的）——把发车触发换成「launchd 定时扫**已提交的**编排文件 ＋ 一枚只有 Shao Peishen 的 git 身份能签的武装令牌」，授权动作从「发车那刻点 Run」前移成「编排完成时签一次」。🔴 **要他拍**：这改的是发车机制的形状，且 proposal 自己写明「扫编排文件就能通」属**未验证的设计推测**

### ⑲ 2026-09-10 上午·🔴 TD-42 连接假死（当前唯一阻断项）＋ win 端机制核验

**🔴 最要紧的一条：值守通道假死，而且所有仪表都显示正常。**
- 实测一：2026-09-09 23:27 → 09-10 09:38 整整 **10 小时**零接收（库 0 行、归档空、日志无新行），
  期间 `liveness.state=connected`、`stamp_at` 持续推进
- 实测二（决定性）：09:38 `pkill` 重启、3 秒内重连；10:0x **Shao Peishen 本人**（在白名单内）在群里 @ 机器人发一条；
  10:05 核 `liaison_message` **仍为 0**。⇒ **重启后 27 分钟内再次假死，可复现**，且**已知有消息真发出且没到**
- 根因（日志逐字）：看门狗探到假死、也报了警，但「本次连接从未触发过任何 SDK 事件」⇒ 拿不到事件循环把手 ⇒
  **有判据、无把手**，停不下来；`run_forever` 那层同样接不上手
- ⚠️ **与 TD-39 无关**：TD-39（`error` 打断重连链）已修好且已验证（09-09 23:03 断线 3 分 1 秒自愈为证）。本条是另一条路径
- 🔴 **新判据（今后照此判，⛔ 不再看仪表）**：`state=connected` ＋ `stamp_at` 推进 ＋ `effect_log` 增长，
  **三个全绿也不能证明在收**（今天这次三个全绿、通道是死的）。唯一可信的即时判据＝
  **让白名单内的人发一条，看 `liaison_message` 是否 +1**
- 登记 `docs/tech-debt.md` **TD-42**（含两次实测原文）。`0909AS` 在跑（还它）
- ⛔ **汤丽萍不得再发**——截至今天她已白发 4 条（群 @ 三条 + 私信一条）。AS 还上并真实验证通过后再请她

**win 端「打标即开班」机制核验 ＋ HR 移植方案**（`docs/findings/2026-09-10-win端打标即开班机制核验与HR移植方案.md`）
- 🔴 起草者先前的 win/Mac 对照表有三处硬伤，Shao Peishen 逐条纠正、本会话已核真身六处坐实：
  ① README 第九态**有**机器写入（`followup_readme_bridge.mark_reply_arrived`）；
  ② 拆件**不是人做的**，是「打标即开班」**零轮询**事件驱动无头会话；
  ③ win 队列不是「往 markdown 追一行文本」，是编辑锁把守的单一入口（机器可读前缀、WIP 阻断、hook 拦截）
- 🔴 **错误根源＝把「问题陈述」当「现状描述」**。⇒ **读法纪律：「它解决的问题 / 为什么做 / 成因 / 背景」一律是历史，
  现状只看函数体、调度配置、测试三处**
- 🔴 **正本判定纪律**：同一内容多副本时，正本由**代码里解析该路径的那个函数**确定，⛔ 不是文件系统里撞见的第一个
- 起草者自己核出的缺口 0（排在四条之前）：**我们没有「回件→跟进信」那座桥**。win 端靠**串行原则**
  （同一收信人只有一封在途）免去显式关联 ⇒ **串行闸不只是防打扰，它是回件归属的判据**
- 移植分期 P0 桥＋第九态 → P1 打标即开班 → P2 章程正本 → P3 口径点台账；🔴 **载体不换**（`liaison_task` 保持 DB）
- ~~`0909AT` 待跑~~ ✅ **已跑完（2026-09-10）**：grill 八问全答、包 `liaison-reply-bridge-and-patrol` 已立（0/33）；tasks §0 把「TD-42 已还并真实验证」**与「SDK `message` 事件接线（AT-1）」**都列为开工前置——后者是本次新查出的更前一道缺口

**其余落档**
- ✅ **名单外归档口径裁决**（Shao Peishen 09-10，学 AI 赋能项目）：**归档**。win 端 `intake.py` 无白名单概念、回件到达即归档，
  与本项目 spec 与 `inbound.py` docstring 的「SHALL 仍然归档」一致 ⇒ `0909AP` 取 spec 是对的，
  🔴 `0909AP` opener 里「名单外 ⛔ 不归档」那句**是起草者写错、已作废**。两条红线不变：⛔ 不入队、只回礼貌回复
- 🔴 **号池规则第 4 条**（`0909AP` 撞号的根治）：**写进台账还不够，必须 commit**——未提交的登记行 grep 得到、
  别的 session `git show` 不到。⇒ **出号 → 写表 → 当场 commit，三步一气做完**
- 🔴 **起草者读串行的教训**：`0909AL` 的 opener 里「TD-29 已被 `0909T` 处置」是错的——那句属紧邻其后的 TD-30，
  起草者抓摘要时 awk 边界没截住。AL 逐条核代码才发现四条全欠着。⇒ **摘要不是真身，拿摘要当依据要先回原文核**
- 收口核对（Shao Peishen 09-10 授权）：`0909P` 发版 ✅（`ec38cb5`）／`0909AI` 落档 ✅／TD-41（0 字节附件）✅ 已登记／
  TD-29 剩 2 条 ⏸ 交 `0909AR`
- 🔢 号池：`0909` 双字母已用到 **AT**（AZ 是看护者号）。下一个可用 `0909AU`

### ⑱ 2026-09-09 晚·跟进信线收口（Cowork·0909Q）

- ✅ **人事部#1 已真发出**：`python -m tools.liaison send-followup --send` 发进「人力AI保障组」，markdown 正文 ＋ docx（39.4K）均到达（截图为证）。**HR 线跟进信第一次走通端到端自动发送，⛔ 今后不手工发**
- 🔴 **两次台账状态与事实不符，方向相反，两次都无症状**：
  ① `bf63e7a` 入库时提前写成 `✅ 已推送`，而信从未发出 ⇒ CLI 幂等保护认这一行，`--dry-run` 直接短路返回「已是终态」`EXIT=0`、正文一字不打印，真跑 `--send` 也会**一条不发却报成功**；已人工订正回 `🆕 待发` 才跑通
  ② 真发成功后**主工作区台账未被自动回填**（文件 mtime 仍是人工订正那次）；已按事实人工改成终态
  📌 **教训**：台账状态列是**机器判据不是记录**——写终态＝对下游宣布「这件事已发生」，写早了会让真正该发生的事再也不发生
- ⏸ **待查（`0909AD` 第 3 项验收未成立）**：回填为何没落到主工作区。判据＝那次 `--send` 命令的**最后一行有没有打印「已发送并回填台账」**；有 ⇒ 大概率在 worktree 里跑、改了副本（三个 worktree 里都有台账副本）；无 ⇒ 回填逻辑真缺陷。⛔ 未查清前不要把 `0909AD` 当已验收
- ⏸ **汤丽萍私信通道：Shao Peishen 2026-09-09 定「等明天她发信再接通」** ⇒ ⛔ 今天不再请她发任何消息（今天已让她白发 4 条：群 @ 三条 + 私信一条，全部因服务未在线而落空）。明天她发信后，从那条入站消息里读单聊 chatid
- ✅ **TD-39 已还并已真实复验**（`0909AH` 改码 + `0909AJ` 断网 245 秒复验，2026-09-09）。**8.6 的该项阻断解除**；G-4 装 launchd 的三项解锁条件已全部满足，⏸ 只等 Shao Peishen 本人在 Terminal 跑（⛔ 不可代）
- 🔢 号池：`0909` 双字母已用到 **AH**，下一个是 `AI`

### ⑰ 眼下手里的三件（2026-09-09 下午）

- ~~**`0909AA`**~~ ✅ **已跑完**（`8bd102b` / `e969f1b` / `a0a63b2`，均已推）：三凭据 len 35/43/89 齐、三条守卫全过；`tools/liaison/.venv` 建起（wecom-aibot-python-sdk 1.0.2 / pytest 8.3.4 / PyYAML 6.0.3）；表面探针与既有 findings **逐字节零差异 ⇒ 表面无变化**；首跑 4 red 已裁决清零，套件 **741 passed**。TD-19 **未销**（如期），但其阻塞理由已消失 ⇒ 转「已提上日程」，详见 TD-19 的「⏫ 2026-09-09 状态更新」段
- **汤丽萍欢迎信：✅ 草稿已出（md ＋ docx 双件），⏳ 待你审**。落位 `docs/跟进信/`：正文 `人事部-汤丽萍-跟进-2026-09-09-AI招聘值守机制启用与配合方式.md` ＋ 同名 docx ＋ 新建台账 `README-跟进信清单.md`。三项自检已跑：`决策点: 1 项（a 使用反馈的形式与节奏）` 在位、**全文无第三人称**（剔除「其他」后命中 0）、docx 里 frontmatter 未渗进正文。
  - 🔴 **口径变更（2026-09-09 Shao Peishen 定，此前的写法全部作废）**：**HR 项目不碰 Windows 侧那套**——zhuopin-ai 仓库的 README 主表、取号 CLI、串行闸 CLI、登记 CLI、PowerShell 发送通道**都不用**；HR 跟进信**自成一条编号线，从 `人事部#1` 起**（这封就是 #1）。⛔ 今后不要再写「待 Windows 侧取号／闸核／转 docx／登记」那四项
  - **沿用**（其他部门跑了两个月已验证）：§4 三要素信骨架、`决策点:` 字段必写、起草期代词自检、docx 必发、发送状态语义、串行原则。**替代**：取号与闸核看 `docs/跟进信/README-跟进信清单.md`；docx 在 Cowork 里跑 `md-to-word`；🔴 **发送由 Shao Peishen 本人在企微完成，代理人永不代发**
  - **身份已定**：汤丽萍＝**人事部 AI 专员**，与其他专员同等对待（名录正本里她还挂在「其他」栏、未记部门 ⇒ 那边需补一行，但 HR 线不依赖它）
  - ✅ **留白已清（09-09 他让直接改，「手工介入容易出错」）**：开头「先肯定最近一次真实交付」那句**判定欢迎信不适用、整行删除**——⛔ 不编造交付。删后重出 docx 并写回，占位零残留、frontmatter 未渗漏、25 段。**md ＋ docx 双件均为定稿态，状态仍 `⏳ 待你审`，发送由他本人在企微完成**
  - 🔴 **发送口径与实证（09-09 下午）**：他审核通过并授权先发群，台账已转 `🆕 待发`；**但 Cowork 实际发不出去**——`qyapi.weixin.qq.com` 在 Mac 侧 device shell 无 DNS、在云容器经代理 CONNECT 得 403（不在 egress 白名单）。⇒ 这封由他本人在企微发。要让 Cowork 能代发群，前提是把该域名加进本会话 egress 白名单
  - ✅ **口径固化**：「人力AI保障组」＝**人事部门群**（项目叫法 vs 实际部门，同一个）；Mac 端只此一群、不向 Windows 端其他群发信。已写进 `docs/跟进信/README-跟进信清单.md` 抬头
  - 🔴 **win 端收发实证（09-09 他口述 ＋ 本会话核 win 源码），三条改假设的事实**，全文 `docs/findings/2026-09-09-win端aibot收发实证-对8.6灰度的三条影响.md`：① **aibot 能主动发**，单聊与群聊同一方法只差 chatid（`send_markdown(chatid,…)` / `send_file(chatid, media_id)`）——**前置是专员先私信一次机器人**，那个单聊 chatid 才存在；本文此前「aibot 不能主动私信」的说法**已作废**。② **群里只收文字平信，文档回灌只能私信** ⇒ 第 4 章附件归档链路在群消息上**永远验不到**，8.6 验收必须先加「专员私信一次机器人」这一步，TD-22（真实 msgid 字符集）也只能在带附件的私信上核。③ aibot 开机即在线＝长连接监听形态可行，缺的只是把 `client.run()` 接进 `run_forever`（TD-19）。④ 必须分清：**值守服务由 launchd 在 macOS 原生环境跑、用本机网络能连企微；Cowork 的 shell 在隔离 VM 里无出网** ⇒ 服务发消息不需要我有网
  - ✅ **whitelist 已填**：汤丽萍（len=10）、邵培申（len=11）均非空，fail-closed 那道闸已过。⇒ 她的**回灌走群即可**（她在群里发、值守服务收），⛔ 不需要另建私信通道；真正的卡点只剩 **TD-19**（`make_sdk_connect` 探到 `connect` 是协程即拒启动，要把 `client.run()` 接进 `run_forever` 并用真实凭据端到端验证）＝ 8.6 灰度那件事
  - 🧹 仓库根的 `Claude outputs/`（桌面端自动存的旧版「人力资源部」信）已挪进 `_to_delete/Claude outputs-20260909-旧版人力资源部信/`——Cowork 删不了文件，只能挪；⚠️ `_to_delete/` 未进 `.gitignore`，提交时别把它 add 进去
  - 📎 截图实证（09-09 下午）：「人力AI保障组」7 人，**MAC机器人已在组内**（另一个 BOT 是陈承的机器人）；成员含邵培申（群主）、陈承、聂鑫、汤丽萍、王寒月
  - **远程（Mac／Cowork）能做**：读正本起草 md 正文、跑代词自检与决策点自检、出 `⏳ 待你审` 草稿
  - **远程不能做、⛔ 不要绕**：取号与串行闸判定只认 Windows 侧 CLI（`工具-跟进闸查询.py --to`）；README 主表（86 KB）远程读不到**也不该读**；docx 转换与登记 CLI 在 Windows 侧
  - **编号规则与发送八态改读** `6-人才与组织/部门AI专员跟进/跟进机制-判据版.md`（≈17 KB，远程可取）。已取到：编号按**部门连续计数器**、跨收信人共用、换人不重置、未发出/已作废不占号；README「编号」列未发出时写 `部门#N（待你审，暂不占号）` 带括注，信件抬头不带；文件名 `部门-姓名-跟进-YYYY-MM-DD-主要事项.md`、⛔ 不含会变的数字；落款固定 `—— OPVP Shao Peishen`
  - **已取到的起草依据**：正本 §4 三要素信骨架（抬头带部门连续编号→开头先肯定最近一次真实交付→逐件事「做什么／怎么做／什么时候交」→知识资产段→结尾附一页纸＋落款）、§5 落位与登记五步（含 1bis `决策点:` 字段、步骤 2 代词自检、步骤 3 docx 必发、步骤 5 首次发新机制信须随附《专员协作说明-新版需求确认怎么配合-2026-07-25.md》）
  - **名录硬事实**：汤丽萍＝**女**（名录正本）。⚠️ 她在名录里**不在「部门AI专员」五人之列**（姚祖怡／陈忱／唐燕萍／泓钦／陈承），列在「其他」且未记部门 ⇒ ⛔ 不得自行把她写成「人力资源部AI专员」，信里用中性表述，这一条要当场问 Shao Peishen 一次
  - **口径（09-09 他拍）**：先发「机制版」——只建立需求确认与使用反馈机制，明写「系统尚在灰度，暂不用改变你现在的习惯，开通时点我单独告知」；使用告知留 8.7
  - ⏸ **09-09 下午他说「稍等 10 分钟再取，win 侧在修复」** ⇒ 下一轮重取正本前先确认他说修完了
### ⑱ 灰度（8.6）前置清单 · 2026-09-09 17:xx 重排

⚠️ **顺序变了**：`install_launchd.py` 此前与其他项并列，实为**排在 TD-19 之后**的下游项。
理由见 G-3。

| # | 事项 | 谁做 | 状态 | 判据（怎样算完） | 不做会怎样 |
|---|---|---|---|---|---|
| **G-1** | 两人企微 `userid` 填进 `whitelist.yaml` | Shao Peishen 提供 → 已代填 | ✅ **已完成**（`e969f1b`） | `load_whitelist(SHIPPED_CONFIG) == {"TangLiPing", "ShaoPeiShen"}`，已成断言 | — |
| **G-2** | 核对 `ShaoPeiShen` 的大小写 | Shao Peishen | ✅ **已确认**（2026-09-09 本人回「ShaoPeiShen 正确」） | 已比对企微通讯录「账号」列，与 `whitelist.yaml:22` 逐字符一致 | — |
| **G-3** | 还 **TD-19**（`client.run()` 的 async→同步适配） | — | ✅ **已完成**（代码 `0909AC`，端到端实测 `0909AE`） | `0909AE` 首次真实建连：连接建立成功、认证通过 ⇒ TD-19 销账。⚠️ 同次实测暴露的心跳 bug 是**另一条**（TD-38），⛔ 不算本条未完 | — |
| **G-4** | 在 Terminal 跑 `install_launchd.py`（不带 `--dry-run`） | **Shao Peishen**（脚本 docstring 明写「Claude ⛔ 不代跑」） | ✅ **已完成 —— Shao Peishen 本人 2026-09-09 23:27 CST 在 Terminal 跑完**。`bootstrap rc=0`、`state = running`、`pid 39462`、`last exit code = (never exited)`；plist 新建于 `~/Library/LaunchAgents/com.zhuopin.hr.liaison.plist`。⚠️ **`bootout rc=5 Input/output error` 是预期的**（首次安装、没装过，脚本 docstring 明写「没装过时 bootout 返回非 0，那是正常的，⛔ 不要当失败」），⛔ 不要当故障回溯。`0909AJ` 事后核验：`liveness` = `connected`、`interval: 30000ms`、心跳每 30 秒全有 ack、无 `45009`；`startup_gap` 补记正确（23:11:30 停机→23:27:43 拉起那段空窗已自动补记成窗口并闭合）。🔴 **值守进程从此常驻**（`KeepAlive=true`）—— 以后 `pgrep -f 'python.*tools\.liaison'` **必然非空**，⛔ **不要再把「非空」当异常**，也⛔ 不要 kill / bootout 它。⚠️ `launchd.err.log` **开头那两条 ERROR 是已知噪声**（冷启动误报，他答 `3b` 明确不修，每次 KeepAlive 拉起都刷一遍），⛔ 不要按真故障查 | ~~🔴 现在装比不装更危险~~ —— **该理由 2026-09-09 已消解**：`0909AG` 的「断线后永不恢复、永不告警」已由 `0909AJ` 实测推翻（复网 23.5 秒自愈、窗口闭合并发出恢复告警）。他已答 `1a` 授权装机，**现在只差他本人跑那一条命令**。`--dry-run` 已于 09-09 验过，渲染无误、四条路径正确、无凭据取值 | 见左栏——**不是**「装了没用」，是「装了会掩盖故障」 |
| **G-5** | 还 **TD-36**（假凭据会真实建连） | — | ✅ **已完成**（`0909AC`，与 TD-19 同一 commit） | 两条用例改走 `--self-check`（跑完全部校验、建连前退出），另加 `tests/netguard/` 网络闸门：非回环连接一律 raise，进程内＋子进程两条都装。⚠️ 落地中实测到一次经本机 `127.0.0.1` 代理的真实外发，已修并落档 `docs/findings/2026-09-09-测试网络闸门被本机代理绕过.md` | — |
| **G-7** | 还 **TD-40**（`.env` 的 `HR_LIAISON_*` 让 app 配置加载不了） | — | ✅ **已完成**（`0909AC`，2026-09-09 他答 `1c` ＝ 改法 ③） | 凭据已迁到 `tools/liaison/.env`（0600）；`Settings()` 正常实例化；根 venv 全量 **2041 passed / 0 failed**（迁移前 23 failed）；两道回归岗在位 | — |
| **G-8** | 还 **TD-38**（`heartbeat_interval` 传秒当毫秒，心跳 ×1000） | 泳道 `[Mac]0909AF` | ✅ **已还且已独立验证**（`840f5cf` 修复、`edb30d3` 销账；`0909AG` 独立复验） | 常量改名 `DEFAULT_HEARTBEAT_MS = 30_000`，同义反复的断言换成绝对值 `== 30_000`。**复验数据**：`interval: 30000ms`、90 秒 3 次（间隔精确 30.0 秒）、全程 8 分钟零 `45009` —— 对照修前 1399 次/44 秒，**降约 954 倍** | — |
| **G-9** | 复核 **TD-39**（原疑点：断线事件疑似没到 `LiaisonSession`） | 泳道 `[Mac]0909AG` | ✅ **已执行完毕**，结论＝🔴 **缺陷已确认，⛔ 不是虚惊**（`f35328b` 定性、`373fa1f` 订正） | 六项判据 **3 过 / 3 不过**（断网 91.77 秒 ＋ 复网观察 122.28 秒，全自动脚本，Wi-Fi 已确认恢复）。✅ ① 断线日志 ② `liveness` 翻 `disconnected`（时延 55.75 秒＝心跳 30s×2 未回 pong，**非缺陷**）③ 新开 `disconnect_event` 窗口；❌ ④ 复网后 122.28 秒**零日志**、`Authentication successful` 全程仅 1 次（启动那次）⑤ `liveness` 永不翻回 `connected`、`stamp_at` 冻结 158 秒 ⑥ 窗口 `recovered_at`/`closed_by`/`alerted_at` 三列全空。⚠️ **原疑点措辞需修正**：断线事件**到了**（②③ 为证），`0909AE` 看到的「16 秒仍 connected」只是 55.75 秒判死时延没走完就被强杀 ⇒ **检测侧是好的，坏的是恢复侧**。证据 `docs/findings/2026-09-09-断线重连实测.md` | 复核本身已完成；缺陷的后果见 **G-10** |
| **G-10** | 还 **TD-39**（重连链被 `error` 事件抛出打断） | 泳道 `[Mac]0909AH`（已执行）；opener `docs/openers/0909AH-TD39重连链修复.md` 由 `[Mac]0909AE` 交付（worktree ☑） | 🛠 **代码已还**（`62d3594`、落档 `7b20186`）。派发＝2026-09-09 晚（`[Mac]0909AE` 交付引用块；Shao Peishen 答 `1a` 授权开工），⛔ **已派发过，不要再派 `0909AH`**。两项都已落地：`error` 事件监听器（根因）＋ `liveness.stamp_at` 看门狗（N-0，已接进 `main()`，⛔ 不是死代码）。测试 +24（liaison 783→807、全量 2042→2066），全部**先红后绿**，三条变异复核证明有判据力。🔴 ⏸ **TD-39 暂不销账**：未做真实建连、未断网 ⇒「真的能重连回来」尚未验证 ⇒ 见 **G-11** | 补 `error` 事件监听器（**根因**：本项目没接 `error`，pyee 在无监听器时对 `error` 是 `raise` 而非丢弃；该抛出恰落在 `aibot/ws.py:152` `on_error(e)` 与 `:153` `_schedule_reconnect()` 之间，杀死 `_receive_loop` task —— 唯一还能触发重连的地方）＋ `liveness.stamp_at` 看门狗（**N-0**：`session_client.py:258-262` 假设「`run()` 不返回 ⇒ 外层 `run_forever` 兜底」，本故障下不成立 —— task 死后 loop 照样挂着、`run()` 仍不返回，兜底一次都不触发 ⇒ **两层重连被同一个异常一并打掉**）＋ **三条先红后绿的自动化测试**。判据：三条测试先红后绿 ＋ 全量 pytest 不回归 ＋ 合并回 main。🔴 ⛔ **TD-39 暂不销账**，等真实断网复验才销（参照 `0909AF` 教训：销账 ≠ 已验证）。⚠️ **TD-38 修好是暴露本条的前提**——修前连接活不过 44 秒就被限流踢断，从没走到「运行中断线 → 重连失败」这条分支 | 🔴 值守通道**断线后永不自愈、永不告警**，8.6 灰度会带着一个**无症状**故障上线 |
| **G-11** | **TD-39 真实断网复验**（G-10 的验收另一半） | 泳道 `[Mac]0909AJ`，opener 已就位：`docs/openers/0909AJ-TD39真实断网复验.md`；**⛔ 必须在主工作区**（worktree ❌ —— 那里没有 `.env`、没有真实库，硬做只会得到假结论） | ✅ **已完成（`[Mac]0909AJ`，2026-09-09 23:02–23:10 CST）—— TD-39 已销账**。实测断网 **245 秒**（>180 秒阈值），六项判据全过：④ 复网后 **23.66 秒** 再次 `Authenticated`、⑤ `liveness` **23.48 秒** 翻回 `connected`、⑥ 窗口 **23.48 秒** 闭合（`closed_by='reconnect'`）。🔴 **恢复由「改法 1（`error` 监听器）」单独做到，看门狗（N-0）本次未触发、仍只有单测覆盖**（机理：error 事件每 ≤30 秒刷新一次存活戳，永远达不到 180 秒阈值）—— ⛔ 不许读成「两层都验过了」。证据 `docs/findings/2026-09-09-TD39真实断网复验.md`。顺带登记一条**未修**项：启动瞬间看门狗读到上轮残留存活戳会误报两条 ERROR，归另一条 opener | 用真实凭据跑 `PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison`，断网 → 等 **≥180 秒**（看门狗阈值，⛔ 等不满就复网测不到兜底那一层）→ 复网。判据＝`0909AG` 那六项里 ❌ 的三项翻绿：④ 复网后自动重连成功 ⑤ `liveness` 翻回 `connected` ⑥ 中断窗口闭合并发出恢复告警。三项都过 ⇒ **TD-39 才可销**、G-4（launchd 装机）才谈解锁 | 🔴 **代码改了但没验＝以为修好了**。`0909AF`（TD-38）就是没区分「销账」与「已验证」，才要 `0909AG` 回头补验，而那次补验查出的正是本条这个缺陷。不验就装 launchd ＝ 让一个可能仍然「断了回不来、还不吭声」的服务无人值守地跑 |
| **G-12** | **N-0 兜底层（存活戳看门狗）端到端覆盖** | 泳道 `[Mac]0909AQ`（⚠️ 原 `0909AP`，撞号改判，见下方号池段），opener 已就位：`docs/openers/0909AQ-N0看门狗端到端覆盖.md`（worktree ☑） | ✅ **已完成（`[Mac]0909AQ`，2026-09-10 CST）**。三条 E-1/E-2/E-3 全部**先红后绿**，红的报错原文已落进 `docs/tech-debt.md` TD-39「N-0 端到端覆盖」一节的表格。🔴 **只加测试、⛔ 未改任何产品代码**（`session_client.py` / `__main__.py` 一字节未动）。构造法＝假 SDK 逐行复刻 `aibot/client.py:345-362` 的 `run()`，握手后**事件完全静默**而 `loop.run_forever()` 一直挂着；**产品阈值 `STALE_LIVENESS_SECONDS = 180.0` 未被调小**，跨阈值靠假时钟往前跳，三条合计 **0.15 秒**跑完。全量 894→**897 passed** / 5 skipped（+3）。⚠️ 本条在 CC 云端（Linux ＋ Py3.13）执行，该环境下 `test_launchd_plist.py` 有一条 macOS 专属用例恒红（`✗ 本脚本只在 macOS 上有意义`），**与本条无关、Mac 上不会红**。「启动瞬间看门狗误报」那条（答 `3b` 不修）被**绕开、未顺手修**。触碰区 `test_session_client_reconnect.py`，与第十六批（AK–AO）**不重叠** | `0909AJ` 实测证明 TD-39 两层修法里**只有改法 1 被真实验证**，看门狗那层至今只有单测。🔴 ⛔ **那条不要再去断 Wi-Fi**——改法 1 生效后 `error` 事件每 ≤30 秒刷新一次存活戳，**永远达不到 180 秒阈值**，普通断网**按设计**就触发不了看门狗。必须在测试里构造「SDK 事件完全静默但 loop 还活着」的形态（＝`0909AG` 撞到的原始故障）。三条 E-1/E-2/E-3 各须**先红后绿** | 🔴 兜底层没被端到端验过 ＝ **SDK 里任何别的「task 死了但 loop 还活着」的形态都会重演 `0909AG`**，而症状同样是「服务看起来在跑、其实早断了」 |
| ~~**G-6**~~ ✅ **已闭合** | `0909P` 发版到 `.51` | **Shao Peishen** 拍（发版不可代） | ✅ **2026-09-09 23:43 发版完成，现网＝`0aa1af4`**，冒烟 ①–⑤ 全过（目标验收 `Select-String` ＝ 1），未回滚。他 23:32 回「发」＋ 亲自点 Run `sync-to-server.sh` 双重留痕。以下为发车前的备料记录，保留：`0909AJ` 已代跑五项前置**全过**：`.51` 可达（`ssh zp51` 回 ok，他在内网）／现网健康 **200**／`requirements.txt` diff **为空**（纯 sync 前提成立）／本机回归 **12 passed**／main 与 origin 同步。范围现算＝**5 个文件 276 insertions**，与 opener §零 文件清单**一致**、无多出项，且 **TD-33（`25d8715`）已合入**——正是本行原先建议「一次带上」的那条 | 「这次发什么」发车前**仍要再现算一次**（`0909AJ` 的现算是 23:33 的快照）。⚠️ opener §零 写于 09-08，`0909AJ` 已订正：`idempotency.py` 现在含 TD-33，⛔ **不要因为行数比原文多就停车**。发版命令那一步仍**由 Shao Peishen 点 Run**（分类器拦 AI 直调 `sync-to-server.sh`） | 并发安全已核：第十六批五条泳道全在 `tools/liaison/`，而 `tools/` **不在 `SYNC_PATHS` 白名单**，改动到不了 `.51`，两条可并行 |

**2026-09-09 结存（`0909AJ` 后订正，⛔ 上一版已过期）：他手上只剩一件 —— G-4 那一条 Terminal 命令。**
G-1/G-2/G-3/G-5/G-7/G-8/G-9/G-10/G-11 全部闭合（TD-19/36/38/39/40 均已销账）。
**G-4 已闭合**（2026-09-09 23:27 他本人跑完，值守通道现由 launchd 常驻托管）。
G-12 待派发（可与第十六批并行）；G-6 等他一个「发」。
⇒ **球已全部交回泳道侧**：G-4 于 23:27 由他本人跑完闭合；G-6 于 23:32 他回「发」，剩下的是派发 `0909P` 执行，⛔ 但发版命令那一步仍要他点 Run（那一下本身就是发版授权留痕）。

**🔢 号池登记（2026-09-09）**：`0909AK`–`AO` ＋ `AZ` 由第十六批编排占用（见 `OP-0820` 未提交段），
🔴 **撞号已改判（`[Mac]0909AZ` 2026-09-09 处置）**：`0909AP` 早被**第十六批·入站预演泳道**占用——
那一行当时在 `OP-0820` 的 161 行未提交改动里，`0909AJ` 看不见，才误判为空号。
⇒ **G-12（N-0 看门狗）改用 `0909AQ`**，文件已 `git mv` 为 `docs/openers/0909AQ-N0看门狗端到端覆盖.md`，
号池台账两行（`0909AP` 入站预演、`0909AQ` N-0）均已落进 `OP-0820`。
⚠️ **下一个可用号是 `0909AR`**。⛔ `0909AP` 不是空洞，⛔ 不要重派给 N-0。

### ⑲ 第十六批发车前置：`0909AB` superpowers 可达（2026-09-09 由 `0903B` 会话派，⛔ 不进泳道）

| # | 事项 | 谁做 | 状态 | 判据（怎样算完） | 不做会怎样 |
|---|---|---|---|---|---|
| **P-1** | `0909AB`：插件（superpowers 及其它 scope=project 且 projectPath≠本仓库的）改装 **user 作用域**；无头 + worktree 两条探针复验；仍不通才给 run-lanes.sh 加 `--plugin-dir` | Shao Peishen 贴 CC opener（`docs/openers/0909AB-superpowers无头可达.md`，引用式） | ✅ **09-09 已跑完**：`--scope user` 装上（6.3.0），两条无头探针修后均 `LOADED`（修前均 `Unknown skill`），`subagent-driven-development` 另验 `SDD-LOADED`；**未走 `--plugin-dir` 退路，`run-lanes.sh` 一行未改** | 无头探针 `cd 仓库根` 与 `cd /private/tmp` 两次都回 `LOADED`；skill 模板里「取不到→手工走」已删、看护者预案改红灯；commit 已推 | 第十六批起每条泳道仍 `Unknown skill`，继续靠执行者手工走协议（08-27 起已六批），机器保证为零 |
| **P-2** | 第十六批发车 | Cowork 编排 → 看护者 | ✅ **已完成，2026-09-10 00:09 收敛**（编号最终用了 `0909AK`–`AP` ＋ 看护者 `0909AZ`，共 6 条泳道，**没有改用 `0910`**；编排 `5efb0e2`，收敛 `c6d9940`，过期标注清理 `407faa7`）。⚠️ 本行 09-09 晚写的「可编（编号改用 `0910`）」**已过期**，2026-09-10 由 `[Mac]0910B` 会话核实后订正——留着它下一轮会被当成待办重发一批，正是重复劳动的成因 | 看护报告「是否真调到」栏全 ✅（六条泳道均落地：TD-23/24/26/28/29/31/32/35 销账 ＋ 回填复核 `9c456d9` ＋ 入站预演 `7cafbfc`） | ~~发早了＝再跑一批"靠运气"~~ ⚰️ 已不适用，前置 P-1 于 09-09 跑完后才发的车 |
| **P-3** | 🔴 **TD-42 真实验证**（`liveness.last_event_at` 与看门狗终止路径） | **Shao Peishen 本人**（不可代：要 `.env` ＋ 真实建连，云端与 worktree 都做不到） | ⏸ **未做**（`docs/tech-debt.md` 仍是「⏸ 真实验证留步」）。🔴 **⛔ 不是一件新事**——与本文件顶部 **AS-1**（`[Mac]0910A`）是**同一个物理动作**，TD-42 验证是 AS-1 那条 opener 的第四步。⇒ **跑 AS-1 即销本行**，⛔ 不要另起 opener | TD-42 末段由「⏸ 真实验证留步」改为「✅ 已验证」并附两条证据：① `last_event_at` 每 ~30 秒推进；② 断网/静默 ≥ 13 分钟后日志出现「看门狗终止进程」、进程被 launchd 拉起、`data/liaison/watchdog.json` 计数 +1 | AT-1b（真实入站落库一条）无法判读——`liaison_message` 若仍为 0，分不清是假死还是接线没接对，正是 TD-42 末段那条「不可区分」重演 |
| **P-4** | ~~`0910C` 发车~~ ⚰️ **已并入 AT-1a／`[Mac]0910B`，不是漏跑** | Shao Peishen 贴 `[Mac]0910B` 引用块 → CC worktree session | `[Mac]0910D` 2026-09-10 出过一份同题 opener（`0910C`），同时刻 `[Mac]0910A` 出了更完整的 `[Mac]0910B`（多帧字段映射 fail-closed 处置、真实解释器路径、8.5bis 回勾）⇒ **废 `0910C`、留 `0910B`**，`0910C` 正文已删、号池留墓碑。⇒ **本行不再是独立待办**，执行看顶部 **AT-1** | 同 AT-1 | 两份同题 opener 并存，派错一份就白跑一轮 |

⚠️ Shao Peishen 09-09 口径：**修好以后一律走规范流程**——`Skill(superpowers:…)` 不可达即环境故障、泳道停，⛔ 不再允许"按磁盘 SKILL.md 手工走"。过往六批的交付**不回炉**（看护报告逐条核过：两阶段 review、fix loop、终审都手工走了，丢的是机器保证不是步骤）。


全是**技术债与机制债**，五条泳道零重叠、全走轻量通道（不走 spec-to-plan / run-build）：

| 泳道 | 编号 | 做什么 | 触碰区 |
|---|---|---|---|
| 发车机制 | `0909S` | launchd plist 两缺陷（`AbandonProcessGroup` + `EnvironmentVariables.PATH`）＋单测；⛔ 不装 | `scripts/install_lane_launcher.py`、`tests/test_lane_launcher.py` |
| 留存 | `0909T` | 冲突 B 裁决落地 ＋ TD-30（日志 30 天）＋ TD-34（咬不住的回归测试） | `retention.py`、`logsetup.py`、`liaison-message-archive/spec.md`、`design.md` §D13 |
| 会话 | `0909U` | TD-20 裁决落地 ＋ spec 两处改口径 | `session.py`、`liaison-channel-session/spec.md` |
| 群通知债 | `0909V` | TD-25（按 `last_errcode` 过滤，**不加列**）＋ TD-27（`MODE_REJECT` 改 raise） | `notify/webhook.py`、`notify/store.py` |
| 幂等基座 | `0909W` | TD-33（`effect_log` INSERT 非 `IntegrityError` 也回滚）；~~随 `0909P` 上 `.51`~~ ✅ **已上线 `0aa1af4`** | `app/storage/idempotency.py` |

- dry-run 已在 Cowork 侧核过（用 `--plan` 指本机挂载路径，绕开脚本里的 `REPO` 绝对路径常量）：**5 泳道 5 条**，正文 18/22/19/19/19 行，`Σ=N=5`（`M=6`，多出的一处是第十三批 `0909B` 的残留标注，BUDGET-HIT 不自动摘、实为虚惊）
- 发车参数：`--full-auto --yes --only 0909S,0909T,0909U,0909V,0909W --max-parallel 5`
- 🔴 **本批唯一跨泳道共享文件 `docs/tech-debt.md`**（T/U/V/W 各销自己那几条，段间相隔 30 行以上）。真身核验判据：销账数比基线 **+6**（TD-20/25/27/30/33/34），且 TD-23/24/26 仍未销
- 🔴 **本批五条泳道都不碰 `tasks.md`**——8.1–8.5 已勾、8.6–8.9 留他本人，本批没有要回勾的行；跑完 `grep -c '^- \[x\]'` 应一字不变
- 🔴 `0909S` 修的正是 launchd 发车缺陷，**修完要他在 Terminal 重装一次才生效**，所以本批自己大概率仍走「点 Run」退路（`0909X` 的【四】【五】已写死判据与无引号退路命令）
- 不进本批：8.6–8.9（他亲自）；`0909P` 发版（等「发」；建议等 `0909W` 合入后一次带上 TD-33，「这次发什么」由 `git diff <现网 HEAD>..main` 现算）；TD-23/24（第十六批）；TD-26（与 `0909V` 同文件，下一批单独还）；三件凭据的验证 opener（等他写进 `.env`）
- 🔢 号池：`0909` 字母池 **S–X 已全部派出**，09-09 当日已无可用字母。第十六批若仍在 09-09 发车，必须改用 `0910` 并在号池台账另起一段。⚠️ **2026-09-10 订正：该条件未成立**——第十六批实际在 09-09 晚发车，用的是溢出双字母 `0909AK`–`AP` ＋ `AZ`，⛔ **没有**改用 `0910`。`0910` 段自 09-10 才另起（见 `OP-0820` 号池台账末）

**D. 顺手件**：清理 `/private/tmp/wt-0908M`、`wt-0909B`（真未合 0）；CLAUDE.md 252 行已超 250 红线 2 行，下次改它时拆一段出去。

---

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
| 10 | 8.4 ＋ U6 巡检 CLI 上 `.51` | 8.4 要人在页面跑通"模糊回复→点选→带缺口确认"；U6 的巡检 CLI 从未对 `.51` 真实 `demo.db`/`decisions.jsonl` 跑过——且 U6 代码**还没部署到 `.51`**（现网是 `d104249`，早于 U6）。✅ **已闭合**（`feb49d6`）：二次发版含 U6，巡检 `EXIT=2`＝镜像尚不存在，8.4 本人跑通回勾 |
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

### ⑲ 09-09 晚：8.6 首次真实建连已跑，卡在心跳单位（`[Mac]0909AE` 出）

**已发生的事实**：`0909AE` 用真实凭据首次让值守服务连上企微——**连上了**（鉴权通过、
`liveness.json` 写出 `connected`、`liaison.db` 建出五张表）⇒ **TD-19 已销账**。
但只在线 **44 秒**：SDK 的 `heartbeat_interval` 单位是**毫秒**，`session_client.py:37` 传的是
`DEFAULT_HEARTBEAT_SECONDS = 30`，被当成 **30 毫秒** ⇒ 心跳 ×1000（44 秒内 1399 次）⇒
企微返 `45009 Too many requests` 判死连接。全文 `docs/findings/2026-09-09-首次真实建连实测.md`，
commit `159305f`。新登记 **TD-38**（阻断 8.6）、**TD-39**（断线事件疑似未达状态机，⚠️ 待复核）。

**Shao Peishen 2026-09-09 已答 `1a，2a，3a`**，三条待办如下（⛔ 串行，AG 依赖 AF 合入）：

| # | 谁做 | 状态 | 判据：怎样算完 | 不做会怎样 |
|---|---|---|---|---|
| 1 | `[Mac]0909AF`（CC，worktree）| 🆕 待派 | `DEFAULT_HEARTBEAT_MS = 30_000` 合入 main；AST 钉子能挡住改回 30；`reconnect_interval` 注释口径改对；TD-38 销账 | 值守通道**无法保持在线**，且每次拉起都对企微洪泛 ≈32 次/秒，bot 凭据有被限流/封禁风险 |
| 2 | `[Mac]0909AG`（CC，主工作区，**全自动**，⛔ 不需他在场）| 🆕 待派，**前置＝AF 已合入** | 心跳实测为 `30000ms`；断网后 `liveness` 翻 `disconnected` ＋ `outage_window` 开窗；复网后自动重连并闭窗（六项全过）| "服务看起来在跑、其实早断了"这类**无症状**故障没人守；8.6 灰度建立在没验过的重连上 |
| 3 | launchd 装机（`install_launchd.py`，他本人在 Terminal 跑）| ⏸ **已明确暂缓**（他答 `3a`）| TD-38 已还 **且** AG 六项全过后，由他重新拍板 | 现在装＝一台无人值守地反复重启、反复洪泛、反复吃 45009 的机器（`KeepAlive=true` + `ThrottleInterval=30` 会放大） |

⚠️ **口径订正**：`0909AE` opener 结尾那句「⏸ 下一步：Shao Peishen 在 Terminal 跑
`install_launchd.py` 装常驻」**已作废**——那句写在不知道 TD-38 存在时。以本节第 3 行为准。

⚠️ **`0909AE` 的两处偏离**（已如实落档，非隐患）：① `git pull --rebase` 时
`tools/liaison/__main__.py` 与 `0909AD` 的 `send-followup` 块冲突，两侧都是文件尾纯插入，
按项目口径**合并双方**、正文一字节未改；② 观察窗提前 51 秒收停（判定是对企微洪泛后主动
SIGINT），事后证明不是多虑——45009 在第 44 秒就到了。

📌 **一条值得记住的教训**：这个 bug 之所以活到真实建连才暴露，是因为守它的断言是**同义反复**——
`assert options.heartbeat_interval == session_client.DEFAULT_HEARTBEAT_SECONDS` 拿传进去的值
跟它自己比，单位错成什么样都绿。⇒ **配置项断言必须写绝对值，⛔ 不许拿常量跟自己比。**
`0909AF` 已把"先让新断言在旧代码下真的红"写成强制步骤。

🔴 **2026-09-09 新口径（Shao Peishen 原话）：「以后能改成全自动都全自动，减少人工干预少出错」。**
⇒ 已落进真源：`CLAUDE.md`「⏰ 无人值守禁止提问 ｜ 能自动化的一律自动化」＋
`.claude/skills/kickoff/SKILL.md`「能自动化的一律自动化」（含自动化必须满足的三条：
自愈 `trap` ＋ 独立兜底、无害预检、失联可续）。`0909AG` 已据此重写为全自动（修订1）——
它的初版写「你手动关一次 Wi-Fi」被当场退回，改自动化后**证据反而更好**（2 秒采样能算出
`liveness` 翻转时延，人眼算不出）。⛔ 这条**不覆盖**凭据、对外发送、CLAUDE.md 不可代项。

---

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
