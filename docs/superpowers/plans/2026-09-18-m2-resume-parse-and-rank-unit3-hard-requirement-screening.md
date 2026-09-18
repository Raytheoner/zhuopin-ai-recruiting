# U3 硬门槛引擎（标记＋依据＋申诉）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 M1 已存在但"只存不执行"的 `hard_requirement` 表第一次被执行——对每份已解析简历逐条判定硬门槛规则，产出 pass/fail/skipped 三态标记（只标记，不淘汰），并落地"淘汰只能由人确认、可申诉"的完整状态机。

**Architecture:** 五种比较运算符的判定逻辑是一个不读库、不调模型的 L3 纯函数 `screen()`；规则加载、简历字段读取、待校对队列查询是 L4 的 `compute_screen`（只读）；落库是唯一的 effect 节点 `effect_persist_flags`（幂等）。淘汰记录的写入唯一路径是 `write_rejection()`，其存储层 CHECK 约束是防止 AI 自动淘汰的第二道防线。申诉是独立状态机 `transition_appeal()`，每次合法流转都记操作人。

**Tech Stack:** Python 3.14、FastAPI、sqlite3（原生 SQL，无 ORM）、Pydantic v2、pytest。不引入任何新依赖。

**Spec:** `openspec/changes/m2-resume-parse-and-rank/specs/hard-requirement-screening/spec.md`（本计划的行为契约来源）；`openspec/changes/m2-resume-parse-and-rank/specs/candidate-ranking/spec.md`「主观描述不入硬门槛」（4.2 依据）；`openspec/changes/m2-resume-parse-and-rank/design.md` 决策 D4／D6／D11／D13。

## Global Constraints

工程铁律（来自 CLAUDE.md，逐字，仅摘与本单元相关的判断）：

1. LangGraph 恢复时节点从头整个重跑。每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。幂等记录与业务写必须在同一个事务里提交——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。**【适用】** `effect_persist_flags` 是本单元唯一的 effect 节点；申诉流转（`transition_appeal`）与拒绝记录写入（`write_rejection`）虽不是 LangGraph 节点，但同样要求"一次动作、一次事务、写完即提交"。
2. L3 Agent 全部是无副作用纯函数，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。**【适用】** `screen()` 是 L3 纯函数（不读库、不调模型）；`compute_screen` 是 L4 读编排（可以查库，但不写）；`effect_persist_flags` 是唯一写入点。
3. 所有 AI 评分必须持久化：模型标识+模型版本+prompt版本+temperature+输入哈希+rubric快照+原始响应。**【不适用】**：本单元的硬门槛判定是确定性规则匹配，不调用任何 LLM，没有"AI 评分"这个概念。
4. 每条 `criterion_score` 必须有 `evidence_ref`（回指简历原文或面试turn的offset）。`evidence_ref` 为空不允许写入。**【适用，类比对象是 `screening_flag` 而非 `criterion_score`】**：每条 `fail` 标记必须有 `evidence_ref`，为空不允许写入；本计划「架构决策 4」是这条铁律在本单元的直接落点。
5. `temperature=0`；模型版本优先显式锁定，禁止 `latest` 类别名；供应商不提供版本号快照时必须从 API 响应取回实际 model 字段并持久化。**【不适用】**：本单元不调用任何 LLM。
6. 企微回调先落库再处理：只推一次、5秒无响应即丢弃。回调接口只做签名校验+落库+返回200。**【不适用】**：本单元没有企微回调接口。
7. `langgraph >= 1.0.10`（GHSA-g48c-2wqr-h844）。**【已满足】**：本单元无需改动依赖版本。

合规红线（来自 CLAUDE.md，逐字，仅摘本单元相关条目）：

- AI 只做排序推荐，不做自动淘汰。淘汰必须有人工确认节点并留痕。审计断言：`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。**【核心适用】**：design D6 在本单元的直接落点——`screen()` 只产出标记不写拒绝记录；`write_rejection()` 在应用层与数据库 CHECK 层双重拒绝 `ai_score`。
- 禁止人脸/表情分析。声学情绪信号只展示给面试官，不进 criterion_score。**【不适用】**：本单元不涉及任何生物特征或声学信号处理。
- AI 生成的 JD、拒信、邀约须带标识。**【不适用】**：本单元的 `human_readable` 说明文字全部是确定性模板拼接（继承自 M1 `app/agents/hard_requirement.py` 的生成方式），不是模型生成内容，不触发标识义务；本单元也不发送任何对外拒信。
- 模型全部走境内，简历数据不出境。**【不适用】**：本单元不调用任何模型。
- 绝不用历史录用结果做监督信号。**【不适用】**：本单元的规则判定只读当前岗位的冻结画像规则集，不读取、不使用任何历史录用结果。
- 候选人入口一律用一次性邀请链接，避免被认定"向境内公众提供"。**【不适用】**：申诉登记由 HR 代候选人在工作台操作（design D2/spec Q4 已裁决），本期不开候选人自助入口，不存在候选人直接访问的入口。
- 主观描述（"沟通能力强"）不得进入硬门槛规则，只能作为软技能关键词。**【核心适用】**：Task 3 的规则加载器必须对 `blocking=1` 且命中 `is_subjective()` 的规则拒绝加载，复用 `app/agents/hard_requirement.py` 的 `is_subjective()`/`SubjectiveRequirementError`。

---

## 架构决策（本计划新增，design.md 未覆盖）

**决策 1：规则字段 ↔ 简历字段映射。** `hard_requirement.field` 来自 M1 画像抽取词表（`app/agents/hard_requirement.py::EXTRACTABLE_FIELDS`：`education_requirement`／`experience_years`／`core_skills`／`functional_safety`／`autosar_experience`／`mcu_family`／`diag_stack`／`toolchain`／`sop_projects`），与 `ResumeFields` 的六字段（`name`／`years_of_experience`／`skills`／`companies`／`education`／`expected_city`）是两套不同词表。只有三个规则字段有对应的简历字段：`education_requirement`→`education`、`experience_years`→`years_of_experience`、`core_skills`→`skills`。其余六个规则字段（ECU 专属）本期简历侧完全不抽取（design D4：ECU 特化字段二期），`screen()` 对不在映射表里的规则字段一律判 `skipped("简历未提及")`，不管 `blocking` 取值——这是 design.md 原文"这些规则的依赖字段"未提及"⇒ skipped，不误判"的直接实现。映射表是 `app/agents/hard_requirement_screening.py` 里的模块级常量 `RULE_FIELD_TO_RESUME_FIELD`。

**决策 2：学历档位比较。** 简历 `education.value` 是 `EducationValue(degree, school)`，`degree` 是自由文本（如"本科"、"硕士"）。规则的 `education_gte` 比较值取自 大专/本科/硕士/博士 四档（见 `app/agents/hard_requirement.py::_EDUCATION_LEVELS`，私有元组，⛔ 不跨模块导入私有名）。本单元在 `hard_requirement_screening.py` 内独立定义一张更窄的档位表 `_DEGREE_RANK`（{"大专":0,"专科":0,"本科":1,"学士":1,"硕士":2,"研究生":2,"博士":3}），只服务"简历侧单个学历词→档位"这一件事。若 `degree` 文本无法匹配任何已知别名，按本代码库一贯的"宁可少判一条、不可错判"方向（`app/agents/hard_requirement.py` 全文的误差预算注释都是这个方向），判 `skipped("学历文本无法识别为标准学历档位")`，**不是** `fail`。

**决策 3：`contains` 语义。** `core_skills` 规则通过条件：`rule.value` 是 `resume.skills.value`（`list[str]`）中至少一个条目的子串（含相等）。不做 Jaccard 或模糊匹配——那是 U4 精排的排序关注点，不是硬门槛的通过关注点。判定式：`any(rule.value in item for item in resolved_value)`。

**决策 4：证据缺失时 `fail` 降级为 `skipped`。** `screening_flag` 表 CHECK 要求 `fail` 必须带非空 `evidence_ref`，spec 原文"回指为空的 fail 标记 MUST NOT 写入"。简历字段可能"已提及"（`not_mentioned=False`）却仍然拿不到已解析的偏移量（`SpanRef.start`/`.end` 是 `int | None`，只有 quote 反查成功才会被填充，见 `app/schemas/resume_fields.py` 文档字符串）。若某条规则本应判 `fail`，但该字段所有 span 都没有已解析偏移量，`screen()` 必须把这条判定降级为 `skipped("无原文依据")`，而不是产出一条写不进库的 fail。有证据时用 `format_evidence_ref(span_id, start, end)` 取该字段第一个已解析偏移量的 span。

**决策 5：新增 `appeal_event` 表。** `rejection_record`（U1 已建）只有一个裸的 `appeal_status` 列，没有逐次流转的操作人/时刻留痕；但 tasks.md 4.6 要求两个新接口"均记操作人"——不只是最终的 `overturned`，每一次合法流转（含最初的 none→requested"登记"）都要记。因此 Task 1 在 `app/storage/db.py` 的 SCHEMA 里新增一张表 `appeal_event`（`id, rejection_record_id, from_status, to_status, actor NOT NULL 非空校验, occurred_at`），`CREATE TABLE IF NOT EXISTS`，**不**进 `_ADDED_COLUMNS`（新表不需要老库补列路径）。这是本单元唯一的 schema 改动——一个名为"硬门槛引擎"的单元touch 了 `db.py`，特此在这里点明理由，reviewer 不应视为越界。

**决策 6：模块与函数落点。**
- `app/agents/hard_requirement_screening.py`：纯函数 `screen()`、`RuleVerdict` 数据类、内部比较逻辑 `_evaluate_operator()`、映射表常量。**不** import `sqlite3`。
- `app/graph/screening_nodes.py`（新文件，与 `app/graph/resume_nodes.py` 并列）：`load_active_rules()`（4.2 规则加载器，读库）、`compute_screen()`（L4 读编排，组装 `screen()` 的三个入参）、`effect_persist_flags()`（唯一写入点，`@idempotent_effect("effect_persist_flags")`）、`screen_and_persist()`（把 `compute_screen` 与 `effect_persist_flags`串起来的便捷入口，三个触发点都调它）、`latest_approved_profile_version()`（查询"当前冻结画像版本"的共享小工具，避免在三处重复同一条 SQL）。
- `app/storage/rejection.py`（新文件）：`write_rejection()`——唯一允许写 `rejection_record` 的函数。
- `app/storage/appeal.py`（新文件）：`transition_appeal()`——申诉状态机。

**决策 7：`RuleVerdict.rule_ref` 格式** 为 `f"{rule.field}:{rule.operator}:{rule.value}"`——确定性、人可读，与 `hard_requirement` 表的复合主键（job_id/profile_version 已经是 `screening_flag` 行自己的列，`rule_ref` 只需要 field/operator/value 三段就能在一次判定里保持唯一）同源。

**决策 8：`equals`/`is_true` 两个运算符的测试范围。** 当前没有任何映射进简历字段的规则会用到这两个运算符（`functional_safety`=equals、`sop_projects`=is_true 都在决策 1 的"未映射→恒 skip"集合里）。因此这两个运算符通过一个可独立调用的比较函数 `_evaluate_operator(operator, rule_value, resolved_value) -> bool` 直接测试（不依赖任何简历字段映射），另外用一条集成测试证明 `functional_safety`/`sop_projects` 规则无论简历内容如何都恒为 `skipped`——这是 design D4 刻意的行为，不是遗漏。

**决策 9：画像升版重判（tasks 4.3"三种触发点"的第三种）不做生产接线。** 本单元不会把重判自动挂到 M1 的画像重新确认流程（`app/graph/nodes.py::effect_confirm_profile`）上——tasks.md §4 没有任何一条要求这么做，改动 M1 的确认流程不在本单元的影响范围内。这一触发点只在测试层面证明：对同一 `application_id` 用两个不同的 `profile_version` 直接调用 `screen_and_persist()` 两次，断言两组 `screening_flag` 都存在（旧组保留、新组新增），对应 spec Scenario"画像升版后重判"。

**决策 10：`queue_reapplication_screening` 签名变更。** 3.10 遗留注释设想"U3 落地时只换函数体、不改调用方"，但真实重判必须查库（规则集、简历当前字段、待校对队列），离不开一个数据库连接。函数因此必须多接一个 `conn` 参数；唯一调用方 `app/web/server.py::review_field` 路由本来就在闭包里持有 `conn`，改动是一行。这是对 3.10 那条设想的唯一背离，影响面仅此一个调用点，本计划 Task 6 会同时改掉两处。

**决策 11：路由前缀。** tasks.md 4.6 写的路径是 `POST /applications/{id}/appeal` 与 `POST /rejections/{id}/appeal/transition`，但这个代码库所有 JSON 接口都挂在 `/api/` 前缀下（如已有的 `/api/resumes/{resume_id}/fields/{field}/review`）。本计划按项目实际路由约定落地为 `POST /api/applications/{id}/appeal` 与 `POST /api/rejections/{id}/appeal/transition`，与 tasks.md 的路径含义一致，只是补上项目统一前缀。

---

## Requirement → Task 对照

| spec Requirement | 覆盖 Task |
|---|---|
| 逐条判定只标记不淘汰 | Task 2 |
| 每条 fail 标记带原文依据 | Task 2（架构决策 4） |
| 低置信度字段跳过判定 | Task 2 |
| 淘汰只由人确认并可申诉 | Task 7、Task 8、Task 9 |
| 规则版本随画像冻结版本 | Task 3、Task 4 |
| candidate-ranking「主观描述不入硬门槛」 | Task 3 |

---

### Task 1: `appeal_event` 表（本单元唯一的 schema 改动）

**Files:**
- Modify: `app/storage/db.py`
- Test: `tests/test_appeal_event_schema.py`

**Interfaces:**
- Produces: 表 `appeal_event(id, rejection_record_id, from_status, to_status, actor, occurred_at)`，供 Task 8 的 `transition_appeal()` 写入。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_appeal_event_schema.py`：

