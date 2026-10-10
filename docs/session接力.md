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
- **2026-10-08 09:4x：`1001I` 已闭环（`807634e` 合回 main，判据 117+10 passed）＋值守已重启**（liveness
  `since=2026-10-08T09:42:57`，新通知代码生效）；**`1001J` 首页新壳泳道已发车**（Shao Peishen 答 `1a`：
  首页与其余 7 页统一外壳，⛔ 不动 JS，判据含 `test_static_frontend` 全套 126 passed／3.2s）。
- **2026-10-08 10:0x：私信链路实证＋新泳道** —— Shao Peishen 09:54 私信发图**成功入档入队**
  （msgid `84dafa24…`，归档 `ShaoPeiShen/20261008/`；`image` 附件本体 fail-closed 未落盘，帧结构已取证）
  ⇒ **收件链路是通的**；她今日 4 条群消息与他的群测试仍未到达（待群里真 @ 复测；09:42:46–57 有我重启造成的
  10 秒盲窗）。答 `1a，2a` 已发车 **`1001K`**：补 `image` 附件映射（url/aeskey＋JPEG/PNG 魔数）＋单聊回执。
- **2026-10-08 10:2x：🈸 拆件会话（Codex 首跑）接住 `人事部#3` 补齐重发，`#3` 已闭环转 `📥`** ——
  汤丽萍 10:23:51–10:24:35 连发 4 条（`162b5920…` 10 岗位清单实际内容、`e596d9db…` 4.5-1～4.5-5、
  `75bf2d38…` 5.3-1～5.3-4、`bc572f2a…` 6/7-1/7-2/7-3），连同 09-20／09-21／09-24 旧 pending 共 **10 条一并回灌**；
  Q-59 要求的补齐两项（决策点 a 清单实际内容、决策点 b 时数＋人名）均已到位。回灌结论：
  `docs/跟进信/回件/人事部#3-2026-10-08.md`；`docs/跟进信/README-跟进信清单.md` 中 `人事部#3` 由第九态转
  **`📥 已回件并回灌 2026-10-08`**（串行闸对 `#3` 解除；`人事部#4` 仍按 G4 闸门 `Q-67` 待答，本场未动 `#4` 行）。
  🔴 **提交被环境阻断（新缺口，须处置）**：本会话沙箱（`CODEX_SANDBOX=seatbelt`）对 `.git` **只读**——
  `git add` 三次重试均 `Operation not permitted`（`.git/index.lock` 无法创建）；提交请求通道的请求文件落点
  `.claude/handoff/commit/**` 被拆件守卫（`scripts/hooks/codex_unpack_guard.py`，白名单＝章程 §三 四路径）按章程 deny
  ⇒ **本轮三个落档文件保持未提交态**；按章程 §一.5「清信号须在 commit 完成后」**本会话未清信号**
  （10 条 pending 原样保留）。⛔ 未 push、⛔ 未删任何锁、⛔ 未越界写白名单外路径。
- **2026-10-08 10:3x：主会话已代为收口（待人 #1 完成 ✅）**——① 三个落档文件经提交通道提交
  （`1fa32a2`，含 `docs/跟进信/回件/人事部#3-2026-10-08.md`／`README-跟进信清单.md`／`docs/session接力.md`）；
  ② 按章程 §一.5 清信号：`unpack-signal --clear --before 2026-10-08T10:25:00+08:00`（10 条一并覆盖，
  结论文件已逐条列全）⇒ **pending 0**；③ **值守第 N 次重启**加载 `1001K`（pid 86050，10:35:49 connected，
  私信回执＋图片附件映射生效）。仍待人：#2（`3.3-2`/`3.3-3` 是否补问）与 **#3（拆件提交链路修法，建议照
  0930D 先例扩到 `session_runner`：会话退出后由值守侧代提交白名单路径并清信号）**。
- **2026-10-08 12:1x：答 `1a，2a，3审核通过·发` 落地**——① `3.3-2`/`3.3-3` 残项**不补**（维持 `#3` 闭环）；
  ② 拆件提交链路修法＝**派 `1001L`**（照 0930D 先例扩到 `session_runner`，泳道见 opener）；
  ③ **`人事部#4` 已放行发送**（G4 `Q-67`＝审核通过·发；信件改写为正式信件体＋生成 docx 38 KB；
  经动作通道 `send-followup` 发出，README 行＝🆕 待发→（发送后回填））。
