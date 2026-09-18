# Session 接力 · HR 招聘智能体

> 滚动更新，覆盖旧版。新会话读完本文即可接上。
> 最后更新：2026-09-17 06:2x（Cowork·0917D：R-1 实核已完成；体积闸归档接力 61→41 KB、编排 65→12 KB；R-2 拆成 `0917G`→`0917H` 经 launchd 发车（首发 E/F 作废）。此前：Cowork·HR业务线-接力0917D 转场：把 `0916E`–`0917B` 这一大段
> **从未进过本文**的进展补齐——第十七批波次 1＋2 已基本完成、Token 治理线已结项；
> 订正 `tasks.md` 抬头进度行失真 24/33 → 27/33）

---

## 🔴 新 session 先看这一节（2026-09-18 08:5x，Cowork·HR业务线-接力0917D 换道前实核）

⚠️ 本节之后的历史节一律以本节为准。**推进依据已从周排期改为任务驱动**：`docs/roadmap/任务驱动上线路线图.md`。

### 一、机制已就位（09-17 一天建成，全部已合 main）

- **任务驱动调度器**：`com.zhuopin.hr.task-dispatcher`（launchd，WatchPaths `.claude/handoff/events/` ＋ 每日 09:00 兜底）。skill `.claude/skills/task-dispatcher/`；台账 `docs/roadmap/任务台账.yaml`；定夺队列 `docs/roadmap/定夺队列.md`。
- **在环闸门 G1–G5**（`scripts/gates.py`，口径见 `docs/roadmap/任务驱动workflow设计.md` 末节）：G1 intent 定稿／G2 design·spec 定稿／G3 发布／G4 发信／G5 口径签认。放行只认定夺队列该行「状态＝已答」且答复为放行字（定／发／签；G4 须「审核通过」＋「发」）。⛔ 任何无头会话不得越闸、不得自己改台账状态放行。
- **动作通道** `.claude/handoff/commit/<ts>.action`：`send-followup`（双闸：台账 🆕 待发 ＋ G4 放行）、`install-agent`、`kickstart-liaison`；提交通道 `<ts>.request`（白名单 `docs/**`、`openspec/changes/*/tasks.md`，跑体积闸与全量 doc size 测试）。
- **发车**：`.claude/handoff/launch/<ts>.request` 一行参数；发车器占用时自动入队 `launch/queue/`。
- **`.51` 无头可达**：Mac 常驻内网，launchd 起的会话可免交互 ssh `zp51`（`docs/findings/2026-09-17-无头会话51可达性探测.md`）。09-17 已用它完成两次发版：现网 = `f81cc9d`，VC++ 升到 v14.44（torch／BGE-M3 在 `.51` 可用，单份 ~117 ms），每日快照计划任务 `ZhuopinDailySnapshot` 已装。
- **Cowork 侧红线（09-17 踩过）**：⛔ Cowork 不在仓库跑任何 `git` 命令（会留下删不掉的 `.git/index.lock`，卡住全部自动提交）；一切提交走提交请求通道。
- **受限会话投递中继**（0918H）：`.claude/**` 对远端文件工具只读，三条通道投递口都在其下 ⇒ 没本机 shell 的受限会话投不进去；破法是仓库根 `handoff-inbox/`（受限会话能写）＋ `com.zhuopin.hr.handoff-relay`（launchd，WatchPaths＋300s 兜底）跑 `scripts/handoff_relay.py` 按前缀转投三条通道，只搬运不放行。前缀→通道对照与五条校验见 `docs/roadmap/任务驱动workflow设计.md` §四。

### 二、业务真身

| 场景 | 状态 |
|---|---|
| M1 需求解析与岗位画像 | 已在 `.51` 演示环境；两包 G2 已放行（Q-22/23），剩 9.1 画像质量验收等人事部 10 个历史岗位 |
| M2 简历解析与评分排序 | U0 定型（抽取模型＝`deepseek-flash`）；U1 数据模型 Segment A 已合 main，Segment B＋C 已合 main（`0918B`／`66e3b97`，3092 passed·0 失败，2.4–2.8＋8.1 已勾）；已按「人事部可见优先」切出 **U2.5 可见薄片**（上传页＋解析结果列表＋逐字段 evidence 高亮＋校对，章 9，5 条任务），G2 已放行（Q-30）。**G3「M2 可见薄片」已发版 `.51`**（`0918AM` 无头，发版 commit `67772ba`，现网原 `f81cc9d`→新 `67772ba`；前置①②④全过，全量 pytest 3456 passed·0 failed；五阶段快照→依赖→sync→G-e→冒烟全过未回滚；入库闸发版前后复验均关闭；携带项 U3 硬门槛引擎／语音面试包 m3-prep 等逐项核验不改变现网行为；执行记录 `docs/releases/2026-09-18-发版三-M2可见薄片执行记录.md`） |
| M3 语音面试 | intent 定稿（Q-32/37）；包 `voice-structured-interview` 立完、G2 已放行（Q-46，75 条任务）。顺序：U0 探针 → U1 数据模型 → U2 出题 → U3 邀约同意 → U5 评分 → U4 实时语音 → U6 视图 → U7 合规开闸。🔴 语音主机规格与预算待探针实测后单独定夺 |
| 后半程四场景 | `channel-resume-intake`／`interview-scheduling`／`offer-generation`／`onboarding-flow` 已立包，G2 **按 Shao Peishen 批注暂缓**：等 `人事部#3` 回件补齐现用流程与样例后回闸 |
| 合规验收 #1 | 三份草稿在 `docs/compliance/`；法务对接人 **谷雨**；G5 暂缓，待她审阅后再签 |
| 跟进信 | `人事部#3`（需求讨论会结论回收单）09-17 22:19 已推送，在途；`人事部#2` 已作废并入 #3。两份 Word 讨论稿在 `docs/人事部讨论/` |

