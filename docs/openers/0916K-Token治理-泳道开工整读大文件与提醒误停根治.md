[Mac]0916K-Token治理-泳道开工整读大文件与提醒误停根治
【设置】执行环境: CC ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（改项目 hook、执行器与共享文档，要在主检出一次改完一次提交，且要跑本机 claude CLI 实测）｜ 工作区: 仓库根 ｜ 派发: Cowork·Token治理-0916

> 本文件是 `[Mac]0916K` 的正文（引用式 opener）。**看护件：一次粘贴，内部串行**。
> 起因：Win 端实录——两条泳道开工 3／7 分钟碰到 150k 提醒按规矩收尾（#584 一行代码没改）；#584 的 78 次请求上下文 29k→170k，主因是**开工就整份读大文件**（opener 骨架、批处理脚本读两次、测试文件）。
> Cowork 核对本仓库：①「提醒叫停泳道」**尚未发生**（`context-guard` 挂在 UserPromptSubmit，无头 `-p` 只有首条消息、此时无 transcript），但措辞「建议新开 session」存在同样误读风险；②「整读大文件」**同样存在且没被拦**——Phase 4 的 hook 只管 Bash，Read 工具不受限；本仓库被跟踪的 >40 KB 文件有 15+ 份（`docs/superpowers/plans/*` 90–165 KB、`openspec/.../tasks.md` 100 KB、`docs/tech-debt.md` 162 KB、`kickoff` SKILL 36 KB、`run-lanes.sh` 40 KB）；③改 `.claude/settings.json` 被守卫拦——本仓库一直是 Cowork 写好、CC 只提交，本件沿用。
> Cowork 已写好（未提交，Linux 50 passed）：
> - `scripts/hooks/limit-output.py`：新增 Read 闸——不带 offset 且（无 limit 或 limit > 400 行）整读 > 40 KB 文件即拦，提示先 grep 定位再分段读；`.claude/settings.json` PreToolUse matcher 改 `Bash|Read`
> - `scripts/hooks/context-guard.py`：环境变量 `HR_HEADLESS_LANE` 存在时一律不提醒；提示语加「⛔ 不许因为上下文大而提前收尾或缩小任务范围」
> - `docs/openers/run-lanes.sh`：起泳道时带 `HR_HEADLESS_LANE=1`
> - `scripts/archive_docs.py`：新增 `--scope techdebt`，把 `docs/tech-debt.md` 里 `## ~~TD-N~~`（已还）整段原样搬到 `docs/archive/tech-debt-已还.md`（拷贝实测 162 KB → 45 KB，守恒通过）；`tests/test_doc_size_budget.py` 加 tech-debt 闸
> - 对应测试：`tests/test_limit_output_hook.py`、`tests/test_context_guard_hook.py`、`tests/test_run_lanes_model.py`、`tests/test_archive_docs.py`
> 🔴 窄读纪律照旧；⛔ 不起泳道；⛔ 不碰 `data/`、`.env`、`_to_delete/`；⛔ 不改用户级 `~/.claude`。

【一、前置自检——必须串行在 0916J 之后】
1. `git log --oneline -30 | grep -q '0916J' && echo J-DONE || echo J-MISSING` → **J-MISSING 即停下报我**「先跑完 `[Mac]0916J` 再贴本件」（两件都改路线图与号池台账，并跑会互相踩）
2. `pgrep -f 'run-lanes.*\.sh'` 为空，否则停下报我
3. `git pull --rebase --autostash origin main`；`git status --short` 本件相关预期：M .claude/settings.json ｜ M scripts/hooks/limit-output.py ｜ M scripts/hooks/context-guard.py ｜ M docs/openers/run-lanes.sh ｜ M scripts/archive_docs.py ｜ M tests/test_limit_output_hook.py ｜ M tests/test_context_guard_hook.py ｜ M tests/test_run_lanes_model.py ｜ M tests/test_archive_docs.py ｜ M tests/test_doc_size_budget.py ｜ M docs/openers/号池台账.md ｜ ?? 本文件
   `git status --short -- docs/tech-debt.md` 必须为空（有别人未提交改动 → 停下报我）

【二、macOS 复测】
1. `bash -n docs/openers/run-lanes.sh && echo OK`
2. `./venv/bin/python -m pytest -q tests/test_limit_output_hook.py tests/test_context_guard_hook.py tests/test_run_lanes_model.py tests/test_archive_docs.py 2>&1 | tail -3` → 0 失败（不过 → 修实现，⛔ 不改断言；3 轮不过 → 报 PARTIAL 停下，⛔ 不提交半截）

