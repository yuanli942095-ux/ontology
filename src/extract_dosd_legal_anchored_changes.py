"""Extract candidate legal changes from the same provision identifier in two official versions.

This is a pre-annotation quality gate.  It deliberately emits a review pool,
not an Oracle-labelled benchmark: a provision is retained only when the UK
+legislation XML identifies the same P2 clause in both versions and its text
changed.  The old v1 nearest-neighbour paragraph matching is not used here.
"""

from __future__ import annotations

import argparse
import difflib
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from dosd_multidomain_common import load_source_pairs, write_csv


ROOT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(r"[a-z][a-z0-9-]{2,}")
EXCEPTION = re.compile(r"\b(?:except|unless|however|provided that|subject to|does not apply)\b", re.I)
SCOPE = re.compile(r"\b(?:person|organisation|undertaking|provider|aged|scope|appl(?:y|ies|icable)|only|including|excluding)\b", re.I)
TEMPORAL = re.compile(r"\b(?:effective|from|until|commenc|date|year|month|day)\b", re.I)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def compact(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def provisions(path: Path) -> dict[str, str]:
    output: dict[str, str] = {}
    for element in ET.parse(path).getroot().iter():
        identifier = element.attrib.get("id", "")
        if not identifier or local_name(element.tag) != "P2":
            continue
        text = compact(" ".join(element.itertext()))
        if 100 <= len(text) <= 1400:
            output[identifier] = text
    return output


def classify(old: str, new: str) -> str:
    text = f"{old} {new}"
    scores = {
        "GENERAL_RULE_EXCEPTION": 3 * len(EXCEPTION.findall(text)),
        "CROSS_SENTENCE_SCOPE": 2 * len(SCOPE.findall(text)),
        "TEMPORAL_VERSION": len(TEMPORAL.findall(text)),
    }
    return max(scores, key=lambda key: (scores[key], key))


def raw_path(cache: Path, pair_id: str, role: str) -> Path:
    choices = sorted(
        path for path in cache.glob(f"{pair_id}-{role}.*") if path.suffix.lower() in {".xml", ".html"}
    )
    if not choices:
        raise FileNotFoundError(f"no cached XML for {pair_id}/{role}")
    return choices[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-pairs", type=Path, default=ROOT / "benchmark" / "dosd-source-pairs" / "legal-source-pairs.csv")
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "dosd-legal-v2-rebuild" / "anchored-change-pool.csv")
    parser.add_argument("--min-ratio", type=float, default=0.45)
    parser.add_argument("--max-ratio", type=float, default=0.98)
    args = parser.parse_args()

    cache = ROOT / "data" / "dosd-source-cache" / "legal"
    rows: list[dict[str, str]] = []
    for pair in load_source_pairs(args.source_pairs):
        old_path = raw_path(cache, pair.pair_id, "old")
        new_path = raw_path(cache, pair.pair_id, "new")
        old_items = provisions(old_path)
        new_items = provisions(new_path)
        for provision_id in sorted(old_items.keys() & new_items.keys()):
            old, new = old_items[provision_id], new_items[provision_id]
            if old == new:
                continue
            ratio = difflib.SequenceMatcher(None, old.lower(), new.lower(), autojunk=False).ratio()
            if not args.min_ratio <= ratio <= args.max_ratio:
                continue
            old_tokens, new_tokens = set(TOKEN.findall(old.lower())), set(TOKEN.findall(new.lower()))
            overlap = len(old_tokens & new_tokens) / max(1, len(old_tokens | new_tokens))
            if overlap < 0.20:
                continue
            rows.append(
                {
                    "source_pair": pair.pair_id,
                    "source_family": pair.source_family,
                    "provision_id": provision_id,
                    "old_version": pair.old_version,
                    "new_version": pair.new_version,
                    "old_span": old,
                    "new_span": new,
                    "sequence_ratio": f"{ratio:.6f}",
                    "token_jaccard": f"{overlap:.6f}",
                    "provisional_semantic_type": classify(old, new),
                    "quality_gate": "SAME_OFFICIAL_PROVISION_ID",
                    "annotation_status": "PENDING_DUAL_REVIEW",
                }
            )
    write_csv(args.output, rows)
    print({"rows": len(rows), "types": dict(Counter(row["provisional_semantic_type"] for row in rows)), "output": str(args.output)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
