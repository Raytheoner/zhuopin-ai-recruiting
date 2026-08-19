# server-runtime-logging · 服务运行时可观测性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 `.51` 现网补上运行时日志通道——进程启停与运行期日志持久化到有界的文件、一次请求的所有日志可由单一标识串联、500 必须留下含请求标识与业务会话标识的完整堆栈、日志内容受个人信息脱敏与留存期约束。范围 = `openspec/changes/server-runtime-logging/tasks.md` 第 1～4 章，逐条覆盖 `specs/runtime-observability/spec.md` 的全部 5 条 Requirement。

**Architecture:** 应用内一处 `logging.config.dictConfig` 统一接管根 logger、应用 logger 与 uvicorn 三个 logger（design 决策 1），在 `app/main.py` 导入期、早于 `create_app` 调用——uvicorn 在 `Config.__init__` 里先 `configure_logging()`、之后才 `load()` 导入应用模块，所以本配置一定后手生效。轮转用自定义 `DailyRotatingFileHandler`（按天 + 单文件大小兜底 + 按 mtime 清理留存期）。请求标识由**纯 ASGI 中间件**写入 `contextvars`、由挂在 handler 上的 `logging.Filter` 注入每条 record，业务代码已有的 `logger.*` 调用点一行都不改。脱敏两层：主防线是 `loggable_summary()`（白名单外一律不输出取值），兜底是 `RedactionFilter` + `RedactingFormatter`（后者补扫异常堆栈，Filter 看不到那部分）。日志目录不可写时降级为仅 stdout 并在 `/health` 暴露，绝不因此拒绝业务功能。

**Tech Stack:** Python 3.14（`venv/bin/python`，与 `.51` 严格对齐）、FastAPI 0.115.6 + Starlette 0.41.3、uvicorn[standard] 0.34.0、pydantic-settings 2.15.0、LangGraph 1.0.10、pytest 8.3.4、httpx 0.28.1、Python 标准库 `logging` / `logging.handlers` / `contextvars`。**不新增任何依赖**——`requirements.txt` 本次不改。

---

## Global Constraints

以下条目**逐字**来自项目 `CLAUDE.md` 的「工程铁律」7 条、「合规红线」7 条与「部署约束」5 条（铁律 1 为 2026-08-19 补充幂等事务性要求后的最新版）。本计划每个 Task 隐含都要遵守，`subagent-driven-development` 会把这一段原样交给 reviewer 当注意力透镜。

### 工程铁律（不可违背）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
   *为什么*：业务写失败而幂等记录成功 → 系统判定"已执行"→ 永不重试。**幂等本是防重复的保护，拆开事务后变成永久丢失的保证。** 实证：`.51` 现网 2026-08-10 与 08-12 各丢一轮 `outbox`（幂等记录已落），用户没收到回复且永远不会补发，见 `docs/findings/2026-08-13-sqlite-事务归属冲突.md` §8.5。
2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。
3. **所有 AI 评分必须持久化**：模型标识 + 模型版本 + prompt 版本 + temperature + 输入哈希 + rubric 快照 + 原始响应。
4. **每条 `criterion_score` 必须有 `evidence_ref`**（回指简历原文或面试 turn 的 offset）。`evidence_ref` 为空不允许写入。
5. **`temperature=0`；模型版本优先显式锁定**，禁止 `latest` 类别名。
   供应商不提供带版本号快照时（如 DeepSeek 公开 API 只有 `deepseek-chat` 这类会漂移的别名），**必须从 API 响应里取回实际的 `model` 字段并持久化**——配置里写的名字不算数，响应返回的才算。
   *为什么*：铁律的目的是评分可复现、可审计。供应商静默升级模型会让历史评分失去解释力，而 PIPL 的说明权要求你能回答"这条评分是哪个版本打的"。锁不住版本时，至少要记得住版本。
6. **企微回调先落库再处理**：只推一次、5 秒无响应即丢弃。回调接口只做签名校验 + 落库 + 返回 200。
7. **`langgraph >= 1.0.10`**（GHSA-g48c-2wqr-h844）。

### 合规红线

