## Why

HumanResource 是"企业AI转型"项目的部门模块之一。采购/财务/质量三个部门的 AI 专员早已通过一台 Windows 机器上的企微"智能机器人"（aibot）24 小时值守通道收材料、传信息（源码 `5-平台底座/wecom-aibot-service/`，架构与六个真实生产 bug 的分析见本仓库 `06-企业AI转型资产借鉴清单.md` §三）。HR 侧的对应专员已确认是**汤丽萍**，企微群"人力AI保障组"（邵培申／陈承／聂鑫／汤丽萍／王寒月）已建，但双方目前仍在群里**手动**交换材料（例：`岗位要求确认反馈表v3.xlsx`）——没有归档、没有任务队列、没有留痕，材料靠人记得去翻聊天记录。

**为什么是现在**：那台 Windows 机器上的现有开发模块即将结束使用，Mac 已是今后的主力开发机。这条值守通道必须在 Windows 那套退役前迁到 Mac，否则 HR 侧的材料协同会退回纯手工。

## What Changes

- **新增一个独立进程的 HR 企微值守服务**，落在 `tools/liaison/`（**不在 `app/` 也不在 `scripts/` 下**——两者都在 `sync-to-server.sh:42-50` 的 `SYNC_PATHS` 白名单里，放进去就会被推到 `.51` 生产服务器，而本服务明确永不部署 `.51`）。依赖清单独立（`tools/liaison/requirements.txt`），**不进根 `requirements.txt`**，让依赖清单本身充当"不可能被误部署"的结构性门禁。
- **企微 aibot 长连接接入**：参考 `wecom-aibot-service` 的 SDK 选型思路（`wecom-aibot-python-sdk`，Windows 侧在用 v1.0.2），**只读参考做法、不 import 其代码**。凭据 `HR_LIAISON_BOT_ID` / `HR_LIAISON_BOT_SECRET` 由 Shao Peishen 在企业微信管理后台注册一个**新的、与 Windows 那套不共用的** aibot 应用后自行填入 `.env`；凭据缺失时服务 MUST 拒绝启动（fail-closed），不静默空转。
- **HR 白名单准入（fail-closed）**：白名单外的消息只归档 + 礼貌回复，不产生任务队列行。白名单落在版本管理的配置文件里（不是硬编码 `frozenset`），配置缺失或解析失败 = 空白名单 = 全拒。名单范围结论见 design.md 决策 D2。
- **入站消息与附件归档**：归档键细到 `msgid`，**不用"按天"粒度**（Windows 侧生产 bug「归档覆盖」的直接成因）；附件一律按字节流处理，完整性只校验字节长度 + SHA-256，**不做任何 UTF-8 解码校验**（生产 bug「二进制判误」的直接成因）。
- **值守任务队列**：队列真身是 SQLite 表（`data/liaison.db`），Markdown 只作只读导出视图。Windows 侧「队列越界写入」「队列追加并发覆盖」「竖线破坏表格」三个生产 bug 全部源于"拿 Markdown 表格当数据库"，本实现从存储形态上消除这一类。
- **落库走本仓库现成的幂等机制**：复用 `app/storage/idempotency.py` 的 `idempotent_effect` 装饰器与 `effect_log` 表设计（同仓库单向复用，`app/` 不得反向 import `tools/`），幂等键沿用 `{thread_id}:{node_name}:{business_key}` 形态。**不照抄 `wecom-aibot-service` 的 at-most-once 模型**——它的 `msgid` 只是归档文件名后缀，不是幂等键。
- **群通知外发**：参考 `shared_tools/notifiers/wecom.py` 的 webhook 封装做法（`urllib` + JSON + `errcode` 检查 + 真实 URL 落 `.env`），并**一并补上参考实现留下的两处空白**：限流退避（企微群机器人 20 条/分钟，`errcode 45009`）与超长内容截断/降级（按**字节**计，超限降级为提要 + 附件；提要仍超限或无附件则拒发，不静默丢内容）。
- **断线缺口诚实告警**：企微 aibot 协议**没有离线消息补推能力**，断线期间的消息永久丢失。幂等只解决重复投递，解决不了协议层丢失。服务 MUST 记录连接中断窗口并显式告警"这段时间可能漏消息"，**MUST NOT** 把这个缺口伪装成已被幂等覆盖。

## Capabilities

### New Capabilities

- `liaison-channel-session`: 值守通道的接入与会话生命周期——凭据缺失即拒绝启动的 fail-closed 启动校验、长连接心跳与退避重连、连接中断窗口的记录与"可能漏消息"显式告警（不假装被幂等覆盖）
- `liaison-inbound-whitelist`: 入站白名单准入——白名单从版本管理的配置加载，缺失/解析失败一律折成空名单全拒；命中者的消息进入归档与任务队列，未命中者只归档 + 礼貌回复、不产生队列行
- `liaison-message-archive`: 入站消息与附件归档——归档键细到单条消息标识（禁止按天等粗粒度键）、附件按字节流处理且完整性校验不依赖文本可解码性、同一条消息重复投递不产生第二份归档
- `liaison-task-queue`: 值守任务队列——队列真身为结构化存储且入队走幂等 effect（幂等记录与业务写同一事务提交）、任意消息内容不得破坏队列结构、Markdown 视图为只读导出物
- `liaison-group-notify`: 群通知外发——发送速率受限流约束并对限流响应做退避重试、消息长度按字节守卫且超限走"提要 + 附件"降级、降级不可行时拒发而非静默截断丢内容

### Modified Capabilities

