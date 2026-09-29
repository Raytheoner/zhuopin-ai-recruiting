---
name: liaison-unpack
description: HR 企微回件拆件（Codex 引擎）：拆件会话按章程（`tools/liaison/unpack/charter.py` 指的那份 SKILL.md）无头执行。当回件桥打标即开班、或用户说"拆件""unpack"时使用。
---

回件拆件（Codex 版）。**章程正本与拆件会话的职责在 `.claude/skills/liaison-unpack/SKILL.md`
（`tools/liaison/unpack/charter.py` 解析的那份）**，未写在这里的照正本执行。

## Codex 差异

1. **起活引擎**：`tools/liaison/unpack/dispatch.py` 默认以 codex 引擎起无头拆件会话，argv 形状：
   `codex exec --json --sandbox workspace-write -c approval_policy=never -c forced_login_method=chatgpt -`
   （prompt 从 stdin 写，与 Claude 版同协议）。
2. **约束边界现状**：章程 §三「只改白名单路径」由 workspace-write 沙箱 + 章程正文 + 网络沙箱
   （禁 `git push`）共同约束；「Edit 路径级 deny」的 Codex PreToolUse hook 化是**已知缺口**
   （`AGENTS.md` §4），⛔ 不在章程里假装它已实现。
3. **无 set_session_title**：Codex 拆件会话不调 `mcp__ccd_session_mgmt__set_session_title`。
