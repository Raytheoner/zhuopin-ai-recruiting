# M1 采集质量修复 · 交付单元 A（存储地基与结构化追问）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 `job_profile` 加上可幂等迁移的新列并落逐轮时序留痕，同时把「一个追问」从裸字符串升级为贯穿 agent → graph → API → 前端的一等对象——**本单元不改变任何用户可见的判定行为**。

**Architecture:** 两条互相独立、可分别 review 的改动线。第一条是存储与可观测性：`init_schema` 之后跑一段幂等的 `ALTER TABLE ADD COLUMN`（`PRAGMA table_info` 判缺），`LLMGateway` 把它已经算出来但被 `NoopAuditHook` 丢掉的 `latency_ms` 与 `response_model` 透出给调用方（**不改 `AuditHook` 签名**），经 `IntakeState` 传到 `effect_persist_draft`，与画像草案落在**同一条 INSERT** 里。第二条是问题对象化：新增 `app/agents/intake_question.py` 承载 `IntakeQuestion`、`question_id` 派生规则与**唯一**的问题→文本渲染函数，`_IntakeTurnSchema.questions` 从 `list[str]` 改为结构化对象数组并对模型退化成裸字符串的情况自动降级。两条线在 Task 5/6 汇合。

**Tech Stack:** Python 3.14（`./venv`，与 `.51` 服务器严格对齐）· LangGraph 1.0.10 + `langgraph-checkpoint-sqlite` 2.0.6 · FastAPI 0.115.6 · pydantic 2.13.4 · SQLite（`sqlite3` 3.53.3）· pytest 8.3.4 · 原生 DOM 单文件前端（无构建）

## Global Constraints

以下条目从 `CLAUDE.md`（2026-08-19 版）逐字复制。**每个 Task 的验收隐含包含本节全部内容**，`subagent-driven-development` 会把这一段原样交给 reviewer 当注意力透镜。

**工程铁律（不可违背）**

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
   *为什么*：业务写失败而幂等记录成功 → 系统判定"已执行"→ 永不重试。**幂等本是防重复的保护，拆开事务后变成永久丢失的保证。** 实证：`.51` 现网 2026-08-10 与 08-12 各丢一轮 `outbox`（幂等记录已落），用户没收到回复且永远不会补发，见 `docs/findings/2026-08-13-sqlite-事务归属冲突.md` §8.5。
2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。
5. **`temperature=0`；模型版本优先显式锁定**，禁止 `latest` 类别名。
   供应商不提供带版本号快照时（如 DeepSeek 公开 API 只有 `deepseek-chat` 这类会漂移的别名），**必须从 API 响应里取回实际的 `model` 字段并持久化**——配置里写的名字不算数，响应返回的才算。
   *为什么*：铁律的目的是评分可复现、可审计。供应商静默升级模型会让历史评分失去解释力，而 PIPL 的说明权要求你能回答"这条评分是哪个版本打的"。锁不住版本时，至少要记得住版本。
7. **`langgraph >= 1.0.10`**（GHSA-g48c-2wqr-h844）。

**部署约束**

1. **路径前缀就绪**：FastAPI `root_path=/hr/recruit-agent`，前端资源与接口调用**一律相对路径**，禁止硬编码 `/static/…` `/api/…`。验收标准是挂到任意子路径下都能正常工作，且有测试覆盖。
4. **目标服务器是 Windows，没有 Docker**。部署形态 = Python venv + Windows 计划任务（SYSTEM 账户 + AtStartup + 失败重启 3 次）+ 防火墙规则 + scp 推送。不要引入容器。

**合规红线（本单元相关的两条）**

- **AI 生成的 JD、拒信、邀约须带标识**（《AI 生成合成内容标识办法》2025-09-01 施行）。→ 本单元只让 `options` 有地方放，**不渲染选项控件**；"以下为 AI 建议选项"的标识在第 4 章落地（tasks 4.3）。本单元不得出现任何未标识的 AI 生成候选项渲染。
- **模型全部走境内**，简历数据不出境。→ 本单元不换供应商、不改 `base_url`。

**明确不适用（reviewer 不必在本单元追这几条）**

- 铁律 3（AI 评分持久化）、铁律 4（`evidence_ref` 非空）：本单元不写 `criterion_score`，代码库中亦无该表。reviewer 确认无相关落地即可。
- 铁律 6（企微回调先落库）：本单元不接企微通道。
- 合规红线「AI 只做排序推荐，不做自动淘汰」：本单元不涉及候选人淘汰路径。
- **`temperature=0`** 已由 `app/llm/gateway.py:273` 硬编码，本单元不得修改这一行。

---

## 交付单元边界

**本单元 = `openspec/changes/m1-intake-quality-fixes/tasks.md` 第 1 章 + 第 2 章。**

- 第 1 章「存储地基与逐轮时序留痕」（1.1–1.7）→ 能力 `intake-turn-observability`
- 第 2 章「结构化追问对象端到端透传」（2.1–2.8）→ 能力 `intake-guided-options` / `intake-question-tracking` 的**载体部分**

选这两章作为一个单元的理由：第 2 章是第 3/4/5 章的地基（`tasks.md` 依赖顺序注明"必须先合"），第 1 章是第 7 章的地基（新增两列）。两章都不改变用户可见行为，可以独立测试、独立合并、独立回滚。

**第 2 章的自我约束（原文）：「本章只换载体，不改任何判定行为——合并后用户可见行为应与合并前一致。」** 这条是 reviewer 的第一判据：任何"顺手把兜底逻辑一起做了"的改动都应被打回，它属于第 3 章。

### 与并行变更 `server-runtime-logging` 的边界

`server-runtime-logging`（并行进行中）负责建立 logging 基础设施：`app/observability/` 的日志配置与脱敏过滤器、`app/main.py` 的进程启动初始化、`request_id` 中间件、`deploy-server.ps1` 的日志参数。

**本单元一律复用、不另建一套：**

- ❌ **不得修改 `app/main.py`** —— 日志初始化是并行变更的落点，两边同时改会冲突
- ❌ 不新增任何 logging handler / formatter / 轮转配置
- ✅ 需要打日志时只用 `logger = logging.getLogger(__name__)`（`app/llm/gateway.py:12` 与 `app/storage/idempotency.py:6` 已是这个写法），配置由并行变更统一注入
- ✅ 本单元的时序留痕落**数据库列**，不落日志文件——两者职责不同：业务时序要能被 SQL 统计，运行日志会被轮转清掉

### P1-1 的范围复核（2026-08-19 取证之后）

`intake-question-tracking`（「未答子问题被换措辞重问」，即 P1-1）在 propose 阶段的归因是：**"每轮打包问 2-3 个问题、答不全就换措辞追"是设计策略的固有代价。**

`docs/findings/2026-08-13-sqlite-事务归属冲突.md` §8.5（2026-08-19 落档）推翻了这个归因的**唯一性**：

- thread `1cabfb91` 在 2026-08-12 09:49:09 那一轮，`effect_deliver_message` 的幂等记录已落而 `outbox` 缺行——**用户压根没收到 AI 的回复**。该 thread 下一条 `outbox` 以「抱歉，刚才的问题重复了」开头（全仓 grep 该句零命中，是模型自己生成的）：AI 在为一次**它以为自己犯的重复**道歉，真实原因是上一轮回复没送到。`41909b40`（08-10 07:16:00）同型。
- pilot 于 2026-08-16~08-18 收集，而事务归属修复 2026-08-18 13:04 才部署到 `.51`——**pilot 期间跑的是有 bug 的版本**。

**对本计划的三条硬性影响：**

1. **P1-1 仍然要做**（第 5 章，不在本单元）。追问策略确实有"未答子问题换措辞重问"这个代价，`2494103e` 第 3–4 轮的 IATF 16949 / ISO 26262 序列是该会话自身的记录，两条原子性不变式都是绿的，没有查到丢消息。
2. **但它不再是唯一解**。投递可靠性那一层已由 `fix-sqlite-transaction-ownership` 解决（已归档，`openspec/specs/effect-transaction-integrity`）。
3. **验收标准禁止写成「重复追问归零」。** 那个目标已经部分由别的修复达成，用它当判据会掩盖 P1-1 本身有没有效果。第 5 章的验收必须写成可归因的形式——例如"同一 `question_id` 重问时 `is_reask=true` 且渲染带重问标注"、"重问次数超上限即转未指定字段"——即断言**机制生效**，不是断言**表象消失**。

**本单元（第 1、2 章）的对应约束**：`_repeats_earlier_assistant_turn`（`app/agents/intake_agent.py:170`）在本单元只做签名适配（改为接收渲染后的文本），**不得删除、不得改判定逻辑**。它的去留是 tasks 5.8 要给的结论，属第 5 章。本单元也不得在任何注释或文档里把"重复追问"写成已解决。

---

## Requirement → Task 覆盖矩阵（全变更包）

5 个 spec 的每一条 `### Requirement:` 都列在下面。**本单元覆盖的指到本计划的 Task；其余指到 `tasks.md` 的章节**，等本单元合并后再各自出计划（届时类型名与函数签名已是既成事实，不会漂移）。

| Spec | Requirement | 落点 | 本单元? |
|---|---|---|---|
| `intake-turn-observability` | 逐轮时序留痕 | **Task 1 / 2 / 4 / 5** | ✅ |
| `intake-turn-observability` | 系统延迟与用户思考时长可分离 | **Task 5**（分离口径测试） | ✅ |
| `intake-turn-observability` | 时序留痕不承担审计职责 | **Task 5**（`llm_response_model` 在本单元保持 NULL） | ✅ |
| `intake-guided-options` | 结构化追问与可选项作答 | **Task 3 / 4 / 6（载体与契约）** + 第 4 章（可点选控件、AI 建议标识） | ⚠️ 部分 |
| `intake-guided-options` | 模糊回复与反问的兜底档位 | 第 3 章（3.3–3.8） | ❌ |
| `intake-guided-options` | 候选档位不得代替用户做决定 | 第 3 章（3.7） | ❌ |
| `intake-guided-options` | 零产出轮不消耗追问预算 | 第 3 章（3.9–3.11），依赖 **Task 1** 加的 `is_productive` 列 | ❌ |
| `intake-question-tracking` | 子问题的稳定标识与拆分 | **Task 3（`question_id` 派生）+ Task 4（拆分约束进 SYSTEM_PROMPT）** | ✅ |
| `intake-question-tracking` | 已问未答的判定 | 第 5 章（5.1–5.3） | ❌ |
| `intake-question-tracking` | 重问必须显式标注 | 第 5 章（5.4），渲染入口与 `is_reask` 字段由 **Task 3** 就位 | ⚠️ 部分 |
| `intake-question-tracking` | 重问次数上限 | 第 5 章（5.5） | ❌ |
| `intake-completeness-warning` | 未指定字段由系统确定性推导 | 第 6 章（6.1–6.3） | ❌ |
| `intake-completeness-warning` | 确认前的显著缺口警示 | 第 6 章（6.4–6.6） | ❌ |
| `intake-completeness-warning` | 带缺口确认必须显式知情 | 第 6 章（6.7–6.10） | ❌ |
| `intake-field-grounding` | 画像字段必须携带可校验的来源 | 第 7 章（7.1–7.4） | ❌ |
| `intake-field-grounding` | 来源校验是确定性的 | 第 7 章（7.3、7.6–7.8） | ❌ |
| `intake-field-grounding` | 本能力只度量不拦截 | 第 7 章（7.5），落在 **Task 1** 加的 `ungrounded_fields` 列 | ❌ |
| `intake-field-grounding` | 编造信号可按模型版本归因 | 第 7 章（7.9–7.10），依赖 **Task 2** 透出的 `response_model` 与 **Task 1** 加的 `llm_response_model` 列 | ❌ |

**两处 ⚠️ 部分的准确含义**：本单元只交付数据结构与端到端契约（问题带 `options` / `is_reask` 字段并原样透传到 API 与前端），**不渲染选项控件、不判定重问**。第 4、5 章补齐剩余部分后这两条才算完成。归档 `m1-intake-quality-fixes` 前必须已全部变为 ✅。

---

## File Structure

**新建**

| 文件 | 职责 |
|---|---|
| `app/agents/intake_question.py` | 追问对象的**唯一**真源：`IntakeQuestion` 数据结构、`derive_question_id()`、`render_questions_text()`（唯一渲染入口）、`normalize_question_payload()`（历史 payload 兼容）。第 3、4、5 章都从这里取 |
| `tests/test_intake_question.py` | 上述纯函数的单元测试 |
| `tests/test_db_migration.py` | 老库加列迁移测试（含"老 schema + 已有数据"的还原） |
| `tests/test_static_frontend.py` | 单文件前端的静态断言（相对路径、结构化问题渲染） |
| `docs/tech-debt.md` | 仓库级技术债清单（tasks 1.7 要求登记，第 7 章的 7.11 之后追加到同一份） |

**修改**

| 文件 | 改什么 |
|---|---|
| `app/storage/db.py` | `SCHEMA` 的 `job_profile` 补 6 列；新增 `_ADDED_COLUMNS` / `apply_column_migrations()` / `sqlite_utc_now()`；`init_schema()` 调用迁移 |
| `app/llm/gateway.py` | 新增 `LLMCallMeta` 与 `extract_structured_with_meta()`；`extract_structured()` 降为薄包装。**`AuditHook` Protocol 一个字符都不改** |
| `app/agents/intake_question.py` | （新建，见上） |
| `app/agents/intake_agent.py` | `_IntakeQuestionSchema`；`_IntakeTurnSchema.questions` 改结构化 + 裸字符串降级校验器；`IntakeTurnResult` 带 `questions_text` / `llm_latency_ms` / `llm_response_model`；`SYSTEM_PROMPT` 加拆分约束与问题形状说明（`prompt_version` 升到 `intake-v3`）；`_repeats_earlier_assistant_turn` 改收文本 |
| `app/graph/state.py` | 加 `turn_started_at` / `llm_latency_ms`；`pending_questions` 语义改为 `list[dict]` |
| `app/graph/nodes.py` | `compute_intake_turn` 透传时序与结构化问题；`effect_persist_draft` 在同一条 INSERT 里写时序两列 |
| `app/graph/build.py` | `_deliver_node` 的 question payload 带结构化问题列表 + `questions_text` |
| `app/web/server.py` | `_run_turn` 打 `turn_started_at` 时间戳；`_run_turn` / `get_job` 的响应过 `normalize_question_payload()` |
| `app/web/static/index.html` | `renderMessage` 适配新 payload（**本章仍只渲染文本**），兼容历史裸字符串 |
| `tests/test_db.py`、`tests/test_llm_gateway.py`、`tests/test_intake_agent.py`、`tests/test_graph_nodes.py`、`tests/test_web_api.py` | 断言适配 + 新增覆盖 |

