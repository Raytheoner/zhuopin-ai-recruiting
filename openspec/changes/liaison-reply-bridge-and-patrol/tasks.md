> **进度**：0/33。立包 2026-09-10（`[Mac]0909AT`）。需求树见 `intent.md`，裁决 Q1–Q7 见 `design.md` D1–D7。
> 🔴 **§0 三条门槛全部勾完之前，§1–§5 ⛔ 不得开工**——「打标即开班」建在一条会假死且看不出来（TD-42）、且根本收不到消息（F1 接线缺失）的通道上，是在为一个不存在的入站做自动化。
> 🔴 **验收纪律**：§5 的真实起活实测记录是本包的验收标准，⛔ 单测全绿不算（win 端同族纪律，`docs/findings/2026-09-10-win端打标即开班机制核验与HR移植方案.md` §五）。
> 粒度：每个 `##` 章节 ＝ 一个 superpowers plan ＝ 一条 worktree 分支。每份 plan 必须含 Global Constraints 段（CLAUDE.md「工程铁律」逐字）。

## 0. 前置门槛（⛔ 本包不实现，只核验；三条都勾才开工）

- [ ] 0.1 核验 **TD-42 已销账且有真实验证记录**：`docs/tech-debt.md` 里 `~~TD-42~~ ✅ 已还（<commit>）` 存在，且销账行引用了一次真实的「假死→自终止→launchd 拉起」实测（⛔ 单测不算）。未满足 ⇒ 本包停在此处，登记接力文档
- [ ] 0.2 核验 **SDK `message` 事件接线已完成**（`hr-wecom-aibot-liaison` 追加任务 8.5bis，design D6）：`session_client.SUBSCRIBED_EVENTS` 含 `message`；`__main__.py` 值守线程调 `handle_inbound_message`；`test_this_chapter_wires_no_message_handling` 已删。未满足 ⇒ 停，登记待派发
- [ ] 0.3 核验 **一条真实入站已落库**：`data/liaison.db` 的 `liaison_message` ≥1 行、来源为真实企微消息（⛔ 不是测试库）。这是 F1「接线缺失单独就足以造成 0 行」的反证

## 1. P0 · 回件桥＋第九态（`liaison-reply-bridge`）

- [ ] 1.1 `tools/liaison/unpack/__init__.py` 与 `unpack/bridge.py` 骨架；纯函数 `compute_bridge_decision(ledger_text, *, sender_name, msgid, archived_relpath, now) -> BridgeDecision`：按 intent F3 判据定位在途行（收信人＝`sender_name` ∧ 状态以 `✅ 已推送` 开头或已含第九态标记）；返回 `marked / skipped_no_inflight / refused_serial_violation / skipped_already_marked` 四态之一与新台账文本。⛔ 不读文件、不读时钟、不记日志
- [ ] 1.2 第九态文案生成 `compute_ninth_state_cell(original_cell, *, archived_relpath, now_cst)`（design D9）：CST 时刻、仓库相对路径、`━━━ 原状态 ━━━` ＋原状态列完整原文；单测逐字断言，并断言「只重写命中行、其它行逐字节相同」（复用 `followup.py` 的行定位手法，⛔ 不复制那段代码——抽成共用或直接 import）
- [ ] 1.3 单测四态：恰一封在途 ⇒ `marked`；无在途 ⇒ `skipped_no_inflight`（D1）；≥2 封 ⇒ `refused_serial_violation` 且返回涉及编号列表（D2）；已是第九态 ⇒ `skipped_already_marked` 且台账不变（幂等）。🔴 D1/D2 两条要先红后绿
- [ ] 1.4 台账原子写 `write_ledger_atomic(path, text)`：临时文件 ＋ `os.replace`；写失败抛出由调用方转 `bridge_failed`。⛔ 不用 `with`（AST 守卫），用 `try/finally` 关句柄
- [ ] 1.5 `storage/schema.py` 加 `liaison_unpack_audit` 表（design D11，`kind` CHECK 九值，append-only）；`storage/effects.py` 加 `effect_unpack_audit`（`@idempotent_effect`，键 `{thread_id}:effect_unpack_audit:{msgid}:{kind}`）。**幂等策略**：同 msgid 同 kind 只落一行；`assert_effect_log_identity` 对该表按 `(msgid, kind)` 恒等并加测试
- [ ] 1.6 `bridge.run_bridge(conn, *, inbound_result, sender_name, ledger_path, signal_path, now, dispatch)`：顺序＝决策 → 台账写 → 审计 effect →（`marked`/`skipped_already_marked` 时）信号追加 → 起活；整体 `try/except Exception` 只记 ERROR ＋ `bridge_failed` 审计，⛔ 不上抛（D10）。`refused_serial_violation` 走 `alerts.py` 发告警（内容含涉及编号）。**幂等策略**：台账按 D9 标记短路；审计按 1.5；信号按 msgid 去重
- [ ] 1.7 `__main__.py` 值守线程接线：`handle_inbound_message` 返回且 `route.admitted` 为真后调 `run_bridge`；发送人姓名取自 `whitelist` 加载结果的 `name`；台账路径由单点常量 `LEDGER_PATH = REPO_ROOT / "docs/跟进信/README-跟进信清单.md"` 解析。⚠️ 名单外消息 ⛔ 不调桥（spec）
- [ ] 1.8 单测：桥抛异常时归档与队列行保持已提交、链路正常返回、`bridge_failed` 一条；台账不可写 ⇒ 无信号无起活；同 msgid 重投 ⇒ 台账、信号、审计各不重复；第九态期间新 msgid ⇒ 台账不变、信号 +1、起活被调
- [ ] 1.9 `docs/跟进信/README-跟进信清单.md`「发送状态」表加第九态一行（含义：回件已到待拆件，**仍在途、串行闸仍锁**；怎么变过来：值守服务自动写；怎么出去：拆件回灌后转闭环四态之一，或判非实质回件按后缀原状态还原）。⛔ 只加这一行与串行原则段一句「第九态视同在途」，不动清单表

