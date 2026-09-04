# 硬门槛规则草案 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让岗位画像在被业务经理确认的那一刻，同时产出一份可解释、可单独启停的硬门槛规则草案并落库，且主观描述在结构上进不去这份草案。

**Architecture:** 提取是一个**确定性纯函数**（`app/agents/hard_requirement.py`，L3 层，⛔ 不调模型），输入是画像 dict、输出是 `list[HardRequirement]`；落库发生在既有的 `effect_confirm_profile` 节点体内，与「画像冻结为 approved」「`human_review` 留痕」落在**同一个事务**里，由 `idempotent_effect` 装饰器统一提交一次。新表 `hard_requirement` 只**存**规则、不**执行**规则——本单元不引入任何自动淘汰逻辑。

**Tech Stack:** Python 3.11+ · SQLite（`app/storage/db.py`，M2 迁 Postgres）· LangGraph ≥ 1.0.10 · pytest

**范围对应**：`openspec/changes/m1-job-profile-intake/tasks.md` 的 **1.2b**（建表 `hard_requirement`）、**5.8**（硬门槛规则提取：字段/运算符/值/是否阻断 + 一句人类可读说明）、**5.9**（主观描述拦截断言）。
**Spec 输入**：`openspec/changes/m1-job-profile-intake/specs/job-profile-intake/spec.md` 的 `### Requirement: 硬门槛规则草案提取`（含两个 Scenario）。

---

## Global Constraints

以下条目从 `CLAUDE.md` 的「工程铁律」「合规红线」两节**逐字复制**。每个 Task 的验收隐含包含本节全部内容，reviewer 以此为注意力透镜。

**工程铁律 1（逐字）：**
> **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
> **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
> *为什么*：业务写失败而幂等记录成功 → 系统判定"已执行"→ 永不重试。**幂等本是防重复的保护，拆开事务后变成永久丢失的保证。**

**工程铁律 2（逐字）：**
> **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。

**工程铁律 7（逐字）：**
> **`langgraph >= 1.0.10`**（GHSA-g48c-2wqr-h844）。

**合规红线（逐字）：**
> **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。审计断言：`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。

**合规红线（逐字）：**
> 主观描述（"沟通能力强"）不得进入硬门槛规则，只能作为软技能关键词。

**本单元附加的硬约束（违反即返工）：**

1. **提取必须是确定性纯函数，⛔ 不调模型做二次判断。** 同一份画像重跑两次必须逐条相同、顺序相同。理由：草案要能被人复核、被回放对比；一次模型调用就把它变成不可复算的东西，且会绕开「AI 只做排序推荐」的边界。
2. **`hard_requirement` 只存规则、不执行规则。** 本单元⛔ 不新增任何读这张表做判定、打分或淘汰的代码路径。`blocking` 列是**标注**，不是执行开关。
3. **新表走 `CREATE TABLE IF NOT EXISTS`，⛔ 不进 `_ADDED_COLUMNS`。** 加列路径只服务"老库缺列"这一种情况；把新表塞进去会让 `apply_column_migrations` 对着一张不存在的表执行 `ALTER TABLE`（既有守卫：`tests/test_db_migration.py::test_audit_tables_never_enter_the_add_column_path`）。
4. **列集合固定为**：`job_id` / `profile_version` / `field` / `operator` / `value` / `blocking` / `human_readable` / `created_at`。⛔ 不加代理主键 `id`。
5. **落库与画像冻结同一事务**：`_record_hard_requirements()` ⛔ 不 `commit`、⛔ 不开事务、⛔ 不新增 `effect_*` 节点。多一个节点就多一个幂等键，而两个幂等键意味着"画像已 approved、规则草案却缺席"是一个可达状态。
6. **并行同伴（本计划⛔ 不碰这些路径）**：断言泳道正在改 `app/audit/assertions.py`；Web 泳道正在改 `app/web/`。本单元**不改** `app/audit/`、`app/web/`、`app/outbound/`，**不改** `effect_deliver_message`、**不改** `app/storage/idempotency.py` 的 `idempotent_effect`。
7. **`human_readable` 是确定性模板拼接的产物，不是模型生成内容**，因此不触发《AI 生成合成内容标识办法》的标识义务。⛔ 不要在其中调用 LLM 润色。

---

### Task 1: `hard_requirement` 建表（tasks 1.2b）

**Files:**
- Modify: `app/storage/db.py`（在 `SCHEMA` 字符串末尾、`human_review` 段之后追加；⛔ 不动 `_ADDED_COLUMNS`）
- Test: `tests/test_hard_requirement_schema.py`（新建）

**Interfaces:**
- Consumes: `app.storage.db.get_connection(db_path) -> sqlite3.Connection`、`app.storage.db.init_schema(conn) -> None`、`app.storage.db._ADDED_COLUMNS`
- Produces: SQLite 表 `hard_requirement`，列固定为 `job_id TEXT` / `profile_version INTEGER` / `field TEXT` / `operator TEXT` / `value TEXT` / `blocking INTEGER` / `human_readable TEXT` / `created_at TEXT`；复合主键 `(job_id, profile_version, field, operator, value)`；`operator` 的 CHECK 取值集合 = `('gte','education_gte','contains','equals','is_true')`。Task 2 的 `OPERATORS` 常量与这组 CHECK 取值**逐字同源**。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_hard_requirement_schema.py`：

```python
"""`hard_requirement` 建表守卫（tasks 1.2b）。

这张表是硬门槛规则草案的载体：spec「硬门槛规则草案提取」要求每条规则包含
字段名、比较运算符、比较值、是否阻断，并附一句人类可读说明。

表上的 CHECK 不是装饰：
- `operator` 的取值集合与 app/agents/hard_requirement.OPERATORS 逐字同源，
  绕过应用层直接 INSERT 一个野运算符同样被拒；
- `human_readable` 非空是 spec「每条规则附一句人类可读的说明（用于将来向
  候选人解释淘汰原因）」在存储层的落点——说明为空的规则等于没有说明，而
  将来要拿它向候选人解释淘汰原因。

⛔ 本表只**存**规则、不**执行**规则（合规红线：AI 只做排序推荐，不做自动淘汰）。
"""

import sqlite3

import pytest

from app.storage.db import _ADDED_COLUMNS, get_connection, init_schema

_EXPECTED_COLUMNS = {
    "job_id",
    "profile_version",
    "field",
    "operator",
    "value",
    "blocking",
    "human_readable",
    "created_at",
}


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "hr.db"))
    init_schema(c)
    yield c
    c.close()


def _columns(c: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in c.execute(f"PRAGMA table_info({table})")}


def _insert(c: sqlite3.Connection, **overrides):
    row = {
        "job_id": "job-1",
        "profile_version": 1,
        "field": "education_requirement",
        "operator": "education_gte",
        "value": "本科",
        "blocking": 1,
        "human_readable": "学历要求：本科及以上（不满足则不通过硬门槛）",
    }
    row.update(overrides)
    c.execute(
        "INSERT INTO hard_requirement "
        "(job_id, profile_version, field, operator, value, blocking, human_readable, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (
            row["job_id"],
            row["profile_version"],
            row["field"],
            row["operator"],
            row["value"],
            row["blocking"],
            row["human_readable"],
        ),
    )


def test_table_exists_with_exactly_the_agreed_columns(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "hard_requirement" in tables
    assert _columns(conn, "hard_requirement") == _EXPECTED_COLUMNS


def test_new_table_never_enters_the_add_column_path(conn):
    """新表不走 _ADDED_COLUMNS：加列路径只服务"老库缺列"这一种情况。"""
    assert "hard_requirement" not in {table for table, _c, _d in _ADDED_COLUMNS}


def test_init_schema_is_idempotent(tmp_path):
    """重跑三次不报错——CREATE TABLE 与主键都必须带 IF NOT EXISTS 的幂等性。"""
    c = get_connection(str(tmp_path / "again.db"))
    init_schema(c)
    init_schema(c)
    init_schema(c)
    tables = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "hard_requirement" in tables
    c.close()


def test_unknown_operator_is_rejected_by_the_database(conn):
    """野运算符绕过应用层直接 INSERT 同样被拒。"""
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, operator="regex_match")


def test_blank_human_readable_is_rejected_by_the_database(conn):
    """空白说明 = 没有说明。纯制表符也要被拒（SQLite 单参 trim() 只剥空格）。"""
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, human_readable="   ")
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, human_readable="\t\n")


def test_blocking_only_accepts_zero_or_one(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, blocking=2)


def test_same_rule_cannot_be_written_twice_for_one_profile_version(conn):
    """复合主键 = 同一版画像内规则去重的第二道防线（第一道是 effect_log）。"""
    _insert(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn)


def test_different_profile_versions_keep_their_own_rules(conn):
    """画像改动走新版本（design 决策四），两版的规则各自成立、互不覆盖。"""
    _insert(conn, profile_version=1, value="本科")
    _insert(conn, profile_version=2, value="硕士")
    rows = conn.execute(
        "SELECT profile_version, value FROM hard_requirement "
        "WHERE job_id = 'job-1' ORDER BY profile_version"
    ).fetchall()
    assert [tuple(r) for r in rows] == [(1, "本科"), (2, "硕士")]
```

