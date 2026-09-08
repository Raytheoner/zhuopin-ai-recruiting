> **进度**：12/66（第 1 章「通道可行性与服务骨架」、第 3 章「准入名单」已完成并合回 main，2026-09-08）
>
> **粒度约定**（CLAUDE.md「粒度映射」）：本文件的**一个 `##` 章节 = 一个 superpowers plan = 一条 worktree 分支 = 一个可独立测试并合并的交付单元**。章节的 checkbox 在该 plan 的 final review 通过后才勾。
>
> **落点约定**（design.md D10）：全部代码落 `tools/liaison/`，依赖落 `tools/liaison/requirements.txt`。⛔ 不落 `app/`、⛔ 不落 `scripts/`（二者都在 `sync-to-server.sh` 的 `SYNC_PATHS` 里，会被推到 `.51`）、⛔ 不进根 `requirements.txt`。允许 `tools/liaison` 单向 `import app.storage.idempotency`，⛔ `app/` 不得反向 import。
>
> **幂等约定**（design.md D3）：有副作用的动作一律独占一个 `effect_*` 函数并带幂等键 `{thread_id}:{node_name}:{business_key}`，`thread_id` = 会话标识（私聊取 userid／群聊取 chatid），`business_key` = 企微 `msgid`。计算类一律 `compute_*` 纯函数。

## 1. 通道可行性与服务骨架

对应能力：`liaison-channel-session`（凭据 fail-closed 启动校验部分）。**本章是全部后续章节的前置**：SDK 在 Python 3.14 上是否可用未经验证，未验证前不写业务代码（design.md D8）。

- [x] 1.1 建 `tools/liaison/` 目录骨架与 `tools/liaison/requirements.txt`（只含本服务依赖），加 `tools/liaison/README.md` 写明"开发期值守工具、永不部署 .51、不是产品功能"三条边界
- [x] 1.2 **SDK 兼容性实测（阻塞项）**：在 Python 3.14 环境安装 `wecom-aibot-python-sdk`，记录可安装性与 import 结果；结论写入 `docs/findings/2026-09-08-aibot-sdk-py314-兼容性.md`
- [x] 1.3 按 1.2 结论二选一并在 findings 文档里写明选了哪条：① SDK 可用 → 钉死具体版本号写进 `tools/liaison/requirements.txt`；② SDK 不可用 → 按 design.md D8 退路建最小 WS 客户端的模块骨架（只覆盖本服务用到的消息类型，不做通用 SDK）
- [x] 1.4 实现启动期凭据校验：`HR_LIAISON_BOT_ID` / `HR_LIAISON_BOT_SECRET` 缺失、空串、纯空白一律拒绝启动并指明缺失项，进程不驻留
- [x] 1.5 `.env.example` 追加 `HR_LIAISON_*` 占位（只写变量名与注释，⛔ 不写任何真实值）；加测试断言受版本管理的文件中不含真实密钥形态的取值
- [x] 1.6 单测覆盖 1.4／1.5：三种缺失形态各一条、版本管理无凭据一条

**验收**：`liaison-channel-session` 中「凭据缺失时拒绝启动」一条要求的全部场景通过；SDK 路线已定且有 findings 落档。

> **第 1 章落地偏离登记**（2026-09-08，run-build 收口时记）：
>
> - **1.3 选了路线 ①**：`wecom-aibot-python-sdk` 在 Python 3.14.6 上实测装得上（发行版本
>   `1.0.2`）、`import aibot` 成功、`WSClient` / `WSClientOptions` 的建连＋心跳＋重连三样参数齐备，
>   判据 A/B/C 全过。已在 `tools/liaison/requirements.txt` 钉死 `==1.0.2`。证据见
>   `docs/findings/2026-09-08-aibot-sdk-py314-兼容性.md`。**路线 ② 的最小 WS 客户端骨架未建**
>   （分支未命中，⛔ 不是漏做）。
> - ⚠️ **发行名 ≠ import 名**：PyPI 是 `wecom-aibot-python-sdk`，顶层模块是 `aibot`。
>   且模块 `__version__` 报 `1.0.0` 与发行版本 `1.0.2` 对不上——**一律以
>   `importlib.metadata.version()` 为准**，任何拿 `__version__` 校验装对没有的写法都会误判。
> - 🔴 **第 7 章 7.6 的接线约束**：`WSClientOptions.max_reconnect_attempts` 默认值是 **10**，
>   与 spec「断线后自动恢复接收」要求的"重试直至成功、服务不退出"冲突。第 7 章必须显式传
>   `-1`（SDK 里 `-1` = 无限重连），⛔ 不许用默认值。
> - **超出原 1.5 范围的一处加固**：凭据扫描正则原本锚在行首不含缩进，终审实测发现**缩进的**
>   真实赋值可以整条逃过扫描（嵌套代码块／YAML 里粘一条即中）。已放行前导空白并补回归用例。
> - ⏸ **留步：真实建连未验**。需 Shao Peishen 在企业微信管理后台注册**新** aibot 应用取得
>   `BotID` / `Secret`（账号级操作，无法代劳）。本章只验 SDK 能力面是否齐备，真实建连归第 7 章。
> - **`tools/liaison/tests/` 已接进根 `pyproject.toml` 的 `testpaths`**，全量 `pytest` 一次跑到。
>   与 `proposal.md`「Impact · 不触碰 `pyproject.toml`」的口径冲突，已登记 TD-14，归档前订正。

