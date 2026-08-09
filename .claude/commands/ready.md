---
name: "环境自检"
description: "检查本机开发环境是否就绪（终端版入口，内容以 skill 为准）"
allowed-tools: Bash, Read, Glob, Skill
category: "Workflow"
---

调用 `env-ready` skill 并按其内容执行。

真源在 `.claude/skills/env-ready/SKILL.md`。本文件只是 Claude Code 终端的入口——`.claude/commands/` 是遗留格式，只在终端生效；skills 在终端、Desktop、Cowork 都能用。**改规则请改 skill，不要改这里。**
