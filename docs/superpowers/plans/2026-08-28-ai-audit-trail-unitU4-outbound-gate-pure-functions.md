# AI 留痕与外发门禁 · 交付单元 U4（`app/outbound` 门禁纯函数）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新建 `app/outbound/` 包，交付**第二道结构闸的纯函数部分**——`compute_outbound_gate(message, outbound_enabled) -> GateDecision`：fail-closed 判定、两道闸（人工确认 + 外发总开关）、判定所依据字段的原始取值随判定结果一起返回（供 U5 直接留痕，不重新求值）。**本单元不插入任何外发路径、不写库、不发消息、不碰 `app/graph/nodes.py`**，合并后系统可观察行为与合并前完全一致。

**Architecture:** 两个新模块。`contracts.py` 只放常量与 Protocol（已登记消息类型、风险等级词表、门禁读取的字段清单），`gate.py` 放判定。判定函数是一层 `try/except Exception: 判拦截` 的**薄壳**包住私有 `_evaluate_outbound_gate()`——这个形状直接抄 U1 的 `is_candidate_outbound_enabled()`，理由同源：**契约由结构保证，不靠枚举异常类型**（U1 枚举法在 review 中失败过两次，见 `tasks.md`「1.x 落地偏离登记」偏离 5）。开关不读配置、由调用方以 **callable** 传入（`app.config` 在本单元的导入黑名单里），`app/outbound/` 因此对 `app/audit/`（U2）与 `app/config.py`（U1）都零 import 依赖。

**Tech Stack:** Python 3.14.6（`./venv`）· 纯标准库（`dataclasses` / `ast` / `typing`）· pytest 8.3.4 · **不引入任何新依赖**（`requirements.txt` / `pyproject.toml` diff 必须为空）

---

## Global Constraints

以下条目从 `CLAUDE.md`（2026-08-27 版）「工程铁律」「合规红线」「决策代理」、本变更包 `delivery-units.md` §2.U4 / §3.3 / §3.5 / §4、`specs/outbound-approval-gate/spec.md` **逐字复制**。**每个 Task 的验收隐含包含本节全部内容**，`subagent-driven-development` 会把这一段原样交给 reviewer 当注意力透镜。

### 本单元的头号约束（`delivery-units.md` §3.3 末段，逐字）

> **写进 U4 的 plan 的 Global Constraints**：`compute_outbound_gate` 内**禁止出现带默认值的属性读取**（`getattr(x, k, <default>)` / `dict.get(k, <default>)`）——取不到就是未知，未知就是拦截，默认值这个概念本身与 fail-closed 互斥。reviewer 判据可以直接 grep。

**reviewer 的机械判据**：Task 5 的 `test_gate_source_has_no_defaulted_attribute_reads` 用 `ast` 遍历 `app/outbound/gate.py` 与 `contracts.py`，断言**不存在**三参 `getattr(...)` 调用、也不存在两参 `<expr>.get(...)` 调用，违例列表必须为空列表。用 AST 而不是正则：正则会被字符串、注释、换行骗过去，AST 不会。

### 本单元的第二条约束（`delivery-units.md` §3.5 对实现的两条硬约束，逐字）

> 1. `CANDIDATE_OUTBOUND_ENABLED` **必须每次外发时求值**。⛔ 禁止在模块导入期、`__init__` 里、或任何单例上把它读成一个常量。tasks 4.5 已写"支持传 callable"，用那条路。
>    守护测试：运行中改值后**不重启**，下一次外发要立刻按新值走。
> 2. **合并时保持关闭**（全拦），观察拦截留痕符合预期后再开，与 design 迁移计划第 4 步一致。

**本单元怎么兑现**：`compute_outbound_gate` 的第二个形参**只接受零参 callable**，每次判定**恰好调用它一次**（Task 4 的 `test_switch_callable_is_invoked_exactly_once_per_decision` 计数断言）。传进来一个 `bool` 属于结构性误用，按拦截处理——把"启动时缓存一次"这个失败形状从"约定"升级成"类型上做不到"。

### 本单元的第三条约束（U1 plan 对 U4 的点名要求，逐字）

> U4 的 `compute_outbound_gate` 必须保持纯函数——开关值由调用方**以 callable 形式传入**，`compute_outbound_gate` 内部**不得** `import app.config`。这条在 U4 兑现，U1 只负责把接口做成能兑现的形状。

**reviewer 的机械判据**：Task 5 的 `test_outbound_package_imports_nothing_stateful` 断言 `app/outbound/` 下每个 `.py` 的 import 集合与 `{app.config, app.storage, app.channels, app.graph, app.audit, app.web, sqlite3}` **无交集**。`app.audit` 也在黑名单里——`delivery-units.md` §2.U4 写明 U4「逻辑上不依赖 U2/U3」，这条把它变成机器可查。

### 工程铁律（不可违背，从 `CLAUDE.md` 逐字复制）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。**幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者。

> **本单元与这条的关系**：U4 **不新增任何 `effect_*` 节点、不写任何一行数据、不发任何一条消息**。判定必须可被重复求值任意多次而结果恒定（spec「门禁判定与副作用分离」）。
> **reviewer 判据：本单元 diff 里不出现 `@idempotent_effect`、不出现 `INSERT`/`UPDATE`/`conn.`、不出现 `channel.`、不出现 `open(`（测试文件除外）。**

2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。

> **本单元是这条铁律在外发路径上的落点**：函数名 `compute_outbound_gate` 的 `compute_` 前缀是契约不是风格。入队、投递、留痕三类副作用全部归 U5。

3. **所有 AI 评分必须持久化**：模型标识 + 模型版本 + prompt 版本 + temperature + 输入哈希 + rubric 快照 + 原始响应。
4. **每条 `criterion_score` 必须有 `evidence_ref`**。`evidence_ref` 为空不允许写入。
5. **`temperature=0`；模型版本优先显式锁定**，禁止 `latest` 类别名。

> **3/4/5 与本单元的关系**：无直接落点（留痕侧属 U2/U3）。但**铁律 5 的失败模式与本单元同形**：配置里写的名字不算数，响应返回的才算——放到门禁上就是"消息自称什么不算数，读得出来的字段值才算"。⚠️ 特别地，`LLM_MODEL=latest` 这类与外发毫无关系的配置错误**不得**把外发闸门一起带走：U1 已用 `test_unrelated_config_error_does_not_break_gate` 钉住 `is_candidate_outbound_enabled()` 这一侧，U4 这一侧由「开关 callable 抛任何异常 → 判拦截」兜住（Task 5）。

6. **企微回调先落库再处理**：回调接口只做签名校验 + 落库 + 返回 200。（本单元无落点）
7. **`langgraph >= 1.0.10`**（GHSA-g48c-2wqr-h844）。（本单元不改依赖，diff 必须为空）

### 合规红线（本单元直接承载的三条）

- **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。
  → 本单元是"人工确认"这四个字在代码里的**唯一判定点**。
- **AI 生成的 JD、拒信、邀约须带标识**（《AI 生成合成内容标识办法》2025-09-01 施行）。
  → tasks 4.4：**复用** `app/agents/jd_agent.py` 的 `AI_LABEL_TEMPLATE`，⛔ 不另写一套标识逻辑。
- 候选人入口一律用一次性邀请链接。（本单元无落点）

### 决策代理（`CLAUDE.md`，逐字）

**不可代**（无论多紧急一律挂起等本人）：合规红线七条的任何变更或单次例外；**候选人对外通道的开关：一次性邀请链接发放、拒信/邀约对外发送**；……
**代理人未指定期间的默认**：「可代」项同样一律挂起等本人。留空不等于谁都可以。

> **本单元与这条的关系**：本 plan 末尾「## 五项口径 —— ✅ 已拍板」记录了 D-1 至 D-5 的结论。**2026-08-28 Shao Peishen 拍板：一律取最保险一侧**，D-2 因此从「只进证据」改成第七条拦截规则，其余四项保持原样。**⛔ 实施者不得自行改动这五项的口径**，改判须再走一次拍板并当次留痕。

### ⛔ 本单元明确不碰的东西（并发红线，2026-08-28 同期在跑的其他 session）

| 不碰 | 原因 |
|---|---|
| `openspec/changes/ai-audit-trail-and-outbound-gate/tasks.md` | 另一条 session 正在写。需要登记的内容一律写进本 plan 文件 |
| `app/graph/nodes.py`、`app/agents/intake_agent.py` | M1 单元 F 的热点，且 `nodes.py` 属 U5 |
| `docs/openers/run-batch.sh`、`docs/openers/run-lanes.sh` | 另一条泳道在改 |
| `app/config.py`（**只读，一个字节都不改**） | `delivery-units.md` §4 约定 1：两个配置键在 U1 一次加齐，U3 与 U4 只读不写 |
| **`app/config.py` 的 `_read_switch_file()` 编码处理** | ⛔ **已由 Shao Peishen 定案：不改代码**。UTF-8 BOM / UTF-16 开关文件打不开开关且不报错，方向是 fail-closed；改由 U7 运维文档规定用 `[System.IO.File]::WriteAllText($p,'true')` 写。**在合规开关上放松属不可代事项，本 plan 不提议任何相关改动** |

---

## 开工前置（必做，5 分钟）

- [ ] **rebase 到最新 main**（`delivery-units.md` §4 约定 8）。本包与 `m1-intake-quality-fixes` 同期在跑。

```bash
git pull --rebase origin main
```

- [ ] **确认 U1 的前置真的在 main 上**（本单元的唯一前置）。以 git 真值为准，不以 proposal 描述为准：

```bash
grep -n "def is_candidate_outbound_enabled\|candidate_outbound_enabled: bool = False" app/config.py
```

预期：`is_candidate_outbound_enabled()` 函数存在，基线字段默认 `False`。**若 grep 不到，停下——U4 的前置未满足。**

- [ ] **确认 `app/outbound/` 尚不存在**（本单元是全新目录，与仓库零写入重叠）：

```bash
ls app/outbound 2>&1 | head -1
```

预期：`No such file or directory`。若已存在，说明有人先动了，停下核对。

- [ ] **取基线**：全量测试必须全绿，记下数字。

```bash
./venv/bin/python -m pytest -q 2>&1 | tail -2
```

预期：`487 passed`（2026-08-28 本机实测）。本单元合并后应为 `487 + 本单元新增用例数`。

- [ ] **确认 AI 标识常量的锚点仍然对得上**（rebase 后可能漂移，以内容为准而非行号）：

```bash
grep -n "AI_LABEL_TEMPLATE" app/agents/jd_agent.py
```

预期：`app/agents/jd_agent.py:11` 附近定义 `AI_LABEL_TEMPLATE`，值为
`"【AI 生成】本文案由系统基于岗位画像自动生成，生成时间 {generated_at}。"`。
**这个字面值会被 Task 1 的一条测试逐字钉住**——它变了，测试就该红，这是有意的（合规标识文案属红线资产，不该被静默改掉）。

---

## U4 / U5 边界（本 plan 与下一个单元的分界线）

判据一句话：**"给定一条已经成形的消息，该不该发"属 U4；"消息从哪来、拦下来之后怎么办、投递给谁"属 U5。**

