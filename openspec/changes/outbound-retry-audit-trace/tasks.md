## 1. 幂等键改造与 approve() 留痕接线（核心修复）

- [x] 1.1 `app/outbound/delivery.py`：把 `_audit_event()` 里 `DecisionEvent.id` 的拼接公式，与 `deliver_candidate_message()` 传给 `effect_record_outbound_audit` 的 `business_key`，统一改为 `{content_hash}:{allowed}:{decision.reason or ""}`（design.md 决策 1）。两处必须引用同一个求值表达式——⛔ 不允许各写一遍字符串拼接，提炼成一个模块内小函数或共享的表达式。
- [x] 1.2 `app/outbound/delivery.py`：把 `deliver_candidate_message()` 尾部"构造 `DecisionEvent` → 调 `effect_record_outbound_audit` → 按返回值是否为 `None` 决定是否 `recorder.mirror()`"这一段（现内联于 `app/outbound/delivery.py:105-160`）提炼为公共函数 `record_outbound_decision(conn, *, thread_id, message, decision, recorder) -> None`，`deliver_candidate_message()` 自身改为调用它，不再内联重复。**幂等策略**：本任务不改变判重机制本身，仍完全依赖 `effect_record_outbound_audit` 既有的 `idempotent_effect` 装饰器；只是把已有的调用序列原样搬进一个新函数。
- [x] 1.3 `app/outbound/queue.py`：`TYPE_CHECKING` 块内新增 `from app.audit.recorder import AuditRecorder`（与已有的 `GateDecision` 并列，运行时零成本、不产生模块级依赖）；`approve()` 函数签名在 `deliver` 参数之后新增关键字参数 `recorder: AuditRecorder`。
- [x] 1.4 `app/outbound/queue.py`：`approve()` 的 `if not decision.allowed:` 分支内、`return decision` 之前，插入**函数体内**延迟导入 `from app.outbound.delivery import record_outbound_decision`（避免与 `app/graph/nodes.py` 已有的 `from app.outbound import queue` 构成模块级循环导入，见 design.md 决策 2），调用 `record_outbound_decision(conn, thread_id=row["thread_id"], message=signed, decision=decision, recorder=recorder)`。⛔ 不改这一分支的任何其他行为——不加 `enqueue`、不改 CAS 之前的提前返回、不改返回值，这是 design D5 死锁防线要求保持不变的部分。**幂等策略**：留痕写入完全交给 `record_outbound_decision` 内部的 `effect_record_outbound_audit`，判重键随 1.1 的新公式一起生效，本任务不新增任何独立的幂等判断。
- [x] 1.5 `app/graph/nodes.py`：更新 `effect_record_outbound_audit` 的 docstring，把"`business_key` = `{content_hash}:{allowed}`（tasks 5.4）"更正为新公式 `{content_hash}:{allowed}:{reason}`，并说明现在有两个调用点（`delivery.py`、`queue.py`）共用同一条公式。
- [x] 1.6 更新 `tests/test_outbound_queue.py` 里所有调用 `queue.approve(...)` 的地方（含 `_approve()` 测试 helper）传入可用的 `recorder`（`AuditRecorder(SqliteSink(conn), JsonlChainSink(tmp_path/...))` 或等价 fixture）；`tests/test_outbound_end_to_end.py::test_approving_the_queued_letter_delivers_it_and_leaves_a_second_trail` 里手写的 `deliver=lambda m: ...` 闭包同步补上 `recorder` 透传，使其继续可用。
- [x] 1.7 新增回归测试（放进 `tests/test_outbound_end_to_end.py`，与同主题的整圈测试放在一起）：一封拒信首次被拦截入队后（原因"等待人工确认"），对其调用 `queue.approve()` 且外发总开关此时关闭——断言产生**恰好一条新增**、动作类型为"被拦截"、原因为"外发总开关关闭"的留痕记录，且它与首次入队时的拦截记录在 JSONL 镜像里是**两条不同的行**（各自可按 `id` 检索到）。⚠️ 这条测试是 TD-9 现象的正面回归：先在 1.1–1.4 落地前确认它能复现失败（零留痕或与首次拦截合并成一条），再确认落地后通过。
- [x] 1.8 新增测试：对同一次"放行被总开关拦下"的尝试重放两次（模拟 LangGraph 节点从头重跑），断言只产生一条对应的留痕记录，不产生第二条——5.4"同一原因重放不重复留痕"原意的回归。

## 2. 回归、契约同步与销账

