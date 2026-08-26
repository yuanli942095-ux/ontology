# Semantic-V2 Methodology-Corrected Report

Generated after expanding semantic-v2 to 30 controlled test events.

## 1. Methodological Correction

The previous report over-positioned `OPTION_FORMAL_POLICY_HARD_GATE` as the final method. That is not a defensible paper narrative.

The corrected interpretation is:

| Method | Correct Role |
|---|---|
| `DIRECT_FREE` | LLM direct baseline without candidate options |
| `OPTION_VALUE_ONLY` | Weak candidate baseline with option values only |
| `OPTION_FORMAL_OPERATION` | Baseline with formal repair operations, but no policy |
| `OPTION_FORMAL_POLICY` | Main LLM-assisted method under structured policy input |
| `OPTION_FORMAL_POLICY_HARD_GATE` | Policy-available symbolic upper bound |
| `TEMPLATE_POLICY_HARD_GATE` | Event-id-free symbolic upper bound over manually structured slots |

`OPTION_FORMAL_POLICY_HARD_GATE` must not be claimed as a fully automatic method. It executes manually structured `facts` and prioritized `rules`. Its zero-token runtime cost excludes policy construction effort.

## 2. Benchmark State

Current semantic-v2 benchmark:

| Split | Events |
|---|---:|
| dev | 6 |
| test | 30 |
| total READY | 36 |

Test semantic type distribution:

| Semantic Type | Test Events |
|---|---:|
| `TEMPORAL_VERSION` | 10 |
| `GENERAL_RULE_EXCEPTION` | 10 |
| `CROSS_SENTENCE_SCOPE` | 10 |

The expansion was controlled and policy-pattern based. New challenge events should be described as controlled challenge events constructed before the 30-test reproduction run, not as events added after seeing a particular model failure.

## 3. Benchmark Freeze Manifest

Command:

```powershell
& 'G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe' `
  'G:\LearnAI\ontology-evolution\src\generate_semantic_v2_freeze_manifest.py' `
  --split test `
  --prefix semantic-v2-freeze-manifest-test-20260826
```

Result:

| Item | Value |
|---|---:|
| Split | test |
| Events | 30 |
| `TEMPORAL_VERSION` | 10 |
| `GENERAL_RULE_EXCEPTION` | 10 |
| `CROSS_SENTENCE_SCOPE` | 10 |
| Public hashed files | 113 |
| Private Oracle integrity rows | 31 |
| Git repository available | True |
| Freeze commit | `688e6cdd8a78327e89358846dbfbe61eac2271d3` |

The manifest records the current frozen file state for the 30-test split. It hashes public benchmark inputs, documents, formal-policy rules, mutants, built public event files, and reproduction scripts. It also writes a separate private Oracle integrity file for local audit only. A local Git repository was initialized and the current benchmark state was committed as the freeze point above.

Important limitation:

The freeze commit was created after the development experiments in this report, so it cannot retroactively prove that earlier experiments were run after a freeze. It does provide a concrete fixed state for headline reruns. For a paper artifact, archive the benchmark with an external timestamp or DOI.

Post-freeze deterministic validation:

| Check | Result |
|---|---:|
| Template-policy hard gate | 30/30 events, 150/150 calls, 100.00% |
| Hard-gate OWL repair closure | 30/30 events, 150/150 calls, 100.00% |

Post-freeze Qwen headline rerun status:

The post-freeze main-table Qwen rerun initially failed because the local Ollama/Qwen service was degraded. A minimal `/api/chat` JSON request took 34.12 seconds, and a one-call E14 diagnostic probe with `--timeout 60` returned `REJECTED_ERROR` with `reason=TimeoutError: timed out`. After unloading and reloading `qwen3.5:9b`, the same E14 benchmark prompt completed in 3.8 seconds.

The full post-freeze main-table rerun then completed from HEAD `e0ab5e9ef8e3d5592a6e9010fb7ca9934f4f3697`. The result exactly reproduces the development headline table: `DIRECT_FREE` 77.33%, `OPTION_VALUE_ONLY` 81.33%, `OPTION_FORMAL_OPERATION` 76.00%, `OPTION_FORMAL_POLICY` 97.33%, and `OPTION_FORMAL_POLICY_HARD_GATE` 100.00%.

The post-freeze OWL repair closure rerun also completed from the post-freeze main-table details and reproduced the repair-closure result.

Evidence files:

- `src/generate_semantic_v2_freeze_manifest.py`
- `output/semantic-v2-freeze-manifest-test-20260826.json`
- `output/semantic-v2-freeze-manifest-test-20260826-files.csv`
- `output/semantic-v2-freeze-manifest-test-20260826-private-oracle.csv` local audit only
- `output/semantic-v2-freeze-manifest-test-20260826.log`
- `output/semantic-v2-freeze-manifest-test-20260826-post-git-freeze.json`
- `output/post-freeze-template-policy-hard-gate-test-summary.csv`
- `output/post-freeze-repair-closure-hardgate-test-summary.csv`
- `output/post-freeze-qwen-error-probe-e14-direct-free-details.csv`
- `output/post-freeze-qwen-error-probe-e14-direct-free.json`
- `output/post-restore-qwen-probe-e14-direct-free-details.csv`
- `output/post-restore-qwen-probe-e14-direct-free.json`
- `output/post-freeze-final-main-table-test-r5-seed20260820-summary.csv`
- `output/post-freeze-final-main-table-test-r5-seed20260820-event-level.csv`
- `output/post-freeze-final-main-table-test-r5-seed20260820-details.csv`
- `output/post-freeze-final-main-table-test-r5-seed20260820.json`
- `output/post-freeze-repair-closure-final-main-table-test-r5-seed20260820-summary.csv`
- `output/post-freeze-repair-closure-final-main-table-test-r5-seed20260820-details.csv`
- `output/post-freeze-repair-closure-final-main-table-test-r5-seed20260820.json`

## 4. Main 30-Test Reproduction

Command:

```powershell
& 'G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe' `
  'G:\LearnAI\ontology-evolution\src\run_candidate_information_ablation.py' `
  --split test `
  --runs 5 `
  --seed 20260820 `
  --methods DIRECT_FREE,OPTION_VALUE_ONLY,OPTION_FORMAL_OPERATION,OPTION_FORMAL_POLICY,OPTION_FORMAL_POLICY_HARD_GATE `
  --prefix final-main-table-test-r5-seed20260820 `
  --timeout 180 `
  --num-predict 300 `
  --temperature 0.2
```

