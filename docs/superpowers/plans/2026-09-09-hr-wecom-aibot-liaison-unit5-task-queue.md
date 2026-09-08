# 值守任务队列（hr-wecom-aibot-liaison 交付单元 5）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让"收到的材料变成一条可追踪状态的待办"这件事有一个不会被消息内容破坏、不会被并发覆盖、也不会因为崩溃重投而丢失的真身——队列真身是 `liaison.db` 的 `liaison_task` 表，Markdown 只是一份单向的只读导出视图。

**Architecture:**

```
tools/liaison/                       ← ⛔ 不在 sync-to-server.sh 的 SYNC_PATHS 里，结构上到不了 .51（design D10）
├── storage/schema.py                ← 【本章一个字都不改】liaison_task 表、三态 CHECK、
│                                       pushed_at 等式 CHECK、defer TRIGGER 第 2 章已建好
├── storage/effects.py               ← 【本章一个字都不改】effect_enqueue_task 第 2 章已写好
├── queue.py                         ← 本章主体之一：入队接线 + 状态流转 + 回指查询
│   ├── enqueue_task(conn, ...) -> bool                        5.1 / 5.2 包一层 effect_enqueue_task
│   ├── effect_defer_task      (@idempotent_effect)            5.3 UPDATE，⛔ 不进 EFFECT_NODE_TO_TABLE
│   ├── effect_mark_task_pushed(@idempotent_effect)            5.3 UPDATE，⛔ 不进 EFFECT_NODE_TO_TABLE
│   ├── load_task_source(conn, task_id) -> TaskSource | None   5.5 条目 → 消息 → 全部附件
│   └── MissingSourceMessage / TaskNotFound / TaskTransitionRejected
├── queue_view.py                    ← 本章主体之二：只读导出视图
│   ├── escape_cell(text) -> str                               纯函数，竖线/换行/控制字符转义
│   ├── render_queue_markdown(conn, generated_at) -> str       纯渲染，⛔ 不碰文件系统
│   └── export_queue_markdown(conn, out_path, generated_at)    本模块唯一写文件的地方
├── inbound.py                       ← 【只加两行 + 改 docstring】should_enqueue 后面接上入队
└── tests/
    ├── test_queue_enqueue.py        5.1 / 5.2 / 5.10
    ├── test_queue_status.py         5.3 / 5.9
    ├── test_queue_view.py           5.4 / 5.8 + 单向性静态断言
    ├── test_queue_source_lookup.py  5.5
    ├── test_queue_concurrency.py    5.6 / 5.7 / 5.10（多线程 + 多进程）
    └── test_inbound_routing.py      【修改】删掉"第 5 章还没来"的钉子，换成真接线断言
```

开工前必须先读懂的**七条判断**，改动前逐条对照。它们不是背景说明，是本章最容易踩且踩了会静默出错的地方。

**1. 队列真身是表，不是 Markdown。这一条是本章存在的理由。**
参考服务的三个生产 bug——「队列越界写入」（消息含竖线破坏表格结构）、「队列追加并发覆盖」、以及靠"竖线归一化 + 9 条边界列数自检"才能勉强挡住的结构损坏——**全部源于同一个根因：拿 Markdown 表格当数据库**（design D11、06 清单 §3.4）。本章不复现这个根因：任意消息内容（含竖线、换行、控制字符、超长文本）在本实现里都只是 `liaison_task.summary` / `liaison_message.content` 列里的一个字符串值，**不可能破坏结构**。
⛔ **因此本章不许出现任何"竖线归一化"「入队前清洗内容」的代码。** 那是给 Markdown-当-数据库那套形态打的补丁，在本形态下是纯粹的信息损失：它会悄悄改掉用户真正发来的字。转义只发生在**渲染层**（`queue_view.escape_cell`），存储层原样存。

**2. 存储层已经把三态与转移规则钉死了，本章 ⛔ 不许在应用层再实现一遍。**
`storage/schema.py` 第 2 章已经建好这三道（原文去读，不要凭记忆）：
- `CHECK (send_status IN ('pending', 'deferred', 'pushed'))` —— 非法取值被拒；
- `CHECK ((send_status = 'pushed') = (pushed_at IS NOT NULL))` —— 「已推送必带时间戳」与「未推送必不带时间戳」双向同时钉住；
- `TRIGGER trg_liaison_task_defer_only_from_pending` —— `BEFORE UPDATE`，`NEW.send_status = 'deferred' AND OLD.send_status <> 'pending'` 时 `RAISE(ABORT, ...)`。

**opener 要求的"存储层 TRIGGER 或应用层校验二选一"，本计划写死选前者：存储层是唯一真源。**
应用层（`queue.py`）只做一件事——把 SQLite 抛出的 `sqlite3.IntegrityError` 翻译成本模块的领域异常 `TaskTransitionRejected`，**⛔ 不预判、不重复实现任何一条转移规则**。
*为什么写死*：两处各实现一遍，两边就会分叉，而分叉**不报错**——应用层放行、存储层拒绝时你看到的是一个 IntegrityError；应用层拒绝、存储层其实允许时你**永远看不到**任何症状，只是某条合法转移莫名其妙做不了。真源只能有一个，而"业务层可以被绕过，表自己必须拒绝"（schema 原文注释）决定了这个真源必须是表。

**3. 🚨 本目录禁止写 `with open(...)`——这是本章最容易踩、且踩了会在一个毫不相干的测试里变红的坑。**
`tests/test_liaison_effects.py::test_no_second_transaction_manager_in_source` 会扫 `tools/liaison/**/*.py`（`tests` 目录除外），把**任何** `with <Name|Attribute|Call>:` 判为"隐式提交事务边界"违规——它认的是 `ast.With` / `ast.AsyncWith` 的形状，不看上下文管理器到底是不是数据库连接。白名单只有 `{"storage/db.py": {"init_schema"}}`。
于是 `queue_view.py` 里写 `with open(path, "w") as f:` 会报 `用 with 隐式提交事务`。本章一律用**不带 `with` 的写法**：`pathlib.Path.write_text()` + `os.replace()`。⛔ 同理不许用 `contextlib.suppress`（它也是 `with`）、不许用 `conn.executescript()`。
⛔ **不要为了写 `with` 去放宽那个扫描器或把本章文件加进白名单。** 它守的是工程铁律 1 唯一的静态判据，为一行文件写削弱它，代价与收益差着数量级。测试代码里可以照常用 `with`（扫描器跳过 `tests` 目录）。

**4. 两个 UPDATE 型 effect 必须定义在 `queue.py`，且 ⛔ 永远不许登记进 `EFFECT_NODE_TO_TABLE`。**
已核对第 2 章两条守卫测试的实际判据，确认这样写不会让它们变红：
- `test_effect_node_to_table_matches_reality` 比对的是 `vars(tools.liaison.storage.effects)` 里的 `effect_*` 函数集合——只看 `storage/effects.py` 这一个模块。本章的两个 effect 定义在 `queue.py`，不在它的取值范围内。
- `test_effect_node_to_table_matches_the_insert_target_repo_wide` 虽然全仓扫 `@idempotent_effect`，但 `_validate_node_to_table_mapping` 只遍历 **mapping 的键**；扫到了却没登记的 effect 不计入违规。

⛔ **`effect_defer_task` 与 `effect_mark_task_pushed` 永远不许登记**：它们是 UPDATE，不产生新行，登记进去会让"`effect_log` 条数 == 业务表行数"这条恒等式**因为一个完全正当的理由变红**，而那时最顺手的"修法"是去削弱恒等断言本身。**只有 INSERT 型 effect 才配登记。**
本章真正需要被恒等式覆盖的是 `effect_enqueue_task`，它第 2 章已登记为 `liaison_task`，本章 5.10 直接跑现成的 `assert_effect_log_identity` 即可，⛔ 不需要新写断言。

**5. 🔴 入队 ⛔ 不许用 `outcome.newly_archived` 做门槛——这是本章唯一会造成"永久丢失待办"的写法。**
`inbound.py` 现在的礼貌回复是 `if route.reply_text is not None and outcome.newly_archived:`——**回复该这么写，入队不该。**
想清楚崩溃点：归档事务已提交、进程在入队之前被杀。SDK 重连后重投同一 `msgid`：
- 归档幂等命中 ⇒ `newly_archived is False`；
- 如果入队跟着 `newly_archived` 走，这一条**永远不会入队**。材料在库里躺着，待办从来没出现过，而且**没有任何症状**。这正是铁律 1 那句"幂等本是防重复的保护，拆开事务后变成永久丢失的保证"换了个壳。

**正确写法：`route.should_enqueue` 为真就无条件调 `enqueue_task`，重复由 `effect_enqueue_task` 自己的幂等键挡住。**
两者不对称是刻意的，理由写进代码注释：**丢一条告知是可接受的（对方还能再问一次），丢一条待办不可接受（没有人知道它曾经存在）。** 礼貌回复重发是骚扰，入队重发是幂等空转。

**6. 顺序钉死：先归档、后入队。⛔ 不许反。**
`liaison_task.msgid` 有 `REFERENCES liaison_message (msgid)`，且 `get_connection` 开了 `PRAGMA foreign_keys = ON`。反过来会直接撞外键。这条外键同时就是 spec 的「条目缺少来源信息不允许写入」——不是额外加的校验，是同一件事。

**7. 导出的 Markdown 是单向的。⛔ 系统不从那个文件读任何东西。**
`queue_view.py` 里**一次都不许出现**读取路径的动作（`open(` / `read_text` / `read_bytes` / `json.load` / `iterdir`）。Task 4 有一条静态断言扫本模块源码把这条钉住。
人在导出文件里把某行改成「已推送」，下一次渲染会原样覆盖掉，数据库一个字都不变——这就是"避免两份真身"的全部含义。⛔ 不要好心加一个"检测到手改就提示/回填"的分支：那一加，文件就变成了第二份真身。

**Tech Stack:** Python 3.14（钉死 `>=3.14,<3.15`）· SQLite3（标准库，`data/liaison.db`）· pytest · `threading` / `multiprocessing`（5.7 真并发）。⛔ 本章不引入任何新的第三方依赖，`tools/liaison/requirements.txt` 与根 `pyproject.toml` 一行都不改。

## Global Constraints

以下条目从 `CLAUDE.md` 逐字复制，是本计划每个 Task 的隐含验收项，reviewer 按它审。

**工程铁律 1（逐字）**：

> **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
> **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
> *为什么*：业务写失败而幂等记录成功 → 系统判定"已执行"→ 永不重试。**幂等本是防重复的保护，拆开事务后变成永久丢失的保证。**

本章落点：入队幂等键 **`{thread_id}:effect_enqueue_task:{msgid}`**（`thread_id` = 私聊取 `userid`、群聊取 `chatid`；`business_key` = 企微 `msgid`）。⛔ 本章任何非测试代码都不许调 `conn.commit()` / `conn.rollback()` / `conn.executescript()`——提交由 `app.storage.idempotency.idempotent_effect` 独占。5.10 直接跑第 2 章的恒等脚手架 `tools.liaison.tests.test_liaison_effects.assert_effect_log_identity`。

**工程铁律 2（逐字）**：

> **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。

本章落点：`escape_cell` / `render_queue_markdown` 是纯函数（不碰文件系统、不读时钟——`generated_at` 由调用方传入）；只有 `effect_defer_task` / `effect_mark_task_pushed` / `export_queue_markdown` 有副作用。

**spec 逐字要求（`specs/liaison-task-queue/spec.md`）**：

> 值守任务队列的真身 SHALL 是结构化存储，其中消息内容只作为字段取值。任意消息内容——包含竖线、换行、制表符、控制字符、超长文本——MUST NOT 破坏队列的结构，MUST NOT 导致条目的字段错位或串行。

> 入队 SHALL 是有副作用的独立动作，带幂等键。幂等记录与队列条目的写入 MUST 在同一个事务内提交——两者 MUST NOT 分处不同事务。队列条目数与其幂等记录数 SHALL 按会话恒等。该不变式 MUST 有测试覆盖。

> 队列条目的发送状态 SHALL 取自固定的三个取值：待发、暂缓、已推送。存储层 SHALL 拒绝任何其他取值。「暂缓」SHALL 只能由「待发」转入。「已推送」SHALL 携带推送时间戳。

> 队列的文本（Markdown）呈现 SHALL 是从结构化存储渲染出的只读导出物。渲染 SHALL 对内容做转义，使内容中的表格分隔字符不破坏呈现。对导出的文本文件所做的任何修改 MUST NOT 回写到结构化存储，系统 MUST NOT 从该文本文件读取任何状态。

**opener 逐字约束**：

