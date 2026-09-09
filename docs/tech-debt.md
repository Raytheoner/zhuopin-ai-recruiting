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

## ~~TD-14~~ · `hr-wecom-aibot-liaison` 的 proposal「不触碰 pyproject.toml」与实现已不符 ✅ 已还

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

**已还**（2026-09-09 `[Mac]0909O`，轻量通道）：`proposal.md:37` 那一行把 `pyproject.toml`
从「不触碰」清单里摘出来单列，订正为「⛔ 不往 `pyproject.toml` 添加任何依赖；`testpaths`
因 `tools/liaison/tests` 接入而新增一条」。**只改了这一行**，`git diff` 为 1 insertion /
1 deletion。挡依赖的判据没有放松：`test_no_liaison_dependency_leaked_into_pyproject`
原样守着 `[project].dependencies`。

---

## ~~TD-15~~ · 准入名单出厂态下每条消息刷 3 条 ERROR 日志 ✅ 已还

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

**已还**（2026-09-09 `[Mac]0909O`，轻量通道。裁决＝**去重不降级**，Shao Peishen 第十四批认可）：
`whitelist.py` 新增 `_FailureLog`，把全部 `logger.error` 收口成 `failures.error`，按
**名单文件内容的 SHA-256**（前面拼路径，见下）去重——同一份内容连续失败只记**一组**，
内容一变重新记一组。⛔ 未降级：记出来的仍是 `ERROR`（`test_dedup_does_not_downgrade_the_level`）。
⛔ 未缓存名单：全局状态只有 `_LAST_LOGGED_FAILURE_FINGERPRINT` 这**一个 64 字符十六进制串**，
判定路径每次仍完整重读重解析（`test_dedup_caches_only_a_fingerprint_never_the_roster`
＋原有的 `test_admit_rereads_the_file_on_every_call` 双守）。
契约「任何失败都记 ERROR」原样成立：同一轮里的兄弟 ERROR ⛔ 不许互相吞——去重判据在
`_FailureLog` **构造时**快照上一轮指纹，全局只在 `finish()` 更新一次
（`test_error_group_covers_every_distinct_failure_before_dedup_kicks_in` 断言出厂态那组
仍是 2 条「userid 为空」+ 1 条「零条有效条目」）。成功加载会清空指纹槽，
所以「修好→又改坏回同一份内容」会重新报。

**两处刻意的收紧**（都比 opener 字面要求更严，登记备查）：
1. 指纹 = `sha256(repr(path) + 文件内容)` 而不是只含内容。两个不同文件恰好写坏成同一份内容
   是两个独立现场，⛔ 不该互相吞 ERROR（`test_two_different_files_with_identical_bad_content_both_report`）。
   生产上只有一份 `DEFAULT_WHITELIST_PATH`，对去重效果零差别。
2. 每输出一组 ERROR，组尾补一行「以上准入名单失败按文件内容去重…」。
   ⚠️ 否则运维看见 3 条 ERROR 之后突然安静，会误以为问题自己好了——
   **日志安静不等于修好了**，这一行是唯一的提示。`config/README.md` 同步写进了失败面表。

## ~~TD-16~~ · 终审延后的四条 Minor（准入名单）✅ 已还

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

**已还**（2026-09-09 `[Mac]0909O`，轻量通道，四条一并）：

1. **YAML 重复键 fail-closed**：新增 `_NoDuplicateKeySafeLoader`（`yaml.SafeLoader` 子类，
   安全性不变——`test_yaml_python_tags_are_not_constructed` 常绿），构造映射前先自己扫一遍
   键，撞重复即抛 `_DuplicateKeyError`；`_read_roster` 单开一条分支记 ERROR 并**点名键名 +
   行列号**。⛔ 只记键名不记值，与 `extra` / `top_level_extra` 两处同口径
   （`test_duplicate_key_error_does_not_leak_field_values`）。
2. **非 UTF-8 单列一类**：`read_text` 换成 `read_bytes` + 显式 `decode("utf-8")`，
   `UnicodeDecodeError` 单开分支，日志说「不是 UTF-8 编码（另存为 UTF-8 无 BOM 即可）」
   并只带 `encoding` / 字节偏移量 / `reason` 三个定长元信息——⛔ 不记 `str(exc)`、
   更不记 `exc.object`（那是文件内容）。不再落「未预期异常」。
3. **`_read_roster` 顶部 `path = Path(path)`**：调用方传 `str` 现在走正常分支，
   诊断落到「文件不可读」而不是「未预期异常」（`test_str_path_*` 两条）。
4. **`config/README.md` 补「⚠️ 失败面」段**：六类失败 × 典型现场 × 日志里会说什么的表，
   点明「改坏了也是立刻生效」「唯一提示是 ERROR 日志」「⛔ 不要以为没报错就是好了」，
   并单独警告重复键的静默 last-wins 与 TD-15 的去重语义。

**验证**：`tools/liaison/tests/test_whitelist.py` 新增 15 条（TD-15 七条 + TD-16 八条），
全文件 61 passed。逐条证伪过：把 `whitelist.py` 换回改前版本再跑，这 15 条里有 9 条变红
（三条 TD-16 判据 + TD-15 去重），其余 6 条是防回归的常绿守卫。
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

## ~~TD-18~~ · 值守服务事务扫描器对 `with <Call>:` 会误报 ✅ 已还

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

**已还**（第 4 章 Task 3，2026-09-09）：`_scan_transaction_violations` 加 `_NON_DB_CONTEXT_CALLEES`
正面白名单，放行 `open` / `os.fdopen` / `io.open` / `contextlib.suppress` /
`tempfile.NamedTemporaryFile` / `tempfile.TemporaryDirectory`。⛔ 未窄化判据——
`with self._conn:` 与 `with get_connection():` 仍被抓，9 条 `test_scanner_*` 全绿。

**已还（第二轮细化，2026-09-09 `[Mac]0909O` 轻量通道）**：第一轮的正面白名单只放行 6 个
**逐条全名**，`with suppress(...)`（裸名导入）、`with TemporaryDirectory():`、
`with contextlib.ExitStack():` 这些照样误报（实测：还原成第一轮实现后，新增的
`test_scanner_allows_non_db_context_managers` 10 格里有 4 格红）。本轮把 `ast.Call`
这一格拆成三步判据：

1. 名字含 `conn` / `connect` / `transaction` / `begin`（**不分大小写**），或命中
   `_KNOWN_CONNECTION_CALLEES`（`closing` / `atomic` / `savepoint` / `cursor` /
   `Session` …）→ **违规**；
2. 命中非 DB 白名单——逐条全名，**或模块族** `contextlib.*` / `tempfile.*` → 放行；
3. 其余陌生被调用者 → **仍判违规**。

第 1 步压在第 2 步**前面**是刻意的：`contextlib.closing(conn)` 属于 `contextlib.*`
却货真价实管着一个连接，⛔ 不许被模块族白名单捞走。

🔴 **`ast.Name` / `ast.Attribute` 两格一个字没动，仍然无条件判违规**——本条原文
⛔ 的那种"退回只认裸局部名"没有发生。实测证伪：把 `isinstance(expr, (Name, Attribute, Call))`
改回 `isinstance(expr, ast.Name)`，**12 条测试当场变红**（含
`test_scanner_catches_with_self_conn_attribute` 与新增参数表里的 `with self._conn:`）。

⚠️ **第 3 步是相对本轮 opener 字面要求的一处收紧偏离**，刻意为之并登记：opener 写的是
"只在名字含 conn/… 时判违规"，照字面写会让 `with pool.acquire():` 这类名字里一个词根都
没有的陌生连接**静默通过**——而本条原文的落款正是"宁可留误报，⛔ 不许退回窄化"。
实测证伪：把第 3 步改成放行，`test_scanner_still_catches_unknown_callees_by_default` 变红。
opener 逐条点名要放行的 `open` / `contextlib.*` / `tempfile.*` / `suppress` /
`TemporaryDirectory` 全部已放行，**TD-18 的真实痛点已消**；留下的误报面是响亮的
（一条可见的测试失败 + 往白名单加一行即解），漏判则**没有症状**。

新增 `test_scanner_allows_non_db_context_managers`（10 格）、
`test_scanner_still_catches_connection_shaped_context_managers`（11 格证伪）、
`test_scanner_still_catches_unknown_callees_by_default`。`test_liaison_effects.py` 55 passed。

## ~~TD-19~~ · 真实建连适配 ✅ 已还（`0909AC`，`[Mac]0909AE` 实测验收）

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

