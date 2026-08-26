param(
    [ValidateSet("validate", "build", "dev", "test", "all")]
    [string]$Mode = "validate",
    [int]$Runs = 5,
    [int]$Seed = 20260820,
    [double]$Temperature = 0.2,
    [int]$Timeout = 180,
    [int]$NumPredict = 300,
    [string]$Model = "qwen3.5:9b"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

Push-Location $ProjectRoot
try {
    if ($Mode -eq "validate") {
        & $Python ".\src\validate_semantic_benchmark_v2.py" --require-ready
    }
    elseif ($Mode -eq "build") {
        & $Python ".\src\validate_semantic_benchmark_v2.py" --require-ready
        if ($LASTEXITCODE -ne 0) { throw "数据校验失败" }
        & $Python ".\src\build_semantic_benchmark_v2.py" --timeout $Timeout
    }
    else {
        & $Python ".\src\run_semantic_benchmark_v2.py" `
            --split $Mode `
            --runs $Runs `
            --seed $Seed `
            --temperature $Temperature `
            --timeout $Timeout `
            --num-predict $NumPredict `
            --model $Model
    }
    if ($LASTEXITCODE -ne 0) {
        throw "脚本执行失败，退出码=$LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
