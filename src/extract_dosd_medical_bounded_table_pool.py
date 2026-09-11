from __future__ import annotations

import argparse
import csv
import re
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from dosd_multidomain_common import sha256_text, write_csv
from extract_dosd_medical_amendment_table_events import classify_change, clean_text


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROWS = [
    {
        "source_id": "MEDCMP_001",
        "title": "NICE NG25 Preterm labour and birth amended recommendation wording",
        "pdf": ROOT / "data/dosd-source-cache/medical-v2/MED2_SRC_003-amendment-appendix.pdf",
        "url": "https://www.nice.org.uk/guidance/ng25/evidence/appendix-2-amended-recommendation-wording-change-to-intent-without-an-evidence-review-june-2022-update-pdf-11080670366",
        "old_version": "2015 guideline",
        "new_version": "current guideline amended 2022",
    },
    {
        "source_id": "MEDCMP_005",
        "title": "NICE NG222 Depression in adults appendices 1 and 2",
        "pdf": ROOT / "data/dosd-source-cache/medical-v2/MEDCMP_005-ng222-appendices.pdf",
        "url": "https://www.nice.org.uk/guidance/ng222/evidence/appendices-1-and-2-recommendations-that-have-been-deleted-or-changed-without-an-evidence-review-pdf-11130965533",
        "old_version": "2009 guideline",
        "new_version": "current guideline amended 2022",
    },
    {
        "source_id": "MEDCMP_006",
        "title": "NICE NG229 Fetal monitoring in labour appendix 1",
        "pdf": ROOT / "data/dosd-source-cache/medical-v2/MEDCMP_006-ng229-appendix1.pdf",
        "url": "https://www.nice.org.uk/guidance/ng229/evidence/appendix-1-recommendations-that-have-been-deleted-or-changed-without-an-evidence-review-pdf-11314226413",
        "old_version": "2014 guideline",
        "new_version": "current guideline",
    },
    {
        "source_id": "MEDCMP_007",
        "title": "NICE NG246 Overweight and obesity management appendix Q",
        "pdf": ROOT / "data/dosd-source-cache/medical-v2/MEDCMP_007-ng246-appendixq.pdf",
        "url": "https://www.nice.org.uk/guidance/ng246/evidence/cg189-appendix-q-recommendations-from-nice-guideline-cg43-deleted-or-changed-pdf-6960327451",
        "old_version": "2006 guideline",
        "new_version": "current guideline",
    },
    {
        "source_id": "MEDCMP_009",
        "title": "NICE CG184 Dyspepsia and gastro-oesophageal reflux disease appendix J",
        "pdf": ROOT / "data/dosd-source-cache/medical-v2/MEDCMP_009-cg184-appendixj.pdf",
        "url": "https://www.nice.org.uk/guidance/cg184/evidence/appendix-j-recommendations-from-cg17-pdf-6955335254",
        "old_version": "2004 guideline",
        "new_version": "2014 guideline",
    },
    {
        "source_id": "MEDCMP_010",
        "title": "NICE NG47 supporting evidence 4",
        "pdf": ROOT / "data/dosd-source-cache/medical-v2/MEDCMP_010-ng47-supporting-evidence-4.pdf",
        "url": "https://www.nice.org.uk/guidance/ng47/documents/supporting-evidence-4",
        "old_version": "previous guideline",
        "new_version": "current guideline",
    },
]
NS = {"x": "http://www.w3.org/1999/xhtml"}
REC_ID = re.compile(r"^\d+\.\d+(?:\.\d+)?\.?$")
DATE_TAG = re.compile(r"\[\d{4}(?:,\s*amended\s*\d{4})?\]")
NOISE = re.compile(r"(?i)(appendix \d|recommendation in|reason for change|© nice|isbn:|subject to notice)")
TEMPORARILY_EXCLUDED_SOURCES = {"MEDCMP_006"}
CONTAMINATED_ROW = re.compile(
    r"(?i)(recommendations from nice guideline|clinical guideline \d+ that have been guideline|"
    r"deleted or changed| as soon is | are washout| there an inadequate|therapy 2 is|"
    r"or 1 reflux disease|or 2014\]|\bguideline symptoms\b)"
)


