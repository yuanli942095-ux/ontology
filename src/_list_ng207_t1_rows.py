from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from extract_dosd_medical_amendment_table_events import extract_replacement_deletion_table_section

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "data/dosd-source-cache/medical-v2/MEDCMP_015-ng207-supplement6.txt").read_text(
    encoding="utf-8", errors="replace"
).splitlines()
rows = extract_replacement_deletion_table_section(
    lines,
    "Table 1: Recommendations that have been deleted",
    "Table 2:",
    "MEDCMP_015",
)
print("count", len(rows))
for row in rows:
    print(row["old_recommendation_id"], row["new_recommendation_id"])