- **5.4**：Markdown 视图**单向只读**——⛔ 不从该文件读任何状态、不回写；竖线/换行转义。
- **5.3**：「暂缓」只能来自「待发」，「已推送」必带推送时间戳，非法取值/转移一律拒绝（存储层 TRIGGER 或应用层校验二选一，**本计划写死存储层 TRIGGER**）。
- **5.7**：并发入队各自成行——**真多线程/多进程测试，⛔ 不用顺序调用冒充并发**。判据见 Task 6：`threading.Barrier(N, timeout=...)` 不 `BrokenBarrierError` 才证明 N 个线程真的同时活着；顺序调用会在第一个线程上超时抛 `BrokenBarrierError`。
- **design D10 落点**：代码落 `tools/liaison/`。⛔ 不落 `app/`（会被同步到 `.51`）、⛔ 不落 `scripts/`（同样在 `SYNC_PATHS` 白名单里）。依赖只进 `tools/liaison/requirements.txt`。
- ⛔ **本章不许触碰**：`app/**`、`scripts/**`、`tools/liaison/session.py`、`tools/liaison/session_client.py`、`tools/liaison/alerts.py`、`tools/liaison/__main__.py`、`tools/liaison/storage/schema.py`、`tools/liaison/storage/effects.py`、`tools/liaison/storage/db.py`、`pyproject.toml`、`tools/liaison/requirements.txt`。别的泳道正在改其中一些文件，碰了就是撞车。

**合规红线（本章相关，逐字）**：

> **模型全部走境内**，简历数据不出境。

本服务不调用任何 LLM、不做任何 AI 评分，不产生 `analysis_run` / `criterion_score`（design D7）。⛔ 因此本章 **不许**引入任何"用模型生成摘要"的想法——`summary` 只能是消息内容的机械截断。
本服务全部通信对象是内部同事，**MUST NOT 用于向候选人发送任何内容**（design D7）。队列条目只是 Shao Peishen 本人的待办台账，不是任何对外通道。

---

### Task 1: 入队接线与"缺来源即拒绝"

把第 2 章已经写好的 `effect_enqueue_task` 包成一个业务层可用的入口：命中幂等要能分辨、缺来源要被拒、UNIQUE 撞车要按幂等处理而不是炸给调用方。

**Files:**
- Create: `tools/liaison/queue.py`
- Test: `tools/liaison/tests/test_queue_enqueue.py`

**Interfaces:**
- Consumes:
  - `tools.liaison.storage.effects.effect_enqueue_task(conn, *, thread_id, business_key, sender_userid, received_at, summary="") -> int | None`（命中幂等返回 `None`）
  - `tools.liaison.storage.db.get_connection(db_path) -> sqlite3.Connection` / `init_schema(conn) -> None`
  - `tools.liaison.storage.effects.effect_archive_message(conn, *, thread_id, business_key, sender_userid, received_at, msgtype, content="", attachments_json="[]")`（测试里造父行用）
- Produces（Task 2/3/5/6 全部依赖这些确切名字）:
  - `tools.liaison.queue.MissingSourceMessage(ValueError)`
  - `tools.liaison.queue.TaskNotFound(LookupError)`
  - `tools.liaison.queue.TaskTransitionRejected(ValueError)`
  - `tools.liaison.queue.compute_task_summary(content: str, *, msgtype: str, max_chars: int = 120) -> str`
  - `tools.liaison.queue.enqueue_task(conn, *, thread_id: str, msgid: str, sender_userid: str, received_at: str, summary: str = "") -> bool`（`True` = 这次真的新建了条目；`False` = 幂等命中）

- [ ] **Step 1: 写失败测试**

创建 `tools/liaison/tests/test_queue_enqueue.py`：

