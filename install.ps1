param(
    [string]$Root = 'D:\QQBot',
    [string]$AstrBotRoot = '',
    [switch]$ApplyPatches,
    [switch]$InstallBackupTask
)

$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not $AstrBotRoot) { $AstrBotRoot = Join-Path $Root 'AstrBot' }
$Plugins = Join-Path $AstrBotRoot 'data\plugins'

if (-not (Test-Path -LiteralPath $Plugins)) {
    Write-Host "找不到 AstrBot 的插件目录：$Plugins" -ForegroundColor Red
    Write-Host '先把 AstrBot 装好（默认放到 <Root>\AstrBot），或用 -AstrBotRoot 指定。' -ForegroundColor Yellow
    exit 1
}

$pyproject = Join-Path $AstrBotRoot 'pyproject.toml'
if (Test-Path -LiteralPath $pyproject) {
    $ver = (Select-String -LiteralPath $pyproject -Pattern '^version\s*=\s*"([^"]+)"' |
        Select-Object -First 1).Matches.Groups[1].Value
    if ($ver) {
        Write-Host "AstrBot 版本：$ver"
        if ($ver -notlike '4.29.*') {
            Write-Host '注意：补丁按 AstrBot 4.29.1 做的，其他版本可能对不上。' -ForegroundColor Yellow
        }
    }
}

Write-Host '复制插件 ...'
$dst = Join-Path $Plugins 'astrbot_plugin_dafeiyu'
if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Recurse -Force }
Copy-Item -LiteralPath (Join-Path $Repo 'engines\v2\plugin') -Destination $dst -Recurse -Force

Write-Host '复制控制台、脚本、人设 ...'
foreach ($name in @('panel', 'scripts', 'personas', 'patches')) {
    $src = Join-Path $Repo $name
    if (-not (Test-Path -LiteralPath $src)) { continue }
    $out = Join-Path $Root $name
    New-Item -ItemType Directory -Path $out -Force | Out-Null
    Copy-Item -Path (Join-Path $src '*') -Destination $out -Recurse -Force
}
foreach ($name in @('start_sovits.ps1', 'backup.ps1', 'backup.vbs')) {
    $src = Join-Path $Repo $name
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $Root $name) -Force
    }
}

foreach ($d in @('data\dafeiyu', 'logs', 'stickers', 'datasets', 'rvc\models', 'bin', 'backups')) {
    New-Item -ItemType Directory -Path (Join-Path $Root $d) -Force | Out-Null
}

$conf = Join-Path $Root 'data\dafeiyu\config.json'
if (-not (Test-Path -LiteralPath $conf)) {
    Copy-Item -LiteralPath (Join-Path $Repo 'config-examples\dafeiyu.config.example.json') -Destination $conf
    Write-Host "已生成配置：$conf，记得填 QQ 号" -ForegroundColor Yellow
}
$access = Join-Path $Root 'data\access_lists.json'
if (-not (Test-Path -LiteralPath $access)) {
    Copy-Item -LiteralPath (Join-Path $Repo 'config-examples\access_lists.json') -Destination $access
}

if ($ApplyPatches) {
    & (Join-Path $Repo 'patches\apply_core_patches.ps1') -AstrBotRoot $AstrBotRoot
}

if ($InstallBackupTask) {
    try {
        $action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('//B "{0}"' -f (Join-Path $Root 'backup.vbs'))
        $trigger = New-ScheduledTaskTrigger -Daily -At '03:00'
        Register-ScheduledTask -TaskName 'QQBot-DailyBackup' -Action $action -Trigger $trigger -Description '大肥鱼每日备份' -Force | Out-Null
        Write-Host '已设置每天 03:00 自动备份' -ForegroundColor Green
    } catch {
        Write-Host "计划任务创建失败：$($_.Exception.Message)" -ForegroundColor Yellow
        Write-Host '用管理员 PowerShell 再跑一次，或在任务计划程序里手动建每天 03:00 的 backup.vbs。' -ForegroundColor Yellow
    }
}

Write-Host ''
Write-Host '完成。下一步：' -ForegroundColor Green
Write-Host "  1. 填配置：$conf（管理员 QQ、机器人 QQ、主群号）"
Write-Host '  2. 在 AstrBot 6185 配模型 key（可参考 config-examples\cmd_config.snippet.json）'
Write-Host '  3. 启动一次 AstrBot，再跑：'
Write-Host "     $AstrBotRoot\.venv\Scripts\python.exe $Root\scripts\import_persona.py"
Write-Host '  4. 跑体检：'
Write-Host "     $Root\AstrBot\.venv\Scripts\python.exe $Root\scripts\doctor.py"
Write-Host '  5. 按 docs\V2_TEST.md 人工验收'
Write-Host '  6. 要每天自动备份，重跑安装时加 -InstallBackupTask'