Result:

| Method | Attempts | Oracle Accuracy | Wrong Selection | Abstain | Strict Event Success |
|---|---:|---:|---:|---:|---:|
| `DIRECT_FREE` | 150 | 77.33% | 0.00% | 22.67% | 22/30 |
| `OPTION_VALUE_ONLY` | 150 | 81.33% | 8.00% | 10.67% | 22/30 |
| `OPTION_FORMAL_OPERATION` | 150 | 76.00% | 7.33% | 16.67% | 20/30 |
| `OPTION_FORMAL_POLICY` | 150 | 97.33% | 2.67% | 0.00% | 28/30 |
| `OPTION_FORMAL_POLICY_HARD_GATE` | 150 | 100.00% | 0.00% | 0.00% | 30/30 |

Qwen calls:

| Method | Qwen Calls |
|---|---:|
| `DIRECT_FREE` | 150 |
| `OPTION_VALUE_ONLY` | 150 |
| `OPTION_FORMAL_OPERATION` | 150 |
| `OPTION_FORMAL_POLICY` | 150 |
| `OPTION_FORMAL_POLICY_HARD_GATE` | 0 |

Interpretation:

`OPTION_FORMAL_POLICY` is the fair LLM-assisted method in the policy-available prompt setting. `OPTION_FORMAL_POLICY_HARD_GATE` is a symbolic execution upper bound showing that, once policy has been correctly structured, candidate selection can be made deterministic.

Post-freeze reproduction:

The same command was rerun with prefix `post-freeze-final-main-table-test-r5-seed20260820` after the benchmark freeze. It reproduced the same call-level and event-level headline results.

Evidence files:

- `output/final-main-table-test-r5-seed20260820-summary.csv`
- `output/final-main-table-test-r5-seed20260820-event-level.csv`
- `output/final-main-table-test-r5-seed20260820-by-event.csv`
- `output/final-main-table-test-r5-seed20260820-details.csv`
- `output/final-main-table-test-r5-seed20260820.json`
- `output/post-freeze-final-main-table-test-r5-seed20260820-summary.csv`
- `output/post-freeze-final-main-table-test-r5-seed20260820-event-level.csv`
- `output/post-freeze-final-main-table-test-r5-seed20260820-by-event.csv`
- `output/post-freeze-final-main-table-test-r5-seed20260820-details.csv`
- `output/post-freeze-final-main-table-test-r5-seed20260820.json`

## 5. Description Contrast on 30-Test

Command:

```powershell
& 'G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe' `
  'G:\LearnAI\ontology-evolution\src\run_candidate_information_ablation.py' `
  --split test `
  --runs 5 `
  --seed 20260820 `
  --methods OPTION_DESCRIPTION,OPTION_DESCRIPTION_MASKED,OPTION_DESCRIPTION_SHUFFLED `
  --prefix description-contrast-test-r5-seed20260820 `
  --timeout 180 `
  --num-predict 300 `
  --temperature 0.2
