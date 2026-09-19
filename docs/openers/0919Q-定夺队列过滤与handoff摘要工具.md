# `[Mac]0919Q` 定夺队列「只出待答行」查询 ＋ `.claude/handoff` 近期摘要工具

【设置】执行环境: CC ｜ Session: 新开（run-lanes 无头起）｜ 分支: lane-0919q-queue-digest ｜ worktree: ✅ 勾 ｜ 工作区: `.claude/worktrees/lane-0919q-queue-digest` ｜ 派发: Cowork·HR业务线-接力0918AO ｜ 模型: Sonnet

## 零、为什么

Shao Peishen 2026-09-19 晚间在一个姐妹项目（Win 端，企业AI赋能升级）里做了一次开场 token 浪费复盘，修了三处（队列查询加 `--digest --actionable` 过滤、cat 补丁改成回放器只出结论行、分诊加自身留痕降档），并要求"跨项目遵守，不要再犯"。Cowork·0918AO 本轮巡检卓品这边时，当场复现了同一类浪费：读 `docs/roadmap/定夺队列.md`「一、待答」时把全表 49 行（真正待答只有 8 行）整段读入；查 `handoff-inbox` 提交协议格式时用不带过滤的 `find` 扫 `.claude/handoff`，命中几百个历史 `lanes-*`/日志文件名，触发工具输出 63KB 硬截断报错。本条补齐卓品这边对应的两处节流。

## 一、Task 1：`定夺队列.md` 只出待答行

`scripts/dispatcher_answers.py` 已有 `queue_rows(text)`（解析「一、待答 ＋ 二、远期」两节带任务 id 的行）与 `_status(row)`（判定 待答／已答／作废）——**复用这两个函数**，不要重新写表格解析器。加一个 CLI 入口（新增 `--pending` 参数，或新建一个薄壳脚本 `scripts/queue_pending.py` import 这两个函数，你判断哪种改动更小、对既有 `dispatcher_answers.py` 的现有调用方影响更小）：

- 只输出 `_status(row) == "待答"` 的行，每行只印：编号｜场景｜阻塞类型｜问题（截断到约 60 字，末尾加"…"如截断）｜推荐。
- 表头必须显式回显：`共 N 行，其中待答 M 行（隐藏 N-M 行已答/作废/远期）`——⛔ 不静默吞掉隐藏的行数，这是 Win 端那次复盘的机器判据原话，照搬。
- 解析不出「状态」列或行格式异常的行 **一律保留**（当"待答"处理，不吞），避免因为解析器意外漏看真正待答的行。

**测试**：新增/扩展对应测试文件，覆盖：全部已答（输出 0 行待答但表头如实报数）、混合待答已答、格式异常行不被吞掉三种场景。

## 二、Task 2：`.claude/handoff` 近期协议摘要

新建 `scripts/handoff_digest.py`（只读，不改任何 handoff 文件）：

- 对 `commit/`、`launch/`、`events/` 三个子目录，各自只列**最近 N 条**（默认 N=10，可传参）按 mtime 排序的文件名＋一行状态（`.done`/`.rejected`/`.deferred`/`.request` 待处理），外加该目录总条目数（`共 X 条，只显示最近 N 条`）。
- 对根目录下大量历史 `lanes-*` 批次目录与各类 `.log`：只输出**汇总行**（总目录数、总 .log 数、最新一条的文件名与 mtime），⛔ 不逐个列出——这正是本轮实际触发 63KB 截断的那部分内容，是本条要防的核心场景。
- 加一个 `--recent-only`（默认开）与 `--full`（明确要全量时才用，且 `--full` 模式下也要求调用方传 `--confirm-large-output` 之类的显式确认参数，避免未来又被无脑全量调用）。

**测试**：新增测试文件覆盖：`.claude/handoff` 有大量历史目录时输出保持在合理体量（可以断言输出字节数低于某阈值，如 3KB）、`--full` 不传确认参数时拒绝执行并给出提示。

## 三、红线

⛔ 不改 `.claude/handoff` 目录下任何现有文件、不改 `handoff_relay.py`／`commit_request.py` 的搬运逻辑本身（只新增一个纯读取的摘要脚本）。⛔ 不改定夺队列 `queue_rows`/`_status` 的既有行为（只复用，不改语义）。⛔ 不改闸门判据。⛔ 不得把任务交给后台子代理后结束回合——子代理一律前台等到返回，哨兵由本会话自己顶格打印。

## 四、无头块铁律

同 `OP-0820`「三、无头块铁律」。

## 五、验证与收口

1. 真身验证：实跑新工具对当前 `定夺队列.md`（真身应输出约 8 行待答，具体数字以实测为准）与当前 `.claude/handoff`（真身应远小于本轮实测的 63KB 全量 dump），把两次实测输出体量（字节数）写进收工记录，与本轮实测的浪费数字（全表读入量、63KB 截断）对照，证明确有节流效果。
2. 跑本分支涉及的全部测试，pytest 0 unexpected failed。

【并发协议】
1. 只 `git add` 本条产出的文件；⛔ 禁止 `git add -A`／`git add .`／`git commit -a`／`git stash`
2. `git status` 里出现别人的改动是正常的，不要停下、不要问、不要顺手提交
3. push 被拒 → `git pull --rebase --autostash origin main` 后重试，最多 3 次
4. 报 `.git/index.lock` 已存在 → 等 5 秒重试最多 5 次，⛔ 默认绝不删除该锁

收工三行：Task 1（新增/改动文件 commit＋实测「待答」行数与表头回显效果）｜ Task 2（新增文件 commit＋实测输出体量对照）｜ 全部相关测试 passed/failed 数。最后一行顶格输出 `OPENER_DONE` 或 `OPENER_PARTIAL: <原因>`。⛔ 不提问（无人值守）。
