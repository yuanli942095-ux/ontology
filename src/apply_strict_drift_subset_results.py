from __future__ import annotations

"""Apply frozen strict-drift subset to completed DeepSeek v5 results (post-review only)."""

import argparse
from collections import defaultdict
from pathlib import Path

from paper_final_validation_common import (
    DEFAULT_BENCHMARK_FREEZE_SUMMARY,
    DEFAULT_DEEPSEEK_PRED_DIR,
    DEFAULT_METHOD_FREEZE_DIR,
    PAPER_VALIDATION_ROOT,
    load_freeze_sha256,
    load_method_freeze_sha256,
    read_csv,
    verify_deepseek_prediction_dir,
    write_binding,
    write_manifest_csv,
    write_summary_json,
    utc_now_iso,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict-manifest",
        type=Path,
        default=PAPER_VALIDATION_ROOT / "04-strict-drift-subset" / "strict-subset-frozen.csv",
    )
    parser.add_argument("--prediction-dir", type=Path, default=DEFAULT_DEEPSEEK_PRED_DIR)
    parser.add_argument("--output-dir", type=Path, default=PAPER_VALIDATION_ROOT / "04-strict-drift-subset")
    parser.add_argument("--benchmark-freeze-summary", type=Path, default=DEFAULT_BENCHMARK_FREEZE_SUMMARY)
    parser.add_argument("--method-freeze-dir", type=Path, default=DEFAULT_METHOD_FREEZE_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.strict_manifest.is_file():
        write_summary_json(
            args.output_dir / "summary.json",
            {
                "generated_at_utc": utc_now_iso(),
                "status": "AWAITING_FROZEN_SUBSET",
                "message": "Human review required before creating strict-subset-frozen.csv",
            },
        )
        print("[strict-drift-results] frozen subset missing; skipped")
        return 0

    benchmark_sha256, benchmark_manifest = load_freeze_sha256(args.benchmark_freeze_summary)
    method_freeze_sha256, method_freeze_manifest = load_method_freeze_sha256(args.method_freeze_dir)
    verify_deepseek_prediction_dir(
        args.prediction_dir,
        benchmark_sha256=benchmark_sha256,
        method_freeze_sha256=method_freeze_sha256,
    )
    subset_rows = [
        row
        for row in read_csv(args.strict_manifest)
        if row.get("review_status", "").upper() in {"PASS", "FROZEN", "ACCEPTED"}
    ]
    subset_ids = {row["event_id"] for row in subset_rows}
    details_path = args.prediction_dir / "m16-full-holdout-combined" / "m16-full-holdout-combined-details.csv"
    details = [row for row in read_csv(details_path) if row["event_id"] in subset_ids] if subset_ids else []

    if not subset_ids:
        write_summary_json(
            args.output_dir / "summary.json",
            {
                "generated_at_utc": utc_now_iso(),
                "status": "COMPLETE",
                "subset_events": 0,
                "attempts": 0,
                "message": "Human review found no event with t0_support_old=PASS, t1_support_old=PASS, t1_support_new=PASS, and explicit lineage.",
            },
        )
        write_binding(
            args.output_dir,
            experiment_role="04-strict-drift-subset-results",
            script_path=Path(__file__),
            benchmark_manifest=benchmark_manifest,
            benchmark_sha256=benchmark_sha256,
            method_freeze_manifest=method_freeze_manifest,
            method_freeze_sha256=method_freeze_sha256,
            extra={
                "input_prediction_dir": str(args.prediction_dir),
                "llm_backend": "deepseek-chat",
                "model": "deepseek-chat",
            },
        )
        print("[strict-drift-results] subset_events=0 attempts=0")
        return 0

    by_type: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in details:
        by_type[row["semantic_type"]].append(row)

    def rate(field: str, rows: list[dict[str, str]]) -> float:
        if not rows:
            return 0.0
        return sum(str(item.get(field, "")).lower() == "true" for item in rows) / len(rows)

    summary_rows = []
    for semantic_type, rows in [("ALL", details), *sorted(by_type.items())]:
        summary_rows.append(
            {
                "semantic_type": semantic_type,
                "events": len({row["event_id"] for row in rows}),
                "attempts": len(rows),
                "oracle_accuracy": rate("oracle_correct", rows),
                "closure_accuracy": rate("closure_success", rows),
                "coverage": len(rows) / len(rows) if rows else 0.0,
                "p_oracle_given_select": rate("oracle_correct", [row for row in rows if row.get("selected_candidate_id")]),
                "strict_event_success_rate": rate("strict_event_success", rows),
            }
        )

    write_manifest_csv(args.output_dir / "results.csv", details)
    write_manifest_csv(args.output_dir / "results-summary.csv", summary_rows)
    write_summary_json(
        args.output_dir / "summary.json",
        {
            "generated_at_utc": utc_now_iso(),
            "status": "COMPLETE",
            "subset_events": len(subset_ids),
            "attempts": len(details),
        },
    )
    write_binding(
        args.output_dir,
        experiment_role="04-strict-drift-subset-results",
        script_path=Path(__file__),
        benchmark_manifest=benchmark_manifest,
        benchmark_sha256=benchmark_sha256,
        method_freeze_manifest=method_freeze_manifest,
        method_freeze_sha256=method_freeze_sha256,
        extra={
            "input_prediction_dir": str(args.prediction_dir),
            "llm_backend": "deepseek-chat",
            "model": "deepseek-chat",
        },
    )
    print(f"[strict-drift-results] subset_events={len(subset_ids)} attempts={len(details)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
