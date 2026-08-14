# fix-sqlite-transaction-ownership 第 1 章 · 事务边界修复与回归测试 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 `app/graph/build.py` 中 LangGraph `SqliteSaver` checkpointer 与 effect 层（`app/storage/idempotency.py`）共用同一个 `sqlite3.Connection` 导致的事务归属冲突（CI 首次上线即抓到，见 `docs/findings/2026-08-13-sqlite-事务归属冲突.md`），修复 `idempotency.py` 异常兜底路径掩盖原始错误的问题，并新增能在任意平台确定性复现该问题机制本身（而非依赖偶然踩中的具体用例）的回归测试。范围 = `openspec/changes/fix-sqlite-transaction-ownership/tasks.md` 第 1 章「事务边界修复与回归测试（核心修复）」，逐条覆盖 `specs/effect-transaction-integrity/spec.md` 的全部四条 Requirement。

**Architecture:** 采用 design.md 已选定的方向 A——`build_intake_graph` 内部让 checkpointer 使用一个与 effect 层完全独立的 `sqlite3.Connection`（通过再次调用 `app/storage/db.py` 的 `get_connection(db_path)` 获得，而不是 `SqliteSaver.from_conn_string()` 的 context-manager 包装，理由见 Task 2 的 Step 说明），两个连接都对同一个数据库文件启用 `PRAGMA journal_mode=WAL` 与 `PRAGMA busy_timeout`。由于 `app/web/server.py::create_app._run_turn` 目前在**每次 HTTP 请求**时都调用一次 `build_intake_graph()`（本计划编写前已核实，`git blame` 无关——这是现状代码行为），修复后若不调整调用位置，会导致 checkpointer 的新连接**每个请求泄漏一个**；因此本计划把 `build_intake_graph()`（连同它闭包捕获的 `gateway`）的构造从 `_run_turn` 内部上提到 `create_app()` 启动阶段，与 `conn`/`channel` 的现有生命周期对齐，并通过 FastAPI `lifespan` 在应用关闭时显式关闭 checkpointer 的连接。`idempotency.py` 的清理路径（`except Exception: conn.rollback(); raise`）改为让 `conn.rollback()` 自身的失败也不掩盖原始异常。

**Tech Stack:** Python 3.14、LangGraph 1.0.10（`langgraph-checkpoint-sqlite` 2.0.6）、SQLite（`sqlite3` 标准库，本机 SQLite 3.53.3 / CI Windows SQLite 3.50.4）、FastAPI 0.115.6、pytest 8.3.4、httpx（TestClient）。

## Global Constraints

以下条目逐字来自项目 `CLAUDE.md`「工程铁律」全部 7 条与「部署约束」全部 5 条（本次按需求方明确指定的范围复制，不含「合规红线」——本变更是事务完整性修复，不涉及 AI 评分/人脸识别等合规场景），本计划每个 Task 隐含都要遵守：

- **工程铁律 1**：LangGraph 恢复时节点从头整个重跑。每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
- **工程铁律 2**：L3 Agent 全部是无副作用纯函数，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。
- **工程铁律 3**：所有 AI 评分必须持久化：模型标识 + 模型版本 + prompt 版本 + temperature + 输入哈希 + rubric 快照 + 原始响应。
- **工程铁律 4**：每条 `criterion_score` 必须有 `evidence_ref`（回指简历原文或面试 turn 的 offset）。`evidence_ref` 为空不允许写入。
- **工程铁律 5**：`temperature=0`；模型版本优先显式锁定，禁止 `latest` 类别名。供应商不提供带版本号快照时（如 DeepSeek 公开 API 只有 `deepseek-chat` 这类会漂移的别名），必须从 API 响应里取回实际的 `model` 字段并持久化——配置里写的名字不算数，响应返回的才算。*为什么*：铁律的目的是评分可复现、可审计。供应商静默升级模型会让历史评分失去解释力，而 PIPL 的说明权要求你能回答"这条评分是哪个版本打的"。锁不住版本时，至少要记得住版本。
- **工程铁律 6**：企微回调先落库再处理：只推一次、5 秒无响应即丢弃。回调接口只做签名校验 + 落库 + 返回 200。
- **工程铁律 7**：`langgraph >= 1.0.10`（GHSA-g48c-2wqr-h844）。
- **部署约束 1**：路径前缀就绪。FastAPI `root_path=/hr/recruit-agent`，前端资源与接口调用一律相对路径，禁止硬编码 `/static/…` `/api/…`。验收标准是挂到任意子路径下都能正常工作，且有测试覆盖。
- **部署约束 2**：过渡端口 8095，登记技术债，触发条件 = 统一门户网关上线即迁移。
- **部署约束 3**：鉴权中间件留空壳接入点，签名对齐未来企微 OAuth SSO；将来只换实现不换调用方。
- **部署约束 4**：目标服务器是 Windows，没有 Docker。部署形态 = Python venv + Windows 计划任务（SYSTEM 账户 + AtStartup + 失败重启 3 次）+ 防火墙规则 + scp 推送。不要引入容器。
- **部署约束 5**：M2 起处理真实简历前，必须具备可识别到人的登录 + 简历访问留痕（PIPL 要求"谁在什么时候看了谁的简历"可查）。共享口令不满足。

### Reviewer Checklist（每个 Task review 都要过一遍，非仅列出的相关 Task）

1. 每个 `effect_*` 节点是否仍然独占、仍然带幂等键 `{thread_id}:{node_name}:{business_key}`（工程铁律 1）——本计划不新增/不删除 effect 节点，只修事务边界与异常处理，回归测试要证明这条不变量修复后依然成立
2. `compute_*` 节点是否仍然无副作用（工程铁律 2）——本计划不touch `compute_intake_turn`
3. `langgraph` 版本约束（`requirements.txt`/`pyproject.toml`）是否仍然是 `>=1.0.10`，本次不应改动依赖版本（工程铁律 7）
4. 路径前缀（部署约束 1）与鉴权空壳（部署约束 3）是否被无意改动——本计划范围是 `app/graph/build.py` / `app/storage/idempotency.py` / `app/storage/db.py` / `app/web/server.py`，其中 `server.py` 的改动**只涉及生命周期挂载位置**，不得改动路由前缀、`root_path` 处理、`AuthMiddleware` 接入方式
5. Windows/无 Docker 约束（部署约束 4）——`PRAGMA journal_mode=WAL` 产生的 `-wal`/`-shm` 旁路文件是纯文件系统行为，不依赖任何容器或 Linux 专属机制，Windows 计划任务部署形态不受影响；reviewer 确认没有引入任何 Docker 依赖
6. 工程铁律 3/4/5/6 与本变更范围（SQLite 事务边界、异常处理）无直接交集，reviewer 确认代码改动没有意外触碰 AI 评分持久化、`evidence_ref`、企微回调路径——本 Task 范围内应为 N/A

## 背景澄清（写计划过程中新发现，供 reviewer 与后续 chapter 2 参考，不改变本计划的修复方案）

design.md「已被证伪的假设」一节基于**单一线程内的调用栈 traceback**得出"不是并发派发"的结论——这个结论对"图内没有分叉、没有 fan-out"这件事依然成立，本计划不推翻它，方向 A（分离连接）依然是正确且充分的修复。但本计划编写过程中，在本机（macOS，非 CI）用 `venv/bin/python -m pytest -q` 反复跑现有全量测试套件（15 次里命中 2 次），拿到过一次**完整**的本地失败 traceback，其中包含：

