param(
    [string]$Root = $env:QQBOT_ROOT,
    [switch]$NoPanel
)

$ErrorActionPreference = 'Continue'
if (-not $Root) { $Root = $PSScriptRoot }
$logs = Join-Path $Root 'logs'
New-Item -ItemType Directory -Path $logs -Force | Out-Null
$env:PYTHONIOENCODING = 'utf-8'

function Test-Port($port) {
    [bool](Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
}

function Start-Child($exe, $argList, $workdir, $outLog, $errLog) {
    Start-Process -FilePath $exe -ArgumentList $argList -WorkingDirectory $workdir -RedirectStandardOutput $outLog -RedirectStandardError $errLog -WindowStyle Hidden
}

# 1) 协议端（SnowLuma）：没有就跳过，用 NapCat 的话自己开
$snow = Join-Path $Root 'SnowLuma'
if ((Test-Path (Join-Path $snow 'node.exe')) -and -not (Test-Port 5099)) {
    Write-Host '启动 SnowLuma ...'
    Start-Child (Join-Path $snow 'node.exe') 'index.mjs' $snow (Join-Path $logs 'snowluma.out.log') (Join-Path $logs 'snowluma.err.log')
    Start-Sleep -Seconds 8
} elseif (-not (Test-Port 5099)) {
    Write-Host '没有 SnowLuma（或没开），协议端请自己启动。' -ForegroundColor Yellow
} else {
    Write-Host 'SnowLuma 已在运行'
}

# 2) AstrBot
$ast = Join-Path $Root 'AstrBot'
$py = Join-Path $ast '.venv\Scripts\python.exe'
if ((Test-Path $py) -and -not (Test-Port 6185)) {
    Write-Host '启动 AstrBot ...'
    Start-Child $py 'main.py' $ast (Join-Path $logs 'astrbot.out.log') (Join-Path $logs 'astrbot.err.log')
    Start-Sleep -Seconds 12
} elseif (-not (Test-Path $py)) {
    Write-Host "找不到 AstrBot：$py" -ForegroundColor Red
} else {
    Write-Host 'AstrBot 已在运行'
}

# 3) GPT-SoVITS：跟着机器人一起起，不单独开机自启
$sov = Join-Path $Root 'start_sovits.ps1'
if ((Test-Path $sov) -and -not (Test-Port 9880)) {
    Write-Host '启动 GPT-SoVITS ...'
    $sovArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $sov)
    Start-Process -FilePath 'powershell.exe' -ArgumentList $sovArgs -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs 'sovits.out.log') -RedirectStandardError (Join-Path $logs 'sovits.err.log')
    Start-Sleep -Seconds 3
} elseif (-not (Test-Path $sov)) {
    Write-Host '没有 GPT-SoVITS，语音会回落 edge-tts。' -ForegroundColor Yellow
} else {
    Write-Host 'GPT-SoVITS 已在运行'
}

# 4) 控制台
if (-not $NoPanel) {
    $vbs = Join-Path $Root 'panel\panel.vbs'
    if (Test-Path $vbs) {
        Write-Host '启动控制台 ...'
        $vbsArg = '"{0}"' -f $vbs
        Start-Process 'wscript.exe' -ArgumentList $vbsArg -WindowStyle Hidden
    }
}

Write-Host ''
Write-Host 'AstrBot 面板:  http://127.0.0.1:6185'
Write-Host '控制台:       http://127.0.0.1:6868'
Write-Host "日志目录:      $logs"
