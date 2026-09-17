## MODIFIED Requirements

### Requirement: 门禁覆盖范围

系统 SHALL 对候选人拒信（`rejection_letter`）、面试邀约（`interview_invitation`）与 Offer 函（`offer_letter`）三类外发动作施加人工确认门禁。这三类 MUST 一律判为高风险。

面向内部员工的通知（如岗位画像确认卡片、Offer 内部审批通知）不在门禁范围内，其外发行为保持原状。

#### Scenario: 拒信走门禁

- **WHEN** 系统准备外发一封候选人拒信
- **THEN** 门禁判定生效，未经人工确认不得外发

#### Scenario: 邀约走门禁

- **WHEN** 系统准备外发一封面试邀约
- **THEN** 门禁判定生效，未经人工确认不得外发

#### Scenario: Offer 函走门禁

- **WHEN** 系统准备外发一封候选人 Offer 函
- **THEN** 门禁判定生效，未经人工确认不得外发
- **AND** 类型 `offer_letter` 已在类型清单中登记，不再按"未知类型"拦截

#### Scenario: 内部通知不受影响

- **WHEN** 系统向内部业务经理推送岗位画像确认卡片或 Offer 审批待办
- **THEN** 该消息不经候选人外发门禁，投递行为与本变更前一致
