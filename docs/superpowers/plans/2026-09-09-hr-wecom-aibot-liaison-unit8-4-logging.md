# 日志接入：轮转 + 有界容量 + 个人信息脱敏（hr-wecom-aibot-liaison 交付单元 8.4）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 HR 值守通道这个**没有控制台的常驻进程**在出事之后还有话可说——运行证据落到有界的轮转文件里（不会把磁盘写满、也不会因为没人开终端就整段消失），同时任何流经本包的日志行都不许把手机号、邮箱、身份证号或凭据取值写成明文；而排障真正要用的两个键（`msgid` / `thread_id`）**必须原样保留**，不许被脱敏顺手一起吃掉。

**Architecture:**

```
tools/liaison/                          ← ⛔ 不在 sync-to-server.sh 的 SYNC_PATHS 里，结构上到不了 .51（design D10）
├── logsetup.py                         ← 【本章唯一的新模块】自建，⛔ 不 import app.*
│   ├── compute_redacted_text(text)                 纯函数：脱敏的全部语义在这里
│   ├── class RedactionFilter(logging.Filter)       挂包级 logger **与每个 handler**（见下 2）
│   ├── class RedactingFormatter(logging.Formatter) 兜 traceback（Filter 看不到它）
│   ├── setup_logging(...) -> LoggingStatus         进程启动调一次，幂等
│   ├── teardown_logging()                          只给测试收尾用
│   └── logging_status() -> LoggingStatus           降级事实可被读到
├── __main__.py                         ← 【只加两行】一行 import + main() 首句 setup_logging()
│                                          ⛔ 不动任何既有行
└── tests/
    ├── conftest.py                     ← 【新增】autouse：把日志目录顶到 tmp_path（见下 5）
    ├── test_liaison_log_redaction.py   ← 脱敏正例 / 反例 / 子 logger / traceback
    └── test_liaison_logsetup.py        ← 轮转有界 / 降级不崩 / 幂等 / 接线与结构守卫
```

开工前必须先读懂的**七条判断**，改动前逐条对照：

**1. 🚨 本模块禁止写 `with` —— 这是本目录最容易踩、且踩了会在一个毫不相干的测试里变红的坑。**
第 2 章的 `tools/liaison/tests/test_liaison_effects.py::test_no_second_transaction_manager_in_source` 会扫 `tools/liaison/**/*.py`（`tests` 目录除外），把**任何** `with <名字|属性|调用>:` 判为「隐式提交事务边界」违规——它认的是 `ast.With` 的形状，不看上下文管理器到底是不是数据库连接。TD-18 之后 `open` / `os.fdopen` / `io.open` / `contextlib.suppress` / `tempfile.*` 这几个 callee 被放行了，但 **⛔ 不要依赖那份白名单**：本模块一律用 `pathlib.Path.write_text()` / `unlink()`，一个 `with` 都不写。Task 4 有一条 AST 断言把这条钉死。
⛔ **不许为了写 `with` 去放宽那个扫描器或往白名单里加本模块。** 它守的是工程铁律 1 唯一的静态判据。测试代码里可以照常用 `with`（扫描器跳过 `tests` 目录）。

**2. 🚨 「脱敏 Filter 挂在包级 logger」这句话按字面实现是**失效的**——必须同时挂到每个 handler 上。**
这是本章唯一一个「不报错、无症状、但脱敏整个不生效」的陷阱，务必先看懂再动手：
Python 的 `logging` 里，`Logger.handle()` 只对**记录发起的那个 logger** 调用 `self.filter(record)`。子 logger 的记录会**沿祖先链找 handler**，但**不会**再跑祖先 logger 的 filter。于是：

```
logging.getLogger("tools.liaison").warning("13812345678")        → 被脱敏 ✅
logging.getLogger("tools.liaison.inbound").warning("13812345678") → 明文落盘 ❌
```

而本服务**每一个模块**都是 `logging.getLogger(__name__)`（`tools.liaison.inbound` / `tools.liaison.session` / `tools.liaison.alerts` …），也就是说按字面实现的话，**真正会打印个人信息的那些行一条都过不了 Filter**。
做法（`app/observability/logging_config.py` 的 `dictConfig` 也是这么配的——filters 挂在 handlers 上，不挂在 logger 上）：
- 包级 logger `tools.liaison` 上挂一个 `RedactionFilter`（满足 opener 约束 2 的字面要求，且覆盖直接用包级 logger 打的行）；
- **每个 handler 上再各挂一个** `RedactionFilter`；
- 每个 handler 的 formatter 用 `RedactingFormatter`。

**三层各自不可替代，这是实测出来的，⛔ 不许"简化"掉任何一层。** 计划成稿时在临时副本里做过三次拆层证伪（`venv/bin/python 3.14.6` / pytest 8.3.4），结果如下——这张表就是本章的 reviewer 判据：

| 拆掉哪一层 | 变红的用例 | 说明 |
|---|---|---|
| 只摘 handler 上的 `RedactionFilter`（留 Formatter） | 仅 `test_filter_neutralises_a_broken_format_string_...` | handler filter **唯一不可替代**的作用是中和坏格式串——`getMessage()` 抛异常时 Formatter 自己也会抛，落进 stdlib `Handler.handleError()`，那条路径把 `record.msg`/`args` **原文**写进 stderr，绕开全部防线 |
| 只把 `RedactingFormatter` 换成裸 `logging.Formatter`（留 filter） | 仅 `test_exception_tracebacks_are_redacted` | Formatter **唯一不可替代**的作用是兜 traceback——`Filter` 只看得到 `record.getMessage()`，看不到 `exc_text` |
| **两层都摘**（只剩包级 logger 上那一个 filter） | `test_child_logger_records_are_redacted_too` + 上面两条，共 3 条 | 这才是「只挂包级 logger」的真实后果：子 logger 的普通消息**明文落盘** |

⚠️ **注意第一行**：单摘 handler filter 时子 logger 那条用例**不会**红——`RedactingFormatter` 顺手把它兜住了。所以 Task 2 的证伪步骤必须**两层一起摘**（第三行），⛔ 不要只摘 filter 就以为证伪过了。
Task 2 的 `test_child_logger_records_are_redacted_too` 是这条的证伪测试，⛔ 不许删。

**3. `msgid` / `thread_id` 的取值受保护，做法是「先切出保护段，再在保护段之外脱敏」。**
opener 约束 2 逐字：⛔ 不脱敏 `msgid`、`thread_id`（排障要用）。但这两个键的**取值恰恰可能长得像个人信息**——`thread_id` 私聊时取的就是 `userid`，企微后台允许把 userid 配成手机号；`msgid` 是不透明串，含 11 位数字并非不可能。天真的实现（先跑正则、再想办法救回来）必然在某天把一个 `thread_id` 打成 `<redacted:phone>`，然后没人能把那条日志和库里的行对上。
所以顺序反过来：`compute_redacted_text` 先用 `_PROTECTED_SPAN_RE` 把 `msgid=… ` / `thread_id=…` 这样的**整段**切出来原样保留，只对保护段**之间**的文本跑脱敏正则。这样「保护」是结构性的，不依赖正则之间谁先谁后。
⚠️ 代价明写在这里，reviewer 不必再猜：**如果某个 `thread_id` 的取值真的是一个手机号，它会以明文留在日志里**。这是 opener 约束 2 与合规红线之间被显式选定的一侧——排障可行性优先，且该取值本来就作为主键明文存在 `liaison_message.thread_id` 列里，日志不是新增的泄露面。⛔ 不许因此把 `thread_id` 也纳入脱敏（那会让归档链路彻底不可追）。
⛔ `sender_userid` **不进**保护名单：它不是排障必需的键（`msgid` 已足够定位），把它保护起来只会白白扩大明文面。

**4. 「有界容量」= `RotatingFileHandler(maxBytes, backupCount)`，⛔ 不自己写轮转。**
`app/observability/handlers.py` 的 `DailyRotatingFileHandler` 是**按天**轮转 + 大小兜底 + 按 mtime 清理，那套复杂度是为了满足产品服务「按天查日志」的排障习惯，本服务不需要。这里借的是**做法**（落到有界的轮转文件），⛔ 不是抄那个类——抄过来就得连它的 `rotation_filename` 补丁、`purge_expired_logs`、以及「Windows 上第二个进程持有句柄会轮转失败」那一整串注意事项一起养着。
标准库 `logging.handlers.RotatingFileHandler` 的语义正好是本章要的上界：磁盘占用 ≤ `maxBytes × (backupCount + 1)`，超出的历史单元由它自己删。默认 5 MiB × (5 + 1) = 30 MiB。

**5. 必须新增 `tools/liaison/tests/conftest.py`，否则跑一次 pytest 就会在仓库里拉出真实日志文件。**
`setup_logging()` 一旦接进 `main()`，现有的 6 条 `test_main_*` 用例（它们都真的调 `liaison_main.main()`）就会在 `data/liaison/logs/` 下写出真实文件。`data/` 虽然在 `.gitignore` 里不会被提交，但**开发机上跑一次测试就落一份含真实内容的日志**，这与「日志是个人信息的第二份拷贝」（`.gitignore:17` 的原话）直接冲突。
处置：新增 `tools/liaison/tests/conftest.py`，autouse fixture 把 `HR_LIAISON_LOG_DIR` 顶到 `tmp_path`，并在 teardown 里 `teardown_logging()` 把 handler 摘掉关掉（否则上一条用例的 handler 会攥着一个已被删除的 tmp 目录）。
⚠️ **并发处置（⛔ 不需要任何人当场回答）**：若开工时 `tools/liaison/tests/conftest.py` 已被别的泳道创建，**在其中追加本 fixture，⛔ 不覆盖整个文件**。