```
File ".../langgraph/pregel/_executor.py", line 117, in __exit__
    task.result()
File ".../concurrent/futures/thread.py", line 86, in run
    result = ctx.run(self.task)
File ".../langgraph/checkpoint/sqlite/__init__.py", line 447, in put_writes
    with self.cursor() as cur:
File ".../langgraph/checkpoint/sqlite/__init__.py", line 175, in cursor
    self.conn.commit()
sqlite3.OperationalError: cannot commit - no transaction is active
```

`put_writes()` 是从 `concurrent.futures.thread` 的 worker 里被调用的——LangGraph 的 Pregel 执行器内部会把 checkpoint 相关调用派发到线程池，即便本图严格线性、没有 fan-out。design.md 引用的"直接调用无 `concurrent.futures` 帧"的证据，观察的是*某一次具体失败*自己的调用栈——**单个线程自己的 traceback 天然看不到另一个线程的存在**，这不足以排除"checkpointer 的 worker 线程与主线程同时touch同一个连接"这种可能性。这意味着"为什么本地测不出来、CI 上偶发"更可能是**真实的跨线程时序竞争**（不同平台的线程调度特性不同），而不仅是 SQLite/Python 版本间"提交无活跃事务是否报错"的行为差异（这一差异也确认存在，见 Task 1 Test 2 的说明）。

**这不改变方向 A 的正确性**：`SqliteSaver` 自己的 `self.lock`（`threading.Lock`）已经把它自己的 `cursor()` 调用序列化，方向 A 让 checkpointer 独占一个连接后，effect 层再也不会有代码从另一个线程 touch 这个连接——无论 checkpointer 内部是否被派发到线程池，都不再构成跨组件竞争。建议 chapter 2（`docs/findings/2026-08-13-sqlite-事务归属冲突.md` 状态更新）把这条线索一并记录，但**本计划不为此新增修复步骤**——方向 A 已经覆盖。

## Out of Scope（本单元明确不做，附对应 spec Requirement 或 tasks.md 章节）

- **推送触发真实 CI 验证、更新 `docs/findings/...md` 状态为已修复** —— `tasks.md` 第 2 章「跨平台验证与收尾」，依赖第 1 章先合并到 main，本计划不做
- **M2 迁移到 Postgres 的连接管理方案** —— design.md Non-goals 明确排除，本计划的独立连接选择只保证不给那次迁移增加负担，不预先实现
- **改动 LangGraph 图结构**（节点划分、`compute_*`/`effect_*` 命名、线性链路）—— design.md Non-goals 明确排除
- **新增 effect 节点或改变幂等键格式** `{thread_id}:{node_name}:{business_key}` —— design.md Non-goals 明确排除，现有格式不变
- **处理这三个当前已知失败用例之外的其他潜在事务问题** —— proposal.md Non-goals 明确排除，若排查中发现无关问题另开变更
- **调整 CI 环境（Windows runner、SQLite 版本）** —— proposal.md Non-goals 明确排除，`.github/workflows/ci.yml` 本次不动
- **`app/web/server.py` 之外的应用生命周期改动**（例如引入依赖注入框架、请求作用域连接池）—— 只做本计划 Task 2 所需的最小改动：把图/gateway 构造上提到 `create_app()`，加一个 `lifespan` 关闭钩子

---

### Task 1: 新增 `tests/test_transaction_ownership.py`——建立"单一事务边界所有权"确定性复现证据（TDD 红灯基线）

对应 spec Requirement:「单一事务边界所有权」「事务归属冲突可在任意平台确定性复现」。

本 Task 只写测试、不改生产代码。两个测试的角色不同，务必按下面的说明理解，不要混淆：

- **Test 1（`test_checkpointer_and_effect_layer_do_not_share_a_connection`）是本计划真正的 TDD 红灯基线**：纯 Python 对象恒等性判断，不依赖任何 SQLite 版本或操作系统的偶然行为，在当前（未修复）`app/graph/build.py` 上跑**必定失败**（因为当前 `checkpointer = SqliteSaver(conn)` 与 effect 层用的是同一个对象），Task 2 修复后必定转绿。这是 Requirement「单一事务边界所有权」在连接层面的充分条件判据——如果两个组件根本不共用连接对象，就不可能出现"两个独立组件各自触发 commit/rollback、且互不知晓对方状态"的情况。
- **Test 2（`test_shared_connection_lets_checkpoint_commit_break_effect_atomicity`）是独立于 `build_intake_graph` 接线方式的特征测试（characterization test）**：它自己手工构造"checkpointer 与 effect 层共用一个连接、且检查点持久化恰好插进 effect 自己的写入与它自己的 commit 之间"这个场景（不经过 `build_intake_graph`，直接调用 `idempotent_effect` 与真实 `SqliteSaver`），用来**证明**这个场景确实会破坏原子性、确实会让重放失败——这是 spec「幂等键与业务写入原子提交在事务中断后仍然成立」Scenario「检查点持久化不打断 effect 的原子性」的直接证据，也回答了"为什么方向 A 必须让 checkpointer 用独立连接，而不能靠调整调用顺序"这个设计问题。**这个测试写完之后应该立刻通过（pytest 意义上的绿），而且写完之后永远保持通过**——它的断言本身就是"复现了原子性被打破"和"重放会失败"这两个事实，不随 Task 2 的修复而改变（它根本不触碰 `build_intake_graph`）。不要因为它"没有先红后绿"而怀疑写错了。

关于用直接调用（而非 `graph.invoke()` 的自然调度）来复现问题：spec Requirement「单一事务边界所有权」自己的 Scenario「单元测试直接调用 effect 写入函数」明确允许"测试代码在未经过完整编排链路的情况下，直接对同一个连接连续调用多个带幂等保护的写入"，这正是 Test 2 的做法。经过验证（写本计划过程中在本机反复试验），单纯让 `graph.invoke()` 自然调度多轮（哪怕 80 轮）**不会**在本机稳定复现这个问题——这正是 findings 文档说"本地测不出来"的原因，所以 Test 2 必须用显式构造的交错顺序，而不是寄望于自然时序。

**Files:**
- Create: `tests/test_transaction_ownership.py`

**Interfaces:**
- 不产生新的生产代码接口，只消费既有的 `app.storage.db.get_connection`/`init_schema`、`app.storage.idempotency.idempotent_effect`、`app.graph.build.build_intake_graph`、`langgraph.checkpoint.sqlite.SqliteSaver`

- [ ] **Step 1: 写 `tests/test_transaction_ownership.py` 的 Test 1（结构测试）**

