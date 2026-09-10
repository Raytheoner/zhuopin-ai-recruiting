## Why

HR 值守通道（`hr-wecom-aibot-liaison`，62/66）能把汤丽萍的消息**归档、入队**，但入站只知道「有人发了消息」——它不知道这条消息是**对哪封跟进信的回件**，台账 `docs/跟进信/README-跟进信清单.md` 不会变，串行闸不会响，也没有任何东西去拆件回灌。win 端（企业AI转型）两个月前已用「回件→README 第九态→信号→非阻塞起无头会话」把这段从人手里拿掉，机制经六处真身核验（`docs/findings/2026-09-10-win端打标即开班机制核验与HR移植方案.md`）。**为什么是现在**：人事部#1 已于 2026-09-09 真发出、正等回件；她的回件一到，这段流程就要么自动、要么回到人肉翻聊天记录。

## What Changes

- **P0 · 回件桥＋第九态**（新能力 `liaison-reply-bridge`）：名单内入站落库后，桥按串行原则在台账里定位该发送人的在途信（收信人＝白名单 `name`，状态以 `✅ 已推送` 开头），把「发送状态」列改写为第九态 `📨 回件已到，待拆件`，**原状态原样接在后面**（可溯源、可还原）。第九态**仍属在途、串行闸仍锁**。
  - 🔴 **无在途信 ⇒ 不标、不起活**（Shao Peishen 2026-09-10 答 Q1a）——这是我们比 win 多出的分支：我们的入站不只承接回件，还承接岗位需求。
  - 🔴 **同一收信人 ≥2 封在途 ⇒ 拒标＋告警、不起活**（答 Q2a，fail-closed）。
  - 在途信存在时，**该发送人的任何名单内入站都算回件**（答 Q7a，照搬 win）；拆件会话判「非实质回件」则按后缀里的原状态还原。
- **P1 · 打标即开班**（新能力 `liaison-unpack-dispatch`）：与打标**同一次调用**里 ① 落信号（JSON `pending[]`）② **非阻塞**起一个无头 `claude -p` 拆件会话；零轮询、不等任何定时器。并发守卫用 **pid 判活**（`os.kill(pid, 0)`），🔴 判活查询本身失败一律归「不存活」（代价不对称）；坏锁按陈旧锁处理照常起活。**fail-open 只对「起没起来」负责、⛔ 从不读写信号文件**：四类失败（章程读不到／日志建不了／`Popen` 抛／兜底 except）只记审计不上抛。
  - 🔴 **权限层不照搬 win 的 `--dangerously-skip-permissions`**（答 Q3a）：用 `--permission-mode acceptEdits` ＋ `--allowedTools` 白名单，`send-followup`／`git push`／其它 `python -m tools.liaison` 子命令在**权限层**被拒；章程 §〇 红线作第二层。
  - 拆件会话在**主工作区**跑（答 Q4a）：桥与拆件会话改的是同一份台账，无 worktree 副本分叉；库隔离靠权限层（会话打不开库）。
- **P2 · 拆件章程正本**（新能力 `liaison-unpack-charter`）：落 `.claude/skills/liaison-unpack/SKILL.md`，由**单点常量**解析路径，🔴 代码里⛔ 不留副本；运行时整段读出拼进 prompt（事件驱动前言 ＋ 章程全文）。§〇 红线八项照单全收（答 Q3b）：对外发送／建造／新裁决／改 spec·design·tasks／写 `data/liaison.db*` 与 `.env`／改 `whitelist.yaml`／`git push` 与任何 `.51` 动作／改 `CLAUDE.md` 与 `.claude/skills/`。git 收口＝只 `add` 列出路径＋commit、⛔ 不 push（答 Q6a）。
- **P3 · 口径点台账**（新能力 `liaison-criteria-ledger`）：`docs/跟进信/口径点台账.md` ＋ 转态 CLI；`已签认` **必须带 `--evidence`**，缺则拒绝（退出码 3）；🔴 **⛔ 无任何超期自动签认路径**。
- **前置（本包 ⛔ 不做，只列门槛）**：① TD-42 已还并真实验证；② SDK `message` 事件→`handle_inbound_message` 的接线（当前**完全缺失**，且 `test_this_chapter_wires_no_message_handling` 明令不接）——**归 `hr-wecom-aibot-liaison` 追加任务**（答 Q5a），本包开工前必须已接通并有一条真实入站落 `liaison_message` 的记录。
- 🔴 **载体不换**：`liaison_task` 保持 DB，台账保持 markdown，⛔ 不引入 markdown 队列＋编辑锁＋hook。**移植的是流程与红线，⛔ 不是载体。**

## Capabilities

### New Capabilities

- `liaison-reply-bridge`: 回件桥——名单内入站落库后按串行原则定位在途跟进信并写第九态（原状态接后）；无在途不标、多在途拒标告警；同一消息重投不重复标；桥失败不影响已完成的归档与入队
- `liaison-unpack-dispatch`: 打标即开班——信号落盘、非阻塞起无头拆件会话、pid 判活并发守卫、fail-open 四类失败只审计、信号只由 CLI 清且只清检查点之前
- `liaison-unpack-charter`: 拆件章程——仓库内单一正本、运行时逐字拼进 prompt、§〇 红线、权限层白名单、信号探测与循环规则、主工作区与 git 收口纪律
- `liaison-criteria-ledger`: 口径点台账——`已签认` 必须带 evidence、无超期自动签认、转态由 CLI 执行且拒绝码可辨

