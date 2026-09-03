## Why

`docs/tech-debt.md` TD-9：一封候选人拒信/邀约首次被门禁拦下入队后，人工点放行、却**又被外发总开关拦下**的那次尝试，**一条留痕都不产生**。

```
首次拦截            → ✅ 留痕 outbound_blocked
放行被总开关拦下    → ❌ 零留痕
最终成功放行        → ✅ 留痕 outbound_delivered
```

这违反 `specs/outbound-approval-gate`「外发与拦截动作强制留痕」——`系统 SHALL 对每一次外发尝试留痕，无论结果是放行还是拦截`——以及其 Scenario「总开关关闭时已确认的消息」（该 Scenario 目前只在草稿是全新的、从未被拦过时才成立；从待审批队列走 `approve()` 这条真正的生产放行路径上不成立）。

**两条成因叠加，缺一条都不足以解释，必须一起改**：

1. `app/outbound/queue.py:approve()` 在门禁判定 `decision.allowed` 为假时**早返回**，压根不调用 `deliver`，`deliver_candidate_message`（`app/outbound/delivery.py`）里那段留痕逻辑因此没有机会跑——`approve()` 走的是另一条独立路径，不经过它。
2. 即使把 ①改成留痕也会被幂等机制吞掉：`effect_record_outbound_audit` 的 `business_key = {content_hash}:{allowed}`（`ai-audit-trail-and-outbound-gate/tasks.md` 5.4 **字面规定**）只区分"拦截 vs 放行"，不区分**是哪一条拦截**。同一草稿的第二次拦截与第一次拦截键完全相同，撞上 `effect_log` 已有行，`idempotent_effect` 返回 `None`，镜像 append 被跳过（实测日志：`外发留痕已存在（重放），跳过镜像 append`）。

**为什么必须走正式变更、不能顺手改**：修复要同时改动**两份已过审的契约**——`ai-audit-trail-and-outbound-gate` tasks.md 5.4 逐字写定的幂等键公式，与该变更包 Task 2 已过审并有测试钉住的 `approve()` 函数签名（`test_the_approve_path_contains_no_enqueue_call`、`test_the_switch_off_path_never_calls_enqueue` 两条死锁防线测试必须原样通过）。这属于计划/契约层变更，且触碰合规留痕语义，2026-08-30 与 2026-09-03 的两次无人值守 session 都因此只登记不改（`tasks.md` 6.x 落地偏离登记第 6 行）。2026-09-03 Shao Peishen 裁决：TD-9 走正式 `openspec-propose`，不塞进泳道顺手改。

**影响面已界定，按实际严重程度处理**：闸门本身完好——被拦的消息确实没有发出去，`compute_outbound_gate` 的 fail-closed 判定一步没少。丢的**只是可观测性**：审计看不到"这封信被人试着放行过几次、每次为什么没成"。**现网风险 = 0**（TD-8：`deliver_candidate_message` 在生产里没有任何调用方，`queue.approve()` 同样只被测试调用，这条洞今天一次都不会被触发）。但**必须在 M2 接线之前修掉**：`app/audit/assertions.py` 的 6.5 拦截统计（`outbound_block_stats`）已于 U6（commit `e5e8e33`）落地，数据源是 JSONL 镜像——第二次拦截在镜像里一行都没有，"一直发不出去的那批信"从统计口径里系统性缺席，而这恰恰是最该被看见的那批。M2 一旦把拒信/邀约生成单元接上 `deliver_candidate_message()`（TD-8 的触发条件），观察期采到的拦截分布从第一天起就是缺的。

## What Changes

- 幂等键公式变更：`effect_record_outbound_audit` 的 `business_key` 由 `{content_hash}:{allowed}` 改为 `{content_hash}:{allowed}:{reason}`。仍满足 5.4「同一草稿的拦截与放行各留一条痕、重放不重复留痕」的原意——**同一原因**的重放键不变、照样短路；**不同原因**（如"等待人工确认" vs "外发总开关关闭"）才另起一条留痕。
- `app/outbound/queue.py:approve()` 增加 `recorder`（`AuditRecorder`）依赖：被总开关拦下时（`decision.allowed` 为假）也调用 `effect_record_outbound_audit` 留痕并触发镜像，而不是直接早返回。
- **同步订正** `openspec/changes/ai-audit-trail-and-outbound-gate/tasks.md` 5.4 的字面幂等键公式，使已过审的契约文本与本次修订后的实际实现保持一致（写成本变更包 tasks 的一条动作，⛔ 现在不单独改那份文件）。

## Capabilities

### New Capabilities

（无。本变更不引入新领域能力，只修一个已声明能力里的留痕缺口。）

