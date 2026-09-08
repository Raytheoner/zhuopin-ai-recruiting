# 连接生命周期与中断告警（hr-wecom-aibot-liaison 交付单元 7）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 HR 企微值守通道的"在线"这件事本身可信——连接健康就持续盖存活戳（哪怕两小时没人说话），连接真断了就把这段窗口的起止时间落库、闭合后显式告警"这段时间的消息可能没收到、请重发"，进程被杀导致窗口没闭合的，下次启动补记而不是丢掉。

**Architecture:**

```
tools/liaison/                          ← ⛔ 不在 sync-to-server.sh 的 SYNC_PATHS 里，结构上到不了 .51（design D10）
├── storage/schema.py                   ← 【本章只追加一张表】OUTAGE_WINDOW_SCHEMA + 拼进 SCHEMA
│                                          ⛔ 不改第 2 章的 liaison_message / liaison_task 两张表
├── session.py                          ← 本章主体：存活戳 + 中断窗口三个 effect + 会话状态机
│   ├── effect_write_liveness_stamp(path, *, state, now, since)   文件覆写，⛔ 无幂等键（理由见下 3）
│   ├── read_liveness_stamp(path) -> dict | None                  只读
│   ├── effect_open_outage_window   (@idempotent_effect)          唯一 INSERT
│   ├── effect_close_outage_window  (@idempotent_effect)          UPDATE，按起始时间去重
│   ├── effect_mark_window_alerted  (@idempotent_effect)          UPDATE，告警成功才落
│   └── class LiaisonSession        start / on_connected / on_disconnected / tick
├── alerts.py                           ← 告警：纯函数文本 + 接口 + 日志实现
│   ├── compute_outage_alert_text(started_at, recovered_at) -> str   纯函数
│   ├── class AlertSink(Protocol)                                    本章只定接口
│   ├── class LoggingAlertSink                                       本章唯一实现
│   └── effect_emit_outage_alert(sink, text) -> bool                 ⛔ 永不抛，失败只记日志
├── session_client.py                   ← SDK 接线（本章唯一碰 aibot 的文件）
│   ├── compute_backoff_delay(attempt) -> float                      纯函数，⛔ 永不返回 0
│   ├── run_forever(connect, *, sleep, monotonic, ...)               外层建连重试，⛔ 不返回
│   ├── build_ws_options(credentials) -> aibot.WSClientOptions       max_reconnect_attempts = -1
│   └── make_sdk_connect(options, *, on_connected, on_disconnected)  表面未验时显式报错
├── __main__.py                         ← 接线：凭据 → 建库 → session.start → run_forever
├── scripts/probe_ws_surface.py         ← 只读探针：把 WSClient 的真实方法/事件名落进 findings
└── tests/
    ├── test_session_liveness.py        7.1 / 7.7 空闲两小时
    ├── test_session_outage_windows.py  7.2 / 7.3 / 7.7 补记
    ├── test_alerts.py                  7.4 / 7.5 / 7.8
    ├── test_session_client.py          7.6 / 7.9 退避与不退出
    └── test_main_wiring.py             接线与留步的可执行形式
```

开工前必须先读懂的**六条判断**，改动前逐条对照：

**1. 🚨 本目录禁止写 `with open(...)` —— 这是本章最容易踩、且踩了会莫名其妙变红的坑。**
第 2 章的 `tests/test_liaison_effects.py::test_no_second_transaction_manager_in_source` 会扫 `tools/liaison/**/*.py`（测试目录除外），把**任何** `with <Name|Attribute|Call>:` 判为"隐式提交事务边界"违规——它认的是 `ast.With` 的形状，不看上下文管理器到底是不是数据库连接。于是 `with open(path, "w") as f:` 会**在一个跟文件读写毫无关系的测试里**报 `用 with open(...): 隐式提交`。
本章因此一律用**不带 `with` 的写法**：`pathlib.Path.write_text()` / `read_text()` + `os.replace()`。⛔ 同理不许用 `contextlib.suppress`（它也是 `with`）。
⛔ **不要为了写 `with` 去放宽那个扫描器或往白名单里加本章的文件。** 它守的是工程铁律 1 唯一的静态判据，为了一行文件写削弱它，代价与收益差着数量级。测试代码里可以照常用 `with`（扫描器跳过 `tests` 目录）。

**2. 中断窗口的三个动作全部走 `@idempotent_effect`，本章不自己 commit。**
`storage/db.py` 的模块 docstring 写死：除 `init_schema` 外，`tools/liaison/` 下非测试代码一律不许调 `conn.commit()`/`rollback()`/`executescript()`，提交由 `idempotent_effect` 独占。本章要写三种库变更（开窗、闭窗、标记已告警），任何一种"自己 INSERT 然后自己 commit"的写法都会当场违反上述静态判据。
把它们写成 `@idempotent_effect("effect_xxx")` 一举两得：提交归装饰器所有（判据满足），幂等键 `{thread_id}:{node_name}:{business_key}` 天然实现了 7.3 要的"按窗口起始时间去重"（`business_key` = 窗口起始时间字符串）。

**3. `EFFECT_NODE_TO_TABLE` 本章一个字都不加。⛔ 不碰 `storage/effects.py`。**
归档队列泳道正在改那个文件（本章 opener 约束 6）。已核对第 2 章两条守卫测试的实际判据，确认本章新增的 effect **不会**让它们变红：
- `test_effect_node_to_table_matches_reality` 比对的是 `vars(tools.liaison.storage.effects)` 里的 `effect_*` 函数集合——只看 `effects.py` 这一个模块，本章的 effect 定义在 `session.py`，不在它的取值范围内。
- `test_effect_node_to_table_matches_the_insert_target_repo_wide` 虽然全仓扫 `@idempotent_effect`，但 `_validate_node_to_table_mapping` 只遍历 **mapping 的键**；扫到了却没登记的 effect 不计入违规。
代价是：本章的三个 effect **不被 `assert_effect_log_identity` 覆盖**。这个缺口本章自己补——Task 1 写一条本地版恒等断言 `assert_outage_identity(conn)`（`effect_open_outage_window` 的 `effect_log` 条数按 `thread_id` == `liaison_outage_window` 行数），并在窗口相关的每条用例末尾调它。
⛔ **`effect_close_outage_window` 与 `effect_mark_window_alerted` 永远不许登记进 `EFFECT_NODE_TO_TABLE`**：它们是 UPDATE，不产生新行，登记进去会让"effect_log 条数 == 业务表行数"这条恒等式因为一个正当理由变红，而那时最顺手的修法是削弱恒等断言本身。⛔ 只有 INSERT 型 effect 才配登记。

**4. 存活戳落文件，不落库；且它**不带**幂等键——这是刻意的，不是漏了。**
spec 要求存活戳"可被外部读取"且"持续更新"。落库意味着每次心跳都要写一行/改一行，也就是每次心跳都要一次事务提交；而 `idempotent_effect` 的语义是"这件事做过就不再做"，套到心跳上会让存活戳**只写一次然后永远不再更新**——幂等键在这里不是保护，是把功能反过来关掉。
所以：存活戳 = `data/liaison/liveness.json`，`Path.write_text()` 写临时文件 + `os.replace()` 原子替换（覆写语义，末次写入即真相，不累积、不可能重复）。函数名仍按铁律 2 的 `effect_*` 前缀命名（它确实有副作用），但**不挂装饰器**，理由逐字写在函数 docstring 里，供 reviewer 核对。
⛔ 存活戳**不做 fsync**：它是心跳，掉电丢掉最后一次心跳的代价是"看起来早停了一秒"，而 `os.replace` 已经保证读到的永远是完整的一份。这与第 4 章附件落盘**必须** fsync 是两回事——那边丢的是不可恢复的材料，这边丢的是一个下一秒会被重写的时间戳。

**5. "存活戳只跟连接健康走"必须做成结构，不能只是注释。**
参考服务的生产 bug（`06-企业AI转型资产借鉴清单.md` §三、`gap_alert.py`）就是把"一段时间没消息"当成了断线。本实现让这个错误**在结构上写不出来**：`LiaisonSession.tick()` 的判据只有 `self._state == STATE_CONNECTED` 一个变量，会话对象里**根本不存在**"上一条消息什么时候来的"这个字段。
Task 2 加一条 AST 断言：`session.py` 的标识符（`ast.Name` / `ast.Attribute` / 形参名 / 函数名）里不得出现匹配 `last_?message|no_?message|idle|silence` 的名字。⛔ 断言只看标识符不看字符串与注释——写在注释里解释"⛔ 不按无消息判断线"是应该的，写成一个变量才是问题。

**6. 告警必须至少送到一次，宁可重复也不静默丢。**
"闭窗"与"发告警"不可能在一个事务里（发告警是外部动作）。若在"闭窗已提交"和"告警已送出"之间进程被杀，那条告警就永远没人发——而这恰恰是本章要消灭的那类静默缺口。
做法：`liaison_outage_window` 加一列 `alerted_at`，闭窗时**不**设置它；告警送出成功后再由 `effect_mark_window_alerted` 落。每次启动扫一遍"已闭合但 `alerted_at IS NULL`"的窗口补发。于是失败模式是**可能重复告警**（送出后、标记前被杀），⛔ 不是静默丢失。这个方向是刻意选的：重复的告警看得见、代价是收信人多看一眼；丢失的告警看不见、代价是漏掉的消息永远没人补发。
这同时让 7.5「告警通道失败只记日志、不中止接收」真正可恢复——发失败 ⇒ `alerted_at` 保持 NULL ⇒ 下次启动自动重试。

**Tech Stack:** Python 3.14（根 `pyproject.toml` 钉死 `>=3.14,<3.15`；第 1 章实测 3.14.6）· 标准库（`sqlite3` / `pathlib` / `json` / `os` / `logging` / `datetime` / `ast` / `time` / `typing.Protocol`）· `app.storage.idempotency.idempotent_effect`（唯一放行的 `app.*` 导入）· `wecom-aibot-python-sdk==1.0.2`（只在 `session_client.py` 的函数体内 import，根 venv 里跑测试时 skip）· pytest 8.3.4

---

## Global Constraints

以下逐字复制自 `CLAUDE.md`「工程铁律」「合规红线」「部署约束」、`design.md` 与本交付单元 opener。**reviewer 以此为注意力透镜。**

### 来自 CLAUDE.md 工程铁律（逐字）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
   *（本章三个库 effect 全部挂 `@idempotent_effect`，`business_key` = 窗口起始时间。恒等判据由本章自带的 `assert_outage_identity` 覆盖 `effect_open_outage_window`——见 Architecture 第 3 条为什么只有 INSERT 型才进恒等式。）*
2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。
   *（本章纯函数：`compute_outage_alert_text`、`compute_backoff_delay`。副作用：三个库 effect + `effect_write_liveness_stamp` + `effect_emit_outage_alert`。⛔ 不许出现既算又写的混合函数。）*
5. **`temperature=0`；模型版本优先显式锁定**，禁止 `latest` 类别名。
   *（本章不调任何模型。这条在这里的等价物是 SDK 版本：`wecom-aibot-python-sdk==1.0.2` 已钉死，⛔ 不许改成 `>=`。）*
6. **企微回调先落库再处理**：只推一次、5 秒无响应即丢弃。回调接口只做签名校验 + 落库 + 返回 200。
   *（本服务走 WS 长连接协议，不存在 HTTP 回调端点——`06` §三已澄清这条协议路径绕开了签名校验那一步。本章不新建任何入站端口。）*
7. **`langgraph >= 1.0.10`**（GHSA-g48c-2wqr-h844）。
   *（design D6：本服务不引入 LangGraph。`test_no_checkpointer_or_langgraph_in_liaison` 会扫本章新增的每个 `.py`，⛔ 源码里连 `langgraph` / `SqliteSaver` 字样都不许出现。）*

### 来自 CLAUDE.md 合规红线（逐字）

- **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。
- **AI 生成的 JD、拒信、邀约须带标识**（《AI 生成合成内容标识办法》2025-09-01 施行）。
  *（本章告警文本是模板拼接、不含任何模型生成内容，因此不需要 AI 标识；⛔ 也不许因为"顺手"往告警里塞任何模型输出。）*
- **模型全部走境内**，简历数据不出境。
- 候选人入口一律用一次性邀请链接，避免被认定"向境内公众提供"。
  *（本服务全部对象是内部同事，⛔ MUST NOT 用于向候选人发送任何内容——design D7 已定。）*
- 主观描述（"沟通能力强"）不得进入硬门槛规则。

**本章的合规落点**：告警文本与存活戳文件里 ⛔ **不得出现任何消息正文、发送人姓名、手机号或其他个人信息**——中断窗口只需要时间。Task 3 有断言守这条。

### 来自 CLAUDE.md 部署约束（逐字）

4. **目标服务器是 Windows，没有 Docker**。部署形态 = Python venv + Windows 计划任务（SYSTEM 账户 + AtStartup + 失败重启 3 次）+ 防火墙规则 + scp 推送。不要引入容器。
   *（本服务永不部署 `.51`，跑在 Mac 上、由 launchd 守护——launchd plist 是第 8 章 8.3，⛔ 本章不写。）*

### 来自 design.md（逐字）

- **D3 · 协议层丢失单独处理，不进幂等的账**：服务维护存活戳并**区分"真断线"与"空闲"**（参考服务曾混淆此二者、后修正）。检测到连接中断窗口时，MUST 记录该窗口的起止时间并显式告警"这段时间可能漏消息，请重发"。⛔ MUST NOT 因为"我们有幂等了"就把这个缺口当成已覆盖——幂等防的是重复，不是丢失。
- **D8 · SDK 选型**：SDK 内置 WS 连接管理、心跳（30s 间隔、2 次无响判死）与指数退避重连（封顶 30s），这些是协议细节而非业务逻辑，自己实现只会重复踩官方已处理的边界。第 1 章实测走**路线 ①**（`docs/findings/2026-09-08-aibot-sdk-py314-兼容性.md`：判据 A/B/C 全过，`wecom-aibot-python-sdk==1.0.2` 已钉死）。
- **D8 的实测遗留**（同一份 findings 的"意外与坑"逐字）：`max_reconnect_attempts: int = 10`（默认值 10，非无限），`-1` 表示无限重连。**这是第 7 章 7.6 的接线约束**，⛔ 第 1 章不实现，但第 7 章接线时必须显式把 `max_reconnect_attempts` 设为 `-1`，⛔ 不许用默认值 10。
- **D10 · 落点**：代码落 **`tools/liaison/`**。⛔ 不落 `app/`，⛔ 也不落 `scripts/`。依赖落 **`tools/liaison/requirements.txt`**，⛔ 不进根 `requirements.txt`。⛔ 不改 `sync-to-server.sh` / `deploy-server.ps1`。
- **D5 的单向依赖**：`tools/liaison` 可以 `import app.storage.idempotency`，⛔ `app/` 下任何模块不得 import `tools/`；`app.*` 的其余一切都不许进来（白名单由 `test_liaison_does_not_import_product_db_layer` 守）。
- **D12 · 进程守护**：内层重连交给 SDK 默认机制。进程级守护用 launchd（`KeepAlive` + `ThrottleInterval`）。⛔ 不移植参考服务的三级退避重启脚本（1min/5min/15min）。

### 来自本交付单元 opener（逐字，六条）

1. **7.1 逐字：存活戳只跟连接健康走，⛔ "一段时间无消息"≠断线**
2. **7.3 幂等：未闭合窗口按起始时间去重，重复启动不重复补记；窗口落 `data/liaison.db`（表由本章加，`CREATE TABLE IF NOT EXISTS`，⛔ 不改第 2 章两表）**
3. **7.4/7.5 逐字：告警内容含起止时间 + "该时段消息可能未收到、请重发"；⛔ 措辞不得声称缺口已被幂等机制覆盖；告警通道失败只记本地日志、⛔ 不中止接收。告警**发送通道**本章只定接口 + 日志实现（真实群 webhook 是第 6 章），⛔ 不在本章实现外发**
4. **7.6：用 SDK 内置重连；测试用 fake 连接对象验退避递增与"服务不退出"，⛔ 单测联真企微**
5. **🔴 真实建连需要 `HR_LIAISON_BOT_ID`/`SECRET`（Shao Peishen 尚未在企微后台注册）——本章全部用 fake，真实建连留步登记，⛔ 不用任何别的机器人凭据**
6. **D10 落点；本章独占 `tools/liaison/session*` `alerts*` `__main__.py`；⛔ 不碰 `storage/effects.py`、`archive*`、`queue*`（归档队列泳道在改）**

### 本章的五条"不做"

