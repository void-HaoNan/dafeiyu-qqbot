# 备份机器人需要的东西：AstrBot 数据、大肥鱼数据、协议端配置、音色状态、人设和密钥
# 每天 03:00 由 QQBot-DailyBackup 计划任务调用，也可以在控制台点「立即备份」
param(
    [string]$Root = ''
)

$ErrorActionPreference = 'Stop'

if (-not $Root) {
    $Root = Split-Path -Parent $MyInvocation.MyCommand.Path
}
$dest = Join-Path $Root 'backups'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$staging = Join-Path $env:TEMP "qqbot-backup-$stamp"
$log = Join-Path $Root 'logs\backup.log'
New-Item -ItemType Directory -Force -Path $dest, $staging, (Split-Path -Parent $log) | Out-Null

function Write-Log([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -LiteralPath $log -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
}

Write-Log "开始备份 -> $dest"

$targets = @(
    @{ Path = (Join-Path $Root 'AstrBot\data');       Name = 'astrbot-data';             File = $false },
    @{ Path = (Join-Path $Root 'data');               Name = 'data';                     File = $false },
    @{ Path = (Join-Path $Root 'SnowLuma\config');    Name = 'snowluma-config';          File = $false },
    @{ Path = (Join-Path $Root 'secrets');            Name = 'secrets';                  File = $false },
    @{ Path = (Join-Path $Root 'personas');           Name = 'personas';                 File = $false },
    @{ Path = (Join-Path $Root 'panel\model_state.json'); Name = 'panel\model_state.json'; File = $true },
    @{ Path = (Join-Path $Root 'panel\rvc_state.json');   Name = 'panel\rvc_state.json';   File = $true },
    @{ Path = (Join-Path $Root 'rvc\model_map.json');     Name = 'rvc\model_map.json';     File = $true }
)

try {
    foreach ($t in $targets) {
        if (-not (Test-Path -LiteralPath $t.Path)) { continue }
        $out = Join-Path $staging $t.Name
        $parent = Split-Path -Parent $out
        if (-not (Test-Path -LiteralPath $parent)) {
            New-Item -ItemType Directory -Force -Path $parent | Out-Null
        }
        if ($t.File) {
            Copy-Item -LiteralPath $t.Path -Destination $out -Force
        } else {
            Copy-Item -LiteralPath $t.Path -Destination $out -Recurse -Force
        }
    }

    # 可再生的缓存和临时文件不进备份
    Get-ChildItem $staging -Recurse -Directory -Filter 'cache' -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem $staging -Recurse -Directory -Filter 'temp' -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

    $info = @(
        '大肥鱼备份',
        ('时间: ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')),
        ('来源: ' + $Root),
        '包含: AstrBot 数据、大肥鱼数据、SnowLuma 配置、密钥、人设、音色状态、RVC 映射',
        '恢复: 解压后把各目录覆盖回同名位置；先停 AstrBot 和控制台。'
    ) -join "`r`n"
    Set-Content -LiteralPath (Join-Path $staging 'backup-info.txt') -Value $info -Encoding UTF8

    $zip = Join-Path $dest "qqbot-$stamp.zip"
    Compress-Archive -Path (Join-Path $staging '*') -DestinationPath $zip -CompressionLevel Optimal
    $size = [math]::Round((Get-Item -LiteralPath $zip).Length / 1MB, 1)
    Write-Log "完成: $zip ($size MB)"
    Write-Host "备份完成: $zip ($size MB)"
}
catch {
    Write-Log "失败: $($_.Exception.Message)"
    throw
}
finally {
    Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
}

# 只保留最近 14 份；部署包和手工备份不动
Get-ChildItem $dest -Filter 'qqbot-*.zip' |
    Where-Object { $_.Name -notlike 'qqbot-deploy-*' } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 14 |
    ForEach-Object {
        Remove-Item $_.FullName -Force
        Write-Log "清理旧备份: $($_.Name)"
    }
