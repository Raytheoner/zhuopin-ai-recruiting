# AI 留痕与外发门禁 · 交付单元 U1（数据层与配置位）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 `app/storage/db.py` 的 `SCHEMA` 追加 `analysis_run` / `criterion_score` / `pending_approval` 三张全新表（含铁律 4 的存储层 `CHECK`、状态限值、重复入队唯一索引与三条查询索引），并在 `app/config.py` 一次加齐留痕与外发门禁所需的配置键——其中候选人外发总开关落成**每次求值、可热改、不重启生效**的函数，**既有表一行不改、现有行为零变化**。

**Architecture:** 三张表全部走 `CREATE TABLE IF NOT EXISTS` 追加进 `SCHEMA` 常量末尾，**完全不碰 `_ADDED_COLUMNS` 那条 `ALTER TABLE` 加列路径**（`db.py:89-117`）——加列路径只服务"老库缺列"，新表不需要它，`.51` 上 `data/demo.db` 的 15 个真实 job 无任何数据迁移。配置侧新增三个键：`audit_jsonl_path`（U2/U3 消费）、`candidate_outbound_enabled`（基线值，默认关）、`candidate_outbound_switch_file`（热改通道）；对外只暴露一个 `is_candidate_outbound_enabled()` 函数，U4 按 tasks 4.5 的"支持传 callable"直接把这个**函数对象**接进门禁，任何一层读不出明确的"开"都返回 `False`。

**Tech Stack:** Python 3.14.6（`./venv`）· SQLite（标准库 `sqlite3`，WAL + `PRAGMA foreign_keys=ON`）· pydantic-settings · pytest 8.3.4 · **不引入任何新依赖**（`requirements.txt` / `pyproject.toml` diff 必须为空）

---

## Global Constraints

以下条目从 `CLAUDE.md`（2026-08-26 版）「工程铁律」「合规红线」「部署约束」、本变更包 `delivery-units.md` §2.U1 / §3.5 / §4、以及 OP-0826-D 指令 §2 **逐字复制**。**每个 Task 的验收隐含包含本节全部内容**，`subagent-driven-development` 会把这一段原样交给 reviewer 当注意力透镜。

### 本单元的头号约束（OP-0826-D §2 第 1 条，逐字）

> **`analysis_run` 的业务关联列与 rubric 列必须全部可空**：application_id / job_id / rubric_snapshot / system_fingerprint / token_usage 一律允许 NULL。
> 理由不是"以防万一"：U3 一旦把 RecorderAuditHook 接到 `_gateway_factory()` 上，**M1 现有的岗位画像采集调用会立刻开始写 analysis_run**，而采集期没有投递、没有 rubric。
> 任何一列 NOT NULL 都会在 U3 合并当天把 M1 的采集流程打挂。
> 只有 id / configured_model / prompt_version / temperature / input_hash / raw_response / created_at 是 NOT NULL。

**reviewer 的机械判据**：`PRAGMA table_info(analysis_run)` 中 `notnull=1` 的列集合**恰好等于**上面那七个，一个不多。这条由 Task 1 的 `test_analysis_run_notnull_set_is_exactly_the_seven_reproducibility_columns` 锁死——它断言的是**相等**而不是包含，多加一列 NOT NULL 会立刻变红。

### 本单元的第二条约束（OP-0826-D §2 第 2 条，逐字）

> **新表全部走 CREATE TABLE IF NOT EXISTS，⛔ 不碰 _ADDED_COLUMNS 那条加列路径**（app/storage/db.py:71-108）。三张表都是新的，.51 上 15 个真实 job 的既有表一行不改，无数据迁移。tasks 1.6 的回归测试就是这条的守护。

**reviewer 的机械判据（三条）**：

1. 本单元 diff 里 `_ADDED_COLUMNS` 元组**一个字节都不改**（Task 5 的 `test_audit_tables_never_enter_the_add_column_path` 断言其 table 集合恒为 `{"job_profile"}`）
2. 本单元 diff 里**不出现新的 `ALTER TABLE`**
3. 老库跑完 `init_schema()` 后，`effect_log` / `outbox` / `job` / `job_profile` 的列集合与行数与跑之前**完全相同**（Task 5 的 `test_existing_tables_and_rows_are_untouched_by_audit_schema` 用一个 dict 相等断言把四项一次锁死）

### 工程铁律（不可违背）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
   *为什么*：业务写失败而幂等记录成功 → 系统判定"已执行"→ 永不重试。**幂等本是防重复的保护，拆开事务后变成永久丢失的保证。** 实证：`.51` 现网 2026-08-10 与 08-12 各丢一轮 `outbox`（幂等记录已落），用户没收到回复且永远不会补发，见 `docs/findings/2026-08-13-sqlite-事务归属冲突.md` §8.5。

> **本单元与这条的关系**：U1 **不新增任何 `effect_*` 节点、不新增任何写入语句、不改 `idempotent_effect` 装饰器、不改 `effect_log` 表结构**——只加三张空表与三个配置键。但 U1 要为铁律 1 留好地基：`pending_approval` 的 `(thread_id, content_hash)` 唯一索引是 U5 幂等键 `{thread_id}:effect_enqueue_pending_approval:{content_hash}` 的**第二道防线**，两者粒度必须一致（见 Task 3 的偏离登记）。
> **reviewer 判据：本单元 diff 里不出现 `@idempotent_effect`、不出现 `INSERT INTO`（测试文件除外）、不出现 `conn.commit()` 的新增调用点。**

2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。

> **本单元与这条的关系**：`is_candidate_outbound_enabled()` 读文件、读环境变量，是**有外部依赖的读取**，但它不写任何东西，放在 `app/config.py` 而**不是** `app/agents/` 或 `app/outbound/gate.py` 里。U4 的 `compute_outbound_gate` 必须保持纯函数——开关值由调用方**以 callable 形式传入**，`compute_outbound_gate` 内部**不得** `import app.config`。这条在 U4 兑现，U1 只负责把接口做成能兑现的形状。

3. **所有 AI 评分必须持久化**：模型标识 + 模型版本 + prompt 版本 + temperature + 输入哈希 + rubric 快照 + 原始响应。

> **U1 是这条铁律的存储层落点**：`analysis_run` 的十四列就是这条的逐字兑现。注意"模型标识"与"模型版本"落成 `configured_model` / `response_model` **两个字段各自保存、不互相覆盖**（Task 1 的 `test_analysis_run_keeps_configured_and_response_model_apart`）。真正写值发生在 U2/U3。

4. **每条 `criterion_score` 必须有 `evidence_ref`**（回指简历原文或面试 turn 的 offset）。`evidence_ref` 为空不允许写入。

> **U1 是这条铁律从"应用层自觉"变成"数据库强制"的那一步**：`CHECK` 约束落在建表 DDL 里，**绕过应用层直接 `INSERT` 同样被拒**。Task 2 的参数化用例是直接执行 `INSERT`，不经任何应用层代码。
> **注意 trim 字符集**：SQLite 的单参 `trim()` 只剥空格，一个纯制表符的 `evidence_ref` 会通过。DDL 里必须显式列出空格/制表/换行/回车四个字符（见 Task 2 的偏离登记）。

5. **`temperature=0`；模型版本优先显式锁定**，禁止 `latest` 类别名。
   供应商不提供带版本号快照时（如 DeepSeek 公开 API 只有 `deepseek-chat` 这类会漂移的别名），**必须从 API 响应里取回实际的 `model` 字段并持久化**——配置里写的名字不算数，响应返回的才算。

> **本单元与这条的关系**：`analysis_run.temperature` 与 `configured_model` / `response_model` / `system_fingerprint` 三列是这条铁律的持久化位。`response_model` 与 `system_fingerprint` **必须可空**（供应商不返回时记空值、留痕照常写入，spec `Scenario: 供应商不返回部署指纹`），这与头号约束方向一致。U1 不改 `validate_model_version()`，`latest` 别名的拒绝逻辑已在 `app/config.py:26-30` 生效。

6. **企微回调先落库再处理**：不适用（本单元不接企微通道）。
7. **`langgraph >= 1.0.10`**：本单元不动依赖版本，`requirements.txt` diff 必须为空。

### 合规红线