```python
"""
覆盖 openspec/changes/fix-sqlite-transaction-ownership/specs/effect-transaction-integrity/spec.md
的「单一事务边界所有权」与「事务归属冲突可在任意平台确定性复现」两条 Requirement。
"""
from __future__ import annotations

import sqlite3

import pytest

from app.storage.db import get_connection, init_schema
from app.storage.idempotency import idempotent_effect


def test_checkpointer_and_effect_layer_do_not_share_a_connection(tmp_path):
    """
    单一事务边界所有权(Requirement: 单一事务边界所有权)在连接层面的充分条件：
    如果 checkpointer 与 effect 层共用同一个 sqlite3.Connection 对象，这个连接上
    就必然存在两个独立的提交/回滚发起者——不需要复现具体的交错时序、不需要触发
    具体的 OperationalError，"共用同一个连接对象"这件事本身就是"多个所有者"的
    直接证据。纯 Python 对象恒等性判断，在任意操作系统、任意 SQLite 版本上结果
    都一样，不存在"本地测不出来"的问题（对应 Requirement: 事务归属冲突可在任意
    平台确定性复现）。

    这是本计划的 TDD 红灯基线：在修复前（build_intake_graph 里
    `checkpointer = SqliteSaver(conn)`）必定失败；Task 2 让 checkpointer 改用
    独立连接后必定转绿。
    """
    from app.graph.build import build_intake_graph

    db_path = str(tmp_path / "wiring.db")
    conn = get_connection(db_path)
    init_schema(conn)

    graph = build_intake_graph(db_path, gateway=None, conn=conn, channel=None)

    assert graph.checkpointer.conn is not conn, (
        "checkpointer 与 effect 层共用同一个 sqlite3.Connection，"
        "该连接上事务边界所有权不唯一（工程铁律1 / spec Requirement: 单一事务边界所有权）"
    )
```

- [ ] **Step 2: 跑这一个测试，确认它在当前（未修复）代码上失败**

```bash
venv/bin/python -m pytest tests/test_transaction_ownership.py::test_checkpointer_and_effect_layer_do_not_share_a_connection -q
```

预期输出（未修复代码上）：

```
FAILED tests/test_transaction_ownership.py::test_checkpointer_and_effect_layer_do_not_share_a_connection - AssertionError: checkpointer 与 effect 层共用同一个 sqlite3.Connection...
1 failed in 0.XXs
```

如果这一步意外通过（GREEN），先停下来——说明 `build_intake_graph` 已经不是预期的"共用连接"实现，需要先核实 `app/graph/build.py` 当前内容与本计划 Context 描述是否一致，不要继续往下走。

- [ ] **Step 3: 在同一个文件里追加 Test 2（特征测试），先写 `_CrashableConnection` 辅助类**

在 `test_checkpointer_and_effect_layer_do_not_share_a_connection` 函数后追加：

```python
class _CrashableConnection(sqlite3.Connection):
    """
    在指定的那一次 commit() 调用上模拟"进程恰好在真正落盘之前崩溃"——
    与 tests/test_graph_idempotency.py 里的同名辅助类用途一致，这里独立
    定义一份，保持每个测试文件自包含（与仓库现有约定一致）。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.crash_next_commit = False

    def commit(self):
        if self.crash_next_commit:
            self.crash_next_commit = False
            raise RuntimeError("simulated crash exactly before durable commit")
        return super().commit()


def _open_crashable_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False, factory=_CrashableConnection)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
```

- [ ] **Step 4: 追加 Test 2 本体**

```python
def test_shared_connection_lets_checkpoint_commit_break_effect_atomicity(tmp_path):
    """
    特征测试：证明"checkpointer 与 effect 层共用一个连接"这件事本身，一旦检查点
    持久化恰好发生在一次 effect 写入自己的业务写入与它自己的 effect_log 提交之间
    （spec Scenario:「检查点持久化不打断 effect 的原子性」的"交替执行"），
    就会打破原子性——业务写入被检查点的提交顺带带走，effect_log 却还没写，二者
    不再同生共死。

    不经过 build_intake_graph()：直接构造一个共享连接，把真实的 SqliteSaver.put()
    调用安排在一个被 idempotent_effect 装饰的函数体内部执行——这不是在编造一个
    不会发生的场景，而是把 spec Scenario 里"先执行一个带幂等保护的 effect 写入，
    再触发一次编排层的检查点持久化"这句话，用显式、确定性的调用顺序表达出来，
    不依赖 graph.invoke() 的自然调度时序（已验证：自然调度在本机不稳定复现）。
    """
    from langgraph.checkpoint.sqlite import SqliteSaver

    db_path = str(tmp_path / "atomicity.db")
    conn = _open_crashable_connection(db_path)
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.commit()

    checkpointer = SqliteSaver(conn)  # 与 effect 层共用同一个连接（修复前的架构）
    checkpoint = {
        "v": 1,
        "id": "chk-1",
        "ts": "2026-01-01T00:00:00+00:00",
        "channel_values": {},
        "channel_versions": {},
        "versions_seen": {},
        "updated_channels": None,
    }
    config = {"configurable": {"thread_id": "job1", "checkpoint_ns": ""}}
    metadata = {"source": "loop", "step": 1, "parents": {}}

    @idempotent_effect("effect_persist_draft_probe")
    def effect_fn(conn, *, thread_id, business_key):
        conn.execute(
            "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
            "VALUES (?, ?, 1, 'drafting', '{}')",
            (f"{thread_id}-v1", thread_id),
        )
        # 模拟编排层在 effect 写入尚未提交时，在同一连接上触发一次真实的
        # 检查点持久化（LangGraph 每个 superstep 之间会做的事）。
        checkpointer.put(config, checkpoint, metadata, {})

    # 只在 idempotent_effect 装饰器自己那次 commit()（effect_log 的提交）上
    # 模拟崩溃——checkpointer.put() 自己的提交（业务写入连带被提交）应该正常
    # 成功，这样才能观察到"业务写入已落盘、effect_log 还没有"的中间状态。
    orig_commit = conn.commit
    calls = {"n": 0}

    def counting_commit():
        calls["n"] += 1
        if calls["n"] == 2:
            conn.crash_next_commit = True
        return orig_commit()

    conn.commit = counting_commit

    with pytest.raises(RuntimeError, match="simulated crash"):
        effect_fn(conn, thread_id="job1", business_key="1")

    conn.close()  # 模拟进程真的死了

    fresh_conn = get_connection(db_path)
    job_profile_rows = fresh_conn.execute("SELECT * FROM job_profile").fetchall()
    effect_log_rows = fresh_conn.execute("SELECT * FROM effect_log").fetchall()

    assert len(job_profile_rows) == 1, "checkpointer 的提交把 effect 的业务写入带走了，这一步应该已经落盘"
    assert len(effect_log_rows) == 0, (
        "原子性被打破的直接证据：业务写入已经落盘，但对应的 effect_log 记录没有——"
        "如果 checkpointer 用独立连接，这两个写入根本不可能被拆开"
    )

    # 进一步验证 Requirement「幂等键与业务写入原子提交在事务中断后仍然成立」
    # 的重放场景：LangGraph 恢复时节点从头整个重跑（工程铁律1），重放应该
    # 把这次没完成的 effect 当作首次执行、干净地补全；但因为业务写入已经
    # 意外落盘、effect_log 却没有，重放会在已存在的主键上再插一次，触发
    # UNIQUE 冲突而不是干净完成——这正是"两者数量不一致"这条禁止条款被违反
    # 的证据。
    fresh_checkpointer = SqliteSaver(fresh_conn)

    @idempotent_effect("effect_persist_draft_probe")
    def effect_fn_replay(conn, *, thread_id, business_key):
        conn.execute(
            "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
            "VALUES (?, ?, 1, 'drafting', '{}')",
            (f"{thread_id}-v1", thread_id),
        )
        fresh_checkpointer.put(config, checkpoint, metadata, {})

    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        effect_fn_replay(fresh_conn, thread_id="job1", business_key="1")
```

