## Purpose

面试结束后，把转写按 turn 对齐、按冻结 rubric 逐维评分，产出每条都能回指候选人原话的 ScoreCard 与给面试官的要点提示。ScoreCard 只是人工终面的参考，不是决定。

## ADDED Requirements

### Requirement: 转写按 turn 对齐

post 段 MUST 把场次的全部 turn（语音转写或文本作答）整理为可定位的证据单元：每个 turn 有唯一标识、文本、字符偏移可寻址；语音 turn 另带音频起止毫秒与 ASR 置信度。低置信度 turn（阈值岗位级可配）MUST 标记为"转写待复核"，评分时 MUST 仍参与但其证据 MUST 标注低置信度；面试官视图 MUST 能对低置信度 turn 回放原音频。

#### Scenario: 常规对齐

- **WHEN** 一个场次已完成
- **THEN** 每个 turn 可按（场次、序号、起止偏移）定位到文本与音频

#### Scenario: 低置信度转写

- **WHEN** 某 turn 的 ASR 置信度低于阈值
- **THEN** 该 turn 标记"转写待复核"，基于它的评分证据带低置信度标注

### Requirement: 逐维评分带 turn 回指

评分 MUST 按冻结快照中的 rubric 逐维进行；每条评分项 MUST 携带非空的证据回指（turn 标识＋起止偏移＋原话摘录）；回指为空或指向不存在的 turn 的评分项 MUST NOT 写入（沿用既有存储层约束）。评分 MUST 走既有 AI 调用留痕（配置侧模型、响应侧模型、prompt 版本、`temperature=0`、输入哈希、rubric 快照、原始响应）。评分输入 MUST NOT 包含身份核验数据、声学情绪信号或候选人身份字段。

#### Scenario: 一个场次的评分

- **WHEN** 一个场次完成对齐
- **THEN** 每个 rubric 维度各有一条评分项，均带可定位的 turn 回指
- **AND** 全部评分项关联到同一条 AI 调用留痕记录

#### Scenario: 模型未给出证据

- **WHEN** 模型返回的某维度评分缺少可定位证据
- **THEN** 该次评分整体判不可用，不写入任何评分项，场次标注"评分失败待重试"，可观测

#### Scenario: 回指校正

- **WHEN** 模型给出的偏移与原话摘录不一致
- **THEN** 系统用摘录在 turn 文本中反查校正偏移；反查失败判该评分项无证据

### Requirement: ScoreCard 与要点提示只作参考

ScoreCard MUST 包含：逐维得分与证据、总体摘要、"面试要点提示"（建议人工终面追问的方向，每条指向具体维度与 turn）。ScoreCard MUST NOT 触发阶段流转、淘汰或对候选人的外发；面试官视图 MUST 带 AI 生成标识与"仅供参考，最终决定由面试官作出"的说明。淘汰记录 MUST NOT 以 AI 面试评分为直接原因（延用既有断言）。

#### Scenario: 低分场次

- **WHEN** 一个场次总分最低
- **THEN** 投递阶段不变，无拒绝记录产生，面试官视图正常展示

### Requirement: 声学信号只展示不计分

语速、停顿时长、静默比例等声学信号 MUST 只在面试官视图中作为参考展示，MUST NOT 进入评分输入、评分项或总分；MUST NOT 做情绪、性格或可信度推断；展示处 MUST 注明"与能力无映射关系，仅供参考"。

#### Scenario: 面试官查看声学参考

- **WHEN** 面试官打开一个场次的 ScoreCard
- **THEN** 声学参考在独立区域展示，评分项中不存在任何声学字段

### Requirement: 面试官视图与回放

面试官 MUST 能按投递查看 ScoreCard、逐 turn 转写、点击证据回指跳到对应原话并回放该段音频。查看录音与转写 MUST 写访问留痕（谁、何时、看了哪个场次）。面试官只能看到分配给自己岗位的场次。

#### Scenario: 点击证据

- **WHEN** 面试官点击某条评分项的证据
- **THEN** 页面定位到对应 turn 的原话并可回放该段音频，写入一条访问留痕

### Requirement: HR 进度与完成率视图

HR MUST 能按岗位与批次查看场次状态分布与完成率（已完成 ÷ 已签发链接）；完成率 MUST 可按批次导出用于验收（门槛 ≥ 70%）。

#### Scenario: 批次完成率

- **WHEN** HR 查看一个批次
- **THEN** 看到各状态计数与完成率

### Requirement: 一致性评估的数据导出

系统 MUST 能为一致性评估导出一批 ScoreCard（去除候选人身份字段）供 3 名面试官独立打分；导出 MUST 留痕。

#### Scenario: 导出评估包

- **WHEN** HR 对一个内部模拟批次点"导出一致性评估包"
- **THEN** 得到不含身份字段的 ScoreCard 集合，留痕一次导出
