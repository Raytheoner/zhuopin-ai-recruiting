---
name: spec-to-plan
description: 把 OpenSpec 的 spec 转成 Superpowers 实现计划，是本项目 OpenSpec→Superpowers 的唯一接缝。当用户说"出实现计划""spec-to-plan""把 M1 的 spec 变成计划""开始做某个交付单元""writing-plans"时使用。强制以 spec.md 为输入（绝不用 tasks.md），强制注入 Global Constraints。
---

把 OpenSpec 的行为契约转成可执行的实现计划。这是本项目 **OpenSpec → Superpowers 的唯一接缝**，完整规则见 `03-工具链协作规则.md`。

**输入**：变更名（如 `m1-job-profile-intake`）。若未给出，列出 `openspec/changes/` 下的活跃变更让用户选。

## 前置检查

确认当前会话能调用 `superpowers:writing-plans`。**不能调用就停下来**，告诉用户换到能调用它的界面（通常是 Desktop 的 Code tab 或 Claude Code 终端），不要自己手写一份计划冒充——手写的产物不带 `### Task N:` 结构，后面 `scripts/task-brief` 会解析失败。

## ⛔ 不需要 git worktree

本技能只产出一份 markdown 计划文件（`docs/superpowers/plans/`），**不写任何代码、不建分支**。直接在主检出里跑。

隔离工作区是 `run-build` 的事——`writing-plans` 技能自己也写明了「worktree should have been created at **execution** time」。在这一步建 worktree 只会多一层目录切换的混乱。

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

- [ ] **任务标题必须是三级 `### Task N: ` —— 不是二级 `## Task N:`**
      `scripts/task-brief PLAN_FILE N` 按三级标题抽取任务全文。用二级会**静默失败**：
      脚本返回空，controller 拿着空 brief 去派发 implementer，看不出哪里错了。
      自查命令：`grep -c '^### Task ' <计划文件>` 应等于实际任务数，不能是 0。
      *实证*：2026-08-19 `server-runtime-logging` 那份计划 1900 行、7 个任务，
      全用了 `## Task N:`，三级标题数为 0。
- [ ] 有 **Global Constraints** 段，内容与 CLAUDE.md 一致
- [ ] spec 里每条 `### Requirement:` 都能指到至少一个 Task
- [ ] 每个 Task 有确切文件路径、完整代码、确切命令与预期输出
- [ ] 无 TBD / TODO / "适当处理错误" 类占位符
- [ ] 前后 Task 的类型名、函数签名、字段名一致
- [ ] **每个有副作用的动作独占一个 Task 步骤**且带幂等键（第一铁律）
- [ ] 涉及 AI 评分的部分，测试断言覆盖 `evidence_ref` 非空

### 6. 端到端提取验证（强烈建议，2026-08-09 起纳入标准动作）

自查清单只能查"看起来对不对"，查不出"跑不跑得起来"。写完计划后再做一步：

1. 把计划里全部代码块**原样提取**到一个临时目录
2. 按计划里的 `requirements.txt` **精确锁定版本**装进独立 venv（含 `langgraph==1.0.10`）
3. 跑完整测试套件
4. 有失败就**先在临时副本里定位修复，确认后再同步回正式计划文件**
5. 修完重新提取一遍跑全量，确认 Edit 操作没引入转录误差
6. 清理临时目录

**为什么值得**：2026-08-09 这一步在 M1 第 0 章计划里揪出 3 个真实 bug（JD 生成重试次数差一、SQLite 跨线程连接、`SqliteSaver` 用法错误），全都藏在从旧版计划原样搬运、从未被执行过的部分。不做这一步，它们要到 run-build 中途才爆。

**边界**：测试与被测代码出自同一份文档、同一个作者，全通只证明**代码可执行且内部自洽**，不证明**符合 spec**。spec 合规由 `run-build` 的两阶段 review 负责，这一步不是它的替代品。

### 7. 输出

- 计划路径
- 覆盖了 spec 的哪些 Requirement、对应 tasks.md 哪些章节
- Task 数量
- 提取验证结果（测试数、修复的 bug）
- 下一步：用 `run-build` 执行

**不要在本次响应里开始实现。** 计划出完就停。
