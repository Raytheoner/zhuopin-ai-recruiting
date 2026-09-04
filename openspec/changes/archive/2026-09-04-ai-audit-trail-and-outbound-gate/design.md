## Context

动机见 proposal.md「Why」。需求契约见 `specs/ai-decision-audit/spec.md` 与 `specs/outbound-approval-gate/spec.md`。这里只说塑造实现路径的现状与约束。

**本仓库现状**

- `app/llm/gateway.py` 已定义 `AuditHook` Protocol 与调用点，默认实现 `NoopAuditHook` 只打日志。铁律 5 的"取回响应实际 `model` 字段"已经做了（`response_model` + `system_fingerprint` 都取到并传给钩子），缺的是钩子另一端的持久化。
- `app/storage/db.py` 是 SQLite 单文件 + WAL，`init_schema()` 用 `executescript` 建表。已有 `effect_log`（幂等）与 `outbox`（消息投递）。M2 迁 Postgres。
- `app/graph/nodes.py` 已建立 `compute_*`（纯函数）/ `effect_*`（副作用 + `idempotent_effect` 装饰器）的命名与职责分离。`effect_deliver_message` 目前无条件投递。
- `app/channels/base.py` 的 `OutboundMessage` 是极简 dataclass，`Channel` 是 Protocol（`deliver` / `latest`）。
- `app/agents/jd_agent.py` 已有 `AI_LABEL_TEMPLATE` 与 `_compose_with_label()`，AI 生成标识机制已存在。
- `app/middleware/auth.py` 是鉴权空壳，`AuthContext.user_id` 恒为 `None`。

**参考来源（只读，不引用不拷贝）**

`zhuopin_platform` 的 `audit/{events,logger,sinks}.py` 与 `shared_tools/notifiers/dispatch.py`。参考的是四个设计要点与它踩过的坑，不是代码。边界如何在设计里体现，见下方「参考边界」。

**已定的两个分叉**（本次决策，非待议）

- 留痕存储：SQLite 为真身 + JSONL hash-chain 为防篡改镜像
- 待审批队列：新建独立 `pending_approval` 表，不复用 `outbox`

## Goals / Non-Goals

**Goals:**

- 铁律 3、4 从"钩子留着"变成"数据库约束强制"——`evidence_ref` 为空由 `CHECK` 拒写，不靠应用层自觉
- 门禁语义一次做对：fail-closed、两道闸、拦截也留痕。运行时拦 + CI 断言查双保险
- 平台侧已修的两个 hash-chain 绕过，本仓库一次做对，不重走弯路
- 换存储后端只改一个 sink 实现：M2 迁 Postgres 时表结构照搬，JSONL 层不变

**Non-Goals（设计层边界，业务范围见 proposal.md「Non-goals」）:**

- 不改 `idempotent_effect` 装饰器与 `effect_log` 表。新增的 `effect_*` 节点是装饰器的**使用方**，不是它的改动方
- 不改 `Channel` Protocol。门禁插在 `Channel.deliver` **之前**，通道实现完全不知道门禁存在
- 不引入 ORM、迁移框架、异步。留痕与门禁全部同步、标准库 + 现有依赖
- 不做跨进程写锁。只做进程内互斥（与平台侧同层级），多进程部署留给 M2 的 Postgres

## Decisions

### D1：SQLite 为真身，JSONL 为防篡改镜像；不一致时以 SQLite 为准，靠对账检出

**选择**：`analysis_run` / `criterion_score` / `pending_approval` 三张 SQLite 表是可查询真身；每条决策事件同时 append 一行进 JSONL 并带 `prev_hash`。

**为什么不只做 JSONL**（平台侧的做法）：合规断言"`reason_type='ai_score'` 的拒绝记录数恒为 0"要进 CI。写成一条 SQL 是 5 行，写成扫 JSONL 文件是一段解析 + 聚合代码，而且随记录增长越来越慢。`evidence_ref` 非空这条更关键——SQLite 能用 `CHECK` 在**存储层**拒写，JSONL 文件做不到，只能靠应用层校验，而应用层校验是可以被下一个开发者绕过的（直接写文件）。铁律 4 说的是"不允许写入"，要的就是这个强度。

**为什么不只做 SQLite + 行内 hash 链**：SQLite 行可被 `UPDATE` / `DELETE`。哈希链能检出内容被改，但检不出"整行连着它的链一起被重算"——有写权限的人可以从被改的那一行往后全部重算 `prev_hash`，链照样通过。append-only 文件也不是绝对不可改，但它的攻击面小得多（改文件中间一行要重写后面全部字节，且文件通常可以放在 HR 之外的备份路径 / 只追加权限的目录下），且与 SQLite 库文件是两套介质——同时改两处才能无痕，成本量级不同。**两处互为独立证据**才是这个组合的意义。

