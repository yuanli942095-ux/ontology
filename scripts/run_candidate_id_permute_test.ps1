param(
    [int]$EventLimit = 0,
    [int]$Runs = 5,
    [int]$QwenTimeout = 180,
    [switch]$Resume,
    [switch]$Clean,
    [switch]$OfflineOnly
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $ProjectRoot

function Import-DotEnv {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return }
    foreach ($line in Get-Content $Path) {
        $line = $line.Trim()
        if (-not $line -or $line.StartsWith("#")) { continue }
        $idx = $line.IndexOf("=")
        if ($idx -lt 1) { continue }
        $name = $line.Substring(0, $idx).Trim()
        $value = $line.Substring($idx + 1).Trim()
        if ($value.Length -ge 2 -and $value.StartsWith('"') -and $value.EndsWith('"')) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        Set-Item -Path "env:$name" -Value $value
    }
}

Import-DotEnv (Join-Path $ProjectRoot ".env")

if (-not $env:DEEPSEEK_API_KEY) {
    if (-not $OfflineOnly) {
        Write-Host "DEEPSEEK_API_KEY not set; running offline replay (candidate-blind stages reused)."
        $OfflineOnly = $true
    }
}

if ($OfflineOnly) {
    & (Join-Path $ProjectRoot ".venv\Scripts\python.exe") src/run_candidate_id_permute_offline_eval.py
    & (Join-Path $ProjectRoot ".venv\Scripts\python.exe") src/build_candidate_id_permutation_test_report.py
    exit $LASTEXITCODE
}

if (-not $env:DEEPSEEK_API_KEY) {
    throw "DEEPSEEK_API_KEY is not set. Add it to .env or use -OfflineOnly."
}

$benchmark = "benchmark\external-real-holdout-v5-blind-large-robustness-variants\candidate-id-permute"
$outDir = "output\external-real-holdout-v5-blind-large\robustness-online\candidate-id-permute-full-r5-deepseek"
$log = "output\external-real-holdout-v5-blind-large\robustness-online\candidate-id-permute-full-r5-deepseek-pipeline.log"

if ($Clean -and (Test-Path $outDir)) {
    Remove-Item -Recurse -Force $outDir
}

$env:PYTHONPATH = "src"
$env:ONTOLOGY_EVOLUTION_OFFICIAL_RUN = "1"
$env:ONTOLOGY_EVOLUTION_MODEL_ABLATION = "1"

$argsList = @(
    "src/run_v5_online_robustness_pipeline.py",
    "--benchmark-dir", $benchmark,
    "--output-dir", $outDir,
    "--event-limit", $EventLimit,
    "--runs", $Runs,
    "--qwen-timeout", $QwenTimeout,
    "--llm-backend", "deepseek_api"
)
if ($Resume) { $argsList += "--resume" }

Write-Host "Candidate-Identifier Permutation Test (full online): $Runs runs, event-limit=$EventLimit"
& (Join-Path $ProjectRoot ".venv\Scripts\python.exe") @argsList 2>&1 | Tee-Object -FilePath $log

& (Join-Path $ProjectRoot ".venv\Scripts\python.exe") src/build_candidate_id_permutation_test_report.py
