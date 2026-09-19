#!/usr/bin/env bash
#
# 把语音主机需要的代码同步到语音主机（voice-structured-interview U4，本计划
# 「设计决策 2」）。白名单包含两类：① voice_host/ 整个目录（语音主机自己的
# 代码）；② `.51` 仓库里被 voice_host/ 依赖、且必须与 `.51` 侧保持同源的
# 单文件契约（签名算法、schema、follow_up_selector、LLM 网关）。
#
# 用法：
#   ./sync-to-voice-host.sh                          # 用默认值
#   VOICE_HOST_SERVER=voicehost1 ./sync-to-voice-host.sh   # 用 ~/.ssh/config 里的别名
set -euo pipefail

export LANG="${LANG:-en_US.UTF-8}"
export LC_ALL="${LC_ALL:-en_US.UTF-8}"

VOICE_HOST_SERVER="${VOICE_HOST_SERVER:-voicehost1}"
REMOTE_APP_DIR="${REMOTE_APP_DIR:-/opt/zhuopin-voice-host}"

cd "$(dirname "$0")"

SYNC_PATHS=(
    "voice_host"
    "app/agents/follow_up_selector.py"
    "app/schemas/follow_up_result.py"
    "app/schemas/interview_ai_input.py"
    "app/schemas/session_bundle.py"
    "app/schemas/live_turn_event.py"
    "app/llm/gateway.py"
    "scripts/provision_voice_host.sh"
)

# 这些子目录即使落在白名单路径里也不推：本地 venv、CosyVoice 权重仓库、
# 运行时队列数据库、密钥文件。
EXCLUDES=(
    "--exclude=voice_host/.venv"
    "--exclude=voice_host/.venv-cosyvoice"
    "--exclude=voice_host/CosyVoice"
    "--exclude=voice_host/.env"
    "--exclude=voice_host_data"
    "--exclude=__pycache__"
)

rsync -avR "${EXCLUDES[@]}" "${SYNC_PATHS[@]}" "$VOICE_HOST_SERVER:$REMOTE_APP_DIR/"

echo "同步完成。语音主机侧需要手工重启服务使代码生效（见 provision_voice_host.sh 顶部说明，本脚本不代管进程生命周期）。"
