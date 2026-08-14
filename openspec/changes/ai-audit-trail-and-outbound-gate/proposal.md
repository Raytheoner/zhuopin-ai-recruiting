## Why

两条合规底线目前在本仓库里都只有壳，没有实现：

**一、AI 评分留痕。** `app/llm/gateway.py` 的 `AuditHook` Protocol 已经把调用点留出来了，但默认实现 `NoopAuditHook` 只打一行 debug 日志，代码注释里自己写着"完整的 analysis_run 持久化是技术债"。这意味着工程铁律 3、4 目前一条都不成立：模型标识、prompt 版本、输入哈希、rubric 快照、原始响应都没有落盘；`criterion_score` 与 `evidence_ref` 连表都还不存在。M2 开始处理真实简历后，PIPL 的说明权要求我们能回答"这条评分是哪个模型、哪个版本、按哪份 rubric 打的，依据是简历里哪一段"——现在回答不了。

**二、外发人工确认门禁。** 合规红线写着"AI 只做排序推荐，不做自动淘汰，淘汰必须有人工确认节点并留痕"，但现有的 `effect_deliver_message` 是无条件投递：拿到 `OutboundMessage` 就交给 `Channel.deliver`，没有任何一道闸检查"这条消息是不是拒信/邀约、有没有人批过"。M1 阶段外发对象只有内部业务经理，风险低；一旦拒信和邀约进入外发路径，缺这道闸就是把红线交给调用方的自觉去守。

企业AI转型平台侧（`zhuopin_platform`）在 SC1/SC8 场景里已经把这两件事各做过一遍并踩过坑（hash-chain 防篡改绕过、fail-closed 语义、approve 路径绕过总开关）。本变更**参考它的做法、在本仓库自建等价实现**，不引入跨仓库依赖——理由见下方"为什么不引入跨仓库依赖"。

## What Changes

**AI 评分留痕**

- 新增 `app/audit/` 模块：结构化决策事件（`DecisionEvent`）+ 统一入口（`AuditRecorder`）+ 可换存储后端（`AuditSink` Protocol）
- 新增 SQLite 表 `analysis_run`（一次 AI 调用的完整可复现快照）与 `criterion_score`（逐项评分 + `evidence_ref`），`evidence_ref` 为空由数据库约束直接拒写
- 新增 JSONL append-only 防篡改镜像，逐行嵌 `prev_hash` 构成 hash-chain，附 `verify_chain()` 自检
- 把 `LLMGateway` 现有的 `AuditHook` 调用点接到真实实现上，替换 `NoopAuditHook` 作为生产默认；`NoopAuditHook` 保留给单元测试
- 新增审计断言查询：`reason_type='ai_score'` 的拒绝记录数恒为 0、`criterion_score` 中 `evidence_ref` 空值数恒为 0

**候选人外发人工确认门禁**

- 新增 `app/outbound/` 模块：`OutboundGate`，语义为 **fail-closed**——消息类型未知、风险等级未知、缺 `requires_confirmation` 字段时一律拦截，绝不默认放行
- 门禁覆盖 `rejection_letter`（拒信）与 `interview_invitation`（邀约）两类外发动作；两类都判为高风险，必须带 `confirmed_by` 才放行
- 新增第二道结构性总开关 `CANDIDATE_OUTBOUND_ENABLED`：关闭时**即便已人工确认也不外发**，堵住"approve 路径不查总开关"的旁路
- 新增独立表 `pending_approval` 承载被拦截的草稿（含 `blocked_reason` / `confirmed_by` / 状态机），不复用 `outbox`
- 每次外发与每次拦截都经 `AuditRecorder` 留痕，记录渠道、收件人、判定依据原始值、`confirmed_by`、是否入队
- `effect_deliver_message` 之前插入门禁判定；门禁本身是纯函数（`compute_*` 语义），入队与投递各自落在既有的 `effect_*` 节点里

## Capabilities

### New Capabilities

- `ai-decision-audit`: AI 评分与决策的可复现留痕——模型标识与实际返回版本、prompt 版本、temperature、输入哈希、rubric 快照、原始响应的持久化；逐项评分与 `evidence_ref` 的强制绑定；append-only hash-chain 防篡改与链完整性自检；合规审计断言查询
- `outbound-approval-gate`: 候选人拒信与邀约外发前的人工确认门禁——fail-closed 判定、人工确认放行、第二道结构性总开关、被拦截草稿的持久化待审批队列、外发与拦截动作的强制留痕

### Modified Capabilities