### 三、下一步（新 session 直接接着做）

1. 读 `docs/roadmap/定夺队列.md`「一、待答」——回本线时先报待答项。当前待答只剩外部输入类：Q-01 私信附件测试帧、Q-04 人事部#3 回件、Q-08 `.51` 冒烟、Q-10 群 webhook、Q-16 门户导航。
2. ✅ `0918B`（M2 U1 Segment B/C）已收敛合 main。当前：`0918E`（M2·U2 上传与解析管线出实现计划）在 `launch/queue/` 等 `0918F` 收敛后自动发车 ⇒ 调度器自动接 U2 建造 → U2.5 ⇒ 满足预授权条件即发版 ⇒ 在本线报结果。
3. 汤丽萍回件到达 ⇒ 值守服务自动归档、拆件回灌 ⇒ 结论回填需求基线 ⇒ 后半程四场景 G2 回闸问 Shao Peishen。
4. 口令「看看泳道」＝读泳道 results、调度器日志、定夺队列，逐条报。节奏与设备口径见 `docs/roadmap/一天标准工作流程.md`。

### 四、Cowork 用量口径与换道提醒（09-18 实测＋Shao Peishen 定）

一整天单会话折算约 2050 万 token：43% 花在反复重读对话历史、38% 新内容入缓存、14% 每轮重发的说明书与工具清单、5% 输出。⇒ **长会话及时换道**；⛔ 少读截图（三张 Word 截图＝37 万字符）；同一目的的多条查询合并成一条命令。
🔴 **Cowork 主动提醒换道**（口径：`docs/roadmap/一天标准工作流程.md` 末节「换道提醒」）：跨过一个完整阶段、往返超约 50 轮、或历史明显偏长时，在回复末尾给一行提醒＋接力节位置＋可粘的开场词；⛔ 只说时机，不提额度。


## 开场词（复制即用）

```
[Mac]0918G-HR业务线接力
【设置】执行环境: Cowork ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（Cowork 无 worktree，只做文档、编排与派单）｜ 工作区: 仓库根（/Users/paulshao/Projects/HumanResource）
读 /Users/paulshao/Projects/HumanResource/docs/session接力.md 的「🔴 新 session 先看这一节（2026-09-18 08:5x）」恢复上下文，然后按【三、下一步】继续。
```

> 🔴 **Cowork 接力开场词同样是 opener，头两行＝标题行 + 【设置】行**（CLAUDE.md「CC 与 Cowork 两端同等适用」；09-09 Shao Peishen 指出漏了一次）。本 session 派出去的 opener，`派发` 一律写 `Cowork·HR业务线-接力0909Q`。

> 🔒 **首行只用于给读文档的人对编号，不指望侧边栏**。Cowork 侧的 session 名是摘要生成的，
> 首行无效（08-27 实测：`HR业务线-接力0827B` → 侧边栏 `HR业务线接力`）。⛔ 不要再改首行格式硬试。
>
> 🔴 **出号前必查号池台账**：`docs/openers/OP-0820-全量编排.md` 顶部「🔢 号池台账」。
> **Cowork 与 CC 共用同一号池**，`Z` 固定留给看护者。08-27 一天撞号 5 次，根因是
> 提交类 opener 只在聊天里派、从不落档 ⇒ 下轮 grep 不到 ⇒ 重派。**给出后当场登记**。

---

## 二、下一步

### 🆕 2026-09-18 `0918AB` reportlab／Pillow 依赖登记核查——早已登记，无需改动；顺带定位到 main 当前测试红

派车前提（`0918U`：main 全量 pytest 4 failed，判「reportlab／Pillow 未登记进任何依赖清单」）已过期：**两包早在 2026-09-17 M2·U0（`6e93eb6`）就已登记进 `requirements-m2-u0.txt`**（标「轻依赖（单测需要）」，版本与共享 venv 已装版本一致），`tests/test_extract_text.py`／`tests/test_compare_models_m2.py` 现状 23 passed，无需任何改动。

`0918AB` 全量 pytest 实测（main `8e4574f`＝本分支 HEAD，`git rev-list --count main...HEAD`＝0）：**5 failed**（非 opener 预期的 4 条，且均与 reportlab/Pillow 无关）——1 条真回归 `tests/test_effect_idempotency_suite.py::test_manifest_matches_the_source_tree`（`EFFECT_NODE_MANIFEST` 漏登 M2U3 合并带入的 `effect_persist_flags`）；4 条环境缺口 `tests/test_probe_m3_voice.py`（`funasr`／`livekit` 未装，`requirements-m3-u0.txt` 未装进共享 venv）。按 opener 预案「真回归⇒立刻停，不顺手改业务代码」，本条到此为止，两项登记待派发：

- 【谁做】M2U3 后续 lane／下一次 task-dispatcher 唤醒｜【状态】待派发｜【判据】`EFFECT_NODE_MANIFEST` 补上 `effect_persist_flags` ＋ `build_recipes()` 加对应崩溃-恢复配方，该测试转绿｜【不做会怎样】铁律1（每个 effect_* 节点须被强制中断验证过）对新节点失去覆盖，且全量 pytest 持续红，掩盖后续真回归信号
- 【谁做】M3 U0/U1 建造 lane 开工前｜【状态】待派发｜【判据】`requirements-m3-u0.txt` 装进共享 venv，或测试改 `pytest.importorskip` 使懒加载生效，4 条转绿或规范 skip｜【不做会怎样】全量 pytest 持续非 0 failed，同样掩盖真回归信号

