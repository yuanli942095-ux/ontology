from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from extract_dosd_medical_amendment_table_events import extract_dual_column_appendix_section

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "data/dosd-source-cache/medical-v2/MEDCMP_005-ng222-appendices.txt").read_text(
    encoding="utf-8", errors="replace"
).splitlines()
rows = extract_dual_column_appendix_section(lines, "Appendix 2. Amended recommendation wording", "MEDCMP_005")
print("count", len(rows))
print("has 187", any(r["old_recommendation_id"] == "1.8.1.7" for r in rows))