```python
"""5.1 / 5.2：入队接线与「缺来源即拒绝」。

⛔ 本文件不测渲染、不测状态流转——那是 test_queue_view.py 与 test_queue_status.py。
"""

import sqlite3

import pytest

from tools.liaison.queue import (
    MissingSourceMessage,
    compute_task_summary,
    enqueue_task,
)
from tools.liaison.storage import db as liaison_db
from tools.liaison.storage.effects import effect_archive_message
from tools.liaison.tests.test_liaison_effects import assert_effect_log_identity

RECEIVED_AT = "2026-09-09T10:00:00+08:00"


@pytest.fixture
def conn(tmp_path):
    c = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(c)
    yield c
    c.close()


def _archive(conn, *, thread_id, msgid, content="材料收到"):
    """造一条父台账行。队列条目的外键指向它，⛔ 没有它入队必被拒。"""
    effect_archive_message(
        conn,
        thread_id=thread_id,
        business_key=msgid,
        sender_userid=thread_id,
        received_at=RECEIVED_AT,
        msgtype="text",
        content=content,
    )


def test_enqueue_writes_one_row_carrying_the_source_handles(conn):
    """spec：每条队列条目 SHALL 携带发送人标识、来源会话标识、来源消息唯一标识、接收时间。"""
    _archive(conn, thread_id="u_zhang", msgid="msg-1")

    assert enqueue_task(
        conn,
        thread_id="u_zhang",
        msgid="msg-1",
        sender_userid="u_zhang",
        received_at=RECEIVED_AT,
        summary="报价单已发",
    ) is True

    rows = conn.execute(
        "SELECT msgid, thread_id, sender_userid, received_at, summary, send_status, pushed_at "
        "FROM liaison_task"
    ).fetchall()
    assert rows == [("msg-1", "u_zhang", "u_zhang", RECEIVED_AT, "报价单已发", "pending", None)]
    assert_effect_log_identity(conn)


def test_enqueue_is_idempotent_on_redelivery(conn):
    """SDK 重连后重投同一 msgid：⛔ 不许产生第二条待办。"""
    _archive(conn, thread_id="u_zhang", msgid="msg-1")
    assert enqueue_task(
        conn, thread_id="u_zhang", msgid="msg-1", sender_userid="u_zhang", received_at=RECEIVED_AT
    ) is True
    assert enqueue_task(
        conn, thread_id="u_zhang", msgid="msg-1", sender_userid="u_zhang", received_at=RECEIVED_AT
    ) is False

    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 1
    assert_effect_log_identity(conn)


def test_enqueue_rejects_when_the_source_message_does_not_exist(conn):
    """5.2 / spec「条目缺少来源信息不允许写入」：外键是这条要求的真身。"""
    with pytest.raises(MissingSourceMessage):
        enqueue_task(
            conn,
            thread_id="u_zhang",
            msgid="msg-does-not-exist",
            sender_userid="u_zhang",
            received_at=RECEIVED_AT,
        )

    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 0
    # 铁律 1 的方向：业务写失败 ⇒ 幂等记录也必须不存在，否则重投时会被判"已执行"而永不重试。
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = 'effect_enqueue_task'"
    ).fetchone()[0] == 0
    assert_effect_log_identity(conn)


def test_enqueue_rejects_a_null_msgid(conn):
    """`msgid TEXT NOT NULL`：连来源标识都没有的条目，⛔ 一行都不许落。"""
    with pytest.raises(MissingSourceMessage):
        enqueue_task(
            conn,
            thread_id="u_zhang",
            msgid=None,
            sender_userid="u_zhang",
            received_at=RECEIVED_AT,
        )
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 0
    assert_effect_log_identity(conn)


def test_same_msgid_from_another_thread_is_treated_as_an_idempotent_hit(conn):
    """`msgid TEXT NOT NULL UNIQUE`：一条消息最多一条队列条目。

    幂等装饰器按 `{thread_id}:...:{msgid}` 去重，thread_id 换了就命不中；
    但 UNIQUE 会拦住。这是 schema 注释写的"两道防线"里的第二道——
    ⛔ 它必须表现为幂等命中（返回 False），不是抛给调用方的崩溃。
    """
    _archive(conn, thread_id="u_zhang", msgid="msg-1")
    assert enqueue_task(
        conn, thread_id="u_zhang", msgid="msg-1", sender_userid="u_zhang", received_at=RECEIVED_AT
    ) is True

    assert enqueue_task(
        conn, thread_id="chat_group", msgid="msg-1", sender_userid="u_li", received_at=RECEIVED_AT
    ) is False

    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 1
    assert_effect_log_identity(conn)


def test_summary_is_a_mechanical_truncation_never_a_generated_one():
    """⛔ 摘要只能是机械截断。本服务不调用任何 LLM（design D7 / 合规红线）。"""
    assert compute_task_summary("报价单已发", msgtype="text") == "报价单已发"

    long_text = "甲" * 500
    truncated = compute_task_summary(long_text, msgtype="text", max_chars=120)
    assert len(truncated) == 120
    assert truncated.endswith("…")
    assert truncated[:119] == "甲" * 119


def test_summary_keeps_the_raw_characters_including_pipes_and_newlines():
    """⛔ 入队前不许做任何"竖线归一化"。转义只发生在渲染层（判断 1）。"""
    raw = "第一行|第二列\n第二行"
    assert compute_task_summary(raw, msgtype="text") == raw


def test_summary_for_a_message_without_text_names_the_type():
    """图片/文件消息没有正文。摘要写成`[file]`比空字符串可用——但仍然不是生成的内容。"""
    assert compute_task_summary("", msgtype="file") == "[file]"
    assert compute_task_summary("   ", msgtype="image") == "[image]"


def test_queue_module_never_commits_by_itself():
    """提交由 idempotent_effect 独占（第 2 章的单一事务管理者约束）。"""
    import ast
    import pathlib

    from tools.liaison import queue as queue_module

    tree = ast.parse(pathlib.Path(queue_module.__file__).read_text(encoding="utf-8"))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("commit", "rollback", "executescript")
    ]
    assert offenders == [], f"queue.py 里出现了事务边界调用，行号 {offenders}"
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `pytest tools/liaison/tests/test_queue_enqueue.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'tools.liaison.queue'`（collection error，全部 9 条都收不上来）。

- [ ] **Step 3: 写最小实现**

创建 `tools/liaison/queue.py`：

```python
"""值守任务队列的业务层：入队、状态流转、条目回指材料。

**队列真身是 `liaison.db` 的 `liaison_task` 表**（design D11）。Markdown 只是
`queue_view.py` 渲染出的只读导出物，⛔ 系统不从那个文件读任何状态。

⛔ **本模块不做任何"内容清洗"**：不归一化竖线、不删换行、不截断正文。
参考服务需要那些是因为它拿 Markdown 表格当数据库；本形态下任意字符都只是列里的
一个字符串值，清洗只会静默改掉用户真正发来的字。转义在渲染层做（`escape_cell`）。

⛔ **本模块不许出现 `conn.commit()` / `conn.rollback()` / `conn.executescript()`。**
提交由 `idempotent_effect` 独占——它在同一个隐式事务里写业务行与 `effect_log` 行
然后一次性提交。自己再提交一次就是制造第二个事务管理者，也就是
`docs/findings/2026-08-13-sqlite-事务归属冲突.md` 那场事故的成因。

⛔ **本模块不许写 `with` 语句**（含 `contextlib.suppress`）：第 2 章的
`test_no_second_transaction_manager_in_source` 把 `tools/liaison/` 非测试代码里
任何 `with X:` 判为隐式提交违规，它认形状不认语义。
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
from dataclasses import dataclass

from app.storage.idempotency import idempotent_effect
from tools.liaison.archive import DEFAULT_ARCHIVE_ROOT
from tools.liaison.attachments import StoredAttachment
from tools.liaison.storage.effects import effect_enqueue_task

#: 摘要的默认长度上限（字符数，不是字节）。摘要只进 Markdown 视图与队列列表，
#: 不参与任何完整性校验，所以按字符截断即可——⛔ 不要在这里引入字节口径，
#: 那是第 6 章外发长度守卫的事，两个数字来源不同，共用等于埋一个静默截断。
DEFAULT_SUMMARY_MAX_CHARS = 120


class MissingSourceMessage(ValueError):
    """队列条目没有可回指的来源消息（`msgid` 为空，或库里根本没有那条消息）。

    spec「条目缺少来源信息不允许写入」的领域异常形态。真正拒绝它的是
    `liaison_task.msgid` 的 `NOT NULL` 与 `REFERENCES liaison_message (msgid)`，
    本异常只是把 sqlite 的 `IntegrityError` 翻译成调用方能分辨的东西。
    """


class TaskNotFound(LookupError):
    """要改状态的队列条目不存在。

    ⚠️ 这条异常存在的理由是：`UPDATE ... WHERE msgid = ?` 匹配 0 行时 SQLite
    **不报错**。不显式检查 `rowcount`，一次针对不存在条目的"推送标记"会安静成功、
    并且留下一条 `effect_log` 记录——从此这个 msgid 的推送标记**永远不会重试**。
    这正是铁律 1 那句"幂等本是防重复的保护，拆开后变成永久丢失的保证"。
    """


class TaskTransitionRejected(ValueError):
    """存储层拒绝了这次状态取值或状态转移。

    ⛔ 本模块**不预判**任何一条转移规则——三态 CHECK、`pushed_at` 等式 CHECK、
    `trg_liaison_task_defer_only_from_pending` 触发器都在 `storage/schema.py` 里，
    那是唯一真源（本计划判断 2）。本异常只是它的翻译层。
    """


def compute_task_summary(
    content: str, *, msgtype: str, max_chars: int = DEFAULT_SUMMARY_MAX_CHARS
) -> str:
    """纯函数（铁律 2 的形状）：把消息正文压成一行摘要。

    **机械截断，⛔ 不调用任何模型。** 本服务不做 AI 评分、不调 LLM（design D7），
    队列摘要更没有理由成为第一个例外——那会让一条待办的文字来源变得不可解释。

    ⛔ 不做竖线/换行清洗：原样保留，转义由渲染层负责（本模块 docstring）。
    """
    text = content if isinstance(content, str) else ""
    if not text.strip():
        # 图片/文件/语音这类没有正文的消息。写成 `[file]` 而不是空串——
        # 空摘要在队列视图里看不出这条待办到底是什么。
        return f"[{msgtype}]"
    if len(text) <= max_chars:
        return text
    # 截断标记占一个字符，保证返回长度恰好等于 max_chars。
    return text[: max_chars - 1] + "…"


def enqueue_task(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    msgid: str,
    sender_userid: str,
    received_at: str,
    summary: str = "",
) -> bool:
    """把一条已归档的消息变成一条值守任务。返回 `True` 表示这次真的新建了条目。

    ⚠️ **调用方必须先归档再入队**（`liaison_task.msgid` 外键指向 `liaison_message`）。
    顺序反了会直接撞外键——这不是额外校验，它就是 spec「条目缺少来源信息不允许写入」。

    🔴 **⛔ 不要用 `ArchiveOutcome.newly_archived` 给这次调用加门槛。**
    归档已提交、入队之前进程被杀，重投时归档幂等命中（`newly_archived is False`），
    跟着它走就意味着这条待办**永远不会出现**，而且没有任何症状。重复由本函数下面
    的幂等键与 UNIQUE 两道防线挡住，代价只是一次空转。
    （礼貌回复相反——那条该跟 `newly_archived` 走，重发是骚扰。丢一条告知可接受，
    丢一条待办不可接受。）
    """
    try:
        applied = effect_enqueue_task(
            conn,
            thread_id=thread_id,
            business_key=msgid,
            sender_userid=sender_userid,
            received_at=received_at,
            summary=summary,
        )
    except sqlite3.IntegrityError as exc:
        detail = str(exc)
        if "UNIQUE" in detail:
            # 第二道防线命中：这条消息早就有队列条目了（可能是另一个 thread_id 投的，
            # 幂等键因此没命中）。这是**正常路径**，⛔ 不是异常——一条消息最多一条待办
            # 本来就是 schema 的契约。装饰器已经回滚，库里没有半截写入。
            return False
        if "NOT NULL" in detail or "FOREIGN KEY" in detail:
            raise MissingSourceMessage(
                f"队列条目缺少可回指的来源消息：msgid={msgid!r} thread_id={thread_id!r}；"
                f"⛔ 必须先归档再入队。原始约束：{detail}"
            ) from exc
        raise
    return applied is not None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tools/liaison/tests/test_queue_enqueue.py -v`
Expected: PASS，9 passed。

- [ ] **Step 5: 跑第 2 章守卫，确认新模块没触雷**

Run: `pytest tools/liaison/tests/test_liaison_effects.py -v`
Expected: PASS，全绿。特别确认 `test_no_second_transaction_manager_in_source`、`test_effect_node_to_table_matches_reality`、`test_effect_node_to_table_matches_the_insert_target_repo_wide` 三条通过——若 `test_no_second_transaction_manager_in_source` 变红，多半是 `queue.py` 里写了 `with`（本计划判断 3）。

- [ ] **Step 6: 提交**

```bash
git add tools/liaison/queue.py tools/liaison/tests/test_queue_enqueue.py
git commit -m "feat(liaison): 入队接线 enqueue_task——外键缺失即拒、UNIQUE 撞车按幂等处理（5.1/5.2）"
```

---

### Task 2: 接进入站分支——名单内才入队

第 3/4 章留下的 `InboundRoute.should_enqueue` 一直是个没人读的布尔值，本 Task 把它接上。这是本章唯一触碰既有文件的地方。

**Files:**
- Modify: `tools/liaison/inbound.py`（module docstring、`handle_inbound_message` 尾部、`InboundResult`）
- Modify: `tools/liaison/tests/test_inbound_routing.py`（删掉 `test_inbound_module_never_references_the_enqueue_effect`，换成真接线断言）
- Test: `tools/liaison/tests/test_inbound_routing.py`

**Interfaces:**
- Consumes: `tools.liaison.queue.enqueue_task`、`tools.liaison.queue.compute_task_summary`（Task 1）
- Produces: `InboundResult` 多一个字段 `enqueued: bool`（Task 6 的端到端并发用例读它）

- [ ] **Step 1: 先删掉那颗钉子，再写新的失败测试**

`tools/liaison/tests/test_inbound_routing.py` 里**整段删除** `test_inbound_module_never_references_the_enqueue_effect`（它的 docstring 原文写明"第 5 章要做的是在 `should_enqueue` 后面加一行，那时候把这条断言删掉"——现在正是那时候）。

⛔ **不要改成 `assert "effect_enqueue_task" in source` 之类的反向断言**：那是把一个刻意的临时钉子改造成永久约束，而"inbound 必须直接引用某个函数名"从来不是任何一条要求。删掉即可。

在同一文件末尾追加：

```python
def test_admitted_message_is_archived_and_enqueued(tmp_path, conn, whitelist_file):
    """4.10 + 5.1：名单内 ⇒ 归档 + 入队，且两者都只发生一次。"""
    result = handle_inbound_message(
        conn,
        thread_id="u_zhang",
        msgid="msg-1",
        sender_userid="u_zhang",
        received_at="2026-09-09T10:00:00+08:00",
        msgtype="text",
        content="报价单已发",
        archive_root=tmp_path / "archive",
        whitelist_path=whitelist_file,
    )

    assert result.route.should_enqueue is True
    assert result.enqueued is True
    rows = conn.execute("SELECT msgid, summary, send_status FROM liaison_task").fetchall()
    assert rows == [("msg-1", "报价单已发", "pending")]


def test_outsider_message_is_archived_but_never_enqueued(tmp_path, conn, whitelist_file):
    """4.10 逐字：名单外只归档 + 礼貌回复，⛔ 不生成任何队列条目。"""
    result = handle_inbound_message(
        conn,
        thread_id="u_stranger",
        msgid="msg-2",
        sender_userid="u_stranger",
        received_at="2026-09-09T10:00:00+08:00",
        msgtype="text",
        content="你好",
        archive_root=tmp_path / "archive",
        whitelist_path=whitelist_file,
        reply=lambda *_: None,
    )

    assert result.route.should_enqueue is False
    assert result.enqueued is False
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 1


def test_enqueue_happens_even_when_the_archive_was_an_idempotent_hit(
    tmp_path, conn, whitelist_file
):
    """🔴 本章最重要的一条回归。

    模拟"归档已提交、入队之前进程被杀"：先只跑归档，再走完整入站。
    第二次归档会幂等命中（`newly_archived is False`）；如果入队跟着这个布尔值走，
    这条待办就**永远不会出现**且毫无症状。断言它照样入队。
    """
    from tools.liaison.archive import archive_message

    archive_message(
        conn,
        thread_id="u_zhang",
        msgid="msg-1",
        sender_userid="u_zhang",
        received_at="2026-09-09T10:00:00+08:00",
        msgtype="text",
        content="报价单已发",
        archive_root=tmp_path / "archive",
    )
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 0

    result = handle_inbound_message(
        conn,
        thread_id="u_zhang",
        msgid="msg-1",
        sender_userid="u_zhang",
        received_at="2026-09-09T10:00:00+08:00",
        msgtype="text",
        content="报价单已发",
        archive_root=tmp_path / "archive",
        whitelist_path=whitelist_file,
    )

    assert result.outcome.newly_archived is False, "前置没造对：这次归档应当是幂等命中"
    assert result.enqueued is True, (
        "归档幂等命中时没有入队——这条待办已经永久丢失。⛔ 入队不许用 newly_archived 做门槛"
    )
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 1


def test_redelivering_the_same_message_twice_yields_exactly_one_task(
    tmp_path, conn, whitelist_file
):
    """SDK 重连重投：待办 ⛔ 不许变成两条。"""
    for _ in range(2):
        handle_inbound_message(
            conn,
            thread_id="u_zhang",
            msgid="msg-1",
            sender_userid="u_zhang",
            received_at="2026-09-09T10:00:00+08:00",
            msgtype="text",
            content="报价单已发",
            archive_root=tmp_path / "archive",
            whitelist_path=whitelist_file,
        )
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 1
```

⚠️ **`conn` 与 `whitelist_file` 两个 fixture 沿用该文件里已有的定义，⛔ 不要新写一份同名 fixture。** 实现前先 `grep -n "def conn\|def whitelist_file" tools/liaison/tests/test_inbound_routing.py` 确认它们的确切名字与签名；如果既有 fixture 叫别的名字（例如 `roster_file`），**改测试去适配既有 fixture**，⛔ 不要改既有 fixture 去适配这段新代码——那个文件里别的用例正靠着它。

- [ ] **Step 2: 跑测试确认它失败**

Run: `pytest tools/liaison/tests/test_inbound_routing.py -v`
Expected: FAIL —— `AttributeError: 'InboundResult' object has no attribute 'enqueued'`，四条新用例全红。

- [ ] **Step 3: 改 `inbound.py`**

在 import 段加：

```python
from tools.liaison.queue import compute_task_summary, enqueue_task
```

`InboundResult` 加一个字段：

```python
@dataclass(frozen=True)
class InboundResult:
    route: InboundRoute
    outcome: ArchiveOutcome
    replied: bool
    enqueued: bool = False
```

（给默认值是为了不打断该文件里已有的构造点；实现时**把所有构造点都显式传上 `enqueued=`**，默认值只作为向后兼容的安全网。）

`handle_inbound_message` 在归档之后、礼貌回复之前插入入队：

```python
    enqueued = False
    if route.should_enqueue:
        # 🔴 ⛔ 这里**不许**加 `and outcome.newly_archived`。
        # 归档已提交、入队之前进程被杀 ⇒ 重投时归档幂等命中 ⇒ 跟着它走
        # 这条待办就永远不会出现，且没有任何症状（工程铁律 1 的失效方向）。
        # 重复由 effect_enqueue_task 的幂等键与 liaison_task.msgid 的 UNIQUE
        # 两道防线挡住，代价只是一次空转。
        # 礼貌回复相反（下面那段仍然跟 newly_archived 走）：丢一条告知可接受，
        # 丢一条待办不可接受。
        enqueued = enqueue_task(
            conn,
            thread_id=thread_id,
            msgid=msgid,
            sender_userid=sender_userid,
            received_at=received_at,
            summary=compute_task_summary(content, msgtype=msgtype),
        )
```

并把函数末尾的 `return InboundResult(...)` 补上 `enqueued=enqueued`。

同时更新模块 docstring：把「⛔ **本模块不入队。**」那一段替换为：

```
3. **名单内的消息入队**——`InboundRoute.should_enqueue` 为真时调 `queue.enqueue_task`；
   🔴 ⛔ **不加 `newly_archived` 门槛**，理由见 `handle_inbound_message` 里的注释。
4. 名单外回一条礼貌说明，且 ⛔ **MUST NOT 生成任何值守任务队列条目**。
```

⛔ **不要顺手改礼貌回复那段的条件**。它现在的 `and outcome.newly_archived` 是对的，`inbound.py` 的 docstring 已写明理由。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tools/liaison/tests/test_inbound_routing.py -v`
Expected: PASS，全绿（含该文件原有的全部用例）。

- [ ] **Step 5: 跑第 4 章相关全量，确认接线没打破归档**

Run: `pytest tools/liaison/tests/ -v`
Expected: PASS。若 `test_liaison_effects.py::test_no_second_transaction_manager_in_source` 变红，检查 `inbound.py` 是否被顺手加了 `with`。

- [ ] **Step 6: 提交**

```bash
git add tools/liaison/inbound.py tools/liaison/tests/test_inbound_routing.py
git commit -m "feat(liaison): 名单内消息入队接线；⛔ 不以 newly_archived 为门槛（5.1）"
```

---

### Task 3: 状态流转——三态、暂缓只能来自待发、已推送必带时间戳

**Files:**
- Modify: `tools/liaison/queue.py`（追加两个 effect + 两个只读查询）
- Test: `tools/liaison/tests/test_queue_status.py`

**Interfaces:**
- Consumes: `app.storage.idempotency.idempotent_effect`、Task 1 的 `TaskNotFound` / `TaskTransitionRejected`
- Produces:
  - `tools.liaison.queue.effect_defer_task(conn, *, thread_id, business_key) -> str | None`
  - `tools.liaison.queue.effect_mark_task_pushed(conn, *, thread_id, business_key, pushed_at) -> str | None`
  - `tools.liaison.queue.defer_task(conn, *, thread_id, msgid) -> bool`
  - `tools.liaison.queue.mark_task_pushed(conn, *, thread_id, msgid, pushed_at) -> bool`
  - `tools.liaison.queue.list_tasks(conn) -> list[sqlite3.Row]`（Task 4 渲染用；按 `received_at, id` 升序）

- [ ] **Step 1: 写失败测试**

创建 `tools/liaison/tests/test_queue_status.py`：

```python
"""5.3 / 5.9：三态、非法取值被拒、非法转移被拒、已推送必带时间戳。

