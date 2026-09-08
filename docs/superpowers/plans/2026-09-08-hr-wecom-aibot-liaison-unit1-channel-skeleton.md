# 通道可行性与服务骨架（hr-wecom-aibot-liaison 交付单元 1）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 HR 企微值守服务在写任何业务代码之前先站稳两件事——① `wecom-aibot-python-sdk` 在本项目钉死的 Python 3.14 上到底能不能用（能／不能都必须有 findings 落档与二选一的确定结论，⛔ 不留"视情况"）；② 凭据缺失时服务**拒绝启动**而不是"启动了但收不到消息"。同时用目录落点与依赖清单本身充当"不可能被误部署到 `.51`"的结构性门禁，并把 `tools/liaison/tests/` 接进根 `pyproject.toml` 的 `testpaths`，让全量 `pytest` 一次跑到它。

**Architecture:**

```
tools/                          ← ⛔ 不在 sync-to-server.sh 的 SYNC_PATHS 里，结构上不可能被推到 .51
└── liaison/
    ├── __init__.py
    ├── README.md               ← 三条边界：开发期值守工具 / 永不部署 .51 / 不是产品功能
    ├── requirements.txt         ← 本服务独立依赖，⛔ 不进根 requirements.txt、⛔ 不进 pyproject
    ├── errors.py               ← MissingCredentialsError（错误里只出现变量名，⛔ 不出现取值）
    ├── config.py               ← load_credentials()：缺失／空串／纯空白一律拒绝，指明缺失项
    ├── __main__.py             ← 入口：读 .env 进 os.environ → 校验凭据 → 失败 exit 2、进程不驻留
    ├── channel/                ← 路线 ② 命中时才有内容（最小 WS 客户端骨架）
    ├── scripts/
    │   └── probe_sdk_py314.py  ← SDK 兼容性实测探针（Task 2 用，输出机器可读 JSON）
    └── tests/                  ← 接进根 pyproject testpaths，全量 pytest 跑得到
```

三条支撑这套结构的判断：

1. **依赖隔离靠清单本身，不靠人记得。** `sync-to-server.sh:48-56` 的 `SYNC_PATHS` 白名单是 `app` / `scripts` / `requirements.txt` / `pyproject.toml` / `.env.example` / `deploy-server.ps1` / `sync-to-server.sh` 七项。`tools` 不在里面，所以本服务的代码与依赖**结构上到不了 `.51`**。⛔ 不去改 `sync-to-server.sh` 加黑名单——不在白名单里已经足够，改它反而制造一个必须维护的黑名单（design D10）。
2. **凭据校验必须发生在任何 SDK import 之前。** 这不是风格问题：根 venv 里**不会**装 aibot SDK（它只进 `tools/liaison/.venv`），如果 `__main__.py` 在模块层 import SDK，"凭据缺失 → 退出码 2 + 指明缺失项"这条 spec 行为在根 venv 里就变成了 `ModuleNotFoundError`，测不出来也证不了。Task 5 用 AST 把这条约束钉成断言。
3. **`tools/liaison/tests` 进根 `testpaths` 是安全的**，即使 `pyproject.toml` 会被同步到 `.51` 而 `tools/` 不会。已实测（pytest 8.3.4 与 9.1.1、Python 3.14.6）：`testpaths` 条目按 glob 处理，**不匹配任何路径时被静默跳过**，`pytest` 退出码仍为 0。Task 7 把这次实测固化成一条可重跑的验证步骤。

**Tech Stack:** Python 3.14（`/opt/homebrew/bin/python3.14`，本机实测 3.14.6）· 标准库（`os` / `pathlib` / `dataclasses` / `subprocess` / `tomllib` / `ast` / `re`）· pytest 8.3.4（与根 `requirements.txt` 同版本）· `wecom-aibot-python-sdk`（**待 Task 2 判定**）· SQLite（本章不涉及，第 2 章起）

---

## Global Constraints

以下逐字复制自 `CLAUDE.md`「工程铁律」「合规红线」「部署约束」，以及本交付单元 opener 的六条范围约束。**reviewer 以此为注意力透镜。**

### 来自 CLAUDE.md（逐字）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。
3. **企微回调先落库再处理**：只推一次、5 秒无响应即丢弃。回调接口只做签名校验 + 落库 + 返回 200。
4. **模型全部走境内**，简历数据不出境。
5. **候选人入口一律用一次性邀请链接，避免被认定"向境内公众提供"。**
6. **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。
7. **部署约束 4**：**目标服务器是 Windows，没有 Docker**。部署形态 = Python venv + Windows 计划任务 + 防火墙规则 + scp 推送。不要引入容器。

### 来自本交付单元 opener（逐字，六条）

1. design D10 逐字：全部代码落 `tools/liaison/`，依赖落 `tools/liaison/requirements.txt`；⛔ 不落 app/、⛔ 不落 scripts/（都在 sync-to-server.sh 的 SYNC_PATHS 里会被推到 .51）、⛔ 不进根 requirements.txt、⛔ 不进 pyproject 依赖
2. 1.2 是阻塞项：SDK 在 3.14 装得上且 import 成功才走 ①（钉死版本号）；否则走 ② design D8 的退路。两条路都在 plan 里写成独立 Task 分支，⛔ 不写"视情况"
3. 1.4 凭据校验 fail-closed：缺失 / 空串 / 纯空白一律拒绝启动并指明缺失项；1.5 .env.example 只写变量名与注释，⛔ 任何真实值；测试断言受版本管理文件不含密钥形态
4. 铁律 6 的精神（先落库再处理）与 D3（at-least-once + 幂等 effect）在本章只做骨架预留，⛔ 不提前写业务
5. 单向依赖 design D5：tools/ 可以 import app/，app/ ⛔ 不得 import tools/（第 2 章 2.6 加断言，本章不用做但 plan 要说明）
6. 测试落点：plan 要定 tools/liaison/tests/ 是否进根 pyproject 的 testpaths——推荐加进 testpaths（让全量 pytest 能跑到它），并说明理由；⛔ 不另起一套测试跑法

### 本章的三条"不做"（避免越界写进第 2–8 章的地盘）

- ⛔ **不建任何数据库、不建任何表、不写任何 `effect_*` 函数。** 幂等与事务是第 2 章（`data/liaison.db` schema 与 `idempotent_effect` 接入）。本章只在 README 与 `channel/` 的 docstring 里**预留说明**，不落代码。
- ⛔ **不建连、不收发任何真实消息、不写白名单／归档／队列／群通知。** 本章的入口跑完凭据校验就返回，第 7 章才接线长连接与断线告警。
- ⛔ **不放宽 `requires-python`**（design D8 明文禁止）。SDK 装不上就走路线 ②，不许为装上 SDK 去改产品的版本对齐前提。

### 单向依赖的说明（本章不实现，第 2 章 2.6 落断言）

design D5 定的复用方向是**单向**的：`tools/liaison` 可以 `import app.storage.idempotency`（复用幂等装饰器与 `effect_log` 表设计），⛔ `app/` 下任何模块不得 `import tools.*`。

反向 import 一旦发生，`app/` 就带上了一个**不会被同步到 `.51`** 的依赖——`sync-to-server.sh` 推 `app` 但不推 `tools`，于是本地全绿、服务器 `ModuleNotFoundError` 起不来。这个失败模式在开发机上永远不现形，只在发版那一刻爆。

**本章不写这条断言**（本章 `tools/liaison` 还没 import 任何 `app/` 的东西，断言无对象），它属于第 2 章 2.6：用 AST 扫 `app/` 下全部 `.py`，断言无任何 `import tools` / `from tools ... import`。本章唯一相关的动作是 Task 5 的"入口不得在模块层 import SDK"——方向不同、目的不同，⛔ 不要把两者合并。

---

## 分支执行说明（⚠️ controller 必读）

本章有**一个阻塞判定点**：Task 2 的 SDK 兼容性实测。

- Task 1 → Task 2 顺序执行。
- **Task 2 结论 = 路线 ①（SDK 在 3.14 装得上且 import 成功）** → 执行 **Task 3**，⛔ **跳过 Task 4**。
- **Task 2 结论 = 路线 ②（装不上 / import 失败 / 建连能力缺失）** → 执行 **Task 4**，⛔ **跳过 Task 3**。
- 被跳过的那个 Task 在 run-build 报告里写「未执行（分支未命中，Task 2 结论 = 路线 X）」，⛔ 不标成失败、⛔ 也不标成完成。
- Task 5 / 6 / 7 与分支无关，两条路线下都要执行。

**⚠️ 计划编写期的探测结果（2026-09-08，仅供参考，⛔ 不是验收依据）：** 写本计划时在 `/tmp` 的一次性 venv 里探过一轮，`python3.14 -m venv` + `pip install wecom-aibot-python-sdk` 成功装上 **1.0.2**，`import aibot` 在 **Python 3.14.6** 上成功。**两个必须写进 findings 的意外**：

- **发行名与 import 名不一致**：PyPI 发行名是 `wecom-aibot-python-sdk`，**顶层模块名是 `aibot`**（`import wecom_aibot_python_sdk` 会 `ModuleNotFoundError`）。模块 `__version__` 报 `1.0.0`，与发行版本 `1.0.2` 对不上——**以发行版本为准**，`__version__` 不可信。
- **默认重连次数有上限**：`WSClientOptions(bot_id, secret, reconnect_interval=1000, max_reconnect_attempts=10, heartbeat_interval=30000, request_timeout=10000, ws_url="", logger=None)`。`max_reconnect_attempts` 默认 **10**，而 spec `liaison-channel-session`「断线后自动恢复接收」要求"自动重试建立连接**直至成功**、服务不退出"。SDK docstring 写明 `-1` 表示无限重连。**这是第 7 章的接线约束**，本章只在 findings 里记一笔，⛔ 本章不实现。

**Task 2 必须在仓库内重跑一遍并以重跑结果为准**——上面这轮是在 `/tmp` 跑的、没有 findings 落档、也没有验证建连能力，⛔ 不许拿它冒充 Task 2 的产出。

---

### Task 1: 目录骨架、边界 README 与结构性门禁断言

对应 WBS 1.1。

**Files:**
- Create: `tools/__init__.py`
- Create: `tools/liaison/__init__.py`
- Create: `tools/liaison/README.md`
- Create: `tools/liaison/requirements.txt`
- Create: `tools/liaison/tests/__init__.py`
- Create: `tools/liaison/tests/test_liaison_boundaries.py`

**Interfaces:**
- Produces: 包路径 `tools.liaison`（后续全部模块的落点）、`tools.liaison.tests`（后续全部用例的落点）
- Consumes: 只读 `sync-to-server.sh` / `requirements.txt` / `pyproject.toml` 的文本，⛔ 不修改它们

**为什么 `tests/` 下要放 `__init__.py`：** 根 `tests/` 目录**没有** `__init__.py`，pytest 按 basename 给测试模块命名，两个目录里出现同名文件会直接冲突报错。给 `tools/liaison/tests/` 加 `__init__.py`（连带 `tools/__init__.py`、`tools/liaison/__init__.py`），模块名变成 `tools.liaison.tests.test_x`，与根 `tests/` 永不撞名。根 `pyproject.toml` 已有 `pythonpath = ["."]`，仓库根在 `sys.path` 上，这条 import 路径成立。

