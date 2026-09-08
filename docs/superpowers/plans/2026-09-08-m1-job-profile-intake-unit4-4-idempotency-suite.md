# 幂等专项测试（交付单元 4.4）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给仓库里**每一个** `effect_*` 节点建一条「在业务写之后、事务提交之前强制中断 → 模拟进程重启 → 按 thread_id 恢复重跑」的专项用例，断言副作用恰好发生一次；并让"新增了 effect 节点却没进测试"变成一条**必然变红**的失败。

**Architecture:** 单一新增文件 `tests/test_effect_idempotency_suite.py`，三层：
① **节点清单自动收集器**——用 `ast` 扫 `app/` 下全部 `.py`，收集所有被 `@idempotent_effect("…")` 装饰的函数（AST 只认真装饰器，天然跳过 `app/audit/assertions.py:264` 与 `app/outbound/delivery.py:155` 那两处出现在注释/docstring 里的字面量，grep 会误报）；与硬编码 `EFFECT_NODE_MANIFEST` 双向比对，任一方多一个都失败。
② **配方注册表 `build_recipes(tmp_path)`**——每个节点一条 `Recipe`（种子数据、调用方式、业务行计数、每次生效应产生的业务行数）。注册表的键集合必须与清单**逐字相等**，于是"加了节点没加配方"也会当场变红。
③ **参数化崩溃-恢复用例**——用 `sqlite3.Connection` 子类在"紧跟 `INSERT INTO effect_log` 之后的那一次 `commit()`"上抛异常，这正是 `idempotent_effect` 唯一一次提交、且业务写已经在同一事务里的时刻。随后 `conn.close()`（未提交事务随连接丢弃＝进程崩溃）、换一条全新连接（＝进程重启）确认什么都没落盘，再用同一 `thread_id`/`business_key` 重跑，断言 `effect_log` 与业务行各恰好一份。

**Tech Stack:** Python 3.11+ · pytest · sqlite3（`app/storage/db.py` 的 `get_connection` / `init_schema`）· LangGraph ≥1.0.10（本单元不直接调用，仅其"恢复时节点从头整个重跑"语义是被测契约）

## Global Constraints

以下逐字复制自 `CLAUDE.md`「工程铁律」「合规红线」，以及本交付单元 opener 的五条范围约束。**reviewer 以此为注意力透镜。**

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
   *为什么*：业务写失败而幂等记录成功 → 系统判定"已执行"→ 永不重试。**幂等本是防重复的保护，拆开事务后变成永久丢失的保证。**
2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。
3. **"强制中断"要真中断**：在业务写之后、事务提交之前注入异常（子类化连接在 `commit()` 上抛，或节点内钩子），然后按 `thread_id` 恢复重跑。⛔ **不用"连着调两次函数"冒充中断**——那只重新验证了装饰器的短路分支，不验证"崩溃时半截写入不落盘"。
4. **每个 `effect_*` 节点各一条参数化用例**，节点清单由 AST 自动收集并与硬编码清单比对——新增节点没进测试时**测试必须失败**（防清单过期）。
5. **只新增 `tests/` 下的文件；⛔ 不改 `app/` 任何代码。** 若发现某节点真不幂等，登记为红灯写进本计划末尾的「红灯与观察项」并在 run-build 报告里单列，⛔ 不顺手修（那是另一个单元的事）。
6. **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。本单元不得新增任何会写 `rejection_record` 的路径。
7. **合规红线：AI 生成的 JD、拒信、邀约须带标识。** 涉及 `effect_update_jd_text` / `effect_mark_jd_human_written` 的用例，断言只看幂等与行数恒等，⛔ 不得为了让用例好写而绕过标识逻辑。
8. **并行同伴**：网关泳道在改 `app/graph/nodes.py`（提取异常映射处），识别泳道在改 `app/agents/`、`app/web/`。本单元的测试若依赖 `nodes.py` 的新信号，**以 rebase 后的 main 为准**；⛔ 不把 `nodes.py` 的当前行号写进断言。

---

## 被测清单（截至 2026-09-08，`ast` 口径，共 **10** 个）

| # | 节点 | 位置 | 业务事实（计数口径） | 现有覆盖 |
|---|---|---|---|---|
| 1 | `effect_persist_draft` | `app/graph/nodes.py` | `job_profile` 行 | ✅ 已有崩溃-恢复用例 |
| 2 | `effect_deliver_message` | `app/graph/nodes.py` | `outbox` 行 | ✅ 已有崩溃-恢复用例 |
| 3 | `effect_confirm_profile` | `app/graph/nodes.py` | `human_review` 中 `approved` 行 | ⚠️ 仅"调两次"+ commit 计数 |
| 4 | `effect_request_revision` | `app/graph/nodes.py` | `human_review` 中 `revision_requested` 行 | ❌ 无 |
| 5 | `effect_abandon_profile` | `app/graph/nodes.py` | `human_review` 中 `abandoned` 行 | ❌ 无 |
| 6 | `effect_generate_and_persist_jd` | `app/graph/nodes.py` | `job_profile.profile_json` 里带 `_jd_text` 的行 | ❌ 无（**代价最大的一个**，重放会重复触发付费 LLM 调用） |
| 7 | `effect_enqueue_pending_approval` | `app/graph/nodes.py` | `pending_approval` 行 | ⚠️ 仅"调两次"+ commit 计数 |
| 8 | `effect_record_outbound_audit` | `app/graph/nodes.py` | **SQLite 里 0 行**（真身在 JSONL 镜像，由调用方在提交之后 append） | ⚠️ 仅"调两次" |
| 9 | `effect_update_jd_text` | `app/graph/jd_nodes.py` | `profile_json._jd_text` 等于目标文本的行 | ⚠️ 仅"调两次" |
| 10 | `effect_mark_jd_human_written` | `app/graph/jd_nodes.py` | `profile_json._jd_authorship` 非空的行 | ⚠️ 函数体内钩子崩溃（非提交前） |

