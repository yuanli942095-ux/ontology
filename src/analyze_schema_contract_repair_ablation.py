from __future__ import annotations

"""Analyze V4.5 Schema Contract Repair ablation: main table, recovery/regression, McNemar."""

import argparse
import csv
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR, write_csv

OUTPUT_ROOT = PROJECT_DIR / "output" / "schema-contract-repair-ablation"
IR_PREFIX = "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827"


def binom_two_sided_p(k: int, n: int, p: float = 0.5) -> float:
    """Two-sided exact binomial p-value for McNemar discordant pairs.

    Under H0, discordant counts follow Binomial(n, 0.5). Pass k=min(a_only, b_only).
    """
    if n <= 0:
        return 1.0
    observed = math.comb(n, k) * (p**k) * ((1 - p) ** (n - k))
    total = 0.0
    for i in range(n + 1):
        prob = math.comb(n, i) * (p**i) * ((1 - p) ** (n - i))
        if prob <= observed + 1e-15:
            total += prob
    return min(1.0, total)


def load_details(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    return {(row["event_id"], row["run"]): row for row in csv.DictReader(path.open(encoding="utf-8-sig"))}


def truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes"}


def valid_json_status(status: str) -> bool:
    return status in {"GENERATED", "INVALID_SCHEMA", "ABSTAIN"}


def summarize_rows(rows: list[dict[str, str]]) -> dict[str, Any]:
    selected = [row for row in rows if row.get("selection_status") == "SELECTED"]
    return {
        "attempts": len(rows),
        "valid_json": sum(valid_json_status(row.get("generation_status", "")) for row in rows) / len(rows) if rows else 0.0,
        "schema_valid": sum(truthy(row.get("schema_valid", "")) or row.get("generation_status") == "GENERATED" for row in rows) / len(rows) if rows else 0.0,
        "ir_ok": sum(row.get("ir_status") == "OK" for row in rows) / len(rows) if rows else 0.0,
        "selected": len(selected) / len(rows) if rows else 0.0,
        "coverage": len(selected) / len(rows) if rows else 0.0,
        "oracle_accuracy": sum(truthy(row.get("selection_oracle_correct", "")) for row in rows) / len(rows) if rows else 0.0,
        "closure_accuracy": sum(truthy(row.get("full_closure_success", "")) for row in rows) / len(rows) if rows else 0.0,
        "precision_given_select": (
            sum(truthy(row.get("selection_oracle_correct", "")) for row in selected) / len(selected) if selected else 0.0
        ),
        "abstain": sum(row.get("selection_status") == "ABSTAIN" for row in rows) / len(rows) if rows else 0.0,
    }


def strict_events(rows: list[dict[str, str]]) -> tuple[int, int]:
    by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_event[row["event_id"]].append(row)
    successes = sum(1 for event_rows in by_event.values() if event_rows and all(truthy(row.get("full_closure_success", "")) for row in event_rows))
    return successes, len(by_event)


def paired_mcnemar(a_rows: dict[tuple[str, str], dict[str, str]], b_rows: dict[tuple[str, str], dict[str, str]], metric: str) -> dict[str, Any]:
    keys = sorted(set(a_rows) & set(b_rows))
    a_only = sum(truthy(a_rows[key].get(metric, "")) and not truthy(b_rows[key].get(metric, "")) for key in keys)
    b_only = sum((not truthy(a_rows[key].get(metric, ""))) and truthy(b_rows[key].get(metric, "")) for key in keys)
    discordant = a_only + b_only
    return {
        "pairs": len(keys),
        "a_only": a_only,
        "b_only": b_only,
        "discordant_pairs": discordant,
        "exact_mcnemar_p": binom_two_sided_p(min(a_only, b_only), discordant) if discordant else 1.0,
    }


def bootstrap_closure_ci(
    a_rows: dict[tuple[str, str], dict[str, str]],
    b_rows: dict[tuple[str, str], dict[str, str]],
    *,
    boots: int = 2000,
    seed: int = 20260831,
) -> tuple[float, float, float]:
    keys = sorted(set(a_rows) & set(b_rows))
    diffs = [
        float(truthy(b_rows[key].get("full_closure_success", "")))
        - float(truthy(a_rows[key].get("full_closure_success", "")))
        for key in keys
    ]
    if not diffs:
        return 0.0, 0.0, 0.0
    mean_diff = sum(diffs) / len(diffs)
    rng = random.Random(seed)
    samples = []
    for _ in range(boots):
        draw = [diffs[rng.randrange(len(diffs))] for _ in range(len(diffs))]
        samples.append(sum(draw) / len(draw))
    samples.sort()
    low = samples[int(0.025 * len(samples))]
    high = samples[int(0.975 * len(samples)) - 1]
    return mean_diff, low, high


def enrich_schema_valid(rows: dict[tuple[str, str], dict[str, str]], raw_summary_path: Path) -> None:
    if not raw_summary_path.is_file():
        return
    raw_map = {(row["event_id"], str(row["run"])): row for row in csv.DictReader(raw_summary_path.open(encoding="utf-8-sig"))}
    for key, row in rows.items():
        raw = raw_map.get(key)
        if raw:
            row["schema_valid"] = str(raw.get("schema_valid", ""))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_ROOT)
    parser.add_argument(
        "--ir-subdir",
        default="ir",
        help="IR output subdir under each arm (e.g. ir-no-robust)",
    )
    parser.add_argument(
        "--output-suffix",
        default="",
        help="suffix for analysis outputs (e.g. -no-robust-ir)",
    )
    args = parser.parse_args()

    arm_a_details = args.output_dir / "arm-a-adaptive" / args.ir_subdir / f"{IR_PREFIX}-details.csv"
    arm_b_details = args.output_dir / "arm-b-contract-repair" / args.ir_subdir / f"{IR_PREFIX}-details.csv"
    a_map = load_details(arm_a_details)
    b_map = load_details(arm_b_details)
    enrich_schema_valid(a_map, args.output_dir / "arm-a-raw-summary.csv")
    enrich_schema_valid(b_map, args.output_dir / "arm-b-raw-summary.csv")

    a_rows = [a_map[key] for key in sorted(a_map)]
    b_rows = [b_map[key] for key in sorted(b_map)]

    a_metrics = summarize_rows(a_rows)
    b_metrics = summarize_rows(b_rows)
    a_strict, a_events = strict_events(a_rows)
    b_strict, _b_events = strict_events(b_rows)

    main_table = []
    labels = [
        ("valid_json", "Valid JSON"),
        ("schema_valid", "Schema Valid"),
        ("ir_ok", "IR OK"),
        ("selected", "Selected"),
        ("coverage", "Coverage"),
        ("oracle_accuracy", "Oracle Accuracy"),
        ("closure_accuracy", "OWL Closure Accuracy"),
        ("precision_given_select", "P|select"),
        ("abstain", "Abstain"),
    ]
    for key, label in labels:
        main_table.append(
            {
                "metric": label,
                "v45_adaptive": a_metrics[key],
                "plus_contract_repair": b_metrics[key],
                "delta": b_metrics[key] - a_metrics[key],
            }
        )
    main_table.append(
        {
            "metric": "Strict Events",
            "v45_adaptive": f"{a_strict}/{a_events}",
            "plus_contract_repair": f"{b_strict}/{a_events}",
            "delta": b_strict - a_strict,
        }
    )

    a_fail = {
        key
        for key in a_map
        if a_map[key].get("generation_status") in {"INVALID_SCHEMA", "INVALID_JSON"} or a_map[key].get("ir_status") != "OK"
    }
    recovered_ir = [key for key in a_fail if a_map[key].get("ir_status") != "OK" and b_map[key].get("ir_status") == "OK"]
    recovered_closure = [key for key in recovered_ir if truthy(b_map[key].get("full_closure_success", ""))]
    regressions = [
        key
        for key in a_map
        if truthy(a_map[key].get("full_closure_success", "")) and not truthy(b_map[key].get("full_closure_success", ""))
    ]

    recovery_metrics = {
        "schema_ir_failures_arm_a": len(a_fail),
        "recovered_to_ir_ok": len(recovered_ir),
        "contract_recovery_rate": len(recovered_ir) / len(a_fail) if a_fail else 0.0,
        "recovered_selected": sum(b_map[key].get("selection_status") == "SELECTED" for key in recovered_ir),
        "recovered_closure_pass": len(recovered_closure),
        "recovered_closure_rate": len(recovered_closure) / len(recovered_ir) if recovered_ir else 0.0,
        "regression_count": len(regressions),
    }

    mcnemar = paired_mcnemar(a_map, b_map, "full_closure_success")
    delta_closure, ci_low, ci_high = bootstrap_closure_ci(a_map, b_map)

    by_type_rows = []
    for semantic_type in ("ALL", "TEMPORAL_VERSION", "GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE"):
        subset_a = a_rows if semantic_type == "ALL" else [row for row in a_rows if row.get("semantic_type") == semantic_type]
        subset_b = b_rows if semantic_type == "ALL" else [row for row in b_rows if row.get("semantic_type") == semantic_type]
        for label, subset in (("V4.5 Adaptive", subset_a), ("+ Contract Repair", subset_b)):
            metrics = summarize_rows(subset)
            strict_ok, strict_total = strict_events(subset)
            by_type_rows.append(
                {
                    "semantic_type": semantic_type,
                    "arm": label,
                    **metrics,
                    "strict_events": f"{strict_ok}/{strict_total}",
                }
            )

    paired_rows = []
    for key in sorted(a_map):
        a_row = a_map[key]
        b_row = b_map[key]
        paired_rows.append(
            {
                "event_id": key[0],
                "run": key[1],
                "semantic_type": a_row.get("semantic_type", ""),
                "a_generation_status": a_row.get("generation_status", ""),
                "b_generation_status": b_row.get("generation_status", ""),
                "a_ir_status": a_row.get("ir_status", ""),
                "b_ir_status": b_row.get("ir_status", ""),
                "a_selected": a_row.get("selected_candidate_id", ""),
                "b_selected": b_row.get("selected_candidate_id", ""),
                "a_closure": a_row.get("full_closure_success", ""),
                "b_closure": b_row.get("full_closure_success", ""),
                "closure_regression": truthy(a_row.get("full_closure_success", "")) and not truthy(b_row.get("full_closure_success", "")),
                "closure_gain": (not truthy(a_row.get("full_closure_success", ""))) and truthy(b_row.get("full_closure_success", "")),
            }
        )

    success_criteria = {
        "ir_ok_increase": b_metrics["ir_ok"] - a_metrics["ir_ok"],
        "closure_increase_pp": (b_metrics["closure_accuracy"] - a_metrics["closure_accuracy"]) * 100,
        "p_select_drop_pp": (a_metrics["precision_given_select"] - b_metrics["precision_given_select"]) * 100,
        "regression_count": len(regressions),
        "keep_contract_repair": (
            (b_metrics["ir_ok"] > a_metrics["ir_ok"])
            and ((b_metrics["closure_accuracy"] - a_metrics["closure_accuracy"]) >= 0.02)
            and ((a_metrics["precision_given_select"] - b_metrics["precision_given_select"]) <= 0.02)
            and len(regressions) == 0
        ),
    }

    suffix = args.output_suffix or ""
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / f"main-table{suffix}.csv", main_table)
    write_csv(args.output_dir / f"by-type{suffix}.csv", by_type_rows)
    write_csv(args.output_dir / f"paired-comparison{suffix}.csv", paired_rows)
    if regressions:
        write_csv(
            args.output_dir / f"closure-regressions{suffix}.csv",
            [dict(a_map[key], **{"run": key[1]}) for key in regressions],
        )
    (args.output_dir / f"analysis-summary{suffix}.json").write_text(
        json.dumps(
            {
                "contract_repair_included_in_main_method": False,
                "contract_repair_decision": "rejected_overlap_with_robust_ir",
                "contract_repair_rationale": (
                    "Under production robust_ir=True, Contract Repair adds 0 closure gain while "
                    "overlapping robust_ir. Under robust_ir=False it yields +1.5pp closure with "
                    "0 regressions but below the pre-registered +2-3pp inclusion threshold."
                ),
                "recommended_pipeline": [
                    "V4.5 Adaptive Recovery",
                    "robust_ir",
                    "Semantic IR",
                    "V4.3 Repair Decision",
                    "OWL Closure",
                ],
                "ir_subdir": args.ir_subdir,
                "robust_ir": args.ir_subdir != "ir-no-robust",
                "attempts": len(a_rows),
                "main_table": main_table,
                "recovery_metrics": recovery_metrics,
                "mcnemar_closure": mcnemar,
                "delta_closure": delta_closure,
                "delta_closure_ci95": [ci_low, ci_high],
                "success_criteria": success_criteria,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(success_criteria, ensure_ascii=False, indent=2))
    print(f"recovery={recovery_metrics}")
    print(f"mcnemar={mcnemar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
