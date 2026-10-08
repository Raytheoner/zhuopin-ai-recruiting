# Session 接力 · HR 招聘智能体

> 滚动更新，覆盖旧版。新会话读完本文即可接上。
> 最后更新：2026-10-07 21:xx（Codex·`[Mac]1001G` PRD v2.0 ＋ UI v2.0 收口，见本页 🔴 节）／上一版 2026-10-07 18:5x（Codex·`[Mac]0930R` 泳道看护接棒实核）／上上版 2026-09-29（Codex 迁移实核：构建 workflow 双引擎切换已验收）
> **从未进过本文**的进展补齐——第十七批波次 1＋2 已基本完成、Token 治理线已结项；
> 订正 `tasks.md` 抬头进度行失真 24/33 → 27/33）

---

## 🔴 新 session 先看这一节（2026-10-07 21:xx 更新，Codex·`[Mac]1001G`）

**泳道看护的状态与入口**（历史节已归档到 `docs/archive/session接力-归档.md`）：

- **全局暂停仍在**（Shao Peishen 2026-09-30 答 `1b`／10-07 答 `2b` 维持）：心跳 automation `hr` = `PAUSED`，
  `com.zhuopin.hr.task-dispatcher` = `disabled` ＋ 未加载；**自动发车与自动刷新台账都关着**，
  看护由主会话手工执行（跑 `--dry-run` 核对 → 写 launch 请求 → `wait-lanes.sh` 盯 → 合分支 → 刷台账 → 落档）。
- **最近闭环（2026-10-07）**：`1001A` 回推文案可读化／`1001B` M2 前置两项／`1001C` UI 共用样式层／
  `1001D` 逐页对齐与窄屏／`1001E` M1 9.1 机械部分（10 岗位真 LLM 重跑）／`1001F` 375px 真机实测；
  **`1001G`（本场收口）＝ `08-PRD.md` v2.0（全量功能需求＋两张缺口清单）＋ `09-UI-Design.md` v2.0
  （audit→方向→落地→复核，8 页产品级升级）**；证据：`08-PRD.md`／`09-UI-Design.md`／
  `docs/findings/2026-10-07-1001G-UI审计与升级.md`。
- **本场机器判据（全绿）**：`ui_narrow_check.mjs` rc=0（同意 327px／邀约 305px，scrollWidth 375）；
  指定 pytest 子集 65 passed；全量 **3825 passed / 10 skipped**；AI 标识 grep 仍在。
- **当前待人**（详见 `docs/roadmap/定夺队列.md` 与本页新增项）：
  ① M1 9.1 的**人评**：Shao Peishen 2026-10-07 答 `3a`——本人直接把评估表转给评估人；回表后看护者只统计与落档（≥80% ⇒ 勾 9.1＋归档两包；<80% ⇒ 按维度返工）；
  ② 仍开的两项输入：`D4`（Q-05 定型二次签认＋脱敏脚本过审）与 `D5`（二期四包 G2 放行策略）；
  ③ 汤丽萍回件（`人事部#3` 两条部分命中回件按 Q-59a 等补齐，本场无新回件）；
  ④ **M2 判例批改表**：Shao Peishen 2026-10-07 答 `5a`——**转发说明已备到"可发送态"**
  （`docs/templates/m2-判例批改表-转发说明.md`，含可直接粘贴的企微话术），等他本人发出；
  回件附件链路已可用（TD-51 映射 `body.file.url/aeskey` 于 `0923C` 落地、值守 09-30 23:56 已加载）。
- **2026-10-07 21:xx 追加（答 `1a–7a`，本场全部按推荐落档）**：`08-PRD.md` §10 的 N1–N6 与 D3 已标"已定"
  （北极星口径＝答 1a／权限矩阵＝2a／M1 人评＝3a／留存数值随法务签认填齐＝4a／二期顺序+提醒节奏+两套口径＝7a）；
  `09-UI-Design.md` §13 的 U6–U8 已标已定；**`[Mac]1001H` 小泳道（答 6a）已发车**——
  `GET /api/resumes/{id}/parsed` 只读响应带 `field_review_status`＋前端接线＋测试，泳道 `机制-校对待校对接线`
  （worktree `lane-1001h-review-pending`，Haiku 档，批次 `lanes-20261007-205454`）。
- **2026-10-07 22:xx 追加：`1001H` 已闭环（`12d2a8c` 合回 main）**：产物＝`app/web/server.py`（只读响应新增
  `field_review_status`）＋`resume_review.html` 接线＋`tests/test_resume_review_pending.py`（3 条）＋tech-debt 指针；
  复核实测：相关 21 passed、main 全量 **3828 passed / 10 skipped**。⚠️ **机制教训（已落 `lane-dispatch` skill ③-6）**：
  泳道 `## 机器判据` 块里**别放全量 pytest**——gate 单条命令超时 60s，全量约 108s 必 `rc=124`⇒GATE-FAIL，
  产物全绿也不自动合 main（本次由看护者复核后手工 ff 合并）。另：`m2-判例批改表-转发说明.md` 已就绪（可发送态，
  等他发出）；TD-51 附件映射已可用。
- **2026-10-08 08:xx 追加（答 `1a，2a`）**：① **D4a 脱敏脚本已起草**——`scripts/desensitize_resume.py`
  ＋`tests/test_desensitize_resume.py`（8 条，合成假数据）＋审稿说明
  `docs/templates/历史离职简历脱敏-规则与使用说明.md`（S1–S5 待他拍板；演示跑通：住址保留到「市」、
  工作/教育年份保留、其余直接标识全抹、报告零原值）；② **D5a 二期四包 G2 放行已生效**——`Q-39`～`Q-42`
  答复按字面「定」转写，`scripts/gates.py state G2` 四者皆 `已放行`；台账重生成：闸门待放行 **4→0**、
  待开 195→214、阻塞 78→59，ready 集含四条 `*/U1/plan`（下一场可直接 spec-to-plan 发车 U1–U3；
  U4 含 `.51` 发版仍为不可代阻塞）。