## 2. P1 · 信号与打标即开班（`liaison-unpack-dispatch`）

- [ ] 2.1 `unpack/signal.py`：`append_signal(path, item)`（按 `msgid` 去重；文件缺失/损坏 ⇒ 新文件替换并返回 `replaced=True` 供调用方审计 `signal_file_replaced`）、`probe_signal(path) -> bool`、`clear_signal_before(path, checkpoint)`（只清 `at < checkpoint`）。纯文件操作，⛔ 不 import `storage.db`
- [ ] 2.2 单测 signal：去重、损坏替换、`--before` 只清检查点前保留其后项、空文件视同无信号
- [ ] 2.3 `unpack/dispatch.py`：锁文件读写、`compute_is_alive(pid) -> bool`（`os.kill(pid, 0)`；`ProcessLookupError`/`PermissionError`/任何异常 ⇒ `False`）、`compute_is_busy(lock_text, is_alive)`（坏锁/缺字段 ⇒ 不忙）；单测覆盖 D12 四种形状（活/死/坏锁/判活抛异常）
- [ ] 2.4 `dispatch.resolve_claude_bin(env)`：`HR_LIAISON_CLAUDE_BIN` → `shutil.which` → `~/.local/bin/claude`；`build_headless_argv(bin, budget)`：单点常量 `HEADLESS_ARGV_TEMPLATE`（design D4）。单测断言 argv 含 `-p`、`--output-format text`、`--permission-mode acceptEdits`、`--max-budget-usd`，⛔ 不含 `--dangerously-skip-permissions`，allowedTools 不含 `send-followup`/`git push`
- [ ] 2.5 `dispatch.dispatch_headless_unpack(*, charter_text, prompt, log_dir, lock_path, env, now, popen=subprocess.Popen) -> DispatchOutcome`：忙 ⇒ `skipped_busy`；否则建日志文件 → 写锁 → 非阻塞 `Popen`（stdin 传 prompt 后关闭，⛔ 不 `wait`）→ `started`。四类失败（章程读不到／日志建不了／Popen 抛／兜底）各转 `failed(reason)`，⛔ 不上抛，⛔ 本函数不读写信号文件（spec）。**幂等策略**：本函数无业务写；审计由调用方按 msgid+kind 幂等
- [ ] 2.6 单测 dispatch：四类失败各一条（注入抛异常的 `popen`/不可写 `log_dir`/缺章程），每条断言不上抛且返回原因；忙 ⇒ `skipped_busy` 且未调 `popen`；🔴 「真实 `Popen`／真实 `os.kill` 一律不在单测里调用」写进测试文件文首（win 端同族纪律）
- [ ] 2.7 把 dispatch 接进 `bridge.run_bridge`（1.6 的 `dispatch` 注入位）：审计 `dispatch_started / dispatch_skipped_busy / dispatch_failed` 各走 1.5 的 effect；审计写失败本身吞掉只记日志
- [ ] 2.8 `__main__.py` 子命令 `unpack-signal --probe|--clear --before <ISO>` 与 `unpack-dispatch --dry-run|--force`；AST 测试：这两条子命令的模块 ⛔ 不 import `tools.liaison.storage.db`（spec「子命令不碰库」）
- [ ] 2.9 `tools/liaison/.env.example` 加 `HR_LIAISON_CLAUDE_BIN`（可选）与 `HR_LIAISON_UNPACK_BUDGET_USD`（注释写明默认 5，design D16），只写变量名与说明，⛔ 无凭据取值；`test_liaison_boundaries` 的 plist/凭据守卫不受影响（回归跑一遍）

## 3. P2 · 拆件章程正本（`liaison-unpack-charter`）

