"""Create a review pool from matching numbered recommendations in official medical PDFs.

Only an identical recommendation identifier across an old and a new official
document can enter the pool.  The pool is deliberately pre-annotation: it is a
screening artifact, not a benchmark and contains no Oracle label.
"""

from __future__ import annotations

import argparse
import difflib
import re
from collections import Counter, defaultdict
from pathlib import Path

from dosd_multidomain_common import load_source_pairs, write_csv


ROOT = Path(__file__).resolve().parents[1]
REC_START = re.compile(r"(?m)^\s*(\d+\.\d+(?:\.\d+)?)\s+(.{10,})$")
NORMATIVE = re.compile(r"\b(?:shall|must|should|recommend|offer|consider|advise|ensure|refer|do not|may|required|prohibited|eligible|except|unless)\b", re.I)
EXCEPTION = re.compile(r"\b(?:except|unless|however|provided that|subject to|does not apply|contraindicat)\b", re.I)
SCOPE = re.compile(r"\b(?:adult|child|patient|person|people|aged|only|including|excluding|appl(?:y|ies|icable))\b", re.I)
TEMPORAL = re.compile(r"\b(?:effective|from|until|replace|supersed|amend|date|year|month|day)\b", re.I)


def compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def numbered_blocks(path: Path) -> dict[str, list[str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    matches = list(REC_START.finditer(text))
    blocks: dict[str, list[str]] = defaultdict(list)
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = compact(text[match.start() : end])
        if not 80 <= len(block) <= 1800:
            continue
        if ". . ." in block[:250] or re.search(r"\.{8,}", block[:250]):
            continue
        if not NORMATIVE.search(block):
            continue
        blocks[match.group(1)].append(block)
    return blocks


def classify(old: str, new: str) -> str:
    text = f"{old} {new}"
    scores = {
        "GENERAL_RULE_EXCEPTION": 3 * len(EXCEPTION.findall(text)),
        "CROSS_SENTENCE_SCOPE": 2 * len(SCOPE.findall(text)),
        "TEMPORAL_VERSION": len(TEMPORAL.findall(text)),
    }
    return max(scores, key=lambda name: (scores[name], name))


def best_changed_pair(old_blocks: list[str], new_blocks: list[str], minimum: float, maximum: float) -> tuple[str, str, float] | None:
    options: list[tuple[float, str, str]] = []
    for old in old_blocks:
        for new in new_blocks:
            if old == new:
                continue
            score = difflib.SequenceMatcher(None, old.lower(), new.lower(), autojunk=False).ratio()
            if minimum <= score <= maximum:
                options.append((score, old, new))
    if not options:
        return None
    score, old, new = max(options, key=lambda item: item[0])
    return old, new, score


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-pairs", type=Path, default=ROOT / "benchmark" / "dosd-source-pairs" / "medical-source-pairs-v2-expansion.csv")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "data" / "dosd-source-cache" / "medical-v2")
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "dosd-medical-v2-rebuild" / "numbered-recommendation-pool.csv")
    parser.add_argument("--min-ratio", type=float, default=0.45)
    parser.add_argument("--max-ratio", type=float, default=0.98)
    args = parser.parse_args()

    rows: list[dict[str, str]] = []
    for pair in load_source_pairs(args.source_pairs):
        old_path = args.cache_dir / f"{pair.pair_id}-old.txt"
        new_path = args.cache_dir / f"{pair.pair_id}-new.txt"
        if not old_path.is_file() or not new_path.is_file():
            continue
        old, new = numbered_blocks(old_path), numbered_blocks(new_path)
        for recommendation_id in sorted(old.keys() & new.keys()):
            changed = best_changed_pair(old[recommendation_id], new[recommendation_id], args.min_ratio, args.max_ratio)
            if not changed:
                continue
            old_span, new_span, ratio = changed
            rows.append({
                "source_pair": pair.pair_id,
                "source_family": pair.source_family,
                "recommendation_id": recommendation_id,
                "old_version": pair.old_version,
                "new_version": pair.new_version,
                "old_span": old_span,
                "new_span": new_span,
                "sequence_ratio": f"{ratio:.6f}",
                "provisional_semantic_type": classify(old_span, new_span),
                "quality_gate": "SAME_NUMBERED_RECOMMENDATION_ID",
                "annotation_status": "PENDING_DUAL_REVIEW",
            })
    write_csv(args.output, rows)
    print({"rows": len(rows), "types": dict(Counter(row["provisional_semantic_type"] for row in rows)), "output": str(args.output)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
