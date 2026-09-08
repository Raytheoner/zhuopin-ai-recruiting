# 技术债清单

> 每条必须写明**触发条件**（什么时候还）与**不还的后果**。没有触发条件的条目会永远悬着。
> 本文件是仓库级真源。变更包归档后 `openspec/changes/` 里的 tasks.md 会移走，
> 计划文件也会变旧，但这份清单留着。

## TD-1 · `job_profile` 的两列时序留痕是过渡形态

**欠的是什么**：`job_profile.turn_started_at` 与 `job_profile.llm_latency_ms`
（2026-08-19，`m1-intake-quality-fixes` 第 1 章加入）。

**触发条件（2026-08-28 订正，⚠️ 旧版本写错了）**：**`analysis_run` 里出现带
`job_id` 的行**——也就是 `audit_context` 真正接到 intake 路径之后。

> **旧版本写的是「`analysis_run` 表落地即删」，这条已于 2026-08-27（U1）满足，
> 但按它动手会丢数据。** 实测三处不成立，逐条：
>
> 1. **`turn_started_at` 在 `analysis_run` 里没有任何对应列。** 它是「用户开始等」
>    的时刻，在取数之前就打了（`app/web/server.py:96-100` 的注释逐字：「节点里打
>    会漏掉下面几次取数的时间」）；`analysis_run.created_at` 是留痕**写入**时刻，
>    在模型返回**之后**。两者不是同一个东西，替换会让"这一轮用户等了多久"从
>    可算变成不可算。
> 2. **两个 `latency_ms` 口径不同。** `job_profile.llm_latency_ms` 是**本轮累计
>    含重试**，`analysis_run.latency_ms` 是**单次尝试**——`app/llm/gateway.py:218-220`
>    的注释就写着「两个口径互不污染」。要替换必须先 `SUM(latency_ms) GROUP BY`
>    那一轮。
> 3. **而那个 GROUP BY 的键现在不存在。** U3（留痕接线）**不把 `audit_context`
>    接到 intake 路径**（要改 `app/graph/nodes.py` 与 `app/agents/intake_agent.py`，
>    超出该单元的文件边界），所以 U3 合并后 intake 侧写进 `analysis_run` 的行
>    `job_id` / `application_id` 全为 NULL，按岗位聚合无从做起。

**怎么还**：① 先有一个单元把 `audit_context`（至少含 `thread_id` / `job_id` /
`node`）接到 intake 的 LLM 调用上；② 确认 `SUM(analysis_run.latency_ms)` 能复算出
与 `job_profile.llm_latency_ms` 一致的数；③ 为「轮次开始时刻」找到落点（要么保留
`turn_started_at` 这一列不删，要么在 `analysis_run` 增列，**这一步需要决定，不能
默认删掉**）；④ 才是删列 + 删 `effect_persist_draft` 里对它们的写入 + 把统计口径
（见 `docs/superpowers/plans/2026-08-19-m1-intake-quality-fixes-unitA-storage-and-structured-questions.md`
Task 5 的分离口径 SQL）改指 `analysis_run`。

⛔ **删列本身改的是 `.51` 现网库的表结构，属生产决定，需 Shao Peishen 拍板后
另开变更包**（`delivery-units.md` §2.U3 逐字：「U3 的范围**不含删列**」）。

**现状（2026-08-28，U3 留痕接线已合并）**：`RecorderAuditHook` 已接到
`app/main.py:_gateway_factory()`，`analysis_run` 开始有真实数据；但 intake 路径
尚未传 `audit_context`，那些行的 `job_id` / `application_id` 全为 `NULL`——
**上面第 ① 步仍未完成，债未到期**。两列继续照写，时序口径以 `job_profile` 为准。

**不还的后果**：两套时序数据长期并存、互相矛盾，而没人知道该信哪一份。

**为什么当时要欠**：本批 P0/P1 的修复必须能被验证（"兜底档位是否真的减少了
空转轮、有没有把单轮延迟拖长"）。`ai-audit-trail-and-outbound-gate` 范围大得多
且尚未排期，等它意味着本批的效果只能靠感觉判断（design.md 决策 9）。

## TD-2 · `job_profile.unspecified_fields` 与 `JobProfile.unspecified_fields` 已降级为对照

**欠的是什么**：`job_profile.unspecified_fields` 这一列与 `JobProfile.unspecified_fields`
这个 pydantic 字段。2026-08-27（`m1-intake-quality-fixes` 第 6 章）起，真源是
`derived_unspecified_fields` 列，这两处只保留"模型自称了什么"的对照价值。

**触发条件**：第 8 章 8.7 的编造率/漏报率数字算完并写进 `docs/` 之后，对照数据的
使命就结束了，届时删列 + 删字段。

**不还的后果**：两个同名不同义的载体长期并存，下一个改这块代码的人有一半概率
读错真源——而读错的表现是"警示块少列了一个字段"，没有任何报错。

## TD-3 · 未溯源字段只观测、拦截策略未定

**欠的是什么**：`intake-field-grounding`（`m1-intake-quality-fixes` 第 7 章）只度量不
拦截（`design.md` 决策 12）。未溯源字段**照常写进岗位画像**，系统只把清单
（`job_profile.ungrounded_fields`）与该轮响应模型标识（`llm_response_model`）落库。
换句话说，上线后画像里仍可能有编造内容，与今天一样——区别只是它从这一批起
**可见且可数**，口径见 `docs/m1-fabrication-rate.md`。

**触发条件**：本批上线后累计 **≥ 20 场真实采集会话**，按
`docs/m1-fabrication-rate.md` 的口径拿到未溯源率分布之后，**单独开一个变更**
定拦截阈值与降级方式（退回追问 vs 记为未指定）。
这个条件是写死的，不是"有空再说"——`design.md` 决策 12 原文。

**怎么还**：新变更里定三件事：① 阈值取多少；② 命中阈值后怎么降级；
③ 降级动作对业务经理是否可见。**注意**：拦截会改变「AI 不得代替业务经理做决定」
这条红线附近的行为，属决策代理表里 Shao Peishen 本人拍板的范围。

**不还的后果**：编造率被年复一年地"观测"下去，没有任何一次真正拦住编造。
这条债的全部价值在于那个触发条件——删掉触发条件，这条就等于没登记。

