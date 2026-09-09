# Session 接力 · HR 招聘智能体

> 滚动更新，覆盖旧版。新会话读完本文即可接上。
> 最后更新：2026-09-09 12:2x（Cowork `HR业务线-接力0909Q`，两条裁决已拍 + 第十五批编排完）

---

## 开场词（复制即用）

```
[Mac]0909Q-HR业务线接力
【设置】执行环境: Cowork ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（Cowork 无 worktree，只做文档、编排与派单）｜ 工作区: 仓库根（/Users/paulshao/Projects/HumanResource）｜ 派发: Cowork·HR业务线-接力0903B
读 /Users/paulshao/Projects/HumanResource/docs/session接力.md 恢复上下文，然后按【下一步】继续。
```

> 🔴 **Cowork 接力开场词同样是 opener，头两行＝标题行 + 【设置】行**（CLAUDE.md「CC 与 Cowork 两端同等适用」；09-09 Shao Peishen 指出漏了一次）。本 session 派出去的 opener，`派发` 一律写 `Cowork·HR业务线-接力0909Q`。

> 🔒 **首行只用于给读文档的人对编号，不指望侧边栏**。Cowork 侧的 session 名是摘要生成的，
> 首行无效（08-27 实测：`HR业务线-接力0827B` → 侧边栏 `HR业务线接力`）。⛔ 不要再改首行格式硬试。
>
> 🔴 **出号前必查号池台账**：`docs/openers/OP-0820-全量编排.md` 顶部「🔢 号池台账」。
> **Cowork 与 CC 共用同一号池**，`Z` 固定留给看护者。08-27 一天撞号 5 次，根因是
> 提交类 opener 只在聊天里派、从不落档 ⇒ 下轮 grep 不到 ⇒ 重派。**给出后当场登记**。

---

## 一、状态快照（2026-09-09 11:50，第十三、十四批跑完后）