```

Result:

| Method | Attempts | Oracle Accuracy | Wrong Selection | Abstain | Strict Event Success |
|---|---:|---:|---:|---:|---:|
| `OPTION_FORMAL_POLICY` | 150 | 97.33% | 2.67% | 0.00% | 28/30 |
| `OPTION_DESCRIPTION` | 150 | 97.33% | 0.67% | 2.00% | 27/30 |
| `OPTION_DESCRIPTION_MASKED` | 150 | 79.33% | 9.33% | 11.33% | 22/30 |
| `OPTION_DESCRIPTION_SHUFFLED` | 150 | 10.00% | 86.00% | 4.00% | 0/30 |
| `OPTION_FORMAL_POLICY_HARD_GATE` | 150 | 100.00% | 0.00% | 0.00% | 30/30 |

Non-strict events for `OPTION_DESCRIPTION`:

| Event | Type | Accuracy | Behavior |
|---|---|---:|---|
| E41 | `CROSS_SENTENCE_SCOPE` | 4/5 | unstable wrong selection in one run |
| E45 | `GENERAL_RULE_EXCEPTION` | 3/5 | two abstains |
| E47 | `CROSS_SENTENCE_SCOPE` | 4/5 | one abstain |

Interpretation:

The description contrast answers an important review question. `OPTION_DESCRIPTION` does not outperform `OPTION_FORMAL_POLICY` on the 30-test reproduction: both reach 97.33% call-level Oracle accuracy, while `OPTION_FORMAL_POLICY` has slightly better strict event success, 28/30 versus 27/30. The shuffled-description collapse to 10.00% shows that human candidate descriptions are not a neutral formatting aid; they are strong semantic guidance. Therefore the safer claim is not that formal policy beats every possible human-description prompt, but that structured formal policy matches the human-description prompt while being executable, auditable, and compatible with deterministic gate checking.

Evidence files:

- `output/description-contrast-test-r5-seed20260820-summary.csv`
- `output/description-contrast-test-r5-seed20260820-event-level.csv`
- `output/description-contrast-test-r5-seed20260820-by-event.csv`
- `output/description-contrast-test-r5-seed20260820-details.csv`
- `output/description-contrast-test-r5-seed20260820.json`
- `output/description-vs-formal-policy-test-r5-seed20260820-summary.csv`

## 6. Formal Policy Source and Complexity Audit

Command:

```powershell
& 'G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe' `
  'G:\LearnAI\ontology-evolution\src\audit_formal_policy_source_costs.py' `
  --split test `
  --prefix formal-policy-source-cost-test
```

Audit result:

| Semantic Type | Events | Manual Formalized | Forbidden Source Markers | Facts | Rules | Conditions | Policy Complexity Points |
|---|---:|---:|---:|---:|---:|---:|---:|
| ALL | 30 | 30 | 0 | 85 | 91 | 117 | 1312 |
| `TEMPORAL_VERSION` | 10 | 10 | 0 | 29 | 30 | 33 | 431 |
| `GENERAL_RULE_EXCEPTION` | 10 | 10 | 0 | 30 | 30 | 45 | 450 |
| `CROSS_SENTENCE_SCOPE` | 10 | 10 | 0 | 26 | 31 | 39 | 431 |

Important limitation:

The complexity column is a heuristic proxy, not measured annotation time:

```text
policy_complexity_points = 8 + 3*facts + 5*rules + 2*conditions + 4*source_docs
```

The formula has not been calibrated against wall-clock annotation data and should not be interpreted as minutes. It is used only to make construction effort visible: hard-gate execution has zero Qwen calls, but formal-policy construction is not free.

Evidence files:

- `output/formal-policy-source-cost-test-details.csv`
- `output/formal-policy-source-cost-test-summary.csv`
- `output/formal-policy-source-cost-test.json`

Annotation-time pilot measurement:

A 12-event annotation-time pilot has been prepared to measure formal-policy construction effort directly. The selected events are balanced across semantic types, four per type: E14/E17/E21/E42 for `TEMPORAL_VERSION`, E27/E30/E31/E45 for `GENERAL_RULE_EXCEPTION`, and E34/E37/E41/E47 for `CROSS_SENTENCE_SCOPE`. The pilot protocol requires annotators to use only public event materials and candidate formal operations during annotation. Oracle checking is explicitly separated and may be filled only after annotation stops.

The completed pilot CSV contains measured annotation minutes for all 12 rows: `measurement_status=MEASURED`, `rows=12`, `measured_rows=12`, and `leakage_flag_rows=0`. The pilot records 361.0 total minutes, 30.08 mean minutes/event, and 30.5 median minutes/event. By semantic type, mean time is 23.5 minutes for `TEMPORAL_VERSION`, 31.25 minutes for `GENERAL_RULE_EXCEPTION`, and 35.5 minutes for `CROSS_SENTENCE_SCOPE`.

All required per-row metadata fields are now filled in the completed CSV. The pilot uses one annotator and reports `unique_gate_decision_rate=100.00%` and post-annotation `oracle_correct_after_blind_check_rate=100.00%`, with no Oracle or model-output access marked during annotation. It remains a small single-annotator, single-benchmark pilot rather than a general annotation-cost model.

Pilot evidence files:

- `benchmark/semantic-v2/annotation-time-pilot/annotation-time-pilot-protocol.md`
- `benchmark/semantic-v2/annotation-time-pilot/annotation-time-pilot-template.csv`
- `benchmark/semantic-v2/annotation-time-pilot/annotation-time-pilot-template.completed.csv`
- `src/analyze_annotation_time_pilot.py`
- `output/annotation-time-pilot-template-check-details.csv`
- `output/annotation-time-pilot-template-check-summary.csv`
- `output/annotation-time-pilot-template-check.json`
- `output/annotation-time-pilot-measured-details.csv`
- `output/annotation-time-pilot-measured-summary.csv`
- `output/annotation-time-pilot-measured.json`
- `output/annotation-time-pilot-completed-details.csv`
- `output/annotation-time-pilot-completed-summary.csv`
- `output/annotation-time-pilot-completed.json`