**不得修改**：`app/main.py`（并行变更的落点）、`app/storage/idempotency.py`（幂等语义不变）、`app/llm/gateway.py` 的 `AuditHook` 与 `temperature=0`、`app/graph/build.py` 的 `checkpointer_conn` 独立连接逻辑与 `business_key` 构造规则、`~/Library/CloudStorage/OneDrive-Personal/Projects/企业AI转型/`（可读，**不得写入或 lock**）。

## 开工前的环境确认

本仓库的 venv 是 `./venv`（**不是** `.venv`），Python 3.14.6。基线：`91 passed`。

```bash
./venv/bin/python --version && ./venv/bin/python -m pytest -q 2>&1 | tail -3
```

预期：`Python 3.14.6` 且 `91 passed`。**基线不绿不要开工**——否则分不清是自己改坏的还是本来就坏的。

---

### Task 1: `job_profile` 加列的幂等迁移（tasks 1.1 / 1.2）

**Files:**
- Modify: `app/storage/db.py:13-21`（`SCHEMA` 里的 `job_profile`）、`app/storage/db.py:75-77`（`init_schema`）
- Test: `tests/test_db_migration.py`（新建）

**Interfaces:**
- Produces:
  - `app.storage.db._ADDED_COLUMNS: tuple[tuple[str, str, str], ...]` —— `(表名, 列名, 列 DDL 片段)`
  - `app.storage.db.apply_column_migrations(conn: sqlite3.Connection) -> list[str]` —— 幂等加列，返回本次真的加上的列名
  - `app.storage.db.init_schema(conn: sqlite3.Connection) -> None` —— 签名不变，内部多跑一次迁移
  - 新列（全部服务本变更包，本单元只写其中两列的值）：`is_productive INTEGER NOT NULL DEFAULT 1`（第 3 章写）、`turn_started_at TEXT`（**Task 6 写**）、`llm_latency_ms REAL`（**Task 6 写**）、`derived_unspecified_fields TEXT NOT NULL DEFAULT '[]'`（第 6 章写）、`ungrounded_fields TEXT NOT NULL DEFAULT '[]'`（第 7 章写）、`llm_response_model TEXT`（第 7 章写）

**为什么不能只改 `CREATE TABLE`**：`CREATE TABLE IF NOT EXISTS` 对已存在的表完全无效。`.51` 上 `data/demo.db` 已有 15 个真实 job，部署脚本不重建库——只改 `CREATE TABLE` 的话新列在服务器上永远不会出现，是**上线后才炸、且只在服务器上炸**的静默故障（design.md 决策 10）。

- [ ] **Step 1: 写失败测试（老库加列）**

新建 `tests/test_db_migration.py`：

```python
import json
import sqlite3

import pytest

from app.storage.db import _ADDED_COLUMNS, apply_column_migrations, get_connection, init_schema

# 2026-08-18 及之前 .51 现网 data/demo.db 里 job / job_profile 的真实形态。
# 刻意硬编码而不是从 SCHEMA 裁剪：这两条 DDL 代表"服务器上已经存在的那个库长
# 什么样"，是一个历史事实，不能随 SCHEMA 一起演进——否则这个测试会跟着新代码
# 一起漂移，永远测不出"老库升级不了"这个真正要防的故障。
_LEGACY_JOB_DDL = """
CREATE TABLE job (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    department TEXT,
    status TEXT NOT NULL DEFAULT 'drafting',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_LEGACY_JOB_PROFILE_DDL = """
CREATE TABLE job_profile (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES job(id),
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    unspecified_fields TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _legacy_db(tmp_path) -> sqlite3.Connection:
    """建一个"老 schema + 已有数据"的库，模拟 .51 上的 data/demo.db。"""
    conn = get_connection(str(tmp_path / "legacy.db"))
    conn.executescript(_LEGACY_JOB_DDL + _LEGACY_JOB_PROFILE_DDL)
    conn.execute(
        "INSERT INTO job (id, title, status) VALUES ('old-job', '采购工程师', 'approved')"
    )
    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json, unspecified_fields) "
        "VALUES ('old-job-v1', 'old-job', 1, 'approved', ?, ?)",
        (
            json.dumps({"job_title": "采购工程师"}, ensure_ascii=False),
            json.dumps(["toolchain"], ensure_ascii=False),
        ),
    )
    conn.commit()
    return conn


def test_init_schema_adds_new_columns_to_legacy_db(tmp_path):
    conn = _legacy_db(tmp_path)
    assert "turn_started_at" not in _columns(conn, "job_profile")

    init_schema(conn)

    expected = {column for _table, column, _ddl in _ADDED_COLUMNS}
    assert expected <= _columns(conn, "job_profile")


def test_legacy_rows_survive_migration_with_defaults(tmp_path):
    """既有 15 个 job 的历史行不需要回填：新列必须可空或有常量默认值。"""
    conn = _legacy_db(tmp_path)

    init_schema(conn)

    row = conn.execute(
        "SELECT profile_json, unspecified_fields, is_productive, turn_started_at, "
        "llm_latency_ms, derived_unspecified_fields, ungrounded_fields, llm_response_model "
        "FROM job_profile WHERE id='old-job-v1'"
    ).fetchone()
    assert json.loads(row[0])["job_title"] == "采购工程师"  # 老数据一字不动
    assert json.loads(row[1]) == ["toolchain"]
    assert row[2] == 1  # is_productive 默认按"有产出"算，语义与今天一致
    assert row[3] is None  # 历史行没有时序留痕，留 NULL 而不是编一个
    assert row[4] is None
    assert json.loads(row[5]) == []
    assert json.loads(row[6]) == []
    assert row[7] is None


def test_apply_column_migrations_is_idempotent(tmp_path):
    conn = _legacy_db(tmp_path)

    first = apply_column_migrations(conn)
    second = apply_column_migrations(conn)

    assert set(first) == {column for _table, column, _ddl in _ADDED_COLUMNS}
    assert second == []  # 第二次一列都不加，且不抛 "duplicate column name"

    init_schema(conn)  # 重复跑整个 init_schema 同样不能报错
    init_schema(conn)


def test_fresh_and_migrated_schemas_have_identical_columns(tmp_path):
    """
    漂移守卫：SCHEMA 的 CREATE TABLE 与 _ADDED_COLUMNS 是同一件事的两种表达
    （新库走 CREATE、老库走 ALTER）。只改一边是这类迁移最经典的错法——本地
    新建的库全绿，服务器上的老库缺列，而两者都不会报错。
    """
    fresh = get_connection(str(tmp_path / "fresh.db"))
    init_schema(fresh)

    migrated = _legacy_db(tmp_path)
    init_schema(migrated)

    assert _columns(fresh, "job_profile") == _columns(migrated, "job_profile")


def test_every_added_column_is_nullable_or_has_constant_default(tmp_path):
    """
    "既有行不需要回填"这个承诺的机器判据：notnull=1 的列必须带默认值。
    另外 SQLite 明确拒绝 ALTER TABLE ADD COLUMN 带非常量默认值
    （"Cannot add a column with non-constant default"），所以 DDL 里不能写
    DEFAULT (datetime('now'))——这条测试顺带把那个坑钉死。
    """
    conn = get_connection(str(tmp_path / "fresh.db"))
    init_schema(conn)

    added = {column for _table, column, _ddl in _ADDED_COLUMNS}
    for row in conn.execute("PRAGMA table_info(job_profile)"):
        name, notnull, default = row[1], row[3], row[4]
        if name not in added:
            continue
        if notnull:
            assert default is not None, f"{name} 是 NOT NULL 却没有默认值，老行无法回填"
        assert "datetime(" not in str(default or ""), f"{name} 用了非常量默认值，ALTER TABLE 会被 SQLite 拒绝"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_db_migration.py -v`
Expected: FAIL —— `ImportError: cannot import name '_ADDED_COLUMNS' from 'app.storage.db'`

- [ ] **Step 3: 实现迁移**

`app/storage/db.py`：把 `SCHEMA` 里的 `job_profile` 替换成下面这段（新列有注释说明各自归谁写）：

```sql
CREATE TABLE IF NOT EXISTS job_profile (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES job(id),
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    unspecified_fields TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    -- 本轮是否有产出（新字段或新问题）。追问预算按有产出轮计数，判定在
    -- compute_intake_turn 里做（m1-intake-quality-fixes 第 3 章）。默认 1
    -- 保证历史行与"未接入判定前"的行为与今天完全一致。
    is_productive INTEGER NOT NULL DEFAULT 1,
    -- 本轮起始时刻（HTTP 请求进入、尚未调模型）。轮次**结束**时刻沿用
    -- created_at，不另加列。两者格式必须一致，见 sqlite_utc_now()。
    turn_started_at TEXT,
    -- 本轮 LLM 累计耗时（含重试），单位毫秒。
    llm_latency_ms REAL,
    -- 系统按画像字段表推导出的未指定字段（第 6 章写）。与上面那列 LLM
    -- 自由生成的 unspecified_fields 并存，前者是真源、后者降级为对照。
    derived_unspecified_fields TEXT NOT NULL DEFAULT '[]',
    -- 本轮未通过来源校验的字段清单（第 7 章写）。
    ungrounded_fields TEXT NOT NULL DEFAULT '[]',
    -- 本轮 API 响应里实际返回的模型标识（第 7 章写，铁律 5）。
    llm_response_model TEXT
);
```

在 `SCHEMA` 常量之后、`get_connection` 之前插入：

```python
# 2026-08-19 起新增的列必须同时出现在两处：上面的 CREATE TABLE（新库）与下面的
# _ADDED_COLUMNS（老库）。CREATE TABLE IF NOT EXISTS 对已存在的表完全无效，
# .51 上 data/demo.db 有 15 个真实 job、部署脚本不重建库——只改 CREATE TABLE
# 的话新列在服务器上永远不会出现，而且不报错（design.md 决策 10）。
# tests/test_db_migration.py 的漂移守卫测试盯着这两处的一致性。
#
# DDL 片段里的 DEFAULT 必须是常量：SQLite 拒绝 ALTER TABLE ADD COLUMN 带
# 非常量默认值（"Cannot add a column with non-constant default"），所以这里
# 不能写 DEFAULT (datetime('now'))。
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("job_profile", "is_productive", "INTEGER NOT NULL DEFAULT 1"),
    ("job_profile", "turn_started_at", "TEXT"),
    ("job_profile", "llm_latency_ms", "REAL"),
    ("job_profile", "derived_unspecified_fields", "TEXT NOT NULL DEFAULT '[]'"),
    ("job_profile", "ungrounded_fields", "TEXT NOT NULL DEFAULT '[]'"),
    ("job_profile", "llm_response_model", "TEXT"),
)


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def apply_column_migrations(conn: sqlite3.Connection) -> list[str]:
    """
    幂等加列：逐列独立判断、缺哪列补哪列，返回本次真的加上的列名。

    逐列独立是刻意的（design.md 风险表「服务器 SQLite 加列失败或部分成功」）：
    一列失败不影响其余列，重跑一次会把上次没加上的补齐。
    """
    added: list[str] = []
    for table, column, ddl in _ADDED_COLUMNS:
        if column in _existing_columns(conn, table):
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        added.append(column)
    return added
```

把 `init_schema` 改成：

```python
def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # 新库走 CREATE TABLE 就已经带全新列，这里是空转；老库（.51 的 demo.db）
    # 靠这一步补列。两条路径的结果必须一致，由 tests/test_db_migration.py 的
    # test_fresh_and_migrated_schemas_have_identical_columns 守着。
    apply_column_migrations(conn)
    conn.commit()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_db_migration.py -v`
Expected: PASS ×5

- [ ] **Step 5: 跑全量回归**

Run: `./venv/bin/python -m pytest -q 2>&1 | tail -3`
Expected: `96 passed`（基线 91 + 本任务 5）

- [ ] **Step 6: 在真实 demo 库的副本上验证（tasks 8.2 的本地部分，提前做）**

`data/demo.db` 若存在则跑，不存在就跳过并在提交信息里说明。**必须操作副本，不许碰原库。**

```bash
test -f data/demo.db && for ext in "" "-wal" "-shm"; do cp "data/demo.db$ext" "/tmp/demo-migration-check.db$ext" 2>/dev/null; done && ./venv/bin/python -c "
from app.storage.db import get_connection, init_schema, _ADDED_COLUMNS
conn = get_connection('/tmp/demo-migration-check.db')
before = conn.execute('SELECT COUNT(*) FROM job').fetchone()[0]
init_schema(conn)
cols = {r[1] for r in conn.execute('PRAGMA table_info(job_profile)')}
after = conn.execute('SELECT COUNT(*) FROM job').fetchone()[0]
rows = conn.execute('SELECT COUNT(*) FROM job_profile').fetchone()[0]
assert {c for _t, c, _d in _ADDED_COLUMNS} <= cols, cols
assert before == after, (before, after)
print(f'OK: job={after} job_profile={rows} 新列齐全')
" ; rm -f /tmp/demo-migration-check.db /tmp/demo-migration-check.db-wal /tmp/demo-migration-check.db-shm
```