### ⏫ 2026-09-09 状态更新：阻塞理由已消失，本条转为**已提上日程**（Shao Peishen 裁决）

上面「触发条件」里写的前置——"凭据尚未注册，本仓库拿不到"——**今天已经不成立**：
`HR_LIAISON_BOT_ID` / `HR_LIAISON_BOT_SECRET` / `HR_LIAISON_GROUP_WEBHOOK` 三者均已落
本机 `.env`（`[Mac]0909AA` 只验存在性、未回显取值），白名单两个 userid 也已填入生效
（`e969f1b`）。本条不再是"等外部条件"，而是**队列里的一件待办**。

🔴 **它现在是 launchd 装机的真前置，而不是反过来。** 服务进程当前 `exit 4` 且**立刻返回**；
而 `com.zhuopin.hr.liaison.plist` 是 `RunAtLoad=true` + `KeepAlive=true` +
`ThrottleInterval=30`。TD-19 未还就装 job，得到的是一台**每 30 秒重启一次、每次立刻失败、
把 `launchd.err.log` 无限追加同一条报错的机器**——plist 自己的注释预警过这个形状（原文针对
退出码 2 的缺凭据场景，退出码 4 是同一形状）。⛔ 灰度前置清单里把 launchd 装机与 TD-19
并列是错的，它排在 TD-19 之后。

### ✅ 2026-09-09（`0909AC`）代码已就位——**本条仍不销**，剩最后一步归 Shao Peishen

**已做完的**（`tools/liaison/session_client.py` / `__main__.py`）：

1. `make_sdk_connect` 现在返回的阻塞调用真的调 **`client.run()`**（同步、跑到断线为止），
   ⛔ 不再是那个协程 `connect`。`REQUIRED_CLIENT_ATTRS` 从 `("on", "connect")` 改成
   `("on", "run")`——清单里只留**真正会被调用**的方法。
2. **护栏没被删、没被降级，只是挪了位置**：`verify_client_surface()` 现在核四项——
   方法齐全、`run` 可调用、`run` **不是**协程函数、`run` 能**零参数**调用。断言在岗：
   `test_make_sdk_connect_refuses_a_coroutine_function_run`、
   `test_make_sdk_connect_refuses_a_run_that_needs_arguments`、
   `test_main_exits_when_the_sdk_run_is_a_coroutine_function`、
   `test_make_sdk_connect_verifies_the_surface_before_run_forever_can_swallow_it`。
   ⛔ 没有加任何"跳过校验"的开关或环境变量。
3. 🔴 **每次建连尝试改用一个全新的连接对象**（`make_sdk_connect` 收的是**工厂**不是对象）。
   实测 `aibot==1.0.2` `client.py::connect` 开头是 `if self._started: return self`，而
   `_started` 只有 `disconnect()` 会清。同一个对象第二次 `run()` ⇒ connect 立刻返回 ⇒
   `loop.run_forever()` 挂在空转的事件循环上 ⇒ **进程活着、日志正常、永远不再连上**，
   ⛔ 没有任何症状。守护断言：`test_make_sdk_connect_builds_a_fresh_client_for_every_attempt`。

**实证**（装了闸门、⛔ 未真连企微）：`python -m tools.liaison` 已能一路走到 `client.run()`，
外层退避 1s → 2s → 4s，且每一轮 SDK 都重新打印 `Establishing WebSocket connection...`
（复用旧对象时这里会变成 `Client already connected` 然后永久挂起——正是第 3 条防的形态）。

**⏳ 曾经仍欠的那一步——已由 `[Mac]0909AE` 完成**：8.6 首次真实建连实测，连接**建立成功**
（`WebSocket connection established` ＋ 认证通过），本适配就此验收销账。⚠️ 那次实测同时暴露了
**另一个** bug（TD-38，`heartbeat_interval` 单位错配），⛔ 它不属于本条——本条守的是
「`run_forever` 拿到的 callable 是否真的阻塞建连」，那一问的答案已经是肯定的。

🔴 **2026-09-09 稍晚订正——⛔ 先别跑真实建连**：`[Mac]0909AE` 已经替本条跑了第一次
（见 `docs/findings/2026-09-09-首次真实建连实测.md`），结果撞上 **TD-38**（`heartbeat_interval`
传秒当毫秒，心跳 ×1000，44 秒即被企微 `45009 Too many requests` 限流）。**TD-38 未还之前
再跑一次只是再洪泛一次**，⛔ 不要重复。下面这条命令保留给 TD-38 还上之后。

⚠️ TD-38 的成因就在本条改的那个文件里（`session_client.py:37`），但它是**单位错配**——
类型相同（int）、只有单位不同，按设计就在 `verify_client_surface` 那道护栏的盲区里，
且**无任何本地症状**，只有真连上企微才暴露。⛔ 不要因此去加"校验单位"的启发式，
真正的判据是 TD-39 那三项端到端观察。

**TD-38 还上之后复跑用的命令**（在仓库根、Terminal 里，⛔ 不经 pytest；本条已销，这条留给 TD-39 复核）：

```bash
PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison
```

看三件事：① SDK 日志出现 `WebSocket connection established` 且**没有** `errcode=853000`；
② 拔网线／断 Wi-Fi 后 `liaison_outage_window` 开出一条窗口、群里收到断线告警；
③ 网络恢复后自动重连、窗口闭合并发出恢复告警。三条都对 ⇒ 本条可销，G-4（launchd 装机）解锁。

⚠️ 先跑一次 `... -m tools.liaison --self-check` 更省事：它把凭据与 SDK 表面全校验一遍
就退出（exit 0），⛔ 不建连——凭据打错时不必等到真连才发现。


**销账依据（`[Mac]0909AE`，2026-09-09 21:25:49 CST）**：用真实凭据前台跑
`python -m tools.liaison`，SDK 日志出现 `WebSocket connection established` →
`Authentication successful` → `Authenticated`，`data/liaison/liveness.json` 写出
`state: connected`，`data/liaison.db` 建出五张表。**`client.run()` 接进
`run_forever` 是对的，本条欠的东西已还清。** 全文见
`docs/findings/2026-09-09-首次真实建连实测.md`。

⚠️ **销账 ≠ 8.6 通过**：同一次实测暴露出**另一个**缺陷（心跳单位，**TD-38**），
服务只在线 44 秒就被企微以 `45009 Too many requests` 判死。8.6 的阻断项从 TD-19
**转移**到 TD-38，⛔ 不要因为本条已销就以为 8.6 可以往下走。

## ~~TD-20~~ · `start()` 的启动补记用"本次启动时间"当恢复时间，会低报"重启时网络仍未恢复"的中断时长 ✅ 已还（`08d8784`）

**2026-09-09 已处置（`0909U`）**：裁决＝**改法 ①**（2026-09-09 Shao Peishen，见
`docs/findings/2026-09-09-Shao-Peishen-裁决-留存冲突B与TD20改法.md` 裁决二），已落地。
`LiaisonSession.start()` ⛔ 不再调 `_backfill_open_windows(now)`——启动时**不闭合**任何
未闭合窗口；闭合统一交给 `on_connected` 的 `CLOSED_BY_RECONNECT` 路径（"启动后首次连上"
走的是同一条），恢复时间因此自然是**真正连上的时间**。`start()` 其余步骤与顺序不变：
读存活戳 → 必要时开 `startup_gap` 窗口 → 补发"已闭合未告警"的旧窗口 → 最后写存活戳。

`CLOSED_BY_STARTUP_BACKFILL` 与 `_backfill_open_windows` 失去调用方后**保留**，
docstring 已注明按本裁决停用——`effect_log` 与 `liaison_outage_window` 里存着用它闭合的
历史行，schema 的 `CHECK` 仍允许该取值，删掉它们那些历史行就失去解释。

回归用例咬住 reviewer 的原始复现时间线（`test_starting_while_still_offline_does_not_
underreport_the_outage`，`tools/liaison/tests/test_session_state_machine.py`）：
`connected@10:10` → `10:40` 网络未恢复时重启 → `12:00` 首次 `on_connected`。
修复前告警「10:10 至 10:40（持续 30 分 0 秒）」，修复后「10:10 至 12:00（持续 1 小时
50 分 0 秒）」＝ 110 分钟。`specs/liaison-channel-session/spec.md` 第 46／56 两处口径同步。

⛔ **本次未做、也不算欠**：改法 ③（接 `run_forever` 的 `on_attempt_failed`）由裁决明确
排除——它是 ① 之上的加强，要做须另立一条，⛔ 不许当成 TD-20 的遗留。

