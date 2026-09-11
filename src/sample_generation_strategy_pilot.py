from __future__ import annotations

"""Freeze the stratified 60-event pilot list for generation strategy ablation."""

import argparse
import json
from pathlib import Path

import run_auto_formal_policy_batch_v3 as v3
from generation_strategy_ablation import (
    PILOT_EVENTS_FILE,
    PILOT_OUTPUT_DIR,
    PILOT_SAMPLE_SEED,
    build_pilot_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", default="external-real-v8-grounded")
    parser.add_argument("--seed", type=int, default=PILOT_SAMPLE_SEED)
    parser.add_argument("--output", type=Path, default=PILOT_EVENTS_FILE)
    args = parser.parse_args()

    v3.configure_benchmark(args.benchmark)
    events = list(v3.load_events().values())
    manifest = build_pilot_manifest(events, seed=args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("sample_seed", "attempt_count", "semantic_types")}, indent=2))
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
