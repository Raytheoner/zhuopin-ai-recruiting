# M1 采集质量修复 · 交付单元 B（模糊回复兜底档位与追问预算口径）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让业务经理回一句「不知道 / 你有什么建议」或把问题反问回来时，系统当轮就给出 2-3 个具体候选档位而不是空话；同时让这类零产出的空转轮不再消耗追问预算。

**Architecture:** 三条改动线在 `run_intake_turn` 汇合。第一条是**判定**：新增纯函数 `is_vague_reply()`，用模糊表态词表 + 反问模式（问号结尾且与上一轮问题无二字片段交集）做确定性判定，**不调模型**——这次事故本身就是"提示词说了、模型没做"（design.md 决策 3）。第二条是**档位供给**：`ecu_knowledge.FOLLOWUP_RULES` 从 `list[str]` 升级为 `list[FollowupSpec]`（`text`/`field`/`options`），补齐采购侧词条，再加一份覆盖全部画像字段的通用档位表，`fallback_options_for_field()` 三级取数保证任何字段都拿得到 2-3 个档位。第三条是**预算口径**：`job_profile` 新增 `asked_questions` 列承载已问台账，`compute_intake_turn` 判定 `is_productive` 并与画像草案写在**同一条 INSERT** 里，`MAX_ROUNDS`(5) 改为只数有产出轮、新增 `MAX_TOTAL_ROUNDS`(8) 数总行数，任一命中即收尾。`business_key` 继续用总行数，幂等语义完全不变。

**Tech Stack:** Python 3.14（`./venv`，与 `.51` 服务器严格对齐）· LangGraph 1.0.10 + `langgraph-checkpoint-sqlite` 2.0.6 · FastAPI 0.115.6 · pydantic 2.13.4 · SQLite（`sqlite3`）· pytest 8.3.4 · 原生 DOM 单文件前端（无构建）

---

## Global Constraints

以下条目从 `CLAUDE.md`（2026-08-19 版）与 `openspec/changes/m1-intake-quality-fixes/delivery-units.md` §5 **逐字复制**。**每个 Task 的验收隐含包含本节全部内容**，`subagent-driven-development` 会把这一段原样交给 reviewer 当注意力透镜。

### 工程铁律（不可违背）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
   *为什么*：业务写失败而幂等记录成功 → 系统判定"已执行"→ 永不重试。**幂等本是防重复的保护，拆开事务后变成永久丢失的保证。** 实证：`.51` 现网 2026-08-10 与 08-12 各丢一轮 `outbox`（幂等记录已落），用户没收到回复且永远不会补发，见 `docs/findings/2026-08-13-sqlite-事务归属冲突.md` §8.5。
2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。
5. **`temperature=0`；模型版本优先显式锁定**，禁止 `latest` 类别名。
   供应商不提供带版本号快照时（如 DeepSeek 公开 API 只有 `deepseek-chat` 这类会漂移的别名），**必须从 API 响应里取回实际的 `model` 字段并持久化**——配置里写的名字不算数，响应返回的才算。
   *为什么*：铁律的目的是评分可复现、可审计。供应商静默升级模型会让历史评分失去解释力，而 PIPL 的说明权要求你能回答"这条评分是哪个版本打的"。锁不住版本时，至少要记得住版本。
7. **`langgraph >= 1.0.10`**（GHSA-g48c-2wqr-h844）。

### 部署约束

1. **路径前缀就绪**：FastAPI `root_path=/hr/recruit-agent`，前端资源与接口调用**一律相对路径**，禁止硬编码 `/static/…` `/api/…`。验收标准是挂到任意子路径下都能正常工作，且有测试覆盖。
4. **目标服务器是 Windows，没有 Docker**。部署形态 = Python venv + Windows 计划任务（SYSTEM 账户 + AtStartup + 失败重启 3 次）+ 防火墙规则 + scp 推送。不要引入容器。

### 合规红线（本单元相关的三条）

- **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。审计断言：`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。
  → 本单元的具体形态：**候选档位只是建议**。用户未明确选定之前，任何档位都不得进入 `profile_patch`。用户回"你决定 / 随便"本身不算选定（Task 5 有专门测试）。
- **AI 生成的 JD、拒信、邀约须带标识**（《AI 生成合成内容标识办法》2025-09-01 施行）。
  → 本单元的具体形态：候选档位是 AI 生成内容，**在纯文本通道渲染出来时就必须带"以下为 AI 建议选项"标识**（Task 5）。第 4 章的可点选控件另有自己的标识（tasks 4.3），两处各管各的呈现形态，不能只做一处。
- 主观描述（"沟通能力强"）不得进入硬门槛规则，只能作为软技能关键词。
  → 本单元的具体形态：`soft_skill_keywords` 的通用档位（沟通协调 / 跨部门推动 / 供应商谈判）只能落到 `soft_skill_keywords` 字段，不得出现在 `core_skills` 之类的硬性字段档位里。

### 跨单元接口约定（`delivery-units.md` §5，逐字抄录）

3. **B 与 F 都会改 `SYSTEM_PROMPT` → 各自升 `prompt_version`**（现为 `intake-v3`；B → `v4`，F → `v5`）。铁律 5
4. **B 若为已问台账新增列，走 1.1 已建立的 `init_schema` 幂等加列路径**，不另起迁移机制（决策 10）；所有新列必须可空或有默认值，既有 15 个 job 的历史行不回填
5. **每个单元开工前必须 rebase 到最新 main** —— `app/agents/intake_agent.py` 与 `app/graph/nodes.py` 被 B/D/E/F 四个单元连续改动，是本批最热的两个文件

### 明确不适用（reviewer 不必在本单元追这几条）

- 铁律 3（AI 评分持久化）、铁律 4（`evidence_ref` 非空）：本单元不写 `criterion_score`，代码库中亦无该表。reviewer 确认无相关落地即可。
- 铁律 6（企微回调先落库）：本单元不接企微通道。
- 合规红线「禁止人脸/表情分析」「模型全部走境内」：本单元不换供应商、不改 `base_url`、不引入任何多模态。

---

## 开工前必须知道的三件事

### 一、Coverage Gap 已关闭，对本单元零影响

`intake-turn-observability`「整轮失败记累计耗时」那条 Coverage Gap 已于 2026-08-19 由 Shao Peishen 决策**走窄化路（路 B）**关闭，`specs/intake-turn-observability/spec.md` 原文已改。

**对本单元的影响是零**：Task 4 的 `is_productive` 判定**不需要**多加"整轮失败"分支，Task 6 的预算口径**不需要**多加规则。若在任何旧文档里读到"这是 B 的开工前置"，一律以 `spec.md` 现行原文与 `design.md` 的 Coverage Gaps 段（已销号）为准。

### 二、`derive_question_id` 的字段校验是本单元的**前置条件**，不是顺手修

单元 A 终审记的 minor 项（`derive_question_id` 未校验 `field` 是否属于 `JobProfile.model_fields`）落在本单元，且必须**第一个做**（Task 1）。

理由比"第 5 章之前修"强得多：**Task 4 让 `question_id` 第一次从"只用来渲染"变成"参与判定"**。`is_productive` 直接按"有没有问出未问过的 `question_id`"取值。模型给一个野 `field`（拼错，或幻觉出一个不存在的字段名）时，`derive_question_id` 今天原样接受，于是每轮都产出一个"新"的 `question_id`、每一轮都被判成有产出——`MAX_ROUNDS` 的有产出轮计数当场失效，**正是这一章要修的那个故障换了个形式回来**。

落地形态：对不在 `JobProfile.model_fields` 里的 `field` 按"无 field"降级（走既有的 `free:` 哈希分支，**不抛异常**——降级而非报错是单元 A 已确立的基调），并把"降级次数"与"null-field 次数"打点，供第 8 章 8.1 回放时看比例。

### 三、已问台账落哪儿 —— **已决：新增一列 `job_profile.asked_questions`**

这一项属技术方案，按 `CLAUDE.md` 决策代理表在 plan 阶段定，不必回头找 Shao Peishen。**选新增列（方案 a），不选 `profile_json` 的下划线内部键（方案 b）。**

**决定性理由（一条就足够否掉方案 b）**：`profile_json` 在采集期间每轮都被 `app/web/server.py` 的 `_run_turn` 整份读回来当 `profile_patch_accumulated`，再由 `_build_user_prompt` 整份 `json.dumps` 进【已确认字段】段发给模型。**台账放进去就会每轮泄漏进 prompt**——既污染 `input_hash`（铁律 5 的可复算性），又让模型看到一份它不该看、也看不懂的内部结构。决策 8 的 `_jd_text` 之所以能用这个位置，是因为它只在 `confirm` 那一刻（对话已终止）才写入，从来不会进 prompt；台账是采集期间每轮都写的，**位置相同、时机完全不同**。

次要理由：
- 方案 a 走的正是 §5 约定 4 指定的 `init_schema` 幂等加列路径（决策 10），历史行拿 `'[]'` 默认值、不需要回填；
- 单元 E 的 5.1 要在其上扩"已答 / 重问次数"，独立列可以再加列（`answered_question_ids` 等），塞在 `profile_json` 里则要在一个 LLM 自由生成的 dict 上做结构演进；
- 8.1 回放要按台账统计，`SELECT asked_questions FROM job_profile` 比在 JSON 里挖内部键直接得多。

**列的内容不是裸 id 列表，而是 `IntakeQuestion.to_payload()` 的完整数组。** 多存 `field` 与 `options` 是必需的：Task 5 的「候选档位不得代替用户做决定」判定要知道上一轮**给过哪些档位**，只存 id 就查不回来。id 集合由并集派生。

**给单元 E 的交接**：`asked_question_ids_before`（并集）与 `previous_questions`（上一轮原样）已经由 `_run_turn` 读出并放进 `IntakeState`，E 的 5.1 直接在这两个 state 键上扩，不需要重新设计取数路径。

---

## File Structure

| 文件 | 本单元的职责 | Task |
|---|---|---|
| `app/schemas/job_profile.py` | 新增 `SYSTEM_MANAGED_FIELDS` 常量（唯一真源，`intake_question` 与 `intake_agent` 共用，避免循环导入） | 1 |
| `app/agents/intake_question.py` | `derive_question_id` 校验 `field` 归属 + 降级指标；`render_questions_text` 把档位渲进文本并带 AI 建议标识 | 1、5 |
| `app/agents/ecu_knowledge.py` | `FollowupSpec` 对象化的领域选项库；采购侧词条；通用字段档位；`fallback_options_for_field()` 三级取数 | 2 |
| `app/agents/intake_agent.py` | `is_vague_reply()` 确定性判定；兜底档位注入与合成；候选档位不入画像；`is_productive` 判定；双预算口径；`prompt_version` → `intake-v4` | 3、4、5、6 |
| `app/storage/db.py` | `job_profile.asked_questions` 列（`SCHEMA` 与 `_ADDED_COLUMNS` 两处同步） | 4 |
| `app/graph/state.py` | `productive_round_count` / `asked_question_ids_before` / `previous_questions` / `is_productive` / `asked_questions` 五个键 | 4、6 |
| `app/graph/nodes.py` | `compute_intake_turn` 透传新入参与新结果；`effect_persist_draft` 把两列写进**同一条 INSERT** | 4 |
| `app/web/server.py` | `_run_turn` 从库里读两个预算口径与已问台账 | 4、6 |

**不碰的文件**：`app/web/static/index.html`（第 4 章＝单元 C 的唯一战场，B ∥ C 的并行前提就是零重叠）、`app/llm/gateway.py`、`app/storage/idempotency.py`、`app/graph/build.py`。

**明确不动的既有逻辑**：`_repeats_earlier_assistant_turn`。它的去留结论归 tasks 5.8（单元 E），单元 A 已在其 docstring 里把这个待办显式挂给 5.8。本单元只是让它比对到的文本多了档位行——渲染入口仍然唯一，逐字比对不失效。

---

### Task 1: `derive_question_id` 校验 field 归属并打降级指标

**Files:**
- Modify: `app/schemas/job_profile.py`（新增 `SYSTEM_MANAGED_FIELDS` 常量）
- Modify: `app/agents/intake_question.py`
- Test: `tests/test_intake_question.py`

**Interfaces:**
- Consumes: `JobProfile.model_fields`（pydantic v2）、既有的 `derive_question_id(field, text)` 签名
- Produces:
  - `app.schemas.job_profile.SYSTEM_MANAGED_FIELDS: frozenset[str]`
  - `app.agents.intake_question.QUESTION_TARGET_FIELDS: frozenset[str]`
  - `app.agents.intake_question.question_id_metrics() -> dict[str, int]`
  - `app.agents.intake_question.reset_question_id_metrics() -> None`
  - `derive_question_id(field, text)` 签名不变，行为变：野 field → `free:<hash>`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_intake_question.py` 末尾：

