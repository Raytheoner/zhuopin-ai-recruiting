# AI 留痕与外发门禁 · 交付单元 U3（留痕接线）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `app/audit`（U2 已交付）真正接到 LLM 网关上——`AuditHook` Protocol 扩参、实现 `RecorderAuditHook` 适配器、在 `app/main.py` 单点注入、并把 `criterion_key` 白名单集中定义一处并强制。合并后工程铁律 3、4 从"钩子留着"变成真实生效。

**Architecture:** 网关继续对业务无知：`extract_structured*()` 新增可选 `audit_context`，**原样透传给钩子、不解释内容**（design D6）。适配层 `RecorderAuditHook` 是唯一知道招聘语义的地方，它把网关的扁平参数折成 `DecisionEvent` 再交给 `AuditRecorder`。**适配器持有一条专属的 SQLite 连接并自己提交**——理由见 §「一处必须自己定的架构决定」。白名单落成 `app/audit/criteria.py` 一处定义，强制点在 `CriterionScore.__post_init__`，任何写入路径都绕不过。

> ✅ **`criterion_key` 口径已于 2026-08-28 拍板取 A（评分维度）**，五个 Task 全部无阻塞可开工。依据与被否决的两个口径见文末「口径决定」。

**Tech Stack:** Python 3.14.6（`./venv`）· 标准库 `sqlite3` / `uuid` / `logging` / `ast` · pytest 8.3.4 · **不引入任何新依赖**（`requirements.txt` / `pyproject.toml` diff 必须为空）

---

## 一处必须自己定的架构决定（tasks.md 与 delivery-units.md 都没写，reviewer 必须确认）

**问题**：`AuditRecorder.record(conn, event)` 要求调用方把**事务所在的那条连接**原样传进来，并断言它与 sink 绑定的是同一个对象（`app/audit/recorder.py:86-91`，工程铁律 1）。但 `AuditHook.record()` 的触发点在 `LLMGateway.extract_structured_with_meta()` 内部（`app/llm/gateway.py:245-254`），**网关手里没有任何 `conn`**。两个真实调用点的形状还不一样：

| 调用点 | 事务状态 | 证据 |
|---|---|---|
| `compute_intake_turn` → `run_intake_turn` → `gateway.extract_structured_with_meta()` | **完全不在事务里**。`compute_*` 是纯函数节点，不碰库 | `app/graph/nodes.py:17-18` 的 docstring「纯函数，只调用 LLM 与做数据转换，不写库」；`app/agents/intake_agent.py:972` |
| `effect_generate_and_persist_jd` → `generate_jd()` → `gateway.extract_structured()` | 在事务里，但 `conn` 在那个栈帧里，没传进网关 | `app/graph/nodes.py:237`、`app/agents/jd_agent.py:69` |

三个可选方案，逐一评估：

| 方案 | 做法 | 结论 |
|---|---|---|
| **(A) 适配器自持一条专属连接，自己 commit** | `RecorderAuditHook` 在构造时拿一条 `get_connection(db_path)`，`record()` 写完立刻 `commit()`，再 `mirror()` | ✅ **采用** |
| (B) 钩子只缓存事件，由 `effect_*` 节点在自己的事务里 flush | 语义最贴 U2 的两段式 | ⛔ **否决**：要改 `app/graph/nodes.py` 与 `app/agents/intake_agent.py` 两个调用点，超出 U3 的文件边界（`delivery-units.md:24` 给 U3 的文件是 `gateway.py`｜`main.py`｜`test_llm_gateway.py`），且 `compute_*` 节点拿不到 `conn`——它按定义就不该拿到 |
| (C) 复用全应用共享的那条连接，不 commit | 改动最小 | ⛔ **否决，且这一条是陷阱**：`app/storage/idempotency.py:41-68` 明写，被装饰函数抛异常时装饰器会 `conn.rollback()`——留痕行会**被一起回滚掉**，而那次 LLM 调用是真的发生过、真的花了钱。spec「留痕写入失败 MUST NOT 被静默忽略」在这条路径上会变成"留痕被静默撤销"。另一半同样糟：不回滚时留痕行悬在隐式事务里，由**下一个不相关的 effect** 的 `conn.commit()` 顺手提交（`idempotency.py:42-47` 逐字描述了这个失败模式） |

**(A) 为什么不违反工程铁律 1**。铁律 1 的原文约束是「幂等记录与业务写必须在同一个事务里提交……且该连接上不得存在**第二个事务管理者**」。专属连接上的事务管理者**只有适配器自己一个**，不存在第二个。且本仓库已经在跑"两条连接写同一个库文件"的形态——`app/storage/db.py:245-253` 逐字写明 checkpointer 与 effect 层各持一条独立连接，并因此把 `journal_mode=WAL` 与 `busy_timeout=5000` 设成了连接默认值。审计连接走同一个 `get_connection()`，自动继承这两条 PRAGMA。

**(A) 的语义后果，是对的那一侧**：业务事务回滚时，留痕行仍在。这不是偏差——**那次 AI 调用真的发生过**，留痕记录它是事实陈述。反过来（调用发生了但没有留痕）才是 spec 禁止的。

**⚠️ 需 reviewer 确认**：本决定改变了「`record()` 的 `conn` 由业务事务提供」这个 U2 计划里的默认想象。U2 的实现没有阻止它（`recorder.py:86-91` 只断言 conn 与 sink 绑定的是同一对象，不关心那是谁的事务），所以是"U2 未规定、U3 定死"，不是"U3 推翻 U2"。

---

## Global Constraints

以下条目从 `CLAUDE.md`「工程铁律」「合规红线」、`delivery-units.md` §2.U3 / §4、`design.md` D1/D2/D6/D7、本变更包 `specs/ai-decision-audit/spec.md`，以及 U1/U2 的落地真值**逐字复制或按 `file:line` 引用**。**每个 Task 的验收隐含包含本节全部内容。**

### 头号约束：注入点只有一处，且不在 `create_app()`

> `delivery-units.md` §2.U3 逐字：「**实际构造 `LLMGateway` 的不是 `create_app()`，是 `app/main.py:18` 的 `_gateway_factory()`**……**U3 的注入点写死在 `app/main.py:_gateway_factory()`，不改 `create_app` 签名。** 回滚 = 换回一行。」

`tasks.md` 3.3 的字面写的是「生产装配处（`create_app()`）注入」，**以 `delivery-units.md` 为准**。实测佐证：`app/web/server.py:54` 的签名是 `create_app(*, db_path, gateway_factory: Callable, root_path)`，它自己不 `new` gateway。改 `create_app` 签名会立刻与 M1 的 B/D 单元串行（§3.2）。

**reviewer 机械判据**：U3 的 diff 里 `app/web/server.py` **必须为空**。

### 第二条：`RecorderAuditHook` 必须一次性构造，不能每次 `_gateway_factory()` 都新建

**实测**：`gateway_factory()` 在 `app/web/server.py` 被调用**两处**——启动时 `:66`，以及每次请求 `:278`。若把 `get_connection()` 写进 `_gateway_factory()` 函数体，**每个 HTTP 请求泄漏一条 SQLite 连接**。落地形态：recorder 与 hook 在 `app/main.py` 模块级构造一次，`_gateway_factory()` 闭包引用它。

**reviewer 机械判据**：`app/main.py` 里 `get_connection(` 不出现在 `def _gateway_factory` 的函数体内（Task 4 有 AST 守护）。

### 第三条：双写的失败语义是**不对称**的，⛔ 不许写成对称

- **SQLite 写失败 → 抛，不吞**。spec 逐字：「留痕写入失败 MUST NOT 被静默忽略：留痕写入失败时系统 SHALL 视该次 AI 结果为不可用，其评分 MUST NOT 进入下游排序。」异常穿透出网关 = 该次评分拿不到结果 = 进不了下游，这是唯一自洽的落地。
- **JSONL 镜像写失败 → 只记日志，⛔ 不抛**。`design.md` D1 与 `delivery-units.md` §3.4 第 3 条：允许的偏差**只有单向**——「SQLite 有、JSONL 缺行」（真身完整、镜像缺证据）。镜像失败就把整次调用打挂，等于把一个被明确允许的偏差升级成故障。缺行由 `AuditRecorder.reconcile()` 检出、`backfill()` 在链尾补录（U2 已交付）。

把这两条写成对称（都抛或都吞）是本 Task 最容易犯的错，Task 3 有两条方向相反的测试各钉一侧。

### 第四条：⛔ 禁止在 `effect_*` 函数体内 append JSONL

`delivery-units.md` §3.4 第 2 条。U2 已落 AST 守护 `tests/test_audit_recorder.py:158 test_no_effect_function_appends_jsonl`（带三分支阳性对照）。`RecorderAuditHook.record` 不叫 `effect_*`，守护天然为绿——**Task 3 完成后必须重跑这条确认它仍绿**，它是这条约束在 U3 的唯一自动判据。

### 第五条：`app/audit/` ⛔ 不得 import `app.config` / `app.graph`

U2 已落 AST 守护（`tests/test_audit_recorder.py:446`）。**但该守护当前参数化在一个硬编码列表 `["events", "sinks", "recorder"]` 上（`:445`）——U3 新增的 `hook.py` 与 `criteria.py` 不在其中，守护对新文件是瞎的。** Task 1 必须把它改成目录扫描，否则 U3 新增的两个文件可以随便 import `app.config` 而守护全绿。

落地形态：路径与连接一律由 `app/main.py` 传入，`app/audit/` 自己不读配置。

### 工程铁律（不可违背，逐字）

3. **所有 AI 评分必须持久化**：模型标识 + 模型版本 + prompt 版本 + temperature + 输入哈希 + rubric 快照 + 原始响应。

> **U3 是这条从"钩子留着"变成"真实生效"的那一步。** 注意 `analysis_run.temperature` 是 **NOT NULL**（`app/storage/db.py:102`），而**现行 `AuditHook.record()` 的签名里根本没有 temperature**（`gateway.py:114-126`）——不补这个参数，U3 第一条真实写入就会撞 NOT NULL。见偏离登记 2。

4. **每条 `criterion_score` 必须有 `evidence_ref`**。U1 已做成数据库 `CHECK`，U2 已做到"不吞 IntegrityError"。**U3 在这条上不新增代码**，只新增白名单这一侧（铁律 4 的邻居，不是它本身）。

5. **`temperature=0`；模型版本优先显式锁定**，禁止 `latest` 类别名；**必须从 API 响应里取回实际的 `model` 字段并持久化**——配置里写的名字不算数，响应返回的才算。

> **U3 的落点**：`configured_model` 与 `response_model` **分两字段各自保存、不互相覆盖**（tasks 3.5，spec `Scenario: 一次评分调用完成` 逐字）。`system_fingerprint` 缺失时记空值且留痕照常写入，**不让网关炸掉**（tasks 3.6，spec `Scenario: 供应商不返回部署指纹`）。

### 合规红线

- **禁止人脸/表情分析**（《人脸识别技术应用安全管理办法》2025-06-01 施行）。声学情绪信号（语速/停顿/静默）只展示给面试官，**不进 `criterion_score`**。

> **这是 tasks 3.4 / 3.7 的红线出处。** 白名单必须**集中在一处 Python 定义**里（`design.md` Risks 最后一条：「白名单集中在一处定义，加维度是一行改动 + 一次 review」），散成两处会出现"一处放行一处拒绝"的分叉，分叉的那一侧就是红线的缺口。
> ⚠️ **强制必须走白名单（未知即拒），不能走黑名单**。黑名单对"没想到的新维度"默认放行，正好是红线要防的方向。Task 1 有一条用**既不在白名单也不在黑名单**的编造维度做的测试专钉这一点。

- **AI 只做排序推荐，不做自动淘汰**：U3 不涉及（断言在 U6）。
- **绝不用历史录用结果做监督信号**：U2 已在 `app/audit/events.py` 模块 docstring 与 `analysis_run` 表注释各写一遍。**U3 新增的 `criteria.py` / `hook.py` 不重复写**，避免同一条声明散成四处后互相漂移。

