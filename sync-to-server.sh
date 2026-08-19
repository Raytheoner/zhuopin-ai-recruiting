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

# 2026-08-11 修复：本机 shell 默认 locale 是 "C"（非 UTF-8）。旧版脚本用
# `for item in *` 遍历仓库根目录时，会把中文文件名（如 03-工具链协作规则.md）
# 原样传给 scp——在 "C" locale 下这些多字节文件名被静默改写成八进制转义，
# 远端收到的是一串乱码路径，scp 直接报 "No such file or directory" 失败，
# 且是在中途失败，此前已经 scp 过去的文件不会回滚。显式切到 UTF-8，不依赖
# 调用者的 shell 环境是否已经配好。
export LANG="${LANG:-en_US.UTF-8}"
export LC_ALL="${LC_ALL:-en_US.UTF-8}"

SERVER="${SERVER:-zp51}"
REMOTE_APP_DIR="${REMOTE_APP_DIR:-C:/apps/zhuopin-recruit-agent}"
TASK_NAME="${TASK_NAME:-ZhuopinRecruitAgent}"
HEALTH_URL="${HEALTH_URL:-http://localhost:8095/hr/recruit-agent/}"

cd "$(dirname "$0")"

# 白名单而不是黑名单：服务器只需要真正跑起来这个 app 所需的东西。
# 2026-08-11 review 发现旧的黑名单实现（遍历仓库根目录、排除少数几个名字）
# 会把仓库根目录下的调研/架构/协作规则这些中文文件名的策略文档、.claude/、
# openspec/、.agents 之类统统推到生产服务器——不仅没必要（app 运行时不读
# 这些），中文文件名还正是触发上面那个 locale 编码 bug 的原因。改成白名单
# 后 .env 也不再需要运行时判断跳过：它本来就从没出现在这份列表里，结构上
# 不可能被同步（服务器 .env 是独立维护的生产凭据，按 docs/deploy-51-server.md
# 单独维护）。
SYNC_PATHS=(
    "app"
    "scripts"
    "requirements.txt"
    "pyproject.toml"
    ".env.example"
    "deploy-server.ps1"
    "sync-to-server.sh"
)

# 即使在上面的白名单路径里，这些子目录也不推：.venv/venv（服务器自己建）、
# data（运行时数据）、缓存。
# logs 与 data 同属运行时数据：生产日志含个人信息，被反向同步回开发机
# 会构成一次未经授权的个人信息转移（server-runtime-logging design Risks）。
# 它本来就不在 SYNC_PATHS 白名单里，这里再写一遍是为了让「不同步」这件事
# 有一个显式的、可被测试断言的落点，而不是依赖「现在没有就永远没有」。
EXCLUDE_NAMES=(".venv" "venv" "data" "logs" "__pycache__" ".pytest_cache")

echo "==> 推送代码到 ${SERVER}:${REMOTE_APP_DIR}"

for item in "${SYNC_PATHS[@]}"; do
    [[ -e "$item" ]] || { echo "    (跳过，不存在: ${item})" >&2; continue; }

    name="$(basename "$item")"
    skip=0
    for ex in "${EXCLUDE_NAMES[@]}"; do
        if [[ "$name" == "$ex" ]]; then skip=1; break; fi
    done
    [[ $skip -eq 1 ]] && continue

    echo "    scp: ${item}"
    if [[ -d "$item" ]]; then
        scp -q -r "$item" "${SERVER}:${REMOTE_APP_DIR}/"
    else
        scp -q "$item" "${SERVER}:${REMOTE_APP_DIR}/"
    fi
done

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
