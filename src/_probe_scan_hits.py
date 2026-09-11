from __future__ import annotations

import re
import urllib.request

GUIDES = ["ng207", "ng209", "cg49", "cg147", "ng185", "ng28"]


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "ontology-evolution/1.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode("utf-8", errors="replace")


def main() -> None:
    keys = ("deleted", "changed", "amended", "recommendations-from", "appendices-an", "summary-of-deleted")
    for gid in GUIDES:
        url = f"https://www.nice.org.uk/guidance/{gid}/evidence"
        html = fetch(url)
        links = sorted(set(re.findall(r'href="(/guidance/[^"]+evidence/[^"]+)"', html)))
        hits = [link for link in links if any(key in link.lower() for key in keys)]
        if hits:
            print(f"=== {gid}")
            for link in hits:
                print(f"  https://www.nice.org.uk{link}")


if __name__ == "__main__":
    main()
