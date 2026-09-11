param(
    [string]$Python = ".\.venv\Scripts\python.exe",
    [string]$BenchmarkDir = "benchmark\rfc-213-confirmatory-core",
    [string]$RawDir = "output\rfc-213-confirmatory-core\phase3-direct-ir\engineering-v1\raw-predicted-ir",
    [string]$Manifest = "output\rfc-213-confirmatory-core\phase3-direct-ir\engineering-v1\r2b-engineering-manifest.csv",
    [string]$OutputDir = "output\rfc-213-confirmatory-core\phase3-direct-ir\engineering-v2-window-resolution-rerun"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$env:PYTHONPATH = "src"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python executable not found: $Python"
}
if (-not (Test-Path -LiteralPath $RawDir)) {
    throw "Frozen candidate-blind raw directory not found: $RawDir"
}
if (-not (Test-Path -LiteralPath $Manifest)) {
    throw "Engineering manifest not found: $Manifest"
}

Write-Host "[1/3] Running offline tests (no LLM calls)..."
& $Python -m pytest -q `
    tests\test_rfc213_direct_repair_ir.py `
    tests\test_rfc213_direct_repair_ir_v2.py `
    tests\test_gamma_mvp_rfc213.py `
    tests\test_rfc213_confirmatory_core.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[2/3] Running R2b-v2 Gamma/Reasoner/CQ evaluation (no LLM calls)..."
& $Python src\evaluate_rfc213_direct_ir_v2_window_resolution.py `
    --benchmark-dir $BenchmarkDir `
    --raw-dir $RawDir `
    --manifest $Manifest `
    --output-dir $OutputDir `
    --timeout 180
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[3/3] Result summary:"
$summary = Import-Csv (Join-Path $OutputDir "r2b-v2-window-resolution-summary.csv")
$summary | Select-Object `
    semantic_type, events, attempts, predicted_gamma_ir_exact, gamma_accepted, `
    coverage, wrong_repairs, wrr, selective_risk, abstains, ses_success, ses |
    Format-Table -AutoSize

Write-Host "Development result only; do not label it as an independent blind result."
Write-Host "Output: $OutputDir"
