## Purpose

把 M3 的合规承诺变成 CI 里可自动执行的断言，作为合规验收 #2 的机器判据。断言只查事实不改数据。

## ADDED Requirements

### Requirement: 面试评分证据 100% 可回溯

断言 MUST 检查：`run_type=interview` 的评分项中，`evidence_ref` 类型为 `interview_turn`、指向的 turn 存在、起止偏移在该 turn 文本范围内的比例为 100%；任何一条不满足即断言失败并列出违规项。

#### Scenario: 全部可回溯

- **WHEN** 所有面试评分项均指向存在的 turn 且偏移合法
- **THEN** 断言通过

#### Scenario: 存在悬空回指

- **WHEN** 某评分项指向的 turn 不存在
- **THEN** 断言失败并输出该评分项标识

### Requirement: AI 面试评分不得成为淘汰原因

断言 MUST 延用既有 `rejection_record.reason_type='ai_score'` 恒 0；并额外检查：`rejection_record` 的任何一行 MUST NOT 引用面试评分 run 作为原因。

#### Scenario: 存在以面试评分为由的淘汰

- **WHEN** 某条拒绝记录的原因引用了 `run_type=interview` 的评分 run
- **THEN** 断言失败

### Requirement: 身份核验与评分隔离

断言 MUST 检查：`identity_check` 表不含图像类列（BLOB／路径列名含 image、photo、face）；评分输入哈希对应的输入快照不含核验字段；评分项与总分表不含任何核验列。

#### Scenario: 表结构合规

- **WHEN** 检查 `identity_check` 表结构
- **THEN** 不存在图像类列，断言通过

### Requirement: 声学信号不进评分

断言 MUST 检查评分项与评分输入快照中不存在声学信号字段（语速、停顿、静默等）。

#### Scenario: 评分输入含声学字段

- **WHEN** 某次评分的输入快照含语速字段
- **THEN** 断言失败

### Requirement: 录音到期删除率 100%

断言 MUST 检查：`retention_until` 早于当前时刻减宽限期（默认 24 小时）的场次，录音 MUST 已删除且有删除留痕；不满足即失败。

#### Scenario: 存在过期未删

- **WHEN** 某场次已过期超过宽限期且录音仍存在
- **THEN** 断言失败并列出该场次

### Requirement: 真实候选人开闸默认关

断言 MUST 检查：真实候选人开闸开关关闭时，库内不存在非内部模拟的场次；开关开启时 MUST 存在合规验收 #2 的签认记录（文件路径与签认人）。

#### Scenario: 开关关闭

- **WHEN** 开关关闭
- **THEN** 所有场次的样本类别均为内部模拟，断言通过

### Requirement: 断言接入 CI 且可单独运行

以上断言 MUST 与既有合规断言同一入口运行、同一 CI 阶段执行；MUST 可单独按断言名运行以便定位；断言失败 MUST 阻断合并。

#### Scenario: CI 执行

- **WHEN** CI 运行合规断言阶段
- **THEN** M3 断言与 M1／M2 断言一起执行，任一失败即阶段失败
