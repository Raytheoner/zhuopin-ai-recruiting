# TD-9 · 外发重试留痕（`outbound-retry-audit-trace`）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让「一封候选人信件首次被门禁拦下入队后、人工放行又被外发总开关拦下」这一次尝试**留下一条独立的痕**，把 `docs/tech-debt.md` TD-9 的两条成因（`approve()` 被拦时早返回不留痕 ＋ 幂等键只到 `{content_hash}:{allowed}` 分辨不出"是哪一条拦截"）一次改完。

**Architecture:** 两处改动、零新增模块。① `app/outbound/delivery.py` 把 `DecisionEvent.id` 与 `business_key` 的字符串公式收敛成**唯一一个求值函数** `audit_business_key()`，公式升级为 `{content_hash}:{allowed}:{reason}`（`reason` 以空串归一化 `None`）；同时把"构造事件 → 调 `effect_record_outbound_audit` → 按返回值是否为 `None` 决定是否 `mirror()`"这一整段从 `deliver_candidate_message()` 尾部提炼成公共函数 `record_outbound_decision()`。② `app/outbound/queue.py:approve()` 追加关键字参数 `recorder: AuditRecorder`，被拦分支通过**函数体内延迟 import** 调用同一个 `record_outbound_decision()`，规避 `queue → nodes → queue` 的模块级循环导入。⛔ 不改 `idempotent_effect` 装饰器、`effect_log` 表、`Channel` Protocol、`effect_deliver_message` 函数体、`app/outbound/gate.py`。

**Tech Stack:** Python 3.14 · SQLite（`app/storage/db.py` 单连接）· pytest 8.3.4 · 标准库 `ast` / `json` / `logging`（本单元 ⛔ 不新增任何依赖，`requirements.txt` 一行不改）

**范围（交付单元）：** `openspec/changes/outbound-retry-audit-trace/tasks.md` **第 1 章（1.1–1.8）＋ 第 2 章（2.1–2.7）合为一个交付单元**，共 15 项，一条分支。

> 🔴 **这是对「一章一 plan / 一章一分支」的明示偏离，理由必须随计划走，不要在 review 时当成疏漏：**
> 第 2 章不是一批独立可交付的功能，而是第 1 章的**回归与契约同步**——其中 2.5 要把
> `openspec/changes/ai-audit-trail-and-outbound-gate/tasks.md` 5.4 那条**逐字写定**的幂等键公式
> 从 `{content_hash}:{allowed}` 订正为 `{content_hash}:{allowed}:{reason}`。
> 拆成两条分支分别合并，main 上必然出现一段"代码里幂等键已经是三段式、5.4 的字面规定还是两段式"的中间态；
> 而 5.4 是**已过审的契约文本**，`docs/tech-debt.md` TD-9 与 U5 的 5.x 偏离登记都在引用它。
> 契约与实现在 main 上短暂对不上，比多合并一次的成本高得多——所以两章一起进、一起出。

**输入（真源）：**
- `openspec/changes/outbound-retry-audit-trace/specs/outbound-approval-gate/spec.md`（**唯一行为契约输入**）
- `openspec/changes/outbound-retry-audit-trace/design.md` 决策 1 / 决策 2 / Risks
- `openspec/changes/outbound-retry-audit-trace/proposal.md` Why / Non-goals / Impact
- `docs/tech-debt.md` TD-9（成因两条叠加，缺一条修不好）
- `tasks.md` **只用来划范围**，⛔ 不是本计划的输入（`03-工具链协作规则.md` 的接缝规则）

**基线（2026-09-04 实跑，`venv/bin/python`）：** `tests/test_outbound_delivery.py tests/test_outbound_queue.py tests/test_outbound_end_to_end.py tests/test_outbound_block_stats.py` → **44 passed**。本计划所有"预期 FAIL / 预期 PASS"都以这个基线为准。

---

## ⚠️ 开工前必读：TD-9 已实跑复现，别再花时间证明它存在

复现脚本（2026-09-04 在 main 上跑，非计划产物、⛔ 不要提交进仓库）：对同一封拒信先后调用两次
`deliver_candidate_message()`——第一次未签名 + 开关开（拦截原因 `消息自称需要人工确认`），
第二次带签名 + 开关关（拦截原因 `外发总开关关闭`）。实测输出：

```
d1.reason = 消息自称需要人工确认
d2.reason = 外发总开关关闭
content_hash 相同 = True
镜像行数 = 1                      ← 应为 2
   job-7:effect_record_outbound_audit:d38f4a…b2b7:False | 消息自称需要人工确认
WARNING 外发留痕已存在（重放），跳过镜像 append（id=…:False）
approve 被总开关拦下 → 外发总开关关闭 ；镜像行数 1 → 1     ← 应为 2
```

**三条结论直接拿去写测试，不要重新验证：**

1. `content_hash()` **刻意不含 `confirmed_by`**（`tests/test_outbound_queue.py::test_content_hash_ignores_the_confirmation_signature` 钉死的），所以"首次拦截"与"放行被拦"两次的 `content_hash` **必然相同**、`allowed` **必然都是 `False`**——旧公式下两次的 `business_key` 逐字相同，第二次撞 `effect_log` 短路。这就是成因②。
2. `queue.approve()` 走被拦分支时**镜像行数原地不动**（1 → 1），因为它连 `deliver` 都没调用。这就是成因①。
3. ⚠️ **默认 `CandidateOutboundMessage` 的首次拦截原因是 `消息自称需要人工确认`（`REASON_CONFIRMATION_REQUIRED`），不是 tasks.md 1.7 散文里写的"等待人工确认"。**
   原因：`requires_confirmation` 默认 `True`（`app/outbound/messages.py:31`，红线要求），`app/outbound/gate.py:284` 先命中 `REASON_CONFIRMATION_REQUIRED`；`REASON_AWAITING_CONFIRMATION`（"等待人工确认"）只在 `requires_confirmation=False` 且非最高风险时才出现。
   **处置：测试里 ⛔ 不许硬编码首次拦截的原因文案**，一律用第一次调用返回的 `decision.reason` 或 `gate.REASON_*` 常量。spec Scenario 关心的是"两次拦截原因不同 → 两条痕"，不限定第一条具体是哪个原因；这不是偏离，是把散文落到实际取值上。

---

## Global Constraints

**每一条都是 reviewer 的注意力透镜。违反其中任何一条即判 reject，不进入下一个 Task。**

### 一、本单元专属（来自 design.md 与 opener，逐字）

1. **design 决策 1：** 幂等键与 `DecisionEvent.id` 统一为 `{content_hash}:{allowed}:{reason}`，`reason` 以空串归一化 `None`；**同原因重放键不变、照样短路**。
   ⛔ 不为放行分支单独省略 `:{reason}` 段——放行事件的 key 形如 `{content_hash}:True:`（末尾空段），两条分支必须共用**同一个求值表达式**。reviewer 判据：全仓库 `grep -rn '{content_hash}:{decision.allowed}\|:{decision.allowed}"' app/` 零命中，且 `app/` 下拼这个 key 的地方**有且只有一处**。

2. **design 决策 2：** `approve()` 追加**关键字参数** `recorder: AuditRecorder`；留痕提炼为公共函数，函数体内**延迟 import** 规避循环依赖。
   ⛔ `app/outbound/queue.py` **模块顶部**不许出现 `from app.graph.nodes import ...` 或 `from app.outbound.delivery import ...`（会构成 `queue → nodes → queue` 模块级循环导入，`app/graph/nodes.py` 顶部已有 `from app.outbound import queue`）。`AuditRecorder` 只进 `TYPE_CHECKING` 块。

3. **⛔ 不改 `CANDIDATE_OUTBOUND_ENABLED` 默认值与读取逻辑**（`app/outbound/delivery.py` 模块 docstring 的**不可代项**，改它要 Shao Peishen 本人拍板）；**⛔ 不改 `idempotent_effect` 装饰器、`effect_log` 表、`Channel` Protocol、`effect_deliver_message` 函数体**。
   reviewer 判据：`git diff --stat` 里 ⛔ 不得出现 `app/storage/idempotency.py`、`app/storage/db.py`、`app/channels/`；`app/graph/nodes.py` 的 diff **只能是 `effect_record_outbound_audit` 的 docstring**，一行可执行代码都不许改。

4. **⛔ 禁止在 `effect_*` 函数体内 append JSONL。** AST 守护 `tests/test_audit_recorder.py::test_no_effect_function_appends_jsonl` 必须仍绿。
   ⚠️ 提炼出来的 `record_outbound_decision()` **确实**会调 `recorder.mirror()`——这合规，因为它**不是** `effect_*` 前缀的函数、也没有 `@idempotent_effect` 装饰，`mirror()` 在装饰器 `commit` 之后才执行（`delivery-units.md` §3.4 第 2/4 条）。⛔ 不许为了"省一次调用"把 mirror 塞回 `effect_record_outbound_audit` 体内。