**真源是存储层**（`storage/schema.py` 的两条 CHECK + defer 触发器）。
本文件同时从两侧验：直接写库（证明表自己会拒）与走 `queue.py`（证明翻译层没吞掉）。
"""

import sqlite3

import pytest

from tools.liaison.queue import (
    TaskNotFound,
    TaskTransitionRejected,
    defer_task,
    enqueue_task,
    list_tasks,
    mark_task_pushed,
)
from tools.liaison.storage import db as liaison_db
from tools.liaison.storage.effects import effect_archive_message
from tools.liaison.storage.schema import SEND_STATUSES
from tools.liaison.tests.test_liaison_effects import assert_effect_log_identity

RECEIVED_AT = "2026-09-09T10:00:00+08:00"
PUSHED_AT = "2026-09-09T11:30:00+08:00"


@pytest.fixture
def conn(tmp_path):
    c = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(c)
    yield c
    c.close()


@pytest.fixture
def task(conn):
    effect_archive_message(
        conn,
        thread_id="u_zhang",
        business_key="msg-1",
        sender_userid="u_zhang",
        received_at=RECEIVED_AT,
        msgtype="text",
        content="报价单已发",
    )
    enqueue_task(
        conn,
        thread_id="u_zhang",
        msgid="msg-1",
        sender_userid="u_zhang",
        received_at=RECEIVED_AT,
        summary="报价单已发",
    )
    return "msg-1"


def _status(conn, msgid):
    return conn.execute(
        "SELECT send_status, pushed_at FROM liaison_task WHERE msgid = ?", (msgid,)
    ).fetchone()


def test_the_three_states_are_exactly_these(conn):
    assert SEND_STATUSES == ("pending", "deferred", "pushed")


def test_new_task_starts_pending(conn, task):
    assert _status(conn, task) == ("pending", None)


def test_defer_from_pending_is_allowed(conn, task):
    assert defer_task(conn, thread_id="u_zhang", msgid=task) is True
    assert _status(conn, task) == ("deferred", None)
    assert_effect_log_identity(conn)


def test_mark_pushed_carries_the_timestamp(conn, task):
    """spec：「已推送」SHALL 携带推送时间戳。"""
    assert mark_task_pushed(
        conn, thread_id="u_zhang", msgid=task, pushed_at=PUSHED_AT
    ) is True
    assert _status(conn, task) == ("pushed", PUSHED_AT)
    assert_effect_log_identity(conn)


def test_defer_from_pushed_is_rejected(conn, task):
    """spec 逐字场景：把一条「已推送」条目改为「暂缓」⇒ 该转移被拒绝。"""
    mark_task_pushed(conn, thread_id="u_zhang", msgid=task, pushed_at=PUSHED_AT)

    with pytest.raises(TaskTransitionRejected):
        defer_task(conn, thread_id="u_zhang", msgid=task)

    assert _status(conn, task) == ("pushed", PUSHED_AT), "被拒的转移 ⛔ 不许留下任何痕迹"
    assert_effect_log_identity(conn)


def test_defer_from_deferred_is_rejected(conn, task):
    """触发器的条件是 `OLD.send_status <> 'pending'`——deferred → deferred 同样被拒。

    ⚠️ 这也意味着 `defer_task` 对同一条已暂缓的条目**不是**幂等的：第二次会抛。
    这是存储层刻意的形状（「暂缓」只能来自「待发」），⛔ 不许为了"看起来更幂等"
    去改触发器或在应用层吞掉这个异常。
    """
    defer_task(conn, thread_id="u_zhang", msgid=task)
    with pytest.raises(TaskTransitionRejected):
        defer_task(conn, thread_id="u_zhang", msgid=task)
    assert _status(conn, task) == ("deferred", None)


def test_mark_pushed_twice_is_an_idempotent_no_op(conn, task):
    """同一 msgid 第二次标记推送 ⇒ 幂等命中返回 False，时间戳 ⛔ 不许被覆盖。"""
    assert mark_task_pushed(conn, thread_id="u_zhang", msgid=task, pushed_at=PUSHED_AT) is True
    assert mark_task_pushed(
        conn, thread_id="u_zhang", msgid=task, pushed_at="2026-09-09T23:59:59+08:00"
    ) is False
    assert _status(conn, task) == ("pushed", PUSHED_AT)
    assert_effect_log_identity(conn)


def test_mark_pushed_without_a_timestamp_is_rejected(conn, task):
    """`CHECK ((send_status='pushed') = (pushed_at IS NOT NULL))` 的一半。"""
    with pytest.raises(TaskTransitionRejected):
        mark_task_pushed(conn, thread_id="u_zhang", msgid=task, pushed_at=None)
    assert _status(conn, task) == ("pending", None)


def test_storage_rejects_a_status_outside_the_three(conn, task):
    """从存储层直接验：⛔ 绕过应用层也拒。这条证明真源确实在表上。"""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE liaison_task SET send_status = 'archived' WHERE msgid = ?", (task,))
    assert _status(conn, task) == ("pending", None)


def test_storage_rejects_a_pending_row_carrying_a_pushed_at(conn, task):
    """等式 CHECK 的另一半：未推送 ⛔ 不许带时间戳。"""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE liaison_task SET pushed_at = ? WHERE msgid = ?", (PUSHED_AT, task)
        )


def test_status_change_on_a_missing_task_raises_and_leaves_no_effect_log(conn):
    """🔴 `UPDATE ... WHERE msgid = ?` 匹配 0 行时 SQLite **不报错**。

    不检查 rowcount，这次调用会安静"成功"并留下 effect_log 记录，
    从此这个 msgid 的推送标记永远不会重试——铁律 1 的失效方向本尊。
    """
    with pytest.raises(TaskNotFound):
        mark_task_pushed(
            conn, thread_id="u_zhang", msgid="msg-does-not-exist", pushed_at=PUSHED_AT
        )
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = 'effect_mark_task_pushed'"
    ).fetchone()[0] == 0

    with pytest.raises(TaskNotFound):
        defer_task(conn, thread_id="u_zhang", msgid="msg-does-not-exist")
    assert conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = 'effect_defer_task'"
    ).fetchone()[0] == 0


def test_update_effects_are_not_registered_in_effect_node_to_table(conn):
    """⛔ UPDATE 型 effect 永远不许登记进 EFFECT_NODE_TO_TABLE。

    它们不产生新行，登记进去会让「effect_log 条数 == 业务表行数」这条恒等式
    因为一个完全正当的理由变红——而那时最顺手的"修法"是削弱恒等断言本身，
    也就是把铁律 1 唯一的机器守卫拆掉。
    """
    from tools.liaison.storage.effects import EFFECT_NODE_TO_TABLE

    assert "effect_defer_task" not in EFFECT_NODE_TO_TABLE
    assert "effect_mark_task_pushed" not in EFFECT_NODE_TO_TABLE


def test_list_tasks_orders_by_received_at_then_id(conn):
    for index, msgid in enumerate(("msg-b", "msg-a"), start=1):
        effect_archive_message(
            conn,
            thread_id="u_zhang",
            business_key=msgid,
            sender_userid="u_zhang",
            received_at=f"2026-09-09T1{index}:00:00+08:00",
            msgtype="text",
            content=msgid,
        )
        enqueue_task(
            conn,
            thread_id="u_zhang",
            msgid=msgid,
            sender_userid="u_zhang",
            received_at=f"2026-09-09T1{index}:00:00+08:00",
            summary=msgid,
        )
    assert [row["msgid"] for row in list_tasks(conn)] == ["msg-b", "msg-a"]
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `pytest tools/liaison/tests/test_queue_status.py -v`
Expected: FAIL —— `ImportError: cannot import name 'defer_task' from 'tools.liaison.queue'`。

- [ ] **Step 3: 在 `queue.py` 追加实现**

```python
def _require_single_row(cursor, *, msgid: str, action: str) -> None:
    """UPDATE 必须命中恰好一行，否则抛 `TaskNotFound`。

    🔴 SQLite 对匹配 0 行的 UPDATE **不报错**。不显式检查 rowcount，装饰器就会
    照常写下 `effect_log` 行并提交——这次"成功"是假的，而这个 msgid 的该动作
    从此**永远不会重试**（铁律 1 的失效方向）。
    在 `@idempotent_effect` 函数体里抛异常是安全的：装饰器会回滚并把异常原样抛出，
    半截写入与幂等记录都不会留下。
    """
    if cursor.rowcount != 1:
        raise TaskNotFound(f"{action} 找不到队列条目：msgid={msgid!r}（命中 {cursor.rowcount} 行）")


@idempotent_effect("effect_defer_task")
def effect_defer_task(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str
) -> str:
    """把一条队列条目转入「暂缓」。`business_key` 即 `msgid`。

    ⛔ **本函数不判断旧状态。** 「暂缓只能来自待发」由
    `trg_liaison_task_defer_only_from_pending` 触发器拒绝——那是唯一真源。
    在这里再判一遍会制造第二份规则，两边分叉时**没有任何症状**。

    ⚠️ 本 effect 是 UPDATE，⛔ 永远不许登记进 `EFFECT_NODE_TO_TABLE`
    （见 `queue.py` 模块 docstring 与本计划判断 4）。
    """
    cursor = conn.execute(
        "UPDATE liaison_task SET send_status = 'deferred' WHERE msgid = ?", (business_key,)
    )
    _require_single_row(cursor, msgid=business_key, action="暂缓")
    return business_key


@idempotent_effect("effect_mark_task_pushed")
def effect_mark_task_pushed(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str, pushed_at: str
) -> str:
    """把一条队列条目标记为「已推送」并写入推送时间戳。

    ⛔ **不在这里取当前时间。** 时间戳由调用方传入——effect 读时钟会让
    "同一输入产生同一结果"不再成立，测试只能靠 monkeypatch 才写得动。
    「已推送必带时间戳」由表上的等式 CHECK 拒绝，⛔ 本函数不预判。

    ⚠️ UPDATE 型 effect，⛔ 不登记进 `EFFECT_NODE_TO_TABLE`。
    """
    cursor = conn.execute(
        "UPDATE liaison_task SET send_status = 'pushed', pushed_at = ? WHERE msgid = ?",
        (pushed_at, business_key),
    )
    _require_single_row(cursor, msgid=business_key, action="标记已推送")
    return business_key


def _translate_transition_error(exc: sqlite3.IntegrityError, *, msgid: str, action: str):
    return TaskTransitionRejected(
        f"存储层拒绝了这次状态变更：{action} msgid={msgid!r}。"
        f"三态 CHECK / pushed_at 等式 CHECK / 暂缓转移触发器之一命中。原始约束：{exc}"
    )


def defer_task(conn: sqlite3.Connection, *, thread_id: str, msgid: str) -> bool:
    """转入「暂缓」。返回 `True` 表示这次真的改了状态，`False` 表示幂等命中。

    ⚠️ 对一条已经是「暂缓」的条目再调一次会抛 `TaskTransitionRejected`
    （触发器的条件是 `OLD.send_status <> 'pending'`），⛔ 不许吞掉它——
    "暂缓只能来自待发"是 spec 的要求，不是可以体谅的边界情况。
    """
    try:
        applied = effect_defer_task(conn, thread_id=thread_id, business_key=msgid)
    except sqlite3.IntegrityError as exc:
        raise _translate_transition_error(exc, msgid=msgid, action="暂缓") from exc
    return applied is not None


def mark_task_pushed(
    conn: sqlite3.Connection, *, thread_id: str, msgid: str, pushed_at: str
) -> bool:
    """标记「已推送」。返回 `True` 表示这次真的改了状态，`False` 表示幂等命中。

    幂等命中时 ⛔ 不覆盖已有时间戳——第一次推送的那个时刻才是事实。
    """
    try:
        applied = effect_mark_task_pushed(
            conn, thread_id=thread_id, business_key=msgid, pushed_at=pushed_at
        )
    except sqlite3.IntegrityError as exc:
        raise _translate_transition_error(exc, msgid=msgid, action="标记已推送") from exc
    return applied is not None


def list_tasks(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """按接收时间升序列出全部队列条目。只读，⛔ 不写任何东西。

    显式设 `row_factory` 而不是要求调用方先设——渲染层按列名取值，
    位置索引在加列时会静默错位。
    """
    cursor = conn.execute(
        "SELECT id, msgid, thread_id, sender_userid, received_at, summary, "
        "send_status, pushed_at, created_at "
        "FROM liaison_task ORDER BY received_at, id"
    )
    cursor.row_factory = sqlite3.Row
    return cursor.fetchall()
```

⚠️ 实现时注意：`cursor.row_factory` 要在 `execute` **之后、`fetchall` 之前**设置才对已打开的游标生效。若实测无效，改成在 `execute` 前用 `conn.row_factory = sqlite3.Row` + 用完还原——但**⛔ 不许把 `conn.row_factory` 永久改掉**，别的模块（`consistency.py`、`test_liaison_effects.py`）按元组解包取值，改了会静默错位。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tools/liaison/tests/test_queue_status.py -v`
Expected: PASS，13 passed。

- [ ] **Step 5: 跑第 2 章守卫**

Run: `pytest tools/liaison/tests/test_liaison_effects.py -v`
Expected: PASS。两个新 effect 定义在 `queue.py` 且未登记进 `EFFECT_NODE_TO_TABLE`，三条守卫都不该变红（本计划判断 4 已核对判据）。**若 `test_effect_node_to_table_matches_the_insert_target_repo_wide` 变红，说明有人顺手把它们登记了——回退那次登记，⛔ 不要改测试。**

- [ ] **Step 6: 提交**

```bash
git add tools/liaison/queue.py tools/liaison/tests/test_queue_status.py
git commit -m "feat(liaison): 队列三态流转——存储层触发器为唯一真源，应用层只做翻译（5.3/5.9）"
```

---

### Task 4: 只读 Markdown 导出视图

**Files:**
- Create: `tools/liaison/queue_view.py`
- Test: `tools/liaison/tests/test_queue_view.py`

**Interfaces:**
- Consumes: Task 3 的 `tools.liaison.queue.list_tasks`
- Produces:
  - `tools.liaison.queue_view.escape_cell(text, *, max_chars=200) -> str`
  - `tools.liaison.queue_view.STATUS_LABELS: dict[str, str]`
  - `tools.liaison.queue_view.render_queue_markdown(conn, *, generated_at: str) -> str`
  - `tools.liaison.queue_view.export_queue_markdown(conn, *, out_path=DEFAULT_QUEUE_VIEW_PATH, generated_at: str) -> pathlib.Path`
  - `tools.liaison.queue_view.DEFAULT_QUEUE_VIEW_PATH: pathlib.Path`

- [ ] **Step 1: 写失败测试**

创建 `tools/liaison/tests/test_queue_view.py`：

```python
"""5.4 / 5.8：Markdown 只是只读导出物，且是**单向的**。