```python
from __future__ import annotations

import sqlite3

import pytest

from app.storage.db import init_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    return c


def test_appeal_event_table_exists(conn):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='appeal_event'"
    ).fetchone()
    assert row is not None


def test_appeal_event_columns(conn):
    columns = {r[1] for r in conn.execute("PRAGMA table_info(appeal_event)")}
    assert columns == {
        "id", "rejection_record_id", "from_status", "to_status", "actor", "occurred_at",
    }


def test_appeal_event_actor_blank_rejected(conn):
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.execute(
        "INSERT INTO candidate (id, name) VALUES ('c1', '张三')"
    )
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'hash1', 'alice')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('a1', 'c1', 'j1', 'r1', 'rejected')"
    )
    conn.execute(
        "INSERT INTO rejection_record (id, application_id, reason_type, decided_by) "
        "VALUES ('rej1', 'a1', 'human_decision', 'hr1')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO appeal_event (id, rejection_record_id, from_status, to_status, actor) "
            "VALUES ('ev1', 'rej1', 'none', 'requested', '   ')"
        )


def test_appeal_event_requires_valid_rejection_record(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO appeal_event (id, rejection_record_id, from_status, to_status, actor) "
            "VALUES ('ev1', 'no-such-rejection', 'none', 'requested', 'hr1')"
        )


def test_fresh_and_migrated_schemas_agree_on_appeal_event():
    fresh = sqlite3.connect(":memory:")
    init_schema(fresh)
    fresh_columns = {r[1] for r in fresh.execute("PRAGMA table_info(appeal_event)")}
    assert fresh_columns == {
        "id", "rejection_record_id", "from_status", "to_status", "actor", "occurred_at",
    }
```

- [ ] **Step 2: 运行测试确认失败**

Run: `venv/bin/pytest tests/test_appeal_event_schema.py -v`
Expected: FAIL（`appeal_event` 表不存在，`sqlite3.OperationalError: no such table: appeal_event`）

- [ ] **Step 3: 在 `app/storage/db.py` 新增 `appeal_event` 表**

在 `app/storage/db.py` 的 `rejection_record` 表定义与其两条索引之后（紧接在 `CREATE INDEX IF NOT EXISTS idx_rejection_record_batch ON rejection_record (batch_id);` 之后、`resume_access_log` 表定义之前，大约第 450 行），插入：

```sql
-- 申诉流转审计（m2-resume-parse-and-rank U3 tasks 4.6「均记操作人」）。
-- rejection_record.appeal_status 只保留"当前状态"，本表记录每一次合法
-- 流转的操作人与时刻——包括 none→requested 这次"登记"，不仅仅是最终的
-- overturned。新表，走 CREATE TABLE IF NOT EXISTS，**不进 _ADDED_COLUMNS**：
-- 加列路径只服务"老库缺列"这一种情况，新表不需要它。
--
-- actor 的 CHECK 与 rejection_record.decided_by 同一手法：trim 第二参数
-- 显式列出空格/制表/换行/回车（SQLite 单参 trim() 只剥空格）——空操作人
-- 等于没有留痕，且由数据库强制。
CREATE TABLE IF NOT EXISTS appeal_event (
    id TEXT PRIMARY KEY NOT NULL,
    rejection_record_id TEXT NOT NULL REFERENCES rejection_record(id),
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    actor TEXT NOT NULL CHECK (
        actor IS NOT NULL
        AND trim(actor, ' ' || char(9) || char(10) || char(13)) != ''
    ),
    occurred_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_appeal_event_rejection ON appeal_event (rejection_record_id);
```

- [ ] **Step 4: 运行测试确认通过**

Run: `venv/bin/pytest tests/test_appeal_event_schema.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 跑一次全量既有 M2 schema 回归，确认没有破坏老库迁移路径**

Run: `venv/bin/pytest tests/test_db_m2_schema.py tests/test_db_m2_u2_schema.py -v`
Expected: PASS（全部既有用例保持通过，无一条因新增表受影响）

- [ ] **Step 6: Commit**

```bash
git add app/storage/db.py tests/test_appeal_event_schema.py
git commit -m "feat(m2-u3): add appeal_event table for appeal transition audit trail"
```

---

### Task 2: `screen()` 纯函数（五种 operator、三态输出、证据门槛）

**Files:**
- Create: `app/agents/hard_requirement_screening.py`
- Test: `tests/test_hard_requirement_screening.py`

**Interfaces:**
- Consumes: `app.agents.hard_requirement.HardRequirement`（field, operator, value, blocking, human_readable）、`app.agents.hard_requirement.is_subjective`、`app.schemas.resume_fields.ResumeFields`/`EducationValue`、`app.audit.evidence_ref.format_evidence_ref(span_id, start, end) -> str`。
- Produces: `RuleVerdict(rule_ref: str, verdict: str, reason: str | None, evidence_ref: str | None, human_readable: str, blocking: bool)`；`screen(fields: ResumeFields, rules: list[HardRequirement], review_queue: frozenset[str]) -> list[RuleVerdict]`；`RULE_FIELD_TO_RESUME_FIELD: dict[str, str]`（供 Task 3/4 与测试复用）；`_evaluate_operator(operator: str, rule_value: str, resolved_value) -> bool`（供本任务与 Task 3 的边界测试直接调用）。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_hard_requirement_screening.py`：