### Modified Capabilities

- `outbound-approval-gate`：「外发与拦截动作强制留痕」Requirement 新增/修改 Scenario——从待审批队列 `approve()` 被总开关拦下时，SHALL 留痕、拦截原因记为"外发总开关关闭"、且与首次拦截各算一次尝试各留一条痕（而不是与首次拦截共享同一条留痕记录）。

## Non-goals（不做什么）

- **不改 `CANDIDATE_OUTBOUND_ENABLED` 默认值与读取逻辑**。开关默认关闭、按进程工作目录解析、fail-closed 语义全部保持不动（`app/outbound/delivery.py` 模块 docstring 的不可代项，改它需 Shao Peishen 本人拍板，本变更不在此范围）。
- **不改 `idempotent_effect` 装饰器与 `effect_log` 表结构**。复用现有幂等机制，只改传入 `business_key` 的字符串拼接公式。
- **不在 `effect_*` 函数体内 append JSONL**。镜像仍在调用点、装饰器 `commit` 之后触发（`delivery-units.md` §3.4 第 2/4 条跨单元约定不动）。
- **不给 `approve()` 加回入队逻辑**。design D5 的死锁防线本身不变：`approve()` 被拦时留痕，但既不 `enqueue`（已在队列里）也不改变除留痕外的任何状态；两条现有的死锁防线测试（`test_the_approve_path_contains_no_enqueue_call` 的 AST 结构守护、`test_the_switch_off_path_never_calls_enqueue` 的行为级 spy）必须原样通过。
- **不做 M2 的拒信/邀约生成单元接线**。那是 TD-8 的范围，仍待 M2 排期；本变更只保证接上之后留痕不缺行。
- **不改 6.5 拦截统计（`outbound_block_stats`）本身的实现**。洞补上后，第二次拦截会作为一条新记录出现在 JSONL 镜像里，统计函数按现有逻辑遍历镜像即可看见，不需要改统计代码。

## Impact

**修改代码**

- `app/outbound/queue.py`：`approve()` 函数签名新增 `recorder: AuditRecorder` 参数；被总开关拦下的分支从早返回改为先留痕再返回。
- `app/graph/nodes.py`：`effect_record_outbound_audit` 的调用方式不变（仍是同一个 `effect_*` 节点、同一套 `idempotent_effect` 装饰器），变化的是各调用点传入的 `business_key` 字符串本身（`app/outbound/delivery.py` 与 `approve()` 内部都要从 `{content_hash}:{allowed}` 改成 `{content_hash}:{allowed}:{reason}`，两处必须同步改，否则同一函数的两个调用点会产生两种不同粒度的幂等键）。
- `app/outbound/delivery.py`：`_audit_event()` 构造 `business_key` 与 `DecisionEvent.id` 的地方同步改用新公式（`id` 目前也是 `{thread_id}:effect_record_outbound_audit:{content_hash}:{decision.allowed}`，同样需要把 `reason` 并入，否则 `id` 与 `effect_log` 用的 `business_key` 出现两种不同粒度）。

**修改契约文档**

- `openspec/changes/ai-audit-trail-and-outbound-gate/tasks.md` 5.4：幂等键公式的字面规定同步订正为 `{content_hash}:{allowed}:{reason}`。

**测试**

- `tests/test_outbound_queue.py`：新增用例覆盖"`approve()` 被总开关拦下时产生一条独立于首次拦截的留痕"；已有两条死锁防线测试（`test_the_approve_path_contains_no_enqueue_call`、`test_the_switch_off_path_never_calls_enqueue`）与 `test_outbound_gate.py` 全量回归必须原样通过。
- `app/audit/assertions.py` 的 6.5 拦截统计需能在集成测试里看见"同一草稿两次不同原因的拦截"各计一次。

**依赖**：无新增。**外部系统**：无。

**现网风险**：0——`app/outbound/queue.py:approve()` 与 `app/outbound/delivery.py:deliver_candidate_message()` 在生产代码里均无调用方（TD-8），本变更修改的两个函数今天不会被任何生产路径触发；风险窗口是 M2 接线之后。

## 合规影响说明

- 本变更不新增任何个人信息字段、不改变外发门禁的判定逻辑或总开关语义，只修复"已被拦截的外发尝试"在审计留痕里的完整性缺口。
- 修复后，「淘汰必须有人工确认节点并留痕」这条合规红线的证据链更完整：审计能区分"从未尝试放行"与"尝试放行但被总开关拦下"这两种不同的事实，而不是把后者悄悄归零。
- 不改变数据不出境、不用历史录用结果做监督信号等既有约束。
