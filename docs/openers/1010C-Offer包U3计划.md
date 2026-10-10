[Mac]1010C-Offer包U3计划
【设置】执行环境: Codex ｜ Session: 新开（run-lanes 无头起）｜ 分支: lane-1010c-offer-unit3-plan ｜ worktree: ✅ 勾（产出一份 plan 文档）｜ 工作区: .claude/worktrees/lane-1010c-offer-unit3-plan ｜ 派发: Codex·`[Mac]1001G`

## 零、任务

按 `.agents/skills/spec-to-plan/SKILL.md`（Codex 版；正本＝`.claude/skills/spec-to-plan/SKILL.md`，**逐节照做**）
为 `offer-generation` 的 **U3 交付单元**（`tasks.md` 第 3 章「U3 内部审批流」）产出实现计划：

- 输入：`openspec/changes/offer-generation/specs/outbound-approval-gate/`（内审链相关，自行核对能力文件；
  如涉及 `offer-record-and-approval` 一并列出理由）＋ `design.md` 相关 Decisions ＋ **上游参照**：U1 实现真身
  （`app/storage/offer_approval_chain.py`、`offer`／`offer_approval*` 表）＋ U2 计划
  `docs/superpowers/plans/2026-10-10-offer-generation-unit2-letter-engine.md`（已落 main；U2 代码分段落地中，
  ⚠️ 引用以磁盘真身为准，拿不准的写清假设）。
  ⛔ `tasks.md` **只用于确认 U3 章节边界（3.1–3.7）**，不作为计划输入。
- 输出：`docs/superpowers/plans/2026-10-10-offer-generation-unit3-approval-flow.md`（**单文件**）。
- 硬格式：三级 `### Task N:`；**Global Constraints 段**（逐字抄相关铁律／红线）；每 Task 给确切文件路径／
  完整代码／命令与预期输出，⛔ 无 TBD；spec 每条 Requirement 至少指到一个 Task。
  **格式样板**＝同包 U1 计划（`docs/superpowers/plans/2026-10-08-offer-generation-unit1-domain-model.md`）。
- 本单元要点（逐条覆盖）：发起 Offer（前置校验＋`pending_approval`＋流转 `offer` 阶段＋history 同事务、
  幂等键）；`effect_record_approval`（审批人列表校验、最后一级 approved、任一级 returned ⇒ needs_revision
  且后续级不触发、幂等键含 round/level/approver/decision）；退回后修改（`round+1`、链从第一级重走）；
  审批页（⛔ 不显示 AI 评分／排名／建议录用类文本，测试反证）；内部通知（幂等键含 offer_id/round/level、
  无通道不阻塞）；导出对未审批状态的拒绝；U3 e2e。

## 一、边界与红线

- 只写上述这一个 plan 文件；⛔ 不改 `openspec/**`、`app/**`、`tests/**`、`scripts/**`、⛔ 不碰 `data/`。
- 同批另有 3 条泳道（1010A/B/D）各写自己包的 U3 计划——**你只写你这一份**，不要碰任何其它文件。
- ⛔ 不建分支、不自行 git 提交（执行器代做）；⛔ 不对外发送。

## 二、机器判据

```bash
test -n "$(ls docs/superpowers/plans/2026-10-10-offer-generation-unit3*.md 2>/dev/null)"
grep -q '^### Task ' docs/superpowers/plans/2026-10-10-offer-generation-unit3*.md
grep -q 'Global Constraints' docs/superpowers/plans/2026-10-10-offer-generation-unit3*.md
grep -q 'effect_record_approval' docs/superpowers/plans/2026-10-10-offer-generation-unit3*.md
```

## 三、收口

1. 自检 `## 机器判据` 全绿，把实测尾行写进最终答复；
2. 只留改动在 worktree，顶格输出 `OPENER_DONE`；任一判据不过输出 `OPENER_PARTIAL` 并写清哪条。
3. 「`codex` CLI 不可达、或 worktree 里缺 `AGENTS.md`」⇒ 登记『⏸ 留步：执行环境不可达』并停。