（无。`openspec/specs/` 当前为空——M1 变更尚未归档，其能力还不是活文档。本变更改动的是 `LLMGateway` 的审计钩子默认实现与 `effect_deliver_message` 的调用前置条件，属实现层接线，不改 M1 已声明的任何 spec 级行为。）

## Non-goals（不做什么）

- **不做鉴权与登录方式统一**。企微 OAuth SSO 待卓品智能 AI 转型项目两侧共同决定，本变更不碰 `app/middleware/auth.py` 的空壳实现。**登记为已知待办**：部署约束 5 要求"M2 起处理真实简历前必须具备可识别到人的登录 + 简历访问留痕"，本变更把留痕这一半做掉，登录那一半仍是 M2 前的阻塞项。留痕表里的 `operator_id` 字段现阶段允许写调用方传入的标识；SSO 落地后它才真正可信。
- **不碰 `effect_log` 与幂等键的任何设计**。那是 LangGraph 图恢复机制自身的问题，与本次两条留痕/门禁的融合无关。新增的 `effect_*` 节点沿用现有 `idempotent_effect` 装饰器，不改装饰器本身、不改 `effect_log` 表结构、不改幂等键格式。
- **不碰部署形态**。venv + Windows 计划任务 + scp 推送，两侧已经一致，本变更只新增文件不改部署脚本。
- **不引入 `zhuopin_platform` 的 pip 依赖，不跨仓库 import，不把参考文件拷进本仓库**。理由见下节。
- **不做 ClickHouse 后端**。平台侧为 9 月 U9C 数据汇聚预留了 `ClickHouseSink`；本仓库只定义 `AuditSink` Protocol 留出换后端的位置，不实现第二个后端。
- **不做审批流 UI**。`pending_approval` 只提供表、状态机与查询/放行接口，人工审批的前端界面不在本次范围。
- **不做通用通知门禁**。门禁只覆盖拒信与邀约两类**对候选人**的外发；内部业务经理的画像确认卡片走原路径，不加闸（M1 现有行为不变）。
- **不改 rubric 本身**。本变更只负责把 rubric 快照存下来，rubric 内容的定义属于 M2 的匹配能力。

## 为什么不引入跨仓库依赖

`zhuopin_platform` 与 `zhuopin-ai-recruiting` 是同一个卓品智能 AI 赋能项目下的两个模块，但它们的**演进节奏和约束不同**，共享代码会让两边同时失去自由：

1. **平台侧的字段语义是它自己场景的**。`AuditEvent` 的 `scenario`（"SC1"/"Q1"/"FI1"）、`automation_level`（L1/L2/L3）、`oem_context`（OEM 数据隔离审计）、`override_reason`（判例采集）都是采购/质量场景的建模。招聘场景要的是 `application_id`、`criterion_key`、`evidence_ref`、`rubric_version`。硬套平台的字段表会逼我们把招聘语义塞进 `payload` 的自由字典里——正好丢掉工程铁律 4 想要的强约束（`evidence_ref` 为空必须拒写，字典里的键做不到这件事）。
2. **依赖方向会反过来卡住平台**。一旦本仓库 import 平台包，平台改 `AuditEvent` 字段就得考虑不破坏招聘侧。平台侧 9 月要迁 ClickHouse、要接 U9C 数据汇聚，那是它自己的路线；招聘侧 M2 要迁 Postgres，是另一条路线。绑在一起的结果是两边都不敢动。
3. **分发形态不支持**。平台包没有发布到内部 PyPI，跨仓库引用只能靠相对路径或 git 依赖；而 `~/Library/CloudStorage/OneDrive-Personal/Projects/企业AI转型/` 这个目录本项目**只读不可写**（跨端 OneDrive 同步会造成文件锁冲突与覆盖），依赖一个只读且由另一台机器维护的路径，等于把构建的可重复性押在 OneDrive 的同步状态上。
4. **拷文件比写依赖更糟**。拷进来的副本会在两边各自演进后静默分叉——平台侧修了 hash-chain 的防篡改绕过（`prev_hash` 字段被删光后整链仍通过），我们的副本不会自动得到这个修复，而它长得一样，会让人误以为已经修过。

**结论：共享的是做法，不是代码。** 本变更把平台侧验证过的四个设计要点当成需求写进 spec——append-only + hash-chain 防篡改、fail-closed 未知即拦截、`confirmed_by` 才放行、每次动作强制留痕——然后在 `app/` 下按招聘领域的字段自建实现，并把平台侧踩过的具体坑写成验收场景（见 design.md 的"参考边界"一节）。

## 合规影响说明