5. **⛔ 不改 `app/outbound/gate.py` 与 `tests/test_outbound_gate.py` 的四条锁定用例**；死锁防线两条测试**原样通过**（tasks 2.1 / 2.2）：
   `test_the_approve_path_contains_no_enqueue_call`（AST 结构守护）、`test_the_switch_off_path_never_calls_enqueue`（行为级 spy）。
   ⛔ 被拦分支**不加 `enqueue`**、不改 CAS 之前的提前返回、不改返回值——除留痕外**一个状态都不许动**（design D5）。

6. **重放测试的判据 ＝ JSONL 镜像行数，⛔ 不许只断言 `effect_log` 计数。**
   `idempotent_effect` 重放时返回 `None`、函数体根本没跑，`effect_log` 本来就恒为 1 条——它对"镜像被写重了没有"**零分辨力**（U5 终审踩过的假绿；`app/audit/hook.py:174-218` 有实测证据：SQLite 1 行、JSONL 2 行，`reconcile().ok` 仍为 `True`）。

7. **2.4 必须让 6.5 `outbound_block_stats` 真的看见第二次拦截**——这是 TD-9 的可观测性目标**本身**，必须有测试，⛔ 不许只在代码里"应该能看见"就算完。

8. **2.5 改 ai-audit 包 `tasks.md` 5.4 字面公式；2.6 TD-9 销账保留成因原文；2.7 归档本包。**
   ⛔ 2.7 只归档 `outbound-retry-audit-trace` **本包**，⛔ **不归档** `ai-audit-trail-and-outbound-gate`——那一包的归档是另一条泳道（0903Q）的事，它的 `tasks.md` 顶部进度行已经写明"归档交 0903Q（等 5.4 公式订正）"。本单元只负责把 5.4 订正掉，把归档留给它。

### 二、工程铁律（`CLAUDE.md`，逐字复制）

9. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。

10. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。

11. **所有 AI 评分必须持久化**：模型标识 + 模型版本 + prompt 版本 + temperature + 输入哈希 + rubric 快照 + 原始响应。

12. **每条 `criterion_score` 必须有 `evidence_ref`**（回指简历原文或面试 turn 的 offset）。`evidence_ref` 为空不允许写入。

13. **`temperature=0`；模型版本优先显式锁定**，禁止 `latest` 类别名。

14. **企微回调先落库再处理**：只推一次、5 秒无响应即丢弃。回调接口只做签名校验 + 落库 + 返回 200。

15. **`langgraph >= 1.0.10`**（GHSA-g48c-2wqr-h844）。

### 三、合规红线（`CLAUDE.md`，与本单元直接相关的三条，逐字复制）

16. **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。审计断言：`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。

17. **AI 生成的 JD、拒信、邀约须带标识**（《AI 生成合成内容标识办法》2025-09-01 施行）。

18. 候选人入口一律用一次性邀请链接，避免被认定"向境内公众提供"。

> 🔴 **本单元与红线 16 的关系**：TD-9 丢的正是"人工确认节点留痕"的一半——审计今天分辨不出"从未尝试放行"与"尝试放行但被总开关拦下"。修完之后这条证据链才完整。⛔ 但本单元**不改变门禁的任何判定逻辑**：被拦的信仍然一封都发不出去，`compute_outbound_gate` 的 fail-closed 判定一步不动。

### 四、并发协议（本仓库可能有别的泳道在并行跑）

19. 只 `git add` 本计划每个 Task 明确列出的路径。⛔ 禁止 `git add -A` / `git add .` / `git commit -a`。
20. `git status` 里出现别人的改动是正常的，不要停下、不要问、不要顺手提交。
21. ⛔ 不在 commit 前 `pull`。顺序：add 明确路径 → commit → push；push 被拒才 `git pull --rebase --autostash origin main` 再重试，最多 3 次。
22. 报 `.git/index.lock` 已存在 → 等 5 秒重试最多 5 次，⛔ 绝不删除该锁。

---

## File Structure

| 文件 | 动作 | 责任 |
|---|---|---|
| `app/outbound/delivery.py` | 修改 | 新增 `audit_business_key()`（公式唯一求值处）与公共函数 `record_outbound_decision()`；`_audit_event()` 与 `deliver_candidate_message()` 改为复用它们 |
| `app/outbound/queue.py` | 修改 | `TYPE_CHECKING` 加 `AuditRecorder`；`approve()` 加 `recorder` 关键字参数；被拦分支延迟 import 调 `record_outbound_decision()` |
| `app/graph/nodes.py` | 修改（**仅 docstring**） | `effect_record_outbound_audit` 的 `business_key` 说明更新为新公式 + 两个调用点 |
| `tests/test_outbound_delivery.py` | 修改 | 新增公式与提炼的单元覆盖；订正引用旧公式的 docstring |
| `tests/test_outbound_queue.py` | 修改 | `_approve()` helper 与三处直接调 `queue.approve(...)` 的地方补 `recorder` |
| `tests/test_outbound_end_to_end.py` | 修改 | 订正硬编码的旧 `expected_id`；新增 1.7 / 1.8 / 2.4 三条回归 |
| `openspec/changes/ai-audit-trail-and-outbound-gate/tasks.md` | 修改（**仅 5.4 一行 + 一句依据**） | 契约字面同步 |
| `docs/tech-debt.md` | 修改（**仅 TD-9 末尾追加**） | 销账状态 |
| `openspec/changes/outbound-retry-audit-trace/tasks.md` | 修改 | 回勾 15 项 checkbox |

**⛔ 不新建任何文件。** 留痕逻辑留在 `delivery.py`——那里已经是它事实上的归属地（design 决策 2 逐字）。

---

## ⚠️ 三个非显然点，实施前先读，reviewer 也按这三条查

### （一）`result is None` 与 `result is False` 是两件完全不同的事

提炼 `record_outbound_decision()` 时，`app/outbound/delivery.py:114-149` 那一大段注释**必须原样搬过去**，一个字都不许精简。它是 2026-08-28 两轮 review 才收敛出的结论：

- `None` → 装饰器判定 `(thread_id, business_key)` 已在 `effect_log` 里，函数体**没有真的执行**，这是重放 → ⛔ 不能再 `append`；
- `False` → 函数体**真的执行了**，只是外发事件在 `SqliteSink` 里没有真身（`SUPPORTED_EVENT_TYPES` 只收 `ai_analysis`）→ **必须** `mirror`，否则外发决策**永远**没有留痕；
- 判据不是布尔真值性，是"**是不是 `None`**"。

### （二）被拦分支现在会经由装饰器 `commit`——这是已知语义变化，必须同步改 docstring

`approve()` 的 docstring 现在写着「⛔ 不自行 `commit`：调用方负责事务边界」。1.4 之后，被拦分支调用的 `record_outbound_decision()` 内部会走 `effect_record_outbound_audit`，而 `idempotent_effect` 装饰器**在函数体成功后 `conn.commit()`**（`app/storage/idempotency.py:75`）。

**判断：可接受，与投递路径完全同构**——被拦分支在这一步之前只做过读（`get()` / `compute_outbound_gate()`），`mark_resolved` 的 CAS 在早返回之后、根本没执行，没有半截事务会被这次 commit 意外提交。

**但 docstring 必须同步订正**，改成"本函数自身不 `commit`；被拦分支的留痕经 `effect_*` 装饰器提交，与 `deliver_candidate_message` 的既有行为一致"。⛔ 不许留着旧句子——下一个人按旧 docstring 推理会得出错误的事务模型。

### （三）`content_hash` 在 `record_outbound_decision()` 内只算一次

design 决策 2 定死的签名是 `record_outbound_decision(conn, *, thread_id, message, decision, recorder) -> None`——不含 `content_hash`。所以函数内部要 `content_hash = message.content_hash()` 算一次，**同时喂给 `_audit_event()` 与 `audit_business_key()`**。

这**不违反** `_audit_event()` docstring 里"`content_hash` 由调用方传入、⛔ 不在这里重新算一遍"那条：那条防的是"两个调用点各算一遍 → 隐性耦合"，而这里是**一处算、两处用**。⛔ 不许在 `_audit_event()` 或 `audit_business_key()` 内部再调一次 `message.content_hash()`。

---

## Task 数与 spec 覆盖

本计划 6 个 Task。spec 只有一条 MODIFIED Requirement「外发与拦截动作强制留痕」，其四条 Scenario 映射如下：

| spec 元素 | 覆盖它的 Task |
|---|---|
| Requirement 正文第 2 段（"多次尝试各计一次、各留一条独立记录，MUST NOT 合并或静默丢弃"） | Task 1（幂等键）＋ Task 3（approve 留痕） |
| Scenario「外发成功后留痕」（回归，不得被破坏） | Task 2（提炼后全量回归）＋ Task 3 |
| Scenario「拦截后留痕」（回归） | Task 2 |
| Scenario「查询某类消息的拦截情况」 | Task 5（6.5 统计能看见第二次拦截） |
| Scenario「从待审批队列放行时被总开关拦下，与首次入队的拦截分别留痕」（本变更新增） | Task 3（正面）＋ Task 4（"也不因重放产生第三条"那一句） |

| tasks.md 条目 | Task |
|---|---|
| 1.1 / 1.5 | Task 1 |
| 1.2 | Task 2 |
| 1.3 / 1.4 / 1.6 / 1.7 / 2.1 / 2.2 | Task 3 |
| 1.8 | Task 4 |
| 2.3 | Task 2（提炼当次验收）＋ Task 6（收口全量） |
| 2.4 | Task 5 |
| 2.5 / 2.6 / 2.7 | Task 6 |

---

### Task 1: 幂等键公式统一为 `{content_hash}:{allowed}:{reason}`

**Files:**
- Modify: `app/outbound/delivery.py:40-65`（`_audit_event()`）、`app/outbound/delivery.py:105-112`（`business_key=` 那一处）
- Modify: `app/graph/nodes.py:317-333`（`effect_record_outbound_audit` docstring，**仅 docstring**）
- Modify: `tests/test_outbound_end_to_end.py:179-190`（硬编码的旧 `expected_id`）
- Modify: `tests/test_outbound_delivery.py:238-243`（引用旧公式的 docstring 句子）
- Test: `tests/test_outbound_delivery.py`（新增两条）

**Interfaces:**
- Produces: `app.outbound.delivery.audit_business_key(content_hash: str, decision: GateDecision) -> str`——Task 2 的 `record_outbound_decision()` 与 Task 3 的 `approve()` 路径都经由它取 key，⛔ 不许出现第二个拼接点。

- [ ] **Step 1: 写失败测试（公式本身 + 端到端两条痕）**

追加到 `tests/test_outbound_delivery.py` 末尾：

```python
def test_the_business_key_carries_the_block_reason(wired):
    """
    design.md 决策 1。TD-9 成因②：旧公式 `{content_hash}:{allowed}` 只分辨
    "拦截 vs 放行"，分辨不出"是哪一条拦截"——同一草稿的第二次拦截撞上 effect_log
    已有行被短路，镜像里一行都不多。

    ⚠️ content_hash 刻意不含 confirmed_by（test_content_hash_ignores_the_
    confirmation_signature 钉死），所以"未签名被拦"与"签名后被开关拦"两次的
    content_hash 必然相同、allowed 必然都是 False——旧公式下两个 key 逐字相同。
    """
    from app.outbound.delivery import audit_business_key

    message = _msg()
    signed = message.with_confirmation("张三")
    blocked_by_confirmation = compute_outbound_gate(message, lambda: True)
    blocked_by_switch = compute_outbound_gate(signed, lambda: False)

    assert message.content_hash() == signed.content_hash()  # 前提，先钉住
    assert blocked_by_confirmation.allowed is False
    assert blocked_by_switch.allowed is False
    assert blocked_by_confirmation.reason != blocked_by_switch.reason

    key_a = audit_business_key(message.content_hash(), blocked_by_confirmation)
    key_b = audit_business_key(signed.content_hash(), blocked_by_switch)
    assert key_a != key_b, "两条不同原因的拦截必须落在两个不同的幂等键上"
    assert key_a.endswith(f":{blocked_by_confirmation.reason}")
    assert key_b.endswith(f":{blocked_by_switch.reason}")


