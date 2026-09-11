from __future__ import annotations

"""Offline pilot: compare exact literal matching with canonical value signatures."""

import csv
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
BENCHMARK = PROJECT / "benchmark/ecr-repair-post-freeze-blind-v1"
DECISIONS = PROJECT / "output/ecr-repair-v26-development/80-event-canonical-tuple-v5/tuple-decisions.csv"
OUTPUT = PROJECT / "output/ecr-repair-v26-development/literal-gold-tuple-pilot"

PILOT_EVENTS = (
    "BLIND_E001", "BLIND_E007", "BLIND_E018", "BLIND_E026", "BLIND_E027",
    "BLIND_E029", "BLIND_E052", "BLIND_E059", "BLIND_E061", "BLIND_E066",
)

ALIASES = {
    "shall": "must", "must": "must", "required": "must",
    "triggers": "trigger", "triggered": "trigger", "sectors": "sector",
    "services": "service", "effects": "effect", "claims": "claim",
    "values": "value", "objects": "object", "disks": "disk",
}
STOP = {
    "a", "an", "the", "of", "to", "for", "in", "on", "by", "with", "and",
    "or", "is", "are", "be", "as", "that", "this", "which", "will", "has",
    "have", "its", "their", "from", "such", "other", "sector",
}


@dataclass(frozen=True)
class ValueSignature:
    concepts: frozenset[str]
    numbers: frozenset[str]
    negated: bool


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold().rstrip(".")


def token(token: str) -> str:
    token = token.casefold()
    if token in ALIASES:
        return ALIASES[token]
    if len(token) > 5 and token.endswith("ing"):
        token = token[:-3]
    elif len(token) > 4 and token.endswith("ed"):
        token = token[:-2]
    elif len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        token = token[:-1]
    return ALIASES.get(token, token)


def signature(text: str) -> ValueSignature:
    raw = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", normalize(text))
    numbers = frozenset(re.findall(r"\d+(?:\.\d+)?", normalize(text)))
    concepts = frozenset(token(x) for x in raw if not x[0].isdigit() and token(x) not in STOP)
    negated = any(x in {"not", "no", "never", "without", "prohibit", "forbid"} for x in raw)
    return ValueSignature(concepts, numbers, negated)


def tuple_equivalent(left: str, right: str) -> tuple[bool, float]:
    a, b = signature(left), signature(right)
    if a.negated != b.negated or a.numbers != b.numbers:
        return False, 0.0
    overlap = len(a.concepts & b.concepts) / min(len(a.concepts), len(b.concepts)) if a.concepts and b.concepts else 0.0
    return overlap >= 0.80, overlap


def negative_control(gold: str) -> str:
    numbers = re.findall(r"\d+(?:\.\d+)?", gold)
    if numbers:
        first = numbers[0]
        return gold.replace(first, str(int(float(first)) + 1), 1)
    if re.search(r"\b(?:not|no|never)\b", gold, flags=re.I):
        return re.sub(r"\b(?:not|no|never)\b", "", gold, count=1, flags=re.I)
    return "NOT " + gold


def load_jsonl(path: Path) -> dict[str, dict]:
    return {row["event_id"]: row for row in (json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip())}


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    gold = load_jsonl(BENCHMARK / "private/oracle/gold.jsonl")
    with DECISIONS.open(encoding="utf-8-sig", newline="") as handle:
        decisions = {row["event_id"]: row for row in csv.DictReader(handle)}

    rows = []
    for event_id in PILOT_EVENTS:
        answer = gold[event_id]
        decision = decisions[event_id]
        predicted = json.loads(decision["predicted_ir_json"])["replacement"]["new_value"]["lexical"]
        expected = answer["replacement"]["new_value"]["lexical"]
        negative = negative_control(expected)
        exact_positive = normalize(predicted) == normalize(expected)
        tuple_positive, positive_overlap = tuple_equivalent(predicted, expected)
        tuple_negative, negative_overlap = tuple_equivalent(negative, expected)
        rows.append({
            "event_id": event_id,
            "predicted_literal": predicted,
            "gold_literal": expected,
            "negative_control": negative,
            "exact_positive": exact_positive,
            "tuple_positive": tuple_positive,
            "positive_overlap": round(positive_overlap, 4),
            "tuple_false_accept_negative": tuple_negative,
            "negative_overlap": round(negative_overlap, 4),
            "predicted_signature": json.dumps(asdict(signature(predicted)), default=sorted, ensure_ascii=False, sort_keys=True),
            "gold_signature": json.dumps(asdict(signature(expected)), default=sorted, ensure_ascii=False, sort_keys=True),
        })

    with (OUTPUT / "details.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)

    exact_tp = sum(row["exact_positive"] for row in rows)
    tuple_tp = sum(row["tuple_positive"] for row in rows)
    tuple_fp = sum(row["tuple_false_accept_negative"] for row in rows)
    summary = {
        "status": "DEVELOPMENT_OFFLINE_PILOT",
        "events": len(rows),
        "model_calls": 0,
        "exact_literal_equivalent_accepts": exact_tp,
        "exact_literal_equivalent_recall": exact_tp / len(rows),
        "canonical_signature_equivalent_accepts": tuple_tp,
        "canonical_signature_equivalent_recall": tuple_tp / len(rows),
        "negative_controls": len(rows),
        "canonical_signature_false_accepts": tuple_fp,
        "canonical_signature_false_accept_rate": tuple_fp / len(rows),
        "success_criterion": "canonical recall exceeds exact recall and negative-control false-accept rate is zero",
        "criterion_pass": tuple_tp > exact_tp and tuple_fp == 0,
        "boundary": "The ten events come from the V2.6 development set. This tests representation-layer feasibility, not blind end-to-end generalization.",
    }
    (OUTPUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(
        "# Literal Gold vs canonical signature pilot\n\n"
        "This zero-call development pilot compares normalized exact-string evaluation with a deterministic value signature. "
        "Each real semantically equivalent prediction/Gold pair is accompanied by a polarity- or number-changing negative control.\n\n"
        f"- Exact recall: {exact_tp}/{len(rows)}\n"
        f"- Canonical-signature recall: {tuple_tp}/{len(rows)}\n"
        f"- Negative false accepts: {tuple_fp}/{len(rows)}\n"
        f"- Criterion: {'PASS' if summary['criterion_pass'] else 'FAIL'}\n\n"
        "This is not a replacement for human tuple annotation or an unseen confirmatory set.\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
