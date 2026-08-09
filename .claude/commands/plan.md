---
name: "出实现计划"
description: "把 OpenSpec 的 spec 转成 Superpowers 实现计划（终端版入口，内容以 skill 为准）"
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Skill
category: "Workflow"
---

调用 `spec-to-plan` skill 并按其内容执行。参数 `$ARGUMENTS` 作为变更名传入。

真源在 `.claude/skills/spec-to-plan/SKILL.md`。本文件只是 Claude Code 终端的入口——`.claude/commands/` 是遗留格式，只在终端生效；skills 在终端、Desktop、Cowork 都能用。**改规则请改 skill，不要改这里。**
