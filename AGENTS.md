# AGENTS.md —— Codex 执行环境适配层

> 本仓库的**约束真源仍是根目录 `CLAUDE.md`**（Claude 回退引擎与 Codex 引擎共用，避免双写漂移）。
> **开工第一步：完整读 `CLAUDE.md`**（约 24 KB，未超 32 KB 指令预算），把其中的工程铁律、
> 合规红线、部署约束、决策代理、数据模型要点、沟通纪律全部当作本会话的硬约束。
> 本文件只放 **Codex 与 Claude 不同的部分**；未写在这里的，一律以 `CLAUDE.md` 为准。

## 1. 执行引擎（2026-09-29 迁移，已验证）

- 无头执行器三件套——`docs/openers/run-lanes.sh`、`scripts/dispatcher_event.sh`、
  `tools/liaison/unpack/dispatch.py`——全部双引擎：环境变量 `HR_AGENT_ENGINE` 默认 `codex`，
  `claude` 是回退引擎。⛔ 不要再在脚本里硬写 `claude -p`。
- codex 引擎的无头调用形态（唯一真源是三个执行器自身）：
  `codex exec --json --sandbox workspace-write -c approval_policy=never -c forced_login_method=chatgpt -m <模型> -c model_reasoning_effort=<档> - < prompt`
- ⚠️ 本机 codex CLI（0.158.0-alpha）的 `-a/--ask-for-approval` 旗标**实测被参数解析器拒绝**
  （help 列出、实跑报 unexpected argument）：审批一律走 `-c approval_policy=…`。
- `-c forced_login_method=chatgpt` 是本机对齐项：全局 `~/.codex/config.toml` 写死
  `forced_login_method="api"`，而 `~/.codex/auth.json` 的 `auth_mode` 是 `chatgpt`——不覆盖会
  「API key login is required, but ChatGPT is currently being used」启动即失败。⛔ 不要删这行。
- codex **没有** `--max-budget-usd` / `--output-format` / `--allowedTools` / `--strict-mcp-config`
  旗标；`--budget` 对 codex 引擎不生效（参数保留只为 claude 回退与历史契约）。

## 2. 模型分级映射（opener 词汇不变）

- opener 的「模型: Opus|Sonnet|Haiku」三档词汇**保持不变**（编排文件、号池台账都不动），只在执行器内映射：
  Opus → deepseek-v4-pro（reasoning=max）｜ Sonnet → deepseek-v4-pro（reasoning=high）｜
  Haiku → deepseek-flash（reasoning=low）。
- 覆盖键：`HR_CODEX_MODEL_*` / `HR_CODEX_REASON_*` / `HR_CODEX_FORCED_LOGIN` / `HR_CODEX_BIN`。

## 3. 技能与 SDD 执行层

- Codex 技能真源：`.agents/skills/<name>/SKILL.md`（9 个项目自建技能已从 `.claude/skills/` 迁移适配）。
- `.claude/skills/` **保留**作为 claude 回退引擎与机器闸真源（`tests/` 逐字核它），两处措辞⛔ 互改。
- `superpowers@claude-plugins-official` 插件在 Codex 不可用：
  `spec-to-plan` 直接按内置三段式规范产出 `### Task N:` 计划；`run-build` 用
  `scripts/codex_sdd_runner.py`（按 Task 独立上下文 + 两阶段 review）。

## 4. 环境差异（已验证的结论，⛔ 不要当未知问题重查）

- macOS Seatbelt 沙箱禁止跨进程读命令行参数 ⇒ `pgrep -f` 恒空。并发判据用
  `.claude/handoff/launch/run-lanes.pid` 兜底：`lane-launcher.sh` 发车时写、`run-lanes.sh`
  退出时自删、`scripts/action_request.py` 读它判活。
  ⚠️ 沙箱会话内 `kill -0 <pid>` 对 launchd 起的进程**也恒失败**（2026-09-30 控制组实证：
  对活着的值守 pid 与运行中的 run-lanes pid 均 rc=1）——`kill -0` 失败后必须用
  `lsof -p <pid>` 复核（有输出＝在跑）。⛔ 不得只凭 `pgrep` 空或 `kill -0` 失败断言「无泳道在跑」。
- Codex 的 `workspace-write` 沙箱把写权限锁在会话 cwd（泳道 worktree 条目＝泳道目录），
  天然禁止改主工作区——worktree 隔离不再依赖 hook。
- **泳道也因此不能自行 git 提交**（2026-09-30 `0930D` 修复）：worktree 的 git 元数据在主工作区
  `.git/worktrees/<名>` 下，泳道内 `git add/commit` 报 `Operation not permitted`（0930A/B/C 实证）。
  收口改由执行器代做——`run-lanes.sh` 退出每条泳道时调 `scripts/lane_collect.py stage1` 代提交到
  泳道分支，一轮收敛后 `stage2` 只把「OK＋机器判据 PASS」的泳道合回 main 并推送；PARTIAL/GATE-*
  只留分支待人工。⇒ Codex 泳道的 opener ⛔ 不再写 git commit/merge/push 步骤。
- 无头执行需要联网（模型推理）。Codex 交互沙箱禁网，真实泳道由 launchd（用户态）发起，不受此限。
- **拆件受限会话的 Edit 路径级 deny（0930A 已实现）**：Codex PreToolUse hook
  （`scripts/hooks/codex_unpack_guard.py` + 项目层 `.codex/hooks.json`），
  `HR_LIAISON_UNPACK=1` 时按 `dispatch.CHARTER_WRITABLE_PATHS`（唯一真源）拦截
  编辑类工具与整体性 git 动作（`git add -A`/`git add .`/`git commit -a`/`git stash`/
  `git push`）；测试 `tests/test_unpack_guard_hook.py`；无头会话经
  `--dangerously-bypass-hook-trust` 跳过 hook 信任流程（已自审 hook 源）。

## 5. 目录与提交

- `.agents/skills/<name>/SKILL.md` 已在提交通道白名单（与 `.claude/skills/<name>/SKILL.md` 同待遇）；
  其余 `.agents/**` 不白名单。
- 本文件与 CLAUDE.md 同受「收录规则」三闸约束（见 `docs/CLAUDE-md-收录规则.md`）。
