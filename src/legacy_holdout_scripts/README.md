# Legacy Hold-out Scripts

These scripts are archived to prevent accidental use in the post-M16 v5 blind
evaluation workflow.

They hard-code older benchmark/output defaults such as:

- `external-real-holdout-v1-expanded`
- `external-real-holdout-v3-large`
- `external-real-holdout-v4-blind`
- earlier "final blind" wording that is no longer methodologically safe after
  M14/M15/M16 development.

Use these current entrypoints instead:

- Method freeze: `src/freeze_m16_full_repair_method.py`
- New large benchmark build: `src/build_external_real_holdout_v5_blind_large.py`
- Oracle review sheet: `src/generate_holdout_oracle_review_sheet.py`
- Benchmark freeze: `src/freeze_external_real_holdout_v1_expanded.py` with explicit `--benchmark-dir`, `--output-dir`, and `--prefix`
- Main M13 generation: `src/run_m13_rule_refinement_pilot.py` with explicit v5 paths and `--manifest-mode all`
- M14 recovery: `src/run_m14_clause_level_gre_css_recovery.py` with explicit v5 paths
- M15 recovery: `src/run_m15_temporal_anchor_recovery.py` with explicit v5 paths
- M16 verifier: `src/run_m16_candidate_entailment_verifier.py` with explicit v5 paths
- Stage combine: `src/combine_holdout_pipeline_details.py` with explicit v5 paths

Do not move scripts back into `src/` unless the experiment lineage is updated
and the paper text clearly distinguishes diagnostic, validation, and final
blind datasets.
