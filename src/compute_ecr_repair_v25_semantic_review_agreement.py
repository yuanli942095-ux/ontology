from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "output/ecr-repair-post-freeze-blind-v1/v25-natural-literal-posthoc-r5/failure-analysis/replacement-semantic-blind-review"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else ["review_id", "field", "annotator_a_value", "annotator_b_value", "adjudicated_value", "adjudicator_id", "adjudication_notes"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def kappa(a: list[str], b: list[str]) -> float:
    observed = sum(x == y for x, y in zip(a, b)) / len(a)
    ca, cb = Counter(a), Counter(b)
    expected = sum(ca[key] * cb[key] for key in set(ca) | set(cb)) / (len(a) ** 2)
    return (observed - expected) / (1 - expected) if expected < 1 else 1.0


def note_required(row: dict[str, str]) -> bool:
    return row["semantic_relation"] != "EXACT_EQUIVALENT" or row["evidence_support_A"] != "YES" or row["evidence_support_B"] != "YES"


def main() -> int:
    a = {row["review_id"]: row for row in read_csv(BASE / "SEM_ANN_A-template.csv")}
    b = {row["review_id"]: row for row in read_csv(BASE / "SEM_ANN_B-template.csv")}
    if len(a) != 37 or set(a) != set(b):
        raise SystemExit("A/B sheet coverage mismatch")
    required = ("semantic_relation", "evidence_support_A", "evidence_support_B", "preferred_repair", "confidence")
    issues = []
    for label, rows, expected in (("A", a, "SEM_ANN_A"), ("B", b, "SEM_ANN_B")):
        for review_id, row in rows.items():
            if any(not row[field].strip() for field in required):
                issues.append(f"{label}:{review_id}:missing structured field")
            if row["reviewer_id"] != expected:
                issues.append(f"{label}:{review_id}:reviewer_id")
            if note_required(row) and not row["reviewer_notes"].strip():
                issues.append(f"{label}:{review_id}:required note missing")
    if issues:
        (BASE / "agreement-validation-errors.json").write_text(json.dumps(issues, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "INCOMPLETE", "issues": issues}, ensure_ascii=False, indent=2))
        return 1

    judged_fields = ("semantic_relation", "evidence_support_A", "evidence_support_B", "preferred_repair")
    disagreements = []
    for review_id in sorted(a):
        for field in judged_fields:
            if a[review_id][field] != b[review_id][field]:
                disagreements.append({
                    "review_id": review_id, "field": field,
                    "annotator_a_value": a[review_id][field], "annotator_b_value": b[review_id][field],
                    "adjudicated_value": "", "adjudicator_id": "SEM_ADJ_C", "adjudication_notes": "",
                })
    write_csv(BASE / "semantic-review-disagreements-for-SEM_ADJ_C.csv", disagreements)
    metrics = {}
    for field in (*judged_fields, "confidence"):
        av = [a[key][field] for key in sorted(a)]
        bv = [b[key][field] for key in sorted(b)]
        metrics[field] = {"agreement": sum(x == y for x, y in zip(av, bv)) / len(av), "kappa": kappa(av, bv), "disagreements": sum(x != y for x, y in zip(av, bv))}
    report = {
        "status": "AWAITING_SEM_ADJ_C" if disagreements else "AGREED_NO_ADJUDICATION_NEEDED",
        "pairs": 37, "disagreement_fields": len(disagreements),
        "disagreement_pairs": len({row["review_id"] for row in disagreements}), "metrics": metrics,
    }
    (BASE / "semantic-review-agreement.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
