from __future__ import annotations

import argparse
import csv
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from dosd_multidomain_common import write_csv


ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data/dosd-source-cache/medical-v2"
DEFAULT_REGISTRY = ROOT / "output/dosd-medical-v2-rebuild/ncbi-amendment-discovery.csv"

KNOWN_NBK = {
    "NBK555203",
    "NBK554746",
    "NBK554181",
    "NBK552672",
    "NBK552606",
    "NBK542453",
    "NBK561646",
    "NBK367648",
    "NBK553260",
}

HEADER_MARKERS = (
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


def fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "ontology-evolution-medical-discovery/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def esearch_nice_books(term: str, retmax: int = 200) -> list[str]:
    params = urllib.parse.urlencode(
        {
            "db": "books",
            "term": term,
            "retmax": retmax,
            "retmode": "xml",
        }
    )
    xml = fetch(f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?{params}")
    root = ET.fromstring(xml)
    return [node.text for node in root.findall(".//Id") if node.text]


def book_title(nbk: str) -> str:
    xml = fetch(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?"
        + urllib.parse.urlencode({"db": "books", "id": nbk, "retmode": "xml"})
    )
    root = ET.fromstring(xml)
    for item in root.findall(".//Item"):
        if item.attrib.get("Name") == "Title":
            return (item.text or nbk).strip()
    return nbk


def resolve_nbk(book_id: str) -> str:
    xml = fetch(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?"
        + urllib.parse.urlencode({"db": "books", "id": book_id, "retmode": "xml"})
    )
    root = ET.fromstring(xml)
    for item in root.findall(".//Item"):
        if item.attrib.get("Name") == "RID":
            value = (item.text or "").strip()
            if value.startswith("NBK"):
                return value.split("/")[0]
    return ""


def find_amendment_table_url(page_html: str, nbk: str) -> str | None:
    pattern = rf"/books/{nbk}/table/([^\"'?]+)"
    for match in re.finditer(pattern, page_html, flags=re.I):
        table_id = match.group(1)
        if not table_id.lower().startswith("cha.tab"):
            continue
        table_url = f"https://www.ncbi.nlm.nih.gov/books/{nbk}/table/{table_id}/?report=objectonly"
        table_html = fetch(table_url)
        lower = table_html.lower()
        if "amended recommendation wording" in lower and sum(
            1 for marker in HEADER_MARKERS if marker in lower
        ) >= 2:
            return table_url
    return None


def score_table_html(table_html: str) -> dict[str, object]:
    lower = table_html.lower()
    header_hits = sum(1 for marker in HEADER_MARKERS if marker in lower)
    has_section = "amended recommendation wording" in lower
    row_count = len(re.findall(r"<tr\b", table_html, flags=re.I))
    text = re.sub(r"<[^>]+>", " ", table_html)
    text = re.sub(r"\s+", " ", text).strip()
    return {
        "has_amendment_section": has_section,
        "header_marker_hits": header_hits,
        "approx_row_count": max(row_count - 1, 0),
        "text_chars": len(text),
    }


def classify_candidate(score: dict[str, object], already_known: bool) -> str:
    if already_known:
        return "ALREADY_ADMITTED"
    if not score["has_amendment_section"]:
        return "NO_AMENDMENT_SECTION"
    if score["header_marker_hits"] < 2:
        return "HEADER_INCOMPLETE"
    if score["approx_row_count"] < 2:
        return "TABLE_TOO_SMALL"
    return "CANDIDATE_THREE_COLUMN_TABLE"


def discover(retmax: int = 200, sleep_s: float = 0.34) -> list[dict[str, str]]:
    search_terms = [
        "NICE[Publisher] AND \"Amended recommendation wording\"",
        "NICE[Publisher] AND \"Reason for change\" AND \"Recommendation in\"",
        "NICE[Publisher] AND \"change to meaning\"",
    ]
    nbk_ids: list[str] = []
    for term in search_terms:
        nbk_ids.extend(esearch_nice_books(term, retmax=retmax))
        time.sleep(sleep_s)
    unique_ids = sorted(set(nbk_ids))
    rows: list[dict[str, str]] = []
    for index, book_id in enumerate(unique_ids, start=1):
        nbk = resolve_nbk(book_id) or book_id
        if not nbk.startswith("NBK"):
            continue
        source_id = f"MEDHTML_AUTO_{index:03d}"
        page_url = f"https://www.ncbi.nlm.nih.gov/books/{nbk}/"
        try:
            page_html = fetch(page_url)
            title = book_title(nbk)
            table_url = find_amendment_table_url(page_html, nbk)
            if not table_url:
                rows.append(
                    {
                        "source_id": source_id,
                        "issuer": "NICE via NCBI Bookshelf",
                        "title": title,
                        "nbk_id": nbk,
                        "page_url": page_url,
                        "table_url": "",
                        "publication_state": "FINAL_NCBI_BOOKSHELF",
                        "discovery_status": "NO_TABLE_URL_FOUND",
                        "approx_row_count": "0",
                        "header_marker_hits": "0",
                        "notes": "Bookshelf page fetched but no cha.tab* amendment table link found",
                    }
                )
                time.sleep(sleep_s)
                continue
            table_html = fetch(table_url)
            score = score_table_html(table_html)
            status = classify_candidate(score, nbk in KNOWN_NBK)
            rows.append(
                {
                    "source_id": source_id,
                    "issuer": "NICE via NCBI Bookshelf",
                    "title": title,
                    "nbk_id": nbk,
                    "page_url": page_url,
                    "table_url": table_url,
                    "publication_state": "FINAL_NCBI_BOOKSHELF",
                    "discovery_status": status,
                    "approx_row_count": str(score["approx_row_count"]),
                    "header_marker_hits": str(score["header_marker_hits"]),
                    "notes": (
                        "Official NCBI HTML amendment table with old/new/reason headers"
                        if status == "CANDIDATE_THREE_COLUMN_TABLE"
                        else "Fetched for inspection only"
                    ),
                }
            )
            cache_page = CACHE_DIR / f"{source_id}-page.html"
            cache_table = CACHE_DIR / f"{source_id}-table.html"
            cache_page.write_text(page_html, encoding="utf-8")
            cache_table.write_text(table_html, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - discovery should continue on single-source failures
            rows.append(
                {
                    "source_id": source_id,
                    "issuer": "NICE via NCBI Bookshelf",
                    "title": nbk,
                    "nbk_id": nbk,
                    "page_url": page_url,
                    "table_url": "",
                    "publication_state": "FINAL_NCBI_BOOKSHELF",
                    "discovery_status": "FETCH_FAILED",
                    "approx_row_count": "0",
                    "header_marker_hits": "0",
                    "notes": str(exc)[:240],
                }
            )
        time.sleep(sleep_s)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover NICE/NCBI official amendment tables.")
    parser.add_argument("--output", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--retmax", type=int, default=200)
    args = parser.parse_args()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    rows = discover(retmax=args.retmax)
    write_csv(args.output, rows)
    candidates = [r for r in rows if r["discovery_status"] == "CANDIDATE_THREE_COLUMN_TABLE"]
    print(f"wrote {len(rows)} discovery rows to {args.output}")
    print(f"new candidate tables: {len([r for r in candidates if r['nbk_id'] not in KNOWN_NBK])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
