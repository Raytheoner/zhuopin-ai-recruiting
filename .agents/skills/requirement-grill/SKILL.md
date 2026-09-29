---
name: requirement-grill
description: 新场景开工前的需求收敛（需求 grill，Codex 版）：与 Shao Peishen 多轮问清前沿、把能自查的事实查掉、产出 intent 进 openspec。当用户说"需求 grill""收敛需求""新场景开工前问一轮"时使用。⛔ 无人值守场景不跑本技能（它就是要人答）。
---

需求 grill（Codex 版）。**正本三条机制（M1 设计树 / M2 自己查 / M3 前沿清空）、分流判据、
提问格式、intent 骨架与 G1 落档在 `.claude/skills/requirement-grill/SKILL.md`**，未写在这里的照正本执行。

## Codex 差异

- **无引擎差异**：本技能在有人在场的交互会话里跑（⛔ 不进无头泳道、不进定时自动化）。
- 事实源清单里「项目约束层」一项同时含 `CLAUDE.md`（规则真源）与 `AGENTS.md`（Codex 适配层，§1–§4）。
- 产出的 intent / 需求树草稿照正本骨架逐字写（`tests/test_requirement_grill_skill.py` 认的节名⛔ 改）。
