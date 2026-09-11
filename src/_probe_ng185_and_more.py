from __future__ import annotations

import re
import urllib.request

GUIDES = ["ng185", "ng209", "ng106", "ng145", "ng136", "ng97", "ng95", "ng87", "ng80"]
PATHS = ["evidence", "resources", "chapter/Update-information"]
KEYS = ("appendix", "deleted", "changed", "amended", "changes", "recommendations-from")


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "ontology-evolution/1.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode("utf-8", errors="replace")


def main() -> None:
    for gid in GUIDES:
        for path in PATHS:
            url = f"https://www.nice.org.uk/guidance/{gid}/{path}"
            try:
                html = fetch(url)
            except Exception as exc:
                print(f"ERR {gid} {path}: {exc}")
                continue
            links = sorted(set(re.findall(rf'href="(/guidance/{gid}/[^"]+)"', html)))
            hits = [link for link in links if any(key in link.lower() for key in KEYS)]
            if hits:
                print(f"=== {gid} /{path}")
                for link in hits:
                    print(f"  https://www.nice.org.uk{link}")


if __name__ == "__main__":
    main()