**为什么当时要欠**：`deepseek-chat`（flash）的真实编造率是未知数。接近 0 时拦截
几乎无成本；像 `v4-pro` 一样是 1/3 时，直接拦截会把三分之一的字段挡在画像外，
采集直接不可用。而"模型不擅长给逐字引用"与"模型在编造"这两种情况，在没有数据
之前分不开。**先量再拦**是唯一负责任的顺序；先拦会用一次线上事故换来同一个数字。

## TD-4 · 模型返回空响应体时网关抛 TypeError 而不是走重试

**欠的是什么**：`app/llm/gateway.py` 的 `json.loads(raw_content)`，当供应商返回
`choices[0].message.content = None` 时抛 `TypeError`。该异常不在
`except (json.JSONDecodeError, ValidationError)` 元组里，**直接穿透出网关**——
调用方拿到的是一个裸 `TypeError` 而不是 `SchemaExtractionFailed`，而且这次空响应
**不消耗重试次数**，重试机制在这条路径上等于不存在。

**这不是 U3 引入的**：`json.loads` 那行与它的 `except` 元组在 U3 之前就是这样。
U3 只是在写 `RecorderAuditHook` 的空响应兜底时把它照出来了（2026-08-28
review round 1 实测复现）。

**触发条件**：`.51` 上出现第一例真实的空响应（DeepSeek 在限流或内容过滤时会
返回空 content）。目前没有观测手段——它现在的表现是一个没有上下文的 `TypeError`
堆栈，看日志的人不会知道成因。

**怎么还**：把 `TypeError` 加进那个 `except` 元组，让空响应走重试、耗尽后抛
`SchemaExtractionFailed`。⚠️ 这**改变 M1 的重试行为**（现在是立刻崩、改后是重试
两次），属可观察行为变更，要走一次 review，不能顺手改。

**不还的后果**：一次本可重试成功的空响应变成用户可见的 500，且日志里查不出成因。

**为什么当时要欠**：U3 的范围是留痕接线（`delivery-units.md:24`），改网关的重试
语义超出该边界；留痕这一侧已经处理好了（空响应照样落库，见
`app/audit/hook.py` 的 `raw_response=raw_response or ""`）。

## TD-5 · `raw_response` 逐字存，模型引用简历原文时原文会进留痕

**欠的是什么**：spec「AI 调用的可复现留痕」写的是「系统 MUST NOT 在留痕记录中
存储简历原文。**输入内容**以哈希形式记录」——约束的是**输入**。而工程铁律 3
明令**必须存原始响应**，`analysis_run.raw_response` 因此是逐字落盘的。

两条合起来留了一个口子：**评分模型把简历片段引回响应里**（而这正是
`evidence_ref` 这套设计鼓励它做的事——证据回指要人能定位到原文片段），那段原文
就进了 `analysis_run.raw_response` 与 append-only 的 JSONL 镜像，而镜像**按设计
不可删改**。

**发现经过**：2026-08-28 review round 1 指出
`tests/test_audit_end_to_end.py::test_prompt_text_is_never_stored_only_its_hash`
只在 prompt 里放了标记串，桩响应是 `{"ok": true}`，所以它证明的是输入侧，不是
"留痕里没有任何简历原文"。测试已按实际覆盖面改名。

**触发条件**：M2 开始处理真实简历、且评分 prompt 要求模型给出证据引文时。
**M1 阶段不触发**——目前没有任何评分调用。

**怎么还**：⚠️ **需 Shao Peishen 拍板，属合规红线相关的不可代项**（PIPL 与
「简历原文按其自身访问控制管理」）。可选方向：① 评分 prompt 只要求返回
offset 区间不要求引文，让 `evidence_ref` 承担定位、响应里不出现原文；
② `raw_response` 落盘前按已知的简历文本做脱敏；③ 接受并在留痕的访问控制上补齐。
⛔ 三条都不能由代理人选。

**不还的后果**：简历原文进入一份**按设计不可删改**的 append-only 文件，
候选人行使删除权时无法执行。

**为什么现在只登记不做**：M1 没有评分调用，口子还没被真正打开；而三个方向都
改变对外可观察行为或合规口径，必须由决策人本人定。

## TD-6 · `operator_id` 现阶段不可信（鉴权是空壳）

**欠的是什么**：留痕与待审批队列里的「谁批的」——`operator_id` / `confirmed_by`。
鉴权中间件按部署约束 3 只留了空壳接入点，`AuthContext.user_id` 恒为 `None`
（`design.md` D7），所以这个值现阶段**只能由调用方自己传进来，不可信**：
门禁能保证「有人签了字」，但保证不了「签字的是这个人」。

**2026-09-04 补**：`human_review.reviewer` 加入同一份清单
（`m1-job-profile-intake` tasks 6.4）。人工确认 / 修改 / 放弃三个分支的决策人
现阶段一律写 `app/middleware/auth.py` 的 `UNKNOWN_REVIEWER = "unknown:web-session"`
——⛔ 不写 NULL（分不清"没有决策人"和"这条漏写了"）、⛔ 不编人名（伪造留痕比不
留痕更糟），显式标注"身份未知"是这个阶段唯一诚实的写法。SSO 落地后 `reviewer_of()`
自动返回真实 userid，`human_review` 表结构与所有调用方**一行不改**。
⚠️ 审计断言四（`app/audit/assertions.py`）能验"有没有留痕"，**验不了"追不追得到
人"**——后者正是这条债。

**触发条件**：**M2 开始处理真实简历之前**。这是部署约束 5 的原文——
「M2 起处理真实简历前，必须具备可识别到人的登录 + 简历访问留痕」，
本变更完成的是留痕那一半，登录那一半就是这条债。⚠️ 该条件是硬门槛，
不是"有空再说"：留痕已就位而登录未就位时，M2 仍**不得**开始处理真实简历。

**怎么还**：接企微 OAuth SSO，让 `AuthContext.user_id` 有真实取值，
`operator_id` / `confirmed_by` 改为从鉴权上下文取、⛔ 不再接受调用方传值。
按部署约束 3「将来只换实现不换调用方」，表结构与调用点都不用改。
⚠️ **企微 OAuth 的对接口径要与企微侧共同决定**，不是本项目单方面能定的。

**不还的后果**：候选人拒信/邀约的人工放行留痕上写着一个名字，而**任何调用方
都能把任意名字写进去**。这条留痕是合规红线「淘汰必须有人工确认节点并留痕」
的唯一证据，不可信就等于没有——出事时既追不到人，也证明不了当时确实有人确认过。

## TD-7 · JSONL 写入侧只有进程内锁，假设单进程部署

