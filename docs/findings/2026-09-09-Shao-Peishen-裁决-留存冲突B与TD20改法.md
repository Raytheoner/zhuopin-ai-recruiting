# Shao Peishen 裁决（2026-09-09 中午）· 留存冲突 B 与 TD-20 改法

> 来源：Cowork `HR业务线-接力0909Q` 会话，Shao Peishen 本人当场二选一。
> 两条都是 `hr-wecom-aibot-liaison` 8.6 单机灰度的前置，代理人不得代拍，故单独落档。
> 泳道执行时以本文为唯一依据，⛔ 不再回问。

## 裁决一 · 冲突 B（名单内消息的归档在 FK 下永远清不掉）＝ 取方案 ②

**问题**（`0909K` 登记，P1）：`liaison_task.msgid → liaison_message(msgid)` 无 `ON DELETE`，
而 8.1 的 opener 约束写死「队列行任何情况不删」⇒ 汤丽萍/邵培申（名单内）的归档全部落进
`blocked_by_queue` 桶，只有名单外发送人的归档会被 180 天清理。属个人信息留存期问题，
与 design D13「180 天」和 proposal 合规说明相悖。

**裁决**：队列行到**终态**（`send_status='pushed'`，展示层「✅ 已推送」）且超 180 天的，
连同其归档一起清；`pending`（🆕 待发）/ `deferred`（⏸ 暂缓）的不动——那两态仍是活台账。

落地口径（供泳道逐条执行）：

- D13 的「队列行不参与自动清理」改成「**非终态**队列行不参与自动清理；终态且超期的队列行
  连同其归档一起清」。`design.md` §D13 与 `specs/liaison-message-archive/spec.md` 的留存条款
  各改一句，⛔ 不改别处
- `blocked_by_queue` 一分为二，新桶要求**两个时间都过期**（消息 `archived_at` 与队列行
  `pushed_at` 都早于 cutoff）——少一个就不删
- 删除顺序 FK 安全：先 `liaison_task` 行 → 再 `liaison_message` 行 → 再归档文件；
  单条失败不中止整轮；幂等键沿用 `RETENTION_DELETE_NODE`，⛔ 不新增 effect 节点名
  （那个名字同时是 `assert_effect_log_identity` 的依据，改它会同时断两处）

**未选的两条**（留作记录）：① 保持现状只报计数——名单内归档无限期驻留，与 D13 相悖；
③ 删台账行前把 `liaison_task.msgid` 置空——队列行失去回指来源消息，打破 5.5。

## 裁决二 · TD-20（启动补记用启动时间当恢复时间，低报中断时长）＝ 取改法 ①

**问题**（`0909F` 留步）：`LiaisonSession.start()` 在**尚未连上**的时刻就把所有未闭合窗口
按「恢复时间 = 本次启动时间」闭合并告警。reviewer 实测：connected@10:10 → 10:40 网络未恢复时
重启 → 12:00 才真正连上，结果只发一条「10:10–10:40（30 分）」的告警，**低报 80 分钟**，
收信人按偏短的区间补发，区间外的消息永远补不回来。

**裁决**：改法 ①——**启动时不闭合旧窗口**，留到 `on_connected` 真正连上时再闭合，
告警因此自然带上真实恢复时间。

落地口径：

- `start()` ⛔ 不再调 `_backfill_open_windows(now)`；其余步骤与顺序不变
  （读存活戳 → 必要时开 `startup_gap` 窗口 → 补发「已闭合未告警」的旧窗口 → 最后写存活戳）
- 闭合改由 `on_connected` 的既有路径负责（`CLOSED_BY_RECONNECT` ＝ 真实恢复时间），
  「启动后首次连上」必须走同一条
- `CLOSED_BY_STARTUP_BACKFILL` 与 `_backfill_open_windows` 失去调用方后**保留**，
  docstring 注明「已按本裁决停用，留作历史窗口的解释」——`effect_log` 里存着用它闭合的历史行
- `specs/liaison-channel-session/spec.md`（第 46、56 两处）「恢复时间为本次启动时间」
  改成「恢复时间为下一次真正连上的时间；启动时未闭合的窗口保持开启」
- ⛔ **不单独选改法 ③**（接 `run_forever` 的 `on_attempt_failed`）——它是 ① 之上的加强，
  单独选覆盖不全。本批不做，要做另立一条

**未选的一条**：② 启动时另开一个新窗口专等 `on_connected`——多一种窗口类型要维护和测。
