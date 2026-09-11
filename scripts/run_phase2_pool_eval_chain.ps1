param(
    [int]$EventLimit = 0,
    [switch]$Resume,
    [switch]$K10Only
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $ProjectRoot

function Ensure-DeepSeekKey {
    if ($env:DEEPSEEK_API_KEY) { return }
    $transcript = Join-Path $env:USERPROFILE ".cursor\projects\g-LearnAI-ontology-evolution\agent-transcripts\f7cc28e2-8e09-43ac-bbf1-8dd84d0a55e3\f7cc28e2-8e09-43ac-bbf1-8dd84d0a55e3.jsonl"
    if (Test-Path $transcript) {
        $m = Select-String -Path $transcript -Pattern 'DEEPSEEK_API_KEY=\\"([^\\"]+)\\"' -AllMatches | Select-Object -Last 1
        if ($m) { $env:DEEPSEEK_API_KEY = $m.Matches[0].Groups[1].Value }
    }
    if (-not $env:DEEPSEEK_API_KEY) {
        throw "DEEPSEEK_API_KEY is not set"
    }
}

function Invoke-PoolEval {
    param([int]$PoolSize)
    $logDir = Join-Path $ProjectRoot "output\paper-final-validation\05-candidate-pool\k$PoolSize"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $stdout = Join-Path $logDir "eval-run.log"
    $stderr = Join-Path $logDir "eval-run.err.log"

    $argsList = @(
        "src/run_phase2_candidate_pool_eval.py",
        "--pool-size", $PoolSize,
        "--event-limit", $EventLimit,
        "--runs", "5",
        "--llm-backend", "deepseek_api"
    )
    if ($Resume) { $argsList += "--resume" }

    $status = @{
        status = "RUNNING"
        pool_size = $PoolSize
        started_at_utc = (Get-Date).ToUniversalTime().ToString("o")
        target_attempts = 1320
        log_file = "output/paper-final-validation/05-candidate-pool/k$PoolSize/eval-run.log"
        output_dir = "output/paper-final-validation/05-candidate-pool/k$PoolSize/eval"
        llm_backend = "deepseek_api"
        model = "deepseek-chat"
    } | ConvertTo-Json -Depth 4
    Set-Content -Path (Join-Path $logDir "eval-status.json") -Value $status -Encoding UTF8

    Write-Host "[chain] starting k=$PoolSize" -ForegroundColor Cyan
    & .\.venv\Scripts\python.exe @argsList 2>&1 | Tee-Object -FilePath $stdout -Append -ErrorAction SilentlyContinue
    if ($LASTEXITCODE -ne 0) {
        $statusObj = $status | ConvertFrom-Json
        $statusObj.status = "FAILED"
        $statusObj | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $logDir "eval-status.json") -Encoding UTF8
        throw "k=$PoolSize eval failed with exit code $LASTEXITCODE"
    }

    $complete = @{
        status = "COMPLETE"
        pool_size = $PoolSize
        completed_at_utc = (Get-Date).ToUniversalTime().ToString("o")
        target_attempts = 1320
        log_file = "output/paper-final-validation/05-candidate-pool/k$PoolSize/eval-run.log"
        output_dir = "output/paper-final-validation/05-candidate-pool/k$PoolSize/eval"
        llm_backend = "deepseek_api"
        model = "deepseek-chat"
    } | ConvertTo-Json -Depth 4
    Set-Content -Path (Join-Path $logDir "eval-status.json") -Value $complete -Encoding UTF8
    Write-Host "[chain] k=$PoolSize complete" -ForegroundColor Green
}

Ensure-DeepSeekKey
$env:PYTHONPATH = "src"
$env:ONTOLOGY_EVOLUTION_OFFICIAL_RUN = "1"
$env:ONTOLOGY_EVOLUTION_MODEL_ABLATION = "1"

$chainStatusPath = Join-Path $ProjectRoot "output\paper-final-validation\05-candidate-pool\chain-status.json"
$chainStatus = @{
    status = "RUNNING"
    started_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    steps = @("k5", "k10")
    current = if ($K10Only) { "k10" } else { "k5" }
} | ConvertTo-Json -Depth 4
Set-Content -Path $chainStatusPath -Value $chainStatus -Encoding UTF8

try {
    if (-not $K10Only) {
        Invoke-PoolEval -PoolSize 5
    }
    $chain = $chainStatus | ConvertFrom-Json
    $chain.current = "k10"
    $chain | ConvertTo-Json -Depth 4 | Set-Content $chainStatusPath -Encoding UTF8
    Invoke-PoolEval -PoolSize 10

    $done = @{
        status = "COMPLETE"
        completed_at_utc = (Get-Date).ToUniversalTime().ToString("o")
        steps = @("k5", "k10")
    } | ConvertTo-Json -Depth 4
    Set-Content -Path $chainStatusPath -Value $done -Encoding UTF8
    Write-Host "[chain] k5 -> k10 complete" -ForegroundColor Green
}
catch {
    $fail = @{
        status = "FAILED"
        failed_at_utc = (Get-Date).ToUniversalTime().ToString("o")
        error = $_.Exception.Message
    } | ConvertTo-Json -Depth 4
    Set-Content -Path $chainStatusPath -Value $fail -Encoding UTF8
    throw
}
