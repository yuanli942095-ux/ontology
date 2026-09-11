from __future__ import annotations

import argparse
import html
import re
from collections import Counter
from pathlib import Path

from dosd_multidomain_common import sha256_text, write_csv
from extract_dosd_medical_amendment_table_events import RECOMMENDATION_ID, classify_change, clean_text


ROOT = Path(__file__).resolve().parents[1]
TABLE_SOURCES = [
    {
        "source_id": "MEDHTML_003",
        "source_title": "NCBI Bookshelf / NICE schizophrenia recommendations amended",
        "source_url": "https://www.ncbi.nlm.nih.gov/books/NBK555203/table/cha.tab1/?report=objectonly",
        "old_version": "2009 guideline",
        "new_version": "current guideline amended 2014",
        "html": ROOT / "data/dosd-source-cache/medical-v2/MEDHTML_003-ncbi-schizophrenia-table.html",
    },
    {
        "source_id": "MEDHTML_004",
        "source_title": "NCBI Bookshelf / NICE motor neurone disease amended recommendation wording",
        "source_url": "https://www.ncbi.nlm.nih.gov/books/NBK554746/table/cha.tab3/?report=objectonly",
        "old_version": "2010 guideline",
        "new_version": "current guideline amended 2016",
        "html": ROOT / "data/dosd-source-cache/medical-v2/MEDHTML_004-ncbi-mnd-table.html",
    },
    {
        "source_id": "MEDHTML_005",
        "source_title": "NCBI Bookshelf / NICE hypothermia amended recommendation wording",
        "source_url": "https://www.ncbi.nlm.nih.gov/books/NBK554181/table/cha.tab1/?report=objectonly",
        "old_version": "2008 guideline",
        "new_version": "current guideline",
        "html": ROOT / "data/dosd-source-cache/medical-v2/MEDHTML_005-table.html",
    },
    {
        "source_id": "MEDHTML_006",
        "source_title": "NCBI Bookshelf / NICE familial hypercholesterolaemia amended recommendation wording",
        "source_url": "https://www.ncbi.nlm.nih.gov/books/NBK552672/table/cha.tab1/?report=objectonly",
        "old_version": "2008 guideline",
        "new_version": "current guideline",
        "html": ROOT / "data/dosd-source-cache/medical-v2/MEDHTML_006-table.html",
    },
    {
        "source_id": "MEDHTML_007",
        "source_title": "NCBI Bookshelf / NICE familial breast cancer amended recommendation wording",
        "source_url": "https://www.ncbi.nlm.nih.gov/books/NBK552606/table/cha.tab3/?report=objectonly",
        "old_version": "2013 guideline",
        "new_version": "current guideline",
        "html": ROOT / "data/dosd-source-cache/medical-v2/MEDHTML_007-table.html",
    },
    {
        "source_id": "MEDHTML_008",
        "source_title": "NCBI Bookshelf / NICE post-traumatic stress disorder amended recommendation wording",
        "source_url": "https://www.ncbi.nlm.nih.gov/books/NBK542453/table/cha.tab1/?report=objectonly",
        "old_version": "2005 guideline",
        "new_version": "current guideline",
        "html": ROOT / "data/dosd-source-cache/medical-v2/MEDHTML_008-table.html",
    },
    {
        "source_id": "MEDHTML_009",
        "source_title": "NCBI Bookshelf / NICE venous thromboembolism amended recommendation wording",
        "source_url": "https://www.ncbi.nlm.nih.gov/books/NBK561646/table/cha.tab1/?report=objectonly",
        "old_version": "2010 guideline",
        "new_version": "current guideline",
        "html": ROOT / "data/dosd-source-cache/medical-v2/MEDHTML_009-table.html",
    },
    {
        "source_id": "MEDHTML_010",
        "source_title": "NCBI Bookshelf / NICE haematological cancers amended recommendation wording",
        "source_url": "https://www.ncbi.nlm.nih.gov/books/NBK367648/table/cha.tab2/?report=objectonly",
        "old_version": "2003 guideline",
        "new_version": "current guideline",
        "html": ROOT / "data/dosd-source-cache/medical-v2/MEDHTML_010-table.html",
    },
    {
        "source_id": "MEDHTML_011",
        "source_title": "NCBI Bookshelf / NICE alcohol-use disorders amended recommendation wording",
        "source_url": "https://www.ncbi.nlm.nih.gov/books/NBK553260/table/cha.tab1/?report=objectonly",
        "old_version": "2010 guideline",
        "new_version": "current guideline",
        "html": ROOT / "data/dosd-source-cache/medical-v2/MEDHTML_011-table.html",
    },
]


TAG = re.compile(r"<[^>]+>")
ROW = re.compile(r"<tr\b.*?</tr>", re.IGNORECASE | re.DOTALL)
CELL = re.compile(r"<t[dh]\b.*?</t[dh]>", re.IGNORECASE | re.DOTALL)


def strip_tags(value: str) -> str:
    value = re.sub(r"</(?:p|li|div|br|ul|ol)>", " ", value, flags=re.IGNORECASE)
    return clean_text([html.unescape(TAG.sub(" ", value))])


def extract_source(source: dict[str, object]) -> list[dict[str, str]]:
    raw = Path(source["html"]).read_text(encoding="utf-8", errors="replace")
    rows: list[dict[str, str]] = []
    for tr in ROW.findall(raw):
        cells = [strip_tags(cell) for cell in CELL.findall(tr)]
        if len(cells) != 3:
            continue
        if cells[0].lower().startswith("recommendation in"):
            continue
        old, new, reason = cells
        if min(len(old), len(new)) < 45:
            continue
        if len(reason) < 15:
            continue
        row_hash = sha256_text(f"{source['source_id']}|{old}|{new}|{reason}")
        old_id = re.findall(rf"\b({RECOMMENDATION_ID})\b", old)
        new_id = re.findall(rf"\b({RECOMMENDATION_ID})\b", new)
        rows.append({
            "source_id": str(source["source_id"]),
            "source_title": str(source["source_title"]),
            "source_url": str(source["source_url"]),
            "old_version": str(source["old_version"]),
            "new_version": str(source["new_version"]),
            "old_recommendation_id": old_id[0] if old_id else "",
            "new_recommendation_id": new_id[0] if new_id else "",
            "old_span": old,
            "new_span": new,
            "reason_for_change": reason,
            "semantic_type": classify_change(old, new, reason),
            "source_quality_gate": "OFFICIAL_NICE_HTML_AMENDED_RECOMMENDATION_TABLE_ROW",
            "row_sha256": row_hash,
        })
    write_csv(Path(source["html"]).with_suffix(".extracted.csv"), rows)
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output/dosd-medical-v2-rebuild/medical-html-table-pool.csv")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows: list[dict[str, str]] = []
    for source in TABLE_SOURCES:
        if Path(source["html"]).is_file():
            rows.extend(extract_source(source))
    write_csv(args.output, rows)
    print({"rows": len(rows), "by_source": dict(Counter(row["source_id"] for row in rows)), "by_type": dict(Counter(row["semantic_type"] for row in rows)), "output": str(args.output)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
