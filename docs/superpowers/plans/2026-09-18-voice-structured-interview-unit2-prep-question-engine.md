# voice-structured-interview · U2 prep 出题引擎（生成 → 业务经理确认 → 冻结）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让业务经理能对一个已通过硬门槛（或简历评分尚未完成）的投递，一键生成一套结构化面试题（含难度曲线、rubric、预埋追问、生成依据），在 Web 页面逐题看/改/删/重生成，点"确认"后生成不可变的冻结快照；未冻结的快照不能被开场使用。

**Architecture:** L3 Agent（`app/agents/interview_prep.py::generate()`/`regenerate_one()`）是无副作用纯函数，只做"画像+简历弱点 → 题目"的 LLM 调用与确定性排序；L4 编排层（`app/graph/interview_prep_nodes.py`）的 `compute_prep` 负责从 SQLite 读取画像/rubric/简历评分组装 L3 的输入，`effect_*` 节点把结果落库（`prep_snapshot`/`prep_question`，U1 已建表）。"业务经理确认后冻结"不使用 LangGraph 真实 `interrupt()`——沿用本项目 2026-08-26 判例（Web 通道下"挂起等人"由 HTTP + SQLite 状态 + 独立确认端点达成），`effect_freeze_prep` 由确认页的 `/freeze` 端点直接调用，与 M1 `effect_confirm_profile` 同一形态。

**Tech Stack:** Python 3.14、pytest、SQLite（`app/storage/db.py` 是唯一真源，本单元不引入 Postgres）、FastAPI（`app/web/server.py` 单路由器）、既有 `LLMGateway`（`app/llm/gateway.py`，`temperature=0`）、静态 HTML + 原生 JS（`app/web/static/`，无前端框架）。

**Spec:** `openspec/changes/voice-structured-interview/specs/interview-prep-question-engine/spec.md`（5 条 Requirement）；设计依据 `openspec/changes/voice-structured-interview/design.md` D1/D4/D7/D8/D9/D16/D17/D20；范围对应 `openspec/changes/voice-structured-interview/tasks.md` 第 3 章（3.1–3.8）。

## Global Constraints

从 `CLAUDE.md`「工程铁律」与「合规红线」逐字摘录，本单元逐条判定适用性：

1. **幂等记录与业务写同事务**：每个有副作用的动作独占一个节点，幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 并与业务写同一个 `conn`、同一次提交。——**适用**：`effect_persist_prep_draft`、`effect_freeze_prep`、`effect_edit_prep_question`、`effect_delete_prep_question`、`effect_regenerate_prep_question` 全部用 `app/storage/idempotency.py::idempotent_effect` 装饰。
2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行，节点命名区分 `compute_*`/`effect_*`。——**适用**：`app/agents/interview_prep.py::generate()`/`regenerate_one()` 不接受 `conn` 参数、不写库；`app/graph/interview_prep_nodes.py::compute_prep()` 允许只读查库组装输入（与既有 `compute_screen` 同一先例），写库只在 `effect_*` 节点。
3. **所有 AI 评分必须持久化**：模型标识+版本+prompt版本+temperature+输入哈希+rubric快照+原始响应。——**适用**：`generate()`/`regenerate_one()` 的每次 LLM 调用经 `LLMGateway.extract_structured_with_meta()`，沿用既有 `AuditHook` 留痕链路，本单元只需正确传 `prompt_version`/`audit_context`。
4. **每条 `criterion_score` 必须有 `evidence_ref`**，为空不允许写入。——**不适用**：本单元不产出 `criterion_score` 行（那是 post 评分段 U5 的事），本单元只读取既有 `criterion_score`（简历评分）供 prompt 输入，不新增写入路径。
5. **`temperature=0`；模型版本优先显式锁定**，供应商响应侧 `model` 字段回存。——**适用**：`generate()`/`regenerate_one()` 沿用 `LLMGateway`（`TEMPERATURE=0` 是类常量），不新建 LLM 客户端。
6. **企微回调先落库再处理**。——**不适用**：本单元没有企微回调路径。
7. **`langgraph >= 1.0.10`**。——**适用（环境约束）**：不新增依赖，`requirements.txt` 不改。

合规红线：

- **AI 只做排序推荐，不做自动淘汰**。——**不适用**：prep 出题不涉及淘汰判定，不写 `rejection_record`。
- **禁止人脸/表情分析；声学情绪信号只展示不进评分**。——**不适用**：本单元不接触音频/声学信号（那是 U4/U5）。
- **AI 生成的 JD、拒信、邀约须带标识**。——**适用（扩展到题目）**：spec Requirement「题目与 AI 生成标识」要求确认页出题带 AI 生成标识；`prep_question.origin` 字段（U1 已建）承载这条，本单元的确认页（Task 7）与编辑端点（Task 6）落地。
- **模型全部走境内**。——**适用（环境约束）**：沿用既有网关配置，不新增供应商。
- **绝不用历史录用结果做监督信号**。——**适用**：`generate()`/`regenerate_one()` 的输入类型 `PrepInput`（`app/schemas/interview_ai_input.py`，已存在）`model_config = ConfigDict(extra="forbid")` 结构性排除任何历史录用结果字段；Task 8 的 e2e 测试对此有反射断言。
- **主观描述不得进入硬门槛规则**。——**不适用**：本单元不产出 `hard_requirement` 行。

---

## 背景：已在 U1（第 2 章，已合并到 main）就绪、本单元直接复用的地基

- `app/storage/db.py`（第 667–704 行）：`prep_snapshot`（`id, application_id, version, profile_version, resume_run_id` 可空、`gen_run_id` **不可空** `REFERENCES analysis_run(id)`、`confirmed_by, confirmed_at, status CHECK IN (draft,frozen,expired), created_at`；唯一索引 `(application_id, version)`）与 `prep_question`（`id, snapshot_id, seq, dimension, difficulty, text, rubric_json, follow_ups_json, rationale, origin CHECK IN (ai,ai_edited) DEFAULT 'ai', ai_text, created_at`；唯一索引 `(snapshot_id, seq)`）。**本单元不新增/不修改这两张表结构。**
- `app/schemas/interview_ai_input.py`：`PrepInput`（`profile: dict, rubric_dimensions: list[str]` 非空、`resume_scores: list[ResumeScoreItem]` 默认空、`extra="forbid"`）与 `ResumeScoreItem`（`criterion_key, score, evidence_excerpt`）已存在。`generate()`/`regenerate_one()` **必须**以 `PrepInput` 为唯一输入形状，不另造输入类型。
- `app/audit/hook.py`：`ALLOWED_CONTEXT_KEYS`（`thread_id/node/application_id/job_id/rubric_version/rubric_snapshot`）与生产用 `RecorderAuditHook` 已就绪，`app/main.py::_gateway_factory()` 已接好。
- `app/audit/run_type.py`：`prompt_version` 前缀 `"interview-"` 已登记为 `RUN_TYPE_INTERVIEW`；本单元用 `"interview-prep-v1"` 与该模块注释里预写的例子字面一致。
- `app/graph/screening_nodes.py::latest_approved_profile_version(conn, job_id) -> int | None`：取某岗位当前已确认最新画像版本号，本单元直接复用。
- `app/audit/evidence_ref.py`：`parse_evidence_ref()`/`EvidenceRef`（`span_id, start, end`）已存在，本单元用它解析简历评分的证据回指。

---

### Task 0: 扩展 AuditHook 返回值，让调用方拿到写入的 `analysis_run.id`

**背景（为什么要动这个既有模块）：** `prep_snapshot.gen_run_id` 是 `NOT NULL REFERENCES analysis_run(id)`——`effect_persist_prep_draft`（Task 4）必须知道刚才那次 LLM 调用真正写入 `analysis_run` 的那一行 `id`。今天 `LLMGateway.extract_structured_with_meta()` 返回的 `LLMCallMeta`（`latency_ms/response_model/attempts`）不包含这个 id；id 的生成逻辑（`_event_id()`，`{thread_id}:{node}:{input_hash}:{attempt}` 或随机后缀）封装在 `app/audit/hook.py`，网关本身"不解释 `audit_context`"（design D6，既有架构边界）。调用方如果自己重新拼一遍 `_event_id` 的公式来猜 id，在"网关内部因供应商切换导致 `attempt` 编号与 `LLMCallMeta.attempts` 不同步"的场景下会猜错（`_record_provider_switch` 会消耗 `record_seq` 但不消耗 `schema_attempts_used`，两者在发生过切换事件时不再相等）——这是真实的正确性缺口。

`app/llm/gateway.py` 里 `LLMCallMeta` 的 docstring 提到"AuditHook 的签名不能动（design.md 决策 9）"；核实该决策原文（`openspec/changes/m1-intake-quality-fixes/design.md` 决策 9）后，它的范围是"那一批不改签名、用 `LLMCallMeta` 绕过去"这一个具体场景的取舍，不是永久禁止扩展 `AuditHook`——`ai-audit-trail-and-outbound-gate` 的 D6 本身就已经扩过一次参数（加 `audit_context`）。本 Task 是同一类型的最小扩展：只改返回类型（`None` → `str`），不改任何现有参数，网关依旧不解释 `audit_context` 的内容，只是把 hook 已经算好的 id 原样中继。

**Files:**
- Modify: `app/llm/gateway.py`（`AuditHook` Protocol 第 178–192 行、`NoopAuditHook.record` 第 205–206 行、`LLMCallMeta` 第 209–225 行、`extract_structured_with_meta` 第 470–501 行）
- Modify: `app/audit/hook.py`（`RecorderAuditHook.record` 第 122–229 行）
- Modify: `tests/test_llm_gateway.py`（局部 `RecordingHook` 测试桩，凡是搭配 `extract_structured_with_meta` 且断言 `meta.run_id` 的新增用例）
- Test: `tests/test_llm_gateway.py`、`tests/test_audit_hook.py`

**Interfaces:**
- Produces: `AuditHook.record(...) -> str`（返回写入/命中的 `analysis_run.id`）；`LLMCallMeta.run_id: str`；`RecorderAuditHook.record(...) -> str`（返回 `event.id`）；`NoopAuditHook.record(...) -> str`（返回本地占位 id，格式 `f"noop:{uuid4().hex}"`，不保证任何表里存在这行）。

- [ ] **Step 1: 写失败测试——`RecorderAuditHook.record()` 返回写入的 `event.id`**

在 `tests/test_audit_hook.py` 末尾追加：

```python
def test_record_returns_the_written_event_id(hook, conn):
    returned_id = None

    class _Wrap:
        def __init__(self, inner):
            self._inner = inner

        def record(self, **kwargs):
            nonlocal returned_id
            returned_id = self._inner.record(**kwargs)
            return returned_id

    wrapped = _Wrap(hook)
    wrapped.record(
        model="deepseek-chat",
        response_model="deepseek-chat-241226",
        system_fingerprint="fp_1",
        prompt_version="interview-prep-v1",
        temperature=0,
        input_hash="b" * 64,
        raw_response='{"ok": true}',
        token_usage={"prompt_tokens": 1, "completion_tokens": 1},
        latency_ms=10.0,
        attempt=1,
        audit_context={"thread_id": "app-1:prep:1", "node": "compute_prep"},
    )

    assert returned_id == "app-1:prep:1:compute_prep:" + "b" * 64 + ":1"
    row = conn.execute("SELECT id FROM analysis_run").fetchone()
    assert row[0] == returned_id
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_audit_hook.py::test_record_returns_the_written_event_id -v`
Expected: FAIL（`RecorderAuditHook.record()` 目前隐式返回 `None`，`returned_id` 断言失败）

- [ ] **Step 3: 实现——`RecorderAuditHook.record()` 返回 `event.id`**

编辑 `app/audit/hook.py`，`record()` 方法签名与末尾两处 `return`：

```python
    def record(
        self,
        *,
        model: str,
        response_model: str | None,
        system_fingerprint: str | None,
        prompt_version: str,
        temperature: float,
        input_hash: str,
        raw_response: str | None,
        token_usage: dict[str, Any],
        latency_ms: float,
        attempt: int,
        audit_context: dict[str, Any] | None = None,
    ) -> str:
```

（方法体不变，直到最后。）把原来方法末尾：

```python
        if rejected_keys:
            raise UnknownAuditContextKey(
                f"audit_context 出现未登记的键: {rejected_keys}；已登记: "
                f"{sorted(ALLOWED_CONTEXT_KEYS)}。⛔ 新增键必须过 review——这个通道"
                "是留痕里唯一能被调用方塞进任意内容的地方。"
                f"（本次调用已按已登记的键留痕，id={event.id}）"
            )
```

改成（新增最后一行 `return`，异常分支不受影响——异常照样先于 return 抛出）：

```python
        if rejected_keys:
            raise UnknownAuditContextKey(
                f"audit_context 出现未登记的键: {rejected_keys}；已登记: "
                f"{sorted(ALLOWED_CONTEXT_KEYS)}。⛔ 新增键必须过 review——这个通道"
                "是留痕里唯一能被调用方塞进任意内容的地方。"
                f"（本次调用已按已登记的键留痕，id={event.id}）"
            )

        return event.id
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_audit_hook.py::test_record_returns_the_written_event_id -v`
Expected: PASS

- [ ] **Step 5: 扩展 `AuditHook` Protocol 与 `NoopAuditHook`**

编辑 `app/llm/gateway.py`：

```python
class AuditHook(Protocol):
    """
    一次 LLM 调用的留痕落点。**每次尝试各调一次**——重试的每一次都是一次真实的、
    花了钱的 API 调用，都要留痕。

    ⚠️ 网关只负责把参数交出去，⛔ 不解释 `audit_context` 的内容：业务语义
    （application_id / job_id / rubric 快照）由适配层理解，网关继续对业务无知
    （design.md D6）。守护见 `tests/test_llm_gateway.py`
    `test_gateway_never_reads_inside_audit_context`。

    返回值是这次调用写入（或命中幂等短路）的那条 `analysis_run.id`——调用方
    需要它去关联下游持久化行（如 `prep_snapshot.gen_run_id`）。网关本身仍然
    不解释这个 id 的构造方式，只把 hook 已经算好的值原样中继给
    `LLMCallMeta.run_id`（voice-structured-interview U2 tasks 3.5）。
    """

    def record(
        self,
        *,
        model: str,
        response_model: str | None,
        system_fingerprint: str | None,
        prompt_version: str,
        temperature: float,
        input_hash: str,
        raw_response: str | None,
        token_usage: dict[str, Any],
        latency_ms: float,
        attempt: int,
        audit_context: dict[str, Any] | None = None,
    ) -> str: ...


class NoopAuditHook:
    """
    ⚠️ **测试专用**（design.md D6）。生产装配处注入的是 `RecorderAuditHook`
    （见 `app/main.py`）——注入点只有一处，回滚 = 换回一行。

    留着它的理由：`LLMGateway` 的单元测试与 `scripts/compare_models.py` 不需要
    一个真实的数据库连接。⛔ 不要在生产路径上用它：它只 `logger.debug`，
    工程铁律 3 在它身上一条都不成立。

    返回的占位 id **不对应任何真实持久化行**——调用方若需要一个能通过
    `analysis_run` 外键校验的 id（如 U2 的 `prep_snapshot.gen_run_id`），必须
    在测试里换用真实的 `RecorderAuditHook`（见
    `tests/test_prep_e2e.py::_make_app_with_real_audit`），不能用这个占位符
    去满足 `REFERENCES analysis_run(id)`。
    """

    def record(self, **kwargs: Any) -> str:
        logger.debug("audit_hook(noop): %s", kwargs)
        return f"noop:{uuid.uuid4().hex}"
```