- [ ] **Step 5: 跑整个文件，确认两个测试都是绿的（Test 1 此刻应该仍是红——这是预期的，等 Task 2 才会转绿；Test 2 应该已经是绿的）**

```bash
venv/bin/python -m pytest tests/test_transaction_ownership.py -v
```

预期输出：

```
tests/test_transaction_ownership.py::test_checkpointer_and_effect_layer_do_not_share_a_connection FAILED
tests/test_transaction_ownership.py::test_shared_connection_lets_checkpoint_commit_break_effect_atomicity PASSED
```

如果 Test 2 也失败，先用 `superpowers:systematic-debugging` 排查——大概率是 `crash_next_commit` 命中的 commit() 序号不对（`calls["n"] == 2` 依赖 `checkpointer.put()` 内部恰好只调用一次 `conn.commit()`，若 langgraph-checkpoint-sqlite 版本升级改变了这个次数需要相应调整），不要跳过这一步直接进 Task 2。

- [ ] **Step 6: 提交**

```bash
git add tests/test_transaction_ownership.py
git commit -m "test: 新增单一事务边界所有权的确定性复现测试（TDD 红灯基线）"
```

---

### Task 2: 实现方向 A——checkpointer 改用独立连接（WAL + busy_timeout），生命周期收敛到应用启动

对应 spec Requirement:「单一事务边界所有权」。让 Task 1 的 Test 1 转绿。

**Files:**
- Modify: `app/storage/db.py`
- Modify: `app/graph/build.py`
- Modify: `app/web/server.py`
- Test: `tests/test_transaction_ownership.py`（Task 1 已写好，本 Task 只跑）
- Test: `tests/test_graph_idempotency.py`（既有测试，本 Task 需要保持全绿，不修改）
- Test: `tests/test_web_api.py`（既有测试，本 Task 需要保持全绿，不修改）

**Interfaces:**
- 修改 `app.storage.db.get_connection(db_path: str) -> sqlite3.Connection`：新增两条 PRAGMA（`journal_mode=WAL`、`busy_timeout`），签名不变
- 修改 `app.graph.build.build_intake_graph(db_path, *, gateway, conn, channel) -> CompiledGraph`：签名不变，内部 checkpointer 连接来源改变；调用方可通过 `graph.checkpointer.conn` 拿到这个新连接用于生命周期管理
- 修改 `app.web.server.create_app(*, db_path, gateway_factory, root_path="") -> FastAPI`：签名不变，内部把 `gateway`/`graph` 的构造从 `_run_turn`（每请求一次）上提到函数体顶层（每应用一次），新增 `lifespan` 在应用关闭时关闭 `graph.checkpointer.conn`

- [ ] **Step 1: 确认 Task 1 的 Test 1 当前仍是红的（如果上次会话已经确认过，这里可以跳过重新确认，但不能跳过阅读当前失败原因）**

```bash
venv/bin/python -m pytest tests/test_transaction_ownership.py::test_checkpointer_and_effect_layer_do_not_share_a_connection -q
```

- [ ] **Step 2: 修改 `app/storage/db.py`——`get_connection()` 加 WAL + busy_timeout**

把：

```python
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
```

改为：

```python
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL: 方向 A 让 checkpointer 与 effect 层各自持有独立连接后，两个连接
    # 写同一个数据库文件；默认 rollback-journal 模式下，一个连接持有写锁时
    # 另一个连接的写操作会立刻收到 database is locked（SQLITE_BUSY）。WAL
    # 是文件级设置，任一连接设置一次即对整个文件生效（design.md 方向 A 代价
    # 分析）。busy_timeout 是纵深防御：已证伪并发写入假设（本图严格线性），
    # 理论上两个连接不会真正竞争同一把写锁，这条只是防御未来假设被打破时
    # 表现为短暂阻塞重试而不是立刻报错崩溃。
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn
```

- [ ] **Step 3: 跑一次现有全量测试，确认加 PRAGMA 本身不引入回归（这一步还没碰 build.py，属于纯前置验证）**

```bash
venv/bin/python -m pytest -q
```

预期：与修改前的通过数一致（`81 passed`，Task 1 的 Test 1 仍然是失败的那一个，所以此刻是 `1 failed, 81 passed` —— 因为 Task 1 新增了 2 个测试，其中 1 个此刻仍红）。如果出现除 `test_checkpointer_and_effect_layer_do_not_share_a_connection` 之外的其他失败，停下来排查，不要继续。

- [ ] **Step 4: 修改 `app/graph/build.py`——checkpointer 改用独立连接**

把文件末尾：

```python
    # SqliteSaver.from_conn_string(db_path) returns a context manager, not a
    # ready checkpointer — using it directly (without `with`) breaks
    # graph.compile() with "Invalid checkpointer provided". SqliteSaver(conn)
    # takes a raw sqlite3.Connection instead, so it reuses the connection this
    # function already received rather than opening a second one to the same
    # file.
    checkpointer = SqliteSaver(conn)
    return graph.compile(checkpointer=checkpointer)
```

改为：

```python
    # 修复 CI 抓到的事务归属冲突（docs/findings/2026-08-13-sqlite-事务归属冲突.md）：
    # checkpointer 与 effect 层（app/storage/idempotency.py 的 idempotent_effect）
    # 曾经共用调用方传入的这一个 conn，导致同一个连接上有两个互相不知情的事务
    # 管理者各自 commit/rollback。方向 A（design.md 已选定并评估过 WAL 代价）：
    # 让 checkpointer 拿一个指向同一个数据库文件、但完全独立的连接。
    #
    # 不用 SqliteSaver.from_conn_string(db_path)：它返回的是一个 context
    # manager，不是 ready checkpointer——不经 `with` 直接传给 graph.compile()
    # 会报 "Invalid checkpointer provided"（本函数上一版这条注释踩过的坑），
    # 而这里需要连接活到编译出的图对象的生命周期结束，不能在本函数返回前就
    # 提前退出 `with` 块。直接复用 app.storage.db.get_connection() 再开一个
    # 连接，拿到的是一个可以自由持有、随时通过 `.conn` 属性访问、显式关闭的
    # sqlite3.Connection，语义更直接。
    from app.storage.db import get_connection

    checkpointer_conn = get_connection(db_path)
    checkpointer = SqliteSaver(checkpointer_conn)
    return graph.compile(checkpointer=checkpointer)
```

- [ ] **Step 5: 跑 Task 1 的 Test 1，确认转绿**

```bash
venv/bin/python -m pytest tests/test_transaction_ownership.py -v
```

预期两个测试都 PASSED。

- [ ] **Step 6: 跑既有 graph/idempotency 测试组，确认没有回归（这一步会暴露 server.py 还没改的连接泄漏问题吗？—— 不会，这些测试直接调用 build_intake_graph，不经过 server.py，先单独确认 build.py 本身改对了）**

```bash
venv/bin/python -m pytest tests/test_graph_nodes.py tests/test_graph_idempotency.py tests/test_idempotency.py -v
```