- **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。审计断言：`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。

> **U1 的对应形态**：`pending_approval` 是这条红线在外发路径上的**结构性落点**——被拦下的草稿有地方存、有状态机、有 `confirmed_by`。U1 只建表，判定与接线在 U4/U5。`rejection_record` 表本单元**不建**（M2 才有，断言由 U6 以"表不存在即通过"的形式实现）。

- **禁止人脸/表情分析**。声学情绪信号（语速/停顿/静默）只展示给面试官，不进 `criterion_score`。

> **U1 不实现白名单**（`criterion_key` 白名单是 U3 的 3.4、断言是 U6 的 6.3）。U1 的 `criterion_score.criterion_key` 是自由文本列，**不要**在本单元加 `CHECK (criterion_key IN (...))`——白名单要集中在一处 Python 定义里，加维度是一行改动 + 一次 review（design 风险表最后一条）；把它写死进 DDL 会让加维度变成一次数据库迁移。

- **AI 生成的 JD、拒信、邀约须带标识**：U4 的 4.4 兑现，U1 不涉及。
- **模型全部走境内**、**绝不用历史录用结果做监督信号**：后者是 `analysis_run` 表注释必须写明的内容（spec `Requirement: 留痕数据的用途限制`），由 Task 1 的 `test_analysis_run_carries_no_training_use_marker` 机器校验。
- 主观描述不得进入硬门槛规则：本单元不涉及。

### 部署约束

4. **目标服务器是 Windows，没有 Docker**。部署形态 = Python venv + Windows 计划任务 + 防火墙规则 + scp 推送。

> **本单元与这条的关系有两处**：
> ① 三张新表的 DDL 是 `CREATE TABLE IF NOT EXISTS`，对既有库幂等、无回填，与 M1 的 `ALTER TABLE ADD COLUMN` 互不干扰，**可同批推 `.51`**（`delivery-units.md` §3.2 末段）。
> ② 外发总开关的热改通道之所以必须是**文件**而不只是环境变量：`.51` 是 Windows 计划任务拉起的单进程，改环境变量必须重启服务，而机器上还跑着另外 7 个服务。见下方 §3.5 拍板结论。

5. **M2 起处理真实简历前**，必须具备可识别到人的登录 + 简历访问留痕。

> `pending_approval.confirmed_by` 现阶段**不可信**（鉴权是空壳，`AuthContext.user_id` 恒为 `None`，design D7）。表注释必须写明这一点，避免后来者误以为现在的 `confirmed_by` 已经可审计。SSO 落地后同一字段变可信，表结构不改。技术债登记是 U7 的 7.5，**U1 不重复登记**。

### 跨单元接口约定（`delivery-units.md` §4，第 1、5 条逐字）

1. **两个配置键在 U1 一次加齐**（审计 JSONL 路径 + `CANDIDATE_OUTBOUND_ENABLED`），U3 与 U4 只读不写 `app/config.py`。否则两个单元共写同一文件，U2 ∥ U4、U3 ∥ U4 的并行全部作废。
5. **`analysis_run` 的业务关联列与 rubric 列全部可空**（U1），否则 U3 接线当天打挂 M1 的采集流程。理由见 §2.U1。

### 外发总开关的形态（`delivery-units.md` §3.5，Shao Peishen 2026-08-26 拍板，逐字）

> 1. `CANDIDATE_OUTBOUND_ENABLED` **必须每次外发时求值**。⛔ 禁止在模块导入期、`__init__` 里、或任何单例上把它读成一个常量。tasks 4.5 已写"支持传 callable"，用那条路。守护测试：运行中改值后**不重启**，下一次外发要立刻按新值走。
> 2. **合并时保持关闭**（全拦），观察拦截留痕符合预期后再开，与 design 迁移计划第 4 步一致。

**reviewer 的机械判据（两条）**：

1. `grep -rn "candidate_outbound_enabled" app/` 在 `app/config.py` 之外**不得出现任何模块级赋值**（形如 `ENABLED = ...`）。U1 之后，唯一合法的消费方式是把 `is_candidate_outbound_enabled` **函数对象**传下去，在真正要外发的那一刻才带括号调用。
2. `is_candidate_outbound_enabled` 上**不得**有 `@lru_cache` / `@cache`（Task 4 的 `test_switch_function_is_not_memoised` 断言它没有 `cache_clear` / `cache_info` 属性）。加缓存是"看起来无害的优化"，但它让热改彻底失效，而所有既有用例照样全绿。

### 本变更包的三条硬边界（全部单元）

不新增 `zhuopin_platform` 依赖、不跨仓库 import、不拷贝参考文件。**U1 的验证方式**：`git diff` 里 `requirements.txt` 与 `pyproject.toml` 必须为空，`grep -rn "zhuopin_platform" app/ tests/` 零命中。CI 化是 U7 的 7.1/7.2。

---

## 开工前置（必做，5 分钟）

- [ ] **rebase 到最新 main**（`delivery-units.md` §4 约定 8）。本包与 `m1-intake-quality-fixes` 同期在跑。

```bash
git pull --rebase origin main
```

- [ ] **确认 U1 的三个触碰文件此刻没有别人的改动**。M1 剩余单元（B/D/E/F/G）按 §3.2 的逐文件对照**不碰** `app/storage/db.py` 与 `app/config.py`，理论上零冲突；实际核一遍：

```bash
git log --oneline -5 -- app/storage/db.py app/config.py
```

- [ ] **取基线**：全量测试必须全绿，记下数字。

```bash
./venv/bin/python -m pytest -q 2>&1 | tail -2
```

预期：`222 passed`（2026-08-26 实测）。本单元合并后应为 `275 passed`（新增 53 个用例）。

- [ ] **确认锚点行号仍然对得上**（rebase 后可能漂移，以内容为准而非行号）：

```bash
grep -n "^SCHEMA = \|_ADDED_COLUMNS: tuple\|def init_schema" app/storage/db.py
grep -n "log_max_bytes\|def get_settings" app/config.py
```

---

## 明确的范围边界（U1 **不做**什么）

| 不做 | 归属 |
|---|---|
| `app/audit/` 目录、`DecisionEvent`、`AuditSink`、`JsonlChainSink`、`verify_chain()` | U2 |
| 把 `RecorderAuditHook` 接到 `app/main.py:_gateway_factory()` | U3 |
| `criterion_key` 白名单的 Python 定义与拒写逻辑 | U3（3.4） |
| `app/outbound/`、`compute_outbound_gate`、`GateDecision` | U4 |
| `queue.py`、两个新 `effect_*` 节点、外发路径分流 | U5 |
| `assertions.py`、对账查询、CI 接入 | U6 |
| 运维文档（JSONL 路径与备份、开关流程）、技术债登记 | U7（7.3/7.5/7.6） |
| 删 `job_profile.turn_started_at` / `llm_latency_ms` 两列 | **不在本包**——改 `.51` 现网表结构属生产决定（不可代），U3 只负责把技术债标为"触发条件已满足" |

**U1 合并后系统的可观察行为必须与合并前完全一致**：三张表是空的、没有任何代码读写它们、`is_candidate_outbound_enabled()` 没有任何调用方。这是本单元"可独立合并"的定义。

---

## File Structure

| 文件 | 动作 | 责任 |
|---|---|---|
| `app/storage/db.py` | 修改（只在 `SCHEMA` 字符串末尾追加） | 三张表 DDL + 四条索引。**不改任何函数体、不改 `_ADDED_COLUMNS`** |
| `app/config.py` | 修改 | 三个配置键 + `_TRUTHY` + `_as_switch()` + `is_candidate_outbound_enabled()` |
| `tests/test_db_audit_schema.py` | 新建（Task 1 建，Task 2/3 追加） | 三张表的结构与约束断言 |
| `tests/test_config_audit_and_outbound.py` | 新建（Task 4） | 配置键默认值 + 总开关的每次求值/热改/fail-closed |
| `tests/test_db_migration.py` | 修改（只追加，Task 5） | 老库回归：既有表零变化、幂等重跑、加列路径不被污染 |

**为什么测试分三个文件而不是全塞进 `tests/test_db.py`**：`test_db.py` 测的是 M1 既有表，混进来会让"U1 有没有破坏既有表"这个问题的答案散在同一个文件里；`test_db_migration.py` 已经持有 `_legacy_db()` 这个"模拟 `.51` 老库"的夹具，回归守护必须复用它而不是另起一套。

---

### Task 1: `analysis_run` 表与可空性守护

**Files:**
- Modify: `app/storage/db.py`（`SCHEMA` 字符串末尾，`outbox` 建表语句之后、结尾 `"""` 之前）
- Test: `tests/test_db_audit_schema.py`（新建）

**Interfaces:**
- Consumes: `app.storage.db.get_connection(db_path) -> sqlite3.Connection`、`app.storage.db.init_schema(conn) -> None`（均已存在，签名不改）
- Produces: 表 `analysis_run`，十四列——`id` / `application_id` / `job_id` / `configured_model` / `response_model` / `system_fingerprint` / `prompt_version` / `temperature` / `input_hash` / `rubric_snapshot` / `raw_response` / `token_usage` / `latency_ms` / `created_at`；索引 `idx_analysis_run_application`。U2 的 `SqliteSink.write` 按这十四列写入，`id` 由调用方以 `{thread_id}:{node}:{input_hash}` 生成（tasks 2.2），**U1 不生成 id、不加 AUTOINCREMENT**。

- [ ] **Step 1: Write the failing test**

新建 `tests/test_db_audit_schema.py`：

```python
import sqlite3

import pytest

from app.storage.db import get_connection, init_schema


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "audit.db"))
    init_schema(c)
    return c


def _notnull_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})") if row[3]}


def _nullable_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})") if not row[3]}


def _table_sql(conn: sqlite3.Connection, name: str) -> str:
    return conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()[0]


def _insert_run(conn: sqlite3.Connection, run_id: str = "run-1", **overrides) -> None:
    row = {
        "id": run_id,
        "application_id": None,
        "job_id": None,
        "configured_model": "deepseek-chat",
        "response_model": "deepseek-chat-241226",
        "system_fingerprint": "fp_abc",
        "prompt_version": "score-v1",
        "temperature": 0.0,
        "input_hash": "sha256:deadbeef",
        "rubric_snapshot": '{"criteria": []}',
        "raw_response": '{"score": 3}',
        "token_usage": '{"total_tokens": 12}',
        "latency_ms": 812.5,
    }
    row.update(overrides)
    conn.execute(
        "INSERT INTO analysis_run (id, application_id, job_id, configured_model, "
        "response_model, system_fingerprint, prompt_version, temperature, input_hash, "
        "rubric_snapshot, raw_response, token_usage, latency_ms) "
        "VALUES (:id, :application_id, :job_id, :configured_model, :response_model, "
        ":system_fingerprint, :prompt_version, :temperature, :input_hash, "
        ":rubric_snapshot, :raw_response, :token_usage, :latency_ms)",
        row,
    )
    conn.commit()


# ── analysis_run ────────────────────────────────────────────────────────


def test_audit_tables_exist(conn):
    tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"analysis_run", "criterion_score", "pending_approval"} <= tables


def test_analysis_run_notnull_set_is_exactly_the_seven_reproducibility_columns(conn):
    """
    U1 的头号约束的机器判据。这七列是"复现一次调用"的最小集合，其余全部可空。

    多一列 NOT NULL，U3 把 RecorderAuditHook 接到 _gateway_factory() 的当天，
    M1 的岗位画像采集就会开始写这张表并撞上约束——采集期没有投递、没有 rubric。
    """
    assert _notnull_columns(conn, "analysis_run") == {
        "id",
        "configured_model",
        "prompt_version",
        "temperature",
        "input_hash",
        "raw_response",
        "created_at",
    }


def test_analysis_run_business_and_rubric_columns_are_nullable(conn):
    assert _nullable_columns(conn, "analysis_run") == {
        "application_id",
        "job_id",
        "response_model",
        "system_fingerprint",
        "rubric_snapshot",
        "token_usage",
        "latency_ms",
    }


def test_analysis_run_accepts_intake_shaped_row_without_application_or_rubric(conn):
    """U3 合并当天 M1 采集流程会写的就是这个形状：没投递、没 rubric、没指纹。"""
    _insert_run(
        conn,
        "run-intake",
        application_id=None,
        job_id=None,
        rubric_snapshot=None,
        system_fingerprint=None,
        token_usage=None,
        latency_ms=None,
        response_model=None,
    )

    row = conn.execute(
        "SELECT application_id, rubric_snapshot, system_fingerprint, created_at "
        "FROM analysis_run WHERE id='run-intake'"
    ).fetchone()
    assert row[0] is None
    assert row[1] is None
    assert row[2] is None
    assert row[3] is not None  # created_at 由数据库补，调用方不必给


def test_analysis_run_keeps_configured_and_response_model_apart(conn):
    """铁律 5：配置侧别名与响应实际返回的标识分两字段，不互相覆盖。"""
    _insert_run(
        conn,
        "run-models",
        configured_model="deepseek-chat",
        response_model="deepseek-chat-241226",
    )

    row = conn.execute(
        "SELECT configured_model, response_model FROM analysis_run WHERE id='run-models'"
    ).fetchone()
    assert row == ("deepseek-chat", "deepseek-chat-241226")


def test_analysis_run_carries_no_training_use_marker(conn):
    """
    spec「留痕数据的用途限制」：开发者查看表定义时必须看得到禁止训练的标注。
    注释写在 CREATE TABLE 的括号内部，才会被 sqlite_master.sql 保留下来——
    写在语句外面的注释 sqlite3 .schema 看不到，等于没写。
    """
    sql = _table_sql(conn, "analysis_run")
    assert "禁止用作任何模型的训练" in sql
    assert "Amazon 2018" in sql


def test_analysis_run_has_application_index(conn):
    indexes = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }
    assert "idx_analysis_run_application" in indexes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_db_audit_schema.py -q`
Expected: FAIL —— `sqlite3.OperationalError: no such table: analysis_run`（`test_audit_tables_exist` 则是 `AssertionError`）

- [ ] **Step 3: Write minimal implementation**

在 `app/storage/db.py` 的 `SCHEMA` 字符串里，`outbox` 建表语句的 `);` 之后、结尾的 `"""` 之前追加：

```sql
-- ─────────────────────────────────────────────────────────────────────────
-- 以下三张表属变更包 ai-audit-trail-and-outbound-gate（交付单元 U1）。
-- 三张都是新表，全部走 CREATE TABLE IF NOT EXISTS，**不进 _ADDED_COLUMNS**：
-- 加列路径只服务"老库缺列"这一种情况，新表不需要它。.51 上 data/demo.db 的
-- 15 个真实 job 与既有表一行不改，无数据迁移。
-- ─────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS analysis_run (
    -- ⚠️ 审计资产：本表内容禁止用作任何模型的训练、微调或调优输入。
    -- 理由：历史评分与录用结果携带既有偏见，拿它当监督信号会把偏见放大并
    -- 固化（Amazon 2018 教训，见 CLAUDE.md 合规红线「绝不用历史录用结果做
    -- 监督信号」）。本表只服务两件事：PIPL 第 24 条说明权（"这条评分是哪个
    -- 模型、哪个版本、按哪份 rubric 打的"）与 CI 里的合规断言。
    --
    -- 可空性是刻意设计，不是偷懒：业务关联列与 rubric 列一律允许 NULL。
    -- U3 把 RecorderAuditHook 接到 app/main.py:_gateway_factory() 之后，M1
    -- 现有的岗位画像采集调用会立刻开始写本表，而采集期没有投递、没有 rubric。
    -- 任何一列 NOT NULL 都会在 U3 合并当天把 M1 的采集流程打挂。
    id TEXT PRIMARY KEY NOT NULL,
    application_id TEXT,
    job_id TEXT,
    configured_model TEXT NOT NULL,
    response_model TEXT,
    system_fingerprint TEXT,
    prompt_version TEXT NOT NULL,
    temperature REAL NOT NULL,
    input_hash TEXT NOT NULL,
    rubric_snapshot TEXT,
    raw_response TEXT NOT NULL,
    token_usage TEXT,
    latency_ms REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_analysis_run_application
    ON analysis_run (application_id);
```

**三个容易做错的点**：

1. `id TEXT PRIMARY KEY` **必须显式再写一次 `NOT NULL`**。SQLite 有个历史遗留行为：`TEXT PRIMARY KEY` 列**允许 NULL**（只有 `INTEGER PRIMARY KEY` 例外），`PRAGMA table_info` 会报 `notnull=0`。不写的话 Step 1 的两个集合相等断言会红。
2. 表注释写在**括号内部**。`sqlite_master.sql` 保存的是 `CREATE` 关键字之后的原文，写在语句外面的注释不会被保存，`sqlite3 .schema` 看不到，等于没写——`test_analysis_run_carries_no_training_use_marker` 就是这条的守护。
3. `created_at` 用 `DEFAULT (datetime('now'))` 是**可以的**——这是 `CREATE TABLE`，不是 `ALTER TABLE ADD COLUMN`。`db.py:83-85` 那条"DDL 片段里的 DEFAULT 必须是常量"的注释只约束 `_ADDED_COLUMNS`。

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_db_audit_schema.py -q`
Expected: `6 passed, 1 failed`——`test_audit_tables_exist` 仍会 FAIL，因为另外两张表还没建。这是预期的，它在 Task 3 之后才转绿。

改用只跑 analysis_run 相关用例确认本 Task 的交付：

Run: `./venv/bin/python -m pytest tests/test_db_audit_schema.py -q -k "analysis_run"`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add app/storage/db.py tests/test_db_audit_schema.py
git commit -m "feat(audit): 建 analysis_run 表，业务关联列与 rubric 列全部可空"
```

⛔ 只 add 上面两个路径。`git status` 里出现别人的改动是正常的，不要顺手提交。

---

### Task 2: `criterion_score` 表与 `evidence_ref` 的存储层强制

**Files:**
- Modify: `app/storage/db.py`（`SCHEMA` 字符串，`idx_analysis_run_application` 之后）
- Test: `tests/test_db_audit_schema.py`（追加在 Task 1 的用例之后）

**Interfaces:**
- Consumes: Task 1 的 `analysis_run` 表与 `tests/test_db_audit_schema.py` 里的 `conn` 夹具、`_insert_run()` / `_notnull_columns()` 辅助函数
- Produces: 表 `criterion_score`——`id` / `analysis_run_id`（外键指向 `analysis_run(id)`）/ `criterion_key` / `score` / `evidence_ref`（带 `CHECK`）/ `created_at`；索引 `idx_criterion_score_run`。U2 的 `SqliteSink.write` 按这六列写入；U6 的断言二/断言三查这张表。

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_db_audit_schema.py`：

```python
# ── criterion_score ─────────────────────────────────────────────────────


def test_criterion_score_accepts_row_with_evidence(conn):
    _insert_run(conn)
    conn.execute(
        "INSERT INTO criterion_score (id, analysis_run_id, criterion_key, score, evidence_ref) "
        "VALUES ('cs-1', 'run-1', 'embedded_c', 4.0, 'resume:cand-7#120-186')"
    )
    conn.commit()

    row = conn.execute(
        "SELECT analysis_run_id, evidence_ref FROM criterion_score WHERE id='cs-1'"
    ).fetchone()
    assert row == ("run-1", "resume:cand-7#120-186")


@pytest.mark.parametrize(
    "evidence",
    [
        pytest.param(None, id="null"),
        pytest.param("", id="empty"),
        pytest.param("   ", id="spaces"),
        pytest.param("\t", id="tab"),
        pytest.param("\n", id="newline"),
        pytest.param(" \t\r\n ", id="mixed-whitespace"),
    ],
)
def test_criterion_score_rejects_blank_evidence_at_storage_layer(conn, evidence):
    """
    铁律 4 由存储层强制：这里是**直接执行 INSERT**，完全绕过任何应用层校验，
    照样必须被拒。纯制表符/换行那几个参数是 trim 字符集的守护——单参 trim()
    只剥空格，写成 trim(evidence_ref) 的话这几条会通过，铁律 4 就有了缺口。
    """
    _insert_run(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO criterion_score (id, analysis_run_id, criterion_key, score, evidence_ref) "
            "VALUES ('cs-blank', 'run-1', 'embedded_c', 4.0, ?)",
            (evidence,),
        )
        conn.commit()
    conn.rollback()


def test_criterion_score_requires_existing_analysis_run(conn):
    """评分与调用快照双向可追溯：外键保证不会出现指向空气的评分项。"""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO criterion_score (id, analysis_run_id, criterion_key, score, evidence_ref) "
            "VALUES ('cs-orphan', 'no-such-run', 'embedded_c', 4.0, 'resume:x#1-2')"
        )
        conn.commit()
    conn.rollback()


def test_criterion_score_is_reachable_from_its_analysis_run(conn):
    _insert_run(conn, "run-join", application_id="app-9")
    conn.execute(
        "INSERT INTO criterion_score (id, analysis_run_id, criterion_key, score, evidence_ref) "
        "VALUES ('cs-join', 'run-join', 'autosar', 3.0, 'resume:cand-9#4-40')"
    )
    conn.commit()

    row = conn.execute(
        "SELECT r.application_id, s.criterion_key FROM criterion_score s "
        "JOIN analysis_run r ON r.id = s.analysis_run_id WHERE s.id='cs-join'"
    ).fetchone()
    assert row == ("app-9", "autosar")


def test_criterion_score_has_run_index(conn):
    indexes = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }
    assert "idx_criterion_score_run" in indexes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_db_audit_schema.py -q -k "criterion_score"`
Expected: FAIL —— `sqlite3.OperationalError: no such table: criterion_score`

- [ ] **Step 3: Write minimal implementation**

在 `SCHEMA` 里 `idx_analysis_run_application` 之后追加：

```sql
CREATE TABLE IF NOT EXISTS criterion_score (
    -- ⚠️ 审计资产：与 analysis_run 同，禁止用作训练/微调/调优输入。
    --
    -- evidence_ref 的 CHECK 是工程铁律 4 的存储层落点：证据回指为空的评分项
    -- 不允许写入，且这条**由数据库强制**——绕过应用层直接 INSERT 同样被拒。
    -- trim 的第二参数显式列出空格/制表/换行/回车：SQLite 的单参 trim() 只剥
    -- 空格，只写 trim(evidence_ref) 的话一个纯制表符的 evidence_ref 会通过，
    -- 那就等于铁律 4 有一个静默缺口。
    id TEXT PRIMARY KEY NOT NULL,
    analysis_run_id TEXT NOT NULL REFERENCES analysis_run(id),
    criterion_key TEXT NOT NULL,
    score REAL NOT NULL,
    evidence_ref TEXT NOT NULL CHECK (
        evidence_ref IS NOT NULL
        AND trim(evidence_ref, ' ' || char(9) || char(10) || char(13)) != ''
    ),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_criterion_score_run
    ON criterion_score (analysis_run_id);
```

**⚠️ 相对 `tasks.md` 1.2 的偏离（已登记，需 reviewer 确认）**：tasks 1.2 的字面是 `CHECK (evidence_ref IS NOT NULL AND trim(evidence_ref) != '')`。本计划**加强**为带字符集的 `trim(evidence_ref, ' ' || char(9) || char(10) || char(13))`。理由：SQLite 的单参 `trim()` **只剥空格**，`evidence_ref = "\t"` 会通过字面版的 `CHECK`，铁律 4 就有一个静默缺口。方向是更严，不是更松。

**外键为什么能生效**：`get_connection()` 已经 `PRAGMA foreign_keys = ON`（`db.py:141`）。若哪天有人去掉那行，`test_criterion_score_requires_existing_analysis_run` 会立刻变红——它顺带成了那条 PRAGMA 的守护。

**⛔ 不要给 `criterion_key` 加 `CHECK (criterion_key IN (...))`**：白名单是 U3 的 3.4，集中在一处 Python 定义里。写死进 DDL 会让"加一个评分维度"变成一次数据库迁移。

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_db_audit_schema.py -q -k "criterion_score"`
Expected: `10 passed`（4 个用例 + 参数化的 6 条）

- [ ] **Step 5: Commit**

```bash
git add app/storage/db.py tests/test_db_audit_schema.py
git commit -m "feat(audit): 建 criterion_score 表，evidence_ref 非空由 CHECK 强制"
```

---

### Task 3: `pending_approval` 表与重复入队防线

**Files:**
- Modify: `app/storage/db.py`（`SCHEMA` 字符串，`idx_criterion_score_run` 之后）
- Test: `tests/test_db_audit_schema.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `conn` 夹具与 `_notnull_columns()`
- Produces: 表 `pending_approval`——`id` / `thread_id` / `message_type`（可空）/ `recipient`（可空）/ `payload_json` / `blocked_reason` / `content_hash` / `status`（默认 `pending`，限值 `pending`/`approved`/`abandoned`）/ `confirmed_by`（可空）/ `enqueued_at` / `resolved_at`（可空）；唯一索引 `idx_pending_approval_content` 建在 **`(thread_id, content_hash)`**；索引 `idx_pending_approval_status`。U5 的 `queue.py` 按这十一列读写，`approve()` 改 `status` 并回填 `confirmed_by` / `resolved_at`（**不 DELETE**）。

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_db_audit_schema.py`：

```python
# ── pending_approval ────────────────────────────────────────────────────


def _enqueue(conn: sqlite3.Connection, row_id: str, content_hash: str, **overrides) -> None:
    row = {
        "id": row_id,
        "thread_id": "job-1",
        "message_type": "rejection_letter",
        "recipient": "cand-7",
        "payload_json": '{"body": "..."}',
        "blocked_reason": "等待人工确认",
        "content_hash": content_hash,
    }
    row.update(overrides)
    conn.execute(
        "INSERT INTO pending_approval (id, thread_id, message_type, recipient, "
        "payload_json, blocked_reason, content_hash) "
        "VALUES (:id, :thread_id, :message_type, :recipient, :payload_json, "
        ":blocked_reason, :content_hash)",
        row,
    )
    conn.commit()


def test_pending_approval_defaults_to_pending(conn):
    _enqueue(conn, "pa-1", "hash-1")

    row = conn.execute(
        "SELECT status, confirmed_by, resolved_at, enqueued_at FROM pending_approval WHERE id='pa-1'"
    ).fetchone()
    assert row[0] == "pending"
    assert row[1] is None
    assert row[2] is None
    assert row[3] is not None


@pytest.mark.parametrize("status", ["pending", "approved", "abandoned"])
def test_pending_approval_accepts_the_three_legal_states(conn, status):
    _enqueue(conn, f"pa-{status}", f"hash-{status}")
    conn.execute("UPDATE pending_approval SET status=? WHERE id=?", (status, f"pa-{status}"))
    conn.commit()

    assert conn.execute(
        "SELECT status FROM pending_approval WHERE id=?", (f"pa-{status}",)
    ).fetchone()[0] == status


@pytest.mark.parametrize("status", ["sent", "PENDING", "", "deleted"])
def test_pending_approval_rejects_illegal_status(conn, status):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO pending_approval (id, thread_id, message_type, recipient, "
            "payload_json, blocked_reason, content_hash, status) "
            "VALUES ('pa-bad', 'job-1', 'rejection_letter', 'cand-7', '{}', 'x', 'h-bad', ?)",
            (status,),
        )
        conn.commit()
    conn.rollback()


def test_pending_approval_rejects_duplicate_content_in_same_thread(conn):
    """重复入队的第二道防线（第一道是 U5 的 idempotent_effect）。"""
    _enqueue(conn, "pa-a", "same-hash")
    with pytest.raises(sqlite3.IntegrityError):
        _enqueue(conn, "pa-b", "same-hash")
    conn.rollback()


def test_pending_approval_allows_same_content_in_different_threads(conn):
    """
    唯一索引按 (thread_id, content_hash)，不是单列 content_hash——粒度与 U5 的
    幂等键 {thread_id}:effect_enqueue_pending_approval:{content_hash} 一致。
    单列唯一会让两个不同 thread 的同内容草稿撞 IntegrityError，把"拦下来排队"
    变成异常。
    """
    _enqueue(conn, "pa-t1", "same-hash", thread_id="job-1")
    _enqueue(conn, "pa-t2", "same-hash", thread_id="job-2")

    assert conn.execute(
        "SELECT count(*) FROM pending_approval WHERE content_hash='same-hash'"
    ).fetchone()[0] == 2


def test_pending_approval_accepts_malformed_draft_with_unknown_type_and_recipient(conn):
    """
    fail-closed 的一部分：草稿被拦下的常见原因正是这些字段缺失。message_type
    或 recipient 设成 NOT NULL，就会把"拦下一条畸形消息"变成 IntegrityError，
    异常穿透到调用方 → 一个 except 就是 fail-open。
    """
    _enqueue(conn, "pa-weird", "hash-weird", message_type=None, recipient=None)

    row = conn.execute(
        "SELECT message_type, recipient, status, blocked_reason FROM pending_approval WHERE id='pa-weird'"
    ).fetchone()
    assert row[0] is None
    assert row[1] is None
    assert row[2] == "pending"
    assert row[3] == "等待人工确认"


def test_pending_approval_notnull_set(conn):
    assert _notnull_columns(conn, "pending_approval") == {
        "id",
        "thread_id",
        "payload_json",
        "blocked_reason",
        "content_hash",
        "status",
        "enqueued_at",
    }


def test_pending_approval_has_status_index(conn):
    indexes = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }
    assert "idx_pending_approval_status" in indexes


