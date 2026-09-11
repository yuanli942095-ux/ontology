from __future__ import annotations

import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

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


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "ontology-evolution/1.0"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read().decode("utf-8", errors="replace")


def esearch(term: str, retmax: int = 500) -> list[str]:
    params = urllib.parse.urlencode({"db": "books", "term": term, "retmax": retmax, "retmode": "xml"})
    root = ET.fromstring(fetch(f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?{params}"))
    return [n.text for n in root.findall(".//Id") if n.text]


def resolve_nbk(book_id: str) -> tuple[str, str]:
    root = ET.fromstring(
        fetch(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?"
            + urllib.parse.urlencode({"db": "books", "id": book_id, "retmode": "xml"})
        )
    )
    nbk = ""
    title = book_id
    for item in root.findall(".//Item"):
        if item.attrib.get("Name") == "RID":
            nbk = (item.text or "").split("/")[0]
        if item.attrib.get("Name") == "Title":
            title = (item.text or "").strip()
    return nbk, title


def find_table(nbk: str) -> tuple[str, int] | None:
    page = fetch(f"https://www.ncbi.nlm.nih.gov/books/{nbk}/")
    table_ids = sorted(
        {m for m in re.findall(rf"/books/{nbk}/table/([^\"'?]+)", page) if m.lower().startswith("cha.tab")}
    )
    for table_id in table_ids:
        time.sleep(0.35)
        table_html = fetch(f"https://www.ncbi.nlm.nih.gov/books/{nbk}/table/{table_id}/?report=objectonly")
        lower = table_html.lower()
        if "amended recommendation wording" not in lower:
            continue
        if sum(1 for marker in MARKERS if marker in lower) < 2:
            continue
        rows = max(len(re.findall(r"<tr\b", table_html, flags=re.I)) - 1, 0)
        return table_id, rows
    return None


def main() -> None:
    terms = [
        'NICE[Publisher] AND "Amended recommendation wording"',
        'NICE[Publisher] AND "Reason for change"',
        'niceng[Filter] AND "Amended recommendation wording"',
    ]
    book_ids: list[str] = []
    for term in terms:
        book_ids.extend(esearch(term))
        time.sleep(0.5)
    seen_nbk: set[str] = set()
    hits: list[tuple[str, str, str, int]] = []
    for book_id in sorted(set(book_ids)):
        time.sleep(0.25)
        try:
            nbk, title = resolve_nbk(book_id)
        except Exception as exc:
            print("ERR resolve", book_id, exc)
            continue
        if not nbk.startswith("NBK") or nbk in KNOWN or nbk in seen_nbk:
            continue
        seen_nbk.add(nbk)
        try:
            found = find_table(nbk)
        except Exception as exc:
            print("ERR table", nbk, exc)
            continue
        if found:
            table_id, rows = found
            hits.append((nbk, table_id, title, rows))
            print("HIT", nbk, table_id, rows, title[:70])
    print("TOTAL_NEW", len(hits))


if __name__ == "__main__":
    main()
