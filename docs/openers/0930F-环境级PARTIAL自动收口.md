[Mac]0930F-环境级PARTIAL自动收口
【设置】执行环境: Codex ｜ Session: 新开（run-lanes 无头起）｜ 分支: lane-0930f-env-partial-collect ｜ worktree: ✅ 勾（改收口器与测试）｜ 工作区: .claude/worktrees/lane-0930f-env-partial-collect ｜ 派发: Codex·泳道看护（automation hr）

## 零、为什么

`0930D` 的收口器 `scripts/lane_collect.py` 只自动合并 `status=OK 且机器判据 PASS` 的泳道（PARTIAL 一律留人工）。
但 codex 泳道当前**必然**会因沙箱只读 `.git` 而报 `OPENER_PARTIAL`（0930A/B/C 三条实证）——于是每批都
卡在最后一步等一次人工合并，自动化名存实亡。本 opener 给收口器补一条机判规则：**留步仅为环境级
沙箱只读阻断**（git 提交 / `.codex` 只读等）且有机器判据 PASS／无判据块的泳道，也纳入自动合并。
顺带修正一处时序：codex 泳道的 `## 机器判据` 目前在主工作区、早于代提交执行（判的是未含泳道产物的
main）；本 opener 要求收口器在**泳道 worktree 内**实跑判据后再决定是否合并。

## 一、授权与边界（Shao Peishen 2026-09-30 答 1a 授权的**专门 opener**）

⛔ 只许改：`scripts/lane_collect.py`、`tests/test_lane_collect.py`。
⛔ 不改 `run-lanes.sh` 及任何执行器、不改其它脚本/规则/技能；⛔ 不改写任何泳道产物内容。

## 二、前置

1. 读 `scripts/lane_collect.py` 头注与实现（stage1/stage2、`collect.tsv` 列、`_gate_for`）与
   `tests/test_lane_collect.py`。
2. 读三条真实收工日志 `.claude/handoff/lanes-20260930-072754/*.log` 的 `OPENER_PARTIAL:` 行与「⏸ 留步」节，
   作为判据样本（三条都必须被新规则判为「环境级」；另自造一条业务留步样本必须判为非环境级）。

## 三、交付物

1. **`scripts/lane_collect.py`**：
   - 新增模块级纯函数 **`is_sandbox_git_only_partial(log_text: str) -> bool`**：读日志全文，判「留步是否仅为
     环境级沙箱只读阻断」——判据要素：① 存在 `OPENER_PARTIAL:` 行；② 其理由命中沙箱只读签名
     （沙箱／Seatbelt／workspace-write 与 只读／Operation not permitted／index.lock 同现）；③ 日志里的
     「⏸ 留步」逐条都属该签名（任何一条业务留步 ⇒ 返回 False）。具体正则由实现者定，但必须通过下面判据块
     的真实样本断言。
   - stage1：`status=PARTIAL` 且 `is_sandbox_git_only_partial(<lane 日志>)` 为真 ⇒ 在 `collect.tsv`
     新增列 `env_only=1`（其余为 0）；列名与列序要与旧读法向后兼容（旧行缺列按 0 处理）。
   - stage2：合并判据改为——
     * `status=OK`：保持现有行为（gate 取自 run-lanes 的 gates.tsv）；
     * `env_only=1`：在**该泳道 worktree 内**按与 run-lanes 同一提取逻辑取 `docs/openers/<id>-*.md` 的
       `## 机器判据` bash 块（同黑名单安全预检 `git push|commit|reset|clean|rm -rf|sudo|curl|ssh|launchctl|…`
       与同超时），PASS 或无判据块 ⇒ 合并（`--ff-only`，失败改 `--no-edit`）；未过或 UNSAFE ⇒ 不合并，
       记 `NEEDS-MANUAL-GATE` 并点名；
     * 其余 PARTIAL / GATE-*：不合并（现行行为）。
   - 汇总输出里分别点名「自动合并（OK）」「自动合并（环境级 PARTIAL）」「留人工」三类，便于看护引用。
