# M2 U2：简历上传与解析管线（含置信度与校对队列）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 HR 能把一批简历文件（PDF/Word）批量上传进系统，系统在真实简历入库闸默认关闭的前提下把文件变成六个结构化字段（带原文回指与置信度），机器没把握的字段进人工校对队列，每一次读取都留痕到具体的人。

**Architecture:** 单进程同步管线（design.md Non-Goals：不做异步任务队列）。上传接口收到文件后，在同一次 HTTP 请求里依次完成：落盘 → 抽文本 → 分片 → 调 LLM 抽取六字段 → 用 span 可定位性合成置信度 → 一个事务内落库（`resume` 更新 + 首次解析时创建 `candidate`/`application` + 低置信度字段进 `field_review_queue`）。鉴权从 M1 的空壳换成本地账号 + 会话 cookie，签名保持不变，将来切企微 OAuth SSO 只换中间件内部实现。

**Tech Stack:** Python 3.14、FastAPI、SQLite（`app/storage/db.py` 的 `SCHEMA`/`_ADDED_COLUMNS` 双轨迁移）、pydantic v2、`app/llm/gateway.py`（既有 LLM 网关，双供应商+留痕+版本锁定已具备）、pytest、httpx。

**Spec:**
- `openspec/changes/m2-resume-parse-and-rank/specs/resume-upload-and-gate/spec.md`
- `openspec/changes/m2-resume-parse-and-rank/specs/resume-parsing/spec.md`
- `openspec/changes/m2-resume-parse-and-rank/design.md`（决策 D1/D2/D4/D5/D11/D12/D13/D14）
- `openspec/changes/m2-resume-parse-and-rank/tasks.md` 第 3 章（3.1–3.10）

## Global Constraints

以下逐字摘自 `CLAUDE.md`「工程铁律」「合规红线」「部署约束」，与本交付单元的适用判断一并列出。**每个 Task 的验收隐含包含本节全部条目。**

1. **工程铁律 1**：LangGraph 恢复时节点从头整个重跑。每个有副作用的动作必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引；幂等记录与业务写必须在同一个事务里提交，同一连接、同一个 `BEGIN`，该连接上不得存在第二个事务管理者。✅ **适用**：`effect_persist_parse` 是本单元唯一的副作用节点，必须用既有 `app/storage/idempotency.py::idempotent_effect` 装饰器（它已经保证同事务提交，见 Task 6）。⚠️ **本单元的 `thread_id` 用 `resume_id` 而不是 `application_id`**——见下方「架构决策」第 1 条，偏离 `tasks.md` 3.7 的字面写法，原因写在那条决策里。
2. **工程铁律 2**：L3 Agent 全部是无副作用纯函数，副作用只在 L4 编排层的 `effect_*` 节点执行，节点命名区分 `compute_*` / `effect_*`。✅ **适用**：`app/agents/resume_parser.py::compute_parse` 只调 LLM、做数据转换，不碰数据库；唯一写库的是 `effect_persist_parse`。
3. **工程铁律 3**：所有 AI 评分必须持久化模型标识+版本、prompt 版本、temperature、输入哈希、rubric 快照、原始响应。✅ **适用，且已由既有基础设施满足**：`LLMGateway.extract_structured_with_meta` 与 `RecorderAuditHook` 已经做到这些（见 `app/llm/gateway.py`、`app/audit/hook.py`），本单元只需正确传 `audit_context`（Task 5）。
4. **工程铁律 4**：每条 `criterion_score` 必须有 `evidence_ref`。⛔ **不适用，理由**：`criterion_score` 由 U4 精排写入，本单元不写这张表。本单元的类比约束是 spec「原文分片与字段回指」（字段回指用 `SpanRef.span_id/start/end`，不复用 `evidence_ref` 这个字段名/表），已作为 Task 5 的独立验收覆盖。
5. **工程铁律 5**：`temperature=0`；模型版本优先显式锁定，禁止 `latest` 类别名；无版本号快照的供应商必须从 API 响应取回实际 `model` 字段并持久化。✅ **适用，且已由既有基础设施满足**：`LLMGateway`/`Settings.validate_model_version()` 已经做到；`compute_parse` 通过 `extract_structured_with_meta` 拿到的 `LLMCallMeta.response_model` 就是响应侧模型标识，Task 6 落库时用它。
6. **工程铁律 6**：企微回调先落库再处理，只推一次、5 秒无响应即丢弃。⛔ **不适用，理由**：本单元没有企微回调路径。
7. **工程铁律 7**：`langgraph >= 1.0.10`。✅ **适用，环境约束**：`requirements.txt` 已锁 `langgraph==1.0.10`，本单元不降级。
8. **合规红线·AI 只做排序推荐，不做自动淘汰**：✅ **适用**：本单元的置信度合成与校对队列只决定"要不要交给人校对"，绝不淘汰任何投递；`field_review_queue` 命中不产生 `rejection_record`。
9. **合规红线·禁止人脸/表情分析**：⛔ **不适用，理由**：本单元不处理任何影像/声学信号。
10. **合规红线·AI 生成内容须带标识**：⛔ **不适用于本单元，理由**：AI 标识与"仅供参考"说明的展示要求落在页面层（U2.5 第 9 章 9.2/9.3），本单元只交付 API/管道，不产出面向候选人的生成物。
11. **合规红线·模型全部走境内，简历数据不出境**：✅ **适用**：`compute_parse` 使用 `Settings.llm_provider`（默认 `deepseek`，境内），不引入新的境外供应商。
12. **合规红线·绝不用历史录用结果做监督信号**：⛔ **不适用，理由**：本单元不训练、不使用历史录用结果。
13. **部署约束 1·路径前缀就绪**：FastAPI `root_path=/hr/recruit-agent`，接口与静态资源一律相对路径，禁止硬编码 `/static/…` `/api/…`。✅ **适用**：新增路由全部走 `router`（挂载时统一加 `root_path` 前缀，与既有 `/api/jobs*` 同一机制），新增登录页 HTML 内的请求路径沿用 `index.html` 的 `<base href>` 相对路径写法。
14. **部署约束 3·鉴权中间件留空壳接入点，将来只换实现不换调用方**：✅ **适用，且是本单元的核心交付**：`AuthContext`/`reviewer_of()` 签名保持不变，只替换 `AuthMiddleware.dispatch` 内部实现（Task 2）。
15. **部署约束 4·目标服务器是 Windows，没有 Docker，Python venv 部署，不引入容器**：✅ **适用**：本单元新增依赖（`pypdf`、`python-docx`、`python-multipart`）必须是可在 Windows cp314 上 `pip install` 的纯 wheel（`pypdf`/`python-docx` 已在 U0 `[Mac]0917AX`/`0917BB` 于 `.51` 同款 Windows 环境冒烟通过，见 `docs/m2-model-comparison.md`「环境」节；`python-multipart` 是纯 Python 包，无编译依赖）。
16. **部署约束 5·M2 起处理真实简历前必须具备可识别到人的登录 + 简历访问留痕**：✅ **适用，是本单元的验收核心**：Task 2（登录）+ Task 9（访问留痕）+ Task 1（入库闸的两个结构性前置）共同落地这条。

## 架构决策（先读，避免和 `tasks.md`/`design.md` 的字面表述对不上）

**1. `effect_persist_parse` 的幂等 `thread_id` 用 `resume_id`，不用 `application_id`。**
`tasks.md` 3.7 写的幂等键是 `{application_id}:effect_persist_parse:{parser_version}`，但 `application` 表的行在**候选人身份确定之后**才能创建（`candidate` 按姓名去重，姓名恰恰是这次解析要抽取的字段之一——上传时还不存在 `application_id`，这是一个真实的先有鸡先有蛋的问题）。`app/storage/db.py` 里 `resume` 表的建表注释也写着"resume 与 candidate 的关联由 application 一次性接起来"，同样暗示 `application` 是解析完成后才出现的。本计划的解法：`effect_persist_parse` 以 `resume_id`（上传时就已确定）为 `thread_id`、以 `parser_version` 为 `business_key`；该节点内部**首次解析时创建** `candidate`/`application`/`application_stage_history`，重解析时只更新解析结果、不重复创建。这与 M1 `effect_confirm_profile` 用 `thread_id=job_id`（不是某个更晚才出现的 id）是同一个先例（`app/graph/nodes.py:331`）。

**2. 解析结果版本化用新表 `resume_parse_version`，不是"插入新的 `resume` 行"。**
`db.py` 里 `resume` 表的注释写"重解析会插入新的 resume 行"，但同一张表上 `idx_resume_job_content_hash` 对 `(job_id, content_sha256)` 加了唯一索引——同一份文件重解析不会产生新的 `content_sha256`，插入新行会直接撞唯一索引失败。这条注释和约束互相矛盾，本计划按约束（唯一索引）为准，引入 `resume_parse_version` 表存每一次解析尝试（Task 3），`resume.parsed_json/parse_confidence/parser_version` 三列作为"最新版缓存"随每次解析更新，工作台默认读 `resume` 表即得到最新版，历史版本查 `resume_parse_version`。Task 3 会同步把 `db.py` 里那条误导性注释改掉。

**3. `compute_parse` 不经过 LangGraph 的 `StateGraph`/checkpointer。**
`design.md` D13 把 `compute_parse → effect_persist_parse` 描述成某个 thread 的图节点序列，但那个序列里的 `interrupt()`（需要 checkpointer 挂起/恢复）要到 U5（复核挂起）才出现。本单元没有任何挂起点，把 `effect_persist_parse` 强行套进一个真实编译的 `StateGraph` 只会多一层无意义的包装。做法：`compute_parse`/`effect_persist_parse` 都是普通 Python 函数（后者带 `@idempotent_effect` 装饰器），由路由处理函数直接调用——与 M1 `effect_confirm_profile`/`effect_abandon_profile` 被 `/api/jobs/{id}/confirm` 等路由直接调用（不经 `graph.invoke()`）是同一种既有形态。U5 引入 `interrupt()` 时，如果需要把这些节点接入真正的图，是那个单元的工作，不在本计划范围内。

**4. 候选人手机号本单元不采集，`candidate.phone_hash` 恒为 `NULL`。**
六字段抽取 schema（`姓名/工作年限/技能列表/公司经历/教育/期望城市`）不含手机号，`design.md` D11 的"按姓名+手机号哈希去重"在本单元退化为"只按姓名去重"。SQLite 的 `UNIQUE INDEX` 对 `NULL` 值不去重（两行 `phone_hash IS NULL` 不会互相冲突），这意味着**同名不同人**在当前范围内会被去重逻辑误判为同一候选人（因为去重查询是"按姓名精确匹配即复用"，见 Task 6）。这是一个已知限制，登记为 Task 6 内的技术债 TD-52 的姊妹条目（不重复开号，直接写在 Task 6 的实现注释与测试里），触发条件是 M3 采集手机号后按 `(name, phone_hash)` 二次校验重新收紧。

## File Structure

| 文件 | 责任 |
|---|---|
| `app/config.py`（改） | 新增 `live_resume_intake_enabled`、`resume_storage_dir` 两个配置项 |
| `app/storage/db.py`（改） | 新增 `hr_session`、`resume_parse_version` 两张表；`resume` 表加 `raw_text` 列；`job` 表加 `parse_confidence_threshold` 列（`_ADDED_COLUMNS`）；修正误导性注释 |
| `app/storage/live_resume_gate.py`（新建） | `is_live_resume_intake_enabled()`：真实简历入库闸求值 |
| `app/storage/auth_session.py`（新建） | 会话的创建/校验/失效，供登录接口与中间件复用 |
| `app/middleware/auth.py`（改） | `AuthMiddleware.dispatch` 从空壳换成读会话 cookie 校验；`AuthContext`/`reviewer_of()` 签名不变 |
| `app/parsing/resume_ingest.py`（新建） | 纯函数：文件路径 → `(ExtractedText, list[TextSpan])`，复用既有 `app/parsing/extract_text.py`/`spans.py` |
| `app/agents/resume_parser.py`（新建） | `compute_parse`：调 LLM 抽取六字段 + span 反查 + 置信度合成，纯函数（不写库） |
| `app/graph/resume_nodes.py`（新建） | `effect_persist_parse`（含首次解析建 candidate/application）、`record_resume_access`、`queue_reapplication_screening`（U3 接入点空壳） |
| `app/web/server.py`（改） | 新增路由：登录/登出、上传、四个读取接口（原文/分片/解析结果/下载）、重解析、字段校对 |
| `app/web/static/login.html`（新建） | 登录页，无框架，相对路径 |
| `requirements.txt`（改） | 加 `python-multipart`、`pypdf==6.19.0`、`python-docx==1.2.0` |
| `docs/tech-debt.md`（改） | 登记 TD-52（PaddleOCR 在 cp314 装不上，扫描件走"不可读"退路） |
| `scripts/create_hr_account.py`（不改，仅复用） | U1 已交付，本单元登录接口直接消费它建的账号 |

