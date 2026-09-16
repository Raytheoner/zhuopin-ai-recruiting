# Token 账本 · 2026-09-16 → 今

会话 115（含子代理）｜ API 调用 2540 ｜ API 等价成本 **$95.2573** ｜ <synthetic> 本地消息 14 条（不计费）

> 对比基线：成本 $767.3258 → $95.2573（-672.07）；主会话首轮上下文中位数 43k → 51k

## 按模型

| 模型 | 调用 | cache_read | cache_write | output | 成本$ | 占比 |
|---|---:|---:|---:|---:|---:|---:|
| sonnet | 2172 | 299.8M | 6.4M | 1.2M | 88 | 92% |
| opus | 65 | 4.7M | 393k | 938 | 5 | 5% |
| haiku | 303 | 12.0M | 1.1M | 670 | 3 | 3% |

## 按会话类型

| 类型 | 会话 | 调用 | 成本$ |
|---|---:|---:|---:|
| 泳道/opener | 27 | 1661 | 73 |
| 子代理 | 69 | 829 | 20 |
| 交互/其他 | 18 | 25 | 1 |
| 看护 | 1 | 25 | 1 |

## 单次调用上下文分段（滚雪球程度）

- <50k：486 次（19%）
- 50–150k：1424 次（56%）
- 150–250k：343 次（14%）
- ≥250k：287 次（11%）

首轮上下文中位数：主会话 51k ｜ 子代理 29k

## 大回显（单次 ≥10KB）来源 Top 15

| 来源 | 次数 | 合计 |
|---|---:|---:|
| `Read:session接力.md` | 7 | 0.17MB |
| `Read:__main__.py` | 5 | 0.12MB |
| `Read:review-c491230..506346f.diff` | 6 | 0.09MB |
| `Read:whitelist.py` | 4 | 0.09MB |
| `Read:task-5-brief.md` | 6 | 0.08MB |
| `Read:2026-09-10-liaison-criteria-ledger.md` | 3 | 0.08MB |
| `Read:task-7-brief.md` | 3 | 0.06MB |
| `Read:tasks.md` | 4 | 0.05MB |
| `Read:task-2-brief.md` | 4 | 0.05MB |
| `Read:design.md` | 3 | 0.05MB |
| `Read:task-4-brief.md` | 3 | 0.05MB |
| `Read:2026-09-10-liaison-unpack-charter.md` | 2 | 0.04MB |
| `Read:test_inbound_wiring.py` | 2 | 0.04MB |
| `Read:frames.py` | 3 | 0.04MB |
| `Read:test_notify_webhook.py` | 1 | 0.04MB |

## 被 Read 最多的文件

- 13× `/Users/paulshao/Projects/HumanResource/docs/session接力.md`
- 12× `/Users/paulshao/Projects/HumanResource/.claude/worktrees/wave2-p0-bridge/tools/liaison/unpack/bridge.py`
- 9× `/Users/paulshao/Projects/HumanResource/.claude/worktrees/wave2-p0-bridge/tools/liaison/tests/test_inbound_wiring.py`
- 8× `/Users/paulshao/Projects/HumanResource/docs/tech-debt.md`
- 8× `/Users/paulshao/Projects/HumanResource/.claude/worktrees/wave2-p2-charter/tools/liaison/unpack/charter.py`
- 7× `/Users/paulshao/Projects/HumanResource/tools/liaison/tests/test_inbound_wiring.py`
- 6× `/Users/paulshao/Projects/HumanResource/.claude/worktrees/wave2-p1-dispatch/docs/superpowers/plans/2026-09-10-liaison-unpack-dispatch.md`
- 6× `/Users/paulshao/Projects/HumanResource/.claude/worktrees/wave2-p0-bridge/.superpowers/sdd/2026-09-10-liaison-reply-bridge/review-c491230..506346f.diff`
- 6× `/Users/paulshao/Projects/HumanResource/.claude/worktrees/wave2-p0-bridge/tools/liaison/tests/test_unpack_bridge.py`
- 6× `/Users/paulshao/Projects/HumanResource/.claude/worktrees/wave2-p0-bridge/tools/liaison/tests/test_whitelist.py`

