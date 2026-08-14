## Context

见 `proposal.md - Why` 与 `docs/findings/2026-08-13-sqlite-事务归属冲突.md`（根因已定位，本设计直接采纳，不重新排查）。这里只补设计需要的现状约束：

- `app/graph/build.py` 目前把 effect 层与 `SqliteSaver` checkpointer 绑在**同一个 `sqlite3.Connection`** 上，注释明确说明这是刻意的（"reuses the connection this function already received rather than opening a second one to the same file"）——即当前实现是权衡后的选择，不是疏忽，设计必须正面回应"为什么当初这样做、现在为什么要改"。
- 执行模型是严格线性链（`compute → persist → deliver → END`），已证伪"应用层节点被并发派发导致互相踩"这个假设——**这不等于连接完全不会被跨线程访问**：LangGraph 的 Pregel 执行器内部本来就用后台线程池派发节点/checkpoint 调用（`BackgroundExecutor`），即便图无分叉；`SqliteSaver` 自己用 `threading.Lock` 序列化了它对连接的访问，所以这一层线程池不会让它自己跟自己竞争（见 `docs/findings/2026-08-13-sqlite-事务归属冲突.md` §3.1）。真正不会发生的是**应用代码之间的并发写冲突**，本设计不需要为"我们自己的 effect 节点互相竞争"做防御，只需要让"同一个连接上事务状态账本不被两个记账人各自维护"这一件事成立。
- `idempotent_effect` 装饰器（`app/storage/idempotency.py`）的 commit/rollback 是 effect 层唯一的事务管理逻辑，除它之外没有其他地方手动管理事务。
- SqliteSaver 何时提交（每个 superstep 之间）是 LangGraph 库的内部实现细节，不是公开契约的一部分，也没有开关可以关闭。
- M2 计划整体迁移到 Postgres + `langgraph-checkpoint-postgres`（见 `01-开源调研与技术选型.md`），checkpointer 与 effect 层大概率天然使用连接池中的不同连接，本设计的选择不应该给那次迁移增加额外负担。

## Goals / Non-Goals

**Goals**

- 让 effect 层与 checkpointer 各自的事务边界互不干扰，无论以什么顺序交替执行
- 让异常清理路径本身不产生新的、掩盖原始错误的异常
- 修复本身要能用一条在任意平台确定性触发的回归测试锁定，防止同类问题以不同的用例名字复发（对应 `specs/effect-transaction-integrity/spec.md`）

**Non-Goals**

- 不为真实并发写冲突设计防护（已证伪，见 Context）
- 不改变 `effect_log` 幂等键的格式或 `idempotent_effect` 的对外调用签名
- 不在这次修复里预先实现 Postgres 迁移的连接管理方案，只保证这次的选择不会让那次迁移更难

## Decisions

### 候选方向评估

三个方向来自证据包第 6 节（当时明确标注"未评估"），以下是本次设计阶段的实际取舍论证。

#### 方向 A：给 checkpointer 单开一个连接

**做法**：`build_intake_graph` 不再把调用方传入的 `conn` 交给 `SqliteSaver`，而是内部对同一个数据库文件再开一个专用连接，通过 `SqliteSaver.from_conn_string(db_path)` 以 `with` 正确持有（避免重蹈 `build.py` 现有注释里提到的"直接用会破坏 `graph.compile()`"那个坑），生命周期与图对象绑定。

**代价**：两个连接写同一个 SQLite 文件，默认的 rollback-journal 模式下，一个连接持有写锁时另一个连接的写操作会立刻收到 `database is locked`（`SQLITE_BUSY`）。需要显式把数据库文件的 journal mode 切到 WAL（`PRAGMA journal_mode=WAL`，这是文件级设置，两个连接共享同一份文件头，任一连接设置一次即可对整个文件生效），并给两个连接都设置一个非零 `busy_timeout` 作为兜底——即便执行模型是线性无并发（见 Context），也不依赖"绝对不会有哪怕一瞬间的锁竞争"这种脆弱假设。

**为什么可行**：这是"每个持久化组件拥有自己的连接"这个更朴素、更符合直觉的不变量,不需要理解 LangGraph 内部什么时候提交,只需要保证它的连接不被别人共用。诊断价值也更高——真出问题时报错会是标准的 `database is locked`,而不是当前这种"事务状态账本对不上"的诡异错误,排查成本更低。

#### 方向 B：把事务边界收敛到一处（不新开连接，让 effect 层或 checkpointer 二选一放弃自行提交）

评估了两个子方向，都否决：

1. **effect 层放弃自行 commit/rollback，依赖 checkpointer 的提交把所有变更一起刷盘**——表面上更"原子"（业务写入 + `effect_log` + checkpoint 状态一次性提交），但 `idempotency.py:36-46` 现有的异常路径 `rollback()` 是有意义的：一旦 `fn` 写了一半就抛异常，必须立刻回滚，否则这些半成品写入会留在隐式事务里，等着**下一次任意无关的 commit**把它们意外持久化——这正是这次要修的 bug 的镜像版本。若 effect 层放弃自己管理事务，出错时清理谁来做、什么时候做，答案会落到"依赖 LangGraph 什么时候提交/是否提交",这是我们不掌控、没有公开契约、库版本升级可能静默改变的内部行为。工程铁律要求幂等性"可审计",建立在别人未公开承诺的行为上不满足这个要求。
2. **checkpointer 放弃自行 commit，改由 effect 层的提交顺带把 checkpoint 一起刷盘**——`SqliteSaver` 的提交时机是库内部实现,没有公开开关可以关闭,唯一办法是继承并覆写内部方法,相当于给一个第三方库的私有实现细节维护一个本地补丁,每次升级 `langgraph`（本项目已固定 `>=1.0.10` 并因 CVE 而对版本敏感）都要重新核对这个补丁是否仍然成立。维护成本和脆弱性都高于方向 A。

