from __future__ import annotations

import re
import urllib.request

URLS = [
    "https://www.nice.org.uk/guidance/ng185/evidence",
    "https://www.nice.org.uk/guidance/ng17/evidence",
    "https://www.nice.org.uk/guidance/ng3/evidence",
    "https://www.nice.org.uk/guidance/ng23/evidence",
    "https://www.nice.org.uk/guidance/ng158/evidence",
    "https://www.nice.org.uk/guidance/ng192/evidence",
    "https://www.nice.org.uk/guidance/ng69/evidence",
    "https://www.nice.org.uk/guidance/ng140/evidence",
    "https://www.nice.org.uk/guidance/ng164/evidence",
    "https://www.nice.org.uk/guidance/ng232/evidence",
    "https://www.nice.org.uk/guidance/ng73/evidence",
    "https://www.nice.org.uk/guidance/ng209/evidence",
    "https://www.nice.org.uk/guidance/ng250/evidence",
    "https://www.nice.org.uk/guidance/cg190/evidence",
    "https://www.nice.org.uk/guidance/ng136/evidence",
    "https://www.nice.org.uk/guidance/ng28/evidence",
    "https://www.nice.org.uk/guidance/ng246/evidence",
    "https://www.nice.org.uk/guidance/ng18/evidence",
    "https://www.nice.org.uk/guidance/ng106/evidence",
    "https://www.nice.org.uk/guidance/ng145/evidence",
]
KEYS = ("deleted", "changed", "amended", "recommendations-from", "appendices-an", "appendix-q", "change-to-meaning")


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "ontology-evolution/1.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode("utf-8", errors="replace")


def main() -> None:
    for url in URLS:
        gid = url.split("/")[-2]
        html = fetch(url)
        links = sorted(set(re.findall(r'href="(/guidance/[^"]+evidence/[^"]+)"', html)))
        hits = [link for link in links if any(key in link.lower() for key in KEYS)]
        if hits:
            print("===", gid)
            for link in hits:
                print(" ", link)


if __name__ == "__main__":
    main()
