$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$port = 8000
$hostName = "127.0.0.1"
$configPath = Join-Path $repoRoot "config.local.yaml"
$frontendDist = Join-Path $repoRoot "frontend\dist"
$logDir = Join-Path $repoRoot "data\logs"
$outLog = Join-Path $logDir "echo-full-8000.out.log"
$errLog = Join-Path $logDir "echo-full-8000.err.log"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($listener -and $listener.OwningProcess) {
    Write-Host "Stopping process $($listener.OwningProcess) on port $port..."
    Stop-Process -Id $listener.OwningProcess -Force
    Start-Sleep -Seconds 1
}

$env:ECHO_WEBUI_DIST = $frontendDist

Write-Host "Starting full Echo backend on http://${hostName}:$port ..."
$proc = Start-Process `
    -FilePath python `
    -ArgumentList @("-m", "runtime", "serve", "--config", $configPath, "--host", $hostName, "--port", "$port") `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -PassThru

Start-Sleep -Seconds 8

$active = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $active) {
    Write-Error "Backend did not start on port $port. See $errLog"
}

$commandLine = Get-CimInstance Win32_Process -Filter "ProcessId=$($active.OwningProcess)" |
    Select-Object -ExpandProperty CommandLine
$status = $null
for ($attempt = 1; $attempt -le 5; $attempt++) {
    try {
        $status = Invoke-RestMethod -Uri "http://${hostName}:$port/api/status" -TimeoutSec 5
        break
    } catch {
        Start-Sleep -Seconds 1
    }
}

if (-not $status) {
    Write-Error "Backend started, but /api/status did not respond. See $errLog"
}

Write-Host "PID: $($active.OwningProcess)"
Write-Host "Command: $commandLine"
Write-Host "Skills: $($status.skill_count)"
Write-Host "Logs: $errLog"
exit 0
