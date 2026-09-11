$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$runner = Join-Path $root "src\run_external_real_holdout_v6_direct_ir_blind.py"

if (-not (Test-Path $python)) { throw "Missing project Python: $python" }
$env:PYTHONPATH = Join-Path $root "src"

& $python $runner --step preflight
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python $runner --step prepare
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Preflight and 300x5 manifest preparation completed."
Write-Host "Start paid/model execution explicitly with:"
Write-Host ".\.venv\Scripts\python.exe src\run_external_real_holdout_v6_direct_ir_blind.py --step generate --llm-backend deepseek_api --resume"
Write-Host "After generation:"
Write-Host ".\.venv\Scripts\python.exe src\run_external_real_holdout_v6_direct_ir_blind.py --step evaluate"