| 东西 | 归属 | 判据 |
|---|---|---|
| `compute_outbound_gate()` 判定逻辑、六条 fail-closed 规则、两道闸 | **U4** | 纯计算，不写库不发消息（spec「门禁判定与副作用分离」） |
| `GateDecision`（`allowed` / `reason` / `evidence` / `absent_fields` / `error`） | **U4** | 判定的返回值形状 |
| `OutboundGateMessage` Protocol、已登记消息类型清单、风险等级词表 | **U4** | tasks 4.1 |
| AI 生成标识的**判定**（body 里有没有标识） | **U4** | tasks 4.4 |
| AI 生成标识的**拼接**（给拒信/邀约加上标识） | **不在本包** | 拒信/邀约的生成属 M2；`jd_agent._compose_with_label()` 已有先例 |
| 把 `OutboundMessage`（`app/channels/base.py` 的 `type` + `payload` 两字段）**适配**成门禁能读的形状 | **U5** | 适配器要知道 `nodes.py` 里 payload 的真实形状，触碰 `app/graph/nodes.py`，属 U5 的串行区 |
| `pending_approval` 的读写与状态机（`queue.py`） | **U5** | tasks 5.1/5.2 |
| `effect_enqueue_pending_approval` / `effect_record_outbound_audit` | **U5** | 带 `@idempotent_effect` 的副作用节点 |
| 外发路径分流（判定 → 入队 or 投递） | **U5** | tasks 5.5，改 `nodes.py` |
| 把 `is_candidate_outbound_enabled` 这个**函数对象**接进 `compute_outbound_gate` | **U5** | U4 只定义"第二个参数必须是零参 callable"，谁传进来是接线 |
| `GateDecision.evidence` 落进 `DecisionEvent.evidence` | **U5** | U4 保证 evidence 是 `json.dumps` 得动的扁平 dict；写入是副作用 |
| 内部通知（岗位画像确认卡片）不经门禁 | **U5**（tasks 5.9 回归条款） | U4 的函数没有调用方，谈不上"绕过"；分流点在 `nodes.py` |
| 按 `message_type` 与拦截原因统计 | **U6**（tasks 6.5） | U4 只保证 `reason` 取值来自一个有限集合（`ALL_BLOCK_REASONS`），可直接 GROUP BY |

**U4 合并后系统的可观察行为必须与合并前完全一致**：`app/outbound/` 下的函数**没有任何调用方**，`git grep -n "compute_outbound_gate" -- app/ | grep -v "^app/outbound/"` 必须零命中。这是本单元"可独立合并"的定义。

---

## File Structure

| 文件 | 动作 | 责任 |
|---|---|---|
| `app/outbound/__init__.py` | 新建（Task 1 建，Task 3 补出口） | 包 docstring（写明"判定在这里、副作用在 U5"）+ 对外出口 |
| `app/outbound/contracts.py` | 新建（Task 1） | `OutboundGateMessage` Protocol、`REGISTERED_MESSAGE_TYPES`、`KNOWN_SEVERITIES` / `MAX_SEVERITY`、`GATE_FIELDS`。**只有常量与类型，零逻辑** |
| `app/outbound/gate.py` | 新建（Task 2 建，Task 3/4/5 扩） | `GateDecision`、九个 `REASON_*` 常量、`compute_outbound_gate()` 薄壳 + `_evaluate_outbound_gate()` |
| `tests/test_outbound_gate.py` | 新建（Task 1 建，逐 Task 追加） | 行为面：六条 fail-closed、唯一放行路径、两道闸、异常路径、纯函数性 |
| `tests/test_outbound_gate_structure.py` | 新建（Task 5） | 结构面：AST 查带默认值的属性读取、import 黑名单、标识常量复用同一对象 |

**为什么测试分两个文件**（相对 `delivery-units.md` 只写了 `tests/test_outbound_gate.py` 的偏离，见末尾登记）：结构面那三条测的不是"门禁判得对不对"，而是"门禁的**源码形状**有没有腐化"——它们读 `.py` 源码、解析 AST，与行为用例既不共享 fixture 也不共享失败信号。混在一个文件里，`-k` 跑不开，reviewer 也难一眼看出结构防线还在不在。

---

### Task 1: 消息契约与登记表（tasks 4.1）

**Files:**
- Create: `app/outbound/__init__.py`
- Create: `app/outbound/contracts.py`
- Test: `tests/test_outbound_gate.py`

**Interfaces:**
- Consumes: 无（本单元第一个 Task，只依赖标准库）
- Produces:
  - `app.outbound.contracts.OutboundGateMessage` —— Protocol，字段 `message_type: str` / `requires_confirmation: bool` / `severity: str` / `recipient: str` / `body: str` / `confirmed_by: str | None`
  - `app.outbound.contracts.REGISTERED_MESSAGE_TYPES: frozenset[str]` = `{"rejection_letter", "interview_invitation"}`
  - `app.outbound.contracts.KNOWN_SEVERITIES: tuple[str, ...]` = `("low", "medium", "high")`
  - `app.outbound.contracts.MAX_SEVERITY: str` = `"high"`
  - `app.outbound.contracts.GATE_FIELDS: tuple[str, ...]` —— 门禁会去读的六个属性名，顺序固定

- [ ] **Step 1: 写失败测试**

新建 `tests/test_outbound_gate.py`：

```python
"""`app/outbound` 门禁纯函数的行为面测试（交付单元 U4）。

⚠️ 本文件里的期望值一律写**字面量**，不引用被测模块的常量。
理由：判据和构造共用同一个常量 = 自我实现的测试，改常量时两边一起变、
永远不红。本仓库已经栽过两次（U1 拿 init_schema() 和 init_schema() 互比；
单元 E 用 [[…]] * MAX_ASKS_PER_QUESTION 造数据）。
枚举用被测常量（新增取值时强制作者面对），断言用字面量。
"""

from app.outbound.contracts import (
    GATE_FIELDS,
    KNOWN_SEVERITIES,
    MAX_SEVERITY,
    REGISTERED_MESSAGE_TYPES,
)


def test_registered_message_types_are_exactly_the_two_candidate_facing_kinds():
    """
    spec「门禁覆盖范围」：拒信与邀约两类走门禁，内部通知不在范围内。
    断言**相等**而不是包含——多登记一类就是多开一个候选人外发口子，
    属不可代事项，必须在这里变红而不是静默通过。
    """
    assert REGISTERED_MESSAGE_TYPES == frozenset({"rejection_letter", "interview_invitation"})


def test_severity_vocabulary_is_ordered_and_its_top_is_the_blocking_one():
    """风险等级词表是有序的，最高级单独有名字——判定要用它做等值比较。"""
    assert KNOWN_SEVERITIES == ("low", "medium", "high")
    assert MAX_SEVERITY == "high"
    assert MAX_SEVERITY == KNOWN_SEVERITIES[-1]


def test_gate_fields_cover_every_attribute_the_gate_reads():
    """
    这六个名字是 fail-closed 的作用面：门禁只从这六个属性取信息，
    证据也只记这六项（body 除外，见 Task 2 的 EVIDENCE_KEYS 注释）。
    """
    assert GATE_FIELDS == (
        "message_type",
        "requires_confirmation",
        "severity",
        "recipient",
        "body",
        "confirmed_by",
    )


def test_protocol_is_not_runtime_checkable():
    """
    结构性守护：`OutboundGateMessage` 绝不能是 @runtime_checkable。
    一旦可以 isinstance()，下一个人就会在门禁入口写
    `if not isinstance(msg, OutboundGateMessage): return`——而 fail-closed
    的前提正是"来的东西可能什么属性都没有"，那种消息必须走完判定被拦下并
    留痕，不能在门口被一个类型判断吃掉（拦截留痕是误拦的唯一观测手段，
    见 design 风险表第 2 条）。
    """
    from app.outbound.contracts import OutboundGateMessage

    assert not getattr(OutboundGateMessage, "_is_runtime_protocol", False)
```

> ⚠️ 上面最后一条用了三参 `getattr` —— **测试文件不在禁令范围内**，禁令针对的是 `app/outbound/` 下的实现源码（Task 5 的 AST 断言只扫实现文件）。这里读的是 `typing` 的私有标记位，不存在即视为未开启，正是三参 `getattr` 的正当用法。

- [ ] **Step 2: 跑测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_outbound_gate.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.outbound'`

- [ ] **Step 3: 写最小实现**

新建 `app/outbound/__init__.py`：

```python
"""候选人外发门禁。

**判定在这里，副作用不在这里。** `compute_outbound_gate()` 是纯函数：
不写库、不发消息、不读配置文件，同一输入判多少次结果都一样
（spec「门禁判定与副作用分离」）。入队、投递、留痕三类副作用各自归
`app/graph/nodes.py` 的 `effect_*` 节点（交付单元 U5）。

这道闸存在的理由：`effect_deliver_message` 在本变更之前是**无条件投递**，
合规红线「AI 只做排序推荐，不做自动淘汰」全靠调用方自觉。
"""
```

新建 `app/outbound/contracts.py`：

```python
"""门禁的消息契约与登记表。

本模块**只有常量与类型**，零判定逻辑、零 import 副作用模块——判定在
`gate.py`，副作用在 U5。
"""

from __future__ import annotations

from typing import Protocol


class OutboundGateMessage(Protocol):
    """门禁判定所需字段的说明书。

    ⚠️ **这不是运行时校验，而且刻意没有加 `@runtime_checkable`。**
    fail-closed 的前提正是"来的东西可能什么属性都没有"：那样的消息必须
    走完判定、被判拦截、带着证据进留痕，而不是在门口被一个 isinstance
    挡回去（挡回去就没有拦截留痕，误拦就只能等业务方投诉）。
    `gate.py` 逐字段试读，读不到即未知，未知即拦截。
    """

    message_type: str
    requires_confirmation: bool
    severity: str
    recipient: str
    body: str
    confirmed_by: str | None


# 已登记的候选人外发消息类型。⛔ 往这个集合里加取值 = 多开一个候选人
# 外发口子，属 CLAUDE.md 决策代理表的**不可代**项（"候选人对外通道的
# 开关：拒信/邀约对外发送"），必须由 Shao Peishen 本人拍板。
# 不在本集合中的类型一律拦截——未知类型即拦截（spec「fail-closed 判定语义」）。
REGISTERED_MESSAGE_TYPES: frozenset[str] = frozenset(
    {"rejection_letter", "interview_invitation"}
)

# 风险等级词表，**从低到高有序**。不在表内的取值一律视为"未知"→ 拦截；
# 表内最高级 MAX_SEVERITY 同样拦截。于是实际能过闸的只有 low / medium。
KNOWN_SEVERITIES: tuple[str, ...] = ("low", "medium", "high")
MAX_SEVERITY: str = KNOWN_SEVERITIES[-1]

# 门禁会去读的六个属性名，顺序固定（证据字典的键序由它决定，便于 U6 对账
# 时逐字比对）。⛔ 门禁不读这六个之外的任何属性——尤其不读
# `requires_confirmation` 的任何同义别名，"消息自称"只有这一个入口。
GATE_FIELDS: tuple[str, ...] = (
    "message_type",
    "requires_confirmation",
    "severity",
    "recipient",
    "body",
    "confirmed_by",
)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_outbound_gate.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add app/outbound/__init__.py app/outbound/contracts.py tests/test_outbound_gate.py
git commit -m "feat(outbound): 门禁消息契约与已登记类型/风险等级词表（tasks 4.1）"
```

