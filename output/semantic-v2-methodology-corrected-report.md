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

An `external-real-v1` validation benchmark has been expanded to 30 READY events and kept separate from the controlled `semantic-v2` diagnostic benchmark. It now uses three public source families: Beijing Municipal Agriculture and Rural Affairs Bureau agricultural-insurance policy PDFs, W3C WCAG 2.1/2.2 public standard/change-summary pages, and NIST SP 800-63-3/800-63-4 public guideline pages. Each event has public event metadata, public source documents with SHA-256 hashes, three public candidate operations, a mutant OWL, candidate OWL artifacts, an executable formal-policy JSON file, and a private Oracle row.

External-real-v1 30-event distribution:

| Semantic Type | Events |
|---|---:|
| `TEMPORAL_VERSION` | 10 |
| `GENERAL_RULE_EXCEPTION` | 10 |
| `CROSS_SENTENCE_SCOPE` | 10 |
| total | 30 |

Public/private timeline:

- Existing Beijing PDF events `EXT_E001` through `EXT_E003` were public-frozen first and then adjudicated with private Oracle rows.
- The 27 added public events `EXT_E004` through `EXT_E030` were generated from public W3C/NIST source pages and frozen in Git commit `0635ef5` before their new private Oracle rows were appended.
- During the first 30-event closure run, `EXT_E009`, `EXT_E010`, and `EXT_E015` abstained because the generated distractor `CAND_003` duplicated the correct value of `CAND_002`. This was a benchmark-construction defect, not a model failure. The generator was corrected to make distractor levels non-duplicate, public artifacts were regenerated, and the correction is retained as an auditable post-freeze construction correction.

External-real-v1 30-event validation:

- Command: `G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe src\validate_external_real_v1.py --prefix external-real-v1-validation-30-private-oracle-fixed`
- Command: `G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe src\generate_external_real_v1_freeze_manifest.py --prefix external-real-v1-freeze-manifest-30-private-oracle-fixed --include-built`
- Validation result: `events_ready=30`, `private_oracle_rows=30`, `errors=0`, `warnings=0`, `status=PASS`.
- Manifest result: `events_ready=30`, `public_file_count=381`, `private_integrity_rows=31`, public freeze base commit `0635ef58a8cb675fd268083add1569dfe3a893b7`, `git_dirty=True` because private Oracle/results/report files were intentionally generated after the public freeze.

External-real-v1 symbolic closure:

- Command: `G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe src\run_external_real_v1_symbolic_closure.py --only EXT_E001,EXT_E002,EXT_E003,EXT_E004,EXT_E005,EXT_E006,EXT_E007,EXT_E008,EXT_E009,EXT_E010,EXT_E011,EXT_E012,EXT_E013,EXT_E014,EXT_E015,EXT_E016,EXT_E017,EXT_E018,EXT_E019,EXT_E020,EXT_E021,EXT_E022,EXT_E023,EXT_E024,EXT_E025,EXT_E026,EXT_E027,EXT_E028,EXT_E029,EXT_E030 --prefix external-real-v1-symbolic-closure-30-fixed`
- Result: selected repairs `30/30`, Oracle correct `30/30`, full OWL repair closure `30/30`.
- Closure details: every selected candidate removed one old triple, added one new triple, passed the Reasoner gate, triggered the source repair CQ, and satisfied the candidate repair CQ.
- Boundary: this is an external-real smoke validation with public source documents and balanced semantic types. It is stronger than the earlier 3-event smoke check, but it is still not a large independently annotated external benchmark. The W3C/NIST events are script-formalized from official public change-summary material, so the paper should describe this as public-source external validation, not as a fully independent real-world corpus.

External-real-v1 candidate-information ablation:

- Command: `G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe src\run_external_real_v1_candidate_ablation.py --runs 5 --seed 20260820 --methods DIRECT_FREE,OPTION_VALUE_ONLY,OPTION_FORMAL_OPERATION,OPTION_FORMAL_POLICY,OPTION_FORMAL_POLICY_HARD_GATE --prefix external-real-v1-candidate-ablation-r5-seed20260820`
- Result: 30 events, 5 runs, 5 methods, 750 total method attempts.

| Method | Attempts | Oracle Accuracy | Strict Event Success | Qwen Calls | Qwen Tokens | Qwen Runtime | Policy Complexity |
|---|---:|---:|---:|---:|---:|---:|---:|
| `DIRECT_FREE` | 150 | 91.33% | 27/30 | 150 | 151254 | 8.94 min | 0 |
| `OPTION_VALUE_ONLY` | 150 | 99.33% | 29/30 | 150 | 167651 | 8.19 min | 0 |
| `OPTION_FORMAL_OPERATION` | 150 | 98.00% | 28/30 | 150 | 250537 | 8.28 min | 0 |
| `OPTION_FORMAL_POLICY` | 150 | 100.00% | 30/30 | 150 | 299019 | 7.31 min | 1330 |
| `OPTION_FORMAL_POLICY_HARD_GATE` | 150 | 100.00% | 30/30 | 0 | 0 | 0.00 min | 1330 |

Semantic-type grouping on external-real-v1 shows `OPTION_FORMAL_POLICY` and `OPTION_FORMAL_POLICY_HARD_GATE` reach 100% in all three groups. `DIRECT_FREE` remains weakest on `GENERAL_RULE_EXCEPTION`, with 74.00% call-level accuracy. The policy complexity score is the same heuristic point unit used in the controlled benchmark and is not measured annotation time.

External-real-v1 automatic formal-policy construction:

`AUTO_POLICY_V2_TYPE_AWARE_CANDIDATE_BLIND` was the first candidate-blind automatic-policy construction run.

- Input boundary: public evidence only; no Oracle, no candidate IDs/values, no manually written formal-policy file.
- Generation result: 150/150 generated policies, 0 abstains, 0 forbidden outputs, 0 invalid JSON, 0 invalid schema.
- Semantic evaluation result: 124/150 semantically correct, 82.67% call-level semantic accuracy, 13/30 strict event success.
- Semantic-type accuracy: `TEMPORAL_VERSION` 78.00%, `GENERAL_RULE_EXCEPTION` 94.00%, `CROSS_SENTENCE_SCOPE` 76.00%.
- Candidate repair result: selected 124/150, Oracle correct 124/150, full OWL closure 124/150, strict event success 13/30.

V2 diagnosis:

V2 removed candidate and Oracle access, but it still asked Qwen to emit a free-form natural-language `semantic_result`. Most failures were not caused by OWL repair execution. They were caused by insufficient normalization of the generated semantics into the controlled vocabulary required by candidate repair, especially WCAG criterion IDs, conformance levels, and scope/input-rule families.