```python
def test_unknown_field_degrades_to_text_hash():
    """
    模型幻觉出一个不存在的字段名时按"无 field"降级。
    不降级的后果不是脏数据，是判定失效：每轮一个新 id → 每轮都被判成有产出
    → MAX_ROUNDS 的有产出轮计数当场归零（第 3 章 3.9）。
    """
    from app.agents.intake_question import derive_question_id

    text = "要哪个 ASIL 等级？"
    assert derive_question_id("functional_safety_level", text) == derive_question_id(None, text)


def test_unknown_field_does_not_raise():
    """降级而非报错——单元 A 已确立的基调，一个野字段不该炸掉整轮采集。"""
    from app.agents.intake_question import derive_question_id

    assert derive_question_id("完全不存在的字段", "随便问一句？").startswith("free:")


def test_system_managed_field_is_not_a_valid_question_target():
    """unspecified_fields 由系统填，不该成为追问目标。"""
    from app.agents.intake_question import QUESTION_TARGET_FIELDS, derive_question_id

    assert "unspecified_fields" not in QUESTION_TARGET_FIELDS
    assert derive_question_id("unspecified_fields", "哪些字段没定？").startswith("free:")


def test_metrics_count_null_and_unknown_fields():
    """8.1 回放要看降级比例，所以计数必须真的在累计。"""
    from app.agents.intake_question import (
        derive_question_id,
        question_id_metrics,
        reset_question_id_metrics,
    )

    reset_question_id_metrics()
    derive_question_id("headcount", "招几个人？")
    derive_question_id(None, "车型是？")
    derive_question_id("mcu_familly", "MCU 平台族是？")  # 拼错

    metrics = question_id_metrics()
    assert metrics["total"] == 3
    assert metrics["null_field"] == 1
    assert metrics["unknown_field"] == 1
    assert metrics["unknown_field:mcu_familly"] == 1
    reset_question_id_metrics()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
./venv/bin/python -m pytest tests/test_intake_question.py -q
```

Expected: FAIL —— `ImportError: cannot import name 'QUESTION_TARGET_FIELDS'`（前两条测试则是 `AssertionError`，因为野 field 目前被原样返回）。

- [ ] **Step 3: 加 `SYSTEM_MANAGED_FIELDS` 常量**

在 `app/schemas/job_profile.py` 的 `from pydantic import BaseModel, Field` 之后、`class JobStatus` 之前插入：

```python
# 由系统填写、不接受模型或用户直接作答的字段。追问的 field 落在这里面时
# 与"字段名不存在"同等处理（app/agents/intake_question.derive_question_id）。
# 放在 schema 模块而不是 intake_agent：intake_question 也要用，从 intake_agent
# 导入会形成 intake_agent → intake_question → intake_agent 的循环。
SYSTEM_MANAGED_FIELDS: frozenset[str] = frozenset({"unspecified_fields"})
```

- [ ] **Step 4: 改 `app/agents/intake_question.py`**

把文件顶部的 import 段改为：

```python
from __future__ import annotations

import hashlib
import threading
from collections import Counter
from dataclasses import dataclass

from app.schemas.job_profile import SYSTEM_MANAGED_FIELDS, JobProfile
```

在 `_FREE_ID_PREFIX = "free:"` 那一行之后、`def derive_question_id` 之前插入：

```python
# 允许作为追问目标的字段 = JobProfile 的字段表减去系统管理字段。
# 从 model_fields 取而不是手写一份：手写的清单会和 schema 悄悄漂移，而这里
# 一旦漂移，合法字段会被当成野字段降级——降级不报错，没人会发现。
QUESTION_TARGET_FIELDS: frozenset[str] = frozenset(JobProfile.model_fields) - SYSTEM_MANAGED_FIELDS

# question_id 派生的降级计数。第 8 章 8.1 回放时看比例用：野 field 占比高
# 说明 SYSTEM_PROMPT 的字段表没被模型遵守，null-field 占比高说明模型普遍
# 拿不准目标字段——两者都会让第 5 章的重问追踪失效，必须看得见。
#
# 只在进程内累计、不进日志：野 field 名是模型自由生成的不可信字符串，
# 打进日志既可能撞上 RedactionFilter 的高危键名正则，也没有留存价值——
# 需要的是比例，不是每一次的现场。
_METRICS_LOCK = threading.Lock()
_metrics_counter: Counter = Counter()


def _record_question_id_metric(kind: str, field_name: str | None = None) -> None:
    with _METRICS_LOCK:
        _metrics_counter[kind] += 1
        if field_name is not None:
            _metrics_counter[f"unknown_field:{field_name}"] += 1


def question_id_metrics() -> dict[str, int]:
    """派生指标快照。键：total / null_field / unknown_field，以及每个被拒绝的
    字段名一条 `unknown_field:<name>`（只在进程内，不落库不进日志）。"""
    with _METRICS_LOCK:
        return dict(_metrics_counter)


def reset_question_id_metrics() -> None:
    """测试与 8.1 回放前清零。生产路径不调用。"""
    with _METRICS_LOCK:
        _metrics_counter.clear()
```

在 `derive_question_id` 的 docstring 里，`field 缺失时退回文本哈希。` 那一段**之前**插入：

```
    field 必须是 JobProfile 字段表里的真实字段（QUESTION_TARGET_FIELDS）。
    不在表里的一律按"无 field"降级走文本哈希分支。为什么这是硬要求而不是
    洁癖：第 3 章的 is_productive 按"有没有问出未问过的 question_id"取值，
    模型每轮幻觉一个新字段名就每轮产出一个"新" id，于是每一轮都被判成有
    产出——MAX_ROUNDS 的有产出轮计数当场失效，正是第 3 章要修的那个故障
    换了个形式回来。

```

把函数体的前两行替换为：

```python
    normalized_field = (field or "").strip()
    _record_question_id_metric("total")
    if normalized_field and normalized_field not in QUESTION_TARGET_FIELDS:
        # 野 field（模型拼错、或幻觉出一个不存在的字段名）按"无 field"降级，
        # 不抛异常——降级而非报错是本模块已确立的基调。
        _record_question_id_metric("unknown_field", normalized_field)
        normalized_field = ""
    elif not normalized_field:
        _record_question_id_metric("null_field")
    if normalized_field:
        return normalized_field
```

（后面的 `digest = hashlib.sha256(...)` 与 `return f"{_FREE_ID_PREFIX}{digest}"` 两行保持原样。）

- [ ] **Step 5: 跑测试确认通过**

```bash
./venv/bin/python -m pytest tests/test_intake_question.py -q
```

Expected: PASS，19 passed（原 15 条 + 本 Task 新增 4 条）。

- [ ] **Step 6: 跑全量回归**

```bash
./venv/bin/python -m pytest -q
```

Expected: `174 passed`（改动前基线 170，本 Task 新增 4 条）。任何既有用例转红都说明有一个合法字段被误判成野字段，不要改测试，回头查 `QUESTION_TARGET_FIELDS`。

- [ ] **Step 7: 提交**

```bash
git add app/schemas/job_profile.py app/agents/intake_question.py tests/test_intake_question.py && git commit -m "fix(intake): derive_question_id 校验 field 归属并打降级指标"
```

---

### Task 2: 领域选项库对象化与采购侧词条

**Files:**
- Rewrite: `app/agents/ecu_knowledge.py`
- Modify: `app/agents/intake_agent.py`（`suggested_followups` 返回类型、`_build_user_prompt` 渲染）
- Test: `tests/test_ecu_knowledge.py`、`tests/test_intake_agent.py`（三条既有用例要跟着改）

**Interfaces:**
- Consumes: Task 1 的 `QUESTION_TARGET_FIELDS`（守卫测试用）
- Produces:
  - `FollowupSpec(text: str, field: str | None = None, options: tuple[str, ...] = ())`，frozen dataclass
  - `FOLLOWUP_RULES: dict[str, list[FollowupSpec]]`
  - `GENERIC_FIELD_OPTIONS: dict[str, tuple[str, ...]]`、`LAST_RESORT_OPTIONS: tuple[str, ...]`
  - `FALLBACK_QUESTION_TEXT: dict[str, str]`、`FALLBACK_FIELD_ORDER: tuple[str, ...]`
  - `library_options_for_field(field) -> tuple[str, ...]`
  - `fallback_options_for_field(field) -> tuple[str, ...]`（**保证非空且长度 2-3**）
  - `suggested_followups(history) -> list[FollowupSpec]`（返回类型变了，Task 3-6 依赖）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_ecu_knowledge.py` 末尾：

```python
def test_every_spec_targets_a_real_profile_field_or_none():
    """
    field 写错不会报错，只会被 derive_question_id 静默降级成文本哈希 id——
    第 5 章的重问追踪就跟丢了。守卫测试是这个静默故障唯一的探测器。
    """
    from app.agents.intake_question import QUESTION_TARGET_FIELDS

    for term, specs in FOLLOWUP_RULES.items():
        for spec in specs:
            assert spec.field is None or spec.field in QUESTION_TARGET_FIELDS, (
                f"{term} 的 {spec.text!r} 指向了不存在的字段 {spec.field!r}"
            )


def test_every_spec_has_zero_or_two_to_three_options():
    """spec「模糊回复与反问的兜底档位」写死 2-3 个：1 个不算选择。"""
    for term, specs in FOLLOWUP_RULES.items():
        for spec in specs:
            assert len(spec.options) == 0 or 2 <= len(spec.options) <= 3, (
                f"{term} 的 {spec.text!r} 档位数为 {len(spec.options)}"
            )


def test_procurement_terms_are_covered():
    """姚祖怡那场卡死在"一般材料"上——知识库当时一个采购词条都没有。"""
    for term in ("一般材料", "办公采购", "非标产品", "供应商开发"):
        assert term in FOLLOWUP_RULES
    assert match_ambiguous_terms("招个采购，主要管一般材料") == ["一般材料"]


def test_fallback_options_never_empty_for_any_profile_field():
    """spec「领域外的字段也要有兜底」：不得因为知识库未命中而退回空话。"""
    from app.agents.ecu_knowledge import fallback_options_for_field
    from app.agents.intake_question import QUESTION_TARGET_FIELDS

    for name in QUESTION_TARGET_FIELDS:
        options = fallback_options_for_field(name)
        assert 2 <= len(options) <= 3, f"{name} 的兜底档位数为 {len(options)}"


def test_fallback_options_for_unknown_field_include_a_negative_choice():
    """含"无要求 / 不限"这类明确的否定档位，否则用户被逼着在三个"要"里挑一个。"""
    from app.agents.ecu_knowledge import fallback_options_for_field

    options = fallback_options_for_field(None)
    assert 2 <= len(options) <= 3
    assert any("无要求" in option or "不限" in option for option in options)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