**双写故障语义**（必须明确，否则是隐性 bug）：

- 顺序：先写 SQLite（含事务提交），再 append JSONL。
- 崩溃窗口：两者之间崩溃 → SQLite 有、JSONL 缺行。这是**可接受的偏差方向**：真身完整，镜像缺证据。反之（JSONL 有、SQLite 无）会让审计查不到记录，更糟，所以顺序不能反。
- 检出：`app/audit/assertions.py` 提供对账查询（按 `analysis_run.id` 比对两侧记录集合），差集非空即报告。
- 补齐：JSONL 缺行**不允许事后插回原位**——插回会断链。补齐方式是在链尾 append 一条 `type=backfill` 的补录事件，指向缺失的 `analysis_run.id`，形成"缺过、什么时候补的"的显式记录。这比伪造一条看起来正常的历史行诚实。
- 留痕整体失败（SQLite 就没写成）：按 spec 要求，该次 AI 结果视为不可用。实现上 `AuditRecorder.record()` 抛异常，调用方不吞。

**替代方案**：双写包在一个"伪事务"里（JSONL 写失败就回滚 SQLite）。否决——文件 append 无法参与 SQLite 事务，勉强模拟只会得到一个更难推理的半成品，而偏差方向已经被顺序选择固定在安全的一侧。

### D2：`DecisionEvent` 按招聘领域自建字段，不套平台的 `scenario` / `automation_level`

平台的 `AuditEvent` 字段是采购/质量场景的建模（`scenario="SC1"`、`automation_level="L2"`、`oem_context`、`override_reason`）。招聘侧要的是 `application_id`、`job_id`、`criterion_key`、`rubric_version`、`evidence_ref`。

**关键理由**：套平台字段表就得把招聘语义塞进 `payload` 自由字典。字典里的键**没法加数据库约束**——`evidence_ref` 为空必须拒写这条就落不了地，铁律 4 直接失效。字段是一等公民还是字典键，决定了约束能不能由存储层强制。这是 proposal.md「为什么不引入跨仓库依赖」第 1 点的具体落点。

保留的共性：`AuditSink` Protocol（写入路径与存储解耦）、统一入口只暴露 `record()`（业务代码不关心后端）、`verify_chain()` 与 `verify_integrity()` 两级自检。这些是**做法**，各自重新实现一遍，形状相似但字段不同。

### D3：hash-chain 的两个已知绕过，一次做对

平台侧审计报告里修过的两条，直接写进本仓库的验收场景（见 spec「留痕不可无痕篡改」）：

1. **缺字段豁免只对第 1 行生效。** 第 2 行起缺 `prev_hash` 判为断链。否则攻击者删光全文件的 `prev_hash` 字段重写，整链会因"每行都豁免"而通过校验。
2. **校验对磁盘原始字节重算哈希。** 不做 JSON 解析后重新 `dumps` 的规范化——重排序、`ensure_ascii` 差异、空格差异都会让哈希对不上，导致明明没被改的中文记录报断链。链的定义就是"上一行落盘字节的 SHA-256"。

写入侧的两个配套细节：进程内按文件路径共享一把互斥锁（避免并发 append 行穿插）；上一条哈希游标同样按路径共享（两个指向同一文件的 sink 实例交替写不断链），缓存缺失时从磁盘末行重算而不是当成 genesis。

### D4：门禁是纯函数，副作用留在既有 `effect_*` 节点里

平台的 `Notifier.send()` 把判定、投递、入队、留痕四件事装在一个方法里。本仓库不能这么做——铁律 1（LangGraph 恢复时节点从头整个重跑）要求每个副作用独占一个节点带幂等键。

拆法：

```
compute_outbound_gate(message, outbound_enabled) -> GateDecision   # 纯函数，可重复求值
    ↓ decision.allowed == False
effect_enqueue_pending_approval(...)   # @idempotent_effect，business_key = 草稿内容哈希
    ↓ decision.allowed == True
effect_deliver_message(...)            # 已存在，不改其内部逻辑
（两条路径都）effect_record_outbound_audit(...)   # @idempotent_effect
```

`GateDecision` 携带 `allowed` / `reason` / `evidence`（判定所依据的各字段原始取值），留痕直接消费 `evidence`，不重新求值一遍——避免"判定时未知、留痕时又变成已知"的不一致。

