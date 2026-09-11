from __future__ import annotations

import argparse
import csv
import json
import random
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from dosd_multidomain_common import compact_token, operation_json, owl_text, sha256_text, write_csv


ROOT = Path(__file__).resolve().parents[1]
SOURCE_META = {
    "MEDCMP_001": {
        "source_family": "NICE_NG25_AMENDMENT_TABLE",
        "title": "Preterm labour and birth Appendix 2 amended recommendation wording",
        "subject": "NICE NG25 preterm labour and birth",
        "url": "https://www.nice.org.uk/guidance/ng25/evidence/appendix-2-amended-recommendation-wording-change-to-intent-without-an-evidence-review-june-2022-update-pdf-11080670366",
        "old_version": "2015 guideline",
        "new_version": "current guideline amended 2022",
        "cache_path": "data/dosd-source-cache/medical-v2/MED2_SRC_003-amendment-appendix.txt",
    },
    "MEDCMP_005": {
        "source_family": "NICE_NG222_AMENDMENT_TABLE",
        "title": "Depression in adults appendices 1 and 2",
        "subject": "NICE NG222 depression in adults",
        "url": "https://www.nice.org.uk/guidance/ng222/evidence/appendices-1-and-2-recommendations-that-have-been-deleted-or-changed-without-an-evidence-review-pdf-11130965533",
        "old_version": "2009 guideline",
        "new_version": "current guideline amended 2022",
        "cache_path": "data/dosd-source-cache/medical-v2/MEDCMP_005-ng222-appendices.txt",
    },
    "MEDCMP_006": {
        "source_family": "NICE_NG229_AMENDMENT_TABLE",
        "title": "Fetal monitoring in labour appendix 1",
        "subject": "NICE NG229 fetal monitoring in labour",
        "url": "https://www.nice.org.uk/guidance/ng229/evidence/appendix-1-recommendations-that-have-been-deleted-or-changed-without-an-evidence-review-pdf-11314226413",
        "old_version": "2014 guideline",
        "new_version": "current guideline",
        "cache_path": "data/dosd-source-cache/medical-v2/MEDCMP_006-ng229-appendix1.txt",
    },
    "MEDCMP_009": {
        "source_family": "NICE_CG184_AMENDMENT_TABLE",
        "title": "Dyspepsia and gastro-oesophageal reflux disease appendix J",
        "subject": "NICE CG184 dyspepsia and gastro-oesophageal reflux disease",
        "url": "https://www.nice.org.uk/guidance/cg184/evidence/appendix-j-recommendations-from-cg17-pdf-6955335254",
        "old_version": "2004 guideline",
        "new_version": "2014 guideline",
        "cache_path": "data/dosd-source-cache/medical-v2/MEDCMP_009-cg184-appendixj.txt",
    },
    "MEDCMP_012": {
        "source_family": "NICE_NG18_AMENDMENT_TABLE",
        "title": "Diabetes children and young people appendix A",
        "subject": "NICE NG18 diabetes in children and young people",
        "url": "https://www.nice.org.uk/guidance/ng18/evidence/appendices-an-pdf-435396353",
        "old_version": "2004 guideline",
        "new_version": "current guideline amended 2015",
        "cache_path": "data/dosd-source-cache/medical-v2/MEDCMP_012-ng18-appendices-an.txt",
    },
    "MEDCMP_015": {
        "source_family": "NICE_NG207_AMENDMENT_TABLE",
        "title": "Inducing labour supplement 6 summary of deleted and amended recommendations",
        "subject": "NICE NG207 inducing labour",
        "url": "https://www.nice.org.uk/guidance/ng207/evidence/supplement-6-summary-of-deleted-and-amended-recommendations-pdf-10884146226",
        "old_version": "2008 guideline",
        "new_version": "current guideline amended 2021",
        "cache_path": "data/dosd-source-cache/medical-v2/MEDCMP_015-ng207-supplement6.txt",
    },
    "MEDHTML_003": {
        "source_family": "NICE_SCHIZOPHRENIA_HTML_AMENDMENT_TABLE",
        "title": "Schizophrenia recommendations amended table",
        "subject": "NICE schizophrenia recommendations",
        "url": "https://www.ncbi.nlm.nih.gov/books/NBK555203/table/cha.tab1/?report=objectonly",
        "old_version": "2009 guideline",
        "new_version": "current guideline amended 2014",
        "cache_path": "data/dosd-source-cache/medical-v2/MEDHTML_003-ncbi-schizophrenia-table.html",
    },
    "MEDHTML_004": {
        "source_family": "NICE_MND_HTML_AMENDMENT_TABLE",
        "title": "Motor neurone disease amended recommendation wording",
        "subject": "NICE motor neurone disease recommendations",
        "url": "https://www.ncbi.nlm.nih.gov/books/NBK554746/table/cha.tab3/?report=objectonly",
        "old_version": "2010 guideline",
        "new_version": "current guideline amended 2016",
        "cache_path": "data/dosd-source-cache/medical-v2/MEDHTML_004-ncbi-mnd-table.html",
    },
    "MEDHTML_005": {
        "source_family": "NICE_HYPOTHERMIA_HTML_AMENDMENT_TABLE",
        "title": "Hypothermia amended recommendation wording",
        "subject": "NICE hypothermia prevention and management recommendations",
        "url": "https://www.ncbi.nlm.nih.gov/books/NBK554181/table/cha.tab1/?report=objectonly",
        "old_version": "2008 guideline",
        "new_version": "current guideline",
        "cache_path": "data/dosd-source-cache/medical-v2/MEDHTML_005-table.html",
    },
    "MEDHTML_006": {
        "source_family": "NICE_FH_HTML_AMENDMENT_TABLE",
        "title": "Familial hypercholesterolaemia amended recommendation wording",
        "subject": "NICE familial hypercholesterolaemia recommendations",
        "url": "https://www.ncbi.nlm.nih.gov/books/NBK552672/table/cha.tab1/?report=objectonly",
        "old_version": "2008 guideline",
        "new_version": "current guideline",
        "cache_path": "data/dosd-source-cache/medical-v2/MEDHTML_006-table.html",
    },
    "MEDHTML_007": {
        "source_family": "NICE_FAMILIAL_BREAST_CANCER_HTML_AMENDMENT_TABLE",
        "title": "Familial breast cancer amended recommendation wording",
        "subject": "NICE familial breast cancer recommendations",
        "url": "https://www.ncbi.nlm.nih.gov/books/NBK552606/table/cha.tab3/?report=objectonly",
        "old_version": "2013 guideline",
        "new_version": "current guideline",
        "cache_path": "data/dosd-source-cache/medical-v2/MEDHTML_007-table.html",
    },
    "MEDHTML_008": {
        "source_family": "NICE_PTSD_HTML_AMENDMENT_TABLE",
        "title": "Post-traumatic stress disorder amended recommendation wording",
        "subject": "NICE post-traumatic stress disorder recommendations",
        "url": "https://www.ncbi.nlm.nih.gov/books/NBK542453/table/cha.tab1/?report=objectonly",
        "old_version": "2005 guideline",
        "new_version": "current guideline",
        "cache_path": "data/dosd-source-cache/medical-v2/MEDHTML_008-table.html",
    },
    "MEDHTML_009": {
        "source_family": "NICE_VTE_HTML_AMENDMENT_TABLE",
        "title": "Venous thromboembolism amended recommendation wording",
        "subject": "NICE venous thromboembolism recommendations",
        "url": "https://www.ncbi.nlm.nih.gov/books/NBK561646/table/cha.tab1/?report=objectonly",
        "old_version": "2010 guideline",
        "new_version": "current guideline",
        "cache_path": "data/dosd-source-cache/medical-v2/MEDHTML_009-table.html",
    },
    "MEDHTML_010": {
        "source_family": "NICE_HAEMATOLOGICAL_CANCERS_HTML_AMENDMENT_TABLE",
        "title": "Haematological cancers amended recommendation wording",
        "subject": "NICE haematological cancers recommendations",
        "url": "https://www.ncbi.nlm.nih.gov/books/NBK367648/table/cha.tab2/?report=objectonly",
        "old_version": "2003 guideline",
        "new_version": "current guideline",
        "cache_path": "data/dosd-source-cache/medical-v2/MEDHTML_010-table.html",
    },
    "MEDHTML_011": {
        "source_family": "NICE_ALCOHOL_USE_DISORDERS_HTML_AMENDMENT_TABLE",
        "title": "Alcohol-use disorders physical complications amended recommendation wording",
        "subject": "NICE alcohol-use disorders physical complications recommendations",
        "url": "https://www.ncbi.nlm.nih.gov/books/NBK553260/table/cha.tab1/?report=objectonly",
        "old_version": "2010 guideline",
        "new_version": "current guideline",
        "cache_path": "data/dosd-source-cache/medical-v2/MEDHTML_011-table.html",
    },
}
RECOMMENDATION_ID = r"\d+(?:\.\d+)+"
RECOMMENDATION_START = re.compile(rf"^\s*({RECOMMENDATION_ID})\s")
BRACKET_REC_ID = re.compile(rf"\(({RECOMMENDATION_ID})\)")