---

<details>
<summary>原始登记（保留备查）</summary>

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

**⚠️ 标注**：裁决＝**改法 ①**（2026-09-09 Shao Peishen），已落地。原标注「需
Shao Peishen 拍板改法、⛔ 执行方不得自行改动 `session.py` 的状态机行为」已经兑现——
拍板已完成，改动依裁决执行，⛔ 不再是待拍事项。

</details>

## TD-21 · 值守服务的礼貌回复是 at-most-once，崩溃即丢

**现象**：`tools/liaison/inbound.py` 的礼貌回复走注入的 reply port，不是
`effect_*`、没有 outbox 行。台账已提交、回复还没发出去时进程被杀，这条回复
永久丢失——重投同一 `msgid` 时归档幂等命中，不会再触发回复。

**为什么当时这么做**：做成幂等 effect 需要一张 outbox 表；第 4 章 opener
约束 5 明确"⛔ 不改第 2 章的表结构（需要加列/加表则登记偏离并停在该点）"，
而真实外发通道本来就在第 6／7 章。

**影响面**：丢的是一条"您不在受理名单内"的告知。⛔ 不丢材料（归档已落）、
⛔ 不丢待办（名单外本来就不入队）。方向安全。

**还债动作**：第 6／7 章落真实外发通道时，把回复改成带 outbox 行的
`effect_*`（幂等键 `{thread_id}:effect_reply_notice:{msgid}`），并在
`EFFECT_NODE_TO_TABLE` 登记。届时删掉本条。

## TD-22 · `msgid` 禁含 `_` 的规则未在真实企微 msgid 上验证过字符集

**欠的是什么**：`tools/liaison/archive.py::_validated_key`（`forbid_underscore=True`
那一支）拒收任何含 `_` 的 `msgid`——这是让 `<msgid>__<文件名>` 这条拼接
可逆的必要条件（详见该函数 docstring 的推导）。但**企微 aibot 的 `msgid`
是不透明字符串，字符集从未被真实流量验证过**，只是"看起来"像 base64url——
而 base64url 的字母表本身就含 `_`。这条规则目前完全建立在假设上。

**症状会是困惑而不是显而易见**：`compute_archive_path` 只在
`if attachment is not None` 分支下才会被调用（见 `archive_message`）。
一旦真实 `msgid` 含 `_`：
- **纯文本消息照常归档成功**（不走这条路径）；
- **每一条带附件的消息**都会在 `compute_archive_path` 里抛
  `ArchivePathError`，一路冒出 `archive_message` → `handle_inbound_message`，
  连**名单外的礼貌回复**也发不出去（不止是名单内候选人受影响）。

排障的人看到的现象是"带附件的消息全部失败、纯文本正常"，第一反应大概率
是查通道/网络，而不是去查 `msgid` 的字符集——症状与根因隔了一层。

**为什么当时这么定**：round 1/2 的两条局部规则都被对抗性测试
（`test_no_two_adversarial_msgid_filename_pairs_collide`）找出过碰撞漏洞，
"msgid 里禁止一切 `_`" 是唯一能让编码可逆、经得住批量对抗输入验证的规则。
第 4 章的 opener 约束不允许为了迁就假设中的 `msgid` 字符集而改 design D4
的路径形态——那需要先看到真实数据。

**还债动作（remediation，代码 docstring 已写明方向）**：如果第 7 章接通道
后发现真实 `msgid` 确实含 `_`，要改的是 **D4 的叶子路径形态**（例如把
`msgid` 单独放一段路径、不再靠 `__` 分隔符做单射编码），⛔ **不是**把
`_validated_key` 的检查放松成"清洗掉 `_`"——放松等于把「归档覆盖」
（同一路径落两份不同材料）的缝隙重新打开，`archive.py` 模块 docstring
与 `_validated_key` docstring 对此有逐字说明。

**触发条件**：第 7 章接通真实企微通道、**在真实消息流量到来前**，先把
真实 `msgid` 打进日志核对字符集是否含 `_`（不要等第一批真实带附件消息
批量失败才发现）。字符集确认不含 `_` ⇒ 本条注销；确认含 `_` ⇒ 按上面
的还债动作改 D4 叶子形态，然后注销。

**不还的后果**：第 7 章上线当天，如果真实 msgid 恰好含 `_`，带附件消息
会整批失败且外部看起来毫无规律（时好时坏取决于具体 msgid 内容），
且没有任何提前预警——这条 TD 就是那份预警。

---

## TD-23 `defer_task` 用「幂等键命中」推断状态，而不是读状态

**登记时间**：2026-09-09（第 5 章 run-build 收口，[Mac]0909D）
**位置**：`tools/liaison/queue.py` `defer_task` 的幂等短路分支
**级别**：不阻塞第 5 章（`defer_task` 当前**零生产调用方**，只有测试在调）

**成因**：`idempotent_effect` 的前置检查在被装饰函数体**之前**短路返回，所以对同一
`{thread_id}:effect_defer_task:{msgid}` 的第二次调用走不到存储层 TRIGGER。为满足 spec
「非法转移一律拒绝」，`defer_task` 把"幂等命中"当作"这行已经离开过 pending"的证据直接
抛 `TaskTransitionRejected`。

**缺陷**：「离开过 pending」**不等于**「现在不在 pending」。存储层只在
`NEW.send_status='deferred'` 时触发，`deferred → pending` 在 schema 上是**合法的**，
只是目前没有对应的业务函数。

**触发场景（第 6 章一旦加「取消暂缓/重新置为待发」就会踩到）**：
`defer(M)` 成功 → 撤销暂缓把 `M` 改回 `pending` → 再 `defer(M)` → 幂等键命中 → 抛
`TaskTransitionRejected` 并声称"此前已成功从待发转入过暂缓"。但此刻 `M` 就在 `pending`，
这次转移**完全合法**却被永久拒绝，且**该 msgid 的暂缓从此再也做不成**（幂等键永远命中）。
附带：该分支异常文案硬编码"从待发转入过暂缓"，在 `pending→deferred→pushed→再 defer`
路径下文案也会失真（拒绝结论仍正确）。

**还债动作**（二选一，⛔ 不要改成 `return False`——那与「非法转移一律拒绝」冲突）：
① 在 schema 加一条禁止 `deferred/pushed → pending` 的 TRIGGER，让上述推理真正成立；
② 在幂等命中分支**读一次当前状态**再决定抛什么。

**触发条件**：第 6 章要把 `defer_task` 接进任何调用路径之前。
**不还的后果**：第 6 章接线后，被撤销过暂缓的条目永远无法再次暂缓，且报错信息指向错误
的原因，排查会被带偏。

**为什么第 5 章不改**：本服务有强制结构测试 `test_no_checkpointer_or_langgraph_in_liaison`
钉死 `tools/liaison/` **不含 langgraph/checkpointer**，「节点从头重跑」的重放场景在此服务
不存在；且当前零生产调用方。终审 reviewer 独立核查确认该裁定依据成立。

---

## TD-24 `mark_task_pushed` 的推送时间戳只有 thread 级幂等保护，缺第二道防线

**登记时间**：2026-09-09（第 5 章 run-build 收口，[Mac]0909D）
**位置**：`tools/liaison/queue.py` `mark_task_pushed`
**级别**：不阻塞第 5 章（当前**零生产调用方**）

**成因**：队列条目的业务身份是**全局唯一**的 `msgid`（`liaison_task.msgid UNIQUE`），但
幂等键是 `{thread_id}:effect_mark_task_pushed:{msgid}`——**带 thread 前缀**。
`enqueue_task` 面对同一问题有 `msgid UNIQUE` 这道结构防线兜底（并有测试覆盖
「同一 msgid 由另一个 thread_id 投递」），`mark_task_pushed` **没有任何兜底**：
存储层的 TRIGGER 只拦 `→ deferred`，`pushed → pushed` 表层完全放行。

**触发场景（第 6 章「群通知外发」正是这个接缝）**：
msgid `M` 归档时 `thread_id = u_tang`（私聊），10:00 首次推送成功、`pushed_at = 10:00`；
第 6 章重试时若按**推送目标群**取 `thread_id = chat_xxx` 调
`mark_task_pushed(thread_id="chat_xxx", msgid="M", pushed_at="11:30")`
→ `effect_key` 不同 → 预检不命中 → UPDATE 真的执行 → **`pushed_at` 被改写成 11:30**，
`effect_log` 里 `effect_mark_task_pushed` 变成 2 行。
docstring「第一次推送的那个时刻才是事实」当场变假，且**没有任何症状**——
`effect_mark_task_pushed` 是 UPDATE 型、不进 `EFFECT_NODE_TO_TABLE`，恒等断言抓不到它。
现有 `test_mark_pushed_twice_is_an_idempotent_no_op` 只走同一个 `thread_id`，测不到这条。

