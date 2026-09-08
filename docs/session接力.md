# Session 接力 · HR 招聘智能体

> 滚动更新，覆盖旧版。新会话读完本文即可接上。
> 最后更新：2026-09-08（Cowork 业务线）

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

## 一、状态快照（2026-09-08 18:00，第十二批跑完后）

| 项 | 现状 |
|---|---|
| main | `3423684`，**与 origin 同步（ahead 0）**。第十二批 44 个 commit 全合入 |
| 工作区 | 未提交（Cowork 侧）：本文、`OP-0820-全量编排.md`（第十三批 + 号池）、`docs/openers/0908U-*.md`、`0909Z-*.md`。`0908U`【五】先带走一部分，`0909Z`【二】收尾。⚠️ `.claude/handoff/` 在 `.gitignore` 里，看护报告只在本机 |
| `.51` 代码 | ✅ **已发版 `95b298c`**（`0908K` 2026-09-08 五次发版成功）。⚠️ **实际范围远大于该轮 opener 所述的「只发巡检 CLI 编码修复」**——`7a48a19..95b298c` 对 `app/ scripts/` ＝ 15 文件 / +905 −118，一并上线 **WBS 2.3 备用供应商切换**、**WBS 2.5 重试耗尽转人工**（两个新 `effect_*` 节点）、**tasks 5.3 离题轮不留岗位记录/丢弃**；⇒ 今后发版 opener 的「这次发什么」必须由 `git diff <现网 HEAD>..main` 现算。快照 `C:\apps\backups\20260908-1420`（`app\` 126 文件完整；`data\` 因 `demo.db-shm` 被占用中断，不影响回滚——回滚只用 `app\`）。纯 sync，⛔ 未装依赖、未重跑 `deploy-server.ps1`。冒烟 4 项：首页 200 ｜ `GET /api/jobs` JSON 列表 ｜ 新进程 PID 9600 于 `14:22:36` 启动、**其后零 Traceback**（尾 60 行里那段 `Traceback` 时间戳为 `13:18:32`，属上一版进程的历史错误，已取证非本次回归，未回滚）｜ 🔴 **不带 `PYTHONIOENCODING` 裸跑巡检 `EXIT=0`**、6 条断言全过、断言四豁免 7 条 ⇒ `19b8937` 修复现网生效。⇒ **`0908J` 那条「加 `PYTHONIOENCODING=utf-8` 才得真结果」已作废，⛔ 不要再加环境变量**。🔴 查实遗留缺陷（早于本次发版、未修）：重复 `confirm` 时 `idempotent_effect` 抛 `sqlite3.IntegrityError: UNIQUE constraint failed: effect_log.effect_key` 而非短路，用户可见 500（恒等式未破）→ ✅ 第十二批 `0908S/T` 已修合入（撞键即 rollback 业务写、按已执行返回），**尚未上 `.51`**。ℹ️ 口径订正：`.51` 应用日志真实路径是 `logs\app.log`，**不是** `data\logs\app.log`。详见 `docs/audit-and-outbound-ops.md` §五 ｜ `0908B` 失败回滚见 `docs/findings/2026-09-08-51四次发版回滚.md` |
| pytest | main 侧 **1378 passed / 1 skipped / 0 failed**（09-08 17:xx `0908Y` 复核）。⛔ 别抄进 opener 当基线，见【四】 |
| 生产 | `.51:8095`，`/hr/recruit-agent`，服务正常 |
| worktree | `/private/tmp/wt-0908M`（prunable，分支真未合 0，重启即消失，不管）；三个旧分支仍在、真未合 0 |

**变更包进度**

| 变更包 | 进度 | 剩什么 |
|---|---|---|
| ~~`ai-audit-trail-and-outbound-gate`~~ | **53/53 ✅ 已归档** | `openspec/changes/archive/2026-09-04-ai-audit-trail-and-outbound-gate`；specs 折进 `ai-decision-audit` + `outbound-approval-gate` |
| `m1-intake-quality-fixes` | **68/69** | 8.4 ✅（Shao Peishen 09-03 页面实跑，job `51b225f1`，0903L 取证）；只剩 **8.9**（归档，须 `m1-job-profile-intake` 先归档） |
| ~~`outbound-retry-audit-trace`~~ | **15/15 ✅ 已归档** | `archive/2026-09-04-outbound-retry-audit-trace`；delta 已合进 `outbound-approval-gate/spec.md`；修复已随 `3f59842` 上 `.51` |
| `hr-wecom-aibot-liaison` | **18/66** | 第 1–3 章 ✅（第十二批：骨架 + SDK 1.0.2 在 3.14 实测走路线① / 存储基座与幂等不变式 / 准入名单两人 fail-closed）。第十三批做第 4、5、7 章；第 6 章要 Shao Peishen 给群 webhook；第 8 章灰度要他实操。**真实建连留步：他尚未在企微后台注册新 aibot 拿 BotID/Secret** |
| `m1-job-profile-intake` | **60/72** | 第十批：9.6 ✅、硬门槛 1.2b/5.8/5.9 ✅、账目对齐 4 条 ✅；0905A：Web 8.1/8.2/8.4 ✅（`05e90cc`）；**第十一批：2.3+2.5 ✅（`7ef4bb2`）、5.3 ✅、4.4 ✅**。剩 12 条，🔴 **已全部有去向，本包内无待做代码**：**C 类 11 条已移出**——8 条企微（1.5b/3.x/9.2）→ `hr-wecom-aibot-liaison`（`0908A` 已立包），3 条调度（1.7/5.6/6.8）→ **调度基础设施（待立项）**（`0908I` 09-08 落档，依据 Shao Peishen「按推荐」裁决）；**D 类只剩 9.1**（10 个真实岗位重跑、HR 与业务经理双方评估，**要真人参与**）。⇒ **归档判定权在 Shao Peishen**，⛔ 代理人不代拍 |

**已跑完的批次**：第四至第九批；**第十批（0904G–M，报告 `lanes-20260904-134429-看护报告.md`；M 预算耗尽 NO-SENTINEL，由 `0905A` 09-07 利旧 worktree 续跑收口）**。
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

### ~~⑥ 三条裁决~~ ✅ K/L 都跑完（`a15862d` / `feb49d6`，**未推**）

| 裁决 | 落点 |
|---|---|
| TD-9 **走 `openspec-propose`** | `[Mac]0903K`：立正式变更包（只出 proposal/specs/design/tasks，不写代码），顺带提交真源改动。正文 `docs/openers/0903K-TD9立变更包.md` |
| 8.4 ＋ U6 巡检 **都要上 `.51`** | `[Mac]0903L`：再发一次版（main 当前 HEAD，含 U6）→ 巡检 CLI 对真实库跑 → 他在页面跑 8.4 → session 从库里取证回勾。🔴 发版不可代，本条裁决即授权。正文 `docs/openers/0903L-51二次发版与U6巡检与8.4取证.md` |
| 回放类任务收口前**拷走 `data/` 产物** | ✅ 已落真源：`.claude/skills/run-build/SKILL.md` 收口第 2 步、`.claude/skills/lane-dispatch/SKILL.md` ③ 第 4 条。`0903K` 提交 |

结果：K 立了 `outbound-retry-audit-trace`（2 章）并提交全部真源改动；L 二次发版 `b86db65`（含 U6，快照 `backups\20260903-1428`，冒烟 4/4），
巡检 CLI 首跑 `EXIT=2`＝JSONL 镜像尚不存在（现网无外发调用方，不是失败），8.4 由 Shao Peishen 本人页面跑通、取证三判据全中。

### ~~⑦ 第八批~~ ✅ 跑完（N/O/P OK，Q PARTIAL＝归档顺序反了、正确刹车）

结果：U7 53/53（`6e1272c`）；TD-9 修复全部合入（`bf45370`），pytest 786→842；两条 plan 落档。
过程：O 两次撞 Anthropic 529 上游事故（status 页有 incident），事故解后重跑收敛；Auto Mode 分类器拦 `nohup`，
Shao Peishen 亲自跑 `scripts/allow_run_lanes.py` 加白名单后解除。superpowers 四条全没调到、全手工走完（第 7–10 次）。
🔴 **归档没做**：retry 包的 delta 是对 ai-audit 包 ADDED 需求的 MODIFIED，主 spec 要等 ai-audit 归档才存在——opener 顺序写反了，Q 没产半成品。已触发「归档时限」规则，`0904A` 当场补。规则已写进 run-build skill 收口第 5 步。

### ~~⑧ `0904A`~~ ✅ 跑完（`7faf682`）：两包归档、TD-10 登记、wt-U7 删；三处真源改动一并入库

### ~~⑨ 第九批~~ ✅ 跑完（B/C/D/E 全合入，pytest 842→1008，`.51` 三次发版 `3f59842` 含 TD-9 修复）＋ `0904F` 9.6 规格更新完

结果：第 6 章确认断点 9 条 + 1.4 + 9.3 合入（`be6322a`），第 7 章 7.3/7.5/7.7 合入（`6ae57bf`），45/72。
过程：Auto Mode 分类器**在白名单齐全时仍拦** `nohup run-lanes.sh` 与 `sync-to-server.sh`——推翻 09-03「白名单能解决」的归因；
唯一验证有效的路径＝把命令贴成 bash 块、Shao Peishen 在 CC Desktop 点 Run（已写进 lane-dispatch skill）。C/E 各撞一次预算上限（$25）跳过全分支终审。
C 发现断言四豁免线用 `created_at` 有洞 → Shao Peishen 裁决「现在修」→ `0904F` 出了规格（tasks 9.6）。

### ~~⑩ 第十批~~ ✅ 跑完（G/H/I/J/K/L OK，M 预算耗尽）＋ ~~`0905A`~~ ✅ 续跑收口（`05e90cc`，09-07）

结果：9.6 合入（`3b3aac7`）、硬门槛 1.2b/5.8/5.9 合入（`46b84b9`，含 hard_requirement 新表）、账目对齐 4 条、Web 8.1/8.2/8.4 合入（`05e90cc`，登记 19 条落地偏离与 parked）。56/72，pytest 1008→1061+。
🔴 0904I 留步：9.6 上 `.51` 后，留痕上线前的历史行会从"被豁免"翻成断言四违例（巡检 EXIT=1）——**预期结果**，处置＝人工逐条核实，⛔ 不挪豁免线。✅ **已闭合（`0908J` 09-08）：9.6 已随 `7a48a19` 上线，巡检 `EXIT=0`，预告的违例未发生**——`0904F` 把豁免线改成决策时间戳后，7 条历史行被正确豁免而非翻成违例，故无「待核实清单」可抄（⛔ 不是漏跑）。7 条明细存证于 `docs/audit-and-outbound-ops.md` §五。
另一条线：`0908A` HR 企微值守机器人 `openspec-propose`（09-08 09:31，由别的 session 派）——它承接的正是本包 8 条 C 类企微条目。

### ~~⑪ 09-08 上午~~ ✅ 全清：第十一批 6/6 合入（2.3/2.5/5.3/4.4，intake 60/72）；`0908B` 缺 tzdata 回滚 → `0908J` 重发成功 `7a48a19` → `0908K` 五次发版 `95b298c`（巡检裸跑 EXIT=0）；`0908I` 三条移出 C 类
新发现：① 现网缺陷——重复 confirm 时 `idempotent_effect` 撞 UNIQUE 抛 IntegrityError 而非短路（500，恒等式未破）→ 第十二批修复泳道；② `run-lanes.sh` 静默错配（awk UTF-8 locale）已由 `2f14d2c` 钉 `LC_ALL=C`，护栏交第十二批机制泳道；③ 🔴 **备用 LLM 供应商选型（采购，不可代）**——2.3 链路已合入但 `LLM_FALLBACK_*` 全空等同未启用，等 Shao Peishen

### ~~⑫ 第十二批~~ ✅ 跑完（9/9，值守 18/66，pytest 1233→1378，幂等撞键修复合入，run-lanes 条目自检上线）

🔴 事故：`0908R` 在泳道里改 `run-lanes.sh`，运行中的 bash 按旧字节偏移续读新文件 → 编排器自我损坏、六条被派两遍（产出未受损，泳道自己识别成复核）。
根治＝脚本开跑先自拷贝到临时目录再 exec（`0908U`，永不进泳道，发车前单独跑）。规矩：**改 `run-lanes.sh` 的 opener 永远不进它自己驱动的泳道**（已写进 lane-dispatch skill）。
SDK 结论：`wecom-aibot-python-sdk 1.0.2` 在 3.14 判据 A/B/C 全过，走路线①，第 4–7 章按 SDK 正路排。

### ⑬ 09-08 晚：先 `0908U`（护栏）→ 再发第十三批 `0909Z`

- `0908U`：run-lanes.sh 自拷贝执行 + 零条目路径自检可达 + TD-17（`delivery.py:12` 非法转义）。CC 新开、不勾、单独跑。正文 `docs/openers/0908U-run-lanes自拷贝执行与TD17.md`
- 第十三批（编号按发车日 0909，0908 字母池只剩 U–X）：归档队列泳道 `0909A→B→C→D`（第 4 章消息归档 → 第 5 章任务队列，同写 `storage/effects.py` 故串行）∥ 连接泳道 `0909E→F`（第 7 章连接生命周期，独占 `session*` `alerts*` `__main__.py`）。dry-run 预期 A20/B23/C19/D23/E20/F23，Σ=6。正文 `docs/openers/0909Z-泳道批次看护.md`
- 等 Shao Peishen 的三件：① 备用 LLM 供应商（采购，推荐阿里云百炼 qwen-plus）；② 企微后台注册新 aibot 拿 `HR_LIAISON_BOT_ID/SECRET`（第 7 章真实建连、第 8 章灰度都要）；③ 「人力AI保障组」群 webhook 地址（第 6 章）

---

## 三、待决策 / 悬置

| # | 事项 | 状态 |
|---|---|---|
| 1 | 🔴 **`git push` 被 auto mode classifier 拦** | 反复出现（08-28、08-30、09-03 K/L 又两次）。09-03 `0903Z` 实测：发车前那次被**直接拒绝**（非挂起待点击），收尾那次成功——同一 session 内两次结果不同，机制仍不明。两条路：**(a)** 每次在能批准的 session 里点放行；**(b)** 给 `.claude/settings.json` 加 `"permissions": {"allow": ["Bash(git push:*)"]}`——项目级、可提交、对所有 session 生效。**Shao Peishen 09-03 拍板走 (b)**。Cowork 侧改 `settings.json` 被 classifier 拦（改权限配置本就该在 CC 里人眼过一遍），✅ `0903M` 已加（`365e5fa`）。⚠️ 白名单对**新开的** session 生效。**09-03 17:xx 又撞一层**：Auto Mode 分类器拦 `0903Y` 的 `nohup run-lanes.sh`（判「无人值守起子 session」高风险），看护者自己改 settings.json 也被拦 ⇒ Shao Peishen 手工加 `Bash(bash docs/openers/run-lanes.sh:*)` 与 `Bash(nohup bash …:*)` 两条。已写进 lane-dispatch skill ④ 与看护者前置自检第 0 条。**09-04 `0904Z` 推翻「白名单能解决」的归因**：白名单三条确认已在 `.claude/settings.json` 里（`grep -c`=2，`scripts/allow_run_lanes.py` 复跑也确认无新增可加），但 `nohup run-lanes.sh` 与 `./sync-to-server.sh` 仍各被拦两次——说明白名单从未是真正生效的机制，09-03 的"解除"很可能是巧合归因，不是因果。**唯一验证有效的路径**：把启动命令原样贴成 ` ```bash ` 代码块发给 Shao Peishen，他在 CC Desktop 对话里点 Run 按钮直接执行——用户直接动作不经过 AI 的 Bash 工具调用，不触发该分类器；看护者自己反复重试大概率无效。已写回 `lane-dispatch` skill |
| 2 | 🔴 **worktree 被未落档地清理，已发生两次** | 08-30 11:38 扫掉 u2/unitE/unitF/u1 四条（判据＝真未合 0，代码零损失）；**09-03 前 u5 也被移除**——而 08-30 那份报告刚评估过「u5 真未合 11，同样的清理不会碰它」。⇒ **判据变了或用了 `--force`，机制不明**。代码没丢（分支 `worktree-audit-u5-queue-and-wiring` 与 `19ab503`/`f899c98` 都在），丢的是 worktree 内 git-ignored 的 `.superpowers/sdd/` 台账。**要不要查清是谁在清、加个护栏？** |
| 3 | **TD-9**：同一草稿第二次拦截零留痕 | U6（0903G）已**坐实**："放行后复发又被拦"路径系统性缺席。修复要改已过审的 `approve()` 签名 + 5.4 幂等键公式，属契约层变更。✅ **已修复合入**（`bf45370`，0903Q），TD-9 销账行已写；只差包归档（`0904A`） |
| 4 | `.51` 留步清单**只剩一项** | §5-1 备份任务确认/新增（`0903D` 只做了一次性快照 `C:\apps\backups\20260903-1003`，不等于常态化备份任务）。§5-2 链校验与 §5-3 四步已于 09-03 闭合。见 `docs/audit-and-outbound-ops.md` 第五节 |
| 5 | `.51` 整机重启 | 阻断已清、只差窗口。⚠️ 爆炸半径 **7 个服务**（含门户网关本体），`CBS RebootPending=True` + 已 85 天未重启，停机时长不可按常规估。opener＝编排文件 `[Mac] 0820-9R` |
| 6 | 阶段 C 门户导航 | 需在 Win 笔记本上改门户 HTML。板块名「HR·招聘智能体」，外链 `http://192.168.100.51:8095/hr/recruit-agent/`。与 `.51` 服务无关，不影响运行中的服务 |
| 7 | 决策代理人 | `CLAUDE.md` 框架已建，**2026-08-28 决定继续不设**。「可代」项在无代理人期间同样一律挂起等本人 |
| 8 | 06 清单剩余 | 3.3 企微 webhook（无挂载点）、9.1/9.2/9.3 沟通线。7.1/7.4 合规条款已随 audit 包推进 |
| 10 | 8.4 ＋ U6 巡检 CLI 上 `.51` | 8.4 要人在页面跑通"模糊回复→点选→带缺口确认"；U6 的巡检 CLI 从未对 `.51` 真实 `demo.db`/`decisions.jsonl` 跑过——且 U6 代码**还没部署到 `.51`**（现网是 `d104249`，早于 U6）。✅ **已闭合**（`feb49d6`）：二次发版含 U6，巡检 `EXIT=2`＝镜像尚不存在，8.4 本人跑通回勾 |
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
