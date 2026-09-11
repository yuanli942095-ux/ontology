$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$runner = Join-Path $root "src\run_external_real_holdout_v6_direct_ir_blind.py"
$benchmark = Join-Path $root "benchmark\external-real-holdout-v6-2-direct-ir-blind"
$output = Join-Path $root "output\external-real-holdout-v6-2-direct-ir-blind\final-blind-r2b-v2-r5"

if (-not (Test-Path $python)) { throw "Missing project Python: $python" }
if (-not (Test-Path $runner)) { throw "Missing runner: $runner" }
if (-not (Test-Path $benchmark)) { throw "Missing benchmark: $benchmark" }

$env:PYTHONPATH = Join-Path $root "src"

& $python $runner `
  --benchmark-dir $benchmark `
  --output-dir $output `
  --step preflight
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $python $runner `
  --benchmark-dir $benchmark `
  --output-dir $output `
  --step prepare
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "v6.2 preflight and 300x5 manifest preparation completed."
Write-Host "Start paid/model execution explicitly with:"
Write-Host ".\.venv\Scripts\python.exe src\run_external_real_holdout_v6_direct_ir_blind.py --benchmark-dir benchmark\external-real-holdout-v6-2-direct-ir-blind --output-dir output\external-real-holdout-v6-2-direct-ir-blind\final-blind-r2b-v2-r5 --step generate --llm-backend deepseek_api --resume"
Write-Host ""
Write-Host "After generation:"
Write-Host ".\.venv\Scripts\python.exe src\run_external_real_holdout_v6_direct_ir_blind.py --benchmark-dir benchmark\external-real-holdout-v6-2-direct-ir-blind --output-dir output\external-real-holdout-v6-2-direct-ir-blind\final-blind-r2b-v2-r5 --step evaluate"