本文件最重要的两条是 `test_queue_view_module_never_reads_anything`（静态，钉住单向性）
与 `test_hand_edits_to_the_export_never_reach_the_database`（行为，钉住"没有第二份真身"）。
"""

import ast
import pathlib

import pytest

from tools.liaison import queue_view
from tools.liaison.queue import enqueue_task, mark_task_pushed
from tools.liaison.queue_view import (
    escape_cell,
    export_queue_markdown,
    render_queue_markdown,
)
from tools.liaison.storage import db as liaison_db
from tools.liaison.storage.effects import effect_archive_message

RECEIVED_AT = "2026-09-09T10:00:00+08:00"
GENERATED_AT = "2026-09-09T12:00:00+08:00"


@pytest.fixture
def conn(tmp_path):
    c = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(c)
    yield c
    c.close()


def _enqueue(conn, *, msgid, content, thread_id="u_zhang"):
    effect_archive_message(
        conn,
        thread_id=thread_id,
        business_key=msgid,
        sender_userid=thread_id,
        received_at=RECEIVED_AT,
        msgtype="text",
        content=content,
    )
    enqueue_task(
        conn,
        thread_id=thread_id,
        msgid=msgid,
        sender_userid=thread_id,
        received_at=RECEIVED_AT,
        summary=content,
    )


# ── 转义（纯函数） ────────────────────────────────────────────────────

def test_escape_cell_escapes_the_pipe():
    """spec：内容中的竖线以转义形式呈现。"""
    assert escape_cell("A|B") == r"A\|B"


def test_escape_cell_escapes_the_backslash_before_the_pipe_without_double_escaping():
    """⛔ 不许用链式 .replace 实现——那会把竖线规则插入的反斜杠再转义一遍。

    `str.translate` 是单遍替换，插入的字符不会被后续规则再看一次。
    """
    assert escape_cell(r"A\B") == r"A\\B"
    assert escape_cell(r"A\|B") == r"A\\\|B"


def test_escape_cell_turns_newlines_into_visible_two_char_sequences():
    """换行 ⇒ 字面 `\\n`。

    ⛔ 刻意不用 `<br>`：那是往一份只读文本里注入 HTML，
    而这个文件唯一的读者是 Shao Peishen 本人的编辑器。
    """
    assert escape_cell("第一行\n第二行") == r"第一行\n第二行"
    assert escape_cell("回车\r\n换行") == r"回车\r\n换行"
    assert escape_cell("制表\t符") == r"制表\t符"


def test_escape_cell_escapes_other_control_characters():
    assert escape_cell("空\x00字节") == r"空\x00字节"
    assert escape_cell("转义\x1b序列") == r"转义\x1b序列"
    assert escape_cell("删除\x7f符") == r"删除\x7f符"


def test_escape_cell_truncates_and_marks_it():
    long_text = "甲" * 500
    cell = escape_cell(long_text, max_chars=200)
    assert len(cell) == 200
    assert cell.endswith("…")


def test_escape_cell_keeps_cjk_and_emoji_intact():
    """⛔ 不许因为"看起来不是 ASCII"就转义。只有不可打印字符才转。"""
    assert escape_cell("报价单 📎 已发") == "报价单 📎 已发"


# ── 渲染（纯函数） ────────────────────────────────────────────────────

def test_render_keeps_the_table_shape_when_content_has_pipes_and_newlines(conn):
    """5.6 的渲染侧：任意内容 ⛔ 不许改变表格的列数或行数。"""
    _enqueue(conn, msgid="msg-1", content="a|b|c\nd|e")
    _enqueue(conn, msgid="msg-2", content="普通内容")

    text = render_queue_markdown(conn, generated_at=GENERATED_AT)
    table_rows = [line for line in text.splitlines() if line.startswith("|")]

    # 表头 + 分隔行 + 2 条数据 = 4 行，⛔ 一行都不许多
    assert len(table_rows) == 4
    column_counts = {row.count("|") - row.count(r"\|") for row in table_rows}
    assert len(column_counts) == 1, f"列数不一致，字段错位了：{column_counts}"


def test_render_shows_the_emoji_labels_but_the_database_stores_the_enum(conn):
    """design D11：存的是枚举值，emoji 只出现在渲染层。"""
    _enqueue(conn, msgid="msg-1", content="待处理")
    text = render_queue_markdown(conn, generated_at=GENERATED_AT)
    assert "🆕 待发" in text
    assert conn.execute("SELECT send_status FROM liaison_task").fetchone()[0] == "pending"


def test_render_shows_the_push_timestamp(conn):
    _enqueue(conn, msgid="msg-1", content="已发出")
    mark_task_pushed(
        conn, thread_id="u_zhang", msgid="msg-1", pushed_at="2026-09-09T11:30:00+08:00"
    )
    text = render_queue_markdown(conn, generated_at=GENERATED_AT)
    assert "✅ 已推送" in text
    assert "2026-09-09T11:30:00+08:00" in text


def test_render_carries_the_read_only_banner(conn):
    """人打开这个文件，第一眼就该知道改它没用。"""
    text = render_queue_markdown(conn, generated_at=GENERATED_AT)
    assert "只读导出" in text
    assert "不会回写" in text
    assert GENERATED_AT in text


def test_render_on_an_empty_queue_still_produces_a_table(conn):
    """⛔ 空队列不许渲染成空文件——那和"渲染失败"长得一模一样。"""
    text = render_queue_markdown(conn, generated_at=GENERATED_AT)
    assert "只读导出" in text
    assert text.count("\n|") >= 2  # 表头 + 分隔行仍在


# ── 单向性 ────────────────────────────────────────────────────────────

def test_hand_edits_to_the_export_never_reach_the_database(conn, tmp_path):
    """5.8 / spec 逐字场景：有人在导出文件里把状态改成「已推送」。"""
    _enqueue(conn, msgid="msg-1", content="报价单已发")
    out = tmp_path / "queue.md"
    export_queue_markdown(conn, out_path=out, generated_at=GENERATED_AT)

    tampered = out.read_text(encoding="utf-8").replace("🆕 待发", "✅ 已推送")
    tampered += "\n| 999 | ✅ 已推送 | 伪造 | 伪造 | 伪造 | 伪造 | 伪造 |\n"
    out.write_text(tampered, encoding="utf-8")

    # 真身一个字都没变
    assert conn.execute("SELECT send_status, pushed_at FROM liaison_task").fetchall() == [
        ("pending", None)
    ]
    assert conn.execute("SELECT COUNT(*) FROM liaison_task").fetchone()[0] == 1

    # 下一次渲染原样覆盖手改内容
    export_queue_markdown(conn, out_path=out, generated_at="2026-09-09T13:00:00+08:00")
    after = out.read_text(encoding="utf-8")
    assert "🆕 待发" in after
    assert "伪造" not in after
    assert "✅ 已推送" not in after


def test_export_leaves_no_temporary_file_behind(conn, tmp_path):
    _enqueue(conn, msgid="msg-1", content="报价单已发")
    out = tmp_path / "queue.md"
    export_queue_markdown(conn, out_path=out, generated_at=GENERATED_AT)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["liaison.db", "liaison.db-shm",
                                                          "liaison.db-wal", "queue.md"]


def test_queue_view_module_never_reads_anything():
    """🔴 单向性的静态判据：本模块**一次都不许**出现读取动作。

    行为测试只能证明"这一次没回写"；这条证明"源码里根本没有回写的路径"。
    ⛔ 不要为了加一个"检测手改并提示"的功能去放宽它——那一加，导出文件
    就变成了第二份真身，而"避免两份真身"正是 design D11 单向性的全部含义。
    """
    source = pathlib.Path(queue_view.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    forbidden_attrs = {"read_text", "read_bytes", "readline", "readlines", "read", "iterdir"}
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr in forbidden_attrs:
                offenders.append(f"L{node.lineno} .{node.func.attr}()")
            if isinstance(node.func, ast.Name) and node.func.id == "open":
                offenders.append(f"L{node.lineno} open()")
    assert offenders == [], f"queue_view.py 出现了读取动作，单向性破了：{offenders}"


def test_queue_view_module_has_no_with_statement():
    """第 2 章的事务扫描器把任何 `with X:` 判为隐式提交违规（本计划判断 3）。

    这条在本模块自己的测试里先拦一道，免得改动在一个毫不相干的测试里变红、
    让人以为是事务出了问题。
    """
    tree = ast.parse(pathlib.Path(queue_view.__file__).read_text(encoding="utf-8"))
    offenders = [
        node.lineno for node in ast.walk(tree) if isinstance(node, (ast.With, ast.AsyncWith))
    ]
    assert offenders == [], (
        f"queue_view.py 出现 with 语句，行号 {offenders}；"
        "改用 Path.write_text() + os.replace()"
    )
```

⚠️ `test_export_leaves_no_temporary_file_behind` 里的文件名列表依赖 WAL 模式实际产生的边车文件。实测若与 `-shm` / `-wal` 的出现时机不符，**改成只断言"没有以 `.tmp` 结尾的文件"**：`assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]`。⛔ 不要为了让断言好写去关掉 WAL——`journal_mode=WAL` 是 `get_connection` 的既定行为，第 2 章有理由。

- [ ] **Step 2: 跑测试确认它失败**

Run: `pytest tools/liaison/tests/test_queue_view.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'tools.liaison.queue_view'`。

- [ ] **Step 3: 写实现**

创建 `tools/liaison/queue_view.py`：

```python
"""队列的 Markdown 导出视图。**只读、单向、可随时重建。**

