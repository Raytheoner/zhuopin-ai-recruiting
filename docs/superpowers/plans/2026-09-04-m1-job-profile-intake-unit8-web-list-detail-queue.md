# M1 交付单元 8 · Web 列表、详情与转人工队列 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给现在只有"一条会话"的 Demo 补上**会话之外的三个只读视图**——岗位列表与状态视图（8.1）、画像详情页含版本历史与生成快照（8.2）、`needs_manual` 转人工队列（8.4），让业务经理与 HR 在关掉那个聊天页之后仍然找得到自己的岗位。

**Architecture:** 三个视图**全部是推导出来的只读视图**，不新增任何状态列、不新增任何写入。查询逻辑落在**新文件** `app/storage/job_queries.py`（纯 SQL 读函数 + 纯推导函数，⛔ 不含一条 INSERT/UPDATE/DELETE，⛔ 不 import `app.graph`），`app/web/server.py` 只做三个 `GET` 端点的接线与中文标签映射，前端在同一个 `app/web/static/index.html` 里加一条三按钮导航与两个新视图容器（⛔ 不引框架、⛔ 不加构建步骤、⛔ 不加路由库）。转人工队列的关键设计是**多来源推导**而不是读一个状态列：`JobStatus.NEEDS_MANUAL` 至今没有任何写入方（WBS 2.5 未做），只认它的话队列永远是空的，而"空队列"和"没人需要处理"在界面上长得一模一样。

**Tech Stack:** Python 3.14（`requires-python = ">=3.14,<3.15"`，与 .51 部署环境严格对齐）· FastAPI · SQLite（`app/storage/db.py`）· pytest（`venv/bin/python -m pytest`）· 单文件原生 JS 前端（无构建、无框架、无第三方库）

---

## Global Constraints

以下每一条对**每个** Task 都成立，reviewer 按这一段逐条看。第 1–6 条从 `CLAUDE.md` 的「工程铁律」「合规红线」「部署约束」逐字复制，第 7–14 条是本交付单元的边界。

1. **（工程铁律 1）LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
   **本单元的落点是"这条铁律不该被触发"**：本单元不新增任何 `effect_*` 节点，因为本单元不产生任何副作用。reviewer 判据反过来用——**在本单元新增的代码里出现任何一条 `INSERT` / `UPDATE` / `DELETE` 都是违例**，不论它有没有带幂等键。
2. **（工程铁律 2）L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。本单元全部代码都在"无副作用"这一侧。
3. **（工程铁律 5）`temperature=0`；模型版本优先显式锁定**，禁止 `latest` 类别名。供应商不提供带版本号快照时，**必须从 API 响应里取回实际的 `model` 字段并持久化**——配置里写的名字不算数，响应返回的才算。
   本单元的落点：8.2 的"生成快照"展示的模型标识 **MUST** 取 `job_profile.llm_response_model`（API 响应实际返回值），⛔ 不得展示配置里写的 `settings.llm_model`——那正是这条铁律要区分开的两个东西。
4. **（合规红线）AI 只做排序推荐，不做自动淘汰。淘汰必须有人工确认节点并留痕。** 本单元的落点：转人工队列**只展示、不处置**——⛔ 不做批量确认、批量放弃、批量重生成，一个写按钮都不许有（M2 的事）。
5. **（合规红线）AI 生成的 JD、拒信、邀约须带标识**（《AI 生成合成内容标识办法》2025-09-01 施行）。
   本单元的落点：**详情页 ⛔ 不渲染 JD 正文**，只渲染「带 AI 生成标识」/「已标记为人工撰写」这两个徽标之一。理由：渲染正文就等于新开了一个必须自己保证标识不被裁掉的展示位，而正文已经有一个专门的、合规上已经过审的展示位（`#jd-output`，交付单元 7 交付）。少一个展示位就少一处会漏标识的地方。
6. **（部署约束 1）路径前缀就绪**：FastAPI `root_path=/hr/recruit-agent`，前端资源与接口调用**一律相对路径**，禁止硬编码 `/static/…` `/api/…`。验收标准是挂到任意子路径下都能正常工作，且有测试覆盖。
   本单元的落点：新增的三个 `fetch()` 全部写成不带开头 `/` 的相对路径（`api/jobs`、`api/jobs/${id}/profile`、`api/queues/needs-manual`），由 `<base href>` 解析；`tests/test_web_api.py` 现有的路径前缀用例（`test_index_base_href_matches_configured_root_path`、`test_unprefixed_paths_404_when_root_path_is_configured`、`test_frontend_html_has_no_hardcoded_absolute_api_or_static_paths`）必须照样通过，Task 5 另加一条覆盖新端点的前缀用例。
7. **本单元只读，⛔ 一条写入都没有。** 三个视图、五个新函数、三个新端点，全部只做 `SELECT`。具体禁止项（逐条都是真实的诱惑，不是凑数）：
   - ⛔ 不回写 `job.title`（它被 `create_job` 写死成 `'待确定'` 后再无人更新；列表标题从最新一版画像的 `job_title` **读**出来，不写回去）
   - ⛔ 不写 `job.status = 'needs_manual'`（那是 WBS 2.5 的事，见第 8 条）
   - ⛔ 不新建 `job_profile` 版本、不改任何 `status`
   - ⛔ 不落库任何"队列快照"或"列表缓存"
8. **`needs_manual` 队列是推导视图，不是状态列。** `JobStatus.NEEDS_MANUAL`（`app/schemas/job_profile.py:16`）至今**没有任何写入方**（WBS 2.5 未做），本单元又不许写（第 7 条）。因此队列必须同时查三个来源，缺一不可：
   ① `job.status = 'needs_manual'`——今天恒为空，2.5 落地当天自动生效，⛔ 不要因为"现在查不到"就省掉；
   ② `job_profile.profile_json` 的 `_jd_needs_manual`——JD 连续 2 次触发歧视性表述检测后由 `app/graph/nodes.py:417` 的 `effect_generate_and_persist_jd` 落库（**今天唯一真实存在的写入方**）；
   ③ 修改次数达上限——spec「修改次数上限」要求"提示转人工"，而 `app/web/server.py` 的 `revise()` 只在那一次 409 响应里说了一句话，页面一关就没了。
   只认 ① 的实现会得到一个永远为空的队列，而**空队列与"没人需要处理"在界面上长得一模一样**——这是一个没有任何症状的故障。
9. **⛔ 不碰 `app/graph/`、`app/storage/db.py`、`app/audit/`、`app/agents/`。** 新的只读查询放在**新文件** `app/storage/job_queries.py`。这条同时是并发边界：`app/graph/nodes.py`（500+ 行）与 `db.py` 上有并行泳道。
10. **`app/storage/job_queries.py` ⛔ 不得 import `app.graph` 或 `app.agents`。** `app/graph/nodes.py` 已经 import 了 `app/storage/idempotency.py`，反向再导一次就是层次倒置。需要用到 graph 层的常量（`MAX_REVISIONS`、`DECISION_REVISION_REQUESTED`）时，由调用方 `app/web/server.py`（它本来就 import 着它们）**当参数传进来**；⛔ 也不许在本文件里重抄一份字面量——重抄就多一个会漂移的真源，而漂移**没有任何症状**：不报错、不失败，只是队列的上限判定悄悄和 `revise()` 的判定对不上。允许 import `app.schemas.job_profile`（那是叶子模块，`nodes.py` 也在导）。
11. **单页应用 ⛔ 不引框架、⛔ 不加构建步骤。** 现状是一个 `index.html`，保持。视图切换用按钮 + `style.display`，⛔ 不用 hash 路由、⛔ 不用 `<a href>`（那会和 `<base href>` 的解析纠缠出一类只在挂前缀时才现形的 bug）。
12. **前端一律 `document.createElement` + `textContent`，⛔ 不拼 HTML 字符串塞进 DOM。** 岗位标题、画像字段值、JD 相关文案全部是 LLM 自由生成的文本，`innerHTML` 就是一条注入路径。这条与 `index.html` 现有的 `renderProfileSummary` / `renderQuestionBlock` 是同一条纪律。
13. **⛔ 界面上不得出现英文 `snake_case` 字段名或英文 status 值。** `drafting` / `approved` / `abandoned` / `revision_requested` 一律由服务端映射成中文标签下发；未指定字段一律走 `field_labels()` 的中文名。这条与 `index.html:211-213` 既有约束同源——那正是第 6 章修过一遍的故障现象。
14. **⛔ 不改 `requirements.txt`、不改 CI、不改 `openspec/` 下任何文件、不改 `docs/tech-debt.md`**（WBS 回勾由 run-build 收尾时统一做；8.2 快照的缺口已登记在 TD-1，只引用不新开条目，理由见 File Structure 段末）。

---

## File Structure

| 文件 | 新建/修改 | 职责 |
|---|---|---|
| `app/storage/job_queries.py` | 新建 | 只读 SQL（列表行、修改计数、最后消息类型、版本历史、决策留痕）+ 纯推导函数（显示标题、JD 状态、转人工理由、中文状态标签） |
| `app/web/server.py` | 修改（追加 3 个 `GET` 端点 + 2 个私有 helper + 3 张中文标签映射表） | 接线与中文化；⛔ 不动既有 11 个端点的任何一行 |
| `app/web/static/index.html` | 修改（HTML 加导航与两个视图容器、CSS 加 8 条规则、JS 追加一段只读视图代码） | 三个视图的渲染 |
| `tests/test_job_queries.py` | 新建 | Task 1 |
| `tests/test_job_views_api.py` | 新建（Task 2）→ 追加（Task 3、Task 4） | 三个端点的 API 测试；测试脚手架只在 Task 2 建一次 |
| `tests/test_static_frontend.py` | 修改（**只追加**，⛔ 不改既有 30 条用例） | Task 5 |

⛔ **本单元不改 `docs/tech-debt.md`**：8.2 快照的缺口（`analysis_run` 的 `job_id` 恒为 NULL）**已经登记在 TD-1 里**——TD-1「怎么还」第 ① 步逐字写着"先有一个单元把 `audit_context`（至少含 `thread_id` / `job_id` / `node`）接到 intake 的 LLM 调用上"，「现状」段又逐字写着"intake 路径尚未传 `audit_context`，那些行的 `job_id` / `application_id` 全为 `NULL`"。再开一条新 TD 就是给同一个事实开第二个真源，两边迟早会写得不一样而**没有任何症状**。本单元只**引用** TD-1。

依赖方向：Task 1 → Task 2 → Task 3 → Task 4 → Task 5。⛔ 不要打乱顺序：Task 2 建的测试脚手架（`_make_app` / `_seed_job`）被 Task 3、Task 4 直接复用，Task 5 的端到端用例依赖前四个 Task 的端点全部就位。

---

## 现状核实（出计划时逐条查过，⛔ 不要在实现时重新假设）

| 事实 | 证据 | 对本单元的影响 |
|---|---|---|
| `job.title` 恒为 `'待确定'` | `app/web/server.py:268` 写死插入；全仓库只有 `app/graph/nodes.py:270` 与 `:365` 两条 `UPDATE job SET`，都只改 `status` | 列表标题必须从画像里推导（Task 1 `display_title`） |
| `job.status` 实际取值只有 `drafting` / `approved` / `abandoned` | 同上两条 UPDATE + `create_job` 的默认值 | `needs_manual` 分支今天走不到，但必须写（Global Constraints 第 8 条） |
| `job_profile.status` 取值 `drafting` / `approved` / `abandoned` | `nodes.py:193`（插入 `'drafting'`）、`:268`（改 `'approved'`）、`:362`（改 `'abandoned'`） | 版本历史的中文状态映射按这三个值写 |
| 缺口的真源是 `derived_unspecified_fields` 列，**不是** `unspecified_fields` 列 | `nodes.py:180-183` + `db.py:31-33` 的注释：裸 `unspecified_fields` 存的是"模型自称的对照"，已降级（TD-2） | 详情页读 `derived_unspecified_fields` |
| `analysis_run.job_id` 恒为 `NULL` | `app/audit/hook.py:157` 取 `context.get("job_id")`，而 `audit_context` 参数在 `app/graph/` 与 `app/agents/` 下**没有任何调用点**（`grep -rn "audit_context" app/graph/ app/agents/ app/main.py` 无输出）；`docs/tech-debt.md` TD-1 的「现状」段已逐字记着这件事 | 8.2 的"生成快照"**不能**从 `analysis_run` 按岗位查，只能用 `job_profile` 逐轮落的列；差额**引用现有的 TD-1**，⛔ 不新开 TD 条目 |
| `MAX_REVISIONS = 5` | `app/graph/nodes.py:284` | 由 `server.py` 传参进 `job_queries`（Global Constraints 第 10 条） |
| `DECISION_REVISION_REQUESTED = "revision_requested"` | `app/graph/nodes.py:22` | 同上 |

---

### Task 1: 只读查询层 `app/storage/job_queries.py`

**Files:**
- Create: `app/storage/job_queries.py`
- Test: `tests/test_job_queries.py`

