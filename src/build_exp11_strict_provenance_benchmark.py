from __future__ import annotations

"""Build strict-provenance benchmark subset with per-event ID permutation (seed 20260906)."""

import argparse
import csv
import json
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from build_v5_candidate_robustness_variants import id_permute, copy_source, candidate_csv, oracle_csv
from paper_final_validation_common import read_csv
from semantic_v2_common import PROJECT_DIR


SOURCE = PROJECT_DIR / "benchmark" / "external-real-holdout-v5-blind-large"
DEFAULT_OUT = PROJECT_DIR / "benchmark" / "external-real-holdout-v5-blind-large-strict-provenance"
EXP11_ID_PERMUTE_SEED = 20260906


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def filter_csv(path: Path, event_ids: set[str], key: str = "event_id") -> int:
    rows = read_csv(path)
    if not rows:
        return 0
    kept = [row for row in rows if row.get(key, "") in event_ids]
    write_csv(path, kept, list(rows[0].keys()))
    return len(kept)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--frozen-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--seed", type=int, default=EXP11_ID_PERMUTE_SEED)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    subset_rows = read_csv(args.frozen_csv)
    event_ids = {row["event_id"] for row in subset_rows}
    if not event_ids:
        raise SystemExit("frozen subset is empty")

    target = args.output_dir.resolve()
    copy_source(args.source.resolve(), target, args.force)

    filter_csv(target / "public" / "events" / "external-real-event-template.csv", event_ids)
    filter_csv(target / "public" / "documents" / "external-real-document-template.csv", event_ids)
    filter_csv(candidate_csv(target), event_ids)
    filter_csv(oracle_csv(target), event_ids)

    retrieval = target / "public" / "retrieval" / "external-real-v8-event-retrieval.csv"
    if retrieval.is_file():
        filter_csv(retrieval, event_ids)

    excerpts = target / "public" / "excerpts"
    if excerpts.is_dir():
        for path in excerpts.glob("*-evidence.md"):
            event_id = path.name.replace("-evidence.md", "")
            if event_id not in event_ids:
                path.unlink()

    mutants = target / "repair-stage" / "mutants"
    if mutants.is_dir():
        for path in mutants.glob("*.owl"):
            if path.stem not in event_ids:
                path.unlink()

    permute_info = id_permute(target, args.seed)
    readme = target / "README.md"
    extra = (
        "\n\n## Exp11 Strict Provenance Subset\n\n"
        f"- Events: {len(event_ids)}\n"
        f"- ID permute seed: {args.seed}\n"
        f"- Frozen subset: `{args.frozen_csv}`\n"
        f"- Permute summary: `{json.dumps(permute_info, ensure_ascii=False)}`\n"
    )
    readme.write_text(readme.read_text(encoding="utf-8") + extra, encoding="utf-8")

    summary = {
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": str(args.source),
        "output": str(target),
        "events": len(event_ids),
        "id_permute_seed": args.seed,
        "permute_info": permute_info,
        "frozen_csv": str(args.frozen_csv),
    }
    (target / "exp11-benchmark-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