- **2026-10-08 08:3x 追加（Shao Peishen 指示：盯住 `人事部#3` 重发＋串行规则）**：
  ① **"上次没接住"根因已查明**（写进 `docs/findings/2026-10-08-拆件接件准备与0924故障根因.md`）：
  09-24 那次拆件会话（claude 引擎）启动即死于「Claude 订阅被禁」，回灌结论为空、信号保留至今；
  ② **今天的接件条件已实测**：按 dispatch 真实 argv＋真实 cwd 的 codex 无头冒烟 **rc=0（回复"接住"）** ⇒
  她今天重发后，值守→桥追加信号→**codex 拆件首跑**应能接住；首跑若失败按 findings §三 回退预案人工处理；
  ③ **串行规则按住 `人事部#4`**：`#3` 在途期间不发独立新信，只能等 `#3` 闭环或改写成 `#3` 的补充信
  （待 Shao Peishen 选）；定夺队列 `Q-67` 保持待答。
- **2026-10-08 08:4x 追加：🎉 发版五（新 UI）已发布完成**（Shao Peishen 答 `1发`；G3 `Q-66` 已放行）：
  `.51` 由 `8596829` 升到本机 main（`aa19d3f` 时点；改动面仅 `app/web/**`——8 页新 UI＋`app.css`＋
  `server.py` 的 `field_review_status`，无依赖/无迁移）。执行尾行：快照 `C:\apps\backups\20261008-0843`；
  `sync-to-server.sh` → `发版完成`；`LastTaskResult=267009`／`Running`；`8095 LISTENING pid 1564`；
  `Application startup complete` 08:44:05、近 200 行 0 Traceback；冒烟六项全过（CSS `.term-card`×3、
  8/8 页带 `?v=20261007`、`field_review_status`×2、`/api/jobs` 200、合规断言 `EXIT=0` 6 项 OK、
  入库闸 `False`）。**未回滚**；未对外发送。执行记录见
  `docs/releases/2026-10-08-发版五-新UI执行清单.md` §七。
- **2026-10-08 08:4x：`人事部#4` 路径已定（答 `2a`）**——等 `#3` 回灌闭环后再发独立 `#4`（不改写补充信）；
  `Q-67`（G4）保持待答。她尚未重发（最新入站仍是 10-01 00:02；值守存活戳 08:44:46 正常）。
- **2026-10-08 09:0x：拆件结果通知小泳道已发车（答 `1a`）**——`[Mac]1001I-拆件结果本人通知`
  （泳道 `机制-拆件通知`，worktree `lane-1001i-unpack-owner-notify`）：① 起活失败→写 `owner_notify_outbox`
  （dedupe `unpack-dispatch-failed:{msgid}`）；② 新增会话 wrapper（`session_runner.py`）——会话结束即写
  `unpack-session-exit:{msgid}`（含 exit_code/日志路径），⛔ 不动拆件章程与拆件会话权限（通知一律由值守侧写）。
  她重发后 codex 首跑无论成败都会有私信到达 Shao Peishen。
- **下一步（业务泳道建议，下一场可直接接）**：M1 9.1 人评回收（≥80% ⇒ 勾 9.1＋归档两包；<80% ⇒ 按维度返工）
  → M2 三样输入落地（Q-06 脱敏脚本过审＋样本入库）→ U4/U5/U6 泳道（前置：判例批改表模板发出＋回件）。
- **🔀 转场说明（2026-10-07 晚，已执行完毕）**：`[Mac]1001G` 已完成"PRD 补全 → @Product Design UI 升级"，
  需求与 UI 框架已稳定；下一场回到业务泳道（M1 9.1 人评回收、M2 三样输入）。
- **详细日志**：本文末 `### 🈸 2026-09-30 11:2x 泳道看护接棒实核（Codex·[Mac]0930R 本场实核）` 节
  （09-30 → 10-07 的逐条追加都在那里面）。

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

### 🈸 2026-09-30 11:2x 泳道看护接棒实核（Codex·`[Mac]0930R` 本场实核）

- **批次盘面**：无批次在跑（无 `run-lanes.pid`、无未消费 launch 请求；最新批次目录
  `lanes-20260930-082553` 的 mtime 停在 08:34）。本日三批已全部收口：`0930A/B/C`（各 PARTIAL→
  `lane_collect` 代提交并合回 main，`86af16d`/`bdf1f53`/`21df3a8`）、`0930E`（GATE-FAIL 假阴→
  救援合回 `bc401e5`，判据在 main 复跑 34 passed）、`0930F`（PARTIAL→`7505c2f`，判据 6 passed）。
  `main = origin/main = 4a913b7`。
- **可发车项：无**。`ready=4` 全是业务 gate（`interview-scheduling/0.2`、`offer-generation/0.2`、
  `offer-generation/0.5`、`onboarding-flow/0.2`），均等 M2 U5 等上游交付；台账 559 条
  （待开 199／在跑 7／完成 275／阻塞 78；阻塞·决策 52／外部 25）；`conflicts=8` 是
  「台账=完成·真身=待开」的已知家族（`propose:S-*` 等来源行映射不全），非新故障。
