## Purpose

入职日 HR 确认"已入职"或"放弃入职"，投递从此有正式终态事实；终态只能由人确认，且与 Offer 状态保持一致。

## ADDED Requirements

### Requirement: 确认已入职

系统 SHALL 允许 HR 在入职日当天或之后把投递确认为"已入职"。确认 MUST 写一条阶段流转事实（`stage_type=hired` 终态，`actor_type=human`，含操作人与实际入职日）。必需条目未完成且未豁免时系统 MUST 提示但 MUST NOT 阻止确认（由 HR 判断），提示内容 MUST 被留痕。

#### Scenario: 全部完成后确认

- **WHEN** 必需条目全部完成，HR 在入职日确认"已入职"
- **THEN** 投递写入终态事实，清单标记为已关闭

#### Scenario: 有未完成条目时确认

- **WHEN** 仍有 1 条必需条目待办，HR 确认"已入职"并填说明
- **THEN** 确认成功，留痕记录未完成条目清单与 HR 说明

#### Scenario: 入职日前确认被拒

- **WHEN** 入职日为明天，HR 今天确认"已入职"
- **THEN** 请求被拒绝

### Requirement: 放弃入职

系统 SHALL 允许 HR 把投递标为"放弃入职"（须填原因）。该动作 MUST 写流转事实，`application.status` 变为 `refused`，Offer 记录状态变为"已放弃"，清单关闭；MUST NOT 写入淘汰记录（这是候选人自愿退出，不是我方淘汰）。

#### Scenario: 放弃入职

- **WHEN** HR 标记"放弃入职，原因：接受其他公司"
- **THEN** `application.status` 为 `refused`，流转事实新增一条，淘汰记录表不新增行

### Requirement: 终态动作幂等且唯一

已入职与放弃入职 MUST 是独占的副作用执行单元，带幂等键；同一投递 MUST 只能有一个终态；重跑 MUST NOT 产生第二条终态事实。

#### Scenario: 已入职后再标放弃

- **WHEN** 投递已确认"已入职"，HR 尝试标"放弃入职"
- **THEN** 请求被拒绝

#### Scenario: 终态节点重跑

- **WHEN** 确认动作已提交，执行单元被从头重跑
- **THEN** 终态事实条数不变

### Requirement: 终态由人确认

系统 MUST NOT 依据清单全部完成、入职日到达或任何自动条件自动写入终态。

#### Scenario: 入职日到达无人操作

- **WHEN** 入职日已过 3 天且无人确认
- **THEN** 投递仍无终态事实，清单页显示"待确认"