### 🆕 2026-09-18 `0918B` M2·U1 数据模型 Segment B＋C 已合 main（`66e3b97`，3092 passed，2.4–2.8＋8.1 已勾）
- 【谁做】U2／U4 接手者【状态】9 条 Minor 延后【判据】见 `docs/findings/2026-09-18-0918B-M2U1-SegmentBC收口.md`「终审遗留」逐条关闭【不做会怎样】U2 写 `accessor` 与 U7 审计口径可能不一致；U4 落库前不校验 evidence_ref 偏移会存脏回指

### 🆕 2026-09-18 S-M3 立包 `voice-structured-interview`（G1 Q-37 放行后，无头）〔原派车号 `0918B`，因号池体积闸登记被撤回（Q-43），该号已归 M2U1 建造；本条不占号〕
- 包已立并过 `openspec validate --strict`：6 能力（`interview-prep-question-engine`／`interview-invite-and-consent`／`live-voice-interview-session`／`interview-scorecard`／`interview-recording-retention`／`m3-compliance-assertions`）／tasks 76 条（0.1 R-9 已勾，1/76）／🔴 7／design Open Questions **11**（OQ-1–4 待专员 `HR-G-NN` 四条、OQ-5 X5 探针、OQ-6 合规验收 #2、OQ-7 X6、OQ-8 对外通道、OQ-9 语音主机采购、OQ-10 短信通道与验证码门禁口径、OQ-11 内部模拟录音留存）。路线图 §二 M3 行改「已立包 1/76」。commit hash＝本行所在提交（`git log --oneline -1 -- openspec/changes/voice-structured-interview/proposal.md`）
- 【谁做】task-dispatcher【状态】待 G2（Open Questions 全部是待专员＋外部依赖原样转入，不阻塞 spec-to-plan；U0 探针与 U1–U3、U5 不依赖任何 OQ）【判据】Shao Peishen 在定夺队列对本包 G2 答「定」后派 spec-to-plan（从 U0 探针起）【不做会怎样】M3 停在 propose；X5 探针继续空等
- ⚠️ **重复派发实证**：`0918B` 同一 opener 在 `4299785` 合入后又被无头起了一次（本行所在提交），后者开工自检发现包已在 main、四项交付物全在，按「让位给进度靠前的」规则未重跑、未覆盖，只登记本行。【谁做】task-dispatcher【状态】待查【判据】调度器派发前对号池台账查「已完成」标记并跳过【不做会怎样】每次重复派发白烧一份预算，且未跟踪产出可能互相冲掉
- ⚠️ 本文件追加后 ≈55 KB，闸 58 KB（`tests/test_doc_size_budget.py` 现值，5 passed）——余量 < 3 KB，下一条追加前宜先打「【已闭环】」跑 `scripts/archive_docs.py --apply`

### 🆕 2026-09-17 `0917BF` 四场景立包完成（G1 Q-33–36 放行后，无头单条串行）
- 四包已立并过 `openspec validate --strict`：`interview-scheduling`（4 能力／42 条／🔴 6／OQ 8）、`offer-generation`（4 新能力＋`outbound-approval-gate` delta 增 `offer_letter`／40 条／🔴 4／OQ 7）、`onboarding-flow`（4 能力／34 条／🔴 6／OQ 7）、`channel-resume-intake`（4 能力／28 条／🔴 4／OQ 7）。四份 intent `status` 已改「已确认（G1 Q-xx）」。commit hash＝本行所在提交（`git log --oneline -1 -- openspec/changes/interview-scheduling/proposal.md`）
- 【谁做】task-dispatcher【状态】待 G2（四包 design 各留 7–8 条 Open Questions，全部是「待专员」＋外部依赖原样转入，不阻塞 spec-to-plan）【判据】Shao Peishen 在定夺队列对四包 G2 答「定」后派 spec-to-plan【不做会怎样】四包停在 propose，波次 5 无法开工。路线图 §二 无四场景行，按 opener 预案跳过不新建

### 🆕 2026-09-17 `0917Y` G3 泳道结果私信本人——代码已合，待重启值守服务后观察首条真发

裁决 1a（只私信本人、⛔ 不进群）已做成结构：`owner_notify_outbox` 发件箱 ＋ `python -m tools.liaison owner-notify` 入队 CLI ＋ 值守线程空闲 tick 消费（`owner_notify.py`）；`run-lanes.sh` 收敛后自动入队 `lanes-<STAMP>`。收件人只从 `config/whitelist.yaml` 按 `name == 邵培申` 解析，无收件人参数。发送口 = SDK `client.send_message(userid, markdown)`（1.0.2 表面只读核过，**未真发过**）。

- 【谁做】Paul（CC opener，落档提交类）｜【状态】⏸ 待做｜【判据】`launchctl kickstart` 重启值守服务后，下一批 run-lanes 收敛 ⇒ 企微单聊收到「泳道批次收敛」私信，且 `owner_notify_outbox` 该行 `sent_at` 非空、`attempts=0`｜【不做会怎样】旧进程没有消费者代码，发件箱只积不发、无任何症状
- 首条真发若 `attempts` 到 3 停发（看 `data/liaison/logs` 里「本人通知已停发」），最可能是单聊 chatid 口径或 markdown body 形状与真实 SDK 不符 ⇒ 按 `last_error` 修 `owner_notify.SdkSendPort`，⛔ 不改收件人解析