- ⛔ **不实现任何消息处理**：归档（第 4 章）、入队（第 5 章）、白名单准入（第 3 章已完成）、群通知外发（第 6 章）都不在本章。`__main__.py` 只为消息处理留一个**显式的空 handler 挂载点**并写明它属于第 4/5 章。
- ⛔ **不实现心跳、不实现"连上之后的重连"**：那是 SDK 的（D8）。本章只做 SDK 管不到的**外层建连重试**（第一次就连不上时 SDK 的内置重连尚未生效）。
- ⛔ **不做真实建连、不联真企微**：全部用 fake。真实建连按 opener 约束 5 留步登记。
- ⛔ **不写 launchd plist、不写留存清理**：第 8 章 8.1–8.4。
- ⛔ **不碰** `tools/liaison/storage/effects.py`、任何 `archive*`、任何 `queue*` 文件（并行泳道在改）。⛔ 不碰 `sync-to-server.sh`、`deploy-server.ps1`、根 `requirements.txt`、`pyproject.toml`。

---

## 前置状态与冲突处置（⚠️ controller 开工前必读）

**并行泳道正在改同一个包。** 本计划触碰的文件里，只有 `tools/liaison/storage/schema.py` 有与别人相撞的可能（其余全是本章独占的新文件或 `__main__.py`）。

**冲突处置已定，⛔ 不需要任何人当场回答**：
`storage/schema.py` 若产生冲突，**保留双方**——别人新增的常量与本章的 `OUTAGE_WINDOW_SCHEMA` 都留下，末尾的 `SCHEMA = ...` 拼接串把两边的常量**都串上**。⛔ 不许用整文件覆盖、⛔ 不许在两个常量里二选一。（同类现场见 memory「并行泳道收口会撞号」：撞车的解法一律是合并双方。）

**`data/liaison.db` 已存在时不需要迁移**：新表用 `CREATE TABLE IF NOT EXISTS`，`init_schema` 每次启动都跑一遍，老库启动即补上这张表。⛔ 不写任何迁移脚本。

## ⏸ 留步：真实建连未验（本章无法闭合，⛔ 不许假装完成）

`HR_LIAISON_BOT_ID` / `HR_LIAISON_BOT_SECRET` 需要 Shao Peishen 本人在企业微信管理后台注册 aibot 才能拿到（账号级操作，proposal「Impact · 人」已写明；第 1 章 findings 已登记过一次同样的留步）。

**本章的处置（已定）**：
- 全部测试用 fake 连接对象，⛔ 不联真企微、⛔ 不用任何别的机器人凭据凑数。
- `build_ws_options` / `make_sdk_connect` 的**结构**在本章完成并被测试覆盖（在装了 SDK 的 `tools/liaison/.venv` 里跑；根 venv 里 `importorskip` 跳过）。
- **真实建连、真实断线重连的端到端验证**记入 `tasks.md` 的验收备注为 `⏸ 留步：待 aibot 凭据注册后在第 8 章灰度（8.6）里验`。⛔ 不许把 7.6 的 checkbox 当作"真实链路已验"的证据。

## Spec Requirement → Task 对照

| `liaison-channel-session` 的 Requirement | 覆盖它的 Task |
|---|---|
| 凭据缺失时拒绝启动 | ⛔ **第 1 章已完成**（`config.py` + `__main__.py`）。本章 Task 6 只保证接线**不绕过**它，并有回归断言 |
| 存活戳区分空闲与断线（Scenario：长时间无消息但连接健康 / 连接真的断开） | Task 2、Task 4 |
| 连接中断窗口必须被记录（Scenario：断线后恢复 / 进程被杀后重启） | Task 1、Task 4 |
| 中断窗口必须显式告警且说明可能漏消息（Scenario：告警内容含时间窗口与重发请求 / 告警通道本身失败） | Task 3、Task 4 |
| 断线后自动恢复接收（Scenario：网络恢复后自动重连 / 长时间无法连接） | Task 5、Task 6 |

`tasks.md` 第 7 章条目对照：7.1 → Task 2；7.2 → Task 1 + Task 4；7.3 → Task 1 + Task 4；7.4 → Task 3；7.5 → Task 3 + Task 4；7.6 → Task 5 + Task 6；7.7 → Task 2 + Task 4；7.8 → Task 3 + Task 4；7.9 → Task 5。

---

### Task 1: 中断窗口表与三个幂等 effect

**Files:**
- Modify: `tools/liaison/storage/schema.py`（**只在文件末尾追加**一个常量并把它串进 `SCHEMA`，⛔ 不动 `EFFECT_LOG_SCHEMA` 与 `MESSAGE_AND_TASK_SCHEMA` 一个字）
- Create: `tools/liaison/session.py`
- Test: `tools/liaison/tests/test_session_outage_windows.py`

**Interfaces:**
- Consumes: `tools.liaison.storage.db.get_connection` / `init_schema`（第 2 章）；`app.storage.idempotency.idempotent_effect`（唯一放行的 `app.*` 导入）
- Produces:
  - 表 `liaison_outage_window(started_at PK, thread_id, detected_by, recovered_at, closed_by, alerted_at)`
  - 常量 `CONNECTION_THREAD_ID = "__liaison_connection__"`、`DETECTED_BY_DISCONNECT` / `DETECTED_BY_STARTUP_GAP` / `CLOSED_BY_RECONNECT` / `CLOSED_BY_STARTUP_BACKFILL`、`CHINA_TZ`
  - `format_instant(moment: datetime) -> str`
  - `effect_open_outage_window(conn, *, thread_id, business_key, detected_by) -> str | None`
  - `effect_close_outage_window(conn, *, thread_id, business_key, recovered_at, closed_by) -> str | None`
  - `effect_mark_window_alerted(conn, *, thread_id, business_key, alerted_at) -> str | None`
  - `select_open_windows(conn) -> list[str]`、`select_unalerted_closed_windows(conn) -> list[tuple[str, str]]`
  - 异常 `OutageWindowStateError`
  - Task 2–6 全部依赖这些名字。

- [ ] **Step 1: 写失败的测试（表与开窗）**

新建 `tools/liaison/tests/test_session_outage_windows.py`：

```python
"""第 7 章·中断窗口的库层断言（7.2 / 7.3）。

这三个 effect 不在 EFFECT_NODE_TO_TABLE 里（本章 ⛔ 不碰 storage/effects.py），
因此第 2 章的 assert_effect_log_identity 覆盖不到它们。恒等判据由本文件的
assert_outage_identity 自带——⛔ 不要因为"第 2 章已经有一条了"就省掉这条。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from tools.liaison import session
from tools.liaison.storage import db as liaison_db

T0 = datetime(2026, 9, 9, 10, 0, 0, tzinfo=session.CHINA_TZ)


@pytest.fixture
def conn(tmp_path):
    c = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(c)
    yield c
    c.close()


def assert_outage_identity(conn: sqlite3.Connection) -> None:
    """铁律 1 的恒等判据，本章版本：只比 INSERT 型的那个节点。

    ⛔ 不要把 effect_close_outage_window / effect_mark_window_alerted 也算进来——
    它们是 UPDATE，不产生新行，算进来这条等式会因为一个完全正当的理由变红，
    而那时最顺手的"修法"是削弱本断言。⛔ 明确写死：不许改成总数比较、不许约等于。
    """
    effect_counts = dict(
        conn.execute(
            "SELECT thread_id, COUNT(*) FROM effect_log WHERE node_name = ? GROUP BY thread_id",
            ("effect_open_outage_window",),
        ).fetchall()
    )
    business_counts = dict(
        conn.execute(
            "SELECT thread_id, COUNT(*) FROM liaison_outage_window GROUP BY thread_id"
        ).fetchall()
    )
    assert effect_counts == business_counts, (
        f"中断窗口恒等不变式破裂：effect_log={effect_counts} 表={business_counts}"
    )


def test_opening_a_window_writes_one_row_and_one_effect_log(conn):
    started = session.format_instant(T0)
    returned = session.effect_open_outage_window(
        conn,
        thread_id=session.CONNECTION_THREAD_ID,
        business_key=started,
        detected_by=session.DETECTED_BY_DISCONNECT,
    )
    assert returned == started
    rows = conn.execute(
        "SELECT started_at, detected_by, recovered_at, closed_by, alerted_at "
        "FROM liaison_outage_window"
    ).fetchall()
    assert rows == [(started, session.DETECTED_BY_DISCONNECT, None, None, None)]
    assert_outage_identity(conn)


def test_opening_the_same_started_at_twice_is_a_no_op(conn):
    """7.3 的幂等策略：按窗口起始时间去重。重复启动不重复补记。"""
    started = session.format_instant(T0)
    first = session.effect_open_outage_window(
        conn,
        thread_id=session.CONNECTION_THREAD_ID,
        business_key=started,
        detected_by=session.DETECTED_BY_STARTUP_GAP,
    )
    second = session.effect_open_outage_window(
        conn,
        thread_id=session.CONNECTION_THREAD_ID,
        business_key=started,
        detected_by=session.DETECTED_BY_STARTUP_GAP,
    )
    assert first == started
    assert second is None, "幂等命中必须返回 None，⛔ 不许再插一行"
    assert conn.execute("SELECT COUNT(*) FROM liaison_outage_window").fetchone()[0] == 1
    assert_outage_identity(conn)


def test_effect_key_format_matches_the_ironclad_rule(conn):
    """幂等键必须是 {thread_id}:{node_name}:{business_key}，business_key = 窗口起始时间。"""
    started = session.format_instant(T0)
    session.effect_open_outage_window(
        conn,
        thread_id=session.CONNECTION_THREAD_ID,
        business_key=started,
        detected_by=session.DETECTED_BY_DISCONNECT,
    )
    key = conn.execute("SELECT effect_key FROM effect_log").fetchone()[0]
    assert key == f"{session.CONNECTION_THREAD_ID}:effect_open_outage_window:{started}"


def test_format_instant_keeps_microseconds_and_the_china_offset():
    """⛔ 不许截到秒：同一秒内两次断线会撞主键，第二个窗口被幂等静默吃掉。"""
    text = session.format_instant(T0.replace(microsecond=123456))
    assert text == "2026-09-09T10:00:00.123456+08:00"


def test_format_instant_rejects_naive_datetime():
    with pytest.raises(ValueError):
        session.format_instant(datetime(2026, 9, 9, 10, 0, 0))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tools/liaison/tests/test_session_outage_windows.py -v`
Expected: FAIL —— `ImportError: cannot import name 'session' from 'tools.liaison'`（`session.py` 还不存在）

- [ ] **Step 3: 追加建表 DDL**

在 `tools/liaison/storage/schema.py` **文件末尾**追加（⛔ 上面两个常量一个字都不动）：

```python
#: 第 7 章·连接生命周期。窗口的键是**起始时间**——7.3 的幂等策略「按窗口起始时间
#: 去重」在这里有两道防线：主键（结构）与 idempotent_effect 的幂等键（机制），
#: 与第 2 章 liaison_task 的 UNIQUE + 装饰器同一手法。
#:
#: ⛔ 不要把 recovered_at 设成 NOT NULL DEFAULT ''：本表全靠 `recovered_at IS NULL`
#: 表达"这个窗口还没闭合"，空串会让"未闭合"与"闭合于空时间"两件事无法区分，
#: 而"未闭合"正是 7.3 启动期补记要找的那批行。
OUTAGE_WINDOW_SCHEMA = """
CREATE TABLE IF NOT EXISTS liaison_outage_window (
    -- 窗口起始时间（ISO8601、带 +08:00、精确到微秒），同时是幂等键的 business_key。
    started_at TEXT PRIMARY KEY,
    -- 恒等分组用。连接不是一个会话，取固定哨兵值 __liaison_connection__。
    thread_id TEXT NOT NULL,
    detected_by TEXT NOT NULL
        CHECK (detected_by IN ('disconnect_event', 'startup_gap')),
    recovered_at TEXT,
    closed_by TEXT
        CHECK (closed_by IS NULL OR closed_by IN ('reconnect', 'startup_backfill')),
    -- 告警**送出成功**后才落。⛔ 闭窗时不许顺手填——填了就等于宣称一条可能
    -- 根本没送出去的告警已经送到了，而这正是本章要消灭的那类静默缺口。
    alerted_at TEXT,
    -- 「闭合」必须两列同时有值。写成等式而不是两条 CHECK：拆开写容易只加一半。
    CHECK ((recovered_at IS NULL) = (closed_by IS NULL)),
    -- 没闭合的窗口不可能已经告警过（告警文本必须含恢复时间）。
    CHECK (alerted_at IS NULL OR recovered_at IS NOT NULL)
);

-- 启动期补记要找 recovered_at IS NULL 的行；补发告警要找 alerted_at IS NULL 的行。
CREATE INDEX IF NOT EXISTS idx_liaison_outage_open
    ON liaison_outage_window (recovered_at, started_at);
"""
```

然后把文件末尾原有的那一行

```python
SCHEMA = EFFECT_LOG_SCHEMA + MESSAGE_AND_TASK_SCHEMA
```

改成

```python
#: 本服务的全量 DDL。
SCHEMA = EFFECT_LOG_SCHEMA + MESSAGE_AND_TASK_SCHEMA + OUTAGE_WINDOW_SCHEMA
```

⚠️ 若此处与并行泳道冲突：**保留双方**，把两边新增的常量都串进这一行，⛔ 不许二选一、⛔ 不许整文件覆盖。

- [ ] **Step 4: 写 `session.py` 的时间工具与开窗 effect**

新建 `tools/liaison/session.py`：

```python
"""连接生命周期：存活戳、中断窗口、启动期补记（tasks.md 第 7 章）。

本模块的三条硬约束，改动前先读：

1. ⛔ **不许写 `with open(...)`**（连 `contextlib.suppress` 也不行）。
   tests/test_liaison_effects.py::test_no_second_transaction_manager_in_source 扫
   tools/liaison 下所有非测试 .py，把任何 `with <名字|属性|调用>:` 判为"隐式提交
   事务边界"违规——它认形状不认语义。文件读写一律用 Path.write_text/read_text +
   os.replace。⛔ 不要为了写 with 去改那个扫描器或往白名单里加本文件。
2. ⛔ **不许 conn.commit()/rollback()/executescript()**。提交由 idempotent_effect
   独占（storage/db.py 的模块 docstring 已写死），本模块的每一次库写入都必须
   挂在 @idempotent_effect 上。
3. ⛔ **本模块里不许出现"上一条消息什么时候来的"这种状态**。存活戳只跟连接健康走
   （spec「存活戳区分空闲与断线」、design D3）。参考服务把"一段时间没消息"当断线，
   是一个真实发生过的生产 bug（06-企业AI转型资产借鉴清单.md §三）。
   tests/test_session_liveness.py 有一条 AST 断言守着这一点。
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import sqlite3
from datetime import datetime, timedelta, timezone

from app.storage.idempotency import idempotent_effect

logger = logging.getLogger(__name__)

#: 固定 +08:00 偏移，⛔ 不用 ZoneInfo("Asia/Shanghai")：那要依赖系统 tzdata，
#: 而本服务的时间语义只需要一个固定偏移，多一个环境依赖就多一种"在别的机器上不一样"。
CHINA_TZ = timezone(timedelta(hours=8))

#: 幂等键需要一个 thread_id 分量，但**连接不是一个会话**——它不属于任何私聊或群。
#: 用一个固定哨兵值，双下划线包裹是为了不可能与真实的 userid / chatid 撞上。
CONNECTION_THREAD_ID = "__liaison_connection__"

DETECTED_BY_DISCONNECT = "disconnect_event"
DETECTED_BY_STARTUP_GAP = "startup_gap"
CLOSED_BY_RECONNECT = "reconnect"
CLOSED_BY_STARTUP_BACKFILL = "startup_backfill"


class OutageWindowStateError(RuntimeError):
    """要闭合／要标记的窗口不在预期状态。这是真 bug，⛔ 不许吞。"""


def format_instant(moment: datetime) -> str:
    """统一的时间字面量：ISO8601、+08:00、**精确到微秒**。

    ⛔ 不许截到秒：起始时间是窗口的主键与幂等键，同一秒内两次断线截到秒就会撞键，
    第二个窗口被幂等**静默**吃掉——而"静默吃掉一个中断窗口"正是本章要消灭的东西。

    ⛔ 不接受 naive datetime：没有时区的时间戳落进库里，将来没人能确定它是哪个
    时区的，而告警文本要把这个时间直接给人看。
    """
    if moment.tzinfo is None:
        raise ValueError("format_instant 需要带时区的 datetime，⛔ 不接受 naive 时间")
    return moment.astimezone(CHINA_TZ).isoformat(timespec="microseconds")


@idempotent_effect("effect_open_outage_window")
def effect_open_outage_window(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    detected_by: str,
) -> str:
    """开一个中断窗口。`business_key` = 窗口起始时间，也是表的主键。

    幂等命中（同一起始时间再开一次）由装饰器短路，返回 None——7.3 的
    「重复启动不重复补记」就是这条。
    """
    conn.execute(
        "INSERT INTO liaison_outage_window (started_at, thread_id, detected_by) "
        "VALUES (?, ?, ?)",
        (business_key, thread_id, detected_by),
    )
    return business_key
```

