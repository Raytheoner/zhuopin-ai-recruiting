---
name: lane-dispatch
description: 编排并发车一批泳道任务（Codex 引擎）。当 Shao Peishen 说"开始泳道看护""发一批泳道""跑下一批""lane-dispatch"时使用。全流程：扫待办 → 判触碰区分泳道 → 写 opener 进编排文件 → dry-run 核对 → 出看护 opener 与发车命令。⛔ 本技能不亲自执行任何 opener。
---

把「下一批做什么」变成一次可发车的泳道编排（Codex 版）。**正本四步流程与入选判据在
`.claude/skills/lane-dispatch/SKILL.md`**，未写在这里的照正本执行。真源仍是
`docs/openers/OP-0820-全量编排.md` + `docs/openers/run-lanes.sh`。

## Codex 差异（写 opener 与发车时必须遵守）

1. **执行环境写 `Codex`**：无头块【设置】行 `执行环境: Codex`（⛔ 不再写 `CC`）。
2. **⛔ 不写 set_session_title**：Codex 没有 `mcp__ccd_session_mgmt__set_session_title` 工具，
   无头块第三行直接写任务。这条正是 `run-lanes.sh` 预检对无头块的要求。
3. **`superpowers` 不可达条款换成 Codex 版**：每条走 spec-to-plan / run-build 的 opener 逐字写死：
   「`codex` CLI 不可达、或 worktree 里缺 `AGENTS.md` ⇒ 登记『⏸ 留步：执行环境不可达』并停」。
   ⛔ 不写「按磁盘 SKILL.md 手工走」。
4. **发车命令不变**：`bash docs/openers/run-lanes.sh --dry-run` 核对（看「执行引擎：codex」一行与
   「模型 X（来源）」），再经动作通道 `{"action":"launch-lanes","args":"--full-auto --yes --only <编号>"}`
   发车。`run-lanes.sh` 默认引擎即 codex，⛔ 不需要额外参数。
5. **模型档词汇不变**：只有推理密集条目在【设置】行写 `｜ 模型: Opus`；映射见 `AGENTS.md` §2。

## 机器闸（照正本，⛔ 不绕）

```bash
bash docs/openers/run-lanes.sh --dry-run --only <本批编号,逗号分隔>
python3 scripts/opener_split_check.py
./venv/bin/python -m pytest tests/test_doc_size_budget.py -q
```