def test_pending_approval_is_not_outbox(conn):
    """design D5：两张表各自独立，读错表必须是 no such table/column 级的显性错误。"""
    outbox_columns = {row[1] for row in conn.execute("PRAGMA table_info(outbox)")}
    assert "status" not in outbox_columns
    assert "confirmed_by" not in outbox_columns
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_db_audit_schema.py -q -k "pending_approval"`
Expected: FAIL —— `sqlite3.OperationalError: no such table: pending_approval`

- [ ] **Step 3: Write minimal implementation**

在 `SCHEMA` 里 `idx_criterion_score_run` 之后追加（这是 `SCHEMA` 的最后一段，其后就是结尾的 `"""`）：

```sql
CREATE TABLE IF NOT EXISTS pending_approval (
    -- 被门禁拦下、等人工放行的候选人外发草稿。**不复用 outbox**：outbox 的
    -- 语义是"已决定要投递的消息"，本表的语义相反（"尚未获批、可能永远不发"）。
    -- 合表就要求每个读 outbox 的地方都加状态过滤，漏一处 = 未审批的拒信被发
    -- 出去（design D5）。
    --
    -- message_type / recipient 可空是刻意的：草稿被拦下的常见原因**正是**这
    -- 些字段缺失或未知（fail-closed）。把它们设成 NOT NULL，会让"拦下一条畸
    -- 形消息"从入队变成 IntegrityError——异常穿透到调用方，一个 except 就是
    -- fail-open。可空性在这里是 fail-closed 的一部分。
    --
    -- confirmed_by 现阶段不可信：鉴权是空壳（AuthContext.user_id 恒为 None），
    -- 值只能由调用方传入。SSO 落地后同一字段变可信，表结构不改（design D7）。
    id TEXT PRIMARY KEY NOT NULL,
    thread_id TEXT NOT NULL,
    message_type TEXT,
    recipient TEXT,
    payload_json TEXT NOT NULL,
    blocked_reason TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'abandoned')),
    confirmed_by TEXT,
    enqueued_at TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT
);

-- 重复入队的第二道防线（第一道是 U5 的 idempotent_effect）。按
-- (thread_id, content_hash) 而不是单列 content_hash：U5 的幂等键是
-- {thread_id}:effect_enqueue_pending_approval:{content_hash}，两道防线的
-- 粒度必须一致；单列唯一会让两个不同 thread 的同内容草稿撞上 IntegrityError。
CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_approval_content
    ON pending_approval (thread_id, content_hash);

CREATE INDEX IF NOT EXISTS idx_pending_approval_status
    ON pending_approval (status);
```

**⚠️ 相对 `tasks.md` 1.3 的两处偏离（已登记，需 reviewer 确认）**：

1. **唯一索引建在 `(thread_id, content_hash)`，不是单列 `content_hash`。** tasks 1.3 的字面是"`content_hash` 加唯一索引"。改成两列的理由：U5 的幂等键（tasks 5.3）是 `{thread_id}:effect_enqueue_pending_approval:{content_hash}`，本来就是 thread 内唯一；单列全局唯一会让两个不同 thread 的同内容草稿在入队时撞 `IntegrityError`，把"拦下来排队"变成异常穿透——那正是 fail-closed 最怕的形状。**U5 的实现必须按这个粒度写**。
2. **`message_type` / `recipient` 可空。** tasks 1.3 平铺列出这两列、没写可空性。设成 NOT NULL 会与 fail-closed 直接冲突（理由见表注释与上面的测试 docstring），方向与本单元头号约束一致：审计/队列表上多一个 NOT NULL，就多一个把"记录异常"变成"进程异常"的地方。

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_db_audit_schema.py -q`
Expected: `31 passed`（此时 `test_audit_tables_exist` 也转绿，三张表齐了）

- [ ] **Step 5: Commit**

```bash
git add app/storage/db.py tests/test_db_audit_schema.py
git commit -m "feat(outbound): 建 pending_approval 表，状态限值与重复入队唯一索引"
```

---

### Task 4: 配置位——审计 JSONL 路径与外发总开关（每次求值、可热改）

**Files:**
- Modify: `app/config.py`
- Test: `tests/test_config_audit_and_outbound.py`（新建）

**Interfaces:**
- Consumes: `app.config.Settings`、`app.config.get_settings`（已存在，`get_settings` 带 `@lru_cache`，本单元**不改这个装饰器**）
- Produces:
  - `Settings.audit_jsonl_path: str = "data/audit/decisions.jsonl"` —— U2 的 `JsonlChainSink` 与 U3 的装配处消费
  - `Settings.candidate_outbound_enabled: bool = False` —— **基线值，业务代码不得直接读**
  - `Settings.candidate_outbound_switch_file: str = "data/candidate_outbound.switch"` —— 热改通道
  - `app.config.is_candidate_outbound_enabled() -> bool` —— **U4/U5 唯一合法的消费入口**。U4 按 tasks 4.5 的"支持传 callable"接收**函数对象本身**（`compute_outbound_gate(message, outbound_enabled=is_candidate_outbound_enabled)`），在真正要外发的那一刻才求值

- [ ] **Step 1: Write the failing test**

新建 `tests/test_config_audit_and_outbound.py`：

```python
import pytest

from app.config import Settings, get_settings, is_candidate_outbound_enabled


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """get_settings() 带 lru_cache，用例之间必须清干净，否则互相污染。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def switch_path(tmp_path, monkeypatch):
    path = tmp_path / "candidate_outbound.switch"
    monkeypatch.setenv("CANDIDATE_OUTBOUND_SWITCH_FILE", str(path))
    monkeypatch.delenv("CANDIDATE_OUTBOUND_ENABLED", raising=False)
    return path