### spec 的一条硬要求（`specs/ai-decision-audit/spec.md` 逐字）

> 系统 MUST NOT 在留痕记录中存储简历原文。输入内容以哈希形式记录。

**U3 的落点**：`audit_context` 是本变更唯一新增的"调用方能往留痕里塞东西"的通道，它必须**拒收未登记的键**（Task 3）——否则将来有人往里塞一个 `resume_text` "方便排查"，它会一路流进 JSONL 镜像。⛔ 未登记的键一律抛，不是忽略：忽略等于静默丢数据，抛才能让写错的人当场知道。

### 范围边界（U3 **不做**什么）

| 事项 | 归属 |
|---|---|
| 把 `audit_context` 真正接到 intake / jd 两条业务路径上 | ⛔ 不做。要改 `app/graph/nodes.py` 与 `app/agents/intake_agent.py`，超出 `delivery-units.md:24` 给 U3 的文件边界。U3 只保证**通道通**（网关透传 + 适配器解析 + 端到端测试），业务侧填值另开单元 |
| 删 `job_profile.turn_started_at` / `llm_latency_ms` 两列 | ⛔ 不做，只标注触发条件已满足（Task 5）。改 `.51` 现网库的表结构属生产决定，不可代 |
| `criterion_score` 的实际写入 | 本变更无写入方（评分在 M2）。U3 只保证白名单强制点在位 |
| 合规断言与 CI | U6（第 6 章） |
| 门禁、`pending_approval` | U4 / U5 |

### 并发与操作约束

- ⛔ 不碰 `app/graph/nodes.py`、`app/agents/intake_agent.py`、`app/web/server.py`、`docs/openers/run-batch.sh`、`docs/openers/run-lanes.sh`。
- ⛔ 不碰 `app/outbound/`、`app/config.py`（U4 的并行分支正在这两处，`delivery-units.md` §4 约定 1：U3 与 U4 只读不写 `config.py`）。
- ⛔ 不修改 `openspec/changes/ai-audit-trail-and-outbound-gate/tasks.md`——回勾与偏离登记在 U3 全部 Task 完成、且确认无并行 session 在写该文件之后单独一笔。
- 遇到 `.git/index.lock`：先等 5 秒重试，最多 5 次；仍不行才看孤儿锁三项判据，**判据 3 用 `pgrep -x git`，⛔ 不要用 `-f`**。
- 每个 Task 结束提交一笔，⛔ 不 push、不开 run-build。

---

## File Structure

| 文件 | 动作 | 职责 | Task |
|---|---|---|---|
| `app/audit/criteria.py` | **新建** | `criterion_key` 白名单的**唯一定义**与 `validate_criterion_key()`。不 import 任何 app 内模块 | 1 |
| `app/audit/events.py` | 修改 | `CriterionScore.__post_init__` 调用白名单校验（唯一强制点） | 1 |
| `app/audit/hook.py` | **新建** | `RecorderAuditHook`：`AuditHook` 扁平参数 → `DecisionEvent` → `AuditRecorder`。自持连接、自己 commit、两段式、失败语义不对称 | 3 |
| `app/audit/__init__.py` | 修改 | 导出 `RecorderAuditHook` / 白名单符号 | 1、3 |
| `app/llm/gateway.py` | 修改 | `AuditHook` Protocol 扩参；`temperature` 收成单一真源；`extract_structured*` 透传 `audit_context`；`NoopAuditHook` 注释改「测试专用」 | 2 |
| `app/main.py` | 修改 | 模块级构造 recorder + hook，`_gateway_factory()` 闭包注入。**唯一注入点** | 4 |
| `docs/tech-debt.md` | 修改 | TD-1 标注「触发条件已满足，删列另开变更」 | 5 |
| `tests/test_audit_criteria.py` | **新建** | 白名单：红线维度被拒、未知维度被拒（fail-closed）、强制点在构造期 | 1 |
| `tests/test_audit_recorder.py` | 修改 | 把 import 守护从硬编码三文件改成目录扫描 | 1 |
| `tests/test_llm_gateway.py` | 修改 | 扩参后的透传、per-attempt 语义、旧的签名锁定测试同步更新 | 2 |
| `tests/test_audit_hook.py` | **新建** | 适配器：字段映射、失败语义不对称、id 规则、`audit_context` 拒收未登记键 | 3 |
| `tests/test_main_wiring.py` | **新建** | 注入点唯一、不每次新建连接、`create_app` 未被改签名 | 4 |
| `tests/test_audit_end_to_end.py` | **新建** | 3.5 / 3.6 / 3.7 的端到端验收 | 5 |

---

### Task 1: `criterion_key` 白名单——一处定义、构造期强制、守护不再对新文件瞎

**Files:**
- Create: `app/audit/criteria.py`
- Modify: `app/audit/events.py`（`CriterionScore.__post_init__`）
- Modify: `app/audit/__init__.py`（导出）
- Create: `tests/test_audit_criteria.py`
- Modify: `tests/test_audit_recorder.py:443-455`（import 守护从硬编码三文件改成目录扫描）

**Interfaces:**
- Consumes: U2 已交付的 `CriterionScore`（`app/audit/events.py:44-70`，`frozen=True` dataclass，字段 `criterion_key: str` / `score: float` / `evidence_ref: str` / `id: str | None = None`）
- Produces:
  - `app.audit.criteria.CRITERION_KEY_WHITELIST: frozenset[str]`
  - `app.audit.criteria.ForbiddenCriterionKey(ValueError)`
  - `app.audit.criteria.validate_criterion_key(key: str) -> str`（合法则原样返回，非法抛 `ForbiddenCriterionKey`）
  - Task 5 的端到端测试依赖「构造 `CriterionScore` 时即拒」这个时机

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_audit_criteria.py`：

```python
"""
criterion_key 白名单。合规红线：声学情绪信号（语速/停顿/静默）只展示给面试官，
不进 criterion_score；人脸/表情类维度禁止出现在任何评分项中
（《人脸识别技术应用安全管理办法》2025-06-01 施行）。

⚠️ 本文件里的维度名**全部写成字面量**，⛔ 不从 CRITERION_KEY_WHITELIST 里派生。
从常量派生的测试会随常量一起变——把 "facial_expression" 加进白名单，派生式断言
跟着放宽、全绿，而红线已经破了。字面量是这条测试与常量之间唯一的独立支点。
"""

import pytest

from app.audit.criteria import (
    CRITERION_KEY_WHITELIST,
    ForbiddenCriterionKey,
    validate_criterion_key,
)
from app.audit.events import CriterionScore


@pytest.mark.parametrize(
    "key",
    ["speech_rate", "pause_duration", "silence_ratio", "speech_tempo", "voice_emotion"],
)
def test_acoustic_emotion_dimensions_are_rejected(key):
    """合规红线：声学情绪信号只展示给面试官，不进评分项。"""
    with pytest.raises(ForbiddenCriterionKey):
        validate_criterion_key(key)


@pytest.mark.parametrize(
    "key",
    ["facial_expression", "micro_expression", "face_match", "emotion_score", "gaze_stability"],
)
def test_biometric_dimensions_are_rejected(key):
    """合规红线：生物特征类维度（人脸、表情）禁止出现在任何评分项中。"""
    with pytest.raises(ForbiddenCriterionKey):
        validate_criterion_key(key)


def test_an_unregistered_dimension_is_rejected_too():
    """
    ⭐ fail-closed 的分水岭。上面两条只证明"已知的坏维度被拦下"——那是黑名单
    也能做到的事。这条用一个**既不在白名单、也不在任何黑名单示例里**的编造维度，
    断言它同样被拒：只有"未登记即拒绝"的实现能让它变红，黑名单实现会放行。

    红线要防的正是"没想到的新维度"——想得到的那些本来就写在文档里了。
    """
    with pytest.raises(ForbiddenCriterionKey):
        validate_criterion_key("candidate_vibe_index_v3")


def test_a_registered_dimension_passes_and_is_returned_unchanged():
    assert validate_criterion_key("skill_match") == "skill_match"


@pytest.mark.parametrize(
    "red_line_key",
    [
        "speech_rate",
        "pause_duration",
        "silence_ratio",
        "facial_expression",
        "micro_expression",
        "emotion_score",
    ],
)
def test_whitelist_itself_contains_no_red_line_dimension(red_line_key):
    """
    直接钉常量的内容，不经过 validate_criterion_key()。
    将来有人"为了跑通某个 demo"把 facial_expression 加进白名单，
    validate_criterion_key() 会老老实实放行、上面的用例全部变绿——只有这条会红。
    """
    assert red_line_key not in CRITERION_KEY_WHITELIST


def test_rejection_happens_at_construction_not_at_write_time():
    """
    强制点在 CriterionScore 构造期，不是 sink 的写入期。这决定了**所有**写入
    路径（U5 的 queue、U6 的断言、将来 M2 的评分器）都绕不过去——它们连一个
    非法的 CriterionScore 对象都造不出来。
    """
    with pytest.raises(ForbiddenCriterionKey):
        CriterionScore(
            criterion_key="facial_expression",
            score=0.9,
            evidence_ref="interview-1#10-20",
        )


def test_a_legal_criterion_score_still_constructs():
    """阴性对照：别把校验写成"所有 key 都拒"，那样上面全部变绿而功能全死。"""
    score = CriterionScore(
        criterion_key="skill_match",
        score=0.8,
        evidence_ref="resume-1#120-180",
    )

    assert score.criterion_key == "skill_match"
```

- [ ] **Step 2: 跑测试确认它失败**

```bash
./venv/bin/python -m pytest tests/test_audit_criteria.py -q
```

预期：**collection error**，`ModuleNotFoundError: No module named 'app.audit.criteria'`。

- [ ] **Step 3: 写白名单模块**

创建 `app/audit/criteria.py`：

```python
"""
`criterion_key` 白名单——本仓库对"什么可以作为评分维度"的**唯一定义**。

⚠️ **强制方式是白名单（未登记即拒绝），不是黑名单。** 黑名单对"没想到的新维度"
默认放行，而红线要防的恰恰是没想到的那些。下面的 `RED_LINE_EXAMPLES` **只参与
报错信息**，不参与放行判定——它存在的意义是让被拦下的人立刻看懂"这不是漏配，
是红线"，而不是去提 PR 把维度加进白名单。

**加一个维度 = 改这里一行 + 一次 review**（design.md Risks 最后一条）。⛔ 不要
在任何别的地方再写第二份判定：散成两处就会出现"一处放行一处拒绝"的分叉，而分叉
的那一侧就是红线的缺口。

合规依据：
- 声学情绪信号（语速、停顿、静默时长）只允许展示给面试官参考，MUST NOT 作为
  评分项写入（specs/ai-decision-audit 「评分项白名单约束」）。
- 生物特征类维度（人脸、表情）MUST NOT 出现在任何评分项中
  （《人脸识别技术应用安全管理办法》2025-06-01 施行）。
"""

from __future__ import annotations


class ForbiddenCriterionKey(ValueError):
    """试图把一个未登记的维度写成评分项。"""


# 已登记的评分维度。⛔ 增删必须过 review——这一行就是合规红线的闸门。
CRITERION_KEY_WHITELIST = frozenset(
    {
        "skill_match",  # 技能与岗位要求的匹配度
        "experience_depth",  # 相关经验的深度
        "project_relevance",  # 项目经历与岗位的相关性
        "domain_knowledge",  # 行业/领域知识
        "education_fit",  # 学历与专业要求的匹配
        "language_proficiency",  # 岗位要求的语言能力
        "role_seniority_fit",  # 职级与岗位定位的匹配
    }
)

# ⚠️ 只用于报错信息，**不参与放行判定**。判定恒为"是否在白名单里"。
RED_LINE_EXAMPLES = frozenset(
    {
        "speech_rate",
        "speech_tempo",
        "pause_duration",
        "silence_ratio",
        "voice_emotion",
        "facial_expression",
        "micro_expression",
        "face_match",
        "emotion_score",
        "gaze_stability",
    }
)


