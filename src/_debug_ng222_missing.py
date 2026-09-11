from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from extract_dosd_medical_amendment_table_events import extract_dual_column_appendix_section, sha256_text
import re
from extract_dosd_medical_amendment_table_events import RECOMMENDATION_ID

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "data/dosd-source-cache/medical-v2/MEDCMP_005-ng222-appendices.txt").read_text(
    encoding="utf-8", errors="replace"
).splitlines()
rows = extract_dual_column_appendix_section(lines, "Appendix 2. Amended recommendation wording", "MEDCMP_005")
keys = {}
for row in rows:
    key = (row["old_recommendation_id"], row["new_recommendation_id"], sha256_text(row["new_span"])[:16])
    keys.setdefault(key, 0)
    keys[key] += 1
print("rows", len(rows))
joined = "\n".join(lines).split("Appendix 2. Amended recommendation wording", 1)[1]
section_lines = joined.splitlines()
row_starts = [i for i, line in enumerate(section_lines) if len(re.findall(rf"\b({RECOMMENDATION_ID})\b", line)) >= 2]
print("starts", len(row_starts))
for position, start in enumerate(row_starts):
    first = section_lines[start]
    ids = re.findall(rf"\b({RECOMMENDATION_ID})\b", first)
    key_base = (ids[0], ids[1])
    found = any(r["old_recommendation_id"] == ids[0] and r["new_recommendation_id"] == ids[1] for r in rows)
    if not found:
        print("MISSING", ids[0], ids[1], first[:100])