- [ ] **Step 2: 跑测试，确认它失败**

Run: `pytest tests/test_hard_requirement_schema.py -v`
Expected: FAIL — `sqlite3.OperationalError: no such table: hard_requirement`（`test_new_table_never_enters_the_add_column_path` 会先通过，其余全红）

- [ ] **Step 3: 追加建表 DDL**

在 `app/storage/db.py` 的 `SCHEMA` 字符串里，`CREATE UNIQUE INDEX IF NOT EXISTS idx_human_review_decision ...` 那条**之后**、闭合的 `"""` 之前，追加：

```sql
-- ─────────────────────────────────────────────────────────────────────────
-- 硬门槛规则草案（m1-job-profile-intake tasks 1.2b / 5.8 / 5.9）。
-- 新表，走 CREATE TABLE IF NOT EXISTS，**不进 _ADDED_COLUMNS**：加列路径只
-- 服务"老库缺列"这一种情况，新表不需要它。.51 上 data/demo.db 的既有 job 与
-- 既有表一行不改，无数据迁移。
--
-- ⛔ **本表只存规则、不执行规则。** 合规红线「AI 只做排序推荐，不做自动淘汰」
-- 意味着这里没有任何一行会自己把候选人筛掉；blocking 是给人看的标注，不是
-- 执行开关。本变更包内⛔ 不得出现读本表做判定/打分/淘汰的代码路径。
--
-- ⛔ job_id 上刻意不加外键。与 human_review.job_id、effect_log.thread_id 同一
-- 形态：规则草案按 thread 记事实，把它的可写性绑在业务表上，"草案写不进去"
-- 就会变成"画像确认整个失败"。
--
-- ⛔ 不设代理主键 id。天然键就是规则本身——同一版画像里"同字段同运算符同值"
-- 出现两次就是 bug，而不是两条合法数据。复合主键同时充当去重的第二道防线
-- （第一道是 effect_log 里 {job_id}:effect_confirm_profile:{version} 那把键）。
--
-- operator 的 CHECK 取值与 app/agents/hard_requirement.py 的 OPERATORS 常量
-- 逐字同源。⛔ 改一处必须同步改另一处，否则新运算符会在业务经理点确认的那
-- 一刻炸成 IntegrityError。
--
-- human_readable 的 CHECK 是 spec「每条规则附一句人类可读的说明（用于将来向
-- 候选人解释淘汰原因）」在存储层的落点：说明为空的规则等于没有说明。trim 的
-- 第二参数显式列出空格/制表/换行/回车——SQLite 的单参 trim() 只剥空格（与
-- criterion_score.evidence_ref、human_review.reviewer 的 CHECK 同一理由）。
CREATE TABLE IF NOT EXISTS hard_requirement (
    job_id TEXT NOT NULL,
    profile_version INTEGER NOT NULL,
    field TEXT NOT NULL,
    operator TEXT NOT NULL CHECK (
        operator IN ('gte', 'education_gte', 'contains', 'equals', 'is_true')
    ),
    value TEXT NOT NULL,
    blocking INTEGER NOT NULL CHECK (blocking IN (0, 1)),
    human_readable TEXT NOT NULL CHECK (
        human_readable IS NOT NULL
        AND trim(human_readable, ' ' || char(9) || char(10) || char(13)) != ''
    ),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (job_id, profile_version, field, operator, value)
);
```

⛔ 不要在 `_ADDED_COLUMNS` 里加任何东西。

- [ ] **Step 4: 跑测试，确认通过**

Run: `pytest tests/test_hard_requirement_schema.py -v`
Expected: PASS（8 passed）

- [ ] **Step 5: 跑既有建表/迁移测试，确认没打破别人**

Run: `pytest tests/test_db.py tests/test_db_migration.py tests/test_db_audit_schema.py tests/test_human_review_schema.py -q`
Expected: PASS，0 failed（既有断言全部用 `<=` 子集比较，多一张表不影响）

- [ ] **Step 6: 提交**

```bash
git add app/storage/db.py tests/test_hard_requirement_schema.py
git commit -m "feat(storage): 建表 hard_requirement（tasks 1.2b）"
```

---

### Task 2: 硬门槛规则草案提取纯函数（tasks 5.8）

**Files:**
- Create: `app/agents/hard_requirement.py`
- Test: `tests/test_hard_requirement.py`（新建）

**Interfaces:**
- Consumes: Task 1 建的表（本 Task 只产出内存对象，不写库）；`app.schemas.job_profile.JobProfile` 的字段名与 `SkillItem.required` / `SopProject.is_mass_production` 语义
- Produces（Task 3、Task 4 依赖这些确切名字）：
  - `HardRequirement` — `@dataclass(frozen=True)`，字段 `field: str`、`operator: str`、`value: str`、`blocking: bool`、`human_readable: str`
  - `OPERATORS: tuple[str, ...]` — `("gte", "education_gte", "contains", "equals", "is_true")`
  - `EXTRACTABLE_FIELDS: tuple[str, ...]` — 可提取字段白名单（决定输出顺序）
  - `extract_hard_requirements(profile: dict) -> list[HardRequirement]` — 纯函数

- [ ] **Step 1: 写失败测试**

新建 `tests/test_hard_requirement.py`：