# ── 审计 JSONL 路径 ─────────────────────────────────────────────────────


def test_audit_jsonl_path_has_a_default():
    """零配置即可用：.51 的 .env 不随代码同步，新键必须带默认值。"""
    assert Settings().audit_jsonl_path == "data/audit/decisions.jsonl"


def test_audit_jsonl_path_overridable_via_env(monkeypatch):
    monkeypatch.setenv("AUDIT_JSONL_PATH", "D:/hr/audit/decisions.jsonl")
    assert Settings().audit_jsonl_path == "D:/hr/audit/decisions.jsonl"


# ── 外发总开关：默认关闭 ────────────────────────────────────────────────


def test_candidate_outbound_is_closed_by_default(switch_path):
    """代码默认关闭（Shao Peishen 2026-08-26 拍板选项 A）。"""
    assert Settings().candidate_outbound_enabled is False
    assert is_candidate_outbound_enabled() is False


# ── 外发总开关：每次求值、热改立刻生效 ──────────────────────────────────


def test_switch_file_flips_to_closed_at_runtime_without_restart(switch_path):
    """
    守护测试（spec「总开关运行期间被关闭」）：进程已经跑起来、Settings 已经
    被 lru_cache 缓存，此时改开关文件，**下一次求值立刻按新值走**，全程不
    cache_clear、不重启。
    """
    switch_path.write_text("true", encoding="utf-8")
    assert is_candidate_outbound_enabled() is True

    switch_path.write_text("false", encoding="utf-8")

    assert is_candidate_outbound_enabled() is False