### 🆕 2026-09-16 `0916U` P2 拆件章程正本（liaison-unpack-charter）Task 4-5 建造收口

全 5 Task（Task1-3 上一 session、Task4-5 本 session）+ 终审 + 修复波 全部通过，已合入 main（合并
commit 见收工报告）。终审（Opus）放行「With fixes」，一波修复（`c5aebd4`：`compute_prompt` 前言里
残留的裸 `unpack-signal` 命令形＋`read_charter` 收窄的异常捕获）已修并复审通过。3 条遗留待办：

| # | 事项 | 谁做 | 状态 | 判据（怎样算完） | 不做会怎样 |
|---|---|---|---|---|---|
| 1 | `compute_prompt` 未接生产路径：`__main__.py`/`dispatch_wiring.py` 仍各自持有 `CHARTER_RELPATH` 字面量与 `_build_minimal_prompt`（两处都带 `P2-TODO` 注释指名本变更包收尾时删除） | 下一条 CC 泳道（建议单独起 opener，读 `docs/superpowers/plans/2026-09-10-liaison-unpack-charter.md` 与本条后追加一个 wiring task，走 TDD） | ✅ **已还**（`0917H`） | `grep -rn CHARTER_RELPATH tools/liaison/__main__.py tools/liaison/unpack/dispatch_wiring.py` 零命中 ＋ `bridge_dispatch` 实际调用 `charter.compute_prompt` ＋ 单测覆盖 | — |
| 2 | 章程 §二.6／`dispatch.py` 白名单的 `python -m tools.liaison criteria` 都是裸形式，且本机只有 `python3` 无 `python`；`criteria.py` 尚未交付（P3，`2026-09-10-liaison-criteria-ledger.md`） | P3 交付时顺带修（章程改 `PYTHONPATH=. tools/liaison/.venv/bin/python` 前缀形式 ＋ 同步改 `dispatch.py` 白名单，否则 Task5 的双向核对测试会红） | ✅ **已还**（`0917H`，即 TD-45 销账） | P3 落地后 `test_unpack_charter_allowlist.py` 两条测试仍绿 ＋ 章程与白名单用词一致 | — |
| 3 | `design.md` D4 行 52 仍写 `HEADLESS_ARGV_TEMPLATE`，代码实际常量名是 `HEADLESS_ARGV_FIXED_PART`（P1 交付时改的名，design 文档没跟着改） | 下次碰 design.md D4 的人顺手改一个词 | ✅ **已还**（`0917H`） | `grep -n HEADLESS_ARGV_TEMPLATE openspec/changes/liaison-reply-bridge-and-patrol/design.md` 零命中 | — |

SDD 台账（`.superpowers/sdd/2026-09-10-liaison-unpack-charter/progress.md`，worktree 内、gitignored）
有完整的终审全文摘要与逐条裁决理由，worktree 删除前已转写本条要点，原台账即将随收口清除。

### ⑱ 2026-09-09 晚·跟进信线收口（Cowork·0909Q）

- ✅ **人事部#1 已真发出**：`python -m tools.liaison send-followup --send` 发进「人力AI保障组」，markdown 正文 ＋ docx（39.4K）均到达（截图为证）。**HR 线跟进信第一次走通端到端自动发送，⛔ 今后不手工发**
- 🔴 **两次台账状态与事实不符，方向相反，两次都无症状**：
  ① `bf63e7a` 入库时提前写成 `✅ 已推送`，而信从未发出 ⇒ CLI 幂等保护认这一行，`--dry-run` 直接短路返回「已是终态」`EXIT=0`、正文一字不打印，真跑 `--send` 也会**一条不发却报成功**；已人工订正回 `🆕 待发` 才跑通
  ② 真发成功后**主工作区台账未被自动回填**（文件 mtime 仍是人工订正那次）；已按事实人工改成终态
  📌 **教训**：台账状态列是**机器判据不是记录**——写终态＝对下游宣布「这件事已发生」，写早了会让真正该发生的事再也不发生
- ⏸ **待查（`0909AD` 第 3 项验收未成立）**：回填为何没落到主工作区。判据＝那次 `--send` 命令的**最后一行有没有打印「已发送并回填台账」**；有 ⇒ 大概率在 worktree 里跑、改了副本（三个 worktree 里都有台账副本）；无 ⇒ 回填逻辑真缺陷。⛔ 未查清前不要把 `0909AD` 当已验收
- ⏸ **汤丽萍私信通道：Shao Peishen 2026-09-09 定「等明天她发信再接通」** ⇒ ⛔ 今天不再请她发任何消息（今天已让她白发 4 条：群 @ 三条 + 私信一条，全部因服务未在线而落空）。明天她发信后，从那条入站消息里读单聊 chatid
- ✅ **TD-39 已还并已真实复验**（`0909AH` 改码 + `0909AJ` 断网 245 秒复验，2026-09-09）。**8.6 的该项阻断解除**；G-4 装 launchd 的三项解锁条件已全部满足，⏸ 只等 Shao Peishen 本人在 Terminal 跑（⛔ 不可代）
- 🔢 号池：`0909` 双字母已用到 **AH**，下一个是 `AI`

### ⑰ 眼下手里的三件（2026-09-09 下午）

