## Context

这是项目的第一个变更，除了交付 M1 的业务价值，还要立起后续 M2/M3 共用的四块地基：编排骨架、LLM 网关、企微通道、审计表。地基设计错了，后面每个变更都要付利息。

约束来自 `01-开源调研与技术选型.md` 与 `02-系统架构与MVP范围.md`：

- 编排层用 LangGraph + Postgres checkpointer。**恢复时节点从头整个重跑**（官方明确："restarts the entire node from the beginning"），这是最容易踩的坑
- 必须 `langgraph >= 1.0.10`（GHSA-g48c-2wqr-h844，CVSS 6.8，需攻击者已能写 checkpoint 存储，非 P0 但没理由不升）
- 企微回调只推一次、5 秒无响应即丢弃
- 模型型号尚未定型。调研给的型号已发现过时（Qwen3.5-Plus 已是旧版），W1 实测后才能定

## Goals / Non-Goals

**Goals**

- 跑通一条完整的"Agent 产出 → 人工断点 → 冻结 → 下游消费"链路，形态可被 M2/M3 直接复用
- 建立幂等约定，让"节点重跑"从此不再是隐患
- 建立审计留痕的统一结构，为 M2 的 PIA 做好承载

**Non-Goals**

- 不追求高并发。日均新增岗位个位数，不做连接池调优、不做水平扩展设计
- 不做通用工作流引擎。只服务招聘流程，不抽象成平台
- 不做前端框架选型的深度投入。M1 的交互主战场在企微卡片

## Decisions

### 决策一：副作用与纯计算严格分离

**做法**：LangGraph 图里的节点分两类。`compute_*` 节点只做 LLM 调用与数据转换，无副作用；`effect_*` 节点只做一件对外动作（发一条消息、写一次状态），且带幂等键。

**为什么**：这是对"节点从头重跑"的直接防御。已有公开踩坑案例——一个节点里同时 `create_ticket` 和 `send_email`，恢复后建了两张工单且看不出异常。

