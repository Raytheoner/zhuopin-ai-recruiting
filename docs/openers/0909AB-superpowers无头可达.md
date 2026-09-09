[Mac]0909AB-superpowers无头可达
【设置】执行环境: CC ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（改插件安装作用域 + 可能改 run-lanes.sh 一处，直接在主工作区提交）｜ 工作区: 仓库根 ｜ 派发: Cowork·HR业务线-接力0903B

> 本文件是 `[Mac]0909AB` 的正文（引用式 opener）。Shao Peishen 2026-09-09 裁决：「superpowers 调不到」**是故障，就地修**。
> 已知事实（`docs/session接力.md`【四】）：插件装在 **`projectPath: /Users/paulshao/Projects` 项目作用域**，本仓库 `.claude/settings.json` 的 `enabledPlugins` 开着也解析不到——
> 无头 session 的 cwd 是 `/Users/paulshao/Projects/HumanResource` 或 `/private/tmp/wt-*`，都不是那个 projectPath，于是 08-27 至 09-09 六批 20+ 条 `Skill(superpowers:…)` 一律 `Unknown skill`；交互 session 偶尔能用是运气。
> 修法＝**改成 user 作用域**（对任何 cwd、含 worktree 都生效）；复验不通才退到 run-lanes.sh 加 `--plugin-dir`（CLI 2.1.260 自带：Load a plugin from a directory for this session only）。
> 🔴 若走到改 `run-lanes.sh` 那一步：⛔ 该改动永远不进泳道，且改时必须没有泳道在跑。

【零、前置（任一不过即停）】
1. pgrep -f 'run-lanes.*\.sh' 必须为空；git status -sb 记基线
2. SP=$(ls -d ~/.claude/plugins/cache/claude-plugins-official/superpowers/*/ 2>/dev/null | sort -V | tail -1); echo "$SP"; grep -m1 '"name"' "$SP/.claude-plugin/plugin.json"
   为空 → 停下报告「缓存不在」，⛔ 不猜路径

【一、复现（只读，输出全文贴进报告）】
1. claude plugin list --json 2>&1 —— **全部**插件逐条记 name / scope / projectPath / enabled。Shao Peishen 09-09 指示：**凡 scope=project 且 projectPath ≠ /Users/paulshao/Projects/HumanResource 的，一并修**（同样对本仓库与 worktree 不可见）；scope=user 的不动；只在别的项目用的插件（如企业AI转型专用）不动，列进报告即可
2. 交互态：本 session 直接调 Skill 工具加载 `superpowers:writing-plans`（只加载、读到内容即停，⛔ 不执行它）。记 LOADED / UNKNOWN 原文
3. 无头复现（预期 UNKNOWN）：
   cd /Users/paulshao/Projects/HumanResource && printf '%s' '调用 Skill 工具加载 superpowers:writing-plans。加载成功只回一行 LOADED；失败只回一行 UNKNOWN 并附工具返回原文。不做任何别的事。' | claude -p --output-format text --max-budget-usd 0.50 --permission-mode acceptEdits
4. worktree 形态也复现一次：cd /private/tmp && 同上命令（预期 UNKNOWN）

【二、修：改成 user 作用域】
1. 对【一】1 圈出的每一个插件（superpowers 必在其中）：claude plugin install <name>@<marketplace> --scope user 2>&1（已装同版本时会提示；若提示需先卸项目作用域的那份 → claude plugin disable <name>@<marketplace> --scope project，再 install --scope user）
2. claude plugin list --json —— 圈出的每一个 scope 都必须是 user；⛔ 不留一半
3. 复验：【一】3 与【一】4 各重跑一次，预期都 LOADED
4. 🔴 两条都 LOADED → 跳过【三】，进【四】；任一仍 UNKNOWN → 进【三】

【三、退路：run-lanes.sh 显式挂载（仅在【二】4 失败时做，唯一改动点）】
1. 先验：【一】3 的命令末尾加 --plugin-dir "${SP%/}" 跑一次，必须 LOADED；仍 UNKNOWN → ⛔ 不改脚本，把全部输出贴进报告停下（根因要人看）
2. docs/openers/run-lanes.sh：参数解析之后、打印编排摘要之前加一段（只解析一次）：
   SP_DIR=$(ls -d "$HOME/.claude/plugins/cache/claude-plugins-official/superpowers/"*/ 2>/dev/null | LC_ALL=C sort -V | tail -1); SP_DIR="${SP_DIR%/}"
   [[ -n "$SP_DIR" && -f "$SP_DIR/.claude-plugin/plugin.json" ]] || SP_DIR=""
   摘要与 dry-run 都打印一行：有 → 「superpowers 插件：--plugin-dir $SP_DIR」；无 → 「⚠ 未找到 superpowers 缓存，各条按预案读磁盘 SKILL.md 手工走」
   run_lane() 的 `local args=(-p -n …)` 那行之后加：[[ -n "$SP_DIR" ]] && args+=(--plugin-dir "$SP_DIR")
   文件头注释加一条来源说明（0909AB，2026-09-09）。⛔ 不动其它任何行；bash -n 通过；bash docs/openers/run-lanes.sh --dry-run 看到那一行

【四、真源落档——Shao Peishen 09-09 定：修好以后一律走规范流程，⛔ 不再允许"手工走"】
1. docs/session接力.md【四】那条「无头 session 取不到 superpowers」改写为实测结论（user 作用域后 LOADED / 或退路生效），⛔ 不删旧结论只加新结论
2. .claude/skills/lane-dispatch/SKILL.md：**删掉** opener 模板里那句「superpowers 取不到 → 按磁盘 SKILL.md 手工走，报告单列是否真调到」，改成「`Skill(superpowers:…)` 回 Unknown skill → 登记「⏸ 留步：superpowers 不可达」并停，⛔ 不手工走、⛔ 不自己装插件」；看护者模板里「调不到 superpowers → 已知问题，不是新故障」那条预案改成 **红灯**（该条按 FAIL 处置，进红灯清单）。⛔ 不删历史实证段落（08-27 → 09-09 五批手工走的记录），只加新结论
3. .claude/skills/run-build/SKILL.md 前置检查 1 与 spec-to-plan/SKILL.md 前置检查：各加一句「自 0909AB 起在无头 session 与 worktree 里同样应可达；不可达＝环境故障，按 0909AB【一】复现后修，⛔ 不绕」
4. docs/openers/OP-0820-全量编排.md：只改**看护者节**与**第十五批之后尚未跑的块**里的那句预案（grep "手工走"）；已跑完的历史块⛔ 不追改

【五、提交（只 add 列出的路径）】
git add docs/session接力.md .claude/skills/lane-dispatch/SKILL.md .claude/skills/run-build/SKILL.md .claude/skills/spec-to-plan/SKILL.md docs/openers/OP-0820-全量编排.md docs/openers/0909AB-superpowers无头可达.md
（走了【三】再加 docs/openers/run-lanes.sh；⛔ 禁止 -A / . / -a / git stash；`0909Q` 那个 Cowork session 可能同时在改接力文档与编排文件——别人的改动不停不问，push 被拒 → git pull --rebase --autostash origin main 重试最多 3 次；index.lock 等 5 秒重试 5 次不删）
git commit -m "fix(plugins): superpowers 改装 user 作用域，无头/worktree session 可达（0909AB）"
git push origin main

【六、汇报】plugin list 前后 / 交互态与两次无头探针原文 / 是否走了【三】及 run-lanes.sh diff / dry-run 那一行 / commit hash / ahead