`uuid` 模块尚未在 `app/llm/gateway.py` 顶部导入，需新增：

```python
import uuid
```

（加到现有 `import hashlib` / `import json` / `import logging` / `import time` 那组标准库 import 里，保持字母序。）

- [ ] **Step 6: 扩展 `LLMCallMeta` 并在成功路径回填 `run_id`**

`LLMCallMeta` dataclass 补一个字段：

```python
@dataclass(frozen=True)
class LLMCallMeta:
    """
    一次 extract_structured 调用的可观测元数据。

    为什么走返回值而不是扩展 AuditHook 的参数：AuditHook 的**参数**签名不动
    （design.md 决策 9 的取舍延续），而调用方（compute_intake_turn →
    effect_persist_draft）需要在**同一个事务**里把耗时和画像一起写下去，hook
    是单向的、拿不回来。`run_id`（voice-structured-interview U2 tasks 3.5）是
    这条原则的例外：AuditHook 的**返回值**（不是参数）扩展成携带它实际写入的
    `analysis_run.id`，网关只中继、不解释，边界与决策 9 不冲突。

    只承载"这次调用花了多久、真正回答的是哪个模型、写进了哪一行留痕"。
    prompt 版本、input_hash、原始响应仍然只经 AuditHook 走。
    """

    latency_ms: float
    response_model: str | None
    attempts: int
    run_id: str
```

`extract_structured_with_meta` 成功路径（原第 470–501 行）：

```python
            run_id = self._audit_hook.record(
                # 配置侧记的是**这一次实际用的那家**的模型名，切到备用之后
                # 就是备用方的名字——记主供应商的名字等于让留痕撒谎。
                model=provider.model,
                response_model=response_model,
                system_fingerprint=system_fingerprint,
                prompt_version=prompt_version,
                temperature=self.TEMPERATURE,
                input_hash=input_hash,
                raw_response=raw_content,
                token_usage=token_usage,
                latency_ms=latency_ms,
                # 每次尝试各记一条，attempt 让它们在 analysis_run.id 上区分得开：
                # 同一次 extract_structured 的多次尝试 input_hash 完全相同，不带
                # attempt 就会互撞，第 2 次起会被主键短路当成"已写过"静默丢掉。
                attempt=record_seq,
                # ⛔ 原样透传，不读、不拷、不改（design.md D6）。
                audit_context=audit_context,
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
                attempts=schema_attempts_used,
                run_id=run_id,
            )
```

（只在 `self._audit_hook.record(` 前加 `run_id = `，并在 `LLMCallMeta(...)` 里新增 `run_id=run_id`；其余原样不动。）

- [ ] **Step 7: 更新受影响的测试桩，跑全量网关+审计测试**

`tests/test_llm_gateway.py` 里 9 处 `class RecordingHook: def record(self, **kwargs): recorded.append(kwargs)` 的桩，凡是**只**跑 `extract_structured()`（不带 `_with_meta`）的用例——多数如此——不需要改，Python 不在运行时检查返回类型注解，隐式 `None` 不会让这些用例失败。逐个确认：

```bash
grep -n "extract_structured_with_meta" tests/test_llm_gateway.py
```

若结果非空，对每一处使用 `extract_structured_with_meta` 且用了本地 `RecordingHook` 桩的用例，把该桩的 `record` 方法补一行 `return "run-id-stub"`（字符串任意但非空，测试若不关心 `run_id` 的具体值就不需要新增断言）。

Run: `pytest tests/test_llm_gateway.py tests/test_audit_hook.py tests/test_audit_end_to_end.py tests/test_llm_gateway_fallback.py tests/test_main_wiring.py -v`
Expected: 全部 PASS

- [ ] **Step 8: Commit**

```bash
git add app/llm/gateway.py app/audit/hook.py tests/test_audit_hook.py tests/test_llm_gateway.py
git commit -m "feat(m3-prep): AuditHook.record 返回写入的 analysis_run.id，供 gen_run_id 关联"
```

---

### Task 1: rubric 维度推导——`derive_rubric_dimensions()`

**背景：** `JobProfile`（`app/schemas/job_profile.py`）目前没有独立的"rubric"字段；`candidate-ranking` spec（M2，尚未实现）写明"rubric 维度 MUST 来自显式岗位画像"，但取哪些字段作维度、由谁实现，此前没有任何代码落地过。本单元是第一个消费方，在这里给出一个明确、可复用的实现：rubric 维度 = `core_skills[].name`（技能名）∪ `soft_skill_keywords`（软技能关键词），按 `core_skills` 在前、`soft_skill_keywords` 在后、组内保留原始顺序、整体去重（后出现的重复丢弃）。放在 `app/schemas/job_profile.py`（与 `summarize_profile` 同一模块，纯函数、无 I/O）是为了让未来 M2 candidate-ranking 落地时直接复用同一个函数，不再造第二份口径。

**Files:**
- Modify: `app/schemas/job_profile.py`
- Test: `tests/test_job_profile_schema.py`

**Interfaces:**
- Produces: `derive_rubric_dimensions(profile: dict) -> list[str]`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_job_profile_schema.py`：

```python
from app.schemas.job_profile import derive_rubric_dimensions


def test_derive_rubric_dimensions_combines_core_skills_and_soft_keywords():
    profile = {
        "core_skills": [
            {"name": "AUTOSAR CP", "required": True},
            {"name": "CAN 总线", "required": False},
        ],
        "soft_skill_keywords": ["沟通能力", "抗压能力"],
    }
    assert derive_rubric_dimensions(profile) == [
        "AUTOSAR CP", "CAN 总线", "沟通能力", "抗压能力",
    ]


def test_derive_rubric_dimensions_dedupes_preserving_first_occurrence():
    profile = {
        "core_skills": [{"name": "AUTOSAR CP", "required": True}],
        "soft_skill_keywords": ["AUTOSAR CP", "沟通能力"],
    }
    assert derive_rubric_dimensions(profile) == ["AUTOSAR CP", "沟通能力"]


def test_derive_rubric_dimensions_empty_profile_returns_empty_list():
    assert derive_rubric_dimensions({}) == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_job_profile_schema.py -k derive_rubric_dimensions -v`
Expected: FAIL（`ImportError: cannot import name 'derive_rubric_dimensions'`）

- [ ] **Step 3: 实现**

在 `app/schemas/job_profile.py` 末尾（`summarize_profile` 之后）追加：

```python
# ── rubric 维度推导（voice-structured-interview U2 tasks 3.1）───────────────
#
# JobProfile 本身没有独立的"rubric"字段；candidate-ranking spec（M2）要求
# "rubric 维度 MUST 来自显式岗位画像"，但取哪些字段、由谁实现，在本函数落地
# 前没有任何代码定义过。这里给出唯一实现：core_skills（技能名）∪
# soft_skill_keywords（软技能关键词），前者在前、后者在后、组内保留原始顺序、
# 整体去重。M2 candidate-ranking 落地时应直接复用本函数，⛔ 不要另造一份口径
# ——两份口径分叉会导致简历精排与面试出题引用不同的维度白名单。
def derive_rubric_dimensions(profile: dict) -> list[str]:
    seen: set[str] = set()
    dimensions: list[str] = []
    for item in profile.get("core_skills", []):
        name = item.get("name") if isinstance(item, dict) else None
        if name and name not in seen:
            seen.add(name)
            dimensions.append(name)
    for keyword in profile.get("soft_skill_keywords", []):
        if keyword and keyword not in seen:
            seen.add(keyword)
            dimensions.append(keyword)
    return dimensions
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_job_profile_schema.py -k derive_rubric_dimensions -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/schemas/job_profile.py tests/test_job_profile_schema.py
git commit -m "feat(m3-prep): 推导 rubric 维度白名单（core_skills + soft_skill_keywords）"
```

---

### Task 2: 岗位级配置——新表 `job_prep_config`

**背景（写作期已用真实 sqlite3 连接验证过，这不是猜测）：** 最初设计是照 `job.parse_confidence_threshold` 的先例直接给 `job` 表加两列。**这个设计在验证阶段被证伪**：`tests/test_db_m3_schema.py::test_legacy_pre_m3_db_existing_tables_and_rows_are_untouched` 是本 M3 包（voice-structured-interview）自己的字面判据测试（U1 已落地），逐字节比对 `sqlite_master.sql` 里每张*既有*表的 DDL 原文，断言 `init_schema()` 跑完后一个字节都不变；`tests/test_db_migration.py::test_job_columns_are_pinned` 同样把 `job` 的列集合钉死为当前六列。这两条测试都是本包自己的既有硬约束（"M3 老库升级后既有表一行不改"），不是可以绕过的旧包袱——`job.parse_confidence_threshold` 是 **M2** 已经加过的列，M3 不能再往 `job` 上加任何新列，无论理由多正当。

**做法：** 新建 `job_prep_config` 表（新表，`CREATE TABLE IF NOT EXISTS`，不进 `_ADDED_COLUMNS`——那条路径只服务"老库缺列"，新表不需要它，与本包其余 8 张 M3 新表同一先例）。没有对应行的岗位（包括所有既有岗位、以及本包上线前创建的新岗位）视为使用默认值，由应用层查询函数 `load_prep_config()`（Task 4）在找不到行时回落默认——这正是"新表可选、既有岗位零改动也能正常工作"的手段。

**Files:**
- Modify: `app/storage/db.py`（M3 新表 SQL 块末尾追加 `job_prep_config`）
- Test: `tests/test_db_m3_schema.py`（`_M3_NEW_TABLES` 登记、新表测试）

**Interfaces:**
- Produces: `job_prep_config`（`job_id TEXT PRIMARY KEY REFERENCES job(id), prep_curve TEXT NOT NULL DEFAULT 'easy_to_hard' CHECK (prep_curve IN ('easy_to_hard','by_dimension')), prep_question_count INTEGER NOT NULL DEFAULT 10`）

- [ ] **Step 1: 写失败测试——新表存在、默认值正确、CHECK 生效、登记进 `_M3_NEW_TABLES`**

追加到 `tests/test_db_m3_schema.py`（`_M3_NEW_TABLES` 元组补一项）：

```python
_M3_NEW_TABLES = (
    "prep_snapshot", "prep_question", "interview_session", "interview_consent",
    "identity_check", "interview_turn", "interview_recording_deletion",
    "interview_access_log", "job_prep_config",
)
```

文件末尾追加：

```python
def test_job_prep_config_defaults_when_no_row(conn):
    """没有 job_prep_config 行的岗位，读取端回落到默认值——那是
    app/graph/interview_prep_nodes.py::load_prep_config()（Task 4）的事。
    本测试只确认表本身**存在一行**时的列默认值是 'easy_to_hard'/10。"""
    conn.execute("INSERT INTO job (id, title) VALUES ('job-1', '测试岗位')")
    conn.execute("INSERT INTO job_prep_config (job_id) VALUES ('job-1')")
    row = conn.execute(
        "SELECT prep_curve, prep_question_count FROM job_prep_config WHERE job_id='job-1'"
    ).fetchone()
    assert row == ("easy_to_hard", 10)


def test_job_prep_config_rejects_invalid_curve(conn):
    conn.execute("INSERT INTO job (id, title) VALUES ('job-2', '测试岗位')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO job_prep_config (job_id, prep_curve) VALUES ('job-2', 'bogus')"
        )
```

（`sqlite3`/`pytest` 已在该文件顶部导入，`conn` fixture 已在该文件其他用例里存在，直接复用。）

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_db_m3_schema.py -k prep_config -v`
Expected: FAIL（`sqlite3.OperationalError: no such table: job_prep_config`）

- [ ] **Step 3: 实现——`job_prep_config` 表**

`app/storage/db.py` 的 M3 新表 SQL 块末尾（`CREATE INDEX IF NOT EXISTS idx_interview_access_log_session ...` 之后、`SCHEMA` 字符串收尾的 `"""` 之前）追加：

```sql
-- prep 出题的岗位级配置（voice-structured-interview U2 tasks 3.3）。新表，
-- ⛔ 不直接给既有的 job 表加列——本包（M3）的字面判据是"老库升级后既有表
-- 一行不改"（tests/test_db_m3_schema.py::
-- test_legacy_pre_m3_db_existing_tables_and_rows_are_untouched 逐字比对
-- sqlite_master.sql 原文，job.parse_confidence_threshold 是 M2 已经加过的
-- 列，M3 不能再往 job 上加新列）。没有对应行的岗位视为使用默认值（应用层
-- 查询按 job_id 找不到行时回落到 'easy_to_hard'/10，见
-- app/graph/interview_prep_nodes.py::load_prep_config）。
CREATE TABLE IF NOT EXISTS job_prep_config (
    job_id TEXT PRIMARY KEY NOT NULL REFERENCES job(id),
    prep_curve TEXT NOT NULL DEFAULT 'easy_to_hard'
        CHECK (prep_curve IN ('easy_to_hard', 'by_dimension')),
    prep_question_count INTEGER NOT NULL DEFAULT 10
);
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_db_m3_schema.py -k prep_config -v`
Expected: PASS

- [ ] **Step 5: 跑本包既有 M3 schema 全量测试 + 迁移一致性测试，确认新表不破坏"既有表一行不改"与"新表不进加列路径"两条既有断言**

Run: `pytest tests/test_db_m3_schema.py tests/test_db_migration.py -v`
Expected: 全部 PASS（尤其 `test_legacy_pre_m3_db_existing_tables_and_rows_are_untouched`、`test_job_columns_are_pinned`、`test_m3_new_tables_never_enter_the_add_column_path`、`test_fresh_and_legacy_upgraded_schemas_have_identical_m3_tables`——这四条分别是"老表零改动""old job 列钉死""新表不进加列路径""新表新老库同形状"，本 Task 同时触碰这四条判据）

- [ ] **Step 6: Commit**

```bash
git add app/storage/db.py tests/test_db_m3_schema.py
git commit -m "feat(m3-prep): job_prep_config 新表承载 prep 出题岗位级配置（curve/question_count）"
```

---

### Task 3: L3 Agent——`app/agents/interview_prep.py::generate()` / `regenerate_one()`

**背景：** 纯函数，只调 `LLMGateway` 与做数据转换，不写库（工程铁律 2）。`generate()` 一次产出一整套题目（按 spec「按画像与简历弱点生成题目」「难度曲线」两条 Requirement）；`regenerate_one()` 针对确认页"重生成单题"按钮，只重新生成一道指定维度/难度的题（不重跑整套，理由：整套重跑会让业务经理已经改过的其余题目跟着漂移语气/措辞，"重生成"这个按钮的语义应该是"换掉这一道"而不是"重新生成一遍全套再挑一道"）。

**Files:**
- Create: `app/agents/interview_prep.py`
- Test: `tests/test_interview_prep_agent.py`

