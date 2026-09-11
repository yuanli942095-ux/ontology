param(
    [Parameter(Mandatory = $true)]
    [ValidateSet(5, 10)]
    [int]$PoolSize,

    [int]$EventLimit = 0,
    [switch]$Resume
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not $env:DEEPSEEK_API_KEY) {
    $transcript = Join-Path $env:USERPROFILE ".cursor\projects\g-LearnAI-ontology-evolution\agent-transcripts\f7cc28e2-8e09-43ac-bbf1-8dd84d0a55e3\f7cc28e2-8e09-43ac-bbf1-8dd84d0a55e3.jsonl"
    if (Test-Path $transcript) {
        $m = Select-String -Path $transcript -Pattern 'DEEPSEEK_API_KEY=\\"([^\\"]+)\\"' -AllMatches | Select-Object -Last 1
        if ($m) { $env:DEEPSEEK_API_KEY = $m.Matches[0].Groups[1].Value }
    }
}
if (-not $env:DEEPSEEK_API_KEY) {
    throw "DEEPSEEK_API_KEY is not set"
}

$env:PYTHONPATH = "src"
$argsList = @(
    "src/run_phase2_candidate_pool_eval.py",
    "--pool-size", $PoolSize,
    "--event-limit", $EventLimit,
    "--runs", "5",
    "--llm-backend", "deepseek_api"
)
if ($Resume) { $argsList += "--resume" }

& .\.venv\Scripts\python.exe @argsList