./venv/bin/python -m pytest tests/test_ecu_knowledge.py -q
```

Expected: FAIL —— `AttributeError: 'str' object has no attribute 'field'`（`FOLLOWUP_RULES` 现在还是 `list[str]`）。

- [ ] **Step 3: 整体重写 `app/agents/ecu_knowledge.py`**

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FollowupSpec:
    """
    领域知识库里的一条追问：问什么、补哪个字段、有哪些具体档位。

    从 list[str] 升级成对象是第 3 章的地基（design.md 决策 4）：档位会被写进
    岗位硬性要求，来源必须可追溯、可评审，所以优先取知识库而不是让模型现场编
    ——"编造 MCU 型号"正是这类风险的高发面。

    field 必须是 app.agents.intake_question.QUESTION_TARGET_FIELDS 里的真实
    字段名，或者 None；写一个不存在的字段名会被 derive_question_id 静默降级成
    文本哈希 id，第 5 章的重问追踪就跟丢了。test_ecu_knowledge 有守卫测试。

    options 要么为空，要么 2-3 个：spec「模糊回复与反问的兜底档位」写死了
    "2 至 3 个具体的候选档位"，1 个不算选择，4 个开始变成新的负担。
    """

    text: str
    field: str | None = None
    options: tuple[str, ...] = ()


# 术语 → 追问（每条不超过 3 个，满足"每轮追问不超过 3 个问题"约束）
FOLLOWUP_RULES: dict[str, list[FollowupSpec]] = {
    "嵌入式开发": [
        FollowupSpec(
            "是否涉及 AUTOSAR（CP/AP）？",
            field="autosar_experience",
            options=("CP", "AP", "无要求"),
        ),
        FollowupSpec(
            "MCU 平台族是？（如英飞凌 Aurix / NXP S32K / TI）",
            field="mcu_family",
            options=("英飞凌 Aurix", "NXP S32K", "不限"),
        ),
        FollowupSpec(
            "是否有功能安全等级（ASIL）要求？",
            field="functional_safety",
            options=("ASIL-B", "ASIL-D", "无要求"),
        ),
    ],
    "驱动开发": [
        FollowupSpec(
            "驱动对接的总线类型是？（CAN-FD / LIN / 以太网）",
            field="core_skills",
            options=("CAN-FD", "LIN", "车载以太网"),
        ),
        FollowupSpec(
            "是否要求 UDS 诊断栈经验？",
            field="diag_stack",
            options=("UDS（ISO 14229）", "OBD 诊断", "无要求"),
        ),
    ],
    "功能安全": [
        FollowupSpec(
            "具体到 ASIL 哪个等级？",
            field="functional_safety",
            options=("ASIL-B", "ASIL-D", "无要求"),
        ),
        # field 刻意留 None：同一轮里它和上面那条都指向 functional_safety 会
        # 撞同一个 question_id，_to_intake_questions 的去重会把它整条丢掉。
        # 留 None 走文本哈希，两条问题都问得出来，代价是第 5 章追踪不到它。
        FollowupSpec(
            "是否要求 FuSa 工程师认证？",
            options=("要求", "不要求"),
        ),
    ],
    "算法开发": [
        FollowupSpec(
            "是感知/控制/诊断算法中的哪一类？",
            field="core_skills",
            options=("感知算法", "控制算法", "诊断算法"),
        ),
        FollowupSpec(
            "是否要求量产项目（SOP）经验？",
            field="sop_projects",
            options=("要求量产（SOP）经验", "预研/样件经验即可", "不限"),
        ),
    ],
    # 以下四条是非 ECU（采购/供应链）侧词条。姚祖怡那场就是卡死在"一般材料"
    # 上——知识库当时一个采购词条都没有，兜底只能退回空话（design.md 决策 4）。
    "一般材料": [
        FollowupSpec(
            "该岗位采购的「一般材料」指哪些品类？",
            field="project_experience_requirement",
            options=("原材料（钢材/塑料粒子等）", "电子元器件", "五金标准件与包装辅材"),
        ),
    ],
    "办公采购": [
        FollowupSpec(
            "办公采购的范围主要是哪一类？",
            field="project_experience_requirement",
            options=("办公用品与耗材", "IT 设备与软件", "行政服务外包"),
        ),
    ],
    "非标产品": [
        FollowupSpec(
            "非标产品指的是哪一类定制件？",
            field="project_experience_requirement",
            options=("按图定制机加工件", "定制工装夹具", "定制自动化设备"),
        ),
    ],
    "供应商开发": [
        FollowupSpec(
            "供应商开发这块最看重哪一项能力？",
            field="core_skills",
            options=("新供应商导入与审核", "供应商绩效与降本", "供应商质量改善"),
        ),
    ],
}


# 字段 → 通用候选档位。知识库未命中时的第二道，覆盖 JobProfile 的全部可问字段。
# spec「领域外的字段也要有兜底」要求这里必须给得出档位，且含"无要求 / 不限"
# 这类明确的否定档位——否则用户会被逼着在三个"要"里挑一个。
GENERIC_FIELD_OPTIONS: dict[str, tuple[str, ...]] = {
    "job_title": ("沿用现有岗位名称", "按职级重新拟定", "由 HR 拟定"),
    "department": ("研发部门", "供应链/采购部门", "其他部门（我来补充）"),
    "headcount": ("1 人", "2-3 人", "3 人以上"),
    "education_requirement": ("大专及以上", "本科及以上", "硕士及以上"),
    "experience_years": ("3 年以下", "3-5 年", "5 年以上"),
    "core_skills": ("按岗位常规技能即可", "有明确必会项（我来补充）", "不限"),
    "project_experience_requirement": ("要求同行业经验", "要求同岗位经验", "不限"),
    # 软技能档位只能落到 soft_skill_keywords（合规红线：主观描述不得进硬门槛）。
    "soft_skill_keywords": ("沟通协调", "跨部门推动", "供应商/客户谈判"),
    "autosar_experience": ("CP", "AP", "无要求"),
    "functional_safety": ("ASIL-B", "ASIL-D", "无要求"),
    "mcu_family": ("英飞凌 Aurix", "NXP S32K", "不限"),
    "diag_stack": ("UDS（ISO 14229）", "OBD 诊断", "无要求"),
    "sop_projects": ("要求量产（SOP）经验", "预研/样件经验即可", "不限"),
    "toolchain": ("Vector（CANoe/CANape）", "ETAS INCA", "不限"),
}

# 最后一道：连字段都没有（field=None）的问题也必须给得出选择。
LAST_RESORT_OPTIONS: tuple[str, ...] = ("无要求 / 不限", "按行业惯例即可", "有明确要求（我来补充）")

# 系统自己合成兜底问题时用的问法。第 6 章会另外引入一份"字段中文名"映射
# （tasks 6.4，用于确认前的缺口警示），那份是给人看字段名的，这份是给人回答
# 的问句，用途不同不要合并；6.4 落地时在这里加一条注释互相指认即可。
FALLBACK_QUESTION_TEXT: dict[str, str] = {
    "job_title": "这个岗位对外挂什么岗位名称？",
    "department": "这个岗位归在哪个部门？",
    "headcount": "这次计划招几个人？",
    "education_requirement": "学历上有什么要求？",
    "experience_years": "工作年限上有什么要求？",
    "core_skills": "有哪些必须会的核心技能？",
    "project_experience_requirement": "对项目经历有什么要求？",
    "soft_skill_keywords": "软技能上更看重哪一项？",
    "autosar_experience": "是否涉及 AUTOSAR（CP/AP）？",
    "functional_safety": "功能安全等级（ASIL）上有什么要求？",
    "mcu_family": "MCU 平台族有指定吗？",
    "diag_stack": "诊断栈上有什么要求？",
    "sop_projects": "对量产（SOP）项目经验有什么要求？",
    "toolchain": "工具链上有什么要求？",
}

# 合成兜底问题时的字段优先级：先问决定寻源方向的，再问细节。
# 顺序是刻意固定的——同一份对话重跑必须问出同一个问题，否则 8.1 的回放对比
# 不可复算。
FALLBACK_FIELD_ORDER: tuple[str, ...] = (
    "job_title",
    "department",
    "headcount",
    "experience_years",
    "education_requirement",
    "core_skills",
    "functional_safety",
    "autosar_experience",
    "mcu_family",
    "diag_stack",
    "toolchain",
    "sop_projects",
    "project_experience_requirement",
    "soft_skill_keywords",
)


def match_ambiguous_terms(text: str) -> list[str]:
    return [term for term in FOLLOWUP_RULES if term in text]


def library_options_for_field(field: str | None) -> tuple[str, ...]:
    """知识库里为这个字段登记过的档位，取第一条命中的。没有则返回空。"""
    if not field:
        return ()
    for specs in FOLLOWUP_RULES.values():
        for spec in specs:
            if spec.field == field and spec.options:
                return spec.options
    return ()


def fallback_options_for_field(field: str | None) -> tuple[str, ...]:
    """
    兜底档位的三级取数：领域选项库 → 通用字段档位 → 最后一道。

    **保证非空且长度在 2-3 之间**——spec「领域外的字段也要有兜底」要求
    "不得因为知识库未命中而退回空话"，返回空元组就是退回空话。
    """
    options = library_options_for_field(field)
    if not options and field:
        options = GENERIC_FIELD_OPTIONS.get(field, ())
    if not options:
        options = LAST_RESORT_OPTIONS
    return options[:3]
```

- [ ] **Step 4: 让 `intake_agent` 跟上新类型**

在 `app/agents/intake_agent.py` 中：

把 `from app.agents.ecu_knowledge import FOLLOWUP_RULES, match_ambiguous_terms` 替换为：

```python
from app.agents.ecu_knowledge import (
    FALLBACK_FIELD_ORDER,
    FALLBACK_QUESTION_TEXT,
    FOLLOWUP_RULES,
    FollowupSpec,
    fallback_options_for_field,
    match_ambiguous_terms,
)
```

（`FALLBACK_*` 与 `fallback_options_for_field` 在 Task 5 才用到，一次导入到位，避免 Task 5 再改一遍 import 段。）

把 `suggested_followups` 的签名与函数体末尾改为：

```python
def suggested_followups(history: list[dict]) -> list[FollowupSpec]:
```

```python
    specs: list[FollowupSpec] = []
    for term in match_ambiguous_terms(user_text):
        for spec in FOLLOWUP_RULES[term]:
            if spec not in specs:
                specs.append(spec)
    return specs
```

（docstring 与取 `user_text` 的那几行保持原样。）

在 `def _build_user_prompt` **之前**插入渲染函数，并把 `_build_user_prompt` 的类型标注改掉：

```python
def _render_followup_line(spec: FollowupSpec) -> str:
    """把一条知识库追问渲染进 prompt。带上 field 与 options，模型才知道该照抄
    哪个字段名、有哪些现成档位可用——否则它只看得到问题文本，档位又要自己编。"""
    parts = [f"- {spec.text}"]
    if spec.field:
        parts.append(f"（目标字段：{spec.field}）")
    if spec.options:
        parts.append("（可选档位：" + "、".join(spec.options) + "）")
    return "".join(parts)


def _build_user_prompt(
    history: list[dict], profile_patch_accumulated: dict, followups: list[FollowupSpec]
) -> str:
```

把【本行业标准追问】那一段的最后一行：

```python
            + "\n".join(f"- {q}" for q in followups)
```

替换为：

```python
            + "\n".join(_render_followup_line(spec) for spec in followups)
```

- [ ] **Step 5: 改三条随类型变化的既有用例**

`tests/test_intake_agent.py` 里三处按字符串遍历 `FOLLOWUP_RULES` 的断言要改。

`test_matched_ecu_terms_inject_curated_followups_into_prompt` 末尾的两行替换为：

```python
    for spec in FOLLOWUP_RULES["嵌入式开发"]:
        assert spec.text in prompt, f"命中术语的领域追问 {spec.text!r} 没有进入 prompt"
        # 3.1 起 prompt 里还要带上目标字段与候选档位，否则模型只看得到问题文本、
        # 档位仍然要自己编——那正是决策 4 要堵的编造面。
        assert spec.field is None or spec.field in prompt
        for option in spec.options:
            assert option in prompt
```

`test_unmatched_text_does_not_inject_followups` 末尾两行替换为：

```python
    for spec in FOLLOWUP_RULES["驱动开发"]:
        assert spec.text not in prompt
```

`test_only_user_turns_are_matched_for_ambiguous_terms` 最后一行替换为：

```python
    assert FOLLOWUP_RULES["功能安全"][1].text not in prompt
```

- [ ] **Step 6: 跑测试确认通过**

```bash
./venv/bin/python -m pytest tests/test_ecu_knowledge.py tests/test_intake_agent.py -q
```

Expected: PASS，`tests/test_ecu_knowledge.py` 8 passed（原 3 条 + 新增 5 条）、`tests/test_intake_agent.py` 全绿。

- [ ] **Step 7: 跑全量回归**

```bash
./venv/bin/python -m pytest -q
```

Expected: `179 passed`。

- [ ] **Step 8: 提交**

```bash
git add app/agents/ecu_knowledge.py app/agents/intake_agent.py tests/test_ecu_knowledge.py tests/test_intake_agent.py && git commit -m "feat(intake): 领域选项库对象化，补齐采购侧词条与通用兜底档位"
```

---

### Task 3: `is_vague_reply` 确定性判定

**Files:**
- Modify: `app/agents/intake_agent.py`
- Test: `tests/test_intake_agent.py`

**Interfaces:**
- Consumes: `app.agents.intake_question.IntakeQuestion`（已导入）
- Produces:
  - `is_vague_reply(text: str, *, asked_questions: list[IntakeQuestion] | None = None) -> bool`
  - `_compact(text) -> str`（Task 5 的 `_value_matches_option` 复用同一个归一化面）
  - `MAX_TOTAL_ROUNDS = 8`（Task 6 用）

**这一步只加纯函数，不接线。** 接线在 Task 5。拆开是因为判定规则本身要能被单独证伪——它是整章的判据基础，混在接线里改，测试红了分不清是判定错了还是接线错了。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_intake_agent.py` 末尾：

```python
# ---------------------------------------------------------------------------
# 第 3 章：模糊回复兜底与预算口径
# ---------------------------------------------------------------------------

from app.agents.intake_agent import MAX_ROUNDS, MAX_TOTAL_ROUNDS, is_vague_reply  # noqa: E402
from app.agents.intake_question import IntakeQuestion  # noqa: E402


def test_is_vague_reply_hits_marker_words():
    assert is_vague_reply("这些我不太了解，你有什么建议")
    assert is_vague_reply("你决定吧")
    assert is_vague_reply("随便")
    assert is_vague_reply("不理解你想问的问题，我不知道怎么回答")