- [ ] **Step 1: 写失败的测试**

创建 `tools/__init__.py`、`tools/liaison/__init__.py`、`tools/liaison/tests/__init__.py` 三个空包标记文件（各写一行 docstring，⛔ 不留空文件）：

`tools/__init__.py`：
```python
"""开发期工具的落点。

⚠️ **`tools/` 刻意不在 `sync-to-server.sh` 的 `SYNC_PATHS` 白名单里**，因此这里的
任何东西都不会被推到 `.51` 生产服务器。这不是疏漏，是结构性门禁（design D10）：
让目录本身承担"不可能被误部署"的约束，而不是靠人记得别放错地方。

⛔ 不要把 `tools` 加进 `SYNC_PATHS`；⛔ 也不要改 `sync-to-server.sh` 去加"排除 tools"
的黑名单——不在白名单里已经足够，加黑名单只是多一份必须维护的清单。
"""
```

`tools/liaison/__init__.py`：
```python
"""HR 企微值守通道服务（开发期工具，永不部署 .51）。

边界见 `tools/liaison/README.md`。依赖清单是本目录下独立的 `requirements.txt`，
⛔ 不进根 `requirements.txt`、⛔ 不进 `pyproject.toml` 的依赖。
"""
```

`tools/liaison/tests/__init__.py`：
```python
"""HR 值守通道服务的测试。

本目录被接进根 `pyproject.toml` 的 `testpaths`，全量 `pytest` 会跑到它。
`__init__.py` 是必需的：根 `tests/` 没有 `__init__.py`，靠 basename 命名模块，
本目录若也不带包标记，两边一旦出现同名文件就会直接冲突。
"""
```

创建 `tools/liaison/tests/test_liaison_boundaries.py`：

```python
"""结构性门禁断言：本服务的代码与依赖必须到不了 `.51`。

这三条断言守的是 design D10 的核心手法——**让清单本身承担约束**。它们全部只读
仓库里已有的文件，不依赖任何运行时状态，因此在任何机器上结果都一样。

⛔ 断言失败时不要改断言。失败意味着有人把本服务的依赖塞进了会被同步的清单，
或者把 `tools` 加进了同步白名单——那正是这几条要挡住的事。
"""

import pathlib
import re
import tomllib

# tools/liaison/tests/test_x.py → parents[0]=tests, [1]=liaison, [2]=tools, [3]=仓库根
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

# 本服务的依赖里，任何一个出现在会被同步的清单里都是事故。
FORBIDDEN_IN_SYNCED_MANIFESTS = (
    "wecom-aibot-python-sdk",
    "wecom_aibot_python_sdk",
    "websockets",
)


def _sync_paths() -> list[str]:
    """从 sync-to-server.sh 里解析出 SYNC_PATHS 数组的字面量条目。"""
    text = (REPO_ROOT / "sync-to-server.sh").read_text(encoding="utf-8")
    match = re.search(r"SYNC_PATHS=\((.*?)\n\)", text, re.DOTALL)
    assert match is not None, "sync-to-server.sh 里找不到 SYNC_PATHS=( ... ) 数组"
    return re.findall(r'"([^"]+)"', match.group(1))


def test_tools_is_not_in_sync_paths():
    """`tools/` 不得进同步白名单——这是本服务"不可能被误部署"的唯一依据。"""
    entries = _sync_paths()
    assert entries, "SYNC_PATHS 解析出来是空的，解析逻辑坏了"
    assert "tools" not in entries
    assert not any(e == "tools/" or e.startswith("tools/") for e in entries)


def test_root_requirements_has_no_liaison_dependency():
    """根 requirements.txt 会被同步且在 .51 上被 pip install，本服务的依赖不得进入。"""
    text = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    for forbidden in FORBIDDEN_IN_SYNCED_MANIFESTS:
        assert forbidden not in text, f"根 requirements.txt 里出现了本服务的依赖: {forbidden}"


def test_pyproject_declares_no_runtime_dependencies():
    """pyproject.toml 也在 SYNC_PATHS 里。本服务的依赖同样不得从这里溜过去。"""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    declared = project.get("dependencies", [])
    joined = " ".join(declared).lower()
    for forbidden in FORBIDDEN_IN_SYNCED_MANIFESTS:
        assert forbidden not in joined, f"pyproject 依赖里出现了本服务的依赖: {forbidden}"


def test_liaison_requirements_file_exists_and_is_independent():
    """本服务的依赖清单必须独立存在，且不是空文件（空清单等于没有隔离对象）。"""
    path = REPO_ROOT / "tools" / "liaison" / "requirements.txt"
    assert path.is_file(), "缺 tools/liaison/requirements.txt"
    content = path.read_text(encoding="utf-8")
    meaningful = [
        line for line in content.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert meaningful, "tools/liaison/requirements.txt 里一条依赖都没有"


def test_readme_states_the_three_boundaries():
    """README 必须写死三条边界，评审时不需要去翻 proposal 才知道这是什么东西。"""
    text = (REPO_ROOT / "tools" / "liaison" / "README.md").read_text(encoding="utf-8")
    for phrase in ("开发期值守工具", "永不部署", "不是产品功能"):
        assert phrase in text, f"README 缺边界表述: {phrase}"
```

跑一次，确认它**因为文件不存在而失败**（不是因为断言逻辑写错）：

```bash
python3.14 -m pytest tools/liaison/tests/test_liaison_boundaries.py -q
```

预期输出含 `FileNotFoundError` 或 `AssertionError: 缺 tools/liaison/requirements.txt`，退出码非 0。

- [ ] **Step 2: 让测试通过**

创建 `tools/liaison/README.md`：

````markdown
# HR 企微值守通道服务

**这是什么**：Shao Peishen 在 HumanResource 开发期用的 24 小时值守工具，通过企业微信
"智能机器人"长连接与 HR 专员（汤丽萍）收材料、传信息，并把材料归档、把待办入队。

## 三条边界（评审与改动时先看这里）

1. **开发期值守工具**，不是 HumanResource 的产品功能。它的对手方是内部同事，
   ⛔ 不碰候选人、不碰简历、不做任何 AI 评分。
2. **永不部署 `.51`**（Shao Peishen 2026-08-13 拍板，与 Windows 侧同一口径）。
   落在 `tools/` 而不是 `app/` / `scripts/` 就是为了让这条约束由目录结构本身承担：
   `tools` 不在 `sync-to-server.sh` 的 `SYNC_PATHS` 白名单里，推不上去。
3. **不是产品功能**，因此 ⛔ 不受 `04-部署与门户挂载.md` 管辖：不挂 `/hr/recruit-agent`、
   不做路径前缀就绪、不进 FastAPI 应用、不走 8095 端口、不接鉴权中间件空壳。
   评审按 `openspec/changes/hr-wecom-aibot-liaison/proposal.md` 的验收标准判，
   ⛔ 不要套产品功能的验收标准。

## 依赖与环境

依赖清单是本目录下独立的 `requirements.txt`，⛔ 不进根 `requirements.txt`、
⛔ 不进 `pyproject.toml`——那两份都会被同步到 `.51` 并在那边 `pip install`。

```bash
python3.14 -m venv tools/liaison/.venv
tools/liaison/.venv/bin/pip install -r tools/liaison/requirements.txt
```

`tools/liaison/.venv/` 被根 `.gitignore` 的 `.venv/` 规则覆盖（该规则不带前导斜杠，
匹配任意层级的 `.venv` 目录），不会入库。

## 凭据

`HR_LIAISON_BOT_ID` 与 `HR_LIAISON_BOT_SECRET` **只从进程环境读**，真实值只落仓库根的
`.env`（`.gitignore` 已排除）。两者缺失、为空串或只含空白字符时，服务**拒绝启动**并
指明缺哪一项，⛔ 不以"启动了但收不到消息"的状态驻留。占位见根 `.env.example`。

测试专用逃生口 `HR_LIAISON_DOTENV_PATH`：指定入口读哪个 `.env` 文件，缺省为仓库根的
`.env`。⛔ 它只服务于测试隔离（避免开发机上真实的 `.env` 让"凭据缺失"用例变绿），
⛔ 不要在生产用法里依赖它，因此它**不写进 `.env.example`**。

## 当前进度

第 1 章（本目录的骨架、SDK 可行性结论、凭据 fail-closed 校验）。存储、白名单、
归档、队列、群通知、断线告警在第 2–8 章，见变更包 `tasks.md`。
````

创建 `tools/liaison/requirements.txt`（Task 2 判定前的初始内容，只放测试依赖；SDK 那一行由 Task 3 或 Task 4 填）：

```
# HR 值守通道服务的独立依赖清单。
#
# ⛔ 这份清单**不进**根 requirements.txt、**不进** pyproject.toml。
#    那两份都在 sync-to-server.sh 的 SYNC_PATHS 里，会被推到 .51 并在那边 pip install。
#    依赖隔离靠清单本身承担（design D10），不靠人记得别装错地方。
#
# 装法（Python 3.14，与产品同一版本区间，⛔ 不放宽 requires-python）：
#     python3.14 -m venv tools/liaison/.venv
#     tools/liaison/.venv/bin/pip install -r tools/liaison/requirements.txt

# 与根 requirements.txt 同版本，避免两套 pytest 行为差异变成"这边绿那边红"。
pytest==8.3.4

# ── 通道 SDK ────────────────────────────────────────────────────────────
# 由第 1 章 Task 3（路线 ①）或 Task 4（路线 ②）按兼容性实测结论填入。
# ⛔ 在 docs/findings/2026-09-08-aibot-sdk-py314-兼容性.md 有结论之前，这里保持为空。
```

- [ ] **Step 3: 验证**

```bash
python3.14 -m pytest tools/liaison/tests/test_liaison_boundaries.py -q
```

预期：`5 passed`，退出码 0。

```bash
git status --porcelain tools/
```

预期：只列出本 Task 新建的 6 个文件，⛔ 不应出现 `tools/liaison/.venv`（被 `.gitignore` 挡住）。

---

### Task 2: SDK 在 Python 3.14 的兼容性实测与 findings 落档（阻塞项）

对应 WBS 1.2 与 1.3 的判定部分。**这是本章唯一的分支点**，Task 3 与 Task 4 由它的结论二选一。

**Files:**
- Create: `tools/liaison/scripts/__init__.py`
- Create: `tools/liaison/scripts/probe_sdk_py314.py`
- Create: `docs/findings/2026-09-08-aibot-sdk-py314-兼容性.md`

**Interfaces:**
- Produces: 探针的 JSON 结论（`installable` / `importable` / `has_ws_client` 三个布尔 + 版本与模块名），以及 findings 文档里一行**唯一的**结论标记 `**路线结论：①**` 或 `**路线结论：②**`
- Consumes: `/opt/homebrew/bin/python3.14`（或 `PATH` 里的 `python3.14`）、PyPI

**判定口径（⛔ 三项全过才算路线 ①，缺一即路线 ②）：**