- [ ] **Step 5: 跑测试确认开窗那几条过**

Run: `python -m pytest tools/liaison/tests/test_session_outage_windows.py -v`
Expected: PASS —— 5 passed

- [ ] **Step 6: 写闭窗与标记告警的失败测试**

追加到 `tools/liaison/tests/test_session_outage_windows.py`：

```python
def _open(conn, moment, detected_by=session.DETECTED_BY_DISCONNECT):
    started = session.format_instant(moment)
    session.effect_open_outage_window(
        conn,
        thread_id=session.CONNECTION_THREAD_ID,
        business_key=started,
        detected_by=detected_by,
    )
    return started


def test_closing_a_window_sets_both_recovered_and_closed_by(conn):
    started = _open(conn, T0)
    recovered = session.format_instant(T0 + timedelta(minutes=3))
    returned = session.effect_close_outage_window(
        conn,
        thread_id=session.CONNECTION_THREAD_ID,
        business_key=started,
        recovered_at=recovered,
        closed_by=session.CLOSED_BY_RECONNECT,
    )
    assert returned == started
    row = conn.execute(
        "SELECT recovered_at, closed_by, alerted_at FROM liaison_outage_window"
    ).fetchone()
    assert row == (recovered, session.CLOSED_BY_RECONNECT, None)
    assert_outage_identity(conn)


def test_closing_the_same_window_twice_keeps_the_first_close(conn):
    """先被正常重连闭合过的窗口，下一次启动的补记 ⛔ 不许覆盖它。"""
    started = _open(conn, T0)
    first = session.format_instant(T0 + timedelta(minutes=3))
    session.effect_close_outage_window(
        conn,
        thread_id=session.CONNECTION_THREAD_ID,
        business_key=started,
        recovered_at=first,
        closed_by=session.CLOSED_BY_RECONNECT,
    )
    again = session.effect_close_outage_window(
        conn,
        thread_id=session.CONNECTION_THREAD_ID,
        business_key=started,
        recovered_at=session.format_instant(T0 + timedelta(hours=5)),
        closed_by=session.CLOSED_BY_STARTUP_BACKFILL,
    )
    assert again is None
    row = conn.execute("SELECT recovered_at, closed_by FROM liaison_outage_window").fetchone()
    assert row == (first, session.CLOSED_BY_RECONNECT)


def test_closing_a_window_that_was_never_opened_raises_and_leaves_no_trace(conn):
    """闭一个不存在的窗口是真 bug，必须炸，且 ⛔ 不许留下幂等记录。

    留下了幂等记录 = 系统认定"这件事做过了" = 真正该闭的那次永远不会再执行。
    """
    with pytest.raises(session.OutageWindowStateError):
        session.effect_close_outage_window(
            conn,
            thread_id=session.CONNECTION_THREAD_ID,
            business_key=session.format_instant(T0),
            recovered_at=session.format_instant(T0 + timedelta(minutes=1)),
            closed_by=session.CLOSED_BY_RECONNECT,
        )
    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 0
    assert_outage_identity(conn)


def test_mark_alerted_is_recorded_once_and_only_after_recovery(conn):
    started = _open(conn, T0)
    recovered = session.format_instant(T0 + timedelta(minutes=3))
    session.effect_close_outage_window(
        conn,
        thread_id=session.CONNECTION_THREAD_ID,
        business_key=started,
        recovered_at=recovered,
        closed_by=session.CLOSED_BY_RECONNECT,
    )
    alerted = session.format_instant(T0 + timedelta(minutes=4))
    assert (
        session.effect_mark_window_alerted(
            conn,
            thread_id=session.CONNECTION_THREAD_ID,
            business_key=started,
            alerted_at=alerted,
        )
        == started
    )
    assert (
        session.effect_mark_window_alerted(
            conn,
            thread_id=session.CONNECTION_THREAD_ID,
            business_key=started,
            alerted_at=session.format_instant(T0 + timedelta(minutes=9)),
        )
        is None
    )
    assert conn.execute("SELECT alerted_at FROM liaison_outage_window").fetchone()[0] == alerted


def test_select_open_windows_returns_only_unclosed_ones_oldest_first(conn):
    older = _open(conn, T0)
    newer = _open(conn, T0 + timedelta(hours=1))
    session.effect_close_outage_window(
        conn,
        thread_id=session.CONNECTION_THREAD_ID,
        business_key=newer,
        recovered_at=session.format_instant(T0 + timedelta(hours=2)),
        closed_by=session.CLOSED_BY_RECONNECT,
    )
    assert session.select_open_windows(conn) == [older]


def test_select_unalerted_closed_windows_skips_open_and_already_alerted(conn):
    open_one = _open(conn, T0)
    closed_unalerted = _open(conn, T0 + timedelta(hours=1))
    closed_alerted = _open(conn, T0 + timedelta(hours=2))
    for started, offset in ((closed_unalerted, 90), (closed_alerted, 150)):
        session.effect_close_outage_window(
            conn,
            thread_id=session.CONNECTION_THREAD_ID,
            business_key=started,
            recovered_at=session.format_instant(T0 + timedelta(minutes=offset)),
            closed_by=session.CLOSED_BY_RECONNECT,
        )
    session.effect_mark_window_alerted(
        conn,
        thread_id=session.CONNECTION_THREAD_ID,
        business_key=closed_alerted,
        alerted_at=session.format_instant(T0 + timedelta(minutes=151)),
    )
    pending = session.select_unalerted_closed_windows(conn)
    assert [row[0] for row in pending] == [closed_unalerted]
    assert open_one not in [row[0] for row in pending]


def test_table_rejects_half_closed_rows(conn):
    """CHECK 约束：recovered_at 与 closed_by 必须同时有值或同时为空。"""
    started = _open(conn, T0)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE liaison_outage_window SET recovered_at = ? WHERE started_at = ?",
            (session.format_instant(T0 + timedelta(minutes=1)), started),
        )
    conn.rollback()


def test_identity_scaffold_actually_catches_a_break(conn):
    """证伪：绕过 effect 直接插一行，恒等断言必须红。⛔ 不许只写断言不验它会红。"""
    _open(conn, T0)
    conn.execute(
        "INSERT INTO liaison_outage_window (started_at, thread_id, detected_by) "
        "VALUES (?, ?, ?)",
        ("2026-09-09T23:00:00.000000+08:00", session.CONNECTION_THREAD_ID, "disconnect_event"),
    )
    with pytest.raises(AssertionError):
        assert_outage_identity(conn)
    conn.rollback()
```

- [ ] **Step 7: 跑测试确认失败**

Run: `python -m pytest tools/liaison/tests/test_session_outage_windows.py -v`
Expected: FAIL —— `AttributeError: module 'tools.liaison.session' has no attribute 'effect_close_outage_window'`

- [ ] **Step 8: 实现闭窗、标记与两个查询**

追加到 `tools/liaison/session.py`：

```python
@idempotent_effect("effect_close_outage_window")
def effect_close_outage_window(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    recovered_at: str,
    closed_by: str,
) -> str:
    """闭合一个中断窗口。幂等键仍按**起始时间**去重（7.3 逐字）。

    ⛔ 本函数不许登记进 storage/effects.py 的 EFFECT_NODE_TO_TABLE：它是 UPDATE，
    不产生新行，登记进去会让"effect_log 条数 == 业务表行数"这条恒等式因为一个
    正当理由变红。恒等式只算 INSERT 型的 effect_open_outage_window。

    `WHERE recovered_at IS NULL` 与幂等键是两道独立的防线：一道在库里、一道在
    机制里。先被正常重连闭合过的窗口，之后的启动期补记会在**装饰器那一层**就被
    短路，因此 ⛔ 补记不可能覆盖掉真实的恢复时间。
    """
    cursor = conn.execute(
        "UPDATE liaison_outage_window SET recovered_at = ?, closed_by = ? "
        "WHERE started_at = ? AND recovered_at IS NULL",
        (recovered_at, closed_by, business_key),
    )
    if cursor.rowcount != 1:
        # 抛出去 ⇒ 装饰器回滚并原样上抛 ⇒ **不留幂等记录**。留了就等于宣称这件事
        # 做过了，真正该闭的那次从此永远不会再执行。
        raise OutageWindowStateError(
            f"要闭合的中断窗口不存在或已闭合：started_at={business_key}"
        )
    return business_key


@idempotent_effect("effect_mark_window_alerted")
def effect_mark_window_alerted(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    alerted_at: str,
) -> str:
    """标记"这个窗口的告警已经送出去了"。

    ⚠️ **只有在告警真的送出成功之后才允许调用**（alerts.effect_emit_outage_alert
    返回 True）。提前调用 = 宣称一条可能根本没送出的告警已经送到，
    而下一次启动的补发扫描正是靠 alerted_at IS NULL 找回这批漏发的。
    """
    cursor = conn.execute(
        "UPDATE liaison_outage_window SET alerted_at = ? "
        "WHERE started_at = ? AND alerted_at IS NULL",
        (alerted_at, business_key),
    )
    if cursor.rowcount != 1:
        raise OutageWindowStateError(
            f"要标记告警的窗口不存在或已标记：started_at={business_key}"
        )
    return business_key


def select_open_windows(conn: sqlite3.Connection) -> list[str]:
    """未闭合窗口的起始时间，**旧的在前**（只读，不进事务）。"""
    return [
        row[0]
        for row in conn.execute(
            "SELECT started_at FROM liaison_outage_window "
            "WHERE recovered_at IS NULL ORDER BY started_at"
        )
    ]


def select_unalerted_closed_windows(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """已闭合但告警还没送出去的窗口 `(started_at, recovered_at)`，旧的在前。

    这是"宁可重复告警、⛔ 不静默丢告警"的落点：告警发失败、或送出后进程被杀，
    这批行都还在，下一次启动会重新扫到。
    """
    return [
        (row[0], row[1])
        for row in conn.execute(
            "SELECT started_at, recovered_at FROM liaison_outage_window "
            "WHERE recovered_at IS NOT NULL AND alerted_at IS NULL ORDER BY started_at"
        )
    ]
```

- [ ] **Step 9: 跑本文件与第 2 章守卫测试**

Run: `python -m pytest tools/liaison/tests/test_session_outage_windows.py tools/liaison/tests/test_liaison_effects.py tools/liaison/tests/test_liaison_schema.py -v`
Expected: PASS —— 本文件 13 passed；`test_liaison_effects.py` 与 `test_liaison_schema.py` 全绿（尤其 `test_no_second_transaction_manager_in_source`、`test_effect_node_to_table_matches_the_insert_target_repo_wide`、`test_liaison_does_not_import_product_db_layer` 三条）

⚠️ 若 `test_no_second_transaction_manager_in_source` 红了，去看 `session.py` 里是不是写了 `with`——按本任务 Step 4 的模块 docstring 第 1 条处理，⛔ 不许改测试。

- [ ] **Step 10: 提交**

```bash
git add tools/liaison/session.py tools/liaison/storage/schema.py tools/liaison/tests/test_session_outage_windows.py
git commit -m "feat(liaison): 中断窗口表与三个幂等 effect（第 7 章 7.2/7.3 库层）"
```

---

### Task 2: 存活戳——只跟连接健康走，且这一点被结构钉死

**Files:**
- Modify: `tools/liaison/session.py`（追加，⛔ 不动 Task 1 已写的部分）
- Test: `tools/liaison/tests/test_session_liveness.py`

**Interfaces:**
- Consumes: Task 1 的 `format_instant` / `CHINA_TZ`
- Produces:
  - `STATE_STARTING = "starting"` / `STATE_CONNECTED = "connected"` / `STATE_DISCONNECTED = "disconnected"`
  - `DEFAULT_LIVENESS_PATH: pathlib.Path`（`<仓库根>/data/liaison/liveness.json`）
  - `effect_write_liveness_stamp(path, *, state, now, since=None) -> None`
  - `read_liveness_stamp(path) -> dict | None`
  - 存活戳 JSON 的字段契约：`{"state": str, "stamp_at": str, "since": str}`。Task 4／6 依赖这三个键名。

- [ ] **Step 1: 写失败的测试**

新建 `tools/liaison/tests/test_session_liveness.py`：

```python
"""第 7 章·存活戳（7.1 / 7.7 的一半）。

spec liaison-channel-session「存活戳区分空闲与断线」：存活戳 MUST 在连接健康但
无消息往来（空闲）时继续更新，MUST NOT 因为"一段时间没有消息"被判定为断线。
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
from datetime import datetime, timedelta

import pytest

from tools.liaison import session

T0 = datetime(2026, 9, 9, 10, 0, 0, tzinfo=session.CHINA_TZ)
SESSION_SOURCE = pathlib.Path(session.__file__)


def test_writing_a_stamp_creates_the_file_with_the_three_contract_keys(tmp_path):
    path = tmp_path / "liveness.json"
    session.effect_write_liveness_stamp(path, state=session.STATE_CONNECTED, now=T0)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {
        "state": "connected",
        "stamp_at": session.format_instant(T0),
        "since": session.format_instant(T0),
    }


def test_writing_a_stamp_creates_missing_parent_directories(tmp_path):
    path = tmp_path / "data" / "liaison" / "liveness.json"
    session.effect_write_liveness_stamp(path, state=session.STATE_STARTING, now=T0)
    assert path.is_file()


def test_stamp_keeps_refreshing_through_two_idle_hours(tmp_path):
    """spec Scenario「长时间无消息但连接健康」：两小时零消息，存活戳照更。

    ⛔ 这条用例里**根本没有"消息"这个概念**——因为存活戳的实现里也不该有。
    """
    path = tmp_path / "liveness.json"
    connected_since = T0
    stamps = []
    for minute in range(0, 121):
        now = T0 + timedelta(minutes=minute)
        session.effect_write_liveness_stamp(
            path, state=session.STATE_CONNECTED, now=now, since=connected_since
        )
        stamps.append(json.loads(path.read_text(encoding="utf-8"))["stamp_at"])
    assert stamps[-1] == session.format_instant(T0 + timedelta(hours=2))
    assert len(set(stamps)) == 121, "每一次都必须真的刷新，⛔ 不许写一次就不写了"
    assert json.loads(path.read_text(encoding="utf-8"))["since"] == session.format_instant(T0)


def test_stamp_is_replaced_atomically_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "liveness.json"
    session.effect_write_liveness_stamp(path, state=session.STATE_CONNECTED, now=T0)
    session.effect_write_liveness_stamp(
        path, state=session.STATE_CONNECTED, now=T0 + timedelta(seconds=30)
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == ["liveness.json"], (
        "临时文件必须被 os.replace 掉，⛔ 不许留下半截文件让外部读到"
    )


def test_reading_a_missing_stamp_returns_none(tmp_path):
    assert session.read_liveness_stamp(tmp_path / "nope.json") is None


def test_reading_a_corrupt_stamp_returns_none_and_logs(tmp_path, caplog):
    """存活戳坏了 ⇒ 当作"没有上一次的记录"，⛔ 不许炸掉启动流程。

    存活戳是诊断信息，不是账本；中断窗口的账在库里。为一个坏掉的诊断文件拒绝
    启动，等于让服务因为温度计坏了就不上班。
    """
    path = tmp_path / "liveness.json"
    path.write_text("{不是 JSON", encoding="utf-8")
    with caplog.at_level("WARNING"):
        assert session.read_liveness_stamp(path) is None
    assert "存活戳" in caplog.text


def test_reading_a_stamp_missing_required_keys_returns_none(tmp_path):
    path = tmp_path / "liveness.json"
    path.write_text(json.dumps({"state": "connected"}), encoding="utf-8")
    assert session.read_liveness_stamp(path) is None


def test_stamp_payload_carries_no_personal_information(tmp_path):
    """合规：窗口与存活戳只需要时间。⛔ 不许出现消息正文、姓名、手机号。"""
    path = tmp_path / "liveness.json"
    session.effect_write_liveness_stamp(path, state=session.STATE_CONNECTED, now=T0)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {"state", "stamp_at", "since"}


# ─────────────────────────────────────────────────────────────────────────
# 结构断言：让"按无消息判断线"这个 bug 在 session.py 里写不出来
# ─────────────────────────────────────────────────────────────────────────

_FORBIDDEN_IDENTIFIER = re.compile(r"(?i)(last_?message|no_?message|idle|silence|quiet)")


def _identifiers(tree: ast.AST) -> set[str]:
    """只收标识符：变量名、属性名、形参名、函数名。

    ⛔ 刻意不看字符串与注释——在注释里写「⛔ 不按无消息判断线」是**应该**的，
    把它变成一个变量才是问题。
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def test_session_module_has_no_message_timing_state():
    """7.1 逐字：存活戳只跟连接健康走，"一段时间无消息"≠断线。

    参考服务混淆过这两件事（06-企业AI转型资产借鉴清单.md §三），修正过一次。
    本断言让同样的 bug 在这个模块里**写不出来**：一旦有人加了 `last_message_at`
    之类的状态，这条当场红。
    ⛔ 红了不要往正则里加豁免——先问"这个模块为什么需要知道消息的时间"。
    """
    tree = ast.parse(SESSION_SOURCE.read_text(encoding="utf-8"), filename=str(SESSION_SOURCE))
    offenders = sorted(n for n in _identifiers(tree) if _FORBIDDEN_IDENTIFIER.search(n))
    assert offenders == [], f"session.py 出现了与消息时序有关的标识符：{offenders}"


def test_the_structural_guard_actually_catches_a_break():
    """证伪：喂一段带 last_message_at 的源码，上面那条判据必须抓到。"""
    tree = ast.parse(
        "def tick(now, last_message_at):\n"
        "    if now - last_message_at > 7200:\n"
        "        return 'disconnected'\n"
    )
    offenders = sorted(n for n in _identifiers(tree) if _FORBIDDEN_IDENTIFIER.search(n))
    assert offenders == ["last_message_at"]


def test_session_module_never_uses_a_with_statement():
    """⛔ tools/liaison 非测试代码里不许出现 with —— 第 2 章的事务扫描器会把任何
    `with <名字|属性|调用>:` 判为隐式提交违规（它认形状不认语义）。

    这条断言把那个远处的失败搬到本模块自己的测试里，省掉"为什么一个文件读写会让
    事务测试变红"这段排查。
    """
    tree = ast.parse(SESSION_SOURCE.read_text(encoding="utf-8"), filename=str(SESSION_SOURCE))
    assert not [n for n in ast.walk(tree) if isinstance(n, (ast.With, ast.AsyncWith))]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tools/liaison/tests/test_session_liveness.py -v`
