param(
    [switch]$SkipExp14
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $ProjectRoot
$env:PYTHONPATH = "src"
$py = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

Write-Host "Building Exp12 human annotation package..."
& $py src/build_exp12_human_annotation_package.py

Write-Host "Building Exp13 IR fidelity package..."
& $py src/build_exp13_ir_fidelity_package.py

if (-not $SkipExp14) {
    Write-Host "Building Exp14 statistical audit..."
    & $py src/build_exp14_statistical_audit.py
}

Write-Host "Building Exp15 audit trace export..."
& $py src/build_exp15_audit_trace.py

Write-Host "Computing Exp12 agreement (after annotator B)..."
& $py src/compute_exp12_agreement.py

Write-Host "Building Exp13 IR fidelity report..."
& $py src/build_exp13_ir_fidelity_report.py

Write-Host "Phase 3 (Exp12-15) package build complete."
