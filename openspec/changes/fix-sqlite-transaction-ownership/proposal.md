## Why

CI 首次上线（`4ddf53b`）即在 `test` job（windows-latest + Python 3.14）抓到三个本地 macOS 测不出来的失败用例，报错为 `sqlite3.OperationalError: cannot commit / rollback - no transaction is active` 与 `cannot start a transaction within a transaction`。根因已定位（见 `docs/findings/2026-08-13-sqlite-事务归属冲突.md`）：`app/graph/build.py` 结尾 `SqliteSaver(conn)` 与 effect 层（`app/storage/idempotency.py`）共用同一个 `sqlite3.Connection`，连接上存在两个互相不知情的事务管理者——LangGraph checkpointer 每个 superstep 之间自行 `commit`，effect 层的幂等装饰器也自行 `commit`/`rollback`。Python `sqlite3` 模块内部记录的事务状态因此与 SQLite 实际状态脱节。

这不是偶发问题：对比最近两次 CI 运行，代码完全未变（第二次是纯文档提交），但具体哪三个用例失败换了一批，只有一个重叠。说明连接状态污染由执行时序细节决定谁先撞上，而非某个用例天生有缺陷——**根因是平台无关的逻辑缺陷，只是被 CI 的 Windows + SQLite 3.50.4 环境更容易触发**，本地 macOS + SQLite 3.53.3 恰好没踩中那个交错顺序。按部署约束第 5 条，M2 起处理真实简历前必须解决；目标服务器 `.51` 就是 Windows，大概率会在生产环境精确复现。

这条缺陷还直接命中 `CLAUDE.md` 工程铁律第 1、2 条：幂等键机制依赖 `effect_log` 与业务写入在同一个事务里原子提交，事务被第三方（checkpointer）随机切断后，这个保证不再成立。`idempotency.py:45` 的异常兜底 `rollback()` 在已无事务的连接上二次抛异常，还会把原始错误掩盖成一个误导性的 rollback 报错，现场排查会被带偏。

## What Changes

- 修复 `app/graph/build.py` 中 checkpointer 与 effect 层共用同一 `sqlite3.Connection` 导致的事务归属冲突，使同一连接上只有一个事务管理者
- 修复 `app/storage/idempotency.py:45` 的兜底 `rollback()` 会在无事务连接上二次抛异常、掩盖原始错误的问题
- 新增能在任意平台（不依赖"本地测不出来"这种偶然性）稳定复现该事务冲突的回归测试，覆盖机制本身而非当前这几个具体失败用例的名字
- 新增验证："幂等键与业务写入原子提交"这条保证在事务被第三方（checkpointer）切断后仍然成立的测试断言，对应工程铁律第 1、2 条的审计要求

## Capabilities

### New Capabilities

- `effect-transaction-integrity`：effect 层幂等写入与编排层 checkpoint 持久化之间的事务边界所有权保证——同一连接上事务管理者必须唯一、异常路径不得掩盖原始错误、该保证可在任意平台确定性回归验证

### Modified Capabilities

（无。`job-profile-intake` / `job-profile-approval` / `job-description` 三个业务能力尚未从 `m1-job-profile-intake` 归档进 `openspec/specs/`，此变更也不改变它们的对外行为契约——`job-profile-approval` 的"副作用幂等"需求描述的是业务可观察行为，本变更修的是支撑该行为的底层事务实现，行为契约本身不变）

## Non-goals（不做什么）

- **不迁移到 Postgres**——M2 迁移时的整体方案单独评估，本变更只解决 SQLite 阶段的事务归属冲突，见 `design.md` 的候选方向取舍
- **不改动 LangGraph 图结构**（节点划分、`compute_*`/`effect_*` 命名、线性链路）——已证伪"并发派发"假设，问题不在图结构，不借这次修复顺带重构
- **不新增 effect 节点或改变幂等键格式** `{thread_id}:{node_name}:{business_key}`——现有格式不变
- **不处理这三个当前失败用例之外的其他潜在事务问题**（如果排查中发现无关问题，另开变更）
- **不对 CI 环境（Windows runner、SQLite 版本）做任何调整**——`.github/workflows/ci.yml` 本次不动

## 合规影响说明

本变更不处理候选人个人信息，不涉及新的个人信息处理场景。影响面限于系统内部事务完整性与审计留痕可靠性——修复后，`effect_log` 与业务写入的原子提交保证重新成立，这是工程铁律第 1、2 条与后续 PIPL 审计（"谁在什么时候看了谁的简历"可查）的前置基础，M2 处理真实简历前必须先具备这个保证。

## Impact

**受影响代码**
- `app/graph/build.py`——`SqliteSaver(conn)` 的连接来源
- `app/storage/idempotency.py`——`idempotent_effect` 装饰器的 commit/rollback 逻辑
- `app/channels/web_channel.py`——`deliver()` 的写入触发了第一现场报错
- `app/graph/nodes.py`——`effect_persist_draft`、`effect_deliver_message` 等被装饰的 effect 节点

**受影响测试**
- `tests/test_graph_nodes.py`、`tests/test_web_api.py`——当前受此缺陷影响而在 CI 上间歇性失败的用例；新增回归测试文件覆盖机制本身

**不受影响**
- 对外 API 行为、业务画像 Schema、企业微信通道（尚未接入）、部署配置