`AUTO_POLICY_V3_CANONICAL_CANDIDATE_BLIND` addresses that failure mode without changing the benchmark, candidate set, or Oracle.

- Input boundary: same as V2; public evidence only, no Oracle, no candidate IDs/values, no manually written formal-policy file.
- Method change: the prompt requires a structured `canonical_result`; the script then deterministically normalizes that object plus public event metadata/evidence into a controlled `semantic_result`.
- Generation result: 150/150 generated policies, 0 abstains, 0 forbidden outputs, 0 invalid schema, 0 unresolved canonical results.
- Average Qwen runtime: 12.17 seconds per call.
- Semantic evaluation result: 150/150 semantically correct, 100.00% call-level semantic accuracy, 30/30 strict event success.
- Semantic-type accuracy: `TEMPORAL_VERSION` 100.00%, `GENERAL_RULE_EXCEPTION` 100.00%, `CROSS_SENTENCE_SCOPE` 100.00%.
- Candidate repair result: selected 150/150, Oracle correct 150/150, full OWL closure 150/150, strict event success 30/30, abstains 0/150.

V3 component ablation:

- Command: `G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe src\run_auto_formal_policy_batch_v3_no_normalizer.py`
- Command: `G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe src\run_auto_policy_v3_component_ablation.py --prefix auto-policy-v3-component-ablation-r5-seed20260820`

| Variant | Qwen | Canonical Schema | Deterministic Normalizer | Metadata/Evidence Only | Semantic Accuracy | Repair Closure | Strict Events | Abstains |
|---|---|---|---|---|---:|---:|---:|---:|
| `V2_FREE_TEXT` | yes | no | no | no | 82.67% | 82.67% | 13/30 | 26/150 |
| `V3_NO_NORMALIZER` | yes | yes | no | no | 36.67% | 36.67% | 8/30 | 95/150 |
| `V3_FULL` | yes | yes | yes | no | 100.00% | 100.00% | 30/30 | 0/150 |
| `METADATA_EVIDENCE_ONLY_NORMALIZER` | no | yes | yes | yes | 100.00% | 100.00% | 30/30 | 0/150 |

Component interpretation:

The `V3_NO_NORMALIZER` result shows that asking Qwen for a canonical schema is not enough. Although it generated 150/150 valid JSON policies, its own emitted `semantic_result` strings were often not consumable by the candidate selector, dropping repair closure to 36.67%. Therefore deterministic canonical normalization is a necessary component, not a cosmetic post-processing step.

The `METADATA_EVIDENCE_ONLY_NORMALIZER` result is a deliberate leakage/templating probe. It does not read candidates, Oracle, or manual formal-policy files, but it also does not call Qwen. Its 100.00% result shows that the current external-real-v1 evidence notes and event metadata are highly structured enough for the deterministic normalizer to solve the benchmark alone. This does not invalidate V3, but it narrows the safe claim: external-real-v1 currently demonstrates the value of controlled canonicalization and executable repair closure, while stronger natural-language document-understanding claims require less templated evidence and perturbation robustness experiments.

V3 robustness experiment:

- Command: `G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe src\run_auto_policy_v3_robustness.py --prefix auto-policy-v3-robustness-r3-seed20260827`
- The final summary was regenerated after fixing a summary-path bug with: `G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe src\run_auto_policy_v3_robustness.py --skip-generation --skip-evaluation --prefix auto-policy-v3-robustness-r3-seed20260827`

| Variant | Metadata | Evidence Perturbation | Attempts | Semantic Accuracy | Repair Closure | Strict Events | Abstains |
|---|---|---|---:|---:|---:|---:|---:|
| `ORDER_SHUFFLE_FULL_METADATA` | full | shuffled evidence lines | 90 | 100.00% | 100.00% | 30/30 | 0/90 |
| `LESS_TEMPLATED_METADATA_LIGHT` | light | paragraph rewrite | 90 | 100.00% | 100.00% | 30/30 | 0/90 |
| `DISTRACTOR_METADATA_LIGHT` | light | paragraph rewrite plus irrelevant distractors | 90 | 61.11% | 61.11% | 14/30 | 11/90 |

Robustness interpretation:

V3 is robust to evidence order changes and to a less-templated paragraph rewrite when no misleading distractor is added. However, with metadata-light input and irrelevant but semantically similar distractor sentences, repair closure drops to 61.11%. The failures are concentrated in WCAG temporal-version and cross-sentence-scope events, where distractors mention alternative conformance levels, older statuses, or deferred changes. Therefore V3 should be described as robust to formatting perturbation, but not yet robust to adversarial or near-miss evidence contamination. The next method improvement should add provenance-aware evidence filtering, explicit conflict resolution, or target-scoped citation grounding before canonical normalization.

V3 negative safety experiment:

- Command: `G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe src\run_auto_policy_v3_negative_safety.py --prefix auto-policy-v3-negative-safety-r1-seed20260827`
- Boundary: generation remains candidate-blind and Oracle-blind. Oracle is loaded only after all selections are fixed. In this experiment, every negative variant is expected to fail closed; therefore any selected candidate is counted as an unsafe selection, even if it happens to match the original Oracle.

| Negative Variant | Attempts | Safe Abstain | Unsafe Selection | Unsafe Wrong Selection | Main Meaning |
|---|---:|---:|---:|---:|---|
| `MISSING_KEY_FIELD` | 30 | 86.67% | 13.33% | 0.00% | mostly fails closed when key semantic fields are removed |
| `CONFLICTING_EVIDENCE` | 30 | 20.00% | 80.00% | 36.67% | does not reliably detect explicit conflicts |
| `NO_MATCHING_CANDIDATE` | 30 | 76.67% | 23.33% | 0.00% | often fails closed when generated semantics have no candidate match |
| `DISTRACTOR_DOMINATES` | 30 | 3.33% | 96.67% | 0.00% | usually selects despite dominant misleading distractors |
| `MULTIPLE_MATCHING_CANDIDATES` | 30 | 100.00% | 0.00% | 0.00% | candidate selector correctly fails closed on multi-survivor ambiguity |
| ALL | 150 | 57.33% | 42.67% | 7.33% | current V3 lacks a sufficient conflict/uncertainty gate |

Negative-safety interpretation:

The candidate selector behaves correctly for `MULTIPLE_MATCHING_CANDIDATES`: when more than one candidate matches, it returns `ABSTAIN`. The generation-plus-normalization layer is weaker. It mostly fails closed when key fields are removed, and often fails closed when no candidate matches, but it does not reliably abstain under conflicting evidence or dominant near-miss distractors. This means V3 cannot yet support the strong claim "safe under uncertainty." The safer claim is: V3 has a deterministic multi-survivor fail-closed mechanism at the candidate-selection layer, but still needs evidence conflict detection and confidence gating before canonical normalization.

