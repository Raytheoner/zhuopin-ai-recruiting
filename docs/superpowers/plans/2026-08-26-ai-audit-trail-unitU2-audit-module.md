# AI 留痕与外发门禁 · 交付单元 U2（`app/audit` 模块）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新建 `app/audit/` 目录，落地留痕的三块砖——`DecisionEvent` 领域事件（design D2 的招聘领域字段，一等字段而非自由字典）、`SqliteSink` + `JsonlChainSink` 双 sink（design D1 的「SQLite 真身 + JSONL 防篡改镜像」）、`AuditRecorder` 统一入口（**两段式 API**：写 SQLite 与 append JSONL 是两次可分别调用的动作）。**此时尚未接线**，与整个仓库零文件重叠，现有行为完全不变。

**Architecture:** 三层单向依赖 `events.py ← sinks.py ← recorder.py`，无反向引用、无循环。`SqliteSink` 绑定调用方的连接、**不自行 `commit`**（与 `effect_persist_draft` 同一约定），进的是调用方那一个 `BEGIN`；`JsonlChainSink` 与数据库无关，按解析后的绝对路径共享进程内互斥锁与上一条哈希游标，append 与校验全部走**二进制字节**（校验对磁盘原始字节重算 SHA-256，不做 JSON 重序列化）。`AuditRecorder` 只做两件事的**分发**，⛔ 不提供把两段打包成一次调用的方法——打包会在事务回滚时留下「JSONL 有、SQLite 无」，design D1 明令这是更糟的偏差方向。

**Tech Stack:** Python 3.14.6（`./venv`）· 标准库 `sqlite3` / `hashlib` / `threading` / `json` / `dataclasses` / `ast` · pytest 8.3.4 · **不引入任何新依赖**（`requirements.txt` / `pyproject.toml` diff 必须为空）

---

## Global Constraints

以下条目从 `CLAUDE.md`（2026-08-26 版）「工程铁律」「合规红线」「部署约束」、本变更包 `delivery-units.md` §2.U2 / §3.4 / §4、`design.md` D1–D3，以及 OP-0826-E 指令 §三 **逐字复制**。**每个 Task 的验收隐含包含本节全部内容**，`subagent-driven-development` 会把这一段原样交给 reviewer 当注意力透镜。

### 本单元的头号约束（OP-0826-E §三 第 1 条，逐字）

> **AuditRecorder 的对外 API 必须是两段式**——写 SQLite 与 append JSONL 是两次可分别调用的动作，⛔ 不得打包在一个 `record()` 里同步完成。tasks 2.8 的"先 SQLite 后 JSONL"是**顺序**要求，两段式满足它；打包成一次调用会在事务回滚时产生「JSONL 有、SQLite 无」——design D1 明令这是更糟的偏差方向（审计查不到记录）。这条在 U2 定死，否则 U3/U5 返工。

**reviewer 的机械判据（三条，全部有测试）**：

1. `AuditRecorder.record(conn, event)` 调用后，镜像 sink 的写入计数**恒为 0**（Task 5 的 `test_record_writes_sqlite_only` 用 spy sink 计数断言）。
2. `AuditRecorder.mirror(event)` 调用后，真身 sink 的写入计数**恒为 0**（`test_mirror_writes_jsonl_only`）。
3. `app/audit/recorder.py` 里**不存在任何一个函数体同时触碰两个 sink 的 write**（Task 5 的 `test_recorder_exposes_no_packed_method` 用 AST 扫源码断言，并带一条**阳性对照**——把一个故意写错的源码字符串喂进同一个检查函数，必须被抓出来。没有阳性对照，这条断言在"检查函数根本没生效"时同样是绿的，那不叫验证）。

### 本单元的第二条约束（OP-0826-E §三 第 2 条，逐字）

> **⛔ 禁止在 effect_\* 函数体内 append JSONL**。允许的偏差只有单向：「SQLite 有、JSONL 缺行」（真身完整、镜像缺证据）；反向禁止。落地形态＝写 SQLite 那段进 effect_\* 函数体，append JSONL 那段在 effect_\* **返回之后**由调用点触发（此时幂等装饰器已 commit）。**这不需要改 idempotent_effect 装饰器**。

**U2 此刻还没有任何 `effect_*` 函数，所以这条在本单元的落地形态是一条「会在 U5 变红」的结构守护**：Task 5 的 `test_no_effect_function_appends_jsonl` 用 AST 扫 `app/**/*.py`，找出所有 `effect_` 开头的函数，断言它们的函数体内不出现 `mirror(` / `backfill(` 调用、不出现 `JsonlChainSink` 这个名字。同样带阳性对照。

**这条守护今天是"恒真"的（`app/` 下现在没有任何 `effect_*` 引用 audit），所以阳性对照不是可选项**——它是这条断言与"断言写成了恒真"之间唯一的区别（判据形状与 tasks 6.7 相同）。

### 本单元的第三条约束（OP-0826-E §三 第 3 条，逐字）

> **2.5 的第四个场景是这条防线的分水岭，不是"多写一个用例"**：「删光全部 prev_hash 字段后重写」必须在**第 2 行**就判断链断裂。平台侧已经踩过这个绕过，本仓库一次做对。2.5 与 2.6 是本单元含金量最高的两条。

**reviewer 的机械判据**：`test_all_prev_hash_fields_stripped_breaks_at_line_two` 断言的是 `result.broken_at == 2`——**不是** `result.ok is False`。只断言 `ok is False` 的话，一个"任何 `prev_hash` 缺失都算断链（含第 1 行）"的实现也会绿，而那个实现违反 spec「仅第 1 条记录可豁免（向前兼容既有文件）」。位置断言同时锁住了豁免的**存在**与豁免的**边界**。

### 工程铁律（不可违背）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
   *为什么*：业务写失败而幂等记录成功 → 系统判定"已执行"→ 永不重试。**幂等本是防重复的保护，拆开事务后变成永久丢失的保证。** 实证：`.51` 现网 2026-08-10 与 08-12 各丢一轮 `outbox`（幂等记录已落），用户没收到回复且永远不会补发，见 `docs/findings/2026-08-13-sqlite-事务归属冲突.md` §8.5。

> **本单元与这条的关系有三处，全部要在实现里兑现**：
> ① **`SqliteSink.write` ⛔ 不自行 `commit`**（tasks 2.2 逐字；与 `effect_persist_draft` 同一约定，见 `app/graph/nodes.py` 那段「不在这里 conn.commit()」的注释）。自行 commit 会把幂等记录与业务写拆成两个事务。
> ② **`AuditRecorder.record(conn, event)` 要求调用方把事务所在的那个连接原样传进来，并断言它与 sink 绑定的连接是同一个对象**（`conn is not self._store.conn` 即抛 `TransactionOwnershipError`）。这个参数在功能上是冗余的——它存在的唯一理由就是把"同一连接"这条不变式变成调用点上一句写得出来、测得到的断言。2026-08-13 那次事故的形状正是"两个事务管理者共用一条连接"。
> ③ **U2 不新增任何 `effect_*` 节点、不改 `idempotent_effect` 装饰器、不改 `effect_log` 表结构。** reviewer 判据：本单元 diff 里不出现 `@idempotent_effect`、不出现对 `effect_log` 的任何 DDL/DML、不出现新增的 `conn.commit()` 调用点（测试文件里为构造场景而显式 commit 除外，且必须在测试里，不得在 `app/audit/` 下）。

2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。

> **本单元与这条的关系**：`app/audit/` 是被 L4 调用的**存储适配层**，不是 L3 Agent。它的写入动作将来全部发生在 U5 的 `effect_*` 节点内部（`record()`）与节点返回之后（`mirror()`）。U2 自己不决定何时被调用，因此 **`app/audit/` 下不得 import `app.graph`、不得 import `app.config`**——前者会形成反向依赖，后者会让"审计路径"在启动时就绑死配置、并让 U3 的注入点不再是唯一一处。Task 5 的 `test_audit_module_imports_no_config_or_graph` 是这条的守护。

3. **所有 AI 评分必须持久化**：模型标识 + 模型版本 + prompt 版本 + temperature + 输入哈希 + rubric 快照 + 原始响应。

> **U2 是这条铁律的写入路径**：`DecisionEvent` 的字段就是这条的逐字兑现，`SqliteSink` 把它落进 U1 的 `analysis_run` 十四列。注意"模型标识"与"模型版本"落成 `configured_model` / `response_model` **两个字段各自保存、不互相覆盖**（Task 2 的 `test_configured_and_response_model_are_kept_apart`）。真正的调用方接线是 U3。

4. **每条 `criterion_score` 必须有 `evidence_ref`**（回指简历原文或面试 turn 的 offset）。`evidence_ref` 为空不允许写入。

> **U1 已经把这条做成了数据库 `CHECK`。U2 在这条上的唯一职责是「不吞异常」**：`SqliteSink` ⛔ 不得把 `sqlite3.IntegrityError` 当成"已写入"短路掉。主键冲突的短路（tasks 2.2）必须**精确到是不是 `analysis_run.id` 的 UNIQUE 冲突**，`CHECK constraint failed` 必须原样抛出去。Task 2 的 `test_check_constraint_failure_is_not_swallowed` 是这条的守护——把 `except sqlite3.IntegrityError: return False` 写宽一格，铁律 4 当场从"数据库强制"退回"静默放过"，而所有正常用例照样全绿。

5. **`temperature=0`；模型版本优先显式锁定**，禁止 `latest` 类别名。
   供应商不提供带版本号快照时（如 DeepSeek 公开 API 只有 `deepseek-chat` 这类会漂移的别名），**必须从 API 响应里取回实际的 `model` 字段并持久化**——配置里写的名字不算数，响应返回的才算。

> **本单元与这条的关系**：`response_model` 与 `system_fingerprint` 是"响应返回的才算"的落盘位，两列**必须允许写空**（spec `Scenario: 供应商不返回部署指纹`——"该字段记为空值，留痕照常写入，留痕流程不因字段缺失而失败"）。Task 2 的 `test_missing_system_fingerprint_is_stored_as_null` 断言的是**写入成功且列为 NULL**，不是抛异常。U2 不实现 `latest` 别名的拒绝（已在 `app/config.py:26-30` 生效）。

6. **企微回调先落库再处理**：不适用（本单元不接企微通道）。
7. **`langgraph >= 1.0.10`**：本单元不动依赖版本，`requirements.txt` diff 必须为空。

### 合规红线

- **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。审计断言：`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。

> **U2 的对应形态**：`DecisionEvent` 的 `event_type` 白名单里有 `outbound_blocked` / `outbound_delivered` 两类——"被拦下"本身是一条要留痕的决策事实（`specs/outbound-approval-gate` 的「外发与拦截动作强制留痕」：留痕 MUST 使用与 AI 评分留痕相同的机制，落入同一份可校验的记录中）。U2 只提供承载形状，判定在 U4、接线在 U5、断言在 U6。

- **禁止人脸/表情分析**（《人脸识别技术应用安全管理办法》2025-06-01 施行）。声学情绪信号（语速/停顿/静默）只展示给面试官，不进 `criterion_score`。

> **⛔ U2 不实现 `criterion_key` 白名单**（是 U3 的 3.4，断言是 U6 的 6.3）。不要在 `SqliteSink` 里加一个"顺手做掉"的维度校验——白名单必须集中在一处 Python 定义里（design 风险表最后一条），散成两处会出现"一处放行一处拒绝"的分叉，而分叉的那一侧就是红线的缺口。U2 的 `criterion_key` 是透传。

- **AI 生成的 JD、拒信、邀约须带标识**：U4 的 4.4 兑现，U2 不涉及。
- **模型全部走境内**、**绝不用历史录用结果做监督信号**：后者是 `app/audit/events.py` 的**模块 docstring 必须写明**的内容（spec `Requirement: 留痕数据的用途限制`——"该限制 SHALL 在数据结构层面以显式标注体现，使后续开发者不会误用"）。U1 已在 `analysis_run` 的表注释里写过一遍；模块层要再写一遍，因为读代码的人和读 schema 的人不是同一批。由 Task 1 的 `test_events_module_carries_no_training_use_marker` 机器校验。
- 主观描述不得进入硬门槛规则：本单元不涉及。

### 合规红线之外的一条硬要求（spec「AI 调用的可复现留痕」第三段，逐字）

> 系统 MUST NOT 在留痕记录中存储简历原文。输入内容以哈希形式记录，原文留在简历主存储中按其自身访问控制管理。

**落地形态**：`DecisionEvent` 上**只有 `input_hash`，没有任何承载原文的字段**。Task 1 的 `test_no_field_name_smells_like_resume_plaintext` 参数化断言字段名里不出现 `resume` / `cv_text` / `input_text` / `raw_input` / `plaintext`——将来有人加一个 `input_text` "方便排查"，这条会立刻变红。`raw_response`（模型原始响应）是铁律 3 明令要存的，不在此列。

### 部署约束

4. **目标服务器是 Windows，没有 Docker**。部署形态 = Python venv + Windows 计划任务（SYSTEM 账户 + AtStartup + 失败重启 3 次）+ 防火墙规则 + scp 推送。不要引入容器。

> **本单元与这条的关系是一个真实的 Windows 坑**：JSONL 的 append 与校验**必须走二进制模式**（`open(path, "ab")` / `open(path, "rb")`）。用文本模式在 Windows 上写 `"\n"` 会被换行翻译成 `\r\n` 落盘，而链的定义是"上一行落盘字节的 SHA-256"——写入侧算的是不含 `\r` 的字节、校验侧读回来的是含 `\r` 的字节，**在 Mac 上全绿、推到 `.51` 上整条链立刻报断**。Task 3 的 `test_file_uses_lf_only_no_crlf` 直接断言落盘字节里不出现 `\r\n`。

5. **M2 起处理真实简历前**，必须具备可识别到人的登录 + 简历访问留痕。

> `DecisionEvent.confirmed_by` 现阶段**不可信**（鉴权是空壳，`AuthContext.user_id` 恒为 `None`，design D7）。字段先占位、docstring 写明不可信，SSO 落地后同一字段变可信、结构不改。技术债登记是 U7 的 7.5，**U2 不重复登记**。

### 跨单元接口约定（`delivery-units.md` §4，第 2、7、8 条逐字）

2. **`AuditRecorder` 是两段式 API**：写 SQLite（进事务）与 append JSONL（提交后）分开。⛔ 禁止在任何 `effect_*` 函数体内 append JSONL。理由见 §3.4。
7. **本包三条硬边界**（全部单元）：不新增 `zhuopin_platform` 依赖、不跨仓库 import、不拷贝参考文件。U7 的 7.1/7.2 把它变成 CI 可查。
8. **每个单元开工前必须 rebase 到最新 main**——本包与 `m1-intake-quality-fixes` 同期在跑，`app/graph/nodes.py` 是两批共同的最热文件。

**第 7 条在 U2 的验证方式**：`git diff` 里 `requirements.txt` 与 `pyproject.toml` 必须为空，`grep -rn "zhuopin_platform" app/ tests/` 零命中。模块名、类名、字段名均按招聘领域自建（`DecisionEvent` ≠ 平台的 `AuditEvent`，`AuditRecorder` ≠ `AuditLogger`），**不出现 `scenario` / `automation_level` / `oem_context` / `override_reason` 这四个平台字段**（design「参考边界」表）。

### 本单元的依赖（`delivery-units.md` §2.U2，逐字）

> 只依赖 U1 的三张表与 `app/storage/db.py` 的连接约定（`SqliteSink.write` 不自行 `commit`，与 `effect_persist_draft` 同一约定，见 `nodes.py:61` 那段注释）。
> 全新目录，此时**尚未接线**，现有行为完全不变。与整个仓库零文件重叠——本批里最干净的一个单元，可与 M1 的任何单元并行。

---

## 开工前置（必做，5 分钟）

- [ ] **rebase 到最新 main**（§4 约定 8）。

```bash
git pull --rebase origin main
```

- [ ] **确认 U1 已合并**。U2 的全部测试都建在 U1 的三张表上，U1 没合并就没有可以跑的东西——此时**停下并登记「⏸ 留步：U1 未合并」**，不要自己在 U2 里补一份建表 DDL（那会与 U1 撞车，且让 U1 的老库回归守护失效）。

```bash
grep -c "CREATE TABLE IF NOT EXISTS analysis_run" app/storage/db.py      # 期望 1
grep -c "CREATE TABLE IF NOT EXISTS criterion_score" app/storage/db.py   # 期望 1
grep -c "CREATE TABLE IF NOT EXISTS pending_approval" app/storage/db.py  # 期望 1
```

- [ ] **确认三张表的实际列名与本计划一致**（U1 的 reviewer 可能调整过命名，以库为准而非以本文为准）：

```bash
./venv/bin/python - <<'PY'
import tempfile, os
from app.storage.db import get_connection, init_schema
path = os.path.join(tempfile.mkdtemp(), "probe.db")
conn = get_connection(path); init_schema(conn)
for table in ("analysis_run", "criterion_score", "pending_approval"):
    cols = [(r[1], "NOT NULL" if r[3] else "NULL") for r in conn.execute(f"PRAGMA table_info({table})")]
    print(table, cols, sep="\n  ")