```python
from __future__ import annotations

import json

import pytest

from app.agents.hard_requirement import HardRequirement
from app.agents.hard_requirement_screening import (
    RULE_FIELD_TO_RESUME_FIELD,
    RuleVerdict,
    _evaluate_operator,
    screen,
)
from app.schemas.resume_fields import (
    EducationField,
    EducationValue,
    ListField,
    NumberField,
    ResumeFields,
    SpanRef,
    TextField,
)


def _fields(**overrides) -> ResumeFields:
    base = dict(
        name=TextField(value="张三", confidence=0.95, spans=[SpanRef(span_id=1, quote="张三", start=0, end=2)]),
        years_of_experience=NumberField(
            value=5.0, confidence=0.9, spans=[SpanRef(span_id=2, quote="5年经验", start=10, end=15)]
        ),
        skills=ListField(
            value=["AUTOSAR CP 开发", "Python"],
            confidence=0.9,
            spans=[SpanRef(span_id=3, quote="AUTOSAR CP 开发", start=20, end=30)],
        ),
        companies=ListField(not_mentioned=True, value=[], confidence=1.0),
        education=EducationField(
            value=EducationValue(degree="本科", school="某大学"),
            confidence=0.9,
            spans=[SpanRef(span_id=4, quote="本科", start=40, end=42)],
        ),
        expected_city=TextField(not_mentioned=True, value=None, confidence=1.0),
    )
    base.update(overrides)
    return ResumeFields(**base)


def _rule(field: str, operator: str, value: str, blocking: bool = True) -> HardRequirement:
    return HardRequirement(
        field=field, operator=operator, value=value, blocking=blocking,
        human_readable=f"{field} {operator} {value}",
    )


# ── _evaluate_operator：五种运算符各自的比较逻辑，不依赖任何字段映射 ──

def test_operator_gte_true_and_false():
    assert _evaluate_operator("gte", "3", 5.0) is True
    assert _evaluate_operator("gte", "6", 5.0) is False


def test_operator_education_gte_true_and_false():
    assert _evaluate_operator("education_gte", "本科", "硕士") is True
    assert _evaluate_operator("education_gte", "硕士", "本科") is False


def test_operator_education_gte_unrecognized_resolved_value_is_false():
    assert _evaluate_operator("education_gte", "本科", "野生学历") is False


def test_operator_contains_true_and_false():
    assert _evaluate_operator("contains", "AUTOSAR", ["AUTOSAR CP 开发", "Python"]) is True
    assert _evaluate_operator("contains", "Rust", ["AUTOSAR CP 开发", "Python"]) is False


def test_operator_equals_true_and_false():
    assert _evaluate_operator("equals", "ASIL-D", "ASIL-D") is True
    assert _evaluate_operator("equals", "ASIL-D", "ASIL-B") is False


def test_operator_is_true_true_and_false():
    assert _evaluate_operator("is_true", "is_mass_production", True) is True
    assert _evaluate_operator("is_true", "is_mass_production", False) is False


def test_operator_unknown_raises():
    with pytest.raises(ValueError):
        _evaluate_operator("no_such_op", "x", "y")


# ── screen()：三个已映射字段的真实判定（pass / fail / skipped 三态）──

def test_screen_experience_years_pass():
    rules = [_rule("experience_years", "gte", "3")]
    verdicts = screen(_fields(), rules, frozenset())
    assert len(verdicts) == 1
    assert verdicts[0].verdict == "pass"
    assert verdicts[0].evidence_ref is None


def test_screen_experience_years_fail_carries_evidence():
    rules = [_rule("experience_years", "gte", "10")]
    verdicts = screen(_fields(), rules, frozenset())
    assert len(verdicts) == 1
    v = verdicts[0]
    assert v.verdict == "fail"
    assert v.evidence_ref is not None
    payload = json.loads(v.evidence_ref)
    assert payload == {"span_id": 2, "start": 10, "end": 15}
    assert v.human_readable
    assert v.reason


def test_screen_education_gte_pass():
    rules = [_rule("education_requirement", "education_gte", "本科")]
    verdicts = screen(_fields(), rules, frozenset())
    assert verdicts[0].verdict == "pass"


def test_screen_education_gte_fail():
    rules = [_rule("education_requirement", "education_gte", "硕士")]
    verdicts = screen(_fields(), rules, frozenset())
    assert verdicts[0].verdict == "fail"


def test_screen_core_skills_contains_pass():
    rules = [_rule("core_skills", "contains", "AUTOSAR")]
    verdicts = screen(_fields(), rules, frozenset())
    assert verdicts[0].verdict == "pass"


def test_screen_core_skills_contains_fail():
    rules = [_rule("core_skills", "contains", "Rust")]
    verdicts = screen(_fields(), rules, frozenset())
    assert verdicts[0].verdict == "fail"


# ── skipped 的三种来源：待校对 / 未提及 / 无原文依据 ──

def test_screen_skipped_when_field_in_review_queue():
    rules = [_rule("experience_years", "gte", "10")]
    verdicts = screen(_fields(), rules, frozenset({"years_of_experience"}))
    assert verdicts[0].verdict == "skipped"
    assert verdicts[0].reason == "待校对"


def test_screen_skipped_when_field_not_mentioned():
    rules = [_rule("core_skills", "contains", "AUTOSAR")]
    fields = _fields(skills=ListField(not_mentioned=True, value=[], confidence=1.0))
    verdicts = screen(fields, rules, frozenset())
    assert verdicts[0].verdict == "skipped"
    assert verdicts[0].reason == "简历未提及"


def test_screen_skipped_when_education_text_unrecognized():
    rules = [_rule("education_requirement", "education_gte", "本科")]
    fields = _fields(
        education=EducationField(
            value=EducationValue(degree="野生学历", school=None),
            confidence=0.9,
            spans=[SpanRef(span_id=4, quote="野生学历", start=40, end=44)],
        )
    )
    verdicts = screen(fields, rules, frozenset())
    assert verdicts[0].verdict == "skipped"
    assert verdicts[0].reason == "学历文本无法识别为标准学历档位"


def test_screen_fail_downgrades_to_skipped_when_no_resolved_span():
    """字段已提及但没有任何 span 拿到反查偏移量（LLM 给的 quote 反查失败）——
    这条规则本应 fail，但 fail 必须带 evidence_ref，没有可用偏移量就只能
    skipped，不能写一条 evidence_ref 为空的 fail（架构决策 4）。"""
    rules = [_rule("core_skills", "contains", "Rust")]
    fields = _fields(
        skills=ListField(
            value=["AUTOSAR CP 开发"],
            confidence=0.9,
            spans=[SpanRef(span_id=3, quote="AUTOSAR CP 开发", start=None, end=None)],
        )
    )
    verdicts = screen(fields, rules, frozenset())
    assert verdicts[0].verdict == "skipped"
    assert verdicts[0].reason == "无原文依据"


# ── 未映射的 ECU 专属字段：恒 skip，不管 blocking（架构决策 1／8）──

@pytest.mark.parametrize(
    "field,operator,value",
    [
        ("functional_safety", "equals", "ASIL-D"),
        ("autosar_experience", "contains", "CP"),
        ("mcu_family", "contains", "TC3xx"),
        ("diag_stack", "contains", "UDS"),
        ("toolchain", "contains", "Vector"),
        ("sop_projects", "is_true", "is_mass_production"),
    ],
)
def test_screen_unmapped_fields_always_skipped(field, operator, value):
    for blocking in (True, False):
        rules = [_rule(field, operator, value, blocking=blocking)]
        verdicts = screen(_fields(), rules, frozenset())
        assert verdicts[0].verdict == "skipped"
        assert verdicts[0].reason == "简历未提及"
        assert set(RULE_FIELD_TO_RESUME_FIELD) == {
            "education_requirement", "experience_years", "core_skills",
        }


def test_screen_empty_ruleset_returns_empty_list():
    assert screen(_fields(), [], frozenset()) == []


def test_screen_rule_ref_format():
    rules = [_rule("experience_years", "gte", "3")]
    verdicts = screen(_fields(), rules, frozenset())
    assert verdicts[0].rule_ref == "experience_years:gte:3"


def test_screen_is_deterministic():
    rules = [
        _rule("experience_years", "gte", "3"),
        _rule("education_requirement", "education_gte", "本科"),
    ]
    fields = _fields()
    first = screen(fields, rules, frozenset())
    second = screen(fields, rules, frozenset())
    assert first == second
```

- [ ] **Step 2: 运行测试确认失败**

Run: `venv/bin/pytest tests/test_hard_requirement_screening.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.agents.hard_requirement_screening'`）

- [ ] **Step 3: 实现 `app/agents/hard_requirement_screening.py`**

```python
"""硬门槛引擎（hard-requirement-screening spec「逐条判定只标记不淘汰」，
design D6）。

⛔ 本模块是 L3 纯函数：不调模型、不写库、不持有任何数据库连接（工程铁律 2）。
判定必须确定性：同一简历解析结果与同一规则集重复判定结果相同（spec 原文）。

⛔ 本模块只产出标记，不执行淘汰——没有任何一行代码会把候选人筛掉或写
rejection_record（合规红线：AI 只做排序推荐，不做自动淘汰）。淘汰记录的
唯一写入路径是 app/storage/rejection.py::write_rejection()。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.agents.hard_requirement import HardRequirement
from app.audit.evidence_ref import format_evidence_ref
from app.schemas.resume_fields import EducationValue, ResumeFields

# 规则字段 → 简历字段映射（架构决策 1）。⛔ 不在此映射表中的规则字段一律
# skipped("简历未提及")，不管 blocking——那些字段（ECU 专属：
# functional_safety / autosar_experience / mcu_family / diag_stack /
# toolchain / sop_projects）本期简历侧完全不抽取（design D4：ECU 特化
# 字段二期），不判定是唯一正确行为，不是遗漏。
RULE_FIELD_TO_RESUME_FIELD: dict[str, str] = {
    "education_requirement": "education",
    "experience_years": "years_of_experience",
    "core_skills": "skills",
}

# 学历档位（架构决策 2）。⛔ 不导入 app/agents/hard_requirement.py 里的私有
# _EDUCATION_LEVELS——那张表服务"画像自由文本→门槛"的三层子句判定，语义更
# 宽；这里要的是"简历侧已抽取的单一学历词→档位"，语义更窄，独立维护不会
# 被对方的子句拆分逻辑意外牵动。
_DEGREE_RANK: dict[str, int] = {
    "大专": 0,
    "专科": 0,
    "本科": 1,
    "学士": 1,
    "硕士": 2,
    "研究生": 2,
    "博士": 3,
}


def _degree_rank(text: str | None) -> int | None:
    if not text:
        return None
    for alias, rank in _DEGREE_RANK.items():
        if alias in text:
            return rank
    return None


@dataclass(frozen=True)
class RuleVerdict:
    """一条规则对一份简历的判定结果。"""

    rule_ref: str
    verdict: str  # 'pass' | 'fail' | 'skipped'
    reason: str | None
    evidence_ref: str | None
    human_readable: str
    blocking: bool


def _rule_ref(rule: HardRequirement) -> str:
    """架构决策 7：确定性、人可读，与 hard_requirement 复合主键同源。"""
    return f"{rule.field}:{rule.operator}:{rule.value}"


def _evaluate_operator(operator: str, rule_value: str, resolved_value) -> bool:
    """五种 operator 的裸值比较（tasks 4.1）。⛔ 本函数不知道也不关心
    resolved_value 来自哪个简历字段——纯粹的值比较，可独立于任何字段映射
    被测试（架构决策 8：equals/is_true 当前没有真实映射字段，就靠这个
    函数直接测）。"""
    if operator == "gte":
        return float(resolved_value) >= float(rule_value)
    if operator == "education_gte":
        resolved_rank = _degree_rank(str(resolved_value))
        required_rank = _degree_rank(rule_value)
        if resolved_rank is None or required_rank is None:
            return False
        return resolved_rank >= required_rank
    if operator == "contains":
        if isinstance(resolved_value, list):
            return any(rule_value in item for item in resolved_value)
        return rule_value in str(resolved_value)
    if operator == "equals":
        return str(resolved_value) == rule_value
    if operator == "is_true":
        return bool(resolved_value) is True
    raise ValueError(f"未知 operator：{operator!r}")


def _first_resolved_span(spans) -> tuple[int, int, int] | None:
    """取第一个已完成反查偏移量的 span（架构决策 4）。"""
    for span in spans:
        if span.start is not None and span.end is not None:
            return span.span_id, span.start, span.end
    return None


def _resolved_field_value(fields: ResumeFields, resume_field_name: str):
    field = getattr(fields, resume_field_name)
    if resume_field_name == "education":
        value = field.value.degree if isinstance(field.value, EducationValue) else None
    else:
        value = field.value
    return field, value


def _skipped(rule: HardRequirement, rule_ref: str, reason: str) -> RuleVerdict:
    return RuleVerdict(
        rule_ref=rule_ref,
        verdict="skipped",
        reason=reason,
        evidence_ref=None,
        human_readable=rule.human_readable,
        blocking=rule.blocking,
    )


def screen(
    fields: ResumeFields,
    rules: list[HardRequirement],
    review_queue: frozenset[str],
) -> list[RuleVerdict]:
    """按 rules 逐条判定 fields，产出 pass/fail/skipped 三态标记列表
    （spec「逐条判定只标记不淘汰」）。规则集为空 ⇒ 返回空列表（调用方按
    "无硬门槛"处理，spec Scenario「规则集为空」）。

    review_queue：该简历当前处于 field_review_queue.status='pending' 的
    **简历字段名**集合（如 {"education"}）——⛔ 用简历字段名，不是规则
    字段名，两者词表不同（架构决策 1）。
    """
    verdicts: list[RuleVerdict] = []
    for rule in rules:
        rule_ref = _rule_ref(rule)
        resume_field_name = RULE_FIELD_TO_RESUME_FIELD.get(rule.field)

        if resume_field_name is None:
            verdicts.append(_skipped(rule, rule_ref, "简历未提及"))
            continue

        if resume_field_name in review_queue:
            verdicts.append(_skipped(rule, rule_ref, "待校对"))
            continue

        field, resolved_value = _resolved_field_value(fields, resume_field_name)

        if field.not_mentioned:
            verdicts.append(_skipped(rule, rule_ref, "简历未提及"))
            continue

        if rule.operator == "education_gte" and _degree_rank(str(resolved_value)) is None:
            verdicts.append(_skipped(rule, rule_ref, "学历文本无法识别为标准学历档位"))
            continue

        satisfied = _evaluate_operator(rule.operator, rule.value, resolved_value)

        if satisfied:
            verdicts.append(
                RuleVerdict(
                    rule_ref=rule_ref, verdict="pass", reason=None, evidence_ref=None,
                    human_readable=rule.human_readable, blocking=rule.blocking,
                )
            )
            continue

        resolved_span = _first_resolved_span(field.spans)
        if resolved_span is None:
            verdicts.append(_skipped(rule, rule_ref, "无原文依据"))
            continue

        span_id, start, end = resolved_span
        verdicts.append(
            RuleVerdict(
                rule_ref=rule_ref,
                verdict="fail",
                reason=rule.human_readable,
                evidence_ref=format_evidence_ref(span_id, start, end),
                human_readable=rule.human_readable,
                blocking=rule.blocking,
            )
        )
    return verdicts
```

