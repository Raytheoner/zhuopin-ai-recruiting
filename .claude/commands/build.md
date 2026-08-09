---
name: "执行实现计划"
description: "用 Superpowers 子代理驱动开发执行计划（终端版入口，内容以 skill 为准）"
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Skill, Task
category: "Workflow"
---

调用 `run-build` skill 并按其内容执行。参数 `$ARGUMENTS` 作为计划文件路径传入。

真源在 `.claude/skills/run-build/SKILL.md`。本文件只是 Claude Code 终端的入口——`.claude/commands/` 是遗留格式，只在终端生效；skills 在终端、Desktop、Cowork 都能用。**改规则请改 skill，不要改这里。**