- **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。审计断言：`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。
- **禁止人脸/表情分析**（《人脸识别技术应用安全管理办法》2025-06-01 施行）。声学情绪信号（语速/停顿/静默）只展示给面试官，不进 `criterion_score`。
- **AI 生成的 JD、拒信、邀约须带标识**（《AI 生成合成内容标识办法》2025-09-01 施行）。
- **模型全部走境内**，简历数据不出境。
- **绝不用历史录用结果做监督信号**（Amazon 2018 教训），只用显式岗位能力 rubric。
- 候选人入口一律用一次性邀请链接，避免被认定"向境内公众提供"。
- 主观描述（"沟通能力强"）不得进入硬门槛规则，只能作为软技能关键词。

### 部署约束

1. **路径前缀就绪**：FastAPI `root_path=/hr/recruit-agent`，前端资源与接口调用**一律相对路径**，禁止硬编码 `/static/…` `/api/…`。验收标准是挂到任意子路径下都能正常工作，且有测试覆盖。
2. **过渡端口 8095**，登记技术债，触发条件 = 统一门户网关上线即迁移。
3. **鉴权中间件留空壳接入点**，签名对齐未来企微 OAuth SSO；将来只换实现不换调用方。
4. **目标服务器是 Windows，没有 Docker**。部署形态 = Python venv + Windows 计划任务（SYSTEM 账户 + AtStartup + 失败重启 3 次）+ 防火墙规则 + scp 推送。不要引入容器。
5. **M2 起处理真实简历前**，必须具备可识别到人的登录 + 简历访问留痕（PIPL 要求"谁在什么时候看了谁的简历"可查）。共享口令不满足。

---

## 立项动机（供 reviewer 校准注意力）

2026-08-18 的现网取证确认：SQLite 事务归属冲突在修复前的 `.51` 现网实际触发过两次（`1cabfb91` 的 2026-08-12 09:49:09、`41909b40` 的 2026-08-10 07:16:00），表现为**幂等记录已提交而 `outbox` 缺行**——那两轮的回复永久丢失，用户侧只看到"问了一句没回应"，会当成网络问题重试，不会报障。

**这两次是靠 SSH 进 `data/demo.db` 逐 thread 手工比对数据库不变式反推出来的，不是靠日志查出来的。** 现网不存在任何日志文件：计划任务以 SYSTEM 账户直接执行 `uvicorn.exe`，stdout/stderr 没有重定向也没有控制台承接，进程写出的每一行都进了虚空。同一份修复给 `app/storage/idempotency.py` 加的 `logger.error("rollback failed …")` 兜底告警，在现网同样什么都不会留下。

本计划要让这类证据默认落盘，把"只有在有人想到去查的时候才会发现"变成"出事当场就有可查的痕迹"。**Task 6 的 `test_idempotency_rollback_alert_reaches_the_persistent_log` 是这条动机的直接测试化身**，reviewer 请重点看它。

---

## 范围边界（与并行变更划清）

| 事项 | 归属 | 说明 |
|---|---|---|
| 进程启停、未捕获异常、请求级日志 | **本包** | 建立基础设施 |
| 日志落盘 / 轮转 / 留存 / 脱敏 | **本包** | 建立基础设施 |
| 逐轮埋点（逐轮时间戳、单轮延迟） | `m1-intake-quality-fixes` 的 `intake-turn-observability` | **不在本包**，由并行任务负责 |
| `effect_log` ↔ 业务表行数不变式的自动检查与告警 | **后续变更** | 见下方说明 |
| 简历访问留痕、业务审计事件、hash-chain | `ai-audit-trail-and-outbound-gate` | 不在本包 |
| 登录与鉴权 | 独立阻塞项 | `app/middleware/auth.py` 空壳不动 |

**两边都会碰 logging 初始化——本包负责建立基础设施，业务埋点复用它，不要各建一套。** `intake-turn-observability` 的逐轮埋点应当直接 `logger = logging.getLogger(__name__)` 然后正常 `logger.info(...)`：本包的 dictConfig 已经接管了根 logger，请求标识由 Filter 自动注入，埋点侧**不需要**也**不应该**再调一次 `dictConfig`、再挂一个 handler，或自己配置文件路径。若逐轮埋点需要输出画像内容相关字段，必须走本包 Task 4 的 `loggable_summary()`，不得整体记录业务对象。

**关于不变式自动告警**：opener 曾把它列入本包范围，但变更包的 `proposal.md` 明确将其列为 Non-goal（理由：企微通道尚未接入，且没有日志先落盘告警也没有可引用的证据），`specs/runtime-observability/spec.md` 也没有对应 Requirement。2026-08-19 与 Shao Peishen 确认**按 spec 走，本次不做**，留作后续变更。本包为它预留的落点是：`logging_config.setup_logging()` 已经统一了日志通道，不变式自检届时只需 `logging.getLogger(...).error(...)`，告警就自动带请求标识、自动落盘、自动受脱敏与留存约束——先后顺序本来就成立。**实现者不要在本计划范围内提前写这部分。**

---

## File Structure

**新建 `app/observability/` 包**（横切关注点，与 `app/middleware/` 平级；不放进 `app/web/` 是因为日志初始化发生在 web 层之前）：

| 文件 | 职责 | 依赖 |
|---|---|---|
| `app/observability/__init__.py` | 仅包文档字符串。**刻意不做 re-export**，避免 `__init__` 变成所有子模块都要回头改一笔的公共依赖点 | — |
| `app/observability/handlers.py` | `DailyRotatingFileHandler`（按天 + 大小兜底）、`purge_expired_logs()` | 纯标准库 |
| `app/observability/context.py` | `request_id_var` ContextVar、`RequestIdFilter` | 纯标准库 |
| `app/observability/redaction.py` | `loggable_summary()` 主防线、`RedactionFilter` + `RedactingFormatter` 兜底 | 纯标准库 |
| `app/observability/logging_config.py` | `setup_logging()` 组装 dictConfig、可写性探测、`LoggingStatus` | 上面三个 |
| `app/observability/middleware.py` | `RequestIdMiddleware`（纯 ASGI）、`unhandled_exception_handler` | `context` + starlette |

**修改既有文件**：`app/config.py`（4 个日志配置项）、`app/main.py`（导入期调 `setup_logging`）、`app/web/server.py`（挂中间件 + 异常处理器 + `/health`）、`deploy-server.ps1`（建日志目录 + 验可写）、`sync-to-server.sh`（`logs` 进 `EXCLUDE_NAMES`）、`.env.example`、`05-发布运行手册.md`。

**新建测试**：`tests/conftest.py`、`tests/test_logging_setup.py`、`tests/test_request_id.py`、`tests/test_log_redaction.py`、`tests/test_deploy_logging_wiring.py`。

**依赖顺序**（决定了 Task 顺序）：`handlers` / `context` / `redaction` 三个叶子模块彼此独立 → `logging_config` 组装它们 → `middleware` 用 `context` → `server.py` 用 `logging_config` + `middleware`。**每个文件只在一个 Task 里写一次、之后不再改**，唯一例外是 `logging_config.py` 在 Task 4 接入脱敏时改 4 行（Task 4 给出精确前后对照）。

---

## Spec Requirement → Task 覆盖表

| spec Requirement | Scenario | Task | 测试 |
|---|---|---|---|
| 运行日志必须持久化且容量有界 | 无控制台环境下启动 | Task 3 | `test_logs_land_in_file_without_any_console` |
| 同上 | 日志量超过配置上限 | Task 1 | `test_rotation_keeps_old_unit_and_loses_no_line`、`test_same_day_rotation_does_not_overwrite_previous_unit` |
| 同上 | 日志写入位置不可用 | Task 3 | `test_unwritable_log_dir_degrades_instead_of_crashing`、`test_health_endpoint_reports_logging_state_under_root_path`（Task 5） |
| 一次请求的日志可由单一标识串联 | 正常请求 | Task 5 | `test_all_lines_of_one_request_share_the_id_and_it_is_returned` |
| 同上 | 并发请求互不串扰 | Task 5 | `test_overlapping_requests_never_cross_talk[sync]` / `[async]` |
| 服务端错误必须留下可定位的证据 | 未捕获异常导致服务端错误 | Task 6 | `test_server_error_leaves_request_id_exception_type_and_stack` |
| 同上 | 已被记录为错误的内部失败 | Task 6 | `test_idempotency_rollback_alert_reaches_the_persistent_log` |
| 日志内容受个人信息脱敏约束 | 受控字段随对象被整体记录 | Task 4 | `test_whole_object_logged_leaves_no_plaintext`、`test_exception_stack_carrying_content_is_redacted` |
| 同上 | 新增字段的默认归属 | Task 4 | `test_newly_added_undeclared_field_defaults_to_controlled` |
| 日志留存期有上限且与业务数据分离 | 日志超过留存期 | Task 1 | `test_retention_purge_removes_only_expired_units` |
| 同上 | 需要长期举证的记录 | Task 7 | 文档边界（`05-发布运行手册.md` + 模块 docstring），无代码断言——这条 Scenario 是禁止性约束，由 review 守 |

**tasks.md 章节映射**：Task 1 ← 1.3/1.4/1.5/1.6/3.5/3.6 · Task 2 ← 2.2（上下文部分）· Task 3 ← 1.1/1.2/1.7/1.8/1.9 · Task 4 ← 3.1/3.2/3.3/3.4 · Task 5 ← 2.1/2.2/2.3/2.4 · Task 6 ← 2.5/2.6/2.7/2.8 · Task 7 ← 3.7/3.8/4.1～4.6

---

## Reviewer Checklist（每个 Task 都过一遍）

1. **铁律 1**：本变更不新增、不删除、不修改任何 `effect_*` 节点，也不碰事务边界。reviewer 确认 `app/storage/idempotency.py`、`app/graph/nodes.py`、`app/graph/build.py` **一行未改**。唯一相关的是 Task 6 给 `idempotency.py` 那条既有 `logger.error` 加了落盘守卫测试——只加测试，不改被测代码。
2. **铁律 2**：不新增 L3/L4 节点，`compute_*` / `effect_*` 命名不受影响。
3. **铁律 3/4/5**：本变更不触碰 AI 评分持久化、`evidence_ref`、模型版本锁定，应为 N/A。reviewer 确认没有意外改动 `app/llm/gateway.py`、`app/agents/`。
4. **铁律 6**：不涉及企微回调。本包**不做**主动告警（见「范围边界」），reviewer 若看到往企微发消息的代码，判为越界。
5. **铁律 7**：`requirements.txt` 本次不改，`langgraph==1.0.10` 保持不变。reviewer 确认没有新增依赖——全部用标准库。
6. **合规红线 · 个人信息**：这是本变更最相关的一条。日志是个人信息的**第二份副本**，reviewer 必须确认：受控内容字段不以明文进日志（Task 4）；`logs/` 不在 `sync-to-server.sh` 的同步白名单里（Task 7）；留存期上限存在且生效（Task 1）。**日志不出境**（只落 `.51` 本地文件系统），不涉及人脸/表情数据，不涉及自动化决策。
7. **合规红线 · 审计与运行日志分离**：运行日志会被轮转与留存期清掉，**不得**被当作合规举证依据。reviewer 若看到有人把决策留痕/外发审批写进运行日志，判为违背 spec 最后一条 Scenario。
8. **部署约束 1**：中间件与 `/health` 都不得硬编码路径前缀。`test_app_works_when_mounted_at_arbitrary_subpath` 与 `test_health_endpoint_moves_with_the_mount_prefix` 必须通过。
9. **部署约束 3**：`app/middleware/auth.py` 一行不改。`RequestIdMiddleware` 必须挂在 `AuthMiddleware` **外面**（后 `add_middleware` 的更靠外），且 `request.state.auth` 的读取方式不变。
10. **部署约束 4**：Windows + 无 Docker。不引入常驻日志代理、不引入 ELK/Loki/Sentry。**单进程前提**：轮转方案依赖 uvicorn 无 `--workers`；reviewer 确认 Task 1 的代码注释与 Task 7 的运维文档都写明了「加 `--workers` 前必须先换轮转方案」。
11. **Windows 句柄**：测试里 `setup_logging()` 会往 `tmp_path` 挂文件 handler。**Windows 删不掉仍有打开句柄的文件**，不释放会让 pytest 的 tmp 清理在 `windows-latest` runner 上失败（macOS/Linux 永远不现形）。reviewer 确认 `tests/conftest.py` 的 autouse fixture 存在且没被删。

---

### Task 1: 轮转与留存的文件系统基座

对应 tasks.md 1.3 / 1.4 / 1.5 / 1.6 / 3.5 / 3.6。纯标准库单元，不依赖本包其它模块，可独立测试。

**Files:**
- Create: `app/observability/__init__.py`
- Create: `app/observability/handlers.py`
- Create: `tests/test_log_rotation.py`
- Modify: `app/config.py`（在 `root_path` 之后、`validate_model_version` 之前插入 4 个配置项）

**Interfaces:**
- Consumes: 无（本 Task 是叶子）
- Produces:
  - `purge_expired_logs(log_dir: pathlib.Path, retention_days: int) -> list[pathlib.Path]`
  - `DailyRotatingFileHandler(filename: str, *, retention_days: int = 30, max_bytes: int = 50*1024*1024, encoding: str = "utf-8")`
  - `Settings.log_dir: str`、`Settings.log_level: str`、`Settings.log_retention_days: int`、`Settings.log_max_bytes: int`

- [ ] **Step 1: 建包目录与 `__init__.py`**

```bash
mkdir -p app/observability
```

`app/observability/__init__.py`：

```python
"""运行时可观测性：日志落盘与轮转、请求标识串联、错误证据、个人信息脱敏。

刻意不做 re-export：各模块按全路径导入（app.observability.logging_config 等），
避免 __init__ 变成一个所有子模块都要回头改一笔的公共依赖点。
"""
```

- [ ] **Step 2: 写失败测试**

创建 `tests/test_log_rotation.py`：

```python
import logging
import os
import time

from app.observability.handlers import DailyRotatingFileHandler, purge_expired_logs


