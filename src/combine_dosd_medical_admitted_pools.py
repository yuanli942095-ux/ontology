from __future__ import annotations

import argparse
import csv
from pathlib import Path

from dosd_multidomain_common import sha256_text, write_csv


ROOT = Path(__file__).resolve().parents[1]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bbox-pool", type=Path, default=ROOT / "output/dosd-medical-v2-rebuild/medical-bbox-table-pool.csv")
    parser.add_argument("--html-pool", type=Path, default=ROOT / "output/dosd-medical-v2-rebuild/medical-html-table-pool.csv")
    parser.add_argument("--layout-pool", type=Path, default=ROOT / "output/dosd-medical-v2-rebuild/medical-layout-table-pool.csv")
    parser.add_argument("--output", type=Path, default=ROOT / "output/dosd-medical-v2-rebuild/medical-admitted-table-pool.csv")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    layout_rows = read_csv(args.layout_pool)
    layout_sources = {row.get("source_id", "") for row in layout_rows}
    bbox_rows = [
        row for row in read_csv(args.bbox_pool) if row.get("source_id", "") not in layout_sources
    ]
    rows = bbox_rows + read_csv(args.html_pool) + layout_rows
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for row in rows:
        key = row.get("row_sha256") or sha256_text(
            "|".join(
                (
                    row.get("source_id", ""),
                    row.get("old_span", ""),
                    row.get("new_span", ""),
                    row.get("reason_for_change", ""),
                )
            )
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    write_csv(args.output, deduped)
    print({"rows": len(deduped), "output": str(args.output)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