⚠️ **`openspec/changes/m1-job-profile-intake/tasks.md` 第 136 行那条注解写的是"4 个 effect 节点里覆盖了 3 个"，该数字已过期**（写于只有 4 个节点时）。以本表与 AST 收集结果为准。Task 3 收尾时顺带订正那行注解。

---

### Task 1: 节点清单自动收集器与防过期守卫

**Files:**
- Create: `tests/test_effect_idempotency_suite.py`

**Interfaces:**
- Consumes: `app/storage/idempotency.py` 的 `idempotent_effect` 装饰器（只读其字面量参数，不 import 运行时对象）
- Produces: 供 Task 2、Task 3 使用的模块级符号——
  - `EFFECT_NODE_MANIFEST: frozenset[str]`（10 个节点名）
  - `collect_effect_nodes() -> dict[str, str]`（节点名 → `"相对路径:行号"`）

- [ ] **Step 1: 写失败的测试**

新建 `tests/test_effect_idempotency_suite.py`，写入文件头与 Task 1 的两段内容：

```python
"""
交付单元 4.4：`effect_*` 节点的幂等专项测试。

⭐ 本文件是**工程铁律 1 的全量守护**，不是"再写几个用例"。它要做到两件事：

1. 仓库里**每一个** `effect_*` 节点都有一条「业务写之后、事务提交之前强制中断
   → 进程重启 → 按 thread_id 恢复重跑」的用例，断言副作用恰好发生一次。
2. 将来**新增一个 effect 节点却忘了加用例时，这个文件必须变红**。清单靠 AST
   从源码里现扫，与下面的硬编码清单双向比对——两边不一致就失败，谁都别想
   悄悄溜过去。铁律 1 的失败是静默的（不报错、不失败，只是少做/多做一次副
   作用），能抓住它的只有"清单过期即变红"这一条机制。

⛔ **本文件不改 `app/` 任何代码。** 若某个节点在这里被测出真的不幂等，
登记进计划的「红灯与观察项」，由另一个交付单元修——在测试单元里顺手改
被测代码，等于让测试给自己开绿灯。

⚠️ 与既有三个文件的分工（⛔ 不重复造）：
- `tests/test_idempotency.py`     —— 装饰器**本身**的语义（短路、回滚、日志）
- `tests/test_transaction_ownership.py` —— 事务**归属**（checkpointer 不得共用连接）
- `tests/test_graph_idempotency.py`     —— `effect_persist_draft` /
  `effect_deliver_message` / `effect_confirm_profile` 三个节点的既有覆盖
本文件补的是**横向全量**：把同一条崩溃-恢复协议施加到全部 10 个节点上，
并让清单无法过期。既有用例一条都不删、一条都不改。
"""

import ast
import pathlib

# 仓库根 = tests/ 的上一级
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_APP_ROOT = _REPO_ROOT / "app"

# ⚠️ 硬编码清单。加了新的 effect 节点就往这里加一行，**同时**去
# build_recipes() 里加一条配方——两处都加了，测试才会绿。
# ⛔ 不要为了让测试变绿而删这里的名字：删掉等于宣布"这个节点不需要幂等
# 保护"，那是铁律 1 的例外，只有 Shao Peishen 能拍。
EFFECT_NODE_MANIFEST = frozenset(
    {
        "effect_persist_draft",
        "effect_deliver_message",
        "effect_confirm_profile",
        "effect_request_revision",
        "effect_abandon_profile",
        "effect_generate_and_persist_jd",
        "effect_enqueue_pending_approval",
        "effect_record_outbound_audit",
        "effect_update_jd_text",
        "effect_mark_jd_human_written",
    }
)


def collect_effect_nodes() -> dict[str, str]:
    """AST 扫 `app/` 下全部 .py，返回 {节点名: "相对路径:行号"}。

    ⭐ **用 AST 而不是 grep**：`app/audit/assertions.py` 的注释里、
    `app/outbound/delivery.py` 的 docstring 里都出现过 `@idempotent_effect`
    这串字面量（后者原文是"本函数**不是** effect_* 节点、⛔ 不加
    @idempotent_effect"）。grep 会把这两处当成节点，清单守卫就会为了一条
    注释而误报，几次之后没人再信它——一个总在误报的守卫等于没有守卫。
    AST 只看真正的 decorator 节点，从结构上没有这个问题。

    节点名取**装饰器的字面量参数**而非函数名：幂等键里存进 effect_log 的是
    这个字面量（见 app/storage/idempotency.py），它才是数据库里的事实。
    两者一致由 `app/audit/assertions.py` 另行保证，本文件不重复断言。
    """
    found: dict[str, str] = {}
    for path in sorted(_APP_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                if not isinstance(deco, ast.Call):
                    continue
                func = deco.func
                deco_name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if deco_name != "idempotent_effect":
                    continue
                assert len(deco.args) == 1 and isinstance(deco.args[0], ast.Constant), (
                    f"{path}:{node.lineno} 的 @idempotent_effect 参数不是字面量字符串。"
                    "节点名必须是字面量——变量或表达式会让 effect_log 里的 node_name "
                    "无法从源码静态推断，这条清单守卫和 app/audit/assertions.py 的"
                    "断言就同时失效了。"
                )
                found[deco.args[0].value] = f"{path.relative_to(_REPO_ROOT)}:{node.lineno}"
    return found


def test_manifest_matches_the_source_tree():
    """⭐ 防清单过期：源码里有、清单里没有 → 失败（新节点漏测）；反之亦然（节点已删）。"""
    discovered = set(collect_effect_nodes())
    missing_from_manifest = discovered - set(EFFECT_NODE_MANIFEST)
    stale_in_manifest = set(EFFECT_NODE_MANIFEST) - discovered
    assert not missing_from_manifest, (
        f"源码里新增了 effect 节点但没进本文件的清单：{sorted(missing_from_manifest)}。"
        "把它们加进 EFFECT_NODE_MANIFEST，并在 build_recipes() 里各加一条崩溃-恢复"
        "配方——铁律 1 要求每个 effect_* 节点都被强制中断验证过。"
    )
    assert not stale_in_manifest, (
        f"清单里的节点在源码里已经不存在了：{sorted(stale_in_manifest)}。"
        "确认是被删/改名而不是被漏扫，然后同步更新 EFFECT_NODE_MANIFEST。"
    )


def test_collector_reports_where_each_node_lives():
    """收集器要给出位置，否则清单变红时没人知道该去哪个文件加配方。"""
    located = collect_effect_nodes()
    assert located["effect_persist_draft"].startswith("app/graph/nodes.py:")
    assert located["effect_update_jd_text"].startswith("app/graph/jd_nodes.py:")


def test_collector_ignores_the_literal_in_comments_and_docstrings():
    """
    回归守卫：`app/outbound/delivery.py` 的 docstring 里有一句
    "⛔ 不加 @idempotent_effect"，`app/audit/assertions.py` 的注释里也有。
    用 grep 实现收集器会把它们当成节点，这条断言把那种实现钉死在红灯上。
    """
    located = collect_effect_nodes()
    for name, where in located.items():
        assert name.startswith("effect_"), f"{name} @ {where} 不像节点名，收集器可能扫到了注释"
    assert not any(where.startswith("app/outbound/delivery.py:") for where in located.values()), (
        "app/outbound/delivery.py 里没有任何 effect_* 节点，只有一句说明它"
        "**不是**节点的 docstring——收集器扫到它说明用错了实现（应为 AST，非文本匹配）"
    )
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `python -m pytest tests/test_effect_idempotency_suite.py -v`
Expected: 3 条用例全部收集到；`test_manifest_matches_the_source_tree` 是本步的关键——如果 `app/` 下的节点与清单不符会 FAIL 并列出差集。若此刻已 PASS，说明清单与源码恰好一致，属正常（这两条守卫的价值在**将来**变红），继续 Step 3。

- [ ] **Step 3: 用一次人为篡改证明守卫真的会红**

临时把 `EFFECT_NODE_MANIFEST` 里的 `"effect_mark_jd_human_written"` 那一行删掉，跑：

Run: `python -m pytest tests/test_effect_idempotency_suite.py::test_manifest_matches_the_source_tree -v`
Expected: FAIL，报文包含 `源码里新增了 effect 节点但没进本文件的清单：['effect_mark_jd_human_written']`

**把那一行加回去**，再跑一次：

Run: `python -m pytest tests/test_effect_idempotency_suite.py -v`
Expected: 3 passed

⚠️ 这一步是**必做**的，不是可选验证。"新增节点漏测就变红"是本单元交付的核心机制，只写不验＝交付了一个从未被证明会响的警报器。

- [ ] **Step 4: 提交**

```bash
git add tests/test_effect_idempotency_suite.py
git commit -m "test(idempotency): AST 收集 effect_* 节点清单，漏测即变红"
```

---

### Task 2: 每节点「真中断 → 进程重启 → 恢复重跑」参数化用例

**Files:**
- Modify: `tests/test_effect_idempotency_suite.py`（在 Task 1 内容之后追加）

**Interfaces:**
- Consumes: Task 1 的 `EFFECT_NODE_MANIFEST`
- Produces:
  - `class _CrashBeforeDurableCommit(sqlite3.Connection)`
  - `_open_crashing_connection(db_path: str) -> sqlite3.Connection`
  - `@dataclass(frozen=True) class Recipe`（字段：`thread_id: str`、`seed: Callable[[sqlite3.Connection], None]`、`invoke: Callable[[sqlite3.Connection], None]`、`count_business_rows: Callable[[sqlite3.Connection], int]`、`rows_per_effect: int`、`note: str`）
  - `build_recipes(tmp_path: pathlib.Path) -> dict[str, Recipe]`

- [ ] **Step 1: 写失败的测试（先只写崩溃机制 + 配方注册表完备性）**

在 `tests/test_effect_idempotency_suite.py` 末尾追加：

```python
import json
import sqlite3
from dataclasses import dataclass
from typing import Callable