```python
"""硬门槛规则草案提取（tasks 5.8）。

spec「硬门槛规则草案提取」/ Scenario「提取硬门槛」：
    画像产出完成 → 每条规则包含字段名、比较运算符、比较值、是否阻断，
    并附一句人类可读的说明。

⛔ 提取是**确定性纯函数**，不调模型：草案要能被人复核、被回放对比，
一次模型调用就把它变成不可复算的东西。本文件的
test_extraction_is_deterministic 是这条约束的机器判据。
"""

import copy

from app.agents.hard_requirement import (
    EXTRACTABLE_FIELDS,
    OPERATORS,
    HardRequirement,
    extract_hard_requirements,
)


def _profile(**overrides) -> dict:
    profile = {
        "job_title": "嵌入式软件工程师",
        "department": "电子研发部",
        "headcount": 2,
        "education_requirement": "本科及以上",
        "experience_years": "3-5年",
        "core_skills": [
            {"name": "C 语言", "required": True},
            {"name": "Python 脚本", "required": False},
        ],
        "project_experience_requirement": "有 ECU 量产项目经历",
        "soft_skill_keywords": ["沟通能力强", "有责任心"],
        "autosar_experience": ["CP"],
        "functional_safety": "ASIL-B",
        "mcu_family": ["英飞凌 Aurix"],
        "diag_stack": ["UDS（ISO 14229）"],
        "sop_projects": [
            {
                "vehicle_model": "A 车型",
                "sop_date": "2024-06",
                "role": "软件负责人",
                "is_mass_production": True,
            }
        ],
        "toolchain": ["Vector（CANoe/CANape）"],
        "unspecified_fields": [],
    }
    profile.update(overrides)
    return profile


def _by_field(rules, field):
    return [r for r in rules if r.field == field]


def test_every_rule_carries_the_four_required_parts_plus_a_sentence():
    """spec 的四件套 + 一句人类可读说明，一条都不能缺。"""
    rules = extract_hard_requirements(_profile())

    assert rules, "完整画像必须提取出至少一条规则"
    for rule in rules:
        assert isinstance(rule, HardRequirement)
        assert rule.field in EXTRACTABLE_FIELDS
        assert rule.operator in OPERATORS
        assert rule.value.strip() != ""
        assert isinstance(rule.blocking, bool)
        assert rule.human_readable.strip() != ""


def test_education_lower_bound():
    rules = _by_field(extract_hard_requirements(_profile()), "education_requirement")
    assert len(rules) == 1
    assert (rules[0].operator, rules[0].value, rules[0].blocking) == (
        "education_gte",
        "本科",
        True,
    )


def test_education_takes_the_lowest_level_mentioned():
    """"本科及以上，硕士优先" 的硬门槛是本科，不是硕士。

    取**最低**被提到的档位是刻意的保守方向：门槛取高了会把合格的人挡在外面，
    而这条规则将来要用来向候选人解释淘汰原因。
    """
    rules = _by_field(
        extract_hard_requirements(_profile(education_requirement="本科及以上，硕士优先")),
        "education_requirement",
    )
    assert [r.value for r in rules] == ["本科"]


def test_education_without_a_recognizable_level_yields_no_rule():
    for text in ("不限", "学历不限", "未指定", ""):
        assert _by_field(
            extract_hard_requirements(_profile(education_requirement=text)),
            "education_requirement",
        ) == []


def test_experience_lower_bound():
    rules = _by_field(extract_hard_requirements(_profile()), "experience_years")
    assert len(rules) == 1
    assert (rules[0].operator, rules[0].value, rules[0].blocking) == ("gte", "3", True)


def test_experience_upper_bound_is_not_a_hard_gate():
    """"3 年以下" 是上限，不是下限。⛔ 不得把它当成 gte 3 提取出来。"""
    for text in ("3 年以下", "5年以内"):
        assert _by_field(
            extract_hard_requirements(_profile(experience_years=text)),
            "experience_years",
        ) == []


def test_experience_without_a_number_yields_no_rule():
    for text in ("不限", "应届亦可", "未指定", ""):
        assert _by_field(
            extract_hard_requirements(_profile(experience_years=text)), "experience_years"
        ) == []


def test_required_skill_blocks_and_optional_skill_does_not():
    rules = _by_field(extract_hard_requirements(_profile()), "core_skills")
    assert [(r.operator, r.value, r.blocking) for r in rules] == [
        ("contains", "C 语言", True),
        ("contains", "Python 脚本", False),
    ]


def test_functional_safety_none_yields_no_rule():
    assert _by_field(
        extract_hard_requirements(_profile(functional_safety="无")), "functional_safety"
    ) == []


def test_functional_safety_level_blocks():
    rules = _by_field(extract_hard_requirements(_profile()), "functional_safety")
    assert [(r.operator, r.value, r.blocking) for r in rules] == [
        ("equals", "ASIL-B", True)
    ]


def test_autosar_none_yields_no_rule():
    assert _by_field(
        extract_hard_requirements(_profile(autosar_experience=["无"])),
        "autosar_experience",
    ) == []


def test_transferable_platform_fields_are_not_blocking():
    """MCU 平台 / 诊断栈 / 工具链是可迁移经验，提成规则但不阻断。"""
    rules = extract_hard_requirements(_profile())
    for field in ("mcu_family", "diag_stack", "toolchain"):
        found = _by_field(rules, field)
        assert found, f"{field} 应产出一条规则"
        assert all(r.blocking is False for r in found)
        assert all(r.operator == "contains" for r in found)


def test_mass_production_sop_yields_one_blocking_rule():
    rules = _by_field(extract_hard_requirements(_profile()), "sop_projects")
    assert [(r.operator, r.value, r.blocking) for r in rules] == [
        ("is_true", "is_mass_production", True)
    ]


def test_non_mass_production_sop_yields_no_rule():
    profile = _profile()
    profile["sop_projects"][0]["is_mass_production"] = False
    assert _by_field(extract_hard_requirements(profile), "sop_projects") == []


def test_unspecified_fields_never_become_gates():
    """追问超限用"未指定"填充的字段⛔ 不得变成硬门槛（spec「追问达到上限」）。"""
    profile = _profile(unspecified_fields=["education_requirement", "experience_years"])
    rules = extract_hard_requirements(profile)
    assert _by_field(rules, "education_requirement") == []
    assert _by_field(rules, "experience_years") == []


def test_non_gateable_fields_are_structurally_excluded():
    """岗位名称/部门/编制数/项目经验自由文本都不是候选人可自动判定的门槛。"""
    for field in (
        "job_title",
        "department",
        "headcount",
        "project_experience_requirement",
    ):
        assert field not in EXTRACTABLE_FIELDS


def test_rules_are_ordered_by_the_field_whitelist():
    rules = extract_hard_requirements(_profile())
    positions = [EXTRACTABLE_FIELDS.index(r.field) for r in rules]
    assert positions == sorted(positions)


def test_extraction_is_deterministic():
    """同一份画像重跑必须逐条相同、顺序相同——⛔ 不调模型的机器判据。"""
    profile = _profile()
    assert extract_hard_requirements(profile) == extract_hard_requirements(profile)


def test_extraction_does_not_mutate_the_profile():
    """纯函数（工程铁律 2）：入参画像一个字节都不许改。"""
    profile = _profile()
    before = copy.deepcopy(profile)
    extract_hard_requirements(profile)
    assert profile == before


def test_duplicate_skills_collapse_into_one_rule():
    """同名技能出现两次只产出一条——复合主键容不下第二条，重复即 IntegrityError。"""
    profile = _profile(
        core_skills=[
            {"name": "C 语言", "required": True},
            {"name": "C 语言", "required": True},
        ]
    )
    assert len(_by_field(extract_hard_requirements(profile), "core_skills")) == 1


def test_empty_and_malformed_profile_never_raises():
    """画像形状不可信时也不许抛——抛了就是业务经理点确认的那一刻炸成 500。"""
    assert extract_hard_requirements({}) == []
    assert extract_hard_requirements({"core_skills": "不是列表"}) == []
    assert extract_hard_requirements({"core_skills": [None, 42, {"required": True}]}) == []
```

- [ ] **Step 2: 跑测试，确认它失败**

Run: `pytest tests/test_hard_requirement.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agents.hard_requirement'`

- [ ] **Step 3: 写实现**

新建 `app/agents/hard_requirement.py`：