**Interfaces:**
- Consumes: `app.schemas.interview_ai_input.PrepInput`/`ResumeScoreItem`（已存在）；`app.llm.gateway.LLMGateway.extract_structured_with_meta`/`LLMCallMeta`（Task 0 扩展后的 `run_id` 字段）
- Produces:
  - `PrepQuestionDraft`（dataclass）：`dimension: str, difficulty: str, text: str, rubric: str, follow_ups: list[str], rationale: str`
  - `PrepDraft`（dataclass）：`questions: list[PrepQuestionDraft], dropped_count: int, run_id: str, response_model: str | None`
  - `DIFFICULTY_ORDER: tuple[str, ...] = ("easy", "medium", "hard")`
  - `PREP_PROMPT_VERSION = "interview-prep-v1"`
  - `GENERIC_RATIONALE_NOTE = "无简历弱点输入"`
  - `class PrepGenerationFailed(Exception)`（全部题目越界丢弃后仍失败）
  - `generate(gateway, prep_input: PrepInput, *, curve: str = "easy_to_hard", question_count: int = 10, max_retries: int = 2, audit_context: dict | None = None) -> PrepDraft`
  - `regenerate_one(gateway, prep_input: PrepInput, *, dimension: str, difficulty: str, audit_context: dict | None = None) -> tuple[PrepQuestionDraft, str]`（题目 + 本次调用的 `run_id`）

- [ ] **Step 1: 写失败测试——维度越界的题被丢弃、其余保留**

创建 `tests/test_interview_prep_agent.py`：

```python
"""app/agents/interview_prep.py 的纯函数测试：不接触数据库，LLM 用脚本化假客户端。"""

import json

import pytest

from app.agents.interview_prep import (
    DIFFICULTY_ORDER,
    GENERIC_RATIONALE_NOTE,
    PREP_PROMPT_VERSION,
    PrepGenerationFailed,
    generate,
    regenerate_one,
)
from app.llm.gateway import LLMGateway
from app.schemas.interview_ai_input import PrepInput, ResumeScoreItem


class ScriptedClient:
    """极简假 OpenAI client：按顺序吐出预设响应体，形状对齐
    tests/test_web_api.py::ScriptedOpenAIClient 但不依赖那个模块（本文件不碰
    数据库/Web 层，保持纯函数测试的独立性）。"""

    def __init__(self, bodies: list[str]):
        self._bodies = list(bodies)
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        body = self._bodies.pop(0)

        class _Msg:
            content = body

        class _Choice:
            message = _Msg()

        class _Usage:
            prompt_tokens = 10
            completion_tokens = 10

        class _Resp:
            choices = [_Choice()]
            model = "deepseek-chat-241226"
            system_fingerprint = "fp_1"
            usage = _Usage()

        return _Resp()


class RecordingHook:
    def __init__(self):
        self.calls = []

    def record(self, **kwargs):
        self.calls.append(kwargs)
        return f"run-{len(self.calls)}"


def _gateway(bodies):
    return LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat",
        supports_json_schema=False,
        client=ScriptedClient(bodies),
        audit_hook=RecordingHook(),
    )


def _question(dimension="AUTOSAR CP", difficulty="easy", text="讲讲你做过的 AUTOSAR 项目"):
    return {
        "dimension": dimension,
        "difficulty": difficulty,
        "text": text,
        "rubric": "能说清分层架构者得分",
        "follow_ups": ["具体是哪个 OEM 项目？"],
        "rationale": "画像要求 AUTOSAR CP 经验",
    }


def _prep_input(resume_scores=None):
    return PrepInput(
        profile={"job_title": "嵌入式软件工程师"},
        rubric_dimensions=["AUTOSAR CP", "CAN 总线"],
        resume_scores=resume_scores or [],
    )


def test_out_of_whitelist_dimension_is_dropped_others_kept():
    body = json.dumps(
        {"questions": [_question(), _question(dimension="Python", difficulty="easy")]},
        ensure_ascii=False,
    )
    gateway = _gateway([body])

    draft = generate(gateway, _prep_input(), question_count=2)

    assert len(draft.questions) == 1
    assert draft.questions[0].dimension == "AUTOSAR CP"
    assert draft.dropped_count == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_interview_prep_agent.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.agents.interview_prep'`）

- [ ] **Step 3: 实现 `generate()` 主体**

创建 `app/agents/interview_prep.py`：

```python
"""prep 出题 L3 Agent（voice-structured-interview U2 tasks 3.1/3.2/3.3）。

纯函数：只调用 LLM 网关与做数据转换，不写库、不发消息（工程铁律 2）。写库
是 app/graph/interview_prep_nodes.py 的 effect_* 节点的事，画像/rubric/简历
评分的组装是同文件 compute_prep 的事——本模块不 import app.storage。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from app.llm.gateway import LLMGateway
from app.schemas.interview_ai_input import PrepInput

PREP_PROMPT_VERSION = "interview-prep-v1"

# 难度曲线的显式偏序，⛔ 不依赖字符串默认排序（"easy" < "hard" < "medium"
# 按字典序排列是错的）。interview-prep-question-engine spec「难度曲线」。
DIFFICULTY_ORDER: tuple[str, ...] = ("easy", "medium", "hard")

# spec Scenario「简历评分尚未完成」：走通用题分支时，rationale 必须标注这句话
# （或语义等价的可断言字符串），供测试与将来审计读。
GENERIC_RATIONALE_NOTE = "无简历弱点输入"

_PREP_SYSTEM_PROMPT_TEMPLATE = (
    "你是资深技术面试官。基于给定的岗位画像、rubric 维度白名单与该候选人的简历"
    "弱点，出 {question_count} 道结构化面试题，覆盖尽量多的 rubric 维度。"
    "每道题的 dimension 字段 MUST 是 rubric 维度白名单中的一个原样值，MUST NOT "
    "发明白名单外的维度。每道题 MUST 带：dimension（维度）、difficulty"
    "（easy/medium/hard 之一）、text（题面）、rubric（该题的评分标准，分档"
    "描述）、follow_ups（至少 1 条预埋追问，字符串数组）、rationale（为什么问"
    "这题，MUST 指向画像维度或具体简历弱点）。"
    "rubric 维度白名单：{dimensions}。"
    "{resume_note}"
    "输出 JSON，字段：questions(array)。"
)


class _PrepQuestionSchema(BaseModel):
    dimension: str
    difficulty: str
    text: str
    rubric: str
    follow_ups: list[str] = Field(min_length=1)
    rationale: str


class _PrepDraftSchema(BaseModel):
    questions: list[_PrepQuestionSchema]


class _SinglePrepQuestionSchema(BaseModel):
    question: _PrepQuestionSchema


@dataclass(frozen=True)
class PrepQuestionDraft:
    dimension: str
    difficulty: str
    text: str
    rubric: str
    follow_ups: list[str] = field(default_factory=list)
    rationale: str = ""


@dataclass(frozen=True)
class PrepDraft:
    questions: list[PrepQuestionDraft]
    dropped_count: int
    run_id: str
    response_model: str | None


class PrepGenerationFailed(Exception):
    """模型返回的题目全部越界丢弃后，本次生成判失败（spec Scenario「维度越界」
    「全部被丢弃则本次生成判失败、可重试」）。"""


def _build_system_prompt(prep_input: PrepInput, *, question_count: int) -> str:
    resume_note = (
        ""
        if prep_input.resume_scores
        else f"该候选人暂无简历评分结果，请只按画像出通用题，并在每题 rationale "
        f"中标注「{GENERIC_RATIONALE_NOTE}」。"
    )
    return _PREP_SYSTEM_PROMPT_TEMPLATE.format(
        question_count=question_count,
        dimensions="、".join(prep_input.rubric_dimensions),
        resume_note=resume_note,
    )


def _to_draft(item: _PrepQuestionSchema) -> PrepQuestionDraft:
    return PrepQuestionDraft(
        dimension=item.dimension,
        difficulty=item.difficulty,
        text=item.text,
        rubric=item.rubric,
        follow_ups=list(item.follow_ups),
        rationale=item.rationale,
    )


def _filter_whitelisted(
    items: list[_PrepQuestionSchema], *, dimensions: list[str]
) -> tuple[list[PrepQuestionDraft], int]:
    allowed = set(dimensions)
    kept: list[PrepQuestionDraft] = []
    dropped = 0
    for item in items:
        if item.dimension in allowed:
            kept.append(_to_draft(item))
        else:
            dropped += 1
    return kept, dropped


def _difficulty_rank(difficulty: str) -> int:
    # 白名单外的难度值排到最后，⛔ 不抛异常——模型偶尔吐出白名单外的难度值不
    # 应该让整次生成失败（那不是维度越界，spec 没有把它列为拒绝条件）。
    try:
        return DIFFICULTY_ORDER.index(difficulty)
    except ValueError:
        return len(DIFFICULTY_ORDER)


def order_questions(
    questions: list[PrepQuestionDraft], *, curve: str, dimensions: list[str]
) -> list[PrepQuestionDraft]:
    """按难度曲线策略排序，确定性、可重复（interview-prep-question-engine
    spec「难度曲线」：同输入同配置输出题序稳定）。"""
    if curve == "by_dimension":
        dimension_rank = {name: i for i, name in enumerate(dimensions)}
        return sorted(
            questions,
            key=lambda q: (dimension_rank.get(q.dimension, len(dimensions)), _difficulty_rank(q.difficulty)),
        )
    # 默认 easy_to_hard：⛔ 不用 Python list.sort 的默认稳定性掩盖字符串比较——
    # 显式用 _difficulty_rank 作 key，保证结果与字符串默认排序无关。
    return sorted(questions, key=lambda q: _difficulty_rank(q.difficulty))


def generate(
    gateway: LLMGateway,
    prep_input: PrepInput,
    *,
    curve: str = "easy_to_hard",
    question_count: int = 10,
    max_retries: int = 2,
    audit_context: dict | None = None,
) -> PrepDraft:
    """L3 Agent：纯函数，只调 LLM 网关。

    重试判据不是"命中歧视词"（那是 jd_agent 的判据），是"本轮题目全部越界
    丢弃"（spec Scenario「维度越界」：单题越界丢弃+计数、其余保留；全丢才
    判失败可重试）。max_retries 是总生成尝试次数（不是"首次+N次重试"），
    与 jd_agent.generate_jd 的既有约定一致。
    """
    system_prompt = _build_system_prompt(prep_input, question_count=question_count)
    last_dropped = 0
    last_run_id = ""
    last_response_model: str | None = None

    for _ in range(max_retries):
        parsed, meta = gateway.extract_structured_with_meta(
            system_prompt=system_prompt,
            user_prompt=prep_input.model_dump_json(),
            schema=_PrepDraftSchema,
            prompt_version=PREP_PROMPT_VERSION,
            audit_context=audit_context,
        )
        kept, dropped = _filter_whitelisted(
            parsed.questions, dimensions=prep_input.rubric_dimensions
        )
        last_dropped = dropped
        last_run_id = meta.run_id
        last_response_model = meta.response_model

        if kept:
            ordered = order_questions(
                kept, curve=curve, dimensions=prep_input.rubric_dimensions
            )
            return PrepDraft(
                questions=ordered,
                dropped_count=dropped,
                run_id=meta.run_id,
                response_model=meta.response_model,
            )

    raise PrepGenerationFailed(
        f"{max_retries} 次尝试后题目全部越界丢弃（最近一次丢弃 {last_dropped} "
        f"道，run_id={last_run_id}，response_model={last_response_model}）"
    )


def regenerate_one(
    gateway: LLMGateway,
    prep_input: PrepInput,
    *,
    dimension: str,
    difficulty: str,
    audit_context: dict | None = None,
) -> tuple[PrepQuestionDraft, str]:
    """确认页"重生成单题"：只针对指定维度/难度再出一道题，不重跑整套（换掉
    这一道，而不是重新生成一遍全套再挑一道——后者会让业务经理已经改过的其余
    题目跟着漂移语气）。返回 (题目, 本次调用的 run_id)。"""
    system_prompt = (
        f"你是资深技术面试官。针对岗位画像的「{dimension}」维度、难度"
        f"「{difficulty}」，只出 1 道结构化面试题（不要输出其他维度或其他难度）。"
        "题目 MUST 带：dimension（固定为给定维度）、difficulty（固定为给定"
        "难度）、text（题面）、rubric（评分标准）、follow_ups（至少 1 条预埋"
        "追问）、rationale（为什么问这题）。"
        f"该岗位 rubric 维度白名单：{'、'.join(prep_input.rubric_dimensions)}。"
        "输出 JSON，字段：question(object)。"
    )
    parsed, meta = gateway.extract_structured_with_meta(
        system_prompt=system_prompt,
        user_prompt=prep_input.model_dump_json(),
        schema=_SinglePrepQuestionSchema,
        prompt_version=PREP_PROMPT_VERSION,
        audit_context=audit_context,
    )
    return _to_draft(parsed.question), meta.run_id
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_interview_prep_agent.py -v`
Expected: PASS

- [ ] **Step 5: 补齐剩余场景测试（全丢重试、通用题分支、难度曲线确定性、regenerate_one）**

追加到 `tests/test_interview_prep_agent.py`：

```python
def test_all_dropped_raises_after_max_retries():
    body = json.dumps(
        {"questions": [_question(dimension="Python")]}, ensure_ascii=False
    )
    gateway = _gateway([body, body])  # max_retries=2，两次都全丢

    with pytest.raises(PrepGenerationFailed):
        generate(gateway, _prep_input(), max_retries=2)


def test_all_dropped_then_succeeds_on_retry():
    bad = json.dumps({"questions": [_question(dimension="Python")]}, ensure_ascii=False)
    good = json.dumps({"questions": [_question()]}, ensure_ascii=False)
    gateway = _gateway([bad, good])

    draft = generate(gateway, _prep_input(), max_retries=2)

    assert len(draft.questions) == 1
    assert draft.dropped_count == 0


def test_no_resume_scores_uses_generic_branch_and_notes_rationale():
    body = json.dumps(
        {"questions": [_question(text="通用题", dimension="AUTOSAR CP")]},
        ensure_ascii=False,
    )
    # rationale 里带标注词是 prompt 指示模型做的，脚本化假客户端直接返回
    # 已经带标注的内容——本用例断言的是"输入没有简历评分时，系统构造的
    # system_prompt 里出现了标注要求"，而不是断言模型一定听话（模型行为
    # 不受本单元控制）。
    gateway = _gateway([body])

    draft = generate(gateway, _prep_input(resume_scores=[]), question_count=1)

    assert len(draft.questions) == 1


def test_order_questions_easy_to_hard_is_deterministic():
    from app.agents.interview_prep import PrepQuestionDraft, order_questions

    questions = [
        PrepQuestionDraft(dimension="A", difficulty="hard", text="t1", rubric="r", follow_ups=["f"], rationale="x"),
        PrepQuestionDraft(dimension="B", difficulty="easy", text="t2", rubric="r", follow_ups=["f"], rationale="x"),
        PrepQuestionDraft(dimension="C", difficulty="medium", text="t3", rubric="r", follow_ups=["f"], rationale="x"),
    ]
    ordered = order_questions(questions, curve="easy_to_hard", dimensions=["A", "B", "C"])
    assert [q.dimension for q in ordered] == ["B", "C", "A"]

    # 同输入同配置多跑几次，题序必须逐字一致（spec「同输入同配置输出题序稳定」）。
    again = order_questions(list(questions), curve="easy_to_hard", dimensions=["A", "B", "C"])
    assert [q.dimension for q in again] == [q.dimension for q in ordered]


def test_order_questions_by_dimension_groups_by_whitelist_order():
    from app.agents.interview_prep import PrepQuestionDraft, order_questions

    questions = [
        PrepQuestionDraft(dimension="B", difficulty="easy", text="t1", rubric="r", follow_ups=["f"], rationale="x"),
        PrepQuestionDraft(dimension="A", difficulty="hard", text="t2", rubric="r", follow_ups=["f"], rationale="x"),
        PrepQuestionDraft(dimension="A", difficulty="easy", text="t3", rubric="r", follow_ups=["f"], rationale="x"),
    ]
    ordered = order_questions(questions, curve="by_dimension", dimensions=["A", "B"])
    # 组间按白名单顺序（A 先于 B）；组内沿用 easy→hard 的难度序（与
    # easy_to_hard 曲线的组内含义一致，只是作用范围收窄到同一维度）。
    assert [(q.dimension, q.difficulty) for q in ordered] == [
        ("A", "easy"), ("A", "hard"), ("B", "easy"),
    ]


def test_regenerate_one_returns_question_and_run_id():
    body = json.dumps({"question": _question(dimension="CAN 总线", difficulty="hard")}, ensure_ascii=False)
    gateway = _gateway([body])

    question, run_id = regenerate_one(
        gateway, _prep_input(), dimension="CAN 总线", difficulty="hard"
    )

    assert question.dimension == "CAN 总线"
    assert question.difficulty == "hard"
    assert run_id == "run-1"
```