| # | 判据 | 通过条件 |
|---|---|---|
| A | 可安装 | `pip install` 在 Python 3.14 的干净 venv 里成功退出 |
| B | 可 import | 顶层模块能 import 成功，无 `SyntaxError` / `ModuleNotFoundError` / C 扩展加载失败 |
| C | 建连能力齐备 | 模块暴露出 WS 客户端类与其配置项（连接、心跳、重连三样参数可配） |

⚠️ **判据 C 只验"能力面是否齐备"，⛔ 不验"能不能连上企微服务端"**——真实建连需要 Shao Peishen 在企业微信管理后台注册的 aibot 凭据（proposal 已写明是账号级操作、无法代劳），此刻不具备。真实建连验证属第 7 章，且必须在拿到凭据之后。本 Task 若因缺凭据而无法验真实建连，findings 里如实写「⏸ 留步：真实建连未验，缺 aibot 凭据（账号级操作，Shao Peishen 本人在企业微信管理后台完成）」，⛔ 不得因此判整件失败、⛔ 也不得假装验过。

- [ ] **Step 1: 写探针**

创建 `tools/liaison/scripts/__init__.py`：

```python
"""HR 值守通道服务的一次性脚本（探针、导出等）。

⚠️ 这里是 `tools/liaison/scripts/`，**不是**仓库根的 `scripts/`。根 `scripts/` 在
`sync-to-server.sh` 的 `SYNC_PATHS` 白名单里、会被推到 `.51`；本目录不在。
⛔ 不要把本服务的任何脚本挪到根 `scripts/` 下。
"""
```

创建 `tools/liaison/scripts/probe_sdk_py314.py`：

```python
"""`wecom-aibot-python-sdk` 在 Python 3.14 上的兼容性探针（WBS 1.2）。

**为什么需要一个脚本而不是手敲几条命令**：这个结论要落进 findings 文档、要成为
路线 ①／② 的判定依据，而"我在终端里试过、能跑"是不可复核的。脚本把三条判据
（可安装 / 可 import / 建连能力齐备）固定下来并吐一份机器可读的 JSON，任何人
重跑都能拿到同一份结论。

用法（在**已经装好 SDK 的那个 venv 的解释器**里跑）：

    tools/liaison/.venv/bin/python -m tools.liaison.scripts.probe_sdk_py314

⛔ 本脚本不自己装包、不自己建 venv——安装本身是判据 A，必须在脚本外面跑，
它的成败要由 pip 的退出码直接说了算，不能被脚本的 try/except 吞掉。
"""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import sys

DISTRIBUTION_NAME = "wecom-aibot-python-sdk"

# ⚠️ 发行名与顶层模块名**不一致**：PyPI 上叫 wecom-aibot-python-sdk，import 进来叫
# aibot。两个候选都试，避免"import 名猜错"被误判成"SDK 不可用"而错走路线 ②。
CANDIDATE_MODULE_NAMES = ("aibot", "wecom_aibot_python_sdk")

# 判据 C：建连、心跳、重连三样都要能配，缺一说明这份 SDK 撑不起 spec
# 「断线后自动恢复接收」那条要求，只能走路线 ②。
REQUIRED_ATTRS = ("WSClient", "WSClientOptions")
REQUIRED_OPTION_FIELDS = ("bot_id", "secret", "heartbeat_interval", "max_reconnect_attempts")


def probe() -> dict:
    result: dict = {
        "python_version": sys.version.split()[0],
        "distribution_name": DISTRIBUTION_NAME,
        "distribution_version": None,
        "module_name": None,
        "module_version_attr": None,
        "importable": False,
        "has_ws_client": False,
        "missing_attrs": [],
        "missing_option_fields": [],
        "import_error": None,
    }

    try:
        result["distribution_version"] = importlib.metadata.version(DISTRIBUTION_NAME)
    except importlib.metadata.PackageNotFoundError:
        result["import_error"] = f"发行包未安装: {DISTRIBUTION_NAME}"
        return result

    module = None
    errors = []
    for name in CANDIDATE_MODULE_NAMES:
        try:
            module = importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 —— 探针要如实记录任何 import 失败
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        result["module_name"] = name
        result["importable"] = True
        break

    if module is None:
        result["import_error"] = " | ".join(errors)
        return result

    # ⚠️ 模块的 __version__ 与发行版本可能对不上（实测 1.0.2 的包里写着 1.0.0）。
    # 两个都记下来，findings 里以**发行版本**为准——requirements.txt 钉的是发行版本。
    result["module_version_attr"] = getattr(module, "__version__", None)

    result["missing_attrs"] = [a for a in REQUIRED_ATTRS if not hasattr(module, a)]
    if not result["missing_attrs"]:
        options_cls = getattr(module, "WSClientOptions")
        annotations = getattr(options_cls, "__annotations__", {})
        result["missing_option_fields"] = [
            f for f in REQUIRED_OPTION_FIELDS if f not in annotations
        ]
    else:
        result["missing_option_fields"] = list(REQUIRED_OPTION_FIELDS)

    result["has_ws_client"] = (
        not result["missing_attrs"] and not result["missing_option_fields"]
    )
    return result


def main() -> int:
    result = probe()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # 退出码即结论：0 = 判据 B+C 通过（判据 A 由外层 pip 的退出码负责）。
    return 0 if (result["importable"] and result["has_ws_client"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 跑实测（判据 A → B → C）**

```bash
rm -rf tools/liaison/.venv
python3.14 -m venv tools/liaison/.venv
tools/liaison/.venv/bin/python -V
tools/liaison/.venv/bin/pip install -r tools/liaison/requirements.txt
tools/liaison/.venv/bin/pip install wecom-aibot-python-sdk ; echo "判据A_EXIT=$?"
tools/liaison/.venv/bin/pip freeze
PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison.scripts.probe_sdk_py314 ; echo "判据BC_EXIT=$?"
```

**把上面每一条的真实输出原样贴进 findings 文档**（版本号、退出码、JSON 全文）。⛔ 不许概括成"装上了、能 import"——findings 是给三个月后的人复核用的，概括等于把证据扔了。

- [ ] **Step 3: 写 findings 并给出唯一结论**

创建 `docs/findings/2026-09-08-aibot-sdk-py314-兼容性.md`，按下面的骨架填**实测得到的**内容：

````markdown
# `wecom-aibot-python-sdk` 在 Python 3.14 的兼容性实测

**日期**：2026-09-08 ｜ **变更包**：`hr-wecom-aibot-liaison` ｜ **对应 WBS**：1.2 / 1.3
**为什么要测**：本项目 `requires-python = ">=3.14,<3.15"`（`pyproject.toml`，刻意收紧到
单一版本区间，理由是"测试环境 = 运行环境"）；而该 SDK 文档未声明 Python 支持范围，
按依赖链只能推断 3.8+。design D8 因此把兼容性实测定为写业务代码之前的阻塞项。

## 环境

| 项 | 值 |
|---|---|
| 解释器 | （贴 `python -V` 实际输出） |
| venv 落点 | `tools/liaison/.venv`（`.gitignore` 的 `.venv/` 规则覆盖，不入库） |
| 发行名 / 版本 | （贴 `pip freeze` 里的实际行） |

## 三条判据与实测结果

| # | 判据 | 结果 | 证据 |
|---|---|---|---|
| A | 可安装 | ✅／❌ | `pip install` 退出码 …（贴输出尾部） |
| B | 可 import | ✅／❌ | 探针 JSON 的 `importable` 字段 |
| C | 建连能力齐备 | ✅／❌ | 探针 JSON 的 `has_ws_client` / `missing_*` 字段 |

## 探针原始输出

```json
（贴 probe_sdk_py314 的 JSON 全文，⛔ 不删字段、⛔ 不改格式）
```

## 意外与坑（⚠️ 后续章节会被它绊到）

- **发行名 ≠ import 名**：PyPI 发行名 `wecom-aibot-python-sdk`，顶层模块名 `aibot`。
  写 `import wecom_aibot_python_sdk` 会 `ModuleNotFoundError`。
- **模块 `__version__` 不可信**：与发行版本对不上（实测填实际值）。
  `requirements.txt` 钉的是**发行版本**，任何"用 `__version__` 校验装对没有"的做法都会误判。
- **默认重连次数有上限**：`WSClientOptions.max_reconnect_attempts` 默认 10，
  而 spec `liaison-channel-session`「断线后自动恢复接收」要求"自动重试**直至成功**、
  服务不退出"。SDK 里 `-1` 表示无限重连。**这是第 7 章 7.6 的接线约束**，
  ⛔ 第 1 章不实现，但第 7 章不许用默认值。

## ⏸ 留步项

- **真实建连未验**：需要企业微信管理后台注册的 aibot `BotID` / `Secret`，属账号级操作，
  只能 Shao Peishen 本人完成（proposal「Impact · 人」已写明）。本次只验能力面是否齐备。
  真实建连验证归第 7 章，且必须在拿到凭据之后。

## 结论

**路线结论：①**（或 **②**）

- **①**＝ 判据 A/B/C 全过 → 采用 SDK，在 `tools/liaison/requirements.txt` 里**钉死发行版本号**。
- **②**＝ 任一判据未过 → 按 design D8 退路，自建最小 WS 客户端骨架（只覆盖本服务用到的
  消息类型，⛔ 不做通用 SDK）。

⛔ 本文件里**有且只有一行**以 `**路线结论：` 开头的文本。多写一行会让 Task 3/4 的分支
判定失去唯一依据。

⛔ 无论结论是哪条，都**不放宽 `requires-python`**（design D8 明文禁止）——放宽会把整个
产品的版本对齐前提改掉，代价与本工具的收益完全不成比例。
````

- [ ] **Step 4: 验证结论唯一且可被机器读出**

```bash
grep -c '^\*\*路线结论：' "docs/findings/2026-09-08-aibot-sdk-py314-兼容性.md"
grep    '^\*\*路线结论：' "docs/findings/2026-09-08-aibot-sdk-py314-兼容性.md"
```

预期：第一条输出 `1`；第二条输出唯一一行结论。**controller 按这一行决定下一个 Task 是 3 还是 4。**

---

### Task 3: 路线 ① — 钉死 SDK 发行版本并加冒烟测试

对应 WBS 1.3 分支 ①。**⚠️ 仅当 Task 2 的 findings 写的是 `**路线结论：①**` 时执行；结论是 ② 则整个 Task 跳过并在报告里写「未执行（分支未命中）」。**

**Files:**
- Modify: `tools/liaison/requirements.txt`
- Create: `tools/liaison/tests/test_liaison_sdk_smoke.py`

**Interfaces:**
- Produces: 钉死版本号的依赖行；一条**在没装 SDK 的环境里自动 skip**的冒烟用例
- Consumes: Task 2 的 findings（取实测得到的发行版本号与顶层模块名）

**为什么冒烟测试必须能被 skip：** 根 venv（跑全量 `pytest` 的那个）**不装** aibot SDK——它只进 `tools/liaison/.venv`。如果这条用例在根 venv 里硬 import SDK，全量 `pytest` 会红，而红的原因是"依赖隔离生效了"，正是设计意图。用 `pytest.importorskip` 让它在根 venv 里 skip、在 liaison venv 里真跑，两边都说实话。

- [ ] **Step 1: 写失败的测试**

创建 `tools/liaison/tests/test_liaison_sdk_smoke.py`：

```python
"""路线 ① 的冒烟测试：SDK 装在 liaison venv 里时，它必须真的能用。

⚠️ **在根 venv 里这条用例会 skip**，这是刻意的：aibot SDK 只进 tools/liaison/.venv，
⛔ 不进根 requirements.txt（design D10 的依赖隔离）。skip 不是漏测——它在
tools/liaison/.venv 的那次运行里会真跑。两处运行方式见 tools/liaison/README.md。

⛔ 不要为了"让根 venv 也能跑"把 SDK 加进根 requirements.txt，那会让本服务的依赖
被推到 .51 并在那边安装，正是整个 D10 要挡住的事。
"""

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
FINDINGS = REPO_ROOT / "docs" / "findings" / "2026-09-08-aibot-sdk-py314-兼容性.md"

