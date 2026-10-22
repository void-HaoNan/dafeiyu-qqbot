param(
    [string]$AstrBotRoot = 'D:\QQBot\AstrBot'
)

$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Src = Join-Path $Here 'astrbot-4.29.1'

if (-not (Test-Path -LiteralPath $AstrBotRoot)) {
    Write-Host "找不到 AstrBot 目录：$AstrBotRoot" -ForegroundColor Red
    exit 1
}

$Pyproject = Join-Path $AstrBotRoot 'pyproject.toml'
if (Test-Path -LiteralPath $Pyproject) {
    $Version = (Select-String -LiteralPath $Pyproject -Pattern '^version\s*=\s*"([^"]+)"' |
        Select-Object -First 1).Matches.Groups[1].Value
    if ($Version -and $Version -ne '4.29.1') {
        Write-Host "注意：当前 AstrBot 版本是 $Version，补丁是按 4.29.1 做的，可能对不上。" -ForegroundColor Yellow
    }
}

$Files = @(
    'astrbot\builtin_stars\astrbot\group_chat_context.py',
    'astrbot\core\pipeline\whitelist_check\stage.py'
)

foreach ($Rel in $Files) {
    $Target = Join-Path $AstrBotRoot $Rel
    $Source = Join-Path $Src $Rel
    if (-not (Test-Path -LiteralPath $Source)) {
        Write-Host "补丁文件缺失：$Source" -ForegroundColor Red
        exit 1
    }
    if (-not (Test-Path -LiteralPath $Target)) {
        Write-Host "目标文件不存在，跳过：$Target" -ForegroundColor Yellow
        continue
    }
    $Bak = "$Target.bak"
    if (-not (Test-Path -LiteralPath $Bak)) {
        Copy-Item -LiteralPath $Target -Destination $Bak
    }
    Copy-Item -LiteralPath $Source -Destination $Target -Force
    Write-Host "已打补丁：$Rel"
}

$FixStage = Join-Path $Here 'fix_waking_stage.py'
$Py = Join-Path $AstrBotRoot '.venv\Scripts\python.exe'
if ((Test-Path -LiteralPath $FixStage) -and (Test-Path -LiteralPath $Py)) {
    & $Py $FixStage --root $AstrBotRoot
} else {
    Write-Host '跳过空消息保护：找不到 venv python 或 fix_waking_stage.py' -ForegroundColor Yellow
}

Write-Host ''
Write-Host '完成。重启 AstrBot 生效。' -ForegroundColor Green
