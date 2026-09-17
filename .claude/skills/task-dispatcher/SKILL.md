---
name: task-dispatcher
description: 任务驱动 workflow 的调度器（R2）。由事件（泳道批次收敛、定夺答复提交、每日 09:00 兜底）唤醒的无头会话执行：刷新任务台账 → 按 rules.md 算 ready 集 → 按 lane-dispatch 规则写无头块并经动作通道发车 → 需人项追加定夺队列 → 文档改动经提交请求通道提交 → 事件文件归档。当 `scripts/dispatcher_event.sh` 起的无头会话收到「执行 task-dispatcher」、或 Shao Peishen 说"跑一次调度器""调度器唤醒一次""task-dispatcher"时使用。⛔ 本技能不亲自执行任何 opener、不替 Shao Peishen 拍任何不可代项。
---

# task-dispatcher · 调度器（R2）

> 2026-09-17 `[Mac]0917AM` 写定；设计见 `docs/roadmap/任务驱动workflow设计.md` §四 R2。
> 阶段推进规则的**真源是同目录 `rules.md`**（R4，`0917AL`）——ready 五判据（含在环闸门）、阶段完成判据、能开尽开上限、新场景发现、定夺队列契约都在那里，⛔ 本文件不复述、不改写；两处不一致以 `rules.md` 为准。
> 唤醒方式：`scripts/dispatcher_event.sh`（launchd `com.zhuopin.hr.task-dispatcher`，`scripts/install_task_dispatcher.py` 安装）以 Sonnet 起 `claude -p`，预算上限 $10，日志 `.claude/handoff/dispatcher/<ts>.log`。

## 0. 会话性质：无人在场

本 skill 的执行者是**无头会话**，没人能回答问题。⛔ 不输出问句、不等回复。凡「需 Shao Peishen 拍板」的点一律写进 `docs/roadmap/定夺队列.md` 然后**继续发别的**；opener 没写预案的歧义取保守方向（宁可留一条待办，不把没做的事标成做完）并登记。
遇不确定 ⇒ 写定夺队列，不猜。

## 1. 红线（机器闸 `tests/test_task_dispatcher.py` 逐字核这一节）

| # | ⛔ 绝不做 | 判据 |
|---|---|---|
| ① | **⛔ 发版 `.51`**：不 ssh、不 `sync-to-server.sh`、不写 `deploy-51` 动作请求、不改 `docs/deploy-51-server.md` 的发版记录 | `.51` 发版属不可代项；台账 `release` 阶段只生成定夺队列一条「是否发版」（`rules.md` §2） |
| ② | **⛔ 对外发信**：⛔ 调用 `send-followup` 动作（不写 `send-followup` 动作请求、不调 `tools.liaison send-followup --send`、不发企微私信／群消息）；⛔ 把跟进信台账 `docs/跟进信/README-跟进信清单.md` 任何一行改为 `🆕 待发` | **无例外**（2026-09-17 `0917AO` 删去「台账行已是 🆕 待发」例外）：发信只由 Cowork 在本线收到 Shao Peishen 「发」（定夺队列 G4 行已答含「发」）后落 `🆕 待发` 并经动作通道执行；调度器到 G4 只追加定夺队列行（`rules.md` §6） |
| ③ | **⛔ 改 `.claude/skills/` 其它 skill**：只读 `lane-dispatch`／`kickoff`／`run-build`／`spec-to-plan` 等，不改它们一个字；本 skill 自己的 `SKILL.md` 与 `rules.md` 也⛔ 不在无头会话里改（改规则由 Shao Peishen 派专门 opener） | `git status` 里出现 `.claude/skills/` 下的改动 ⇒ 本会话违规，`git checkout --` 撤回并在日志登记 |
| ④ | **⛔ 改 CLAUDE.md**（含任何 `**/CLAUDE.md`） | 同上 |
| ⑤ | **⛔ 真实简历**：不读、不拉、不处理 `data/`、`evalset/` 下任何真实简历；不为「真实简历数据处理范围」相关条目发车（台账里它们阻塞类型＝决策，本就不 ready；`rules.md` §1 ④ 复核） | M2 门槛「登录＋访问留痕」未就位前，真实简历处理范围属不可代项 |
| ⑥ | **⛔ 替 Shao Peishen 拍任何不可代项**：合规红线七条变更或例外、候选人淘汰规则例外、候选人对外通道开关、真实简历处理范围、`.51` 发版、预算与外部采购 | 命中 ⇒ 定夺队列一条，台账条目 `阻塞类型＝决策` |
| ⑦ | **⛔ 越闸**：G1 intent 定稿／G2 design·spec 定稿／G3 发布／G4 发信／G5 口径签认，闸门行未「已答＋放行字样」前，不为该 subject 的下一阶段发车、不改 intent `status`、不归档、不发信、不转口径点已签认 | 放行只认 `docs/roadmap/定夺队列.md`（`python3 scripts/gates.py open G<n> <subject>`），⛔ 不认聊天记忆、不认台账状态自改；`--show ready` 已把到闸未放行的条目剔除，⛔ 不手工把它们加回发车集。到闸 ⇒ `python3 scripts/gates.py sweep --apply` 追加行（去重）后停在该场景，其它场景照常能开尽开 |

