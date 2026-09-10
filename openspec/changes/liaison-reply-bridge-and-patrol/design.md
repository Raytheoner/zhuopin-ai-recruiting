## Context

动机与范围见 `proposal.md`；需求收敛过程与事实清单见 `intent.md`（§一 F1–F15）。这里只列塑造技术方案的现状与约束：

- **win 端真身已核**（`docs/findings/2026-09-10-win端打标即开班机制核验与HR移植方案.md` §一）：一次调用里「第九态 → 信号 → 非阻塞 Popen」三件事；pid 判活；fail-open 四分支只审计；章程正本在仓库、代码无副本；真实起活验收靠手工实测记录。**四条取舍照搬，载体不搬。**
- **HR 侧现状**：归档（`archive.py`）、入队（`queue.py`）、白名单（`whitelist.py`）、台账回写纯函数（`followup.py::compute_backfilled_ledger`）、launchd 常驻、`idempotent_effect` 幂等机制均已就位。**缺的是 F1（消息事件接线，归 `hr-wecom-aibot-liaison`）与本包的 P0–P3。**
- **线程模型不可破**（`__main__.py` docstring）：值守线程独占 sqlite 连接与状态机；SDK 回调只往队列放 `(事件名, 时刻)`；看门狗线程只读存活戳。⇒ 桥必须在**值守线程**里、在 `handle_inbound_message` 返回之后跑。
- **事务纪律不可破**（F12）：非测试代码 ⛔ `conn.commit()`／`with X:`；提交由 `idempotent_effect` 独占；AST 测试守着。⇒ 审计行只能做成 `effect_*` 节点。
- **台账是 markdown 且进了版本库**（F8）：每个 worktree 一份副本；桥在主工作区写。
- **权限层可选**（F9）：`claude -p` 支持 `--permission-mode acceptEdits` ＋ `--allowedTools`，非交互模式下未放行的工具调用直接被拒。
- **TD-42 在还**（F14，`0909AS` 触碰 `session_client.py`）；本包 ⛔ 不碰该文件。
- **Shao Peishen 2026-09-10 八项裁决**（intent §二 Q1–Q7），⛔ design 审时不重开。

## Goals / Non-Goals

**Goals：**

- 回件一到，台账当场进第九态、串行闸继续锁、拆件会话当场起——**零轮询、零人工触发**。
- 起活失败绝不吞信号、绝不打断消息链路；忙则跳过，信号原地等下一条真实回件再触发。
- 无头会话的爆炸半径由**权限层**限定，章程红线只是第二层。
- 每一步可审计、可计数；真实起活有实测记录而非单测全绿。

**Non-Goals（设计层面）：**

- 不追求「拆件会话一定能干完」——fail-open 只对「起没起来」负责。
- 不追求信号零丢失——信号文件损坏时以新文件替换，只保本次一项（旧项会由下一条回件再触发）。
- 不做 README 的编辑锁——桥的写是「重读→只改一行→原子替换」，与人工编辑撞车的窗口是毫秒级，撞了以审计记录暴露而非加锁。
- 不与 win 端同构：不移植 `工具-共享文档编辑锁.py`、不移植 markdown 队列、不移植 `PT1H` 定时兜底。

## Decisions

### D1 · 无在途信 ⇒ 不标、不起活（Shao Peishen 答 Q1a）

桥找不到该发送人的在途信时，台账不变、无信号、无起活，只留审计 `bridge_skipped_no_inflight`。消息照常归档＋入队，由人在 `liaison_task` 里处理。

**为什么**：HR 入站不只承接回件（还承接岗位需求、自测消息）。「一律标」会把新岗位需求误挂成对某封信的回复，且把已闭环的信重新拉回在途。**替代方案**（不标但起会话分拣）会把章程从「拆件」扩成「通用入站分拣」，红线面翻倍、每条入站起一个 claude 进程，否决。⛔ 本条是结论。

### D2 · 一人多封在途 ⇒ 拒标＋告警、不起活（答 Q2a）

命中 ≥2 行在途时：台账不变、无信号、无起活；`alerts.py` 发一条告警列出涉及编号；审计 `bridge_refused_serial_violation`。

