[Mac]0916H-Token治理P5-启动底座瘦身
【设置】执行环境: CC ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（改 CLAUDE.md 与执行器，要在主检出一次改完一次提交，且要跑本机 claude CLI 实测）｜ 工作区: 仓库根 ｜ 派发: Cowork·Token治理-0916

> 本文件是 `[Mac]0916H` 的正文（引用式 opener）。**看护件：一次粘贴，内部串行五段**。
> 输入：`docs/token治理/P5-启动底座实测.md`（0916G）。结论摘要：CLI 首轮 E0＝52.8k；空目录 E4＝28.0k ⇒ **项目层≈24.9k 最大**；MCP≈1.5k；Desktop 独有≈30k（仓库里改不到，本件不碰）。
> Cowork 已写好（未提交）：
> - `docs/openers/run-lanes.sh`：无头泳道默认加 `--strict-mcp-config`，opener【设置】写「MCP: on」可关；`tests/test_run_lanes_model.py` 加断言（Linux 5 passed）
> - `scripts/check_claude_md_slim.py`（CLAUDE.md 瘦身守恒校验）＋ 空壳 `docs/CLAUDE-md-由来.md` ＋ `tests/test_doc_size_budget.py` 加 `CLAUDE.md: 24`
> - 路线图 Phase 5 状态行、号池台账 0916H 行
> 🔴 窄读纪律照旧（hook 已上线）。⛔ 不起泳道；⛔ 不碰 `data/`、`.env`、`_to_delete/`；⛔ 不改用户级 `~/.claude` 任何配置；⛔ 不禁用 superpowers 插件（run-build／spec-to-plan 依赖它）。

【一、前置自检】
1. `pgrep -f 'run-lanes.*\.sh'` 必须为空，否则停下报我
2. `git pull --rebase --autostash origin main`；`git status --short` 本件相关预期：M docs/openers/run-lanes.sh ｜ M tests/test_run_lanes_model.py ｜ M tests/test_doc_size_budget.py ｜ M docs/token治理/路线图.md ｜ M docs/openers/号池台账.md ｜ ?? scripts/check_claude_md_slim.py ｜ ?? docs/CLAUDE-md-由来.md ｜ ?? 本文件
3. `git status --short -- CLAUDE.md` 必须为空

【二、项目层 24.9k 再分解（只量）】
写 `/tmp/p5b/measure.sh`：每个实验在**独立临时目录**里跑，**连跑 2 次取第 2 次**；首轮＝`input_tokens + cache_creation_input_tokens + cache_read_input_tokens`（三项之和与缓存是否命中无关——0916G 报告里把 E2 的负差归因于缓存是**不对的**，和是总输入量，本段顺带重测 E2 定案）：
`echo '只回复 OK' | claude -p --output-format json --max-budget-usd 0.30 <参数> | python3 -c "import sys,json;u=json.load(sys.stdin)['usage'];print(u['input_tokens']+u.get('cache_creation_input_tokens',0)+u.get('cache_read_input_tokens',0))"`
- F0 空目录（基准，应≈E4）
- F1 空目录 ＋ 拷入 `CLAUDE.md`
- F2 空目录 ＋ 拷入 `.claude/skills/`（整目录）
- F3 空目录 ＋ 拷入 `.claude/commands/`（整目录）
- F4 空目录 ＋ 写 `.claude/settings.json` 只含 `{"enabledPlugins":{"superpowers@claude-plugins-official":true}}`
- F5 仓库根（应≈E0），F6 仓库根 `--disable-slash-commands`（重测 E2）
在 `docs/token治理/P5-启动底座实测.md` 末尾追加「## 六、项目层分解（0916H）」：F0–F6 表 ＋ 各块＝Fi−F0 ＋ 「各块之和 vs F5−F0」对照一行 ＋ 一句 E2 定案（`--disable-slash-commands` 是真的多加了内容，还是两次测量间项目状态变了）。

