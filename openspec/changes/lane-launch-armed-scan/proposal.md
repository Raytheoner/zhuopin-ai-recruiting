## Why

泳道发车至今没有一条能无人值守走通的路。看护者（CC Desktop session）无论用 Bash `nohup`、`run_in_background`、Write 工具还是 Bash 重定向，只要动作的效果是"起一批会自主 commit/push 的无头 session"，就会被 Auto Mode 分类器拦下——2026-09-03 至 09-09 五次实证，`permissions.allow` 三条齐全也拦（`0904Z`）。09-08 `0909G` 设计的 launchd `WatchPaths` 路只搬走了"起进程"这一半，"写请求文件"那一半仍在看护者手里，2026-09-09 第十五批实测**写 `.request` 同样被拦**；同一行内容写到无关路径成功，证明拦的是 `.claude/handoff/launch/` 这个路径本身，不是内容。

结果是每一批泳道都要 Shao Peishen 本人在 CC Desktop 对话里点一次 Run，且必须**恰好在看护者就绪的那一刻**在场。这正是 CLAUDE.md 明令禁止的"伪装成机制的人工节奏控制"：发车时机被绑在人身上，人一转场整批就停在原地。

本变更把发车触发从"看护者写一个请求文件"换成"操作系统定时扫描仓库里**已提交的**编排文件"，并引入一枚只有 Shao Peishen 能签发的**武装令牌**，把他的授权动作从"发车那一刻的点击"前移成"批次编排完成时的一次签发"。他不再需要在场；看护者可以把一切准备好，但**签不了那枚令牌**。

## What Changes

- **新增武装令牌机制**：一个批次要发车，必须在仓库里存在一份该批次的武装令牌，且令牌须由 Shao Peishen 的 git 身份签发。看护者可以写编排、写 opener、跑 dry-run，但产出的令牌不被承认。
- **新增定时扫描器**：launchd 以固定间隔（而非 `WatchPaths`）唤醒扫描器，读取**已提交到 git 的**编排文件，取出既有 `> 泳道：` 待跑标注、又被有效令牌覆盖的批次，交给既有的 `run-lanes.sh` 执行。
- **发车不再依赖任何由 Claude 写入的文件**。请求文件协议（`.request` / `.claimed` / `.started` / `.rejected` / `.deferred` / `.consumed`）整套保留但降级为**退路**，不删除——09-03 与 09-04 的结论都还在，多一条退路不是冗余。
- **令牌一次一销**：令牌被消费后由扫描器就地作废，重跑同一批次须重新签发。防止一枚令牌在编排文件被后续追加内容后无限授权新泳道。
- **扫描器不接受任何来自仓库文件的自由参数**。`run-lanes.sh` 的参数由令牌里的受限字段决定，白名单沿用 `lane-launcher.sh` 现有的六项，并**继续排除** `--chain` / `--model` / `--dry-run`。
- **BREAKING**（对现有运维习惯而非代码）：装了本机制之后，「在编排文件里写下 `> 泳道：`」不再等于「这批会跑」。要跑必须另有令牌。这是刻意的，防止写文档变成发车。

## 不做什么（Non-goals）

- ⛔ **不取消人工授权闸门**。本变更把闸门前移、让它可提前、可远程、可批量，但**不移除**它。让"AI 写一个文件就能起一批自主 commit/push 的 session"是本变更明确拒绝的形态。
- ⛔ **不改 `run-lanes.sh` 的执行语义**。泳道划分、错峰、哨兵双指标、条目总数自检、自拷贝执行全部原样不动。本变更只换"谁在什么条件下调用它"。
- ⛔ **不删除现有 `.request` 请求文件路**，也不改 `lane-launcher.sh` 的白名单与认领协议。
- ⛔ **不触碰 `.51` 生产服务器**，不改任何部署链路。本机制只在 Shao Peishen 的 Mac 上运行。
- ⛔ **不让扫描器执行 git 写操作**（`pull` / `commit` / `push` 一概不做）。它只读工作区与 git 元数据；同步由人或看护者按既有规则完成。
- ⛔ **不做 Web/UI 控制台**，不引入任何常驻服务或端口。
- ⛔ 不解决"分类器该不该拦"这个问题本身。本变更承认该拦，只是把**授权决定**从 AI 手里挪回人手里，而不是把它绕过去。

## Capabilities

### New Capabilities

- `lane-launch-authorization`: 泳道批次的授权与发车触发——武装令牌的签发者判据、有效期与作用域、一次一销语义、定时扫描器的选取与拒绝规则、并发与幂等保证。

### Modified Capabilities

（无。现有四份 spec `ai-decision-audit` / `effect-transaction-integrity` / `outbound-approval-gate` / `runtime-observability` 描述的是招聘业务系统的行为，本变更只动本地开发编排工具链，不改变其中任何一条要求。）

## Impact

**新增**
- 扫描器脚本（`docs/openers/` 下，与 `run-lanes.sh` / `lane-launcher.sh` 同级）
- 令牌签发与校验的小工具 + 其单测
- `scripts/install_lane_launcher.py` 增加定时任务的 plist 分支（`StartInterval`），与现有 `WatchPaths` 分支并存

**修改**
- `scripts/install_lane_launcher.py`：多装一个 LaunchAgent 或给现有 plist 增加时间触发；⛔ 不动已验证的 `AbandonProcessGroup` 与绝对 `PATH`（`496f55e` 刚修）
- `.claude/skills/lane-dispatch/SKILL.md`：发车段落改写，三条历史实证全部保留、追加第四条
- `CLAUDE.md`：若发车口径变化影响给 Shao Peishen 的指令格式，当次落档（约定变更当次落档铁律）

**不修改**
- `docs/openers/run-lanes.sh`（零改动，本变更的硬判据之一）
- `docs/openers/lane-launcher.sh`（零改动，退路原样保留）
- `app/`、`tools/liaison/`、任何业务代码与数据库

**依赖**：无新增第三方依赖。扫描器用 bash + 既有 Python 标准库，与现有两个脚本同口径钉死 `LC_ALL=C`。

**运维**：装机仍需 Shao Peishen 在 Terminal 手工跑一次安装脚本（起/换 LaunchAgent 属安全配置，Claude 不代做）。

## 合规影响

本变更**不处理任何个人信息**：作用域是本地开发编排工具链，触碰的文件是编排文档、令牌文件与 launchd 配置，不读取、不传输、不存储候选人简历或任何 PII，不产生 AI 评分，不涉及对外发送。

需要点明的一条关联：本机制发出去的泳道 session 具备 commit/push 能力，而 CLAUDE.md 的不可代项（合规红线变更、候选人对外通道、真实简历处理范围、`.51` 发版、预算采购）依赖"人在环"。因此武装令牌的签发者判据是本变更的**合规相关设计点**——它是"哪些自动化动作获得了谁的授权"这条链的锚点，spec 中必须给出可机器判定的判据，不能只写"由本人签发"。
