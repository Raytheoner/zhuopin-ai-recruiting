# Session 接力 · HR 招聘智能体

> 滚动更新，覆盖旧版。新会话读完本文即可接上。
> 最后更新：2026-09-03（Cowork 业务线）

---

## 开场词（复制即用）

```
HR业务线-接力0903B
读 /Users/paulshao/Projects/HumanResource/docs/session接力.md 恢复上下文，然后按【下一步】继续。
```

> 🔒 **首行只用于给读文档的人对编号，不指望侧边栏**。Cowork 侧的 session 名是摘要生成的，
> 首行无效（08-27 实测：`HR业务线-接力0827B` → 侧边栏 `HR业务线接力`）。⛔ 不要再改首行格式硬试。
>
> 🔴 **出号前必查号池台账**：`docs/openers/OP-0820-全量编排.md` 顶部「🔢 号池台账」。
> **Cowork 与 CC 共用同一号池**，`Z` 固定留给看护者。08-27 一天撞号 5 次，根因是
> 提交类 opener 只在聊天里派、从不落档 ⇒ 下轮 grep 不到 ⇒ 重派。**给出后当场登记**。

---

## 一、状态快照（2026-09-03 第七批跑完后）

| 项 | 现状 |
|---|---|
| main | `b86db65`，**与 origin 同步（ahead 0）**（`0903J` 推成；`0903Z` 发车前那次 push 被 classifier 拒、收尾那次成功，见【三】#1） |
| 工作区 | 未提交（Cowork 侧 0903B 产出）：本文、`OP-0820-全量编排.md`、`CLAUDE.md`、kickoff / lane-dispatch / run-build 三个 skill、`docs/openers/0903K-*.md` `0903L-*.md`。`0903K` 提交。⚠️ `.claude/handoff/` 在 `.gitignore` 里，看护报告只在本机 |
| `.51` 代码 | ✅ **已发版 `b86db65`（含 U6）**（`0903L` 2026-09-03 二次发版，`sync-to-server.sh`，冒烟 4 项全过；U6 巡检 CLI 首次实跑 `EXIT=2`／JSONL 镜像不存在＝尚无审计记录非失败；8.4 已在页面手工跑通并回勾，见 `docs/audit-and-outbound-ops.md` §五与 `tasks.md` 8.4） |
| pytest | main 侧 **786 passed / 1 skipped / 0 failed**（09-03 `0903Z` 独立复核；skipped 那条＝`REPLAY_LIVE` 门控的真回放）。⛔ 别抄进 opener 当基线，见【四】 |
| 生产 | `.51:8095`，`/hr/recruit-agent`，服务正常 |
| worktree | 只剩主工作区，无多余分支（G/H 的 worktree 由各自 finishing 流程自删，不是外部清理；H 的陈旧分支 `0903J` 已删） |

**变更包进度**

| 变更包 | 进度 | 剩什么 |
|---|---|---|
| `ai-audit-trail-and-outbound-gate` | **51/53** | 第1–6章✅（U6 `e5e8e33`，0903G）；只剩 **7.1/7.2**（CI 查 `zhuopin_platform` 依赖与 `sys.path` 注入）。归档条件＝这两条做完 |
| `m1-intake-quality-fixes` | **67/69** | 第 8 章 7/9（0903H/I）；剩 **8.4**（要 Shao Peishen 在 `.51` 页面手工跑通）与 **8.9**（归档顺序：`m1-job-profile-intake` 先） |
| `m1-job-profile-intake` | 33/71 | 08-26 已按现实重写 WBS，四类归档 |

**已跑完的批次**：第四批（0827B/C）、第五批（0828A/C/D/B）、第六批（0830A）、**第七批（0903F/G/H/I，4/4 合入 main，报告 `lanes-20260903-115028-看护报告.md`）**。
看护报告都在 `.claude/handoff/lanes-*-看护报告.md`。

---

## 二、下一步

### ~~① 提交工作区 2 文件~~ ✅ `0903C` 已跑完（`3101c99`）

### ~~② 发 `[Mac]0903A`~~ ⏸ 已跑，**仍未闭合**——不是路径，是 `.51` 没部署过 08-27 后的代码

