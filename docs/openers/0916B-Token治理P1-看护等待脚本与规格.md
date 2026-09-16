[Mac]0916B-Token治理P1-看护等待脚本与规格
【设置】执行环境: CC ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（文件已由 Cowork 写好在主检出，本件只做 macOS 实测与提交）｜ 工作区: 仓库根 ｜ 派发: Cowork·Token治理-0916 ｜ 模型: Sonnet

> 本文件是 `[Mac]0916B` 的正文（引用式 opener）。一次粘贴、内部串行。
> 背景：P0 账本（`docs/token治理/P0-对账.md`）看护会话 $60／837 次调用，根因是看护正文「每 3–5 分钟查一次」。
> Cowork 已写好四样东西（未提交）：`docs/openers/wait-lanes.sh`（阻塞等待，只在状态变化时返回）、`tests/test_wait_lanes.py`、
> `tests/test_caretaker_openers.py`（新看护正文的机器闸）、`.claude/skills/lane-dispatch/SKILL.md` 新增「看护正文的等待一律交给 wait-lanes.sh」一节。
> 这些已在 Linux 上测过 9 passed；**本件的活是在 macOS 上实测（stat/pgrep/ls 行为不同）并提交**，顺带提交 0916A 之后 Cowork 追加的路线图 Phase 7 节。
> 🔴 ⛔ 不改 `run-lanes.sh`、`lane-launcher.sh`、CLAUDE.md。⛔ 不起任何泳道批次。看文件用 `head`/`sed -n`/`grep`，⛔ 不 cat 大文件。

【一、前置自检】
1. `git status --short`，预期（本件相关）：
   ?? docs/openers/wait-lanes.sh ｜ ?? tests/test_wait_lanes.py ｜ ?? tests/test_caretaker_openers.py ｜ ?? 本文件
   M .claude/skills/lane-dispatch/SKILL.md ｜ M docs/token治理/路线图.md ｜ M docs/openers/0916A-Token治理P0-账本基线与报告对账.md ｜ M docs/openers/OP-0820-全量编排.md
   别人的改动（如 0910A opener、session接力.md）照常存在，⛔ 不动不提交。上面任一缺失 → 停下报我。
2. `pgrep -f 'run-lanes.*\.sh'`：有输出只记进报告，不影响本件（本件不碰执行器）。

【二、macOS 实测】
1. `bash -n docs/openers/wait-lanes.sh && echo OK`；`ls -l docs/openers/wait-lanes.sh` 应有 x 位，没有就 `chmod +x`
2. `./venv/bin/python -m pytest -q tests/test_wait_lanes.py tests/test_caretaker_openers.py 2>&1 | tail -5` → 预期 9 passed
   不过 → 读失败断言，修 `wait-lanes.sh`（多半是 macOS 的 stat/pgrep/ls 差异），⛔ 不改测试的断言去迁就脚本；最多修 3 轮，仍不过 → 收工报 PARTIAL
3. 真实数据只读核对（用一个已收敛的历史批次 + 不存在的 PID，应立刻 EXITED 并打印 summary）：
   `bash docs/openers/wait-lanes.sh --pid 999999 --logdir .claude/handoff/lanes-20260909-234309 --interval 1 --max 5; echo rc=$?`
   预期首行含 `EXITED`、随后是该批 summary 表、`rc=0`。
4. 全量基线：`./venv/bin/python -m pytest -q 2>&1 | tail -3` → 0 失败（新测试进了全量，看护者开跑前的基线会跑到它们）。有失败 → 看是否本件引起：是则修，否则只记进报告。

【三、提交】
1. `git diff docs/openers/OP-0820-全量编排.md | grep '^[+-]|' ` → 应只有 `[Mac]0916B` 这一行新增；混了别人的改动 → 该文件不 add，报告里写「台账行未提交」
2. `git status --short | grep -i pycache` 应为空（有就说明没被忽略，⛔ 不提交它）
3. git add docs/openers/wait-lanes.sh tests/test_wait_lanes.py tests/test_caretaker_openers.py .claude/skills/lane-dispatch/SKILL.md docs/token治理/路线图.md "docs/openers/0916A-Token治理P0-账本基线与报告对账.md" "docs/openers/0916B-Token治理P1-看护等待脚本与规格.md" docs/openers/OP-0820-全量编排.md
4. git commit -m "feat(token治理): P1 看护阻塞等待脚本 wait-lanes.sh ＋ 看护正文机器闸 ＋ lane-dispatch 盯守规格（0916B）"
5. 按 CLAUDE.md 并发协议 push（⛔ 不 add -A；index.lock 等 5 秒重试最多 5 次；push 被拒才 pull --rebase --autostash 再推，最多 3 次）

【四、收工报告】逐项给，⛔ 不写「均已完成」：
1. commit hash（`git log --oneline -3` 反查）与 `git status -sb` ahead 数
2. 【二】2 与 4 的 pytest 末行原样；若修过脚本，改了哪几行、为什么
3. 【二】3 的输出前 5 行原样
4. 下一步只给一条：「回 Cowork 说『出 Phase 2』」
