## Purpose

Offer 与拒信默认由 HR 导出／复制后自行发送；若走系统外发，只能经既有候选人外发门禁，`offer_letter` 作为新登记类型与 `rejection_letter` 同等受控，不开任何新口子。

## ADDED Requirements

### Requirement: 默认交付形态是 HR 自行发送

系统 SHALL 提供"导出 docx"（Offer）与"复制文案"（拒信）作为默认发送方式，并允许 HR 回填"已发出（渠道）"。系统外发路径的开启 MUST 是人工决定，默认关闭。

#### Scenario: 复制拒信后回填

- **WHEN** HR 复制拒信文案经邮件发出后回填"已发出·邮件"
- **THEN** 该拒信记录状态为"已发出"，记录 HR 与时刻

### Requirement: 系统外发一律经既有门禁

若 HR 选择"由系统发送"，消息 MUST 经候选人外发门禁，类型为 `offer_letter` 或 `rejection_letter`，收件对象拍平为非空字符串标识，携带人工确认人标识；总开关关闭时 MUST 被拦截并留痕。系统 MUST NOT 存在绕过门禁的候选人文书外发路径。

#### Scenario: 总开关关闭时系统发送 Offer

- **WHEN** HR 点"由系统发送"且总开关关闭
- **THEN** 消息进入待审批队列，留痕记录"外发总开关关闭"

#### Scenario: 缺 AI 标识的拒信走系统外发

- **WHEN** 一封由 AI 生成但标识被移除的拒信进入系统外发
- **THEN** 门禁拦截，不外发

#### Scenario: 收件对象缺失

- **WHEN** 候选人联系方式保管未开启导致收件对象为空
- **THEN** 门禁按收件对象未知拦截，留痕保留原始取值

### Requirement: 拒信发送时机由 HR 决定

系统 MUST NOT 在批量确认淘汰时自动生成或自动发送拒信；拒信生成与发送 MUST 由 HR 在工作台逐份或按批显式触发。

#### Scenario: 批量确认淘汰后

- **WHEN** HR 在 M2 工作台批量确认淘汰 12 份投递
- **THEN** 系统不生成任何拒信草稿，直到 HR 在拒信页显式选择这些投递并点"生成"
