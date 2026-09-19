#!/usr/bin/env bash
#
# 语音主机幂等安装脚本（voice-structured-interview U4 tasks 5.1，design
# D12）。目标：Linux 云主机（具体规格由 docs/m3-voice-probe.md「目标机规格
# 建议」定，采购由 0.4 完成后填入）。
#
# 幂等：已安装的组件跳过重装（用可执行文件存在/版本匹配判断），可安全重跑。
# 无害预检：--precheck-only 只跑检查、不改动任何系统状态，用于在真正安装前
# 确认目标机满足前提（端口空闲/磁盘空间/Python 版本/网络可达 GitHub 与
# PyPI 镜像）。
#
# ⚠️ P2/P3（docs/m3-voice-probe.md）当前阻塞：FunASR 缺 30s 中文样本音频、
# CosyVoice 缺 hyperpyyaml 依赖。本脚本按已确认可行的安装路径实现，
# 实际执行仍需先解决这两处阻塞（本计划前置状态段已如实记录）。
set -euo pipefail

VOICE_HOST_DIR="$(cd "$(dirname "$0")/.." && pwd)/voice_host"
LIVEKIT_VERSION="${LIVEKIT_VERSION:-}"  # 留空 = 装最新 release，见 Step 3 说明
COSYVOICE_REPO_DIR="${COSYVOICE_REPO_DIR:-$VOICE_HOST_DIR/CosyVoice}"
PRECHECK_ONLY=0

for arg in "$@"; do
    case "$arg" in
        --precheck-only) PRECHECK_ONLY=1 ;;
        *) echo "未知参数: $arg" >&2; exit 2 ;;
    esac
done

log() { echo "[provision_voice_host] $*"; }

# ── 无害预检：任何一项不满足都在这里退出，不做任何安装动作 ──────────────
precheck() {
    local failed=0

    for port in 7880 7881 3478 8090; do
        if command -v ss >/dev/null 2>&1 && ss -ltn "( sport = :$port )" | grep -q "$port"; then
            echo "预检失败：端口 $port 已被占用" >&2
            failed=1
        fi
    done

    local free_kb
    free_kb=$(df -Pk "$VOICE_HOST_DIR/.." 2>/dev/null | tail -1 | awk '{print $4}')
    if [ -n "${free_kb:-}" ] && [ "$free_kb" -lt 20971520 ]; then  # 20GB
        echo "预检失败：可用磁盘空间不足 20GB（当前 ${free_kb}KB）" >&2
        failed=1
    fi

    if ! command -v python3.14 >/dev/null 2>&1 && ! command -v python3.12 >/dev/null 2>&1; then
        echo "预检失败：未找到 python3.14 或 python3.12（docs/m3-voice-probe.md P5 结论：优先 3.14，回落 3.12）" >&2
        failed=1
    fi

    if ! command -v python3.10 >/dev/null 2>&1 && ! command -v python3.12 >/dev/null 2>&1; then
        echo "预检失败：未找到 python3.10 或 python3.12（CosyVoice venv 需要，docs/m3-voice-probe-cosyvoice-install.md）" >&2
        failed=1
    fi

    if ! command -v git >/dev/null 2>&1; then
        echo "预检失败：未找到 git（CosyVoice 需要 git clone）" >&2
        failed=1
    fi

    if ! curl -sI --max-time 5 https://github.com >/dev/null 2>&1; then
        echo "预检失败：无法访问 https://github.com（CosyVoice 仓库克隆需要）" >&2
        failed=1
    fi

    if [ "$failed" -ne 0 ]; then
        echo "预检未通过，退出（无害——本次调用没有做任何安装动作）" >&2
        exit 1
    fi
    log "预检通过"
}

precheck
if [ "$PRECHECK_ONLY" -eq 1 ]; then
    log "--precheck-only：只跑预检，退出"
    exit 0
fi