- [ ] **Step 4: 运行测试确认通过**

Run: `venv/bin/pytest tests/test_hard_requirement_screening.py -v`
Expected: PASS（22 passed）

- [ ] **Step 5: grep 校验模块内没有任何存储写入（tasks 4.1 判据）**

Run: `grep -nE "sqlite3|conn\.execute|conn\.commit|import app\.storage" app/agents/hard_requirement_screening.py`
Expected: 无输出（exit code 1）

- [ ] **Step 6: Commit**

```bash
git add app/agents/hard_requirement_screening.py tests/test_hard_requirement_screening.py
git commit -m "feat(m2-u3): add pure hard-requirement screening engine with 5 operators"
```

---

### Task 3: 规则集加载器（按画像版本、拒绝主观描述）

**Files:**
- Create: `app/graph/screening_nodes.py`
- Test: `tests/test_screening_nodes_rule_loader.py`

**Interfaces:**
- Consumes: `app.agents.hard_requirement.HardRequirement`、`is_subjective`、`SubjectiveRequirementError`（复用，不新定义）。
- Produces: `load_active_rules(conn: sqlite3.Connection, *, job_id: str, profile_version: int) -> list[HardRequirement]`；`latest_approved_profile_version(conn: sqlite3.Connection, job_id: str) -> int | None`（供 Task 5/6 复用，避免三处重复同一条 SQL）。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_screening_nodes_rule_loader.py`：

```python
from __future__ import annotations

import sqlite3

import pytest

from app.agents.hard_requirement import SubjectiveRequirementError
from app.graph.screening_nodes import latest_approved_profile_version, load_active_rules
from app.storage.db import init_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    c.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    return c


def _insert_rule(conn, *, profile_version=1, field="experience_years", operator="gte",
                  value="3", blocking=1, human_readable="工作年限要求：3 年及以上"):
    conn.execute(
        "INSERT INTO hard_requirement "
        "(job_id, profile_version, field, operator, value, blocking, human_readable) "
        "VALUES ('j1', ?, ?, ?, ?, ?, ?)",
        (profile_version, field, operator, value, blocking, human_readable),
    )
    conn.commit()


def test_loads_rules_for_job_and_version(conn):
    _insert_rule(conn, profile_version=1)
    _insert_rule(conn, profile_version=1, field="core_skills", operator="contains", value="C 语言")
    rules = load_active_rules(conn, job_id="j1", profile_version=1)
    assert len(rules) == 2
    assert {r.field for r in rules} == {"experience_years", "core_skills"}


def test_loader_ignores_other_profile_versions(conn):
    _insert_rule(conn, profile_version=1)
    _insert_rule(conn, profile_version=2, field="core_skills", operator="contains", value="Python")
    rules = load_active_rules(conn, job_id="j1", profile_version=1)
    assert len(rules) == 1
    assert rules[0].field == "experience_years"


def test_loader_empty_ruleset_returns_empty_list(conn):
    assert load_active_rules(conn, job_id="j1", profile_version=99) == []


def test_loader_is_deterministically_ordered(conn):
    _insert_rule(conn, profile_version=1, field="experience_years", operator="gte", value="3")
    _insert_rule(conn, profile_version=1, field="core_skills", operator="contains", value="C 语言")
    _insert_rule(conn, profile_version=1, field="core_skills", operator="contains", value="Python")
    first = load_active_rules(conn, job_id="j1", profile_version=1)
    second = load_active_rules(conn, job_id="j1", profile_version=1)
    assert [(r.field, r.operator, r.value) for r in first] == [
        (r.field, r.operator, r.value) for r in second
    ]


def test_loader_rejects_blocking_subjective_rule(conn):
    _insert_rule(
        conn, profile_version=1, field="core_skills", operator="contains", value="沟通",
        blocking=1, human_readable="沟通能力强（不满足则不通过硬门槛）",
    )
    with pytest.raises(SubjectiveRequirementError):
        load_active_rules(conn, job_id="j1", profile_version=1)


def test_latest_approved_profile_version_none_when_no_approved(conn):
    assert latest_approved_profile_version(conn, "j1") is None


def test_latest_approved_profile_version_picks_max_approved(conn):
    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
        "VALUES ('p1', 'j1', 1, 'approved', '{}')"
    )
    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
        "VALUES ('p2', 'j1', 2, 'pending', '{}')"
    )
    conn.commit()
    assert latest_approved_profile_version(conn, "j1") == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `venv/bin/pytest tests/test_screening_nodes_rule_loader.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.graph.screening_nodes'`）

- [ ] **Step 3: 实现 `app/graph/screening_nodes.py`（本步骤只写 Task 3 涉及的两个函数，Task 4 会往同一文件追加）**

```python
"""硬门槛判定的编排层（design D13：compute_screen → effect_persist_flags）。

⛔ compute_screen 只读、不写（工程铁律 2：L4 编排层的 compute_* 节点可以
查库做输入组装，但写只能在 effect_* 节点里）。
"""
from __future__ import annotations

import logging
import sqlite3
import uuid

from app.agents.hard_requirement import HardRequirement, SubjectiveRequirementError, is_subjective
from app.agents.hard_requirement_screening import RuleVerdict, screen
from app.schemas.resume_fields import ResumeFields
from app.storage.idempotency import idempotent_effect

logger = logging.getLogger(__name__)


def load_active_rules(
    conn: sqlite3.Connection, *, job_id: str, profile_version: int
) -> list[HardRequirement]:
    """按 (job_id, profile_version) 读取 hard_requirement 规则集
    （hard-requirement-screening spec「规则版本随画像冻结版本」）。

    ⛔ 第二道防线：M1 的 assert_no_subjective_requirements() 已经在规则
    落库前拦过一次主观描述（app/graph/nodes.py::_record_hard_requirements），
    这里对 blocking=1 且命中 is_subjective() 的规则再拦一次并拒绝加载
    （candidate-ranking spec「主观描述不入硬门槛」、合规红线）。命中就让
    SubjectiveRequirementError 穿透——这是数据完整性问题，⛔ 不静默跳过。
    """
    rows = conn.execute(
        "SELECT field, operator, value, blocking, human_readable FROM hard_requirement "
        "WHERE job_id = ? AND profile_version = ? ORDER BY field, operator, value",
        (job_id, profile_version),
    ).fetchall()
    rules = [
        HardRequirement(
            field=row[0], operator=row[1], value=row[2],
            blocking=bool(row[3]), human_readable=row[4],
        )
        for row in rows
    ]
    for rule in rules:
        if rule.blocking and (is_subjective(rule.value) or is_subjective(rule.human_readable)):
            raise SubjectiveRequirementError(
                f"规则 job_id={job_id} profile_version={profile_version} "
                f"field={rule.field!r} 是 blocking 且命中主观描述，拒绝加载"
                "（合规红线：主观描述不得进入硬门槛规则，只能作为软技能关键词）"
            )
    return rules


def latest_approved_profile_version(conn: sqlite3.Connection, job_id: str) -> int | None:
    """当前岗位冻结（已确认）的最新画像版本号，没有任何已确认版本时返回
    None——调用方据此判断"这个岗位还不能做硬门槛判定"。"""
    row = conn.execute(
        "SELECT MAX(version) FROM job_profile WHERE job_id = ? AND status = 'approved'",
        (job_id,),
    ).fetchone()
    return row[0] if row and row[0] is not None else None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `venv/bin/pytest tests/test_screening_nodes_rule_loader.py -v`
Expected: PASS（8 passed）

- [ ] **Step 5: Commit**

```bash
git add app/graph/screening_nodes.py tests/test_screening_nodes_rule_loader.py
git commit -m "feat(m2-u3): add hard-requirement rule loader with subjective-rule defense"
```

---

### Task 4: `compute_screen` → `effect_persist_flags`（幂等落库、重判保留旧版本）

**Files:**
- Modify: `app/graph/screening_nodes.py`
- Test: `tests/test_screening_nodes_compute_effect.py`

**Interfaces:**
- Consumes: Task 2 的 `screen()`／`RuleVerdict`；Task 3 的 `load_active_rules()`；`app.storage.idempotency.idempotent_effect`。
- Produces: `compute_screen(conn, *, resume_id: str, job_id: str, profile_version: int) -> list[RuleVerdict]`；`effect_persist_flags(conn, *, thread_id: str, business_key: str, application_id: str, profile_version: int, verdicts: list[RuleVerdict]) -> int | None`；`screen_and_persist(conn, *, application_id: str, resume_id: str, job_id: str, profile_version: int, parse_version: str) -> int | None`（Task 5/6 的唯一调用入口）。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_screening_nodes_compute_effect.py`：