| 项 | 现状 |
|---|---|
| main | `5e2573f`，**与 origin 同步（ahead 0）**。第十三批（`0909A–F`）与第十四批（`0909H–O`）共 60+ commit 全合入 |
| 工作区 | `0909R` 转场提交 ✅ `de4a32e`（含 CLAUDE.md `git stash` 禁令、两个 skill、09-09 裁决 findings）。之后 Cowork 侧又改了两处**未提交**：本文开场词补【设置】行、`.claude/skills/kickoff/SKILL.md` 加「Cowork 接力开场词模板」——**新 session 派出的第一条 CC opener 一并 add 提交**。➕ 09-09 12:2x 第十五批编排又落了三件未提交：`OP-0820-全量编排.md`（第十五批一节 + 号池 S–X 六行 + 看护者指针）、`docs/openers/0909X-泳道批次看护.md`（新）、`docs/findings/2026-09-09-Shao-Peishen-裁决-留存冲突B与TD20改法.md`（新）——`0909X` 的【二】已把五条路径写全（含 kickoff skill）。⚠️ `.claude/handoff/` 在 `.gitignore` 里，看护报告只在本机 |
| `.51` 代码 | ✅ **已发版 `95b298c`**（`0908K` 2026-09-08 五次发版成功）。⚠️ **实际范围远大于该轮 opener 所述的「只发巡检 CLI 编码修复」**——`7a48a19..95b298c` 对 `app/ scripts/` ＝ 15 文件 / +905 −118，一并上线 **WBS 2.3 备用供应商切换**、**WBS 2.5 重试耗尽转人工**（两个新 `effect_*` 节点）、**tasks 5.3 离题轮不留岗位记录/丢弃**；⇒ 今后发版 opener 的「这次发什么」必须由 `git diff <现网 HEAD>..main` 现算。快照 `C:\apps\backups\20260908-1420`（`app\` 126 文件完整；`data\` 因 `demo.db-shm` 被占用中断，不影响回滚——回滚只用 `app\`）。纯 sync，⛔ 未装依赖、未重跑 `deploy-server.ps1`。冒烟 4 项：首页 200 ｜ `GET /api/jobs` JSON 列表 ｜ 新进程 PID 9600 于 `14:22:36` 启动、**其后零 Traceback**（尾 60 行里那段 `Traceback` 时间戳为 `13:18:32`，属上一版进程的历史错误，已取证非本次回归，未回滚）｜ 🔴 **不带 `PYTHONIOENCODING` 裸跑巡检 `EXIT=0`**、6 条断言全过、断言四豁免 7 条 ⇒ `19b8937` 修复现网生效。⇒ **`0908J` 那条「加 `PYTHONIOENCODING=utf-8` 才得真结果」已作废，⛔ 不要再加环境变量**。🔴 查实遗留缺陷（早于本次发版、未修）：重复 `confirm` 时 `idempotent_effect` 抛 `sqlite3.IntegrityError: UNIQUE constraint failed: effect_log.effect_key` 而非短路，用户可见 500（恒等式未破）→ ✅ 第十二批 `0908S/T` 已修合入（撞键即 rollback 业务写、按已执行返回），**尚未上 `.51`**。ℹ️ 口径订正：`.51` 应用日志真实路径是 `logs\app.log`，**不是** `data\logs\app.log`。详见 `docs/audit-and-outbound-ops.md` §五 ｜ `0908B` 失败回滚见 `docs/findings/2026-09-08-51四次发版回滚.md` |
| pytest | main 侧 **1941 passed / 4 skipped / 0 failed**（09-09 10:2x `0909Y` 真身核验；4 条 skip 全是刻意离线/SDK 只装在 tools venv）。⛔ 别抄进 opener 当基线，见【四】 |
| 生产 | `.51:8095`，`/hr/recruit-agent`，服务正常 |
| worktree | `/private/tmp/wt-0908M`、`/private/tmp/wt-0909B`（分支均真未合 0，可安全清理；两批看护者按 opener 都没清）；旧分支若干、真未合 0 |

**变更包进度**

| 变更包 | 进度 | 剩什么 |
|---|---|---|
| ~~`ai-audit-trail-and-outbound-gate`~~ | **53/53 ✅ 已归档** | `openspec/changes/archive/2026-09-04-ai-audit-trail-and-outbound-gate`；specs 折进 `ai-decision-audit` + `outbound-approval-gate` |
| `m1-intake-quality-fixes` | **68/69** | 8.4 ✅（Shao Peishen 09-03 页面实跑，job `51b225f1`，0903L 取证）；只剩 **8.9**（归档，须 `m1-job-profile-intake` 先归档） |
| ~~`outbound-retry-audit-trace`~~ | **15/15 ✅ 已归档** | `archive/2026-09-04-outbound-retry-audit-trace`；delta 已合进 `outbound-approval-gate/spec.md`；修复已随 `3f59842` 上 `.51` |
| `hr-wecom-aibot-liaison` | **62/66** | 第 1–7 章 ✅ ＋ 8.1–8.5 ✅（第十三批：4/5/7 章；第十四批：6 章 + 8.1–8.5 + TD-14/15/16/18）。**只剩 8.6 单机灰度 / 8.7 加汤丽萍 / 8.8 一周观察 / 8.9 归档——全部要 Shao Peishen 亲自做**。灰度前置（见【二】⑮）：新 aibot 凭据进 `.env`（他 09-09 说去建）、名单 userid、`tools/liaison/.venv`、TD-19 真实建连表面实测、TD-20 改法拍板、launchd 安装（他 Terminal 跑 `tools/liaison/scripts/install_launchd.py`）。⚠️ 全部代码路径全程 fake，**尚无一次真实建连、真实投递** |
| `m1-job-profile-intake` | **60/72** | 第十批：9.6 ✅、硬门槛 1.2b/5.8/5.9 ✅、账目对齐 4 条 ✅；0905A：Web 8.1/8.2/8.4 ✅（`05e90cc`）；**第十一批：2.3+2.5 ✅（`7ef4bb2`）、5.3 ✅、4.4 ✅**。剩 12 条，🔴 **已全部有去向，本包内无待做代码**：**C 类 11 条已移出**——8 条企微（1.5b/3.x/9.2）→ `hr-wecom-aibot-liaison`（`0908A` 已立包），3 条调度（1.7/5.6/6.8）→ **调度基础设施（待立项）**（`0908I` 09-08 落档，依据 Shao Peishen「按推荐」裁决）；**D 类只剩 9.1**（10 个真实岗位重跑、HR 与业务经理双方评估，**要真人参与**）。⇒ **归档判定权在 Shao Peishen**，⛔ 代理人不代拍 |

**已跑完的批次**：第四至第十四批。第十三批报告 `lanes-20260908-200757-第十三批看护报告.md`，第十四批报告 `lanes-20260909-081142-第十四批看护报告.md`（`lanes-20260908-235021-*` 是同批 launchd 发车失败那半份）。
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

### ~~⑬ 第十三批~~ ✅ 跑完（6/6，值守 18→47/66，pytest 1378→1663；B 的 BUDGET-HIT 是虚惊）

- `0908U`：run-lanes.sh 自拷贝执行 + 零条目路径自检可达 + TD-17（`delivery.py:12` 非法转义）。CC 新开、不勾、单独跑。正文 `docs/openers/0908U-run-lanes自拷贝执行与TD17.md`
- 第十三批（编号按发车日 0909，0908 字母池只剩 U–X）：归档队列泳道 `0909A→B→C→D`（第 4 章消息归档 → 第 5 章任务队列，同写 `storage/effects.py` 故串行）∥ 连接泳道 `0909E→F`（第 7 章连接生命周期，独占 `session*` `alerts*` `__main__.py`）。dry-run 预期 A20/B23/C19/D23/E20/F23，Σ=6。正文 `docs/openers/0909Z-泳道批次看护.md`
- 🔴 09-08 晚 Shao Peishen 问「每批都要点 Run？」→ 是（09-04 起分类器拦看护者起脚本，白名单无效）。根治＝`0909G` launchd WatchPaths 触发器：看护者写 `.claude/handoff/launch/<ts>.request` 文件即发车，起进程的是 launchd 不是 Claude。装 launchd 那一步他在 Terminal 跑一次 `scripts/install_lane_launcher.py`
- 📅 **09-09 Shao Peishen 说三件明天给**：备用 LLM 供应商 key / 企微 aibot BotID+Secret / 群 webhook。🔴 接收口径：**凭据不进聊天、不进 git**——他自己写进 `.env`（Mac 本机 `.env`：`HR_LIAISON_BOT_ID` `HR_LIAISON_BOT_SECRET` `HR_LIAISON_GROUP_WEBHOOK`；`.51` 的 `.env`：`LLM_FALLBACK_*` 四项，名字以 `.env.example` 为准），Cowork/CC 只出「验证存在性 + 连通性」的 opener，⛔ 不读值、不回显、不写进任何被跟踪文件
- ~~等 Shao Peishen 的三件~~ **09-09 已裁决**（落档 `docs/findings/2026-09-09-Shao-Peishen-裁决-无备用LLM与aibot独立注册.md`）：① 备用 LLM **不要了，只用 DeepSeek**——`LLM_FALLBACK_*` 四项两台机器都留空，代码零改动（不配＝无备用，主家故障走 2.5 转人工）；② aibot **⛔ 不与 Windows 共用**——官方文档「连接数量限制」：同一机器人同一时间只能一条长连接，新连接踢旧连接（design D1 坐实）→ 他在企微后台**新建**一个 aibot 给 Mac，**用超级管理员账号建**（`from.userid` 才是明文，否则是加密 userid）；③ 群 webhook 他自己写进本机 `.env`。🔴 09-09 他把 Windows 侧 BotID/Secret 与 webhook 贴进了聊天——Cowork 侧未复述、未落档；那对凭据 Mac 侧⛔ 不用
- 准入名单 `userid`：他 09-09 说「汤丽萍的 chatid 好像已经存档」（企业AI转型仓 `6-人才与组织/部门AI专员跟进/README-跟进机制与命名约定.md`《企微 chatid 名录》，三种格式并存）。⚠️ 名单认的是新 Mac 机器人看到的 `from.userid`，不是 Windows 机器人的单聊 chatid；**以企微管理后台通讯录「账号」为准**填 `tools/liaison/config/whitelist.yaml`，存档值只作核对（相同即一致，不同以通讯录为准）。两人（汤丽萍、邵培申）都要填，留空＝fail-closed
- 第十三批 09-08 20:07 发车（点 Run，PID 见 `0909Z` 那个 session）；20:3x 时 A（第 4 章计划）OK、E（第 7 章计划）PARTIAL 已摘标注，B/C/D/F 在跑。报告等 `0909Z`

### ~~⑭ 第十四批~~ ✅ 跑完（8/8，值守 47→62/66，pytest 1663→1941；launchd 发车只成一半、退路点 Run）

- Shao Peishen 09-08「第十四批任务可以现在先理出来吗？能开尽开，不要被泳道上限束缚」→ 五条零重叠泳道：**群通知** `0909H→I`（第 6 章，真实投递开关不在内）∥ **留存** `0909J→K`（8.1–8.2）∥ **日志** `0909L→M`（8.4）∥ **运行守护** `0909N`（8.3 launchd 模板 + 8.5 守护测试，轻量单条）∥ **技术债** `0909O`（TD-14/15/16/18，轻量单条）。dry-run 已在 Cowork VM 核过：5 泳道 8 条，Σ=N=8（`--only` 生效）
- 发车参数（看护者写进 `.claude/handoff/launch/<ts>.request`）：`--full-auto --yes --only 0909H,0909I,0909J,0909K,0909L,0909M,0909N,0909O --max-parallel 5`。`--only` 隔开第十三批可能残留的标注；`0909Y` 会自己等第十三批 `run-lanes.sh` 退出再发车，**现在就能贴**
- 🔴 **TD-15 裁决**：按「同一份名单内容只记一次 ERROR（内容哈希去重），内容变了再记」处理，不降级、不缓存名单。他贴 `0909Y` 即认可；不认可则从 `--only` 去掉 `0909O`
- 唯一共享文件 `tools/liaison/__main__.py`：留存加末尾子命令、日志加 main() 首行，各一处；真身核验第 8 条查"两头都在"
- 不进本批：8.6–8.9（他亲自）、第 6 章真实投递（8.6 时验）、`.51` 六次发版（`0909P` 已备好，**等他回「发」**——幂等撞键短路修复上线，纯 sync）、明天三件凭据的验证 opener（他写进 `.env` 后另出）
- 正文：`docs/openers/0909Y-泳道批次看护.md`（看护）、`docs/openers/0909P-51六次发版幂等撞键修复.md`（发版）；块与号池在 `OP-0820-全量编排.md` 第十四批节

---

### ⑮ 09-09 起：等他三样东西，然后第十五批 ＋ 8.6 灰度

**A. 要 Shao Peishen 拍的（不可代）** —— ✅ **两条 2026-09-09 中午已拍**，落档 `docs/findings/2026-09-09-Shao-Peishen-裁决-留存冲突B与TD20改法.md`：冲突 B 取方案 ②（终态队列行连归档一起清）；TD-20 取改法 ①（启动不闭旧窗，`on_connected` 才闭）。两条均已排进第十五批（`0909T` / `0909U`），下面原文留作判据记录。

1. 🔴 **冲突 B：名单内消息的归档在 FK 下永远清不掉**（`0909K` 登记 P1，未替他拍）。`liaison_task.msgid → liaison_message(msgid)` 无 `ON DELETE`，opener 又写死队列行永不删 ⇒ 汤丽萍/邵培申的归档全进 `blocked_by_queue` 桶，只有名单外发送人的归档会被 180 天清理。属个人信息留存期问题。
   - ① 保持现状（名单内归档永久保留，只报计数）——**不推荐**，与 D13「180 天」和 proposal 合规说明相悖
   - ② **推荐**：队列行到终态（`✅ 已推送`）且超 180 天的，连同其归档一起清；`🆕 待发` / `⏸ 暂缓` 的不动（仍是活台账）。D13「队列行不参与自动清理」改成「非终态队列行不参与」，spec + design 同步改一句，代码在 `retention.py` 加一个桶
   - ③ 台账行删除前把 `liaison_task.msgid` 置空（去掉 FK 依赖）——队列行失去回指，5.5「回指来源消息」被打破，不推荐
2. 🔴 **TD-20 三选一**（`0909F` 留步；8.6 前必须定）：① 启动时不闭旧窗口、等 `on_connected` 真连上再闭 ② 启动另开新窗专等 `on_connected` ③ 接 `run_forever` 的 `on_attempt_failed`。**推荐 ①**：改动最小、告警自然带真实恢复时间、不新增窗口类型；③ 可作为 ① 之上的加强，不单独选。

**B. 他自己要做的三样（09-09 已说去做）** —— 进度 09-09 12:3x：
- ✅ **Mac 端 aibot 已建**，`HR_LIAISON_BOT_ID` / `HR_LIAISON_BOT_SECRET` 已写进本机 `.env`（权限 600，`.gitignore:2` 忽略，`git status` 零条目）。🔴 **口径变更**：他 09-09 12:3x 本人放宽了「凭据不进聊天、只由他本人写」那一条（原话「这个不是敏感信息，只为公司内部群沟通，不用担心」）⇒ **Cowork 可代写进本机 `.env`**。⛔ **不变的仍然不变**：凭据不进 git、不进任何被跟踪文件、不回显取值、不写进 `.env.example` 与 opener 正文
- ✅ `HR_LIAISON_GROUP_WEBHOOK`（「人力AI保障群」）已于 09-09 12:4x 一并写进 `.env`。三个变量齐（len 35/43/89）。守卫已核：`.env` 权限 600、`gitignore:2` 命中、被跟踪文件里真实 `qyapi.weixin.qq.com` webhook **0 命中**（`tools/liaison/tests/test_notify_*.py` 里那两处是 `example.invalid` 假值，判据要认真实域名——与第十四批报告分歧②一致）
- ⏸ `whitelist.yaml` 两人 `userid` **仍是空串**（留空＝fail-closed，谁都进不来）；以企微管理后台通讯录「账号」为准
- ⏸ `tools/liaison/.venv` **仍不存在**（`install_launchd.py` 对此 fail-closed）。🔴 **Cowork 代建不了**：device_bash 跑在 Mac 上的一个独立 Linux VM 里，在挂载目录建 venv 会往 macOS 路径里塞 Linux 解释器。⇒ 必须由 macOS 上的 CC 建，已写成 `0909AA`（引用式，`docs/openers/0909AA-凭据验证与liaison-venv.md`）
- ℹ️ **口径订正**：`probe_ws_surface.py` 的 docstring 明写「⛔ 只做内省：不建连、不发消息、不需要任何真实凭据」。本文此前写的「真实建连一次」**是错的**，⛔ 不要据此改脚本或给它加凭据参数。TD-19 的真正销账要等 8.6 用真实凭据把 `client.run()` 端到端跑通

- 企微后台**新建** aibot（超管账号建；API 模式选长连接；加进「人力AI保障群」），BotID/Secret 与群 webhook 写进本机 `.env`（`HR_LIAISON_BOT_ID` / `HR_LIAISON_BOT_SECRET` / `HR_LIAISON_GROUP_WEBHOOK`）。⛔ 凭据不进聊天不进 git；Cowork 只出「验存在性 + 只读探针」opener
- 通讯录里抄两人「账号」，填 `tools/liaison/config/whitelist.yaml` 的 `userid`（留空＝fail-closed）
- 他回「写好了」后 → 出 opener：验三个变量非空（不回显）→ `tools/liaison/.venv` 建起来装 `requirements.txt` → 跑 `tools/liaison/scripts/probe_ws_surface.py`（TD-19 只读探针，真实建连一次，新机器人不影响 Windows）→ 跑通则改常量 + 落 findings，跑不通则留步（`SdkSurfaceUnverifiedError` 就是设计的拒启动）

**C. 第十五批候选** —— ✅ **已编排发车（见下面 ⑯）**。下表留作取舍记录：六条候选里五条进了本批；队列债（TD-23/24）因「零生产调用方、8.x 推送接线前还即可」推到第十六批。

| 泳道 | 内容 | 触碰区 |
|---|---|---|
| launchd 修复 | `scripts/install_lane_launcher.py` 生成的 plist 加 `AbandonProcessGroup=true` + `EnvironmentVariables.PATH`（含 `~/.local/bin`）；单测断言两键在位；他 Terminal 重装一次。验收＝写一份 `.request` 后 `lanes-*` 目录真建出来 | `scripts/`、`tests/test_lane_launcher.py`（⛔ 不碰 run-lanes.sh） |
| 留存收口 | 冲突 B 按裁决落地 ＋ TD-30 日志时间维度（独立 `HR_LIAISON_LOG_RETENTION_DAYS` 默认 30，⛔ 不复用 180） | `tools/liaison/retention.py`、`logsetup.py`、spec/design 一句 |
| 会话 | TD-20 按裁决改 `session.py` 状态机 ＋ TD-21 礼貌回复 at-most-once | `tools/liaison/session*`、`inbound.py` |
| 队列债 | TD-23（`defer_task` 读状态）＋ TD-24（`mark_task_pushed` 第二道防线）——当前零生产调用方，8.x 推送接线前必须还 | `tools/liaison/queue.py` |
| 群通知债 | TD-25（非限流错误不落 `pending_resend`）＋ TD-27（`MODE_REJECT` 不发空 markdown）＋ 待重发驱动器（`0909H` 留步说属第 8 章，但 tasks.md 无此条 → 先 openspec 补一条 8.x 再建） | `tools/liaison/notify/` |
| 幂等基座 | TD-33：`app/storage/idempotency.py` 非 `IntegrityError` 也回滚——影响所有 effect，铁律 1 近亲；改完随 `0909P` 一起上 `.51` | `app/storage/idempotency.py`、`tests/` |

⛔ 不进泳道：8.6–8.9（他亲自）；`0909P` 发版（等「发」；建议等幂等基座泳道合入后再发，一次带上 TD-33，「这次发什么」现算）；launchd 重装与 liaison launchd 安装（他 Terminal）。
下一批看护者判据要收窄两处（`0909Y` 报告分歧①②）：`.env.example` 允许注释行、只禁取值；webhook grep 只认 `qyapi.weixin.qq.com` 真实域名。

### ⑯ 第十五批（`0909S–W`，看护者 `0909X`）· ✅ **已跑完（5/5，3 OK ／ 2 PARTIAL，零 FAIL）**

报告 `.claude/handoff/lanes-20260909-124030-第十五批看护报告.md`；摘牌提交 `21db086`。
**六条 TD 全销**：TD-20（`08d8784`）／TD-25+TD-27（`26986e8`）／TD-30+TD-34（`ac686d1`）／TD-33（`25d8715`）。两条 PARTIAL 都合法：`0909S` 等他重装 launchd、`0909W` 等 `0909P` 上 `.51`。

🔴 **本批产出的三件要接着处理的**：
1. **新 TD-35**（`0909T` 当场登记）：`assert_effect_log_identity` 对 `liaison_task` 的严格恒等与裁决一冲突——终态队列行被连带清掉后，`effect_log` 行数必然大于 `liaison_task` 行数，机器守卫会因**完全正当**的理由变红。`0909T` 没擅改共享守卫（该文件归别的泳道），改为加 `assert_retention_accounting` 第二条记账等式兜底并用 `pytest.raises` 钉成可见。不阻塞 8.6
2. 🔴 **发车机制的新实证**：分类器拦的是 `.claude/handoff/launch/` **这个路径本身**（同一行内容写别处成功）⇒ `0909S` 修好 plist 并不能让 A 路通，**装完之后看护者仍然写不了 `.request`**。⛔ 不要再把「装 launchd」当作根治
3. **未提交**：`openspec/changes/lane-launch-armed-scan/`（proposal + design + specs，某条泳道/看护者起草的）——把发车触发换成「launchd 定时扫**已提交的**编排文件 ＋ 一枚只有 Shao Peishen 的 git 身份能签的武装令牌」，授权动作从「发车那刻点 Run」前移成「编排完成时签一次」。🔴 **要他拍**：这改的是发车机制的形状，且 proposal 自己写明「扫编排文件就能通」属**未验证的设计推测**

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
### ⑱ 灰度（8.6）前置清单 · 2026-09-09 17:xx 重排

⚠️ **顺序变了**：`install_launchd.py` 此前与其他项并列，实为**排在 TD-19 之后**的下游项。
理由见 G-3。

| # | 事项 | 谁做 | 状态 | 判据（怎样算完） | 不做会怎样 |
|---|---|---|---|---|---|
| **G-1** | 两人企微 `userid` 填进 `whitelist.yaml` | Shao Peishen 提供 → 已代填 | ✅ **已完成**（`e969f1b`） | `load_whitelist(SHIPPED_CONFIG) == {"TangLiPing", "ShaoPeiShen"}`，已成断言 | — |
| **G-2** | 核对 `ShaoPeiShen` 的大小写 | Shao Peishen | ✅ **已确认**（2026-09-09 本人回「ShaoPeiShen 正确」） | 已比对企微通讯录「账号」列，与 `whitelist.yaml:22` 逐字符一致 | — |
| **G-3** | 还 **TD-19**（`client.run()` 的 async→同步适配） | — | ✅ **已完成**（代码 `0909AC`，端到端实测 `0909AE`） | `0909AE` 首次真实建连：连接建立成功、认证通过 ⇒ TD-19 销账。⚠️ 同次实测暴露的心跳 bug 是**另一条**（TD-38），⛔ 不算本条未完 | — |
| **G-4** | 在 Terminal 跑 `install_launchd.py`（不带 `--dry-run`） | **Shao Peishen**（脚本 docstring 明写「Claude ⛔ 不代跑」） | ⛔ **暂缓——等 G-8/G-9** | G-8（TD-38）与 G-9（TD-39）都通过后再跑。⛔ TD-38 未还就装 job ＝ 无人值守地反复对企微洪泛。`--dry-run` 已于 09-09 验过，渲染无误、四条路径正确、无凭据取值 | **现在跑就是造一台刷屏机**：plist 是 `RunAtLoad`+`KeepAlive`+`ThrottleInterval=30`，而进程 `exit 4` 立刻返回 ⇒ 每 30 秒重启一次、每次立刻失败、`launchd.err.log` 无限追加同一条报错 |
| **G-5** | 还 **TD-36**（假凭据会真实建连） | — | ✅ **已完成**（`0909AC`，与 TD-19 同一 commit） | 两条用例改走 `--self-check`（跑完全部校验、建连前退出），另加 `tests/netguard/` 网络闸门：非回环连接一律 raise，进程内＋子进程两条都装。⚠️ 落地中实测到一次经本机 `127.0.0.1` 代理的真实外发，已修并落档 `docs/findings/2026-09-09-测试网络闸门被本机代理绕过.md` | — |
| **G-7** | 还 **TD-40**（`.env` 的 `HR_LIAISON_*` 让 app 配置加载不了） | — | ✅ **已完成**（`0909AC`，2026-09-09 他答 `1c` ＝ 改法 ③） | 凭据已迁到 `tools/liaison/.env`（0600）；`Settings()` 正常实例化；根 venv 全量 **2041 passed / 0 failed**（迁移前 23 failed）；两道回归岗在位 | — |
| **G-8** | 还 **TD-38**（`heartbeat_interval` 传秒当毫秒，心跳 ×1000） | 泳道，opener 已就位：`docs/openers/0909AF-TD38心跳单位修复.md` | ⏸ **已派号未发车**（`[Mac]0909AF`） | 常量改名按毫秒口径 ＋ 补 AST 钉子 ＋ 复核 `reconnect_interval` 注释口径；⛔ 该泳道不做真实建连 | 🔴 值守通道保持不了在线，且每次拉起都对企微洪泛 ≈32 次/秒，有被限流甚至封号的风险 |
| **G-9** | 复核 **TD-39**（断线事件疑似没到 `LiaisonSession`） | 泳道，opener 已就位：`docs/openers/0909AG-TD39断线复核.md` | ⏸ **串行在 G-8 之后**（`[Mac]0909AG`） | 心跳修对、连接稳定后手动断/复 Wi-Fi，验六项判据；三项全绿则销为虚惊，任一不绿则升级为缺陷 | 「服务看起来在跑、其实早断了」——中断窗口一条都不会有，「没有告警」被当成「一切正常」 |
| **G-6** | `0909P` 发版到 `.51` | **Shao Peishen** 拍（发版不可代） | ⏸ 等他一个「发」 | 建议一次带上 TD-33，「这次发什么」发车前现算 | — |

**2026-09-09 结存（`0909AC` 后订正）：他手上多了一件 —— G-3 的端到端那一步。**
G-1/G-2/G-5 已闭合；**G-3 的代码已就位，只差他在 Terminal 跑一次真实建连**（8.6 不进泳道，
判据见上表 G-3 那一格）；G-4 仍等 G-3 跑通后解锁；G-6 等发车范围算清。
⇒ **球已交回他手上**：`0909AC` 泳道已完成，TD-36 销账、TD-19 代码就位，只差 G-3 那一次真实建连。

### ⑲ 第十六批发车前置：`0909AB` superpowers 可达（2026-09-09 由 `0903B` 会话派，⛔ 不进泳道）

| # | 事项 | 谁做 | 状态 | 判据（怎样算完） | 不做会怎样 |
|---|---|---|---|---|---|
| **P-1** | `0909AB`：插件（superpowers 及其它 scope=project 且 projectPath≠本仓库的）改装 **user 作用域**；无头 + worktree 两条探针复验；仍不通才给 run-lanes.sh 加 `--plugin-dir` | Shao Peishen 贴 CC opener（`docs/openers/0909AB-superpowers无头可达.md`，引用式） | ✅ **09-09 已跑完**：`--scope user` 装上（6.3.0），两条无头探针修后均 `LOADED`（修前均 `Unknown skill`），`subagent-driven-development` 另验 `SDD-LOADED`；**未走 `--plugin-dir` 退路，`run-lanes.sh` 一行未改** | 无头探针 `cd 仓库根` 与 `cd /private/tmp` 两次都回 `LOADED`；skill 模板里「取不到→手工走」已删、看护者预案改红灯；commit 已推 | 第十六批起每条泳道仍 `Unknown skill`，继续靠执行者手工走协议（08-27 起已六批），机器保证为零 |
| **P-2** | 第十六批发车 | Cowork 编排 → 看护者 | ✅ **P-1 已跑完，前置解除**，可编（编号改用 `0910`）。新口径：泳道 opener 里⛔ 不再写「取不到→手工走」，一律写「回 `Unknown skill` ⇒ 登记『⏸ 留步：superpowers 不可达』并停」 | 看护报告「是否真调到」栏全 ✅ | 发早了＝再跑一批"靠运气" |

⚠️ Shao Peishen 09-09 口径：**修好以后一律走规范流程**——`Skill(superpowers:…)` 不可达即环境故障、泳道停，⛔ 不再允许"按磁盘 SKILL.md 手工走"。过往六批的交付**不回炉**（看护报告逐条核过：两阶段 review、fix loop、终审都手工走了，丢的是机器保证不是步骤）。


全是**技术债与机制债**，五条泳道零重叠、全走轻量通道（不走 spec-to-plan / run-build）：

| 泳道 | 编号 | 做什么 | 触碰区 |
|---|---|---|---|
| 发车机制 | `0909S` | launchd plist 两缺陷（`AbandonProcessGroup` + `EnvironmentVariables.PATH`）＋单测；⛔ 不装 | `scripts/install_lane_launcher.py`、`tests/test_lane_launcher.py` |
| 留存 | `0909T` | 冲突 B 裁决落地 ＋ TD-30（日志 30 天）＋ TD-34（咬不住的回归测试） | `retention.py`、`logsetup.py`、`liaison-message-archive/spec.md`、`design.md` §D13 |
| 会话 | `0909U` | TD-20 裁决落地 ＋ spec 两处改口径 | `session.py`、`liaison-channel-session/spec.md` |
| 群通知债 | `0909V` | TD-25（按 `last_errcode` 过滤，**不加列**）＋ TD-27（`MODE_REJECT` 改 raise） | `notify/webhook.py`、`notify/store.py` |
| 幂等基座 | `0909W` | TD-33（`effect_log` INSERT 非 `IntegrityError` 也回滚）；随 `0909P` 上 `.51` | `app/storage/idempotency.py` |

- dry-run 已在 Cowork 侧核过（用 `--plan` 指本机挂载路径，绕开脚本里的 `REPO` 绝对路径常量）：**5 泳道 5 条**，正文 18/22/19/19/19 行，`Σ=N=5`（`M=6`，多出的一处是第十三批 `0909B` 的残留标注，BUDGET-HIT 不自动摘、实为虚惊）
- 发车参数：`--full-auto --yes --only 0909S,0909T,0909U,0909V,0909W --max-parallel 5`
- 🔴 **本批唯一跨泳道共享文件 `docs/tech-debt.md`**（T/U/V/W 各销自己那几条，段间相隔 30 行以上）。真身核验判据：销账数比基线 **+6**（TD-20/25/27/30/33/34），且 TD-23/24/26 仍未销
- 🔴 **本批五条泳道都不碰 `tasks.md`**——8.1–8.5 已勾、8.6–8.9 留他本人，本批没有要回勾的行；跑完 `grep -c '^- \[x\]'` 应一字不变
- 🔴 `0909S` 修的正是 launchd 发车缺陷，**修完要他在 Terminal 重装一次才生效**，所以本批自己大概率仍走「点 Run」退路（`0909X` 的【四】【五】已写死判据与无引号退路命令）
- 不进本批：8.6–8.9（他亲自）；`0909P` 发版（等「发」；建议等 `0909W` 合入后一次带上 TD-33，「这次发什么」由 `git diff <现网 HEAD>..main` 现算）；TD-23/24（第十六批）；TD-26（与 `0909V` 同文件，下一批单独还）；三件凭据的验证 opener（等他写进 `.env`）
- 🔢 号池：`0909` 字母池 **S–X 已全部派出**，09-09 当日已无可用字母。第十六批若仍在 09-09 发车，必须改用 `0910` 并在号池台账另起一段

**D. 顺手件**：清理 `/private/tmp/wt-0908M`、`wt-0909B`（真未合 0）；CLAUDE.md 252 行已超 250 红线 2 行，下次改它时拆一段出去。

---

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

### ⑲ 09-09 晚：8.6 首次真实建连已跑，卡在心跳单位（`[Mac]0909AE` 出）

**已发生的事实**：`0909AE` 用真实凭据首次让值守服务连上企微——**连上了**（鉴权通过、
`liveness.json` 写出 `connected`、`liaison.db` 建出五张表）⇒ **TD-19 已销账**。
但只在线 **44 秒**：SDK 的 `heartbeat_interval` 单位是**毫秒**，`session_client.py:37` 传的是
`DEFAULT_HEARTBEAT_SECONDS = 30`，被当成 **30 毫秒** ⇒ 心跳 ×1000（44 秒内 1399 次）⇒
企微返 `45009 Too many requests` 判死连接。全文 `docs/findings/2026-09-09-首次真实建连实测.md`，
commit `159305f`。新登记 **TD-38**（阻断 8.6）、**TD-39**（断线事件疑似未达状态机，⚠️ 待复核）。

**Shao Peishen 2026-09-09 已答 `1a，2a，3a`**，三条待办如下（⛔ 串行，AG 依赖 AF 合入）：

| # | 谁做 | 状态 | 判据：怎样算完 | 不做会怎样 |
|---|---|---|---|---|
| 1 | `[Mac]0909AF`（CC，worktree）| 🆕 待派 | `DEFAULT_HEARTBEAT_MS = 30_000` 合入 main；AST 钉子能挡住改回 30；`reconnect_interval` 注释口径改对；TD-38 销账 | 值守通道**无法保持在线**，且每次拉起都对企微洪泛 ≈32 次/秒，bot 凭据有被限流/封禁风险 |
| 2 | `[Mac]0909AG`（CC，主工作区，**需他本人在场**手动断/复 Wi-Fi）| 🆕 待派，**前置＝AF 已合入** | 心跳实测为 `30000ms`；断网后 `liveness` 翻 `disconnected` ＋ `outage_window` 开窗；复网后自动重连并闭窗（六项全过）| "服务看起来在跑、其实早断了"这类**无症状**故障没人守；8.6 灰度建立在没验过的重连上 |
| 3 | launchd 装机（`install_launchd.py`，他本人在 Terminal 跑）| ⏸ **已明确暂缓**（他答 `3a`）| TD-38 已还 **且** AG 六项全过后，由他重新拍板 | 现在装＝一台无人值守地反复重启、反复洪泛、反复吃 45009 的机器（`KeepAlive=true` + `ThrottleInterval=30` 会放大） |

⚠️ **口径订正**：`0909AE` opener 结尾那句「⏸ 下一步：Shao Peishen 在 Terminal 跑
`install_launchd.py` 装常驻」**已作废**——那句写在不知道 TD-38 存在时。以本节第 3 行为准。

⚠️ **`0909AE` 的两处偏离**（已如实落档，非隐患）：① `git pull --rebase` 时
`tools/liaison/__main__.py` 与 `0909AD` 的 `send-followup` 块冲突，两侧都是文件尾纯插入，
按项目口径**合并双方**、正文一字节未改；② 观察窗提前 51 秒收停（判定是对企微洪泛后主动
SIGINT），事后证明不是多虑——45009 在第 44 秒就到了。

📌 **一条值得记住的教训**：这个 bug 之所以活到真实建连才暴露，是因为守它的断言是**同义反复**——
`assert options.heartbeat_interval == session_client.DEFAULT_HEARTBEAT_SECONDS` 拿传进去的值
跟它自己比，单位错成什么样都绿。⇒ **配置项断言必须写绝对值，⛔ 不许拿常量跟自己比。**
`0909AF` 已把"先让新断言在旧代码下真的红"写成强制步骤。

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
