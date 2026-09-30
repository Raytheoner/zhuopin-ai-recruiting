[Mac]0930E-已移出识别与伪ready清零
【设置】执行环境: Codex ｜ Session: 新开（run-lanes 无头起）｜ 分支: lane-0930e-moved-out-detect ｜ worktree: ✅ 勾（改生成器与测试）｜ 工作区: .claude/worktrees/lane-0930e-moved-out-detect ｜ 派发: Codex·泳道看护（automation hr）

## 零、为什么

`0930C` 的 conflicts 核真把 35 条一律判「真身对（台账 stale）」并订正台账，但其中 8 条 M1 条目的
「真身=待开」其实是**生成器不认识 tasks.md 的续行** `⤷ 已移出…` 造成的误判（生成器只看条目行本身）。
后果：`ready` 里出现伪可发车项，看护直接发车会重做「已移出/已建成」的活。本 opener 修根因并订正状态。

## 一、授权与边界（Shao Peishen 2026-09-30 答 1a 授权的**专门 opener**；改调度器脚本＝红线③）

⛔ 只许改：`scripts/dispatcher_backlog.py`、`tests/test_dispatcher_backlog.py`、
`docs/roadmap/任务台账.yaml`、`docs/roadmap/conflicts-核真-20260930.md`。
⛔ 不改 `run-lanes.sh` 及任何执行器、不改 `.agents/skills/**`、不改判据语义（只补「已移出/不走 plan」两类识别）。

## 二、前置

1. 复现基线：`PYTHONPATH=. ./venv/bin/python -m scripts.dispatcher_backlog --show ready` 当前含
   `m1-job-profile-intake/9.2`、`m1-job-profile-intake/U1/plan`、`m1-intake-quality-fixes/U8/plan`（伪可发车）。
2. 读 `docs/roadmap/conflicts-核真-20260930.md` 的这三条与 8 条 M1 条目节；读
   `openspec/changes/m1-job-profile-intake/tasks.md` 的 `- [ ] 9.2`／`1.5b`／`1.7` 原文（续行含
   `⤷ **已移出**…`／`⤷ 已移出→调度基础设施`），以及
   `openspec/changes/m1-intake-quality-fixes/delivery-units.md:111`（「不建议当成一份 TDD plan 跑
   `run-build`」，同文件表格写「9 → 不走 plan」）。

## 三、交付物

1. **`scripts/dispatcher_backlog.py`**：
   - tasks.md 解析识别**条目行的下一行续行**（以 `⤷` 开头且含「已移出」）⇒ 该条目视同已处理，
     不计入未勾、不再产出「待开」真身；
   - plan 项：若其单元在 `delivery-units.md` 被标注「不走 plan」类语义（如第 8 章），或该单元已
     全部完成而 plan 文件从未落档（早期单元）⇒ 该 plan 项不产出「待开」真身（避免把已建成的活重新排期）。
2. **`docs/roadmap/任务台账.yaml`**：订正这 8 条为 `完成`（`m1-job-profile-intake/1.5b`／`1.7`／`5.6`／
   `6.8`／`9.2`／`U1`／`U5`／`U6`，备注里写「已移出（续行原文为据）」）；`U1/plan`、`U8/plan` 按不适用订正
   （备注写依据：U1 早期单元无 plan 落档；U8 见 `delivery-units.md:111`「不走 plan」）。
3. **`tests/test_dispatcher_backlog.py`**：新增用例——① 续行「已移出」不再判未完成；②
   `m1-job-profile-intake/9.2`、`U1/plan`、`m1-intake-quality-fixes/U8/plan` 不进 ready；③ 生成器幂等。
4. **`docs/roadmap/conflicts-核真-20260930.md`**：追加一节「2026-09-30 追加：已移出/不走 plan 识别修正」，
   记录根因、订正清单与残留例外（若有）。

## 四、机器判据

