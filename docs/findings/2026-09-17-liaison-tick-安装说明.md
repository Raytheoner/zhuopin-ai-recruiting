# liaison tick（Mac 侧调度）安装说明

> 来源：`[Mac]0917AA-G4Mac侧调度tick`（run-lanes 无头起）｜ 派发：Cowork·HR业务线-接力0917D
> 裁决：Shao Peishen 2026-09-17 答 2a——先做 Mac 侧调度；`.51` 侧 M1 挂起提醒随发版（TD-11 不销账）

## 它是什么

`python -m tools.liaison tick` 是一个**一次性进程**：跑一遍、退出。launchd 每 **300 秒**拉起一次
（`StartInterval`），⛔ 不是常驻循环、不开后台线程（TD-11 口径）。每次做三件事，**全部只入队**
（写 `data/liaison.db` 的 `owner_notify_outbox`），真正私信本人由值守服务本体
（`com.zhuopin.hr.liaison`）在连着时发：

| # | 扫什么 | 幂等键 | 何时入队 |
|---|---|---|---|
| 1 | `.claude/handoff/lanes-*/summary.txt`（批次已收敛） | 批次目录名（与 run-lanes.sh 自己那条 `owner-notify` 同一把键） | 回看窗内（今天＋昨天，按目录名日期）且发件箱里没有 |
| 2 | `docs/跟进信/README-跟进信清单.md` 里「✅ 已推送 <日期>」起头、交期列没写「不催」的行 | `followup:<编号>:<第几次>` | 推送满 3 天第 1 次、满 7 天第 2 次（`>=`，漏跑补一次不重复） |
| 3 | `docs/findings/2026-09-17-值守通道一周观察窗.md` 的「到期：YYYY-MM-DD」 | `observation:<文件名>:<到期日>` | 到期当天（或之后首次跑到）一次；解析失败 ⇒ 记告警跳过 |

「自然日」按中国日历（CST）算，⛔ 不按本机 EDT。三类扫描各自 try/except，一类失败不拖死另外两类。
⛔ 只提醒本人，⛔ 不给专员发——收件人由值守服务本体从名单按本人姓名解析，tick 与 CLI 都没有收件人参数。

## Shao Peishen 要跑的一行（Terminal，本人身份）

```
python3 /Users/paulshao/Projects/HumanResource/scripts/install_liaison_tick.py
```

- 幂等：重复跑等价于重装（先 `bootout` 再 `bootstrap`）。
- 无害预检：`tools/liaison/.venv/bin/python` 不存在会当场退回并说明，⛔ 不写一份起不来的 plist。
- 只看不装：加 `--print`，打印将写入的 plist 原文，不写文件、不碰 launchctl。
- 装好后立刻跑一次（`RunAtLoad`），之后每 5 分钟一次。日志：`data/liaison/logs/tick.out.log` / `tick.err.log`。
- 手动跑一次验证：`cd /Users/paulshao/Projects/HumanResource && tools/liaison/.venv/bin/python -m tools.liaison tick`
  ——输出形如 `tick：入队 N，已存在 M，告警 K`。

装 LaunchAgent 属安全配置变更（与改 settings.json 同类），⛔ Claude 不代做。

## 值守服务本体要不要重启

**要。** tick 入队的行由本体的值守线程在空闲 tick 里消费（`0917Y` 的 `_drain_owner_notify`），
本体若是 `0917Y` 合入**之前**起的老进程，它的代码里没有消费者，发件箱只进不出。判据：
`launchctl print gui/$UID/com.zhuopin.hr.liaison` 里的 `pid` 对应进程的启动时间早于 `67d2447`
合入时刻 ⇒ 重启一次：

```
launchctl kickstart -k gui/$UID/com.zhuopin.hr.liaison
```

（`-k` = 先杀再拉起；`KeepAlive` 在，⛔ 不用 bootout/bootstrap 重装。）本体没起时 tick 照样入队，
等本体起来再发，⛔ 不丢。

## 卸载

```
launchctl bootout gui/$UID ~/Library/LaunchAgents/com.zhuopin.hr.liaison-tick.plist
rm ~/Library/LaunchAgents/com.zhuopin.hr.liaison-tick.plist
```

## 仍欠（TD-11 不销账）

`.51` 侧 M1「流程长时间挂起」第 1 天 / 第 3 天提醒（tasks 6.8、5.6）——Windows 计划任务 ＋
同一 `effect_*` 幂等形状，随下一次发版做。
