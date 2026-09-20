# 0920I · 泳道看护分流表补 CTX-RELAY／RELAY-EXHAUSTED

## 零、为什么

`0920H`（已合 main，commit `08d2564`＋`2f8993e`）给无头泳道加了上下文硬闸：单条 opener 越
150k 上下文 ⇒ `context-guard.py` 注入转场哨兵 `CTX-RELAY`，`run-lanes.sh` 见到该哨兵会在**同一
泳道队尾自动排一条续棒**，续满 `HR_LANE_RELAY_MAX=3` 次仍越线则记 `RELAY-EXHAUSTED` 并停该泳
道，交人判断任务要不要拆。

但 `.claude/skills/lane-watch-relay/SKILL.md` §4「接着做什么」分流表还停在旧版本——只列了
`ready`／`FAIL`／`NO-SENTINEL`／off-LAN／外部输入五种情形，没有这两个新状态。看护者（人或续棒
会话）读表时遇到 `CTX-RELAY` 或 `RELAY-EXHAUSTED` 会无表可查，容易把前者误判成失败去 `tail -50`
诊断（浪费精力，且 `CTX-RELAY` 本来就是正常状态），或者不知道后者该怎么处置。

## 一、做什么

在 `.claude/skills/lane-watch-relay/SKILL.md` §4 分流表（现有五行，「有 FAIL／NO-SENTINEL」那
行之后）追加两行，字面沿用 `run-lanes.sh` 里实际使用的状态字符串：

| 情形 | 处置 |
|---|---|
| `CTX-RELAY`（某条 opener 越 150k 被注入转场哨兵） | **＝正常**，`run-lanes.sh` 已自动在同泳道队尾排了续棒；⛔ 不当失败处理、不用去 `tail` 日志找错误 |
| `RELAY-EXHAUSTED`（续棒已达上限 `HR_LANE_RELAY_MAX=3` 仍越线） | 该泳道已停，需人判断任务是否该拆分后再派；报出来等 Shao Peishen／Cowork 定夺，⛔ 不代为拆分 |

只改这一处表格，其余内容（含表格上下文字、其他小节）一字不动。

## 二、开工自核

1. `grep -n "CTX-RELAY\|RELAY-EXHAUSTED" .claude/skills/lane-watch-relay/SKILL.md` 必须先是**零
   命中**（防重复添加；若已有命中说明别的会话已经做过，直接 `git log` 核实后原地收工，不重复改）
2. `grep -n "CTX-RELAY\|RELAY-EXHAUSTED" docs/openers/run-lanes.sh` 必须命中（防止字面写错、抄到
   不存在的状态名）

## 三、完成判据

- `git diff --stat -- .claude/skills/lane-watch-relay/SKILL.md` 只显示新增两行（`+2`／无
  `-`，除非原表格结尾缺一个换行需要顺带补）
- 新增两行的状态字面与 `run-lanes.sh` 里的字符串逐字一致（区分大小写、连字符）
- `git diff` 里不出现除这一个文件之外的任何改动

## 四、并发协议（逐字内嵌，⛔ 不省略）

1. **只 `git add .claude/skills/lane-watch-relay/SKILL.md` 这一个路径。** ⛔ 禁止 `git add -A` /
   `git add .` / `git commit -a` / `git stash`（含 `-u`）——这是并行成立的唯一前提
2. `git status` 里出现别人的改动是正常的，不要停下、不要问、不要顺手提交
3. push 被拒 → `git pull --rebase --autostash origin main` 后重试，最多 3 次。⛔ 不要在 commit 前
   pull
4. 报 `.git/index.lock` 已存在 → 等 5 秒重试最多 5 次，⛔ 默认绝不删除该锁（判据见 CLAUDE.md 同条）

## 五、红线

- 只改 `.claude/skills/lane-watch-relay/SKILL.md` 一个文件，⛔ 不碰 `lane-dispatch`／`kickoff`／
  `run-build`／`spec-to-plan` 等其它 skill
- ⛔ 不改 `docs/openers/run-lanes.sh`（那是读它取状态字面值，不是改它）
- ⛔ 不碰 `.51`

## 六、收口

1. `git add .claude/skills/lane-watch-relay/SKILL.md`
2. `git commit -m "docs(skills): lane-watch-relay §4 分流表补 CTX-RELAY／RELAY-EXHAUSTED（0920I）"`
3. `git push`
4. 收工报告：改了几行、`git log --oneline -1`、`OPENER_DONE` 或 `OPENER_PARTIAL: <原因>`