**还债动作**（二选一）：
① 在 schema 加 `BEFORE UPDATE ... WHEN NEW.send_status='pushed' AND OLD.send_status='pushed'`
   的 `RAISE`（与 defer TRIGGER 同一手法，保持「存储层是唯一真源」）；
② `mark_task_pushed` 内部**从任务行读回 `thread_id`**，不由调用方传。

**触发条件**：第 6 章要调用 `mark_task_pushed` 之前。
**不还的后果**：推送时间戳被静默改写，审计上「第一次推送时刻」不再可信，且无告警。

## ~~TD-25~~ · 非限流错误也落 `pending_resend`，第 8 章重发驱动器会拿到永远重发不成的行 ✅ 已还（26986e8）

**登记时间**：2026-09-09（第 6 章 run-build 收口，[Mac]0909I）
**位置**：`tools/liaison/notify/webhook.py:247` → `tools/liaison/notify/store.py:106`
**级别**：不阻塞第 6 章（spec 只要求「被记录且不被当作成功」，当前实现不违规）

**成因**：`effect_deliver_with_backoff` 对**任何**未送达的结局都返回 `delivered=False`，
`store` 一律落成 `state='pending_resend'`。但 `93000`（机器人不在群）、`40001`（凭据失效）
这类错误重试多少次都是同一结果，与 `45009` 限流有本质区别。

**不还的后果**：`select_pending_resends` 是第 8 章重发驱动器的取数口径，这些行会被
反复取出、反复失败，且每失败一次可能再告警一次——变成一条**永远刷屏的死行**。

**还债动作**（二选一）：① 台账加一列区分「可自动重发 / 需人工介入」；
② 在 `select_pending_resends` 上按 errcode 过滤，只返回 `45009` 一类可重试的。
**触发条件**：第 8 章接重发驱动器之前。

**已还**（2026-09-09，`26986e8`，[Mac]0909V）：采用方案 ②（errcode 过滤），**未加列**，
理由见 `tools/liaison/notify/store.py::RETRYABLE_ERRCODES` 的代码注释——「能不能自动重发」
由 `last_errcode` 派生得出，而它已经在表里，加列就是把同一事实存两遍且不一致时无症状。
`RETRYABLE_ERRCODES = {45009}`（唯一有明文依据的瞬时错误）；`93000` / `40001` /
`last_errcode IS NULL` 归「需人工介入」，由新增的只读 `select_manual_intervention_resends`
原样取得，⛔ 不变成沉默行。两个口径构成对 `pending_resend` 的一个划分，有测试守着。
⏸ 重发驱动器本身仍属第 8 章，本次 ⛔ 未接。

## TD-26 令牌桶无进程级单例，且降级投递第一步 2 次 HTTP 只扣 1 个令牌

**登记时间**：2026-09-09（第 6 章 run-build 收口，[Mac]0909I）
**位置**：`tools/liaison/notify/webhook.py:286`（`bucket` 由调用方注入）、`webhook.py:184-196` + `:236`
**级别**：不阻塞第 6 章（本章零接线代码，两条都要到第 8 章接线才可达）

**成因**：两处独立缺口，后果相同——**实际发送速率能突破 D9 的 20 条/分钟**：
① `bucket` 是 `send_group_notify` 的参数，没有进程级单例。两个调用方各造一个
   `make_group_webhook_bucket()` 就是两份配额，速率直接翻倍。
② `DegradedDelivery` 第一次 `send_next()` 发出**两次** HTTP（`post_multipart` 上传附件
   + `post_json` 发文件消息），但外层只 `bucket.acquire()` 一次。降级通知在压力下
   以约 1.5 倍配额打服务端。

**不还的后果**：主动限流的全部意义就是「不靠被平台打回才知道」。突破配额后又回到
被 `45009` 打回、走退避重试的老路，而这正是本章要消灭的状态。
**还债动作**：① 第 8 章接线时用模块级单例并加断言；② 令 `send_next()` 自报本次要发几个
请求，由 `effect_deliver_with_backoff` 按数取令牌。
**触发条件**：第 8 章给群通知接上真实调用方之前。

## ~~TD-27~~ · `make_group_webhook_delivery` 对 `MODE_REJECT` 静默降级，会发出一条空 markdown ✅ 已还（26986e8）

**登记时间**：2026-09-09（第 6 章 run-build 收口，[Mac]0909I）
**位置**：`tools/liaison/notify/webhook.py:199-203`
**级别**：不阻塞第 6 章（`store` 提前短路，当前不可达）

**成因**：docstring 写着「⛔ `MODE_REJECT` 到不了这里（store 提前短路）」，但真到了这里
不是报错，而是**静默走 `DirectDelivery`**——`plan.body` 在 reject 模式下为空，结果是往群里
发一条 `body=""` 的空 markdown。「到不了这里」这个前提由**调用方**保证，而不是由结构保证。

**不还的后果**：将来有人从别处调 `make_group_webhook_delivery`（它是公开函数），
「拒发」会静默变成「发一条空消息」——比拒发更糟，因为它看起来成功了。
**还债动作**：`MODE_REJECT` 分支改成 `raise ValueError`，并补一条测试。
**触发条件**：`make_group_webhook_delivery` 出现第二个调用方之前。

**已还**（2026-09-09，`26986e8`，[Mac]0909V）：`MODE_REJECT` 分支改为 `raise ValueError`
（文案点名"拒发模式不产生投递对象，调用方必须提前短路"），
`test_notify_webhook.py::test_reject_mode_refuses_to_produce_a_delivery_object` 断言它真的抛
且抛在任何 HTTP 之前。`store` 那条提前短路是正路，⛔ 未动。

## TD-28 `liaison_group_notify` 的 CHECK 只守字段取值域，跨字段的荒唐组合能写进去

**登记时间**：2026-09-09（第 6 章 run-build 收口，[Mac]0909I）
**位置**：`tools/liaison/storage/schema.py` `GROUP_NOTIFY_SCHEMA`
**级别**：不阻塞第 6 章（现有代码路径产不出这些行）

**成因**：表注释宣称「结构（主键）与机制（idempotent_effect）两道防线守同一件事」，
但 CHECK 实际只约束了各字段各自的取值域。review 期用直连 SQL 逐条**实证写入成功**：
- `state='sent'` + `mode='reject'`（"被拒发的通知已送达"）
- `state='rejected'` + `mode='direct'` + `attempts=99`
- `byte_length=-5` / `limit_bytes=-1` / `attempts=-3`

**还债动作**：补 `CHECK ((state='rejected') = (mode='reject'))` 与
`CHECK (attempts >= 0 AND byte_length >= 0 AND limit_bytes > 0)`。

**附带两条本章 blocking 修复引入的、已判可接受的后果**（⛔ 不是缺陷，登记备查）：
① 主键改成 `(thread_id, digest)` 后 `digest` 不再全局唯一也无单列索引——第 8 章若要
   "按 digest 单独查一条"会全表扫描。当前唯一取行路径 `select_pending_resends` 走
   `idx_liaison_group_notify_state`，不受影响。
② `sent_at` 从微秒降到秒级（与 `created_at` 的 `datetime('now')` 对齐的必然结果），
   同一秒内多条通知在 `sent_at` 上不再可分辨，排序另有 `thread_id, digest` 兜底。

## TD-29 第 6 章包边角：再导出面不对称、死代码、测试脚手架三处复制粘贴、异常文案错位

**登记时间**：2026-09-09（第 6 章 run-build 收口，[Mac]0909I）
**级别**：不阻塞第 6 章，全部是可读性/可维护性

四条一起登记（都很小，建议一次还完）：
1. **`tools/liaison/notify/__init__.py` 再导出面不对称且不完整**：`STATE_*` 导出而 `MODE_*` 不导出；
   `transport.py` 一个名字都没导出（调用方从包根拿不到 `WebhookTransportError`）；
   `NotifyRecord` / `effect_send_group_notify` / `GROUP_NOTIFY_THREAD_ID` 缺席；
   `AIBOT_CHANNEL` 导出但生产代码零消费者。
