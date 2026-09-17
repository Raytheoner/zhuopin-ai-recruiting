#!/usr/bin/env bash
# dispatcher_event.sh —— 任务调度器（R2）的事件唤醒薄壳（2026-09-17，0917AM）
# ===========================================================================
# 调用方：~/Library/LaunchAgents/com.zhuopin.hr.task-dispatcher.plist
#         （scripts/install_task_dispatcher.py 生成；WatchPaths .claude/handoff/events/
#           ＋ StartCalendarInterval 每日 09:00 兜底）。合入后由 Cowork 经动作通道
#         `install-agent` 安装，不需要人开终端。
#
# 本壳只做五件事，逻辑全在 skill `.claude/skills/task-dispatcher/SKILL.md`：
#   ① cd 仓库根、PATH 补 ~/.local/bin（claude 装在那儿；launchd 不继承登录 shell 的 PATH）
#   ② 单实例锁 .claude/handoff/dispatcher.lock（pid 存活即退出；死 pid 视为孤儿锁覆盖）
#   ③ 判「该不该起会话」：有未处理事件文件、或当日兜底戳不存在 ⇒ 起；否则静默退出
#      —— skill 把事件搬进 events/processed/ 会再触发 WatchPaths，没有这条判据就是死循环
#   ④ printf | claude -p 以 Sonnet 执行 skill，预算上限 $10，日志 .claude/handoff/dispatcher/<ts>.log
#   ⑤ 会话 rc=0 ⇒ 把本轮列给它的事件里仍留在 events/ 的搬进 processed/（skill ⑨ 应已做，这里兜底防重复处理）；
#      rc≠0 ⇒ 事件原地留着，等下次唤醒／每日兜底。同一次调用最多跑 3 轮，吃掉会话期间新到的事件。
#
# ⛔ 本壳不 source .env、不读密钥、不把事件文件内容当命令（事件文件只用文件名）。
# 单测注入口（生产由 launchd 直接调用、不设）：
#   DISPATCHER_REPO    仓库根（默认 /Users/paulshao/Projects/HumanResource）
#   DISPATCHER_CLAUDE  claude 可执行文件（默认 PATH 里的 claude；测试指到假脚本）
#   DISPATCHER_MODEL   模型（默认 sonnet）    DISPATCHER_BUDGET  预算美元（默认 10）
#   DISPATCHER_MAX_ROUNDS 同次调用最多轮数（默认 3）
# ===========================================================================
set -uo pipefail

# 与 run-lanes.sh 同口径钉死 locale（macOS 自带 bash 3.2 在 UTF-8 下比较会静默出错）。
export LC_ALL=C
export LANG=C

REPO="${DISPATCHER_REPO:-/Users/paulshao/Projects/HumanResource}"
CLAUDE_BIN="${DISPATCHER_CLAUDE:-claude}"
MODEL="${DISPATCHER_MODEL:-sonnet}"
BUDGET="${DISPATCHER_BUDGET:-10}"
MAX_ROUNDS="${DISPATCHER_MAX_ROUNDS:-3}"

# ⛔ 不许留字面量 ~：这里是 shell，$HOME 会展开；plist 里那份由安装器渲染成绝对路径。
export PATH="$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"

HANDOFF="$REPO/.claude/handoff"
EVENTS="$HANDOFF/events"
PROCESSED="$EVENTS/processed"
LOGDIR="$HANDOFF/dispatcher"
LOCK="$HANDOFF/dispatcher.lock"

cd "$REPO" || { echo "✗ 无法 cd 到仓库根 $REPO" >&2; exit 1; }
mkdir -p "$EVENTS" "$PROCESSED" "$LOGDIR" || { echo "✗ 无法创建 $HANDOFF 下目录" >&2; exit 1; }

STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$LOGDIR/$STAMP.log"
log() { echo "[$(date +%Y-%m-%dT%H:%M:%S)] $*" | tee -a "$LOG"; }

# ---------------------------------------------------------------------------
# ② 单实例锁。锁文件内容 = 持锁 pid。pid 存活 ⇒ 另一个实例在跑，退出；
#    pid 已死（会话被 SIGKILL、机器重启）⇒ 孤儿锁，覆盖。⛔ 不用 flock：macOS 自带
#    bash 没有 flock 命令，而 pid 判据在两台机器上行为一致且可测。
# ---------------------------------------------------------------------------
if [[ -f "$LOCK" ]]; then
  holder="$(head -1 "$LOCK" 2>/dev/null | tr -cd '0-9')"
  if [[ -n "$holder" ]] && kill -0 "$holder" 2>/dev/null; then
    echo "[$(date +%Y-%m-%dT%H:%M:%S)] 另一个调度器实例 pid=$holder 在跑，退出" >> "$LOGDIR/launchd.log"
    exit 0
  fi
fi
echo "$$" > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

# ---------------------------------------------------------------------------
# ③ 该不该起会话。
#    pending_events：events/ 下的普通文件（不含 processed/ 子目录、不含隐藏文件）。
#    daily stamp：.claude/handoff/dispatcher/daily-<YYYYMMDD>，当日第一次调用（无论由谁触发）
#    都算兜底跑一次；此后当日再被 WatchPaths 触发而没有事件 ⇒ 静默退出。
# ---------------------------------------------------------------------------
pending_events() {
  find "$EVENTS" -mindepth 1 -maxdepth 1 -type f ! -name '.*' 2>/dev/null | sort
}

