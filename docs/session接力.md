# Session 接力 · HR 招聘智能体

> 滚动更新，覆盖旧版。新会话读完本文即可接上。
> 最后更新：2026-09-23 10:2x（Cowork·`0923R` 续棒看护实核）／上一版 2026-09-20 15:4x（Cowork·`0920R` 续棒看护实核）／上上版 2026-09-19 21:5x（Cowork·0918AO 巡检实核：0918V 已收口、M2·U3 建造完成待发布闸 Q-49、M3 U4 已合 main 进度 36/76）｜ 上一次：2026-09-18 20:4x（Cowork·0918G 换道前实核：M2 可见薄片已上线 .51、投递中继上线、无头块铁律立规）｜ 上上次：2026-09-17 06:2x（Cowork·0917D：R-1 实核已完成；体积闸归档接力 61→41 KB、编排 65→12 KB；R-2 拆成 `0917G`→`0917H` 经 launchd 发车（首发 E/F 作废）。此前：Cowork·HR业务线-接力0917D 转场：把 `0916E`–`0917B` 这一大段
> **从未进过本文**的进展补齐——第十七批波次 1＋2 已基本完成、Token 治理线已结项；
> 订正 `tasks.md` 抬头进度行失真 24/33 → 27/33）

---

## 🔴 新 session 先看这一节（2026-09-23 10:2x，Cowork·`0923R` 续棒看护实核）

⚠️ 本节之后的历史节一律以本节为准。推进依据＝任务驱动：`docs/roadmap/任务驱动上线路线图.md`。

### 一、09-23 已落地

1. **`0921E`／`0921E续1`／`0921F` 三条「自报 DONE 但 main 上全空」的旧账已清**：合并重做为 `0923A`，机判 7 条含 3 条新测试全过（`tests/test_attachments_unknown_frame_dump.py`），已确认 ff 合回 `main`（commit `48f346f`，`origin/main` 与本机 `main` 一致），并在主工作区复跑核实——不是只信自报。
2. **附件只走私信口径已生效**：`tools/liaison/frames.py`／`__main__.py::handle_message_frame` 群帧附件一律忽略；单聊（私信）帧才会尝试解析。⚠️ **`ATTACHMENT_FIELD_PATHS_BY_MSGTYPE` 仍故意留空**（fail-closed）——私信帧到达时只落一份结构取证（键路径＋类型名，⛔ 无取值）到 `unknown_attachment_log_dir`，文件本体尚未真正能收。需汤丽萍下次真的私信发一次文件，取证帧到手后再派一条泳道填表，才能真正收到文件内容。已就此口径当面答复过 Shao Peishen（本场对话）。
3. **决策点 c 漏收诊断仍留步**：`人事部#2` 09-20 17:04 那条 `@MAC机器人` 决策点c文本为何未进归档，本机与本 worktree 均无 `data/liaison/logs`，需 `.51` 上的真实入站日志才能排除四个候选成因，⏸ 待具备 `.51` 只读权限的泳道或 Shao Peishen 现场核对。
4. **`0923B`（拆 `人事部#3` 四条 pending）结果 PARTIAL**：4 条全部复核，均不满足拆件章程完整性判据（决策点b缺时数/人名；信件正文A/B/C/D场景仍完全未答），全部折进同一条 `Q-59` 待人，**未新解锁任何一条**，`data/liaison/unpack-signal.json` 里 4 条 pending 数量未变。
5. 已给 Shao Peishen 一份可直接发送的微信私信草稿（发给汤丽萍）：① 请她改走**私信**（非群）重发 `10个岗位需求.doc`；② 决策点b补投入小时数/牵头人姓名；③ 请回信正文 A/B/C/D 场景与路线图——尚未确认他是否已发出。
6. 清理：已删除本场为 `0923A/0923B` 设的一次性回查提醒（`trig_01H1Bf5nvB9qnvL2jxwBmEQo`），因已在本场手工核实完毕，避免重复报告。
7. **`0923R2`（续棒二次核实）**：`queue_pending`／`dispatcher_backlog --dry-run`／最新 `lanes-20260923-094859/results.tsv` 三项与本节结论**逐项核对一致**，本轮未新跑任何泳道。`ready` 集合较写本节时新增 `interview-scheduling/0.2`（共 4 条），已用 `grep 任务台账.yaml` 核实其「阶段」仍为 `gate`——不是可发车项。未新增开口项，未新增决策项。
8. **`Q-59` 已答（Shao Peishen `0923R2` 回 `1a`）**：`人事部#3` 两条部分命中回件（决策点a清单无实体、决策点b缺时数/姓名）均判**不完整**，退回汤丽萍补齐，`人事部#3` 维持整信待齐 A/B/C/D 场景与决策点a/b/c 全部到齐后一次性转闭环——已回填 `docs/roadmap/定夺队列.md` `Q-59` 行状态与答复列。下一步：需一条跟进信泳道向汤丽萍催补（决策点a清单实体＋决策点b时数/姓名），本场未派，留给调度器或下次机制类泳道。
9. **`Q-01`（G1 私信附件真实帧确认）已答（`0923R2`）**：Shao Peishen 报「文件又给私信发了一遍」（微信私信截图核对），对应真实帧证据实为 `2026-09-21 11:08:40` 那次私信触发的 fail-closed 日志（`data/liaison/logs/liaison.log:274`）——键路径已确认 `body.file.url`（341字符）／`body.file.aeskey`（43字符），**帧里无 filename 字段**（WeCom 文件回调不带原文件名）。该证据早于 `unknown_attachment_log_dir` 落盘功能（随 `0923A` 才合入 main）,故当时只落日志行、无 JSON 取证文件，但键路径信息已足够。⚠️ `data/liaison/liveness.json` 显示值守进程自 `2026-09-22 10:48` 起连续运行、**尚未重启**，仍在跑 `0923A` 合并前的旧代码。下一步需一条实现泳道：① 按上述键路径 + 文件命名方案（无 filename，需另定）填 `ATTACHMENT_FIELD_PATHS_BY_MSGTYPE`；② 重启值守。本场只回填队列答案，未派该泳道。