# ── Step A: LiveKit server 二进制 ────────────────────────────────────
install_livekit_server() {
    if command -v livekit-server >/dev/null 2>&1; then
        log "livekit-server 已安装，跳过（$(livekit-server --version 2>&1 | head -1)）"
        return
    fi
    if [ -n "$LIVEKIT_VERSION" ]; then
        log "警告：LIVEKIT_VERSION=$LIVEKIT_VERSION 已设置，但官方安装脚本的版本锁定参数尚未验证，本次仍会安装最新版（TODO：确认 get.livekit.io 的版本锁定语法后补上）"
    else
        log "安装 livekit-server（官方安装脚本，未固定版本，将安装最新版）"
    fi
    curl -sSL https://get.livekit.io | bash
}

# ── Step B: 主 venv（FastAPI/livekit-agents/FunASR）────────────────────
install_main_venv() {
    local venv_dir="$VOICE_HOST_DIR/.venv"
    local python_bin
    python_bin="$(command -v python3.14 || command -v python3.12)"

    if [ -x "$venv_dir/bin/python" ]; then
        log "主 venv 已存在，跳过创建（$venv_dir）"
    else
        log "创建主 venv（$python_bin）"
        "$python_bin" -m venv "$venv_dir"
    fi
    "$venv_dir/bin/pip" install --upgrade pip
    "$venv_dir/bin/pip" install -r "$VOICE_HOST_DIR/requirements.txt"
}

# ── Step C: CosyVoice 独立 venv（本计划「设计决策 10」）─────────────────
install_cosyvoice_venv() {
    local venv_dir="$VOICE_HOST_DIR/.venv-cosyvoice"
    local python_bin
    python_bin="$(command -v python3.10 || command -v python3.12)"

    if [ ! -d "$COSYVOICE_REPO_DIR" ]; then
        log "克隆 CosyVoice 仓库"
        git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git "$COSYVOICE_REPO_DIR"
        (cd "$COSYVOICE_REPO_DIR" && git submodule update --init --recursive)
    else
        log "CosyVoice 仓库已存在，跳过克隆"
    fi

    if [ -x "$venv_dir/bin/python" ]; then
        log "CosyVoice venv 已存在，跳过创建（$venv_dir）"
    else
        log "创建 CosyVoice venv（$python_bin）"
        "$python_bin" -m venv "$venv_dir"
    fi

    # 已确认的真实阻塞修复（docs/m3-voice-probe-cosyvoice-install.md）：
    # grpcio==1.57.0 的 setup.py 硬依赖 pkg_resources，现代 pip 构建隔离不
    # 自动带它，必须先装旧版 setuptools 补回 pkg_resources。
    "$venv_dir/bin/pip" install -r "$VOICE_HOST_DIR/requirements-cosyvoice.txt"
    "$venv_dir/bin/pip" install -r "$COSYVOICE_REPO_DIR/requirements.txt" \
        -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host=mirrors.aliyun.com
}

# ── Step D: 环境变量模板（⛔ 不覆盖已存在的 .env，保护已配置的密钥）───────
write_env_template() {
    local env_file="$VOICE_HOST_DIR/.env"
    if [ -f "$env_file" ]; then
        log ".env 已存在，跳过（不覆盖已配置的密钥）"
        return
    fi
    log "写入 .env 模板（占位密钥，部署时必须手工替换）"
    cat > "$env_file" <<'EOF'
VOICE_HOST_SHARED_SECRET=REPLACE_ME_BEFORE_FIRST_START
VOICE_HOST_DATA_DIR=voice_host_data
VOICE_HOST_PORT=8090
EOF
    chmod 600 "$env_file"
}

install_livekit_server
install_main_venv
install_cosyvoice_venv
write_env_template

log "安装完成。启动前请确认 $VOICE_HOST_DIR/.env 里的 VOICE_HOST_SHARED_SECRET 已替换为真实密钥。"