PY
```

预期 `analysis_run` 的 NOT NULL 列**恰好**是 `id` / `configured_model` / `prompt_version` / `temperature` / `input_hash` / `raw_response` / `created_at` 七列。**对不上就停下登记**，不要改表。

- [ ] **确认 `app/audit/` 不存在**（本单元是全新目录，存在即说明有人已经动过）：

```bash
ls app/audit 2>&1 | head -2      # 期望 "No such file or directory"
```

- [ ] **取基线**：全量测试必须全绿，记下数字。

```bash
./venv/bin/python -m pytest -q 2>&1 | tail -2
```

预期：U1 合并后为 `275 passed`（U1 计划的口径；以实测为准）。本单元合并后应为 **基线 + 90**（2026-08-26 提取验证实测）。

---

## 明确的范围边界（U2 **不做**什么）

| 不做 | 归属 |
|---|---|
| 改 `app/storage/db.py`（任何 DDL） | U1 已完成；U2 **一个字节都不碰** |
| 改 `app/config.py`、在 audit 模块里读配置 | U1 已加齐配置键；消费在 U3（§4 约定 1） |
| `AuditHook` Protocol 扩参、`RecorderAuditHook`、`app/main.py:_gateway_factory()` 注入 | U3（3.1–3.3） |
| `criterion_key` 白名单的 Python 定义与拒写逻辑 | U3（3.4） |
| `app/outbound/`、`compute_outbound_gate`、`GateDecision` | U4 |
| `queue.py`、两个新 `effect_*` 节点、外发路径分流 | U5 |
| `assertions.py`、三条合规断言、CI 接入、按 `message_type` 的拦截统计 | U6（6.1–6.6） |
| 运维文档、技术债登记 | U7（7.3/7.5/7.6） |
| 跨进程写锁 | design Non-Goals：只做进程内互斥，多进程留给 M2 的 Postgres |
| JSONL 按月切分、体积优化 | design Risks：M1/M2 量级吃得住，不提前优化 |

**U2 合并后系统的可观察行为必须与合并前完全一致**：`app/audit/` 没有任何调用方，JSONL 文件在生产里根本不会被创建。这是本单元"可独立合并"的定义。

---

## File Structure

| 文件 | 动作 | 责任 |
|---|---|---|
| `app/audit/__init__.py` | 新建（Task 1 建，逐 Task 追加导出） | 公开 `DecisionEvent` / `CriterionScore` / `AuditSink` / `SqliteSink` / `JsonlChainSink` / `ChainVerification` / `Reconciliation` / `AuditRecorder` |
| `app/audit/events.py` | 新建（Task 1） | `DecisionEvent`、`CriterionScore`、事件类型白名单、`to_dict()` |
| `app/audit/sinks.py` | 新建（Task 2 建，Task 3/4 追加） | `AuditSink` Protocol、`SqliteSink`、`JsonlChainSink`、`verify_chain()` |
| `app/audit/recorder.py` | 新建（Task 5） | `AuditRecorder` 两段式入口、`query_by` / `verify_integrity` / `reconcile` / `backfill` |
| `tests/test_audit_events.py` | 新建（Task 1） | 事件模型：字段白名单、空 `error` 剔除、禁存原文守护 |
| `tests/test_audit_sinks_sqlite.py` | 新建（Task 2） | 真身 sink：十四列、不 commit、主键短路、CHECK 不吞 |
| `tests/test_audit_chain.py` | 新建（Task 3 建，Task 4 追加） | 镜像 sink：写入侧 + `verify_chain()` 四攻击场景 + 序列化鲁棒性 + 并发 |
| `tests/test_audit_recorder.py` | 新建（Task 5 建，Task 6 追加） | 两段式守护、事务归属、双写故障语义、对账与补录 |

**为什么测试分四个文件**：`verify_chain()` 的攻击场景与写入侧同生共死（写入侧的 `prev_hash` 规则一改，校验规则必须同步改，§3.4），必须同文件；而两段式守护与双写故障语义是 `AuditRecorder` 的性质，放进 sink 的测试文件会让"这个约束属于谁"变模糊。

---

### Task 1: `DecisionEvent` 领域事件与「禁存原文 / 禁作训练输入」的结构守护

**Files:**
- Create: `app/audit/__init__.py`
- Create: `app/audit/events.py`
- Test: `tests/test_audit_events.py`

**Interfaces:**
- Consumes: 无（纯标准库）
- Produces: `DecisionEvent`（frozen dataclass）、`CriterionScore`（frozen dataclass）、常量 `AI_ANALYSIS` / `OUTBOUND_BLOCKED` / `OUTBOUND_DELIVERED` / `BACKFILL` / `EVENT_TYPES`。`DecisionEvent.to_dict() -> dict[str, Any]`，JSON 可序列化，**空 `error` 字段被剔除**（tasks 2.1）。Task 2 的 `SqliteSink` 与 Task 3 的 `JsonlChainSink` 都只消费 `to_dict()` 与字段本身。

- [ ] **Step 1: Write the failing test**

新建 `tests/test_audit_events.py`：

```python
import json
from dataclasses import FrozenInstanceError, fields

import pytest

from app.audit.events import (
    AI_ANALYSIS,
    BACKFILL,
    EVENT_TYPES,
    OUTBOUND_BLOCKED,
    CriterionScore,
    DecisionEvent,
)

# to_dict() 的键集合。这份清单是"留痕里允许出现什么"的白名单——加字段必须先
# 改这里，于是加字段这个动作本身会被 review 看见。
EXPECTED_KEYS = {
    "id",
    "event_type",
    "thread_id",
    "created_at",
    "application_id",
    "job_id",
    "configured_model",
    "response_model",
    "system_fingerprint",
    "prompt_version",
    "temperature",
    "input_hash",
    "rubric_version",
    "rubric_snapshot",
    "raw_response",
    "token_usage",
    "latency_ms",
    "scores",
    "message_type",
    "recipient",
    "content_hash",
    "blocked_reason",
    "confirmed_by",
    "evidence",
    "backfill_of",
    "error",
}


def _analysis_event(**overrides) -> DecisionEvent:
    payload = {
        "id": "thread-1:effect_record_analysis:sha256:abc",
        "event_type": AI_ANALYSIS,
        "thread_id": "thread-1",
        "application_id": "app-1",
        "job_id": "job-1",
        "configured_model": "deepseek-chat",
        "response_model": "deepseek-chat-241226",
        "system_fingerprint": "fp_abc",
        "prompt_version": "score-v1",
        "temperature": 0.0,
        "input_hash": "sha256:abc",
        "rubric_version": "ecu-embedded-v2",
        "rubric_snapshot": {"criteria": [{"key": "autosar", "weight": 0.4}]},
        "raw_response": '{"scores": []}',
        "token_usage": {"total_tokens": 128},
        "latency_ms": 812.5,
        "scores": (
            CriterionScore(criterion_key="autosar", score=3.0, evidence_ref="resume-1#120-180"),
        ),
    }
    payload.update(overrides)
    return DecisionEvent(**payload)


@pytest.mark.parametrize("bad_type", ["", "unknown_kind"])
def test_decision_event_rejects_unregistered_event_type(bad_type):
    """
    未登记的事件类型立刻抛，不留给下游 sink 去猜。fail-loud 与门禁的
    fail-closed 同一口径：未知就是错，不是默认值。
    """
    with pytest.raises(ValueError, match="未登记的事件类型"):
        _analysis_event(event_type=bad_type)


def test_all_registered_event_types_construct():
    for event_type in EVENT_TYPES:
        assert _analysis_event(event_type=event_type).event_type == event_type


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_to_dict_drops_empty_error_field(empty):
    """tasks 2.1：to_dict() 剔除空 error 字段。"""
    payload = _analysis_event(error=empty).to_dict()

    assert "error" not in payload


def test_to_dict_keeps_non_empty_error():
    payload = _analysis_event(error="镜像 append 失败").to_dict()

    assert payload["error"] == "镜像 append 失败"


def test_to_dict_keeps_fields_that_are_none():
    """
    ⚠️ 只有 error 被剔除，其余 None 一律保留。spec「供应商不返回部署指纹」要求
    该字段"记为空值、留痕照常写入"——把 None 一并剔掉，镜像里就分不清"这次调用
    没拿到指纹"和"这个版本的代码还没有指纹这个概念"。
    """
    payload = _analysis_event(system_fingerprint=None).to_dict()

    assert "system_fingerprint" in payload
    assert payload["system_fingerprint"] is None


def test_to_dict_keys_are_exactly_the_whitelist():
    assert set(_analysis_event(error="x").to_dict()) == EXPECTED_KEYS


@pytest.mark.parametrize("token", ["resume", "cv_text", "input_text", "raw_input", "plaintext"])
def test_no_field_name_smells_like_resume_plaintext(token):
    """
    spec「AI 调用的可复现留痕」：系统 MUST NOT 在留痕记录中存储简历原文。
    将来有人为"方便排查"加一个 input_text 字段，这条立刻变红。
    raw_response 是铁律 3 明令要存的模型响应，不在此列。
    """
    names = {field.name for field in fields(DecisionEvent)}
    assert not any(token in name for name in names), f"字段名疑似承载原文: {token}"


def test_scores_serialise_as_list_of_dicts():
    payload = _analysis_event().to_dict()

    assert payload["scores"] == [
        {"id": None, "criterion_key": "autosar", "score": 3.0, "evidence_ref": "resume-1#120-180"}
    ]


def test_scores_are_normalised_to_a_tuple():
    """传 list 也要变成 tuple——frozen dataclass 里挂一个可变列表是个陷阱。"""
    event = _analysis_event(scores=[CriterionScore("autosar", 3.0, "resume-1#1-2")])

    assert isinstance(event.scores, tuple)


def test_event_is_frozen():
    with pytest.raises(FrozenInstanceError):
        _analysis_event().id = "tampered"


def test_to_dict_is_json_serialisable_with_chinese():
    payload = _analysis_event(blocked_reason="外发总开关关闭\n第二行").to_dict()

    text = json.dumps(payload, ensure_ascii=False)
    assert "外发总开关关闭" in text
    assert json.loads(text)["blocked_reason"] == "外发总开关关闭\n第二行"


def test_events_module_carries_no_training_use_marker():
    """
    spec「留痕数据的用途限制」：该限制 SHALL 在数据结构层面以显式标注体现。
    U1 已在 analysis_run 的表注释里写过一遍；读代码的人和读 schema 的人不是
    同一批，模块层要再写一遍。
    """
    import app.audit.events as module

    doc = module.__doc__ or ""
    assert "禁止用作" in doc
    assert "训练" in doc
    assert "偏见" in doc  # 理由必须在场，不能只有一句禁令


def test_backfill_event_points_at_the_missing_record():
    event = DecisionEvent(
        id="backfill:run-1", event_type=BACKFILL, backfill_of="run-1", error="镜像缺行"
    )

    assert event.to_dict()["backfill_of"] == "run-1"


def test_outbound_event_carries_gate_evidence():
    event = DecisionEvent(
        id="thread-1:effect_record_outbound_audit:hash-1:False",
        event_type=OUTBOUND_BLOCKED,
        message_type="rejection_letter",
        blocked_reason="缺少 requires_confirmation",
        evidence={"requires_confirmation": None, "severity": ""},
    )

    assert event.to_dict()["evidence"] == {"requires_confirmation": None, "severity": ""}