def test_rotation_keeps_old_unit_and_loses_no_line(tmp_path):
    """大小上界触发轮转：旧单元保留为独立文件、新单元继续记录、无行丢失。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    handler = DailyRotatingFileHandler(
        str(log_dir / "app.log"), retention_days=30, max_bytes=2048
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("rotation-probe")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    total = 200
    for i in range(total):
        logger.info("line-%03d %s", i, "x" * 60)
    handler.close()

    units = sorted(log_dir.glob("app.log*"))
    assert len(units) > 1, f"大小上界没有触发轮转，只有 {units}"

    seen = []
    for unit in units:
        seen.extend(
            line for line in unit.read_text(encoding="utf-8").splitlines() if line.strip()
        )
    assert len(seen) == total, f"轮转丢了行：期望 {total} 实得 {len(seen)}"
    assert {f"line-{i:03d}" for i in range(total)} == {line.split()[0] for line in seen}


def test_same_day_rotation_does_not_overwrite_previous_unit(tmp_path):
    """同一天内二次轮转必须落到带序号的新名字，不能覆盖当天已有单元。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    handler = DailyRotatingFileHandler(
        str(log_dir / "app.log"), retention_days=30, max_bytes=512
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("same-day-probe")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    for i in range(60):
        logger.info("payload-%02d %s", i, "y" * 40)
    handler.close()

    rotated = sorted(p for p in log_dir.glob("app.log.*"))
    assert len(rotated) >= 2, f"同日多次轮转没有产出多个单元：{rotated}"
    assert any(p.name.endswith(".1") for p in rotated), f"没有带序号的单元：{rotated}"


def test_retention_purge_removes_only_expired_units(tmp_path):
    """超期单元被清理、未超期的保留；重复执行结果一致（幂等）。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    old = log_dir / "app.log.2026-06-01"
    fresh = log_dir / "app.log.2026-08-18"
    current = log_dir / "app.log"
    for p in (old, fresh, current):
        p.write_text("x\n", encoding="utf-8")

    ancient = time.time() - 40 * 86400
    os.utime(old, (ancient, ancient))

    removed = purge_expired_logs(log_dir, retention_days=30)
    assert removed == [old]
    assert not old.exists()
    assert fresh.exists()
    assert current.exists(), "当前正在写入的 app.log 不带日期后缀，不应被清理"

    assert purge_expired_logs(log_dir, retention_days=30) == []
```

- [ ] **Step 3: 跑测试确认失败**

Run: `venv/bin/python -m pytest tests/test_log_rotation.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.observability.handlers'`

- [ ] **Step 4: 实现 `app/observability/handlers.py`**

```python
from __future__ import annotations

import os
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

LOG_SUFFIX_GLOB = "*.log.*"


def purge_expired_logs(log_dir: Path, retention_days: int) -> list[Path]:
    """按文件 mtime 清理超过留存期的历史日志单元，返回被删掉的路径。

    幂等：判据是「文件时间早于阈值即删」，重复执行结果一致；文件已被别处
    删掉时吞掉 FileNotFoundError，不重复报错。

    用 mtime 而不是 TimedRotatingFileHandler 自带的 backupCount：本 handler
    在同一天内因大小上界二次轮转时会产出 app.log.2026-08-19.1 这种带序号的
    名字，stdlib 的 getFilesToDelete() 用 `^\\d{4}-\\d{2}-\\d{2}$` 匹配后缀，
    认不出带序号的单元，会把它们永远留下。所以 backupCount 置 0、自己算。
    """
    if retention_days <= 0:
        return []
    cutoff = time.time() - retention_days * 86400
    removed: list[Path] = []
    for path in sorted(log_dir.glob(LOG_SUFFIX_GLOB)):
        if not path.is_file():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed.append(path)
        except FileNotFoundError:
            continue
    return removed


class DailyRotatingFileHandler(TimedRotatingFileHandler):
    """按天轮转 + 单文件大小兜底 + 按 mtime 清理留存期。

    ⚠️ 单进程前提：计划任务里的 uvicorn 没有 --workers，全程一个进程。
    Windows 轮转要重命名当前文件，若有第二个进程持有该文件句柄会失败。
    **给 uvicorn 加 --workers 之前必须先更换轮转方案**（见 05-发布运行手册.md）。
    """

    def __init__(
        self,
        filename: str,
        *,
        retention_days: int = 30,
        max_bytes: int = 50 * 1024 * 1024,
        encoding: str = "utf-8",
    ) -> None:
        # backupCount=0：删除交给 purge_expired_logs()，理由见其 docstring。
        super().__init__(
            filename, when="midnight", backupCount=0, encoding=encoding, delay=False
        )
        self.retention_days = retention_days
        self.max_bytes = max_bytes

    def shouldRollover(self, record) -> bool:
        if super().shouldRollover(record):
            return True
        if self.max_bytes <= 0:
            return False
        if self.stream is None:
            self.stream = self._open()
        try:
            size = self.stream.tell()
        except (OSError, ValueError):
            return False
        msg = self.format(record) + self.terminator
        return size + len(msg.encode(self.encoding or "utf-8")) >= self.max_bytes

    def rotation_filename(self, default_name: str) -> str:
        """同一天内的第二次（大小触发）轮转要落到新名字，不能覆盖当天已有单元。

        stdlib 的 doRollover 在算出 dfn 后有一句 `if os.path.exists(dfn): return`
        （「Already rolled over」），直接放弃本次轮转 —— 那会让当天的日志无上界地
        继续写下去，正好违反「日志量超过配置上限」场景。这里保证返回的名字总是
        未被占用的，那条早退分支就永远走不到，也不会删掉上一段。
        """
        candidate = default_name
        index = 0
        while os.path.exists(candidate):
            index += 1
            candidate = f"{default_name}.{index}"
        return candidate

    def doRollover(self) -> None:
        super().doRollover()
        purge_expired_logs(Path(self.baseFilename).parent, self.retention_days)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `venv/bin/python -m pytest tests/test_log_rotation.py -q`
Expected: PASS — `3 passed`

- [ ] **Step 6: 加配置项**

在 `app/config.py` 的 `root_path: str = "/hr/recruit-agent"` 之后、`def validate_model_version` 之前插入：

```python

    # 日志配置全部带默认值，零配置即生效（design 决策 6）。.51 的 .env 是
    # 服务器上独立维护、不随代码同步的生产凭据文件；若日志功能依赖 .env 新增
    # 字段，"推代码"与"改 .env"就成了两个必须同时做对的步骤，漏一个就静默地
    # 没有日志——正是这次要根治的失败模式。
    log_dir: str = "logs"
    log_level: str = "INFO"
    log_retention_days: int = 30
    log_max_bytes: int = 50 * 1024 * 1024
```

- [ ] **Step 7: 跑全量确认没打破既有用例**

Run: `venv/bin/python -m pytest -q`
Expected: PASS — `94 passed`（基线 91 + 本 Task 新增 3）

- [ ] **Step 8: 提交**

```bash
git add app/observability/__init__.py app/observability/handlers.py tests/test_log_rotation.py app/config.py
git commit -m "feat(observability): 按天轮转 + 大小兜底 + 留存期清理的日志 handler"
```

---

### Task 2: 请求标识的上下文载体

对应 tasks.md 2.2 的上下文部分。纯标准库单元，不依赖 web 框架，可独立测试。

**Files:**
- Create: `app/observability/context.py`
- Create: `tests/test_request_context.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `REQUEST_ID_HEADER: str = "X-Request-ID"`、`UNSET_REQUEST_ID: str = "-"`
  - `request_id_var: contextvars.ContextVar[str]`
  - `current_request_id() -> str`
  - `RequestIdFilter`（`logging.Filter` 子类，给 record 注入 `request_id` 属性；**已有该属性时不覆盖**——Task 6 的异常处理器靠 `extra=` 显式传入，必须优先于 contextvar 回填）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_request_context.py`：

```python
import asyncio
import logging

from app.observability.context import (
    UNSET_REQUEST_ID,
    RequestIdFilter,
    current_request_id,
    request_id_var,
)


def _record() -> logging.LogRecord:
    return logging.LogRecord("probe", logging.INFO, __file__, 1, "msg", None, None)


def test_filter_injects_current_request_id():
    token = request_id_var.set("abc123")
    try:
        record = _record()
        assert RequestIdFilter().filter(record) is True
        assert record.request_id == "abc123"
    finally:
        request_id_var.reset(token)


def test_filter_defaults_when_outside_any_request():
    record = _record()
    RequestIdFilter().filter(record)
    assert record.request_id == UNSET_REQUEST_ID
    assert current_request_id() == UNSET_REQUEST_ID


def test_filter_never_overwrites_an_explicitly_supplied_id():
    """Task 6 的异常处理器用 extra={"request_id": ...} 显式传值——那时 contextvar
    已被中间件的 finally 复位，若这里覆盖就会把错误日志的标识抹成 '-'。"""
    token = request_id_var.set("from-contextvar")
    try:
        record = _record()
        record.request_id = "from-extra"
        RequestIdFilter().filter(record)
        assert record.request_id == "from-extra"
    finally:
        request_id_var.reset(token)


def test_contextvar_isolates_concurrent_async_tasks():
    """异步路由全部跑在同一个事件循环线程上——thread-local 会在这里串号。"""

    async def one(value: str) -> str:
        request_id_var.set(value)
        await asyncio.sleep(0.01)
        return current_request_id()

    async def drive():
        return await asyncio.gather(*(one(f"id-{i}") for i in range(8)))

    assert asyncio.run(drive()) == [f"id-{i}" for i in range(8)]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest tests/test_request_context.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.observability.context'`

- [ ] **Step 3: 实现 `app/observability/context.py`**

```python
from __future__ import annotations

import contextvars
import logging

REQUEST_ID_HEADER = "X-Request-ID"
UNSET_REQUEST_ID = "-"

# contextvars 而不是 thread-local：异步路由全部跑在同一个事件循环线程上，
# thread-local 会让并发的异步请求互相覆盖标识（spec「并发请求互不串扰」）。
# contextvars 在 asyncio 任务与 Starlette 派发同步路由用的 run_in_threadpool
# 两侧都能正确复制上下文。
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=UNSET_REQUEST_ID
)


def current_request_id() -> str:
    return request_id_var.get()


class RequestIdFilter(logging.Filter):
    """给每条 record 注入 request_id，使格式串可以无条件引用 %(request_id)s。

    挂在 handler 而不是 logger 上：handler 能看到所有路由到它的 record，
    包括 uvicorn 与第三方库的——业务代码里已有的 logger.* 调用点一行都不用改。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get()
        return True
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest tests/test_request_context.py -q`
Expected: PASS — `4 passed`

- [ ] **Step 5: 提交**

```bash
git add app/observability/context.py tests/test_request_context.py
git commit -m "feat(observability): contextvars 承载请求标识 + logging Filter 注入"
```

---

### Task 3: 日志系统组装、进程接线与降级暴露

对应 tasks.md 1.1 / 1.2 / 1.7 / 1.8 / 1.9。把 Task 1、2 的零件装成一处 `dictConfig`，并接进进程启动路径。

**Files:**
- Create: `app/observability/logging_config.py`
- Create: `tests/conftest.py`
- Create: `tests/test_logging_setup.py`
- Modify: `app/main.py`

**Interfaces:**
- Consumes: `DailyRotatingFileHandler` / `purge_expired_logs`（Task 1）、`RequestIdFilter`（Task 2）
- Produces:
  - `setup_logging(*, log_dir: str, level: str = "INFO", retention_days: int = 30, max_bytes: int = 50*1024*1024) -> LoggingStatus`
  - `logging_status() -> LoggingStatus`
  - `LoggingStatus`（dataclass：`configured` / `degraded` / `reason` / `log_file` / `handlers`，含 `as_dict()`）
  - `UVICORN_LOGGERS: tuple[str, ...]`、`LOG_FILENAME: str`、`LOG_FORMAT: str`

- [ ] **Step 1: 写 conftest（Windows 句柄释放，先建后用）**

创建 `tests/conftest.py`：

```python
import logging

import pytest

from app.observability.logging_config import UVICORN_LOGGERS


@pytest.fixture(autouse=True)
def _release_log_file_handles():
    """每个用例结束后关闭并摘掉指向文件的 handler。

    setup_logging() 会把文件 handler 挂到 root 与 uvicorn 三个 logger 上，测试
    里指向的是 tmp_path。**Windows 删不掉仍有打开句柄的文件**，句柄不释放会让
    pytest 的 tmp 目录清理失败——这个故障在 macOS/Linux 上永远不现形，只会在
    CI 的 windows-latest runner 上炸（跟 SQLite 事务冲突那次是同一类教训）。
    """
    yield
    targets = [logging.getLogger()] + [logging.getLogger(n) for n in UVICORN_LOGGERS]
    for logger in targets:
        for handler in list(logger.handlers):
            if isinstance(handler, logging.FileHandler):
                handler.close()
                logger.removeHandler(handler)
```

- [ ] **Step 2: 写失败测试**

创建 `tests/test_logging_setup.py`：

```python
import logging
import os
import stat
import sys

import pytest

from app.observability.logging_config import logging_status, setup_logging


def test_logs_land_in_file_without_any_console(tmp_path, monkeypatch):
    """无控制台环境（计划任务以 SYSTEM 身份拉起，stdout 无处可去）下，
    日志仍必须落到持久化位置。"""
    monkeypatch.setattr(sys, "stdout", open(os.devnull, "w", encoding="utf-8"))
    log_dir = tmp_path / "logs"

    status = setup_logging(log_dir=str(log_dir), level="INFO", retention_days=30)

    logging.getLogger("app.storage.idempotency").error("rollback failed while cleaning up")
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).info("hello from %s", name)
    logging.shutdown()

    log_file = log_dir / "app.log"
    assert status.degraded is False
    assert log_file.exists()
    text = log_file.read_text(encoding="utf-8")
    assert "rollback failed while cleaning up" in text
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        assert f"hello from {name}" in text, f"{name} 的输出没落进文件"


