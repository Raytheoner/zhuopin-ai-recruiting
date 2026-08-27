---
name: lane-dispatch
description: 编排并发车一批泳道任务。当 Shao Peishen 说"开始泳道看护""发一批泳道""跑下一批""排下一批开工""lane-dispatch"时使用。全流程：扫待办 → 判触碰区分泳道 → 写 opener 进编排文件 → dry-run 核对 → 出看护 opener 与发车命令。⛔ 本技能不亲自执行任何 opener。
---

把"下一批做什么"变成一次可发车的泳道编排。**产出是编排 + 两条命令，不是代码。**

真源：`docs/openers/OP-0820-全量编排.md`（编排与全部 opener 正文）、
`docs/openers/run-lanes.sh`（执行器）。口径以 `CLAUDE.md` 为准，不一致以 CLAUDE.md 为准。

---

## 四步，按顺序做完再回话

### ① 定这批做什么 —— 先看真身，别问

⛔ **不要上来就问"这批做什么"。** 待办是可以查出来的，查完再确认比空问有用。

```bash
cd /Users/paulshao/Projects/HumanResource && git pull --rebase origin main
git log --oneline -12 && git status --short
# 各变更包的章节进度
for T in openspec/changes/*/tasks.md; do echo "== $T"; grep -c '^- \[x\]' $T; grep -c '^- \[ \]' $T; done
ls -t docs/superpowers/plans/*.md | head -6   # 哪些单元的 plan 已就绪
```

**入选判据（三条全中才算「可发车」）**：

1. **plan 已就绪**且 `grep -c '^### Task '` 不为 0
2. **前置已合进 main** —— ⚠️ 用 `git cherry -v main <分支> | grep -c '^+'` 判，
   ⛔ 不要只看 `git rev-list --count`：main 被 rebase 过时它会假阳性
   （实证：单元 C 与 unit-e 都撞过，`rev-list` 非 0 而真未合 = 0）
3. **不属不可代项** —— 合规红线、候选人对外通道、`.51` 发版、真实简历范围、预算采购
   一律不进泳道，登记后等 Shao Peishen 本人

### ② 判触碰区，分泳道 —— 这一步最容易错，也最值钱

**判据只有一条：触碰文件是否重叠。重叠即同泳道串行，零重叠才跨泳道并行。**

⛔ 不要凭"感觉像是独立的"就并行。去 `delivery-units.md` 的对应单元节里读它自报的触碰区，
读不到就自己 grep 那几个文件谁会改。**本批最热的文件历来是
`app/agents/intake_agent.py` 与 `app/graph/nodes.py`** —— 碰它们的单元一律串行。

*正面例子*：2026-08-27 F ∥ U2 能并行，是因为 `delivery-units.md` §2.U2 明写
"全新目录、与整个仓库零文件重叠"，不是因为它们看起来无关。

### ③ 写进编排文件

每条 opener 在 `OP-0820-全量编排.md` 里是这个形状——**`> 泳道：` 那行必须在代码块前一行**，
`run-lanes.sh` 靠它分组：

```
> 泳道：<泳道名>

​```
[Mac]MMDDX-<主题短名>
【设置】执行环境: CC ｜ Session: 新开 ｜ 分支: ... ｜ worktree: ... ｜ 工作区: ...
...
​```
```

编号规则见 `kickoff` skill「编号与抬头」：**MMDD 实跑 `TZ=Asia/Shanghai date +%m%d` 取**，
序号只用字母。

**opener 正文里必须挖出并写死的三类东西**（不写，reviewer 就查不到，而它们错了都不报错）：

1. **跨单元接口约束** —— 从 `delivery-units.md` 的「跨单元接口约定」节逐条抄，
   要求逐字进 plan 的 Global Constraints
2. **收口验证** —— 合并后 `git rev-list --count main..<分支>`，非 0 时再跑
   `git cherry -v main <分支>`：全 `-` 是 rebase 假阳性，有 `+` 才是真没合。
   ⛔ 确认内容真在 main 上之前不许输出 `OPENER_DONE`
3. **并行同伴的触碰区** —— 明确告诉它"另一条在改 X，你不要碰"

### ④ 核对并发车

```bash
bash docs/openers/run-lanes.sh --dry-run
```

**逐条核这四样，任一不对就停下修，不要硬发**：

- 泳道数、条目数与你的编排一致
- **每条都带「正文 NN 行」** —— 0 行即抽取失败，实跑必 NO-BODY
- 无 `index.lock`（有锁会 `exit 14`，带锁开跑五条泳道会全灭）
- 工作区没有本批相关的未提交改动

然后给 Shao Peishen **两样，一次给全**：

1. **看护者 opener** —— `OP-0820-全量编排.md` 顶部那份 `[Mac]MMDDZ-泳道批次看护`，
   **把里面的预期值改成本批的**（几泳道几条、各多少行）
2. **发车命令**：

   ```bash
   cd /Users/paulshao/Projects/HumanResource
   bash docs/openers/run-lanes.sh --dry-run
   bash docs/openers/run-lanes.sh --chain --full-auto --yes
   ```

   ⚠️ **首次跑某个新类型的任务时建议先不加 `--chain`**（如第一批 run-build），
   拿到耗时与用量样本再开链式。链式用 `exec` 重启自身，中途 `Ctrl-C` 只杀当前轮，
   已起的子 session 要用 `claude agents` / `claude stop` 收。

---

## 不要做

- ⛔ **不要亲自执行 opener**。本技能只编排，执行是脚本起的独立 session 的事。
- ⛔ **不要手工摘泳道标注**。跑成的条目由 `run-lanes.sh` 的 `mark_done()` 自动摘
  （只摘 OK/PARTIAL）。你手工摘会和它打架。
- ⛔ **不要把不可代项排进泳道**（见 ①.3）。
- ⛔ **不要在 opener 里写提问句**。它们在无人值守下跑，没人能回答，写了即空转且不报错。
  一律改成预案覆盖，口径同 `CLAUDE.md`「无人值守 prompt 禁止提问」。

## 回话时说什么

一批发出去，回话只要三样，不要复述 opener 全文：

1. 一张表：泳道 / 编号 / 做什么 / 触碰区 / **为什么能并行或必须串行**
2. 本批挖出的硬约束里最要紧的一两条，及它错了会怎样
3. 两条命令

跑完之后核验用 `run-lanes.sh` 末尾打印的那几条命令，**汇总表说 OK 不等于 main 上有东西**
（实证：单元 B 那次日志正常、main 上空的）。