**结论**：方向 B 的两个子方向本质上都是"强迫两个生命周期、错误处理需求都不同的组件共用一个事务边界",这比"给它们分别一个边界"更难维护，否决。

#### 方向 C：推迟到 M2 迁 Postgres 一并解决

**否决理由**：

- M1 demo 已经实际部署在 `.51`（Windows）并完成过真实浏览器/curl 端到端验证（见 `tasks.md` 0.10）。`.51` 与 CI 的 Windows runner 大概率是同一类环境，这个 bug **现在就可能在生产 demo 环境触发**，不是一个只存在于未来 M2 的风险。
- CI 从上线第一天就是红的。每一个后续 PR 都要背着这个已知会间歇性失败的信号，会迅速侵蚀团队对 CI 结果的信任——"CI 红是不是这个老问题"会变成每次失败都要先排除的噪音。
- 部署约束第 5 条要求"M2 起处理真实简历前必须具备可识别到人的登录留痕"，这是**前置条件**而不是"和 M2 一起做的事情之一"。把一个独立的事务正确性 bug 捆进 M2 迁移，只会让那次本就复杂的迁移背上不相关的额外风险面，且不能保证同一个逻辑缺陷不会在 Postgres 连接管理方式下以另一种形式复现——迁移本身不构成修复，只是换了一个可能重新踩坑的环境。
- Postgres 迁移到来时，`langgraph-checkpoint-postgres` 通常从连接池中获取连接，与业务层的连接天然分离，这与方向 A 的"各自持有连接"思路是一致的，不冲突、不需要返工。

**采纳**：方向 A（给 checkpointer 单开专用连接，启用 WAL + busy_timeout）。方向 A 与未来的 Postgres 迁移方向一致，不产生技术债。

### 异常掩盖问题的修复

`idempotency.py:36-46` 的 `except Exception: conn.rollback(); raise` 改为把 `conn.rollback()` 本身也纳入保护：如果清理动作抛出异常，捕获它但不让它替换原始异常向上传播——调用方最终看到的必须是触发失败的原始异常。这一处修复与方向 A 相互独立：方向 A 消除"清理时事务早被切断"这一类触发条件的根源，本处修复是不依赖根因是否被消除都成立的防御——就算未来出现方向 A 没预料到的场景导致连接的事务状态又一次对不上，异常掩盖这个次生问题也不会再发生。

## Risks / Trade-offs

- **WAL 模式改变了数据库文件的落盘方式**（写入 `-wal`/`-shm` 旁路文件，checkpoint 到主文件的时机不同于 rollback-journal）→ 影响面仅限这一个 SQLite demo 数据库文件，没有其他进程或工具依赖当前的 rollback-journal 语义（部署形态是单进程 Windows 计划任务，见 `04-部署与门户挂载.md`）；迁移只需在启动时对目标文件执行一次 `PRAGMA journal_mode=WAL`，幂等、可重复执行
- **两个连接仍是同一个 SQLite 文件，`busy_timeout` 只是兜底不是根治** → 已证伪的是"应用层 effect 节点互相竞争"，不是"连接绝不会被跨线程访问"——LangGraph 内部的线程池派发是已确认存在的现状（见 Context），`busy_timeout` 不是为一个纯假设性的未来风险兜底，而是对这个已确认存在的跨线程调度做纵深防御：即便 `SqliteSaver` 自己的锁已经序列化了它对自己连接的访问，`busy_timeout` 仍能防止任何未预料到的短暂重叠表现为立刻报错崩溃，而是短暂阻塞重试
- **新增连接的生命周期管理**：`build_intake_graph` 目前是无状态的图构建函数,新增一个需要显式关闭的连接会改变调用方的生命周期管理责任 → 复用 `build.py` 现有注释已经点出的教训（`from_conn_string` 返回上下文管理器不能直接用），本次实现需要让新连接的生命周期与图对象/应用生命周期绑定一致，在 `tasks.md` 对应章节里必须包含"进程正常退出与异常退出都不遗留未关闭连接"的验证

## Migration Plan

不涉及数据结构变更，属于纯代码修复，无需数据迁移：

1. 先落地"任意平台确定性复现"的回归测试（对应 spec 的 Requirement），在修复前跑一次确认它能稳定失败——这一步是防止"以为修好了但其实只是换了个环境没触发"
2. 实现方向 A：checkpointer 专用连接 + WAL + busy_timeout
3. 实现异常掩盖修复：`idempotency.py` 清理路径不产生次生异常
4. 回归测试转绿，且原三个已知失败用例（`test_build_intake_graph_runs_end_to_end` / `test_app_works_when_mounted_at_arbitrary_subpath` / `test_second_turn_prompt_contains_first_turn_message_and_known_fields`，以及本轮 CI 观察到的另一批同根因用例）全部通过
5. 幂等专项验证：强制中断后重放，断言业务写入与 `effect_log` 恰好各一份（对应工程铁律第 1、2 条审计断言）

**回滚**：纯代码改动，回滚即恢复到修复前的 commit。回滚后 CI 会重新变红（回到本变更提出时的已知状态），不产生数据层面的不可逆变化。
