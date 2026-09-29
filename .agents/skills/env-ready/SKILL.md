---
name: env-ready
description: 卓品AI招聘项目的开发环境自检与修复（Codex 版）。当用户说"环境自检""检查环境""env-ready""开发环境好了没""新同事上手"时使用。逐项检查 Codex CLI/Node/OpenSpec/Python/Git/技能与 SDD 执行器/脏文件，能自动修的当场修，只把需要人操作的列出来。
---

检查本项目的开发环境是否就绪（Codex 版）。逐项验证，结果做成一张表，然后主动修复能修的。

## 检查项（Codex 口径）

1. **Codex CLI** —— `codex --version`（本机实测 0.158.0-alpha.2.1），且
   `codex -c forced_login_method=chatgpt login status` 出「Logged in using ChatGPT」。
   ⛔ 用不带覆盖键的 `codex login status` 会因全局 `forced_login_method="api"` 与 auth.json 冲突误报
   Not logged in——那是配置错位不是未登录（见 `AGENTS.md` §1）。
2. **Node** —— `node -v` ≥ 20.19.0。
3. **OpenSpec CLI** —— `openspec --version` 精确 1.9.0（⛔ 不用 latest）；command not found 时
   `npm install -g @fission-ai/openspec@1.9.0`；EACCES 时 `npm config set prefix ~/.npm-global` 后重装。
4. **OpenSpec 结构** —— `openspec/config.yaml` 存在、`openspec list` 能跑。
5. **Python** —— `./venv/bin/python -V` 必须 3.14.x（`requires-python = ">=3.14,<3.15"`）；
   值守环境 `tools/liaison/.venv/bin/python -V` 存在。
6. **Git** —— 仓库已 init 且有首提交；`.gitignore` 存在。
7. **SDD 执行层（替代 superpowers）** —— `scripts/codex_sdd_runner.py` 存在；
   `grep -c '^### Task ' <任一 plan>` 用法可用。⛔ 不再检查 superpowers 插件。
8. **指令层** —— `CLAUDE.md` 与 `AGENTS.md` 都存在，AGENTS.md 含「读 CLAUDE.md」指针。
9. **残留脏文件** —— 清掉 `.openspec-test-*`、`.DS_Store`。

## 输出

一张表：检查项 / 状态（✅ ⚠️ ❌）/ 实际值 / 处理动作。表下分三段：**我已修复** / **需要你自己做** /
**换个界面才能做**（本界面能力不足的事项）。全绿则一句话确认，并提示下一步是 `spec-to-plan`。

## 注意

- 能修的就修，不要只报告；不改 `openspec/` 下任何变更产物；不 `git commit`，除非用户明确要求。
