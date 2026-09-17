# `data/eval/m2-pilot/` 目录规范（M2 U0 对比样本）

- **位置**：`data/eval/m2-pilot/`，整个 `data/` 已在 `.gitignore`，⛔ 任何样本、留痕 JSONL、对比输出都不进版本库。
- **来源类别**（`truth.json.sample_class`）：`synthetic`（合成，无真人信息）／`anonymized`（脱敏）／`departed`（历史离职）。`live` 一律拒绝（`load_samples` 抛错；spec「评测集样本来源与访问控制」）。
- **访问控制**：目录只在开发机与 `.51` 存在；`.51` 上由 Windows ACL 限制到运行账户与 HR 管理员（发版时随 U7 落实）。
- **留存期**：合成样本无限期；脱敏／离职样本与回件归档件 **90 天**（2026-09-17 裁决，以合规验收 #1 留存策略为准）。
- **禁止训练用途**：`truth.json` 的人工排序与字段真值只用于离线指标，⛔ 不进任何训练／微调／prompt 自动优化（U6 7.5 有机器断言）。
- **结构**：`<id>.txt`（真值文本）、`<id>.docx`、`<id>.pdf`、`<id>_scan.pdf`（可选）、`truth.json`（见 `scripts/gen_pilot_samples.py` 文档串）、`runs/<候选>/<ts>.jsonl`（每次 LLM 调用一行，铁律 3）、`compare-run.md/json`、`bench-bge-m3.json`、`smoke-<平台>.json`。
- **替换为真实脱敏样本**：保持同一布局与 `truth.json` 字段，`human_rank` 改为汤丽萍判例批改表的名次，脚本零改动。