- **定夺队列**待答 11 条：Q-04／Q-10／Q-16／Q-52／Q-57／Q-58／Q-F1–F5；其中 Q-F5（8.8 一周观察窗）
  09-24 到期，**已超期 6 天**。
- **本场新发现（机制）**：`results.tsv` 第 13 列 `upeak` 对 codex 泳道口径失真——`codex exec` 全程只在
  收尾发一次 `turn.completed`（session 累计用量），`scripts/codex_jsonl_summary.py` 的
  `peak=max(i+cr+cw)` 于是＝**整场累计 input＋cache_read**（0930F：6,454,677＋6,394,496＝12,849,173），
  既不是单轮上下文峰值、cache 又被重复计入 ⇒ 「≥150k 越线点名」会对每条 codex 泳道误报。
  ⛔ 不要据此列判泳道上下文越线。

- 【谁做：Shao Peishen】【状态：⏸ 待答（本场报出）】【判据：心跳看护 automation `hr` 自 2026-09-30 08:50
  起为 `PAUSED`，答「恢复」则 30 分钟心跳复活；答「保持暂停」则只在本会话手工看护】
  【不做会怎样：无人在看泳道——FAIL／滞留分支要等下一次人工会话才发现】
- 【谁做：Shao Peishen】【状态：⏸ 待答】【判据：Q-57／Q-58 两条「答复→任务映射缺失」答 (a) 授权补映射
  后重跑生成器，或 (b) 只解阻塞不生成任务】【不做会怎样：两条答复永远不产生后续动作，队列每轮重报】
- 【谁做：Shao Peishen】【状态：⏸ 待答（09-24 到期）】【判据：Q-F5 答 (a) 派机制泳道收 8.8 一周观察结论
  并归档 `hr-wecom-aibot-liaison`，或 (b) 继续等待】【不做会怎样：该变更包卡在归档前，8.8／8.9 两章勾不上】
- 【谁做：下一条机制泳道】【状态：待派（本场新发现）】【判据：修 `scripts/codex_jsonl_summary.py` 的 `peak`
  口径（逐轮增量、不重复计 cache）并补测试】【不做会怎样：codex 泳道上下文越线判据恒假阳，
  续棒／拆分无从判断】

**12:0x 追加（Shao Peishen 答 `1b，2a，3a，4a`）**

- 🔴 **全局暂停（1b）**：automation `hr`（心跳看护）自 08:50 起保持 `PAUSED`；本轮另把
  **task-dispatcher 触发器**停掉——`launchctl disable gui/502/com.zhuopin.hr.task-dispatcher`
  （持久，重启也不起）。⛔ 两周内不会有任何自动发车／自动刷新台账；**新机制两周后替代**。
  恢复＝`launchctl enable gui/502/com.zhuopin.hr.task-dispatcher` ＋
  `python3 scripts/install_task_dispatcher.py`（幂等）。`lane-launcher`／`commit-launcher` 是
  被动型（只认请求文件），保留不动；值守类（`liaison*`）属生产监听，不在暂停范围。
- **2a 已闭环**：Q-57／Q-58 销号——`Q-53b`／`Q-54a` 映射早由 `0930B`（`7235cb8`）落地，
  `缺映射=0`；定夺队列待答 **11 → 9**（余 Q-04／Q-10／Q-16／Q-52／Q-F1–F5）。
- **3a／4a 已发车（12:03:55，pid 76998，`--only 0930H,0930I`）**：
  `0930H` 收 `hr-wecom-aibot-liaison` 8.8 一周观察结论（泳道「机制-8.8观察收尾」，
  worktree `lane-0930h-liaison-observation`）；`0930I` 修 codex `upeak` 口径
  （泳道「机制-upeak口径」，worktree `lane-0930i-upeak-caliber`）。两条均
  **`模型: Haiku`（＝Flash 档 `deepseek-flash`）**，看护为本会话手工看守（心跳已暂停）。
  ⚠️ 泳道**内部子代理**仍是 sonnet 档：`--subagent-model haiku` 不在 `lane-launcher` 白名单里，
  要降档须单独授权改白名单（属执行器改动）。

**12:09 批次收敛（`lanes-20260930-120355`，两条均 Flash 档，3 分钟级）**

- `0930H` = **PARTIAL**：8.8 一周观察结论已落档 `docs/findings/2026-09-30-liaison一周观察结论.md`
  （三条观察项＋覆盖区间＋5 条数据缺口；结论要点：窗内 5 条告警全是 `startup_gap` 重启噪音、零网络断线型
  中断；限流零命中但**结构上未接通**（⛔ 不得判「已验证正常」）；归档 10 份零重名冲突、⚠️ 附件互撞无样本）。
  tasks.md 8.8 已勾；**8.6 仍待 Shao Peishen 私信一次机器人 ⇒ ⛔ 不勾 8.6、8.9 不归档**。
  分支 `lane-0930h-liaison-observation`（`7bea9e6`）由本会话人工合回 main（`7066fdc`，PARTIAL 不在
  收口器自动合并范围）。
- `0930I` = **OK**：`scripts/codex_jsonl_summary.py` 的 `upeak` 口径归正（逐轮增量、不再重复计
  cache_read；0930F 样本 12,849,173 → **6,454,677**）＋新增 `tests/test_codex_jsonl_summary.py`；
  收口器判据 PASS 并自动合回 main（`99b70ca`），主工作区复跑 6 passed 复核通过。
  ⚠️ 遗留：`run-lanes.sh` 收工汇总里的「≥150k 越线」播报对 codex 泳道仍是按单轮累计口径比较，
  语义已在 `0930I` 的 docstring 写明，改播报口径属执行器改动，**未做**（登记待派）。
