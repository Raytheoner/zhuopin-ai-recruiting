# conflicts 逐条核真（2026-09-30）

> 本文件由 [Mac]0930C 泳道无头核真生成。逐条对照「台账态 vs 生成器真身态」，
> 判定台账 stale（真身对）的条目只改状态轴对齐真身，⛔ 不手改 id／标题／判据，
> 不改 `scripts/dispatcher_backlog.py` 判据语义、不改 `.claude/skills/**`。

## 结论

- 落档基线 conflicts=35；当次全量重算（含 session接力 滚动覆盖后的新消失条目）为 **37** 条。
- 判定：37 条全部「真身对（台账 stale）」——台账状态较真身滞后，无「台账对」、无「两方都需修」。
- 处置：17 条生成类条目状态对齐真身（待开／阻塞）；20 条「来源已消失或已勾选」条目归「完成」。
- 重跑 `scripts/dispatcher_backlog.py`（默认 ledger）后 conflicts=**0**，幂等（连跑两次 diff 为空）。
- 无保留例外（37 条全部定论）。

## 判定汇总

| 分类 | 条数 | 处置 |
|---|---|---|
| 台账 stale · 真身=待开/阻塞（生成类） | 17 | 状态对齐真身 |
| 台账 stale · 真身=来源已消失或已勾选 | 20 | 归「完成」 |
| 台账对 / 两方都需修 | 0 | — |
| 保留例外（穷尽后仍无法定论） | 0 | — |

---

### channel-resume-intake/0.1

- 场景／阶段：channel-resume-intake ／ gate
- 台账态：完成（无） → 真身态：待开／无
- 核验命令：`grep -n '^- \[ \] 0.1' openspec/changes/channel-resume-intake/tasks.md`
- 原始结果：- [ ] 0.1（gate checkbox 未勾；其判据 M2 tasks 2.1/3.3/3.4 已 [x]，但 checkbox 本身未勾，生成器只看 checkbox）
- 判定：真身对（台账 stale）
- 处置：状态 完成→待开（gate 未过，U1 门槛重新生效；判据实质成立一事已在备注留存）

---

### m1-intake-quality-fixes/8.9

- 场景／阶段：M1 ／ build
- 台账态：阻塞（无） → 真身态：待开／无
- 核验命令：`grep -n '^- \[ \] 8.9' openspec/changes/m1-intake-quality-fixes/tasks.md`
- 原始结果：- [ ] 8.9（归档顺序提醒，未勾；正文无 🔴/外部 关键词，生成器判待开）
- 判定：真身对（台账 stale）
- 处置：状态 阻塞→待开

---

### m1-intake-quality-fixes/U8/plan

- 场景／阶段：M1 ／ plan
- 台账态：完成（无） → 真身态：待开／无
- 核验命令：`ls docs/superpowers/plans/ | grep 'm1-intake-quality-fixes-unit8'`
- 原始结果：无 unit8 plan 文件（delivery-units.md 说第 8 章不建议当 TDD plan 跑）
- 判定：真身对（台账 stale）
- 处置：状态 完成→待开

---

### m1-job-profile-intake/1.5b

- 场景／阶段：M1 ／ build
- 台账态：完成（无） → 真身态：待开／无
- 核验命令：`grep -n '1.5b' openspec/changes/m1-job-profile-intake/tasks.md`
- 原始结果：- [ ] 1.5b，续行 '⤷ 已移出到阶段二·企微通道'（生成器只识别同行「已移出」，续行不识别）
- 判定：真身对（台账 stale）
- 处置：状态 完成→待开

---

### m1-job-profile-intake/1.7

- 场景／阶段：M1 ／ build
- 台账态：完成（无） → 真身态：待开／无
- 核验命令：`grep -n '1.7' openspec/changes/m1-job-profile-intake/tasks.md`
- 原始结果：- [ ] 1.7，续行 '⤷ 已移出→调度基础设施（待立项）'
- 判定：真身对（台账 stale）
- 处置：状态 完成→待开

---

### m1-job-profile-intake/5.6

