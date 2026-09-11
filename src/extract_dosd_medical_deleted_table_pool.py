from __future__ import annotations

import argparse
import re
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from dosd_multidomain_common import sha256_text, write_csv
from extract_dosd_medical_amendment_table_events import classify_change, clean_text


ROOT = Path(__file__).resolve().parents[1]
NS = {"x": "http://www.w3.org/1999/xhtml"}
REC_ID = re.compile(r"^\d+\.\d+(?:\.\d+)?\.?$")
NOISE = re.compile(r"(?i)(appendix|table \d|recommendation in|comment|© nice|isbn:|subject to notice)")
COMMENT_HINT = re.compile(r"(?i)\b(deleted|replaced|covered|included|merged|superseded|no longer|not carried forward)\b")
CONTAMINATION = re.compile(r"(?i)(recommendations that have been|this table|evidence review|supporting documents)")


SOURCES = [
    {
        "source_id": "MEDDEL_001",
        "title": "NICE NG25 recommendations that have been deleted",
        "pdf": ROOT / "data/dosd-source-cache/medical-v2/MEDCMP_011-ng25-appendix1-deleted.pdf",
        "url": "https://www.nice.org.uk/guidance/ng25/evidence/appendix-1-recommendations-that-have-been-deleted-june-2022-update-pdf-11080670365",
        "old_version": "2015 guideline",
        "new_version": "current guideline amended 2022",
    },
    {
        "source_id": "MEDDEL_002",
        "title": "NICE NG229 fetal monitoring deleted recommendations",
        "pdf": ROOT / "data/dosd-source-cache/medical-v2/MEDCMP_006-ng229-appendix1.pdf",
        "url": "https://www.nice.org.uk/guidance/ng229/evidence/appendix-1-recommendations-that-have-been-deleted-or-changed-without-an-evidence-review-pdf-11314226413",
        "old_version": "2014 guideline",
        "new_version": "current guideline",
    },
    {
        "source_id": "MEDDEL_003",
        "title": "NICE NG222 depression deleted recommendations",
        "pdf": ROOT / "data/dosd-source-cache/medical-v2/MEDCMP_005-ng222-appendices.pdf",
        "url": "https://www.nice.org.uk/guidance/ng222/evidence/appendices-1-and-2-recommendations-that-have-been-deleted-or-changed-without-an-evidence-review-pdf-11130965533",
        "old_version": "2009 guideline",
        "new_version": "current guideline amended 2022",
    },
    {
        "source_id": "MEDDEL_004",
        "title": "NICE CG184 dyspepsia deleted recommendations",
        "pdf": ROOT / "data/dosd-source-cache/medical-v2/MEDCMP_009-cg184-appendixj.pdf",
        "url": "https://www.nice.org.uk/guidance/cg184/evidence/appendix-j-recommendations-from-cg17-pdf-6955335254",
        "old_version": "2004 guideline",
        "new_version": "2014 guideline",
    },
]


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


def page_lines(page: ET.Element, page_index: int) -> dict[str, list[tuple[float, str]]]:
    columns = {"old": (50.0, 295.0), "comment": (295.0, 560.0)}
    buckets: dict[tuple[str, float], list[tuple[float, str]]] = {}
    page_offset = page_index * 1000.0
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
    out = {name: [] for name in columns}
    for (name, y_key), words in buckets.items():
        text = " ".join(token for _, token in sorted(words))
        if text and not NOISE.search(text):
            out[name].append((y_key, text))
    for name in out:
        out[name].sort()
    return out


def starts(lines: list[tuple[float, str]]) -> list[tuple[int, float, str]]:
    out = []
    for index, (y, text) in enumerate(lines):
        parts = text.split(maxsplit=1)
        if parts and REC_ID.match(parts[0]):
            out.append((index, y, parts[0].rstrip(".")))
    return out


def join(lines: list[tuple[float, str]], y0: float, y1: float) -> str:
    return clean_text([text for y, text in lines if y0 <= y < y1])


def extract_source(source: dict[str, object]) -> list[dict[str, str]]:
    root = ET.parse(ensure_bbox(Path(source["pdf"]))).getroot()
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for page_index, page in enumerate(root.findall(".//x:page", NS)):
        cols = page_lines(page, page_index)
        old_starts = starts(cols["old"])
        if not old_starts:
            continue
        for pos, (_, y0, old_id) in enumerate(old_starts):
            y1 = old_starts[pos + 1][1] if pos + 1 < len(old_starts) else (page_index + 1) * 1000.0
            old = join(cols["old"], y0, y1)
            comment = join(cols["comment"], y0, y1)
            if min(len(old), len(comment)) < 45:
                continue
            if not COMMENT_HINT.search(comment):
                continue
            candidate_text = f"{old} {comment}"
            if CONTAMINATION.search(candidate_text):
                continue
            semantic_type = "TEMPORAL_VERSION" if "replaced by" in comment.lower() else classify_change(old, comment, comment)
            row_hash = sha256_text(f"{source['source_id']}|{old_id}|{old}|{comment}")
            if row_hash in seen:
                continue
            seen.add(row_hash)
            rows.append({
                "source_id": str(source["source_id"]),
                "source_title": str(source["title"]),
                "source_url": str(source["url"]),
                "old_version": str(source["old_version"]),
                "new_version": str(source["new_version"]),
                "old_recommendation_id": old_id,
                "new_recommendation_id": f"{old_id}:deleted_or_replaced",
                "old_span": old,
                "new_span": f"DELETED_OR_REPLACED: {comment}",
                "reason_for_change": comment,
                "semantic_type": semantic_type,
                "source_quality_gate": "OFFICIAL_NICE_DELETED_RECOMMENDATION_TABLE_BBOX_ROW",
                "row_sha256": row_hash,
            })
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output/dosd-medical-v2-rebuild/medical-deleted-table-pool.csv")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows: list[dict[str, str]] = []
    for source in SOURCES:
        if Path(source["pdf"]).is_file():
            rows.extend(extract_source(source))
    write_csv(args.output, rows)
    print({"rows": len(rows), "by_source": dict(Counter(row["source_id"] for row in rows)), "by_type": dict(Counter(row["semantic_type"] for row in rows)), "output": str(args.output)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
