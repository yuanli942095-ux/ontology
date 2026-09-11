from __future__ import annotations

import re
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


def resolve(book_id: str) -> tuple[str, str]:
    params = urllib.parse.urlencode({"db": "books", "id": book_id, "retmode": "xml"})
    root = ET.fromstring(fetch(f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?{params}"))
    nbk = ""
    title = book_id
    for item in root.findall(".//Item"):
        if item.attrib.get("Name") == "RID":
            nbk = (item.text or "").split("/")[0]
        if item.attrib.get("Name") == "Title":
            title = (item.text or "").strip()
    return nbk, title


def find_tables(nbk: str) -> list[tuple[str, int, int]]:
    page = fetch(f"https://www.ncbi.nlm.nih.gov/books/{nbk}/")
    table_ids = sorted(
        {m for m in re.findall(rf"/books/{nbk}/table/([^\"'?]+)", page) if m.lower().startswith("cha.tab")}
    )
    results: list[tuple[str, int, int]] = []
    for table_id in table_ids:
        table_html = fetch(f"https://www.ncbi.nlm.nih.gov/books/{nbk}/table/{table_id}/?report=objectonly")
        lower = table_html.lower()
        if "amended recommendation wording" not in lower:
            continue
        marker_hits = sum(1 for marker in MARKERS if marker in lower)
        rows = max(len(re.findall(r"<tr\b", table_html, flags=re.I)) - 1, 0)
        results.append((table_id, marker_hits, rows))
    return results


def main() -> None:
    book_ids = esearch("niceng[Filter]")
    print("BOOKS", len(book_ids))
    seen: set[str] = set()
    for book_id in book_ids:
        nbk, title = resolve(book_id)
        if not nbk.startswith("NBK") or nbk in seen:
            continue
        seen.add(nbk)
        try:
            tables = find_tables(nbk)
        except Exception as exc:
            print(f"ERR {nbk} {exc}")
            continue
        if not tables:
            continue
        status = "KNOWN" if nbk in KNOWN else "NEW"
        for table_id, marker_hits, rows in tables:
            url = f"https://www.ncbi.nlm.nih.gov/books/{nbk}/table/{table_id}/?report=objectonly"
            print(f"{status} {nbk} {table_id} markers={marker_hits} rows={rows} | {title[:60]}")
            print(f"  {url}")
    print("CHECKED", len(seen))


if __name__ == "__main__":
    main()