def test_no_platform_vocabulary_leaked_into_the_event():
    """
    design「参考边界」：不拷贝参考文件。平台侧 AuditEvent 的四个字段一个都不许
    出现——字段名相同就是"照着抄了一遍"最直接的证据。
    """
    names = {field.name for field in fields(DecisionEvent)}
    assert names.isdisjoint({"scenario", "automation_level", "oem_context", "override_reason"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_audit_events.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.audit'`

- [ ] **Step 3: Write minimal implementation**

新建 `app/audit/events.py`：

```python
"""
留痕事件的领域模型。

design D2：按招聘领域自建字段，**不套**平台的 scenario / automation_level /
oem_context / override_reason。关键理由：套平台字段表就得把招聘语义塞进一个
payload 自由字典，而字典里的键**没法加数据库约束**——`evidence_ref` 为空必须
拒写这条就落不了地，工程铁律 4 直接失效。字段是一等公民还是字典键，决定了
约束能不能由存储层强制。

⚠️ **审计资产：本模块承载的数据禁止用作任何模型的训练、微调或调优输入。**
理由：历史评分与录用结果携带既有偏见，拿它当监督信号会把偏见放大并固化
（Amazon 2018 教训，见 CLAUDE.md 合规红线「绝不用历史录用结果做监督信号」）。
留痕只服务两件事：PIPL 第 24 条说明权（"这条评分是哪个模型、哪个版本、按哪份
rubric 打的，依据是简历里哪一段"）与 CI 里的合规断言。

⚠️ **本模块 MUST NOT 承载简历原文。** 输入只以 `input_hash` 形式记录，原文留在
简历主存储中按其自身访问控制管理（spec「AI 调用的可复现留痕」）。加字段前先想
一遍：它会不会把原文带进来。

⚠️ `confirmed_by` 现阶段**不可信**：鉴权是空壳（`AuthContext.user_id` 恒为
`None`），值只能由调用方传入（design D7）。SSO 落地后同一字段变可信，结构不改。
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

# ── 事件类型白名单 ──────────────────────────────────────────────────────
# 未登记的类型在构造时就抛，不留给下游 sink 去猜。与门禁的 fail-closed 同一
# 口径：未知就是错，不是默认值。
AI_ANALYSIS = "ai_analysis"
OUTBOUND_BLOCKED = "outbound_blocked"
OUTBOUND_DELIVERED = "outbound_delivered"
BACKFILL = "backfill"

EVENT_TYPES = frozenset({AI_ANALYSIS, OUTBOUND_BLOCKED, OUTBOUND_DELIVERED, BACKFILL})


@dataclass(frozen=True)
class CriterionScore:
    """
    一条逐项评分。

    `evidence_ref` 是工程铁律 4 的落点，格式为"材料标识 + 位置区间"
    （如 `resume-1#120-180`），使人可以据此定位到简历原文或面试记录的具体片段。
    为空由数据库 `CHECK` 拒写（U1 已落），本层**不做**重复校验也**不做**兜底——
    应用层多一道"友好提示"就多一个把 IntegrityError 吞掉的地方。

    `id` 留空时由 `SqliteSink` 按 `{analysis_run_id}:{criterion_key}` 生成：
    确定性 id 让重放撞主键而不是插出第二行。
    """

    criterion_key: str
    score: float
    evidence_ref: str
    id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "criterion_key": self.criterion_key,
            "score": self.score,
            "evidence_ref": self.evidence_ref,
        }


@dataclass(frozen=True)
class DecisionEvent:
    """
    一条可留痕的决策事实。AI 评分与外发门禁共用这一个形状——
    `specs/outbound-approval-gate` 要求"留痕 MUST 使用与 AI 评分留痕相同的机制，
    落入同一份可校验的记录中"，两套事件模型就等于两条链。

    字段按用途分四组，跨组字段为 None 是常态而不是异常：一次 AI 评分不会有
    `message_type`，一次拦截不会有 `raw_response`。
    """

    # ── 通用 ──
    # id 由调用方按 `{thread_id}:{node_name}:{business_key}` 生成（tasks 2.2）,
    # 与幂等键同源，所以 thread_id 不另设数据库列。
    id: str
    event_type: str
    thread_id: str | None = None
    created_at: str | None = None  # 留空由数据库 datetime('now') 填
    application_id: str | None = None
    job_id: str | None = None

    # ── AI 评分侧（铁律 3 的逐字兑现）──
    configured_model: str | None = None  # 配置里写的名字
    response_model: str | None = None  # API 响应实际返回的名字，铁律 5：响应返回的才算
    system_fingerprint: str | None = None  # 供应商不返回时记空值，留痕照常写入
    prompt_version: str | None = None
    temperature: float | None = None
    input_hash: str | None = None  # ⚠️ 只存哈希，绝不存原文
    rubric_version: str | None = None
    rubric_snapshot: dict[str, Any] | None = None
    raw_response: str | None = None
    token_usage: dict[str, Any] | None = None
    latency_ms: float | None = None
    scores: tuple[CriterionScore, ...] = ()

    # ── 外发门禁侧（U4/U5 消费，U2 只提供形状）──
    message_type: str | None = None
    recipient: str | None = None
    content_hash: str | None = None
    blocked_reason: str | None = None
    confirmed_by: str | None = None  # ⚠️ 现阶段不可信，见模块 docstring
    evidence: dict[str, Any] | None = None  # 判定所依据字段的原始取值，含空值

    # ── 补录（design D1：镜像缺行走链尾补录，不插回原位）──
    backfill_of: str | None = None

    # ── 失败标注 ──
    error: str | None = None

    def __post_init__(self) -> None:
        if self.event_type not in EVENT_TYPES:
            raise ValueError(
                f"未登记的事件类型: {self.event_type!r}；已登记: {sorted(EVENT_TYPES)}"
            )
        # frozen dataclass 上挂一个可变列表是个陷阱：调用方后续 append 会静默
        # 改掉一个"不可变"对象。统一折成 tuple。
        object.__setattr__(self, "scores", tuple(self.scores))

    def to_dict(self) -> dict[str, Any]:
        """
        折成可 JSON 序列化的字典。**空 error 字段被剔除**（tasks 2.1）：正常事件
        里挂一个 `"error": null` 会让镜像里每一行都带着一个永远为空的键，读的人
        以为这里曾经出过错。

        ⚠️ 只剔 error。其余 None 一律保留——spec「供应商不返回部署指纹」要求该
        字段"记为空值、留痕照常写入"，一并剔掉就分不清"这次没拿到"和"这版代码
        还没这个概念"。
        """
        payload: dict[str, Any] = {
            field.name: getattr(self, field.name) for field in fields(self)
        }
        payload["scores"] = [score.to_dict() for score in self.scores]
        if not (self.error or "").strip():
            payload.pop("error")
        return payload
```

新建 `app/audit/__init__.py`：

```python
"""
AI 决策留痕。design D1：SQLite 为可查询真身，JSONL hash-chain 为防篡改镜像。

⚠️ 审计资产，禁止用作任何模型的训练/微调/调优输入——理由见 events.py 模块 docstring。

**本包不 import `app.config` 与 `app.graph`**：前者会让审计路径在启动时绑死配置、
并让 U3 的注入点不再是唯一一处，后者是反向依赖。路径与连接一律由调用方传入。
"""

from app.audit.events import (
    AI_ANALYSIS,
    BACKFILL,
    EVENT_TYPES,
    OUTBOUND_BLOCKED,
    OUTBOUND_DELIVERED,
    CriterionScore,
    DecisionEvent,
)

__all__ = [
    "AI_ANALYSIS",
    "BACKFILL",
    "EVENT_TYPES",
    "OUTBOUND_BLOCKED",
    "OUTBOUND_DELIVERED",
    "CriterionScore",
    "DecisionEvent",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_audit_events.py -q`
Expected: `22 passed`（2026-08-26 提取验证实测）

- [ ] **Step 5: Run the full suite（确认零影响）**

Run: `./venv/bin/python -m pytest -q 2>&1 | tail -2`
Expected: 基线 + 22，无 failed / error

- [ ] **Step 6: Commit**

```bash
git add app/audit/__init__.py app/audit/events.py tests/test_audit_events.py
git commit -m "feat(audit): DecisionEvent 领域事件，禁存原文与禁作训练输入的结构守护"
```

---

### Task 2: `AuditSink` Protocol 与 `SqliteSink`（真身，不 commit，主键短路精确到列）

**Files:**
- Modify: `app/audit/__init__.py`（追加导出）
- Create: `app/audit/sinks.py`
- Test: `tests/test_audit_sinks_sqlite.py`

**Interfaces:**
- Consumes: U1 的 `analysis_run` / `criterion_score` 两张表；`app.storage.db.get_connection` / `init_schema`（仅测试用，实现里不 import——连接由调用方传入）
- Produces: `AuditSink` Protocol（`write(event) -> bool` / `read_all() -> list[dict]`）、`SqliteSink(conn)`，额外提供 `query(**filters) -> list[dict]`。Task 5 的 `AuditRecorder` 以 `SqliteSink` 为「真身」sink。

**⚠️ 相对 `tasks.md` 2.2 的两处偏离（已登记，需 reviewer 确认）**：

1. **`rubric_version` 与 `rubric_snapshot` 在 SQLite 侧合并落进 `rubric_snapshot` 一列**，形态为 `{"version": <rubric_version>, "snapshot": <rubric_snapshot>}`，`read_all()` 拆回两个字段（round-trip 无损，有测试锁）。理由：tasks 2.1 把 `rubric_version` 列为一等字段，而 U1 的 `analysis_run` 没有对应列；**U2 ⛔ 不改 U1 的表**——跨单元改表会让 U1 的老库回归守护失效，且 `db.py` 一旦被 U2 写就与 U4 共写同一文件、§5 的并行作废。spec 要求的是"所用 rubric 的**完整快照**"，版本是快照的属性，合并落盘完整满足。将来若 U6 需要按版本检索，由那时的变更加列。
2. **`AuditSink.write` 返回 `bool`（是否真的落了一行），不是 `None`。** 主键冲突短路（tasks 2.2 逐字："主键冲突即视为已写入，短路返回"）需要一个能被调用方与测试观察到的信号；返回 `None` 的话"已写过"和"写成功"在调用点长得一模一样。

- [ ] **Step 1: Write the failing test**

新建 `tests/test_audit_sinks_sqlite.py`：

```python
import sqlite3

import pytest

from app.audit.events import BACKFILL, OUTBOUND_BLOCKED, CriterionScore, DecisionEvent, AI_ANALYSIS
from app.audit.sinks import SqliteSink
from app.storage.db import get_connection, init_schema


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "audit.db")


@pytest.fixture
def conn(db_path):
    c = get_connection(db_path)
    init_schema(c)
    return c


@pytest.fixture
def sink(conn):
    return SqliteSink(conn)


def _event(**overrides) -> DecisionEvent:
    payload = {
        "id": "thread-1:effect_record_analysis:sha256:abc",
        "event_type": AI_ANALYSIS,
        "thread_id": "thread-1",
        "application_id": "app-1",
        "job_id": "job-1",
        "configured_model": "deepseek-chat",
        "response_model": "deepseek-chat-241226",
        "system_fingerprint": "fp_abc",
        "prompt_version": "score-v1",
        "temperature": 0.0,
        "input_hash": "sha256:abc",
        "rubric_version": "ecu-embedded-v2",
        "rubric_snapshot": {"criteria": [{"key": "autosar", "weight": 0.4}]},
        "raw_response": '{"scores": [3]}',
        "token_usage": {"total_tokens": 128},
        "latency_ms": 812.5,
        "scores": (
            CriterionScore(criterion_key="autosar", score=3.0, evidence_ref="resume-1#120-180"),
            CriterionScore(criterion_key="can_bus", score=2.0, evidence_ref="resume-1#300-360"),
        ),
    }
    payload.update(overrides)
    return DecisionEvent(**payload)


def _run_row(conn, run_id="thread-1:effect_record_analysis:sha256:abc"):
    cursor = conn.execute("SELECT * FROM analysis_run WHERE id = ?", (run_id,))
    columns = [c[0] for c in cursor.description]
    row = cursor.fetchone()
    return dict(zip(columns, row)) if row else None


# ── 写入：铁律 3 的十四列 ────────────────────────────────────────────────


def test_write_persists_the_analysis_run(sink, conn):
    assert sink.write(_event()) is True

    row = _run_row(conn)
    assert row["configured_model"] == "deepseek-chat"
    assert row["prompt_version"] == "score-v1"
    assert row["temperature"] == 0.0
    assert row["input_hash"] == "sha256:abc"
    assert row["raw_response"] == '{"scores": [3]}'
    assert row["latency_ms"] == 812.5
    assert row["created_at"]  # 留空由 datetime('now') 填


def test_configured_and_response_model_are_kept_apart(sink, conn):
    """铁律 5：配置里写的名字不算数，响应返回的才算——两个字段各自保存。"""
    sink.write(_event(configured_model="deepseek-chat", response_model="deepseek-chat-250801"))

    row = _run_row(conn)
    assert row["configured_model"] == "deepseek-chat"
    assert row["response_model"] == "deepseek-chat-250801"


@pytest.mark.parametrize("column", ["response_model", "system_fingerprint"])
def test_missing_vendor_fields_are_stored_as_null(sink, conn, column):
    """
    spec「供应商不返回部署指纹」：该字段记为空值，留痕照常写入，
    **留痕流程不因字段缺失而失败**。断言的是写入成功，不是抛异常。
    """
    assert sink.write(_event(**{column: None})) is True

    assert _run_row(conn)[column] is None


def test_rubric_version_and_snapshot_round_trip(sink):
    """
    偏离登记 1：两者合并落进 rubric_snapshot 一列，read_all() 必须无损拆回。
    """
    sink.write(_event())

    record = sink.read_all()[0]
    assert record["rubric_version"] == "ecu-embedded-v2"
    assert record["rubric_snapshot"] == {"criteria": [{"key": "autosar", "weight": 0.4}]}


def test_scores_get_deterministic_ids(sink, conn):
    sink.write(_event())

    rows = conn.execute(
        "SELECT id, criterion_key, evidence_ref FROM criterion_score ORDER BY criterion_key"
    ).fetchall()
    assert [row[0] for row in rows] == [
        "thread-1:effect_record_analysis:sha256:abc:autosar",
        "thread-1:effect_record_analysis:sha256:abc:can_bus",
    ]
    assert rows[0][2] == "resume-1#120-180"


# ── 事务归属：⛔ 不自行 commit ───────────────────────────────────────────


def test_write_does_not_commit(sink, db_path):
    """
    铁律 1：被 idempotent_effect 装饰的函数体里的写入，必须与装饰器追加的
    effect_log 行落在同一个事务里、由装饰器统一提交一次。这里先提交一次，
    进程在"这次提交"与"装饰器提交 effect_log"之间崩溃就会留下
    「业务写已落盘、幂等记录没落」——重放时撞主键，重试永久失败。
    实证见 docs/findings/2026-08-13-sqlite-事务归属冲突.md §8.5。
    """
    sink.write(_event())

    onlooker = get_connection(db_path)
    assert onlooker.execute("SELECT count(*) FROM analysis_run").fetchone()[0] == 0

    sink.conn.commit()
    assert onlooker.execute("SELECT count(*) FROM analysis_run").fetchone()[0] == 1


def test_rollback_undoes_the_whole_event(sink, conn):
    sink.write(_event())
    conn.rollback()

    assert conn.execute("SELECT count(*) FROM analysis_run").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM criterion_score").fetchone()[0] == 0


# ── 主键短路 vs. 约束失败：这一格写宽了，铁律 4 当场失效 ─────────────────


def test_duplicate_primary_key_short_circuits(sink, conn):
    """tasks 2.2：主键冲突即视为已写入，短路返回。"""
    assert sink.write(_event()) is True
    assert sink.write(_event()) is False

    assert conn.execute("SELECT count(*) FROM analysis_run").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM criterion_score").fetchone()[0] == 2


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_empty_evidence_ref_is_not_swallowed(sink, conn, blank):
    """
    铁律 4 由数据库 CHECK 强制（U1）。本层的唯一职责是**不吞异常**——
    把 `except sqlite3.IntegrityError: return False` 写宽一格，铁律 4 就从
    "数据库强制"退回"静默放过"，而所有正常用例照样全绿。
    """
    event = _event(scores=(CriterionScore("autosar", 3.0, blank),))

    with pytest.raises(sqlite3.IntegrityError):
        sink.write(event)


def test_failed_score_leaves_no_orphan_run(sink, conn):
    """
    一半写成功不是可接受的结果：analysis_run 落了、criterion_score 没落，
    审计上就是"有一次调用、但一条评分都没打"，与"根本没调用"无法区分。
    回滚由调用方的事务承担（本层不 commit 正是为了这一点）。
    """
    with pytest.raises(sqlite3.IntegrityError):
        sink.write(_event(scores=(CriterionScore("autosar", 3.0, ""),)))
    conn.rollback()

    assert conn.execute("SELECT count(*) FROM analysis_run").fetchone()[0] == 0


@pytest.mark.parametrize("column", ["prompt_version", "temperature", "input_hash"])
def test_missing_reproducibility_column_raises(sink, column):
    """
    铁律 3 的七个 NOT NULL 列缺一不可，缺了就该炸——静默写一行"复现不了的留痕"
    比不写更糟：它看起来像有记录。
    """
    with pytest.raises(sqlite3.IntegrityError):
        sink.write(_event(**{column: None}))


# ── 不属于本 sink 的事件类型 ─────────────────────────────────────────────


@pytest.mark.parametrize("event_type", [OUTBOUND_BLOCKED, BACKFILL])
def test_non_analysis_events_have_no_body_in_this_sink(sink, conn, event_type):
    """
    外发事件的 SQLite 真身是 pending_approval（U5 的 queue.py 写），补录事件只
    存在于镜像链上。本 sink ⛔ 不替它们凭空造一张表，返回 False 表示"这里没有
    它的真身"，让调用方（AuditRecorder）与对账（U6 6.4）都能看见这个事实。
    """
    assert sink.write(_event(event_type=event_type)) is False
    assert conn.execute("SELECT count(*) FROM analysis_run").fetchone()[0] == 0


# ── 读取与检索 ───────────────────────────────────────────────────────────


def test_read_all_nests_scores_under_their_run(sink):
    sink.write(_event())

    records = sink.read_all()
    assert len(records) == 1
    assert {score["criterion_key"] for score in records[0]["scores"]} == {"autosar", "can_bus"}


def test_read_all_does_not_mutate_row_factory(sink, conn):
    """
    conn 是全应用共享的一条连接（db.get_connection 的注释）。在这里顺手设
    conn.row_factory = sqlite3.Row，会让所有按下标取值的既有代码静默改变行为。
    """
    before = conn.row_factory
    sink.read_all()

    assert conn.row_factory is before


def test_query_filters_by_application_and_time_range(sink, conn):
    sink.write(_event(id="run-a", application_id="app-1", created_at="2026-08-01 00:00:00"))
    sink.write(_event(id="run-b", application_id="app-2", created_at="2026-08-20 00:00:00"))
    sink.write(_event(id="run-c", application_id="app-1", created_at="2026-08-25 00:00:00"))

    hits = sink.query(application_id="app-1", created_from="2026-08-10 00:00:00")
    assert [record["id"] for record in hits] == ["run-c"]


def test_query_by_model_identifier(sink):
    sink.write(_event(id="run-a", response_model="deepseek-chat-241226"))
    sink.write(_event(id="run-b", response_model="deepseek-chat-250801"))

    hits = sink.query(response_model="deepseek-chat-250801")
    assert [record["id"] for record in hits] == ["run-b"]


@pytest.mark.parametrize(
    "bad_key",
    ["id = '' OR 1 = 1 --", "raw_response"],
)
def test_query_rejects_keys_outside_the_whitelist(sink, bad_key):
    """
    列名不能参数化，只能拼进 SQL——所以列名必须走白名单，不是"看起来像列名就放行"。
    raw_response 被排除是刻意的：按响应全文检索会全表扫，且没有审计场景需要它。
    """
    with pytest.raises(ValueError, match="不可检索的字段"):
        sink.query(**{bad_key: "x"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_audit_sinks_sqlite.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.audit.sinks'`

- [ ] **Step 3: Write minimal implementation**

新建 `app/audit/sinks.py`：

```python
"""
留痕的两个 sink。design D1：SQLite 为可查询真身，JSONL hash-chain 为防篡改镜像。

**两者互为独立证据**才是这个组合的意义——SQLite 行可被 UPDATE / DELETE，有写
权限的人可以从被改那行往后全部重算 prev_hash 让链照样通过；append-only 文件的
攻击面小得多，且与库文件是两套介质，同时改两处才能无痕。

⛔ 本模块不 import `app.config` / `app.graph`：路径与连接一律由调用方传入。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Protocol

from app.audit.events import AI_ANALYSIS, EVENT_TYPES, CriterionScore, DecisionEvent


class AuditSink(Protocol):
    """一个留痕落点。write 返回"是否真的落了一行"，read_all 返回原始记录字典。"""

    def write(self, event: DecisionEvent) -> bool: ...

    def read_all(self) -> list[dict[str, Any]]: ...


_ANALYSIS_RUN_COLUMNS = (
    "id",
    "application_id",
    "job_id",
    "configured_model",
    "response_model",
    "system_fingerprint",
    "prompt_version",
    "temperature",
    "input_hash",
    "rubric_snapshot",
    "raw_response",
    "token_usage",
    "latency_ms",
)


def _rows_as_dicts(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    """
    ⚠️ 刻意不设 `conn.row_factory`：conn 是全应用共享的一条连接
    （`app/storage/db.py:get_connection` 的注释），在这里顺手换掉 row_factory
    会让所有按下标取值的既有代码静默改变行为。
    """
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _dumps(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def _loads(raw: str | None) -> Any:
    return None if raw is None else json.loads(raw)


class SqliteSink:
    """
    留痕的真身。写 U1 的 `analysis_run` 与 `criterion_score`。

    ⛔ **不自行 `commit`。** 与 `effect_persist_draft` 同一约定（见
    `app/graph/nodes.py` 那段「不在这里 conn.commit()」的注释）：被
    `idempotent_effect` 装饰的函数体里的写入，必须与装饰器追加的 `effect_log`
    行落在同一个事务里、由装饰器统一提交一次（工程铁律 1）。这里先提交一次，
    进程在"这次提交"与"装饰器提交 effect_log"之间崩溃就会留下「业务写已落盘、
    幂等记录没落」，重放撞主键、重试永久失败。

    连接由调用方传入并**绑定在实例上**：`AuditRecorder.record()` 会断言调用点
    传进来的 conn 与这里绑定的是同一个对象，把"同一连接、同一个 BEGIN"这条不
    变式变成调用点上一句测得到的断言。
    """

    SUPPORTED_EVENT_TYPES = frozenset({AI_ANALYSIS})

    # 可检索的列白名单。列名不能参数化、只能拼进 SQL，所以必须走白名单而不是
    # "看起来像列名就放行"。raw_response 刻意排除：全文检索会全表扫，且没有
    # 审计场景需要它。
    FILTERABLE = frozenset(
        {
            "id",
            "application_id",
            "job_id",
            "configured_model",
            "response_model",
            "system_fingerprint",
            "prompt_version",
            "input_hash",
        }
    )

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ── 写 ──────────────────────────────────────────────────────────────

    def write(self, event: DecisionEvent) -> bool:
        if event.event_type not in EVENT_TYPES:
            raise ValueError(f"未登记的事件类型: {event.event_type!r}")
        if event.event_type not in self.SUPPORTED_EVENT_TYPES:
            # 外发事件的 SQLite 真身是 pending_approval（U5 的 queue.py 写），
            # 补录事件只存在于镜像链上。这里 ⛔ 不替它们凭空造一张表——返回
            # False 让调用方与对账（U6 6.4）都看得见"这里没有它的真身"。
            return False

        try:
            self.conn.execute(
                f"INSERT INTO analysis_run ({', '.join(_ANALYSIS_RUN_COLUMNS)}, created_at) "
                f"VALUES ({', '.join('?' * len(_ANALYSIS_RUN_COLUMNS))}, "
                f"COALESCE(?, datetime('now')))",
                (
                    event.id,
                    event.application_id,
                    event.job_id,
                    event.configured_model,
                    event.response_model,
                    event.system_fingerprint,
                    event.prompt_version,
                    event.temperature,
                    event.input_hash,
                    self._pack_rubric(event),
                    event.raw_response,
                    _dumps(event.token_usage),
                    event.latency_ms,
                    event.created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            # ⚠️ 只短路"这条 run 已经写过"这一种情形。把 except 写宽一格
            # （`except sqlite3.IntegrityError: return False`），evidence_ref
            # 的 CHECK 失败就会被当成"已写入"静默放过——铁律 4 当场从"数据库
            # 强制"退回"静默放过"，而所有正常用例照样全绿。
            if not _is_analysis_run_pk_conflict(exc):
                raise
            return False

        for score in event.scores:
            self.conn.execute(
                "INSERT INTO criterion_score "
                "(id, analysis_run_id, criterion_key, score, evidence_ref, created_at) "
                "VALUES (?, ?, ?, ?, ?, COALESCE(?, datetime('now')))",
                (
                    score.id or f"{event.id}:{score.criterion_key}",
                    event.id,
                    # ⛔ 这里**不做** criterion_key 白名单校验：白名单必须集中在
                    # 一处 Python 定义里（U3 的 3.4），散成两处会出现"一处放行
                    # 一处拒绝"的分叉，而分叉的那一侧就是红线的缺口。
                    score.criterion_key,
                    score.score,
                    score.evidence_ref,
                    event.created_at,
                ),
            )
        return True

    @staticmethod
    def _pack_rubric(event: DecisionEvent) -> str | None:
        """
        rubric_version 与 rubric_snapshot 合并落进一列（偏离登记 1）：U1 的
        analysis_run 没有 version 列，而 U2 ⛔ 不改 U1 的表。spec 要的是"所用
        rubric 的完整快照"，版本是快照的属性，合并落盘完整满足且 round-trip 无损。
        """
        if event.rubric_version is None and event.rubric_snapshot is None:
            return None
        return _dumps({"version": event.rubric_version, "snapshot": event.rubric_snapshot})

    # ── 读 ──────────────────────────────────────────────────────────────

    def read_all(self) -> list[dict[str, Any]]:
        return self.query()

    def query(
        self,
        *,
        created_from: str | None = None,
        created_to: str | None = None,
        **filters: Any,
    ) -> list[dict[str, Any]]:
        """
        spec「留痕可查询」：按业务关联标识、时间区间、模型标识等维度检索。
        """
        clauses: list[str] = []
        params: list[Any] = []
        for key, value in filters.items():
            if key not in self.FILTERABLE:
                raise ValueError(
                    f"不可检索的字段: {key!r}；可用: {sorted(self.FILTERABLE)}"
                )
            clauses.append(f"{key} = ?")
            params.append(value)
        if created_from is not None:
            clauses.append("created_at >= ?")
            params.append(created_from)
        if created_to is not None:
            clauses.append("created_at <= ?")
            params.append(created_to)

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        runs = _rows_as_dicts(
            self.conn.execute(
                f"SELECT * FROM analysis_run{where} ORDER BY created_at, id", params
            )
        )
        if not runs:
            return []

        scores = _rows_as_dicts(
            self.conn.execute("SELECT * FROM criterion_score ORDER BY criterion_key")
        )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in scores:
            grouped.setdefault(row["analysis_run_id"], []).append(row)

        for run in runs:
            packed = _loads(run.pop("rubric_snapshot"))
            run["rubric_version"] = (packed or {}).get("version")
            run["rubric_snapshot"] = (packed or {}).get("snapshot")
            run["token_usage"] = _loads(run["token_usage"])
            run["scores"] = grouped.get(run["id"], [])
        return runs


def _is_analysis_run_pk_conflict(exc: sqlite3.IntegrityError) -> bool:
    message = str(exc)
    return "UNIQUE constraint failed" in message and "analysis_run.id" in message
```

`app/audit/__init__.py` 追加：

```python
from app.audit.sinks import AuditSink, SqliteSink
```

并把 `"AuditSink"`, `"SqliteSink"` 加进 `__all__`。

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_audit_sinks_sqlite.py -q`
Expected: `24 passed`（实测）

- [ ] **Step 5: Commit**

```bash
git add app/audit/__init__.py app/audit/sinks.py tests/test_audit_sinks_sqlite.py
git commit -m "feat(audit): SqliteSink 写 analysis_run/criterion_score，不自行 commit"
```

---

### Task 3: `JsonlChainSink` 写入侧（二进制 append、按路径共享锁与游标）

**Files:**
- Modify: `app/audit/sinks.py`（追加 `JsonlChainSink` 写入侧与 `ChainVerification`）
- Modify: `app/audit/__init__.py`
- Test: `tests/test_audit_chain.py`（新建）

**Interfaces:**
- Consumes: `DecisionEvent.to_dict()`
- Produces: `JsonlChainSink(path)`，`write(event) -> bool` / `read_all() -> list[dict]`；常量 `GENESIS_PREV_HASH`。`verify_chain()` 在 Task 4 补齐（本 Task 先只写入）。

**三个容易做错的点**：

1. **必须二进制 I/O**（`"ab"` / `"rb"`）。文本模式在 Windows 上会把 `"\n"` 翻译成 `\r\n` 落盘——链的定义是"上一行落盘字节的 SHA-256"，写入侧算不含 `\r` 的字节、校验侧读回含 `\r` 的字节，**在 Mac 上全绿、推到 `.51` 上整条链立刻报断**。部署约束 4：目标服务器是 Windows。
2. **锁注册表本身要加锁**。`_LOCKS.setdefault(key, threading.Lock())` 看起来够用，但两个线程同时进来会各自构造一把 Lock，`setdefault` 只有一把胜出——败者拿着自己那把锁去 append，互斥当场失效。用一把类级 `_REGISTRY_LOCK` 守住注册表。
3. **游标缺失时从磁盘末行重算，⛔ 不当 genesis**（design D3 配套细节，tasks 2.3 逐字）。当成 genesis 的话，进程重启后写的第一行会把 `prev_hash` 填成 64 个 0，而它前面明明有内容——链从那一行起永久断裂，且**写入时不报错**，要等到某次 `verify_chain()` 才发现，那时已经过去很久。

- [ ] **Step 1: Write the failing test**

新建 `tests/test_audit_chain.py`：

```python
import hashlib
import json
import threading

import pytest

from app.audit.events import AI_ANALYSIS, OUTBOUND_BLOCKED, DecisionEvent
from app.audit.sinks import GENESIS_PREV_HASH, JsonlChainSink


@pytest.fixture
def chain_path(tmp_path):
    return tmp_path / "audit" / "decisions.jsonl"


@pytest.fixture(autouse=True)
def _clear_class_state():
    """
    锁与游标是类级共享状态（按解析后的绝对路径）。tmp_path 每个用例都不同，
    理论上不会串；显式清一遍是为了让"游标缺失走磁盘重算"这条路径可被主动构造。
    """
    yield
    JsonlChainSink._CURSORS.clear()
    JsonlChainSink._LOCKS.clear()


def _event(index: int, **overrides) -> DecisionEvent:
    payload = {
        "id": f"run-{index}",
        "event_type": AI_ANALYSIS,
        "thread_id": "thread-1",
        "configured_model": "deepseek-chat",
        "prompt_version": "score-v1",
        "temperature": 0.0,
        "input_hash": f"sha256:{index}",
        "raw_response": "{}",
    }
    payload.update(overrides)
    return DecisionEvent(**payload)


def _lines(path) -> list[bytes]:
    return [line for line in path.read_bytes().split(b"\n") if line]


def _objects(path) -> list[dict]:
    return [json.loads(line.decode("utf-8")) for line in _lines(path)]


def test_write_returns_true_and_creates_parent_directory(chain_path):
    sink = JsonlChainSink(chain_path)

    assert sink.write(_event(1)) is True
    assert chain_path.exists()


def test_first_line_carries_the_genesis_sentinel(chain_path):
    JsonlChainSink(chain_path).write(_event(1))

    assert _objects(chain_path)[0]["prev_hash"] == GENESIS_PREV_HASH


def test_second_line_prev_hash_is_sha256_of_the_first_line_bytes(chain_path):
    sink = JsonlChainSink(chain_path)
    sink.write(_event(1))
    sink.write(_event(2))

    lines = _lines(chain_path)
    assert _objects(chain_path)[1]["prev_hash"] == hashlib.sha256(lines[0]).hexdigest()


def test_file_uses_lf_only_no_crlf(chain_path):
    """
    部署约束 4：目标服务器是 Windows。文本模式会把 "\\n" 翻译成 \\r\\n 落盘，
    链在 Mac 上全绿、推到 .51 上整条报断。必须二进制 I/O。
    """
    sink = JsonlChainSink(chain_path)
    sink.write(_event(1))
    sink.write(_event(2))

    assert b"\r\n" not in chain_path.read_bytes()


def test_cursor_miss_recomputes_from_disk_tail(chain_path):
    """
    tasks 2.3 逐字：缓存缺失时**从磁盘末行重算**而非当 genesis。
    当成 genesis 的话，进程重启后第一行的 prev_hash 会是 64 个 0，链从那行起
    永久断裂，而且**写入时不报错**——要等某次 verify_chain() 才发现。
    """
    JsonlChainSink(chain_path).write(_event(1))
    JsonlChainSink._CURSORS.clear()  # 模拟进程重启

    JsonlChainSink(chain_path).write(_event(2))

    lines = _lines(chain_path)
    assert _objects(chain_path)[1]["prev_hash"] == hashlib.sha256(lines[0]).hexdigest()
    assert _objects(chain_path)[1]["prev_hash"] != GENESIS_PREV_HASH


def test_two_instances_alternating_keep_one_chain(chain_path):
    """design D3 配套细节：两个指向同一文件的 sink 实例交替写不断链。"""
    first, second = JsonlChainSink(chain_path), JsonlChainSink(chain_path)
    first.write(_event(1))
    second.write(_event(2))
    first.write(_event(3))

    lines = _lines(chain_path)
    objects = _objects(chain_path)
    for index in range(1, len(lines)):
        assert objects[index]["prev_hash"] == hashlib.sha256(lines[index - 1]).hexdigest()


def test_relative_and_absolute_paths_share_one_lock(tmp_path, monkeypatch):
    """
    锁与游标按**解析后的绝对路径**共享。按传进来的字符串共享的话，
    JsonlChainSink("data/x.jsonl") 与 JsonlChainSink("/abs/data/x.jsonl") 会拿到
    两把不同的锁，写同一个文件——互斥失效且不报错。
    """
    monkeypatch.chdir(tmp_path)
    relative = JsonlChainSink("audit/decisions.jsonl")
    absolute = JsonlChainSink(tmp_path / "audit" / "decisions.jsonl")

    assert relative._key == absolute._key


def test_concurrent_appends_do_not_interleave(chain_path):
    """tasks 2.7：多线程并发 append 同一文件，行不穿插。"""
    sink = JsonlChainSink(chain_path)
    errors: list[BaseException] = []

    def worker(base: int) -> None:
        try:
            for offset in range(10):
                sink.write(_event(base * 10 + offset))
        except BaseException as exc:  # noqa: BLE001 - 线程里的异常必须带回主线程
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    objects = _objects(chain_path)
    assert len(objects) == 80
    assert len({obj["id"] for obj in objects}) == 80
    lines = _lines(chain_path)
    for index in range(1, len(lines)):
        assert objects[index]["prev_hash"] == hashlib.sha256(lines[index - 1]).hexdigest()


def test_read_all_returns_every_line_including_prev_hash(chain_path):
    sink = JsonlChainSink(chain_path)
    sink.write(_event(1))
    sink.write(_event(2, event_type=OUTBOUND_BLOCKED, message_type="rejection_letter"))

    records = sink.read_all()
    assert [record["id"] for record in records] == ["run-1", "run-2"]
    assert records[1]["event_type"] == OUTBOUND_BLOCKED
    assert "prev_hash" in records[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_audit_chain.py -q`
Expected: FAIL —— `ImportError: cannot import name 'GENESIS_PREV_HASH' from 'app.audit.sinks'`

- [ ] **Step 3: Write minimal implementation**

`app/audit/sinks.py` 顶部导入追加 `hashlib` / `os` / `threading` / `dataclass` / `Path`，文件末尾追加：

```python
# 第一行的 prev_hash 哨兵。它是**写入侧的约定**，不是可校验的主张——第 1 行
# 没有前驱，拿什么和它比？verify_chain() 因此不校验第 1 行的取值，只校验
# 「第 2 行起必须有这个字段」（spec：仅第 1 条记录可豁免，向前兼容既有文件）。
GENESIS_PREV_HASH = "0" * 64


@dataclass(frozen=True)
class ChainVerification:
    ok: bool
    total: int
    broken_at: int | None = None
    error: str | None = None
    tail_hash: str | None = None


class JsonlChainSink:
    """
    留痕的防篡改镜像：append-only JSONL，每行嵌上一行**落盘字节**的 SHA-256。

    ⚠️ 只做**进程内**互斥（design Non-Goals：不做跨进程写锁）。当前部署形态是
    单个 Windows 计划任务拉起的单进程，假设成立；多进程部署会断链。M2 迁
    Postgres 时由数据库承担并发写，JSONL 若仍保留需改为单写入者或按进程分文件。
    技术债登记是 U7 的 7.6。

    锁与游标都是**类级、按解析后的绝对路径共享**：两个指向同一文件的实例必须
    用同一把锁、同一个游标，否则交替写就会断链。
    """

    _REGISTRY_LOCK = threading.Lock()
    _LOCKS: dict[str, threading.Lock] = {}
    _CURSORS: dict[str, str] = {}

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        # 按解析后的绝对路径做身份：按传进来的字符串做身份的话，
        # "data/x.jsonl" 与 "/abs/data/x.jsonl" 会拿到两把不同的锁，
        # 写的却是同一个文件——互斥失效且不报错。
        self._key = str(self.path.resolve())

    # ── 写 ──────────────────────────────────────────────────────────────

    def write(self, event: DecisionEvent) -> bool:
        return self._append(event.to_dict())

    def _append(self, payload: dict[str, Any]) -> bool:
        with self._lock_for(self._key):
            prev = self._CURSORS.get(self._key)
            if prev is None:
                # ⛔ 不当 genesis：游标缺失（进程重启、新实例）时必须从磁盘末行
                # 重算，否则重启后第一行的 prev_hash 会是 64 个 0，链从那行起
                # 永久断裂，而且**写入时不报错**（tasks 2.3）。
                prev = self._tail_digest() or GENESIS_PREV_HASH

            body = dict(payload)
            body["prev_hash"] = prev
            # sort_keys 让同一份内容的字节可复现；ensure_ascii=False 让中文按
            # UTF-8 原样落盘（链算的是字节，中文不需要转义成 \uXXXX）。
            # json.dumps 会把真实换行转义成 "\\n" 两个字符，所以一条记录永远
            # 占且只占一行——这是"按 b'\\n' 切行"成立的前提。
            line = json.dumps(
                body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")

            self.path.parent.mkdir(parents=True, exist_ok=True)
            # ⚠️ 必须二进制。文本模式在 Windows 上会把 "\n" 翻成 \r\n 落盘，
            # 链在 Mac 上全绿、推到 .51 上整条报断（部署约束 4）。
            with open(self.path, "ab") as handle:
                handle.write(line + b"\n")
                handle.flush()
                os.fsync(handle.fileno())

            self._CURSORS[self._key] = hashlib.sha256(line).hexdigest()
        return True

    @classmethod
    def _lock_for(cls, key: str) -> threading.Lock:
        # 注册表本身要加锁：setdefault(key, threading.Lock()) 会让两个并发线程
        # 各造一把 Lock、只有一把胜出，败者拿着自己那把去 append——互斥当场失效。
        with cls._REGISTRY_LOCK:
            lock = cls._LOCKS.get(key)
            if lock is None:
                lock = cls._LOCKS[key] = threading.Lock()
            return lock

    def _tail_digest(self) -> str | None:
        for line in reversed(self._raw_lines()):
            return hashlib.sha256(line).hexdigest()
        return None

    def _raw_lines(self) -> list[bytes]:
        if not self.path.exists():
            return []
        with open(self.path, "rb") as handle:
            return [line for line in handle.read().split(b"\n") if line.strip()]

    # ── 读 ──────────────────────────────────────────────────────────────

    def read_all(self) -> list[dict[str, Any]]:
        return [json.loads(line.decode("utf-8")) for line in self._raw_lines()]
```

`app/audit/__init__.py` 追加导出 `ChainVerification` / `GENESIS_PREV_HASH` / `JsonlChainSink`。

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_audit_chain.py -q`
Expected: `9 passed`（实测）

- [ ] **Step 5: Commit**

```bash
git add app/audit/sinks.py app/audit/__init__.py tests/test_audit_chain.py
git commit -m "feat(audit): JsonlChainSink 写入侧，二进制 append 与按路径共享的锁与游标"
```

---

### Task 4: `verify_chain()` —— 四个攻击场景与序列化鲁棒性（本单元含金量最高的一 Task）

**Files:**
- Modify: `app/audit/sinks.py`（`JsonlChainSink.verify_chain()`）
- Test: `tests/test_audit_chain.py`（追加）

**Interfaces:**
- Produces: `JsonlChainSink.verify_chain() -> ChainVerification`，返回 `ok` / `total` / `broken_at` / `error` / `tail_hash`（tasks 2.4）

**归属说明（§3.4）**：`verify_chain()` 是 `JsonlChainSink` 的**自校验方法**，与写入侧同生共死——写入侧的 `prev_hash` 计算规则一改，校验规则必须同步改。跨介质对账（SQLite ↔ JSONL 差集）是另一件事，在 Task 6 与 U6 的 6.4。**两者不可互相替代**：`verify_chain()` 只证明"链自身没被改"，证明不了"该留的痕都留了"。

**已知边界（登记，不在本单元解决）**：哈希链检不出**最后一行**被修改——它没有后继来暴露它。这是哈希链的固有性质，不是实现缺陷。`verify_chain()` 因此返回 `tail_hash`，让将来需要时可以把链尾锚定到外部（备份路径、只追加目录、或另一台机器）。spec 未要求，U2 不做。

- [ ] **Step 1: Write the failing test**

`tests/test_audit_chain.py` 追加：

```python
# ── verify_chain()：四个攻击场景 ─────────────────────────────────────────


def _rewrite(path, objects: list[dict]) -> None:
    """按给定对象重写整个文件（模拟攻击者持有写权限）。"""
    payload = b"\n".join(
        json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        for obj in objects
    )
    path.write_bytes(payload + b"\n")


def test_intact_chain_passes_and_returns_total(chain_path):
    sink = JsonlChainSink(chain_path)
    for index in range(5):
        sink.write(_event(index))

    result = sink.verify_chain()
    assert result.ok is True
    assert result.total == 5
    assert result.broken_at is None


@pytest.mark.parametrize("prepare", ["missing", "empty"])
def test_missing_or_empty_file_passes_with_zero_total(chain_path, prepare):
    if prepare == "empty":
        chain_path.parent.mkdir(parents=True, exist_ok=True)
        chain_path.write_bytes(b"")

    result = JsonlChainSink(chain_path).verify_chain()
    assert result.ok is True
    assert result.total == 0


def test_modified_middle_line_breaks_at_the_next_line(chain_path):
    """
    spec「中间一行被修改」：校验失败并指出首个断链位置。
    改第 2 行 → 第 3 行的 prev_hash 对不上 → 首个断链位置是 3。
    """
    sink = JsonlChainSink(chain_path)
    for index in range(4):
        sink.write(_event(index))

    objects = _objects(chain_path)
    objects[1]["raw_response"] = '{"score": 5}'  # 篡改
    _rewrite(chain_path, objects)

    result = sink.verify_chain()
    assert result.ok is False
    assert result.broken_at == 3


def test_deleted_middle_line_breaks_at_that_position(chain_path):
    """spec「中间一行被删除」。删掉第 2 行后，原第 3 行落到第 2 位且 prev_hash 对不上。"""
    sink = JsonlChainSink(chain_path)
    for index in range(4):
        sink.write(_event(index))

    objects = _objects(chain_path)
    _rewrite(chain_path, objects[:1] + objects[2:])

    result = sink.verify_chain()
    assert result.ok is False
    assert result.broken_at == 2


def test_all_prev_hash_fields_stripped_breaks_at_line_two(chain_path):
    """
    ⭐ 这条是这道防线的分水岭，不是"多写一个用例"（OP-0826-E §三 第 3 条）。

    攻击者删光镜像中所有记录的 prev_hash 字段，试图让整链因"字段缺失即豁免"
    而通过校验。平台侧踩过这个绕过，本仓库一次做对。

    ⚠️ 断言的是 broken_at == 2，**不是** ok is False：只断言 ok is False 的话，
    一个"任何 prev_hash 缺失都算断链（含第 1 行）"的实现也会绿，而那个实现违反
    spec「仅第 1 条记录可豁免（向前兼容既有文件）」。位置断言同时锁住了豁免的
    存在与豁免的边界。
    """
    sink = JsonlChainSink(chain_path)
    for index in range(4):
        sink.write(_event(index))

    objects = _objects(chain_path)
    for obj in objects:
        obj.pop("prev_hash")
    _rewrite(chain_path, objects)

    result = sink.verify_chain()
    assert result.ok is False
    assert result.broken_at == 2
    assert "prev_hash" in (result.error or "")


def test_line_one_may_omit_prev_hash(chain_path):
    """spec：仅第 1 条记录可豁免（向前兼容既有文件）。单行文件缺字段应通过。"""
    sink = JsonlChainSink(chain_path)
    sink.write(_event(1))

    objects = _objects(chain_path)
    objects[0].pop("prev_hash")
    _rewrite(chain_path, objects)

    assert sink.verify_chain().ok is True


def test_non_json_line_is_reported_as_a_break(chain_path):
    sink = JsonlChainSink(chain_path)
    sink.write(_event(1))
    with open(chain_path, "ab") as handle:
        handle.write(b"not json at all\n")

    result = sink.verify_chain()
    assert result.ok is False
    assert result.broken_at == 2


# ── 序列化鲁棒性（tasks 2.6）────────────────────────────────────────────


def test_chinese_and_escaped_newlines_do_not_false_alarm(chain_path):
    """
    spec「记录内容含中文与特殊字符」：链校验仍能正确通过，不因序列化差异误报。
    design D3 第 2 条：校验对磁盘原始字节重算，不做 JSON 解析后重新 dumps 的
    规范化——重排序、ensure_ascii 差异、空格差异都会让哈希对不上。
    """
    sink = JsonlChainSink(chain_path)
    sink.write(_event(1, blocked_reason="缺少『AI 生成』标识\n第二行\t制表符"))
    sink.write(_event(2, blocked_reason="严重度未知——按拦截处理"))
    sink.write(_event(3, raw_response='{"评语": "熟悉 AUTOSAR，CAN 通信经验 3 年"}'))

    result = sink.verify_chain()
    assert result.ok is True
    assert result.total == 3
    # 一条含真实换行的记录仍然只占一行——json.dumps 把它转义成两个字符。
    assert len(_lines(chain_path)) == 3


def test_verification_is_byte_based_not_content_based(chain_path):
    """
    把第 1 行按不同的键顺序重新序列化：**内容完全一样、字节不同**。
    一个"解析后重新 dumps 再比"的实现会放过它；按字节算的实现必须在第 2 行报断。
    这条是 design D3 第 2 条的反向证明。
    """
    sink = JsonlChainSink(chain_path)
    sink.write(_event(1))
    sink.write(_event(2))

    objects = _objects(chain_path)
    reordered = json.dumps(objects[0], ensure_ascii=False, sort_keys=False, indent=None)
    rest = _lines(chain_path)[1:]
    chain_path.write_bytes(b"\n".join([reordered.encode("utf-8"), *rest]) + b"\n")

    result = sink.verify_chain()
    assert result.ok is False
    assert result.broken_at == 2


def test_broken_at_reports_only_the_first_break(chain_path):
    sink = JsonlChainSink(chain_path)
    for index in range(6):
        sink.write(_event(index))

    objects = _objects(chain_path)
    objects[1]["raw_response"] = "tampered-a"
    objects[4]["raw_response"] = "tampered-b"
    _rewrite(chain_path, objects)

    # 改了第 2 行与第 5 行 → 第 3 行与第 6 行都对不上，只报第一处。
    assert sink.verify_chain().broken_at == 3


def test_tail_hash_matches_the_last_line_digest(chain_path):
    """
    已知边界：哈希链检不出**最后一行**被改（没有后继来暴露它）。返回 tail_hash
    让将来需要时可以把链尾锚定到外部。spec 未要求，U2 不做锚定本身。
    """
    sink = JsonlChainSink(chain_path)
    sink.write(_event(1))
    sink.write(_event(2))

    assert sink.verify_chain().tail_hash == hashlib.sha256(_lines(chain_path)[-1]).hexdigest()


def test_chain_stays_verifiable_after_more_appends(chain_path):
    sink = JsonlChainSink(chain_path)
    sink.write(_event(1))
    assert sink.verify_chain().ok is True

    sink.write(_event(2))
    result = sink.verify_chain()
    assert result.ok is True
    assert result.total == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_audit_chain.py -q`
Expected: FAIL —— `AttributeError: 'JsonlChainSink' object has no attribute 'verify_chain'`

- [ ] **Step 3: Write minimal implementation**

`app/audit/sinks.py` 的 `JsonlChainSink` 追加：

```python
    # ── 自校验 ──────────────────────────────────────────────────────────

    def verify_chain(self) -> ChainVerification:
        """
        链完整性校验：能检出任意一行被删除、插入或修改。

        **对磁盘原始字节重算 SHA-256**（design D3 第 2 条）——⛔ 不做 JSON 解析
        后重新 `dumps` 的规范化。重排序、`ensure_ascii` 差异、空格差异都会让哈希
        对不上，导致明明没被改的中文记录报断链。链的定义就是"上一行落盘字节的
        SHA-256"，不是"上一行内容的某种规范形式的 SHA-256"。

        **第 2 条记录起，缺 `prev_hash` 即判定断链；仅第 1 条可豁免**（design D3
        第 1 条）。否则攻击者删光全文件的 `prev_hash` 字段重写，整链会因"每行都
        豁免"而通过校验——这是平台侧修过的绕过。

        第 1 行的 `prev_hash` **取值不校验**：它没有前驱，拿什么和它比？
        `GENESIS_PREV_HASH` 是写入侧的约定，不是可校验的主张。硬要求第 1 行等于
        哨兵，会把"接管一份既有文件"变成永久断链的误报。

        ⚠️ 已知边界：检不出**最后一行**被修改（没有后继来暴露它）。这是哈希链的
        固有性质。返回 `tail_hash` 供外部锚定，本层不做锚定。
        """
        lines = self._raw_lines()
        expected: str | None = None

        for index, line in enumerate(lines, start=1):
            try:
                record = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                return ChainVerification(
                    ok=False,
                    total=len(lines),
                    broken_at=index,
                    error=f"第 {index} 行不是合法的 UTF-8 JSON: {exc}",
                )

            if "prev_hash" not in record:
                if index > 1:
                    return ChainVerification(
                        ok=False,
                        total=len(lines),
                        broken_at=index,
                        error=(
                            f"第 {index} 行缺少 prev_hash 字段；"
                            "缺字段豁免只对第 1 行生效（design D3 第 1 条）"
                        ),
                    )
            elif index > 1 and record["prev_hash"] != expected:
                return ChainVerification(
                    ok=False,
                    total=len(lines),
                    broken_at=index,
                    error=(
                        f"第 {index} 行的 prev_hash 与上一行落盘字节的 SHA-256 不一致："
                        f"期望 {expected}，实得 {record['prev_hash']}"
                    ),
                )

            expected = hashlib.sha256(line).hexdigest()

        return ChainVerification(ok=True, total=len(lines), tail_hash=expected)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_audit_chain.py -q`
Expected: `22 passed`（Task 3 的 9 条 + 本 Task 的 13 条，实测）

- [ ] **Step 5: 手工过一遍分水岭那条（reviewer 会单独看它）**

Run: `./venv/bin/python -m pytest tests/test_audit_chain.py -q -k "stripped or line_one"`
Expected: `2 passed`

- [ ] **Step 6: Commit**

```bash
git add app/audit/sinks.py tests/test_audit_chain.py
git commit -m "feat(audit): verify_chain 按落盘字节校验，第 2 行起缺 prev_hash 即断链"
```

---

### Task 5: `AuditRecorder` 两段式入口与三条结构守护

**Files:**
- Create: `app/audit/recorder.py`
- Modify: `app/audit/__init__.py`
- Test: `tests/test_audit_recorder.py`（新建）

**Interfaces:**
- Consumes: `SqliteSink` / `JsonlChainSink` / `DecisionEvent`
- Produces: `AuditRecorder(store, mirror_sink)`，方法 `record(conn, event) -> bool`（第一段，只写 SQLite）、`mirror(event) -> bool`（第二段，只 append JSONL）、`query_by(**filters)`、`verify_integrity()`；异常 `TransactionOwnershipError`。`reconcile()` / `backfill()` 在 Task 6 补齐。U3 的 `RecorderAuditHook` 与 U5 的两个 `effect_*` 都只通过这个入口。

**⚠️ 相对 `tasks.md` 2.8 的偏离（已登记，需 reviewer 确认）**：tasks 2.8 的字面是「`record()` 按 D1 顺序**先 SQLite 后 JSONL**」。本计划把它落成**两段式**——`record()` 只做 SQLite，`mirror()` 只做 JSONL，⛔ 不提供打包方法。这是 OP-0826-E §三 第 1 条与 `delivery-units.md` §2.U2 / §3.4 的硬约束：tasks 2.8 是**顺序**要求，两段式满足它（调用点必须先 record 后 mirror）；打包成一次调用则 append 会发生在事务提交之前，事务回滚就留下「JSONL 有、SQLite 无」——design D1 明令这是更糟的偏差方向。**这条在 U2 定死，否则 U3/U5 返工。**

- [ ] **Step 1: Write the failing test**

新建 `tests/test_audit_recorder.py`：

```python
import ast
import sqlite3
from pathlib import Path

import pytest

from app.audit.events import AI_ANALYSIS, CriterionScore, DecisionEvent
from app.audit.recorder import AuditRecorder, TransactionOwnershipError
from app.audit.sinks import JsonlChainSink, SqliteSink
from app.storage.db import get_connection, init_schema

APP_ROOT = Path(__file__).resolve().parents[1] / "app"


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "audit.db"))
    init_schema(c)
    return c


@pytest.fixture
def chain_path(tmp_path):
    return tmp_path / "audit" / "decisions.jsonl"


@pytest.fixture(autouse=True)
def _clear_class_state():
    yield
    JsonlChainSink._CURSORS.clear()
    JsonlChainSink._LOCKS.clear()


class CountingSink:
    """记账用的假 sink：只数被调用了几次，不落任何东西。"""

    def __init__(self):
        self.writes: list[DecisionEvent] = []

    def write(self, event):
        self.writes.append(event)
        return True

    def read_all(self):
        return []


def _event(**overrides) -> DecisionEvent:
    payload = {
        "id": "thread-1:effect_record_analysis:sha256:abc",
        "event_type": AI_ANALYSIS,
        "thread_id": "thread-1",
        "application_id": "app-1",
        "configured_model": "deepseek-chat",
        "prompt_version": "score-v1",
        "temperature": 0.0,
        "input_hash": "sha256:abc",
        "raw_response": "{}",
        "scores": (CriterionScore("autosar", 3.0, "resume-1#1-20"),),
    }
    payload.update(overrides)
    return DecisionEvent(**payload)


# ── 两段式：本单元的头号约束 ─────────────────────────────────────────────


def test_record_writes_sqlite_only(conn, chain_path):
    """
    第一段只碰真身。碰了镜像就意味着 append 发生在事务提交之前，
    回滚会留下「JSONL 有、SQLite 无」——design D1 明令更糟的偏差方向。
    """
    mirror = CountingSink()
    recorder = AuditRecorder(SqliteSink(conn), mirror)

    assert recorder.record(conn, _event()) is True

    assert mirror.writes == []
    assert not chain_path.exists()


def test_mirror_writes_jsonl_only(conn, chain_path):
    store = CountingSink()
    store.conn = conn  # 满足事务归属断言
    recorder = AuditRecorder(store, JsonlChainSink(chain_path))

    assert recorder.mirror(_event()) is True

    assert store.writes == []
    assert len(JsonlChainSink(chain_path).read_all()) == 1


def test_recorder_exposes_no_packed_method():
    """
    结构守护：recorder.py 里不存在任何一个函数体同时触碰两个 sink 的 write。
    将来有人"顺手加一个 record_all() 方便调用"，这条立刻变红。
    """
    source = (APP_ROOT / "audit" / "recorder.py").read_text(encoding="utf-8")

    assert _functions_touching_both_sinks(source) == []


def test_packed_method_detector_actually_detects():
    """
    ⭐ 阳性对照。没有它，上一条在"检查函数根本没生效"时同样是绿的——
    "0 命中"同时兼容"约束守住了"和"检查根本没跑"两种解释，那不叫验证
    （判据形状与 tasks 6.7 相同）。
    """
    offending = (
        "class X:\n"
        "    def record_all(self, conn, event):\n"
        "        self._store.write(event)\n"
        "        self._mirror.write(event)\n"
    )

    assert _functions_touching_both_sinks(offending) == ["record_all"]


def _functions_touching_both_sinks(source: str) -> list[str]:
    hits = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        touched = set()
        for inner in ast.walk(node):
            if not (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)):
                continue
            if inner.func.attr != "write":
                continue
            target = inner.func.value
            if isinstance(target, ast.Attribute):
                touched.add(target.attr)
        if {"_store", "_mirror"} <= touched:
            hits.append(node.name)
    return hits


# ── ⛔ 禁止在 effect_* 函数体内 append JSONL ─────────────────────────────


def _effect_functions_touching_the_mirror(source: str) -> list[str]:
    forbidden_calls = {"mirror", "backfill"}
    hits = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("effect_"):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                if inner.func.attr in forbidden_calls:
                    hits.append(f"{node.name}:{inner.func.attr}")
            if isinstance(inner, ast.Name) and inner.id == "JsonlChainSink":
                hits.append(f"{node.name}:JsonlChainSink")
    return hits


def test_no_effect_function_appends_jsonl():
    """
    OP-0826-E §三 第 2 条：⛔ 禁止在 effect_* 函数体内 append JSONL。
    允许的偏差只有单向——「SQLite 有、JSONL 缺行」（真身完整、镜像缺证据）。

    这条今天在 app/ 下是"恒真"的（还没有任何 effect_* 引用 audit）。它存在的
    意义是**在 U5 接线写错时立刻变红**，而不是今天证明了什么。
    """
    offenders = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        offenders += [
            f"{path.name}:{hit}"
            for hit in _effect_functions_touching_the_mirror(path.read_text(encoding="utf-8"))
        ]

    assert offenders == []


def test_effect_mirror_detector_actually_detects():
    """⭐ 阳性对照，理由同上。"""
    offending = (
        "@idempotent_effect('effect_x')\n"
        "def effect_x(conn, *, thread_id, business_key, event):\n"
        "    recorder.record(conn, event)\n"
        "    recorder.mirror(event)\n"
    )

    assert _effect_functions_touching_the_mirror(offending) == ["effect_x:mirror"]


# ── 事务归属（铁律 1）────────────────────────────────────────────────────


def test_record_rejects_a_foreign_connection(conn, tmp_path, chain_path):
    """
    传进来的 conn 与 sink 绑定的不是同一个对象 → 两个事务管理者。
    2026-08-13 那次丢 outbox 的事故就是这个形状（findings §8.5）。
    """
    other = get_connection(str(tmp_path / "other.db"))
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))

    with pytest.raises(TransactionOwnershipError):
        recorder.record(other, _event())


def test_record_does_not_commit(conn, tmp_path, chain_path):
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))
    recorder.record(conn, _event())

    onlooker = get_connection(conn.execute("PRAGMA database_list").fetchone()[2])
    assert onlooker.execute("SELECT count(*) FROM analysis_run").fetchone()[0] == 0