def validate_criterion_key(key: str) -> str:
    """合法则原样返回；未登记则抛 `ForbiddenCriterionKey`。"""
    if key in CRITERION_KEY_WHITELIST:
        return key

    if key in RED_LINE_EXAMPLES:
        raise ForbiddenCriterionKey(
            f"{key!r} 属合规红线维度（声学情绪信号 / 人脸表情），"
            "MUST NOT 作为评分项写入。这不是漏配，⛔ 不要把它加进白名单。"
        )

    raise ForbiddenCriterionKey(
        f"未登记的评分维度: {key!r}；已登记: {sorted(CRITERION_KEY_WHITELIST)}。"
        "未登记即拒绝（fail-closed）——新增维度请改 app/audit/criteria.py 并过 review。"
    )
```

- [ ] **Step 4: 在 `CriterionScore` 上接强制点**

修改 `app/audit/events.py`——在 import 段加：

```python
from app.audit.criteria import validate_criterion_key
```

并给 `CriterionScore` 加 `__post_init__`（紧跟在 `id: str | None = None` 之后、`to_dict` 之前）：

```python
    def __post_init__(self) -> None:
        """
        白名单强制点，**唯一一处**。放在构造期而不是 sink 的写入期：写入期强制
        只罩得住走那一个 sink 的路径，构造期强制让所有写入方连一个非法对象都造
        不出来。定义在 app/audit/criteria.py，本处只调用不重复判定。
        """
        validate_criterion_key(self.criterion_key)
```

同时把 `CriterionScore` docstring 里那句「本层**不做**重复校验也**不做**兜底」下方补一行：

```python
    # ⚠️ 上面那句针对的是 evidence_ref（它由数据库 CHECK 强制）。criterion_key
    # 是另一回事：数据库没有也不该有维度白名单（加维度要改 DDL 就没人愿意加），
    # 强制点只能在这里，见 __post_init__。
```

- [ ] **Step 5: 导出符号**

修改 `app/audit/__init__.py`，在 `from app.audit.events import (...)` **之前**加：

```python
from app.audit.criteria import (
    CRITERION_KEY_WHITELIST,
    ForbiddenCriterionKey,
    validate_criterion_key,
)
```

并在 `__all__` 列表开头加三项：`"CRITERION_KEY_WHITELIST"`、`"ForbiddenCriterionKey"`、`"validate_criterion_key"`。

- [ ] **Step 6: 跑测试确认通过**

```bash
./venv/bin/python -m pytest tests/test_audit_criteria.py -q
```

预期：**20 passed**（7 个测试函数，其中三个参数化分别展开 5 / 5 / 6 条）。

- [ ] **Step 7: 跑 U2 的全部旧测试，确认强制点没打挂已有用例**

```bash
./venv/bin/python -m pytest tests/test_audit_events.py tests/test_audit_sinks_sqlite.py tests/test_audit_chain.py tests/test_audit_recorder.py -q
```

**预期会红三条**，位置已实测确定——U2 的 fixture 用的是**具体技能名**而不是评分维度：

| 位置 | 现值 | 改成 |
|---|---|---|
| `tests/test_audit_sinks_sqlite.py:46` | `criterion_key="autosar"` | `criterion_key="skill_match"` |
| `tests/test_audit_sinks_sqlite.py:47` | `criterion_key="can_bus"` | `criterion_key="domain_knowledge"` |
| `tests/test_audit_events.py:66` | `criterion_key="autosar"` | `criterion_key="skill_match"` |

⛔ **不要把 `autosar` / `can_bus` 加进白名单来让测试变绿。** 它们是某个嵌入式岗位
rubric 里的具体条目，不是评分维度——把它们加进去，白名单就从"七个维度的闸门"
退化成"所有岗位所有技能的登记处"，一年后没人敢再拒绝任何 key。具体技能落在
`rubric_snapshot` 里（那一列本来就是干这个的），维度落在 `criterion_key`。

✅ **口径已拍板（2026-08-28，取 A：`criterion_key` = 评分维度）**，本 Task 的白名单
形态就是最终形态，照做即可。见文末「口径决定」。

- [ ] **Step 8: 修好 import 守护对新文件的盲区**

`tests/test_audit_recorder.py` 当前的守护参数化在硬编码列表上（`:445`），新增的 `criteria.py` 不在其中。把这两段替换掉：

```python
def _audit_module_paths() -> list[Path]:
    """扫目录而不是写死文件名——写死的列表对"新加的文件"是瞎的。"""
    return sorted((APP_ROOT / "audit").glob("*.py"))


def test_audit_module_scan_is_not_silently_empty():
    """
    ⭐ 上面那个 glob 一旦因为路径写错返回空列表，下面的参数化测试会**一条都不跑**，
    而 pytest 对"参数化出 0 条"不报错——守护会以"没有失败"的形式消失。这条用字面
    文件名钉住扫描确实扫到了东西。用子集而不是相等：U3 后续 Task 还要往这个目录
    加文件，相等会把无关的 Task 一起弄红。
    """
    names = {path.name for path in _audit_module_paths()}

    assert {"events.py", "sinks.py", "recorder.py", "criteria.py"} <= names


@pytest.mark.parametrize(
    "path", _audit_module_paths(), ids=lambda path: path.stem
)
def test_audit_module_imports_no_config_or_graph(path):
    """
    铁律 2 的落点：app/audit 是被 L4 调用的存储适配层，自己不决定何时被调用。
    import app.config 会让审计路径在启动时绑死配置、并让 U3 的注入点不再是唯一
    一处；import app.graph 是反向依赖。路径与连接一律由调用方传入。
    """
    assert _modules_importing_config_or_graph(path.read_text(encoding="utf-8")) == []
```

`Path` 已在该文件顶部导入则不必重复；若未导入，补 `from pathlib import Path`。

- [ ] **Step 9: 确认守护真的多覆盖了一个文件**

```bash
./venv/bin/python -m pytest tests/test_audit_recorder.py -q -k "imports_no_config_or_graph or scan_is_not_silently_empty" -v 2>&1 | grep -c "PASSED"
```

预期：**≥ 6**（`__init__`/`criteria`/`events`/`recorder`/`sinks` 五个参数 + 非空扫描 1 条）。改之前只有 3 个参数，这个数字变大就是覆盖面真的变宽的证据。

- [ ] **Step 10: 变异验证——证明白名单真的咬得住**

```bash
./venv/bin/python - <<'PY'
import pathlib, re
p = pathlib.Path("app/audit/criteria.py")
original = p.read_text(encoding="utf-8")
p.write_text(original.replace(
    'CRITERION_KEY_WHITELIST = frozenset(\n    {\n        "skill_match",',
    'CRITERION_KEY_WHITELIST = frozenset(\n    {\n        "facial_expression",\n        "skill_match",'), encoding="utf-8")
print("mutated: facial_expression 已被加进白名单")
PY
./venv/bin/python -m pytest tests/test_audit_criteria.py -q 2>&1 | tail -3
git checkout -- app/audit/criteria.py
./venv/bin/python -m pytest tests/test_audit_criteria.py -q 2>&1 | tail -2
```

预期：变异后 **至少 2 条失败**（`test_biometric_dimensions_are_rejected[facial_expression]` 与 `test_whitelist_itself_contains_no_red_line_dimension[facial_expression]`）；`git checkout` 还原后**全绿**。
⚠️ 若变异后**全绿**，说明白名单没有真的参与判定，⛔ 停下来查——这正是"自我实现的测试"的形状。

- [ ] **Step 11: 提交**

```bash
git add app/audit/criteria.py app/audit/events.py app/audit/__init__.py tests/test_audit_criteria.py tests/test_audit_recorder.py
git commit -m "feat(audit): criterion_key 白名单一处定义、构造期强制（tasks 3.4）"
```

---

### Task 2: `AuditHook` Protocol 扩参与网关透传（`audit_context` / `temperature` / `attempt`）

**Files:**
- Modify: `app/llm/gateway.py:114-137`（Protocol 与 `NoopAuditHook`）、`:181-267`（两个 `extract_structured*`）、`:320-325`（`_call_model` 的 temperature）
- Modify: `tests/test_llm_gateway.py:471-521`（旧的签名锁定测试）
- Modify: `tests/test_llm_gateway.py`（新增四条）

**Interfaces:**
- Consumes: 无（本 Task 不依赖 Task 1）
- Produces: `AuditHook.record()` 的新签名——Task 3 的 `RecorderAuditHook` 按它实现：

```python
def record(
    self, *,
    model: str, response_model: str | None, system_fingerprint: str | None,
    prompt_version: str, temperature: float, input_hash: str,
    raw_response: str | None, token_usage: dict[str, Any], latency_ms: float,
    attempt: int, audit_context: dict[str, Any] | None = None,
) -> None: ...
```
  以及 `LLMGateway.TEMPERATURE = 0`（类常量，发给 API 的值与记进留痕的值的**唯一真源**）

- [ ] **Step 1: 写失败的测试**

在 `tests/test_llm_gateway.py` 末尾追加：

```python
def test_audit_context_reaches_the_hook_as_the_very_same_object():
    """
    design D6：网关**原样透传**，不解释内容。断言的是对象同一性（is），不是相等
    ——相等允许网关中途把它拷一份、顺手改几个键再传下去，同一性不允许。
    """
    client = FakeOpenAIClient([json.dumps({"x": 1, "y": 2})])
    seen = []

    class RecordingHook:
        def record(self, **kwargs):
            seen.append(kwargs)

    context = {"thread_id": "job-1", "node": "compute_intake_turn"}
    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=client,
        audit_hook=RecordingHook(),
    )

    gateway.extract_structured(
        system_prompt="sys", user_prompt="user", schema=Point, audit_context=context
    )

    assert seen[0]["audit_context"] is context


def test_gateway_never_reads_inside_audit_context():
    """
    ⭐ "不解释内容"的机械判据。喂一个"一被读就炸"的 context：网关只要写了
    audit_context.get("job_id") / ["thread_id"] / **audit_context 之类的一行，
    这条立刻变红。没有它，"原样透传"只是一句注释。
    """

    class Explosive(dict):
        def __getitem__(self, key):
            raise AssertionError(f"网关读了 audit_context[{key!r}]——它不该解释内容")

        def get(self, *args, **kwargs):
            raise AssertionError("网关调了 audit_context.get()——它不该解释内容")

        def keys(self):
            raise AssertionError("网关展开了 **audit_context——它不该解释内容")

    client = FakeOpenAIClient([json.dumps({"x": 1, "y": 2})])
    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=client,
    )

    result = gateway.extract_structured(
        system_prompt="sys", user_prompt="user", schema=Point, audit_context=Explosive()
    )

    assert result.x == 1


def test_recorded_temperature_is_the_temperature_actually_sent():
    """
    铁律 3 要求 temperature 进留痕，铁律 5 要求它是 0。这条同时钉两件事：

    1. 记下来的值 == 真正发出去的值（比对两侧，不是各自跟字面量比）——
       只跟字面量比的话，有人把发送侧改成 0.7、把记录侧也改成 0.7，两条断言
       一起改完照样全绿，而留痕就开始撒谎了。
    2. 这个值是 0（铁律 5 的字面要求）。
    """
    client = FakeOpenAIClient([json.dumps({"x": 1, "y": 2})])
    seen = []

    class RecordingHook:
        def record(self, **kwargs):
            seen.append(kwargs)

    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=client,
        audit_hook=RecordingHook(),
    )

    gateway.extract_structured(system_prompt="sys", user_prompt="user", schema=Point)

    sent = client.chat.completions.calls[0]["temperature"]
    assert seen[0]["temperature"] == sent
    assert sent == 0


