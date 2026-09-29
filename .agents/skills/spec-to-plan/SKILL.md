---
name: spec-to-plan
description: 把 OpenSpec 的 spec 转成实现计划（Codex 引擎）。当用户说"出实现计划""spec-to-plan""把 M1 的 spec 变成计划""开始做某个交付单元"时使用。强制以 spec.md 为输入（绝不用 tasks.md），强制注入 Global Constraints，产出三级 `### Task N:` 结构。
---

OpenSpec → 实现计划的唯一接缝（Codex 版）。**正本流程与判据在
`.claude/skills/spec-to-plan/SKILL.md`**（Claude 回退引擎共用）；本文件只覆盖 Codex 差异，
未写在这里的照正本执行。

## 与 Claude 版唯一的实质差异

Claude 版调用插件 `superpowers:writing-plans` 生成计划。Codex **没有这个插件**——
⛔ 不要去找它、不要报「Unknown skill」、也⛔ 不因此留步（那只是 Claude 侧的环境故障语义）。
改由本会话**直接按下面硬格式写计划**；定位输入、交付单元划分、Global Constraints 注入、
端到端提取验证、输出清单，照正本第 1–7 节执行。

## 计划文件硬格式（机器判据，⛔ 不满足后续 run-build 必炸）

1. 任务标题必须三级 `### Task N: `（⛔ 不是二级 `##`）——`scripts/codex_sdd_runner.py` 按三级标题
   抽取任务。写完自查 `grep -c '^### Task ' <计划文件>` 等于实际任务数且不为 0。
2. 必须有 **Global Constraints** 段，内容从 `CLAUDE.md`「工程铁律」与「合规红线」逐字复制与本交付单元
   相关条目——reviewer 把它当注意力透镜，缺了会静默漏查（这个失败不报错）。
3. 每个 Task 有确切文件路径、完整代码、确切命令与预期输出；⛔ 无 TBD/TODO/「适当处理错误」类占位符。
4. 每个有副作用的动作独占一个 Task 步骤且带幂等键 `{thread_id}:{node_name}:{business_key}`（第一铁律）。
5. spec 里每条 `### Requirement:` 都要能指到至少一个 Task。

## 边界

- ⛔ 不需要 git worktree：本技能只产出一份 `docs/superpowers/plans/YYYY-MM-DD-<unit>.md`，
  不写代码、不建分支。隔离工作区是 run-build 的事。
- 计划出完就停，⛔ 不在本响应里开始实现。