def test_record_propagates_storage_failure(conn, chain_path):
    """
    spec「留痕写入失败」：该次评分结果 MUST NOT 进入下游排序，失败可被观测、
    不静默丢弃。实现上 record() 抛异常，调用方不吞（design D1 末条）。
    """
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))

    with pytest.raises(sqlite3.IntegrityError):
        recorder.record(conn, _event(scores=(CriterionScore("autosar", 3.0, ""),)))


# ── 转发与边界 ───────────────────────────────────────────────────────────


def test_query_by_delegates_to_the_store(conn, chain_path):
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))
    recorder.record(conn, _event(id="run-a", application_id="app-1"))
    recorder.record(conn, _event(id="run-b", application_id="app-2"))
    conn.commit()

    assert [record["id"] for record in recorder.query_by(application_id="app-2")] == ["run-b"]


def test_verify_integrity_delegates_to_the_mirror(conn, chain_path):
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))
    recorder.mirror(_event(id="run-a"))
    recorder.mirror(_event(id="run-b"))

    result = recorder.verify_integrity()
    assert result.ok is True
    assert result.total == 2


@pytest.mark.parametrize("module", ["events", "sinks", "recorder"])
def test_audit_module_imports_no_config_or_graph(module):
    """
    铁律 2 的落点：app/audit 是被 L4 调用的存储适配层，自己不决定何时被调用。
    import app.config 会让审计路径在启动时绑死配置、并让 U3 的注入点不再是唯一
    一处；import app.graph 是反向依赖。路径与连接一律由调用方传入。

    ⚠️ 用 AST 扫真正的 import 语句，**不要**用 `"app.config" not in source` 这种
    子串扫描——写明这条规则的 docstring 里就含 "app.config" 四个字，子串版会被
    自己的注释绊倒（2026-08-26 提取验证实测，见文末「提取验证记录」）。
    """
    source = (APP_ROOT / "audit" / f"{module}.py").read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert not [name for name in imported if name.startswith(("app.config", "app.graph"))]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_audit_recorder.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.audit.recorder'`

