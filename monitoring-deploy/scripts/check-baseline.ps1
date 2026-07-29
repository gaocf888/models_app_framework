# PowerShell Phase 0 基线检查（Windows 宿主机）
$ErrorActionPreference = "Continue"
$AppPort = if ($env:APP_PORT) { $env:APP_PORT } else { "8083" }
$VllmPort = if ($env:VLLM_PORT) { $env:VLLM_PORT } else { "8000" }
$MineruPort = if ($env:MINERU_PORT) { $env:MINERU_PORT } else { "8009" }

Write-Host "== Phase 0 baseline =="
$fail = 0
function Test-Url([string]$Name, [string]$Url) {
  try {
    Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5 | Out-Null
    Write-Host "[OK] $Name : $Url"
  } catch {
    Write-Host "[FAIL] $Name : $Url"
    $script:fail = 1
  }
}

Test-Url "models-app /health" "http://127.0.0.1:$AppPort/health"
Test-Url "models-app /metrics" "http://127.0.0.1:$AppPort/metrics"
Test-Url "vllm /health" "http://127.0.0.1:$VllmPort/health"
Test-Url "vllm /metrics" "http://127.0.0.1:$VllmPort/metrics"
try {
  Invoke-WebRequest -Uri "http://127.0.0.1:$MineruPort/health" -UseBasicParsing -TimeoutSec 3 | Out-Null
  Write-Host "[OK] mineru /health"
} catch {
  Write-Host "[SKIP] mineru /health"
}

Write-Host ""
Write-Host "Docker networks:"
docker network ls --format "{{.Name}}" | Select-String -Pattern "vllm|ai-stack|mineru"

if ($fail -eq 0) {
  Write-Host "Phase 0 baseline: PASS"
  exit 0
}
Write-Host "Phase 0 baseline: FAIL"
exit 1