- 台账：`hr-wecom-aibot-liaison/8.8` 已标**完成**（备注写明 8.9 待 8.6）；`Q-F5` 维持待答（归档未完成）。
- 顺带修：`docs/openers/wait-lanes.sh` 判活补 `lsof` 复核（`e768abc`）——本批 12:04 它曾对运行中的
  批次误报 EXITED（Seatbelt 对 launchd 进程 `kill -0` 假阴，AGENTS.md §4 同族）。
- `0930H` 结论里登记 1 条**判据缺口**（开窗文件「误报」判据的第二分支「实际没有消息被漏收」服务侧
  不可判定）——只登记未动，属改判据、超出 3a 授权。

**13:4x 追加（Shao Peishen 答 `1a，2a，3a`）**

- **1a 已执行（主会话直执，按 `0930G` 先例）**：
  - `docs/openers/run-lanes.sh`：**codex 泳道第 13 列 `upeak` 恒写 `-`**——codex 的 JSONL 没有单轮
    口径（只有收尾一条累计 usage），照 claude 分支同一条规矩「不猜、不拿累计量充数」；收工的
    「≥150k 转场线」播报因 `$13!="-"` 自动跳过 codex 泳道，不再整批误报。列定义注释同步写明。
  - `tests/test_run_lanes_model_codex.py`：既有用例加断言（两泳道第 13 列均 `-`）＋新增回归
    `test_codex_peak_column_stays_dash_and_no_150k_false_alarm`（复刻 0930F 累计样本：第 8 列仍记
    6,454,677、第 13 列为 `-`、stdout 无「150k」）。run-lanes 全家族 54 passed／含 dispatcher 86 passed。
  - `docs/tech-debt.md`：**TD-55**（值守「误报」判据第二分支服务侧不可判定，`0930H` 登记）＋
    **TD-56**（codex 泳道无单轮上下文峰值，转场只能靠 `CTX-RELAY` 哨兵）。
- **2a 已建**：到期提醒 automation `10-14`（heartbeat，週频；到期前静默，2026-10-14 起提醒一次后自停）。
- **3a 进行中**：8.6 第 1 条链路（私信发文档→归档）待 Shao Peishen 私信机器人发一个文件——TD-51
  已还、`ATTACHMENT_FIELD_PATHS_BY_MSGTYPE["file"]` 映射在，帧一落地即可派 Flash 泳道自测；
  ⚠️ 第 3 条链路（群通知→回推）仍需他填 `HR_LIAISON_GROUP_WEBHOOK`（`Q-10`，⛔ 泳道不代填凭据）。

**14:0x 追加（Shao Peishen 答 `1a，2a`）**

- **1a（三条链路一次验完）**：前置是他在 `.env` 填 `HR_LIAISON_GROUP_WEBHOOK`——截至 13:4x
  **该键仍不在 `.env` 里**。⇒ 他填好 + 私信发文件后：先 `launchctl kickstart -k gui/502/com.zhuopin.hr.liaison`
  重启值守（确认 `state=running` 与 `liveness.json` 新 `since`），再发车 `0930J`。
- **2a 已执行（Q-65 根因修复，主会话直执）**：`docs/openers/run-lanes.sh` 在 codex worktree 泳道的
  判据块改写里，把 `./venv/bin/<工具>` 定向到 `"$HR_GATE_MAIN/venv/bin/<工具>"`（worktree 里没有
  gitignore 的 `venv/`，0930I 首例是靠现场建 venv 绕过）；新增回归
  `test_codex_worktree_gate_redirects_local_venv_to_main`（主仓 venv 桩＋worktree 判据块：断言改写后
  路径存在、`./venv/bin/python` 不再出现在生成的判据脚本里、泳道判 OK）；run-lanes 家族＋dispatcher
  **133 passed**。⇒ 后续 codex 泳道的判据块可以直接照常用 `./venv/bin/python`。

**15:3x 追加（Shao Peishen 答「好了」＝两件前置已做；`0930J` 已发车并收敛）**

- **前置已到位**：`HR_LIAISON_GROUP_WEBHOOK` 在根 `.env` 与 `tools/liaison/.env` 两处都已配置
  （89 字符，取值未入档）；邵培申 2026-09-30 **15:22:27** 私信机器人发来 `10个岗位需求.doc`（42,496 B）。
- **值守已重启**：`launchctl kickstart -k gui/502/com.zhuopin.hr.liaison`（pid 9825，断线 3 秒）——
  `liveness.json` 新 `since`＝`2026-09-30T15:24:09`，`state=connected`。
- **`0930J` = PARTIAL（Flash 档，2 分钟，分支 `lane-0930j-aibot-gray-selfcheck` `1670d3c` 已人工合回
  main `973207c`）**，结论写进 `docs/findings/2026-09-30-8.6灰度自测.md`（344 行）：
  - **链路一（私信发文档→归档）✅ 首次端到端实证通过**：帧入库（`msgid=33a567cc…`）、附件真落盘
    42,496 B、`attachments_json` 非空（filename／relative_path／byte_length／sha256）、SHA-256 与台账
    逐字一致、魔数 `d0cf11e0a1b1ae11`＝OLE2/doc ⇒ **TD-51 的字段映射在生产上第一次真正跑通**。
  - **链路二（群消息→入队）✅**：`liaison_task` 12 行、来源 `msgid` 可回指；如实登记 12 行
    `send_status` 全 `pending`（推送状态属链路三）。
  - **链路三（群通知→回推）❌ 未实证，且成因变了**：webhook 已配置，但 `liaison_group_notify` 恒 0 行、
    `send_group_notify()`（唯一写台账的门面）**在生产代码零调用点** ⇒ 这不是「缺凭据」，是**尚未接线**。
    另一条真实外发路径是 `python -m tools.liaison send-followup --send`（跟进信群发），属对外发送。
  - **恒等不变式 ✅**：按 `EFFECT_NODE_TO_TABLE` 逐 thread 比对，相等 10 组／不等 0 组。
  - **8.6 ⛔ 不勾**（2/3），`tasks.md` 第 238 行原样保留 `- [ ]`；8.9 归档因此仍不跑。
