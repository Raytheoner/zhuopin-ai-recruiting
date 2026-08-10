<#
.SYNOPSIS
  卓品智能招聘助手 Demo 首次部署脚本 —— Windows venv + 计划任务，无 Docker（部署约束4）。
.DESCRIPTION
  在目标服务器 192.168.100.51 上、以管理员身份运行（RDP 登录后手工执行一次）：
    1. 若不存在则创建 Python venv
    2. 安装 requirements.txt 依赖
    3. 注册 Windows 计划任务：SYSTEM 账户 + AtStartup 触发 + 失败重启 3 次
    4. 开放防火墙入站规则，放行 8095
  代码需先用 sync-to-server.ps1（或手工方式）放到 $AppDir 下，再运行本脚本。
  自包含实现，不依赖「企业AI转型」仓库的 ZhuopinDeploy.psm1。
#>

param(
    [string]$AppDir = "C:\apps\zhuopin-recruit-agent",
    [string]$PythonExe = "python",
    [int]$Port = 8095,
    [string]$TaskName = "ZhuopinRecruitAgent",
    [string]$FirewallRuleName = "ZhuopinRecruitAgent-Inbound-8095"
)

$ErrorActionPreference = "Stop"

Write-Host "==> 部署目录: $AppDir"
if (-not (Test-Path $AppDir)) {
    throw "部署目录 $AppDir 不存在。请先用 sync-to-server.ps1 把代码放到这里，再运行本脚本。"
}

Set-Location $AppDir

$venvPath = Join-Path $AppDir ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$venvUvicorn = Join-Path $venvPath "Scripts\uvicorn.exe"

Write-Host "==> 检查 venv: $venvPath"
if (-not (Test-Path $venvPython)) {
    Write-Host "    venv 不存在，创建中..."
    & $PythonExe -m venv $venvPath
} else {
    Write-Host "    venv 已存在，跳过创建"
}

Write-Host "==> 安装依赖"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r (Join-Path $AppDir "requirements.txt")

Write-Host "==> 检查 .env"
$envFile = Join-Path $AppDir ".env"
if (-not (Test-Path $envFile)) {
    Write-Warning ".env 不存在！请在 $envFile 手工创建（参考 .env.example），填入真实 LLM_API_KEY 后再启动服务。"
}

Write-Host "==> 注册 Windows 计划任务: $TaskName"
$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Write-Host "    任务已存在，先移除旧定义"
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$action = New-ScheduledTaskAction `
    -Execute $venvUvicorn `
    -Argument "app.main:app --host 0.0.0.0 --port $Port" `
    -WorkingDirectory $AppDir

$trigger = New-ScheduledTaskTrigger -AtStartup

$principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "卓品智能招聘助手 Demo（M1 第0章），FastAPI+uvicorn，监听 $Port" | Out-Null

Write-Host "==> 开放防火墙规则: $FirewallRuleName (TCP $Port)"
$existingRule = Get-NetFirewallRule -DisplayName $FirewallRuleName -ErrorAction SilentlyContinue
if ($existingRule) {
    Write-Host "    规则已存在，跳过"
} else {
    New-NetFirewallRule `
        -DisplayName $FirewallRuleName `
        -Direction Inbound `
        -Protocol TCP `
        -LocalPort $Port `
        -Action Allow | Out-Null
}

Write-Host "==> 启动计划任务"
Start-ScheduledTask -TaskName $TaskName

Start-Sleep -Seconds 3
$taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host "==> 计划任务状态: LastTaskResult=$($taskInfo.LastTaskResult)（0 = 成功启动）"
Write-Host "==> 部署完成。验证: curl.exe http://localhost:$Port/hr/recruit-agent/"
