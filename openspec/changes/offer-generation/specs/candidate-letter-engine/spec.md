## Purpose

Offer 与拒信共用一套文书引擎：模板由 HR 维护、草稿由系统按投递事实生成并带 AI 标识、每一版都留痕可回溯、导出为 docx 给 HR 手工补齐后发出。

## ADDED Requirements

### Requirement: 文书模板由 HR 维护并版本化

系统 SHALL 维护两类文书模板（Offer、拒信），由 HR 角色创建与更新，每次更新产生新版本而不覆盖旧版本。模板 MUST NOT 含候选人评分、排名、硬门槛标记的占位符；Offer 模板 MUST NOT 含任何由系统填充的薪资类占位符（薪资位置只允许留空供手填）。

#### Scenario: 更新模板

- **WHEN** HR 修改 Offer 模板并保存
- **THEN** 产生新版本，旧版本仍可查看，已生成的草稿保持指向各自生成时的版本

#### Scenario: 模板含评分占位符被拒

- **WHEN** HR 保存的模板中出现"总分"或"排名"类占位符
- **THEN** 保存被拒绝并指出该占位符

### Requirement: 按投递事实生成草稿并带 AI 标识

系统 SHALL 按模板与投递事实（候选人姓名、岗位、部门、入职日期、汇报对象；拒信则为岗位与礼貌性措辞）生成草稿；草稿 MUST 带 AI 生成标识；生成 MUST 留痕（模型标识取 API 响应字段、prompt 版本、temperature=0、输入哈希、模板版本、原始响应）。同一投递重复生成 MUST 产生新版本。

#### Scenario: 生成 Offer 草稿

- **WHEN** HR 对一份已审批通过的 Offer 记录点"生成文书"
- **THEN** 得到带 AI 生成标识的草稿，留痕含模型标识、prompt 版本与模板版本

#### Scenario: 拒信只对有淘汰记录的投递生成

- **WHEN** HR 对一份没有淘汰记录的投递点"生成拒信"
- **THEN** 请求被拒绝，提示先在复核工作台完成批量确认

### Requirement: 编辑不去标，显式标记人工撰写才去标

HR 编辑草稿后文书 MUST 继续带 AI 生成标识；只有显式"标记为人工撰写"才去掉标识，该动作 MUST 留痕（谁、何时、原 AI 版本）。

#### Scenario: 标记为人工撰写

- **WHEN** HR 对一版草稿点"标记为人工撰写"
- **THEN** 标识去掉，留痕记录 HR、时刻与原 AI 版本号

### Requirement: 导出 docx

系统 SHALL 把指定版本的草稿导出为 docx；导出内容 MUST 与草稿一致并保留 AI 生成标识（未标记人工撰写时）；Offer 导出 MUST 在薪资位置留空。导出 MUST 写访问留痕（谁、何时、哪份投递、哪一版）。

#### Scenario: 导出带标识

- **WHEN** HR 导出一版未标记人工撰写的 Offer
- **THEN** docx 内含 AI 生成标识文字，薪资位置为空白，访问留痕新增一条

### Requirement: 文书草稿的查看留痕

任何查看文书正文的操作 MUST 写访问留痕；留痕失败时 MUST NOT 返回正文。

#### Scenario: 留痕失败

- **WHEN** 访问留痕表不可写
- **THEN** 文书页不显示正文并提示留痕失败