```bash
cd /Users/paulshao/Projects/HumanResource
set -e
./venv/bin/python -m pytest tests/test_dispatcher_backlog.py -q -p no:cacheprovider
PYTHONPATH=. ./venv/bin/python -m scripts.dispatcher_backlog >/dev/null
ready="$(PYTHONPATH=. ./venv/bin/python -m scripts.dispatcher_backlog --show ready 2>/dev/null | cut -f1)"
for x in m1-job-profile-intake/9.2 m1-job-profile-intake/U1/plan m1-intake-quality-fixes/U8/plan; do
  ! printf '%s\n' "$ready" | grep -qx "$x"
done
miss="$(./venv/bin/python -c "import yaml;d=yaml.safe_load(open('docs/roadmap/任务台账.yaml',encoding='utf-8'));items=[v for v in d.values() if isinstance(v,list)][0];m={i.get('id'):i for i in items if isinstance(i,dict)};ids=['m1-job-profile-intake/1.5b','m1-job-profile-intake/1.7','m1-job-profile-intake/5.6','m1-job-profile-intake/6.8','m1-job-profile-intake/9.2','m1-job-profile-intake/U1','m1-job-profile-intake/U5','m1-job-profile-intake/U6'];print(sum(1 for x in ids if m.get(x,{}).get('状态')!='完成'))")"
[ "$miss" -eq 0 ]
n="$(PYTHONPATH=. ./venv/bin/python -m scripts.dispatcher_backlog --show conflicts 2>/dev/null | tail -1 | sed 's/[^0-9]*//g')"
[ "$n" -le 2 ]
if [ "$n" -gt 0 ]; then grep -q 'U1/plan' docs/roadmap/conflicts-核真-20260930.md; grep -q 'U8/plan' docs/roadmap/conflicts-核真-20260930.md; fi
cp docs/roadmap/任务台账.yaml /tmp/0930E-ledger-1.yaml
PYTHONPATH=. ./venv/bin/python -m scripts.dispatcher_backlog >/dev/null
diff -q /tmp/0930E-ledger-1.yaml docs/roadmap/任务台账.yaml
```

## 五、并发协议（逐字照做）

1. 只 `git add` 本 opener 明确列出的路径。⛔ 禁止 `git add -A` / `git add .` / `git commit -a` / `git stash`。
2. `git status` 里出现别人的改动是正常的，不要停下、不要问、不要顺手提交。
3. ⛔ 不要在 commit 前 pull；push 被拒才 `git pull --rebase --autostash origin main` 重试，最多 3 次。
4. 报 `.git/index.lock` 已存在 → 等 5 秒重试最多 5 次，⛔ 绝不删除该锁。

## 六、红线

- ⛔ 不改 `--show ready`／`--show conflicts` 的判据定义，只补语义识别；⛔ 不删冲突条目掩盖问题。
- ⛔ 不把「移出」写成「待办」；订正必须能指到 tasks.md 原文行或 delivery-units 原文行。
- ⛔ 不写提问句；不确定 ⇒ 保守（保留 + 登记例外）并在报告单列，输出 `OPENER_PARTIAL`。

## 七、收口

1. 测试全绿后在分支上 commit（只列本 opener 的路径），提交信息带 `(0930E)`。
2. 合回 main：`git -C /Users/paulshao/Projects/HumanResource merge --ff-only <分支>`；失败改
   `git merge <分支> --no-edit`；然后 `git push origin main`（被拒按并发协议重试 ≤3 次）。
   注：`0930D` 起 run-lanes 收口阶段有 `lane_collect.py` 代提交/代合并兜底，但本 opener 仍按上面顺序自证。
3. 🔴 `git cherry -v main <分支>` 不得出现 `+` 行；确认内容真在 main 上前不许输出 `OPENER_DONE`。
4. 报告里附 ready/conflicts 前后对比，然后顶格 `OPENER_DONE`（或 `OPENER_PARTIAL: <原因>`）。
