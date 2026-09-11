param(
    [int]$EventLimit = 0,
    [int]$Runs = 5,
    [int]$QwenTimeout = 180
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
    throw "DEEPSEEK_API_KEY is not set."
}

$benchmark = "benchmark\external-real-holdout-v5-blind-large-robustness-variants\candidate-id-permute"
$outDir = "output\external-real-holdout-v5-blind-large\robustness-online\candidate-id-permute-full-r5-deepseek"
$m13Details = Join-Path $outDir "m13-pilot\arm-d-rule-refinement\ir\auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827-details.csv"
if (-not (Test-Path $m13Details)) {
    throw "Missing M13 details: $m13Details"
}

$log = "output\external-real-holdout-v5-blind-large\robustness-online\candidate-id-permute-full-r5-deepseek-pipeline-m14-m16.log"
$env:PYTHONPATH = "src"
$env:ONTOLOGY_EVOLUTION_OFFICIAL_RUN = "1"
$env:ONTOLOGY_EVOLUTION_MODEL_ABLATION = "1"
$py = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

$only = (& $py -c "
import csv
from pathlib import Path
rows = list(csv.DictReader((Path('$ProjectRoot') / '$benchmark' / 'public' / 'events' / 'external-real-event-template.csv').open(encoding='utf-8-sig')))
rows = [r for r in rows if r.get('status')=='READY']
rows.sort(key=lambda r: r['event_id'])
limit = $EventLimit
if limit: rows = rows[:limit]
print(','.join(r['event_id'] for r in rows))
").Trim()

Write-Host "Running M14-M16 only (M13 IR already complete): only=$only"

function Invoke-Stage {
    param([string[]]$ArgsList)
    & $py @ArgsList 2>&1 | Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) { throw "Stage failed: $($ArgsList -join ' ')" }
}

Invoke-Stage @(
    "src/run_m14_clause_level_gre_css_recovery.py",
    "--benchmark-dir", $benchmark,
    "--details", $m13Details,
    "--output-dir", (Join-Path $outDir "m14-clause-level-gre-css-recovery"),
    "--limit", "999999",
    "--only", $only,
    "--timeout", $QwenTimeout,
    "--llm-backend", "deepseek_api",
    "--resume"
)

Invoke-Stage @(
    "src/combine_holdout_pipeline_details.py",
    "--stage", "m14",
    "--output-dir", (Join-Path $outDir "m14-full-holdout-combined"),
    "--base-details", $m13Details,
    "--recovery-details", (Join-Path $outDir "m14-clause-level-gre-css-recovery\ir\m14-clause-level-gre-css-recovery-v4-ir-details.csv")
)

Invoke-Stage @(
    "src/run_m15_temporal_anchor_recovery.py",
    "--benchmark-dir", $benchmark,
    "--details", (Join-Path $outDir "m14-full-holdout-combined\m14-full-holdout-combined-full-details.csv"),
    "--output-dir", (Join-Path $outDir "m15-temporal-anchor-recovery"),
    "--limit", "999999",
    "--only", $only,
    "--resume"
)

Invoke-Stage @(
    "src/combine_holdout_pipeline_details.py",
    "--stage", "m15",
    "--output-dir", (Join-Path $outDir "m15-full-holdout-combined"),
    "--base-details", $m13Details,
    "--prior-full-details", (Join-Path $outDir "m14-full-holdout-combined\m14-full-holdout-combined-full-details.csv"),
    "--prior-summary-details", (Join-Path $outDir "m14-full-holdout-combined\m14-full-holdout-combined-details.csv"),
    "--recovery-details", (Join-Path $outDir "m15-temporal-anchor-recovery\ir\m15-temporal-anchor-recovery-v4-ir-details.csv")
)

Invoke-Stage @(
    "src/run_m16_candidate_entailment_verifier.py",
    "--benchmark-dir", $benchmark,
    "--details", (Join-Path $outDir "m15-full-holdout-combined\m15-full-holdout-combined-details.csv"),
    "--m14-details", (Join-Path $outDir "m14-clause-level-gre-css-recovery\ir\m14-clause-level-gre-css-recovery-v4-ir-details.csv"),
    "--output-dir", (Join-Path $outDir "m16-candidate-entailment"),
    "--limit", "999999",
    "--only", $only,
    "--timeout", $QwenTimeout,
    "--llm-backend", "deepseek_api",
    "--resume"
)

Invoke-Stage @(
    "src/combine_holdout_pipeline_details.py",
    "--stage", "m16",
    "--output-dir", (Join-Path $outDir "m16-full-holdout-combined"),
    "--base-details", $m13Details,
    "--prior-full-details", (Join-Path $outDir "m15-full-holdout-combined\m15-full-holdout-combined-full-details.csv"),
    "--prior-summary-details", (Join-Path $outDir "m15-full-holdout-combined\m15-full-holdout-combined-details.csv"),
    "--recovery-details", (Join-Path $outDir "m16-candidate-entailment\m16-candidate-entailment-details.csv")
)

& $py src/build_candidate_id_permutation_test_report.py 2>&1 | Tee-Object -FilePath $log -Append
