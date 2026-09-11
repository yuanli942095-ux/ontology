from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dosd_multidomain_common import sha256_text
from extract_dosd_medical_amendment_table_events import extract_dual_column_appendix_section

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "data/dosd-source-cache/medical-v2/MEDCMP_009-cg184-appendixj.txt").read_text(
    encoding="utf-8", errors="replace"
).splitlines()
rows = extract_dual_column_appendix_section(lines, "Appendix J: Recommendations from NICE", "MEDCMP_009")
bbox: set[str] = set()
with (ROOT / "output/dosd-medical-v2-rebuild/medical-bbox-table-pool.csv").open(encoding="utf-8-sig") as handle:
    for row in csv.DictReader(handle):
        if row["source_id"] == "MEDCMP_009":
            bbox.add(row["row_sha256"])
new = 0
for row in rows:
    digest = sha256_text(
        f"MEDCMP_009|{row['old_span']}|{row['new_span']}|{row['reason_for_change']}"
    )
    if digest not in bbox:
        new += 1
        print("NEW", row["old_recommendation_id"], row["new_recommendation_id"])
print("layout", len(rows), "new_vs_bbox", new)
