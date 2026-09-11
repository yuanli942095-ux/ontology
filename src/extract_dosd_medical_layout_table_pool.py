from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from dosd_multidomain_common import sha256_text, write_csv
from extract_dosd_medical_amendment_table_events import (
    extract_dual_column_appendix_section,
    extract_replacement_deletion_table_section,
    extract_triple_column_prose_appendix_section,
)


ROOT = Path(__file__).resolve().parents[1]
LAYOUT_SOURCES = [
    {
        "source_id": "MEDCMP_005",
        "source_title": "NICE NG222 Depression in adults appendices 1 and 2",
        "source_url": "https://www.nice.org.uk/guidance/ng222/evidence/appendices-1-and-2-recommendations-that-have-been-deleted-or-changed-without-an-evidence-review-pdf-11130965533",
        "old_version": "2009 guideline",
        "new_version": "current guideline amended 2022",
        "layout_text": ROOT / "data/dosd-source-cache/medical-v2/MEDCMP_005-ng222-appendices.txt",
        "appendix_marker": "Appendix 2. Amended recommendation wording",
    },
    {
        "source_id": "MEDCMP_006",
        "source_title": "NICE NG229 Fetal monitoring in labour appendix 1",
        "source_url": "https://www.nice.org.uk/guidance/ng229/evidence/appendix-1-recommendations-that-have-been-deleted-or-changed-without-an-evidence-review-pdf-11314226413",
        "old_version": "2014 guideline",
        "new_version": "current guideline amended 2022",
        "layout_text": ROOT / "data/dosd-source-cache/medical-v2/MEDCMP_006-ng229-appendix1.txt",
        "appendix_marker": "Table 2 Amended recommendation wording without an evidence review",
        "replacement_table_marker": "Table 1 Recommendations that have been deleted",
        "replacement_table_end_marker": "Table 2 Amended recommendation wording",
    },
    {
        "source_id": "MEDCMP_009",
        "source_title": "NICE CG184 Dyspepsia appendix J recommendations from CG17",
        "source_url": "https://www.nice.org.uk/guidance/cg184/evidence/appendix-j-recommendations-from-cg17-pdf-6955335254",
        "old_version": "2004 guideline",
        "new_version": "2014 guideline",
        "layout_text": ROOT / "data/dosd-source-cache/medical-v2/MEDCMP_009-cg184-appendixj.txt",
        "appendix_marker": "Appendix J: Recommendations from NICE",
    },
    {
        "source_id": "MEDCMP_015",
        "source_title": "NICE NG207 Inducing labour supplement 6 deleted and amended recommendations",
        "source_url": "https://www.nice.org.uk/guidance/ng207/evidence/supplement-6-summary-of-deleted-and-amended-recommendations-pdf-10884146226",
        "old_version": "2008 guideline",
        "new_version": "current guideline amended 2021",
        "layout_text": ROOT / "data/dosd-source-cache/medical-v2/MEDCMP_015-ng207-supplement6.txt",
        "appendix_marker": "Table 2: Amended recommendation wording",
        "replacement_table_marker": "Table 1: Recommendations that have been deleted",
        "replacement_table_end_marker": "Table 2:",
    },
    {
        "source_id": "MEDCMP_012",
        "source_title": "NICE NG18 Diabetes children and young people appendix A",
        "source_url": "https://www.nice.org.uk/guidance/ng18/evidence/appendices-an-pdf-435396353",
        "old_version": "2004 guideline",
        "new_version": "current guideline amended 2015",
        "layout_text": ROOT / "data/dosd-source-cache/medical-v2/MEDCMP_012-ng18-appendices-an.txt",
        "appendix_marker": "Appendix A: Recommendations from",
        "end_marker": "Appendix B",
        "parser": "triple_column_prose",
    },
]


def extract_source(source: dict[str, object]) -> list[dict[str, str]]:
    lines = Path(source["layout_text"]).read_text(encoding="utf-8", errors="replace").splitlines()
    parser = source.get("parser", "dual_column")
    if parser == "triple_column_prose":
        extracted = extract_triple_column_prose_appendix_section(
            lines,
            str(source["appendix_marker"]),
            str(source["end_marker"]),
            str(source["source_id"]),
        )
    else:
        extracted = extract_dual_column_appendix_section(
            lines,
            str(source["appendix_marker"]),
            str(source["source_id"]),
        )
        if source.get("replacement_table_marker"):
            extracted.extend(
                extract_replacement_deletion_table_section(
                    lines,
                    str(source["replacement_table_marker"]),
                    str(source["replacement_table_end_marker"]),
                    str(source["source_id"]),
                )
            )
    rows: list[dict[str, str]] = []
    for row in extracted:
        rows.append({
            "source_id": str(source["source_id"]),
            "source_title": str(source["source_title"]),
            "source_url": str(source["source_url"]),
            "old_version": str(source["old_version"]),
            "new_version": str(source["new_version"]),
            "old_recommendation_id": row["old_recommendation_id"],
            "new_recommendation_id": row["new_recommendation_id"],
            "old_span": row["old_span"],
            "new_span": row["new_span"],
            "reason_for_change": row["reason_for_change"],
            "semantic_type": row["semantic_type"],
            "source_quality_gate": row["source_quality_gate"],
            "row_sha256": sha256_text(
                f"{source['source_id']}|{row['old_span']}|{row['new_span']}|{row['reason_for_change']}"
            ),
        })
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "output/dosd-medical-v2-rebuild/medical-layout-table-pool.csv",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows: list[dict[str, str]] = []
    for source in LAYOUT_SOURCES:
        if Path(source["layout_text"]).is_file():
            rows.extend(extract_source(source))
    write_csv(args.output, rows)
    print(
        {
            "rows": len(rows),
            "by_source": dict(Counter(row["source_id"] for row in rows)),
            "by_type": dict(Counter(row["semantic_type"] for row in rows)),
            "output": str(args.output),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
