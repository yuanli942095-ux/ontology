from __future__ import annotations

import re
import urllib.request

GUIDANCE = [f"ng{n}" for n in range(1, 261)]


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "ontology-evolution/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="replace")


KNOWN = {
    "ng25", "ng222", "ng229", "ng18", "ng207", "cg184",
}


def main() -> None:
    for gid in GUIDANCE:
        if gid in KNOWN:
            continue
        try:
            html = fetch(f"https://www.nice.org.uk/guidance/{gid}/evidence")
        except Exception:
            continue
        for link in re.findall(r'href="(/guidance/[^"]+evidence/[^"]+)"', html):
            slug = link.lower()
            if "summary-of-deleted-and-amended" in slug or (
                "supplement" in slug and "amended" in slug and "deleted" in slug
            ):
                print(gid, link.split("/")[-1][:100])


if __name__ == "__main__":
    main()