## 成本 Top 25 会话

| 日期 | 标题 | 类型 | 模型(调用) | 调用 | 首轮 | 峰值 | cache_read | 子代理 | 大回显 | $ |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 09-16 | [Mac]0916D-Token治理P3-滚动大文件归档与体积闸 | 泳道/opener | sonnet:329 | 329 | 84k | 542k | 113.7M | 0 | 5 | 25.9 |
| 09-16 | [Mac]0910G-P0回件桥实现计划 | 泳道/opener | sonnet:116 | 116 | 44k | 322k | 23.0M | 0 | 7 | 6.7 |
| 09-16 | [Mac]0916P-P0建造Task7至9 | 泳道/opener | sonnet:99 | 99 | 45k | 240k | 15.0M | 12 | 2 | 4.4 |
| 09-16 | [Mac]0916U-P2建造Task4至5 | 泳道/opener | sonnet:105 | 105 | 45k | 191k | 12.6M | 9 | 1 | 3.6 |
| 09-17 | [Mac]0916V-P3建造Task1至4 | 泳道/opener | sonnet:93 | 93 | 46k | 203k | 11.6M | 13 | 4 | 3.4 |
| 09-16 | [Mac]0910H-P1打标即开班实现计划 | 泳道/opener | sonnet:56 | 56 | 44k | 213k | 7.5M | 0 | 5 | 2.9 |
| 09-16 | [Mac]0916E-TD29收尾liaison测试脚手架收编与conf | 泳道/opener | sonnet:81 | 81 | 47k | 167k | 10.3M | 0 | 3 | 2.7 |
| 09-16 | [Mac]0916G-Token治理P4大回显拦截与P5底座实测 | 泳道/opener | sonnet:79 | 79 | 84k | 172k | 9.9M | 0 | 5 | 2.6 |
| 09-16 | [Mac]0910J-P3口径点台账实现计划 | 泳道/opener | sonnet:62 | 62 | 44k | 182k | 7.0M | 0 | 2 | 2.5 |
| 09-16 | agent-a0 | 子代理 | sonnet:58 | 58 | 29k | 187k | 7.9M | 0 | 4 | 2.1 |
| 09-16 | agent-a6 | 子代理 | opus:25 | 25 | 27k | 139k | 2.3M | 0 | 6 | 2.1 |
| 09-16 | [Mac]0916T-P2建造Task1至3 | 泳道/opener | sonnet:54 | 54 | 45k | 134k | 5.1M | 7 | 1 | 1.6 |
| 09-16 | [Mac]0916Q-P1建造Task1至3 | 泳道/opener | sonnet:55 | 55 | 45k | 144k | 5.2M | 6 | 1 | 1.6 |
| 09-16 | agent-ac | 子代理 | sonnet:46 | 46 | 32k | 147k | 4.6M | 0 | 3 | 1.6 |
| 09-16 | [Mac]0916C-Token治理P2-模型分级与AB基线 | 泳道/opener | sonnet:46 | 46 | 83k | 162k | 5.1M | 0 | 1 | 1.6 |
| 09-16 | [Mac]0916R-P1建造Task4至6 | 泳道/opener | sonnet:46 | 46 | 45k | 138k | 4.5M | 6 | 3 | 1.5 |
| 09-16 | [Mac]0916I-Token治理P6-长会话护栏与子代理画像 | 泳道/opener | sonnet:42 | 42 | 83k | 146k | 4.5M | 0 | 0 | 1.4 |
| 09-16 | [Mac]0910I-P2拆件章程正本实现计划 | 泳道/opener | sonnet:42 | 42 | 44k | 128k | 3.6M | 0 | 0 | 1.4 |
| 09-16 | [Mac]0916A-Token治理P0-账本基线与报告对账 | 泳道/opener | sonnet:38 | 38 | 83k | 140k | 4.1M | 0 | 1 | 1.4 |
| 09-16 | [Mac]0916H-Token治理P5-启动底座瘦身 | 泳道/opener | sonnet:41 | 41 | 84k | 136k | 4.5M | 0 | 1 | 1.4 |
| 09-16 | [Mac]0916N-P0建造Task1至3 | 泳道/opener | sonnet:43 | 43 | 45k | 135k | 3.7M | 6 | 1 | 1.3 |
| 09-16 | [Mac]0916S-P1建造Task7至7 | 泳道/opener | sonnet:40 | 40 | 45k | 129k | 3.3M | 4 | 1 | 1.2 |
| 09-16 | [Mac]0916O-P0建造Task4至6 | 泳道/opener | sonnet:38 | 38 | 44k | 121k | 3.4M | 6 | 2 | 1.1 |
| 09-16 | agent-a8 | 子代理 | opus:16 | 16 | 25k | 84k | 913k | 0 | 1 | 1.0 |
| 09-16 | agent-a8 | 子代理 | opus:15 | 15 | 24k | 82k | 888k | 0 | 3 | 1.0 |