def ensure_bbox(pdf: Path) -> Path:
    bbox = pdf.with_name(pdf.stem + "-bbox.html")
    if not bbox.is_file() or bbox.stat().st_mtime < pdf.stat().st_mtime:
        result = subprocess.run(
            ["pdftotext", "-bbox-layout", str(pdf), str(bbox)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
    return bbox


def line_text(words: list[tuple[float, str]]) -> str:
    return " ".join(word for _, word in sorted(words))


def extract_column_lines(bbox: Path) -> dict[str, list[tuple[float, str]]]:
    root = ET.parse(bbox).getroot()
    columns = {"old": (50.0, 220.0), "new": (220.0, 380.0), "reason": (380.0, 560.0)}
    out = {name: [] for name in columns}
    for page_index, page in enumerate(root.findall(".//x:page", NS)):
        page_offset = page_index * 1000.0
        buckets: dict[tuple[str, float], list[tuple[float, str]]] = {}
        for word in page.findall(".//x:word", NS):
            token = "".join(word.itertext()).strip()
            if not token:
                continue
            x_min = float(word.attrib["xMin"])
            y_min = float(word.attrib["yMin"])
            for name, (x0, x1) in columns.items():
                if x0 <= x_min < x1:
                    y_key = round((page_offset + y_min) / 3.0) * 3.0
                    buckets.setdefault((name, y_key), []).append((x_min, token))
                    break
        for (name, y_key), words in buckets.items():
            text = line_text(words)
            if text and not NOISE.search(text):
                out[name].append((y_key, text))
    for name in out:
        out[name].sort()
    return out


def starts(lines: list[tuple[float, str]]) -> list[tuple[int, float, str]]:
    found: list[tuple[int, float, str]] = []
    for index, (y, text) in enumerate(lines):
        first = text.split(maxsplit=1)[0] if text.split() else ""
        if REC_ID.match(first):
            found.append((index, y, first.rstrip(".")))
    return found


def join_between(lines: list[tuple[float, str]], y0: float, y1: float) -> str:
    return clean_text([text for y, text in lines if y0 <= y < y1])


def nearest_start_after(candidates: list[tuple[int, float, str]], y0: float, y1: float) -> tuple[int, float, str] | None:
    scoped = [item for item in candidates if y0 - 6 <= item[1] < y1]
    if not scoped:
        return None
    return min(scoped, key=lambda item: abs(item[1] - y0))


def extract_source(source: dict[str, object]) -> list[dict[str, str]]:
    if source["source_id"] in TEMPORARILY_EXCLUDED_SOURCES:
        return []
    pdf = Path(source["pdf"])
    cols = extract_column_lines(ensure_bbox(pdf))
    old_starts = starts(cols["old"])
    new_starts = starts(cols["new"])
    rows: list[dict[str, str]] = []
    for position, (_, y0, old_id) in enumerate(old_starts):
        y1 = old_starts[position + 1][1] if position + 1 < len(old_starts) else 999999.0
        new_start = nearest_start_after(new_starts, y0, y1)
        if not new_start:
            continue
        _, new_y, new_id = new_start
        reason_candidates = [(y, text) for y, text in cols["reason"] if y0 - 9 <= y < y1]
        if not reason_candidates:
            continue
        reason_y = min(y for y, _ in reason_candidates)
        old = join_between(cols["old"], y0, y1)
        new = join_between(cols["new"], new_y, y1)
        reason = join_between(cols["reason"], reason_y, y1)
        if min(len(old), len(new), len(reason)) < 45:
            continue
        if DATE_TAG.sub("", old).strip() == DATE_TAG.sub("", new).strip():
            continue
        merged = f"{old} {new} {reason}".lower()
        candidate_text = f"{old} {new}"
        if (
            "© nice" in merged
            or "isbn:" in merged
            or "recommendations in the 2009 guideline" in merged
            or "evidence review (this table" in merged
            or old.lower().startswith("1.10.4.")
            or CONTAMINATED_ROW.search(candidate_text)
        ):
            continue
        rows.append({
            "source_id": str(source["source_id"]),
            "source_title": str(source["title"]),
            "source_url": str(source["url"]),
            "old_version": str(source["old_version"]),
            "new_version": str(source["new_version"]),
            "old_recommendation_id": old_id,
            "new_recommendation_id": new_id,
            "old_span": old,
            "new_span": new,
            "reason_for_change": reason,
            "semantic_type": classify_change(old, new, reason),
            "source_quality_gate": "OFFICIAL_NICE_AMENDMENT_TABLE_BBOX_COLUMN_ROW",
            "row_sha256": sha256_text(f"{source['source_id']}|{old}|{new}|{reason}"),
        })
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output/dosd-medical-v2-rebuild/medical-bbox-table-pool.csv")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows: list[dict[str, str]] = []
    for source in SOURCE_ROWS:
        if Path(source["pdf"]).is_file():
            rows.extend(extract_source(source))
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for row in rows:
        if row["row_sha256"] not in seen:
            seen.add(row["row_sha256"])
            deduped.append(row)
    write_csv(args.output, deduped)
    print({"rows": len(deduped), "by_source": dict(Counter(row["source_id"] for row in deduped)), "by_type": dict(Counter(row["semantic_type"] for row in deduped)), "output": str(args.output)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