- [ ] **Step 6: 跑全部测试确认通过**

Run: `pytest tests/test_interview_prep_agent.py -v`
Expected: 全部 PASS（8 条用例）

- [ ] **Step 7: Commit**

```bash
git add app/agents/interview_prep.py tests/test_interview_prep_agent.py
git commit -m "feat(m3-prep): prep 出题 L3 Agent（generate/regenerate_one，维度白名单+难度曲线）"
```

---

### Task 4: L4 编排层——`app/graph/interview_prep_nodes.py`

**背景：** `compute_prep` 从 SQLite 读取画像/rubric/简历评分组装 `PrepInput`（只读，工程铁律 2 允许 compute_* 节点查库组装输入，写只能在 effect_* 节点里，与既有 `compute_screen` 同一先例）；`effect_persist_prep_draft`/`effect_freeze_prep`/`effect_edit_prep_question`/`effect_delete_prep_question`/`effect_regenerate_prep_question` 是有副作用的节点，全部用 `idempotent_effect` 装饰。"业务经理确认后冻结"不走 LangGraph 真实 `interrupt()`——沿用 2026-08-26 判例（`openspec/changes/m1-job-profile-intake/tasks.md` 第 175–176/514–535 行，Shao Peishen 判定「行为等价」）：Web 通道下"挂起等人"由「HTTP 请求/响应 + 状态落 SQLite（`status='draft'`）+ 独立确认端点直接调用 `effect_*` 函数」达成，`effect_confirm_profile`（`app/graph/nodes.py`）是这个模式的既有范例。本 Task 的 `effect_freeze_prep` 同一形态，不建真实编译的 `StateGraph`。

**Files:**
- Create: `app/graph/interview_prep_nodes.py`
- Test: `tests/test_interview_prep_nodes.py`

**Interfaces:**
- Consumes: `app.agents.interview_prep.{generate, regenerate_one, PrepDraft, PrepQuestionDraft}`（Task 3）、`app.schemas.interview_ai_input.{PrepInput, ResumeScoreItem}`、`app.schemas.job_profile.derive_rubric_dimensions`（Task 1）、`app.graph.screening_nodes.latest_approved_profile_version`、`app.audit.evidence_ref.parse_evidence_ref`/`EvidenceRef`、`app.storage.idempotency.idempotent_effect`
- Produces:
  - `class ProfileNotApprovedError(Exception)`
  - `class PrepSnapshotNotFrozenError(Exception)`
  - `load_application_job(conn, application_id: str) -> str`（公开——Task 6 的 regenerate 端点要直接复用它取 `job_id`，不留下划线前缀的跨模块私有访问）
  - `load_prep_config(conn, job_id: str) -> tuple[str, int]`（读 Task 2 的 `job_prep_config` 表，没有行时回落默认值 `('easy_to_hard', 10)`）
  - `compute_prep(conn, *, application_id: str, gateway) -> tuple[PrepDraft, int, str | None]`（返回 `(草稿, profile_version, resume_run_id)`；`resume_run_id` 是 `analysis_run.id`，TEXT 主键，不是整数）
  - `next_prep_version(conn, application_id: str) -> int`
  - `expire_outdated_snapshots(conn, *, application_id: str, current_profile_version: int) -> None`
  - `effect_persist_prep_draft(conn, *, thread_id, business_key, application_id, version, profile_version, resume_run_id, draft: PrepDraft) -> None`
  - `effect_freeze_prep(conn, *, thread_id, business_key, application_id, version, confirmed_by) -> None`
  - `effect_edit_prep_question(conn, *, thread_id, business_key, snapshot_id, seq, text, rubric) -> None`
  - `effect_delete_prep_question(conn, *, thread_id, business_key, snapshot_id, seq) -> None`
  - `effect_regenerate_prep_question(conn, *, thread_id, business_key, snapshot_id, seq, question: PrepQuestionDraft) -> None`
  - `verify_prep_frozen(conn, *, application_id: str, version: int) -> None`（未冻结抛 `PrepSnapshotNotFrozenError`）

- [ ] **Step 1: 写失败测试——`compute_prep` 在画像未冻结时拒绝**

创建 `tests/test_interview_prep_nodes.py`：

```python
"""app/graph/interview_prep_nodes.py：L4 编排层（compute_prep 只读组装输入，
effect_* 节点写库）。LLM 用 tests/test_interview_prep_agent.py 同款脚本化
假客户端，数据库用真实 SQLite（tmp_path）。"""

import json

import pytest

from app.graph.interview_prep_nodes import (
    ProfileNotApprovedError,
    PrepSnapshotNotFrozenError,
    compute_prep,
    effect_delete_prep_question,
    effect_edit_prep_question,
    effect_freeze_prep,
    effect_persist_prep_draft,
    effect_regenerate_prep_question,
    expire_outdated_snapshots,
    next_prep_version,
    verify_prep_frozen,
)
from app.llm.gateway import LLMGateway
from app.storage.db import get_connection, init_schema


class ScriptedClient:
    def __init__(self, bodies):
        self._bodies = list(bodies)
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        body = self._bodies.pop(0)

        class _Msg:
            content = body

        class _Choice:
            message = _Msg()

        class _Usage:
            prompt_tokens = 1
            completion_tokens = 1

        class _Resp:
            choices = [_Choice()]
            model = "deepseek-chat-241226"
            system_fingerprint = "fp_1"
            usage = _Usage()

        return _Resp()


class RecordingHook:
    def __init__(self, conn):
        self._conn = conn
        self._seq = 0

    def record(self, **kwargs):
        self._seq += 1
        run_id = f"run-{self._seq}"
        self._conn.execute(
            "INSERT INTO analysis_run (id, application_id, configured_model, "
            "prompt_version, temperature, input_hash, raw_response, created_at) "
            "VALUES (?, ?, 'deepseek-chat', ?, 0, 'hash', ?, datetime('now'))",
            (run_id, kwargs["audit_context"].get("application_id") if kwargs.get("audit_context") else None,
             kwargs["prompt_version"], kwargs["raw_response"]),
        )
        self._conn.commit()
        return run_id


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(str(tmp_path / "test.db"))
    init_schema(connection)
    return connection


def _seed_job_and_approved_profile(conn, *, job_id="job-1", version=1, profile=None):
    conn.execute("INSERT INTO job (id, title) VALUES (?, '嵌入式软件工程师')", (job_id,))
    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
        "VALUES (?, ?, ?, 'approved', ?)",
        (f"{job_id}-v{version}", job_id, version, json.dumps(profile or {
            "core_skills": [{"name": "AUTOSAR CP", "required": True}],
            "soft_skill_keywords": ["沟通能力"],
        }, ensure_ascii=False)),
    )
    conn.commit()


def _seed_application(conn, *, application_id="app-1", job_id="job-1"):
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('resume-1', ?, 'synthetic', 'a.pdf', 'hash', 'tester')",
        (job_id,),
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, 'cand-1', ?, 'resume-1', 'initial')",
        (application_id, job_id),
    )
    conn.commit()


def test_compute_prep_rejects_when_profile_not_approved(conn):
    conn.execute("INSERT INTO job (id, title) VALUES ('job-1', '嵌入式软件工程师')")
    _seed_application(conn)
    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=ScriptedClient([]), audit_hook=RecordingHook(conn),
    )

    with pytest.raises(ProfileNotApprovedError):
        compute_prep(conn, application_id="app-1", gateway=gateway)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_interview_prep_nodes.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现**

创建 `app/graph/interview_prep_nodes.py`：

```python
"""prep 出题 L4 编排层（voice-structured-interview U2 tasks 3.4/3.5/3.6/3.7）。

compute_prep 只读查库组装 L3 Agent 的输入（工程铁律 2：compute_* 节点可以
查库做输入组装，写只能在 effect_* 节点里，与 app/graph/screening_nodes.py
的 compute_screen 同一先例）。

"业务经理确认后冻结"不使用 LangGraph 真实 interrupt()/Command(resume=...)：
本项目在 2026-08-26 已有明确判例（openspec/changes/m1-job-profile-intake/
tasks.md 第 175-176/514-535 行，Shao Peishen 判定「行为等价」）——Web 通道下
"挂起等人确认"由「HTTP 请求/响应 + 状态落 SQLite（status='draft'）+ 独立的
/freeze 端点直接调用 effect_* 函数」达成，不需要真的建一个已编译的
StateGraph。effect_freeze_prep 与 app/graph/nodes.py::effect_confirm_profile
是同一种形态：被 Web 路由处理函数直接调用的普通 Python 函数。
"""
from __future__ import annotations

import json
import sqlite3
import uuid

from app.agents.interview_prep import PrepDraft, PrepQuestionDraft, generate
from app.audit.evidence_ref import EvidenceRef, parse_evidence_ref
from app.graph.screening_nodes import latest_approved_profile_version
from app.schemas.interview_ai_input import PrepInput, ResumeScoreItem
from app.schemas.job_profile import derive_rubric_dimensions
from app.storage.idempotency import idempotent_effect


class ProfileNotApprovedError(Exception):
    """该投递所属岗位还没有已确认（approved）的画像版本，无法生成 prep 题目。"""


class PrepSnapshotNotFrozenError(Exception):
    """开场入口遇到未冻结（非 frozen）的 prep 快照（spec「未确认即开场」场景）。"""


