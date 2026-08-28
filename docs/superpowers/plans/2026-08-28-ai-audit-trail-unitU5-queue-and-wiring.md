# AI 留痕与外发门禁 · 交付单元 U5（待审批队列与图节点接线）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 U4 的门禁纯函数真正插进候选人外发路径——建 `pending_approval` 的读写与状态机、两个新的 `effect_*` 节点（入队 / 留痕）、以及一个**受门禁保护的候选人外发入口**；并证明 M1 现有的内部通知（画像问题、确认卡片）完全不经这道闸。合并时 `CANDIDATE_OUTBOUND_ENABLED` 保持默认关闭（全拦）。

**Architecture:** 判定（U4 的 `compute_outbound_gate`，纯函数）与副作用（本单元的三个 `effect_*`）严格分离。新入口 `deliver_candidate_message()` 只做编排：判一次 → 按 `decision.allowed` 分流到 `effect_enqueue_pending_approval` 或既有 `effect_deliver_message` → 两条路都走 `effect_record_outbound_audit` → 事务提交后由调用点触发镜像 append。⛔ 判定结果**只求值一次**并把 `decision.evidence` 原样带进留痕，绝不在留痕时重新求值（design D4）。

**Tech Stack:** Python 3.14.6（`./venv`）· 标准库 `sqlite3` / `dataclasses` / `hashlib` · pytest 8.3.4 · **不引入任何新依赖**

---

## 开工前置：D-6 已拍板，取口径 B

U4 那条 session 在 `docs/superpowers/plans/2026-08-28-ai-audit-trail-unitU4-outbound-gate-pure-functions.md` 登记了 **D-6** 并写明「U5 开工前必须解决」。**2026-08-28 Shao Peishen 拍板：取 (b)。**

> `confirmed_by` 非空时可清掉「消息自称需确认」与「风险等级为最高级」两条；**其余六条依旧终局拦截**（未登记类型、确认标志未知、风险等级未知、缺 AI 标识、收件人未知、总开关关闭）。放行仍需「人 + 总开关」两道闸。

**这不是在两种合理读法之间挑一个——spec 在这一点上毫不含糊，是 `tasks.md` 4.6/4.7 这份 WBS 写得比 spec 松散。** 逐条对照（`specs/outbound-approval-gate/spec.md`）：

| spec 位置 | 原文 | 结论 |
|---|---|---|
| `Scenario: 确认人放行一条已知的高风险消息`（:89-93） | 「风险等级为**已登记的最高级**、判定所需字段全部读得出，且携带有效的人工确认人标识，外发总开关开启」→「**消息被外发**」 | 最高级 + `confirmed_by` **必须放行**。当前实现拦截，**与这条 Scenario 直接冲突** |
| `Requirement: 人工确认才放行`（:102） | 「确认人标识只能清关**已知的**高风险……判定所需信息本身读不出的**畸形**消息 MUST NOT 因携带确认人标识而被放行」 | spec 早就精确划出了 B 的边界：清关"已知高风险"，不清"畸形" |
| `Scenario: 确认人不能放行一条畸形消息`（:83-87） | 风险等级读不出 + 有 `confirmed_by` → 仍拦截，原因记「风险等级未知」而非「等待人工确认」 | B 的另一半，已在现实现里成立，不用改 |

**为什么 (a) 不是"更保险的那一侧"**：(a) 下队列里的候选人信件永远发不出去，于是 U5 的适配器只能把候选人信件标成 `requires_confirmation=False` + 非最高级才能让流程跑通——那与 spec「这两类 MUST **一律**判为高风险」正面冲突，等于**为了跑通流程而谎报风险等级**。谎报比放行更糟：放行至少留了 `confirmed_by`，谎报连"这是高风险"这个事实都没了。

**✅ 已闭环，本单元不再做（2026-08-28 订正）**：D-6 已由 U4 那条 session 当日落码（`121713f`）并同步 spec（`bcc41a1`），`tasks.md` 4.6/4.7 的措辞由 `[Mac]0828A-账目对齐` 订正、第 4 章已回勾。

⛔ **本单元不改 `app/outbound/gate.py`，也不改 `tests/test_outbound_gate.py`。** 那两个文件里的四条 D-6 锁定用例（`test_confirmed_by_clears_a_known_high_risk_block_per_d6_option_b`、`test_confirmed_by_cannot_clear_a_malformed_message`、`test_a_plain_letter_without_a_confirmer_reports_exactly_the_spec_wording`、`test_a_cleared_high_risk_message_is_still_stopped_by_the_master_switch`）**已经是 (b) 的样子**——翻转它们＝把 D-6 修掉的那个 bug 装回去：`queue.approve()` 带 `confirmed_by` 重走门禁仍被拦，待审批队列里的候选人信件永远发不出去。本单元的门禁是**只读消费方**。

---

## 另一件必须先说清的事：M1 里没有候选人外发路径

**实测**：`grep -rn "rejection_letter|interview_invitation" app/ tests/` 在 `app/outbound/` 之外**零命中**；采集图（`app/graph/build.py:70-91`）只发 `OutboundMessage(type="confirmation_prompt")` 与 `type="question"`，两者都是**发给业务经理的内部通知**，不是候选人信件。

所以 U5 的形状是：

- **建机制，不接业务流。** 拒信/邀约的生成属 M2，本单元不造。U5 交付的是「一条受门禁保护的候选人外发入口 + 待审批队列 + 拦截/放行留痕」，由测试驱动，**生产里暂时没有调用方**——与 U3 的 `audit_context` 同一形状。
- **5.9「内部通知不受影响」因此是本单元最重要的回归**：它证明的不是"我小心地绕开了"，而是"采集图那条路径**结构上就到不了**候选人门禁"。判据必须是结构性的（AST/调用图），不能只跑一遍采集流程看它没报错——后者在"门禁被误插进采集图但恰好放行"时同样是绿的。

---

## Global Constraints

以下条目从 `CLAUDE.md`「工程铁律」「合规红线」、`delivery-units.md` §2.U5 / §3.3 / §3.4、`design.md` D1/D4/D5/D7、`specs/outbound-approval-gate/spec.md`，以及 U1–U4 的落地真值**逐字复制或按 `file:line` 引用**。**每个 Task 的验收隐含包含本节全部内容。**

### 头号约束：判定一次，证据原样带走

> `app/outbound/gate.py:104-112` 的 `GateDecision` docstring 逐字：「`evidence` 是**扁平的、json.dumps 得动的** dict —— U5 直接把它塞进 `DecisionEvent.evidence`，⛔ 不重新求值一遍（tasks 4.2）。重新求值会制造"判定时未知、留痕时又变成已知"的不一致（design D4）。」

**reviewer 机械判据**：`deliver_candidate_message()` 里 `compute_outbound_gate(` 只出现**一次**；留痕节点的 `evidence` 参数来源必须是那次调用返回的 `decision.evidence` 对象本身。Task 5 有一条 AST 守护 + 一条对象同一性断言。

### 第二条：两个新 `effect_*` 沿用既有装饰器，⛔ 不改它、不改 `effect_log`

工程铁律 1。`delivery-units.md` §3.4 与 tasks 5.3 逐字：「沿用现有 `idempotent_effect` 装饰器（**不改装饰器、不改 `effect_log`**）」。

- `effect_enqueue_pending_approval` 的幂等键 = `{thread_id}:effect_enqueue_pending_approval:{content_hash}`，与 U1 的 `(thread_id, content_hash)` 唯一索引**同粒度**（U1 偏离登记 2 就是为这条把单列索引改成两列的）。
- `effect_record_outbound_audit` 的 `business_key` = `{content_hash}:{allowed}`——同一草稿的「拦截」与「放行」各留一条痕，重放不重复留痕（tasks 5.4）。
- 函数体内 ⛔ 不 `commit`，由装饰器统一提交（`app/storage/idempotency.py:70-75`）。

**reviewer 判据**：本单元 diff 里不出现对 `app/storage/idempotency.py` 的任何修改、不出现 `effect_log` 的 DDL/DML、`app/audit/` 与 `app/outbound/gate.py` 之外不出现新的 `conn.commit()`（测试为构造场景显式 commit 除外）。

### 第三条：⛔ 禁止在 `effect_*` 函数体内 append JSONL

`delivery-units.md` §3.4 第 2 条。U2 已落 AST 守护 `tests/test_audit_recorder.py::test_no_effect_function_appends_jsonl`（带三分支阳性对照），U3 合并后它仍绿。

**本单元是这条守护第一次真正有活干的时刻**——U5 是第一个在 `effect_*` 里写留痕的单元。落地形态：`recorder.record(conn, event)` 进 `effect_record_outbound_audit` 的函数体；`recorder.mirror(event)` 由 `deliver_candidate_message()` 在该节点**返回之后**调用（此时装饰器已 `commit`）。

⚠️ **U3 的 `RecorderAuditHook` 不适用于这里**：它持有自己的连接、自己提交，那是给「网关内部、拿不到业务事务」的场景准备的。U5 拿得到 `conn`，就必须用业务事务——这也是 U3 计划里交给下游的第 2 条硬约束：**两条连接不要合并**。

### 第四条：放行复发的死锁防线（平台侧踩过）

`design.md` D5 与 tasks 5.2 逐字：「队列的 `approve()` 持锁期间会带 `confirmed_by` 重走门禁；若此时总开关关闭被拦截，**不能再次入队**——它已经在队列里，重入会撞自己的锁。实现上以"**是否携带 `confirmed_by`**"区分首道拦截与放行复发：只有首道拦截（无 `confirmed_by`）才入队。」

对应 spec `Scenario: 放行时总开关关闭`：草稿 MUST NOT 被重复入队，状态保持「待审批」，可在开关开启后再次放行。

### 第五条：并发与文件边界

- ⛔ **不碰** `app/graph/build.py` 的采集图接线、`app/agents/intake_agent.py`、`app/web/server.py`、`app/config.py`、`app/audit/`（U2/U3 已交付且已回勾）。
- `app/graph/nodes.py` **只追加两个新的 `effect_*` 函数**，⛔ 不改 `effect_deliver_message` / `effect_persist_draft` / `effect_confirm_profile` / `message_business_key` 的任何一行（tasks 5.5 逐字：「不改 `effect_deliver_message` 内部逻辑、不改 `Channel` Protocol」）。
- **Task 1 要改 `app/outbound/gate.py` 与 `tests/test_outbound_gate.py`（U4 的文件）**：开工前先跑 `git log -3 --format='%h %ad %s' --date=format:'%H:%M' -- app/outbound/`，若最近一笔在 30 分钟内，⛔ 停下来确认那条 session 是否还在跑，别两边同写。
- 只 `git add` 本 Task 明确列出的路径，⛔ 禁止 `git add -A` / `git commit -a`。
- `.git/index.lock` 存在 → 等 5 秒重试最多 5 次；仍不行才看孤儿锁三项判据，**判据 3 用 `pgrep -x git`，⛔ 不要用 `-f`**。

