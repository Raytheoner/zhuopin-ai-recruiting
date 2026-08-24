# Session 接力 · HR 招聘智能体

> 滚动更新，覆盖旧版。新会话读完本文即可接上。
> 最后更新：2026-08-19（Cowork 业务线）

---

## 开场词（复制即用）

```
读 /Users/paulshao/Projects/HumanResource/docs/session接力.md 恢复上下文，然后按【下一步】继续。
```

---

## 一、状态快照（2026-08-19）

| 项 | 现状 |
|---|---|
| main | `4c010a5` — 单元 A、C 已合并；**单元 B 做到一半卡在分支上未合并** |
| 测试 | **176**（本地，单元 C 后）；Windows CI 实证基线 124 → 单元 A 后未再跑 CI |
| 生产 | `.51:8095`，`/hr/recruit-agent`，结构化日志版已上线（08-19 21:24 发版） |
| 已归档变更包 | `2026-08-18-fix-sqlite-transaction-ownership`、`2026-08-19-server-runtime-logging` |
| 活跃变更包 | `m1-job-profile-intake`、`m1-intake-quality-fixes`（15/69，剩 54 项拆成 B–G 六单元）、`ai-audit-trail-and-outbound-gate`（0/53） |

**M1 demo 已有 3 位业务经理试用过**（pilot 08-16~08-18）。`m1-intake-quality-fixes`
整个变更包就是为修 pilot 暴露的问题而立。

---

## 二、下一步

**OP-0819-L 已完成**（`17542f9` 落 `delivery-units.md`），**三个决策 Shao Peishen 已于 08-19 全部拍板**：

| 决策 | 结论 |
|---|---|
| Coverage Gap「整轮失败记耗时」 | **路 B 窄化 spec** —— spec 已改，归档阻塞已解除，对 B 零影响 |
| F（第 7 章）排位 | **最后**，接受编造率 20 场样本时钟晚开始一轮 |
| D / E 先后 | **D 先**（5.5 自动成立，不写平行标记逻辑） |

**执行顺序定稿**：`B ∥ C → D → E → F → G`

**下一步 = B 与 C 各出一份 `spec-to-plan`**（CC / 新开 session / main / ❌ worktree）。
两份可并行：B 全在后端、C 只碰 `index.html`，触碰区零重叠。
**前置**：先在 CC 提交本轮 Cowork 的文档改动（见下）。

**Opener 全文**：`docs/openers/OP-0820-全量编排.md` —— 唯一权威清单，9 条 + 1 条手工项，
含编排总图与并行判据。每条自包含，任何新 session 拿到都能独立执行。
（`OP-0820-A_单元BC开工.md` 已废弃，只剩一行指针）

### 本轮 Cowork 已改、待 CC 提交的文件

- `specs/intake-turn-observability/spec.md` —— 窄化那句 SHALL，新增「整轮失败不留痕」Scenario
- `design.md` —— Coverage Gap 销号，归档阻塞解除
- `delivery-units.md` —— 三个决策回填，§3.2/§4/§6 改为已决状态
- `tasks.md` —— 纠正第 4、6、7 章头部的并行性说法（原说法按触碰文件判据不成立）
- `docs/session接力.md`（本文件）
- `.claude/skills/run-build/SKILL.md` —— 上一轮就未提交的改动

### 开工时仍要在 plan 阶段定的（技术方案，不必找 Shao Peishen）

- **B**：已问台账落哪儿——新列（`init_schema` 幂等加列，决策 10）vs `profile_json` 下划线内部键（决策 8）
- **B**：`derive_question_id` 校验 `field` 属于 `JobProfile.model_fields`，野 field 按无 field 降级 + 打点
  （单元 A 终审 minor，落在 B 而非 E：3.9 让 `question_id` 第一次参与判定，野 field 会让每轮都被判成有产出）
- **C**：点选提交必须走「文本原样拼进回复、不改 API 契约」，否则 B ∥ C 的并行立刻失效

---

## 三、待决策 / 悬置

| # | 事项 | 状态 |
|---|---|---|
| 1 | ~~`intake-turn-observability` 的「重试全部失败时记录累计耗时」~~ | **✅ 已关闭 08-19**：走窄化路 B，spec 已改，归档阻塞解除 |
| 2 | 阶段 C 门户导航 | 需 Paul 在 Win 笔记本上改门户 HTML。板块名「HR·招聘智能体」，外链跳 `http://192.168.100.51:8095/hr/recruit-agent/` |
| 3 | `.51` 整机重启验证（07 文档 P1 第 5 条） | 需低峰窗口，会中断另外 5 个服务 |
| 4 | `deploy-server.ps1` 的 ACL 段从未执行过 | 08-19 发版时日志目录是应用自己 mkdir 的，那段 `Set-Acl` 没跑；CI 也不跑 PowerShell |
| 5 | 决策代理人 | `CLAUDE.md` 里框架已建，人选待 Shao Peishen 指定 |
| 6 | 06 清单剩余 11 条 🟢 | 见 `06-企业AI转型资产借鉴清单.md` |
| 7 | 两个 prunable worktree | 下次进 CC 跑一次 `git worktree prune` |

---

## 四、本轮新增的环境事实

- **GitHub Actions**：本仓库是 private，Free 计划 2000 分钟/月，**Windows runner 按 2x 扣**。
  08-19 撞过一次额度。CI 里 1 个 `windows-latest` + 2 个 `ubuntu-latest`。
  若再撞，折中方案是「feature 分支只跑 Linux，PR 与 main 推送才跑 Windows」。
- **日志路径耦合**：`log_file` 是相对路径 `logs\app.log`，靠计划任务的
  `WorkingDirectory=C:\apps\zhuopin-recruit-agent` 解析——**改工作目录会把日志静默挪走**。
- **脱敏层尚未被真实数据检验**：发版验证显示日志里 0 个标记词，但 `<redacted>` 也是 0 命中，
  说明脱敏根本没被触发（`loggable_summary()` 至今无生产调用点）。
  这条验证成立的是「没有泄漏」，**不是「脱敏被证明有效」**。详见
  `docs/findings/2026-08-13-sqlite-事务归属冲突.md` §8.3.1。

---

## 五、绕不开的约束（每次都要记得）

- **给 Paul 的每条指令必须声明四项**：界面 / Session / 分支 / Worktree，缺一不可，
  没有「小任务可省略」的例外。判据表见 `CLAUDE.md`。
- **Opener 必须是完整可整块复制的**，不要让他拼接片段。
- **一次给 ≥2 个 opener 时，必须说明次序与能否并行**（判据＝触碰区是否重叠）。
- **`~/Library/CloudStorage/OneDrive-Personal/Projects/企业AI转型/`**：可完全读取借鉴，
  **绝不得修改、删除或 lock**。
- **git 相关只能在 CC**——Cowork 的 bash 在隔离 VM 里，对 `.git/` 只能写不能删。
- **跨界面看不到对方会话**：`list_sessions` 只列 Cowork 会话，CC tab 的永远看不到。
  查 CC 干了什么走文件系统（git log / `.claude/handoff/*.log`）。

---

## 六、本轮学到的两条（已固化进 skill，此处只留指针）

1. **计划里的任务标题必须是三级 `### Task N:`**——二级会让 `scripts/task-brief`
   静默返回空。已写进 `.claude/skills/spec-to-plan/SKILL.md` 自查清单。
2. **合并后要单独验一次 `git rev-list --count main..<分支>` 是否为 0**——
   `finishing-a-development-branch` 被跳过时毫无症状，唯一表现是 main 上什么都没有。
   已写进 `.claude/skills/run-build/SKILL.md` 收口段。