**为什么不把留痕塞进门禁函数内部**：那样门禁就不是纯函数了，重放会重复留痕；而且铁律 2 明确 `compute_*` 无副作用。

**幂等键**（沿用现有格式，不改装饰器）：`{thread_id}:{node_name}:{business_key}`，`business_key` 取草稿内容哈希（复用 `message_business_key()` 的做法）。同一封拒信重放不会重复入队、不会重复外发。

### D5：`pending_approval` 独立建表，不复用 `outbox`

`outbox` 的现有语义是"已决定要投递的消息"，`WebChannel.latest()` 直接读它取最新一条给前端展示。被拦截的草稿语义相反——"尚未获批、可能永远不发"。

**为什么不加 `status` 字段复用**：`WebChannel.latest()` 以及未来任何读 `outbox` 的代码都必须改成带状态过滤，**漏一处就等于未审批的拒信被当成正常消息展示/发出**。这是"忘记加 WHERE 就出合规事故"的经典形状。独立表让"读错表"变成 `no such column` 级别的显性错误，而不是静默放行。

`pending_approval` 的状态机：`pending` → `approved`（放行成功）/ `abandoned`（人工放弃）。放行时不 `DELETE` 而是改状态，保留"这封拒信被谁在什么时候批的"这条审计事实。

**放行路径的死锁坑**（平台侧踩过）：队列的 `approve()` 持锁期间会带 `confirmed_by` 重走门禁；若此时总开关关闭被拦截，**不能再次入队**——它已经在队列里，重入会撞自己的锁。实现上以"是否携带 `confirmed_by`"区分首道拦截与放行复发：只有首道拦截（无 `confirmed_by`）才入队。这条写进 spec 的验收场景。

### D6：`AuditHook` Protocol 需扩参，`NoopAuditHook` 降级为测试专用

现有 `AuditHook.record()` 的签名只有 LLM 调用层的字段（model / prompt_version / input_hash / raw_response / token_usage / latency_ms），缺 rubric 快照与业务关联（`application_id` / `job_id`）。这两类信息 `LLMGateway` 自己不知道，必须由调用方传入。

做法：`extract_structured()` 增加一个可选的 `audit_context` 参数（承载 rubric 快照与业务关联），网关原样透传给钩子，不解释其内容。网关继续对业务无知，符合它现在的定位。

`NoopAuditHook` 保留但改注释定位为"测试专用"，生产装配处（`app/main.py` / `create_app()`）改为注入接 `AuditRecorder` 的实现。**注入点只有一处**，这是把"技术债"变成"已还"的最小改动面。

### D7：`operator_id` 现阶段可信度标注为低，不等 SSO

留痕表有 `operator_id`（谁批的）。鉴权是空壳（`AuthContext.user_id` 恒为 `None`），所以现阶段这个值只能由调用方传入，不可信。

**不因此推迟本变更**：留痕结构先立起来，字段先占位，SSO 落地后只是同一个字段变得可信，表结构不用改。表注释与 spec 都明确标注这一点，避免后来者误以为现在的 `operator_id` 已经可审计。部署约束 5 的另一半（可识别到人的登录）仍是 M2 处理真实简历前的阻塞项，登记在 proposal.md「Non-goals」。

### 参考边界：「参考做法、不引用代码、不加跨仓库依赖」在设计里怎么体现

理由见 proposal.md 同名小节。这里是可检查的落点：

| 边界 | 具体体现 | 怎么验证 |
|---|---|---|
| 不加跨仓库依赖 | `requirements.txt` / `pyproject.toml` 不新增任何条目；本变更所需全在标准库 + 现有依赖 | `grep -r zhuopin_platform` 在本仓库（除 venv）零命中；依赖文件 diff 为空 |
| 不跨仓库 import | `app/` 下无 `from zhuopin_platform ...`；`sys.path` 不做任何指向 OneDrive 的注入 | 同上 grep；CI 加一条禁止 import 的检查 |
| 不拷贝参考文件 | 模块名、类名、字段名均按招聘领域自建（`DecisionEvent` ≠ `AuditEvent`，`AuditRecorder` ≠ `AuditLogger`，`OutboundGate` ≠ `Notifier`）；无平台侧的 `scenario` / `automation_level` / `oem_context` / `override_reason` 字段 | 逐字比对不应存在整段相同的实现；D2 的字段表就是差异说明 |
| 参考的是做法 | 借的四个要点全部以**需求**形式落在 spec 里（append-only + hash-chain、fail-closed、`confirmed_by` 才放行、动作强制留痕），而非以代码形式落在实现里 | spec 的验收场景可独立于任何实现被测试 |
| 平台的坑不重踩 | D3 两条 hash-chain 绕过、D5 放行复发死锁，均已写成 spec 验收场景 | 对应场景有测试 |
| OneDrive 目录只读 | 本变更不产生任何对 `~/Library/CloudStorage/.../企业AI转型/` 的写入；参考行为发生在提案阶段且已完成 | 实现阶段无需再读该目录 |

