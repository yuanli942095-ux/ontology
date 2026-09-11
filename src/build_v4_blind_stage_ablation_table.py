from __future__ import annotations

"""Build stage-wise ablation tables for the frozen v4-blind hold-out run."""

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR, write_csv


DEFAULT_RUN_DIR = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v4-blind"
    / "final-blind-eval-r5-m16-deepseek"
)
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "output" / "external-real-holdout-v4-blind" / "ablation"


STAGES = [
    {
        "variant": "M13_BASE_ONLY",
        "removed": "M14_GRE_CSS_RECOVERY;M15_TEMPORAL_RECOVERY;M16_ENTAILMENT_VERIFIER",
        "summary": (
            Path("main-method")
            / "m13-pilot"
            / "arm-d-rule-refinement"
            / "ir"
            / "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827-summary.csv"
        ),
        "details": (
            Path("main-method")
            / "m13-pilot"
            / "arm-d-rule-refinement"
            / "ir"
            / "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827-details.csv"
        ),
    },
    {
        "variant": "M13_PLUS_M14",
        "removed": "M15_TEMPORAL_RECOVERY;M16_ENTAILMENT_VERIFIER",
        "summary": Path("m14-full-holdout-combined") / "m14-full-holdout-combined-summary.csv",
    },
    {
        "variant": "M13_PLUS_M14_PLUS_M15",
        "removed": "M16_ENTAILMENT_VERIFIER",
        "summary": Path("m15-full-holdout-combined") / "m15-full-holdout-combined-summary.csv",
    },
    {
        "variant": "FULL_M16",
        "removed": "",
        "summary": Path("m16-full-holdout-combined") / "m16-full-holdout-combined-summary.csv",
    },
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def as_float(row: dict[str, str], key: str) -> float:
    return float(row.get(key, "0") or 0)


def as_int(row: dict[str, str], key: str) -> int:
    return int(float(row.get(key, "0") or 0))


def first_float(row: dict[str, str], keys: tuple[str, ...]) -> float:
    for key in keys:
        value = row.get(key, "")
        if str(value).strip() != "":
            return float(value)
    return 0.0


def first_int(row: dict[str, str], keys: tuple[str, ...]) -> int:
    for key in keys:
        value = row.get(key, "")
        if str(value).strip() != "":
            return int(float(value))
    return 0


def truth(value: Any) -> bool:
    return str(value or "").strip().lower() == "true"


def detail_closure_success(row: dict[str, str]) -> bool:
    return truth(row.get("closure_success")) or truth(row.get("full_closure_success"))


def detail_oracle_success(row: dict[str, str]) -> bool:
    return truth(row.get("oracle_correct")) or truth(row.get("selection_oracle_correct"))


def summarize_details(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    by_type: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        semantic_type = row.get("semantic_type", "UNKNOWN")
        by_type.setdefault("ALL", []).append(row)
        by_type.setdefault(semantic_type, []).append(row)

    summaries: list[dict[str, Any]] = []
    for semantic_type in ("ALL", "TEMPORAL_VERSION", "GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE"):
        subset = by_type.get(semantic_type, [])
        if not subset:
            continue
        attempts = len(subset)
        closure = sum(1 for row in subset if detail_closure_success(row))
        oracle = sum(1 for row in subset if detail_oracle_success(row))
        event_ids = sorted({row["event_id"] for row in subset})
        strict = sum(
            1
            for event_id in event_ids
            if all(detail_closure_success(row) for row in subset if row["event_id"] == event_id)
        )
        summaries.append(
            {
                "semantic_type": semantic_type,
                "attempts": attempts,
                "closure_success": closure,
                "closure_accuracy": round(closure / attempts, 4) if attempts else 0.0,
                "oracle_success": oracle,
                "oracle_accuracy": round(oracle / attempts, 4) if attempts else 0.0,
                "events": len(event_ids),
                "strict_event_successes": strict,
                "strict_event_accuracy": round(strict / len(event_ids), 4) if event_ids else 0.0,
            }
        )
    return summaries


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_DIR))
    except ValueError:
        return str(path.resolve())