- **本变更处理候选人个人信息，但刻意不落原文。** `analysis_run` 存输入**哈希**而非简历原文；`criterion_score.evidence_ref` 存的是回指定位（简历文档 id + 字符 offset 区间）而非摘出来的文本。留痕表因此不构成第二份简历副本，缩小了泄露面。原文仍在简历主存储里，按其自身的访问控制管。
- **PIPL 说明权（第 24 条）由本变更兑现一半。** 候选人有权要求说明自动化决策的逻辑。`analysis_run`（模型标识 + API 实际返回的 model 字段 + prompt 版本 + temperature + rubric 快照 + 原始响应）加上 `criterion_score.evidence_ref`，共同构成"这条评分怎么来的"的完整答案。另一半——谁在什么时候看了谁的简历——依赖登录能力，见 Non-goals。
- **禁止自动淘汰的红线由门禁 + 断言双保险。** 门禁在运行时拦（fail-closed，未知即拦截），审计断言在事后查（`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0）。断言进 CI，红线被破坏时测试直接红。
- **AI 生成内容标识**：拒信与邀约属《AI 生成合成内容标识办法》（2025-09-01 施行）规制范围。本变更**不重复实现标识逻辑**（`app/agents/jd_agent.py` 已有 `AI_LABEL_TEMPLATE` 机制），但门禁会把"是否带标识"作为放行的必要条件之一校验——缺标识按 fail-closed 拦截。
- **禁止人脸/表情分析**：本变更新增的字段中没有任何生物特征位；`criterion_score` 的 `criterion_key` 需受白名单约束，声学情绪信号（语速/停顿/静默）不允许作为 `criterion_key` 写入（只能展示给面试官）。这条以校验 + 测试落地。
- **不用历史录用结果做监督信号**：留痕数据是审计资产，不是训练资产。`analysis_run` / `criterion_score` 明确标注禁止用作任何模型的训练或调优输入。
- **数据不出境**：留痕全部落本地 SQLite 与本地 JSONL 文件，无外部上报。

## Impact

**新增代码**

- `app/audit/`：`events.py`（`DecisionEvent`）、`recorder.py`（`AuditRecorder`）、`sinks.py`（`AuditSink` Protocol + `SqliteSink` + `JsonlChainSink` + 双写组合）、`assertions.py`（合规断言查询）
- `app/outbound/`：`gate.py`（`OutboundGate`，纯函数判定）、`contracts.py`（`OutboundMessage` 的门禁相关字段 Protocol）、`queue.py`（`pending_approval` 的读写与状态机）

**修改代码**

- `app/storage/db.py`：新增 `analysis_run`、`criterion_score`、`pending_approval` 三张表与相应约束/索引（**不动 `effect_log`**）
- `app/llm/gateway.py`：生产默认审计钩子从 `NoopAuditHook` 换成接 `AuditRecorder` 的实现；`AuditHook` Protocol 签名需扩展以承载 rubric 快照与业务关联字段
- `app/graph/nodes.py`：`effect_deliver_message` 之前插入门禁判定；新增 `effect_enqueue_pending_approval`（沿用现有 `idempotent_effect`）
- `app/config.py`：新增 `CANDIDATE_OUTBOUND_ENABLED`、审计 JSONL 路径两项配置

**新增依赖**

无。`hashlib` / `json` / `sqlite3` / `threading` 全在标准库，pydantic 已在 `requirements.txt`。**明确不新增 `zhuopin_platform`。**

**外部系统**

无。留痕与门禁全部在进程内 + 本地文件/SQLite。

**风险面**

- **双写一致性**：SQLite 为真身、JSONL 为防篡改镜像，两处都要写。进程在两次写之间崩溃会导致镜像缺行。需明确"哪个是真身、不一致时以谁为准、如何检出并补齐"，并有测试覆盖（design.md 的"双写故障语义"）。
- **fail-closed 的误拦成本**：语义正确的代价是新增消息类型如果忘了登记就会被静默拦下。需要拦截也留痕、并有查询手段能发现"某类消息一直在被拦"。
- **hash-chain 的两个已知绕过**（平台侧已修，本仓库必须一次做对）：① 第 2 行起缺 `prev_hash` 字段必须判为断链，不能只豁免首行的向前兼容；② `verify_chain()` 必须对磁盘原始字节重算哈希，不能依赖 JSON 重排序后的规范化形式。
- **M2 迁 Postgres**：表结构需照搬，JSONL 层不受影响。`SqliteSink` 与 `AuditSink` Protocol 的分离就是为这次迁移留的位置。