`0903A` 结果（`e8ef150`）：`.venv` 路径订正有效；四步第 2/4 步报
`ImportError: cannot import name 'is_candidate_outbound_enabled'`；§2.2 链校验报
`ModuleNotFoundError: app.audit`。产线开关已绕开应用层直接读文件确认为 `false`（安全）。
**结论：§5-3 在这份部署上无论怎么跑都不可能过，先发版。**

### ~~② `[Mac]0903D`：`.51` 阶段 D 日常发版 ＋ 发版后补跑 §5-3 四步~~ ✅ 已跑完，**已闭合**（`d104249`）

结果：发版 `HTTP 200`，冒烟 4 项全过，§5-3 四步与 §2.2 链校验全过（详见
`docs/audit-and-outbound-ops.md` §五第 2/3 项，均已标 ✅）。第 1 项（备份任务）
仍 ⏸，本轮只做一次性快照 `C:\apps\backups\20260903-1003`。以下为发版前的原始判据，留作记录：

🔴 发版与开关四步均为不可代项。**Shao Peishen 2026-09-03 09:55 已在 0903B 会话回「发」**，
opener 全文在该会话里；若 CC 侧还没跑，去 0903B 会话复制整块。发版判据（09-03 Cowork 侧核过）：

- `requirements.txt` 自 08-19 起无改动 ⇒ 只需 `sync-to-server.sh`，⛔ 不用重跑 `deploy-server.ps1`
- 新配置字段全有默认值，`.env.example` 无新增 ⇒ 服务器 `.env` 不用动
- SQLite 加列走 `_ADDED_COLUMNS` 幂等迁移，启动时自动补 ⇒ `data/demo.db` 不用手工迁
- 发版前先快照 `app\` 与 `data\` 到 `C:\apps\backups\<时间戳>\`，不过冒烟即回滚
- 影响面：3 位 pilot 业务经理会看到 `m1-intake-quality-fixes` 60/69 的新行为（这是设计目的，
  第 8 章"真实会话回放与上线"本来就要它上 `.51`）

### ~~③ `0903D` 报「§5-3 已闭合」后，再发 `[Mac]0903E` U5 收口~~ ✅ 已跑完（合并 commit `06a55d2c`，pytest 675→720）

结果：`finishing-a-development-branch` 把 `worktree-audit-u5-queue-and-wiring` 合回 `main`
（`--no-ff`，merge commit `06a55d2c`）。`git rev-list --count main..<分支>` = 0，确认真合
非 rebase 假阳性。合并后全量 `pytest` **720 passed / 0 failed**（合并前基线 675）。
第 5 章 9/9 已回勾，**未归档**（第 6 章 0/7、第 7 章 4/6 未完，见 `tasks.md` 顶部进度行）。
分支已 `git branch -d` 删除（worktree 本就已不在）。

### ~~④ 第七批~~ ✅ 已跑完（4/4 合入 main 并已推送）

结果：F plan 5 Task（`18f85ad`）；G U6 7/7 合入（`e5e8e33`，终审 5 次变异抓到 2 处真缺口后才闭合）；
H 三段真实会话回放 18 轮全通、未溯源字段 0/18（`2239b90`）；I 8.6/8.7/8.8 回填（`d19625f`）。
superpowers 两条都**没调到**，均按磁盘 SKILL.md 手工走完（第 4 次靠运气）。
🔴 回放实测**单轮 LLM 延迟均值 33.9 / 48.5 / 65.1 s，最大 132 s**——pilot 抱怨的"等待"有了第一个数，修复前无基线不可比。

### ~~⑤ `0903J` 收尾~~ ✅ 已跑完（`b86db65`，已推）

### ⑥ Shao Peishen 09-03 14:05 三条裁决 → 两条 opener 已派（可并行，触碰区零重叠）

| 裁决 | 落点 |
|---|---|
| TD-9 **走 `openspec-propose`** | `[Mac]0903K`：立正式变更包（只出 proposal/specs/design/tasks，不写代码），顺带提交真源改动。正文 `docs/openers/0903K-TD9立变更包.md` |
| 8.4 ＋ U6 巡检 **都要上 `.51`** | `[Mac]0903L`：再发一次版（main 当前 HEAD，含 U6）→ 巡检 CLI 对真实库跑 → 他在页面跑 8.4 → session 从库里取证回勾。🔴 发版不可代，本条裁决即授权。正文 `docs/openers/0903L-51二次发版与U6巡检与8.4取证.md` |
| 回放类任务收口前**拷走 `data/` 产物** | ✅ 已落真源：`.claude/skills/run-build/SKILL.md` 收口第 2 步、`.claude/skills/lane-dispatch/SKILL.md` ③ 第 4 条。`0903K` 提交 |

之后可发车：U7 剩 7.1/7.2（做完 audit 包归档）；TD-9 变更包立好后走 spec-to-plan → run-build。

---

## 三、待决策 / 悬置

| # | 事项 | 状态 |
|---|---|---|
| 1 | 🔴 **`git push` 被 auto mode classifier 拦** | 反复出现（08-28、08-30 两次记录在案）。09-03 `0903Z` 实测：发车前那次被**直接拒绝**（非挂起待点击），收尾那次成功——同一 session 内两次结果不同，机制仍不明。两条路：**(a)** 每次在能批准的 session 里点放行；**(b)** 给 `.claude/settings.json` 加 `"permissions": {"allow": ["Bash(git push:*)"]}`——项目级、可提交、对所有 session 生效。**(b) 是改权限，等 Shao Peishen 拍板** |
| 2 | 🔴 **worktree 被未落档地清理，已发生两次** | 08-30 11:38 扫掉 u2/unitE/unitF/u1 四条（判据＝真未合 0，代码零损失）；**09-03 前 u5 也被移除**——而 08-30 那份报告刚评估过「u5 真未合 11，同样的清理不会碰它」。⇒ **判据变了或用了 `--force`，机制不明**。代码没丢（分支 `worktree-audit-u5-queue-and-wiring` 与 `19ab503`/`f899c98` 都在），丢的是 worktree 内 git-ignored 的 `.superpowers/sdd/` 台账。**要不要查清是谁在清、加个护栏？** |
| 3 | **TD-9**：同一草稿第二次拦截零留痕 | U6（0903G）已**坐实**："放行后复发又被拦"路径系统性缺席。修复要改已过审的 `approve()` 签名 + 5.4 幂等键公式，属契约层变更。**Shao Peishen 09-03 裁决：走 `openspec-propose`**，`0903K` 在立变更包 |
| 4 | `.51` 留步清单**只剩一项** | §5-1 备份任务确认/新增（`0903D` 只做了一次性快照 `C:\apps\backups\20260903-1003`，不等于常态化备份任务）。§5-2 链校验与 §5-3 四步已于 09-03 闭合。见 `docs/audit-and-outbound-ops.md` 第五节 |
| 5 | `.51` 整机重启 | 阻断已清、只差窗口。⚠️ 爆炸半径 **7 个服务**（含门户网关本体），`CBS RebootPending=True` + 已 85 天未重启，停机时长不可按常规估。opener＝编排文件 `[Mac] 0820-9R` |
| 6 | 阶段 C 门户导航 | 需在 Win 笔记本上改门户 HTML。板块名「HR·招聘智能体」，外链 `http://192.168.100.51:8095/hr/recruit-agent/`。与 `.51` 服务无关，不影响运行中的服务 |
| 7 | 决策代理人 | `CLAUDE.md` 框架已建，**2026-08-28 决定继续不设**。「可代」项在无代理人期间同样一律挂起等本人 |
| 8 | 06 清单剩余 | 3.3 企微 webhook（无挂载点）、9.1/9.2/9.3 沟通线。7.1/7.4 合规条款已随 audit 包推进 |
| 10 | 8.4 ＋ U6 巡检 CLI 上 `.51` | 8.4 要人在页面跑通"模糊回复→点选→带缺口确认"；U6 的巡检 CLI 从未对 `.51` 真实 `demo.db`/`decisions.jsonl` 跑过——且 U6 代码**还没部署到 `.51`**（现网是 `d104249`，早于 U6）。**Shao Peishen 09-03 裁决：都要上**，`0903L` 发版 + 巡检 + 8.4 取证 |
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
- **无头 session 取不到 `superpowers:*`**：插件装在 `projectPath: /Users/paulshao/Projects`
  项目作用域，`.claude/settings.json` 里 `enabledPlugins` 开着也解析不到。
  ⇒ `run-build` 的前置检查 1「调不到就停」在无头下**恒定失效**。
  08-27 与 08-30 两次都是执行者照磁盘上的 `SKILL.md` 手工走完协议的——**那是运气，不是机制**。
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

---

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
