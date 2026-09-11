from __future__ import annotations

"""Analyze semantic completion pilot on 20 true semantic-missing slots."""

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

from analyze_schema_contract_repair_ablation import binom_two_sided_p
from semantic_v2_common import PROJECT_DIR, write_csv

OUTPUT_ROOT = PROJECT_DIR / "output" / "semantic-completion-pilot"
IR_PREFIX = "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827"


def load_details(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    return {(row["event_id"], row["run"]): row for row in csv.DictReader(path.open(encoding="utf-8-sig"))}


def truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes"}


def summarize(rows: list[dict[str, str]]) -> dict[str, Any]:
    selected = [row for row in rows if row.get("selection_status") == "SELECTED"]
    return {
        "attempts": len(rows),
        "ir_ok": sum(row.get("ir_status") == "OK" for row in rows),
        "selected": len(selected),
        "oracle_correct": sum(truthy(row.get("selection_oracle_correct", "")) for row in rows),
        "closure_pass": sum(truthy(row.get("full_closure_success", "")) for row in rows),
        "precision_given_select": (
            sum(truthy(row.get("selection_oracle_correct", "")) for row in selected) / len(selected) if selected else 0.0
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_ROOT)
    args = parser.parse_args()

    manifest = list(csv.DictReader((args.output_dir / "completion-summary.csv").open(encoding="utf-8-sig")))
    keys = [(row["event_id"], row["run"]) for row in manifest]

    a_map = load_details(args.output_dir / "arm-a-adaptive" / "ir" / f"{IR_PREFIX}-details.csv")
    b_map = load_details(args.output_dir / "arm-b-semantic-completion" / "ir" / f"{IR_PREFIX}-details.csv")
    a_rows = [a_map[key] for key in keys if key in a_map]
    b_rows = [b_map[key] for key in keys if key in b_map]

    a_metrics = summarize(a_rows)
    b_metrics = summarize(b_rows)
    a_only = sum(
        truthy(a_map[key].get("full_closure_success", "")) and not truthy(b_map[key].get("full_closure_success", ""))
        for key in keys
        if key in a_map and key in b_map
    )
    b_only = sum(
        (not truthy(a_map[key].get("full_closure_success", ""))) and truthy(b_map[key].get("full_closure_success", ""))
        for key in keys
        if key in a_map and key in b_map
    )
    discordant = a_only + b_only
    recovered_ir = [
        key
        for key in keys
        if key in a_map
        and key in b_map
        and a_map[key].get("ir_status") != "OK"
        and b_map[key].get("ir_status") == "OK"
    ]
    recovered_closure = [
        key
        for key in recovered_ir
        if truthy(b_map[key].get("full_closure_success", ""))
    ]

    paired_rows = []
    for key in keys:
        if key not in a_map or key not in b_map:
            continue
        paired_rows.append(
            {
                "event_id": key[0],
                "run": key[1],
                "semantic_type": a_map[key].get("semantic_type", ""),
                "a_ir_status": a_map[key].get("ir_status", ""),
                "b_ir_status": b_map[key].get("ir_status", ""),
                "a_selected": a_map[key].get("selection_status", ""),
                "b_selected": b_map[key].get("selection_status", ""),
                "a_closure": a_map[key].get("full_closure_success", ""),
                "b_closure": b_map[key].get("full_closure_success", ""),
                "closure_regression": truthy(a_map[key].get("full_closure_success", ""))
                and not truthy(b_map[key].get("full_closure_success", "")),
                "closure_gain": (not truthy(a_map[key].get("full_closure_success", "")))
                and truthy(b_map[key].get("full_closure_success", "")),
            }
        )

    summary = {
        "attempts": len(keys),
        "arm_a": a_metrics,
        "arm_b": b_metrics,
        "delta": {
            "ir_ok": b_metrics["ir_ok"] - a_metrics["ir_ok"],
            "selected": b_metrics["selected"] - a_metrics["selected"],
            "oracle_correct": b_metrics["oracle_correct"] - a_metrics["oracle_correct"],
            "closure_pass": b_metrics["closure_pass"] - a_metrics["closure_pass"],
            "precision_given_select": b_metrics["precision_given_select"] - a_metrics["precision_given_select"],
        },
        "recovery": {
            "ir_failures_arm_a": sum(a_map[key].get("ir_status") != "OK" for key in keys if key in a_map),
            "recovered_to_ir_ok": len(recovered_ir),
            "recovered_selected": sum(b_map[key].get("selection_status") == "SELECTED" for key in recovered_ir),
            "recovered_closure_pass": len(recovered_closure),
            "recovered_closure_rate": len(recovered_closure) / len(recovered_ir) if recovered_ir else 0.0,
            "regression_count": a_only,
        },
        "mcnemar_closure": {
            "a_only": a_only,
            "b_only": b_only,
            "discordant_pairs": discordant,
            "exact_mcnemar_p": binom_two_sided_p(min(a_only, b_only), discordant) if discordant else 1.0,
        },
        "keep_semantic_completion": (
            len(recovered_closure) >= 5
            and a_only == 0
            and (b_metrics["precision_given_select"] - a_metrics["precision_given_select"]) >= -0.02
        ),
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "paired-comparison.csv", paired_rows)
    (args.output_dir / "analysis-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