```python
"""从岗位画像里提取硬门槛规则草案（m1-job-profile-intake tasks 5.8 / 5.9）。

spec「硬门槛规则草案提取」：每条规则包含字段名、比较运算符、比较值、是否阻断，
并附一句人类可读的说明（用于将来向候选人解释淘汰原因）。

⛔ **本模块是 L3 纯函数，不调模型、不写库、不改入参**（工程铁律 2）。提取必须
确定性：同一份画像重跑两次逐条相同、顺序相同。理由不是洁癖——草案要能被人复核、
被回放对比，一次模型调用就把它变成不可复算的东西，而且会绕开「AI 只做排序推荐、
不做自动淘汰」的边界。

⛔ **本模块只产出规则，不执行规则。** 这里没有任何一行会把候选人筛掉；`blocking`
是给人看的标注，不是执行开关（合规红线）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 运算符封闭集合。⛔ 与 app/storage/db.py 的 hard_requirement.operator CHECK
# 逐字同源，改一处必须同步改另一处——不同步的后果是业务经理点确认的那一刻
# 炸成 IntegrityError。
OPERATORS: tuple[str, ...] = ("gte", "education_gte", "contains", "equals", "is_true")

# 可提取字段白名单，**同时决定输出顺序**（确定性要求）。
#
# ⛔ soft_skill_keywords 刻意不在此列：合规红线「主观描述不得进入硬门槛规则，
# 只能作为软技能关键词」在这里是**结构性**成立的，不靠下面的词表兜底。
# ⛔ job_title / department / headcount 不是候选人可判定的条件；
# ⛔ project_experience_requirement 是自由文本，自动判定必然要靠语义理解，
#    那就回到"调模型"上去了——排除，宁可少一条规则。
EXTRACTABLE_FIELDS: tuple[str, ...] = (
    "education_requirement",
    "experience_years",
    "core_skills",
    "functional_safety",
    "autosar_experience",
    "mcu_family",
    "diag_stack",
    "toolchain",
    "sop_projects",
)

# 学历档位与别名。**按从低到高排列**，第一个命中的就是门槛。
# "本科及以上，硕士优先" → 本科，不是硕士：取最低档是刻意的保守方向，门槛取高
# 了会把合格的人挡在外面，而这条规则将来要用来向候选人解释淘汰原因。
_EDUCATION_LEVELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("大专", ("大专", "专科")),
    ("本科", ("本科", "学士")),
    ("硕士", ("硕士", "研究生")),
    ("博士", ("博士",)),
)

# "没有要求"的等价表述。命中即不产出规则。
_NO_REQUIREMENT_VALUES: frozenset[str] = frozenset(
    {"", "无", "无要求", "不限", "不限制", "未指定", "没有要求", "无特殊要求"}
)

# 可迁移经验字段：提成规则但不阻断（换个 MCU 平台族两周能上手，把它设成阻断
# 等于用工具品牌筛人）。
_NON_BLOCKING_LIST_FIELDS: tuple[str, ...] = ("mcu_family", "diag_stack", "toolchain")

_LIST_FIELD_SENTENCE: dict[str, str] = {
    "mcu_family": "MCU 平台经验：{value}（加分项，不阻断）",
    "diag_stack": "诊断/总线协议栈经验：{value}（加分项，不阻断）",
    "toolchain": "工具链使用经验：{value}（加分项，不阻断）",
}


@dataclass(frozen=True)
class HardRequirement:
    """一条硬门槛规则草案。

    `blocking` 是**标注**：True 表示"这一项不满足就不通过硬门槛"，False 表示
    "记录下来供筛选时加分参考"。⛔ 它不是执行开关——本变更包内没有任何代码读
    这个字段去淘汰候选人（合规红线：AI 只做排序推荐，不做自动淘汰）。

    `human_readable` 是确定性模板拼接的产物，不是模型生成内容，因此不触发
    《AI 生成合成内容标识办法》的标识义务。⛔ 不要在这里调 LLM 润色。
    """

    field: str
    operator: str
    value: str
    blocking: bool
    human_readable: str


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _is_no_requirement(text: str) -> bool:
    return text in _NO_REQUIREMENT_VALUES


def _education_floor(text: str) -> str | None:
    """学历要求自由文本 → 学历档位下限。识别不出来就返回 None（不产出规则）。"""
    for level, aliases in _EDUCATION_LEVELS:
        if any(alias in text for alias in aliases):
            return level
    return None


def _experience_floor(text: str) -> str | None:
    """年限要求自由文本 → 年限下限（字符串形式的整数）。

    "3-5年" → "3"；"5 年以上" → "5"；"3 年以下" → None。
    ⛔ 含"以下/以内"的是上限，绝不能当成下限——那会把一条"最多 3 年"的偏好
    翻译成"至少 3 年"的门槛，方向完全相反。
    """
    if "以下" in text or "以内" in text:
        return None
    match = re.search(r"\d+", text)
    return match.group(0) if match else None


def _iter_dicts(value) -> list[dict]:
    """从可能不可信的画像值里取出 dict 列表，形状不对就当空。"""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _iter_strings(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for text in (_text(item) for item in value) if text]


def extract_hard_requirements(profile: dict) -> list[HardRequirement]:
    """画像 dict → 硬门槛规则草案列表。纯函数，⛔ 不改入参、不调模型、不写库。

    ⚠️ 入参可能是 LLM 自由生成的裸 dict（形状不可信）。**任何形状都不许抛
    异常**——抛了就是业务经理点确认的那一刻炸成 500。形状不对的部分静默跳过，
    保守方向是"少一条规则"而不是"整个确认失败"。
    """
    if not isinstance(profile, dict):
        return []

    unspecified = set(_iter_strings(profile.get("unspecified_fields")))
    rules: list[HardRequirement] = []

    for field in EXTRACTABLE_FIELDS:
        # 追问超限用"未指定"填充的字段⛔ 不得变成门槛（spec「追问达到上限」）。
        if field in unspecified:
            continue
        rules.extend(_extract_field(field, profile.get(field)))

    return _dedupe(rules)


def _extract_field(field: str, value) -> list[HardRequirement]:
    if field == "education_requirement":
        return _extract_education(value)
    if field == "experience_years":
        return _extract_experience(value)
    if field == "core_skills":
        return _extract_core_skills(value)
    if field == "functional_safety":
        return _extract_functional_safety(value)
    if field == "autosar_experience":
        return _extract_autosar(value)
    if field in _NON_BLOCKING_LIST_FIELDS:
        return _extract_non_blocking_list(field, value)
    if field == "sop_projects":
        return _extract_sop_projects(value)
    return []


def _extract_education(value) -> list[HardRequirement]:
    text = _text(value)
    if _is_no_requirement(text):
        return []
    level = _education_floor(text)
    if level is None:
        return []
    return [
        HardRequirement(
            field="education_requirement",
            operator="education_gte",
            value=level,
            blocking=True,
            human_readable=f"学历要求：{level}及以上（不满足则不通过硬门槛）",
        )
    ]


def _extract_experience(value) -> list[HardRequirement]:
    text = _text(value)
    if _is_no_requirement(text):
        return []
    floor = _experience_floor(text)
    if floor is None:
        return []
    return [
        HardRequirement(
            field="experience_years",
            operator="gte",
            value=floor,
            blocking=True,
            human_readable=f"工作年限要求：{floor} 年及以上（不满足则不通过硬门槛）",
        )
    ]


def _extract_core_skills(value) -> list[HardRequirement]:
    rules: list[HardRequirement] = []
    for item in _iter_dicts(value):
        name = _text(item.get("name"))
        if not name or _is_no_requirement(name):
            continue
        required = bool(item.get("required"))
        sentence = (
            f"必会技能：{name}（不满足则不通过硬门槛）"
            if required
            else f"加分技能：{name}（加分项，不阻断）"
        )
        rules.append(
            HardRequirement(
                field="core_skills",
                operator="contains",
                value=name,
                blocking=required,
                human_readable=sentence,
            )
        )
    return rules


def _extract_functional_safety(value) -> list[HardRequirement]:
    text = _text(value)
    if _is_no_requirement(text):
        return []
    return [
        HardRequirement(
            field="functional_safety",
            operator="equals",
            value=text,
            blocking=True,
            human_readable=f"功能安全等级要求：{text}（不满足则不通过硬门槛）",
        )
    ]


def _extract_autosar(value) -> list[HardRequirement]:
    rules: list[HardRequirement] = []
    for layer in _iter_strings(value):
        if _is_no_requirement(layer):
            continue
        rules.append(
            HardRequirement(
                field="autosar_experience",
                operator="contains",
                value=layer,
                blocking=True,
                human_readable=f"需具备 AUTOSAR {layer} 开发经验（不满足则不通过硬门槛）",
            )
        )
    return rules


def _extract_non_blocking_list(field: str, value) -> list[HardRequirement]:
    rules: list[HardRequirement] = []
    for name in _iter_strings(value):
        if _is_no_requirement(name):
            continue
        rules.append(
            HardRequirement(
                field=field,
                operator="contains",
                value=name,
                blocking=False,
                human_readable=_LIST_FIELD_SENTENCE[field].format(value=name),
            )
        )
    return rules


def _extract_sop_projects(value) -> list[HardRequirement]:
    """量产（SOP）经历。多个项目只产出**一条**规则——"有没有量产经历"是一个
    布尔事实，逐个车型建规则会把车型型号变成筛人条件。"""
    if not any(item.get("is_mass_production") for item in _iter_dicts(value)):
        return []
    return [
        HardRequirement(
            field="sop_projects",
            operator="is_true",
            value="is_mass_production",
            blocking=True,
            human_readable="需具备量产（SOP）项目经历（不满足则不通过硬门槛）",
        )
    ]


def _dedupe(rules: list[HardRequirement]) -> list[HardRequirement]:
    """按天然键去重、保持首次出现的顺序。

    天然键 = (field, operator, value)，与 hard_requirement 的复合主键同粒度。
    ⛔ 不在这里"去重后取 blocking 更严的那条"——同名技能一必会一加分是画像
    本身的矛盾，静默挑一个会把矛盾藏起来；保留首次出现，让它在人复核草案时
    仍然看得见。
    """
    seen: set[tuple[str, str, str]] = set()
    unique: list[HardRequirement] = []
    for rule in rules:
        key = (rule.field, rule.operator, rule.value)
        if key in seen:
            continue
        seen.add(key)
        unique.append(rule)
    return unique
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `pytest tests/test_hard_requirement.py -v`
Expected: PASS（21 passed）

- [ ] **Step 5: 提交**

```bash
git add app/agents/hard_requirement.py tests/test_hard_requirement.py
git commit -m "feat(agents): 硬门槛规则草案提取纯函数（tasks 5.8）"
```

---

### Task 3: 主观描述拦截与反证断言（tasks 5.9）

**Files:**
- Modify: `app/agents/hard_requirement.py`（追加词表、异常、断言函数；并在 `_extract_core_skills` 与 `_extract_non_blocking_list` 里接入过滤）
- Modify: `tests/test_hard_requirement.py`（追加 5.9 的用例段）

**Interfaces:**
- Consumes: Task 2 的 `HardRequirement`、`extract_hard_requirements`
- Produces（Task 4 依赖这些确切名字）：
  - `SubjectiveRequirementError(ValueError)`
  - `SUBJECTIVE_TERMS: tuple[str, ...]`
  - `is_subjective(text: str) -> bool`
  - `assert_no_subjective_requirements(rules: list[HardRequirement]) -> None` — 命中即抛 `SubjectiveRequirementError`

**这条红线的两道防线**（缺一不可，reviewer 请分别确认）：
1. **结构性**：`soft_skill_keywords` 不在 `EXTRACTABLE_FIELDS` 里，主观关键词字段整体进不来（Task 2 已成立）。
2. **词表**：业务经理/模型把"沟通能力强"写进 `core_skills` 是真实会发生的——`SYSTEM_PROMPT` 只是提示词，不是行为约束。词表在提取时过滤掉它们，`assert_no_subjective_requirements()` 则是落库前的最后一道机器判据。

- [ ] **Step 1: 写失败测试**

在 `tests/test_hard_requirement.py` **末尾**追加。顶部 import 段同步补上 `SubjectiveRequirementError`、`assert_no_subjective_requirements`、`is_subjective` 三个名字，并加一行 `import pytest`（`copy` 在 Task 2 已经导入）。⛔ 不要顺手 import `SUBJECTIVE_TERMS` —— 下面这组用例没有一条直接用到它，导进来就是个死 import。

```python
# ── tasks 5.9：主观描述拦截 ──────────────────────────────────────────────
#
# 合规红线（逐字）：主观描述（"沟通能力强"）不得进入硬门槛规则，只能作为软技能
# 关键词。下面这组用例就是这条红线的机器判据——在此之前它"无处可断"（tasks 5.9
# 原话），只有一个断言 prompt 文本含某几个关键词的测试，那验的是提示词写了什么，
# 不是行为。


