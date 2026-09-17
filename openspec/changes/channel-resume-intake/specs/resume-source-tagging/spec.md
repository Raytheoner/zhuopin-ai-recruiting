## Purpose

每份简历记下"从哪个渠道来的"：能从导出件认出来的自动标，认不出的按 HR 给的默认来源标，标错了 HR 能改且留痕。

## ADDED Requirements

### Requirement: 来源值域

来源 SHALL 取自固定值域：`boss`（Boss直聘）、`liepin`（猎聘）、`51job`（前程无忧）、`zhaopin`（智联）、`referral`（内推）、`other`、`unknown`。值域由系统维护，HR MUST NOT 自由输入来源文本。每份简历记录 MUST 有自己的来源；候选人的来源 MUST 等于其最早一份简历的来源。

#### Scenario: 同一候选人两份不同来源的简历

- **WHEN** 候选人先从 `boss` 后从 `liepin` 各投一份
- **THEN** 两份简历来源分别为 `boss` 与 `liepin`，候选人来源为 `boss`

### Requirement: 自动识别来源

系统 SHALL 用确定性规则（文件名模式、导出件首页文本特征）识别来源；识别 MUST 是无副作用的纯计算，MUST NOT 调用任何模型；识别 MUST NOT 提取或存储平台水印之外的其他平台字段（如平台用户 ID）。识别不出时来源为上传时 HR 指定的默认来源；HR 未指定时为 `unknown`。

#### Scenario: 文件名可识别

- **WHEN** 文件名匹配 Boss直聘导出命名模式
- **THEN** 来源标为 `boss`

#### Scenario: 识别不出且 HR 指定了默认来源

- **WHEN** 文件名与版式都无法识别，HR 上传时指定默认来源 `liepin`
- **THEN** 来源标为 `liepin`，并记录"来自默认指定"

#### Scenario: 识别不出且未指定

- **WHEN** 无法识别且 HR 未指定默认来源
- **THEN** 来源标为 `unknown`

### Requirement: HR 事后改正留痕

系统 SHALL 允许 HR 修改任一简历的来源，修改 MUST 记录改正人、时刻、原值、新值；候选人来源随其最早简历的来源重算。

#### Scenario: 改正来源

- **WHEN** HR 把一份 `unknown` 的简历来源改为 `referral`
- **THEN** 简历来源更新，改正留痕新增一条含原值 `unknown` 与新值 `referral`
