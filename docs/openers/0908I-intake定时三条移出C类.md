[Mac]0908I-intake定时三条移出C类
【设置】执行环境: CC ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（只改一份 tasks.md）｜ 工作区: 仓库根

> 本文件是 `[Mac]0908I` 的正文（引用式 opener）。依据：Shao Peishen 2026-09-08 在 Cowork 会话 HR业务线-接力0903B 裁决「按推荐」——
> 1.7 / 5.6 / 6.8 三条要定时基础设施，移出为 C 类、另立「调度基础设施」包（待立项）。
> 🔴 **必须在第十一批（0908Z）收敛之后跑**：本条改的 tasks.md 与识别泳道 0908F 回勾 5.3 的行相邻（5.3 在 5.6 上面 4 行），并行会撞 rebase 冲突。

【一、前置（任一不过即停下报我，⛔ 不硬上）】
1. grep -c "^> 泳道：" docs/openers/OP-0820-全量编排.md 必须为 0（第十一批 6 条已全被脚本摘掉）；不为 0 → 停，报「第十一批未收敛」
2. pgrep -f run-lanes.sh 无进程
3. git status -sb：main 与 origin 同步；工作区干净或只有 .claude/handoff/（gitignore 内）
4. ls openspec/changes/ 含 hr-wecom-aibot-liaison（0908A 已提交 `6bb1d90`）

【二、tasks.md 三处改动（openspec/changes/m1-job-profile-intake/tasks.md）】
1. 1.7 / 5.6 / 6.8 三条：原位保留、⛔ 不勾，条目后追加「⤷ **已移出**（2026-09-08 Shao Peishen 裁决）→ 调度基础设施（待立项）」，写法与 3.x 的「⤷ 已移出」一致
2. 「📤 已移出，另开变更包」节新增小节：
   `### → 调度基础设施（尚无变更包，待立项，2026-09-08 登记）`
   表格三行：1.7 checkpoint 清理任务 / 5.6 业务经理超时提醒与 abandoned / 6.8 挂起提醒第 1、3 天。
   依据一句：M1 没有 cron / 后台任务载体（部署形态＝Windows 计划任务跑 uvicorn 单进程），三条硬做等于自造调度器；与 0904B plan 对 6.8 的留步、TD-11 同源。
   ⚠️ 顶部那句「下面 12 条都是"确实还要做"」的计数改成实际条数
3. 「→ 阶段二·企微通道（尚无变更包，待立项）」小节标题改为「→ `hr-wecom-aibot-liaison`（2026-09-08 已立项，`openspec/changes/hr-wecom-aibot-liaison/`）」，表格不动；⛔ 不改那个包的任何文件
4. 顶部进度行按实际勾数更新（第十一批后应为 60/72），并在「归档还差什么」段追加一句：C 类共 11 条（8 企微 + 3 调度）已移出，D 类只剩 9.1（人工评估）——归档判定等 Shao Peishen

【三、提交（只 add 列出的路径）】
git add openspec/changes/m1-job-profile-intake/tasks.md docs/session接力.md docs/openers/OP-0820-全量编排.md docs/openers/0908I-intake定时三条移出C类.md
（接力【一】进度表与号池 0908I 行顺手补结果；⛔ 禁止 -A / . / -a；⛔ 不碰 hr-wecom-aibot-liaison/；push 白名单已在；index.lock 等 5 秒重试 5 次不删）
git commit -m "docs(m1-intake): 1.7/5.6/6.8 移出为 C 类（调度基础设施，待立项）＋ 企微 8 条指向 hr-wecom-aibot-liaison ＋ 进度对齐"
git push origin main

【四、汇报】三条移出后的原文 / 新小节原文 / 进度行新值 / commit hash / ahead