- 场景／阶段：M1 ／ build
- 台账态：完成（外部） → 真身态：阻塞／外部
- 核验命令：`grep -n '5.6' openspec/changes/m1-job-profile-intake/tasks.md`
- 原始结果：- [ ] 5.6，续行 '⤷ 已移出→调度基础设施'；正文含「业务经理」命中外部关键词，生成器判阻塞/外部
- 判定：真身对（台账 stale）
- 处置：状态 完成→阻塞（外部）

---

### m1-job-profile-intake/6.8

- 场景／阶段：M1 ／ build
- 台账态：完成（无） → 真身态：待开／无
- 核验命令：`grep -n '6.8' openspec/changes/m1-job-profile-intake/tasks.md`
- 原始结果：- [ ] 6.8，续行 '⤷ 已移出→调度基础设施'
- 判定：真身对（台账 stale）
- 处置：状态 完成→待开

---

### m1-job-profile-intake/9.2

- 场景／阶段：M1 ／ build
- 台账态：完成（无） → 真身态：待开／无
- 核验命令：`grep -n '9.2' openspec/changes/m1-job-profile-intake/tasks.md`
- 原始结果：- [ ] 9.2，续行 '⤷ 已移出到阶段二·企微通道'
- 判定：真身对（台账 stale）
- 处置：状态 完成→待开

---

### m1-job-profile-intake/U1

- 场景／阶段：M1 ／ build
- 台账态：完成（无） → 真身态：待开／无
- 核验命令：`grep -nE '^- \[ \]' openspec/changes/m1-job-profile-intake/tasks.md | sed -n '1,20p'`
- 原始结果：第 1 章未勾条目 1.5b/1.7（均续行已移出）⇒ 单元未全勾，生成器判待开
- 判定：真身对（台账 stale）
- 处置：状态 完成→待开

---

### m1-job-profile-intake/U1/plan

- 场景／阶段：M1 ／ plan
- 台账态：完成（无） → 真身态：待开／无
- 核验命令：`ls docs/superpowers/plans/ | grep -E 'm1-job-profile-intake-unit1'`
- 原始结果：无 unit1 plan 文件
- 判定：真身对（台账 stale）
- 处置：状态 完成→待开

---

### m1-job-profile-intake/U5

- 场景／阶段：M1 ／ build
- 台账态：完成（无） → 真身态：待开／无
- 核验命令：`grep -n '5.6' openspec/changes/m1-job-profile-intake/tasks.md`
- 原始结果：第 5 章未勾条目 5.6（续行已移出）⇒ 单元未全勾
- 判定：真身对（台账 stale）
- 处置：状态 完成→待开

---

### m1-job-profile-intake/U6

- 场景／阶段：M1 ／ build
- 台账态：完成（无） → 真身态：待开／无
- 核验命令：`grep -n '6.8' openspec/changes/m1-job-profile-intake/tasks.md`
- 原始结果：第 6 章未勾条目 6.8（续行已移出）⇒ 单元未全勾
- 判定：真身对（台账 stale）
- 处置：状态 完成→待开

---

### propose:S-M3

- 场景／阶段：S-M3 ／ propose
- 台账态：完成（无） → 真身态：待开／无
- 核验命令：`ls openspec/changes/ | grep -i 's-m3\|M3'`
- 原始结果：无 S-M3 变更包（intent 无未答题 ⇒ 待开）
- 判定：真身对（台账 stale）
- 处置：状态 完成→待开

---

### propose:S-Offer

- 场景／阶段：S-Offer ／ propose
- 台账态：完成（无） → 真身态：待开／无
- 核验命令：`ls openspec/changes/`
- 原始结果：无 offer-generation 之外与 S-Offer 对应的 propose 包
- 判定：真身对（台账 stale）
- 处置：状态 完成→待开

---

### propose:S-入职

- 场景／阶段：S-入职 ／ propose
- 台账态：完成（无） → 真身态：待开／无
- 核验命令：`ls openspec/changes/`
- 原始结果：无 S-入职 变更包
- 判定：真身对（台账 stale）
- 处置：状态 完成→待开

---

### propose:S-排期

- 场景／阶段：S-排期 ／ propose
- 台账态：完成（无） → 真身态：待开／无
- 核验命令：`ls openspec/changes/`
- 原始结果：无 S-排期 变更包
- 判定：真身对（台账 stale）
- 处置：状态 完成→待开

