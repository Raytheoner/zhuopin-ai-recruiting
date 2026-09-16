[Mac]0916D-Token治理P3-滚动大文件归档与体积闸
【设置】执行环境: CC ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（改的是主检出上的共享文档，必须在主工作区一次改完一次提交）｜ 工作区: 仓库根 ｜ 派发: Cowork·Token治理-0916

> 本文件是 `[Mac]0916D` 的正文（引用式 opener）。一次粘贴、内部串行。
> 目标：`OP-0820-全量编排.md` 371 KB → 约 2 KB、拆出 `号池台账.md`、`session接力.md` 116 KB → ≤ 40 KB；以后靠体积闸＋归档工具保持，不靠人记得。
> Cowork 已写好（未提交）：`scripts/archive_docs.py`（机械归档＋行守恒校验，默认 dry-run）、`tests/test_archive_docs.py`（5 条）、`tests/test_doc_size_budget.py`（体积闸，**归档前必然失败，正常**）、
> CLAUDE.md（号池位置＋体积闸与「【已闭环】」约定）、`kickoff`／`lane-dispatch` 两 skill 的路径改动、路线图 Phase 3 状态行。
> 已在 Linux 上对真实文档的拷贝跑过：守恒通过、幂等、`[Mac]` 编号全集不变。
> 🔴 铁律：内容**只搬不改不删**。对 `session接力.md` 唯一允许的手工改动是在 `##`/`###` 标题行里加「【已闭环】」四个字，工具会核对。
> ⛔ 不起泳道批次；⛔ 不碰 `run-lanes.sh`、`run-batch.sh`。

【一、前置自检】
1. `pgrep -f 'run-lanes.*\.sh'` 必须为空，否则停下报我
2. `git status --short -- docs/session接力.md` 必须为空；`git diff docs/openers/OP-0820-全量编排.md | grep -E '^[+-][^+-]'` 必须**恰好一行**且是 `+| 09-16 | \`[Mac]0916D\`` 那行（Cowork 登记的本件台账行）。有别人未提交的改动 → 停下报我，⛔ 不要把别人的改动一起搬走提交
3. `git status --short` 本件相关预期：?? scripts/archive_docs.py ｜ ?? tests/test_archive_docs.py ｜ ?? tests/test_doc_size_budget.py ｜ ?? 本文件 ｜ M CLAUDE.md ｜ M .claude/skills/kickoff/SKILL.md ｜ M .claude/skills/lane-dispatch/SKILL.md ｜ M docs/token治理/路线图.md
4. `git pull --rebase --autostash origin main`（先对齐，缩短与其他会话撞车的窗口）；之后重跑 2，确认没被拉下来的提交弄脏

【二、快照（归档前）】
1. `grep -rhoE '\[Mac\][0-9]{4}[A-Z]{1,2}|【OP-[0-9A-Za-z]+-[0-9A-Za-z]+】' --include=*.md docs | sort -u > /tmp/ids-0916d-before.txt; wc -l < /tmp/ids-0916d-before.txt`
2. `bash docs/openers/run-lanes.sh --dry-run 2>&1 | grep '条目总数自检'` 记下原样（当前队列为空，预期 `Σ=N=M=0`）
3. `wc -c docs/session接力.md docs/openers/OP-0820-全量编排.md`

【三、编排文件与号池台账（纯机械）】
1. `python3 scripts/archive_docs.py --init-ledger --scope plan` → 看计划：OP-0820 保留段只应有「号池台账指针」「并发协议」两段＋新增「待执行区」；守恒校验通过
2. `python3 scripts/archive_docs.py --init-ledger --scope plan --apply`；`git status --short -- docs/session接力.md` 仍应为空
3. `grep -rhoE '\[Mac\][0-9]{4}[A-Z]{1,2}|【OP-[0-9A-Za-z]+-[0-9A-Za-z]+】' --include=*.md docs | sort -u | diff /tmp/ids-0916d-before.txt - && echo IDS-SAME` → 必须 IDS-SAME
4. 再跑【二】2，输出必须与归档前逐字相同
5. `grep -c '\[Mac\]0916D' docs/openers/号池台账.md` → 1（本件的台账行已随整节搬过去）
任一不符 → `git checkout -- docs/openers/OP-0820-全量编排.md && rm -rf docs/openers/号池台账.md docs/openers/归档 docs/archive` 复原，停下报我。

