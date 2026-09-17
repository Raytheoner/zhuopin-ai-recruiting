## Purpose

候选人对 Offer 的答复由 HR 人工回填，接受即流转到 `hired`，拒绝则回退，每次都是流转事实，且候选人拒绝不算淘汰。

## ADDED Requirements

### Requirement: 答复人工回填

系统 SHALL 允许 HR 对"已审批"且已导出的 Offer 回填候选人答复：接受（含确认入职日期）、拒绝（须填原因）、谈判中（须填备注）。回填 MUST 记录操作人与时刻，MUST 允许从"谈判中"再次回填为接受或拒绝。

#### Scenario: 回填接受

- **WHEN** HR 回填"接受，入职日期 2026-10-08"
- **THEN** Offer 记录状态为"已接受"，投递阶段流转到 `hired`，`application.status` 变为 `hired`，流转事实新增一条（`actor_type=human`）

#### Scenario: 回填拒绝

- **WHEN** HR 回填"拒绝，原因：接受其他公司"
- **THEN** Offer 记录状态为"已拒绝"，投递阶段回到 `interview` 且 `application.status` 保持 `ongoing`，流转事实新增一条；淘汰记录表不新增任何行

#### Scenario: 拒绝后 HR 决定终止投递

- **WHEN** HR 在回填拒绝后选择"终止该投递"
- **THEN** `application.status` 变为 `refused`，流转事实新增一条，操作人为该 HR；淘汰记录表仍不新增行

### Requirement: 流转动作幂等且事实守恒

答复回填引起的流转 MUST 是独占的副作用执行单元，带幂等键；重跑 MUST NOT 产生第二条流转事实。对任一投递，`offer`／`hired`／回退相关的流转事实条数 MUST 等于成功回填次数。

#### Scenario: 回填节点重跑

- **WHEN** "接受"回填已提交，执行单元被从头重跑
- **THEN** 流转事实条数不变，`hired` 不被重复写入

### Requirement: 未导出前不可回填

系统 MUST 拒绝对尚未导出文书的 Offer 回填答复。

#### Scenario: 未导出即回填

- **WHEN** HR 对未导出过 docx 的 Offer 回填"接受"
- **THEN** 请求被拒绝，提示先导出并发送