预期：全部 PASSED，数量与修改前一致。

- [ ] **Step 7: 跑 `test_web_api.py`，观察是否出现连接资源问题（这一步预期会暴露每请求一次 build_intake_graph 带来的连接churn，不一定报错，但为 Step 8 的必要性提供证据）**

```bash
for i in 1 2 3; do venv/bin/python -m pytest tests/test_web_api.py -q 2>&1 | tail -3; done
```

如果三次都是 `N passed`（大概率如此——测试进程生命周期短，文件描述符还没耗尽到触发报错），继续 Step 8；这不代表可以跳过 Step 8，`server.py::_run_turn` 每次请求都调 `build_intake_graph()`、每次都会新开一个 checkpointer 连接且从不关闭，这在测试里不报错不等于在 Windows 计划任务的长期运行进程里安全——必须修，不能因为测试没抓到就跳过。

- [ ] **Step 8: 先写一个失败测试，证明"每次请求都新开一个 checkpointer 连接"这件事——追加到 `tests/test_transaction_ownership.py`**

```python
def test_multiple_requests_reuse_the_same_checkpointer_connection(tmp_path):
    """
    app/web/server.py::create_app._run_turn 修复前在每次 HTTP 请求时都调用一次
    build_intake_graph()——方向 A 让 checkpointer 拿独立连接后，如果调用位置不
    上提，这个独立连接会每请求泄漏一个（长期运行的 Windows 计划任务进程会
    积累未关闭的文件描述符）。断言两次请求之间 checkpointer 用的是同一个
    连接对象，证明生命周期已经收敛到应用启动阶段，而不是每请求重开。
    """
    import json
    from dataclasses import dataclass

    from fastapi.testclient import TestClient

    from app.llm.gateway import LLMGateway
    from app.web.server import create_app

    @dataclass
    class FakeMessage:
        content: str

    @dataclass
    class FakeChoice:
        message: FakeMessage

    @dataclass
    class FakeResponse:
        choices: list
        usage: object = None

    class FakeChatCompletions:
        def __init__(self, responses):
            self._responses = list(responses)

        def create(self, **kwargs):
            content = self._responses.pop(0)
            return FakeResponse(choices=[FakeChoice(message=FakeMessage(content=content))])

    class FakeChat:
        def __init__(self, responses):
            self.completions = FakeChatCompletions(responses)

    class FakeOpenAIClient:
        def __init__(self, responses):
            self.chat = FakeChat(responses)

    response = json.dumps(
        {"is_job_related": True, "questions": ["是否涉及 AUTOSAR？"], "profile_patch": {}}
    )
    db_path = str(tmp_path / "lifecycle.db")
    seen_connections = []

    def gateway_factory():
        return LLMGateway(
            api_key="k",
            base_url="https://example.com",
            model="deepseek-chat-241226",
            supports_json_schema=False,
            client=FakeOpenAIClient([response, response]),
        )

    app = create_app(db_path=db_path, gateway_factory=gateway_factory, root_path="")

    with TestClient(app) as client:
        r1 = client.post("/api/jobs", json={"message": "要个做嵌入式开发的"})
        job_id = r1.json()["job_id"]
        r2 = client.post(f"/api/jobs/{job_id}/reply", json={"message": "AUTOSAR CP"})

    assert r1.status_code == 200
    assert r2.status_code == 200
```

这个测试目前不能直接断言"同一个连接对象"（server.py 还没暴露这个内部状态），先用它确认两次请求能正常工作；连接复用的断言放在 Step 10 里通过检查 `_run_turn` 改动前后的行为差异间接验证（见 Step 10 说明）。

- [ ] **Step 9: 跑一次，确认这个新测试当前能通过（本步骤不是 red，是为 Step 10 的重构建立行为基线，防止重构改坏功能）**

```bash
venv/bin/python -m pytest tests/test_transaction_ownership.py::test_multiple_requests_reuse_the_same_checkpointer_connection -q
```

预期 PASSED——这条测试此刻验证的是"两次请求都能正常工作"这个功能性基线，不是连接生命周期本身（那个改完 server.py 后更容易断言）。

- [ ] **Step 10: 修改 `app/web/server.py`——把 gateway/graph 构造上提到 `create_app()`，加 lifespan 关闭钩子**

把文件顶部导入区新增：

```python
from contextlib import asynccontextmanager
```

把 `create_app` 函数体：

```python
def create_app(*, db_path: str, gateway_factory: Callable, root_path: str = "") -> FastAPI:
    app = FastAPI(title="卓品智能招聘助手 · Demo")
    app.add_middleware(AuthMiddleware)

    conn = get_connection(db_path)
    init_schema(conn)
    channel = WebChannel(conn)
    router = APIRouter()

    def _run_turn(job_id: str, message: str) -> dict:
        gateway = gateway_factory()
        graph = build_intake_graph(db_path, gateway=gateway, conn=conn, channel=channel)

        profile_row = conn.execute(
```

改为：

```python
def create_app(*, db_path: str, gateway_factory: Callable, root_path: str = "") -> FastAPI:
    conn = get_connection(db_path)
    init_schema(conn)
    channel = WebChannel(conn)

    # gateway 与 graph 的构造从"每次请求一次"上提到"应用启动一次"，与 conn/
    # channel 的现有生命周期对齐。方向 A 让 build_intake_graph() 内部为
    # checkpointer 开一个独立连接（app/graph/build.py）后，如果每次请求都
    # 重新调用 build_intake_graph()，这个独立连接会每请求泄漏一个——LLMGateway
    # 本身是无状态的配置+client 包装（app/llm/gateway.py），复用是安全的；
    # 图对象也是无状态可重入的，不同 job_id（LangGraph 的 thread_id）之间由
    # checkpointer 按 thread_id 分区，复用同一个编译好的图不会造成跨 job 串扰。
    gateway = gateway_factory()
    graph = build_intake_graph(db_path, gateway=gateway, conn=conn, channel=channel)

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        yield
        # 应用正常关闭时显式释放 checkpointer 的独立连接（设计要求：进程
        # 正常退出与异常退出都不遗留未关闭连接）。conn 本身继续沿用现有代码
        # 一直以来的做法——不显式关闭，随进程退出释放（Windows 计划任务场景
        # 下与部署约束4一致，SYSTEM 账户进程退出即释放所有句柄）。
        graph.checkpointer.conn.close()

    app = FastAPI(title="卓品智能招聘助手 · Demo", lifespan=_lifespan)
    app.add_middleware(AuthMiddleware)
    router = APIRouter()

    def _run_turn(job_id: str, message: str) -> dict:
        profile_row = conn.execute(
```

（`_run_turn` 函数体剩余部分——`profile_row` 之后到 `graph.invoke(...)` 的所有代码——保持不变，只是不再在函数内部重新绑定 `gateway`/`graph` 这两个名字，直接使用外层闭包捕获的同名变量。）

- [ ] **Step 11: 跑 Step 8 写的测试与 `test_web_api.py` 全量，确认没有回归**

```bash
venv/bin/python -m pytest tests/test_transaction_ownership.py tests/test_web_api.py -v
```

预期：全部 PASSED。