**Interfaces:**
- Consumes: `app.schemas.job_profile.field_labels(names: list[str]) -> list[str]`、`app.schemas.job_profile.summarize_profile(profile: dict) -> list[dict]`（两者已存在，签名不变）；`app.storage.db.init_schema(conn)`（测试用）
- Produces（后面三个 Task 全部按这些名字与类型调用）：
  - `REASON_JOB_STATUS: str = "job_status"`、`REASON_JD_DISCRIMINATION: str = "jd_discrimination"`、`REASON_REVISION_LIMIT: str = "revision_limit"`
  - `latest_profile_rows(conn: sqlite3.Connection) -> list[dict]`，每项键：`job_id, title, status, created_at, latest_version (int|None), latest_profile_status (str|None), updated_at, profile (dict)`
  - `revision_counts(conn, *, revision_decision_type: str) -> dict[str, int]`
  - `latest_message_types(conn) -> dict[str, str]`
  - `display_title(row: dict) -> str`
  - `jd_state(profile: dict) -> dict`，键 `generated / needs_manual / human_written`，全为 `bool`
  - `derive_needs_manual_reasons(*, job_status: str, profile: dict, revision_count: int, max_revisions: int) -> list[dict]`，每项 `{"code": str, "label": str}`
  - `stage_label(*, job_status: str, latest_version: int | None, latest_message_type: str | None, jd: dict) -> str`
  - `profile_versions(conn, job_id: str) -> list[dict]`
  - `decision_records(conn, job_id: str) -> list[dict]`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_job_queries.py`：

```python
import ast
import json
import sqlite3
from pathlib import Path

import pytest

from app.graph.nodes import DECISION_REVISION_REQUESTED, MAX_REVISIONS
from app.storage import job_queries
from app.storage.db import init_schema


@pytest.fixture()
def conn(tmp_path):
    connection = sqlite3.connect(str(tmp_path / "q.db"))
    connection.execute("PRAGMA foreign_keys = ON")
    init_schema(connection)
    yield connection
    connection.close()


def _insert_job(conn, job_id, status="drafting", created_at="2026-09-01 10:00:00"):
    conn.execute(
        "INSERT INTO job (id, title, status, created_at) VALUES (?, '待确定', ?, ?)",
        (job_id, status, created_at),
    )


def _insert_version(conn, job_id, version, profile, *, status="drafting",
                    created_at="2026-09-01 10:01:00", derived=(), asked=(),
                    written=(), ungrounded=(), model="deepseek-chat-241226",
                    latency=1234.5, productive=1):
    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json, "
        "unspecified_fields, derived_unspecified_fields, created_at, is_productive, "
        "turn_started_at, llm_latency_ms, ungrounded_fields, written_fields, "
        "llm_response_model, asked_questions) "
        "VALUES (?, ?, ?, ?, ?, '[]', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"{job_id}-v{version}",
            job_id,
            version,
            status,
            json.dumps(profile, ensure_ascii=False),
            json.dumps(list(derived), ensure_ascii=False),
            created_at,
            productive,
            "2026-09-01 10:00:55",
            latency,
            json.dumps(list(ungrounded), ensure_ascii=False),
            json.dumps(list(written), ensure_ascii=False),
            model,
            json.dumps(list(asked), ensure_ascii=False),
        ),
    )


def _insert_review(conn, job_id, version, decision_type, *, reviewer="unknown:web-session",
                   feedback=None, decided_at="2026-09-01 11:00:00"):
    conn.execute(
        "INSERT INTO human_review (id, job_id, profile_version, decision_type, reviewer, "
        "feedback, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            f"{job_id}-{version}-{decision_type}",
            job_id,
            version,
            decision_type,
            reviewer,
            feedback,
            decided_at,
        ),
    )


def test_latest_profile_rows_returns_only_the_newest_version_per_job(conn):
    _insert_job(conn, "j1")
    _insert_version(conn, "j1", 1, {"job_title": "旧标题"}, created_at="2026-09-01 10:01:00")
    _insert_version(conn, "j1", 2, {"job_title": "新标题"}, created_at="2026-09-01 10:05:00")

    rows = job_queries.latest_profile_rows(conn)

    assert len(rows) == 1
    assert rows[0]["latest_version"] == 2
    assert rows[0]["profile"]["job_title"] == "新标题"
    assert rows[0]["updated_at"] == "2026-09-01 10:05:00"


def test_latest_profile_rows_keeps_jobs_that_have_no_profile_yet(conn):
    """第一轮就失败的 job 在库里是"有 job、没有任何 job_profile"。

    INNER JOIN 会让它从列表里彻底消失——而它恰恰是最需要被人看见的那一种。
    """
    _insert_job(conn, "empty", created_at="2026-09-02 09:00:00")

    rows = job_queries.latest_profile_rows(conn)

    assert [row["job_id"] for row in rows] == ["empty"]
    assert rows[0]["latest_version"] is None
    assert rows[0]["profile"] == {}
    assert rows[0]["updated_at"] == "2026-09-02 09:00:00"


def test_latest_profile_rows_sorts_most_recently_active_first(conn):
    _insert_job(conn, "old", created_at="2026-09-01 08:00:00")
    _insert_version(conn, "old", 1, {}, created_at="2026-09-01 08:01:00")
    _insert_job(conn, "new", created_at="2026-09-01 09:00:00")
    _insert_version(conn, "new", 1, {}, created_at="2026-09-03 15:00:00")

    assert [row["job_id"] for row in job_queries.latest_profile_rows(conn)] == ["new", "old"]


def test_revision_counts_only_counts_revision_requests(conn):
    _insert_job(conn, "j1")
    _insert_version(conn, "j1", 1, {})
    _insert_review(conn, "j1", 1, DECISION_REVISION_REQUESTED)
    _insert_review(conn, "j1", 2, DECISION_REVISION_REQUESTED, decided_at="2026-09-01 11:05:00")
    _insert_review(conn, "j1", 3, "approved", decided_at="2026-09-01 11:10:00")

    counts = job_queries.revision_counts(conn, revision_decision_type=DECISION_REVISION_REQUESTED)

    assert counts == {"j1": 2}


def test_latest_message_types_returns_last_row_per_thread(conn):
    conn.execute(
        "INSERT INTO outbox (thread_id, message_type, payload_json) VALUES ('j1', 'question', '{}')"
    )
    conn.execute(
        "INSERT INTO outbox (thread_id, message_type, payload_json) "
        "VALUES ('j1', 'confirmation_prompt', '{}')"
    )
    conn.execute(
        "INSERT INTO outbox (thread_id, message_type, payload_json) VALUES ('j2', 'question', '{}')"
    )

    assert job_queries.latest_message_types(conn) == {
        "j1": "confirmation_prompt",
        "j2": "question",
    }


def test_display_title_prefers_profile_job_title_over_placeholder(conn):
    row = {"title": "待确定", "profile": {"job_title": "  嵌入式软件工程师  "}}

    assert job_queries.display_title(row) == "嵌入式软件工程师"


def test_display_title_falls_back_to_job_row_title_when_profile_has_none(conn):
    assert job_queries.display_title({"title": "待确定", "profile": {}}) == "待确定"
    assert job_queries.display_title({"title": "待确定", "profile": {"job_title": "   "}}) == "待确定"


def test_jd_state_reads_the_three_internal_keys():
    assert job_queries.jd_state({}) == {
        "generated": False,
        "needs_manual": False,
        "human_written": False,
    }
    assert job_queries.jd_state(
        {"_jd_text": "岗位职责…", "_jd_needs_manual": True, "_jd_authorship": {"marked_by": "x"}}
    ) == {"generated": True, "needs_manual": True, "human_written": True}


def test_needs_manual_reasons_empty_for_a_healthy_job():
    assert job_queries.derive_needs_manual_reasons(
        job_status="drafting", profile={}, revision_count=0, max_revisions=MAX_REVISIONS
    ) == []


def test_needs_manual_reason_from_job_status_column():
    """WBS 2.5 落地当天这一条自动生效，⛔ 不许因为"现在无人写入"而省掉。"""
    reasons = job_queries.derive_needs_manual_reasons(
        job_status="needs_manual", profile={}, revision_count=0, max_revisions=MAX_REVISIONS
    )

    assert [r["code"] for r in reasons] == [job_queries.REASON_JOB_STATUS]


def test_needs_manual_reason_from_jd_discrimination_flag():
    reasons = job_queries.derive_needs_manual_reasons(
        job_status="approved",
        profile={"_jd_needs_manual": True},
        revision_count=0,
        max_revisions=MAX_REVISIONS,
    )

    assert [r["code"] for r in reasons] == [job_queries.REASON_JD_DISCRIMINATION]


def test_needs_manual_reason_from_revision_limit_and_label_carries_the_number():
    reasons = job_queries.derive_needs_manual_reasons(
        job_status="drafting", profile={}, revision_count=5, max_revisions=5
    )

    assert [r["code"] for r in reasons] == [job_queries.REASON_REVISION_LIMIT]
    # 上限数字不能在文案里写死：写死之后改 MAX_REVISIONS 界面上不会跟着变，
    # 而且不报错——业务经理看到的上限和系统实际执行的上限会悄悄不一致。
    assert "5" in reasons[0]["label"]


def test_needs_manual_reasons_can_stack():
    reasons = job_queries.derive_needs_manual_reasons(
        job_status="needs_manual",
        profile={"_jd_needs_manual": True},
        revision_count=9,
        max_revisions=5,
    )

    assert [r["code"] for r in reasons] == [
        job_queries.REASON_JOB_STATUS,
        job_queries.REASON_JD_DISCRIMINATION,
        job_queries.REASON_REVISION_LIMIT,
    ]


def test_stage_label_covers_every_reachable_state():
    healthy_jd = {"generated": False, "needs_manual": False, "human_written": False}
    generated_jd = {"generated": True, "needs_manual": False, "human_written": False}

    assert job_queries.stage_label(
        job_status="abandoned", latest_version=2, latest_message_type=None, jd=healthy_jd
    ) == "已放弃"
    assert job_queries.stage_label(
        job_status="needs_manual", latest_version=2, latest_message_type=None, jd=healthy_jd
    ) == "待人工处理"
    assert job_queries.stage_label(
        job_status="approved", latest_version=2, latest_message_type=None, jd=generated_jd
    ) == "已确认 · JD 已生成"
    assert job_queries.stage_label(
        job_status="approved", latest_version=2, latest_message_type=None, jd=healthy_jd
    ) == "已确认"
    assert job_queries.stage_label(
        job_status="drafting", latest_version=None, latest_message_type=None, jd=healthy_jd
    ) == "刚发起，还没有画像"
    assert job_queries.stage_label(
        job_status="drafting", latest_version=1,
        latest_message_type="confirmation_prompt", jd=healthy_jd
    ) == "等你确认"
    assert job_queries.stage_label(
        job_status="drafting", latest_version=1, latest_message_type="question", jd=healthy_jd
    ) == "追问中"


def test_stage_label_never_leaks_english_status():
    """合规不相干，但界面纪律相干：⛔ 英文 status 不得出现在业务经理眼前。"""
    for status in ("drafting", "approved", "abandoned", "needs_manual"):
        label = job_queries.stage_label(
            job_status=status,
            latest_version=1,
            latest_message_type="question",
            jd={"generated": False, "needs_manual": False, "human_written": False},
        )
        assert status not in label


def test_profile_versions_returns_every_version_ascending_with_snapshot(conn):
    _insert_job(conn, "j1")
    _insert_version(
        conn, "j1", 1, {"job_title": "嵌入式工程师", "department": "研发部"},
        derived=["experience_years"], asked=[{"question_id": "q1"}, {"question_id": "q2"}],
        written=["job_title"], ungrounded=["mcu_family"],
        model="deepseek-chat-241226", latency=2100.0,
    )
    _insert_version(
        conn, "j1", 2, {"job_title": "嵌入式工程师", "_jd_text": "岗位职责…"},
        status="approved", created_at="2026-09-01 10:20:00", productive=0,
    )

    versions = job_queries.profile_versions(conn, "j1")

    assert [v["version"] for v in versions] == [1, 2]
    first = versions[0]
    assert first["status_label"] == "草案"
    assert first["is_productive"] is True
    assert first["unspecified_fields"] == ["experience_years"]
    # 界面只认中文名（Global Constraints 第 13 条）。
    assert first["unspecified_field_labels"] and "experience_years" not in first["unspecified_field_labels"][0]
    assert first["asked_question_count"] == 2
    assert first["snapshot"]["llm_response_model"] == "deepseek-chat-241226"
    assert first["snapshot"]["llm_latency_ms"] == 2100.0
    assert first["snapshot"]["ungrounded_fields"] == ["mcu_family"]
    assert first["snapshot"]["written_fields"] == ["job_title"]
    assert first["jd"]["generated"] is False
    assert any(item["value"] == "嵌入式工程师" for item in first["summary"])

    second = versions[1]
    assert second["status_label"] == "已确认"
    assert second["is_productive"] is False
    assert second["jd"]["generated"] is True


def test_profile_versions_never_leaks_internal_underscore_keys_into_summary(conn):
    """`_jd_text` / `_gap_acknowledgement` 这类内部键 ⛔ 不得出现在摘要里。"""
    _insert_job(conn, "j1")
    _insert_version(
        conn, "j1", 1,
        {"job_title": "工程师", "_jd_text": "这是 JD 正文", "_gap_acknowledgement": {"acknowledged": True}},
    )

    summary = job_queries.profile_versions(conn, "j1")[0]["summary"]

    rendered = json.dumps(summary, ensure_ascii=False)
    assert "_jd_text" not in rendered
    assert "这是 JD 正文" not in rendered
    assert "_gap_acknowledgement" not in rendered