真身是 `liaison.db` 的 `liaison_task` 表（design D11）。本模块把它渲染成一份
给人看的 Markdown，⛔ **反过来一个字都不读**：人在导出文件里改了什么，下一次
渲染原样覆盖，数据库一个字都不变。

⛔ **本模块不许出现任何读取动作**（`open(` / `read_text` / `read_bytes` /
`iterdir`）。`tests/test_queue_view.py::test_queue_view_module_never_reads_anything`
用 AST 把这条钉死。加一个"检测到手改就提示/回填"的分支，等于把导出文件变成
第二份真身——那正是 design D11 单向性要消灭的东西。

⛔ **本模块不许写 `with` 语句**（含 `with open(...)`、`contextlib.suppress`）：
第 2 章的 `test_no_second_transaction_manager_in_source` 把 `tools/liaison/` 非测试
代码里任何 `with X:` 判为隐式提交事务违规，它认形状不认语义。
文件写一律 `pathlib.Path.write_text()` + `os.replace()`。
"""

from __future__ import annotations

import os
import pathlib
import sqlite3

from tools.liaison.queue import list_tasks
from tools.liaison.storage.db import REPO_ROOT

#: 默认导出落点。`data/` 已被 .gitignore 覆盖，导出物不会误入版本管理。
DEFAULT_QUEUE_VIEW_PATH = REPO_ROOT / "data" / "liaison" / "queue.md"

#: 枚举值 → 展示文案。**只在这里出现 emoji**（design D11：存的是枚举值，
#: emoji 只允许出现在渲染层）。⛔ 不要把这份映射倒过来用去解析文件——
#: 那就是从导出物读状态，单向性当场破掉。
STATUS_LABELS = {
    "pending": "🆕 待发",
    "deferred": "⏸ 暂缓",
    "pushed": "✅ 已推送",
}

#: 单元格的字符上限。超长内容在视图里截断，**真身不截断**——
#: 要看全文去 `liaison_message.content`，视图只是视图。
DEFAULT_CELL_MAX_CHARS = 200

#: 单遍替换表。**必须用 `str.translate` 而不是链式 `.replace`**：
#: 链式替换里，竖线规则插入的反斜杠会被反斜杠规则再转义一遍（或反过来漏转），
#: 取决于谁先跑——两种顺序都错，只是错法不同。`translate` 一遍扫完，
#: 插入的字符不会被后续规则再看一次。
_CELL_TRANSLATION = str.maketrans(
    {
        "\\": r"\\",
        "|": r"\|",
        "\n": r"\n",
        "\r": r"\r",
        "\t": r"\t",
    }
)

_TABLE_HEADER = "| ID | 状态 | 发送人 | 会话 | 接收时间 | 推送时间 | msgid | 摘要 |"
_TABLE_DIVIDER = "|---:|---|---|---|---|---|---|---|"


def escape_cell(text, *, max_chars: int = DEFAULT_CELL_MAX_CHARS) -> str:
    """纯函数：把任意字符串压成一个安全的表格单元格。

    竖线转义成 `\\|`、换行/回车/制表符转义成可见的两字符序列、其余不可打印字符
    转义成 `\\xNN`。超长按字符截断并以 `…` 标记。

    ⛔ 只在这里转义。存储层原样存——参考服务的"竖线归一化"是给
    "拿 Markdown 当数据库"那套形态打的补丁，在本形态下只会静默改掉用户发来的字。
    """
    escaped = ("" if text is None else str(text)).translate(_CELL_TRANSLATION)
    escaped = "".join(ch if ch.isprintable() else f"\\x{ord(ch):02x}" for ch in escaped)
    if len(escaped) > max_chars:
        escaped = escaped[: max_chars - 1] + "…"
    return escaped


def render_queue_markdown(conn: sqlite3.Connection, *, generated_at: str) -> str:
    """纯渲染：读库、返回字符串。⛔ 不碰文件系统、⛔ 不读时钟。

    `generated_at` 由调用方传入而不是在这里 `datetime.now()`——渲染读时钟会让
    "同一份数据渲染出同一份文本"不再成立，测试只能靠 monkeypatch 才写得动。
    """
    lines = [
        "# 值守任务队列（只读导出）",
        "",
        f"> 生成于 {generated_at}。本文件由 `render_queue_markdown` 从 `data/liaison.db` "
        "渲染而来，是一份**只读导出**。",
        "> ⛔ 在这里改任何东西**不会回写**数据库，下一次渲染会原样覆盖。要改状态请走 "
        "`queue.defer_task` / `queue.mark_task_pushed`。",
        "",
        _TABLE_HEADER,
        _TABLE_DIVIDER,
    ]
    for row in list_tasks(conn):
        lines.append(
            "| {id} | {status} | {sender} | {thread} | {received} | {pushed} | {msgid} | {summary} |".format(
                id=row["id"],
                # 未知取值不该出现（表上有 CHECK），但真出现了要看得见，
                # ⛔ 不要静默显示成空白——那会让一个约束破裂的行看起来正常。
                status=STATUS_LABELS.get(row["send_status"], f"⚠️ 未知({row['send_status']})"),
                sender=escape_cell(row["sender_userid"]),
                thread=escape_cell(row["thread_id"]),
                received=escape_cell(row["received_at"]),
                pushed=escape_cell(row["pushed_at"]) if row["pushed_at"] else "—",
                msgid=escape_cell(row["msgid"]),
                summary=escape_cell(row["summary"]),
            )
        )
    lines.append("")
    return "\n".join(lines)


def export_queue_markdown(
    conn: sqlite3.Connection,
    *,
    out_path: pathlib.Path | str = DEFAULT_QUEUE_VIEW_PATH,
    generated_at: str,
) -> pathlib.Path:
    """把渲染结果原子地覆盖写到 `out_path`。**本模块唯一的副作用。**

    先写同目录临时文件再 `os.replace()`：中途崩溃时读者要么看到上一版完整内容、
    要么看到新版完整内容，⛔ 不会看到半截表格——半截表格看起来就像"队列少了几条"。

    ⛔ 不用 `with open(...)`（本模块 docstring 的第三条）。
    """
    destination = pathlib.Path(out_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = render_queue_markdown(conn, generated_at=generated_at)
    staging = destination.with_name(destination.name + ".tmp")
    staging.write_text(text, encoding="utf-8")
    os.replace(staging, destination)
    return destination
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tools/liaison/tests/test_queue_view.py -v`
Expected: PASS，15 passed。

- [ ] **Step 5: 跑第 2 章事务扫描器**

Run: `pytest tools/liaison/tests/test_liaison_effects.py::test_no_second_transaction_manager_in_source -v`
Expected: PASS。变红说明 `queue_view.py` 里混进了 `with`——改成 `Path.write_text()` + `os.replace()`，⛔ 不要往扫描器白名单里加本模块。

- [ ] **Step 6: 提交**

```bash
git add tools/liaison/queue_view.py tools/liaison/tests/test_queue_view.py
git commit -m "feat(liaison): 队列 Markdown 只读导出视图——转义 + 原子覆盖 + 单向性静态断言（5.4/5.8）"
```

---

### Task 5: 从队列条目回指来源消息与全部附件

**Files:**
- Modify: `tools/liaison/queue.py`（追加 `TaskSource` 与 `load_task_source`）
- Test: `tools/liaison/tests/test_queue_source_lookup.py`

**Interfaces:**
- Consumes: `tools.liaison.attachments.StoredAttachment`（字段 `filename` / `relative_path` / `byte_length` / `sha256`）、`tools.liaison.archive.DEFAULT_ARCHIVE_ROOT`
- Produces:
  - `tools.liaison.queue.TaskSource`（frozen dataclass）
  - `tools.liaison.queue.TaskSource.attachment_paths(archive_root=DEFAULT_ARCHIVE_ROOT) -> tuple[pathlib.Path, ...]`
  - `tools.liaison.queue.load_task_source(conn, *, task_id: int) -> TaskSource | None`

- [ ] **Step 1: 写失败测试**

创建 `tools/liaison/tests/test_queue_source_lookup.py`：

```python
"""5.5 / spec「从队列条目找回材料」：条目 → 来源消息 → 全部附件。"""

import pytest

from tools.liaison.archive import InboundAttachment, archive_message
from tools.liaison.attachments import StoredAttachment
from tools.liaison.queue import enqueue_task, load_task_source
from tools.liaison.storage import db as liaison_db

RECEIVED_AT = "2026-09-09T10:00:00+08:00"


@pytest.fixture
def conn(tmp_path):
    c = liaison_db.get_connection(tmp_path / "liaison.db")
    liaison_db.init_schema(c)
    yield c
    c.close()


def _inbound(conn, archive_root, *, msgid, content, payload=None, filename=None):
    attachment = (
        InboundAttachment(filename=filename, payload=payload) if payload is not None else None
    )
    archive_message(
        conn,
        thread_id="u_zhang",
        msgid=msgid,
        sender_userid="u_zhang",
        received_at=RECEIVED_AT,
        msgtype="file" if payload is not None else "text",
        content=content,
        attachment=attachment,
        archive_root=archive_root,
    )
    enqueue_task(
        conn,
        thread_id="u_zhang",
        msgid=msgid,
        sender_userid="u_zhang",
        received_at=RECEIVED_AT,
        summary=content,
    )
    return conn.execute("SELECT id FROM liaison_task WHERE msgid = ?", (msgid,)).fetchone()[0]


def test_load_task_source_returns_the_message_and_its_attachments(conn, tmp_path):
    archive_root = tmp_path / "archive"
    payload = b"\x50\x4b\x03\x04binary-not-utf8\xff\xfe"
    task_id = _inbound(
        conn, archive_root, msgid="msg-1", content="报价单", payload=payload, filename="报价.xlsx"
    )

    source = load_task_source(conn, task_id=task_id)

    assert source is not None
    assert source.task_id == task_id
    assert source.msgid == "msg-1"
    assert source.thread_id == "u_zhang"
    assert source.sender_userid == "u_zhang"
    assert source.received_at == RECEIVED_AT
    assert source.send_status == "pending"
    assert source.pushed_at is None
    assert source.msgtype == "file"
    assert source.content == "报价单"
    assert len(source.attachments) == 1
    assert isinstance(source.attachments[0], StoredAttachment)
    assert source.attachments[0].filename == "报价.xlsx"
    assert source.attachments[0].byte_length == len(payload)


def test_the_attachment_can_actually_be_read_back_byte_for_byte(conn, tmp_path):
    """spec：据此 SHALL 能取回该条目对应的原始消息与附件。

    "能定位到"不等于"真的取得回来"——所以这条把字节读出来比一遍。
    ⛔ 全程 rb，不做任何 UTF-8 解码（design D4 的「二进制判误」生产 bug）。
    """
    archive_root = tmp_path / "archive"
    payload = b"\x89PNG\r\n\x1a\n\xff\xd8not-text"
    task_id = _inbound(
        conn, archive_root, msgid="msg-1", content="图纸", payload=payload, filename="图纸.png"
    )

    source = load_task_source(conn, task_id=task_id)
    paths = source.attachment_paths(archive_root=archive_root)

    assert len(paths) == 1
    assert paths[0].exists()
    assert paths[0].read_bytes() == payload


def test_a_message_without_attachments_yields_an_empty_tuple(conn, tmp_path):
    """⛔ 空附件返回空元组，不是 None——调用方少一个分支。"""
    task_id = _inbound(conn, tmp_path / "archive", msgid="msg-1", content="纯文本")
    source = load_task_source(conn, task_id=task_id)
    assert source.attachments == ()
    assert source.attachment_paths(archive_root=tmp_path / "archive") == ()


def test_load_task_source_returns_none_for_an_unknown_task(conn):
    assert load_task_source(conn, task_id=999) is None


def test_content_with_pipes_and_newlines_comes_back_verbatim(conn, tmp_path):
    """5.6 的存储侧：任意内容原样存、原样取回，⛔ 不许被任何"归一化"改掉。"""
    raw = "第一行|第二列\n第二行\t制表\x00空字节"
    task_id = _inbound(conn, tmp_path / "archive", msgid="msg-1", content=raw)
    assert load_task_source(conn, task_id=task_id).content == raw


def test_a_corrupt_attachments_json_does_not_crash_the_lookup(conn, tmp_path):
    """台账里的 JSON 坏了，回指要退化成"消息取得到、附件取不到"，⛔ 不是整个查询炸掉。

    材料的完整性核对是 `consistency.verify_ledger_against_archive` 的职责；
    本函数只负责取回，遇到坏 JSON 返回空附件即可。
    """
    task_id = _inbound(conn, tmp_path / "archive", msgid="msg-1", content="纯文本")
    conn.execute(
        "UPDATE liaison_message SET attachments_json = ? WHERE msgid = ?", ("{坏的", "msg-1")
    )
    source = load_task_source(conn, task_id=task_id)
    assert source is not None
    assert source.content == "纯文本"
    assert source.attachments == ()
```

⚠️ `test_a_corrupt_attachments_json_does_not_crash_the_lookup` 里那条裸 `conn.execute` 是**测试代码**，事务扫描器跳过 `tests` 目录，不会因此变红；但它不会自动提交，同连接内后续读得到即可，⛔ 不要在测试里加 `conn.commit()` 去"保险"。

- [ ] **Step 2: 跑测试确认它失败**

Run: `pytest tools/liaison/tests/test_queue_source_lookup.py -v`
Expected: FAIL —— `ImportError: cannot import name 'load_task_source' from 'tools.liaison.queue'`。

- [ ] **Step 3: 在 `queue.py` 追加实现**

```python
@dataclass(frozen=True)
class TaskSource:
    """一条队列条目连同它回指到的来源消息与全部附件。

    spec「每条队列条目可回指来源消息」的取回形态。⛔ 不做懒加载：
    调用这个函数的场景是"点开一条待办看材料"，多一次往返换来的省略毫无意义，
    而懒加载会让"取得回来"这件事在调用点之外的某个时刻才失败。
    """

    task_id: int
    msgid: str
    thread_id: str
    sender_userid: str
    received_at: str
    send_status: str
    pushed_at: str | None
    summary: str
    msgtype: str
    content: str
    attachments: tuple[StoredAttachment, ...]

    def attachment_paths(
        self, *, archive_root: pathlib.Path = DEFAULT_ARCHIVE_ROOT
    ) -> tuple[pathlib.Path, ...]:
        """把台账里的相对路径还原成可读的绝对路径。

        台账存的是**相对归档根**的路径（`attachments.StoredAttachment` 的注释）：
        归档根将来可能整体搬家，存绝对路径会让台账里所有的行一起失效。
        """
        return tuple(archive_root / item.relative_path for item in self.attachments)


def _parse_attachments(attachments_json: str) -> tuple[StoredAttachment, ...]:
    """把 `liaison_message.attachments_json` 解析成结构化附件清单。

    坏 JSON / 不是数组 / 缺字段 ⇒ 返回空元组，⛔ 不抛。取回材料和核对材料是
    两件事：核对由 `consistency.verify_ledger_against_archive` 负责报告不一致，
    在这里抛异常只会让"点开一条待办"变成一次崩溃。
    """
    try:
        entries = json.loads(attachments_json)
    except (TypeError, ValueError):
        return ()
    if not isinstance(entries, list):
        return ()
    parsed = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            parsed.append(
                StoredAttachment(
                    filename=entry["filename"],
                    relative_path=entry["relative_path"],
                    byte_length=entry["byte_length"],
                    sha256=entry["sha256"],
                )
            )
        except KeyError:
            continue
    return tuple(parsed)


def load_task_source(conn: sqlite3.Connection, *, task_id: int) -> TaskSource | None:
    """按队列条目 id 取回它的来源消息与全部附件。找不到返回 `None`。

    只读，⛔ 不写任何东西。JOIN 而不是两次查询：外键保证消息一定在，
    分两次查会给"消息中途被删"这种本不可能的状态留一个分支。
    """
    row = conn.execute(
        "SELECT t.id, t.msgid, t.thread_id, t.sender_userid, t.received_at, "
        "       t.send_status, t.pushed_at, t.summary, m.msgtype, m.content, m.attachments_json "
        "FROM liaison_task AS t "
        "JOIN liaison_message AS m ON m.msgid = t.msgid "
        "WHERE t.id = ?",
        (task_id,),
    ).fetchone()
    if row is None:
        return None
    return TaskSource(
        task_id=row[0],
        msgid=row[1],
        thread_id=row[2],
        sender_userid=row[3],
        received_at=row[4],
        send_status=row[5],
        pushed_at=row[6],
        summary=row[7],
        msgtype=row[8],
        content=row[9],
        attachments=_parse_attachments(row[10]),
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tools/liaison/tests/test_queue_source_lookup.py -v`
Expected: PASS，6 passed。

- [ ] **Step 5: 提交**

```bash
git add tools/liaison/queue.py tools/liaison/tests/test_queue_source_lookup.py
git commit -m "feat(liaison): 队列条目回指来源消息与附件（5.5）"
```

---

### Task 6: 内容鲁棒性、真并发入队、恒等不变式

本 Task 只写测试，不加生产代码。它要证明的三件事恰好是本章存在的理由：内容破不了结构、并发不丢条目、幂等记录与条目按会话恒等。

**Files:**
- Create: `tools/liaison/tests/test_queue_concurrency.py`
- Test: `tools/liaison/tests/test_queue_concurrency.py`

**Interfaces:**
- Consumes: Task 1 的 `enqueue_task`、Task 3 的 `list_tasks`、Task 4 的 `render_queue_markdown`、第 2 章的 `assert_effect_log_identity`、`tools.liaison.storage.db.get_connection`

- [ ] **Step 1: 写失败测试**

创建 `tools/liaison/tests/test_queue_concurrency.py`：

```python
"""5.6 / 5.7 / 5.10：内容鲁棒性、真并发入队、恒等不变式。

