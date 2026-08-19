# 技术债清单

> 每条必须写明**触发条件**（什么时候还）与**不还的后果**。没有触发条件的条目会永远悬着。
> 本文件是仓库级真源。变更包归档后 `openspec/changes/` 里的 tasks.md 会移走，
> 计划文件也会变旧，但这份清单留着。

## TD-1 · `job_profile` 的两列时序留痕是过渡形态

**欠的是什么**：`job_profile.turn_started_at` 与 `job_profile.llm_latency_ms`
（2026-08-19，`m1-intake-quality-fixes` 第 1 章加入）。

**触发条件**：`ai-audit-trail-and-outbound-gate` 的 `analysis_run` 表落地即删
——该变更的 tasks 1.1 已包含 `latency_ms` 与 `created_at`，届时这两列成为冗余。

**怎么还**：删两列 + 删 `effect_persist_draft` 里对它们的写入 + 把统计口径
（见 `docs/superpowers/plans/2026-08-19-m1-intake-quality-fixes-unitA-storage-and-structured-questions.md`
Task 5 的分离口径 SQL）改指 `analysis_run`。

**不还的后果**：两套时序数据长期并存、互相矛盾，而没人知道该信哪一份。

**为什么当时要欠**：本批 P0/P1 的修复必须能被验证（"兜底档位是否真的减少了
空转轮、有没有把单轮延迟拖长"）。`ai-audit-trail-and-outbound-gate` 范围大得多
且尚未排期，等它意味着本批的效果只能靠感觉判断（design.md 决策 9）。
