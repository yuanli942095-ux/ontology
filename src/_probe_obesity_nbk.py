from __future__ import annotations

import re
import urllib.request

NBK = "NBK588750"


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "ontology-evolution/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="replace")


def main() -> None:
    page = fetch(f"https://www.ncbi.nlm.nih.gov/books/{NBK}/")
    tabs = sorted({m for m in re.findall(rf"/books/{NBK}/table/(cha\.tab[^\"'?]+)", page, flags=re.I)})
    print("tabs", tabs)
    for tid in tabs:
        th = fetch(f"https://www.ncbi.nlm.nih.gov/books/{NBK}/table/{tid}/?report=objectonly")
        low = th.lower()
        if "amended recommendation" in low or "reason for change" in low:
            rows = max(len(re.findall(r"<tr\b", th, flags=re.I)) - 1, 0)
            print("HIT", tid, "rows", rows)


if __name__ == "__main__":
    main()
