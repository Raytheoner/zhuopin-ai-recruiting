# 存储基座与幂等不变式（hr-wecom-aibot-liaison 交付单元 2）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 HR 企微值守服务立起一个**结构上不可能出错**的存储基座：独立的 `data/liaison.db`、一张与产品库 `effect_log` 同构的幂等表、消息台账 `liaison_message` 与队列真身 `liaison_task` 两张表，并把 `app.storage.idempotency.idempotent_effect` 单向接进来，用测试把工程铁律 1 的**恒等不变式**（每个 `effect_*` 的 `effect_log` 条数与其业务表行数按 `thread_id` 恒等）钉死在脚手架上。本章**只建表与验证机制**，队列的业务写入逻辑在第 5 章。

**Architecture:**

```
data/liaison.db                 ← 独立库，⛔ 不与 data/demo.db 混库（design D5）；已被 .gitignore:11 的 data/ 覆盖
tools/liaison/
├── storage/
│   ├── __init__.py
│   ├── schema.py               ← 全部 DDL 字面量：effect_log（同构）+ liaison_message + liaison_task + 三条约束
│   ├── db.py                   ← get_connection() / init_schema()；本服务**唯一**的事务管理者入口
│   └── effects.py              ← effect_archive_message / effect_enqueue_task，均由 idempotent_effect 装饰
└── tests/
    ├── test_liaison_schema.py          ← 2.1 同构断言 + 2.2 三态与时间戳约束
    ├── test_liaison_effects.py         ← 2.3 单一事务管理者 + 2.4 恒等不变式 + 2.5 异常不留幂等记录
    └── test_app_does_not_import_tools.py ← 2.6 结构性单向断言
```

依赖方向**单向且只有一条**：

```
tools/liaison/storage/effects.py  ──import──▶  app.storage.idempotency.idempotent_effect
app/**                            ──✗──▶      tools/**        ← Task 6 用 AST 把这条钉成断言
```

四条支撑这套结构的判断：

1. **复用的是机制与代码，不是同一个文件。** `app/storage/idempotency.py` 只 import `functools` / `logging` / `sqlite3` / `typing`（已核，无任何 `app.*` 内部依赖），因此 `tools/liaison` import 它不会把产品的 DB 层、配置层、FastAPI 依赖一起拖进来。这是这条复用能成立的前提——**换成 import `app.storage.db` 就不成立了，⛔ 不要那样做。**
2. **`effect_log` 必须与 `app/storage/db.py:59-67` 同构，且同构性要能被机器判定。** 「照着抄一遍」在 review 时看不出差别，半年后产品库加了一列而这边没加也不会有任何症状。Task 1 的做法是把两份 schema 各自建进内存库，用 `PRAGMA table_info` + `PRAGMA index_list` + `PRAGMA index_info` **逐字段比对**（本机已实测通过），差一列当场红。
3. **本服务不引入 checkpointer（design D6），所以连接上不存在第二个事务管理者——但这件事必须被断言，不能靠"我们记得没引入"。** Task 3 用两道判据：① AST 扫 `tools/liaison/` 下非测试代码里 `.commit()` / `.rollback()` 的调用点，白名单只允许 `storage/db.py::init_schema` 一处；② 运行期用 spy 连接数出每次 effect 调用的 commit/rollback 次数。`docs/findings/2026-08-13-sqlite-事务归属冲突.md` 记的那场事故，成因正是"没人断言过谁是事务的主人"。
4. **三态用 `CHECK` 钉在存储层，emoji 只活在渲染层。** `send_status` 存 `pending` / `deferred` / `pushed` 三个枚举值。⛔ 存 `🆕 待发` 这类 emoji 串——参考服务把展示串当状态存，导致改一次文案就要迁一次数据，且任何拼写偏差都变成一个新状态。本机已实测：写入 `'🆕 待发'` 被 `CHECK` 拒绝。

**Tech Stack:** Python 3.14（`/opt/homebrew/bin/python3.14`，本机实测 3.14.6；根 `pyproject.toml:7` 钉死 `>=3.14,<3.15`）· 标准库 `sqlite3` / `ast` / `pathlib` / `hashlib` · pytest 8.3.4（根 `requirements.txt` 同版本）· 复用 `app.storage.idempotency.idempotent_effect`。⛔ 本章不引入任何第三方依赖，`tools/liaison/requirements.txt` 一行不改。

---

## Global Constraints

以下逐字复制自 `CLAUDE.md`「工程铁律」「合规红线」「部署约束」，以及本交付单元 opener 的四条范围约束。**reviewer 以此为注意力透镜。**

### 来自 CLAUDE.md（逐字）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
   *为什么*：业务写失败而幂等记录成功 → 系统判定"已执行"→ 永不重试。**幂等本是防重复的保护，拆开事务后变成永久丢失的保证。** 实证：`.51` 现网 2026-08-10 与 08-12 各丢一轮 `outbox`（幂等记录已落），用户没收到回复且永远不会补发，见 `docs/findings/2026-08-13-sqlite-事务归属冲突.md` §8.5。
2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。
3. **企微回调先落库再处理**：只推一次、5 秒无响应即丢弃。回调接口只做签名校验 + 落库 + 返回 200。
4. **模型全部走境内**，简历数据不出境。
5. **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。
6. **部署约束 4**：**目标服务器是 Windows，没有 Docker**。部署形态 = Python venv + Windows 计划任务 + 防火墙规则 + scp 推送。不要引入容器。

### 来自本交付单元 opener（逐字，四条）

1. 铁律 1 逐字：`effect_*` 独占 + 幂等键 `{thread_id}:{node_name}:{business_key}`（`thread_id` ＝私聊 `userid`／群聊 `chatid`，`business_key` ＝企微 `msgid`）；幂等记录与业务写同一事务、同一连接、无第二个事务管理者；恒等不变式有测试
2. D5：独立 `data/liaison.db`，⛔ 不与 `data/demo.db` 混库；⛔ 不改 `app/storage/` 任何文件——只 import
3. 2.2 `send_status` 用 `CHECK` 钉死三态枚举值，⛔ 存 emoji
4. D10 落点；2.6 结构断言（`app/` 下无模块 import `tools/`）作为独立 Task

### 本章的四条"不做"（避免越界写进第 3–8 章的地盘）

- ⛔ **不写任何附件落盘代码。** 附件的字节流读写、`fsync` + 原子 `rename`、SHA-256 校验是**第 4 章**（design D4）。本章的 `liaison_message.attachments_json` 只是一个列，Task 2 给它默认值 `'[]'`，⛔ 不实现写入。
- ⛔ **不写队列的业务入队逻辑。** 白名单判定、内容归一化、长度守卫、Markdown 渲染全在**第 3／第 5 章**。本章的 `effect_enqueue_task` 只做「一条消息 → 一条队列行」的最小写入，存在的唯一理由是让 `liaison-task-queue`「入队幂等且与幂等记录原子提交」的两个场景**现在就能在脚手架上跑通**（tasks.md 第 2 章验收原文）。
- ⛔ **不实现留存期清理。** `liaison-message-archive`「归档数据的留存期有上限」（180 天，design D13）是**第 7 章**。
- ⛔ **不改 `app/storage/` 下任何文件，不改根 `requirements.txt` / `pyproject.toml` / `sync-to-server.sh` / `deploy-server.ps1`。** 本章新增的测试落在 `tools/liaison/tests/`，交付单元 1 已把该目录接进根 `pyproject.toml:21` 的 `testpaths`，全量 `pytest` 自动跑得到，**无需再动配置**。

---

## 本计划对 spec Requirement 的覆盖

| spec | Requirement | 覆盖它的 Task |
|---|---|---|
| `liaison-task-queue` | 队列真身为结构化存储，任意消息内容不得破坏其结构 | Task 2（表即真身）、Task 4（竖线/换行/控制字符内容原样回读） |
| `liaison-task-queue` | 入队幂等且与幂等记录原子提交 | Task 3、Task 4、Task 5 |
| `liaison-task-queue` | 每条队列条目可回指来源消息 | Task 2（四个来源列 NOT NULL + FK）、Task 4 |
| `liaison-task-queue` | 队列条目的发送状态为受约束的三态 | Task 2（`CHECK` 三态 + `pushed_at` 配对 `CHECK` + 转移 `TRIGGER`） |
| `liaison-task-queue` | 文本视图为只读导出物且单向 | **第 5 章**（本章只提供"真身在库里"这个前提，渲染不在范围内）——已在「本章的四条不做」登记 |
| `liaison-message-archive` | 归档键细到单条消息 | Task 2（`liaison_message.msgid` 为主键，日期不进键） |
| `liaison-message-archive` | 重复投递不产生第二份归档 | Task 3、Task 4、Task 5 |
| `liaison-message-archive` | 附件按字节流处理，完整性校验不依赖文本可解码性 | **第 4 章**——已在「本章的四条不做」登记 |
| `liaison-message-archive` | 台账中存在的消息其材料必然可取回 | **第 4 章**（文件先落、DB 后写的顺序）——已在「本章的四条不做」登记 |
| `liaison-message-archive` | 文件名归一化只处理路径安全 | **第 4 章**——已在「本章的四条不做」登记 |
| `liaison-message-archive` | 归档数据的留存期有上限 | **第 7 章**——已在「本章的四条不做」登记 |

