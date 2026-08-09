---
name: spec-to-plan
description: 把 OpenSpec 的 spec 转成 Superpowers 实现计划，是本项目 OpenSpec→Superpowers 的唯一接缝。当用户说"出实现计划""spec-to-plan""把 M1 的 spec 变成计划""开始做某个交付单元""writing-plans"时使用。强制以 spec.md 为输入（绝不用 tasks.md），强制注入 Global Constraints。
---

把 OpenSpec 的行为契约转成可执行的实现计划。这是本项目 **OpenSpec → Superpowers 的唯一接缝**，完整规则见 `03-工具链协作规则.md`。

**输入**：变更名（如 `m1-job-profile-intake`）。若未给出，列出 `openspec/changes/` 下的活跃变更让用户选。

## 前置检查

确认当前会话能调用 `superpowers:writing-plans`。**不能调用就停下来**，告诉用户换到能调用它的界面（通常是 Desktop 的 Code tab 或 Claude Code 终端），不要自己手写一份计划冒充——手写的产物不带 `### Task N:` 结构，后面 `scripts/task-brief` 会解析失败。

## 步骤

### 1. 定位输入

- 读 `openspec/changes/<change>/tasks.md` —— 这是 **WBS**，用来划分交付单元
- 读 `openspec/changes/<change>/specs/**/spec.md` —— 这些才是 **真正的输入**
- 读 `openspec/changes/<change>/design.md` 取技术决策

与用户确认这次为哪个交付单元出计划。一个交付单元 = tasks.md 的一个或几个相邻章节 = 一条可独立测试并合并的分支。

### 2. 硬性禁止

- ❌ **绝不把 `tasks.md` 当作 writing-plans 的输入。** 它是 WBS，粒度差一个数量级
- ❌ 绝不调用 `/opsx:apply` 或 `openspec-apply-change`
- ❌ 绝不跳过 `writing-plans` 直接进 `subagent-driven-development`

### 3. 准备 Global Constraints

从 `CLAUDE.md` 的「工程铁律」与「合规红线」两节**逐字复制**出与本交付单元相关的条目。

这不是形式主义——`subagent-driven-development` 会把这段原样交给 reviewer 当注意力透镜。铁律不进计划，reviewer 就查不出「这个 `effect_*` 节点没加幂等键」，而这个失败是**静默的**。

### 4. 调用 writing-plans

用 Skill 工具调用 `superpowers:writing-plans`，明确告诉它：

- 输入的 spec 文件路径（可能多份）
- 本次交付单元范围（对应 tasks.md 哪些章节）
- Global Constraints 全文，要求原样写进计划的 Global Constraints 段
- 技术栈：Python、LangGraph ≥1.0.10、Postgres、pytest
- 计划存到 `docs/superpowers/plans/YYYY-MM-DD-<unit-name>.md`

### 5. 交付前自查

- [ ] 有 **Global Constraints** 段，内容与 CLAUDE.md 一致
- [ ] spec 里每条 `### Requirement:` 都能指到至少一个 Task
- [ ] 每个 Task 有确切文件路径、完整代码、确切命令与预期输出
- [ ] 无 TBD / TODO / "适当处理错误" 类占位符
- [ ] 前后 Task 的类型名、函数签名、字段名一致
- [ ] **每个有副作用的动作独占一个 Task 步骤**且带幂等键（第一铁律）
- [ ] 涉及 AI 评分的部分，测试断言覆盖 `evidence_ref` 非空

### 6. 输出

- 计划路径
- 覆盖了 spec 的哪些 Requirement、对应 tasks.md 哪些章节
- Task 数量与预估
- 下一步：用 `run-build` 执行

**不要在本次响应里开始实现。** 计划出完就停。