V3 conflict/uncertainty gate:

- Command: `G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe src\run_auto_policy_v3_conflict_gate.py --prefix auto-policy-v3-conflict-gate-r1-seed20260827`
- The gate does not call Qwen. It reads only generated policy status, generated canonical semantic output, and candidate-blind evidence files. It does not read candidate values or Oracle before the gate decision.
- Gate checks: missing-key markers, unresolved canonical outputs, explicit conflict markers, dominant distractor markers, and simple WCAG code/level ambiguity under conflict markers. Candidate multi-survivor ambiguity is still handled by the existing selector.

Dataset-level result:

| Dataset | Attempts | Gate Abstain | Selected | Oracle Accuracy | Safe Abstain | Unsafe Selection |
|---|---:|---:|---:|---:|---:|---:|
| Normal V3 | 150 | 0.00% | 100.00% | 100.00% | n/a | n/a |
| Negative Safety | 150 | 64.67% | 1.33% | 1.33% | 98.67% | 1.33% |

Negative variant result after gate:

| Negative Variant | Safe Abstain | Unsafe Selection |
|---|---:|---:|
| `MISSING_KEY_FIELD` | 96.67% | 3.33% |
| `CONFLICTING_EVIDENCE` | 100.00% | 0.00% |
| `NO_MATCHING_CANDIDATE` | 96.67% | 3.33% |
| `DISTRACTOR_DOMINATES` | 100.00% | 0.00% |
| `MULTIPLE_MATCHING_CANDIDATES` | 100.00% | 0.00% |

Gate interpretation:

The gate fixes the main safety weakness exposed by the negative-safety experiment: safe abstain improves from 57.33% to 98.67%, while the normal V3 set remains at 100.00% selection accuracy with no gate-induced abstains. The two remaining unsafe selections are both `EXT_E003` formula cases where the negative evidence construction did not actually remove or alter the English formula evidence, so the gate has no candidate-blind uncertainty signal to use. This result supports a stronger but still bounded claim: V3 can be made fail-closed under the tested uncertainty markers by adding a lightweight evidence gate, but this is still a rule-based uncertainty detector and should be validated on less synthetic conflict evidence.

V3 statistical analysis:

- Command: `G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe src\analyze_auto_policy_v3_statistics.py`
- Method: Wilson 95% confidence intervals for binomial proportions; exact paired McNemar/binomial tests on matched event-run pairs.

| Result | Estimate | Wilson 95% CI | Paired Test |
|---|---:|---:|---:|
| V2 repair closure | 124/150 = 82.67% | 75.81%-87.89% | baseline |
| V3 repair closure | 150/150 = 100.00% | 97.50%-100.00% | vs V2: discordant 26, p=2.98e-08 |
| V3 distractor robustness closure | 55/90 = 61.11% | 50.78%-70.53% | descriptive |
| V3+gate normal-set Oracle accuracy | 150/150 = 100.00% | 97.50%-100.00% | gate does not reduce normal-set accuracy in this run |
| V3+gate negative safe abstain | 148/150 = 98.67% | 95.27%-99.63% | vs ungated negative safety: discordant 62, p=8.47e-16 |

Statistical interpretation:

The V3 repair-closure gain over V2 is not just a percentage artifact: on matched event-run pairs, all 26 discordant cases favor V3. The gate improvement is also paired: 62 discordant negative cases favor the gate and none favor the ungated baseline. These tests support the internal benchmark claim that canonical normalization and uncertainty gating materially improve this pipeline. They do not remove the external-validity limits already stated above, because all confidence intervals and paired tests are conditional on the current 30-event external-real-v1 benchmark design.

Paper-level main result table:

- Command: `G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe src\build_paper_experiment_main_table.py`
- Output: `output/paper-experiment-main-table.csv`, `output/paper-experiment-main-table.md`, and `output/paper-experiment-main-table.json`.

| Method | Paper Role | Manual Policy | Qwen Calls | Qwen Tokens | Policy Cost | Selection Accuracy | 95% CI | Strict Events | OWL Repair Closure | Negative Safety | Statistical Note |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| `DIRECT_FREE` | Direct baseline | No | 150 | 151254 | 0 | 91.33% (137/150) | 85.74%-94.87% | 27/30 | not run in this ablation | not tested | descriptive |
| `OPTION_VALUE_ONLY` | Candidate-value baseline | No | 150 | 167651 | 0 | 99.33% (149/150) | 96.32%-99.88% | 29/30 | not run in this ablation | not tested | descriptive |
| `OPTION_FORMAL_OPERATION` | Formal-operation baseline | No | 150 | 250537 | 0 | 98.00% (147/150) | 94.29%-99.32% | 28/30 | not run in this ablation | not tested | descriptive |
| `OPTION_FORMAL_POLICY` | Policy-available LLM upper baseline | Yes | 150 | 299019 | 1330 | 100.00% (150/150) | 97.50%-100.00% | 30/30 | not run per attempt; candidate artifacts are executable | not tested | descriptive |
| `OPTION_FORMAL_POLICY_HARD_GATE` | Policy-available symbolic upper bound | Yes | 0 | 0 | 1330 | 100.00% (150/150) | 97.50%-100.00% | 30/30 | 30/30 symbolic closure | zero/multi-survivor stress only | descriptive |
| `AUTO_POLICY_V2` | Candidate-blind automatic policy baseline | No | 150 | 177471 | 0 | 82.67% (124/150) | 75.81%-87.89% | 13/30 | 82.67% (124/150) | not tested | paired baseline for V3 |
| `AUTO_POLICY_V3` | Main automatic policy method | No | 150 | 210869 | 0 | 100.00% (150/150) | 97.50%-100.00% | 30/30 | 100.00% (150/150) | ungated safe abstain 57.33%; unsafe 42.67% | vs V2 p=2.98e-08 |
| `AUTO_POLICY_V3_PLUS_GATE` | Main method with uncertainty gate | No | 150 | 210869 | 0 | 100.00% (150/150) | 97.50%-100.00% | 30/30 normal set | 100.00% (150/150) | safe abstain 98.67% (148/150); unsafe 1.33% | gate vs ungated p=8.47e-16; safe-abstain CI 95.27%-99.63% |

Main-table interpretation:

This table is the current paper-safe headline view. It separates candidate-selection accuracy from executable OWL repair closure because the older candidate-information ablations were not all connected to per-attempt Reasoner/CQ closure. The cleanest main-method claim is therefore about `AUTO_POLICY_V3_PLUS_GATE`: it preserves 100.00% normal-set candidate selection and V3 repair closure on the 30-event external-real-v1 benchmark, while improving fail-closed behavior on the tested negative variants. The table also makes the manual-policy upper-bound status explicit: `OPTION_FORMAL_POLICY` and `OPTION_FORMAL_POLICY_HARD_GATE` reach 100.00%, but they require 1330 heuristic policy-complexity points and must not be described as fully automatic.