Expected: `OK: job=<N> job_profile=<M> 新列齐全`。

**必须连 `-wal` / `-shm` 一起拷**：这个库跑在 WAL 模式下（`get_connection` 设了 `PRAGMA journal_mode=WAL`），只拷主文件会丢掉还在 WAL 里的最近若干轮数据，验证就成了对一份残缺副本的验证。

本机 `data/demo.db` 撰写本计划时是 5 个 job / 22 个画像版本（2026-08-19 实测通过，历史行的新列取到 `is_productive=1`、`turn_started_at=NULL`、`derived_unspecified_fields='[]'`）；`.51` 上是 15 个 job。**关键判据是加列前后 job 数相等、断言不抛**，不是具体数字。

- [ ] **Step 7: 提交**

```bash
git add app/storage/db.py tests/test_db_migration.py
git commit -m "feat(db): job_profile 幂等加列迁移，老库不重建即可升级

tasks.md 1.1/1.2。CREATE TABLE IF NOT EXISTS 对已存在的表无效，.51 上
data/demo.db 有 15 个真实 job 且部署脚本不重建库，只改 CREATE TABLE 是一个
上线后才炸、且只在服务器上炸的静默故障（design.md 决策 10）。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: `LLMGateway` 把 latency 与 response_model 透出给调用方（tasks 1.3）

**Files:**
- Modify: `app/llm/gateway.py:113-125`（`AuditHook` —— **只读，不改**）、`app/llm/gateway.py:160-222`（`extract_structured`）
- Test: `tests/test_llm_gateway.py`（追加）

**Interfaces:**
- Consumes: 无（本任务不依赖 Task 1）
- Produces:
  - `app.llm.gateway.LLMCallMeta` —— frozen dataclass，字段 `latency_ms: float` / `response_model: str | None` / `attempts: int`
  - `LLMGateway.extract_structured_with_meta(*, system_prompt: str, user_prompt: str, schema: type[T], prompt_version: str = "v1") -> tuple[T, LLMCallMeta]`
  - `LLMGateway.extract_structured(...) -> T` —— 签名与行为不变（`app/agents/jd_agent.py` 与 `scripts/compare_models.py` 继续用它）

**硬约束**：`AuditHook` Protocol 一个字符都不能改。`ai-audit-trail-and-outbound-gate` 正基于现签名设计，改它会制造冲突（design.md 决策 9）。所以时序不走 hook，走**返回值**。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_llm_gateway.py` 末尾（复用文件里已有的 `FakeOpenAIClient`）：

```python
def test_extract_structured_with_meta_returns_latency_and_response_model():
    class Payload(BaseModel):
        a: int

    client = FakeOpenAIClient(
        [json.dumps({"a": 1})], response_model="deepseek-chat-241226"
    )
    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat",  # 配置里写的是会漂移的别名
        supports_json_schema=False,
        client=client,
    )

    parsed, meta = gateway.extract_structured_with_meta(
        system_prompt="sys", user_prompt="user", schema=Payload
    )

    assert parsed.a == 1
    assert meta.latency_ms >= 0
    # 铁律 5：配置里写的名字不算数，响应返回的才算
    assert meta.response_model == "deepseek-chat-241226"
    assert meta.attempts == 1


def test_meta_latency_accumulates_across_retries(monkeypatch):
    """
    intake-turn-observability「重试计入耗时」：调用方要落库的是"这一轮用户等了
    多久"，不是"最后那次成功的尝试花了多久"。
    """
    from app.llm import gateway as gateway_module

    class Payload(BaseModel):
        a: int

    # (start, end) × 2 次尝试：第一次 1.5s，第二次 2.25s
    ticks = iter([0.0, 1.5, 10.0, 12.25])
    monkeypatch.setattr(gateway_module.time, "monotonic", lambda: next(ticks))

    client = FakeOpenAIClient(["这不是 JSON", json.dumps({"a": 1})])
    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=client,
    )

    _parsed, meta = gateway.extract_structured_with_meta(
        system_prompt="sys", user_prompt="user", schema=Payload
    )

    assert meta.attempts == 2
    assert meta.latency_ms == pytest.approx(3750.0)


def test_audit_hook_still_records_per_attempt_with_unchanged_signature(monkeypatch):
    """
    时序改走返回值而不是 hook：AuditHook 的签名与"每次尝试记一条"的语义都不动
    （design.md 决策 9——ai-audit-trail-and-outbound-gate 正基于现签名设计）。
    """
    from app.llm import gateway as gateway_module

    class Payload(BaseModel):
        a: int

    ticks = iter([0.0, 1.5, 10.0, 12.25])
    monkeypatch.setattr(gateway_module.time, "monotonic", lambda: next(ticks))

    class RecordingHook:
        def __init__(self):
            self.calls = []

        def record(self, **kwargs):
            self.calls.append(kwargs)

    hook = RecordingHook()
    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        audit_hook=hook,
        client=FakeOpenAIClient(["这不是 JSON", json.dumps({"a": 1})]),
    )

    _parsed, meta = gateway.extract_structured_with_meta(
        system_prompt="sys", user_prompt="user", schema=Payload
    )

    assert len(hook.calls) == 2  # 每次尝试各一条，语义不变
    # hook 记的是单次尝试耗时；累计只在返回值里，两者不互相污染
    assert [call["latency_ms"] for call in hook.calls] == pytest.approx([1500.0, 2250.0])
    assert meta.latency_ms == pytest.approx(3750.0)
    assert set(hook.calls[0]) == {
        "model",
        "response_model",
        "system_fingerprint",
        "prompt_version",
        "input_hash",
        "raw_response",
        "token_usage",
        "latency_ms",
    }


def test_extract_structured_still_returns_bare_model():
    """jd_agent 与 scripts/compare_models.py 不关心时序，旧签名必须原样可用。"""

    class Payload(BaseModel):
        a: int

    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=FakeOpenAIClient([json.dumps({"a": 1})]),
    )

    parsed = gateway.extract_structured(
        system_prompt="sys", user_prompt="user", schema=Payload
    )

    assert parsed.a == 1
    assert not isinstance(parsed, tuple)
```

文件顶部若还没有 `from pydantic import BaseModel`，加上（`pytest` 与 `json` 已有）。

- [ ] **Step 2: 跑测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_llm_gateway.py -k "meta or per_attempt or bare_model" -v`
Expected: FAIL —— `AttributeError: 'LLMGateway' object has no attribute 'extract_structured_with_meta'`

- [ ] **Step 3: 实现**

在 `app/llm/gateway.py` 的 `NoopAuditHook` 之后、`class LLMGateway` 之前插入：

```python
@dataclass(frozen=True)
class LLMCallMeta:
    """
    一次 extract_structured 调用的可观测元数据。

    为什么走返回值而不是扩展 AuditHook：AuditHook 的签名不能动
    （design.md 决策 9——ai-audit-trail-and-outbound-gate 正基于现签名设计），
    而调用方（compute_intake_turn → effect_persist_draft）需要在**同一个事务**
    里把耗时和画像一起写下去，hook 是单向的、拿不回来。

    只承载"这次调用花了多久、真正回答的是哪个模型"。prompt 版本、input_hash、
    原始响应仍然只经 AuditHook 走——intake-turn-observability 明确要求时序留痕
    不承担审计职责。
    """

    latency_ms: float
    response_model: str | None
    attempts: int
```

文件顶部补 `from dataclasses import dataclass`。

把 `extract_structured` 整体替换为下面两个方法（`_call_model` 不动）：

```python
    def extract_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[T],
        prompt_version: str = "v1",
    ) -> T:
        """原签名保留：不关心时序的调用方（jd_agent、scripts/compare_models.py）继续用这个。"""
        parsed, _meta = self.extract_structured_with_meta(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            prompt_version=prompt_version,
        )
        return parsed

    def extract_structured_with_meta(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[T],
        prompt_version: str = "v1",
    ) -> tuple[T, LLMCallMeta]:
        input_hash = hashlib.sha256(
            f"{system_prompt}\n{user_prompt}".encode("utf-8")
        ).hexdigest()

        last_error: Exception | None = None
        attempts = self._max_retries + 1
        total_latency_ms = 0.0

        for attempt_index in range(attempts):
            started = time.monotonic()
            response = self._call_model(system_prompt, user_prompt, schema)
            latency_ms = (time.monotonic() - started) * 1000
            # 累计而不是覆盖：调用方落库的是"这一轮用户等了多久"，重试的时间
            # 用户也在等（intake-turn-observability「重试计入耗时」）。
            # AuditHook 那边继续按单次尝试记录，两个口径互不污染。
            total_latency_ms += latency_ms
            raw_content = response.choices[0].message.content

            # 铁律 5（2026-08-09 现行版）：response.model 是 API 实际返回的模型标识，
            # 与构造函数传入的配置值 self._model 分开记录——配置里写的名字不算数，
            # 供应商静默升级 deepseek-chat 这类别名时，只有响应里的值可信。
            response_model = getattr(response, "model", None)

            # response.model 只是回显请求里的别名，供应商换掉别名底下的实际模型时
            # 它照样原样返回，证明不了版本没变。system_fingerprint（OpenAI 兼容
            # API 的惯例字段，随底层模型/部署变化）才是目前唯一能盯出漂移的信号。
            # 不是所有供应商都带这个字段，缺失时老实记 None，不能让网关炸掉。
            system_fingerprint = getattr(response, "system_fingerprint", None)

            usage = getattr(response, "usage", None)
            token_usage = (
                {
                    "prompt_tokens": getattr(usage, "prompt_tokens", None),
                    "completion_tokens": getattr(usage, "completion_tokens", None),
                }
                if usage is not None
                else {}
            )

            self._audit_hook.record(
                model=self._model,
                response_model=response_model,
                system_fingerprint=system_fingerprint,
                prompt_version=prompt_version,
                input_hash=input_hash,
                raw_response=raw_content,
                token_usage=token_usage,
                latency_ms=latency_ms,
            )

            try:
                data = json.loads(raw_content)
                parsed = schema.model_validate(data)
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
                continue

            return parsed, LLMCallMeta(
                latency_ms=total_latency_ms,
                response_model=response_model,
                attempts=attempt_index + 1,
            )

        raise SchemaExtractionFailed(
            f"{attempts} 次尝试后仍未通过 Schema 校验（{schema.__name__}）: {last_error}"
        ) from last_error
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_llm_gateway.py -v 2>&1 | tail -5`
Expected: 全绿，含 4 条新测试

- [ ] **Step 5: 全量回归**

Run: `./venv/bin/python -m pytest -q 2>&1 | tail -3`
Expected: `100 passed`

- [ ] **Step 6: 提交**

```bash
git add app/llm/gateway.py tests/test_llm_gateway.py
git commit -m "feat(llm): 透出 LLMCallMeta（累计耗时 + 响应模型标识），AuditHook 签名不动

tasks.md 1.3。latency_ms 与 response_model 本来就已经算出/取回，只是被
NoopAuditHook 丢掉。改走返回值而非扩展 AuditHook：后者的签名被
ai-audit-trail-and-outbound-gate 依赖（design.md 决策 9）。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: `IntakeQuestion` 对象、`question_id` 派生、唯一渲染函数（tasks 2.1 / 2.3 / 2.5 的载体部分）

**Files:**
- Create: `app/agents/intake_question.py`
- Test: `tests/test_intake_question.py`（新建）

**Interfaces:**
- Consumes: 无
- Produces（第 3、4、5 章都从这里取，签名在本任务定死）：
  - `IntakeQuestion` —— `@dataclass(frozen=True)`，字段 `text: str` / `question_id: str` / `field: str | None = None` / `options: tuple[str, ...] = ()` / `allow_free_text: bool = True` / `is_reask: bool = False`；方法 `to_payload(self) -> dict`、类方法 `from_payload(cls, payload: dict) -> IntakeQuestion`
  - `derive_question_id(field: str | None, text: str) -> str`
  - `render_questions_text(questions: list[IntakeQuestion]) -> str` —— **唯一**的问题→文本渲染入口
  - `normalize_question_payload(payload: dict) -> dict`

**为什么单独一个模块**：`intake_agent.py` 已 234 行，第 3、5、7 章都要往里加东西。问题对象的契约被 graph、web、前端三处消费，放在 agent 文件里会让"改一个渲染细节"牵动整个 agent 的 review 面。

**为什么 `question_id` 由系统派生而不让模型给**（design.md 决策 2）：跨轮复用同一个 id 要求模型记住历史 id 表，这正是 `temperature=0` 下也不可靠的那类要求，而"稳定标识"是 `intake-question-tracking` 的地基——地基不能建在模型自觉上。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_intake_question.py`：

```python
from app.agents.intake_question import (
    IntakeQuestion,
    derive_question_id,
    normalize_question_payload,
    render_questions_text,
)


def test_question_id_is_the_target_field():
    """一个字段最多同时挂一个未答问题，所以 id 就是字段名（design.md 决策 2）。"""
    assert derive_question_id("functional_safety", "要哪个 ASIL 等级？") == "functional_safety"


def test_question_id_ignores_wording_when_field_present():
    """换措辞不改标识（intake-question-tracking「换措辞不改变标识」）。"""
    first = derive_question_id("functional_safety", "是否有功能安全等级要求？")
    second = derive_question_id("functional_safety", "这个岗位需要 ASIL 几？")
    assert first == second


def test_question_id_falls_back_to_text_hash_when_field_missing():
    question_id = derive_question_id(None, "具体车型与量产时间是？")
    assert question_id.startswith("free:")
    assert derive_question_id("", "具体车型与量产时间是？") == question_id


def test_fallback_id_ignores_whitespace_differences():
    assert derive_question_id(None, "具体车型与量产时间是？") == derive_question_id(
        None, " 具体车型 与量产时间是？ "
    )


def test_fallback_id_changes_when_wording_changes():
    """
    降级不是等价方案，这条测试把代价写下来：field 缺失时换措辞就追踪不到了。
    第 5 章的重问追踪只对拿得到 field 的问题成立。
    """
    assert derive_question_id(None, "车型是？") != derive_question_id(None, "哪个车型？")