- [ ] **Step 3: Write minimal implementation**

新建 `app/audit/recorder.py`：

```python
"""
留痕的统一入口。业务代码只跟 `AuditRecorder` 打交道，不关心后端。

⚠️ **两段式 API，这是本模块最重要的形状约束**（`delivery-units.md` §2.U2 / §3.4）：

    第一段  record(conn, event)  → 只写 SQLite，进调用方的事务，⛔ 不 commit
    第二段  mirror(event)        → 只 append JSONL，必须在事务**已提交之后**调用

⛔ **不提供把两段打包成一次调用的方法。** 打包会让 append 发生在事务提交之前，
事务一旦回滚就留下「JSONL 有、SQLite 无」——镜像里出现一条数据库里查不到的
记录，design D1 明令这是更糟的偏差方向（审计查不到记录）。允许的偏差只有单向：
「SQLite 有、JSONL 缺行」（真身完整、镜像缺证据），由 `reconcile()` 检出、由
`backfill()` 在链尾补录。

⛔ **禁止在任何 `effect_*` 函数体内调用 `mirror()`。** 落地形态：`record()` 进
`effect_*` 函数体，`mirror()` 由调用点在 `effect_*` **返回之后**触发——此时
`idempotent_effect` 已 `commit`。**这不需要改 `idempotent_effect` 装饰器**，
因为 append 发生在装饰器之外。守护见 `tests/test_audit_recorder.py`。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from app.audit.events import AI_ANALYSIS, BACKFILL, DecisionEvent
from app.audit.sinks import AuditSink, ChainVerification


class TransactionOwnershipError(RuntimeError):
    """
    调用点传进来的连接与真身 sink 绑定的不是同一个对象。

    工程铁律 1：幂等记录与业务写必须同一连接、同一个 `BEGIN`，且该连接上不得
    存在第二个事务管理者。实证：`.51` 现网 2026-08-10 与 08-12 各丢一轮 outbox
    （幂等记录已落、业务写没落），见
    `docs/findings/2026-08-13-sqlite-事务归属冲突.md` §8.5。
    """


@dataclass(frozen=True)
class Reconciliation:
    """
    跨介质对账结果（design D1 的检出手段）。U6 的 6.4 在这之上写断言。

    `unexplained_missing` 才是该报警的那一集：链尾已有 `type=backfill` 补录事件
    的缺行是**已知且已登记**的，把它一直算成违例会让这条断言长期红着，红久了
    就没人看了。
    """

    missing_in_mirror: frozenset[str] = frozenset()
    missing_in_store: frozenset[str] = frozenset()
    backfilled: frozenset[str] = frozenset()

    @property
    def unexplained_missing(self) -> frozenset[str]:
        return frozenset(self.missing_in_mirror - self.backfilled)

    @property
    def ok(self) -> bool:
        return not self.unexplained_missing and not self.missing_in_store


class AuditRecorder:
    """统一入口。形状约束见模块 docstring。"""

    def __init__(self, store: AuditSink, mirror_sink: AuditSink) -> None:
        self._store = store
        self._mirror = mirror_sink

    # ── 第一段：真身 ────────────────────────────────────────────────────

    def record(self, conn: sqlite3.Connection, event: DecisionEvent) -> bool:
        """
        写 SQLite。**进调用方的事务，不 commit。**

        `conn` 在功能上是冗余的（真身 sink 自己就绑着一条连接）——它存在的唯一
        理由是把工程铁律 1 的"同一连接"从一句注释变成调用点上一句测得到的断言。
        冗余在这里是刻意的成本。

        失败即抛，⛔ 调用方不得吞：spec「留痕写入失败」要求该次 AI 结果视为不
        可用，其评分 MUST NOT 进入下游排序，且失败本身可被观测。
        """
        bound = getattr(self._store, "conn", None)
        if bound is not None and conn is not bound:
            raise TransactionOwnershipError(
                "record() 收到的连接与真身 sink 绑定的不是同一个对象；"
                "幂等记录与业务写必须落在同一连接的同一个 BEGIN 里（工程铁律 1）"
            )
        return self._store.write(event)

    # ── 第二段：镜像 ────────────────────────────────────────────────────

    def mirror(self, event: DecisionEvent) -> bool:
        """
        append JSONL。**必须在事务已提交之后调用**（`effect_*` 返回之后）。

        ⛔ 不要为了"少一次调用"把它塞回 `record()` 里，理由见模块 docstring。
        """
        return self._mirror.write(event)

    # ── 查询与自检 ──────────────────────────────────────────────────────

    def query_by(self, **filters: Any) -> list[dict[str, Any]]:
        """spec「留痕可查询」：按业务关联标识、时间区间、模型标识等维度检索。"""
        return self._store.query(**filters)

    def verify_integrity(self) -> ChainVerification:
        """
        链自校验。⚠️ 它只证明"链自身没被改"，**证明不了"该留的痕都留了"**——
        后者是 `reconcile()`。两者不可互相替代（`delivery-units.md` §3.4）。
        """
        return self._mirror.verify_chain()
```

