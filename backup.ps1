# 备份机器人需要的东西：备份内容由 data\dafeiyu\backup_config.json 决定。
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

# 默认全部核心项；控制台保存后会生成这个文件
$cfgPath = Join-Path $Root 'data\dafeiyu\backup_config.json'
$cfg = $null
if (Test-Path -LiteralPath $cfgPath) {
    try {
        $cfg = Get-Content -LiteralPath $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        $cfg = $null
    }
}
function Get-Bool([string]$Name, [bool]$Default) {
    if ($null -eq $cfg) { return $Default }
    $p = $cfg.PSObject.Properties[$Name]
    if ($null -eq $p) { return $Default }
    return [bool]$p.Value
}
function Get-Int([string]$Name, [int]$Default) {
    if ($null -eq $cfg) { return $Default }
    $p = $cfg.PSObject.Properties[$Name]
    if ($null -eq $p) { return $Default }
    try { return [int]$p.Value } catch { return $Default }
}

$retention = [Math]::Max(1, [Math]::Min(60, (Get-Int 'retention' 14)))
$targets = @()
$labels = @()

if (Get-Bool 'astrbot_data' $true) {
    $targets += @{ Path = (Join-Path $Root 'AstrBot\data'); Name = 'astrbot-data'; File = $false }
    $labels += 'AstrBot 数据'
}
if (Get-Bool 'dafeiyu_data' $true) {
    $targets += @{ Path = (Join-Path $Root 'data'); Name = 'data'; File = $false }
    $labels += '大肥鱼数据'
}
if (Get-Bool 'snowluma_config' $true) {
    $targets += @{ Path = (Join-Path $Root 'SnowLuma\config'); Name = 'snowluma-config'; File = $false }
    $labels += 'SnowLuma 配置'
}
if (Get-Bool 'secrets' $true) {
    $targets += @{ Path = (Join-Path $Root 'secrets'); Name = 'secrets'; File = $false }
    $labels += '密钥'
}
if (Get-Bool 'personas' $true) {
    $targets += @{ Path = (Join-Path $Root 'personas'); Name = 'personas'; File = $false }
    $labels += '人设'
}
if (Get-Bool 'panel_code' $true) {
    foreach ($f in 'app.py', 'index.html', 'panel.vbs') {
        $targets += @{ Path = (Join-Path $Root "panel\$f"); Name = "panel\$f"; File = $true }
    }
    $labels += '控制台代码'
}
if (Get-Bool 'panel_state' $true) {
    foreach ($f in 'model_state.json', 'rvc_state.json') {
        $targets += @{ Path = (Join-Path $Root "panel\$f"); Name = "panel\$f"; File = $true }
    }
    $labels += '控制台状态'
}
if (Get-Bool 'rvc_map' $true) {
    $targets += @{ Path = (Join-Path $Root 'rvc\model_map.json'); Name = 'rvc\model_map.json'; File = $true }
    $labels += 'RVC 映射'
}
if (Get-Bool 'stickers' $false) {
    $targets += @{ Path = (Join-Path $Root 'stickers'); Name = 'stickers'; File = $false }
    $labels += '表情包'
}

if ($targets.Count -eq 0) {
    Write-Log '失败: 备份内容全是空的'
    throw '没有勾选任何备份内容'
}

Write-Log "开始备份 -> $dest；内容: $($labels -join '、')；保留 $retention 份"

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
        ('包含: ' + ($labels -join '、')),
        ('保留: ' + $retention + ' 份'),
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

# 只保留最近 N 份；部署包和手工备份不动
Get-ChildItem $dest -Filter 'qqbot-*.zip' |
    Where-Object { $_.Name -notlike 'qqbot-deploy-*' } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip $retention |
    ForEach-Object {
        Remove-Item $_.FullName -Force
        Write-Log "清理旧备份: $($_.Name)"
    }
