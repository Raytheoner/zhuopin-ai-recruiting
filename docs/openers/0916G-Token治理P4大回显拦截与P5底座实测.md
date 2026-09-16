[Mac]0916G-Token治理P4大回显拦截与P5底座实测
【设置】执行环境: CC ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（改主检出上的共享文档与项目 hook，且要跑本机 claude CLI 实测）｜ 工作区: 仓库根 ｜ 派发: Cowork·Token治理-0916

> 本文件是 `[Mac]0916G` 的正文（引用式 opener）。**看护件：一次粘贴，内部串行四段**，前一段不过不进下一段。
> Cowork 已写好（未提交）：`scripts/hooks/limit-output.py`（PreToolUse(Bash)，拦 > 20 KB 的 cat／sed -n 范围／head／tail 整读）、`tests/test_limit_output_hook.py`（23 条，Linux 已过）、
> `.claude/settings.json` 挂上该 hook、路线图 Phase 4／5 状态行、号池台账 0916G 行。
> 🔴 本件自己遵守将要上线的纪律：看文件先 `grep -n` 定位再 `sed -n` 窄读；⛔ 不 cat 大文件。
> ⛔ 不起泳道批次；⛔ 不改 `run-lanes.sh`；⛔ 不碰 `data/`、`.env`、`_to_delete/`；本件**不往 `docs/session接力.md` 追加任何新内容**（它离体积闸只剩约 0.4 KB）。

【一、前置自检】
1. `pgrep -f 'run-lanes.*\.sh'` 必须为空，否则停下报我
2. `git pull --rebase --autostash origin main`
3. `git status --short`，本件相关预期：?? scripts/hooks/limit-output.py ｜ ?? tests/test_limit_output_hook.py ｜ ?? 本文件 ｜ M .claude/settings.json ｜ M docs/token治理/路线图.md ｜ M docs/openers/号池台账.md ｜ M docs/openers/OP-0820-全量编排.md（这一处是 `[Mac]0916F` 漏提交的 mark_done：`git diff docs/openers/OP-0820-全量编排.md | grep -E '^[+-][^+-]'` 应恰好两行——`-> 泳道：liaison测试脚手架收尾` 与 `+> ✅ 已完成 2026-09-16（OK）…`；不是这两行 → 该文件不 add、报告里写明）
   `git status --short -- docs/session接力.md` 必须为空（有别人未提交改动 → 停下报我）

【二、补 0916F 的收口（机械）】
1. `python3 scripts/token_ledger.py --since 2026-09-16 --out /tmp/ledger-0916g` ；`grep -F '0916E' /tmp/ledger-0916g.md` 取该会话成本 $（找不到就写「账本未命中」）
2. 在 `docs/token治理/P2-AB基线.md`「切换后记录」表追加一行：
   `| lanes-20260916-113021 | 1 | 1/0 | 0 | 0 | $<上一步> | 样本 1/8，继续攒 |`
3. 顺带记下 /tmp/ledger-0916g.md 里今天各会话的「首轮」列，【四】要用

【三、Phase 4：上线大回显 hook】
1. `./venv/bin/python -m pytest -q tests/test_limit_output_hook.py 2>&1 | tail -3` → 0 失败（macOS 复测；不过 → 修 hook，⛔ 不改断言；3 轮不过 → 从 settings.json 摘掉 PreToolUse 段，本段报 PARTIAL，其余段继续）
2. 端到端（真 CLI 过 hook，验证 hook 真的挂上且拦得住）：
   `echo '用 Bash 工具执行这一条命令且只执行这一条：cat docs/openers/归档/OP-0820-历史批次.md 。然后只回复你看到的第一行文字。' | claude -p --output-format text --max-budget-usd 0.50 2>&1 | tail -5`
   预期：回复里提到「已拦截」或改用了 grep/sed 窄读，⛔ 不应返回该文件正文大段。若直接读出了全文 → hook 没生效：查 `.claude/settings.json` 的 hooks.PreToolUse 与 `$CLAUDE_PROJECT_DIR`，修好再验；修不好同 1 处置
3. 放行抽查：`echo '用 Bash 工具执行：git status --short | head -3 ，只回复输出行数。' | claude -p --output-format text --max-budget-usd 0.30 2>&1 | tail -3` → 正常给出行数

