[Mac]1001O-Offer包U1计划
【设置】执行环境: Codex ｜ Session: 新开（run-lanes 无头起）｜ 分支: lane-1001o-offer-unit1-plan ｜ worktree: ✅ 勾（产出一份 plan 文档）｜ 工作区: .claude/worktrees/lane-1001o-offer-unit1-plan ｜ 派发: Codex·`[Mac]1001G`

## 零、任务

按 `.agents/skills/spec-to-plan/SKILL.md`（Codex 版；正本＝`.claude/skills/spec-to-plan/SKILL.md`，**逐节照做**）
为 `offer-generation` 的 **U1 交付单元**（`tasks.md` 第 1 章「Offer 域模型」）产出实现计划：

- 输入：`openspec/changes/offer-generation/specs/` 下与本单元相关的能力文件（自行列出来由。
  建议＝`offer-record-and-approval`）＋ `design.md` 相关 Decisions。
  ⛔ `tasks.md` **只用于确认 U1 章节边界**，不作为计划输入。
- 输出：`docs/superpowers/plans/<今日>-offer-generation-unit1-domain-model.md`（**单文件**）。
- 硬格式：三级 `### Task N:`；**Global Constraints 段**（从 `CLAUDE.md` 逐字抄相关条目）；每个 Task 给
  确切文件路径／完整代码／命令与预期输出，⛔ 无 TBD/TODO；spec 每条 Requirement 至少指到一个 Task。
- 🔴 本包合规红线：**薪资等敏感字段不入库**（只存岗位/部门/入职日/汇报对象/模板版本/审批状态/答复）——
  plan 里必须把这个约束写进 Global Constraints 并给出断言。

## 一、边界与红线

- 只写这一个 plan 文件；⛔ 不改 `openspec/**`、`app/**`、`tests/**`、`scripts/**`、⛔ 不碰 `data/`。
- ⛔ 不建分支、不自行 git 提交（执行器代做）；⛔ 不对外发送。

## 二、机器判据

```bash
test -n "$(ls docs/superpowers/plans/*offer-generation*unit1*.md 2>/dev/null)"
grep -q '^### Task ' docs/superpowers/plans/*offer-generation*unit1*.md
grep -q 'Global Constraints' docs/superpowers/plans/*offer-generation*unit1*.md
grep -q 'offer_approval' docs/superpowers/plans/*offer-generation*unit1*.md
```

## 三、收口

1. 自检 `## 机器判据` 全绿，把实测尾行写进最终答复；
2. 只留改动在 worktree，顶格输出 `OPENER_DONE`；任一判据不过输出 `OPENER_PARTIAL` 并写清哪条。
3. 「`codex` CLI 不可达、或 worktree 里缺 `AGENTS.md`」⇒ 登记『⏸ 留步：执行环境不可达』并停。
