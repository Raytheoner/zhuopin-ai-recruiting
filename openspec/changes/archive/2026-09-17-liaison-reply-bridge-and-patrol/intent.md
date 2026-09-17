---
status: 已确认
场景: HR 值守通道 · 回件桥＋第九态＋打标即开班（win 端机制移植）
grill会话: "[Mac]0909AT"
grill日期: 2026-09-10（CST）
进入判例包的开放点数量: 0
---

# intent · 打标即开班需求收敛（grill 产出）

> 依据 `docs/findings/2026-09-10-win端打标即开班机制核验与HR移植方案.md`（win 端六处真身核验）。
> grill 规则正本＝企业AI转型仓库 `0-学习与工具/skills源码/zhuopin-需求grill/SKILL.md`（GitHub 只读取回，未复制）。
> 本文件是 `design.md` Decisions 的来源；三节固定：§一 M2 已自查的事实 ／ §二 已定 ／ §三 待专员。

## §一 · M2 已自查的事实（⛔ 这些没有问他，全部从仓库查得）

| # | 事实 | 出处 | 对设计的影响 |
|---|---|---|---|
| F1 | 🔴 **值守服务没有订阅 SDK 的 `message` / `message.*` 事件**：`session_client.SUBSCRIBED_EVENTS = (connected, disconnected, error)`；`__main__.py` 模块 docstring 明写「消息处理不在本文件里」，并由 `tests/test_main_wiring.py::test_this_chapter_wires_no_message_handling` 守着。`handle_inbound_message` 只有测试在调 | `tools/liaison/session_client.py:56-77`、`tools/liaison/__main__.py:1-20`、`grep -rln handle_inbound_message` | 即使 TD-42 还了、连接健康，`liaison_message` 仍是 0 行。**这是比 TD-42 更靠前的前置**，此前未登记在任何 task／TD 里（TD-42 把「十小时 0 行」全归因于假死，但接线缺失单独就足以造成 0 行） |
| F2 | SDK 真实发出的入站事件名：`message`、`message.text/.image/.mixed/.voice/.file`、`event` | `tools/liaison/.venv/.../aibot/message_handler.py:55-78` | 接线只需订 `message`（总事件） |
| F3 | 台账八态里**只有 `✅ 已推送` 是"等回件"的在途态**；`⏳/🆕/⏸` 尚未发出、不可能有回件；`📥/✅ 无需回复/📨/❌` 已闭环 | `docs/跟进信/README-跟进信清单.md`「发送状态」表 | 桥定位在途信 ＝ 收信人匹配 ∧ 状态以 `✅ 已推送` 开头（或已是第九态） |
| F4 | 串行原则在 HR 侧**无机器闸**，全靠起草时看表 | 同上「🔴 串行原则」段；`tools/liaison/` 无任何闸核代码 | 「一人多封在途」是**可能发生的台账违规**，桥必须有预案（→ Q2） |
| F5 | 发送人 `userid` → 中文姓名的映射已存在（`whitelist.yaml` 的 `name` 字段）；台账「收信人」列用中文姓名 | `tools/liaison/config/whitelist.yaml`、`whitelist.py` | 桥用 name 匹配收信人列，⛔ 不新建映射表 |
| F6 | HR 线只有一个群（人力AI保障组）、一条编号线（`人事部#N`）；Mac 端不向其它群发信 | README 抬头「收件面口径」 | 「跨部门／多专员」当前不存在；将来多专员时串行原则本就是按收信人各算各的，桥逻辑不变 |
| F7 | `followup.py::compute_backfilled_ledger` 已有「按编号定位行 → 只改命中行 → 去括注 → 写状态列」的纯函数；`_ALREADY_SENT` 命中即幂等短路 | `tools/liaison/followup.py:83-105` | 第九态回写复用同一手法：命中行已含第九态标记 ⇒ 短路 |
| F8 | `docs/跟进信/` 在版本库内 ⇒ **每个 worktree 都有一份台账副本**；`0909AD` 真发后主工作区台账未回填，正是改错副本 | `followup.py:176-186` 注释；`docs/session接力.md` ⑱ | 桥在主工作区的值守进程里写第九态 ⇒ 拆件会话若在 worktree 改台账必然分叉（→ Q4） |
| F9 | `run-lanes.sh` 已有无头起活范式：默认 `--permission-mode acceptEdits`，只有 `--full-auto` 才 `--dangerously-skip-permissions`；prompt 走 stdin `printf ... \| claude -p` | `docs/openers/run-lanes.sh:24,300,540-545` | 权限层红线是现成可选项，不必照搬 win 的纯提示词层（→ Q3） |
| F10 | `data/liaison.db` 是 WAL、单进程单连接、`busy_timeout=5000`；值守服务由 launchd 以 `WorkingDirectory=仓库根` 常驻 | `tools/liaison/storage/db.py:36-42`、`launchd/*.plist.template` | 拆件会话**不需要开库**：信号带 `msgid` ＋ 归档路径，归档文件只读即可 |
| F11 | `claude` CLI 在 `/Users/paulshao/.local/bin/claude`（2.1.263）；launchd 环境 PATH 未必含该目录 | `which claude` | 起活时二进制路径要**显式解析**（env 覆盖 → `shutil.which` → 已知路径），找不到 ＝ 四类失败之一 |
| F12 | `tools/liaison/` 非测试代码 ⛔ 不许 `conn.commit()`／`with X:`，提交由 `idempotent_effect` 独占；AST 测试守着 | `storage/db.py` docstring、`queue.py` docstring | 桥的审计行必须做成 `effect_*` 节点走 `idempotent_effect`，⛔ 不能裸 INSERT |
| F13 | HR 侧尚无口径点台账（win 端 `coverage_point_ledger`）；跟进信 `决策点:` 字段已必写 | README、findings §二表 | P3 从零建，载体沿用 markdown 表（与 README 台账同族） |
| F14 | TD-42（连接假死有判据无把手）由 `0909AS` 在还，触碰 `session_client.py`；本包触碰区与之零重叠 | `docs/tech-debt.md:1982`、`docs/openers/0909AS-*.md` | 列为前置，⛔ 不在本包内改 |
| F15 | 人事部#1 当前 `✅ 已推送 2026-09-09`，收信人汤丽萍 ⇒ **她的下一条名单内入站就会触发桥**（真实起活验收的天然场景） | README 清单表 | 真实起活验收可不造数据 |

