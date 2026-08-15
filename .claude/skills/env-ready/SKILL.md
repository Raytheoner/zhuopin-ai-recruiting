---
name: env-ready
description: 卓品AI招聘项目的开发环境自检与修复。当用户说"环境自检""检查环境""env-ready""开发环境好了没""为什么 opsx 命令跑不了""新同事上手"时使用。逐项检查 Node/OpenSpec CLI/Superpowers/Python/Git/脏文件，能自动修的当场修，只把需要人操作的列出来。
---

检查本项目的开发环境是否就绪。逐项验证，**结果做成一张表**，然后主动修复能修的。

## 先判断你在哪

不同界面能做的事不一样，先说清楚再检查：

- **Claude Code（终端 / Desktop Code tab）**：bash 跑在用户本机，能装东西、能跑 git、能派发 subagent 做真实开发
- **Cowork**：bash 跑在隔离 Linux VM 里，**装的东西不落到用户 Mac 上**。能读写项目文件、能跑 openspec（因为它只操作项目目录），但装 npm 包对用户本机无效

如果你在 Cowork 里，第 2、5 项的"修复"要明确告诉用户「这只在沙箱里生效，你本机还得装一次」。

## 检查项

1. **Node 版本** —— `node -v`，OpenSpec 要求 ≥ 20.19.0
2. **OpenSpec CLI** —— `openspec --version`，要求精确 1.9.0（不用 `latest`——CI 的机器门禁把版本硬编码锁在 1.9.0，理由同工程铁律 5：校验规则漂移会让「昨天绿今天红」变成无从解释的事；本地版本落后或超前 CI 都可能导致「本地过了 CI 不过」或反过来）
   - 若 command not found：执行 `npm install -g @fission-ai/openspec@1.9.0`
   - 若 EACCES 权限错误：改用 `npm config set prefix ~/.npm-global && npm install -g @fission-ai/openspec@1.9.0`，并提示把 `~/.npm-global/bin` 加进 PATH
   - 若版本号不是 1.9.0（不论新旧）：重装到精确版本，`npm install -g @fission-ai/openspec@1.9.0`
3. **OpenSpec 项目结构** —— `openspec/config.yaml` 存在，`openspec list` 能跑通
4. **Superpowers 可用性** —— 检查当前会话能否调用 `superpowers:writing-plans` 与 `superpowers:subagent-driven-development`
   - 不可用时**不要**建议用户重装。先说明：superpowers 是 Claude Code 插件，作用域绑定在某个目录上，且不一定在所有界面暴露
   - 给出的动作是：在 Desktop 的 **Code tab** 里确认能否调用；实现阶段必须在能调用它的界面里做
5. **Python** —— `python3 -V`，需 ≥ 3.11
6. **Git** —— 是否 `git init`；是否有首次提交；`.gitignore` 是否存在
   - 注意：Cowork 的沙箱对 `.git/` 常无写权限，git 操作要在 Claude Code 里做
7. **残留脏文件** —— 清掉 `.openspec-test-*`、`.DS_Store`
8. **CLAUDE.md** —— 存在且含「工具链分工」与「工程铁律」两节

## 输出

一张表：检查项 / 状态（✅ ⚠️ ❌）/ 实际值 / 处理动作。

表下面分三段：

- **我已修复**：实际执行了什么
- **需要你自己做**：真正需要人操作的（插件安装、PATH、凭据申请），每条给确切命令或步骤
- **换个界面才能做**：本界面能力不足的事项，说明该去哪个界面

全绿则一句话确认，并提示下一步是 `spec-to-plan`。

## 注意

- 能修的就修，不要只报告
- 不改 `openspec/` 下任何变更产物
- 不 `git commit`，除非用户明确要求