---

### Task 2: 证据采集——absent / None / 空串三态如实落到底（tasks 4.2 的 `evidence` 半边）

**Files:**
- Create: `app/outbound/gate.py`
- Modify: `tests/test_outbound_gate.py`（追加）

**Interfaces:**
- Consumes: `app.outbound.contracts.GATE_FIELDS`
- Produces:
  - `app.outbound.gate.GateDecision` —— frozen dataclass，字段 `allowed: bool` / `reason: str | None` / `evidence: dict[str, Any]` / `absent_fields: tuple[str, ...]` / `error: str | None`
  - `app.outbound.gate.EVIDENCE_KEYS: tuple[str, ...]` —— 证据字典的固定键序
  - 私有 `_ABSENT` 哨兵、`_read(message, name)`、`_json_safe(value)`（Task 3 起被判定复用）

**为什么证据要先于判定做**：spec「外发与拦截动作强制留痕」要求留痕保留"判定所依据的各字段**原始取值**（缺失时如实记为空值，以便追溯 fail-closed 是被哪一条触发的）"，而 tasks 4.2 要求"留痕直接消费 `evidence` **不重新求值**"。所以证据必须**对全部六个字段一次性采齐**，不能跟着判定短路——短路了，被第一条规则拦下的消息就只剩一个字段的证据，"是哪一条触发的"从留痕里读不出来。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_outbound_gate.py`：

```python
import json
import pytest

from app.outbound.gate import GateDecision, compute_outbound_gate


class _Message:
    """按需构造的消息桩：**只设置显式传入的属性**。

    不给默认值、不继承任何基类——"某个属性根本不存在"这个状态必须能被
    构造出来，它是本单元主防线的输入（delivery-units §3.3 第 1 条）。
    """

    def __init__(self, **fields):
        for name, value in fields.items():
            setattr(self, name, value)


_LABELLED_BODY = (
    "【AI 生成】本文案由系统基于岗位画像自动生成，生成时间 2026-08-28。"
    "很遗憾，本次未能与您继续推进。"
)


def _valid_message(**overrides):
    """六条 fail-closed 全部合格、且带确认人的消息——唯一放行路径的输入。

    ⚠️ body 里的 AI 标识是**逐字写死的字面量**，不用
    AI_LABEL_TEMPLATE.format() 生成。用 format() 生成就是"构造和判据共用
    同一个常量"，jd_agent 那个模板被改掉时两边一起变、测试永远不红——
    而那句标识是《AI 生成合成内容标识办法》要求的合规资产，被改掉时就该红。
    """
    fields = {
        "message_type": "rejection_letter",
        "requires_confirmation": False,
        "severity": "low",
        "recipient": "candidate-42",
        "body": _LABELLED_BODY,
        "confirmed_by": "shao-peishen",
    }
    fields.update(overrides)
    return _Message(**fields)


def test_evidence_records_every_judged_field_even_when_blocked_by_the_first_rule():
    """
    证据不跟着判定短路：被第一条规则（未登记类型）拦下时，其余字段的原始
    取值同样在证据里。否则留痕里读不出"是哪一条 fail-closed 触发的"，
    而那正是 spec 对拦截留痕的原文要求。
    """
    decision = compute_outbound_gate(
        _valid_message(message_type="offer_letter"), lambda: True
    )

    assert decision.allowed is False
    assert set(decision.evidence) == {
        "message_type",
        "requires_confirmation",
        "severity",
        "recipient",
        "confirmed_by",
        "ai_label_present",
        "outbound_enabled",
    }
    assert decision.evidence["message_type"] == "offer_letter"
    assert decision.evidence["severity"] == "low"
    assert decision.evidence["confirmed_by"] == "shao-peishen"


def test_evidence_never_carries_the_message_body():
    """
    ⛔ body 不进证据：拒信正文是候选人可识别内容，而留痕会被 U6 的对账、
    U7 的运维文档反复读取。判定结果 ai_label_present 进证据就够了，
    正文的指纹由 U5 的 content_hash 承担（tasks 5.3）。
    """
    decision = compute_outbound_gate(_valid_message(), lambda: True)

    assert "body" not in decision.evidence
    assert decision.evidence["ai_label_present"] is True
    for value in decision.evidence.values():
        assert "很遗憾" not in str(value)


@pytest.mark.parametrize(
    "missing_field",
    ["message_type", "requires_confirmation", "severity", "recipient", "body", "confirmed_by"],
)
def test_absent_attribute_is_distinguishable_from_an_explicit_none(missing_field):
    """
    "属性根本不存在"与"属性存在但值是 None"在证据里都记成 None（U2 的
    DecisionEvent.evidence 是扁平 dict[str, Any]，见
    tests/test_audit_events.py::test_outbound_event_carries_gate_evidence），
    两者的区别由 absent_fields 单独承载——运维要判断"这个字段是没给，
    还是给了个空"，靠的是这个元组。
    """
    absent = compute_outbound_gate(
        _valid_message(**{missing_field: None}), lambda: True
    )
    assert missing_field not in absent.absent_fields

    fields = {
        "message_type": "rejection_letter",
        "requires_confirmation": False,
        "severity": "low",
        "recipient": "candidate-42",
        "body": _LABELLED_BODY,
        "confirmed_by": "shao-peishen",
    }
    del fields[missing_field]
    truly_absent = compute_outbound_gate(_Message(**fields), lambda: True)
    assert missing_field in truly_absent.absent_fields


def test_evidence_stays_json_serialisable_for_an_exotic_field_value():
    """
    U5 会把 evidence 原样塞进 DecisionEvent 并 json.dumps 落 JSONL。
    一个非 JSON 原生类型的字段值若原样带过去，序列化会在 effect 里抛错。
    门禁在这里就把它折成 repr 字符串——信息不丢，序列化炸不了。
    """

    class _Weird:
        def __repr__(self):
            return "<Weird severity>"

    decision = compute_outbound_gate(_valid_message(severity=_Weird()), lambda: True)

    assert decision.allowed is False
    assert json.dumps(decision.evidence, ensure_ascii=False)
    assert decision.evidence["severity"] == "<Weird severity>"


def test_decision_is_frozen():
    """判定结果是事实，不是可以被下游改写的草稿。"""
    decision = compute_outbound_gate(_valid_message(), lambda: True)

    with pytest.raises(Exception):
        decision.allowed = True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_outbound_gate.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.outbound.gate'`

- [ ] **Step 3: 写最小实现**

新建 `app/outbound/gate.py`（本 Task 只到证据采集为止，判定在 Task 3 接上）：

```python
"""候选人外发门禁的判定纯函数。

⛔ 本模块**不得** import `app.config` / `app.storage` / `app.channels` /
`app.graph` / `app.audit` / `app.web` / `sqlite3`（Task 5 的 AST 测试机器可查）。
外发总开关由调用方以**零参 callable** 形式传入——`app/config.py` 的
`is_candidate_outbound_enabled` 就是为此做成函数而不是常量的
（Shao Peishen 2026-08-26 拍板：允许热改、不重启生效）。

⛔ 本模块**禁止出现带默认值的属性读取**（`getattr(x, k, <default>)` /
`<dict>.get(k, <default>)`）：取不到就是未知，未知就是拦截。默认值这个
概念本身与 fail-closed 互斥（delivery-units §3.3）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.agents.jd_agent import AI_LABEL_TEMPLATE
from app.outbound.contracts import GATE_FIELDS

# AI 生成标识的不变前缀：把模板里的 {generated_at} 之前的部分取出来当作
# 判定依据（生成时间每封信都不同，不能参与匹配）。
# ⚠️ tasks 4.4：**复用** jd_agent 的机制，不另写一套。这里 import 的是同一个
# 常量对象本身，Task 5 的 test_ai_label_source_is_the_jd_agent_constant 用
# `is` 钉住这一点。
_LABEL_PLACEHOLDER = "{generated_at}"
AI_LABEL_PREFIX: str = AI_LABEL_TEMPLATE.partition(_LABEL_PLACEHOLDER)[0]

# 证据字典的固定键序。body 不在其中——正文是候选人可识别内容，只记
# "标识在不在"这个判定结果；正文指纹由 U5 的 content_hash 承担。
EVIDENCE_KEYS: tuple[str, ...] = (
    "message_type",
    "requires_confirmation",
    "severity",
    "recipient",
    "confirmed_by",
    "ai_label_present",
    "outbound_enabled",
)


class _Absent:
    """"这个属性根本不存在"的哨兵。

    ⛔ 不用 None 表示缺失：spec 要求留痕能区分"没给这个字段"与"给了个空
    值"，两者在证据 dict 里都落成 null，区别由 GateDecision.absent_fields
    承载。用 None 当哨兵，这个区别当场消失。
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - 只为调试可读
        return "<absent>"


_ABSENT = _Absent()


@dataclass(frozen=True)
class GateDecision:
    """一次门禁判定的完整事实。

    `evidence` 是**扁平的、json.dumps 得动的** dict —— U5 直接把它塞进
    `DecisionEvent.evidence`，⛔ 不重新求值一遍（tasks 4.2）。重新求值会
    制造"判定时未知、留痕时又变成已知"的不一致（design D4）。
    """

    allowed: bool
    reason: str | None
    evidence: dict[str, Any] = field(default_factory=dict)
    absent_fields: tuple[str, ...] = ()
    error: str | None = None


def _read(message: object, name: str) -> Any:
    """读一个属性，读不到返回 `_ABSENT`。

    ⛔ 用两参 `getattr` + `except AttributeError`，**不用三参 getattr**。
    三参写法把"没有这个属性"和"属性值恰好等于那个默认值"折成同一件事，
    fail-closed 当场变 fail-open（delivery-units §3.3 点名的那种一行重构）。

    属性是个会抛别的异常的 property 时，异常原样向上抛——由
    `compute_outbound_gate()` 的外壳统一兜成"拦截"。
    """
    try:
        return getattr(message, name)
    except AttributeError:
        return _ABSENT


def _json_safe(value: Any) -> Any:
    """把任意取值折成 json.dumps 认识的形状，信息不丢。

    缺失折成 None（U2 的 DecisionEvent.evidence 是扁平 dict）；str / int /
    float / bool / None 原样保留；其余一律 repr()——U5 的 JSONL append 因
    一个奇怪的字段值而抛错，是本可避免的故障。
    """
    if value is _ABSENT:
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _has_ai_label(body: Any) -> bool:
    """正文里有没有 AI 生成标识。非字符串正文一律判"没有"（未知即拦截）。"""
    return isinstance(body, str) and AI_LABEL_PREFIX in body


def _collect(message: object, outbound_enabled: Any) -> tuple[dict[str, Any], dict[str, Any], tuple[str, ...]]:
    """一次采齐：原始取值、证据字典、缺失字段清单。

    ⚠️ **不跟着判定短路**：被第一条规则拦下的消息，其余字段的原始取值
    同样要在证据里，否则留痕读不出是哪一条 fail-closed 触发的。
    """
    raw = {name: _read(message, name) for name in GATE_FIELDS}
    absent_fields = tuple(name for name in GATE_FIELDS if raw[name] is _ABSENT)

    # 开关在这里求值，**每次判定恰好一次**。放在采集阶段而不是判定末尾，
    # 是为了让证据里恒有它的原始取值（哪怕消息先被别的规则拦下）。
    if callable(outbound_enabled):
        switch_raw: Any = outbound_enabled()
    else:
        switch_raw = _ABSENT

    evidence = {
        "message_type": _json_safe(raw["message_type"]),
        "requires_confirmation": _json_safe(raw["requires_confirmation"]),
        "severity": _json_safe(raw["severity"]),
        "recipient": _json_safe(raw["recipient"]),
        "confirmed_by": _json_safe(raw["confirmed_by"]),
        "ai_label_present": _has_ai_label(raw["body"]),
        "outbound_enabled": _json_safe(switch_raw),
    }
    raw["_switch"] = switch_raw
    return raw, evidence, absent_fields


def compute_outbound_gate(
    message: object, outbound_enabled: Callable[[], bool]
) -> GateDecision:
    """候选人外发门禁判定。本 Task 只到证据采集，判定在 Task 3 接上。"""
    raw, evidence, absent_fields = _collect(message, outbound_enabled)
    return GateDecision(
        allowed=False, reason=None, evidence=evidence, absent_fields=absent_fields
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_outbound_gate.py -q`
Expected: PASS（Task 1 的 4 条 + 本 Task 的 10 条，共 14 passed）

