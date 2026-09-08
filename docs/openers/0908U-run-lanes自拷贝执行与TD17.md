[Mac]0908U-run-lanes自拷贝执行与TD17
【设置】执行环境: CC ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（改一个脚本 + 一处 docstring，直接在主工作区 TDD 提交）｜ 工作区: 仓库根 ｜ 派发: Cowork·HR业务线-接力0903B

> 本文件是 `[Mac]0908U` 的正文（引用式 opener）。🔴 **必须在下一批泳道发车之前、单独跑完**——本条改的是 `run-lanes.sh` 本身，
> 第十二批实证（`lanes-20260908-171737-第十二批看护报告.md` §二）：把改 `run-lanes.sh` 的 opener 放进它自己驱动的泳道，运行中的 bash 按旧字节偏移续读新文件，
> 编排器自我损坏、六条被派两遍。⛔ 本条永远不进泳道。

【一、前置】
1. pgrep -f run-lanes.sh 必须无进程；grep -c "^> 泳道：" docs/openers/OP-0820-全量编排.md 应为 0（第十二批已全摘）
2. git status -sb：main 与 origin 同步；工作区干净或只有 Cowork 侧的 docs/session接力.md / OP-0820-全量编排.md / docs/openers/0908U-*.md / 0909*-*.md
3. ./venv/bin/python -m pytest -q 记下基线（python 不在 PATH）

【二、改 docs/openers/run-lanes.sh（两处）】
1. **自拷贝执行**（第十二批看护报告 §九推荐，机制性根治）：脚本最前面（在 `set -u` 之后、任何逻辑之前）加：
   若环境变量 `RUN_LANES_COPY` 未设 → `cp "$0"` 到 `$(mktemp -d)/run-lanes.sh`，`RUN_LANES_COPY=1 exec bash <副本> "$@"`；已设则继续。
   副本里 `REPO`/`PLAN` 仍指仓库绝对路径（脚本本来就是绝对路径常量，⛔ 不要改成相对 `$0`）。
   dry-run 与实跑都走这条路；`--only`、`--chain` 等参数原样透传。
2. **零条目路径的自检可达性**（报告 §五黄灯）：把 0908R 加的「条目总数自检」挪到 exit 12 守卫之前，或在 exit 12 分支里也打印那一行——判据：队列为空时 dry-run 输出里**也**能看到「条目总数自检：Σ=N=M=0」再 exit 12。

【三、验证（正反两次都贴进报告）】
1. bash docs/openers/run-lanes.sh --dry-run：第一行能看出是副本在跑（打印副本路径），且零条目时自检行出现
2. 反证：起一个 `bash docs/openers/run-lanes.sh --dry-run` 的同时（或在脚本里临时加 `sleep 20` 后）向 run-lanes.sh 追加 80 行注释——运行实例不受影响、正常结束；改回后再跑一次
3. ⛔ 不起真泳道

【四、TD-17 顺手修（app/ 一行，轻量通道）】
app/outbound/delivery.py:12 docstring 里的 Windows 路径含非法转义 `\z` 等 → 改成 raw docstring（`r"""`）或把反斜杠写成 `\\`；
验证：`./venv/bin/python -W error -c "import ast,sys; ast.parse(open('app/outbound/delivery.py').read())"` 不再报 SyntaxWarning；
tools/liaison/tests/test_app_does_not_import_tools.py 跑一遍 0 warning。docs/tech-debt.md TD-17 标 ✅ 已还（commit）。⛔ 不动该文件其它任何字符。

【五、提交（只 add 列出的路径）】
git add docs/openers/run-lanes.sh app/outbound/delivery.py docs/tech-debt.md docs/session接力.md docs/openers/OP-0820-全量编排.md .claude/skills/lane-dispatch/SKILL.md .claude/skills/kickoff/SKILL.md docs/openers/0908U-run-lanes自拷贝执行与TD17.md
（接力/编排/两个 skill 是 Cowork 侧第十三批编排与规矩落档，一并带上；⛔ 禁止 -A / . / -a；index.lock 等 5 秒重试 5 次不删）
git commit -m "fix(run-lanes): 自拷贝到临时目录再 exec，运行实例与仓库文件脱钩 ＋ 零条目路径自检可达 ＋ TD-17 非法转义修复"
git push origin main

【六、汇报】自拷贝段原文 / 正反两次 dry-run 输出 / TD-17 验证输出 / commit hash / ahead。跑完回 Cowork 一句，第十三批看护者才能发车。