2. **`tests/test_lane_collect.py`**：新增用例——① 真实 0930A/B/C 三条日志 → `is_sandbox_git_only_partial` 全真；
   ② 业务留步对照样本 → 假；③ stage1 对 PARTIAL+环境级写 `env_only=1`、对其它 PARTIAL 写 0；
   ④ stage2 对 `env_only=1` 且 worktree 判据 PASS/无判据 ⇒ 合并；判据 FAIL ⇒ `NEEDS-MANUAL-GATE` 不合并
   （用临时 git 仓与假 worktree，⛔ 不碰真实仓库）。

## 四、机器判据

```bash
cd /Users/paulshao/Projects/HumanResource
set -e
./venv/bin/python -m pytest tests/test_lane_collect.py -q -p no:cacheprovider
./venv/bin/python -m pytest tests/test_run_lanes_model_codex.py -q -p no:cacheprovider
./venv/bin/python - <<'PY'
import os
from pathlib import Path
from scripts.lane_collect import is_sandbox_git_only_partial
main = os.environ.get('HR_GATE_MAIN', '.')
logs = sorted(Path(main, '.claude/handoff/lanes-20260930-072754').glob('*.log'))
hits = [p.name for p in logs if is_sandbox_git_only_partial(p.read_text(encoding='utf-8', errors='replace'))]
assert len(hits) == 3, hits
assert not is_sandbox_git_only_partial('OPENER_PARTIAL: 等 .51 低峰窗口再发版')
assert not is_sandbox_git_only_partial('OPENER_PARTIAL: 沙箱挡 git\n\n## ⏸ 留步\n- 等汤丽萍回件')
print('matcher OK:', hits)
PY
grep -q 'is_sandbox_git_only_partial' scripts/lane_collect.py
./venv/bin/python scripts/lane_collect.py stage2 --dry-run --repo . --logdir .claude/handoff/lanes-20260930-072754 >/dev/null 2>&1 || true
```

## 五、并发协议（逐字照做）

1. 只 `git add` 本 opener 明确列出的路径。⛔ 禁止 `git add -A` / `git add .` / `git commit -a` / `git stash`。
2. `git status` 里出现别人的改动是正常的，不要停下、不要问、不要顺手提交。
3. ⛔ 不要在 commit 前 pull；push 被拒才 `git pull --rebase --autostash origin main` 重试，最多 3 次。
4. 报 `.git/index.lock` 已存在 → 等 5 秒重试最多 5 次，⛔ 绝不删除该锁。

## 六、红线

- ⛔ 不放松合并安全阀：业务留步、判据 FAIL、判据 UNSAFE 一律不合并；⛔ 不硬解合并冲突（abort + 点名）。
- ⛔ 不动 `run-lanes.sh`；⛔ 不改写泳道产物；⛔ 不写提问句。
- 任何判不准的样本 ⇒ 保守判「非环境级」（不合并）并在报告里登记，输出 `OPENER_PARTIAL`。

## 七、收口

1. 测试全绿后在分支上 commit（只列本 opener 的路径），提交信息带 `(0930F)`。
2. 合回 main：`git -C /Users/paulshao/Projects/HumanResource merge --ff-only <分支>`；失败改
   `git merge <分支> --no-edit`；然后 `git push origin main`（被拒按并发协议重试 ≤3 次）。
   注：本 opener 的收口同样受沙箱只读 `.git` 限制——若无法自行提交，按现行惯例如实 `OPENER_PARTIAL`
   （`lane_collect` stage1 会代提交到分支，stage2 在新规则落地后覆盖不了自己这一批）。
3. 🔴 `git cherry -v main <分支>` 不得出现 `+` 行；确认内容真在 main 上前不许输出 `OPENER_DONE`。
4. 报告里给出新增/修改文件清单与测试尾行，然后顶格 `OPENER_DONE`（或 `OPENER_PARTIAL: <原因>`）。