**为什么**：串行原则在 HR 侧无机器闸（F4），违规可能真的发生；桥若「取最新一封」会在回件其实答的是早一封时认错行，且错误无症状。fail-closed 把台账违规当场暴露，人工归属后（把多余那封手工转闭环或作废）下一条回件即恢复自动。⛔ 本条是结论。

### D3 · 「回件到了」＝该发送人任何名单内入站（答 Q7a）

在途信存在时，不看内容、不看附件、不看群/私信，一律标第九态并起活。拆件会话判「非实质回件」（寒暄、误发）时**按后缀里的原状态还原**并登记原因。

**为什么**：照搬 win 的串行原则免显式关联；零漏。「原状态原样接在后面」这个设计正是为还原而存在。**替代方案**「仅带附件」会漏掉她在群里直接文字回答决策点的情形；「仅私信」要维护 chatid 名单且她尚未私信过机器人。

### D4 · 权限层：`acceptEdits` ＋ `allowedTools` 白名单，⛔ 不用 `--dangerously-skip-permissions`（答 Q3a）

起活 argv（`dispatch.py` 单点常量 `HEADLESS_ARGV_TEMPLATE`）：

```
claude -p --output-format text --permission-mode acceptEdits
       --allowedTools Read Edit Write Glob Grep
                      "Bash(git add:*)" "Bash(git commit:*)" "Bash(git status:*)" "Bash(git diff:*)" "Bash(git log:*)"
                      "Bash(python -m tools.liaison unpack-signal:*)" "Bash(python -m tools.liaison criteria:*)"
       --max-budget-usd $HR_LIAISON_UNPACK_BUDGET_USD
```

cwd＝仓库根；prompt 走 stdin；stdout/stderr → `data/liaison/logs/unpack-headless/<UTC戳>.log`。

**为什么**：非交互模式下未放行的工具调用直接被拒 ⇒ `send-followup`（任何模式）、`git push`、`python -m tools.liaison` 的其它子命令（含会开库的 `queue`／`retention`）在**权限层**就走不通，不依赖章程文字。**已知边界**：`acceptEdits` 对文件编辑不按路径限制 ⇒ 红线 ②④⑥⑧（改代码／spec／白名单／skills）仍是提示词层守，见 Risks。**替代方案**照搬 win 的 `--dangerously-skip-permissions`：一次提示词失守＝可能对外发信，否决。

### D5 · 拆件会话在主工作区跑（答 Q4a）

不建 worktree。库隔离靠 D4（会话打不开库）；文件隔离靠章程 §〇 ⑤ ＋ 信号只经 CLI。

**为什么**：桥在主工作区的值守进程里写第九态；会话若在 worktree 改台账副本，两边必然分叉（F8，`0909AD` 实证）。**替代方案** worktree 要额外设计「合并回主工作区」路径且桥写的那行必冲突，否决。

### D6 · 消息事件接线归 `hr-wecom-aibot-liaison`，本包只列前置（答 Q5a）

本包 tasks §0 以「核验」形式列门槛：`liaison_message` 已有一条**真实**入站行。接线本身（订 SDK `message` 事件、回调只往队列放帧、值守线程调 `handle_inbound_message`、删 `test_this_chapter_wires_no_message_handling`）由该包追加任务 8.5bis 完成——**本 session 只登记待派发，⛔ 不改它的 `tasks.md`**。

### D7 · git 收口：只 `add` 列出路径＋commit，不 push（答 Q6a）

章程收口段逐字内嵌 CLAUDE.md 并行四条规则；`git push` 不在 D4 白名单，权限层双保险。

### D8 · 载体不换（findings §二 🔴）

`liaison_task` 保持 DB；台账保持 markdown；⛔ 不引入 markdown 队列、编辑锁、hook。信号是 JSON 文件（与 win 同形，因为它本来就只是"有没有活"的旗子，不是数据）。

### D9 · 第九态文案与时刻口径

状态列写成（一行）：

```
📨 回件已到，待拆件 2026-09-10 14:03 CST（值守服务自动标记，入信归档 `data/liaison/archive/<thread>/<yyyymmdd>/<leaf>`；仍属在途、串行闸仍锁，拆件回灌后须转闭环四态之一） ━━━ 原状态 ━━━ ✅ 已推送 2026-09-09
```