---

### Task 1: 真实简历入库闸求值

**Files:**
- Modify: `app/config.py`（新增字段）
- Create: `app/storage/live_resume_gate.py`
- Test: `tests/test_live_resume_gate.py`

**Interfaces:**
- Consumes: `app.middleware.auth.AuthContext`（已存在，`user_id: str | None`, `authenticated: bool`）
- Produces: `is_live_resume_intake_enabled(*, auth: AuthContext, conn: sqlite3.Connection) -> bool`，供 Task 7（上传接口）调用

- [ ] **Step 1: 在 `app/config.py` 加两个字段**

在 `Settings` 类内、`candidate_outbound_switch_file` 字段之后加：

```python
    # 真实简历入库闸（design D2）。默认关闭；每次上传时求值，⛔ 不缓存——
    # 唯一合法入口是 app/storage/live_resume_gate.py 的
    # is_live_resume_intake_enabled()，业务代码不得直接读这个字段。
    live_resume_intake_enabled: bool = False

    # 上传文件落盘目录（U2 tasks 3.3/3.4）。相对路径按进程工作目录解析，
    # 与 db_path 同一约定。
    resume_storage_dir: str = "data/resumes"
```

- [ ] **Step 2: 写失败测试**

```python
# tests/test_live_resume_gate.py
from __future__ import annotations

import sqlite3

import pytest

from app.middleware.auth import AuthContext
from app.storage.db import init_schema
from app.storage.live_resume_gate import is_live_resume_intake_enabled


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    return c


AUTHED = AuthContext(user_id="alice", authenticated=True)
UNKNOWN = AuthContext(user_id=None, authenticated=False)


def test_default_off_even_when_authed(conn, monkeypatch):
    monkeypatch.delenv("LIVE_RESUME_INTAKE_ENABLED", raising=False)
    assert is_live_resume_intake_enabled(auth=AUTHED, conn=conn) is False


def test_env_on_but_identity_unknown_stays_off(conn, monkeypatch):
    """闸开启但登录身份不可识别 ⇒ 关（spec Scenario）。"""
    monkeypatch.setenv("LIVE_RESUME_INTAKE_ENABLED", "true")
    assert is_live_resume_intake_enabled(auth=UNKNOWN, conn=conn) is False


def test_env_on_and_identity_known_and_access_log_ok(conn, monkeypatch):
    monkeypatch.setenv("LIVE_RESUME_INTAKE_ENABLED", "true")
    assert is_live_resume_intake_enabled(auth=AUTHED, conn=conn) is True


def test_env_off_blocks_even_with_identity(conn, monkeypatch):
    monkeypatch.setenv("LIVE_RESUME_INTAKE_ENABLED", "false")
    assert is_live_resume_intake_enabled(auth=AUTHED, conn=conn) is False


def test_missing_access_log_table_blocks(monkeypatch):
    """访问留痕表不存在 ⇒ 探针失败 ⇒ 闸求值为关（两个结构性前置之二）。"""
    monkeypatch.setenv("LIVE_RESUME_INTAKE_ENABLED", "true")
    bare_conn = sqlite3.connect(":memory:")  # 没跑 init_schema，表都不存在
    assert is_live_resume_intake_enabled(auth=AUTHED, conn=bare_conn) is False


def test_runtime_toggle_takes_effect_without_restart(conn, monkeypatch):
    """运行期间开关变化：改环境变量立刻生效，不缓存（spec Scenario）。"""
    monkeypatch.delenv("LIVE_RESUME_INTAKE_ENABLED", raising=False)
    assert is_live_resume_intake_enabled(auth=AUTHED, conn=conn) is False
    monkeypatch.setenv("LIVE_RESUME_INTAKE_ENABLED", "true")
    assert is_live_resume_intake_enabled(auth=AUTHED, conn=conn) is True


def test_never_raises_on_broken_state(conn):
    """auth=None 这种调用方传错类型的情况，⛔ 不许抛异常——未知即拦截。"""
    assert is_live_resume_intake_enabled(auth=None, conn=conn) is False  # type: ignore[arg-type]
```

- [ ] **Step 3: 运行测试确认失败**

Run: `pytest tests/test_live_resume_gate.py -v`
Expected: `ModuleNotFoundError: No module named 'app.storage.live_resume_gate'`

- [ ] **Step 4: 实现 `app/storage/live_resume_gate.py`**

```python
"""真实简历入库闸（resume-upload-and-gate spec「真实简历入库闸」，design D2）。

与 app/config.py::is_candidate_outbound_enabled() 同一口径：默认关、每次求值、
⛔ 不缓存。唯一区别是本闸多两个结构性前置（登录身份可识别 + 访问留痕已启用），
这两条把部署约束 5 变成代码而不是流程——"登录没换成真实身份就开闸"在结构上
不可能发生。
"""
from __future__ import annotations

import logging
import os
import sqlite3

from app.config import get_settings
from app.middleware.auth import AuthContext

logger = logging.getLogger(__name__)

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_ENV_VAR = "LIVE_RESUME_INTAKE_ENABLED"
_PROBE_MARKER = "00000000-0000-0000-0000-live-gate"


def is_live_resume_intake_enabled(*, auth: AuthContext, conn: sqlite3.Connection) -> bool:
    """真实简历入库闸求值。⛔ 绝不抛出任何异常——任何一步出错，结果都是 False。"""
    try:
        return _evaluate(auth=auth, conn=conn)
    except Exception:
        logger.exception("真实简历入库闸求值过程出错，按关闭处理")
        return False


def _evaluate(*, auth: AuthContext, conn: sqlite3.Connection) -> bool:
    base = _base_switch()
    if not base:
        return False
    if not _identity_recognizable(auth):
        return False
    return _access_log_probe(conn)


def _base_switch() -> bool:
    """优先级：环境变量 > Settings 基线值。⛔ 不读 lru_cache 的 get_settings()
    结果去判断环境变量——环境变量必须每次读 os.environ，理由与
    is_candidate_outbound_enabled() 完全一致。"""
    raw_env = os.environ.get(_ENV_VAR)
    if raw_env is not None:
        return raw_env.strip().lower() in _TRUTHY
    try:
        settings = get_settings()
    except Exception:
        return False
    return settings.live_resume_intake_enabled


def _identity_recognizable(auth: AuthContext | None) -> bool:
    if auth is None:
        return False
    if not auth.authenticated:
        return False
    user_id = auth.user_id
    if not user_id:
        return False
    return not user_id.startswith("unknown:")


def _access_log_probe(conn: sqlite3.Connection) -> bool:
    """在一个 SAVEPOINT 里试写一行 resume_access_log 并回滚，探测表存在且可写。

    ⛔ 不用 sqlite_master 查表名了事：那只能证明表存在，证不了这条连接现在
    真的能写（磁盘满、只读文件系统这类失败查表名看不出来）。
    """
    try:
        conn.execute("SAVEPOINT live_gate_probe")
        conn.execute(
            "INSERT INTO resume_access_log (id, accessor, resume_id, access_type) "
            "VALUES (?, ?, ?, ?)",
            (_PROBE_MARKER, "probe:live-gate", "probe", "raw_text"),
        )
        conn.execute("ROLLBACK TO live_gate_probe")
        conn.execute("RELEASE live_gate_probe")
        return True
    except Exception:
        try:
            conn.execute("ROLLBACK TO live_gate_probe")
        except Exception:
            pass
        return False
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_live_resume_gate.py -v`
Expected: 7 个用例全部 PASS

- [ ] **Step 6: Commit**

```bash
git add app/config.py app/storage/live_resume_gate.py tests/test_live_resume_gate.py
git commit -m "feat(m2-u2): 真实简历入库闸求值（tasks 3.1）"
```

---

### Task 2: 本地账号登录（会话 cookie + AuthMiddleware 换血）

**Files:**
- Create: `app/storage/auth_session.py`
- Modify: `app/middleware/auth.py`
- Modify: `app/storage/db.py`（加 `hr_session` 表）
- Modify: `app/main.py`（`add_middleware` 传 `conn`/`root_path`）
- Modify: `app/web/server.py`（加登录/登出路由）
- Create: `app/web/static/login.html`
- Test: `tests/test_auth_session.py`, `tests/test_auth_middleware.py`

**Interfaces:**
- Consumes: `app.storage.hr_account.verify_password`（已存在）、`app.storage.db.sqlite_utc_now`（已存在）
- Produces: `create_session(conn, hr_account_id) -> str`（返回 token）、`resolve_session(conn, token) -> str | None`（返回 username 或 None）、`delete_session(conn, token) -> None`；`AuthMiddleware(app, *, conn, root_path="")`；`PROTECTED_PATH_PREFIXES` 常量供 Task 7/9/10/8 的路由复用判断

- [ ] **Step 1: 在 `app/storage/db.py` 的 `SCHEMA` 里加 `hr_session` 表**

紧跟在 `hr_account` 表定义之后加（`hr_account` 表定义在第 590 行附近）：

```sql
-- 会话（design D12：鉴权从空壳换成本地账号）。id 本身就是不透明的高熵令牌
-- （secrets.token_urlsafe(32)，256 bit），直接当 Cookie 值使用——校验靠"这条
-- 连接查得到这一行"而不是签名验证，与 Django 的 session 表是同一手法。
-- ⛔ 不加 last_seen_at 之类的滑动续期列：会话固定 TTL，简单够用（app/storage/
-- auth_session.py 的 SESSION_TTL_SECONDS）。
CREATE TABLE IF NOT EXISTS hr_session (
    id TEXT PRIMARY KEY NOT NULL,
    hr_account_id TEXT NOT NULL REFERENCES hr_account(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_hr_session_account ON hr_session (hr_account_id);
```

- [ ] **Step 2: 写 `auth_session.py` 的失败测试**

```python
# tests/test_auth_session.py
from __future__ import annotations

import sqlite3

import pytest

from app.storage.auth_session import create_session, delete_session, resolve_session
from app.storage.db import init_schema
from app.storage.hr_account import upsert_account


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    return c


@pytest.fixture
def account_id(conn):
    return upsert_account(conn, username="alice", password="s3cret!")


def test_create_then_resolve_returns_username(conn, account_id):
    token = create_session(conn, hr_account_id=account_id)
    assert resolve_session(conn, token) == "alice"


def test_resolve_unknown_token_returns_none(conn):
    assert resolve_session(conn, "not-a-real-token") is None


def test_resolve_expired_session_returns_none(conn, account_id, monkeypatch):
    import app.storage.auth_session as mod

    monkeypatch.setattr(mod, "SESSION_TTL_SECONDS", -1)  # 立刻过期
    token = create_session(conn, hr_account_id=account_id)
    assert resolve_session(conn, token) is None


def test_delete_session_invalidates_it(conn, account_id):
    token = create_session(conn, hr_account_id=account_id)
    delete_session(conn, token)
    assert resolve_session(conn, token) is None


def test_each_call_returns_a_distinct_high_entropy_token(conn, account_id):
    a = create_session(conn, hr_account_id=account_id)
    b = create_session(conn, hr_account_id=account_id)
    assert a != b
    assert len(a) >= 32
```

- [ ] **Step 3: 运行测试确认失败**

Run: `pytest tests/test_auth_session.py -v`
Expected: `ModuleNotFoundError: No module named 'app.storage.auth_session'`

- [ ] **Step 4: 实现 `app/storage/auth_session.py`**