### 工程铁律（不可违背，逐字）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。**幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者。
2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。
3. **所有 AI 评分必须持久化**：U5 不产生 AI 评分，本条经由 `effect_record_outbound_audit` 复用同一套留痕机制（spec「外发与拦截动作强制留痕」：留痕 MUST 使用与 AI 评分留痕相同的机制，落入同一份可校验的记录中）。
4. **每条 `criterion_score` 必须有 `evidence_ref`**：U5 不写 `criterion_score`。⚠️ 但 U3 已把 `criterion_key` 白名单的强制点放在 `CriterionScore.__post_init__`（构造期），U5 若要造评分项，`criterion_key` 只能取 `app/audit/criteria.py` 里那七个维度之一。
5. **`temperature=0`；禁止 `latest` 类别名**：U5 不调模型。

### 合规红线

- **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。
  > **U5 就是这条红线的落地节点。** 本单元合并前，`effect_deliver_message` 是**无条件投递**（`app/graph/nodes.py:167-178`，函数体一行 `channel.deliver(...)`），红线全靠调用方自觉。
- **AI 生成的 JD、拒信、邀约须带标识**：门禁的第六条判据（缺 AI 标识即拦截）已在 U4 落地，U5 只负责把拦截结果落库与留痕。
- **⛔ 不提供"一键放行全部"的配置项**（`design.md` 迁移计划回滚策略逐字）：关闭 `CANDIDATE_OUTBOUND_ENABLED` 是"更安全"的方向（全拦）；真要恢复无门禁投递必须显式移除门禁节点。**reviewer 判据**：本单元 diff 里不出现任何"跳过门禁"的开关、环境变量或参数。

### 范围边界（U5 **不做**什么）

| 事项 | 归属 |
|---|---|
| 生成拒信/邀约的内容 | M2。U5 只提供受保护的外发入口 |
| 待审批队列的 Web UI / API 端点 | ⛔ 不做。要改 `app/web/server.py`，超出 `delivery-units.md:26` 给 U5 的文件边界 |
| 审批时效提醒（超过 N 天未审批） | `design.md` Open Questions，明确不改本变更的 spec 与任务拆解 |
| 合规断言与 CI | U6（第 6 章） |
| `confirmed_by` 的可信度（鉴权仍是空壳） | `design.md` D7，U7 的 7.5 登记技术债，U5 不重复登记 |

---

## File Structure

| 文件 | 动作 | 职责 | Task |
|---|---|---|---|
| `app/outbound/messages.py` | **新建** | `CandidateOutboundMessage`：门禁六字段 + `to_outbound_message()`，是唯一能喂进候选人门禁的形状 | 2 |
| `app/outbound/queue.py` | **新建** | `pending_approval` 的读写与状态机；`enqueue` / `list_pending` / `approve` / `abandon` | 2、3 |
| `app/graph/nodes.py` | **追加**两个函数 | `effect_enqueue_pending_approval`、`effect_record_outbound_audit` | 4 |
| `app/outbound/delivery.py` | **新建** | `deliver_candidate_message()`：判一次 → 分流 → 留痕 → 提交后镜像 | 5 |
| `app/outbound/__init__.py` | 修改 | 导出新符号 | 2、5 |
| `tests/test_outbound_queue.py` | **新建** | 状态机、幂等、死锁防线 | 2、3 |
| `tests/test_outbound_effects.py` | **新建** | 两个 effect 的幂等与事务归属 | 4 |
| `tests/test_outbound_delivery.py` | **新建** | 分流、判定只求值一次、证据原样 | 5 |
| `tests/test_outbound_end_to_end.py` | **新建** | 5.6–5.9 的端到端与回归 | 6 |

---

### Task 1: 候选人消息契约与待审批队列的读写（tasks 5.1）

**Files:**
- Create: `app/outbound/messages.py`
- Create: `app/outbound/queue.py`
- Modify: `app/outbound/__init__.py`（导出）
- Create: `tests/test_outbound_queue.py`

**Interfaces:**
- Consumes：U1 的 `pending_approval` 表（`app/storage/db.py:136-171`，列见下）、U4 的 `REGISTERED_MESSAGE_TYPES` / `KNOWN_SEVERITIES` / `MAX_SEVERITY`（`app/outbound/contracts.py`）
- Produces（后续 Task 全部依赖这些确切签名）：
  - `CandidateOutboundMessage`（frozen dataclass，六个门禁字段 + `payload`）
    - `.content_hash() -> str`
    - `.with_confirmation(confirmed_by: str) -> CandidateOutboundMessage`
    - `.to_outbound_message() -> OutboundMessage`
  - `queue.enqueue(conn, *, thread_id: str, message: CandidateOutboundMessage, blocked_reason: str) -> str`（返回 approval id）
  - `queue.list_pending(conn, *, message_type: str | None = None) -> list[dict]`
  - `queue.get(conn, approval_id: str) -> dict | None`
  - `queue.mark_resolved(conn, approval_id: str, *, status: str, confirmed_by: str | None) -> bool`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_outbound_queue.py`：

```python
"""
待审批队列。spec「被拦截草稿的持久化待审批队列」：
- 记录 MUST 含消息内容、类型、收件对象、拦截原因、入队时刻、当前状态
- 状态至少区分「待审批」「已放行」「已放弃」
- 同一草稿重复被拦截 MUST NOT 产生重复队列记录
- 已放行或已放弃的草稿 MUST NOT 再被当作待审批项返回
"""

import pytest

from app.outbound.messages import CandidateOutboundMessage
from app.outbound import queue
from app.storage.db import get_connection, init_schema

AI_BODY = "【AI 生成】本文案由系统基于岗位画像自动生成，生成时间 2026-08-28。很遗憾……"


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "q.db"))
    init_schema(c)
    return c


def _msg(**over):
    payload = {
        "message_type": "rejection_letter",
        "severity": "high",
        "recipient": "cand-9@example.com",
        "body": AI_BODY,
    }
    payload.update(over)
    return CandidateOutboundMessage(**payload)


def test_enqueue_persists_everything_spec_requires(conn):
    approval_id = queue.enqueue(
        conn, thread_id="job-7", message=_msg(), blocked_reason="等待人工确认"
    )
    conn.commit()

    row = queue.get(conn, approval_id)
    assert row["thread_id"] == "job-7"
    assert row["message_type"] == "rejection_letter"
    assert row["recipient"] == "cand-9@example.com"
    assert row["blocked_reason"] == "等待人工确认"
    assert row["status"] == "pending"
    assert row["enqueued_at"]  # 入队时刻
    assert row["resolved_at"] is None
    # 消息内容整份可还原——放行时要拿它重走门禁
    assert row["payload"]["body"] == AI_BODY


def test_the_same_draft_blocked_twice_produces_one_row(conn):
    """spec「同一草稿重复被拦截」：队列中该草稿仍只有一条记录。"""
    first = queue.enqueue(conn, thread_id="job-7", message=_msg(), blocked_reason="等待人工确认")
    second = queue.enqueue(conn, thread_id="job-7", message=_msg(), blocked_reason="等待人工确认")
    conn.commit()

    assert first == second  # 同一条，id 确定性
    assert len(queue.list_pending(conn)) == 1


def test_same_content_in_different_threads_are_two_rows(conn):
    """
    U1 偏离登记 2 的下游判据：唯一索引是 (thread_id, content_hash) 两列。
    两个岗位给同一个候选人发同样的拒信是正常业务，⛔ 不能互相顶掉。
    """
    queue.enqueue(conn, thread_id="job-7", message=_msg(), blocked_reason="等待人工确认")
    queue.enqueue(conn, thread_id="job-8", message=_msg(), blocked_reason="等待人工确认")
    conn.commit()

    assert len(queue.list_pending(conn)) == 2


def test_resolved_drafts_leave_the_pending_list(conn):
    """spec「检索待审批项」：已放行与已放弃的草稿不在结果中。"""
    approved = queue.enqueue(conn, thread_id="job-7", message=_msg(), blocked_reason="等待人工确认")
    abandoned = queue.enqueue(
        conn, thread_id="job-8", message=_msg(), blocked_reason="等待人工确认"
    )
    still_pending = queue.enqueue(
        conn, thread_id="job-9", message=_msg(), blocked_reason="等待人工确认"
    )
    queue.mark_resolved(conn, approved, status="approved", confirmed_by="张三")
    queue.mark_resolved(conn, abandoned, status="abandoned", confirmed_by="李四")
    conn.commit()

    ids = [row["id"] for row in queue.list_pending(conn)]
    assert ids == [still_pending]


def test_resolving_records_who_and_when_and_does_not_delete(conn):
    """
    tasks 5.1 逐字：放行不 DELETE 而是改状态并记 confirmed_by 与 resolved_at。
    删掉就没有"谁在什么时候放的"这条审计事实了。
    """
    approval_id = queue.enqueue(
        conn, thread_id="job-7", message=_msg(), blocked_reason="等待人工确认"
    )
    queue.mark_resolved(conn, approval_id, status="approved", confirmed_by="张三")
    conn.commit()

    row = queue.get(conn, approval_id)
    assert row is not None  # 行还在
    assert row["status"] == "approved"
    assert row["confirmed_by"] == "张三"
    assert row["resolved_at"]


@pytest.mark.parametrize("bad_status", ["done", "sent", "PENDING", ""])
def test_an_unregistered_status_is_rejected_by_the_database(conn, bad_status):
    """
    U1 的 CHECK (status IN ('pending','approved','abandoned')) 是这条的强制点。
    ⛔ 应用层不再写第二份判定——两处判定就会出现"一处放行一处拒绝"的分叉。
    这里断言的是**数据库**拒绝，绕过应用层直接 UPDATE 同样被拒。
    """
    import sqlite3

    approval_id = queue.enqueue(
        conn, thread_id="job-7", message=_msg(), blocked_reason="等待人工确认"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE pending_approval SET status = ? WHERE id = ?", (bad_status, approval_id)
        )


def test_enqueue_does_not_commit(conn):
    """
    工程铁律 1：入队会被包进 effect_enqueue_pending_approval，写入必须与装饰器的
    effect_log 记录落在同一个事务里、由装饰器提交一次。自己 commit 会把两者拆开。
    """
    queue.enqueue(conn, thread_id="job-7", message=_msg(), blocked_reason="等待人工确认")
    conn.rollback()

    assert queue.list_pending(conn) == []


def test_content_hash_ignores_the_confirmation_signature(conn):
    """
    ⭐ 放行路径的地基。`approve()` 会带上 confirmed_by 重走门禁——如果
    content_hash 把 confirmed_by 算进去，那条草稿在幂等键与唯一索引眼里就变成了
    **另一条草稿**：既找不回队列里的原记录，重新被拦时还会插出第二行。

    ⛔ 这条不是"顺手多测一个"，它是 5.2 死锁防线成立的前提。
    """
    plain = _msg()
    signed = plain.with_confirmation("张三")

    assert signed.confirmed_by == "张三"
    assert signed.content_hash() == plain.content_hash()


def test_content_hash_changes_when_the_body_changes():
    """阴性对照：别把 content_hash 写成常量，那样上一条恒真而去重全错。"""
    assert _msg().content_hash() != _msg(body=AI_BODY + "（改了一个字）").content_hash()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