2. **`tools/liaison/notify/guard.py` `NotifyPlan.is_send` 是死代码**（全仓零引用，含测试）。
3. **6 个子代理各造一份测试脚手架**：`FakeClock`（`test_notify_ratelimit.py` / `test_notify_webhook.py`）、
   `RecordingSink`（`test_notify_store.py` / `test_notify_webhook.py`）、`FAKE_WEBHOOK`
   （`test_notify_transport.py` / `test_notify_webhook.py`）三处复制粘贴，建议收进 `tests/conftest.py`。
   另 `test_notify_store.py` 有两条写库用例末尾漏调 `assert_group_notify_identity(conn)`
   （`test_effect_key_is_thread_node_digest` / `test_pending_resends_are_listable`）——
   其余七条都调了，覆盖不缺口，但本章自带判据是唯一防线，建议补齐。
4. **`tools/liaison/config.py:75` 复用 `MissingCredentialsError` 带来文案错位**：该异常消息逐字是
   「HR 值守通道**拒绝启动**：…」，而 `load_group_webhook` 的 docstring 正好在论证它
   **不是**启动期检查。运维在服务正常运行、只是群通知发不出去时，会收到一句说服务拒绝启动的告警。

---

## ~~TD-30~~ 日志留存期只有容量上界，缺时间维度的清理 ✅ 已还（ac686d1）

**2026-09-09 已处置（`0909T`）**：按下方「还债动作」逐字落地。新增独立的
`HR_LIAISON_LOG_RETENTION_DAYS`（默认 **30**，非法值走 `RetentionConfigError` 同一套
fail-closed），`run_cleanup` 里按 **mtime** 清 `data/liaison/logs/*.log.*`。
⛔ **没有**复用 `HR_LIAISON_RETENTION_DAYS`（180）——D13 明写两者刻意不对齐，
`test_log_retention_days_has_its_own_env_var` 把这条钉死。
三处刻意的收窄：① ⛔ 不删当前活动日志 `liaison.log` 本体（`RotatingFileHandler`
正攥着它的 fd，unlink 之后日志会静默进黑洞直到下一次轮转）；② ⛔ 不递归子目录、
⛔ 不跟符号链接；③ 日志那一遍与归档那两遍**完全解耦**——一条读不出来的
`attachments_json` 会停住归档清理，但 ⛔ 不该连坐日志（两者之间没有任何引用关系）。
清理目录取自 `logsetup.resolve_log_dir()`，与 `setup_logging` 真正写日志的目录同一个
真源，⛔ 不在清理侧另读一遍环境变量。

**登记时间**：2026-09-09（第 8 章 8.4 run-build 收口，[Mac]0909M）
**位置**：`tools/liaison/logsetup.py`（缺的动作不在任何文件里）
**级别**：不阻塞 8.4（容量上界已满足，磁盘写不满）

**欠的是什么**：design D7 从 `runtime-observability` 借的是三条做法——有界轮转文件、
个人信息脱敏、**留存期有上限**。8.4 的 opener 只写了前两条，本章按 opener 范围做，
未擅自扩大。`RotatingFileHandler(maxBytes, backupCount)` 让磁盘占用 ≤
`maxBytes × (backupCount + 1)`（默认 5 MiB × 6 = 30 MiB），
所以「日志占用的存储空间 MUST 有明确上界」这条**是满足的**。

**不满足的是时间维度**：「超过留存期的日志 MUST 被清理」。低流量时一份含个人信息的
历史日志可以躺很久——`.gitignore:17` 逐字写明「日志是个人信息的第二份拷贝」，
躺得越久，PIPL 下的暴露面越大。这不是磁盘问题，是留存期问题。

**还债动作**：在 8.1 的留存清理任务里加一条按 mtime 清理 `data/liaison/logs/*.log.*`，
用**独立的** `HR_LIAISON_LOG_RETENTION_DAYS`（默认 **30**）。
⛔ 不要复用 `HR_LIAISON_RETENTION_DAYS`（180）——D13 明写了两者刻意不对齐：
日志是运行证据，归档是工作材料，两者的留存期本就不同。

**触发条件**：8.1 留存清理落地时顺带做；最迟不得晚于 8.6 灰度真发
（那之后日志里开始出现真实候选人内容）。
**不还的后果**：含个人信息的历史日志无限期驻留，PIPL 的最小必要与留存期限要求失守，
且**没有任何症状**——磁盘不会满，监控不会响。

---

## TD-31 带分隔符渲染的手机号不脱敏

**登记时间**：2026-09-09（第 8 章 8.4 final review，[Mac]0909M）
**位置**：`tools/liaison/logsetup.py` `_VALUE_PATTERNS` 的手机号正则
**级别**：不阻塞 8.4（**实测当前不可达**）

**成因**：手机号正则认的是 11 位连写（`+86`/`0086` 前缀已在 Task 1 fix round 覆盖），
带分隔符或全角的渲染整条失配。final reviewer 实测：

```
'138-1234-5678'     -> 原样
'138 1234 5678'     -> 原样
'138.1234.5678'     -> 原样
'+86-138-1234-5678' -> 原样
'１３８１２３４５６７８'      -> 原样（全角数字）
```

**为什么现在不修**：reviewer 逐条读了全部 12 处非测试 `logger.*` 调用点
（`inbound.py:139,151`、`alerts.py:60,114`、`session.py:233,236,293`、
`session_client.py:104`、`whitelist.py:105,122`、`__main__.py:110,195`），
**没有一处记录候选人键入的消息正文**——`alerts.LoggingAlertSink.send` 收到的
只有系统合成的中断告警文本。此刻无真实泄露面。

**触发条件**：任何一处调用点开始把消息正文／候选人自填内容写进日志之前。
**不还的后果**：候选人手抖用了分隔符格式的手机号即明文落盘，且**无症状**——
脱敏看起来在工作（同一行里的邮箱照样打码），只有这一种渲染漏。

---

## TD-32 JSON/全角冒号渲染下 `thread_id` 反被打码，归档链路可能对不上

**登记时间**：2026-09-09（第 8 章 8.4 Task 1 review 裁决，[Mac]0909M）
**位置**：`tools/liaison/logsetup.py` `_PROTECTED_SPAN_RE`
**级别**：不阻塞 8.4（**实测当前不可达**）

**成因**：保护段只认 `key=value` / `key:value` 且分隔符紧贴键名。键名带引号或用全角冒号时
整条失配，于是取值落进脱敏区：

```
'{"thread_id": "13812345678"}' -> '{"thread_id": "<redacted:phone>"}'
'thread_id：13812345678'        -> 'thread_id：<redacted:phone>'
```

`thread_id` 私聊时取的就是 `userid`，而企微后台允许把 userid 配成手机号——这不是假想。
一旦被打码，那条日志就再也和 `liaison_message.thread_id` 对不上，排障链路断掉。

**为什么当次不修（裁决留痕）**：修法是让保护段也认引号/全角冒号，
方向＝**扩大明文豁免面**，与合规红线反向。无人值守场景下按「保守方向」裁定不修。
现网两处调用点（`inbound.py:141,152`）均为 `"thread_id=%s msgid=%s"` 无空格渲染，
实测 fix 前后字节一致，今天不触发。

**触发条件**：任何调用点改用 JSON/dict/`%r`/全角冒号渲染 `thread_id` 或 `msgid` 之前。
**不还的后果**：排障时日志里的 `thread_id` 与库里对不上，且**无症状**——
看起来只是"脱敏很尽职"。
⚠️ 还债时 ⛔ 不要顺手把 `sender_userid` 一起纳入保护名单，那是真的扩大明文面。

---

## ~~TD-33~~ ✅ 已还（25d8715） · `idempotent_effect` 的 `effect_log` INSERT 只兜 `IntegrityError`，其余异常不回滚

**登记**：2026-09-09 [Mac]0909K（8.1–8.2 留存清理 run-build，Task 2 实现者发现、终审复核确认）
**位置**：`app/storage/idempotency.py`（第二个 `try` 块）
**范围**：⚠️ **不是本单元引入的**，是共享基础设施的既有缺口，`effect_archive_message` / `effect_enqueue_task` 等**所有**既有 effect 同样受影响。

第二个 `try`（写 `effect_log`）只 `except sqlite3.IntegrityError`。若该 INSERT 抛的是**别的**异常
（磁盘满的 `OperationalError` 是最现实的一种），异常直接向上抛，**中间不做 `conn.rollback()`**——
于是 `fn` 已经完成的业务写就那样悬在连接**尚未提交的事务**里，直到某个不相干的后续 `commit()`
把它悄悄一起带走。

