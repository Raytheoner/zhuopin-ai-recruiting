[Mac]0916A-Token治理P0-账本基线与报告对账
【设置】执行环境: CC ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（只读本机日志＋在主检出提交文档与脚本）｜ 工作区: 仓库根 ｜ 派发: Cowork·Token治理-0916

> 本文件是 `[Mac]0916A` 的正文（引用式 opener）。路线图真源：`docs/token治理/路线图.md`（先读其「一」「三·Phase 0」两节，⛔ 不必读全文其它节）。
> 目标＝在 CC（能读 `~/.claude/projects`）里重算 09-01～09-15 的 token 账，建基线，逐条复核外部报告的数字。
> 🔴 本件**只读日志**，⛔ 不改 `run-lanes.sh`、skill、CLAUDE.md 或任何配置——那些是 Phase 1 起的事。
> 🔴 自律：本件自己也按 Phase 4 纪律执行——看文件用 `head`/`sed -n`/`grep`，⛔ 不 `cat` 大文件；⛔ 不读 `docs/session接力.md` 与 `OP-0820-全量编排.md` 全文（台账行已由 Cowork 登记好）。

【一、前置自检】
1. `git status --short`：预期能看到未提交的 `scripts/token_ledger.py`、`docs/token治理/路线图.md`、本文件、`docs/openers/OP-0820-全量编排.md`（台账加了 0916A 一行）。别人的改动照常存在，不要动。
2. `python3 -m py_compile scripts/token_ledger.py && echo OK`
3. `ls -d ~/.claude/projects/-Users-paulshao-Projects-HumanResource* | wc -l`：≥1，否则停下报我。

【二、跑基线】
python3 scripts/token_ledger.py --since 2026-09-01 --until 2026-09-15 --out docs/token治理/baseline-0901-0915
- 报错就修脚本（口径注释不许改：按 message.id 去重、synthetic 不计费、价格表）。修了在收工报告里写改了什么。
- 生成后 `head -60 docs/token治理/baseline-0901-0915.md` 看一眼是否合理（会话数应在百级、模型至少有 opus/sonnet）。

【三、对账】新建 `docs/token治理/P0-对账.md`，一张表，逐行对应路线图「一」核验表中依赖日志数字的条目，外加报告正文里的这些数字：
- 9 月会话数 165；总 cache read 26.8 亿；总 output 1818 万；Bash 6520 次 / Agent 343 次
- Opus 129 会话 12.56 亿；Sonnet 28 会话 8.94 亿；`<synthetic>` 14 会话 5.34 亿
- 首轮上下文 74k；≥10KB 大回显 920 次；`cat` 全文 395 次
- 峰值会话：`e455a5b6`（sonnet 967 轮 395M）、`0904Z`、`0909AJ`、`0908B`、`8b375086`、`f0be567b`
- run-lanes 无头泳道实际跑的模型（报告说 Opus）
列：报告值 ｜ 实测值 ｜ 成立？（✅/❌/⚠️口径不同）｜ 一句话说明。报告的数字是 09 全月还是到哪天，拿不准就写「口径不明」，⛔ 不硬凑。
表下追加一张小表：成本 Top 10 会话逐个标「可切分／不可切分」（可切分＝由几个前后独立的步骤组成，如落档→建造→合回、发版→巡检；不可切分＝一条要靠前面积累线索的调试线），判断依据只看标题与对应 `docs/openers/<号>-*.md` 的节标题（`grep -n '^【' <文件>`），⛔ 不读正文全文。这是路线图候选 Phase 7 的输入。
表下再写三行结论：① 成本 Top 3 来源（按会话类型）② 首轮上下文主会话 vs 子代理中位数 ③ 大回显 Top 3 来源。

【四、按数据校排序】对照路线图「二」的 Phase 排序：若实测显示某 Phase 的打击对象占比 < 5%（例如看护会话成本占比很小），在路线图「二」表下追加一行「P0 校正：…」写清改动和数据依据；否则追加「P0 校正：排序不变」。⛔ 不改路线图其它内容。

【五、提交】
git add scripts/token_ledger.py docs/token治理/路线图.md docs/token治理/P0-对账.md docs/token治理/baseline-0901-0915.md docs/token治理/baseline-0901-0915.json "docs/openers/0916A-Token治理P0-账本基线与报告对账.md" docs/openers/OP-0820-全量编排.md
git commit -m "docs(token治理): P0 账本基线与外部报告对账（0916A）"
按 CLAUDE.md 并发协议 push（⛔ 不 add -A；index.lock 等 5 秒重试；push 被拒才 pull --rebase --autostash）。
⚠️ `OP-0820-全量编排.md` 若有别人未提交的改动混在里面，只提交台账 0916A 那一行（`git add -p` 不可交互 ⇒ 改用：先 `git diff docs/openers/OP-0820-全量编排.md | head -40` 看，若只有这一行就整件 add；有别的改动就不 add 该文件，在报告里写明「台账行未提交」）。

【六、收工报告】逐项给，⛔ 不写「均已完成」：
1. commit hash（`git log --oneline -3` 反查）
2. 基线总成本（API 等价 $）与按模型、按会话类型两张小表
3. 对账表里 ❌ 的行（原样列出）
4. 路线图排序是否校正、依据
5. 下一步：只给一条——「请回 Cowork 说『出 Phase 1 看护件』」，⛔ 不列选项
