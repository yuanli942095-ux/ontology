from __future__ import annotations

import re
import urllib.request

URL = "https://www.nice.org.uk/guidance/ng28/evidence"


def main() -> None:
    html = urllib.request.urlopen(
        urllib.request.Request(URL, headers={"User-Agent": "ontology-evolution/1.0"}),
        timeout=60,
    ).read().decode("utf-8", errors="replace")
    for link in re.findall(r'href="(/guidance/ng28/evidence/[^"]+)"', html):
        low = link.lower()
        if any(
            p in low
            for p in (
                "deleted",
                "changed",
                "amended",
                "recommendations-from",
                "appendix",
                "summary",
                "supplement",
            )
        ):
            print(link.split("/")[-1][:120])


if __name__ == "__main__":
    main()