## 2. 逐步执行（① → ⑨，按序做完再收工）

每一步先做、再在收工报告里写一行结果。任一步失败 ⇒ 登记原因、跳到 ⑨ 归档事件（只归档已处理的）、收工报告写 `OPENER_PARTIAL`。

### ① 单实例锁 `.claude/handoff/dispatcher.lock`

- 锁文件内容 = 持锁进程 pid（一行）。`scripts/dispatcher_event.sh` 起本会话前已取锁并导出 `DISPATCHER_LOCK_HELD=1`；见到该环境变量 ⇒ 锁归壳管，本步只核 `cat .claude/handoff/dispatcher.lock` 有 pid 即过。
- 手工起的会话（没有该环境变量）：锁存在且 `kill -0 <pid>` 成功 ⇒ **pid 存活即退出**——输出一行「另一个调度器实例 <pid> 在跑，本会话退出」＋ `OPENER_DONE`，⛔ 不做任何事、⛔ 不删锁；锁不存在或 pid 已死 ⇒ `echo $PPID > .claude/handoff/dispatcher.lock`，收工前删掉（会话被打断留下的死 pid 锁会被壳判孤儿自动覆盖，不需人清）。

### ② 刷新台账

```bash
python3 scripts/dispatcher_backlog.py
python3 scripts/dispatcher_backlog.py --show conflicts
```

`conflicts` 非空 ⇒ 逐条按 `rules.md` §2 的真身判据核（`git cherry -v main <分支>`、tasks.md checkbox、plan 文件存在），核实的用 `python3 - <<'EOF'` 按 id 改台账该条 `状态`／`阻塞类型`（⛔ 不整读 > 40 KB 的台账，⛔ 不改 id）；核不实的留 conflicts 不动。

### ③ 算 ready 集

```bash
python3 scripts/dispatcher_backlog.py --show ready
python3 scripts/dispatcher_backlog.py --show running
pgrep -f 'run-lanes.*\.sh' >/dev/null && ls -d .claude/handoff/lanes-*/ | while read -r d; do [ -f "$d/summary.txt" ] || echo "未收敛批次：$d"; done
# ⛔ 不用 results.tsv 判收敛（发车前就建好、逐条 append）；summary.txt 只在全部泳道 wait 完才写，缺它＝在跑，其块的触碰区算「在跑」
```

对 `--show ready` 的每条再过 `rules.md` §1 五判据（尤其 ③ 触碰区不与在跑重叠、④ 不属不可代项、⑤ 闸门已放行）与 §3 上限（在跑 ≤ 3、单批 ≤ 6 条），得到本批发车集；聚合条目（`<change>/U<n>`、`change:*`、`scene:*`）永不发车。
本批发车集为空也**正常**（多数唤醒只是刷新台账与推进状态），⛔ 不为了「有事做」放宽判据。
同时按 `rules.md` §2 逐阶段核「完成判据」：成立的把台账状态改「完成」并生成下一阶段条目；`rules.md` §4 三条新场景发现规则命中 ⇒ 只写定夺队列（⛔ 不启动）。
**在环闸门**（`rules.md` §6）：

```bash
python3 scripts/gates.py sweep --apply        # 到闸（propose/plan/release 依赖已齐）而定夺队列缺行的 ⇒ 追加 G1/G2/G3 行（去重）；已有行只打印状态
python3 scripts/dispatcher_backlog.py --show gated   # 停在闸前的条目清单（闸、subject、缺行|待答|已答·未放行|作废）
```

到闸条目在台账里是 `阻塞／决策` 带 `闸门:` 字段，⛔ 不手改回「待开」——放行后重跑生成器会自动改回。G4／G5 不由本步触发（G4 由起草方在信稿自检过后 `gates.py request G4 <编号>`，G5 由拆件会话追加）。

### ④ 写无头块（按 `lane-dispatch` skill 规则，⛔ 不改那个 skill）

