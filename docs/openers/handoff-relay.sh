#!/usr/bin/env bash
# handoff-relay.sh —— launchd WatchPaths 投递中继触发器的薄壳（2026-09-18，0918H）
# ===========================================================================
# 全部逻辑在 scripts/handoff_relay.py（设计、协议、白名单都写在它的头注释里）。
# 本壳只做三件事：找 python3、cd 到仓库根、exec 那个脚本。为什么是 Python + 薄壳
# 而不是纯 bash，见 commit_request.py 头注释「为什么是 Python ＋ 薄壳」同理。
#
# 调用方：~/Library/LaunchAgents/com.zhuopin.hr.handoff-relay.plist
#         （由 scripts/install_handoff_relay.py 生成并 bootstrap，只由 Shao Peishen
#           本人在 Terminal 跑一次；起 LaunchAgent 属安全配置，Claude 不代做）
#
# 受限会话侧协议：写仓库根 handoff-inbox/<前缀>-<时间戳>[-<来源>].<ext>，
# 本脚本把合格件搬进 .claude/handoff/**（去掉前缀），不合格的进 handoff-inbox/rejected/。
# 请求格式与前缀→通道对照表见 scripts/handoff_relay.py 头注释。
#
# ⛔ 本壳不 source 任何 .env、不读环境密钥、不把投递文件里的任何内容当命令。
# ===========================================================================
set -uo pipefail

# 仓库绝对路径。⛔ 不要改成相对 $0：launchd 起进程时 cwd 是 /。
# HANDOFF_RELAY_REPO 是单测注入口，生产由 launchd 直接调用、不设。
REPO="${HANDOFF_RELAY_REPO:-/Users/paulshao/Projects/HumanResource}"
export HANDOFF_RELAY_REPO="$REPO"
# Python 3.7+ 在 C locale 下自动进 UTF-8 模式；这里再钉一次，中文文件名不靠运气。
export PYTHONUTF8=1

# 脚本本体按**本壳所在仓库**定位（与 REPO 可能不同：单测里 REPO 是 tmp 仓库）。
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$SELF_DIR/../../scripts/handoff_relay.py"
[[ -f "$SCRIPT" ]] || { echo "✗ 找不到 $SCRIPT" >&2; exit 1; }

# 本仓库的 venv 叫 venv/，.venv/ 只是兜底。
PY=""
for cand in "$REPO/venv/bin/python" "$REPO/.venv/bin/python"; do
  [[ -x "$cand" ]] && { PY="$cand"; break; }
done
[[ -n "$PY" ]] || PY="$(command -v python3 || true)"
[[ -n "$PY" ]] || { echo "✗ 找不到 python3（launchd 的 PATH 见 install_handoff_relay.py）" >&2; exit 10; }

cd "$REPO" || { echo "✗ 无法 cd 到 $REPO" >&2; exit 1; }
exec "$PY" "$SCRIPT"