def test_an_allowed_decision_keeps_the_empty_reason_segment(wired):
    """
    design.md 决策 1 的归一化规则：`reason` 在 allowed=True 时恒为 None，
    拼接前统一取 `decision.reason or ""`，放行事件的 key 形如
    `{content_hash}:True:`（末尾空段）。

    ⛔ 不为放行分支单独省略 `:{reason}` 段——两条分支必须共用同一个求值表达式，
    分叉的公式迟早会被改错其中一半而没人发现。
    """
    from app.outbound.delivery import audit_business_key

    signed = _msg().with_confirmation("张三")
    allowed = compute_outbound_gate(signed, lambda: True)

    assert allowed.allowed is True and allowed.reason is None
    assert audit_business_key(signed.content_hash(), allowed) == (
        f"{signed.content_hash()}:True:"
    )


def test_two_different_block_reasons_leave_two_mirror_lines(wired):
    """
    ⭐ TD-9 成因②的正面回归，判据是 **JSONL 镜像行数**（Global Constraint 6）。
    ⛔ 不许改成断言 effect_log 计数：重放时 idempotent_effect 返回 None、函数体
    根本没跑，effect_log 本来就恒为 1 条，对"镜像被写重/被吞"零分辨力。

    2026-09-04 在 main 上实测：镜像行数 = 1（应为 2），WARNING 里可见
    「外发留痕已存在（重放），跳过镜像 append」。
    """
    conn, chain_path, recorder, channel = wired
    message = _msg()

    first = deliver_candidate_message(
        conn, thread_id="job-7", message=message, channel=channel,
        recorder=recorder, outbound_enabled=lambda: True,
    )
    second = deliver_candidate_message(
        conn, thread_id="job-7", message=message.with_confirmation("张三"),
        channel=channel, recorder=recorder, outbound_enabled=lambda: False,
    )

    assert first.allowed is False and second.allowed is False
    assert first.reason != second.reason
    lines = _mirror_lines(chain_path)
    assert len(lines) == 2, f"两次不同原因的拦截应各留一条痕，实得 {len(lines)} 行：{lines}"
    assert {l["blocked_reason"] for l in lines} == {first.reason, second.reason}
    assert len({l["id"] for l in lines}) == 2, "两条痕的 id 必须可分别检索"
```

⚠️ `_mirror_lines` / `_msg` / `wired` 是 `tests/test_outbound_delivery.py` 里**已有**的 helper 与 fixture，⛔ 不要重新定义。

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest tests/test_outbound_delivery.py -q -k "business_key or empty_reason_segment or two_different_block_reasons"`
Expected: FAIL — 前两条 `ImportError: cannot import name 'audit_business_key'`，第三条 `AssertionError: 两次不同原因的拦截应各留一条痕，实得 1 行`。

- [ ] **Step 3: 实现——公式收敛到唯一求值处**

在 `app/outbound/delivery.py` 的 `_audit_event()` **之前**插入：

```python
def audit_business_key(content_hash: str, decision: GateDecision) -> str:
    """
    外发留痕幂等键与 `DecisionEvent.id` 的**唯一**求值处（design.md 决策 1）。

    公式 `{content_hash}:{allowed}:{reason}`。⛔ 不许在别处再拼一遍这个字符串——
    两个调用点（`deliver_candidate_message` 与 `queue.approve`）各写一份，迟早会
    被改错其中一半而没人发现，而失败是**静默的**：键分叉之后，同一次尝试会按两种
    粒度判重，第二次拦截要么被吞、要么被写重。

    ⚠️ `reason` 归一化为空串：`decision.reason` 在 `allowed=True` 时恒为 `None`
    （`app/outbound/gate.py` 放行分支的构造），放行事件的 key 形如
    `{content_hash}:True:`（末尾空段）。⛔ 不为放行分支单独省略 `:{reason}` 段——
    两条分支必须共用同一个求值表达式。

    ⚠️ 为什么 `reason` 必须进键（TD-9 成因②）：旧公式 `{content_hash}:{allowed}`
    只区分"拦截 vs 放行"、不区分**是哪一条拦截**。`content_hash()` 刻意不含
    `confirmed_by`，于是"首次未签名被拦"与"放行后被总开关拦下"两次的 content_hash
    与 allowed 全都相同 → 键逐字相同 → 第二次撞 effect_log 被 `idempotent_effect`
    短路 → 镜像 append 被跳过 → **一条痕都不产生**。
    """
    return f"{content_hash}:{decision.allowed}:{decision.reason or ''}"
```

`_audit_event()` 的 `id=` 一行改为：

```python
        id=f"{thread_id}:effect_record_outbound_audit:"
        f"{audit_business_key(content_hash, decision)}",
```

`deliver_candidate_message()` 里 `business_key=` 一行改为：

```python
        business_key=audit_business_key(content_hash, decision),
```

