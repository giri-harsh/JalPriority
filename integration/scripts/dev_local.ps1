<#
  Run the whole stack locally WITHOUT Docker (INTEGRATION.md 1.6 / 2.6):
    A    uvicorn jp_a.main:app        :8001
    B    uvicorn jp_b.main:app        :8002   (B_USE_MOCK_A=false, B_UPSTREAM_A_URL=http://localhost:8001)
    Vite B's shell (real A screens)   :5173   (VITE_B_USE_MOCK_A_SCREENS=false; proxies /a -> 8001, /b -> 8002)
  All three load integration/.env. Open http://localhost:5173 and log in as officer_01 / jal-demo-2026.

  .\dev_local.ps1             start, wait for health, then block until Ctrl+C (children are stopped on exit)
  .\dev_local.ps1 -Detach     start, wait for health, return (PIDs in integration/.run/pids.json)
  .\dev_local.ps1 -Stop       stop a detached stack
#>
param([switch]$Detach, [switch]$Stop, [switch]$NoVite)
$ErrorActionPreference = "Stop"
$integration = Split-Path -Parent $PSScriptRoot
$repo = Split-Path -Parent $integration
$runDir = Join-Path $integration ".run"
$pidFile = Join-Path $runDir "pids.json"
New-Item -ItemType Directory -Force $runDir | Out-Null

function Stop-Stack {
  if (Test-Path $pidFile) {
    foreach ($p in (Get-Content $pidFile | ConvertFrom-Json)) {
      & taskkill.exe /PID $p /T /F 2>$null | Out-Null
    }
    Remove-Item $pidFile -Force
  }
}
if ($Stop) { Stop-Stack; Write-Host "stopped"; return }
Stop-Stack

# --- environment: integration/.env is the only source of SHARED_JWT_* ---------------------------------------
$envFile = Join-Path $integration ".env"
if (-not (Test-Path $envFile)) { throw "integration/.env missing: copy .env.example and set SHARED_JWT_SECRET" }
foreach ($line in Get-Content $envFile) {
  $l = $line.Trim()
  if ($l -eq "" -or $l.StartsWith("#") -or -not $l.Contains("=")) { continue }
  $k, $v = $l.Split("=", 2)
  Set-Item -Path "Env:$($k.Trim())" -Value $v.Trim()
}
$env:SHARED_ENV = "local"
$env:A_AUTH_DISABLED = "false"
$env:A_CORS_ORIGINS = "http://localhost:5173"
$env:B_CORS_ORIGINS = "http://localhost:5173"
$env:B_USE_MOCK_A = "false"
$env:B_UPSTREAM_A_URL = "http://localhost:8001"
$env:B_SQLITE_PATH = Join-Path $repo "member-b\var\jp_b.sqlite3"
$env:VITE_B_USE_MOCK_A_SCREENS = "false"
$env:VITE_B_A_SCREENS_PATH = "../../member-a/dashboard/src/screens/index.ts"
New-Item -ItemType Directory -Force (Split-Path $env:B_SQLITE_PATH) | Out-Null

function Free-Port([int]$port) {
  Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
}
8001, 8002, 5173 | ForEach-Object { Free-Port $_ }

$pyA = Join-Path $repo "member-a\.venv\Scripts\python.exe"
$pyB = Join-Path $repo "member-b\.venv\Scripts\python.exe"
$pids = @()
$pids += (Start-Process $pyA -ArgumentList "-m", "uvicorn", "jp_a.main:app", "--app-dir", "backend", "--port", "8001" `
    -WorkingDirectory (Join-Path $repo "member-a") -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $runDir "a.log") -RedirectStandardError (Join-Path $runDir "a.err.log")).Id
$pids += (Start-Process $pyB -ArgumentList "-m", "uvicorn", "jp_b.main:app", "--app-dir", "backend", "--port", "8002" `
    -WorkingDirectory (Join-Path $repo "member-b") -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $runDir "b.log") -RedirectStandardError (Join-Path $runDir "b.err.log")).Id
if (-not $NoVite) {
  $pids += (Start-Process "npm.cmd" -ArgumentList "run", "dev" `
      -WorkingDirectory (Join-Path $repo "member-b\dashboard") -PassThru -WindowStyle Hidden `
      -RedirectStandardOutput (Join-Path $runDir "vite.log") -RedirectStandardError (Join-Path $runDir "vite.err.log")).Id
}
$pids | ConvertTo-Json | Set-Content $pidFile

function Wait-Url([string]$url, [int]$seconds = 90) {
  $deadline = (Get-Date).AddSeconds($seconds)
  while ((Get-Date) -lt $deadline) {
    try { Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 3 | Out-Null; return $true } catch { Start-Sleep -Milliseconds 700 }
  }
  return $false
}
$ok = (Wait-Url "http://localhost:8001/a/v1/health") -and (Wait-Url "http://localhost:8002/b/v1/health")
if ($ok -and -not $NoVite) { $ok = Wait-Url "http://localhost:5173/" }
if (-not $ok) { Write-Host "stack did not become healthy; see $runDir"; Stop-Stack; exit 1 }
Write-Host "A http://localhost:8001 | B http://localhost:8002 | shell http://localhost:5173  (logs: $runDir)"
if ($Detach) { return }
try { while ($true) { Start-Sleep -Seconds 2 } } finally { Stop-Stack }
