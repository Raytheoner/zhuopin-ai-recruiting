---
name: lane-watch-relay
description: 续棒泳道看护——新会话免粘贴 Opener 直接接上上一场的泳道看护。当 Shao Peishen 说"续棒泳道看护""看泳道""接上泳道""泳道续棒""接棒看护"时使用。按固定只读清单接手（接力卡新节＋扫池结论工具＋最新 results.tsv＋定夺队列），逐条报状态并出定夺项。⛔ 本技能不亲自执行任何 opener、不发车。
---

**这份 skill 存在的理由**：以前续棒要 Shao Peishen 手工粘一份转场 Opener。现在他只需在新会话说一句
「续棒泳道看护」，接手动作全部写死在本文件里。⛔ 不再出转场 Opener 让他粘。

---

## 0. 边界 —— 先读，违反即全错

🔴 **本项目只用本仓库的资产。** 泳道看护的正本是本仓库 `.claude/skills/lane-dispatch/SKILL.md` ＋ 本文件。

- ⛔ **不读、不调、不引用其他项目的 skill**（`zhuopin-lane-watch`／`zhuopin-lan-closeout` 等「企业AI转型」线的技能与其 GitHub 正本一律不碰）——它们是另一条线的规则，判据与本项目不同，照跑必错。
- ⛔ **GitHub 只是存储，不是运行时**：本机构建本地项目，永不从 GitHub 拉 skill 来执行。
- ✅ **唯一例外**：Shao Peishen 明确说「参考 XX 项目的做法」时，才去只读参考，且**只模仿做法、在本仓库自建实现**，⛔ 不 clone、不跨仓库引用。

路径（两侧不同，别写错）：

| 侧 | 仓库路径 | 能不能碰 git |
|---|---|---|
| Cowork（隔离 VM 挂载） | `$HOME/mnt/HumanResource` | ⛔ 不碰（会在 `.git/` 留删不掉的 `index.lock`），提交走 `scripts/commit_request.py` |
| CC／本机 | `/Users/paulshao/Projects/HumanResource` | ✅ 可以 |

---

## 1. 第一步：会话编号与标题

Cowork 侧**没有** `set_session_title` 工具（CC 侧才有），所以标题只能由他的第一句话带出来。

**约定（2026-09-20 Shao Peishen 定）**：续棒会话固定用 **`R` 系列**，他开场只要打这一行——
它既是标题来源，也是触发词，不需要查号池：

```
[Mac]MMDDR 续棒泳道看护
```

- `[Mac]` ＝ 本机 Mac Studio；他的 Win 笔记本用 `[Win]`。`MMDD` 取当天（中国时区）。
- 同一天第二场续棒用 `R2`、第三场 `R3`，依此类推。
- 🔴 **本技能开场第一件事**：实跑 `TZ=Asia/Shanghai date +%m%d` 取真日期，核对他打的 `MMDD`；
  不一致或他没带编号 ⇒ 在第一句回话里把正确的一行原样给他（他点标题下拉改一次即可），⛔ 不停下等他改。
- 🔴 **当场登记进 `docs/openers/号池台账.md`**：`R` 系列与 `A/B/C…` 共用同一号池，不登记下轮必撞。

---

## 2. 第二步：只读接手 —— 一次跑完，⛔ 不逐条试探

在**本机侧或挂载侧**（都只读，不碰 git）一次性跑完下面这组，**不要拆成多轮**：

```bash
cd <上表对应的仓库路径>
TZ=Asia/Shanghai date '+%m%d %H:%M %Z'
sed -n '/^## 🔴 新 session 先看这一节/,/^## 开场词/p' docs/session接力.md    # 上一场交接结论
PYTHONPATH=. python3 -m scripts.queue_pending                              # 定夺队列：只出待答
PYTHONPATH=. python3 -m scripts.dispatcher_backlog --dry-run | tail -14    # 台账摘要＋ready
pgrep -f 'run-lanes.*\.sh' || echo "无泳道在跑"
D=$(ls -dt .claude/handoff/lanes-2026* | head -1); echo "$D"; cat "$D/results.tsv"
ls -t .claude/handoff/commit/*.done | head -1 | xargs tail -4             # 调度器最近一次
git -C <仓库> log --oneline -5   # ⚠️ 只在本机侧跑
```

🔴 **只读结论性清单，⛔ 禁止**：`cat` 泳道日志正文、`cat` 全量定夺队列、整目录列举、读 opener 正文、
拉全文 `git diff`。判「某条还失不失败」只看 `results.tsv` 的行与汇总数字。
（依据：09-19 复盘——开场读太多是本项目 token 的头号去处。）

大文件（> 40 KB）先 `grep` 定位再分段读，hook 会拦整读。

---

## 3. 第三步：逐条报 —— 两栏各归各位

按 `CLAUDE.md`「会话末需你定夺」写：

- **状态同步（无需你答）**：泳道 results 逐条（编号｜标题｜OK/FAIL｜哨兵）；调度器最近一次自扫结论；
  `.51` 生产健康与最近发版；台账数字（待开／在跑／完成／阻塞／ready）。
  ⛔ 汇报类一律普通文字，**不做成代码块**。
- **需你定夺**：编号清单，每项带 `(a)/(b)` 与推荐项，写清选它会发生什么。
  末尾单个无语言标签代码块，**块里只放他要回的那几个字符**（如 `1a，2a`），说明一律移到块外。
- 无决策项 ⇒ 明写「**本次无需你决策**」。

---

## 4. 第四步：接着做什么

| 情形 | 处置 |
|---|---|
| `ready` 非空且均非不可代项 | 交 `lane-dispatch` 编排发车（本技能⛔ 不亲自跑 opener） |
| `ready` 全是 `gate` 且行内写着「待开 X 交付」 | 是闸未满足，**不是可发车**——报出来，⛔ 不硬派 |
| 有 FAIL／NO-SENTINEL | `tail -50` 该条日志判真失败还是漏哨兵，⛔ 不读全文 |
| off-LAN（`.51:8095` 不通，周末常态） | 优先机制类（`[D:机]`）与快速缩编；`.51` 部署类挂起等他回 LAN |
| 只剩外部输入／不可代项 | 明写「本轮无可发车项」并列出卡在谁身上，⛔ 不追问 |

🔴 **永不代办**：合规红线七条、候选人对外通道（邀请链接／拒信／邀约）、`.51` 发版决定、
真实简历范围、预算采购、任何对外发送。做到「可发送态」即停。

---

## 5. 落档与提交

本技能产出的结论写进 `docs/session接力.md`（滚动闸 ≤ 40 KB，超了先跑 `scripts/archive_docs.py --apply`）。
Cowork 侧提交走 `scripts/commit_request.py`（带 `--repo` 指到挂载路径），⛔ 不在挂载侧直接 git。