- 待办（`0930J` 的「需你定夺」原样转记）：① 链路三接线（派泳道补「入队后回推值守群」）／重定义
  8.6 验收面为两条后勾／继续挂起；② 本 findings 结论是否补进 `tasks.md` 8.6 注记（推荐补）。

**19:5x 追加（Shao Peishen 答 `1a，2a`）：`0930K` 接线已合回 main**

- **`2a` 已做**：`openspec/changes/hr-wecom-aibot-liaison/tasks.md` 8.6 注记已补（`0930J` 结论 2/3＋
  链路三成因改为「未接线」＋恒等不变式 10/0），随 `33624d2` 落档。
- **`1a` 已做**：`[Mac]0930K` 接线泳道 **OK（11 分钟，Flash 档）**，改动落在
  `tools/liaison/__main__.py`（+71 行）、`tools/liaison/notify/{__init__,guard,relay,store}.py`、
  `tools/liaison/tests/`（新增 `notify/relay.py` 162 行）——群帧**入队成功后**回推、私信不回推、
  复用既有幂等（`effect_send_group_notify`）与限流门面。代提交 `e42d6ae`，执行器自动合回 main
  （`ff11df4`）。
- **判据补跑（看护者真身核验）**：因我把 opener 的提交排在发车之后，泳道 worktree 里**没有**
  `docs/openers/0930K-*.md` ⇒ 执行器 `gate_extract` 找不到判据块、**机器判据没跑**（`gates.tsv` 空）。
  我在 main 上按 opener 的判据块原样补跑：**`tools/liaison/tests` 1223 passed / 5 skipped**
  （基线 1216／5，+7 条新用例）＋「回推接线结构 OK」，rc=0。
  🔴 **教训（下批照做）**：新 opener 必须先落档（提交推送）**再**发车，否则 worktree 里看不到它。
- **值守已二次重启**吃进接线：`pid=37076`、`since=2026-09-30T19:47:37`（15:24 那次是吃 webhook）。
- **链路三的真实投递验证待做**：需要 Shao Peishen 在值守群发一条文字（他本人 `ShaoPeiShen` 在白名单里）
  ⇒ 系统入队后应自动回推一条群通知 ⇒ 核 `liaison_group_notify` 新行＋日志 ⇒ 才勾 8.6、随后跑 8.9 归档。

**23:5x 追加（Shao Peishen 答 1a）：`hr-wecom-aibot-liaison` 整包闭环归档** 🎉

- ✅ **链路三真投递**：他 23:48:30 在值守群发文字（`msgid=e30158528d8f959cc0a3511dd7960319`）⇒ 入队
  `liaison_task` #13 ⇒ **回推真投递**：`liaison_group_notify` 首行 `state=sent`／`channel=group_webhook`，
  正文「【值守通道·新任务已登记】来源会话：wrvDL_… 来源消息：e30158528d… 摘要：@MAC机器人 8.6链路三测试」
  ——该表自建成以来**第一行**。
- ✅ **8.6 勾（三条验收面 3/3）**：链路一 15:22 私信 doc 落盘＋sha256 逐字一致；链路二 群文字入队；
  链路三 回推真投递；恒等不变式 10 组相等／0 不等。
- ✅ **8.9 当场跑**：`openspec archive hr-wecom-aibot-liaison -y` —— 5 个 delta spec
  （`liaison-channel-session`／`liaison-group-notify`／`liaison-inbound-whitelist`／
  `liaison-message-archive`／`liaison-task-queue`）同步进 `openspec/specs/`（+27 requirements），
  变更包移入 `openspec/changes/archive/2026-09-30-hr-wecom-aibot-liaison/`；主会话直执，提交 `7be4062`。
- **台账已手工对齐**（生成器对「来源已消失」条目默认保守留旧状态）：8.6／8.9／U8／
  `change:hr-wecom-aibot-liaison` 四条改完成并写备注；重跑生成器后 hr-wecom 家族 **19/19 完成、无 conflicts**，
  全表 conflicts 13→9。`Q-10` 已按「已完成路径 a」作废销号。
- ⚠️ **登记未改（新发现）**：回推后 `liaison_task.send_status` 仍为 `pending`——0930K 只落
  `liaison_group_notify` 终态、**未回写队列行** ⇒ 队列视图会显示「未推送」。通知确已送达，影响面小，
  建议另开一条小泳道对齐口径（⛔ 未擅改）。

**23:5x 追加（Shao Peishen 答 1a）：`0930L` 回写接线已合回 main**

- **`0930L` = OK（2 分钟，Flash 档）**：`tools/liaison/__main__.py::_push_group_task_notice` 接住回推返回值，
  **只在终态 `STATE_SENT`** 时调既有 `queue.mark_task_pushed`（幂等键
  `{thread_id}:effect_mark_task_pushed:{msgid}` ＋ `send_status <> 'pushed'` 两道防线，⛔ 未另写 UPDATE）；
  `rejected`／`pending_resend`／幂等命中（`None`）一律不回写；`pushed_at` 取 `session.format_instant(moment)`
  （与同一条消息的 `received_at` 同源同格式）。测试 **1226 passed／5 skipped**（+3 条）。
  ⚠️ 本次机器判据**自动跑过**（`gates.tsv: PASS`）——因为按 `0930K` 的教训**先落档再发车**（opener `572bc62`）。
  代提交 `95e336c`，收口器自动合回 main。