def test_payload_round_trip():
    question = IntakeQuestion(
        text="要哪个 ASIL 等级？",
        question_id="functional_safety",
        field="functional_safety",
        options=("ASIL-B", "ASIL-D", "无要求"),
        allow_free_text=True,
        is_reask=True,
    )

    restored = IntakeQuestion.from_payload(question.to_payload())

    assert restored == question
    assert question.to_payload()["options"] == ["ASIL-B", "ASIL-D", "无要求"]  # JSON 友好


def test_from_payload_fills_missing_id_and_defaults():
    restored = IntakeQuestion.from_payload({"text": "招几个人？", "field": "headcount"})

    assert restored.question_id == "headcount"
    assert restored.options == ()
    assert restored.allow_free_text is True
    assert restored.is_reask is False


def test_render_questions_text_joins_with_newline():
    questions = [
        IntakeQuestion(text="A？", question_id="a"),
        IntakeQuestion(text="B？", question_id="b"),
    ]
    assert render_questions_text(questions) == "A？\nB？"


def test_render_questions_text_marks_reask():
    """
    重问标注的渲染在这里就位，但本单元不会有 is_reask=True 的问题产生
    （判定属第 5 章 tasks 5.4）。先放渲染是为了让第 5 章只改判定、不动渲染。
    """
    questions = [IntakeQuestion(text="是否需要 ISO 26262？", question_id="functional_safety", is_reask=True)]
    rendered = render_questions_text(questions)
    assert "是否需要 ISO 26262？" in rendered
    assert rendered != "是否需要 ISO 26262？"  # 带了可见的重问前缀


def test_render_empty_questions_is_empty_string():
    assert render_questions_text([]) == ""


def test_normalize_question_payload_upgrades_legacy_string_list():
    """
    .51 现网 data/demo.db 的 outbox 里存着 2026-08-18 及之前写下的裸字符串问题。
    GET /api/jobs/{id} 会把这些历史行原样读回来当响应，新前端按对象访问
    q.text 会在真实数据上直接崩——和 design.md 决策 10 同一类"只在服务器上炸"
    的坑：本地测试库全是新写的行，永远走不到这条路径。
    """
    legacy = {"questions": ["是否涉及 AUTOSAR？", "MCU 平台族是？"]}

    normalized = normalize_question_payload(legacy)

    assert [q["text"] for q in normalized["questions"]] == [
        "是否涉及 AUTOSAR？",
        "MCU 平台族是？",
    ]
    assert all(q["question_id"] for q in normalized["questions"])
    assert all(q["options"] == [] for q in normalized["questions"])
    assert normalized["questions_text"] == "是否涉及 AUTOSAR？\nMCU 平台族是？"


def test_normalize_question_payload_preserves_other_keys_and_is_idempotent():
    payload = {
        "questions": [{"text": "招几个人？", "field": "headcount", "options": ["1", "2-3"]}],
        "unspecified_fields": ["toolchain"],
    }

    once = normalize_question_payload(payload)
    twice = normalize_question_payload(once)

    assert once == twice
    assert once["unspecified_fields"] == ["toolchain"]
    assert once["questions"][0]["question_id"] == "headcount"
    assert once["questions"][0]["options"] == ["1", "2-3"]


def test_normalize_question_payload_handles_missing_questions_key():
    normalized = normalize_question_payload({"type": "confirmation_prompt"})
    assert normalized["questions"] == []
    assert normalized["questions_text"] == ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_intake_question.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.agents.intake_question'`

- [ ] **Step 3: 实现**

新建 `app/agents/intake_question.py`：

```python
from __future__ import annotations

import hashlib
from dataclasses import dataclass

# 重问前缀：让"这个你刚才没答"在文本通道里也看得见。判定 is_reask 属第 5 章
# （tasks 5.4），本模块只负责一旦置了标记就渲染出来。
_REASK_PREFIX = "（这个你刚才没答）"

# field 缺失时的 id 前缀。带前缀是为了在库里一眼看出"这个问题没有目标字段"，
# 而不是让它和真实字段名混在一起。
_FREE_ID_PREFIX = "free:"


def derive_question_id(field: str | None, text: str) -> str:
    """
    question_id 由系统按目标字段派生，不让模型自己编（design.md 决策 2）。

    field 缺失时退回文本哈希。代价必须说清楚：文本一变 id 就变，这类问题
    换措辞之后追踪不到——所以"没有 field"是降级，不是等价方案。第 5 章的
    重问追踪只对拿得到 field 的问题成立。

    同一字段的两个递进问题（"要不要 ISO 26262" 与 "要哪个 ASIL 等级"）会撞
    id，撞了就按"重问"处理，这是 design.md 决策 2 已经评估并接受的近似；
    重问次数上限取 2 就是给这种递进留的余量。
    """
    normalized_field = (field or "").strip()
    if normalized_field:
        return normalized_field
    digest = hashlib.sha256("".join(str(text).split()).encode("utf-8")).hexdigest()[:8]
    return f"{_FREE_ID_PREFIX}{digest}"


@dataclass(frozen=True)
class IntakeQuestion:
    """
    一个可独立作答的追问，是贯穿 agent → graph → API → 前端的一等对象
    （design.md 决策 1）。后续接企微卡片只需换渲染层。

    除 text 外全部可空或有默认值：模型退化成只会给一句问题文本时，系统降级成
    "纯文本问题"，绝不因为缺 field/options 就报错（design.md 风险表第 1 条）。

    frozen=True 是刻意的——问题对象在 graph 里被多个节点读到，可变对象会让
    "谁改了它"变成一个需要排查的问题。
    """

    text: str
    question_id: str
    field: str | None = None
    options: tuple[str, ...] = ()
    allow_free_text: bool = True
    is_reask: bool = False

    def to_payload(self) -> dict:
        """转成 JSON 友好的 dict。options 用 list 而不是 tuple：这个 dict 会被
        json.dumps 写进 outbox，也会进 LangGraph checkpoint。"""
        return {
            "question_id": self.question_id,
            "text": self.text,
            "field": self.field,
            "options": list(self.options),
            "allow_free_text": self.allow_free_text,
            "is_reask": self.is_reask,
        }

    @classmethod
    def from_payload(cls, payload: dict) -> "IntakeQuestion":
        text = str(payload.get("text", ""))
        field = payload.get("field") or None
        return cls(
            text=text,
            question_id=str(payload.get("question_id") or derive_question_id(field, text)),
            field=field,
            options=tuple(payload.get("options") or ()),
            allow_free_text=bool(payload.get("allow_free_text", True)),
            is_reask=bool(payload.get("is_reask", False)),
        )


def render_questions_text(questions: list[IntakeQuestion]) -> str:
    """
    问题 → 文本的**唯一**渲染入口。

    为什么必须唯一（design.md 决策 1「代价」）：写进 conversation/history 的
    assistant 文本、以及下发给通道的文本，如果各渲染一遍，
    _repeats_earlier_assistant_turn 就会拿"历史里的那一版"去比对"实际下发的
    另一版"，逐字比对静默失效——而它现在是重复追问的最后一道防线。

    本单元不把 options 渲进文本：第 2 章的自我约束是"只换载体、用户可见行为
    与合并前一致"，选项的可点选控件与"AI 建议选项"标识属第 4 章
    （tasks 4.1/4.3，后者是《AI 生成合成内容标识办法》的要求）。
    """
    lines = []
    for question in questions:
        prefix = _REASK_PREFIX if question.is_reask else ""
        lines.append(f"{prefix}{question.text}")
    return "\n".join(lines)


def normalize_question_payload(payload: dict) -> dict:
    """
    把任意历史形态的 question payload 归一化成结构化形态。

    为什么需要：.51 现网 data/demo.db 的 outbox 里存着 2026-08-18 及之前写下的
    {"questions": ["问题文本", ...]}（裸字符串）。GET /api/jobs/{id} 会把这些
    历史行原样读回来当响应体，新前端按对象访问 q.text 会在真实数据上直接崩。
    与 design.md 决策 10（老库加列）同一类的坑：本地测试库全是新写的行，
    永远走不到这条路径，所以必须专门测。

    幂等：已经是新形态的 payload 过一遍不变。
    """
    raw = payload.get("questions") or []
    questions = [
        IntakeQuestion.from_payload({"text": item} if isinstance(item, str) else item)
        for item in raw
    ]
    normalized = {**payload, "questions": [q.to_payload() for q in questions]}
    normalized.setdefault("questions_text", render_questions_text(questions))
    return normalized
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_intake_question.py -v`
Expected: PASS ×13

- [ ] **Step 5: 全量回归 + 提交**

Run: `./venv/bin/python -m pytest -q 2>&1 | tail -3`
Expected: `113 passed`

```bash
git add app/agents/intake_question.py tests/test_intake_question.py
git commit -m "feat(intake): IntakeQuestion 对象、question_id 派生与唯一渲染入口

tasks.md 2.1/2.3/2.5。question_id 由系统按 field 派生而不让模型自己编
（design.md 决策 2）；渲染函数唯一，否则 _repeats_earlier_assistant_turn
会拿历史文本比对实际下发的另一版，逐字比对静默失效。
normalize_question_payload 兼容 .51 现网 outbox 里的裸字符串历史行。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: 结构化问题贯通 agent 与 compute 节点（tasks 2.2 / 2.3 / 2.4 / 2.5 / 1.4 前半）

**Files:**
- Modify: `app/agents/intake_agent.py`（`SYSTEM_PROMPT` 尾段、`_IntakeTurnSchema`、`IntakeTurnResult`、`_repeats_earlier_assistant_turn`、`run_intake_turn`）
- Test: `tests/test_intake_agent.py`（改断言 + 新增）

**Interfaces:**
- Consumes: `IntakeQuestion` / `derive_question_id` / `render_questions_text`（Task 3）、`LLMGateway.extract_structured_with_meta`（Task 2）
- Produces:
  - `IntakeTurnResult.questions: list[IntakeQuestion]`（**类型变了**）
  - `IntakeTurnResult.questions_text: str` —— 已渲染的文本，`compute_intake_turn` 直接用，不许自己再渲染一遍
  - `IntakeTurnResult.llm_latency_ms: float = 0.0`、`IntakeTurnResult.llm_response_model: str | None = None`
  - `_repeats_earlier_assistant_turn(candidate_text: str, history: list[dict]) -> bool`（**第一个参数从 `list[str]` 改为已渲染文本**）
  - `prompt_version` 从 `"intake-v2"` 升到 `"intake-v3"`
  - `IntakeState["pending_questions"]: list[dict]` —— **每项是 `IntakeQuestion.to_payload()` 的结果，不是 `IntakeQuestion` 实例**
  - `IntakeState["turn_started_at"]: str` —— 由调用方（`app/web/server.py`，Task 5）写入，节点只透传
  - `IntakeState["llm_latency_ms"]: float` —— 由 `compute_intake_turn` 写入，`effect_persist_draft`（Task 5）消费

**关键事实（别按错的前提写代码）**：`_IntakeTurnSchema` 含 `profile_patch: dict`（自由 object），`_has_free_form_object` 会命中，因此这个 schema 在真实调用里**始终走 json_object 模式**，strict json_schema 那条路走不到（`tests/test_llm_gateway.py::test_free_form_dict_schema_falls_back_to_json_object_mode` 已经钉住这个事实）。json_object 模式下形状约束靠 `schema.model_json_schema()` 写进 system prompt，而嵌套模型在那份 schema 里是 `$defs` + `$ref` 形态——模型得自己解引用。**所以两件事都要做**：(a) 在 `SYSTEM_PROMPT` 里用中文把问题对象的形状写清楚，别只指望 `$ref`；(b) 加裸字符串降级校验器，因为模型退回字符串数组是真实会发生的事。

**为什么 agent 与 compute 节点必须同一个 Task**：`IntakeTurnResult.questions` 的类型一变，`compute_intake_turn` 里的 `"\n".join(result.questions)` 立刻 `TypeError`，整条图挂掉——把这两处拆成两个 Task，中间那次提交会留下 35 个失败用例。**每个 Task 结束时测试必须全绿**，这是 Task 边界的划法依据。

**为什么 state 里放 dict 而不是 dataclass**：`IntakeState` 会被 `SqliteSaver` 序列化进 checkpoint。放纯 dict 的往返语义是确定的（json/msgpack 都认），放 dataclass 则依赖序列化器的实现细节——而"重放后类型变了"是那种只在恢复路径上炸的故障。

**为什么 `llm_response_model` 不进 state**：`intake-turn-observability` 明确要求「时序留痕不承担审计职责……不记录模型标识」。模型标识由第 7 章按 `intake-field-grounding` 的口径落库；本任务把它透到 `IntakeTurnResult` 就到此为止。

- [ ] **Step 1: 改已有断言（类型变了，先让现有测试表达新契约）**

`tests/test_intake_agent.py` 四处断言要改（其余测试喂的是裸字符串输入，靠降级校验器原样通过，不用动）：

| 行 | 现在 | 改成 |
|---|---|---|
| 87 | `assert result.questions == ["是否涉及 AUTOSAR？"]` | `assert [q.text for q in result.questions] == ["是否涉及 AUTOSAR？"]` |
| 134 | `assert len(result.questions) == 3` | 不变（长度断言仍然成立） |
| 245 | `assert result.questions == ["招聘人数是？"]` | `assert [q.text for q in result.questions] == ["招聘人数是？"]` |
| 426 | `assert len(result.questions) == 2` | 不变 |

第 113、175、216 行的 `assert result.questions == []` 不用动（空列表两种类型下都相等）。

另外两个文件各有一处断言要同步改成过渡形态（`_deliver_node` 的 payload 此刻变成了 dict 列表，`questions_text` 由 Task 6 补）：

| 文件:行 | 现在 | 改成 |
|---|---|---|
| `tests/test_graph_nodes.py:82` | `assert new_state["pending_questions"] == ["是否涉及 AUTOSAR？"]` | `assert [q["text"] for q in new_state["pending_questions"]] == ["是否涉及 AUTOSAR？"]` |
| `tests/test_graph_nodes.py:178` | `assert latest.payload["questions"] == ["是否涉及 AUTOSAR？"]` | `assert [q["text"] for q in latest.payload["questions"]] == ["是否涉及 AUTOSAR？"]` |
| `tests/test_web_api.py:101` | `assert body["message"]["payload"]["questions"] == ["是否涉及 AUTOSAR？"]` | `assert [q["text"] for q in body["message"]["payload"]["questions"]] == ["是否涉及 AUTOSAR？"]` |


- [ ] **Step 2: 写新增的失败测试**

先把文件顶部的 fake 客户端补上 `model` 字段（现在的 `FakeResponse` 没有它，`getattr(response, "model", None)` 永远返回 None，测不了 `response_model` 透传）：

```python
@dataclass
class FakeResponse:
    choices: list[FakeChoice]
    usage: object = None
    model: str | None = None