- [ ] **Step 4: 更新 `effect_record_outbound_audit` 的 docstring（⛔ 只改 docstring）**

把 `app/graph/nodes.py` 里这一段：

```python
    `business_key` = `{content_hash}:{allowed}`（tasks 5.4）——同一草稿的"拦截"
    与"放行"各留一条痕。⛔ 只用 content_hash 的话，放行那条会命中拦截那条的
    `effect_log` 被短路，于是**投递发生了却没有留痕**。
```

替换为：

```python
    `business_key` = `{content_hash}:{allowed}:{reason}`（tasks 5.4，经变更包
    `outbound-retry-audit-trace` 订正；原为 `{content_hash}:{allowed}`）——同一草稿
    的"拦截"与"放行"各留一条痕，**不同原因的两次拦截也各留一条**。
    ⛔ 只用 content_hash 的话，放行那条会命中拦截那条的 `effect_log` 被短路，
    于是**投递发生了却没有留痕**；只到 `{allowed}` 的话，"首次被拦"与"放行时被
    总开关拦下"两次的键逐字相同（content_hash 不含 confirmed_by），第二次尝试
    **一条痕都不产生**——这正是 TD-9。

    ⚠️ 现在有**两个调用点**共用同一条公式：`app/outbound/delivery.py` 的
    `deliver_candidate_message()` 与 `app/outbound/queue.py` 的 `approve()`，
    两者都经由 `app.outbound.delivery.audit_business_key()` 求值，
    ⛔ 不许任何调用点自己再拼一遍这个字符串。
```

⛔ 这个文件本 Task **只允许改这段 docstring**，一行可执行代码都不许动。

- [ ] **Step 5: 订正引用旧公式的既有测试**

① `tests/test_outbound_end_to_end.py:181-183` 的硬编码 id **必然变红**（它写死了旧的两段式）。改为：

```python
    # 第四样：镜像行数。id 由生产代码的构成规则重算（delivery.py:audit_business_key），
    # 与被断言的对象同源，⛔ 但与 REPLAYS 无关。
    from app.outbound.delivery import audit_business_key
    from app.outbound.gate import compute_outbound_gate

    allowed = compute_outbound_gate(signed, lambda: True)
    expected_id = (
        f"job-7:effect_record_outbound_audit:"
        f"{audit_business_key(signed.content_hash(), allowed)}"
    )
```

② `tests/test_outbound_delivery.py:240-242` 的 docstring 里那句
`（\`f"{content_hash}:{decision.allowed}"\` 里 \`decision.allowed\` 不同）`
改为 `（\`audit_business_key()\` 里 \`decision.allowed\` 不同）`。⛔ 只改这句注释，测试代码不动。

- [ ] **Step 6: 跑测试确认通过**

Run: `venv/bin/python -m pytest tests/test_outbound_delivery.py tests/test_outbound_end_to_end.py tests/test_outbound_queue.py tests/test_outbound_block_stats.py tests/test_audit_recorder.py -q`
Expected: PASS（基线 44 + `test_audit_recorder.py` 全部；新增 3 条 → 全绿，⛔ 零 failed、零 error）

Run: `grep -rn ':{decision.allowed}"' app/`
Expected: 无输出（旧公式在 `app/` 下已无残留）

- [ ] **Step 7: 提交**

```bash
git add app/outbound/delivery.py app/graph/nodes.py tests/test_outbound_delivery.py tests/test_outbound_end_to_end.py
git commit -m "fix(outbound): TD-9 幂等键并入 reason，两次不同原因的拦截各留一条痕（1.1/1.5）"
```

---

### Task 2: 把留痕逻辑提炼为公共函数 `record_outbound_decision()`（纯重构，行为不变）

**Files:**
- Modify: `app/outbound/delivery.py:105-162`（`deliver_candidate_message()` 尾部整段搬出）
- Test: `tests/test_outbound_delivery.py`（新增一条直调用例）

**Interfaces:**
- Consumes: Task 1 的 `audit_business_key(content_hash, decision) -> str`
- Produces: `app.outbound.delivery.record_outbound_decision(conn, *, thread_id: str, message: CandidateOutboundMessage, decision: GateDecision, recorder: AuditRecorder) -> None`——Task 3 的 `queue.approve()` 通过**函数体内延迟 import** 调用它。签名逐字来自 design.md 决策 2，⛔ 不许增删参数、不许改成位置参数。

> ⚠️ **本 Task 单独验收，⛔ 不与 Task 3 合并。** design.md「Risks / Trade-offs」第二条：提炼过程若不小心改变了原有执行顺序（先分流 `effect_enqueue_pending_approval` / `effect_deliver_message`，再构造 `event`，再调 `effect_record_outbound_audit`，再判是否 `mirror`），会**静默**改变既有测试锁定的行为。合并验收会让提炼引入的偏差被 Task 3 新功能的绿色掩盖。

- [ ] **Step 1: 写失败测试（公共函数可被独立调用）**

追加到 `tests/test_outbound_delivery.py`：

```python
def test_record_outbound_decision_is_callable_on_its_own(wired):
    """
    design.md 决策 2：留痕逻辑提炼成公共函数、两个调用点共用，⛔ 不许在
    queue.py 里手写第二份。

    这条钉住"它能被独立调用"这一件事本身——`queue.approve()` 的被拦分支
    （Task 3）拿不到 channel、也不该经过 deliver_candidate_message 的分流逻辑，
    它只需要留痕这一段。

    为什么必须提炼而不是重写一份：`result is None` / `is False` 的区分是两轮
    review 才收敛出的非显然结论（None=重放、不许再 append；False=真跑了、
    只是这个事件类型在 SqliteSink 里没有真身，必须 append）。手写第二份等于给
    这条不变式开一个可能读漏、写歪的第二入口。
    """
    from app.outbound.delivery import record_outbound_decision

    conn, chain_path, recorder, _channel = wired
    signed = _msg().with_confirmation("张三")
    decision = compute_outbound_gate(signed, lambda: False)

    record_outbound_decision(
        conn, thread_id="job-7", message=signed, decision=decision, recorder=recorder
    )

    lines = _mirror_lines(chain_path)
    assert len(lines) == 1
    assert lines[0]["blocked_reason"] == decision.reason
    assert lines[0]["confirmed_by"] == "张三"
    # 它只留痕：⛔ 不投递、⛔ 不入队
    assert queue.list_pending(conn) == []


def test_record_outbound_decision_does_not_duplicate_on_replay(wired):
    """
    提炼不得削弱判重：同一条决策连调三次，镜像仍恰好一行（判据是 JSONL 行数，
    ⛔ 不是 effect_log 计数——见 Global Constraint 6）。
    """
    from app.outbound.delivery import record_outbound_decision

    conn, chain_path, recorder, _channel = wired
    signed = _msg().with_confirmation("张三")
    decision = compute_outbound_gate(signed, lambda: False)

    for _ in range(3):
        record_outbound_decision(
            conn, thread_id="job-7", message=signed, decision=decision, recorder=recorder
        )

    assert len(_mirror_lines(chain_path)) == 1
```

