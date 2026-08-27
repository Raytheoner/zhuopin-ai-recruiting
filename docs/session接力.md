# Session 接力 · HR 招聘智能体

> 滚动更新，覆盖旧版。新会话读完本文即可接上。
> 最后更新：2026-08-27（Cowork 业务线）

---

## 开场词（复制即用）

```
HR业务线-接力0827D
读 /Users/paulshao/Projects/HumanResource/docs/session接力.md 恢复上下文，然后按【下一步】继续。
```

> ✅ **实验已结束，结论落定（2026-08-27）：Cowork 侧走摘要，首行无效。**
>
> 上一轮首行写 `HR业务线-接力0827B`，侧边栏显示 **`HR业务线接力`**——
> 既不是原样，也不是截断（截断会留 `HR业务线-接力`），**连字符与编号一起被改写掉了**。
> 对应实验表第三行：❌ 另找路径，⛔ **不要再改开场词首行的格式硬试**。
> 已落进 `CLAUDE.md` / `kickoff` skill / `check-opener-header.py` 三处真源。
>
> ⚠️ **反例别拿去复活旧假说**：侧边栏里 `Session context 业务总线0826` 是带编号的。
> **未核实它是不是手工补的**——要拿它当"摘要有时保留编号"的论据，先去问，别推断。
> （CC 侧那三条假说就是这么栽的。）
>
> 🔒 **首行仍然要写，但它的用处只剩"给读接力文档的人对编号"**，不再指望侧边栏。
> ⚠️ 编号跳到 `0827D` 是刻意的：`0827B`/`0827C` 已被第四批 CC 泳道占用，
> 同日同号在两个界面指不同东西会读岔。**Cowork 与 CC 共用一个 MMDDX 号池。**

---

## 一、状态快照（2026-08-27）

| 项 | 现状 |
|---|---|
| main | `a171cef` — 单元 A/B/C/D/E 与 audit U1 全部已合并；与 `origin/main` 同步（0/0） |
| 测试 | **222 passed**（pytest 实测。⚠️ 别用 `grep -c "def test_"`，那个数是 221，参数化/类内方法对不上） |
| 生产 | `.51:8095`，`/hr/recruit-agent`，结构化日志版已上线（08-19 发版） |
| 活跃变更包 | `m1-intake-quality-fixes` **49/69**（第 3/4/5/6 章全勾，剩第 7 章 F、第 8 章 G）<br>`ai-audit-trail-and-outbound-gate` **6/53**（第 1 章 U1 全勾，剩 U2–U7）<br>`m1-job-profile-intake` 33/71（08-26 已按现实重写 WBS，四类归档） |
| 待提交 | ⚠️ 工作区有 3 改（0827D 轮 Cowork 产出：opener 补 `set_session_title`），见【二、下一步】① |

**M1 demo 已有 3 位业务经理试用过**（pilot 08-16~08-18）。`m1-intake-quality-fixes`
整个变更包就是为修 pilot 暴露的问题而立。

---

## 二、下一步

### ~~① 提交 0827B 轮 Cowork 产出（6 改 1 新）~~ ✅ 已完成 08-27

三条 commit 已在 `origin/main`：`5e1e151`（set_session_title 硬规则）、`a5fc8d3`
（lane-dispatch skill）、`a171cef`（run-lanes 链式 + 锁预检 + 预算上调）。

### ① 提交 0827D 轮的 opener 补丁（工作区 3 改）

昨天定的「CC opener 第 3 行必须调 `set_session_title`」只落进了 CLAUDE.md / kickoff skill /
hook，**没回填到已有的 opener 文件**——编排文件里当时一条都没带。本轮补齐：

```
CLAUDE.md                            豁免口径；Cowork 命名实测结论（推翻「取自首行」）
.claude/skills/kickoff/SKILL.md      会话命名节：实验段改结论段；-n 标为未实测
docs/openers/OP-0820-全量编排.md      0826Z 看护者补第 3 行；0827B/0827C 加豁免注明
docs/openers/run-lanes.sh            🆕 开跑前 set_session_title 自检；订正 -n 那段错"实证"
scripts/hooks/check-opener-header.py  Cowork 命名结论；注明本判据扫不到存量文件
docs/session接力.md                   本文件
```