def test_attempt_number_is_one_based_and_increments_per_retry():
    """
    attempt 存在的唯一理由：analysis_run.id 要靠它区分同一次 extract_structured
    里的多次尝试。两次尝试的 input_hash 完全相同，没有 attempt 就会撞主键，
    U2 的短路逻辑会把第 2 次尝试当成"已写过"静默丢掉（sinks.py:156-168）。
    """
    seen = []

    class RecordingHook:
        def record(self, **kwargs):
            seen.append(kwargs)

    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        audit_hook=RecordingHook(),
        client=FakeOpenAIClient(["这不是 JSON", json.dumps({"x": 1, "y": 2})]),
    )

    gateway.extract_structured(system_prompt="sys", user_prompt="user", schema=Point)

    assert [call["attempt"] for call in seen] == [1, 2]


def test_call_sites_that_pass_no_audit_context_still_work():
    """tasks 3.1 逐字：现有调用点不传也能跑。jd_agent 与 compare_models.py 就是这种。"""
    seen = []

    class RecordingHook:
        def record(self, **kwargs):
            seen.append(kwargs)

    gateway = LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        audit_hook=RecordingHook(),
        client=FakeOpenAIClient([json.dumps({"x": 1, "y": 2})]),
    )

    gateway.extract_structured(system_prompt="sys", user_prompt="user", schema=Point)

    assert seen[0]["audit_context"] is None
```

- [ ] **Step 2: 跑测试确认它失败**

```bash
./venv/bin/python -m pytest tests/test_llm_gateway.py -q -k "audit_context or temperature_is_the_temperature or attempt_number" 2>&1 | tail -5
```

预期：**FAILED**，`TypeError: extract_structured() got an unexpected keyword argument 'audit_context'`（前两条）与 `KeyError: 'temperature'` / `KeyError: 'attempt'`（后两条）。

- [ ] **Step 3: 改 Protocol 与 `NoopAuditHook`**

把 `app/llm/gateway.py:114-137` 整段替换为：

```python
class AuditHook(Protocol):
    """
    一次 LLM 调用的留痕落点。**每次尝试各调一次**（重试的每一次都是一次真实的、
    花了钱的 API 调用，都要留痕）。

    ⚠️ 网关只负责把参数交出去，⛔ 不解释 `audit_context` 的内容——业务语义
    （application_id / job_id / rubric 快照）由适配层理解，网关继续对业务无知
    （design.md D6）。
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
    ) -> None: ...


class NoopAuditHook:
    """
    ⚠️ **测试专用**（design.md D6）。生产装配处注入的是 `RecorderAuditHook`
    （见 `app/main.py`）——注入点只有一处，回滚 = 换回一行。

    留着它的理由：`LLMGateway` 的单元测试与 `scripts/compare_models.py` 不需要
    一个真实的数据库连接。⛔ 不要在生产路径上用它：它只 `logger.debug`，铁律 3
    在它身上一条都不成立。
    """

    def record(self, **kwargs: Any) -> None:
        logger.debug("audit_hook(noop): %s", kwargs)
```

- [ ] **Step 4: temperature 收成单一真源**

在 `class LLMGateway:` 的第一行（`def __init__` 之前）加类常量：

```python
class LLMGateway:
    # 铁律 5：temperature 恒为 0。发给 API 的值与记进留痕的值必须是**同一个**
    # 来源——写成两处字面量，改了一处忘了另一处，留痕就开始撒谎且没人发现。
    TEMPERATURE = 0
```

并把 `_call_model` 里 `self._client.chat.completions.create(...)` 的 `temperature=0` 改成：

```python
            temperature=self.TEMPERATURE,
```

- [ ] **Step 5: 两个 `extract_structured*` 加参数并透传**

`extract_structured` 的签名加一行 `audit_context: dict[str, Any] | None = None,`（放在 `prompt_version` 之后），函数体转调时补 `audit_context=audit_context,`。

`extract_structured_with_meta` 同样加该参数，并把 `:245-254` 的 hook 调用替换为：

```python
            self._audit_hook.record(
                model=self._model,
                response_model=response_model,
                system_fingerprint=system_fingerprint,
                prompt_version=prompt_version,
                temperature=self.TEMPERATURE,
                input_hash=input_hash,
                raw_response=raw_content,
                token_usage=token_usage,
                latency_ms=latency_ms,
                attempt=attempt_index + 1,
                # ⛔ 原样透传，不读、不拷、不改（design.md D6）。
                audit_context=audit_context,
            )
```

- [ ] **Step 6: 同步更新旧的签名锁定测试**

`tests/test_llm_gateway.py:471` 的 `test_audit_hook_still_records_per_attempt_with_unchanged_signature` 现在名不副实——签名**已经**动了。改名并更新它的 key 集合断言（**⛔ 不要删掉这条断言**：精确的 key 集合是"有人偷偷再加一个参数"的唯一自动判据）：

```python
def test_audit_hook_records_one_row_per_attempt(monkeypatch):
    """
    "每次尝试记一条"的语义不变（design.md 决策 9 的这一半仍然成立）；
    签名在 U3 扩了三个参数（temperature / attempt / audit_context），
    理由见 openspec 变更包 ai-audit-trail-and-outbound-gate tasks 3.1 与
    docs/superpowers/plans/2026-08-28-ai-audit-trail-unitU3-recorder-wiring.md
    的偏离登记 2。
    """
```

并把该函数末尾的 key 集合断言改成：

```python
    assert set(hook.calls[0]) == {
        "model",
        "response_model",
        "system_fingerprint",
        "prompt_version",
        "temperature",
        "input_hash",
        "raw_response",
        "token_usage",
        "latency_ms",
        "attempt",
        "audit_context",
    }
```

- [ ] **Step 7: 跑测试确认通过**

```bash
./venv/bin/python -m pytest tests/test_llm_gateway.py -q
```

预期：**21 passed**（改动前实测 16 条 + 本 Task 新增 5 条）。

- [ ] **Step 8: 确认没打挂任何既有调用点**

```bash
./venv/bin/python -m pytest tests -q 2>&1 | tail -3
```

预期：**487 + 新增条数，0 failed**。`jd_agent.py:69` 与 `scripts/compare_models.py:115` 不传新参数，靠默认值继续工作——这就是 tasks 3.1「现有调用点不传也能跑」的实证。

- [ ] **Step 9: 提交**

```bash
git add app/llm/gateway.py tests/test_llm_gateway.py
git commit -m "feat(llm): AuditHook 扩参 audit_context/temperature/attempt，温度收单一真源（tasks 3.1）"
```

---

### Task 3: `RecorderAuditHook` 适配器——自持连接、两段式、失败语义不对称

**Files:**
- Create: `app/audit/hook.py`
- Modify: `app/audit/__init__.py`（导出 `RecorderAuditHook` / `UnknownAuditContextKey`）
- Create: `tests/test_audit_hook.py`

**Interfaces:**
- Consumes:
  - Task 2 的 `AuditHook.record()` 新签名（11 个关键字参数）
  - U2 已交付：`AuditRecorder.record(conn, event) -> bool`（`app/audit/recorder.py:74`）、`AuditRecorder.mirror(event) -> bool`（`:95`）、`DecisionEvent`（`app/audit/events.py:72`）、`AI_ANALYSIS`（`events.py:34`）
- Produces: `app.audit.hook.RecorderAuditHook(recorder: AuditRecorder, conn: sqlite3.Connection)`，Task 4 在 `app/main.py` 里构造它

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_audit_hook.py`：

```python
"""
RecorderAuditHook：网关的扁平参数 → DecisionEvent → AuditRecorder。