【四、接力文件：标记已闭环段落再归档】
（此时 `session接力.md` 仍与 HEAD 相同——打标必须在未搬运的原文件上做，`--verify-tags` 才比得了）
1. 用 Read 工具分页读 `docs/session接力.md`（每次 ≤ 300 行），逐个 `##` 段、再逐个 `###` 小节判断，满足任一条即在其标题行加「【已闭环】」：
   a. 讲的是已经结束的事——批次「已跑完／已收敛／已合入」，事故「已修复并有 commit hash」，且段内没有未完成的待办（没有状态不是 ✅ 的四列待办行、没有「⏸」「待他」「仍未」之类未了结字样）
   b. 是被更新状态取代的旧快照（如「一、状态快照（2026-09-09…）」这类带过期时间戳的全局快照，且后面已有更新的 🆕 段覆盖）
   c. 教训类段落，其结论已固化进 skill 或 CLAUDE.md（`grep -rn '<关键词>' .claude/skills CLAUDE.md` 能查到对应规则才算）
   已划掉的 `### ~~…~~` 小节不用标，工具自动搬。
   ⛔ 永不标：「开场词」「新 session 先看」「三、待决策 / 悬置」里仍悬着的项、「四、绕不开的环境事实」「五、绕不开的约束」「六、已固化进 skill 的判据」、任何含未完成待办的段。
   一个 `##` 段里有开有闭 → 只给闭环的 `###` 标，⛔ 不标整段。拿不准 → 不标。
2. `python3 scripts/archive_docs.py --verify-tags` → 必须 ✓（报错＝碰了正文，`git diff docs/session接力.md` 找出来改回去）
3. `python3 scripts/archive_docs.py --scope relay` 看计划 → `--scope relay --apply`；`wc -c docs/session接力.md`
4. 仍 > 40 KB：`git checkout -- docs/session接力.md && rm -f docs/archive/session接力-归档.md` 复原，只针对剩下的大段再过一遍 1（上次的标记要重新加），回到 2。最多两遍。
   两遍后仍超 → 保留第二遍结果，把 `tests/test_doc_size_budget.py` 里接力文件上限改成「当前 KB 向上取整 + 5」，并在 `docs/token治理/路线图.md` Phase 3 状态行后追加：「接力文件闸暂设 N KB：剩余大段 <段名+KB>，未闭环原因 <一句话>」。⛔ 不删内容凑数。
5. `sed -n 1,60p docs/session接力.md` 确认开场词与「新 session 先看」完整；若有句子指向已搬走的段（「见上文 ⑫」之类），在该句末补「（已归档至 docs/archive/session接力-归档.md）」——这是归档后唯一允许的补字，逐条列进报告

【五、测试】
1. `./venv/bin/python -m pytest -q tests/test_archive_docs.py tests/test_doc_size_budget.py 2>&1 | tail -3` → 0 失败
2. `./venv/bin/python -m pytest -q 2>&1 | tail -3` → 0 失败（非本件引起的只记报告）
3. `python3 scripts/archive_docs.py` → 应输出「无可归档内容」（幂等）

【六、提交】
git add scripts/archive_docs.py tests/test_archive_docs.py tests/test_doc_size_budget.py CLAUDE.md .claude/skills/kickoff/SKILL.md .claude/skills/lane-dispatch/SKILL.md docs/token治理/路线图.md docs/openers/OP-0820-全量编排.md docs/openers/号池台账.md docs/openers/归档 docs/session接力.md docs/archive "docs/openers/0916D-Token治理P3-滚动大文件归档与体积闸.md"
git commit -m "chore(token治理): P3 编排文件与接力文件原样归档＋号池台账拆出＋体积闸（0916D）"
按 CLAUDE.md 并发协议 push。⚠️ push 被拒要 rebase 时，若冲突落在接力或编排文件上（别的会话刚改过）：`git rebase --abort`，`git reset --soft HEAD~1`，重新从【一】4 开始跑一遍（工具幂等，重跑安全），⛔ 不手工解冲突。

【七、收工报告】逐项给，⛔ 不写「均已完成」：
1. commit hash；`git status -sb` ahead 数
2. 体积表：五个文件归档前 → 后 KB
3. IDS-SAME 与 dry-run 自检行两项核对结果
4. 标了「【已闭环】」的段落清单（标题前 30 字），以及保留在接力文件里最大的 3 段及 KB
5. 【四】5／6 是否触发，触发了写了什么
6. 下一步只给一条：「发下一批泳道（说『开始泳道看护』），攒 A/B 样本」
