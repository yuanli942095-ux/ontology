from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from extract_dosd_medical_amendment_table_events import (
    extract_dual_column_appendix_section,
    extract_replacement_deletion_table_section,
)

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "data/dosd-source-cache/medical-v2/MEDCMP_015-ng207-supplement6.txt").read_text(
    encoding="utf-8", errors="replace"
).splitlines()

for marker in (
    "Table 2: Amended recommendation wording",
    "Table 2 Amended recommendation wording",
    "Appendix 2. Amended recommendation wording",
):
    rows = extract_dual_column_appendix_section(lines, marker, "MEDCMP_015")
    print("dual", marker[:40], len(rows))

for start, end in (
    ("Table 1: Recommendations that have been deleted", "Table 2:"),
    ("Table 1 Recommendations that have been deleted", "Table 2"),
):
    rows = extract_replacement_deletion_table_section(lines, start, end, "MEDCMP_015")
    print("repl", start[:40], len(rows))