- **值守第三次重启加载新代码**：`pid 11877`、`since 2026-09-30T23:56:13`。
- **历史一行已补齐**：`#13`（23:48:30 那条，接线前推的）用**同一套幂等**补成
  `send_status='pushed'`、`pushed_at=2026-09-30T23:48:30.770486+08:00`（事件时刻），
  `effect_log` 现有 1 行 `effect_mark_task_pushed`。
- **待验证**：请 Shao Peishen 再发一条群消息（值守此刻已加载新代码）⇒ 应**自动**回写 `pushed`；
  验到即闭环，否则按 PARTIAL 登记。

**00:0x 追加（2026-10-01，Shao Peishen「已发」）：自动回写实测通过 ✅**

- 他 **2026-10-01 00:02:32** 在值守群发「@MAC机器人 回写验证」（`msgid=2aa8deae565d0de3…`）⇒
  入队 `liaison_task` **#14** ⇒ 群通知回推 `state='sent'`（`liaison_group_notify` 第 2 行）⇒
  **值守自动回写**：`send_status='pushed'`、`pushed_at=2026-10-01T00:02:32.199461+08:00`
  （事件时刻，与 `received_at` 同源同格式），`effect_log` 新增
  `wrvDL_…:effect_mark_task_pushed:2aa8deae…`。
- 至此链路三的**真投递**与**队列状态回写**两环都在生产上实证；`effect_mark_task_pushed` 现 2 行
  （#14 由值守自动、#13 本场幂等补齐）。`hr-wecom-aibot-liaison` 归档后的两个增量
（`0930K` 接线、`0930L` 回写）**均已闭环**，无遗留项。

**00:1x 追加（2026-10-01，Shao Peishen 提）：回推文案可读化 `[Mac]1001A` 已合回 main**

- 他看过群通知后要求「来源会话／来源消息不要一串数字，可读一点」。`[Mac]1001A`（Flash 档，2 分钟，**判据自动 PASS**）
  把模板改成：

  ```
  【值守通道·新任务已登记】
  来源：邵培申（群消息）
  时间：2026-10-01 00:02
  摘要：@MAC机器人 回写验证
  追踪号：e3015852
  ```

  人名取 `load_whitelist_names`（取不到回退 `userid`，⛔ 不抛）；渠道标签由 `frames.frame_chattype` 判定
  （⛔ 不用 `thread_id` 形状猜）；追踪号只留 `msgid` 前 8 位（grep 得到、够对账），⛔ 完整 `msgid`／`thread_id` 不进群消息。
- **一处已留痕的保守偏差**：`时间` 取**该消息首次归档的 `received_at`**，而不是本次处理时刻 `moment`——
  因为回推文本本身进幂等键摘要（`{thread_id}:effect_send_group_notify:{摘要}`），若文本随处理时刻变化，
  重试会算出新键 ⇒ 重复发群消息。以原文为准既稳定又对账直观。
- 测试 **1228 passed／5 skipped**（+2 条）；代提交 `ec5ca93`，收口器自动合回 main。
- **值守第四次重启**加载新文案：`pid 13755`、`since 2026-10-01T00:11:00`、`connected`。
  ⇒ 群里**下一条**通知即新格式（想立刻看到就再发一条测试消息，下次自然消息也会是新格式）。

**2026-10-07 15:0x 追加（Shao Peishen：「继续泳道看护，业务范围内能推进就推进」）**

- **接手核对**：无批次在跑；`ready=4` 全是业务 gate（等 M2 U5／U1 等上游）；把 M2 依赖链逐条算了一遍——
  **主线 U4→U5→U6→U7 全链卡在 U0**，而 U0 ← 1.2／1.6／1.7 ＝ **Q-06 真实脱敏样本／Q-03 试运行岗位／
  Q-05 定型签认**三件在 Shao Peishen 手上（另有汤丽萍回件、M1 9.1、M3 采购暂缓）。
- **本轮推进（挑出两件不吃主线依赖的 M2 条目，一条泳道交付）**：
  - `[Mac]1001B`（Flash 档，1 分钟，判据自动 `PASS`）＝ M2 **7.1**「判例批改表」xlsx 模板
    （`docs/templates/m2-判例批改表.xlsx`，11 列＋示例行、冻结首行、时刻列文本格式）＋
    `docs/templates/m2-判例批改表-填写说明.md`（≤20 行/样本、类别非 live、脱敏、导入校验口径）——
    **这正是 Q-04 要发给汤丽萍的标注模板**；以及 M2 **8.6** `docs/compliance/README.md` 索引
    （三份草稿：PIA／同意条款／留存策略，均「草稿·待签认」、对接人谷雨、起草日 2026-09-17、对应 Q-02）。
    代提交 `1286a75`，收口器自动合回 main。
  - 台账对齐：7.1／8.6 标**完成**并写备注（生成器对「来源已消失」条目保守留旧状态，手工改）；全表 conflicts 11→9。
  - 队列：**Q-65 作废销号**（已由 `[Mac]0930R` 答 2a 的执行器侧根治 `2f00d4f` 处理，不必再逐条改 opener 模板）。