- [ ] **Step 5: 提交**

```bash
git add app/outbound/gate.py tests/test_outbound_gate.py
git commit -m "feat(outbound): 门禁证据采集，absent/None/空串三态如实落到底（tasks 4.2）"
```

---

### Task 3: fail-closed 六条判定（tasks 4.2 / 4.4 / 4.6 —— 本单元主防线）

**Files:**
- Modify: `app/outbound/gate.py`
- Modify: `app/outbound/__init__.py`（补出口）
- Modify: `tests/test_outbound_gate.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `_collect` / `_json_safe` / `GateDecision`；Task 1 的 `REGISTERED_MESSAGE_TYPES` / `KNOWN_SEVERITIES` / `MAX_SEVERITY`
- Produces: 六个拦截原因常量 `REASON_UNREGISTERED_TYPE` / `REASON_CONFIRMATION_FLAG_UNKNOWN` / `REASON_CONFIRMATION_REQUIRED` / `REASON_SEVERITY_UNKNOWN` / `REASON_SEVERITY_MAX` / `REASON_MISSING_AI_LABEL`（Task 4 再加三个），以及判定后的 `GateDecision.reason`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_outbound_gate.py`：

```python
from app.outbound.contracts import REGISTERED_MESSAGE_TYPES


def test_a_bare_object_with_no_attributes_at_all_is_blocked():
    """
    ⭐ **本单元的主防线**（delivery-units §3.3 第 1 条逐字要求）。

    这条用例是唯一能在"后来者写一句 getattr(msg, 'requires_confirmation',
    False) 当作合理默认值"式重构下变红的：一个连 message_type 属性都没有
    的裸对象喂进来，必须拦。所有既有用例喂的都是字段齐全的消息，那种
    重构在它们眼里全绿。
    """
    decision = compute_outbound_gate(object(), lambda: True)

    assert decision.allowed is False
    assert decision.reason is not None
    assert decision.absent_fields == (
        "message_type",
        "requires_confirmation",
        "severity",
        "recipient",
        "body",
        "confirmed_by",
    )


@pytest.mark.parametrize("message_type", sorted(REGISTERED_MESSAGE_TYPES))
@pytest.mark.parametrize(
    "field_name", ["requires_confirmation", "severity", "recipient", "body"]
)
@pytest.mark.parametrize("bad_kind", ["absent", "none", "empty"])
def test_registered_types_are_blocked_for_every_unknown_field_value(
    message_type, field_name, bad_kind
):
    """
    「已登记类型 × 判定字段 × {字段缺失, 字段为 None, 字段为空串}」的笛卡尔积
    （delivery-units §3.3 第 1 条）。新增一个消息类型时，参数化会强制作者
    面对每一种未知取值——这正是它铺满的意义。

    ⚠️ field_name 这一维**不含 confirmed_by**：那道闸在 Task 4 才接上，
    放进来会让本 Task 的用例在实现尚未落地时就红。confirmed_by 的缺失 /
    None / 空白三态由 Task 4 的
    test_missing_or_blank_confirmer_is_blocked_awaiting_confirmation 覆盖。

    ⚠️ 枚举用 REGISTERED_MESSAGE_TYPES（新增类型自动进入覆盖），
    但判据是字面量 False，不引用任何被测常量。
    """
    fields = {
        "message_type": message_type,
        "requires_confirmation": False,
        "severity": "low",
        "recipient": "candidate-42",
        "body": _LABELLED_BODY,
        "confirmed_by": "shao-peishen",
    }
    if bad_kind == "absent":
        del fields[field_name]
    elif bad_kind == "none":
        fields[field_name] = None
    else:
        fields[field_name] = ""

    decision = compute_outbound_gate(_Message(**fields), lambda: True)

    assert decision.allowed is False


def test_unregistered_message_type_is_blocked_with_its_own_reason():
    decision = compute_outbound_gate(
        _valid_message(message_type="offer_letter"), lambda: True
    )

    assert decision.allowed is False
    assert decision.reason == "未登记的消息类型"


@pytest.mark.parametrize("flag", [None, "", "false", "true", 0, 1, "False"])
def test_non_boolean_confirmation_flag_is_unknown_and_blocked(flag):
    """
    ⚠️ 严格 `is False` / `is True` 判定，不用真值性。
    字符串 "false" 的真值性是 True，"0" 也是 True——用 if flag: 写这条
    规则，一个字符串开关就把 fail-closed 变成了 fail-open。
    整数 0/1 同理：它们不是布尔，就是未知。
    """
    decision = compute_outbound_gate(
        _valid_message(requires_confirmation=flag), lambda: True
    )

    assert decision.allowed is False


def test_confirmation_flag_true_and_unknown_have_different_reasons():
    """
    "消息自称需要确认"与"这个标志读不出来"是两回事：前者是消息作者的
    显式意图，后者是消息畸形。6.5 按拦截原因统计时两者必须分得开。
    """
    explicit = compute_outbound_gate(
        _valid_message(requires_confirmation=True), lambda: True
    )
    unknown = compute_outbound_gate(
        _valid_message(requires_confirmation=None), lambda: True
    )

    assert explicit.allowed is False
    assert unknown.allowed is False
    assert explicit.reason != unknown.reason


@pytest.mark.parametrize("severity", [None, "", "  ", "critical", "LOW", "低", 3])
def test_unknown_severity_is_blocked(severity):
    """词表外的取值一律未知。大小写不同也算未知——不做归一化。"""
    decision = compute_outbound_gate(_valid_message(severity=severity), lambda: True)

    assert decision.allowed is False
    assert decision.reason == "风险等级缺失或未登记"


def test_top_severity_is_blocked_with_its_own_reason():
    decision = compute_outbound_gate(_valid_message(severity="high"), lambda: True)

    assert decision.allowed is False
    assert decision.reason == "风险等级为最高级"


def test_missing_ai_label_is_blocked():
    """《AI 生成合成内容标识办法》：拒信/邀约缺标识按拦截处理（tasks 4.4）。"""
    decision = compute_outbound_gate(
        _valid_message(body="很遗憾，本次未能与您继续推进。"), lambda: True
    )

    assert decision.allowed is False
    assert decision.reason == "缺少 AI 生成标识"


@pytest.mark.parametrize(
    "body",
    [
        "AI 生成：本文案由系统自动生成。",  # 缺【】书名号，不是那句标识
        "【AI生成】本文案由系统基于岗位画像自动生成，生成时间 2026-08-28。",  # 少一个空格
        "【AI 生成】",  # 只有标记头，没有那句话
        b"\xe3\x80\x90AI",  # 根本不是 str
    ],
)
def test_near_miss_labels_do_not_count_as_labelled(body):
    """
    近似但不相同的标识不算数。判据是 jd_agent 那句模板的不变前缀全量匹配
    （见「需 Shao Peishen 拍板」D-1，当前取最严的一侧）。
    """
    decision = compute_outbound_gate(_valid_message(body=body), lambda: True)

    assert decision.allowed is False
    assert decision.reason == "缺少 AI 生成标识"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_outbound_gate.py -q`
Expected: FAIL —— `assert None == '未登记的消息类型'` 之类（Task 2 的实现恒返回 `reason=None`）

- [ ] **Step 3: 写最小实现**

改 `app/outbound/gate.py`：在 import 段追加 `MAX_SEVERITY` / `KNOWN_SEVERITIES` / `REGISTERED_MESSAGE_TYPES`，在 `EVIDENCE_KEYS` 之后加原因常量，并把 `compute_outbound_gate` 换成薄壳 + `_evaluate_outbound_gate`：

```python
from app.outbound.contracts import (
    GATE_FIELDS,
    KNOWN_SEVERITIES,
    MAX_SEVERITY,
    REGISTERED_MESSAGE_TYPES,
)

# 拦截原因。取值是**中文字面量**而不是英文枚举码：spec 逐字写了
# 「外发总开关关闭」与「等待人工确认」两条要能区分开，U5 会把它原样写进
# pending_approval.blocked_reason，U6 的 6.5 直接 GROUP BY 这一列。
REASON_UNREGISTERED_TYPE = "未登记的消息类型"
REASON_CONFIRMATION_FLAG_UNKNOWN = "确认标志缺失或取值未知"
REASON_CONFIRMATION_REQUIRED = "消息自称需要人工确认"
REASON_SEVERITY_UNKNOWN = "风险等级缺失或未登记"
REASON_SEVERITY_MAX = "风险等级为最高级"
REASON_MISSING_AI_LABEL = "缺少 AI 生成标识"
# 第七条，2026-08-28 拍板新增（spec 的六条之外，方向更严）
REASON_RECIPIENT_UNKNOWN = "收件对象缺失或为空"
```

判定主体（替换 Task 2 里那个占位的 `compute_outbound_gate`）：

