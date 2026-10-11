# Clean demo state: wipe B's SQLite database and A's derived (disruption) scenarios.
# Stop the stack first (dev_local.ps1 -Stop) so the SQLite file is not locked.
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

$bVar = Join-Path $repo "member-b\var"
if (Test-Path $bVar) {
  Get-ChildItem $bVar -Filter "jp_b.sqlite3*" -ErrorAction SilentlyContinue | Remove-Item -Force
  Write-Host "removed B sqlite files in $bVar"
}
$derived = Join-Path $repo "member-a\data\derived"
if (Test-Path $derived) {
  Get-ChildItem $derived -Filter "*.json" | Remove-Item -Force
  Write-Host "removed A derived scenarios in $derived"
}
Write-Host "demo reset done (docker users: docker compose down -v)"