def test_switch_file_flips_back_and_forth(switch_path):
    for raw, expected in [("on", True), ("0", False), ("YES", True), ("no", False)]:
        switch_path.write_text(raw, encoding="utf-8")
        assert is_candidate_outbound_enabled() is expected, raw


def test_switch_file_removal_falls_back_to_baseline(switch_path):
    switch_path.write_text("true", encoding="utf-8")
    assert is_candidate_outbound_enabled() is True

    switch_path.unlink()

    assert is_candidate_outbound_enabled() is False


def test_env_var_is_read_every_call_not_cached_at_startup(switch_path, monkeypatch):
    """
    环境变量走 os.environ 直读，不经 get_settings() 的缓存：先把 Settings 缓存
    起来（默认关），再改环境变量，下一次求值必须已经是新值。
    """
    assert is_candidate_outbound_enabled() is False  # 此刻 Settings 已被缓存

    monkeypatch.setenv("CANDIDATE_OUTBOUND_ENABLED", "true")

    assert is_candidate_outbound_enabled() is True


def test_switch_file_wins_over_env(switch_path, monkeypatch):
    """
    .51 的 .env 写着开启时，出事要能靠一个文件立刻全拦——文件必须压过环境变量。
    """
    monkeypatch.setenv("CANDIDATE_OUTBOUND_ENABLED", "true")
    switch_path.write_text("false", encoding="utf-8")

    assert is_candidate_outbound_enabled() is False