import pytest

from app.agents.jd_agent import AI_LABEL_TEMPLATE
from app.audit.events import OUTBOUND_BLOCKED, DecisionEvent
from app.audit.recorder import AuditRecorder
from app.audit.sinks import JsonlChainSink, SqliteSink
from app.channels.base import OutboundMessage
from app.channels.web_channel import WebChannel
from app.graph.jd_nodes import (
    JD_AUTHORSHIP_KEY,
    JD_TEXT_KEY,
    effect_mark_jd_human_written,
    effect_update_jd_text,
    jd_edit_business_key,
)
from app.graph.nodes import (
    effect_abandon_profile,
    effect_confirm_profile,
    effect_deliver_message,
    effect_enqueue_pending_approval,
    effect_generate_and_persist_jd,
    effect_persist_draft,
    effect_record_outbound_audit,
    effect_request_revision,
)
from app.outbound.messages import CandidateOutboundMessage
from app.schemas.job_profile import JobProfile
from app.storage.db import get_connection, init_schema

_JOB = "job-4-4"
_TS = "2026-09-08T02:00:00+00:00"
_LABEL = AI_LABEL_TEMPLATE.format(generated_at=_TS)
_AI_BODY = f"【AI 生成】本文案由系统基于岗位画像自动生成，生成时间 {_TS}。很遗憾……"


