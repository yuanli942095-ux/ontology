param(
    [ValidateSet("pilot", "formal")]
    [string]$Phase = "formal",
    [string]$Components = "",
    [int]$EventLimit = 0,
    [int]$Runs = 5,
    [switch]$Resume,
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
if (-not $env:DEEPSEEK_API_KEY) {
    throw "DEEPSEEK_API_KEY is not set."
}
if (-not $env:DEEPSEEK_MODEL) {
    $env:DEEPSEEK_MODEL = "deepseek-v4-flash"
}
if (-not $env:DEEPSEEK_THINKING) {
    $env:DEEPSEEK_THINKING = "disabled"
}

$env:PYTHONPATH = "src"
$env:ONTOLOGY_EVOLUTION_OFFICIAL_RUN = "1"
$env:ONTOLOGY_EVOLUTION_MODEL_ABLATION = "1"
$py = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

# scikit-learn for Exp10-C statistical classifiers
& $py -c "import sklearn" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing scikit-learn for Exp10-C..."
    & $py -m pip install scikit-learn -q
}

$argsList = @("src/run_exp10_candidate_shortcut.py", "--event-limit", $EventLimit)
if ($Phase -eq "pilot") {
    $argsList += "--pilot"
} else {
    $argsList += "--runs"
    $argsList += $Runs
}
if ($Components) {
    $argsList += @("--components", $Components)
}
if ($Resume) {
    $argsList += "--resume"
}
if ($ExportOnly) {
    $argsList += "--export-only"
}

$log = "output\paper-final-validation\10-candidate-shortcut\exp10-$Phase-pipeline.log"
Write-Host "Exp10 candidate shortcut ($Phase): components=$Components runs=$Runs event-limit=$EventLimit"
& $py @argsList 2>&1 | Tee-Object -FilePath $log

if ($Phase -eq "formal" -and -not $ExportOnly) {
    & $py src/build_exp10_candidate_shortcut_report.py --phase formal 2>&1 | Tee-Object -FilePath $log -Append
}
