---
name: liaison-unpack
description: 拆件会话章程正本——回件打标即开班后自动起的无头 Claude 会话必须把本文件全文逐字拼进 prompt 结尾作为行为边界；人也可手动 `/liaison-unpack` 走一遍同一流程。
---

# 拆件会话章程

本章程是拆件会话（回件桥打标后自动起活的无头 Claude 会话）唯一的行为边界正本。
仓库内其它任何代码与文档 MUST NOT 持有本文件 §〇 的整句副本；本章程由
`tools/liaison/unpack/charter.py::CHARTER_RELATIVE_PATH` 单点指向本文件。

## §〇 红线九项（绝对不做）

① 对外发送——含 `send-followup` 任何模式，一律不做。
② 建造——不修改 `tools/`、`app/`、`scripts/`、`tests/` 下的任何文件。
③ 新下裁决——不自行做出需要 Shao Peishen 或代理人拍板的新判断，转「待人」栏（见 §四）。
④ 不修改 `openspec/` 下任何 spec、design、tasks 文件。
⑤ 不写 `data/liaison.db*`、不写任何 `.env`；归档目录（`data/liaison/archive/`）只读；信号只经 CLI（`PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison unpack-signal`）操作，不直接改信号文件。
⑥ 不修改白名单配置（`tools/liaison/config/whitelist.yaml` 等）。
⑦ 不 `git push`；不做任何针对生产服务器（`.51`）的动作。
⑧ 不修改 `CLAUDE.md` 与 `.claude/skills/` 下任何文件（含本文件自身）。
⑨ 归档件疑似含候选人个人信息（简历、身份证号、手机号等）⇒ ⛔ 不读入 prompt、不摘录，只登记并转「待人」栏。判据＝文件名或首段显示为候选人简历／名单／证件。

以上九项，凡遇到需要新下判断的事项，一律转「待人」栏登记（§四），MUST NOT 自行决定。

## §一 信号探测与循环

1. 开工先探测信号：`PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison unpack-signal --probe`。
2. 输出 `[NO-SIGNAL]` ⇒ 本次会话结束，不做任何后续步骤。
3. 输出 `[SIGNAL]` ⇒ 按 §二 处理其中一条 pending 项。
4. 探测命令返回非零、命令缺失、或输出既不是 `[SIGNAL]` 也不是 `[NO-SIGNAL]` ⇒ **一律按「有信号」处理**，不得当成「无信号」结束。
5. 清信号只在 §二 的回灌结论已落档并 `git commit` 完成之后执行：`PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison unpack-signal --clear --before <检查点时刻>`；只清检查点之前的项，检查点之后新落的信号项保留给下一轮。

## §二 拆件步骤

1. 读信号：`unpack-signal --probe` 给出的 pending 项含 `letter_number`、`msgid`、`archived_path`。
2. 读归档件：用 `Read` 工具打开 `archived_path`（仓库相对路径）。⚠️ 遇 §〇 ⑨ 情形立即停止本条、转「待人」，不得继续往下读。
3. 判实质/非实质：内容是否为该封信「决策点」的实际回应。寒暄、误发、与决策点无关 ⇒ 判「非实质回件」。
4. 回灌结论：用 `Write` 工具把结论写入 `docs/跟进信/回件/<信编号>-<日期>.md`（目录不存在则新建）。
5. 台账转态或还原：
   - **实质回件** ⇒ 用 `Edit` 工具把 `docs/跟进信/README-跟进信清单.md` 中该信编号所在行，从第九态标记
     （`📨 回件已到，待拆件 …`）转为闭环四态之一（`📥 已回件并回灌 <日期>` / `✅ 无需回复` /
     `📨 已确认闭环 <日期>` / `❌ 已作废`，按内容判定其一）。
   - **非实质回件** ⇒ 把该行**按分隔符 `━━━ 原状态 ━━━` 之后的原状态原文**整行还原，
     并在 `docs/session接力.md` 登记还原原因。
6. 口径点台账：涉及口径点确认时，用 `python -m tools.liaison criteria --id HR-G-NN --to 已回复|已签认|已作废 [--evidence …]` 转态。
7. 登记接力文档：在 `docs/session接力.md` 追加一行，含【谁做：本次拆件会话（自动）】【状态：已闭环/待人】
   【判据：<本条怎样算完>】【不做会怎样：<下一条回件是否受阻>】四列。
8. 回到 §一 步骤 5 清信号，再回步骤 1 探测下一条 pending 项。

## §三 收口

- 会话在**主工作区**运行，不建 worktree、不建分支。
- 只 `git add` 本轮明确写入的路径（`docs/跟进信/回件/…`、`docs/跟进信/README-跟进信清单.md`、
  `docs/跟进信/口径点台账.md`、`docs/session接力.md`）；⛔ 不 `git add -A`、不 `git add .`、
  不 `git commit -a`、不 `git stash`。
- 提交前用 `git diff --cached` 自查暂存内容只含上述路径；用 `git status --porcelain` 做越界自检——
  发现列表外路径被改动 ⇒ 该路径不 add，在 `docs/session接力.md` 登记「自检发现越界编辑 <路径>，
  未提交、待人处理」。
- `git status` 里出现他人改动是正常的，不停、不问、不顺手提交。
- `git commit` 之后**不 push**（红线 ⑦）。
- 提交完用 `git log --oneline -5` 反查一次，确认刚才的提交真的落在当前分支上。
- 遇 `.git/index.lock` 已存在 ⇒ 等待重试，⛔ 绝不删除该锁。

## §四 待人栏

以下情形一律停止当前条目的自动处理，在 `docs/session接力.md` 追加一行【谁做：Shao Peishen】
【状态：待人】【判据：<本条何时算处理完>】【不做会怎样：<该条 pending 信号项保留，下一轮仍会
再探到，不会丢>】，然后跳过本条继续探测/处理其它信号项（若有）：

- §〇 ⑨ 命中：归档件疑似含候选人个人信息。
- 判「实质/非实质」出现真正的歧义（内容既不像决策点回应也不像纯寒暄）。
- 台账该信编号找不到对应行，或该行已不处于第九态（并发被别的流程改写）。
- 任何本章程未列出、需要新下判断的事项（红线③）。
