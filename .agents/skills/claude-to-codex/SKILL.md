---
name: claude-to-codex
description: 把「Claude 引擎的项目构建 workflow」整体迁移到 Codex 引擎（双引擎开关、无头执行器、看护/调度器、技能与提交通道），产出迁移计划＋验收判据＋已实证差异表，供同类项目（含 Win 端）复用。当用户说「Claude 切 Codex」「引擎切换」「迁到 Codex」「引擎切换参考」时使用。⛔ 不用于只改一条命令或在 Codex 里偶尔跑一次脚本的场景。
---

把一个跑在 Claude 上的**项目级构建 workflow**整体切到 Codex（或反向）。目标不是"能跑一次"，
而是**无人值守链闭环＋一条开关可回退**。2026-09-29/30 在本仓库（HR 招聘智能体）实跑过一遍，
以下是该次沉淀；⛔ 不重走已证伪的弯路。

## 0. 先决三问（答不出就别开工）

1. **执行面在哪**：哪些脚本硬编码了旧 CLI？本项目三处——`docs/openers/run-lanes.sh`、
   `scripts/dispatcher_event.sh`、`tools/liaison/unpack/dispatch.py`。
   用 `rg -n "claude -p|claude --print|CLAUDECODE"` 先找全集。
2. **往返通道在哪**：发车（launchd/计划任务/请求文件）、收敛事件、提交、看护（心跳自动化）
   各是谁在跑？迁移必须**通道不变、引擎可换**。
3. **判据会不会失真**：哪些"跑通"判据在新引擎下会假阳/假阴？本项目实证：
   `pgrep -f` 空≠没在跑；挂载侧执行 venv 报错≠环境坏；`--help` 列出的旗标≠可用。

## 1. 七步迁移法（每步带判据）

1. **盘点**：执行器、调度器、服务、钩子、技能、提交白名单六个面各列出调用点，
   每个调用点给"换/不换"结论。判据＝清单可逐条核对。
2. **双引擎开关**：环境变量（本项目 `HR_AGENT_ENGINE`：默认 codex、claude 回退），
   ⛔ 不删旧路径、⛔ 不整体重写。判据＝只改环境变量即可整体回退。
3. **最小调用探针**：`codex exec --json --sandbox workspace-write -c approval_policy=never
   -c forced_login_method=chatgpt -m <模型> - < prompt`。先 `--help` **再实跑一次**：
   本机 0.158.0-alpha 的 `-a/--ask-for-approval` help 里有、实跑被拒 ⇒ 审批一律走
   `-c approval_policy=…`。判据＝探针产出 JSONL＋最终文本。
4. **登录态对齐**：`~/.codex/config.toml` 的 `forced_login_method` 与 `auth.json` 的
   `auth_mode` 必须同侧；不同就在调用处覆盖 `-c forced_login_method=chatgpt`（不写会启动即报
   "API key login is required, but ChatGPT is currently being used"）。判据＝无头调用无认证错。
5. **输出收敛器**：把新引擎 stdout 收敛成与旧引擎**同列序**的 TSV（cost/in/out/cache/turns/peak），
   下游零改动；⛔ 不编造没有的字段（codex 无美元计费 ⇒ cost 记 `-`）；peak 口径注明"近似、
   不当硬闸"（codex `turn.completed` 是整场累计，与 claude `usage.iterations` 口径不同）。
   判据＝老报表的消费方一行都不改。本项目实现：`scripts/codex_jsonl_summary.py`。
6. **判活与沙箱重推**：逐条实测"怎么判在跑、能写哪里"：
   - macOS Seatbelt 下 `pgrep -f` 恒空 ⇒ **PID 文件＋`kill -0`**；
   - `workspace-write` 把写权限锁在进程 cwd ⇒ 泳道 worktree 的 git 元数据在主仓 `.git` 下，
     泳道内 `git add/commit/merge/push` 全被拒 ⇒ **提交/合并挪到执行器收口阶段**。
     本项目实现 `scripts/lane_collect.py`：stage1 代提交到泳道分支并记账；stage2 只把
     「OK＋机器判据 PASS」的合回 main 并推送，PARTIAL/GATE-* 只留分支点名，冲突 abort 不硬解。
   判据＝在沙箱内跑一次"被判活/被提交"，结论与真身一致。
7. **调度器 PATH 与退出码**：launchd/计划任务下 PATH 最小，CLI 解析写死顺序
   `HR_*_BIN → PATH → ~/.local/bin → 应用内置路径`；找不到 ⇒ 明确退出码（本项目 10），
   ⛔ 不静默。判据＝冷启动一次服务能自解析到 CLI（本项目修前是"一秒退出"）。

## 2. 落档与技能（两边怎么放）

- 新引擎真源放 `<新引擎>/skills/`（Codex＝`.agents/skills/<name>/SKILL.md`）；旧侧保留作回退，
  ⛔ 两处措辞互改；机器闸仍核旧路径时尤其注意。
