---
name: run-build
description: 用 Superpowers 子代理驱动开发执行实现计划，完成后回勾 OpenSpec 的 WBS。当用户说"执行计划""run-build""开始写代码""跑这份 plan""subagent-driven-development""继续实现"时使用。开工前强制检查 Global Constraints 段，缺失则拒绝执行。
---

执行 `spec-to-plan` 产出的实现计划。完整规则见 `03-工具链协作规则.md`。

**输入**：计划文件路径。若未给出，列出 `docs/superpowers/plans/` 下的计划让用户选，并标出哪些已完成。

## 前置检查（任何一条不过就停）

1. **能否调用 `superpowers:subagent-driven-development`？**
   不能就停。告诉用户换到能调用它的界面（Desktop 的 Code tab 或 Claude Code 终端）。
   **不要退化成自己逐个任务写代码**——那就丢掉了上下文隔离、两阶段 review、进度台账，也就丢掉了用这套工具的全部理由。

2. **bash 是否跑在用户本机？**
   Cowork 的 bash 在隔离 VM 里，git worktree 与提交行为不可靠。实现阶段应在 Claude Code 里做。

3. **计划里有没有 Global Constraints 段？**
   没有就停，回去跑 `spec-to-plan` 补。缺了这段，后面每一轮 review 都是瞎的——而且这个失败**不会报错**，只会安静地漏掉所有铁律检查。

4. **✅ 这一步需要 git worktree**（与 `spec-to-plan` 不同，那一步不需要）
   调用 `superpowers:using-git-worktrees` 建隔离工作区。这里会真正写代码、提交、最后走 `finishing-a-development-branch` 合并。

   **建完 worktree 后必须手工补三样 git 不会带过去的东西**，否则会遇到看似莫名其妙的失败：

   | 缺什么 | 症状 | 处理 |
   |---|---|---|
   | `.env`（gitignored） | LLM 调用报"无 API key"，像是代码 bug | 从主检出拷过去 |
   | 本地 SQLite / `data/` | 启动即报文件不存在 | 按需拷贝或让代码自动初始化 |
   | Python venv | `ModuleNotFoundError` | 在 worktree 内重新建 venv 装依赖 |

   **拷完 `.env` 后确认 worktree 的 `.gitignore` 生效**——新目录里误提交 `.env` 是最容易发生的泄露。

5. **进度台账是否已存在？**
   路径是 `.superpowers/sdd/<计划文件名去掉.md>/progress.md`（**按计划分子目录，不是扁平的 `sdd/progress.md`**）。
   例：`.superpowers/sdd/2026-08-06-m1-chapter0-demo/progress.md`

   存在说明这份计划跑过一部分。台账里标记 complete 的 Task **不要重跑**——重跑已完成任务是这套流程里最贵的失败模式。

   注意：台账是 git-ignored 的，**存在 worktree 内部**。删掉 worktree 台账就没了，恢复只能靠 `git log`。

## 执行

用 Skill 工具调用 `superpowers:subagent-driven-development`，把计划路径交给它。

它自己处理：每 Task 派发全新子代理、两阶段 review（spec 合规 + 代码质量）、修复循环、进度台账、最终全分支 review。

**不要中途停下来问「要继续吗」**——该技能明确要求连续执行到完成或阻塞。

## 本项目额外的 review 关注点

给 reviewer 的 Global Constraints 里，这几条要盯死：

- 每个 `effect_*` 节点是否独占、是否带幂等键 `{thread_id}:{node_name}:{business_key}`
- `compute_*` 节点是否真的无副作用
- LLM 调用是否 `temperature=0` 且模型版本显式锁定（无 `latest`）
- 写 `criterion_score` 处，`evidence_ref` 为空是否被拒绝
- 有没有任何路径能产生 `reason_type='ai_score'` 的淘汰记录（必须没有）

## 收口

最终全分支 review 通过后：

1. 回到 `openspec/changes/<change>/tasks.md`，把本交付单元覆盖的章节条目 `- [ ]` 改成 `- [x]`
   **只有 final review 通过才勾**，写完代码不算完
2. 🔴 **先把 worktree 里 git-ignored 的产物搬回主工作区，再合并**（Shao Peishen 2026-09-03 定）：
   `data/` 下本次拉取或生成的东西（`.51` 快照、回放落库、临时库）一律 `cp` 到主检出的同名路径
   （如 `data/replay/`）。**worktree 随 finishing 流程删除时，git-ignored 的文件一起没**——
   实证：`0903H` 拉回的 `.51` 一致快照（2.4 MB）随收口消失，findings 里的结论再也无法逐字节复核。
   报告里写明搬了哪些文件、落在哪。
3. 调用 `superpowers:finishing-a-development-branch` 处理合并
4. **验证合并真的发生了**：`git rev-list --count main..<分支名>` 必须是 `0`。

   不是 0 就说明第 3 步没跑或没跑完，**代码还挂在分支上**。

   不是 0 时**先别下结论**，再跑一次 `git cherry -v main <分支名>`：

   - 全部行以 `-` 开头 → **是假阳性**，内容已经在 main 上，只是 main 被 rebase 重写过
     历史，commit 换了 hash，旧分支还指向旧 hash。此时该做的是删掉这个陈旧分支
   - 有任何一行以 `+` 开头 → **是真的没合**，那几条就是漏掉的，去补第 3 步

   `git cherry` 按 patch-id 比对内容，能穿透 rebase；`rev-list --count` 只比 hash，
   不能。实证：2026-08-26 `claude/delivery-unit-c-run-build-22abbe` 的
   `rev-list --count` 报 5，`git cherry` 五行全是 `-`，第 4 章 checkbox 也是 5/5 全勾
   ——单元 C 早就合进去了。**只信 `rev-list` 会把已完成的单元误判成待补**。

   *为什么要单独验一次*：这一步被跳过时，本地看起来一切正常——测试全绿、
   tasks.md 勾满、review 通过、汇报也说"已完成"。**唯一的症状是 main 上什么都没有**，
   而没有任何东西会报错。实证：2026-08-19 `server-runtime-logging` 跑完全部 7 个 Task
   并通过终审，11 个提交在分支上挂了整整一轮，直到下一次会话查 git 才发现。
5. 若该变更 tasks.md 已全部勾完，提示用户用 `openspec-archive-change` 收口，把 spec 折进 `openspec/specs/`。
   🔴 **归档顺序按 spec 依赖，不按完成先后**（2026-09-03 `0903Q` 实证）：一个包的 delta 若是对另一个包
   ADDED 的需求做 MODIFIED，主 spec 要等那个包归档才存在——先归档 delta 包会把 MODIFIED 落到不存在的基底上。
   先归档被依赖的包，再归档 delta 包；派归档 opener 时把顺序写死

## 输出

- 完成的 Task 数与提交范围
- 最终 review 结论，以及遗留的 Minor findings
- tasks.md 整体进度（N/M 章节）
- 下一步建议
