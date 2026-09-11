from __future__ import annotations

import re
import urllib.request

GUIDANCE = [f"ng{n}" for n in range(1, 261)] + [f"cg{n}" for n in range(1, 201)]
PATTERNS = (
    "summary-of-deleted-and-amended",
    "supplement-",
    "deleted-or-changed-without-an-evidence-review",
    "amended-recommendation-wording",
    "recommendations-from-",
)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "ontology-evolution/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="replace")


def main() -> None:
    hits: list[tuple[str, str]] = []
    for gid in GUIDANCE:
        try:
            html = fetch(f"https://www.nice.org.uk/guidance/{gid}/evidence")
        except Exception:
            continue
        for link in re.findall(r'href="(/guidance/[^"]+evidence/[^"]+)"', html):
            lower = link.lower()
            if not any(p in lower for p in PATTERNS):
                continue
            hits.append((gid, "https://www.nice.org.uk" + link))
    for gid, url in hits:
        print(gid, url.split("/")[-1][:100])
    print("TOTAL", len(hits))


if __name__ == "__main__":
    main()