def build_rows(run_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    all_baseline: dict[str, str] | None = None
    previous_all: dict[str, str] | None = None
    for stage in STAGES:
        summary_path = run_dir / stage["summary"]
        if not summary_path.is_file():
            raise FileNotFoundError(f"missing summary file: {summary_path}")
        source_rows = read_csv(summary_path)
        details_path = run_dir / stage["details"] if "details" in stage else None
        if details_path and details_path.is_file():
            source_rows = summarize_details(read_csv(details_path))
        for row in source_rows:
            semantic_type = row["semantic_type"]
            is_all = semantic_type == "ALL"
            if is_all and all_baseline is None:
                all_baseline = row
            closure_acc = first_float(row, ("closure_accuracy", "full_closure_accuracy"))
            strict_acc = first_float(row, ("strict_event_accuracy",))
            baseline_gain = ""
            previous_gain = ""
            if is_all and all_baseline is not None:
                baseline_gain = f"{(closure_acc - first_float(all_baseline, ('closure_accuracy', 'full_closure_accuracy'))) * 100:.2f}"
            if is_all and previous_all is not None:
                previous_gain = f"{(closure_acc - first_float(previous_all, ('closure_accuracy', 'full_closure_accuracy'))) * 100:.2f}"
            rows.append(
                {
                    "variant": stage["variant"],
                    "semantic_type": semantic_type,
                    "attempts": first_int(row, ("attempts",)),
                    "closure_success": first_int(row, ("closure_success",)),
                    "closure_accuracy": closure_acc,
                    "oracle_success": first_int(row, ("oracle_success",)),
                    "oracle_accuracy": first_float(row, ("oracle_accuracy",)),
                    "events": first_int(row, ("events",)),
                    "strict_event_successes": first_int(row, ("strict_event_successes",)),
                    "strict_event_accuracy": strict_acc,
                    "all_closure_gain_vs_m13_pp": baseline_gain,
                    "all_closure_gain_vs_previous_pp": previous_gain,
                    "removed_modules": stage["removed"],
                    "source_summary": relpath(summary_path),
                }
            )
        previous_all = next(item for item in source_rows if item["semantic_type"] == "ALL")
    return rows


def write_markdown(path: Path, rows: list[dict[str, Any]]) -> None:
    all_rows = [row for row in rows if row["semantic_type"] == "ALL"]
    by_type_rows = [row for row in rows if row["semantic_type"] != "ALL"]
    lines = [
        "# external-real-holdout-v4-blind stage-wise ablation",
        "",
        "Dataset: frozen `external-real-holdout-v4-blind`, 220 events, 5 runs, 1100 attempts.",
        "",
        "This table is recomputed from frozen blind-run artifacts. It does not call the LLM and does not tune thresholds.",
        "",
        "## Overall",
        "",
        "| Variant | Closure | Strict event | Gain vs M13 (pp) | Incremental gain (pp) | Removed modules |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in all_rows:
        lines.append(
            "| {variant} | {closure_success}/{attempts} ({closure}) | "
            "{strict_event_successes}/{events} ({strict}) | {gain_base} | {gain_prev} | {removed} |".format(
                variant=row["variant"],
                closure_success=row["closure_success"],
                attempts=row["attempts"],
                closure=pct(row["closure_accuracy"]),
                strict_event_successes=row["strict_event_successes"],
                events=row["events"],
                strict=pct(row["strict_event_accuracy"]),
                gain_base=row["all_closure_gain_vs_m13_pp"],
                gain_prev=row["all_closure_gain_vs_previous_pp"],
                removed=row["removed_modules"] or "none",
            )
        )
    lines.extend(
        [
            "",
            "## By Semantic Type",
            "",
            "| Variant | Semantic type | Closure | Strict event |",
            "|---|---|---:|---:|",
        ]
    )
    for row in by_type_rows:
        lines.append(
            "| {variant} | {semantic_type} | {closure_success}/{attempts} ({closure}) | "
            "{strict_event_successes}/{events} ({strict}) |".format(
                variant=row["variant"],
                semantic_type=row["semantic_type"],
                closure_success=row["closure_success"],
                attempts=row["attempts"],
                closure=pct(row["closure_accuracy"]),
                strict_event_successes=row["strict_event_successes"],
                events=row["events"],
                strict=pct(row["strict_event_accuracy"]),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    args.run_dir = args.run_dir.resolve()
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    rows = build_rows(args.run_dir)
    csv_path = args.output_dir / "v4-blind-stage-ablation.csv"
    md_path = args.output_dir / "v4-blind-stage-ablation.md"
    json_path = args.output_dir / "v4-blind-stage-ablation.json"
    write_csv(csv_path, rows)
    write_markdown(md_path, rows)
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "csv": relpath(csv_path),
                "markdown": relpath(md_path),
                "json": relpath(json_path),
                "rows": len(rows),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