**一句话**：两个仓库共享的是"append-only + hash-chain 防篡改、fail-closed 未知即拦截、confirmed_by 才放行、动作强制留痕"这四条工程判断，各自用自己领域的字段实现一遍，独立演进。

## Risks / Trade-offs

- **双写不一致（SQLite 有、JSONL 缺）** → 顺序固定为先真身后镜像，把偏差限制在安全方向；`assertions.py` 提供对账查询；补齐走链尾补录事件而非插回原位。测试覆盖"JSONL 写入抛错时 SQLite 记录仍在且可被对账发现"。
- **fail-closed 误拦：新增消息类型忘登记就被静默拦下** → 拦截也留痕，且留痕含拦截原因；提供"按类型统计拦截次数"的查询，让"某类消息一直在被拦"能被发现而不是等业务方投诉。这是刻意接受的代价：漏发一封邀约可以补，未审批发出一封拒信不能撤。
- **写入侧只有进程内锁，多进程部署会断链** → 当前部署形态是单个 Windows 计划任务拉起的单进程，假设成立。文档与代码注释显式标注该假设；M2 迁 Postgres 时由数据库承担并发写，JSONL 若仍保留需改为单写入者或按进程分文件。
- **留痕体积增长**：`raw_response` 全文入库，简历评分场景响应不小 → M1/M2 阶段量级（百级岗位、千级投递）SQLite 完全吃得住，不提前优化。JSONL 按月切分留作后续手段，本次不做。
- **`AuditHook` 扩参是破坏性签名变更** → 该 Protocol 目前只有 `NoopAuditHook` 一个实现，且只在 `LLMGateway` 内部被调用，影响面可控。新增参数设为可选，现有调用点不改也能跑。
- **`evidence_ref` 的 `CHECK` 约束在 SQLite 上的强度** → SQLite 支持 `CHECK`，但 `PRAGMA ignore_check_constraints` 可以关掉。生产连接不设该 pragma，并在 `assertions.py` 里保留"空 `evidence_ref` 计数恒为 0"的事后断言作为纵深防御。
- **`criterion_key` 白名单会拦住合法的新维度** → 白名单集中在一处定义，加维度是一行改动 + 一次 review；用它换"声学情绪信号 / 人脸表情永远进不了评分"这条红线，值得。

## Migration Plan

1. **建表**：在 `db.py` 的 `SCHEMA` 追加三张表与约束/索引。`CREATE TABLE IF NOT EXISTS` 对既有库幂等，无数据迁移——三张表都是新的，不改任何现有表。
2. **模块落地**：`app/audit/` 与 `app/outbound/` 独立新增，此时尚未接线，现有行为完全不变。可单独测试。
3. **接线一（留痕）**：生产装配处把审计钩子从 `NoopAuditHook` 换成 `AuditRecorder`。回滚 = 换回一行。
4. **接线二（门禁）**：在外发路径插入门禁节点。此时 `CANDIDATE_OUTBOUND_ENABLED` 默认**关闭**——先让门禁与队列跑一段，观察拦截留痕是否符合预期，再开开关。回滚 = 关开关（不需要改代码）。
5. **断言进 CI**：合规断言与对账查询加入测试套件。

**回滚策略**：三步都是配置或单行注入的回退，不涉及数据回滚（新表可留着不用）。门禁的回滚要注意：关闭 `CANDIDATE_OUTBOUND_ENABLED` 是"更安全"的方向（全拦），真要恢复无门禁投递必须显式移除门禁节点——**不提供"一键放行全部"的配置项**，避免它成为红线的旁路。

## Open Questions

- **JSONL 文件的存放路径与备份策略**：放 `data/audit/` 还是与数据库同目录？是否纳入 `.51` 服务器的备份范围？这个答案不影响表结构、模块划分与任务拆解，可在实现阶段按运维便利定，配置项已预留。
- **`pending_approval` 的审批时效提醒**（超过 N 天未审批是否提醒）：M1 的画像确认已有"第 1 天、第 3 天各提醒一次"的先例，本次可沿用同一机制，但提醒本身属于通知能力，不改本变更的 spec 与任务拆解。
