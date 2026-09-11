from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from extract_dosd_medical_amendment_table_events import (
    ROW_START_ID,
    clean_text,
    replacement_new_span,
    infer_replacement_comment_col,
    REPLACEMENT_ROW_SPLIT,
    REPLACEMENT_ROW_LEAKED_COMMENT,
)

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "data/dosd-source-cache/medical-v2/MEDCMP_015-ng207-supplement6.txt").read_text(
    encoding="utf-8", errors="replace"
).splitlines()
section = "\n".join(lines).split("Table 1: Recommendations that have been deleted", 1)[1].split("Table 2:", 1)[0]
section_lines = section.splitlines()
comment_col = infer_replacement_comment_col(section_lines)
old_parts: list[str] = []
comment_parts: list[str] = []
old_id = ""
for raw_line in section_lines:
    if raw_line.strip().startswith("©"):
        continue
    line = raw_line.replace("\f", "")
    for pattern in (REPLACEMENT_ROW_SPLIT, REPLACEMENT_ROW_LEAKED_COMMENT):
        match = pattern.match(line)
        if match:
            old_part, comment_part = match.group("prefix"), match.group("comment").lstrip()
            break
    else:
        old_part, comment_part = line[:comment_col], line[comment_col:]
    start_match = ROW_START_ID.match(old_part)
    if start_match and old_parts and start_match.group(1) == "1.2.9.5":
        old = clean_text(old_parts)
        comment = clean_text(comment_parts)
        print("OLD_LEN", len(old))
        print("COMMENT", comment[:200])
        print("NEW_SPAN", replacement_new_span(comment)[:200])
    if start_match:
        if start_match.group(1) == "1.2.9.5":
            old_id = start_match.group(1)
            old_parts = [old_part]
            comment_parts = [comment_part]
            continue
        old_id = start_match.group(1)
        old_parts = [old_part]
        comment_parts = [comment_part]
        continue
    if old_id == "1.2.9.5" and old_parts:
        old_parts.append(old_part)
        comment_parts.append(comment_part)