def test_profile_versions_tolerates_legacy_rows_with_null_snapshot_columns(conn):
    """.51 上 2026-08-19 之前写的行这些列是 NULL / '[]'，⛔ 不能因此抛异常。"""
    _insert_job(conn, "j1")
    _insert_version(conn, "j1", 1, {"job_title": "工程师"}, model=None, latency=None)

    snapshot = job_queries.profile_versions(conn, "j1")[0]["snapshot"]

    assert snapshot["llm_response_model"] is None
    assert snapshot["llm_latency_ms"] is None


def test_decision_records_are_chronological_and_labelled_in_chinese(conn):
    _insert_job(conn, "j1")
    _insert_version(conn, "j1", 1, {})
    _insert_review(conn, "j1", 1, DECISION_REVISION_REQUESTED,
                   feedback="人数改成 3 个", decided_at="2026-09-01 11:00:00")
    _insert_review(conn, "j1", 2, "approved", decided_at="2026-09-01 12:00:00")

    records = job_queries.decision_records(conn, "j1")

    assert [r["profile_version"] for r in records] == [1, 2]
    assert records[0]["decision_label"] == "要求修改"
    assert records[0]["feedback"] == "人数改成 3 个"
    assert records[1]["decision_label"] == "确认"
    for record in records:
        assert record["decision_type"] not in record["decision_label"]


def _non_docstring_literals(path: str) -> list[str]:
    """模块里所有**非 docstring** 的字符串字面量。

    ⛔ 不扫整份源码：本模块的注释里逐字写着"⛔ 一条 INSERT / UPDATE / DELETE
    都不许有"这类说明，扫全文会被自己的注释判违例——而一条永远红的断言等于
    没有断言，下一个人只会把它删掉。注释根本不进 AST，docstring 显式排除，
    剩下的字符串字面量正好就是 SQL 所在的地方。
    """
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    docstring_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if ast.get_docstring(node, clean=False) is not None:
                docstring_nodes.add(id(node.body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstring_nodes
    ]


def test_module_contains_no_write_statements():
    """本单元的硬边界（Global Constraints 第 1/7 条）：只读模块里出现任何一条
    写语句都是违例。这条断言是机器判据，⛔ 不要靠 review 眼力守。"""
    for literal in _non_docstring_literals("app/storage/job_queries.py"):
        upper = literal.upper()
        for statement in ("INSERT INTO", "UPDATE ", "DELETE FROM", "ALTER TABLE", "DROP TABLE"):
            assert statement not in upper, f"只读查询层里不许出现 {statement}：{literal!r}"


def test_module_does_not_import_graph_or_agents_layer():
    """Global Constraints 第 10 条：storage → graph 是层次倒置。

    按 AST 里真实的 import 判，⛔ 不按文本包含判：模块注释里正写着"本模块不
    import app.graph"，文本判会把这句说明本身当成违例。
    """
    tree = ast.parse(Path("app/storage/job_queries.py").read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")

    for name in imported:
        assert not name.startswith("app.graph"), f"⛔ 层次倒置：{name}"
        assert not name.startswith("app.agents"), f"⛔ 层次倒置：{name}"
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `venv/bin/python -m pytest tests/test_job_queries.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.storage.job_queries'`（收集阶段就报错，20 个用例全部无法执行）

- [ ] **Step 3: 写实现**

创建 `app/storage/job_queries.py`：

```python
from __future__ import annotations

import json
import sqlite3

from app.schemas.job_profile import field_labels, summarize_profile

# ─────────────────────────────────────────────────────────────────────────────
# 会话之外三个只读视图的查询层（m1-job-profile-intake tasks 8.1 / 8.2 / 8.4）。
#
# ⛔ 本模块只读：一条 INSERT / UPDATE / DELETE / ALTER / DROP 都不许有。三个
#    视图都是把已经落库的事实读出来摆好，加一条写入就越过了本交付单元的边界，
#    也会让"这几个页面可以放心给业务经理点"这个前提不再成立。
#    机器判据见 tests/test_job_queries.py::test_module_contains_no_write_statements。
#
# ⛔ 本模块不 import app.graph / app.agents：app/graph/nodes.py 已经 import 了
#    app/storage/idempotency.py，反向再导一次就是层次倒置。需要 graph 层的常量
#    （MAX_REVISIONS、DECISION_REVISION_REQUESTED）时由调用方 app/web/server.py
#    当参数传进来——它本来就 import 着这两个名字。⛔ 也不在这里重抄字面量：
#    重抄就多一个会漂移的真源，而漂移没有任何症状，只是队列的上限判定悄悄和
#    revise() 的判定对不上。
# ─────────────────────────────────────────────────────────────────────────────


# 转人工的三个理由码。与前端 index.html 无耦合（前端只渲染 label，不认 code），
# code 存在是为了让测试与将来的过滤按稳定标识来写，而不是按会改的中文文案。
REASON_JOB_STATUS = "job_status"
REASON_JD_DISCRIMINATION = "jd_discrimination"
REASON_REVISION_LIMIT = "revision_limit"

# 英文 status → 中文标签。⛔ 界面上不得出现英文 snake_case 或英文 status
# （与 index.html:211-213 既有约束同源，那正是第 6 章修过一遍的故障现象）。
# 兜底返回原值而不是抛异常：出现未登记的取值时，界面上会显示一个刺眼的英文串，
# 这是**可见**的故障；抛异常则会让整个详情页白屏，把一个显示问题升级成不可用。
_PROFILE_STATUS_LABELS = {
    "drafting": "草案",
    "approved": "已确认",
    "abandoned": "已放弃",
}

# 与 app/graph/nodes.py 的 DECISION_* 常量、app/storage/db.py 的
# human_review.decision_type CHECK 逐字同源（那三处已经互为同源，这里是第四处
# **只读**的消费方）。⛔ 不要在这里新增取值——新增取值的正确做法是先改那三处。
_DECISION_LABELS = {
    "approved": "确认",
    "revision_requested": "要求修改",
    "abandoned": "放弃",
}


def _loads_list(raw: str | None) -> list:
    """JSON 列列的容错读取。

    .51 上 2026-08-19 之前写的历史行这些列可能是 NULL（加列时给的是 '[]'，
    但更早的行经由别的路径写入过 NULL）。列表页不能因为一行历史数据整页 500。
    """
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def latest_profile_rows(conn: sqlite3.Connection) -> list[dict]:
    """每个 job 一行：job 表字段 + 该 job 版本号最大的那一版 job_profile。

    LEFT JOIN 而不是 INNER JOIN：POST /api/jobs 先 INSERT job 再跑第一轮
    （app/web/server.py 的 create_job），第一轮如果失败，库里就留下一个
    "有 job、没有任何 job_profile"的行。INNER JOIN 会让这种 job 在列表里彻底
    消失——而它恰恰是最需要被人看见的那一种（"我提过一个需求，怎么找不到了"）。

    排序按"最近有动静"倒序：列表回答的是"最近发生了什么"。队列的排序刻意相反，
    见 app/web/server.py 的 needs_manual_queue()。
    """
    rows = conn.execute(
        "SELECT j.id, j.title, j.status, j.created_at, "
        "       p.version, p.status, p.created_at, p.profile_json "
        "FROM job j "
        "LEFT JOIN job_profile p "
        "       ON p.job_id = j.id "
        "      AND p.version = (SELECT MAX(v.version) FROM job_profile v WHERE v.job_id = j.id) "
        "ORDER BY COALESCE(p.created_at, j.created_at) DESC, j.id DESC"
    ).fetchall()

    result: list[dict] = []
    for row in rows:
        profile = {}
        if row[7]:
            try:
                loaded = json.loads(row[7])
                profile = loaded if isinstance(loaded, dict) else {}
            except (TypeError, ValueError):
                profile = {}
        result.append(
            {
                "job_id": row[0],
                "title": row[1],
                "status": row[2],
                "created_at": row[3],
                "latest_version": row[4],
                "latest_profile_status": row[5],
                "updated_at": row[6] or row[3],
                "profile": profile,
            }
        )
    return result


def revision_counts(conn: sqlite3.Connection, *, revision_decision_type: str) -> dict[str, int]:
    """每个 job 的修改次数。真源是 human_review 行数，与
    app/graph/nodes.py::revision_count 同一口径——⛔ 不另存计数列。

    一次聚合查询覆盖全部 job，⛔ 不在列表里逐个 job 调 revision_count()：
    那是 N+1，列表页一打开就是几十次查询。
    """
    return {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT job_id, COUNT(*) FROM human_review WHERE decision_type = ? GROUP BY job_id",
            (revision_decision_type,),
        )
    }


def latest_message_types(conn: sqlite3.Connection) -> dict[str, str]:
    """每个 thread 最后一条 outbox 消息的类型（用来区分"追问中"与"等你确认"）。

    同样是一次聚合，⛔ 不逐个 job 调 WebChannel.latest()。
    """
    return {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT o.thread_id, o.message_type FROM outbox o "
            "WHERE o.id = (SELECT MAX(x.id) FROM outbox x WHERE x.thread_id = o.thread_id)"
        )
    }


def display_title(row: dict) -> str:
    """列表里显示的岗位名。

    真源是**最新一版画像里的 job_title**，⛔ 不是 job.title：job.title 在
    create_job 里被写死成 '待确定'（app/web/server.py:268），此后没有任何代码
    更新它（全仓库只有两条 UPDATE job SET，都只改 status）。拿 job.title 当
    标题，整个列表会是一列一模一样的「待确定」。

    ⛔ 也不要顺手把画像标题回写进 job.title —— 本单元只读。
    """
    title = row.get("profile", {}).get("job_title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return row["title"]


def jd_state(profile: dict) -> dict:
    """这一版画像的 JD 状态。三个键都读 profile_json 里的下划线内部键
    （交付单元 7 确立的做法：走内部键，不建新表）。

    ⛔ 只回布尔，不回 JD 正文：详情页不渲染正文（Global Constraints 第 5 条），
    正文有专门的、合规上已过审的展示位 GET /api/jobs/{id}/jd。
    """
    return {
        "generated": bool(profile.get("_jd_text")),
        "needs_manual": bool(profile.get("_jd_needs_manual")),
        "human_written": bool(profile.get("_jd_authorship")),
    }


def derive_needs_manual_reasons(
    *,
    job_status: str,
    profile: dict,
    revision_count: int,
    max_revisions: int,
) -> list[dict]:
    """这个岗位为什么需要 HR 人工介入。空列表 = 不需要。

    ⚠️ 队列是**推导出来的视图**，不是一个状态列。JobStatus.NEEDS_MANUAL
    （app/schemas/job_profile.py:16）至今没有任何写入方（WBS 2.5 未做），
    只认它的话队列永远是空的——而"空队列"和"没人需要处理"在界面上长得一模
    一样，这是一个没有任何症状的故障。三个来源都查：

      1) job.status = 'needs_manual'：今天恒为空，2.5 落地当天自动生效。
         ⛔ 不要因为"现在查不到"就省掉这一条
      2) profile_json._jd_needs_manual：JD 连续 2 次触发歧视性表述检测后由
         app/graph/nodes.py 的 effect_generate_and_persist_jd 落库
         （今天唯一真实存在的写入方）
      3) 修改次数达上限：spec「修改次数上限」要求"提示转人工"，而
         app/web/server.py 的 revise() 只在那一次 409 响应里说了一句话，
         页面一关就没了。这里把它变成一条查得到的事实

    ⛔ "放弃（abandoned）的岗位不进队列"这条过滤**不在本函数里**做：本函数只
    回答"有哪些理由"，"要不要进队列"是队列端点的事。两件事混在一起会让
    详情页也拿不到理由——而详情页恰恰应该显示"这个岗位当初为什么被转人工"。
    """
    reasons: list[dict] = []
    if job_status == "needs_manual":
        reasons.append({"code": REASON_JOB_STATUS, "label": "岗位状态已被置为「转人工」"})
    if profile.get("_jd_needs_manual"):
        reasons.append(
            {
                "code": REASON_JD_DISCRIMINATION,
                "label": "JD 连续 2 次触发歧视性表述检测，已转人工；请核对文案后再发布",
            }
        )
    if revision_count >= max_revisions:
        reasons.append(
            {
                "code": REASON_REVISION_LIMIT,
                # 上限数字从入参渲染，⛔ 不在文案里写死：写死之后改 MAX_REVISIONS
                # 界面上不会跟着变，而且不报错——业务经理看到的上限和系统实际
                # 执行的上限会悄悄不一致。
                "label": f"画像修改已达上限 {max_revisions} 次，请由 HR 直接编辑画像后提交确认",
            }
        )
    return reasons


def stage_label(
    *,
    job_status: str,
    latest_version: int | None,
    latest_message_type: str | None,
    jd: dict,
) -> str:
    """列表里那一列中文状态。⛔ 不把英文 status 直接摆给业务经理看。

    判定顺序即优先级：终态压过过程态。一个已放弃的岗位即使最后一条消息是
    confirmation_prompt，显示的也必须是「已放弃」——否则界面会在邀请人去点
    一个服务端已经用 409 挡死的按钮。
    """
    if job_status == "abandoned":
        return "已放弃"
    if job_status == "needs_manual":
        return "待人工处理"
    if job_status == "approved":
        return "已确认 · JD 已生成" if jd["generated"] else "已确认"
    if latest_version is None:
        return "刚发起，还没有画像"
    if latest_message_type == "confirmation_prompt":
        return "等你确认"
    return "追问中"


def profile_versions(conn: sqlite3.Connection, job_id: str) -> list[dict]:
    """一个岗位的完整版本历史，按 version 升序，每版带一份生成快照。

    ⚠️ 缺口读的是 derived_unspecified_fields 这一列，**不是** unspecified_fields：
    后者存的是"模型自称的"，已降级为对照（app/storage/db.py:31-33 的注释与
    docs/tech-debt.md TD-2）。读错列会让详情页显示一份和确认页不一致的缺口清单，
    而两边都不报错。

    ⚠️ 快照里的模型标识取 llm_response_model（API 响应实际返回的 model 字段，
    工程铁律 5 的落点），⛔ 不取配置里写的 settings.llm_model——那正是这条铁律
    要区分开的两个东西。
    """
    rows = conn.execute(
        "SELECT version, status, created_at, is_productive, derived_unspecified_fields, "
        "       ungrounded_fields, written_fields, llm_response_model, llm_latency_ms, "
        "       turn_started_at, asked_questions, profile_json "
        "FROM job_profile WHERE job_id = ? ORDER BY version ASC",
        (job_id,),
    ).fetchall()

    versions: list[dict] = []
    for row in rows:
        try:
            loaded = json.loads(row[11])
            profile = loaded if isinstance(loaded, dict) else {}
        except (TypeError, ValueError):
            profile = {}
        unspecified = [name for name in _loads_list(row[4]) if isinstance(name, str)]
        versions.append(
            {
                "version": row[0],
                "status": row[1],
                "status_label": _PROFILE_STATUS_LABELS.get(row[1], row[1]),
                "created_at": row[2],
                "is_productive": bool(row[3]),
                "unspecified_fields": unspecified,
                "unspecified_field_labels": field_labels(unspecified),
                "asked_question_count": len(_loads_list(row[10])),
                # summarize_profile 按 FIELD_LABELS 声明序只输出有值的业务字段，
                # profile_json 里以下划线开头的内部键（_jd_text /
                # _gap_acknowledgement / _jd_authorship）天然不在其中。
                "summary": summarize_profile(profile),
                "snapshot": {
                    "llm_response_model": row[7],
                    "llm_latency_ms": row[8],
                    "turn_started_at": row[9],
                    "completed_at": row[2],
                    "ungrounded_fields": _loads_list(row[5]),
                    "written_fields": _loads_list(row[6]),
                },
                "jd": jd_state(profile),
            }
        )
    return versions


def decision_records(conn: sqlite3.Connection, job_id: str) -> list[dict]:
    """一个岗位的人工决策留痕（spec「决策留痕」：谁、什么时候、决定了哪一版）。

    ⛔ 只读 human_review，不做任何补写。查不到记录时返回空列表——"这个岗位
    还没有人做过决策"和"留痕漏了"由 app/audit/assertions.py 的断言四去区分，
    ⛔ 不在展示层替它下结论。
    """
    rows = conn.execute(
        "SELECT profile_version, decision_type, reviewer, feedback, decided_at "
        "FROM human_review WHERE job_id = ? ORDER BY decided_at ASC, profile_version ASC",
        (job_id,),
    ).fetchall()
    return [
        {
            "profile_version": row[0],
            "decision_type": row[1],
            "decision_label": _DECISION_LABELS.get(row[1], row[1]),
            "reviewer": row[2],
            "feedback": row[3],
            "decided_at": row[4],
        }
        for row in rows
    ]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest tests/test_job_queries.py -q`
Expected: PASS，21 passed

- [ ] **Step 5: 跑一次全量回归，确认没碰坏别的**

Run: `venv/bin/python -m pytest -q -p no:randomly`
Expected: **1029 passed, 1 skipped**（基线 1008 passed / 1 skipped，本 Task 新增 21 条）。⛔ 出现任何既有用例失败都必须当场定位，不许标记 xfail 绕过。

- [ ] **Step 6: 提交**

```bash
git add app/storage/job_queries.py tests/test_job_queries.py
git commit -m "feat(web): 岗位列表/详情/转人工队列的只读查询层（tasks 8.1/8.2/8.4）"
```

---

### Task 2: `GET /api/jobs` 岗位列表端点（8.1）

**Files:**
- Modify: `app/web/server.py`（追加 import、一个私有 helper、一个端点；⛔ 不动既有 11 个端点的任何一行）
- Test: `tests/test_job_views_api.py`（新建，含 Task 3/4 共用的测试脚手架）

**Interfaces:**
- Consumes: Task 1 的 `latest_profile_rows` / `revision_counts` / `latest_message_types` / `display_title` / `jd_state` / `derive_needs_manual_reasons` / `stage_label`；已存在的 `app.graph.nodes.MAX_REVISIONS`、`app.graph.nodes.DECISION_REVISION_REQUESTED`
- Produces（Task 3/4 与前端按这些名字调用）：
  - `app/web/server.py` 内的 `_job_row_payload(row: dict, counts: dict, message_types: dict) -> dict`
  - `GET {root_path}/api/jobs` → `{"jobs": [ {job_id, title, status, stage_label, created_at, updated_at, latest_version, revision_count, jd, needs_manual, needs_manual_reasons} ]}`
  - 测试脚手架 `_make_app(tmp_path, root_path="")`、`_seed_job(conn, ...)`、`_seed_version(conn, ...)`、`_seed_review(conn, ...)`、`_seed_outbox(conn, ...)`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_job_views_api.py`：

```python
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.graph.nodes import DECISION_REVISION_REQUESTED, MAX_REVISIONS
from app.llm.gateway import LLMGateway
from app.web.server import create_app


class _NeverCalledCompletions:
    """三个视图端点全是只读 GET。任何一次模型调用都是 bug，当场戳穿。"""

    def create(self, **kwargs):
        raise AssertionError("只读视图端点不得触发任何 LLM 调用")


class _NeverCalledChat:
    def __init__(self):
        self.completions = _NeverCalledCompletions()


class _NeverCalledClient:
    def __init__(self):
        self.chat = _NeverCalledChat()


def _make_app(tmp_path, root_path: str = ""):
    """建 app 并额外开一条**独立连接**直接写测试数据。

    直接写库而不是走 POST /api/jobs 跑真实链路：三个端点都是只读的，用真实
    链路造数据要脚本化好几轮 LLM 响应，而那些响应内容与本单元要断言的东西
    毫无关系——测试会变成在测别人的代码。库文件同一份，WAL 模式下两条连接
    并存是既有做法（app/storage/db.py 的 get_connection 已开 WAL）。
    """
    db_path = str(tmp_path / "views.db")

    def gateway_factory():
        return LLMGateway(
            api_key="k",
            base_url="https://example.com",
            model="deepseek-chat-241226",
            supports_json_schema=False,
            client=_NeverCalledClient(),
        )

    app = create_app(db_path=db_path, gateway_factory=gateway_factory, root_path=root_path)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    return TestClient(app), conn


def _seed_job(conn, job_id, *, status="drafting", created_at="2026-09-01 10:00:00"):
    conn.execute(
        "INSERT INTO job (id, title, status, created_at) VALUES (?, '待确定', ?, ?)",
        (job_id, status, created_at),
    )
    conn.commit()


def _seed_version(
    conn,
    job_id,
    version,
    profile,
    *,
    status="drafting",
    created_at="2026-09-01 10:01:00",
    derived=(),
    asked=(),
    written=(),
    ungrounded=(),
    model="deepseek-chat-241226",
    latency=1500.0,
    productive=1,
):
    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json, "
        "unspecified_fields, derived_unspecified_fields, created_at, is_productive, "
        "turn_started_at, llm_latency_ms, ungrounded_fields, written_fields, "
        "llm_response_model, asked_questions) "
        "VALUES (?, ?, ?, ?, ?, '[]', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"{job_id}-v{version}",
            job_id,
            version,
            status,
            json.dumps(profile, ensure_ascii=False),
            json.dumps(list(derived), ensure_ascii=False),
            created_at,
            productive,
            "2026-09-01 10:00:55",
            latency,
            json.dumps(list(ungrounded), ensure_ascii=False),
            json.dumps(list(written), ensure_ascii=False),
            model,
            json.dumps(list(asked), ensure_ascii=False),
        ),
    )
    conn.commit()


