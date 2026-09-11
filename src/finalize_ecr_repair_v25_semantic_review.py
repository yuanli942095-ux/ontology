from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "output/ecr-repair-post-freeze-blind-v1/v25-natural-literal-posthoc-r5"
BASE = RUN / "failure-analysis/replacement-semantic-blind-review"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def final_value(review_id: str, field: str, a: dict[str, str], b: dict[str, str], adjudicated: dict[tuple[str, str], str]) -> tuple[str, str]:
    if a[field] == b[field]:
        return a[field], "A_B_AGREEMENT"
    key = (review_id, field)
    if key not in adjudicated:
        raise ValueError(f"missing adjudication: {key}")
    return adjudicated[key], "SEM_ADJ_C"


def main() -> int:
    a = {row["review_id"]: row for row in read_csv(BASE / "SEM_ANN_A-template.csv")}
    b = {row["review_id"]: row for row in read_csv(BASE / "SEM_ANN_B-template.csv")}
    adjudication_rows = read_csv(BASE / "semantic-review-disagreements-for-SEM_ADJ_C.csv")
    allowed = {
        "semantic_relation": {"EXACT_EQUIVALENT", "A_ENTAILS_B_ONLY", "B_ENTAILS_A_ONLY", "CONTRADICTORY", "RELATED_NOT_EQUIVALENT", "UNDECIDABLE"},
        "evidence_support_A": {"YES", "NO", "UNCERTAIN"}, "evidence_support_B": {"YES", "NO", "UNCERTAIN"},
        "preferred_repair": {"A", "B", "BOTH_EQUIVALENT", "NEITHER", "UNDECIDABLE"},
    }
    adjudicated = {}
    for row in adjudication_rows:
        field, value = row["field"], row["adjudicated_value"].strip()
        if field not in allowed or value not in allowed[field] or row["adjudicator_id"] != "SEM_ADJ_C" or not row["adjudication_notes"].strip():
            raise ValueError(f"invalid adjudication row: {row}")
        adjudicated[(row["review_id"], field)] = value

    mappings = [json.loads(line) for line in (BASE / "mapping-do-not-give-reviewers.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    resolved = []
    pair_acceptance: dict[tuple[str, str], dict[str, bool]] = {}
    for mapping in mappings:
        review_id = mapping["review_id"]
        values = {}
        provenance = {}
        for field in allowed:
            values[field], provenance[field] = final_value(review_id, field, a[review_id], b[review_id], adjudicated)
        predicted_side = mapping["predicted_side"]
        predicted_support = values[f"evidence_support_{predicted_side}"]
        strict_equivalent = values["semantic_relation"] == "EXACT_EQUIVALENT" and predicted_support == "YES" and values["preferred_repair"] == "BOTH_EQUIVALENT"
        preferred_supported = predicted_support == "YES" and values["preferred_repair"] in {predicted_side, "BOTH_EQUIVALENT"}
        pair_acceptance[(mapping["event_id"], mapping["predicted_literal"])] = {
            "strict_equivalent": strict_equivalent, "preferred_supported": preferred_supported,
        }
        resolved.append({
            "review_id": review_id, "event_id": mapping["event_id"], "attempt_weight": mapping["attempt_weight"],
            "predicted_side": predicted_side, "gold_side": mapping["gold_side"], **values,
            "strict_semantic_equivalent": strict_equivalent, "predicted_is_evidence_supported_preferred_repair": preferred_supported,
            "decision_provenance_json": json.dumps(provenance, sort_keys=True),
        })
    write_csv(BASE / "semantic-review-final-pair-decisions.csv", resolved)

    attempt_details = read_csv(RUN / "failure-analysis/failure-attempt-details.csv")
    adjusted = []
    for row in attempt_details:
        original = row["ses_success"].lower() == "true"
        acceptance = pair_acceptance.get((row["event_id"], row["raw_new_lexical"]), {})
        strict = original or bool(acceptance.get("strict_equivalent"))
        broad = original or bool(acceptance.get("preferred_supported"))
        adjusted.append({**row, "original_ses_success": original, "semantic_strict_success": strict, "semantic_preferred_supported_success": broad})
    write_csv(BASE / "semantic-adjusted-attempt-details.csv", adjusted)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in adjusted:
        for label in {"ALL", row["gold_partition"], row["semantic_type"], row["domain"]}:
            groups[label].append(row)
    summaries = []
    for label, rows in sorted(groups.items()):
        events: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            events[row["event_id"]].append(row)
        exact = sum(row["original_ses_success"] for row in rows)
        strict = sum(row["semantic_strict_success"] for row in rows)
        broad = sum(row["semantic_preferred_supported_success"] for row in rows)
        summaries.append({
            "group": label, "events": len(events), "attempts": len(rows), "exact_ses_successes": exact,
            "exact_ses_rate": exact / len(rows), "strict_semantic_successes": strict,
            "strict_semantic_rate": strict / len(rows), "preferred_supported_successes": broad,
            "preferred_supported_rate": broad / len(rows),
            "strict_semantic_event_successes": sum(all(item["semantic_strict_success"] for item in items) for items in events.values()),
            "strict_semantic_event_rate": sum(all(item["semantic_strict_success"] for item in items) for items in events.values()) / len(events),
        })
    write_csv(BASE / "semantic-adjusted-summary.csv", summaries)
    overall = next(row for row in summaries if row["group"] == "ALL")
    payload = {
        "status": "FINAL_HUMAN_ADJUDICATED_POST_HOC_SEMANTIC_SENSITIVITY",
        "pairs": len(resolved), "source_mismatch_attempts": sum(int(row["attempt_weight"]) for row in resolved),
        "pair_relation_counts": dict(Counter(row["semantic_relation"] for row in resolved)),
        "strict_equivalent_pairs": sum(row["strict_semantic_equivalent"] for row in resolved),
        "strict_equivalent_weighted_attempts": sum(int(row["attempt_weight"]) for row in resolved if row["strict_semantic_equivalent"]),
        "preferred_supported_pairs": sum(row["predicted_is_evidence_supported_preferred_repair"] for row in resolved),
        "preferred_supported_weighted_attempts": sum(int(row["attempt_weight"]) for row in resolved if row["predicted_is_evidence_supported_preferred_repair"]),
        "overall": overall,
        "boundary": "Post-hoc semantic sensitivity analysis; frozen literal-exact SES remains the primary metric.",
    }
    (BASE / "semantic-review-final-summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
