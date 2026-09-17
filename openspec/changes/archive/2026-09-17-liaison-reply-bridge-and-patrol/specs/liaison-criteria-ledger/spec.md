## Purpose

把跟进信里抛出的口径点（决策点）作为可追踪的台账管理：每条有 ID、来源信、状态与证据；`已签认` 必须能指出依据，且不存在任何"放久了就算签认"的路径。

## ADDED Requirements

### Requirement: 口径点台账为版本管理内的表格

口径点台账 SHALL 是 `docs/跟进信/` 下版本管理内的 markdown 表格，每行含：口径点 ID（形如 `HR-G-NN`）、来源信编号、描述、状态（`待专员` / `已回复` / `已签认` / `已作废`）、evidence、更新时刻。

#### Scenario: 新增口径点

- **WHEN** 通过 CLI 新增一条口径点
- **THEN** 台账多出一行，状态为 `待专员`，ID 唯一且连续

### Requirement: 转态只经 CLI 且已签认必须带 evidence

台账状态转换 SHALL 只经本服务的 `criteria` 子命令执行。转为 `已签认` 时 MUST 提供 `--evidence`（指向回件归档件或落档件的路径与定位）；缺失或为空 SHALL 被拒绝，退出码为 3，台账不变。

#### Scenario: 缺 evidence 的签认

- **WHEN** 执行转 `已签认` 而未给 `--evidence`
- **THEN** 退出码为 3
- **AND** 台账逐字节不变

#### Scenario: 带 evidence 的签认

- **WHEN** 执行转 `已签认` 并给出非空 `--evidence`
- **THEN** 该行状态为 `已签认`，evidence 列为所给值

### Requirement: 无任何超期自动签认路径

系统 MUST NOT 存在任何按时间、按沉默、按批处理把口径点自动转为 `已签认` 的路径；`criteria` 子命令 MUST NOT 接受基于时间的批量签认参数。

#### Scenario: 扫描代码面

- **WHEN** 检查 `criteria` 子命令的参数与实现
- **THEN** 不存在任何以时间或超期为条件改写状态为 `已签认` 的分支

### Requirement: 转态命令不打开值守数据库

`criteria` 子命令 MUST NOT 打开值守数据库文件。

#### Scenario: 子命令运行

- **WHEN** `criteria` 子命令运行
- **THEN** 值守数据库文件未被打开