---

### propose:S-渠道

- 场景／阶段：S-渠道 ／ propose
- 台账态：完成（无） → 真身态：待开／无
- 核验命令：`ls openspec/changes/`
- 原始结果：无 S-渠道 变更包
- 判定：真身对（台账 stale）
- 处置：状态 完成→待开

---

### opener:[Mac]0903A

- 场景／阶段：构建自动化 ／ build
- 台账态：阻塞（决策） → 真身态：来源已消失或已勾选
- 核验命令：`grep -n '[Mac]0903A' docs/openers/号池台账.md`（或 `dispatcher_backlog --show conflicts` 当次重算）
- 原始结果：来源行不再生成（session接力／号池台账 滚动覆盖后已勾选或归档）
- 判定：真身对（台账 stale）——来源消失＝该 relay/opener 已闭环
- 处置：状态 阻塞 → 完成

---

### relay:AT-4

- 场景／阶段：构建自动化 ／ build
- 台账态：阻塞（外部） → 真身态：来源已消失或已勾选
- 核验命令：`grep -n 'AT-4' docs/session接力.md`（或 `dispatcher_backlog --show conflicts` 当次重算）
- 原始结果：来源行不再生成（session接力／号池台账 滚动覆盖后已勾选或归档）
- 判定：真身对（台账 stale）——来源消失＝该 relay/opener 已闭环
- 处置：状态 阻塞 → 完成

---

### relay:H-4

- 场景／阶段：构建自动化 ／ build
- 台账态：完成（无） → 真身态：来源已消失或已勾选
- 核验命令：`grep -n 'H-4' docs/session接力.md`（或 `dispatcher_backlog --show conflicts` 当次重算）
- 原始结果：来源行不再生成（session接力／号池台账 滚动覆盖后已勾选或归档）
- 判定：真身对（台账 stale）——来源消失＝该 relay/opener 已闭环
- 处置：状态 完成 → 完成

---

### relay:R-10

- 场景／阶段：构建自动化 ／ build
- 台账态：阻塞（外部） → 真身态：来源已消失或已勾选
- 核验命令：`grep -n 'R-10' docs/session接力.md`（或 `dispatcher_backlog --show conflicts` 当次重算）
- 原始结果：来源行不再生成（session接力／号池台账 滚动覆盖后已勾选或归档）
- 判定：真身对（台账 stale）——来源消失＝该 relay/opener 已闭环
- 处置：状态 阻塞 → 完成

---

### relay:R-6

- 场景／阶段：M0 ／ build
- 台账态：阻塞（外部） → 真身态：来源已消失或已勾选
- 核验命令：`grep -n 'R-6' docs/session接力.md`（或 `dispatcher_backlog --show conflicts` 当次重算）
- 原始结果：来源行不再生成（session接力／号池台账 滚动覆盖后已勾选或归档）
- 判定：真身对（台账 stale）——来源消失＝该 relay/opener 已闭环
- 处置：状态 阻塞 → 完成

---

### relay:tag#0f5416eb

- 场景／阶段：M0 ／ build
- 台账态：阻塞（外部） → 真身态：来源已消失或已勾选
- 核验命令：`grep -n 'tag#0f5416eb' docs/session接力.md`（或 `dispatcher_backlog --show conflicts` 当次重算）
- 原始结果：来源行不再生成（session接力／号池台账 滚动覆盖后已勾选或归档）
- 判定：真身对（台账 stale）——来源消失＝该 relay/opener 已闭环
- 处置：状态 阻塞 → 完成

---

### relay:tag#1370d6ad

- 场景／阶段：构建自动化 ／ build
- 台账态：阻塞（决策） → 真身态：来源已消失或已勾选
- 核验命令：`grep -n 'tag#1370d6ad' docs/session接力.md`（或 `dispatcher_backlog --show conflicts` 当次重算）
- 原始结果：来源行不再生成（session接力／号池台账 滚动覆盖后已勾选或归档）
- 判定：真身对（台账 stale）——来源消失＝该 relay/opener 已闭环
- 处置：状态 阻塞 → 完成

---

### relay:tag#198d8fba