```python
"""本地账号会话（design D12）。token 本身即会话主键，⛔ 不做滑动续期——
固定 TTL，到期后 resolve_session 返回 None，前端收到 401 后引导重新登录。
"""
from __future__ import annotations

import secrets
import sqlite3

from app.storage.db import sqlite_utc_now

SESSION_TTL_SECONDS = 12 * 3600  # 12 小时，一个 HR 的正常工作时段


def create_session(conn: sqlite3.Connection, *, hr_account_id: str) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO hr_session (id, hr_account_id, expires_at) "
        "VALUES (?, ?, datetime('now', ?))",
        (token, hr_account_id, f"+{SESSION_TTL_SECONDS} seconds"),
    )
    conn.commit()
    return token


def resolve_session(conn: sqlite3.Connection, token: str) -> str | None:
    """返回该会话对应的用户名；不存在或已过期返回 None。

    过期判断用字符串比较——sqlite_utc_now() 与 SQLite datetime('now') 产出
    的格式（"YYYY-MM-DD HH:MM:SS"）逐位可比较，这与仓库里其它时刻比较的
    既有约定一致。
    """
    row = conn.execute(
        "SELECT hr_account.username, hr_session.expires_at "
        "FROM hr_session JOIN hr_account ON hr_account.id = hr_session.hr_account_id "
        "WHERE hr_session.id = ?",
        (token,),
    ).fetchone()
    if row is None:
        return None
    username, expires_at = row
    if expires_at <= sqlite_utc_now():
        return None
    return username


def delete_session(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM hr_session WHERE id = ?", (token,))
    conn.commit()
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_auth_session.py -v`
Expected: 5 个用例全部 PASS

- [ ] **Step 6: 重写 `app/middleware/auth.py`**

`AuthContext`、`UNKNOWN_REVIEWER`、`reviewer_of()` 三个符号**逐字保留**（部署约束 3）。只替换 `AuthMiddleware`：

```python
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.storage.auth_session import resolve_session

SESSION_COOKIE_NAME = "hr_session"

# 未登录即 401 的路径前缀（resume-upload-and-gate spec「可识别到人的登录」）。
# 按 root_path 拼接后做前缀匹配。⛔ /api/jobs* 与静态资源不在这个列表——M1
# 的岗位画像流程本单元不改变可见性。
PROTECTED_PATH_PREFIXES: tuple[str, ...] = (
    "/api/candidates",
    "/api/resumes",
    "/api/applications",
)


@dataclass
class AuthContext:
    """
    当前请求的鉴权上下文。
    企微 OAuth SSO 接入时，只替换 AuthMiddleware.dispatch 内部的解析逻辑，
    调用方（路由处理函数）读取 request.state.auth 的方式不变。
    """

    user_id: str | None
    authenticated: bool


UNKNOWN_REVIEWER = "unknown:web-session"


def reviewer_of(request: Request) -> str:
    """当前请求的决策人标识。SSO 落地后本函数不用改。"""
    auth = getattr(request.state, "auth", None)
    return getattr(auth, "user_id", None) or UNKNOWN_REVIEWER


class AuthMiddleware(BaseHTTPMiddleware):
    """
    鉴权中间件（design D12）：读会话 cookie → 查 hr_session/hr_account。
    `/candidates* /resumes* /applications*` 未登录一律 401，不返回任何数据。

    ⚠️ conn 是全应用共享的单连接（app/storage/db.py::get_connection），这里
    只做只读 SELECT，不与其它写路径的事务冲突。root_path 用于把
    PROTECTED_PATH_PREFIXES 的相对前缀换算成实际请求路径的前缀。
    """

    def __init__(self, app, *, conn: sqlite3.Connection, root_path: str = "") -> None:
        super().__init__(app)
        self._conn = conn
        self._root_path = root_path

    def _is_protected(self, path: str) -> bool:
        return any(
            path.startswith(f"{self._root_path}{prefix}")
            for prefix in PROTECTED_PATH_PREFIXES
        )

    async def dispatch(self, request: Request, call_next) -> Response:
        token = request.cookies.get(SESSION_COOKIE_NAME)
        username = resolve_session(self._conn, token) if token else None
        if username is not None:
            request.state.auth = AuthContext(user_id=username, authenticated=True)
        else:
            request.state.auth = AuthContext(user_id=None, authenticated=False)

        if self._is_protected(request.url.path) and username is None:
            return JSONResponse(status_code=401, content={"detail": "未登录"})

        return await call_next(request)
```

- [ ] **Step 7: 写 `test_auth_middleware.py`**

```python
# tests/test_auth_middleware.py
from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.auth import AuthMiddleware, reviewer_of
from app.storage.auth_session import create_session
from app.storage.db import init_schema
from app.storage.hr_account import upsert_account


def _make_app(conn: sqlite3.Connection) -> FastAPI:
    app = FastAPI()
    app.add_middleware(AuthMiddleware, conn=conn, root_path="")

    @app.get("/api/resumes/1")
    def get_resume():
        return {"ok": True}

    @app.get("/api/jobs")
    def list_jobs():
        return {"ok": True}

    @app.get("/whoami")
    def whoami(request):
        return {"reviewer": reviewer_of(request)}

    return app


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    return c


def test_protected_path_without_cookie_is_401(conn):
    client = TestClient(_make_app(conn))
    resp = client.get("/api/resumes/1")
    assert resp.status_code == 401


def test_unprotected_path_without_cookie_passes(conn):
    client = TestClient(_make_app(conn))
    resp = client.get("/api/jobs")
    assert resp.status_code == 200


def test_protected_path_with_valid_cookie_passes(conn):
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client = TestClient(_make_app(conn))
    client.cookies.set("hr_session", token)
    resp = client.get("/api/resumes/1")
    assert resp.status_code == 200


def test_reviewer_of_returns_real_username_not_unknown(conn):
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client = TestClient(_make_app(conn))
    client.cookies.set("hr_session", token)
    resp = client.get("/whoami")
    assert resp.json()["reviewer"] == "alice"
```

- [ ] **Step 8: 运行测试确认通过**

Run: `pytest tests/test_auth_middleware.py tests/test_auth_session.py -v`
Expected: 全部 PASS

- [ ] **Step 9: 在 `app/web/server.py` 加登录/登出路由与 `create_app` 的中间件装配**

在 `create_app()` 里把：

```python
    app.add_middleware(AuthMiddleware)
```

改成：

```python
    app.add_middleware(AuthMiddleware, conn=conn, root_path=root_path)
```

在文件顶部 import 区加：

```python
from app.storage.auth_session import create_session, delete_session
from app.storage.hr_account import verify_password
```

在 `router = APIRouter()` 之后、其它路由之前加：

```python
    class LoginRequest(BaseModel):
        username: str
        password: str

    @router.post("/api/auth/login")
    def login(req: LoginRequest, response: Response):
        row = conn.execute(
            "SELECT id, password_hash, password_salt FROM hr_account WHERE username = ?",
            (req.username.strip(),),
        ).fetchone()
        if row is None or not verify_password(req.password, row[1], row[2]):
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        token = create_session(conn, hr_account_id=row[0])
        response.set_cookie(
            "hr_session",
            token,
            httponly=True,
            samesite="lax",
            path=root_path or "/",
        )
        return {"ok": True}

    @router.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        token = request.cookies.get("hr_session")
        if token:
            delete_session(conn, token)
        response.delete_cookie("hr_session", path=root_path or "/")
        return {"ok": True}

    @router.get("/login")
    def login_page():
        html = (STATIC_DIR / "login.html").read_text(encoding="utf-8")
        base_href = f"{root_path}/" if root_path else "/"
        return HTMLResponse(html.replace("<!--BASE_HREF-->", f'<base href="{base_href}">'))
```

`Response` 需要从 `fastapi` import（补进已有的 `from fastapi import APIRouter, FastAPI, HTTPException, Request` 一行，改成 `from fastapi import APIRouter, FastAPI, HTTPException, Request, Response`）。

- [ ] **Step 10: 新建 `app/web/static/login.html`**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <!--BASE_HREF-->
  <title>登录 · 卓品智能招聘助手</title>
</head>
<body>
  <h1>HR 登录</h1>
  <form id="login-form">
    <label>用户名 <input id="username" required></label>
    <label>口令 <input id="password" type="password" required></label>
    <button type="submit">登录</button>
  </form>
  <p id="error" style="color:red"></p>
  <script>
    document.getElementById("login-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const username = document.getElementById("username").value;
      const password = document.getElementById("password").value;
      const resp = await fetch("api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (resp.ok) {
        window.location.href = ".";
      } else {
        document.getElementById("error").textContent = "用户名或密码错误";
      }
    });
  </script>
</body>
</html>
```

- [ ] **Step 11: 在 `app/main.py` 无需改动**——`create_app` 内部已经把 `conn`/`root_path` 传给 `AuthMiddleware`，`app/main.py` 调 `create_app(...)` 的签名不变，直接受益。

- [ ] **Step 12: 加端到端测试**

```python
# tests/test_auth_routes.py（新建）
from __future__ import annotations

from app.storage.hr_account import upsert_account


def _client_with_account(make_test_client):
    client, conn = make_test_client()
    upsert_account(conn, username="alice", password="s3cret!")
    return client


def test_login_wrong_password_401(make_test_client):
    client = _client_with_account(make_test_client)
    resp = client.post("/api/auth/login", json={"username": "alice", "password": "wrong"})
    assert resp.status_code == 401


def test_login_then_protected_route_then_logout(make_test_client):
    client = _client_with_account(make_test_client)
    resp = client.post("/api/auth/login", json={"username": "alice", "password": "s3cret!"})
    assert resp.status_code == 200
    assert "hr_session" in resp.cookies

    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200
    # 登出后同一个 client 的 cookie 已被清掉，受保护路径应再次 401
    resp = client.get("/api/resumes/nonexistent/text")
    assert resp.status_code == 401
```

这个测试需要一个 `make_test_client` fixture——若仓库里还没有面向 `create_app()` 的共享 fixture，在 `tests/conftest.py` 里加：

```python
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.llm.gateway import LLMGateway
from app.storage.db import get_connection
from app.web.server import create_app


@pytest.fixture
def make_test_client(tmp_path):
    """⚠️ resume_storage_dir 显式指到 tmp_path 下——不传的话会落到
    Settings.resume_storage_dir 的默认值 "data/resumes"（相对 pytest 的运行
    目录），把测试上传的文件真的写进仓库工作区。

    gateway_factory 返回一个"不会被真正调用"的 LLMGateway：create_app() 在
    构造阶段就会调用一次 gateway_factory() 来装配图（app/web/server.py
    "gateway = gateway_factory()"），所以这里不能像别处那样用抛异常的桩，
    只能给一个不连真实网络的 client 占位（client=object()）——Task 7 起的
    用例都会 monkeypatch 掉 app.web.server.compute_parse 本身，不会真的走到
    gateway 内部去发请求。
    """

    def _make():
        db_path = str(tmp_path / "test.db")
        resume_dir = str(tmp_path / "resumes")

        def _gateway_factory():
            return LLMGateway(
                api_key="test", base_url="https://example.invalid",
                model="deepseek-chat", supports_json_schema=False, client=object(),
            )

        app = create_app(
            db_path=db_path, gateway_factory=_gateway_factory, root_path="",
            resume_storage_dir=resume_dir,
        )
        client = TestClient(app)
        conn = get_connection(db_path)
        return client, conn

    return _make
```

- [ ] **Step 13: 运行全部相关测试确认通过**

Run: `pytest tests/test_auth_session.py tests/test_auth_middleware.py tests/test_auth_routes.py -v`
Expected: 全部 PASS

- [ ] **Step 14: Commit**

```bash
git add app/storage/db.py app/storage/auth_session.py app/middleware/auth.py \
  app/web/server.py app/web/static/login.html tests/test_auth_session.py \
  tests/test_auth_middleware.py tests/test_auth_routes.py tests/conftest.py
