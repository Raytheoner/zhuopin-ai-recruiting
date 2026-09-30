[Mac]0930I-codex-upeak口径修复
【设置】执行环境: Codex ｜ Session: 新开（run-lanes 无头起）｜ 分支: lane-0930i-upeak-caliber ｜ worktree: ✅ 勾（改脚本与测试）｜ 工作区: .claude/worktrees/lane-0930i-upeak-caliber ｜ 派发: Codex·`[Mac]0930R` ｜ 模型: Haiku

## 零、为什么

`results.tsv` 第 13 列 `upeak`（0920G 引入，口径＝该 session 单轮上下文峰值）**对 codex 泳道失真**：

- `codex exec --json` 全程只在收尾发**一次** `turn.completed`，其 `usage` 是**整场累计值**
  （0930F 实证：`input_tokens=6454677`、`cached_input_tokens=6394496`、`cache_write=0`、`turns=1`）。
- 现实现 `peak = max(input + cache_read + cache_write)` 于是＝**累计量再叠加一份 cache_read**
  ⇒ 0930F 记为 12,849,173（真实是 6,454,677）。
- 后果：`run-lanes.sh` 第 1116 行的「≥150k 越线播报」会对**每一条** codex 泳道误报（0930A/B/C/E/F
  五条全中），看护无法据它判该不该转场。

claude 分支的同族教训已写在执行器注释里（`docs/openers/run-lanes.sh` 第 136／889 行）：
**累计量不能充峰值**，没有 `iterations` 就留 `-`。本 opener 把 codex 侧的口径改成同等诚实。

## 一、授权与边界（Shao Peishen 2026-09-30 12:0x 答 4a）

⛔ 只许改：`scripts/codex_jsonl_summary.py`、新建 `tests/test_codex_jsonl_summary.py`。
⛔ 不改 `run-lanes.sh`／`dispatcher_event.sh`／任何其它执行器与技能；⛔ 不改 7 列 TSV 的列名与列序
（下游 `results.tsv`／`usage.tsv` 读法零改动）。

## 二、交付物

1. **`scripts/codex_jsonl_summary.py`**：
   - `turn.completed` 的 `usage` 一律按**累计值**处理：与上一条 `turn.completed` 作差得该轮增量
     （首条事件的增量＝其自身）。`peak = max(增量 input + 增量 cache_write)`，
     ⛔ **不再加 cache_read**（它已包含在 `input_tokens` 里，重复计入是本次失真的一半）。
   - 单事件（codex 当前的常态）⇒ `peak = input + cache_write`；0930F 样本应为 **6,454,677**。
   - 模块 docstring 写死口径：**codex 的 JSONL 不提供单轮上下文峰值**，本列＝「最大单轮累计用量」，
     ⛔ 不得与 claude 的 `usage.iterations` 口径并排比较、⛔ 不得单独作为 150k 转场判据
     （codex 泳道的转场判据只看 `CTX-RELAY` 哨兵）。
   - `turns`／`in`／`out`／`cache_read`／`cache_write` 五列语义与现行为一字不动（仍是累计口径）。
2. **`tests/test_codex_jsonl_summary.py`**（新建，只碰临时文件，⛔ 不动真实批次目录）：
   ① 两条累计 `turn.completed` ⇒ 增量取 max；② 0930F 真实 usage 样本 ⇒ `peak == "6454677"`；
   ③ 全非 JSON 输入 ⇒ 七列全 `-` 且文本原样兜底（防退）；④ `agent_message` 文本拼接顺序不变。

## 三、机器判据

```bash
cd /Users/paulshao/Projects/HumanResource
set -e
./venv/bin/python -m pytest tests/test_codex_jsonl_summary.py -q -p no:cacheprovider
./venv/bin/python -m pytest tests/test_run_lanes_model_codex.py tests/test_task_dispatcher.py -q -p no:cacheprovider
./venv/bin/python - <<'PY'
import json
from scripts.codex_jsonl_summary import summarize

real = json.dumps({"type": "turn.completed", "usage": {
    "input_tokens": 6454677, "cached_input_tokens": 6394496,
    "cache_write_input_tokens": 0, "output_tokens": 52930, "reasoning_output_tokens": 24921}})
f, _, _ = summarize(real + "\n")
assert f[6] == "6454677", f          # 旧实现 12849173

seq = "\n".join([
    json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1000, "output_tokens": 10}}),
    json.dumps({"type": "turn.completed", "usage": {"input_tokens": 3000, "output_tokens": 40}}),
])
g, _, _ = summarize(seq + "\n")
assert g[6] == "2000", g             # 第二轮增量 = 3000 - 1000
assert g[1] == "3000" and g[5] == "2", g   # 累计 input 与 turns 仍照记

raw = "not json at all\n"
h, txt, saw = summarize(raw)
assert h == ["-"] * 7 and saw is False and "not json at all" in txt, (h, txt, saw)
print("upeak 口径 OK:", f[6], g[6])
PY
MAIN="${HR_GATE_MAIN:-/Users/paulshao/Projects/HumanResource}"
test -f "$MAIN/.claude/handoff/lanes-20260930-082553/机制-环境级收口-0930F.json"
echo "真实样本存在（只读引用，不修改）"
```

## 四、并发协议（逐字照做）

1. 只 `git add` 本 opener 明确列出的路径。⛔ 禁止 `git add -A` / `git add .` / `git commit -a` / `git stash`。
2. `git status` 里出现别人的改动是正常的，不要停下、不要问、不要顺手提交。
3. ⛔ 不要在 commit 前 pull；push 被拒才 `git pull --rebase --autostash origin main` 重试，最多 3 次。
4. 报 `.git/index.lock` 已存在 → 等 5 秒重试最多 5 次，⛔ 绝不删除该锁。
5. **并行同伴**：同批 `0930H` 在改 `docs/findings/` 与 `openspec/changes/hr-wecom-aibot-liaison/tasks.md`
   ——⛔ 本泳道不碰 `docs/`、`openspec/`；`0930I` 只碰 `scripts/codex_jsonl_summary.py` 与 `tests/`。

## 五、红线

- ⛔ 不动任何真实批次目录与 `results.tsv`（只读引用 0930F 样本做断言）。
- ⛔ 不改列名/列序/下游读法；⛔ 不用 `-` 之外的占位编造用量；⛔ 不写提问句。
- 拿不准 `usage` 字段是否存在（老版本 codex 可能缺字段）⇒ 缺字段按 0 处理并在 docstring 写明，
  ⛔ 不让整个解析退化成 `-`。

## 六、收口

1. 机器判据全绿后，改动**留在 worktree**（Codex 泳道 ⛔ 不自行 git 提交／合并——`run-lanes.sh`
   退出时由 `scripts/lane_collect.py` stage1 代提交到泳道分支，stage2 只把「OK＋判据 PASS」合回 main）。
2. 报告里给出：改动文件清单、新旧口径对照（0930F：12,849,173 → 6,454,677）、机器判据尾行。
3. 顶格输出 `OPENER_DONE`（判据不过 ⇒ `OPENER_PARTIAL: <哪条没过、实测值>`）。
