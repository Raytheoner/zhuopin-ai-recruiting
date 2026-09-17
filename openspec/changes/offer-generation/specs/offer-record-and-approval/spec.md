## Purpose

Offer 记录只存做决定所需的非敏感事实，录用决定由业务经理／决策人在工作台审批并留痕，审批通过之前不能导出文书。

## ADDED Requirements

### Requirement: Offer 记录的字段边界

系统 SHALL 允许 HR 对当前阶段为 `interview` 及之后且未终止的投递发起 Offer 记录，字段限于：岗位、部门、拟入职日期、汇报对象、备注（自由文本，页面提示不得填薪资）。记录 MUST NOT 含薪资、股权、签字费、津贴等任何报酬字段；系统 MUST NOT 在任何表、日志、留痕中持久化报酬信息。

#### Scenario: 发起 Offer

- **WHEN** HR 对一份 `interview` 阶段投递发起 Offer，填岗位／部门／入职日期／汇报对象
- **THEN** 产生一条状态为"待审批"的 Offer 记录，投递阶段流转到 `offer` 并写流转事实

#### Scenario: 对已淘汰投递发起被拒

- **WHEN** HR 对一份已有淘汰记录的投递发起 Offer
- **THEN** 请求被拒绝

### Requirement: 内部审批链

系统 SHALL 按岗位配置的审批链（一级或多级，审批人为可识别账号）逐级审批；每级审批 MUST 记录审批人、时刻、结论（通过／退回）、意见。退回 MUST 让 Offer 记录回到"待修改"，HR 修改后重新走审批链。审批链配置 MUST 由 HR 维护，MUST NOT 由 AI 生成或推荐审批人。

#### Scenario: 两级审批通过

- **WHEN** 业务经理通过、总经理通过
- **THEN** Offer 记录状态变为"已审批"，两条审批留痕各含审批人与时刻

#### Scenario: 一级退回

- **WHEN** 业务经理退回并填意见
- **THEN** Offer 记录回到"待修改"，退回留痕保存，后续级不被触发

#### Scenario: 非审批人操作被拒

- **WHEN** 一个不在该级审批人列表内的账号尝试审批
- **THEN** 请求被拒绝

### Requirement: 审批通过才可导出

系统 MUST 只允许对状态为"已审批"的 Offer 记录生成正式文书并导出 docx；"待审批"与"待修改"状态下 MUST NOT 提供导出。

#### Scenario: 待审批时导出被拒

- **WHEN** HR 对"待审批"的 Offer 点"导出"
- **THEN** 请求被拒绝，提示待审批

### Requirement: 审批动作幂等

每级审批 MUST 是独占的副作用执行单元，带幂等键；编排流程重跑时 MUST NOT 产生第二条同级审批留痕或重复推进状态。

#### Scenario: 审批节点重跑

- **WHEN** 业务经理的通过动作已提交，执行单元被从头重跑
- **THEN** 审批留痕条数不变，状态不重复推进

### Requirement: 录用决定不由 AI 做

系统 MUST NOT 提供任何"按评分自动发 Offer"或"AI 建议是否录用"的功能；Offer 发起与审批页面 MUST NOT 展示 AI 评分作为决策依据的推荐语。

#### Scenario: 页面不含 AI 录用建议

- **WHEN** 审批人打开审批页
- **THEN** 页面不出现"建议录用"类 AI 生成的结论