`app/audit/__init__.py` 追加导出 `AuditRecorder` / `Reconciliation` / `TransactionOwnershipError`。

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_audit_recorder.py -q`
Expected: `14 passed`（实测）

- [ ] **Step 5: Commit**

```bash
git add app/audit/recorder.py app/audit/__init__.py tests/test_audit_recorder.py
git commit -m "feat(audit): AuditRecorder 两段式入口，record 进事务 mirror 在提交后"
```

---

### Task 6: 双写故障语义——对账检出差集，补齐走链尾不插回原位

**Files:**
- Modify: `app/audit/recorder.py`（`reconcile()` / `backfill()`）
- Test: `tests/test_audit_recorder.py`（追加）

**Interfaces:**
- Produces: `AuditRecorder.reconcile() -> Reconciliation`、`AuditRecorder.backfill(missing_id, *, reason) -> bool`（tasks 2.9）

**归属说明**：U6 的 6.4 是 `assertions.py` 里**面向 CI 的**对账查询，本 Task 提供的是它调用的**原语**。tasks 2.9 要求的是"对账能检出差集"这条**能力在 U2 就可测**，不是把 CI 断言提前做掉。

- [ ] **Step 1: Write the failing test**

`tests/test_audit_recorder.py` 追加：

```python
# ── 双写故障语义（tasks 2.9 / design D1）─────────────────────────────────


