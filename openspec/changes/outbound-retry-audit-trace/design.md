## Context

见 proposal.md「Why」的两条成因。本节只补实现相关的现状事实，不重复动机。

**现有调用图**：`app/outbound/delivery.py:deliver_candidate_message()` 是**唯一**构造 `DecisionEvent` 并调用 `effect_record_outbound_audit` 的地方（`_audit_event()` 私有函数 + 函数体后半段的 `None`/`False` 分支镜像逻辑，`app/outbound/delivery.py:40-162`）。`app/outbound/queue.py:approve()` 是完全独立的另一个函数，对 `app.audit.*` 与 `app.graph.nodes` **零依赖**——`decision.allowed` 为假时直接 `return decision`，从未触达任何留痕代码路径。

**"最终成功放行"今天为什么有留痕**：生产里两者目前都没有调用方（TD-8），唯一的调用方式是测试手写的闭包——`tests/test_outbound_end_to_end.py::test_approving_the_queued_letter_delivers_it_and_leaves_a_second_trail` 把 `approve()` 的 `deliver` 参数绑定成 `lambda m: deliver_candidate_message(...)`。`decision.allowed=True` 那条分支会跑到 `deliver(signed)`，间接触发 `deliver_candidate_message` 的留痕逻辑；`decision.allowed=False` 那条分支在这一行之前就已经 `return`，`deliver` 从未被调用——这正是 TD-9 成因①。

**现有模块依赖方向（本设计不改变，只在其上新增一条延迟依赖）**：
- `app/graph/nodes.py` → `app/outbound/queue.py`（顶部 `from app.outbound import queue`，供 `effect_enqueue_pending_approval` 调 `queue.enqueue`）
- `app/outbound/delivery.py` → `app/graph/nodes.py`（顶部导入三个 `effect_*`）
- `app/outbound/queue.py` 现在对 `app.audit.*`、`app.graph.nodes`、`app.outbound.delivery` 均无依赖

## Goals / Non-Goals

**Goals**

- 同一草稿两次**不同原因**的拦截各产生一条独立留痕；同一原因重放仍短路、不重复留痕（5.4 原意不变，见 proposal.md）。
- `approve()` 新增的留痕调用不引入 `app.outbound.queue` ↔ `app.graph.nodes`（或 `app.outbound.delivery`）之间的**模块级**循环导入。
- "构造 `DecisionEvent` → 调 `effect_record_outbound_audit` → 按返回值是 `None` 还是其他决定是否 `mirror()`"这套逻辑全程只允许一处实现，两个调用点共用，不允许出现第二份手写副本。
- 两条既有死锁防线测试（`test_the_approve_path_contains_no_enqueue_call` 的 AST 结构守护、`test_the_switch_off_path_never_calls_enqueue` 的行为级 spy）在新增参数与新增调用之后原样通过。

**Non-Goals**

- 不重构 `app/graph/nodes.py`、`app/outbound/queue.py`、`app/outbound/delivery.py` 三者之间既有的 import 方向——本设计只新增一条"函数体内延迟 import"，不消除已经存在的耦合，也不让 `effect_enqueue_pending_approval` 改用别的方式调用 `queue.enqueue`。
- 不改变 `approve()` 对外的既有调用顺序契约：先 CAS（`mark_resolved`）后 `deliver`、抢输即抛 `ApprovalNotPending`。新增的留痕调用只插在"判定不通过、尚未触碰 `mark_resolved`"这一步，不影响放行分支的既有顺序。

## Decisions

### 决策 1：幂等键与 `DecisionEvent.id` 统一改为 `{content_hash}:{allowed}:{reason}`，`reason` 以空串归一化 `None`

**应用范围**：`app/outbound/delivery.py` 内构造 `DecisionEvent.id` 的公式，与传给 `effect_record_outbound_audit` 的 `business_key`——这两处目前各自手写同一个字符串（`_audit_event()` 里的 `id=f"{thread_id}:effect_record_outbound_audit:{content_hash}:{decision.allowed}"`，和 `deliver_candidate_message()` 里的 `business_key=f"{content_hash}:{decision.allowed}"`），必须同步改、同一条公式，本变更给 `queue.py:approve()` 新增的第二个调用点也必须用同一条公式，不允许出现第三种写法。

