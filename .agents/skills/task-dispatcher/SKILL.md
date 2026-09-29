---
name: task-dispatcher
description: 任务驱动 workflow 的调度器（R2，Codex 引擎）。由事件（泳道批次收敛、定夺答复提交、每日 09:00 兜底）唤醒的无头会话执行：刷新任务台账 → 按 rules.md 算 ready 集 → 按 lane-dispatch 规则写无头块并经动作通道发车 → 需人项追加定夺队列 → 文档改动经提交请求通道提交 → 事件文件归档。⛔ 本技能不亲自执行任何 opener、不替 Shao Peishen 拍任何不可代项。
---

# task-dispatcher · 调度器（R2，Codex 版）

**正本九步执行（①→⑨）、红线七条、收工六行报告在 `.claude/skills/task-dispatcher/SKILL.md`**；
阶段推进规则的真源仍是 **`.claude/skills/task-dispatcher/rules.md`**（`scripts/dispatcher_backlog.py`
读的是这份路径，⛔ 不要在本目录复制一份）。未写在这里的照正本执行。

## Codex 差异

1. **唤醒壳**：`scripts/dispatcher_event.sh` 默认以 codex 引擎无头执行本技能
   （`codex exec --json --sandbox workspace-write -c approval_policy=never …`）；shell 起的会话在
   `DISPATCHER_LOCK_HELD=1` 下核锁即可，与正本①同。
2. **红线 ③ 措辞扩展**：⛔ 改 `.claude/skills/` **与 `.agents/skills/`** 下其它 skill；本技能自己的
   两份 SKILL.md 与 rules.md 也⛔ 不在无头会话里改。
3. **红线 ④ 扩展**：⛔ 改 `CLAUDE.md` **与 `AGENTS.md`**。
4. **发车与提交通道不变**：动作请求 `launch-lanes`、提交请求 `.request`、`emit_event:false` 回环规避
   照正本 ⑤⑦；事件归档照正本 ⑨。run-lanes.sh 默认 codex 引擎，⛔ 不需要在 args 里传引擎。