External-real-v1 preparation:

An `external-real-v1` scaffold has been created for real-world validation events. It is intentionally separate from the controlled `semantic-v2` benchmark and currently contains no READY events. The scaffold includes source-intake templates, public event/document/candidate templates, a private Oracle template, a collection protocol, a document SHA-256 backfill script, and a validator that checks public/private separation, source-document files, SHA-256 consistency, candidate JSON structure, semantic-type distribution, and answer-leakage patterns in public templates.

Current validation status is `PASS` for the empty scaffold: `events_ready=0`, `errors=0`, and `warnings=0`. A scaffold-v2 freeze manifest was also generated: `events_ready=0`, `public_file_count=11`, and `private_integrity_rows=1`. This is preparation for external validation, not an external validation result. The next evidence-bearing step is to add real public document revisions and freeze them before running any model.

External-real-v1 evidence files:

- `benchmark/external-real-v1/protocol.md`
- `benchmark/external-real-v1/source-intake/README.md`
- `benchmark/external-real-v1/source-intake/external-real-source-intake.csv`
- `benchmark/external-real-v1/input/external-real-event-template.csv`
- `benchmark/external-real-v1/input/external-real-document-template.csv`
- `benchmark/external-real-v1/input/external-real-candidate-template.csv`
- `benchmark/external-real-v1/private/external-real-oracle-template.csv`
- `src/validate_external_real_v1.py`
- `src/update_external_real_v1_document_hashes.py`
- `src/generate_external_real_v1_freeze_manifest.py`
- `output/external-real-v1-validation-scaffold-v2-details.csv`
- `output/external-real-v1-validation-scaffold-v2-summary.csv`
- `output/external-real-v1-validation-scaffold-v2.json`
- `output/external-real-v1-freeze-manifest-scaffold-v2-files.csv`
- `output/external-real-v1-freeze-manifest-scaffold-v2-private-oracle.csv`
- `output/external-real-v1-freeze-manifest-scaffold-v2.json`

## 7. Template-Policy Hard Gate

Command:

```powershell
& 'G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe' `
  'G:\LearnAI\ontology-evolution\src\run_template_policy_hard_gate.py' `
  --split test `
  --prefix template-policy-hard-gate-test
```

Result:

| Semantic Type | Accuracy | Wrong | Abstain | Zero Survivor | Multiple Survivors | Event-ID Branch | Manual Slots |
|---|---:|---:|---:|---:|---:|---|---|
| ALL | 100.00% | 0.00% | 0.00% | 0 | 0 | False | True |
| `TEMPORAL_VERSION` | 100.00% | 0.00% | 0.00% | 0 | 0 | False | True |
| `GENERAL_RULE_EXCEPTION` | 100.00% | 0.00% | 0.00% | 0 | 0 | False | True |
| `CROSS_SENTENCE_SCOPE` | 100.00% | 0.00% | 0.00% | 0 | 0 | False | True |

Interpretation:

This addresses only the narrow concern that the executor is literally `if event_id == ...`. The template executor removes event-id branching from decision code, but it still consumes manually structured facts and rule slots. Therefore it is still an upper-bound experiment, not an automatic extraction result.

Evidence files:

- `output/template-policy-hard-gate-test-details.csv`
- `output/template-policy-hard-gate-test-summary.csv`
- `output/template-policy-hard-gate-test.json`

## 8. Zero/Multi-Survivor Stress Test

Command:

```powershell
& 'G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe' `
  'G:\LearnAI\ontology-evolution\src\run_hard_gate_survivor_stress.py' `
  --split test `
  --prefix hard-gate-survivor-stress-test
```

This stress test intentionally perturbs candidate lists or policy slots. It does not call Qwen. Oracle is loaded only after all gate decisions.

Result:

| Variant | Expected Behavior Pass | SAFE_ACCEPT | ABSTAIN | Zero Survivor | Multi Survivor |
|---|---:|---:|---:|---:|---:|
| `CONTROL_UNIQUE` | 30/30 | 30 | 0 | 0 | 0 |
| `ZERO_SURVIVOR_CANDIDATE_MISMATCH` | 30/30 | 0 | 30 | 30 | 0 |
| `NO_APPLICABLE_RULE` | 30/30 | 0 | 30 | 30 | 0 |
| `MULTI_SURVIVOR_DUPLICATE_VALUE` | 30/30 | 0 | 30 | 0 | 30 |
| `MULTI_SURVIVOR_BROAD_ALLOWED_VALUES` | 30/30 | 0 | 30 | 0 | 30 |

Interpretation:

The hard gate fails closed when the policy/candidate interface is not uniquely decidable. It deterministically selects only when exactly one candidate survives. If no rule applies, if policy values do not match candidates, or if multiple candidates survive, the gate returns `ABSTAIN` rather than forcing a selection.

Evidence files:

- `output/hard-gate-survivor-stress-test-details.csv`
- `output/hard-gate-survivor-stress-test-summary.csv`
- `output/hard-gate-survivor-stress-test.json`

## 9. Semantic-Type Grouped Results

Command:

```powershell
& 'G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe' `
  'G:\LearnAI\ontology-evolution\src\analyze_semantic_type_groups.py' `
  --details 'G:\LearnAI\ontology-evolution\output\post-freeze-final-main-table-test-r5-seed20260820-details.csv' `
  --prefix post-freeze-final-main-table-test-r5-seed20260820-semantic-type
```

The 30-test split contains 10 events per semantic type and 50 attempts per method/type pair.

| Method | Temporal Version | General Rule Exception | Cross-Sentence Scope |
|---|---:|---:|---:|
| `DIRECT_FREE` | 72.00%, 6/10 | 80.00%, 8/10 | 80.00%, 8/10 |
| `OPTION_VALUE_ONLY` | 86.00%, 7/10 | 80.00%, 8/10 | 78.00%, 7/10 |
| `OPTION_FORMAL_OPERATION` | 78.00%, 7/10 | 78.00%, 7/10 | 72.00%, 6/10 |
| `OPTION_FORMAL_POLICY` | 100.00%, 10/10 | 98.00%, 9/10 | 94.00%, 9/10 |
| `OPTION_FORMAL_POLICY_HARD_GATE` | 100.00%, 10/10 | 100.00%, 10/10 | 100.00%, 10/10 |

Interpretation:

The LLM-assisted `OPTION_FORMAL_POLICY` method improves all three semantic categories, but residual errors remain in general-rule exceptions and cross-sentence scope. The hard gate remains an upper bound under manually structured policy availability.

Evidence files:

- `output/post-freeze-final-main-table-test-r5-seed20260820-semantic-type-summary.csv`
- `output/post-freeze-final-main-table-test-r5-seed20260820-semantic-type-pivot.csv`
- `output/post-freeze-final-main-table-test-r5-seed20260820-semantic-type.json`

## 10. Runtime and Policy Construction Complexity

Commands:

```powershell
& 'G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe' `
  'G:\LearnAI\ontology-evolution\src\analyze_runtime_and_policy_costs.py' `
  --details 'G:\LearnAI\ontology-evolution\output\post-freeze-final-main-table-test-r5-seed20260820-details.csv' `
  --policy-costs 'G:\LearnAI\ontology-evolution\output\formal-policy-source-cost-test-details.csv' `
  --prefix post-freeze-final-main-table-test-r5-seed20260820-runtime-policy-costs
```

The table separates Qwen inference cost from formal-policy construction complexity. Runtime minutes and policy complexity points are reported separately because their units are different. The policy column is a heuristic proxy from the source/complexity audit, not measured annotation wall time.

| Method | Accuracy | Qwen Calls | Qwen Tokens | Qwen Runtime Min | Policy Complexity Points |
|---|---:|---:|---:|---:|---:|
| `DIRECT_FREE` | 77.33% | 150/150 | 107277 | 8.47 | 0 |
| `OPTION_VALUE_ONLY` | 81.33% | 150/150 | 124400 | 10.16 | 0 |
| `OPTION_FORMAL_OPERATION` | 76.00% | 150/150 | 195646 | 10.80 | 0 |
| `OPTION_FORMAL_POLICY` | 97.33% | 150/150 | 252997 | 10.25 | 1312 |
| `OPTION_FORMAL_POLICY_HARD_GATE` | 100.00% | 0/150 | 0 | 0.00 | 1312 |

Interpretation:

The hard gate has zero Qwen runtime and zero tokens in the decision phase, but it is not a zero-effort method. Under the policy-available setting, both `OPTION_FORMAL_POLICY` and `OPTION_FORMAL_POLICY_HARD_GATE` depend on the same manually structured formal policies. The current audit reports 1312 policy-complexity points for the 30 test policies; it does not report measured annotation minutes.

Evidence files:

- `output/post-freeze-final-main-table-test-r5-seed20260820-runtime-policy-costs-summary.csv`
- `output/post-freeze-final-main-table-test-r5-seed20260820-runtime-policy-costs-by-type.csv`
- `output/post-freeze-final-main-table-test-r5-seed20260820-runtime-policy-costs.json`

## 11. Failure Case Analysis

Command:

```powershell
& 'G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe' `
  'G:\LearnAI\ontology-evolution\src\analyze_failure_cases.py' `
  --details 'G:\LearnAI\ontology-evolution\output\post-freeze-final-main-table-test-r5-seed20260820-details.csv' `
  --prefix post-freeze-final-main-table-test-r5-seed20260820-failure-cases `
  --methods DIRECT_FREE,OPTION_VALUE_ONLY,OPTION_FORMAL_OPERATION,OPTION_FORMAL_POLICY