def normalize_layout_rec_id(rec_id: str) -> str:
    """Fix PDF layout OCR where leading '1.' merges with the next digit (12.6.10 -> 1.2.6.10)."""
    match = re.fullmatch(r"^(1[0-9])(\.\d+(?:\.\d+)*)$", rec_id)
    if match and int(match.group(1)) >= 10:
        return f"1.{match.group(1)[1:]}{match.group(2)}"
    return rec_id


def bracket_rec_ids(text: str) -> list[str]:
    return [normalize_layout_rec_id(rec_id) for rec_id in BRACKET_REC_ID.findall(text)]


REASON_HINT = re.compile(
    r"(?i)\b(the |it |wording|rationale|action|type|fifth|use of|time to|lower limit|has been|changed|amended|included|removed|simplified|clarified)\b"
)
NEW_SPAN_CONTAMINATION = re.compile(
    r"(?i)\b(the wording has|it has been clarified|the rationale for|the action to be|the type of|the fifth bullet|"
    r"the use of the term|the time to clamping|the lower limit for|has been changed from|has been amended to)\b"
)
REASON_COLUMN_LEAK = re.compile(
    rf"\b{RECOMMENDATION_ID}\s+(?:Offer|Do|Be|If|Use|Advise|Explain|Consider|Carry|Review|Take|Categorise|"
    r"Return|Perform|Discuss|Ensure|Inform|Transfer|Make|Address|Supplement|Categorise)\b"
)
PROSE_APPENDIX_NOISE = re.compile(
    r"(?i)^(diabetes \(type|recommendations from nice|update 2015|national collaborating|appendix a:|"
    r"recommendation numbers in the table)"
)
ROW_START_ID = re.compile(rf"^\s*({RECOMMENDATION_ID})\b")
REPLACEMENT_MARKER = re.compile(r"(?i)\breplaced (?:with|by):\s*")
REPLACEMENT_CARRIED_OUT = re.compile(r"(?i)(?:evidence|idence) review was carried out:\s*")
REPLACEMENT_COMMENT_START = re.compile(r"(?i)\bThis (?:recommendation|heading)\b")
REPLACEMENT_ROW_SPLIT = re.compile(
    rf"^(?P<prefix>\s*{RECOMMENDATION_ID}\b.+?)(\s{{2,}})(?P<comment>This (?:recommendation|heading)\b.*)$"
)
REPLACEMENT_ROW_LEAKED_COMMENT = re.compile(
    rf"^(?P<prefix>\s*{RECOMMENDATION_ID}\b.+?)(?P<comment>\s+by new recommendations\b.*)$"
)