Expected: FAIL —— `AttributeError: module 'tools.liaison.session' has no attribute 'effect_write_liveness_stamp'`

- [ ] **Step 3: 实现存活戳**

追加到 `tools/liaison/session.py`：

```python
#: tools/liaison/session.py → parents[0]=liaison, [1]=tools, [2]=仓库根
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: 存活戳落点。`data/` 已被 .gitignore:11 覆盖，⛔ 不会误入版本管理。
#: ⛔ 不落 data/liaison.db：存活戳是每次心跳都覆写的诊断信息，落库意味着每次心跳
#: 都要一次事务提交，而本模块**不许**自己提交（提交归 idempotent_effect 独占）。
DEFAULT_LIVENESS_PATH = REPO_ROOT / "data" / "liaison" / "liveness.json"

STATE_STARTING = "starting"
STATE_CONNECTED = "connected"
STATE_DISCONNECTED = "disconnected"

#: 存活戳 JSON 的字段契约。少任何一个都当作"读不到上一次的记录"。
LIVENESS_KEYS = ("state", "stamp_at", "since")


def effect_write_liveness_stamp(
    path: pathlib.Path,
    *,
    state: str,
    now: datetime,
    since: datetime | None = None,
) -> None:
    """盖一次存活戳（覆写语义，末次写入即真相）。

    **⛔ 本函数刻意不挂 @idempotent_effect，这不是漏了。**
    幂等键的语义是"这件事做过就不再做"。心跳恰恰要求每次都做——挂上装饰器，
    存活戳会**只写一次然后永远不再更新**，而外部看到的现象是"服务好像十分钟前
    就死了"。幂等在这里不是保护，是把功能反过来关掉。
    它也不需要幂等：覆写不累积、重复执行的结果与执行一次完全相同，本来就没有
    "重复"这个失败模式。

    ⛔ 不做 fsync：这是心跳，掉电丢掉最后一次的代价是"看起来早停了一秒"；
    `os.replace` 已经保证读者永远读到完整的一份。这与第 4 章附件落盘**必须**
    fsync 是两回事——那边丢的是不可恢复的材料。

    ⛔ 不用 `with open(...)`：见模块 docstring 第 1 条。
    """
    moment = format_instant(now)
    payload = {
        "state": state,
        "stamp_at": moment,
        "since": format_instant(since) if since is not None else moment,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    # 原子替换：外部读到的要么是上一份完整的，要么是这一份完整的，⛔ 不会是半截。
    os.replace(tmp_path, path)


def read_liveness_stamp(path: pathlib.Path) -> dict | None:
    """读上一次的存活戳。读不到／坏了／缺字段一律返回 None。

    ⛔ 不许因为存活戳坏了就拒绝启动：中断窗口的账在库里，存活戳只是诊断信息。
    为一个坏掉的温度计停工，代价与收益完全不成比例。
    """
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("存活戳读取失败，按「没有上一次记录」处理：%s", path, exc_info=True)
        return None
    if not isinstance(payload, dict) or any(key not in payload for key in LIVENESS_KEYS):
        logger.warning("存活戳字段不完整，按「没有上一次记录」处理：%s", path)
        return None
    return payload
```

⚠️ 上面两条 `logger.warning` 的日志文案用的是「」而不是双引号——⛔ 不要改成半角双引号，那会提前结束字符串字面量。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tools/liaison/tests/test_session_liveness.py -v`
Expected: PASS —— 12 passed

- [ ] **Step 5: 跑第 2 章守卫测试确认没被本任务弄红**

Run: `python -m pytest tools/liaison/tests/test_liaison_effects.py -q`
Expected: PASS（全绿）

- [ ] **Step 6: 提交**

```bash
git add tools/liaison/session.py tools/liaison/tests/test_session_liveness.py
git commit -m "feat(liaison): 存活戳只跟连接健康走，并用 AST 断言钉死（第 7 章 7.1）"
```

---

### Task 3: 中断告警——文本是纯函数，通道只定接口 + 日志实现

**Files:**
- Create: `tools/liaison/alerts.py`
- Test: `tools/liaison/tests/test_alerts.py`

**Interfaces:**
- Consumes: 无（本模块 ⛔ 不 import `session.py`，也 ⛔ 不 import `sqlite3`——它对"窗口存在库里"这件事一无所知）
- Produces:
  - `ALERT_RESEND_SENTENCE = "该时段消息可能未收到、请重发"`
  - `FORBIDDEN_ALERT_CLAIMS: tuple[str, ...]`
  - `compute_outage_duration_text(seconds: int) -> str`
  - `compute_outage_alert_text(started_at: str, recovered_at: str) -> str`
  - `class AlertSink(Protocol)`：唯一方法 `send(self, text: str) -> None`
  - `class LoggingAlertSink`：本章唯一实现
  - `effect_emit_outage_alert(sink: AlertSink, text: str) -> bool`（⛔ 永不抛）
  - Task 4／6 依赖 `AlertSink` 与 `effect_emit_outage_alert` 的返回值语义。

- [ ] **Step 1: 写失败的测试**

新建 `tools/liaison/tests/test_alerts.py`：

```python
"""第 7 章·中断告警（7.4 / 7.5 / 7.8）。

spec liaison-channel-session「中断窗口必须显式告警且说明可能漏消息」：
内容 MUST 包含该窗口的起止时间，并 MUST 明确说明该窗口内对方发送的消息可能未被
接收、需要重发；MUST NOT 把该缺口描述为已被重复投递保护（幂等）机制覆盖。
"""

from __future__ import annotations

import ast
import logging
import pathlib

import pytest

from tools.liaison import alerts

ALERTS_SOURCE = pathlib.Path(alerts.__file__)

STARTED = "2026-09-09T10:00:00.000000+08:00"
RECOVERED = "2026-09-09T10:03:12.000000+08:00"


def test_alert_text_contains_both_ends_of_the_window():
    """7.4 逐字：告警文本包含中断起止时间。"""
    text = alerts.compute_outage_alert_text(STARTED, RECOVERED)
    assert "2026-09-09 10:00:00" in text
    assert "2026-09-09 10:03:12" in text


def test_alert_text_contains_the_resend_request():
    """7.4 逐字：含"该时段消息可能未收到、请重发"的明确表述。"""
    text = alerts.compute_outage_alert_text(STARTED, RECOVERED)
    assert alerts.ALERT_RESEND_SENTENCE in text
    assert alerts.ALERT_RESEND_SENTENCE == "该时段消息可能未收到、请重发"


def test_alert_text_never_claims_the_gap_is_covered_by_idempotency():
    """⛔ 措辞不得声称缺口已被幂等机制覆盖（design D3 逐字）。

    幂等防的是同一条消息被处理两次，**不能补回从未到达的消息**。写一句
    "已由幂等机制保障不丢"是把一个真实缺口说成不存在——收信人因此不会重发。
    """
    text = alerts.compute_outage_alert_text(STARTED, RECOVERED)
    for claim in alerts.FORBIDDEN_ALERT_CLAIMS:
        assert claim not in text, f"告警文本出现了禁语：{claim}"
    assert alerts.FORBIDDEN_ALERT_CLAIMS, "禁语清单不许是空的"


def test_alert_text_carries_the_duration():
    text = alerts.compute_outage_alert_text(STARTED, RECOVERED)
    assert "3 分 12 秒" in text


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0 秒"),
        (7, "7 秒"),
        (60, "1 分 0 秒"),
        (192, "3 分 12 秒"),
        (3600, "1 小时 0 分 0 秒"),
        (7325, "2 小时 2 分 5 秒"),
    ],
)
def test_duration_text_covers_the_boundaries(seconds, expected):
    assert alerts.compute_outage_duration_text(seconds) == expected


def test_alert_text_rejects_a_window_that_ends_before_it_starts():
    """恢复时间早于起始时间是真 bug（时钟被改／参数传反），⛔ 不许拼出一条负数告警。"""
    with pytest.raises(ValueError):
        alerts.compute_outage_alert_text(RECOVERED, STARTED)


def test_alert_text_carries_no_personal_information():
    """合规：中断窗口只需要时间。⛔ 告警里不许出现消息正文、姓名、手机号、userid。"""
    text = alerts.compute_outage_alert_text(STARTED, RECOVERED)
    for token in ("userid", "@", "手机", "姓名", "http"):
        assert token not in text


def test_logging_sink_writes_the_alert_to_the_local_log(caplog):
    sink = alerts.LoggingAlertSink()
    with caplog.at_level(logging.WARNING):
        sink.send("【HR 值守通道·连接中断】测试")
    assert "【HR 值守通道·连接中断】测试" in caplog.text


def test_emit_returns_true_when_the_sink_accepts():
    sent = []

    class OkSink:
        def send(self, text: str) -> None:
            sent.append(text)

    assert alerts.effect_emit_outage_alert(OkSink(), "hello") is True
    assert sent == ["hello"]


def test_emit_returns_false_and_only_logs_when_the_sink_fails(caplog):
    """7.5 逐字：告警通道失败只记本地日志、⛔ 不中止接收。

    "不中止"在这一层的可执行形式 = **本函数不把异常抛出去**。抛出去，调用它的
    那条接收循环就会被一次告警失败打断——而告警失败与"能不能继续收消息"毫无关系。
    """

    class BoomSink:
        def send(self, text: str) -> None:
            raise RuntimeError("webhook 502")

    with caplog.at_level(logging.ERROR):
        assert alerts.effect_emit_outage_alert(BoomSink(), "hello") is False
    assert "告警" in caplog.text
    assert "webhook 502" in caplog.text


def test_emit_lets_keyboard_interrupt_through():
    """⛔ 不许吞 BaseException：Ctrl-C 与 SystemExit 必须能停下服务。"""

    class InterruptingSink:
        def send(self, text: str) -> None:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        alerts.effect_emit_outage_alert(InterruptingSink(), "hello")


def test_alerts_module_has_no_outbound_channel():
    """opener 约束 3：告警发送通道本章只定接口 + 日志实现，⛔ 不在本章实现外发。

    真实群 webhook 是第 6 章。这条断言让"顺手把 urllib 接上"当场变红。
    """
    tree = ast.parse(ALERTS_SOURCE.read_text(encoding="utf-8"), filename=str(ALERTS_SOURCE))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {"urllib", "http", "requests", "httpx", "socket", "aiohttp", "smtplib"}
    assert not (imported & forbidden), f"alerts.py 引入了外发通道：{sorted(imported & forbidden)}"


def test_alerts_module_never_uses_a_with_statement():
    """⛔ tools/liaison 非测试代码里不许出现 with（见 session.py 模块 docstring 第 1 条）。"""
    tree = ast.parse(ALERTS_SOURCE.read_text(encoding="utf-8"), filename=str(ALERTS_SOURCE))
    assert not [n for n in ast.walk(tree) if isinstance(n, (ast.With, ast.AsyncWith))]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tools/liaison/tests/test_alerts.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'tools.liaison.alerts'`

- [ ] **Step 3: 实现 `alerts.py`**

新建 `tools/liaison/alerts.py`：

```python
"""中断告警：文本怎么写、往哪送。

**本章只定接口 + 日志实现**（opener 约束 3）。真实的群 webhook 外发是第 6 章
（`liaison-group-notify`，带令牌桶限流与长度守卫）——⛔ 本模块不许出现任何
网络调用，`test_alerts_module_has_no_outbound_channel` 守着这条。

⛔ 不许写 `with`（见 session.py 模块 docstring 第 1 条：第 2 章的事务扫描器
会把任何 `with <名字|属性|调用>:` 判为隐式提交违规）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Protocol

logger = logging.getLogger(__name__)

#: 7.4 逐字要求的那句话。⛔ 改这句之前先读 spec：它是被 Scenario 直接断言的文本。
ALERT_RESEND_SENTENCE = "该时段消息可能未收到、请重发"

#: ⛔ 告警文本里绝不许出现的说法。
#:
#: 幂等防的是同一条消息被处理两次，**补不回从未到达的消息**（design D3 逐字）。
#: 一旦告警里写了"已由幂等机制保障"，收信人就不会重发——一个真实的缺口被一句
#: 让人安心的话盖住，而这正是本章存在的理由。
FORBIDDEN_ALERT_CLAIMS = (
    "幂等",
    "重复投递",
    "已覆盖",
    "已被覆盖",
    "无需重发",
    "不会丢",
    "不会遗漏",
    "已自动补收",
)


class AlertSink(Protocol):
    """告警的出口。**本章只定这个形状**，真实群通知在第 6 章接同一个形状。

    约定：送不出去就 `raise`。⛔ 不许在实现里自己吞掉异常然后假装送到了——
    调用方 `effect_emit_outage_alert` 需要靠异常判定"这条要留到下次重发"。
    """

    def send(self, text: str) -> None: ...


class LoggingAlertSink:
    """本章唯一实现：写本地运行日志。

    ⛔ 不是"临时占位"。第 6 章接上真实群通知之后，这个实现仍然有用武之地：
    单机灰度、测试、以及真实通道自己也挂掉时的兜底。
    """

    def __init__(self, target_logger: logging.Logger | None = None) -> None:
        self._logger = target_logger if target_logger is not None else logger

    def send(self, text: str) -> None:
        self._logger.warning("%s", text)


def compute_outage_duration_text(seconds: int) -> str:
    """把秒数说成人话。纯函数（铁律 2）。"""
    if seconds < 0:
        raise ValueError(f"中断时长不可能是负数：{seconds}")
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if hours:
        parts.append(f"{hours} 小时")
    if hours or minutes:
        parts.append(f"{minutes} 分")
    parts.append(f"{secs} 秒")
    return " ".join(parts)


def compute_outage_alert_text(started_at: str, recovered_at: str) -> str:
    """拼一条中断告警。纯函数：不读库、不写日志、不发送（铁律 2）。

    入参是 `session.format_instant` 产出的 ISO8601 字面量；展示时截到秒——
    微秒是键的精度需求，不是给人看的。
    """
    start = datetime.fromisoformat(started_at)
    end = datetime.fromisoformat(recovered_at)
    seconds = int((end - start).total_seconds())
    if seconds < 0:
        raise ValueError(
            f"恢复时间早于中断时间，参数可能传反了：started_at={started_at} "
            f"recovered_at={recovered_at}"
        )
    return (
        "【HR 值守通道·连接中断】"
        f"{start.strftime('%Y-%m-%d %H:%M:%S')} 至 {end.strftime('%Y-%m-%d %H:%M:%S')}"
        f"（持续 {compute_outage_duration_text(seconds)}）连接中断，期间无法接收消息。"
        f"{ALERT_RESEND_SENTENCE}。"
    )


def effect_emit_outage_alert(sink: AlertSink, text: str) -> bool:
    """把告警送出去。**⛔ 永不抛异常**，返回是否送成功。

    7.5 逐字：告警通道失败只记本地日志、⛔ 不中止接收。"不中止"在这一层的
    可执行形式就是这个 try/except——把异常抛给接收循环，一次告警失败就能打断
    消息接收，而这两件事毫无关系。

    返回 False ⇒ 调用方 ⛔ 不许标记 `alerted_at` ⇒ 下次启动会重新扫到并补发。
    ⛔ 捕获的是 `Exception` 而不是 `BaseException`：`KeyboardInterrupt` 与
    `SystemExit` 必须能停下服务。
    """
    try:
        sink.send(text)
    except Exception:
        logger.error(
            "中断告警发送失败，本次 ⛔ 不标记已告警，下次启动会重发；"
            "⛔ 不因此中止消息接收。原文：%s",
            text,
            exc_info=True,
        )
        return False
    return True
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tools/liaison/tests/test_alerts.py -v`
Expected: PASS —— 18 passed（含 6 条 parametrize）

- [ ] **Step 5: 提交**

```bash
git add tools/liaison/alerts.py tools/liaison/tests/test_alerts.py
git commit -m "feat(liaison): 中断告警文本与日志通道，禁语清单入断言（第 7 章 7.4/7.5）"
```

---

### Task 4: 会话状态机——把存活戳、窗口、告警串成一条可复现的时间线

**Files:**
- Modify: `tools/liaison/session.py`（追加 `LiaisonSession`，⛔ 不动 Task 1／2 已写的部分）
- Test: `tools/liaison/tests/test_session_state_machine.py`

**Interfaces:**
- Consumes: Task 1 的三个 effect 与两个查询、Task 2 的存活戳、Task 3 的 `AlertSink` / `compute_outage_alert_text` / `effect_emit_outage_alert`
- Produces:
  - `class LiaisonSession(conn, alert_sink, *, liveness_path=DEFAULT_LIVENESS_PATH)`
  - 方法 `start(now)` / `on_connected(now)` / `on_disconnected(now)` / `tick(now)`，只读属性 `state`
  - Task 5／6 只调这四个方法，⛔ 不直接调 effect。

- [ ] **Step 1: 写失败的测试**

新建 `tools/liaison/tests/test_session_state_machine.py`：

```python
"""第 7 章·会话状态机（7.2 / 7.3 / 7.5 / 7.7 / 7.8）。