本文件最重要的两条是**方向相反**的失败语义（计划 Global Constraints 第三条）：
SQLite 失败必须抛，JSONL 失败必须不抛。把它们写成对称是本 Task 最容易犯的错，
所以两条各自独立成用例，任何一侧被改成另一侧的语义都会单独变红。
"""

import json
import sqlite3

import pytest

from app.audit.events import AI_ANALYSIS, DecisionEvent
from app.audit.hook import RecorderAuditHook, UnknownAuditContextKey
from app.audit.recorder import AuditRecorder
from app.audit.sinks import JsonlChainSink, SqliteSink
from app.storage.db import get_connection, init_schema


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "audit.db")


@pytest.fixture
def conn(db_path):
    connection = get_connection(db_path)
    init_schema(connection)
    return connection


@pytest.fixture
def chain_path(tmp_path):
    return tmp_path / "decisions.jsonl"


@pytest.fixture
def hook(conn, chain_path):
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))
    return RecorderAuditHook(recorder, conn)


def _call(hook, **overrides):
    """一次形状真实的网关回调。默认不带 audit_context（现有调用点就是这样）。"""
    payload = {
        "model": "deepseek-chat",
        "response_model": "deepseek-chat-241226",
        "system_fingerprint": "fp_8802",
        "prompt_version": "intake-v5",
        "temperature": 0,
        "input_hash": "a" * 64,
        "raw_response": '{"ok": true}',
        "token_usage": {"prompt_tokens": 10, "completion_tokens": 5},
        "latency_ms": 1500.0,
        "attempt": 1,
        "audit_context": None,
    }
    payload.update(overrides)
    hook.record(**payload)


def _rows(conn):
    cursor = conn.execute("SELECT * FROM analysis_run")
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


# ── 字段映射 ─────────────────────────────────────────────────────────────


def test_configured_and_response_model_land_in_separate_columns(hook, conn):
    """
    spec「一次评分调用完成」逐字：配置侧模型标识与响应返回的模型标识
    **分两个字段各自保存，不互相覆盖**。这两个值在真实环境里经常不同
    （配置写会漂移的别名 deepseek-chat，响应回具体版本）。
    """
    _call(hook)

    row = _rows(conn)[0]
    assert row["configured_model"] == "deepseek-chat"
    assert row["response_model"] == "deepseek-chat-241226"


def test_business_keys_come_from_audit_context(hook, conn):
    _call(
        hook,
        audit_context={
            "thread_id": "job-7",
            "node": "compute_intake_turn",
            "application_id": "app-3",
            "job_id": "job-7",
            "rubric_version": "ecu-embedded-v2",
            "rubric_snapshot": {"skill_match": 0.4},
        },
    )

    row = _rows(conn)[0]
    assert row["application_id"] == "app-3"
    assert row["job_id"] == "job-7"
    assert json.loads(row["rubric_snapshot"]) == {
        "version": "ecu-embedded-v2",
        "snapshot": {"skill_match": 0.4},
    }


def test_unknown_audit_context_key_is_rejected(hook):
    """
    ⭐ spec 硬要求：「系统 MUST NOT 在留痕记录中存储简历原文」。audit_context 是本
    变更唯一新增的"调用方能往留痕里塞东西"的通道——不拒收未登记的键，将来一个
    "方便排查"的 resume_text 会一路流进 JSONL 镜像。

    ⛔ 抛而不是忽略：忽略等于静默丢数据，写错的人永远不知道自己的 job_id 没进库。
    """
    with pytest.raises(UnknownAuditContextKey, match="resume_text"):
        _call(hook, audit_context={"thread_id": "job-1", "resume_text": "张三，男，1990"})


def test_missing_system_fingerprint_is_stored_as_null_and_does_not_raise(hook, conn):
    """
    tasks 3.6 / spec「供应商不返回部署指纹」：该字段记为空值，留痕照常写入，
    留痕流程**不因字段缺失而失败**。断言的是"写成功且列为 NULL"，不是抛异常。
    """
    _call(hook, system_fingerprint=None)

    row = _rows(conn)[0]
    assert row["system_fingerprint"] is None
    assert row["configured_model"] == "deepseek-chat"  # 其余字段照常落盘


def test_none_raw_response_is_coerced_and_flagged_in_the_mirror(hook, conn, chain_path):
    """
    analysis_run.raw_response 是 NOT NULL（app/storage/db.py:105）。模型返回空
    响应体时若原样传 None，留痕会撞 NOT NULL 而把整次调用打挂——把"模型没说话"
    升级成"系统故障"。折成空串写入，并在镜像的 error 字段留下痕迹：真身满足
    NOT NULL，"这次是空的"这个事实不丢。
    """
    _call(hook, raw_response=None)

    assert _rows(conn)[0]["raw_response"] == ""
    mirrored = json.loads(chain_path.read_text(encoding="utf-8").splitlines()[0])
    assert "raw_response" in mirrored["error"]


# ── 两段式与提交时机 ─────────────────────────────────────────────────────


def test_row_is_already_committed_when_the_mirror_runs(conn, chain_path, db_path):
    """
    ⭐ U2 的两段式约束在 U3 的落点：mirror 必须发生在**事务已提交之后**
    （delivery-units.md §3.4 第 3 条）。

    判据不是"读代码看见 commit 在前"——那不是测试。这里在 mirror 被调用的瞬间
    另开一条连接去查：能查到，就证明提交确实已经发生。第二条连接看不见未提交
    的事务，这是 SQLite 的隔离性替我们做的断言。
    """
    seen_from_outside = []

    class SpyMirror:
        def write(self, event):
            other = sqlite3.connect(db_path)
            seen_from_outside.append(
                other.execute("SELECT count(*) FROM analysis_run").fetchone()[0]
            )
            other.close()
            return True

        def read_all(self):
            return []

    recorder = AuditRecorder(SqliteSink(conn), SpyMirror())
    _call(RecorderAuditHook(recorder, conn))

    assert seen_from_outside == [1]


def test_hook_is_not_an_effect_function(hook):
    """
    ⛔ 禁止在 effect_* 函数体内 append JSONL（delivery-units.md §3.4 第 2 条）。
    本适配器自己 append，所以它**必须不叫** effect_*，否则 U2 的 AST 守护
    tests/test_audit_recorder.py::test_no_effect_function_appends_jsonl 会变红。
    这条是那个守护的可读版本，让"为什么不叫 effect_record"有个明写的理由。
    """
    assert not type(hook).__name__.startswith("effect_")
    assert not any(name.startswith("effect_") for name in dir(hook))


# ── 失败语义：两个方向必须相反 ───────────────────────────────────────────


def test_sqlite_failure_propagates_out_of_the_hook(conn, chain_path):
    """
    ⭐ 方向一。spec 逐字：「留痕写入失败 MUST NOT 被静默忽略：留痕写入失败时
    系统 SHALL 视该次 AI 结果为不可用，其评分 MUST NOT 进入下游排序。」

    异常穿透出网关 = 调用方拿不到解析结果 = 进不了下游，这是唯一自洽的落地。
    """

    class ExplodingStore:
        conn = None

        def write(self, event):
            raise sqlite3.OperationalError("disk I/O error")

        def read_all(self):
            return []

    recorder = AuditRecorder(ExplodingStore(), JsonlChainSink(chain_path))

    with pytest.raises(sqlite3.OperationalError):
        _call(RecorderAuditHook(recorder, conn))


def test_mirror_failure_does_not_propagate_and_the_row_survives(hook, conn, chain_path, monkeypatch):
    """
    ⭐ 方向二，与上一条相反。design D1 / delivery-units.md §3.4 第 3 条：允许的
    偏差**只有单向**——「SQLite 有、JSONL 缺行」（真身完整、镜像缺证据）。镜像
    失败就把整次调用打挂，等于把一个被明确允许的偏差升级成故障。缺行由
    AuditRecorder.reconcile() 检出、backfill() 在链尾补录（U2 已交付）。

    ⚠️ 这条和上一条必须都在。只留一条，实现者把两侧写成同一种语义时，
    另一侧不会有人发现。
    """

    def boom(event):
        raise OSError("镜像文件所在磁盘满了")

    monkeypatch.setattr(hook._recorder, "mirror", boom)

    _call(hook)  # 不抛

    assert len(_rows(conn)) == 1  # 真身还在


def test_failed_sqlite_write_leaves_no_half_written_row(conn, chain_path):
    """
    写失败后必须回滚。不回滚的话，半截写入悬在 SQLite 的隐式事务里，会被**下一个
    不相关的**提交顺手带进库（app/storage/idempotency.py:42-47 逐字描述了这个
    失败模式）。这里用 evidence_ref 为空的评分项触发数据库 CHECK 失败。
    """
    from app.audit.criteria import CRITERION_KEY_WHITELIST  # noqa: F401  仅确认模块在位
    from app.audit.events import CriterionScore

    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))
    event = DecisionEvent(
        id="job-1:compute:hash:1",
        event_type=AI_ANALYSIS,
        configured_model="deepseek-chat",
        prompt_version="v1",
        temperature=0,
        input_hash="b" * 64,
        raw_response="{}",
        scores=(CriterionScore(criterion_key="skill_match", score=0.5, evidence_ref="   "),),
    )

    with pytest.raises(sqlite3.IntegrityError):
        RecorderAuditHook(recorder, conn)._write(event)

    assert conn.execute("SELECT count(*) FROM analysis_run").fetchone()[0] == 0


# ── analysis_run.id 的生成规则 ───────────────────────────────────────────


def test_id_is_deterministic_when_graph_context_is_present(hook, conn):
    """
    tasks 2.2 逐字：id 由调用方以 {thread_id}:{node}:{input_hash} 生成，主键冲突
    即视为已写入、短路返回。U3 在末尾追加 :{attempt}——同一次 extract_structured
    的多次尝试 input_hash 完全相同，不带 attempt 就会互撞，第 2 次尝试被 U2 的
    短路当成"已写过"静默丢掉（app/audit/sinks.py:156-168）。
    """
    context = {"thread_id": "job-7", "node": "compute_intake_turn"}
    _call(hook, audit_context=context, attempt=1)
    _call(hook, audit_context=context, attempt=2)

    ids = sorted(row["id"] for row in _rows(conn))
    assert ids == [
        f"job-7:compute_intake_turn:{'a' * 64}:1",
        f"job-7:compute_intake_turn:{'a' * 64}:2",
    ]


def test_two_identical_calls_without_graph_context_produce_two_rows(hook, conn):
    """
    ⭐ 没有 thread_id/node 时不能沿用确定性 id：两次内容完全相同的调用是**两次真实
    的、各花了一次钱的 API 调用**，确定性 id 会让第二次撞主键、被短路成 False，
    留痕**静默少一条**。确定性 id 的用途是 LangGraph 重放去重（tasks 2.2），
    而没有图上下文的调用根本不在重放路径上。
    """
    _call(hook)
    _call(hook)

    rows = _rows(conn)
    assert len(rows) == 2
    assert rows[0]["id"] != rows[1]["id"]
```

- [ ] **Step 2: 跑测试确认它失败**

```bash
./venv/bin/python -m pytest tests/test_audit_hook.py -q 2>&1 | tail -4
```

预期：**collection error**，`ModuleNotFoundError: No module named 'app.audit.hook'`。

- [ ] **Step 3: 写适配器**

创建 `app/audit/hook.py`：

```python
"""
`AuditHook`（`app/llm/gateway.py` 的 Protocol）到 `AuditRecorder` 的适配层。

**这是整条留痕链路上唯一知道招聘业务语义的地方**：网关只管把扁平参数交出来、
不解释 `audit_context`（design D6）；`AuditRecorder` 只管两段式分发。中间这层
负责把两者对上。

⚠️ **本适配器持有一条专属的 SQLite 连接并自己提交**，不复用全应用共享的那条。
理由（三条，缺一条这个决定就不成立）：

1. 钩子的触发点在 `LLMGateway` 内部，那里**根本没有 conn**。两个真实调用点的
   形状还不一样：`compute_intake_turn` 完全不在事务里（`app/graph/nodes.py:17`
   的 docstring：纯函数，不写库），`effect_generate_and_persist_jd` 在事务里但
   `conn` 没传进网关（`nodes.py:237`）。
2. 复用共享连接会踩 `app/storage/idempotency.py:41-68`：被装饰函数抛异常时装饰器
   `conn.rollback()`，**留痕行被一起回滚**——而那次 LLM 调用是真的发生过、真的
   花了钱。spec「留痕写入失败 MUST NOT 被静默忽略」在这条路径上会变成"留痕被
   静默撤销"。
3. 工程铁律 1 禁止的是"**同一条连接上有第二个事务管理者**"。专属连接上的管理者
   只有本适配器一个。本仓库已在跑两条连接写同一个库文件的形态，`journal_mode=WAL`
   与 `busy_timeout=5000` 就是为此设成连接默认值的（`app/storage/db.py:245-253`）。

**语义后果是对的那一侧**：业务事务回滚时留痕仍在——那次 AI 调用真的发生过，
留痕记录它是事实陈述。反过来（调用发生了却没有留痕）才是 spec 禁止的方向。

⛔ 本模块不 import `app.config` / `app.graph`：连接与路径一律由 `app/main.py` 传入。
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any
from uuid import uuid4

from app.audit.events import AI_ANALYSIS, DecisionEvent
from app.audit.recorder import AuditRecorder

logger = logging.getLogger(__name__)


class UnknownAuditContextKey(ValueError):
    """`audit_context` 里出现了未登记的键。"""


# audit_context 允许承载的键。⛔ 白名单，不是黑名单——未登记即拒绝。
# 这个通道是唯一"调用方能往留痕里塞东西"的入口，放开它等于放开
# spec「MUST NOT 在留痕记录中存储简历原文」的唯一入口。
ALLOWED_CONTEXT_KEYS = frozenset(
    {
        "thread_id",  # 会话/岗位标识，与幂等键同源
        "node",  # 图节点名，与幂等键同源
        "application_id",
        "job_id",
        "rubric_version",
        "rubric_snapshot",
    }
)


def _validated_context(audit_context: dict[str, Any] | None) -> dict[str, Any]:
    if audit_context is None:
        return {}
    unknown = sorted(set(audit_context) - ALLOWED_CONTEXT_KEYS)
    if unknown:
        raise UnknownAuditContextKey(
            f"audit_context 出现未登记的键: {unknown}；已登记: "
            f"{sorted(ALLOWED_CONTEXT_KEYS)}。⛔ 新增键必须过 review——这个通道"
            "是留痕里唯一能被调用方塞进任意内容的地方。"
        )
    return dict(audit_context)


def _event_id(context: dict[str, Any], input_hash: str, attempt: int) -> str:
    """
    有图上下文时用确定性 id（tasks 2.2 的 `{thread_id}:{node}:{input_hash}`
    加上 `:{attempt}`）；没有图上下文时带随机后缀。

    为什么不无条件用确定性 id：确定性的用途是 **LangGraph 重放去重**。没有图
    上下文的调用（jd_agent、compare_models.py）不在重放路径上，而两次内容相同
    的调用是两次真实的、各花了一次钱的 API 调用——确定性 id 会让第二次撞主键、
    被 `SqliteSink` 短路成 `False`，留痕静默少一条。
    """
    thread_id = context.get("thread_id")
    node = context.get("node")
    if thread_id and node:
        return f"{thread_id}:{node}:{input_hash}:{attempt}"
    return f"llm:{input_hash}:{attempt}:{uuid4().hex}"