./venv/bin/python -m pytest tests/test_outbound_queue.py -q 2>&1 | tail -4
```

预期：collection error，`ModuleNotFoundError: No module named 'app.outbound.messages'`。

- [ ] **Step 3: 写消息契约**

创建 `app/outbound/messages.py`：

```python
"""
候选人外发消息的具体形状。**这是唯一能喂进候选人门禁的形状**——
`contracts.OutboundGateMessage` 只是说明书（刻意不 runtime_checkable），
本模块给出那份说明书的一个具体实现。

⚠️ 两个默认值是 spec 的直接落地，⛔ 不要为了"让流程跑通"改掉它们：
`specs/outbound-approval-gate` 的「门禁覆盖范围」逐字写着拒信与邀约
「这两类 MUST **一律**判为高风险」。所以 `requires_confirmation` 默认 `True`、
`severity` 默认最高级——候选人信件**天生**需要人签字，这不是保守配置，是红线。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any

from app.channels.base import OutboundMessage
from app.outbound.contracts import MAX_SEVERITY


@dataclass(frozen=True)
class CandidateOutboundMessage:
    """一封待外发的候选人信件。字段名与 `GATE_FIELDS` 逐一对应。"""

    message_type: str
    recipient: str
    body: str
    severity: str = MAX_SEVERITY
    requires_confirmation: bool = True
    confirmed_by: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def content_hash(self) -> str:
        """
        草稿内容的稳定指纹。**⛔ 不含 `confirmed_by`。**

        它同时是三样东西的来源：`pending_approval.content_hash`、
        `(thread_id, content_hash)` 唯一索引、以及两个 effect 的幂等键。
        把签名算进去的话，`approve()` 带签名重走门禁时同一封信会变成"另一封"
        ——找不回队列里的原记录，重新被拦时还会插出第二行，5.2 的死锁防线当场
        失效。签名是**对这封信的处置**，不是这封信的内容。

        `sort_keys=True` 保证同一份内容渲染结果稳定（与
        `app/graph/nodes.py:message_business_key` 同一做法）。
        """
        material = {
            "message_type": self.message_type,
            "recipient": self.recipient,
            "body": self.body,
            "severity": self.severity,
            "requires_confirmation": self.requires_confirmation,
            "payload": self.payload,
        }
        blob = json.dumps(material, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def with_confirmation(self, confirmed_by: str) -> CandidateOutboundMessage:
        """带上确认人标识的同一封信。`content_hash()` 不变，见其 docstring。"""
        return replace(self, confirmed_by=confirmed_by)

    def to_outbound_message(self) -> OutboundMessage:
        """交给 `Channel.deliver` 的形状。⛔ 不改 `Channel` Protocol（tasks 5.5）。"""
        return OutboundMessage(
            type=self.message_type,
            payload={**self.payload, "body": self.body, "recipient": self.recipient},
        )
```

- [ ] **Step 4: 写队列**

创建 `app/outbound/queue.py`：

```python
"""
待审批队列：被门禁拦下的候选人草稿的持久化落点。

⛔ **不复用 `outbox`**（design D5）：outbox 的语义是"已决定要投递的消息"，
本表的语义相反（"尚未获批、可能永远不发"）。合表就要求每个读 outbox 的地方
都加状态过滤，漏一处 = 未审批的拒信被发出去。

⛔ **本模块不自行 `commit`**：写入会被包进 `effect_enqueue_pending_approval`，
必须与装饰器追加的 `effect_log` 行落在同一个事务里（工程铁律 1）。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.outbound.messages import CandidateOutboundMessage

# 队列状态。⛔ 应用层**不**再写一份取值校验：U1 已把它做成数据库 CHECK
# （app/storage/db.py:159-160），两处判定就会出现"一处放行一处拒绝"的分叉。
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_ABANDONED = "abandoned"


def approval_id(thread_id: str, content_hash: str) -> str:
    """
    确定性 id，与 `(thread_id, content_hash)` 唯一索引同粒度（U1 偏离登记 2）。
    确定性让"同一草稿重复被拦截"天然收敛到同一行，而不是靠先查后插。
    """
    return f"{thread_id}:{content_hash}"


def enqueue(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    message: CandidateOutboundMessage,
    blocked_reason: str,
) -> str:
    """
    入队一条被拦下的草稿，返回 approval id。重复入队是 no-op（spec「同一草稿
    重复被拦截」），靠 `ON CONFLICT DO NOTHING` 而不是先查后插——先查后插在
    并发下有窗口，唯一索引没有。

    ⚠️ `confirmed_by` 一栏**入队时恒为 NULL**：带着签名的草稿走的是放行复发
    路径，那条路 ⛔ 不入队（design D5 的死锁防线，见 Task 2）。

    ⚠️ **冲突目标必须显式写成 `(thread_id, content_hash)`，⛔ 不要"简化"成无目标
    的 `ON CONFLICT DO NOTHING`。** 后者会把**任何**约束冲突都吞掉——包括 U1 那条
    `status IN (...)` 的 CHECK——于是一条畸形记录会被静默丢弃而调用方以为入队了。
    2026-08-28 实测：`id` 是确定性主键，重复入队时主键与唯一索引**同时**命中，
    SQLite 按显式目标 DO NOTHING、主键冲突不会漏出来，行数稳定为 1。
    """
    content_hash = message.content_hash()
    row_id = approval_id(thread_id, content_hash)
    conn.execute(
        "INSERT INTO pending_approval "
        "(id, thread_id, message_type, recipient, payload_json, blocked_reason, "
        " content_hash, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(thread_id, content_hash) DO NOTHING",
        (
            row_id,
            thread_id,
            message.message_type,
            message.recipient,
            json.dumps(
                {
                    "message_type": message.message_type,
                    "recipient": message.recipient,
                    "body": message.body,
                    "severity": message.severity,
                    "requires_confirmation": message.requires_confirmation,
                    "payload": message.payload,
                },
                ensure_ascii=False,
            ),
            blocked_reason,
            content_hash,
            STATUS_PENDING,
        ),
    )
    return row_id