对应参考服务的两个生产 bug（06 清单 §3.4、design D11）：
- 「队列越界写入」——消息含竖线破坏表格结构；
- 「队列追加并发覆盖」——并发追加互相覆盖。

两个 bug 的根因是同一个：**拿 Markdown 表格当数据库**。本文件证明在
"真身是 SQLite 表"的形态下，这两类失效在结构上不可能发生。
"""

import multiprocessing
import pathlib
import sqlite3
import threading

import pytest

from tools.liaison.queue import enqueue_task, list_tasks
from tools.liaison.queue_view import render_queue_markdown
from tools.liaison.storage import db as liaison_db
from tools.liaison.storage.effects import effect_archive_message
from tools.liaison.tests.test_liaison_effects import assert_effect_log_identity

RECEIVED_AT = "2026-09-09T10:00:00+08:00"
GENERATED_AT = "2026-09-09T12:00:00+08:00"

#: 并发度。⛔ 不要调低到 2——2 个线程撞不出竞态窗口，通过了也说明不了什么。
CONCURRENCY = 8

#: 5.6 的恶意内容集合。每一条都对应"拿 Markdown 当数据库"时会炸的一种输入。
HOSTILE_CONTENTS = [
    ("pipes", "列一|列二|列三"),
    ("newlines", "第一行\n第二行\n第三行"),
    ("crlf", "第一行\r\n第二行"),
    ("tabs", "字段一\t字段二"),
    ("table_row", "| 999 | ✅ 已推送 | 伪造的一整行 |"),
    ("divider", "|---|---|---|"),
    ("control", "空字节\x00转义\x1b删除\x7f"),
    ("backslash_pipe", r"已经转义过的\|竖线"),
    ("very_long", "甲" * 5000),
    ("markdown_meta", "# 标题\n> 引用\n- 列表\n```代码块```"),
]


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "liaison.db"
    conn = liaison_db.get_connection(path)
    liaison_db.init_schema(conn)
    conn.close()
    return path


@pytest.fixture
def conn(db_path):
    c = liaison_db.get_connection(db_path)
    yield c
    c.close()


# ── 5.6 内容鲁棒性 ────────────────────────────────────────────────────

@pytest.mark.parametrize("label,content", HOSTILE_CONTENTS, ids=[c[0] for c in HOSTILE_CONTENTS])
def test_hostile_content_produces_exactly_one_row_with_intact_fields(conn, label, content):
    """spec：任意消息内容 MUST NOT 导致条目的字段错位或串行。"""
    effect_archive_message(
        conn,
        thread_id="u_zhang",
        business_key=f"msg-{label}",
        sender_userid="u_zhang",
        received_at=RECEIVED_AT,
        msgtype="text",
        content=content,
    )
    assert enqueue_task(
        conn,
        thread_id="u_zhang",
        msgid=f"msg-{label}",
        sender_userid="u_zhang",
        received_at=RECEIVED_AT,
        summary=content,
    ) is True

    rows = list_tasks(conn)
    assert len(rows) == 1, f"{label}：产生了 {len(rows)} 条队列条目，应当恰好 1 条"
    row = rows[0]
    # 字段没串行：每一列还是它自己
    assert row["msgid"] == f"msg-{label}"
    assert row["thread_id"] == "u_zhang"
    assert row["sender_userid"] == "u_zhang"
    assert row["received_at"] == RECEIVED_AT
    assert row["send_status"] == "pending"
    # 内容原样保存，⛔ 没有被任何"归一化"改掉
    assert row["summary"] == content
    assert_effect_log_identity(conn)


def test_hostile_content_never_adds_a_row_to_the_rendered_table(conn):
    """5.6 的渲染侧：10 条恶意内容渲染出的表格恰好 10 行数据，⛔ 一行不多。

    「队列越界写入」的症状就是这里多出行、或者别的条目被挤走。
    """
    for index, (label, content) in enumerate(HOSTILE_CONTENTS):
        effect_archive_message(
            conn,
            thread_id="u_zhang",
            business_key=f"msg-{label}",
            sender_userid="u_zhang",
            received_at=f"2026-09-09T10:{index:02d}:00+08:00",
            msgtype="text",
            content=content,
        )
        enqueue_task(
            conn,
            thread_id="u_zhang",
            msgid=f"msg-{label}",
            sender_userid="u_zhang",
            received_at=f"2026-09-09T10:{index:02d}:00+08:00",
            summary=content,
        )

    text = render_queue_markdown(conn, generated_at=GENERATED_AT)
    table_rows = [line for line in text.splitlines() if line.startswith("|")]
    assert len(table_rows) == len(HOSTILE_CONTENTS) + 2, "表头 + 分隔行 + 每条一行"

    # 每行的未转义竖线数一致 ⇒ 列数一致 ⇒ 没有字段错位
    unescaped_pipe_counts = {row.count("|") - row.count(r"\|") for row in table_rows}
    assert len(unescaped_pipe_counts) == 1, f"列数不一致：{unescaped_pipe_counts}"
    assert_effect_log_identity(conn)


# ── 5.7 真并发 ────────────────────────────────────────────────────────

def _seed_messages(db_path, count):
    """预先把父台账行造好。入队的外键指向它们。"""
    conn = liaison_db.get_connection(db_path)
    for index in range(count):
        effect_archive_message(
            conn,
            thread_id=f"u_{index}",
            business_key=f"msg-{index}",
            sender_userid=f"u_{index}",
            received_at=RECEIVED_AT,
            msgtype="text",
            content=f"内容|{index}\n第二行",
        )
    conn.close()


def test_concurrent_enqueue_from_real_threads_never_loses_a_row(db_path):
    """5.7：短时间内多条消息同时入队 ⇒ 每条各自成行，⛔ 没有任何条目被覆盖或丢失。

    🔴 **`threading.Barrier` 是"这是真并发"的机器判据。** 顺序调用冒充并发时，
    第一个调用会卡在 `barrier.wait()` 上直到超时并抛 `BrokenBarrierError`——
    只有 CONCURRENCY 个线程**同时活着**，这个 barrier 才过得去。
    ⛔ 不许把 barrier 删掉或改成 `time.sleep()`：那就退回成"看起来像并发"。
    """
    _seed_messages(db_path, CONCURRENCY)
    barrier = threading.Barrier(CONCURRENCY, timeout=10)
    results: dict[int, object] = {}
    lock = threading.Lock()

    def worker(index: int) -> None:
        # 每个线程必须开自己的连接：sqlite3 默认 check_same_thread=True，
        # ⛔ 不许把主线程的连接传进来（那会抛 ProgrammingError，
        # 而且会把这条测试变成"测了个假的"）。
        conn = liaison_db.get_connection(db_path)
        try:
            barrier.wait()  # ← 这一行就是判据本身
            outcome = enqueue_task(
                conn,
                thread_id=f"u_{index}",
                msgid=f"msg-{index}",
                sender_userid=f"u_{index}",
                received_at=RECEIVED_AT,
                summary=f"内容|{index}\n第二行",
            )
        except BaseException as exc:  # noqa: BLE001  失败要能看见，⛔ 不吞
            outcome = exc
        finally:
            conn.close()
        with lock:
            results[index] = outcome

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(CONCURRENCY)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert all(not t.is_alive() for t in threads), "有线程没退出，多半卡在写锁上"
    failures = {i: r for i, r in results.items() if isinstance(r, BaseException)}
    assert failures == {}, f"并发入队出现异常：{failures}"
    assert set(results.values()) == {True}, f"有入队被判成幂等命中：{results}"

    conn = liaison_db.get_connection(db_path)
    rows = list_tasks(conn)
    assert len(rows) == CONCURRENCY, f"并发入队丢了条目：只剩 {len(rows)} 条"
    assert sorted(row["msgid"] for row in rows) == sorted(
        f"msg-{i}" for i in range(CONCURRENCY)
    )
    # 每条内容还是自己的，⛔ 没有互相覆盖
    for row in rows:
        index = row["msgid"].removeprefix("msg-")
        assert row["summary"] == f"内容|{index}\n第二行"
    assert_effect_log_identity(conn)
    conn.close()


def test_the_barrier_actually_rejects_sequential_calls(db_path):
    """证伪：证明上面那条测试的判据真的有判别力，而不是摆设。

    顺序地在同一个线程里连着 wait 两次 ⇒ 必然 BrokenBarrierError。
    ⛔ 这条不许删——没有它，`barrier.wait()` 可能被后人改成一个永远通过的空操作
    而没人发现。
    """
    barrier = threading.Barrier(2, timeout=0.5)
    with pytest.raises(threading.BrokenBarrierError):
        barrier.wait()


def _enqueue_in_subprocess(db_path_str: str, index: int, ready, start):
    """多进程 worker。**必须是模块级函数**——spawn 启动方式要求可 pickle。"""
    conn = liaison_db.get_connection(pathlib.Path(db_path_str))
    try:
        ready.release()
        start.wait(timeout=20)
        enqueue_task(
            conn,
            thread_id=f"u_{index}",
            msgid=f"msg-{index}",
            sender_userid=f"u_{index}",
            received_at=RECEIVED_AT,
            summary=f"内容|{index}\n第二行",
        )
    finally:
        conn.close()


def test_concurrent_enqueue_across_real_processes_never_loses_a_row(db_path):
    """5.7 的跨进程版本。

    线程版共享一个 GIL；真正的部署形态里 SDK 回调与导出命令可能是两个进程，
    竞争的是**文件锁**而不是 GIL。这条把那一层也覆盖掉。
    用 `spawn` 而不是 `fork`：macOS 上 fork 一个已打开 sqlite 连接的进程是未定义行为。
    """
    _seed_messages(db_path, CONCURRENCY)
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Semaphore(0)
    start = ctx.Event()

    processes = [
        ctx.Process(target=_enqueue_in_subprocess, args=(str(db_path), i, ready, start))
        for i in range(CONCURRENCY)
    ]
    for process in processes:
        process.start()
    # 等所有子进程都到齐了再放行——这就是跨进程版的 barrier。
    for _ in range(CONCURRENCY):
        assert ready.acquire(timeout=30), "有子进程没能就绪，⛔ 不要把这条改成 sleep"
    start.set()
    for process in processes:
        process.join(timeout=60)

    assert all(p.exitcode == 0 for p in processes), [p.exitcode for p in processes]

    conn = liaison_db.get_connection(db_path)
    rows = list_tasks(conn)
    assert len(rows) == CONCURRENCY, f"跨进程并发入队丢了条目：只剩 {len(rows)} 条"
    assert_effect_log_identity(conn)
    conn.close()


# ── 5.10 恒等不变式 ───────────────────────────────────────────────────

def test_identity_holds_per_thread_after_a_mixed_batch(conn):
    """5.10：跑第 2 章的恒等脚手架，确认队列条目数与幂等记录数**按会话**恒等。

    刻意用**多个会话 + 重投 + 名单外只归档不入队**的混合批次：
    ⛔ 总数相等可以由"A 会话多一条、B 会话少一条"凑出来，那恰恰是最该被抓住的错。
    """
    plan = [("u_a", 3), ("u_b", 1), ("chat_g", 4)]
    for thread_id, count in plan:
        for index in range(count):
            msgid = f"{thread_id}-msg-{index}"
            effect_archive_message(
                conn,
                thread_id=thread_id,
                business_key=msgid,
                sender_userid=thread_id,
                received_at=RECEIVED_AT,
                msgtype="text",
                content="内容|含竖线",
            )
            # 重投两次：第二次必须是幂等命中，⛔ 不许多出条目也不许多出 effect_log 行
            enqueue_task(
                conn,
                thread_id=thread_id,
                msgid=msgid,
                sender_userid=thread_id,
                received_at=RECEIVED_AT,
                summary="内容|含竖线",
            )
            enqueue_task(
                conn,
                thread_id=thread_id,
                msgid=msgid,
                sender_userid=thread_id,
                received_at=RECEIVED_AT,
                summary="内容|含竖线",
            )

    # 名单外的一条：只归档、不入队。它会让 liaison_message 比 liaison_task 多一行，
    # 而恒等式仍必须成立——恒等式比的是每个 effect 与**它自己的**业务表。
    effect_archive_message(
        conn,
        thread_id="u_outsider",
        business_key="outsider-msg-1",
        sender_userid="u_outsider",
        received_at=RECEIVED_AT,
        msgtype="text",
        content="你好",
    )

    assert_effect_log_identity(conn)

    counts = dict(
        conn.execute("SELECT thread_id, COUNT(*) FROM liaison_task GROUP BY thread_id").fetchall()
    )
    assert counts == {"u_a": 3, "u_b": 1, "chat_g": 4}
    assert conn.execute("SELECT COUNT(*) FROM liaison_message").fetchone()[0] == 9


def test_identity_is_not_a_total_count_comparison(conn):
    """证伪：证明 `assert_effect_log_identity` 真的按会话分组比，而不是比总数。

    人为构造"总数相等但分组不等"的状态，断言它变红。
    ⛔ 这条不许删：它是"不许把恒等断言削弱成总数比较"这句禁令的机器守卫。
    """
    effect_archive_message(
        conn,
        thread_id="u_a",
        business_key="msg-1",
        sender_userid="u_a",
        received_at=RECEIVED_AT,
        msgtype="text",
        content="x",
    )
    enqueue_task(
        conn, thread_id="u_a", msgid="msg-1", sender_userid="u_a", received_at=RECEIVED_AT
    )
    # 直接改 thread_id：effect_log 说这条属于 u_a，队列行说属于 u_b。总数仍是 1 == 1。
    conn.execute("UPDATE liaison_task SET thread_id = 'u_b' WHERE msgid = 'msg-1'")

    with pytest.raises(AssertionError, match="恒等不变式破裂"):
        assert_effect_log_identity(conn)
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `pytest tools/liaison/tests/test_queue_concurrency.py -v`
Expected: 全部 FAIL（Task 1/3/4 若尚未完成则是 collection error；若已完成，本 Step 应当直接看到通过——那说明这些行为已经由前面的实现兜住了，**这是可接受的**：本 Task 是回归护栏，不是新功能的 TDD）。

⚠️ 若跨进程用例出现 `database is locked`：`get_connection` 已设 `busy_timeout=5000` 与 `journal_mode=WAL`。**⛔ 不要通过降低 `CONCURRENCY`、加 `sleep` 或关掉 WAL 来"修"它**——那是把并发正确性问题伪装成通过。正确的处置是登记「⏸ 留步：跨进程入队在 N=8 下出现写锁竞争」并把现象写进报告，⛔ 不删这条测试。

- [ ] **Step 3: 跑测试确认通过**

Run: `pytest tools/liaison/tests/test_queue_concurrency.py -v`
Expected: PASS，24 passed（10 条 parametrize + 14 条）。

- [ ] **Step 4: 跑本服务全量**

Run: `pytest tools/liaison/tests/ -v`
Expected: PASS，全绿。特别确认 `test_liaison_effects.py` 的三条守卫与 `test_liaison_testpaths_wiring.py` 都通过。

- [ ] **Step 5: 跑仓库全量，确认没影响别的泳道**

Run: `pytest -q`
Expected: PASS。若根 `tests/` 下有失败且与 `tools/liaison` 无关，登记后继续，⛔ 不要顺手去修别人的测试（并发协议：别人的改动出现在 `git status` 里是正常的）。

- [ ] **Step 6: 提交**

```bash
git add tools/liaison/tests/test_queue_concurrency.py
git commit -m "test(liaison): 内容鲁棒性、真并发入队（线程+进程）、按会话恒等（5.6/5.7/5.10）"
```

---

## 收口检查（全部 Task 完成后）

- [ ] `grep -rn "with " tools/liaison/queue.py tools/liaison/queue_view.py` 无输出（判断 3）
- [ ] `grep -n "newly_archived" tools/liaison/inbound.py` 只在礼貌回复那一处出现，⛔ 入队那一处没有（判断 5）
- [ ] `python -c "from tools.liaison.storage.effects import EFFECT_NODE_TO_TABLE as m; assert set(m) == {'effect_archive_message','effect_enqueue_task'}, m"` 通过（判断 4）
- [ ] `git diff --stat main -- tools/liaison/storage/ app/ scripts/ pyproject.toml` 为空——本章 ⛔ 不许碰这些
- [ ] `pytest tools/liaison/tests/ -q` 全绿
- [ ] 回勾 `openspec/changes/hr-wecom-aibot-liaison/tasks.md` 第 5 章 5.1–5.10，进度行相应更新（⚠️ 并行泳道会撞这一行，收口时**合并双方**而不是覆盖）
- [ ] 验收另一半：`liaison-inbound-whitelist`「名单内成员的消息进入归档与队列」全部场景通过——由 Task 2 的 `test_admitted_message_is_archived_and_enqueued` 与 `test_outsider_message_is_archived_but_never_enqueued` 覆盖

## spec 覆盖对照

| spec Requirement | 覆盖它的 Task |
|---|---|
| 队列真身为结构化存储，任意消息内容不得破坏其结构 | Task 1（存储原样存）· Task 4（渲染转义）· Task 6（10 组恶意内容 + 表格列数不变） |
| ├ Scenario: 内容含竖线 | Task 6 `test_hostile_content_produces_exactly_one_row_with_intact_fields[pipes]` |
| ├ Scenario: 内容含换行 | Task 6 `...[newlines]` / `...[crlf]` |
| └ Scenario: 并发写入不互相覆盖 | Task 6 线程版 + 跨进程版 |
| 入队幂等且与幂等记录原子提交 | Task 1 |
| ├ Scenario: 业务写失败时不留下幂等记录 | Task 1 `test_enqueue_rejects_when_the_source_message_does_not_exist` |
| └ Scenario: 幂等记录与条目数恒等 | Task 6 `test_identity_holds_per_thread_after_a_mixed_batch` |
| 每条队列条目可回指来源消息 | Task 5 |
| ├ Scenario: 从队列条目找回材料 | Task 5 `test_the_attachment_can_actually_be_read_back_byte_for_byte` |
| └ Scenario: 条目缺少来源信息不允许写入 | Task 1 `test_enqueue_rejects_a_null_msgid` |
| 队列条目的发送状态为受约束的三态 | Task 3 |
| ├ Scenario: 非法状态被拒绝 | Task 3 `test_storage_rejects_a_status_outside_the_three` |
| ├ Scenario: 已推送带时间戳 | Task 3 `test_mark_pushed_carries_the_timestamp` |
| └ Scenario: 暂缓只能来自待发 | Task 3 `test_defer_from_pushed_is_rejected` |
| 文本视图为只读导出物且单向 | Task 4 |
| ├ Scenario: 渲染含竖线的内容 | Task 4 `test_escape_cell_escapes_the_pipe` + `test_render_keeps_the_table_shape...` |
| └ Scenario: 手改导出文件不影响真身 | Task 4 `test_hand_edits_to_the_export_never_reach_the_database` |

## tasks.md 第 5 章对照

| WBS | Task |
|---|---|
| 5.1 `effect_enqueue_task` 与幂等键 | Task 1 + Task 2（接线） |
| 5.2 拒绝缺来源 `msgid` | Task 1 |
| 5.3 三态 + 转移校验 | Task 3 |
| 5.4 `render_queue_markdown` 单向只读 | Task 4 |
| 5.5 条目回指材料 | Task 5 |
| 5.6 内容不错位单测 | Task 6 |
| 5.7 并发入队单测 | Task 6 |
| 5.8 手改导出不影响真身 | Task 4 |
| 5.9 非法状态/转移/时间戳 | Task 3 |
| 5.10 恒等脚手架 | Task 6（各 Task 的用例末尾也各调一次） |
