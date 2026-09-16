[Mac]0916C-Token治理P2-模型分级与AB基线
【设置】执行环境: CC ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（改动已由 Cowork 写在主检出，本件做 macOS 实测、自验与提交）｜ 工作区: 仓库根 ｜ 派发: Cowork·Token治理-0916

> 本文件是 `[Mac]0916C` 的正文（引用式 opener）。一次粘贴、内部串行。
> 🔴 本件**不要手动切模型**——它自己就是「项目默认 Sonnet 是否对 Desktop 生效」的试验品，【三】会用账本查本会话实际跑的模型。
> Cowork 已写好（未提交）：
> - `docs/openers/run-lanes.sh`：`DEFAULT_MODEL=sonnet`、`SUBAGENT_MODEL=sonnet`；解析 opener【设置】行「模型: X」；dry-run 每条打印模型与来源；不认识的模型名预检 exit 13；实跑传 `--model` 并以 `CLAUDE_CODE_SUBAGENT_MODEL` 传子代理模型；`results.tsv` 第 6 列记模型；`--chain` 续跑透传子代理模型
> - `tests/test_run_lanes_model.py`（4 条，沙箱里跑拷贝脚本＋假 claude，⛔ 不碰真仓库 handoff）
> - `.claude/settings.json` 顶部加 `"model": "sonnet"`
> - `CLAUDE.md`「工具链分工」加「模型分级」一段；`lane-dispatch` 与 `kickoff` 两个 skill 各加模型字段说明；`lane-dispatch` 看护规格加「报告必附 A/B 行」
> - `docs/token治理/P2-AB基线.md`（基线失败类 11%／71 条＋判据）、路线图 Phase 2 状态行
> Linux 上 4 passed 且手跑 dry-run／实跑输出核对过；**本件在 macOS 上复测并提交**。
> ⛔ 不起任何真实泳道批次。看文件用 `head`/`sed -n`/`grep`，⛔ 不 cat 大文件。

【一、前置自检】
1. `pgrep -f 'run-lanes.*\.sh'` 必须为空——有批次在跑 → 停下报我（脚本有自拷贝保护，但本件要在无批次时提交执行器改动，这是 lane-dispatch「改 run-lanes.sh 不进泳道、发车前跑完」的同一口径）
2. `git status --short`，本件相关预期：
   M docs/openers/run-lanes.sh ｜ M .claude/settings.json ｜ M CLAUDE.md ｜ M .claude/skills/lane-dispatch/SKILL.md ｜ M .claude/skills/kickoff/SKILL.md ｜ M docs/token治理/路线图.md ｜ M docs/openers/OP-0820-全量编排.md
   ?? tests/test_run_lanes_model.py ｜ ?? docs/token治理/P2-AB基线.md ｜ ?? 本文件
   别人的改动照常存在，⛔ 不动不提交。本件相关任一缺失 → 停下报我。
3. `git diff --stat docs/openers/run-lanes.sh` 应约 60–70 行增删；`git diff docs/openers/run-lanes.sh | grep '^-' | grep -v '^---'` 逐行看被删的只有：`--model` 注释行、两处 `printf` results 行、`[[ -n "$MODEL" ]] && args+=` 行、`| claude "${args[@]}"` 行、`local id title …` 行、chain 的 `${MODEL:+…}` 行、`单条预算上限` 打印行、`echo "  • [$lane/$id]` 行。出现其它被删行 → 停下报我。

【二、macOS 复测】
1. `bash -n docs/openers/run-lanes.sh && echo OK`
2. `./venv/bin/python -m pytest -q tests/test_run_lanes_model.py tests/test_wait_lanes.py tests/test_lane_launcher.py 2>&1 | tail -5` → 0 失败
   不过 → 修 `run-lanes.sh`（多半是 macOS bash 3.2／BSD grep 的差异，如 `grep -oE` 的多字节、`<<<` here-string），⛔ 不改测试断言迁就；最多 3 轮，仍不过 → 收工报 PARTIAL，⛔ 不提交执行器改动（其余文档照提）
3. 真编排文件 dry-run：`bash docs/openers/run-lanes.sh --dry-run 2>&1 | head -30`
   预期：若队列为空 → `Σ=N=M=0` 且 exit 12（正常）；若有条目 → 每条下面有「模型 sonnet（默认）」或「模型 opus（opener设置行）」。⛔ 不实跑。
4. 全量：`./venv/bin/python -m pytest -q 2>&1 | tail -3` → 0 失败（有失败先判是否本件引起；不是则只记报告）

【三、项目默认模型自验】
1. CLI：`echo '只回复两个字：收到' | claude -p --output-format json --max-budget-usd 0.30 2>/dev/null | grep -oE '"claude-[a-z0-9.-]+"' | sort -u`
   预期只出现 sonnet 系列。出现 opus → 说明被更高优先级覆盖（查 `echo $ANTHROPIC_MODEL`、`~/.claude/settings.json` 是否写死 model，只报告，⛔ 不改用户级配置）
2. Desktop（本会话自己）：`python3 scripts/token_ledger.py --since 2026-09-16 --out /tmp/ledger-0916c && grep -F '0916C' /tmp/ledger-0916c.md | head -3`
   看「模型(调用)」列。标题若还没进账本（会话标题写入有延迟），改查：`grep -F 'model' <(ls -t ~/.claude/projects/-Users-paulshao-Projects-HumanResource/*.jsonl | head -1 | xargs tail -c 20000) | grep -oE '"model":"[^"]+"' | sort | uniq -c`
   结论写成一句：「Desktop 新会话默认模型 ＝ sonnet ✅」或「＝ opus ❌（项目 settings 对 Desktop 不生效，需在选择器手动切）」。❌ 时在 `docs/token治理/P2-AB基线.md` 顶部加一行 ⚠️ 记下，并在 CLAUDE.md「模型分级」段末尾补一句「Desktop 不读项目 model 设置，手工会话开工前在选择器切 Sonnet」。

【四、提交】
1. `git diff docs/openers/OP-0820-全量编排.md | grep '^+|'` → 只应是 `[Mac]0916C` 一行；混了别人的改动 → 该文件不 add，报告里写明
2. git add docs/openers/run-lanes.sh tests/test_run_lanes_model.py .claude/settings.json CLAUDE.md .claude/skills/lane-dispatch/SKILL.md .claude/skills/kickoff/SKILL.md docs/token治理/路线图.md docs/token治理/P2-AB基线.md "docs/openers/0916C-Token治理P2-模型分级与AB基线.md" docs/openers/OP-0820-全量编排.md
3. git commit -m "feat(token治理): P2 模型分级——泳道/子代理/项目默认 Sonnet，opener 可声明 Opus，results 记模型，A/B 基线（0916C）"
4. 按 CLAUDE.md 并发协议 push（⛔ 不 add -A；index.lock 等 5 秒重试最多 5 次；push 被拒才 pull --rebase --autostash，最多 3 次）

【五、收工报告】逐项给，⛔ 不写「均已完成」：
1. commit hash 与 `git status -sb` ahead 数
2. 【二】2、3、4 的末几行原样；修过脚本的话改了什么
3. 【三】1 的输出原样 ＋【三】2 的一句结论
4. 下一步只给一条：「下一批泳道照常发（说『开始泳道看护』），看护报告会自动带 A/B 行；攒够 8 条后回 Cowork 说『判 Phase 2』」