**为什么当次不修**：`app/` 在本交付单元的文件范围之外（opener 与计划都写死 ⛔ 不碰 `app/`），
且改动会同时影响所有既有 effect 的失败语义，不该由一条泳道顺手改。

**触发条件**：下一次有人动 `app/storage/idempotency.py`，或第 8 章灰度（8.6）之前。
**不还的后果**：这正是铁律 1 要防的那类事故的近亲——`.51` 2026-08-10 / 08-12 两轮 `outbox` 丢失
是"业务写失败、幂等记录成功"，这条是"业务写成功、提交归属不明"。两者都**无症状**。

---

## ~~TD-34~~ · `test_file_appearing_after_the_scan_is_not_deleted` 并不能区分修复前后 ✅ 已还（ac686d1）

**2026-09-09 已处置（`0909T`）**：新写
`test_ledger_row_committed_after_the_scan_still_protects_its_file`，它对读顺序**真正敏感**。
两处改动缺一不可：① 那个文件在 `fake_iter` 调 `real_iter` **之前**就落盘，因此
**包含在返回的候选快照里**（旧用例的致命处正是它不在快照里，而「不在快照里的文件
不会被删」是恒真的）；② 它的台账行在**扫盘之后**才提交（`_archive` 内部 commit），
模拟 `archive_message` 先写文件后写台账行的真实顺序。

**自证（⛔ 未用 `git stash`，CLAUDE.md 并行铁律禁止）**：用
`git worktree add --detach` 在修复前的 `1f2d018` 上开一个一次性 worktree，把新旧两条
用例的等价探针一起丢进去跑，实测 **新用例 FAILED（`deleted_files` 里真的出现了
`u1/20260101/late__c.bin`，即 design D3 禁止的「台账已记、材料缺失」）、旧用例 PASSED**，
与本条登记的判断逐字吻合。探针与该 worktree 已删除，⛔ 未进版本管理。
旧用例**保留**并改了 docstring，说明它守的是另一件事（「不在快照里的文件不会被删」
这条自愈性质），⛔ 不再把它当 finding 5(a) 的回归测试。

**登记**：2026-09-09 [Mac]0909K（8.1–8.2 终审后 scoped re-review 实测发现）
**位置**：`tools/liaison/tests/test_retention.py`（该用例）

它是终审 finding 5(a)（`run_cleanup` 先建 `referenced` 后扫盘的读顺序竞态）唯一的回归测试，
但**对读顺序不敏感**：用例里的 `fake_iter` 先读真实目录列表、**之后**才把"迟到的"文件写到盘上再返回，
所以那个文件无论如何都不会出现在返回的列表里。re-reviewer 把这条用例原样丢进修复前的
checkout（`1f2d018`）跑，**通过**。

⚠️ **生产代码的修复本身是对的**，已被 re-reviewer 用另写的探针独立验证：探针在 `1f2d018` 上能复现
误删、在 `6c2a315` 上文件被正确保护。欠的只是"能咬住"的回归测试。

**为什么当次不修**：终审只有一轮修复波次，无第二轮；该项经裁定**不承重**
（无下游任务依赖它，且生产行为已独立验证正确），故 park 并转成技术债。
**触发条件**：下一条泳道再动 `run_cleanup` 的读顺序之前（8.3/8.4 或 8.6 灰度）。
**不还的后果**：未来某次重构悄悄把读顺序改回去，整套测试仍然全绿——
症状是"归档文件在台账行还没写完时被删掉"，即 design D3 明令禁止的那个中间态。

---

## TD-35 · `assert_effect_log_identity` 对 `liaison_task` 的严格恒等与裁决一冲突

**登记**：2026-09-09 `0909T`（还冲突 B / 裁决一时当场发现）
**位置**：`tools/liaison/tests/test_liaison_effects.py` 的 `assert_effect_log_identity`
（第 775 行那个 `if table == "liaison_message":` 收窄）
**级别**：不阻塞 8.6 灰度（队列侧的账目已有等价强度的替代断言，见下）

**欠的是什么**：8.1–8.2 终审的 finding 2 把「清理会让业务表行数变少」这条豁免
**刻意收窄**到只对 `liaison_message` 成立，理由逐字是「`liaison_task` 从不被清理删除
（opener 约束 2）」。裁决一（2026-09-09）推翻了那个前提——终态（`pushed`）且超期的
队列行现在会被连带清掉，于是在**被清理过的 thread** 上，
`effect_enqueue_task` 的 `effect_log` 行数必然大于 `liaison_task` 的行数，
`assert_effect_log_identity` 会因为一个**完全正当**的理由变红。

**当次为什么不改**：那个文件由别的泳道持有，`0909T` 的 opener 明确列了可动文件清单
（⛔ 只动 `retention.py` / `logsetup.py` / 两个对应测试 / 两份规格 / TD-30·34 两段），
擅自改一个共享的机器守卫会和并行泳道撞在同一个函数上。

**当次的兜底（⛔ 不是"没管"）**：
1. `test_retention.py` 的 `assert_retention_accounting` 加了**第二条记账等式**
   ——`入队 effect 行数 == 存活队列行数 + 连带已清的队列行数`，右边那一项完全从
   `effect_log`（一行不删）推出来：既留下过 `effect_enqueue_task` 又留下过
   `RETENTION_DELETE_NODE` 的那些 `business_key`。⛔ 不是宽松判据，强度与原恒等式相当。
2. `test_identity_assertion_does_not_yet_account_for_a_cleaned_queue_row` 用
   `pytest.raises(AssertionError)` 把这个缺口**钉成可见的**，⛔ 不让它静默。

**还债动作**：把 `assert_effect_log_identity` 的豁免从「只对 `liaison_message`」放开到
「`liaison_message` 与 `liaison_task` 都对 `cleaned_threads` 豁免」，
并在 docstring 里把 finding 2 的理由更新成裁决一之后的口径。
🔴 ⛔ **不许**顺手把它削弱成总数比较或「约等于」——那是原 docstring 明令禁止的，
它是铁律 1 唯一的机器守卫。⛔ 也不许删掉
`test_identity_assertion_still_catches_a_break_in_an_uncleaned_thread`。
还完之后 `test_identity_assertion_does_not_yet_account_for_a_cleaned_queue_row` 必须
**改成正断言**（直接调 `assert_effect_log_identity(conn)` 且通过），⛔ 不许删掉了事
——删掉就等于把这个缺口重新变成静默的。

**触发条件**：下一条持有 `test_liaison_effects.py` 的泳道；最迟不得晚于 8.6 单机灰度
（灰度会真的产生终态队列行，那之后这条守卫的覆盖缺口开始有实际影响）。
**不还的后果**：`effect_enqueue_task` ↔ `liaison_task` 这一对在被清理过的 thread 上
不再有任何断言检查——那正是铁律 1 的核心不变式，而缺口是静默的。

## ~~TD-36~~ · TD-19 一旦还上，两条凭据用例会带着假凭据向企微发起真实建连 ✅ 已还（`0909AC`，与 TD-19 同一 commit）

**欠的是什么**：`test_liaison_credentials.py` 的
`test_entrypoint_succeeds_when_credentials_present` 与
`test_entrypoint_reads_dotenv_when_process_env_is_absent` 用 `sys.executable` 起真实子进程，
喂进去的是**假凭据**（`bot-1`/`sec-1`、`bot-from-file`/`sec-from-file`）。今天它们安全，
纯属**运气**——进程走到 SDK 表面校验就被 `SdkSurfaceUnverifiedError` 拦住（`exit 4`），
在任何网络动作之前。

TD-19 还上之后这道拦阻消失，同样两条用例会让子进程带着假凭据**真的去连企微 WebSocket**。
后果：跑一次本地测试就向企微服务端发起若干次认证失败的连接；在 `KeepAlive` 之外也构成
对外部服务的非预期请求，且**测试从此依赖网络可达**（离线时转红，原因与被测行为无关）。

**触发条件**：TD-19 落地的**同一个变更**里必须一并处理，⛔ 不能等它响。
建议方向（未裁决）：给入口加一个只做启动期自检、在建连前退出的开关，让这两条用例走它；
或在用例侧把 SDK 构造点 monkeypatch 掉。⛔ 不要靠"假凭据反正连不上"来免责——
连不上也是发出去了。