DAILY="$LOGDIR/daily-$(date +%Y%m%d)"
# 兜底戳一天一个，只留最近 7 天（只删本壳自己写的 daily-* 戳，⛔ 不碰会话日志）。
find "$LOGDIR" -maxdepth 1 -name 'daily-*' -mtime +7 -delete 2>/dev/null
events="$(pending_events)"
if [[ -z "$events" && -f "$DAILY" ]]; then
  echo "[$(date +%Y-%m-%dT%H:%M:%S)] 无未处理事件且当日兜底已跑，静默退出" >> "$LOGDIR/launchd.log"
  exit 0
fi

command -v "$CLAUDE_BIN" >/dev/null 2>&1 || [[ -x "$CLAUDE_BIN" ]] || {
  log "✗ 找不到 claude CLI（$CLAUDE_BIN；PATH=$PATH）"
  exit 10
}

# ---------------------------------------------------------------------------
# ④ 起会话。prompt 只含：事件文件名清单、无头引导、一句「用 Skill 工具调 task-dispatcher」。
#    ⛔ 不把事件文件内容拼进 prompt（run-lanes 写的是空文件；将来有内容也只当数据由 skill 读）。
# ---------------------------------------------------------------------------
build_prompt() {
  local ev_list="$1" trigger="$2"
  cat <<EOF
【无头执行引导】本 session 由 scripts/dispatcher_event.sh 无头启动（launchd com.zhuopin.hr.task-dispatcher），没有人在旁边。
① 无人在场，禁止提问。需 Shao Peishen 拍板的点（合规红线、候选人淘汰规则例外、候选人对外通道开关、真实简历处理范围、.51 发版、预算与外部采购）一律写进 docs/roadmap/定夺队列.md 后继续，⛔ 绝不默认生效、绝不替他拍。
② 本仓库此刻可能有别的泳道在并行跑：⛔ 不 git add -A / git add . / git commit -a / git stash；⛔ 不删 .git/index.lock。文档改动一律经提交请求通道（skill ⑦）。
③ 环境不可达时留步，不许假装闭合。
④ 收工输出 skill §3 六行报告，最后一行顶格 OPENER_DONE 或 OPENER_PARTIAL: <原因>。

[Mac]dispatcher-$STAMP
【设置】执行环境: CC ｜ Session: 新开（dispatcher_event.sh 无头起）｜ 分支: main ｜ worktree: ❌ 不勾（调度器只改文档并经提交通道提交）｜ 工作区: 仓库根 ｜ 派发: launchd·task-dispatcher

触发：$trigger
本轮事件文件（相对 .claude/handoff/events/，处理完按 skill ⑨ 移入 processed/）：
${ev_list:-（无——每日兜底：只刷新台账、推进状态、算 ready 集）}

请用 Skill 工具调用 \`task-dispatcher\`，按其 SKILL.md ① → ⑨ 逐步执行到底。单实例锁已由壳持有（DISPATCHER_LOCK_HELD=1）。
EOF
}

round=0
overall_rc=0
while :; do
  round=$((round + 1))
  events="$(pending_events)"
  ev_names=""
  if [[ -n "$events" ]]; then
    ev_names="$(printf '%s\n' "$events" | xargs -n1 basename)"
    trigger="事件（$(printf '%s\n' "$ev_names" | wc -l | tr -d ' ') 个）"
  else
    trigger="每日兜底（无事件文件）"
  fi
  : > "$DAILY"
  log "▶ 第 $round 轮：$trigger model=$MODEL budget=\$$BUDGET"
  [[ -n "$ev_names" ]] && printf '%s\n' "$ev_names" | sed 's/^/    事件: /' | tee -a "$LOG"

  # -p 模式默认只等后台子任务 600 秒；调度器不派子代理，但与 run-lanes 同口径放宽无害。
  build_prompt "$ev_names" "$trigger" | env DISPATCHER_LOCK_HELD=1 HR_HEADLESS_LANE=1 \
      CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=3600000 CLAUDE_CODE_SUBAGENT_MODEL="$MODEL" \
      "$CLAUDE_BIN" -p -n "[Mac]dispatcher-$STAMP" --output-format text \
      --model "$MODEL" --max-budget-usd "$BUDGET" --dangerously-skip-permissions --strict-mcp-config \
      >> "$LOG" 2>&1
  rc=$?
  log "■ 第 $round 轮结束 rc=$rc"

  if [[ $rc -eq 0 ]]; then
    # ⑤ 兜底归档：skill ⑨ 应已搬走；仍留着的（skill 漏搬）这里搬，防下次唤醒重复处理。
    while IFS= read -r ev; do
      [[ -n "$ev" && -f "$ev" ]] || continue
      mv "$ev" "$PROCESSED/" && log "  归档（壳兜底）：$(basename "$ev")"
    done <<< "$events"
  else
    overall_rc=$rc
    log "  ✗ 会话非 0 退出，事件原地留着等下次唤醒／每日兜底"
    break
  fi

  # 会话期间新到的事件：同次调用继续吃，最多 MAX_ROUNDS 轮；超出留给下一次 WatchPaths／兜底。
  [[ -n "$(pending_events)" ]] || break
  [[ $round -lt $MAX_ROUNDS ]] || { log "  达 $MAX_ROUNDS 轮上限，剩余事件留给下次唤醒"; break; }
done

log "日志：$LOG"
exit "$overall_rc"