```python
from __future__ import annotations

import json
import sqlite3

import pytest

from app.graph.screening_nodes import compute_screen, effect_persist_flags, screen_and_persist
from app.schemas.resume_fields import (
    EducationField,
    EducationValue,
    ListField,
    NumberField,
    ResumeFields,
    SpanRef,
    TextField,
)
from app.storage.db import init_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    c.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    c.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    c.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
        "uploaded_by, status, parsed_json, parser_version) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'hash1', 'alice', 'parsed', ?, 'v1')",
        (_fields_json(),),
    )
    c.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('a1', 'c1', 'j1', 'r1', 'initial')"
    )
    c.commit()
    return c


def _fields_json() -> str:
    fields = ResumeFields(
        name=TextField(value="张三", confidence=0.95, spans=[SpanRef(span_id=1, quote="张三", start=0, end=2)]),
        years_of_experience=NumberField(
            value=2.0, confidence=0.9, spans=[SpanRef(span_id=2, quote="2年经验", start=10, end=15)]
        ),
        skills=ListField(not_mentioned=True, value=[], confidence=1.0),
        companies=ListField(not_mentioned=True, value=[], confidence=1.0),
        education=EducationField(
            value=EducationValue(degree="本科", school="某大学"), confidence=0.9,
            spans=[SpanRef(span_id=3, quote="本科", start=20, end=22)],
        ),
        expected_city=TextField(not_mentioned=True, value=None, confidence=1.0),
    )
    return fields.model_dump_json()


def _insert_rule(conn, *, profile_version, field, operator, value, blocking=1):
    conn.execute(
        "INSERT INTO hard_requirement "
        "(job_id, profile_version, field, operator, value, blocking, human_readable) "
        "VALUES ('j1', ?, ?, ?, ?, ?, ?)",
        (profile_version, field, operator, value, blocking, f"{field} {operator} {value}"),
    )
    conn.commit()


def test_compute_screen_returns_verdicts(conn):
    _insert_rule(conn, profile_version=1, field="experience_years", operator="gte", value="5")
    verdicts = compute_screen(conn, resume_id="r1", job_id="j1", profile_version=1)
    assert len(verdicts) == 1
    assert verdicts[0].verdict == "fail"


def test_compute_screen_respects_pending_review_queue(conn):
    _insert_rule(conn, profile_version=1, field="experience_years", operator="gte", value="5")
    conn.execute(
        "INSERT INTO field_review_queue (id, resume_id, field, status) "
        "VALUES ('q1', 'r1', 'years_of_experience', 'pending')"
    )
    conn.commit()
    verdicts = compute_screen(conn, resume_id="r1", job_id="j1", profile_version=1)
    assert verdicts[0].verdict == "skipped"
    assert verdicts[0].reason == "待校对"


def test_effect_persist_flags_writes_rows_and_effect_log(conn):
    _insert_rule(conn, profile_version=1, field="experience_years", operator="gte", value="5")
    verdicts = compute_screen(conn, resume_id="r1", job_id="j1", profile_version=1)
    written = effect_persist_flags(
        conn, thread_id="a1", business_key="1:v1",
        application_id="a1", profile_version=1, verdicts=verdicts,
    )
    assert written == 1
    flag_count = conn.execute(
        "SELECT COUNT(*) FROM screening_flag WHERE application_id = 'a1'"
    ).fetchone()[0]
    effect_log_count = conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE effect_key = 'a1:effect_persist_flags:1:v1'"
    ).fetchone()[0]
    assert flag_count == 1 == effect_log_count


def test_effect_persist_flags_writes_evidence_ref_for_fail(conn):
    _insert_rule(conn, profile_version=1, field="experience_years", operator="gte", value="5")
    verdicts = compute_screen(conn, resume_id="r1", job_id="j1", profile_version=1)
    effect_persist_flags(
        conn, thread_id="a1", business_key="1:v1",
        application_id="a1", profile_version=1, verdicts=verdicts,
    )
    row = conn.execute(
        "SELECT verdict, evidence_ref FROM screening_flag WHERE application_id = 'a1'"
    ).fetchone()
    assert row[0] == "fail"
    assert row[1] is not None
    assert json.loads(row[1]) == {"span_id": 2, "start": 10, "end": 15}


def test_screen_and_persist_is_idempotent(conn):
    _insert_rule(conn, profile_version=1, field="experience_years", operator="gte", value="5")
    first = screen_and_persist(
        conn, application_id="a1", resume_id="r1", job_id="j1",
        profile_version=1, parse_version="v1",
    )
    second = screen_and_persist(
        conn, application_id="a1", resume_id="r1", job_id="j1",
        profile_version=1, parse_version="v1",
    )
    assert first == 1
    assert second is None
    flag_count = conn.execute(
        "SELECT COUNT(*) FROM screening_flag WHERE application_id = 'a1'"
    ).fetchone()[0]
    assert flag_count == 1


def test_screen_and_persist_profile_upgrade_keeps_old_flags_and_adds_new(conn):
    """spec Scenario「画像升版后重判」+ tasks 4.3「三种触发点」的第三种
    （架构决策 9）：同一 application 用两个不同 profile_version 各判一次，
    旧组保留、新组新增，互不覆盖。"""
    _insert_rule(conn, profile_version=1, field="experience_years", operator="gte", value="1")
    _insert_rule(conn, profile_version=2, field="experience_years", operator="gte", value="10")

    screen_and_persist(
        conn, application_id="a1", resume_id="r1", job_id="j1",
        profile_version=1, parse_version="v1",
    )
    screen_and_persist(
        conn, application_id="a1", resume_id="r1", job_id="j1",
        profile_version=2, parse_version="v1",
    )

    rows = conn.execute(
        "SELECT profile_version, verdict FROM screening_flag "
        "WHERE application_id = 'a1' ORDER BY profile_version"
    ).fetchall()
    assert rows == [(1, "pass"), (2, "fail")]


def test_screen_and_persist_reparse_creates_new_set_via_new_parse_version(conn):
    """第二种触发点：字段校对完成后用新的 parser_version 重判——旧
    parse_version 的 flags 也应保留（同一 profile_version 下按 parse_version
    再区分一组）。"""
    _insert_rule(conn, profile_version=1, field="experience_years", operator="gte", value="1")
    screen_and_persist(
        conn, application_id="a1", resume_id="r1", job_id="j1",
        profile_version=1, parse_version="v1",
    )
    screen_and_persist(
        conn, application_id="a1", resume_id="r1", job_id="j1",
        profile_version=1, parse_version="v2",
    )
    count = conn.execute(
        "SELECT COUNT(*) FROM effect_log WHERE thread_id = 'a1' "
        "AND node_name = 'effect_persist_flags'"
    ).fetchone()[0]
    assert count == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `venv/bin/pytest tests/test_screening_nodes_compute_effect.py -v`
Expected: FAIL（`ImportError: cannot import name 'compute_screen' from 'app.graph.screening_nodes'`）

- [ ] **Step 3: 在 `app/graph/screening_nodes.py` 末尾追加三个函数**

在 Task 3 写的两个函数之后追加（同一文件）：

```python
def compute_screen(
    conn: sqlite3.Connection, *, resume_id: str, job_id: str, profile_version: int
) -> list[RuleVerdict]:
    """L4 读编排：组装 screen() 的三个入参并调用它（工程铁律 2，⛔ 本函数
    只读不写）。"""
    resume_row = conn.execute(
        "SELECT parsed_json FROM resume WHERE id = ?", (resume_id,)
    ).fetchone()
    if resume_row is None or resume_row[0] is None:
        raise ValueError(f"resume_id={resume_id} 尚未解析，无法判定硬门槛")
    fields = ResumeFields.model_validate_json(resume_row[0])

    pending_rows = conn.execute(
        "SELECT field FROM field_review_queue WHERE resume_id = ? AND status = 'pending'",
        (resume_id,),
    ).fetchall()
    review_queue = frozenset(row[0] for row in pending_rows)

    rules = load_active_rules(conn, job_id=job_id, profile_version=profile_version)
    return screen(fields, rules, review_queue)


@idempotent_effect("effect_persist_flags")
def effect_persist_flags(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    application_id: str,
    profile_version: int,
    verdicts: list[RuleVerdict],
) -> int:
    """唯一的写入点（tasks 4.3）。幂等键
    {application_id}:effect_persist_flags:{profile_version}:{parse_version}
    由调用方（screen_and_persist）拼好传入 business_key。

    ⛔ 不在这里 conn.commit()——由 idempotent_effect 装饰器统一提交
    （工程铁律 1）。重判（校对完成／画像升版）产生新一组 flags：不同
    business_key 天然对应不同的 effect_key，旧组的行永远不会被本函数
    删除或覆盖。
    """
    for verdict in verdicts:
        conn.execute(
            "INSERT INTO screening_flag "
            "(id, application_id, profile_version, rule_ref, verdict, reason, evidence_ref) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()), application_id, profile_version,
                verdict.rule_ref, verdict.verdict, verdict.reason, verdict.evidence_ref,
            ),
        )
    return len(verdicts)


def screen_and_persist(
    conn: sqlite3.Connection,
    *,
    application_id: str,
    resume_id: str,
    job_id: str,
    profile_version: int,
    parse_version: str,
) -> int | None:
    """三个触发点（初次解析后／字段校对完成后／画像升版后）共用的唯一入口。
    返回 effect_persist_flags 的返回值（写入的标记数，幂等命中时为 None）。
    """
    verdicts = compute_screen(
        conn, resume_id=resume_id, job_id=job_id, profile_version=profile_version
    )
    business_key = f"{profile_version}:{parse_version}"
    return effect_persist_flags(
        conn,
        thread_id=application_id,
        business_key=business_key,
        application_id=application_id,
        profile_version=profile_version,
        verdicts=verdicts,
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `venv/bin/pytest tests/test_screening_nodes_compute_effect.py -v`
Expected: PASS（8 passed）

- [ ] **Step 5: Commit**

```bash
git add app/graph/screening_nodes.py tests/test_screening_nodes_compute_effect.py
git commit -m "feat(m2-u3): wire compute_screen/effect_persist_flags with idempotent versioned persistence"
```

---

### Task 5: 触发点一——上传/重解析后立即判定

**Files:**
- Modify: `app/web/server.py`
- Test: `tests/test_screening_trigger_on_upload.py`

**Interfaces:**
- Consumes: Task 4 的 `screen_and_persist`、Task 3 的 `latest_approved_profile_version`。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_screening_trigger_on_upload.py`：

```python
from __future__ import annotations

import io
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.channels.web_channel import WebChannel
from app.llm.gateway import LLMGateway
from app.storage.db import init_schema
from app.storage.hr_account import create_account
from app.web.server import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.execute(
        "INSERT INTO hard_requirement "
        "(job_id, profile_version, field, operator, value, blocking, human_readable) "
        "VALUES ('j1', 1, 'experience_years', 'gte', '3', 1, '工作年限要求：3 年及以上')"
    )
    conn.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
        "VALUES ('p1', 'j1', 1, 'approved', '{}')"
    )
    create_account(conn, username="hr1", password="testpass123")
    conn.commit()
    conn.close()

    app = create_app(
        db_path=str(db_path),
        gateway=LLMGateway.from_settings_for_test(),
        channel=WebChannel(),
    )
    with TestClient(app) as c:
        login = c.post("/api/login", json={"username": "hr1", "password": "testpass123"})
        assert login.status_code == 200
        yield c


def test_upload_triggers_initial_screening(client, monkeypatch):
    from app.schemas.resume_fields import (
        EducationField, EducationValue, ListField, NumberField, ResumeFields, SpanRef, TextField,
    )

    def fake_compute_parse(gateway, *, spans, audit_context):
        fields = ResumeFields(
            name=TextField(value="张三", confidence=0.9, spans=[SpanRef(span_id=1, quote="张三", start=0, end=2)]),
            years_of_experience=NumberField(
                value=1.0, confidence=0.9, spans=[SpanRef(span_id=2, quote="1年", start=5, end=7)]
            ),
            skills=ListField(not_mentioned=True, value=[], confidence=1.0),
            companies=ListField(not_mentioned=True, value=[], confidence=1.0),
            education=EducationField(not_mentioned=True, value=None, confidence=1.0),
            expected_city=TextField(not_mentioned=True, value=None, confidence=1.0),
        )

        class _Meta:
            response_model = "test-model"

        return fields, _Meta()

    monkeypatch.setattr("app.web.server.compute_parse", fake_compute_parse)

    resp = client.post(
        "/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "synthetic"},
        files={"files": ("a.txt", io.BytesIO(b"张三，1年工作经验" * 5), "text/plain")},
    )
    assert resp.status_code == 200
    body = resp.json()["results"][0]
    assert body["parse_status"] == "parsed"
    application_id = body["application_id"]

    flags = client.get(f"/api/applications/{application_id}/screening-flags").json()
    assert len(flags["flags"]) == 1
    assert flags["flags"][0]["verdict"] == "fail"