【三、无头泳道去 MCP（已写好，验证后保留）】
1. `./venv/bin/python -m pytest -q tests/test_run_lanes_model.py tests/test_wait_lanes.py 2>&1 | tail -3` → 0 失败（不过 → 修脚本，⛔ 不改断言；3 轮不过 → `git checkout -- docs/openers/run-lanes.sh tests/test_run_lanes_model.py` 撤回本段，报 PARTIAL，其余段继续）
2. 插件技能不受影响的实证：`echo '列出你可用的技能名里所有含 superpowers 的，只列名字' | claude -p --strict-mcp-config --output-format text --max-budget-usd 0.30 2>&1 | tail -8` → 必须出现 `superpowers:` 开头的技能名（如 `subagent-driven-development`）。**没有** → `--strict-mcp-config` 连插件一起关了，撤回本段（同 1），报告写明
3. `bash docs/openers/run-lanes.sh --dry-run 2>&1 | tail -4` → 不因本改动报错（队列空 exit 12 属正常）

【四、CLAUDE.md 瘦身（守恒搬运）】
1. `grep -n '^## ' CLAUDE.md` 列章节；逐节 `sed -n '起,止p' CLAUDE.md` 窄读。**只搬这几类整行/整段**，原样剪到 `docs/CLAUDE-md-由来.md` 对应的 `## <同名章节>` 下：
   - 以 `*为什么*`、`*实证*`、`实证：`、`*由来*`、`反例`、`**Why**` 开头的行或段
   - 纯事故叙述的引用块（`> ` 开头、讲某日某次出了什么事的）
   - 某条规则后面跟着的「例如 08-27 撞号 4 次」这类**独立成行**的案例说明
   ⛔ 同一行里既有规则又有由来的，**整行不动**（校验会拦：含 ⛔🔴必须不许禁止一律不得硬规则 的行不许移走）。⛔ 不改写、不缩句、不合并。
2. 每节搬完，在该节规则末尾加**一行**指针：`（由来与实证见 docs/CLAUDE-md-由来.md「<章节名>」）`——这是 CLAUDE.md 唯一允许新增的正文
3. `python3 scripts/check_claude_md_slim.py` → 必须 ✓；✗ 按提示改回，⛔ 不改校验脚本
4. `wc -c CLAUDE.md`；把 `tests/test_doc_size_budget.py` 里 `"CLAUDE.md": 24` 改成「当前 KB 向上取整 + 2」（且 ≤ 24），注释改成 `# 0916H 瘦身后基线`
5. 目标参考 ≤ 18 KB；达不到不硬凑，报告写实际值与「剩余里最大的三节」

【五、效果复测与收口】
1. 重跑【二】的 F5（仓库根，连跑 2 次取第 2 次），与 F5 首测相减 → CLAUDE.md 瘦身实际省下的首轮 token；追加到实测文件「六」末尾一行
2. `./venv/bin/python -m pytest -q 2>&1 | tail -3` → 0 失败（非本件引起只记报告）
3. git add docs/openers/run-lanes.sh tests/test_run_lanes_model.py tests/test_doc_size_budget.py scripts/check_claude_md_slim.py CLAUDE.md docs/CLAUDE-md-由来.md docs/token治理/P5-启动底座实测.md docs/token治理/路线图.md docs/openers/号池台账.md "docs/openers/0916H-Token治理P5-启动底座瘦身.md"
4. git commit -m "feat(token治理): P5 启动底座瘦身——无头泳道去 MCP ＋ CLAUDE.md 由来外移（守恒校验）＋ 项目层分解实测（0916H）"
5. 按 CLAUDE.md 并发协议 push

【六、收工报告】逐项给，⛔ 不写「均已完成」：
1. commit hash；ahead 数
2. F0–F6 表原样 ＋ 各块推算 ＋ E2 定案一句
3. 【三】2 的输出原样（≤ 8 行）；【三】是否保留
4. CLAUDE.md 前 → 后 KB、搬走行数、校验输出原样、F5 前后差值
5. 下一步只给一条：「回 Cowork 说『出 Phase 6』」