这里的每条用例都是 spec liaison-channel-session 的一个 Scenario 的可执行形式。
时间全部由参数注入，⛔ 没有一处 sleep、⛔ 没有一处 datetime.now()——两小时的
空闲要在毫秒内跑完，且结果必须逐次可复现。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from tools.liaison import alerts, session
from tools.liaison.storage import db as liaison_db

T0 = datetime(2026, 9, 9, 10, 0, 0, tzinfo=session.CHINA_TZ)


class RecordingSink:
    """记下每一条被送出的告警。⛔ 不做任何网络动作。"""

    def __init__(self) -> None:
        self.texts: list[str] = []

    def send(self, text: str) -> None:
        self.texts.append(text)


class BoomSink:
    """永远送不出去的通道，用来验 7.5「失败只记日志、不中止接收」。"""

    def __init__(self) -> None:
        self.attempts = 0

    def send(self, text: str) -> None:
        self.attempts += 1
        raise RuntimeError("告警通道 502")


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "liaison.db"


@pytest.fixture
def liveness_path(tmp_path):
    return tmp_path / "liveness.json"


def make_session(db_path, liveness_path, sink):
    """新建一个会话对象 = 模拟一次**进程启动**（新连接、新内存状态、同一个库与存活戳）。"""
    conn = liaison_db.get_connection(db_path)
    liaison_db.init_schema(conn)
    return session.LiaisonSession(conn, sink, liveness_path=liveness_path)


def windows(conn):
    return conn.execute(
        "SELECT started_at, detected_by, recovered_at, closed_by, alerted_at "
        "FROM liaison_outage_window ORDER BY started_at"
    ).fetchall()


def test_two_idle_hours_produce_no_window_and_no_alert(db_path, liveness_path):
    """spec Scenario「长时间无消息但连接健康」：存活戳持续更新、不产生断线告警。

    ⛔ 本用例里一条消息都没有——这正是重点：连接健康与消息往来是两件事。
    """
    sink = RecordingSink()
    svc = make_session(db_path, liveness_path, sink)
    svc.start(T0)
    svc.on_connected(T0)
    for minute in range(1, 121):
        svc.tick(T0 + timedelta(minutes=minute))
    payload = json.loads(liveness_path.read_text(encoding="utf-8"))
    assert payload["state"] == session.STATE_CONNECTED
    assert payload["stamp_at"] == session.format_instant(T0 + timedelta(hours=2))
    assert payload["since"] == session.format_instant(T0)
    assert windows(svc.conn) == []
    assert sink.texts == []


def test_disconnect_opens_a_window_and_freezes_the_stamp(db_path, liveness_path):
    """spec Scenario「连接真的断开」：存活戳停止更新并记录断线时刻。"""
    sink = RecordingSink()
    svc = make_session(db_path, liveness_path, sink)
    svc.start(T0)
    svc.on_connected(T0)
    down_at = T0 + timedelta(minutes=5)
    svc.on_disconnected(down_at)

    frozen = json.loads(liveness_path.read_text(encoding="utf-8"))
    assert frozen["state"] == session.STATE_DISCONNECTED
    assert frozen["stamp_at"] == session.format_instant(down_at)

    # 断线期间继续 tick：存活戳 ⛔ 不许再动。
    for minute in range(6, 20):
        svc.tick(T0 + timedelta(minutes=minute))
    assert json.loads(liveness_path.read_text(encoding="utf-8")) == frozen

    rows = windows(svc.conn)
    assert len(rows) == 1
    assert rows[0][0] == session.format_instant(down_at)
    assert rows[0][1] == session.DETECTED_BY_DISCONNECT
    assert rows[0][2] is None, "还没恢复，⛔ 不许提前闭合"
    assert sink.texts == [], "窗口没闭合就 ⛔ 不许告警——告警文本必须含恢复时间"


def test_repeated_disconnect_events_do_not_open_a_second_window(db_path, liveness_path):
    """SDK 可能对同一次断线回调多次。⛔ 一次中断只许有一个窗口。"""
    svc = make_session(db_path, liveness_path, RecordingSink())
    svc.start(T0)
    svc.on_connected(T0)
    svc.on_disconnected(T0 + timedelta(minutes=5))
    svc.on_disconnected(T0 + timedelta(minutes=6))
    assert len(windows(svc.conn)) == 1


def test_reconnect_closes_the_window_and_alerts_once(db_path, liveness_path):
    """spec Scenario「断线后恢复」：存在一条起止时间完整的中断窗口记录 + 一条告警。"""
    sink = RecordingSink()
    svc = make_session(db_path, liveness_path, sink)
    svc.start(T0)
    svc.on_connected(T0)
    down_at = T0 + timedelta(minutes=5)
    up_at = T0 + timedelta(minutes=8)
    svc.on_disconnected(down_at)
    svc.on_connected(up_at)

    rows = windows(svc.conn)
    assert len(rows) == 1
    assert rows[0][2] == session.format_instant(up_at)
    assert rows[0][3] == session.CLOSED_BY_RECONNECT
    assert rows[0][4] is not None, "告警送出成功后必须落 alerted_at"

    assert len(sink.texts) == 1
    assert "2026-09-09 10:05:00" in sink.texts[0]
    assert "2026-09-09 10:08:00" in sink.texts[0]
    assert alerts.ALERT_RESEND_SENTENCE in sink.texts[0]

    # 恢复后存活戳重新开始更新。
    svc.tick(up_at + timedelta(minutes=1))
    payload = json.loads(liveness_path.read_text(encoding="utf-8"))
    assert payload["state"] == session.STATE_CONNECTED
    assert payload["stamp_at"] == session.format_instant(up_at + timedelta(minutes=1))


def test_restart_after_a_kill_during_an_outage_backfills_the_window(db_path, liveness_path):
    """spec Scenario「进程被杀后重启」：上一次未闭合的窗口被补记，恢复时间 = 本次启动时间。"""
    first = make_session(db_path, liveness_path, RecordingSink())
    first.start(T0)
    first.on_connected(T0)
    down_at = T0 + timedelta(minutes=5)
    first.on_disconnected(down_at)
    first.conn.close()  # 进程被 kill -9：没有任何优雅收尾

    sink = RecordingSink()
    restart_at = T0 + timedelta(hours=1)
    second = make_session(db_path, liveness_path, sink)
    second.start(restart_at)

    rows = windows(second.conn)
    assert len(rows) == 1
    assert rows[0][0] == session.format_instant(down_at)
    assert rows[0][2] == session.format_instant(restart_at)
    assert rows[0][3] == session.CLOSED_BY_STARTUP_BACKFILL
    assert len(sink.texts) == 1
    assert "2026-09-09 10:05:00" in sink.texts[0] and "2026-09-09 11:00:00" in sink.texts[0]


def test_restarting_again_does_not_backfill_or_alert_twice(db_path, liveness_path):
    """7.3 幂等：按窗口起始时间去重，重复启动不重复补记，也不重复告警。"""
    first = make_session(db_path, liveness_path, RecordingSink())
    first.start(T0)
    first.on_connected(T0)
    first.on_disconnected(T0 + timedelta(minutes=5))
    first.conn.close()

    second = make_session(db_path, liveness_path, RecordingSink())
    second.start(T0 + timedelta(hours=1))
    second.conn.close()

    third_sink = RecordingSink()
    third = make_session(db_path, liveness_path, third_sink)
    third.start(T0 + timedelta(hours=2))

    rows = windows(third.conn)
    assert len(rows) == 1, "⛔ 不许补记出第二条窗口"
    assert rows[0][2] == session.format_instant(T0 + timedelta(hours=1)), (
        "第二次启动补记的恢复时间 ⛔ 不许被第三次启动覆盖"
    )
    assert third_sink.texts == [], "已经告警过的窗口 ⛔ 不许再告警一次"


def test_restart_after_a_kill_while_connected_records_the_gap_from_the_last_stamp(
    db_path, liveness_path
):
    """连接健康时被杀：库里没有任何未闭合窗口，但那段停机时间同样收不到消息。

    spec Requirement 正文是「为**每一次连接中断**记录一个中断窗口」——进程不在了
    也是一种中断，只是两条 Scenario 没有单独举它。存活戳的最后一次盖戳时间正是
    这个窗口的起点，⛔ 不许因为"Scenario 没写"就让这段停机静默地过去。
    """
    first = make_session(db_path, liveness_path, RecordingSink())
    first.start(T0)
    first.on_connected(T0)
    last_stamp_at = T0 + timedelta(minutes=10)
    first.tick(last_stamp_at)
    first.conn.close()  # 连着的时候被 kill -9

    sink = RecordingSink()
    restart_at = T0 + timedelta(minutes=40)
    second = make_session(db_path, liveness_path, sink)
    second.start(restart_at)

    rows = windows(second.conn)
    assert len(rows) == 1
    assert rows[0][0] == session.format_instant(last_stamp_at)
    assert rows[0][1] == session.DETECTED_BY_STARTUP_GAP
    assert rows[0][2] == session.format_instant(restart_at)
    assert rows[0][3] == session.CLOSED_BY_STARTUP_BACKFILL
    assert len(sink.texts) == 1


def test_clean_restart_after_a_disconnected_stamp_records_no_gap(db_path, liveness_path):
    """上一次的存活戳是 disconnected/starting ⇒ ⛔ 不许凭空造一个 startup_gap 窗口。

    那段时间的账已经由"未闭合窗口补记"这条路径管着了，两条路径都记 = 一次中断
    被记成两个窗口 = 收信人收到两条内容重叠的告警。
    """
    first = make_session(db_path, liveness_path, RecordingSink())
    first.start(T0)
    first.conn.close()

    second = make_session(db_path, liveness_path, RecordingSink())
    second.start(T0 + timedelta(hours=3))
    assert windows(second.conn) == []


def test_a_stamp_from_the_future_is_ignored_instead_of_making_a_negative_window(
    db_path, liveness_path
):
    """机器时钟被往回调过：存活戳比"现在"还新。⛔ 不许造出一个负数长度的窗口。"""
    first = make_session(db_path, liveness_path, RecordingSink())
    first.start(T0)
    first.on_connected(T0)
    first.tick(T0 + timedelta(hours=5))
    first.conn.close()

    sink = RecordingSink()
    second = make_session(db_path, liveness_path, sink)
    second.start(T0 + timedelta(hours=1))
    assert windows(second.conn) == []
    assert sink.texts == []


def test_alert_failure_neither_stops_the_session_nor_marks_the_window(db_path, liveness_path):
    """7.5 / 7.8：告警发送失败 ⇒ 只记日志、不中止；alerted_at 保持 NULL。"""
    boom = BoomSink()
    svc = make_session(db_path, liveness_path, boom)
    svc.start(T0)
    svc.on_connected(T0)
    svc.on_disconnected(T0 + timedelta(minutes=5))
    svc.on_connected(T0 + timedelta(minutes=8))  # ⛔ 不许因为告警失败而抛出

    rows = windows(svc.conn)
    assert rows[0][2] is not None, "窗口该闭合的还是要闭合"
    assert rows[0][4] is None, "告警没送出去就 ⛔ 不许标记已告警"
    assert boom.attempts == 1

    # 接收照常继续：存活戳仍在更新。
    svc.tick(T0 + timedelta(minutes=9))
    assert json.loads(liveness_path.read_text(encoding="utf-8"))["stamp_at"] == (
        session.format_instant(T0 + timedelta(minutes=9))
    )


