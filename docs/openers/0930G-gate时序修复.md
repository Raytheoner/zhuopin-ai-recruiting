[Mac]0930G-gate时序修复
【设置】执行环境: Codex ｜ Session: 主会话直执（改执行器按 lane-dispatch 正本禁入泳道，⛔ 不派泳道）｜ 分支: main ｜ worktree: ❌ 不勾（改 `run-lanes.sh` 等执行器文件）｜ 工作区: 仓库根 ｜ 派发: Codex·泳道看护（automation hr）

## 零、为什么（Q-63 根因）

`0930E` 泳道自报 DONE 且修正正确，却被判 **GATE-FAIL**：`run-lanes.sh` 的机器判据在**主工作区 main**、
**早于** `lane_collect stage2` 合并执行；codex worktree 泳道受沙箱限制不能自行提交，产物此时只在
分支上 ⇒ 判据读到的是「未含泳道产物」的旧状态（main 31 passed vs worktree 34 passed）。任何带
`## 机器判据` 的 codex worktree 泳道都会被同一条时序假阴拖住。

## 一、授权与边界（Shao Peishen 2026-09-30 答 1a，授权执行 Q-63 根因修复）

只改：`docs/openers/run-lanes.sh`、`AGENTS.md`（§4 一行）、`tests/test_run_lanes_model_codex.py`、
`docs/openers/0930F-环境级PARTIAL自动收口.md`（判据块改用 `$HR_GATE_MAIN`）。⛔ 不改 run-lanes 的
状态机语义、不改泳道产物、不改其它规则。

## 二、改动

1. `gate_extract <id> [repo]`／`gate_run <file> <log> [repo]` 增加运行根参数（默认 `$REPO`）。
2. `run_lane` 判据段：`ENGINE==codex && is_wt==1` ⇒ `gate_repo="$run_dir"`（判据在泳道 worktree 内跑）。
3. 历史 opener 的判据块首行硬编码 `cd /Users/paulshao/Projects/HumanResource` ⇒ worktree 运行时由执行器
   改写为 `cd "$(pwd -P)"`（运行时根）。
4. `gate_run` 注入环境变量：`HR_GATE_REPO`（实际运行根）与 **`HR_GATE_MAIN`**（主工作区）——需要读
   主仓独有状态（gitignored 日志等）的判据块显式用 `$HR_GATE_MAIN`（0930F 的判据块已改为此形态）。
   `AGENTS.md` §4 同步写明该约定。

## 三、机器判据

```bash
cd /Users/paulshao/Projects/HumanResource
set -e
./venv/bin/python -m pytest tests/test_run_lanes_model_codex.py tests/test_run_lanes_gate.py tests/test_run_lanes_relay.py tests/test_run_lanes_model.py tests/test_lane_collect.py -q -p no:cacheprovider
grep -q 'HR_GATE_MAIN' docs/openers/run-lanes.sh
grep -q 'HR_GATE_MAIN' AGENTS.md
grep -q 'pwd -P' docs/openers/run-lanes.sh
./venv/bin/python -m pytest tests/test_run_lanes_model_codex.py -q -p no:cacheprovider -k worktree_gate
```

## 四、验证与收口

- 回归用例 `test_codex_worktree_gate_runs_inside_worktree`：判据块故意用历史形态（硬编码主仓路径＋
  只存在于泳道产物的 `gate-marker.txt`）——修复前必假阴 GATE-FAIL，修复后判 OK；47 条相关测试全绿。
- 主会话直执：一次性提交（执行器文件走不了提交通道/泳道），随后 `git push origin main` 并复核
  `git cherry -v main` 无残留分支未合。