def test_soft_skill_keywords_field_is_structurally_excluded():
    """第一道防线：整个字段进不来，不靠词表兜底。"""
    assert "soft_skill_keywords" not in EXTRACTABLE_FIELDS


def test_subjective_description_in_core_skills_is_filtered_out():
    """第二道防线：模型把主观描述塞进 core_skills 是真实会发生的。"""
    profile = _profile(
        core_skills=[
            {"name": "沟通能力强", "required": True},
            {"name": "有责任心", "required": True},
            {"name": "C 语言", "required": True},
        ]
    )
    rules = _by_field(extract_hard_requirements(profile), "core_skills")
    assert [r.value for r in rules] == ["C 语言"]


def test_subjective_description_stays_in_the_profile_as_soft_skills():
    """spec：这类描述只作为软技能关键词保留在画像中。

    "保留"= 提取过程一个字节都不改画像（画像冻结后不可变，改动走新版本）。
    """
    profile = _profile(soft_skill_keywords=["沟通能力强", "有责任心"])
    before = copy.deepcopy(profile)
    rules = extract_hard_requirements(profile)

    assert profile["soft_skill_keywords"] == ["沟通能力强", "有责任心"]
    assert profile == before
    assert all("沟通" not in r.value for r in rules)


def test_assert_rejects_a_hand_built_subjective_rule():
    """反证：绕过提取直接构造一条违规规则，落库前的断言必须报违例。"""
    rogue = HardRequirement(
        field="core_skills",
        operator="contains",
        value="沟通能力强",
        blocking=True,
        human_readable="必会技能：沟通能力强（不满足则不通过硬门槛）",
    )
    with pytest.raises(SubjectiveRequirementError) as exc:
        assert_no_subjective_requirements([rogue])
    assert "沟通" in str(exc.value)


def test_assert_rejects_a_rule_on_the_soft_skill_field_itself():
    """字段本身就是软技能关键词时，即便值看起来中性也必须被拒。"""
    rogue = HardRequirement(
        field="soft_skill_keywords",
        operator="contains",
        value="跨部门推动",
        blocking=False,
        human_readable="软技能：跨部门推动",
    )
    with pytest.raises(SubjectiveRequirementError):
        assert_no_subjective_requirements([rogue])


def test_assert_passes_on_a_clean_rule_set():
    assert_no_subjective_requirements(extract_hard_requirements(_profile()))


def test_extraction_output_always_passes_the_assert():
    """提取与断言必须自洽：正常路径永远不该在落库前被自己的断言拦下。"""
    profile = _profile(
        core_skills=[
            {"name": "沟通能力强", "required": True},
            {"name": "抗压能力强", "required": True},
            {"name": "AUTOSAR MCAL 配置", "required": True},
        ],
        soft_skill_keywords=["有责任心", "团队合作"],
    )
    assert_no_subjective_requirements(extract_hard_requirements(profile))