**欠的是什么**：`JsonlChainSink` 的互斥是**进程内**的——按文件路径共享的类级
锁字典（tasks 2.3）。它只在"一个进程里的多个线程"这个前提下成立。
哈希链的正确性依赖"读上一条哈希 → append 下一条"这段是原子的，跨进程时这个
前提不存在。

**触发条件**：出现以下任一情形——① M2 迁 Postgres 时（届时要重新处理并发写与
JSONL 镜像的关系：镜像还留不留、留的话锁往哪放）；② 或在此之前 `.51` 上的部署
形态从单进程变成多进程/多 worker（当前是 Windows 计划任务拉起的单进程，
见部署约束 4）。**两条哪个先到就哪个触发。**

**怎么还**：按届时的形态二选一——迁 Postgres 后由数据库承担串行化、镜像链改由
数据库侧生成；或保留 JSONL 但把锁换成跨进程的文件锁（`msvcrt.locking` /
`fcntl.flock`，注意 `.51` 是 Windows）。

**不还的后果**：两个进程同时 append 会各自读到同一条 `prev_hash` 并写出两条
指向它的记录，链**当场断在那里**，而 `verify_chain()` 事后只能报告"第 N 行
断了"——分不清是并发写还是有人篡改。**防篡改证据链失去证明力的方式，
恰恰是它自己被写坏。** 且这个损坏不可事后修复：镜像是 append-only 的。

---

## TD-8 · 候选人外发门禁已就位，但生产里没有调用方

**欠的是什么**：`app/outbound/delivery.py:deliver_candidate_message()` 是候选人
拒信/邀约的受保护外发入口，U5 已把它连同待审批队列、两个 `effect_*` 节点与
拦截/放行留痕全部建好。**但 M1 里没有任何地方生成拒信或邀约**——2026-08-30 实测
`grep -rn "rejection_letter\|interview_invitation" app/` 在 `app/outbound/` 之外
零命中，`deliver_candidate_message` 在 `app/outbound/` 之外也零调用方（只有
`app/outbound/__init__.py` 的延迟导出）。采集图只发 `question` /
`confirmation_prompt` 这类内部通知。

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

⚠️ **本条不是"等 M2 再说"就完事**：U5 合并时 `CANDIDATE_OUTBOUND_ENABLED` 保持
默认关闭（全拦），design 迁移计划要的"观察拦截留痕是否符合预期"这个观察期，
在没有调用方之前**采不到任何样本**。观察期实际上从 M2 接线那一刻才开始计时。

---

## TD-9 · 同一草稿的第二次拦截永远不留痕（外发审计有洞）

**欠的是什么**：一封候选人信件被门禁拦下入队后，人工点放行**又被总开关拦下**的
那次尝试，**一条留痕都不会产生**。2026-08-30 实测（`worktree-audit-u5-queue-and-wiring`
全分支终审）：

```
首次拦截            → ✅ 留痕 outbound_blocked / 消息自称需要人工确认
放行被总开关拦下    → ❌ 零留痕
最终成功放行        → ✅ 留痕 outbound_delivered / confirmed_by=张三
```

**违反的是**：`specs/outbound-approval-gate` 的「外发与拦截动作强制留痕」
——`系统 SHALL 对每一次外发尝试留痕，无论结果是放行还是拦截`。
Scenario「总开关关闭时已确认的消息」要求留痕原因记为"外发总开关关闭"，
目前**只在草稿是全新的（没被拦过）时**才成立；从待审批队列走 `approve()`
这条**真正的生产路径**上不成立。

**两条成因叠加，缺一条都不足以解释**：

1. `app/outbound/queue.py:approve()` 在 `decision.allowed` 为假时**早返回**，
   压根不调用 `deliver`，于是 `deliver_candidate_message` 里那段留痕逻辑没机会跑。
2. 就算把 ① 改成无条件调用也**仍然无效**：`effect_record_outbound_audit` 的
   `business_key` = `{content_hash}:{allowed}`（`tasks.md` 5.4 **字面规定**），
   只区分"拦截 vs 放行"、不区分**是哪一条拦截**。第二次拦截的键与首次完全相同
   → 撞上 `effect_log` 已有行 → `idempotent_effect` 返回 `None` → 镜像被跳过。
   实测日志可见：`外发留痕已存在（重放），跳过镜像 append（id=…:False）`。

**影响面（已界定，别读得比实际严重）**：**闸门本身完好**——被拦的消息确实没发出去，
`compute_outbound_gate` 的 fail-closed 判定一步没少。丢的**只是可观测性**：
审计看不到"这封信被人试着放行过几次、每次为什么没成"。⛔ 但不能因此当小事：
U6 的 6.5 要按拦截原因做分布统计，这个洞会让"一直发不出去的那批信"在报表里
**系统性缺席**——恰恰是最该被看见的那批。

**怎么还**（两处一起改，只改一处无效）：

1. 幂等键改为 `{content_hash}:{allowed}:{reason}`。仍满足 5.4「同一草稿的拦截与
   放行各留一条痕、重放不重复留痕」的原意——同一原因的重放键不变、照样短路，
   不同原因才另起一条。**需同步改 `tasks.md` 5.4 的字面规定**。
2. `queue.approve()` 被拦时也要留痕。它现在没有 `recorder` 依赖，需加参数——
   **这会改动已过审的 Task 2 签名**。⚠️ 改的时候 ⛔ 不要顺手把"被拦时也入队"
   一起加回去：`approve()` 里一行 `enqueue` 都没有**是 design D5 的死锁防线本身**
   （平台侧踩过），有 `test_the_approve_path_contains_no_enqueue_call`（AST）与
   `test_the_switch_off_path_never_calls_enqueue`（行为级 spy）两条测试钉着。

**为什么现在只登记不做**：修复要同时改 `tasks.md` 5.4 字面规定的幂等键公式与
Task 2 已过审的函数签名，属计划/契约层变更，且触碰合规路径上的留痕语义。
2026-08-30 的 U5 续跑是**无人值守 session**，⛔ 不自行拍板重设计。