def replacement_new_span(comment: str) -> str:
    """Return explicit replacement recommendation text from a NICE deletion-table comment."""
    if comment.lower().startswith("by new recommendations"):
        comment = f"This recommendation has been replaced {comment}"
    match = REPLACEMENT_MARKER.search(comment)
    if match:
        return clean_text([comment[match.end() :]])
    if not re.search(r"(?i)\breplaced\b", comment):
        return ""
    carried = REPLACEMENT_CARRIED_OUT.search(comment)
    if carried:
        return clean_text([comment[carried.end() :]])
    singular = re.search(
        r"(?i)replaced (?:with|by) (?:a )?new recommendation(?:s)?(?: as a new)?(?: evidence review was carried out:)?\s*",
        comment,
    )
    if singular:
        return clean_text([comment[singular.end() :]])
    id_match = re.search(rf"(?i)(?:carried out:\s*)?(({RECOMMENDATION_ID})\b.+)", comment)
    if id_match:
        return clean_text([id_match.group(1)])
    return ""


def infer_replacement_comment_col(section_lines: list[str], fallback: int = 40) -> int:
    for line in section_lines[:80]:
        match = REPLACEMENT_ROW_SPLIT.match(line.replace("\f", ""))
        if match:
            return match.start("comment")
        if "Recommendation in" in line and "Comment" in line:
            gaps = table_column_gaps(line)
            if gaps:
                return gaps[0][1]
    return fallback


def clean_text(parts: list[str]) -> str:
    value = re.sub(r"\s+", " ", " ".join(part.strip() for part in parts if part.strip())).strip()
    return value.replace(" ,", ",").replace(" .", ".")


def classify_change(old: str, new: str, reason: str) -> str:
    old_clean = re.sub(r"\[\d{4}(?:,\s*amended\s*\d{4})?\]", " ", old.lower())
    new_clean = re.sub(r"\[\d{4}(?:,\s*amended\s*\d{4})?\]", " ", new.lower())
    text = f"{old_clean} {new_clean} {reason.lower()}"
    temporal_value = re.compile(r"\b(?:\d+\+\d+|\d+\s*(?:seconds|minutes|days|weeks))\b")
    if set(temporal_value.findall(old_clean)) != set(temporal_value.findall(new_clean)):
        return "TEMPORAL_VERSION"
    if re.search(r"\b(if|unless|except|condition|when|where|risk|contraindicat|negative|positive|do not|offer|reduce|stop)\b", text):
        return "GENERAL_RULE_EXCEPTION"
    return "CROSS_SENTENCE_SCOPE"


def table_column_gaps(line: str) -> list[tuple[int, int]]:
    return [(match.start(), match.end()) for match in re.finditer(r" {4,}", line)]


def infer_layout_table_columns(section_lines: list[str]) -> tuple[int, int]:
    for line in section_lines[:30]:
        if "Reason for change" not in line:
            continue
        gaps = table_column_gaps(line)
        if len(gaps) >= 2:
            return gaps[0][1], gaps[1][1]
    return 33, 73


def split_layout_table_line(
    line: str,
    fallback_new_col: int,
    fallback_reason_col: int,
) -> tuple[str, str, str]:
    line = line.replace("\f", "")
    gaps = table_column_gaps(line)
    if len(gaps) >= 2:
        return line[: gaps[0][0]], line[gaps[0][1] : gaps[1][0]], line[gaps[1][1] :]
    if len(gaps) == 1:
        return line[: gaps[0][0]], line[gaps[0][1] :], ""
    return line[:fallback_new_col], line[fallback_new_col:fallback_reason_col], line[fallback_reason_col:]


def reason_col_on_line(line: str, new_col: int) -> int:
    segment = line[new_col:]
    matches = list(re.finditer(r"\s{4,}([A-Za-z])", segment))
    if matches:
        return new_col + matches[-1].start(1)
    return max(new_col + 32, 69)


def trim_reason_leak_from_new(new: str, reason: str) -> tuple[str, str]:
    for marker in (
        " relating to onset of",
        " relating to ",
        " The language relating",
        " wherever induction of",
        " updated from",
        " gestational age has",
    ):
        idx = new.find(marker)
        if idx > 40:
            return new[:idx].strip(), clean_text([reason, new[idx:].strip()])
    return new, reason


def trim_new_column_leak_from_reason(reason: str) -> str:
    """Drop new-column recommendation prose that leaked into the reason column."""
    leak = REASON_COLUMN_LEAK.search(reason)
    if leak and leak.start() > 60:
        return reason[: leak.start()].strip()
    return reason


