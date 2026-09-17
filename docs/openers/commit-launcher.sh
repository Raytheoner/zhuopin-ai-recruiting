#!/usr/bin/env bash
# commit-launcher.sh —— launchd WatchPaths 提交请求触发器的薄壳（2026-09-17，0917X）
# ===========================================================================
# 全部逻辑在 scripts/commit_request.py（设计、协议、白名单都写在它的头注释里）。
# 本壳只做三件事：找 python3、cd 到仓库根、exec 那个脚本。为什么不是纯 bash，
# 见 commit_request.py 头注释「为什么是 Python ＋ 薄壳」。
#
# 调用方：~/Library/LaunchAgents/com.zhuopin.hr.commit-launcher.plist
#         （由 scripts/install_commit_launcher.py 生成并 bootstrap，只由 Shao Peishen
#           本人在 Terminal 跑一次；起 LaunchAgent 属安全配置，Claude 不代做）
#
# Cowork 侧协议：写 .claude/handoff/commit/<时间戳>.request（JSON），等 <同名>.done /
# .rejected / .deferred。请求格式见 commit_request.py。
#
# ⛔ 本壳不 source 任何 .env、不读环境密钥、不把请求文件里的任何内容当命令。
# ===========================================================================
set -uo pipefail

# 仓库绝对路径。⛔ 不要改成相对 $0：launchd 起进程时 cwd 是 /。
# COMMIT_LAUNCHER_REPO 是单测注入口，生产由 launchd 直接调用、不设。
REPO="${COMMIT_LAUNCHER_REPO:-/Users/paulshao/Projects/HumanResource}"
export COMMIT_LAUNCHER_REPO="$REPO"
# Python 3.7+ 在 C locale 下自动进 UTF-8 模式；这里再钉一次，中文文件名不靠运气。
export PYTHONUTF8=1

# 脚本本体按**本壳所在仓库**定位（与 REPO 可能不同：单测里 REPO 是 tmp 仓库）。
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$SELF_DIR/../../scripts/commit_request.py"
[[ -f "$SCRIPT" ]] || { echo "✗ 找不到 $SCRIPT" >&2; exit 1; }

# 本仓库的 venv 叫 venv/（pre-commit 钩子 INSTALL_PYTHON 也指它），.venv/ 只是兜底。
# 用 venv 的 python 起，是为了 git commit 触发的 pre-commit 钩子能找到 pre_commit 模块。
PY=""
for cand in "$REPO/venv/bin/python" "$REPO/.venv/bin/python"; do
  [[ -x "$cand" ]] && { PY="$cand"; break; }
done
[[ -n "$PY" ]] || PY="$(command -v python3 || true)"
[[ -n "$PY" ]] || { echo "✗ 找不到 python3（launchd 的 PATH 见 install_commit_launcher.py）" >&2; exit 10; }

cd "$REPO" || { echo "✗ 无法 cd 到 $REPO" >&2; exit 1; }
# 2026-09-17 动作请求（.action）：先处理，失败不影响提交请求。见 scripts/action_request.py
[[ -f "$SELF_DIR/../../scripts/action_request.py" ]] && "$PY" "$SELF_DIR/../../scripts/action_request.py" || true
exec "$PY" "$SCRIPT"