V3 failure-case analysis:

- Command: `G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe src\build_auto_policy_v3_failure_case_analysis.py`
- Output: `output/auto-policy-v3-failure-case-analysis.md` plus CSV tables for distractor, ungated negative, gate residual, by-type, and by-variant views.

Failure/unsafe summary by semantic type:

| Source | Semantic Type | Failed Events | Failures/Unsafe | Attempts | Rate |
|---|---|---:|---:|---:|---:|
| distractor robustness | `TEMPORAL_VERSION` | 6/10 | 15 | 30 | 50.00% |
| distractor robustness | `CROSS_SENTENCE_SCOPE` | 7/10 | 15 | 30 | 50.00% |
| distractor robustness | `GENERAL_RULE_EXCEPTION` | 3/10 | 5 | 30 | 16.67% |
| ungated negative safety | `TEMPORAL_VERSION` | 10/10 | 18 | 50 | 36.00% |
| ungated negative safety | `CROSS_SENTENCE_SCOPE` | 10/10 | 19 | 50 | 38.00% |
| ungated negative safety | `GENERAL_RULE_EXCEPTION` | 9/10 | 27 | 50 | 54.00% |
| V3+gate residual unsafe | `TEMPORAL_VERSION` | 0/10 | 0 | 50 | 0.00% |
| V3+gate residual unsafe | `CROSS_SENTENCE_SCOPE` | 0/10 | 0 | 50 | 0.00% |
| V3+gate residual unsafe | `GENERAL_RULE_EXCEPTION` | 1/10 | 2 | 50 | 4.00% |

Ungated negative safety unsafe selections by variant:

| Variant | Unsafe Events | Unsafe Selections | Attempts | Unsafe Rate |
|---|---:|---:|---:|---:|
| `CONFLICTING_EVIDENCE` | 24/30 | 24 | 30 | 80.00% |
| `DISTRACTOR_DOMINATES` | 29/30 | 29 | 30 | 96.67% |
| `MISSING_KEY_FIELD` | 4/30 | 4 | 30 | 13.33% |
| `NO_MATCHING_CANDIDATE` | 7/30 | 7 | 30 | 23.33% |
| `MULTIPLE_MATCHING_CANDIDATES` | 0/30 | 0 | 30 | 0.00% |

Residual unsafe events after gate:

| Variant | Event | Semantic Type | Unsafe Selections | Gate Reasons | Failure Mode |
|---|---|---|---:|---|---|
| `MISSING_KEY_FIELD` | `EXT_E003` | `GENERAL_RULE_EXCEPTION` | 1 | empty | negative construction left a normal-looking formula signal; the candidate-blind gate had no uncertainty marker |
| `NO_MATCHING_CANDIDATE` | `EXT_E003` | `GENERAL_RULE_EXCEPTION` | 1 | empty | negative construction left a normal-looking formula signal; the candidate-blind gate had no uncertainty marker |

Failure-case interpretation:

The distractor robustness failures are not evenly distributed. They are concentrated in metadata-light WCAG temporal-version and cross-sentence-scope rows, where near-miss criterion/status/level evidence or scope-adjacent text can dominate the canonical result. Ungated V3 is weakest under `DISTRACTOR_DOMINATES` and `CONFLICTING_EVIDENCE`, which shows that canonical normalization alone is not a safety mechanism. The conflict/uncertainty gate removes all unsafe selections for temporal-version and cross-sentence-scope negative variants, leaving only two `EXT_E003` formula cases. Those residual cases are better interpreted as a negative-case construction weakness: the altered evidence still contained a normal-looking formula signal, so a candidate-blind uncertainty gate had no observable reason to abstain.

Method flow and input-isolation diagrams:

- Command: `G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe src\build_method_flow_input_isolation_diagrams.py`
- Output: `output/method-flow-and-input-isolation-diagrams.md`, plus standalone Mermaid files for method flow, input isolation, and policy-boundary diagrams.

Figure usage:

| Figure | File | Paper Use | Main Message |
|---|---|---|---|
| Auto Policy V3 and V3+Gate pipeline | `output/auto-policy-v3-method-flow.mmd` | Method section | public evidence -> candidate-blind canonical generation -> deterministic normalization -> uncertainty gate -> candidate repair -> Reasoner/CQ -> offline Oracle metrics |
| Input isolation by stage | `output/auto-policy-v3-input-isolation.mmd` | Validity/leakage section | Qwen generation sees public evidence and target metadata only; candidate operations are used only after semantic result is fixed; Oracle is loaded only for metrics |
| Manual-policy upper bound versus automatic V3 | `output/auto-policy-v3-policy-boundary.mmd` | Discussion/ablation section | `OPTION_FORMAL_POLICY_HARD_GATE` is a policy-available symbolic upper bound, while `AUTO_POLICY_V3_PLUS_GATE` is the automatic candidate-blind method line |

Input-isolation interpretation:

The diagrams make the leakage boundary explicit. During V3 generation, Qwen does not receive candidate IDs, candidate values, candidate operations, manual formal-policy facts/rules, or Oracle labels. The deterministic normalizer uses the generated canonical result, public event metadata, public evidence, and a predefined canonical vocabulary. The uncertainty gate is also candidate-blind: it checks generated status, canonical output, and evidence uncertainty markers before candidate values are exposed. Candidate operations and candidate OWL artifacts are used only after the semantic result is fixed. The private Oracle is loaded only after selection and repair-closure rows exist, so Oracle access is restricted to evaluation.

Reproducibility package:

- Command: `G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe src\build_reproducibility_package.py`
- Output: `output/reproducibility-package.md` and `output/reproducibility-package.json`.

The reproducibility package contains two run paths. The full reproduction path starts from benchmark validation and runs the Qwen-dependent experiments, with approximately 1440 expected Qwen calls under the current script settings. The faster offline rebuild path assumes existing raw Qwen outputs and regenerates candidate repair closure, robustness/negative summaries, conflict gate results, statistics, main table, failure analysis, and diagrams without intentionally calling Qwen. It also records expected headline checks so a rerun can be compared against the current report.

Paper experiment section draft:

- Command: `G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe src\build_paper_experiment_section_draft.py`
- Output: `output/paper-experiment-section-draft.md` and `output/paper-experiment-section-draft.json`.

