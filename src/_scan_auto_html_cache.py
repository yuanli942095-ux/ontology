from __future__ import annotations

import re
from pathlib import Path

KNOWN = {
    "NBK555203", "NBK554746", "NBK554181", "NBK552672", "NBK552606",
    "NBK542453", "NBK561646", "NBK367648", "NBK553260",
}
MARKERS = (
    "recommendation in previous guideline",
    "recommendation in current guideline",
    "reason for change",
    "recommendation in 2004 guideline",
    "recommendation in 2009 guideline",
    "recommendation in 2010 guideline",
    "recommendation in 2013 guideline",
    "recommendation in 2015 guideline",
    "recommendation in 2008 guideline",
    "recommendation in 2003 guideline",
)
ROOT = Path(r"g:\LearnAI\ontology-evolution\data\dosd-source-cache\medical-v2")


def score_table(html: str) -> dict[str, object]:
    lower = html.lower()
    return {
        "header_hits": sum(1 for m in MARKERS if m in lower),
        "has_section": "amended recommendation wording" in lower,
        "rows": max(len(re.findall(r"<tr\b", html, flags=re.I)) - 1, 0),
    }


def main() -> None:
    hits: list[tuple[str, str, str, dict[str, object]]] = []
    for page in sorted(ROOT.glob("MEDHTML_AUTO_*-page.html")):
        page_text = page.read_text(encoding="utf-8", errors="replace")
        nbk = re.search(r'ncbi_acc" content="(NBK\d+)"', page_text)
        nbk_id = nbk.group(1) if nbk else "?"
        title = re.search(r'citation_title" content="([^"]+)"', page_text)
        title = title.group(1)[:80] if title else page.stem
        table = page.with_name(page.name.replace("-page.html", "-table.html"))
        if not table.is_file():
            continue
        score = score_table(table.read_text(encoding="utf-8", errors="replace"))
        if score["has_section"] and score["header_hits"] >= 2 and score["rows"] >= 2:
            status = "KNOWN" if nbk_id in KNOWN else "NEW"
            hits.append((status, nbk_id, title, score))
            print(status, nbk_id, score["rows"], title, table.name)
    print("TOTAL_CANDIDATES", len(hits))
    print("NEW_CANDIDATES", sum(1 for h in hits if h[0] == "NEW"))


if __name__ == "__main__":
    main()