class FakeChatCompletions:
    def __init__(self, responses: list[str], response_model: str | None = None):
        self._responses = list(responses)
        self._response_model = response_model
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content = self._responses.pop(0)
        return FakeResponse(
            choices=[FakeChoice(message=FakeMessage(content=content))],
            model=self._response_model,
        )


class FakeChat:
    def __init__(self, responses, response_model: str | None = None):
        self.completions = FakeChatCompletions(responses, response_model=response_model)


class FakeOpenAIClient:
    def __init__(self, responses, response_model: str | None = None):
        self.chat = FakeChat(responses, response_model=response_model)


def make_gateway(responses: list[str], response_model: str | None = None) -> LLMGateway:
    return LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=FakeOpenAIClient(responses, response_model=response_model),
    )
```

然后追加到文件末尾：

```python
def test_plain_string_questions_degrade_to_text_only_questions():
    """
    模型退化成只给一句文本时降级，而不是校验失败重试三次
    （design.md 风险表第 1 条）。这条路径是真实会走到的：本 schema 含自由
    dict（profile_patch），网关始终走 json_object 模式，供应商不校验形状。
    """
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": ["是否涉及 AUTOSAR？"],
                    "profile_patch": {},
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway, history=[{"role": "user", "content": "要个做嵌入式开发的"}], round_count=0
    )

    assert len(result.questions) == 1
    question = result.questions[0]
    assert question.text == "是否涉及 AUTOSAR？"
    assert question.field is None
    assert question.options == ()
    assert question.allow_free_text is True
    assert question.is_reask is False
    assert question.question_id.startswith("free:")


def test_structured_question_carries_field_and_options():
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [
                        {
                            "text": "要哪个 ASIL 等级？",
                            "field": "functional_safety",
                            "options": ["ASIL-B", "ASIL-D", "无"],
                            "allow_free_text": True,
                        }
                    ],
                    "profile_patch": {},
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway, history=[{"role": "user", "content": "要个做功能安全的"}], round_count=0
    )

    question = result.questions[0]
    assert question.question_id == "functional_safety"
    assert question.field == "functional_safety"
    assert question.options == ("ASIL-B", "ASIL-D", "无")


def test_question_id_is_derived_by_system_even_if_model_supplies_one():
    """模型自己编的 id 必须被丢弃（design.md 决策 2）。"""
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [
                        {
                            "text": "招几个人？",
                            "field": "headcount",
                            "question_id": "模型自己编的-q1",
                        }
                    ],
                    "profile_patch": {},
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway, history=[{"role": "user", "content": "要个嵌入式"}], round_count=0
    )

    assert result.questions[0].question_id == "headcount"


def test_questions_text_comes_from_the_single_renderer():
    """
    history 里的 assistant 文本与下发给通道的文本必须同源
    （design.md 决策 1「代价」）。这条测试锁住"result 自带渲染结果"，
    让 compute_intake_turn 没有理由自己再 join 一遍。
    """
    from app.agents.intake_question import render_questions_text

    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [
                        {"text": "要哪个 ASIL 等级？", "field": "functional_safety"},
                        {"text": "招几个人？", "field": "headcount"},
                    ],
                    "profile_patch": {},
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway, history=[{"role": "user", "content": "要个嵌入式"}], round_count=0
    )

    assert result.questions_text == render_questions_text(result.questions)
    assert result.questions_text == "要哪个 ASIL 等级？\n招几个人？"


def test_result_carries_llm_latency_and_response_model():
    """
    铁律 5：配置里写的名字不算数，响应返回的才算。本单元把它透到 agent 层
    （第 7 章才落库），时序则由第 6 章落库。
    """
    gateway = make_gateway(
        [
            json.dumps(
                {"is_job_related": True, "questions": [], "profile_patch": {"headcount": 2}}
            )
        ],
        response_model="deepseek-chat-241226",
    )

    result = run_intake_turn(
        gateway, history=[{"role": "user", "content": "要两个嵌入式"}], round_count=0
    )

    assert result.llm_latency_ms >= 0
    assert result.llm_response_model == "deepseek-chat-241226"


def test_system_prompt_requires_one_answerable_subquestion_per_item():
    """
    tasks 2.4：一个问题条目只能承载一个可独立作答的子问题，且要给反例。
    反例用真实事故里的那一对（2494103e 第 3 轮把 IATF 16949 与 ISO 26262
    打包成一句，用户只答了前者，第 4 轮被换措辞重问）。
    """
    assert "只能承载一个" in SYSTEM_PROMPT
    assert "IATF 16949" in SYSTEM_PROMPT
    assert "ISO 26262" in SYSTEM_PROMPT
    # 问题对象的形状要用中文写清楚：json_object 模式下嵌套模型在 schema 里是
    # $ref，不能只指望模型自己解引用
    assert "allow_free_text" in SYSTEM_PROMPT
    assert "question_id" in SYSTEM_PROMPT  # 明确告诉模型不要自己编 id
```

- [ ] **Step 3: 跑测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_intake_agent.py -v 2>&1 | tail -15`
Expected: 新增 6 条全 FAIL（`AttributeError: 'IntakeTurnResult' object has no attribute 'questions_text'` / `AssertionError` on `question_id`），已改的 4 处断言也 FAIL

- [ ] **Step 4: 实现**

`app/agents/intake_agent.py`：

导入段改为（新增两行）：

```python
from pydantic import BaseModel, field_validator

from app.agents.ecu_knowledge import FOLLOWUP_RULES, match_ambiguous_terms
from app.agents.intake_question import IntakeQuestion, derive_question_id, render_questions_text
from app.llm.gateway import LLMGateway
from app.schemas.job_profile import JobProfile
```

`SYSTEM_PROMPT` 的最后一段（现在的 `"输出 JSON，字段：..."`）替换为：

```python
    "【追问的拆分规则】questions 里一个条目**只能承载一个可独立作答的子问题**。"
    "反例：「是否需要熟悉 IATF 16949 或 ISO 26262？」——这是两个独立要求，"
    "用户只会答其中一个，另一个就永远悬着。必须拆成两条："
    "「是否要求熟悉 IATF 16949？」与「是否要求熟悉 ISO 26262？」。\n"
    "\n"
    "【追问的字段形状】questions 的每一项是一个对象：\n"
    "- text（必填，字符串）：问给用户看的那句话\n"
    "- field（选填，字符串）：这个问题想补全上面字段表里的哪个字段名；"
    "拿不准就留 null，不要硬填一个不存在的字段名\n"
    "- options（选填，字符串数组）：可供用户直接选择的具体档位；"
    "没有可枚举档位的问题（如「具体车型与量产时间」）留空数组\n"
    "- allow_free_text（选填，布尔）：是否允许用户自由文本作答，默认 true\n"
    "不要输出 question_id，那个由系统按 field 派生；你自己编的 id 会被丢弃。\n"
    "\n"
    "输出 JSON，字段：is_job_related(bool), questions(上述问题对象的数组), "
    "profile_patch(object), unspecified_fields(string[], 可选)。"
```

`_IntakeTurnSchema` 与新的 `_IntakeQuestionSchema`：

```python
class _IntakeQuestionSchema(BaseModel):
    """
    模型侧的问题形状。**不含 question_id / is_reask**——那两个由系统派生与判定
    （design.md 决策 2），放进模型 schema 等于邀请模型自己编 id。
    """

    text: str
    field: str | None = None
    options: list[str] = []
    allow_free_text: bool = True


class _IntakeTurnSchema(BaseModel):
    is_job_related: bool
    questions: list[_IntakeQuestionSchema] = []
    profile_patch: dict = {}
    unspecified_fields: list[str] = []

    @field_validator("questions", mode="before")
    @classmethod
    def _tolerate_plain_strings(cls, value):
        """
        模型只给一句文本时降级成纯文本问题，而不是校验失败、重试三次、
        最后抛 SchemaExtractionFailed 把整轮采集废掉（design.md 风险表第 1 条）。

        这条路径是真实会走到的：本 schema 含自由 dict（profile_patch），
        _has_free_form_object 命中后网关始终走 json_object 模式，供应商只保证
        "是合法 JSON"、不校验形状（见 app/llm/gateway.py 的 _call_model）。

        只兜"整项是字符串"这一种退化。dict 里缺 text 之类的结构性错误仍然走
        既有的 SchemaExtractionFailed 重试路径——那是模型没按 schema 输出，
        重试一次比猜一个 text 更对。
        """
        if not isinstance(value, list):
            return value
        return [{"text": item} if isinstance(item, str) else item for item in value]
```

`IntakeTurnResult`：

```python
@dataclass
class IntakeTurnResult:
    is_job_related: bool
    questions: list[IntakeQuestion]
    profile_patch: dict
    is_complete: bool
    unspecified_fields: list[str] = field(default_factory=list)
    # 已渲染的问题文本。带在结果里而不是让调用方自己 join：history 里的
    # assistant 文本与下发给通道的文本必须同源（design.md 决策 1「代价」）。
    questions_text: str = ""
    # 本轮 LLM 累计耗时（含重试），由 effect_persist_draft 落库（第 1 章）。
    llm_latency_ms: float = 0.0
    # API 响应里实际返回的模型标识（铁律 5）。本单元只透出不落库——落库属
    # 第 7 章（字段溯源要按模型版本归因），而 intake-turn-observability 明确
    # 要求时序留痕不记模型标识。
    llm_response_model: str | None = None
```

`_repeats_earlier_assistant_turn` 的签名与首段改为（原有 docstring 的两段历史说明**保留不动**，在末尾追加一段）：

```python
def _repeats_earlier_assistant_turn(candidate_text: str, history: list[dict]) -> bool:
    """
    判断这轮生成的问题文本是否和历史上**任意一轮** assistant 说过的内容只有
    空白差异地相同。

    2026-08-10 真实环境试跑发现：用户回答模糊（如对"CP 还是 AP"这种二选一问题
    回答"是的"）时，profile_patch 常年提不出任何字段，ECU 知识库的追问建议又
    逐轮原样重新注入 prompt，模型在 temperature=0 下倾向于生成和上一轮几乎
    一字不差的问题——不能靠 MAX_ROUNDS 兜底，那之前每一轮都在把同一组问题
    原样再发一次给用户。

    2026-08-16 姚祖怡试跑反馈"重复问了同一件事情"，追查发现只比对"上一轮"不够：
    只要中间隔了一轮问别的，第 1 轮问过的问题在第 3 轮被模型重新问出来，跟
    "上一轮"（第 2 轮）文本不同，原先的检测完全看不到——比对范围改为历史上
    **所有** assistant 轮次，而不只是最后一轮。

    2026-08-19：入参从 list[str] 改为**已渲染的文本**，渲染由
    app/agents/intake_question.render_questions_text 唯一负责——两处各渲染一遍
    会让这里比对到与实际下发不一致的文本，逐字比对静默失效。

    这道防线**保留不动**。同期取证（docs/findings/2026-08-13-sqlite-事务归属冲突.md
    §8.5）证明"用户体感重复"还有第三种成因：投递丢失导致用户没收到上一轮回复，
    模型从 checkpoint 读到自己问过、便道歉并换措辞重问。那一层已由
    fix-sqlite-transaction-ownership 修复，与本函数无关。按 question_id 追踪
    未答子问题是 m1-intake-quality-fixes 第 5 章的事（tasks 5.8 给本函数去留的
    结论），本单元不动它的判定逻辑。
    """
    if not candidate_text:
        return False
    normalize = lambda s: "".join(str(s).split())
    candidate = normalize(candidate_text)
    earlier_assistant_turns = (
        turn.get("content", "") for turn in history if turn.get("role") == "assistant"
    )
    return any(candidate == normalize(turn) for turn in earlier_assistant_turns)
```

在 `_repeats_earlier_assistant_turn` 之后加两个辅助函数：

```python
_GUIDANCE_TEXT = "没听懂是不是用人需求，可以试试：'要招一个做XX的工程师'"


def _to_intake_questions(raw: list[_IntakeQuestionSchema]) -> list[IntakeQuestion]:
    """模型侧形状 → 系统侧一等对象。question_id 在这里派生，模型给的 id 拿不到
    这一步（_IntakeQuestionSchema 里根本没有那个字段，pydantic 默认忽略多余键）。"""
    return [
        IntakeQuestion(
            text=item.text,
            question_id=derive_question_id(item.field, item.text),
            field=item.field or None,
            options=tuple(item.options),
            allow_free_text=item.allow_free_text,
        )
        for item in raw
    ]


def _guidance_question() -> IntakeQuestion:
    return IntakeQuestion(
        text=_GUIDANCE_TEXT,
        question_id=derive_question_id(None, _GUIDANCE_TEXT),
    )
```

`run_intake_turn` 整体替换为：

