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

Write-Host "==> 准备日志目录"
# 幂等：目录已存在则跳过创建、不清空内容（沿用本脚本既有约定）。
# 但下面的 ACL 设置不受这个跳过影响，每次运行都会重新应用——这是有意为之的
# 自愈：如果有人手动收回了 SYSTEM 的写权限，重跑本脚本应该能把它找回来，而
# 不是把"目录已存在"当成"权限也一定还对"。计划任务以 SYSTEM 账户运行，日志
# 目录必须 SYSTEM 可写，否则应用启动时会降级为仅 stdout——而计划任务没有
# 控制台，等于回到零日志。
$logDir = Join-Path $AppDir "logs"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
    Write-Host "    已创建: $logDir"
} else {
    Write-Host "    已存在，跳过创建: $logDir"
}

$acl = Get-Acl $logDir
$systemRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    "SYSTEM", "Modify",
    "ContainerInherit,ObjectInherit", "None", "Allow")
$acl.SetAccessRule($systemRule)
Set-Acl -Path $logDir -AclObject $acl

# 只验证不假设：写一个探针文件确认真的可写，失败就当场报错而不是等到运行时静默降级。
$probe = Join-Path $logDir ".deploy-write-probe"
try {
    Set-Content -Path $probe -Value "" -ErrorAction Stop
    Remove-Item $probe -ErrorAction SilentlyContinue
    Write-Host "    可写性验证通过"
} catch {
    throw "日志目录 $logDir 不可写：$_。计划任务以 SYSTEM 运行，请检查该目录的 ACL。"
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
    Write-Host "    规则已存在，确保 Profile 覆盖 Any"
    Set-NetFirewallRule -DisplayName $FirewallRuleName -Profile Any | Out-Null
} else {
    New-NetFirewallRule `
        -DisplayName $FirewallRuleName `
        -Direction Inbound `
        -Protocol TCP `
        -LocalPort $Port `
        -Profile Any `
        -Action Allow | Out-Null
}

Write-Host "==> 启动计划任务"
Start-ScheduledTask -TaskName $TaskName

Start-Sleep -Seconds 3
$taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host "==> 计划任务状态: LastTaskResult=$($taskInfo.LastTaskResult)（0 = 成功启动）"
Write-Host "==> 部署完成。验证:"
Write-Host "    curl.exe http://localhost:$Port/hr/recruit-agent/"
Write-Host "    curl.exe http://localhost:$Port/hr/recruit-agent/health   # status 应为 ok，degraded 表示日志没落盘"
Write-Host "    Get-Content (Join-Path $AppDir logs\app.log) -Tail 20"