def is_dual_column_row_start(line: str) -> bool:
    """True when a line begins a dual-ID amendment row (not a parenthetical ID list)."""
    ids = re.findall(rf"\b({RECOMMENDATION_ID})\b", line)
    if len(ids) < 2:
        return False
    stripped = line.strip()
    if stripped.endswith(")") and re.search(rf"\b{ids[0]}\b and \b{ids[1]}\b", stripped):
        return False
    prose = re.sub(rf"\b{RECOMMENDATION_ID}\b", "", line)
    prose = re.sub(r"\s+", " ", prose).strip()
    return len(prose) >= 15


def extract_dual_column_appendix_section(
    lines: list[str],
    appendix_marker: str,
    source_id: str,
    source_quality_gate: str = "OFFICIAL_NICE_AMENDMENT_TABLE_LAYOUT_ROW",
) -> list[dict[str, str]]:
    """Extract rows from NICE PDF layout text where each row begins with old/new IDs."""
    joined = "\n".join(lines)
    if appendix_marker not in joined:
        return []
    section = joined.split(appendix_marker, 1)[1]
    lines = section.splitlines()
    fallback_new_col, fallback_reason_col = infer_layout_table_columns(lines)

    row_starts: list[int] = []
    for index, line in enumerate(lines):
        if is_dual_column_row_start(line):
            row_starts.append(index)

    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for position, start in enumerate(row_starts):
        end = row_starts[position + 1] if position + 1 < len(row_starts) else len(lines)
        first = lines[start].replace("\f", "")
        id_matches = list(re.finditer(rf"\b({RECOMMENDATION_ID})\b", first))
        if len(id_matches) < 2:
            continue
        old_id = id_matches[0].group(1)
        new_id = id_matches[1].group(1)
        first_gaps = table_column_gaps(first)
        if len(first_gaps) >= 2:
            row_new_col, row_reason_col = first_gaps[0][1], first_gaps[1][1]
        else:
            row_new_col = fallback_new_col
            row_reason_col = fallback_reason_col
        old_parts: list[str] = []
        new_parts: list[str] = []
        reason_parts: list[str] = []
        for raw_line in lines[start:end]:
            if raw_line.strip().startswith("©"):
                continue
            if "isbn:" in raw_line.lower():
                continue
            old_part, new_part, reason_part = split_layout_table_line(
                raw_line,
                row_new_col,
                row_reason_col,
            )
            if not old_part.strip() and not new_part.strip() and not reason_part.strip():
                continue
            old_parts.append(old_part)
            new_parts.append(new_part)
            reason_parts.append(reason_part)
        old = clean_text(old_parts)
        new = clean_text(new_parts)
        reason = clean_text(reason_parts)
        new, reason = trim_reason_leak_from_new(new, reason)
        reason = trim_new_column_leak_from_reason(reason)
        if min(len(old), len(new), len(reason)) < 40:
            continue
        if old == new:
            continue
        contaminated = " ".join((old, new, reason)).lower()
        if "© nice" in contaminated or "isbn:" in contaminated or "subject to notice of rights" in contaminated:
            continue
        if NEW_SPAN_CONTAMINATION.search(new):
            continue
        if not REASON_HINT.search(reason):
            continue
        if REASON_COLUMN_LEAK.search(reason):
            continue
        key = (old_id, new_id, sha256_text(new)[:16])
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "source_id": source_id,
            "old_recommendation_id": old_id,
            "new_recommendation_id": new_id,
            "old_span": old,
            "new_span": new,
            "reason_for_change": reason,
            "semantic_type": classify_change(old, new, reason),
            "source_quality_gate": source_quality_gate,
        })
    return rows


def extract_replacement_deletion_table_section(
    lines: list[str],
    table_marker: str,
    end_marker: str,
    source_id: str,
    source_quality_gate: str = "OFFICIAL_NICE_REPLACEMENT_DELETION_TABLE_ROW",
) -> list[dict[str, str]]:
    """Extract NG229-style deletion-table rows where the comment column gives explicit replacement text."""
    joined = "\n".join(lines)
    if table_marker not in joined:
        return []
    section = joined.split(table_marker, 1)[1]
    if end_marker in section:
        section = section.split(end_marker, 1)[0]
    section_lines = section.splitlines()
    comment_col = infer_replacement_comment_col(section_lines)

    rows: list[dict[str, str]] = []
    old_parts: list[str] = []
    comment_parts: list[str] = []
    old_id = ""

    def split_replacement_line(raw_line: str) -> tuple[str, str]:
        line = raw_line.replace("\f", "")
        for pattern in (REPLACEMENT_ROW_SPLIT, REPLACEMENT_ROW_LEAKED_COMMENT):
            match = pattern.match(line)
            if match:
                return match.group("prefix"), match.group("comment").lstrip()
        return line[:comment_col], line[comment_col:]

    def flush_row() -> None:
        nonlocal old_id, old_parts, comment_parts
        if not old_id:
            return
        old = clean_text(old_parts)
        comment = clean_text(comment_parts)
        new_span = replacement_new_span(comment)
        if not new_span or min(len(old), len(comment)) < 40:
            reset_row()
            return
        reason_for_change = comment
        if not new_span or len(new_span) < 25:
            reset_row()
            return
        new_ids = re.findall(rf"\b({RECOMMENDATION_ID})\b", new_span)
        if not new_ids:
            reset_row()
            return
        rows.append({
            "source_id": source_id,
            "old_recommendation_id": old_id,
            "new_recommendation_id": new_ids[0],
            "old_span": old,
            "new_span": new_span,
            "reason_for_change": reason_for_change,
            "semantic_type": classify_change(old, new_span, reason_for_change),
            "source_quality_gate": source_quality_gate,
        })
        reset_row()

    def reset_row() -> None:
        nonlocal old_id
        old_id = ""
        old_parts.clear()
        comment_parts.clear()

    def spillover_to_comment(old_part: str, comment_part: str) -> bool:
        prev_comment = clean_text(comment_parts)
        if prev_comment.endswith(" as a new") or prev_comment.endswith(" as a new evidence"):
            return True
        combined = f"{old_part} {comment_part}".lower()
        if "idence review was carried out" in combined and not ROW_START_ID.match(old_part.strip()):
            return True
        return False

    for raw_line in section_lines:
        if raw_line.strip().startswith("©"):
            continue
        old_part, comment_part = split_replacement_line(raw_line)
        start_match = ROW_START_ID.match(old_part)
        if start_match and old_parts:
            flush_row()
        if start_match:
            old_id = start_match.group(1)
            old_parts = [old_part]
            comment_parts = [comment_part]
            continue
        if old_parts:
            if spillover_to_comment(old_part, comment_part):
                comment_parts.append(clean_text([old_part, comment_part]))
                continue
            old_parts.append(old_part)
            comment_parts.append(comment_part)
    flush_row()
    return rows