def test_a_failed_alert_is_retried_on_the_next_start(db_path, liveness_path):
    """宁可重复告警，⛔ 不静默丢告警：alerted_at 还是 NULL，下次启动重发。"""
    boom = BoomSink()
    first = make_session(db_path, liveness_path, boom)
    first.start(T0)
    first.on_connected(T0)
    first.on_disconnected(T0 + timedelta(minutes=5))
    first.on_connected(T0 + timedelta(minutes=8))
    first.conn.close()

    sink = RecordingSink()
    second = make_session(db_path, liveness_path, sink)
    second.start(T0 + timedelta(hours=1))
    assert len(sink.texts) == 1, "上次没送出去的告警必须补发"
    assert windows(second.conn)[0][4] is not None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tools/liaison/tests/test_session_state_machine.py -v`
Expected: FAIL —— `AttributeError: module 'tools.liaison.session' has no attribute 'LiaisonSession'`

- [ ] **Step 3: 实现 `LiaisonSession`**

追加到 `tools/liaison/session.py`（文件顶部的 import 补一行 `from tools.liaison import alerts as liaison_alerts`——⚠️ 放在模块顶部，`alerts.py` ⛔ 不 import `session.py`，不存在循环）：

```python
class LiaisonSession:
    """连接生命周期的状态机。**唯一持有"现在是连着还是断着"这个判断的地方。**

    ⛔ 本类里不存在"上一条消息什么时候来的"这种状态——`tick()` 的判据只有
    `self._state` 一个变量。7.1 逐字：存活戳只跟连接健康走，
    "一段时间无消息" ≠ 断线。tests/test_session_liveness.py 的 AST 断言守着这条。

    时间一律**由调用方传入**，⛔ 类内部不调 `datetime.now()`：两小时的空闲要在
    单测里毫秒跑完，且每次跑出来的时间线必须一模一样。
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        alert_sink: "liaison_alerts.AlertSink",
        *,
        liveness_path: pathlib.Path = DEFAULT_LIVENESS_PATH,
    ) -> None:
        self.conn = conn
        self.alert_sink = alert_sink
        self.liveness_path = liveness_path
        self._state = STATE_STARTING
        self._since: datetime | None = None

    @property
    def state(self) -> str:
        return self._state

    # ── 启动 ──────────────────────────────────────────────────────────
    def start(self, now: datetime) -> None:
        """一次进程启动要做的全部补记（7.3）。顺序是钉死的，⛔ 不许调换：

        1. 读上一次的存活戳；若上次死在**连接健康**的状态，用最后一次盖戳的时间
           开一个 `startup_gap` 窗口——那段停机时间同样收不到消息；
        2. 把**所有**未闭合窗口（含上一步刚开的那个）按"恢复时间 = 本次启动时间"
           闭合。一条闭合路径管两种成因，⛔ 不要为 gap 单独写一条；
        3. 补发所有"已闭合但还没告警"的窗口；
        4. 最后才写本次启动的存活戳——写早了，第 1 步就读不到上一次的了。
        """
        previous = read_liveness_stamp(self.liveness_path)
        if previous is not None and previous["state"] == STATE_CONNECTED:
            gap_started_at = previous["stamp_at"]
            if gap_started_at < format_instant(now):
                effect_open_outage_window(
                    self.conn,
                    thread_id=CONNECTION_THREAD_ID,
                    business_key=gap_started_at,
                    detected_by=DETECTED_BY_STARTUP_GAP,
                )
            else:
                # 存活戳比"现在"还新 ⇒ 机器时钟被往回调过。造窗口只会得到一段
                # 负数长度的中断，⛔ 记一笔日志就过去，不猜也不编。
                logger.warning(
                    "存活戳时间 %s 不早于本次启动时间 %s，跳过停机窗口补记",
                    gap_started_at,
                    format_instant(now),
                )
        self._backfill_open_windows(now)
        self._flush_pending_alerts(now)
        self._state = STATE_STARTING
        self._since = now
        effect_write_liveness_stamp(
            self.liveness_path, state=STATE_STARTING, now=now, since=now
        )

    # ── 连接事件 ──────────────────────────────────────────────────────
    def on_connected(self, now: datetime) -> None:
        """连上了（首次或重连）。把还开着的窗口闭合并告警。"""
        for started_at in select_open_windows(self.conn):
            effect_close_outage_window(
                self.conn,
                thread_id=CONNECTION_THREAD_ID,
                business_key=started_at,
                recovered_at=format_instant(now),
                closed_by=CLOSED_BY_RECONNECT,
            )
        self._flush_pending_alerts(now)
        self._state = STATE_CONNECTED
        self._since = now
        effect_write_liveness_stamp(
            self.liveness_path, state=STATE_CONNECTED, now=now, since=now
        )

    def on_disconnected(self, now: datetime) -> None:
        """断了。开窗 + 把存活戳定格在断线时刻，此后 `tick()` ⛔ 不再更新它。

        已经是断开状态时直接返回：SDK 对同一次断线回调多次是常态，
        ⛔ 一次中断只许有一个窗口。
        """
        if self._state == STATE_DISCONNECTED:
            return
        effect_open_outage_window(
            self.conn,
            thread_id=CONNECTION_THREAD_ID,
            business_key=format_instant(now),
            detected_by=DETECTED_BY_DISCONNECT,
        )
        self._state = STATE_DISCONNECTED
        self._since = now
        effect_write_liveness_stamp(
            self.liveness_path, state=STATE_DISCONNECTED, now=now, since=now
        )

    # ── 心跳 ──────────────────────────────────────────────────────────
    def tick(self, now: datetime) -> None:
        """周期性盖存活戳。**判据只有连接状态**（7.1 逐字）。

        ⛔ 不许在这里加任何"距离上一条消息多久"的判断——那正是参考服务踩过的
        那个生产 bug：把空闲当成断线，于是安静的下午会收到一串假的中断告警，
        而真正的断线反而被淹没在里面。
        """
        if self._state != STATE_CONNECTED:
            return
        effect_write_liveness_stamp(
            self.liveness_path,
            state=STATE_CONNECTED,
            now=now,
            since=self._since if self._since is not None else now,
        )

    # ── 内部 ──────────────────────────────────────────────────────────
    def _backfill_open_windows(self, now: datetime) -> None:
        for started_at in select_open_windows(self.conn):
            effect_close_outage_window(
                self.conn,
                thread_id=CONNECTION_THREAD_ID,
                business_key=started_at,
                recovered_at=format_instant(now),
                closed_by=CLOSED_BY_STARTUP_BACKFILL,
            )

    def _flush_pending_alerts(self, now: datetime) -> None:
        """把"已闭合但还没告警"的窗口逐条补发。

        送出**成功**才标记 `alerted_at`。失败 ⇒ 留着 ⇒ 下次启动再来一遍。
        于是失败模式是"可能重复告警"，⛔ 不是"静默丢告警"——这个方向是刻意选的：
        重复的告警看得见，丢掉的告警看不见。
        """
        for started_at, recovered_at in select_unalerted_closed_windows(self.conn):
            text = liaison_alerts.compute_outage_alert_text(started_at, recovered_at)
            if not liaison_alerts.effect_emit_outage_alert(self.alert_sink, text):
                continue
            effect_mark_window_alerted(
                self.conn,
                thread_id=CONNECTION_THREAD_ID,
                business_key=started_at,
                alerted_at=format_instant(now),
            )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tools/liaison/tests/test_session_state_machine.py -v`
Expected: PASS —— 10 passed

- [ ] **Step 5: 跑本章与第 2 章全部测试**

Run: `python -m pytest tools/liaison/tests -q`
Expected: PASS（本章 3 个测试文件 + 第 1–3 章既有测试全绿；`test_liaison_sdk_smoke.py` 里依赖 SDK 的三条在根 venv 里 skip 是预期行为）

- [ ] **Step 6: 提交**

```bash
git add tools/liaison/session.py tools/liaison/tests/test_session_state_machine.py
git commit -m "feat(liaison): 连接生命周期状态机——空闲不误判、断线记窗口、重启补记（第 7 章 7.2/7.3/7.7）"
```

---

### Task 5: 重连接线——退避是我们的，心跳与连内重连是 SDK 的

**Files:**
- Create: `tools/liaison/session_client.py`
- Create: `tools/liaison/scripts/probe_ws_surface.py`
- Create（探针跑得通时）: `docs/findings/2026-09-09-aibot-wsclient-表面实测.md`
- Test: `tools/liaison/tests/test_session_client.py`

**Interfaces:**
- Consumes: `tools.liaison.config.LiaisonCredentials`（第 1 章）
- Produces:
  - `BASE_BACKOFF_SECONDS = 1.0` / `MAX_BACKOFF_SECONDS = 30.0` / `HEALTHY_SESSION_SECONDS = 60.0`
  - `UNLIMITED_RECONNECT_ATTEMPTS = -1` / `DEFAULT_HEARTBEAT_SECONDS = 30`
  - `compute_backoff_delay(attempt, *, base_seconds, cap_seconds) -> float`
  - `run_forever(connect, *, sleep, monotonic, on_attempt_failed) -> NoReturn`
  - `build_ws_options(credentials, *, heartbeat_interval) -> "aibot.WSClientOptions"`
  - `make_sdk_connect(client, *, on_connected, on_disconnected) -> Callable[[], None]`
  - 异常 `SdkSurfaceUnverifiedError`
  - Task 6 只调 `run_forever` / `build_ws_options` / `make_sdk_connect`。

**这个任务的边界，先读清楚再写：**
SDK 负责**心跳**（30s 间隔、2 次无响判死）与**连上之后的重连**（指数退避封顶 30s）——design D8 已定，⛔ 本任务不实现、不模拟、不替换。
本任务只做 SDK 管不到的那一层：**建连尝试本身失败时的外层重试**。SDK 的内置重连在"从来没连上过"的时候还没生效（网线没插、DNS 挂了、凭据被停用），没有这一层，进程会在第一次建连失败时直接退出，然后一切都指望 launchd 重拉——而 launchd 的 `ThrottleInterval` 是固定间隔，⛔ 不是退避。

- [ ] **Step 1: 写失败的测试**

新建 `tools/liaison/tests/test_session_client.py`：

```python
"""第 7 章·重连接线（7.6 / 7.9）。

opener 约束 4 逐字：用 SDK 内置重连；测试用 fake 连接对象验退避递增与"服务不退出"，
⛔ 单测联真企微。本文件里没有任何一处真实网络调用，也没有一处 sleep。
"""

from __future__ import annotations

import pytest

from tools.liaison import session_client


class FakeClock:
    """每调用一次前进 `step` 秒的单调时钟。⛔ 不用真 time.monotonic：会让用例变慢且不可复现。"""

    def __init__(self, step: float = 0.0) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        current = self.value
        self.value += self.step
        return current


class _StopLoop(Exception):
    """测试专用：让"永不返回"的循环停下来的唯一手段。

    ⛔ 不给 run_forever 加"最多重试 N 次"的生产参数来方便测试——那个参数一旦
    存在，就迟早会有人在生产配置里把它设成一个有限值，服务于是会在某次长时间
    断网后**安静地退出**，而 spec 要的是"自动重试直至成功、服务不退出"。
    """


def make_sleep(stop_after: int):
    delays: list[float] = []

    def sleep(seconds: float) -> None:
        delays.append(seconds)
        if len(delays) >= stop_after:
            raise _StopLoop
    return sleep, delays


@pytest.mark.parametrize(
    ("attempt", "expected"),
    [(1, 1.0), (2, 2.0), (3, 4.0), (4, 8.0), (5, 16.0), (6, 30.0), (7, 30.0), (99, 30.0)],
)
def test_backoff_doubles_then_caps(attempt, expected):
    assert session_client.compute_backoff_delay(attempt) == expected


def test_backoff_never_returns_zero():
    """⛔ 0 就是紧密循环：spec「服务不退出、不占满 CPU」的另一半。"""
    for attempt in range(1, 200):
        assert session_client.compute_backoff_delay(attempt) > 0


def test_backoff_rejects_a_non_positive_attempt():
    with pytest.raises(ValueError):
        session_client.compute_backoff_delay(0)


def test_backoff_does_not_blow_up_on_a_huge_attempt_count():
    """连续失败一整夜也不许把 2 ** attempt 这个大整数算出来。"""
    assert session_client.compute_backoff_delay(10_000) == session_client.MAX_BACKOFF_SECONDS


def test_run_forever_backs_off_between_failed_attempts_and_never_returns():
    """spec Scenario「长时间无法连接」：重试间隔随失败次数增长，服务不退出。"""
    attempts = []

    def connect():
        attempts.append(1)
        raise ConnectionRefusedError("网线没插")

    sleep, delays = make_sleep(stop_after=5)
    with pytest.raises(_StopLoop):
        session_client.run_forever(connect, sleep=sleep, monotonic=FakeClock())

    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0]
    assert len(attempts) == 5
    assert delays == sorted(delays) and len(set(delays)) == 5, "间隔必须严格递增"


def test_run_forever_sleeps_even_when_connect_returns_immediately():
    """连上又立刻掉线（connect 正常返回）同样 ⛔ 不许变成紧密循环。

    这条比"connect 抛异常"更阴险：不抛异常的立即返回看起来像"成功了"，
    没有退避就是一个满速自旋的 while True。
    """
    calls = []

    def connect():
        calls.append(1)  # 立刻返回，不抛异常

    sleep, delays = make_sleep(stop_after=3)
    with pytest.raises(_StopLoop):
        session_client.run_forever(connect, sleep=sleep, monotonic=FakeClock())
    assert delays == [1.0, 2.0, 4.0]
    assert len(calls) == 3


def test_run_forever_resets_the_backoff_after_a_healthy_session():
    """连着跑了足够久再断 ⇒ 退避从头开始。

    ⛔ 不重置的话，一个连了三天的服务在第一次断线时就要等满 30 秒才重连——
    退避是为了保护"一直连不上"的场景，不是惩罚"连得挺好偶尔断一次"。
    """

    def connect():
        raise ConnectionResetError("对端断开")

    sleep, delays = make_sleep(stop_after=4)
    with pytest.raises(_StopLoop):
        session_client.run_forever(
            connect,
            sleep=sleep,
            monotonic=FakeClock(step=session_client.HEALTHY_SESSION_SECONDS + 1),
        )
    assert delays == [1.0, 1.0, 1.0, 1.0]


def test_run_forever_reports_each_failed_attempt():
    seen = []

    def connect():
        raise TimeoutError("超时")

    sleep, _ = make_sleep(stop_after=2)
    with pytest.raises(_StopLoop):
        session_client.run_forever(
            connect,
            sleep=sleep,
            monotonic=FakeClock(),
            on_attempt_failed=lambda attempt, delay, exc: seen.append((attempt, delay, type(exc))),
        )
    assert seen == [(1, 1.0, TimeoutError), (2, 2.0, TimeoutError)]


def test_run_forever_lets_keyboard_interrupt_out():
    """⛔ 不许吞 BaseException：Ctrl-C 与 launchd 的 SIGTERM 必须能停下服务。"""

    def connect():
        raise KeyboardInterrupt

    sleep, _ = make_sleep(stop_after=99)
    with pytest.raises(KeyboardInterrupt):
        session_client.run_forever(connect, sleep=sleep, monotonic=FakeClock())


# ─────────────────────────────────────────────────────────────────────────
# SDK 表面：装了 SDK 的 venv 里才跑；根 venv 里 skip 是预期行为（design D10）
# ─────────────────────────────────────────────────────────────────────────


def test_ws_options_disable_the_reconnect_attempt_ceiling():
    """findings 2026-09-08 实测：max_reconnect_attempts 默认 10，-1 才是无限。

    spec「断线后自动恢复接收」要求"自动重试**直至成功**"，⛔ 不许用默认值 10——
    10 次退避封顶 30 秒 ≈ 5 分钟后彻底放弃，之后服务还活着但永远不再连上，
    而这个状态**没有任何症状**。
    """
    pytest.importorskip("aibot", reason="aibot SDK 只装在 tools/liaison/.venv，根 venv skip 是预期")
    from tools.liaison.config import LiaisonCredentials

    options = session_client.build_ws_options(
        LiaisonCredentials(bot_id="fake-bot", bot_secret="fake-secret")
    )
    assert options.max_reconnect_attempts == session_client.UNLIMITED_RECONNECT_ATTEMPTS
    assert options.heartbeat_interval == session_client.DEFAULT_HEARTBEAT_SECONDS
    assert options.bot_id == "fake-bot"


def test_make_sdk_connect_refuses_a_client_missing_the_expected_surface():
    """SDK 换版本／表面对不上时 ⛔ 必须当场炸，不许"看起来接上了"。

    静默的错接线在这里的后果是：服务起来了、日志一片正常，但断线事件永远
    不会到达 LiaisonSession——于是中断窗口一条都不会有，"没有告警"被当成
    "一切正常"。
    """

    class BareClient:
        pass

    with pytest.raises(session_client.SdkSurfaceUnverifiedError) as excinfo:
        session_client.make_sdk_connect(
            BareClient(), on_connected=lambda: None, on_disconnected=lambda: None
        )
    for attr in session_client.REQUIRED_CLIENT_ATTRS:
        assert attr in str(excinfo.value)


def test_make_sdk_connect_subscribes_both_events_and_returns_a_blocking_callable():
    """用 fake 连接对象验接线形状，⛔ 不联真企微。"""
    events = {}
    ran = []

    class FakeClient:
        def on(self, event, handler):
            events[event] = handler

        def connect(self):
            ran.append(1)

    connect = session_client.make_sdk_connect(
        FakeClient(),
        on_connected=lambda: events.setdefault("_called_connected", True),
        on_disconnected=lambda: events.setdefault("_called_disconnected", True),
    )
    assert set(events) == {session_client.EVENT_CONNECTED, session_client.EVENT_DISCONNECTED}
    connect()
    assert ran == [1]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tools/liaison/tests/test_session_client.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'tools.liaison.session_client'`

- [ ] **Step 3: 实现 `session_client.py`**

新建 `tools/liaison/session_client.py`：

```python
"""SDK 接线：外层建连退避 + WSClientOptions 构造 + 事件订阅。

**边界**（design D8，⛔ 不许越）：
- SDK 负责心跳（30s、2 次无响判死）与**连上之后**的重连（指数退避封顶 30s）。
  ⛔ 本模块不实现、不模拟、不替换。
- 本模块只负责 SDK 管不到的那层：**建连尝试本身失败**时的外层重试。SDK 的内置
  重连在"从来没连上过"的时候还没生效，没有这一层，进程会在第一次建连失败时
  直接退出。

⛔ 不许写 `with`（见 session.py 模块 docstring 第 1 条）。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

#: 外层退避：1s 起、翻倍、封顶 30s。封顶值与 SDK 内置退避的封顶取同一个数，
#: 让两层的节奏是一致的（design D8 记的 SDK 封顶就是 30s）。
BASE_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 30.0

#: 一次连接活过这么久，就认为"连得挺好、只是偶尔断了"，退避从头开始。
#: ⛔ 不重置的话，连了三天的服务第一次断线也要等满 30 秒才重连。
HEALTHY_SESSION_SECONDS = 60.0

#: 超过这个尝试次数一律直接返回封顶值，⛔ 不去算 2 ** attempt 那个大整数。
_SATURATION_ATTEMPT = 64

