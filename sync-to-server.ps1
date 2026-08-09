<#
.SYNOPSIS
  日常发版脚本 —— 从开发机把代码同步到 51 服务器并重启计划任务。
.DESCRIPTION
  不重建 venv、不重装依赖；requirements.txt 变更时需另外在服务器上手工
  重跑 deploy-server.ps1 的 venv/依赖安装部分（或直接重跑整个 deploy-server.ps1，
  它对已存在的 venv 是幂等的，只会跳过创建步骤）。
  依赖本机到目标服务器的 SSH 访问（scp/ssh 命令行工具，Windows 10/11 自带 OpenSSH 客户端）。
  自包含实现，不依赖「企业AI转型」仓库的 ZhuopinDeploy.psm1。
#>

param(
    [string]$ServerHost = "192.168.100.51",
    [string]$ServerUser = "Administrator",
    [string]$RemoteAppDir = "C:/apps/zhuopin-recruit-agent",
    [string]$LocalAppDir = ".",
    [string]$TaskName = "ZhuopinRecruitAgent"
)

$ErrorActionPreference = "Stop"

$remote = "$ServerUser@$ServerHost"

Write-Host "==> 推送代码到 $remote`:$RemoteAppDir"

# 不推送这些目录：.venv（服务器自己建）、data（运行时数据）、缓存、.git
$excludeNames = @(".venv", "data", "__pycache__", ".pytest_cache", ".git")

$itemsToCopy = Get-ChildItem -Path $LocalAppDir -Force |
    Where-Object { $excludeNames -notcontains $_.Name }

foreach ($item in $itemsToCopy) {
    Write-Host "    scp: $($item.Name)"
    if ($item.PSIsContainer) {
        scp -r $item.FullName "${remote}:${RemoteAppDir}/"
    } else {
        scp $item.FullName "${remote}:${RemoteAppDir}/"
    }
}

Write-Host "==> 远程重启计划任务: $TaskName"
# /end 在任务未运行时会报非零退出码，用 & 而不是 && 让 /run 无论如何都执行
ssh $remote "schtasks /end /tn `"$TaskName`" & schtasks /run /tn `"$TaskName`""

Write-Host "==> 等待服务重新监听"
Start-Sleep -Seconds 5

Write-Host "==> 远程健康检查"
ssh $remote "curl.exe -sS -o NUL -w `"HTTP %{http_code}`n`" --max-time 10 http://localhost:8095/hr/recruit-agent/"

Write-Host "==> 发版完成"
