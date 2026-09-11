from __future__ import annotations

import re
import urllib.request

BOOKS = [
    "NBK553007", "NBK598564", "NBK585280", "NBK547161", "NBK542416",
    "NBK552160", "NBK588750", "NBK553264", "NBK611968", "NBK519155",
]


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "ontology-evolution/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="replace")


def main() -> None:
    for nbk in BOOKS:
        try:
            page = fetch(f"https://www.ncbi.nlm.nih.gov/books/{nbk}/")
        except Exception as exc:
            print(nbk, "ERR", exc)
            continue
        low = page.lower()
        if "amended recommendation wording" not in low:
            continue
        tabs = sorted({m for m in re.findall(rf"/books/{nbk}/table/(cha\.tab[^\"'?]+)", page, flags=re.I)})
        print(nbk, "section=yes", "tabs", tabs)
        for tid in tabs:
            th = fetch(f"https://www.ncbi.nlm.nih.gov/books/{nbk}/table/{tid}/?report=objectonly")
            if "reason for change" in th.lower():
                rows = max(len(re.findall(r"<tr\b", th, flags=re.I)) - 1, 0)
                print(" ", tid, "rows", rows)


if __name__ == "__main__":
    main()
