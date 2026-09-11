from __future__ import annotations

"""Analyze Qwen vs DeepSeek model capacity ablation on M1/M2 decomposed extraction."""

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from analyze_schema_contract_repair_ablation import binom_two_sided_p
from semantic_v2_common import PROJECT_DIR, write_csv

OUTPUT_ROOT = PROJECT_DIR / "output" / "m12-model-capacity-ablation"
IR_PREFIX = "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827"


def load_details(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    return {(row["event_id"], row["run"]): row for row in csv.DictReader(path.open(encoding="utf-8-sig"))}


def truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes"}


def summarize_ir(rows: list[dict[str, str]]) -> dict[str, Any]:
    selected = [row for row in rows if row.get("selection_status") == "SELECTED"]
    return {
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

    manifest = list(csv.DictReader((args.output_dir / "deepseek-decomposition-summary.csv").open(encoding="utf-8-sig")))
    keys = [(row["event_id"], row["run"]) for row in manifest]
    decomp = {(row["event_id"], row["run"]): row for row in manifest}

    qwen_map = load_details(args.output_dir / "arm-a-qwen" / "ir" / f"{IR_PREFIX}-details.csv")
    deep_map = load_details(args.output_dir / "arm-b-deepseek" / "ir" / f"{IR_PREFIX}-details.csv")

    qwen_rows = [qwen_map[k] for k in keys if k in qwen_map]
    deep_rows = [deep_map[k] for k in keys if k in deep_map]

    qwen_pilot = PROJECT_DIR / "output" / "m12-decomposed-extraction-pilot" / "decomposition-summary.csv"
    qwen_pilot_rows = list(csv.DictReader(qwen_pilot.open(encoding="utf-8-sig")))
    qwen_d1_count = sum(row.get("failure_layer") == "D1" for row in qwen_pilot_rows)
    deep_d1_count = sum(row.get("deepseek_failure_layer") == "D1" for row in manifest)
    deep_atomic_complete = sum(truthy(row.get("deepseek_atomic_complete", "")) for row in manifest)
    qwen_atomic_complete = sum(truthy(row.get("atomic_complete", "")) for row in qwen_pilot_rows)

    qwen_metrics = summarize_ir(qwen_rows)
    deep_metrics = summarize_ir(deep_rows)

    a_only = sum(
        truthy(qwen_map[k].get("full_closure_success", "")) and not truthy(deep_map[k].get("full_closure_success", ""))
        for k in keys
        if k in qwen_map and k in deep_map
    )
    b_only = sum(
        (not truthy(qwen_map[k].get("full_closure_success", ""))) and truthy(deep_map[k].get("full_closure_success", ""))
        for k in keys
        if k in qwen_map and k in deep_map
    )
    discordant = a_only + b_only

    by_type: dict[str, dict[str, int]] = defaultdict(lambda: {"qwen_closure": 0, "deep_closure": 0, "slots": 0})
    paired = []
    for key in keys:
        q = qwen_map.get(key, {})
        d = deep_map.get(key, {})
        st = q.get("semantic_type") or d.get("semantic_type") or ""
        by_type[st]["slots"] += 1
        by_type[st]["qwen_closure"] += int(truthy(q.get("full_closure_success", "")))
        by_type[st]["deep_closure"] += int(truthy(d.get("full_closure_success", "")))
        paired.append(
            {
                "event_id": key[0],
                "run": key[1],
                "semantic_type": st,
                "qwen_ir": q.get("ir_status", ""),
                "deep_ir": d.get("ir_status", ""),
                "qwen_closure": q.get("full_closure_success", ""),
                "deep_closure": d.get("full_closure_success", ""),
                "deep_failure_layer": decomp.get(key, {}).get("deepseek_failure_layer", ""),
                "deep_runtime_ms": decomp.get(key, {}).get("deepseek_runtime_ms", ""),
                "deep_total_tokens": decomp.get(key, {}).get("deepseek_total_tokens", ""),
            }
        )

    total_latency = sum(int(row.get("deepseek_runtime_ms") or 0) for row in manifest)
    total_tokens = sum(int(row.get("deepseek_total_tokens") or 0) for row in manifest)

    summary = {
        "attempts": len(keys),
        "qwen": {
            **qwen_metrics,
            "atomic_fact_complete": qwen_atomic_complete,
            "d1_count": qwen_d1_count,
        },
        "deepseek": {
            **deep_metrics,
            "atomic_fact_complete": deep_atomic_complete,
            "d1_count": deep_d1_count,
            "avg_latency_ms": total_latency / max(1, len(manifest)),
            "total_tokens": total_tokens,
        },
        "delta": {
            "ir_ok": deep_metrics["ir_ok"] - qwen_metrics["ir_ok"],
            "closure_pass": deep_metrics["closure_pass"] - qwen_metrics["closure_pass"],
            "d1_count": deep_d1_count - qwen_d1_count,
            "precision_given_select": deep_metrics["precision_given_select"] - qwen_metrics["precision_given_select"],
        },
        "mcnemar_closure": {
            "a_only": a_only,
            "b_only": b_only,
            "discordant_pairs": discordant,
            "exact_mcnemar_p": binom_two_sided_p(min(a_only, b_only), discordant) if discordant else 1.0,
        },
        "by_semantic_type": dict(by_type),
        "interpretation": "",
    }
    if deep_metrics["closure_pass"] - qwen_metrics["closure_pass"] >= 4 and deep_d1_count <= qwen_d1_count - 3:
        summary["interpretation"] = "strong_model_capacity_effect"
    elif deep_metrics["closure_pass"] - qwen_metrics["closure_pass"] <= 1:
        summary["interpretation"] = "not_primarily_model_capacity"
    else:
        summary["interpretation"] = "mixed_model_capacity_signal"

    main_table = [
        {"metric": "Atomic Fact Complete", "qwen": qwen_atomic_complete, "deepseek": deep_atomic_complete},
        {"metric": "D1 Count", "qwen": qwen_d1_count, "deepseek": deep_d1_count},
        {"metric": "IR OK", "qwen": qwen_metrics["ir_ok"], "deepseek": deep_metrics["ir_ok"]},
        {"metric": "SELECT", "qwen": qwen_metrics["selected"], "deepseek": deep_metrics["selected"]},
        {"metric": "Oracle Correct", "qwen": qwen_metrics["oracle_correct"], "deepseek": deep_metrics["oracle_correct"]},
        {"metric": "OWL Closure", "qwen": qwen_metrics["closure_pass"], "deepseek": deep_metrics["closure_pass"]},
        {"metric": "P|select", "qwen": qwen_metrics["precision_given_select"], "deepseek": deep_metrics["precision_given_select"]},
        {"metric": "Avg Latency ms (DeepSeek)", "qwen": "", "deepseek": summary["deepseek"]["avg_latency_ms"]},
        {"metric": "Total Tokens (DeepSeek)", "qwen": "", "deepseek": total_tokens},
    ]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "main-table.csv", main_table)
    write_csv(args.output_dir / "paired-comparison.csv", paired)
    (args.output_dir / "analysis-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
