## Purpose

拆件会话做什么、绝不做什么，只有一处正本；它逐字进入每次会话的 prompt，红线在权限层与提示词层各守一道，让一个没人盯着的会话既能干完活、又不可能越过对外发送、建造、碰库、推送这几条线。

## ADDED Requirements

### Requirement: 章程正本唯一且由单点常量解析

拆件章程 SHALL 只有一份正本，位于仓库内；其路径 MUST 由代码中的单一常量解析。代码、测试与其它文档 MUST NOT 持有章程正文的副本。

#### Scenario: 正本路径

- **WHEN** 查询章程路径
- **THEN** 得到仓库内唯一一个文件
- **AND** 该路径只在一处常量定义

#### Scenario: 代码里无副本

- **WHEN** 扫描服务代码中是否含章程 §〇 的任一整句
- **THEN** 命中数为 0

### Requirement: 章程原文逐字进入 prompt 且前言在前

每次起活的 prompt SHALL 由「事件驱动前言」与「章程全文」拼接而成。章程全文 MUST 逐字出现在 prompt 的**结尾**，前言 MUST 出现在其之前；前言 MUST NOT 改写章程任何一句。前言 SHALL 包含：本次触发的信件编号与消息标识、信号文件路径、检查点时刻、以及「走完一轮后再探一次信号，仍有则再走一轮，直到无信号」这条只在前言不在章程的规则。

#### Scenario: prompt 结构

- **WHEN** 构造起活 prompt
- **THEN** prompt 以章程全文逐字结尾
- **AND** 章程之前存在非空前言
- **AND** 前言含信件编号、消息标识、检查点时刻

### Requirement: 章程 §〇 红线八项照单

章程 §〇 SHALL 明列以下八项为拆件会话绝对不做：① 对外发送（含 `send-followup` 任何模式）② 建造（修改 `tools/`、`app/`、`scripts/`、`tests/`）③ 新下裁决 ④ 修改 `openspec/` 下的 spec、design、tasks ⑤ 写 `data/liaison.db*` 与任何 `.env`（归档目录只读、信号只经 CLI）⑥ 修改白名单配置 ⑦ `git push` 与任何针对生产服务器的动作 ⑧ 修改 `CLAUDE.md` 与 `.claude/skills/`。凡需新下判断的事项 SHALL 转「待人」栏登记，MUST NOT 自行决定。

#### Scenario: 红线可被机器核对

- **WHEN** 解析章程 §〇
- **THEN** 八项各自的关键短语均可被找到

### Requirement: 章程规定信号探测与循环

章程 SHALL 规定：会话开工先探测信号；无信号即结束；有信号则处理；探测脚本出错、缺失或输出不认识时一律按「有信号」处理；清信号在回灌结论落档并提交之后、且只清检查点之前的项。

#### Scenario: 探测异常

- **WHEN** 信号探测命令返回非零或输出无法识别
- **THEN** 章程要求按「有信号」继续，而非结束

### Requirement: 章程规定工作区与 git 收口

章程 SHALL 规定会话在主工作区运行、不建 worktree；只 `git add` 本次明确列出的路径并 commit，MUST NOT `git add -A`、`git add .`、`git commit -a`、`git stash`，MUST NOT push；`git status` 里出现他人改动时不停、不问、不顺手提交；遇 `.git/index.lock` 等待重试且 MUST NOT 删除该锁。

#### Scenario: 收口纪律可核对

- **WHEN** 解析章程收口段
- **THEN** 含「不 push」「只 add 列出路径」「不删 index.lock」三条

### Requirement: 章程规定拆件的回灌与还原

章程 SHALL 规定回灌结论落 `docs/跟进信/` 下的回件目录并登记接力文档；判「非实质回件」时把台账该行从第九态**按后缀里的原状态还原**，并登记原因；判「实质回件」并完成回灌后把该行转闭环四态之一。

#### Scenario: 非实质回件还原

- **WHEN** 会话判定入站只是寒暄
- **THEN** 台账该行恢复为分隔符后的原状态原文
- **AND** 接力文档登记了还原原因

### Requirement: 章程规定候选人个人信息的处置

章程 §〇 SHALL 含第 ⑨ 条：归档件疑似含候选人个人信息（简历、身份证号、手机号等）时，MUST NOT 读入 prompt、MUST NOT 摘录，只登记并转「待人」栏。该条的最终措辞与判据待 Shao Peishen 确认。

#### Scenario: 疑似含简历的附件

- **WHEN** 归档件文件名或首段显示为候选人简历
- **THEN** 章程要求会话不读取其内容、只登记转人