- ~~**`0909AA`**~~ ✅ **已跑完**（`8bd102b` / `e969f1b` / `a0a63b2`，均已推）：三凭据 len 35/43/89 齐、三条守卫全过；`tools/liaison/.venv` 建起（wecom-aibot-python-sdk 1.0.2 / pytest 8.3.4 / PyYAML 6.0.3）；表面探针与既有 findings **逐字节零差异 ⇒ 表面无变化**；首跑 4 red 已裁决清零，套件 **741 passed**。TD-19 **未销**（如期），但其阻塞理由已消失 ⇒ 转「已提上日程」，详见 TD-19 的「⏫ 2026-09-09 状态更新」段
- **汤丽萍欢迎信：✅ 草稿已出（md ＋ docx 双件），⏳ 待你审**。落位 `docs/跟进信/`：正文 `人事部-汤丽萍-跟进-2026-09-09-AI招聘值守机制启用与配合方式.md` ＋ 同名 docx ＋ 新建台账 `README-跟进信清单.md`。三项自检已跑：`决策点: 1 项（a 使用反馈的形式与节奏）` 在位、**全文无第三人称**（剔除「其他」后命中 0）、docx 里 frontmatter 未渗进正文。
  - 🔴 **口径变更（2026-09-09 Shao Peishen 定，此前的写法全部作废）**：**HR 项目不碰 Windows 侧那套**——zhuopin-ai 仓库的 README 主表、取号 CLI、串行闸 CLI、登记 CLI、PowerShell 发送通道**都不用**；HR 跟进信**自成一条编号线，从 `人事部#1` 起**（这封就是 #1）。⛔ 今后不要再写「待 Windows 侧取号／闸核／转 docx／登记」那四项
  - **沿用**（其他部门跑了两个月已验证）：§4 三要素信骨架、`决策点:` 字段必写、起草期代词自检、docx 必发、发送状态语义、串行原则。**替代**：取号与闸核看 `docs/跟进信/README-跟进信清单.md`；docx 在 Cowork 里跑 `md-to-word`；🔴 **发送由 Shao Peishen 本人在企微完成，代理人永不代发**
  - **身份已定**：汤丽萍＝**人事部 AI 专员**，与其他专员同等对待（名录正本里她还挂在「其他」栏、未记部门 ⇒ 那边需补一行，但 HR 线不依赖它）
  - ✅ **留白已清（09-09 他让直接改，「手工介入容易出错」）**：开头「先肯定最近一次真实交付」那句**判定欢迎信不适用、整行删除**——⛔ 不编造交付。删后重出 docx 并写回，占位零残留、frontmatter 未渗漏、25 段。**md ＋ docx 双件均为定稿态，状态仍 `⏳ 待你审`，发送由他本人在企微完成**
  - 🔴 **发送口径与实证（09-09 下午）**：他审核通过并授权先发群，台账已转 `🆕 待发`；**但 Cowork 实际发不出去**——`qyapi.weixin.qq.com` 在 Mac 侧 device shell 无 DNS、在云容器经代理 CONNECT 得 403（不在 egress 白名单）。⇒ 这封由他本人在企微发。要让 Cowork 能代发群，前提是把该域名加进本会话 egress 白名单
  - ✅ **口径固化**：「人力AI保障组」＝**人事部门群**（项目叫法 vs 实际部门，同一个）；Mac 端只此一群、不向 Windows 端其他群发信。已写进 `docs/跟进信/README-跟进信清单.md` 抬头
  - 🔴 **win 端收发实证（09-09 他口述 ＋ 本会话核 win 源码），三条改假设的事实**，全文 `docs/findings/2026-09-09-win端aibot收发实证-对8.6灰度的三条影响.md`：① **aibot 能主动发**，单聊与群聊同一方法只差 chatid（`send_markdown(chatid,…)` / `send_file(chatid, media_id)`）——**前置是专员先私信一次机器人**，那个单聊 chatid 才存在；本文此前「aibot 不能主动私信」的说法**已作废**。② **群里只收文字平信，文档回灌只能私信** ⇒ 第 4 章附件归档链路在群消息上**永远验不到**，8.6 验收必须先加「专员私信一次机器人」这一步，TD-22（真实 msgid 字符集）也只能在带附件的私信上核。③ aibot 开机即在线＝长连接监听形态可行，缺的只是把 `client.run()` 接进 `run_forever`（TD-19）。④ 必须分清：**值守服务由 launchd 在 macOS 原生环境跑、用本机网络能连企微；Cowork 的 shell 在隔离 VM 里无出网** ⇒ 服务发消息不需要我有网
  - ✅ **whitelist 已填**：汤丽萍（len=10）、邵培申（len=11）均非空，fail-closed 那道闸已过。⇒ 她的**回灌走群即可**（她在群里发、值守服务收），⛔ 不需要另建私信通道；真正的卡点只剩 **TD-19**（`make_sdk_connect` 探到 `connect` 是协程即拒启动，要把 `client.run()` 接进 `run_forever` 并用真实凭据端到端验证）＝ 8.6 灰度那件事
  - 🧹 仓库根的 `Claude outputs/`（桌面端自动存的旧版「人力资源部」信）已挪进 `_to_delete/Claude outputs-20260909-旧版人力资源部信/`——Cowork 删不了文件，只能挪；⚠️ `_to_delete/` 未进 `.gitignore`，提交时别把它 add 进去
  - 📎 截图实证（09-09 下午）：「人力AI保障组」7 人，**MAC机器人已在组内**（另一个 BOT 是陈承的机器人）；成员含邵培申（群主）、陈承、聂鑫、汤丽萍、王寒月
  - **远程（Mac／Cowork）能做**：读正本起草 md 正文、跑代词自检与决策点自检、出 `⏳ 待你审` 草稿
  - **远程不能做、⛔ 不要绕**：取号与串行闸判定只认 Windows 侧 CLI（`工具-跟进闸查询.py --to`）；README 主表（86 KB）远程读不到**也不该读**；docx 转换与登记 CLI 在 Windows 侧
  - **编号规则与发送八态改读** `6-人才与组织/部门AI专员跟进/跟进机制-判据版.md`（≈17 KB，远程可取）。已取到：编号按**部门连续计数器**、跨收信人共用、换人不重置、未发出/已作废不占号；README「编号」列未发出时写 `部门#N（待你审，暂不占号）` 带括注，信件抬头不带；文件名 `部门-姓名-跟进-YYYY-MM-DD-主要事项.md`、⛔ 不含会变的数字；落款固定 `—— OPVP Shao Peishen`
  - **已取到的起草依据**：正本 §4 三要素信骨架（抬头带部门连续编号→开头先肯定最近一次真实交付→逐件事「做什么／怎么做／什么时候交」→知识资产段→结尾附一页纸＋落款）、§5 落位与登记五步（含 1bis `决策点:` 字段、步骤 2 代词自检、步骤 3 docx 必发、步骤 5 首次发新机制信须随附《专员协作说明-新版需求确认怎么配合-2026-07-25.md》）
  - **名录硬事实**：汤丽萍＝**女**（名录正本）。⚠️ 她在名录里**不在「部门AI专员」五人之列**（姚祖怡／陈忱／唐燕萍／泓钦／陈承），列在「其他」且未记部门 ⇒ ⛔ 不得自行把她写成「人力资源部AI专员」，信里用中性表述，这一条要当场问 Shao Peishen 一次
  - **口径（09-09 他拍）**：先发「机制版」——只建立需求确认与使用反馈机制，明写「系统尚在灰度，暂不用改变你现在的习惯，开通时点我单独告知」；使用告知留 8.7
  - ⏸ **09-09 下午他说「稍等 10 分钟再取，win 侧在修复」** ⇒ 下一轮重取正本前先确认他说修完了