## §二 · 已定（＝ design.md Decisions；Shao Peishen 2026-09-10 10:0x CST 于本 session 当场作答，八问全按推荐，⛔ 无一项靠超时默认生效）

| # | 问题 | 结论 | 理由摘要 |
|---|---|---|---|
| **Q1** | 名单内发送人来信，但该人**无** `✅ 已推送` 在途信 | **(a) 不标、不起活**；只走现状路径（归档＋入队），审计 `bridge_skipped_no_inflight` | 绝不会把新岗位需求误挂到某封信上；红线面最小。⛔ 起草者未代拍 |
| **Q2** | 同一收信人 **≥2 行** `✅ 已推送`（串行原则被破） | **(a) 拒标＋告警，不起活**（fail-closed）；审计 `bridge_refused_serial_violation`，走 `alerts.py` 发一条告警 | 绝不认错信；把台账违规当场暴露。⛔ 起草者未代拍 |
| **Q3** | 无头拆件会话的权限层 | **(a) `--permission-mode acceptEdits` ＋ `--allowedTools` 白名单**；章程 §〇 红线作第二层 | `send-followup`／`git push`／其它 `python -m tools.liaison` 子命令在**权限层**被拒，不靠提示词 |
| **Q3b** | 章程 §〇 红线清单 | **八项照单全收**：① 对外发送（`send-followup` 任何模式）② 建造（改 `tools/ app/ scripts/ tests/`）③ 新裁决 ④ 改 spec/design/tasks.md ⑤ 写 `data/liaison.db*`、`.env`（归档目录只读、信号只经 CLI）⑥ 改 `whitelist.yaml` ⑦ `git push`／任何 `.51` 动作 ⑧ 改 `CLAUDE.md` 与 `.claude/skills/` | 凡需新下判断一律转「待人」栏 |
| **Q4** | 拆件会话工作区 | **(a) 主工作区**；隔离靠 Q3 权限层（库打不开）＋章程红线＋信号只经 CLI | 与桥同一份台账，无副本分叉（F8） |
| **Q5** | SDK `message` 事件接线归属 | **(a) 归 `hr-wecom-aibot-liaison` 追加任务**（8.5bis），本包只列前置 | 8.6「自测归档／入队」本就隐含它；⚠️ 要删 `test_this_chapter_wires_no_message_handling` |
| **Q6** | 拆件会话 git 收口 | **(a) 只 `git add` 列出路径＋commit，⛔ 不 push**；并行四条规则内嵌章程 | 与 Q3b ⑦ 一致；回灌结论不以未提交状态堆积 |
| **Q7** | 在途信存在时何为「回件到了」 | **(a) 该发送人任何名单内入站**（照搬 win）；拆件会话判「非实质回件」则按后缀里的原状态还原 | 零漏；原状态接在后面正为此存在 |

起草者自定的设计取舍（不属"只有他能定"，落 design.md D8–D14，design 审时可驳）：载体不换、第九态文案、桥在值守线程内 `handle_inbound_message` 之后执行且异常不上抛、信号／锁／日志的落位、章程落 `.claude/skills/liaison-unpack/SKILL.md`、口径点台账为 markdown ＋ CLI、审计走 `effect_*`。

## §三 · 待专员（＝判例包待办）

**无。** 本场景的对手方是值守服务自己与 Shao Peishen，不涉及专员实操口径。汤丽萍侧行为完全不变（照原样在群／私信里发）。

## §四 · 收工三列指标（grill 规则 §六）

| 列 | 值 | 说明 |
|---|---|---|
| ① grill 前既有口径点 | 0 | HR 侧尚无口径点台账（F13） |
| ② grill 新发现＋归因 | **2**：F1「入站未接线」（归 ⓐ 与真身逐格对账）、「回件附件可能含候选人个人信息」（归 ⓑ 既有纪律：合规红线「简历数据不出境」）。**grill 问答本身净贡献 ＝ 0** | 两条都不是问出来的，是查出来的 |
| ③ 拦下数 | **8**（Q1–Q7 全部由场景发起人内部拍板，未打包发给专员） | 本机制价值主张所在 |

## §五 · 原开放项（2026-09-10 10:3x Shao Peishen 答 `1a，2a`，已关闭）

1. ✅ 章程 §〇 ⑨ 候选人个人信息条：**按起草措辞定稿**（design D15）。
2. ✅ `HR_LIAISON_UNPACK_BUDGET_USD` 默认 **5**（design D16）。