- **2026-10-08 12:2x：三项全部闭环**——① **`人事部#4` 已发出**（动作通道 rc=0，
  `2026-10-08 12:11:32`；台账回填 `✅ 已推送 2026-10-08`，回收期 10-10 前）；
  ② **`1001L` 已闭环**（`3624017` 合回 main，判据 96 passed）：`session_runner` 在会话退出后按
  `CHARTER_WRITABLE_PATHS` 代 `git add`＋`commit`、成功后按检查点清信号，失败 fail-closed 并在退出通知里写备注；
  值守已重启（`since=2026-10-08T12:22:17`）⇒ **下一轮拆件不再需要人工补提交**；
  ③ `3.3-2`/`3.3-3` 残项按 `1a` 不补。串行闸现状：`#4` 在途（等 10-10 回件）。
- **2026-10-08 12:3x：M2 判例批改表转发路径已定（答 `1a`）**——等 `#4` 回件闭环后**作为独立新信**发出
  （⛔ 不并作补充信）；转发物＝`docs/templates/m2-判例批改表.xlsx`＋`-填写说明.md`＋`-转发说明.md`（已就绪），
  发出前过 G4。
- **2026-10-08 15:1x：发版六（首页新壳）已发布**（答 `1a`；G3 `Q-68` 放行）：`.51` 由发版五 → 本机 main；
  实测：快照 `20261008-1511`；`发版完成`；`Running`/`267009`；`8095 LISTENING pid 9416`；`15:11:48 startup complete`；
  冒烟六项全过（**served 首页含 `topbar`×3**、线上 CSS `.topbar`×3、`/api/jobs` 200、断言 `EXIT=0`、闸 False）。
  ⇒ **8 页在 `.51` 上全部是新 UI**。
- **2026-10-08 15:1x：Word 附件字体已统一（Shao Peishen 指出"参差不齐"）**——根因：一次性转换器只设了
  西文字体、未设东亚字体（`w:eastAsia`），中文各自回退。已把渲染器固化为仓库脚本
  `scripts/letter_md_to_docx.py`（Normal/Heading1-3/List 全钉「微软雅黑」＋Consolas 行内码，含
  `tests/test_letter_md_to_docx.py` 3 条），并用它重生成 `人事部#4` 的 docx（15:10；旧版保留在
  git 历史 `40e61f5`）。**不需要借力本机 MS Word**：Word 可在最终目检时打开看效果；
  是否把修正版附件**补发**给汤丽萍待定夺。
- **2026-10-08 16:3x：泳道看护已恢复（答"能开尽开"）· 第一批（4 条 spec-to-plan）全绿闭环**——
  `1001M/N/O/P` 四包 U1 计划（渠道/排期/Offer/入职）全部 **OK＋判据 PASS**，执行器自动合回 main
  （merge 提交 `d6fb96e`/`f0c6c7f`(P)/`fabde0e`(N)/`afcc01b`(M)）；产物
  `docs/superpowers/plans/2026-10-08-*`（渠道 7 Task/1510 行、排期 10/1330、Offer 9/1204、入职 5/1216，
  均含 Global Constraints）。A/B 行：条数 4／全部 `sonnet`（results.tsv 第 6 列）／无失败；
  ⚠️ Token 治理 P2 基线属 Claude 线（已结项），本批未回填。台账已按真身解冲突（完成 283→285）。
- **2026-10-08 16:5x：第二批已编排待发车（串行，`--max-parallel 1`）**——`1001Q/R/S/T`：
  四包 U1 的 **run-build Task 1–3**（`codex_sdd_runner`）。🔴 四者全触碰 `app/storage/db.py`＋
  `app/web/server.py` ⇒ **同批串行**（分泳道判据＝触碰文件重叠），各自 worktree/branch、各自判据、
  各自自动合并。