**6. `setup_logging()` 读的是**进程环境**，读不到 `.env`——这是接线位置决定的，不是漏了。**
opener 约束 3 逐字：只在 `main()` **第一行**加 `setup_logging()`，⛔ 不动既有行。而 `main()` 现在的第一句是 `load_dotenv_into_environ(resolve_dotenv_path())`——`setup_logging()` 排在它**前面**，因此 `.env` 里写的 `HR_LIAISON_LOG_DIR` 对它无效。
这是刻意接受的：本服务的部署形态是 launchd，环境变量由 plist 的 `EnvironmentVariables` 给（8.3 的范围），进程环境本来就是权威来源；而排在最前面换来的是**凭据校验失败那条路径也已经有日志**——`main()` 里 `MissingCredentialsError` 分支会 `print(..., file=sys.stderr)` 然后退出，日志晚一步装配就意味着启动期最常见的那类失败完全没有落盘证据。
⛔ 不许为了让 `.env` 生效而把 `setup_logging()` 挪到 dotenv 之后，也 ⛔ 不许改 `load_dotenv_into_environ` 的位置。这条限制写进 `logsetup.setup_logging` 的 docstring，供 reviewer 核对。

**7. `setup_logging` 不叫 `effect_setup_logging`——铁律 2 的命名规则在这里不适用，理由写死在 docstring 里。**
铁律 2 的 `compute_*` / `effect_*` 是给 **L4 编排层的图节点**定的命名法，用来让「哪些动作有副作用、需要幂等键」在节点名上一眼可见。`setup_logging` 是进程 bootstrap，不在任何图里、不落 `effect_log`、也不需要幂等键（它的幂等由「先摘旧 handler 再挂新的」在实现里保证，是覆写语义）。opener 约束 3 逐字写的就是 `setup_logging()`，照此命名。
纯函数 `compute_redacted_text` 按铁律 2 加 `compute_` 前缀——它确实是纯函数，这个前缀在这里是有信息量的。

**Tech Stack:** Python 3.14（根 `pyproject.toml` 钉死 `>=3.14,<3.15`；本机 `venv/` 实测 3.14.6）· 纯标准库（`logging` / `logging.handlers` / `os` / `pathlib` / `re` / `sys` / `dataclasses` / `ast`）· pytest 8.3.4 · ⛔ 零新增依赖（`tools/liaison/requirements.txt` 一个字不改）

---

## Global Constraints

以下逐字复制自 `CLAUDE.md`「工程铁律」「合规红线」「部署约束」、`design.md` 与本交付单元 opener。**reviewer 以此为注意力透镜。**

### 来自 CLAUDE.md 工程铁律（逐字）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
   *（本章 ⛔ 一行库都不写、⛔ 不新增任何 `effect_*`、⛔ 不碰 `storage/`。日志装配是进程 bootstrap，不进 `effect_log`。这条在本章的可执行形式是 Architecture 第 1 条：⛔ 不写 `with`，别把事务扫描器踩红。）*
2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。
   *（本章纯函数：`compute_redacted_text`——⛔ 不读环境、不写文件、不打日志。有副作用的只有 `setup_logging` / `teardown_logging`，命名例外的理由见 Architecture 第 7 条。⛔ 不许出现「既算又写」的混合函数。）*
5. **`temperature=0`；模型版本优先显式锁定**，禁止 `latest` 类别名。
   供应商不提供带版本号快照时（如 DeepSeek 公开 API 只有 `deepseek-chat` 这类会漂移的别名），**必须从 API 响应里取回实际的 `model` 字段并持久化**——配置里写的名字不算数，响应返回的才算。
   *（本章不调任何模型。这条在这里的等价物：⛔ 不新增依赖，`tools/liaison/requirements.txt` 一个字不改，纯标准库。）*
6. **企微回调先落库再处理**：只推一次、5 秒无响应即丢弃。回调接口只做签名校验 + 落库 + 返回 200。
   *（本服务走 WS 长连接，无 HTTP 回调端点；本章不新建任何入站端口。）*

### 来自 CLAUDE.md 合规红线（逐字）

