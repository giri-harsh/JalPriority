# Smoke test (INTEGRATION.md 2.7). $env:JP_BASE_URL defaults to the gateway (http://localhost:8080);
# use http://localhost:5173 with dev_local.ps1.
$ErrorActionPreference = "Stop"
$base = if ($env:JP_BASE_URL) { $env:JP_BASE_URL } else { "http://localhost:8080" }

function Check($name, $ok) { if ($ok) { Write-Host "PASS $name" } else { Write-Host "FAIL $name"; exit 1 } }

$ha = Invoke-RestMethod "$base/a/v1/health"
Check "A health" ($ha.status -eq "ok")
$hb = Invoke-RestMethod "$base/b/v1/health"
Check "B health" ($hb.status -eq "ok")

$login = Invoke-RestMethod -Method Post "$base/b/v1/auth/login" -ContentType "application/json" `
  -Body (@{ username = "officer_01"; password = "jal-demo-2026" } | ConvertTo-Json)
$h = @{ Authorization = "Bearer $($login.token)" }
Check "login" ([bool]$login.token)

$sc = Invoke-RestMethod "$base/a/v1/scenarios" -Headers $h
Check "A accepts B's token (GET /a/v1/scenarios)" ($sc.scenarios.Count -ge 3)

$plan = Invoke-RestMethod -Method Post "$base/b/v1/plans" -Headers $h -ContentType "application/json" `
  -Body (@{ scenario_id = "scn_sa01_w06" } | ConvertTo-Json)
Check "B -> A plan" ($plan.plan_id -like "pln_*")
$plan.metrics | ConvertTo-Json
