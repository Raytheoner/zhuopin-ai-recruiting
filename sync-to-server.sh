#!/usr/bin/env bash
#
# 日常发版脚本 —— 从 macOS/Linux 开发机把代码同步到 51 服务器并重启计划任务。
#
# 不重建 venv、不重装依赖；requirements.txt 变更时需另外 RDP 登录服务器重跑
# deploy-server.ps1（它对已存在的 venv 是幂等的，只会重新 pip install）。
#
# 依赖：本机到目标服务器的 SSH 免密访问（配置见 05-发布运行手册.md 阶段 A）。
# 自包含实现，不依赖「企业AI转型」仓库的 ZhuopinDeploy.psm1。
#
# 用法：
#   ./sync-to-server.sh                    # 用默认值
#   SERVER=zp51 ./sync-to-server.sh        # 用 ~/.ssh/config 里的别名
#   SERVER=Administrator@192.168.100.51 ./sync-to-server.sh

set -euo pipefail

SERVER="${SERVER:-zp51}"
REMOTE_APP_DIR="${REMOTE_APP_DIR:-C:/apps/zhuopin-recruit-agent}"
TASK_NAME="${TASK_NAME:-ZhuopinRecruitAgent}"
HEALTH_URL="${HEALTH_URL:-http://localhost:8095/hr/recruit-agent/}"

cd "$(dirname "$0")"

# 不推送：.venv（服务器自己建）、data（运行时数据）、缓存、.git、脚本自身无所谓但保持整洁
EXCLUDES=(".venv" "venv" "data" "__pycache__" ".pytest_cache" ".git" ".claude" ".superpowers")

echo "==> 推送代码到 ${SERVER}:${REMOTE_APP_DIR}"

shopt -s dotglob nullglob
for item in *; do
    name="$(basename "$item")"

    skip=0
    for ex in "${EXCLUDES[@]}"; do
        if [[ "$name" == "$ex" ]]; then skip=1; break; fi
    done
    [[ $skip -eq 1 ]] && continue

    # .env 及其变体绝不能从开发机同步：服务器的 .env 是独立维护的生产凭据
    # （真实 LLM_API_KEY、锁定的模型版本，工程铁律5），会被开发机本地 .env
    # 静默覆盖且没有任何提示。.env.example 是例外——那是随代码分发的占位模板，
    # 不含真实凭据。服务器 .env 按 docs/deploy-51-server.md 单独维护。
    if [[ "$name" == .env* && "$name" != ".env.example" ]]; then
        echo "    ⚠️  跳过 ${name}：服务器 .env 是独立维护的生产配置，不从开发机同步" >&2
        continue
    fi

    echo "    scp: ${name}"
    if [[ -d "$item" ]]; then
        scp -q -r "$item" "${SERVER}:${REMOTE_APP_DIR}/"
    else
        scp -q "$item" "${SERVER}:${REMOTE_APP_DIR}/"
    fi
done
shopt -u dotglob nullglob

echo "==> 远程重启计划任务: ${TASK_NAME}"
# /end 在任务未运行时会返回非零退出码，用 & 而不是 && 让 /run 无论如何都执行。
# 远端是 Windows，命令由 cmd 解释。
ssh "$SERVER" "schtasks /end /tn \"${TASK_NAME}\" & schtasks /run /tn \"${TASK_NAME}\""

echo "==> 等待服务重新监听"
sleep 5

echo "==> 远程健康检查"
ssh "$SERVER" "curl.exe -sS -o NUL -w \"HTTP %{http_code}\" --max-time 10 ${HEALTH_URL}"
echo

echo "==> 发版完成"