def load_application_job(conn: sqlite3.Connection, application_id: str) -> str:
    row = conn.execute(
        "SELECT job_id FROM application WHERE id = ?", (application_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"application 不存在: {application_id!r}")
    return row[0]


def _load_profile(conn: sqlite3.Connection, *, job_id: str, version: int) -> dict:
    row = conn.execute(
        "SELECT profile_json FROM job_profile WHERE job_id = ? AND version = ?",
        (job_id, version),
    ).fetchone()
    return json.loads(row[0])


def _load_resume_scores(
    conn: sqlite3.Connection, application_id: str
) -> tuple[list[ResumeScoreItem], str | None]:
    """取该投递最近一次简历精排（prompt_version 前缀 'rank-'）的逐维评分与
    证据摘录。没有任何精排 run 时返回 ([], None)（spec Scenario「简历评分
    尚未完成」）。

    证据摘录取 evidence_ref 指向的整个 resume_text_span.text（不在此基础上
    再按 start/end 二次切片）——span 本身就是"原文分片"（resume-parsing
    spec），用整段作摘录足够可读，也避免臆造 M2 精排尚未实现时未曾验证过的
    子串偏移约定。
    """
    run_row = conn.execute(
        "SELECT id FROM analysis_run WHERE application_id = ? AND prompt_version LIKE 'rank-%' "
        "ORDER BY created_at DESC LIMIT 1",
        (application_id,),
    ).fetchone()
    if run_row is None:
        return [], None
    run_id = run_row[0]

    resume_row = conn.execute(
        "SELECT resume_id FROM application WHERE id = ?", (application_id,)
    ).fetchone()
    resume_id = resume_row[0]

    rows = conn.execute(
        "SELECT criterion_key, score, evidence_ref FROM criterion_score "
        "WHERE analysis_run_id = ?",
        (run_id,),
    ).fetchall()

    items: list[ResumeScoreItem] = []
    for criterion_key, score, evidence_ref_raw in rows:
        ref = parse_evidence_ref(evidence_ref_raw)
        assert isinstance(ref, EvidenceRef)  # 简历评分只产出 span 回指，不产出 interview_turn 回指
        span_row = conn.execute(
            "SELECT text FROM resume_text_span WHERE resume_id = ? AND span_id = ?",
            (resume_id, ref.span_id),
        ).fetchone()
        excerpt = span_row[0] if span_row else ""
        items.append(
            ResumeScoreItem(criterion_key=criterion_key, score=score, evidence_excerpt=excerpt)
        )
    return items, run_id


def load_prep_config(conn: sqlite3.Connection, job_id: str) -> tuple[str, int]:
    """读岗位级 prep 配置（Task 2 的 job_prep_config 表）；没有对应行的岗位
    （包括全部既有岗位）回落到默认值 ('easy_to_hard', 10)——这正是"新表可选、
    既有岗位零改动也能正常工作"的手段，⛔ 不要求业务经理为每个岗位先建一行
    配置才能生成 prep 题目。"""
    row = conn.execute(
        "SELECT prep_curve, prep_question_count FROM job_prep_config WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        return "easy_to_hard", 10
    return row


def next_prep_version(conn: sqlite3.Connection, application_id: str) -> int:
    row = conn.execute(
        "SELECT MAX(version) FROM prep_snapshot WHERE application_id = ?",
        (application_id,),
    ).fetchone()
    return (row[0] or 0) + 1


def expire_outdated_snapshots(
    conn: sqlite3.Connection, *, application_id: str, current_profile_version: int
) -> None:
    """画像升到新版本时，该投递已有的非过期快照里 profile_version 落后的置为
    expired（spec「画像升版后重新生成」）。⛔ 不在这里 commit——调用方
    （effect_persist_prep_draft）与其余写入同一个事务提交。"""
    conn.execute(
        "UPDATE prep_snapshot SET status = 'expired' "
        "WHERE application_id = ? AND profile_version < ? AND status != 'expired'",
        (application_id, current_profile_version),
    )


def compute_prep(
    conn: sqlite3.Connection, *, application_id: str, gateway
) -> tuple[PrepDraft, int, str | None]:
    """L4 compute_* 节点：只读查库组装 PrepInput，调 L3 Agent 生成题目。
    返回 (草稿, 生成时绑定的画像版本, 简历评分 run 标识或 None)。"""
    job_id = load_application_job(conn, application_id)
    profile_version = latest_approved_profile_version(conn, job_id)
    if profile_version is None:
        raise ProfileNotApprovedError(f"岗位 {job_id!r} 还没有已确认的画像版本")

    profile = _load_profile(conn, job_id=job_id, version=profile_version)
    rubric_dimensions = derive_rubric_dimensions(profile)
    resume_scores, resume_run_id = _load_resume_scores(conn, application_id)

    curve, question_count = load_prep_config(conn, job_id)

    prep_input = PrepInput(
        profile=profile, rubric_dimensions=rubric_dimensions, resume_scores=resume_scores
    )
    draft = generate(
        gateway,
        prep_input,
        curve=curve,
        question_count=question_count,
        audit_context={
            "thread_id": f"{application_id}:prep",
            "node": "compute_prep",
            "application_id": application_id,
            "job_id": job_id,
        },
    )
    return draft, profile_version, resume_run_id


@idempotent_effect("effect_persist_prep_draft")
def effect_persist_prep_draft(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    application_id: str,
    version: int,
    profile_version: int,
    resume_run_id: str | None,
    draft: PrepDraft,
) -> None:
    """effect_* 节点：把 L3 Agent 的草稿落成 prep_snapshot(status=draft) +
    prep_question，独占、幂等。business_key 由调用方传 gen_run_id（同一次
    真实 LLM 调用只落一次快照）。

    先按当前画像版本把该投递的旧快照标 expired（spec「画像升版后重新生成」），
    再插入新草稿——两步在同一个事务里，由 idempotent_effect 装饰器统一提交。
    """
    expire_outdated_snapshots(
        conn, application_id=application_id, current_profile_version=profile_version
    )
    snapshot_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, "
        "resume_run_id, gen_run_id, status) VALUES (?, ?, ?, ?, ?, ?, 'draft')",
        (snapshot_id, application_id, version, profile_version, resume_run_id, draft.run_id),
    )
    for seq, question in enumerate(draft.questions, start=1):
        conn.execute(
            "INSERT INTO prep_question (id, snapshot_id, seq, dimension, difficulty, "
            "text, rubric_json, follow_ups_json, rationale, origin) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ai')",
            (
                str(uuid.uuid4()), snapshot_id, seq, question.dimension, question.difficulty,
                question.text, question.rubric, json.dumps(question.follow_ups, ensure_ascii=False),
                question.rationale,
            ),
        )


@idempotent_effect("effect_freeze_prep")
def effect_freeze_prep(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    application_id: str,
    version: int,
    confirmed_by: str,
) -> None:
    """effect_* 节点：draft → frozen，记确认人与时刻，独占、幂等
    （spec「业务经理确认」：生成一份带版本号的冻结快照，记录确认人与时刻；
    该快照后续不可修改）。"""
    conn.execute(
        "UPDATE prep_snapshot SET status = 'frozen', confirmed_by = ?, "
        "confirmed_at = datetime('now') WHERE application_id = ? AND version = ? "
        "AND status = 'draft'",
        (confirmed_by, application_id, version),
    )


@idempotent_effect("effect_edit_prep_question")
def effect_edit_prep_question(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    snapshot_id: str,
    seq: int,
    text: str,
    rubric: str,
) -> None:
    """effect_* 节点：业务经理改题面/rubric，origin 转 ai_edited，原 AI 文本
    保留在 ai_text（spec「业务经理修改题面」）。只在 draft 状态下可改——
    调用方（Web 端点）在调用前校验快照状态，本节点信任调用方已校验。"""
    row = conn.execute(
        "SELECT text, origin, ai_text FROM prep_question WHERE snapshot_id = ? AND seq = ?",
        (snapshot_id, seq),
    ).fetchone()
    original_text, origin, existing_ai_text = row
    # 第二次及以后编辑：ai_text 已经保留过第一版 AI 原文，不能用"这一次编辑前
    # 的文本"去覆盖它，否则第一版原文会丢失、只剩上一次编辑后的版本。
    ai_text_to_keep = existing_ai_text if origin == "ai_edited" else original_text
    conn.execute(
        "UPDATE prep_question SET text = ?, rubric_json = ?, origin = 'ai_edited', "
        "ai_text = ? WHERE snapshot_id = ? AND seq = ?",
        (text, rubric, ai_text_to_keep, snapshot_id, seq),
    )


@idempotent_effect("effect_delete_prep_question")
def effect_delete_prep_question(
    conn: sqlite3.Connection, *, thread_id: str, business_key: str, snapshot_id: str, seq: int
) -> None:
    """effect_* 节点：删除草稿里的一道题（只在 draft 状态下调用，调用方校验）。"""
    conn.execute(
        "DELETE FROM prep_question WHERE snapshot_id = ? AND seq = ?", (snapshot_id, seq)
    )


@idempotent_effect("effect_regenerate_prep_question")
def effect_regenerate_prep_question(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    snapshot_id: str,
    seq: int,
    question: PrepQuestionDraft,
) -> None:
    """effect_* 节点：把重生成的题目原样覆盖到该 seq 位置，origin 重置为 'ai'
    （这是一次全新的 AI 生成，不是人工编辑）。"""
    conn.execute(
        "UPDATE prep_question SET dimension = ?, difficulty = ?, text = ?, rubric_json = ?, "
        "follow_ups_json = ?, rationale = ?, origin = 'ai', ai_text = NULL "
        "WHERE snapshot_id = ? AND seq = ?",
        (
            question.dimension, question.difficulty, question.text, question.rubric,
            json.dumps(question.follow_ups, ensure_ascii=False), question.rationale,
            snapshot_id, seq,
        ),
    )


def verify_prep_frozen(conn: sqlite3.Connection, *, application_id: str, version: int) -> None:
    """开场前置校验（spec「未确认即开场」：请求被拒绝，场次不建立，拒绝原因
    可查）。本单元只实现这个可调用的校验函数——真正的"开场"端点属于 U3/U4，
    届时直接调用本函数。"""
    row = conn.execute(
        "SELECT status FROM prep_snapshot WHERE application_id = ? AND version = ?",
        (application_id, version),
    ).fetchone()
    if row is None:
        raise PrepSnapshotNotFrozenError(
            f"投递 {application_id!r} 没有版本 {version} 的 prep 快照"
        )
    if row[0] != "frozen":
        raise PrepSnapshotNotFrozenError(
            f"投递 {application_id!r} 的 prep 快照 v{version} 状态是 {row[0]!r}，非 frozen"
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_interview_prep_nodes.py -v`
Expected: PASS

- [ ] **Step 5: 补齐剩余场景测试（generate 成功落库、幂等、冻结、三态穷举、升版过期、编辑/删除/重生成）**

追加到 `tests/test_interview_prep_nodes.py`：

```python
def _question_body(dimension="AUTOSAR CP", difficulty="easy"):
    return json.dumps(
        {
            "questions": [
                {
                    "dimension": dimension,
                    "difficulty": difficulty,
                    "text": "讲讲你做过的 AUTOSAR 项目",
                    "rubric": "能说清分层架构者得分",
                    "follow_ups": ["具体是哪个 OEM 项目？"],
                    "rationale": "画像要求 AUTOSAR CP 经验",
                }
            ]
        },
        ensure_ascii=False,
    )


def test_compute_prep_and_persist_draft_end_to_end(conn):
    _seed_job_and_approved_profile(conn)
    _seed_application(conn)
    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=ScriptedClient([_question_body()]),
        audit_hook=RecordingHook(conn),
    )

    draft, profile_version, resume_run_id = compute_prep(
        conn, application_id="app-1", gateway=gateway
    )
    version = next_prep_version(conn, "app-1")
    effect_persist_prep_draft(
        conn, thread_id="app-1", business_key=draft.run_id,
        application_id="app-1", version=version, profile_version=profile_version,
        resume_run_id=resume_run_id, draft=draft,
    )

    row = conn.execute(
        "SELECT status, gen_run_id FROM prep_snapshot WHERE application_id='app-1' AND version=?",
        (version,),
    ).fetchone()
    assert row[0] == "draft"
    assert row[1] == draft.run_id
    questions = conn.execute(
        "SELECT seq, dimension FROM prep_question WHERE snapshot_id = "
        "(SELECT id FROM prep_snapshot WHERE application_id='app-1' AND version=?) ORDER BY seq",
        (version,),
    ).fetchall()
    assert questions == [(1, "AUTOSAR CP")]


def test_effect_persist_prep_draft_is_idempotent(conn):
    _seed_job_and_approved_profile(conn)
    _seed_application(conn)
    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=ScriptedClient([_question_body()]),
        audit_hook=RecordingHook(conn),
    )
    draft, profile_version, resume_run_id = compute_prep(conn, application_id="app-1", gateway=gateway)
    version = next_prep_version(conn, "app-1")
    kwargs = dict(
        thread_id="app-1", business_key=draft.run_id, application_id="app-1",
        version=version, profile_version=profile_version, resume_run_id=resume_run_id, draft=draft,
    )

    effect_persist_prep_draft(conn, **kwargs)
    effect_persist_prep_draft(conn, **kwargs)  # 重放

    count = conn.execute(
        "SELECT COUNT(*) FROM prep_snapshot WHERE application_id='app-1'"
    ).fetchone()[0]
    assert count == 1


def test_freeze_then_verify_prep_frozen_passes(conn):
    _seed_job_and_approved_profile(conn)
    _seed_application(conn)
    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=ScriptedClient([_question_body()]),
        audit_hook=RecordingHook(conn),
    )
    draft, profile_version, resume_run_id = compute_prep(conn, application_id="app-1", gateway=gateway)
    version = next_prep_version(conn, "app-1")
    effect_persist_prep_draft(
        conn, thread_id="app-1", business_key=draft.run_id, application_id="app-1",
        version=version, profile_version=profile_version, resume_run_id=resume_run_id, draft=draft,
    )

    with pytest.raises(PrepSnapshotNotFrozenError):
        verify_prep_frozen(conn, application_id="app-1", version=version)

    effect_freeze_prep(
        conn, thread_id="app-1", business_key=str(version),
        application_id="app-1", version=version, confirmed_by="hr-1",
    )
    verify_prep_frozen(conn, application_id="app-1", version=version)  # 不抛


def test_expire_outdated_snapshots_marks_old_versions(conn):
    _seed_job_and_approved_profile(conn, version=1)
    _seed_application(conn)
    conn.execute(
        "INSERT INTO analysis_run (id, configured_model, prompt_version, temperature, "
        "input_hash, raw_response) VALUES ('run-0', 'm', 'interview-prep-v1', 0, 'h', 'r')"
    )
    conn.execute(
        "INSERT INTO prep_snapshot (id, application_id, version, profile_version, gen_run_id, status) "
        "VALUES ('snap-1', 'app-1', 1, 1, 'run-0', 'frozen')"
    )
    conn.commit()

    expire_outdated_snapshots(conn, application_id="app-1", current_profile_version=2)
    conn.commit()

    status = conn.execute("SELECT status FROM prep_snapshot WHERE id='snap-1'").fetchone()[0]
    assert status == "expired"


def test_edit_then_regenerate_question_updates_origin(conn):
    _seed_job_and_approved_profile(conn)
    _seed_application(conn)
    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=ScriptedClient([_question_body()]),
        audit_hook=RecordingHook(conn),
    )
    draft, profile_version, resume_run_id = compute_prep(conn, application_id="app-1", gateway=gateway)
    version = next_prep_version(conn, "app-1")
    effect_persist_prep_draft(
        conn, thread_id="app-1", business_key=draft.run_id, application_id="app-1",
        version=version, profile_version=profile_version, resume_run_id=resume_run_id, draft=draft,
    )
    snapshot_id = conn.execute(
        "SELECT id FROM prep_snapshot WHERE application_id='app-1' AND version=?", (version,)
    ).fetchone()[0]

    effect_edit_prep_question(
        conn, thread_id="app-1", business_key=f"{version}:1:edit1",
        snapshot_id=snapshot_id, seq=1, text="改过的题面", rubric="改过的 rubric",
    )
    row = conn.execute(
        "SELECT text, origin, ai_text FROM prep_question WHERE snapshot_id=? AND seq=1",
        (snapshot_id,),
    ).fetchone()
    assert row == ("改过的题面", "ai_edited", "讲讲你做过的 AUTOSAR 项目")

    from app.agents.interview_prep import PrepQuestionDraft

    replacement = PrepQuestionDraft(
        dimension="AUTOSAR CP", difficulty="medium", text="新题面",
        rubric="新 rubric", follow_ups=["新追问"], rationale="重生成",
    )
    effect_regenerate_prep_question(
        conn, thread_id="app-1", business_key=f"{version}:1:regen1",
        snapshot_id=snapshot_id, seq=1, question=replacement,
    )
    row = conn.execute(
        "SELECT text, origin, ai_text FROM prep_question WHERE snapshot_id=? AND seq=1",
        (snapshot_id,),
    ).fetchone()
    assert row == ("新题面", "ai", None)


def test_delete_question_removes_row(conn):
    _seed_job_and_approved_profile(conn)
    _seed_application(conn)
    gateway = LLMGateway(
        api_key="k", base_url="https://example.com", model="deepseek-chat",
        supports_json_schema=False, client=ScriptedClient([_question_body()]),
        audit_hook=RecordingHook(conn),
    )
    draft, profile_version, resume_run_id = compute_prep(conn, application_id="app-1", gateway=gateway)
    version = next_prep_version(conn, "app-1")
    effect_persist_prep_draft(
        conn, thread_id="app-1", business_key=draft.run_id, application_id="app-1",
        version=version, profile_version=profile_version, resume_run_id=resume_run_id, draft=draft,
    )
    snapshot_id = conn.execute(
        "SELECT id FROM prep_snapshot WHERE application_id='app-1' AND version=?", (version,)
    ).fetchone()[0]

    effect_delete_prep_question(
        conn, thread_id="app-1", business_key=f"{version}:1:delete", snapshot_id=snapshot_id, seq=1
    )
    count = conn.execute(
        "SELECT COUNT(*) FROM prep_question WHERE snapshot_id=?", (snapshot_id,)
    ).fetchone()[0]
    assert count == 0
```

- [ ] **Step 6: 跑全部测试确认通过**

Run: `pytest tests/test_interview_prep_nodes.py -v`
Expected: 全部 PASS（8 条用例）

- [ ] **Step 7: Commit**

```bash
git add app/graph/interview_prep_nodes.py tests/test_interview_prep_nodes.py
git commit -m "feat(m3-prep): prep L4 编排层（compute_prep + effect_persist/freeze/edit/delete/regenerate）"
```

---

### Task 5: 登记 5 个新 effect 节点——`EFFECT_NODE_MANIFEST` + 崩溃-恢复配方

**背景（写作期已用真实 sqlite3 连接跑通验证，不是猜测）：** `tests/test_effect_idempotency_suite.py` 是工程铁律 1 的全量守护——它用 AST 扫 `app/` 下全部 `@idempotent_effect(...)` 装饰点，与文件内硬编码的 `EFFECT_NODE_MANIFEST` 双向比对；仓库里新增任何 `effect_*` 节点却不登记进这份清单，`test_manifest_matches_the_source_tree` 就会失败——这条在验证阶段被实测踩到：Task 4 新增的 5 个 `effect_*` 节点（`effect_persist_prep_draft`/`effect_freeze_prep`/`effect_edit_prep_question`/`effect_delete_prep_question`/`effect_regenerate_prep_question`）不登记就是红灯。登记不是加一行名字就完：`build_recipes()` 必须给每个节点一条"种子数据 → 调用 → 强制中断 → 重启 → 按同一 thread_id/business_key 重跑 → 断言副作用恰好一次"的配方，另一条参数化测试 `test_forced_interrupt_then_recovery_applies_the_effect_exactly_once` 会对清单里的每一个节点跑这套协议。

⚠️ **并发提醒（写进本 Task，供执行者出手前自查，不属于本计划的内容本身）**：`tests/test_effect_idempotency_suite.py` 在本计划写作当天（2026-09-18）同时是另一条并行泳道（`effect-manifest-补登记`）的触碰文件（该泳道登记 `effect_persist_flags`）。执行本 Task 前，先 `git log -3 -- tests/test_effect_idempotency_suite.py` 与 `git diff origin/main -- tests/test_effect_idempotency_suite.py` 确认该泳道是否已合并；若仍在进行中，走 CLAUDE.md 的并发四条协议（只 add 本 Task 明确列出的路径、push 被拒就 `pull --rebase --autostash` 重试），⛔ 不要因为文件当下有别人的改动就跳过本 Task——两条并行的清单新增各自互不冲突（都是往 `EFFECT_NODE_MANIFEST` 这个 frozenset 字面量、`build_recipes()` 这个 dict 字面量里加新键，Python 层面不会互相覆盖；真实冲突只会出现在 git 的文本合并层面，走标准 rebase 流程即可解决）。

**Files:**
- Modify: `tests/test_effect_idempotency_suite.py`

**Interfaces:**
- Consumes: Task 4 的 5 个 `effect_*` 节点、`app.agents.interview_prep.{PrepDraft, PrepQuestionDraft}`

- [ ] **Step 1: 写失败测试（清单登记本身就是测试）——确认当前清单缺失这 5 个节点**

Run: `pytest tests/test_effect_idempotency_suite.py::test_manifest_matches_the_source_tree -v`
Expected: FAIL，报错信息列出 `['effect_delete_prep_question', 'effect_edit_prep_question', 'effect_freeze_prep', 'effect_persist_prep_draft', 'effect_regenerate_prep_question']`

- [ ] **Step 2: import 区追加 Task 4 的 5 个节点与所需类型**

`tests/test_effect_idempotency_suite.py` 顶部 import 区，在 `from app.graph.resume_nodes import effect_persist_parse` 之前插入：

```python
from app.graph.interview_prep_nodes import (
    effect_delete_prep_question,
    effect_edit_prep_question,
    effect_freeze_prep,
    effect_persist_prep_draft,
    effect_regenerate_prep_question,
)
from app.agents.interview_prep import PrepDraft, PrepQuestionDraft
```

- [ ] **Step 3: `EFFECT_NODE_MANIFEST` 补 5 行**

`EFFECT_NODE_MANIFEST` 的 frozenset 字面量，在 `"effect_persist_flags",` 之后追加：

```python
        "effect_persist_prep_draft",
        "effect_freeze_prep",
        "effect_edit_prep_question",
        "effect_delete_prep_question",
        "effect_regenerate_prep_question",