def extract_triple_column_prose_appendix_section(
    lines: list[str],
    appendix_marker: str,
    end_marker: str,
    source_id: str,
    source_quality_gate: str = "OFFICIAL_NICE_AMENDMENT_TABLE_LAYOUT_ROW",
) -> list[dict[str, str]]:
    """Extract rows from prose-style NICE amendment tables with bracket recommendation IDs."""
    joined = "\n".join(lines)
    if appendix_marker not in joined:
        return []
    section = joined.split(appendix_marker, 1)[1]
    if end_marker in section:
        section = section.split(end_marker, 1)[0]
    section_lines = section.splitlines()
    new_col, reason_col = infer_layout_table_columns(section_lines)

    old_parts: list[str] = []
    new_parts: list[str] = []
    reason_parts: list[str] = []
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    last_old_id = ""

    def append_row(old_id: str, new_id: str) -> None:
        old = clean_text(old_parts)
        new = clean_text(new_parts)
        reason = clean_text(reason_parts)
        if min(len(old), len(new), len(reason)) < 40:
            return
        if old == new:
            return
        contaminated = " ".join((old, new, reason)).lower()
        if "© " in contaminated or "isbn:" in contaminated or "draft for consultation" in contaminated:
            return
        if NEW_SPAN_CONTAMINATION.search(new):
            return
        if not REASON_HINT.search(reason):
            return
        if REASON_COLUMN_LEAK.search(reason):
            return
        if old_id == new_id:
            return
        key = (old_id, new_id, sha256_text(new)[:16])
        if key in seen:
            return
        seen.add(key)
        rows.append({
            "source_id": source_id,
            "old_recommendation_id": old_id,
            "new_recommendation_id": new_id,
            "old_span": old,
            "new_span": new,
            "reason_for_change": reason,
            "semantic_type": classify_change(old, new, reason),
            "source_quality_gate": source_quality_gate,
        })
        nonlocal last_old_id
        last_old_id = old_id

    def reset_parts() -> None:
        old_parts.clear()
        new_parts.clear()
        reason_parts.clear()

    def flush_pair(old_id: str, new_id: str) -> None:
        append_row(old_id, new_id)
        reset_parts()

    def column_ends_with_id(part: str) -> bool:
        text = part.strip()
        return bool(text.endswith(")")) and bool(BRACKET_REC_ID.search(text))

    def is_new_row_start(old_part: str) -> bool:
        text = old_part.strip()
        if len(text) < 15 or not text[0].isupper():
            return False
        if text.startswith(("\uf0b7", "•", "o ", "-", "")):
            return False
        return True

    def try_flush_completed_row(old_part: str = "", new_part: str = "") -> None:
        old_ids_acc = bracket_rec_ids(clean_text(old_parts))
        new_ids_acc = bracket_rec_ids(clean_text(new_parts))
        old_ids_line = bracket_rec_ids(old_part)
        new_ids_line = bracket_rec_ids(new_part)
        if old_ids_line and new_ids_line:
            flush_pair(old_ids_line[-1], new_ids_line[-1])
            return
        new_text = new_part.strip()
        if not (new_text.endswith(")") and BRACKET_REC_ID.search(new_text)):
            if not (column_ends_with_id(old_part)):
                return
        old_id = old_ids_acc[0] if old_ids_acc else last_old_id
        if not old_id or not new_ids_acc:
            return
        new_id = bracket_rec_ids(new_text)[-1] if new_text.endswith(")") else new_ids_acc[-1]
        if old_id == new_id:
            return
        flush_pair(old_id, new_id)

    for raw_line in section_lines:
        if "Recommendation in 2004" in raw_line or "Recommendation in current" in raw_line:
            continue
        if PROSE_APPENDIX_NOISE.match(raw_line.strip()):
            continue
        if raw_line.strip().startswith("©"):
            continue
        line = raw_line.replace("\f", "")
        old_part = line[:new_col]
        new_part = line[new_col:reason_col]
        reason_part = line[reason_col:]
        if not old_part.strip() and not new_part.strip() and not reason_part.strip():
            continue
        if is_new_row_start(old_part) and old_parts:
            try_flush_completed_row()
        old_parts.append(old_part)
        new_parts.append(new_part)
        reason_parts.append(reason_part)
        try_flush_completed_row(old_part, new_part)

    return rows


