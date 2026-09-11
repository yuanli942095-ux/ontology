from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from extract_dosd_medical_amendment_table_events import (
    extract_dual_column_appendix_section,
    extract_triple_column_prose_appendix_section,
)

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "data/dosd-source-cache/medical-v2/MEDCMP_018-cg147-appendices.txt").read_text(
    encoding="utf-8", errors="replace"
).splitlines()

for marker in (
    "Appendix A:",
    "Appendix A ",
    "Table 2: Amended",
    "Amended recommendation wording",
):
    dual = extract_dual_column_appendix_section(lines, marker, "MEDCMP_018")
    triple = extract_triple_column_prose_appendix_section(
        lines, marker, "Appendix B", "MEDCMP_018"
    )
    print(marker[:35], "dual", len(dual), "triple", len(triple))