- 规则变更**当次落档**（CLAUDE.md/AGENTS.md/技能正本），⛔ 只在会话里说好。
- 提交通道白名单同步扩到新路径（本项目 `.agents/skills/*/SKILL.md` 与旧路径同待遇）。

## 3. 验收清单（全过才算迁移完成）

- [ ] dry-run 打印「执行引擎：codex」＋每条模型档映射来源
- [ ] 真实无头冒烟 1 条（能写文件＋顶格输出 `OPENER_DONE`）
- [ ] 机制类泳道 3 条实车（互不触碰）＋收敛事件→调度器→看护通知闭环
- [ ] 无人值守下提交/合并/推送可达；失败回退命令一条可用
- [ ] 全量测试＋合规断言过（本项目 3769+74）
- [ ] 已知缺口表登记（见 §5，⛔ 不假装解决）
- [ ] 回退演练：环境变量切回旧引擎，跑一条最小任务

## 4. 已实证差异表（2026-09-29/30，本仓库）

| 项 | Claude 侧 | Codex 侧（实测） |
|---|---|---|
| 无头调用 | `claude -p --output-format json` | `codex exec --json --sandbox workspace-write -c approval_policy=…` |
| 审批旗标 | `--dangerously-skip-permissions` | ⛔ 无 `-a`；用 `-c approval_policy=never` |
| 登录态 | 继承 claude 登录 | `-c forced_login_method=chatgpt` 对齐 auth.json |
| 模型分级 | Opus/Sonnet/Haiku | 映射 deepseek-v4-pro(max)/v4-pro(high)/flash(low)，词汇不变 |
| 进程判活 | `pgrep -f` 可用 | Seatbelt 下恒空 ⇒ PID 文件＋kill -0 |
| 沙箱写权 | hook 守主工作区 | workspace-write 锁 cwd；worktree git 只读 ⇒ 执行器代收口 |
| 上下文续棒 | hook 注入 150k 转场哨兵 | ⛔ 无等价 hook（只记账＋提示，缺口在册） |
| 技能真源 | `.claude/skills/` | `.agents/skills/`（旧侧保留） |
| 计划内插件 | superpowers 可用 | ⛔ 不可用 ⇒ 自建 `scripts/codex_sdd_runner.py` |
| hooks | settings.json＋hook 脚本 | 项目层 `.codex/hooks.json`；无头需 `--dangerously-bypass-hook-trust` |

## 5. 已知缺口（照抄，⛔ 不当已解决）

1. Codex 无 150k 上下文自动续棒等价物——越线只记账/提示，长任务需人为拆分或把缺口修进执行器。
2. 泳道内不能跑 git（沙箱未改，属设计）——一切提交经执行器收口；PARTIAL 产物留分支待人工判断。
3. 交互会话沙箱禁网，真实无头泳道要靠**用户态调度器**发起（launchd/计划任务）。
4. 项目级 hooks 的信任流程在无头下走 `--dangerously-bypass-hook-trust`（源须已自审）。
5. `turn.completed` 的 peak 是整场累计口径，⛔ 不要拿它当单轮上下文峰值做硬闸。

## 6. Win 端适配（复制到 Win 项目时逐条实测，⛔ 不套 macOS 结论）

| # | 项 | 判据（怎样算过） |
|---|---|---|
| 1 | codex CLI 安装与登录态（`%USERPROFILE%\.codex\auth.json` 与 config 同名键） | 无头最小探针返回 JSONL、无认证错 |
| 2 | 调度器＝任务计划程序（非 launchd）：账户、触发器、失败重启 | 冷启动一次请求文件→进程真起（有 PID/日志） |
| 3 | PID 判活：PID 文件＋`Get-Process`/`tasklist`（不依赖 `pgrep -f`） | 杀掉进程后判据翻为"不在跑" |
| 4 | 引号与路径：PowerShell vs Git Bash 各自调用形态（`$(...)` 不通用） | 同一 opener 在目标 shell 逐字可跑 |
| 5 | 沙箱/审批：Windows 下与 Seatbelt 行为不同，逐条实测"能写哪、能不能 push" | 写出写权矩阵实测表（与 §4 对齐） |
| 6 | 行尾：先钉 `.gitattributes`（LF），⛔ 别让 CRLF 打穿 grep 锚点 | 体积闸/机器判据在 Win 检出上同样过 |
| 7 | venv/python：`venv\Scripts\python.exe` 或 `py -3`，脚本里不写死 macOS 路径 | 机器判据块在 Win 上原样跑过 |
| 8 | 代理/egress：CLI 推理与 webhook 出网白名单 | 真实无头泳道跑通一条最小任务 |

## 7. ⛔ 不要做

- 整体重写一次性替换（必须留一条开关回退）；把 `--help` 当契约（先实跑）。
- 把旧引擎的 hook/判据缺口当"应该也适用"；把 macOS 的判据（pgrep/venv/curl）套到 Win。
- 为了让测试过而调高体积上限、删规则或跳过机器判据。
- 在泳道里改执行器脚本本身（本项目实证：脚本运行中被自己改写会把函数名切成两半、条目跑两遍）。