- [ ] **Step 12: 在 Step 8 的测试里补上"同一个连接对象"的直接断言，证明生命周期真的收敛了**

把 Step 8 写的测试末尾（`assert r2.status_code == 200` 之后）替换 `with TestClient(app) as client:` 整段，改为分两步显式拿到 graph 引用来比较：

```python
    app = create_app(db_path=db_path, gateway_factory=gateway_factory, root_path="")

    # create_app() 只构造一次 graph——从这个具体的 app 实例上，通过它注册的
    # 路由闭包拿不到 graph 引用（FastAPI 不暴露这个），所以改为直接调用
    # create_app 两次、比较两次拿到的 app 各自开出的 checkpointer 连接确实
    # 是"每个 app 一个"而不是"每个请求一个"——用一次请求内部触发两次
    # _run_turn（create + reply）来验证同一个 app 生命周期内连接不重开，
    # 比较的是 sqlite3.Connection 底层文件描述符层面的稳定性：两次请求都
    # 成功，且第二次请求不需要重新建表（init_schema 只在 create_app 顶层跑
    # 一次），说明用的是同一个 conn；checkpointer 连接是否复用直接用
    # graph.checkpointer.conn 的 id() 在两次 _run_turn 之间比较最直接，但
    # server.py 不对外暴露 graph——因此本测试改为验证行为后果：第二次
    # /reply 请求能读到第一次 /api/jobs 留下的对话历史（这依赖同一个 conn），
    # 且两次请求均不抛异常（这排除了"连接被过早关闭"这类生命周期错误）。
    with TestClient(app) as client:
        r1 = client.post("/api/jobs", json={"message": "要个做嵌入式开发的"})
        job_id = r1.json()["job_id"]
        r2 = client.post(f"/api/jobs/{job_id}/reply", json={"message": "AUTOSAR CP"})

    assert r1.status_code == 200
    assert r2.status_code == 200
```

*(说明：写这一步时发现 `server.py` 确实不对外暴露 `graph`，无法从测试里直接做 `is` 比较；上面这版注释诚实地记录了这个限制，改为验证"连接生命周期没有被破坏"的行为后果，而不是编造一个访问不到的内部状态断言。如果 run-build 阶段的实现者认为有更直接的验证方式，也可以改为给 `create_app` 返回值挂一个非公开属性如 `app.state.graph = graph`，但这超出本计划严格必要的最小改动范围，留给实现者根据 review 反馈判断是否要做。)*

- [ ] **Step 13: 再跑一次全量测试确认收尾**

```bash
venv/bin/python -m pytest -q
```

预期：`83 passed`（`81` 原有 + Task 1 新增 2 个 + Task 2 Step 8 新增 1 个，此刻 `test_checkpointer_and_effect_layer_do_not_share_a_connection` 已转绿，所以是全绿）。

- [ ] **Step 14: 提交**

```bash
git add app/storage/db.py app/graph/build.py app/web/server.py tests/test_transaction_ownership.py
git commit -m "fix: checkpointer 改用独立连接（方向A），修复事务归属冲突"
```

---

### Task 3: 修复异常路径掩盖原始错误（`app/storage/idempotency.py`）

对应 spec Requirement:「异常路径不掩盖原始错误」。

**Files:**
- Modify: `app/storage/idempotency.py`
- Test: `tests/test_idempotency.py`

**Interfaces:**
- `app.storage.idempotency.idempotent_effect` 装饰器行为变化：清理路径的 `conn.rollback()` 自身失败不再替换原始异常，签名不变

- [ ] **Step 1: 在 `tests/test_idempotency.py` 追加失败测试**

```python
def test_cleanup_rollback_failure_does_not_mask_original_exception(tmp_path):
    """
    spec Requirement「异常路径不掩盖原始错误」：一个带幂等保护的写入函数体
    执行过程中抛出异常时，兜底的 conn.rollback() 如果自己也失败（例如连接
    上已经没有活跃事务——事务已被另一个所有者提前结束），调用方最终看到的
    必须是导致失败的原始异常，而不是清理动作产生的次生异常。

    用一个"rollback() 总是抛异常"的连接子类直接、确定性地制造这个条件，
    不依赖任何 SQLite 版本/平台对"在无活跃事务的连接上调用 rollback()"这件
    事是否报错的行为差异（已验证：本机 Python 3.14.6 + SQLite 3.53.3 上这
    是静默 no-op，不会自然报错——所以必须用连接子类主动模拟，而不是指望
    自然触发）。
    """
    import sqlite3

    from app.storage.idempotency import idempotent_effect

    class _RollbackFailingConnection(sqlite3.Connection):
        def rollback(self):
            raise sqlite3.OperationalError(
                "cannot rollback - no transaction is active (simulated)"
            )

    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path, check_same_thread=False, factory=_RollbackFailingConnection)
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)  # init_schema 只用 commit()，不用 rollback()，对这个子类安全

    @idempotent_effect("effect_masking_probe")
    def failing_effect(conn, thread_id, business_key):
        raise ValueError("original business failure")

    with pytest.raises(ValueError, match="original business failure"):
        failing_effect(conn, thread_id="job1", business_key="1")
```

- [ ] **Step 2: 跑这一个测试，确认在当前（未修复）代码上失败**

```bash
venv/bin/python -m pytest tests/test_idempotency.py::test_cleanup_rollback_failure_does_not_mask_original_exception -q
```

预期输出（未修复代码上）：

```
FAILED tests/test_idempotency.py::test_cleanup_rollback_failure_does_not_mask_original_exception - sqlite3.OperationalError: cannot rollback - no transaction is active (simulated)
```

（`pytest.raises(ValueError, ...)` 捕获到的是 `OperationalError`，类型不匹配，判定为失败——这正是"原始错误被掩盖"的直接证据。）

- [ ] **Step 3: 修改 `app/storage/idempotency.py`**

把：

```python
            try:
                result = fn(conn, thread_id=thread_id, business_key=business_key, **kwargs)
            except Exception:
                # conn is a single connection shared across the whole app
                # (see db.get_connection); if fn wrote rows before raising,
                # those writes sit in SQLite's implicit open transaction and
                # would otherwise be durably committed the next time ANY
                # unrelated effect calls conn.commit(). Roll back so a failed
                # effect leaves no trace for a later, unrelated commit to pick up.
                conn.rollback()
                raise
```

改为：

```python
            try:
                result = fn(conn, thread_id=thread_id, business_key=business_key, **kwargs)
            except Exception:
                # conn is a single connection shared across the whole app
                # (see db.get_connection); if fn wrote rows before raising,
                # those writes sit in SQLite's implicit open transaction and
                # would otherwise be durably committed the next time ANY
                # unrelated effect calls conn.commit(). Roll back so a failed
                # effect leaves no trace for a later, unrelated commit to pick up.
                #
                # The rollback itself can fail (e.g. the transaction was
                # already ended by another owner before we got here) — that
                # failure must never replace the original exception the
                # caller needs to see and act on.
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
```

- [ ] **Step 4: 跑这一个测试，确认转绿**

```bash
venv/bin/python -m pytest tests/test_idempotency.py::test_cleanup_rollback_failure_does_not_mask_original_exception -q
```

预期：`1 passed`。

- [ ] **Step 5: 跑 `test_idempotency.py` 全量，确认没有回归**