【四、Phase 5：启动底座实测（只量不改）】
1. `claude --help 2>&1 | grep -E 'strict-mcp-config|disable-slash-commands|mcp-config'` 记下哪些开关存在；不存在的实验跳过并注明
2. 写 `/tmp/p5/measure.sh`，对下面每个实验各跑一次，**每次都是全新会话、只问一句**，从 JSON 取首轮上下文＝`input_tokens + cache_creation_input_tokens + cache_read_input_tokens`：
   `echo '只回复 OK' | claude -p --output-format json --max-budget-usd 0.30 <额外参数> | python3 -c "import sys,json;u=json.load(sys.stdin)['usage'];print(u['input_tokens']+u.get('cache_creation_input_tokens',0)+u.get('cache_read_input_tokens',0))"`
   - E0 仓库根，无额外参数（基准）
   - E1 仓库根，`--strict-mcp-config`（不加载任何 MCP）
   - E2 仓库根，`--disable-slash-commands`（不加载技能清单）
   - E3 仓库根，`--strict-mcp-config --disable-slash-commands`
   - E4 在 `mktemp -d` 的空目录里跑，无额外参数（去掉项目 CLAUDE.md、项目 settings 与项目技能；用户级的仍在）
   ⚠️ JSON 若只有一轮汇总不是首轮也照用（「只回复 OK」只有一轮）；取不到 `usage` 键就把 JSON 顶层键名写进报告，⛔ 不猜
3. 新建 `docs/token治理/P5-启动底座实测.md`：一张表 E0–E4 首轮 token；推算行——MCP ≈ E0−E1、技能清单 ≈ E0−E2、项目层（CLAUDE.md＋项目技能＋插件） ≈ E0−E4；
   再一行对照 Desktop：今天 Desktop 手工会话（0916A–D、0910A、本件）首轮中位数 vs 无头泳道 0916E 首轮（取自【二】3），「Desktop 独有 ≈ Desktop 首轮 − E0」。
   最后三行结论：占比最大的三块、各自是否「无头泳道用不到」（依据：`grep -l 'mcp__' .claude/handoff/lanes-2026091*/*.log` 有无命中）、以及一句「建议 Phase 5 先动哪一块」。⛔ 本件不改任何配置去验证建议。

【五、接力文件归档（让体积闸重新有余量）】
1. 分页读 `docs/session接力.md`（Read，每次 ≤ 300 行），只找**今天已闭环**的段：`0910A` 的 AT-1b／TD-42／TD-44、`0916E` TD-29、以及原本因「等 0910A」而悬着、现已无未完成待办的段（`0910A 派号未落档`、第十六批裁决里 0910B→0910A 排序那类）。标题加「【已闭环】」，判据同 `0916D`【四】1（⛔ 含未完成待办的不标，拿不准不标）
2. `python3 scripts/archive_docs.py --verify-tags` → ✓ ；`python3 scripts/archive_docs.py --scope relay --apply` → 守恒通过；`wc -c docs/session接力.md`
3. 若 ≤ 40 KB：把 `tests/test_doc_size_budget.py` 接力文件上限改回 40，路线图 Phase 3 那行「暂设 71 KB」后追加「→ 09-16 `0916G` 归档后恢复 40 KB」
   若 > 40 KB：上限改成「当前 KB 向上取整 + 5」（必须 ≤ 71），路线图同处追加新值与剩余大段；⛔ 不删内容凑数
4. `./venv/bin/python -m pytest -q tests/test_doc_size_budget.py tests/test_archive_docs.py 2>&1 | tail -3` → 0 失败

【六、测试与提交】
1. `./venv/bin/python -m pytest -q 2>&1 | tail -3` → 0 失败（非本件引起只记报告）
2. git add scripts/hooks/limit-output.py tests/test_limit_output_hook.py .claude/settings.json docs/token治理/路线图.md docs/token治理/P2-AB基线.md docs/token治理/P5-启动底座实测.md docs/openers/号池台账.md docs/openers/OP-0820-全量编排.md docs/session接力.md docs/archive tests/test_doc_size_budget.py "docs/openers/0916G-Token治理P4大回显拦截与P5底座实测.md"
   （【一】3 判定 OP-0820 不该 add 的，从列表里去掉；【三】摘了 hook 的，照样 add settings.json 的最终状态）
3. git commit -m "feat(token治理): P4 大回显拦截 hook ＋ P5 启动底座实测 ＋ 0916F 收口补记 ＋ 接力归档（0916G）"
4. 按 CLAUDE.md 并发协议 push；rebase 冲突落在接力文件上 → `git rebase --abort && git reset --soft HEAD~1`，从【五】重做（工具幂等），⛔ 不手工解冲突

【七、收工报告】逐项给，⛔ 不写「均已完成」：
1. commit hash；ahead 数
2. 【三】1 末行、【三】2 与 3 的回复原文（各 ≤ 5 行）
3. 【四】E0–E4 数字表原样 ＋ Desktop 对照行 ＋ 三行结论
4. 接力文件 前 → 后 KB、标了哪几段、闸上限最终值
5. A/B 记账行原样
6. 下一步只给一条：「回 Cowork 说『出 Phase 5』」
