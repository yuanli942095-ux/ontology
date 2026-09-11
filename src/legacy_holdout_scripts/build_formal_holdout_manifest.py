from __future__ import annotations

"""Build frozen 236×5 formal hold-out manifest and public input records.

Uses only public excerpts; no oracle/candidate leakage.
"""

import argparse
import csv
import json
from pathlib import Path

from formal_holdout_eval_guard import FORMAL_HOLDOUT_RUNS, FORMAL_HOLDOUT_SEEDS, validate_pre_run_gates
from run_auto_policy_v4_ir_candidate_repair import configure_paths, read_csv, ready
from semantic_v2_common import PROJECT_DIR, write_csv

DEFAULT_BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v1-expanded"
DEFAULT_OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v1-expanded" / "final-blind-eval-r5"


def excerpt_path(benchmark_dir: Path, event_id: str) -> Path:
    return benchmark_dir / "public" / "excerpts" / f"{event_id}-evidence.md"


def build_input_record(
    *,
    event_id: str,
    run: int,
    seed: int,
    evidence_rel: str,
    semantic_type: str,
) -> dict[str, object]:
    return {
        "event_id": event_id,
        "semantic_type": semantic_type,
        "run": run,
        "seed": seed,
        "variant": "HOLDOUT_PUBLIC_FROZEN_EVIDENCE",
        "source_type": "HOLDOUT_PUBLIC_FROZEN_EVIDENCE",
        "candidate_blind_file": evidence_rel.replace("\\", "/"),
        "oracle_used": False,
        "candidate_used": False,
        "note_used": False,
        "manual_policy_used": False,
        "manual_formal_policy_used": False,
        "retrieval_status": "RETRIEVAL_READY",
        "status": "RETRIEVAL_READY",
        "m13_input_stub": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--skip-guard", action="store_true")
    parser.add_argument("--expected-events", type=int, default=236)
    args = parser.parse_args()

    if not args.skip_guard:
        validate_pre_run_gates()

    args.benchmark_dir = args.benchmark_dir.resolve()
    args.output_dir = args.output_dir.resolve()
    evidence_dir = args.output_dir / "public-input" / "candidate-blind-evidence"
    raw_dir = args.output_dir / "public-input" / "raw"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    paths = configure_paths(args.benchmark_dir)
    events = [row for row in read_csv(paths["event_csv"]) if ready(row.get("status", ""))]
    if args.expected_events > 0 and len(events) != args.expected_events:
        raise SystemExit(f"expected {args.expected_events} READY hold-out events, found {len(events)}")

    manifest_rows: list[dict[str, str]] = []
    missing_excerpts: list[str] = []
    for event in sorted(events, key=lambda row: row["event_id"]):
        event_id = event["event_id"]
        src = excerpt_path(args.benchmark_dir, event_id)
        if not src.is_file():
            missing_excerpts.append(event_id)
            continue
        for run, seed in enumerate(FORMAL_HOLDOUT_SEEDS, start=1):
            evidence_name = f"{event_id}-run{run}-seed{seed}-candidate-blind.md"
            evidence_path = evidence_dir / evidence_name
            if not evidence_path.is_file():
                evidence_path.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            raw_name = f"{event_id}-run{run}-seed{seed}.json"
            raw_path = raw_dir / raw_name
            evidence_rel = str(evidence_path.relative_to(PROJECT_DIR))
            record = build_input_record(
                event_id=event_id,
                run=run,
                seed=seed,
                evidence_rel=evidence_rel,
                semantic_type=event.get("semantic_type", ""),
            )
            raw_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            manifest_rows.append(
                {
                    "event_id": event_id,
                    "run": str(run),
                    "seed": str(seed),
                    "semantic_type": event.get("semantic_type", ""),
                    "domain": event.get("domain", ""),
                    "missing_subtype": "FORMAL_HOLDOUT_EVAL",
                    "raw_output_file": str(raw_path.relative_to(PROJECT_DIR)).replace("\\", "/"),
                }
            )

    if missing_excerpts:
        raise SystemExit(f"missing public excerpts for {len(missing_excerpts)} events: {', '.join(missing_excerpts[:5])}")

    manifest_path = args.output_dir / "formal-eval-manifest-r5.csv"
    write_csv(manifest_path, manifest_rows)
    binding = {
        "benchmark": str(args.benchmark_dir.relative_to(PROJECT_DIR)),
        "events": len(events),
        "runs": FORMAL_HOLDOUT_RUNS,
        "seeds": list(FORMAL_HOLDOUT_SEEDS),
        "attempts": len(manifest_rows),
        "manifest": str(manifest_path.relative_to(PROJECT_DIR)),
        "public_input_raw_dir": str(raw_dir.relative_to(PROJECT_DIR)),
    }
    (args.output_dir / "run-binding.json").write_text(
        json.dumps(binding, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(binding, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
