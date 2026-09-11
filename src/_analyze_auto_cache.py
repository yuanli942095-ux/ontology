from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data/dosd-source-cache/medical-v2"
KNOWN = {
    "NBK555203", "NBK554746", "NBK554181", "NBK552672", "NBK552606",
    "NBK542453", "NBK561646", "NBK367648", "NBK553260",
}

for page in sorted(CACHE.glob("MEDHTML_AUTO_*-page.html")):
    html = page.read_text(encoding="utf-8", errors="replace")
    nbk = re.search(r'ncbi_acc" content="(NBK\d+)"', html)
    nbk_id = nbk.group(1) if nbk else "?"
    title = re.search(r'ncbi_pagename" content="([^"]+)"', html)
    title_s = title.group(1).replace(" - NCBI Bookshelf", "") if title else ""
    tbl = CACHE / page.name.replace("-page.html", "-table.html")
    if tbl.exists():
        th = tbl.read_text(encoding="utf-8", errors="replace")
        rows = max(len(re.findall(r"<tr\b", th, re.I)) - 1, 0)
        hdr = sum(1 for x in ("Recommendation in", "Reason for change") if x in th)
        print(f"{page.stem}: {nbk_id} rows~{rows} hdr~{hdr} known={nbk_id in KNOWN} | {title_s[:70]}")
    else:
        print(f"{page.stem}: {nbk_id} NO_TABLE known={nbk_id in KNOWN} | {title_s[:70]}")
