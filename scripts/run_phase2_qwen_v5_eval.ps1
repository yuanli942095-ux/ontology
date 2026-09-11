param(
    [int]$EventLimit = 0,
    [int]$Runs = 5,
    [int]$QwenTimeout = 300,
    [switch]$Resume
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $ProjectRoot

$outDir = Join-Path $ProjectRoot "output\paper-final-validation\06-backend-qwen-v5"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$log = Join-Path $outDir "eval-run.log"
$err = Join-Path $outDir "eval-run.err.log"

$status = @{
    status = "RUNNING"
    experiment = "06-backend-qwen-v5"
    started_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    target_attempts = 1320
    llm_backend = "ollama"
    model = "qwen3.5:9b"
    log_file = "output/paper-final-validation/06-backend-qwen-v5/eval-run.log"
    output_dir = "output/paper-final-validation/06-backend-qwen-v5"
} | ConvertTo-Json -Depth 4
Set-Content -Path (Join-Path $outDir "eval-status.json") -Value $status -Encoding UTF8

$env:PYTHONPATH = "src"
$env:ONTOLOGY_EVOLUTION_OFFICIAL_RUN = "1"
$env:ONTOLOGY_EVOLUTION_MODEL_ABLATION = "1"

$argsList = @(
    "src/run_phase2_qwen_v5_eval.py",
    "--event-limit", $EventLimit,
    "--runs", $Runs,
    "--qwen-timeout", $QwenTimeout
)
if ($Resume) { $argsList += "--resume" }

Write-Host "[exp6] starting Qwen v5 eval" -ForegroundColor Cyan
& .\.venv\Scripts\python.exe @argsList 2>&1 | Tee-Object -FilePath $log -Append -ErrorAction SilentlyContinue
if ($LASTEXITCODE -ne 0) {
    $fail = $status | ConvertFrom-Json
    $fail.status = "FAILED"
    $fail | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $outDir "eval-status.json") -Encoding UTF8
    throw "Exp6 failed with exit code $LASTEXITCODE"
}

$done = @{
    status = "COMPLETE"
    experiment = "06-backend-qwen-v5"
    completed_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    target_attempts = 1320
} | ConvertTo-Json -Depth 4
Set-Content -Path (Join-Path $outDir "eval-status.json") -Value $done -Encoding UTF8
Write-Host "[exp6] complete" -ForegroundColor Green
