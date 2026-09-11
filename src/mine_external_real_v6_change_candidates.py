from __future__ import annotations

"""Mine reviewable change units from cached official version pairs.

The miner is deterministic and source-first. It does not read benchmark event
candidates, formal policies, or private Oracle files, and its output is a
curation queue rather than an accepted benchmark.
"""

import csv
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from build_external_real_v5_retrieved_evidence import split_units, tokens


ROOT = Path(r"G:\LearnAI\ontology-evolution")
CACHE = ROOT / "data" / "external-real-v6-normative-cache"
REGISTRY = CACHE / "normative-source-registry.csv"
MANIFEST = CACHE / "source-cache-manifest.csv"
OUTPUT = CACHE / "change-candidate-review-v2.csv"
SUMMARY = CACHE / "change-candidate-review-v2-summary.json"

NORMATIVE_TERMS = {
    "shall": 3.0,
    "must": 3.0,
    "required": 2.5,
    "requirement": 2.0,
    "prohibited": 3.0,
    "shall not": 3.5,
    "must not": 3.5,
    "should": 1.5,
    "may only": 2.5,
    "applies": 1.5,
    "applicable": 1.5,
    "effective": 1.5,
    "no later than": 2.5,
    "at least": 2.0,
    "within": 1.0,
    "不得": 3.0,
    "应当": 3.0,
    "必须": 3.0,
    "可以": 1.0,
    "适用": 1.5,
    "不低于": 2.0,
    "不超过": 2.0,
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def cache_lookup() -> dict[str, dict[str, str]]:
    return {
        row["url"]: row
        for row in read_csv(MANIFEST)
        if row.get("status") == "SUCCESS" and Path(row.get("text_path", "")).is_file()
    }


def normalized_unit(unit: str) -> str:
    return re.sub(r"\s+", " ", unit).strip()


def eligible_units(text: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for unit in split_units(text):
        unit = normalized_unit(unit)
        lower = unit.lower()
        if lower in seen or not 55 <= len(unit) <= 1000:
            continue
        if len(tokens(unit)) < 5:
            continue
        if re.search(r"copyright|all rights reserved|privacy policy|cookie", lower):
            continue
        seen.add(lower)
        result.append(unit)
    return result


def normative_score(text: str) -> float:
    lower = text.lower()
    score = sum(weight for term, weight in NORMATIVE_TERMS.items() if term in lower)
    numeric = len(set(re.findall(r"\b\d+(?:\.\d+)?(?:%| days?| years?)?\b", lower)))
    score += min(3.0, 0.6 * numeric)
    if re.search(r"\b(article|section|paragraph|criterion|control|function|category)\b", lower):
        score += 1.0
    if 90 <= len(text) <= 500:
        score += 0.75
    return round(score, 3)


def inverted_index(units: list[str]) -> tuple[list[set[str]], dict[str, set[int]]]:
    token_sets = [tokens(unit) for unit in units]
    index: dict[str, set[int]] = defaultdict(set)
    for position, unit_tokens in enumerate(token_sets):
        for token in unit_tokens:
            index[token].add(position)
    return token_sets, index


def best_previous(
    current_tokens: set[str],
    previous_units: list[str],
    previous_tokens: list[set[str]],
    index: dict[str, set[int]],
) -> tuple[str, float]:
    positions: set[int] = set()
    for token in current_tokens:
        positions.update(index.get(token, set()))
    best_unit = ""
    best_score = 0.0
    for position in positions:
        other = previous_tokens[position]
        intersection = len(current_tokens & other)
        union = len(current_tokens | other)
        score = intersection / union if union else 0.0
        if score > best_score:
            best_score = score
            best_unit = previous_units[position]
    return best_unit, round(best_score, 4)


def mine_pair(
    family: dict[str, str],
    cache: dict[str, dict[str, str]],
    limit: int = 35,
) -> list[dict[str, Any]]:
    current_text = Path(cache[family["source_url"]]["text_path"]).read_text(
        encoding="utf-8", errors="replace"
    )
    previous_text = Path(cache[family["old_source_url"]]["text_path"]).read_text(
        encoding="utf-8", errors="replace"
    )
    current_units = eligible_units(current_text)
    previous_units = eligible_units(previous_text)
    previous_tokens, index = inverted_index(previous_units)
    ranked: list[tuple[float, dict[str, Any], set[str]]] = []

    for current in current_units:
        current_tokens = tokens(current)
        previous, similarity = best_previous(
            current_tokens, previous_units, previous_tokens, index
        )
        norm_score = normative_score(current)
        if norm_score < 1.0 or similarity >= 0.92:
            continue
        change_type = "ADDED" if similarity < 0.15 else "MODIFIED"
        change_score = norm_score * (1.15 - similarity) + math.log2(1 + len(current_tokens)) / 5
        row = {
            "source_id": family["source_id"],
            "domain": family["domain"],
            "publisher": family["publisher"],
            "change_type": change_type,
            "change_score": round(change_score, 4),
            "similarity_to_previous": similarity,
            "normative_score": norm_score,
            "current_excerpt": current,
            "previous_excerpt": previous,
            "current_source_title": family["source_title"],
            "current_source_url": family["source_url"],
            "previous_source_title": family["old_source_title"],
            "previous_source_url": family["old_source_url"],
            "review_status": "UNREVIEWED",
            "reviewer_id": "",
            "evidence_locator": "",
            "review_seconds": "",
            "review_notes": "",
            "candidate_used": False,
            "formal_policy_used": False,
            "private_oracle_used": False,
        }
        ranked.append((change_score, row, current_tokens))

    ranked.sort(key=lambda item: (-item[0], item[1]["current_excerpt"]))
    selected: list[dict[str, Any]] = []
    selected_tokens: list[set[str]] = []
    for _, row, row_tokens in ranked:
        if any(
            len(row_tokens & existing) / max(1, len(row_tokens | existing)) >= 0.80
            for existing in selected_tokens
        ):
            continue
        selected.append(row)
        selected_tokens.append(row_tokens)
        if len(selected) >= limit:
            break
    return selected


def main() -> int:
    cache = cache_lookup()
    rows: list[dict[str, Any]] = []
    for family in read_csv(REGISTRY):
        if family["source_url"] not in cache or family["old_source_url"] not in cache:
            continue
        rows.extend(mine_pair(family, cache))
    rows.sort(key=lambda row: (row["domain"], row["source_id"], -float(row["change_score"])))
    for number, row in enumerate(rows, start=1):
        row["mining_id"] = f"MINE_{number:04d}"
    # Put stable IDs first in the CSV without changing the remaining audit fields.
    rows = [{"mining_id": row.pop("mining_id"), **row} for row in rows]
    write_csv(OUTPUT, rows)

    domains = Counter(row["domain"] for row in rows)
    sources = Counter(row["source_id"] for row in rows)
    summary = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "review_candidates": len(rows),
        "domains": dict(sorted(domains.items())),
        "source_families": dict(sorted(sources.items())),
        "candidate_used": False,
        "formal_policy_used": False,
        "private_oracle_used": False,
        "output": str(OUTPUT),
        "boundary": (
            "Rows are automatically mined change candidates, not accepted events. "
            "Human source verification and independent event construction are required."
        ),
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