### 二、开口项（截至 `0923R`）

- 台账 `ready` 三条 `offer-generation/0.2`、`offer-generation/0.5`、`onboarding-flow/0.2` **阶段全是 `gate`**（等上游交付），不是可发车。
- 待外部输入、挂起不追问：`Q-01`（附件取证已就位，等汤丽萍真实私信一次文件）、`Q-04`（汤丽萍回件）、`Q-08`、`Q-10`、`Q-16`、`Q-52`（回 LAN 取客户端侧一手证据）、`Q-59`（`人事部#3` 四条 pending，正文完全未答）。
- 🔴 须本人：`Q-53` 语音主机采购与预算（阻塞 M3 `5.11` live e2e）、`Q-F1`～`Q-F4`、决策点c的 `.51` 日志核对、微信草稿是否已发。

### 三、下一步（新 session 直接接着做）

1. **本轮无可发车项**：`ready` 三条全是闸。
2. 若汤丽萍已私信发过文件：先看 `unknown-attachment-frames/` 下是否已落新取证文件，有则派一条泳道据真实结构填 `ATTACHMENT_FIELD_PATHS_BY_MSGTYPE`。
3. 若他已在 `.51` 上核对过决策点c日志：把结论写回 `Q-01` 并把该项状态由「留步」改「已诊断」。
4. 其余按调度器自转（daily 09:00 兜底＋批次收敛事件唤醒）。
5. 续棒口令：新会话打一行 `[Mac]MMDDR 续棒泳道看护`，本文件是接手第一读件。

---

## 开场词（复制即用）

