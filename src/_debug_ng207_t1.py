from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from extract_dosd_medical_amendment_table_events import (
    ROW_START_ID,
    clean_text,
    replacement_new_span,
    table_column_gaps,
)

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "data/dosd-source-cache/medical-v2/MEDCMP_015-ng207-supplement6.txt").read_text(
    encoding="utf-8", errors="replace"
).splitlines()
section = "\n".join(lines).split("Table 1: Recommendations that have been deleted", 1)[1].split("Table 2:", 1)[0]
section_lines = section.splitlines()
comment_col = 40
for line in section_lines[:20]:
    if "Recommendation in" in line and "Comment" in line:
        gaps = table_column_gaps(line)
        if gaps:
            comment_col = gaps[0][1]
            print("header", repr(line), "gaps", gaps, "comment_col", comment_col)
            break

old_parts: list[str] = []
comment_parts: list[str] = []
old_id = ""
for raw_line in section_lines:
    if raw_line.strip().startswith("©"):
        continue
    line = raw_line.replace("\f", "")
    old_part = line[:comment_col]
    comment_part = line[comment_col:]
    start_match = ROW_START_ID.match(old_part)
    if start_match and old_parts:
        old = clean_text(old_parts)
        comment = clean_text(comment_parts)
        new_span = replacement_new_span(comment)
        new_ids = __import__("re").findall(r"\b(\d+\.\d+(?:\.\d+){0,3})\b", new_span)
        print("FLUSH", old_id, "old", len(old), "comment", len(comment), "new", len(new_span), "ids", new_ids[:2])
        if not new_span:
            print("  comment_head", comment[:120])
    if start_match:
        old_id = start_match.group(1)
        old_parts = [old_part]
        comment_parts = [comment_part]
        continue
    if old_parts:
        old_parts.append(old_part)
        comment_parts.append(comment_part)
