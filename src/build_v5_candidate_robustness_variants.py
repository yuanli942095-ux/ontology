from __future__ import annotations

"""Create derived v5 benchmark variants for online candidate robustness tests.

The original frozen benchmark is never modified. Variants are derived copies:

- candidate-order-shuffle: same candidate IDs and Oracle, shuffled candidate CSV rows.
- candidate-id-rename: candidate IDs are renamed consistently in candidate CSV
  and private Oracle, while display values and operations are unchanged.
- candidate-id-permute: per event, shuffle which candidate_id label attaches to
  each fixed candidate content; oracle id updated accordingly.
"""

import argparse
import csv
import json
import random
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR


SOURCE = PROJECT_DIR / "benchmark" / "external-real-holdout-v5-blind-large"
OUTPUT_ROOT = PROJECT_DIR / "benchmark" / "external-real-holdout-v5-blind-large-robustness-variants"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def copy_source(source: Path, target: Path, force: bool) -> None:
    if target.exists():
        if not force:
            raise SystemExit(f"target exists; pass --force to overwrite: {target}")
        shutil.rmtree(target)
    shutil.copytree(source, target)


def candidate_csv(root: Path) -> Path:
    return root / "repair-stage" / "candidates" / "external-real-candidate-template.csv"


def oracle_csv(root: Path) -> Path:
    return root / "private" / "oracle" / "external-real-oracle-template.csv"


def order_shuffle(target: Path, seed: int) -> dict[str, Any]:
    path = candidate_csv(target)
    rows = read_csv(path)
    fieldnames = list(rows[0].keys())
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["event_id"]].append(row)
    out: list[dict[str, str]] = []
    changed = 0
    for event_id in sorted(grouped):
        items = grouped[event_id]
        original = [row["candidate_id"] for row in items]
        rng = random.Random(f"{seed}|{event_id}|order-shuffle")
        rng.shuffle(items)
        if [row["candidate_id"] for row in items] != original:
            changed += 1
        out.extend(items)
    write_csv(path, out, fieldnames)
    return {"events_with_order_changed": changed, "candidate_rows": len(out)}


def id_permute(target: Path, seed: int) -> dict[str, Any]:
    path = candidate_csv(target)
    rows = read_csv(path)
    fieldnames = list(rows[0].keys())
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["event_id"]].append(row)

    oracle_rows = read_csv(oracle_csv(target))
    oracle_fields = list(oracle_rows[0].keys())
    oracle_by_event = {row["event_id"]: row for row in oracle_rows}

    out: list[dict[str, str]] = []
    oracle_after: Counter[str] = Counter()
    events_changed = 0

    for event_id in sorted(grouped):
        event_rows = sorted(grouped[event_id], key=lambda item: item["candidate_id"])
        labels = [row["candidate_id"] for row in event_rows]
        rng = random.Random(f"{seed}|{event_id}|id-permute")
        new_labels = list(labels)
        rng.shuffle(new_labels)
        if new_labels != labels:
            events_changed += 1
        old_to_new = dict(zip(labels, new_labels))
        for row in event_rows:
            new_row = dict(row)
            new_row["candidate_id"] = old_to_new[row["candidate_id"]]
            out.append(new_row)

        oracle_row = oracle_by_event[event_id]
        oracle_old = oracle_row["oracle_candidate_id"]
        oracle_new = old_to_new[oracle_old]
        oracle_row["oracle_candidate_id"] = oracle_new
        oracle_after[oracle_new] += 1

    write_csv(path, out, fieldnames)
    write_csv(oracle_csv(target), oracle_rows, oracle_fields)
    return {
        "candidate_rows": len(out),
        "events_with_id_assignment_changed": events_changed,
        "oracle_candidate_id_distribution": dict(oracle_after),
        "seed": seed,
    }


def id_rename(target: Path) -> dict[str, Any]:
    path = candidate_csv(target)
    rows = read_csv(path)
    fieldnames = list(rows[0].keys())
    mapping: dict[tuple[str, str], str] = {}
    labels = ["ALT_A", "ALT_B", "ALT_C", "ALT_D", "ALT_E"]
    for event_id in sorted({row["event_id"] for row in rows}):
        event_rows = [row for row in rows if row["event_id"] == event_id]
        for idx, row in enumerate(sorted(event_rows, key=lambda item: item["candidate_id"])):
            mapping[(event_id, row["candidate_id"])] = labels[idx]
    for row in rows:
        row["candidate_id"] = mapping[(row["event_id"], row["candidate_id"])]
    write_csv(path, rows, fieldnames)

    opath = oracle_csv(target)
    oracle_rows = read_csv(opath)
    oracle_fields = list(oracle_rows[0].keys())
    for row in oracle_rows:
        row["oracle_candidate_id"] = mapping[(row["event_id"], row["oracle_candidate_id"])]
    write_csv(opath, oracle_rows, oracle_fields)
    return {"renamed_candidate_rows": len(rows), "renamed_oracle_rows": len(oracle_rows)}


def update_readme(target: Path, variant: str, info: dict[str, Any]) -> None:
    readme = target / "README.md"
    existing = readme.read_text(encoding="utf-8", errors="replace") if readme.is_file() else ""
    addition = (
        "\n\n## Derived Robustness Variant\n\n"
        f"- Variant: `{variant}`\n"
        f"- Created at UTC: `{datetime.now(timezone.utc).isoformat()}`\n"
        "- Purpose: online candidate perturbation robustness only.\n"
        "- Parent benchmark: `external-real-holdout-v5-blind-large`.\n"
        "- This derived copy must not be used for method tuning.\n"
        f"- Transform summary: `{json.dumps(info, ensure_ascii=False)}`\n"
    )
    readme.write_text(existing.rstrip() + addition + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--seed", type=int, default=20260831)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    source = args.source.resolve()
    root = args.output_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, Any]] = []

    variants = {
        "candidate-order-shuffle": order_shuffle,
        "candidate-id-rename": id_rename,
        "candidate-id-permute": id_permute,
    }
    for variant, transform in variants.items():
        target = root / variant
        copy_source(source, target, args.force)
        if variant == "candidate-order-shuffle":
            info = transform(target, args.seed)
        elif variant == "candidate-id-permute":
            info = transform(target, args.seed)
        else:
            info = transform(target)
        update_readme(target, variant, info)
        summaries.append({"variant": variant, "path": str(target), **info})

    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "output_root": str(root),
        "variants": summaries,
    }
    (root / "robustness-variants-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