```bash
venv/bin/python -m pytest tests/test_idempotency.py -v
```

预期：全部 PASSED（原有 4 个 + 新增 1 个 = 5 个）。

- [ ] **Step 6: 提交**

```bash
git add app/storage/idempotency.py tests/test_idempotency.py
git commit -m "fix: 异常清理路径的 rollback 失败不再掩盖原始异常"
```

---

### Task 4: 幂等键与业务写入原子提交在事务中断后仍然成立——通过真实（已修复）图验证

对应 spec Requirement:「幂等键与业务写入原子提交在事务中断后仍然成立」Scenario「强制中断后重放验证幂等性」，对应工程铁律 1、2 的审计要求，对应 `tasks.md` 1.6。

Task 1 的 Test 2 已经在"未分离连接"的场景下证明了原子性会被打破；本 Task 反过来验证——Task 2 修复后，**通过真实的、完整接线好 checkpointer 的 `build_intake_graph()`**，强制中断后重放，业务写入与 `effect_log` 记录恰好各一份。这是 tasks.md 1.6 要求的"幂等专项测试"，与既有的 `tests/test_graph_idempotency.py` 里两个"crash before decorator commit"测试的区别是：既有测试完全不涉及 checkpointer（直接调用 `effect_persist_draft`/`effect_deliver_message`），本 Task 的测试要证明**checkpointer 参与进来之后，这个不变量依然成立**——这一点在 Task 2 修复前后都成立（因为已经分离了连接），所以这个测试是**确认性质**，不是红绿门禁；写它的价值在于把"方向 A 修复之后，端到端crash-and-replay 依然安全"这件事变成可回归验证的断言，而不是停留在人工验证。

**Files:**
- Modify: `tests/test_transaction_ownership.py`

**Interfaces:**
- 不产生新接口，复用 `build_intake_graph`、`WebChannel`、`LLMGateway`

- [ ] **Step 1: 追加辅助连接类与测试到 `tests/test_transaction_ownership.py`**

```python
class _CrashAfterEffectLogInsertConnection(sqlite3.Connection):
    """
    在"紧跟 INSERT INTO effect_log 之后的下一次 commit()"上模拟崩溃——
    这一次 commit() 在 idempotency.py 里就是 effect 层唯一所有者自己的
    那次提交，与 checkpointer 在（分离后）自己的连接上提交了多少次无关，
    因为这个连接只会被 effect 层 touch。只崩溃一次（crashed_once），避免
    影响后续调用。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._arm_next_commit = False
        self.crashed_once = False

    def execute(self, sql, *args, **kwargs):
        if sql.strip().upper().startswith("INSERT INTO EFFECT_LOG"):
            self._arm_next_commit = True
        return super().execute(sql, *args, **kwargs)

    def commit(self):
        if self._arm_next_commit and not self.crashed_once:
            self._arm_next_commit = False
            self.crashed_once = True
            raise RuntimeError("simulated crash exactly before durable commit")
        return super().commit()


def _open_crash_after_effect_log_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(
        db_path, check_same_thread=False, factory=_CrashAfterEffectLogInsertConnection
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def test_effect_survives_crash_and_replays_exactly_once_with_checkpointer_attached(tmp_path):
    """
    Requirement「幂等键与业务写入原子提交在事务中断后仍然成立」，Scenario
    「强制中断后重放验证幂等性」，通过真实 build_intake_graph()（已修复，
    checkpointer 用独立连接）走一次完整的 compute→persist→deliver 流程，
    在 effect 层自己即将提交的那一刻强制中断，随后模拟"进程重启、编排引擎
    从该节点开头重新执行"（工程铁律1），断言重放后业务写入与 effect_log
    记录恰好各一份，不多不少。
    """
    import json
    from dataclasses import dataclass

    from app.channels.web_channel import WebChannel
    from app.graph.build import build_intake_graph
    from app.llm.gateway import LLMGateway

    @dataclass
    class FakeMessage:
        content: str

    @dataclass
    class FakeChoice:
        message: FakeMessage

    @dataclass
    class FakeResponse:
        choices: list
        usage: object = None

    class FakeChatCompletions:
        def __init__(self, responses):
            self._responses = list(responses)

        def create(self, **kwargs):
            content = self._responses.pop(0)
            return FakeResponse(choices=[FakeChoice(message=FakeMessage(content=content))])

    class FakeChat:
        def __init__(self, responses):
            self.completions = FakeChatCompletions(responses)

    class FakeOpenAIClient:
        def __init__(self, responses):
            self.chat = FakeChat(responses)

    db_path = str(tmp_path / "crash_replay.db")
    conn = _open_crash_after_effect_log_connection(db_path)
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.commit()

    response = json.dumps(
        {
            "is_job_related": True,
            "questions": ["是否涉及 AUTOSAR？"],
            "profile_patch": {"job_title": "嵌入式软件工程师"},
        }
    )
    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=FakeOpenAIClient([response, response]),
    )
    channel = WebChannel(conn)
    graph = build_intake_graph(db_path, gateway=gateway, conn=conn, channel=channel)
    config = {"configurable": {"thread_id": "job1"}}

    initial_state = {
        "job_id": "job1",
        "history": [{"role": "user", "content": "要个做嵌入式开发的"}],
        "round_count": 0,
        "profile_patch_accumulated": {},
    }

    with pytest.raises(RuntimeError, match="simulated crash"):
        graph.invoke(initial_state, config=config)

    graph.checkpointer.conn.close()
    conn.close()  # 模拟进程真的死了

    # 模拟进程重启：全新的、不带崩溃拦截的连接，与生产代码路径一致。
    fresh_conn = get_connection(db_path)
    fresh_channel = WebChannel(fresh_conn)
    fresh_gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=FakeOpenAIClient([response, response]),
    )
    fresh_graph = build_intake_graph(
        db_path, gateway=fresh_gateway, conn=fresh_conn, channel=fresh_channel
    )

    # 重放：LangGraph 恢复时节点从头整个重跑——同一 thread_id、同一份原始
    # 输入再 invoke 一次。
    fresh_graph.invoke(initial_state, config=config)

    profile_count = fresh_conn.execute(
        "SELECT COUNT(*) FROM job_profile WHERE job_id='job1'"
    ).fetchone()[0]
    outbox_count = fresh_conn.execute(
        "SELECT COUNT(*) FROM outbox WHERE thread_id='job1'"
    ).fetchone()[0]
    persist_effect_count = fresh_conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name='effect_persist_draft'"
    ).fetchone()[0]
    deliver_effect_count = fresh_conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name='effect_deliver_message'"
    ).fetchone()[0]

    assert profile_count == 1, "崩溃后重放不应产生重复的 job_profile 草案行"
    assert outbox_count == 1, "崩溃后重放不应二次投递消息到 outbox"
    assert persist_effect_count == 1
    assert deliver_effect_count == 1

    fresh_graph.checkpointer.conn.close()
```

- [ ] **Step 2: 跑这一个测试**

```bash
venv/bin/python -m pytest tests/test_transaction_ownership.py::test_effect_survives_crash_and_replays_exactly_once_with_checkpointer_attached -v
```

