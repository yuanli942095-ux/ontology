from __future__ import annotations

"""Offline Candidate-Identifier Permutation Test using frozen candidate-blind stages.

Reuses M13/M14/M15 candidate-blind artifacts from the original v5 DeepSeek eval,
re-runs V4 IR candidate ranking on the permute benchmark, then recombine through M16.
No LLM API calls required when M16 entailment verifier has no qualifying abstain rows.
"""

import argparse
import csv
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR

BENCHMARK = (
    PROJECT_DIR
    / "benchmark"
    / "external-real-holdout-v5-blind-large-robustness-variants"
    / "candidate-id-permute"
)
ORIG_BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v5-blind-large"
ORIG_EVAL = PROJECT_DIR / "output" / "external-real-holdout-v5-blind-large" / "final-blind-eval-r5-m16-deepseek"
OUT = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v5-blind-large"
    / "robustness-online"
    / "candidate-id-permute-full-r5-deepseek"
)
IR_PREFIX = "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827"
M14_IR_PREFIX = "m14-clause-level-gre-css-recovery-v4-ir"
M15_IR_PREFIX = "m15-temporal-anchor-recovery-v4-ir"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def load_display_maps() -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    def load(bench: Path) -> dict[str, dict[str, str]]:
        rows = read_csv(bench / "repair-stage" / "candidates" / "external-real-candidate-template.csv")
        out: dict[str, dict[str, str]] = {}
        for row in rows:
            out.setdefault(row["event_id"], {})[row["candidate_id"]] = row["display_value"]
        return out

    return load(ORIG_BENCHMARK), load(BENCHMARK)