对本批每条：
1. **先 grep 号池**：`grep -n "<MMDD><序号>" docs/openers/号池台账.md docs/openers/归档/ -r`，MMDD 实跑 `TZ=Asia/Shanghai date +%m%d`，序号取台账里该日最后一个之后的字母（A…Z、AA…）；⛔ 不占已登记的号
2. 按 `lane-dispatch` skill §③ 的形状写进 `docs/openers/OP-0820-全量编排.md`：`> 泳道：<名>` 行 ＋ 代码块；【设置】行六项齐（`派发: task-dispatcher·<事件文件名>`）；worktree 块写 `分支: lane-<编号小写>` ＋ `工作区: .claude/worktrees/lane-<编号小写>`；正文按阶段套 `rules.md` §2 的下一任务（spec-to-plan／run-build 拆段／archive），写死收口验证（`git cherry -v main <分支>` 无 `+`）、并行同伴触碰区、`superpowers` 不可达即留步；只有 propose／design 类才写 `｜ 模型: Opus`
3. 登记号池：`docs/openers/号池台账.md` 当日节追加一行（编号／主题／派发＝task-dispatcher／状态＝已派）
4. 机器闸三连，任一红 ⇒ 修到绿再发，⛔ 不绕：
   ```bash
   bash docs/openers/run-lanes.sh --dry-run --only <本批编号,逗号分隔>   # 每条都要「正文 NN 行」且 NN > 0、模型行正确
   python3 scripts/opener_split_check.py                                 # 退出码 1 ⇒ 拆段或写「拆分豁免：<理由>」
   venv/bin/python -m pytest tests/test_doc_size_budget.py -q            # 体积闸：OP-0820 ≤ 60 KB、号池 ≤ 60 KB、接力 ≤ 40 KB；超 ⇒ 先给闭环节加【已闭环】再 python3 scripts/archive_docs.py --apply
   ```
5. 台账里这些条目 `状态` 改「在跑」，备注写编号与事件文件名

### ⑤ 发车：经动作通道 `launch-lanes`

用 **Write 工具**（⛔ 不经 Bash）写 `.claude/handoff/commit/<YYYYMMDD-HHMMSS>-dispatch.action`：

```json
{"action": "launch-lanes", "args": "--full-auto --yes --only <本批编号,逗号分隔>"}
```

`args` 只能是 lane-launcher 白名单六项（`--full-auto` `--yes` `--only` `--max-parallel` `--stagger` `--budget`），⛔ `--dry-run`／`--model`／`--chain` 必被拒。
动作通道回 `.done` 里 `queued: true` ⇒ 发车器被占、请求已自动入队 `launch/queue/`，**正常**，⛔ 不重发、不等；`.rejected` ⇒ 按 reason 修参数重写一次，再拒即登记留步。
等回执最多 60 秒（`ls .claude/handoff/commit/<同名>.*`），没等到也继续——launchd 是异步的，回执会在下次唤醒时看到。

### ⑥ 需人项追加 `docs/roadmap/定夺队列.md`（去重）

凡 ③ 里判为「决策」／「外部输入」的新条目、`rules.md` §2 `propose` 阶段的 Open Questions、§4 新场景发现、外部输入超期：
- 追加前 **先 grep**：`grep -n "<任务 id>" docs/roadmap/定夺队列.md`，已有 ⇒ 不重复入队（远期的移入待答即可）
- 按 `rules.md` §5 契约写：编号 `Q-<两位递增>`（取现有最大 +1）／场景／阻塞类型／问题／`(a)/(b)` 各带代价／推荐或「无默认」／来源／阻塞的任务 id／状态＝待答／答复留空
- 台账对应条目 `状态＝阻塞`、`阻塞类型＝决策|外部`
- 处理 `decision-*` 事件时反向（2026-09-17 `0917AQ` 起**机器做**，⛔ 不手改台账）：重跑 `python3 scripts/dispatcher_backlog.py --register-unmapped`——生成器按 `rules.md` §7 把已答行所列条目解阻塞（或按映射保持阻塞并写原因）、生成 `answer:Q-xx` 任务、作废行改完成；答复键无映射的行登记进定夺队列「`【答复→任务映射缺失】Q-xx`」（去重）并保持阻塞。收工报告写 `定夺队列⇒台账:` 那行的数字。**闸门行**（问题列 `【G<n> …】`）⛔ 不手改台账：重跑 `python3 scripts/dispatcher_backlog.py` 即按 `gates.py` 判定改回；G1 放行还要把该 intent 的 frontmatter `status` 改「已确认（G1 Q-xx）」（只在此刻）

### ⑦ 文档改动经提交请求通道提交

本会话改的只能是文档：`docs/roadmap/任务台账.yaml`、`docs/roadmap/定夺队列.md`、`docs/openers/OP-0820-全量编排.md`、`docs/openers/号池台账.md`（＋ `archive_docs.py` 搬出的 `docs/openers/归档/*`）。用 Write 工具写 `.claude/handoff/commit/<YYYYMMDD-HHMMSS>-dispatcher.request`：

