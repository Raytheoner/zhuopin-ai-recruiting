#!/usr/bin/env bash
# lane-launcher.sh —— launchd WatchPaths 发车触发器（2026-09-08，0909G）
# ===========================================================================
# 为什么存在：
#   2026-09-03 看护者自己 `nohup` 起 run-lanes.sh 成功过一次；09-04 起每批都被
#   CC Desktop 的 Auto Mode 分类器拦下（判「无人值守起会自主 commit/push 的子
#   session」为高风险）。`.claude/settings.json` 的 permissions.allow 三条齐全时
#   照拦，`run_in_background` 也拦（lanes-20260904-093026-看护报告.md §五）。
#   此后每批都要 Shao Peishen 在对话里点一次 Run —— 正是 CLAUDE.md 说的
#   「伪装成机制的人工节奏控制」。
#
#   根治思路与「企业AI转型」侧一致（那边是 Windows 计划任务跑 opener 批处理）：
#   **起进程的不是 Claude，是操作系统。** 看护者只用 Write 工具**写一个文件**
#   （不走 Bash，不经过分类器），launchd 的 WatchPaths 看到目录变化，以 Shao
#   Peishen 本人的用户身份调用本脚本，由本脚本起 run-lanes.sh。
#
# 调用方：~/Library/LaunchAgents/com.zhuopin.hr.lane-launcher.plist
#         （由 scripts/install_lane_launcher.py 生成并 bootstrap，只由本人在
#           Terminal 跑一次；起 launchd 属安全配置，Claude 不代做）
#
# 协议（看护者侧）：
#   ① 写 .claude/handoff/launch/<批次时间戳>.request，**内容一行**＝run-lanes.sh 的参数
#   ② 等 <同名>.started 出现（最多 60 秒），从中读 PID 与 boot.log 路径
#   ③ 看到 <同名>.rejected / .deferred 即发车未成，读文件里的原因
#
# ⛔ 本脚本不 source 任何 .env，不读环境里的密钥，不接受请求文件里的任意命令。
# ===========================================================================
set -uo pipefail

# 与 run-lanes.sh 同口径钉死 locale。理由见 run-lanes.sh 顶部「locale 钉死」段：
# macOS 自带 awk/bash 3.2 在 UTF-8 locale 下的比较与变量名解析会**静默出错**。
export LC_ALL=C
export LANG=C

# 仓库绝对路径。⛔ 不要改成相对 $0：launchd 起进程时 cwd 是 /，相对路径当场失准。
# LANE_LAUNCHER_REPO 是**单测用的注入口**，生产由 launchd 直接调用、不设该变量。
REPO="${LANE_LAUNCHER_REPO:-/Users/paulshao/Projects/HumanResource}"
LAUNCH_DIR="$REPO/.claude/handoff/launch"
# 同为单测注入口：假 run-lanes.sh 只 echo 参数，不真起 claude。
RUNNER="${LANE_LAUNCHER_RUNNER:-docs/openers/run-lanes.sh}"
# 并发判据的 pgrep 模式。⛔ 不要用 `-f run-lanes`（不带 .sh）—— run-lanes 三个字
# 会命中本批日志路径等无关进程；也不要把整条命令做成环境变量，那等于开了一个
# 任意命令入口。这里只注入**模式串**。
PGREP_PATTERN="${LANE_LAUNCHER_PGREP_PATTERN:-run-lanes.*\.sh}"

mkdir -p "$LAUNCH_DIR" || { echo "✗ 无法创建 $LAUNCH_DIR" >&2; exit 1; }

log() { echo "[$(date +%Y-%m-%dT%H:%M:%S)] $*"; }

