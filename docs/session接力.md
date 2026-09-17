# Session 接力 · HR 招聘智能体

> 滚动更新，覆盖旧版。新会话读完本文即可接上。
> 最后更新：2026-09-17 06:2x（Cowork·0917D：R-1 实核已完成；体积闸归档接力 61→41 KB、编排 65→12 KB；R-2 拆成 `0917G`→`0917H` 经 launchd 发车（首发 E/F 作废）。此前：Cowork·HR业务线-接力0917D 转场：把 `0916E`–`0917B` 这一大段
> **从未进过本文**的进展补齐——第十七批波次 1＋2 已基本完成、Token 治理线已结项；
> 订正 `tasks.md` 抬头进度行失真 24/33 → 27/33）

---

## 🔴 新 session 先看这一节（2026-09-17 06:0x 实核真身，⛔ 下面各历史节均早于它）

⚠️ **本文此前停在 2026-09-16 14:2x（`[Mac]0916D`）**，而其后 `0916E`–`0917B` 约 20 个号的进展
**一条都没进过本文**（实核：全文对 `0916E`/`0916V`/`0916W`/`0917A`/`0917B`/`Token治理` 的命中数 ＝ **0**）。
本节按文件系统真身补齐。⛔ 读下面那些历史节时，一律以本节为准。

### 一、第十七批：`liaison-reply-bridge-and-patrol` 已 **27/33**

| 章 | 单元 | 状态 | 谁跑的 |
|---|---|---|---|
| §0 | 三条前置门槛 | ✅ **全勾** | `[Mac]0910A`（2026-09-16 跑完 AT-1b／TD-43 与 TD-42 真实验证） |
| §1 | **P0 · 回件桥＋第九态** | ✅ **9/9 合回 main** | 波次 2 `0916N`→`O`→`P` |
| §2 | **P1 · 信号与打标即开班** | ✅ **9/9 合回 main** | 波次 2 `0916Q`→`R`→`S` |
| §3 | **P2 · 拆件章程正本** | 🟡 **3/4** —— 🔴 只剩 **3.2 `unpack/charter.py`** | 波次 2 `0916T`→`U` |
| §4 | **P3 · 口径点台账** | ✅ **3/3 合回 main**（`9724339`，顺带登记 TD-44） | 波次 2 `0916V` |
| §5 | 验收 · 真实起活实测 | 🔴 **0/5，全部不可代** | **Shao Peishen 本人** |

⇒ **「回灌自动落档、自动拆件」那条链，四个零环已经建起来三个半**（对照本文下一节那张六环表——
那张表是 2026-09-10 的快照，⛔ 已过期，留作判据由来）。

**剩下的 6 条，只有 1 条是机器能做的**：

- **3.2** `tools/liaison/unpack/charter.py`：单点常量 `CHARTER_RELATIVE_PATH`、`read_charter(repo_root)`
  （缺失抛 `CharterMissing`，由 dispatch 转 `failed(charter_missing)`）、纯函数 `compute_prompt(...)`。
  ⚠️ 3.1／3.3／3.4 都已勾 —— 章程正本 `.claude/skills/liaison-unpack/SKILL.md` 与两组交叉核对测试都在，
  **只差把章程读进来的那个模块**。
- **5.1–5.5**：重启值守服务 → P1 单独验 `unpack-dispatch --dry-run` → P0+P1 端到端等汤丽萍真实入站
  → D1 反证（邵培申自己发一条，他名下无在途信）→ 实测记录落 `docs/findings/`。
  🔴 **⛔ 单测全绿不算验收**，本包 `tasks.md` 抬头写死。

### 二、🔴 `tasks.md` 抬头进度行失真（同一族教训又一次）

`openspec/changes/liaison-reply-bridge-and-patrol/tasks.md` 第 1 行写 **`24/33`**，实际勾选 **`27/33`**。
差的正是 §4 P3 那 3 条——`9724339` 把 4.1/4.2/4.3 回勾了，**没同步抬头**。
📌 **这和「`git log -3` 深度判据」「预占号」是同一族**：把一个会变的量抄成了常量。
⇒ 订正动作已交 `[Mac]0917C`（见【下一步】）。