## 2. 存储基座与幂等不变式

对应能力：跨全部能力的存储前提（design.md D3／D5）。

- [ ] 2.1 建 `data/liaison.db` 的 schema 初始化：`effect_log` 表与 `app/storage/db.py:59-67` **同构**（同列、同主键、同唯一索引），`CREATE TABLE IF NOT EXISTS` 幂等建表
- [ ] 2.2 建 `liaison_message`（消息台账）与 `liaison_task`（队列真身）两张表；`liaison_task.send_status` 用 `CHECK` 约束钉死三态枚举值（存枚举，⛔ 不存 emoji）
- [ ] 2.3 接入 `app.storage.idempotency.idempotent_effect`（单向 import），确认装饰器在本服务的单连接模型下工作；写一条测试断言该连接上不存在第二个事务管理者
- [ ] 2.4 写「恒等不变式」测试脚手架：给定任意一批消息，每个 `effect_*` 的 `effect_log` 条数与其业务表行数按 `thread_id` 恒等。⚠️ 幂等策略：本章不产生对外副作用，只建表与验证机制
- [ ] 2.5 单测：业务写抛异常时 `effect_log` 不留记录、重跑会重新尝试（对应 `liaison-task-queue`「业务写失败时不留下幂等记录」场景）
- [ ] 2.6 加断言测试：`app/` 下无任何模块 import `tools/`（结构性单向约束，design.md D5）

**验收**：`liaison-task-queue` 中「入队幂等且与幂等记录原子提交」一条要求的两个场景可在脚手架上跑通（队列写入逻辑本身在第 5 章）。

## 3. 准入名单

对应能力：`liaison-inbound-whitelist`。

- [x] 3.1 定名单配置文件格式（`tools/liaison/config/whitelist.yaml`）：字段只含企微 userid、中文姓名、角色说明；⛔ 不含手机号／邮箱／身份证号
- [x] 3.2 写入 design.md D2 的结论名单：汤丽萍、邵培申两人。⛔ 聂鑫／王寒月／陈承不入（理由已在 D2 表格，⛔ 不在实现阶段重开此结论）
- [x] 3.3 实现 `compute_admission`：纯函数，输入发送人标识，输出是否命中。文件缺失／不可读／解析失败／名单为空一律折成空名单全拒；⛔ 不沿用任何此前加载过的名单
- [x] 3.4 判定路径不抛异常给调用方：任何失败结果都是"未命中"，并记录 ERROR 级日志
- [x] 3.5 单测覆盖四种 fail-closed 形态（不存在／格式坏／空名单／权限不可读）+ 命中一条 + 名单配置字段受限一条
- [x] 3.6 单测：追加一名成员并重载后该成员命中，且未修改任何 `.py` 文件（对应「名单变更不需要改动代码」场景）

**验收**：`liaison-inbound-whitelist` 除「名单内／名单外消息的归档与入队行为」两条（依赖第 4／5 章）外全部场景通过。

## 4. 消息归档

对应能力：`liaison-message-archive`。