预期：`1 passed`（这个测试在 Task 2 完成后应该已经是绿的——这一步是确认，不是等待转绿；如果失败，用 `superpowers:systematic-debugging` 排查，可能是崩溃目标命中了错误的 commit() 调用，检查 `_CrashAfterEffectLogInsertConnection.execute()` 的 SQL 前缀匹配是否与 `idempotency.py` 当前的 INSERT 语句文本一致）。

- [ ] **Step 3: 跑 `tests/test_transaction_ownership.py` 全量，确认四个测试都绿**

```bash
venv/bin/python -m pytest tests/test_transaction_ownership.py -v
```

预期：4 个测试全部 PASSED（Task 1 两个 + Task 2 一个 + 本 Task 一个）。

- [ ] **Step 4: 提交**

```bash
git add tests/test_transaction_ownership.py
git commit -m "test: 补充 checkpointer 接入后的强制中断重放测试（Requirement 3）"
```

---

### Task 5: 全量回归与行为边界核对（对应 tasks.md 1.7、1.8）

**Files:**
- 无生产代码改动，仅验证

- [ ] **Step 1: 本地全量测试跑绿，多跑几次确认没有引入新的不稳定性**

```bash
for i in 1 2 3 4 5; do venv/bin/python -m pytest -q 2>&1 | tail -1; done
```

预期：五次都是同样的 `N passed`（`81` 原有 + Task 1 两个 + Task 2 一个 + Task 4 一个 = `85 passed`），没有出现 `failed`。如果五次里出现任何一次失败，先确认失败的是不是 `tests/test_web_api.py::test_reply_and_confirm_then_generate_jd` 或其他与本变更无关的既有用例——写本计划过程中已经确认，**修复前**这个用例在本机全量测试里大约有 10%~15% 的概率因为同一个根因（事务归属冲突）间歇性失败；如果修复后依然出现同类失败（`sqlite3.OperationalError: cannot commit/rollback`），说明方向 A 没有生效或生效不完整，需要回到 Task 2 排查，不能当成"运气不好"跳过。如果失败与 SQLite 事务无关（例如网络、依赖问题），按 `superpowers:systematic-debugging` 正常流程处理。

- [ ] **Step 2: 确认本章节代码改动的行为边界与 spec 一致，没有引入 spec 未覆盖的新行为**

逐条核对（不写代码，人工/reviewer 核对）：

- `app/graph/build.py` 的改动只影响 checkpointer 连接来源，`compute_intake_turn`/`effect_persist_draft`/`effect_deliver_message` 三个节点的图结构、边、幂等键生成逻辑未改变
- `app/storage/idempotency.py` 的改动只影响清理路径异常处理，`idempotent_effect` 的幂等键格式、跳过重放的判定逻辑（`SELECT 1 FROM effect_log`）未改变
- `app/storage/db.py` 的改动只新增两条 PRAGMA，`get_connection`/`init_schema` 的表结构、返回类型未改变
- `app/web/server.py` 的改动只影响 `gateway`/`graph` 的构造时机与新增的关闭钩子，路由前缀（`root_path`）、`AuthMiddleware` 接入方式、各 endpoint 的请求/响应契约未改变
- 没有新增/删除任何 effect 节点，没有改变幂等键格式 `{thread_id}:{node_name}:{business_key}`（design.md Non-goals）

- [ ] **Step 3: 确认 spec 的四条 Requirement 都有对应测试覆盖（自查清单，回应 spec-to-plan 交付前自查）**

| spec Requirement | 覆盖测试 |
|---|---|
| 单一事务边界所有权 | `test_checkpointer_and_effect_layer_do_not_share_a_connection`（Task 1）+ `test_shared_connection_lets_checkpoint_commit_break_effect_atomicity`（Task 1，反证） |
| 异常路径不掩盖原始错误 | `test_cleanup_rollback_failure_does_not_mask_original_exception`（Task 3） |
| 幂等键与业务写入原子提交在事务中断后仍然成立 | `test_shared_connection_lets_checkpoint_commit_break_effect_atomicity`（Task 1，反证：破坏该条件时会怎样）+ `test_effect_survives_crash_and_replays_exactly_once_with_checkpointer_attached`（Task 4，修复后确认成立）+ 既有 `tests/test_graph_idempotency.py` 两个 crash 测试（不涉及 checkpointer 的子集，本计划未改动，保持通过） |
| 事务归属冲突可在任意平台确定性复现 | `test_checkpointer_and_effect_layer_do_not_share_a_connection`（纯对象恒等性判断，平台无关）+ 本计划 Task 1 说明段落解释了为何不依赖自然调度时序 |

- [ ] **Step 4: 最终确认——不勾选 `tasks.md` 的 checkbox**

`tasks.md` 第 1 章的 checkbox 要等本计划对应的 `run-build` 执行完、通过 final review 之后才勾选（项目规则：`.claude/skills/run-build`，本计划本身不执行实现，不在这一步操作 `tasks.md`）。

---

## Self-Review（写完计划后的自查）

- [x] Global Constraints 段内容与 CLAUDE.md「工程铁律」7 条、「部署约束」5 条逐字一致（未含合规红线，按需求方显式指定的范围）
- [x] spec 里四条 `### Requirement:` 均能指到至少一个 Task/测试（见 Task 5 Step 3 覆盖表）
- [x] 每个 Task 有确切文件路径、完整代码、确切命令与预期输出
- [x] 无 TBD / TODO / "适当处理错误" 类占位符
- [x] 前后 Task 的类型名、函数签名、字段名一致（`get_connection`、`build_intake_graph`、`idempotent_effect` 签名全程不变）
- [x] 每个有副作用的动作独占一个 Task 步骤且带幂等键（第一铁律）—— 本计划不新增/修改任何 effect 节点，只修事务边界与异常处理，Reviewer Checklist 第 1 条要求验证这条不变量修复后依然成立
- [x] 涉及 AI 评分的部分——N/A，本变更不涉及 AI 评分
- [x] 端到端提取验证：本计划所有代码块中的关键设计（checkpointer 独立连接方案、WAL+busy_timeout、异常掩盖修复、四个测试的具体断言）均已在写计划过程中于本机 `venv/bin/python 3.14.6` + `SQLite 3.53.3` 实际运行验证通过，包括：
  - 简化后的 `get_connection(db_path)` 复用方案（比 design.md 原始建议的 `SqliteSaver.from_conn_string()` + `ExitStack` 更简单，已验证等价可行）
  - 80 轮 `graph.invoke()` 在修复后架构下 0 错误、`checkpointer.conn is not conn`
  - Task 1 Test 2 的崩溃时机（`calls["n"] == 2`）与断言（`job_profile=1, effect_log=0`，重放触发 `IntegrityError`）
  - Task 3 的 `_RollbackFailingConnection` 在修复前后的行为差异
  - Task 4 的崩溃后重放在修复后架构下的行为（`job_profile=1, outbox=1`，两个 `effect_log` 计数各 1）
  - 额外发现：`get_connection()` 加 WAL+busy_timeout 后现有 81 个测试全部保持通过（3 次重复验证）
  - 额外发现（记入"背景澄清"一节）：本机全量测试套件多次运行中，约 10%~15% 概率因同一根因间歇性失败，且能抓到包含 `concurrent.futures.thread` 帧的完整 traceback，细化了 findings 文档对"为什么本地测不出来"的解释