⚠️ `queue`、`compute_outbound_gate`、`deliver_candidate_message` 在 `tests/test_outbound_delivery.py` 顶部**已经** import（第 17–19 行），⛔ 不要重复 import；`record_outbound_decision` / `audit_business_key` 用函数体内局部 import，与该文件既有风格一致。

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest tests/test_outbound_delivery.py -q -k "record_outbound_decision"`
Expected: FAIL — `ImportError: cannot import name 'record_outbound_decision' from 'app.outbound.delivery'`

- [ ] **Step 3: 实现——原样搬，⛔ 不许"顺手优化"**

在 `app/outbound/delivery.py` 里，`deliver_candidate_message()` **之后**新增：

```python
def record_outbound_decision(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    message: CandidateOutboundMessage,
    decision: GateDecision,
    recorder: AuditRecorder,
) -> None:
    """
    外发/拦截判定的留痕，**两个调用点共用的唯一一份实现**（design.md 决策 2）：
    `deliver_candidate_message()` 与 `app/outbound/queue.py:approve()` 的被拦分支。

    ⛔ 不要在别处重新手写一份——下面 `result is None` / `is False` 的区分是 2026-08-28
    两轮 review 才收敛出的非显然结论，处理反了会让镜像被写重、腐蚀 hash-chain 的
    唯一真源，而 `reconcile()` 的 id 集合差集**看不出**这种重复。

    ⚠️ 本函数**不是** `effect_*` 节点、⛔ 不加 `@idempotent_effect`：`recorder.mirror()`
    必须在装饰器 `commit` **之后**触发（`delivery-units.md` §3.4 第 2/4 条），
    这也是 AST 守护 `test_no_effect_function_appends_jsonl` 的合规边界所在。

    ⚠️ `content_hash` 在这里算一次、同时喂给 `_audit_event()` 与 `audit_business_key()`。
    `_audit_event()` docstring 里"不在这里重新算一遍"防的是"两个调用点各算一遍"的
    隐性耦合，⛔ 不是禁止一处算两处用。
    """
    content_hash = message.content_hash()
    event = _audit_event(thread_id, message, decision, content_hash)
    result = effect_record_outbound_audit(
        conn,
        thread_id=thread_id,
        business_key=audit_business_key(content_hash, decision),
        recorder=recorder,
        event=event,
    )

    # 第二段：镜像。**在这里而不是在 effect_* 函数体内**——此时装饰器已 commit
    # （delivery-units.md §3.4 第 3 条：允许的偏差只有单向「SQLite 有、JSONL 缺行」）。
    #
    # ⚠️ 【原样保留 app/outbound/delivery.py:117-142 那整段 None / False / True 三态
    #     注释，一个字都不许精简或改写】
    if result is None:
        logger.warning(
            "外发留痕已存在（重放），跳过镜像 append（id=%s）。同一 id 被写第二次"
            "通常意味着同一个 (thread_id, business_key) 被重复处理，"
            "而确定性 id 分辨不出来。",
            event.id,
        )
        return

    try:
        recorder.mirror(event)
    except Exception:  # noqa: BLE001 —— 镜像失败⛔ 不抛，理由同 app/audit/hook.py
        logger.error(
            "外发留痕镜像 append 失败（id=%s）。这是被允许的单向偏差，"
            "由对账检出、链尾补录；⛔ 不要改成抛异常。",
            event.id,
            exc_info=True,
        )
```

`deliver_candidate_message()` 的尾部（原 `event = _audit_event(...)` 到函数末尾）整段替换为：

```python
    record_outbound_decision(
        conn,
        thread_id=thread_id,
        message=message,
        decision=decision,
        recorder=recorder,
    )
    return decision
```

⛔ **不许改动 `deliver_candidate_message()` 前半段的任何一行**：判定一次 → 按 `decision.allowed` 分流到 `effect_deliver_message`、按 `elif not (message.confirmed_by or "").strip()` 分流到 `effect_enqueue_pending_approval` —— 顺序与判据全部保持原样（design D5 死锁防线）。

- [ ] **Step 4: 跑测试确认通过 + 全量回归（本 Task 的独立验收点）**

Run: `venv/bin/python -m pytest tests/test_outbound_delivery.py -q -k "record_outbound_decision"`
Expected: PASS（2 passed）

Run: `venv/bin/python -m pytest tests/test_outbound_delivery.py tests/test_outbound_queue.py tests/test_outbound_end_to_end.py tests/test_outbound_block_stats.py tests/test_audit_recorder.py tests/test_audit_hook.py tests/test_audit_sinks.py -q`
Expected: PASS，**零 failed、零 error**。
🔴 **验收判据（design Risks 第二条）：这一步除了 Step 1 新增的 2 条，既有用例的断言一行都没改过。**
若为了让某条既有用例变绿而修改了它 → **停下，说明理由**：那意味着提炼改变了行为，不是"测试写得不好"。

- [ ] **Step 5: 提交**

```bash
git add app/outbound/delivery.py tests/test_outbound_delivery.py
git commit -m "refactor(outbound): 留痕逻辑提炼为 record_outbound_decision，两个调用点共用（1.2）"
```

---

### Task 3: `approve()` 被总开关拦下时也留痕

**Files:**
- Modify: `app/outbound/queue.py:12-22`（`TYPE_CHECKING` 块）、`app/outbound/queue.py:164-217`（`approve()` 签名 + docstring + 被拦分支）
- Modify: `tests/test_outbound_queue.py:193-202`（`_approve()` helper）、`:239-245`、`:263-280`（两处直接调 `queue.approve(...)`）
- Modify: `tests/test_outbound_end_to_end.py:108-121`（手写 `deliver=lambda m: ...` 闭包所在的 approve 调用）
- Test: `tests/test_outbound_end_to_end.py`（新增 1.7 正面回归）

**Interfaces:**
- Consumes: Task 2 的 `record_outbound_decision(conn, *, thread_id, message, decision, recorder) -> None`
- Produces: `queue.approve(conn, approval_id_, *, confirmed_by: str, outbound_enabled: Callable[[], bool], deliver: Callable[[CandidateOutboundMessage], None], recorder: "AuditRecorder") -> "GateDecision"`——`recorder` 追加在 `deliver` **之后**，关键字参数，无默认值。

> ⛔ **`recorder` 不给默认值。** 给 `None` 默认值等于让调用方"忘了传也能跑"，而忘了传的后果正是 TD-9 本身：静默零留痕。宁可让所有调用点当场 `TypeError`。

- [ ] **Step 1: 写失败测试（1.7 正面回归）**

追加到 `tests/test_outbound_end_to_end.py`：

```python
def test_approving_into_a_closed_switch_leaves_its_own_trail(wired):
    """
    ⭐ TD-9 的正面回归（tasks 1.7 / spec Scenario「从待审批队列放行时被总开关拦下，
    与首次入队的拦截分别留痕」）。

    2026-09-04 在 main 上实测：approve() 被总开关拦下时镜像行数原地不动（1 → 1），
    因为它在 `if not decision.allowed: return decision` 就走了，`deliver` 从未被调用
    （TD-9 成因①），而且就算调了也会被旧幂等键吞掉（成因②）。两条成因缺一条修不好。

    ⚠️ 首次拦截的原因 ⛔ 不许硬编码文案：默认 CandidateOutboundMessage 的
    `requires_confirmation` 为 True（红线要求），实际命中的是
    `REASON_CONFIRMATION_REQUIRED`「消息自称需要人工确认」，不是 tasks 散文里写的
    「等待人工确认」。这里一律用第一次调用返回的 decision.reason 与 REASON_* 常量。

    判据是 **JSONL 镜像行数**（Global Constraint 6），⛔ 不是 effect_log 计数。
    """
    from app.outbound.gate import REASON_OUTBOUND_DISABLED

    conn, chain_path, recorder, channel = wired
    first = deliver_candidate_message(
        conn, thread_id="job-7", message=_msg(), channel=channel,
        recorder=recorder, outbound_enabled=lambda: True,
    )
    approval_id = queue.list_pending(conn)[0]["id"]
    assert len(_mirror(chain_path)) == 1  # 首次拦截那一条，先钉住基线

    decision = queue.approve(
        conn,
        approval_id,
        confirmed_by="张三",
        outbound_enabled=lambda: False,
        deliver=lambda m: pytest.fail("总开关关闭时 ⛔ 不得投递"),
        recorder=recorder,
    )
    conn.commit()

    assert decision.allowed is False
    assert decision.reason == REASON_OUTBOUND_DISABLED
    assert channel.delivered == []

    lines = _mirror(chain_path)
    assert len(lines) == 2, f"放行被拦下必须新增一条痕，实得 {len(lines)} 行：{lines}"
    assert [l["event_type"] for l in lines] == [OUTBOUND_BLOCKED, OUTBOUND_BLOCKED]
    assert lines[1]["blocked_reason"] == REASON_OUTBOUND_DISABLED
    assert lines[0]["blocked_reason"] == first.reason
    assert first.reason != REASON_OUTBOUND_DISABLED
    # spec 逐字：两条记录各自可按 id 分别被检索到
    assert lines[0]["id"] != lines[1]["id"]
    # ⛔ 除留痕外一个状态都不许动（design D5）
    assert queue.get(conn, approval_id)["status"] == "pending"
    assert len(queue.list_pending(conn)) == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest tests/test_outbound_end_to_end.py -q -k "closed_switch_leaves_its_own_trail"`
Expected: FAIL — `TypeError: approve() got an unexpected keyword argument 'recorder'`

- [ ] **Step 3: 实现——`TYPE_CHECKING` 与签名**

`app/outbound/queue.py` 的 `TYPE_CHECKING` 块改为：

```python
if TYPE_CHECKING:
    from app.audit.recorder import AuditRecorder
    from app.outbound.gate import GateDecision
