# ECR-IR-Gamma R2b draft

This directory contains the method-owned Direct Predicted Repair IR contract.
It does not belong to the frozen RFC-213 benchmark and does not alter Gamma.

Current status: smoke-v2 passed; freeze-v1 manifest generation is required
before the `engineering` profile can run.

Before running the model smoke, audit whether each expected canonical literal
is deterministically derivable from its reviewed public evidence:

```powershell
.\.venv\Scripts\python.exe src\audit_rfc213_direct_ir_target_contract.py
```

The audit is deliberately offline and may read private Gold only for scoring.
Its details are prohibited generation inputs. A nonzero exit means RFC-213 is
not yet suitable for a confirmatory Direct-IR exact-literal claim, even though
the runner can still be used as a diagnostic smoke test.

The runner intentionally exposes only the five-event smoke profile. Generation
is candidate-blind but ontology-aware: it reads public event metadata, the
frozen public evidence excerpt, and literal assertions in the current ontology.
Candidates and private Oracle data are opened only by the offline evaluation
step after predictions have been persisted.

The model selects one contiguous, sufficient, verbatim source-window span and
emits its surface text. The candidate-blind normalizer then applies the public v5 ordered unique-token
encoding to produce the literal accepted by Gamma. Raw and canonical values are
both retained in the evaluation details.

PowerShell smoke commands from the repository root:

```powershell
$direct = @(
  "src\run_rfc213_direct_ir_gamma.py",
  "--profile", "smoke",
  "--llm-backend", "deepseek_api"
)

.\.venv\Scripts\python.exe @direct --step prepare
.\.venv\Scripts\python.exe @direct --step generate --resume
.\.venv\Scripts\python.exe @direct --step evaluate
```

Do not add an engineering or full profile until the Direct-IR method has been
audited and frozen. Do not tune on the RFC-213 confirmatory outcomes and then
reuse those events as an independent confirmatory test.

Freeze after smoke-v2:

```powershell
.\.venv\Scripts\python.exe src\freeze_rfc213_direct_ir_method.py
```

The frozen runner permits `engineering` (213 events x 1 run) but keeps the
213 x 5 profile locked.