# ── 外发总开关：未知即拦截 ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("", id="empty"),
        pytest.param("   \n\n", id="blank-lines"),
        pytest.param("maybe", id="garbage"),
        pytest.param("ture", id="typo"),
        pytest.param("2", id="number"),
    ],
)
def test_unrecognised_switch_content_is_closed(switch_path, content):
    switch_path.write_text(content, encoding="utf-8")

    assert is_candidate_outbound_enabled() is False


def test_unreadable_switch_path_is_closed(tmp_path, monkeypatch):
    """路径被一个目录占住 → 读不出来 → 关。出错只能往保守那一侧倒。"""
    blocked = tmp_path / "switch_as_dir"
    blocked.mkdir()
    monkeypatch.setenv("CANDIDATE_OUTBOUND_SWITCH_FILE", str(blocked))
    monkeypatch.setenv("CANDIDATE_OUTBOUND_ENABLED", "true")

    assert is_candidate_outbound_enabled() is False


def test_first_nonblank_line_decides(switch_path):
    switch_path.write_text("\n\n  true  \nfalse\n", encoding="utf-8")

    assert is_candidate_outbound_enabled() is True


# ── 形态守护：不许被缓存成常量 ──────────────────────────────────────────


def test_switch_is_a_callable_not_a_value():
    """tasks 4.5「支持传 callable」：U4 拿到的必须是这个函数本身。"""
    assert callable(is_candidate_outbound_enabled)
    assert not isinstance(is_candidate_outbound_enabled, bool)


def test_switch_function_is_not_memoised():
    """
    结构性守护：给这个函数加 @lru_cache 是"看起来无害的优化"，但它会让热改
    彻底失效，而所有既有用例照样全绿（每个用例都是新进程状态）。
    """
    assert not hasattr(is_candidate_outbound_enabled, "cache_clear")
    assert not hasattr(is_candidate_outbound_enabled, "cache_info")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_config_audit_and_outbound.py -q`
Expected: FAIL —— `ImportError: cannot import name 'is_candidate_outbound_enabled' from 'app.config'`

- [ ] **Step 3: Write minimal implementation**

`app/config.py` 三处改动。

① 文件头（第 1-3 行）改为：

```python
import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 只有这几个取值算"开"。其余一切（拼错、空串、"maybe"、读不到）都算"关"——
# 未知即拦截，与门禁的 fail-closed 同一口径。
_TRUTHY = frozenset({"1", "true", "yes", "on"})
```

② `Settings` 类里，`log_max_bytes` 那行之后、`def validate_model_version` 之前插入：

```python
    # ── ai-audit-trail-and-outbound-gate（U1 一次加齐，U3/U4 只读不写）──
    # 留痕 JSONL 镜像的落盘路径（design D1：SQLite 为真身，JSONL 为防篡改
    # 镜像）。相对路径按进程工作目录解析，与 db_path 同一约定。
    audit_jsonl_path: str = "data/audit/decisions.jsonl"

    # 候选人外发总开关的**基线**取值，默认关闭。⛔ 业务代码不得直接读这个
    # 字段——Settings 由 get_settings() 缓存，直接读等于"启动时缓存一次"，
    # 违反 spec「总开关 MUST 在每次外发时求值」。唯一合法入口是本模块的
    # is_candidate_outbound_enabled()。
    candidate_outbound_enabled: bool = False

    # 热改开关文件。存在即以它为准，优先级高于环境变量与上面的基线值。
    # 为什么需要它：.51 是 Windows 计划任务拉起的单进程，改环境变量必须重启
    # 整机上的服务，而机器上还跑着另外 7 个服务。出事时要能立刻全拦，改一个
    # 文件就够（Shao Peishen 2026-08-26 拍板：允许热改、不重启生效）。
    candidate_outbound_switch_file: str = "data/candidate_outbound.switch"
```

③ 文件末尾，`get_settings()` 之后追加：

```python
def _as_switch(raw: str | None) -> bool:
    """把任意原始取值折成布尔，**未知一律折成 False**。"""
    if raw is None:
        return False
    for line in raw.splitlines():
        token = line.strip().lower()
        if token:
            return token in _TRUTHY
    return False  # 空文件 / 全空行：未知即关