- **仍缺 Shao Peishen 的输入（主线停下等）**：Q-06（真实脱敏样本来源与脱敏方式）、Q-03（试运行岗位，哪个仍在招）、
  Q-05（U0 1.7 定型签认）；另有 M1 `9.1` 画像质量验收（10 个真实历史岗位重跑＋HR/业务经理评估）、
  M3 语音主机采购已答「暂缓」、四条新场景等汤丽萍回件。
- **待他确认（仓库外）**：心跳 `hr` 与 task-dispatcher 是否恢复（当前 `PAUSED`／`disabled`）。
- 队列再清两条过期项：**`Q-F4`／`Q-F5` 撤出「远期定夺」并在「已答/作废」留闭环记录**（Q-F4 问的「是否启动 M3
  与四场景」早已放行、M3 与四场景都已开班；Q-F5 问的「收 8.8 观察结论⇒勾 8.8⇒归档」已于 09-30 全部完成）
  ⇒ 待答 **8 → 6**（余 Q-04／Q-16／Q-F1／Q-F2／Q-F3／Q-52，全部真需他或汤丽萍）。

**2026-10-07 16:0x 追加（Shao Peishen：答 `1b，2b` ＋ 转 PRD/UI 两项）**

- **答 1b**：⛔ 不写给汤丽萍的跟进稿（他自己处理对外沟通）。
- **答 2b**：⛔ 心跳 `hr` 与 task-dispatcher **保持暂停**（当前 `PAUSED`／`disabled`，未恢复）。
- **引擎/流程四问的答复口径**（证据见 `08-PRD.md` §9）：新引擎泳道 ✅ 12 条 codex 泳道实跑；
  workflow 🟡 主干打通、**调度器自动发车分支与拆件 codex 版实跑两处未实证**；机器人监听 ✅（10-01 起连续在跑、
  收件/归档/回推/回写全实证）、拆件 ⏸（最后实跑停在 09-24 的 claude 版）；任务队列 🟡 双轨正常
  （`liaison_task` #13/#14 已 `pushed`；任务驱动调度器 Codex 版已跑 2 轮，自动发车未触发）。
- **本轮交付**：**`08-PRD.md`（221 行）＋ `09-UI-Design.md`（256 行）v0.1**（首次正式整理；README 已加索引）。
  两份都遵循「不新造决策」：未定事项分别进 PRD §10（D1–D5：北极星指标／HR 负责人／M1 9.1 验收清单／
  M2 三样输入／四场景 G2 放行）与 UI §13（U1–U5：共用样式层／品牌色／暗色／移动端范围／是否引第三方框架）。
- **按他指示**：泳道看护暂不推进具体业务任务，等他评审这两份文档。

**2026-10-07 16:3x 追加（Shao Peishen 答 `1a，2a，3a，4a`：PRD/UI 定稿）**

- `08-PRD.md` → **v1.0 定稿**：北极星＝「HR 处理每份简历净耗时 ↓」＋「简历沉淀率 ↑」；HR 负责人先只留角色位、
  暂不推送；D3／D4／D5 仍挂（缺的是材料：M1 9.1 验收清单、M2 三样输入、四场景 G2 放行）。
- `09-UI-Design.md` → **v1.0 定稿**：引入极简 `app.css`（无框架/无构建链）、⛔ 不引第三方库、品牌色沿用
  `#0d6efd`（等 VI 色值再换）、⛔ 不做暗色、移动端只保候选人两页（同意/邀约）窄屏可用。
- 落地第一步已发车：`[Mac]1001C`（泳道「机制-UI共用层」，Flash 档）＝ 新建 `app/web/static/app.css`
  （§3 令牌＋§6 组件类）＋8 页加 `<link>`＋三页（resume_review／resume_list／upload）样式收敛；
  判据含 7 个前端测试文件 + 令牌/链接/字面量收敛断言（见 opener §三）。

**2026-10-07 17:3x 追加：`1001C` 已合回 main（a48188f）＋ 揪出并修掉一个「只在主工作区才炸」的坑**

- `1001C` 泳道自报 **PARTIAL**，但**产物是对的**：它发现我 opener 里判据写错——页面挂在根路径
  （`/`、`/login`）而 `<base href>` 是 `/hr/recruit-agent/`，所以链接必须写 `href="static/app.css"`
  （`app.css` 会 404）；它按红线选了正确形态并做了端到端实证（8 页逐一按 `<base>` 解析请求 ⇒ 8/8 → 200）。
  ⇒ 我订正 opener 判据（`098a0d8`）→ 人工合并分支（`a48188f`）→ **在 main 上补跑订正后的判据：90 passed ＋「UI 共用层 OK」**。
- 🔴 **顺带揪出的真坑（与 1001C 无关）**：合并后在 main 上跑判据时 `tests/test_web_api.py` **46 条红**——
  根因是**仓库根 `.env` 里那行 `HR_LIAISON_GROUP_WEBHOOK`**（2026-09-30 我让他加的，实为 liaison 的键）。
  `app/config.py::Settings` 是 `env_file=".env"` + **extra=forbid** ⇒ `Settings()` 直接抛
  `ValidationError: Extra inputs are not permitted`。⚠️ **泳道看不见它**：判据在 worktree 里跑，而 worktree
  不带 gitignored 的 `.env`。
  - **处置**：根 `.env` 里那行**注释保留原值**（⛔ 不删凭据）+ 加 3 行说明；`tools/liaison/.env` **一字未动**
    （值守本就读它，CLI 自己打印「⛔ 仓库根的 .env 不再被本服务读」）。修后那 7 个判据文件 **90 passed**。
  - **止损**：全量 `pytest tests/ tools/liaison/tests` = **3819 passed / 10 skipped**（102s）。
  - **回归**：新增 `tests/test_root_dotenv_keys.py`（两条：键名白名单 + 真构造一次 `Settings()`），
    且用"临时塞一个未知键"做了负例验证（两条都红，还原后绿）。