```

Method-level failure summary:

| Method | Failed Runs | Failed Events | Wrong Selections | Abstains | Focus Failed Events |
|---|---:|---:|---:|---:|---|
| `DIRECT_FREE` | 34/150 | 8 | 0 | 34 | E17, E27, E37, E42, E45, E47 |
| `OPTION_VALUE_ONLY` | 28/150 | 8 | 12 | 16 | E17, E27, E37, E42, E45, E47 |
| `OPTION_FORMAL_OPERATION` | 36/150 | 10 | 11 | 25 | E17, E27, E37, E42, E45, E47 |
| `OPTION_FORMAL_POLICY` | 4/150 | 2 | 4 | 0 | E27, E47 |

Key failure cases:

| Method | Event | Success | Failed Behavior | Oracle Value |
|---|---|---:|---|---:|
| `DIRECT_FREE` | E17/E27/E37/E42/E45/E47 | 0/5 each | abstains because code-mapping cannot be grounded without candidate-level mapping | mixed |
| `OPTION_VALUE_ONLY` | E17 | 1/5 | unstable numeric choices: 58 or 73 instead of 63 | 63 |
| `OPTION_VALUE_ONLY` | E27 | 0/5 | abstains or chooses 0 instead of late-proof 30 | 30 |
| `OPTION_VALUE_ONLY` | E37 | 0/5 | chooses 12 or 24 instead of remote-code 18 | 18 |
| `OPTION_FORMAL_OPERATION` | E27 | 0/5 | often chooses waiver-like 0 rather than late-proof 30 | 30 |
| `OPTION_FORMAL_OPERATION` | E42/E45/E47 | 0/5 each | formal operation does not expose code-to-policy mapping; model abstains | mixed |
| `OPTION_FORMAL_POLICY` | E27 | 4/5 | one LLM policy interpretation chooses 0 instead of 30 | 30 |
| `OPTION_FORMAL_POLICY` | E47 | 2/5 | three runs choose base duration 12 instead of footnote-standard 18 | 18 |

Interpretation:

The residual `OPTION_FORMAL_POLICY` failures show that merely presenting a structured policy to the LLM does not guarantee deterministic execution. E27 and E47 are exactly the cases where rule priority and code-to-candidate mapping must be applied rigidly. This supports the corrected framing: hard gate is useful as a manually structured symbolic upper bound, while `OPTION_FORMAL_POLICY` remains the main LLM-assisted method and still has execution instability.

Evidence files:

- `output/post-freeze-final-main-table-test-r5-seed20260820-failure-cases-by-method.csv`
- `output/post-freeze-final-main-table-test-r5-seed20260820-failure-cases-summary.csv`
- `output/post-freeze-final-main-table-test-r5-seed20260820-failure-cases-failed-runs.csv`
- `output/post-freeze-final-main-table-test-r5-seed20260820-failure-cases.md`
- `output/post-freeze-final-main-table-test-r5-seed20260820-failure-cases.json`

## 12. OWL Repair Closure: Reasoner and CQ Regression

Command:

```powershell
& 'G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe' `
  'G:\LearnAI\ontology-evolution\src\run_semantic_v2_repair_closure.py' `
  --split test `
  --methods DIRECT_FREE,OPTION_VALUE_ONLY,OPTION_FORMAL_OPERATION,OPTION_FORMAL_POLICY,OPTION_FORMAL_POLICY_HARD_GATE `
  --prefix semantic-v2-repair-closure-test-r5-seed20260820 `
  --timeout 120
```

This experiment validates the selected candidate as an OWL repair artifact. For each selected candidate, the script loads the candidate OWL generated by the finite repair operator, reruns the OWL reasoner, checks that the selected operation is actually reflected in RDF triples, and then runs an offline Oracle CQ to verify that the repaired OWL restores the expected target value. Oracle is loaded only after the selection rows are read.

Result:

| Method | Full Repair Closure | Repair Coverage | Reasoner Pass on Selected | Selected-Operation CQ | Oracle Repair CQ on Selected |
|---|---:|---:|---:|---:|---:|
| `DIRECT_FREE` | 77.33% | 77.33% | 100.00% | 100.00% | 100.00% |
| `OPTION_VALUE_ONLY` | 81.33% | 89.33% | 100.00% | 100.00% | 91.04% |
| `OPTION_FORMAL_OPERATION` | 76.00% | 83.33% | 100.00% | 100.00% | 91.20% |
| `OPTION_FORMAL_POLICY` | 97.33% | 100.00% | 100.00% | 100.00% | 97.33% |
| `OPTION_FORMAL_POLICY_HARD_GATE` | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |

Semantic-type full repair closure for `OPTION_FORMAL_POLICY`:

| Semantic Type | Full Repair Closure |
|---|---:|
| `TEMPORAL_VERSION` | 100.00% |
| `GENERAL_RULE_EXCEPTION` | 98.00% |
| `CROSS_SENTENCE_SCOPE` | 94.00% |

Residual `OPTION_FORMAL_POLICY` closure failures:

| Event | Type | Failed Runs | Cause |
|---|---|---:|---|
| E27 | `GENERAL_RULE_EXCEPTION` | 1/5 | selected candidate OWL is consistent and operation CQ passes, but it repairs to 0 rather than Oracle 30 |
| E47 | `CROSS_SENTENCE_SCOPE` | 3/5 | selected candidate OWL is consistent and operation CQ passes, but it repairs to 12 rather than Oracle 18 |

Interpretation:

The repair closure shows that the OWL execution layer is working for the generated candidates: every selected candidate OWL is reasoner-consistent and passes the selected-operation CQ. The remaining failures are semantic selection failures, not OWL patch execution failures. Therefore the current method can now be described as selecting among executable OWL repair candidates and validating the selected artifact with Reasoner and CQ regression. It still should not be described as fully automatic candidate generation from raw documents, because the candidate OWLs are generated from finite candidate operations already present in the benchmark.

