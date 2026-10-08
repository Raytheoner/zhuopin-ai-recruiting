# codex run-build 执行器缺口与修法（2026-10-08，`1001U`）

> 触发：Shao Peishen 答 `1a`「派专门 opener 定位修法」。现场＝第二批四条 run-build 泳道
> （`1001Q/R/S/T`，批次 `lanes-20261008-170927`）**全部 PARTIAL**，四条日志同报
> 「⏸ 留步：执行环境不可达」。本文件记录逐项定位、已修项与仍待办的执行器级修法。

## 一、现象与现场

- `1001Q/R/S` 三条已 PARTIAL（`1001T` 收尾中）：**无改动、无提交、无越界**；
  失败点全部在前置检查：`codex --version` 不可用、worktree 内 `venv/` 缺失。
- 泳道日志原文（两条独立会话同报）：`codex` 不在 PATH；即便用应用包绝对路径，
  `codex exec` 启动即 `Error: failed to initialize in-process app-server client: Operation not permitted (os error 1)`。

## 二、根因逐项（含实验）

| # | 缺口 | 证据 | 处置 |
|---|---|---|---|
| 1 | **泳道内嵌套 codex 被 Seatbelt 拦** | 本场两个对照实验：嵌套 `--sandbox workspace-write` 与 `--sandbox danger-full-access` **都**报同一 `Operation not permitted`（并伴随 `~/.codex` 状态库只读告警）⇒ 与嵌套档位无关，是沙箱层面禁止 app-server 初始化/本地绑定 | **修法＝改由非沙箱父进程执行**：`codex_sdd_runner` 必须由 launchd/主会话（非 Seatbelt）起；本场已用「主会话直跑」验证可行 |
| 2 | `codex_sdd_runner.py` 里 `CODEX_BIN="codex"` 无解析 | 泳道 PATH 里没有 `codex`（只有 `codex-cli/codex-path/`，含 `rg`） | ✅ 已修：新增 `resolve_codex_bin()`＝`HR_CODEX_BIN` → PATH → 应用包兜底；实跑取解析结果（`tests/test_codex_sdd_runner.py` +2 条） |
| 3 | runner 期望 **Task 会话自行提交**，但 worktree 会话无 git 写权限（0930D 同源） | Task 1 会话实测：`git add` 报 `Unable to create .git/worktrees/.../index.lock: Operation not permitted`，文件留在 worktree 未提交 | ✅ 已修：runner 在父进程**代提交**（`_git_commit()`：`git add -A`＋`git commit -m "chore(sdd): Task N — <plan>"`，无改动视为已落定；提交失败 ⇒ 不勾进度、收工 fail-closed） |
| 4 | review 空输出/rc≠0 被当绿灯 | Task 1 两条 review 日志均 `(无最终文本)`、usage 全 0，但进度仍勾了 Task 1 | ✅ 已修：必须含 `VERDICT: PASS` 且 rc=0 才算过 |
| 5 | worktree 无 `venv/`，子会话跑不了测试 | worktree 内 `./venv/bin/python` 不存在（gitignored） | ✅ 已修：runner 注入 `SDD_PYTHON=<父进程解释器>` 并在 prompt 里写明「用 `"$SDD_PYTHON" -m pytest`」 |
| 6 | `parse_plan` 把 **Global Constraints 截成 56 字符**（段内 `### 工程铁律/合规红线` 被当边界）⇒ 约束透镜对子会话与 reviewer **静默为空** | 排期 Task 2 的「代码质量」review 实测：dry-run 报「Global Constraints 56 字符」＝只剩一句 blockquote 引言；四份 U1 计划同病 | ✅ 已修（`1001G`）：边界改为「**同级/更高级标题**，或裸 `### Task N:`」；四计划实测 56 → **1329~2809 字符**；+2 条断言（`d5b8c5f`，89 passed） |

## 三、仍待办（执行器级，属红线③）

1. ✅ **已落地（`1a` 答后当次完成）**：动作通道新增 **`run-sdd`**（`scripts/action_request.py`）——
   `{"action":"run-sdd","plan":"docs/superpowers/plans/<文件>.md","tasks":"1-3","cwd":".claude/worktrees/<名>"}`；
   三闸（plan 白名单＋存在、tasks 形状、cwd 必须是泳道 worktree）＋同步执行（超时默认 7200s）。
   由 launchd 非沙箱执行 ⇒ runner 与它起的嵌套 `codex exec` 都不在 Seatbelt 里。测试
   `tests/test_action_request.py` 新增 1 正例＋8 反例（与既有 86 条同跑全绿）。
   ⚠️ 已知限制：同步执行期间提交通道被占用（构建完再提交文档）。
2. ✅ 另修：runner 的 **Final Review 不再要求"只读会话里跑 pytest"**（只读沙箱无可用临时目录，
   `tmp_path` 初始化必失败）——测试是否全绿改以任务提交记录与执行器 `## 机器判据` 为准。
3. 第二批四条泳道（`1001Q/R/S/T`）按 PARTIAL 留痕；**渠道 seg1 已由过渡路径跑通并合回 main**
   （`bb99fc8`，判据 20 passed）；其余三包 seg1 由 `run-sdd` 通道逐段冒烟推进。

## 四、验证记录（本场）

- `tests/test_codex_sdd_runner.py`：**8 passed**（含新增 3 条：二进制解析、argv 注入、代提交真提交/无改动两支）。
- 渠道包 Task 1 在修后由非沙箱父进程重跑（见 `lane-1001q-channel-u1-build` 的 `.superpowers/sdd/…` 日志与 git log）。

## 五、补记（2026-10-08 晚，`1001G`）：子任务换挡 flash

- Shao Peishen 指令：本项目**所有子任务一律 flash**（含各 Task 的两次 review 会话）。
- 落地：`scripts/codex_sdd_runner.py` 的 `DEFAULT_MODEL/DEFAULT_REASON` 由 `deepseek-v4-pro/high`
  改为 **`deepseek-flash/low`**（AGENTS.md §2 的 Haiku 档映射）；新增断言
  `test_default_model_is_flash_per_user_instruction` 钉死默认值＋dry-run argv 必现 `-m deepseek-flash`。
  提交 `625cbca`（87 passed）。
- 换挡实录（排期 seg1，worktree `lane-1001r-sched-u1-build`）：Task 1 在换挡指令到达前已由 v4-pro
  跑完并**双 review PASS**，runner 正常代提交 `ef3639b`；主会话按指令在 Task 2 起跑后约半分钟处
  掐断 v4-pro 链（旧 action 落 `20261008-184500-排期seg1.failed`，为诚实记录、非故障），随即重投
  `{"action":"run-sdd", … , "tasks":"2-3"}`（`20261008-215400-排期seg1-flash.action`）。实跑 argv 实测
  `-m deepseek-flash -c model_reasoning_effort=low`（`ps` 取证）。
- 顺带加固（review 实测带出）：无头纪律⑤补一句——计划里的裸 `python3`（本机＝3.9，导入 `app` 即
  `ImportError`）一律换成 `"$SDD_PYTHON"`；排期 Task 2 的 flash 会话已按此执行并全绿。