def is_candidate_outbound_enabled() -> bool:
    """
    候选人外发总开关，**每次外发时求值**。

    ⛔ 禁止把返回值存成模块级常量、`__init__` 里的属性、或任何单例上的字段。
    ⛔ 调用点必须带括号求值：`is_candidate_outbound_enabled()`。函数对象本身
       恒为真，漏掉括号会让 fail-closed 静默变成 fail-open。

    取值优先级（前者存在即短路）：

    1. 开关文件 `Settings.candidate_outbound_switch_file`——热改通道，改文件
       立刻生效、不重启（Shao Peishen 2026-08-26 拍板）
    2. 环境变量 `CANDIDATE_OUTBOUND_ENABLED`——每次读 os.environ，不走
       get_settings() 的 lru_cache
    3. `Settings.candidate_outbound_enabled` 基线值，默认 False

    任何一层读不出明确的"开"，结果都是 False：未知即拦截。文件读失败
    （权限、目录占位、编码坏）同样返回 False——出错的方向只能是更保守的
    那一侧。
    """
    settings = get_settings()

    switch_file = Path(settings.candidate_outbound_switch_file)
    try:
        if switch_file.exists():
            return _as_switch(switch_file.read_text(encoding="utf-8"))
    except OSError:
        return False
    except UnicodeDecodeError:
        return False

    raw_env = os.environ.get("CANDIDATE_OUTBOUND_ENABLED")
    if raw_env is not None:
        return _as_switch(raw_env)

    return settings.candidate_outbound_enabled
```

**⚠️ 这里有一个真实踩过的坑（提取验证阶段实测）**：判断开关文件存在必须用 `switch_file.exists()`，**不能用 `is_file()`**。路径被一个目录占住时，`is_file()` 返回 `False` → 直接掉到环境变量那一层 → `.env` 里写着 `true` 就放行了。那是一个**配置坏掉却 fail-open** 的形状。用 `exists()` 时目录会走进 `read_text()` 并抛 `IsADirectoryError`（`OSError` 子类），被捕获后返回 `False`——配置坏掉 = 全拦。`test_unreadable_switch_path_is_closed` 就是这条的守护。

**⚠️ 相对 `delivery-units.md` §4 约定 1 的偏离（已登记，需 reviewer 确认）**：约定说"两个配置键在 U1 一次加齐"，本计划加了**三个**。第三个 `candidate_outbound_switch_file` 是为了兑现 §3.5（三）"允许热改，不重启生效"这条拍板结论——只靠环境变量的话，`.51` 上改开关仍然要重启进程（没人能改一个已运行进程的环境变量），"每次求值"就只在测试里成立、在生产里等于零。加这个键不破坏约定的目的（U3/U4 仍然只读不写 `app/config.py`）。**开关文件的路径与备份口径归 U7 的 7.3 运维文档。**

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_config_audit_and_outbound.py tests/test_config.py -q`
Expected: `22 passed`（新文件 17 条 + `test_config.py` 既有 5 条全绿，证明没破坏既有配置行为）

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/test_config_audit_and_outbound.py
git commit -m "feat(config): 加齐审计 JSONL 路径与外发总开关，开关每次求值可热改"
```

---

### Task 5: 老库回归守护（tasks 1.6）

**Files:**
- Modify: `tests/test_db_migration.py`（**只在文件末尾追加**，不改任何既有用例）

**Interfaces:**
- Consumes: `tests/test_db_migration.py` 既有的 `_legacy_db(tmp_path)` 夹具与 `_columns(conn, table)` 辅助函数、`app.storage.db` 的 `_ADDED_COLUMNS` / `apply_column_migrations` / `init_schema`（文件顶部已 import，**不需要新增 import**）
- Produces: 无生产代码。本 Task 只产出"U1 没有破坏 `.51` 老库"的机器证据——它是 Global Constraints 第二条的守护。

**为什么这个 Task 单独成一个而不是并进 Task 1-3**：它断言的不是"新表建对了"，而是"**既有表一个字节都没变**"。这两件事的失败方式完全不同——前者在本机新库上就能看出来，后者只有在带数据的老库上才现形。给它独立的 review gate，reviewer 才会真的去看老库那条路径。

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_db_migration.py` 末尾：

```python
# ── ai-audit-trail-and-outbound-gate · U1 的回归守护 ──────────────────────


_AUDIT_TABLES = ("analysis_run", "criterion_score", "pending_approval")


def _seed_effect_log_and_outbox(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO effect_log (effect_key, thread_id, node_name, business_key, applied_at) "
        "VALUES ('old-job:effect_persist_draft:1', 'old-job', 'effect_persist_draft', '1', "
        "datetime('now'))"
    )
    conn.execute(
        "INSERT INTO outbox (thread_id, message_type, payload_json) "
        "VALUES ('old-job', 'profile_card', '{\"body\": \"确认卡片\"}')"
    )
    conn.commit()


def test_audit_tables_are_created_on_a_legacy_db(tmp_path):
    """.51 的老库拿到三张新表，走的是 CREATE TABLE IF NOT EXISTS，无数据迁移。"""
    conn = _legacy_db(tmp_path)

    init_schema(conn)

    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(_AUDIT_TABLES) <= tables


def test_existing_tables_and_rows_are_untouched_by_audit_schema(tmp_path):
    """
    U1 的第二条硬约束的守护：既有表一行不改、一列不加。effect_log 与 outbox
    的列集合与行数在 init_schema 前后必须完全相同。
    """
    conn = _legacy_db(tmp_path)
    init_schema(conn)  # 老库先补齐到今天的形态
    _seed_effect_log_and_outbox(conn)

    before = {
        "effect_log_columns": _columns(conn, "effect_log"),
        "outbox_columns": _columns(conn, "outbox"),
        "effect_log_rows": conn.execute("SELECT count(*) FROM effect_log").fetchone()[0],
        "outbox_rows": conn.execute("SELECT count(*) FROM outbox").fetchone()[0],
        "job_rows": conn.execute("SELECT count(*) FROM job").fetchone()[0],
        "job_profile_columns": _columns(conn, "job_profile"),
    }

    init_schema(conn)

    after = {
        "effect_log_columns": _columns(conn, "effect_log"),
        "outbox_columns": _columns(conn, "outbox"),
        "effect_log_rows": conn.execute("SELECT count(*) FROM effect_log").fetchone()[0],
        "outbox_rows": conn.execute("SELECT count(*) FROM outbox").fetchone()[0],
        "job_rows": conn.execute("SELECT count(*) FROM job").fetchone()[0],
        "job_profile_columns": _columns(conn, "job_profile"),
    }
    assert before == after


def test_init_schema_stays_idempotent_with_audit_tables(tmp_path):
    """重跑三次不报错——UNIQUE INDEX 与 CHECK 都必须带 IF NOT EXISTS 的幂等性。"""
    conn = _legacy_db(tmp_path)

    init_schema(conn)
    init_schema(conn)
    init_schema(conn)

    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(_AUDIT_TABLES) <= tables


def test_audit_tables_never_enter_the_add_column_path(tmp_path):
    """
    U1 的第二条硬约束本身：三张全新表不走 _ADDED_COLUMNS。加列路径只服务
    "老库缺列"，把新表塞进去会让 apply_column_migrations 对着一张不存在的表
    执行 ALTER TABLE。
    """
    assert {table for table, _column, _ddl in _ADDED_COLUMNS} == {"job_profile"}


def test_add_column_path_is_a_noop_after_audit_schema(tmp_path):
    """三张新表建好之后，加列路径依然一列都不加。"""
    conn = _legacy_db(tmp_path)
    init_schema(conn)

    assert apply_column_migrations(conn) == []
```

- [ ] **Step 2: Run test to verify it fails**

如果 Task 1-3 已经做完，这五条**应该直接是绿的**——它们守护的是"没有破坏"，不是"新增功能"。要确认它们**真的有效**而不是恒真，先手工制造一次违例：

```bash
# 把 analysis_run 塞进 _ADDED_COLUMNS，看守护测试是否变红
```

在 `app/storage/db.py` 的 `_ADDED_COLUMNS` 元组末尾临时加一行 `("analysis_run", "bogus", "TEXT"),`，然后：

Run: `./venv/bin/python -m pytest tests/test_db_migration.py -q -k "add_column_path or never_enter"`
Expected: FAIL —— `test_audit_tables_never_enter_the_add_column_path` 报 `AssertionError: {'analysis_run', 'job_profile'} != {'job_profile'}`

**确认变红之后，把那一行删掉。** 这一步不能省：一条恒真的守护测试和一条真正生效的守护测试在 CI 里长得一模一样（本项目 §3.3 已确立过这条判据）。

- [ ] **Step 3: Write minimal implementation**