def _row_to_dict(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    """⚠️ 刻意不设 `conn.row_factory`：conn 是全应用共享的一条连接
    （`app/storage/db.py:get_connection`），换掉它会让所有按下标取值的既有代码
    静默改变行为（与 `app/audit/sinks.py:_rows_as_dicts` 同一理由）。"""
    columns = [d[0] for d in cursor.description]
    rows = []
    for raw in cursor.fetchall():
        row = dict(zip(columns, raw))
        row["payload"] = json.loads(row.pop("payload_json"))
        rows.append(row)
    return rows


def get(conn: sqlite3.Connection, approval_id_: str) -> dict[str, Any] | None:
    rows = _row_to_dict(
        conn.execute("SELECT * FROM pending_approval WHERE id = ?", (approval_id_,))
    )
    return rows[0] if rows else None


def list_pending(
    conn: sqlite3.Connection, *, message_type: str | None = None
) -> list[dict[str, Any]]:
    """spec「检索待审批项」：只返回 pending，已放行与已放弃不在结果中。"""
    sql = "SELECT * FROM pending_approval WHERE status = ?"
    params: list[Any] = [STATUS_PENDING]
    if message_type is not None:
        sql += " AND message_type = ?"
        params.append(message_type)
    return _row_to_dict(conn.execute(sql + " ORDER BY enqueued_at, id", params))


def mark_resolved(
    conn: sqlite3.Connection,
    approval_id_: str,
    *,
    status: str,
    confirmed_by: str | None,
) -> bool:
    """
    改状态并记下是谁、什么时候。⛔ **不 DELETE**（tasks 5.1）——删掉就没有
    "谁在什么时候放的"这条审计事实了，而这正是本变更包要建的东西。

    ⚠️ `confirmed_by` 现阶段**不可信**：鉴权是空壳（`AuthContext.user_id` 恒为
    `None`），值只能由调用方传入（design D7）。SSO 落地后同一字段变可信，
    结构不改。
    """
    cursor = conn.execute(
        "UPDATE pending_approval SET status = ?, confirmed_by = ?, "
        "resolved_at = datetime('now') WHERE id = ? AND status = ?",
        (status, confirmed_by, approval_id_, STATUS_PENDING),
    )
    return cursor.rowcount == 1


def to_message(row: dict[str, Any]) -> CandidateOutboundMessage:
    """把队列行还原成可重走门禁的消息。放行路径（Task 2）用它。"""
    payload = row["payload"]
    return CandidateOutboundMessage(
        message_type=payload["message_type"],
        recipient=payload["recipient"],
        body=payload["body"],
        severity=payload["severity"],
        requires_confirmation=payload["requires_confirmation"],
        payload=payload.get("payload", {}),
    )
```

- [ ] **Step 5: 导出**

`app/outbound/__init__.py` 追加：

```python
from app.outbound.messages import CandidateOutboundMessage
```

并把 `"CandidateOutboundMessage"` 加进 `__all__`。⛔ 不导出 `queue` 里的函数——它们带副作用，调用点应当显式 `from app.outbound import queue` 让"这里会写库"在 import 行上就看得见。

- [ ] **Step 6: 跑测试确认通过**

```bash
./venv/bin/python -m pytest tests/test_outbound_queue.py -q
```

预期：**12 passed**（8 个非参数化 + `test_an_unregistered_status_is_rejected_by_the_database` 展开的 4 条）。

- [ ] **Step 7: 变异验证——content_hash 把签名算进去会怎样**

```bash
./venv/bin/python - <<'PYEOF'
import pathlib, shutil
p = pathlib.Path("app/outbound/messages.py")
shutil.copy(p, "/tmp/messages.orig.py")   # ⛔ 不用 git checkout：此刻本文件还未提交
src = p.read_text(encoding="utf-8")
old = '            "payload": self.payload,\n        }'
new = '            "payload": self.payload,\n            "confirmed_by": self.confirmed_by,\n        }'
assert old in src, "变异目标没找到，⛔ 停下来查"
p.write_text(src.replace(old, new), encoding="utf-8")
print("mutated: content_hash 把 confirmed_by 算进去了")
PYEOF
./venv/bin/python -m pytest tests/test_outbound_queue.py 2>&1 | grep -E "^FAILED|passed|failed"
cp /tmp/messages.orig.py app/outbound/messages.py && rm /tmp/messages.orig.py
./venv/bin/python -m pytest tests/test_outbound_queue.py -q 2>&1 | tail -2
```

预期：变异后 `test_content_hash_ignores_the_confirmation_signature` **单独变红**，
`test_content_hash_changes_when_the_body_changes` 仍绿（证明两条咬的不是同一件事）；还原后全绿。

- [ ] **Step 8: 提交**

```bash
git add app/outbound/messages.py app/outbound/queue.py app/outbound/__init__.py tests/test_outbound_queue.py
git commit -m "feat(outbound): 候选人消息契约与待审批队列读写（tasks 5.1）"
```

---

### Task 2: `approve()` 与放行复发的死锁防线（tasks 5.2）

**Files:**
- Modify: `app/outbound/queue.py`（追加 `approve`）
- Modify: `tests/test_outbound_queue.py`（追加一组）

**Interfaces:**
- Consumes: Task 1 的 `enqueue` / `get` / `mark_resolved` / `to_message`；U4 的 `compute_outbound_gate(message, outbound_enabled) -> GateDecision`
- Produces: `queue.approve(conn, approval_id, *, confirmed_by, outbound_enabled, deliver) -> GateDecision`
  - `deliver` 是一个 `Callable[[CandidateOutboundMessage], None]`——**投递动作由调用方注入**，队列自己不碰通道（保持 L3/L4 分层，且让 Task 4 的 `effect_deliver_message` 能被原样复用）

- [ ] **Step 1: 写失败的测试**

在 `tests/test_outbound_queue.py` 追加：

```python
# ── 放行与死锁防线（tasks 5.2 / design D5）──────────────────────────────


def _approve(conn, approval_id, *, switch, delivered, confirmed_by="张三"):
    from app.outbound import queue as q

    return q.approve(
        conn,
        approval_id,
        confirmed_by=confirmed_by,
        outbound_enabled=lambda: switch,
        deliver=delivered.append,
    )


def test_approving_with_the_switch_on_delivers_and_marks_approved(conn):
    """spec「人工放行」：草稿携带确认人标识重新走门禁，两道闸都通过时被外发。"""
    approval_id = queue.enqueue(
        conn, thread_id="job-7", message=_msg(), blocked_reason="等待人工确认"
    )
    delivered = []

    decision = _approve(conn, approval_id, switch=True, delivered=delivered)
    conn.commit()

    assert decision.allowed is True
    assert [m.recipient for m in delivered] == ["cand-9@example.com"]
    assert delivered[0].confirmed_by == "张三"  # 投递出去的是带签名的那份
    row = queue.get(conn, approval_id)
    assert row["status"] == "approved"
    assert row["confirmed_by"] == "张三"


def test_top_severity_is_cleared_by_the_signature_not_terminal(conn):
    """
    ⭐ D-6 取 (b) 的下游判据。默认草稿是 severity=high + requires_confirmation=True
    （spec：候选人信件一律高风险），上一条能放行出去，就证明这两条确实是**由人
    清关**而不是终局拦截。若 U4 的门禁被改回 (a)，上一条会红，本条给出可读的理由。
    """
    assert _msg().severity == "high"
    assert _msg().requires_confirmation is True


def test_approving_with_the_switch_off_does_not_deliver_and_does_not_requeue(conn):
    """
    ⭐ 死锁防线（design D5，平台侧踩过）。spec「放行时总开关关闭」：
    消息仍不外发、草稿 MUST NOT 被重复入队、状态保持 pending、
    可在开关开启后再次放行。

    ⚠️ 三样都要断言。只断言"没投递"的话，一个"投递前先重新入队"的实现照样绿，
    而那正是会撞自己唯一索引的写法。
    """
    approval_id = queue.enqueue(
        conn, thread_id="job-7", message=_msg(), blocked_reason="等待人工确认"
    )
    delivered = []

    decision = _approve(conn, approval_id, switch=False, delivered=delivered)
    conn.commit()

    assert decision.allowed is False
    assert decision.reason == "外发总开关关闭"
    assert delivered == []
    assert len(queue.list_pending(conn)) == 1  # 没有重复入队
    assert queue.get(conn, approval_id)["status"] == "pending"  # 状态没动


def test_a_draft_blocked_by_the_switch_can_be_approved_again_later(conn):
    """spec 同一 Scenario 的后半句：可在总开关开启后再次放行。"""
    approval_id = queue.enqueue(
        conn, thread_id="job-7", message=_msg(), blocked_reason="等待人工确认"
    )
    delivered = []

    _approve(conn, approval_id, switch=False, delivered=delivered)
    decision = _approve(conn, approval_id, switch=True, delivered=delivered)
    conn.commit()

    assert decision.allowed is True
    assert len(delivered) == 1
    assert queue.get(conn, approval_id)["status"] == "approved"


def test_a_malformed_draft_is_not_delivered_even_with_a_signature(conn):
    """
    spec「确认人不能放行一条畸形消息」：风险等级读不出但带了确认人标识 →
    仍拦截，原因是「风险等级未知」而非「等待人工确认」。

    ⭐ 这条是 D-6 口径 B 的**边界**：签名清关"已知的高风险"，⛔ 清不了"畸形"。
    没有它，(b) 就退化成"签个字什么都能发"。
    """
    approval_id = queue.enqueue(
        conn, thread_id="job-7", message=_msg(severity="不认识的等级"),
        blocked_reason="风险等级缺失或未登记",
    )
    delivered = []

    decision = _approve(conn, approval_id, switch=True, delivered=delivered)
    conn.commit()

    assert decision.allowed is False
    assert decision.reason == "风险等级缺失或未登记"
    assert delivered == []
    assert queue.get(conn, approval_id)["status"] == "pending"


def test_approving_an_unknown_or_already_resolved_id_raises(conn):
    """
    ⛔ 不静默返回。放行一条不存在或已处置的草稿是调用方的错，静默吞掉会让
    "我明明点了放行"和"它真的发出去了"这两件事再也对不上。
    """
    approval_id = queue.enqueue(
        conn, thread_id="job-7", message=_msg(), blocked_reason="等待人工确认"
    )
    _approve(conn, approval_id, switch=True, delivered=[])
    conn.commit()

    with pytest.raises(queue.ApprovalNotPending):
        _approve(conn, approval_id, switch=True, delivered=[])  # 已经 approved
    with pytest.raises(queue.ApprovalNotPending):
        _approve(conn, "job-7:不存在", switch=True, delivered=[])
```

- [ ] **Step 2: 跑测试确认失败**

```bash
./venv/bin/python -m pytest tests/test_outbound_queue.py -q -k "approv or severity_is_cleared or malformed" 2>&1 | tail -5
```

预期：`AttributeError: module 'app.outbound.queue' has no attribute 'approve'`。

- [ ] **Step 3: 实现 `approve`**

在 `app/outbound/queue.py` 追加（`import` 段加 `from typing import Callable`，并 `from app.outbound.gate import compute_outbound_gate`；`GateDecision` 仅用于类型标注）：

```python
class ApprovalNotPending(LookupError):
    """要放行的 approval 不存在，或已经不是 pending。"""


def approve(
    conn: sqlite3.Connection,
    approval_id_: str,
    *,
    confirmed_by: str,
    outbound_enabled: Callable[[], bool],
    deliver: Callable[[CandidateOutboundMessage], None],
) -> "GateDecision":
    """
    人工放行：把草稿取回来、带上确认人标识**重新走门禁**，两道闸都过才投递。

    ⛔ **放行复发被拦时不重复入队**（design D5 的死锁防线，平台侧踩过）：
    它已经在队列里，重入会撞自己的唯一索引，把"暂时发不出去"变成 IntegrityError。
    判据是「是否携带 `confirmed_by`」——本函数走的永远是携带的那一支，所以这里
    **一行入队代码都没有**，这就是防线本身。⛔ 不要"顺手补一个 upsert 保险"。

    被拦时状态保持 `pending`：总开关开启后可以再次放行（spec 逐字）。

    ⛔ 不自行 `commit`：调用方（`effect_*` 或测试）负责事务边界。
    """
    row = get(conn, approval_id_)
    if row is None or row["status"] != STATUS_PENDING:
        raise ApprovalNotPending(
            f"approval {approval_id_!r} 不存在或已不是 pending"
            f"（当前 {None if row is None else row['status']!r}）"
        )

    signed = to_message(row).with_confirmation(confirmed_by)
    decision = compute_outbound_gate(signed, outbound_enabled)
    if not decision.allowed:
        return decision

    deliver(signed)
    mark_resolved(conn, approval_id_, status=STATUS_APPROVED, confirmed_by=confirmed_by)
    return decision
```

- [ ] **Step 4: 跑测试确认通过**

```bash
./venv/bin/python -m pytest tests/test_outbound_queue.py -q
```

预期：**18 passed**。

- [ ] **Step 5: 结构守护——放行路径里不许有入队**

在 `tests/test_outbound_queue.py` 追加：

```python
def test_the_approve_path_contains_no_enqueue_call():
    """
    ⭐ 死锁防线的机械判据。上面那条行为测试只能证明"当前实现没有重复入队"；
    这条证明"approve 的函数体里**根本没有**入队这个动作"，将来有人为了
    "保险"补一个 upsert 会立刻变红。

    带阳性对照——0 命中同时兼容"约束守住了"和"检查根本没跑"两种解释。
    """
    import ast
    from pathlib import Path

    def enqueue_calls_in(source: str, func_name: str) -> list[str]:
        hits = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name != func_name:
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    name = getattr(inner.func, "id", None) or getattr(inner.func, "attr", None)
                    if name in {"enqueue", "INSERT"}:
                        hits.append(name)
        return hits

    source = (Path(__file__).resolve().parents[1] / "app" / "outbound" / "queue.py").read_text(
        encoding="utf-8"
    )
    assert enqueue_calls_in(source, "approve") == []
    # 阳性对照
    offending = "def approve(conn, i):\n    enqueue(conn, thread_id='t', message=m, blocked_reason='r')\n"
    assert enqueue_calls_in(offending, "approve") == ["enqueue"]
```

- [ ] **Step 6: 跑测试并提交**

```bash
./venv/bin/python -m pytest tests/test_outbound_queue.py -q
git add app/outbound/queue.py tests/test_outbound_queue.py
git commit -m "feat(outbound): queue.approve 带签名重走门禁，放行复发不重复入队（tasks 5.2）"
```

预期：**19 passed**。

---

### Task 3: 两个 `effect_*` 节点——入队与外发留痕（tasks 5.3 / 5.4）

**Files:**
- Modify: `app/graph/nodes.py`（**只追加两个函数**，⛔ 不改任何既有行）
- Create: `tests/test_outbound_effects.py`

**Interfaces:**
- Consumes: Task 1 的 `queue.enqueue`；U2 的 `AuditRecorder.record(conn, event) -> bool` / `DecisionEvent` / `OUTBOUND_BLOCKED` / `OUTBOUND_DELIVERED`；既有 `idempotent_effect`（`app/storage/idempotency.py:19`）
- Produces：
  - `effect_enqueue_pending_approval(conn, *, thread_id, business_key, message, blocked_reason) -> str | None`
  - `effect_record_outbound_audit(conn, *, thread_id, business_key, recorder, event) -> bool | None`
  （两者被装饰器包过，命中重放时返回 `None`）

> ⚠️ **本 Task 有一个最容易写错的地方，先说在前面。** `AuditRecorder.record()` 对
> `outbound_blocked` / `outbound_delivered` 事件**返回 `False`**——`SqliteSink.SUPPORTED_EVENT_TYPES`
> 只有 `{AI_ANALYSIS}`（`app/audit/sinks.py:84`），外发事件的 SQLite 真身是
> `pending_approval`，不在 `analysis_run` 里。
>
> **所以外发留痕的镜像 append ⛔ 绝不能因为 `False` 就跳过**——那条 JSONL 行是
> 外发事件**唯一的**留痕。这与 U3 的 `RecorderAuditHook` 里 `False` → 跳过镜像
> 的处理**正好相反**，两处的 `False` 含义不同。
>
> 这正是 2026-08-28 对残留 B 的拍板要求的做法：**调用点按自己写的 `event_type`
> 决定行为，⛔ 不从 `False` 反推原因**（`tasks.md`「2.x 落地偏离登记」）。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_outbound_effects.py`：

```python
"""
两个新的 effect_* 节点。工程铁律 1：幂等记录与业务写必须落在同一个事务里，
由 idempotent_effect 装饰器统一提交一次。
"""

import json

import pytest

from app.audit.events import OUTBOUND_BLOCKED, OUTBOUND_DELIVERED, DecisionEvent
from app.audit.recorder import AuditRecorder
from app.audit.sinks import JsonlChainSink, SqliteSink
from app.graph.nodes import effect_enqueue_pending_approval, effect_record_outbound_audit
from app.outbound import queue
from app.outbound.messages import CandidateOutboundMessage
from app.storage.db import get_connection, init_schema

AI_BODY = "【AI 生成】本文案由系统基于岗位画像自动生成，生成时间 2026-08-28。很遗憾……"


@pytest.fixture
def conn(tmp_path):
    c = get_connection(str(tmp_path / "e.db"))
    init_schema(c)
    return c


@pytest.fixture
def chain_path(tmp_path):
    return tmp_path / "decisions.jsonl"


@pytest.fixture
def recorder(conn, chain_path):
    return AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))


def _msg(**over):
    payload = {
        "message_type": "rejection_letter",
        "recipient": "cand-9@example.com",
        "body": AI_BODY,
    }
    payload.update(over)
    return CandidateOutboundMessage(**payload)


def _blocked_event(message, reason="等待人工确认"):
    return DecisionEvent(
        id=f"job-7:effect_record_outbound_audit:{message.content_hash()}:False",
        event_type=OUTBOUND_BLOCKED,
        thread_id="job-7",
        message_type=message.message_type,
        recipient=message.recipient,
        content_hash=message.content_hash(),
        blocked_reason=reason,
        evidence={"severity": "high"},
    )


def _effect_log_count(conn, node_name):
    return conn.execute(
        "SELECT count(*) FROM effect_log WHERE node_name = ?", (node_name,)
    ).fetchone()[0]


# ── 入队节点 ─────────────────────────────────────────────────────────────


def test_enqueue_effect_writes_the_row_and_its_effect_log_together(conn):
    """
    ⭐ 工程铁律 1 的不变式：每个 effect_* 节点的 effect_log 条数与其业务表行数
    按 thread 恒等。2026-08-10 / 08-12 现网各丢一轮 outbox 就是这条被破坏的形状
    （docs/findings/2026-08-13-sqlite-事务归属冲突.md §8.5）。
    """
    message = _msg()
    effect_enqueue_pending_approval(
        conn,
        thread_id="job-7",
        business_key=message.content_hash(),
        message=message,
        blocked_reason="等待人工确认",
    )

    assert len(queue.list_pending(conn)) == 1
    assert _effect_log_count(conn, "effect_enqueue_pending_approval") == 1


def test_enqueue_effect_is_idempotent_on_replay(conn):
    """
    tasks 5.8：外发相关节点被从头重跑 → 已入队不重复入队（effect_log 命中短路）。
    幂等键 {thread_id}:effect_enqueue_pending_approval:{content_hash}，与 U1 的
    (thread_id, content_hash) 唯一索引同粒度——两道防线，粒度必须一致。
    """
    message = _msg()
    for _ in range(3):
        effect_enqueue_pending_approval(
            conn,
            thread_id="job-7",
            business_key=message.content_hash(),
            message=message,
            blocked_reason="等待人工确认",
        )

    assert len(queue.list_pending(conn)) == 1
    assert _effect_log_count(conn, "effect_enqueue_pending_approval") == 1


def test_enqueue_effect_commits_exactly_once(conn, monkeypatch):
    """
    ⛔ 函数体内不 commit（tasks 5.3 逐字）。装饰器提交一次，函数体自己再提交一次
    就把业务写与 effect_log 拆成了两个事务——正是铁律 1 禁止的形状。
    """
    commits = []
    original = conn.commit
    monkeypatch.setattr(conn, "commit", lambda: (commits.append(1), original())[1])

    message = _msg()
    effect_enqueue_pending_approval(
        conn,
        thread_id="job-7",
        business_key=message.content_hash(),
        message=message,
        blocked_reason="等待人工确认",
    )

    assert len(commits) == 1


# ── 留痕节点 ─────────────────────────────────────────────────────────────


def test_outbound_audit_writes_no_analysis_run_row_and_says_so(conn, recorder):
    """
    ⭐ 外发事件在 analysis_run 里**没有真身**——它的真身是 pending_approval。
    AuditRecorder.record() 因此返回 False，⛔ 调用方不得把它当成"写失败"。
    """
    message = _msg()
    stored = effect_record_outbound_audit(
        conn,
        thread_id="job-7",
        business_key=f"{message.content_hash()}:False",
        recorder=recorder,
        event=_blocked_event(message),
    )

    assert stored is False
    assert conn.execute("SELECT count(*) FROM analysis_run").fetchone()[0] == 0
    assert _effect_log_count(conn, "effect_record_outbound_audit") == 1


def test_block_and_release_of_one_draft_each_leave_their_own_trail(conn, recorder):
    """
    tasks 5.4 逐字：business_key = {content_hash}:{allowed}——同一草稿的"拦截"与
    "放行"各留一条痕、重放不重复留痕。

    ⚠️ 若 business_key 只用 content_hash，放行那条会命中拦截那条的 effect_log 而
    被短路，于是**投递发生了却没有留痕**——spec「外发与拦截动作强制留痕」当场破。
    """
    message = _msg()
    for allowed in (False, True):
        for _ in range(2):  # 各跑两遍，验重放
            effect_record_outbound_audit(
                conn,
                thread_id="job-7",
                business_key=f"{message.content_hash()}:{allowed}",
                recorder=recorder,
                event=_blocked_event(message)
                if not allowed
                else DecisionEvent(
                    id=f"job-7:effect_record_outbound_audit:{message.content_hash()}:True",
                    event_type=OUTBOUND_DELIVERED,
                    thread_id="job-7",
                    message_type=message.message_type,
                    content_hash=message.content_hash(),
                    confirmed_by="张三",
                ),
            )

    assert _effect_log_count(conn, "effect_record_outbound_audit") == 2


def test_the_mirror_line_is_written_even_though_sqlite_stored_nothing(
    conn, recorder, chain_path
):
    """
    ⭐⭐ 本 Task 最容易写错的一条。AuditRecorder.record() 对外发事件返回 False，
    但那**不是**"已经写过"——外发事件在这个 sink 里根本没有真身。镜像里那一行是
    外发留痕**唯一的**载体，⛔ 绝不能因为 False 就跳过 append。

    U3 的 RecorderAuditHook 里 False → 跳过镜像，那是因为它只造 ai_analysis 事件、
    False 只可能是"已写过"。两处的 False 含义相反——这正是 2026-08-28 对残留 B
    的拍板要求的：调用点按自己写的 event_type 决定行为，⛔ 不从 False 反推原因。
    """
    message = _msg()
    effect_record_outbound_audit(
        conn,
        thread_id="job-7",
        business_key=f"{message.content_hash()}:False",
        recorder=recorder,
        event=_blocked_event(message),
    )
    conn.commit()
    recorder.mirror(_blocked_event(message))  # 调用点在事务提交后触发（见 Task 4）

    lines = chain_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    mirrored = json.loads(lines[0])
    assert mirrored["event_type"] == OUTBOUND_BLOCKED
    assert mirrored["blocked_reason"] == "等待人工确认"
    assert mirrored["evidence"] == {"severity": "high"}
```

- [ ] **Step 2: 跑测试确认失败**

```bash
./venv/bin/python -m pytest tests/test_outbound_effects.py -q 2>&1 | tail -4
```

预期：`ImportError: cannot import name 'effect_enqueue_pending_approval' from 'app.graph.nodes'`。

- [ ] **Step 3: 追加两个节点**

在 `app/graph/nodes.py` **文件末尾追加**（⛔ 不改动上面任何一行；import 段追加
`from app.audit.events import DecisionEvent`、`from app.audit.recorder import AuditRecorder`、
`from app.outbound import queue`、`from app.outbound.messages import CandidateOutboundMessage`）：

```python
@idempotent_effect("effect_enqueue_pending_approval")
def effect_enqueue_pending_approval(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    message: CandidateOutboundMessage,
    blocked_reason: str,
) -> str:
    """
    effect_* 节点：把一条被门禁拦下的候选人草稿写进待审批队列，独占、幂等。

    `business_key` = 草稿内容哈希，于是幂等键是
    `{thread_id}:effect_enqueue_pending_approval:{content_hash}`——与 U1 的
    `(thread_id, content_hash)` 唯一索引**同粒度**（U1 偏离登记 2 就是为了这条
    才把单列索引改成两列的）。两道防线粒度不一致时，宽的那道形同虚设。

    不在这里 `conn.commit()` —— 理由同 `effect_persist_draft`：写入必须与
    `effect_log` 记录由 `idempotent_effect` 装饰器在同一个事务里一次性提交。

    ⛔ 本函数体内不 append JSONL（delivery-units.md §3.4 第 2 条）。留痕是
    `effect_record_outbound_audit` 的事，镜像 append 更是在事务提交之后才发生。
    """
    return queue.enqueue(
        conn, thread_id=thread_id, message=message, blocked_reason=blocked_reason
    )


@idempotent_effect("effect_record_outbound_audit")
def effect_record_outbound_audit(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    recorder: AuditRecorder,
    event: DecisionEvent,
) -> bool:
    """
    effect_* 节点：外发/拦截动作的留痕，独占、幂等。

    `business_key` = `{content_hash}:{allowed}`（tasks 5.4）——同一草稿的"拦截"
    与"放行"各留一条痕。⛔ 只用 content_hash 的话，放行那条会命中拦截那条的
    `effect_log` 被短路，于是**投递发生了却没有留痕**。

    返回值是 `recorder.record()` 的返回值，对外发事件**恒为 `False`**：外发事件
    在 `analysis_run` 里没有真身（它的真身是 `pending_approval`），
    `SqliteSink.SUPPORTED_EVENT_TYPES` 只收 `ai_analysis`。
    ⛔ **调用方不得把这个 `False` 当成"写失败"，更不得据此跳过镜像 append**——
    镜像里那一行是外发留痕唯一的载体。调用点按自己写的 `event_type` 决定行为，
    不从 `False` 反推原因（2026-08-28 对残留 B 的拍板）。

    ⛔ 函数体内不 append JSONL：`recorder.mirror()` 由调用点在本节点**返回之后**
    触发，那时装饰器已 `commit`（delivery-units.md §3.4 第 3 条）。
    """
    return recorder.record(conn, event)
```

- [ ] **Step 4: 跑测试确认通过**

```bash
./venv/bin/python -m pytest tests/test_outbound_effects.py -q
```

预期：**6 passed**。

- [ ] **Step 5: 确认 U2 的 AST 守护仍绿——这次它是真有活干**

```bash
./venv/bin/python -m pytest tests/test_audit_recorder.py -k "no_effect_function" -v 2>&1 | tail -3
```

预期：**PASSED**。⚠️ **U5 是第一个在 `effect_*` 里写留痕的单元，这条守护到今天为止一直是"恒真"的，从本 Task 起它才第一次真正有活干。** 若它变红，说明 `mirror(` / `backfill(` / `JsonlChainSink` 出现在了某个 `effect_*` 的函数体里——⛔ 不要改守护，改代码。

- [ ] **Step 6: 确认既有节点一行未动**

```bash
git diff app/graph/nodes.py | grep '^-' | grep -v '^---'
```

预期：**无输出**（纯追加，没有删除行）。有输出就是改到了既有节点，⛔ 回滚那部分。

- [ ] **Step 7: 提交**

```bash
git add app/graph/nodes.py tests/test_outbound_effects.py
git commit -m "feat(graph): 入队与外发留痕两个 effect 节点，幂等键各自到位（tasks 5.3/5.4）"
```

---

### Task 4: 受门禁保护的外发入口（tasks 5.5）

**Files:**
- Create: `app/outbound/delivery.py`
- Modify: `app/outbound/__init__.py`
- Create: `tests/test_outbound_delivery.py`

**Interfaces:**
- Consumes: Task 1–3 的全部产出；既有 `effect_deliver_message(conn, *, thread_id, business_key, channel, message)`（`app/graph/nodes.py:167`）
- Produces: `deliver_candidate_message(conn, *, thread_id, message, channel, recorder, outbound_enabled) -> GateDecision`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_outbound_delivery.py`：

```python
"""
候选人外发入口。判定一次 → 分流 → 留痕 → 提交后镜像。

⚠️ 本模块是**唯一**允许把候选人信件交给通道的地方。⛔ 不提供任何"跳过门禁"的
参数或开关（design.md 迁移计划回滚策略：真要恢复无门禁投递必须显式移除门禁节点）。
"""

import ast
import json
from pathlib import Path

import pytest

from app.audit.events import OUTBOUND_BLOCKED, OUTBOUND_DELIVERED
from app.audit.recorder import AuditRecorder
from app.audit.sinks import JsonlChainSink, SqliteSink
from app.outbound import queue
from app.outbound.delivery import deliver_candidate_message
from app.outbound.messages import CandidateOutboundMessage
from app.storage.db import get_connection, init_schema

AI_BODY = "【AI 生成】本文案由系统基于岗位画像自动生成，生成时间 2026-08-28。很遗憾……"


class SpyChannel:
    def __init__(self):
        self.delivered = []

    def deliver(self, thread_id, message):
        self.delivered.append((thread_id, message))

    def latest(self, thread_id):
        return None


@pytest.fixture
def wired(tmp_path):
    conn = get_connection(str(tmp_path / "d.db"))
    init_schema(conn)
    chain_path = tmp_path / "decisions.jsonl"
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))
    return conn, chain_path, recorder, SpyChannel()