**不还的后果**：静默。测试照常绿，没有任何报错会告诉你它刚才对着企微生产端点做了几次
失败认证。发现它的时刻通常是外部限流或安全告警，而不是测试失败。

**来源**：`[Mac]0909AA` 在裁决甲类两条红时顺带识别（Shao Peishen 2026-09-09 批准登记）。
相关：TD-19、`docs/findings/2026-09-09-tools-venv-建立后四条测试转红.md`

### ✅ 2026-09-09 已还（`0909AC`），两层各管一半

**① 被测路径改走启动期自检**：入口新增 `--self-check`（`__main__.SELF_CHECK_ARG`）——把
「读 .env → 校验凭据 → 造连接对象 → 核 SDK 表面 → 接事件」整条路径**原样跑完**，在
`run_forever` 之前返回 0。⛔ 它**不跳过任何一项校验**（守护断言
`test_self_check_runs_the_whole_startup_path_then_stops_before_connecting`：表面对不上时它
照样以 exit 4 拒绝）；它跳过的只是唯一会碰网络的那一步。
⛔ **不许写进 launchd plist**（会让服务每次拉起就 exit 0、值守通道从此不存在且无症状），
守护断言 `test_plist_never_runs_the_self_check_mode`。

判据同时**变严**了：旧断言只说「不是 exit 2」，进程实际停在哪靠下游某一关碰巧拦住；
新断言直接要求整条自检走通（装了 SDK ⇒ exit 0）。**期望值是测出来的、⛔ 不是写死的**
（`_sdk_available_to_subprocess()`）——把"跑测试的解释器装没装 aibot"写进断言正是
`docs/findings/2026-09-09-tools-venv-建立后四条测试转红.md` 记的那个坑。

**② 加了一道机器判据**：`tools/liaison/tests/netguard/`——一个 `sitecustomize.py` 闸门，
非回环的 `getaddrinfo` / `connect` / `connect_ex` 一律 raise。进程内由 conftest 的 autouse
fixture 装上，子进程由 `netguard_support.subprocess_env()` 塞进 `PYTHONPATH`（`site` 在
解释器启动时自动 import）——**两条缺一不可**，因为要防的那条路径跑在子进程里。
于是「测试不触网」从"我看了一遍觉得没有"变成了一条会红的断言。

⚠️ **落地过程中实测到一次真实外发，已单独落档**：闸门第一版按主机名放行回环，而本机
`HTTPS_PROXY=http://127.0.0.1:<port>`——建连打到回环、被放行、由代理转发到企微，
企微回了 `errcode=853000`。只清 `*_PROXY` 环境变量**不够**（`websockets` 走
`urllib.request.getproxies()`，macOS 上还读系统代理设置）。现已连
`getproxies`/`proxy_bypass` 一起摁掉，判据用例也加了 **websockets 栈**的第二个探针。
详见 `docs/findings/2026-09-09-测试网络闸门被本机代理绕过.md`。

**验收实证**：`tools/liaison/.venv` 里 **750 passed / 0 failed**（基线 741）；根 venv 全量
**2005 passed / 5 skipped / 0 failed**。

## TD-37 · `client.run()` 吞掉 KeyboardInterrupt，Ctrl-C 停不下值守服务（要按两次）

**欠的是什么**：`aibot==1.0.2` 的 `WSClient.run()` 自己 `except KeyboardInterrupt:` →
`self.disconnect()` → **正常返回**（`client.py:344-360`）。TD-19 把它接进
`session_client.run_forever` 之后，Ctrl-C 的效果变成：`run()` 静默返回 → 外层当成
"连上后又断了" → 退避 1 秒 → **拿一个全新对象重连**。服务不停。

第二次 Ctrl-C 若落在那 1 秒的 `sleep()` 窗口里，`KeyboardInterrupt` 就能穿过
`run_forever`（它只 catch `Exception`）到达 `main()` 的 `except KeyboardInterrupt`，
线程正常收尾。所以现象是"**要按两次、且第二次得按在退避窗口里**"。

**触发条件**：本条**不必单独排期**。⚠️ 但凡出现下面任一情形就要还：
① 有人在 Terminal 手工跑值守服务并抱怨"Ctrl-C 停不掉"；② 要给服务加优雅停机
（flush、闭合当前窗口再退）——那时"停机信号到不了 `main()`"会从麻烦升级成缺陷。

**不还的后果**：**不静默、后果有限**。生产路径不受影响——launchd 用 SIGTERM，
Python 默认处理直接终止进程，⛔ 不经过 `run()` 的那个 `except`。受影响的只有人工
前台调试，且症状**当场可见**（按一次没停），⛔ 不是"看起来正常其实没工作"那一类。

**已考虑但未做的改法**（留给还它的人，⛔ 不要当成结论）：在 `connect_once` 外面临时
换 `signal.signal(SIGINT, ...)`、置一个标志位、`run()` 返回后补 `raise KeyboardInterrupt`。
代价是在库函数里动进程级信号处理，且只在主线程成立。⛔ `0909AC` 判定它超出该泳道
范围（opener「范围硬界定」），故只登记不动手。

**来源**：`[Mac]0909AC` 写 TD-19 适配时读 SDK 源码发现（⛔ 不是推测，`client.py`
第 344-360 行原文）。相关：TD-19、`docs/findings/2026-09-09-aibot-wsclient-表面实测.md`

## ~~TD-38~~ · `heartbeat_interval` 单位错配：传秒当毫秒，心跳频率 ×1000，44 秒即被企微限流 ✅ 已还（`840f5cf`，`0909AF`）

**欠的是什么**：`tools/liaison/session_client.py:37` 的 `DEFAULT_HEARTBEAT_SECONDS = 30`
被 `build_ws_options`（同文件 118–131 行）原样传给 SDK 的 `heartbeat_interval=`，而
`aibot==1.0.2` 的这个参数**单位是毫秒**（`types.py:49`：`heartbeat_interval: int = 30000`，
docstring 明写「心跳间隔（毫秒），默认 30000」；`ws.py:299` 实际 `asyncio.sleep(interval/1000)`）。
于是 30 秒被解读成 **30 毫秒**，心跳频率放大 **1000 倍**。

**实证**（⛔ 不是推测，`[Mac]0909AE` 现网实测）：建连后 44 秒内发出 **1399** 次心跳
（契约应为 1–2 次），日志首行即 `Heartbeat timer started, interval: 30ms`；
21:26:33 企微返回 `errcode 45009 Too many requests`，SDK 随即
`No heartbeat ack received for 2 consecutive pings, connection considered dead`。
全文与统计见 `docs/findings/2026-09-09-首次真实建连实测.md`。

**不还的后果**：🔴 **值守通道无法保持在线**，且每次拉起都在对企微生产端洪泛
（≈32 次/秒）。**装了 launchd 会放大**：无人值守地反复重启 → 反复洪泛 → 反复吃 45009，
存在 bot 凭据被限流甚至封禁的风险。⛔ **TD-38 未还前不得装 launchd。**

**触发条件**：**立即**，8.6 的阻断项。

**已考虑但未做的改法**（留给还它的人，⛔ 不要当成结论）：把常量改名成毫秒口径并按毫秒传
（如 `DEFAULT_HEARTBEAT_MS = 30_000`），⛔ 不要只把取值从 30 改成 30000 而留着
`_SECONDS` 的名字——那正是本条的成因。还它时**必须一并复核 `reconnect_interval`**：
SDK 默认 `1000`（毫秒 = 1 秒），而 `session_client.py:22-25` 的注释称
`MAX_BACKOFF_SECONDS = 30.0` 与「SDK 内置退避的封顶取同一个数」，那是在两种单位下比的，
结论不成立（本模块当前没传该参数，故未爆）。

**为什么表面校验没拦住**：`verify_client_surface` 守的是方法名、事件名、`run` 的签名与
协程性，全部通过。这是一个**类型相同（int）、单位不同**的参数错配 —— 按设计就在那道护栏的
盲区里，且**无任何本地症状**，只有真连上企微才暴露。这也是"必须真跑一次"的价值所在。

**来源**：`[Mac]0909AE` 首次真实建连实测。相关：TD-19、TD-39、
`docs/findings/2026-09-09-首次真实建连实测.md`

**怎么还的**（`840f5cf`，`[Mac]0909AF`，Shao Peishen 2026-09-09 答 `1a` 授权）：
- `DEFAULT_HEARTBEAT_SECONDS = 30` → `DEFAULT_HEARTBEAT_MS = 30_000`（`session_client.py`），
  常量处写明单位与 SDK 契约出处；`build_ws_options` 的关键字默认值同步改名。