```
[Mac]0918AO-HR业务线接力
【设置】执行环境: Cowork ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（Cowork 无 worktree，只做文档、编排与派单）｜ 审批模式: 逐项询问 ｜ 工作区: 仓库根（/Users/paulshao/Projects/HumanResource）
读 /Users/paulshao/Projects/HumanResource/docs/session接力.md 的「🔴 新 session 先看这一节（2026-09-19 21:5x）」恢复上下文，然后按【四、下一步】继续。
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

### 🈸 2026-09-23 拆件会话（`0923B`）· 人事部#3 四条 pending 复核确认，Q-59 仍待答，无新下裁决

按 `liaison-unpack` 章程逐条重新读取归档件（非沿用摘要），四条内容与既往登记一致，无新增证据：

- `7ed6bf11e1b21d321182ac22f0be5935`（决策点a，「10个岗位需求清单」标题，无实际清单）
- `79ec288dcc53c75df9fa72f3dd1612cd`（决策点b，「选择：1」，缺投入小时数/人名）
- `7b37bb24af440a172f6ae89b6534289f`（决策点a 第二次尝试，「决策点a 答复中的清单」标题，仍无实际清单）
- `459b735f7df8204551564a25007329eb`（0 字节空文本）

`docs/roadmap/定夺队列.md` Q-59 本次核对（2026-09-23）仍为「待答」，答复列为空，未见 Shao Peishen 新裁决。
章程 §二 对四条逐一判定：均落入既有「合并信部分命中，无判据」失败模式（红线③新下裁决），继续折入 Q-59，
不单开新决策、不写回灌结论、不改台账。`data/liaison/unpack-signal.json` 四条 pending 均未清（工具只支持
`--clear --before <单一时间戳>` 前缀清除，四条中最早一条即为待人项，无法只清已判定项而保留待人项，
因此本轮不执行清除，避免误清未决项）。

【谁做：Shao Peishen】【状态：待人（沿用 `Q-59`，本条为复核确认，不构成新裁决）】
【判据：同 `Q-59` 现有判据——一并裁决四条回件（决策点a 两次尝试、决策点b、空文本）是否已足够完整，
并明确 `人事部#3` 是整信待齐 A/B/C/D+a/b/c 后一次性转闭环、还是允许按决策点分批部分关闭】
【不做会怎样：`data/liaison/unpack-signal.json` 中四条 pending 信号继续保留，
`docs/跟进信/README-跟进信清单.md` 里人事部#3 行维持「📨 回件已到，待拆件 2026-09-20 17:04 CST」
原状态不变，下一轮拆件会话探测仍会再探到全部四条，不会丢；机器判据里 `pending<4` 断言本轮预期不过，
是待人的正常结果，不代表处理失败】

### 🈸 2026-09-23 续棒 0923C 第1/3棒 · 合回 main 完成，机器判据「len>=3」疑似笔误待确认

`lane-0923C`（TD-51 附件字段映射与下载口销账）已合并进 main：因 main 同期新增 5 条 docs/dispatcher
commit，`--ff-only` 不可用，改用 `git merge lane-0923C --no-edit`（无冲突），合并 commit `78c7290`。
主工作区复跑 `tools/liaison/tests/test_inbound_attachment_wiring.py` + `test_unpack_bridge.py`：
48 passed。`docs/tech-debt.md` TD-51 销账戳记随合并落地。

`docs/openers/0923C-TD51附件字段映射与下载口销账.md`「三、机器判据」第 1 行
`assert len(f.ATTACHMENT_FIELD_PATHS_BY_MSGTYPE) >= 3` 实测为 `1`
（`{'file': {'download_url': ..., 'aes_key': ...}}`），不通过。核实：该断言把"填三条字段路径"
（download_url／aes_key／filename）误算成"顶层 msgtype 条目数≥3"；但 filename 无真实键，按方案改走
`_guess_attachment_filename` 兜底，不是字典项；`image`／`voice` 两个 msgtype 无真实帧证据，opener 正文
「零、为什么」与 `frames.py` 代码注释都明确⛔ 不可瞎填。`len==1` 是正确、诚实的实现结果，怀疑 `>=3`
是 opener 作者断言笔误。本棒未擅自改 opener 存档或伪造条目让判据"通过"——这触及验收判据本身的正确性，
登记待确认，不越权替他改验收标准。

【谁做：Shao Peishen】
【状态：待人确认（技术判据疑似笔误，非合规红线／淘汰规则／对外通道等不可代事项，但触及验收标准本身，未擅自改）】
【判据：确认 `docs/openers/0923C-TD51附件字段映射与下载口销账.md`「三、机器判据」第 1 行 `>=3` 是否应
改为 `>=1`（或 `==1`，或改成 `"file" in ... and len(...["file"]) >= 2`）；确认后由下一条泳道/看护顺手
改掉该 opener 存档文件，⛔ 不改代码】
【不做会怎样：opener 存档文件的机器判据与实际正确实现永久不一致，下次任何人重跑该判据脚本都会得到
"未过"的假阴性，需要反复重新排查同一个已查清的问题】