def _msg(**over):
    payload = {
        "message_type": "rejection_letter",
        "recipient": "cand-9@example.com",
        "body": AI_BODY,
    }
    payload.update(over)
    return CandidateOutboundMessage(**payload)


def _mirror_lines(chain_path):
    text = chain_path.read_text(encoding="utf-8").strip()
    return [json.loads(line) for line in text.splitlines()] if text else []


def test_a_draft_without_a_signature_is_queued_not_delivered(wired):
    """spec「未带确认人的高风险消息」：拦截、入队、留痕原因为「等待人工确认」。"""
    conn, chain_path, recorder, channel = wired

    decision = deliver_candidate_message(
        conn,
        thread_id="job-7",
        message=_msg(),
        channel=channel,
        recorder=recorder,
        outbound_enabled=lambda: True,
    )

    assert decision.allowed is False
    assert channel.delivered == []
    pending = queue.list_pending(conn)
    assert len(pending) == 1
    assert pending[0]["blocked_reason"] == decision.reason
    assert _mirror_lines(chain_path)[0]["event_type"] == OUTBOUND_BLOCKED


def test_a_signed_draft_with_the_switch_on_is_delivered(wired):
    """spec「两道闸都通过」：消息被外发；留痕动作类型为已发送且含 confirmed_by。"""
    conn, chain_path, recorder, channel = wired

    decision = deliver_candidate_message(
        conn,
        thread_id="job-7",
        message=_msg().with_confirmation("张三"),
        channel=channel,
        recorder=recorder,
        outbound_enabled=lambda: True,
    )

    assert decision.allowed is True
    assert len(channel.delivered) == 1
    assert queue.list_pending(conn) == []  # 直接放行的不进队列
    line = _mirror_lines(chain_path)[0]
    assert line["event_type"] == OUTBOUND_DELIVERED
    assert line["confirmed_by"] == "张三"