# ⚠️ 发行名 ≠ import 名：PyPI 上是 wecom-aibot-python-sdk，import 进来是 aibot。
DISTRIBUTION_NAME = "wecom-aibot-python-sdk"
MODULE_NAME = "aibot"


def test_findings_records_route_one():
    """路线 ① 成立的唯一依据是 findings 里那一行结论，⛔ 不靠谁记得。"""
    assert FINDINGS.is_file(), "缺 SDK 兼容性 findings，Task 2 没做完"
    lines = [
        line for line in FINDINGS.read_text(encoding="utf-8").splitlines()
        if line.startswith("**路线结论：")
    ]
    assert len(lines) == 1, f"findings 里的路线结论行不唯一: {lines}"
    assert "①" in lines[0], f"当前结论不是路线 ①，本文件不该被执行: {lines[0]}"


def test_requirements_pins_exact_sdk_version():
    """⛔ 不许写 >= / ~= / 不带版本号。版本漂移会让'实测过的那份'与'装上的那份'不是一回事。"""
    text = (REPO_ROOT / "tools" / "liaison" / "requirements.txt").read_text(encoding="utf-8")
    pins = re.findall(rf"^{re.escape(DISTRIBUTION_NAME)}==([0-9][^\s#]*)", text, re.MULTILINE)
    assert len(pins) == 1, f"requirements.txt 里没有恰好一条钉死版本的 SDK 依赖: {pins}"
    loose = re.findall(rf"^{re.escape(DISTRIBUTION_NAME)}(?!==)", text, re.MULTILINE)
    assert not loose, "SDK 依赖出现了非 == 的版本约束"


def test_sdk_imports_and_exposes_connection_surface():
    """SDK 真装上时（liaison venv），建连所需的三样参数必须都在。"""
    module = pytest.importorskip(
        MODULE_NAME,
        reason="aibot SDK 只装在 tools/liaison/.venv，根 venv 里 skip 是预期行为",
    )
    assert hasattr(module, "WSClient")
    assert hasattr(module, "WSClientOptions")
    annotations = module.WSClientOptions.__annotations__
    for field in ("bot_id", "secret", "heartbeat_interval", "max_reconnect_attempts"):
        assert field in annotations, f"WSClientOptions 缺字段 {field}"


def test_installed_sdk_version_matches_the_pin():
    """装上的发行版本必须与钉死的那个一致。

    ⚠️ 校验对象是**发行版本**（importlib.metadata），⛔ 不是模块的 __version__——
    实测两者对不上（包里写 1.0.0，发行版本是 1.0.2），拿 __version__ 校验会误判。
    """
    pytest.importorskip(MODULE_NAME, reason="根 venv 不装 SDK，skip 是预期行为")
    import importlib.metadata

    installed = importlib.metadata.version(DISTRIBUTION_NAME)
    text = (REPO_ROOT / "tools" / "liaison" / "requirements.txt").read_text(encoding="utf-8")
    pinned = re.findall(rf"^{re.escape(DISTRIBUTION_NAME)}==([0-9][^\s#]*)", text, re.MULTILINE)[0]
    assert installed == pinned, f"装上的是 {installed}，清单钉的是 {pinned}"
```

跑一次，确认在根 venv 里失败于"版本没钉死"（前两条真跑、后两条 skip）：

```bash
python3.14 -m pytest tools/liaison/tests/test_liaison_sdk_smoke.py -q
```

预期：`test_requirements_pins_exact_sdk_version` 失败（清单里还没有 SDK 那一行）。

- [ ] **Step 2: 让测试通过**

把 `tools/liaison/requirements.txt` 末尾那段占位注释替换成钉死版本的依赖行（版本号取 **Task 2 findings 里实测到的发行版本**，⛔ 不要照抄本计划里的示例数字）：

```
# ── 通道 SDK（路线 ①，依据 docs/findings/2026-09-08-aibot-sdk-py314-兼容性.md）──
# ⛔ 必须钉死到具体发行版本（==），不许 >= / ~= / 裸名字：
#    实测过的那份与装上的那份必须是同一份，否则实测结论不成立（design D8）。
# ⚠️ import 名是 `aibot`，不是 `wecom_aibot_python_sdk`。
wecom-aibot-python-sdk==<Task 2 实测到的发行版本>
```

- [ ] **Step 3: 验证（两个 venv 各跑一次）**

根 venv（SDK 缺席，冒烟用例应 skip）：

```bash
python3.14 -m pytest tools/liaison/tests/ -q -rs
```

预期：全部通过，且输出里有 2 条 `SKIPPED`，skip 原因含 `根 venv` 或 `只装在 tools/liaison/.venv`。

liaison venv（SDK 在场，冒烟用例应真跑）：

```bash
tools/liaison/.venv/bin/pip install -r tools/liaison/requirements.txt
PYTHONPATH=. tools/liaison/.venv/bin/python -m pytest tools/liaison/tests/ -q
```

预期：全部通过，**0 skipped**。

⛔ 只跑其中一个不算验完——两个环境验的是两件不同的事：根 venv 验"依赖隔离没被破坏"，liaison venv 验"SDK 真能用"。

---

### Task 4: 路线 ② — 最小 WS 客户端模块骨架

对应 WBS 1.3 分支 ②，退路依据 design D8。**⚠️ 仅当 Task 2 的 findings 写的是 `**路线结论：②**` 时执行；结论是 ① 则整个 Task 跳过并在报告里写「未执行（分支未命中）」。**

**Files:**
- Modify: `tools/liaison/requirements.txt`
- Create: `tools/liaison/channel/__init__.py`
- Create: `tools/liaison/channel/minimal_ws.py`
- Create: `tools/liaison/tests/test_liaison_minimal_ws.py`

**Interfaces:**
- Produces: `tools.liaison.channel.minimal_ws` 模块，导出 `MinimalWsOptions`（dataclass）、`compute_backoff_delays()`（纯函数）、`MinimalWsClient`（骨架类）
- Consumes: `websockets`（钉死版本，只进 `tools/liaison/requirements.txt`）

**骨架的边界（⛔ 别越界）：** 本 Task 只建**能被测的纯函数 + 明确未实现的类**，⛔ 不建连、⛔ 不解析真实帧、⛔ 不实现心跳循环。真正的连接、心跳、重连接线是第 7 章 7.6。

**为什么退避序列要单独做成纯函数：** spec `liaison-channel-session`「断线后自动恢复接收」有两条可测的要求——"重试间隔随失败次数增长"和"不以无退避的紧密循环重试"。这两条如果只活在一个 `async def run()` 里，就只能靠跑真连接来验；抽成 `compute_backoff_delays()` 之后，第 7 章可以直接对它断言，不需要网络。这与铁律 2 的 `compute_*` / `effect_*` 命名区分是同一条思路。

- [ ] **Step 1: 写失败的测试**

创建 `tools/liaison/tests/test_liaison_minimal_ws.py`：

```python
"""路线 ② 的骨架测试：退避序列是纯函数、可算、有上限、不退化成紧密循环。

⛔ 本文件不测"能不能连上企微"——那需要 Shao Peishen 在企业微信管理后台注册的
凭据，且属第 7 章。这里只测能在无网络环境下确定性验证的那部分。
"""

import pathlib

import pytest