（无。本变更不改动 `openspec/specs/` 下四个活文档能力的任何要求。三处边界关系在 design.md 决策 D7 逐条说明：`effect-transaction-integrity` 的事务归属要求在本服务里**沿用**而非修改；`outbound-approval-gate` 按其「门禁覆盖范围」要求原文——"面向内部员工的通知不在门禁范围内"——本服务的群通知不落在门禁内；`ai-decision-audit` 不适用，本服务不做任何 AI 评分。）

## Impact

- **新增目录**：`tools/liaison/`（服务代码、独立 `requirements.txt`、白名单配置）。
- **新增运行时数据**：`data/liaison.db`（独立库，不与 `data/demo.db` 混用，理由见 design.md D5）、归档目录。`data/` 已在 `sync-to-server.sh:58` 的 `EXCLUDE_NAMES` 里，不会被同步。
- **`.env` / `.env.example`**：新增 `HR_LIAISON_*` 配置项占位（占位符只写变量名，**不写任何真实凭据**）。
- **不触碰**：`app/` 下任何模块（只被单向 import）、根 `requirements.txt`、`pyproject.toml`、`sync-to-server.sh`、`deploy-server.ps1`、`.51` 服务器上的任何东西。
- **外部依赖**：`wecom-aibot-python-sdk`。⚠️ 已知风险：Windows 侧在用 v1.0.2，其文档未明确 Python 版本，按依赖链推断 3.8+；本项目 `requires-python = ">=3.14,<3.15"`。**SDK 在 Python 3.14 上的可安装性与可运行性必须在写业务代码之前先实测**，不兼容时的退路（按官方 WS 协议自建最小客户端）见 design.md D8。
- **人**：Shao Peishen 需在企业微信管理后台注册新 aibot 并取得 BotID/Secret（账号级操作，无法代劳）；汤丽萍侧无需任何操作变更（继续在原群发消息即可）。

## Non-goals（不做什么）

- **不做候选人通道、不碰候选人数据。** 本服务的对手方是内部同事（HR 专员），与候选人/面试官通道是两个不相交的问题域（`06` §3.5 已澄清）。白名单模式**不得**外推到候选人身上（`06` §3.1）。
- **不是 HumanResource 的产品功能。** 这是 Shao Peishen 自己的开发期值守工具，不受 `04-部署与门户挂载.md` 的部署约束管辖：不挂 `/hr/recruit-agent`、不做路径前缀就绪、不进 FastAPI 应用、不走 8095 端口、不接鉴权中间件空壳。评审时按本文件的验收标准判，**不要套产品功能的验收标准**。
- **永不部署 `.51`。** 与 Windows 侧同一口径（Shao Peishen 2026-08-13 拍板）。系统上线后本工具不再需要。
- **不与 Windows 那套共用 aibot 凭据、不共用白名单、不共用任务队列、不做两端对账。** 两套各自独立运行、各管各的域（Windows 管质量/采购/财务，Mac 只管 HR）。理由见 design.md D1。
- **不引入 LangGraph。** 本服务没有多节点编排，铁律 1 的动机（恢复时整节点重跑）在这里由"重连后可能重投同一 `msgid`"承担，靠 `idempotent_effect` 即可，不需要 checkpointer。见 D6。
- **不迁移 Postgres**，继续 SQLite。
- **不做 AI 内容生成、不做任何评分。** 本服务只收、只存、只转发、只通报。因此不涉及 AI 生成内容标识义务，也不产生 `criterion_score`。
- **不实现跨域中继（`outbox_relay`）、不实现跟进信三态流程（`dispatch_followup_letters` / 审批门禁）、不实现素材分片上传。** 这些是 Windows 侧长出来的能力，HR 侧目前没有对应需求，不预造。
- **不改 `app/` 下任何代码，也不改「企业AI转型」仓库里的任何文件**（该仓库在本项目下只读，见 CLAUDE.md）。
- **不做 Markdown 队列的双向写。** Markdown 只出不进：人在 Markdown 里改了状态**不会**回写数据库。

## 合规影响说明

**处理的个人信息**：内部同事的企微 `userid`、中文姓名、消息文本与附件内容。**不处理候选人个人信息，不处理简历**。

- **法律依据**：处理对象是本单位内部员工在履职过程中的工作沟通，属人力资源管理与内部协同必需，依 PIPL 第 13 条第（二）项处理。
- **不出境**：服务在本机（Mac）运行，归档与数据库均为本地文件；企微为境内服务。本服务不调用任何 LLM，不存在推理数据外发。
- **白名单里的 `userid` 与姓名落在版本管理的配置文件里**：这两项是企微内部标识与工作署名，不是可直接联系的联系方式；配置文件 MUST NOT 包含手机号、邮箱、身份证号等信息。
- **凭据铁律**：`HR_LIAISON_BOT_ID` / `HR_LIAISON_BOT_SECRET` / 群 webhook URL 一律只落 `.env`（不入版本管理），配置文件与本变更包 MUST NOT 出现任何真实凭据。
- **归档内容含内部同事的工作附件**：`data/` 与 `logs/` 已被 `sync-to-server.sh` 的 `EXCLUDE_NAMES` 排除，不会被同步到 `.51`，避免构成一次未经授权的个人信息转移。
- **留存期**：归档与队列数据的留存上限在 design.md D9 定；本工具随系统上线退役，退役时归档一并清理。
- **不受候选人外发门禁管辖**：依 `openspec/specs/outbound-approval-gate/spec.md`「门禁覆盖范围」要求原文，面向内部员工的通知不在门禁范围内。本服务的群通知全部面向内部同事，因此不经该门禁——**但这不等于无约束**：`liaison-group-notify` 自带限流与长度守卫，且 MUST NOT 用于向候选人发送任何内容。
