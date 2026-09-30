---
name: lane-watch-relay
description: 续棒泳道看护（Codex 引擎）——新会话免粘贴 Opener 直接接上泳道看护。当 Shao Peishen 说"续棒泳道看护""看泳道""接棒看护"、或定时看护自动唤醒时使用。按固定只读清单接手，逐条报状态并出定夺项。⛔ 本技能不亲自执行任何 opener、不发车。
---

接手泳道看护（Codex 版）。**正本只读清单、判据可用性表、两栏汇报与「接着做什么」表在
`.claude/skills/lane-watch-relay/SKILL.md`**，未写在这里的照正本执行。

## Codex 差异

1. **判「在跑」用 PID 文件兜底，且必须二次核**：Codex 的 macOS Seatbelt 沙箱下 `pgrep -f` 恒空
   （`AGENTS.md` §4）。判活：读 `.claude/handoff/launch/run-lanes.pid` → `kill -0 <pid>`；
   ⚠️ **`kill -0` 在沙箱会话里对 launchd 起的进程也会恒返回失败**（2026-09-30 实证：对活着的
   值守 pid 与运行中的 run-lanes pid 均 rc=1）⇒ `kill -0` 失败时**必须**用 `lsof -p <pid>`
   复核：有输出＝在跑，无输出＝已死。配合最新 `lanes-*` 目录 `results.tsv` 行数 vs 编排条数
   （齐＝已收工，缺行且目录 mtime 在数分钟内＝在跑）。⛔ 不得把「pgrep 空」或「kill -0 失败」
   当成「无泳道在跑」——误判会导致重复发车。
2. **只读清单里加一行引擎自检**：`codex --version`（真实泳道靠它执行；不可用 ⇒ 报出来，
   按 `AGENTS.md` §1 修，⛔ 不因此发车）。
3. **定时/无人值守唤醒时**（heartbeat 自动化）：⛔ 不输出问句、不等回复；无变化／无可行动项 ⇒
   本轮静默收工（不产出一条面向人的汇报）。只在出现「有需要 Shao Peishen 定夺的项、
   泳道 FAIL/NO-SENTINEL/RELAY-EXHAUSTED、闸门待答、发车请求被拒」这类**需要人看**的变化时才汇报，
   并照正本第三、四步两栏写。
4. **看护 opener 的【设置】行**：`执行环境: Codex`，⛔ 不写 set_session_title。
