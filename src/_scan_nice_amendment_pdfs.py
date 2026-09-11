from __future__ import annotations

import re
import time
import urllib.request

GUIDANCE = [f"ng{n}" for n in range(1, 261)] + [f"cg{n}" for n in range(1, 201)]
PATTERNS = (
    "deleted-or-changed",
    "amended-recommendation",
    "recommendations-from-",
    "appendices-an-pdf",
    "appendix-q-recommendations",
    "change-to-meaning",
    "change-to-intent",
)
KNOWN_SLUGS = {
    "appendix-2-amended-recommendation-wording-change-to-intent-without-an-evidence-review-june",
    "appendices-1-and-2-recommendations-that-have-been-deleted-or-changed-without-an-evidence-review",
    "appendix-1-recommendations-that-have-been-deleted-or-changed-without-an-evidence-review",
    "appendix-j-recommendations-from-cg17-pdf",
    "appendices-an-pdf-435396353",
}


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "ontology-evolution/1.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode("utf-8", errors="replace")


def main() -> None:
    hits: list[str] = []
    for gid in GUIDANCE:
        time.sleep(0.15)
        try:
            html = fetch(f"https://www.nice.org.uk/guidance/{gid}/evidence")
        except Exception:
            continue
        for link in re.findall(r'href="(/guidance/[^"]+evidence/[^"]+)"', html):
            lower = link.lower()
            if not any(p in lower for p in PATTERNS):
                continue
            if any(k in lower for k in KNOWN_SLUGS):
                continue
            full = "https://www.nice.org.uk" + link
            hits.append(full)
            print("HIT", gid, link.split("/")[-1][:100])
    print("TOTAL", len(hits))


if __name__ == "__main__":
    main()