Post-freeze reproduction:

The repair-closure command was rerun against `output/post-freeze-final-main-table-test-r5-seed20260820-details.csv` and reproduced the same full-closure headline result: `DIRECT_FREE` 77.33%, `OPTION_VALUE_ONLY` 81.33%, `OPTION_FORMAL_OPERATION` 76.00%, `OPTION_FORMAL_POLICY` 97.33%, and `OPTION_FORMAL_POLICY_HARD_GATE` 100.00%.

Evidence files:

- `src/run_semantic_v2_repair_closure.py`
- `output/semantic-v2-repair-closure-test-r5-seed20260820-details.csv`
- `output/semantic-v2-repair-closure-test-r5-seed20260820-summary.csv`
- `output/semantic-v2-repair-closure-test-r5-seed20260820-by-event.csv`
- `output/semantic-v2-repair-closure-test-r5-seed20260820.json`
- `output/post-freeze-repair-closure-final-main-table-test-r5-seed20260820-details.csv`
- `output/post-freeze-repair-closure-final-main-table-test-r5-seed20260820-summary.csv`
- `output/post-freeze-repair-closure-final-main-table-test-r5-seed20260820-by-event.csv`
- `output/post-freeze-repair-closure-final-main-table-test-r5-seed20260820.json`

## 13. LLM-Extracted Facts + Template Policy

Command:

```powershell
& 'G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe' `
  'G:\LearnAI\ontology-evolution\src\run_llm_extracted_facts_template_policy.py' `
  --split test `
  --runs 5 `
  --seed 20260820 `
  --prefix llm-extracted-facts-template-policy-test-r5-seed20260820 `
  --timeout 180 `
  --num-predict 500 `
  --temperature 0.2
```

This experiment replaces the manually filled fact values with Qwen-extracted fact values, then executes the same template policy gate. The online prompt includes only public case context, target metadata, documents, fact names, and JSON types. It does not include candidate descriptions, rule `allowed_values`, candidate IDs, or Oracle labels.

Important limitation:

This is not automatic rule learning. The fact names/types and template rules remain manually specified. The experiment only tests whether the manually structured `facts` portion can be replaced by LLM extraction from public documents.

Results:

| Group | Oracle Accuracy | Wrong | Abstain | Extraction Error | Fact Match | Qwen Calls | Tokens | Runtime Min |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ALL | 86.00% | 13.33% | 0.67% | 0.00% | 94.12% | 150 | 130541 | 10.07 |
| `TEMPORAL_VERSION` | 88.00% | 10.00% | 2.00% | 0.00% | 93.10% | 50 | 48281 | 4.04 |
| `GENERAL_RULE_EXCEPTION` | 90.00% | 10.00% | 0.00% | 0.00% | 96.67% | 50 | 41615 | 3.12 |
| `CROSS_SENTENCE_SCOPE` | 80.00% | 20.00% | 0.00% | 0.00% | 92.31% | 50 | 40645 | 2.91 |

Non-strict events:

| Event | Type | Accuracy | Behavior | Diagnosis |
|---|---|---:|---|---|
| E15 | `TEMPORAL_VERSION` | 4/5 | one abstain | extracted `entered_after_expiry=true` once, so no rule applied |
| E20 | `TEMPORAL_VERSION` | 0/5 | selected 68 instead of 66 | extracted natural-language region `华东分中心` instead of normalized enum `EAST` |
| E29 | `GENERAL_RULE_EXCEPTION` | 0/5 | selected 90 instead of 45 | extracted natural-language liability label instead of normalized enum `SPECIFIC_CANCER` |
| E40 | `CROSS_SENTENCE_SCOPE` | 0/5 | selected 12 instead of 18 | extracted natural-language plan label instead of normalized enum `ADULT_STANDARD` |
| E46 | `CROSS_SENTENCE_SCOPE` | 0/5 | selected 12 instead of 18 | extracted natural-language tier label instead of normalized enum `STANDARD` |

Interpretation:

This experiment closes the most important methodological gap. The manually structured hard gate reaches 100%, but when the manual fact values are replaced by LLM-extracted values, accuracy drops to 86.00%. The main failure mode is not symbolic rule execution; it is normalization from document wording to the controlled fact vocabulary. Therefore the hard-gate 100% should remain an upper bound under manually structured facts, while a deployable method needs either better automatic fact normalization or an explicit human-in-the-loop fact validation step.

Evidence files:

- `src/run_llm_extracted_facts_template_policy.py`
- `output/llm-extracted-facts-template-policy-test-r5-seed20260820-details.csv`
- `output/llm-extracted-facts-template-policy-test-r5-seed20260820-summary.csv`
- `output/llm-extracted-facts-template-policy-test-r5-seed20260820-by-event.csv`
- `output/llm-extracted-facts-template-policy-test-r5-seed20260820.json`

## 14. Threats to Validity

Construction-cost validity:

The policy construction score is a complexity proxy, not measured human annotation time. The formula is transparent and reproducible, but it has not been calibrated with annotation logs. It may underestimate non-linear effects such as domain expertise, cross-sentence evidence search, conflict resolution, and semantic-type-specific difficulty. A stronger future study should collect wall-clock annotation time on a pilot subset and report inter-annotator variation.