### Modified Capabilities

（无。`liaison-inbound-whitelist` / `liaison-message-archive` / `liaison-task-queue` 的要求不变——桥在它们**之后**执行、失败不回滚它们。`openspec/specs/` 下四个活文档能力均不受影响：`outbound-approval-gate` 按其「门禁覆盖范围」原文，拆件会话不对外发送任何内容；`ai-decision-audit` 不适用——本包不做任何 AI 评分。）

## Impact

- **新增代码**：`tools/liaison/unpack/`（`bridge.py` / `signal.py` / `dispatch.py` / `charter.py` / `criteria.py`）；`tools/liaison/__main__.py` 加三条子命令（`unpack-signal`、`unpack-dispatch`、`criteria`）与桥的接线位；`tools/liaison/storage/schema.py` 加一张 append-only 审计表 `liaison_unpack_audit`。
- **新增文档**：`.claude/skills/liaison-unpack/SKILL.md`（章程正本）；`docs/跟进信/口径点台账.md`；`docs/跟进信/README-跟进信清单.md`「发送状态」表**加第九态一行**（八态→九态，语义表，不是代码）。
- **新增运行时文件**（全在 `data/liaison/`，已被 `.gitignore` 与 `sync-to-server.sh` 的 `EXCLUDE_NAMES` 排除）：`unpack-signal.json`、`unpack-session.lock`、`logs/unpack-headless/<UTC戳>.log`。
- **`tools/liaison/.env.example`**：新增 `HR_LIAISON_CLAUDE_BIN`（可选，覆盖 `claude` 二进制路径）、`HR_LIAISON_UNPACK_BUDGET_USD`（无头会话预算上限）。占位符只写变量名。
- **依赖**：无新第三方依赖。运行期依赖本机 `claude` CLI（已装，`~/.local/bin/claude`）与其登录态——launchd 环境下能否拿到登录态**必须真实起活实测**（见 tasks 验收）。
- **不触碰**：`app/`、`scripts/`、根 `requirements.txt`、`sync-to-server.sh`、`.51`、`session_client.py`（`0909AS` 触碰区）、`inbound.py`／`archive.py`／`queue.py` 的既有行为。
- **对 `hr-wecom-aibot-liaison` 的影响**：需追加一条任务「SDK `message` 事件接线」（含删守卫测试）——由该包自己的进度台账登记，本包只在 tasks §0 列为门槛。
- **人**：Shao Peishen 做一次真实起活验收；章程 §〇 第 ⑨ 条已由他定稿（design D15）。

## Non-goals（不做什么）

- **不做拆件本身的"智能"**：拆件会话按章程做事，本包只保证它被**正确地、可控地起起来**并有红线。章程内容的业务判断力属另一轮。
- **不换载体**：不把 `liaison_task` 换成 markdown 队列，不引入编辑锁与 hook；不给台账加 CLI 取号／闸核（HR 侧口径：取号看表）。
- **不做对外发送**：拆件会话永不调 `send-followup`；回件的答复仍由人起草、Shao Peishen 本人发。
- **不改 `session_client.py`**：TD-42 归 `0909AS`；本包与之触碰区零重叠，可并行。
- **不做消息接线**：归 `hr-wecom-aibot-liaison`（答 Q5a）。
- **不做定时巡检**：零轮询是设计目标，⛔ 不加 launchd `StartInterval` 兜底扫信号（那是 win 端 `PT1H` 的形态，本包刻意不移植——信号原地留着，下一条真实回件会再触发）。
- **不部署 `.51`**：与 `tools/liaison/` 同口径，永不。
- **不做多收信人／多群**：HR 线只有一群一线（intent F6）。

## 合规影响说明

**处理的个人信息**：内部同事（汤丽萍、邵培申）的企微 `userid`、中文姓名、回件正文与附件内容。**不处理候选人个人信息**——但见下面第三条。

- **法律依据**：与 `hr-wecom-aibot-liaison` 同（PIPL 第 13 条第（二）项，内部履职沟通）。
- **新增的数据流向（⚠️ 与 `hr-wecom-aibot-liaison`「本服务不调用任何 LLM」不同）**：拆件会话是一个 `claude -p` 进程，它会把**回件归档件的内容**读入 prompt 送往 Claude 模型。回件内容＝专员对岗位需求确认／机制反馈的工作沟通，与本项目此前用 Claude Code 处理仓库文档的数据流向同类。
- 🔴 **候选人个人信息**：合规红线「简历数据不出境」。回件附件**可能**含候选人简历／名单（例如她把某岗位的候选人材料一并发来）。本包在章程 §〇 加第 ⑨ 条「归档件疑似含候选人个人信息 ⇒ ⛔ 不读入 prompt、不摘录，只登记转待人」，该条措辞与判据由 Shao Peishen 2026-09-10 答 `1a` 定稿（design.md D15）。
- **审计**：桥与起活的每一步都落 `liaison_unpack_audit`（谁、何时、哪条 `msgid`、哪封信、结果），`automation_level='L1'`，满足「谁在什么时候动了哪份材料」可查。
- **凭据**：拆件会话走 `claude` CLI 自身的登录态，本包 ⛔ 不接触任何 API key；`.env` 在会话的权限层红线内（不可写）。
- **留存**：信号文件、锁文件、无头日志均在 `data/liaison/`，随 `hr-wecom-aibot-liaison` 的留存清理口径一并退役。