- 场景／阶段：构建自动化 ／ build
- 台账态：完成（决策） → 真身态：来源已消失或已勾选
- 核验命令：`grep -n 'tag#198d8fba' docs/session接力.md`（或 `dispatcher_backlog --show conflicts` 当次重算）
- 原始结果：来源行不再生成（session接力／号池台账 滚动覆盖后已勾选或归档）
- 判定：真身对（台账 stale）——来源消失＝该 relay/opener 已闭环
- 处置：状态 完成 → 完成

---

### relay:tag#20c246cc

- 场景／阶段：构建自动化 ／ build
- 台账态：阻塞（决策） → 真身态：来源已消失或已勾选
- 核验命令：`grep -n 'tag#20c246cc' docs/session接力.md`（或 `dispatcher_backlog --show conflicts` 当次重算）
- 原始结果：来源行不再生成（session接力／号池台账 滚动覆盖后已勾选或归档）
- 判定：真身对（台账 stale）——来源消失＝该 relay/opener 已闭环
- 处置：状态 阻塞 → 完成

---

### relay:tag#314f3255

- 场景／阶段：M0 ／ build
- 台账态：阻塞（决策） → 真身态：来源已消失或已勾选
- 核验命令：`grep -n 'tag#314f3255' docs/session接力.md`（或 `dispatcher_backlog --show conflicts` 当次重算）
- 原始结果：来源行不再生成（session接力／号池台账 滚动覆盖后已勾选或归档）
- 判定：真身对（台账 stale）——来源消失＝该 relay/opener 已闭环
- 处置：状态 阻塞 → 完成

---

### relay:tag#33937877

- 场景／阶段：构建自动化 ／ build
- 台账态：完成（无） → 真身态：来源已消失或已勾选
- 核验命令：`grep -n 'tag#33937877' docs/session接力.md`（或 `dispatcher_backlog --show conflicts` 当次重算）
- 原始结果：来源行不再生成（session接力／号池台账 滚动覆盖后已勾选或归档）
- 判定：真身对（台账 stale）——来源消失＝该 relay/opener 已闭环
- 处置：状态 完成 → 完成

---

### relay:tag#346eaefa

- 场景／阶段：构建自动化 ／ build
- 台账态：完成（无） → 真身态：来源已消失或已勾选
- 核验命令：`grep -n 'tag#346eaefa' docs/session接力.md`（或 `dispatcher_backlog --show conflicts` 当次重算）
- 原始结果：来源行不再生成（session接力／号池台账 滚动覆盖后已勾选或归档）
- 判定：真身对（台账 stale）——来源消失＝该 relay/opener 已闭环
- 处置：状态 完成 → 完成

---

### relay:tag#431272f7

- 场景／阶段：构建自动化 ／ build
- 台账态：完成（无） → 真身态：来源已消失或已勾选
- 核验命令：`grep -n 'tag#431272f7' docs/session接力.md`（或 `dispatcher_backlog --show conflicts` 当次重算）
- 原始结果：来源行不再生成（session接力／号池台账 滚动覆盖后已勾选或归档）
- 判定：真身对（台账 stale）——来源消失＝该 relay/opener 已闭环
- 处置：状态 完成 → 完成

---

### relay:tag#4391ebf4

- 场景／阶段：M0 ／ build
- 台账态：阻塞（决策） → 真身态：来源已消失或已勾选
- 核验命令：`grep -n 'tag#4391ebf4' docs/session接力.md`（或 `dispatcher_backlog --show conflicts` 当次重算）
- 原始结果：来源行不再生成（session接力／号池台账 滚动覆盖后已勾选或归档）
- 判定：真身对（台账 stale）——来源消失＝该 relay/opener 已闭环
- 处置：状态 阻塞 → 完成

---

### relay:tag#55cb5cbe

- 场景／阶段：构建自动化 ／ build
- 台账态：阻塞（决策） → 真身态：来源已消失或已勾选
- 核验命令：`grep -n 'tag#55cb5cbe' docs/session接力.md`（或 `dispatcher_backlog --show conflicts` 当次重算）
- 原始结果：来源行不再生成（session接力／号池台账 滚动覆盖后已勾选或归档）
- 判定：真身对（台账 stale）——来源消失＝该 relay/opener 已闭环
- 处置：状态 阻塞 → 完成

