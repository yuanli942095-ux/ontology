from __future__ import annotations

import csv
import hashlib
import json
import random
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "benchmark/ecr-repair-post-freeze-blind-v1"
RUN = ROOT / "output/ecr-repair-post-freeze-blind-v1/v25-natural-literal-posthoc-r5"
ANALYSIS = RUN / "failure-analysis"
OUT = ANALYSIS / "replacement-semantic-blind-review"
SEED = 20260910


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    evidence_out = OUT / "evidence"
    evidence_out.mkdir(exist_ok=True)
    failures = [row for row in read_csv(ANALYSIS / "failure-attempt-details.csv") if row["primary_cause"] == "REPLACEMENT_VALUE_ERROR"]
    events = {row["event_id"]: row for row in (json.loads(line) for line in (BENCHMARK / "public/events/events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())}
    golds = {row["event_id"]: row for row in (json.loads(line) for line in (BENCHMARK / "private/oracle/gold.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())}
    multiplicity = Counter((row["event_id"], row["raw_new_lexical"], row["gold_new_lexical"]) for row in failures)
    pairs = sorted(multiplicity)
    rng = random.Random(SEED)
    rng.shuffle(pairs)
    review_rows = []
    mappings = []
    for index, (event_id, predicted, gold_value) in enumerate(pairs, 1):
        review_id = f"SEM_{index:03d}"
        gold_side = rng.choice(("A", "B"))
        literal_a, literal_b = (gold_value, predicted) if gold_side == "A" else (predicted, gold_value)
        event = events[event_id]
        gold = golds[event_id]
        source_evidence = BENCHMARK / event["evidence_file"]
        evidence_name = f"{review_id}-evidence.md"
        evidence_text = (
            f"# Evidence packet {review_id}\n\n"
            f"Case context: {event['case_context']}\n\n"
            f"Target subject: {event['target']['subject_label']}\n\n"
            f"Target predicate: {event['target']['predicate_label']}\n\n"
            f"Current ontology literal: {gold['target']['old_value']['lexical']}\n\n"
            f"## Frozen evidence windows\n\n{source_evidence.read_text(encoding='utf-8')}"
        )
        evidence_path = evidence_out / evidence_name
        evidence_path.write_text(evidence_text, encoding="utf-8")
        review_rows.append({
            "review_id": review_id,
            "evidence_file": f"evidence/{evidence_name}",
            "literal_A": literal_a,
            "literal_B": literal_b,
            "semantic_relation": "",
            "evidence_support_A": "",
            "evidence_support_B": "",
            "preferred_repair": "",
            "confidence": "",
            "reviewer_notes": "",
            "reviewer_id": "",
        })
        mappings.append({
            "review_id": review_id, "event_id": event_id, "gold_side": gold_side,
            "predicted_side": "B" if gold_side == "A" else "A",
            "attempt_weight": multiplicity[(event_id, predicted, gold_value)],
            "predicted_literal": predicted, "gold_literal": gold_value,
            "source_evidence_sha256": sha(source_evidence), "review_evidence_sha256": sha(evidence_path),
        })

    for reviewer_id in ("SEM_ANN_A", "SEM_ANN_B"):
        rows = [dict(row, reviewer_id=reviewer_id) for row in review_rows]
        write_csv(OUT / f"{reviewer_id}-template.csv", rows)
    with (OUT / "mapping-do-not-give-reviewers.jsonl").open("w", encoding="utf-8") as handle:
        for row in mappings:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    write_csv(OUT / "adjudication-template.csv", [{
        "review_id": row["review_id"], "field": field,
        "annotator_a_value": "", "annotator_b_value": "", "adjudicated_value": "",
        "adjudicator_id": "SEM_ADJ_C", "adjudication_notes": "",
    } for row in review_rows for field in ("semantic_relation", "evidence_support_A", "evidence_support_B", "preferred_repair")])
    summary = {
        "status": "READY_FOR_INDEPENDENT_HUMAN_REVIEW", "source_attempts": len(failures),
        "unique_pairs": len(pairs), "events": len({pair[0] for pair in pairs}), "randomization_seed": SEED,
        "gold_side_counts": dict(Counter(row["gold_side"] for row in mappings)),
        "model_or_gold_identity_visible_to_reviewers": False,
    }
    (OUT / "build-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