def test_is_vague_reply_accepts_real_answers():
    """误判的代价是多给一组选项，但真答案不该被判成模糊。"""
    assert not is_vague_reply("要 ASIL-D，必须有 AUTOSAR CP 经验")
    assert not is_vague_reply("招 2 个人，本科以上")


def test_is_vague_reply_empty_text_is_not_vague():
    """第一轮之前没有用户发言，不该被当成模糊回复而提前塞档位。"""
    assert not is_vague_reply("")
    assert not is_vague_reply("   ")


def test_is_vague_reply_detects_counter_question_without_clues():
    asked = [IntakeQuestion(text="要哪个 ASIL 等级？", question_id="functional_safety")]
    assert is_vague_reply("你们公司是干嘛的？", asked_questions=asked)


def test_is_vague_reply_does_not_flag_follow_up_question_that_shares_clues():
    """追着上一轮问细节是有信息的，不是反问。"""
    asked = [IntakeQuestion(text="要哪个 ASIL 等级？", question_id="functional_safety")]
    assert not is_vague_reply("ASIL 等级和量产项目有关系吗？", asked_questions=asked)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
./venv/bin/python -m pytest tests/test_intake_agent.py -q
```

Expected: FAIL —— `ImportError: cannot import name 'MAX_TOTAL_ROUNDS' from 'app.agents.intake_agent'`。

- [ ] **Step 3: 加常量**

在 `app/agents/intake_agent.py` 里，把 `MAX_ROUNDS = 5` / `MAX_QUESTIONS_PER_ROUND = 3` 那一段替换为：

```python
# 有产出轮的预算：只对 is_productive=1 的行计数（design.md 决策 5）。
MAX_ROUNDS = 5
# 总轮次硬上限：对 job_profile 总行数计数，让"零产出轮不消耗预算"不会把对话
# 拖成无限。任一命中即收尾。取 8 = 5 轮有产出 + 最多 3 轮空转（design.md
# Open Questions 里写明这个数字是拍的，上线后拿真实空转轮分布复核）。
MAX_TOTAL_ROUNDS = 8
MAX_QUESTIONS_PER_ROUND = 3
```

同时把 `import json` 下面那行 `from dataclasses import dataclass, field` 改为（`re` 与 `replace` 分别给本 Task 与 Task 5 用，一次到位）：

```python
import json
import re
from dataclasses import dataclass, field, replace
```

并把 `from app.schemas.job_profile import JobProfile` 改为：

```python
from app.schemas.job_profile import SYSTEM_MANAGED_FIELDS, JobProfile
```

把 `_SYSTEM_MANAGED_FIELDS = {"unspecified_fields"}` 那一行连同其上方注释替换为：

```python
# unspecified_fields 由系统在追问超限降级时填写，不该出现在给模型的字段表里，
# 否则模型会把它当成一个可以自己往 profile_patch 里塞的业务字段。
# 真源在 app/schemas/job_profile.py：intake_question 也要用同一份清单，
# 从这里导入会形成循环。
_SYSTEM_MANAGED_FIELDS = SYSTEM_MANAGED_FIELDS
```

- [ ] **Step 4: 实现判定函数**

在 `IntakeTurnResult` 定义之后、`def suggested_followups` 之前插入：

```python
# ---------------------------------------------------------------------------
# 模糊回复与反问的确定性判定（design.md 决策 3）
# ---------------------------------------------------------------------------

# 模糊表态词表。**硬编码的中文规则，会有漏判**（用户用没收录的说法表达"不知道"），
# 漏判的后果是退回今天的行为、不会更差；误判的后果是多给一组选项，也不致命——
# 但绝不允许影响 profile_patch 的写入（design.md 决策 3「代价」）。
_VAGUE_MARKERS: tuple[str, ...] = (
    "不知道",
    "不太了解",
    "不了解",
    "不清楚",
    "不确定",
    "没想好",
    "说不好",
    "不理解你想问",
    "不理解你的问题",
    "你决定",
    "您决定",
    "你看着办",
    "您看着办",
    "你定吧",
    "随便",
    "无所谓",
    "都行",
    "都可以",
    "你有什么建议",
    "有什么建议",
    "你觉得呢",
    "你说呢",
    "听你的",
    "看你的",
)

_QUESTION_MARKS = ("？", "?")

# 反问判定里要忽略的通用二字片段：它们在任何一句问句里都会出现，算作"线索"
# 会让反问判定几乎永远不触发。
_STOPWORD_BIGRAMS: frozenset[str] = frozenset(
    {
        "是否",
        "要求",
        "请问",
        "哪些",
        "什么",
        "这个",
        "那个",
        "需要",
        "可以",
        "具体",
        "岗位",
        "方面",
        "多少",
        "建议",
        "相关",
        "经验",
        "以及",
        "或者",
        "如果",
        "我们",
        "你们",
    }
)

_NON_WORD = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")


def _compact(text: str) -> str:
    """去掉全部空白与标点，只留中文/字母/数字。比对必须在同一个归一化面上做。"""
    return _NON_WORD.sub("", str(text))


def _bigrams(text: str) -> set[str]:
    compact = _compact(text)
    return {compact[i : i + 2] for i in range(len(compact) - 1)}


def _looks_like_counter_question(text: str, asked_questions: list[IntakeQuestion]) -> bool:
    """
    反问模式：用户回复以问号结尾，且**不含任何上一轮问题的线索**。

    "不含线索"用二字片段（bigram）交集判定：上一轮问「该岗位采购的『一般材料』
    指哪些品类」，用户回「一般材料是什么，你都不知道吗」——共享"一般/般材/材料"，
    这条判不成反问，靠 _VAGUE_MARKERS 的"不知道"命中；而「你们公司是做什么的？」
    跟上一轮毫无交集，判成反问。这样切能把"追着上一轮问细节"（有信息）和
    "把问题原样丢回来"（没信息）分开。
    """
    stripped = str(text).strip().rstrip("。！!」』\"'）) 　")
    if not stripped.endswith(_QUESTION_MARKS):
        return False
    clues: set[str] = set()
    for question in asked_questions:
        clues |= _bigrams(question.text)
    clues -= _STOPWORD_BIGRAMS
    return not (_bigrams(text) & clues)


def is_vague_reply(text: str, *, asked_questions: list[IntakeQuestion] | None = None) -> bool:
    """
    纯函数：这条用户回复是不是"没有给出可提取信息"。**不调模型**。

    spec「模糊回复与反问的兜底档位」明确要求"判定 MUST 是确定性的，不得只依赖
    模型自觉"——这次事故本身就是"提示词说了、模型没做"。

    空串返回 False 而不是 True：没有用户发言的场景（第一轮之前）不该被当成
    模糊回复，否则系统会在用户还没说话时就开始塞档位。
    """
    if not str(text).strip():
        return False
    compact = _compact(text)
    if any(_compact(marker) in compact for marker in _VAGUE_MARKERS):
        return True
    return _looks_like_counter_question(text, list(asked_questions or []))
```

- [ ] **Step 5: 跑测试确认通过**

```bash
./venv/bin/python -m pytest tests/test_intake_agent.py -q
```

Expected: PASS。

- [ ] **Step 6: 跑全量回归**

```bash
./venv/bin/python -m pytest -q
```

Expected: `184 passed`。

- [ ] **Step 7: 提交**

```bash
git add app/agents/intake_agent.py tests/test_intake_agent.py && git commit -m "feat(intake): 模糊回复与反问的确定性判定 is_vague_reply"
```

---

### Task 4: 已问台账列与 `is_productive` 判定

**Files:**
- Modify: `app/storage/db.py`（`SCHEMA` 与 `_ADDED_COLUMNS` **两处都要改**）
- Modify: `app/graph/state.py`
- Modify: `app/agents/intake_agent.py`（`IntakeTurnResult` 两个新字段、`run_intake_turn` 三个新入参与判定）
- Modify: `app/graph/nodes.py`
- Modify: `app/web/server.py`
- Test: `tests/test_db_migration.py`、`tests/test_intake_agent.py`、`tests/test_graph_nodes.py`、`tests/test_web_api.py`

**Interfaces:**
- Consumes: Task 1 的 `derive_question_id` 校验（本 Task 的判定正确性依赖它）
- Produces:
  - `job_profile.asked_questions TEXT NOT NULL DEFAULT '[]'`（`IntakeQuestion.to_payload()` 的 JSON 数组）
  - `IntakeState` 新键：`productive_round_count: int`、`asked_question_ids_before: list[str]`、`previous_questions: list[dict]`、`is_productive: bool`、`asked_questions: list[dict]`
  - `IntakeTurnResult.is_productive: bool = True`、`IntakeTurnResult.asked_questions: list[IntakeQuestion]`
  - `run_intake_turn(..., productive_round_count=None, asked_question_ids_before=None, previous_questions=None)`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_db_migration.py` 末尾：

```python
def test_asked_questions_column_defaults_to_empty_list_on_legacy_rows(tmp_path):
    """
    已问台账走的是 1.1 已经建立的幂等加列路径（delivery-units §5 约定 4）。
    历史行拿到 '[]'，读台账的代码不需要为老库写特例。
    """
    conn = _legacy_db(tmp_path)

    init_schema(conn)

    row = conn.execute(
        "SELECT asked_questions FROM job_profile WHERE id='old-job-v1'"
    ).fetchone()
    assert json.loads(row[0]) == []
```

追加到 `tests/test_intake_agent.py` 末尾：

```python
def test_turn_with_new_profile_field_is_productive():
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "招几个人？", "field": "headcount"}],
                    "profile_patch": {"job_title": "嵌入式工程师"},
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "要个嵌入式工程师"}],
        round_count=0,
    )

    assert result.is_productive is True
    assert [q.question_id for q in result.asked_questions] == ["headcount"]


def test_turn_with_nothing_new_is_not_productive():
    """画像与上一轮完全相同、问出的问题此前都问过 = 空转轮。"""
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "招几个人？", "field": "headcount"}],
                    "profile_patch": {"job_title": "嵌入式工程师"},
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "嗯"}],
        round_count=2,
        profile_patch_accumulated={"job_title": "嵌入式工程师"},
        asked_question_ids_before=["headcount"],
    )

    assert result.is_productive is False


def test_new_question_alone_makes_a_turn_productive():
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "工具链上有什么要求？", "field": "toolchain"}],
                    "profile_patch": {"job_title": "嵌入式工程师"},
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "没别的了"}],
        round_count=2,
        profile_patch_accumulated={"job_title": "嵌入式工程师"},
        asked_question_ids_before=["headcount"],
    )

    assert result.is_productive is True


def test_duplicate_question_ids_in_one_round_are_deduped():
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [
                        {"text": "要哪个 ASIL 等级？", "field": "functional_safety"},
                        {"text": "功能安全有要求吗？", "field": "functional_safety"},
                    ],
                    "profile_patch": {},
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "招个功能安全工程师"}],
        round_count=0,
    )

    assert [q.question_id for q in result.questions] == ["functional_safety"]
```

追加到 `tests/test_graph_nodes.py` 末尾：

```python
def _job1_conn(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    init_schema(conn)
    conn.execute("INSERT INTO job (id, title, status) VALUES ('job1', 't', 'drafting')")
    conn.commit()
    return conn


def test_persist_writes_is_productive_and_asked_questions_in_the_same_insert(tmp_path):
    """
    这一轮的画像、这一轮有没有产出、这一轮问了什么，是同一轮的三份事实。
    分开写就会出现"画像有这一轮、台账没这一轮"，而追问预算正是按后两列取数的。
    """
    conn = _job1_conn(tmp_path)
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "招几个人？", "field": "headcount"}],
                    "profile_patch": {"job_title": "嵌入式工程师"},
                }
            )
        ]
    )

    state = compute_intake_turn(
        {
            "job_id": "job1",
            "history": [{"role": "user", "content": "要个嵌入式工程师"}],
            "round_count": 0,
            "productive_round_count": 0,
            "profile_patch_accumulated": {},
            "asked_question_ids_before": [],
            "previous_questions": [],
        },
        gateway=gateway,
    )
    effect_persist_draft(conn, thread_id="job1", business_key="0", state=state)

    row = conn.execute(
        "SELECT is_productive, asked_questions FROM job_profile WHERE job_id='job1'"
    ).fetchone()
    assert row[0] == 1
    assert [item["question_id"] for item in json.loads(row[1])] == ["headcount"]


def test_persist_records_zero_productive_for_an_idle_turn(tmp_path):
    conn = _job1_conn(tmp_path)
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "招几个人？", "field": "headcount"}],
                    "profile_patch": {"job_title": "嵌入式工程师"},
                }
            )
        ]
    )

    state = compute_intake_turn(
        {
            "job_id": "job1",
            "history": [{"role": "user", "content": "嗯"}],
            "round_count": 2,
            "productive_round_count": 1,
            "profile_patch_accumulated": {"job_title": "嵌入式工程师"},
            "asked_question_ids_before": ["headcount"],
            "previous_questions": [],
        },
        gateway=gateway,
    )
    effect_persist_draft(conn, thread_id="job1", business_key="2", state=state)

    assert conn.execute(
        "SELECT is_productive FROM job_profile WHERE job_id='job1'"
    ).fetchone()[0] == 0
```