无生产代码改动。`app/storage/db.py` 保持 Task 3 结束时的状态——确认那行临时违例已经删掉：

```bash
git diff app/storage/db.py | grep -c "bogus"
```

Expected: `0`

- [ ] **Step 4: Run the full suite**

Run: `./venv/bin/python -m pytest -q 2>&1 | tail -2`
Expected: `275 passed`（基线 222 + 本单元新增 53）

再单独确认三个测试文件：

Run: `./venv/bin/python -m pytest tests/test_db_audit_schema.py tests/test_config_audit_and_outbound.py tests/test_db_migration.py tests/test_db.py tests/test_config.py -q`
Expected: `66 passed`

- [ ] **Step 5: Commit**

```bash
git add tests/test_db_migration.py
git commit -m "test(db): U1 老库回归守护——既有表零变化、加列路径不被污染"
```

---

## 交付前自查

- [ ] **全量测试 275 passed**，无 skip、无 xfail

```bash
./venv/bin/python -m pytest -q 2>&1 | tail -2
```

- [ ] **依赖文件 diff 为空**（本包三条硬边界之一）

```bash
git diff origin/main --stat -- requirements.txt pyproject.toml
```

Expected: 无输出

- [ ] **零跨仓库引用**

```bash
grep -rn "zhuopin_platform" app/ tests/ | grep -v "^Binary"
```

Expected: 无输出

- [ ] **`_ADDED_COLUMNS` 与 `db.py` 的函数体一个字节没改**

```bash
git diff origin/main -- app/storage/db.py | grep "^[-+]" | grep -v "^[-+][-+]" | grep -c "^-"
```

Expected: `0`（本单元对 `db.py` 只有新增行，没有删除行）

- [ ] **`analysis_run` 的 NOT NULL 集合恰好是那七列**

```bash
./venv/bin/python -m pytest tests/test_db_audit_schema.py -q -k "notnull_set_is_exactly"
```

- [ ] **总开关没有被读成常量**

```bash
grep -rn "candidate_outbound_enabled" app/ | grep -v "app/config.py"
```

Expected: 无输出（U1 结束时它还没有任何消费方）

- [ ] **本单元的可观察行为为零变化**：`app/graph/`、`app/web/`、`app/agents/`、`app/llm/`、`app/main.py`、`index.html` 全部不在 diff 里

```bash
git diff origin/main --name-only
```

Expected: 恰好五个文件——`app/storage/db.py`、`app/config.py`、`tests/test_db_audit_schema.py`、`tests/test_config_audit_and_outbound.py`、`tests/test_db_migration.py`

---

## spec 覆盖对照

| spec Requirement | U1 的落点 | 完整兑现于 |
|---|---|---|
| `ai-decision-audit` · AI 调用的可复现留痕 | Task 1：`analysis_run` 十四列（含 `configured_model` / `response_model` 分列、`system_fingerprint` 可空） | U2（写入）、U3（接线） |
| `ai-decision-audit` · 逐项评分必须带证据回指 | **Task 2 完整兑现存储层强制那一半**（`CHECK` + 外键双向可追溯） | 白名单校验在 U3 |
| `ai-decision-audit` · 评分项白名单约束 | ⛔ U1 刻意不做（不写死进 DDL） | U3（3.4）、U6（6.3） |
| `ai-decision-audit` · 留痕不可无痕篡改 | Task 4：`audit_jsonl_path` 配置位 | U2（`JsonlChainSink` + `verify_chain`） |
| `ai-decision-audit` · 留痕可查询与合规断言 | Task 1/2/3：三条查询索引 | U2（`query_by`）、U6（断言） |
| `ai-decision-audit` · 留痕数据的用途限制 | **Task 1 完整兑现**（表注释 + 机器校验） | —— |
| `outbound-approval-gate` · 门禁覆盖范围 / fail-closed 判定语义 | —— | U4 |
| `outbound-approval-gate` · 人工确认才放行 | Task 3：`confirmed_by` 列 | U4/U5 |
| `outbound-approval-gate` · 第二道结构性总开关 | **Task 4 完整兑现"每次求值、不启动时缓存、运行期改值不重启"** | U4 消费、U5 接线 |
| `outbound-approval-gate` · 被拦截草稿的持久化待审批队列 | **Task 3 完整兑现存储形态**（独立表、三状态、重复入队防线） | U5（状态机与读写） |
| `outbound-approval-gate` · 外发与拦截动作强制留痕 | Task 3：`blocked_reason` 列 | U5 |
| `outbound-approval-gate` · 门禁判定与副作用分离 | Task 4：开关做成 callable，让门禁能保持纯函数 | U4/U5 |

---

## 本计划相对 `tasks.md` / `delivery-units.md` 的偏离登记（共四条，全部需 reviewer 确认）

| # | 偏离 | 位置 | 方向 | 理由 |
|---|---|---|---|---|
| 1 | `evidence_ref` 的 `CHECK` 用带字符集的 `trim(x, ' '\|\|char(9)\|\|char(10)\|\|char(13))`，而非 tasks 1.2 字面的 `trim(x)` | Task 2 | **更严** | 单参 `trim()` 只剥空格，纯制表符的 `evidence_ref` 会绕过铁律 4 |
| 2 | 唯一索引建在 `(thread_id, content_hash)`，而非 tasks 1.3 字面的单列 `content_hash` | Task 3 | 粒度对齐 | 与 U5 幂等键 `{thread_id}:...:{content_hash}` 同粒度；单列全局唯一会让不同 thread 的同内容草稿入队时抛 `IntegrityError` |
| 3 | `pending_approval.message_type` / `recipient` 可空（tasks 1.3 未写可空性） | Task 3 | fail-closed | 草稿被拦下的常见原因正是这些字段缺失；NOT NULL 会把"拦下畸形消息"变成异常穿透 |
| 4 | 配置键加了**三个**而非 `delivery-units.md` §4 约定 1 说的两个 | Task 4 | 兑现拍板结论 | 只有环境变量的话，`.51` 上改开关仍需重启，§3.5（三）"允许热改、不重启生效"在生产里等于零 |

**四条全部属"不改变外部可观察行为"的技术方案范畴**（`CLAUDE.md` 决策代理表「可代」第 3 项），但因为都落在合规链路上，**在 `run-build` 的 final review 里逐条确认后再合并**。

---

## 提取验证记录（`spec-to-plan` 第 6 步，2026-08-26 实测）

本计划里的**全部代码块与测试**已在一份隔离副本（`scratchpad/u1verify/`，含 `app/` `tests/` `scripts/` 与两个部署脚本）里原样跑过，用的是本仓库的 `./venv`（Python 3.14.6）：

- **结果：275 passed**（基线 222 + 新增 53），0 failed、0 skipped
- **揪出 1 个真实 bug**：`is_candidate_outbound_enabled()` 最初写的是 `switch_file.is_file()`。开关文件路径被一个目录占住时，`is_file()` 返回 `False` → 掉到环境变量层 → `.env` 写着 `true` 就放行。**配置坏掉却 fail-open**，方向完全错。改为 `exists()` 后，目录会走进 `read_text()` 抛 `IsADirectoryError`（`OSError` 子类）被捕获返回 `False`。这个 bug 光看代码不容易发现——`is_file()` 看起来比 `exists()` "更严谨"。
- **边界**：测试与被测代码出自同一份文档、同一个作者，全通只证明**代码可执行且内部自洽**，不证明**符合 spec**。spec 合规由 `run-build` 的两阶段 review 负责。

---

## 完成判据（`tasks.md` 第 1 章的 checkbox 在这些全部成立后才勾）

1. `./venv/bin/python -m pytest -q` → `275 passed`
2. `git diff origin/main --name-only` → 恰好五个文件，都在 §File Structure 表里
3. 三张表在**老库**（`_legacy_db` 夹具）上建得出来，且 `effect_log` / `outbox` / `job` / `job_profile` 的列与行数零变化
4. `analysis_run` 的 `notnull=1` 列集合**恰好**是那七个
5. 直接 `INSERT` 一条空白 `evidence_ref` 被数据库拒（六种空白形态全拒）
6. `is_candidate_outbound_enabled()` 在**不重启、不清缓存**的前提下随开关文件改变而改变
7. 偏离登记表四条已被 reviewer 逐条确认

**合并后立刻解锁**：U2（`app/audit`）与 U4（`app/outbound` 纯函数）可各出一份 `spec-to-plan` 并行开工（`delivery-units.md` §5）。

**下一步**：用 `run-build` 执行本计划。⛔ 不要在本会话里开始实现。