@pytest.mark.skipif(os.name == "nt", reason="Windows 上 chmod 不阻止 SYSTEM/管理员写入")
def test_unwritable_log_dir_degrades_instead_of_crashing(tmp_path):
    """日志目录不可写时不崩溃，且降级事实必须可被察觉。"""
    parent = tmp_path / "readonly"
    parent.mkdir()
    parent.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        status = setup_logging(log_dir=str(parent / "logs"), level="INFO")
        assert status.degraded is True
        assert status.reason
        assert status.log_file is None
        assert logging_status().degraded is True
        logging.getLogger("app").info("业务仍然可以打日志，不抛异常")
    finally:
        parent.chmod(stat.S_IRWXU)
```

- [ ] **Step 3: 跑测试确认失败**

Run: `venv/bin/python -m pytest tests/test_logging_setup.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.observability.logging_config'`（conftest 的 import 先炸，是预期的）

- [ ] **Step 4: 实现 `app/observability/logging_config.py`**

⚠️ 本 Task 先不接脱敏（`redaction` 模块尚不存在），Task 4 会精确改这里的 4 行。

```python
from __future__ import annotations

import logging
import logging.config
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app.observability.context import RequestIdFilter
from app.observability.handlers import DailyRotatingFileHandler, purge_expired_logs

LOG_FILENAME = "app.log"
LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s"

# uvicorn 的三个 logger 必须一并接管：--log-config 只管得到它们，管不到
# app.storage.idempotency 那条 logger.error——而这次事故要救的恰恰是应用侧。
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


@dataclass
class LoggingStatus:
    """日志子系统的当前状态，供健康检查端点读取。"""

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


def _probe_writable(log_dir: Path) -> str | None:
    """返回 None 表示可写，否则返回不可写的原因（人类可读）。"""
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        probe = log_dir / ".write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def setup_logging(
    *,
    log_dir: str,
    level: str = "INFO",
    retention_days: int = 30,
    max_bytes: int = 50 * 1024 * 1024,
) -> LoggingStatus:
    """进程启动时调用一次，统一配置根 logger、应用 logger 与 uvicorn 三个 logger。

    日志目录不可写时**不崩溃、不阻断业务功能**：退回只有 stdout 的配置，并把
    降级事实记进 LoggingStatus 供 /health 暴露——在一个没有控制台、日志本身
    又坏了的进程里，健康检查端点是唯一还能对外说话的通道。
    """
    global _status

    directory = Path(log_dir).expanduser()
    reason = _probe_writable(directory)
    log_path = directory / LOG_FILENAME

    handlers: dict[str, dict] = {
        "console": {
            "class": "logging.StreamHandler",
            "stream": sys.stdout,
            "formatter": "standard",
            "filters": ["request_id"],
            "level": level,
        }
    }
    if reason is None:
        handlers["file"] = {
            "()": DailyRotatingFileHandler,
            "filename": str(log_path),
            "retention_days": retention_days,
            "max_bytes": max_bytes,
            "encoding": "utf-8",
            "formatter": "standard",
            "filters": ["request_id"],
            "level": level,
        }

    handler_names = list(handlers)
    logging.config.dictConfig(
        {
            "version": 1,
            # uvicorn 自己的 dictConfig 也是 False；置 True 会把先于本次配置
            # 创建出来的模块级 logger（如 app.storage.idempotency）整个关掉。
            "disable_existing_loggers": False,
            "filters": {
                "request_id": {"()": RequestIdFilter},
            },
            "formatters": {"standard": {"format": LOG_FORMAT}},
            "handlers": handlers,
            "root": {"level": level, "handlers": handler_names},
            "loggers": {
                name: {"level": level, "handlers": handler_names, "propagate": False}
                for name in UVICORN_LOGGERS
            },
        }
    )

    if reason is None:
        purge_expired_logs(directory, retention_days)

    _status = LoggingStatus(
        configured=True,
        degraded=reason is not None,
        reason=reason,
        log_file=str(log_path) if reason is None else None,
        handlers=handler_names,
    )

    if _status.degraded:
        # 这一条只能落到 stdout（文件通道正是坏掉的那个），但它必须被记录：
        # spec 要求 MUST NOT 静默降级为「什么都不记录」。
        logging.getLogger(__name__).error(
            "日志文件通道不可用，已降级为仅 stdout：目录=%s 原因=%s。"
            "业务功能不受影响，但排障证据不会落盘——请检查该目录的存在性与写权限",
            directory,
            reason,
        )
    return _status
```

- [ ] **Step 5: 跑测试确认通过**

Run: `venv/bin/python -m pytest tests/test_logging_setup.py -q`
Expected: PASS — `2 passed`

- [ ] **Step 6: 接进进程启动路径**

修改 `app/main.py` 顶部——在 `from app.llm.gateway import LLMGateway` 之后加一行 import，并在 `settings = get_settings()` 之后插入调用：

```python
from app.config import get_settings
from app.llm.gateway import LLMGateway
from app.observability.logging_config import setup_logging
from app.web.server import create_app

settings = get_settings()

# 导入期、早于 create_app：uvicorn 在 Config.__init__ 里先 configure_logging()、
# 之后才 load() 导入本模块，所以这里的 dictConfig 一定后手生效、覆盖 uvicorn 的默认配置。
setup_logging(
    log_dir=settings.log_dir,
    level=settings.log_level,
    retention_days=settings.log_retention_days,
    max_bytes=settings.log_max_bytes,
)
```

- [ ] **Step 7: 跑全量**

Run: `venv/bin/python -m pytest -q`
Expected: PASS — `100 passed`（91 + Task1 的 3 + Task2 的 4 + 本 Task 的 2）

- [ ] **Step 8: 提交**

```bash
git add app/observability/logging_config.py app/main.py tests/conftest.py tests/test_logging_setup.py
git commit -m "feat(observability): 一处 dictConfig 统一接管应用与 uvicorn logger，不可写时降级不崩溃"
```

---

### Task 4: 个人信息脱敏（主防线 + 兜底两层）

对应 tasks.md 3.1 / 3.2 / 3.3 / 3.4。**合规红线相关，reviewer 重点看这一节。**

**Files:**
- Create: `app/observability/redaction.py`
- Create: `tests/test_log_redaction.py`
- Modify: `app/observability/logging_config.py`（4 处，见 Step 5）

**Interfaces:**
- Consumes: `setup_logging`（Task 3）
- Produces:
  - `NON_CONTENT_KEYS: frozenset[str]`、`RISKY_KEYS: tuple[str, ...]`、`REDACTED: str = "<redacted>"`
  - `content_digest(value) -> str`（16 位短哈希，非还原）
  - `loggable_summary(obj: Mapping[str, Any], *, known_fields: frozenset[str] | None = None) -> dict`
  - `RedactionFilter`（`logging.Filter`，扫 `record.getMessage()`）
  - `RedactingFormatter`（`logging.Formatter`，扫**格式化后**的全文，覆盖异常堆栈）

**为什么要两个类**：`logging.Filter` 只看得到 `record.getMessage()`，**异常堆栈不在里面**——`exc_text` 是 Formatter 阶段才生成的。堆栈里会出现局部变量 repr（那一帧的 `profile_dict = {...}`）以及 `ValidationError` 把原始输入回显进 `str(exc)` 的情况。Filter 负责替换并**发出告警**（它能拿到 `record.name` / `pathname` / `lineno`，知道是谁绕过了主防线），Formatter 负责**兜住 Filter 看不见的那部分文本**。少任何一个都有真实泄漏路径。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_log_redaction.py`：

```python
import json
import logging

import pytest

from app.observability.logging_config import setup_logging
from app.observability.redaction import NON_CONTENT_KEYS, loggable_summary
from app.schemas.job_profile import JobProfile

SECRET = "负责 AUTOSAR CP 底层通信栈开发，需精通 CAN FD 与 UDS 诊断"
PROFILE = {
    "job_id": "J-REDACT",
    "version": 3,
    "job_title": "嵌入式软件工程师（机密岗位名）",
    "department": "电子电气研发部",
    "responsibilities": SECRET,
    "must_have_skills": ["AUTOSAR", "CAN FD"],
}


@pytest.fixture
def log_file(tmp_path):
    path = tmp_path / "logs"
    setup_logging(log_dir=str(path), level="DEBUG", retention_days=30)
    yield path / "app.log"
    logging.shutdown()


def _read(log_file):
    logging.shutdown()
    return log_file.read_text(encoding="utf-8")


def test_whole_object_logged_leaves_no_plaintext(log_file):
    """主防线被绕过时的报警器：有人把画像对象整体塞进日志。"""
    logging.getLogger("app.web.server").info("profile=%s", PROFILE)
    text = _read(log_file)

    assert SECRET not in text, "受控内容字段以明文进了日志"
    assert "机密岗位名" not in text
    assert "<redacted>" in text
    assert "J-REDACT" in text, "非内容字段应保留，否则日志失去排障价值"