```json
{"message": "chore(dispatcher): <事件文件名> 台账刷新＋派 <编号列表>（task-dispatcher）", "paths": ["docs/roadmap/任务台账.yaml", "docs/roadmap/定夺队列.md", "docs/openers/OP-0820-全量编排.md", "docs/openers/号池台账.md"], "push": true, "emit_event": false}
```

`paths` 只列**真有改动**的文件（`git status --porcelain -- <路径>` 非空的），列了没改的会被整条拒绝。⛔ 本会话不自己 `git add`／`git commit`／`git push`——提交通道会在 `index.lock` 时推迟、被拒时写原因，比会话里硬提安全。回 `.deferred` ⇒ 改动留在工作区，登记「⏸ 留步：提交被推迟」，下次唤醒会带上（提交通道核的是工作区 diff）。
`"emit_event": false` 必带：提交通道对含 `docs/roadmap/定夺队列.md` 的提交默认写 `events/decision-<ts>` 再唤醒调度器（那是给 Cowork 落**答复**用的回环）；本会话只追加待答行、没有新答复，带上它就不会自己唤醒自己。⛔ 不要为避免回环而绕开提交通道。

### ⑧ 处理 `lanes-done-*` 事件时先调 `launch-queue-drain`

事件文件名以 `lanes-done-` 开头 ⇒ 在做 ② 之前先：

```bash
venv/bin/python scripts/action_request.py launch-queue-drain    # 无 run-lanes 在跑 ⇒ queue/ 最早一条移回 launch/，WatchPaths 由此发车
```

输出 `"drained": null` 且 `ls .claude/handoff/launch/queue/*.request` 非空 ⇒ run-lanes 仍在跑（`--chain` 续跑或另一批），留队等下个事件，⛔ 不重试轰炸。然后按该批 `.claude/handoff/lanes-<STAMP>/results.tsv` 逐条核真身（`rules.md` §2：`git cherry -v main <分支>` 无 `+` ＋ checkbox 已勾才算「完成」；`OPENER_DONE` 不算），更新台账状态。

### ⑨ 事件文件处理完移到 `events/processed/`

```bash
mkdir -p .claude/handoff/events/processed
mv .claude/handoff/events/<事件文件> .claude/handoff/events/processed/
```

壳在本会话 rc=0 后会把 prompt 里列出的事件**全部**归档（含本会话输出 `OPENER_PARTIAL` 的情况——`claude -p` 退出码仍是 0），所以事件文件⛔ 不承担「留步记忆」：留步事项一律写进台账备注或定夺队列，⛔ 不靠把事件留在原地等人接。只有壳看到 rc≠0（预算打满、进程被杀）事件才留原地，由每日 09:00 兜底或下一事件带上（壳对失败有 30 分钟退避，⛔ 不会连着烧预算）。`events/` 目录变化会再触发一次壳，壳见锁在、或没有未处理事件与当日兜底戳 ⇒ 静默退出，不会空转。

## 3. 收工报告（写进会话输出，壳会存进日志）

固定六行：

```
事件：<文件名列表 | 每日兜底>
台账：条目 N ｜ ready N ｜ 在跑 N ｜ 阻塞·决策 N ｜ 阻塞·外部 N ｜ conflicts N
本批派发：<编号列表 | 无> ｜ 动作回执：<done/queued/rejected/未等到>
定夺队列：新增 Q-xx… ｜ 解阻塞 Q-xx… ｜ 待答共 N
提交：<.request 文件名> → <done <hash> | deferred | rejected <原因>>
留步：<无 | 逐条>
```

然后最后一行顶格 `OPENER_DONE`（全部完成）或 `OPENER_PARTIAL: <一句话原因>`（有留步）。

## 4. 不要做

- ⛔ 不亲自执行任何 opener（泳道里的活由 run-lanes 起的会话做）
- ⛔ 不用 `git add -A`／`git add .`／`git commit -a`／`git stash`；本会话根本不该跑 `git commit`——走 ⑦
- ⛔ 不删 `.git/index.lock`、不删别人的 worktree、不 `git worktree prune`
- ⛔ 不整读 `docs/roadmap/任务台账.yaml`、`docs/openers/OP-0820-全量编排.md`、`docs/session接力.md`（> 40 KB，先 grep 再分段 Read）
- ⛔ 不在【设置】行给泳道／spec-to-plan／run-build 写 `模型: Opus`
- ⛔ 不把泳道自报 `OPENER_DONE` 当作阶段完成（真身判据见 `rules.md` §2）
- ⛔ 不越闸（红线 ⑦）：到闸只追加定夺队列行；放行与否只问 `scripts/gates.py`，⛔ 不凭「他上次说过」放行