```

⛔ **模块顶部不许新增任何运行时 import**（design.md 决策 2：`app/graph/nodes.py` 顶部已有 `from app.outbound import queue`，`queue.py` 顶部再 import `nodes` 或 `delivery` 就成环）。

`approve()` 签名改为：

```python
def approve(
    conn: sqlite3.Connection,
    approval_id_: str,
    *,
    confirmed_by: str,
    outbound_enabled: Callable[[], bool],
    deliver: Callable[[CandidateOutboundMessage], None],
    recorder: "AuditRecorder",
) -> "GateDecision":
```

- [ ] **Step 4: 实现——被拦分支留痕**

`if not decision.allowed:` 分支改为：

```python
    if not decision.allowed:
        # TD-9：这里以前是光秃秃的 `return decision`，于是「人工点了放行、却被
        # 总开关拦下」这一次尝试**一条痕都不产生**——审计分辨不出"从未尝试放行"
        # 与"尝试过但被拦下"，而 6.5 的拦截统计会让"一直发不出去的那批信"
        # 系统性缺席。
        #
        # ⚠️ **函数体内延迟 import，⛔ 不许提到模块顶部**（design.md 决策 2）：
        # `app/graph/nodes.py` 顶部已有 `from app.outbound import queue`，方向不能
        # 反过来；模块顶部再 import delivery 会构成 queue → delivery → nodes → queue
        # 的模块级循环导入。函数体内的 import 在两个模块都完成初始化之后才执行。
        from app.outbound.delivery import record_outbound_decision

        record_outbound_decision(
            conn,
            thread_id=row["thread_id"],
            message=signed,
            decision=decision,
            recorder=recorder,
        )
        return decision
```

⛔ **这一分支除上面这段之外一行都不许改**：不加 `enqueue`、不改 CAS 之前的提前返回、不改返回值（design D5 死锁防线，Global Constraint 5）。

- [ ] **Step 5: 订正 `approve()` 的事务 docstring（⛔ 别跳过）**

把 docstring 里这一句：

```
    ⛔ 不自行 `commit`：调用方（`effect_*` 或测试）负责事务边界。
```

替换为：

```
    ⛔ 本函数自身不 `commit`。⚠️ 但被拦分支调用的 `record_outbound_decision()`
    内部会走 `effect_record_outbound_audit`，而 `idempotent_effect` 装饰器在函数体
    成功后 `conn.commit()`（`app/storage/idempotency.py:75`）——这与
    `deliver_candidate_message` 的既有行为同构，不是新增的事务管理者。
    这条路径上此前只做过读（`get()` / `compute_outbound_gate()`），`mark_resolved`
    的 CAS 在早返回之后根本没执行，没有半截事务会被这次 commit 意外提交。
```

- [ ] **Step 6: 给所有既有调用点补 `recorder`**

① `tests/test_outbound_queue.py` 的 `conn` fixture 目前只给了连接。新增一个 fixture（放在 `_approve` 之前）：

```python
@pytest.fixture
def recorder(conn, tmp_path):
    """approve() 的留痕依赖（TD-9）。⛔ 不给 recorder 造 stub——真的 sink 才能
    让"被拦时留没留痕"这件事在别处（e2e）被数出来。"""
    from app.audit.recorder import AuditRecorder
    from app.audit.sinks import JsonlChainSink, SqliteSink

    return AuditRecorder(SqliteSink(conn), JsonlChainSink(tmp_path / "decisions.jsonl"))
```

`_approve()` helper 改为：

```python
def _approve(conn, approval_id, *, switch, delivered, recorder, confirmed_by="张三"):
    from app.outbound import queue as q

    return q.approve(
        conn,
        approval_id,
        confirmed_by=confirmed_by,
        outbound_enabled=lambda: switch,
        deliver=delivered.append,
        recorder=recorder,
    )
```

所有调 `_approve(...)` 的测试函数在参数表里加上 `recorder` fixture，并把 `recorder=recorder` 透传。涉及的测试（按当前行号）：
`test_approving_with_the_switch_on_delivers_and_marks_approved`(205)、
`test_losing_the_resolve_race_does_not_deliver_a_second_time`(251)、
`test_approving_with_the_switch_off_does_not_deliver_and_does_not_requeue`(292)、
`test_the_switch_off_path_never_calls_enqueue`(321)、
`test_a_draft_blocked_by_the_switch_can_be_approved_again_later`(350)、
`test_a_malformed_draft_is_not_delivered_even_with_a_signature`(366)、
`test_approving_an_unknown_or_already_resolved_id_raises`(389)。

② `test_the_row_is_already_approved_at_the_moment_deliver_runs`(223) 直接调 `queue.approve(...)`，同样加 `recorder=recorder` 并把 fixture 加进参数表。

③ `tests/test_outbound_end_to_end.py:108-121` 的 `queue.approve(...)` 加一行 `recorder=recorder,`；其 `deliver=lambda m: deliver_candidate_message(...)` 闭包内部**已经**传了 `recorder=recorder`，⛔ 不需要再改。

⛔ **除补参数外，这些既有测试的断言一行都不许改。**

- [ ] **Step 7: 跑测试确认通过（含 2.1 / 2.2 的两条死锁防线）**

Run: `venv/bin/python -m pytest tests/test_outbound_end_to_end.py -q -k "closed_switch_leaves_its_own_trail"`
Expected: PASS

Run: `venv/bin/python -m pytest tests/test_outbound_queue.py -q`
Expected: PASS —— 🔴 其中 `test_the_approve_path_contains_no_enqueue_call`（AST 结构守护）与 `test_the_switch_off_path_never_calls_enqueue`（行为级 spy）必须**原样通过**（tasks 2.2）。
⚠️ 若 AST 守护变红，说明有人在被拦分支里加了 `enqueue` 或 `INSERT` —— ⛔ 不许改这条测试来"适配"，改回实现。

Run: `venv/bin/python -m pytest tests/test_outbound_gate.py tests/test_outbound_gate_structure.py -q`
Expected: PASS（tasks 2.1：本变更 ⛔ 不改 `app/outbound/gate.py`，此项只确认没有意外扰动；四条 2026-08-28 口径锁定用例必须全绿）

Run: `git diff --stat app/outbound/gate.py`
Expected: 无输出

- [ ] **Step 8: 提交**

```bash
git add app/outbound/queue.py tests/test_outbound_queue.py tests/test_outbound_end_to_end.py
git commit -m "fix(outbound): approve() 被总开关拦下时也留痕（1.3/1.4/1.6/1.7）"
```

---

### Task 4: 同一次放行尝试重放两次只留一条痕

**Files:**
- Test: `tests/test_outbound_end_to_end.py`（新增一条）

**Interfaces:**
- Consumes: Task 3 的 `queue.approve(..., recorder=...)`

> 这是 spec Scenario 最后半句「也不因流程重放同一次放行尝试而重复产生第三条」的回归，也是 5.4「同一原因重放不重复留痕」**原意不变**的证明。⛔ 本 Task 不改任何生产代码——如果需要改代码才能让它绿，说明 Task 1 的归一化写错了，回 Task 1 修。

- [ ] **Step 1: 写测试**

追加到 `tests/test_outbound_end_to_end.py`：

```python
def test_replaying_the_same_blocked_approval_leaves_no_second_trail(wired):
    """
    tasks 1.8 / spec Scenario 末句：同一次"放行被总开关拦下"的尝试被重放
    （LangGraph 恢复时节点从头整个重跑，铁律 1），只产生一条留痕、不产生第二条。

    ⚠️ 这条与 test_approving_into_a_closed_switch_leaves_its_own_trail 是一对：
    那条证明"不同原因 → 两条痕"，这条证明"同一原因重放 → 仍是一条"。少了这条，
    Task 1 把 reason 并进幂等键的改动就可能滑向"每次调用都写一行"的另一个极端。

    🔴 判据是 **JSONL 镜像行数**（Global Constraint 6）。⛔ 不许改成断言 effect_log
    计数——重放时 idempotent_effect 返回 None、函数体根本没跑，effect_log 恒为一条，
    对"镜像被写重了没有"零分辨力（U5 终审踩过的假绿）。

    ⚠️ 断言的量与构造的量刻意来自不同的源：构造侧重放 REPLAYS 次，断言侧是常量 2
    （首次拦截 1 条 + 放行被拦 1 条），2 来自领域规则不是从 REPLAYS 推的。
    ⛔ 不许写成任何由 REPLAYS 导出的值。
    """
    from collections import Counter

    from app.outbound.gate import REASON_OUTBOUND_DISABLED

    conn, chain_path, recorder, channel = wired
    deliver_candidate_message(
        conn, thread_id="job-7", message=_msg(), channel=channel,
        recorder=recorder, outbound_enabled=lambda: True,
    )
    approval_id = queue.list_pending(conn)[0]["id"]

    REPLAYS = 3
    assert REPLAYS > 1, "重放次数必须 > 1，否则这条测试什么也没测"
    for _ in range(REPLAYS):
        decision = queue.approve(
            conn,
            approval_id,
            confirmed_by="张三",
            outbound_enabled=lambda: False,
            deliver=lambda m: pytest.fail("总开关关闭时 ⛔ 不得投递"),
            recorder=recorder,
        )
        assert decision.reason == REASON_OUTBOUND_DISABLED
    conn.commit()

    lines = _mirror(chain_path)
    assert len(lines) == 2, f"预期镜像恰好 2 行（首次拦截 + 放行被拦），实得 {len(lines)} 行"
    by_id = Counter(line["id"] for line in lines)
    assert set(by_id.values()) == {1}, (
        f"同一个 event.id 被写了多次，外发留痕的唯一真源被腐蚀：{list(by_id.items())}"
    )
    switch_lines = [l for l in lines if l["blocked_reason"] == REASON_OUTBOUND_DISABLED]
    assert len(switch_lines) == 1
    # 队列状态在整个重放过程中一动不动
    assert queue.get(conn, approval_id)["status"] == "pending"
