---
name: run-build
description: 执行 spec-to-plan 产出的实现计划（Codex 引擎）。当用户说"执行计划""run-build""开始写代码""跑这份 plan""继续实现"时使用。开工前强制检查 Global Constraints 段，缺失则拒绝执行。
---

执行实现计划（Codex 版）。**正本流程在 `.claude/skills/run-build/SKILL.md`**（前置检查五条、
review 关注点、收口 `git cherry` 核真身、归档顺序），未写在这里的照正本执行。

## 唯一的实质差异：执行器

Claude 版调用插件 `superpowers:subagent-driven-development`。Codex **没有这个插件**，改用仓库内置
SDD 执行器（⛔ 不要去找插件、不要报「Unknown skill」）：

```bash
./venv/bin/python scripts/codex_sdd_runner.py --plan <计划文件> [--tasks N-M] [--yes] [--model deepseek-v4-pro]
```

执行器负责：按 `### Task N:` 切任务 → 每个 Task 起独立 `codex exec`（`--sandbox workspace-write`，
cwd＝worktree）跑 TDD 五步 → 每个 Task 两次只读 `codex exec` review（Spec 合规 + 代码质量）→
更新 `.superpowers/sdd/<计划名>/progress.md` → 全部完成后全分支 Final Review。

⛔ 不要退化成自己逐个任务手写代码——那丢掉上下文隔离、两阶段 review、进度台账。

## Codex 特有前置检查（在正本五条之外）

1. 本机 `codex` CLI 可用：`codex --version`。不可用 ⇒ 停，按 `AGENTS.md` §1 修。
2. worktree 里要有 `AGENTS.md`（含「读 CLAUDE.md」指针）——它随 main 分支进 worktree；
   老分支没有 ⇒ 开工前 `git merge main` 一次（只 ff，⛔ 不 rebase 在跑的分支）。
3. `--model` 默认不传（走 `AGENTS.md` 的 Sonnet 档映射）；Opus 档 opener 由 `run-lanes.sh` 按
   【设置】行自动映射，手工跑时显式 `--model deepseek-v4-pro` 即可。
