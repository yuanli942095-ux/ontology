from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from extract_dosd_medical_amendment_table_events import (
    extract_replacement_deletion_table_section,
    extract_dual_column_appendix_section,
)

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "data/dosd-source-cache/medical-v2/MEDCMP_005-ng222-appendices.txt").read_text(
    encoding="utf-8", errors="replace"
).splitlines()

repl = extract_replacement_deletion_table_section(
    lines,
    "Appendix 1. Recommendations that have been deleted",
    "Appendix 2. Amended recommendation wording",
    "MEDCMP_005",
)
dual = extract_dual_column_appendix_section(
    lines,
    "Appendix 2. Amended recommendation wording",
    "MEDCMP_005",
)
print("replacement_rows", len(repl))
print("dual_rows", len(dual))
for row in repl[:3]:
    print("REPL", row["old_recommendation_id"], row["new_recommendation_id"], len(row["old_span"]), len(row["new_span"]))