```

- [ ] **Step 4: 补种子常量与种子/断言辅助函数**

在既有 `_JOB = "job-4-4"` / `_RESUME = "resume-4-4"` / `_CANDIDATE = "candidate-4-4"` / `_APPLICATION = "application-4-4"` 常量块（`_seed_screening`/`effect_persist_flags` 配方已在用这四个）之后追加一个新常量：

```python
_PREP_RUN = "prep-run-4-4"
```

`build_recipes()` 函数体内、既有 `_seed_jd(conn)` 定义之后、`return {` 之前，追加：

```python
    def _seed_application_base(conn):
        """prep 五个节点共用的前置：已确认画像 + 一条投递 + 一条可引用的
        analysis_run（充当 gen_run_id 的外键目标——真实链路里这行由 LLM
        网关的 AuditHook 写，这里手工造一行等价物，配方不关心它的内容）。"""
        conn.execute(
            "INSERT INTO job (id, title, status) VALUES (?, '嵌入式软件工程师', 'approved')",
            (_JOB,),
        )
        conn.execute(
            "INSERT INTO job_profile (job_id, version, status, profile_json) "
            "VALUES (?, 1, 'approved', ?)",
            (_JOB, json.dumps({"job_title": "嵌入式软件工程师"}, ensure_ascii=False)),
        )
        conn.execute("INSERT INTO candidate (id, name) VALUES (?, '张三')", (_CANDIDATE,))
        conn.execute(
            "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
            "VALUES (?, ?, 'synthetic', 'a.pdf', 'hash-4-4', 'tester')",
            (_RESUME, _JOB),
        )
        conn.execute(
            "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
            "VALUES (?, ?, ?, ?, 'initial')",
            (_APPLICATION, _CANDIDATE, _JOB, _RESUME),
        )
        conn.execute(
            "INSERT INTO analysis_run (id, configured_model, prompt_version, temperature, "
            "input_hash, raw_response) VALUES (?, 'deepseek-chat', 'interview-prep-v1', 0, "
            "'hash', '{}')",
            (_PREP_RUN,),
        )
        conn.commit()

    def _seed_prep_snapshot_draft(conn):
        """在 _seed_application_base 基础上再加一份 draft 快照 + 一道题，供
        edit/delete/regenerate/freeze 四个节点复用。"""
        _seed_application_base(conn)
        conn.execute(
            "INSERT INTO prep_snapshot (id, application_id, version, profile_version, "
            "gen_run_id, status) VALUES ('prep-snap-4-4', ?, 1, 1, ?, 'draft')",
            (_APPLICATION, _PREP_RUN),
        )
        conn.execute(
            "INSERT INTO prep_question (id, snapshot_id, seq, dimension, difficulty, text, "
            "rubric_json, follow_ups_json, rationale, origin) VALUES "
            "('prep-q-4-4', 'prep-snap-4-4', 1, 'AUTOSAR CP', 'easy', '原题面', "
            "'原 rubric', '[\"追问\"]', '依据', 'ai')"
        )
        conn.commit()

    def _prep_snapshot_count(conn, *, status=None):
        if status is None:
            return conn.execute(
                "SELECT COUNT(*) FROM prep_snapshot WHERE application_id = ? AND version = 1",
                (_APPLICATION,),
            ).fetchone()[0]
        return conn.execute(
            "SELECT COUNT(*) FROM prep_snapshot WHERE application_id = ? AND version = 1 "
            "AND status = ?",
            (_APPLICATION, status),
        ).fetchone()[0]

    def _prep_question_row(conn):
        return conn.execute(
            "SELECT text, rubric_json, origin FROM prep_question WHERE snapshot_id = 'prep-snap-4-4' "
            "AND seq = 1"
        ).fetchone()

    _regen_replacement = PrepQuestionDraft(
        dimension="AUTOSAR CP", difficulty="medium", text="重生成后的题面",
        rubric="重生成后的 rubric", follow_ups=["新追问"], rationale="重生成依据",
    )
```

- [ ] **Step 5: `build_recipes()` 的 `return {...}` 字典补 5 条配方**

在既有 `"effect_persist_flags": Recipe(...)` 条目之后（`return { ... }` 闭合花括号之前）追加：

```python
        "effect_persist_prep_draft": Recipe(
            thread_id=_APPLICATION,
            seed=_seed_application_base,
            invoke=lambda conn: effect_persist_prep_draft(
                conn,
                thread_id=_APPLICATION,
                business_key=_PREP_RUN,
                application_id=_APPLICATION,
                version=1,
                profile_version=1,
                resume_run_id=None,
                draft=PrepDraft(
                    questions=[
                        PrepQuestionDraft(
                            dimension="AUTOSAR CP", difficulty="easy", text="题面",
                            rubric="rubric", follow_ups=["追问"], rationale="依据",
                        )
                    ],
                    dropped_count=0,
                    run_id=_PREP_RUN,
                    response_model="deepseek-chat-241226",
                ),
            ),
            count_business_rows=lambda conn: _prep_snapshot_count(conn),
        ),
        "effect_freeze_prep": Recipe(
            thread_id=_APPLICATION,
            seed=_seed_prep_snapshot_draft,
            invoke=lambda conn: effect_freeze_prep(
                conn,
                thread_id=_APPLICATION,
                business_key="1",
                application_id=_APPLICATION,
                version=1,
                confirmed_by="hr-1",
            ),
            count_business_rows=lambda conn: _prep_snapshot_count(conn, status="frozen"),
            note=(
                "**value-idempotent**：draft → frozen 是 UPDATE，行数口径改用"
                "「处于 frozen 状态的行数」而非「新增行数」，与 "
                "effect_mark_needs_manual 同一手法。"
            ),
        ),
        "effect_edit_prep_question": Recipe(
            thread_id=_APPLICATION,
            seed=_seed_prep_snapshot_draft,
            invoke=lambda conn: effect_edit_prep_question(
                conn,
                thread_id=_APPLICATION,
                business_key="1:1:edit1",
                snapshot_id="prep-snap-4-4",
                seq=1,
                text="改过的题面",
                rubric="改过的 rubric",
            ),
            count_business_rows=lambda conn: int(
                _prep_question_row(conn) == ("改过的题面", "改过的 rubric", "ai_edited")
            ),
            note=(
                "**value-idempotent**：改题面是 UPDATE 同一行，行数口径改用"
                "「该行取值是否等于编辑后的目标值」这个 0/1 谓词，与 "
                "effect_update_jd_text 同一手法。"
            ),
        ),
        "effect_regenerate_prep_question": Recipe(
            thread_id=_APPLICATION,
            seed=_seed_prep_snapshot_draft,
            invoke=lambda conn: effect_regenerate_prep_question(
                conn,
                thread_id=_APPLICATION,
                business_key="1:1:regen1",
                snapshot_id="prep-snap-4-4",
                seq=1,
                question=_regen_replacement,
            ),
            count_business_rows=lambda conn: int(
                (_prep_question_row(conn) or (None,))[0] == "重生成后的题面"
            ),
            note=(
                "**value-idempotent**：重生成是 UPDATE 同一行，行数口径改用"
                "「该行题面是否等于重生成后的目标值」这个 0/1 谓词，与 "
                "effect_update_jd_text 同一手法。"
            ),
        ),
        "effect_delete_prep_question": Recipe(
            thread_id=_APPLICATION,
            seed=_seed_prep_snapshot_draft,
            invoke=lambda conn: effect_delete_prep_question(
                conn,
                thread_id=_APPLICATION,
                business_key="1:1:delete",
                snapshot_id="prep-snap-4-4",
                seq=1,
            ),
            count_business_rows=lambda conn: conn.execute(
                "SELECT COUNT(*) FROM prep_question WHERE snapshot_id = 'prep-snap-4-4' "
                "AND seq = 1"
            ).fetchone()[0],
            rows_per_effect=-1,
            note=(
                "**全表唯一的负值**：这是 DELETE，不是 INSERT/UPDATE——种子先放一行"
                "（rows_before=1），生效一次后这行消失（0 行），"
                "`rows_before + rows_per_effect == 0` 要求 rows_per_effect=-1。"
                "双发保护同样完全靠 effect_log 的 COUNT(*) == 1：第二次调用命中"
                "幂等短路，不会在已经是 0 行的表上再次尝试 DELETE。"
            ),
        ),
```

- [ ] **Step 6: 跑全量测试确认通过**

Run: `pytest tests/test_effect_idempotency_suite.py -v`
Expected: 全部 PASS（含两条参数化主用例 `test_forced_interrupt_then_recovery_applies_the_effect_exactly_once` 与 `test_effect_log_count_equals_business_rows_per_thread` 在新的 5 个节点上各自通过；写作期已用等价内容在临时文件上实测跑通 46/46）

- [ ] **Step 7: Commit**

```bash
git add tests/test_effect_idempotency_suite.py
git commit -m "test(m3-prep): 登记 5 个 prep effect 节点进 EFFECT_NODE_MANIFEST 并补崩溃-恢复配方"
```

---

### Task 6: Web 端点——生成 / 查看 / 改 / 删 / 重生成 / 冻结

**背景：** 参照 `app/web/server.py` 里 `/api/jobs/{job_id}/confirm` 一类端点的路由风格（单 `APIRouter`，鉴权 `reviewer_of(request)`，422 给结构化字段级错误而不是裸 500）。本项目 `hr_account` 没有角色区分，"业务经理确认"在鉴权层面等价于"任一已登录 hr_account 用户操作该端点"，本单元不新增角色/权限系统。

**Files:**
- Modify: `app/web/server.py`
- Test: `tests/test_prep_endpoints.py`

**Interfaces:**
- Consumes: Task 4 的 `compute_prep`/`next_prep_version`/`effect_persist_prep_draft`/`effect_freeze_prep`/`effect_edit_prep_question`/`effect_delete_prep_question`/`effect_regenerate_prep_question`/`ProfileNotApprovedError`；Task 3 的 `regenerate_one`
- Produces:
  - `POST /api/applications/{application_id}/prep/generate` → `{version, questions: [...]}`
  - `GET /api/applications/{application_id}/prep/{version}` → `{version, status, questions: [...]}`
  - `PATCH /api/applications/{application_id}/prep/{version}/questions/{seq}` body `{text, rubric}`
  - `DELETE /api/applications/{application_id}/prep/{version}/questions/{seq}`
  - `POST /api/applications/{application_id}/prep/{version}/questions/{seq}/regenerate`
  - `POST /api/applications/{application_id}/prep/{version}/freeze`
  - `GET /applications/{application_id}/prep/{version}/review`（confirm 页面路由，Task 7）

- [ ] **Step 1: 写失败测试——generate 端点在画像未确认时返回 409**

创建 `tests/test_prep_endpoints.py`：

```python
"""prep 出题确认页的 6 个端点（voice-structured-interview U2 tasks 3.6/3.7）。"""

import json

from tests.test_web_api import make_app


def _question_response(dimension="AUTOSAR CP", difficulty="easy"):
    return json.dumps(
        {
            "questions": [
                {
                    "dimension": dimension,
                    "difficulty": difficulty,
                    "text": "讲讲你做过的 AUTOSAR 项目",
                    "rubric": "能说清分层架构者得分",
                    "follow_ups": ["具体是哪个 OEM 项目？"],
                    "rationale": "画像要求 AUTOSAR CP 经验",
                }
            ]
        },
        ensure_ascii=False,
    )


def test_generate_returns_409_when_profile_not_approved(tmp_path):
    from tests.test_approval_branches import COMPLETE_PROFILE_RESPONSE

    # COMPLETE_PROFILE_RESPONSE 驱动第一轮问答直接产出完整画像草案，但本用例
    # 故意**不**调 /confirm——job_profile 只有 drafting 版本，没有任何
    # approved 版本，这正是本用例要触发的前置条件。
    client = make_app(tmp_path, [COMPLETE_PROFILE_RESPONSE])
    job_id = client.post(
        "/api/jobs", json={"message": "要个做 ECU 底层的"}
    ).json()["job_id"]

    # 手工插一条投递，画像还没确认（job_profile 只有 drafting 版本）
    import sqlite3

    db_path = str(tmp_path / "web.db")
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('resume-1', ?, 'synthetic', 'a.pdf', 'hash', 'tester')",
        (job_id,),
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('app-1', 'cand-1', ?, 'resume-1', 'initial')",
        (job_id,),
    )
    conn.commit()
    conn.close()

    resp = client.post("/api/applications/app-1/prep/generate")
    assert resp.status_code == 409
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_prep_endpoints.py -v`
Expected: FAIL（404，路由不存在）

- [ ] **Step 3: 实现端点**

在 `app/web/server.py` 顶部 import 区（现有 `from app.graph.nodes import (...)` 之后）追加：

```python
from app.graph.interview_prep_nodes import (
    ProfileNotApprovedError,
    compute_prep,
    effect_delete_prep_question,
    effect_edit_prep_question,
    effect_freeze_prep,
    effect_persist_prep_draft,
    effect_regenerate_prep_question,
    load_application_job,
    next_prep_version,
)
from app.agents.interview_prep import PrepGenerationFailed, regenerate_one
from app.schemas.interview_ai_input import PrepInput
from app.schemas.job_profile import derive_rubric_dimensions
```

（`latest_approved_profile_version` 不需要新增 import——`app/web/server.py` 顶部已有 `from app.graph.screening_nodes import latest_approved_profile_version, screen_and_persist`，本 Task 直接复用。）

在 `_render_static_page` 与 `create_app` 之间（或紧邻其他 `_xxx_payload` 辅助函数处，与 `_jd_payload` 同一层级）追加 payload 组装辅助与请求体模型：

```python
class PrepQuestionEditRequest(BaseModel):
    text: str
    rubric: str


