from __future__ import annotations

import re
import urllib.request

URLS = [
    "https://www.nice.org.uk/guidance/ng246/evidence",
    "https://www.nice.org.uk/guidance/ng17/evidence",
    "https://www.nice.org.uk/guidance/ng28/evidence",
    "https://www.nice.org.uk/guidance/ng69/evidence",
    "https://www.nice.org.uk/guidance/ng192/evidence",
    "https://www.nice.org.uk/guidance/ng128/evidence",
    "https://www.nice.org.uk/guidance/cg128/evidence",
]


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "ontology-evolution/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def main() -> None:
    keys = ("deleted", "changed", "amended", "recommendations-from", "appendices-an", "appendix-q")
    for url in URLS:
        print("===", url)
        html = fetch(url)
        links = sorted(set(re.findall(r'href="(/guidance/[^"]+evidence/[^"]+)"', html)))
        for link in links:
            lower = link.lower()
            if any(k in lower for k in keys):
                print(" ", link)


if __name__ == "__main__":
    main()