```python
def run_intake_turn(
    gateway: LLMGateway,
    *,
    history: list[dict],
    round_count: int,
    profile_patch_accumulated: dict | None = None,
) -> IntakeTurnResult:
    user_prompt = _build_user_prompt(
        history, profile_patch_accumulated or {}, suggested_followups(history)
    )

    # extract_structured_with_meta 而不是 extract_structured：本轮的 LLM 累计
    # 耗时与实际响应模型标识要透给编排层落库（tasks 1.3）。AuditHook 的签名
    # 没有变（design.md 决策 9）。
    parsed, meta = gateway.extract_structured_with_meta(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        schema=_IntakeTurnSchema,
        # SYSTEM_PROMPT 改了就必须升版本：input_hash 与 prompt_version 是
        # "这条结果是哪一版提示词产出的"的唯一依据（铁律 5 的可解释性要求）。
        prompt_version="intake-v3",
    )

    if not parsed.is_job_related:
        questions = _to_intake_questions(parsed.questions) or [_guidance_question()]
        return IntakeTurnResult(
            is_job_related=False,
            questions=questions,
            profile_patch={},
            is_complete=False,
            questions_text=render_questions_text(questions),
            llm_latency_ms=meta.latency_ms,
            llm_response_model=meta.response_model,
        )

    at_round_limit = round_count >= MAX_ROUNDS
    capped_questions = (
        [] if at_round_limit else _to_intake_questions(parsed.questions)[:MAX_QUESTIONS_PER_ROUND]
    )

    stuck = not at_round_limit and _repeats_earlier_assistant_turn(
        render_questions_text(capped_questions), history
    )
    give_up = at_round_limit or stuck
    questions = [] if give_up else capped_questions

    return IntakeTurnResult(
        is_job_related=True,
        questions=questions,
        profile_patch=parsed.profile_patch,
        is_complete=give_up or not questions,
        unspecified_fields=parsed.unspecified_fields if give_up else [],
        questions_text=render_questions_text(questions),
        llm_latency_ms=meta.latency_ms,
        llm_response_model=meta.response_model,
    )
```

- [ ] **Step 5: 跑 agent 层测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_intake_agent.py -v 2>&1 | tail -5`
Expected: `20 passed`（原 14 条 + 新增 6 条）

- [ ] **Step 6: 写 compute 节点侧的失败测试**

追加到 `tests/test_graph_nodes.py` 末尾（文件顶部需要 `import sqlite3` 与 `import pytest`，若尚无就加）：


```python
def test_compute_intake_turn_emits_serializable_structured_questions():
    """
    state 会被 SqliteSaver 序列化进 checkpoint，所以 pending_questions 必须是
    纯 dict——放 dataclass 会让"重放后类型变了"成为只在恢复路径上炸的故障。
    """
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [
                        {
                            "text": "要哪个 ASIL 等级？",
                            "field": "functional_safety",
                            "options": ["ASIL-B", "ASIL-D", "无"],
                        }
                    ],
                    "profile_patch": {},
                }
            )
        ]
    )
    state = {
        "job_id": "job1",
        "history": [{"role": "user", "content": "要个做功能安全的"}],
        "round_count": 0,
        "profile_patch_accumulated": {},
    }

    new_state = compute_intake_turn(state, gateway=gateway)

    question = new_state["pending_questions"][0]
    assert isinstance(question, dict)
    assert question["question_id"] == "functional_safety"
    assert question["options"] == ["ASIL-B", "ASIL-D", "无"]
    assert question["is_reask"] is False
    # 整份 state 必须能 json 序列化，否则 checkpoint 写入会在运行时才炸
    json.dumps(new_state["pending_questions"], ensure_ascii=False)


def test_compute_intake_turn_carries_llm_latency():
    gateway = make_gateway(
        [
            json.dumps(
                {"is_job_related": True, "questions": [], "profile_patch": {"headcount": 2}}
            )
        ]
    )
    state = {
        "job_id": "job1",
        "history": [{"role": "user", "content": "要两个人"}],
        "round_count": 0,
        "profile_patch_accumulated": {},
    }

    new_state = compute_intake_turn(state, gateway=gateway)

    assert new_state["llm_latency_ms"] >= 0


def test_compute_intake_turn_passes_through_turn_started_at():
    """轮次起始时刻由 HTTP 层打（那才是"用户开始等"的时刻），节点不许改写它。"""
    gateway = make_gateway(
        [json.dumps({"is_job_related": True, "questions": [], "profile_patch": {}})]
    )
    state = {
        "job_id": "job1",
        "history": [{"role": "user", "content": "要个人"}],
        "round_count": 0,
        "profile_patch_accumulated": {},
        "turn_started_at": "2026-08-19 01:02:03",
    }

    new_state = compute_intake_turn(state, gateway=gateway)

    assert new_state["turn_started_at"] == "2026-08-19 01:02:03"


def test_assistant_history_text_equals_rendered_questions():
    """
    history 里的 assistant 文本必须来自唯一的渲染函数
    （design.md 决策 1「代价」）：这里和下发给通道的文本一旦分叉，
    _repeats_earlier_assistant_turn 就在比对一个从未下发过的字符串。
    """
    from app.agents.intake_question import IntakeQuestion, render_questions_text

    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [
                        {"text": "要哪个 ASIL 等级？", "field": "functional_safety"},
                        {"text": "招几个人？", "field": "headcount"},
                    ],
                    "profile_patch": {},
                }
            )
        ]
    )
    state = {
        "job_id": "job1",
        "history": [{"role": "user", "content": "要个做功能安全的"}],
        "round_count": 0,
        "profile_patch_accumulated": {},
    }

    new_state = compute_intake_turn(state, gateway=gateway)

    expected = render_questions_text(
        [IntakeQuestion.from_payload(q) for q in new_state["pending_questions"]]
    )
    assert new_state["history"][-1] == {"role": "assistant", "content": expected}
```

- [ ] **Step 7: 跑测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_graph_nodes.py -v 2>&1 | tail -12`
Expected: 大面积 FAIL —— `TypeError: sequence item 0: expected str instance, IntakeQuestion found`（`compute_intake_turn` 还在 `"\n".join(result.questions)`）

- [ ] **Step 8: 实现 state 与 compute 节点**

`app/graph/state.py` 的 `IntakeState` 尾部改为（原有 `history` 的长注释**保留不动**）：

```python
    round_count: int
    profile_patch_accumulated: dict

    # 每项是 IntakeQuestion.to_payload() 的结果（纯 dict），不是 IntakeQuestion
    # 实例：state 会被 SqliteSaver 序列化进 checkpoint，纯 dict 的往返语义是
    # 确定的，dataclass 则依赖序列化器实现细节——"重放后类型变了"是只在恢复
    # 路径上炸的故障。结构定义见 app/agents/intake_question.py。
    pending_questions: list[dict]

    is_complete: bool
    is_job_related: bool
    unspecified_fields: list[str]

    # 本轮开始时刻（HTTP 请求进入、还没调模型），由 app/web/server.py 的
    # _run_turn 写入，节点只透传。轮次**结束**时刻沿用 job_profile.created_at。
    turn_started_at: str

    # 本轮 LLM 累计耗时（含重试），由 compute_intake_turn 写入、
    # effect_persist_draft 与画像草案在同一条 INSERT 里落库。
    llm_latency_ms: float
```

`app/graph/nodes.py` 的 `compute_intake_turn` 尾部（现在的 `assistant_turn` 构造与 return）替换为：

```python
    # 把本轮助手说的话也记进历史，让下一轮的 prompt 是一段真正的对话，而不是
    # 一串没有上下文的用户独白——否则模型不知道上一轮已经问过什么。
    #
    # 文本直接用 result.questions_text，不在这里自己 join：渲染入口必须唯一
    # （app/agents/intake_question.render_questions_text），否则 history 里的
    # 文本和下发给通道的文本会分叉，_repeats_earlier_assistant_turn 就在比对
    # 一个从未真正下发过的字符串（design.md 决策 1「代价」）。
    assistant_turn = {
        "role": "assistant",
        "content": result.questions_text
        if result.questions
        else "（信息已收集完整，等待用人部门确认画像）",
    }

    return {
        **state,
        "history": [*history, assistant_turn],
        "is_job_related": result.is_job_related,
        "pending_questions": [question.to_payload() for question in result.questions],
        "profile_patch_accumulated": accumulated,
        "is_complete": result.is_complete,
        "round_count": state.get("round_count", 0) + 1,
        "unspecified_fields": result.unspecified_fields,
        # 时序：只放耗时，不放模型标识——intake-turn-observability 要求时序
        # 留痕不承担审计职责。模型标识由第 7 章按 intake-field-grounding 落库。
        "llm_latency_ms": result.llm_latency_ms,
    }
```

- [ ] **Step 9: 跑测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_graph_nodes.py tests/test_intake_agent.py -v 2>&1 | tail -5`
Expected: 全绿（`test_graph_nodes.py` 9 条：原 5 条 + 新增 4 条）

- [ ] **Step 10: 全量回归**

Run: `./venv/bin/python -m pytest -q 2>&1 | tail -3`
Expected: `123 passed` —— **必须全绿**。这一步是本任务边界的意义：agent 与 compute 节点一起改完，整条图才是自洽的。

- [ ] **Step 11: 提交**

```bash
git add app/agents/intake_agent.py app/graph/state.py app/graph/nodes.py \
        tests/test_intake_agent.py tests/test_graph_nodes.py tests/test_web_api.py
git commit -m "feat(intake): 追问改为结构化对象并贯通 compute 节点

tasks.md 2.2/2.3/2.4/2.5 + 1.4 前半。question_id 由系统按 field 派生，模型给的
id 丢弃。本 schema 含自由 dict（profile_patch）故始终走 json_object 模式，供应商
不校验形状，模型退回字符串数组是真实会发生的事——降级校验器兜住它，而不是让整轮
采集抛 SchemaExtractionFailed。pending_questions 存 to_payload() 的纯 dict
（checkpoint 序列化语义确定）；assistant history 文本取自唯一渲染函数。
prompt_version 升到 intake-v3。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: 时序两列与画像同一条 INSERT 落库（tasks 1.4 后半 / 1.5 / 1.6）

**Files:**
- Modify: `app/storage/db.py`（新增 `sqlite_utc_now()`）、`app/graph/nodes.py:53-86`（`effect_persist_draft`）、`app/web/server.py:69-98`（`_run_turn`）
- Test: `tests/test_graph_nodes.py`（新增 5 条）、`tests/test_web_api.py`（新增 1 条）

**Interfaces:**
- Consumes: Task 1 的新列与 `sqlite_utc_now()` 的落点、Task 4 的 `state["llm_latency_ms"]`
- Produces: `app.storage.db.sqlite_utc_now() -> str` —— 与 SQLite `datetime('now')` 完全一致格式的 UTC 时间串

**硬约束（铁律 1）**：不新增 effect 节点、不改 `business_key` 语义（仍是 `str(state["round_count"] - 1)`，见 `app/graph/build.py:53`）、不在 `effect_persist_draft` 里 `conn.commit()`。时序两列必须进**同一条 `INSERT`**——`intake-turn-observability` 要求"画像有这一轮、时序没有这一轮"不可能出现。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_graph_nodes.py`：

```python
def test_effect_persist_draft_writes_turn_timing_in_the_same_row(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.commit()

    effect_persist_draft(
        conn,
        thread_id="job1",
        business_key="0",
        state={
            "profile_patch_accumulated": {"job_title": "x"},
            "unspecified_fields": [],
            "turn_started_at": "2026-08-19 01:02:03",
            "llm_latency_ms": 8123.5,
        },
    )

    row = conn.execute(
        "SELECT turn_started_at, llm_latency_ms, created_at FROM job_profile WHERE job_id='job1'"
    ).fetchone()
    assert row[0] == "2026-08-19 01:02:03"
    assert row[1] == 8123.5
    # 轮次结束时刻沿用 created_at，不另加列；两者同格式所以可直接比较
    assert row[2] >= row[0]


def test_timing_does_not_exist_when_profile_write_fails(tmp_path):
    """
    intake-turn-observability「时序与画像同生共死」+ 铁律 1 的不变式：
    业务写失败时，effect_log 也不能留下记录——否则重放会判定"已执行"而静默
    跳过，这正是 .51 现网 2026-08-10/08-12 各丢一轮 outbox 的机理
    （docs/findings/2026-08-13-sqlite-事务归属冲突.md §8.5）。
    """
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    # 不插 job 行：job_profile.job_id 的外键（PRAGMA foreign_keys=ON）会让
    # INSERT 直接失败，模拟"这一轮的画像写不进去"
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        effect_persist_draft(
            conn,
            thread_id="ghost-job",
            business_key="0",
            state={
                "profile_patch_accumulated": {"job_title": "x"},
                "unspecified_fields": [],
                "turn_started_at": "2026-08-19 01:02:03",
                "llm_latency_ms": 8123.5,
            },
        )

    profiles = conn.execute(
        "SELECT COUNT(*) FROM job_profile WHERE job_id='ghost-job'"
    ).fetchone()[0]
    effects = conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE thread_id='ghost-job'"
    ).fetchone()[0]
    assert profiles == 0
    assert effects == 0  # 画像与幂等记录按 thread 恒等，都是 0


def test_persisted_latency_covers_llm_retries(tmp_path, monkeypatch):
    """intake-turn-observability「重试计入耗时」的端到端版本：
    模型第一次返回非法 JSON、第二次成功，落库的耗时必须覆盖两次。"""
    from app.llm import gateway as gateway_module

    ticks = iter([0.0, 1.5, 10.0, 12.25])
    monkeypatch.setattr(gateway_module.time, "monotonic", lambda: next(ticks))

    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.commit()

    gateway = make_gateway(
        [
            "这不是 JSON",
            json.dumps(
                {"is_job_related": True, "questions": [], "profile_patch": {"headcount": 1}}
            ),
        ]
    )
    state = compute_intake_turn(
        {
            "job_id": "job1",
            "history": [{"role": "user", "content": "要一个人"}],
            "round_count": 0,
            "profile_patch_accumulated": {},
            "turn_started_at": "2026-08-19 01:02:03",
        },
        gateway=gateway,
    )

    effect_persist_draft(conn, thread_id="job1", business_key="0", state=state)

    latency = conn.execute(
        "SELECT llm_latency_ms FROM job_profile WHERE job_id='job1'"
    ).fetchone()[0]
    assert latency == pytest.approx(3750.0)  # 1500 + 2250，不是只记最后一次