- 时刻用 **CST（Asia/Shanghai）**，与台账其它列的日期口径一致（win 用 UTC，我们不跟——台账是给人看的）。
- 归档路径写**仓库相对路径**，⛔ 不写绝对路径（worktree 与主工作区路径不同）。
- 幂等判据：命中行已含 `📨 回件已到，待拆件` ⇒ 不再改写（沿用 `followup.py` 的 `_ALREADY_SENT` 手法）。

### D10 · 桥的执行位置与失败隔离

`__main__.py` 值守线程在 `handle_inbound_message(...)` 返回后调 `unpack.bridge.run_bridge(conn, result, ...)`，整个调用包在 `try/except Exception` 里：只记 ERROR ＋审计 `bridge_failed`，⛔ 不上抛。桥内部顺序：定位（纯函数 `compute_bridge_decision`）→ 台账写（原子：写临时文件后 `os.replace`）→ 审计 effect → 信号追加 → 起活。台账写失败 ⇒ 不落信号、不起活。

**为什么台账写在审计之前**：审计是「已标记」的记录，台账没写成就不该记「已标记」；而台账写成、审计写失败 ⇒ 重投时按 D9 幂等短路，审计 effect 会再试——方向安全（多一次空转，不丢标记）。

### D11 · 审计表与 effect 节点

新表 `liaison_unpack_audit(id, msgid, sender_userid, letter_number, kind, detail, at)`，append-only。`kind` 取值受 CHECK 约束：`bridge_marked` / `bridge_skipped_no_inflight` / `bridge_refused_serial_violation` / `bridge_skipped_already_marked` / `bridge_failed` / `signal_file_replaced` / `dispatch_started` / `dispatch_skipped_busy` / `dispatch_failed`。写入走 `effect_unpack_audit` 节点，幂等键 `{thread_id}:effect_unpack_audit:{msgid}:{kind}`（同一 msgid 同一 kind 只记一次，符合铁律 1 形态）。`assert_effect_log_identity` 对该表按 `(msgid, kind)` 恒等。

**为什么不只写日志**：验收要「按消息与结果计数」，日志被脱敏与轮转，数不准。

### D12 · 信号／锁／日志落位与 CLI

- `data/liaison/unpack-signal.json`：`{"pending":[{"letter_number","msgid","archived_path","at"}]}`；追加按 `msgid` 去重；损坏即替换＋审计 `signal_file_replaced`。
- `data/liaison/unpack-session.lock`：`{"pid","started_at","log"}`；判活 `os.kill(pid, 0)`——`ProcessLookupError`/`PermissionError`/任何异常 ⇒ 不存活。⚠️ pid 复用会误判「忙」一次，代价＝信号多等一条回件，可接受。
- `data/liaison/logs/unpack-headless/<UTC戳>.log`。
- 子命令 `unpack-signal --probe`（输出 `[SIGNAL]`/`[NO-SIGNAL]`）、`unpack-signal --clear --before <ISO时刻>`；`unpack-dispatch --dry-run`（打印 argv，不起）、`unpack-dispatch --force`（跳过并发守卫，仅供验收）。三条子命令 ⛔ 不 import `storage.db`（AST 测试守）。
- 二进制解析顺序：`HR_LIAISON_CLAUDE_BIN` → `shutil.which("claude")` → `~/.local/bin/claude`；都无 ⇒ `dispatch_failed(reason=binary_not_found)`。

### D13 · 章程落 `.claude/skills/liaison-unpack/SKILL.md`

单点常量 `charter.CHARTER_RELATIVE_PATH = ".claude/skills/liaison-unpack/SKILL.md"`。整文件（含 frontmatter）逐字拼在 prompt 结尾；前言由 `charter.compute_prompt(preamble_fields, charter_text)` 纯函数生成。

**为什么放 skills 不放 docs**：CLAUDE.md「规则真源在 `.claude/skills/`」；且人也能手动 `/liaison-unpack` 走一遍同一章程。红线 ⑧ 禁会话改它，与 D4 无冲突。⚠️ `claude -p` 在仓库根会自动加载 `CLAUDE.md`——这是**期望的**（并行四条、opener 格式等约束一并生效）。