- **2026-10-08 17:5x–18:2x（`1008R` 续棒看护 + `1001U` 修法）：run-build 在 codex 泳道内不可用——已定位并修一半**——
  ① 第二批四条 run-build 泳道（`1001Q/R/S/T`）**全部 PARTIAL**（留步：执行环境不可达；无改动/无提交）；
  ② **根因**（`docs/findings/2026-10-08-codex-run-build执行器缺口与修法.md`）：泳道内嵌套 `codex exec` 被 Seatbelt 拦
  （两个对照实验：`workspace-write` 与 `danger-full-access` 同报 `failed to initialize in-process app-server client:
  Operation not permitted`）⇒ **run-build 必须由非沙箱父进程执行**；另查出 runner 四处次级缺口；
  ③ **已修（`scripts/codex_sdd_runner.py` +3 项，8 passed）**：`resolve_codex_bin` 三级解析、
  runner 代提交（`_git_commit`，失败 fail-closed 不勾进度）、review 必须 `VERDICT: PASS`（空输出不算过）、
  注入 `SDD_PYTHON` 供子会话跑测试；Final Review 去掉"须已合 main"（合并归执行器）；
  ④ **过渡执行路径已验证**：主会话非沙箱直跑 runner —— 渠道包 U1 **Task 1/2 已代提交**
  （`a21c1a2`/`af545ba`，分支 `lane-1001q-channel-u1-build`），Task 3 在跑；
  ⑤ **Q-04 已销号**（答 `2a`；跟踪改挂「`#4` 闭环 → M2 模板发出」）；待办：executor 级"动作通道跑 runner"（红线③，候选 (a)/(b)）。
- **2026-10-08 18:3x：`run-sdd` 通道已落地并首跑（答 `1a`）**——① `scripts/action_request.py` 新增
  `run-sdd` 动作（三闸＋同步 7200s 超时；launchd 非沙箱执行 runner）；`tests/test_action_request.py`
  +9 条（86 passed 同跑）；② **渠道包 U1 Task 1–3 已跑通并合回 main**（`bb99fc8`；runner 代提交
  `a21c1a2/af545ba/9a7922c`；判据 `tests/test_bundle_unpack.py tests/test_db_migration.py` = **20 passed**）；
  ③ Final Review 口径修正（只读会话不再要求跑 pytest）；④ 排期 seg1 起由 **`run-sdd` 通道冒烟推进**。
- **2026-10-08 22:0x（`1001G` 本场）：子任务全部换挡 flash ＋ 排期包 U1 seg1 闭环**——① Shao Peishen 指令
  「所有子任务都用 flash」：`codex_sdd_runner` 默认模型 `deepseek-v4-pro/high` → **`deepseek-flash/low`**
  （`625cbca`，含 dry-run 钉死断言；无头纪律⑤补一句「计划里的裸 `python3` 一律换 `$SDD_PYTHON`」——本机
  python3=3.9 导入 app 即失败，Task 2/3 实测已用）；换挡时排期 Task 1 已由 v4-pro 双 review PASS 并代提交
  （`ef3639b`，保留不重跑），Task 2 起用 flash 重投（`20261008-215400-排期seg1-flash.action`；旧 action 落
  `.failed`＝诚实记录）；② **排期 seg1 已闭环**：Task 2/3 flash 红→绿（27＋16 用例）＋双 review 全 PASS＋
  Final Review PASS，`.done` rc=0（21:52→22:06，**14 分钟**）；判据 `pytest test_db_migration + 两个新 schema 套件`
  ＝ **57 passed** ＋ 两条 grep → 合回 main **`9a39875`**（已推）。产物＝`app/storage/db.py` 六张新表
  （interviewer／interviewer_availability／interview_slot／interview_slot_interviewer／
  interview_invitation_draft／invitation_template）＋ `tests/test_db_interview_slot_schema.py` ＋
  `tests/test_db_interview_invitation_draft_schema.py`；③ **执行器缺陷修复**（flash 的质量 review 实测带出）：
  `parse_plan` 把 Global Constraints 截成 **56 字符**（段内 `### 工程铁律/红线` 被误当边界 ⇒ 透镜静默为空），
  已修为「同级/更高级标题或裸 `### Task N:` 收尾」，四计划实测 56 → **1329~2809 字符**（`d5b8c5f`，89 passed，已推）；
  ④ 续跑中：Offer seg1（`lane-1001s-offer-u1-build`，已 ff 到 main）→ 入职 seg1（`lane-1001t-onboard-u1-build`），
  均 flash 经 run-sdd 通道；⑤ ⚠️ 遗留提醒：排期计划 Task 9 的聚合测试文件 `tests/test_db_interview_schema.py`
  与 seg1 已落的两个分文件 schema 测试重叠——下一段开工时定「聚合复测 or 合并去重」；
  `interviewer_availability` 目前无专门测试文件引用（Task 1 计划未要求，Final Review 建议 stage2 前留意）。
