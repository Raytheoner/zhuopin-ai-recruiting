---
name: kickoff
description: 生成新开会话用的开场 Prompt（Opener，Codex 版），把界面、Session 新旧、worktree、工作目录、前置检查、完成标准全部写死。当用户说"给我开场 prompt""kickoff""新起一个会话要怎么说""run-build 的 opener""转场 prompt"时使用。
---

生成一份**可一键复制**的开场 Prompt（Codex 版）。**编号与抬头、两条抬头规则、并发四条、
引用式 Opener、一键复制铁律、字母池用尽等正本在 `.claude/skills/kickoff/SKILL.md`**，未写在这里的照正本执行。

## Codex 差异（逐条对照正本）

1. **【设置】行**：`执行环境: Codex`（⛔ 不再写 `CC`）。
2. **⛔ 没有 set_session_title**：Codex 没有 `mcp__ccd_session_mgmt__set_session_title` 工具，
   opener 第 3 行直接写任务；标题靠会话名/摘要，⛔ 照抄 Claude 侧「第 3 行必须调 set_session_title」那套。
3. **界面口径**：实现阶段（spec-to-plan / run-build / git 提交）在 Codex 里做；Cowork（隔离 VM）
   对 `.git/` 只读限制不变，git 操作⛔ 不在 Cowork 侧做。
4. **模型档词汇不变**：默认 Sonnet 档，只有推理密集条目写 `｜ 模型: Opus`；映射见 `AGENTS.md` §2。
5. **无头块**（写进 `OP-0820-全量编排.md`、由 run-lanes.sh 起的）：【设置】行 `执行环境: Codex`、
   不写 set_session_title；其余照正本「引用式 Opener」与 lane-dispatch 的形状。

## 阶段环境对照（Codex 版）

| 阶段 | 界面 | Session | Worktree | 工作目录 |
|---|---|---|---|---|
| openspec-propose 需求与契约 | Codex 或 Cowork | 新开 | ❌ | 主检出 |
| spec-to-plan 出实现计划 | Codex | 新开 | ❌ | 主检出 |
| run-build 写代码 | Codex | 新开 | ✅ | worktree 内 |
| openspec-archive-change 收口 | Codex 或 Cowork | 新开 | ❌ | 主检出 |
