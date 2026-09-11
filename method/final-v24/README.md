# ECR-Repair V2.4 Final

This directory defines the frozen final method used for reporting.

## Reproduce the 300 x 5 offline closure evaluation

```powershell
cd G:\LearnAI\ontology-evolution
$env:PYTHONPATH = (Resolve-Path src)
.\.venv\Scripts\python.exe src\run_external_real_holdout_v6_direct_ir_blind.py `
  --step evaluate `
  --resolver v24 `
  --benchmark-dir benchmark\external-real-holdout-v6-2-direct-ir-blind `
  --output-dir output\external-real-holdout-v6-2-direct-ir-blind\final-v24-full-closure-r5
```

The output directory must already contain the frozen `v6-final-blind-manifest-r5.csv` and 1500 candidate-blind files under `raw-predicted-ir`.

## Verify the freeze

```powershell
$env:PYTHONPATH = (Resolve-Path src)
.\.venv\Scripts\python.exe method\final-v24\verify_freeze.py
```

Do not edit a bound file while continuing to call the method V2.4. Create V2.5 for any behavioral, prompt, schema, or evaluation change.
