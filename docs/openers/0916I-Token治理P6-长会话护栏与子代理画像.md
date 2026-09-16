[Mac]0916I-Token治理P6-长会话护栏与子代理画像
【设置】执行环境: CC ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（改项目 hook 与账本脚本，要读本机 ~/.claude 日志并跑 claude CLI 实测）｜ 工作区: 仓库根 ｜ 派发: Cowork·Token治理-0916

> 本文件是 `[Mac]0916I` 的正文（引用式 opener）。**看护件：一次粘贴，内部串行四段**。
> Cowork 已写好（未提交，Linux 上测试已过）：
> - `scripts/hooks/context-guard.py` ＋ `tests/test_context_guard_hook.py`（5 条）：`UserPromptSubmit` hook，上下文过 150k／250k 各注入一次「换任务先换 session」提示，不阻断；`.claude/settings.json` 已挂
> - `scripts/token_ledger.py` 加「子代理按角色」画像（按委派消息关键词分 implementer／spec-review／quality-review／fix／final-review／explore／其他）
> - 路线图 Phase 6 状态行、号池台账 0916I 行
> 🔴 窄读纪律照旧。⛔ 不起泳道；⛔ 不改 `run-build`／`spec-to-plan` skill 与 superpowers 插件（本件只画像，改不改等数据）；⛔ 不改用户级 `~/.claude` 配置；⛔ 不碰 `data/`、`.env`、`_to_delete/`。

【一、前置自检】
1. `pgrep -f 'run-lanes.*\.sh'` 为空，否则停下报我
2. `git pull --rebase --autostash origin main`；`git status --short` 本件相关预期：M .claude/settings.json ｜ M scripts/token_ledger.py ｜ M docs/token治理/路线图.md ｜ M docs/openers/号池台账.md ｜ ?? scripts/hooks/context-guard.py ｜ ?? tests/test_context_guard_hook.py ｜ ?? 本文件
3. `./venv/bin/python -m pytest -q tests/test_context_guard_hook.py 2>&1 | tail -2` → 0 失败（不过 → 修 hook，⛔ 不改断言；3 轮不过 → 从 settings.json 摘掉 UserPromptSubmit 段，【二】跳过，报 PARTIAL）

【二、长会话护栏端到端】
1. 造一条假 transcript 验真实挂载（不用等真会话滚到 150k）：
   `python3 -c "import json;open('/tmp/cg.jsonl','w').write(json.dumps({'type':'assistant','message':{'usage':{'input_tokens':5,'cache_read_input_tokens':180000}}}))"`
   `echo '{"session_id":"e2e-0916i","transcript_path":"/tmp/cg.jsonl","prompt":"x"}' | python3 scripts/hooks/context-guard.py` → 输出含「180k」与「新开 session」
   `python3 -c "import json;print(json.load(open('.claude/settings.json'))['hooks']['UserPromptSubmit'])"` → 含 `context-guard.py`
2. 真 CLI 冒烟（确认 hook 挂上后不打断正常对话）：`echo '只回复 OK' | claude -p --output-format text --max-budget-usd 0.30 2>&1 | tail -2` → 正常回 OK
3. 本会话自证：本 session 若在收工前上下文已过 150k，应已看到过提示——在报告里如实写「见到／未到阈值」