git commit -m "feat(m2-u2): 本地账号登录换掉鉴权空壳（tasks 3.2）"
```

---

### Task 3: 数据模型补齐（`resume_parse_version` 表、`job.parse_confidence_threshold` 列）

**Files:**
- Modify: `app/storage/db.py`
- Test: `tests/test_db_m2_u2_schema.py`

**Interfaces:**
- Produces: `resume_parse_version` 表结构（供 Task 6 使用）；`job.parse_confidence_threshold` 列（供 Task 5 使用，默认 `0.7`，design D5）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_db_m2_u2_schema.py
from __future__ import annotations

import sqlite3

from app.storage.db import _existing_columns, apply_column_migrations, init_schema


def test_fresh_db_has_resume_parse_version_table():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    cols = _existing_columns(conn, "resume_parse_version")
    assert cols == {
        "resume_id",
        "parser_version",
        "parsed_json",
        "confidence",
        "model_configured",
        "model_response",
        "prompt_version",
        "parsed_at",
    }


def test_resume_table_has_raw_text_column():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    assert "raw_text" in _existing_columns(conn, "resume")


def test_old_job_table_gains_parse_confidence_threshold_via_migration():
    """模拟老库：先建一份没有新列的 job 表，再跑迁移，必须补上且默认 0.7。"""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE job (id TEXT PRIMARY KEY, title TEXT NOT NULL, "
        "department TEXT, status TEXT NOT NULL DEFAULT 'drafting', "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', 't')")
    added = apply_column_migrations(conn)
    assert "parse_confidence_threshold" in added
    value = conn.execute(
        "SELECT parse_confidence_threshold FROM job WHERE id = 'j1'"
    ).fetchone()[0]
    assert value == 0.7


def test_resume_parse_version_unique_per_resume_and_version():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', 't')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'hash1', 'alice')"
    )
    conn.execute(
        "INSERT INTO resume_parse_version "
        "(resume_id, parser_version, parsed_json, confidence, model_configured, "
        "model_response, prompt_version) VALUES ('r1', 'v1', '{}', 0.9, 'deepseek-chat', "
        "'deepseek-chat', 'parse-v1')"
    )
    conn.commit()
    with_conflict = sqlite3.IntegrityError
    try:
        conn.execute(
            "INSERT INTO resume_parse_version "
            "(resume_id, parser_version, parsed_json, confidence, model_configured, "
            "model_response, prompt_version) VALUES ('r1', 'v1', '{}', 0.1, 'x', 'x', 'x')"
        )
        raised = False
    except with_conflict:
        raised = True
    assert raised
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_db_m2_u2_schema.py -v`
Expected: 全部 FAIL（表/列不存在）

- [ ] **Step 3: 编辑 `app/storage/db.py`**

在 `resume` 表定义里加一列（`parser_version TEXT,` 之后加一行），并把误导性注释改掉：

```sql
CREATE TABLE IF NOT EXISTS resume (
    id TEXT PRIMARY KEY NOT NULL,
    job_id TEXT NOT NULL REFERENCES job(id),
    sample_class TEXT NOT NULL CHECK (
        sample_class IN ('synthetic', 'anonymized', 'departed', 'live')
    ),
    file_name TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'parsed', 'unreadable')),
    parsed_json TEXT,
    parse_confidence REAL,
    parser_version TEXT,
    raw_text TEXT,
    uploaded_by TEXT NOT NULL,
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

（这段 SQL 直接替换 `db.py` 里 `SCHEMA` 字符串常量内既有的 `resume` 表定义，不是独立的 Python 文件。）

把该表上方注释里那句改成（原文替换）：

```
-- parsed_json 存 app/schemas/resume_fields.py::ResumeFields 的 model_dump_json()；
-- parser_version/parse_confidence/parsed_json 三列缓存"最新一次解析结果"，
-- 每份历史解析（含重解析）的完整记录在 resume_parse_version 表（U2 tasks 3.8），
-- 工作台默认读本表三列即最新版，查历史版本才查 resume_parse_version。
-- raw_text 是抽取出的全文（app/parsing/extract_text.py::ExtractedText.text），
-- resume_text_span 的 start/end 偏移量都是相对这份原文——字段校对页的高亮
-- 必须对着这份原文切字符串，不能对着任何"重新拼接"的文本切（偏移会对不上）。
```

在 `resume_text_span` 表定义之后加新表：

```sql
-- 解析结果的完整历史（resume-parsing spec「解析留痕与版本」）。resume 表的
-- parsed_json/parse_confidence/parser_version 三列缓存"当前最新版"，本表存
-- 每一次解析尝试的完整记录，旧版本永久保留、不删除、不覆盖。
CREATE TABLE IF NOT EXISTS resume_parse_version (
    resume_id TEXT NOT NULL REFERENCES resume(id),
    parser_version TEXT NOT NULL,
    parsed_json TEXT NOT NULL,
    confidence REAL NOT NULL,
    model_configured TEXT NOT NULL,
    model_response TEXT,
    prompt_version TEXT NOT NULL,
    parsed_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (resume_id, parser_version)
);
```

（追加进 `SCHEMA` 字符串常量，紧跟 `resume_text_span` 表定义之后，同样不是独立文件。）

在 `_ADDED_COLUMNS` 元组里加一行（`job` 表目前没有任何 `_ADDED_COLUMNS` 记录，这是它的第一条）：

```python
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("job_profile", "is_productive", "INTEGER NOT NULL DEFAULT 1"),
    ("job_profile", "turn_started_at", "TEXT"),
    ("job_profile", "llm_latency_ms", "REAL"),
    ("job_profile", "derived_unspecified_fields", "TEXT NOT NULL DEFAULT '[]'"),
    ("job_profile", "ungrounded_fields", "TEXT NOT NULL DEFAULT '[]'"),
    ("job_profile", "written_fields", "TEXT NOT NULL DEFAULT '[]'"),
    ("job_profile", "llm_response_model", "TEXT"),
    ("job_profile", "asked_questions", "TEXT NOT NULL DEFAULT '[]'"),
    # design D5：置信度阈值是岗位级配置，默认 0.7 起步（U0 实测后由 U4 定终值）。
    ("job", "parse_confidence_threshold", "REAL NOT NULL DEFAULT 0.7"),
)
```

同时给 `CREATE TABLE IF NOT EXISTS job (...)` 加同一列，让新库一步到位（不依赖迁移路径）：

```sql
CREATE TABLE IF NOT EXISTS job (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    department TEXT,
    status TEXT NOT NULL DEFAULT 'drafting',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    parse_confidence_threshold REAL NOT NULL DEFAULT 0.7
);
```

（替换 `SCHEMA` 字符串常量里既有的 `job` 表定义，不是独立文件。）

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_db_m2_u2_schema.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 运行既有 schema 测试确认没有破坏老库迁移路径**

Run: `pytest tests/test_db_migration.py tests/test_db_m2_schema.py -v`
Expected: 全部 PASS（新库/老库列集合仍然一致）

- [ ] **Step 6: Commit**

```bash
git add app/storage/db.py tests/test_db_m2_u2_schema.py
git commit -m "feat(m2-u2): resume_parse_version 表与 job.parse_confidence_threshold 列（tasks 3.6/3.8 前置）"
```

---

### Task 4: 文件 → 文本 → 分片（含扫描件不可读退路与依赖登记）

**Files:**
- Create: `app/parsing/resume_ingest.py`
- Modify: `requirements.txt`
- Modify: `docs/tech-debt.md`
- Test: `tests/test_resume_ingest.py`

**Interfaces:**
- Consumes: `app.parsing.extract_text.extract_text`（已存在）、`app.parsing.extract_text.OcrUnavailable`（已存在）、`app.parsing.spans.split_into_spans`（已存在）
- Produces: `ingest_resume_text(path: Path) -> IngestResult`（dataclass：`readable: bool`, `raw_text: str`, `spans: list[TextSpan]`, `kind: str`），供 Task 7（上传接口）调用

- [ ] **Step 1: 加依赖**

在 `requirements.txt` 末尾加：

```
# M2 U2：简历文件解析（tasks 3.4）。版本号与 requirements-m2-u0.txt 里
# 2026-09-17 冒烟通过的版本一致（Mac 与 .51 同款 Windows 均可装，见
# docs/m2-model-comparison.md「环境」节）。
python-multipart==0.0.20
pypdf==6.19.0
python-docx==1.2.0
```

安装：

```bash
pip install python-multipart==0.0.20 pypdf==6.19.0 python-docx==1.2.0
```

- [ ] **Step 2: 登记 TD-52**

在 `docs/tech-debt.md` 末尾加：

```markdown
## TD-52 · PaddleOCR 在项目锁定的 Python 3.14 上装不上，扫描件走"不可读"退路

**欠的是什么**：`app/parsing/extract_text.py::ocr_pdf` 依赖 `paddleocr`/
`paddlepaddle`，2026-09-17 `[Mac]0917AX` 在 Mac 与 `.51` 同款 Windows 上均实测
`paddlepaddle` 无 cp314 wheel、`paddleocr` 依赖树内钉死 `PyYAML==6.0.2`（同样无
cp314 wheel，回退源码编译又缺 MSVC），详见 `docs/m2-model-comparison.md`「环境」
节。design D14 的退路已生效：扫描件识别时 `OcrUnavailable` 会被上抛，
`app/parsing/resume_ingest.py::ingest_resume_text`（本包 M2 U2 tasks 3.4）捕获后
把该简历标记为 `unreadable` 进人工队列，不阻塞上传接口的其它文件。

**触发条件**：`paddlepaddle` 发布 cp314 wheel，或项目降级到 cp313。任一条件满足
后，重新在 `.51` 同款环境跑一遍 `requirements-m2-u0.txt` 里的冒烟脚本，通过后
把 `paddleocr`/`paddlepaddle` 从"重依赖，单测懒加载"移进 `requirements.txt`。

**不还的后果**：扫描件简历在本项目全生命周期内都进人工队列，不参与硬门槛判定
与排序（这是 spec 明确允许的退路，不是缺陷）——后果是这部分候选人的自动化程度
低于文本型简历，需要 HR 手工补录关键字段，不影响系统正确性。
```

- [ ] **Step 3: 写失败测试**

```python
# tests/test_resume_ingest.py
from __future__ import annotations

from pathlib import Path

import docx
import pytest

from app.parsing.resume_ingest import ingest_resume_text


def _write_docx(path: Path, paragraphs: list[str]) -> Path:
    document = docx.Document()
    for p in paragraphs:
        document.add_paragraph(p)
    document.save(str(path))
    return path


def test_readable_docx_returns_spans(tmp_path):
    path = _write_docx(tmp_path / "a.docx", ["张三", "工作年限：5年", "技能：Python"])
    result = ingest_resume_text(path)
    assert result.readable is True
    assert result.kind == "docx"
    assert len(result.spans) == 3
    assert result.spans[0].text == "张三"
    assert result.raw_text.startswith("张三")


def test_unreadable_when_ocr_unavailable(tmp_path, monkeypatch):
    """扫描件路径：pypdf 抽出空文本 ⇒ 走 OCR ⇒ OcrUnavailable ⇒ readable=False。"""
    import app.parsing.extract_text as extract_text_mod

    def _empty_pdf_text(_path):
        return ""

    def _raise_ocr_unavailable(_path):
        raise extract_text_mod.OcrUnavailable("PaddleOCR 未安装")

    monkeypatch.setattr(extract_text_mod, "extract_pdf_text", _empty_pdf_text)
    fake_pdf = tmp_path / "scan.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")
    result = ingest_resume_text(fake_pdf, ocr=_raise_ocr_unavailable)
    assert result.readable is False
    assert result.spans == []


def test_unsupported_type_raises(tmp_path):
    from app.parsing.extract_text import UnsupportedFileType

    bad = tmp_path / "a.xlsx"
    bad.write_bytes(b"not a real xlsx")
    with pytest.raises(UnsupportedFileType):
        ingest_resume_text(bad)