追加到 `tests/test_web_api.py` 末尾：

```python
def test_asked_questions_ledger_accumulates_across_turns(tmp_path):
    """已问台账是第 5 章重问追踪的载体，先在这里证明它真的按轮累积。"""
    import sqlite3

    responses = [
        json.dumps(
            {
                "is_job_related": True,
                "questions": [{"text": "招几个人？", "field": "headcount"}],
                "profile_patch": {"job_title": "嵌入式工程师"},
            }
        ),
        json.dumps(
            {
                "is_job_related": True,
                "questions": [{"text": "工具链上有什么要求？", "field": "toolchain"}],
                "profile_patch": {"headcount": 2},
            }
        ),
    ]
    client = make_app(tmp_path, responses)

    job_id = client.post("/api/jobs", json={"message": "要个嵌入式工程师"}).json()["job_id"]
    client.post(f"/api/jobs/{job_id}/reply", json={"message": "招 2 个"})

    conn = sqlite3.connect(str(tmp_path / "web.db"))
    rows = conn.execute(
        "SELECT asked_questions FROM job_profile WHERE job_id=? ORDER BY version ASC", (job_id,)
    ).fetchall()
    conn.close()

    ledger = [item["question_id"] for (raw,) in rows for item in json.loads(raw)]
    assert ledger == ["headcount", "toolchain"]
```

- [ ] **Step 2: 跑测试确认失败**

```bash
./venv/bin/python -m pytest tests/test_db_migration.py tests/test_intake_agent.py tests/test_graph_nodes.py tests/test_web_api.py -q
```

Expected: FAIL —— `sqlite3.OperationalError: no such column: asked_questions`，以及 `AttributeError: 'IntakeTurnResult' object has no attribute 'is_productive'`。

- [ ] **Step 3: 加列（`SCHEMA` 与 `_ADDED_COLUMNS` 两处）**

`app/storage/db.py`：把 `SCHEMA` 里 `job_profile` 表的最后一列

```sql
    llm_response_model TEXT
);
```

替换为

```sql
    llm_response_model TEXT,
    -- 本轮实际问出的问题（IntakeQuestion.to_payload() 的 JSON 数组）。
    -- 全部行的并集 = 这个 job 的"已问台账"：is_productive 判定要拿它算
    -- "有没有问出未问过的 question_id"（第 3 章），第 5 章在其上扩
    -- "已答 / 重问次数"。存整份 payload 而不是只存 id：候选档位要能回查，
    -- "用户没选定的档位不得入画像"这条判定需要知道上一轮给过哪些档位。
    asked_questions TEXT NOT NULL DEFAULT '[]'
);
```

并在 `_ADDED_COLUMNS` 元组末尾追加一行：

```python
    ("job_profile", "asked_questions", "TEXT NOT NULL DEFAULT '[]'"),
```

- [ ] **Step 4: 扩 `IntakeState`**

`app/graph/state.py`：把

```python
    round_count: int
    profile_patch_accumulated: dict
```

替换为

```python
    # job_profile 总行数。business_key 的口径，语义不变（design.md 决策 5）。
    round_count: int
    # is_productive=1 的行数。MAX_ROUNDS 按它计数，MAX_TOTAL_ROUNDS 按
    # round_count 计数，任一命中即收尾。两个都由 app/web/server.py 的 _run_turn
    # 从库里查出来传进来——预算计数器**不放进 state 自增**，那会引入第二个
    # 真源（design.md 决策 5 否决的替代方案）。
    productive_round_count: int
    profile_patch_accumulated: dict

    # 这个 job 此前所有轮次问过的 question_id 并集（已问台账），由 _run_turn
    # 从 job_profile.asked_questions 读出。compute_intake_turn 用它判定本轮
    # 有没有问出新问题。
    asked_question_ids_before: list[str]

    # 上一轮实际问出的问题（IntakeQuestion.to_payload() 的列表），同样由
    # _run_turn 从库里读。用途有二：反问判定要拿上一轮的问题文本当"线索"；
    # "候选档位不得代替用户做决定"要知道上一轮给过哪些档位。
    previous_questions: list[dict]
```

并把

```python
    is_complete: bool
    is_job_related: bool
    unspecified_fields: list[str]
```

替换为

```python
    is_complete: bool
    is_job_related: bool
    unspecified_fields: list[str]

    # 本轮是否有产出，由 compute_intake_turn 判定、effect_persist_draft 落进
    # job_profile.is_productive。
    is_productive: bool
    # 本轮实际问出的问题（payload 列表），与 is_productive 同一条 INSERT 落库。
    asked_questions: list[dict]
```

- [ ] **Step 5: 扩 `IntakeTurnResult` 并加去重**

`app/agents/intake_agent.py`：在 `IntakeTurnResult` 的 `llm_response_model: str | None = None` 之后追加：

```python
    # 本轮是否有产出（新画像内容 **或** 问出了未问过的 question_id）。
    # 由 effect_persist_draft 落进 job_profile.is_productive，追问预算按它计数
    # （design.md 决策 5）。默认 True：判定路径没接上时的行为与今天一致。
    is_productive: bool = True
    # 本轮实际问出的问题（已问台账的本轮增量），落进
    # job_profile.asked_questions。第 5 章在其上扩"已答 / 重问次数"。
    asked_questions: list[IntakeQuestion] = field(default_factory=list)
```

把 `_to_intake_questions` 整个函数替换为：

```python
def _to_intake_questions(raw: list[_IntakeQuestionSchema]) -> list[IntakeQuestion]:
    """
    模型侧形状 → 系统侧一等对象。question_id 在这里派生，模型给的 id 拿不到
    这一步（_IntakeQuestionSchema 里根本没有那个字段，pydantic 默认忽略多余键）。

    同一轮内 question_id 撞了就只留第一条。撞 id 的两条问题在下游是同一个问题
    （台账、is_productive 判定、第 5 章的重问追踪全按 id 走），留着第二条只会
    让"本轮问了几个问题"和"本轮问了几个 question_id"两个数对不上。
    """
    questions: list[IntakeQuestion] = []
    seen: set[str] = set()
    for item in raw:
        question = IntakeQuestion(
            text=item.text,
            question_id=derive_question_id(item.field, item.text),
            field=item.field or None,
            options=tuple(item.options),
            allow_free_text=item.allow_free_text,
        )
        if question.question_id in seen:
            continue
        seen.add(question.question_id)
        questions.append(question)
    return questions
```

- [ ] **Step 6: 给 `run_intake_turn` 加入参与判定**

把 `run_intake_turn` 的签名与开头两行替换为：

```python
def run_intake_turn(
    gateway: LLMGateway,
    *,
    history: list[dict],
    round_count: int,
    profile_patch_accumulated: dict | None = None,
    productive_round_count: int | None = None,
    asked_question_ids_before: list[str] | None = None,
    previous_questions: list[IntakeQuestion] | None = None,
) -> IntakeTurnResult:
    """
    round_count = job_profile 总行数（business_key 的口径，不变）。
    productive_round_count = is_productive=1 的行数；省略时退化成 round_count，
    保持"没接上判定前的行为与今天完全一致"。
    asked_question_ids_before / previous_questions 都由调用方从数据库读出来传入
    ——IntakeState 没有 reducer，真源是库（见 app/graph/state.py 的说明）。
    """
    accumulated = dict(profile_patch_accumulated or {})
    asked_before = list(asked_question_ids_before or [])
    prior_questions = list(previous_questions or [])
    productive_rounds = round_count if productive_round_count is None else productive_round_count

    user_prompt = _build_user_prompt(history, accumulated, suggested_followups(history))
```

（`prior_questions` 在本 Task 里还没有消费方，Task 5 接上；提前定义是为了 Task 5 只改函数体、不再改签名。）

把 `return IntakeTurnResult(` 之前的 `is_job_related=True` 那个 return 块前面加上判定，并把 return 块本身改掉。即把

```python
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

替换为

```python
    give_up = at_round_limit or stuck
    questions = [] if give_up else capped_questions

    profile_patch = parsed.profile_patch

    # 零产出轮判定（design.md 决策 5）：本轮 profile_patch 相对已累积内容有新
    # 字段或改了值，**或**问出了此前未问过的 question_id。两者都没有 = 空转，
    # 不消耗追问预算。
    has_new_profile_content = any(
        name not in accumulated or accumulated[name] != value
        for name, value in profile_patch.items()
    )
    has_new_question = any(question.question_id not in asked_before for question in questions)

    return IntakeTurnResult(
        is_job_related=True,
        questions=questions,
        profile_patch=profile_patch,
        is_complete=give_up or not questions,
        unspecified_fields=parsed.unspecified_fields if give_up else [],
        questions_text=render_questions_text(questions),
        llm_latency_ms=meta.latency_ms,
        llm_response_model=meta.response_model,
        is_productive=has_new_profile_content or has_new_question,
        asked_questions=questions,
    )
```

- [ ] **Step 7: 接线 `compute_intake_turn` 与 `effect_persist_draft`**

`app/graph/nodes.py`：在顶部 import 段的 `from app.agents.intake_agent import run_intake_turn` 之后加一行：

```python
from app.agents.intake_question import IntakeQuestion
```

把 `compute_intake_turn` 里的 `result = run_intake_turn(...)` 调用替换为：

```python
    round_count = state.get("round_count", 0)
    previous_questions = [
        IntakeQuestion.from_payload(item) for item in state.get("previous_questions", [])
    ]

    result = run_intake_turn(
        gateway,
        history=history,
        round_count=round_count,
        # 已累积的字段必须一起送进 prompt：SYSTEM_PROMPT 要求"不要重复历史已有
        # 字段"，模型看不见这份内容就无从遵守（review Critical 发现1）。
        profile_patch_accumulated=accumulated_before,
        # 预算的两个口径与已问台账都从 state 透传，真源是数据库（_run_turn 查
        # 出来放进 state），compute 节点自己不查库——它是 compute_*，纯函数。
        productive_round_count=state.get("productive_round_count", round_count),
        asked_question_ids_before=list(state.get("asked_question_ids_before", [])),
        previous_questions=previous_questions,
    )
```

把返回 dict 里的

```python
        "round_count": state.get("round_count", 0) + 1,
        "unspecified_fields": result.unspecified_fields,
```

替换为

```python
        "round_count": round_count + 1,
        "unspecified_fields": result.unspecified_fields,
        # 零产出轮判定与本轮台账增量，由 effect_persist_draft 与画像草案写在
        # 同一条 INSERT 里。
        "is_productive": result.is_productive,
        "asked_questions": [question.to_payload() for question in result.asked_questions],
```

`effect_persist_draft` 的 docstring 里，把

```
    is_productive / derived_unspecified_fields / ungrounded_fields /
    llm_response_model 这几列本单元**不写值**，靠列默认值成立（第 3、6、7 章
    各自接上）。
```

替换为

```
    2026-08-19（第 3 章）：is_productive 与 asked_questions 也进同一条 INSERT。
    它们和画像草案是同一轮的三份事实，分开写就会出现"这一轮的画像在、这一轮
    问过什么不在"——而追问预算正是按这两列取数的。

    derived_unspecified_fields / ungrounded_fields / llm_response_model 这三列
    仍然**不写值**，靠列默认值成立（第 6、7 章各自接上）。
```

把 `effect_persist_draft` 的 `conn.execute("INSERT INTO job_profile ...")` 整段替换为：

```python
    asked_questions_json = json.dumps(state.get("asked_questions", []), ensure_ascii=False)

    conn.execute(
        "INSERT INTO job_profile "
        "(id, job_id, version, status, profile_json, unspecified_fields, "
        "turn_started_at, llm_latency_ms, is_productive, asked_questions) "
        "VALUES (?, ?, ?, 'drafting', ?, ?, ?, ?, ?, ?)",
        (
            f"{thread_id}-v{version}",
            thread_id,
            version,
            profile_json,
            unspecified_json,
            state.get("turn_started_at"),
            state.get("llm_latency_ms"),
            # 默认 True：判定没接上时按"有产出"算，与列默认值和历史行一致。
            1 if state.get("is_productive", True) else 0,
            asked_questions_json,
        ),
    )