def test_system_time_and_user_think_time_are_separable(tmp_path):
    """
    intake-turn-observability「系统延迟与用户思考时长可分离」。
    这条测试同时是**统计口径的可执行文档**：下面这段 SQL 就是运维查"业务经理
    到底等了多久"要跑的东西。修复前只有 created_at（轮次结束时刻），相邻两轮
    的间隔把 LLM 耗时和用户打字时间混在一起，问不出"单轮等待感受"。
    """
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    # 直接写两行：本测试验证的是报表口径能不能算出来，不是 effect 的行为
    conn.executemany(
        "INSERT INTO job_profile "
        "(id, job_id, version, status, profile_json, turn_started_at, created_at, llm_latency_ms) "
        "VALUES (?, 'job1', ?, 'drafting', '{}', ?, ?, ?)",
        [
            ("job1-v1", 1, "2026-08-18 09:00:00", "2026-08-18 09:00:12", 12000.0),
            ("job1-v2", 2, "2026-08-18 09:01:30", "2026-08-18 09:01:45", 15000.0),
        ],
    )
    conn.commit()

    rows = conn.execute(
        """
        SELECT version,
               CAST(strftime('%s', created_at) AS INTEGER)
                 - CAST(strftime('%s', turn_started_at) AS INTEGER) AS system_seconds,
               CAST(strftime('%s', turn_started_at) AS INTEGER)
                 - LAG(CAST(strftime('%s', created_at) AS INTEGER)) OVER (ORDER BY version)
                 AS user_seconds
        FROM job_profile WHERE job_id='job1' ORDER BY version
        """
    ).fetchall()

    assert rows[0][1] == 12 and rows[0][2] is None  # 第一轮没有"上一轮"
    assert rows[1][1] == 15  # 系统处理耗时
    assert rows[1][2] == 78  # 用户思考与输入耗时，与系统耗时分开

    total = conn.execute(
        "SELECT CAST(strftime('%s', MAX(created_at)) AS INTEGER) "
        "- CAST(strftime('%s', MIN(turn_started_at)) AS INTEGER) FROM job_profile "
        "WHERE job_id='job1'"
    ).fetchone()[0]
    assert total == 105


def test_timing_trace_records_no_model_identity(tmp_path):
    """
    intake-turn-observability「时序留痕不承担审计职责」：本单元的留痕里只有
    时间与耗时。llm_response_model 这一列已经建好（Task 1）但**本单元不写值**，
    它归第 7 章的 intake-field-grounding（按模型版本归因编造率）。
    """
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.commit()

    gateway = make_gateway(
        [json.dumps({"is_job_related": True, "questions": [], "profile_patch": {"headcount": 1}})]
    )
    state = compute_intake_turn(
        {
            "job_id": "job1",
            "history": [{"role": "user", "content": "要一个人"}],
            "round_count": 0,
            "profile_patch_accumulated": {},
            "turn_started_at": "2026-08-19 01:02:03",
        },
        gateway=gateway,
    )
    effect_persist_draft(conn, thread_id="job1", business_key="0", state=state)

    row = conn.execute(
        "SELECT llm_response_model, ungrounded_fields FROM job_profile WHERE job_id='job1'"
    ).fetchone()
    assert row[0] is None
    assert json.loads(row[1]) == []
    assert "llm_response_model" not in state  # 也不许经 state 漏进来