```python
def _evaluate_outbound_gate(
    message: object, outbound_enabled: Callable[[], bool]
) -> GateDecision:
    """实际判定逻辑，**可能抛异常**——外壳 compute_outbound_gate 统一兜成拦截。

    判定顺序是契约的一部分（见 plan 的 D-3）：**消息自身的六条 fail-closed
    先判，两道闸最后判**。理由是 design 迁移计划第 4 步——U5 合并时总开关
    保持关闭、全拦，要靠这段观察期看拦截留痕是否符合预期；总开关若先判，
    观察期内每一条拦截的 reason 都是"外发总开关关闭"，把其余五条真正的
    畸形消息全部盖住，观察期当场失去意义。
    """
    raw, evidence, absent_fields = _collect(message, outbound_enabled)

    def blocked(reason: str) -> GateDecision:
        return GateDecision(
            allowed=False, reason=reason, evidence=evidence, absent_fields=absent_fields
        )

    # ① 未知类型即拦截。_ABSENT 不在集合里，缺属性天然落进这一条。
    if raw["message_type"] not in REGISTERED_MESSAGE_TYPES:
        return blocked(REASON_UNREGISTERED_TYPE)

    # ② 严格布尔。⛔ 不用真值性：字符串 "false" 的真值性是 True。
    flag = raw["requires_confirmation"]
    if flag is not True and flag is not False:
        return blocked(REASON_CONFIRMATION_FLAG_UNKNOWN)

    # ③ 消息自称需要确认。
    if flag is True:
        return blocked(REASON_CONFIRMATION_REQUIRED)

    # ④ 风险等级必须是词表里的字符串。不做大小写归一化、不 strip——
    #    归一化就是在猜作者的意图，而未知即拦截不允许猜。
    severity = raw["severity"]
    if severity not in KNOWN_SEVERITIES:
        return blocked(REASON_SEVERITY_UNKNOWN)

    # ⑤ 最高级一律拦。
    if severity == MAX_SEVERITY:
        return blocked(REASON_SEVERITY_MAX)

    # ⑥ AI 生成标识（tasks 4.4，复用 jd_agent 的模板）。
    if not evidence["ai_label_present"]:
        return blocked(REASON_MISSING_AI_LABEL)

    # 两道闸在 Task 4 接上。
    return GateDecision(
        allowed=True, reason=None, evidence=evidence, absent_fields=absent_fields
    )


def compute_outbound_gate(
    message: object, outbound_enabled: Callable[[], bool]
) -> GateDecision:
    """候选人外发门禁判定。纯函数：不写库、不发消息、不读配置文件。

    Args:
        message: 待外发消息。**任何形状都合法**——连属性都没有的裸对象
            也必须能喂进来（fail-closed 的输入面），它会被判拦截并带着
            完整证据返回，而不是抛错。
        outbound_enabled: **零参 callable**，每次判定恰好被调用一次
            （spec：总开关 MUST 在每次外发时求值，MUST NOT 启动时缓存）。
            ⛔ 传 bool 属结构性误用，按拦截处理——见 Task 4。

    Returns:
        GateDecision。`allowed is True` 是唯一的放行信号；⛔ 调用方不得用
        真值性判断这个对象本身（GateDecision 实例恒为真）。
    """
    try:
        return _evaluate_outbound_gate(message, outbound_enabled)
    except Exception as exc:  # noqa: BLE001 —— 见下方注释，这里就是要抓全部
        # ⛔ 契约由**结构**保证，不靠枚举异常类型。U1 的
        # is_candidate_outbound_enabled() 用枚举法失败过两次（round 1 漏
        # OSError 之外的类型，round 2 被 NUL 字节路径的裸 ValueError 逃掉，
        # 见 tasks.md「1.x 落地偏离登记」偏离 5），最后改成同一个形状：
        # 内部逻辑整体委托给一个私有函数，外层只做一件事——不管里面抛出
        # 什么类型（哪怕是完全没预料到的新类型），一律截停判拦截。
        # 之后任何人往判定里加一段没包线的新异常来源，都不需要再补一轮修复。
        return GateDecision(
            allowed=False,
            reason=REASON_GATE_ERROR,
            evidence={key: None for key in EVIDENCE_KEYS},
            absent_fields=(),
            error=repr(exc),
        )
```

> `REASON_GATE_ERROR` 在 Task 5 定义；本 Task 先把外壳写出来，Task 5 的第一步就是补上这个常量并测它。**若实施者希望本 Task 结束时全绿，可在本 Task 一并加入 `REASON_GATE_ERROR = "门禁判定内部异常"` 这一行**（它没有行为，只是常量）。

改 `app/outbound/__init__.py`，在 docstring 之后追加出口：

```python
from app.outbound.gate import GateDecision, compute_outbound_gate

__all__ = ["GateDecision", "compute_outbound_gate"]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_outbound_gate.py -q`
Expected: PASS（笛卡尔积 2×3×3=18 条 + 其余，累计约 50 passed）

- [ ] **Step 5: 变异验证（不可跳过）**

把主防线故意打破一次，确认它真的会红：

```bash
# 1) 把 _read 的两参 getattr 改成三参默认值写法（模拟"看起来无害的重构"）
#    app/outbound/gate.py: return getattr(message, name)
#                       →  return getattr(message, name, False)
./venv/bin/python -m pytest tests/test_outbound_gate.py -q 2>&1 | tail -3
```

Expected: **FAIL**，且失败用例里必须包含 `test_a_bare_object_with_no_attributes_at_all_is_blocked`。
若它照样绿，说明主防线是假的，**停下重写**。验证完 `git checkout app/outbound/gate.py` 撤销这次变异。

- [ ] **Step 6: 提交**

```bash
git add app/outbound/gate.py app/outbound/__init__.py tests/test_outbound_gate.py
git commit -m "feat(outbound): fail-closed 六条判定与裸对象主防线（tasks 4.2/4.4/4.6）"
```

---

### Task 4: 两道闸——人工确认与外发总开关（tasks 4.5 / 4.7 / 4.8）

**Files:**
- Modify: `app/outbound/gate.py`
- Modify: `tests/test_outbound_gate.py`（追加）

**Interfaces:**
- Consumes: Task 3 的 `_evaluate_outbound_gate`
- Produces: `REASON_AWAITING_CONFIRMATION = "等待人工确认"`、`REASON_OUTBOUND_DISABLED = "外发总开关关闭"`、`REASON_SWITCH_NOT_CALLABLE = "外发总开关未以 callable 形式传入"`、`ALL_BLOCK_REASONS: frozenset[str]`（U6 的 6.5 按它 GROUP BY）

**⚠️ 本 Task 是本单元的合规核心**：`delivery-units.md` §3.5 是 Shao Peishen 2026-08-26 的拍板结论（选项 A：代码默认关、`.env` 显式开；允许热改不重启），本 Task 的三条测试是那个拍板在门禁这一侧的落地判据。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_outbound_gate.py`：

```python
from app.config import get_settings, is_candidate_outbound_enabled


def test_the_only_release_path():
    """
    tasks 4.7：放行的唯一路径 = 类型已登记 + requires_confirmation 显式为假
    + severity 已知非最高级 + 标识齐备 + 带 confirmed_by + 总开关开启。
    这条是全套用例里**唯一**一条 allowed is True，改动它等于改动红线。
    """
    decision = compute_outbound_gate(_valid_message(), lambda: True)

    assert decision.allowed is True
    assert decision.reason is None
    assert decision.error is None


@pytest.mark.parametrize("confirmed_by", [None, "", "   ", 0, False, ["shao"]])
def test_missing_or_blank_confirmer_is_blocked_awaiting_confirmation(confirmed_by):
    """
    spec「人工确认才放行」：确认人标识为空的高风险消息 MUST 被拦截。
    空白串也算空——一个全是空格的 confirmed_by 不是人。
    """
    decision = compute_outbound_gate(
        _valid_message(confirmed_by=confirmed_by), lambda: True
    )

    assert decision.allowed is False
    assert decision.reason == "等待人工确认"


def test_an_absent_confirmed_by_attribute_is_blocked_awaiting_confirmation():
    """
    「属性根本不存在」这一态：Task 3 的笛卡尔积不覆盖 confirmed_by（那道闸
    当时还没接上），这里补齐。⛔ 缺属性走的必须是同一条拦截路径，
    不能因为读不到就掉进别的分支。
    """
    fields = {
        "message_type": "rejection_letter",
        "requires_confirmation": False,
        "severity": "low",
        "recipient": "candidate-42",
        "body": _LABELLED_BODY,
    }

    decision = compute_outbound_gate(_Message(**fields), lambda: True)

    assert decision.allowed is False
    assert decision.reason == "等待人工确认"
    assert decision.absent_fields == ("confirmed_by",)


def test_confirmed_message_is_still_blocked_when_the_master_switch_is_off():
    """
    tasks 4.8 / spec「第二道结构性总开关」：总开关关闭时，**即便消息已携带
    人工确认人标识**也不外发，且 reason 与「等待人工确认」区分开。
    """
    decision = compute_outbound_gate(_valid_message(), lambda: False)

    assert decision.allowed is False
    assert decision.reason == "外发总开关关闭"
    assert decision.evidence["confirmed_by"] == "shao-peishen"
    assert decision.evidence["outbound_enabled"] is False


def test_awaiting_confirmation_wins_over_switch_off_so_the_observation_window_stays_readable():
    """
    判定顺序的锁定用例（见 plan 的 D-3）：没带确认人 + 总开关也关着时，
    reason 是「等待人工确认」而不是「外发总开关关闭」。

    为什么这条重要：U5 合并时总开关保持关闭（design 迁移计划第 4 步），
    那段观察期里**每一条**外发都会撞上关着的总开关。若总开关先判，
    观察期内所有拦截留痕的 reason 都是同一句话，"某类消息一直在被拦"
    这个 6.5 想回答的问题就永远读不出答案。
    """
    decision = compute_outbound_gate(_valid_message(confirmed_by=None), lambda: False)

    assert decision.allowed is False
    assert decision.reason == "等待人工确认"


@pytest.mark.parametrize("switch_value", ["true", "1", 1, "false", [], object()])
def test_only_the_literal_true_opens_the_switch(switch_value):
    """
    ⚠️ 开关回来的必须**恰好是 True 这个对象**。用真值性判断的话，
    一个返回字符串 "false" 的开关会把闸门打开——"false" 的真值性是 True。
    这正是 U1 的 _as_switch() 在配置那一侧堵的同一个洞（未知即关）。
    """
    decision = compute_outbound_gate(_valid_message(), lambda: switch_value)

    assert decision.allowed is False


@pytest.mark.parametrize("not_callable", [True, False, 1, "true", None])
def test_a_non_callable_switch_is_structural_misuse_and_blocks(not_callable):
    """
    delivery-units §3.5 硬约束 1：⛔ 禁止在模块导入期、__init__ 里、或任何
    单例上把开关读成一个常量。传进来一个 bool 就是那个失败形状的现场——
    值是什么已经不重要，它已经被缓存过了。判拦截，并给一个能一眼看懂的原因。
    """
    decision = compute_outbound_gate(_valid_message(), not_callable)

    assert decision.allowed is False
    assert decision.reason == "外发总开关未以 callable 形式传入"


def test_switch_callable_is_invoked_exactly_once_per_decision():
    """
    "每次外发时求值"的两面：不能一次都不调（那就是缓存），也不能调多次
    （多次调用之间开关可能变，一次判定里出现两个不同的开关状态）。
    """
    calls = []

    def switch():
        calls.append(1)
        return True

    compute_outbound_gate(_valid_message(), switch)
    assert len(calls) == 1

    compute_outbound_gate(_valid_message(message_type="offer_letter"), switch)
    assert len(calls) == 2  # 被第一条规则拦下也照样求值，证据里要有它


def test_switch_flipped_at_runtime_takes_effect_on_the_next_decision():
    """
    spec「总开关运行期间被关闭」：此后的外发请求立即被拦截，**无需重启**。
    """
    state = {"on": True}
    switch = lambda: state["on"]

    assert compute_outbound_gate(_valid_message(), switch).allowed is True

    state["on"] = False

    second = compute_outbound_gate(_valid_message(), switch)
    assert second.allowed is False
    assert second.reason == "外发总开关关闭"