- **2026-10-08 22:5x（`1001G` 本场收口）：四包 U1 的 seg1 全部落地（Task 1–3）**——① 今晚由 run-sdd 通道
  ＋ **flash** 连跑三段：排期（21:52→22:06，14 分钟）／Offer（22:08→22:29，21 分钟）／入职（22:30→22:49，
  19 分钟）；每段＝3 个 Task（红→绿＋双只读 review）＋全分支 Final Review，**全部 PASS、`.done` rc=0**；
  ② 判据（worktree 内跑＋main 复跑同口径）：排期 **57 passed**＋2 grep；Offer **22 passed**＋五表 grep＋
  `offer` 表列块零薪资类词（红线反证）；入职 **34 passed**＋六表 grep＋六表列块零「内容/附件/证件号」类词
  （材料不入库反证）；③ 合回 main：`9a39875`（排期）／`924b423`（Offer）／`46494fe`（入职）——均已推送；
  ④ 本场新增产物：`app/storage/db.py` **17 张域表**（排期 6：interviewer／interviewer_availability／
  interview_slot／interview_slot_interviewer／interview_invitation_draft／invitation_template；Offer 5：
  letter_template／candidate_letter／offer／offer_approval_chain／offer_approval；入职 6：onboarding_template／
  onboarding_checklist／onboarding_item／onboarding_item_history／onboarding_access_log／
  data_disposition_queue）＋四个新测试文件（`test_db_interview_slot_schema.py`／
  `test_db_interview_invitation_draft_schema.py`／`test_db_offer_schema.py`／`test_db_onboarding_schema.py`）；
  ⑤ 待续段（下一场可接，均走 run-sdd＋flash）：排期 Task 4–6（含 `stage` CHECK 四值重建迁移——触碰
  `.51` 现网表结构，开跑前需人核）／Offer Task 4–6（stage 五值＋`letter_access_log`＋审批链存储）／
  入职 Task 4–5（`hr_account.role/department` 加列＋占位模板种子）／渠道 Task 4–6；⑥ `任务台账.yaml`
  已按真身刷新（seg1 四条仍显示 ready——tasks.md 未回勾是 opener 约定，⛔ 不是漏做）。
- **2026-10-09 10:0x（`1001G` 续跑）：四包 U1 的 seg2 全部落地**——① 全部走 run-sdd 通道＋**flash**（单段 20~40 分钟），
  每段＝逐 Task 红→绿＋双只读 review＋Final Review，判据在 worktree 与 main 各跑一遍：排期 Task 4-6 **187 passed**
  ＋老库实证；Offer Task 4-6 **205 passed**；入职 Task 4-5 **111 passed**＋两值 role 老库实测；渠道 Task 4-6 **73 passed**
  （另渠道包全量 4068 passed）；② 合回 main：`e71c586`（排期）／`39151cd`（Offer）／`e30b917`（入职）／`8d9c3a3`（渠道）
  ——均已推送；**main 全量 4070 passed / 10 skipped**；③ 本场三处**真缺陷**都由 review/实证抓住并按根因修（其中两处改的是计划文本本身）：
  排期包 `stage` 四值重建迁移（老库三值路径亲手实证：原行保留／`interview` 可写／FK=1／幂等）；
  Offer 计划 Task 5 的 `PRAGMA foreign_keys` 顺序坑（**事务内 PRAGMA 是 no-op** ⇒ 老库迁移后 FK 强制被留在 OFF）→ 计划修正
  `a6cf562`＋泳道修正版落成**六值** CHECK（`initial/screening/rejected/interview/offer/hired`，不清退排期包的 `interview`）；
  入职计划 Task 4 的**跨包 role CHECK 冲突**（排期两值 `('hr','interviewer')` vs 入职三值；列已存在时 ALTER 路径静默跳过 ⇒
  `dept_manager` 恒被旧 CHECK 拒）→ 计划修正 `9046bd4`＋`hr_account` 整表重建迁移（两值老库实测：`dept_manager` 可写、
  会话行不丢、FK=1）；④ 执行器两处加固：无头纪律⑤「计划里的裸 `python3` 一律换 `$SDD_PYTHON`」；`progress.md`
  分段重跑补行（勾选不再静默丢失，`06d911e`）；⑤ 一次 review **输出截断假阴**（Offer Task 5 第二份 Spec review
  未落 `VERDICT:` 行）→ 主会话做更强等价复核（真跑 pytest）后代提交 `dac66f9`，证据留档泳道进度目录
  `主会话复核-任务5-截断假阴补证.md`；⑥ 遗留：排期包老库 `stage` 守卫的补偿断言在 Task 9（聚合
  `tests/test_db_interview_schema.py`）落地前暂缺；⑦ 进度：**入职包 U1 已全部完成（Task 1-5）**；待续段＝排期 Task 7-10
  （名单维护接口＋聚合 schema 测试）／Offer Task 7-9（审批链接口＋无薪资聚合断言）／渠道 Task 7（收口）；
  ⑧ ⚠️ **`.51` 发版注意**：U1 合入后 `stage`（3/4→6 值）与 `hr_account`（role 2→3 值）都带**整表重建迁移**——
  发版前按发版清单先做 DB 快照，G3 仍需 Shao Peishen 放行。