**幂等键构成**：`{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。`effect_*` 节点执行前先查该表，命中则直接跳过。

**替代方案**：在节点内部做状态判断（"如果已发送就跳过"）。否决——判断逻辑分散在各节点里，漏一个就出事，且无法审计。

### 决策二：LLM 网关自建薄封装，不用 LiteLLM 等聚合层

**做法**：一个约 200 行的薄封装，统一处理供应商切换、模型版本锁定、`temperature=0` 强制、结构化输出校验、调用留痕。

**为什么**：需要的功能很少，但**留痕是合规刚需**且格式必须由我们定（要落 `analysis_run` 表）。引入聚合层会让留痕逻辑绕一圈，且多一层版本兼容负担。至少接两家供应商，因为 W1 的实测结论可能推翻当前假设。

**替代方案**：LiteLLM。否决——为省 200 行代码引入一个持续维护的依赖，不划算。

### 决策三：结构化输出用 Schema 约束 + 校验 + 有限重试，不靠 prompt 祈祷

**做法**：画像 Schema 用 Pydantic 定义。优先用供应商的 `json_schema` 模式；不支持的供应商降级为 `json_object` + 本地 Pydantic 校验。校验失败重试至多 2 次，仍失败则转人工，**不产出半成品**。

**为什么**：调研中有一条未经验证但影响很大的说法——豆包支持 `json_schema` 而 DeepSeek/Qwen 仅支持 `json_object`。这条 W1 必须实测。设计上做成两条路径都能走，实测结果不论如何都不用改架构。

### 决策四：画像冻结后不可变，改动走新版本

**做法**：`job_profile` 表加 `version` 与 `status`。冻结即写死，修改产生新版本，旧版本保留。

**为什么**：下游（M2 的筛选、M3 的出题）都依赖画像。如果画像可以原地改，历史评分就失去了可解释性——"当时按什么标准筛的"会答不上来，这是 PIPL 说明权的硬伤。

### 决策五：企微回调先落库再处理

**做法**：回调接口只做签名校验 + 写入 `wecom_callback` 表 + 返回 200，全程控制在 5 秒内。业务处理由后台任务异步消费。

**为什么**：企微回调只推一次、5 秒不响应就丢弃。把 LLM 调用放在回调同步链路里必然超时。

**注意**：2022-06-20 后新建的企微应用，通讯录接口不再返回手机与邮箱。M1 只用到 userid、姓名、部门，不受影响；但 M2 需要候选人手机号时必须自建库，不能指望企微。

### 决策六：追问轮次设上限，超限降级而非死循环

**做法**：追问上限 5 轮，超限则用"未指定"填充并在确认卡片上显式列出。

**为什么**：业务经理的耐心是稀缺资源。一个追问到第 8 轮的机器人，下次就没人用了。宁可产出一份标注了缺口的画像，让人在确认环节一次性补齐。

### 决策七：断言四（human_review 留痕巡检）的豁免线改用 effect_log.applied_at，不用 job_profile.created_at

**背景**：`app/audit/assertions.py` 的 `assert_every_decision_has_human_review`（断言四）用 `HUMAN_REVIEW_ENFORCED_FROM` 划一条豁免线，原实现拿 `job_profile.created_at`（画像草案创建时刻）跟这条线比。但 `effect_confirm_profile` / `effect_abandon_profile` 是就地 `UPDATE status`，从不推进 `created_at`——凡是在豁免线之前创建、豁免线之后才被确认/放弃的草案，永远落在豁免侧，日后若漏写 `human_review`，断言完全看不见。2026-09-04 Shao Peishen 裁决「现在修」，见 `tasks.md` 6.x 落地偏离登记 Task 8 parked 条目。

**做法**：豁免判定改用该行**终态决策实际提交的时刻**——即写这条决策的 `effect_*` 节点在 `idempotent_effect` 装饰器里落 `effect_log` 那一刻的 `applied_at`（工程铁律 1 的产物：业务写与 `effect_log` 行同一事务提交，`applied_at` 就是决策真实发生的时刻，不是新造的机制）。

关联 key（本次核实清楚，不留给实现阶段猜）：

- `effect_key = f"{job_id}:{node_name}:{version}"`，与 `app/storage/idempotency.py::idempotent_effect`（:32）的幂等键格式逐字同源
- `job_id` = `job_profile.job_id`（同时是 `effect_confirm_profile`/`effect_abandon_profile` 调用时的 `thread_id`，见 `app/web/server.py` 的调用点）
- `version` = `job_profile.version`（INTEGER 列），落进 `effect_log.business_key`（TEXT 列）时是 `str(version)`（见 `app/web/server.py` 各处 `business_key=str(version)`）——**两列类型不同，比较时必须显式转换**（如 `CAST(e.business_key AS INTEGER) = p.version`），不要依赖 SQLite 的隐式仿射转换，否则在某些取值下静默匹配不上却不报错
- `node_name` 按终态 `status` 二选一，新增一张与既有 `TERMINAL_STATUS_DECISIONS` 并行的映射：
  ```python
  TERMINAL_STATUS_EFFECT_NODES: dict[str, str] = {
      "approved": "effect_confirm_profile",
      "abandoned": "effect_abandon_profile",
  }
  ```
  这张表与 `app/graph/nodes.py` 里两个 `@idempotent_effect(...)`（:233、:337）的字面量参数逐字同源，改一处必须同步改另一处——与 `DECISION_APPROVED`/`DECISION_ABANDONED` 那组常量已有的纪律（`nodes.py:20-24` 的注释）相同。

**查不到 effect_log 行怎么处理（fail-closed）**：若某条终态 `job_profile` 行按上述 key 在 `effect_log` 里查不到对应行（例如非经由 `effect_*` 节点写入的历史/测试数据），一律**按未豁免处理**——查不到决策时刻不能反过来当作"证明它发生在豁免线之前"。这与断言四本身"表不存在 → 判失败"的 fail-closed 取向一致：宁可多报一条需要人核实的违例，不可漏判。

**豁免计数同步改口径**：现有 `exempted` 计数（detail 文案「豁免 N 条」的来源，`app/audit/assertions.py:322-327` 附近）目前也是拿 `created_at` 与豁免线比，必须换成与上面同一套 `effect_log` 关联逻辑，否则"违例判定"与"豁免计数"用两套不同的时间基准，报出来的数字会自相矛盾。

**替代方案**：给 `job_profile` 加一列记录终态转移时刻（如 `terminal_at`）。否决——`effect_log.applied_at` 已经是这个事实的真源，另加一列是给同一个事实开第二个真源，且需要一次数据回填迁移；现有数据不缺这个信息，只是断言没在用它。

**`HUMAN_REVIEW_ENFORCED_FROM` 常量去留**：**不改名、不挪值**。这个名字说的是"人工决策留痕的强制线"，从未绑定"以草案创建时刻判定"这个语义——错的是紧邻它的比较逻辑（拿错了时间戳去比），不是这个名字本身。结论：保留常量与取值 `"2026-09-04 00:00:00"`，只需要更新它上方的注释，把"早于此刻创建的画像版本豁免"改为"早于此刻**做出决策**（`effect_log.applied_at`，非画像草案创建时刻）的画像版本豁免"，消除本次修复前遗留的隐藏歧义。

## Risks / Trade-offs

- **节点重跑导致重复副作用** → 幂等键 + `effect_log` 唯一索引；W7 安排专项测试，用"强制中断并恢复"的方式验证每个 `effect_*` 节点
- **W1 实测发现无合适模型，Schema 抽取方案要改** → 设计上 `json_schema` 与 `json_object` 两条路径都支持，实测结果不改架构；最坏情况切商用文档理解 API
- **checkpoint 表膨胀且官方无 TTL 清理** → 自写清理任务，归档已完成流程的 checkpoint。M1 数据量小不构成问题，但机制要在此变更中建好，否则 M2 上量后再补代价更大
- **业务经理不接受对话式交互，仍想填表** → M1 上线后观察真实使用率；若确实抵触，保留降级方案：Web 表单直接提交画像，跳过追问
- **画像质量的验收标准偏主观** → 用 10 个真实历史岗位重跑，由 HR 与对应业务经理双方评估技术栈字段准确率，目标 ≥80%

## Migration Plan

首个变更，无迁移。部署顺序：

1. Postgres 建库建表（含 checkpoint 表）
2. LLM 网关配置至少两家供应商凭据
3. 企微自建应用创建，回调地址配置为公网 HTTPS
4. 灰度：先由 1 位配合度高的业务经理试用 2 个真实岗位，再放开

**回滚**：M1 不影响任何现有流程（现状是 Excel + 微信/邮件），停用即回滚，无数据迁移负担。

## Open Questions

- 企微自建应用的申请与公网 HTTPS 回调地址由谁提供、需要多久？这是 W2 的外部依赖，若卡住需提前启动申请。（不影响架构与任务拆分，可并行推进）
- 岗位画像里"公司与团队介绍"部分的素材从哪来？一期先由 HR 提供一份模板，后续再考虑按部门差异化。
