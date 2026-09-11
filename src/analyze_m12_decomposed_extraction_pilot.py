from __future__ import annotations

"""Analyze M1/M2 decomposed semantic extraction pilot."""

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from analyze_schema_contract_repair_ablation import binom_two_sided_p
from semantic_v2_common import PROJECT_DIR, write_csv

OUTPUT_ROOT = PROJECT_DIR / "output" / "m12-decomposed-extraction-pilot"
IR_PREFIX = "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827"


def load_details(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    return {(row["event_id"], row["run"]): row for row in csv.DictReader(path.open(encoding="utf-8-sig"))}


def truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes"}


def failure_layer(row: dict[str, str], decomposition: dict[str, str]) -> str:
    if decomposition.get("failure_layer"):
        return decomposition["failure_layer"]
    if row.get("ir_status") != "OK":
        return "D4"
    if row.get("selection_status") == "ABSTAIN":
        return "D5"
    if row.get("selection_status") == "SELECTED" and not truthy(row.get("selection_oracle_correct", "")):
        return "D6"
    return "PASS"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_ROOT)
    args = parser.parse_args()

    manifest = list(csv.DictReader((args.output_dir / "decomposition-summary.csv").open(encoding="utf-8-sig")))
    keys = [(row["event_id"], row["run"]) for row in manifest]
    decomp_map = {(row["event_id"], row["run"]): row for row in manifest}

    a_map = load_details(args.output_dir / "arm-a-adaptive" / "ir" / f"{IR_PREFIX}-details.csv")
    b_map = load_details(args.output_dir / "arm-b-decomposed" / "ir" / f"{IR_PREFIX}-details.csv")

    paired = []
    for key in keys:
        a_row = a_map.get(key, {})
        b_row = b_map.get(key, {})
        paired.append(
            {
                "event_id": key[0],
                "run": key[1],
                "missing_subtype": decomp_map.get(key, {}).get("missing_subtype", ""),
                "atomic_complete": decomp_map.get(key, {}).get("atomic_complete", ""),
                "derivation_status": decomp_map.get(key, {}).get("derivation_status", ""),
                "a_ir_status": a_row.get("ir_status", ""),
                "b_ir_status": b_row.get("ir_status", ""),
                "a_selected": a_row.get("selection_status", ""),
                "b_selected": b_row.get("selection_status", ""),
                "a_oracle": a_row.get("selection_oracle_correct", ""),
                "b_oracle": b_row.get("selection_oracle_correct", ""),
                "a_closure": a_row.get("full_closure_success", ""),
                "b_closure": b_row.get("full_closure_success", ""),
                "failure_layer": failure_layer(b_row, decomp_map.get(key, {})),
                "closure_gain": (not truthy(a_row.get("full_closure_success", "")))
                and truthy(b_row.get("full_closure_success", "")),
                "closure_regression": truthy(a_row.get("full_closure_success", ""))
                and not truthy(b_row.get("full_closure_success", "")),
            }
        )

    a_rows = [a_map[k] for k in keys if k in a_map]
    b_rows = [b_map[k] for k in keys if k in b_map]
    a_sel = [r for r in a_rows if r.get("selection_status") == "SELECTED"]
    b_sel = [r for r in b_rows if r.get("selection_status") == "SELECTED"]

    a_only = sum(truthy(a_map[k].get("full_closure_success", "")) and not truthy(b_map[k].get("full_closure_success", "")) for k in keys if k in a_map and k in b_map)
    b_only = sum((not truthy(a_map[k].get("full_closure_success", ""))) and truthy(b_map[k].get("full_closure_success", "")) for k in keys if k in a_map and k in b_map)
    discordant = a_only + b_only
    wrong_select_b = sum(
        1 for k in keys if k in b_map and b_map[k].get("selection_status") == "SELECTED" and not truthy(b_map[k].get("selection_oracle_correct", ""))
    )

    recovered_ir = [k for k in keys if k in a_map and k in b_map and a_map[k].get("ir_status") != "OK" and b_map[k].get("ir_status") == "OK"]
    recovered_closure = [k for k in recovered_ir if truthy(b_map[k].get("full_closure_success", ""))]

    summary = {
        "attempts": len(keys),
        "atomic_fact_complete": sum(truthy(row.get("atomic_complete", "")) for row in manifest),
        "symbolic_derivation_success": sum(row.get("derivation_status") == "DERIVED" for row in manifest),
        "arm_a": {
            "ir_ok": sum(r.get("ir_status") == "OK" for r in a_rows),
            "selected": len(a_sel),
            "oracle_correct": sum(truthy(r.get("selection_oracle_correct", "")) for r in a_rows),
            "closure_pass": sum(truthy(r.get("full_closure_success", "")) for r in a_rows),
            "precision_given_select": (
                sum(truthy(r.get("selection_oracle_correct", "")) for r in a_sel) / len(a_sel) if a_sel else 0.0
            ),
        },
        "arm_b": {
            "ir_ok": sum(r.get("ir_status") == "OK" for r in b_rows),
            "selected": len(b_sel),
            "oracle_correct": sum(truthy(r.get("selection_oracle_correct", "")) for r in b_rows),
            "closure_pass": sum(truthy(r.get("full_closure_success", "")) for r in b_rows),
            "precision_given_select": (
                sum(truthy(r.get("selection_oracle_correct", "")) for r in b_sel) / len(b_sel) if b_sel else 0.0
            ),
        },
        "recovery": {
            "recovered_ir_ok": len(recovered_ir),
            "recovered_closure_pass": len(recovered_closure),
            "recovered_closure_rate": len(recovered_closure) / len(recovered_ir) if recovered_ir else 0.0,
            "regression_count": a_only,
            "wrong_select_b": wrong_select_b,
        },
        "mcnemar_closure": {
            "a_only": a_only,
            "b_only": b_only,
            "discordant_pairs": discordant,
            "exact_mcnemar_p": binom_two_sided_p(min(a_only, b_only), discordant) if discordant else 1.0,
        },
        "failure_layer_counts": {},
        "decision": "",
    }
    summary["failure_layer_counts"] = {
        layer: sum(1 for row in paired if row["failure_layer"] == layer) for layer in ["D1", "D2", "D3", "D4", "D5", "D6", "PASS"]
    }

    new_closure = summary["arm_b"]["closure_pass"] - summary["arm_a"]["closure_pass"]
    p_select_b = summary["arm_b"]["precision_given_select"]
    if new_closure >= 6 and wrong_select_b == 0 and p_select_b >= 0.95:
        summary["decision"] = "strong_support_adopt"
    elif 4 <= new_closure <= 5 and wrong_select_b <= 1:
        summary["decision"] = "promising_needs_more_data"
    else:
        summary["decision"] = "not_worth_complexity"

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "paired-comparison.csv", paired)
    (args.output_dir / "analysis-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