- [ ] 4.1 实现 `compute_archive_path`：纯函数，输出 `data/liaison/archive/<thread_id>/<yyyymmdd>/<msgid>__<归一化文件名>`。日期只做分目录，`msgid` 是最细一级键（⛔ 禁止按天／按人粗粒度落点）
- [ ] 4.2 实现 `compute_safe_filename`：只做路径安全（去路径分隔符与控制字符、去首尾空白、超长截断保留扩展名）；⛔ 不因非 ASCII 或"看着像乱码"改写或拒收，中文名原样保留
- [ ] 4.3 实现附件落盘：`rb`/`wb` 字节流 + 写临时文件 → `fsync` → 原子 `rename` 到最终路径。⚠️ 幂等策略：目标路径含 `msgid`，已存在即视为已完成（rename 幂等）
- [ ] 4.4 实现完整性校验：只用字节长度 + SHA-256。⛔ 禁止任何 UTF-8／文本解码校验，⛔ 不因内容不可解码判定损坏
- [ ] 4.5 实现 `effect_archive_message`：**先落材料、后写台账**，台账行与 `effect_log` 行在同一事务提交。⚠️ 幂等策略：幂等键 `{thread_id}:effect_archive_message:{msgid}`；中间态只允许"材料已在、台账未记"，⛔ 不允许"台账已记、材料缺失"
- [ ] 4.6 单测：同人同天 3 个同名附件各自完整、同名不同内容两份可分别取回（对应生产 bug「归档覆盖」）
- [ ] 4.7 单测：真实 xlsx／pdf 字节样本进出逐字节相同、校验不走解码路径（对应生产 bug「二进制判误」）
- [ ] 4.8 单测：同一 `msgid` 投递两次只有一份归档与一条台账；模拟落盘后被强制终止再重跑，台账与材料一致
- [ ] 4.9 单测：文件名含路径分隔符不逃出目录、超长截断保留扩展名、中文名保留
- [ ] 4.10 实现名单外分支：只归档 + 礼貌回复，⛔ 不生成队列条目（队列条目生成在第 5 章，此处只接线并加断言"队列条目数不变"）

**验收**：`liaison-message-archive` 除「留存期」一条（第 8 章）外全部场景通过；`liaison-inbound-whitelist`「名单外消息只归档并礼貌回复」全部场景通过。

## 5. 值守任务队列

对应能力：`liaison-task-queue`。

- [ ] 5.1 实现 `effect_enqueue_task`：写一条 `liaison_task` 行，携带发送人标识、会话标识、来源 `msgid`、接收时间。⚠️ 幂等策略：幂等键 `{thread_id}:effect_enqueue_task:{msgid}`，队列行与 `effect_log` 行同一事务提交
- [ ] 5.2 存储层拒绝缺少来源 `msgid` 的队列行（`NOT NULL` + 单测）
- [ ] 5.3 实现状态转移校验：三态枚举 + 「暂缓」只能来自「待发」+ 「已推送」必带推送时间戳；非法取值与非法转移一律拒绝
- [ ] 5.4 实现 `render_queue_markdown`：从 `liaison_task` 渲染只读 Markdown 视图，内容中的竖线／换行做转义。⛔ 单向：不从该文件读任何状态、不回写
- [ ] 5.5 实现"从队列条目回指材料"的查询路径（条目 → 来源消息 → 全部附件）
- [ ] 5.6 单测：内容含竖线／换行／控制字符／超长文本时字段不错位、不产生额外条目（对应生产 bug「队列越界写入」）
- [ ] 5.7 单测：短时间内多条消息并发入队各自成行、无覆盖丢失（对应生产 bug「队列追加并发覆盖」）
- [ ] 5.8 单测：手改导出 Markdown 不影响真身、下次渲染覆盖手改内容
- [ ] 5.9 单测：非法状态取值被拒、非法转移被拒、已推送带时间戳
- [ ] 5.10 单测：跑第 2 章的恒等不变式脚手架，确认队列条目数与幂等记录数按会话恒等

**验收**：`liaison-task-queue` 全部场景通过；`liaison-inbound-whitelist`「名单内成员的消息进入归档与队列」全部场景通过。

## 6. 群通知外发

对应能力：`liaison-group-notify`。

