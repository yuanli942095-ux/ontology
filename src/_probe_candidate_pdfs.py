from __future__ import annotations

import re
import urllib.request

CANDIDATES = [
    ("MEDCMP_013", "FINAL_UPDATE", "https://www.nice.org.uk/guidance/ng185/evidence/appendix-a-changes-to-recommendations-on-testing-for-diabetes-across-nice-guidelines-pdf-9253108190"),
    ("MEDCMP_015", "FINAL_UPDATE", "https://www.nice.org.uk/guidance/ng28/evidence/appendix-g-nice-guideline-cg66-deleted-text-pdf-78671532602"),
    ("MEDCMP_014", "FINAL_UPDATE", "https://www.nice.org.uk/guidance/ng28/evidence/appendix-h-nice-guideline-cg87-deleted-text-pdf-78671532603"),
    ("MEDCMP_NEW_209", "CHECK", "https://www.nice.org.uk/guidance/ng209/evidence/summary-of-deleted-and-amended-recommendations-pdf-10894269277"),
    ("MEDCMP_007", "REJECTED_DRAFT", "https://www.nice.org.uk/guidance/ng246/evidence/cg189-appendix-q-recommendations-from-nice-guideline-cg43-deleted-or-changed-pdf-6960327451"),
]


def fetch_head(url: str, nbytes: int = 8000) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ontology-evolution/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read(nbytes)


def main() -> None:
    for sid, state, url in CANDIDATES:
        data = fetch_head(url)
        text = data.decode("latin-1", errors="replace")
        draft = "DRAFT" in text.upper() or "CONSULTATION" in text.upper()
        cols = []
        for label in (
            "Recommendation in 2009 guideline",
            "Recommendation in current guideline",
            "Reason for change",
            "Original recommendation",
            "Change to recommendation",
            "Comment",
        ):
            if label.lower() in text.lower():
                cols.append(label)
        print(f"=== {sid} draft={draft} state={state}")
        print(f"URL {url}")
        print(f"COLS {cols}")
        print()


if __name__ == "__main__":
    main()