## 子代理按角色（Phase 6）

| 角色 | 个数 | 调用 | 平均调用/个 | 峰值上下文中位数 | 成本$ |
|---|---:|---:|---:|---:|---:|
| final-review | 25 | 292 | 11.7 | 55k | 12 |
| implementer | 22 | 395 | 18.0 | 58k | 5 |
| 其他 | 9 | 75 | 8.3 | 49k | 1 |
| spec-review | 7 | 46 | 6.6 | 53k | 1 |
| fix | 5 | 14 | 2.8 | 34k | 0 |
| explore | 1 | 7 | 7.0 | 52k | 0 |

### 子代理成本 Top 15 父会话

| 父会话文件 | 子代理成本$ | 角色分布 |
|---|---:|---|
| [Mac]0916P-P0建造Task7至9 | 6.6 | {'final-review': 6, 'spec-review': 1, 'implementer': 3, 'fix': 2} |
| [Mac]0916S-P1建造Task7至7 | 3.4 | {'final-review': 2, 'spec-review': 1, 'implementer': 1} |
| [Mac]0916V-P3建造Task1至4 | 2.9 | {'final-review': 7, 'implementer': 4, 'fix': 2} |
| [Mac]0916U-P2建造Task4至5 | 2.3 | {'implementer': 2, '其他': 3, 'spec-review': 1, 'final-review': 3} |
| [Mac]0916R-P1建造Task4至6 | 1.2 | {'implementer': 3, 'spec-review': 3} |
| [Mac]0916O-P0建造Task4至6 | 1.2 | {'其他': 2, 'implementer': 3, 'spec-review': 1} |
| [Mac]0916Q-P1建造Task1至3 | 1.0 | {'final-review': 3, '其他': 3} |
| [Mac]0916N-P0建造Task1至3 | 0.8 | {'implementer': 3, 'final-review': 3} |
| [Mac]0916T-P2建造Task1至3 | 0.7 | {'fix': 1, '其他': 1, 'explore': 1, 'implementer': 3, 'final-review': 1} |

### 「其他」委派消息样例（用来校正 ROLE_PATTERNS）

- 2× `⚠️ CRITICAL: You must do ALL work inside this exact worktree directory`
- 2× `You are a task reviewer for a Superpowers subagent-driven-development `
- 1× `你在仓库 worktree `/Users/paulshao/Projects/HumanResource/.claude/worktree`
- 1× `Run these two commands from `/Users/paulshao/Projects/HumanResource` a`
- 1× `I need you to run shell commands and report their EXACT, LITERAL outpu`
- 1× `You are the task reviewer for Task 5 of the `liaison-unpack-charter` p`
- 1× `You are reviewing one task's implementation: first whether it matches `