```

- [ ] **Step 2: 跑测试**

Run: `venv/bin/python -m pytest tests/test_outbound_end_to_end.py -q -k "replaying_the_same_blocked_approval"`
Expected: PASS（Task 1 的归一化 + `idempotent_effect` 既有短路已经保证了它；本 Task ⛔ 不改生产代码）

⚠️ **若它 FAIL**：不要在这里打补丁。行数 > 2 说明 reason 归一化不稳定（同一次尝试两次求值出了不同的 `reason` 字符串），回 Task 1 查 `audit_business_key`；行数 < 2 说明 Task 3 的留痕没生效，回 Task 3。

- [ ] **Step 3: 提交**

```bash
git add tests/test_outbound_end_to_end.py
git commit -m "test(outbound): 同一次放行被拦的重放只留一条痕（1.8）"
```

---

### Task 5: 6.5 拦截统计能看见第二次拦截

**Files:**
- Test: `tests/test_outbound_block_stats.py`（新增一条集成用例）
- ⛔ 不改 `app/audit/assertions.py`（proposal Non-goals 逐字：洞补上后统计函数按现有逻辑遍历镜像即可看见，**不需要改统计代码**）

**Interfaces:**
- Consumes: Task 3 的 `queue.approve(..., recorder=...)`、既有 `app.audit.assertions.outbound_block_stats(mirror: AuditSink) -> OutboundBlockStats`

> 🔴 **这是 TD-9 的可观测性目标本身**（Global Constraint 7）：不是"顺带验证一下"，而是这次修复到底解决了什么问题的唯一直接证据。`tests/test_outbound_block_stats.py` 现有用例全是**手工投喂事件**给 mirror，证明不了"真实外发路径产出的事件能被统计看见"——本条是这个文件里第一条走真实路径的集成用例。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_outbound_block_stats.py`：

```python
def test_a_second_block_on_the_same_draft_gets_its_own_bucket(tmp_path):
    """
    ⭐ tasks 2.4 / TD-9 的可观测性目标本身。

    TD-9 描述的"系统性缺席"：同一封拒信被拦两次（首次入队、放行时被总开关拦下），
    6.5 的 blocked_by_type_and_reason 里只能看到**第一次**的那个 reason 桶——
    第二次被幂等机制吞掉，而"一直发不出去的那批信"恰恰是最该被看见的那批。

    ⚠️ 与本文件其余用例的分工：那些手工投喂事件给 mirror，证明的是统计函数的
    分桶逻辑；这条走**真实外发路径**（deliver_candidate_message → queue.approve），
    证明的是真实路径产出的事件能被统计看见。两者不互相替代。

    ⛔ 本用例不改 app/audit/assertions.py：统计函数按现有逻辑遍历镜像即可看见
    （proposal Non-goals 逐字）。若需要改统计代码才能绿，说明留痕这一侧还没修对。
    """
    from app.audit.recorder import AuditRecorder
    from app.audit.sinks import JsonlChainSink, SqliteSink
    from app.outbound import queue
    from app.outbound.delivery import deliver_candidate_message
    from app.outbound.gate import REASON_OUTBOUND_DISABLED
    from app.outbound.messages import CandidateOutboundMessage
    from app.storage.db import get_connection, init_schema

    AI_BODY = (
        "【AI 生成】本文案由系统基于岗位画像自动生成，生成时间 2026-08-28。很遗憾……"
    )

    class SpyChannel:
        def __init__(self):
            self.delivered = []

        def deliver(self, thread_id, message):
            self.delivered.append((thread_id, message))

        def latest(self, thread_id):
            return None

    conn = get_connection(str(tmp_path / "stats.db"))
    init_schema(conn)
    mirror_sink = JsonlChainSink(tmp_path / "decisions.jsonl")
    recorder = AuditRecorder(SqliteSink(conn), mirror_sink)
    channel = SpyChannel()
    message = CandidateOutboundMessage(
        message_type="rejection_letter", recipient="cand-9@example.com", body=AI_BODY
    )

    first = deliver_candidate_message(
        conn, thread_id="job-7", message=message, channel=channel,
        recorder=recorder, outbound_enabled=lambda: True,
    )
    approval_id = queue.list_pending(conn)[0]["id"]
    queue.approve(
        conn,
        approval_id,
        confirmed_by="张三",
        outbound_enabled=lambda: False,
        deliver=lambda m: None,
        recorder=recorder,
    )
    conn.commit()

    stats = outbound_block_stats(mirror_sink)
    buckets = stats.blocked_by_type_and_reason["rejection_letter"]

    assert buckets == {first.reason: 1, REASON_OUTBOUND_DISABLED: 1}, (
        f"两次不同原因的拦截应各占一个桶，实得 {buckets}——TD-9 的'系统性缺席'还在"
    )
    assert stats.blocked_by_type["rejection_letter"] == 2
    assert stats.blocked_by_reason[REASON_OUTBOUND_DISABLED] == 1
    assert stats.always_blocked_types == ("rejection_letter",)
    assert channel.delivered == []
```

⚠️ `outbound_block_stats` 在该文件顶部**已经** import（`from app.audit.assertions import (...)`），⛔ 不要重复 import。若顶部导入清单里没有它，把它加进那个既有的 import 块，⛔ 不新起一行 `import`。

- [ ] **Step 2: 跑测试确认它测的是真东西**

Run: `venv/bin/python -m pytest tests/test_outbound_block_stats.py -q -k "second_block_on_the_same_draft"`
Expected: PASS（Task 1 + Task 3 已经把洞补上）

🔴 **必须做的反证（⛔ 别跳过，否则这条用例可能恒真）**：临时把 `app/outbound/delivery.py:audit_business_key` 的返回值改回旧公式
`return f"{content_hash}:{decision.allowed}"`，重跑本用例，**必须 FAIL**（实得 `{first.reason: 1}`，只有一个桶）。确认变红后 ⛔ **立刻改回来**并重跑确认变绿。
⚠️ 这一步的临时修改 ⛔ 绝不允许出现在任何 commit 里——提交前 `git diff app/outbound/delivery.py` 确认公式是三段式。

- [ ] **Step 3: 提交**

```bash
git add tests/test_outbound_block_stats.py
git commit -m "test(audit): 6.5 拦截统计能看见同一草稿的第二次拦截（2.4）"
```

---

### Task 6: 契约同步、TD-9 销账与归档

**Files:**
- Modify: `openspec/changes/ai-audit-trail-and-outbound-gate/tasks.md`（**仅 5.4 那一行**）
- Modify: `docs/tech-debt.md`（**仅 TD-9 条目末尾追加**）
- Modify: `openspec/changes/outbound-retry-audit-trace/tasks.md`（回勾 15 项）

- [ ] **Step 1: 全量回归（2.3 的收口）**

Run: `venv/bin/python -m pytest -q`
Expected: PASS，零 failed、零 error。记下总数，写进 Task 6 的提交信息。
⚠️ 若有非本单元造成的失败（别的泳道并行改了别处），**登记后继续**，⛔ 不要顺手修别人的文件。

- [ ] **Step 2: 订正 ai-audit 包 5.4 的字面公式（2.5）**

`openspec/changes/ai-audit-trail-and-outbound-gate/tasks.md` 第 274 行整行替换为：

```markdown
- [x] 5.4 `effect_record_outbound_audit`：沿用 `idempotent_effect`。**幂等策略**：`business_key` = `{content_hash}:{allowed}:{reason}`，同一草稿的"拦截"与"放行"各留一条痕、**不同原因的两次拦截也各留一条**、重放不重复留痕。⚠️ 公式经变更包 `outbound-retry-audit-trace`（TD-9）订正，原为 `{content_hash}:{allowed}`——只到 `{allowed}` 时"首次被拦"与"放行时被总开关拦下"两次的键逐字相同（`content_hash` 不含 `confirmed_by`），第二次尝试一条痕都不产生。
```