## 三、待决策 / 悬置

| # | 事项 | 状态 |
|---|---|---|
| 1 | 🔴 **`git push` 被 auto mode classifier 拦** | 反复出现（08-28、08-30、09-03 K/L 又两次）。09-03 `0903Z` 实测：发车前那次被**直接拒绝**（非挂起待点击），收尾那次成功——同一 session 内两次结果不同，机制仍不明。两条路：**(a)** 每次在能批准的 session 里点放行；**(b)** 给 `.claude/settings.json` 加 `"permissions": {"allow": ["Bash(git push:*)"]}`——项目级、可提交、对所有 session 生效。**Shao Peishen 09-03 拍板走 (b)**。Cowork 侧改 `settings.json` 被 classifier 拦（改权限配置本就该在 CC 里人眼过一遍），✅ `0903M` 已加（`365e5fa`）。⚠️ 白名单对**新开的** session 生效。**09-03 17:xx 又撞一层**：Auto Mode 分类器拦 `0903Y` 的 `nohup run-lanes.sh`（判「无人值守起子 session」高风险），看护者自己改 settings.json 也被拦 ⇒ Shao Peishen 手工加 `Bash(bash docs/openers/run-lanes.sh:*)` 与 `Bash(nohup bash …:*)` 两条。已写进 lane-dispatch skill ④ 与看护者前置自检第 0 条。**09-04 `0904Z` 推翻「白名单能解决」的归因**：白名单三条确认已在 `.claude/settings.json` 里（`grep -c`=2，`scripts/allow_run_lanes.py` 复跑也确认无新增可加），但 `nohup run-lanes.sh` 与 `./sync-to-server.sh` 仍各被拦两次——说明白名单从未是真正生效的机制，09-03 的"解除"很可能是巧合归因，不是因果。**唯一验证有效的路径**：把启动命令原样贴成 ` ```bash ` 代码块发给 Shao Peishen，他在 CC Desktop 对话里点 Run 按钮直接执行——用户直接动作不经过 AI 的 Bash 工具调用，不触发该分类器；看护者自己反复重试大概率无效。已写回 `lane-dispatch` skill。**✅ 09-08 20:3x 根治**：`0909G` 建 launchd WatchPaths 触发器（`docs/openers/lane-launcher.sh` + `scripts/install_lane_launcher.py`），Shao Peishen 在 Terminal 装好（`bootstrap rc=0`，`state = not running` 是 WatchPaths 型的常态，有请求文件才起）。从第十四批起看护者写 `.claude/handoff/launch/<ts>.request` 即发车。**09-09 `0909Y` 首跑：只成一半**——六秒内触发+白名单+`.started` 全通，但 run-lanes 被 launchd 连坐杀（plist 缺 `AbandonProcessGroup`；另潜伏 `PATH` 无 `~/.local/bin`）。修法在【二】⑮ C 第一行；修好前退路仍是点 Run（无引号版命令，已写进 lane-dispatch skill）。发版 `sync-to-server.sh` 仍走点 Run（不可代项，人点一下本身就是授权留痕） |
| 2 | 🔴 **worktree 被未落档地清理，已发生两次** | 08-30 11:38 扫掉 u2/unitE/unitF/u1 四条（判据＝真未合 0，代码零损失）；**09-03 前 u5 也被移除**——而 08-30 那份报告刚评估过「u5 真未合 11，同样的清理不会碰它」。⇒ **判据变了或用了 `--force`，机制不明**。代码没丢（分支 `worktree-audit-u5-queue-and-wiring` 与 `19ab503`/`f899c98` 都在），丢的是 worktree 内 git-ignored 的 `.superpowers/sdd/` 台账。**要不要查清是谁在清、加个护栏？** |
| 3 | **TD-9**：同一草稿第二次拦截零留痕 | U6（0903G）已**坐实**："放行后复发又被拦"路径系统性缺席。修复要改已过审的 `approve()` 签名 + 5.4 幂等键公式，属契约层变更。✅ **已修复合入**（`bf45370`，0903Q），TD-9 销账行已写；只差包归档（`0904A`） |
| 4 | `.51` 留步清单**只剩一项** | §5-1 备份任务确认/新增（`0903D` 只做了一次性快照 `C:\apps\backups\20260903-1003`，不等于常态化备份任务）。§5-2 链校验与 §5-3 四步已于 09-03 闭合。见 `docs/audit-and-outbound-ops.md` 第五节 |
| 5 | `.51` 整机重启 | 阻断已清、只差窗口。⚠️ 爆炸半径 **7 个服务**（含门户网关本体），`CBS RebootPending=True` + 已 85 天未重启，停机时长不可按常规估。opener＝编排文件 `[Mac] 0820-9R` |
| 6 | 阶段 C 门户导航 | 需在 Win 笔记本上改门户 HTML。板块名「HR·招聘智能体」，外链 `http://192.168.100.51:8095/hr/recruit-agent/`。与 `.51` 服务无关，不影响运行中的服务 |
| 7 | 决策代理人 | `CLAUDE.md` 框架已建，**2026-08-28 决定继续不设**。「可代」项在无代理人期间同样一律挂起等本人 |
| 8 | 06 清单剩余 | 3.3 企微 webhook（无挂载点）、9.1/9.2/9.3 沟通线。7.1/7.4 合规条款已随 audit 包推进 |
| 10 | 8.4 ＋ U6 巡检 CLI 上 `.51` | 8.4 要人在页面跑通"模糊回复→点选→带缺口确认"；U6 的巡检 CLI 从未对 `.51` 真实 `demo.db`/`decisions.jsonl` 跑过——且 U6 代码**还没部署到 `.51`**（当时现网 `d104249`，早于 U6；**2026-09-17 七次发版后现网 = `f81cc9d`**）。✅ **已闭合**（`feb49d6`）：二次发版含 U6，巡检 `EXIT=2`＝镜像尚不存在，8.4 本人跑通回勾 |
| 11 | `data/replay/` 快照随 worktree 自删 | H 拉回的 `.51` 一致快照（2.4 MB）随其 finishing 流程一起没了，分析结论已在 `2239b90` 的 findings 里，丢的是原始输入、无法逐字节复核。**Shao Peishen 09-03 裁决：立规矩**，已写进 run-build / lane-dispatch 两个 skill（`0903K` 提交） |
| 9 | 🧪 `claude -p -n` 是否真给 session 起名 | **未实测**。`run_lane()` 一直在传 `-n`，但那条注释原来引的"实证"已被推翻。留着无害，⛔ 不要写成"脚本这条路能保住编号"。5 分钟可验 |