```

`tests/test_graph_nodes.py` 顶部需要 `import sqlite3` 与 `import pytest`（若尚无）。

追加到 `tests/test_web_api.py`：

```python
def test_run_turn_stamps_turn_started_at(tmp_path):
    """
    轮次起始时刻必须在 HTTP 请求进入时打——那才是"用户开始等"的时刻。
    在 compute 节点里打会漏掉排队与取数的时间。
    """
    from app.storage.db import get_connection

    responses = [
        json.dumps(
            {
                "is_job_related": True,
                "questions": [{"text": "招几个人？", "field": "headcount"}],
                "profile_patch": {"job_title": "嵌入式软件工程师"},
            }
        )
    ]
    client = make_app(tmp_path, responses)

    resp = client.post("/api/jobs", json={"message": "要个做嵌入式开发的"})
    job_id = resp.json()["job_id"]

    conn = get_connection(str(tmp_path / "web.db"))
    row = conn.execute(
        "SELECT turn_started_at, llm_latency_ms, created_at FROM job_profile WHERE job_id=?",
        (job_id,),
    ).fetchone()
    assert row[0] is not None
    assert row[1] is not None and row[1] >= 0
    assert row[2] >= row[0]  # 结束不早于开始，说明两者格式一致
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_graph_nodes.py tests/test_web_api.py -k "timing or turn_started_at or retries or separable or model_identity" -v 2>&1 | tail -12`
Expected: FAIL —— `turn_started_at` 为 `None`（`effect_persist_draft` 还没写这两列）

- [ ] **Step 3: 实现**

`app/storage/db.py` 顶部导入改为：

```python
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
```

在 `get_connection` 之前加：

```python
def sqlite_utc_now() -> str:
    """
    与 SQLite `datetime('now')` 完全一致的 UTC 时间串（秒级、无时区后缀）。

    为什么不用 datetime.now().isoformat()：job_profile.created_at 由
    `datetime('now')`（UTC，格式 "YYYY-MM-DD HH:MM:SS"）写入，代表轮次结束
    时刻；turn_started_at 由 Python 侧写入，代表轮次开始时刻。两者格式必须
    一模一样，否则"结束 − 开始"这个减法要先做时区与格式对齐，而这类对齐
    迟早会有人做错——最省事的做法是从一开始就不给人做错的机会。
    """
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
```

`app/graph/nodes.py` 的 `effect_persist_draft` —— 在 docstring 末尾追加一段，并把 `INSERT` 换掉（**其余部分包括 `conversation` 的 UPSERT 一字不动**）：

```python
    2026-08-19（m1-intake-quality-fixes tasks 1.5）：本轮时序（turn_started_at /
    llm_latency_ms）写在**同一条 INSERT** 里。intake-turn-observability 要求
    "画像有这一轮、时序没有这一轮"不可能出现，所以不新增 effect 节点、不另起
    一次写入——多一次写入就多一个能失败的地方，而这两份数据必须同生共死。
    business_key 语义不变（仍是 round_count），幂等键不受影响。

    is_productive / derived_unspecified_fields / ungrounded_fields /
    llm_response_model 这几列本单元**不写值**，靠列默认值成立（第 3、6、7 章
    各自接上）。
    """
    profile_json = json.dumps(state.get("profile_patch_accumulated", {}), ensure_ascii=False)
    unspecified_json = json.dumps(state.get("unspecified_fields", []), ensure_ascii=False)
    version = int(business_key) + 1

    conn.execute(
        "INSERT INTO job_profile "
        "(id, job_id, version, status, profile_json, unspecified_fields, "
        "turn_started_at, llm_latency_ms) "
        "VALUES (?, ?, ?, 'drafting', ?, ?, ?, ?)",
        (
            f"{thread_id}-v{version}",
            thread_id,
            version,
            profile_json,
            unspecified_json,
            state.get("turn_started_at"),
            state.get("llm_latency_ms"),
        ),
    )
```

`app/web/server.py`：导入改为 `from app.storage.db import get_connection, init_schema, sqlite_utc_now`，`_run_turn` 里构造 `state` 的那一段改为：

```python
        state = {
            "job_id": job_id,
            "history": [*prior_history, {"role": "user", "content": message}],
            "round_count": round_count,
            "profile_patch_accumulated": accumulated,
            # 轮次起始时刻在这里打，不在 compute 节点里打：那才是"用户开始等"
            # 的时刻，节点里打会漏掉上面几次取数的时间。格式与 job_profile
            # .created_at 的 datetime('now') 完全一致（见 sqlite_utc_now）。
            "turn_started_at": sqlite_utc_now(),
        }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_graph_nodes.py -v 2>&1 | tail -5`
Expected: 全绿

- [ ] **Step 5: 跑铁律 1 的不变式回归（必须专门看一眼）**

Run: `./venv/bin/python -m pytest tests/test_transaction_ownership.py tests/test_graph_idempotency.py -v 2>&1 | tail -5`
Expected: 全绿。这两个文件守着"`effect_log` 条数与业务表行数按 thread 恒等"——`effect_persist_draft` 的 `INSERT` 被改过，必须确认不变式没被动摇。

- [ ] **Step 6: 全量回归 + 提交**

Run: `./venv/bin/python -m pytest -q 2>&1 | tail -3`
Expected: `129 passed` —— 全绿

```bash
git add app/storage/db.py app/graph/nodes.py app/web/server.py tests/test_graph_nodes.py tests/test_web_api.py
git commit -m "feat(observability): 逐轮时序与画像草案同一条 INSERT 落库

tasks.md 1.4/1.5/1.6。不新增 effect 节点、不改 business_key 语义——时序与画像
必须同生共死（intake-turn-observability）。结束时刻沿用 created_at，起始时刻
在 HTTP 层打且与 datetime('now') 同格式，使系统耗时与用户思考时长可分开算。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: 结构化问题下发到 API 与前端 + 历史 payload 兼容（tasks 2.6 / 2.7 / 2.8）

**Files:**
- Modify: `app/graph/build.py:58-85`（`_deliver_node`）、`app/web/server.py`（`_run_turn` 返回、`get_job`）、`app/web/static/index.html:44-57`（`renderMessage`）
- Test: `tests/test_web_api.py`（改 1 处 + 新增 3 条）、`tests/test_graph_nodes.py:178`（改 1 处）、`tests/test_static_frontend.py`（新建 2 条）

**Interfaces:**
- Consumes: `normalize_question_payload`（Task 3）、`state["pending_questions"]`（Task 4）
- Produces: `question` 类型的 `OutboundMessage.payload` 契约：
  ```json
  {
    "questions": [
      {"question_id": "functional_safety", "text": "要哪个 ASIL 等级？",
       "field": "functional_safety", "options": ["ASIL-B", "ASIL-D", "无要求"],
       "allow_free_text": true, "is_reask": false}
    ],
    "questions_text": "要哪个 ASIL 等级？"
  }
  ```
  `questions_text` 是给纯文本通道（以及本章的前端）用的已渲染文本，**与 history 里的 assistant 文本同源**。

**`business_key` 注意**：`_deliver_node` 的 `business_key` 是 `f"{round_count}:{message_business_key(payload)}"`，而 `message_business_key` 是 payload 的内容哈希。payload 形状变了 → 哈希值会变。**这不影响正确性**（同一轮内重放时 payload 相同、哈希相同，仍正确去重；跨轮次由 `round_count` 前缀区分），但要清楚：升级部署后，同一轮次的旧幂等键不会被命中。已投递的历史消息不会因此重发——历史轮次的 `round_count` 不会再出现。

- [ ] **Step 1: 改已有断言 + 写新增失败测试**

`tests/test_graph_nodes.py:178`（Task 4 已把它改成 `[q["text"] for q in ...]`）下面**追加一行**，锁住新增的 `questions_text`：

```python
    assert latest.payload["questions_text"] == "是否涉及 AUTOSAR？"
```

`tests/test_web_api.py:101` 在 Task 4 已改到位，本任务不用再动。

追加到 `tests/test_web_api.py`：

```python
def test_question_payload_carries_structured_questions(tmp_path):
    responses = [
        json.dumps(
            {
                "is_job_related": True,
                "questions": [
                    {
                        "text": "要哪个 ASIL 等级？",
                        "field": "functional_safety",
                        "options": ["ASIL-B", "ASIL-D", "无要求"],
                    }
                ],
                "profile_patch": {"job_title": "功能安全工程师"},
            }
        )
    ]
    client = make_app(tmp_path, responses)

    body = client.post("/api/jobs", json={"message": "要个做功能安全的"}).json()

    payload = body["message"]["payload"]
    assert payload["questions"][0] == {
        "question_id": "functional_safety",
        "text": "要哪个 ASIL 等级？",
        "field": "functional_safety",
        "options": ["ASIL-B", "ASIL-D", "无要求"],
        "allow_free_text": True,
        "is_reask": False,
    }
    assert payload["questions_text"] == "要哪个 ASIL 等级？"


def test_legacy_string_question_rows_are_normalized_on_read(tmp_path):
    """
    .51 现网 data/demo.db 的 outbox 里存着 2026-08-18 及之前写下的裸字符串问题。
    GET /api/jobs/{id} 会把这些历史行原样读回来，新前端按对象访问 q.text 会在
    真实数据上直接崩——本地测试库全是新写的行，不专门测就走不到这条路径
    （与 design.md 决策 10 同一类只在服务器上炸的坑）。
    """
    from app.storage.db import get_connection

    responses = [
        json.dumps({"is_job_related": True, "questions": [], "profile_patch": {"headcount": 1}})
    ]
    client = make_app(tmp_path, responses)
    job_id = client.post("/api/jobs", json={"message": "要一个人"}).json()["job_id"]

    # 手写一条老形态的 outbox 行，模拟升级前留下的数据
    conn = get_connection(str(tmp_path / "web.db"))
    conn.execute(
        "INSERT INTO outbox (thread_id, message_type, payload_json) VALUES (?, 'question', ?)",
        (job_id, json.dumps({"questions": ["是否涉及 AUTOSAR？"]}, ensure_ascii=False)),
    )
    conn.commit()

    payload = client.get(f"/api/jobs/{job_id}").json()["message"]["payload"]

    assert payload["questions"][0]["text"] == "是否涉及 AUTOSAR？"
    assert payload["questions"][0]["question_id"]
    assert payload["questions"][0]["options"] == []
    assert payload["questions_text"] == "是否涉及 AUTOSAR？"


def test_confirmation_prompt_payload_is_untouched(tmp_path):
    """
    第 2 章只换追问的载体。确认提示的 payload 本章不动（缺口警示块属第 6 章），
    这条测试防止"顺手一起改了"。
    """
    responses = [
        json.dumps({"is_job_related": True, "questions": [], "profile_patch": {"headcount": 1}})
    ]
    client = make_app(tmp_path, responses)

    body = client.post("/api/jobs", json={"message": "要一个人"}).json()

    assert body["message"]["type"] == "confirmation_prompt"
    payload = body["message"]["payload"]
    assert payload["type"] == "confirmation_prompt"
    assert "profile_patch_accumulated" in payload
    assert "unspecified_fields" in payload
```

新建 `tests/test_static_frontend.py`：

```python
import re
from pathlib import Path

INDEX_HTML = Path("app/web/static/index.html").read_text(encoding="utf-8")


def test_index_html_has_no_absolute_paths():
    """
    部署约束 1：前端资源与接口调用一律相对路径，禁止硬编码 /static/… /api/…。
    挂在 root_path=/hr/recruit-agent 下时，绝对路径会打到门户根上去。
    """
    assert not re.search(r"""fetch\(\s*[`'"]/""", INDEX_HTML)
    assert not re.search(r"""(src|href)\s*=\s*["']/(?!\s*$)""", INDEX_HTML.replace("<!--BASE_HREF-->", ""))
    assert "api/jobs" in INDEX_HTML  # 相对路径写法仍在


def test_index_html_renders_structured_questions_and_tolerates_legacy_strings():
    """
    弱断言（本仓库没有 JS 测试运行器，单文件前端无构建）：只保证适配新 payload
    的那几行没被改回去。真正的验证是 Task 6 的手工跑通那一步。
    """
    assert "questions_text" in INDEX_HTML
    # 历史 outbox 行里 questions 是裸字符串，前端也要兜一层
    assert 'typeof q === "string"' in INDEX_HTML
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_web_api.py tests/test_static_frontend.py -v 2>&1 | tail -12`
Expected: FAIL —— payload 里 `questions` 仍是字符串列表、`questions_text` 不存在、`index.html` 里没有 `questions_text`

- [ ] **Step 3: 实现（后端）**

`app/graph/build.py` 的 `_deliver_node` 里 `else` 分支改为：

```python
        else:
            # 结构化问题原样透传（design.md 决策 1）：payload 里既有对象列表
            # （给能渲染控件的通道，第 4 章的 Web 与将来的企微卡片），也有
            # 已渲染的 questions_text（给纯文本通道）。questions_text 与
            # history 里的 assistant 文本同源，都出自 render_questions_text。
            from app.agents.intake_question import IntakeQuestion, render_questions_text

            questions = [
                IntakeQuestion.from_payload(item)
                for item in state.get("pending_questions", [])
            ]
            payload = {
                "questions": [question.to_payload() for question in questions],
                "questions_text": render_questions_text(questions),
            }
            message = OutboundMessage(type="question", payload=payload)
```

`app/web/server.py`：
1. 导入加 `from app.agents.intake_question import normalize_question_payload`
2. `_run_turn` 的返回改为：

```python
        latest = channel.latest(job_id)
        return {"type": latest.type, "payload": _response_payload(latest)}
```

3. 在 `_run_turn` 之前加：

```python
    def _response_payload(message) -> dict:
        """
        对外响应统一过一遍归一化。为什么必须在读的这一侧做：outbox 里存着
        2026-08-18 及之前写下的 {"questions": ["裸字符串"]}（.51 现网 15 个 job
        的历史行），新前端按对象访问会直接崩。归一化是幂等的，新行过一遍不变。
        """
        if message.type != "question":
            return message.payload
        return normalize_question_payload(message.payload)
```

4. `get_job` 的返回改为：

```python
        return {
            "job_id": job[0],
            "status": job[2],
            "message": {"type": latest.type, "payload": _response_payload(latest)}
            if latest
            else None,
        }
```

- [ ] **Step 4: 实现（前端）**

`app/web/static/index.html` 的 `renderMessage` 里 `question` 分支改为：

```javascript
    // payload.questions 现在是结构化问题对象数组（question_id / text / field /
    // options / allow_free_text / is_reask）。**本章只渲染文本**：可点选控件与
    //「以下为 AI 建议选项」标识属第 4 章（后者是《AI 生成合成内容标识办法》
    // 的要求，不能先渲染选项、后补标识）。
    // 兜一层裸字符串：升级前写进 outbox 的历史行里 questions 是字符串数组。
    function questionText(q) {
      return typeof q === "string" ? q : (q && q.text) || "";
    }

    function renderMessage(message) {
      if (message.type === "question") {
        const questions = message.payload.questions || [];
        const text =
          message.payload.questions_text || questions.map(questionText).join("\n");
        appendTurn("assistant", text);
        document.getElementById("confirm-btn").style.display = "none";
      } else if (message.type === "confirmation_prompt") {
```

（`confirmation_prompt` 分支及其后的内容**一字不动**。`questionText` 定义在 `renderMessage` 之前的同一 `<script>` 作用域里。）

- [ ] **Step 5: 跑测试确认通过 + 全量回归**

Run: `./venv/bin/python -m pytest -q 2>&1 | tail -3`
Expected: `134 passed` —— 本单元的全部测试到齐（基线 91 + 新增 43）

- [ ] **Step 6: 手工跑通（前端没有自动化测试，这一步不能省）**

```bash
./venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8099 --root-path /hr/recruit-agent
```

另开一个终端：

```bash
curl -s -X POST http://127.0.0.1:8099/hr/recruit-agent/api/jobs -H 'Content-Type: application/json' -d '{"message":"要个做嵌入式开发的，能写驱动"}' | ./venv/bin/python -m json.tool
```

检查四件事，逐条打勾：
1. 响应里 `message.payload.questions` 是对象数组，每项有 `question_id`
2. 响应里有 `questions_text`，内容与 `questions` 各项 `text` 的换行拼接一致
3. 浏览器打开 `http://127.0.0.1:8099/hr/recruit-agent/`，追问文本正常显示，**界面与升级前看起来一样**（第 2 章的自我约束：只换载体）
4. 浏览器 DevTools 的 Network 面板里，请求 URL 全部带 `/hr/recruit-agent` 前缀（部署约束 1）

跑完 `Ctrl-C` 停掉。**注意**：`app/main.py` 不得修改（并行变更的落点），这里只是运行它。

- [ ] **Step 7: 提交**

```bash
git add app/graph/build.py app/web/server.py app/web/static/index.html tests/test_web_api.py tests/test_graph_nodes.py tests/test_static_frontend.py
git commit -m "feat(web): 结构化问题下发到 API 与前端，兼容历史裸字符串 payload

tasks.md 2.6/2.7/2.8。payload 同时带对象列表（给能渲控件的通道）与
questions_text（给纯文本通道），后者与 history 的 assistant 文本同源。
读侧统一过 normalize_question_payload：.51 现网 outbox 有 08-18 之前写下的
裸字符串行，新前端按对象访问会在真实数据上崩。本章只渲染文本，选项控件与
AI 建议标识属第 4 章。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: 技术债登记与交付单元收尾（tasks 1.7）

**Files:**
- Create: `docs/tech-debt.md`
- Test: 无新增测试（本任务只产出文档 + 跑既有全量）

**为什么必须登记**：这两列在 `ai-audit-trail-and-outbound-gate` 的 `analysis_run` 落地后成为冗余。不登记会导致两套时序数据长期并存并互相矛盾（design.md 决策 9「边界」），而"矛盾的两份时序数据"比没有数据更难用——没人知道该信哪份。

- [ ] **Step 1: 建技术债清单**

新建 `docs/tech-debt.md`：

```markdown
# 技术债清单

> 每条必须写明**触发条件**（什么时候还）与**不还的后果**。没有触发条件的条目会永远悬着。
> 本文件是仓库级真源。变更包归档后 `openspec/changes/` 里的 tasks.md 会移走，
> 计划文件也会变旧，但这份清单留着。

## TD-1 · `job_profile` 的两列时序留痕是过渡形态

**欠的是什么**：`job_profile.turn_started_at` 与 `job_profile.llm_latency_ms`
（2026-08-19，`m1-intake-quality-fixes` 第 1 章加入）。

**触发条件**：`ai-audit-trail-and-outbound-gate` 的 `analysis_run` 表落地即删
——该变更的 tasks 1.1 已包含 `latency_ms` 与 `created_at`，届时这两列成为冗余。

**怎么还**：删两列 + 删 `effect_persist_draft` 里对它们的写入 + 把统计口径
（见 `docs/superpowers/plans/2026-08-19-m1-intake-quality-fixes-unitA-storage-and-structured-questions.md`
Task 5 的分离口径 SQL）改指 `analysis_run`。

**不还的后果**：两套时序数据长期并存、互相矛盾，而没人知道该信哪一份。

**为什么当时要欠**：本批 P0/P1 的修复必须能被验证（"兜底档位是否真的减少了
空转轮、有没有把单轮延迟拖长"）。`ai-audit-trail-and-outbound-gate` 范围大得多
且尚未排期，等它意味着本批的效果只能靠感觉判断（design.md 决策 9）。
```

- [ ] **Step 2: 在代码里留下指针**

`app/storage/db.py` 的 `_ADDED_COLUMNS` 上方注释里，给 `turn_started_at` / `llm_latency_ms` 两列各补一句：

```python
    # turn_started_at / llm_latency_ms 是过渡形态，见 docs/tech-debt.md TD-1
    # （analysis_run 落地即删）。
```

- [ ] **Step 3: 全量回归 + 跑一次干净的提取验证**

```bash
./venv/bin/python -m pytest -q 2>&1 | tail -3
```
Expected: `134 passed`

```bash
git status --porcelain
```
Expected: 只有 `docs/tech-debt.md` 与 `app/storage/db.py` 未提交（前几个任务都已各自提交）

- [ ] **Step 4: 自查交付单元的 Requirement 覆盖**

对照本计划开头的覆盖矩阵，逐条确认：

- [ ] `intake-turn-observability` · 逐轮时序留痕 → Task 1/2/4/5，测试：`test_effect_persist_draft_writes_turn_timing_in_the_same_row`、`test_timing_does_not_exist_when_profile_write_fails`、`test_persisted_latency_covers_llm_retries`
- [ ] `intake-turn-observability` · 系统延迟与用户思考时长可分离 → `test_system_time_and_user_think_time_are_separable`
- [ ] `intake-turn-observability` · 时序留痕不承担审计职责 → `test_timing_trace_records_no_model_identity`
- [ ] `intake-guided-options` · 结构化追问与可选项作答（**载体部分**）→ `test_structured_question_carries_field_and_options`、`test_question_payload_carries_structured_questions`；**可点选控件与 AI 建议标识仍缺，属第 4 章**
- [ ] `intake-question-tracking` · 子问题的稳定标识与拆分 → `test_question_id_is_the_target_field`、`test_question_id_ignores_wording_when_field_present`、`test_question_id_is_derived_by_system_even_if_model_supplies_one`、`test_system_prompt_requires_one_answerable_subquestion_per_item`
- [ ] `intake-question-tracking` · 重问必须显式标注（**渲染部分**）→ `test_render_questions_text_marks_reask`；**判定属第 5 章**

- [ ] **Step 5: 提交**

```bash
git add docs/tech-debt.md app/storage/db.py
git commit -m "docs: 技术债清单建档，登记时序两列的过渡形态与触发条件

tasks.md 1.7。不登记会导致本批的两列与将来 analysis_run 的时序数据长期并存
互相矛盾（design.md 决策 9 边界）。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## 交付单元完成后的动作（不属于任何 Task，由 `run-build` 收尾时做）

1. **回勾 WBS**：`openspec/changes/m1-intake-quality-fixes/tasks.md` 第 1 章（1.1–1.7）与第 2 章（2.1–2.8）的 checkbox，**在 final review 通过后才勾**
2. **不要归档变更包**：第 3–8 章还没做，`m1-intake-quality-fixes` 远未完成
3. **不要推 `.51`**：第 8 章才上线，且生产发版按 CLAUDE.md 决策代理表**不可代**，须 Shao Peishen 拍板
4. **下一个交付单元**：第 3+4+5 章（模糊回复兜底 → 前端可点选 → 已问未答追踪）。届时 `IntakeQuestion` 的字段与 `question_id` 派生规则已是既成事实，出计划时直接引用本计划 Task 3 的 Interfaces 段

## 本计划刻意没做的事（reviewer 不要当成遗漏）

| 没做 | 归属 |
|---|---|
| `is_vague_reply` 模糊回复识别、领域选项库、兜底档位注入 | 第 3 章（3.1–3.8） |
| `is_productive` 判定与 `MAX_TOTAL_ROUNDS` 预算改口径 | 第 3 章（3.9–3.11），本单元只建列 |
| 可点选选项控件、「以下为 AI 建议选项」标识 | 第 4 章（4.1–4.5） |
| 已问未答台账、`is_reask` 判定、重问次数上限、`_repeats_earlier_assistant_turn` 的去留结论 | 第 5 章（5.1–5.8），本单元只备好 `is_reask` 字段与渲染 |
| `derive_unspecified_fields`、中文字段名映射、缺口警示块、知情确认 409 | 第 6 章（6.1–6.10） |
| `profile_patch` 升级为「值 + 来源引用」、`verify_field_grounding`、编造率统计口径 | 第 7 章（7.1–7.11），本单元只建 `ungrounded_fields` / `llm_response_model` 两列并透出 `response_model` |
| 三段真实会话回放、服务器备份与上线、回填 pilot 反馈、改 manager-guide | 第 8 章（8.1–8.9） |
| logging 初始化、`request_id`、日志脱敏与轮转 | 并行变更 `server-runtime-logging`。**本单元不得改 `app/main.py`** |