⛔ **除这一行外，那份 `tasks.md` 的其他内容一个字都不许改**（proposal 与 tasks 2.5 逐字）。
⛔ **不要动它顶部的进度行、不要把它归档**——归档是 0903Q 那条泳道的事（Global Constraint 8）。

验证：`git diff --stat openspec/changes/ai-audit-trail-and-outbound-gate/tasks.md`
Expected: `1 file changed, 1 insertion(+), 1 deletion(-)`

- [ ] **Step 3: TD-9 销账（2.6）**

在 `docs/tech-debt.md` 的 TD-9 条目**末尾**（下一条技术债标题之前）追加：

```markdown
**销账（2026-09-04）**：已于变更包 `outbound-retry-audit-trace` 修复，落码 commit
`<Task 1 hash>`（幂等键并入 reason）、`<Task 2 hash>`（留痕提炼为公共函数）、
`<Task 3 hash>`（`approve()` 被拦时留痕）。回归：`tests/test_outbound_end_to_end.py::
test_approving_into_a_closed_switch_leaves_its_own_trail`（不同原因两条痕）、
`::test_replaying_the_same_blocked_approval_leaves_no_second_trail`（同原因重放仍一条）、
`tests/test_outbound_block_stats.py::test_a_second_block_on_the_same_draft_gets_its_own_bucket`
（6.5 统计能看见第二次拦截）。
⚠️ 上面的成因分析 ⛔ 保留原文，不改写——它是"为什么当时只登记不修"的历史记录。
```

`<Task N hash>` 用 `git log --oneline -8` 取实际短 hash 填入，⛔ 不许留占位符。
⛔ **不删除、不改写 TD-9 原文的任何一句**（tasks 2.6 逐字）。

- [ ] **Step 4: 回勾本包 WBS**

把 `openspec/changes/outbound-retry-audit-trace/tasks.md` 的 1.1–1.8、2.1–2.7 共 15 项 `- [ ]` 改为 `- [x]`。

⚠️ **落地偏离登记**：在该文件末尾追加一节 `## 2.x 落地偏离登记`，逐条写明实施相对文件字面的偏离。**至少包含这三条**（实施时若有新的，一并补上，⛔ 不要合并成一句"有少量调整"）：

| # | 文件字面 | 实际落地 | 方向 / 理由 |
|---|---|---|---|
| 1 | 1.7 写"原因『等待人工确认』" | 首次拦截原因实为 `REASON_CONFIRMATION_REQUIRED`「消息自称需要人工确认」 | **落到实际取值**。`requires_confirmation` 默认 `True`（红线要求），`gate.py:284` 先命中该条；`REASON_AWAITING_CONFIRMATION` 只在 `requires_confirmation=False` 且非最高风险时出现。测试一律用 `decision.reason` / `REASON_*` 常量，⛔ 不硬编码文案 |
| 2 | 1.1 只说"两处必须引用同一个求值表达式" | 提炼成模块级公共函数 `audit_business_key()`，**三个**调用点（`_audit_event` / `deliver_candidate_message` / `record_outbound_decision`）共用 | **更严**。1.1 允许"模块内小函数或共享表达式"，取前者；`grep -rn ':{decision.allowed}"' app/` 零命中可机器验证 |
| 3 | 1.4 只说"插入调用" | 同时订正了 `approve()` docstring 里「⛔ 不自行 commit」那一句 | **信息不丢**。被拦分支现在经 `idempotent_effect` 装饰器 commit（`app/storage/idempotency.py:75`），与投递路径同构、无半截事务风险，但旧 docstring 会让下一个人推出错误的事务模型 |

- [ ] **Step 5: 提交**

```bash
git add openspec/changes/ai-audit-trail-and-outbound-gate/tasks.md docs/tech-debt.md openspec/changes/outbound-retry-audit-trace/tasks.md
git commit -m "docs(outbound): 5.4 公式订正、TD-9 销账、本包 WBS 15/15 收口（2.5/2.6）"
```

- [ ] **Step 6: 当场归档本包（2.7）**

用 `openspec-archive-change` 技能归档 `outbound-retry-audit-trace`（`CLAUDE.md`「归档时限」：⛔ 不得让"代码完成但变更包未归档"跨越一个工作 session）。

⛔ **只归档本包。** ⛔ 不要顺手归档 `ai-audit-trail-and-outbound-gate`——它的 `tasks.md` 顶部写明"归档交 0903Q（等 5.4 公式订正）"，本单元只负责把 5.4 订正掉。

归档产生的改动单独提交：

```bash
git add openspec/
git commit -m "chore(openspec): 归档 outbound-retry-audit-trace（2.7）"
```

- [ ] **Step 7: 收工反查**

```bash
venv/bin/python -m pytest -q
git log --oneline -8
git status
```
Expected: 全量绿；6 条本单元 commit 都在；`git status` 里没有本单元遗留的未提交改动（别人的改动出现是正常的，⛔ 不要顺手提交）。

---

## Self-Review（写完计划后自查，2026-09-04）

**1. spec 覆盖：** spec 只有一条 MODIFIED Requirement，其正文第 2 段与 4 条 Scenario 已在上方「Task 数与 spec 覆盖」表逐条指到 Task。**无缺口。**

**2. 占位符扫描：** 计划内唯一的待填值是 Task 6 Step 3 的 `<Task N hash>`——它是**执行期才存在**的 commit 短 hash，已写明取法（`git log --oneline -8`）与"⛔ 不许留占位符"。其余无 TBD / TODO / "适当处理错误"。

**3. 类型与签名一致性：**
- `audit_business_key(content_hash: str, decision: GateDecision) -> str` —— Task 1 定义，Task 1/2 使用，命名前后一致。
- `record_outbound_decision(conn, *, thread_id, message, decision, recorder) -> None` —— Task 2 定义，Task 3 使用，与 design.md 决策 2 逐字一致。
- `queue.approve(..., recorder: "AuditRecorder")` —— Task 3 定义，Task 3/4/5 使用，一律关键字传参、无默认值。
- 测试 helper `_msg` / `_mirror` / `_mirror_lines` / `wired` / `conn` 全部沿用各文件既有定义，⛔ 无重定义。

**4. 铁律 1（副作用独占节点 + 幂等键）：** 本单元不新增任何 `effect_*` 节点；唯一的副作用动作仍是既有的 `effect_record_outbound_audit`，幂等键仍是 `{thread_id}:{node_name}:{business_key}`，只是 `business_key` 的拼接公式变细。`effect_log` 条数与镜像行数的恒等关系由 Task 4 的重放测试覆盖。

**5. 与 SKILL 第 6 步「端到端提取验证」的偏离（登记）：**
`spec-to-plan` 的标准动作是"把计划里全部代码块提取到临时目录、装独立 venv 跑全量"。**本计划不适用**——它 100% 是对既有仓库文件的增量修改（改函数体、加参数、改 docstring），代码块脱离 `app/` 与 `tests/` 的既有上下文无法独立运行，提取出来只会得到一堆 ImportError。

**替代验证已实跑（2026-09-04，`venv/bin/python`），三项都通过：**
1. **基线**：`tests/test_outbound_{delivery,queue,end_to_end,block_stats}.py` → 44 passed。计划里所有"预期 FAIL / PASS"都以此为准。
2. **缺陷复现**：写脚本在 main 上跑出 TD-9 的两条成因（镜像行数 1 而非 2；`approve()` 被拦时行数 1 → 1），实测输出已抄进上方「开工前必读」。Task 1 / Task 3 的失败测试**就是**这个复现的用例化，⛔ 不是凭空设计的。
3. **计划所依赖的既有事实逐条核对过**：`idempotent_effect` 确实在函数体成功后 `conn.commit()`（`app/storage/idempotency.py:75`）；`JsonlChainSink.write()` **不按 id 去重**（`app/audit/sinks.py:284-303`），所以"不写重"完全靠调用点的 `result is None` 判据；AST 守护 `test_no_effect_function_appends_jsonl` 只扫 `effect_*` 前缀的函数，`record_outbound_decision` 不在其射程内；`test_the_approve_path_contains_no_enqueue_call` 只匹配名为 `enqueue` / `INSERT` 的调用节点，新增的 `record_outbound_decision(...)` 不会误伤。
4. **发现了 tasks.md 没提到的一处必改**：`tests/test_outbound_end_to_end.py:181-183` 硬编码了旧的两段式 `expected_id`，公式一改**必然变红**。已写进 Task 1 Step 5。这正是这一步存在的价值。