The draft organizes the current evidence into paper-facing subsections: experimental setup, benchmark, compared methods, metrics, input isolation, main results, component ablation, robustness, safety analysis, failure cases, threats to validity, and reproducibility. It is intentionally conservative: candidate selection and executable OWL repair closure are separated, manual policy is treated as an upper-bound condition, and the external-validity and safety-generalization limits remain explicit.

Interpretation:

This is now the strongest answer to the manual-rule leakage concern. The policy-available `OPTION_FORMAL_POLICY` and hard gate still show what happens when structured policy is provided manually. Auto Policy V3 shows that, on the 30-event external-real-v1 public-source validation set, a candidate-blind and Oracle-blind construction step can produce normalized semantic policy outputs that select executable OWL repairs and pass Reasoner plus CQ regression. The safe claim is still narrower than "fully automatic rule learning": V3 uses a predefined domain-level canonical vocabulary and deterministic normalization layer, not an automatically learned symbolic rule language.

External-real-v1 evidence files:

- `src/extend_external_real_v1_to_30.py`
- `src/validate_external_real_v1.py`
- `src/generate_external_real_v1_freeze_manifest.py`
- `src/generate_external_real_v1_repair_artifacts.py`
- `src/run_external_real_v1_symbolic_closure.py`
- `src/run_external_real_v1_candidate_ablation.py`
- `src/audit_external_real_v1_policy_costs.py`
- `src/run_auto_formal_policy_batch_v2.py`
- `src/run_auto_formal_policy_batch_v3.py`
- `src/run_auto_policy_v3_robustness.py`
- `src/run_auto_policy_v3_negative_safety.py`
- `src/run_auto_policy_v3_conflict_gate.py`
- `src/analyze_auto_policy_v3_statistics.py`
- `src/build_paper_experiment_main_table.py`
- `src/build_auto_policy_v3_failure_case_analysis.py`
- `src/build_method_flow_input_isolation_diagrams.py`
- `src/build_reproducibility_package.py`
- `src/build_paper_experiment_section_draft.py`
- `src/evaluate_auto_formal_policy_v2_semantic.py`
- `src/run_auto_policy_v2_candidate_repair.py`
- `benchmark/external-real-v1/source-intake/external-real-source-intake.csv`
- `benchmark/external-real-v1/input/external-real-event-template.csv`
- `benchmark/external-real-v1/input/external-real-document-template.csv`
- `benchmark/external-real-v1/input/external-real-candidate-template.csv`
- `benchmark/external-real-v1/private/external-real-oracle-template.csv`
- `output/external-real-v1-validation-30-public-ready-summary.csv`
- `output/external-real-v1-freeze-manifest-30-public-ready.json`
- `output/external-real-v1-symbolic-closure-30-summary.csv` construction-correction audit, first run `27/30`
- `output/external-real-v1-symbolic-closure-30-details.csv` construction-correction audit, duplicate distractor rows
- `output/external-real-v1-validation-30-private-oracle-fixed-summary.csv`
- `output/external-real-v1-freeze-manifest-30-private-oracle-fixed.json`
- `output/external-real-v1-symbolic-closure-30-fixed-summary.csv`
- `output/external-real-v1-symbolic-closure-30-fixed-details.csv`
- `output/external-real-v1-symbolic-closure-30-fixed.json`
- `output/external-real-v1-candidate-ablation-r5-seed20260820-summary.csv`
- `output/external-real-v1-candidate-ablation-r5-seed20260820-event-level.csv`
- `output/external-real-v1-candidate-ablation-r5-seed20260820-semantic-type-groups-summary.csv`
- `output/external-real-v1-candidate-ablation-r5-seed20260820-runtime-policy-costs-summary.csv`
- `output/external-real-v1-candidate-ablation-r5-seed20260820-failure-cases-summary.csv`
- `output/auto-policy-v2/auto-policy-v2-generation-summary.json`
- `output/auto-policy-v2/auto-policy-v2-semantic-evaluation-v2-summary.json`
- `output/auto-policy-v2/auto-policy-v2-semantic-evaluation-v2-by-type.csv`
- `output/auto-policy-v2-candidate-repair-r5-seed20260820-summary.csv`
- `output/auto-policy-v2-candidate-repair-r5-seed20260820-by-type.csv`
- `output/auto-policy-v3/auto-policy-v3-generation-summary.json`
- `output/auto-policy-v3/auto-policy-v3-semantic-evaluation-summary.json`
- `output/auto-policy-v3/auto-policy-v3-semantic-evaluation-by-type.csv`
- `output/auto-policy-v3-candidate-repair-r5-seed20260820-summary.csv`
- `output/auto-policy-v3-candidate-repair-r5-seed20260820-by-type.csv`
- `output/auto-policy-v3-no-normalizer/auto-policy-v3-no-normalizer-generation-summary.json`
- `output/auto-policy-v3-no-normalizer/auto-policy-v3-no-normalizer-semantic-evaluation-summary.json`
- `output/auto-policy-v3-no-normalizer-candidate-repair-r5-seed20260820-summary.csv`
- `output/auto-policy-v3-metadata-evidence-only/auto-policy-v3-metadata-evidence-only-semantic-evaluation-summary.json`
- `output/auto-policy-v3-metadata-evidence-only-candidate-repair-r5-seed20260820-summary.csv`
- `output/auto-policy-v3-component-ablation-r5-seed20260820-summary.csv`
- `output/auto-policy-v3-robustness-r3-seed20260827-summary.csv`
- `output/auto-policy-v3-robustness-r3-seed20260827.json`
- `output/auto-policy-v3-robustness-order_shuffle_full_metadata-candidate-repair-r3-seed20260827-summary.csv`
- `output/auto-policy-v3-robustness-less_templated_metadata_light-candidate-repair-r3-seed20260827-summary.csv`
- `output/auto-policy-v3-robustness-distractor_metadata_light-candidate-repair-r3-seed20260827-summary.csv`
- `output/auto-policy-v3-negative-safety-r1-seed20260827-summary.csv`
- `output/auto-policy-v3-negative-safety-r1-seed20260827-by-variant.csv`
- `output/auto-policy-v3-negative-safety-r1-seed20260827-by-event.csv`
- `output/auto-policy-v3-negative-safety-r1-seed20260827.json`
- `output/auto-policy-v3-conflict-gate-r1-seed20260827-by-dataset.csv`
- `output/auto-policy-v3-conflict-gate-r1-seed20260827-by-variant.csv`
- `output/auto-policy-v3-conflict-gate-r1-seed20260827-details.csv`
- `output/auto-policy-v3-conflict-gate-r1-seed20260827.json`
- `output/auto-policy-v3-statistical-analysis-metrics.csv`
- `output/auto-policy-v3-statistical-analysis-paired-tests.csv`
- `output/auto-policy-v3-statistical-analysis.json`
- `output/paper-experiment-main-table.csv`
- `output/paper-experiment-main-table.md`
- `output/paper-experiment-main-table.json`
- `output/auto-policy-v3-failure-case-analysis.md`
- `output/auto-policy-v3-failure-case-analysis-distractor.csv`
- `output/auto-policy-v3-failure-case-analysis-negative-unsafe.csv`
- `output/auto-policy-v3-failure-case-analysis-gate-residual.csv`
- `output/auto-policy-v3-failure-case-analysis-by-source-type.csv`
- `output/auto-policy-v3-failure-case-analysis-by-negative-variant.csv`
- `output/auto-policy-v3-failure-case-analysis.json`
- `output/method-flow-and-input-isolation-diagrams.md`
- `output/method-flow-and-input-isolation-diagrams.json`
- `output/auto-policy-v3-method-flow.mmd`
- `output/auto-policy-v3-input-isolation.mmd`
- `output/auto-policy-v3-policy-boundary.mmd`
- `output/reproducibility-package.md`
- `output/reproducibility-package.json`
- `output/paper-experiment-section-draft.md`
- `output/paper-experiment-section-draft.json`

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

