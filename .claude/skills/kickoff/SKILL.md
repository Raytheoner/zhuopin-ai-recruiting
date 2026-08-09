---
name: kickoff
description: 生成新开会话用的开场 Prompt（Opener），把界面、Session 新旧、worktree、工作目录、前置检查、完成标准全部写死，避免人工判断出错。当用户说"给我开场 prompt""kickoff""新起一个会话要怎么说""run-build 的 opener""转场 prompt"时使用。
---

生成一份**可一键复制**的开场 Prompt，用于新开 Claude Code 或 Cowork 会话。

目的是消灭"选错环境"这类错误——界面、Session 新旧、worktree 要不要，全部在 Prompt 里写死，人不需要判断。

## 先确认阶段

若用户没说清是哪个阶段，问一句。四个阶段对应四种环境配置：

| 阶段 | 界面 | Session | Worktree | 工作目录 |
|---|---|---|---|---|
| `openspec-propose` 需求与契约 | Cowork 或 CC 均可 | **新开** | ❌ 不要 | 主检出 |
| `spec-to-plan` 出实现计划 | **CC Desktop Code tab / 终端** | **新开** | ❌ 不要 | 主检出 |
| `run-build` 写代码 | **CC Desktop Code tab / 终端** | **新开** | ✅ **要** | worktree 内 |
| `openspec-archive-change` 收口 | Cowork 或 CC 均可 | 新开 | ❌ 不要 | 主检出 |

**为什么一律新开 session**：上一阶段的会话里塞满了它自己的产物与推理，接着用会把新任务锚定在旧结论上。重跑一份计划时尤其明显——旧会话会不自觉地复述旧计划。OpenSpec 官方也建议实现前清空上下文。

例外：同一阶段被中断后**恢复**，可以利旧（`run-build` 有进度台账，重跑同一计划不会重复已完成的 Task）。

## 输出格式（固定模板，不要改结构）

用一个围栏代码块输出，方便一键复制。**代码块内不要出现 markdown 加粗、表格这类渲染标记**，纯文本。

````
```
【环境 · 照此设置，不要自行判断】
界面：<具体到 tab>
Session：<新开 / 利旧+理由>
工作目录：<绝对路径>
Worktree：<不需要 / 需要，分支名 xxx>
分支：<分支名>

【开工前自检 · 任一条不过就停下来告诉我，不要绕过】
1. ...
2. ...

【任务】
<具体要做什么，含输入文件的绝对路径>

【完成标准】
- ...

【不要做】
- ...
```
````

## 各阶段的必备内容

### 通用（每份 Opener 都要有）

- 前置检查里必须有一条：**确认能调用所需的 superpowers/openspec 能力，调不到就停**
- "不要做"里必须有一条：**不得读取或修改 `OneDrive-Personal/Projects/企业AI转型/`**

### `spec-to-plan` 专属

- 前置检查：能否调用 `superpowers:writing-plans`
- 任务里必须点名**输入是哪几份 `spec.md` 的绝对路径**，并显式禁止用 `tasks.md` 当输入
- 完成标准必须含：Global Constraints 段逐字来自 `CLAUDE.md`（含「工程铁律」与「部署约束」）
- 不要做：不要开始实现、不要建 worktree

### `run-build` 专属

- Worktree 段要写清建完之后**手工补三样**：`.env`、本地 SQLite/`data/`、Python venv（都被 gitignore 挡着，git 不带过去，缺失时报错完全不像"文件没拷"）
- 前置检查：计划里有没有 Global Constraints 段（没有就停，回去重跑 `spec-to-plan`）
- 前置检查：`.superpowers/sdd/progress.md` 是否已存在（存在说明跑过一部分，已完成的 Task 不要重跑）
- 完成标准必须含：回勾 `openspec/changes/<change>/tasks.md` 的对应条目，**且只有 final review 通过才勾**
- 不要做：不要中途问"要继续吗"；不要 `git clean -fdx`（会毁掉进度台账）

## 注意

- 路径一律写**绝对路径**，不写"当前目录"
- 若某个前置条件你已经知道结果（比如刚验证过 LLM 域名连通），直接在 Opener 里写明结论，减少对方重复劳动
- Opener 之外，用一两句话说明**为什么是这个环境配置**，帮用户建立判断力，但这部分放在代码块外