---

## 四、绕不开的环境事实

- 🔴 **`.51` 上 venv 的目录名是 `.venv`（带点）**。真源＝`deploy-server.ps1:31`
  `$venvPath = Join-Path $AppDir ".venv"`；旁证＝`docs/findings/2026-08-20-51整机重启验证-重启前采集.md`
  里从实机取的计划任务 `Execute` = `...\.venv\Scripts\uvicorn.exe`。
  **08-31 就因为文档少写这个点，整条 §5-3 验证被误判成「服务器缺 venv」，白等了三天。**
  📌 教训：报错说"找不到 X"时，**先核 X 的拼写与真源是否一致，再去查环境**——
  真源就在本仓库里，一条 grep 的事。
- 🔴 **pytest 基线⛔ 不要写死进 opener**。`222` → `356` → `487` → `675` 已经飘过四轮，
  **写死的数字必然过期，且过期时毫无症状**（判据退化成恒真，等于没在验）。
  看护者 opener 已改成「开跑前自测一次当本批基线，判据＝跑完 ≥ 开跑前且 0 失败」。
- 🔴 **`set_session_title` 在远程编排器派发的 session 里不可用**（08-30 实测，报
  `unavailable in sessions dispatched by a remote orchestrator`）。这类 session 侧边栏会丢编号，
  **不是故障、也不是漏调**，如实登记即可。
- **无头 session 取不到 `superpowers:*`** —— ✅ **2026-09-09 `0909AB` 已修复**；以下旧结论保留为成因记录：
  插件装在 `projectPath: /Users/paulshao/Projects`
  项目作用域，`.claude/settings.json` 里 `enabledPlugins` 开着也解析不到。
  ⇒ `run-build` 的前置检查 1「调不到就停」在无头下**恒定失效**。
  08-27 与 08-30 两次都是执行者照磁盘上的 `SKILL.md` 手工走完协议的——**那是运气，不是机制**。
  - **修法＝改 user 作用域**：`claude plugin install superpowers@claude-plugins-official --scope user`
    （user 作用域对任何 cwd 生效，含 `/private/tmp/wt-*` worktree）。实测：修前 `cd 仓库根` 与 `cd /private/tmp`
    两条无头探针都回 `Unknown skill: superpowers:writing-plans`，修后都回 `LOADED`；
    `subagent-driven-development` 在 `/private/tmp` 另验一次 `SDD-LOADED`。
    **`run-lanes.sh` 的 `--plugin-dir` 退路没用上，脚本一行未改。**
  - ⚠️ **user 作用域装到的是 6.3.0**，不再是六批手工走时读的 6.2.0（两版 `skills/` 目录同为 14 个、名字逐一相同）。
    引用磁盘 SKILL.md 的历史路径写死 `6.2.0` 的地方，读到的已不是现在生效的那份。
  - ⛔ **`claude plugin disable <id> --scope project` 是按插件 id 全局生效的**：09-09 实测它把 user 作用域那份
    一起置成 `enabled: false`（三条记录全灭），已从备份回滚并复验 `LOADED`。要清 `/Users/paulshao/Projects`
    那份旧 project 记录，只能直接编辑那个 `settings.json`，⛔ 不要用 `plugin disable`。
  - 🔴 口径（Shao Peishen 09-09 定）：**修好以后一律走规范流程**——`Skill(superpowers:…)` 回 `Unknown skill`
    ＝环境故障，泳道登记「⏸ 留步：superpowers 不可达」并停，⛔ 不再手工走、⛔ 执行者不自己装插件。
