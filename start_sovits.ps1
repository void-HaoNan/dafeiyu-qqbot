param(
    [string]$Root = $env:QQBOT_ROOT,
    [string]$GptPath = '',
    [string]$SovitsPath = ''
)

if (-not $Root) { $Root = $PSScriptRoot }
$gsv = if ($env:GPT_SOVITS_ROOT) { $env:GPT_SOVITS_ROOT } else { Join-Path $Root 'tools\GPT-SoVITS' }

$env:PATH = "$gsv\bin;$env:PATH"
$env:PYTHONIOENCODING = 'utf-8'
Set-Location $gsv

# 从控制台记录的当前模型读权重；没有记录就用参数
$stateFile = Join-Path $Root 'panel\model_state.json'
$gpt = ''
$sov = ''
if (Test-Path $stateFile) {
    try {
        $s = Get-Content $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $gpt = [string]$s.gpt
        $sov = [string]$s.sovits
    } catch { }
}
if (-not $gpt) { $gpt = $GptPath }
if (-not $sov) { $sov = $SovitsPath }

# 起服务后自动挂载权重（没配模型就只起服务）
if ($gpt -and $sov -and (Test-Path $gpt) -and (Test-Path $sov)) {
    Start-Job -ScriptBlock {
        param($g, $s, $logFile)
        for ($i = 0; $i -lt 60; $i++) {
            Start-Sleep -Seconds 2
            try {
                $null = Invoke-WebRequest -Uri 'http://127.0.0.1:9880/docs' -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
                break
            } catch {
                if ($_.Exception.Response) { break }
            }
        }
        try {
            Invoke-WebRequest -Uri "http://127.0.0.1:9880/set_gpt_weights?weights_path=$([uri]::EscapeDataString($g))" -TimeoutSec 180 -UseBasicParsing | Out-Null
            Invoke-WebRequest -Uri "http://127.0.0.1:9880/set_sovits_weights?weights_path=$([uri]::EscapeDataString($s))" -TimeoutSec 180 -UseBasicParsing | Out-Null
            Add-Content -Path $logFile -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') OK $s"
        } catch {
            Add-Content -Path $logFile -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') FAIL $($_.Exception.Message)"
        }
    } -ArgumentList $gpt, $sov, (Join-Path $Root 'logs\sovits_weights.log') | Out-Null
} else {
    Write-Host '没有找到音色权重，先只起语音服务；在控制台「声音」页选好音色后再重启本脚本。'
}

& "$gsv\env\python.exe" -u api_v2.py -a 127.0.0.1 -p 9880