def test_the_guard_is_not_vacuous(monkeypatch):
    """有效性测试：把词表清空后，同一份画像必须能产出一条被断言抓到的规则。

    没有这一条，上面所有绿灯都可能只是因为断言什么都没查（与
    tests/test_audit_assertion_effectiveness.py 同一思路）。
    """
    import app.agents.hard_requirement as module

    monkeypatch.setattr(module, "SUBJECTIVE_TERMS", ())
    monkeypatch.setattr(module, "SUBJECTIVE_FIELDS", frozenset())
    # ⚠️ 必须按 core_skills 过滤：extract 返回的是**整份**草案（学历/年限/平台
    # 等等都在里面），不过滤的话这条断言比的是全量列表，永远不等。
    leaked = _by_field(
        module.extract_hard_requirements(
            _profile(core_skills=[{"name": "沟通能力强", "required": True}])
        ),
        "core_skills",
    )
    assert [r.value for r in leaked] == ["沟通能力强"]

    monkeypatch.undo()
    with pytest.raises(SubjectiveRequirementError):
        assert_no_subjective_requirements(leaked)


def test_is_subjective_covers_the_documented_examples():
    """CLAUDE.md 与 spec 里点名的两个例子必须被识别。"""
    assert is_subjective("沟通能力强")
    assert is_subjective("有责任心")
    assert not is_subjective("C 语言")
    assert not is_subjective("UDS（ISO 14229）")
    assert not is_subjective("AUTOSAR MCAL 配置")
```

- [ ] **Step 2: 跑测试，确认它失败**

Run: `pytest tests/test_hard_requirement.py -v`
Expected: FAIL — `ImportError: cannot import name 'SUBJECTIVE_TERMS' from 'app.agents.hard_requirement'`

- [ ] **Step 3: 写实现**

在 `app/agents/hard_requirement.py` 里，`_NON_BLOCKING_LIST_FIELDS` 定义**之后**追加：

```python
# 主观描述词表（合规红线：主观描述不得进入硬门槛规则，只能作为软技能关键词）。
#
# ⚠️ **误判方向是刻意选的**：误拦一个真技能 = 少一条硬门槛规则（无害，人复核
# 草案时补得回来）；漏拦一个主观描述 = 触红线（"沟通能力强"变成筛人条件，且
# 将来会被拿去向候选人解释淘汰原因）。所以宁可宽一点。
# 已知会被误拦的技术词：含"稳定性"的（如"系统稳定性调优"）、含"意识"的（如
# "功能安全意识"）。这是接受的代价，⛔ 不要为了它们把词条删掉。
#
# ⛔ 反过来，⛔ 不许加"主动"/"积极"这类单独出现的词：ECU 领域有"主动安全"这
# 类真实技术术语，加进来会把合法技能整片误拦掉。要拦就用"积极主动"这种成词。
SUBJECTIVE_TERMS: tuple[str, ...] = (
    "沟通",
    "协调",
    "责任心",
    "有担当",
    "抗压",
    "团队合作",
    "上进",
    "事业心",
    "执行力",
    "亲和力",
    "情商",
    "性格",
    "稳定性",
    "踏实",
    "细心",
    "耐心",
    "悟性",
    "学习能力",
    "逻辑思维",
    "自驱",
    "积极主动",
    "意识",
    "能力强",
)

# 结构上就不该出现在硬门槛里的字段。spec：这类描述只作为软技能关键词保留在
# 画像中。EXTRACTABLE_FIELDS 里本来就没有它，这份集合是给
# assert_no_subjective_requirements() 用的第二道——挡住绕过提取直接构造的规则。
SUBJECTIVE_FIELDS: frozenset[str] = frozenset({"soft_skill_keywords"})


class SubjectiveRequirementError(ValueError):
    """有主观描述混进了硬门槛规则草案。

    这是**合规红线**被触碰，不是一个可以 except 掉继续跑的错误：调用方
    （app/graph/nodes.py 的 effect_confirm_profile）让它穿透出去，整条确认
    事务回滚，宁可让业务经理看到一次失败，也不让一条"沟通能力强"的门槛落库。
    """


def is_subjective(text: str) -> bool:
    """文本里是否含主观描述。⛔ 空白与 None 一律判为不主观（交给别的校验去管）。"""
    if not isinstance(text, str):
        return False
    return any(term in text for term in SUBJECTIVE_TERMS)


def assert_no_subjective_requirements(rules: list[HardRequirement]) -> None:
    """落库前的最后一道机器判据（tasks 5.9）。命中即抛，⛔ 不静默过滤。

    ⛔ 这里刻意**不**修复、不剔除、不降级——静默过滤会让"提取逻辑漏了一处"
    这个真实缺陷永远不现形。提取阶段该滤的已经滤掉了（_extract_core_skills /
    _extract_non_blocking_list），能走到这里还命中的，只可能是提取逻辑本身
    有洞或有人绕过提取直接构造规则，两种都必须响。
    """
    for rule in rules:
        if rule.field in SUBJECTIVE_FIELDS:
            raise SubjectiveRequirementError(
                f"字段 {rule.field!r} 属于软技能关键词，不得进入硬门槛规则"
                "（合规红线：主观描述不得进入硬门槛规则，只能作为软技能关键词）"
            )
        for text in (rule.value, rule.human_readable):
            if is_subjective(text):
                hit = next(term for term in SUBJECTIVE_TERMS if term in text)
                raise SubjectiveRequirementError(
                    f"硬门槛规则里出现主观描述（命中词 {hit!r}）："
                    f"field={rule.field!r} value={rule.value!r}"
                    "（合规红线：主观描述不得进入硬门槛规则，只能作为软技能关键词）"
                )
```

在 `_extract_core_skills()` 里，`if not name or _is_no_requirement(name):` 那一行**下面**追加：

```python
        # 合规红线：主观描述不得进入硬门槛规则。业务经理和模型把"沟通能力强"
        # 写进 core_skills 是真实会发生的——SYSTEM_PROMPT 只是提示词，不是行为
        # 约束。⛔ 这里静默跳过而不抛：画像里有主观描述本身完全合法，它只是
        # 不该变成门槛。落库前的 assert_no_subjective_requirements() 才是抛的那道。
        if is_subjective(name):
            continue
```

在 `_extract_non_blocking_list()` 里，`if _is_no_requirement(name):` 那一行**下面**追加同样的两行：

```python
        if is_subjective(name):
            continue
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `pytest tests/test_hard_requirement.py -v`
Expected: PASS（30 passed）

- [ ] **Step 5: 提交**

```bash
git add app/agents/hard_requirement.py tests/test_hard_requirement.py
git commit -m "feat(agents): 主观描述不得进硬门槛的两道防线与反证断言（tasks 5.9）"
```

---

### Task 4: 接进 `effect_confirm_profile`，与画像确认同一事务落库（tasks 5.8 落库）

**Files:**
- Modify: `app/graph/nodes.py`（新增 `_record_hard_requirements()`；在 `effect_confirm_profile` 函数体内调用）
- Test: `tests/test_hard_requirement_schema.py`（追加事务不变式与端到端用例段）

**Interfaces:**
- Consumes: Task 2 / Task 3 的 `extract_hard_requirements(profile: dict) -> list[HardRequirement]`、`assert_no_subjective_requirements(rules) -> None`；既有的 `app.storage.idempotency.idempotent_effect`（⛔ 本 Task 不改它）
- Produces: `app.graph.nodes._record_hard_requirements(conn, *, job_id: str, profile_version: int, profile_dict: dict) -> list[HardRequirement]`