---

### relay:tag#5c585390

- 场景／阶段：构建自动化 ／ build
- 台账态：阻塞（决策） → 真身态：来源已消失或已勾选
- 核验命令：`grep -n 'tag#5c585390' docs/session接力.md`（或 `dispatcher_backlog --show conflicts` 当次重算）
- 原始结果：来源行不再生成（session接力／号池台账 滚动覆盖后已勾选或归档）
- 判定：真身对（台账 stale）——来源消失＝该 relay/opener 已闭环
- 处置：状态 阻塞 → 完成

---

### relay:tag#8f75eddb

- 场景／阶段：M0 ／ build
- 台账态：阻塞（决策） → 真身态：来源已消失或已勾选
- 核验命令：`grep -n 'tag#8f75eddb' docs/session接力.md`（或 `dispatcher_backlog --show conflicts` 当次重算）
- 原始结果：来源行不再生成（session接力／号池台账 滚动覆盖后已勾选或归档）
- 判定：真身对（台账 stale）——来源消失＝该 relay/opener 已闭环
- 处置：状态 阻塞 → 完成

---

### relay:tag#91af1c79

- 场景／阶段：构建自动化 ／ build
- 台账态：完成（无） → 真身态：来源已消失或已勾选
- 核验命令：`grep -n 'tag#91af1c79' docs/session接力.md`（或 `dispatcher_backlog --show conflicts` 当次重算）
- 原始结果：来源行不再生成（session接力／号池台账 滚动覆盖后已勾选或归档）
- 判定：真身对（台账 stale）——来源消失＝该 relay/opener 已闭环
- 处置：状态 完成 → 完成

---

### relay:tag#a7e6e7bf

- 场景／阶段：构建自动化 ／ build
- 台账态：完成（无） → 真身态：来源已消失或已勾选
- 核验命令：`grep -n 'tag#a7e6e7bf' docs/session接力.md`（或 `dispatcher_backlog --show conflicts` 当次重算）
- 原始结果：来源行不再生成（session接力／号池台账 滚动覆盖后已勾选或归档）
- 判定：真身对（台账 stale）——来源消失＝该 relay/opener 已闭环
- 处置：状态 完成 → 完成

---

### relay:tag#d14a9d68

- 场景／阶段：构建自动化 ／ build
- 台账态：待开（无） → 真身态：来源已消失或已勾选
- 核验命令：`grep -n 'tag#d14a9d68' docs/session接力.md`（或 `dispatcher_backlog --show conflicts` 当次重算）
- 原始结果：来源行不再生成（session接力／号池台账 滚动覆盖后已勾选或归档）
- 判定：真身对（台账 stale）——来源消失＝该 relay/opener 已闭环
- 处置：状态 待开 → 完成

---

## 生成器判据局限（登记备查，非本次冲突残留）

1. **续行「已移出」不识别**：`m1-job-profile-intake` 的 1.5b／1.7／5.6／6.8／9.2 在 tasks.md 里是
   `- [ ] <条目>` 下一行续写 `⤷ 已移出`。生成器 `parse_tasks` 只识别 checkbox 同行「已移出」
   （`moved = "已移出" in body`），续行不识别 ⇒ 真身恒判「待开/阻塞」。⛔ 本次不顺手改生成器判据，
   后续机制泳道可选：让 `ITEM_RE` 连读续行，或在 tasks.md 把「已移出」并回同行／勾选 checkbox。
2. **`delivery-units.md` 不被读**：`m1-intake-quality-fixes/U8/plan` 真身=待开（无 unit8 plan），
   而 delivery-units.md 明写第 8 章「不建议当成 TDD plan 跑 run-build」。该口径未进生成器，属已知缺口。
3. **gate checkbox 与判据实质分离**：`channel-resume-intake/0.1` 判据（M2 tasks 2.1/3.3/3.4 已勾）实质
   成立，但 gate 自身 checkbox 未勾 ⇒ 真身=待开。生成器只看 checkbox，不查判据子句。
4. **跨变更包依赖不追踪**：`m1-intake-quality-fixes/8.9` 依赖 `m1-job-profile-intake` 先归档，生成器
   不追跨包依赖，只能手工标注（历史备注已记）。