def _seed_review(
    conn, job_id, version, decision_type, *, reviewer="unknown:web-session",
    feedback=None, decided_at="2026-09-01 11:00:00",
):
    conn.execute(
        "INSERT INTO human_review (id, job_id, profile_version, decision_type, reviewer, "
        "feedback, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            f"{job_id}-{version}-{decision_type}",
            job_id,
            version,
            decision_type,
            reviewer,
            feedback,
            decided_at,
        ),
    )
    conn.commit()


def _seed_outbox(conn, job_id, message_type):
    conn.execute(
        "INSERT INTO outbox (thread_id, message_type, payload_json) VALUES (?, ?, '{}')",
        (job_id, message_type),
    )
    conn.commit()


# ── 8.1 岗位列表 ─────────────────────────────────────────────────────────────


def test_list_jobs_returns_empty_list_when_there_is_nothing(tmp_path):
    client, _ = _make_app(tmp_path)

    resp = client.get("/api/jobs")

    assert resp.status_code == 200
    assert resp.json() == {"jobs": []}


def test_list_jobs_uses_profile_job_title_not_the_placeholder(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1")
    _seed_version(conn, "j1", 1, {"job_title": "嵌入式软件工程师"})

    job = client.get("/api/jobs").json()["jobs"][0]

    assert job["title"] == "嵌入式软件工程师"
    assert job["title"] != "待确定"


def test_list_jobs_shows_chinese_stage_label_never_english_status(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1")
    _seed_version(conn, "j1", 1, {"job_title": "A"})
    _seed_outbox(conn, "j1", "confirmation_prompt")

    job = client.get("/api/jobs").json()["jobs"][0]

    assert job["stage_label"] == "等你确认"
    assert "drafting" not in job["stage_label"]


def test_list_jobs_sorts_most_recently_active_first(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "old", created_at="2026-09-01 08:00:00")
    _seed_version(conn, "old", 1, {"job_title": "旧"}, created_at="2026-09-01 08:01:00")
    _seed_job(conn, "new", created_at="2026-09-01 09:00:00")
    _seed_version(conn, "new", 1, {"job_title": "新"}, created_at="2026-09-03 15:00:00")

    ids = [job["job_id"] for job in client.get("/api/jobs").json()["jobs"]]

    assert ids == ["new", "old"]


def test_list_jobs_includes_a_job_with_no_profile_yet(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "empty")

    job = client.get("/api/jobs").json()["jobs"][0]

    assert job["job_id"] == "empty"
    assert job["latest_version"] is None
    assert job["stage_label"] == "刚发起，还没有画像"


def test_list_jobs_reports_revision_count_and_jd_state(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", status="approved")
    _seed_version(conn, "j1", 2, {"job_title": "A", "_jd_text": "岗位职责…"}, status="approved")
    _seed_review(conn, "j1", 1, DECISION_REVISION_REQUESTED)

    job = client.get("/api/jobs").json()["jobs"][0]

    assert job["revision_count"] == 1
    assert job["jd"] == {"generated": True, "needs_manual": False, "human_written": False}
    assert job["stage_label"] == "已确认 · JD 已生成"


def test_list_jobs_flags_needs_manual_with_a_readable_reason(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", status="approved")
    _seed_version(
        conn, "j1", 1, {"job_title": "A", "_jd_text": "…", "_jd_needs_manual": True},
        status="approved",
    )

    job = client.get("/api/jobs").json()["jobs"][0]

    assert job["needs_manual"] is True
    assert len(job["needs_manual_reasons"]) == 1
    assert "歧视" in job["needs_manual_reasons"][0]["label"]


def test_list_jobs_does_not_flag_a_healthy_job(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1")
    _seed_version(conn, "j1", 1, {"job_title": "A"})

    job = client.get("/api/jobs").json()["jobs"][0]

    assert job["needs_manual"] is False
    assert job["needs_manual_reasons"] == []


def test_list_jobs_never_leaks_jd_text_into_the_list_payload(tmp_path):
    """列表是概览，JD 正文有专门的展示位（GET /api/jobs/{id}/jd）。
    把正文塞进列表会让每一行都背着一大段文案，也多一个必须自己保证 AI 标识
    不被裁掉的地方（Global Constraints 第 5 条）。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", status="approved")
    _seed_version(conn, "j1", 1, {"job_title": "A", "_jd_text": "这是一整段 JD 正文"}, status="approved")

    body = client.get("/api/jobs").text

    assert "这是一整段 JD 正文" not in body


def test_list_jobs_is_mounted_under_the_configured_root_path(tmp_path):
    """部署约束 1：挂到任意子路径下都要能工作，不带前缀的路径必须 404。"""
    client, conn = _make_app(tmp_path, root_path="/hr/recruit-agent")
    _seed_job(conn, "j1")

    assert client.get("/hr/recruit-agent/api/jobs").status_code == 200
    assert client.get("/api/jobs").status_code == 404


def test_list_jobs_does_not_write_anything(tmp_path):
    """只读端点的机器判据：调用前后 job / job_profile / human_review /
    effect_log / outbox 的行数逐表恒等。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1")
    _seed_version(conn, "j1", 1, {"job_title": "A"})
    _seed_outbox(conn, "j1", "question")

    tables = ("job", "job_profile", "human_review", "effect_log", "outbox")
    before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}

    client.get("/api/jobs")
    client.get("/api/jobs")

    after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    assert before == after
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `venv/bin/python -m pytest tests/test_job_views_api.py -q`
Expected: FAIL — 11 条里大部分报 `assert 404 == 200`（`GET /api/jobs` 尚未注册，FastAPI 返回 404）

- [ ] **Step 3: 写实现**

在 `app/web/server.py` 的 import 段，把既有的 `from app.graph.nodes import (...)` 补上 `DECISION_REVISION_REQUESTED`（既有块原样保留，只加一行）：

```python
from app.graph.nodes import (
    DECISION_REVISION_REQUESTED,
    MAX_REVISIONS,
    effect_abandon_profile,
    effect_confirm_profile,
    effect_generate_and_persist_jd,
    effect_request_revision,
    revision_count,
)
```

在既有的 `from app.storage.db import ...` 之前加一行（保持 import 按模块路径排序）：

```python
from app.storage import job_queries
```

在 `app/web/server.py` 里 `create_app()` 内部、`@router.get("/api/jobs/{job_id}")` 那个既有端点**之前**，插入下面这段（helper + 端点）：

```python
    # ── 会话之外的只读视图（tasks 8.1 / 8.2 / 8.4）────────────────────────
    #
    # ⛔ 这一段里三个端点全是 GET，且不许有别的：列表、详情、队列都只是把已经
    # 落库的事实读出来摆好。加写入就越过了本交付单元的边界，也会让"这几个页面
    # 可以放心给业务经理点"这个前提不再成立。
    #
    # ⚠️ MAX_REVISIONS 与 DECISION_REVISION_REQUESTED 由这里**传进**查询层，
    # 不在 app/storage/job_queries.py 里重抄：那两个名字的真源是
    # app/graph/nodes.py，而 storage 层 import graph 层是层次倒置
    # （graph 已经在 import storage/idempotency.py）。

    def _job_row_payload(row: dict, counts: dict, message_types: dict) -> dict:
        """列表与队列共用同一个行形状。两处各拼一份的话，将来加一个字段必然
        只加在其中一处，而两个页面显示不一致这件事没有测试会自己发现。"""
        profile = row["profile"]
        jd = job_queries.jd_state(profile)
        revisions = counts.get(row["job_id"], 0)
        reasons = job_queries.derive_needs_manual_reasons(
            job_status=row["status"],
            profile=profile,
            revision_count=revisions,
            max_revisions=MAX_REVISIONS,
        )
        return {
            "job_id": row["job_id"],
            "title": job_queries.display_title(row),
            "status": row["status"],
            "stage_label": job_queries.stage_label(
                job_status=row["status"],
                latest_version=row["latest_version"],
                latest_message_type=message_types.get(row["job_id"]),
                jd=jd,
            ),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "latest_version": row["latest_version"],
            "revision_count": revisions,
            # ⛔ 只回 JD 的三个布尔状态，不回正文：正文有专门的、合规上已过审的
            # 展示位（GET /api/jobs/{job_id}/jd）。多一个渲染正文的地方就多一个
            # 会漏掉 AI 生成标识的地方。
            "jd": jd,
            "needs_manual": bool(reasons),
            "needs_manual_reasons": reasons,
        }

    def _job_rows_with_context() -> tuple[list[dict], dict, dict]:
        """列表与队列都要的三次查询。⛔ 不做逐 job 的 N+1 查询。"""
        return (
            job_queries.latest_profile_rows(conn),
            job_queries.revision_counts(
                conn, revision_decision_type=DECISION_REVISION_REQUESTED
            ),
            job_queries.latest_message_types(conn),
        )

    @router.get("/api/jobs")
    def list_jobs() -> dict:
        """8.1 岗位列表与状态视图。只读。

        ⛔ 不分页：M1 的量级是"日均新增岗位个位数"（design.md 非目标：不追求
        高并发），加分页只会多一套前后端要对齐的状态。量级变了再说。
        """
        rows, counts, message_types = _job_rows_with_context()
        return {"jobs": [_job_row_payload(row, counts, message_types) for row in rows]}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest tests/test_job_views_api.py -q`
Expected: PASS，11 passed

- [ ] **Step 5: 跑一次全量回归**

Run: `venv/bin/python -m pytest -q -p no:randomly`
Expected: **1040 passed, 1 skipped**。特别确认 `tests/test_web_api.py` 的 29 条与 `tests/test_jd_endpoints.py` 全部照旧通过——新增端点⛔ 不得改变既有端点的任何行为。

- [ ] **Step 6: 提交**

```bash
git add app/web/server.py tests/test_job_views_api.py
git commit -m "feat(web): GET /api/jobs 岗位列表与中文状态视图（tasks 8.1）"
```

---

### Task 3: `GET /api/jobs/{job_id}/profile` 画像详情端点（8.2）

**Files:**
- Modify: `app/web/server.py`（追加一个端点 + 一个常量）
- Test: `tests/test_job_views_api.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `profile_versions` / `decision_records` / `display_title` / `jd_state` / `stage_label`；Task 2 的 `_job_rows_with_context`（不复用，详情按单个 job 查）
- Produces：`GET {root_path}/api/jobs/{job_id}/profile` → `{job_id, title, status, stage_label, created_at, latest_version, snapshot_note, versions: [...], decisions: [...]}`；`versions[]` 的键见 Task 1 `profile_versions` 的 Produces

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_job_views_api.py` 末尾：

```python
# ── 8.2 画像详情（版本历史 + 生成快照）─────────────────────────────────────


def test_profile_detail_404_for_unknown_job(tmp_path):
    client, _ = _make_app(tmp_path)

    assert client.get("/api/jobs/nope/profile").status_code == 404


def test_profile_detail_returns_every_version_in_order(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", status="approved")
    _seed_version(conn, "j1", 1, {"job_title": "嵌入式工程师"}, created_at="2026-09-01 10:01:00")
    _seed_version(
        conn, "j1", 2, {"job_title": "嵌入式软件工程师"},
        status="approved", created_at="2026-09-01 10:20:00",
    )

    data = client.get("/api/jobs/j1/profile").json()

    assert data["latest_version"] == 2
    assert [v["version"] for v in data["versions"]] == [1, 2]
    assert data["title"] == "嵌入式软件工程师"
    assert data["versions"][1]["status_label"] == "已确认"


def test_profile_detail_version_carries_a_generation_snapshot(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1")
    _seed_version(
        conn, "j1", 1, {"job_title": "A"},
        model="deepseek-chat-241226", latency=2100.0,
        written=["job_title"], ungrounded=["mcu_family"],
        asked=[{"question_id": "q1"}, {"question_id": "q2"}, {"question_id": "q3"}],
    )

    version = client.get("/api/jobs/j1/profile").json()["versions"][0]

    snapshot = version["snapshot"]
    # 工程铁律 5：模型标识必须是 API 响应实际返回的那个，不是配置里写的。
    assert snapshot["llm_response_model"] == "deepseek-chat-241226"
    assert snapshot["llm_latency_ms"] == 2100.0
    assert snapshot["written_fields"] == ["job_title"]
    assert snapshot["ungrounded_fields"] == ["mcu_family"]
    assert version["asked_question_count"] == 3


def test_profile_detail_shows_gaps_in_chinese_only(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1")
    _seed_version(conn, "j1", 1, {"job_title": "A"}, derived=["experience_years"])

    version = client.get("/api/jobs/j1/profile").json()["versions"][0]

    assert version["unspecified_fields"] == ["experience_years"]
    assert version["unspecified_field_labels"]
    assert "experience_years" not in version["unspecified_field_labels"][0]


def test_profile_detail_does_not_render_jd_text(tmp_path):
    """Global Constraints 第 5 条：详情页只给 JD 状态徽标，不给正文。
    多一个渲染正文的地方就多一个会漏掉 AI 生成标识的地方。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", status="approved")
    _seed_version(
        conn, "j1", 1,
        {"job_title": "A", "_jd_text": "这是一整段 JD 正文", "_jd_needs_manual": False},
        status="approved",
    )

    body = client.get("/api/jobs/j1/profile").text

    assert "这是一整段 JD 正文" not in body
    assert client.get("/api/jobs/j1/profile").json()["versions"][0]["jd"]["generated"] is True


def test_profile_detail_lists_human_decisions_in_chinese(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", status="approved")
    _seed_version(conn, "j1", 1, {"job_title": "A"})
    _seed_review(conn, "j1", 1, DECISION_REVISION_REQUESTED,
                 feedback="人数改成 3 个", decided_at="2026-09-01 11:00:00")
    _seed_review(conn, "j1", 2, "approved", decided_at="2026-09-01 12:00:00")

    decisions = client.get("/api/jobs/j1/profile").json()["decisions"]

    assert [d["decision_label"] for d in decisions] == ["要求修改", "确认"]
    assert decisions[0]["feedback"] == "人数改成 3 个"
    assert decisions[0]["reviewer"] == "unknown:web-session"


def test_profile_detail_states_the_snapshot_boundary_honestly(tmp_path):
    """analysis_run.job_id 恒为 NULL（没有调用点传 audit_context），按岗位查不出来。
    ⛔ 不许静默留白：留白会让人以为"这就是全部留痕"。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1")
    _seed_version(conn, "j1", 1, {"job_title": "A"})

    note = client.get("/api/jobs/j1/profile").json()["snapshot_note"]

    assert "analysis_run" in note
    # ⛔ 指向**既有的** TD-1（它的「怎么还」第 ① 步就是接 audit_context），
    # 不新开一条 TD——同一个事实两个真源，两边迟早写得不一样而没有症状。
    assert "TD-1" in note


def test_profile_detail_for_a_job_without_versions(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "empty")

    data = client.get("/api/jobs/empty/profile").json()

    assert data["versions"] == []
    assert data["decisions"] == []
    assert data["latest_version"] is None
    assert data["stage_label"] == "刚发起，还没有画像"


def test_profile_detail_is_mounted_under_the_configured_root_path(tmp_path):
    client, conn = _make_app(tmp_path, root_path="/hr/recruit-agent")
    _seed_job(conn, "j1")
    _seed_version(conn, "j1", 1, {"job_title": "A"})

    assert client.get("/hr/recruit-agent/api/jobs/j1/profile").status_code == 200
    assert client.get("/api/jobs/j1/profile").status_code == 404


def test_profile_detail_does_not_shadow_the_existing_single_job_endpoint(tmp_path):
    """回归：新增 /api/jobs/{id}/profile 之后，既有的 GET /api/jobs/{id} 必须照旧。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1")
    _seed_version(conn, "j1", 1, {"job_title": "A"})
    _seed_outbox(conn, "j1", "question")

    existing = client.get("/api/jobs/j1").json()

    assert existing["job_id"] == "j1"
    assert existing["status"] == "drafting"
    assert existing["message"]["type"] == "question"


def test_profile_detail_does_not_write_anything(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1")
    _seed_version(conn, "j1", 1, {"job_title": "A"})

    tables = ("job", "job_profile", "human_review", "effect_log", "outbox")
    before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}

    client.get("/api/jobs/j1/profile")
    client.get("/api/jobs/j1/profile")

    after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    assert before == after
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `venv/bin/python -m pytest tests/test_job_views_api.py -q -k profile_detail`
Expected: FAIL — 大部分报 `assert 404 == 200`（端点未注册），`test_profile_detail_404_for_unknown_job` 会碰巧先通过（未注册的路径本来就 404），这是预期内的假通过，Step 4 之后它才有意义

- [ ] **Step 3: 写实现**

在 `app/web/server.py` 里 Task 2 那段的**后面**、`@router.get("/api/jobs/{job_id}")` 既有端点**之前**，插入：

```python
    # 8.2「生成快照」的诚实边界。
    #
    # analysis_run 表里有工程铁律 3 要求的全套字段（模型标识/版本/prompt 版本/
    # temperature/输入哈希/原始响应/token 用量），但**当前没有任何调用点给网关传
    # audit_context**——app/llm/gateway.py 的 audit_context 参数在 app/graph/ 与
    # app/agents/ 下无调用方，于是 app/audit/hook.py 里 context.get("job_id") 恒为
    # None，那些行的 job_id 全是 NULL，按岗位根本查不出来。
    #
    # ⛔ 不在这里瞎猜关联（比如按时间就近匹配 analysis_run 行）：猜出来的留痕比
    # 没有留痕更糟——审计那天答不出"这条是怎么对上的"，而 PIPL 第 24 条说明权
    # 要的正是这个答案。本页展示的快照来自 job_profile 逐轮落的列，其中
    # llm_response_model 是工程铁律 5 的落点（API 响应里实际返回的模型标识）。
    #
    # 补齐的做法是在网关调用点传 audit_context={"job_id": ...}，那要碰 app/graph/
    # 与 app/agents/，超出本交付单元边界。
    #
    # ⛔ 不为此新开一条技术债：这件事**已经登记在 docs/tech-debt.md 的 TD-1** 里
    # ——TD-1「怎么还」第 ① 步逐字写着"先有一个单元把 audit_context（至少含
    # thread_id / job_id / node）接到 intake 的 LLM 调用上"，「现状」段又逐字写着
    # "intake 路径尚未传 audit_context，那些行的 job_id / application_id 全为 NULL"。
    # 再开一条就是给同一个事实开第二个真源，两边迟早写得不一样而没有任何症状。
    _SNAPSHOT_NOTE = (
        "本页快照来自逐轮落库的画像行，模型标识取自 API 响应实际返回值。"
        "完整的模型调用留痕（analysis_run 表）当前未与岗位关联、按岗位查不到，"
        "见技术债 TD-1 的第 ① 步（audit_context 尚未接到 intake 路径）。"
    )
```

⚠️ **被否决的写法（⛔ 不要这样写，写了 reviewer 会退回）**：先 `SELECT ... FROM job WHERE id=?` 拿到 job 行，再另写一段"取最新一版画像的 profile_json"的逻辑来算标题与 JD 状态。那是给"最新一版画像"这个事实开第二个真源——它和 `latest_profile_rows()` 里那段 LEFT JOIN 迟早会在某个边界上不一致（最典型的是"有 job、没有任何 job_profile"那种行），而不一致时**两边都不报错**，只是列表页和详情页显示的标题不一样。正确写法是复用同一条推导路径：

```python
    @router.get("/api/jobs/{job_id}/profile")
    def get_job_profile(job_id: str) -> dict:
        """8.2 画像详情：版本历史 + 每版生成快照 + 人工决策留痕。只读。

        标题与 JD 状态复用 latest_profile_rows() 的同一条推导路径，⛔ 不另写
        一份"取最新版画像"的逻辑：两份推导迟早会在某个边界上不一致（比如
        "只有 job、没有 job_profile"那种行），而不一致时两边都不报错。
        """
        rows = [row for row in job_queries.latest_profile_rows(conn) if row["job_id"] == job_id]
        if not rows:
            raise HTTPException(status_code=404, detail="job not found")
        row = rows[0]

        versions = job_queries.profile_versions(conn, job_id)
        jd = job_queries.jd_state(row["profile"])

        return {
            "job_id": row["job_id"],
            "title": job_queries.display_title(row),
            "status": row["status"],
            "stage_label": job_queries.stage_label(
                job_status=row["status"],
                latest_version=row["latest_version"],
                latest_message_type=job_queries.latest_message_types(conn).get(job_id),
                jd=jd,
            ),
            "created_at": row["created_at"],
            "latest_version": row["latest_version"],
            "snapshot_note": _SNAPSHOT_NOTE,
            "versions": versions,
            "decisions": job_queries.decision_records(conn, job_id),
        }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest tests/test_job_views_api.py -q`
Expected: PASS，22 passed（Task 2 的 11 条 + 本 Task 的 11 条）

- [ ] **Step 5: 跑全量回归**

Run: `venv/bin/python -m pytest -q -p no:randomly`
Expected: **1051 passed, 1 skipped**

- [ ] **Step 6: 提交**

```bash
git add app/web/server.py tests/test_job_views_api.py
git commit -m "feat(web): GET /api/jobs/{id}/profile 版本历史与生成快照（tasks 8.2）"
```

---

### Task 4: `GET /api/queues/needs-manual` 转人工队列端点（8.4）

**Files:**
- Modify: `app/web/server.py`（追加一个端点）
- Test: `tests/test_job_views_api.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `_job_row_payload` / `_job_rows_with_context`
- Produces：`GET {root_path}/api/queues/needs-manual` → `{"jobs": [ <与列表同形状的行> ], "total": int}`

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_job_views_api.py` 末尾：

```python
# ── 8.4 转人工队列 ───────────────────────────────────────────────────────────


def test_queue_is_empty_when_nothing_needs_a_human(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1")
    _seed_version(conn, "j1", 1, {"job_title": "A"})

    data = client.get("/api/queues/needs-manual").json()

    assert data == {"jobs": [], "total": 0}


def test_queue_picks_up_jd_discrimination_flag(tmp_path):
    """今天唯一真实存在的写入方：app/graph/nodes.py 的
    effect_generate_and_persist_jd 在 JD 连续 2 次触发歧视性表述检测后落库。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", status="approved")
    _seed_version(
        conn, "j1", 1, {"job_title": "A", "_jd_text": "…", "_jd_needs_manual": True},
        status="approved",
    )

    data = client.get("/api/queues/needs-manual").json()

    assert data["total"] == 1
    assert data["jobs"][0]["job_id"] == "j1"
    assert [r["code"] for r in data["jobs"][0]["needs_manual_reasons"]] == ["jd_discrimination"]


def test_queue_picks_up_revision_limit(tmp_path):
    """spec「修改次数上限」要求"提示转人工"。修复前那句提示只活在一次 409 响应里，
    页面一关就没了——队列把它变成一条查得到的事实。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1")
    _seed_version(conn, "j1", 1, {"job_title": "A"})
    for version in range(1, MAX_REVISIONS + 1):
        _seed_review(
            conn, "j1", version, DECISION_REVISION_REQUESTED,
            decided_at=f"2026-09-01 11:0{version}:00",
        )

    data = client.get("/api/queues/needs-manual").json()

    assert data["total"] == 1
    assert [r["code"] for r in data["jobs"][0]["needs_manual_reasons"]] == ["revision_limit"]
    assert str(MAX_REVISIONS) in data["jobs"][0]["needs_manual_reasons"][0]["label"]


def test_queue_picks_up_job_status_column_when_someone_finally_writes_it(tmp_path):
    """WBS 2.5 落地当天这一条自动生效。⛔ 不许因为"现在无人写入"就省掉——
    只认一个恒为空的状态列，队列会永远是空的，而"空队列"和"没人需要处理"
    在界面上长得一模一样。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", status="needs_manual")
    _seed_version(conn, "j1", 1, {"job_title": "A"})

    data = client.get("/api/queues/needs-manual").json()

    assert data["total"] == 1
    assert [r["code"] for r in data["jobs"][0]["needs_manual_reasons"]] == ["job_status"]


def test_queue_excludes_abandoned_jobs(tmp_path):
    """放弃是终态、不再流转。把它摆进 HR 的待办里只会制造清不掉的积压。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", status="abandoned")
    _seed_version(
        conn, "j1", 1, {"job_title": "A", "_jd_text": "…", "_jd_needs_manual": True},
        status="abandoned",
    )

    assert client.get("/api/queues/needs-manual").json() == {"jobs": [], "total": 0}


def test_queue_orders_oldest_first(tmp_path):
    """队列按"等得最久的排前面"，与列表页的"最近有动静的排前面"刻意相反：
    列表回答"最近发生了什么"，队列回答"该先办哪一个"。"""
    client, conn = _make_app(tmp_path)
    for job_id, at in (("recent", "2026-09-03 15:00:00"), ("stale", "2026-09-01 08:00:00")):
        _seed_job(conn, job_id, status="approved")
        _seed_version(
            conn, job_id, 1, {"job_title": job_id, "_jd_text": "…", "_jd_needs_manual": True},
            status="approved", created_at=at,
        )

    ids = [job["job_id"] for job in client.get("/api/queues/needs-manual").json()["jobs"]]

    assert ids == ["stale", "recent"]


def test_queue_row_has_the_same_shape_as_a_list_row(tmp_path):
    """两个页面渲染同一个卡片组件。形状分叉了没有测试会自己发现。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", status="approved")
    _seed_version(
        conn, "j1", 1, {"job_title": "A", "_jd_text": "…", "_jd_needs_manual": True},
        status="approved",
    )

    list_row = client.get("/api/jobs").json()["jobs"][0]
    queue_row = client.get("/api/queues/needs-manual").json()["jobs"][0]

    assert set(list_row.keys()) == set(queue_row.keys())
    assert list_row == queue_row


def test_queue_is_mounted_under_the_configured_root_path(tmp_path):
    client, conn = _make_app(tmp_path, root_path="/hr/recruit-agent")
    _seed_job(conn, "j1")

    assert client.get("/hr/recruit-agent/api/queues/needs-manual").status_code == 200
    assert client.get("/api/queues/needs-manual").status_code == 404


def test_queue_exposes_no_write_verbs(tmp_path):
    """合规红线「AI 只做排序推荐，不做自动淘汰」在本单元的落点：队列只展示、
    不处置。⛔ 不做批量确认/批量放弃/批量重生成（M2 的事）。"""
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1")

    for method in (client.post, client.put, client.patch, client.delete):
        assert method("/api/queues/needs-manual").status_code == 405


def test_queue_does_not_write_anything(tmp_path):
    client, conn = _make_app(tmp_path)
    _seed_job(conn, "j1", status="approved")
    _seed_version(
        conn, "j1", 1, {"job_title": "A", "_jd_text": "…", "_jd_needs_manual": True},
        status="approved",
    )

    tables = ("job", "job_profile", "human_review", "effect_log", "outbox")
    before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}

    client.get("/api/queues/needs-manual")
    client.get("/api/queues/needs-manual")

    after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    assert before == after
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `venv/bin/python -m pytest tests/test_job_views_api.py -q -k queue`
Expected: FAIL — `assert 404 == 200`（`/api/queues/needs-manual` 未注册）

- [ ] **Step 3: 写实现**

在 `app/web/server.py` 里 Task 3 那个端点的**后面**、`@router.get("/api/jobs/{job_id}")` 既有端点**之前**，插入：

```python
    @router.get("/api/queues/needs-manual")
    def needs_manual_queue() -> dict:
        """8.4 转人工队列。只读、只展示。

        ⛔ 只展示不处置：本端点与本队列页面 ⛔ 不提供批量确认、批量放弃、批量
        重生成（合规红线「AI 只做排序推荐，不做自动淘汰」；批量处置是 M2 的事，
        且必须有人工确认节点与留痕）。

        ⛔ 放弃（abandoned）的岗位不进队列：放弃是终态、不再流转，把它摆进 HR
        的待办里只会制造清不掉的积压。过滤放在这里而不是
        derive_needs_manual_reasons 里——那个函数只回答"有哪些理由"，详情页
        恰恰应该看得到"这个岗位当初为什么被转人工"，哪怕它后来被放弃了。
        """
        rows, counts, message_types = _job_rows_with_context()
        items = [
            payload
            for payload in (
                _job_row_payload(row, counts, message_types)
                for row in rows
                if row["status"] != "abandoned"
            )
            if payload["needs_manual"]
        ]
        # 队列按"等得最久的排前面"，与列表页的倒序刻意相反：列表回答"最近发生了
        # 什么"，队列回答"该先办哪一个"。job_id 作为第二排序键，保证同一时刻的
        # 两条有稳定顺序（否则每次刷新顺序会跳，看的人会以为队列变了）。
        items.sort(key=lambda item: (item["updated_at"], item["job_id"]))
        return {"jobs": items, "total": len(items)}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest tests/test_job_views_api.py -q`
Expected: PASS，32 passed

- [ ] **Step 5: 跑全量回归**

Run: `venv/bin/python -m pytest -q -p no:randomly`
Expected: **1061 passed, 1 skipped**（出计划时已实测到这个数，见「提取验证记录」）

- [ ] **Step 6: 提交**

```bash
git add app/web/server.py tests/test_job_views_api.py
git commit -m "feat(web): GET /api/queues/needs-manual 转人工队列（tasks 8.4）"
```

---

### Task 5: 前端三视图（导航 + 列表 + 详情 + 队列）

**Files:**
- Modify: `app/web/static/index.html`（HTML 加导航与两个视图容器、CSS 加规则、JS 末尾追加一段）
- Test: `tests/test_static_frontend.py`（**只追加**，⛔ 不改既有 30 条用例的任何一行）

**Interfaces:**
- Consumes: Task 2/3/4 的三个端点与它们的响应形状
- Produces: 页面上的 `#nav-intake` / `#nav-list` / `#nav-queue` 三个按钮、`#view-intake` / `#view-list` / `#view-queue` 三个容器、`#job-list` / `#job-detail` / `#queue-list` 三个渲染目标

⚠️ **改动 `index.html` 时的三条硬约束**（违反其中任何一条都会让既有测试红，且原因不好查）：
1. ⛔ 不改 `collectSelections()` 的缩进与函数体：`tests/test_static_frontend.py` 用正则 `function collectSelections\(\)\s*\{(.*?)\n {4}\}` 抓它的函数体，闭合大括号必须停在 4 空格缩进
2. ⛔ 不改 `REASK_PREFIX` 与 `AI_OPTIONS_HINT` 两个常量的字面量：它们与后端逐字同源，有强断言锁着
3. ⛔ 新增的 `fetch()` 一律相对路径、不带开头 `/`：`test_frontend_html_has_no_hardcoded_absolute_api_or_static_paths` 断言 `'"/api/jobs'` 与 `` '`/api/jobs' `` 都不出现在 HTML 里

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_static_frontend.py` 末尾：

```python
# ── 交付单元 8：会话之外的三个只读视图（tasks 8.1 / 8.2 / 8.4）───────────────


def test_three_views_and_their_nav_buttons_exist():
    for element_id in (
        "nav-intake", "nav-list", "nav-queue",
        "view-intake", "view-list", "view-queue",
        "job-list", "job-detail", "queue-list",
    ):
        assert f'id="{element_id}"' in INDEX_HTML, f"缺少 #{element_id}"


def test_view_endpoints_are_fetched_with_relative_paths():
    """部署约束 1：挂到任意子路径下都要能工作，靠 <base href> 解析。
    带开头 "/" 的路径会打到门户根上去。"""
    for path in ("api/jobs", "api/queues/needs-manual"):
        assert f'"{path}"' in INDEX_HTML or f"`{path}`" in INDEX_HTML

    for absolute in ('"/api/jobs', "`/api/jobs", '"/api/queues', "`/api/queues"):
        assert absolute not in INDEX_HTML


def test_view_code_issues_no_write_requests():
    """本单元只读（Global Constraints 第 7 条）。前端只许 GET。

    既有的写请求（POST /reply、/confirm、/revise、/abandon、/jd…）都显式带
    method: "POST"，本测试数一遍 POST 出现次数，⛔ 新增视图不许再添一个。
    """
    post_count = INDEX_HTML.count('method: "POST"')
    assert post_count == 7, (
        f'index.html 里 method: "POST" 出现了 {post_count} 次，预期 7 次'
        "（reply/create 共用 1、confirm 1、revise 1、abandon 1、jd 编辑 1、"
        "jd 标记人工 1、jd 保存 1）。交付单元 8 的三个视图是只读的，"
        "⛔ 不许新增写请求；确实需要新增写入时，先回到 CLAUDE.md 的合规红线"
        "「AI 只做排序推荐，不做自动淘汰」重新论证。"
    )


def test_queue_page_has_no_batch_action_buttons():
    """合规红线在前端的落点：队列只展示、不处置。"""
    for forbidden in ("批量确认", "批量放弃", "批量处理", "一键确认", "全部确认"):
        assert forbidden not in INDEX_HTML


def test_detail_view_does_not_render_jd_body_text():
    """Global Constraints 第 5 条：详情页只给 JD 状态徽标，不给正文。
    渲染正文就要自己保证 AI 生成标识不被裁掉，而正文已经有专门的展示位。"""
    match = re.search(r"function renderVersionBlock\(.*?\n {4}\}", INDEX_HTML, re.DOTALL)
    assert match, "index.html 里找不到 renderVersionBlock()"

    body = match.group(0)
    assert "jd_text" not in body
    assert "带 AI 生成标识" in body


def test_view_rendering_never_uses_innerhtml():
    """岗位标题与画像字段值是 LLM 自由生成的文本，innerHTML 是一条注入路径。
    这条与既有的 renderProfileSummary / renderQuestionBlock 是同一条纪律。"""
    assert "innerHTML" not in _WITHOUT_LINE_COMMENTS


def test_view_switch_reloads_data_instead_of_caching():
    """列表与队列是别人（HR、另一个业务经理）也会改动的数据。缓存住只会让人
    对着一份过期的队列做决定。"""
    match = re.search(r"function showView\(.*?\n {4}\}", INDEX_HTML, re.DOTALL)
    assert match, "index.html 里找不到 showView()"
    assert "loader()" in match.group(0)
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `venv/bin/python -m pytest tests/test_static_frontend.py -q`
Expected: FAIL — 7 条新用例全红（`缺少 #nav-intake` 等）；既有 30 条仍然全绿

- [ ] **Step 3: 改 HTML 结构**

在 `app/web/static/index.html` 的 `<style>` 段末尾（`#revise-box { … }` 那一行之后）追加：

```css
  .nav { display: flex; gap: 8px; margin-bottom: 20px; border-bottom: 1px solid #dee2e6; }
  .nav-btn { margin: 0 0 -1px; padding: 8px 16px; border: 1px solid transparent; border-radius: 8px 8px 0 0; background: none; font-size: 15px; }
  .nav-btn.active { border-color: #dee2e6 #dee2e6 #fff; background: #fff; font-weight: 600; }
  .job-card { border: 1px solid #dee2e6; border-radius: 8px; padding: 12px 16px; margin-bottom: 12px; }
  .job-card-head { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 6px; }
  .job-title { font-weight: 600; font-size: 16px; }
  .badge { display: inline-block; border: 1px solid #adb5bd; border-radius: 12px; padding: 1px 10px; font-size: 12px; color: #495057; }
  /* 「待人工」用与 gap-warning 同一套红：它和缺口警示是同一类"需要人来处理"
     的信号，两处用不同的颜色只会让人重新学一遍配色含义。 */
  .badge-manual { background: #f8d7da; border-color: #dc3545; color: #58151c; }
  .job-meta { font-size: 13px; color: #6c757d; margin-bottom: 6px; }
  .reason { background: #fff3cd; border: 1px solid #ffe69c; color: #664d03; border-radius: 6px; padding: 6px 10px; margin: 6px 0; font-size: 13px; }
  .muted { color: #6c757d; font-size: 14px; }
  .version-block { border-left: 3px solid #0d6efd; padding: 4px 0 4px 12px; margin: 16px 0; }
  .profile-dl { margin: 8px 0; display: grid; grid-template-columns: max-content 1fr; gap: 6px 16px; }
  .profile-dl dt { font-weight: 600; color: #495057; }
  .profile-dl dd { margin: 0; white-space: pre-wrap; }
  .snapshot { font-size: 13px; color: #495057; background: #f8f9fa; border-radius: 6px; padding: 6px 10px; margin-top: 6px; }
  .decision-list { font-size: 14px; padding-left: 20px; }
```

把 `<body>` 里从 `<h1>一句话提用人需求</h1>` 到 `</div>`（`#jd-panel` 的收尾，即 `<script>` 之前）整段**原样**包进一个 `<div id="view-intake">`，并在 `.banner` 之后、`#view-intake` 之前插入导航，在 `#view-intake` 之后插入两个新视图容器。改完后 `<body>` 的骨架长这样（⛔ `view-intake` 内部的现有内容一行不改）：

```html
<body>
  <div class="banner">⚠️ 演示环境，不进入正式招聘流程</div>

  <!-- 三个视图之间用按钮切换，⛔ 不用 <a href> 或 hash 路由：那会和 <base href>
       的解析纠缠出一类只在挂了 root_path 时才现形的 bug（部署约束 1）。 -->
  <div class="nav">
    <button id="nav-intake" class="nav-btn active">提需求</button>
    <button id="nav-list" class="nav-btn">岗位列表</button>
    <button id="nav-queue" class="nav-btn">转人工队列</button>
  </div>

  <div id="view-intake">
    <h1>一句话提用人需求</h1>
    <!-- …… 既有内容一行不改：#chat / #input / #send-btn / #profile-summary /
         #gap-warning / #approval-actions / #revise-box / #jd-panel …… -->
  </div>

  <div id="view-list" style="display:none;">
    <h1>岗位列表</h1>
    <div id="job-list"></div>
    <div id="job-detail" style="display:none;"></div>
  </div>

  <div id="view-queue" style="display:none;">
    <h1>转人工队列</h1>
    <p class="muted">这里列出需要 HR 人工介入的岗位，等得最久的排在最前面。本页只看不动手：处置动作要回到对应岗位上、由人逐个做。</p>
    <div id="queue-list"></div>
  </div>

  <script>
    <!-- …… 既有脚本一行不改，只在末尾追加 Step 4 那一段 …… -->
  </script>
</body>
```

- [ ] **Step 4: 追加 JS**

在 `<script>` 段的最末尾（既有的 `document.getElementById("confirm-btn").addEventListener(...)` 那一行之后）追加：

```js
    // ── 会话之外的三个只读视图（tasks 8.1 / 8.2 / 8.4）────────────────────
    //
    // ⛔ 这一段里只有 GET，一个写请求都没有，而且不许有。列表、详情、队列都是
    // 把已经落库的事实读出来摆好：加写入就越过了本交付单元的边界，也会让
    // "这几个页面可以放心给业务经理点"这个前提不再成立。队列尤其⛔ 不做批量
    // 处置（合规红线：AI 只做排序推荐，不做自动淘汰；淘汰必须有人工确认节点
    // 并留痕），机器判据见 tests/test_static_frontend.py 的两条断言。
    //
    // ⛔ 全程 createElement + textContent，不拼 HTML 字符串：岗位标题与画像
    // 字段值都是 LLM 自由生成的文本。与 renderProfileSummary 同一条纪律。

    function el(tag, className, text) {
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined && text !== null) node.textContent = text;
      return node;
    }

    async function getJson(path) {
      // 相对路径，配合 <head> 里的 <base href> 解析（部署约束 1）。
      // ⛔ 不写开头的 "/"。
      try {
        const resp = await fetch(path);
        if (!resp.ok) return null;
        return await resp.json();
      } catch (err) {
        // 网络失败与 5xx 走同一条兜底：调用方一律渲染"读取失败"，
        // ⛔ 不留白——留白会让人以为"这就是全部内容"。
        return null;
      }
    }

    function renderJobCard(job) {
      const card = el("div", "job-card");

      const head = el("div", "job-card-head");
      head.appendChild(el("span", "job-title", job.title));
      head.appendChild(el("span", "badge", job.stage_label));
      if (job.needs_manual) {
        head.appendChild(el("span", "badge badge-manual", "待人工"));
      }
      card.appendChild(head);

      const meta = [
        "发起于 " + job.created_at,
        "最近更新 " + job.updated_at,
        job.latest_version === null
          ? "还没有画像版本"
          : "画像已到第 " + job.latest_version + " 版",
        "修改 " + job.revision_count + " 次",
      ];
      card.appendChild(el("div", "job-meta", meta.join(" ｜ ")));

      // 转人工的理由逐条展开，⛔ 不只显示一个"待人工"徽标：HR 打开队列时
      // 第一件要知道的事是"这条为什么在这里"，光一个徽标等于让他挨个点进去猜。
      (job.needs_manual_reasons || []).forEach((reason) => {
        card.appendChild(el("div", "reason", "需人工：" + reason.label));
      });

      const detailBtn = el("button", null, "查看画像详情");
      detailBtn.addEventListener("click", () => loadJobDetail(job.job_id));
      card.appendChild(detailBtn);
      return card;
    }

    function renderVersionBlock(version, latestVersion) {
      const block = el("div", "version-block");
      block.appendChild(
        el("h3", null, "第 " + version.version + " 版" +
          (version.version === latestVersion ? "（最新）" : ""))
      );

      const meta = ["落库于 " + version.created_at, "状态 " + version.status_label];
      if (!version.is_productive) meta.push("这一轮没有新产出");
      block.appendChild(el("div", "job-meta", meta.join(" ｜ ")));

      const summary = version.summary || [];
      if (summary.length > 0) {
        const list = el("dl", "profile-dl");
        summary.forEach((item) => {
          list.appendChild(el("dt", null, item.label));
          list.appendChild(el("dd", null, item.value));
        });
        block.appendChild(list);
      } else {
        block.appendChild(el("div", "muted", "这一版还没有可展示的画像字段。"));
      }

      // ⛔ 只渲染中文名。服务端已经把 unspecified_field_labels 算好下发，
      // 前端不碰英文 snake_case（index.html 既有约束）。
      const gapLabels = version.unspecified_field_labels || [];
      if (gapLabels.length > 0) {
        block.appendChild(el("div", "reason", "未指定：" + gapLabels.join("、")));
      }

      const snapshot = version.snapshot || {};
      const latency = snapshot.llm_latency_ms;
      const snapshotParts = [
        // 工程铁律 5：这是 API 响应实际返回的模型标识，不是配置里写的名字。
        "模型：" + (snapshot.llm_response_model || "未记录"),
        "本轮 LLM 耗时：" +
          (latency === null || latency === undefined ? "未记录" : Math.round(latency) + " ms"),
        "本轮问出 " + version.asked_question_count + " 个问题",
        "本轮写入字段 " + (snapshot.written_fields || []).length + " 个",
      ];
      if ((snapshot.ungrounded_fields || []).length > 0) {
        snapshotParts.push("未溯源字段：" + snapshot.ungrounded_fields.join("、"));
      }
      block.appendChild(el("div", "snapshot", snapshotParts.join(" ｜ ")));

      // ⛔ 这里只渲染 JD 的状态徽标，不渲染 JD 正文：渲染正文就要在这里自己
      // 保证 AI 生成标识不被裁掉，而正文已经有一个专门的、合规上已过审的
      // 展示位（#jd-output）。少一个展示位就少一处会漏标识的地方。
      const jd = version.jd || {};
      if (jd.generated) {
        const jdParts = ["这一版已生成 JD"];
        if (jd.needs_manual) jdParts.push("曾触发歧视性表述检测，已转人工");
        jdParts.push(jd.human_written ? "已标记为人工撰写" : "带 AI 生成标识");
        block.appendChild(el("div", "snapshot", jdParts.join(" ｜ ")));
      }
      return block;
    }

    async function loadJobDetail(targetJobId) {
      const box = document.getElementById("job-detail");
      box.style.display = "block";
      box.textContent = "正在读取画像详情…";

      const data = await getJson(`api/jobs/${targetJobId}/profile`);
      box.textContent = "";
      if (!data) {
        box.appendChild(el("div", "muted", "画像详情读取失败，请稍后重试。"));
        return;
      }

      box.appendChild(el("h2", null, data.title));
      box.appendChild(
        el("div", "job-meta", "状态：" + data.stage_label + " ｜ 发起于 " + data.created_at)
      );
      // 快照边界写在页面上，⛔ 不静默留白：留白会让人以为"这就是全部留痕"。
      box.appendChild(el("div", "ai-hint", data.snapshot_note));

      const versions = (data.versions || []).slice().reverse();
      if (versions.length === 0) {
        box.appendChild(el("div", "muted", "这个岗位还没有任何画像版本。"));
      }
      versions.forEach((version) => {
        box.appendChild(renderVersionBlock(version, data.latest_version));
      });

      const decisions = data.decisions || [];
      if (decisions.length > 0) {
        box.appendChild(el("h3", null, "人工决策留痕"));
        const list = el("ul", "decision-list");
        decisions.forEach((decision) => {
          const parts = [
            decision.decided_at,
            "第 " + decision.profile_version + " 版",
            decision.decision_label,
            "决策人 " + decision.reviewer,
          ];
          if (decision.feedback) parts.push(decision.feedback);
          list.appendChild(el("li", null, parts.join(" ｜ ")));
        });
        box.appendChild(list);
      }
    }

    async function loadJobList() {
      const box = document.getElementById("job-list");
      document.getElementById("job-detail").style.display = "none";
      box.textContent = "正在读取岗位列表…";

      const data = await getJson("api/jobs");
      box.textContent = "";
      if (!data) {
        box.appendChild(el("div", "muted", "岗位列表读取失败，请稍后重试。"));
        return;
      }

      const jobs = data.jobs || [];
      if (jobs.length === 0) {
        box.appendChild(el("div", "muted", "还没有任何岗位。回到「提需求」用一句话发起一个。"));
        return;
      }
      jobs.forEach((job) => box.appendChild(renderJobCard(job)));
    }

    async function loadQueue() {
      const box = document.getElementById("queue-list");
      box.textContent = "正在读取队列…";

      const data = await getJson("api/queues/needs-manual");
      box.textContent = "";
      if (!data) {
        box.appendChild(el("div", "muted", "队列读取失败，请稍后重试。"));
        return;
      }

      const jobs = data.jobs || [];
      if (jobs.length === 0) {
        // ⛔ 不留白：空白区域和"读取失败"长得一样。明说队列是空的。
        box.appendChild(el("div", "muted", "队列是空的，当前没有需要人工介入的岗位。"));
        return;
      }
      box.appendChild(el("div", "job-meta", "共 " + data.total + " 个岗位在等人工处理。"));
      jobs.forEach((job) => box.appendChild(renderJobCard(job)));
    }

    const VIEW_LOADERS = { intake: null, list: loadJobList, queue: loadQueue };

    function showView(name) {
      ["intake", "list", "queue"].forEach((key) => {
        document.getElementById("view-" + key).style.display =
          key === name ? "block" : "none";
        document.getElementById("nav-" + key).classList.toggle("active", key === name);
      });
      const loader = VIEW_LOADERS[name];
      // 每次切过去都重新拉一次，⛔ 不缓存：列表与队列是别人（HR、另一个业务
      // 经理）也会改动的数据，缓存住只会让人对着一份过期的队列做决定。
      if (loader) loader();
    }

    document.getElementById("nav-intake").addEventListener("click", () => showView("intake"));
    document.getElementById("nav-list").addEventListener("click", () => showView("list"));
    document.getElementById("nav-queue").addEventListener("click", () => showView("queue"));
```

- [ ] **Step 5: 跑前端测试确认通过**

Run: `venv/bin/python -m pytest tests/test_static_frontend.py -q`
Expected: PASS，37 passed（既有 30 + 新增 7）

⚠️ 若 `test_view_code_issues_no_write_requests` 因为既有 `method: "POST"` 的实际条数与 7 不符而失败：先 `grep -c 'method: "POST"' app/web/static/index.html` 数一遍**改动前**的真实条数，把断言里的期望值改成那个数字并同步改错误信息里的枚举，⛔ 不要为了让测试过而删掉这条断言——它是"只读"这条边界唯一的机器判据。

- [ ] **Step 6: 跑全量回归**

Run: `venv/bin/python -m pytest -q`
Expected: 全绿。重点确认 `tests/test_web_api.py::test_frontend_html_has_no_hardcoded_absolute_api_or_static_paths`、`test_index_base_href_matches_configured_root_path`、`tests/test_static_frontend.py` 既有 30 条全部照旧通过。

- [ ] **Step 7: 人工过一遍真实页面（⏸ 需要能起服务的环境，起不来就如实登记留步，⛔ 不假装做过）**

Run: `venv/bin/python -m uvicorn app.main:app --port 8099`，浏览器开 `http://127.0.0.1:8099/`
逐项核对：
1. 三个导航按钮能切换，切到「岗位列表」时列表自动加载
2. 列表里的标题是真实岗位名而不是「待确定」；状态列是中文
3. 点「查看画像详情」出版本历史，最新版在最上面，每版带模型标识与耗时
4. 「转人工队列」页面上**没有任何**写按钮
5. 顶部「⚠️ 演示环境，不进入正式招聘流程」在三个视图下都在

- [ ] **Step 8: 提交**

```bash
git add app/web/static/index.html tests/test_static_frontend.py
git commit -m "feat(web): 岗位列表/画像详情/转人工队列三个只读视图（tasks 8.1/8.2/8.4）"
```

---

## Self-Review（写完计划后按 writing-plans 的三步自查，2026-09-04）

**1. Spec 覆盖**

| Spec Requirement | 覆盖它的 Task | 说明 |
|---|---|---|
| `job-profile-intake` ·「结构化岗位画像产出」 | Task 1（`profile_versions` / `summarize_profile` 复用）、Task 3、Task 5 | 详情页把逐版画像（含 ECU 特化字段，由既有 `FIELD_LABELS` 覆盖）读出来；⛔ 本单元不改产出逻辑 |
| `job-profile-intake` ·「结构化岗位画像产出」Scenario：模型返回不符合 Schema → 转人工 | Task 1 `derive_needs_manual_reasons` 的第 ① 条来源、Task 4 | 写入方（WBS 2.5）不在本单元；本单元把"一旦有人写就立刻可见"的读路径铺好，并在测试里锁死这条分支 |
| `job-profile-intake` ·「采集过程审计留痕」 | Task 1 `profile_versions` 的 `snapshot`、Task 3 `_SNAPSHOT_NOTE` | **部分覆盖**：模型标识（铁律 5 的落点）可见；prompt 版本 / temperature / 输入哈希在 `analysis_run` 里但与岗位无关联，差额已由既有的 TD-1 第 ① 步登记，并在页面上显式说明（⛔ 不新开 TD 条目） |
| `job-profile-approval` ·「画像确认断点」Scenario：流程状态持久化，用户关闭页面后重新打开仍能继续 | Task 2 列表的「等你确认」状态 + Task 5 的列表页 | 修复前关掉页面就找不回岗位了（只有 `GET /api/jobs/{id}` 且要先知道 id）；列表页是"找回来"的那条路 |
| `job-profile-approval` ·「修改与重新生成」Scenario：修改次数上限 → 提示转人工 | Task 1 `derive_needs_manual_reasons` 第 ③ 条、Task 4 | 修复前这句提示只活在一次 409 响应里 |
| `job-profile-approval` ·「决策留痕」 | Task 1 `decision_records`、Task 3、Task 5 | 详情页把 `human_review` 读出来（谁、何时、哪一版、什么决定） |
| `job-description` ·「AI 生成内容标识」 | Task 1 `jd_state`、Task 5 `renderVersionBlock` | 详情页显示「带 AI 生成标识」/「已标记为人工撰写」徽标；⛔ 不渲染正文（Global Constraints 第 5 条） |
| `job-description` ·「歧视性表述拦截」Scenario：连续 2 次仍出现则转人工 | Task 1 第 ② 条来源、Task 4 | 今天唯一真实有写入方的转人工来源 |
| `job-description` ·「文案导出」 | — | WBS 8.3，已由交付单元 7 交付并回勾，本单元 ⛔ 不重做 |

**未覆盖且属**故意**留给别人的**：`job-description`「JD 生成」、`job-profile-approval`「副作用幂等」「回调可靠接收」——都在本单元的只读边界之外，本单元不新增任何副作用。

**2. 占位符扫描**：全文无 `TBD` / `TODO` / "适当处理错误" / "类似 Task N" / "为上面写测试"。每个代码步骤都给了完整代码块，每个测试步骤都给了完整测试代码与预期输出。Task 3 Step 3 里「被否决的写法」一段**只用散文描述、没有给可复制的代码**——反例给了代码块，读任务简报的实现者就有可能照抄第一个块。

**3. 类型一致性**：`derive_needs_manual_reasons` 在 Task 1 定义、Task 2 调用（`_job_row_payload`）、Task 4 间接使用，四个 keyword-only 参数名与类型逐字一致；`jd_state` 返回的三个布尔键 `generated` / `needs_manual` / `human_written` 在 Task 1 定义、Task 2 透传、Task 5 前端读取，三处一致；`stage_label` 的四个参数在 Task 1、Task 2 `_job_row_payload`、Task 3 `get_job_profile` 三处调用形式一致；`_job_row_payload(row, counts, message_types)` 在 Task 2 定义、Task 4 复用，位置参数顺序一致；`REASON_*` 三个常量的字符串值与 Task 4 测试里断言的 `"jd_discrimination"` / `"revision_limit"` / `"job_status"` 逐字一致。

## 提取验证记录（2026-09-04，出计划时真跑过）

**Task 1–4 的全部代码块原样提取后跑过真实的 pytest**，不是纸面推演。做法与 `spec-to-plan` SKILL.md 第 6 步的意图一致，但**不装独立 venv**：本单元的每个代码块都不是自足模块（依赖 `app/storage/db.py` 的 SCHEMA、`app/schemas/job_profile.py` 的 `summarize_profile` / `field_labels`、`app/graph/nodes.py` 的常量、`app/web/server.py` 的 `create_app`），提取到空目录里跑不起来，硬做只会得到一个"装了个假仓库来证明代码能跑"的假绿。改成把整个仓库 rsync 到临时目录（排除 `.git` / `venv` / `data`）、把计划里的代码块按 Task 顺序拼进那份副本、用本仓库 venv 的 Python 3.14.6 跑：

| 范围 | 命令 | 结果 |
|---|---|---|
| 基线（未改动的真实仓库） | `pytest -q -p no:randomly` | **1008 passed, 1 skipped** |
| Task 1（`job_queries.py` + 其测试） | `pytest tests/test_job_queries.py -q` | **21 passed** |
| Task 1–4（三个端点全部接线后） | `pytest tests/test_job_queries.py tests/test_job_views_api.py -q` | **53 passed** |
| Task 1–4 之后的全量回归 | `pytest -q -p no:randomly` | **1060 passed, 2 skipped** |

⚠️ 副本里多出的那条 skip 是 `tests/test_boundary_guard.py:454`（"浅克隆，基线 commit 不可达"）——副本没有 `.git`，是提取环境的产物，**不是本单元引入的**。在真实仓库里那条照常通过，所以 Task 4 收尾时的预期是 **1061 passed, 1 skipped**（1008 + 53）。

**这一步逮到的真实 bug（2 个，都在测试代码里，都属于"看起来对、跑起来红"）**：

1. `test_module_contains_no_write_statements` 原先扫**整份源码**找 `INSERT INTO` / `UPDATE ` / `DELETE FROM`。而 `job_queries.py` 的模块注释里逐字写着"⛔ 一条 INSERT / UPDATE / DELETE 都不许有"——**断言被自己的注释判违例**，永远红。而一条永远红的断言等于没有断言，下一个人只会把它删掉，"只读"这条边界就此失去唯一的机器判据。改成用 `ast` 只扫**非 docstring 的字符串字面量**（注释根本不进 AST，SQL 恰好都在那些字面量里）。
2. `test_module_does_not_import_graph_or_agents_layer` 原先按文本判 `"app.graph" not in source`，同样被"本模块不 import app.graph"这句说明本身判违例。改成按 `ast` 里真实的 `Import` / `ImportFrom` 节点判。

两个都是"用文本匹配去断言代码性质"这一类错误，**不做提取验证根本发现不了**——它们在 run-build 的 Step 4 才会爆，而那时实现者面对的是一条自己没写的、看起来毫无道理的红。

**Task 5（前端）未做提取验证**：它改的是 `index.html`，而验证方式就是 Task 5 自己那 7 条断言 + 既有 30 条。⛔ 不重复跑一遍。Task 5 Step 5 里那条关于 `method: "POST"` 计数的提示已经用真实值核过——改动前 `grep -c 'method: "POST"' app/web/static/index.html` **正好是 7**。

**临时副本已清理**，⛔ 没有任何文件被写回本仓库（本次只新增了这份计划）。

## 下一步

用 `run-build` 执行本计划。执行时逐 Task 走，每个 Task 的最后一步提交一次。全部 5 个 Task 完成且两阶段 review 通过后，回勾 `openspec/changes/m1-job-profile-intake/tasks.md` 的 8.1 / 8.2 / 8.4 三条（8.3 已于 2026-09-04 回勾，⛔ 不要重复处理）。