⚠️ **⛔ 不新增 `effect_*` 节点、⛔ 不改 `effect_confirm_profile` 的签名、⛔ 不改 `business_key` 语义（仍是被冻结的 version）、⛔ 不碰 `app/web/server.py`。** 调用点已经把 `profile_dict` 和 `business_key` 传进来了，本 Task 只在函数体内多做一件事。多一个节点就多一个幂等键，而两个幂等键意味着"画像已 approved、规则草案却缺席"是一个可达状态——那正是工程铁律 1 要消灭的形态。

- [ ] **Step 1: 写失败测试**

在 `tests/test_hard_requirement_schema.py` **末尾**追加（顶部 import 补 `from app.graph.nodes import effect_confirm_profile`、`import json`）：

```python
# ── tasks 5.8 落库：与画像确认同一事务 ──────────────────────────────────

_PROFILE = {
    "job_title": "嵌入式软件工程师",
    "department": "电子研发部",
    "headcount": 1,
    "education_requirement": "本科及以上",
    "experience_years": "3-5年",
    "core_skills": [
        {"name": "C 语言", "required": True},
        {"name": "沟通能力强", "required": True},
    ],
    "soft_skill_keywords": ["沟通能力强"],
    "functional_safety": "ASIL-B",
    "autosar_experience": ["CP"],
    "mcu_family": ["英飞凌 Aurix"],
    "diag_stack": [],
    "toolchain": [],
    "sop_projects": [],
    "unspecified_fields": [],
}


def _seed_job(c: sqlite3.Connection, job_id: str = "job-1", version: int = 3) -> None:
    c.execute(
        "INSERT INTO job (id, title, status) VALUES (?, '嵌入式软件工程师', 'drafting')",
        (job_id,),
    )
    c.execute(
        "INSERT INTO job_profile (id, job_id, version, status, profile_json) "
        "VALUES (?, ?, ?, 'drafting', ?)",
        (f"{job_id}-v{version}", job_id, version, json.dumps(_PROFILE, ensure_ascii=False)),
    )
    c.commit()


def test_confirming_a_profile_writes_the_rule_draft(conn):
    _seed_job(conn)

    effect_confirm_profile(
        conn,
        thread_id="job-1",
        business_key="3",
        profile_dict=_PROFILE,
        reviewer="manager-001",
    )

    rows = conn.execute(
        "SELECT field, operator, value, blocking FROM hard_requirement "
        "WHERE job_id = 'job-1' AND profile_version = 3"
    ).fetchall()
    values = {(r[0], r[2]) for r in rows}
    assert ("education_requirement", "本科") in values
    assert ("experience_years", "3") in values
    assert ("core_skills", "C 语言") in values
    # 合规红线：主观描述⛔ 不得落进这张表。
    assert not any("沟通" in r[2] for r in rows)


def test_rules_land_in_the_same_transaction_as_the_confirmation(conn):
    """工程铁律 1：effect_log 那一条与业务写同生共死。

    reviewer 为空会撞上 human_review 的 CHECK。它在 hard_requirement 写入
    **之后**执行，所以这条用例真正验的是"前面写的规则被回滚掉了"——而不是
    "根本没写过"。
    """
    _seed_job(conn)

    with pytest.raises(sqlite3.IntegrityError):
        effect_confirm_profile(
            conn,
            thread_id="job-1",
            business_key="3",
            profile_dict=_PROFILE,
            reviewer="   ",
        )

    assert conn.execute("SELECT count(*) FROM hard_requirement").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM human_review").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM effect_log").fetchone()[0] == 0
    assert conn.execute("SELECT status FROM job WHERE id='job-1'").fetchone()[0] == "drafting"


def test_replaying_the_confirmation_does_not_duplicate_rules(conn):
    """LangGraph 恢复时节点从头整个重跑——第二次必须被 effect_log 短路。"""
    _seed_job(conn)
    kwargs = dict(
        thread_id="job-1", business_key="3", profile_dict=_PROFILE, reviewer="manager-001"
    )

    effect_confirm_profile(conn, **kwargs)
    first = conn.execute("SELECT count(*) FROM hard_requirement").fetchone()[0]
    effect_confirm_profile(conn, **kwargs)
    second = conn.execute("SELECT count(*) FROM hard_requirement").fetchone()[0]

    assert first > 0
    assert first == second


def test_a_profile_with_no_gateable_content_confirms_cleanly(conn):
    """一条规则都提不出来⛔ 不算失败：确认照常成立，只是草案为空。"""
    _seed_job(conn, job_id="job-2", version=1)
    empty = {
        "job_title": "储备干部",
        "department": "综合管理部",
        "headcount": 1,
        "education_requirement": "不限",
        "experience_years": "不限",
        "core_skills": [],
        "soft_skill_keywords": ["沟通能力强"],
        "functional_safety": "无",
        "autosar_experience": [],
        "mcu_family": [],
        "diag_stack": [],
        "toolchain": [],
        "sop_projects": [],
        "unspecified_fields": [],
    }

    effect_confirm_profile(
        conn, thread_id="job-2", business_key="1", profile_dict=empty, reviewer="manager-001"
    )

    assert conn.execute("SELECT status FROM job WHERE id='job-2'").fetchone()[0] == "approved"
    assert (
        conn.execute("SELECT count(*) FROM hard_requirement WHERE job_id='job-2'").fetchone()[0]
        == 0
    )
```

- [ ] **Step 2: 跑测试，确认它失败**

Run: `pytest tests/test_hard_requirement_schema.py -v`
Expected: FAIL — 新增的四条里，`test_confirming_a_profile_writes_the_rule_draft` 报断言失败（`hard_requirement` 查不到行），其余三条中依赖规则行数的也失败

- [ ] **Step 3: 写实现**

在 `app/graph/nodes.py` 顶部 import 段追加（放在 `from app.agents.jd_agent import ...` 之后，保持字母序）：

```python
from app.agents.hard_requirement import (
    HardRequirement,
    assert_no_subjective_requirements,
    extract_hard_requirements,
)
```

在 `_record_human_review()` 定义**之后**追加：

```python
def _record_hard_requirements(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    profile_version: int,
    profile_dict: dict,
) -> list[HardRequirement]:
    """从画像提取硬门槛规则草案并落库（tasks 5.8）。

    ⛔ **本函数不 commit、不开事务**，只能在某个 effect_* 节点的函数体内被调用，
    与该节点的业务写落在同一个事务里、由 idempotent_effect 装饰器统一提交一次
    （工程铁律 1）。分开提交会出现"画像已冻结但规则草案缺席"，而更糟的是：
    幂等记录一旦先落，重试会被判定为"已执行"，那份草案**永远不会补上**。

    ⛔ **提取是确定性纯函数，这里不调模型**（工程铁律 2）。草案要能被人复核、
    被回放对比，一次模型调用就把它变成不可复算的东西。

    ⛔ **本函数只写规则，不执行规则**（合规红线：AI 只做排序推荐，不做自动淘汰）。
    blocking 列是给人看的标注，本变更包内没有任何代码读它去筛人。

    落库前过一道 assert_no_subjective_requirements()：它是合规红线「主观描述不得
    进入硬门槛规则」的最后一道机器判据。命中就让 SubjectiveRequirementError 穿透
    出去、整条确认事务回滚——宁可让业务经理看到一次失败，也不让一条"沟通能力强"
    的门槛落库。

    ⛔ INSERT 不加 OR IGNORE：extract 已经按天然键去重，主键冲突只可能是去重逻辑
    有洞或同一版被写了两次，两种都必须响，⛔ 不许静默吞掉。
    """
    rules = extract_hard_requirements(profile_dict)
    assert_no_subjective_requirements(rules)
    for rule in rules:
        conn.execute(
            "INSERT INTO hard_requirement "
            "(job_id, profile_version, field, operator, value, blocking, "
            "human_readable, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (
                job_id,
                profile_version,
                rule.field,
                rule.operator,
                rule.value,
                1 if rule.blocking else 0,
                rule.human_readable,
            ),
        )
    return rules
```