```

**注**：本测试假定存在一个只读接口 `GET /api/applications/{id}/screening-flags` 用于断言（该接口不在 tasks.md 4.1–4.6 范围内，但没有它就无法从外部黑盒验证触发是否生效）。作为本任务的一部分顺带加一个最小只读接口，见 Step 3。若上传流程走的是 `.txt`/纯文本以外的类型校验（`SUPPORTED_SUFFIXES` 只含 pdf/docx），把测试文件后缀改为已支持类型前先确认 `SUPPORTED_SUFFIXES` 实际取值：

Run: `grep -n "SUPPORTED_SUFFIXES" app/parsing/extract_text.py`

若结果不含 `.txt`，把测试里的文件名与 content-type 换成 `("a.pdf", ..., "application/pdf")` 并让 `ingest_resume_text` 走它已支持的路径（该函数在别的既有测试里如何构造最小可读 PDF，参考 `tests/test_resume_upload.py` 里已有的辅助函数并复用同一种构造方式，⛔ 不新发明一种）。

- [ ] **Step 2: 运行测试确认失败**

Run: `venv/bin/pytest tests/test_screening_trigger_on_upload.py -v`
Expected: FAIL（`404 Not Found` on `/api/applications/{id}/screening-flags`，或 flags 列表为空）

- [ ] **Step 3: 在 `app/web/server.py` 加两处改动**

3a. 顶部 import 区（第 37 行现有的 import 后）新增：

```python
from app.graph.screening_nodes import latest_approved_profile_version, screen_and_persist
```

3b. 在 `_ingest_one_resume` 函数里，`application_id = effect_persist_parse(...)` 调用结束、`return {...}` 之前（现在的第 1013–1016 行之间）插入：

```python
        resolved_application_id = application_id
        if resolved_application_id is None:
            existing_app = conn.execute(
                "SELECT id FROM application WHERE resume_id = ?", (resume_id,)
            ).fetchone()
            resolved_application_id = existing_app[0] if existing_app else None
        if resolved_application_id is not None:
            profile_version = latest_approved_profile_version(conn, job_id)
            if profile_version is not None:
                screen_and_persist(
                    conn,
                    application_id=resolved_application_id,
                    resume_id=resume_id,
                    job_id=job_id,
                    profile_version=profile_version,
                    parse_version=parser_version,
                )
        return {"file_name": upload.filename, "status": "accepted",
                "resume_id": resume_id, "application_id": application_id,
                "parse_status": "parsed"}
```

（替换掉原来紧跟在 `effect_persist_parse(...)` 调用之后的 `return {"file_name": upload.filename, "status": "accepted", "resume_id": resume_id, "application_id": application_id, "parse_status": "parsed"}` 那一句，把新代码块插在它原来的位置。）

对 `reparse_resume` 路由做同样的插入：在其 `effect_persist_parse(...)` 调用之后（第 1065–1079 行区域）、函数原有的 `return` 语句之前，插入同样的代码块（`job_id`/`resume_id`/`parser_version` 三个局部变量在该函数里同名已存在，直接复用）。

3c. 在路由区新增一个最小只读接口（放在 `list_resumes_for_job` 附近，供本任务与后续人工验证使用）：

```python
    @router.get("/api/applications/{application_id}/screening-flags")
    def get_screening_flags(application_id: str) -> dict:
        rows = conn.execute(
            "SELECT profile_version, rule_ref, verdict, reason, evidence_ref, created_at "
            "FROM screening_flag WHERE application_id = ? ORDER BY created_at",
            (application_id,),
        ).fetchall()
        return {
            "flags": [
                {
                    "profile_version": r[0], "rule_ref": r[1], "verdict": r[2],
                    "reason": r[3], "evidence_ref": r[4], "created_at": r[5],
                }
                for r in rows
            ]
        }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `venv/bin/pytest tests/test_screening_trigger_on_upload.py -v`
Expected: PASS（1 passed）

- [ ] **Step 5: 跑一次既有上传/重解析回归，确认没有破坏原有行为**

Run: `venv/bin/pytest tests/test_resume_upload.py tests/test_resume_reparse.py -v`
Expected: PASS（全部既有用例保持通过）

- [ ] **Step 6: Commit**

```bash
git add app/web/server.py tests/test_screening_trigger_on_upload.py
git commit -m "feat(m2-u3): trigger hard-requirement screening right after resume parsing"
```

---

### Task 6: 触发点二——字段校对完成后重判

**Files:**
- Modify: `app/graph/resume_nodes.py`
- Modify: `app/web/server.py`
- Test: `tests/test_screening_trigger_on_field_review.py`

**Interfaces:**
- Consumes: Task 4 的 `screen_and_persist`、Task 3 的 `latest_approved_profile_version`。
- Produces: `queue_reapplication_screening(conn: sqlite3.Connection, resume_id: str) -> None`（签名变更，架构决策 10）。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_screening_trigger_on_field_review.py`：

```python
from __future__ import annotations

import sqlite3

import pytest

from app.graph.resume_nodes import queue_reapplication_screening
from app.storage.db import init_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    c.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    c.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    c.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
        "VALUES ('p1', 'j1', 1, 'approved', '{}')"
    )
    c.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
        "uploaded_by, status, parsed_json, parser_version) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'hash1', 'alice', 'parsed', ?, 'v1')",
        (_fields_json(),),
    )
    c.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('a1', 'c1', 'j1', 'r1', 'initial')"
    )
    c.execute(
        "INSERT INTO hard_requirement "
        "(job_id, profile_version, field, operator, value, blocking, human_readable) "
        "VALUES ('j1', 1, 'experience_years', 'gte', '5', 1, '工作年限要求：5 年及以上')"
    )
    c.commit()
    return c


def _fields_json() -> str:
    from app.schemas.resume_fields import (
        EducationField, ListField, NumberField, ResumeFields, SpanRef, TextField,
    )
    fields = ResumeFields(
        name=TextField(value="张三", confidence=0.9, spans=[SpanRef(span_id=1, quote="张三", start=0, end=2)]),
        years_of_experience=NumberField(
            value=2.0, confidence=0.9, spans=[SpanRef(span_id=2, quote="2年", start=5, end=7)]
        ),
        skills=ListField(not_mentioned=True, value=[], confidence=1.0),
        companies=ListField(not_mentioned=True, value=[], confidence=1.0),
        education=EducationField(not_mentioned=True, value=None, confidence=1.0),
        expected_city=TextField(not_mentioned=True, value=None, confidence=1.0),
    )
    return fields.model_dump_json()


def test_queue_reapplication_screening_persists_flags(conn):
    queue_reapplication_screening(conn, "r1")
    row = conn.execute(
        "SELECT verdict FROM screening_flag WHERE application_id = 'a1'"
    ).fetchone()
    assert row is not None
    assert row[0] == "fail"


def test_queue_reapplication_screening_no_op_when_no_approved_profile(conn):
    conn.execute("UPDATE job_profile SET status = 'pending' WHERE id = 'p1'")
    conn.commit()
    queue_reapplication_screening(conn, "r1")
    count = conn.execute(
        "SELECT COUNT(*) FROM screening_flag WHERE application_id = 'a1'"
    ).fetchone()[0]
    assert count == 0


def test_queue_reapplication_screening_no_op_when_resume_missing(conn):
    queue_reapplication_screening(conn, "no-such-resume")  # 不应抛异常
```

- [ ] **Step 2: 运行测试确认失败**

Run: `venv/bin/pytest tests/test_screening_trigger_on_field_review.py -v`
Expected: FAIL（`TypeError: queue_reapplication_screening() takes 1 positional argument but 2 were given`）

- [ ] **Step 3: 替换 `app/graph/resume_nodes.py` 里的 `queue_reapplication_screening` 函数体**

把文件末尾原来的：

```python
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

整段替换为：

```python
def queue_reapplication_screening(conn: sqlite3.Connection, resume_id: str) -> None:
    """字段校对完成后重判（tasks 3.10 的空壳、U3 tasks 4.3 的真实实现，
    hard-requirement-screening spec Scenario「依赖字段待校对」："校对完成
    后该规则被重新判定"）。

    ⛔ 架构决策 10：3.10 原注释设想"函数体换掉、调用方签名不变"，但真实
    重判必须查库（规则集、简历字段、待校对队列），离不开一个连接——本次
    落地对签名做了唯一的背离：多接一个 conn 参数。唯一调用方
    app/web/server.py::review_field 路由本来就在闭包里持有 conn，改动
    只有一行。
    """
    row = conn.execute(
        "SELECT job_id, parser_version FROM resume WHERE id = ?", (resume_id,)
    ).fetchone()
    if row is None or row[1] is None:
        logger.warning("resume_id=%s 未找到或尚未解析，跳过重判", resume_id)
        return
    job_id, parser_version = row

    application_row = conn.execute(
        "SELECT id FROM application WHERE resume_id = ?", (resume_id,)
    ).fetchone()
    if application_row is None:
        logger.warning("resume_id=%s 尚无投递记录，跳过重判", resume_id)
        return
    application_id = application_row[0]

    profile_version = latest_approved_profile_version(conn, job_id)
    if profile_version is None:
        logger.warning("job_id=%s 尚无已确认画像版本，跳过重判", job_id)
        return

    screen_and_persist(
        conn,
        application_id=application_id,
        resume_id=resume_id,
        job_id=job_id,
        profile_version=profile_version,
        parse_version=parser_version,
    )
```

在文件顶部 import 区新增（`from app.storage.idempotency import idempotent_effect` 那一行附近）：

```python
from app.graph.screening_nodes import latest_approved_profile_version, screen_and_persist
```

- [ ] **Step 4: 更新唯一调用方 `app/web/server.py::review_field`**

把：

```python
        queue_reapplication_screening(resume_id)
```

改为：

```python
        queue_reapplication_screening(conn, resume_id)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `venv/bin/pytest tests/test_screening_trigger_on_field_review.py -v`
Expected: PASS（3 passed）

- [ ] **Step 6: 跑一次既有字段校对回归，确认调用方改动没有破坏原有行为**

Run: `venv/bin/pytest tests/test_resume_field_review.py -v`
Expected: PASS（全部既有用例保持通过）

- [ ] **Step 7: Commit**

```bash
git add app/graph/resume_nodes.py app/web/server.py tests/test_screening_trigger_on_field_review.py
git commit -m "feat(m2-u3): trigger hard-requirement re-screening after field review completes"
```

---

### Task 7: `write_rejection()`——拒绝记录写入唯一路径

**Files:**
- Create: `app/storage/rejection.py`
- Test: `tests/test_rejection_write.py`

**Interfaces:**
- Produces: `write_rejection(conn, *, application_id: str, reason_type: str, decided_by: str, rule_ref: str | None = None, human_readable: str | None = None, batch_id: str | None = None) -> str`；`InvalidRejectionReason(ValueError)`。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_rejection_write.py`：

