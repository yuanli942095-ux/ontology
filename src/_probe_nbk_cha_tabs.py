from __future__ import annotations

import re
import urllib.request

BOOKS = [
    "NBK555203", "NBK552606", "NBK553260", "NBK553486", "NBK553316", "NBK555102",
    "NBK552570", "NBK542416", "NBK552590", "NBK547161", "NBK588750", "NBK553608",
    "NBK598564", "NBK585280", "NBK542453", "NBK561646", "NBK367648", "NBK554746",
    "NBK554181", "NBK552672",
]
MARKERS = ("amended recommendation wording", "reason for change")


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "ontology-evolution/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def main() -> None:
    for nbk in BOOKS:
        try:
            page = fetch(f"https://www.ncbi.nlm.nih.gov/books/{nbk}/")
        except Exception as exc:
            print(nbk, "ERR", exc)
            continue
        tabs = sorted({m for m in re.findall(rf"/books/{nbk}/table/(cha\.tab[^\"'?]+)", page, flags=re.I)})
        hits = []
        for tid in tabs:
            try:
                th = fetch(f"https://www.ncbi.nlm.nih.gov/books/{nbk}/table/{tid}/?report=objectonly")
            except Exception:
                continue
            low = th.lower()
            if all(m in low for m in MARKERS):
                rows = max(len(re.findall(r"<tr\b", th, flags=re.I)) - 1, 0)
                hits.append((tid, rows))
        if hits:
            print(nbk, hits)


if __name__ == "__main__":
    main()
