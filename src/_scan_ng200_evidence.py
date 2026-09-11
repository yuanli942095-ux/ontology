from __future__ import annotations

import re
import urllib.request

GUIDANCE = [f"ng{n}" for n in range(200, 215)]


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
            low = link.lower()
            if any(
                p in low
                for p in (
                    "summary-of-deleted-and-amended",
                    "deleted-or-changed-without-an-evidence-review",
                    "amended-recommendation-wording",
                    "supplement-6-summary",
                )
            ):
                print(gid, link.split("/")[-1])


if __name__ == "__main__":
    main()