#: findings 2026-09-08 实测：`max_reconnect_attempts` 默认 **10**，`-1` 才是无限。
#: spec 要求"自动重试直至成功"，⛔ 不许用默认值。
UNLIMITED_RECONNECT_ATTEMPTS = -1
DEFAULT_HEARTBEAT_SECONDS = 30

#: ⚠️ 事件名与必需方法名以 Step 5 的探针实测为准（见 docs/findings/
#: 2026-09-09-aibot-wsclient-表面实测.md）。探针没跑成时这里保持默认值，
#: `make_sdk_connect` 会在启动时**当场报错**，⛔ 不会静默错接线。
EVENT_CONNECTED = "connected"
EVENT_DISCONNECTED = "disconnected"
REQUIRED_CLIENT_ATTRS = ("on", "connect")


class SdkSurfaceUnverifiedError(RuntimeError):
    """SDK 的方法／事件表面与本模块的假设对不上。

    ⛔ 这个错误不许被吞成一条警告：接不上事件 ⇒ 断线事件永远到不了
    LiaisonSession ⇒ 中断窗口一条都不会有 ⇒ "没有告警"被当成"一切正常"。
    """


def compute_backoff_delay(
    attempt: int,
    *,
    base_seconds: float = BASE_BACKOFF_SECONDS,
    cap_seconds: float = MAX_BACKOFF_SECONDS,
) -> float:
    """第 `attempt` 次失败后该等多久。纯函数（铁律 2）。`attempt` 从 1 起。

    ⛔ 永不返回 0：0 就是紧密循环，spec 的「服务不退出、不占满 CPU」是两条要求，
    退出与自旋一样糟。
    """
    if attempt < 1:
        raise ValueError(f"attempt 从 1 起，收到 {attempt}")
    if attempt >= _SATURATION_ATTEMPT:
        return cap_seconds
    return min(base_seconds * (2 ** (attempt - 1)), cap_seconds)


def run_forever(
    connect: Callable[[], None],
    *,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    on_attempt_failed: Callable[[int, float, BaseException | None], None] | None = None,
) -> None:
    """反复建连，**⛔ 永不主动返回**（spec「网络恢复后自动重连……无需人工重启」）。

    `connect` 是一个"跑到断开为止"的阻塞调用。它抛异常（连不上）或正常返回
    （连上后又断了）都按同一件事处理：等一段退避，再来。
    ⛔ 两种情况都必须 sleep——正常返回那条路径不 sleep 就是满速自旋。

    ⛔ 刻意不提供"最多重试 N 次"的参数：那个参数一旦存在，迟早有人在生产配置里
    设成有限值，服务于是会在某次长时间断网后**安静地退出**。测试要停下这个循环，
    用一个会抛异常的 `sleep`。
    """
    attempt = 0
    while True:
        started = monotonic()
        outcome: BaseException | None = None
        try:
            connect()
        except Exception as exc:  # noqa: BLE001 —— 任何建连失败都只是"再试一次"
            outcome = exc
        # ⛔ 捕获 Exception 而不是 BaseException：KeyboardInterrupt 与 SystemExit
        # 必须能穿过这个循环把服务停下来。
        if monotonic() - started >= HEALTHY_SESSION_SECONDS:
            attempt = 0
        attempt += 1
        delay = compute_backoff_delay(attempt)
        logger.warning(
            "值守通道连接结束（第 %s 次尝试），%.1f 秒后重连：%r", attempt, delay, outcome
        )
        if on_attempt_failed is not None:
            on_attempt_failed(attempt, delay, outcome)
        sleep(delay)


def build_ws_options(credentials, *, heartbeat_interval: int = DEFAULT_HEARTBEAT_SECONDS):
    """构造 SDK 的连接参数。

    ⛔ `import aibot` 写在函数体里：根 venv 不装 SDK（design D10 的依赖隔离），
    模块层 import 会让全量 pytest 在 collect 阶段就整个红掉。
    """
    import aibot

    return aibot.WSClientOptions(
        bot_id=credentials.bot_id,
        secret=credentials.bot_secret,
        heartbeat_interval=heartbeat_interval,
        max_reconnect_attempts=UNLIMITED_RECONNECT_ATTEMPTS,
    )


def make_sdk_connect(
    client,
    *,
    on_connected: Callable[[], None],
    on_disconnected: Callable[[], None],
) -> Callable[[], None]:
    """把连接事件接到回调上，返回一个交给 `run_forever` 用的阻塞调用。

    先核对表面再接线：对不上就 raise，⛔ 不许"能接的先接上、接不上的算了"。
    """
    missing = [attr for attr in REQUIRED_CLIENT_ATTRS if not hasattr(client, attr)]
    if missing:
        raise SdkSurfaceUnverifiedError(
            f"SDK 连接对象缺少本模块依赖的方法 {missing}（期望 {list(REQUIRED_CLIENT_ATTRS)}）。"
            "请按 tools/liaison/scripts/probe_ws_surface.py 的实测输出更新 "
            "REQUIRED_CLIENT_ATTRS / EVENT_CONNECTED / EVENT_DISCONNECTED，"
            "并把原始输出落进 docs/findings/。⛔ 不要绕过本检查。"
        )
    client.on(EVENT_CONNECTED, lambda *args, **kwargs: on_connected())
    client.on(EVENT_DISCONNECTED, lambda *args, **kwargs: on_disconnected())
    return client.connect
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tools/liaison/tests/test_session_client.py -v`
Expected: PASS —— 18 passed, 1 skipped（`test_ws_options_disable_the_reconnect_attempt_ceiling` 在根 venv 里 skip 是预期行为）

- [ ] **Step 5: 写并跑 SDK 表面探针（跑不通就登记留步，⛔ 不猜）**

新建 `tools/liaison/scripts/probe_ws_surface.py`：

```python
"""`aibot.WSClient` 的方法与事件名实测探针（WBS 7.6）。

第 1 章的探针只验了"建连能力齐备"（类与四个 options 字段在）。**接线还需要知道
两件它没验的事**：跑起来的那个方法叫什么、连接/断开事件叫什么。这两个名字猜错
的后果是静默的——服务起得来、日志正常，但断线事件永远到不了 LiaisonSession。

⛔ 本探针**只做内省**：不建连、不发消息、不需要任何真实凭据。

用法（在装了 SDK 的那个 venv 里）：

    PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison.scripts.probe_ws_surface
"""

from __future__ import annotations

import importlib
import inspect
import json
import sys


def probe() -> dict:
    result: dict = {
        "python_version": sys.version.split()[0],
        "importable": False,
        "public_methods": [],
        "connect_like_signatures": {},
        "event_name_candidates": [],
        "base_classes": [],
        "import_error": None,
    }
    try:
        module = importlib.import_module("aibot")
    except Exception as exc:  # noqa: BLE001 —— 探针要如实记录任何 import 失败
        result["import_error"] = f"{type(exc).__name__}: {exc}"
        return result

    result["importable"] = True
    client_cls = getattr(module, "WSClient", None)
    if client_cls is None:
        result["import_error"] = "aibot 里没有 WSClient"
        return result

    result["base_classes"] = [base.__module__ + "." + base.__qualname__ for base in client_cls.__mro__[1:]]
    result["public_methods"] = sorted(
        name for name in dir(client_cls) if not name.startswith("_")
    )
    for name in ("connect", "run", "start", "run_forever", "listen", "on", "add_listener"):
        member = getattr(client_cls, name, None)
        if member is None:
            continue
        try:
            result["connect_like_signatures"][name] = str(inspect.signature(member))
        except (TypeError, ValueError):
            result["connect_like_signatures"][name] = "<签名不可读>"

    # 事件名常量：SDK 常把它们放在模块级常量或枚举里。全量列出来由人核对，
    # ⛔ 探针不做"看起来像连接事件"这种猜测。
    result["event_name_candidates"] = sorted(
        name for name in dir(module)
        if name.isupper() or "EVENT" in name.upper()
    )
    return result


if __name__ == "__main__":
    print(json.dumps(probe(), ensure_ascii=False, indent=2))
```

跑它（⚠️ 需要先有装了 SDK 的 venv；`tools/liaison/.venv` 不入版本管理，本机没有就现建）：

```bash
[ -d tools/liaison/.venv ] || /opt/homebrew/bin/python3.14 -m venv tools/liaison/.venv
tools/liaison/.venv/bin/pip install -r tools/liaison/requirements.txt
PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison.scripts.probe_ws_surface
```

**两条分支，⛔ 不需要任何人当场回答：**

- **探针跑通** → 新建 `docs/findings/2026-09-09-aibot-wsclient-表面实测.md`，把命令与**原始 JSON 输出**原样贴进去，然后按实测把 `session_client.py` 的 `REQUIRED_CLIENT_ATTRS` / `EVENT_CONNECTED` / `EVENT_DISCONNECTED` 改成真名，重跑 `python -m pytest tools/liaison/tests/test_session_client.py -v` 确认全绿（`test_make_sdk_connect_subscribes_both_events_and_returns_a_blocking_callable` 里的 `FakeClient` 也要跟着改成实测的方法名）。
- **探针跑不通**（无网络 / 装不上 / `import_error` 非空）→ ⛔ **不许猜名字**。在本任务的收工报告里登记
  `⏸ 留步：aibot WSClient 表面未实测（<原因>），session_client.py 的事件名沿用默认假设，启动时若对不上会由 SdkSurfaceUnverifiedError 当场报错`，常量保持不动，继续 Step 6。此时 `test_ws_options_*` 与探针相关的验证同样留步。

- [ ] **Step 6: 提交**

```bash
git add tools/liaison/session_client.py tools/liaison/scripts/probe_ws_surface.py tools/liaison/tests/test_session_client.py
git add docs/findings/2026-09-09-aibot-wsclient-表面实测.md 2>/dev/null || true
git commit -m "feat(liaison): 建连外层退避与 SDK 事件接线，max_reconnect_attempts 钉成 -1（第 7 章 7.6/7.9）"
```

---

### Task 6: `__main__.py` 接线——一条线程管库，一条线程管连接

**Files:**
- Modify: `tools/liaison/__main__.py`（第 1 章的凭据校验部分 ⛔ 原样保留，只在其后追加）
- Modify: `tools/liaison/session_client.py`（追加 `build_client`）
- Test: `tools/liaison/tests/test_main_wiring.py`

**Interfaces:**
- Consumes: Task 1–5 的全部产物 + 第 1 章的 `load_dotenv_into_environ` / `load_credentials`
- Produces: `EXIT_SDK_UNAVAILABLE = 3` / `EXIT_SDK_SURFACE_UNVERIFIED = 4`、`TICK_INTERVAL_SECONDS`、`build_session()`、`apply_connection_event()`、`run_session_worker()`、`main(*, session_builder, client_builder, runner)`

**这个任务唯一的技术难点，动手前先读：**
SDK 的连接回调跑在**它自己的线程／事件循环**上，而 `sqlite3` 的连接默认只能在创建它的那条线程里用（`check_same_thread=True`）。直接在回调里调 `svc.on_disconnected(...)` 会抛 `ProgrammingError`——而且是在**真的断线的那一刻**才抛，正是最不该出错的时候。

因此接线做成**两条线程 + 一个队列**：

- **值守线程**：自己创建 sqlite 连接、自己 `init_schema`、自己持有 `LiaisonSession`。库与状态机**只有它一个人碰**。它的循环是 `events.get(timeout=心跳间隔)`：拿到事件就处理，超时就盖一次存活戳。
- **主线程**：只跑 `run_forever(connect)`，SDK 回调只做一件事——把 `(事件名, 当时的时间)` 放进队列。⛔ 回调里不碰库、不碰文件。

⛔ **不要改成 `check_same_thread=False` + 一把锁**：那要动第 2 章的 `get_connection`（本章 ⛔ 不碰 `storage/effects.py` 的同批文件），而且把"谁拥有这个连接"这件事从结构问题降级成纪律问题。
⚠️ 事件的时间戳在**回调那一刻**取，⛔ 不许在值守线程处理时才取——晚取的话，中断窗口的起点会比真实断线时间晚最多一个心跳间隔。

- [ ] **Step 1: 写失败的测试**

新建 `tools/liaison/tests/test_main_wiring.py`：

```python
"""第 7 章·服务接线（7.6 的进程侧 + 第 1 章凭据校验的回归）。"""

from __future__ import annotations

import ast
import pathlib
import queue
from datetime import datetime, timedelta

import pytest

from tools.liaison import __main__ as liaison_main
from tools.liaison import session, session_client
from tools.liaison.storage import db as liaison_db

MAIN_SOURCE = pathlib.Path(liaison_main.__file__)
T0 = datetime(2026, 9, 9, 10, 0, 0, tzinfo=session.CHINA_TZ)


class RecordingSink:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def send(self, text: str) -> None:
        self.texts.append(text)


class StoppingEvent:
    """第 `after` 次询问时才说"停"。⛔ 不用真 threading.Event + sleep：那让用例不可复现。"""

    def __init__(self, after: int) -> None:
        self.after = after
        self.calls = 0

    def is_set(self) -> bool:
        self.calls += 1
        return self.calls > self.after


@pytest.fixture
def svc(tmp_path):
    conn = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(conn)
    return session.LiaisonSession(
        conn, RecordingSink(), liveness_path=tmp_path / "liveness.json"
    )


def test_apply_event_routes_connected_and_disconnected(svc):
    liaison_main.apply_connection_event(svc, liaison_main.EVENT_CONNECTED, T0)
    assert svc.state == session.STATE_CONNECTED
    liaison_main.apply_connection_event(
        svc, liaison_main.EVENT_DISCONNECTED, T0 + timedelta(minutes=1)
    )
    assert svc.state == session.STATE_DISCONNECTED


def test_apply_event_logs_and_survives_an_unknown_event(svc, caplog):
    """SDK 换版本多送一种事件 ⛔ 不许把值守线程打死。"""
    with caplog.at_level("ERROR"):
        liaison_main.apply_connection_event(svc, "reconnecting", T0)
    assert "reconnecting" in caplog.text
    assert svc.state == session.STATE_STARTING


def test_worker_uses_the_event_timestamp_not_the_processing_time(svc):
    """⚠️ 断线窗口的起点必须是**回调那一刻**，⛔ 不是值守线程处理它的时刻。"""
    events: queue.Queue = queue.Queue()
    events.put((liaison_main.EVENT_CONNECTED, T0))
    events.put((liaison_main.EVENT_DISCONNECTED, T0 + timedelta(minutes=5)))
    liaison_main.run_session_worker(
        svc,
        events,
        StoppingEvent(after=2),
        tick_interval=0.01,
        clock=lambda: T0 + timedelta(hours=9),  # 处理时刻故意远离事件时刻
    )
    started_at = svc.conn.execute("SELECT started_at FROM liaison_outage_window").fetchone()[0]
    assert started_at == session.format_instant(T0 + timedelta(minutes=5))


def test_worker_ticks_when_the_queue_stays_empty(svc, tmp_path):
    """队列空 = 一切正常 = 该盖存活戳。这条就是"空闲不误判"在进程侧的形式。"""
    svc.on_connected(T0)
    events: queue.Queue = queue.Queue()
    liaison_main.run_session_worker(
        svc,
        events,
        StoppingEvent(after=2),
        tick_interval=0.01,
        clock=lambda: T0 + timedelta(hours=2),
    )
    import json

    payload = json.loads((tmp_path / "liveness.json").read_text(encoding="utf-8"))
    assert payload["stamp_at"] == session.format_instant(T0 + timedelta(hours=2))


def test_main_still_refuses_to_start_without_credentials(tmp_path, monkeypatch, capsys):
    """第 1 章「凭据缺失时拒绝启动」的回归。⛔ 本章的接线不许绕过它。"""
    monkeypatch.setenv(liaison_main.DOTENV_PATH_ENV, str(tmp_path / "nope.env"))
    monkeypatch.delenv("HR_LIAISON_BOT_ID", raising=False)
    monkeypatch.delenv("HR_LIAISON_BOT_SECRET", raising=False)
    assert liaison_main.main() == liaison_main.EXIT_MISSING_CREDENTIALS
    assert "HR_LIAISON_BOT_ID" in capsys.readouterr().err


@pytest.fixture
def credentials_in_env(tmp_path, monkeypatch):
    monkeypatch.setenv(liaison_main.DOTENV_PATH_ENV, str(tmp_path / "nope.env"))
    monkeypatch.setenv("HR_LIAISON_BOT_ID", "fake-bot")
    monkeypatch.setenv("HR_LIAISON_BOT_SECRET", "fake-secret")