class RecorderAuditHook:
    """生产用的审计钩子。装配点只有一处：`app/main.py:_gateway_factory()`。"""

    def __init__(self, recorder: AuditRecorder, conn: sqlite3.Connection) -> None:
        self._recorder = recorder
        self._conn = conn

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
    ) -> None:
        context = _validated_context(audit_context)

        event = DecisionEvent(
            id=_event_id(context, input_hash, attempt),
            event_type=AI_ANALYSIS,
            thread_id=context.get("thread_id"),
            application_id=context.get("application_id"),
            job_id=context.get("job_id"),
            configured_model=model,
            response_model=response_model,
            system_fingerprint=system_fingerprint,
            prompt_version=prompt_version,
            temperature=temperature,
            input_hash=input_hash,
            rubric_version=context.get("rubric_version"),
            rubric_snapshot=context.get("rubric_snapshot"),
            # analysis_run.raw_response 是 NOT NULL（app/storage/db.py:105）。
            # 模型返回空响应体时原样传 None 会撞 NOT NULL，把"模型没说话"升级成
            # "系统故障"。折成空串，并把这个事实记进 error 让镜像留痕。
            raw_response=raw_response or "",
            token_usage=token_usage,
            latency_ms=latency_ms,
            error=None if raw_response is not None else "raw_response 为 None，已折成空串写入",
        )

        self._write(event)

        # 第二段：镜像。⛔ 失败不抛——允许的偏差只有单向「SQLite 有、JSONL 缺行」，
        # 把它升级成故障就等于把一个被明确允许的偏差当成事故。缺行由
        # AuditRecorder.reconcile() 检出、backfill() 在链尾补录。
        try:
            self._recorder.mirror(event)
        except Exception:
            logger.error(
                "留痕镜像 append 失败，真身已落库（id=%s）。这是被允许的单向偏差，"
                "由对账检出、链尾补录；⛔ 不要改成抛异常。",
                event.id,
                exc_info=True,
            )

    def _write(self, event: DecisionEvent) -> None:
        """
        第一段：真身。**失败即抛**——spec：留痕写入失败时该次 AI 结果视为不可用，
        其评分 MUST NOT 进入下游排序。异常穿透出网关正是这条的落地形态。

        失败时先回滚：半截写入悬在隐式事务里会被下一次提交顺手带进库
        （`app/storage/idempotency.py:42-47` 描述的正是这个失败模式）。
        """
        try:
            self._recorder.record(self._conn, event)
            self._conn.commit()
        except Exception:
            try:
                self._conn.rollback()
            except Exception:
                logger.error(
                    "留痕写入失败后的 rollback 也失败了（id=%s）；半截写入可能被"
                    "下一次提交带进库",
                    event.id,
                    exc_info=True,
                )
            raise
```

- [ ] **Step 4: 导出符号**

`app/audit/__init__.py` 追加：

```python
from app.audit.hook import ALLOWED_CONTEXT_KEYS, RecorderAuditHook, UnknownAuditContextKey
```

并把 `"ALLOWED_CONTEXT_KEYS"`、`"RecorderAuditHook"`、`"UnknownAuditContextKey"` 加进 `__all__`。

- [ ] **Step 5: 跑测试确认通过**

```bash
./venv/bin/python -m pytest tests/test_audit_hook.py -q
```

预期：**12 passed**。

- [ ] **Step 6: 确认 U2 的两条 AST 守护仍绿（新文件已被纳入扫描）**

```bash
./venv/bin/python -m pytest tests/test_audit_recorder.py -q -k "no_effect_function or imports_no_config_or_graph or scan_is_not_silently_empty" -v 2>&1 | tail -12
```

预期：全部 PASSED，且 `imports_no_config_or_graph` 的参数里**出现 `hook`**（Task 1 的目录扫描生效的直接证据）。若 `hook` 没出现，说明 Step 8 的扫描改动没生效，⛔ 回去查。

- [ ] **Step 7: 变异验证——证明两条失败语义各自独立咬住**

```bash
./venv/bin/python - <<'PY'
import pathlib
p = pathlib.Path("app/audit/hook.py")
src = p.read_text(encoding="utf-8")
# 把镜像失败改成"也抛"——即把两个方向写成对称
p.write_text(src.replace("""        except Exception:
            logger.error(
                "留痕镜像 append 失败""", """        except Exception:
            raise
        except BaseException:
            logger.error(
                "留痕镜像 append 失败"""), encoding="utf-8")
print("mutated: 镜像失败改成抛")
PY
./venv/bin/python -m pytest tests/test_audit_hook.py -q 2>&1 | tail -3
git checkout -- app/audit/hook.py
./venv/bin/python -m pytest tests/test_audit_hook.py -q 2>&1 | tail -2
```

预期：变异后 `test_mirror_failure_does_not_propagate_and_the_row_survives` **单独变红**，`test_sqlite_failure_propagates_out_of_the_hook` **仍绿**（证明两条咬的是不同的东西，不是同一条断言的两个说法）；还原后全绿。

- [ ] **Step 8: 提交**

```bash
git add app/audit/hook.py app/audit/__init__.py tests/test_audit_hook.py
git commit -m "feat(audit): RecorderAuditHook 适配器，自持连接、两段式、失败语义不对称（tasks 3.2）"
```

---

### Task 4: 生产装配——`app/main.py` 单点注入

**Files:**
- Modify: `app/main.py:1-31`
- Create: `tests/test_main_wiring.py`

**Interfaces:**
- Consumes: Task 3 的 `RecorderAuditHook(recorder, conn)`；U2 的 `AuditRecorder(store, mirror_sink)` / `SqliteSink(conn)` / `JsonlChainSink(path)`；U1 的 `Settings.audit_jsonl_path`（`app/config.py:35`）
- Produces: 生产进程里 `LLMGateway._audit_hook` 是 `RecorderAuditHook` 实例；Task 5 的端到端验收不依赖它（那条自建装配），本 Task 是真实进程的唯一装配证明

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_main_wiring.py`：

```python
"""
留痕注入点的守护。delivery-units.md §2.U3 逐字：「U3 的注入点写死在
app/main.py:_gateway_factory()，不改 create_app 签名。回滚 = 换回一行。」

本文件用 AST 扫源码 + 一个真实子进程装配，两条路互补：AST 便宜、能钉住"写在
哪儿"，子进程贵、能证明"真的跑得起来"。只有 AST 的话，一个语法正确但运行时
炸掉的装配照样全绿。
"""

import ast
import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_SOURCE = (REPO_ROOT / "app" / "main.py").read_text(encoding="utf-8")


def _call_names_in_function(source: str, func_name: str) -> list[str]:
    """函数体内出现的所有被调用者名字（Name 取 id，Attribute 取 attr）。"""
    names: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != func_name:
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            if isinstance(inner.func, ast.Name):
                names.append(inner.func.id)
            elif isinstance(inner.func, ast.Attribute):
                names.append(inner.func.attr)
    return names


def _top_level_call_names(source: str) -> list[str]:
    """模块级（不在任何 def / class 内）出现的被调用者名字。"""
    tree = ast.parse(source)
    names: list[str] = []
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for inner in ast.walk(stmt):
            if not isinstance(inner, ast.Call):
                continue
            if isinstance(inner.func, ast.Name):
                names.append(inner.func.id)
            elif isinstance(inner.func, ast.Attribute):
                names.append(inner.func.attr)
    return names


def _keywords_of_call_in_function(source: str, func_name: str, callee: str) -> list[str]:
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != func_name:
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == callee
            ):
                return [kw.arg for kw in inner.keywords]
    return []


# ── 阳性对照：三个检查器各一条 ───────────────────────────────────────────


def test_call_name_detector_actually_detects():
    """
    ⭐ 没有阳性对照，下面那条"函数体内不出现 get_connection"在"检查器根本没生效"
    时同样是绿的——空列表同时兼容"约束守住了"和"检查没跑"两种解释。
    """
    offending = "def _gateway_factory():\n    conn = get_connection('x')\n    return conn\n"

    assert "get_connection" in _call_names_in_function(offending, "_gateway_factory")


def test_top_level_call_detector_actually_detects():
    offending = "hook = RecorderAuditHook(r, c)\n\ndef f():\n    return RecorderAuditHook(r, c)\n"

    assert _top_level_call_names(offending).count("RecorderAuditHook") == 1


def test_keyword_detector_actually_detects():
    offending = "def _gateway_factory():\n    return LLMGateway(model='m', audit_hook=h)\n"

    assert _keywords_of_call_in_function(offending, "_gateway_factory", "LLMGateway") == [
        "model",
        "audit_hook",
    ]


# ── 真正的守护 ───────────────────────────────────────────────────────────


def test_gateway_factory_opens_no_connection_per_call():
    """
    ⭐ gateway_factory() 被调用**两处**：启动时 app/web/server.py:66，以及每次
    请求 :278。把 get_connection() 写进工厂函数体 = 每个 HTTP 请求泄漏一条
    SQLite 连接，且每条连接各带一份哈希链游标，JSONL 链会开始互相打架。
    """
    assert "get_connection" not in _call_names_in_function(MAIN_SOURCE, "_gateway_factory")


def test_audit_hook_is_constructed_exactly_once_at_module_level():
    assert _top_level_call_names(MAIN_SOURCE).count("RecorderAuditHook") == 1
    assert MAIN_SOURCE.count("RecorderAuditHook(") == 1


def test_gateway_factory_injects_the_audit_hook():
    keywords = _keywords_of_call_in_function(MAIN_SOURCE, "_gateway_factory", "LLMGateway")

    assert "audit_hook" in keywords


def test_create_app_signature_is_untouched():
    """
    delivery-units.md §2.U3：⛔ 不改 create_app 签名——改了立刻与 M1 的 B/D 单元
    串行。签名是那条约束唯一测得到的形状。
    """
    from app.web.server import create_app

    assert list(inspect.signature(create_app).parameters) == [
        "db_path",
        "gateway_factory",
        "root_path",
    ]


def test_server_module_is_not_touched_by_this_unit():
    """U3 的 diff 里 app/web/server.py 必须为空（Global Constraints 头号约束）。"""
    server_source = (REPO_ROOT / "app" / "web" / "server.py").read_text(encoding="utf-8")

    assert "RecorderAuditHook" not in server_source
    assert "audit_hook" not in server_source


# ── 真实子进程装配 ───────────────────────────────────────────────────────


def test_importing_app_main_wires_a_real_recorder_hook(tmp_path):
    """
    ⭐ 唯一证明"这套装配真的跑得起来"的测试。AST 只看形状：一个 import 写错、
    一个参数顺序反了的 main.py 照样能通过上面全部断言。

    走子进程而不是直接 import：app.main 在导入期就会 setup_logging()、建库、
    create_app()，在测试进程里 import 会污染其余测试，而且 get_settings() 的
    lru_cache 会把第一次读到的路径钉死。
    """
    probe = (
        "import app.main as m\n"
        "gw = m._gateway_factory()\n"
        "hook = gw._audit_hook\n"
        "assert type(hook).__name__ == 'RecorderAuditHook', type(hook).__name__\n"
        # 第二次调用必须拿到同一个 hook 对象——每次新建就是每次新开连接
        "assert m._gateway_factory()._audit_hook is hook, '每次调用都新建了 hook'\n"
        "print('WIRED')\n"
    )
    env = {
        **os.environ,
        "DB_PATH": str(tmp_path / "wiring.db"),
        "AUDIT_JSONL_PATH": str(tmp_path / "decisions.jsonl"),
        "LOG_DIR": str(tmp_path / "logs"),
        "LLM_API_KEY": "test-key",
        "PYTHONPATH": str(REPO_ROOT),
    }

    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "WIRED" in result.stdout
```

- [ ] **Step 2: 跑测试确认它失败**

```bash
./venv/bin/python -m pytest tests/test_main_wiring.py -q 2>&1 | tail -5
```

预期：**6 passed, 3 failed**。
PASS 的六条描述的是接线前**已经成立**的现状——三条阳性对照，加上 `test_create_app_signature_is_untouched`、`test_server_module_is_not_touched_by_this_unit`，以及 `test_gateway_factory_opens_no_connection_per_call`（现在的工厂本来就没开连接，这条是**防回归**的，接线后它才开始真正干活）。
FAIL 的三条是接线本身：`test_audit_hook_is_constructed_exactly_once_at_module_level`、`test_gateway_factory_injects_the_audit_hook`、`test_importing_app_main_wires_a_real_recorder_hook`。

- [ ] **Step 3: 改装配点**

把 `app/main.py` 改成（新增的部分在 `setup_logging(...)` 之后、`def _gateway_factory` 之前）：

