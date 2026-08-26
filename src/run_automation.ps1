$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "Python virtual environment was not found: $Python"
}

Write-Host "[1/2] Regenerating benchmark ontologies with unique Ontology IRIs..." -ForegroundColor Cyan
& $Python (Join-Path $PSScriptRoot "inject_errors.py")
if ($LASTEXITCODE -ne 0) {
    throw "inject_errors.py failed. Exit code: $LASTEXITCODE"
}

Write-Host "`n[2/2] Running Reasoner, evidence validation, and CQ regression..." -ForegroundColor Cyan
& $Python (Join-Path $PSScriptRoot "validate_benchmark.py")
if ($LASTEXITCODE -ne 0) {
    throw "validate_benchmark.py failed. Exit code: $LASTEXITCODE"
}

Write-Host "`nAutomation completed. Results: $ProjectDir\output" -ForegroundColor Green