**现网风险 = 0**：见 [TD-8](#td-8--候选人外发门禁已就位但生产里没有调用方)，
本单元在生产里没有调用方，这条洞今天一次都不会被触发。**但它必须在 M2 接线
之前修掉**——M2 一接上，观察期采到的拦截分布就是缺的。

**2026-09-03（U6 实施，合并 commit `e5e8e33`）证实，⛔ 未修**：第 6 章 6.5 的拦截统计
（`app.audit.assertions.outbound_block_stats`）落地后与本条正面相撞。统计数据源是 JSONL
镜像，而第二次拦截**在镜像里一行都没有**——不是统计口径漏了，是留痕本身没产生。上面
预言的「一直发不出去的那批信在报表里系统性缺席」已经成立，只是现在有代码坐实了它。
U6 是无人值守 session，修复要动已过审的 `approve()` 签名与 5.4 字面规定的幂等键公式，
属**不可代**范围，故只登记。判据与影响面见
`openspec/changes/ai-audit-trail-and-outbound-gate/tasks.md` 的「6.x 落地偏离登记」第 6 行。

**销账（2026-09-04）**：已于变更包 `outbound-retry-audit-trace` 修复，落码 commit
`5d59021`（幂等键并入 reason）、`6d189c5`（留痕提炼为公共函数）、
`ea8f099`（`approve()` 被拦时留痕）。回归：`tests/test_outbound_end_to_end.py::
test_approving_into_a_closed_switch_leaves_its_own_trail`（不同原因两条痕）、
`::test_replaying_the_same_blocked_approval_leaves_no_second_trail`（同原因重放仍一条）、
`tests/test_outbound_block_stats.py::test_a_second_block_on_the_same_draft_gets_its_own_bucket`
（6.5 统计能看见第二次拦截）。
⚠️ 上面的成因分析 ⛔ 保留原文，不改写——它是"为什么当时只登记不修"的历史记录。

---

## ~~TD-10~~ · 边界守护 CI 的依赖基线钉死在立项 commit，有未声明的保质期 ✅ 已还

**2026-09-08 已处置（`0908B`）**：触发条件如期发生——补 `tzdata` 时这道守卫第一次红，
与本条预测逐字吻合。按下方改法**二**（显式登记制）落地：判据从「相对立项 commit 的
`requirements.txt` diff 必须为空」换成「每一条依赖都必须登记在
`scripts/check_boundary.py` 的 `REGISTERED_DEPENDENCIES` 里并写明理由」。
⛔ 不是删掉这道检查（本条明令禁止），是换判据。加依赖从此要改两个文件——
两处都动才是一次刻意的决定。

顺带修掉旧判据的两个毛病：① 不再需要 git，浅克隆的 `test` job 上不必再
`pytest.skip`（那个 skip 是真实存在的覆盖缺口）；② 覆盖**全部**依赖而不只是
「新增的那些」。反证：`tests/test_boundary_guard.py` 48→50 条。

⏸ **「附带盲区」未随本次处置**：扫描范围仍只覆盖 `app/`，`tests/` 与 `scripts/`
依然是缺口。⛔ 本条销号不含那一项——它另立门户，见下方原文。

---

### 原登记（保留备查）

#### TD-10 · 边界守护 CI 的依赖基线钉死在立项 commit，有未声明的保质期

**欠的是什么**：`scripts/check_boundary.py` 的 `BASELINE_COMMIT`
（`e65f6857fe255634d49a3e8696b1dba0f5facbec`，立项 commit）钉死作为依赖 diff 的
基线，判据是「相对该 commit，`requirements.txt` diff 为空」。这条判据没有
保质期——未来任何一次**正当**新增依赖（`pgvector` / `PaddleOCR` / `BGE-M3` /
阶段二 `FunASR`）落地时，diff 必然非空，CI 会在**所有分支**上永久变红，且
不存在"改代码修复"这条路：判据本身就是"不许改 `requirements.txt`"。

**附带盲区**：该守护脚本目前只扫描 `app/`，`tests/` 与 `scripts/` 未纳入扫描
范围——这两处若引入边界违规，现有判据看不见，影响面尚未评估。

**触发条件**：第一次要往 `requirements.txt` 加依赖时。届时改法二选一：
1. 基线改为"上一次 tagged 发版"（滚动基线，随发版前移）；
2. 判据改为"新增依赖必须在白名单内"（显式登记制，不依赖某个历史 commit）。

⛔ 不要到时候图省事简单删掉这道 CI 步骤——那会连同它已经在守护的边界一起丢掉。

**为什么现在只登记不做**：触发条件尚未发生，M1/M2 现有依赖未变。

**来源**：0903O 终审发现，登记于 `lanes-20260904-002413` 看护报告 §四。

---

## TD-11 · 挂起提醒（第 1 天 / 第 3 天）缺定时基础设施

**欠的是什么**：`m1-job-profile-intake` tasks 6.8。spec「流程长时间挂起」要求挂起后
第 1 天与第 3 天各发一次提醒，本系统至今没有任何定时 / 调度基础设施，这条一次都
没实现过。判定口径（第 1 天、第 3 天各一次）在 spec 的 Scenario 里写着，调度器落地
时直接照抄。

**触发条件**：定时基础设施落地。**与 tasks 5.6 同源**（那条也卡在同一件事上），
两条一起做——只做一条会得到一个只服务一个调用方的半吊子调度器。

**怎么还**：发提醒是有副作用的动作，必须是一个带幂等键的 `effect_*` 节点
（幂等键须含"第几次提醒"，否则调度器重跑会重复发）。⛔ **不用 sleep 循环或后台
线程充数**：那种东西进程一重启就没了，而这条 spec 要的恰恰是"挂起 7 天不丢"——
用它充数等于把"没做"标成"做完了"。

**不还的后果**：业务经理挂起后无人提醒，靠自己想起来回来确认；挂起越久越可能
被彻底遗忘，一个岗位就这么无声无息地停在半路。⚠️ 挂起状态**本身不丢**
（`tests/test_suspend_recovery.py` 已验 7 天），丢的只是提醒——所以这条债的代价是
流程变慢，不是数据损坏。

---

## TD-JD-1｜JD 溯源用的是闭集术语词表

**登记**：2026-09-04，交付单元 7「JD 溯源与导出」（tasks 7.3）合入时。

**是什么**：`app/agents/jd_grounding.JD_TECHNICAL_TERMS` 是一份人工维护的闭集词表
（45 个词条）。`verify_jd_grounding()` 只在这个集合里找未溯源的术语，**词表外的
编造看不见**。

**为什么这样做**：自动抽术语要么靠模型（`m1-intake-quality-fixes/design.md` 决策 11
否决过：判官自己会编，且不可复算），要么靠分词（引入新依赖，且中英混排的 ECU 术语
切不准）。闭集词表是确定性的、可评审的、可复算的。

**后果**：`ungrounded_terms` 这个数字是**下界不是精确值**——返回空列表 ⛔ 不等于
"文案没有编造"。与决策 11 声明的"本批要的是一个下界"口径一致。

**触发扩表的条件**：真实使用中出现词表没覆盖到的编造。⛔ 不要为了"更全"而预先
猛加词条——两个字母以内的纯拉丁词条（TI / AP / CP / IO）归一化后会命中大量无关词
的内部片段，噪声会淹没真正的编造。

---

## TD-JD-2｜「标记为人工撰写」的留痕不在 `human_review` 表里

**登记**：2026-09-04，交付单元 7（tasks 7.5）合入时。

**是什么**：「标记为人工撰写」是**唯一**能去掉 AI 生成标识的路径，它的留痕
（谁、何时）落在 `profile_json._jd_authorship` 这个内部键里，**不在 `human_review`
表里**。

**为什么这样做**：`human_review.decision_type` 的 CHECK 只认
`approved` / `revision_requested` / `abandoned` 三个值，而这三个字面量在
`app/storage/db.py`、`app/graph/nodes.py` 的 `DECISION_*` 常量、
`app/audit/assertions.py` 的 `TERMINAL_STATUS_DECISIONS` 三处逐字同源；SQLite 又
**改不了已有表的 CHECK**，`app/storage/db.py` 的加列机制（`_ADDED_COLUMNS`）只能加列
不能改约束，`.51` 上的老库会静默保留旧 CHECK。留痕走内部键与 `_gap_acknowledgement`
同一条路（design.md 决策 8：走内部键，不建新表）。

**后果**：`app/audit/assertions.py` 的**断言四**（每次人工决策都有 `human_review`
记录）**查不到这类决策**。去标识这个动作本身有留痕、且与去标识写在同一次 UPDATE 里
（结构上不可能出现"标识没了但查不到谁去的"），但它不进统一的审计口径。

**怎么还**：一个独立变更——改表（重建 `human_review` 并迁移数据）+ 改三处同源字面量
+ 改断言四。⛔ 不在交付单元 7 里顺手做。

**不还的后果**：审计那天要回答"谁把这份 JD 的 AI 标识去掉了"，得去翻
`profile_json` 而不是查审计表；断言四的"每次人工决策都有记录"这句话对这一类决策
不成立，而**没有任何东西会报错**。

---

## TD-硬门槛-1：学历/年限门槛提取的三条"安全方向"欠覆盖

**发生时间**：2026-09-04（`0904K`，tasks 1.2b/5.8/5.9 交付单元的整支 review）
**触碰文件**：`app/agents/hard_requirement.py`（`_education_floor` / `_experience_floor`）

`extract_hard_requirements` 的错误预算是**不对称**的，模块自述写死了方向：
「保守方向是"少一条规则"而不是"整个确认失败"」「门槛取高了会把合格的人挡在外面，
而这条规则将来要用来向候选人解释淘汰原因」。也就是说——**凭空造出一条 `blocking=True`
的门槛不可接受，丢掉一条真实门槛可接受**（人复核草案时补得回来）。

整支 review 与 controller 的对抗集把"凭空造门槛"这一侧清零了（22 条对抗串 0 误报）。
下面三条是**另一侧**的残留，全部只会**丢规则**，不会凭空造门槛：

1. **`「本科及以上学历，专业不限」` 丢掉真实的本科下限。** 全文 `不限` 否决是无条件的，
   不检查 `不限` 修饰的是**学历**还是**专业**。
2. **`「本科以上（优先考虑硕士）」` 丢掉下限。** 中文全角括号 `（）` 不在子句分隔符集合里，
   所以 `优先` 与 `本科以上` 被判成同一个子句，整个子句被当作偏好丢弃。
3. **`「不少于3年」` 丢掉真实的年限下限。** 它含子串 `少于`，被 `_EXPERIENCE_UPPER_BOUND_MARKERS`
   当成上限吞掉。`「不少于X年」` 在中文 JD 里与 `「X年以上」` 一样常见。

**为什么现在不还**：三条都在可接受的方向上，且 `hard_requirement` 本单元**只存不执行**——
没有任何代码读这张表去筛人，草案落地前还有业务经理的人工复核。修它们要动子句切分与否定
辖域，属于"再往前一步就得上语义理解"的地带，而一旦调模型，提取就不再是可复算、可回放对比
的确定性纯函数（工程铁律 2 + 本单元硬约束 1）。

**触发条件**：`hard_requirement` 第一次被**读**去做筛选/打分时（简历筛选环节），必须先还清。
在那之前"丢一条规则"由人复核兜底；在那之后"丢一条规则"变成静默放过不合格候选人。

**不还的后果**：业务经理看到的草案缺一条他明明写了的门槛，而**没有任何东西会报错**——
他只会以为系统认为那句话不构成门槛。

---

## TD-12 · unit2 网关兜底落地时会新增两个 effect 节点，需同步进幂等清单

**登记**：2026-09-08，delivery unit 4.4（幂等专项测试）终审 fix wave 登记。

**是什么**：`docs/superpowers/plans/2026-09-08-m1-job-profile-intake-unit2-gateway-fallback-and-retry.md`
（unit2 网关兜底与重试，尚未落地）新增两个 effect 节点——`effect_mark_needs_manual`
与 `effect_deliver_manual_handoff`（计划里落在 `app/graph/manual_handoff.py`，均带
`@idempotent_effect(...)`）——该计划全文没有提到
`tests/test_effect_idempotency_suite.py` 的 `EFFECT_NODE_MANIFEST`。unit2 落地
合并时，`test_manifest_matches_the_source_tree` 会因为源码里出现了清单里没有的
节点名而变红——**这是本单元的清单守卫在按设计工作**（「新增节点漏测即变红」），
但下一个实施者需要事先知道该怎么处理这次预期中的红灯，而不是当场现推。

**触发条件**：unit2 落地合并那一刻。

**怎么还**：unit2 的实施者必须做两件事，两件都做才算还清：
① 把 `effect_mark_needs_manual` 与 `effect_deliver_manual_handoff` 两个名字加进
`tests/test_effect_idempotency_suite.py` 的 `EFFECT_NODE_MANIFEST`；
② 在 `build_recipes()` 里各加一条崩溃-恢复配方（种子数据、调用方式、
`count_business_rows`），让 `test_forced_interrupt_then_recovery_applies_the_effect_exactly_once`
与 `test_effect_log_count_equals_business_rows_per_thread` 两条参数化用例把它们
也覆盖到。
⛔ **不得为了让测试变绿而从 `EFFECT_NODE_MANIFEST` 里删掉任何既有条目**——删掉
一个节点名等于宣布"这个节点不需要幂等保护"，这是工程铁律 1 的例外，只有
Shao Peishen 能拍板。

**不还的后果**：两个新节点没有崩溃-恢复用例覆盖，铁律 1 要求的"业务写与
effect_log 同一事务提交"对它们无人验证过；一旦其中一个在提交前崩溃后重放，
不会有任何测试事先发现——而这恰恰是本交付单元存在的全部意义要防的那类失败。

## TD-13 · 丢弃岗位的两次删除之间没有原子性保护

**登记**：2026-09-08，delivery unit 5.3（需求识别）终审 park 登记。**⚠️ 待 Shao Peishen 拍板。**

**是什么**：`POST /api/jobs` 判定首轮不是用人需求时，`app/web/server.py` 的 `create_job`
连着调两个函数把这一轮写下的东西抹掉：

```
discard_unstarted_job(conn, job_id)          # 删业务行，自己 commit
discard_thread_checkpoints(graph.checkpointer, job_id)   # 删 checkpoints / writes
```

两者**不在同一个事务里**（前者已经 commit 了，后者走的是 checkpointer 自己那条连接）。
第二个调用若抛异常，业务行已经删掉、`checkpoints` / `writes` 还留着。

**这不是 5.3 计划里已登记的那个崩溃窗口。** 计划正文登记的是「`INSERT job` 与
`discard_unstarted_job` 之间崩溃 → 留一行零版本的 drafting job」（`create_job` 里有
逐字注释）。本条是**另一个**、更靠后的窗口，计划没有覆盖到。

**真正的代价比"孤儿行"大**（终审订正了首轮 park ruling 的表述，这一条要写清）：
`discard_thread_checkpoints` 抛出来会一路冒到 `create_job` 外面，调用方拿到的是
**500，而不是那句引导语**——而承载引导语的 `outbox` 行已经被前一个调用删掉了。
也就是说这条路径上 **spec 的前半句（"回复引导语说明可以怎么提需求"）也静默失效了**，
不只是记账没做干净。不是数据丢失（那一轮本来就没有任何有价值的东西），
但比"残留几行 checkpoint"严重。

**为什么现在不还（park 的理由）**：

1. **这段代码是 5.3 计划逐字钉死的**（`docs/superpowers/plans/2026-09-08-m1-job-profile-intake-unit5-3-intent-recognition.md`
   的 Task 4 Step 3）。改它属于推翻计划，按工具链协作规则是**人的决定**；
   5.3 是无人值守泳道跑的，⛔ 不替 Shao Peishen 拍板。
2. **实践中近乎不可达**（终审给出、比首轮 ruling 更强的理由）：`get_connection`
   设了 `journal_mode=WAL` 与 `busy_timeout=5000`（`app/storage/db.py`），图是严格线性的，
   checkpointer 自己独占一条连接 —— 这两条 DELETE 要撞上 `SQLITE_BUSY`，得先熬过一个
   5 秒重试窗口，而此刻并没有任何东西在跟它抢。另一个触发源是表改名，那会**立刻、
   每一次**都失败，部署当场就能发现，不会静默。

**⛔ 不要把它理解成"只有两种删除顺序可选"**（终审明确要求把这条记进来，免得
"二选一"的框架被冻进记录里）。至少有三个选项：

- **A（现状）**：先删业务行、再删 checkpoint。失败 → 不可见的孤儿 checkpoint 行 + 500
- **B**：先删 checkpoint、再删业务行。失败 → 留下一行**可见的**「待确定 / drafting」僵尸 job
  （岗位列表走 LEFT JOIN，`app/storage/job_queries.py`），业务经理会真的在屏幕上看见它。
  **比 A 更糟**
- **C（终审提出，两轮 review 都没考虑过）**：保持 A 的顺序，让**业务行删除**作为
  "为准的那次事务"；在**调用点**把 `discard_thread_checkpoints` 的异常 catch 住、
  按 ERROR 记日志、照常把引导语返回给用户。
  `job_discard.py` 里那条 `⛔ 不要 try/except` 是对的——但它约束的是**函数内部**，
  并不延伸到调用点：表改名会在**每一条**离题首轮消息上失败，ERROR 日志会持续刷，
  部署当场暴露，所以"静默空转"在这个场景下不是真风险

**触发条件**：Shao Peishen 复核本条时。若判 C 可接受，改动量约 5 行，只动
`app/web/server.py` 的调用点，不动 `app/storage/job_discard.py`。

**不还的后果**：极低概率下，业务经理发了一句无关的话，屏幕上等来的是一个 500 错误
而不是那句"没听懂是不是用人需求，可以试试…"，而**日志里不会有任何东西说明
引导语其实已经生成过、只是连同 outbox 行一起被删了**。

## TD-14 · `hr-wecom-aibot-liaison` 的 proposal「不触碰 pyproject.toml」与实现已不符

**欠的是什么**：`openspec/changes/hr-wecom-aibot-liaison/proposal.md:37` 的「Impact ·
不触碰」把 `pyproject.toml` 整份列为不触碰。但第 1 章（2026-09-08 落地）**改了它的
`testpaths` 一行**，把 `tools/liaison/tests` 接进去，让全量 `pytest` 一次跑得到本服务的测试。

两者的**本意其实不冲突**：该条要挡的是**依赖**从 `pyproject.toml` 溜到 `.51`（design D10
通篇讲的都是依赖清单，`pyproject.toml` 在 `sync-to-server.sh` 的 `SYNC_PATHS` 里）。
`testpaths` 不是依赖，且第 1 章自带 `test_no_liaison_dependency_leaked_into_pyproject`
守住「不许往 `[project].dependencies` 加任何东西」。冲突的是**措辞**，不是事实。

**触发条件**：**归档该变更包之前**（跑 `openspec-archive-change` 之前）。把那一行从
「不触碰 `pyproject.toml`」订正为「⛔ 不往 `pyproject.toml` 添加任何依赖；`testpaths`
因 `tools/liaison/tests` 接入而新增一条」。

**不还的后果**：变更包归档进 `openspec/specs/` 之后，活文档里会留下一条**与代码相反**的
约束。下一个读它的人（或 reviewer）会把已经通过终审的 `testpaths` 那行当成越界改动，
要么白白花一轮去"修"它，要么把 `tools/liaison/tests` 从 `testpaths` 摘掉——而摘掉的后果
是静默的：不报错、不失败，只是本服务的 28 条测试从此没人跑。

---

## TD-15 · 准入名单出厂态下每条消息刷 3 条 ERROR 日志

**欠的是什么**：`tools/liaison/whitelist.py` 的出厂态（`config/whitelist.yaml` 两条 `userid`
留空，真实企微 userid 尚未取得）下，每次 `load_whitelist()` **必然**产生 3 条 ERROR
（两条「userid 为空，整条丢弃」+ 一条「零条有效条目」）。而 spec 硬性禁止缓存名单，
第 4／5 章又要**每条入站消息**调一次 `admit()` —— 于是机器人收到的每一条消息都会刷 3 条 ERROR。

**为什么现在不改**：这不是 bug，出厂态"谁都不准入"是刻意的 fail-closed 设计；
但把这几条降级会与模块 docstring 里「任何失败都记 ERROR」的契约冲突，
**属于需要拍板的取舍，不是可以顺手改掉的东西**（终审 reviewer 原话：needs a decision
rather than a quiet edit）。本章尚未接线第 4／5 章，实际日志量为零，故留到接线前处置。

**触发条件**：**第 4／5 章把 `admit()` 接进入站消息路径之前**（以先到者为准：或真实
userid 填入使出厂态消失时复核一次）。

**不还的后果**：ERROR 级告警从上线第一天起持续误报，运维会很快学会忽略这个 logger ——
而 `whitelist.py` 里真正的合规漏洞（如已修的 C1 值泄漏、I2 顶层字段静默忽略）
恰恰也是靠 ERROR 日志暴露的。**噪声把唯一的告警通道淹掉**，真故障将无人察觉。

## TD-16 · 终审延后的四条 Minor（准入名单）

2026-09-08 交付单元 3 终审记录、当次未改：

1. **YAML 重复键静默 last-wins**：文件里出现两个 `members:` 块（或条目内两个 `userid`）时，
   PyYAML 静默取后者，**零日志**。运维若"追加一段"而不是扩写原有列表，名单会被静默替换。
2. **非 UTF-8 配置被归为「未预期异常」**：`UnicodeDecodeError` 是 `ValueError` 不是 `OSError`，
   落到兜底带。fail-closed 正确，但运维最可能犯的文件错误被报成内部异常。
3. **`path` 传 `str`／`None` 之外的类型**：类型标注是 `Path`，传 `str` 会 fail-closed 但报
   「未预期异常」。第 4／5 章调用方若传字符串，会得到一个永久拒绝且诊断错位的闸门。
   低成本修法：`_read_roster` 顶部 `path = Path(path)`。
4. **`config/README.md` 未警告失败面**：它告诉运维可以改活文件、不必重启，
   但没说 YAML 写坏／存成非 UTF-8／存盘竞态会**拒绝所有人**，且唯一提示是 ERROR 日志。

**触发条件**：第 4／5 章接线时一并处理（第 3 条尤其影响调用方）；第 4 条可随时补。
**不还的后果**：1 与 4 都是**静默**失败——名单被改小或全员被拒，而闸门看起来健康。
## ~~TD-17~~ · `app/outbound/delivery.py:12` 的非法转义序列 SyntaxWarning ✅ 已还

**2026-09-08 已处置（`0908U`，轻量通道）**：按下方登记的第一种改法，把该模块的
模块级 docstring 前缀成原始字符串（`"""` → `r"""`）。**全文件只改了这 1 个字符**——
本条 opener 明令「⛔ 不动该文件其它任何字符」，`git diff` 为 1 insertion / 1 deletion。

选 `r"""` 而不是把反斜杠转义成 `\\`：那条 Windows 路径是给运维**照抄**的，
`C:\\apps\\...` 在源码里读起来就不再是他要粘进去的那串。raw 前缀让源码与实际路径逐字一致。

**验证**：`./venv/bin/python -W error` 下 `ast.parse` 与 `import app.outbound.delivery`
均通过（修前 `ast.parse` 抛 `SyntaxError: "\z" is an invalid escape sequence`）；
`tools/liaison/tests/test_app_does_not_import_tools.py` 5 passed 且 0 warning；
全量 `pytest -q` 1378 passed / 3 skipped 与基线一致，warning 总数 7725 → 7715
（正是这条贡献的 10 条），全量输出里 `invalid escape sequence` 归零。

---

### 原登记（保留备查）

#### TD-17 · `app/outbound/delivery.py:12` 的非法转义序列 SyntaxWarning

**欠的是什么**：该行 docstring 里写了 Windows 路径 `C:\apps\...\candidate_outbound.switch`，
其中 `\z`（以及同类反斜杠序列）是**非法转义序列**，Python 3.14 会发 `SyntaxWarning:
"\z" is an invalid escape sequence`，且明确警告「Such sequences will not work in the future」。

**为什么现在才浮出来**：第 2 章新增的结构断言（`tools/liaison/tests/test_app_does_not_import_tools.py`）
会 AST-parse `app/` 下全部 52 个模块，于是把这条既有告警**暴露成每次跑该测试文件都出现的 2 条 warning**。
缺陷本体一直都在，只是此前没有任何测试去 parse 它。

**为什么第 2 章不修**：本交付单元的 opener 明令「⛔ 只动 `tools/liaison/` 与测试；⛔ 不碰 `app/`、`scripts/`」。
越界修它会让本章的"零 `app/` 改动"这条可机器核对的边界失效。

**触发条件**：下一个**本来就要改 `app/outbound/`** 的变更包顺手修；或单独派一个 opener。
修法是把该 docstring 改成原始字符串（前缀 `r`）或把反斜杠转义成 `\\`。

**不还的后果**：Python 未来版本会把 `SyntaxWarning` 升级为 `SyntaxError`，届时
`app/outbound/delivery.py` **直接 import 失败**——而它在 `.51` 的发送链路上。
在那之前，它持续污染测试输出，让"测试输出应当干净"这条判据失去分辨力。

## TD-18 · 值守服务事务扫描器对 `with <Call>:` 会误报

**欠的是什么**：`tools/liaison/tests/test_liaison_effects.py` 的 `_scan_transaction_violations`
在第 2 章终审后放宽为：`with` / `async with` 的 context expr 是 `ast.Name`、`ast.Attribute`
**或 `ast.Call`** 一律判为「第二个事务管理者」。放宽是为了抓住 `with self._conn:` 这个
第 3–5 章最可能出现的真实违规形态（终审实测原写法漏掉它）。

**代价**：第 3–5 章一旦在 `tools/liaison/` 的**非测试**代码里写 `with open(...) as f:`、
`with contextlib.suppress(...):` 这类与数据库无关的上下文管理器，会被误判为违规。

**触发条件**：第 3–5 章第一次因此变红时。届时的正确处置是**给扫描器加白名单或细化判据**
（例如只对名字里含 `conn` 的表达式、或对已知连接符号判违规）。

**不还的后果**：可控——**这个失败是响亮的**（一条可见的测试失败），不是静默的。
⚠️ 但要防的是**图省事把守卫改回只认裸局部名**：那会重新打开 `with self._conn:` 的口子，
而那个口子的症状是**没有症状**（`effect_log` 与业务表静默劈叉，正是 `.51` 2026-08-10／08-12
丢 `outbox` 的失败模式）。宁可留误报，⛔ 不许退回窄化。

## TD-19 · 真实建连尚未适配——`make_sdk_connect` 对协程 `connect` 表面按"未验即拒绝启动"处理

**欠的是什么**：Task 5 的探针实测（`docs/findings/2026-09-09-aibot-wsclient-表面实测.md`
「遗留发现」）发现真实 SDK 的 `WSClient.connect` 是 `async def`——同步调用它只会返回一个
协程对象、不执行任何网络操作，不满足 `run_forever` 期望的"阻塞到断开为止"契约；真正的
阻塞入口是 `client.run()`。第 7 章按 controller ruling 把这个判断做成结构：
`session_client.make_sdk_connect` 用 `inspect.iscoroutinefunction` 探测 `connect`，探到协程
函数就当场 `raise SdkSurfaceUnverifiedError`（指名 `client.run()` 与 findings 文档），
`__main__.main()` 据此以 `EXIT_SDK_SURFACE_UNVERIFIED` 拒绝启动。**本章没有写、也没有猜
任何"把 `client.run()` 接进 `run_forever`"的适配代码**——那需要真实凭据把整条链路跑一遍
才能验证接对了，而 `HR_LIAISON_BOT_ID`/`HR_LIAISON_BOT_SECRET` 尚未注册，本仓库拿不到。

**触发条件**：Shao Peishen 在企微后台注册 aibot、取得 `HR_LIAISON_BOT_ID`/
`HR_LIAISON_BOT_SECRET` 之后，第 8 章 8.6 灰度验收——用真实凭据把 `client.run()`（或等价的
同步阻塞封装）接进 `session_client.run_forever`，并端到端验证真实建连与真实断线重连都按
预期记窗口、告警、恢复。在此之前，`tools/liaison/.venv` 里跑
`test_make_sdk_connect_refuses_a_coroutine_function_connect` /
`test_main_exits_when_the_sdk_connect_is_a_coroutine_function` 等断言会持续把这个缺口保持
"响亮可见"。

**不还的后果**：不还也没有隐患——当前处置是"表面对不上就拒绝启动"，失败模式是进程
在启动时**立刻、显式**退出（`EXIT_SDK_SURFACE_UNVERIFIED`，日志与 stderr 都点名
`client.run()`），⛔ 不是静默自旋重连（那正是本章要消灭的失败类别，详见本条上方引用的
findings 与 controller ruling）。真正的风险只在于：如果将来有人绕开
`make_sdk_connect` 的这道检查、直接把 `client.connect`（协程）手工拼进
`run_forever`，就会退回"服务起得来、日志正常、但从不真正建连"的静默故障——⛔ 不许
削弱或绕过这道检查来"让它先跑起来"。

## TD-20 · `start()` 的启动补记用"本次启动时间"当恢复时间，会低报"重启时网络仍未恢复"的中断时长

**欠的是什么**：`LiaisonSession.start()`（`tools/liaison/session.py` ~271-304、~362-370）
在**尚未连上**的时刻就把每一个未闭合窗口（含刚补开的 `startup_gap` 窗口）的
`recovered_at` 记成**本次启动时间**并据此告警——而不是**真正重新连上的时间**。
启动后、首次连上前的那段（`starting` 状态）没有任何机制为它开窗、告警或计入
已发出的那条告警：`tick()` 在 `starting` 状态下直接早退；`on_disconnected` 只在
SDK 事件到达时触发，而"启动后从未连上过"这种情形永远等不到那个事件；
`run_forever` 的 `on_attempt_failed` seam 虽存在，`main()` 从未接上它。

**复现**（reviewer 实测）：末次存活戳 `connected@10:10` → 进程被杀 → 10:40 网络仍未
恢复时重启 → 12:00 才第一次真正连上。结果：只发出**一条**告警，文案是
「10:10 至 10:40（持续 30 分 0 秒）…请重发」，窗口以 `startup_backfill@10:40` 闭合；
10:40 到 12:00 这 80 分钟**没有窗口、没有告警、没有任何记录**。收信人被告知补发
30 分钟，实际丢失的是 **110 分钟**——低报了 80 分钟。

**触发条件**：第 8 章灰度（8.6）接真实 SDK 与 launchd 时。launchd 用
`AtStartup`/`KeepAlive` 在开机或系统唤醒时即拉起服务，这正好早于 Wi-Fi 关联完成
的时间点，是"重启时网络仍未恢复"这一场景最常发生的时刻。届时三条候选修法：
① 启动时不闭合旧窗口，留到 `on_connected` 真正连上时再闭合（告警自然带上真实
恢复时间）；② 启动时改为另开一个新窗口，专等 `on_connected` 闭合；③ 显式接上
`run_forever` 的 `on_attempt_failed` seam，把每次建连失败也计入中断记录。

**不还的后果**：告警会**低报**中断时长——它是"响亮但不完整"的，⛔ 不是静默丢失
（该发的那条告警仍会发出、仍写"请重发"），但收信人会按错误的、偏短的时段去补发，
落在低报区间之外的消息因此永远补不回来。

**⚠️ 标注**：本条行为是计划既定行为，逐字对应 Architecture `start()` step 2，
不是本次实现的疏漏。改状态机语义是设计决策，需 Shao Peishen 拍板改法，
⛔ 执行方不得自行改动 `session.py` 的状态机行为。