```

**不要新增 effect 节点、不要拆成第二条 INSERT。** 铁律 1：多一次写入就多一个能失败的地方，而这三份数据必须同生共死；`business_key` 仍是 `round_count`，幂等语义完全不变。

- [ ] **Step 8: 让 `_run_turn` 从库里读台账**

`app/web/server.py`：在 `round_count = conn.execute(...)` 那一段之后插入：

```python
        # 预算的第二个口径：只数有产出的轮次（design.md 决策 5）。空转轮不
        # 消耗 MAX_ROUNDS，但仍然占 round_count，因此仍受 MAX_TOTAL_ROUNDS 约束。
        productive_round_count = conn.execute(
            "SELECT COUNT(*) FROM job_profile WHERE job_id=? AND is_productive=1", (job_id,)
        ).fetchone()[0]

        # 已问台账：全部轮次的 question_id 并集 + 上一轮问出的问题原样读回。
        # 一次查询取两样东西，按 version 升序，最后一行就是上一轮。
        asked_rows = conn.execute(
            "SELECT asked_questions FROM job_profile WHERE job_id=? ORDER BY version ASC",
            (job_id,),
        ).fetchall()
        asked_question_ids_before: list[str] = []
        previous_questions: list[dict] = []
        for (raw,) in asked_rows:
            # 历史行（.51 上 2026-08-19 之前写的）这一列是默认值 '[]'；老库补列
            # 时也拿到 '[]'。两条路径都不需要回填。
            payloads = json.loads(raw or "[]")
            previous_questions = payloads
            for payload in payloads:
                question_id = payload.get("question_id")
                if question_id and question_id not in asked_question_ids_before:
                    asked_question_ids_before.append(question_id)
```

把构造 `state` 的 dict 改为：

```python
        state = {
            "job_id": job_id,
            "history": [*prior_history, {"role": "user", "content": message}],
            "round_count": round_count,
            "productive_round_count": productive_round_count,
            "profile_patch_accumulated": accumulated,
            "asked_question_ids_before": asked_question_ids_before,
            "previous_questions": previous_questions,
            "turn_started_at": turn_started_at,
        }
```

- [ ] **Step 9: 跑测试确认通过**

```bash
./venv/bin/python -m pytest tests/test_db_migration.py tests/test_intake_agent.py tests/test_graph_nodes.py tests/test_web_api.py -q
```

Expected: PASS。

- [ ] **Step 10: 跑全量回归**

```bash
./venv/bin/python -m pytest -q
```

Expected: `192 passed`。

- [ ] **Step 11: 提交**

```bash
git add app/storage/db.py app/graph/state.py app/graph/nodes.py app/agents/intake_agent.py app/web/server.py tests/test_db_migration.py tests/test_intake_agent.py tests/test_graph_nodes.py tests/test_web_api.py && git commit -m "feat(intake): 已问台账列与零产出轮判定 is_productive"
```

---

### Task 5: 兜底档位强制注入、候选档位不入画像、`prompt_version` 升 v4

**Files:**
- Modify: `app/agents/intake_question.py`（`render_questions_text` 渲染档位）
- Modify: `app/agents/intake_agent.py`
- Test: `tests/test_intake_question.py`、`tests/test_intake_agent.py`、`tests/test_web_api.py`（一条既有断言要改）

**Interfaces:**
- Consumes: Task 2 的 `fallback_options_for_field` / `FALLBACK_QUESTION_TEXT` / `FALLBACK_FIELD_ORDER`；Task 3 的 `is_vague_reply` / `_compact`；Task 4 的 `prior_questions` / `asked_before` 局部变量
- Produces: `run_intake_turn` 在模糊回复轮的行为变化（外部签名不变）

**这是整个单元里唯一改变用户可见行为的 Task。** 它同时落地三条 spec Requirement，reviewer 应当重点看这一个。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_intake_question.py` 末尾：

```python
def test_render_questions_text_includes_options_with_ai_disclosure():
    """
    档位在纯文本通道里也要看得见，且必须带 AI 建议标识
    （《AI 生成合成内容标识办法》）。第 4 章的可点选控件合并之前，这是用户
    唯一能看到档位的地方。
    """
    questions = [
        IntakeQuestion(
            text="要哪个 ASIL 等级？",
            question_id="functional_safety",
            field="functional_safety",
            options=("ASIL-B", "ASIL-D", "无要求"),
        )
    ]

    rendered = render_questions_text(questions)

    assert rendered.splitlines()[0] == "要哪个 ASIL 等级？"
    assert "AI 建议选项" in rendered
    for option in ("ASIL-B", "ASIL-D", "无要求"):
        assert option in rendered


def test_render_questions_text_omits_options_line_when_empty():
    questions = [IntakeQuestion(text="具体车型与量产时间是？", question_id="free:x")]
    assert render_questions_text(questions) == "具体车型与量产时间是？"
```

追加到 `tests/test_intake_agent.py` 末尾：

```python
def test_vague_reply_forces_options_onto_questions():
    """
    真实回放：`19b6ec6d` 第 4 轮。模型给了问题但没给档位（今天的行为），
    系统必须补上 2-3 个具体档位（spec「用户说不知道，系统给档位」）。
    """
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "工具链上有什么要求？", "field": "toolchain"}],
                    "profile_patch": {},
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway,
        history=[
            {"role": "user", "content": "要个底层软件开发工程师"},
            {"role": "assistant", "content": "工具链上有什么要求？"},
            {"role": "user", "content": "这些我不太了解，你有什么建议"},
        ],
        round_count=1,
    )

    assert result.questions
    for question in result.questions:
        assert 2 <= len(question.options) <= 3
        assert question.allow_free_text is True
    assert "AI 建议选项" in result.questions_text


def test_vague_reply_synthesizes_question_when_model_returns_nothing():
    """
    真实回放：`19b6ec6d` 第 4 轮模型实际回的是"我来帮您整理"式空话、一个问题
    都没问。这一轮必须由系统合成一个带档位的问题，否则 spec 的兜底在最需要它
    的那次直接落空。
    """
    gateway = make_gateway(
        [json.dumps({"is_job_related": True, "questions": [], "profile_patch": {}})]
    )

    result = run_intake_turn(
        gateway,
        history=[
            {"role": "user", "content": "要个底层软件开发工程师"},
            {"role": "user", "content": "这些我不太了解，你有什么建议"},
        ],
        round_count=1,
        profile_patch_accumulated={"job_title": "底层软件开发工程师"},
    )

    assert len(result.questions) == 1
    assert 2 <= len(result.questions[0].options) <= 3
    assert result.is_complete is False
    assert "我来帮您整理" not in result.questions_text


def test_counter_question_about_uncovered_domain_still_gets_options():
    """
    真实回放：`a478499c` 第 5 轮"一般材料是什么，你都不知道吗"。
    采购不在 ECU 知识库覆盖范围内，仍然必须给出档位——spec「领域外的字段也要
    有兜底」不允许因为知识库未命中而退回空话。
    """
    gateway = make_gateway(
        [json.dumps({"is_job_related": True, "questions": [], "profile_patch": {}})]
    )

    result = run_intake_turn(
        gateway,
        history=[
            {"role": "user", "content": "招个采购员"},
            {"role": "assistant", "content": "该岗位采购的「一般材料」指哪些品类？"},
            {"role": "user", "content": "一般材料是什么，你都不知道吗"},
        ],
        round_count=2,
        profile_patch_accumulated={"job_title": "采购员"},
    )

    assert result.questions
    assert all(2 <= len(q.options) <= 3 for q in result.questions)


def test_candidate_option_is_not_written_into_profile_when_user_defers():
    """
    合规红线：AI 不做决定。用户回"你决定吧"时，模型顺手把上一轮的候选档位写进
    profile_patch 的，必须被摘掉（spec「候选档位不得代替用户做决定」）。
    """
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [],
                    "profile_patch": {"functional_safety": "ASIL-D"},
                }
            )
        ]
    )
    previous = [
        IntakeQuestion(
            text="要哪个 ASIL 等级？",
            question_id="functional_safety",
            field="functional_safety",
            options=("ASIL-B", "ASIL-D", "无要求"),
        )
    ]

    result = run_intake_turn(
        gateway,
        history=[
            {"role": "user", "content": "招个功能安全工程师"},
            {"role": "assistant", "content": "要哪个 ASIL 等级？"},
            {"role": "user", "content": "你决定吧"},
        ],
        round_count=1,
        profile_patch_accumulated={"job_title": "功能安全工程师"},
        previous_questions=previous,
    )

    assert "functional_safety" not in result.profile_patch


def test_user_typed_option_is_kept_even_on_a_vague_turn():
    """"你决定吧，ASIL-D 也行"——用户自己打出了档位就是选定，不能摘。"""
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [],
                    "profile_patch": {"functional_safety": "ASIL-D"},
                }
            )
        ]
    )
    previous = [
        IntakeQuestion(
            text="要哪个 ASIL 等级？",
            question_id="functional_safety",
            field="functional_safety",
            options=("ASIL-B", "ASIL-D", "无要求"),
        )
    ]

    result = run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "你决定吧，ASIL-D 也行"}],
        round_count=1,
        previous_questions=previous,
    )

    assert result.profile_patch["functional_safety"] == "ASIL-D"


def test_misjudged_vague_reply_does_not_clear_extracted_fields():
    """
    design.md 风险表第 2 条：误判只影响"是否额外给一组选项"，绝不允许影响
    profile_patch 的写入。这里 is_vague_reply 会命中"都行"，但模型提取到的
    字段和上一轮给的候选档位无关，必须原样保留。
    """
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [],
                    "profile_patch": {"headcount": 2, "mcu_family": ["英飞凌 Aurix"]},
                }
            )
        ]
    )
    previous = [
        IntakeQuestion(
            text="要哪个 ASIL 等级？",
            question_id="functional_safety",
            field="functional_safety",
            options=("ASIL-B", "ASIL-D", "无要求"),
        )
    ]

    result = run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "随便哪个 MCU 都行，招 2 个"}],
        round_count=1,
        previous_questions=previous,
    )

    assert result.profile_patch == {"headcount": 2, "mcu_family": ["英飞凌 Aurix"]}


def test_prompt_version_is_intake_v4():
    """铁律 5：SYSTEM_PROMPT 改了就必须升版本。"""
    gateway = make_gateway(
        [json.dumps({"is_job_related": True, "questions": [], "profile_patch": {}})]
    )
    captured = {}
    original = gateway.extract_structured_with_meta

    def _spy(**kwargs):
        captured.update(kwargs)
        return original(**kwargs)

    gateway.extract_structured_with_meta = _spy
    run_intake_turn(gateway, history=[{"role": "user", "content": "要个工程师"}], round_count=0)

    assert captured["prompt_version"] == "intake-v4"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
./venv/bin/python -m pytest tests/test_intake_question.py tests/test_intake_agent.py -q
```

Expected: FAIL —— `assert "AI 建议选项" in rendered` 失败、`assert result.questions` 失败（模糊回复轮目前问不出任何东西）、`prompt_version` 仍是 `intake-v3`。

- [ ] **Step 3: `render_questions_text` 渲染档位**

`app/agents/intake_question.py`：在 `_REASK_PREFIX = "（这个你刚才没答）"` 之后插入：

```python
# 档位在纯文本通道里的呈现。"以下为 AI 建议"是《AI 生成合成内容标识办法》
# （2025-09-01 施行）的要求：候选档位是 AI 生成内容，渲染出来就必须带标识。
# 第 4 章给 Web 通道做可点选控件时同样要带标识（tasks 4.3），两处各自负责
# 自己的呈现形态，标识不能只做一处。
_OPTIONS_PREFIX = "可选（以下为 AI 建议选项，也可自由作答）："
_OPTIONS_SEPARATOR = " / "
```

把 `render_questions_text` 的 docstring 末段

```
    本单元不把 options 渲进文本：第 2 章的自我约束是"只换载体、用户可见行为
    与合并前一致"，选项的可点选控件与"AI 建议选项"标识属第 4 章
    （tasks 4.1/4.3，后者是《AI 生成合成内容标识办法》的要求）。
```

替换为

```
    2026-08-19（第 3 章）：options 开始渲进文本。第 2 章刻意没渲是因为那一章
    的自我约束是"用户可见行为与合并前一致"；第 3 章的整个目的就是让用户在
    对话里看见具体档位，**在第 4 章的可点选控件之前就要看得见**——纯文本通道
    （以及第 4 章合并前的 Web）否则拿不到任何档位，spec「用户说不知道，系统给
    档位」在文本侧就落空了。带 AI 建议标识，见 _OPTIONS_PREFIX。
```

并把函数体的循环替换为：

```python
    lines = []
    for question in questions:
        prefix = _REASK_PREFIX if question.is_reask else ""
        lines.append(f"{prefix}{question.text}")
        if question.options:
            lines.append(_OPTIONS_PREFIX + _OPTIONS_SEPARATOR.join(question.options))
    return "\n".join(lines)
```