from tools.liaison.channel.minimal_ws import (
    MinimalWsClient,
    MinimalWsOptions,
    compute_backoff_delays,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
FINDINGS = REPO_ROOT / "docs" / "findings" / "2026-09-08-aibot-sdk-py314-兼容性.md"


def test_findings_records_route_two():
    """路线 ② 成立的唯一依据是 findings 里那一行结论，⛔ 不靠谁记得。"""
    assert FINDINGS.is_file(), "缺 SDK 兼容性 findings，Task 2 没做完"
    lines = [
        line for line in FINDINGS.read_text(encoding="utf-8").splitlines()
        if line.startswith("**路线结论：")
    ]
    assert len(lines) == 1, f"findings 里的路线结论行不唯一: {lines}"
    assert "②" in lines[0], f"当前结论不是路线 ②，本文件不该被执行: {lines[0]}"


def test_backoff_is_strictly_increasing_until_cap():
    delays = compute_backoff_delays(attempts=8, base_seconds=1.0, cap_seconds=30.0)
    assert delays[0] == pytest.approx(1.0)
    rising = [d for d in delays if d < 30.0]
    assert rising == sorted(rising)
    assert len(set(rising)) == len(rising), "封顶之前不允许出现重复间隔"


def test_backoff_never_returns_zero():
    """⛔ 任何一次重试间隔都不得为 0——那就是 spec 明令禁止的紧密循环。"""
    delays = compute_backoff_delays(attempts=50, base_seconds=1.0, cap_seconds=30.0)
    assert all(d > 0 for d in delays)


def test_backoff_is_capped():
    delays = compute_backoff_delays(attempts=50, base_seconds=1.0, cap_seconds=30.0)
    assert max(delays) == pytest.approx(30.0)


def test_backoff_rejects_nonsense_arguments():
    """fail-closed：参数不合理就报错，⛔ 不悄悄折成默认值跑下去。"""
    for kwargs in (
        {"attempts": 0, "base_seconds": 1.0, "cap_seconds": 30.0},
        {"attempts": 5, "base_seconds": 0.0, "cap_seconds": 30.0},
        {"attempts": 5, "base_seconds": 1.0, "cap_seconds": 0.5},
    ):
        with pytest.raises(ValueError):
            compute_backoff_delays(**kwargs)


def test_options_default_to_unlimited_reconnect():
    """spec 要求"自动重试直至成功、服务不退出"，默认值就得是无限重连。"""
    options = MinimalWsOptions(bot_id="bot", secret="sec")
    assert options.max_reconnect_attempts == -1
    assert options.heartbeat_interval_seconds > 0


def test_client_is_an_honest_skeleton():
    """骨架必须**明确未实现**，⛔ 不许写一个返回 None 的假 run() 冒充可用。"""
    client = MinimalWsClient(MinimalWsOptions(bot_id="bot", secret="sec"))
    with pytest.raises(NotImplementedError) as excinfo:
        client.run_forever()
    assert "第 7 章" in str(excinfo.value)
```

- [ ] **Step 2: 让测试通过**

创建 `tools/liaison/channel/__init__.py`：

```python
"""值守通道的连接层。

路线 ②（design D8 退路）命中时才有内容：SDK 在 Python 3.14 上不可用，按企微官方
WS 协议自建最小客户端。范围**只覆盖本服务用到的消息类型**，⛔ 不做通用 SDK。
"""
```

创建 `tools/liaison/channel/minimal_ws.py`：

```python
"""最小 WS 客户端骨架（design D8 退路，路线 ②）。

**本模块此刻只有骨架**：可测的纯函数（退避序列）+ 明确抛 NotImplementedError 的
客户端类。真正的连接、心跳、重连、帧解析在**第 7 章 7.6**。

⛔ 不要在这里补出一个"能跑的最小实现"。第 1 章的验收边界是「SDK 路线已定且有
findings 落档」，越界实现会让第 7 章的 review 失去对象——那一章要审的正是重连、
存活戳与中断窗口，提前塞进来等于绕过它。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MinimalWsOptions:
    """连接参数。

    默认值刻意与 spec 对齐，⛔ 不照抄任何 SDK 的默认值：
    `max_reconnect_attempts = -1`（无限）对应 spec「断线后自动恢复接收」的
    "自动重试建立连接**直至成功**……服务不退出"。有上限的默认值会让服务在长时间
    断网后**安静地放弃**，而这正是本变更包要根治的那类静默失败。
    """

    bot_id: str
    secret: str
    ws_url: str = "wss://openws.work.weixin.qq.com"
    heartbeat_interval_seconds: float = 30.0
    reconnect_base_seconds: float = 1.0
    reconnect_cap_seconds: float = 30.0
    max_reconnect_attempts: int = -1  # -1 = 无限重连


def compute_backoff_delays(
    *, attempts: int, base_seconds: float, cap_seconds: float
) -> list[float]:
    """算出前 `attempts` 次重连的间隔序列（秒），指数退避 + 封顶。

    纯函数（铁律 2 的 `compute_*` 口径）：不读时钟、不睡眠、不碰网络，因此第 7 章
    可以在没有网络的情况下直接对"间隔随失败次数增长"和"不是紧密循环"这两条 spec
    要求做断言。

    参数不合理时 **raise，⛔ 不折成默认值**——静默折默认值会让"退避被配没了"变成
    一个不报错的配置事故。
    """
    if attempts < 1:
        raise ValueError(f"attempts 必须 >= 1，收到 {attempts}")
    if base_seconds <= 0:
        raise ValueError(f"base_seconds 必须 > 0，收到 {base_seconds}（0 即紧密循环）")
    if cap_seconds < base_seconds:
        raise ValueError(f"cap_seconds({cap_seconds}) 不得小于 base_seconds({base_seconds})")

    return [min(base_seconds * (2 ** i), cap_seconds) for i in range(attempts)]


class MinimalWsClient:
    """企微 aibot WS 长连接的最小客户端。**第 1 章只有骨架。**"""

    def __init__(self, options: MinimalWsOptions) -> None:
        self.options = options

    def run_forever(self) -> None:
        raise NotImplementedError(
            "最小 WS 客户端的连接、心跳与重连在第 7 章（tasks.md 7.6）实现。"
            "第 1 章只定通道路线与骨架，⛔ 不提前建连。"
        )
```

把 `tools/liaison/requirements.txt` 末尾那段占位注释替换成：

```
# ── 通道实现（路线 ②，依据 docs/findings/2026-09-08-aibot-sdk-py314-兼容性.md）──
# SDK 在 Python 3.14 上不可用，按 design D8 退路自建最小 WS 客户端。
# ⛔ 必须钉死到具体版本（==）：退路的代价是自己维护心跳与重连，底层库再漂移
#    就没人兜底了。版本号取实测装上的那个（pip freeze 里的实际行）。
websockets==<实测装上的版本>
```

- [ ] **Step 3: 验证**

```bash
python3.14 -m pytest tools/liaison/tests/ -q
```

预期：全部通过。

```bash
grep -rn "NotImplementedError" tools/liaison/channel/minimal_ws.py
```

预期：命中 1 处，确认骨架是**诚实未实现**而不是被悄悄补成了半个实现。

---

### Task 5: 启动期凭据校验（fail-closed，进程不驻留）

对应 WBS 1.4 与 1.6 的凭据部分；对应 spec `liaison-channel-session`「凭据缺失时拒绝启动」的三个场景中的前两个（第三个「版本管理中不含凭据」在 Task 6）。

**Files:**
- Create: `tools/liaison/errors.py`
- Create: `tools/liaison/config.py`
- Create: `tools/liaison/__main__.py`
- Create: `tools/liaison/tests/test_liaison_credentials.py`

**Interfaces:**
- Produces: `tools.liaison.config.load_credentials(env=None) -> LiaisonCredentials`、`tools.liaison.errors.MissingCredentialsError`、`python -m tools.liaison` 入口（缺凭据退出码 **2**）
- Consumes: 进程环境（`os.environ`）；可选的 `.env` 文件（路径可被 `HR_LIAISON_DOTENV_PATH` 覆盖，仅供测试隔离）

**三条设计判断，reviewer 请按这三条审：**

1. **校验必须发生在任何 SDK import 之前。** 根 venv 不装 SDK；若 `__main__.py` 在模块层 import 了 SDK，"缺凭据 → 退出码 2 + 指明缺失项"这条 spec 行为在根 venv 里会变成 `ModuleNotFoundError`，既测不出也证不了。Task 用 AST 把这条钉成断言。
2. **错误信息里只出现变量名，⛔ 绝不出现取值。** 报错通常会被贴进聊天、日志、issue。把"你配的是 `abc123`"打出来，等于把凭据顺着排障路径散出去。`LiaisonCredentials` 的 `__repr__` 同理必须遮蔽 secret——dataclass 默认生成的 `__repr__` 会把两个字段原样打出来，因此显式 `repr=False` 并自己写一个。
3. **`.env` 的读取路径必须可被测试覆盖掉。** 开发机上仓库根真的有一个 `.env`；若入口写死读它，"凭据缺失"的用例在 Shao Peishen 的机器上会变绿、在 CI 上变红——一条会看人下菜碟的用例比没有更糟。`HR_LIAISON_DOTENV_PATH` 就是为此存在，⛔ 它不写进 `.env.example`（那会把测试逃生口暗示成一种生产用法）。

- [ ] **Step 1: 写失败的测试**

创建 `tools/liaison/tests/test_liaison_credentials.py`：

```python
"""启动期凭据校验（WBS 1.4）：缺失／空串／纯空白一律拒绝启动并指明缺失项。

对应 spec liaison-channel-session「凭据缺失时拒绝启动」的两个场景：
- 凭据未填写 → 启动失败并指明缺失项、进程不驻留
- 凭据为空白字符串 → 视为缺失，启动失败

⛔ 本文件不测 SDK、不测建连——凭据校验必须在任何 SDK import 之前发生，
这本身就是被测的不变式之一（test_entrypoint_does_not_import_sdk_at_module_level）。
"""

import ast
import os
import pathlib
import subprocess
import sys
import time

import pytest

from tools.liaison.config import (
    BOT_ID_ENV,
    BOT_SECRET_ENV,
    LiaisonCredentials,
    load_credentials,
)
from tools.liaison.errors import MissingCredentialsError

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
LIAISON_DIR = REPO_ROOT / "tools" / "liaison"

# 入口在校验凭据之前**不得**触碰这些模块——根 venv 里它们根本不存在，
# 一旦在模块层 import，缺凭据的退出路径就会先炸在 ModuleNotFoundError 上。
FORBIDDEN_MODULE_LEVEL_IMPORTS = ("aibot", "wecom_aibot_python_sdk", "websockets")

# 校验必须发生在 SDK import 之前的那几个模块。
STARTUP_PATH_MODULES = ("errors.py", "config.py", "__main__.py")


def _run_entrypoint(env_overrides: dict[str, str], tmp_path: pathlib.Path):
    """在子进程里跑 `python -m tools.liaison`，返回 CompletedProcess 与耗时。

    ⚠️ 必须指一个**不存在**的 .env：开发机上仓库根真有一个 .env，不隔离的话
    "凭据缺失"这条用例会在他机器上变绿、在 CI 上变红。
    """
    env = os.environ.copy()
    env.pop(BOT_ID_ENV, None)
    env.pop(BOT_SECRET_ENV, None)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["HR_LIAISON_DOTENV_PATH"] = str(tmp_path / "absent.env")
    env.update(env_overrides)

    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-m", "tools.liaison"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc, time.monotonic() - started


# ── 纯函数层：三种缺失形态 ──────────────────────────────────────────────

def test_both_credentials_absent():
    with pytest.raises(MissingCredentialsError) as excinfo:
        load_credentials(env={})
    assert set(excinfo.value.missing_names) == {BOT_ID_ENV, BOT_SECRET_ENV}
    assert BOT_ID_ENV in str(excinfo.value)
    assert BOT_SECRET_ENV in str(excinfo.value)


def test_empty_string_counts_as_missing():
    with pytest.raises(MissingCredentialsError) as excinfo:
        load_credentials(env={BOT_ID_ENV: "", BOT_SECRET_ENV: ""})
    assert set(excinfo.value.missing_names) == {BOT_ID_ENV, BOT_SECRET_ENV}


def test_whitespace_only_counts_as_missing():
    """spec 场景「凭据为空白字符串」：只含空格 / 制表符 / 换行一律算缺失。"""
    blank = {BOT_ID_ENV: "   ", BOT_SECRET_ENV: "\t\n "}
    with pytest.raises(MissingCredentialsError) as excinfo:
        load_credentials(env=blank)
    assert set(excinfo.value.missing_names) == {BOT_ID_ENV, BOT_SECRET_ENV}


def test_error_names_only_the_actually_missing_item():
    """"指明缺失项"要指得准：配了一半时，⛔ 不许把配好的那项也报成缺失。"""
    with pytest.raises(MissingCredentialsError) as excinfo:
        load_credentials(env={BOT_ID_ENV: "bot-present", BOT_SECRET_ENV: "  "})
    assert excinfo.value.missing_names == (BOT_SECRET_ENV,)
    assert BOT_ID_ENV not in str(excinfo.value)


def test_valid_credentials_are_returned_stripped():
    creds = load_credentials(env={BOT_ID_ENV: " bot-1 ", BOT_SECRET_ENV: " sec-1 "})
    assert creds == LiaisonCredentials(bot_id="bot-1", bot_secret="sec-1")


def test_repr_does_not_leak_the_secret():
    """repr 会出现在日志、traceback、pytest 断言里。secret 不能跟着一起走。"""
    creds = load_credentials(env={BOT_ID_ENV: "bot-1", BOT_SECRET_ENV: "super-sensitive"})
    rendered = f"{creds!r} {creds}"
    assert "super-sensitive" not in rendered
    assert "bot-1" in rendered  # bot_id 不是秘密，排障要看得见


# ── 进程层：拒绝启动且不驻留 ────────────────────────────────────────────

def test_entrypoint_exits_nonzero_and_names_missing_items(tmp_path):
    proc, elapsed = _run_entrypoint({}, tmp_path)
    assert proc.returncode == 2, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert BOT_ID_ENV in proc.stderr
    assert BOT_SECRET_ENV in proc.stderr
    # "进程不驻留"：拿不到凭据就该立刻退，⛔ 不允许进入任何等待/重试循环。
    assert elapsed < 15, f"入口在缺凭据时驻留了 {elapsed:.1f}s"


def test_entrypoint_succeeds_when_credentials_present(tmp_path):
    proc, _ = _run_entrypoint(
        {BOT_ID_ENV: "bot-1", BOT_SECRET_ENV: "sec-1"}, tmp_path
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "sec-1" not in proc.stdout + proc.stderr, "凭据取值不得出现在任何输出里"


def test_entrypoint_reads_dotenv_when_process_env_is_absent(tmp_path):
    """凭据"只从进程环境读"——.env 的作用是**填进**进程环境，不是第二个真源。"""
    dotenv = tmp_path / "from-file.env"
    dotenv.write_text(
        f"# 注释行应被跳过\n{BOT_ID_ENV}=bot-from-file\n{BOT_SECRET_ENV}=sec-from-file\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.pop(BOT_ID_ENV, None)
    env.pop(BOT_SECRET_ENV, None)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["HR_LIAISON_DOTENV_PATH"] = str(dotenv)
    proc = subprocess.run(
        [sys.executable, "-m", "tools.liaison"],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"stderr={proc.stderr!r}"


def test_process_env_wins_over_dotenv(tmp_path):
    """进程环境优先于 .env（与 pydantic-settings 在 app/config.py 里的口径一致）。"""
    dotenv = tmp_path / "loser.env"
    dotenv.write_text(f"{BOT_ID_ENV}=from-file\n{BOT_SECRET_ENV}=from-file\n", encoding="utf-8")
    from tools.liaison.__main__ import load_dotenv_into_environ

    env_backup = dict(os.environ)
    try:
        os.environ[BOT_ID_ENV] = "from-process"
        os.environ[BOT_SECRET_ENV] = "from-process"
        load_dotenv_into_environ(dotenv)
        assert os.environ[BOT_ID_ENV] == "from-process"
    finally:
        os.environ.clear()
        os.environ.update(env_backup)


# ── 结构不变式：校验先于 SDK ────────────────────────────────────────────

def test_entrypoint_does_not_import_sdk_at_module_level():
    """启动路径上的模块**只能用标准库**。

    ⚠️ 这不是洁癖：跑全量 pytest 的根 venv 里没有 aibot／websockets（design D10 的
    依赖隔离）。启动路径一旦在模块层 import 它们，"缺凭据 → 退出码 2"就会先炸在
    ModuleNotFoundError 上——fail-closed 变成 fail-confusing，而且只在根 venv 里现形。
    """
    for filename in STARTUP_PATH_MODULES:
        path = LIAISON_DIR / filename
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in tree.body:  # 只看模块层，函数体内的延迟 import 不受此限
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        for name in imported:
            root = name.split(".")[0]
            assert root not in FORBIDDEN_MODULE_LEVEL_IMPORTS, (
                f"{filename} 在模块层 import 了 {name}；"
                f"凭据校验必须先于任何 SDK import 发生"
            )
```

跑一次确认因缺模块而失败：

```bash
python3.14 -m pytest tools/liaison/tests/test_liaison_credentials.py -q
```

预期：collection error，`ModuleNotFoundError: No module named 'tools.liaison.config'`。

- [ ] **Step 2: 让测试通过**

创建 `tools/liaison/errors.py`：

```python
"""HR 值守通道服务的异常类型。"""

from __future__ import annotations

from collections.abc import Iterable


class MissingCredentialsError(RuntimeError):
    """凭据缺失／为空／只含空白时抛出，服务据此拒绝启动。

    ⛔ 消息里**只出现变量名，绝不出现取值**。这类报错会被贴进聊天、日志与 issue，
    把取值打出来等于让凭据顺着排障路径散出去——而排障路径恰恰是最不设防的那条。
    """

    def __init__(self, missing_names: Iterable[str]) -> None:
        self.missing_names: tuple[str, ...] = tuple(missing_names)
        joined = "、".join(self.missing_names)
        super().__init__(
            f"HR 值守通道拒绝启动：以下凭据缺失或只含空白字符 → {joined}。"
            f"请在仓库根的 .env 里补齐（真实值只落 .env，⛔ 不入版本管理）。"
        )
```

创建 `tools/liaison/config.py`：

```python
"""启动期凭据读取与校验（fail-closed）。

spec liaison-channel-session「凭据缺失时拒绝启动」要求：任一项缺失、为空或只含
空白字符时，服务必须拒绝启动并指明缺哪一项，⛔ 不得以"已启动但收不到消息"的
状态继续运行。

**只用标准库。** 启动路径上的模块不许 import 任何第三方包——跑全量 pytest 的根
venv 里没有本服务的依赖（design D10 的依赖隔离），一旦依赖它们，这条 fail-closed
行为在根 venv 里就测不出来了。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from tools.liaison.errors import MissingCredentialsError

BOT_ID_ENV = "HR_LIAISON_BOT_ID"
BOT_SECRET_ENV = "HR_LIAISON_BOT_SECRET"

#: 启动前必须齐备的凭据。顺序即报错里的列举顺序，保持稳定便于比对。
REQUIRED_CREDENTIAL_ENV_NAMES: tuple[str, ...] = (BOT_ID_ENV, BOT_SECRET_ENV)


@dataclass(frozen=True, repr=False)
class LiaisonCredentials:
    """一组已校验过的凭据。

    `repr=False` + 自写 `__repr__` 是刻意的：dataclass 默认生成的 `__repr__` 会把
    secret 原样打出来，而 repr 会出现在日志、traceback 与 pytest 断言里。
    `bot_id` 不是秘密（排障要看得见），`bot_secret` 一律遮蔽。
    """

    bot_id: str
    bot_secret: str

    def __repr__(self) -> str:
        return f"LiaisonCredentials(bot_id={self.bot_id!r}, bot_secret=<redacted>)"


def _is_blank(env: Mapping[str, str], name: str) -> bool:
    """缺失／空串／纯空白三种形态一律算"没配"。

    ⛔ 不要放宽成 `name not in env`：企微后台复制粘贴带上尾随空格、或 .env 里写成
    `NAME=` 都会产出一个"存在但没用"的取值。放行它等于让服务带着一个必然失败的
    凭据启动——那正是 spec 要禁止的"已启动但收不到消息"。
    """
    value = env.get(name)
    return value is None or not value.strip()


def load_credentials(env: Mapping[str, str] | None = None) -> LiaisonCredentials:
    """读并校验凭据。任一项没配就 raise，⛔ 不返回半份、⛔ 不返回 None。"""
    source: Mapping[str, str] = os.environ if env is None else env
    missing = [name for name in REQUIRED_CREDENTIAL_ENV_NAMES if _is_blank(source, name)]
    if missing:
        raise MissingCredentialsError(missing)
    return LiaisonCredentials(
        bot_id=source[BOT_ID_ENV].strip(),
        bot_secret=source[BOT_SECRET_ENV].strip(),
    )
```

创建 `tools/liaison/__main__.py`：

```python
"""HR 值守通道服务的入口：`python -m tools.liaison`。

**第 1 章的入口只做一件事**：把 .env 填进进程环境 → 校验凭据 → 缺就退出码 2 退出。
建连、心跳、断线告警在第 7 章，⛔ 本章不实现、⛔ 不留一个假装在跑的空循环。

⛔ 模块层只 import 标准库与本服务自己的模块。任何 SDK import 都必须在凭据校验
**之后**、且写在函数体里——理由见 config.py 的模块 docstring。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from tools.liaison.config import load_credentials
from tools.liaison.errors import MissingCredentialsError

#: 缺凭据的退出码。选 2 而不是 1：1 太容易和"脚本里随便哪一步炸了"混在一起，
#: 2 让 launchd / 人工排障能一眼分辨出"这是配置没配好，不是程序崩了"。
EXIT_MISSING_CREDENTIALS = 2

#: tools/liaison/__main__.py → parents[0]=liaison, [1]=tools, [2]=仓库根
REPO_ROOT = Path(__file__).resolve().parents[2]

#: 测试专用逃生口：让用例指一个不存在的 .env，免得开发机上真实的 .env 把
#: "凭据缺失"这条用例喂绿。⛔ 不写进 .env.example——那会把它暗示成生产用法。
DOTENV_PATH_ENV = "HR_LIAISON_DOTENV_PATH"


def resolve_dotenv_path() -> Path:
    override = os.environ.get(DOTENV_PATH_ENV)
    return Path(override) if override else REPO_ROOT / ".env"


def load_dotenv_into_environ(path: Path) -> None:
    """把 .env 的键值填进 os.environ，**已存在的环境变量不覆盖**。

    优先级口径与 app/config.py 用的 pydantic-settings 一致：进程环境 > .env 文件。
    ⛔ 不引入 python-dotenv：本服务依赖清单独立，标准库能解决的不加依赖，
    每多一个依赖就多一份"这东西会不会跟着被推到 .51"的疑问。

    文件不存在是正常情况（凭据也可以直接从进程环境给），静默返回。
    """
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def main() -> int:
    load_dotenv_into_environ(resolve_dotenv_path())

    try:
        credentials = load_credentials()
    except MissingCredentialsError as exc:
        # 只打变量名，⛔ 不打取值。进程立刻退，⛔ 不进任何等待/重试循环。
        print(str(exc), file=sys.stderr)
        return EXIT_MISSING_CREDENTIALS

    print(
        f"HR 值守通道：凭据校验通过（bot_id={credentials.bot_id}）。"
        f"第 1 章到此为止——建连与消息处理在第 7 章，本章刻意不驻留。",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: 验证**

```bash
python3.14 -m pytest tools/liaison/tests/test_liaison_credentials.py -q
```

预期：`11 passed`，退出码 0。

```bash
python3.14 -m pytest tools/liaison/tests/ -q
```

预期：全部通过（路线 ① 下含 2 条 skip，路线 ② 下 0 skip）。

---

### Task 6: `.env.example` 占位与「受版本管理文件不含凭据」断言

对应 WBS 1.5 与 1.6 的版本管理部分；对应 spec `liaison-channel-session`「凭据缺失时拒绝启动」的第三个场景「版本管理中不含凭据」。

**Files:**
- Modify: `.env.example`
- Create: `tools/liaison/tests/test_liaison_no_secrets_in_vcs.py`

**Interfaces:**
- Produces: `.env.example` 里两个**空值**占位；一条扫描全部受版本管理文件的断言
- Consumes: `git ls-files -z`（口径就是"受版本管理"，⛔ 不用 `os.walk`——那会把 `.gitignore` 掉的 `.env`、`data/`、`.venv/` 一起扫进来，扫出的东西恰恰是允许存在的）

**扫描口径（两条，缺一不可）：**

1. **变量赋值形态**：任何受版本管理的文件里，`HR_LIAISON_BOT_ID` / `HR_LIAISON_BOT_SECRET` / `HR_LIAISON_GROUP_WEBHOOK_URL`（第 6 章会用到，提前纳入）都不得被赋一个**非占位**的值。占位（`<...>`、`${...}`、`replace-me` 之类）放行——不放行会让文档写不成，而文档写不成的下场是大家改去别处写、断言彻底失去覆盖面。
2. **企微 webhook URL 形态**：`qyapi.weixin.qq.com/cgi-bin/webhook/send?key=…` 带真实 key 的完整地址，一律不得出现。

⚠️ **这条断言会扫到它自己所在的文件、也会扫到本计划文档**——两者都是受版本管理的。因此上面的正则用 `^` 锚在行首、并且把占位形态放行：写文档时用 `NAME=` 空值或 `NAME=<占位>`，⛔ 不要在任何受版本管理的文件里写出行首的真实赋值。

- [ ] **Step 1: 写失败的测试**

创建 `tools/liaison/tests/test_liaison_no_secrets_in_vcs.py`：

```python
"""spec 场景「版本管理中不含凭据」：受版本管理的文件里不得有真实凭据。

口径是 `git ls-files`——**受版本管理**，不是"磁盘上存在"。用 os.walk 会把
.gitignore 掉的 .env / data/ / .venv/ 一起扫进来，而那些地方**恰恰是**真实凭据
被允许存在的地方，扫出来只会得到一条永远红的断言。

⚠️ 本文件会扫到它自己、也会扫到 docs/superpowers/plans/ 下的实现计划——两者都
受版本管理。因此正则锚在行首并放行占位形态；写文档时用空值或 <占位>，
⛔ 不要在任何受版本管理的文件里写出行首的真实赋值。
"""

import pathlib
import re
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

CREDENTIAL_ENV_NAMES = (
    "HR_LIAISON_BOT_ID",
    "HR_LIAISON_BOT_SECRET",
    # 第 6 章（群通知外发）会用到，提前纳入扫描面：等到那一章再加，
    # 中间这段时间里它是没人守的。
    "HR_LIAISON_GROUP_WEBHOOK_URL",
)

_ASSIGNMENT = re.compile(
    r"^(?:export[ \t]+)?(?:" + "|".join(CREDENTIAL_ENV_NAMES) + r")[ \t]*=[ \t]*(\S+)",
    re.MULTILINE,
)

# 明显不是真值的占位形态，放行。⛔ 不要往这里加"看起来像假的"具体字符串——
# 那等于给真凭据开一条按字面量豁免的口子。
_PLACEHOLDER = re.compile(r"^(?:<.*>|\$\{[A-Za-z_]+\}|replace-me|your-[a-z-]+|\.\.\.)$")

# 企微群机器人 webhook 的完整地址（带 key）。
_WECOM_WEBHOOK_URL = re.compile(
    r"qyapi\.weixin\.qq\.com/cgi-bin/webhook/send\?key=[0-9A-Za-z-]{8,}"
)

# 单文件读取上限：超过就跳过。大文件基本是二进制/数据，逐字节扫它没有收益。
_MAX_BYTES = 2 * 1024 * 1024


def _tracked_text_files() -> list[pathlib.Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=str(REPO_ROOT), capture_output=True, check=True,
    )
    paths = []
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        path = REPO_ROOT / raw.decode("utf-8")
        if not path.is_file():
            continue  # 已删但索引里还在
        if path.stat().st_size > _MAX_BYTES:
            continue
        paths.append(path)
    return paths


def _read_text(path: pathlib.Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None  # 二进制或读不了，跳过


def test_dotenv_itself_is_not_tracked():
    """.env 是真实凭据的唯一落点，它绝不能进版本管理。"""
    completed = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".env"],
        cwd=str(REPO_ROOT), capture_output=True,
    )
    assert completed.returncode != 0, ".env 被 git 跟踪了，真实凭据正在入库"


def test_no_tracked_file_assigns_a_real_credential_value():
    offenders = []
    for path in _tracked_text_files():
        text = _read_text(path)
        if text is None:
            continue
        for match in _ASSIGNMENT.finditer(text):
            value = match.group(1)
            if _PLACEHOLDER.match(value):
                continue
            line_no = text[: match.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_no}")
    assert not offenders, f"受版本管理的文件里出现了凭据赋值: {offenders}"


def test_no_tracked_file_contains_a_wecom_webhook_url():
    offenders = []
    for path in _tracked_text_files():
        text = _read_text(path)
        if text is None:
            continue
        if _WECOM_WEBHOOK_URL.search(text):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"受版本管理的文件里出现了企微 webhook 完整地址: {offenders}"


@pytest.mark.parametrize("name", ("HR_LIAISON_BOT_ID", "HR_LIAISON_BOT_SECRET"))
def test_env_example_declares_the_placeholder_with_empty_value(name):
    """占位必须在 .env.example 里就位，且取值为空。

    为什么必须为空而不是写个假值：`.env.example` 会被 `sync-to-server.sh` 推到 .51。
    任何非空取值都可能被谁复制成 .env 用，而"看起来配好了、其实是假的"这种状态，
    正是 fail-closed 校验挡不住的那一种——它不缺失，它只是错的。
    """
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    pattern = re.compile(rf"^{re.escape(name)}[ \t]*=[ \t]*$", re.MULTILINE)
    assert pattern.search(text), f".env.example 里缺 {name} 的空值占位"
```

跑一次，确认它因 `.env.example` 里还没有占位而失败：

```bash
python3.14 -m pytest tools/liaison/tests/test_liaison_no_secrets_in_vcs.py -q
```

预期：两条 `test_env_example_declares_the_placeholder_with_empty_value` 失败，其余通过。

- [ ] **Step 2: 让测试通过**

在 `.env.example` **末尾追加**（⛔ 不动已有任何一行）：

```
# ── HR 值守通道（tools/liaison，开发期工具，永不部署 .51）──────────────
# ⛔ 这里只写变量名与说明，**绝不写任何真实取值**。真实值只落仓库根的 .env
#    （.gitignore 已排除）。本文件会被 sync-to-server.sh 推到 .51，写进来的东西
#    等于发到服务器上。
#
# 两项都必须配齐：缺失、空串、只含空白字符任一 → 服务**拒绝启动**并指明缺哪一项
# （fail-closed，不以"已启动但收不到消息"的状态驻留）。
#
# 取值来源：Shao Peishen 在企业微信管理后台注册的**新** aibot 应用（⛔ 与 Windows
# 侧那套不共用，见 design D1）。属账号级操作，无法代劳。
HR_LIAISON_BOT_ID=
HR_LIAISON_BOT_SECRET=
```

- [ ] **Step 3: 验证**

```bash
python3.14 -m pytest tools/liaison/tests/test_liaison_no_secrets_in_vcs.py -q
```

预期：`5 passed`。

反证一次（确认这条断言真的能抓到东西，⛔ 不要跳过这步——一条永远绿的安全断言比没有更危险）：

```bash
printf 'HR_LIAISON_BOT_SECRET=%s\n' "aaaabbbbccccdddd" > /tmp/liaison_canary.env
cp /tmp/liaison_canary.env ./_canary_secret.env
git add --intent-to-add ./_canary_secret.env
python3.14 -m pytest tools/liaison/tests/test_liaison_no_secrets_in_vcs.py -q ; echo "反证_EXIT=$?"
git rm --cached ./_canary_secret.env >/dev/null && rm -f ./_canary_secret.env /tmp/liaison_canary.env
python3.14 -m pytest tools/liaison/tests/test_liaison_no_secrets_in_vcs.py -q
```

预期：中间那次 `反证_EXIT=1` 且失败信息里出现 `_canary_secret.env:1`；清理后最后一次回到 `5 passed`。
⚠️ **收尾前必须确认 `git status --porcelain` 里没有 `_canary_secret.env` 残留**，⛔ 绝不能把它提交上去。

---

### Task 7: 把 `tools/liaison/tests/` 接进根 `pyproject.toml` 的 `testpaths`

对应 opener 约束 6（"推荐加进 testpaths，让全量 pytest 能跑到它；⛔ 不另起一套测试跑法"）。

**Files:**
- Modify: `pyproject.toml`（**只改 `testpaths` 一行**，⛔ 不动 `[project]`、⛔ 不加任何依赖）
- Create: `tools/liaison/tests/test_liaison_testpaths_wiring.py`

**Interfaces:**
- Produces: `testpaths = ["tests", "tools/liaison/tests"]`
- Consumes: 无

**为什么是"加进 testpaths"而不是给 `tools/liaison/` 单起一套跑法：**

不接进去，本服务的测试就只有"记得手敲那条路径"的人才跑得到。这类测试的失效是**静默的**——不报错、不失败，只是从此没人跑，直到某次改动悄悄破坏了依赖隔离而没人发现。⛔ 另起一套跑法（单独的 `pytest.ini` / 独立 CI 步骤）等于同时维护两条测试通道，是同一个问题换了个更贵的形式。

**为什么这样改对 `.51` 安全（已实测，⛔ 不要只凭直觉接受）：**

`pyproject.toml` **在** `sync-to-server.sh:48-56` 的 `SYNC_PATHS` 里，会被推到 `.51`；而 `tools/` **不在**，不会被推。于是 `.51` 上的 `pyproject.toml` 会带一个指向不存在目录的 `testpaths` 条目。

实测结论：pytest 把 `testpaths` 当 **glob** 处理，**不匹配任何路径的条目被静默跳过**，退出码仍为 0。已在 **pytest 8.3.4**（与根 `requirements.txt` 同版本）与 **pytest 9.1.1** 两个版本、Python 3.14.6 上各验一次。Step 3 把这次实测固化成一条可重跑的验证。

- [ ] **Step 1: 写失败的测试**

创建 `tools/liaison/tests/test_liaison_testpaths_wiring.py`：

```python
"""本目录必须被根 pyproject 的 testpaths 收进去。

不接进去，这套测试就只有"记得手敲路径"的人跑得到——而这种失效是静默的：
不报错、不失败，只是从此没人跑，直到某次改动悄悄破坏了依赖隔离才爆出来。

⛔ 不要改成"另起一套跑法"来绕过这条断言（独立 pytest.ini / 独立 CI 步骤）。
两条测试通道是同一个问题换了个更贵的形式（opener 约束 6）。
"""

import pathlib
import tomllib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

LIAISON_TESTPATH = "tools/liaison/tests"


def _ini_options() -> dict:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["tool"]["pytest"]["ini_options"]


def test_liaison_tests_are_in_root_testpaths():
    testpaths = _ini_options()["testpaths"]
    assert "tests" in testpaths, "根 tests/ 不能被挤掉"
    assert LIAISON_TESTPATH in testpaths, (
        "tools/liaison/tests 没进根 testpaths，全量 pytest 跑不到本服务的测试"
    )


def test_pythonpath_makes_the_tools_package_importable():
    """`from tools.liaison...` 能 import，靠的就是这一条。"""
    assert "." in _ini_options()["pythonpath"]


def test_no_liaison_dependency_leaked_into_pyproject():
    """本 Task 只许动 testpaths。依赖仍然只能在 tools/liaison/requirements.txt 里。"""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = " ".join(data["project"].get("dependencies", [])).lower()
    for forbidden in ("wecom-aibot-python-sdk", "websockets"):
        assert forbidden not in declared
```

- [ ] **Step 2: 让测试通过**

把 `pyproject.toml` 里的

```toml
testpaths = ["tests"]
```

改成（**只改这一处**）：

```toml
# tools/liaison/tests 是 HR 值守通道服务（开发期工具，永不部署 .51）的测试。
# 接进来是为了让全量 pytest 一次跑到它——不接，它就只有"记得手敲路径"的人跑得到，
# 而那种失效是静默的：不报错、不失败，只是从此没人跑。
#
# ⚠️ 这份 pyproject.toml 会被 sync-to-server.sh 推到 .51，而 tools/ 不会。于是 .51 上
# 这一条会指向一个不存在的目录——**这是安全的，已实测**：pytest 把 testpaths 当 glob
# 处理，不匹配任何路径的条目被静默跳过，退出码仍为 0（pytest 8.3.4 与 9.1.1、
# Python 3.14.6 上各验过一次）。
# ⛔ 不要因此改成"给 tools/liaison 单起一套 pytest 配置"——两条测试通道是同一个问题
#    换了个更贵的形式。
testpaths = ["tests", "tools/liaison/tests"]
```

- [ ] **Step 3: 验证（三件事，缺一不可）**

① 全量 `pytest` 真的跑到了本服务的测试：

```bash
python3.14 -m pytest -q 2>&1 | tail -5
python3.14 -m pytest --collect-only -q 2>&1 | grep -c '^tools/liaison/tests/'
```

预期：全量全绿；第二条输出的用例数 > 0，且与单独跑 `pytest tools/liaison/tests/ --collect-only -q` 的条数一致。

② 根 `tests/` 一条都没少（⛔ 确认没把原有测试挤掉）：

```bash
python3.14 -m pytest tests/ -q 2>&1 | tail -3
```

预期：与本 Task 改动之前的条数一致、全绿。

③ **重跑一次"不匹配的 testpaths 条目被静默跳过"的实测**，把 `.51` 的安全性从"我记得是这样"变成"刚验过"：

```bash
rm -rf /tmp/liaison_testpaths_probe && mkdir -p /tmp/liaison_testpaths_probe/tests
printf 'def test_ok():\n    assert True\n' > /tmp/liaison_testpaths_probe/tests/test_probe.py
printf '[tool.pytest.ini_options]\ntestpaths = ["tests", "tools/liaison/tests"]\n' > /tmp/liaison_testpaths_probe/pyproject.toml
(cd /tmp/liaison_testpaths_probe && python3.14 -m pytest -q ; echo "缺失路径_EXIT=$?")
rm -rf /tmp/liaison_testpaths_probe
```

预期：`1 passed` 且 `缺失路径_EXIT=0`——即 `tools/liaison/tests` 不存在时 pytest 不报错、不非零退出，`.51` 上不受影响。
若这一步在实际的 pytest 版本上**不成立**（报 `file or directory not found` 或非零退出），⛔ 不要硬改测试让它绿：这说明"接进 testpaths"这个决定对 `.51` 有副作用，须在 run-build 报告里登记为红灯并把 `.51` 的影响写清楚，由 Shao Peishen 决定是否改走别的接法。

---

## Spec 覆盖对照

输入 spec：`openspec/changes/hr-wecom-aibot-liaison/specs/liaison-channel-session/spec.md`。

| spec Requirement | 场景 | 本章覆盖 | 落在哪 |
|---|---|---|---|
| 凭据缺失时拒绝启动 | 凭据未填写 | ✅ | Task 5 `test_both_credentials_absent` / `test_entrypoint_exits_nonzero_and_names_missing_items` |
| 凭据缺失时拒绝启动 | 凭据为空白字符串 | ✅ | Task 5 `test_empty_string_counts_as_missing` / `test_whitespace_only_counts_as_missing` |
| 凭据缺失时拒绝启动 | 版本管理中不含凭据 | ✅ | Task 6 `test_no_tracked_file_assigns_a_real_credential_value` / `test_no_tracked_file_contains_a_wecom_webhook_url` / `test_dotenv_itself_is_not_tracked` |
| 存活戳区分空闲与断线 | 两个场景 | ⬜ **第 7 章** | 本章不做（WBS 7.1） |
| 连接中断窗口必须被记录 | 两个场景 | ⬜ **第 7 章** | 本章不做（WBS 7.2 / 7.3） |
| 中断窗口必须显式告警且说明可能漏消息 | 两个场景 | ⬜ **第 7 章** | 本章不做（WBS 7.4 / 7.5） |
| 断线后自动恢复接收 | 两个场景 | ⬜ **第 7 章** | 本章只在 Task 4（路线 ②）备下 `compute_backoff_delays` 纯函数；接线在 WBS 7.6 |

⚠️ 本章**刻意只覆盖一条 Requirement**。tasks.md 第 1 章的验收原文就是「`liaison-channel-session` 中**「凭据缺失时拒绝启动」一条要求**的全部场景通过；SDK 路线已定且有 findings 落档」——其余四条 Requirement 归第 7 章。⛔ 不要在本章顺手把它们做掉：那会让第 7 章的两阶段 review 失去审查对象。

---

## 红灯与观察项（run-build 报告里必须原样带出）

1. **⏸ 留步：真实建连未验。** 需要 Shao Peishen 在企业微信管理后台注册**新** aibot 应用取得 `BotID` / `Secret`（proposal「Impact · 人」已写明是账号级操作，无法代劳）。本章只验 SDK 的能力面是否齐备，⛔ 不验能否连上企微服务端。真实建连归第 7 章，且必须在拿到凭据之后。
2. **⚠️ 待订正：`proposal.md`「Impact · 不触碰」列了 `pyproject.toml`，而 Task 7 要改它的 `testpaths` 一行。** 两处口径需要对齐：
   - 该条的**本意**是依赖隔离——`pyproject.toml` 会被同步到 `.51`，本服务的**依赖**不得从这里溜过去（design D10 通篇讲的都是依赖清单）。Task 7 改的是 `testpaths`，**不动任何依赖**，且 Task 7 自带 `test_no_liaison_dependency_leaked_into_pyproject` 守住这一点。
   - 但本计划**不改 proposal.md**（不在本交付单元的文件范围内）。**登记为待办**：归档该变更包之前，把 proposal.md 那一行从「不触碰 `pyproject.toml`」订正为「不往 `pyproject.toml` 添加任何依赖；`testpaths` 因 tools/liaison/tests 接入而新增一条」。
   - ⛔ 不要因为这处口径没对齐就跳过 Task 7——opener 约束 6 明确要求接进 testpaths 且⛔ 不另起一套测试跑法。
3. **⚠️ 第 7 章的接线约束（本章只记录，⛔ 不实现）**：若走路线 ①，`WSClientOptions.max_reconnect_attempts` 的默认值 **10** 与 spec「自动重试建立连接**直至成功**……服务不退出」冲突，第 7 章 7.6 必须显式传 `-1`。⛔ 不许用默认值。
4. **⚠️ SDK 的 `__version__` 与发行版本对不上**（实测模块里写 `1.0.0`、发行版本 `1.0.2`）。任何"用 `__version__` 校验装对没有"的写法都会误判，一律以 `importlib.metadata.version()` 的发行版本为准。

---

## 端到端提取验证记录（2026-09-08，计划编写期）

按 `spec-to-plan` 技能第 6 步做过一轮：把本计划里的全部代码块**原样提取**到 `/tmp` 的
一份仓库骨架里（复制真实的 `sync-to-server.sh` / `requirements.txt` / `pyproject.toml` /
`.env.example` / `.gitignore`，`git init` 后提交），用 **pytest 8.3.4 + Python 3.14.6** 跑。

| 跑什么 | 结果 |
|---|---|
| Task 1 + 5 + 6 + 7 的四个测试文件 | **24 passed**（5 + 11 + 5 + 3，与各 Task「预期」逐条对上） |
| Task 4（路线 ②）`test_liaison_minimal_ws.py`（findings 置为 ②） | **7 passed** |
| Task 3（路线 ①）`test_liaison_sdk_smoke.py`（findings 置为 ①，SDK 真装上） | **4 passed**，含真实 `WSClientOptions` 字段断言与发行版本比对 |
| Task 2 的探针 `probe_sdk_py314.py` | 退出码 0，JSON 输出 `importable=true` / `has_ws_client=true` / `module_name="aibot"` / `distribution_version="1.0.2"` / `module_version_attr="1.0.0"` |
| Task 6 Step 3 的反证（造一个带真值的受版本管理文件） | 断言**如期变红**，失败信息为 `_canary_secret.env:1`；清理后回到全绿 |
| 接进 `testpaths` 后的全量 `pytest` | **29 passed**，`--collect-only` 里 `tools/liaison/tests/` 收到 **28** 条 |
| `testpaths` 里含不存在路径时的 pytest 行为 | `1 passed`、退出码 **0**（pytest 8.3.4 与 9.1.1 各验一次）——`.51` 不受影响 |
| 本计划文档自身过一遍 Task 6 的扫描规则 | **无命中**（文档里的示例全是空值或占位，不会把自己扫红） |

**这一步证明了什么、没证明什么**：证明代码可执行且内部自洽（提取即跑通，无转录误差、无
语法/import 错误、断言口径真的能抓到东西）。**⛔ 不证明符合 spec**——测试与被测代码出自
同一份文档，spec 合规由 `run-build` 的两阶段 review 负责，这一步不是它的替代品。

⚠️ 上述 SDK 相关结果是在 `/tmp` 的一次性 venv 里得到的，**⛔ 不能冒充 Task 2 的产出**：
Task 2 必须在仓库内重跑并把真实输出落进 findings 文档。

---

## 交付前自查清单

- [ ] `grep -c '^### Task ' docs/superpowers/plans/2026-09-08-hr-wecom-aibot-liaison-unit1-channel-skeleton.md` ≥ 3（本计划为 7）
- [ ] 有 Global Constraints 段，内容与 `CLAUDE.md` 一致
- [ ] spec `liaison-channel-session`「凭据缺失时拒绝启动」的三个场景各指到至少一个 Task
- [ ] 每个 Task 有确切文件路径、完整代码、确切命令与预期输出
- [ ] 无 TBD / TODO / "适当处理错误" 类占位符（Task 3/4 里 `<Task 2 实测到的发行版本>` 是**刻意的**——它的取值由 Task 2 的实测产生，写死任何具体数字都是编造）
- [ ] 前后 Task 的常量名一致：`BOT_ID_ENV` / `BOT_SECRET_ENV` / `REQUIRED_CREDENTIAL_ENV_NAMES` / `EXIT_MISSING_CREDENTIALS` / `DOTENV_PATH_ENV`
- [ ] 本章**没有**任何 `effect_*` 函数、没有建表、没有事务——幂等与存储归第 2 章（铁律 1 在本章无适用对象，这是范围问题不是遗漏）
- [ ] 本章不涉及 AI 评分，`evidence_ref` 类断言无适用对象

---

## 下一步

本章 final review 通过后：

1. 回勾 `openspec/changes/hr-wecom-aibot-liaison/tasks.md` 第 1 章的 1.1–1.6 六个 checkbox。
2. 按 Task 2 的路线结论，为第 2 章（存储基座与幂等不变式）出计划——第 2 章才是 `effect_log`、`idempotent_effect` 接入与恒等不变式脚手架的地盘。
3. 把上面「红灯与观察项」第 2 条（`proposal.md` 的 `pyproject.toml` 口径订正）挂进变更包的待办，⛔ 不要拖到归档那一刻才发现。