class _CrashBeforeDurableCommit(sqlite3.Connection):
    """
    在"紧跟 `INSERT INTO effect_log` 之后的那一次 `commit()`"上抛异常。

    ⭐ **这就是"真中断"的落点**，不是随便找个地方抛。`idempotent_effect`
    的执行序是：查 effect_log → 跑函数体（业务写）→ INSERT effect_log →
    commit()。在这一次、也是唯一一次 commit() 上抛，命中的正是
    "业务写已经在事务里、还没落盘"的那一瞬间。异常从 commit() 里抛出，
    落在装饰器 try/except **之外**（它只包着函数体），所以不会触发 rollback，
    事务保持打开——随后 conn.close() 丢弃它，等价于进程崩溃。

    ⛔ 不用"连着调两次函数"冒充中断：那走的是装饰器的短路分支，
    证明的是"命中 effect_log 会跳过"，完全没有触碰"半截写入会不会落盘"。
    第二次调用时数据库里已经有第一次的完整提交，测不出任何原子性问题。

    只崩一次（crashed_once），恢复重跑走的是同一个类的正常路径。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._armed = False
        self.crashed_once = False

    def execute(self, sql, *args, **kwargs):
        if sql.strip().upper().startswith("INSERT INTO EFFECT_LOG"):
            self._armed = True
        return super().execute(sql, *args, **kwargs)

    def commit(self):
        if self._armed and not self.crashed_once:
            self._armed = False
            self.crashed_once = True
            raise RuntimeError("simulated crash exactly before durable commit")
        return super().commit()


def _open_crashing_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(
        db_path, check_same_thread=False, factory=_CrashBeforeDurableCommit
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@dataclass(frozen=True)
class Recipe:
    """一个 effect 节点的崩溃-恢复配方。

    `count_business_rows` 数的是**这个节点自己的那条业务事实**，不是"某张表
    的全部行"。有些节点只做 UPDATE（`effect_confirm_profile` 改 status），
    行数不变，所以口径要选"处于目标状态的行数"而不是"新增行数"。
    `rows_per_effect` 是"一次生效应当留下几条业务事实"，正常是 1。
    """

    thread_id: str
    seed: Callable[[sqlite3.Connection], None]
    invoke: Callable[[sqlite3.Connection], None]
    count_business_rows: Callable[[sqlite3.Connection], int]
    rows_per_effect: int = 1
    note: str = ""


def _seed_nothing(conn: sqlite3.Connection) -> None:
    conn.commit()


def _seed_job(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES (?, '底层软件工程师', 'drafting')",
        (_JOB,),
    )
    conn.commit()


def _seed_job_with_profile_v1(conn: sqlite3.Connection, profile: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES (?, '底层软件工程师', 'drafting')",
        (_JOB,),
    )
    conn.execute(
        "INSERT INTO job_profile (job_id, version, status, profile_json) "
        "VALUES (?, 1, 'drafting', ?)",
        (_JOB, json.dumps(profile or {"job_title": "底层软件工程师"}, ensure_ascii=False)),
    )
    conn.commit()


def _human_review_count(conn: sqlite3.Connection, decision_type: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM human_review WHERE job_id = ? AND decision_type = ?",
        (_JOB, decision_type),
    ).fetchone()[0]


def _profile_v1(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT profile_json FROM job_profile WHERE job_id = ? AND version = 1", (_JOB,)
    ).fetchone()
    return json.loads(row[0])


class _CountingGateway:
    """给 effect_generate_and_persist_jd 用的 LLM 替身。

    只实现 generate_jd() 真正调到的那个方法。**按类计数**（不是按实例），
    因为崩溃与重放各用一次全新的 invoke()，实例计数会各自归零，就看不见
    "同一份 JD 被生成了两次"这个事实——而那正是 Task 3 要如实记录的观察项。
    """

    calls = 0

    def extract_structured(self, *, system_prompt, user_prompt, schema, prompt_version):
        type(self).calls += 1
        return schema(body="岗位职责：负责 ECU 底层软件开发，参与量产项目交付。")


def _jd_profile() -> JobProfile:
    return JobProfile(
        job_title="底层软件工程师",
        department="研发部",
        headcount=1,
        education_requirement="本科及以上",
        experience_years="3-5年",
    )


_EDITED_JD = "岗位职责：负责 ECU 底层软件开发（HR 手改版）。"


def build_recipes(tmp_path: pathlib.Path) -> dict[str, Recipe]:
    """节点名 → 崩溃-恢复配方。键集合必须与 EFFECT_NODE_MANIFEST 逐字相等。"""

    def _deliver(conn):
        effect_deliver_message(
            conn,
            thread_id=_JOB,
            business_key="hash-1",
            channel=WebChannel(conn),
            message=OutboundMessage(type="question", payload={"questions": ["Q1"]}),
        )

    def _audit(conn):
        recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(tmp_path / "decisions.jsonl"))
        message = CandidateOutboundMessage(
            message_type="rejection_letter", recipient="cand-9@example.com", body=_AI_BODY
        )
        effect_record_outbound_audit(
            conn,
            thread_id=_JOB,
            business_key=f"{message.content_hash()}:False:等待人工确认",
            recorder=recorder,
            event=DecisionEvent(
                id=f"{_JOB}:effect_record_outbound_audit:{message.content_hash()}:False",
                event_type=OUTBOUND_BLOCKED,
                thread_id=_JOB,
                message_type=message.message_type,
                recipient=message.recipient,
                content_hash=message.content_hash(),
                blocked_reason="等待人工确认",
                evidence={"severity": "high"},
            ),
        )

    def _enqueue(conn):
        effect_enqueue_pending_approval(
            conn,
            thread_id=_JOB,
            business_key="draft-hash-1",
            message=CandidateOutboundMessage(
                message_type="rejection_letter", recipient="cand-9@example.com", body=_AI_BODY
            ),
            blocked_reason="等待人工确认",
        )

    def _seed_jd(conn):
        _seed_job_with_profile_v1(
            conn,
            {
                "job_title": "底层软件工程师",
                JD_TEXT_KEY: f"岗位职责：负责 ECU 底层软件开发。\n\n{_LABEL}",
                "_jd_needs_manual": False,
            },
        )

    return {
        "effect_persist_draft": Recipe(
            thread_id=_JOB,
            seed=_seed_job,
            invoke=lambda conn: effect_persist_draft(
                conn,
                thread_id=_JOB,
                business_key="0",
                state={"profile_patch_accumulated": {"job_title": "底层软件工程师"}, "history": []},
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM job_profile WHERE job_id = ?", (_JOB,)
            ).fetchone()[0],
        ),
        "effect_deliver_message": Recipe(
            thread_id=_JOB,
            seed=_seed_nothing,
            invoke=_deliver,
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM outbox WHERE thread_id = ?", (_JOB,)
            ).fetchone()[0],
        ),
        "effect_confirm_profile": Recipe(
            thread_id=_JOB,
            seed=_seed_job_with_profile_v1,
            invoke=lambda conn: effect_confirm_profile(
                conn,
                thread_id=_JOB,
                business_key="1",
                profile_dict={"job_title": "底层软件工程师"},
                reviewer="业务经理甲",
            ),
            count_business_rows=lambda conn: _human_review_count(conn, "approved"),
        ),
        "effect_request_revision": Recipe(
            thread_id=_JOB,
            seed=_seed_job_with_profile_v1,
            invoke=lambda conn: effect_request_revision(
                conn,
                thread_id=_JOB,
                business_key="1",
                reviewer="业务经理甲",
                feedback="学历要求写高了",
            ),
            count_business_rows=lambda conn: _human_review_count(conn, "revision_requested"),
        ),
        "effect_abandon_profile": Recipe(
            thread_id=_JOB,
            seed=_seed_job_with_profile_v1,
            invoke=lambda conn: effect_abandon_profile(
                conn,
                thread_id=_JOB,
                business_key="1",
                reviewer="业务经理甲",
                feedback="岗位取消",
            ),
            count_business_rows=lambda conn: _human_review_count(conn, "abandoned"),
        ),
        "effect_generate_and_persist_jd": Recipe(
            thread_id=_JOB,
            seed=_seed_job_with_profile_v1,
            invoke=lambda conn: effect_generate_and_persist_jd(
                conn,
                thread_id=_JOB,
                business_key="1",
                gateway=_CountingGateway(),
                profile=_jd_profile(),
                profile_dict={"job_title": "底层软件工程师"},
                version=1,
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM job_profile WHERE job_id = ? "
                "AND json_extract(profile_json, '$._jd_text') IS NOT NULL",
                (_JOB,),
            ).fetchone()[0],
            note="唯一一个重放会重复触发付费 LLM 调用的节点，见「红灯与观察项」O-1",
        ),
        "effect_enqueue_pending_approval": Recipe(
            thread_id=_JOB,
            seed=_seed_nothing,
            invoke=_enqueue,
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM pending_approval WHERE thread_id = ?", (_JOB,)
            ).fetchone()[0],
        ),
        "effect_record_outbound_audit": Recipe(
            thread_id=_JOB,
            seed=_seed_nothing,
            invoke=_audit,
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM analysis_run WHERE thread_id = ?", (_JOB,)
            ).fetchone()[0],
            rows_per_effect=0,
            note=(
                "**显式声明的例外**：外发事件在 analysis_run 里没有真身"
                "（SqliteSink.SUPPORTED_EVENT_TYPES 只收 ai_analysis），它的载体是"
                "JSONL 镜像，而镜像 append 由调用方在装饰器 commit **之后**触发，"
                "本就不在事务里。所以本节点的 SQLite 业务行数恒为 0——这是设计如此，"
                "⛔ 不是漏测。见「红灯与观察项」O-2"
            ),
        ),
        "effect_update_jd_text": Recipe(
            thread_id=_JOB,
            seed=_seed_jd,
            invoke=lambda conn: effect_update_jd_text(
                conn,
                thread_id=_JOB,
                business_key=jd_edit_business_key(1, _EDITED_JD),
                version=1,
                edited_text=_EDITED_JD,
            ),
            count_business_rows=lambda conn: sum(
                1
                for _ in [1]
                if _EDITED_JD in _profile_v1(conn).get(JD_TEXT_KEY, "")
            ),
        ),
        "effect_mark_jd_human_written": Recipe(
            thread_id=_JOB,
            seed=_seed_jd,
            invoke=lambda conn: effect_mark_jd_human_written(
                conn,
                thread_id=_JOB,
                business_key="1",
                version=1,
                reviewer="HR 乙",
                marked_at=_TS,
            ),
            count_business_rows=lambda conn: sum(
                1 for _ in [1] if _profile_v1(conn).get(JD_AUTHORSHIP_KEY)
            ),
        ),
    }


def test_every_effect_node_has_a_recovery_recipe(tmp_path):
    """⭐ 防配方过期：清单里有、配方里没有 → 失败。与 Task 1 的守卫合起来，
    "新增节点却没写崩溃-恢复用例"在两个方向上都会变红。"""
    recipes = set(build_recipes(tmp_path))
    assert recipes == set(EFFECT_NODE_MANIFEST), (
        f"清单与配方不一致。缺配方：{sorted(set(EFFECT_NODE_MANIFEST) - recipes)}；"
        f"多配方：{sorted(recipes - set(EFFECT_NODE_MANIFEST))}"
    )


@pytest.mark.parametrize("node_name", sorted(EFFECT_NODE_MANIFEST))
def test_forced_interrupt_then_recovery_applies_the_effect_exactly_once(node_name, tmp_path):
    """
    ⭐⭐⭐ 本单元的主用例。对**每一个** effect_* 节点走同一条协议：

      种子数据 → 调用节点 → 在"业务写已入事务、effect_log 已 INSERT、
      commit 尚未落盘"的那一刻强制中断 → close()（未提交事务随连接丢弃，
      等价进程崩溃）→ 换全新连接（等价进程重启）确认**什么都没落盘** →
      用同一个 thread_id / business_key 重跑（等价 LangGraph 从节点开头
      整个重跑）→ 断言 effect_log 与业务事实**各恰好一份**。

    中间那一步"确认什么都没落盘"是关键：只断言最终一份，测不出"业务写落了
    盘、effect_log 没落"这个最坏情形——那种情形下重放要么撞唯一约束永久
    失败，要么静默做第二次副作用。
    """
    recipe = build_recipes(tmp_path)[node_name]
    db_path = str(tmp_path / f"{node_name}.db")

    conn = _open_crashing_connection(db_path)
    init_schema(conn)
    conn.commit()
    recipe.seed(conn)

    rows_before = recipe.count_business_rows(conn)

    with pytest.raises(RuntimeError, match="simulated crash"):
        recipe.invoke(conn)
    assert conn.crashed_once, (
        f"{node_name} 没有触发中断——说明它的 effect_log INSERT 之后没有 commit()，"
        "或者根本没走 idempotent_effect。这本身就是铁律 1 的红灯，⛔ 不要靠放宽"
        "断言绕过去"
    )
    conn.close()

    fresh = get_connection(db_path)
    assert (
        fresh.execute(
            "SELECT COUNT(*) FROM effect_log WHERE node_name = ? AND thread_id = ?",
            (node_name, recipe.thread_id),
        ).fetchone()[0]
        == 0
    ), f"{node_name}：崩溃点之前 effect_log 不应有任何行落盘"
    assert recipe.count_business_rows(fresh) == rows_before, (
        f"{node_name}：崩溃点之前业务写不应落盘。落了就说明业务写与 effect_log "
        "不在同一个事务里——铁律 1 的直接违反，重放会撞唯一约束或静默重复副作用"
    )

    recipe.invoke(fresh)

    effect_rows = fresh.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = ? AND thread_id = ?",
        (node_name, recipe.thread_id),
    ).fetchone()[0]
    assert effect_rows == 1, f"{node_name}：恢复重跑后 effect_log 应恰好 1 条，实得 {effect_rows}"
    assert recipe.count_business_rows(fresh) == rows_before + recipe.rows_per_effect, (
        f"{node_name}：恢复重跑后业务事实应恰好 {recipe.rows_per_effect} 份。{recipe.note}"
    )
```

- [ ] **Step 2: 跑测试**

Run: `python -m pytest tests/test_effect_idempotency_suite.py -v`
Expected: 10 条参数化用例 + 5 条守卫用例，全部 PASS。

**若某个节点 FAIL**：先确认不是配方写错（种子缺行、`business_key` 类型不对、FK 约束）。排除配方问题后仍红 = 该节点真的不幂等 → **⛔ 不改 `app/`**，把它写进本计划末尾「红灯与观察项」，并在这条参数化用例上加 `pytest.param(..., marks=pytest.mark.xfail(strict=True, reason="红灯 R-N：<一句话>，由交付单元 <X> 修"))`。`strict=True` 是必须的：将来那个节点被修好了，xfail 会变成 XPASS 失败，提醒把标记摘掉。

- [ ] **Step 3: 再次人为篡改，证明主用例真的会红**

临时在 `build_recipes` 里把 `effect_request_revision` 那一条整个删掉，跑：

Run: `python -m pytest tests/test_effect_idempotency_suite.py::test_every_effect_node_has_a_recovery_recipe -v`
Expected: FAIL，报文含 `缺配方：['effect_request_revision']`

**恢复**该条配方，重跑全量：

Run: `python -m pytest tests/test_effect_idempotency_suite.py -v`
Expected: 15 passed

- [ ] **Step 4: 跑全量回归，确认没碰坏既有用例**

Run: `python -m pytest tests/ -q`
Expected: 全绿。既有的 `test_idempotency.py` / `test_graph_idempotency.py` / `test_transaction_ownership.py` / `test_jd_nodes.py` / `test_outbound_effects.py` 一条都没改，应当原样通过。

- [ ] **Step 5: 提交**

```bash
git add tests/test_effect_idempotency_suite.py
git commit -m "test(idempotency): 全部 effect_* 节点的强制中断-恢复用例"
```

---

### Task 3: 铁律 1 恒等式断言与红灯登记

**Files:**
- Modify: `tests/test_effect_idempotency_suite.py`（追加）
- Modify: `openspec/changes/m1-job-profile-intake/tasks.md`（勾 4.4，订正过期注解）

**Interfaces:**
- Consumes: Task 2 的 `build_recipes`、`_open_crashing_connection`、`Recipe`
- Produces: 无新符号，只有用例

- [ ] **Step 1: 写失败的测试**

在 `tests/test_effect_idempotency_suite.py` 末尾追加：

```python
@pytest.mark.parametrize("node_name", sorted(EFFECT_NODE_MANIFEST))
def test_effect_log_count_equals_business_rows_per_thread(node_name, tmp_path):
    """
    ⭐ 工程铁律 1 的 reviewer 判据逐字落成断言：
    「每个 effect_* 节点的 effect_log 条数与其业务表行数按 thread 恒等」。

    做法：连续调用三次（同一 thread_id、同一 business_key），第二三次会命中
    effect_log 短路。三次之后 effect_log 恒为 1，业务事实恒为 rows_per_effect。

    ⚠️ 这条**不是** Task 2 主用例的重复。主用例证明的是"崩溃点两侧的原子性"，
    这一条证明的是"稳态下两个计数不会漂移"。前者防丢失，后者防重复——
    铁律 1 的两个方向各需要一条。
    """
    recipe = build_recipes(tmp_path)[node_name]
    conn = get_connection(str(tmp_path / f"{node_name}-steady.db"))
    init_schema(conn)
    conn.commit()
    recipe.seed(conn)
    rows_before = recipe.count_business_rows(conn)

    for _ in range(3):
        recipe.invoke(conn)

    effect_rows = conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE node_name = ? AND thread_id = ?",
        (node_name, recipe.thread_id),
    ).fetchone()[0]
    assert effect_rows == 1, f"{node_name}：同一幂等键调 3 次，effect_log 应恒为 1"
    assert recipe.count_business_rows(conn) == rows_before + recipe.rows_per_effect, (
        f"{node_name}：同一幂等键调 3 次，业务事实应恒为 {recipe.rows_per_effect} 份。{recipe.note}"
    )


def test_llm_call_is_replayed_when_the_crash_lands_before_commit(tmp_path):
    """
    ⚠️ **观察项 O-1 的固化，不是红灯。**

    `effect_generate_and_persist_jd` 的副作用有两半：一半在事务里（写
    job_profile.profile_json），一半在事务外（一次真实、有成本的 LLM 调用）。
    崩溃落在提交之前时，事务那一半被正确丢弃，**LLM 那一半已经发生过了**，
    重放会再调一次——数据库状态依旧精确一次（这正是铁律 1 要保的），
    但**账单是两次**。

    这条用例把这个事实钉住，让它成为一个**已知且被度量**的性质，而不是
    某天有人看账单时才发现的意外。⛔ 本单元不修它（只新增 tests/）；
    要修得让 LLM 调用与写库拆成两个节点（先 compute 出文本并落草稿，
    再 effect 写正式列），那是另一个交付单元的事。
    """
    _CountingGateway.calls = 0
    recipe = build_recipes(tmp_path)["effect_generate_and_persist_jd"]
    db_path = str(tmp_path / "jd-cost.db")

    conn = _open_crashing_connection(db_path)
    init_schema(conn)
    conn.commit()
    recipe.seed(conn)

    with pytest.raises(RuntimeError, match="simulated crash"):
        recipe.invoke(conn)
    conn.close()

    fresh = get_connection(db_path)
    recipe.invoke(fresh)

    assert recipe.count_business_rows(fresh) == 1, "数据库状态必须精确一次"
    assert _CountingGateway.calls == 2, (
        "观察项 O-1：崩溃在提交之前时 LLM 会被调用两次（第一次的结果随事务丢弃）。"
        "若这里变成 1，说明有人把 LLM 调用挪到了事务之外或加了缓存——那是好事，"
        "请更新本用例并从计划的「红灯与观察项」里摘掉 O-1"
    )


def test_outbound_audit_has_no_sqlite_business_row_by_design(tmp_path):
    """
    ⚠️ **观察项 O-2 的固化。** `effect_record_outbound_audit` 的
    `rows_per_effect = 0` 是全表唯一的 0，必须有一条用例说明它是设计如此，
    否则下一个读这份注册表的人只会当成"这条配方没写完"。
    """
    recipe = build_recipes(tmp_path)["effect_record_outbound_audit"]
    assert recipe.rows_per_effect == 0
    assert "显式声明的例外" in recipe.note

    conn = get_connection(str(tmp_path / "audit.db"))
    init_schema(conn)
    conn.commit()
    recipe.invoke(conn)
    assert recipe.count_business_rows(conn) == 0
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM effect_log WHERE node_name = 'effect_record_outbound_audit'"
        ).fetchone()[0]
        == 1
    ), "SQLite 里没有业务行，但幂等保护必须照常生效——重复留痕由 effect_log 挡住"
```

- [ ] **Step 2: 跑测试**

Run: `python -m pytest tests/test_effect_idempotency_suite.py -v`
Expected: 28 passed（5 守卫 + 10 崩溃恢复 + 10 恒等式 + 3 观察项）

- [ ] **Step 3: 跑全量并确认覆盖数**

Run: `python -m pytest tests/ -q && grep -c 'EFFECT_NODE_MANIFEST' tests/test_effect_idempotency_suite.py`
Expected: 全量全绿；`EFFECT_NODE_MANIFEST` 出现次数 ≥ 4（定义 1 + 守卫 2 + 两处 parametrize）

- [ ] **Step 4: 回勾 WBS 并订正过期注解**

编辑 `openspec/changes/m1-job-profile-intake/tasks.md` 第 136 行起的 4.4 条目，改为：

```markdown
- [x] 4.4 **幂等专项测试**：对每个 `effect_*` 节点强制中断并恢复，断言副作用只发生一次 → **已实现**，见 `tests/test_effect_idempotency_suite.py`。当前 **10 个** `effect_*` 节点**全部覆盖**（清单由 AST 从 `app/` 现扫，与硬编码 `EFFECT_NODE_MANIFEST` 双向比对，新增节点漏测即变红）。中断落在"业务写已入事务、`effect_log` 已 INSERT、`commit()` 尚未落盘"那一刻，随后换全新连接确认什么都没落盘、再按同一 `thread_id`/`business_key` 重跑。
      ⚠️ 本条原注解写的"4 个 effect 节点里覆盖了 3 个"写于只有 4 个节点时，已过期，此次一并订正。
      ⚠️ 遗留观察项 O-1：`effect_generate_and_persist_jd` 在崩溃落于提交之前时，LLM 会被**真实调用两次**（数据库状态仍精确一次）。已由 `test_llm_call_is_replayed_when_the_crash_lands_before_commit` 固化度量，修复（把 LLM 调用与写库拆成 compute/effect 两个节点）属另一个交付单元。
```

- [ ] **Step 5: 提交**

```bash
git add tests/test_effect_idempotency_suite.py openspec/changes/m1-job-profile-intake/tasks.md
git commit -m "test(idempotency): 铁律1恒等式断言 + 观察项固化，回勾 4.4"
```

---

## 红灯与观察项

**红灯（R-*）**＝节点真的不幂等，必须登记、⛔ 本单元不修。
**观察项（O-*）**＝行为符合铁律但有代价或有例外，需要被度量而不是被修。

| 编号 | 节点 | 内容 | 处置 |
|---|---|---|---|
| O-1 | `effect_generate_and_persist_jd` | 副作用一半在事务里（写库）、一半在事务外（付费 LLM 调用）。崩溃落于提交前时数据库精确一次、**LLM 调用两次** | 由 `test_llm_call_is_replayed_when_the_crash_lands_before_commit` 固化。修法＝拆成 `compute_jd_text` + `effect_persist_jd` 两个节点，属另一交付单元 |
| O-2 | `effect_record_outbound_audit` | SQLite 业务行恒为 0（外发事件在 `analysis_run` 里没有真身，载体是 JSONL 镜像，且镜像 append 在 commit **之后**、本就不在事务里） | 设计如此。以 `rows_per_effect = 0` + `note` + 专用用例三处显式声明，⛔ 不当作漏测 |
| R-* | —— | run-build 执行中若有节点在 Task 2 Step 2 真红，按 Step 2 的处置写进这一行 | 加 `xfail(strict=True)` 并在 run-build 报告里单列 |

## 自查记录（写计划时已核对）

- [x] 任务标题为三级 `### Task N: `，共 3 个 —— `grep -c '^### Task ' <本文件>` == 3
- [x] 有 **Global Constraints** 段，内容与 `CLAUDE.md`「工程铁律」1/2、「合规红线」逐字一致
- [x] spec `job-profile-approval` 的 `### Requirement: 副作用幂等` 两个 Scenario 各有对应 Task：
      「编排引擎重跑节点」→ Task 2 主用例；「回调重复到达」→ Task 3 恒等式用例（同键连调三次）
- [x] 每个 Task 有确切文件路径、完整代码、确切命令与预期输出
- [x] 无 TBD / TODO / "适当处理错误" 类占位符
- [x] 前后 Task 的符号名一致：`EFFECT_NODE_MANIFEST` / `build_recipes` / `Recipe` / `_open_crashing_connection` 全程同名
- [x] 每个有副作用的动作独占一个 Task 步骤且带幂等键（铁律 1）——本单元只写测试，不新增副作用节点
- [x] 不涉及 AI 评分写入，`evidence_ref` 断言不适用（本单元无 `criterion_score` 路径）
- [ ] **端到端提取验证未做**：本单元的全部代码都直接落在现有仓库的 `tests/` 下、依赖现有 `app/` 与已装 venv，"提取到临时目录 + 独立 venv"反而测不到真实被测对象。改为在 Task 2 Step 4 / Task 3 Step 3 跑**仓库内全量** `pytest tests/ -q` 作为等效验证。⏸ 留步登记于此，供 reviewer 复核这个替代是否成立。