【三、端到端：Read 闸不误伤 Edit】
（这是本件最大的风险：Claude Code 要求 Edit 前「读过」文件——分段 Read 必须仍能满足它，否则泳道改大文件会卡死）
1. `cp openspec/changes/m1-job-profile-intake/tasks.md /tmp/k-tasks.md`（100 KB 拷贝，⛔ 不动原件）
2. `echo '对文件 /tmp/k-tasks.md：先用 Read 工具不带任何参数读它；若被拦，就按提示先 grep 找到含「- [x]」的第一行行号，再用 Read 带 offset 与 limit 读那附近 20 行，然后用 Edit 工具把那一行末尾追加文字「 K测试」。最后只回复「EDIT-OK」或「EDIT-FAIL: 原因」。' | claude -p --output-format text --max-budget-usd 1.00 --dangerously-skip-permissions 2>&1 | tail -4`
3. `grep -c 'K测试' /tmp/k-tasks.md` → 1。
   ＝ 0 或回复 EDIT-FAIL 且原因与「未读过文件」有关 ⇒ **Read 闸与 Edit 冲突**：把 `READ_LIMIT` 调到 `10_000_000`（等于关闭 Read 闸，Bash 闸保留），在报告写明「Read 闸撤回：<原因原文>」，继续后面各段
4. 放行抽查：`echo '用 Read 工具读 CLAUDE.md 前 30 行，只回复第一行' | claude -p --output-format text --max-budget-usd 0.30 2>&1 | tail -2` → 正常回出标题行

【四、端到端：无头不被提醒叫停】
1. `echo '{"session_id":"k1","transcript_path":"/tmp/cg.jsonl","prompt":"x"}' > /tmp/k-in.json; python3 -c "import json;open('/tmp/cg.jsonl','w').write(json.dumps({'type':'assistant','message':{'usage':{'input_tokens':5,'cache_read_input_tokens':200000}}}))"`
2. `HR_HEADLESS_LANE=1 python3 scripts/hooks/context-guard.py < /tmp/k-in.json | wc -c` → 0
3. `CC_CONTEXT_GUARD_DIR=$(mktemp -d) python3 scripts/hooks/context-guard.py < /tmp/k-in.json | grep -c '提前收尾'` → 1

【五、tech-debt 归档】
1. `python3 scripts/archive_docs.py --scope techdebt` 看计划 → `--scope techdebt --apply`
2. `grep -c '^## ~~' docs/tech-debt.md` → 0；`grep -rn 'TD-[0-9]*' tests/*.py | grep -oE 'TD-[0-9]+' | sort -u` 列出的号，逐个 `grep -rl "## .*<号>" docs/tech-debt.md docs/archive/tech-debt-已还.md` 仍能找到（引用不断链）
3. `wc -c docs/tech-debt.md`；把 `tests/test_doc_size_budget.py` 里 `"docs/tech-debt.md": 170` 改成「当前 KB 向上取整 + 5」，注释改 `# 0916K 归档后基线`
4. `./venv/bin/python -m pytest -q tests/test_doc_size_budget.py 2>&1 | tail -2` → 0 失败

【六、规则落真源（防下次再有人整读）】
在 `CLAUDE.md`「工具链分工」节「模型分级」段后追加**一行**（⛔ 不加别的）：
`**读大文件（2026-09-16 \`0916K\`）**：> 40 KB 的文件先 grep 定位、再分段 Read（offset＋limit ≤ 400 行）；hook 会拦整读。长会话提醒只针对「换任务」，⛔ 同一任务不因上下文大而提前收尾。`
然后 `python3 scripts/check_claude_md_slim.py --base HEAD` 会因「新增非指针行」报 ③——**这是预期的**，本件是规则新增不是瘦身，报告里注明即可；`./venv/bin/python -m pytest -q tests/test_doc_size_budget.py` 必须仍过（CLAUDE.md 闸）。超闸 → 把该闸上限 +1 KB 并在报告写明

【七、提交】
1. `./venv/bin/python -m pytest -q 2>&1 | tail -3` → 0 失败（非本件引起只记报告）
2. git add .claude/settings.json scripts/hooks/limit-output.py scripts/hooks/context-guard.py docs/openers/run-lanes.sh scripts/archive_docs.py tests/test_limit_output_hook.py tests/test_context_guard_hook.py tests/test_run_lanes_model.py tests/test_archive_docs.py tests/test_doc_size_budget.py docs/tech-debt.md docs/archive/tech-debt-已还.md CLAUDE.md docs/openers/号池台账.md "docs/openers/0916K-Token治理-泳道开工整读大文件与提醒误停根治.md"
3. git commit -m "fix(token治理): Read 整读大文件拦截 ＋ 无头泳道免长会话提醒 ＋ tech-debt 已还条目归档（0916K）"
4. 按 CLAUDE.md 并发协议 push

【八、收工报告】逐项给，⛔ 不写「均已完成」：
1. commit hash
2. 【三】2 的回复原文、【三】3 计数、Read 闸保留还是撤回
3. 【四】2、3 两个数字
4. tech-debt 前 → 后 KB、闸上限
5. 下一步只给一条：「照常发下一批泳道（说『开始泳道看护』），看护报告里核一句：各条日志有无『已拦截』字样、有无半途收尾」
