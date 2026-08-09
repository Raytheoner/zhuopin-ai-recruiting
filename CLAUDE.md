# 卓品智能 AI 招聘智能体

无锡 ECU（汽车电子）研发制造企业，约 300 人，主要招聘嵌入式/汽车电子工程师。

- 环境与日常流程：`00-环境就绪清单.md`
- 背景与选型论证：`01-开源调研与技术选型.md`
- 架构与 MVP 范围：`02-系统架构与MVP范围.md`
- **工具链协作规则：`03-工具链协作规则.md`（动手前必读）**
- 部署与门户挂载（决策与约束）：`04-部署与门户挂载.md`
- **发布运行手册（怎么做）：`05-发布运行手册.md`**

---

## 🚫 绝对禁止

**不得读取或修改 `~/Library/CloudStorage/OneDrive-Personal/Projects/企业AI转型/` 下任何内容。** 那是 Paul 在另一台 Windows 机器上维护的独立项目，通过 OneDrive 同步，跨端写入会造成文件锁冲突与覆盖。需要参考其做法时只能询问 Paul，不要自己去读。

---

## 工具链分工（硬规则）

本项目同时使用 OpenSpec 与 Superpowers，接缝在 **spec → plan**。日常只用三个技能：

```
openspec-propose  →  proposal · specs · design · tasks(WBS)      ← 需求与契约
      ↓
spec-to-plan      →  superpowers:writing-plans（输入 = spec.md）  ← 实现计划
      ↓
run-build         →  superpowers:subagent-driven-development     ← 执行 + 两阶段 review
      ↓
openspec-archive-change  →  specs 折进 openspec/specs/           ← 活文档
```

`env-ready` 用于环境自检。`spec-to-plan` 与 `run-build` 是本项目自定义 skill（`.claude/skills/`），已封装接缝规则与 Global Constraints 注入。

**规则真源在 `.claude/skills/`，不在 `.claude/commands/`。** commands 是遗留格式、只在终端生效，本项目下只留了一行入口文件。改规则改 skill。

**界面分工**：需求与文档在 Cowork 或 Desktop 均可；**实现阶段（spec-to-plan / run-build / git 提交）必须在 Desktop Code tab 或 Claude Code 终端**——Cowork 的 bash 在隔离 VM 里，git 与 worktree 不可靠。

**禁止**：

- ❌ 不用 `/opsx:apply` —— 它是朴素循环，无 TDD、无 review gate、无上下文隔离，由 Superpowers 执行层取代
- ❌ 不把 `tasks.md` 喂给 `subagent-driven-development` —— 粒度差一个数量级，`scripts/task-brief` 会解析失败
- ❌ 不用 `superpowers:brainstorming` 走主流程 —— 需求与设计由 OpenSpec 承载。仅在还没有 proposal 的开放式探索期使用，且产出要回灌成 `/opsx:propose` 的输入

**粒度映射**：`tasks.md` 的一个章节 = 一个 superpowers plan = 一条 worktree 分支 = 一个可独立测试并合并的交付单元。章节的 checkbox 在该 plan 的 final review 通过后才勾。

**每份 plan 必须包含 Global Constraints 段**，内容从下方"工程铁律"逐字复制——`subagent-driven-development` 会把它作为 reviewer 的注意力透镜。

---

## 工程铁律（不可违背）

1. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
2. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。
3. **所有 AI 评分必须持久化**：模型标识 + 模型版本 + prompt 版本 + temperature + 输入哈希 + rubric 快照 + 原始响应。
4. **每条 `criterion_score` 必须有 `evidence_ref`**（回指简历原文或面试 turn 的 offset）。`evidence_ref` 为空不允许写入。
5. **`temperature=0`；模型版本优先显式锁定**，禁止 `latest` 类别名。
   供应商不提供带版本号快照时（如 DeepSeek 公开 API 只有 `deepseek-chat` 这类会漂移的别名），**必须从 API 响应里取回实际的 `model` 字段并持久化**——配置里写的名字不算数，响应返回的才算。
   *为什么*：铁律的目的是评分可复现、可审计。供应商静默升级模型会让历史评分失去解释力，而 PIPL 的说明权要求你能回答"这条评分是哪个版本打的"。锁不住版本时，至少要记得住版本。
6. **企微回调先落库再处理**：只推一次、5 秒无响应即丢弃。回调接口只做签名校验 + 落库 + 返回 200。
7. **`langgraph >= 1.0.10`**（GHSA-g48c-2wqr-h844）。

## 部署约束（2026-08-09 定，详见 `04-部署与门户挂载.md`）

1. **路径前缀就绪**：FastAPI `root_path=/hr/recruit-agent`，前端资源与接口调用**一律相对路径**，禁止硬编码 `/static/…` `/api/…`。验收标准是挂到任意子路径下都能正常工作，且有测试覆盖。
2. **过渡端口 8095**，登记技术债，触发条件 = 统一门户网关上线即迁移。
3. **鉴权中间件留空壳接入点**，签名对齐未来企微 OAuth SSO；将来只换实现不换调用方。
4. **目标服务器是 Windows，没有 Docker**。部署形态 = Python venv + Windows 计划任务（SYSTEM 账户 + AtStartup + 失败重启 3 次）+ 防火墙规则 + scp 推送。不要引入容器。
5. **M2 起处理真实简历前**，必须具备可识别到人的登录 + 简历访问留痕（PIPL 要求"谁在什么时候看了谁的简历"可查）。共享口令不满足。

## 合规红线

- **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。审计断言：`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。
- **禁止人脸/表情分析**（《人脸识别技术应用安全管理办法》2025-06-01 施行）。声学情绪信号（语速/停顿/静默）只展示给面试官，不进 `criterion_score`。
- **AI 生成的 JD、拒信、邀约须带标识**（《AI 生成合成内容标识办法》2025-09-01 施行）。
- **模型全部走境内**，简历数据不出境。
- **绝不用历史录用结果做监督信号**（Amazon 2018 教训），只用显式岗位能力 rubric。
- 候选人入口一律用一次性邀请链接，避免被认定"向境内公众提供"。
- 主观描述（"沟通能力强"）不得进入硬门槛规则，只能作为软技能关键词。

## 数据模型要点

- `application` 是独立实体，状态属于投递不属于候选人（**不要合并 candidate 和 application**）
- `application_stage_history` 流转事实表是所有报表的基础
- `stage.stage_type` 是语义标签，显示名可自定义，逻辑只认类型
- 自定义字段用 JSON（`property_definition` / `property_value`），不用 EAV

## 技术栈

Python · LangGraph ≥1.0.10 + Postgres Checkpointer · Postgres + pgvector · PaddleOCR/MinerU + 境内 LLM schema 抽取 · BGE-M3 · FunASR/CosyVoice/LiveKit（阶段二） · 企业微信

## 沟通

中文。结论先行。不确定的标注可信度，不编造数据。