def test_bypass_is_visible_not_silently_fixed(log_file):
    logging.getLogger("app.web.server").info("profile=%s", PROFILE)
    text = _read(log_file)
    assert "脱敏兜底命中" in text, "兜底替换必须额外留一条告警，使绕过行为可见"
    assert "loggable_summary" in text, "告警要指出正确做法"


def test_exception_stack_carrying_content_is_redacted(log_file):
    """异常信息不经过 record.getMessage()，Filter 看不到——由 Formatter 补刀。"""
    try:
        raise ValueError(f"validation failed for {json.dumps(PROFILE, ensure_ascii=False)}")
    except ValueError:
        logging.getLogger("app.web.server").exception("confirm 失败")

    text = _read(log_file)
    assert SECRET not in text, "受控内容经异常信息泄漏了"
    assert "<redacted>" in text


def test_loggable_summary_emits_no_content_values():
    summary = loggable_summary(PROFILE, known_fields=frozenset(JobProfile.model_fields))
    rendered = json.dumps(summary, ensure_ascii=False)

    assert SECRET not in rendered
    assert "机密岗位名" not in rendered
    assert summary["job_id"] == "J-REDACT"
    assert summary["version"] == 3
    assert summary["field_count"] == len(PROFILE)
    assert summary["content_chars"] > 0
    assert len(summary["content_digest"]) == 16


def test_newly_added_undeclared_field_defaults_to_controlled():
    """新增字段未显式声明是否受控时，默认必须倾向于不泄露。"""
    leaked = "候选人张某某的手机号 13800138000"
    obj = {**PROFILE, "some_brand_new_field": leaked}

    summary = loggable_summary(obj, known_fields=frozenset(JobProfile.model_fields))
    rendered = json.dumps(summary, ensure_ascii=False)

    assert leaked not in rendered, "未声明的新字段以明文进了摘要"
    assert "some_brand_new_field" not in summary.get("field_names", []), (
        "未知字段名不应进白名单"
    )
    assert summary["unknown_field_count"] >= 1, "未知字段应被计数，使新增可被察觉"