- [ ] **Step 4: 加兜底注入与候选档位守卫**

`app/agents/intake_agent.py`：在 `_to_intake_questions` 之后、`_guidance_question` 之前插入：

```python
def _last_user_text(history: list[dict]) -> str:
    for turn in reversed(history):
        if turn.get("role") == "user":
            return str(turn.get("content", ""))
    return ""


def _fill_missing_options(questions: list[IntakeQuestion]) -> list[IntakeQuestion]:
    """
    命中模糊回复时的强制兜底：本轮下发的每个问题都必须带 options。

    模型给了就用模型的（它更懂当前话题），没给由系统从领域选项库补，库里没有
    就用该字段的通用档位——fallback_options_for_field 保证非空且 2-3 个
    （spec「领域外的字段也要有兜底」）。allow_free_text 一律保持 True：
    spec「选项之外的答案」要求不点选也能自由作答。
    """
    filled: list[IntakeQuestion] = []
    for question in questions:
        if question.options:
            filled.append(question)
            continue
        filled.append(
            replace(
                question,
                options=fallback_options_for_field(question.field),
                allow_free_text=True,
            )
        )
    return filled


def _has_value(value) -> bool:
    return value not in (None, "", [], {}, ())


def _synthesize_fallback_question(
    accumulated: dict, patch: dict, asked_question_ids_before: list[str]
) -> IntakeQuestion | None:
    """
    模糊回复那一轮模型一个问题都没给时，由系统合成一个带档位的问题。

    没有这一步，spec「用户说不知道，系统给档位」在模型返回空 questions 时就
    落空了——而那恰恰是最需要兜底的一次：`19b6ec6d` 第 4 轮就是模型回了一句
    "我来帮您整理"、没问出任何东西。

    优先挑**还没问过**的字段，让这一轮真的推进；全问过时退回第一个仍然没值的
    字段（这一轮会被判成零产出、不消耗预算，符合 spec「空转轮不计入预算」）。
    字段顺序取 FALLBACK_FIELD_ORDER，固定顺序保证同一份对话重跑问出同一个问题。
    """
    merged = {**accumulated, **patch}
    missing = [name for name in FALLBACK_FIELD_ORDER if not _has_value(merged.get(name))]
    if not missing:
        return None
    asked = set(asked_question_ids_before)
    target = next((name for name in missing if name not in asked), missing[0])
    text = FALLBACK_QUESTION_TEXT[target]
    return IntakeQuestion(
        text=text,
        question_id=derive_question_id(target, text),
        field=target,
        options=fallback_options_for_field(target),
    )


def _value_matches_option(value, option: str) -> bool:
    compact_option = _compact(option)
    if not compact_option:
        return False
    if isinstance(value, str):
        return _compact(value) == compact_option
    if isinstance(value, (list, tuple)):
        return any(isinstance(item, str) and _compact(item) == compact_option for item in value)
    return False


def _drop_unchosen_candidate_values(
    patch: dict, *, reply_text: str, previous_questions: list[IntakeQuestion]
) -> dict:
    """
    候选档位不得代替用户做决定（spec「候选档位不得代替用户做决定」）。

    用户回"你决定吧"时，模型有时会顺手把上一轮我们给出的某个候选档位直接写进
    profile_patch——那就是 AI 替业务经理做了决定。这里把这类字段摘掉。

    判据刻意收得很窄，三条同时成立才摘：
      (a) 本轮回复已被 is_vague_reply 判成模糊（调用方保证）；
      (b) 该字段的值**逐字等于**上一轮我们为这个字段给出的某个候选档位；
      (c) 该档位文本**没有**出现在用户这一轮的原话里。
    (c) 是给"你决定吧，ASIL-D 也行"留的门：用户自己打出了这个档位就是选定，
    不能摘。三条之外一律不动 patch——design.md 风险表第 2 条要求"误判时已提取
    字段不被清空"，这里是唯一允许删字段的地方，收窄到这个程度才不会和它冲突。
    """
    if not patch:
        return patch
    compact_reply = _compact(reply_text)
    cleaned = dict(patch)
    for question in previous_questions:
        name = question.field
        if not name or name not in cleaned:
            continue
        for option in question.options:
            if _value_matches_option(cleaned[name], option) and _compact(option) not in compact_reply:
                del cleaned[name]
                break
    return cleaned
```

- [ ] **Step 5: 在 `run_intake_turn` 里接线**

把

```python
    stuck = not at_round_limit and _repeats_earlier_assistant_turn(
```

**之前**插入：

```python
    reply_text = _last_user_text(history)
    vague = is_vague_reply(reply_text, asked_questions=prior_questions)
    if vague and not at_round_limit:
        capped_questions = _fill_missing_options(capped_questions)
        if not capped_questions:
            synthesized = _synthesize_fallback_question(
                accumulated, parsed.profile_patch, asked_before
            )
            capped_questions = [synthesized] if synthesized else []

```

注入放在 `stuck` 判定**之前**是刻意的：`_repeats_earlier_assistant_turn` 必须拿到与实际下发一模一样的文本（含档位行）才能继续成立，渲染入口唯一这条约束不能在这里破。

把 Task 4 留下的

```python
    profile_patch = parsed.profile_patch
```

替换为

```python
    profile_patch = (
        _drop_unchosen_candidate_values(
            parsed.profile_patch, reply_text=reply_text, previous_questions=prior_questions
        )
        if vague
        else parsed.profile_patch
    )
```

- [ ] **Step 6: 改 `SYSTEM_PROMPT` 并升 `prompt_version`**

把 `SYSTEM_PROMPT` 里【回答模糊/不知道时怎么办】整段替换为：

```python
    "【回答模糊/不知道时怎么办】如果用户的回复没有给出具体信息——比如"
    "「不知道」「你决定」「随便」「你有什么建议」这类模糊表态，或者把问题反问"
    "回来（「一般材料是什么，你都不知道吗」）——不要只回一句「我来帮你整理」"
    "这样的空话，那等于浪费一轮却什么都没问出来。这种情况下 questions 里必须"
    "给出 2-3 个具体的可选项（例如该细分领域行业内常见的档位或惯例做法），"
    "让用户下一轮回一个选项、「都要」或「随便选」就能推进，而不是继续面对一个"
    "自己答不出来的开放问题。**这一条不靠你自觉**：系统会用确定性规则判定"
    "模糊回复，你没给 options 时由系统从领域选项库补上。但 profile_patch 仍然"
    "只能放用户已经明确选定或确认的字段——不能因为用户说「你决定」，就自己把"
    "猜的值直接写进 profile_patch；画像里的要求必须由用户明确选定，不是模型"
    "代替业务经理做的决定。\n"
```

把 `prompt_version="intake-v3",` 连同其上方注释替换为：

```python
        # SYSTEM_PROMPT 改了就必须升版本：input_hash 与 prompt_version 是
        # "这条结果是哪一版提示词产出的"的唯一依据（铁律 5 的可解释性要求）。
        # intake-v3 → intake-v4：本轮改了 SYSTEM_PROMPT 的「回答模糊/不知道时
        # 怎么办」段（补反问场景与"系统会强制补 options"的说明）。提示词改了
        # 就必须升版本，否则 input_hash 与历史记录对不上（铁律 5）。
        prompt_version="intake-v4",
```

- [ ] **Step 7: 改一条随渲染变化的既有断言**

`tests/test_web_api.py` 的 `test_question_payload_carries_structured_questions` 末行

```python
    assert payload["questions_text"] == "要哪个 ASIL 等级？"
```

替换为

```python
    # 3.4 起 questions_text 把档位也渲出来：第 4 章的可点选控件合并之前，
    # 文本是用户唯一看得到档位的地方。标识是《AI 生成合成内容标识办法》要求的。
    assert payload["questions_text"] == (
        "要哪个 ASIL 等级？\n可选（以下为 AI 建议选项，也可自由作答）：ASIL-B / ASIL-D / 无要求"
    )
```

- [ ] **Step 8: 跑测试确认通过**

```bash
./venv/bin/python -m pytest tests/test_intake_question.py tests/test_intake_agent.py tests/test_web_api.py -q
```

Expected: PASS。

- [ ] **Step 9: 变异检查——证明这些测试不是摆设**

把 `run_intake_turn` 里的 `vague = is_vague_reply(...)` 临时改成 `vague = False`，再跑：

```bash
./venv/bin/python -m pytest tests/test_intake_agent.py -q
```

Expected: **4 failed** —— `test_vague_reply_forces_options_onto_questions`、`test_vague_reply_synthesizes_question_when_model_returns_nothing`、`test_counter_question_about_uncovered_domain_still_gets_options`、`test_candidate_option_is_not_written_into_profile_when_user_defers`。

看到这 4 条红了以后**把改动还原**再继续。少于 4 条说明有测试没真的咬住行为，回头修测试而不是往下走。

- [ ] **Step 10: 跑全量回归**

```bash
./venv/bin/python -m pytest -q
```

Expected: `201 passed`。

- [ ] **Step 11: 提交**

```bash
git add app/agents/intake_question.py app/agents/intake_agent.py tests/test_intake_question.py tests/test_intake_agent.py tests/test_web_api.py && git commit -m "feat(intake): 模糊回复强制兜底档位，候选档位不入画像，prompt 升 intake-v4"
```

---

### Task 6: 追问预算改为双口径

**Files:**
- Modify: `app/agents/intake_agent.py`（`at_round_limit` 一行）
- Test: `tests/test_intake_agent.py`、`tests/test_web_api.py`

**Interfaces:**
- Consumes: Task 3 的 `MAX_TOTAL_ROUNDS`、Task 4 的 `productive_rounds` 局部变量与 `is_productive` 落库
- Produces: 无新符号；`MAX_ROUNDS` 的语义从"总轮数"变为"有产出轮数"

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_intake_agent.py` 末尾：

```python
def test_budget_counts_productive_rounds_not_total_rounds():
    """
    7 个总轮次但只有 3 轮有产出时不该收尾——这正是"空转轮不消耗预算"要买到的
    东西（spec「空转轮不计入预算」）。
    """
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "工具链上有什么要求？", "field": "toolchain"}],
                    "profile_patch": {},
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "继续"}],
        round_count=7,
        productive_round_count=3,
    )

    assert result.questions
    assert result.is_complete is False


def test_total_round_cap_forces_wrap_up_even_with_no_productive_rounds():
    """spec「总轮次硬上限兜底」：连续零产出轮不能把对话拖成无限。"""
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "工具链上有什么要求？", "field": "toolchain"}],
                    "profile_patch": {},
                    "unspecified_fields": ["toolchain"],
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "不知道"}],
        round_count=MAX_TOTAL_ROUNDS,
        productive_round_count=0,
    )

    assert result.questions == []
    assert result.is_complete is True
    assert result.unspecified_fields == ["toolchain"]


def test_productive_round_limit_still_wraps_up():
    """MAX_ROUNDS 的既有行为不变：有产出轮吃满照样收尾。"""
    gateway = make_gateway(
        [
            json.dumps(
                {
                    "is_job_related": True,
                    "questions": [{"text": "工具链上有什么要求？", "field": "toolchain"}],
                    "profile_patch": {},
                }
            )
        ]
    )

    result = run_intake_turn(
        gateway,
        history=[{"role": "user", "content": "继续"}],
        round_count=MAX_ROUNDS,
        productive_round_count=MAX_ROUNDS,
    )

    assert result.questions == []
    assert result.is_complete is True
```

追加到 `tests/test_web_api.py` 末尾：

```python
def _idle_turn(n: int) -> str:
    """
    一轮空转：画像一字不变，问的还是同一个 question_id（field=toolchain），
    只是换了措辞。

    **必须换措辞**，不能逐字重复：`_repeats_earlier_assistant_turn` 会把逐字
    重复直接判成 stuck 并当场收尾，那一轮根本走不到预算判定。换措辞重问正是
    pilot 里真实发生的形态（采购岗 16949/26262，见 docs/m1-demo-pilot-feedback.md
    的调查第 2 条），也是这一章要处理的那种空转。该检测本身的去留归 5.8，
    本单元不动它。
    """
    return json.dumps(
        {
            "is_job_related": True,
            "questions": [{"text": f"工具链方面还有别的要求吗？（第 {n} 次问）", "field": "toolchain"}],
            "profile_patch": {"job_title": "嵌入式工程师"},
        }
    )