**为什么 0827B/0827C 是加注明而不是补那一行**：这两条由 `run-lanes.sh` 以
`printf | claude` 无头启动，是 print 模式、根本不进侧边栏，没有 session 名可设；
真给它加上，无头 session 会去调一个未必挂载的 MCP 工具。但**不能默默省略**——
下一个人看见会当成漏写去"修"。所以块外写明豁免理由，并在 CLAUDE.md 里落了口径。
`0826Z` 是明确要贴进 CC Desktop 的交互 session，**不豁免，已补**。

✅ **已验证**（在 Cowork VM 里单独跑抽取与自检逻辑，未跑整脚本——它 `command -v claude` 会先退）：

| 场景 | 结果 |
|---|---|
| 真实文件 | `0827B`/`0827C` 各**正文 44 行**，`PRECHECK_BAD=0`，无缺注明 ✅ |
| 反例A：块内误补 `set_session_title` | `PRECHECK_BAD=1` → `exit 13` 拒跑 ✅ |
| 反例B：删掉豁免注明 | 只进 `EXEMPT_MISSING` 报 WARN，**不拒跑** ✅ |

加的三行引用注释不影响 awk 解析（仍出 `溯源/0827B` + `审计/0827C` 两条）。

**自检为什么是这个方向**（容易看反）：本脚本起的每条都是 `claude -p` 无头，那个 MCP 工具
未必挂载、名字另由 `-n` 给 ⇒ 对这些块，**正确状态是「不带那一行」**。
所以硬拒的是「块内误补」（真会炸），只 WARN 的是「块外缺注明」（本次不炸，
但下一个人看见会当成漏写去补，于是变成前一种）。

⚠️ `.git/index.lock` 是 Cowork VM 的 bash 建的、VM 删不掉，**CC 那边提交前先
`rm -f .git/index.lock`**。（0827D 轮全程用了 `git --no-optional-locks`，本轮**没留锁**。）

### ② 然后发第四批泳道（两条，可真并行）

| 泳道 | 编号 | 做什么 | 触碰区 |
|---|---|---|---|
| 溯源 | `0827B` | 单元 F 开工（第 7 章·字段溯源与编造率度量） | `intake_agent.py`/`nodes.py`/`state.py`（worktree 内） |
| 审计 | `0827C` | audit U2 开工（`app/audit` 模块） | **全新目录，与整个仓库零文件重叠** |

```bash
cd /Users/paulshao/Projects/HumanResource
bash docs/openers/run-lanes.sh --dry-run              # 预期 2 泳道 2 条，各 44 行
bash docs/openers/run-lanes.sh --chain --full-auto --yes
```

看护用编排文件里的 `[Mac]0827Z-泳道批次看护`（已补第 3 行 `set_session_title`，
且预期值已按本批改写：2 泳道 2 条各 44 行、核验 m1 第 7 章 11 项 + audit 第 2 章 9 项、
pytest 222 作**下限**不作靶子、尾步加 `git worktree prune`）。
**首次跑 run-build 建议先不加 `--chain`**，拿到耗时与用量样本再开链式。
开跑前看一眼 `/usage` 的 5 小时窗口余量——并行两条 run-build 用量翻倍，
`--budget` 那个美元数在 Max 订阅下不是钱闸，拦不住用量窗口。

⚠️ ① 与 ② **可并行**：② 起的泳道只改 `app/` 下的代码，① 只改 `CLAUDE.md` 与 `docs/openers/`、
`docs/session接力.md`，零重叠。②读的是磁盘上的编排文件，① 提交与否都不影响它读到的内容。

---

## 三、待决策 / 悬置