```

- [ ] **Step 4: 运行测试确认失败**

Run: `pytest tests/test_resume_ingest.py -v`
Expected: `ModuleNotFoundError: No module named 'app.parsing.resume_ingest'`

- [ ] **Step 5: 实现 `app/parsing/resume_ingest.py`**

```python
"""文件路径 → (原文, 分片) 的组装层（resume-parsing spec「原文分片与字段回指」
「扫描件与不可读文件」）。纯函数：不写库，只读文件系统。

⛔ 不吞 UnsupportedFileType——那是"文件类型不在白名单"，调用方（Task 7 的
上传接口）需要它来给出"拒收：不支持的类型"这个逐文件结果（spec「批量上传
入口」）。只吞 OcrUnavailable——那是"这份文件本身没问题，只是扫描件识别能力
暂时不可用"，按 design D14 退路转成 readable=False。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.parsing.extract_text import OcrUnavailable, extract_text
from app.parsing.spans import TextSpan, split_into_spans


@dataclass(frozen=True)
class IngestResult:
    readable: bool
    raw_text: str
    spans: list[TextSpan]
    kind: str


def ingest_resume_text(
    path: Path, *, ocr: Callable[[Path], str] | None = None
) -> IngestResult:
    try:
        extracted = extract_text(path, ocr=ocr)
    except OcrUnavailable:
        return IngestResult(readable=False, raw_text="", spans=[], kind="pdf_scan")

    if not extracted.readable:
        return IngestResult(
            readable=False, raw_text=extracted.text, spans=[], kind=extracted.kind
        )

    spans = split_into_spans(extracted.text)
    return IngestResult(
        readable=True, raw_text=extracted.text, spans=spans, kind=extracted.kind
    )
```

- [ ] **Step 6: 运行测试确认通过**

Run: `pytest tests/test_resume_ingest.py -v`
Expected: 全部 PASS

- [ ] **Step 7: Commit**

```bash
git add app/parsing/resume_ingest.py requirements.txt docs/tech-debt.md tests/test_resume_ingest.py
git commit -m "feat(m2-u2): 文件转文本与分片组装，扫描件不可用退路（tasks 3.4）"
```

---

### Task 5: 六字段抽取 + 置信度合成（`compute_parse`）

**Files:**
- Create: `app/agents/resume_parser.py`
- Test: `tests/test_resume_parser.py`

**Interfaces:**
- Consumes: `app.llm.gateway.LLMGateway`（已存在）、`app.llm.gateway.LLMCallMeta`（已存在）、`app.schemas.resume_fields.ResumeFields`（已存在）、`app.parsing.spans.{TextSpan, resolve_span_ref, render_for_prompt}`（已存在）
- Produces: `compute_parse(gateway, *, spans, prompt_version="parse-v1", audit_context=None) -> tuple[ResumeFields, LLMCallMeta]`，供 Task 6 使用

- [ ] **Step 1: 写失败测试**

```python
# tests/test_resume_parser.py
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from app.agents.resume_parser import compute_parse
from app.llm.gateway import LLMGateway
from app.parsing.spans import split_into_spans


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


@dataclass
class _FakeUsage:
    prompt_tokens: int = 10
    completion_tokens: int = 10


class _FakeResponse:
    def __init__(self, content: str, model: str = "deepseek-chat") -> None:
        self.choices = [_FakeChoice(content)]
        self.model = model
        self.system_fingerprint = None
        self.usage = _FakeUsage()


class _FakeCompletions:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def create(self, **_kwargs):
        return _FakeResponse(json.dumps(self._payload, ensure_ascii=False))


class _FakeChat:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.completions = _FakeCompletions(payload)


class _FakeClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.chat = _FakeChat(payload)


def _gateway(payload: dict[str, Any]) -> LLMGateway:
    return LLMGateway(
        api_key="x",
        base_url="https://example.invalid",
        model="deepseek-chat",
        supports_json_schema=False,
        client=_FakeClient(payload),
    )


SPANS_TEXT = "张三\n工作年限：5年\n技能：Python、Rust\n"


def _field(value, span_id=None, quote=None, not_mentioned=False, confidence=0.9):
    return {
        "not_mentioned": not_mentioned,
        "confidence": confidence,
        "value": value,
        "spans": [] if span_id is None else [{"span_id": span_id, "quote": quote}],
    }


def test_field_with_resolvable_span_keeps_model_confidence():
    spans = split_into_spans(SPANS_TEXT)
    payload = {
        "name": _field("张三", span_id=1, quote="张三", confidence=0.95),
        "years_of_experience": _field(5, span_id=2, quote="5年", confidence=0.8),
        "skills": {**_field([], not_mentioned=False, confidence=0.7),
                   "value": ["Python", "Rust"], "spans": [{"span_id": 3, "quote": "Python、Rust"}]},
        "companies": _field([], not_mentioned=True, confidence=1.0),
        "education": {**_field(None, not_mentioned=True, confidence=1.0)},
        "expected_city": _field(None, not_mentioned=True, confidence=1.0),
    }
    fields, meta = compute_parse(_gateway(payload), spans=spans)
    assert fields.name.value == "张三"
    assert fields.name.confidence == 0.95
    assert fields.name.spans[0].start == 0
    assert fields.name.spans[0].end == 2
    assert meta.response_model == "deepseek-chat"


def test_field_without_resolvable_quote_gets_zero_confidence():
    spans = split_into_spans(SPANS_TEXT)
    payload = {
        "name": _field("李四", span_id=1, quote="这句话原文里没有", confidence=0.9),
        "years_of_experience": _field(None, not_mentioned=True, confidence=1.0),
        "skills": _field([], not_mentioned=True, confidence=1.0),
        "companies": _field([], not_mentioned=True, confidence=1.0),
        "education": _field(None, not_mentioned=True, confidence=1.0),
        "expected_city": _field(None, not_mentioned=True, confidence=1.0),
    }
    fields, _meta = compute_parse(_gateway(payload), spans=spans)
    assert fields.name.confidence == 0.0


def test_not_mentioned_field_is_untouched():
    spans = split_into_spans(SPANS_TEXT)
    payload = {
        "name": _field("张三", span_id=1, quote="张三", confidence=0.9),
        "years_of_experience": _field(None, not_mentioned=True, confidence=1.0),
        "skills": _field([], not_mentioned=True, confidence=1.0),
        "companies": _field([], not_mentioned=True, confidence=1.0),
        "education": _field(None, not_mentioned=True, confidence=1.0),
        "expected_city": _field(None, not_mentioned=True, confidence=1.0),
    }
    fields, _meta = compute_parse(_gateway(payload), spans=spans)
    assert fields.expected_city.not_mentioned is True
    assert fields.expected_city.confidence == 1.0  # 未提及字段不做置信度改写
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_resume_parser.py -v`
Expected: `ModuleNotFoundError: No module named 'app.agents.resume_parser'`

- [ ] **Step 3: 实现 `app/agents/resume_parser.py`**

```python
"""六字段抽取（resume-parsing spec「首期字段抽取」「原文分片与字段回指」
「字段级置信度与人工校对队列」，design D4/D5）。纯函数：只调 LLM 网关、做
数据转换，不写库（工程铁律 2）。
"""
from __future__ import annotations

from typing import Any

from app.llm.gateway import LLMCallMeta, LLMGateway
from app.parsing.spans import TextSpan, render_for_prompt, resolve_span_ref
from app.schemas.resume_fields import FIELD_LABELS, FIELD_NAMES, ResumeFields

PARSE_PROMPT_VERSION = "parse-v1"

_SYSTEM_PROMPT = (
    "你是简历结构化抽取助手。下面会给出一份简历原文，按行分片并标好编号"
    "（形如 [#3] 这一行原文）。请抽取以下六个字段：\n"
    + "\n".join(f"- {name}（{label}）" for name, label in FIELD_LABELS.items())
    + "\n\n规则：\n"
    "1. 每个字段必须给出 confidence（0~1，你对这次抽取结果的把握程度）。\n"
    "2. 简历中确实没有提到的字段，把 not_mentioned 设为 true，value 留空，"
    "⛔ 不要编造。\n"
    "3. 非未提及的字段，spans 至少给一条：span_id 填原文分片编号，quote 填"
    "从该分片**逐字摘录**（不得改写、不得省略号）的原文片段，用于人工核对。"
    "⛔ 不要给出 start/end（由系统计算，你留空即可）。\n"
    "4. education 字段的 value 是 {degree, school} 两个子字段，同样允许"
    "not_mentioned。\n"
    "5. skills、companies 是字符串列表。"
)


def compute_parse(
    gateway: LLMGateway,
    *,
    spans: list[TextSpan],
    prompt_version: str = PARSE_PROMPT_VERSION,
    audit_context: dict[str, Any] | None = None,
) -> tuple[ResumeFields, LLMCallMeta]:
    user_prompt = render_for_prompt(spans)
    fields, meta = gateway.extract_structured_with_meta(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        schema=ResumeFields,
        prompt_version=prompt_version,
        audit_context=audit_context,
    )
    resolved = _resolve_spans_and_synthesize_confidence(fields, spans)
    return resolved, meta


def _resolve_spans_and_synthesize_confidence(
    fields: ResumeFields, spans: list[TextSpan]
) -> ResumeFields:
    """design D5：最终置信度 = 模型自报置信度 × span 可定位性。

    可定位性是 0/1（不是连续值）：只要任意一条 span 的 quote 能在原文里反查到
    位置，就认为"有回指"，置信度维持模型自报值；一条都反查不到，置信度直接
    归零——spec「抽取结果无回指」：这种情况必须进人工校对队列，归零能保证
    "无论岗位阈值设多低，这个字段都会落进队列"（阈值默认 0.7，任何非负阈值
    都大于 0）。
    """
    for name in FIELD_NAMES:
        field = getattr(fields, name)
        if field.not_mentioned:
            continue
        located = False
        for span_ref in field.spans:
            resolved = resolve_span_ref(spans, span_ref.span_id, span_ref.quote)
            if resolved is not None:
                span_ref.start, span_ref.end = resolved
                located = True
        if not located:
            field.confidence = 0.0
    return fields
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_resume_parser.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add app/agents/resume_parser.py tests/test_resume_parser.py
git commit -m "feat(m2-u2): 六字段抽取与置信度合成（tasks 3.5/3.6）"
```

---

### Task 6: `effect_persist_parse`（首次解析建 candidate/application，重解析只加版本）

**Files:**
- Create: `app/graph/resume_nodes.py`
- Test: `tests/test_resume_nodes.py`

**Interfaces:**
- Consumes: `app.storage.idempotency.idempotent_effect`（已存在）、`app.schemas.resume_fields.{ResumeFields, FIELD_NAMES}`（已存在）
- Produces: `effect_persist_parse(conn, *, thread_id, business_key, resume_id, job_id, fields, parser_version, model_configured, model_response, prompt_version, confidence_threshold) -> str`（返回 `application_id`），供 Task 7/8 调用；`queue_reapplication_screening(resume_id) -> None`（U3 接入点空壳），供 Task 10 调用

- [ ] **Step 1: 写失败测试**

```python
# tests/test_resume_nodes.py
from __future__ import annotations

import json
import sqlite3

import pytest

from app.graph.resume_nodes import effect_persist_parse
from app.schemas.resume_fields import (
    EducationField,
    ListField,
    NumberField,
    ResumeFields,
    TextField,
)
from app.storage.db import init_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    c.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    c.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'hash1', 'alice')"
    )
    c.commit()
    return c


def _fields(*, name_confidence: float = 0.9, years_confidence: float = 0.9) -> ResumeFields:
    return ResumeFields(
        name=TextField(value="张三", confidence=name_confidence),
        years_of_experience=NumberField(value=5, confidence=years_confidence),
        skills=ListField(not_mentioned=True, value=[], confidence=1.0),
        companies=ListField(not_mentioned=True, value=[], confidence=1.0),
        education=EducationField(not_mentioned=True, value=None, confidence=1.0),
        expected_city=TextField(not_mentioned=True, value=None, confidence=1.0),
    )


def _persist(conn, **overrides):
    kwargs = dict(
        thread_id="r1",
        business_key="v1",
        resume_id="r1",
        job_id="j1",
        fields=_fields(),
        parser_version="v1",
        model_configured="deepseek-chat",
        model_response="deepseek-chat",
        prompt_version="parse-v1",
        confidence_threshold=0.7,
    )
    kwargs.update(overrides)
    return effect_persist_parse(conn, **kwargs)


def test_first_parse_creates_candidate_and_application(conn):
    application_id = _persist(conn)
    assert application_id is not None
    app_row = conn.execute(
        "SELECT candidate_id, job_id, resume_id, current_stage_id FROM application WHERE id = ?",
        (application_id,),
    ).fetchone()
    assert app_row == (app_row[0], "j1", "r1", "initial")
    candidate_row = conn.execute(
        "SELECT name FROM candidate WHERE id = ?", (app_row[0],)
    ).fetchone()
    assert candidate_row[0] == "张三"


def test_first_parse_writes_stage_history_and_resume_columns(conn):
    application_id = _persist(conn)
    history = conn.execute(
        "SELECT from_stage_id, to_stage_id, actor_type FROM application_stage_history "
        "WHERE application_id = ?",
        (application_id,),
    ).fetchone()
    assert history == (None, "initial", "agent")
    resume_row = conn.execute(
        "SELECT status, parser_version, parse_confidence FROM resume WHERE id = 'r1'"
    ).fetchone()
    assert resume_row[0] == "parsed"
    assert resume_row[1] == "v1"


def test_low_confidence_field_enters_review_queue(conn):
    _persist(conn, fields=_fields(name_confidence=0.3))
    row = conn.execute(
        "SELECT status FROM field_review_queue WHERE resume_id = 'r1' AND field = 'name'"
    ).fetchone()
    assert row is not None
    assert row[0] == "pending"


def test_high_confidence_field_does_not_enter_review_queue(conn):
    _persist(conn, fields=_fields(name_confidence=0.9))
    row = conn.execute(
        "SELECT 1 FROM field_review_queue WHERE resume_id = 'r1' AND field = 'name'"
    ).fetchone()
    assert row is None


def test_rerun_same_effect_key_does_not_duplicate(conn):
    first = _persist(conn)
    second = _persist(conn)
    assert first == second
    count = conn.execute("SELECT COUNT(*) FROM application").fetchone()[0]
    assert count == 1
    history_count = conn.execute(
        "SELECT COUNT(*) FROM application_stage_history"
    ).fetchone()[0]
    assert history_count == 1


def test_reparse_new_version_updates_resume_and_keeps_old_version_row(conn):
    _persist(conn, parser_version="v1", business_key="v1")
    application_id = _persist(
        conn,
        parser_version="v2",
        business_key="v2",
        fields=_fields(name_confidence=0.95),
    )
    versions = conn.execute(
        "SELECT parser_version FROM resume_parse_version WHERE resume_id = 'r1' "
        "ORDER BY parser_version"
    ).fetchall()
    assert [v[0] for v in versions] == ["v1", "v2"]
    resume_row = conn.execute(
        "SELECT parser_version FROM resume WHERE id = 'r1'"
    ).fetchone()
    assert resume_row[0] == "v2"
    application_count = conn.execute("SELECT COUNT(*) FROM application").fetchone()[0]
    assert application_count == 1  # 重解析不产生第二个 application
    assert application_id is not None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_resume_nodes.py -v`
Expected: `ModuleNotFoundError: No module named 'app.graph.resume_nodes'`

- [ ] **Step 3: 实现 `app/graph/resume_nodes.py`**

```python
"""简历解析结果落库（resume-parsing spec「字段级置信度与人工校对队列」
「解析留痕与版本」，design D2/D5/D11/D13）。