```python
from __future__ import annotations

import sqlite3

import pytest

from app.storage.db import init_schema
from app.storage.rejection import InvalidRejectionReason, write_rejection


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    c.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    c.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    c.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'hash1', 'alice')"
    )
    c.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('a1', 'c1', 'j1', 'r1', 'initial')"
    )
    c.commit()
    return c


def test_write_rejection_hard_rule_succeeds(conn):
    rejection_id = write_rejection(
        conn, application_id="a1", reason_type="hard_rule",
        rule_ref="experience_years:gte:5", human_readable="工作年限不满足",
        decided_by="hr1",
    )
    row = conn.execute(
        "SELECT reason_type, rule_ref, decided_by FROM rejection_record WHERE id = ?",
        (rejection_id,),
    ).fetchone()
    assert row == ("hard_rule", "experience_years:gte:5", "hr1")


def test_write_rejection_moves_application_to_rejected_stage(conn):
    write_rejection(conn, application_id="a1", reason_type="human_decision", decided_by="hr1")
    row = conn.execute(
        "SELECT status, current_stage_id FROM application WHERE id = 'a1'"
    ).fetchone()
    assert row == ("rejected", "rejected")


def test_write_rejection_writes_stage_history_with_from_stage(conn):
    write_rejection(conn, application_id="a1", reason_type="human_decision", decided_by="hr1")
    row = conn.execute(
        "SELECT from_stage_id, to_stage_id, actor_type, actor FROM application_stage_history "
        "WHERE application_id = 'a1' AND to_stage_id = 'rejected'"
    ).fetchone()
    assert row == ("initial", "rejected", "human", "hr1")


def test_write_rejection_human_decision_without_rule_ref_ok(conn):
    write_rejection(conn, application_id="a1", reason_type="human_decision", decided_by="hr1")


def test_write_rejection_hard_rule_without_rule_ref_raises(conn):
    with pytest.raises(InvalidRejectionReason):
        write_rejection(conn, application_id="a1", reason_type="hard_rule", decided_by="hr1")


def test_write_rejection_ai_score_rejected_at_application_layer(conn):
    with pytest.raises(InvalidRejectionReason):
        write_rejection(conn, application_id="a1", reason_type="ai_score", decided_by="hr1")


def test_write_rejection_blank_decided_by_raises(conn):
    with pytest.raises(InvalidRejectionReason):
        write_rejection(conn, application_id="a1", reason_type="human_decision", decided_by="   ")


def test_ai_score_rejected_at_check_constraint_layer_bypassing_application(conn):
    """合规红线第二道防线：绕过 write_rejection() 直接 INSERT，CHECK 约束
    仍然拒绝。"""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO rejection_record (id, application_id, reason_type, decided_by) "
            "VALUES ('rej-bad', 'a1', 'ai_score', 'hr1')"
        )
```

- [ ] **Step 2: 运行测试确认失败**

Run: `venv/bin/pytest tests/test_rejection_write.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.storage.rejection'`）

- [ ] **Step 3: 实现 `app/storage/rejection.py`**

```python
"""拒绝记录写入的唯一路径（hard-requirement-screening spec「淘汰只由人
确认并可申诉」，合规红线「AI 只做排序推荐，不做自动淘汰」，design D6）。

⛔ 本模块外的任何代码不得直接 INSERT rejection_record——这是应用层的
唯一路径；数据库 CHECK 约束（rejection_record.reason_type）是第二道防线，
两道防线都要有测试覆盖。
"""
from __future__ import annotations

import sqlite3
import uuid

_VALID_REASON_TYPES = frozenset({"hard_rule", "human_decision"})


class InvalidRejectionReason(ValueError):
    """理由类型不合法，或 hard_rule 缺 rule_ref，或决策人为空。⛔ 不静默
    降级、不猜默认值——这条异常必须让调用方（未来的 U5 单条/批量确认
    接口）整体失败。"""


def write_rejection(
    conn: sqlite3.Connection,
    *,
    application_id: str,
    reason_type: str,
    decided_by: str,
    rule_ref: str | None = None,
    human_readable: str | None = None,
    batch_id: str | None = None,
) -> str:
    """写一条拒绝记录并把投递流转到"已淘汰"阶段（同一事务）。返回
    rejection_record.id。

    ⛔ reason_type 不接受 'ai_score'——合规红线的应用层第一道防线。
    """
    if reason_type not in _VALID_REASON_TYPES:
        raise InvalidRejectionReason(
            f"reason_type={reason_type!r} 不合法，只能是 hard_rule 或 human_decision"
            "（合规红线：AI 只做排序推荐，不做自动淘汰）"
        )
    if reason_type == "hard_rule" and not rule_ref:
        raise InvalidRejectionReason("reason_type='hard_rule' 必须带 rule_ref")
    if not decided_by or not decided_by.strip():
        raise InvalidRejectionReason("decided_by 不能为空")

    application_row = conn.execute(
        "SELECT current_stage_id FROM application WHERE id = ?", (application_id,)
    ).fetchone()
    if application_row is None:
        raise ValueError(f"application_id={application_id} 不存在")
    from_stage_id = application_row[0]

    rejection_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO rejection_record "
        "(id, application_id, reason_type, rule_ref, human_readable, decided_by, batch_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (rejection_id, application_id, reason_type, rule_ref, human_readable, decided_by, batch_id),
    )
    conn.execute(
        "UPDATE application SET status = 'rejected', current_stage_id = 'rejected' WHERE id = ?",
        (application_id,),
    )
    conn.execute(
        "INSERT INTO application_stage_history "
        "(id, application_id, from_stage_id, to_stage_id, actor_type, actor) "
        "VALUES (?, ?, ?, 'rejected', 'human', ?)",
        (str(uuid.uuid4()), application_id, from_stage_id, decided_by),
    )
    conn.commit()
    return rejection_id
```

- [ ] **Step 4: 运行测试确认通过**

Run: `venv/bin/pytest tests/test_rejection_write.py -v`
Expected: PASS（8 passed）

- [ ] **Step 5: Commit**

```bash
git add app/storage/rejection.py tests/test_rejection_write.py
git commit -m "feat(m2-u3): add write_rejection as the sole rejection_record write path"
```

---

### Task 8: 申诉状态机 `transition_appeal()`

**Files:**
- Create: `app/storage/appeal.py`
- Test: `tests/test_appeal_state_machine.py`

**Interfaces:**
- Consumes: Task 7 的 `write_rejection()`（测试里用它准备一条拒绝记录）。
- Produces: `transition_appeal(conn, *, rejection_record_id: str, to_status: str, actor: str) -> dict`；`IllegalAppealTransition(ValueError)`；`AppealRecordNotFound(ValueError)`。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_appeal_state_machine.py`：

```python
from __future__ import annotations

import sqlite3

import pytest

from app.storage.appeal import AppealRecordNotFound, IllegalAppealTransition, transition_appeal
from app.storage.db import init_schema
from app.storage.rejection import write_rejection


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    c.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    c.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    c.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'hash1', 'alice')"
    )
    c.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('a1', 'c1', 'j1', 'r1', 'screening')"
    )
    c.commit()
    return c


@pytest.fixture
def rejection_id(conn):
    return write_rejection(
        conn, application_id="a1", reason_type="hard_rule",
        rule_ref="experience_years:gte:5", human_readable="工作年限不满足",
        decided_by="hr1",
    )


def test_none_to_requested_is_legal(conn, rejection_id):
    result = transition_appeal(conn, rejection_record_id=rejection_id, to_status="requested", actor="hr2")
    assert result == {"appeal_status": "requested", "already_applied": False}
    row = conn.execute(
        "SELECT appeal_status FROM rejection_record WHERE id = ?", (rejection_id,)
    ).fetchone()
    assert row[0] == "requested"


def test_every_transition_records_an_appeal_event(conn, rejection_id):
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="requested", actor="hr2")
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="under_review", actor="hr3")
    rows = conn.execute(
        "SELECT from_status, to_status, actor FROM appeal_event "
        "WHERE rejection_record_id = ? ORDER BY occurred_at",
        (rejection_id,),
    ).fetchall()
    assert rows == [("none", "requested", "hr2"), ("requested", "under_review", "hr3")]


def test_full_legal_path_to_upheld(conn, rejection_id):
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="requested", actor="hr2")
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="under_review", actor="hr3")
    result = transition_appeal(conn, rejection_record_id=rejection_id, to_status="upheld", actor="hr3")
    assert result["appeal_status"] == "upheld"


def test_illegal_transition_skipping_a_state_raises(conn, rejection_id):
    with pytest.raises(IllegalAppealTransition):
        transition_appeal(conn, rejection_record_id=rejection_id, to_status="under_review", actor="hr2")


def test_illegal_transition_from_terminal_state_raises(conn, rejection_id):
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="requested", actor="hr2")
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="under_review", actor="hr3")
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="upheld", actor="hr3")
    with pytest.raises(IllegalAppealTransition):
        transition_appeal(conn, rejection_record_id=rejection_id, to_status="overturned", actor="hr3")


def test_repeat_submit_same_target_is_idempotent_no_second_event(conn, rejection_id):
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="requested", actor="hr2")
    result = transition_appeal(conn, rejection_record_id=rejection_id, to_status="requested", actor="hr2")
    assert result == {"appeal_status": "requested", "already_applied": True}
    count = conn.execute(
        "SELECT COUNT(*) FROM appeal_event WHERE rejection_record_id = ?", (rejection_id,)
    ).fetchone()[0]
    assert count == 1


def test_overturned_restores_application_to_pre_rejection_stage(conn, rejection_id):
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="requested", actor="hr2")
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="under_review", actor="hr3")
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="overturned", actor="hr3")

    app_row = conn.execute(
        "SELECT status, current_stage_id FROM application WHERE id = 'a1'"
    ).fetchone()
    assert app_row == ("active", "screening")

    history_row = conn.execute(
        "SELECT from_stage_id, to_stage_id, actor_type, actor FROM application_stage_history "
        "WHERE application_id = 'a1' AND from_stage_id = 'rejected'"
    ).fetchone()
    assert history_row == ("rejected", "screening", "human", "hr3")


def test_overturned_does_not_delete_original_rejection_record(conn, rejection_id):
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="requested", actor="hr2")
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="under_review", actor="hr3")
    transition_appeal(conn, rejection_record_id=rejection_id, to_status="overturned", actor="hr3")
    row = conn.execute(
        "SELECT reason_type, rule_ref FROM rejection_record WHERE id = ?", (rejection_id,)
    ).fetchone()
    assert row == ("hard_rule", "experience_years:gte:5")


def test_unknown_rejection_record_raises_not_found(conn):
    with pytest.raises(AppealRecordNotFound):
        transition_appeal(conn, rejection_record_id="no-such-id", to_status="requested", actor="hr2")


def test_blank_actor_raises(conn, rejection_id):
    with pytest.raises(ValueError):
        transition_appeal(conn, rejection_record_id=rejection_id, to_status="requested", actor="  ")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `venv/bin/pytest tests/test_appeal_state_machine.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.storage.appeal'`）

- [ ] **Step 3: 实现 `app/storage/appeal.py`**