| # | 事项 | 状态 |
|---|---|---|
| 1 | ~~`intake-turn-observability` 的「重试全部失败时记录累计耗时」~~ | **✅ 已关闭 08-19**：走窄化路 B，spec 已改，归档阻塞解除 |
| 2 | 阶段 C 门户导航 | 需 Paul 在 Win 笔记本上改门户 HTML。板块名「HR·招聘智能体」，外链跳 `http://192.168.100.51:8095/hr/recruit-agent/` |
| 3 | `.51` 整机重启验证 | **阻断已清、只差窗口**。⚠️ 爆炸半径订正为**另外 7 个服务**（含门户网关本体）；`CBS RebootPending=True` + 85 天未重启，停机时长不可按常规估。推荐窗口 08-22 周六 08:00 CST。opener＝编排文件 `[Mac] 0820-9R` |
| 4 | ~~`deploy-server.ps1` 的 ACL 段~~ | **✅ 已核实 08-26**（`2d5eaf0`），06 清单 1.1 一并落地 |
| 5 | 决策代理人 | `CLAUDE.md` 里框架已建，人选待 Shao Peishen 指定 |
| 6 | 06 清单剩余 6 条 🟢 | 工具链四条已落地（`f4f2c8f`）。剩下的：3.3 企微 webhook（无挂载点）、7.1/7.4 合规条款（应进 audit 包的 spec）、9.1/9.2/9.3 沟通线 |
| 7 | prunable worktree | ⚠️ **清完又长出来一个**：`session-naming-lane-dispatch-54cff7`（停在 `7e088cb`，detached）。<br>⇒ 这不是"忘了清"，是**每跑一轮就产生一个**。修个例没用，该在 `run-build` 收口段或 `run-lanes.sh` 尾部加一次 `git worktree prune` |
| 8 | ~~🧪 Cowork session 命名实验~~ | **✅ 已关闭 08-27：走摘要，首行无效。** 首行 `HR业务线-接力0827B` → 侧边栏 `HR业务线接力`。⛔ 不要再改首行格式硬试。已落三处真源，详见本文顶部 |
| 9 | ~~opener 规则回填缺口~~ | **✅ 已关闭 08-27**：`run-lanes.sh` 已加开跑前自检——块内误补 `set_session_title` → `exit 13` 拒跑；块外缺豁免注明 → WARN 不拒。反例已验（见下） |
| 10 | 🧪 `claude -p -n` 是否真给 session 起名 | **未实测**。`run_lane()` 一直在传 `-n "[Mac]<id>-<题>"`，但那条注释原来引的"实证"已被推翻（带号的全是手工补的）。留着无害，⛔ 但不要写成"脚本这条路能保住编号"。5 分钟可验 |

---

## 四、本轮新增的环境事实

- **GitHub Actions**：本仓库是 private，Free 计划 2000 分钟/月，**Windows runner 按 2x 扣**。
  08-19 撞过一次额度。CI 里 1 个 `windows-latest` + 2 个 `ubuntu-latest`。
  若再撞，折中方案是「feature 分支只跑 Linux，PR 与 main 推送才跑 Windows」。
- **日志路径耦合**：`log_file` 是相对路径 `logs\app.log`，靠计划任务的
  `WorkingDirectory=C:\apps\zhuopin-recruit-agent` 解析——**改工作目录会把日志静默挪走**。
- **脱敏层尚未被真实数据检验**：发版验证显示日志里 0 个标记词，但 `<redacted>` 也是 0 命中，
  说明脱敏根本没被触发（`loggable_summary()` 至今无生产调用点）。
  这条验证成立的是「没有泄漏」，**不是「脱敏被证明有效」**。详见
  `docs/findings/2026-08-13-sqlite-事务归属冲突.md` §8.3.1。

**—— 以下为 2026-08-27 本轮新增 ——**

- 🔴 **CC 的 session 名只有一条路：显式调 `mcp__ccd_session_mgmt__set_session_title`**
  （`session_id` 传字面量 `"self"`）。标题行**不会**自动变成 session 名，自动带上编号的
  概率是**零**——此前侧边栏里那些带号的全是 Shao Peishen 手工补的。已上 hook 判据②机器守。
  **已在跑的 session 可补救**，对它说同一句话即可，不必重开。
- 🔴 **三条关于 session 名的假说已被实测推翻，别再提出来**：「首句会被沿用」「长度超限吃掉
  编号」（32 字符实测完整保留）「摘要模型碰巧保留编号」。**判据：一个假说写成规则之前，
  先问有没有一个 5 分钟就能做的实验能推翻它。**