The policy construction score is a complexity proxy, not measured human annotation time. The completed annotation-time pilot adds measured single-annotator wall-clock time for 12 semantic-v2 rows, but it is still too small to calibrate a general linear cost model. The proxy may underestimate non-linear effects such as domain expertise, cross-sentence evidence search, conflict resolution, and semantic-type-specific difficulty. A stronger future study should collect multi-annotator wall-clock annotation time on a larger subset and report inter-annotator variation.

External validity:

The semantic-v2 30-test benchmark is a controlled diagnostic challenge set. It is useful for isolating semantic-drift failure modes, but it is not evidence by itself that the method generalizes to arbitrary real policies or unseen product revisions. The separate `external-real-v1` benchmark now adds 30 READY public-source validation events and reaches full symbolic closure, but it must be positioned as structured public-source validation, not as a natural-document-understanding benchmark. The added W3C/NIST rows are script-formalized from official public change summaries rather than independently collected by multiple annotators. The metadata/evidence-only normalizer reaching 100.00% confirms that the current evidence notes are too structured to isolate Qwen's document-understanding contribution. A stronger paper artifact should add more independently annotated external events from real policy documents, product clauses, or historical revisions, with less templated free text and adversarial near-miss examples not generated from the same templates.

Freeze validity:

The current benchmark now has a local freeze commit, `688e6cdd8a78327e89358846dbfbe61eac2271d3`. This improves reproducibility for future reruns, but it was created after the development experiments already reported here. Therefore it does not retroactively prove that earlier Qwen results were produced after a freeze. For final submission, rerun headline Qwen experiments from this commit and archive the benchmark with an external timestamp or DOI.

Automation validity:

`LLM_EXTRACTED_FACTS_TEMPLATE_POLICY` only replaces manual fact values with Qwen-extracted fact values. Fact names, fact types, and rule templates remain manually specified. The 86.00% result shows that free-form automatic fact normalization is not solved in the controlled semantic-v2 benchmark. This experiment must not be described as automatic policy extraction or automatic rule construction.

External Auto Policy V2 improves this boundary because it is candidate-blind and does not consume the manually written formal-policy files during generation. Its 82.67% repair-closure result exposed the specific weakness of free-form generated `semantic_result` strings. External Auto Policy V3 fixes this on the 30-event external-real-v1 set by using a predefined domain-level canonical vocabulary and deterministic normalization, reaching 100.00% semantic accuracy and repair closure without candidate or Oracle access. However, the method's stages are more general than its resources: the canonical vocabulary and normalizer are domain-specific and must be rebuilt, induced, or adapted for a new regulatory domain. The component ablation also shows that the current evidence notes are highly structured: a metadata/evidence-only normalizer reaches 100.00%, so V3 should not be presented as proving standalone Qwen document understanding. The robustness experiment further shows that V3 drops to 61.11% under metadata-light distractor evidence. The negative-safety experiment shows only 57.33% safe abstain overall before gating, with particularly weak fail-closed behavior under conflicting evidence and dominant distractors. The conflict/uncertainty gate improves negative safe abstain to 98.67% while preserving 100.00% normal-set selection accuracy. The remaining limitation is therefore sharper: the current safety improvement is a rule-based uncertainty gate over tested markers, not a general proof of robust natural-language evidence understanding.

Repair-closure validity:

The repair closure validates selected finite repair candidates as executable OWL artifacts using Reasoner and CQ regression. It does not prove fully automatic repair candidate generation from raw documents. Candidate operations and candidate OWLs are generated from the benchmark candidate set, and the method's main contribution remains evidence-constrained selection and validation among those candidates.

Relation to symbolic rule engines:

The hard gate is a lightweight symbolic rule executor, not a new general-purpose rule engine. SHACL, SWRL, or other rule engines could implement similar manually specified constraints; if they receive the same hand-built rules and facts, they would mainly test executor equivalence rather than document understanding. Decision-tree or rule-learning baselines would require a separate training set, which this 30-test benchmark does not provide. The current contribution is therefore best framed as evidence that structured formal policy and controlled canonicalization constrain LLM semantic selection; it should not be framed as a superiority claim over rule-engine infrastructure.

## 15. Remaining Gaps

The largest remaining gaps are now narrower:

1. Reposition `external-real-v1` correctly. It is structured public-source validation, not a natural-document-understanding benchmark. The metadata/evidence-only ablation reaching 100.00% proves that the current evidence notes are too templated to isolate Qwen's document-understanding contribution.
2. Robust automatic normalization of less-templated natural-language evidence into controlled symbolic vocabularies. Auto Policy V3 solves this for the current external-real-v1 canonical schemas, but the distractor robustness run drops to 61.11% under metadata-light near-miss evidence.
3. Canonical schema generalization. V3 uses a designed domain-level canonical vocabulary and deterministic normalizer; the pipeline is reusable, but the schema itself is not learned automatically and may not transfer unchanged to another domain.
4. Evidence conflict detection and uncertainty gating beyond synthetic markers. The new gate improves negative safe abstain to 98.67%, but it is still a lightweight rule-based detector and must be validated on naturally occurring conflict evidence.
5. Automatic or semi-automatic construction of template rules and canonical vocabularies from policy documents. Future work should study semi-automatic canonical vocabulary construction, rule-template induction, and cross-domain reuse of schema fragments.
6. Expand `external-real-v1` beyond the current 30 public-source smoke-validation events with independently annotated real revision events, less templated free text, contradictory change notes, overlapping effective dates, obsolete-but-authoritative clauses, and source-level disagreement.
7. A larger multi-annotator cost study, because the completed annotation-time pilot is still small, single-annotator, and single-benchmark.
8. A separate study comparing implementation carriers such as SHACL/SWRL, if the paper wants to make claims about symbolic-rule execution infrastructure.
9. Fully automatic repair-candidate generation from raw document changes, because the current closure validates executable candidates already generated from finite operations.

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
- The external-real-v1 public-source validation set now contains 30 READY events balanced across the three semantic types.
- On external-real-v1, the symbolic closure workflow selects 30/30 repairs correctly and validates 30/30 selected OWL repair artifacts with Reasoner and CQ checks.
- On external-real-v1, `OPTION_FORMAL_POLICY` reaches 100.00% under policy-available prompting, while `DIRECT_FREE` reaches 91.33%.
- Auto Policy V2 is candidate-blind and Oracle-blind, generates 150/150 policies, and reaches 82.67% semantic accuracy / repair closure when connected back to candidate selection.
- Auto Policy V3 is candidate-blind and Oracle-blind, generates 150/150 canonical policies, has 0 unresolved canonical results, and reaches 100.00% semantic accuracy plus 100.00% OWL repair closure on external-real-v1.
- Auto Policy V3's gain over V2 is attributable to controlled canonical semantic output and deterministic normalization, not to candidate or Oracle access.
- V3 component ablation shows deterministic canonical normalization is necessary: without it, repair closure falls to 36.67%.
- Metadata/evidence-only normalization also reaches 100.00% on external-real-v1, so the current external evidence notes are too templated to support a strong standalone LLM document-understanding claim.
- V3 is robust to shuffled evidence lines and less-templated paragraph evidence without distractors, reaching 100.00% closure in both settings.
- V3 is not robust to metadata-light near-miss distractors: repair closure drops to 61.11%, with failures concentrated in WCAG temporal and cross-sentence events.
- V3 fails closed on multi-survivor candidate ambiguity, reaching 100.00% safe abstain in `MULTIPLE_MATCHING_CANDIDATES`.
- Current V3 does not reliably fail closed under all negative conditions: overall safe abstain is 57.33%, with only 20.00% safe abstain under conflicting evidence and 3.33% under dominant distractors.
- Adding the V3 conflict/uncertainty gate preserves 100.00% normal-set selection accuracy and improves negative safe abstain to 98.67%.

Unsafe:

- Claiming hard gate is a fully automatic method.
- Claiming hard gate has zero total cost.
- Claiming the current benchmark proves generalization to unseen real-world policies.
- Claiming the freeze commit proves the benchmark was frozen before earlier development experiments; it only fixes the state for future reruns.
- Claiming template policy solves automatic rule construction; it only removes event-id branching from executor code.
- Claiming general automatic fact extraction is solved; the controlled semantic-v2 extraction experiment still shows normalization failures on E20, E29, E40, and E46.
- Claiming `policy_complexity_points` are measured annotation minutes or validated human labor estimates.
- Claiming the current closure proves fully automatic OWL repair from raw documents; it validates selected finite repair candidates, not automatic candidate generation.
- Claiming external-real-v1 is a large independently annotated real-world benchmark; the current 30-event set is a public-source smoke validation, and the W3C/NIST rows are script-formalized from official change summaries.
- Claiming Auto Policy V3 learns symbolic rules automatically; it uses a predefined canonical vocabulary and deterministic normalization over public evidence.
- Claiming Auto Policy V3 proves generalization to arbitrary policy domains; it is validated on the current external-real-v1 public-source schemas.
- Claiming the external-real-v1 V3 result proves Qwen is necessary; the metadata/evidence-only ablation reaches the same 100.00% on the current evidence notes.
- Claiming Auto Policy V3 is robust to misleading or contaminated evidence; the distractor robustness variant drops to 61.11%.
- Claiming Auto Policy V3 is safe under uncertainty; the negative-safety experiment has 42.67% unsafe selections across the tested negative variants.
- Claiming the conflict/uncertainty gate proves general safety; it is currently validated on synthetic uncertainty markers, not naturally occurring conflict corpora.

## 2026-08-27 Additional Naturalness And Transfer Checks

Detailed standalone note: `output/auto-policy-v3-natural-and-transfer-experiments.md`.

Natural-evidence robustness:

- Command: `G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe src\run_auto_policy_v3_natural_evidence_robustness.py --variants NATURAL_PARAGRAPH,RAW_SHORT_CONTEXT --runs 1 --seed 20260827 --prefix auto-policy-v3-natural-evidence-robustness-r1-seed20260827-paragraph-raw-short --skip-repair`
- `NATURAL_PARAGRAPH`: semantic accuracy 100.00%, strict events 30/30.
- `RAW_SHORT_CONTEXT`: semantic accuracy 50.00%, strict events 15/30.
- Repair closure after candidate selection, Reasoner, and CQ: `NATURAL_PARAGRAPH` 100.00%, `RAW_SHORT_CONTEXT` 50.00%.
- RAW short by type: `TEMPORAL_VERSION` 40.00%, `GENERAL_RULE_EXCEPTION` 90.00%, `CROSS_SENTENCE_SCOPE` 20.00%.
- `RAW_PROVENANCE_CONTEXT`: semantic accuracy 100.00%, repair closure 100.00%, strict events 30/30.
- Interpretation: removing note formatting alone is not enough to hurt V3, but naive source-near paragraph retrieval exposes a major natural-document robustness gap. Target-aware provenance retrieval closes that gap on the current benchmark by prioritizing current/new documents and target-code/source-heading windows.

Leave-one-domain-out schema transfer:

- Command: `G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe src\run_auto_policy_v3_schema_transfer.py --prefix auto-policy-v3-schema-transfer-r5-seed20260820-semantic-only --skip-repair`
- `FULL_SCHEMA`: 100.00%, strict events 30/30.
- `LEAVE_OUT_DIGITAL_IDENTITY`: 80.00%, strict events 24/30.
- `LEAVE_OUT_INSURANCE`: 90.00%, strict events 27/30.
- `LEAVE_OUT_WEB_ACCESSIBILITY`: 30.00%, strict events 9/30.
- Held-out domain canonical successes are 0/30 for digital identity, 0/15 for insurance, and 0/105 for web accessibility.
- Interpretation: the schema layer is domain-level rather than event-level, but Auto Policy V3 should be described as requiring a domain canonical schema adapter, not as domain-free rule learning.

Natural conflict safety:

- Command: `G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe src\run_auto_policy_v3_natural_conflict_safety.py --runs 1 --seed 20260827 --prefix auto-policy-v3-natural-conflict-safety-r1-seed20260827`
- Overall safe abstain: 33.33%; unsafe selection: 66.67%.
- `OBSOLETE_AND_CURRENT_MIXED`: safe abstain 26.67%, unsafe selection 73.33%.
- `SAME_DOMAIN_NEAR_MISS_MIXED`: safe abstain 40.00%, unsafe selection 60.00%.
- Interpretation: the current gate is not sufficient for natural old/current paragraph mixtures or same-domain near-miss retrieval noise. It should not be claimed as a general natural-conflict safety solution.