**归一化规则**：`decision.reason` 在 `allowed=True` 时恒为 `None`（`app/outbound/gate.py` 放行分支的构造），拼接前统一取 `decision.reason or ""`——放行事件的 key 形如 `{content_hash}:True:`（末尾空段），拦截事件形如 `{content_hash}:False:{reason 文案}`。⛔ 不为放行分支单独省略 `:{reason}` 段：两条分支必须共用同一个求值表达式，分叉的公式迟早会被改错其中一半而没人发现（`app/graph/nodes.py` 与 `app/outbound/delivery.py` 里已有大量"两处必须一致"埋雷的历史教训，如 5.5 的口径与 U6 的 6.x 偏离登记）。

**替代方案与否决理由**：

- 用递增序号代替 `reason`（如 `{content_hash}:{allowed}:{n}`）——否决。序号需要"先查当前最大序号再 +1"，这是一次额外的库读取；而 `idempotent_effect` 判重靠的是同一个事务里 `effect_key` 是否已存在，不依赖任何额外查询。往判重键里塞一个需要先读库才能确定的值，会在两个并发 `approve()` 之间制造新的竞态窗口——`content_hash`/`allowed`/`reason` 三者都能在判定阶段（`compute_outbound_gate` 返回时）直接拿到，不需要多一次读。
- 用完整 `evidence` 字典的哈希代替 `reason`——否决，过度设计。`reason` 已经是从 `gate.py` 的 `REASON_*` 枚举取值的稳定短语，两条 spec Scenario（"等待人工确认" vs "外发总开关关闭"）要求区分的粒度正好是 reason 这一级，不需要更细。

### 决策 2：`queue.approve()` 新增 `recorder: AuditRecorder` 参数；留痕逻辑提炼为公共函数，通过函数体内延迟 import 调用，规避模块级循环依赖

**为什么不能直接在 `queue.py` 顶部 import**：`app/graph/nodes.py` 顶部已有 `from app.outbound import queue`（服务于 `effect_enqueue_pending_approval`）。这条边已经存在且方向不能反过来——若 `queue.py` 在模块顶部加 `from app.graph.nodes import effect_record_outbound_audit`，会构成 `queue → nodes → queue` 的模块级循环导入。

**解决方式**：

1. `recorder: AuditRecorder` 的类型标注放进 `queue.py` 现有的 `TYPE_CHECKING` 块，与已经用同样方式引入的 `GateDecision` 并列。`app/audit/recorder.py` 本身只依赖 `app.audit.events` / `app.audit.sinks`，对 `app.outbound` / `app.graph` 零依赖，放在 `TYPE_CHECKING` 之外本来也不会成环，这里只是沿用 `queue.py` 已有的风格（类型标注用 `TYPE_CHECKING`，运行时真正调用的东西才走真实 import）。
2. 真正在运行时被调用的 `effect_record_outbound_audit`，不出现在 `queue.py` 模块顶部，而是**提炼进 `delivery.py` 的一个公共函数**（建议命名 `record_outbound_decision(conn, *, thread_id, message, decision, recorder) -> None`，留在 `delivery.py`——那里已经是这套留痕逻辑事实上的归属地，不新开文件），把 `_audit_event()` 构造事件、调用 `effect_record_outbound_audit`、按返回值是否为 `None` 决定是否 `recorder.mirror()` 这一整段（现在内联在 `deliver_candidate_message()` 尾部，`app/outbound/delivery.py:105-160`）原样搬进这个新函数。`deliver_candidate_message()` 自身改为调用它，不再内联重复。`queue.py:approve()` 的被拦分支通过**函数体内**（不是模块顶部）`from app.outbound.delivery import record_outbound_decision` 延迟导入并调用同一个函数。
3. 这条延迟 import 同样不成环：`delivery.py → nodes.py` 已经存在，`queue.py` 现在只是多了一条"函数体内、调用期才发生"的 `delivery.py` 依赖——不在模块加载期发生，循环导入的风险只存在于模块加载阶段，函数体内的 import 在两个模块都已完成初始化之后才执行，这是 Python 里断开这类循环最小侵入的标准做法。不需要挪动 `effect_record_outbound_audit` 的定义位置，也不需要改 `nodes.py` → `queue.py` 的既有方向。