# ── 合规默认值必须真正参与求值 ──────────────────────────────────────────
#
# U1 那轮的教训（tasks.md「1.x 落地偏离登记」偏离 5 末段）：合规默认值被改
# 成 True 却无人发现，是因为所有用例都在喂桩、没有一条逼真实默认值参与。
# 下面这一对用例把 app/config.py 的**真实** is_candidate_outbound_enabled
# 接进门禁，且消息在其余六条上全部合格——只有这样才能走到最后一道闸，
# 让基线默认值 False 成为唯一的拦截理由。⛔ 不允许用喂空消息走 None 分支
# 的方式"覆盖"这条：那是把默认值绕过去，不是逼它求值。


@pytest.fixture
def _real_switch_env(tmp_path, monkeypatch):
    """把真实开关指到一个不存在的临时文件，清干净环境变量与 Settings 缓存。

    形状照抄 tests/test_config_audit_and_outbound.py 的 switch_path 夹具——
    那是 U1 已经验证过的隔离方式，别另起一套。
    """
    path = tmp_path / "candidate_outbound.switch"
    monkeypatch.setenv("CANDIDATE_OUTBOUND_SWITCH_FILE", str(path))
    monkeypatch.delenv("CANDIDATE_OUTBOUND_ENABLED", raising=False)
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


def test_real_config_baseline_default_closes_the_gate_for_an_otherwise_valid_message(
    _real_switch_env,
):
    """
    没有开关文件、没有环境变量 → 走到基线值 candidate_outbound_enabled=False。
    消息本身完全合格，所以拦截理由只可能来自那个默认值。

    变异验证（Step 5 会跑）：把 app/config.py 的
    `candidate_outbound_enabled: bool = False` 改成 True，本条必须变红。
    """
    decision = compute_outbound_gate(_valid_message(), is_candidate_outbound_enabled)

    assert decision.allowed is False
    assert decision.reason == "外发总开关关闭"
    assert decision.evidence["outbound_enabled"] is False


def test_real_switch_file_opens_the_same_message_that_the_default_closed(
    _real_switch_env,
):
    """
    ⭐ 上一条的**阳性对照**，缺了它上一条就是"恒真"的：一条永远过不了
    其余六条的消息也会拿到 allowed is False，看不出默认值有没有参与。
    同一条消息、只把开关文件写上 true，就必须放行——两条合起来才证明
    "拦截确实是那个默认值造成的"。
    """
    _real_switch_env.parent.mkdir(parents=True, exist_ok=True)
    _real_switch_env.write_text("true", encoding="utf-8")

    decision = compute_outbound_gate(_valid_message(), is_candidate_outbound_enabled)

    assert decision.allowed is True
    assert decision.reason is None


def test_all_block_reasons_is_the_closed_set_u6_will_group_by():
    """
    U6 的 6.5「按 message_type 与拦截原因统计」要求原因取值来自一个有限
    集合。断言用字面量集合——加一个原因就该在这里显性变红，让作者顺手去
    U6 补一行统计口径，而不是让新原因静默地掉进"其他"桶里。
    """
    from app.outbound.gate import ALL_BLOCK_REASONS

    assert ALL_BLOCK_REASONS == frozenset(
        {
            "未登记的消息类型",
            "确认标志缺失或取值未知",
            "消息自称需要人工确认",
            "风险等级缺失或未登记",
            "风险等级为最高级",
            "缺少 AI 生成标识",
            "收件对象缺失或为空",
            "等待人工确认",
            "外发总开关关闭",
            "外发总开关未以 callable 形式传入",
            "门禁判定内部异常",
        }
    )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_outbound_gate.py -q`
Expected: FAIL —— `test_the_only_release_path` 之外的两道闸用例全红（Task 3 的实现在标识通过后就直接放行了）

- [ ] **Step 3: 写最小实现**

改 `app/outbound/gate.py`：补三个原因常量与 `ALL_BLOCK_REASONS`，并在 `_evaluate_outbound_gate` 的第 ⑥ 条之后接上两道闸。

```python
REASON_AWAITING_CONFIRMATION = "等待人工确认"
REASON_OUTBOUND_DISABLED = "外发总开关关闭"
REASON_SWITCH_NOT_CALLABLE = "外发总开关未以 callable 形式传入"
REASON_GATE_ERROR = "门禁判定内部异常"