def row_boundaries(lines: list[str]) -> list[tuple[int, int]]:
    starts: list[int] = []
    for index, line in enumerate(lines):
        if RECOMMENDATION_START.match(line.replace("\f", "")):
            starts.append(index)
    return list(zip(starts, starts[1:] + [len(lines)]))


def split_table_row(lines: list[str], start: int, end: int) -> dict[str, str] | None:
    first = lines[start].replace("\f", "")
    matches = list(re.finditer(rf"({RECOMMENDATION_ID})\s", first))
    if len(matches) < 2:
        return None
    old_id = matches[0].group(0).strip()
    new_id = matches[1].group(0).strip()
    new_col = matches[1].start()
    reason_col = max(new_col + 32, 62)
    old_parts: list[str] = []
    new_parts: list[str] = []
    reason_parts: list[str] = []
    for raw_line in lines[start:end]:
        line = raw_line.replace("\f", "")
        if not line.strip():
            continue
        old_parts.append(line[:new_col])
        new_parts.append(line[new_col:reason_col])
        reason_parts.append(line[reason_col:])
    old = clean_text(old_parts)
    new = clean_text(new_parts)
    reason = clean_text(reason_parts)
    if not old or not new:
        return None
    if len(old) < 40 or len(new) < 40:
        return None
    if old == new:
        return None
    return {
        "source_id": "MEDCMP_001",
        "old_recommendation_id": old_id,
        "new_recommendation_id": new_id,
        "old_span": old,
        "new_span": new,
        "reason_for_change": reason,
        "semantic_type": classify_change(old, new, reason),
        "source_quality_gate": "OFFICIAL_NICE_AMENDMENT_TABLE_ROW",
    }


def extract_rows(text_path: Path) -> list[dict[str, str]]:
    raw_text_path = text_path.with_name(text_path.stem + "-raw.txt")
    if raw_text_path.is_file():
        return extract_rows_from_raw(raw_text_path)
    lines = text_path.read_text(encoding="utf-8", errors="replace").splitlines()
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for start, end in row_boundaries(lines):
        row = split_table_row(lines, start, end)
        if not row:
            continue
        key = (row["old_recommendation_id"], sha256_text(row["new_span"])[:16])
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return rows


def extract_rows_from_raw(raw_text_path: Path) -> list[dict[str, str]]:
    """Extract high-confidence rows from pdftotext -raw output.

    NICE amendment PDFs often interleave columns around page breaks.  This
    parser keeps only rows that appear as a clean sequence:
    old recommendation block, current recommendation block, reason block.
    Ambiguous interleaved rows are left for manual processing rather than
    contaminating the benchmark.
    """
    lines = [line.replace("\f", "").strip() for line in raw_text_path.read_text(encoding="utf-8", errors="replace").splitlines()]
    starts = [index for index, line in enumerate(lines) if RECOMMENDATION_START.match(line)]
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    pointer = 0
    while pointer + 1 < len(starts):
        old_start = starts[pointer]
        new_start = starts[pointer + 1]
        next_start = starts[pointer + 2] if pointer + 2 < len(starts) else len(lines)
        old_id = RECOMMENDATION_START.match(lines[old_start]).group(1)  # type: ignore[union-attr]
        new_id = RECOMMENDATION_START.match(lines[new_start]).group(1)  # type: ignore[union-attr]
        if new_start <= old_start or next_start <= new_start:
            pointer += 1
            continue
        new_end = None
        for index in range(new_start, next_start):
            line = lines[index]
            if "amended" in line.lower() and index + 1 < next_start and re.match(r"^\d{4}\]$", lines[index + 1]):
                new_end = index + 2
                break
            if line.lower().startswith("amended ") and index > new_start and "[" in lines[index - 1]:
                new_end = index + 1
                break
            if re.search(r"\[\d{4},\s*amended\s*\d{4}\]", line):
                new_end = index + 1
                break
        if new_end is None:
            pointer += 1
            continue
        reason_lines = [line for line in lines[new_end:next_start] if line]
        if not reason_lines or not REASON_HINT.search(" ".join(reason_lines[:3])):
            pointer += 1
            continue
        old = clean_text(lines[old_start:new_start])
        new = clean_text(lines[new_start:new_end])
        reason = clean_text(reason_lines)
        if min(len(old), len(new), len(reason)) < 40:
            pointer += 1
            continue
        if sha256_text(old) == sha256_text(new):
            pointer += 1
            continue
        contaminated = " ".join((old, new, reason)).lower()
        if "© nice" in contaminated or "isbn:" in contaminated or "subject to notice of rights" in contaminated:
            pointer += 1
            continue
        if NEW_SPAN_CONTAMINATION.search(new):
            pointer += 1
            continue
        key = (old_id, new_id, sha256_text(new)[:16])
        if key not in seen:
            seen.add(key)
            rows.append({
                "source_id": "MEDCMP_001",
                "old_recommendation_id": old_id,
                "new_recommendation_id": new_id,
                "old_span": old,
                "new_span": new,
                "reason_for_change": reason,
                "semantic_type": classify_change(old, new, reason),
                "source_quality_gate": "OFFICIAL_NICE_AMENDMENT_TABLE_ROW_RAW_HIGH_CONFIDENCE",
            })
        pointer += 2
    return rows