⚠️ 幂等 thread_id 用 resume_id，不是 application_id——application 在首次解析
完成前并不存在（候选人姓名恰恰是解析要抽取的字段）。见本计划「架构决策」第 1
条。
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid

from app.schemas.resume_fields import FIELD_NAMES, ResumeFields
from app.storage.idempotency import idempotent_effect

logger = logging.getLogger(__name__)


def _lowest_field_confidence(fields: ResumeFields) -> float:
    """resume.parse_confidence 记录本次解析里最低的那个字段置信度——
    工作台按这一列排序"哪份简历最需要人工介入"最直观。未提及的字段不计入
    （它们没有"抽取把握"这回事）。"""
    values = [
        getattr(fields, name).confidence
        for name in FIELD_NAMES
        if not getattr(fields, name).not_mentioned
    ]
    return min(values) if values else 1.0


def _find_or_create_candidate(conn: sqlite3.Connection, *, name: str) -> str:
    """按姓名去重（design D11 简化版：本单元不采集手机号，phone_hash 恒为
    NULL，见本计划「架构决策」第 4 条——同名不同人会被误判为同一候选人，
    这是已登记的已知限制，M3 采集手机号后按 (name, phone_hash) 重新收紧）。
    """
    row = conn.execute(
        "SELECT id FROM candidate WHERE name = ? AND phone_hash IS NULL", (name,)
    ).fetchone()
    if row is not None:
        return row[0]
    candidate_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO candidate (id, name) VALUES (?, ?)", (candidate_id, name)
    )
    return candidate_id


def _create_application(
    conn: sqlite3.Connection, *, candidate_id: str, job_id: str, resume_id: str
) -> str:
    application_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, ?, ?, ?, 'initial')",
        (application_id, candidate_id, job_id, resume_id),
    )
    conn.execute(
        "INSERT INTO application_stage_history "
        "(id, application_id, from_stage_id, to_stage_id, actor_type) "
        "VALUES (?, ?, NULL, 'initial', 'agent')",
        (str(uuid.uuid4()), application_id),
    )
    return application_id


def _upsert_review_queue_rows(
    conn: sqlite3.Connection,
    *,
    resume_id: str,
    fields: ResumeFields,
    confidence_threshold: float,
) -> None:
    for name in FIELD_NAMES:
        field = getattr(fields, name)
        if field.not_mentioned:
            continue
        if field.confidence >= confidence_threshold:
            continue
        machine_value = json.dumps(field.value if not hasattr(field.value, "model_dump")
                                    else field.value.model_dump(), ensure_ascii=False)
        conn.execute(
            "INSERT INTO field_review_queue (id, resume_id, field, machine_value, confidence) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(resume_id, field) WHERE status='pending' DO NOTHING",
            (str(uuid.uuid4()), resume_id, name, machine_value, field.confidence),
        )


@idempotent_effect("effect_persist_parse")
def effect_persist_parse(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    resume_id: str,
    job_id: str,
    fields: ResumeFields,
    parser_version: str,
    model_configured: str,
    model_response: str | None,
    prompt_version: str,
    confidence_threshold: float,
) -> str:
    """写 resume_parse_version（历史）+ resume 三列（最新版缓存）+
    field_review_queue（低置信度字段）；首次解析额外创建 candidate/application/
    application_stage_history。返回 application_id。

    ⛔ 不在这里 conn.commit()——由 idempotent_effect 装饰器统一提交（工程铁律 1）。
    """
    fields_json = fields.model_dump_json()
    confidence = _lowest_field_confidence(fields)

    conn.execute(
        "INSERT INTO resume_parse_version "
        "(resume_id, parser_version, parsed_json, confidence, model_configured, "
        "model_response, prompt_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (resume_id, parser_version, fields_json, confidence, model_configured,
         model_response, prompt_version),
    )
    conn.execute(
        "UPDATE resume SET status = 'parsed', parsed_json = ?, parse_confidence = ?, "
        "parser_version = ? WHERE id = ?",
        (fields_json, confidence, parser_version, resume_id),
    )

    existing = conn.execute(
        "SELECT id FROM application WHERE resume_id = ?", (resume_id,)
    ).fetchone()
    if existing is None:
        candidate_name = (
            fields.name.value if not fields.name.not_mentioned else "姓名待校对"
        )
        candidate_id = _find_or_create_candidate(conn, name=candidate_name)
        application_id = _create_application(
            conn, candidate_id=candidate_id, job_id=job_id, resume_id=resume_id
        )
    else:
        application_id = existing[0]

    _upsert_review_queue_rows(
        conn, resume_id=resume_id, fields=fields, confidence_threshold=confidence_threshold
    )

    return application_id