def test_a_signed_draft_with_the_switch_off_is_blocked_and_queued(wired):
    """
    spec「总开关关闭时已确认的消息」：拦截，原因记「外发总开关关闭」，
    **与「等待人工确认」区分开**——U6 的 6.5 靠这个分布做判断。
    """
    conn, chain_path, recorder, channel = wired

    decision = deliver_candidate_message(
        conn,
        thread_id="job-7",
        message=_msg().with_confirmation("张三"),
        channel=channel,
        recorder=recorder,
        outbound_enabled=lambda: False,
    )

    assert decision.allowed is False
    assert decision.reason == "外发总开关关闭"
    assert channel.delivered == []


def test_the_gate_is_evaluated_exactly_once_and_its_evidence_is_carried_verbatim(wired):
    """
    ⭐ design D4 / GateDecision docstring 逐字：evidence 直接塞进
    DecisionEvent.evidence，⛔ 不重新求值一遍——重新求值会制造"判定时未知、
    留痕时又变成已知"的不一致。

    断言的是**对象同一性**：相等允许中途拷一份再改几个键，同一性不允许。
    顺带用一个只肯被求值一次的开关钉住"判定只发生一次"。
    """
    conn, chain_path, recorder, channel = wired
    calls = []

    def switch_once():
        calls.append(1)
        return True

    decision = deliver_candidate_message(
        conn,
        thread_id="job-7",
        message=_msg().with_confirmation("张三"),
        channel=channel,
        recorder=recorder,
        outbound_enabled=switch_once,
    )

    assert len(calls) == 1  # 门禁只判了一次
    line = _mirror_lines(chain_path)[0]
    assert line["evidence"] == decision.evidence
    assert line["evidence"] is not None and line["evidence"] != {}


def test_delivery_module_calls_the_gate_exactly_once():
    """
    ⭐ 上一条的结构版阳性对照。行为测试只能证明"当前实现判了一次"；这条证明
    源码里 compute_outbound_gate 只出现一次，将来有人"顺手在留痕前再判一次"
    会立刻变红。
    """

    def gate_calls_in(source: str) -> int:
        return sum(
            1
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
            and (getattr(node.func, "id", None) or getattr(node.func, "attr", None))
            == "compute_outbound_gate"
        )

    source = (Path(__file__).resolve().parents[1] / "app" / "outbound" / "delivery.py").read_text(
        encoding="utf-8"
    )
    assert gate_calls_in(source) == 1
    # 阳性对照
    assert gate_calls_in("a = compute_outbound_gate(m, s)\nb = compute_outbound_gate(m, s)\n") == 2