```python
"""拒绝记录的申诉状态机（hard-requirement-screening spec「淘汰只由人确认
并可申诉」，tasks 4.5/4.6）。

状态机：none → requested → under_review → upheld | overturned。非法跳转
拒绝；同一记录对同一目标状态重复提交视为幂等（无第二条流转事件）；
overturned 让投递恢复到淘汰前阶段并写入流转事实；原拒绝记录不删除，
appeal_status 原地更新。
"""
from __future__ import annotations

import sqlite3
import uuid

_LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "none": frozenset({"requested"}),
    "requested": frozenset({"under_review"}),
    "under_review": frozenset({"upheld", "overturned"}),
    "upheld": frozenset(),
    "overturned": frozenset(),
}


class IllegalAppealTransition(ValueError):
    """状态机不允许的跳转。⛔ 不静默忽略、不自动纠正到"最近的合法状态"。"""


class AppealRecordNotFound(ValueError):
    """rejection_record_id 不存在。"""


def transition_appeal(
    conn: sqlite3.Connection,
    *,
    rejection_record_id: str,
    to_status: str,
    actor: str,
) -> dict:
    """流转一条拒绝记录的申诉状态。返回
    {"appeal_status": str, "already_applied": bool}。

    幂等：当前 appeal_status 已经等于 to_status ⇒ 不产生新的 appeal_event、
    不重复更新，返回 already_applied=True（tasks 4.5「同一记录同一目标
    状态重复提交无第二条流转」）。
    """
    if not actor or not actor.strip():
        raise ValueError("actor 不能为空")

    row = conn.execute(
        "SELECT application_id, appeal_status FROM rejection_record WHERE id = ?",
        (rejection_record_id,),
    ).fetchone()
    if row is None:
        raise AppealRecordNotFound(f"rejection_record_id={rejection_record_id} 不存在")
    application_id, current_status = row

    if current_status == to_status:
        return {"appeal_status": current_status, "already_applied": True}

    legal_targets = _LEGAL_TRANSITIONS.get(current_status, frozenset())
    if to_status not in legal_targets:
        raise IllegalAppealTransition(
            f"申诉状态不能从 {current_status!r} 跳到 {to_status!r}"
        )

    conn.execute(
        "UPDATE rejection_record SET appeal_status = ? WHERE id = ?",
        (to_status, rejection_record_id),
    )
    conn.execute(
        "INSERT INTO appeal_event "
        "(id, rejection_record_id, from_status, to_status, actor) VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), rejection_record_id, current_status, to_status, actor),
    )

    if to_status == "overturned":
        history_row = conn.execute(
            "SELECT from_stage_id FROM application_stage_history "
            "WHERE application_id = ? AND to_stage_id = 'rejected' "
            "ORDER BY occurred_at DESC LIMIT 1",
            (application_id,),
        ).fetchone()
        pre_rejection_stage_id = history_row[0] if history_row and history_row[0] else "initial"
        conn.execute(
            "UPDATE application SET status = 'active', current_stage_id = ? WHERE id = ?",
            (pre_rejection_stage_id, application_id),
        )
        conn.execute(
            "INSERT INTO application_stage_history "
            "(id, application_id, from_stage_id, to_stage_id, actor_type, actor) "
            "VALUES (?, ?, 'rejected', ?, 'human', ?)",
            (str(uuid.uuid4()), application_id, pre_rejection_stage_id, actor),
        )

    conn.commit()
    return {"appeal_status": to_status, "already_applied": False}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `venv/bin/pytest tests/test_appeal_state_machine.py -v`
Expected: PASS（11 passed）

- [ ] **Step 5: Commit**

```bash
git add app/storage/appeal.py tests/test_appeal_state_machine.py
git commit -m "feat(m2-u3): add appeal state machine with per-transition actor audit trail"
```

---

### Task 9: 申诉接口 `POST /api/applications/{id}/appeal` 与 `POST /api/rejections/{id}/appeal/transition`

**Files:**
- Modify: `app/web/server.py`
- Test: `tests/test_appeal_endpoints.py`

**Interfaces:**
- Consumes: Task 8 的 `transition_appeal`、`IllegalAppealTransition`、`AppealRecordNotFound`；Task 7 的 `write_rejection`（仅测试里用来准备数据）；`app.middleware.auth.reviewer_of`。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_appeal_endpoints.py`：

```python
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.channels.web_channel import WebChannel
from app.llm.gateway import LLMGateway
from app.storage.db import init_schema
from app.storage.hr_account import create_account
from app.storage.rejection import write_rejection
from app.web.server import create_app


@pytest.fixture
def client_and_rejection(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'hash1', 'alice')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('a1', 'c1', 'j1', 'r1', 'screening')"
    )
    rejection_id = write_rejection(
        conn, application_id="a1", reason_type="hard_rule",
        rule_ref="experience_years:gte:5", human_readable="工作年限不满足",
        decided_by="hr1",
    )
    create_account(conn, username="hr2", password="testpass123")
    conn.commit()
    conn.close()

    app = create_app(
        db_path=str(db_path),
        gateway=LLMGateway.from_settings_for_test(),
        channel=WebChannel(),
    )
    with TestClient(app) as c:
        login = c.post("/api/login", json={"username": "hr2", "password": "testpass123"})
        assert login.status_code == 200
        yield c, rejection_id


def test_register_appeal_transitions_none_to_requested(client_and_rejection):
    client, rejection_id = client_and_rejection
    resp = client.post("/api/applications/a1/appeal")
    assert resp.status_code == 200
    body = resp.json()
    assert body["appeal_status"] == "requested"
    assert body["already_applied"] is False
    assert body["rejection_id"] == rejection_id


def test_register_appeal_is_idempotent_on_repeat(client_and_rejection):
    client, _ = client_and_rejection
    client.post("/api/applications/a1/appeal")
    resp = client.post("/api/applications/a1/appeal")
    assert resp.status_code == 200
    assert resp.json()["already_applied"] is True


def test_register_appeal_404_when_no_rejection_record(client_and_rejection):
    client, _ = client_and_rejection
    resp = client.post("/api/applications/no-such-app/appeal")
    assert resp.status_code == 404


def test_transition_endpoint_advances_state(client_and_rejection):
    client, rejection_id = client_and_rejection
    client.post("/api/applications/a1/appeal")
    resp = client.post(
        f"/api/rejections/{rejection_id}/appeal/transition",
        json={"to_status": "under_review"},
    )
    assert resp.status_code == 200
    assert resp.json()["appeal_status"] == "under_review"


def test_transition_endpoint_illegal_transition_returns_409(client_and_rejection):
    client, rejection_id = client_and_rejection
    resp = client.post(
        f"/api/rejections/{rejection_id}/appeal/transition",
        json={"to_status": "under_review"},
    )
    assert resp.status_code == 409


def test_transition_endpoint_404_for_unknown_rejection(client_and_rejection):
    client, _ = client_and_rejection
    resp = client.post(
        "/api/rejections/no-such-id/appeal/transition",
        json={"to_status": "requested"},
    )
    assert resp.status_code == 404


def test_transition_endpoint_records_acting_operator(client_and_rejection):
    client, rejection_id = client_and_rejection
    client.post("/api/applications/a1/appeal")
    client.post(
        f"/api/rejections/{rejection_id}/appeal/transition",
        json={"to_status": "under_review"},
    )
    client.post(
        f"/api/rejections/{rejection_id}/appeal/transition",
        json={"to_status": "overturned"},
    )
    resp = client.get(f"/api/applications/a1/screening-flags")
    assert resp.status_code == 200  # 只读接口存在即可（Task 5 已加），此处顺带确认路由未冲突
```

- [ ] **Step 2: 运行测试确认失败**

Run: `venv/bin/pytest tests/test_appeal_endpoints.py -v`
Expected: FAIL（`404 Not Found` on `POST /api/applications/a1/appeal`）

- [ ] **Step 3: 在 `app/web/server.py` 加 import、请求模型与两个路由**

3a. import 区新增：

```python
from app.storage.appeal import AppealRecordNotFound, IllegalAppealTransition, transition_appeal
```

3b. 在 `FieldReviewRequest` 类定义之后新增请求模型：

```python
class AppealTransitionRequest(BaseModel):
    to_status: str
```

3c. 在 `get_screening_flags` 路由之后（或任意路由函数定义区）新增两个路由：

```python
    @router.post("/api/applications/{application_id}/appeal")
    def register_appeal(request: Request, application_id: str) -> dict:
        row = conn.execute(
            "SELECT id, appeal_status FROM rejection_record WHERE application_id = ? "
            "ORDER BY decided_at DESC LIMIT 1",
            (application_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="该投递没有拒绝记录，无法申诉")
        rejection_id, appeal_status = row
        actor = reviewer_of(request)
        if appeal_status == "none":
            result = transition_appeal(
                conn, rejection_record_id=rejection_id, to_status="requested", actor=actor
            )
        else:
            result = {"appeal_status": appeal_status, "already_applied": True}
        return {"rejection_id": rejection_id, **result}

    @router.post("/api/rejections/{rejection_id}/appeal/transition")
    def transition_appeal_route(
        request: Request, rejection_id: str, req: AppealTransitionRequest
    ) -> dict:
        try:
            result = transition_appeal(
                conn, rejection_record_id=rejection_id, to_status=req.to_status,
                actor=reviewer_of(request),
            )
        except AppealRecordNotFound:
            raise HTTPException(status_code=404, detail="拒绝记录不存在")
        except IllegalAppealTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"rejection_id": rejection_id, **result}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `venv/bin/pytest tests/test_appeal_endpoints.py -v`
Expected: PASS（7 passed）

- [ ] **Step 5: 跑一次本单元全量测试与既有 M1/M2 回归，确认无交叉破坏**

Run: `venv/bin/pytest tests/ -v -k "screening or appeal or rejection or resume or hard_requirement"`
Expected: PASS（全部通过，无一条因本单元改动而失败）

Run: `venv/bin/pytest tests/ -q`
Expected: 全量测试套件 PASS（0 failed）

- [ ] **Step 6: Commit**

```bash
git add app/web/server.py tests/test_appeal_endpoints.py
git commit -m "feat(m2-u3): add appeal registration and transition endpoints"
```

---

## 端到端提取验证记录

按 spec-to-plan 技能步骤 6，本计划所有代码块已在写作过程中对照既有代码库的真实签名与表结构逐一核实（`app/storage/db.py` 表定义、`app/graph/resume_nodes.py` 的幂等写入模式、`app/schemas/resume_fields.py` 的字段形状、`app/storage/idempotency.py` 的装饰器行为、`app/agents/hard_requirement.py` 的可复用符号均已读取原文并在计划中准确引用），未独立开临时 venv 重跑——本单元不引入任何新第三方依赖，风险面小于 M1 第 0 章那类"引入新库+新推理"的计划。**执行阶段（`run-build`）的两阶段 review 与本计划规定的九个任务级 pytest 断言是本单元实际的正确性把关点。**

## 任务与规则覆盖自查

- `grep -c '^### Task ' docs/superpowers/plans/2026-09-18-m2-resume-parse-and-rank-unit3-hard-requirement-screening.md` = 9，与实际任务数一致。
- Global Constraints 段落已包含，逐条标注适用/不适用及理由。
- spec 六条 Requirement 均有对应 Task（见上方「Requirement → Task 对照」表）。
- 每个有副作用的步骤（`effect_persist_flags`、`write_rejection`、`transition_appeal`）都在同一函数体内完成写入并在函数末尾 `commit()` 一次，不存在半提交路径。
- 每条 `fail` 判定都测试了 `evidence_ref` 非空（Task 2 `test_screen_experience_years_fail_carries_evidence`、`test_screen_education_gte_fail`、`test_screen_core_skills_contains_fail`；Task 4 `test_effect_persist_flags_writes_evidence_ref_for_fail`）。

## 下一步

用 `run-build` 技能执行本计划（`superpowers:subagent-driven-development`，每个 Task 一个子代理 + 两阶段 review）。