class ExplodingMirror:
    """append 必炸的镜像 sink，用来构造"真身写成了、镜像没写成"这一侧的偏差。"""

    def write(self, event):
        raise OSError("磁盘满")

    def read_all(self):
        return []


def test_mirror_failure_leaves_the_sqlite_row_intact_and_raises(conn, tmp_path):
    """
    design D1 的崩溃窗口：两者之间失败 → SQLite 有、JSONL 缺行。这是**可接受的
    偏差方向**（真身完整、镜像缺证据），但失败本身必须可见，不能吞。
    """
    recorder = AuditRecorder(SqliteSink(conn), ExplodingMirror())
    recorder.record(conn, _event(id="run-a"))
    conn.commit()

    with pytest.raises(OSError):
        recorder.mirror(_event(id="run-a"))

    assert conn.execute("SELECT count(*) FROM analysis_run").fetchone()[0] == 1


def test_reconcile_detects_a_missing_mirror_line(conn, chain_path):
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))
    recorder.record(conn, _event(id="run-a"))
    recorder.record(conn, _event(id="run-b"))
    conn.commit()
    recorder.mirror(_event(id="run-a"))  # run-b 的镜像没写成

    result = recorder.reconcile()
    assert result.missing_in_mirror == frozenset({"run-b"})
    assert result.ok is False


def test_reconcile_detects_a_row_missing_from_the_store(conn, chain_path):
    """
    反方向：镜像里有、真身里没有。这是 design D1 明令**更糟**的一侧（审计查不到
    记录），对账必须能指出来而不是只看单向差集。
    """
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))
    recorder.mirror(_event(id="ghost"))

    result = recorder.reconcile()
    assert result.missing_in_store == frozenset({"ghost"})
    assert result.ok is False


