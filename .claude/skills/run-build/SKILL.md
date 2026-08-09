---
name: run-build
description: 用 Superpowers 子代理驱动开发执行实现计划，完成后回勾 OpenSpec 的 WBS。当用户说"执行计划""run-build""开始写代码""跑这份 plan""subagent-driven-development""继续实现"时使用。开工前强制检查 Global Constraints 段，缺失则拒绝执行。
---

执行 `spec-to-plan` 产出的实现计划。完整规则见 `03-工具链协作规则.md`。

**输入**：计划文件路径。若未给出，列出 `docs/superpowers/plans/` 下的计划让用户选，并标出哪些已完成。

## 前置检查（任何一条不过就停）

1. **能否调用 `superpowers:subagent-driven-development`？**
   不能就停。告诉用户换到能调用它的界面（Desktop 的 Code tab 或 Claude Code 终端）。
   **不要退化成自己逐个任务写代码**——那就丢掉了上下文隔离、两阶段 review、进度台账，也就丢掉了用这套工具的全部理由。

2. **bash 是否跑在用户本机？**
   Cowork 的 bash 在隔离 VM 里，git worktree 与提交行为不可靠。实现阶段应在 Claude Code 里做。

3. **计划里有没有 Global Constraints 段？**
   没有就停，回去跑 `spec-to-plan` 补。缺了这段，后面每一轮 review 都是瞎的——而且这个失败**不会报错**，只会安静地漏掉所有铁律检查。

4. **是否在 main/master 上直接干活？**
   调用 `superpowers:using-git-worktrees` 建隔离工作区。

5. **`.superpowers/sdd/progress.md` 是否已存在？**
   存在说明这份计划跑过一部分。台账里标记完成的 Task **不要重跑**——重跑已完成任务是这套流程里最贵的失败模式。

## 执行

用 Skill 工具调用 `superpowers:subagent-driven-development`，把计划路径交给它。

它自己处理：每 Task 派发全新子代理、两阶段 review（spec 合规 + 代码质量）、修复循环、进度台账、最终全分支 review。

**不要中途停下来问「要继续吗」**——该技能明确要求连续执行到完成或阻塞。

## 本项目额外的 review 关注点

给 reviewer 的 Global Constraints 里，这几条要盯死：

- 每个 `effect_*` 节点是否独占、是否带幂等键 `{thread_id}:{node_name}:{business_key}`
- `compute_*` 节点是否真的无副作用
- LLM 调用是否 `temperature=0` 且模型版本显式锁定（无 `latest`）
- 写 `criterion_score` 处，`evidence_ref` 为空是否被拒绝
- 有没有任何路径能产生 `reason_type='ai_score'` 的淘汰记录（必须没有）

## 收口

最终全分支 review 通过后：

1. 回到 `openspec/changes/<change>/tasks.md`，把本交付单元覆盖的章节条目 `- [ ]` 改成 `- [x]`
   **只有 final review 通过才勾**，写完代码不算完
2. 调用 `superpowers:finishing-a-development-branch` 处理合并
3. 若该变更 tasks.md 已全部勾完，提示用户用 `openspec-archive-change` 收口，把 spec 折进 `openspec/specs/`

## 输出

- 完成的 Task 数与提交范围
- 最终 review 结论，以及遗留的 Minor findings
- tasks.md 整体进度（N/M 章节）
- 下一步建议