- **`--max-budget-usd` 在 Max 订阅下不是钱闸**，是"跑飞保险丝"。真正的天花板是 5 小时滚动 +
  每周用量窗口。并行两条 run-build 用量翻倍，开跑前看 `/usage` 比看美元数有意义。默认已提到 25。
- **GitHub Actions**：private 仓库 Free 计划 2000 分钟/月，**Windows runner 按 2x 扣**，撞过一次额度。
- **日志路径耦合**：`log_file` 是相对路径 `logs\app.log`，靠计划任务的
  `WorkingDirectory` 解析——**改工作目录会把日志静默挪走**。
- **脱敏层尚未被真实数据检验**：日志里 0 个标记词，但 `<redacted>` 也是 0 命中，
  说明脱敏根本没被触发（`loggable_summary()` 至今无生产调用点）。这条验证成立的是
  「没有泄漏」，**不是「脱敏被证明有效」**。

---

## 五、绕不开的约束（每次都要记得）

- 🔴 **正文 > 500 字的 opener 走引用式**（Shao Peishen 09-03 定）：正文写 `docs/openers/<MMDDX>-<主题短名>.md`，
  聊天只贴 4 行引用块（头两行 + set_session_title + 「读该文件逐节执行，文件不存在即停」）。文件随任务提交＝留痕＋号池可 grep。
  模板与理由见 `.claude/skills/kickoff/SKILL.md`「引用式 Opener」。
- **给 Paul 的每条指令，代码块头两行固定**：`[Mac]MMDDX-<主题短名>` + 【设置】单行（五项 ｜ 分隔）。
  **CC 的 opener 第 3 行必须调 `set_session_title`**。`MMDD` 实跑 `TZ=Asia/Shanghai date +%m%d` 取
  ——本机在 EDT，照本机日期编会集体差一天且不报错。判据表见 `CLAUDE.md`。
- 🔴 **他在 Desktop 用 CC，不开终端。要他执行的东西一律包成 opener 代码块，⛔ 不给裸 bash。**
  泳道发车只给**看护者 opener 一整块**（那块的【三】自己会启动脚本），⛔ 不另给发车命令，
  也⛔ 不要写「去编排文件第 N 行整块复制」——要把正文原样贴进回话里。
- **一次给 ≥2 个 opener 时，必须说明次序与能否并行**（判据＝触碰区是否重叠）。
- **在 `.51` 上跑的命令**要写明"在 .51 上跑"并给 **ssh 包装形式**，⛔ 不给裸命令。
- 🔴 **他说的【xx】大概率指 session 名**，不是文件名。⛔ 别去 grep 文件找它——
  跨界面看不到对方会话，正确反应是走文件系统盘点真身（`git log` / `.claude/handoff/*` /
  各 `tasks.md` 的回勾），然后说明「那条 session 我看不到，以下是从文件系统盘出来的」。
- **git 相关只能在 CC**——Cowork 的 bash 在隔离 VM 里，对 `.git/` 只能写不能删。
  Cowork 侧只读核查用 `git --no-optional-locks`，不会留锁。
- **「企业AI转型」已迁出 OneDrive**，唯一入口＝GitHub 公开仓库
  `Raytheoner/zhuopin-ai-transformation`，**分支 master**，WebFetch 读
  `raw.githubusercontent.com/.../master/<路径>`（中文路径要 percent-encode）。
  没有本地副本 → **grep 不了，引用必须给文件级 URL**。

## 六、已固化进 skill 的判据（此处只留指针）

1. **计划里的任务标题必须是三级 `### Task N:`**——二级会让 `scripts/task-brief` 静默返回空。
   已写进 `.claude/skills/spec-to-plan/SKILL.md`。
2. **合并后要单独验 `git rev-list --count main..<分支>`**——`finishing-a-development-branch`
   被跳过时毫无症状。已写进 `.claude/skills/run-build/SKILL.md`。
3. **`rev-list` 非 0 时先别下结论**——main 被 rebase 过时会假阳性。再跑 `git cherry -v main <分支>`：
   全 `-` 是内容已在 main，有 `+` 才是真没合。
4. **说「开始泳道看护」即触发 `lane-dispatch` skill**——扫待办 → 判触碰区分泳道 → 写 opener
   进编排 → dry-run 核对 → **只给看护者 opener 一整块**。
5. **`run-lanes.sh` 开跑前自检**：无头块内含 `set_session_title` → `exit 13` 拒跑；
   块外缺豁免注明 → 只 WARN。方向别看反——无头块的**正确状态是不带那一行**。