External validity:

The 30-test benchmark is a controlled diagnostic challenge set. It is useful for isolating semantic-drift failure modes, but it is not evidence that the method generalizes to arbitrary real insurance policies or unseen product revisions. A stronger paper artifact should add an externally sourced validation set from real policy documents, product clauses, or historical revisions, even if the additional set is smaller.

Freeze validity:

The current benchmark now has a local freeze commit, `688e6cdd8a78327e89358846dbfbe61eac2271d3`. This improves reproducibility for future reruns, but it was created after the development experiments already reported here. Therefore it does not retroactively prove that earlier Qwen results were produced after a freeze. For final submission, rerun headline Qwen experiments from this commit and archive the benchmark with an external timestamp or DOI.

Automation validity:

`LLM_EXTRACTED_FACTS_TEMPLATE_POLICY` only replaces manual fact values with Qwen-extracted fact values. Fact names, fact types, and rule templates remain manually specified. The 86.00% result shows that automatic fact normalization is not solved. This experiment must not be described as automatic policy extraction or automatic rule construction.

Repair-closure validity:

The repair closure validates selected finite repair candidates as executable OWL artifacts using Reasoner and CQ regression. It does not prove fully automatic repair candidate generation from raw documents. Candidate operations and candidate OWLs are generated from the benchmark candidate set, and the method's main contribution remains evidence-constrained selection and validation among those candidates.

Relation to symbolic rule engines:

The hard gate is a lightweight symbolic rule executor, not a new general-purpose rule engine. SHACL, SWRL, or other rule engines could implement similar manually specified constraints; if they receive the same hand-built rules and facts, they would mainly test executor equivalence rather than document understanding. Decision-tree or rule-learning baselines would require a separate training set, which this 30-test benchmark does not provide. The current contribution is therefore best framed as evidence that structured formal policy constrains LLM semantic selection and exposes the remaining bottleneck in fact normalization and rule construction.

## 15. Remaining Gaps

The largest remaining gaps are now narrower:

1. Automatic normalization of extracted natural-language facts into the controlled symbolic vocabulary.
2. Automatic or semi-automatic construction of template rules from policy documents.
3. Populate `external-real-v1` with real public revision events, because the current 30-test set is still controlled and partly synthetic and the new external scaffold has 0 READY events.
4. A larger multi-annotator cost study, because the completed annotation-time pilot is still small and single-benchmark.
5. A separate study comparing implementation carriers such as SHACL/SWRL, if the paper wants to make claims about symbolic-rule execution infrastructure.
6. Fully automatic repair-candidate generation from raw document changes, because the current closure validates executable candidates already generated from finite operations.

## 16. Paper-Safe Claims

Safe:

- Direct LLM and weak candidate baselines remain unstable on controlled semantic-drift events.
- Structured policy input improves LLM selection substantially: `OPTION_FORMAL_POLICY` reaches 97.33% call-level Oracle accuracy and 28/30 strict event success.
- `OPTION_FORMAL_POLICY` matches the human-description prompt at 97.33% call-level accuracy and has slightly better strict event success, 28/30 versus 27/30.
- Shuffling candidate descriptions collapses accuracy to 10.00%, showing that artificial candidate descriptions are strong semantic supervision rather than neutral formatting.
- The selected repair artifacts are executable OWL candidates: selected candidates pass Reasoner and selected-operation CQ at 100%, while full repair closure tracks semantic selection accuracy.
- The 30-test semantic-type grouping shows `OPTION_FORMAL_POLICY` improves all three semantic categories, while still below the manually structured hard-gate upper bound.
- The 30-test failure analysis shows the remaining `OPTION_FORMAL_POLICY` errors are concentrated in code-mapping / rule-execution cases E27 and E47.
- Given manually structured formal policies, deterministic symbolic execution reaches the policy-available upper bound of 100%.
- Replacing manual fact values with Qwen-extracted facts drops template-policy accuracy to 86.00%, showing that automatic fact normalization is the current bottleneck.
- In zero-survivor and multi-survivor stress tests, the deterministic gate fails closed with `ABSTAIN` rather than forcing an unsafe selection.
- The upper bound has zero Qwen runtime calls but non-zero manual construction effort; in the current heuristic audit this is 1312 policy-complexity points for 30 test policies, not measured minutes.
- The frozen benchmark state now has a completed post-freeze Qwen headline rerun and a post-freeze repair-closure rerun.

Unsafe:

- Claiming hard gate is a fully automatic method.
- Claiming hard gate has zero total cost.
- Claiming the current benchmark proves generalization to unseen real-world policies.
- Claiming the freeze commit proves the benchmark was frozen before earlier development experiments; it only fixes the state for future reruns.
- Claiming template policy solves automatic rule construction; it only removes event-id branching from executor code.
- Claiming automatic fact extraction is solved; the 30-test extraction experiment shows normalization failures on E20, E29, E40, and E46.
- Claiming `policy_complexity_points` are measured annotation minutes or validated human labor estimates.
- Claiming the current closure proves fully automatic OWL repair from raw documents; it validates selected finite repair candidates, not automatic candidate generation.
