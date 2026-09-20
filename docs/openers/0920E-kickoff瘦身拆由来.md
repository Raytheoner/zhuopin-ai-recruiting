# [Mac]0920E kickoff 瘦身：成因段拆进由来文件

## 零、为什么有这条

2026-09-20 Shao Peishen 指出姐妹项目（Win 端）的 token 浪费形状——`zhuopin-lane-watch` 正本 `SKILL.md` 44,445 B，比开场必读件加起来还大，成因段、判据成因、缺陷表在正文里反复展开。本项目核对下来有**同形的一件**：

- `.claude/skills/kickoff/SKILL.md` ＝ **35,616 B**，本仓库最大的单份 skill
- `CLAUDE.md` 有三处指向它（强制格式模板、引用式 opener、编号与抬头）⇒ **凡出一份 opener 就整读一次**
- 里面大量篇幅是「为什么／血的教训／历史归因／实证数字」，是**读一次就够的成因**，不是每次出 opener 都要重读的判据

本项目已有同款先例：`docs/CLAUDE-md-由来.md`——CLAUDE.md 正文留结论，成因搬去由来文件。本条照此办理。

🔴 **这是瘦，不是删、不是关**：`kickoff` 在用（`lane-dispatch`／`task-dispatcher`／所有 opener 产出都依赖它），
⛔ 不许删任何一条规则、⛔ 不许改任何判据的语义、⛔ 不许调整模板结构。

## 一、做什么

1. **新建** `docs/kickoff-由来.md`，把 `.claude/skills/kickoff/SKILL.md` 里下列性质的段落**原文整块搬过去**（⛔ 不改写、⛔ 不压缩）：
   - 「为什么／根因／血的教训／历史归因」叙事段
   - 实证数字与日期复盘（如 08-27 撞号 5 次的过程、09-03／09-04 分类器归因的三步实证表）
   - 已被后续结论推翻但保留备查的旧口径
2. **正文对应位置各留一行指针**，形如：
   `> 成因与实证见 docs/kickoff-由来.md「<原小节标题>」。`
   ⛔ 指针不得吞掉结论——**判据、模板、禁令、机器闸本身一律留在正文**。
3. 搬完后正文按原有章节顺序不变，⛔ 不重排、⛔ 不合并小节。

判断「是成因还是判据」的机械判据：**删掉这段，下一个会话出 opener 时会不会立刻做错？**
会 ⇒ 判据，留正文；不会（只是解释为什么这么定） ⇒ 成因，搬走。拿不准 ⇒ **留正文**，⛔ 不搬。

## 二、开工自核

1. `cd /Users/paulshao/Projects/HumanResource && wc -c .claude/skills/kickoff/SKILL.md` —— 应为 35616（不是则说明已有人动过，**停下登记，不要继续**）。
2. `git status --short` 里 `.claude/skills/kickoff/SKILL.md` 不在改动列表（干净起步）。
3. `ls docs/kickoff-由来.md` 不存在（本条负责新建）。

## 三、完成判据（四条全过才算完，任一不过即停）

1. **体积**：`wc -c .claude/skills/kickoff/SKILL.md` ≤ **15360**（15 KB）。
2. **守恒**：`cat .claude/skills/kickoff/SKILL.md docs/kickoff-由来.md | wc -c` ≥ **33800**（＝原 35616 的 95%）。低于此即说明**删了内容**，⛔ 不许，退回重做。
3. **规则不丢**：下列标记在正文里的出现次数**不得减少**——
   `grep -c '🔴' .claude/skills/kickoff/SKILL.md` ＝ 瘦身前的同一计数（先记下原值）；
   `grep -c '⛔' .claude/skills/kickoff/SKILL.md` 同理。
   （成因段里如确有 🔴／⛔，允许随段搬走，但**必须在收工报告里逐条列出搬走的是哪几行**，由人复核。）
4. **章节齐全**：瘦身前后 `grep -c '^#\{1,3\} ' .claude/skills/kickoff/SKILL.md` 相等（16）。

## 四、并发协议（逐字内嵌）

1. **只 `git add` 本条明确列出的路径**：`.claude/skills/kickoff/SKILL.md`、`docs/kickoff-由来.md`、`docs/openers/0920E-kickoff瘦身拆由来.md`、`docs/openers/OP-0820-全量编排.md`、`docs/openers/号池台账.md`、`docs/roadmap/定夺队列.md`、`docs/session接力.md`。
   ⛔ 禁止 `git add -A` / `git add .` / `git commit -a` / `git stash`（含 `-u`）。
2. `git status` 里出现别人的改动是正常的，不要停下、不要问、不要顺手提交。
3. push 被拒 → `git pull --rebase --autostash origin main` 后重试，最多 3 次。⛔ 不要在 commit 前 pull。
4. 报 `.git/index.lock` 已存在 → 等 5 秒重试最多 5 次，⛔ 默认绝不删除该锁。

## 五、红线

- ⛔ 不改任何判据的语义、不删任何规则、不动模板结构。
- ⛔ 不碰其他 skill（`lane-dispatch`／`requirement-grill` 等本轮不动）。
- ⛔ 不碰 `.51`、不发版、不对外发送。
- ⛔ 不删除、不移动工作区里任何未追踪文件。
- ⛔ `Skill(superpowers:…)` 回 `Unknown skill` ⇒ 登记「⏸ 留步：superpowers 不可达」并停（本条不需要 superpowers）。

## 六、收口

1. 跑 `venv/bin/python -m pytest tests/test_doc_size_budget.py -q`，通过才提交。
2. 提交信息：`refactor(skill): kickoff 成因段拆进 docs/kickoff-由来.md，正文 35.6KB→<实际>KB，判据零删减`
3. `git push origin main`，`git rev-list --count main..origin/main` 为 0。
4. 收工报告必须含：瘦身前后字节数、守恒合计、🔴／⛔ 计数前后对照、**搬走的 🔴／⛔ 行逐条列出**。
5. 顶格打印哨兵 `OPENER_DONE`。⛔ 内容真在 main 上之前不许输出。