- 原断言 `assert options.heartbeat_interval == session_client.DEFAULT_HEARTBEAT_SECONDS`
  是**同义反复**——拿传进去的值跟它自己比，单位错成什么样都绿，**这正是本条活到真实建连
  才暴露的原因**。改成绝对值 `== 30_000`，并**先确认它在修复前真的红**
  （带真 SDK 的 `tools/liaison/.venv` 里报 `assert 30 == 30000`）再动实现。
- 补 AST 级钉子 `test_ws_options_source_pins_the_heartbeat_interval_to_milliseconds`：
  上面那条靠 `importorskip("aibot")`，根 venv 按 design D10 不装 SDK ⇒ 恒 skip、**挡不住回退**；
  AST 钉子不依赖 SDK，钉死 `DEFAULT_HEARTBEAT_MS` 必须是字面量 `30_000` 且必须带 `_MS` 名，
  断言消息里写明**为什么是 30000 而不是 30**（否则下一个人只会觉得这个数很怪，顺手"修"回 30）。
- `reconnect_interval` 一并复核（同形状、未爆）：只更正 `session_client.py:22-25` 的注释——
  SDK 的封顶是**毫秒**口径且本模块**根本没传**该参数，「两层取同一个数」的说法作废。
  ⛔ **没有**顺手加传参（那会改变重连行为，超出本条范围）。

⚠️ **销账 ≠ 已验证**：根 venv 跑不到真 SDK，本条能验的到此为止。
**心跳是否真的变成 30 秒，需 `[Mac]0909AG` 真实建连确认**——在那之前，
「⛔ TD-38 未还前不得装 launchd」这条闸门按 Shao Peishen 答 `3a` **继续有效**。

## TD-39 · ⚠️ 待复核：SDK 判连接死亡后，断线事件疑似没到 `LiaisonSession`

**疑点是什么**：`[Mac]0909AE` 实测中，SDK 在 21:26:33.988 打出
`connection considered dead` 之后 **16 秒**，`data/liaison/liveness.json` 仍是
`{"state": "connected", ...}`（`stamp_at` 21:26:49.898 说明值守线程还在正常盖戳），
`liaison_outage_window` 表**一条记录都没有**，日志里也没有任何 `disconnected` /
`reconnecting` 行。若属实，这正是 `make_sdk_connect` docstring 点名要消灭的静默故障：
**中断窗口一条都不会有，"没有告警"被当成"一切正常"**。

🔴 **⛔ 本条尚未定性，不要当成已确认的缺陷**：观察窗只有 16 秒就被人工强杀了，SDK 的
`_ws.close()` → 接收循环收尾 → `on_disconnected`（`client.py:70`）这条链可能仍在途中。

**复核方法**（还 TD-38 之后再做，否则 44 秒就被限流、复现的是限流不是断线）：
心跳改对、连接稳定之后，**拔网线／关 Wi-Fi** 制造真实断线，观察 ①`liveness.json` 是否
在合理时延内翻成 `disconnected`；②`liaison_outage_window` 是否落一条窗口；
③ 恢复网络后是否自动重连并闭合该窗口。三项全绿则本条销账为"虚惊"，任一不绿则升级为缺陷。

**触发条件**：TD-38 还上之后、8.6 灰度验收之前。⛔ 不许跳过——它守的正是
"服务看起来在跑、其实早断了"这一类**无症状**故障。

**来源**：`[Mac]0909AE` 首次真实建连实测。相关：TD-38、
`docs/findings/2026-09-09-首次真实建连实测.md`

## ~~TD-40~~ · `.env` 里的 `HR_LIAISON_*` 三个键让整个 app 的配置加载不了 ✅ 已还（`0909AC`，改法 ③）

**欠的是什么**：`app/config.py:13` 是
`SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")`——pydantic-settings v2 的
`BaseSettings` **默认 `extra="forbid"`**。`0909AA`（2026-09-09）把
`HR_LIAISON_BOT_ID` / `HR_LIAISON_BOT_SECRET` / `HR_LIAISON_GROUP_WEBHOOK` 三个键写进仓库根
`.env` 之后，`Settings()` 每次实例化都抛
`ValidationError: Extra inputs are not permitted`，一次报三条。

**这不是"测试环境的小毛病"**：`Settings` 是 app 的配置入口 ⇒ **Web 服务在他本机起不来**。
`0909AC` 实测：有 `.env` 的仓库根 checkout 上根 venv 全量 **23 failed**
（`test_config.py` 5、`test_config_fallback.py` 6、`test_config_audit_and_outbound.py` 10、
`test_outbound_gate.py` 1、`tests/test_main_wiring.py` 1）；同一份代码在**没有 `.env`** 的
worktree 里 **0 failed**。

⚠️ **失败信息会把 bot_secret 明文打进 pytest 输出**（`input_value='...'`）——终端回滚、
CI 日志、粘给别人看的报错里都带着它。这条本身就构成一个泄漏面。

**为什么一直没被发现**：`.51` 上没有这三个键（`tools/` 不同步过去），CI 也不带 `.env`。
它**只在他这台机器上**发生，而那正是唯一会跑 Web 服务的机器。

**触发条件**：⚠️ **立刻**——他下一次起 Web 服务或跑全量 pytest 就会撞上，现在已经在撞了。

**三个改法（未裁决，Shao Peishen 拍）**：
① `app/config.py` 加 `extra="ignore"`——一行，但**放弃了「`.env` 里写错别字当场报错」这道岗**；
② 给 `Settings` 补三个 `hr_liaison_*` 字段——app 并不用它们，纯为让校验过关，语义上是脏的；
③ 把值守通道的凭据从根 `.env` 挪到 `tools/liaison/.env`（配合
`__main__.resolve_dotenv_path()` 改默认路径）——**与 design D10 的依赖隔离同构**，
两套配置各归各位，代价是他要挪一次文件。

**不还的后果**：Web 服务起不来，且报错指向的是 `.env` 里那三个**本来就该在那儿**的键，
很容易被读成"配置写错了"而去删凭据——删完值守通道又起不来。两边互相打架，
且**每次报错都回显一次 bot_secret**。

**来源**：`[Mac]0909AC` 收工前在仓库根 checkout 跑全量时实测（⛔ 不是推测，见上方失败计数）。
相关：TD-19、design D10

### ✅ 2026-09-09 已还——**裁决＝改法 ③**（Shao Peishen 当次答 `1c`）

值守通道的配置迁到 `tools/liaison/.env`，与 design D10 的依赖隔离同构：依赖在
`tools/liaison/requirements.txt`，配置在 `tools/liaison/.env`，两套各归各位，
`tools/` ⛔ 不进 `sync-to-server.sh` 的 SYNC_PATHS，都不会上 .51。

⛔ **没有选改法 ①（`extra="ignore"`）**：那会连带放弃「根 `.env` 里写错别字当场报错」
这道岗——而那道岗挡的是"配置看起来配了、其实键名拼错了"这类**无症状**故障，
比本条更难发现。

落地清单：

- `__main__.DEFAULT_DOTENV_PATH = LIAISON_DIR / ".env"`，`resolve_dotenv_path()` 据此取值。
  ⛔ **刻意不做"根 `.env` 兜底"**——兜底会让"键还留在根 `.env` 里"这个坏状态继续静默存在。
- 占位从根 `.env.example` **整段迁到** `tools/liaison/.env.example`。⚠️ 连**空占位**都迁走了：
  `extra="forbid"` 判的是键**在不在**，不是值空不空，而 `cp .env.example .env` 是最常见的
  触发路径。
- 缺凭据时 stderr 多打一行「已从 <path> 读取」——迁移之后"我明明配了啊"最可能的原因
  就是文件还在旧位置，⛔ 只打路径不打取值。
- README、plist 模板注释、`errors.py` 的提示文案三处指向同步更新。

两道回归岗：
`test_default_dotenv_path_is_the_liaison_dir_not_the_repo_root`（默认路径被改回仓库根即红）、
`test_root_env_example_does_not_carry_the_liaison_keys`（三个键回到根 `.env.example` 即红）。

**验收实证**：迁移前根 venv 全量 **23 failed**；迁移后 **2041 passed / 5 skipped / 0 failed**，
`Settings()` 正常实例化。liaison venv **786 passed / 0 failed**。
凭据本身已从根 `.env` 移入 `tools/liaison/.env`（权限 0600），⛔ 未留备份副本。