- **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。审计断言：`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。
- **禁止人脸/表情分析**（《人脸识别技术应用安全管理办法》2025-06-01 施行）。声学情绪信号（语速/停顿/静默）只展示给面试官，不进 `criterion_score`。
- **模型全部走境内**，简历数据不出境。
- 候选人入口一律用一次性邀请链接，避免被认定"向境内公众提供"。

**本章的合规落点（这是本章存在的主要理由，reviewer 请重点看这条）**：日志是个人信息的**第二份拷贝**（`.gitignore:17` 逐字）。本章必须让「手机号 / 邮箱 / 身份证号 / 凭据取值以明文进日志」这件事在结构上做不到，而不是靠每个调用点自觉。判据：Task 2 的正例断言在**子 logger** 与 **traceback** 两条路径上都成立——这两条正是最容易漏、且漏了没有症状的路径。
⚠️ 唯一被显式豁免的是 `msgid` / `thread_id` 的取值（Architecture 第 3 条已写明理由与代价）。⛔ 这个豁免 ⛔ 不许扩大到任何其他键。

### 来自 CLAUDE.md 部署约束（逐字）

4. **目标服务器是 Windows，没有 Docker**。部署形态 = Python venv + Windows 计划任务（SYSTEM 账户 + AtStartup + 失败重启 3 次）+ 防火墙规则 + scp 推送。不要引入容器。
   *（本服务 ⛔ 永不部署 `.51`，跑在 Mac 上由 launchd 守护（design D12）。本章因此**必须**保留一份 stderr 输出——launchd 会把 stdout/stderr 收进 plist 指定的文件，那是「日志文件通道自己坏掉时」唯一还能说话的通道。）*

### 来自 design.md（逐字）

- **D7**｜`runtime-observability`：**借用做法，不受其要求约束**。它的要求文本针对产品服务的 HTTP 请求链路（request id 串联等）。本服务是常驻长连接进程，没有请求边界。借用的做法是：日志落到有界的轮转文件、个人信息脱敏、留存期有上限。
- **D10**｜代码落 **`tools/liaison/`**。⛔ 不落 `app/`（产品交付物），⛔ 也不落 `scripts/`——`scripts` 与 `app` **都在** `sync-to-server.sh` 的 `SYNC_PATHS` 白名单里。依赖落 `tools/liaison/requirements.txt`，⛔ 不进根 `requirements.txt`。
- **D13**｜`HR_LIAISON_RETENTION_DAYS` 默认 **180**。选 180 天而不是与日志的 30 天对齐：日志是运行证据，归档是**工作材料**。

### 来自本交付单元 opener（逐字，五条）

1. 落点 `tools/liaison/logsetup.py`（或计划等价命名）：`RotatingFileHandler` 按大小轮转 + `backupCount` 有界；日志目录 `data/liaison/logs/`（gitignore 内），可由环境变量覆盖；stderr 同时保留一份（launchd 会收）
2. 脱敏 Filter 挂在包级 logger：手机号、邮箱、身份证样式、`HR_LIAISON_*` / `LLM_*` 取值样式一律打码；⛔ 不脱敏 `msgid`、`thread_id`（排障要用）
3. 接线只在 `__main__.py` 的 `main()` 第一行加 `setup_logging()` 一处，⛔ 不动既有行（留存泳道在文件末尾加子命令，各占一头）；⛔ 不改其它模块的 logger 调用；⛔ 不改 `whitelist.py`（技术债泳道在改其日志）、`storage/*`、`tools/liaison/README.md`
4. 单测：轮转触发后文件数 ≤ `backupCount + 1`；脱敏正例一条、反例一条（`thread_id` 不被打码）；setup 幂等（调两次不重复挂 handler）
5. D10：`tools/liaison/` 内；⛔ 不碰 `app/`、`scripts/`；TD-18：避开 `with <Call>:`，⛔ 不改扫描器

### 本章的六条"不做"

1. ⛔ **不 import `app.*`**——`app/observability/` 只是**参照读物**，做法照抄、代码自建。从它 import 会把 D10 的隔离从「结构上不可能」降级成「记得别用」。
2. ⛔ **不改任何既有模块的 `logger` 调用**。本章不动 `inbound.py` / `session.py` / `alerts.py` / `archive.py` / `queue*.py` / `whitelist.py` 里的任何一行。脱敏靠 handler 层拦截，⛔ 不靠改调用点。
3. ⛔ **不碰 `storage/`、不碰 `app/`、不碰 `scripts/`、不碰 `tools/liaison/README.md`、不碰 `tools/liaison/requirements.txt`**。
4. ⛔ **不改 `tests/test_liaison_effects.py` 的事务扫描器**（技术债泳道刚还完 TD-18，本章往它上面叠改动必冲突）。
5. ⛔ **不实现按天数的日志留存清理**（见下方「未尽项」——已登记，不在本章闭合）。
6. ⛔ **不动 `.gitignore`**：`data/` 已在第 11 行被整目录忽略，`git check-ignore -v data/liaison/logs/liaison.log` 实测命中 `.gitignore:11`。再加一条只会让同一件事有两处真源。

---

## 前置状态与冲突处置（⚠️ controller 开工前必读）

**并行泳道正在改同一个包。** 本计划触碰的文件：

| 文件 | 动作 | 撞车风险 |
|---|---|---|
| `tools/liaison/logsetup.py` | 新建 | 无（本章独占） |
| `tools/liaison/tests/test_liaison_log_redaction.py` | 新建 | 无 |
| `tools/liaison/tests/test_liaison_logsetup.py` | 新建 | 无 |
| `tools/liaison/tests/conftest.py` | 新建 | **有**（别的泳道可能也要建） |
| `tools/liaison/__main__.py` | **只加两行** | **有**（留存泳道在文件末尾加子命令） |

**冲突处置已定，⛔ 不需要任何人当场回答：**

- `__main__.py`：本章只在**顶部 import 区加一行**、在 `main()` 的**首句位置加一行**。留存泳道加的子命令在**文件末尾**，两处物理上不相邻，`git rebase` 可自动合并。⛔ 本章不重排 import、不合并到既有的 `from tools.liaison import alerts, session, session_client` 那一行（改那行就是「动既有行」，且会和别人的改动落在同一行上）。**新起一行 `from tools.liaison import logsetup`。**
- `tools/liaison/tests/conftest.py`：**若已存在，追加 fixture，⛔ 不覆盖。**
- push 被拒 → `git pull --rebase --autostash origin main` 后重试，最多 3 次。⛔ 不在 commit 前 pull。
- 只 `git add` 本计划列出的 5 个路径。⛔ 禁止 `git add -A` / `git add .` / `git commit -a`。

**⛔ 本章不需要 `data/liaison.db` 的任何 schema 变更**，不写迁移，不碰 `storage/schema.py`。

---

## ⏸ 未尽项（已登记，⛔ 不在本章闭合，⛔ 不许标成做完了）

**按天数的日志留存清理未实现。** design D7 借用的三条做法是「有界轮转文件、个人信息脱敏、**留存期有上限**」，opener 给本章的范围只写了前两条（「轮转 + 有界容量 + 个人信息脱敏」）。本章按 opener 的范围做，**不擅自扩大**。

- 现状下**磁盘占用是有上界的**（`maxBytes × (backupCount + 1)`，默认 30 MiB），所以 `runtime-observability`「日志占用的存储空间 MUST 有明确上界」这条是满足的；
- **不满足的是「超过留存期的日志 MUST 被清理」的时间维度**：低流量时一份含个人信息的历史日志可以躺很久。
- **建议的收口位置**：8.1 的留存清理任务（`HR_LIAISON_RETENTION_DAYS`）里顺带加一条按 mtime 清理 `data/liaison/logs/*.log.*` 的动作，用**独立的** `HR_LIAISON_LOG_RETENTION_DAYS`（默认 30，对齐 D13 正文里「日志的 30 天」）——⛔ 不要复用 180 天那个变量，D13 明写了两者刻意不对齐。
- ⛔ 本章 ⛔ 不实现它，也 ⛔ 不因为它判本章失败。

---

## Spec Requirement → Task 对照

输入 spec：`openspec/specs/runtime-observability/spec.md`（**借做法**，D7）。WBS：`openspec/changes/hr-wecom-aibot-liaison/tasks.md` 第 8 章 8.4 一条。

| Requirement | 本章处置 | Task |
|---|---|---|
| 运行日志必须持久化且容量有界 · 无控制台环境下启动 | 文件 handler + stderr handler 各一份，launchd 两边都收得到 | Task 3 |
| 运行日志必须持久化且容量有界 · 日志量超过配置上限 | `RotatingFileHandler(maxBytes, backupCount)`，文件数 ≤ `backupCount + 1` | Task 3 |
| 运行日志必须持久化且容量有界 · 日志写入位置不可用 | 探测不可写 → 降级为仅 stderr，**不崩溃**，且降级事实记 ERROR + 进 `LoggingStatus` | Task 3 |
| 日志内容受个人信息脱敏约束 · 受控字段随对象被整体记录 | `compute_redacted_text` + Filter（logger 与 handler 双挂）+ `RedactingFormatter`（兜 traceback） | Task 1 / Task 2 |
| 日志内容受个人信息脱敏约束 · 新增字段的默认归属 | 按**值的形态**判定（手机/邮箱/身份证/凭据），⛔ 不按键名白名单——新增字段自动被覆盖，不需要有人去登记 | Task 1 |
| 一次请求的日志可由单一标识串联 | ⛔ **不适用**（D7 逐字：本服务是常驻长连接进程，没有请求边界）。等价物是 `msgid` / `thread_id` 被**保护为明文**，Task 1 有反例断言 | Task 1（反例） |
| 服务端错误必须留下可定位的证据 | ⛔ **不适用**（同上，无请求标识）。本章保证的是「已被记录为错误的内部失败 MUST 出现在持久化的日志中」——ERROR 级同样落文件 | Task 3 |
| 日志留存期有上限且与业务数据分离 | **部分**：容量上界满足；天数维度**未做，已登记**（见上方「未尽项」） | ⏸ 登记 |

---

### Task 1: 脱敏内核——纯函数 `compute_redacted_text` 与保护段优先

**Files:**
- Create: `tools/liaison/logsetup.py`（本 Task 只写模块头 + 脱敏纯函数部分）
- Create: `tools/liaison/tests/test_liaison_log_redaction.py`（本 Task 只写纯函数用例）

**Interfaces:**

```python
PROTECTED_KEYS: tuple[str, ...]                 # ("msgid", "thread_id")
SECRET_MASK: str                                # "<redacted:secret>"
PHONE_MASK: str                                 # "<redacted:phone>"
EMAIL_MASK: str                                 # "<redacted:email>"
IDCARD_MASK: str                                # "<redacted:idcard>"

def compute_redacted_text(text: str) -> str: ...
```

**Step 1:** 写 `tools/liaison/logsetup.py` 的模块头与脱敏部分，内容如下（**完整文件，逐字**）：

```python
"""HR 值守通道的日志装配：轮转 + 有界容量 + 个人信息脱敏（tasks.md 8.4）。

**借用 `runtime-observability` 的做法，⛔ 不 import `app.*`**（design D7 + D10）。
`app/observability/{logging_config,redaction,handlers}.py` 是本模块的**参照读物**：
做法照抄（有界轮转文件、脱敏、不可写时降级不崩），代码自建。一旦从那边 import，
`tools/` 与产品交付物之间就出现一条编译期依赖，D10 的隔离会从「结构上不可能被
同步到 .51」退化成「记得别用」。

⛔ **本模块禁止写 `with`。** 第 2 章的
`tools/liaison/tests/test_liaison_effects.py::test_no_second_transaction_manager_in_source`
把 `tools/liaison/` 下非测试代码里**任何** `with <名字|属性|调用>:` 判为「隐式提交
事务边界」违规——它认的是 `ast.With` 的形状，不看上下文管理器是不是数据库连接。
TD-18 之后 `open()` 等少数 callee 被放行，但 ⛔ 不要依赖那份白名单：本模块一律用
`pathlib.Path.write_text()` / `unlink()`。`test_logsetup_module_never_uses_a_with_statement`
把这条钉死。

⚠️ **脱敏为什么必须挂到 handler 上、而不只是包级 logger 上**：`Logger.handle()` 只对
**记录发起的那个 logger** 跑 filter；子 logger 的记录沿祖先链找 **handler**，⛔ 不会
再跑祖先 logger 的 filter。本服务每个模块都是 `logging.getLogger(__name__)`
（`tools.liaison.inbound` 等），所以只挂包级 logger 等于**对真正会打印个人信息的那些
行完全失明**，且这个失明不报错、无症状。见 `setup_logging` 的实现与
`test_child_logger_records_are_redacted_too` 的证伪。
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import pathlib
import re
import sys
from dataclasses import dataclass, field

#: 本服务全部模块的 logger 都挂在这个包名下（每个模块 `getLogger(__name__)`）。
PACKAGE_LOGGER_NAME = "tools.liaison"
LOG_FILENAME = "liaison.log"
#: ⛔ 不放 `%(request_id)s`：本服务是常驻长连接进程，没有请求边界（design D7）。
LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

#: tools/liaison/logsetup.py → parents[0]=liaison, [1]=tools, [2]=仓库根
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
#: `data/` 已被 `.gitignore:11` 整目录忽略（实测 `git check-ignore -v` 命中该行），
#: 日志不会误入版本管理。⛔ 不要再往 `.gitignore` 加一条——同一件事两处真源。
DEFAULT_LOG_DIR = REPO_ROOT / "data" / "liaison" / "logs"

LOG_DIR_ENV = "HR_LIAISON_LOG_DIR"
LOG_LEVEL_ENV = "HR_LIAISON_LOG_LEVEL"
LOG_MAX_BYTES_ENV = "HR_LIAISON_LOG_MAX_BYTES"
LOG_BACKUP_COUNT_ENV = "HR_LIAISON_LOG_BACKUP_COUNT"

DEFAULT_LEVEL = "INFO"
#: 磁盘占用上界 = DEFAULT_MAX_BYTES × (DEFAULT_BACKUP_COUNT + 1) = 30 MiB。
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5

#: 打在 handler 实例上的标记，让 `setup_logging` 只摘自己挂的那些 handler。
#: ⛔ 不要改成「清空 logger.handlers」——那会连别人（如 pytest 的插件）挂上去的
#: handler 一起摘掉，而这种破坏是静默的。
MANAGED_HANDLER_ATTR = "_hr_liaison_managed"

SECRET_MASK = "<redacted:secret>"
PHONE_MASK = "<redacted:phone>"
EMAIL_MASK = "<redacted:email>"
IDCARD_MASK = "<redacted:idcard>"

#: ⛔ **不脱敏**的键（opener 约束 2 逐字：排障要用）。
#:
#: ⚠️ 代价是明写的，⛔ 不要在实现里偷偷补救：`thread_id` 私聊时取的就是 `userid`，
#: 企微后台允许把 userid 配成手机号；真出现这种取值，它会以明文留在日志里。这是
#: 被显式选定的一侧——该取值本来就作为主键明文存在 `liaison_message.thread_id`
#: 列里，日志不构成新增的泄露面，而「日志里的 thread_id 被打成 <redacted:phone>」
#: 会让归档链路彻底不可追。
#:
#: ⛔ `sender_userid` **不进**本名单：定位一条消息有 `msgid` 就够了，把它保护起来
#: 只是白白扩大明文面。⛔ 往本名单加键之前先回答「不加它，排障具体卡在哪一步」。
PROTECTED_KEYS: tuple[str, ...] = ("msgid", "thread_id")

#: 保护段：`msgid=…` / `thread_id=…`（也认 `: `）整段原样保留。
#: 值的四种形态按顺序尝试：单引号串、双引号串、裸值（吃到下一个空白/逗号/分号/
#: 右括号为止）、空值（`thread_id=` 后面什么都没有）。⛔ 不处理嵌套结构——日志里
#: 这两个键从来都是标量。
_PROTECTED_SPAN_RE = re.compile(
    r"\b(?:" + "|".join(PROTECTED_KEYS) + r")\b\s*[=:]\s*"
    r"(?:'[^']*'|\"[^\"]*\"|[^\s,;)\]}]*)"
)

#: 凭据取值：键名保留（排障要知道是哪一项没配好），取值一律打码。
#: 覆盖 `HR_LIAISON_*` 与 `LLM_*` 两族（opener 约束 2 逐字）。
#: ⚠️ 这会连 `HR_LIAISON_LOG_DIR=/x/y` 的路径也一起打掉——刻意如此：按前缀一刀切
#: 才不需要维护一份「哪些 HR_LIAISON_* 是秘密」的名单，而那种名单必然漏。
_CREDENTIAL_RE = re.compile(
    r"\b((?:HR_LIAISON|LLM)_[A-Z0-9_]+)(\s*[=:]\s*)"
    r"(?:'[^']*'|\"[^\"]*\"|[^\s,;)\]}]+)"
)

#: 按**值的形态**判定，⛔ 不按键名白名单——`runtime-observability`「新增字段的默认
#: 归属」要的就是「未声明即受控」：业务对象新增一个字段，只要它的取值长得像手机号
#: /邮箱/身份证，就自动被覆盖，⛔ 不需要有人记得去登记。
#:
#: **顺序有意义**：邮箱在最前（邮箱本地部分可能含 11 位数字，先整体吃掉才不会被
#: 手机号规则咬掉一半）；身份证在手机号之前（18 位号的前后有 `(?<!\d)`/`(?!\d)`
#: 护栏，本来就不会被手机号规则误伤，但顺序写死省得后人推理）。
#:
#: ⛔ **刻意不做 15 位旧版身份证**：15 位纯数字与时间戳、SDK 序号、字节数撞得太厉害，
#: 加进来会把大量无害数字打成 `<redacted:idcard>`，让日志读不懂。旧版身份证在本服务
#: 的场景（在职员工与候选人）里已基本绝迹。⛔ 不要"顺手补上"。
_VALUE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), EMAIL_MASK),
    (
        re.compile(
            r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])"
            r"(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![\dXx])"
        ),
        IDCARD_MASK,
    ),
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), PHONE_MASK),
)


def _redact_free_span(span: str) -> str:
    """对**保护段之外**的一段文本做脱敏。⛔ 不要单独调它——保护段的切分在
    `compute_redacted_text` 里，绕过那一步就会把 `thread_id` 一起打码。"""
    if not span:
        return span
    span = _CREDENTIAL_RE.sub(r"\1\2" + SECRET_MASK, span)
    for pattern, mask in _VALUE_PATTERNS:
        span = pattern.sub(mask, span)
    return span


def compute_redacted_text(text: str) -> str:
    """脱敏的全部语义。**纯函数**（铁律 2）：⛔ 不读环境、不写文件、不打日志。

    **顺序是结构性的，⛔ 不许调换**：先把 `msgid=…` / `thread_id=…` 整段切出来
    原样保留，只对它们**之间**的文本跑脱敏正则。反过来做（先跑正则、再想办法把
    误伤的键救回来）必然在某天把一个长得像手机号的 `thread_id` 打成
    `<redacted:phone>`，那条日志就再也和库里的行对不上了。

    **幂等**：输出再跑一遍本函数结果不变（掩码串里不含邮箱/手机号/身份证形态），
    因此 Filter 与 Formatter 两层都跑一遍是安全的。
    """
    if not text:
        return text
    pieces: list[str] = []
    cursor = 0
    for match in _PROTECTED_SPAN_RE.finditer(text):
        pieces.append(_redact_free_span(text[cursor : match.start()]))
        pieces.append(match.group(0))
        cursor = match.end()
    pieces.append(_redact_free_span(text[cursor:]))
    return "".join(pieces)
```

**Step 2:** 写 `tools/liaison/tests/test_liaison_log_redaction.py` 的纯函数部分：

```python
"""8.4 · 脱敏内核。正例（打码）与反例（`thread_id` / `msgid` 不许被打码）。

⚠️ 本文件里的"凭据"全是明显的假值。⛔ 不要写成行首赋值形态
（`HR_LIAISON_BOT_SECRET=xxx`）——`test_liaison_no_secrets_in_vcs.py` 会扫受版本
管理的**全部**文件，行首赋值会被判成真凭据入库。
"""

from __future__ import annotations

from tools.liaison import logsetup


def test_redacts_phone_email_idcard_and_credential_values():
    """正例：四种形态一个都不许留明文，但键名要留着（排障得知道是哪一项）。"""
    text = (
        "候选人邮箱 zhang.san@example.com 手机 13812345678 "
        "身份证 320102199001011234 ；启动参数 HR_LIAISON_BOT_SECRET: fake-value-1"
    )
    out = logsetup.compute_redacted_text(text)

    assert "zhang.san@example.com" not in out
    assert "13812345678" not in out
    assert "320102199001011234" not in out
    assert "fake-value-1" not in out

    assert logsetup.EMAIL_MASK in out
    assert logsetup.PHONE_MASK in out
    assert logsetup.IDCARD_MASK in out
    assert logsetup.SECRET_MASK in out
    assert "HR_LIAISON_BOT_SECRET" in out, "键名必须留着，⛔ 不许连键一起打掉"


def test_does_not_redact_msgid_and_thread_id_even_when_they_look_like_personal_data():
    """反例（opener 约束 2 逐字）：⛔ 不脱敏 msgid、thread_id。

    刻意把两个取值都写成手机号形态——这正是天真实现会踩的那颗雷：脱敏正则
    先跑一遍，`thread_id` 变成 `<redacted:phone>`，日志从此和 `liaison_message`
    对不上，而且**不报错**。
    """
    text = "thread_id=13812345678 msgid=13900000000 收到一条消息"
    assert logsetup.compute_redacted_text(text) == text


def test_protects_the_keys_but_still_redacts_the_rest_of_the_same_line():
    """保护段是**段**，不是整行豁免：同一行里键之外的手机号照样要打掉。"""
    text = "thread_id=13812345678 联系方式 13900001111"
    out = logsetup.compute_redacted_text(text)
    assert "thread_id=13812345678" in out
    assert "13900001111" not in out
    assert logsetup.PHONE_MASK in out


def test_protects_quoted_and_empty_key_values():
    """`msgid='...'` 与 `thread_id=`（空值）两种渲染形态都要认。"""
    text = "msgid='13800001111' thread_id= 邮件 a.b@example.com"
    out = logsetup.compute_redacted_text(text)
    assert "msgid='13800001111'" in out
    assert "a.b@example.com" not in out


def test_redaction_is_idempotent():
    """跑两遍结果不变——Filter 与 Formatter 两层都会跑，不幂等就会越洗越花。"""
    text = "邮箱 a.b@example.com 手机 13812345678 LLM_API_KEY=fake-value-2"
    once = logsetup.compute_redacted_text(text)
    assert logsetup.compute_redacted_text(once) == once


def test_empty_text_is_returned_unchanged():
    assert logsetup.compute_redacted_text("") == ""
```

**Step 3:** 跑本 Task 的测试：

```bash
cd /Users/paulshao/Projects/HumanResource
venv/bin/python -m pytest tools/liaison/tests/test_liaison_log_redaction.py -q
```

预期输出：`6 passed`。

**Verification:**
- [ ] `venv/bin/python -m pytest tools/liaison/tests/test_liaison_log_redaction.py -q` → `6 passed`
- [ ] `venv/bin/python -c "import ast,pathlib;t=ast.parse(pathlib.Path('tools/liaison/logsetup.py').read_text());print([n for n in ast.walk(t) if isinstance(n,(ast.With,ast.AsyncWith))])"` → `[]`
- [ ] `grep -n "^import app\|^from app" tools/liaison/logsetup.py` → 无输出

---

### Task 2: Filter 与 Formatter——两层挂载，并证伪「只挂包级 logger 就够了」

**Files:**
- Modify: `tools/liaison/logsetup.py`（追加 `RedactionFilter` / `RedactingFormatter`）
- Modify: `tools/liaison/tests/test_liaison_log_redaction.py`（追加两条路径用例）

**Interfaces:**

```python
class RedactionFilter(logging.Filter):
    RECORD_MARKER: str
    hits: int
    def filter(self, record: logging.LogRecord) -> bool: ...

class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str: ...
```

**Step 1:** 在 `tools/liaison/logsetup.py` 末尾追加：

```python
class RedactionFilter(logging.Filter):
    """在 record 层做脱敏。**必须挂到每个 handler 上**，见模块 docstring。

    同一条 record 会流经多个 handler（stderr + file）各 filter 一遍。用
    `record.hr_liaison_redacted` 做已处理标记，避免第二个 handler 把已经替换成
    掩码的文本重新扫一遍——不是为了正确性（`compute_redacted_text` 是幂等的），
    而是为了别把一次记录算成两次命中，也省掉一次全文正则。
    """

    RECORD_MARKER = "hr_liaison_redacted"

    def __init__(self, name: str = "") -> None:
        super().__init__(name)
        self.hits = 0

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, self.RECORD_MARKER, False):
            return True
        try:
            rendered = record.getMessage()
        except Exception as exc:
            # `getMessage()` 会因为 %-占位符与 args 数量对不上而抛异常。⛔ 绝不能
            # 把未脱敏的 record 原样放行——它会在 emit() 里再抛一次，落进 stdlib 的
            # `Handler.handleError()`，后者把 `record.msg` / `record.args` **原文**
            # 写进 sys.stderr，绕开 Filter 与 Formatter 两层防线。就地中和：丢掉
            # 原始负载，换一句不带内容的诊断信息。
            record.msg = (
                "[liaison-redaction] 日志格式化失败，原始 msg/args 已丢弃以避免明文外泄："
                f"logger={record.name} 位置={record.pathname}:{record.lineno} "
                f"错误类型={type(exc).__name__}"
            )
            record.args = ()
            setattr(record, self.RECORD_MARKER, True)
            return True

        redacted = compute_redacted_text(rendered)
        if redacted != rendered:
            # 替换后 args 必须清空：否则 `getMessage()` 会拿掩码文本再做一次
            # %-格式化，把 `<redacted:phone>` 里不存在的占位符和 args 对不上。
            record.msg = redacted
            record.args = ()
            self.hits += 1
        setattr(record, self.RECORD_MARKER, True)
        return True


class RedactingFormatter(logging.Formatter):
    """异常堆栈 ⛔ 不经过 `record.getMessage()`，Filter 看不到它。

    堆栈里会出现局部变量的 repr、以及抛异常那一行的**源码原文**（
    `raise ValueError("联系 someone@example.com")` 这种）。所以格式化之后再扫
    一遍最终文本，是 Filter 之外必须补的一刀。

    ⚠️ `super().format(record)` 会把**未脱敏**的 traceback 文本缓存进
    `record.exc_text`。本方法只对**返回值**做替换，⛔ 不回写那个缓存。今天这是
    安全的：本模块给所有 handler 配的都是本 Formatter，没有第二个 formatter 会
    读到它。⛔ 谁要给 `tools.liaison` 挂一个裸 `logging.Formatter`，先回来读这段。
    """

    def format(self, record: logging.LogRecord) -> str:
        return compute_redacted_text(super().format(record))
```

**Step 2:** 在 `tools/liaison/tests/test_liaison_log_redaction.py` 末尾追加。这几条是本 Task 的核心——它们证明「只挂包级 logger」是不够的。
⚠️ 下面代码块开头的 `import logging` / `import pytest` 两行**挪到文件顶部的 import 区**，⛔ 不要真的留在文件中间。

```python
import logging

import pytest


@pytest.fixture
def wired_logger(tmp_path):
    """把包级 logger 按生产方式装配好，返回日志文件路径。"""
    logsetup.setup_logging(log_dir=tmp_path)
    yield tmp_path / logsetup.LOG_FILENAME
    logsetup.teardown_logging()


def test_child_logger_records_are_redacted_too(wired_logger):
    """🔴 证伪「Filter 只挂包级 logger 就够了」——本服务真正打日志的全是子 logger。

    `Logger.handle()` 只对**发起记录的那个 logger** 跑 filter；子 logger 的记录
    沿祖先链找 **handler**，⛔ 不会再跑祖先 logger 的 filter。本服务每个模块都是
    `logging.getLogger(__name__)`，所以只挂包级 logger 等于对真正会打印个人信息
    的那些行完全失明，而且**不报错、无症状**。
    把 handler 上那层 filter 摘掉，这条必红。
    """
    logging.getLogger("tools.liaison.inbound").warning("候选人手机 13812345678")
    text = wired_logger.read_text(encoding="utf-8")
    assert "13812345678" not in text
    assert logsetup.PHONE_MASK in text


def test_exception_tracebacks_are_redacted(wired_logger):
    """Filter 看不到 traceback：抛异常那一行的**源码原文**也会被写进日志。"""
    logger = logging.getLogger("tools.liaison.archive")
    try:
        raise ValueError("联系 zhang.san@example.com 核对")
    except ValueError:
        logger.error("归档失败", exc_info=True)
    text = wired_logger.read_text(encoding="utf-8")
    assert "zhang.san@example.com" not in text
    assert logsetup.EMAIL_MASK in text


def test_thread_id_survives_the_whole_handler_chain(wired_logger):
    """端到端反例：走完 Filter + Formatter 两层，`thread_id` 仍是明文。"""
    logging.getLogger("tools.liaison.inbound").info(
        "已归档 thread_id=%s msgid=%s", "13812345678", "MSG-0001"
    )
    text = wired_logger.read_text(encoding="utf-8")
    assert "thread_id=13812345678" in text
    assert "msgid=MSG-0001" in text


def test_filter_neutralises_a_broken_format_string_instead_of_leaking_it(wired_logger):
    """占位符与 args 对不上时 ⛔ 不许把原文交给 stdlib 的 handleError()。"""
    logging.getLogger("tools.liaison.inbound").warning(
        "手机 %s 邮箱 %s", "13812345678"
    )
    text = wired_logger.read_text(encoding="utf-8")
    assert "13812345678" not in text
    assert "[liaison-redaction] 日志格式化失败" in text
```

**Step 3:** 跑：

```bash
cd /Users/paulshao/Projects/HumanResource
venv/bin/python -m pytest tools/liaison/tests/test_liaison_log_redaction.py -q
```

预期输出：`10 passed`。（本 Task 依赖 Task 3 的 `setup_logging` / `teardown_logging`——按 Task 3 先落 `logsetup.py` 的装配部分再跑本步；controller 若按顺序执行，本 Step 的验证与 Task 3 的 Step 3 合并跑一次即可。）

**Verification:**
- [ ] `venv/bin/python -m pytest tools/liaison/tests/test_liaison_log_redaction.py -q` → `10 passed`
- [ ] 🔴 **手工证伪一次，⛔ 不许跳**（判据见 Architecture 第 2 条的实测表）：**同时**把 `setup_logging` 里给 handler 挂 filter 的两行注释掉、并把 `formatter = RedactingFormatter(LOG_FORMAT)` 换成 `logging.Formatter(LOG_FORMAT)`，此时 `test_child_logger_records_are_redacted_too` / `test_exception_tracebacks_are_redacted` / `test_filter_neutralises_a_broken_format_string_...` **三条必须一起变红**；确认后原样改回，重跑必须全绿。
      ⚠️ ⛔ **不要只摘 filter 就算证伪过了**——那样只有坏格式串那一条会红，子 logger 那条被 Formatter 兜住，会给出「只挂包级 logger 也没问题」的错误结论（计划成稿时实测踩过这一脚）

---

### Task 3: `setup_logging`——轮转、有界、不可写时降级、幂等

**Files:**
- Modify: `tools/liaison/logsetup.py`（追加 `LoggingStatus` / `setup_logging` / `teardown_logging` / `logging_status`）
- Create: `tools/liaison/tests/test_liaison_logsetup.py`
- Create: `tools/liaison/tests/conftest.py`

**Interfaces:**

```python
@dataclass
class LoggingStatus:
    configured: bool
    degraded: bool
    reason: str | None
    log_file: str | None
    handlers: list[str]

def setup_logging(
    *,
    log_dir: str | os.PathLike[str] | None = None,
    level: str | None = None,
    max_bytes: int | None = None,
    backup_count: int | None = None,
) -> LoggingStatus: ...

def teardown_logging() -> None: ...
def logging_status() -> LoggingStatus: ...
```

**Step 1:** 在 `tools/liaison/logsetup.py` 末尾追加：

```python
@dataclass
class LoggingStatus:
    """日志子系统的当前状态。**降级不许是静默的**——`runtime-observability`
    逐字：「MUST NOT 静默降级为"什么都不记录"」。降级事实同时走两条路暴露：
    一条 ERROR 日志（走 stderr，launchd 收得到）+ 这个可读的状态对象。"""

    configured: bool = False
    degraded: bool = False
    reason: str | None = None
    log_file: str | None = None
    handlers: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "configured": self.configured,
            "degraded": self.degraded,
            "reason": self.reason,
            "log_file": self.log_file,
            "handlers": list(self.handlers),
        }


_status = LoggingStatus()


def logging_status() -> LoggingStatus:
    return _status


def _env_int(name: str, default: int, *, minimum: int) -> int:
    """环境变量取整。**取不到、非数字、越界一律回落到默认值，⛔ 不抛异常**——
    日志装配是进程的第一个动作，让它因为一个手抖的环境变量把服务打死，
    代价与收益差着数量级。"""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return value if value >= minimum else default


def _resolve_log_dir(explicit: "str | os.PathLike[str] | None") -> pathlib.Path:
    if explicit is not None:
        return pathlib.Path(explicit).expanduser()
    override = os.environ.get(LOG_DIR_ENV)
    if override and override.strip():
        return pathlib.Path(override.strip()).expanduser()
    return DEFAULT_LOG_DIR


def _probe_writable(directory: pathlib.Path) -> str | None:
    """返回 None 表示可写，否则返回不可写的原因（人类可读）。

    ⛔ 不用 `with open(...)`（模块 docstring 第 2 段）：`Path.write_text()` +
    `unlink()` 同样能探到权限/磁盘/父路径是文件这三类故障。"""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def _detach_managed_handlers(logger: logging.Logger) -> None:
    """只摘本模块自己挂的 handler。⛔ 不许写成 `logger.handlers.clear()`——
    那会把 pytest 插件、调试器之类挂上去的 handler 一起摘掉，静默破坏。"""
    for handler in list(logger.handlers):
        if getattr(handler, MANAGED_HANDLER_ATTR, False):
            logger.removeHandler(handler)
            handler.close()


def _detach_redaction_filters(logger: logging.Logger) -> None:
    for log_filter in list(logger.filters):
        if isinstance(log_filter, RedactionFilter):
            logger.removeFilter(log_filter)


def teardown_logging() -> None:
    """摘掉本模块挂的一切并复位状态。**只给测试收尾用**，⛔ 生产路径不调。

    没有它，上一条用例挂的 file handler 会一直攥着一个已被删除的 tmp 目录，
    下一条用例的日志就写进了一个看不见的地方。
    """
    global _status
    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    _detach_managed_handlers(logger)
    _detach_redaction_filters(logger)
    _status = LoggingStatus()


def setup_logging(
    *,
    log_dir: "str | os.PathLike[str] | None" = None,
    level: str | None = None,
    max_bytes: int | None = None,
    backup_count: int | None = None,
) -> LoggingStatus:
    """进程启动时调一次，装配 `tools.liaison` 包级 logger。**幂等**。

    ⚠️ **读的是进程环境，读不到 `.env`。** 本函数被接在 `__main__.main()` 的
    **第一句**，排在 `load_dotenv_into_environ(...)` **前面**（opener 约束 3 逐字：
    ⛔ 不动既有行）。这是刻意的：本服务由 launchd 守护，环境变量来自 plist 的
    `EnvironmentVariables`，进程环境本来就是权威来源；而排在最前面换来的是
    **凭据校验失败那条路径也已经有日志**——那是启动期最常见的一类失败。
    ⛔ 不许为了让 `.env` 生效把本调用挪到 dotenv 之后。

    ⚠️ **⛔ 不动 `logger.propagate`（保持 True）。** 置 False 会让 `caplog` 抓不到
    本包的记录，把第 1–7 章一批现存用例静默变哑（它们断言的是 `caplog.text`）。
    生产环境下 root 没有 handler，不会重复输出。

    命名：⛔ 不叫 `effect_setup_logging`。铁律 2 的 `compute_*`/`effect_*` 是给
    **L4 编排层的图节点**定的命名法（用来让「需要幂等键」一眼可见）；本函数是
    进程 bootstrap，不在任何图里、不落 `effect_log`、幂等由「先摘旧 handler 再挂
    新的」这个覆写语义保证。opener 约束 3 逐字写的就是 `setup_logging()`。

    不可写时**⛔ 不崩溃、⛔ 不阻断业务功能**：退回只有 stderr 的配置，把降级
    事实记进 ERROR 日志与 `LoggingStatus`。
    """
    global _status

    directory = _resolve_log_dir(log_dir)
    resolved_level = (level or os.environ.get(LOG_LEVEL_ENV) or DEFAULT_LEVEL).upper()
    resolved_max_bytes = (
        max_bytes
        if max_bytes is not None
        else _env_int(LOG_MAX_BYTES_ENV, DEFAULT_MAX_BYTES, minimum=1)
    )
    resolved_backup_count = (
        backup_count
        if backup_count is not None
        else _env_int(LOG_BACKUP_COUNT_ENV, DEFAULT_BACKUP_COUNT, minimum=0)
    )

    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    # 幂等的全部实现：先把上一次挂的摘干净，再挂新的。⛔ 不要改成「已配置就直接
    # 返回」——那会让改了环境变量之后的第二次调用**静默无效**。
    _detach_managed_handlers(logger)
    _detach_redaction_filters(logger)

    logger.setLevel(resolved_level)
    logger.addFilter(RedactionFilter())

    formatter = RedactingFormatter(LOG_FORMAT)

    # stderr 一份：launchd 的 `StandardErrorPath` 会收（部署约束 4 + design D12）。
    # 它同时是「文件通道自己坏掉时」唯一还能说话的通道，所以**先挂它**——降级那条
    # ERROR 日志要靠它出去。
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(RedactionFilter())
    stream_handler.setLevel(resolved_level)
    setattr(stream_handler, MANAGED_HANDLER_ATTR, True)
    logger.addHandler(stream_handler)

    handler_names = ["stderr"]
    log_path = directory / LOG_FILENAME
    reason = _probe_writable(directory)
    if reason is None:
        try:
            file_handler = logging.handlers.RotatingFileHandler(
                str(log_path),
                maxBytes=resolved_max_bytes,
                backupCount=resolved_backup_count,
                encoding="utf-8",
            )
        except OSError as exc:
            # 探测通过之后仍可能失败（竞态、句柄耗尽）。同样不许崩。
            reason = f"{type(exc).__name__}: {exc}"
        else:
            file_handler.setFormatter(formatter)
            file_handler.addFilter(RedactionFilter())
            file_handler.setLevel(resolved_level)
            setattr(file_handler, MANAGED_HANDLER_ATTR, True)
            logger.addHandler(file_handler)
            handler_names.append("file")

    _status = LoggingStatus(
        configured=True,
        degraded=reason is not None,
        reason=reason,
        log_file=str(log_path) if reason is None else None,
        handlers=handler_names,
    )

    if _status.degraded:
        logger.error(
            "日志文件通道不可用，已降级为仅 stderr：目录=%s 原因=%s。"
            "业务功能不受影响，但排障证据不会落盘——请检查该目录的存在性与写权限",
            directory,
            reason,
        )
    return _status
```

**Step 2:** 新建 `tools/liaison/tests/conftest.py`。**⚠️ 若该文件已被别的泳道创建，把下面的 fixture 追加进去，⛔ 不要覆盖整个文件。**

```python
"""`tools/liaison/tests` 的公共夹具。

**为什么必须有这个文件**：8.4 把 `setup_logging()` 接进了 `main()`，而现存的一批
`test_main_*` 用例是真的会调 `liaison_main.main()` 的。没有下面这条 autouse
fixture，本机跑一次 pytest 就会在 `data/liaison/logs/` 下拉出真实日志文件——
`data/` 虽在 `.gitignore` 里不会被提交，但「日志是个人信息的第二份拷贝」
（`.gitignore:17` 原话），让它在开发机上无声堆积不是可以接受的默认。
"""

from __future__ import annotations

import pytest

from tools.liaison import logsetup


@pytest.fixture(autouse=True)
def liaison_logs_to_tmp(tmp_path, monkeypatch):
    """把日志目录顶到本用例的 tmp_path，并在收尾时把 handler 摘掉关掉。

    ⚠️ teardown 里的 `teardown_logging()` ⛔ 不能省：不摘的话，上一条用例挂的
    file handler 会一直攥着一个已被 pytest 删掉的目录。
    """
    monkeypatch.setenv(logsetup.LOG_DIR_ENV, str(tmp_path / "liaison-logs"))
    yield
    logsetup.teardown_logging()
```

**Step 3:** 新建 `tools/liaison/tests/test_liaison_logsetup.py`：

```python
"""8.4 · 日志装配：轮转有界、不可写时降级、幂等。"""

from __future__ import annotations

import logging

from tools.liaison import logsetup


def test_rotation_keeps_at_most_backup_count_plus_one_file(tmp_path):
    """opener 约束 4 逐字：轮转触发后文件数 ≤ backupCount + 1。

    这条同时是 `runtime-observability`「日志量超过配置上限」的可执行形式：
    上界必须是**配置值**决定的，⛔ 不能随运行时间无限增长。
    """
    logsetup.setup_logging(log_dir=tmp_path, max_bytes=1024, backup_count=2)
    logger = logging.getLogger("tools.liaison.rotation_probe")
    for index in range(300):
        logger.info("填充日志行 %03d %s", index, "x" * 120)

    files = sorted(path.name for path in tmp_path.glob("liaison.log*"))
    assert len(files) >= 2, f"1 KiB 上限下 300 行必须已经轮转过，实际只有 {files}"
    assert len(files) <= 3, f"文件数超过 backupCount + 1：{files}"


def test_setup_logging_is_idempotent(tmp_path):
    """opener 约束 4 逐字：调两次不重复挂 handler。"""
    first = logsetup.setup_logging(log_dir=tmp_path)
    logger = logging.getLogger(logsetup.PACKAGE_LOGGER_NAME)
    handler_count = len(logger.handlers)

    second = logsetup.setup_logging(log_dir=tmp_path)

    assert len(logger.handlers) == handler_count
    assert first.handlers == second.handlers == ["stderr", "file"]
    filters = [f for f in logger.filters if isinstance(f, logsetup.RedactionFilter)]
    assert len(filters) == 1, f"包级 logger 上的脱敏 Filter 重复挂了：{filters}"


def test_setup_logging_keeps_a_stderr_copy(tmp_path, capsys):
    """launchd 会收 stderr（部署约束 4 + design D12）——文件之外必须留一份。"""
    logsetup.setup_logging(log_dir=tmp_path)
    logging.getLogger("tools.liaison.stderr_probe").warning("值守通道已启动")
    assert "值守通道已启动" in capsys.readouterr().err


def test_setup_logging_degrades_to_stderr_when_the_directory_is_unusable(tmp_path, capsys):
    """`runtime-observability`「日志写入位置不可用」：⛔ 不崩溃、⛔ 不静默。"""
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")

    status = logsetup.setup_logging(log_dir=blocker)

    assert status.configured is True
    assert status.degraded is True
    assert status.handlers == ["stderr"]
    assert status.log_file is None
    assert status.reason
    assert logsetup.logging_status().degraded is True
    err = capsys.readouterr().err
    assert "日志文件通道不可用" in err, "⛔ 不许静默降级为「什么都不记录」"
    assert "not-a-directory" in err


def test_business_logging_still_works_while_degraded(tmp_path, capsys):
    """降级之后业务功能不受影响——记录动作本身不许抛。"""
    blocker = tmp_path / "blocked"
    blocker.write_text("", encoding="utf-8")
    logsetup.setup_logging(log_dir=blocker)
    logging.getLogger("tools.liaison.inbound").warning("手机 13812345678")
    err = capsys.readouterr().err
    assert "13812345678" not in err, "降级路径上脱敏 ⛔ 不许一起失效"
    assert logsetup.PHONE_MASK in err


def test_log_dir_comes_from_the_process_environment(tmp_path, monkeypatch):
    """可由环境变量覆盖（opener 约束 1）。⚠️ 读的是**进程环境**，不是 .env。"""
    target = tmp_path / "from-env"
    monkeypatch.setenv(logsetup.LOG_DIR_ENV, str(target))
    status = logsetup.setup_logging()
    assert status.log_file == str(target / logsetup.LOG_FILENAME)


def test_bad_env_values_fall_back_to_defaults_instead_of_crashing(tmp_path, monkeypatch):
    """手抖的环境变量 ⛔ 不许把进程的第一个动作打死。"""
    monkeypatch.setenv(logsetup.LOG_MAX_BYTES_ENV, "不是数字")
    monkeypatch.setenv(logsetup.LOG_BACKUP_COUNT_ENV, "-3")
    status = logsetup.setup_logging(log_dir=tmp_path)
    assert status.degraded is False
    handler = [
        h
        for h in logging.getLogger(logsetup.PACKAGE_LOGGER_NAME).handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    ][0]
    assert handler.maxBytes == logsetup.DEFAULT_MAX_BYTES
    assert handler.backupCount == logsetup.DEFAULT_BACKUP_COUNT


def test_teardown_only_removes_handlers_it_owns(tmp_path):
    """⛔ 不许写成 handlers.clear()——别人挂的 handler 不归本模块管。"""
    logger = logging.getLogger(logsetup.PACKAGE_LOGGER_NAME)
    foreign = logging.NullHandler()
    logger.addHandler(foreign)
    try:
        logsetup.setup_logging(log_dir=tmp_path)
        logsetup.teardown_logging()
        assert foreign in logger.handlers
    finally:
        logger.removeHandler(foreign)
```

⚠️ `test_bad_env_values_fall_back_to_defaults_instead_of_crashing` 里用到了
`logging.handlers`——`import logging` 不会自动带进 `logging.handlers` 子模块。
本文件顶部除 `import logging` 外**还要加** `import logging.handlers`。

**Step 4:** 跑：

```bash
cd /Users/paulshao/Projects/HumanResource
venv/bin/python -m pytest tools/liaison/tests/test_liaison_logsetup.py tools/liaison/tests/test_liaison_log_redaction.py -q
```

预期输出：`18 passed`。

**Verification:**
- [ ] `venv/bin/python -m pytest tools/liaison/tests/test_liaison_logsetup.py tools/liaison/tests/test_liaison_log_redaction.py -q` → `18 passed`
- [ ] `git status --short data/` → 无 `data/liaison/logs/` 下的新文件（conftest 把它顶到 tmp_path 了）

---

### Task 4: 接线一行 + 结构守卫 + 全量回归

**Files:**
- Modify: `tools/liaison/__main__.py`（**只加两行，⛔ 不动任何既有行**）
- Modify: `tools/liaison/tests/test_liaison_logsetup.py`（追加结构守卫）

**Step 1:** 在 `tools/liaison/__main__.py` 的 import 区**新起一行**（⛔ 不要合并进既有的 `from tools.liaison import alerts, session, session_client` 那一行——改那行就是「动既有行」，且留存泳道的改动也可能落在同一行上）：

```python
from tools.liaison import logsetup
```

放在 `from tools.liaison import alerts, session, session_client` 的**下一行**。

**Step 2:** 在 `main()` 的 docstring 之后、`load_dotenv_into_environ(...)` 之**前**插入一行（⛔ 不动 `load_dotenv_into_environ` 那一行）：

```python
    logsetup.setup_logging()
```

改完后 `main()` 的开头长这样（前后各留一行既有代码作对照）：

```python
    """⚠️ 三个关键字参数是**接线缝**，只给测试注入 fake 用，⛔ 不是配置项——
    ⛔ 不要给它们加环境变量开关。"""
    logsetup.setup_logging()
    load_dotenv_into_environ(resolve_dotenv_path())
```

⚠️ **⛔ 不要给这行加 `with`、不要加 try/except**：`setup_logging` 自己已经保证
不可写时不抛（Task 3），而 `__main__.py` 有一条 `test_main_module_never_uses_a_with_statement`
盯着 `with`。

**Step 3:** 在 `tools/liaison/tests/test_liaison_logsetup.py` 末尾追加结构守卫。
⚠️ 下面代码块开头的 `import ast` / `import pathlib` / `from tools.liaison import __main__ as liaison_main` 三行**挪到文件顶部的 import 区**，⛔ 不要真的留在文件中间。

```python
import ast
import pathlib

from tools.liaison import __main__ as liaison_main

LIAISON_ROOT = pathlib.Path(logsetup.__file__).resolve().parent
MAIN_SOURCE = pathlib.Path(liaison_main.__file__)
LOGSETUP_SOURCE = pathlib.Path(logsetup.__file__)


def _parse(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_main_calls_setup_logging_as_its_first_statement():
    """opener 约束 3 逐字：只在 main() 第一行加 setup_logging() 一处。

    「第一行」是有意义的：`main()` 的凭据校验分支会 print 到 stderr 然后退出，
    日志晚一步装配就意味着启动期最常见的那类失败完全没有落盘证据。
    """
    tree = _parse(MAIN_SOURCE)
    main_fn = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    body = main_fn.body
    first = body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
        first = body[1]  # 跳过 docstring
    assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)
    assert ast.unparse(first.value.func) == "logsetup.setup_logging"


def test_setup_logging_is_wired_exactly_once_in_the_package():
    """⛔ 不许在别的模块里"顺手也调一次"——重复装配会摘掉正在用的 handler。"""
    callers: list[str] = []
    for path in sorted(LIAISON_ROOT.rglob("*.py")):
        if "tests" in path.parts:
            continue
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Call) and ast.unparse(node.func).endswith(
                "setup_logging"
            ):
                callers.append(path.name)
    assert callers == ["__main__.py"], f"setup_logging 的调用点不止一处：{callers}"


def test_logsetup_imports_no_app_module():
    """design D10：⛔ tools/ 不 import app.*。做法照抄，代码自建。"""
    for node in ast.walk(_parse(LOGSETUP_SOURCE)):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("app"), f"⛔ 不许 import {node.module}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("app"), f"⛔ 不许 import {alias.name}"


def test_logsetup_module_never_uses_a_with_statement():
    """TD-18：第 2 章的事务扫描器把任何 `with X:` 判为隐式提交违规。"""
    offenders = [
        node
        for node in ast.walk(_parse(LOGSETUP_SOURCE))
        if isinstance(node, (ast.With, ast.AsyncWith))
    ]
    assert offenders == []
```

**Step 4:** 跑全量回归——本 Task 真正要证明的是「接线没有把第 1–7 章的用例弄红」：

```bash
cd /Users/paulshao/Projects/HumanResource
venv/bin/python -m pytest tools/liaison/tests -q
venv/bin/python -m pytest -q
```

预期：两条都全绿，`tools/liaison/tests` 的用例数 = 原有数 + 22。

**Step 5:** 核对「⛔ 不动既有行」这条：

```bash
cd /Users/paulshao/Projects/HumanResource
git diff --stat tools/liaison/__main__.py
git diff -U0 tools/liaison/__main__.py | grep -c '^-[^-]'
```

预期：`git diff --stat` 显示 `2 +`、`0 -`；第二条命令输出 `0`（**一行都没删、没改**）。

**Verification:**
- [ ] `venv/bin/python -m pytest tools/liaison/tests -q` → 全绿
- [ ] `venv/bin/python -m pytest -q` → 全绿
- [ ] `git diff -U0 tools/liaison/__main__.py | grep -c '^-[^-]'` → `0`
- [ ] `git status --short` 里只有本计划列出的 5 个路径（⛔ 别人的改动不要碰，也不要 add）

---

## 端到端提取验证（已做，`spec-to-plan` §6）

本计划**不是**只经过自查清单——成稿时把全部代码块原样提取到一份仓库的临时副本
（`git ls-files` 的受版本管理文件 + `git init`，⛔ 不碰真实工作区）跑过全量测试。
用的解释器就是仓库自带的 `venv/bin/python`（Python 3.14.6 / pytest 8.3.4）。

| 检查 | 结果 |
|---|---|
| 本章新增用例 | **22 passed**（Task 1 的 6 + Task 2 的 4 + Task 3 的 8 + Task 4 的 4） |
| `tools/liaison/tests` 全量 | **444 passed, 3 skipped**（接线前基线收集数 425 → 接线后 447，**净增正好 22，⛔ 无既有用例被弄红或变哑**） |
| 仓库全量 `pytest -q` | **1696 passed, 4 skipped** |
| `__main__.py` 的改动量 | `git diff` 删除行数 **0**，新增行数 **2**（`from tools.liaison import logsetup` / `logsetup.setup_logging()`）——opener 约束 3「⛔ 不动既有行」逐字满足 |
| 拆层证伪 | 三次实测，结论见 Architecture 第 2 条的表。**其中一次推翻了本计划初稿的说法**：单摘 handler filter 时子 logger 那条用例不会红 |
| 逐字重提取复跑 | 把本文档的 `python` 代码块**原样**再提取一遍落盘重跑（`spec-to-plan` §6 第 5 步，防 Edit 引入转录误差）→ 同样 **444 passed, 3 skipped**。⚠️ 唯一需要人工补的是各 Step 里已用 ⚠️ 标出的 import 归位（含 `import logging.handlers`） |

⚠️ **这一步证明的是「代码可执行且内部自洽」，⛔ 不证明「符合 spec」**——测试与被测代码出自同一份文档、同一个作者。spec 合规由 `run-build` 的两阶段 review 负责。

⚠️ 临时副本已删除。controller 执行时**仍要按每个 Task 的 Verification 逐条重跑**——⛔ 不要因为这里写了"已验证"就跳过。

---

## 收工

只 `git add` 这 5 个路径，一个 commit：

```
tools/liaison/logsetup.py
tools/liaison/__main__.py
tools/liaison/tests/conftest.py
tools/liaison/tests/test_liaison_log_redaction.py
tools/liaison/tests/test_liaison_logsetup.py
```

⛔ 禁止 `git add -A` / `git add .` / `git commit -a`。push 被拒 →
`git pull --rebase --autostash origin main` 后重试，最多 3 次；⛔ 不在 commit 前 pull。

回勾 `openspec/changes/hr-wecom-aibot-liaison/tasks.md` 的 8.4，并在同一次提交里
把「未尽项」（按天数的日志留存清理）登记进 `docs/tech-debt.md`——⚠️ 编号在 commit
前一刻现取，⛔ 不要提前占号（并行泳道会撞号）。

---

## 落地偏离登记（2026-09-09 run-build 收口，[Mac]0909M，无头执行）

本节由 run-build 控制者在收口时写入。SDD 台账随 worktree 删除消失，此处是转写后的真源。
执行协议：superpowers skill **实测取不到**（`Unknown skill: superpowers:subagent-driven-development`），
按磁盘 `~/.claude/plugins/cache/.../subagent-driven-development/SKILL.md` 手工走完协议——
每 Task 一个 implementer + 一次 task review + fix loop + 全分支 final review + 一轮 fix wave。

### 偏离一：Task 2 提前吞掉了 Task 3 的实现部分

Task 2 的 brief 自带的 `wired_logger` fixture 依赖 `setup_logging`/`teardown_logging`，
而它们排在 Task 3。「反证不可选」这条要求 Task 2 当场能跑通三层拆解，故 implementer
逐字从 `task-3-brief.md` Step 1 转写了 `LoggingStatus`/`setup_logging`/`teardown_logging`/
`logging_status`。**判定：接受**，Task 3 相应缩为 test-only（`conftest.py` +
`test_liaison_logsetup.py`），否则同名重复定义。

### 偏离二：三个 Task 的 brief 逐字代码被改动（裁决留痕）

计划给的是「完整文件，逐字」，但 review 在其中查出 7 条有实测复现的缺陷。
无人值守下无人可裁决 plan 修订，控制者按 opener「保守方向」立了统一口径并全程适用：

> **CLAUDE.md 合规红线与函数自身 docstring 契约，优先于 plan 的逐字代码；
> 且只许往更安全的方向纠偏（宁可多脱敏，不可少脱敏；宁可降级，不可崩溃）。**

据此改动，逐条：

| # | 缺陷（均有实测复现） | 方向 |
|---|---|---|
| T1-1 | 保护段吞掉相邻文本：`thread_id=` 空值吞下一个 token，值跑过 `&` 边界 | 收窄豁免 |
| T1-2 | 引号包裹的凭据键名整条失配，`{"LLM_API_KEY": "sk-…"}` 明文 | 多脱敏 |
| T1-3 | `+86`/`0086` 前缀手机号不打码 | 多脱敏 |
| T2-1 | `HR_LIAISON_LOG_LEVEL` 手抖值（`'INFO '`/`'20'`）抛异常打死进程 | 降级不崩 |
| T2-2 | `teardown_logging` 不复位 logger.level，**实测已污染** `test_session_liveness` 一条既有用例 | 修真 bug |
| T2-3 | `propagate=True` 下 root handler 读到未脱敏的 `exc_text` 缓存 | 多脱敏 |
| F-1 | `~nosuchuser/logs` 让 `expanduser()` 抛 `RuntimeError`，launchd 崩溃循环且无日志 | 降级不崩 |
| F-2 | handler 级 `setLevel` 让低于该级的记录**跳过 Filter** 直达 root handler，泄露的是整条消息体 | 多脱敏 |

⚠️ **这些修正尚未回写进上文的代码块**——上文 Task 1/2/3 的「逐字」代码块相对
`tools/liaison/logsetup.py` 现状**已过时**，后续任何人 ⛔ 不要照上文的代码块还原。

### 偏离三：Task 4 少加一个守卫

brief Step 3 给 4 个守卫，其中 `test_logsetup_module_never_uses_a_with_statement`
已由 Task 3 落地。同名重定义只会静默遮蔽前者、并不新增测试，故只追加另外 3 个。

### 偏离四：Task 4 的 task review 与全分支 final review 合并为一次

Task 4 是末任务且 diff 仅「2 行接线 + 3 个守卫」，final review 本就全覆盖。预算考量。

### ⏸ 未尽项与已登记技术债

- **TD-30 日志留存期只有容量上界，缺时间维度**——本章按 opener 范围只做「轮转 + 有界容量 +
  脱敏」，D7 借的第三条「留存期有上限」**未实现**。容量上界满足（≤ 30 MiB），
  时间维度不满足。由 8.1 用独立的 `HR_LIAISON_LOG_RETENTION_DAYS`（默认 30）收口。
- **TD-31 带分隔符/全角渲染的手机号不脱敏**（`138-1234-5678`、全角数字）。
  reviewer 逐条读了全部 12 处非测试 `logger.*` 调用点，无一记录消息正文，当前不可达。
- **TD-32 JSON/全角冒号渲染下 `thread_id` 反被打码**。修它＝扩大明文豁免面，
  与合规红线反向，按保守方向裁定不修；现网两处调用点均为 `thread_id=%s` 无空格渲染。

### 三条硬反证的实测输出

1. **轮转有界**：有界（`backupCount=2`）→ 3 个文件通过；变异（`backupCount` 置 1000）
   → 60 个文件、断言变红。reviewer 用更公平的**源码级**变异独立复现同一 60，
   并另证下界守卫能抓住「根本没轮转」（`maxBytes=0`）与「轮转但不留备份」（`backupCount=0`）。
2. **脱敏落盘**：一条含手机号 + 邮箱 + `HR_LIAISON_BOT_SECRET=xxxx` 的记录经**真实
   `RotatingFileHandler`** 落盘后，三者皆无、`thread_id` 原样；`sender_userid` 仍被打码。
   final reviewer 另用 23 种敌意调用形态（dict/dataclass/bytes/自定义 `__repr__`/
   会抛的 `__str__`/`__cause__` 链/手搓 `LogRecord`/坏格式串）攻击，**零明文**落盘。
3. **幂等**：`setup_logging()` 调两次 handler 数不增（2→2）；变异（摘掉两处 `_detach_*`）
   → `assert 4 == 2` 变红。
4. **三层拆层反证**：同时摘掉 handler 级 `RedactionFilter` 与 `RedactingFormatter` 后恰好
   3 条变红（子 logger / traceback / 坏格式串）；单摘各自只红 1 条，与计划成稿时的实测表一致。
   ⚠️ 但 final reviewer 实测**包级 logger 上那个 Filter 是冗余的**（handler filter 在
   `callHandlers` 里先跑）。保留它是因为 opener 约束 2 逐字要求「脱敏 Filter 挂在包级 logger」。