**为什么把留痕逻辑提炼成公共函数而不是在 `queue.py` 里重新手写一份**：`delivery.py:117-142` 那一大段注释是两轮 review 才收敛出的非显然结论——`effect_record_outbound_audit` 返回 `None` 表示"重放，effect_log 已有记录、函数体没有真的执行"，返回 `False` 表示"函数体真的执行了，只是这个事件类型在 SqliteSink 里没有真身"，两者都不能望文生义地当"布尔假"处理，处理反了会让镜像被写重、腐蚀 hash-chain 的唯一真源。重新在 `queue.py` 里手写第二份，等于给这条不变式开一个可能读漏、写歪的第二入口；提炼成一个函数、两个调用点共用，是本变更能提供的最强保证。

**`approve()` 新签名与改动点**：在现有 `confirmed_by` / `outbound_enabled` / `deliver` 三个关键字参数之后追加 `recorder: AuditRecorder`（同样是关键字参数，与现有风格一致）。被拦分支（`if not decision.allowed:` 内，`return decision` 之前）插入一行 `record_outbound_decision(conn, thread_id=row["thread_id"], message=signed, decision=decision, recorder=recorder)`。`thread_id` 不需要新增参数——`row = get(conn, approval_id_)` 已经带着 `thread_id` 列。⛔ 这一分支不改动任何其他行为：不加 `enqueue`、不改 `mark_resolved` 之前的提前返回、不改返回值本身，这正是 design D5 死锁防线要求保持不变的部分。

## Risks / Trade-offs

- [风险] `queue.py` 新增对 `AuditRecorder` 类型与 `delivery.py` 的依赖，给这个此前刻意保持"零审计依赖"的模块增加概念负担。→ `AuditRecorder` 仅进 `TYPE_CHECKING`，不产生运行时依赖；对 `delivery.py` 的依赖被限定在 `approve()` 函数体内一行延迟 import，模块顶部 import 列表不变，`enqueue`/`get`/`list_pending`/`mark_resolved` 等其余函数完全不受影响。
- [风险] `record_outbound_decision` 从 `deliver_candidate_message()` 内联代码提炼出来时，如果提炼过程中不小心改变了原有的执行顺序（先分流 `effect_enqueue_pending_approval`/`effect_deliver_message`，再构造 `event`，再调 `effect_record_outbound_audit`，再判断是否 `mirror`），会静默改变现有测试锁定的行为。→ tasks.md 把"提炼前后 `tests/test_outbound_*.py` 全量回归逐字不变"列为独立验收项，不与"新增 `approve()` 留痕"这条任务合并验收，避免提炼引入的偏差被新功能的绿色测试掩盖。
- [风险] 两个调用点（`delivery.py`、`queue.py`）现在都能触发 `record_outbound_decision`，需要确认判重语义没有被削弱。→ 不引入新风险：判重仍然只靠 `effect_log` 表上 `effect_key`（`{thread_id}:{node_name}:{business_key}`）的存在性检查，加上应用级单连接对同一个 `conn` 上写操作的天然序列化，这条保证在改动前后完全一致——本变更没有改动 `idempotent_effect` 装饰器或 `effect_log` 表的任何语义（proposal.md Non-goals 已声明不碰）。
- [权衡] 选择"函数体内延迟 import"而不是重构 `nodes.py`/`queue.py` 的依赖方向使其天然无环：后者更"干净"，但会牵动 `effect_enqueue_pending_approval` 现在直接 `import queue` 调 `queue.enqueue` 的既有写法，明显超出 TD-9 的范围。按 proposal.md 已声明的 Non-goals，选择前者。

## Migration Plan

现网无调用方（TD-8：`deliver_candidate_message` 与 `queue.approve` 在生产代码里均无调用点），本次改动没有数据迁移，也没有正在运行、需要兼容的旧状态；`approve()` 的新参数、`effect_record_outbound_audit` 的新 `business_key` 公式都是纯代码签名/常量变更，落地即生效，不需要灰度或分阶段上线，也没有回滚复杂度——回滚即还原这次提交。测试全量通过后随下一次 `.51` 发版一并上线，不需要单独的发版窗口。