def test_non_content_whitelist_holds_no_free_text_keys():
    """白名单只能放结构性标识，任何自由文本字段混进来都是回归。"""
    assert NON_CONTENT_KEYS == {"job_id", "thread_id", "version", "round_count", "status"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest tests/test_log_redaction.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.observability.redaction'`

- [ ] **Step 3: 实现 `app/observability/redaction.py`**

```python
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from typing import Any, Mapping

REDACTED = "<redacted>"

# 白名单：只有这些键是「非内容」字段，可以原样进日志。白名单之外一律不输出
# ——对应 spec「新增字段的默认归属」：未声明即受控。
NON_CONTENT_KEYS = frozenset({"job_id", "thread_id", "version", "round_count", "status"})

# 兜底 Filter 的高危键名。这层是探测性的、不是主防线：正则永远追不上业务
# 字段的增长速度。它的价值是「当有人绕过 loggable_summary 时留下痕迹」。
RISKY_KEYS = (
    "job_title",
    "department",
    "responsibilities",
    "requirements",
    "must_have_skills",
    "nice_to_have_skills",
    "profile_json",
    "profile_patch",
    "history_json",
    "resume_text",
    "candidate_name",
    "content",
    "message",
    "_jd_text",
)

_KEY_ALTERNATION = "|".join(re.escape(k) for k in RISKY_KEYS)
# 匹配 dict/JSON 两种渲染形态里的 "键: '值'" 或 "键": "值"，只吃掉值。
_RISKY_VALUE_RE = re.compile(
    r"(['\"](?:" + _KEY_ALTERNATION + r")['\"]\s*:\s*)"
    r"('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")"
)

_local = threading.local()


def content_digest(value: Any) -> str:
    """非还原性摘要：短哈希。用于「同一段内容是否变过」的排障，不可逆推原文。"""
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def loggable_summary(obj: Mapping[str, Any], *, known_fields: frozenset[str] | None = None) -> dict:
    """主防线：把业务对象压成只含非内容字段的摘要。

    白名单外的键**只贡献名字与统计量，不贡献取值**；且键名本身也要过一遍
    known_fields（画像 patch 是 LLM 自由生成的裸 dict，键名理论上也可能是
    模型幻觉出来的自由文本）。
    """
    summary: dict[str, Any] = {k: obj[k] for k in NON_CONTENT_KEYS if k in obj}
    content_keys = [k for k in obj if k not in NON_CONTENT_KEYS]
    summary["field_count"] = len(obj)
    if known_fields is not None:
        summary["field_names"] = sorted(k for k in content_keys if k in known_fields)
        summary["unknown_field_count"] = sum(1 for k in content_keys if k not in known_fields)
    else:
        summary["unknown_field_count"] = len(content_keys)
    summary["content_chars"] = sum(len(str(obj[k])) for k in content_keys)
    summary["content_digest"] = content_digest({k: obj[k] for k in content_keys})
    return summary


class RedactionFilter(logging.Filter):
    """兜底层：扫描最终 record，命中高危键名时把值替换成非还原形式。

    命中即额外记一条 WARNING，使「主防线被绕过」这件事可见而不是被悄悄修正。
    _local.busy 是重入护栏：那条 WARNING 自己也会流经本 Filter。
    """

    def __init__(self, name: str = "") -> None:
        super().__init__(name)
        self.hits = 0

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except Exception:
            return True

        redacted, count = _RISKY_VALUE_RE.subn(r"\1'" + REDACTED + "'", rendered)
        if count == 0:
            return True

        record.msg = redacted
        record.args = ()
        record.redacted_fields = count
        self.hits += count

        if not getattr(_local, "busy", False):
            _local.busy = True
            try:
                logging.getLogger("app.observability.redaction").warning(
                    "脱敏兜底命中 %d 处：logger=%s 位置=%s:%s。"
                    "主防线被绕过了——业务对象不应整体进日志，请改用 loggable_summary()",
                    count,
                    record.name,
                    record.pathname,
                    record.lineno,
                )
            finally:
                _local.busy = False
        return True


class RedactingFormatter(logging.Formatter):
    """异常堆栈不经过 record.getMessage()，Filter 看不到它。

    堆栈里会出现局部变量的 repr（例如 `profile_dict = {...}` 的那一帧），
    以及 `ValidationError` 把原始输入回显进 str(exc) 的情况——所以格式化
    之后再扫一遍最终文本，是 Filter 之外必须补的一刀。
    """

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        return _RISKY_VALUE_RE.sub(r"\1'" + REDACTED + "'", text)
```

- [ ] **Step 4: 跑测试——`loggable_summary` 的用例应已通过，日志文件类用例仍失败**

Run: `venv/bin/python -m pytest tests/test_log_redaction.py -q`
Expected: 3 passed, 3 failed —— `test_loggable_summary_emits_no_content_values`、`test_newly_added_undeclared_field_defaults_to_controlled`、`test_non_content_whitelist_holds_no_free_text_keys` 通过；三个走日志文件的用例仍失败（脱敏还没接进 `setup_logging`）。

- [ ] **Step 5: 把脱敏接进 `setup_logging`（`app/observability/logging_config.py` 改 4 处）**

改动 1 —— 在 `from app.observability.handlers import ...` 之后新增一行 import：

```python
from app.observability.redaction import RedactingFormatter, RedactionFilter
```

改动 2 —— `handlers["console"]` 的 filters：

```python
            "filters": ["request_id"],
```
改为
```python
            "filters": ["request_id", "redaction"],
```

改动 3 —— `handlers["file"]` 的 filters（同样一行，注意有**两处**同名的 `"filters"`，console 与 file 都要改）：

```python
            "filters": ["request_id", "redaction"],
```

改动 4 —— dictConfig 里的 `filters` 与 `formatters` 两段：

```python
            "filters": {
                "request_id": {"()": RequestIdFilter},
            },
            "formatters": {"standard": {"format": LOG_FORMAT}},
```
改为
```python
            "filters": {
                "request_id": {"()": RequestIdFilter},
                "redaction": {"()": RedactionFilter},
            },
            "formatters": {"standard": {"()": RedactingFormatter, "format": LOG_FORMAT}},
```

- [ ] **Step 6: 跑测试确认全部通过**

Run: `venv/bin/python -m pytest tests/test_log_redaction.py -q`
Expected: PASS — `6 passed`

- [ ] **Step 7: 跑全量**

Run: `venv/bin/python -m pytest -q`
Expected: PASS — `106 passed`

- [ ] **Step 8: 提交**

```bash
git add app/observability/redaction.py app/observability/logging_config.py tests/test_log_redaction.py
git commit -m "feat(observability): 日志脱敏两层——loggable_summary 主防线 + Filter/Formatter 兜底"
```

---

### Task 5: 请求标识中间件、响应头与 /health

对应 tasks.md 2.1 / 2.2 / 2.3 / 2.4，以及 1.7 的健康检查暴露部分。

**Files:**
- Create: `app/observability/middleware.py`（本 Task 只写 `RequestIdMiddleware`；`unhandled_exception_handler` 在 Task 6 追加到同一文件）
- Create: `tests/test_request_id.py`
- Create: `tests/test_health_endpoint.py`
- Modify: `app/web/server.py`

**Interfaces:**
- Consumes: `REQUEST_ID_HEADER` / `request_id_var`（Task 2）、`logging_status`（Task 3）
- Produces: `RequestIdMiddleware`（纯 ASGI，`__init__(self, app)`）；`GET {root_path}/health` → `{"status": "ok"|"degraded", "logging": {...}}`

**为什么必须是纯 ASGI 中间件、不能用 `BaseHTTPMiddleware`**：`BaseHTTPMiddleware` 把下游 app 放进另一个 anyio 任务里跑，`dispatch` 里设的 contextvar 无法可靠传到 endpoint。纯 ASGI 中间件在同一个协程里 `await` 下游，传播是确定的——同步路由经 `run_in_threadpool`（内部 `anyio.to_thread.run_sync`）也会复制上下文。**本计划已在 Python 3.14 + Starlette 0.41.3 上实测确认同步与异步两条路径都成立。**

- [ ] **Step 1: 写失败测试**

创建 `tests/test_request_id.py`：

```python
import asyncio
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.observability.context import REQUEST_ID_HEADER, current_request_id
from app.observability.logging_config import setup_logging
from app.observability.middleware import RequestIdMiddleware

LINE_RE = re.compile(r"\[([0-9a-f]{16})\]")


def _probe_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    log = logging.getLogger("probe")

    @app.get("/sync/{job_id}")
    def sync_route(job_id: str):
        log.info("sync-enter job=%s", job_id)
        time.sleep(0.02)
        log.info("sync-exit job=%s", job_id)
        return {"request_id": current_request_id(), "job_id": job_id}

    @app.get("/async/{job_id}")
    async def async_route(job_id: str):
        log.info("async-enter job=%s", job_id)
        await asyncio.sleep(0.02)
        log.info("async-exit job=%s", job_id)
        return {"request_id": current_request_id(), "job_id": job_id}

    return app


@pytest.fixture
def log_file(tmp_path):
    path = tmp_path / "logs"
    setup_logging(log_dir=str(path), level="INFO", retention_days=30)
    yield path / "app.log"
    logging.shutdown()


def test_all_lines_of_one_request_share_the_id_and_it_is_returned(log_file):
    client = TestClient(_probe_app())
    resp = client.get("/sync/J1")

    assert resp.status_code == 200
    header_id = resp.headers[REQUEST_ID_HEADER]
    assert resp.json()["request_id"] == header_id

    logging.shutdown()
    lines = [
        line for line in log_file.read_text(encoding="utf-8").splitlines() if "job=J1" in line
    ]
    assert len(lines) == 2, f"期望 enter/exit 两行，实得 {lines}"
    assert {LINE_RE.search(line).group(1) for line in lines} == {header_id}


@pytest.mark.parametrize("route", ["sync", "async"])
def test_overlapping_requests_never_cross_talk(log_file, route):
    """同步路由（线程池）与异步路由（事件循环）两条执行路径都要覆盖——
    thread-local 会在异步路径上串号，这条测试是那个错误实现的报警器。"""
    app = _probe_app()
    jobs = [f"J{i}" for i in range(12)]

    if route == "sync":
        client = TestClient(app)
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            responses = list(pool.map(lambda j: client.get(f"/sync/{j}"), jobs))
        pairs = {r.json()["job_id"]: r.headers[REQUEST_ID_HEADER] for r in responses}
    else:

        async def drive():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
                return await asyncio.gather(*(ac.get(f"/async/{j}") for j in jobs))

        responses = asyncio.run(drive())
        pairs = {r.json()["job_id"]: r.headers[REQUEST_ID_HEADER] for r in responses}

    assert len(set(pairs.values())) == len(jobs), "请求标识重复了"

    logging.shutdown()
    text = log_file.read_text(encoding="utf-8")
    for job, request_id in pairs.items():
        for phase in ("enter", "exit"):
            matching = [
                line
                for line in text.splitlines()
                if line.endswith(f"{route}-{phase} job={job}")
            ]
            assert len(matching) == 1, f"{job} 的 {phase} 行数异常：{matching}"
            got = LINE_RE.search(matching[0]).group(1)
            assert got == request_id, (
                f"{job} 的 {phase} 行串号了：日志里是 {got}，该请求实际是 {request_id}"
            )
```

⚠️ 注意 `line.endswith(...)` 而不是 `in`：用 `in` 时 `job=J1` 会同时命中 `job=J10` / `job=J11`，测试会以「行数异常」假失败。

创建 `tests/test_health_endpoint.py`：

```python
from fastapi.testclient import TestClient


def test_health_endpoint_reports_logging_state_under_root_path(tmp_path):
    from app.observability.logging_config import setup_logging
    from app.web.server import create_app

    setup_logging(log_dir=str(tmp_path / "logs"), level="INFO", retention_days=30)
    app = create_app(
        db_path=str(tmp_path / "demo.db"),
        gateway_factory=lambda: None,
        root_path="/hr/recruit-agent",
    )
    client = TestClient(app)

    resp = client.get("/hr/recruit-agent/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["logging"]["degraded"] is False
    assert body["logging"]["log_file"].endswith("app.log")
    assert "file" in body["logging"]["handlers"]


def test_health_endpoint_moves_with_the_mount_prefix(tmp_path):
    """部署约束 1：挂到任意子路径都要工作，中间件与新端点都不得硬编码前缀。"""
    from app.observability.logging_config import setup_logging
    from app.web.server import create_app

    setup_logging(log_dir=str(tmp_path / "logs"), level="INFO", retention_days=30)
    app = create_app(
        db_path=str(tmp_path / "demo.db"),
        gateway_factory=lambda: None,
        root_path="/somewhere/else",
    )
    client = TestClient(app)

    assert client.get("/somewhere/else/health").status_code == 200
    assert client.get("/health").status_code == 404
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest tests/test_request_id.py tests/test_health_endpoint.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.observability.middleware'`

- [ ] **Step 3: 实现 `app/observability/middleware.py`**

```python
from __future__ import annotations

import logging
import uuid

from starlette.datastructures import MutableHeaders

from app.observability.context import REQUEST_ID_HEADER, request_id_var

logger = logging.getLogger(__name__)


class RequestIdMiddleware:
    """纯 ASGI 中间件：生成请求标识、写 contextvars、回写响应头。

    刻意不用 starlette 的 BaseHTTPMiddleware：它把下游 app 放进另一个 anyio
    任务里跑，contextvars 的设置无法可靠地传到 endpoint。纯 ASGI 中间件在同一个
    协程里 await 下游，传播是确定的（同步路由经 run_in_threadpool 也会复制上下文）。

    标识同时写进 scope["state"]：未捕获异常的 500 响应由更外层的
    ServerErrorMiddleware 生成，走的是它自己的 send、绕过本类的 send 包装，
    而那时 contextvar 已经被下面的 finally 复位了。unhandled_exception_handler
    从 request.state 取标识，不依赖 contextvar 的生命周期。
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex[:16]
        token = request_id_var.set(request_id)
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_header(message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            request_id_var.reset(token)
```

- [ ] **Step 4: 挂中间件与 /health（`app/web/server.py`）**

改动 1 —— 在 `from app.middleware.auth import AuthMiddleware` 之后加：

```python
from app.observability.logging_config import logging_status
from app.observability.middleware import RequestIdMiddleware
```

改动 2 —— 在 `app.add_middleware(AuthMiddleware)` 之后加：

```python
    # 后 add 的更靠外：RequestIdMiddleware 必须包住 AuthMiddleware，
    # 否则鉴权层自己产生的日志与异常拿不到请求标识。
    app.add_middleware(RequestIdMiddleware)
```

改动 3 —— 在 `@router.get("/")` 之前插入 `/health` 路由（注意挂在 `router` 上，`router` 最后按 `root_path` 前缀 include，所以端点自动跟随挂载前缀，**不得**挂在 `app` 上）：

```python
    @router.get("/health")
    def health() -> dict:
        """日志子系统坏掉时唯一还能对外说话的通道（design 决策 5）。

        降级时仍返回 200：服务照常提供业务功能，用 503 会诱导监控去重启一个
        其实健康的进程——拿更大的故障换更小的故障。降级事实放在 body 里，
        运维检查看 status 字段而不是 HTTP 码。
        """
        status = logging_status()
        return {
            "status": "degraded" if status.degraded else "ok",
            "logging": status.as_dict(),
        }

```

- [ ] **Step 5: 跑测试确认通过**

Run: `venv/bin/python -m pytest tests/test_request_id.py tests/test_health_endpoint.py -q`
Expected: PASS — `5 passed`

- [ ] **Step 6: 跑全量，重点确认既有子路径测试没被中间件打破**

Run: `venv/bin/python -m pytest -q`
Expected: PASS — `111 passed`

Run: `venv/bin/python -m pytest tests/test_web_api.py -q -k "subpath or unprefixed or hardcoded"`
Expected: PASS — `3 passed`（部署约束 1 未被中间件破坏）

- [ ] **Step 7: 提交**

```bash
git add app/observability/middleware.py app/web/server.py tests/test_request_id.py tests/test_health_endpoint.py
git commit -m "feat(observability): 请求标识贯穿日志与响应头，新增 /health 暴露日志降级"
```

---

### Task 6: 未捕获异常的可定位证据

对应 tasks.md 2.5 / 2.6 / 2.7 / 2.8。**本变更的立项动机在这一节落地，reviewer 重点看。**

**Files:**
- Modify: `app/observability/middleware.py`（追加 `unhandled_exception_handler`）
- Modify: `app/web/server.py`（注册异常处理器）
- Create: `tests/test_error_evidence.py`

**Interfaces:**
- Consumes: `RequestIdMiddleware`（Task 5）、`UNSET_REQUEST_ID`（Task 2）
- Produces: `unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse`、`SESSION_PARAM_NAMES: tuple[str, ...]`

**⚠️ 这里有一个实测踩到的坑，实现者请先读**：最初的实现把「记录异常」放在 `RequestIdMiddleware` 的 `except` 分支里，并靠中间件的 `send` 包装加响应头。**那样做时 500 响应上没有 `X-Request-ID`。** 原因是 Starlette 的 `ServerErrorMiddleware` 位于**全部用户中间件之外**：它捕获异常后用**自己的 `send`** 发 500，中间件的 `send` 包装根本不在那条路径上。而「使用者报告问题时可以提供该标识」要救的恰恰是出错这一次——头掉在这里等于 spec 那条要求在最重要的场景下失效。所以改为挂 `Exception` 异常处理器：它由 `ServerErrorMiddleware` 调用，返回的 `JSONResponse` 自带 headers，能真正到达客户端。

同时注意：处理器执行时，中间件的 `finally` **已经**复位了 contextvar，所以 `request_id` 必须经 `extra=` 显式传给日志，靠 Task 2 的 `RequestIdFilter`「已有属性不覆盖」的语义生效。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_error_evidence.py`：

```python
import logging
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.observability.context import REQUEST_ID_HEADER
from app.observability.logging_config import setup_logging
from app.observability.middleware import RequestIdMiddleware, unhandled_exception_handler
from app.storage.db import get_connection, init_schema
from app.storage.idempotency import idempotent_effect


@pytest.fixture
def log_file(tmp_path):
    path = tmp_path / "logs"
    setup_logging(log_dir=str(path), level="INFO", retention_days=30)
    yield path / "app.log"
    logging.shutdown()


def _boom_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    @app.get("/boom/{job_id}")
    def boom(job_id: str):
        raise RuntimeError("kaboom")

    return app


def test_server_error_leaves_request_id_exception_type_and_stack(log_file):
    client = TestClient(_boom_app(), raise_server_exceptions=False)
    resp = client.get("/boom/J-ERR")

    assert resp.status_code == 500
    request_id = resp.headers[REQUEST_ID_HEADER]

    logging.shutdown()
    text = log_file.read_text(encoding="utf-8")
    error_lines = [line for line in text.splitlines() if "未捕获异常导致服务端错误" in line]
    assert len(error_lines) == 1, f"未捕获异常没有留下恰好一条错误日志：{error_lines}"

    line = error_lines[0]
    assert request_id in line, "错误日志里没有请求标识，无法对应到用户报告的那一次"
    assert "'job_id': 'J-ERR'" in line, "错误日志里没有业务会话标识，还得查库反推"
    assert "RuntimeError" in text and "kaboom" in text, "缺异常类型"
    assert "Traceback (most recent call last)" in text, "缺完整调用栈"
    assert "raise RuntimeError" in text, "调用栈没到抛出点"


def test_idempotency_rollback_alert_reaches_the_persistent_log(log_file, tmp_path):
    """findings 第 8.3 节：这条 logger.error 是本变更的直接触发原因——
    修复给它加了兜底告警，而现网零日志让它什么都不会留下。"""
    conn = get_connection(str(tmp_path / "probe.db"))
    init_schema(conn)

    class RollbackExplodes:
        def __init__(self, real): self._real = real
        def __getattr__(self, name): return getattr(self._real, name)
        def rollback(self): raise sqlite3.OperationalError("rollback boom")

    @idempotent_effect("effect_probe")
    def effect_probe(conn, *, thread_id, business_key):
        raise RuntimeError("business write exploded")

    with pytest.raises(RuntimeError, match="business write exploded"):
        effect_probe(RollbackExplodes(conn), thread_id="T1", business_key="B1")

    logging.shutdown()
    text = log_file.read_text(encoding="utf-8")
    assert "rollback failed" in text, "兜底告警没有落进持久化日志"
    assert "T1:effect_probe:B1" in text, "告警里没有幂等键，无法定位是哪个 effect"
```

⚠️ `test_idempotency_rollback_alert_reaches_the_persistent_log` **只加测试，不改 `app/storage/idempotency.py` 一行**（工程铁律 1 相关代码本次不动）。

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest tests/test_error_evidence.py -q`
Expected: FAIL — `ImportError: cannot import name 'unhandled_exception_handler' from 'app.observability.middleware'`

- [ ] **Step 3: 追加 `unhandled_exception_handler`（`app/observability/middleware.py`）**

在 import 段补上（`from app.observability.context import ...` 那一行改为带 `UNSET_REQUEST_ID`）：

```python
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.observability.context import REQUEST_ID_HEADER, UNSET_REQUEST_ID, request_id_var
```

在 `logger = logging.getLogger(__name__)` 之后加常量：

```python
# 业务会话标识在路由里的参数名。Starlette 在把请求交给 endpoint 之前就把
# path_params 写进了 scope，所以即使 endpoint 抛异常也依然读得到是哪个会话
# 出的问题——不需要另行查库反推（spec 明确要求这一点）。
SESSION_PARAM_NAMES = ("job_id", "thread_id")
```

在文件末尾追加：

```python
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """未捕获异常的统一记录点，挂在 FastAPI 的 Exception handler 上。

    挂这里而不是挂在中间件的 except 分支里，是因为 ServerErrorMiddleware 位于
    全部用户中间件之外：它捕获异常后用自己的 send 发 500，中间件加的响应头到
    不了那个响应上。而「使用者报告问题时可以提供标识」要救的恰恰是出错这一次。

    注意 request_id 必须显式经 extra 传入：此刻 contextvar 已被中间件的 finally
    复位，RequestIdFilter 只在 record 没有该属性时才回填，extra 优先。
    """
    request_id = getattr(request.state, "request_id", UNSET_REQUEST_ID)
    params = request.scope.get("path_params") or {}
    session = {k: params[k] for k in SESSION_PARAM_NAMES if k in params}
    logger.error(
        "未捕获异常导致服务端错误：method=%s path=%s session=%s",
        request.method,
        request.url.path,
        session or "<无会话上下文>",
        exc_info=exc,
        extra={"request_id": request_id},
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "internal server error", "request_id": request_id},
        headers={REQUEST_ID_HEADER: request_id},
    )
```

- [ ] **Step 4: 在应用上注册（`app/web/server.py`）**

把 Task 5 加的 import 改为：

```python
from app.observability.logging_config import logging_status
from app.observability.middleware import (
    RequestIdMiddleware,
    unhandled_exception_handler,
)
```

在 `app.add_middleware(RequestIdMiddleware)` 之后加一行：

```python
    app.add_exception_handler(Exception, unhandled_exception_handler)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `venv/bin/python -m pytest tests/test_error_evidence.py -q`
Expected: PASS — `2 passed`

- [ ] **Step 6: 跑全量**

Run: `venv/bin/python -m pytest -q`
Expected: PASS — `113 passed`

- [ ] **Step 7: 提交**

```bash
git add app/observability/middleware.py app/web/server.py tests/test_error_evidence.py
git commit -m "feat(observability): 500 留下含请求标识、会话标识与完整堆栈的错误证据"
```

---

### Task 7: 部署接线、文档边界与现网发布

对应 tasks.md 3.7 / 3.8 / 4.1 / 4.2 / 4.3 / 4.4 / 4.5 / 4.6。

**Files:**
- Modify: `deploy-server.ps1`
- Modify: `sync-to-server.sh`
- Modify: `.env.example`
- Modify: `05-发布运行手册.md`
- Create: `tests/test_deploy_logging_wiring.py`

**Interfaces:**
- Consumes: 前六个 Task 的全部产出
- Produces: 无新代码接口；产出的是部署脚本的幂等改动与两条固化断言

- [ ] **Step 1: 写失败测试**

创建 `tests/test_deploy_logging_wiring.py`：

```python
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_sync_whitelist_never_ships_logs_to_or_from_the_server():
    """日志目录若被误加进同步白名单，会把生产日志反向拉回开发机——
    那是未授权的个人信息转移，属合规事件，不是运维小事。"""
    script = (REPO / "sync-to-server.sh").read_text(encoding="utf-8")

    sync_block = re.search(r"SYNC_PATHS=\((.*?)\)", script, re.S).group(1)
    assert "logs" not in re.findall(r'"([^"]+)"', sync_block)

    exclude_block = re.search(r"EXCLUDE_NAMES=\((.*?)\)", script, re.S).group(1)
    assert "logs" in re.findall(r'"([^"]+)"', exclude_block)


def test_deploy_script_creates_and_verifies_writable_log_dir():
    script = (REPO / "deploy-server.ps1").read_text(encoding="utf-8")
    assert 'Join-Path $AppDir "logs"' in script
    assert "New-Item -ItemType Directory -Path $logDir" in script
    assert "FileSystemAccessRule" in script and "SYSTEM" in script
    assert "deploy-write-probe" in script, "只创建目录不验证可写，等于把降级留到运行时"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest tests/test_deploy_logging_wiring.py -q`
Expected: FAIL — 两条都失败（`assert 'logs' in [...]` 与 `assert 'Join-Path $AppDir "logs"' in script`）

- [ ] **Step 3: `sync-to-server.sh` 把 `logs` 加进排除名单**

把这一行：

```bash
EXCLUDE_NAMES=(".venv" "venv" "data" "__pycache__" ".pytest_cache")
```

替换为：

```bash
# logs 与 data 同属运行时数据：生产日志含个人信息，被反向同步回开发机
# 会构成一次未经授权的个人信息转移（server-runtime-logging design Risks）。
# 它本来就不在 SYNC_PATHS 白名单里，这里再写一遍是为了让「不同步」这件事
# 有一个显式的、可被测试断言的落点，而不是依赖「现在没有就永远没有」。
EXCLUDE_NAMES=(".venv" "venv" "data" "logs" "__pycache__" ".pytest_cache")
```

`SYNC_PATHS` **不改**——它是白名单，`logs` 本来就不在里面。

- [ ] **Step 4: `deploy-server.ps1` 建日志目录并验可写**

在 `Write-Host "==> 注册 Windows 计划任务: $TaskName"` **之前**插入：

```powershell
Write-Host "==> 准备日志目录"
# 幂等：目录已存在则跳过创建、不重置权限、不清空内容（沿用本脚本既有约定）。
# 计划任务以 SYSTEM 账户运行，日志目录必须 SYSTEM 可写，否则应用启动时会
# 降级为仅 stdout——而计划任务没有控制台，等于回到零日志。
$logDir = Join-Path $AppDir "logs"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
    Write-Host "    已创建: $logDir"
} else {
    Write-Host "    已存在，跳过创建: $logDir"
}