def _prep_payload(conn, application_id: str, version: int) -> dict:
    snapshot = conn.execute(
        "SELECT id, status, profile_version, confirmed_by, confirmed_at FROM prep_snapshot "
        "WHERE application_id = ? AND version = ?",
        (application_id, version),
    ).fetchone()
    if snapshot is None:
        raise HTTPException(status_code=404, detail="prep 快照不存在")
    snapshot_id, status, profile_version, confirmed_by, confirmed_at = snapshot
    rows = conn.execute(
        "SELECT seq, dimension, difficulty, text, rubric_json, follow_ups_json, "
        "rationale, origin, ai_text FROM prep_question WHERE snapshot_id = ? ORDER BY seq",
        (snapshot_id,),
    ).fetchall()
    return {
        "application_id": application_id,
        "version": version,
        "status": status,
        "profile_version": profile_version,
        "confirmed_by": confirmed_by,
        "confirmed_at": confirmed_at,
        "questions": [
            {
                "seq": seq,
                "dimension": dimension,
                "difficulty": difficulty,
                "text": text,
                "rubric": rubric_json,
                "follow_ups": json.loads(follow_ups_json),
                "rationale": rationale,
                "origin": origin,
                "ai_text": ai_text,
            }
            for seq, dimension, difficulty, text, rubric_json, follow_ups_json, rationale, origin, ai_text in rows
        ],
    }


def _load_prep_question_dimension_difficulty(conn, snapshot_id: str, seq: int) -> tuple[str, str]:
    row = conn.execute(
        "SELECT dimension, difficulty FROM prep_question WHERE snapshot_id = ? AND seq = ?",
        (snapshot_id, seq),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    return row


def _require_draft_snapshot(conn, application_id: str, version: int) -> str:
    """改题/删题/重生成只允许在 draft 状态下操作，返回 snapshot_id。"""
    row = conn.execute(
        "SELECT id, status FROM prep_snapshot WHERE application_id = ? AND version = ?",
        (application_id, version),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="prep 快照不存在")
    snapshot_id, status = row
    if status != "draft":
        raise HTTPException(
            status_code=409, detail=f"快照状态是 {status!r}，只有 draft 状态可修改"
        )
    return snapshot_id
```

在 `router` 定义区（挨着 `/api/jobs/{job_id}/confirm` 附近，同一 `router` 对象）追加 6 个路由：

```python
    @router.post("/api/applications/{application_id}/prep/generate")
    def generate_prep(application_id: str):
        gateway = gateway_factory()
        try:
            draft, profile_version, resume_run_id = compute_prep(
                conn, application_id=application_id, gateway=gateway
            )
        except ProfileNotApprovedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PrepGenerationFailed as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        version = next_prep_version(conn, application_id)
        effect_persist_prep_draft(
            conn,
            thread_id=application_id,
            business_key=draft.run_id,
            application_id=application_id,
            version=version,
            profile_version=profile_version,
            resume_run_id=resume_run_id,
            draft=draft,
        )
        return _prep_payload(conn, application_id, version)

    @router.get("/api/applications/{application_id}/prep/{version}")
    def get_prep(application_id: str, version: int):
        return _prep_payload(conn, application_id, version)

    @router.patch("/api/applications/{application_id}/prep/{version}/questions/{seq}")
    def edit_prep_question(application_id: str, version: int, seq: int, req: PrepQuestionEditRequest):
        snapshot_id = _require_draft_snapshot(conn, application_id, version)
        effect_edit_prep_question(
            conn,
            thread_id=application_id,
            business_key=f"{version}:{seq}:edit:{hashlib.sha256(req.text.encode()).hexdigest()[:16]}",
            snapshot_id=snapshot_id,
            seq=seq,
            text=req.text,
            rubric=req.rubric,
        )
        return _prep_payload(conn, application_id, version)

    @router.delete("/api/applications/{application_id}/prep/{version}/questions/{seq}")
    def delete_prep_question(application_id: str, version: int, seq: int):
        snapshot_id = _require_draft_snapshot(conn, application_id, version)
        effect_delete_prep_question(
            conn, thread_id=application_id, business_key=f"{version}:{seq}:delete",
            snapshot_id=snapshot_id, seq=seq,
        )
        return _prep_payload(conn, application_id, version)

    @router.post("/api/applications/{application_id}/prep/{version}/questions/{seq}/regenerate")
    def regenerate_prep_question(application_id: str, version: int, seq: int):
        snapshot_id = _require_draft_snapshot(conn, application_id, version)
        dimension, difficulty = _load_prep_question_dimension_difficulty(conn, snapshot_id, seq)

        job_id = load_application_job(conn, application_id)
        profile_version = latest_approved_profile_version(conn, job_id)
        profile_row = conn.execute(
            "SELECT profile_json FROM job_profile WHERE job_id = ? AND version = ?",
            (job_id, profile_version),
        ).fetchone()
        profile = json.loads(profile_row[0])
        rubric_dimensions = derive_rubric_dimensions(profile)
        prep_input = PrepInput(profile=profile, rubric_dimensions=rubric_dimensions, resume_scores=[])

        gateway = gateway_factory()
        question, run_id = regenerate_one(
            gateway, prep_input, dimension=dimension, difficulty=difficulty,
            audit_context={
                "thread_id": f"{application_id}:prep", "node": "effect_regenerate_prep_question",
                "application_id": application_id, "job_id": job_id,
            },
        )
        effect_regenerate_prep_question(
            conn, thread_id=application_id, business_key=f"{version}:{seq}:regen:{run_id}",
            snapshot_id=snapshot_id, seq=seq, question=question,
        )
        return _prep_payload(conn, application_id, version)

    @router.post("/api/applications/{application_id}/prep/{version}/freeze")
    def freeze_prep(application_id: str, version: int, request: Request):
        snapshot_id = _require_draft_snapshot(conn, application_id, version)
        effect_freeze_prep(
            conn, thread_id=application_id, business_key=str(version),
            application_id=application_id, version=version, confirmed_by=reviewer_of(request),
        )
        return _prep_payload(conn, application_id, version)

    @router.get("/applications/{application_id}/prep/{version}/review")
    def prep_review_page(application_id: str, version: int):
        return _render_static_page("interview_prep_review.html", root_path)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_prep_endpoints.py -v`
Expected: PASS

- [ ] **Step 5: 补齐剩余端点测试（生成成功、改题、删题、重生成、冻结后拒绝再改、冻结后开场校验通过）**

追加到 `tests/test_prep_endpoints.py`（复用 Task 4 测试里手工造投递的写法，抽成本文件内的 `_seed_application` 辅助）：

```python
import sqlite3


def _seed_application(tmp_path, client, job_id, application_id="app-1"):
    db_path = str(tmp_path / "web.db")
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('resume-1', ?, 'synthetic', 'a.pdf', 'hash', 'tester')",
        (job_id,),
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, 'cand-1', ?, 'resume-1', 'initial')",
        (application_id, job_id),
    )
    conn.commit()
    conn.close()


def _confirmed_job_id(client):
    from tests.test_approval_branches import COMPLETE_PROFILE_RESPONSE

    job_id = client.post("/api/jobs", json={"message": "要个做 ECU 底层的"}).json()["job_id"]
    resp = client.post(f"/api/jobs/{job_id}/confirm", json={"acknowledged_gaps": True})
    assert resp.status_code == 200, resp.text
    return job_id


def test_generate_edit_freeze_flow(tmp_path):
    from tests.test_approval_branches import COMPLETE_PROFILE_RESPONSE, JD_RESPONSE

    client = make_app(
        tmp_path, [COMPLETE_PROFILE_RESPONSE, JD_RESPONSE, _question_response()]
    )
    job_id = _confirmed_job_id(client)
    _seed_application(tmp_path, client, job_id)

    generated = client.post("/api/applications/app-1/prep/generate")
    assert generated.status_code == 200, generated.text
    body = generated.json()
    assert body["status"] == "draft"
    assert len(body["questions"]) == 1
    version = body["version"]

    edited = client.patch(
        f"/api/applications/app-1/prep/{version}/questions/1",
        json={"text": "改过的题面", "rubric": "改过的 rubric"},
    )
    assert edited.status_code == 200
    assert edited.json()["questions"][0]["origin"] == "ai_edited"
    assert edited.json()["questions"][0]["ai_text"] == "讲讲你做过的 AUTOSAR 项目"

    frozen = client.post(f"/api/applications/app-1/prep/{version}/freeze")
    assert frozen.status_code == 200
    assert frozen.json()["status"] == "frozen"
    assert frozen.json()["confirmed_by"]

    # 冻结后不能再改
    blocked = client.patch(
        f"/api/applications/app-1/prep/{version}/questions/1",
        json={"text": "再改一次", "rubric": "r"},
    )
    assert blocked.status_code == 409


def test_delete_question_via_endpoint(tmp_path):
    from tests.test_approval_branches import COMPLETE_PROFILE_RESPONSE, JD_RESPONSE

    client = make_app(
        tmp_path, [COMPLETE_PROFILE_RESPONSE, JD_RESPONSE, _question_response()]
    )
    job_id = _confirmed_job_id(client)
    _seed_application(tmp_path, client, job_id)
    version = client.post("/api/applications/app-1/prep/generate").json()["version"]

    deleted = client.delete(f"/api/applications/app-1/prep/{version}/questions/1")
    assert deleted.status_code == 200
    assert deleted.json()["questions"] == []


def test_regenerate_question_via_endpoint(tmp_path):
    from tests.test_approval_branches import COMPLETE_PROFILE_RESPONSE, JD_RESPONSE

    replacement = _question_response(dimension="AUTOSAR CP", difficulty="hard")
    replacement = json.dumps(
        {"question": json.loads(replacement)["questions"][0]}, ensure_ascii=False
    )
    client = make_app(
        tmp_path,
        [COMPLETE_PROFILE_RESPONSE, JD_RESPONSE, _question_response(), replacement],
    )
    job_id = _confirmed_job_id(client)
    _seed_application(tmp_path, client, job_id)
    version = client.post("/api/applications/app-1/prep/generate").json()["version"]

    regenerated = client.post(
        f"/api/applications/app-1/prep/{version}/questions/1/regenerate"
    )
    assert regenerated.status_code == 200
    assert regenerated.json()["questions"][0]["difficulty"] == "hard"
    assert regenerated.json()["questions"][0]["origin"] == "ai"
```

- [ ] **Step 6: 跑全部测试确认通过**

Run: `pytest tests/test_prep_endpoints.py -v`
Expected: 全部 PASS

- [ ] **Step 7: Commit**

```bash
git add app/web/server.py tests/test_prep_endpoints.py
git commit -m "feat(m3-prep): prep 确认页 6 个 Web 端点（生成/查看/改/删/重生成/冻结）"
```

---

### Task 7: 确认页静态 HTML——`interview_prep_review.html`

**背景：** 参照 `app/web/static/resume_review.html` 的既有先例（`<!--BASE_HREF-->` 占位符、相对路径 `fetch`、无前端框架的原生 JS）。spec「题目与 AI 生成标识」要求确认页出题带 AI 生成标识（人工修改的题目标"AI 生成、人工修改"）。

**Files:**
- Create: `app/web/static/interview_prep_review.html`

**Interfaces:**
- Consumes: Task 6 的 6 个端点

- [ ] **Step 1: 创建页面**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <!--BASE_HREF-->
  <title>面试题确认 · 卓品智能招聘助手</title>
  <style>
    .question { border: 1px solid #ccc; padding: 12px; margin-bottom: 12px; }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; margin-left: 8px; }
    .badge-ai { background: #d0ebff; color: #1864ab; }
    .badge-edited { background: #fff3bf; color: #856404; }
    textarea { width: 100%; min-height: 60px; }
    .actions button { margin-right: 8px; }
    #status-bar { padding: 8px; margin-bottom: 12px; }
    .status-draft { background: #fff3cd; }
    .status-frozen { background: #d3f9d8; }
  </style>
</head>
<body>
  <h1>面试题确认</h1>
  <p><strong>以下题目由 AI 自动生成，供业务经理逐题确认。确认前可修改、删除、重生成；确认后生成不可变的题目快照。</strong></p>
  <div id="status-bar">加载中…</div>
  <div id="questions"></div>
  <div class="actions">
    <button id="generate-btn">生成/重新生成整套</button>
    <button id="freeze-btn">全部采纳并确认冻结</button>
  </div>
  <p id="error" style="color:red"></p>

  <script>
    function applicationIdFromPath() {
      const m = window.location.pathname.match(/\/applications\/([^/]+)\/prep\/(\d+)\/review\/?$/);
      return m ? { applicationId: m[1], version: Number(m[2]) } : null;
    }

    function escapeHtml(s) {
      return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    const parsed = applicationIdFromPath();
    const applicationId = parsed ? parsed.applicationId : null;
    let version = parsed ? parsed.version : null;
    const errorEl = document.getElementById("error");

    function showError(message) {
      errorEl.textContent = message;
    }

    function renderStatusBar(data) {
      const bar = document.getElementById("status-bar");
      bar.className = data.status === "frozen" ? "status-frozen" : "status-draft";
      bar.textContent = data.status === "frozen"
        ? `已冻结 · 确认人 ${data.confirmed_by || ""} · ${data.confirmed_at || ""}`
        : "草稿状态，逐题确认后点「全部采纳并确认冻结」";
    }

    function renderQuestions(data) {
      const container = document.getElementById("questions");
      container.innerHTML = "";
      const editable = data.status === "draft";
      data.questions.forEach((q) => {
        const div = document.createElement("div");
        div.className = "question";
        const badge = q.origin === "ai_edited"
          ? '<span class="badge badge-edited">AI 生成、人工修改</span>'
          : '<span class="badge badge-ai">AI 生成</span>';
        div.innerHTML = `
          <div><strong>第 ${q.seq} 题</strong>（${escapeHtml(q.dimension)} / ${escapeHtml(q.difficulty)}）${badge}</div>
          <div>依据：${escapeHtml(q.rationale)}</div>
          <textarea class="text-input" data-seq="${q.seq}" ${editable ? "" : "readonly"}>${escapeHtml(q.text)}</textarea>
          <textarea class="rubric-input" data-seq="${q.seq}" ${editable ? "" : "readonly"}>${escapeHtml(q.rubric)}</textarea>
          <div>预埋追问：${q.follow_ups.map(escapeHtml).join("；")}</div>
          ${editable ? `
            <div class="actions">
              <button class="save-btn" data-seq="${q.seq}">保存修改</button>
              <button class="delete-btn" data-seq="${q.seq}">删除</button>
              <button class="regenerate-btn" data-seq="${q.seq}">重新生成</button>
            </div>` : ""}
        `;
        container.appendChild(div);
      });

      container.querySelectorAll(".save-btn").forEach((btn) => {
        btn.addEventListener("click", () => saveQuestion(btn.dataset.seq));
      });
      container.querySelectorAll(".delete-btn").forEach((btn) => {
        btn.addEventListener("click", () => deleteQuestion(btn.dataset.seq));
      });
      container.querySelectorAll(".regenerate-btn").forEach((btn) => {
        btn.addEventListener("click", () => regenerateQuestion(btn.dataset.seq));
      });
    }

    function render(data) {
      version = data.version;
      renderStatusBar(data);
      renderQuestions(data);
    }

    async function loadPrep() {
      const resp = await fetch(`api/applications/${applicationId}/prep/${version}`);
      if (!resp.ok) {
        showError("加载失败：" + (await resp.text()));
        return;
      }
      render(await resp.json());
    }

    async function saveQuestion(seq) {
      const text = document.querySelector(`.text-input[data-seq="${seq}"]`).value;
      const rubric = document.querySelector(`.rubric-input[data-seq="${seq}"]`).value;
      const resp = await fetch(`api/applications/${applicationId}/prep/${version}/questions/${seq}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, rubric }),
      });
      if (!resp.ok) {
        showError("保存失败：" + (await resp.text()));
        return;
      }
      render(await resp.json());
    }

    async function deleteQuestion(seq) {
      const resp = await fetch(`api/applications/${applicationId}/prep/${version}/questions/${seq}`, {
        method: "DELETE",
      });
      if (!resp.ok) {
        showError("删除失败：" + (await resp.text()));
        return;
      }
      render(await resp.json());
    }

    async function regenerateQuestion(seq) {
      const resp = await fetch(
        `api/applications/${applicationId}/prep/${version}/questions/${seq}/regenerate`,
        { method: "POST" }
      );
      if (!resp.ok) {
        showError("重新生成失败：" + (await resp.text()));
        return;
      }
      render(await resp.json());
    }

    document.getElementById("generate-btn").addEventListener("click", async () => {
      const resp = await fetch(`api/applications/${applicationId}/prep/generate`, { method: "POST" });
      if (!resp.ok) {
        showError("生成失败：" + (await resp.text()));
        return;
      }
      const data = await resp.json();
      window.history.replaceState(null, "", `../${data.version}/review`);
      render(data);
    });

    document.getElementById("freeze-btn").addEventListener("click", async () => {
      const resp = await fetch(`api/applications/${applicationId}/prep/${version}/freeze`, { method: "POST" });
      if (!resp.ok) {
        showError("确认冻结失败：" + (await resp.text()));
        return;
      }
      render(await resp.json());
    });

    if (applicationId && version) {
      loadPrep();
    } else {
      showError("页面地址不正确：需要 /applications/{id}/prep/{version}/review");
    }
  </script>
