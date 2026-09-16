#!/usr/bin/env bash
# wait-lanes.sh —— 看护者专用「阻塞等待」：一次调用在 shell 里等，直到有事才返回，只打印几行增量。
#
# 为什么有它（Token 治理 Phase 1，2026-09-16，[Mac]0916B）：
#   原看护正文写「每 3–5 分钟 cat results.tsv; ls -la *.log; ps」——每查一次就是模型一轮，
#   一批几小时下来上百轮，每轮都重读整段对话历史（P0 账本：看护 11 会话 837 次调用 $60，
#   0904Z 峰值上下文 352k）。等待本身不需要模型：交给 shell，模型只在「状态真的变了」时醒来判断。
#
# 用法（看护者在 CC 里循环调用，Bash 工具 timeout 设 600000）：
#   bash docs/openers/wait-lanes.sh --pid <run-lanes PID> [--logdir <lanes-目录>] [--max 540] [--interval 15]
#
# 返回时机与退出码：
#   0  CHANGE   results.tsv 新增了行（打印新增的 编号/状态/分钟）
#   0  ROUND    --chain 起了新一轮日志目录（PID 不变，exec 续跑）
#   0  EXITED   run-lanes 进程已退出（打印 summary.txt）——此时进入真身核验
#   3  HEARTBEAT 等满 --max 秒无变化（打印一行进度），看护者直接再调一次即可，⛔ 不要额外去查
#   2  用法错误 / 找不到日志目录
# 任何返回都可能附带 WARN 行：日志里出现 529 Overloaded、.git/index.lock 存在超过 10 分钟。
#
# 纯只读：不改任何文件、不杀任何进程。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." && pwd)"
HANDOFF="$REPO/.claude/handoff"
PID=""; LOGDIR=""; MAX=540; INTERVAL=15

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pid)      PID="$2"; shift 2 ;;
    --logdir)   LOGDIR="$2"; shift 2 ;;
    --max)      MAX="$2"; shift 2 ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    --handoff)  HANDOFF="$2"; shift 2 ;;   # 测试用
    --repo)     REPO="$2"; shift 2 ;;      # 测试用（查 index.lock）
    *) echo "USAGE 未知参数：$1"; exit 2 ;;
  esac
done
[[ -z "$PID" ]] && { echo "USAGE 必须给 --pid（run-lanes 的 PID，来自 .started 的 pid= 或启动时的 \$!）"; exit 2; }

newest_dir() {
  ls -td "$HANDOFF"/lanes-* 2>/dev/null | while read -r d; do [[ -d "$d" ]] && { echo "$d"; break; }; done
}
lines() { [[ -f "$1" ]] && wc -l < "$1" | tr -d ' ' || echo 0; }
mtime() {  # macOS 与 Linux 的 stat 参数互不兼容（Linux 的 -f 是查文件系统，会"成功"输出一堆文字）
  if [[ "$(uname)" == Darwin ]]; then stat -f %m "$1" 2>/dev/null || echo 0; else stat -c %Y "$1" 2>/dev/null || echo 0; fi
}
now() { date +%s; }
alive() { kill -0 "$PID" 2>/dev/null; }

[[ -z "$LOGDIR" ]] && LOGDIR="$(newest_dir)"
[[ -z "$LOGDIR" || ! -d "$LOGDIR" ]] && { echo "USAGE 找不到日志目录（$HANDOFF/lanes-*）"; exit 2; }
LOGDIR="$(cd "$LOGDIR" && pwd)"  # 归一化为绝对路径：newest_dir() 返回绝对路径，传相对路径进来会因字符串不等误判 ROUND

warns() {
  local hits
  hits="$(grep -l 'API Error: 529' "$LOGDIR"/*.log 2>/dev/null | xargs -n1 basename 2>/dev/null | tr '\n' ' ')"
  [[ -n "$hits" ]] && echo "WARN 529 Overloaded：$hits"
  local lock="$REPO/.git/index.lock"
  if [[ -f "$lock" ]] && (( $(now) - $(mtime "$lock") > 600 )); then
    echo "WARN .git/index.lock 已存在超过 10 分钟（⛔ 不删，报告里记）"
  fi
}
progress() {
  local total done_n kids age newest
  total="$(lines "$LOGDIR/manifest.tsv")"; done_n="$(lines "$LOGDIR/results.tsv")"
  kids="$(pgrep -f 'claude -p' 2>/dev/null | wc -l | tr -d ' ')"
  newest="$(ls -t "$LOGDIR"/*.log 2>/dev/null | head -1)"
  age="-"; [[ -n "$newest" ]] && age="$(( $(now) - $(mtime "$newest") ))s"
  echo "已完成 ${done_n}/${total} ｜ claude -p 子进程 ${kids} ｜ 最新日志 $(basename "${newest:-无}") ${age} 前更新 ｜ 目录 $(basename "$LOGDIR")"
}

start="$(now)"; n0="$(lines "$LOGDIR/results.tsv")"
while :; do
  d="$(newest_dir)"
  if [[ -n "$d" && "$d" != "$LOGDIR" ]]; then
    echo "$(date +%H:%M) ROUND 新一轮日志目录：$(basename "$d")（上一轮 $(basename "$LOGDIR") 共 $(lines "$LOGDIR/results.tsv") 条结果）"
    LOGDIR="$d"; warns; exit 0
  fi
  n="$(lines "$LOGDIR/results.tsv")"
  if (( n > n0 )); then
    echo "$(date +%H:%M) CHANGE 新增结果："
    tail -n "$(( n - n0 ))" "$LOGDIR/results.tsv" | awk -F'\t' '{printf "  %s %s %s分钟 （泳道 %s）\n", $2, $3, $4, $1}'
    progress; warns; exit 0
  fi
  if ! alive; then
    echo "$(date +%H:%M) EXITED run-lanes（PID ${PID}）已退出"
    [[ -f "$LOGDIR/summary.txt" ]] && head -40 "$LOGDIR/summary.txt" || echo "  ⚠️ 无 summary.txt（非正常退出，读 boot_log 末尾 30 行）"
    warns; exit 0
  fi
  if (( $(now) - start >= MAX )); then
    echo "$(date +%H:%M) HEARTBEAT $(progress)"
    warns; exit 3
  fi
  sleep "$INTERVAL"
done