- 🔴 **`git pull --rebase` 不能放在 commit 前**：并发下别的泳道有未提交改动是常态，
  实测直接 `error: cannot pull with rebase: You have unstaged changes`。
  正确顺序＝`add 明确路径 → commit → push`，**push 被拒才** `pull --rebase --autostash`。
- **`--max-budget-usd` 在 Max 订阅下不是钱闸**：那个美元数是 Claude Code 按标准价目**本地
  折算**的估值，用量已包含在订阅里，提高它不产生额外收费（查证 code.claude.com/docs/en/costs）。
  真花钱的只有 usage credits（claude.ai → Settings → Usage 单独开的开关）。
  **真正的天花板是 5 小时滚动 + 每周用量窗口**——并行两条 run-build 用量翻倍，
  开跑前看 `/usage` 比看美元数有意义。已把默认从 8 提到 25。
- **Cowork 的 bash 会在 `.git/` 留下删不掉的 `index.lock`**（VM 对 `.git/` 只能写不能删）。
  跑过 `git status` 之后 CC 那边提交前要先 `rm -f .git/index.lock`。
  `run-lanes.sh` 已加开跑前锁预检（`exit 14`）——孤儿锁不会自己消失，带锁开跑会让**所有泳道全灭**
  （实证 `lanes-20260826-231550` 五条全败于同一把锁）。

---

## 五、绕不开的约束（每次都要记得）

- **给 Paul 的每条指令，代码块头两行固定**：`[Mac]MMDDX-<主题短名>` + 【设置】单行（五项用 ｜ 分隔）。
  **CC 的 opener 第 3 行必须调 `set_session_title`**。`MMDD` 要实跑 `TZ=Asia/Shanghai date +%m%d` 取
  ——本机在 EDT，比中国晚 12 小时，照本机日期编会集体差一天且不报错。判据表见 `CLAUDE.md`。
- **Opener 必须是完整可整块复制的**，不要让他拼接片段。
- **一次给 ≥2 个 opener 时，必须说明次序与能否并行**（判据＝触碰区是否重叠）。
- **「企业AI转型」已于 2026-08-26 迁出 OneDrive**，本地路径已作废。
  唯一入口＝GitHub 公开仓库 `Raytheoner/zhuopin-ai-transformation`，**分支 master**，
  用 WebFetch 读 `raw.githubusercontent.com/.../master/<路径>`（中文路径要 percent-encode）。
  没有本地副本 → **grep 不了，引用必须给文件级 URL**。
- **git 相关只能在 CC**——Cowork 的 bash 在隔离 VM 里，对 `.git/` 只能写不能删。
- **跨界面看不到对方会话**：`list_sessions` 只列 Cowork 会话，CC tab 的永远看不到。
  查 CC 干了什么走文件系统（git log / `.claude/handoff/*.log`）。

---

## 六、已固化进 skill 的判据（此处只留指针）

1. **计划里的任务标题必须是三级 `### Task N:`**——二级会让 `scripts/task-brief`
   静默返回空。已写进 `.claude/skills/spec-to-plan/SKILL.md` 自查清单。
2. **合并后要单独验一次 `git rev-list --count main..<分支>` 是否为 0**——
   `finishing-a-development-branch` 被跳过时毫无症状，唯一表现是 main 上什么都没有。
   已写进 `.claude/skills/run-build/SKILL.md` 收口段。
3. **`git rev-list --count main..<分支>` 非 0 时先别下结论**——main 被 rebase 过时它会假阳性。
   再跑 `git cherry -v main <分支>`：全 `-` 是内容已在 main（删掉陈旧分支即可），有 `+` 才是真没合。
   实证：单元 C 与 `worktree-delivery-unit-e-run-build` 都撞过。已写进 `run-build/SKILL.md`。
4. **说「开始泳道看护」即触发 `lane-dispatch` skill**——扫待办 → 判触碰区分泳道 → 写 opener
   进编排 → dry-run 核对 → 出看护 opener 与发车命令。其中「判触碰区」是最容易错也最值钱的一步。