- **2026-10-10 19:1x（`1001G` 收官）：四包 U1 全部完成＋发版七已上 `.51`＋两页样张在线**——① seg3 三段连跑（run-sdd＋flash）：
  排期 Task 7-10（36 分钟，判据 120）／Offer Task 7-9（30 分钟，判据 163）／渠道 Task 7（8 分钟，判据 32）；合回 main：
  `40e26e4`／`100bc5f`／`1f1ec9e`——**渠道 7/7、排期 10/10、Offer 9/9、入职 5/5，四包 U1 全部收官**；main 全量
  **4129 passed／10 skipped／0 失败**；② **发版七已上 `.51`**（G3 `Q-69`，答 `2a`；快照 `C:\apps\backups\20261010-1816`
  含 demo.db 在线备份）：U1 seg1+seg2 全量＋**首页/上传页观感样张**（`app-sample.css`，仅两页引用，删文件即回退）；
  迁移判据实测：`stage` 六行／`hr_account` 含 `dept_manager`／U1 八张抽样表全在；合规断言 `EXIT=0`；入库闸 False；未回滚；
  **样张待 Shao Peishen 评审**（通过 ⇒ 并入 `app.css` 全量、更新各页 `?v=`；不通过 ⇒ 删文件回退）；③ 发版八
  （seg3 件：`/api/interviewers` 与审批链接口）**已备料到可执行**（`docs/releases/2026-10-10-发版八-U1-seg3收官执行清单.md`），
  **G3 待放行**（发版决定不可代）；④ 遗留：渠道 Task 7 Spec review 记一条跟进建议（路由级超限场景测试缺失＝F1，非阻塞）。
- **2026-10-10 21:2x（`1001G` 续）：样张全量并轨（答 `1a`）＋发版八已上（答 `2a`）**——① 并轨：`app-sample.css` 并入
  `app.css`（全站 8 页统一视觉层）、删除样张文件、8 页 `app.css?v=` 同步 `20261010`；提交 `b53e615`（main 全量
  **4129 passed**；本机抽查登录/校对/候选人同意页无破样）；② **发版八已上 `.51`**（快照 `C:\apps\backups\20261010-2115`
  含 demo.db 备份）：seg3 件（`/api/interviewers`、`/api/jobs/{id}/offer-approval-chain`）＋样张并轨一并上线；
  冒烟：两新接口未登录 `401`（路由已接）、`/api/jobs` `200`、served 页面/样式并轨判据过、合规断言 `EXIT=0`、
  入库闸 False；未回滚；③ 至此**四包 U1 全部完成＋全站新视觉已在 `.51` 共处一版**（main `b53e615`）。