def test_reconcile_is_clean_when_both_sides_match(conn, chain_path):
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))
    for run_id in ("run-a", "run-b"):
        recorder.record(conn, _event(id=run_id))
    conn.commit()
    for run_id in ("run-a", "run-b"):
        recorder.mirror(_event(id=run_id))

    result = recorder.reconcile()
    assert result.ok is True
    assert result.missing_in_mirror == frozenset()


def test_backfill_appends_at_the_tail_not_in_place(conn, chain_path):
    """
    design D1：JSONL 缺行**不允许事后插回原位**——插回必然断链。补齐方式是在链尾
    append 一条 type=backfill 的补录事件，指向缺失的 analysis_run.id，形成
    "缺过、什么时候补的"的显式记录。这比伪造一条看起来正常的历史行诚实。
    """
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))
    recorder.record(conn, _event(id="run-a"))
    recorder.record(conn, _event(id="run-b"))
    conn.commit()
    recorder.mirror(_event(id="run-a"))

    assert recorder.backfill("run-b", reason="镜像 append 时磁盘满") is True

    records = JsonlChainSink(chain_path).read_all()
    assert [record["event_type"] for record in records] == ["ai_analysis", "backfill"]
    assert records[-1]["backfill_of"] == "run-b"
    assert records[-1]["error"] == "镜像 append 时磁盘满"


def test_backfill_keeps_the_chain_verifiable(conn, chain_path):
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))
    recorder.mirror(_event(id="run-a"))
    recorder.backfill("run-b", reason="镜像缺行")

    assert recorder.verify_integrity().ok is True


def test_backfilled_id_moves_out_of_unexplained_missing(conn, chain_path):
    """
    补录之后差集**仍然非空**（镜像里确实没有 run-b 的原始行，这是事实，不该被
    抹掉），但它不再是"未解释的缺失"。U6 的断言按 unexplained_missing 取数。
    """
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))
    recorder.record(conn, _event(id="run-a"))
    recorder.record(conn, _event(id="run-b"))
    conn.commit()
    recorder.mirror(_event(id="run-a"))
    recorder.backfill("run-b", reason="镜像缺行")

    result = recorder.reconcile()
    assert result.missing_in_mirror == frozenset({"run-b"})
    assert result.backfilled == frozenset({"run-b"})
    assert result.unexplained_missing == frozenset()
    assert result.ok is True


def test_backfill_writes_nothing_to_sqlite(conn, chain_path):
    """补录是镜像侧的事实。往真身里插一行"补录"会污染 analysis_run 的语义。"""
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))
    recorder.backfill("run-x", reason="镜像缺行")

    assert conn.execute("SELECT count(*) FROM analysis_run").fetchone()[0] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_audit_recorder.py -q -k "reconcile or backfill or mirror_failure"`
Expected: FAIL —— `AttributeError: 'AuditRecorder' object has no attribute 'reconcile'`

- [ ] **Step 3: Write minimal implementation**

`app/audit/recorder.py` 的 `AuditRecorder` 追加：

```python
    # ── 对账与补录（design D1 的检出与补齐手段）──────────────────────────

    def reconcile(self) -> Reconciliation:
        """
        跨介质对账：按 `analysis_run.id` 比对两侧记录集合，差集非空即报告。

        ⚠️ 与 `verify_integrity()` 是两条不同的断言，**不可互相替代**：
        `verify_chain()` 只证明"链自身没被改"，`reconcile()` 才回答"该留的痕都
        留了没有"（`delivery-units.md` §3.4 / §2.U6）。

        只比对 `ai_analysis` 类事件：外发事件的 SQLite 真身是 `pending_approval`
        （U5 的 queue.py 写），不在 `analysis_run` 里，拿它对账会把每一条外发留痕
        都算成"真身缺失"。
        """
        store_ids = {record["id"] for record in self._store.read_all()}

        mirror_ids: set[str] = set()
        backfilled: set[str] = set()
        for record in self._mirror.read_all():
            event_type = record.get("event_type")
            if event_type == AI_ANALYSIS:
                mirror_ids.add(record["id"])
            elif event_type == BACKFILL and record.get("backfill_of"):
                backfilled.add(record["backfill_of"])

        return Reconciliation(
            missing_in_mirror=frozenset(store_ids - mirror_ids),
            missing_in_store=frozenset(mirror_ids - store_ids),
            backfilled=frozenset(backfilled),
        )

    def backfill(self, missing_id: str, *, reason: str) -> bool:
        """
        补齐一条镜像缺行：在**链尾** append 一条 `type=backfill` 事件。

        ⛔ 不插回原位——插回必然断链（design D1）。留下"缺过、什么时候补的"这条
        显式记录，比伪造一条看起来正常的历史行诚实。

        补录**只写镜像**：往 `analysis_run` 里插一行"补录"会污染真身的语义。
        """
        return self._mirror.write(
            DecisionEvent(
                id=f"backfill:{missing_id}",
                event_type=BACKFILL,
                backfill_of=missing_id,
                error=reason,
            )
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_audit_recorder.py -q`
Expected: `22 passed`（Task 5 的 14 条 + 本 Task 的 8 条，实测）

- [ ] **Step 5: 全量回归**

```bash
./venv/bin/python -m pytest -q 2>&1 | tail -2
```

Expected: 基线 + 90，无 failed / error。**若有既有用例变红，立刻停下**——U2 与整个仓库零文件重叠，任何既有用例变红都说明触碰了不该碰的东西。

- [ ] **Step 6: 边界自查（三条硬边界 + 不碰既有文件）**

```bash
git diff --stat origin/main -- requirements.txt pyproject.toml app/storage/ app/config.py app/graph/
grep -rn "zhuopin_platform" app/ tests/
grep -rn "scenario\|automation_level\|oem_context\|override_reason" app/audit/
grep -rn "conn.commit()" app/audit/
```

Expected: 第一条**无输出**（这些文件一个字节都没改）；第二、三、四条**零命中**。

- [ ] **Step 7: Commit**

```bash
git add app/audit/recorder.py tests/test_audit_recorder.py
git commit -m "feat(audit): 双写对账与链尾补录，补齐不插回原位"
```

---

## 交付前自查

- [ ] `grep -c '^### Task ' docs/superpowers/plans/2026-08-26-ai-audit-trail-unitU2-audit-module.md` **等于 6**，不是 0
- [ ] Global Constraints 段在场，内容与 `CLAUDE.md`「工程铁律」「合规红线」「部署约束」一致
- [ ] 每个 Task 有确切文件路径、完整代码、确切命令与预期输出
- [ ] 无 TBD / TODO / "适当处理错误" 类占位符
- [ ] 前后 Task 的类型名、函数签名、字段名一致（`DecisionEvent` 的字段在 Task 1 定死，Task 2/3/5/6 一律照用）
- [ ] **`SqliteSink.write` 全文无 `conn.commit()`**（铁律 1）
- [ ] **`AuditRecorder` 无打包方法**，且这条有 AST 守护 + 阳性对照
- [ ] **`app/audit/` 下无 `@idempotent_effect`、无 `effect_log` 的任何 DDL/DML**
- [ ] `evidence_ref` 相关：`CHECK` 失败**原样抛出**，只有 `analysis_run.id` 的 UNIQUE 冲突才短路
- [ ] 全量测试全绿，`requirements.txt` / `pyproject.toml` diff 为空

## spec 覆盖对照

| spec Requirement（`specs/ai-decision-audit/spec.md`） | 本单元的 Task | 未尽部分归属 |
|---|---|---|
| AI 调用的可复现留痕 | Task 1（字段）、Task 2（十四列落盘、指纹缺失记空值、只存 `input_hash`）、Task 5（失败即抛、不静默） | 网关接线与 `audit_context` 透传 → U3；`latest` 别名拒绝已在 `app/config.py` |
| 逐项评分必须带证据回指 | Task 2（`CHECK` 失败不吞、失败不留孤儿 run） | 存储层 `CHECK` 本身 → U1（已完成）；纵深防御断言 → U6 6.2 |
| 评分项白名单约束 | **不在本单元**（Task 2 明确透传 `criterion_key`，⛔ 不加第二处校验） | 白名单定义与拒写 → U3 3.4；断言 → U6 6.3 |
| 留痕不可无痕篡改 | **Task 3（写入侧）+ Task 4（`verify_chain()` 四攻击场景 + 序列化鲁棒性 + 并发）** | —— |
| 留痕可查询与合规断言 | Task 2（`query`）、Task 5（`query_by`）、Task 6（`reconcile`） | 三条合规断言与 CI 接入 → U6 6.1–6.6 |
| 留痕数据的用途限制 | Task 1（模块 docstring 标注 + 机器校验） | 表注释 → U1（已完成） |

| spec Requirement（`specs/outbound-approval-gate/spec.md`） | 本单元的 Task | 未尽部分归属 |
|---|---|---|
| 外发与拦截动作强制留痕（"MUST 使用与 AI 评分留痕相同的机制，落入同一份可校验的记录中"） | Task 1（`outbound_blocked` / `outbound_delivered` 事件类型与门禁字段）、Task 3（同一条链） | 判定 → U4；接线与幂等 → U5；拦截统计 → U6 6.5 |

## 本计划相对 `tasks.md` / `delivery-units.md` 的偏离登记（共三条，全部需 reviewer 确认）

1. **`AuditRecorder` 落成两段式 `record()` / `mirror()`，⛔ 不提供打包方法**（tasks 2.8 的字面是一个 `record()` 先 SQLite 后 JSONL）。依据是 OP-0826-E §三 第 1 条与 `delivery-units.md` §2.U2 / §3.4 的明文硬约束：2.8 是**顺序**要求，两段式满足它。方向是更严不是更松。
2. **`rubric_version` 与 `rubric_snapshot` 在 SQLite 侧合并落进 `rubric_snapshot` 一列**（`{"version":…, "snapshot":…}`，`read_all()` 无损拆回）。理由：tasks 2.1 把 `rubric_version` 列为一等字段，U1 的表没有对应列，而 U2 ⛔ 不改 U1 的表（会让 U1 的老库回归守护失效，且共写 `db.py` 会作废 U2 ∥ U4 的并行）。spec 要的是"完整快照"，版本是快照的属性。
3. **`AuditSink.write` 返回 `bool` 而非 `None`**，且 `SqliteSink` 对非 `ai_analysis` 事件返回 `False`（不替外发事件凭空造表——它们的真身是 `pending_approval`，U5 的 `queue.py` 写）。理由：主键冲突短路（tasks 2.2 逐字）需要一个调用方与测试都能观察到的信号。

## 已登记的边界与技术债（不在本单元解决）

| 事项 | 处置 |
|---|---|
| 哈希链检不出**最后一行**被修改（无后继暴露它） | 哈希链固有性质。`verify_chain()` 返回 `tail_hash` 供将来外部锚定，spec 未要求，U2 不做 |
| JSONL 写入侧**仅进程内锁**，多进程部署会断链 | design Non-Goals 明确接受（当前是单进程 Windows 计划任务）。代码注释已标注，技术债登记是 U7 的 7.6，**U2 不重复登记** |
| `confirmed_by` / `operator_id` 现阶段不可信 | design D7。字段先占位、docstring 标注，SSO 落地后结构不改。技术债登记是 U7 的 7.5 |
| JSONL 文件路径与备份策略 | design Open Questions。U2 的 sink 只接受调用方传入的路径，不读配置；路径决策在 U3 接线时按 U1 的 `audit_jsonl_path` 落 |
| 每次 append 都 `fsync` 的开销 | M1/M2 量级（百级岗位、千级投递）可忽略。审计镜像的意义是"崩溃后还在"，省掉 fsync 就把这个意义省掉了。不提前优化 |

## 提取验证记录（`spec-to-plan` 第 6 步，2026-08-26 实测）

U1 尚未合并，所以验证在 `scratchpad/sandbox/` 里做：把本计划的全部 `python` 代码块**原样提取**并按 Task 顺序拼装成 `app/audit/{events,sinks,recorder}.py` 与四个测试文件，`app/storage/db.py` 取仓库真源 + 从 U1 计划里原样提取的三张表 DDL，跑 `pytest tests -q`。

**结果：90 passed**（events 22 · sqlite 24 · chain 22 · recorder 22）。逐 Task：Task 1 → 22，Task 2 → 24，Task 3 → 9，Task 4 → 13，Task 5 → 14，Task 6 → 8。

**揪出并已回灌的 bug 一个**：`test_audit_module_imports_no_config_or_graph` 原本写成 `assert "app.config" not in source` 的子串扫描——而 `sinks.py` 的模块 docstring 里就有「不 import `app.config` / `app.graph`」这句话，**这条守护被自己要守护的那条规则的文字绊倒了**，恒红。已改成 AST 扫真正的 `import` / `from ... import` 语句。

> 这类"扫源码的守护被注释/文档字符串误伤"是结构守护的典型翻车形状，本计划里还有三处 AST 扫描（两处带阳性对照），全部走 AST 而非子串，原因就在这里。

**边界**：测试与被测代码出自同一份文档、同一个作者，全通只证明**代码可执行且内部自洽**，不证明**符合 spec**。spec 合规由 `run-build` 的两阶段 review 负责。另外沙箱里的 `db.py` 是"真源 + U1 计划里的 DDL"，**不等于 U1 合并后的实际结果**——开工前置里那条 `PRAGMA table_info` 核对因此不可跳过。

## 完成判据（`tasks.md` 第 2 章的 checkbox 在这些全部成立后才勾）

- 2.1 ✅ Task 1　2.2 ✅ Task 2　2.3 ✅ Task 3　2.4 ✅ Task 4　2.5 ✅ Task 4（分水岭那条断言 `broken_at == 2`）
- 2.6 ✅ Task 4　2.7 ✅ Task 3　2.8 ✅ Task 5（两段式，偏离登记 1）　2.9 ✅ Task 6
- 全量测试全绿（基线 + 90），`app/storage/db.py` / `app/config.py` / `app/graph/` 的 diff 为空
- `subagent-driven-development` 的两阶段 review 通过，且 reviewer 明确确认了上面三条偏离