def recompute_oracle_flags(
    rows: list[dict[str, str]],
    oracle_by_event: dict[str, str],
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in rows:
        new_row = dict(row)
        selected = str(new_row.get("selected_candidate_id", "")).strip()
        oracle_id = oracle_by_event.get(row["event_id"], "")
        ok = bool(selected) and selected == oracle_id
        new_row["selection_oracle_correct"] = str(ok)
        if "oracle_correct" in new_row:
            new_row["oracle_correct"] = str(ok)
        if "full_closure_success" in new_row:
            new_row["full_closure_success"] = str(
                ok and str(new_row.get("closure_success", new_row.get("full_closure_success", ""))).lower() == "true"
            )
        out.append(new_row)
    return out


def remap_selected_ids(
    rows: list[dict[str, str]],
    orig_by_event: dict[str, dict[str, str]],
    perm_by_event: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    perm_display_to_id: dict[str, dict[str, str]] = {}
    for event_id, id_to_disp in perm_by_event.items():
        perm_display_to_id[event_id] = {disp: cid for cid, disp in id_to_disp.items()}

    out: list[dict[str, str]] = []
    for row in rows:
        new_row = dict(row)
        event_id = row["event_id"]
        selected = str(row.get("selected_candidate_id", "")).strip()
        if selected and event_id in orig_by_event:
            display = orig_by_event[event_id].get(selected, "")
            if display:
                mapped = perm_display_to_id.get(event_id, {}).get(display, "")
                if mapped:
                    new_row["selected_candidate_id"] = mapped
        out.append(new_row)
    return out


def run(cmd: list[str], env: dict[str, str]) -> None:
    print(" ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=PROJECT_DIR, env=env, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-v4-rerun", action="store_true")
    args = parser.parse_args()

    py = str(PROJECT_DIR / ".venv" / "Scripts" / "python.exe")
    env = {
        **dict(__import__("os").environ),
        "PYTHONPATH": "src",
        "ONTOLOGY_EVOLUTION_OFFICIAL_RUN": "1",
        "ONTOLOGY_EVOLUTION_MODEL_ABLATION": "1",
    }

    m13_raw = OUT / "m13-pilot" / "arm-d-rule-refinement" / "raw_window_metadata_light" / "raw"
    m13_ir_dir = OUT / "m13-pilot" / "arm-d-rule-refinement" / "ir"
    m13_details = m13_ir_dir / f"{IR_PREFIX}-details.csv"

    if not args.skip_v4_rerun:
        run(
            [
                py,
                "src/run_auto_policy_v4_ir_candidate_repair.py",
                "--benchmark-dir",
                str(BENCHMARK),
                "--raw-dir",
                str(m13_raw),
                "--output-dir",
                str(m13_ir_dir),
                "--prefix",
                IR_PREFIX,
                "--method-name",
                "M13_Rule_Refinement_Permute",
                "--min-score",
                "0.30",
                "--gre-css-min-score",
                "0.24",
                "--min-margin",
                "0.00",
                "--reranker",
                "constraint",
                "--temporal-unique-top1",
                "--robust-ir",
                "--skip-missing-raw",
                "--discover-raw",
                "--allow-legacy-experiment",
            ],
            env,
        )

    oracle_rows = read_csv(BENCHMARK / "private" / "oracle" / "external-real-oracle-template.csv")
    oracle_by_event = {row["event_id"]: row["oracle_candidate_id"] for row in oracle_rows}

    orig_by_event, perm_by_event = load_display_maps()

    m14_recovery_details = (
        OUT / "m14-clause-level-gre-css-recovery" / "ir" / f"{M14_IR_PREFIX}-details.csv"
    )
    if m14_recovery_details.is_file():
        rows = read_csv(m14_recovery_details)
        remapped = remap_selected_ids(rows, orig_by_event, perm_by_event)
        remapped = recompute_oracle_flags(remapped, oracle_by_event)
        write_csv(m14_recovery_details, remapped, list(rows[0].keys()))

    m15_recovery_details = OUT / "m15-temporal-anchor-recovery" / "ir" / f"{M15_IR_PREFIX}-details.csv"
    if m15_recovery_details.is_file():
        rows = read_csv(m15_recovery_details)
        remapped = remap_selected_ids(rows, orig_by_event, perm_by_event)
        remapped = recompute_oracle_flags(remapped, oracle_by_event)
        write_csv(m15_recovery_details, remapped, list(rows[0].keys()))

    m14_combined = OUT / "m14-full-holdout-combined"
    m15_combined = OUT / "m15-full-holdout-combined"
    m16_combined = OUT / "m16-full-holdout-combined"
    m16_dir = OUT / "m16-candidate-entailment"

    run(
        [
            py,
            "src/combine_holdout_pipeline_details.py",
            "--stage",
            "m14",
            "--output-dir",
            str(m14_combined),
            "--base-details",
            str(m13_details),
            "--recovery-details",
            str(m14_recovery_details),
        ],
        env,
    )
    run(
        [
            py,
            "src/combine_holdout_pipeline_details.py",
            "--stage",
            "m15",
            "--output-dir",
            str(m15_combined),
            "--base-details",
            str(m13_details),
            "--prior-full-details",
            str(m14_combined / "m14-full-holdout-combined-full-details.csv"),
            "--prior-summary-details",
            str(m14_combined / "m14-full-holdout-combined-details.csv"),
            "--recovery-details",
            str(m15_recovery_details),
        ],
        env,
    )

    m16_details_path = m16_dir / "m16-candidate-entailment-details.csv"
    if (ORIG_EVAL / "m16-candidate-entailment" / "m16-candidate-entailment-details.csv").is_file():
        rows = read_csv(ORIG_EVAL / "m16-candidate-entailment" / "m16-candidate-entailment-details.csv")
        if rows:
            remapped = remap_selected_ids(rows, orig_by_event, perm_by_event)
            remapped = recompute_oracle_flags(remapped, oracle_by_event)
            write_csv(m16_details_path, remapped, list(rows[0].keys()))
        else:
            write_csv(m16_details_path, [], ["event_id", "run", "seed"])
    else:
        write_csv(m16_details_path, [], ["event_id", "run", "seed"])

    run(
        [
            py,
            "src/combine_holdout_pipeline_details.py",
            "--stage",
            "m16",
            "--output-dir",
            str(m16_combined),
            "--base-details",
            str(m13_details),
            "--prior-full-details",
            str(m15_combined / "m15-full-holdout-combined-full-details.csv"),
            "--prior-summary-details",
            str(m15_combined / "m15-full-holdout-combined-details.csv"),
            "--recovery-details",
            str(m16_details_path),
        ],
        env,
    )

    summary = read_csv(m16_combined / "m16-full-holdout-combined-summary.csv")
    binding = {
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "experiment": "candidate-id-permute-offline-replay",
        "benchmark": str(BENCHMARK),
        "output": str(OUT),
        "method": "frozen M16; candidate-blind M13-M15 reused; V4 IR re-ranked on permute benchmark",
        "summary": summary,
    }
    (OUT / "v5-online-robustness-pipeline-summary.json").write_text(
        json.dumps(binding, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(binding, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