def test_main_exits_with_a_dedicated_code_when_the_sdk_is_missing(credentials_in_env, capsys):
    """SDK 装不上是配置问题，⛔ 不许进重试循环装作在等网络。"""

    def boom(_credentials):
        raise ImportError("No module named 'aibot'")

    ran = []
    assert (
        liaison_main.main(client_builder=boom, runner=lambda connect: ran.append(1))
        == liaison_main.EXIT_SDK_UNAVAILABLE
    )
    assert ran == [], "⛔ 不许在 SDK 缺失时进入 run_forever"
    assert "aibot" in capsys.readouterr().err


def test_main_exits_when_the_sdk_surface_does_not_match(credentials_in_env, capsys):
    class BareClient:
        pass

    ran = []
    assert (
        liaison_main.main(client_builder=lambda _c: BareClient(), runner=lambda c: ran.append(1))
        == liaison_main.EXIT_SDK_SURFACE_UNVERIFIED
    )
    assert ran == [], "⛔ 表面对不上时不许硬着头皮跑起来"


def test_main_wires_both_callbacks_into_the_queue(credentials_in_env, tmp_path):
    """happy path：回调只往队列里放 (事件, 时间)，⛔ 不碰库。"""
    handlers = {}

    class FakeClient:
        def on(self, event, handler):
            handlers[event] = handler

        def connect(self):
            raise AssertionError("本用例不应真的建连")

    captured: dict = {}

    def fake_runner(connect):
        captured["connect"] = connect
        raise KeyboardInterrupt

    def session_builder():
        conn = liaison_db.get_connection(tmp_path / "liaison.db")
        liaison_db.init_schema(conn)
        return session.LiaisonSession(
            conn, RecordingSink(), liveness_path=tmp_path / "liveness.json"
        )

    assert (
        liaison_main.main(
            session_builder=session_builder,
            client_builder=lambda _c: FakeClient(),
            runner=fake_runner,
        )
        == 0
    )
    assert set(handlers) == {session_client.EVENT_CONNECTED, session_client.EVENT_DISCONNECTED}
    assert callable(captured["connect"])


def test_this_chapter_wires_no_message_handling():
    """opener：本章 ⛔ 不实现归档／入队／群通知。这条断言让"顺手接上"当场变红。

    判据只看 `tools.liaison.` 开头的导入——⛔ 不能只匹配模块名里有没有 "queue"：
    标准库 `queue` 是本文件自己要用的，那样写会把它误伤成违规。
    """
    tree = ast.parse(MAIN_SOURCE.read_text(encoding="utf-8"), filename=str(MAIN_SOURCE))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    own = [name for name in imported if name.startswith("tools.liaison")]
    forbidden = [name for name in own if "archive" in name or "queue" in name]
    assert forbidden == [], f"__main__.py 接了本章范围外的模块：{forbidden}"


def test_main_module_never_uses_a_with_statement():
    tree = ast.parse(MAIN_SOURCE.read_text(encoding="utf-8"), filename=str(MAIN_SOURCE))
    assert not [n for n in ast.walk(tree) if isinstance(n, (ast.With, ast.AsyncWith))]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tools/liaison/tests/test_main_wiring.py -v`
Expected: FAIL —— `AttributeError: module 'tools.liaison.__main__' has no attribute 'apply_connection_event'`

- [ ] **Step 3: 给 `session_client.py` 补 `build_client`**

追加到 `tools/liaison/session_client.py`：

```python
def build_client(credentials):
    """按凭据造一个 SDK 连接对象。

    ⛔ `import aibot` 同样写在函数体里（根 venv 不装 SDK）。装不上时抛的是
    `ImportError`，调用方据此给一个**专用退出码**——SDK 缺失是配置问题，
    ⛔ 不许被当成"网络不好"进重试循环，那会让一个永远不会自愈的故障
    看起来像是在等待恢复。
    """
    import aibot

    return aibot.WSClient(build_ws_options(credentials))
```

⚠️ `WSClient(options)` 这个构造形状与 `EVENT_*` 一样，以 Task 5 Step 5 的探针实测为准；探针留步时保持本写法，启动时若对不上会由 `TypeError` 当场炸——⛔ 不会静默错接线。

- [ ] **Step 4: 改 `__main__.py`**

`tools/liaison/__main__.py` 的模块 docstring、`EXIT_MISSING_CREDENTIALS`、`REPO_ROOT`、`DOTENV_PATH_ENV`、`resolve_dotenv_path`、`load_dotenv_into_environ` 与 `load_credentials` 的调用**全部原样保留**（第 1 章产物）。把模块 docstring 里那句"建连、心跳、断线告警在第 7 章，⛔ 本章不实现"改写成下面的新版，删掉 `main()` 里那段"第 1 章到此为止"的 print，并追加：

```python
"""HR 值守通道服务的入口：`python -m tools.liaison`。

**两条线程，各管各的**（第 7 章）：
- **值守线程**独占 sqlite 连接与 `LiaisonSession`——库与状态机只有它一个人碰。
  它的循环是 `events.get(timeout=心跳间隔)`：有事件就处理，超时就盖存活戳。
- **主线程**只跑 `run_forever(connect)`。SDK 回调唯一做的事是把
  `(事件名, 当时的时间)` 放进队列，⛔ 回调里不碰库、不碰文件——sqlite 连接
  默认只能在创建它的线程里用，在断线回调里碰库会在最不该出错的那一刻抛异常。

⛔ 消息处理（归档=第 4 章、入队=第 5 章、群通知=第 6 章）不在本文件里。
tests/test_main_wiring.py::test_this_chapter_wires_no_message_handling 守着这条。
"""

import logging
import queue
import threading
from datetime import datetime

from tools.liaison import alerts, session, session_client
from tools.liaison.storage import db as liaison_db

logger = logging.getLogger(__name__)

#: SDK 装不上（配置问题，⛔ 不进重试循环）。
EXIT_SDK_UNAVAILABLE = 3
#: SDK 的方法／事件表面与接线假设对不上（⛔ 不许硬着头皮跑）。
EXIT_SDK_SURFACE_UNVERIFIED = 4

#: 存活戳的刷新间隔。取 15s——SDK 心跳是 30s、2 次无响判死（design D8），
#: 比它快一档，外部读存活戳时不至于把"正常心跳间隙"看成"停更"。
TICK_INTERVAL_SECONDS = 15.0

EVENT_CONNECTED = session_client.EVENT_CONNECTED
EVENT_DISCONNECTED = session_client.EVENT_DISCONNECTED


def now() -> datetime:
    """当前时间，带 +08:00。⛔ 唯一允许调 `datetime.now()` 的地方——
    状态机里一律由调用方传时间，好让两小时的场景能在单测里毫秒跑完。"""
    return datetime.now(session.CHINA_TZ)


def build_session() -> session.LiaisonSession:
    """在**调用它的那条线程里**建连接与状态机。⛔ 不许在别处建好再传进来。"""
    conn = liaison_db.get_connection()
    liaison_db.init_schema(conn)
    return session.LiaisonSession(conn, alerts.LoggingAlertSink())


def apply_connection_event(svc: session.LiaisonSession, name: str, moment: datetime) -> None:
    """把一个连接事件喂给状态机。未知事件只记 ERROR，⛔ 不许打死值守线程。"""
    if name == EVENT_CONNECTED:
        svc.on_connected(moment)
    elif name == EVENT_DISCONNECTED:
        svc.on_disconnected(moment)
    else:
        logger.error("收到未知的连接事件 %r（时间 %s），已忽略", name, moment)


def run_session_worker(
    svc: session.LiaisonSession,
    events: "queue.Queue",
    stop_event,
    *,
    tick_interval: float = TICK_INTERVAL_SECONDS,
    clock=now,
) -> None:
    """值守线程主体：处理事件，没事件就盖存活戳。

    ⚠️ 事件自带时间戳（回调那一刻取的），这里 ⛔ 不许用 `clock()` 覆盖它——
    否则中断窗口的起点会比真实断线时间晚最多一个心跳间隔。
    """
    while not stop_event.is_set():
        try:
            name, moment = events.get(timeout=tick_interval)
        except queue.Empty:
            svc.tick(clock())
            continue
        apply_connection_event(svc, name, moment)


def _session_thread_main(events, stop_event, session_builder) -> None:
    svc = session_builder()
    svc.start(now())
    run_session_worker(svc, events, stop_event)


def main(
    *,
    session_builder=build_session,
    client_builder=session_client.build_client,
    runner=session_client.run_forever,
) -> int:
    """⚠️ 三个关键字参数是**接线缝**，只给测试注入 fake 用，⛔ 不是配置项——
    ⛔ 不要给它们加环境变量开关。"""
    load_dotenv_into_environ(resolve_dotenv_path())

    try:
        credentials = load_credentials()
    except MissingCredentialsError as exc:
        # 只打变量名，⛔ 不打取值。进程立刻退，⛔ 不进任何等待/重试循环。
        print(str(exc), file=sys.stderr)
        return EXIT_MISSING_CREDENTIALS

    try:
        client = client_builder(credentials)
    except ImportError as exc:
        print(
            f"HR 值守通道拒绝启动：aibot SDK 不可用（{exc}）。"
            "请在 tools/liaison/.venv 里装 tools/liaison/requirements.txt。"
            "⛔ 不要把它加进根 requirements.txt（design D10）。",
            file=sys.stderr,
        )
        return EXIT_SDK_UNAVAILABLE

    events: queue.Queue = queue.Queue()

    try:
        connect = session_client.make_sdk_connect(
            client,
            on_connected=lambda: events.put((EVENT_CONNECTED, now())),
            on_disconnected=lambda: events.put((EVENT_DISCONNECTED, now())),
        )
    except session_client.SdkSurfaceUnverifiedError as exc:
        print(f"HR 值守通道拒绝启动：{exc}", file=sys.stderr)
        return EXIT_SDK_SURFACE_UNVERIFIED

    stop_event = threading.Event()
    worker = threading.Thread(
        target=_session_thread_main,
        args=(events, stop_event, session_builder),
        name="liaison-session",
        daemon=True,
    )
    worker.start()

    try:
        runner(connect)
    except KeyboardInterrupt:
        stop_event.set()
        logger.warning("收到中断信号，值守通道停止接收")
    return 0
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tools/liaison/tests/test_main_wiring.py -v`
Expected: PASS —— 10 passed

- [ ] **Step 6: 跑全量**

Run: `python -m pytest -q`
Expected: PASS —— 全仓绿（`tools/liaison/tests` 里依赖 SDK 的用例 skip 是预期行为）

- [ ] **Step 7: 回勾 `tasks.md` 第 7 章并登记留步**

把 `openspec/changes/hr-wecom-aibot-liaison/tasks.md` 第 7 章的 7.1–7.9 全部改成 `- [x]`，并在该章「验收」那一行**后面另起一行**追加：

```markdown
⏸ 留步：真实建连与真实断线重连未验——需要 Shao Peishen 在企微后台注册 aibot 取得
`HR_LIAISON_BOT_ID` / `HR_LIAISON_BOT_SECRET`。本章全部用 fake 连接对象验到接线形状与
退避行为；真实链路归第 8 章 8.6 灰度。⛔ 7.6 的勾不代表真实链路已验。
```

⚠️ 若 `tasks.md` 与并行泳道冲突：**合并双方的勾**，⛔ 不许二选一（memory「并行泳道收口会撞号」）。

- [ ] **Step 8: 提交**

```bash
git add tools/liaison/__main__.py tools/liaison/session_client.py tools/liaison/tests/test_main_wiring.py openspec/changes/hr-wecom-aibot-liaison/tasks.md
git commit -m "feat(liaison): 服务接线——值守线程独占库、主线程跑连接（第 7 章 7.6，回勾 7.1-7.9）"
```

---

## 收尾

- **⛔ 不跑 `openspec-archive-change`**：第 8 章（留存清理、进程守护、灰度验收）还没做，变更包未完成。归档时限的判据是「`tasks.md` 全部勾选后当场归档」，本章勾完只到 7.9。
- **下一个交付单元**：第 8 章 8.1–8.8。⚠️ 8.1 的留存期清理会让第 2 章 `assert_effect_log_identity` 的隐含前提（业务表只增不删）失效——那份 docstring 里已经写死了唯一允许的两种应对，⛔ 不许削弱断言。
- **本章遗留给第 6 章的接口**：`alerts.AlertSink`（唯一方法 `send(text) -> None`，送不出去就 raise）。第 6 章的群通知实现同一个形状即可替换 `LoggingAlertSink`，⛔ 不需要改 `session.py` 一行。

## 交付前自查记录

**1. spec 覆盖**（`liaison-channel-session` 五条 Requirement）

| Requirement | Task | 备注 |
|---|---|---|
| 凭据缺失时拒绝启动 | 第 1 章已完成；Task 6 有回归断言 | `test_main_still_refuses_to_start_without_credentials` |
| 存活戳区分空闲与断线 | Task 2 / Task 4 | 两条 Scenario 各有专属用例；另有 AST 结构断言 |
| 连接中断窗口必须被记录 | Task 1 / Task 4 | 两条 Scenario 各有专属用例；另覆盖了"连着被杀"的第三种形态 |
| 中断窗口必须显式告警且说明可能漏消息 | Task 3 / Task 4 | 含禁语清单断言与"告警失败不中止"两条 |
| 断线后自动恢复接收 | Task 5 / Task 6 | 两条 Scenario 各有专属用例；`max_reconnect_attempts=-1` 单独断言 |

**2. 占位符扫描**：无 TBD / TODO / "适当处理错误" / "参照 Task N"。每个代码步骤都给了完整可粘贴的代码与确切命令。唯一的条件分支是 Task 5 Step 5 的探针两分支——两条路径都写全了确定动作（跑通就改常量并落 findings；跑不通就登记留步、常量不动），⛔ 不需要任何人当场回答。

**3. 类型与命名一致性**（跨 Task 核对过的名字）

- `format_instant` / `CONNECTION_THREAD_ID` / `STATE_*` / `DETECTED_BY_*` / `CLOSED_BY_*`：Task 1、2 定义，Task 4、6 与全部测试引用，拼写一致。
- 三个 effect 的关键字参数一律 `(conn, *, thread_id, business_key, ...)`，与 `idempotent_effect` 装饰器要求的签名一致。
- `AlertSink.send(text) -> None`、`effect_emit_outage_alert(sink, text) -> bool`：Task 3 定义，Task 4 `_flush_pending_alerts` 与 Task 6 的 `LoggingAlertSink` 用同一形状。
- `EVENT_CONNECTED` / `EVENT_DISCONNECTED` 只有 `session_client.py` 一处真源，`__main__.py` 是取别名而不是各写一份。

**4. 与第 2 章既有守卫的相容性**（本计划逐条核对过实际判据，不是想当然）

| 既有断言 | 本章是否触发 | 依据 |
|---|---|---|
| `test_no_second_transaction_manager_in_source` | 否 | 本章无 `commit`/`rollback`/`executescript`，且**全模块无 `with`**（三个新模块各有一条自测守着） |
| `test_effect_node_to_table_matches_reality` | 否 | 它只看 `vars(storage.effects)`，本章 effect 定义在 `session.py` |
| `test_effect_node_to_table_matches_the_insert_target_repo_wide` | 否 | `_validate_node_to_table_mapping` 只遍历 mapping 的键，未登记的 effect 不计违规 |
| `test_liaison_does_not_import_product_db_layer` | 否 | 只 import `app.storage.idempotency`（白名单内） |
| `test_no_checkpointer_or_langgraph_in_liaison` | 否 | 本章源码不出现 `langgraph` / `SqliteSaver` |
| `assert_effect_log_identity` | 否 | 它按 `node_name` 过滤，本章的节点名不在它的遍历范围内；本章自带 `assert_outage_identity` 补上恒等判据 |

**5. 端到端提取验证**：⏸ **留步——本计划未做**。SKILL 第 6 步（把代码块提取到临时目录、装独立 venv、跑全量）需要建 venv 与 `pip install`（含 `wecom-aibot-python-sdk` 的 16 个传递依赖）。本 session 是 `run-lanes.sh` 无头起的计划编写任务，opener 明确 ⛔ 不进 run-build，且提取验证会与 Task 5 Step 5 的探针抢同一个 venv 落点。**处置**：这一步下放给 run-build 的第一个 Task——Task 1 Step 2 的"跑测试确认失败"就是第一次真实执行，任何转录误差在那里立刻暴露。⛔ 不许因为本计划自查栏写着"未做"就跳过 run-build 里的每一步 `pytest`。

## 下一步

用 `run-build` 执行本计划（`superpowers:subagent-driven-development`，每个 Task 一个新子代理 + 两阶段 review）。⚠️ 开工前确认 `tools/liaison/storage/effects.py`、`archive*`、`queue*` 不在本次改动清单里——那批文件归并行泳道。
