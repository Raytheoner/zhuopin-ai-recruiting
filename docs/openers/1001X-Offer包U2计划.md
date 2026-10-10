[Mac]1001X-Offer包U2计划
【设置】执行环境: Codex ｜ Session: 新开（run-lanes 无头起）｜ 分支: lane-1001x-offer-unit2-plan ｜ worktree: ✅ 勾（产出一份 plan 文档）｜ 工作区: .claude/worktrees/lane-1001x-offer-unit2-plan ｜ 派发: Codex·`[Mac]1001G`

## 零、任务

按 `.agents/skills/spec-to-plan/SKILL.md`（Codex 版；正本＝`.claude/skills/spec-to-plan/SKILL.md`，**逐节照做**）
为 `offer-generation` 的 **U2 交付单元**（`tasks.md` 第 2 章「U2 文书引擎（Offer／拒信共用）」）产出实现计划：

- 输入：`openspec/changes/offer-generation/specs/` 下与本单元相关的能力文件（自行列出来由。
  建议＝`candidate-letter-engine`（主）＋`candidate-letter-outbound`（导出/查看留痕/外发准备相关））
  ＋ `design.md` 相关 Decisions ＋ **上游 U1 已合 main 的真实实现**（`letter_template`／`candidate_letter`
  两表、`app/storage/offer_approval_chain.py`、`offer` 表——接口核对参照，⚠️ 与磁盘真身一致）。
  ⛔ `tasks.md` **只用于确认 U2 章节边界（2.1–2.8）**，不作为计划输入。
- 输出：`docs/superpowers/plans/2026-10-10-offer-generation-unit2-letter-engine.md`（**单文件**）。
- 硬格式：三级 `### Task N:`；**Global Constraints 段**（从 `CLAUDE.md`「工程铁律」与「合规红线」逐字抄
  与本单元相关的条目）；每个 Task 给确切文件路径／完整代码／确切命令与预期输出，⛔ 无 TBD/TODO 占位；
  spec 里每条 `### Requirement:` 至少指到一个 Task。**格式样板**＝同包 U1 计划
  `docs/superpowers/plans/2026-10-08-offer-generation-unit1-domain-model.md`（已全部落地）。
- 本单元要点（逐条覆盖）：模板维护接口（版本递增不覆盖；⛔ 评分/排名/硬门槛占位符、Offer ⛔ 薪资类占位符，
  命中即拒）、`compute_letter_draft` 纯函数（`temperature=0`、`prompt_version`、模型标识取 API 响应、
  AI 生成标识与 M1 JD 同串、`facts` schema 层无薪资键、⛔ 无 storage 写入）、`effect_persist_letter`
  幂等节点（Offer 前置 `approved`；拒信前置 `rejection_record`）、编辑与「标记为人工撰写」、
  docx 导出（未标记人工 ⇒ 页眉含 AI 标识；Offer 薪资处留空）与查看留痕（先留痕再返回）、
  文书页、`.51` 同款 Windows 环境导出冒烟。

## 一、边界与红线

- 只写上述这一个 plan 文件；⛔ 不改 `openspec/**`、`app/**`、`tests/**`、`scripts/**`、⛔ 不碰 `data/`。
- 同批另有 3 条泳道（1001V/W/Y）各写自己包的 U2 计划——**你只写你这一份**，不要碰任何其它文件。
- ⛔ 不建分支、不自行 git 提交（执行器代做）；⛔ 不对外发送。

## 二、机器判据

```bash
test -n "$(ls docs/superpowers/plans/2026-10-10-offer-generation-unit2*.md 2>/dev/null)"
grep -q '^### Task ' docs/superpowers/plans/2026-10-10-offer-generation-unit2*.md
grep -q 'Global Constraints' docs/superpowers/plans/2026-10-10-offer-generation-unit2*.md
grep -q 'compute_letter_draft' docs/superpowers/plans/2026-10-10-offer-generation-unit2*.md
```

## 三、收口

1. 自检 `## 机器判据` 全绿，把实测尾行写进最终答复；
2. 只留改动在 worktree，顶格输出 `OPENER_DONE`；任一判据不过输出 `OPENER_PARTIAL` 并写清哪条。
3. 「`codex` CLI 不可达、或 worktree 里缺 `AGENTS.md`」⇒ 登记『⏸ 留步：执行环境不可达』并停。
