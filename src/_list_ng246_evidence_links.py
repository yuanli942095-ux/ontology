from __future__ import annotations

import re
import urllib.request

URL = "https://www.nice.org.uk/guidance/ng246/evidence"


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ontology-evolution/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def main() -> None:
    html = fetch(URL).decode("utf-8", errors="replace")
    for link in sorted(set(re.findall(r'href="(/guidance/ng246/evidence/[^"]+)"', html))):
        lower = link.lower()
        if any(k in lower for k in ("appendix", "deleted", "changed", "amended", "recommendations-from", "cg43", "cg189")):
            print(link)


if __name__ == "__main__":
    main()
