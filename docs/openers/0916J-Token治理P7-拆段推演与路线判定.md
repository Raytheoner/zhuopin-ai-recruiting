[Mac]0916J-Token治理P7-拆段推演与路线判定
【设置】执行环境: CC ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（只读本机日志、跑推演脚本、提交文档）｜ 工作区: 仓库根 ｜ 派发: Cowork·Token治理-0916

> 本文件是 `[Mac]0916J` 的正文（引用式 opener）。**看护件：一次粘贴，内部串行**。本件**只判不试**——不改 skill、不改执行器、不跑任何 Workflow。
> Cowork 已写好（未提交）：`scripts/split_projection.py`（按真实每次调用的上下文序列推演「拆 k 段」省量上限，构造数据验算与路线图 0909AJ 手算一致：拆 3 段省 34%）、路线图 Phase 7 状态行、号池台账 0916J 行。
> 背景数据：P6 画像显示子代理链路**不是**浪费源（fix/review ÷ implementer 均 < 0.35，峰值中位数 52–79k）；成本大头仍是**主会话一口气做多件事**（P0 Top 25 调用 69–144、峰值 185–371k）。
> 🔴 窄读纪律照旧；⛔ 不碰 `data/`、`.env`、`_to_delete/`。

【一、前置自检】
1. `git pull --rebase --autostash origin main`；`git status --short` 本件相关预期：?? scripts/split_projection.py ｜ ?? 本文件 ｜ M docs/token治理/路线图.md ｜ M docs/openers/号池台账.md
2. `python3 -m py_compile scripts/split_projection.py && echo OK`

【二、Workflow 可用性探针】
1. 本会话：你自己的工具列表里是否有名为 `Workflow` 的工具？如实写「有／没有」，⛔ 不要调用它
2. 无头：`echo '你的可用工具里是否有名字恰好是 Workflow 的工具？只回答「有」或「没有」' | claude -p --output-format text --max-budget-usd 0.30 2>&1 | tail -2`
3. `claude --version`

【三、拆段推演】
1. `python3 scripts/split_projection.py --since 2026-09-01 --until 2026-09-15 --top 10 --out docs/token治理/P7-拆段推演.md`
2. 逐行标两列（替换「可切分？」列为「可切分？｜可无头？」）。依据只看：标题、`grep -rn '^【' docs/openers/<号>-*.md docs/openers/归档/OP-0820-历史批次.md | grep '<号>'` 的节标题，⛔ 不读正文全文：
   - **可切分**＝由前后相对独立的几步组成（落档→建造→合回；发版→巡检；立包→计划）；**不可切分**＝一条靠前面线索往下查的调试/排障线
   - **可无头**＝不需要 Paul 在场发消息/拍板、不需要 Desktop 专属能力、不碰 .51 生产（按 CLAUDE.md 不可代项）；否则「需在场」
3. 表下追加三行汇总（只按可切分行重算）：
   - A 类「可切分＋可无头」：条数、合计成本、拆 3 段省量上限 $
   - B 类「可切分＋需在场」：同上
   - C 类「不可切分」：条数、合计成本（不计省量）
   - 并换算占 P0 基线总成本 $767 的百分比

【四、路线判定（写死判据，照判，⛔ 不问我）】
在推演文件末尾追加「## 判定」，按顺序套：
1. A 类省量上限 ≥ $15（≈ 基线 2%）⇒ **判 A 路**：长 opener 按节拆成同泳道串行多条，由 `run-lanes.sh` 逐条新开会话执行（现成机制，零新依赖）。写出：拆分门槛建议（例如「预计 > 60 次调用或 ≥ 3 个独立节」）、交接方式（前一条的产出文件＋commit hash，⛔ 不传对话）。
2. B 类省量上限 ≥ $15 **且**【二】1 为「有」⇒ **判 B 路**：对 B 类挑一件即将出现的同类任务做 Workflow 试点（3–5 段，每段显式 `model`）。【二】1 为「没有」⇒ B 类记「暂无工具，待 Workflow 可用再议」。
3. A、B 都 < $15 ⇒ **判 C 路**：Phase 7 不实施，理由写一句；长会话只靠 Phase 6 的 `context-guard` 提醒兜底。
A、B 可同时成立。每条判定后面写一句「依据：<数字>」。

【五、提交】
git add scripts/split_projection.py docs/token治理/P7-拆段推演.md docs/token治理/路线图.md docs/openers/号池台账.md "docs/openers/0916J-Token治理P7-拆段推演与路线判定.md"
git commit -m "docs(token治理): P7 拆段推演 ＋ Workflow 可用性探针 ＋ 路线判定（0916J）"
按 CLAUDE.md 并发协议 push。

【六、收工报告】逐项给，⛔ 不写「均已完成」：
1. commit hash
2. 【二】三项结果原样
3. 推演表原样（10 行）＋ A/B/C 三行汇总
4. 判定原文
5. 下一步只给一条：判 A 或 B ⇒「回 Cowork 说『出 Phase 7 实施』」；判 C ⇒「回 Cowork 说『出总验收』」