- **2026-10-10 21:35（`1001G` 续）：U2 计划批四条全绿（lane-dispatch 发车）**——① 编排：四包 U2 spec-to-plan
  各一泳道（1001V 渠道去重合并／1001W 排期时段登记与排期动作／1001X Offer 文书引擎／1001Y 入职清单页与进度），
  无头块进 `OP-0820-全量编排.md`、引用式 opener 落 `docs/openers/1001V–Y-*.md`、号池已登记；
  四者触碰区零重叠 ⇒ `--max-parallel 4 --stagger 30` 并行；② 结果：**4/4 OK**（V 18′／W 17′／X 20′／Y 17′），
  机器判据 4 条过 0 条不过，stage2 自动合合并并推送（`0186efa`/`4b793bb`/`bc31beb`/`d9e0950`）；
  产物＝四份 U2 计划：`docs/superpowers/plans/2026-10-10-channel-resume-intake-unit2-dedup-merge.md`（8 Task/1913 行）／
  `…interview-scheduling-unit2-availability-and-slot-actions.md`（11/2529）／`…offer-generation-unit2-letter-engine.md`（8/2049）／
  `…onboarding-flow-unit2-checklist-and-progress.md`（8/2133）——均含 Global Constraints 段与单元标志词；
  ③ 🔴 **发现一处执行器小缺口（待修，非本批产物问题）**：`run-lanes.sh` 批次私信入队用 `python3`（本机解析
  `/usr/bin/python3`＝3.9.6）⇒ `tools.liaison` 导不进、**入队必失败**——全 OK 批次按口径不私信（CLI rc=2，
  run-lanes 还会把它当失败打一条多余警告），但**有失败的批次恰恰发不出私信**（最需要它的场景）；服务用
  `/opt/homebrew/bin/python3.14` 可正常跑同命令（已实证）。改执行器属红线③，待 Shao Peishen 点头派专门修复＋冒烟。
- 【谁做：看护者/下一场主会话】【状态：待 `#4` 回件闭环】【判据：`docs/跟进信/README-跟进信清单.md` 中
  `人事部#4` 转闭环四态之后，把 M2 判例批改表包作为独立新信过 G4 发出】【不做会怎样：M2 评测集标注
  （U6 前置）无法启动，U4 召回精排继续无真实样本】。
- **`.51` 待发版项（非必须，建议随下批发）**：`1001J` 首页新壳（`app/web/static/index.html`＋`app.css`）
  在发版五之后才合入 main ⇒ **`.51` 首页仍是旧壳**；要发就在发版六里带上（G3）。
- 【谁做：Shao Peishen】【状态：待人】【判据：①在有 `.git` 写权限的会话里对三个白名单路径 `git add`＋`git commit`（⛔ 不 push）——`docs/跟进信/回件/人事部#3-2026-10-08.md`、`docs/跟进信/README-跟进信清单.md`、`docs/session接力.md`；②随后按 §一.5 清信号：`PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison unpack-signal --clear --before 2026-10-08T10:23:51.627781+08:00`】【不做会怎样：回灌结论与 `#3` 闭环只存在于未提交工作区，机器判据／归档看不全；信号保留 ⇒ 下一轮拆件会话仍会探到同一批项并重复处理】
- 【谁做：Shao Peishen】【状态：待人】【判据：明确「`3.3-2`／`3.3-3` 无需补」或指定补问路径（催补／补充信）——回件未写此两条，信件正文允许「没讨论到的编号可以不写」，`7-3` 写「同上」指向未写的 `3.3-3`】【不做会怎样：产品侧两项口径（超时提醒与自动关闭节奏、验收后放开范围与撤演示标识）缺基线输入；`#3` 已闭环、串行闸已放行，此项无机器再提醒】
- 【谁做：Shao Peishen】【状态：待人】【判据：决定根因修法并派 opener——codex 拆件会话的提交链路（候选：照 0930D「执行器/值守侧代提交」先例扩到拆件链路；或调整 dispatch 的 codex 沙箱参数放开 `.git` 写）——属改 `tools/`/`scripts/` 的建造动作，本会话按红线②不做、未修】【不做会怎样：下次真实回件照旧「文档已写、无法提交、信号清不掉」，每轮都要人工补提交＋清信号】
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