# U6 的 6.5 按拦截原因统计，需要一个封闭集合。新增原因必须同时加进这里。
ALL_BLOCK_REASONS: frozenset[str] = frozenset(
    {
        REASON_UNREGISTERED_TYPE,
        REASON_CONFIRMATION_FLAG_UNKNOWN,
        REASON_CONFIRMATION_REQUIRED,
        REASON_SEVERITY_UNKNOWN,
        REASON_SEVERITY_MAX,
        REASON_MISSING_AI_LABEL,
        REASON_RECIPIENT_UNKNOWN,
        REASON_AWAITING_CONFIRMATION,
        REASON_OUTBOUND_DISABLED,
        REASON_SWITCH_NOT_CALLABLE,
        REASON_GATE_ERROR,
    }
)
```

`_evaluate_outbound_gate` 尾部（替换 Task 3 的 `return GateDecision(allowed=True, ...)`）：

```python
    # ⑦ 收件对象必须是一个非空字符串（2026-08-28 拍板新增第七条，见
    #    「## 五项口径 —— ✅ 已拍板」D-2）。⛔ 非字符串同样判未知。
    recipient = raw["recipient"]
    if not isinstance(recipient, str) or not recipient.strip():
        return blocked(REASON_RECIPIENT_UNKNOWN)

    # ⑧ 第一道闸：人工确认。spec「人工确认才放行」——确认人标识为空的
    #    高风险消息 MUST 被拦截。空白串不是人。
    confirmed_by = raw["confirmed_by"]
    if not isinstance(confirmed_by, str) or not confirmed_by.strip():
        return blocked(REASON_AWAITING_CONFIRMATION)

    # ⑨ 第二道闸：外发总开关。⛔ 必须是 callable——传进来一个 bool 说明
    #    调用方已经把它缓存成值了，那正是 spec 禁止的"启动时缓存一次"。
    if not callable(outbound_enabled):
        return blocked(REASON_SWITCH_NOT_CALLABLE)

    # ⑩ 只有**恰好是 True** 才算开。⛔ 不用真值性：字符串 "false" 的真值性
    #    是 True，一个字符串开关就能把闸门打开。与 U1 的 _as_switch()
    #    在配置那一侧的口径一致——未知即关。
    if raw["_switch"] is not True:
        return blocked(REASON_OUTBOUND_DISABLED)

    return GateDecision(
        allowed=True, reason=None, evidence=evidence, absent_fields=absent_fields
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_outbound_gate.py -q`
Expected: PASS

- [ ] **Step 5: 变异验证——逼真实默认值参与求值（不可跳过，合规红线）**

```bash
# 把 U1 的合规默认值反过来，确认那一对用例真的咬住了它
sed -i '' 's/    candidate_outbound_enabled: bool = False/    candidate_outbound_enabled: bool = True/' app/config.py
./venv/bin/python -m pytest tests/test_outbound_gate.py -q 2>&1 | tail -5
```

Expected: **FAIL**，失败用例里必须出现
`test_real_config_baseline_default_closes_the_gate_for_an_otherwise_valid_message`。
若它照样绿 → 默认值没有真正参与求值，**停下重写这一对用例**。

```bash
git checkout app/config.py   # 撤销变异，app/config.py 一个字节都不许改
git diff --stat app/config.py   # 必须为空
```

- [ ] **Step 6: 提交**

```bash
git add app/outbound/gate.py tests/test_outbound_gate.py
git commit -m "feat(outbound): 两道闸——人工确认与总开关每次求值（tasks 4.5/4.7/4.8）"
```

---

### Task 5: 异常路径、纯函数性与结构性守护（tasks 4.3 / 4.9）

**Files:**
- Modify: `app/outbound/gate.py`（若 `REASON_GATE_ERROR` 已在 Task 4 落地，本 Task 不改实现）
- Create: `tests/test_outbound_gate_structure.py`
- Modify: `tests/test_outbound_gate.py`（追加异常路径与纯函数性）

**Interfaces:**
- Consumes: Task 2–4 的全部产物
- Produces: 无新符号；本 Task 交付的是**守护**

- [ ] **Step 1: 写失败测试（行为面）**

追加到 `tests/test_outbound_gate.py`：

```python
def test_exception_inside_the_gate_is_treated_as_a_block_not_a_leak():
    """
    tasks 4.3 / spec「门禁判定自身抛错」：按拦截处理，MUST NOT 因判定失败
    而放行。异常穿透到调用方，调用方一个 `except: pass` 就是 fail-open。
    """

    class _Exploding:
        message_type = "rejection_letter"
        requires_confirmation = False
        severity = "low"
        recipient = "candidate-42"
        confirmed_by = "shao-peishen"

        @property
        def body(self):
            raise RuntimeError("读 body 炸了")

    decision = compute_outbound_gate(_Exploding(), lambda: True)

    assert decision.allowed is False
    assert decision.reason == "门禁判定内部异常"
    assert "读 body 炸了" in decision.error


def test_an_exception_type_nobody_enumerated_still_closes_the_gate():
    """
    ⛔ 契约由结构保证，不靠枚举异常类型。U1 的枚举法失败过两次
    （tasks.md 偏离 5）：round 1 漏了 OSError 之外的类型，round 2 被 NUL
    字节路径的裸 ValueError 逃掉。这里直接抛一个本仓库里根本不存在的
    异常类型，闸门照样必须关。
    """

    class _NobodyEverHeardOfThis(Exception):
        pass

    def switch():
        raise _NobodyEverHeardOfThis("全新的失败形状")

    decision = compute_outbound_gate(_valid_message(), switch)

    assert decision.allowed is False
    assert decision.reason == "门禁判定内部异常"


def test_keyboard_interrupt_is_not_swallowed():
    """
    兜底只抓 Exception，⛔ 不抓 BaseException：把 KeyboardInterrupt /
    SystemExit 吞成一条"拦截"会让进程杀不掉，那不是 fail-closed，是挂死。
    """

    def switch():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        compute_outbound_gate(_valid_message(), switch)


@pytest.mark.parametrize(
    "message_factory",
    [
        lambda: _valid_message(),
        lambda: _valid_message(severity="high"),
        lambda: _Message(),
        lambda: object(),
    ],
)
def test_repeated_evaluation_is_identical(message_factory):
    """tasks 4.9 / spec「重复判定结果一致」。"""
    message = message_factory()

    first = compute_outbound_gate(message, lambda: True)
    second = compute_outbound_gate(message, lambda: True)

    assert first == second


def test_judging_writes_nothing_to_disk(tmp_path, monkeypatch):
    """
    tasks 4.9 后半句：判定过程无任何持久化写入与消息投递。
    在一个空目录里当工作目录跑一遍判定，目录必须一个文件都不多。
    """
    monkeypatch.chdir(tmp_path)
    before = {p.name for p in tmp_path.iterdir()}

    compute_outbound_gate(_valid_message(), lambda: True)
    compute_outbound_gate(object(), lambda: False)

    assert {p.name for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize(
    "recipient", ["", "   ", None, 0, ["candidate-42"], {"open_id": "ou_x"}]
)
def test_unknown_recipient_is_blocked_per_the_2026_08_28_ruling(recipient):
    """
    ⚠️ **口径锁定用例。批准人：Shao Peishen｜时间：2026-08-28｜事项：D-2
    取最保险一侧。** 见本 plan 的「## 五项口径 —— ✅ 已拍板」。

    spec 的 fail-closed 条件清单只列了六条，recipient 不在其中；本单元最初
    按 spec 字面落成"只进证据、不参与判定"，2026-08-28 拍板改为**第七条**
    拦截规则：收件对象读不出一个非空字符串就是未知，未知即拦截。
    非字符串（dict / list）同样判未知——门禁不猜"这个结构里哪个键是收件人"。
    """
    decision = compute_outbound_gate(_valid_message(recipient=recipient), lambda: True)

    assert decision.allowed is False
    assert decision.reason == "收件对象缺失或为空"


def test_absent_recipient_attribute_is_blocked_too():
    """「属性根本不存在」这一态：与空串走同一条拦截路径。"""
    fields = {
        "message_type": "rejection_letter",
        "requires_confirmation": False,
        "severity": "low",
        "body": _LABELLED_BODY,
        "confirmed_by": "shao-peishen",
    }

    decision = compute_outbound_gate(_Message(**fields), lambda: True)

    assert decision.allowed is False
    assert decision.reason == "收件对象缺失或为空"
    assert decision.absent_fields == ("recipient",)
```

- [ ] **Step 2: 写失败测试（结构面）**

新建 `tests/test_outbound_gate_structure.py`：

```python
"""`app/outbound` 的**源码形状**守护（交付单元 U4）。

这三条测的不是"门禁判得对不对"，而是"门禁的源码有没有腐化成 fail-open
的形状"。它们读 .py 源码解析 AST——用 AST 而不是正则，是因为正则会被
字符串字面量、注释和换行骗过去。
"""

import ast
import pathlib

import app.outbound.contracts
import app.outbound.gate

_SOURCE_FILES = {
    "gate.py": pathlib.Path(app.outbound.gate.__file__),
    "contracts.py": pathlib.Path(app.outbound.contracts.__file__),
}

_BANNED_IMPORT_PREFIXES = (
    "app.config",
    "app.storage",
    "app.channels",
    "app.graph",
    "app.audit",
    "app.web",
    "sqlite3",
)


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8"))


def test_gate_source_has_no_defaulted_attribute_reads():
    """
    delivery-units §3.3 逐字：`compute_outbound_gate` 内禁止出现带默认值的
    属性读取（getattr(x, k, <default>) / dict.get(k, <default>)）。
    取不到就是未知，未知就是拦截，**默认值这个概念本身与 fail-closed 互斥**。

    这是"后来者写一句 getattr(msg, 'requires_confirmation', False) 当作
    合理默认值"那种一行重构的机器判据。
    """
    offenders = []
    for name, path in _SOURCE_FILES.items():
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) >= 3:
                offenders.append(f"{name}:{node.lineno} 三参 getattr")
            if isinstance(func, ast.Attribute) and func.attr == "get" and len(node.args) >= 2:
                offenders.append(f"{name}:{node.lineno} 两参 .get")

    assert offenders == []


def test_outbound_package_imports_nothing_stateful():
    """
    U1 plan 点名要求：compute_outbound_gate 内部**不得** import app.config，
    开关只能由调用方以 callable 传入。delivery-units §2.U4 另要求 U4
    「逻辑上不依赖 U2/U3」——所以 app.audit 也在黑名单里。
    """
    offenders = []
    for name, path in _SOURCE_FILES.items():
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                if module.startswith(_BANNED_IMPORT_PREFIXES):
                    offenders.append(f"{name}:{node.lineno} {module}")

    assert offenders == []


def test_ai_label_source_is_the_jd_agent_constant():
    """
    tasks 4.4：**复用** app/agents/jd_agent.py 现有的 AI_LABEL_TEMPLATE
    机制判定，⛔ 不另写一套标识逻辑。断言的是**同一个对象**，
    照抄一份字面量过来会当场变红。
    """
    from app.agents.jd_agent import AI_LABEL_TEMPLATE

    assert app.outbound.gate.AI_LABEL_TEMPLATE is AI_LABEL_TEMPLATE


def test_ai_label_prefix_is_pinned_verbatim():
    """
    合规标识文案（《AI 生成合成内容标识办法》2025-09-01 施行）是红线资产，
    不该被静默改掉。这条把当前判定前缀逐字钉死——jd_agent 那句模板一变，
    这里就红，改动必须是有人看着的。
    """
    assert (
        app.outbound.gate.AI_LABEL_PREFIX
        == "【AI 生成】本文案由系统基于岗位画像自动生成，生成时间 "
    )


def test_gate_has_no_side_effect_vocabulary():
    """
    铁律 2：compute_* 无副作用。源码里出现这几个词就说明副作用爬进来了。
    """
    for name, path in _SOURCE_FILES.items():
        source = path.read_text(encoding="utf-8")
        for forbidden in ("@idempotent_effect", "INSERT INTO", "conn.execute", "channel.deliver"):
            assert forbidden not in source, f"{name} 里出现了 {forbidden}"
```

- [ ] **Step 3: 跑测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_outbound_gate.py tests/test_outbound_gate_structure.py -q`
Expected: FAIL —— 若 `REASON_GATE_ERROR` 尚未定义，异常路径三条报 `NameError`；结构面若实现干净则可能直接绿（**结构测试允许一开始就绿**，它守的是不腐化，不是新功能）。

- [ ] **Step 4: 补实现直至全绿**

`app/outbound/gate.py` 若还缺 `REASON_GATE_ERROR = "门禁判定内部异常"`，在原因常量段补上（Task 4 的 `ALL_BLOCK_REASONS` 已经引用了它）。除此之外本 Task 不应有任何实现改动——若结构面测试变红，**改的是实现的形状，不是测试的断言**。

Run: `./venv/bin/python -m pytest tests/test_outbound_gate.py tests/test_outbound_gate_structure.py -q`
Expected: PASS

- [ ] **Step 5: 变异验证结构防线（不可跳过）**

```bash
# 在 gate.py 里临时加一行 `import app.config`，确认导入黑名单真的咬住
./venv/bin/python -m pytest tests/test_outbound_gate_structure.py -q 2>&1 | tail -3
```

Expected: **FAIL** on `test_outbound_package_imports_nothing_stateful`。验完撤销。

- [ ] **Step 6: 全量回归**

```bash
./venv/bin/python -m pytest -q 2>&1 | tail -2
git diff --stat -- requirements.txt pyproject.toml app/config.py app/graph/nodes.py
```

Expected: 全绿，数字 ≥ `487 + 本单元新增用例数`；第二条命令输出**必须为空**（不新增依赖、不碰 U1 的配置文件、不碰 U5 的 `nodes.py`）。

- [ ] **Step 7: 提交**

```bash
git add app/outbound/gate.py tests/test_outbound_gate.py tests/test_outbound_gate_structure.py
git commit -m "feat(outbound): 异常按拦截处理、纯函数性与源码形状守护（tasks 4.3/4.9）"
```

---

## 交付前自查

- [ ] `grep -c '^### Task ' docs/superpowers/plans/2026-08-28-ai-audit-trail-unitU4-outbound-gate-pure-functions.md` > 0
- [ ] `git grep -n "compute_outbound_gate" -- app/ | grep -v "^app/outbound/"` **零命中**（U4 没有调用方，这是"可独立合并"的定义）
- [ ] `git diff --stat -- app/config.py app/storage/db.py app/graph/nodes.py app/agents/ openspec/` **为空**
- [ ] `git diff --stat -- requirements.txt pyproject.toml` **为空**（design「参考边界」：依赖文件 diff 必须为空）
- [ ] `git grep -n "zhuopin_platform" -- app/ tests/` **零命中**（本包三条硬边界之一）
- [ ] 全量 `./venv/bin/python -m pytest -q` 全绿
- [ ] 三次变异验证都跑过且都如期变红（Task 3 Step 5、Task 4 Step 5、Task 5 Step 5）
- [ ] 三条口径锁定用例都在且都绿：`test_unknown_recipient_is_blocked_per_the_2026_08_28_ruling`、`test_near_miss_labels_do_not_count_as_labelled`、`test_awaiting_confirmation_wins_over_switch_off_so_the_observation_window_stays_readable`

---

## spec 覆盖对照

| spec Requirement / Scenario | 落在哪 |
|---|---|
| 门禁覆盖范围：拒信、邀约两类 | Task 1 `REGISTERED_MESSAGE_TYPES` + `test_registered_message_types_are_exactly_the_two_candidate_facing_kinds` |
| 内部通知不受影响 | **U5**（tasks 5.9）。U4 只保证未登记类型一律拦截 |
| fail-closed：未登记类型 | Task 3 `test_unregistered_message_type_is_blocked_with_its_own_reason` |
| fail-closed：确认标志缺失/为空 | Task 3 `test_non_boolean_confirmation_flag_is_unknown_and_blocked` + 笛卡尔积 |
| fail-closed：确认标志为真 | Task 3 `test_confirmation_flag_true_and_unknown_have_different_reasons` |
| fail-closed：风险等级缺失/未知 | Task 3 `test_unknown_severity_is_blocked` |
| fail-closed：风险等级最高级 | Task 3 `test_top_severity_is_blocked_with_its_own_reason` |
| fail-closed：缺 AI 生成标识 | Task 3 `test_missing_ai_label_is_blocked` + `test_near_miss_labels_do_not_count_as_labelled` |
| fail-closed：收件对象缺失/为空（**第七条**） | `test_unknown_recipient_is_blocked_per_the_2026_08_28_ruling` + `test_absent_recipient_attribute_is_blocked_too`。2026-08-28 拍板新增，spec 同日已补 |
| fail-closed：属性根本不存在 | Task 3 `test_a_bare_object_with_no_attributes_at_all_is_blocked`（**主防线**） |
| 门禁判定自身抛错 → 拦截 | Task 5 `test_exception_inside_the_gate_is_treated_as_a_block_not_a_leak` + `test_an_exception_type_nobody_enumerated_still_closes_the_gate` |
| 人工确认才放行 | Task 4 `test_missing_or_blank_confirmer_is_blocked_awaiting_confirmation` |
| 两道闸都通过才发 | Task 4 `test_the_only_release_path` |
| 第二道结构性总开关：已确认也拦 | Task 4 `test_confirmed_message_is_still_blocked_when_the_master_switch_is_off` |
| 总开关每次求值、运行期改值立即生效 | Task 4 `test_switch_callable_is_invoked_exactly_once_per_decision` + `test_switch_flipped_at_runtime_takes_effect_on_the_next_decision` + 真实默认值那一对 |
| 拦截留痕含判定字段原始取值（含空值） | Task 2 `test_evidence_records_every_judged_field_even_when_blocked_by_the_first_rule` + `test_absent_attribute_is_distinguishable_from_an_explicit_none`。**写入**属 U5 |
| 判定与副作用分离、重复判定一致 | Task 5 `test_repeated_evaluation_is_identical` + `test_judging_writes_nothing_to_disk` + 结构面三条 |
| 待审批队列、放行、重放安全 | **U5**（tasks 5.1–5.8），不在本单元 |

---

## 本计划相对 `tasks.md` / `delivery-units.md` 的偏离登记（共 8 条，全部需 reviewer 确认）

> ⛔ 按并发约定，本节**不写进 `tasks.md`**（另一条 session 正在改那个文件）。合并时由 reviewer 决定是否搬运。

| # | 文件字面 | 本计划落地 | 方向 / 理由 |
|---|---|---|---|
| 1 | tasks 4.1 的 Protocol 字段是五个（`message_type` / `requires_confirmation` / `severity` / `recipient` / `body`） | 六个，多一个 `confirmed_by` | **保签名**。design D4 把签名定死为 `compute_outbound_gate(message, outbound_enabled)` 两参，而 tasks 4.7 要求判定"带 `confirmed_by`"。`confirmed_by` 只能挂在消息上。缺失方向是拦截，无 fail-open 风险 |
| 2 | tasks 4.5「支持传 callable」 | **只**接受 callable，传 bool 判拦截 | **更严**。"支持"是允许，本计划升级成强制——把 §3.5 硬约束 1「禁止把它读成一个常量」从约定变成类型上做不到 |
| 3 | tasks 4.2 的 `GateDecision` 是三字段（`allowed` / `reason` / `evidence`） | 五字段，多 `absent_fields` 与 `error` | **信息不丢**。U2 已落地的 `DecisionEvent.evidence` 是扁平 `dict[str, Any]`（见 `tests/test_audit_events.py::test_outbound_event_carries_gate_evidence`），absent 与 None 在扁平 dict 里必然同形；区别挪到 `absent_fields` 承载，`evidence` 保持 U5 可直接消费 |
| 4 | delivery-units §2.U4 只写了 `tests/test_outbound_gate.py` | 拆成行为面 + 结构面两个文件 | **可读性**。结构面读源码解析 AST，与行为用例不共享 fixture 也不共享失败信号 |
| 5 | spec 未规定判定顺序 | 六条 fail-closed 先判，两道闸最后判 | **口径**，见 D-3。总开关先判会在 U5 的观察期内把其余五条原因全部盖住 |
| 6 | spec「留痕记录判定所依据的各字段原始取值」 | `body` 不进 evidence，改记 `ai_label_present` 布尔 | **更保守**。拒信正文是候选人可识别内容；正文指纹由 U5 的 `content_hash` 承担 |
| 7 | spec 原本的六条拦截条件不含 recipient | **新增第七条拦截规则**：收件对象非空字符串才放行 | **更严**。2026-08-28 Shao Peishen 拍板取最保险一侧（见上节 D-2）。✅ spec 已同步补第七条，`validate --strict` 通过，代码与 spec 一致 |
| 8 | tasks 4.4「复用 `AI_LABEL_TEMPLATE`」未规定匹配强度 | 取模板 `{generated_at}` 之前的**不变前缀全量匹配** | **最严的一侧**，见 D-1。有逐字 pin 测试 |

---

## 五项口径 —— ✅ 已拍板（2026-08-28 Shao Peishen）

> **批准人：Shao Peishen（本项目唯一决策人）｜时间：2026-08-28｜事项：本节 D-1 至 D-5 全部**
> **依据：本人指示「五项拍板都按最保险落地确认」。** 留痕格式按 `CLAUDE.md`「决策代理」的要求。
> 其中 D-2 改变了已落地的行为，当次即改代码并补了回归测试；其余四项**当前落地本身就是最保险的一侧**，不改一行。

| 项 | 结论 | 是否改动代码 |
|---|---|---|
| **D-1** AI 标识判定强度 | **(a) 保持**：匹配 `AI_LABEL_TEMPLATE` 中 `{generated_at}` 之前的完整不变前缀。三个选项里最严的一侧 | 否 |
| **D-2** 空 `recipient` 是否拦截 | **(b) 改判**：新增**第七条** fail-closed 规则，收件对象读不出非空字符串即拦截；非字符串（dict/list）同样判未知 | **是** |
| **D-3** 拦截原因归属顺序 | **(a) 保持**：消息自身的畸形先判，两道闸最后判。见下方说明——「最保险」在这一项上不构成区分 | 否 |
| **D-4** 风险等级词表 | **(a) 保持** `("low","medium","high")`，最高级 `"high"`，实际过闸的只有 low / medium。**加 `critical` 反而更松**（`"high"` 会变成非最高级而放行），三档才是更严的一侧 | 否 |
| **D-5** 只接受 callable | **保持并追认**：传 bool 判拦截。把 §3.5 硬约束 1「禁止读成常量」从约定变成类型上做不到 | 否 |

**D-3 为什么「最保险」不构成区分（必须写明，否则这条追认是空的）**：两个选项下**放行/拦截的行为完全一致**——总开关关着时消息一律拦，先判后判都拦。差别只在留痕里记哪一条 `reason`。所以这一项没有「更安全的一侧」可选，判据只能是可观测性：总开关若先判，U5 合并后那段「全关」的观察期里每一条拦截留痕的 `reason` 都是同一句话，把其余六条真正的畸形消息全部盖住，design 迁移计划第 4 步「观察拦截留痕是否符合预期」当场失去意义。按此保持 (a)。
锁定用例：`test_awaiting_confirmation_wins_over_switch_off_so_the_observation_window_stays_readable`。

**D-2 落地细节与遗留**：

- 规则位置：排在六条之后、两道闸之前（编号 ⑦）。它是**消息自身的畸形**，与「等待人工确认」是两回事，6.5 按原因统计时必须分得开
- 新原因 `REASON_RECIPIENT_UNKNOWN = "收件对象缺失或为空"`，已进 `ALL_BLOCK_REASONS`
- 判据：非字符串收件人（dict / list）同样判未知——门禁不猜「这个结构里哪个键是收件人」，拍平成字符串是 U5 适配器的活
- 回归测试：`test_unknown_recipient_is_blocked_per_the_2026_08_28_ruling`（6 种未知取值参数化）、`test_absent_recipient_attribute_is_blocked_too`；Task 3 的笛卡尔积 `field_name` 维也把 `recipient` 加了进来（2×4×3=24 条）
- ✅ **spec 已同步（2026-08-28）**：`specs/outbound-approval-gate/spec.md` 的「fail-closed 判定语义」已补第七条条件、改写「仅当……才判为低风险」那句、新增 `Scenario: 收件对象未知`，并在原文里注明这一条是当日追加、方向更严、门禁不负责从渠道对象推断收件人。`openspec validate ai-audit-trail-and-outbound-gate --strict` 通过。代码与 spec 现在都是七条


## ⛔ D-6 —— 未决，**U5 开工前必须解决**（2026-08-28 code review 发现 3）

**问题一句话**：`requires_confirmation=True` 与 `severity` 最高级现在是**终局拦截**，带上 `confirmed_by` 也清不掉。若 U5 照 spec 把候选人信件标成高风险，`queue.approve()` 重走门禁仍会被拦——**待审批队列里的东西永远发不出去，人工放行路径整体失效**。

**两份已批准的文档在这一点上互相矛盾，不是我的实现走偏**：

| 出处 | 原文 | 推出的模型 |
|---|---|---|
| `tasks.md` 4.6 | 「`requires_confirmation` 为真 …… 全部拦截」 | 六条是**彼此独立的终局拦截条件** |
| `tasks.md` 4.7 | 「放行的唯一路径：…… + `requires_confirmation` **显式为假** + `severity` 已知非最高级 + …」 | 同上 |
| spec「门禁覆盖范围」 | 「这两类 MUST **一律**判为高风险」 | 候选人信件恒为高风险 |
| spec「人工确认才放行」 | 「高风险消息 SHALL **仅在携带** `confirmed_by` 时才被放行外发」 | 六条是**风险分级的输入**，`confirmed_by` 是**清关** |
| spec Scenario「人工放行」 | 「该草稿携带确认人标识**重新走门禁** …… 两道闸都通过时**被外发**」 | 同上 |

当前实现取的是 `tasks.md` 的字面读法。按 spec 那半边读，高风险 + `confirmed_by` 应当放行。

**⛔ 为什么本 session 不自行改**：让 `confirmed_by` 能清掉这两条是**放松闸门**，属 `CLAUDE.md` 决策代理表的不可代项（「候选人对外通道的开关：拒信/邀约对外发送」）。2026-08-28 的「一律取最保险一侧」是对当时列出的**五项**而言，不构成改写放行路径的授权。而且这里的「最保险」有陷阱：保持终局拦截确实拦得最多，但代价是**这个变更包立项要建的人工放行能力从未生效**——那不是保守，是功能不存在。这个取舍必须他本人知情后再定。

**三个选项**：

| | 做法 | 后果 |
|---|---|---|
| **(a)** 维持现状（`tasks.md` 字面） | 六条终局拦截，`confirmed_by` 只能清「等待人工确认」 | 拦得最死。U5 的适配器**必须**把候选人信件标成 `requires_confirmation=False` + `severity` 非最高级，否则队列发不出东西——而这与 spec「一律判为高风险」字面冲突，等于把矛盾推给 U5 |
| **(b)** 按 spec 改：`confirmed_by` 非空时可清掉「自称需确认」与「最高级」两条 | 人工放行路径真正可用，与 spec 的三条原文一致。**仍不放松**：未登记类型、标志未知、等级未知、缺标识、收件人未知、总开关六条依旧终局；放行仍需人 + 总开关两道闸 | 需改 `tasks.md` 4.6/4.7 的措辞 |
| **(c)** 折中：只让 `confirmed_by` 清掉「最高级」，「自称需确认」仍终局 | 消息作者的显式意图不可被推翻，但风险等级可由人清关 | 语义最绕，两条同源的规则走两套口径，日后没人记得为什么 |

**判据锁定用例**：`test_confirmed_by_cannot_clear_a_self_declared_or_top_severity_block_pending_d6`（钉住 (a)，改判时红的就是它）。


## 完成判据（`tasks.md` 第 4 章的 checkbox 在这些全部成立后才勾）

1. 五个 Task 全部完成，三次变异验证都如期变红并已撤销
2. 全量测试全绿，且 `app/config.py` / `app/graph/nodes.py` / `requirements.txt` / `pyproject.toml` 的 diff 为空
3. `compute_outbound_gate` 在 `app/outbound/` 之外零调用方
4. ✅ 五项口径已于 2026-08-28 全部拍板（一律取最保险一侧），D-2 已改码并补测，`specs/outbound-approval-gate/spec.md` 的第七条同日补齐、`openspec validate --strict` 通过
5. ✅ 一轮 code review 的 8 条发现：7 条已修（证据保真度与结构守护，**无一是 fail-open**）；⛔ 第 8 条是 **D-6**，属不可代口径，**U5 开工前必须由 Shao Peishen 定**
5. final review 通过后，由**当时持有 `tasks.md` 写锁的那条 session**回勾第 4 章的 9 个 checkbox 并搬运偏离登记——⛔ 本单元自己不改 `tasks.md`