- [x] 2.1 全量跑 `tests/test_outbound_gate.py`，确认包括两条 2026-08-28 口径锁定用例（`test_unknown_recipient_is_blocked_per_the_2026_08_28_ruling`、`test_confirmed_by_clears_a_known_high_risk_block_per_d6_option_b`）在内全部原样通过——本变更不修改 `app/outbound/gate.py`，此项只为确认没有意外扰动。
- [x] 2.2 全量跑 `tests/test_outbound_queue.py`，确认两条死锁防线测试（`test_the_approve_path_contains_no_enqueue_call` 的 AST 结构守护、`test_the_switch_off_path_never_calls_enqueue` 的行为级 spy）原样通过——新增的 `recorder` 参数与 1.4 的调用不得被判定为"新增了 enqueue 调用"。
- [x] 2.3 全量跑 `tests/test_outbound_end_to_end.py` 与既有 `tests/test_audit_*.py`，确认 1.2 的提炼没有改变已有测试锁定的执行顺序与镜像行为（design.md「Risks / Trade-offs」第二条的验收点）。
- [x] 2.4 扩展 `app/audit/assertions.py` 6.5 的集成测试：构造"同一草稿先被入队拦截、再放行被总开关拦下"的完整场景，断言 `outbound_block_stats()` 的 `blocked_by_type_and_reason` 对该 `message_type` 计入两个不同的 `reason` 桶，而不是被幂等机制吞掉只剩一条——验证 TD-9 描述的"6.5 系统性缺席"确实被补上。
- [x] 2.5 同步订正 `openspec/changes/ai-audit-trail-and-outbound-gate/tasks.md` 5.4 的字面幂等键公式描述，由 `{content_hash}:{allowed}` 改为 `{content_hash}:{allowed}:{reason}`，并在该条后追加一句指向本变更包（`outbound-retry-audit-trace`）作为订正依据。⛔ 除这一处文字外不改那份 `tasks.md` 的其他内容。
- [x] 2.6 `docs/tech-debt.md` TD-9 条目补写销账状态：在条目末尾追加"已于变更包 `outbound-retry-audit-trace`（commit `<待填入实际 hash>`）修复"。⛔ 不删除 TD-9 原文的成因分析——保留作为"为什么当时只登记不修"的历史记录，只在条目末尾追加销账状态，不改写中间正文。
- [ ] 2.7 全部任务勾选后，当场执行 `openspec-archive-change`（CLAUDE.md「归档时限」：不得让"代码完成但变更包未归档"跨越一个工作 session）。

## 2.x 落地偏离登记

| # | 文件字面 | 实际落地 | 方向 / 理由 |
|---|---|---|---|
| 1 | 1.7 写"原因『等待人工确认』" | 首次拦截原因实为 `REASON_CONFIRMATION_REQUIRED`「消息自称需要人工确认」 | **落到实际取值**。`requires_confirmation` 默认 `True`（红线要求），`gate.py:284` 先命中该条；`REASON_AWAITING_CONFIRMATION` 只在 `requires_confirmation=False` 且非最高风险时出现。测试一律用 `decision.reason` / `REASON_*` 常量，⛔ 不硬编码文案 |
| 2 | 1.1 只说"两处必须引用同一个求值表达式" | 提炼成模块级公共函数 `audit_business_key()`，**两个**直接调用点（`_audit_event` / `record_outbound_decision`）共用；`deliver_candidate_message()` 不直接调用它，是经 `record_outbound_decision()` 间接受益 | **更严**。1.1 允许"模块内小函数或共享表达式"，取前者；`grep -rn ':{decision.allowed}"' app/` 零命中可机器验证 |
| 3 | 1.4 只说"插入调用" | 同时订正了 `approve()` docstring 里「⛔ 不自行 commit」那一句，**以及 `queue.py` 模块 docstring 里同义的那一句** | **信息不丢**。被拦分支现在经 `idempotent_effect` 装饰器 commit（`app/storage/idempotency.py:75`），与投递路径同构、无半截事务风险，但旧 docstring 会让下一个人推出错误的事务模型。模块 docstring 那一句是 review 第一轮查出来的 |
| 4 | 2.7 写"当场归档本包" | 归档改到**合并回 main 之后**执行，本 Task 内不归档；因此本包 checkbox 收在 **14/15**，2.7 留待归档当场勾 | **执行位置调整，终态不变**。归档会写 `openspec/specs/` 与 `openspec/changes/archive/`，在 worktree 内做会与 main 产生大面积合并冲突。泳道 opener（0903Q）【四】明确要求"合并回 main → 双验 → 回勾 → 归档" |
| 5 | plan Task 2 Step 4 点名回归 `tests/test_audit_sinks.py` | 仓库内不存在该文件，实际跑的是 `tests/test_audit_sinks_sqlite.py` | **计划文本笔误，非实施偏离**。控制器已独立核实 `ls tests/ \| grep -i sink` 只有 `test_audit_sinks_sqlite.py` |