def test_no_bypass_parameter_exists():
    """
    ⛔ design.md 迁移计划回滚策略逐字：**不提供"一键放行全部"的配置项**，
    避免它成为红线的旁路。这条扫入口函数的参数名。
    """
    source = (Path(__file__).resolve().parents[1] / "app" / "outbound" / "delivery.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "deliver_candidate_message":
            names = [a.arg for a in node.args.args + node.args.kwonlyargs]
            assert not any(
                bad in name.lower()
                for name in names
                for bad in ("bypass", "skip_gate", "force", "no_gate")
            ), names
            return
    raise AssertionError("没找到 deliver_candidate_message")
```

- [ ] **Step 2: 跑测试确认失败**

```bash
./venv/bin/python -m pytest tests/test_outbound_delivery.py -q 2>&1 | tail -4
```

预期：`ModuleNotFoundError: No module named 'app.outbound.delivery'`。

- [ ] **Step 3: 写入口**

创建 `app/outbound/delivery.py`：

```python
"""
候选人外发的唯一入口。**编排，不判定、不写库**——判定在 `gate.py`（纯函数），
写库在 `app/graph/nodes.py` 的三个 `effect_*` 节点。

⛔ 本模块不提供任何"跳过门禁"的参数、开关或环境变量（design.md 迁移计划回滚
策略：关闭 `CANDIDATE_OUTBOUND_ENABLED` 是更安全的方向；真要恢复无门禁投递必须
显式移除门禁节点）。守护见 `tests/test_outbound_delivery.py::test_no_bypass_parameter_exists`。
"""

from __future__ import annotations

import sqlite3
from typing import Any, Callable

from app.audit.events import OUTBOUND_BLOCKED, OUTBOUND_DELIVERED, DecisionEvent
from app.audit.recorder import AuditRecorder
from app.graph.nodes import (
    effect_deliver_message,
    effect_enqueue_pending_approval,
    effect_record_outbound_audit,
)
from app.outbound.gate import GateDecision, compute_outbound_gate
from app.outbound.messages import CandidateOutboundMessage


def _audit_event(
    thread_id: str, message: CandidateOutboundMessage, decision: GateDecision
) -> DecisionEvent:
    """
    把判定结果折成留痕事件。**`evidence` 原样带走**（design D4）：⛔ 这里不重新
    读消息的任何属性，那会制造"判定时未知、留痕时又变成已知"的不一致。
    """
    content_hash = message.content_hash()
    return DecisionEvent(
        id=f"{thread_id}:effect_record_outbound_audit:{content_hash}:{decision.allowed}",
        event_type=OUTBOUND_DELIVERED if decision.allowed else OUTBOUND_BLOCKED,
        thread_id=thread_id,
        message_type=message.message_type,
        recipient=message.recipient,
        content_hash=content_hash,
        confirmed_by=message.confirmed_by,
        blocked_reason=decision.reason,
        evidence=decision.evidence,
        error=decision.error,
    )


def deliver_candidate_message(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    message: CandidateOutboundMessage,
    channel: Any,
    recorder: AuditRecorder,
    outbound_enabled: Callable[[], bool],
) -> GateDecision:
    """
    受门禁保护的候选人外发。返回门禁判定，调用方据 `.allowed` 得知结果。

    顺序是刻意的：**判一次 → 分流 → 留痕（都在事务里）→ 提交后镜像**。
    """
    decision = compute_outbound_gate(message, outbound_enabled)
    content_hash = message.content_hash()

    if decision.allowed:
        effect_deliver_message(
            conn,
            thread_id=thread_id,
            business_key=content_hash,
            channel=channel,
            message=message.to_outbound_message(),
        )
    elif not (message.confirmed_by or "").strip():
        # ⛔ 只有**首道拦截**（没带签名）才入队。放行复发被拦时它已经在队列里，
        # 重入会撞自己的唯一索引，把"暂时发不出去"变成 IntegrityError
        # （design D5 的死锁防线，平台侧踩过）。判据就是"是否携带 confirmed_by"。
        effect_enqueue_pending_approval(
            conn,
            thread_id=thread_id,
            business_key=content_hash,
            message=message,
            blocked_reason=decision.reason or "",
        )

    event = _audit_event(thread_id, message, decision)
    effect_record_outbound_audit(
        conn,
        thread_id=thread_id,
        business_key=f"{content_hash}:{decision.allowed}",
        recorder=recorder,
        event=event,
    )

    # 第二段：镜像。**在这里而不是在 effect_* 函数体内**——此时装饰器已 commit
    # （delivery-units.md §3.4 第 3 条：允许的偏差只有单向「SQLite 有、JSONL 缺行」）。
    #
    # ⚠️ ⛔ 不要因为上面 effect_record_outbound_audit 返回 False 就跳过这一步：
    # 外发事件在 analysis_run 里本来就没有真身（真身是 pending_approval），
    # 镜像里这一行是它**唯一的**留痕。调用点按自己写的 event_type 决定行为，
    # 不从 False 反推原因（2026-08-28 对残留 B 的拍板）。
    try:
        recorder.mirror(event)
    except Exception:  # noqa: BLE001 —— 镜像失败⛔ 不抛，理由同 app/audit/hook.py
        import logging

        logging.getLogger(__name__).error(
            "外发留痕镜像 append 失败（id=%s）。这是被允许的单向偏差，"
            "由对账检出、链尾补录；⛔ 不要改成抛异常。",
            event.id,
            exc_info=True,
        )

    return decision
```

- [ ] **Step 4: 导出并跑测试**

`app/outbound/__init__.py` 追加 `from app.outbound.delivery import deliver_candidate_message`，
并加进 `__all__`。

```bash
./venv/bin/python -m pytest tests/test_outbound_delivery.py -q
```

预期：**6 passed**。

- [ ] **Step 5: 变异验证——去掉「只有首道拦截才入队」会怎样**

```bash
./venv/bin/python - <<'PYEOF'
import pathlib, shutil
p = pathlib.Path("app/outbound/delivery.py")
shutil.copy(p, "/tmp/delivery.orig.py")
src = p.read_text(encoding="utf-8")
old = '    elif not (message.confirmed_by or "").strip():'
assert old in src, "变异目标没找到，⛔ 停下来查"
p.write_text(src.replace(old, "    else:"), encoding="utf-8")
print("mutated: 放行复发被拦时也会入队")
PYEOF
./venv/bin/python -m pytest tests/test_outbound_delivery.py tests/test_outbound_queue.py 2>&1 | grep -E "^FAILED|passed|failed"
cp /tmp/delivery.orig.py app/outbound/delivery.py && rm /tmp/delivery.orig.py
./venv/bin/python -m pytest tests/test_outbound_delivery.py tests/test_outbound_queue.py -q 2>&1 | tail -2
```

预期：变异后 `test_a_signed_draft_with_the_switch_off_is_blocked_and_queued` 变红
（它会多出一条队列记录）；还原后全绿。
⚠️ 若变异后**全绿**，说明死锁防线没有被任何测试咬住，⛔ 停下来补测再继续。

- [ ] **Step 6: 提交**

```bash
git add app/outbound/delivery.py app/outbound/__init__.py tests/test_outbound_delivery.py
git commit -m "feat(outbound): 受门禁保护的候选人外发入口，判定一次证据原样带走（tasks 5.5）"
```

---

### Task 5: 端到端、重放安全与内部通知回归（tasks 5.6 / 5.7 / 5.8 / 5.9）

**Files:**
- Create: `tests/test_outbound_end_to_end.py`
- Modify: `docs/tech-debt.md`（登记"无生产调用方"）

**Interfaces:** Consumes Task 1–4 的全部产出。本 Task **不新增生产代码**。

- [ ] **Step 1: 写端到端与回归测试**

创建 `tests/test_outbound_end_to_end.py`：

```python
"""
tasks 5.6–5.9。拦截 → 入队 → 放行 → 投递的完整一圈，外加两条守护：
重放不重复副作用，以及 M1 的内部通知**结构上到不了**候选人门禁。
"""

import ast
import json
from pathlib import Path

import pytest

from app.audit.events import OUTBOUND_BLOCKED, OUTBOUND_DELIVERED
from app.audit.recorder import AuditRecorder
from app.audit.sinks import JsonlChainSink, SqliteSink
from app.outbound import queue
from app.outbound.delivery import deliver_candidate_message
from app.outbound.messages import CandidateOutboundMessage
from app.storage.db import get_connection, init_schema

REPO_ROOT = Path(__file__).resolve().parents[1]
AI_BODY = "【AI 生成】本文案由系统基于岗位画像自动生成，生成时间 2026-08-28。很遗憾……"


class SpyChannel:
    def __init__(self):
        self.delivered = []

    def deliver(self, thread_id, message):
        self.delivered.append((thread_id, message))

    def latest(self, thread_id):
        return None


@pytest.fixture
def wired(tmp_path):
    conn = get_connection(str(tmp_path / "e2e.db"))
    init_schema(conn)
    chain_path = tmp_path / "decisions.jsonl"
    recorder = AuditRecorder(SqliteSink(conn), JsonlChainSink(chain_path))
    return conn, chain_path, recorder, SpyChannel()


def _msg():
    return CandidateOutboundMessage(
        message_type="rejection_letter", recipient="cand-9@example.com", body=AI_BODY
    )


def _mirror(chain_path):
    text = chain_path.read_text(encoding="utf-8").strip()
    return [json.loads(line) for line in text.splitlines()] if text else []


def test_blocked_letter_is_queued_with_reason_and_evidence(wired):
    """
    tasks 5.6：一封无 confirmed_by 的拒信 → 未投递、入队为 pending、
    留痕含拦截原因**与判定字段原始取值**。

    ⚠️ evidence 那半句是重点：只断言"有留痕"的话，一条 evidence 全空的留痕
    照样绿，而 U6 的 6.5 正是靠 evidence 才能 group 出"哪一类消息一直在被拦"。
    """
    conn, chain_path, recorder, channel = wired

    decision = deliver_candidate_message(
        conn, thread_id="job-7", message=_msg(), channel=channel,
        recorder=recorder, outbound_enabled=lambda: True,
    )

    assert channel.delivered == []
    row = queue.list_pending(conn)[0]
    assert row["status"] == "pending"
    assert row["blocked_reason"] == decision.reason
    line = _mirror(chain_path)[0]
    assert line["event_type"] == OUTBOUND_BLOCKED
    assert line["blocked_reason"] == decision.reason
    assert line["evidence"]["message_type"] == "rejection_letter"
    assert line["evidence"]["severity"] == "high"


def test_approving_the_queued_letter_delivers_it_and_leaves_a_second_trail(wired):
    """
    tasks 5.7：队列 approve + 总开关开启 → 投递发生、队列转 approved、
    留痕动作类型为「已发送」且含 confirmed_by。

    ⭐ 这条在 D-6 口径 (a) 下**不可能通过**——那是它存在的意义。
    """
    conn, chain_path, recorder, channel = wired
    deliver_candidate_message(
        conn, thread_id="job-7", message=_msg(), channel=channel,
        recorder=recorder, outbound_enabled=lambda: True,
    )
    approval_id = queue.list_pending(conn)[0]["id"]

    decision = queue.approve(
        conn, approval_id, confirmed_by="张三", outbound_enabled=lambda: True,
        deliver=lambda m: deliver_candidate_message(
            conn, thread_id="job-7", message=m, channel=channel,
            recorder=recorder, outbound_enabled=lambda: True,
        ),
    )
    conn.commit()

    assert decision.allowed is True
    assert len(channel.delivered) == 1
    assert queue.get(conn, approval_id)["status"] == "approved"
    assert queue.list_pending(conn) == []
    delivered_line = [l for l in _mirror(chain_path) if l["event_type"] == OUTBOUND_DELIVERED]
    assert len(delivered_line) == 1
    assert delivered_line[0]["confirmed_by"] == "张三"


def test_replaying_the_whole_flow_repeats_no_side_effect(wired):
    """
    tasks 5.8：外发相关节点被从头重跑 → 已外发不重复外发、已入队不重复入队
    （effect_log 命中短路）。LangGraph 恢复时节点从头整个重跑，这是铁律 1 的前提。

    ⚠️ 三样都断言：投递次数、队列行数、effect_log 条数。只看投递次数的话，
    一个"重复入队但没重复投递"的实现也是绿的。
    """
    conn, chain_path, recorder, channel = wired
    signed = _msg().with_confirmation("张三")

    for _ in range(3):
        deliver_candidate_message(
            conn, thread_id="job-7", message=signed, channel=channel,
            recorder=recorder, outbound_enabled=lambda: True,
        )

    assert len(channel.delivered) == 1
    assert queue.list_pending(conn) == []
    counts = dict(
        conn.execute(
            "SELECT node_name, count(*) FROM effect_log GROUP BY node_name"
        ).fetchall()
    )
    assert counts["effect_deliver_message"] == 1
    assert counts["effect_record_outbound_audit"] == 1


def test_the_intake_graph_cannot_reach_the_candidate_gate():
    """
    ⭐⭐ tasks 5.9 / spec「内部通知不受影响」。**判据是结构性的，不是跑一遍看它
    没报错**——后者在"门禁被误插进采集图但恰好放行"时同样是绿的，而那时候红线
    已经破了（内部通知被候选人开关左右）。

    这里断言 app/graph/build.py 既不 import 候选人门禁的任何符号，也不调用
    deliver_candidate_message：采集图那条路径**结构上到不了**这道闸。
    """

    def imported_names(source: str) -> set[str]:
        names: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
                names |= {a.name for a in node.names}
            elif isinstance(node, ast.Import):
                names |= {a.name for a in node.names}
        return names

    source = (REPO_ROOT / "app" / "graph" / "build.py").read_text(encoding="utf-8")
    names = imported_names(source)

    assert not any(n.startswith("app.outbound") for n in names), names
    assert "deliver_candidate_message" not in names
    assert "compute_outbound_gate" not in names
    assert "deliver_candidate_message" not in source  # 连字符串引用都没有

    # 阳性对照：检查器确实能抓到
    assert "app.outbound.delivery" in imported_names(
        "from app.outbound.delivery import deliver_candidate_message\n"
    )


def test_internal_notifications_still_deliver_unconditionally(tmp_path):
    """
    tasks 5.9 的行为面：M1 现有投递行为与本变更前一致。画像确认卡片
    （type="confirmation_prompt"）**不带**门禁要的六个字段，若它被误接进候选人
    门禁，会因"未登记的消息类型"当场被拦——这条会红。

    ⚠️ 它与上一条互补：上一条证明"结构上到不了"，这条证明"就算到了也会立刻暴露"。
    """
    from app.channels.base import OutboundMessage
    from app.graph.nodes import effect_deliver_message, message_business_key

    conn = get_connection(str(tmp_path / "m1.db"))
    init_schema(conn)
    channel = SpyChannel()
    payload = {"question": "这个岗位需要几个人？"}

    effect_deliver_message(
        conn,
        thread_id="job-7",
        business_key=message_business_key(payload),
        channel=channel,
        message=OutboundMessage(type="confirmation_prompt", payload=payload),
    )

    assert len(channel.delivered) == 1
    assert channel.delivered[0][1].type == "confirmation_prompt"
    assert queue.list_pending(conn) == []  # 内部通知不进候选人待审批队列
```

- [ ] **Step 2: 跑测试确认通过**

```bash
./venv/bin/python -m pytest tests/test_outbound_end_to_end.py -q
```

预期：**5 passed**。

- [ ] **Step 3: 变异验证——把门禁接进采集图会不会被抓到**

```bash
./venv/bin/python - <<'PYEOF'
import pathlib, shutil
p = pathlib.Path("app/graph/build.py")
shutil.copy(p, "/tmp/build.orig.py")
src = p.read_text(encoding="utf-8")
p.write_text("from app.outbound.delivery import deliver_candidate_message  # 变异\n" + src, encoding="utf-8")
print("mutated: 采集图 import 了候选人门禁")
PYEOF
./venv/bin/python -m pytest tests/test_outbound_end_to_end.py 2>&1 | grep -E "^FAILED|passed|failed"
cp /tmp/build.orig.py app/graph/build.py && rm /tmp/build.orig.py
./venv/bin/python -m pytest tests/test_outbound_end_to_end.py -q 2>&1 | tail -2
git status --short app/graph/build.py && echo "⛔ build.py 没还原干净，停下来查" || echo "build.py 已还原"
```

预期：变异后 `test_the_intake_graph_cannot_reach_the_candidate_gate` **单独变红**；还原后全绿且 `git status` 对 `build.py` 无输出。

- [ ] **Step 4: 全量与守护**

```bash
./venv/bin/python -m pytest tests -q 2>&1 | tail -2
./venv/bin/python -m pytest tests/test_audit_recorder.py -k "no_effect_function or packed_method or imports_no_config or scan_is_not" -q 2>&1 | tail -2
./venv/bin/python -m pytest tests/test_graph_idempotency.py tests/test_transaction_ownership.py tests/test_web_api.py -q 2>&1 | tail -2
```

预期：三条全绿，`0 failed`。第三条是 M1 既有行为的回归面。

- [ ] **Step 5: 边界与依赖证据**

```bash
git status --short app/web/server.py app/config.py app/audit/ app/agents/ && echo "(无输出=没碰)"
git diff --stat main -- requirements.txt pyproject.toml && echo "(无输出=依赖零新增)"
git diff app/graph/nodes.py | grep '^-' | grep -v '^---' && echo "⛔ 改到了既有节点" || echo "nodes.py 纯追加"
```

- [ ] **Step 6: 登记"无生产调用方"**

在 `docs/tech-debt.md` 末尾追加：

```markdown
## TD-8 · 候选人外发门禁已就位，但生产里没有调用方

**欠的是什么**：`app/outbound/delivery.py:deliver_candidate_message()` 是候选人
拒信/邀约的受保护外发入口，U5 已把它连同待审批队列、两个 `effect_*` 节点与
拦截/放行留痕全部建好。**但 M1 里没有任何地方生成拒信或邀约**——实测
`grep -rn "rejection_letter|interview_invitation" app/` 在 `app/outbound/` 之外
零命中，采集图只发 `question` / `confirmation_prompt` 这类内部通知。

**所以本单元交付的是"机制"不是"在跑的流程"**：门禁、队列、留痕全部有测试覆盖，
但生产路径上一次都不会被执行到。与 U3 的 `audit_context` 同一形状。

**触发条件**：M2 开始生成候选人信件时。那个单元**必须**走
`deliver_candidate_message()`，⛔ 不得直接调 `effect_deliver_message` 或
`channel.deliver` 发候选人信件——那会绕过整道闸，而合规红线「AI 只做排序推荐、
不做自动淘汰」的技术保证就在这道闸上。

**怎么还**：M2 的拒信/邀约生成单元接上这个入口，并把
`is_candidate_outbound_enabled()` 作为 `outbound_enabled` 传进去。

**不还的后果**：一整套门禁与审批留痕建好了却没人用，而真正发信的代码另起一条
不受管的路径——比没有门禁更糟，因为审计会看到一个"门禁存在"的假象。

**为什么现在只登记不做**：拒信/邀约的内容生成属 M2 范围
（`delivery-units.md:26` 给 U5 的文件边界不含 agent 层）。
```

- [ ] **Step 7: 提交**

```bash
git add tests/test_outbound_end_to_end.py docs/tech-debt.md
git commit -m "test(outbound): 拦截→入队→放行→投递端到端，内部通知结构性隔离（tasks 5.6-5.9）"
```

---

## 交付前自查

- [ ] `grep -c '^### Task ' docs/superpowers/plans/2026-08-28-ai-audit-trail-unitU5-queue-and-wiring.md` > 0
- [ ] 全量 `pytest tests -q` 0 failed
- [ ] `git status --short app/web/server.py app/config.py app/audit/ app/agents/ app/graph/build.py` 无输出
- [ ] `git diff app/graph/nodes.py | grep '^-' | grep -v '^---'` 无输出（纯追加）
- [ ] `git diff --stat main -- requirements.txt pyproject.toml` 无输出
- [ ] 四次变异验证（content_hash 含签名 / 放行复发也入队 / 采集图 import 门禁 / 见 Task 2 Step 5 的结构守护阳性对照）各自看到**预期的那条**单独变红
- [ ] `tests/test_audit_recorder.py::test_no_effect_function_appends_jsonl` 仍绿——**它从本单元起才第一次真正有活干**

## spec 覆盖对照

| tasks.md 第 5 章 | 落点 | 验收 |
|---|---|---|
| 5.1 队列读写与状态机、放行不 DELETE、只返回 pending | Task 1 | `tests/test_outbound_queue.py` 前 8 条 |
| 5.2 `approve` 带签名重走门禁 + 死锁防线 | Task 2 | `test_approving_with_the_switch_off_does_not_deliver_and_does_not_requeue`、`test_the_approve_path_contains_no_enqueue_call`（AST + 阳性对照） |
| 5.3 `effect_enqueue_pending_approval` 幂等与事务 | Task 3 | `test_enqueue_effect_writes_the_row_and_its_effect_log_together`、`..._is_idempotent_on_replay`、`..._commits_exactly_once` |
| 5.4 `effect_record_outbound_audit`，拦截与放行各一条痕 | Task 3 | `test_block_and_release_of_one_draft_each_leave_their_own_trail` |
| 5.5 分流接线，不改 `effect_deliver_message` 与 `Channel` | Task 4 | `tests/test_outbound_delivery.py` 全 6 条 + Task 3 Step 6 的纯追加判据 |
| 5.6 端到端拦截 | Task 5 | `test_blocked_letter_is_queued_with_reason_and_evidence` |
| 5.7 端到端放行 | Task 5 | `test_approving_the_queued_letter_delivers_it_and_leaves_a_second_trail` |
| 5.8 重放安全 | Task 5 | `test_replaying_the_whole_flow_repeats_no_side_effect` |
| 5.9 内部通知不受影响 | Task 5 | `test_the_intake_graph_cannot_reach_the_candidate_gate`（结构）+ `test_internal_notifications_still_deliver_unconditionally`（行为） |

**spec `Scenario: 确认人放行一条已知的高风险消息`** → Task 5 的 5.7 那条（D-6 口径 B 的端到端兑现）。
**spec `Scenario: 确认人不能放行一条畸形消息`** → Task 2 的 `test_a_malformed_draft_is_not_delivered_even_with_a_signature`。
**spec `Scenario: 重复判定结果一致` / `流程从中断处恢复重跑`** → U4 已覆盖纯函数那一半，U5 补 `test_replaying_the_whole_flow_repeats_no_side_effect`。

## 本计划相对 `tasks.md` / `delivery-units.md` 的偏离登记（三条，全部需 reviewer 确认）

1. **新增 `app/outbound/messages.py` 与 `app/outbound/delivery.py` 两个文件**（`delivery-units.md:26` 给 U5 列的是 `app/outbound/queue.py`｜`app/graph/nodes.py`｜`app/graph/build.py`）。理由：① 门禁要的六个字段在既有 `OutboundMessage`（只有 `type` / `payload`，`app/channels/base.py:8-11`）上不存在，必须有一个具体形状承载，塞进 `queue.py` 会让"消息是什么"和"队列怎么存"耦在一起；② 编排逻辑放 `queue.py` 会让队列模块反过来 import 图节点，形成 `queue → nodes → queue` 的循环。**`app/graph/build.py` 反而没动**——见偏离 2。

2. **⛔ 不改 `app/graph/build.py`**（`delivery-units.md:26` 把它列进 U5 的触碰文件）。理由：`build.py` 里的是**采集图**，它投递的 `question` / `confirmation_prompt` 都是发给业务经理的内部通知，spec「内部通知不受影响」明令它们不走候选人门禁。把门禁接进采集图正是这条要防的事。候选人外发是一条**独立入口**（`deliver_candidate_message`），M2 的信件生成单元直接调它。方向是更严不是更松，且 5.9 的结构守护把这条钉住了。

3. **`CandidateOutboundMessage` 的 `severity` 默认最高级、`requires_confirmation` 默认 `True`**（本文件与 `delivery-units.md` 都没规定默认值）。理由：spec「门禁覆盖范围」逐字「拒信与邀约这两类 MUST **一律**判为高风险」。默认值写反的话，一封忘记显式设置的拒信会走"低风险"路径直接发出去——默认值必须站在红线这一侧。

## 已登记的边界与技术债（不在本单元解决）

| 事项 | 处置 |
|---|---|
| 生产里没有调用方 | **TD-8**（Task 5 Step 6 登记）。M2 的信件生成单元必须走 `deliver_candidate_message()` |
| 待审批队列的 Web UI / API | ⛔ 不做，要改 `app/web/server.py`，超出文件边界。M2 或单独变更 |
| `confirmed_by` 不可信（鉴权空壳） | `design.md` D7，U7 的 7.5 登记，U5 不重复登记 |
| 审批时效提醒 | `design.md` Open Questions，明确不改本变更的 spec 与任务拆解 |
| 拦截原因的分布统计 | U6 的 6.5。U5 只保证 `evidence` 与 `blocked_reason` 落得下去 |

## 需 Shao Peishen 拍板 / 需协调

1. **⚠️ 开工时序**：本计划成立的前提是 U4 已合并且第 4 章回勾。实测 U4 的代码（含 D-6 口径 B）已在 main 上（`121713f`、`bcc41a1`），但 **`tasks.md` 第 4 章仍是 0/9**——那条 session 还没走完 final review 与回勾。U5 开工前先确认第 4 章已回勾，否则门禁的行为可能还会再变一次。
2. **D-6 已闭环，无需再决**：2026-08-28 你裁定取 (b)，而 U4 那条 session 在同一天独立得出并落码了同一结论（`121713f` 的 commit message 与 `app/outbound/gate.py` 第 ② 条下方的注释）。两条路径互为佐证，本计划按 (b) 写。