$acl = Get-Acl $logDir
$systemRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    "SYSTEM", "Modify",
    "ContainerInherit,ObjectInherit", "None", "Allow")
$acl.SetAccessRule($systemRule)
Set-Acl -Path $logDir -AclObject $acl

# 只验证不假设：写一个探针文件确认真的可写，失败就当场报错而不是等到运行时静默降级。
$probe = Join-Path $logDir ".deploy-write-probe"
try {
    Set-Content -Path $probe -Value "" -ErrorAction Stop
    Remove-Item $probe -ErrorAction SilentlyContinue
    Write-Host "    可写性验证通过"
} catch {
    throw "日志目录 $logDir 不可写：$_。计划任务以 SYSTEM 运行，请检查该目录的 ACL。"
}

```

并把文件最后一行：

```powershell
Write-Host "==> 部署完成。验证: curl.exe http://localhost:$Port/hr/recruit-agent/"
```

替换为：

```powershell
Write-Host "==> 部署完成。验证:"
Write-Host "    curl.exe http://localhost:$Port/hr/recruit-agent/"
Write-Host "    curl.exe http://localhost:$Port/hr/recruit-agent/health   # status 应为 ok，degraded 表示日志没落盘"
Write-Host "    Get-Content (Join-Path $AppDir \"logs\app.log\") -Tail 20"
```

**计划任务的 `-Argument` 不改**：日志配置全部走代码默认值（design 决策 6），计划任务定义不需要新增参数。

- [ ] **Step 5: 跑测试确认通过**

Run: `venv/bin/python -m pytest tests/test_deploy_logging_wiring.py -q`
Expected: PASS — `2 passed`

- [ ] **Step 6: `.env.example` 追加日志段**

在文件末尾追加：

```bash