def copy_seed(seed: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(seed, target)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def append_events(args: argparse.Namespace) -> dict[str, object]:
    seed = args.seed.resolve()
    target = args.target.resolve()
    output = args.output.resolve()
    source_text = args.source_text.resolve()
    copy_seed(seed, target)
    output.mkdir(parents=True, exist_ok=True)
    extracted = read_csv(args.pool) if args.pool and args.pool.is_file() else extract_rows(source_text)
    write_csv(output / "medical-amendment-table-extracted-pool.csv", extracted)

    event_path = target / "public/events/external-real-event-template.csv"
    document_path = target / "public/documents/external-real-document-template.csv"
    retrieval_path = target / "public/retrieval/external-real-v8-event-retrieval.csv"
    source_family_path = target / "public/retrieval/external-real-v8-grounded-source-families.csv"
    candidate_path = target / "repair-stage/candidates/external-real-candidate-template.csv"
    construction_path = target / "private/construction/construction-provenance.csv"
    ann_a_path = target / "private/annotation/annotation-sheet-A.csv"
    ann_b_path = target / "private/annotation/annotation-sheet-B.csv"

    events = read_csv(event_path)
    documents = read_csv(document_path)
    retrieval = read_csv(retrieval_path)
    source_families = read_csv(source_family_path)
    candidates = read_csv(candidate_path)
    construction = read_csv(construction_path)
    for construction_row in construction:
        construction_row.setdefault("provision_id", "")
    ann_a = read_csv(ann_a_path)
    ann_b = read_csv(ann_b_path)

    now = datetime.now(timezone.utc).isoformat()
    source_text_cache: dict[str, tuple[str, str]] = {}
    existing_sources = {row.get("pair_id") for row in source_families}
    for source_id in sorted({row["source_id"] for row in extracted}):
        meta = SOURCE_META[source_id]
        source_path = ROOT / meta["cache_path"]
        full_text = source_path.read_text(encoding="utf-8", errors="replace")
        source_text_cache[source_id] = (full_text, sha256_text(full_text))
        if source_id not in existing_sources:
            source_families.append({
                "pair_id": source_id,
                "corpus": "medical",
                "source_family": meta["source_family"],
                "jurisdiction": "UK",
                "title": meta["title"],
                "issuer": "NICE",
                "old_version": meta["old_version"],
                "new_version": meta["new_version"],
                "old_url": meta["url"],
                "new_url": meta["url"],
                "license_note": "NICE public guidance evidence document",
                "status": "SUCCESS",
            })

    for index, row in enumerate(extracted, 1):
        event_id = f"MED2_E{index:03d}"
        source_id = row["source_id"]
        meta = SOURCE_META[source_id]
        full_text, full_sha = source_text_cache[source_id]
        old_span = row["old_span"]
        new_span = row["new_span"]
        semantic_type = row["semantic_type"]
        dimension = compact_token(f"{meta['subject']} {row['new_recommendation_id']} {semantic_type}", 8)
        old_value = f"{dimension}={compact_token(old_span)}_{sha256_text(old_span)[:8]}"
        new_value = f"{dimension}={compact_token(new_span)}_{sha256_text(new_span)[:8]}"
        distractor = f"{dimension}=unmodified_or_unrelated_{sha256_text(row['reason_for_change'])[:8]}"
        values = [("OLD", old_value), ("NEW", new_value), ("DISTRACTOR", distractor)]
        random.Random(int(sha256_text(event_id)[:12], 16)).shuffle(values)
        role_to_id = {}
        predicate = re.sub(r"[^a-z0-9_]+", "_", dimension.lower())[:80]
        for cand_index, (role, value) in enumerate(values, 1):
            candidate_id = f"CAND_{cand_index:03d}"
            role_to_id[role] = candidate_id
            candidates.append({
                "event_id": event_id,
                "candidate_id": candidate_id,
                "display_value": value,
                "operation_json": operation_json(event_id, predicate, old_value, value),
                "status": "READY",
                "notes": "Candidate generated from an official amendment-table row before annotation; role and oracle are not exposed to the repair method.",
            })
        old_doc = f"DOC_{event_id}_OLD"
        new_doc = f"DOC_{event_id}_NEW"
        excerpt = (
            f"[OLD_VERSION]\n{old_span}\n\n"
            f"[NEW_VERSION]\n{new_span}\n\n"
            f"[OFFICIAL_REASON_FOR_CHANGE]\n{row['reason_for_change']}\n"
        )
        (target / "public/excerpts" / f"{event_id}-evidence.md").write_text(excerpt, encoding="utf-8")
        for doc_id, column_label in ((old_doc, "Recommendation in old guideline"), (new_doc, "Recommendation in current guideline")):
            file_name = f"{doc_id}.txt"
            (target / "public/documents" / file_name).write_text(full_text, encoding="utf-8")
            documents.append({
                "document_id": doc_id,
                "event_id": event_id,
                "file_name": file_name,
                "authority": "100",
                "effective_from": meta["old_version"] if doc_id.endswith("_OLD") else meta["new_version"],
                "effective_to": "",
                "issuer": "NICE",
                "document_type": f"official_amendment_comparison_table:{column_label}",
                "source_type": "EXTERNAL_PUBLIC_AMENDMENT_TABLE",
                "source_url": meta["url"],
                "retrieved_at": now,
                "raw_sha256": full_sha,
                "text_sha256": full_sha,
                "extraction_mode": "official_amendment_table_row",
                "cache_path": meta["cache_path"],
                "window_sha256": sha256_text(old_span if doc_id.endswith("_OLD") else new_span),
                "sha256": full_sha,
                "status": "READY",
            })
        mutant_path = target / "repair-stage/mutants" / f"{event_id}.owl"
        mutant_path.write_text(owl_text(event_id, predicate, old_value), encoding="utf-8")
        events.append({
            "event_id": event_id,
            "split": "annotation",
            "semantic_type": semantic_type,
            "domain": "medical",
            "title": f"{meta['title']}: amended recommendation {row['new_recommendation_id']}",
            "case_context": "Compare the official NICE amendment table row; determine the evidence-grounded ontology repair.",
            "subject_label": meta["subject"],
            "predicate_label": dimension.replace("_", " "),
            "value_kind": "literal_string",
            "allowed_min": "",
            "allowed_max": "",
            "document_ids": f"{old_doc}|{new_doc}",
            "source_owl": str(mutant_path.relative_to(ROOT)),
            "source_url": meta["url"],
            "retrieved_at": now,
            "status": "PENDING_DUAL_REVIEW",
            "notes": "Extracted from an official NICE amendment comparison table; not gold until dual review and adjudication.",
            "lexical_status": "OFFICIAL_AMENDMENT_TABLE_ROW",
            "support_status": "PENDING_DUAL_REVIEW",
            "semantic_support": "PENDING",
            "support_adjudication_method": "dual_independent_annotation",
            "support_checked_before_model_run": "false",
            "support_gate_version": "dosd-medical-amendment-table-v1",
        })
        retrieval.append({
            "event_id": event_id,
            "domain": "medical",
            "source_id": source_id,
            "query_terms": f"{meta['subject']}|{row['new_recommendation_id']}|{semantic_type}",
            "retrieval_status": "OFFICIAL_AMENDMENT_TABLE_ROW_READY",
            "current_score": "1.0",
            "previous_score": "",
            "current_windows": "1",
            "previous_windows": "1",
            "window_sha256": sha256_text(excerpt),
            "fallback_used": "false",
            "candidate_used": "false",
            "oracle_used": "false",
            "note_used": "false",
            "errors": "",
        })
        construction.append({
            "event_id": event_id,
            "pair_id": source_id,
            "provision_id": row["new_recommendation_id"],
            "automated_alignment_score": "1.0",
            "construction_new_candidate_id": role_to_id["NEW"],
            "status": "PRIVATE_CONSTRUCTION_HINT_NOT_GOLD",
        })
        annotation_row = {
            "event_id": event_id,
            "source_pair": source_id,
            "old_span": old_span,
            "new_span": new_span,
            "candidate_1": values[0][1],
            "candidate_2": values[1][1],
            "candidate_3": values[2][1],
            "annotator_name": "",
            "annotator_drift_type": "",
            "annotator_ontology_change": "",
            "annotator_gold_candidate": "",
            "evidence_sufficient": "",
            "version_relation_valid": "",
            "notes": "",
            "completed_at": "",
        }
        ann_a.append(annotation_row.copy())
        ann_b.append(annotation_row.copy())

    write_csv(event_path, events)
    write_csv(document_path, documents)
    write_csv(retrieval_path, retrieval)
    write_csv(source_family_path, source_families)
    write_csv(candidate_path, candidates)
    write_csv(
        construction_path,
        construction,
        fieldnames=["event_id", "pair_id", "provision_id", "automated_alignment_score", "construction_new_candidate_id", "status"],
    )
    write_csv(ann_a_path, ann_a)
    write_csv(ann_b_path, ann_b)

    summary = {
        "benchmark": target.name,
        "seed_events": len(events) - len(extracted),
        "extracted_amendment_events": len(extracted),
        "total_events": len(events),
        "candidate_rows": len(candidates),
        "type_counts": dict(Counter(row["semantic_type"] for row in events)),
        "status": "DRAFT_REQUIRES_DUAL_REVIEW",
        "source_quality_gate": "official amendment table rows only",
    }
    (output / "medical-amendment-table-build-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (target / "REBUILD_STATUS.md").write_text(
        "# DOSD Medical v2 Expanded Draft\n\n"
        f"- Total events: {summary['total_events']}\n"
        f"- Added official amendment-table events: {summary['extracted_amendment_events']}\n"
        "- Status: draft, requires fresh dual review before freeze.\n"
        "- Construction rule: only rows from explicit official amendment tables are appended; no nearest-neighbor matching is used for these new events.\n",
        encoding="utf-8",
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, default=ROOT / "benchmark/dosd-medical-v2-screened-seed")
    parser.add_argument("--target", type=Path, default=ROOT / "benchmark/dosd-medical-v2-expanded-draft")
    parser.add_argument("--output", type=Path, default=ROOT / "output/dosd-medical-v2-rebuild")
    parser.add_argument(
        "--source-text",
        type=Path,
        default=ROOT / "data/dosd-source-cache/medical-v2/MED2_SRC_003-amendment-appendix.txt",
    )
    parser.add_argument(
        "--pool",
        type=Path,
        default=ROOT / "output/dosd-medical-v2-rebuild/medical-bbox-table-pool.csv",
    )
    return parser.parse_args()


if __name__ == "__main__":
    print(json.dumps(append_events(parse_args()), ensure_ascii=False, indent=2))
