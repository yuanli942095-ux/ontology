from __future__ import annotations

import re
import urllib.request

GUIDANCE = [
    "ng59", "ng73", "ng97", "ng128", "ng136", "ng140", "ng158", "ng190", "ng192",
    "ng203", "ng204", "ng205", "ng206", "ng208", "ng210", "ng211", "ng212",
    "ng3", "ng17", "ng28", "ng69", "ng185", "ng246",
]
PATTERNS = (
    "summary-of-deleted-and-amended",
    "supplement-6",
    "supplement-5",
    "deleted-or-changed-without-an-evidence-review",
    "amended-recommendation-wording",
    "change-to-intent-without-an-evidence-review",
    "change-to-meaning",
)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "ontology-evolution/1.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode("utf-8", errors="replace")


def main() -> None:
    for gid in GUIDANCE:
        try:
            html = fetch(f"https://www.nice.org.uk/guidance/{gid}/evidence")
        except Exception as exc:
            print("ERR", gid, exc)
            continue
        for link in re.findall(r'href="(/guidance/[^"]+evidence/[^"]+)"', html):
            lower = link.lower()
            if not any(p in lower for p in PATTERNS):
                continue
            if "appendix-q-health" in lower or "appendices-an-pdf" in lower:
                continue
            print(gid, "https://www.nice.org.uk" + link)


if __name__ == "__main__":
    main()