- [ ] 3.1 写 `.claude/skills/liaison-unpack/SKILL.md`（章程正本）：§〇 红线九项（八项照单 Q3b ＋ 第 ⑨ 候选人个人信息条，措辞逐字取 design D15）；§一 信号探测与循环（探测异常按有信号、清信号在落批之后且只清检查点前）；§二 拆件步骤（读信号 → 读归档件 → 判实质/非实质 → 回灌结论落 `docs/跟进信/回件/<信编号>-<日期>.md` → 台账转态或按后缀原状态还原 → 口径点台账转态 → 登记 `docs/session接力.md`）；§三 收口（只 add 列出路径＋commit、不 push、并行四条逐字、`git status --porcelain` 越界自检、`.git/index.lock` 不删）；§四 待人栏。⛔ 章程内不出现任何凭据、不出现绝对路径
- [ ] 3.2 `unpack/charter.py`：单点常量 `CHARTER_RELATIVE_PATH`、`read_charter(repo_root) -> str`（缺失抛 `CharterMissing`，由 dispatch 转 `failed(charter_missing)`）、纯函数 `compute_prompt(*, letter_number, msgid, signal_relpath, checkpoint_iso, charter_text) -> str`：前言（含「走完一轮再探一次，直到 `[NO-SIGNAL]`」这条只在前言的规则）＋ 章程全文
- [ ] 3.3 单测 `test_事件驱动前言不改写章程原文`：prompt 以章程全文逐字结尾、其前有非空前言、前言含编号/msgid/检查点；`test_代码里无章程副本`：扫 `tools/liaison/**/*.py` 不含 §〇 任一整句；`test_章程红线九项可核对`：九项关键短语各命中 ≥1；`test_章程收口三条可核对`（不 push／只 add 列出路径／不删 index.lock）
- [ ] 3.4 章程与 `allowedTools`（D4）交叉核对测试：章程 §二 里要求会话执行的每条命令，都能被 `HEADLESS_ARGV_TEMPLATE` 的白名单放行（否则会话会卡住而不报错）；反向：白名单放行的每条命令都在章程里有用途

## 4. P3 · 口径点台账（`liaison-criteria-ledger`）

- [ ] 4.1 建 `docs/跟进信/口径点台账.md`：表头 `口径点ID | 来源信 | 描述 | 状态 | evidence | 更新`；抬头写四态语义与「`已签认` 必须带 evidence、⛔ 无超期自动签认」；把人事部#1 的决策点 a 登记为 `HR-G-01`（状态 `待专员`，evidence 空）
- [ ] 4.2 `unpack/criteria.py` ＋ `__main__.py` 子命令 `criteria --add --from <信编号> --desc <…>` / `criteria --id HR-G-NN --to 已回复|已签认|已作废 [--evidence <…>]`：纯函数 `compute_criteria_transition(text, *, id, to, evidence) -> (new_text, changed)`；`已签认` 缺 evidence ⇒ 退出码 3、文件不变。⛔ 不 import `storage.db`
- [ ] 4.3 单测：缺 evidence 签认 ⇒ exit 3 且文件逐字节不变；带 evidence ⇒ 状态与 evidence 列正确；ID 连续唯一；argparse ⛔ 无 `--auto`/`--expire`/`--before`/`--older-than` 类选项；源码 AST 无按时间改写状态为 `已签认` 的分支（测试用「不存在 `datetime`/`time` 参与状态判断」的形状断言）

## 5. 验收 · 真实起活实测（⛔ 单测不算）

- [ ] 5.1 合回 main 后由 Shao Peishen 重启值守服务（`launchctl kickstart -k gui/$UID/com.zhuopin.hr.liaison`）；核 `launchd.err.log` 无新 ERROR（冷启动那两条已知噪声除外）
- [ ] 5.2 **P1 单独验**：`python -m tools.liaison unpack-dispatch --dry-run` 打印 argv 与解析到的 `claude` 路径；`--force` 真起一次；判据＝`data/liaison/unpack-session.lock` 有 pid 且 `ps -p <pid>` 可见、无头日志前 20 行显示会话已读到章程并输出 `[NO-SIGNAL]` 后结束。🔴 若日志显示登录态/权限问题（launchd 环境），登记为阻断项，⛔ 不改成 `--dangerously-skip-permissions` 绕过
- [ ] 5.3 **P0+P1 端到端验**：等汤丽萍下一条真实入站（人事部#1 在途）——判据＝台账该行进第九态且原状态接后；`liaison_unpack_audit` 有 `bridge_marked` 与 `dispatch_started` 各一条；信号文件有一项；无头日志显示会话走完一轮并 `--clear --before` 清了信号；`git log` 有会话的 commit 且 ⛔ 未 push
- [ ] 5.4 **D1 反证验**：邵培申自己发一条消息（他名下无在途信）——判据＝台账不变、`bridge_skipped_no_inflight` 一条、无起活
- [ ] 5.5 把 5.2–5.4 的实测记录（命令、时刻、审计表三条 SELECT 输出、日志前 20 行）落 `docs/findings/2026-MM-DD-打标即开班真实起活实测.md`；全部通过后 **当场**跑 `openspec-archive-change`（CLAUDE.md「归档时限」）

**验收**：§0 三门槛有核验记录；§1–§4 单测全绿且 D1/D2/四类失败/缺 evidence 五条**先红后绿**；§5 真实起活实测记录已落档；章程 §〇 ⑨ 与 design D15 逐字一致。