### 三、另一条线：Token 治理已结项

`0917A` 总验收（治理前后五项指标对比与结项判定）、`0917B` 结项登记（路线图结项段、跟踪项 T-1、
10-01 复测）。产物在 `docs/token治理/`。⇒ **这条线已闭，⛔ 不要再当待办排**。
机制侧顺带落地：`0916W` 给 `run-lanes.sh` 加了**脚本强制建 worktree** ＋ `worktree-guard` hook
（⇒ 以后无头泳道不再需要执行者自己建 worktree，`0910B` 那次卡自检 1 的形态已根治）。

### 四、当前工作区与号池

- `git status`：**`docs/openers/OP-0820-全量编排.md` 一处未提交** —— `0917B` 的泳道标注被
  `run-lanes.sh` 自动摘成 `> ✅ 已完成 2026-09-17（PARTIAL）· 原泳道 Token治理结项提交`。
  另有 `?? _to_delete/`（⛔ 不 add、⛔ 不删）。与 origin ahead/behind 均 0。
- 🔢 **号池台账已迁到 `docs/openers/号池台账.md`**（2026-09-16），历史月份在 `docs/openers/归档/号池台账-归档.md`。
  ⛔ 不要再往 `OP-0820` 顶部那张旧表登记。已用到 **`[Mac]0917B`**；`0917C`／`0917D` 本轮派出，
  `0917E`–`0917N` 已由 0917D 派出（E/F 作废），**下一个可用号是 `[Mac]0917O`**。
- 其余包：`hr-wecom-aibot-liaison` **63/67**（剩 8.6–8.9 灰度四条，他亲自）；
  `m1-intake-quality-fixes` 68/69；`m1-job-profile-intake` 60/72；`lane-launch-armed-scan` 0/35（**已裁定搁置**）。

### 五、【下一步】—— 四列待办

| # | 事项 | 谁做 | 状态 | 判据（怎样算完） | 不做会怎样 |
|---|---|---|---|---|---|
| **R-1** | `[Mac]0917C` 转场落档提交 | CC | ✅ **已完成**（`73a5be2`，已推，ahead/behind 0；`tasks.md` 抬头已是 27/33） | — | — |
| **R-2** | 3.2 章程接入生产路径 ＋ TD-45 ＋ design D4 常量名 | `[Mac]0917G`→`[Mac]0917H` | ✅ **已完成**（`59e9a78`＋`9e510d1`，已 ff 合入并推；tasks 28/33；生产代码 P2-TODO/CHARTER_RELPATH 零命中） | — | — |
| **R-2b** | 全量 pytest 残留 1 红（`test_worktree_lane_runs_inside_script_created_worktree`） | `[Mac]0917I` | ✅ **已完成**（`159dca5`）：根因＝`sandbox` fixture 用 `dict(os.environ)` 把泳道的 `HR_LANE_*` 带进被测子进程；全量 2454 passed / 0 failed；TD-46 已还；run-lanes.sh 零改动 | — | — |
| **R-3** | §5 五条真实起活实测 | `[Mac]0917N` 无头泳道（Cowork·0917D 经 launchd 远程发车；5.4 需他本人手机发一条消息） | ✅ **5.1/5.2/5.4 通过**（`[Mac]0917N`）：5.1 重启 pid 41080→52687，0 新增 ERROR；5.2 TD-47 修复后重跑，日志无 `Not logged in`，会话正常登录并跑完一轮（按 §四止步于陈旧信号，非失败）；5.4 `liaison_message` +1、新增 `bridge_skipped_no_inflight` 一条、无 `dispatch_started`、台账文件 diff 空。5.3 仍待汤丽萍下一条真实入站（`人事部#1` 现状 `✅ 已推送`，非第九态，前提未成立）；详见 `docs/findings/2026-09-17-打标即开班真实起活实测.md`「重跑三（0917N）」 | 5.3 通过 → 5.1–5.5 全勾，当场归档 | 5.3 无真实入站前无法归档；TD-48 仍开放，每次 `--force` 起活都可能再产生一次未授权自发提交 |
| **R-3b** | TD-47：拆件子进程未登录 | `[Mac]0917K` 已修（`8ce57b3`，补 `USER`）→ `[Mac]0917N` 只 cherry-pick 该提交合入 main | ✅ **已还**（`[Mac]0917N`，cherry-pick 后 `ca02197`，已 push）；未取 K 分支上的子会话噪声提交（`a644d45`/`f575c5e`/`fe64bc1`），已随 K worktree/分支一并清理丢弃 | — | TD-47 已划掉；下游 TD-48（无头会话自发提交）在本轮 5.2 中再次复现（`07537fe`，已 `git revert` 为 `748a323`），独立于本条 |
| **R-4** | 汤丽萍解锁 | — | ✅ **已解锁**（§0 的 0.3 已勾＝真实入站已落库一条） | — | — |
| **R-5** | `hr-wecom-aibot-liaison` 8.6–8.9 灰度四条 | Shao Peishen 本人 | ⏸ 等本包 §5 | 四条全勾并归档 | 值守通道停在「代码齐、没真用过」 |

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
[Mac]0917D-HR业务线接力
【设置】执行环境: Cowork ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（Cowork 无 worktree，只做文档、编排与派单）｜ 工作区: 仓库根（/Users/paulshao/Projects/HumanResource）｜ 派发: Cowork·HR业务线-接力0909AU
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

