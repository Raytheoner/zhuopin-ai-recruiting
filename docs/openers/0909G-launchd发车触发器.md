[Mac]0909G-launchd发车触发器
【设置】执行环境: CC ｜ Session: 新开 ｜ 分支: main ｜ worktree: ❌ 不勾（新增一个触发脚本 + 一个安装脚本 + 改 skill 文档，直接在主工作区 TDD 提交）｜ 工作区: 仓库根 ｜ 派发: Cowork·HR业务线-接力0903B

> 本文件是 `[Mac]0909G` 的正文（引用式 opener）。目的：**让泳道发车不再需要 Shao Peishen 每批点一次 Run**。
> ⛔ 本条不改 `docs/openers/run-lanes.sh`（第十二批实证改它的 opener 不能与泳道同跑；本条只新增旁路文件，可与第十三批并行）。

【零、为什么要这个】
09-03 看护者自己 `nohup` 起脚本成功过一次；09-04 起每批都被 CC Desktop 的 Auto Mode 分类器拦（判「无人值守起会自主 commit/push 的子 session」高风险），
`settings.json` 白名单齐全也拦、`run_in_background` 也拦（`lanes-20260904-093026-看护报告.md` §五）。此后靠他点 Run 发车——每批一次人工动作，正是 CLAUDE.md 说的「伪装成机制的人工节奏控制」。
根治思路与「企业AI转型」侧一致（那边用 Windows 计划任务跑 `工具-opener批处理执行v2.ps1`）：**起进程的不是 Claude，是操作系统**。Mac 上用 launchd `WatchPaths`：
看护者只**写一个文件**（Write 工具，不走 Bash，不触分类器），launchd 看到文件就以他本人的用户身份起 `run-lanes.sh`。

【一、产物（三件，⛔ 不多不少）】
1. `docs/openers/lane-launcher.sh`（新）：被 launchd 调用。逻辑：
   - 扫 `$REPO/.claude/handoff/launch/*.request`（按文件名排序，一次只处理最早的一个）；没有就退出 0
   - 读该文件第一行作为 `run-lanes.sh` 的参数（如 `--full-auto --yes` 或 `--full-auto --yes --only 0909C,0909D`），⛔ 只允许这些参数：`--full-auto --yes --only <逗号编号> --max-parallel N --stagger N --budget N`，其它任何 token 一律拒绝并把请求改名为 `.rejected` 写明原因（防止请求文件成为任意命令入口）
   - 拒绝并发：若已有 `run-lanes.sh` 进程在跑（`pgrep -f 'run-lanes.*\.sh'` 非空）→ 请求改名 `.deferred`，退出 0（launchd 下次 WatchPaths 触发再试；看护者写请求时也会先查）
   - 起：`cd $REPO && nohup bash docs/openers/run-lanes.sh <参数> > .claude/handoff/launch/<同名>.boot.log 2>&1 &`，把 PID 与 boot.log 路径写进 `<同名>.started`，请求文件改名 `<同名>.consumed`
   - 全程 `export LC_ALL=C`（与 run-lanes.sh 一致）；脚本自身⛔ 不 source 任何 .env
2. `scripts/install_lane_launcher.py`（新，幂等，只由 Shao Peishen 在 Terminal 跑）：写 `~/Library/LaunchAgents/com.zhuopin.hr.lane-launcher.plist`
   （`ProgramArguments` = `/bin/bash <绝对路径>/docs/openers/lane-launcher.sh`；`WatchPaths` = `<绝对路径>/.claude/handoff/launch`；`RunAtLoad` false；`StandardOutPath/ErrorPath` 落 `.claude/handoff/launch/launchd.log`），
   然后 `launchctl bootout gui/$UID <plist>`（忽略失败）+ `launchctl bootstrap gui/$UID <plist>`，打印 `launchctl print gui/$UID/com.zhuopin.hr.lane-launcher` 的状态行。
   参考 `scripts/allow_run_lanes.py` 的写法（幂等、打印前后状态）。⛔ 本 session 不执行它（起 launchd 属安全配置，分类器会拦，也不该由 Claude 代做）
3. `.claude/skills/lane-dispatch/SKILL.md` ④ 与看护者模板：发车步骤改为「先 `pgrep -f run-lanes` 为空 → Write `.claude/handoff/launch/<批次时间戳>.request`（内容一行参数）→ 等 `<同名>.started` 出现（最多 60 秒）→ 从中读 PID 与 boot.log」；保留旧的点 Run 路径作为 launchd 未装时的退路（判据：`launchctl print gui/$UID/com.zhuopin.hr.lane-launcher` 非 0 即未装）。
   把 09-03 → 09-04 → 09-08 这三步（自己起成功一次 / 白名单无效 / 点 Run）的实证一起写进去，⛔ 不删旧结论只加新结论。

【二、验证（正反都贴报告）】
1. 单测 `tests/test_lane_launcher.py`：用临时目录 + 假 `run-lanes.sh`（只 echo 参数并 sleep 1）跑 lane-launcher.sh：
   ① 合法请求 → `.started` 含 PID、`.consumed` 存在、假脚本收到的参数逐字相同
   ② 非法 token（如 `; rm -rf`、`--full-auto --yes --model x`）→ `.rejected` 且假脚本未被调用
   ③ 已有 run-lanes 进程（用假进程名冒充）→ `.deferred`
2. `--dry-run` 请求不允许（launcher 只起真跑；dry-run 由看护者自己在 session 里跑，那条命令分类器不拦）——单测 ④ 断言 `--dry-run` 被拒
3. ⛔ 本 session 不装 launchd、不起真泳道（第十三批正在跑）

【三、提交（只 add 列出的路径）】
git add docs/openers/lane-launcher.sh scripts/install_lane_launcher.py tests/test_lane_launcher.py .claude/skills/lane-dispatch/SKILL.md docs/openers/0909G-launchd发车触发器.md
⛔ 禁止 -A / . / -a；第十三批泳道在改 tools/liaison/，别人的改动不停不问；push 白名单已在；index.lock 等 5 秒重试 5 次不删。
git commit -m "feat(lanes): launchd WatchPaths 发车触发器——看护者写请求文件即发车，不再需要人点 Run"
git push origin main

【四、汇报】三个文件路径 / 四条单测输出 / commit hash / 以及**给 Shao Peishen 在 Terminal 跑一次的安装命令**（就一行：`python3 /Users/paulshao/Projects/HumanResource/scripts/install_lane_launcher.py`）