【三、画像（只量不改）】
1. `python3 scripts/token_ledger.py --since 2026-09-01 --until 2026-09-15 --out /tmp/p6/ledger-0901-0915`
2. `sed -n '/子代理按角色/,$p' /tmp/p6/ledger-0901-0915.md`
3. **校正分类表**：看「其他」样例。若「其他」成本占子代理总成本 > 30%，读 2–3 个样例对应的真实委派消息开头（`grep -l '<样例前 20 字>' ~/.claude/projects/-Users-paulshao-Projects-HumanResource*/*/subagents/*.jsonl | head -2`，再对该文件 `head -c 1500`），把反复出现的措辞补进 `scripts/token_ledger.py` 的 `ROLE_PATTERNS`，重跑 1–2。最多补两轮。⛔ 报告里不贴委派消息原文超过一行。
4. 同时量 superpowers 模板本身：`ls ~/.claude/plugins/cache/claude-plugins-official/superpowers/*/skills/subagent-driven-development/` ＋ 各文件 `wc -c`
5. 新建 `docs/token治理/P6-子代理画像.md`：
   - 角色表（原样贴 2 的最终版）＋ 子代理成本 Top 15 父会话表
   - 三个判断，每个给数字依据：
     a. **修复循环是否是大头**：`fix` 个数 ÷ `implementer` 个数；quality-review／spec-review 个数 ÷ implementer 个数（理想≈1，明显 > 1.5 ⇒ 评审没过反复重做）
     b. **单个子代理是否在滚雪球**：各角色「峰值上下文中位数」与「平均调用/个」；implementer 峰值 > 120k ⇒ 任务书切得太大
     c. **模板重读**：`implementer-prompt.md`／`task-reviewer-prompt.md`／`global-constraints.md` 被 Read 的次数（账本「被 Read 最多的文件」）× 各自 KB
   - 结论只给一条建议动作＋预期省多少，⛔ 本件不实施

【四、未解释的 13k 项目层开销定位（只量不改）】
P5 实测：仓库根 F5≈52.8k、空目录 F0≈27.8k，拆出的四块（CLAUDE.md 10.3k、skills 1.1k、commands 0.3k、插件声明≈0）之和 11.7k，还差约 13.2k。
1. 候选一·项目 auto memory：`ls -la ~/.claude/projects/-Users-paulshao-Projects-HumanResource/memory/ 2>/dev/null; wc -c ~/.claude/projects/-Users-paulshao-Projects-HumanResource/memory/* 2>/dev/null | tail -1`
2. 候选二·git 状态注入：`git status --short | wc -c`；`git log --oneline -5 | wc -c`（`_to_delete/` 等未跟踪目录会进状态块）
3. 候选三·settings.local.json／其它：`wc -c .claude/settings.local.json`；`ls -la` 仓库根有无 `AGENTS.md`／其它会被自动加载的说明文件
4. 验证实验（各连跑 2 次取第 2 次，方法同 P5【二】）：
   - G1 空目录 ＋ `git init` ＋ 拷入 `CLAUDE.md`（对照 F1，看 git 仓库身份本身加多少）
   - G2 仓库根 ＋ 环境变量 `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`（若 `claude --help`／文档无此项，改为把 memory 目录临时重命名为 `memory.bak-0916i` 测完**立刻改回**，并 `ls` 证明已改回）
   - G3 仓库根的 `git worktree add /tmp/p6/wt HEAD` 干净检出里跑（无未跟踪文件、无 handoff），测完 `git worktree remove /tmp/p6/wt`
5. 追加到 `docs/token治理/P5-启动底座实测.md` 末尾「## 七、未解释开销定位（0916I）」：G1–G3 表 ＋ 13.2k 的归因拆分 ＋ 一句「能不能在仓库内治理、怎么治」。⛔ 本件不改 memory、不删 `_to_delete/`。

【五、提交】
1. `./venv/bin/python -m pytest -q 2>&1 | tail -3` → 0 失败（非本件引起只记报告）
2. git add .claude/settings.json scripts/hooks/context-guard.py tests/test_context_guard_hook.py scripts/token_ledger.py docs/token治理/P6-子代理画像.md docs/token治理/P5-启动底座实测.md docs/token治理/路线图.md docs/openers/号池台账.md "docs/openers/0916I-Token治理P6-长会话护栏与子代理画像.md"
3. git commit -m "feat(token治理): P6 长会话换任务护栏 hook ＋ 子代理角色画像 ＋ 项目层未解释开销定位（0916I）"
4. 按 CLAUDE.md 并发协议 push

【六、收工报告】逐项给，⛔ 不写「均已完成」：
1. commit hash；ahead 数
2. 【二】1 与 2 的输出原样（各 ≤ 3 行）＋【二】3 一句
3. 角色表原样 ＋ a/b/c 三个判断的数字 ＋ 那一条建议
4. G1–G3 表原样 ＋ 13.2k 归因一句
5. 下一步只给一条：「回 Cowork 说『出 Phase 7』」