### D14 · 口径点台账＝markdown ＋ CLI

`docs/跟进信/口径点台账.md` 表格；`criteria` 子命令：`--add --from 人事部#N --desc …`、`--id HR-G-NN --to 已回复|已签认|已作废 [--evidence …]`。`已签认` 缺 evidence ⇒ 退出码 3、文件不变。⛔ 无 `--auto`/`--expire`/`--before` 类参数（测试断言 argparse 无此类选项且源码无按时间改状态分支）。

## Risks / Trade-offs

- [`acceptEdits` 不按路径限制编辑 ⇒ 红线 ②④⑥⑧ 只在提示词层] → 章程收口段要求会话最后一步 `git status --porcelain` 自检，越界路径不 add、并在接力文档登记「自检发现越界编辑 <路径>，未提交、待人处理」；tasks 加一条「越界编辑不入提交」的测试（对章程文本）与验收观察项。⚠️ 这是已知缺口，⛔ 不宣称机器守。
- [launchd 环境下 `claude` 拿不到登录态 / PATH 无 claude] → D12 三级解析；登录态问题**只能真实起活实测**发现，tasks §5 强制一次真实起活并把日志前 20 行抄进验收记录。
- [值守线程里做文件 I/O ＋ Popen 拖慢存活戳] → 台账是 KB 级、Popen 非阻塞，毫秒级；看门狗阈值 180 秒，无风险。
- [桥与人工同时编辑台账] → 原子替换；人工那次会被覆盖或覆盖桥——两种都会被下一次对账发现（第九态存在但状态与事实不符）。不加锁是刻意的（Non-Goals）。
- [信号文件损坏替换丢旧项] → 旧项对应的回件仍在归档与队列里，下一条回件再触发；审计 `signal_file_replaced` 留痕。
- [pid 复用误判忙] → 见 D12，信号多等一条回件。
- [拆件会话读回件内容送往境外模型] → 章程 §〇 ⑨（D15，已定）；合规影响说明已列。
- [TD-42 未还时通道假死而无人知，桥永远收不到入站] → tasks §0 门槛；⛔ 不在本包内绕。

## Migration Plan

1. **前置门槛**（tasks §0）：TD-42 销账＋真实验证记录；消息接线（`hr-wecom-aibot-liaison` 8.5bis）完成且 `liaison_message` 有一条真实行。
2. P0→P1→P2→P3 按 tasks 顺序，每章一条 worktree 分支、独立可测可合。
3. 合回 main 后 **重启值守服务**（`launchctl kickstart -k gui/$UID/com.zhuopin.hr.liaison`，由 Shao Peishen 跑——重启会打断在线等回件的连接，属他的操作），观察 `launchd.err.log` 无新 ERROR。
4. **真实起活验收**（tasks §5）：等汤丽萍下一条真实入站，或用 `unpack-dispatch --force` 只验 P1；把审计表三条记录与无头日志前 20 行抄进验收记录。
5. **回滚**：`git revert` 本包 commit ＋ 重启服务；台账里已写的第九态行**手工**按后缀原状态还原（一行）；`data/liaison/unpack-*` 文件删除即可，无 schema 迁移回退需求（审计表 append-only、无外键指向它）。

### D15 · 章程 §〇 第 ⑨ 条：候选人个人信息（Shao Peishen 2026-09-10 答 1a，合规红线项）

章程 §〇 ⑨ 逐字：「归档件疑似含候选人个人信息（简历、身份证号、手机号等）⇒ ⛔ 不读入 prompt、不摘录，只登记并转『待人』栏。」判据＝文件名或首段显示为候选人简历／名单／证件。⛔ 本条是结论，不是待选项。

### D16 · 无头会话预算（答 2a）

`HR_LIAISON_UNPACK_BUDGET_USD` 默认 **5**，按 env 读，`.env.example` 注释里写明默认值。

## Open Questions

无。原两条（章程 ⑨ 措辞、预算默认值）已于 2026-09-10 由 Shao Peishen 答 `1a，2a` 落为 D15／D16。