Enhanced natural-conflict gate:

- Command: `G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe src\run_auto_policy_v3_natural_conflict_gate.py --prefix auto-policy-v3-natural-conflict-gate-r1-seed20260827`
- Normal set: 150/150 selected, 100.00% Oracle accuracy, 0.00% false abstain.
- Natural conflict set: 60/60 safe abstain, 0.00% unsafe selection.
- `OBSOLETE_AND_CURRENT_MIXED`: safe abstain 100.00%, unsafe selection 0.00%.
- `SAME_DOMAIN_NEAR_MISS_MIXED`: safe abstain 100.00%, unsafe selection 0.00%.
- Interpretation: adding candidate-blind mixed-evidence checks closes the current natural-conflict benchmark without harming the normal structured-note set. This should be described as a provenance/evidence-block consistency gate, not as general open-domain contradiction detection.

Updated paper-safe position:

- Auto Policy V3 is strong on controlled candidate-blind structured notes and executable candidate repair closure.
- Its perfect external-real-v1 note-level result does not transfer to naive raw source-paragraph retrieval, but a provenance-aware target retrieval layer restores 30/30 closure on this benchmark.
- Its schema is domain-level and auditable, but domain-specific.
- The original safety gate works on controlled uncertainty markers, but natural-conflict safety requires the enhanced mixed-evidence gate and still needs validation on independently collected natural conflict corpora.

Schema adapter inventory:

- Command: `G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe src\build_schema_adapter_inventory.py`
- Output: `output/auto-policy-v3-schema-adapter-inventory.md`
- `digital_identity`: 6 events, `normalize_nist`, full-schema domain canonical rate 100.00%, held-out domain canonical rate 0.00%, held-out overall semantic accuracy 80.00%.
- `insurance`: 3 events, `normalize_insurance`, full-schema domain canonical rate 100.00%, held-out domain canonical rate 0.00%, held-out overall semantic accuracy 90.00%.
- `web_accessibility`: 21 events, `normalize_wcag`, full-schema domain canonical rate 100.00%, held-out domain canonical rate 0.00%, held-out overall semantic accuracy 30.00%.
- The adapters do not branch on `event_id`, so they are not one hand-written rule per event. But the held-out-domain canonical rate is 0.00% for every domain, so they must be described as domain canonical schema adapters rather than domain-free rule learning.

External-validity register:

- Command: `G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe src\build_external_validity_register.py`
- Output: `output/auto-policy-v3-external-validity-register.md`
- Quantified residual risks: small 30-event diagnostic benchmark, human annotation cost proxy, and limited freeze proof before exploratory experiments.
- Experimentally quantified risks: structured evidence notes overstate natural-document understanding; raw short context drops to 50.00%, while provenance-aware retrieval restores 100.00% on this benchmark.
- Partially mitigated risks: natural conflict safety; enhanced gate reaches 100.00% safe abstain on the constructed natural-conflict benchmark, but this is not yet an independent real conflict corpus.
- Scope-clarified risks: V3 validates finite candidate selection and OWL repair closure, not fully automatic raw-document candidate generation.
- Paper-safe contribution statement: candidate-blind policy generation plus auditable domain canonicalization, provenance-aware evidence retrieval, fail-closed conflict gating, and executable OWL repair closure over finite candidates.

Natural-conflict-real-v1:

- Command: `G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe src\run_natural_conflict_real_v1.py --runs 5 --seed 20260820 --prefix natural-conflict-real-v1-gate-r5-seed20260820`
- Output: `output/natural-conflict-real-v1-gate-r5-seed20260820.md`
- Evidence construction: mixed public-source excerpts from `benchmark/external-real-v1/documents/excerpts`; candidate values and private Oracle content removed; no synthetic conflict marker or artificial value rewrite.
- Overall result: 60 scenarios, 300 attempts, 100.00% safe abstain, 0.00% unsafe selection, 30/30 events covered.
- Scenario types: `REAL_SAME_DOMAIN_NEAR_MISS` 150/150 safe abstain; `REAL_VERSION_FAMILY_COLLISION` 150/150 safe abstain.
- Domain results: digital identity 60/60, insurance 30/30, web accessibility 210/210 safe abstain.
- Updated safety boundary: this substantially strengthens the gate claim beyond synthetic negative markers, but it is still a constructed real-public-excerpt mixture benchmark rather than an independently mined natural conflict incident corpus.

Schema adapter onboarding evidence:

- Command: `G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe src\build_schema_adapter_onboarding_evidence.py`
- Output: `output/auto-policy-v3-schema-adapter-onboarding-evidence.md`
- Web accessibility: 21 events, 3 canonical families, 7.00 events per family, strong reuse.
- Insurance: 3 events, 3 canonical families, 1.00 events per family, weak reuse / small sample.
- Digital identity: 6 events, 6 canonical change-key families, 1.00 events per family, weak reuse under the current NIST subset.
- Updated schema boundary: the adapter is not event-id branching, but the current evidence only shows strong family-level reuse for WCAG; new domains still require canonical vocabulary onboarding.

External-real-v2 expansion plan:

- Command: `G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe src\build_external_real_v2_expansion_plan.py`
- Output: `output/external-real-v2-expansion-plan.md`
- Purpose: address the residual 30-event sample-size limitation with a concrete source-intake and quota plan.
- Completion command: `G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe src\build_external_real_v2_complete.py`
- Validation command: `G:\LearnAI\ontology-evolution\.venv\Scripts\python.exe src\validate_external_real_v2.py --min-ready-events 60 --min-domains 5 --min-per-type 15 --prefix external-real-v2-validation-68`
- Current status: completed expanded diagnostic benchmark.
- Final scale: 68 READY events, 5 domains, 136 document rows, 204 candidates, 68 private Oracle rows.
- Semantic-type counts: `TEMPORAL_VERSION` 21, `GENERAL_RULE_EXCEPTION` 25, `CROSS_SENTENCE_SCOPE` 22.
- Validation result: errors 0, warnings 0.
- Candidate source families include W3C WCAG/WCAG2Mobile, NIST SP 800-63 Revision 3/4, EUR-Lex amendments, and eCFR/Federal Register current regulatory text.
- Paper-safe boundary: data scale is now completed, but the full model experiment table has not yet been rerun on external-real-v2; current model-performance claims should still be tied to the 30-event external-real-v1 unless a v2 rerun is performed.