def queue_reapplication_screening(resume_id: str) -> None:
    """U3 硬门槛引擎的接入点空壳（tasks 3.10「触发该投递重判」，与
    app/middleware/auth.py::AuthMiddleware 是同一种"空壳接入点"手法）。

    ⛔ 本单元不实现重判逻辑——U3 还没有 compute_screen/effect_persist_flags。
    这里只留一个签名稳定的调用点：字段校对提交后调它，U3 落地时只需要把
    函数体换成真实的重判触发，⛔ 不改调用方（本函数所在 app/web/server.py 的
    /resumes/{id}/fields/{field}/review 路由不用动）。
    """
    logger.info(
        "resume_id=%s 的字段校对已完成，等待 U3 接入重判逻辑（当前为空壳）",
        resume_id,
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_resume_nodes.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add app/graph/resume_nodes.py tests/test_resume_nodes.py
git commit -m "feat(m2-u2): effect_persist_parse——首次解析建档、重解析加版本（tasks 3.7/3.8 前置）"
```

---

### Task 7: 上传接口 `POST /api/resumes/upload`（端到端）

**Files:**
- Modify: `app/web/server.py`
- Test: `tests/test_resume_upload.py`

**Interfaces:**
- Consumes: Task 1 `is_live_resume_intake_enabled`、Task 4 `ingest_resume_text`、Task 5 `compute_parse`、Task 6 `effect_persist_parse`
- Produces: `POST /api/resumes/upload`（multipart/form-data：`job_id`、`sample_class`、`files[]`）→ 逐文件结果数组

- [ ] **Step 1: 写失败测试（先写测试再改路由，符合 TDD 但 FastAPI 路由测试天然是"跑起来才知道"，故本任务的 RED 是"路由不存在，404/405"）**

```python
# tests/test_resume_upload.py
from __future__ import annotations

import io

import docx
import pytest

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account


def _docx_bytes(paragraphs: list[str]) -> bytes:
    document = docx.Document()
    for p in paragraphs:
        document.add_paragraph(p)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


@pytest.fixture
def logged_in_client(make_test_client):
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.commit()
    return client, conn


def test_missing_job_id_or_sample_class_rejects_whole_batch(logged_in_client):
    client, _conn = logged_in_client
    files = [("files", ("a.docx", _docx_bytes(["张三"]),
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]
    resp = client.post("/api/resumes/upload", data={"job_id": "j1"}, files=files)
    assert resp.status_code == 422


def test_unsupported_type_is_rejected_others_accepted(logged_in_client, monkeypatch):
    client, _conn = logged_in_client
    _stub_llm_success(monkeypatch)
    files = [
        ("files", ("a.docx", _docx_bytes(["张三"]),
                   "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
        ("files", ("b.xlsx", b"not really xlsx",
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
    ]
    resp = client.post(
        "/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "synthetic"},
        files=files,
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert results[0]["status"] == "accepted"
    assert results[1]["status"] == "rejected"
    assert "不支持的类型" in results[1]["reason"]


def test_live_sample_class_rejected_when_gate_closed(logged_in_client):
    client, _conn = logged_in_client
    files = [("files", ("a.docx", _docx_bytes(["张三"]),
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]
    resp = client.post(
        "/api/resumes/upload", data={"job_id": "j1", "sample_class": "live"}, files=files
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"][0]["status"] == "rejected"
    assert "入库闸未开启" in body["results"][0]["reason"]


def test_duplicate_content_hash_returns_existing_resume_id(logged_in_client, monkeypatch):
    client, _conn = logged_in_client
    _stub_llm_success(monkeypatch)
    content = _docx_bytes(["张三"])
    files = [("files", ("a.docx", content,
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]
    first = client.post(
        "/api/resumes/upload", data={"job_id": "j1", "sample_class": "synthetic"}, files=files
    ).json()["results"][0]
    second = client.post(
        "/api/resumes/upload", data={"job_id": "j1", "sample_class": "synthetic"}, files=files
    ).json()["results"][0]
    assert second["status"] == "duplicate"
    assert second["resume_id"] == first["resume_id"]


def _stub_llm_success(monkeypatch):
    import app.web.server as server_mod
    from app.llm.gateway import LLMCallMeta
    from app.schemas.resume_fields import (
        EducationField,
        ListField,
        NumberField,
        ResumeFields,
        TextField,
    )

    def _fake_compute_parse(_gateway, *, spans, prompt_version="parse-v1", audit_context=None):
        fields = ResumeFields(
            name=TextField(value="张三", confidence=0.95,
                            spans=[{"span_id": 1, "quote": "张三", "start": 0, "end": 2}]),
            years_of_experience=NumberField(not_mentioned=True, value=None, confidence=1.0),
            skills=ListField(not_mentioned=True, value=[], confidence=1.0),
            companies=ListField(not_mentioned=True, value=[], confidence=1.0),
            education=EducationField(not_mentioned=True, value=None, confidence=1.0),
            expected_city=TextField(not_mentioned=True, value=None, confidence=1.0),
        )
        return fields, LLMCallMeta(latency_ms=1.0, response_model="deepseek-chat", attempts=1)

    monkeypatch.setattr(server_mod, "compute_parse", _fake_compute_parse)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_resume_upload.py -v`
Expected: `404 Not Found`（路由不存在）导致断言失败

- [ ] **Step 3: 在 `app/web/server.py` 加上传路由**

顶部 import 区加：

```python
import hashlib
from pathlib import Path

from fastapi import File, Form, UploadFile

from app.agents.resume_parser import compute_parse
from app.graph.resume_nodes import effect_persist_parse
from app.parsing.extract_text import UnsupportedFileType
from app.parsing.resume_ingest import ingest_resume_text
from app.storage.live_resume_gate import is_live_resume_intake_enabled
```

`create_app` 签名里已有 `settings`？没有——`create_app(*, db_path, gateway_factory, root_path="")` 不接收 `Settings`。补一个可选参数，默认从 `get_settings()` 取，不破坏既有调用方：

```python
def create_app(
    *,
    db_path: str,
    gateway_factory: Callable,
    root_path: str = "",
    resume_storage_dir: str | None = None,
) -> FastAPI:
```

在函数体靠前的位置（`conn = get_connection(db_path)` 之后）加：

```python
    from app.config import get_settings

    _resume_storage_dir = Path(resume_storage_dir or get_settings().resume_storage_dir)
    _resume_storage_dir.mkdir(parents=True, exist_ok=True)
```

`app/main.py` 里 `create_app(...)` 调用处补上 `resume_storage_dir=settings.resume_storage_dir`（不改变其它现有调用方，因为参数有默认值）。

在路由区加上传接口：

```python
    _VALID_SAMPLE_CLASSES = {"synthetic", "anonymized", "departed", "live"}

    @router.post("/api/resumes/upload")
    def upload_resumes(
        request: Request,
        job_id: str = Form(...),
        sample_class: str = Form(...),
        files: list[UploadFile] = File(...),
    ):
        if sample_class not in _VALID_SAMPLE_CLASSES:
            raise HTTPException(status_code=422, detail="sample_class 取值非法")
        job = conn.execute("SELECT id FROM job WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")

        uploader = reviewer_of(request)
        results = []

        if sample_class == "live":
            gate_open = is_live_resume_intake_enabled(auth=request.state.auth, conn=conn)
            if not gate_open:
                for f in files:
                    results.append({
                        "file_name": f.filename,
                        "status": "rejected",
                        "reason": "真实简历入库闸未开启",
                    })
                logger.warning(
                    "闸关闭时的 live 上传尝试：uploader=%s job_id=%s file_count=%d",
                    uploader, job_id, len(files),
                )
                return {"results": results}

        for f in files:
            results.append(_ingest_one_resume(job_id=job_id, sample_class=sample_class,
                                               uploaded_by=uploader, upload=f))
        return {"results": results}

    def _ingest_one_resume(*, job_id: str, sample_class: str, uploaded_by: str,
                            upload: UploadFile) -> dict:
        suffix = Path(upload.filename or "").suffix.lower()
        content = upload.file.read()
        if suffix not in (".pdf", ".docx"):
            return {"file_name": upload.filename, "status": "rejected",
                    "reason": "不支持的类型"}

        content_hash = hashlib.sha256(content).hexdigest()
        dup = conn.execute(
            "SELECT id FROM resume WHERE job_id = ? AND content_sha256 = ?",
            (job_id, content_hash),
        ).fetchone()
        if dup is not None:
            return {"file_name": upload.filename, "status": "duplicate",
                    "resume_id": dup[0]}

        resume_id = str(uuid.uuid4())
        stored_path = _resume_storage_dir / f"{resume_id}{suffix}"
        stored_path.write_bytes(content)

        conn.execute(
            "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
            "uploaded_by) VALUES (?, ?, ?, ?, ?, ?)",
            (resume_id, job_id, sample_class, upload.filename, content_hash, uploaded_by),
        )
        conn.commit()

        ingest_result = ingest_resume_text(stored_path)
        if not ingest_result.readable:
            conn.execute(
                "UPDATE resume SET status = 'unreadable' WHERE id = ?", (resume_id,)
            )
            conn.commit()
            return {"file_name": upload.filename, "status": "accepted",
                    "resume_id": resume_id, "parse_status": "unreadable"}

        conn.execute(
            "UPDATE resume SET raw_text = ? WHERE id = ?",
            (ingest_result.raw_text, resume_id),
        )
        for span in ingest_result.spans:
            conn.execute(
                "INSERT INTO resume_text_span (resume_id, span_id, start, end, text) "
                "VALUES (?, ?, ?, ?, ?)",
                (resume_id, span.span_id, span.start, span.end, span.text),
            )
        conn.commit()

        threshold_row = conn.execute(
            "SELECT parse_confidence_threshold FROM job WHERE id = ?", (job_id,)
        ).fetchone()
        confidence_threshold = threshold_row[0] if threshold_row else 0.7

        try:
            fields, meta = compute_parse(
                gateway,
                spans=ingest_result.spans,
                audit_context={"thread_id": resume_id, "node": "compute_parse", "job_id": job_id},
            )
        except Exception:
            logger.exception("resume_id=%s 抽取失败，简历留在 pending，可稍后重解析", resume_id)
            return {"file_name": upload.filename, "status": "accepted",
                    "resume_id": resume_id, "parse_status": "parse_failed"}

        parser_version = "v1"
        application_id = effect_persist_parse(
            conn,
            thread_id=resume_id,
            business_key=parser_version,
            resume_id=resume_id,
            job_id=job_id,
            fields=fields,
            parser_version=parser_version,
            model_configured=gateway._model,
            model_response=meta.response_model,
            prompt_version="parse-v1",
            confidence_threshold=confidence_threshold,
        )
        return {"file_name": upload.filename, "status": "accepted",
                "resume_id": resume_id, "application_id": application_id,
                "parse_status": "parsed"}
```

`gateway._model` 是访问私有属性；改成在 `LLMGateway` 上补一个只读属性更干净——加到 `app/llm/gateway.py` 的 `LLMGateway` 类里（`__init__` 之后任意位置）：

```python
    @property
    def model(self) -> str:
        return self._model
```

并把上面路由代码里的 `gateway._model` 改成 `gateway.model`。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_resume_upload.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 补一条 `LLMGateway.model` 属性的单测**

```python
# 追加到 tests/test_llm_gateway.py（若该文件已存在就追加函数；不存在则新建）
def test_model_property_exposes_configured_model():
    from app.llm.gateway import LLMGateway

    gateway = LLMGateway(
        api_key="x", base_url="https://example.invalid", model="deepseek-chat",
        supports_json_schema=False, client=object(),
    )
    assert gateway.model == "deepseek-chat"
```

Run: `pytest tests/test_llm_gateway.py -v`
Expected: 该用例 PASS，且既有用例不受影响

- [ ] **Step 6: Commit**

```bash
git add app/web/server.py app/llm/gateway.py app/main.py tests/test_resume_upload.py tests/test_llm_gateway.py
git commit -m "feat(m2-u2): POST /api/resumes/upload 端到端上传解析（tasks 3.3）"
```

---

### Task 8: 重解析接口 `POST /api/resumes/{id}/reparse`

**Files:**
- Modify: `app/web/server.py`
- Test: `tests/test_resume_reparse.py`

**Interfaces:**
- Consumes: Task 5 `compute_parse`、Task 6 `effect_persist_parse`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_resume_reparse.py
from __future__ import annotations

import pytest

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account
from tests.test_resume_upload import _docx_bytes, _stub_llm_success


@pytest.fixture
def uploaded_resume(make_test_client, monkeypatch):
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.commit()
    _stub_llm_success(monkeypatch)
    files = [("files", ("a.docx", _docx_bytes(["张三"]),
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]
    resp = client.post(
        "/api/resumes/upload", data={"job_id": "j1", "sample_class": "synthetic"}, files=files
    )
    resume_id = resp.json()["results"][0]["resume_id"]
    return client, conn, resume_id


def test_reparse_creates_new_version_and_updates_resume(uploaded_resume, monkeypatch):
    client, conn, resume_id = uploaded_resume
    resp = client.post(f"/api/resumes/{resume_id}/reparse")
    assert resp.status_code == 200
    versions = conn.execute(
        "SELECT parser_version FROM resume_parse_version WHERE resume_id = ? ORDER BY parser_version",
        (resume_id,),
    ).fetchall()
    assert len(versions) == 2


def test_reparse_nonexistent_resume_404(uploaded_resume):
    client, _conn, _resume_id = uploaded_resume
    resp = client.post("/api/resumes/does-not-exist/reparse")
    assert resp.status_code == 404
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_resume_reparse.py -v`
Expected: FAIL（路由不存在）

- [ ] **Step 3: 加路由**

```python
    @router.post("/api/resumes/{resume_id}/reparse")
    def reparse_resume(resume_id: str):
        row = conn.execute(
            "SELECT job_id FROM resume WHERE id = ?", (resume_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="resume not found")
        job_id = row[0]

        spans = [
            TextSpan(span_id=r[0], start=r[1], end=r[2], text=r[3])
            for r in conn.execute(
                "SELECT span_id, start, end, text FROM resume_text_span "
                "WHERE resume_id = ? ORDER BY span_id",
                (resume_id,),
            ).fetchall()
        ]
        threshold_row = conn.execute(
            "SELECT parse_confidence_threshold FROM job WHERE id = ?", (job_id,)
        ).fetchone()
        confidence_threshold = threshold_row[0] if threshold_row else 0.7

        existing_versions = conn.execute(
            "SELECT COUNT(*) FROM resume_parse_version WHERE resume_id = ?", (resume_id,)
        ).fetchone()[0]
        parser_version = f"v{existing_versions + 1}"

        fields, meta = compute_parse(
            gateway,
            spans=spans,
            audit_context={"thread_id": resume_id, "node": "compute_parse", "job_id": job_id},
        )
        application_id = effect_persist_parse(
            conn,
            thread_id=resume_id,
            business_key=parser_version,
            resume_id=resume_id,
            job_id=job_id,
            fields=fields,
            parser_version=parser_version,
            model_configured=gateway.model,
            model_response=meta.response_model,
            prompt_version="parse-v1",
            confidence_threshold=confidence_threshold,
        )
        return {"resume_id": resume_id, "application_id": application_id,
                "parser_version": parser_version}
```

顶部 import 加 `from app.parsing.spans import TextSpan`。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_resume_reparse.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add app/web/server.py tests/test_resume_reparse.py
git commit -m "feat(m2-u2): 重解析接口，新旧版本并存（tasks 3.8）"
```

---

### Task 9: 简历访问留痕（原文/分片/解析结果/下载四个只读接口）

**Files:**
- Modify: `app/web/server.py`
- Test: `tests/test_resume_access_log.py`

**Interfaces:**
- Produces: `record_resume_access(conn, *, accessor, resume_id, access_type) -> None`（写入即返回，失败抛异常）；四个 GET 路由

- [ ] **Step 1: 写失败测试**

```python
# tests/test_resume_access_log.py
from __future__ import annotations

import pytest

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account
from tests.test_resume_upload import _docx_bytes, _stub_llm_success


@pytest.fixture
def uploaded_resume(make_test_client, monkeypatch):
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.commit()
    _stub_llm_success(monkeypatch)
    files = [("files", ("a.docx", _docx_bytes(["张三"]),
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]
    resp = client.post(
        "/api/resumes/upload", data={"job_id": "j1", "sample_class": "synthetic"}, files=files
    )
    resume_id = resp.json()["results"][0]["resume_id"]
    return client, conn, resume_id


@pytest.mark.parametrize(
    "path_suffix,access_type",
    [("text", "raw_text"), ("spans", "spans"), ("parsed", "parsed_result")],
)
def test_each_read_endpoint_writes_access_log(uploaded_resume, path_suffix, access_type):
    client, conn, resume_id = uploaded_resume
    resp = client.get(f"/api/resumes/{resume_id}/{path_suffix}")
    assert resp.status_code == 200
    row = conn.execute(
        "SELECT accessor, access_type FROM resume_access_log "
        "WHERE resume_id = ? AND access_type = ?",
        (resume_id, access_type),
    ).fetchone()
    assert row == ("alice", access_type)


def test_download_writes_access_log_and_returns_file(uploaded_resume):
    client, conn, resume_id = uploaded_resume
    resp = client.get(f"/api/resumes/{resume_id}/download")
    assert resp.status_code == 200
    row = conn.execute(
        "SELECT 1 FROM resume_access_log WHERE resume_id = ? AND access_type = 'download'",
        (resume_id,),
    ).fetchone()
    assert row is not None


def test_access_log_write_failure_blocks_the_read(uploaded_resume, monkeypatch):
    client, _conn, resume_id = uploaded_resume
    import app.web.server as server_mod

    def _boom(*_args, **_kwargs):
        raise sqlite3_error()

    def sqlite3_error():
        import sqlite3
        return sqlite3.OperationalError("disk full (模拟)")

    monkeypatch.setattr(server_mod, "record_resume_access", _boom)
    resp = client.get(f"/api/resumes/{resume_id}/text")
    assert resp.status_code == 500
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_resume_access_log.py -v`
Expected: FAIL（路由不存在）

- [ ] **Step 3: 在 `app/graph/resume_nodes.py` 加 `record_resume_access`**

```python
def record_resume_access(
    conn: sqlite3.Connection, *, accessor: str, resume_id: str, access_type: str
) -> None:
    """resume-upload-and-gate spec「简历访问留痕」：写入失败必须让调用方的读取
    也失败——本函数不吞任何异常，调用方（路由）不 catch 就是正确行为
    （FastAPI 未捕获异常 ⇒ 500，读取自然失败，不返回简历内容）。
    """
    conn.execute(
        "INSERT INTO resume_access_log (id, accessor, resume_id, access_type) "
        "VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), accessor, resume_id, access_type),
    )
    conn.commit()
```

- [ ] **Step 4: 在 `app/web/server.py` 加四个只读路由**

```python
    from fastapi.responses import FileResponse

    from app.graph.resume_nodes import record_resume_access

    def _require_resume(resume_id: str) -> tuple:
        row = conn.execute(
            "SELECT id, file_name, raw_text, parsed_json, sample_class FROM resume WHERE id = ?",
            (resume_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="resume not found")
        return row

    @router.get("/api/resumes/{resume_id}/text")
    def get_resume_text(request: Request, resume_id: str):
        row = _require_resume(resume_id)
        record_resume_access(conn, accessor=reviewer_of(request), resume_id=resume_id,
                              access_type="raw_text")
        return {"resume_id": resume_id, "raw_text": row[2] or ""}

    @router.get("/api/resumes/{resume_id}/spans")
    def get_resume_spans(request: Request, resume_id: str):
        _require_resume(resume_id)
        record_resume_access(conn, accessor=reviewer_of(request), resume_id=resume_id,
                              access_type="spans")
        rows = conn.execute(
            "SELECT span_id, start, end, text FROM resume_text_span "
            "WHERE resume_id = ? ORDER BY span_id",
            (resume_id,),
        ).fetchall()
        return {"spans": [
            {"span_id": r[0], "start": r[1], "end": r[2], "text": r[3]} for r in rows
        ]}

    @router.get("/api/resumes/{resume_id}/parsed")
    def get_resume_parsed(request: Request, resume_id: str):
        row = _require_resume(resume_id)
        record_resume_access(conn, accessor=reviewer_of(request), resume_id=resume_id,
                              access_type="parsed_result")
        return {"resume_id": resume_id, "parsed_json": json.loads(row[3]) if row[3] else None}

    @router.get("/api/resumes/{resume_id}/download")
    def download_resume(request: Request, resume_id: str):
        row = _require_resume(resume_id)
        file_name = row[1]
        suffix = Path(file_name).suffix.lower()
        stored_path = _resume_storage_dir / f"{resume_id}{suffix}"
        if not stored_path.exists():
            raise HTTPException(status_code=404, detail="文件已不在存储中")
        record_resume_access(conn, accessor=reviewer_of(request), resume_id=resume_id,
                              access_type="download")
        return FileResponse(str(stored_path), filename=file_name)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_resume_access_log.py -v`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add app/graph/resume_nodes.py app/web/server.py tests/test_resume_access_log.py
git commit -m "feat(m2-u2): 简历读取四接口统一走访问留痕（tasks 3.9）"
```

---

### Task 10: 字段校对接口 `POST /api/resumes/{id}/fields/{field}/review`

**Files:**
- Modify: `app/web/server.py`
- Test: `tests/test_resume_field_review.py`

**Interfaces:**
- Consumes: Task 6 `queue_reapplication_screening`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_resume_field_review.py
from __future__ import annotations

import pytest

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account
from tests.test_resume_upload import _docx_bytes, _stub_llm_success


@pytest.fixture
def low_confidence_resume(make_test_client, monkeypatch):
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.commit()

    import app.web.server as server_mod
    from app.llm.gateway import LLMCallMeta
    from app.schemas.resume_fields import (
        EducationField, ListField, NumberField, ResumeFields, TextField,
    )

    def _fake_compute_parse(_gateway, *, spans, prompt_version="parse-v1", audit_context=None):
        fields = ResumeFields(
            name=TextField(value="张三", confidence=0.2,
                            spans=[{"span_id": 1, "quote": "张三", "start": 0, "end": 2}]),
            years_of_experience=NumberField(not_mentioned=True, value=None, confidence=1.0),
            skills=ListField(not_mentioned=True, value=[], confidence=1.0),
            companies=ListField(not_mentioned=True, value=[], confidence=1.0),
            education=EducationField(not_mentioned=True, value=None, confidence=1.0),
            expected_city=TextField(not_mentioned=True, value=None, confidence=1.0),
        )
        return fields, LLMCallMeta(latency_ms=1.0, response_model="deepseek-chat", attempts=1)

    monkeypatch.setattr(server_mod, "compute_parse", _fake_compute_parse)

    files = [("files", ("a.docx", _docx_bytes(["张三"]),
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]
    resp = client.post(
        "/api/resumes/upload", data={"job_id": "j1", "sample_class": "synthetic"}, files=files
    )
    resume_id = resp.json()["results"][0]["resume_id"]
    return client, conn, resume_id


def test_review_updates_queue_and_records_reviewer(low_confidence_resume):
    client, conn, resume_id = low_confidence_resume
    resp = client.post(
        f"/api/resumes/{resume_id}/fields/name/review",
        json={"human_value": "张三三"},
    )
    assert resp.status_code == 200
    row = conn.execute(
        "SELECT status, human_value, reviewed_by FROM field_review_queue "
        "WHERE resume_id = ? AND field = 'name'",
        (resume_id,),
    ).fetchone()
    assert row == ("reviewed", "张三三", "alice")


def test_review_is_idempotent_for_same_value(low_confidence_resume):
    client, conn, resume_id = low_confidence_resume
    client.post(f"/api/resumes/{resume_id}/fields/name/review", json={"human_value": "张三三"})
    resp = client.post(
        f"/api/resumes/{resume_id}/fields/name/review", json={"human_value": "张三三"}
    )
    assert resp.status_code == 200
    count = conn.execute(
        "SELECT COUNT(*) FROM field_review_queue WHERE resume_id = ? AND field = 'name'",
        (resume_id,),
    ).fetchone()[0]
    assert count == 1


def test_review_unknown_field_404(low_confidence_resume):
    client, _conn, resume_id = low_confidence_resume
    resp = client.post(
        f"/api/resumes/{resume_id}/fields/name/review", json={"human_value": "x"}
    )
    assert resp.status_code == 200
    resp2 = client.post(
        f"/api/resumes/{resume_id}/fields/years_of_experience/review", json={"human_value": "5"}
    )
    assert resp2.status_code == 404  # 该字段没有 pending 队列行
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_resume_field_review.py -v`
Expected: FAIL（路由不存在）

- [ ] **Step 3: 加路由**

```python
    class FieldReviewRequest(BaseModel):
        human_value: str

    @router.post("/api/resumes/{resume_id}/fields/{field}/review")
    def review_field(request: Request, resume_id: str, field: str, req: FieldReviewRequest):
        row = conn.execute(
            "SELECT id, human_value FROM field_review_queue "
            "WHERE resume_id = ? AND field = ? AND status = 'pending'",
            (resume_id, field),
        ).fetchone()
        if row is None:
            already_reviewed = conn.execute(
                "SELECT human_value FROM field_review_queue "
                "WHERE resume_id = ? AND field = ? AND status = 'reviewed' "
                "ORDER BY created_at DESC LIMIT 1",
                (resume_id, field),
            ).fetchone()
            if already_reviewed is not None and already_reviewed[0] == req.human_value:
                return {"ok": True, "already_reviewed": True}
            raise HTTPException(status_code=404, detail="该字段没有待校对记录")

        reviewer = reviewer_of(request)
        conn.execute(
            "UPDATE field_review_queue SET status = 'reviewed', human_value = ?, "
            "reviewed_by = ?, reviewed_at = datetime('now') WHERE id = ?",
            (req.human_value, reviewer, row[0]),
        )
        conn.commit()
        queue_reapplication_screening(resume_id)
        return {"ok": True, "already_reviewed": False}
```

顶部 import 加 `from app.graph.resume_nodes import queue_reapplication_screening`（与 `effect_persist_parse`、`record_resume_access` 同一行 import 语句合并即可）。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_resume_field_review.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 运行本单元全部测试，确认互不干扰**

Run: `pytest tests/test_live_resume_gate.py tests/test_auth_session.py tests/test_auth_middleware.py tests/test_auth_routes.py tests/test_db_m2_u2_schema.py tests/test_resume_ingest.py tests/test_resume_parser.py tests/test_resume_nodes.py tests/test_resume_upload.py tests/test_resume_reparse.py tests/test_resume_access_log.py tests/test_resume_field_review.py -v`
Expected: 全部 PASS

- [ ] **Step 6: 运行全仓库测试，确认没有破坏 M1/U0/U1 既有功能**

Run: `pytest -q`
Expected: 全部 PASS（新增依赖已装，若某些 U0 冒烟测试因缺 `paddleocr`/`FlagEmbedding` 而 `skip` 属预期，不算失败）

- [ ] **Step 7: Commit**

```bash
git add app/web/server.py tests/test_resume_field_review.py
git commit -m "feat(m2-u2): 字段校对接口，完成后触发重判空壳（tasks 3.10）"
```

---

## Self-Review 记录（写计划人自查，交付前已完成）

**Spec 覆盖**：
- 「批量上传入口」→ Task 7（多文件、逐文件结果、类型白名单、必填校验）
- 「真实简历入库闸」→ Task 1（求值）+ Task 7（闸命中处理与留痕）
- 「简历访问留痕」→ Task 9
- 「可识别到人的登录」→ Task 2
- 「首期字段抽取」→ Task 5
- 「原文分片与字段回指」→ Task 4（分片）+ Task 5（回指与反查）
- 「字段级置信度与人工校对队列」→ Task 5（合成）+ Task 6（落库）+ Task 10（校对提交）
- 「解析留痕与版本」→ Task 6（`resume_parse_version`）+ Task 8（重解析接口）
- 「扫描件与不可读文件」→ Task 4

**占位符扫描**：全部代码块含真实实现；`queue_reapplication_screening` 是刻意的空壳接入点（有先例：`AuthMiddleware` 在 M1 就是同样写法），不算占位符。

**类型一致性**：`ResumeFields`/`TextField`/`NumberField`/`ListField`/`EducationField`/`FIELD_NAMES`/`FIELD_LABELS` 全部复用 `app/schemas/resume_fields.py` 既有定义，各 Task 之间未重新定义。`effect_persist_parse` 的参数名（`resume_id`/`job_id`/`fields`/`parser_version`/`model_configured`/`model_response`/`prompt_version`/`confidence_threshold`）在 Task 6/7/8 三处调用点逐字一致。

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-18-m2-resume-parse-and-rank-unit2-upload-and-parsing.md`. 两种执行方式：

1. **Subagent-Driven（推荐）**——每个 Task 派一个新鲜子代理，两阶段 review，快速迭代
2. **Inline Execution**——本 session 内批量执行，检查点式推进

（本计划由无头 opener `0918E` 生成，不在本轮内做执行方式选择——按 `03-工具链协作规则.md`，`run-build` 是独立的下一个 opener。）