```python
from app.audit import AuditRecorder, JsonlChainSink, RecorderAuditHook, SqliteSink
from app.config import get_settings
from app.llm.gateway import LLMGateway
from app.observability.logging_config import setup_logging
from app.storage.db import get_connection
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

# ── 留痕装配（ai-audit-trail-and-outbound-gate，交付单元 U3）─────────────
# **注入点只有这一处**，回滚 = 把下面 _gateway_factory 里的 audit_hook 参数删掉
# 一行（design.md 迁移计划第 3 步）。
#
# ⚠️ 审计走**专属连接**，不复用 create_app() 内那条全应用共享的连接：钩子在
# LLMGateway 内部触发，那里没有 conn；复用共享连接会让留痕行被 idempotent_effect
# 的 rollback 一起撤销（app/storage/idempotency.py:41-68）。理由全文见
# app/audit/hook.py 的模块 docstring。
#
# ⚠️ 在模块级构造一次，⛔ 不要挪进 _gateway_factory()：那个工厂被调用两处
# （app/web/server.py:66 启动时、:278 每次请求），挪进去等于每个请求泄漏一条
# SQLite 连接。
#
# 建表由 create_app() 里的 init_schema() 负责（app/web/server.py:55-56）；本连接
# 只写不建表，首次写入发生在第一个 HTTP 请求，那时表一定已经在了。
_audit_conn = get_connection(settings.db_path)
_audit_recorder = AuditRecorder(
    SqliteSink(_audit_conn),
    JsonlChainSink(settings.audit_jsonl_path),
)
_audit_hook = RecorderAuditHook(_audit_recorder, _audit_conn)


def _gateway_factory() -> LLMGateway:
    return LLMGateway(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        supports_json_schema=settings.llm_supports_json_schema,
        audit_hook=_audit_hook,
    )


app = create_app(
    db_path=settings.db_path,
    gateway_factory=_gateway_factory,
    root_path=settings.root_path,
)
```

- [ ] **Step 4: 跑测试确认通过**

```bash
./venv/bin/python -m pytest tests/test_main_wiring.py -q
```

预期：**9 passed**。

- [ ] **Step 5: 确认 `app/web/server.py` 的 diff 真的是空的**

```bash
git status --short app/web/server.py
```

预期：**无输出**。有输出就是违反了 Global Constraints 的头号约束，⛔ 回滚那个改动。

- [ ] **Step 6: 跑全量**

```bash
./venv/bin/python -m pytest tests -q 2>&1 | tail -3
```

预期：**0 failed**。

- [ ] **Step 7: 提交**

```bash
git add app/main.py tests/test_main_wiring.py
git commit -m "feat(main): 留痕单点注入 RecorderAuditHook，审计走专属连接（tasks 3.3）"
```

---

### Task 5: 端到端验收与技术债标注

**Files:**
- Create: `tests/test_audit_end_to_end.py`
- Modify: `docs/tech-debt.md`（TD-1 标注触发条件已满足）

**Interfaces:**
- Consumes: Task 1–4 的全部产出。本 Task 不新增任何生产代码，只做验收与文档。
- Produces: tasks 3.5 / 3.6 / 3.7 的验收证据

- [ ] **Step 1: 写端到端验收测试**

创建 `tests/test_audit_end_to_end.py`：

```python
"""
tasks 3.5 / 3.6 / 3.7 的验收：一次形状真实的调用穿过
LLMGateway → RecorderAuditHook → AuditRecorder → SqliteSink + JsonlChainSink，
落进真实的 analysis_run 表与真实的哈希链文件。

这里**不 mock 任何一层留痕**，只 mock 供应商 HTTP 客户端（FakeOpenAIClient）——
留痕链路上任何一环写错，这几条会红。
"""

import hashlib
import json

import pytest
from pydantic import BaseModel

from app.audit.hook import RecorderAuditHook
from app.audit.recorder import AuditRecorder
from app.audit.sinks import JsonlChainSink, SqliteSink
from app.llm.gateway import LLMGateway
from app.storage.db import get_connection, init_schema
from tests.test_llm_gateway import FakeOpenAIClient


class Verdict(BaseModel):
    ok: bool


# 一段"简历原文"的替身。选一个不可能自然出现在代码或响应里的串，
# 这样"它没出现在留痕里"就是个有意义的断言而不是碰巧。
RESUME_PLAINTEXT = "候选人张三·1990-03·某某大学·ZHENGWEN-MARKER-7f3a"


@pytest.fixture
def wired(tmp_path):
    conn = get_connection(str(tmp_path / "e2e.db"))
    init_schema(conn)
    chain_path = tmp_path / "decisions.jsonl"
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))
    hook = RecorderAuditHook(recorder, conn)
    return conn, chain_path, hook


def _gateway(hook, client):
    return LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat",
        supports_json_schema=False,
        client=client,
        audit_hook=hook,
    )


def _rows(conn):
    cursor = conn.execute("SELECT * FROM analysis_run")
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def test_one_scoring_call_lands_every_reproducibility_field(wired):
    """
    tasks 3.5 / spec「一次评分调用完成」：留痕包含配置侧模型标识、响应返回的模型
    标识、部署指纹、prompt 版本、temperature、输入哈希、rubric 快照、原始响应、
    调用时刻——工程铁律 3 的逐项兑现。
    """
    conn, _chain_path, hook = wired
    client = FakeOpenAIClient(
        [json.dumps({"ok": True})],
        response_model="deepseek-chat-241226",
        system_fingerprint="fp_8802",
    )

    _gateway(hook, client).extract_structured(
        system_prompt="按 rubric 打分",
        user_prompt=RESUME_PLAINTEXT,
        schema=Verdict,
        prompt_version="score-v1",
        audit_context={
            "thread_id": "job-7",
            "node": "compute_score",
            "application_id": "app-3",
            "job_id": "job-7",
            "rubric_version": "ecu-embedded-v2",
            "rubric_snapshot": {"skill_match": {"weight": 0.4}},
        },
    )

    row = _rows(conn)[0]
    assert row["configured_model"] == "deepseek-chat"
    assert row["response_model"] == "deepseek-chat-241226"  # 分两列，不互相覆盖
    assert row["system_fingerprint"] == "fp_8802"
    assert row["prompt_version"] == "score-v1"
    assert row["temperature"] == 0
    assert row["raw_response"] == json.dumps({"ok": True})
    assert row["application_id"] == "app-3"
    assert row["job_id"] == "job-7"
    assert json.loads(row["rubric_snapshot"])["version"] == "ecu-embedded-v2"
    assert row["created_at"]  # 调用时刻，由数据库 datetime('now') 填


def test_input_hash_is_the_sha256_of_the_prompts_not_something_else(wired):
    """
    "输入以哈希形式记录"这句话只有在哈希**真的是那段输入的哈希**时才成立。
    这里独立重算一遍：留痕里的值必须等于 sha256("system\\nuser")。
    顺带把这个拼接格式钉住——它是审计可复现性的一部分，改了要有人知道。
    """
    conn, _chain_path, hook = wired
    client = FakeOpenAIClient([json.dumps({"ok": True})])

    _gateway(hook, client).extract_structured(
        system_prompt="按 rubric 打分", user_prompt=RESUME_PLAINTEXT, schema=Verdict
    )

    expected = hashlib.sha256(
        f"按 rubric 打分\n{RESUME_PLAINTEXT}".encode("utf-8")
    ).hexdigest()
    assert _rows(conn)[0]["input_hash"] == expected


def test_missing_system_fingerprint_records_null_and_the_call_still_succeeds(wired):
    """
    tasks 3.6 / spec「供应商不返回部署指纹」：该字段记为空值，留痕照常写入，
    **留痕流程不因字段缺失而失败**。断言"调用照常返回结果"，不是"抛异常"。
    """
    conn, _chain_path, hook = wired
    client = FakeOpenAIClient([json.dumps({"ok": True})], system_fingerprint=None)

    parsed = _gateway(hook, client).extract_structured(
        system_prompt="sys", user_prompt="user", schema=Verdict
    )

    assert parsed.ok is True
    row = _rows(conn)[0]
    assert row["system_fingerprint"] is None
    assert row["raw_response"] == json.dumps({"ok": True})  # 其余照常


def test_no_resume_plaintext_anywhere_in_the_trail(wired):
    """
    ⭐ tasks 3.7 / spec 逐字：「系统 MUST NOT 在留痕记录中存储简历原文。输入内容
    以哈希形式记录。」

    检查两侧介质：SQLite 真身的全部列，与 JSONL 镜像的全部字节。用一个不可能
    自然出现的标记串，所以"没找到"是个有意义的结论。
    """
    conn, chain_path, hook = wired
    client = FakeOpenAIClient([json.dumps({"ok": True})])

    _gateway(hook, client).extract_structured(
        system_prompt="按 rubric 打分",
        user_prompt=RESUME_PLAINTEXT,
        schema=Verdict,
        audit_context={"thread_id": "job-7", "node": "compute_score"},
    )

    persisted = json.dumps(_rows(conn), ensure_ascii=False, default=str)
    mirrored = chain_path.read_text(encoding="utf-8")

    assert "ZHENGWEN-MARKER-7f3a" not in persisted
    assert "ZHENGWEN-MARKER-7f3a" not in mirrored
    # 阴性对照：留痕**确实写了东西**，否则上面两条在"什么都没写"时也是绿的
    assert len(_rows(conn)) == 1
    assert mirrored.strip()


@pytest.mark.parametrize(
    "forbidden_key",
    ["speech_rate", "pause_duration", "silence_ratio", "facial_expression", "micro_expression"],
)
def test_red_line_dimensions_cannot_reach_the_trail(forbidden_key):
    """
    tasks 3.7：写入声学情绪维度、写入人脸/表情维度分别被拒。
    强制点在构造期（Task 1），所以连一个非法的 CriterionScore 对象都造不出来——
    留痕链路上根本没有能承载它的形状。
    """
    from app.audit.criteria import ForbiddenCriterionKey
    from app.audit.events import CriterionScore

    with pytest.raises(ForbiddenCriterionKey):
        CriterionScore(criterion_key=forbidden_key, score=0.9, evidence_ref="r-1#1-2")


def test_the_mirror_chain_verifies_after_a_real_call(wired):
    """
    留痕镜像不是"写进去就算数"——U2 的链校验必须在真实调用之后仍然通过。
    这条把 U3 的接线与 U2 的防篡改能力连起来测一次。
    """
    conn, chain_path, hook = wired
    client = FakeOpenAIClient([json.dumps({"ok": True}), json.dumps({"ok": False})])
    gateway = _gateway(hook, client)

    gateway.extract_structured(system_prompt="s1", user_prompt="u1", schema=Verdict)
    gateway.extract_structured(system_prompt="s2", user_prompt="u2", schema=Verdict)

    result = JsonlChainSink(chain_path).verify_chain()
    assert result.ok is True
    assert result.total == 2
    assert len(_rows(conn)) == 2
```

- [ ] **Step 2: 跑测试确认通过**

```bash
./venv/bin/python -m pytest tests/test_audit_end_to_end.py -q
```

预期：**10 passed**（5 个非参数化 + `test_red_line_dimensions_cannot_reach_the_trail` 展开的 5 条）。

- [ ] **Step 3: 变异验证——证明"配置侧与响应侧分两列"这条真的咬得住**

这是 spec 逐字点名的一条（「不互相覆盖」），值得单独验一次：

```bash
./venv/bin/python - <<'PY'
import pathlib
p = pathlib.Path("app/audit/hook.py")
src = p.read_text(encoding="utf-8")
p.write_text(src.replace(
    "            configured_model=model,\n            response_model=response_model,",
    "            configured_model=response_model,\n            response_model=response_model,"), encoding="utf-8")
print("mutated: configured_model 被 response_model 覆盖")
PY
./venv/bin/python -m pytest tests/test_audit_end_to_end.py tests/test_audit_hook.py -q 2>&1 | tail -3
git checkout -- app/audit/hook.py
./venv/bin/python -m pytest tests/test_audit_end_to_end.py tests/test_audit_hook.py -q 2>&1 | tail -2
```

预期：变异后 `test_one_scoring_call_lands_every_reproducibility_field` 与
`test_configured_and_response_model_land_in_separate_columns` **各自变红**；还原后全绿。