def test_idle_rounds_do_not_consume_the_followup_budget(tmp_path):
    """
    spec「空转轮不计入预算」的端到端证据：连跑 5 轮空转（总轮数已到 MAX_ROUNDS
    以上），对话仍然停在追问状态，业务经理没有因为空转而失去有效追问机会。
    """
    from app.agents.intake_agent import MAX_ROUNDS

    first = json.dumps(
        {
            "is_job_related": True,
            "questions": [{"text": "工具链上有什么要求？", "field": "toolchain"}],
            "profile_patch": {"job_title": "嵌入式工程师"},
        }
    )
    client = make_app(tmp_path, [first] + [_idle_turn(n) for n in range(MAX_ROUNDS)])

    body = client.post("/api/jobs", json={"message": "要个嵌入式工程师"}).json()
    job_id = body["job_id"]
    for _ in range(MAX_ROUNDS):
        body = client.post(f"/api/jobs/{job_id}/reply", json={"message": "嗯"}).json()

    assert body["message"]["type"] == "question"

    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "web.db"))
    total, productive = conn.execute(
        "SELECT COUNT(*), SUM(is_productive) FROM job_profile WHERE job_id=?", (job_id,)
    ).fetchone()
    assert total == MAX_ROUNDS + 1
    assert productive == 1  # 只有第一轮真的有产出
    conn.close()


def test_total_round_cap_ends_the_conversation(tmp_path):
    """spec「总轮次硬上限兜底」：空转到 MAX_TOTAL_ROUNDS 就进确认流程。"""
    from app.agents.intake_agent import MAX_TOTAL_ROUNDS

    first = json.dumps(
        {
            "is_job_related": True,
            "questions": [{"text": "工具链上有什么要求？", "field": "toolchain"}],
            "profile_patch": {"job_title": "嵌入式工程师"},
        }
    )
    client = make_app(tmp_path, [first] + [_idle_turn(n) for n in range(MAX_TOTAL_ROUNDS)])

    body = client.post("/api/jobs", json={"message": "要个嵌入式工程师"}).json()
    job_id = body["job_id"]
    for _ in range(MAX_TOTAL_ROUNDS):
        body = client.post(f"/api/jobs/{job_id}/reply", json={"message": "嗯"}).json()

    assert body["message"]["type"] == "confirmation_prompt"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
./venv/bin/python -m pytest tests/test_intake_agent.py tests/test_web_api.py -q
```

Expected: FAIL —— `test_budget_counts_productive_rounds_not_total_rounds` 断言 `result.questions` 为空（`round_count=7 >= MAX_ROUNDS=5` 触发了旧口径的收尾）。

- [ ] **Step 3: 改判据**

`app/agents/intake_agent.py`：把

```python
    at_round_limit = round_count >= MAX_ROUNDS
```

替换为

```python
    # 两个口径任一命中即收尾：有产出轮吃满 MAX_ROUNDS，或总轮数吃满
    # MAX_TOTAL_ROUNDS（后者是"零产出轮不消耗预算"的兜底，spec「总轮次硬上限
    # 兜底」）。
    at_round_limit = productive_rounds >= MAX_ROUNDS or round_count >= MAX_TOTAL_ROUNDS
```

**`business_key` 不动。** `app/graph/build.py` 的 `_persist_node` 仍然用 `state["round_count"] - 1`，幂等语义完全不变（design.md 决策 5）。

- [ ] **Step 4: 跑测试确认通过**

```bash
./venv/bin/python -m pytest tests/test_intake_agent.py tests/test_web_api.py -q
```

Expected: PASS。

- [ ] **Step 5: 跑全量回归**

```bash
./venv/bin/python -m pytest -q
```

Expected: `206 passed`。

- [ ] **Step 6: 提交**

```bash
git add app/agents/intake_agent.py tests/test_intake_agent.py tests/test_web_api.py && git commit -m "feat(intake): 追问预算改双口径，空转轮不消耗 MAX_ROUNDS"
```

---

### Task 7: 老库迁移演练、文档回填与 WBS 回勾

**Files:**
- Modify: `openspec/changes/m1-intake-quality-fixes/tasks.md`（勾第 3 章 3.1–3.11）
- Modify: `openspec/changes/m1-intake-quality-fixes/design.md`（Open Questions 里 `derive_question_id` 那条销号）
- Test: 无新测试，本 Task 是验证与收尾

- [ ] **Step 1: 在 `data/demo.db` 的副本上演练加列**

design.md Migration Plan 第 1 条要求的动作。**必须在副本上跑，不要动原库。**

```bash
mkdir -p /tmp/unitB-migration && cp data/demo.db /tmp/unitB-migration/demo.db && ./venv/bin/python -c "
from app.storage.db import get_connection, init_schema
conn = get_connection('/tmp/unitB-migration/demo.db')
init_schema(conn)
cols = {row[1] for row in conn.execute('PRAGMA table_info(job_profile)')}
print('asked_questions in cols:', 'asked_questions' in cols)
print('jobs:', conn.execute('SELECT COUNT(*) FROM job').fetchone()[0])
print('rows with default ledger:', conn.execute(\"SELECT COUNT(*) FROM job_profile WHERE asked_questions='[]'\").fetchone()[0])
print('productive count query works:', conn.execute('SELECT COUNT(*) FROM job_profile WHERE is_productive=1').fetchone()[0])
"
```

Expected: `asked_questions in cols: True`，`jobs:` 打出既有 job 数（历史数据一行不少），`rows with default ledger:` 等于 `job_profile` 总行数，最后一行不报错。

`data/demo.db` 不存在时跳过本步，并在提交信息里写明"本地无 demo.db，加列演练留到 8.3 在 `.51` 备份后做"。

- [ ] **Step 2: 清理演练目录**

```bash
rm -rf /tmp/unitB-migration
```

- [ ] **Step 3: 确认三级标题结构没被破坏**

```bash
grep -c '^### Task ' docs/superpowers/plans/2026-08-19-m1-intake-quality-fixes-unitB-fallback-options-and-budget.md
```

Expected: `7`。**不是 0**——`scripts/task-brief` 按三级标题抽取任务全文，二级标题会让它静默返回空 brief。

- [ ] **Step 4: 回勾 WBS**

`openspec/changes/m1-intake-quality-fixes/tasks.md` 第 3 章 3.1–3.11 全部由 `- [ ]` 改为 `- [x]`。

在 3.10 那一行末尾追加一句实施记录（取值理由要留痕，第 8 章复核 `MAX_TOTAL_ROUNDS` 时要看）：

```
（实施记录：`MAX_TOTAL_ROUNDS=8`，已把 2.4 拆分规则带来的槽位消耗算进去；口径为 `job_profile` 总行数，`MAX_ROUNDS` 改为对 `is_productive=1` 计数，`business_key` 不变）
```

在第 3 章标题下的附注段末尾追加：

```
> **实施记录（2026-08-19，单元 B）**：已问台账落在**新增列 `job_profile.asked_questions`**（`IntakeQuestion.to_payload()` 的 JSON 数组），走 1.1 的 `init_schema` 幂等加列路径。否决 `profile_json` 内部键的理由：`profile_json` 每轮被读回来当 `profile_patch_accumulated` 送进 prompt，台账放进去会每轮泄漏进 prompt 并污染 `input_hash`；`_jd_text` 能用那个位置是因为它只在 `confirm` 那一刻写、从不进 prompt。单元 E 的 5.1 直接在 `IntakeState.asked_question_ids_before` / `previous_questions` 两个键上扩。
```

- [ ] **Step 5: design.md 的 Open Questions 销号**

把 Open Questions 最后一条

```
- `derive_question_id` 未校验 `field` 是否属于 `JobProfile` schema，也没有 null-`field` 比例的监控指标——unit A 收尾复核记下，明确"第 5 章之前修"，本单元不实现。
```

替换为

```
- ~~`derive_question_id` 未校验 `field` 是否属于 `JobProfile` schema，也没有 null-`field` 比例的监控指标~~ → **✅ 2026-08-19 单元 B Task 1 已实现**：野 `field` 按"无 field"降级走 `free:` 哈希分支（不抛异常），`question_id_metrics()` 在进程内累计 `total` / `null_field` / `unknown_field` 及每个被拒字段名，供 8.1 回放看比例。之所以提前到第 3 章而不是"第 5 章之前"：3.9 让 `question_id` 第一次参与判定，野 `field` 会让每轮都产出"新" id、每轮都被判成有产出，`MAX_ROUNDS` 的有产出轮计数当场失效。
```

- [ ] **Step 6: 最后一次全量回归**

```bash
./venv/bin/python -m pytest -q
```

Expected: `206 passed`。

- [ ] **Step 7: 提交**

```bash
git add openspec/changes/m1-intake-quality-fixes/tasks.md openspec/changes/m1-intake-quality-fixes/design.md && git commit -m "docs(openspec): 回勾第 3 章——交付单元 B 完成"
```

---

## Self-Review：spec 覆盖矩阵

对应 `openspec/changes/m1-intake-quality-fixes/specs/intake-guided-options/spec.md`。

| Requirement / Scenario | 落在哪个 Task | 判据 |
|---|---|---|
| **模糊回复与反问的兜底档位** | | |
| 用户说不知道，系统给档位 | Task 3（判定）+ Task 5（注入） | `test_vague_reply_forces_options_onto_questions`、`test_vague_reply_synthesizes_question_when_model_returns_nothing` |
| 用户把问题反问回系统 | Task 3 + Task 5 | `test_counter_question_about_uncovered_domain_still_gets_options`、`test_is_vague_reply_detects_counter_question_without_clues` |
| 领域外的字段也要有兜底 | Task 2（`fallback_options_for_field` 三级取数）+ Task 5 | `test_fallback_options_never_empty_for_any_profile_field`、`test_fallback_options_for_unknown_field_include_a_negative_choice` |
| "判定 MUST 是确定性的，不得只依赖模型自觉" | Task 3 | `is_vague_reply` 是纯函数、不调模型；Task 5 Step 9 的变异检查证明它真的在起作用 |
| **候选档位不得代替用户做决定** | | |
| 未选定不入画像 | Task 5 | `test_candidate_option_is_not_written_into_profile_when_user_defers` |
| 选定后才入画像 | Task 5 | `test_user_typed_option_is_kept_even_on_a_vague_turn` |
| **零产出轮不消耗追问预算** | | |
| 空转轮不计入预算 | Task 4（判定与落库）+ Task 6（取数口径） | `test_turn_with_nothing_new_is_not_productive`、`test_idle_rounds_do_not_consume_the_followup_budget` |
| 有产出的轮次照常计数 | Task 4 | `test_turn_with_new_profile_field_is_productive`、`test_new_question_alone_makes_a_turn_productive` |
| 总轮次硬上限兜底 | Task 6 | `test_total_round_cap_forces_wrap_up_even_with_no_productive_rounds`、`test_total_round_cap_ends_the_conversation` |
| **结构化追问与可选项作答**（单元 A 已交付载体，本单元只补两处） | | |
| 选项渲染须标明是 AI 建议 | Task 5 | `test_render_questions_text_includes_options_with_ai_disclosure`（Web 可点选控件的标识归 tasks 4.3） |
| 无法给出有意义选项的问题只渲染自由文本 | Task 5 | `test_render_questions_text_omits_options_line_when_empty` |

**本单元不覆盖、也不该覆盖的**：「追问带可点选选项」「点选即可作答」两个 Scenario 的 Web 通道落地属第 4 章（单元 C，`index.html`）；`is_reask` 的判定属第 5 章（单元 E）。

## Self-Review：三处需要 reviewer 特别看的取舍

1. **`_repeats_earlier_assistant_turn` 与「空转轮不消耗预算」在"逐字重复"这一种形态上冲突。** 模型逐字重复上一轮问题时，`stuck` 会当场收尾，那一轮根本走不到预算判定——业务经理不但没保住预算，还直接被推进确认流程。本单元**不动它**（去留归 tasks 5.8，单元 A 已在 docstring 里把这个待办显式挂给 5.8），因此端到端验证用的是"换措辞重问"形态——那也正是 pilot 里真实发生的形态。**这条要在单元 E 的 5.8 一并了结，不要在这里顺手改。**

2. **`functional_safety` 的候选档位用「无要求」而不是枚举字面值「无」。** spec 的 Scenario 原文就是 `ASIL-B`、`ASIL-D`、`无要求`，档位是给人看的标签。用户选「无要求」后由模型映射回枚举值 `无`，映射失败会在 `POST /confirm` 撞 `JobProfile` 校验、走既有的 422 路径（有说明是哪个字段、期望什么），不会静默写错。按决策 12「本批只观测不拦截」，这个残余风险接受。

3. **`_synthesize_fallback_question` 用的是 `FALLBACK_QUESTION_TEXT`，第 6 章的 6.4 会另引入一份「字段中文名」映射。** 两份不是重复：这份是给人**回答**的问句，6.4 那份是给人**看**的字段名。已在 `ecu_knowledge.py` 里写了注释互相指认；6.4 落地时若判断可合并，由单元 D 决定，不在本单元预先合。
