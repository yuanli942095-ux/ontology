from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from extract_dosd_medical_amendment_table_events import (
    extract_dual_column_appendix_section,
    REASON_HINT,
    REASON_COLUMN_LEAK,
    NEW_SPAN_CONTAMINATION,
    clean_text,
    sha256_text,
    infer_layout_table_columns,
    table_column_gaps,
    split_layout_table_line,
)
import re
from extract_dosd_medical_amendment_table_events import RECOMMENDATION_ID

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "data/dosd-source-cache/medical-v2/MEDCMP_015-ng207-supplement6.txt").read_text(
    encoding="utf-8", errors="replace"
).splitlines()
joined = "\n".join(lines)
section = joined.split("Table 2: Amended recommendation wording", 1)[1]
section_lines = section.splitlines()
fallback_new_col, fallback_reason_col = infer_layout_table_columns(section_lines)
row_starts = [i for i, line in enumerate(section_lines) if len(re.findall(rf"\b({RECOMMENDATION_ID})\b", line)) >= 2]
print("starts", len(row_starts))
for position, start in enumerate(row_starts):
    end = row_starts[position + 1] if position + 1 < len(row_starts) else len(section_lines)
    first = section_lines[start].replace("\f", "")
    id_matches = list(re.finditer(rf"\b({RECOMMENDATION_ID})\b", first))
    old_id, new_id = id_matches[0].group(1), id_matches[1].group(1)
    first_gaps = table_column_gaps(first)
    if len(first_gaps) >= 2:
        row_new_col, row_reason_col = first_gaps[0][1], first_gaps[1][1]
    else:
        row_new_col, row_reason_col = fallback_new_col, fallback_reason_col
    old_parts, new_parts, reason_parts = [], [], []
    for raw_line in section_lines[start:end]:
        old_part, new_part, reason_part = split_layout_table_line(raw_line, row_new_col, row_reason_col)
        if not old_part.strip() and not new_part.strip() and not reason_part.strip():
            continue
        old_parts.append(old_part)
        new_parts.append(new_part)
        reason_parts.append(reason_part)
    old = clean_text(old_parts)
    new = clean_text(new_parts)
    reason = clean_text(reason_parts)
    if min(len(old), len(new), len(reason)) < 40:
        print("REJECT", old_id, new_id, "short", len(old), len(new), len(reason))
        continue
    if not REASON_HINT.search(reason):
        print("REJECT", old_id, new_id, "no_reason_hint", reason[:80])
        continue
    if REASON_COLUMN_LEAK.search(reason):
        print("REJECT", old_id, new_id, "leak", reason[:80])
        continue
    if NEW_SPAN_CONTAMINATION.search(new):
        print("REJECT", old_id, new_id, "contam", new[:80])