**留步登记**：上表 5 条标「第 4／5／7 章」的 Requirement 本章不实现。它们不是遗漏，是 tasks.md 的章节划分使然（第 2 章验收原文只要求「入队幂等且与幂等记录原子提交」一条要求的两个场景跑通）。⛔ reviewer 不要因为它们没被实现而判本计划不完整；但**也不要在本章顺手实现它们**。

---

### Task 1: `effect_log` 同构建表与连接工厂

**Files:**
- Create: `tools/liaison/storage/__init__.py`
- Create: `tools/liaison/storage/schema.py`
- Create: `tools/liaison/storage/db.py`
- Test: `tools/liaison/tests/test_liaison_schema.py`
- Read-only reference: `app/storage/db.py:59-67`（⛔ 不修改）

**Interfaces:**
- Produces:
  - `tools.liaison.storage.schema.EFFECT_LOG_SCHEMA: str` —— 只含 `effect_log` 表与其唯一索引的 DDL
  - `tools.liaison.storage.schema.SCHEMA: str` —— 本服务全部 DDL（Task 1 时 == `EFFECT_LOG_SCHEMA`，Task 2 追加两张表）
  - `tools.liaison.storage.db.DEFAULT_DB_PATH: pathlib.Path` —— 仓库根下的 `data/liaison.db`
  - `tools.liaison.storage.db.get_connection(db_path: str | os.PathLike[str] | None = None) -> sqlite3.Connection`
  - `tools.liaison.storage.db.init_schema(conn: sqlite3.Connection) -> None`
- Consumes: 无（本章第一个 Task）

- [ ] **Step 1: 写失败的测试**

创建 `tools/liaison/tests/test_liaison_schema.py`：

```python
"""`data/liaison.db` 的 schema 断言。

核心是一条**机器可判定的同构性**：本服务的 `effect_log` 必须与产品库
`app/storage/db.py:59-67` 逐列、逐索引一致。⛔ 不要把这条改成"人工看一眼"——
产品库将来加一列而这边没跟上，不会有任何症状，直到某天两边的幂等记录对不上。

⛔ 断言失败时不要改断言去迁就实现。失败意味着两份 schema 真的分叉了。
"""

import sqlite3

import pytest

from app.storage.db import SCHEMA as APP_SCHEMA
from tools.liaison.storage import db as liaison_db
from tools.liaison.storage.schema import EFFECT_LOG_SCHEMA, SCHEMA


def _table_shape(conn: sqlite3.Connection, table: str) -> tuple:
    """把一张表的结构压成可比较的值：列定义 + 索引 + 每个索引的列。

    `PRAGMA table_info` 每行是 (cid, name, type, notnull, dflt_value, pk)。
    **刻意丢掉 cid**——它只是序号，两份 schema 里 effect_log 是各自库里的第几张表
    与同构性无关；保留 name/type/notnull/default/pk 五项。
    """
    columns = [tuple(row)[1:] for row in conn.execute(f"PRAGMA table_info({table})")]
    indexes = sorted((row[1], row[2]) for row in conn.execute(f"PRAGMA index_list({table})"))
    index_columns = {
        row[1]: [entry[2] for entry in conn.execute(f"PRAGMA index_info('{row[1]}')")]
        for row in conn.execute(f"PRAGMA index_list({table})")
    }
    return columns, indexes, index_columns


def test_effect_log_is_isomorphic_to_product_schema():
    """同列、同类型、同 NOT NULL、同主键、同唯一索引。"""
    product = sqlite3.connect(":memory:")
    product.executescript(APP_SCHEMA)
    liaison = sqlite3.connect(":memory:")
    liaison.executescript(EFFECT_LOG_SCHEMA)

    assert _table_shape(liaison, "effect_log") == _table_shape(product, "effect_log")


def test_effect_log_key_is_unique():
    """唯一索引是幂等的唯一权威（见 app/storage/idempotency.py 里那段注释）。"""
    conn = sqlite3.connect(":memory:")
    conn.executescript(EFFECT_LOG_SCHEMA)
    conn.execute(
        "INSERT INTO effect_log VALUES ('u1:effect_archive_message:m1', 'u1', "
        "'effect_archive_message', 'm1', datetime('now'))"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO effect_log VALUES ('u1:effect_archive_message:m1', 'u1', "
            "'effect_archive_message', 'm1', datetime('now'))"
        )


def test_liaison_db_does_not_contain_product_tables(tmp_path):
    """D5：独立库。产品表出现在这里，说明有人把两个库混了。

    `job`（M1 画像任务）与 `analysis_run`（AI 审计）是产品库的标志性表，
    任一出现即失败。
    """
    conn = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(conn)
    names = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert "job" not in names
    assert "analysis_run" not in names


def test_default_db_path_is_liaison_not_demo():
    """⛔ 不与 data/demo.db 混库（design D5）。"""
    assert liaison_db.DEFAULT_DB_PATH.name == "liaison.db"
    assert liaison_db.DEFAULT_DB_PATH.parent.name == "data"


def test_init_schema_is_idempotent(tmp_path):
    """建表走 CREATE TABLE IF NOT EXISTS，重复初始化必须无害。"""
    path = tmp_path / "liaison.db"
    conn = liaison_db.get_connection(path)
    liaison_db.init_schema(conn)
    liaison_db.init_schema(conn)  # ⛔ 不许抛
    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 0


def test_connection_keeps_legacy_transaction_control(tmp_path):
    """事务归属的地基：连接必须留在隐式事务模式。

    `autocommit = sqlite3.LEGACY_TRANSACTION_CONTROL`（值 -1）且
    `isolation_level == ""` 时，一次 `conn.execute("INSERT ...")` 会开启一个隐式
    事务，直到有人 `commit()`。`idempotent_effect` 正是靠这一点让业务写与
    `effect_log` 写落在**同一个 BEGIN** 里。

    ⛔ 谁把 isolation_level 改成 None（或 autocommit=True），每条语句都会自动提交，
    业务写与幂等记录就变成两个事务——铁律 1 当场破掉，而且**不报错、无症状**，
    只有在崩溃恢复时才以"消息永远收不到"的形式暴露。这条断言就是那道闸。
    """
    conn = liaison_db.get_connection(tmp_path / "liaison.db")
    assert conn.autocommit == sqlite3.LEGACY_TRANSACTION_CONTROL
    assert conn.isolation_level == ""


def test_schema_contains_effect_log_schema():
    """SCHEMA 是全量 DDL，必须包含 effect_log 那一段（Task 2 会往 SCHEMA 追加两张表）。"""
    assert EFFECT_LOG_SCHEMA.strip() in SCHEMA
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `python3.14 -m pytest tools/liaison/tests/test_liaison_schema.py -v`
Expected: 收集阶段即 FAIL —— `ModuleNotFoundError: No module named 'tools.liaison.storage'`

- [ ] **Step 3: 写最小实现**

创建 `tools/liaison/storage/__init__.py`：

```python
"""HR 值守通道服务的存储层：独立 `data/liaison.db`。

⛔ 不与 `data/demo.db` 混库（design D5）。两个常驻进程写同一个 SQLite 文件 =
写锁竞争 + 间歇性的 `database is locked`。

复用方向**单向**：本包可以 `import app.storage.idempotency`，
⛔ `app/` 下任何模块不得 import `tools/`（由 test_app_does_not_import_tools.py 守着）。
"""
```

创建 `tools/liaison/storage/schema.py`：

```python
"""`data/liaison.db` 的全部 DDL。

DDL 以字面量常量的形式集中在本文件，**不散落在建表函数里**：Task 1 的同构断言要把
`EFFECT_LOG_SCHEMA` 单独建进一个内存库去和产品库比对，散在函数里就比不了。
"""

from __future__ import annotations