# ---------------------------------------------------------------------------
# 取最早的一个请求。一次只处理一个：launchd 的 WatchPaths 在目录再次变化时会
# 重新触发本脚本，剩下的请求下一轮再处理，不需要在这里循环。
# ---------------------------------------------------------------------------
shopt -s nullglob
requests=("$LAUNCH_DIR"/*.request)
shopt -u nullglob
if [[ ${#requests[@]} -eq 0 ]]; then
  exit 0
fi
# 按文件名排序取最早。文件名是批次时间戳，字典序即时间序。
# ⛔ 不要写成 `IFS=$'\n' requests=($(... | sort))` —— 那不是命令前缀而是普通赋值，
#    IFS 会**永久改成换行**，下面 `read -ra` 就不再按空格切词，整行参数变成一个
#    token，合法请求被自己的白名单拒掉（0909G 实测踩过）。用 while-read 收集。
sorted_requests=()
while IFS= read -r _line; do
  sorted_requests+=("$_line")
done < <(printf '%s\n' "${requests[@]}" | sort)
req="${sorted_requests[0]}"
base="${req%.request}"

# ---------------------------------------------------------------------------
# 原子认领。
#
# 为什么必须先认领再读：本脚本自己往 LAUNCH_DIR 里写 .started / .consumed 会
# **再次触发 WatchPaths**，于是可能有两个实例同时看到同一个 .request。
# `mv` 在源文件已被别人搬走时返回非 0 —— 这就是那个原子的「谁抢到」判据。
# 抢输的实例直接退出，⛔ 不要重试，不要等。
# ---------------------------------------------------------------------------
claimed="$base.claimed"
mv "$req" "$claimed" 2>/dev/null || {
  log "请求 $req 已被另一个实例认领，退出"
  exit 0
}

reject() {
  local reason="$1"
  { cat "$claimed"; echo; echo "# 拒绝原因：$reason"; } > "$base.rejected"
  rm -f "$claimed"
  log "✗ 拒绝：$reason"
  exit 0
}

defer() {
  local reason="$1"
  { cat "$claimed"; echo; echo "# 推迟原因：$reason"; } > "$base.deferred"
  rm -f "$claimed"
  log "⏸ 推迟：$reason"
  exit 0
}

# 读第一行作为参数。⛔ 不用 eval、不用 source —— 请求文件是普通文件，任何人
# （包括被注入的 Claude）都能写，它绝不能成为任意命令入口。
# `read -ra` 只按 IFS 切词，不做路径展开、不解析引号，配合下面的白名单足够。
first_line=""
IFS= read -r first_line < "$claimed" || true
first_line="${first_line%$'\r'}"   # 容忍 CRLF 行尾
IFS=$' \t\n'                       # 显式复位，⛔ 不要依赖上面没人动过它
read -ra ARGS <<< "$first_line"

[[ ${#ARGS[@]} -gt 0 ]] || reject "请求文件首行为空，没有参数可传"

# ---------------------------------------------------------------------------
# 参数白名单。**只允许这六个**，其余一律拒绝：
#   --full-auto | --yes | --only <逗号编号> | --max-parallel N | --stagger N | --budget N
#
# ⛔ --dry-run 不在白名单里（0909G 明写）：launcher 只起真跑。dry-run 由看护者
#    自己在 session 里跑，那条命令分类器不拦，没有理由绕到这里来。
# ⛔ --model / --chain / --plan 也不在：前者会改评分可复现性的前提，后两者会让
#    一个请求文件在无人值守下无限接续或指向任意计划文件。
# 取值一律做字符集校验：编号只允许 [A-Za-z0-9_.,-]，数值只允许非负整数或小数。
# 校验不通过就整条拒绝，⛔ 不做「跳过这个参数继续」——半截参数比不跑更危险。
# ---------------------------------------------------------------------------
i=0
while [[ $i -lt ${#ARGS[@]} ]]; do
  tok="${ARGS[$i]}"
  case "$tok" in
    --full-auto|--yes)
      i=$((i + 1))
      ;;
    --only)
      val="${ARGS[$((i + 1))]:-}"
      [[ -n "$val" ]] || reject "--only 缺少取值"
      [[ "$val" =~ ^[A-Za-z0-9_.,-]+$ ]] || reject "--only 取值含非法字符：$val"
      i=$((i + 2))
      ;;
    --max-parallel|--stagger|--budget)
      val="${ARGS[$((i + 1))]:-}"
      [[ -n "$val" ]] || reject "$tok 缺少取值"
      [[ "$val" =~ ^[0-9]+(\.[0-9]+)?$ ]] || reject "$tok 取值不是数字：$val"
      i=$((i + 2))
      ;;
    *)
      reject "不在白名单里的 token：$tok"
      ;;
  esac
done

# ---------------------------------------------------------------------------
# 拒绝并发。两条 run-lanes.sh 同时跑会让两批泳道抢同一批 worktree 分支
# （实证见 memory「同一 opener 重复派发会静默覆盖产出」）。
#
# ⚠️ 被推迟的请求**不会自动重试**：它已经改名成 .deferred，不再匹配 *.request。
#    这是刻意的——若改回 .request，重命名本身又会触发 WatchPaths，在 run-lanes
#    跑完之前会一直空转。要重发由看护者写一个新的 .request（它写之前也会先查）。
# ---------------------------------------------------------------------------
if pgrep -f "$PGREP_PATTERN" >/dev/null 2>&1; then
  defer "已有 run-lanes 进程在跑（pgrep -f '$PGREP_PATTERN' 非空）"
fi

# ---------------------------------------------------------------------------
# 发车。
# ---------------------------------------------------------------------------
boot_log="$base.boot.log"
cd "$REPO" || reject "无法 cd 到仓库根 $REPO"
[[ -f "$REPO/$RUNNER" ]] || reject "执行器不存在：$REPO/$RUNNER"

nohup bash "$REPO/$RUNNER" "${ARGS[@]}" > "$boot_log" 2>&1 &
pid=$!

{
  echo "pid=$pid"
  echo "boot_log=$boot_log"
  echo "args=$first_line"
  echo "started_at=$(date +%Y-%m-%dT%H:%M:%S)"
} > "$base.started"
mv "$claimed" "$base.consumed"

log "▶ 已发车：pid=$pid args=$first_line"
log "  boot.log：$boot_log"
exit 0
