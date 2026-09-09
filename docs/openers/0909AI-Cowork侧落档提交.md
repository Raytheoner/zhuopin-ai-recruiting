[Mac]0909AI-Cowork侧落档提交
【设置】执行环境: CC ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（只提交文档）｜ 工作区: 仓库根 ｜ 派发: Cowork·HR业务线-接力0909Q

> 本文件是 `[Mac]0909AI` 的正文（引用式 opener）。把 2026-09-09 Cowork 侧攒下的落档一次提交干净。
> ⛔ 只提交文档，不改任何代码、不跑任何服务、不发任何消息。

【一、先看清】git status --short。预期这几条（多出来的**单列报告、不提交**）：
  M docs/跟进信/README-跟进信清单.md          （人事部#1 → ✅ 已推送 2026-09-09 ＋ 两条状态订正记录）
  M openspec/changes/hr-wecom-aibot-liaison/tasks.md （8.6 补前置：专员先私信一次，群里只收文字、文档只走私信）
  M docs/session接力.md                        （新增 ⑱ 跟进信线收口）
  ?? docs/findings/2026-09-09-win端aibot收发实证-对8.6灰度的三条影响.md
  ?? docs/openers/0909AD-跟进信群发CLI.md
  ?? docs/openers/0909AE-首次真实建连验证.md   （若已被别的 session 提交则不在列，正常）
  ?? docs/openers/0909AI-Cowork侧落档提交.md   （本文件）
  ?? _to_delete/                               🔴 **绝不 add**

【二、提交】只 add 下面这些路径，⛔ 禁止 git add -A / git add . / commit -a：
  git add docs/跟进信/ openspec/changes/hr-wecom-aibot-liaison/tasks.md docs/session接力.md docs/findings/ docs/openers/0909AD-跟进信群发CLI.md docs/openers/0909AE-首次真实建连验证.md docs/openers/0909AI-Cowork侧落档提交.md
  git status --short 复核：暂存区里**不得出现** `_to_delete/`、`.env`、`tools/liaison/.env`、`data/` 下任何文件。出现即停下报，⛔ 不 commit
  git commit -m "docs(跟进信): 人事部#1 已发出与两次状态订正落档；8.6 补私信前置；win 端收发实证与三份 opener 入库"
  git push origin main
push 被拒 → git pull --rebase --autostash origin main 后重试最多 3 次；index.lock 等 5 秒重试 5 次不删。

【三、顺手清一件（可选，失败不阻塞）】`claude/mac0909ac-td19-td36-isolation-9b00fa` 这条分支：
`rev-list` 报未合 1、`cherry` 报 `+`，但已核实**是 main 被 rebase 过的假阳性**——`client.run()` 适配与 netguard 都已在 main 上，
且 main 上 `session_client.py` 已被 `840f5cf` 改得更新，分支比 main 落后 2374 行。**代码零损失**。
可 `git worktree remove` ＋ `git branch -D` 清掉。⚠️ 清之前再跑一次 `git branch --contains` 复核，不确定就跳过、只报告。

【四、⛔ 不做】⛔ 不动 `_to_delete/`（Shao Peishen 自己删）；⛔ 不碰任何 `tools/liaison/` 代码；⛔ 不跑 send-followup；⛔ 不装 launchd；⛔ 不请任何人发消息。

【五、收工】报告：commit hash、push 结果、暂存区守卫复核结论、分支是否清掉。