#: 与 `app/storage/db.py:59-67` **逐字同构**。同列、同类型、同 NOT NULL、同主键、
#: 同唯一索引——由 tests/test_liaison_schema.py 的同构断言机器判定。
#:
#: ⛔ 改这段之前先想清楚：产品库那份是不是也要改？两边分叉不会报错，
#: 只会让"复用同一个 idempotent_effect"这句话在某天悄悄变成假的。
EFFECT_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS effect_log (
    effect_key TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    node_name TEXT NOT NULL,
    business_key TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_effect_log_key ON effect_log (effect_key);
"""

#: 本服务的全量 DDL。Task 2 会在这里追加 liaison_message 与 liaison_task。
SCHEMA = EFFECT_LOG_SCHEMA
```

创建 `tools/liaison/storage/db.py`：

```python
"""连接工厂与 schema 初始化。

**本模块是本服务唯一的事务管理者入口**：除 `init_schema` 之外，`tools/liaison/`
下的非测试代码一律 ⛔ 不许调用 `conn.commit()` / `conn.rollback()`——提交由
`app.storage.idempotency.idempotent_effect` 独占负责，这样"业务写与幂等记录同一个
BEGIN"才是结构上成立的，而不是靠每个调用点自觉。
这条约束由 tests/test_liaison_effects.py 的 AST 断言守着（白名单只有 init_schema）。
"""

from __future__ import annotations

import os
import pathlib
import sqlite3

from tools.liaison.storage.schema import SCHEMA

# tools/liaison/storage/db.py → parents[0]=storage, [1]=liaison, [2]=tools, [3]=仓库根
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

#: ⛔ 不是 data/demo.db。独立库是 design D5 的结论，不是可调参数。
#: `data/` 已被 .gitignore:11 覆盖，库文件不会误入版本管理。
DEFAULT_DB_PATH = REPO_ROOT / "data" / "liaison.db"


def get_connection(db_path: str | os.PathLike[str] | None = None) -> sqlite3.Connection:
    """开一个连接。

    ⛔ **不要传 `isolation_level=None`、也不要设 `autocommit=True`。** 那会让每条语句
    各自提交，业务写与 `effect_log` 写从此分处两个事务——工程铁律 1 当场破掉，
    且**不报错、无症状**。默认的 LEGACY_TRANSACTION_CONTROL 正是这里需要的语义。
    """
    path = DEFAULT_DB_PATH if db_path is None else pathlib.Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    # 外键：liaison_task.msgid → liaison_message.msgid 的引用完整性靠它生效。
    # SQLite 默认关闭外键，⛔ 不设这条则 Task 2「条目缺少来源信息不允许写入」形同虚设。
    conn.execute("PRAGMA foreign_keys = ON")
    # 本服务是单进程单连接，但库文件可能被只读的导出/排查命令同时打开。
    # WAL 让读不阻塞写；busy_timeout 是纵深防御，不是并发写的许可。
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """幂等建表。

    这是**本服务唯一被允许 commit 的非 effect 路径**（见模块 docstring 的白名单）。
    建表发生在任何 effect 之前，此时连接上没有未提交的业务写，因此这次 commit
    不可能把半截业务事务带下去。
    """
    conn.executescript(SCHEMA)
    conn.commit()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3.14 -m pytest tools/liaison/tests/test_liaison_schema.py -v`
Expected: PASS —— 7 passed

- [ ] **Step 5: 跑全量确认没碰坏别处**

Run: `python3.14 -m pytest -q`
Expected: 全绿，退出码 0。⚠️ `app/outbound/delivery.py:12` 会打一条 `SyntaxWarning: "\z" is an invalid escape sequence`——**这是既有现象，不是本 Task 引入的**，⛔ 不要顺手去改它（不在本交付单元范围内）。

- [ ] **Step 6: 提交**

```bash
git add tools/liaison/storage/__init__.py tools/liaison/storage/schema.py tools/liaison/storage/db.py tools/liaison/tests/test_liaison_schema.py
git commit -m "feat(liaison): data/liaison.db 的 effect_log 与产品库同构 + 连接工厂"
```

---

### Task 2: 消息台账与队列真身两张表（含三态约束）

**Files:**
- Modify: `tools/liaison/storage/schema.py`（追加两张表的 DDL，`SCHEMA` 改为拼接）
- Modify: `tools/liaison/tests/test_liaison_schema.py`（追加约束断言）

**Interfaces:**
- Consumes: Task 1 的 `EFFECT_LOG_SCHEMA` / `SCHEMA` / `get_connection` / `init_schema`
- Produces:
  - `tools.liaison.storage.schema.MESSAGE_AND_TASK_SCHEMA: str`
  - `tools.liaison.storage.schema.SEND_STATUS_PENDING = "pending"`
  - `tools.liaison.storage.schema.SEND_STATUS_DEFERRED = "deferred"`
  - `tools.liaison.storage.schema.SEND_STATUS_PUSHED = "pushed"`
  - `tools.liaison.storage.schema.SEND_STATUSES: tuple[str, str, str]`
  - 表 `liaison_message(msgid PK, thread_id, sender_userid, received_at, msgtype, content, attachments_json, archived_at)`
  - 表 `liaison_task(id PK AUTOINCREMENT, msgid UNIQUE→FK, thread_id, sender_userid, received_at, summary, send_status, pushed_at, created_at)`

- [ ] **Step 1: 写失败的测试**

追加到 `tools/liaison/tests/test_liaison_schema.py` 末尾：

```python
# ─────────────────────────────────────────────────────────────────────────
# Task 2：liaison_message（消息台账）与 liaison_task（队列真身）
# ─────────────────────────────────────────────────────────────────────────

from tools.liaison.storage.schema import (  # noqa: E402
    SEND_STATUS_DEFERRED,
    SEND_STATUS_PENDING,
    SEND_STATUS_PUSHED,
    SEND_STATUSES,
)


@pytest.fixture
def conn(tmp_path):
    c = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(c)
    return c


def _insert_message(conn, msgid="m1", thread_id="u1", content="hello"):
    conn.execute(
        "INSERT INTO liaison_message (msgid, thread_id, sender_userid, received_at, msgtype, content) "
        "VALUES (?, ?, ?, ?, 'text', ?)",
        (msgid, thread_id, thread_id, "2026-09-08T10:00:00+08:00", content),
    )
    return msgid


def _insert_task(conn, msgid="m1", thread_id="u1"):
    conn.execute(
        "INSERT INTO liaison_task (msgid, thread_id, sender_userid, received_at) "
        "VALUES (?, ?, ?, ?)",
        (msgid, thread_id, thread_id, "2026-09-08T10:00:00+08:00"),
    )


def test_message_key_is_msgid_not_date(conn):
    """归档键细到单条消息：同人同天三条消息 = 三行，互不覆盖。

    对应 liaison-message-archive「归档键细到单条消息 / 同人同天多条消息」。
    ⛔ 任何"按天一个键"的设计都会让第三条把第一条盖掉——参考服务的生产 bug。
    """
    for msgid in ("m1", "m2", "m3"):
        _insert_message(conn, msgid=msgid, thread_id="u1", content=f"body-{msgid}")
    conn.commit()
    rows = conn.execute(
        "SELECT msgid, content FROM liaison_message WHERE thread_id = 'u1' ORDER BY msgid"
    ).fetchall()
    assert rows == [("m1", "body-m1"), ("m2", "body-m2"), ("m3", "body-m3")]


def test_message_content_survives_pipes_newlines_and_control_chars(conn):
    """任意消息内容都只是列里的一个字符串值，不可能破坏结构。

    对应 liaison-task-queue「队列真身为结构化存储」的「内容含竖线」「内容含换行」。
    竖线归一化在这个形态下**根本不需要**——这正是 design D11 放弃 Markdown 当真身
    换来的收益。
    """
    nasty = "a|b|c\n第二行\t制表\x07响铃|" + "长" * 5000
    _insert_message(conn, msgid="m9", content=nasty)
    conn.commit()
    got = conn.execute("SELECT content FROM liaison_message WHERE msgid = 'm9'").fetchone()[0]
    assert got == nasty
    assert conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 1


def test_task_requires_existing_source_message(conn):
    """条目缺少来源信息不允许写入（liaison-task-queue「每条队列条目可回指来源消息」）。"""
    with pytest.raises(sqlite3.IntegrityError):
        _insert_task(conn, msgid="does-not-exist")


@pytest.mark.parametrize("null_column", ["thread_id", "sender_userid", "received_at"])
def test_task_source_columns_are_not_null(conn, null_column):
    """来源四要素（发送人、来源会话、来源消息、接收时间）一个都不许为空。

    第四要素 msgid 由 NOT NULL + FK 覆盖，见 test_task_requires_existing_source_message。
    """
    _insert_message(conn)
    conn.commit()
    values = {"thread_id": "u1", "sender_userid": "u1", "received_at": "t"}
    values[null_column] = None
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO liaison_task (msgid, thread_id, sender_userid, received_at) "
            "VALUES ('m1', ?, ?, ?)",
            (values["thread_id"], values["sender_userid"], values["received_at"]),
        )
    conn.rollback()


def test_task_can_be_traced_back_to_its_message(conn):
    """从一条队列条目能定位到来源消息。"""
    _insert_message(conn, msgid="m1", content="原文")
    _insert_task(conn, msgid="m1")
    conn.commit()
    row = conn.execute(
        "SELECT m.content, m.sender_userid FROM liaison_task t "
        "JOIN liaison_message m ON m.msgid = t.msgid WHERE t.msgid = 'm1'"
    ).fetchone()
    assert row == ("原文", "u1")


def test_send_status_enum_values_are_ascii_not_emoji():
    """存枚举值，⛔ 不存 emoji（opener 约束 3 / design D11）。

    emoji 只允许出现在第 5 章的渲染层。存展示串意味着改一次文案就要迁一次数据，
    且任何一个拼写偏差都变成一个悄悄多出来的新状态。
    """
    assert SEND_STATUSES == (SEND_STATUS_PENDING, SEND_STATUS_DEFERRED, SEND_STATUS_PUSHED)
    assert SEND_STATUSES == ("pending", "deferred", "pushed")
    for value in SEND_STATUSES:
        assert value.isascii(), f"send_status 枚举值必须是 ASCII，实际 {value!r}"


def test_send_status_defaults_to_pending(conn):
    _insert_message(conn)
    _insert_task(conn)
    conn.commit()
    assert (
        conn.execute("SELECT send_status FROM liaison_task WHERE msgid = 'm1'").fetchone()[0]
        == SEND_STATUS_PENDING
    )


@pytest.mark.parametrize("bad", ["🆕 待发", "⏸ 暂缓", "✅ 已推送", "done", "PENDING", ""])
def test_storage_rejects_status_outside_the_three(conn, bad):
    """非法状态被拒绝（liaison-task-queue「非法状态被拒绝」）。

    注意 'PENDING' 也必须被拒——大小写变体是个真实的失手方式。
    """
    _insert_message(conn)
    _insert_task(conn)
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE liaison_task SET send_status = ? WHERE msgid = 'm1'", (bad,))
    conn.rollback()


def test_pushed_requires_timestamp(conn):
    """已推送带时间戳（liaison-task-queue「已推送带时间戳」）。"""
    _insert_message(conn)
    _insert_task(conn)
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE liaison_task SET send_status = ? WHERE msgid = 'm1'", (SEND_STATUS_PUSHED,)
        )
    conn.rollback()
    conn.execute(
        "UPDATE liaison_task SET send_status = ?, pushed_at = datetime('now') WHERE msgid = 'm1'",
        (SEND_STATUS_PUSHED,),
    )
    conn.commit()
    assert conn.execute("SELECT pushed_at FROM liaison_task WHERE msgid = 'm1'").fetchone()[0]


def test_non_pushed_must_not_carry_timestamp(conn):
    """反向也要钉住：待发/暂缓带着推送时间戳是自相矛盾的状态。"""
    _insert_message(conn)
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO liaison_task (msgid, thread_id, sender_userid, received_at, "
            "send_status, pushed_at) VALUES ('m1', 'u1', 'u1', 't', ?, datetime('now'))",
            (SEND_STATUS_PENDING,),
        )
    conn.rollback()


def test_deferred_only_from_pending(conn):
    """暂缓只能来自待发（liaison-task-queue「暂缓只能来自待发」）。

    钉在存储层（TRIGGER）而不是只在业务层判：业务层的判断可以被绕过，
    TRIGGER 不能。第 5 章的入队/改状态逻辑仍应自己先判一次，这里是兜底。
    """
    _insert_message(conn)
    _insert_task(conn)
    conn.commit()
    # pending → deferred 允许
    conn.execute(
        "UPDATE liaison_task SET send_status = ? WHERE msgid = 'm1'", (SEND_STATUS_DEFERRED,)
    )
    conn.commit()
    # deferred → pushed 允许
    conn.execute(
        "UPDATE liaison_task SET send_status = ?, pushed_at = datetime('now') WHERE msgid = 'm1'",
        (SEND_STATUS_PUSHED,),
    )
    conn.commit()
    # pushed → deferred 被拒
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE liaison_task SET send_status = ?, pushed_at = NULL WHERE msgid = 'm1'",
            (SEND_STATUS_DEFERRED,),
        )
    conn.rollback()
    assert (
        conn.execute("SELECT send_status FROM liaison_task WHERE msgid = 'm1'").fetchone()[0]
        == SEND_STATUS_PUSHED
    )


def test_one_task_per_message(conn):
    """重复投递不产生第二条队列条目——UNIQUE(msgid) 是最后一道结构性保险。

    幂等装饰器是第一道（Task 3）。两道都要有：装饰器防的是"同一路径重复执行"，
    UNIQUE 防的是"别的路径也来写一条"。
    """
    _insert_message(conn)
    _insert_task(conn)
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        _insert_task(conn)
    conn.rollback()
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `python3.14 -m pytest tools/liaison/tests/test_liaison_schema.py -v`
Expected: 收集阶段 FAIL —— `ImportError: cannot import name 'SEND_STATUS_DEFERRED' from 'tools.liaison.storage.schema'`

- [ ] **Step 3: 写最小实现**

在 `tools/liaison/storage/schema.py` 的 `EFFECT_LOG_SCHEMA` 之后、`SCHEMA` 之前插入：

```python
#: `send_status` 的三个合法取值。**存的是这三个 ASCII 枚举值**，
#: emoji（🆕 待发 / ⏸ 暂缓 / ✅ 已推送）只允许出现在第 5 章的 Markdown 渲染层。
#: 语义照搬参考服务已验证的三态（design D11），存储形态不照搬。
SEND_STATUS_PENDING = "pending"
SEND_STATUS_DEFERRED = "deferred"
SEND_STATUS_PUSHED = "pushed"
SEND_STATUSES = (SEND_STATUS_PENDING, SEND_STATUS_DEFERRED, SEND_STATUS_PUSHED)

MESSAGE_AND_TASK_SCHEMA = """
-- 消息台账。**主键是 msgid**（企微协议内单条消息的唯一标识），日期只用于分目录
-- 与排序，⛔ 绝不作为键的最细一级——参考服务的生产 bug「归档覆盖」（同人同天多条
-- 消息互相覆盖）正是把日期当了键（design D4）。
CREATE TABLE IF NOT EXISTS liaison_message (
    msgid TEXT PRIMARY KEY,
    -- 来源会话：私聊取 userid，群聊取 chatid。这同时是幂等键的 thread_id 分量。
    thread_id TEXT NOT NULL,
    sender_userid TEXT NOT NULL,
    received_at TEXT NOT NULL,
    msgtype TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    -- 附件清单（JSON 数组）。**第 2 章只建列不写入**：附件的字节流落盘、fsync +
    -- 原子 rename、SHA-256 校验全在第 4 章（design D4）。默认 '[]' 表示"这条消息
    -- 没有附件"，⛔ 不要拿它表示"附件还没处理"——那两件事必须能区分开。
    attachments_json TEXT NOT NULL DEFAULT '[]',
    archived_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_liaison_message_thread
    ON liaison_message (thread_id, received_at);

-- 队列真身（design D11）。Markdown 只是从这张表渲染出来的只读导出物，
-- ⛔ 人在导出文件里改的任何东西都不回写这里。
CREATE TABLE IF NOT EXISTS liaison_task (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- UNIQUE + FK 一起用：UNIQUE 保证"一条消息最多一条队列条目"（重复投递的结构性
    -- 保险，与幂等装饰器互为两道防线）；FK 保证"条目必然指向一条真实存在的消息"，
    -- 即 spec 的「条目缺少来源信息不允许写入」。
    -- ⚠️ FK 只在连接开了 PRAGMA foreign_keys = ON 时生效，见 storage/db.py。
    msgid TEXT NOT NULL UNIQUE REFERENCES liaison_message (msgid),
    thread_id TEXT NOT NULL,
    sender_userid TEXT NOT NULL,
    received_at TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    -- 三态钉死在存储层。⛔ 存 emoji 就等于把展示层的文案变成数据契约。
    send_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (send_status IN ('pending', 'deferred', 'pushed')),
    pushed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    -- 「已推送必带时间戳」与「未推送必不带时间戳」一条表约束同时钉住。
    -- 写成等式而不是两条 CHECK：两个方向必须同时成立，拆开写容易只加一半。
    CHECK ((send_status = 'pushed') = (pushed_at IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_liaison_task_status
    ON liaison_task (send_status, received_at);

-- 「暂缓」只能由「待发」转入。CHECK 表达不了状态**转移**（它看不到旧值），
-- 所以用 BEFORE UPDATE 触发器。⛔ 不要只在业务层判——业务层可以被绕过，
-- 而这张表是队列的真身，它自己必须拒绝非法转移。
CREATE TRIGGER IF NOT EXISTS trg_liaison_task_defer_only_from_pending
BEFORE UPDATE OF send_status ON liaison_task
FOR EACH ROW WHEN NEW.send_status = 'deferred' AND OLD.send_status <> 'pending'
BEGIN
    SELECT RAISE(ABORT, 'send_status: deferred may only be entered from pending');
END;
"""
```

然后把文件末尾的 `SCHEMA` 一行替换为：

```python
#: 本服务的全量 DDL。
SCHEMA = EFFECT_LOG_SCHEMA + MESSAGE_AND_TASK_SCHEMA
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3.14 -m pytest tools/liaison/tests/test_liaison_schema.py -v`
Expected: PASS —— **26 passed**（Task 1 的 7 条 + 本 Task 的 19 条。19 = 12 个测试函数，其中 `test_task_source_columns_are_not_null` 被 parametrize 展开成 3 条、`test_storage_rejects_status_outside_the_three` 展开成 6 条）

- [ ] **Step 5: 提交**

```bash
git add tools/liaison/storage/schema.py tools/liaison/tests/test_liaison_schema.py
git commit -m "feat(liaison): liaison_message 台账与 liaison_task 队列真身，三态 CHECK + 转移 TRIGGER"
```

---

### Task 3: 单向接入 `idempotent_effect` 并断言无第二个事务管理者

**Files:**
- Create: `tools/liaison/storage/effects.py`
- Create: `tools/liaison/tests/test_liaison_effects.py`
- Read-only reference: `app/storage/idempotency.py`（⛔ 不修改）

**Interfaces:**
- Consumes: Task 1 的 `get_connection` / `init_schema`；Task 2 的表与 `SEND_STATUS_*`
- Produces:
  - `tools.liaison.storage.effects.effect_archive_message(conn, *, thread_id, business_key, sender_userid, received_at, msgtype, content, attachments_json="[]") -> str | None`
  - `tools.liaison.storage.effects.effect_enqueue_task(conn, *, thread_id, business_key, sender_userid, received_at, summary="") -> int | None`
  - `tools.liaison.storage.effects.EFFECT_NODE_TO_TABLE: dict[str, str]` —— 节点名 → 其业务表名，Task 4 的恒等断言按它遍历

⚠️ **签名约束（来自 `app/storage/idempotency.py` 的装饰器实现，⛔ 不可改）**：被装饰函数必须是 `fn(conn, *, thread_id, business_key, **kwargs)`。`conn` 是**唯一的位置参数**，`thread_id` 与 `business_key` 必须是关键字参数——装饰器就是这样转发的，写成位置参数会 `TypeError`。

- [ ] **Step 1: 写失败的测试**

创建 `tools/liaison/tests/test_liaison_effects.py`：

```python
"""effect 层的三组断言：单一事务管理者、恒等不变式、异常不留幂等记录。

这三组合起来就是工程铁律 1 在本服务里的可执行形式。⛔ 任何一条红了都不要改断言——
`.51` 现网 2026-08-10 与 08-12 各丢一轮 outbox，成因正是这三条里的第三条没被守住。
"""

from __future__ import annotations

import ast
import pathlib
import sqlite3

import pytest

from tools.liaison.storage import db as liaison_db
from tools.liaison.storage.effects import (
    EFFECT_NODE_TO_TABLE,
    effect_archive_message,
    effect_enqueue_task,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
LIAISON_ROOT = REPO_ROOT / "tools" / "liaison"

#: 允许调用 conn.commit()/rollback() 的**唯一**位置：schema 初始化。
#: 键是相对 tools/liaison 的路径，值是允许的函数名集合。
TRANSACTION_OWNER_ALLOWLIST = {"storage/db.py": {"init_schema"}}


@pytest.fixture
def conn(tmp_path):
    c = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(c)
    return c


class SpyConnection:
    """记账用的连接包装：数 commit/rollback 各被调了几次。

    ⛔ 不要用 unittest.mock 去 patch sqlite3.Connection.commit——它是 C 实现的
    只读属性，patch 不上（实测 AttributeError）。包一层才是可行的做法。
    """

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real
        self.commits = 0
        self.rollbacks = 0

    def execute(self, *args, **kwargs):
        return self._real.execute(*args, **kwargs)

    def commit(self) -> None:
        self.commits += 1
        self._real.commit()

    def rollback(self) -> None:
        self.rollbacks += 1
        self._real.rollback()


# ─────────────────────────────────────────────────────────────────────────
# 2.3 单向 import + 无第二个事务管理者
# ─────────────────────────────────────────────────────────────────────────


def test_effects_reuses_the_product_decorator_not_a_local_copy():
    """必须 import app.storage.idempotency，⛔ 不许在本目录抄一份幂等实现。

    抄一份的代价是：产品那边修了幂等的 bug（例如 2026-09-08 那次唯一键竞态短路），
    这边不会跟着修，而且没有任何症状。
    """
    source = (LIAISON_ROOT / "storage" / "effects.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "app.storage.idempotency" in imported


def test_liaison_does_not_import_product_db_layer():
    """只 import 幂等装饰器这一个模块，⛔ 不 import app.storage.db。

    import 了 app.storage.db 就等于把产品库的 schema、迁移、连接语义一起拖进来，
    D5 的"独立库"就名存实亡了。
    """
    for path in LIAISON_ROOT.rglob("*.py"):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module != "app.storage.db", f"{path} 不该 import app.storage.db"
                assert not node.module.startswith("app.graph"), f"{path} 不该 import {node.module}"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "app.storage.db", f"{path} 不该 import app.storage.db"


def test_no_second_transaction_manager_in_source():
    """静态判据：非测试代码里 commit/rollback 的调用点只允许出现在白名单里。

    事务的主人只有一个——`idempotent_effect`。任何别的地方调 commit()，都可能把
    一个只写了一半的业务事务提交下去，让 effect_log 与业务表的条数当场对不上，
    **而且不报错**。这条断言是那道闸。
    """
    offenders = []
    for path in sorted(LIAISON_ROOT.rglob("*.py")):
        if "tests" in path.parts:
            continue
        rel = path.relative_to(LIAISON_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            for node in ast.walk(func):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("commit", "rollback")
                ):
                    if func.name not in TRANSACTION_OWNER_ALLOWLIST.get(rel, set()):
                        offenders.append(f"{rel}::{func.name} 调了 {node.func.attr}()")
    assert offenders == [], "发现白名单之外的事务管理者：" + "; ".join(offenders)


def test_no_checkpointer_or_langgraph_in_liaison():
    """design D6：本服务不引入 LangGraph，因此连接上不会出现第二个事务管理者。

    checkpointer 与 effect 层共用连接正是 2026-08-13 那份 findings 的事故成因。
    这条断言让"我们没引入"从一句话变成一个会红的测试。
    """
    for path in LIAISON_ROOT.rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        assert "langgraph" not in text.lower(), f"{path} 提到了 langgraph"
        assert "SqliteSaver" not in text, f"{path} 提到了 SqliteSaver"


def test_successful_effect_commits_exactly_once(conn):
    """运行期判据：一次成功的 effect = 恰好 1 次 commit、0 次 rollback。"""
    spy = SpyConnection(conn)
    effect_archive_message(
        spy,
        thread_id="u1",
        business_key="m1",
        sender_userid="u1",
        received_at="2026-09-08T10:00:00+08:00",
        msgtype="text",
        content="hello",
    )
    assert (spy.commits, spy.rollbacks) == (1, 0)


def test_duplicate_effect_neither_commits_nor_rolls_back(conn):
    """重复投递被静默跳过：不写、不提交、不回滚，返回 None。

    对应 liaison-message-archive「同一消息被投递两次」。
    """
    kwargs = dict(
        thread_id="u1",
        business_key="m1",
        sender_userid="u1",
        received_at="2026-09-08T10:00:00+08:00",
        msgtype="text",
        content="hello",
    )
    effect_archive_message(conn, **kwargs)
    spy = SpyConnection(conn)
    assert effect_archive_message(spy, **kwargs) is None
    assert (spy.commits, spy.rollbacks) == (0, 0)
    assert conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 1


def test_effect_key_format_matches_the_ironclad_rule(conn):
    """幂等键 = {thread_id}:{node_name}:{business_key}，逐字符对。

    thread_id ＝私聊 userid／群聊 chatid；business_key ＝企微 msgid。
    """
    effect_archive_message(
        conn,
        thread_id="chat-42",
        business_key="msg-7",
        sender_userid="u1",
        received_at="2026-09-08T10:00:00+08:00",
        msgtype="text",
        content="x",
    )
    keys = [row[0] for row in conn.execute("SELECT effect_key FROM effect_log")]
    assert keys == ["chat-42:effect_archive_message:msg-7"]
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `python3.14 -m pytest tools/liaison/tests/test_liaison_effects.py -v`
Expected: 收集阶段 FAIL —— `ModuleNotFoundError: No module named 'tools.liaison.storage.effects'`

- [ ] **Step 3: 写最小实现**

创建 `tools/liaison/storage/effects.py`：

```python
"""本服务全部有副作用的写库动作。

**每个有副作用的动作独占一个 `effect_*` 函数**（工程铁律 1、2）。计算类
（白名单判定、内容归一化、长度守卫）一律是别处的 `compute_*` 纯函数，
⛔ 不许混进本模块。

幂等键 = `{thread_id}:{node_name}:{business_key}`：
- `thread_id` = 消息来源会话标识（私聊取 `userid`，群聊取 `chatid`）
- `business_key` = 企微 `msgid`

⛔ **本模块不许出现任何 `conn.commit()` / `conn.rollback()`。** 提交由
`idempotent_effect` 独占——它在同一个隐式事务里写业务行与 `effect_log` 行然后
一次性提交。自己再提交一次，就是在制造第二个事务管理者，也就是
`docs/findings/2026-08-13-sqlite-事务归属冲突.md` 那场事故的成因。
本约束由 tests/test_liaison_effects.py::test_no_second_transaction_manager_in_source 守着。

**范围**：本模块属第 2 章存储基座，只做"一条消息 → 一行台账 / 一行队列"的最小写入。
白名单准入（第 3 章）、附件落盘（第 4 章）、队列渲染与状态流转（第 5 章）
⛔ 都不在这里。
"""

from __future__ import annotations

import sqlite3

from app.storage.idempotency import idempotent_effect

#: 每个 effect 节点写的是哪张业务表。Task 4 的恒等不变式断言按它遍历，
#: 新增 effect 时**必须**同步登记——漏登记会让那个 effect 悄悄逃过恒等检查。
EFFECT_NODE_TO_TABLE = {
    "effect_archive_message": "liaison_message",
    "effect_enqueue_task": "liaison_task",
}


@idempotent_effect("effect_archive_message")
def effect_archive_message(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    sender_userid: str,
    received_at: str,
    msgtype: str,
    content: str = "",
    attachments_json: str = "[]",
) -> str:
    """把一条消息写进台账。`business_key` 即 `msgid`，也是台账主键。

    ⚠️ 调用方必须**先把附件落盘**再调本函数（design D3 钉死的顺序）。
    "文件已落、DB 行未写"是可收敛的中间态；"DB 行已写、文件缺失"是不可恢复的谎。
    附件落盘本身是第 4 章的事，本函数不做也不检查——它只负责写这一行。
    """
    conn.execute(
        "INSERT INTO liaison_message "
        "(msgid, thread_id, sender_userid, received_at, msgtype, content, attachments_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (business_key, thread_id, sender_userid, received_at, msgtype, content, attachments_json),
    )
    return business_key


@idempotent_effect("effect_enqueue_task")
def effect_enqueue_task(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    sender_userid: str,
    received_at: str,
    summary: str = "",
) -> int:
    """把一条消息变成一条值守任务条目。返回新条目的 rowid。

    `send_status` 走表默认值 `pending`，⛔ 不在这里显式传——默认值是表的契约，
    在调用点重复一遍只会制造两个真源。
    """
    cursor = conn.execute(
        "INSERT INTO liaison_task (msgid, thread_id, sender_userid, received_at, summary) "
        "VALUES (?, ?, ?, ?, ?)",
        (business_key, thread_id, sender_userid, received_at, summary),
    )
    return cursor.lastrowid
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3.14 -m pytest tools/liaison/tests/test_liaison_effects.py -v`
Expected: PASS —— 7 passed

- [ ] **Step 5: 提交**

```bash
git add tools/liaison/storage/effects.py tools/liaison/tests/test_liaison_effects.py
git commit -m "feat(liaison): effect_archive_message/effect_enqueue_task 单向复用 idempotent_effect"
```

---

### Task 4: 恒等不变式测试脚手架

**Files:**
- Modify: `tools/liaison/tests/test_liaison_effects.py`（追加恒等不变式一节）

**Interfaces:**
- Consumes: Task 3 的 `effect_archive_message` / `effect_enqueue_task` / `EFFECT_NODE_TO_TABLE`
- Produces: `assert_effect_log_identity(conn)` —— 供第 4／5／7 章的测试复用的断言函数

这是本章的**核心交付物**。铁律 1 的 reviewer 判据原文：「每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖」。脚手架把这句话变成一个**任何后续章节都能一行调用**的断言。

- [ ] **Step 1: 写失败的测试**

追加到 `tools/liaison/tests/test_liaison_effects.py` 末尾：

```python
# ─────────────────────────────────────────────────────────────────────────
# 2.4 恒等不变式脚手架
# ─────────────────────────────────────────────────────────────────────────


def assert_effect_log_identity(conn: sqlite3.Connection) -> None:
    """铁律 1 的 reviewer 判据，做成可复用的断言。

    对 EFFECT_NODE_TO_TABLE 里每个节点：
      按 thread_id 分组，该节点的 effect_log 条数 == 其业务表的行数。

    ⛔ **不要退化成"总数相等"**——总数相等可以由"A 会话多一行、B 会话少一行"凑出来，
    那恰恰是最需要被抓住的那种错。按 thread_id 逐组比才有意义。

    第 4／5／7 章的测试应当直接 import 本函数在各自的场景末尾调一次。
    """
    for node_name, table in EFFECT_NODE_TO_TABLE.items():
        effect_counts = dict(
            conn.execute(
                "SELECT thread_id, COUNT(*) FROM effect_log WHERE node_name = ? "
                "GROUP BY thread_id",
                (node_name,),
            ).fetchall()
        )
        business_counts = dict(
            conn.execute(f"SELECT thread_id, COUNT(*) FROM {table} GROUP BY thread_id").fetchall()
        )
        assert effect_counts == business_counts, (
            f"恒等不变式破裂：节点 {node_name} 的 effect_log 分组计数 {effect_counts} "
            f"≠ 业务表 {table} 的分组计数 {business_counts}"
        )


def _process(conn, *, thread_id, msgid, content="hello"):
    """把一条消息走完本章范围内的两个 effect：归档 + 入队。

    顺序是钉死的：先台账后队列。队列条目的 FK 指向台账，反过来会撞 FK。
    """
    effect_archive_message(
        conn,
        thread_id=thread_id,
        business_key=msgid,
        sender_userid=thread_id,
        received_at="2026-09-08T10:00:00+08:00",
        msgtype="text",
        content=content,
    )
    effect_enqueue_task(
        conn,
        thread_id=thread_id,
        business_key=msgid,
        sender_userid=thread_id,
        received_at="2026-09-08T10:00:00+08:00",
        summary=content[:40],
    )


def test_identity_holds_on_empty_database(conn):
    """空库也必须恒等（两边都是空 dict）。边界条件，别跳过。"""
    assert_effect_log_identity(conn)


def test_identity_holds_across_a_batch_of_messages(conn):
    """处理任意一批消息后核对（liaison-task-queue「幂等记录与条目数恒等」）。"""
    for thread_id, msgids in (("u1", ["m1", "m2", "m3"]), ("chat-9", ["m4", "m5"])):
        for msgid in msgids:
            _process(conn, thread_id=thread_id, msgid=msgid)
    assert_effect_log_identity(conn)
    assert conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 5
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 5
    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 10


def test_identity_holds_after_replaying_the_whole_batch(conn):
    """重连后通道重投一批历史消息：归档与队列均无新增，恒等仍成立。

    对应 liaison-message-archive「重连后重投历史消息」。
    """
    batch = [("u1", "m1"), ("u1", "m2"), ("chat-9", "m3")]
    for thread_id, msgid in batch:
        _process(conn, thread_id=thread_id, msgid=msgid)
    before = (
        conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0],
    )
    for _ in range(3):  # 重投三遍
        for thread_id, msgid in batch:
            _process(conn, thread_id=thread_id, msgid=msgid)
    after = (
        conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0],
    )
    assert after == before == (3, 3, 6)
    assert_effect_log_identity(conn)


def test_identity_scaffold_actually_catches_a_break(conn):
    """脚手架本身要有牙——绕过 effect 直接写业务表，断言必须红。

    ⛔ 不要删这条。一个永远为真的断言比没有断言更糟：它会让 reviewer 以为
    这条不变式被守住了。这条是对断言本身的证伪测试。
    """
    _process(conn, thread_id="u1", msgid="m1")
    assert_effect_log_identity(conn)
    # 绕过 effect 层偷偷插一行业务数据
    conn.execute(
        "INSERT INTO liaison_message (msgid, thread_id, sender_userid, received_at, msgtype) "
        "VALUES ('sneaky', 'u1', 'u1', 't', 'text')"
    )
    conn.commit()
    with pytest.raises(AssertionError, match="恒等不变式破裂"):
        assert_effect_log_identity(conn)


def test_identity_is_per_thread_not_global(conn):
    """跨会话的数量互相抵消也必须被抓住。

    构造：u1 的业务表多一行、u2 的 effect_log 多一行，总数相等但分组不等。
    如果断言写成"总数相等"，这条会绿——那就是它存在的意义。
    """
    _process(conn, thread_id="u1", msgid="m1")
    conn.execute(
        "INSERT INTO liaison_message (msgid, thread_id, sender_userid, received_at, msgtype) "
        "VALUES ('extra', 'u1', 'u1', 't', 'text')"
    )
    conn.execute(
        "INSERT INTO effect_log VALUES ('u2:effect_archive_message:ghost', 'u2', "
        "'effect_archive_message', 'ghost', datetime('now'))"
    )
    conn.commit()
    total_effect = conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = 'effect_archive_message'"
    ).fetchone()[0]
    total_business = conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0]
    assert total_effect == total_business == 2  # 总数相等，但……
    with pytest.raises(AssertionError, match="恒等不变式破裂"):
        assert_effect_log_identity(conn)


def test_nasty_content_does_not_break_the_queue_structure(conn):
    """含竖线/换行/控制字符的内容入队后，条目字段不错位、不串行。

    对应 liaison-task-queue「内容含竖线」「内容含换行」两个场景在队列真身上的形态。
    """
    nasty = "标题|列二|列三\n---|---|---\n值\t制表\x00空字节"
    _process(conn, thread_id="u1", msgid="m1", content=nasty)
    _process(conn, thread_id="u1", msgid="m2", content="normal")
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 2
    assert (
        conn.execute("SELECT content FROM liaison_message WHERE msgid = 'm1'").fetchone()[0]
        == nasty
    )
    assert_effect_log_identity(conn)


def test_concurrent_style_interleaved_writes_do_not_overwrite(conn):
    """多条消息交错入队，每条各自产生一条条目，无覆盖无丢失。

    对应 liaison-task-queue「并发写入不互相覆盖」。本服务是单进程单连接
    （design D6/D12），所以这里模拟的是**交错的调用顺序**而非真并发线程；
    真并发不在本服务的模型内，⛔ 不要为了"更真"而引入线程池——那会引入一个
    本服务不存在的假设。结构上的防护是 liaison_task.msgid 的 UNIQUE 约束。
    """
    msgids = [f"m{i}" for i in range(10)]
    for msgid in msgids:
        effect_archive_message(
            conn,
            thread_id="u1",
            business_key=msgid,
            sender_userid="u1",
            received_at="2026-09-08T10:00:00+08:00",
            msgtype="text",
            content=f"body-{msgid}",
        )
    for msgid in reversed(msgids):  # 入队顺序与归档顺序刻意相反
        effect_enqueue_task(
            conn,
            thread_id="u1",
            business_key=msgid,
            sender_userid="u1",
            received_at="2026-09-08T10:00:00+08:00",
        )
    stored = [row[0] for row in conn.execute("SELECT msgid FROM liaison_task ORDER BY msgid")]
    assert stored == sorted(msgids)
    assert_effect_log_identity(conn)
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `python3.14 -m pytest tools/liaison/tests/test_liaison_effects.py -k identity -v`
Expected: FAIL —— `NameError: name 'assert_effect_log_identity' is not defined` 之前先撞 `EFFECT_NODE_TO_TABLE` 相关错误；若 Task 3 已完成则本步的失败点是这些新测试尚未有实现支撑（`_process` 未定义）。

⚠️ 本 Task 的"实现"就是 Step 1 里那个 `assert_effect_log_identity` 函数本身——它是测试脚手架，不是产品代码。因此 Step 1 写完即同时是实现；Step 2 的意义在于**先确认脚手架能红**（`test_identity_scaffold_actually_catches_a_break` 与 `test_identity_is_per_thread_not_global` 两条证伪测试就是为此存在的）。

- [ ] **Step 3: 跑测试确认全部通过**

Run: `python3.14 -m pytest tools/liaison/tests/test_liaison_effects.py -v`
Expected: PASS —— 14 passed（Task 3 的 7 条 + 本 Task 的 7 条）

- [ ] **Step 4: 手工验证脚手架真的有牙**

Run:
```bash
python3.14 -m pytest tools/liaison/tests/test_liaison_effects.py::test_identity_scaffold_actually_catches_a_break tools/liaison/tests/test_liaison_effects.py::test_identity_is_per_thread_not_global -v
```
Expected: 2 passed —— 这两条**通过**恰恰证明断言在不变式破裂时会抛 `AssertionError`。

- [ ] **Step 5: 提交**

```bash
git add tools/liaison/tests/test_liaison_effects.py
git commit -m "test(liaison): 恒等不变式脚手架，按 thread_id 分组比对且自带两条证伪测试"
```

---

### Task 5: 业务写异常时不留幂等记录

**Files:**
- Modify: `tools/liaison/tests/test_liaison_effects.py`（追加异常路径一节）

**Interfaces:**
- Consumes: Task 3 的 effects、Task 4 的 `assert_effect_log_identity` 与 `SpyConnection`
- Produces: 无新增生产代码——本 Task 验证的是**已有机制在本服务里确实成立**

这条是铁律 1 那段"为什么"的正面覆盖：业务写失败而幂等记录成功 ⇒ 系统判定"已执行" ⇒ 永不重试 ⇒ **幂等从防重复的保护变成永久丢失的保证**。`.51` 现网 2026-08-10 与 08-12 各丢一轮 `outbox` 就是这么丢的。

- [ ] **Step 1: 写失败的测试**

追加到 `tools/liaison/tests/test_liaison_effects.py` 末尾：

```python
# ─────────────────────────────────────────────────────────────────────────
# 2.5 业务写抛异常 ⇒ effect_log 不留记录 ⇒ 重跑会重新尝试
# ─────────────────────────────────────────────────────────────────────────


def test_no_effect_log_when_business_write_raises(conn):
    """业务写失败时不留下幂等记录（liaison-task-queue 同名场景）。

    ⛔ 这条红了**绝不能**靠"先写 effect_log 再写业务"来"修"——那正是事故本身。
    """
    from app.storage.idempotency import idempotent_effect

    @idempotent_effect("effect_archive_message")
    def failing_archive(c, *, thread_id, business_key):
        c.execute(
            "INSERT INTO liaison_message (msgid, thread_id, sender_userid, received_at, msgtype) "
            "VALUES (?, ?, 'u1', 't', 'text')",
            (business_key, thread_id),
        )
        raise RuntimeError("模拟业务写之后、提交之前的失败")

    with pytest.raises(RuntimeError, match="模拟业务写"):
        failing_archive(conn, thread_id="u1", business_key="m1")

    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 0
    assert_effect_log_identity(conn)


def test_failed_effect_is_retried_on_next_run(conn):
    """重新处理同一条消息时该动作会被重新尝试，并且这次成功。

    这是上一条的另一半：不留幂等记录**的目的**就是让重试可能发生。
    只断言"没留记录"而不断言"重试真的成功了"，等于只测了一半。
    """
    from app.storage.idempotency import idempotent_effect

    attempts = {"n": 0}

    @idempotent_effect("effect_archive_message")
    def flaky_archive(c, *, thread_id, business_key):
        attempts["n"] += 1
        c.execute(
            "INSERT INTO liaison_message (msgid, thread_id, sender_userid, received_at, msgtype) "
            "VALUES (?, ?, 'u1', 't', 'text')",
            (business_key, thread_id),
        )
        if attempts["n"] == 1:
            raise RuntimeError("第一次失败")
        return business_key

    with pytest.raises(RuntimeError):
        flaky_archive(conn, thread_id="u1", business_key="m1")
    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 0

    assert flaky_archive(conn, thread_id="u1", business_key="m1") == "m1"
    assert attempts["n"] == 2
    assert conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 1
    assert_effect_log_identity(conn)


def test_failed_effect_rolls_back_exactly_once_and_never_commits(conn):
    """运行期判据：失败路径 = 0 次 commit、1 次 rollback。

    0 次 commit 是关键——只要失败路径上有过一次 commit，那半截业务写就落盘了，
    而 effect_log 是空的，恒等式当场破且无症状。
    """
    from app.storage.idempotency import idempotent_effect

    @idempotent_effect("effect_enqueue_task")
    def failing_enqueue(c, *, thread_id, business_key):
        raise RuntimeError("boom")

    spy = SpyConnection(conn)
    with pytest.raises(RuntimeError):
        failing_enqueue(spy, thread_id="u1", business_key="m1")
    assert (spy.commits, spy.rollbacks) == (0, 1)


def test_constraint_violation_in_business_write_leaves_no_trace(conn):
    """真实失败形态而非人造异常：FK 违约（队列条目指向不存在的消息）。

    比 `raise RuntimeError` 更接近现网会发生的事——顺序写反了就是这个报错。
    """
    with pytest.raises(sqlite3.IntegrityError):
        effect_enqueue_task(
            conn,
            thread_id="u1",
            business_key="never-archived",
            sender_userid="u1",
            received_at="2026-09-08T10:00:00+08:00",
        )
    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 0
    assert_effect_log_identity(conn)


def test_partial_batch_failure_keeps_identity(conn):
    """一批消息中间有一条失败，其余照常，恒等式对每个会话仍成立。"""
    _process(conn, thread_id="u1", msgid="m1")
    with pytest.raises(sqlite3.IntegrityError):
        effect_enqueue_task(
            conn,
            thread_id="u1",
            business_key="orphan",
            sender_userid="u1",
            received_at="2026-09-08T10:00:00+08:00",
        )
    _process(conn, thread_id="u1", msgid="m2")
    _process(conn, thread_id="chat-9", msgid="m3")
    assert_effect_log_identity(conn)
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 3
```

- [ ] **Step 2: 跑测试确认状态**

Run: `python3.14 -m pytest tools/liaison/tests/test_liaison_effects.py -v`
Expected: PASS —— 19 passed（Task 3 的 7 + Task 4 的 7 + 本 Task 的 5）

⚠️ 本 Task 的测试**预期一开始就是绿的**——它验证的是 `app/storage/idempotency.py` 已有的回滚机制在本服务的连接模型下确实成立，不是驱动新代码。这不违反 TDD：**红-绿循环的价值在这里由"证伪"承担**，见 Step 3。

- [ ] **Step 3: 证伪——确认这些测试真的在测东西**

临时把 `tools/liaison/storage/effects.py` 里 `effect_enqueue_task` 的 `INSERT` 语句改成先写一行 `effect_log` 再抛异常（模拟"幂等记录先落"的错误实现），跑：

Run: `python3.14 -m pytest tools/liaison/tests/test_liaison_effects.py -k "no_effect_log or constraint_violation" -v`
Expected: FAIL —— 断言 `COUNT(*) FROM effect_log == 0` 不成立

确认失败后**立刻把改动还原**：

```bash
git checkout -- tools/liaison/storage/effects.py
python3.14 -m pytest tools/liaison/tests/test_liaison_effects.py -q
```
Expected: 还原后 19 passed

- [ ] **Step 4: 提交**

```bash
git add tools/liaison/tests/test_liaison_effects.py
git commit -m "test(liaison): 业务写异常不留幂等记录，含 FK 违约与重试成功两条真实路径"
```

---

### Task 6: 结构断言——`app/` 下无模块 import `tools/`

**Files:**
- Create: `tools/liaison/tests/test_app_does_not_import_tools.py`

**Interfaces:**
- Consumes: 无（纯静态扫描，只读仓库文件）
- Produces: 无

复用方向**单向**（design D5 末条）：`tools/liaison` 可以 import `app.storage.idempotency`，⛔ `app/` 下任何模块不得 import `tools/`。这条不是风格洁癖——`app/` 会被 `sync-to-server.sh` 推到 `.51`，而 `tools/` **不在** `SYNC_PATHS` 白名单里。`app/` 里一旦出现 `import tools.xxx`，`.51` 上就是一个 `ModuleNotFoundError`，**而且只在运行到那行时才炸**。

- [ ] **Step 1: 写测试**

创建 `tools/liaison/tests/test_app_does_not_import_tools.py`：

```python
"""结构性单向依赖断言：`app/` ⛔ 不得 import `tools/`（design D5）。

用 AST 而不是 grep：grep 会把注释、docstring、字符串字面量里的 "import tools"
一起算进来（本仓库的中文注释里就有大量提到 tools 的句子），产生假阳性；
也会漏掉 `importlib.import_module("tools.x")` 这类写法——后者由本文件的
第二条断言单独覆盖。

⛔ 断言失败时不要把违规模块加进豁免名单（本文件刻意没有豁免名单）。
`app/` 会被 sync-to-server.sh 推到 .51，`tools/` 不会。这条依赖在 .51 上必然是
ModuleNotFoundError，而且只在执行到那一行时才炸——测试环境全绿、现网崩。
"""

from __future__ import annotations

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
APP_ROOT = REPO_ROOT / "app"


def _app_modules() -> list[pathlib.Path]:
    return sorted(p for p in APP_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def test_app_has_modules_to_scan():
    """先证明扫描范围非空——空目录会让下面两条断言变成永远为真的摆设。"""
    modules = _app_modules()
    assert len(modules) >= 40, f"app/ 下只扫到 {len(modules)} 个模块，扫描范围可能不对"


def test_no_app_module_imports_tools():
    """静态 import：`import tools.x` 与 `from tools.x import y` 都要抓。"""
    offenders: list[str] = []
    for path in _app_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "tools" or alias.name.startswith("tools."):
                        offenders.append(f"{path.relative_to(REPO_ROOT)}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                # node.level > 0 是相对 import（from . import x），
                # 相对 import 出不了 app/ 包，不可能指到 tools，跳过。
                if node.level == 0 and (module == "tools" or module.startswith("tools.")):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: from {module} import ...")
    assert offenders == [], (
        "app/ 下出现了对 tools/ 的 import，违反 design D5 的单向依赖：\n"
        + "\n".join(offenders)
    )


def test_no_app_module_imports_tools_dynamically():
    """动态 import：`importlib.import_module("tools.x")` / `__import__("tools.x")`。

    静态扫描抓不到它们，但它们在 .51 上一样炸。
    """
    offenders: list[str] = []
    for path in _app_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = None
            if isinstance(node.func, ast.Attribute):
                name = node.func.attr
            elif isinstance(node.func, ast.Name):
                name = node.func.id
            if name not in ("import_module", "__import__"):
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if arg.value == "tools" or arg.value.startswith("tools."):
                        offenders.append(
                            f"{path.relative_to(REPO_ROOT)}: {name}({arg.value!r})"
                        )
    assert offenders == [], "app/ 下动态 import 了 tools/：\n" + "\n".join(offenders)


def test_the_scanner_would_catch_a_violation(tmp_path):
    """对扫描器本身的证伪：给它一个真的违规文件，它必须报出来。

    ⛔ 不要删这条。上面三条在"扫描器写错了"的情况下会永远绿，
    而"永远绿的结构断言"比没有断言更危险——它会让人以为约束被守住了。
    """
    bad = tmp_path / "bad_module.py"
    bad.write_text("from tools.liaison.storage import db\n", encoding="utf-8")
    tree = ast.parse(bad.read_text(encoding="utf-8"))
    hits = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.level == 0
        and (node.module or "").startswith("tools.")
    ]
    assert hits == ["tools.liaison.storage"]
```

- [ ] **Step 2: 跑测试**

Run: `python3.14 -m pytest tools/liaison/tests/test_app_does_not_import_tools.py -v`
Expected: PASS —— 4 passed（本机已实测：`app/` 下 52 个 `.py`，`tools` import 违规数为 0）

- [ ] **Step 3: 证伪——确认扫描器真的会红**

```bash
printf 'import tools.liaison.storage.db\n' > app/_tmp_violation.py
python3.14 -m pytest tools/liaison/tests/test_app_does_not_import_tools.py::test_no_app_module_imports_tools -v
```
Expected: FAIL —— 报 `app/_tmp_violation.py: import tools.liaison.storage.db`

**清理（⛔ 不要漏，这个临时文件绝不能进提交）：**

```bash
rm app/_tmp_violation.py
python3.14 -m pytest tools/liaison/tests/test_app_does_not_import_tools.py -q
git status --short app/
```
Expected: 4 passed；`git status --short app/` 输出为空

- [ ] **Step 4: 跑全量并提交**

Run: `python3.14 -m pytest -q`
Expected: 全绿，退出码 0

```bash
git add tools/liaison/tests/test_app_does_not_import_tools.py
git commit -m "test(liaison): app/ 不得 import tools/ 的 AST 结构断言，含扫描器证伪"
```

---

## 收尾：回勾 tasks.md

全部 6 个 Task 的 final review 通过后，把 `openspec/changes/hr-wecom-aibot-liaison/tasks.md` 第 2 章的 2.1–2.6 六个 checkbox 勾上，并单独提交：

```bash
git add openspec/changes/hr-wecom-aibot-liaison/tasks.md
git commit -m "docs(liaison): 第 2 章存储基座 2.1-2.6 回勾"
```

⚠️ **回勾必须在 review 通过之后**，⛔ 不要在 Task 逐条完成时就勾——章节的 checkbox 是交付单元级的，不是 Task 级的（`CLAUDE.md`「粒度映射」）。

---

## 交付前自查记录

- [x] 任务标题全部是三级 `### Task N: ` —— `grep -c '^### Task ' <本文件>` 应为 **6**
- [x] 有 **Global Constraints** 段，内容逐字来自 `CLAUDE.md`
- [x] spec 的每条 `### Requirement:` 都在「本计划对 spec Requirement 的覆盖」表里有归属；本章不实现的 5 条**已显式登记**为第 4／5／7 章，并在「本章的四条不做」里重申
- [x] 每个 Task 有确切文件路径、完整代码、确切命令与预期输出
- [x] 无 TBD / TODO / "适当处理错误" 类占位符
- [x] 前后 Task 的类型名、函数签名、字段名一致（`effect_archive_message` / `effect_enqueue_task` / `EFFECT_NODE_TO_TABLE` / `assert_effect_log_identity` / `SpyConnection` 全文一致）
- [x] 每个有副作用的动作独占一个 `effect_*` 函数且带幂等键 `{thread_id}:{node_name}:{business_key}`（铁律 1）
- [x] 不涉及 AI 评分，`evidence_ref` 一条不适用（本服务不调 LLM，design D7）

### 关键代码已在本机预跑验证（Python 3.14.6）

写计划前用一份临时原型（`/tmp/liaison-proto/`，已清理）把下列断言**实跑过**，计划里的预期输出是实测值而非推测：

| 验证项 | 实测结果 |
|---|---|
| `effect_log` 与 `app/storage/db.py` 的 `PRAGMA` 形状比对 | `isomorphic: True` |
| 连接的事务控制模式 | `autocommit = -1`（`LEGACY_TRANSACTION_CONTROL`）、`isolation_level = ''` |
| 重复调用同一 effect | 返回 `None`，业务表 1 行 / `effect_log` 1 行 |
| 业务写抛异常后 | 业务表与 `effect_log` 均无该条记录，异常原样上抛 |
| `CHECK` 拒绝 `'🆕 待发'` / `'done'` / `''` | 三者全部 `CHECK constraint failed: send_status IN (...)` |
| `pushed` 无时间戳 | `CHECK constraint failed: (send_status = 'pushed') = (pushed_at IS NOT NULL)` |
| `pushed → deferred` | `send_status: deferred may only be entered from pending`（TRIGGER 生效） |
| 队列条目指向不存在的消息 | `FOREIGN KEY constraint failed` |
| commit/rollback 计数（spy 连接） | 成功 `(1, 0)`、重复 `(+0, +0)`、异常 `(+0, +1)` |
| `app/` 下 52 个模块的 `tools` import 扫描 | 违规数 `0` |

### 端到端提取验证（本计划全部代码块已原样提取后跑过）

把本文件里的 11 个 `python` 代码块**原样提取**到 `/tmp/liaison-verify/`（复制真实的 `app/` 与 `tools/` 作为上下文），按各 Task 的落点组装成文件后跑全量：

```
venv/bin/python -m pytest tools/liaison/tests/{test_liaison_schema,test_liaison_effects,test_app_does_not_import_tools}.py -q
→ 49 passed（pytest 8.3.4 / Python 3.14.6）
```

逐文件：`test_liaison_schema.py` 26 passed（Task 1 独立跑 7 passed）· `test_liaison_effects.py` 19 passed · `test_app_does_not_import_tools.py` 4 passed。临时目录已清理。

提取验证过程中修掉的 1 个真实缺陷：`test_task_source_columns_are_not_null` 原写法在 `pytest.raises` 块里放了一条合法 INSERT 加 `raise AssertionError("unreachable")`，是个自相矛盾的死写法，已改为 `parametrize` 逐列置 NULL（这也是 Task 2 预期计数从 22 变成 26 的原因）。

**边界**：这些验证只证明**代码可执行且内部自洽**，不证明**符合 spec**。spec 合规由 `run-build` 的两阶段 review 负责，⛔ 这一步不是它的替代品。

---

## 下一步

用 `run-build` 执行本计划。⛔ 本计划只出计划，不进实现。