- [ ] 6.1 实现 webhook 发送封装：标准库 HTTP 客户端 + JSON + `errcode` 检查 + 超时；真实地址只从环境变量读，受版本管理的配置只记变量名
- [ ] 6.2 实现 `compute_length_guard`：按 UTF-8 **字节数**判定超限；⛔ 两条通道阈值独立配置（aibot 通道 20480 字节；群 webhook markdown 约 4096 字符对应的字节阈值），⛔ 不共用同一常量
- [ ] 6.3 实现超限降级：降级为"提要 + 附件"；提要仍超限或该通道无附件承载方式 → 拒发 + 告警，⛔ 不静默截断
- [ ] 6.4 实现令牌桶主动节流（容量 20、按 20/60s 补充），发送前生效
- [ ] 6.5 实现限流退避重试：收到 `errcode 45009` 按 1s→2s→4s→8s 重试，最多 4 次
- [ ] 6.6 实现重试耗尽的待重发持久化 + 告警。⚠️ 幂等策略：`effect_send_group_notify` 幂等键 `{thread_id}:effect_send_group_notify:{通知内容摘要}`，重试不产生重复通知
- [ ] 6.7 结构性约束：收件对象只能是内部值守群或名单内成员；⛔ 不提供以候选人标识为收件对象的参数或调用路径。加断言测试守护（对应 `liaison-group-notify`「无候选人外发入口」场景）
- [ ] 6.8 单测：字符数不超但字节数超 → 判超限；两通道阈值独立生效；非限流错误不被当作成功
- [ ] 6.9 单测：降级成功一条、无法降级拒发一条、不静默截断一条；限流后重试成功不产生重复通知一条、重试耗尽落待重发 + 告警一条
- [ ] 6.10 单测：环境变量未配置时拒发并告知缺失项，⛔ 不静默跳过后报告成功

**验收**：`liaison-group-notify` 全部场景通过；全部测试用 mock 端点，⛔ 不做任何真实发送。

## 7. 连接生命周期与中断告警

对应能力：`liaison-channel-session`（除第 1 章已完成的凭据校验外）。

- [ ] 7.1 实现存活戳：连接健康但空闲时持续更新；⛔ 不把"一段时间无消息"判为断线（参考服务曾混淆此二者）
- [ ] 7.2 实现中断窗口记录：起始时间 + 恢复时间，恢复后闭合
- [ ] 7.3 实现未闭合窗口的启动期补记：上次未闭合窗口按"起始已知、恢复时间 = 本次启动时间"补记，⛔ 不丢弃。⚠️ 幂等策略：按窗口起始时间去重，重复启动不重复补记
- [ ] 7.4 实现中断告警：内容必含窗口起止时间与"该时段消息可能未收到、请重发"的明确表述；⛔ 措辞中不得声称该缺口已被幂等机制覆盖
- [ ] 7.5 告警通道失败时只记本地日志，⛔ 不中止消息接收
- [ ] 7.6 接线自动重连（SDK 内置机制或 D8 退路的自建实现），确认重试带退避、⛔ 无紧密循环
- [ ] 7.7 单测：空闲两小时不告警、真断线记窗口、进程被杀后重启补记窗口
- [ ] 7.8 单测：告警文本含起止时间与重发请求；告警发送失败时接收不中止
- [ ] 7.9 单测：重连间隔随失败次数增长、服务不退出

**验收**：`liaison-channel-session` 全部场景通过。

## 8. 留存清理、进程守护与灰度验收

对应能力：`liaison-message-archive`（留存期一条）+ 整体可运行性。

- [ ] 8.1 实现留存期清理：`HR_LIAISON_RETENTION_DAYS` 默认 180，超期归档与消息台账清理；队列行不参与自动清理。⚠️ 幂等策略：按年龄判定，重复执行安全
- [ ] 8.2 清理失败时告警，⛔ 不静默跳过；单测覆盖超期被清理一条、清理失败告警一条
- [ ] 8.3 写 launchd plist（`KeepAlive` + `ThrottleInterval`）与安装说明；⛔ 不移植 Windows 侧的三级退避重启脚本（design.md D12）
- [ ] 8.4 日志接入：轮转 + 有界容量 + 个人信息脱敏（借用 `runtime-observability` 的做法，不受其 HTTP 请求链路要求约束）
- [ ] 8.5 加结构性守护测试：`tools/` 不在 `sync-to-server.sh` 的 `SYNC_PATHS` 里、SDK 依赖不在根 `requirements.txt` 里（design.md D10 的两道门禁）
- [ ] 8.6 单机灰度：名单先只放邵培申自己，自测归档／入队／通报三条链路，核对恒等不变式
- [ ] 8.7 加入汤丽萍（配置加一行 + 重启），并告知她"照原样在群里发即可、不用改任何习惯"
- [ ] 8.8 一周观察窗口的观察项落档：漏消息告警是否误报、限流是否被触发、归档是否有重名冲突。⚠️ 观察结论写 `docs/findings/`，⛔ 不在观察期内改判据
- [ ] 8.9 全部章节勾完后**当场**跑 `openspec-archive-change`（CLAUDE.md「归档时限」：不得跨越一个工作 session）

**验收**：服务在本机常驻可用、三条链路端到端通、两道结构性门禁有测试守护、观察结论已落档。
