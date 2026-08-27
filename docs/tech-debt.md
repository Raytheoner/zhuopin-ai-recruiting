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
