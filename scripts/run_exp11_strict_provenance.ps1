param(
    [ValidateSet("pilot", "formal")]
    [string]$Phase = "formal",
    [int]$TargetEvents = 60,
    [int]$EventLimit = 0,
    [int]$Runs = 5,
    [switch]$Resume,
    [switch]$SkipBuild,
    [switch]$ExportOnly
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
if (-not $env:DEEPSEEK_API_KEY) { throw "DEEPSEEK_API_KEY is not set." }
if (-not $env:DEEPSEEK_MODEL) { $env:DEEPSEEK_MODEL = "deepseek-v4-flash" }
if (-not $env:DEEPSEEK_THINKING) { $env:DEEPSEEK_THINKING = "disabled" }

$env:PYTHONPATH = "src"
$env:ONTOLOGY_EVOLUTION_OFFICIAL_RUN = "1"
$env:ONTOLOGY_EVOLUTION_MODEL_ABLATION = "1"
$py = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

$argsList = @(
    "src/run_exp11_strict_provenance.py",
    "--target-events", $TargetEvents,
    "--event-limit", $EventLimit
)
if ($Phase -eq "pilot") { $argsList += "--pilot" } else { $argsList += @("--runs", $Runs) }
if ($Resume) { $argsList += "--resume" }
if ($SkipBuild) { $argsList += "--skip-build" }
if ($ExportOnly) { $argsList += "--export-only" }

$log = "output\paper-final-validation\11-strict-provenance\exp11-$Phase-pipeline.log"
Write-Host "Exp11 strict provenance ($Phase): target-events=$TargetEvents runs=$Runs"
& $py @argsList 2>&1 | Tee-Object -FilePath $log

if ($Phase -eq "formal" -and -not $ExportOnly) {
    & $py src/build_exp11_strict_provenance_report.py --phase formal 2>&1 | Tee-Object -FilePath $log -Append
}