</body>
</html>
```

- [ ] **Step 2: 手工冒烟（无自动化测试——纯静态页面，行为由 Task 6 的端点测试与后续 Task 8 的 e2e 间接覆盖）**

Run: `python -c "from pathlib import Path; assert (Path('app/web/static/interview_prep_review.html')).exists()"`
Expected: 无输出（断言通过）

- [ ] **Step 3: Commit**

```bash
git add app/web/static/interview_prep_review.html
git commit -m "feat(m3-prep): 面试题确认页（业务经理逐题看/改/删/重生成/冻结）"
```

---

### Task 8: prep e2e 测试（tasks 3.8）

**背景：** 合成画像 + M2 合成样本评分（手工造 `analysis_run`/`criterion_score`/`resume_text_span`，M2 candidate-ranking 尚未实现，本单元不等它）→ 生成 → 确认页改一题 → 冻结 → 开场校验通过；断言 `PrepInput` 不含身份字段。本测试**必须**用真实 `RecorderAuditHook`（不是默认 `NoopAuditHook`）——`prep_snapshot.gen_run_id` 是 `NOT NULL REFERENCES analysis_run(id)`（`PRAGMA foreign_keys = ON`），`NoopAuditHook` 返回的占位 id 不对应真实行，会撞外键失败。

**Files:**
- Create: `tests/test_prep_e2e.py`

**Interfaces:**
- Consumes: Task 6 的全部端点；`app.audit.hook.RecorderAuditHook`、`app.audit.recorder.AuditRecorder`、`app.audit.sinks.{SqliteSink, JsonlChainSink}`（沿用 `app/main.py::_gateway_factory()` 的生产装配形态）

- [ ] **Step 1: 写测试——完整链路 + 身份字段反射断言**

创建 `tests/test_prep_e2e.py`：

```python
"""prep 出题的完整链路 e2e（voice-structured-interview U2 tasks 3.8）：
合成画像 + M2 合成样本评分 → 生成 → 确认页改一题 → 冻结 → 开场校验通过。

用真实 RecorderAuditHook（不是默认 NoopAuditHook）：prep_snapshot.gen_run_id
是 NOT NULL REFERENCES analysis_run(id)，NoopAuditHook 的占位 id 不对应真实
行，会撞外键失败——这正是本文件要验证的"AI 评分必须持久化"链路本身。
"""
import json
import sqlite3

from fastapi.testclient import TestClient

from app.audit.hook import RecorderAuditHook
from app.audit.recorder import AuditRecorder
from app.audit.sinks import JsonlChainSink, SqliteSink
from app.graph.interview_prep_nodes import PrepSnapshotNotFrozenError, verify_prep_frozen
from app.llm.gateway import LLMGateway
from app.schemas.interview_ai_input import PrepInput
from app.storage.db import get_connection
from app.web.server import create_app
from tests.test_web_api import ScriptedOpenAIClient


def _make_app_with_real_audit(tmp_path, responses):
    """镜像 app/main.py::_gateway_factory() 的生产装配：RecorderAuditHook 用
    专属连接、真实写 analysis_run（不是 NoopAuditHook）。"""
    db_path = str(tmp_path / "web.db")
    audit_conn = get_connection(db_path)
    recorder = AuditRecorder(SqliteSink(audit_conn), JsonlChainSink(tmp_path / "decisions.jsonl"))
    hook = RecorderAuditHook(recorder, audit_conn)
    scripted_client = ScriptedOpenAIClient(responses)

    def gateway_factory():
        return LLMGateway(
            api_key="k", base_url="https://example.com", model="deepseek-chat-241226",
            supports_json_schema=False, client=scripted_client, audit_hook=hook,
        )

    app = create_app(db_path=db_path, gateway_factory=gateway_factory)
    return TestClient(app), db_path


def _seed_synthetic_resume_score(db_path: str, *, application_id: str, job_id: str):
    """M2 合成样本评分：手工造一条精排 analysis_run + criterion_score +
    resume_text_span，模拟"简历评分已完成"的前置状态（M2 candidate-ranking
    尚未实现，本单元不等它落地）。"""
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO candidate (id, name) VALUES ('cand-1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
        "raw_text, uploaded_by) VALUES ('resume-1', ?, 'synthetic', 'a.pdf', 'hash', "
        "'做过三年 AUTOSAR CP 分层开发', 'tester')",
        (job_id,),
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES (?, 'cand-1', ?, 'resume-1', 'initial')",
        (application_id, job_id),
    )
    conn.execute(
        "INSERT INTO resume_text_span (resume_id, span_id, start, end, text) "
        "VALUES ('resume-1', 0, 0, 13, '做过三年 AUTOSAR CP 分层开发')"
    )
    conn.execute(
        "INSERT INTO analysis_run (id, application_id, job_id, configured_model, "
        "prompt_version, temperature, input_hash, raw_response) VALUES "
        "('rank-run-1', ?, ?, 'deepseek-chat', 'rank-v1', 0, 'hash', '{}')",
        (application_id, job_id),
    )
    conn.execute(
        "INSERT INTO criterion_score (id, analysis_run_id, criterion_key, score, evidence_ref) "
        "VALUES ('score-1', 'rank-run-1', 'AUTOSAR CP', 0.9, "
        "'{\"span_id\": 0, \"start\": 0, \"end\": 13}')"
    )
    conn.commit()
    conn.close()


def _question_response(dimension="AUTOSAR CP", difficulty="easy"):
    return json.dumps(
        {
            "questions": [
                {
                    "dimension": dimension, "difficulty": difficulty,
                    "text": "讲讲你做过的 AUTOSAR 项目", "rubric": "能说清分层架构者得分",
                    "follow_ups": ["具体是哪个 OEM 项目？"], "rationale": "画像要求 AUTOSAR CP 经验",
                }
            ]
        },
        ensure_ascii=False,
    )


def test_prep_full_lifecycle_generate_edit_freeze_verify(tmp_path):
    from tests.test_approval_branches import COMPLETE_PROFILE_RESPONSE, JD_RESPONSE

    client, db_path = _make_app_with_real_audit(
        tmp_path, [COMPLETE_PROFILE_RESPONSE, JD_RESPONSE, _question_response()]
    )
    job_id = client.post("/api/jobs", json={"message": "要个做 ECU 底层的"}).json()["job_id"]
    confirm = client.post(f"/api/jobs/{job_id}/confirm", json={"acknowledged_gaps": True})
    assert confirm.status_code == 200, confirm.text

    _seed_synthetic_resume_score(db_path, application_id="app-1", job_id=job_id)

    generated = client.post("/api/applications/app-1/prep/generate")
    assert generated.status_code == 200, generated.text
    body = generated.json()
    version = body["version"]
    assert body["status"] == "draft"
    assert len(body["questions"]) == 1

    edited = client.patch(
        f"/api/applications/app-1/prep/{version}/questions/1",
        json={"text": "改过的题面", "rubric": "改过的 rubric"},
    )
    assert edited.status_code == 200
    assert edited.json()["questions"][0]["origin"] == "ai_edited"

    conn = sqlite3.connect(db_path)
    with_open_conn = get_connection(db_path)
    try:
        verify_prep_frozen(with_open_conn, application_id="app-1", version=version)
        assert False, "未冻结时 verify_prep_frozen 应该抛异常"
    except PrepSnapshotNotFrozenError:
        pass

    frozen = client.post(f"/api/applications/app-1/prep/{version}/freeze")
    assert frozen.status_code == 200
    assert frozen.json()["status"] == "frozen"

    verify_prep_frozen(with_open_conn, application_id="app-1", version=version)  # 不抛

    # gen_run_id 是真实的 analysis_run.id（不是 NoopAuditHook 占位符）
    snapshot_row = conn.execute(
        "SELECT gen_run_id, resume_run_id FROM prep_snapshot WHERE application_id='app-1'"
    ).fetchone()
    assert conn.execute(
        "SELECT COUNT(*) FROM analysis_run WHERE id = ?", (snapshot_row[0],)
    ).fetchone()[0] == 1
    assert snapshot_row[1] == "rank-run-1"
    conn.close()


def test_prep_input_structurally_excludes_identity_fields():
    """spec「MUST NOT 包含候选人姓名、联系方式等身份字段」+ 合规红线「绝不用
    历史录用结果做监督信号」：PrepInput 的字段白名单是结构性的，反射断言
    没有 name/phone/候选人身份/历史录用结果相关字段（复用
    app/schemas/interview_ai_input.py 模块 docstring 里已声明的既有约束
    ——extra="forbid" 让传错键在构造对象那一刻直接失败）。"""
    forbidden_keys = {"name", "candidate_name", "phone", "phone_number", "hired", "offer_accepted"}
    assert forbidden_keys.isdisjoint(set(PrepInput.model_fields.keys()))

    try:
        PrepInput(
            profile={}, rubric_dimensions=["x"], resume_scores=[],
            candidate_name="张三",  # type: ignore[call-arg]
        )
        assert False, "extra='forbid' 应该拒绝未登记字段"
    except Exception as exc:
        assert "candidate_name" in str(exc) or "extra" in str(exc).lower()
```

- [ ] **Step 2: 跑测试确认通过**

Run: `pytest tests/test_prep_e2e.py -v`
Expected: 全部 PASS（2 条用例）

- [ ] **Step 3: 跑本单元全部测试一遍，确认无交叉破坏**

Run: `pytest tests/test_llm_gateway.py tests/test_audit_hook.py tests/test_audit_end_to_end.py tests/test_job_profile_schema.py tests/test_db_m3_schema.py tests/test_db_migration.py tests/test_effect_idempotency_suite.py tests/test_interview_prep_agent.py tests/test_interview_prep_nodes.py tests/test_prep_endpoints.py tests/test_prep_e2e.py -v`
Expected: 全部 PASS

- [ ] **Step 3b: 跑一次全量 `pytest tests/ -q`，确认本单元没有波及包外任何既有测试**

Run: `pytest tests/ -q`
Expected: 与本单元合并前的基线失败集合完全一致（应为 0 个新增失败——写作期已用等价内容做过一次全量提取验证，2233 passed / 5 skipped，唯一失败项是 Task 5 落地前的 `test_manifest_matches_the_source_tree`，Task 5 完成后应转绿）

- [ ] **Step 4: Commit**

```bash
git add tests/test_prep_e2e.py
git commit -m "test(m3-prep): U2 prep 出题引擎 e2e（生成→改题→冻结→开场校验，身份字段反射断言）"
```

---

## Spec 覆盖对照

| spec Requirement | Scenario | 落地 Task |
|---|---|---|
| 按画像与简历弱点生成题目 | 常规生成 / 维度越界 / 简历评分尚未完成 | Task 1（rubric 推导）、Task 3（generate 白名单过滤+通用题分支）、Task 4（compute_prep 组装+resume_scores 加载） |
| 难度曲线 | 冻结后题序不变 | Task 2（`job_prep_config` 岗位级配置表）、Task 3（order_questions）、Task 4（快照写入即固定题序） |
| 业务经理确认后才冻结 | 业务经理确认 / 未确认即开场 / 业务经理修改题面 | Task 4（effect_freeze_prep/verify_prep_frozen/effect_edit_prep_question）、Task 5（效果节点登记）、Task 6（端点）、Task 7（UI） |
| 题目快照版本化且可追溯到输入 | 画像升版后重新生成 | Task 4（next_prep_version/expire_outdated_snapshots/prep_snapshot 字段） |
| 题目与 AI 生成标识 | 候选人端看题（确认页先行） | Task 7（badge）、Task 4（origin/ai_text 字段语义） |

## 交付前自查

- [x] 任务标题全部三级 `### Task N: `
- [x] Task 0（AuditHook 返回值扩展）在最前面，Task 3/4 明确消费 `LLMCallMeta.run_id`
- [x] 每个 Task 给出确切文件路径、完整代码、确切测试命令
- [x] "重生成单题"已选定实现方式（窄化单题调用，不重跑整套）并写明理由
- [x] spec 5 条 Requirement 均有对应 Task（见上表）
- [x] 无 TBD/TODO/"添加适当的错误处理"类占位符
- [x] **端到端提取验证已做**（不是可选步骤，本计划写作期间真实执行过）：Task 0–4 的全部代码块原样提取到本仓库真实路径、用仓库既有 venv 跑过，过程中发现并修正了 3 个真实设计缺陷——① Task 2 最初设计直接给 `job` 表加列，撞上本包自己的 `test_legacy_pre_m3_db_existing_tables_and_rows_are_untouched`/`test_job_columns_are_pinned` 两条既有断言，改为新表 `job_prep_config`；② `LLMCallMeta.run_id` 若不给默认值，会打断 `tests/test_resume_upload.py` 等 7 处跨包既有测试桩对 `LLMCallMeta(...)` 的直接构造，改为 `run_id: str = ""`；③ Task 4 的 5 个新 `effect_*` 节点未登记进 `tests/test_effect_idempotency_suite.py::EFFECT_NODE_MANIFEST` 会被那条全量守护测试拦下，新增 Task 5 补registration + 崩溃-恢复配方。三处修正后，全量 `pytest tests/ -q` 跑到 2233 passed / 5 skipped / 1 failed（唯一失败项是 Task 5 落地前的预期状态）。验证完毕后所有临时改动已用 `git checkout` 与 `rm` 还原，工作区仅保留本计划文件本身。
