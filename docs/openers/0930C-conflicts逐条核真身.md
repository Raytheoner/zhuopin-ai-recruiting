[Mac]0930C-conflicts逐条核真身
【设置】执行环境: Codex ｜ Session: 新开（run-lanes 无头起）｜ 分支: lane-0930c-conflicts-audit ｜ worktree: ✅ 勾（改台账与新建记录）｜ 工作区: .claude/worktrees/lane-0930c-conflicts-audit ｜ 派发: Codex·泳道看护（automation hr）

## 零、为什么

`dispatcher_backlog --show conflicts` 长期挂着 30+ 条「台账状态 vs 真身推导不一致」。调度器每轮只能
「保守不猜」（2026-09-29 17:44 报告原文：35 conflicts 预存，未逐条核真身），导致每次刷新都要人工跳过、
且其中若有真·漏派会被埋掉。本条逐条核真身、给出结论并让 conflicts 归零（或把穷尽后仍无法定论的
极少数逐条登记）。

## 一、授权与边界（Shao Peishen 2026-09-30 答 1a 授权的**专门 opener**）

⛔ 只许改「三、交付物」列出的文件。⛔ 不改 `.claude/skills/**`、不改 `scripts/dispatcher_backlog.py`
的判据语义（发现判据 bug ⇒ 只在核真文档里登记 + 停在该条，⛔ 不顺手改代码）。并行同伴：0930A 改
`.codex/**`＋`scripts/hooks/**`＋`tools/liaison/unpack/dispatch.py`；0930B 改 `scripts/dispatcher_answers.py`
＋`pyproject.toml`＋其测试。⛔ 不碰它们的文件。

## 二、前置

1. `PYTHONPATH=. ./venv/bin/python -m scripts.dispatcher_backlog --show conflicts` 全量拉取（当次输出为准，
   本 opener 落档时为 35 条）。
2. 读 `.claude/skills/task-dispatcher/rules.md` §2 的真身判据与 `scripts/dispatcher_backlog.py` 头注
   （「合并不是覆盖」语义）。

## 三、交付物

1. **`docs/roadmap/conflicts-核真-20260930.md`（新）**：每条冲突一节 `### <id>`，写：
   台账态／真身态／核验命令与原始结果（`git cherry -v main <分支>`、`tasks.md` checkbox、plan 文件存在性、
   `git log -S` 等）／判定（台账对｜真身对｜两方都需修）／处置。穷尽后仍无法定论的条目单列
   「## 保留例外」并逐条写原因与已登记去向（定夺队列或备注）。
2. **`docs/roadmap/任务台账.yaml`**：按逐条判定修正状态（只改判定为「台账 stale」的条目；⛔ 不用
   `--resolve-conflicts truth` 整批覆盖、⛔ 不手改 id／标题／判据字段），改完重跑生成器让 conflicts 归零或
   只剩「保留例外」。

## 四、机器判据

```bash
cd /Users/paulshao/Projects/HumanResource
set -e
doc=docs/roadmap/conflicts-核真-20260930.md
[ -f "$doc" ]
[ "$(grep -c '^### ' "$doc")" -ge 30 ]
./venv/bin/python -m pytest tests/test_dispatcher_backlog.py -q -p no:cacheprovider
PYTHONPATH=. ./venv/bin/python -m scripts.dispatcher_backlog >/dev/null
n="$(PYTHONPATH=. ./venv/bin/python -m scripts.dispatcher_backlog --show conflicts 2>/dev/null | tail -1 | sed 's/[^0-9]*//g')"
[ "$n" -le 3 ]
if [ "$n" -gt 0 ]; then grep -q '^## 保留例外' "$doc"; fi
```

## 五、并发协议（逐字照做）

1. 只 `git add` 本 opener 明确列出的路径。⛔ 禁止 `git add -A` / `git add .` / `git commit -a` / `git stash`。
2. `git status` 里出现别人的改动是正常的，不要停下、不要问、不要顺手提交。
3. ⛔ 不要在 commit 前 pull；push 被拒才 `git pull --rebase --autostash origin main` 重试，最多 3 次。
4. 报 `.git/index.lock` 已存在 → 等 5 秒重试最多 5 次，⛔ 绝不删除该锁。

## 六、红线

- ⛔ 不改判据语义、不删冲突条目、不把「不确定」硬判成「完成」——不确定 ⇒ 进「保留例外」并登记。
- ⛔ 不动 0930A／0930B 的文件；⛔ 不写提问句。
- 台账改动只许影响「状态」轴（生成器对 `队列:`／`闸门:` 轴另有语义，⛔ 不碰）。

## 七、收口

1. 改完重跑 `dispatcher_backlog`（幂等：连跑两次 diff 为空），测试全绿后在分支上 commit，提交信息带 `(0930C)`。
2. 合回 main：`git -C /Users/paulshao/Projects/HumanResource merge --ff-only <分支>`；失败改
   `git merge <分支> --no-edit`；然后 `git push origin main`（被拒按并发协议重试 ≤3 次）。
3. 🔴 `git cherry -v main <分支>` 不得出现 `+` 行；确认内容真在 main 上前不许输出 `OPENER_DONE`。
4. 报告里附 conflicts 前后条数（35 → N），然后顶格 `OPENER_DONE`（或 `OPENER_PARTIAL: <原因>`）。
