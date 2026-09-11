from __future__ import annotations

import re
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(r"g:\LearnAI\ontology-evolution")
CACHE = ROOT / "data/dosd-source-cache/medical-v2"

GUIDANCE = [
    "ng17", "ng19", "ng28", "ng59", "ng69", "ng73", "ng97", "ng128", "ng136",
    "ng140", "ng158", "ng192", "ng246", "ng3", "ng23", "ng185", "cg128", "cg190",
    "cg87", "cg66", "cg43", "cg15", "ng18",
]
PATTERNS = (
    "deleted-or-changed",
    "amended-recommendation",
    "recommendations-from",
    "appendices-an",
    "appendix-q",
    "change-to-meaning",
    "change-to-intent",
)
KNOWN = {
    "appendix-2-amended-recommendation-wording-change-to-intent",
    "appendices-1-and-2-recommendations-that-have-been-deleted-or-changed",
    "appendix-1-recommendations-that-have-been-deleted-or-changed-without",
    "appendix-j-recommendations-from-cg17",
    "appendices-an-pdf-435396353",
}


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "ontology-evolution/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def main() -> None:
    hits: list[tuple[str, str]] = []
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
            if any(k in lower for k in KNOWN):
                continue
            full = "https://www.nice.org.uk" + link
            hits.append((gid, full))
            print("HIT", gid, link.split("/")[-1][:90])
    print("TOTAL", len(hits))
    for gid, url in hits[:5]:
        slug = url.split("/")[-1]
        pdf = CACHE / f"PROBE_{gid}_{slug[:40]}.pdf"
        txt = pdf.with_suffix(".txt")
        if pdf.is_file():
            continue
        try:
            pdf.write_bytes(fetch(url).encode("latin1") if False else urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "ontology-evolution/1.0"}), timeout=60
            ).read())
            subprocess.run(["pdftotext", "-layout", str(pdf), str(txt)], check=True)
            text = txt.read_text(encoding="utf-8", errors="replace")
            draft = "draft for consultation" in text.lower() or "draft for consult" in text.lower()
            reason = text.count("Reason for change")
            rec2004 = text.count("Recommendation in 2004")
            rec_curr = text.count("Recommendation in current")
            print("PROBE", gid, "draft", draft, "reason", reason, "oldhdr", rec2004, "newhdr", rec_curr, "chars", len(text))
        except Exception as exc:
            print("PROBE_ERR", gid, exc)


if __name__ == "__main__":
    main()