在 `effect_confirm_profile()` 的函数体里，`conn.execute("UPDATE job SET status = 'approved' ...")` 那一行**之后**、`_record_human_review(...)` 调用**之前**插入：

```python
    # 2026-09-04（tasks 5.8/5.9）：硬门槛规则草案也进**同一个事务**。它回答的是
    # "按这一版画像，哪些条件是可自动判定的门槛"（spec「硬门槛规则草案提取」）。
    # ⛔ 不新增一个 effect_extract_hard_requirements 节点：多一个节点就多一个
    # 幂等键，而两个幂等键意味着"画像已 approved、规则草案却缺席"是一个可达
    # 状态——将来筛简历时没人会发现草案缺了，只会以为这个岗位本来就没门槛。
    # ⛔ 放在 _record_human_review 之前：规则草案是这次确认的产物，人工留痕是
    # 这次确认的凭据，顺序调过来不会出错，但保持"先产物后凭据"与
    # effect_abandon_profile 的写法一致。
    _record_hard_requirements(
        conn,
        job_id=thread_id,
        profile_version=int(business_key),
        profile_dict=profile_dict,
    )
```

同时在 `effect_confirm_profile` 的 docstring 末尾追加一段：

```
    2026-09-04（tasks 5.8/5.9）：硬门槛规则草案（`hard_requirement`）也进**同一个
    事务**，由 `_record_hard_requirements()` 写。提取本身是 `app/agents/
    hard_requirement.py` 里的确定性纯函数，⛔ 不调模型。草案只**存**规则、不
    **执行**规则——本变更包内没有任何代码读 `blocking` 去淘汰候选人（合规红线：
    AI 只做排序推荐，不做自动淘汰）。
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `pytest tests/test_hard_requirement_schema.py -v`
Expected: PASS（12 passed）

- [ ] **Step 5: 跑全量回归，确认没打破并行同伴以外的任何东西**

Run: `pytest -q`
Expected: 0 failed。若出现失败，**只允许**修 `app/agents/hard_requirement.py`、`app/graph/nodes.py`、`app/storage/db.py` 与本单元的两个测试文件；⛔ 不得为了让测试变绿去改 `app/audit/`、`app/web/`、`app/outbound/`（那是并行泳道的工作区）。

- [ ] **Step 6: 提交**

```bash
git add app/graph/nodes.py tests/test_hard_requirement_schema.py
git commit -m "feat(graph): 硬门槛规则草案与画像确认同一事务落库（tasks 5.8）"
```

---

## Spec 覆盖对照

| spec Requirement / Scenario | 落在哪个 Task |
|---|---|
| `Requirement: 硬门槛规则草案提取` —— "每条规则 MUST 可解释、可单独启停" | Task 1（`human_readable` NOT NULL + CHECK 非空 = 可解释；一条规则一行 + `blocking` 列 = 可单独启停）、Task 2（`human_readable` 模板） |
| `Scenario: 提取硬门槛` —— 字段名/比较运算符/比较值/是否阻断 | Task 1（四列 + `operator` CHECK）、Task 2（`extract_hard_requirements`、`test_every_rule_carries_the_four_required_parts_plus_a_sentence`） |
| `Scenario: 提取硬门槛` —— "画像产出完成"这个时机 | Task 4（挂在 `effect_confirm_profile`，画像冻结为 approved 的同一事务） |
| `Scenario: 主观要求不得进入硬门槛` —— MUST NOT 转为硬门槛规则 | Task 3（结构性 + 词表两道防线，含反证与有效性测试） |
| `Scenario: 主观要求不得进入硬门槛` —— "只作为软技能关键词保留在画像中" | Task 3（`test_subjective_description_stays_in_the_profile_as_soft_skills`：提取过程一个字节不改画像） |
| tasks 1.2b 建表 | Task 1 |
| tasks 5.8 | Task 2（提取）+ Task 4（落库） |
| tasks 5.9 | Task 3 |

**本单元刻意不覆盖的**（登记，不做）：

- 规则的**执行**（拿 `hard_requirement` 去筛简历）——属简历筛选环节，不在本变更包。合规上也必须先有人工确认节点。
- 规则的**启停开关**（UI 或 API）——Web 泳道的工作区，本单元⛔ 不碰 `app/web/`。表结构已经能承载（一条规则一行、`blocking` 独立），加开关时不需要改表。
- `project_experience_requirement` 的自动判定——自由文本，判定必然要靠语义理解，那就回到"调模型"上去了。保守方向：不提取。

## 自查结果

- `grep -c '^### Task ' docs/superpowers/plans/2026-09-04-m1-job-profile-intake-unit-hard-requirement.md` = **4**（三级标题，`scripts/task-brief` 可解析）
- Global Constraints 段存在，内容逐字取自 `CLAUDE.md`
- 无 TBD / TODO / "适当处理错误" 类占位符；每个代码步骤都给了完整代码与确切命令
- 类型名一致性：`HardRequirement` / `OPERATORS` / `EXTRACTABLE_FIELDS` / `SUBJECTIVE_TERMS` / `SUBJECTIVE_FIELDS` / `SubjectiveRequirementError` / `is_subjective` / `assert_no_subjective_requirements` / `extract_hard_requirements` / `_record_hard_requirements` 在 Task 2/3/4 间逐字一致
- 有副作用的写入独占既有节点 `effect_confirm_profile` 并带幂等键 `{job_id}:effect_confirm_profile:{version}`；事务不变式由 `test_rules_land_in_the_same_transaction_as_the_confirmation` 与 `test_replaying_the_confirmation_does_not_duplicate_rules` 覆盖
- 本单元不产生 AI 评分，`evidence_ref` 断言不适用（`criterion_score` 未被触碰）

## 端到端提取验证（2026-09-04 实跑）

计划里的代码块被原样提取到临时目录、用本仓库 `venv`（pytest 8.3.4）跑过，**不是只做了纸面自查**。

| 验证项 | 结果 |
|---|---|
| Task 2 + Task 3 的 `app/agents/hard_requirement.py` + `tests/test_hard_requirement.py` 全量 | **30 passed**（Task 2 段 21 条、Task 3 段 9 条，与两个 Task 的 Expected 一致） |
| Task 1 的建表 DDL（独立 sqlite 内存库） | 8 列名与约定逐字一致；`executescript` 连跑三次幂等；野运算符 / 空白 `human_readable`（空格与制表符两种）/ `blocking=2` 全部被 CHECK 拒；复合主键拒重复、两版画像并存 |
| Task 4 的事务不变式（用真实的 `app.storage.idempotency.idempotent_effect` + 最小 schema 复现） | `reviewer="   "` → `IntegrityError`；`hard_requirement` / `human_review` / `effect_log` 三表回滚到 0 行、`job.status` 仍为 `drafting`；同一 `business_key` 重放两次规则行数不变（被 `effect_log` 短路） |

**揪出并已修回计划的 bug（1 个）**：`test_the_guard_is_not_vacuous` 原本拿 `extract_hard_requirements()` 的**全量**返回值去比 `["沟通能力强"]`，而那份返回值里还有学历/年限/平台等 8 条规则，断言永远不等。已改为先按 `core_skills` 过滤。这类错误纸面自查看不出来——它长得完全正确。

**边界**：测试与被测代码出自同一份文档、同一个作者，全绿只证明**代码可执行且内部自洽**，不证明**符合 spec**。spec 合规由 `run-build` 的两阶段 review 负责。另：Task 1 / Task 4 的实际测试文件跑在真实的 `app/storage/db.py` 与 `app/graph/nodes.py` 上，本次只验证了它们所依赖的 DDL 与事务语义，未在真实仓库里执行（本单元不进 run-build）。