- [ ] **Step 4: 标注 TD-1 的触发条件已满足**

⚠️ `delivery-units.md` §2.U3 说这条债记在 `07-开发环境现状与优化待办.md`，**实测不是**：真源在 `docs/tech-debt.md`，该文件开头逐字写着「本文件是仓库级真源。变更包归档后 `openspec/changes/` 里的 tasks.md 会移走，计划文件也会变旧，但这份清单留着」。以 `docs/tech-debt.md` 为准。

在 `docs/tech-debt.md` 的 TD-1 小节里，把「**触发条件**」那一段改成：

```markdown
**触发条件**：`ai-audit-trail-and-outbound-gate` 的 `analysis_run` 表落地即删
——该变更的 tasks 1.1 已包含 `latency_ms` 与 `created_at`，届时这两列成为冗余。

**⚠️ 触发条件已于 2026-08-28 满足（U3 留痕接线合并）。** `analysis_run` 表在 U1
落地（`app/storage/db.py:84-110`），U3 把 `RecorderAuditHook` 接到
`app/main.py:_gateway_factory()` 之后，每次 LLM 调用都会写 `analysis_run.latency_ms`
与 `analysis_run.created_at`——两套时序数据从此并存。

**删列不在 U3 范围内**：改 `.51` 现网库的表结构属生产决定，不可代
（`delivery-units.md` §2.U3 逐字：「U3 的范围**不含删列**」）。**需 Shao Peishen
拍板后另开一个变更包**，内容 = 删两列 + 删 `effect_persist_draft` 里对它们的写入
+ 统计口径改指 `analysis_run`。在那之前，`job_profile` 的两列与 `analysis_run`
的口径**以 `analysis_run` 为准**。
```

- [ ] **Step 5: 跑全量并确认 U2 的守护仍绿**

```bash
./venv/bin/python -m pytest tests -q 2>&1 | tail -3
```

预期：**0 failed**，总数 = 487（U3 前基线）+ 本单元新增。

```bash
./venv/bin/python -m pytest tests/test_audit_recorder.py -q -k "no_effect_function or packed_method or imports_no_config" 2>&1 | tail -2
```

预期：全绿——U3 新增的 `hook.py` 里有 `mirror()` 调用，但它不在任何 `effect_*` 函数体内，`test_no_effect_function_appends_jsonl` 必须仍绿。**这条是 Global Constraints 第四条在 U3 的唯一自动判据，⛔ 不许跳过。**

- [ ] **Step 6: 依赖零新增的证据**

```bash
git diff --stat main -- requirements.txt pyproject.toml
```

预期：**无输出**（design.md「参考边界」：依赖文件 diff 为空）。

```bash
grep -rn "zhuopin_platform" app tests scripts 2>/dev/null | grep -v venv | wc -l
```

预期：**0**。

- [ ] **Step 7: 提交**

```bash
git add tests/test_audit_end_to_end.py docs/tech-debt.md
git commit -m "test(audit): 留痕接线端到端验收，TD-1 标注触发条件已满足（tasks 3.5/3.6/3.7）"
```

---

## 交付前自查

- [ ] `grep -c '^### Task ' docs/superpowers/plans/2026-08-28-ai-audit-trail-unitU3-recorder-wiring.md` > 0
- [ ] 全量 `pytest tests -q` 0 failed
- [ ] `git status --short app/web/server.py app/graph/nodes.py app/agents/intake_agent.py app/config.py app/outbound` 无输出
- [ ] `git diff --stat main -- requirements.txt pyproject.toml` 无输出
- [ ] 三次变异验证（Task 1 Step 10、Task 3 Step 7、Task 5 Step 3）各自看到**预期的那条**单独变红
- [ ] `tests/test_audit_recorder.py` 的 import 守护参数里出现 `criteria` 与 `hook`

## spec 覆盖对照

| tasks.md 第 3 章 | 落点 | 验收 |
|---|---|---|
| 3.1 `AuditHook` 扩参、原样透传、现有调用点不传也能跑 | Task 2 | `test_audit_context_reaches_the_hook_as_the_very_same_object`、`test_gateway_never_reads_inside_audit_context`、`test_call_sites_that_pass_no_audit_context_still_work` |
| 3.2 `RecorderAuditHook`；`NoopAuditHook` 改注释为测试专用 | Task 3（适配器）、Task 2 Step 3（注释） | `tests/test_audit_hook.py` 全 13 条 |
| 3.3 生产装配单点注入 + 审计 JSONL 路径配置 | Task 4 | `tests/test_main_wiring.py` 全 9 条 |
| 3.4 `criterion_key` 白名单集中一处、非白名单被拒、显式排除声学与人脸 | Task 1 | `tests/test_audit_criteria.py` 全部 |
| 3.5 落齐全部字段、配置侧与响应侧分两列 | Task 5 | `test_one_scoring_call_lands_every_reproducibility_field` |
| 3.6 `system_fingerprint` 缺失记空值、不炸网关 | Task 3、Task 5 | `test_missing_system_fingerprint_records_null_and_the_call_still_succeeds` |
| 3.7 红线维度被拒；留痕不含简历原文 | Task 1、Task 5 | `test_red_line_dimensions_cannot_reach_the_trail`、`test_no_resume_plaintext_anywhere_in_the_trail` |

**spec `Scenario: 留痕写入失败`** → `test_sqlite_failure_propagates_out_of_the_hook`（Task 3）。
**spec `Scenario: 禁止 latest 类别名`** → 已在 `app/config.py:49-53` 与 `app/llm/gateway.py:172-173` 生效，U3 不改，`test_rejects_latest_model_alias` 已在位。

## 本计划相对 `tasks.md` / `delivery-units.md` 的偏离登记（三条，全部需 reviewer 确认）

1. **`AuditHook.record()` 除 `audit_context` 外还新增 `temperature` 与 `attempt` 两个参数**（tasks 3.1 字面只说「新增可选 `audit_context`」）。依据是两条实测约束：① `analysis_run.temperature` 是 **NOT NULL**（`app/storage/db.py:102`）而现签名里没有 temperature（`gateway.py:114-126`），不补参数第一条真实写入就撞 NOT NULL；② 钩子在重试循环内**每次尝试各调一次**（`gateway.py:245` 在 `for attempt_index in ...` 体内，且 `tests/test_llm_gateway.py:471` 明确锁定了这条语义），多次尝试的 `input_hash` 完全相同，不带 `attempt` 就会撞 tasks 2.2 的确定性主键、被 U2 短路成 `False` 静默丢掉第 2 次起的全部尝试。方向是"补齐铁律 3 要求的字段 + 防静默丢数据"，不是加需求。

2. **`analysis_run.id` 在没有图上下文时带随机后缀**（tasks 2.2 字面是 `{thread_id}:{node}:{input_hash}`）。依据：确定性 id 的用途是 LangGraph 重放去重；`jd_agent` 与 `scripts/compare_models.py` 这类调用不在重放路径上，两次内容相同的调用是两次真实的、各花了一次钱的 API 调用，确定性 id 会让第二次撞主键被短路、留痕静默少一条。有图上下文时仍严格按 2.2 的形状（加 `:{attempt}`，理由见偏离 1）。
   > ⚠️ **一条未消除的固有张力，登记但不在 U3 解决**：有图上下文时，LangGraph 重放会真的再调一次 LLM，而确定性 id 会让这第二次被短路成"已写过"。这是 tasks 2.2 的方案本身带来的，不是 U3 引入的。方向是"少记"而不是"记错"，且 U6 的对账能看见。要改需改 2.2 的 id 口径，属跨单元决定。

3. **TD-1 的标注落在 `docs/tech-debt.md` 而不是 `07-开发环境现状与优化待办.md`**（`delivery-units.md` §2.U3 写的是后者）。依据：`docs/tech-debt.md:4` 逐字自述「本文件是仓库级真源」，且 TD-1 条目实际就在那里（`docs/tech-debt.md:7-23`）。`07-` 那份文件里 `grep turn_started_at` 零命中。

## 已登记的边界与技术债（不在本单元解决）

| 事项 | 处置 |
|---|---|
| `audit_context` 尚未接到 intake / jd 两条真实业务路径 | U3 只保证**通道通**。接业务侧要改 `app/graph/nodes.py` 与 `app/agents/intake_agent.py`，超出 `delivery-units.md:24` 给 U3 的文件边界，另开单元 |
| 审计专属连接与共享连接并存 | 已由 `journal_mode=WAL` + `busy_timeout=5000` 承载（`app/storage/db.py:245-253`）。M2 迁 Postgres 时两条连接都换成连接池 |
| `job_profile` 两列删除 | TD-1，触发条件已满足，需 Shao Peishen 拍板后另开变更包（Task 5 Step 4 已标注） |
| JSONL 多进程断链 | U7 的 7.6，U2 已登记，U3 不重复 |
| `criterion_score` 白名单的数据库层强制 | ⛔ 不做。加维度要改 DDL 会让"加一个合法维度"变成一次迁移，`design.md` Risks 明确选了"集中在一处 Python 定义 + review"这条路。纵深防御由 U6 的事后断言承担 |

## 需 Shao Peishen 拍板

1. **审计走专属连接、业务事务回滚时留痕仍在**（本计划 §「一处必须自己定的架构决定」）。三个方案的取舍已列全，(A) 是唯一不越出 U3 文件边界且不会静默丢留痕的选项，但它确实改变了「留痕与业务写同生共死」这个直觉。
2. **TD-1 删列另开变更包的时机**：U3 一合并，`job_profile.llm_latency_ms` 与 `analysis_run.latency_ms` 就开始并存。两套数据并存的时间越长，"该信哪一份"的成本越高。

3. ~~`criterion_key` 的口径~~ **✅ 2026-08-28 已拍板取 A，无阻塞。** 记录留在下面，因为 M2 的评分器要按这个结论写。

   **发现经过**：写 Task 1 时实测仓库里现有的三处 `criterion_key` 取值，全是**具体技能名**——`autosar`（两处）、`can_bus`（一处），位置见 Task 1 Step 7 的表。而 spec 与 design 用的词是"**维度**"：

   > spec「评分项白名单约束」：「系统 SHALL 用白名单限定可作为评分项的**维度**。」
   > design Risks：「`criterion_key` 白名单会拦住合法的新**维度** → 白名单集中在一处定义，**加维度是一行改动 + 一次 review**。」

   两种读法都讲得通，但导出的系统完全不同：

   | 口径 | `criterion_key` 取值 | 白名单可行性 | 代价 |
   |---|---|---|---|
   | **A（本计划采用）** 维度 | `skill_match` / `experience_depth` … 七个 | ✅ 封闭集合，fail-closed 成立，"加维度一行 + review" 字面成立 | 要改 U2 的三处 fixture；具体技能改落 `rubric_snapshot` |
   | **B** rubric 具体条目 | `autosar` / `can_bus` / 每个岗位各不相同 | ❌ 封闭白名单不可行——每开一个岗位就要改代码 | 只能退回黑名单或正则，而黑名单对"没想到的新维度"默认放行，**正是红线要防的方向** |
   | **C** 两级 `维度:条目` | `skill_match:autosar` | ✅ 维度侧仍 fail-closed，条目侧自由 | 引入一个需要全仓库遵守的字符串格式；U6 的断言与将来的检索都要按它切分 |

   **✅ 2026-08-28 Shao Peishen 拍板：取 A。** 依据是 design Risks 那句"加维度是一行改动 + 一次 review"——只有维度数量是个位数时这句话才成立，B 会让它变成"每个岗位一次 PR"，C 则要全仓库遵守一个字符串格式。

   **这条结论对 M2 有约束，必须往下传**：评分器 MUST 把**具体技能/rubric 条目写进 `rubric_snapshot`**，`criterion_key` 只放七个维度之一。写反了会在 `CriterionScore` 构造期当场抛 `ForbiddenCriterionKey`——这是刻意的，不是 bug。