## 二、下一步

### 🆕 2026-09-16 `0916U` P2 拆件章程正本（liaison-unpack-charter）Task 4-5 建造收口

全 5 Task（Task1-3 上一 session、Task4-5 本 session）+ 终审 + 修复波 全部通过，已合入 main（合并
commit 见收工报告）。终审（Opus）放行「With fixes」，一波修复（`c5aebd4`：`compute_prompt` 前言里
残留的裸 `unpack-signal` 命令形＋`read_charter` 收窄的异常捕获）已修并复审通过。3 条遗留待办：

| # | 事项 | 谁做 | 状态 | 判据（怎样算完） | 不做会怎样 |
|---|---|---|---|---|---|
| 1 | `compute_prompt` 未接生产路径：`__main__.py`/`dispatch_wiring.py` 仍各自持有 `CHARTER_RELPATH` 字面量与 `_build_minimal_prompt`（两处都带 `P2-TODO` 注释指名本变更包收尾时删除） | 下一条 CC 泳道（建议单独起 opener，读 `docs/superpowers/plans/2026-09-10-liaison-unpack-charter.md` 与本条后追加一个 wiring task，走 TDD） | ✅ **已还**（`0917H`） | `grep -rn CHARTER_RELPATH tools/liaison/__main__.py tools/liaison/unpack/dispatch_wiring.py` 零命中 ＋ `bridge_dispatch` 实际调用 `charter.compute_prompt` ＋ 单测覆盖 | — |
| 2 | 章程 §二.6／`dispatch.py` 白名单的 `python -m tools.liaison criteria` 都是裸形式，且本机只有 `python3` 无 `python`；`criteria.py` 尚未交付（P3，`2026-09-10-liaison-criteria-ledger.md`） | P3 交付时顺带修（章程改 `PYTHONPATH=. tools/liaison/.venv/bin/python` 前缀形式 ＋ 同步改 `dispatch.py` 白名单，否则 Task5 的双向核对测试会红） | ✅ **已还**（`0917H`，即 TD-45 销账） | P3 落地后 `test_unpack_charter_allowlist.py` 两条测试仍绿 ＋ 章程与白名单用词一致 | — |
| 3 | `design.md` D4 行 52 仍写 `HEADLESS_ARGV_TEMPLATE`，代码实际常量名是 `HEADLESS_ARGV_FIXED_PART`（P1 交付时改的名，design 文档没跟着改） | 下次碰 design.md D4 的人顺手改一个词 | ✅ **已还**（`0917H`） | `grep -n HEADLESS_ARGV_TEMPLATE openspec/changes/liaison-reply-bridge-and-patrol/design.md` 零命中 | — |

SDD 台账（`.superpowers/sdd/2026-09-10-liaison-unpack-charter/progress.md`，worktree 内、gitignored）
有完整的终审全文摘要与逐条裁决理由，worktree 删除前已转写本条要点，原台账即将随收口清除。

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