# 运行时日志（server-runtime-logging）。全部可选：不配也能工作，默认写到
# <工作目录>/logs/app.log、INFO 级、按天轮转保留 30 天。
# .51 的 .env 是服务器上独立维护、不随代码同步的文件——日志功能刻意不依赖它，
# 免得"推代码"与"改 .env"变成两个必须同时做对的步骤，漏一个就静默地没有日志。
LOG_DIR=logs
LOG_LEVEL=INFO
LOG_RETENTION_DAYS=30
LOG_MAX_BYTES=52428800
```

- [ ] **Step 7: 运维文档写明两条边界（`05-发布运行手册.md`）**

在「## 故障排查」小节**之前**插入一节。这两条都是禁止性约束，没有代码断言守得住，只能靠文档 + review：

````markdown
## 日志

日志落在服务器的 `C:\apps\zhuopin-recruit-agent\logs\app.log`，按天轮转，
默认保留 30 天，单文件超过 50MB 也会切分（文件名带 `.1` 序号）。

查看最近 50 行：

```powershell
Get-Content C:\apps\zhuopin-recruit-agent\logs\app.log -Tail 50
```

按请求标识捞一次请求的全部日志（标识从响应头 `X-Request-ID` 或 500 响应体拿）：

```powershell
Select-String -Path C:\apps\zhuopin-recruit-agent\logs\*.log* -Pattern "<请求标识>"
```

确认日志子系统健康：

```powershell
curl.exe http://localhost:8095/hr/recruit-agent/health
```

`status` 为 `degraded` 表示日志目录不可写、日志没有落盘——服务仍在正常提供
业务功能，但排障证据在丢失，需尽快处理 `logs\` 目录的权限。

### ⚠️ 两条不可违背的前提

1. **给 uvicorn 加 `--workers` 之前必须先更换轮转方案。** 当前按天轮转依赖
   单进程：Windows 上轮转要重命名当前日志文件，若有第二个进程持有该文件
   句柄，重命名会失败。计划任务里的 uvicorn 现在没有 `--workers`，改之前
   先换掉 `app/observability/handlers.py` 的 `DailyRotatingFileHandler`。

2. **运行日志不是合规举证依据。** 它会被轮转和留存期清掉。需要长期保存以
   供举证的记录——决策留痕、外发审批、简历访问记录——必须由独立的、不受
   日志轮转影响的持久化机制承载（`ai-audit-trail-and-outbound-gate`），
   **绝不能只存在于运行日志里**。
````

- [ ] **Step 8: 本地全量跑绿**

Run: `venv/bin/python -m pytest -q`
Expected: PASS — `115 passed`（基线 91 + 本变更新增 24）

- [ ] **Step 9: 提交并推 CI（Windows runner）**

```bash
git add deploy-server.ps1 sync-to-server.sh .env.example 05-发布运行手册.md tests/test_deploy_logging_wiring.py
git commit -m "chore(deploy): 日志目录接进部署流程，固化 logs 不同步与轮转单进程前提"
git push
```

CI 跑在 `windows-latest` + Python 3.14，与 `.51` 运行环境对齐。**必须确认 Windows 上全绿再往下走**——轮转的文件重命名、`tmp_path` 的句柄释放、路径分隔符这三类差异只有真实 Windows 才测得出（SQLite 事务冲突那次的教训）。若 CI 红，先回到 `tests/conftest.py` 的句柄释放 fixture 排查。

- [ ] **Step 10: 🚦 决策点 —— `.51` 生产发版（不可代，须 Shao Peishen 拍板）**

按 CLAUDE.md 决策代理表，**生产服务器 `.51` 的发版决定属「不可代」**。代理人未指定期间同样一律挂起等本人。**执行者到这一步必须停下来，向 Shao Peishen 取得明确同意后再继续。**

取得同意后按序执行：

```bash
./sync-to-server.sh
```

`sync-to-server.sh` 只推代码 + 重启计划任务，**不会**创建日志目录。首次发版需要在 `.51` 上跑一次 `deploy-server.ps1` 来建目录并设权限（RDP 登录，管理员身份）：

```powershell
powershell -ExecutionPolicy Bypass -File C:\apps\zhuopin-recruit-agent\deploy-server.ps1
```

验证（在 `.51` 上跑，或从 Mac 用 ssh 包装）：

```bash
ssh zp51 "curl.exe -sS http://localhost:8095/hr/recruit-agent/health"
```

Expected: JSON 里 `"status": "ok"`、`"degraded": false`、`"log_file"` 指向 `C:\apps\zhuopin-recruit-agent\logs\app.log`

```bash
ssh zp51 "powershell -Command \"Get-Content C:\apps\zhuopin-recruit-agent\logs\app.log -Tail 20\""
```

Expected: 有内容且非空，能看到 uvicorn 启动行与 `[<16位十六进制>]` 形态的请求标识

- [ ] **Step 11: 回填 findings 并核对 spec 一致性**

在 `docs/findings/2026-08-13-sqlite-事务归属冲突.md` 第 8.3 节追加：日志缺口已闭环、实际采用的方案（应用内 `dictConfig` + 按天轮转 + `contextvars` 请求标识 + 两层脱敏）、留存期实际取值（默认 30 天，如发版时改过以实际为准）、以及「`idempotency.py` 的 `rollback failed` 告警现在会落盘，由 `tests/test_error_evidence.py::test_idempotency_rollback_alert_reaches_the_persistent_log` 守住」。

再对照 `openspec/changes/server-runtime-logging/specs/runtime-observability/spec.md` 逐条确认本变更没有引入 spec 未覆盖的新行为（tasks.md 4.6）。**特别确认没有实现不变式自动告警**——那条已确认按 spec 走、本次不做（见「范围边界」）。

```bash
git add docs/findings/2026-08-13-sqlite-事务归属冲突.md
git commit -m "docs: server-runtime-logging 发版后回填日志缺口闭环记录"
```

- [ ] **Step 12: 回勾 OpenSpec WBS 并归档**

把 `openspec/changes/server-runtime-logging/tasks.md` 的 1～4 章全部 checkbox 勾上，然后**当场**跑 `openspec-archive-change`——CLAUDE.md 规定「代码完成但变更包未归档」的中间态不得跨越一个工作 session。

---

## 提取验证结果（2026-08-19，写计划时完成）

按 `spec-to-plan` 第 6 步，本计划的全部代码块在编写阶段就被原样落地到临时工作区（`git ls-files` 复制的完整仓库副本）并用项目自带的 `venv/bin/python`（Python 3.14.6，与 `.51` 对齐）跑了全量测试。

**结果：`115 passed`**（基线 91 + 本变更新增 24）。逐 Task 的新增用例数：Task 1 = 3、Task 2 = 4、Task 3 = 2、Task 4 = 6、Task 5 = 5、Task 6 = 2、Task 7 = 2。

同时验证了：

- **Task 3 的中间态可独立跑绿**——脱敏尚未接入时的 `logging_config.py` 不会因缺少 `redaction` 模块而崩，Task 4 的 4 处改动是纯增量
- **测试顺序无污染**——`setup_logging()` 改的是全局 logging 状态，把日志相关测试提到 `test_web_api.py`、`test_idempotency.py` 之后重排跑了一遍，仍然全绿（既有的 `caplog` 用例不受 `dictConfig` 影响）

### 期间揪出并已修掉的 3 个真实缺陷

1. **500 响应丢失 `X-Request-ID`（真实实现缺陷，spec 违背）**
   最初把异常记录与响应头都放在 `RequestIdMiddleware` 里。实测 500 响应上**没有**该头——Starlette 的 `ServerErrorMiddleware` 在全部用户中间件之外，用自己的 `send` 发 500，绕过了中间件的 `send` 包装。spec 要求「该标识 MUST 出现在该请求的响应中，使得使用者报告问题时可以提供它」，而它恰恰在最需要的出错场景下失效。**已改为挂 `Exception` 异常处理器**（Task 6），并连带发现 contextvar 此刻已被复位、必须经 `extra=` 显式传 `request_id`。

2. **同日二次轮转会静默丢弃整段日志（真实实现缺陷）**
   stdlib 的 `TimedRotatingFileHandler.doRollover()` 算出目标名后有一句 `if os.path.exists(dfn): return`（"Already rolled over"）——大小上界在同一天内二次触发时目标名已被占用，会**直接放弃轮转**，当天日志继续无上界地写下去，违反「日志量超过配置上限」场景。**已通过覆写 `rotation_filename()` 返回带序号的未占用名字解决**，并因此把 `backupCount` 置 0、改用 mtime 自行清理（stdlib 的 `getFilesToDelete()` 认不出带序号的单元，会把它们永远留下）。

3. **测试断言子串误匹配（测试缺陷，会造成假失败）**
   并发串扰测试用 `f"job={job}" in line` 判定，`job=J1` 会同时命中 `job=J10` / `job=J11`。**已改为 `line.endswith(...)`。**

另外清掉了 `RedactionFilter` 里一处残留的空 `if ... : pass` 分支。

### 这一步验证不了什么

测试与被测代码出自同一份文档、同一个作者，全绿只证明**代码可执行且内部自洽**，不证明**符合 spec**。spec 合规由 `run-build` 的两阶段 review 负责。此外：

- **Windows 行为未验证。** 本地是 macOS。轮转时的文件重命名、`tmp_path` 的句柄释放、ACL 设置三处都有平台差异，必须靠 Task 7 Step 9 的 `windows-latest` CI 兜住。`tests/conftest.py` 的句柄释放 fixture 就是为此预置的，**不要因为「本地不加也过」而删掉它**。
- **`deploy-server.ps1` 的 PowerShell 代码未执行过**，只有文本断言。ACL 那段要到 Task 7 Step 10 在 `.51` 上真跑才算验证。
