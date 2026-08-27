# 卓品智能 · AI Agent 招聘智能体

无锡卓品智能（ECU 汽车电子研发制造，约 300 人）的自研招聘系统。目标是把事务性、重复性的招聘工作交给 AI Agent，让 HR 聚焦在关键决策与候选人关系上。

> ⚠️ **内部私有项目。** 涉及岗位画像、候选人评估逻辑与合规设计，仓库必须保持 private。

---

## 快速上手

新同事从这里开始，按顺序读：

| 文档 | 内容 |
|---|---|
| [`00-环境就绪清单.md`](00-环境就绪清单.md) | **先读这个。** 装什么、在哪个界面干什么、日常三个技能 |
| [`01-开源调研与技术选型.md`](01-开源调研与技术选型.md) | 为什么这么选。含开源侦察结论与合规红线论证 |
| [`02-系统架构与MVP范围.md`](02-系统架构与MVP范围.md) | 分层架构、数据模型、MVP 范围与 12 周排期 |
| [`03-工具链协作规则.md`](03-工具链协作规则.md) | OpenSpec × Superpowers 怎么分工。**动手前必读** |
| [`04-部署与门户挂载.md`](04-部署与门户挂载.md) | 部署决策与约束（**为什么**这么部署） |
| [`05-发布运行手册.md`](05-发布运行手册.md) | 发布操作手册（**怎么做**）。含 SSH 打通、首装、门户接入、验收清单、故障排查、回滚 |
| [`CLAUDE.md`](CLAUDE.md) | 工程铁律与合规红线。AI 助手每次会话自动加载 |

---

## 当前状态

**阶段一（W1-8）**：需求解析 + 简历评分排序，第 8 周真实上线
**阶段二（W9-12）**：实时语音结构化面试

正在做：**M1 第 0 章 · 内网 Demo**（3-5 天）——浏览器里一句话提需求 → 岗位画像 → JD。
不碰候选人个人信息，合规风险为零，用于让 HR 尽早感受产品形态。

---

## 开发流程

```
openspec-propose  →  需求与契约（proposal · specs · design · tasks）
      ↓
spec-to-plan      →  实现计划（输入是 spec.md，不是 tasks.md）
      ↓
run-build         →  子代理驱动开发 + 两阶段 review
      ↓
openspec-archive-change  →  specs 折进 openspec/specs/ 成为活文档
```

**界面分工**：需求与文档在 Cowork 或 Desktop 均可；**实现阶段必须在 Desktop Code tab 或 Claude Code 终端**（Cowork 的 bash 在隔离 VM 里，git 与 worktree 不可靠）。

不要用 `openspec-apply-change` / `/opsx:apply` —— 它没有 TDD、没有 review gate。理由见 `03-工具链协作规则.md`。

---

## 技术栈

Python · LangGraph ≥1.0.10 + Postgres Checkpointer · Postgres + pgvector · PaddleOCR/MinerU + 境内 LLM schema 抽取 · BGE-M3 · FunASR/CosyVoice/LiveKit（阶段二） · 企业微信

---

## 红线

**工程**（完整版见 `CLAUDE.md`）

- 每个有副作用的动作独占一个 LangGraph 节点，带幂等键——恢复时节点会从头整个重跑
- L3 Agent 全是无副作用纯函数，副作用只在 `effect_*` 节点
- 每条 `criterion_score` 必须有 `evidence_ref` 回指原文 offset
- `temperature=0`，模型版本显式锁定

**合规**

- AI 只做排序推荐，**不做自动淘汰**。淘汰必须有人工确认并留痕
- 禁止人脸/表情分析
- AI 生成的 JD、拒信、邀约须带标识
- 模型全部走境内，简历数据不出境
- 绝不用历史录用结果做监督信号

**数据**

真实简历、评测集、面试录音、`.env` **一律不入库**，已由 `.gitignore` 拦截。首次提交前请确认。

---

## 相关但无关的项目

「企业AI转型」是同一体系的上级项目——**本项目本来就是它的部门模块之一**，只是移到 Mac 端开发，
发布同样汇合到 `.51` 服务器。

**2026-08-26 起它已迁出 OneDrive，本地路径作废。** 唯一入口是 GitHub 公开仓库：

```
https://github.com/Raytheoner/zhuopin-ai-transformation    分支 master（不是 main）
raw: https://raw.githubusercontent.com/Raytheoner/zhuopin-ai-transformation/master/<路径>
```

**只读参考，不 clone 进来、不跨仓库引用。** 引入方式是「读取参考 + 在本仓库自建实现」——
两个仓库独立演进，共享的是做法不是代码。