- 结论：`09-UI-Design.md` §11 ① 已落地（共用样式层在 main 上生效）；② 逐页对齐（M2 工作台三页）待那三页有实现后再做。

**2026-10-07 17:5x 追加：`1001D` 已合回 main（e55be73）——UI 收尾第 ② 步（现有 8 页）完成**

- `app.css`：新增 `.notice-success`／`.notice-danger`、`img, table { max-width: 100% }`、
  `@media (max-width: 480px)`（`.btn` 全宽、表格横向滚动、容器内边距收窄）。
- `index.html`：**33 处颜色字面量（14 种色值）收敛**为共用类／令牌（`.notice*`／`var(--color-warn-*)`／`.badge`／`.btn*`），
  ⛔ 文案与 JS 一字未动；三面试页加 viewport meta（3/3）。
- 新增 `tests/test_candidate_pages_narrow.py`（3 条：候选人两页 viewport／三页无 >375px 固定宽度／媒体查询给 `.btn` 全宽）。
- 判据：**117 passed ＋「UI 逐页对齐与窄屏 OK」**（执行器自动 PASS 并合回）。
- ⚠️ **375px 是静态判据通过**：仓库 venv 无 playwright、无浏览器二进制 ⇒ **浏览器/真机实测未做**（已如实登记，
  见 opener §五红线：不许声称"已实测无横向滚动"）。
- 至此 `09-UI-Design.md` §11 ①② 全部落地；§10 清单里 ⑤（破坏性动作二次确认）要等 M2 批量确认页实现后才具备验收对象。

**2026-10-07 18:2x 追加（Shao Peishen 答 `1a，2a`）：`1001E`／`1001F` 两条泳道补跑闭合**

- 两条泳道都撞在**同一个环境墙**（都如实 PARTIAL、产物都对）：
  ① `1001E` 真 LLM 跑不了——**codex `--sandbox workspace-write` 禁网**（`CODEX_SANDBOX_NETWORK_DISABLED=1`），
  而 `AGENTS.md` §4 原写「真实泳道由 launchd 发起不受此限」**不成立**（禁网的是 codex 自己的沙箱层，与 launchd 无关）；
  ② `1001F` 起不了 Chrome——同一层 Seatbelt 拦浏览器进程（`SIGABRT`）。
  ⇒ 已按 0930F 先例：人工合并分支，再由看护者在**非沙箱**环境补跑缺失步骤。
- ✅ **M1 9.1 机械部分闭合**：`--extract-doc`（10 份 JD，从归档 `10个岗位需求.doc`）→ `--live`
  **10/10 全通**（`deepseek-flash`，各岗位 3–7 个字段，5.1–11.9s）→ 生成
  `docs/findings/2026-10-07-m1-9.1-画像重跑结果.md`（脚本生成，⛔ 不手改）＋评估表 AI 值列预填（61 行）。
  **剩人评**：HR／业务经理按 `docs/templates/m1-画像技术栈评估表.xlsx` 打勾，≥80% 即 9.1 通过。
- ✅ **375px 真机实测通过（并修掉一个真缺陷）**：补跑第一次即复现——两页无横滚（`scrollWidth=375`）但
  「签发」按钮仅 **42.67px**、`#code-btn` 82.66px；修法＝`app.css` 窄屏媒体查询按 id 给五个裸按钮
  `width:100%; font-size:16px`（⛔ 不挂 `.btn`，它的 14px 会压破 §8 的 ≥16px）＋
  `tests/test_candidate_pages_narrow.py` 增一条钉住这五个 id；**复测 rc=0**（按钮 335px 全宽）。
- 🔴 **机制缺口（登记，待你定夺）**：需要联网/起浏览器的活，**codex 泳道做不了**。两条路：
  (a) 给 `run-lanes.sh` 的 codex 参数加 `-c sandbox_workspace_write.network_access=true`（改执行器，需你点头）；
  (b) 维持现状：这类活固定走「主会话非沙箱补跑」（本轮的形态）。

**2026-10-07 18:4x 追加（Shao Peishen 答 `1a，2a`）**

- **1a 已落地（`cf609e8`）**：`run-lanes.sh` 的 codex argv 加 `-c sandbox_workspace_write.network_access=true`
  ——codex 的 workspace-write **默认还禁网**（`CODEX_SANDBOX_NETWORK_DISABLED=1`），这是 `1001E`（真调 LLM）
  与 `1001F`（起 Chrome）双双失败的根因，且失败形态像业务 bug。写入仍锁 `run_dir`，不扩大信任面
  （泳道本来就以无头会话在调 LLM）。`tests/test_run_lanes_model_codex.py` 补 argv 断言，run-lanes 家族 **49 passed**。
  ⇒ 以后 M2 的模型对比/评测类泳道可在泳道内直接联网跑，不必再人工补跑。
- **2a 已交付**：`docs/templates/m1-画像技术栈评估表-转发说明.md` —— 含**可直接粘贴的企微消息**（带 `<称呼>`／
  `<截止日>` 占位）、评估人填写口径（同义/大小写/中英混写算命中；「未提及」而 JD 里有 ⇒ 未命中）、
  回收流程（原文件回传 ⇒ 看护者只统计落档、⛔ 不代填）。⏭ 待 Shao Peishen 指定评估人并转发。
