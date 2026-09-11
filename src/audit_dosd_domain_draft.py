from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from dosd_multidomain_common import SEMANTIC_TYPES, read_csv, sha256_text, write_csv


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "medical": {"TEMPORAL_VERSION": 90, "GENERAL_RULE_EXCEPTION": 90, "CROSS_SENTENCE_SCOPE": 84},
    "legal": {"TEMPORAL_VERSION": 80, "GENERAL_RULE_EXCEPTION": 94, "CROSS_SENTENCE_SCOPE": 90},
}


def cohen_kappa(pairs: list[tuple[str, str]]) -> float | None:
    if not pairs:
        return None
    labels = sorted({value for pair in pairs for value in pair})
    observed = sum(a == b for a, b in pairs) / len(pairs)
    expected = sum(
        sum(a == label for a, _ in pairs) / len(pairs) * sum(b == label for _, b in pairs) / len(pairs)
        for label in labels
    )
    return None if math.isclose(expected, 1.0) else (observed - expected) / (1.0 - expected)


def audit(corpus: str, benchmark: Path, output: Path, allow_observed_distribution: bool = False) -> int:
    findings: list[dict[str, str]] = []
    events = read_csv(benchmark / "public" / "events" / "external-real-event-template.csv")
    documents = read_csv(benchmark / "public" / "documents" / "external-real-document-template.csv")
    candidates = read_csv(benchmark / "repair-stage" / "candidates" / "external-real-candidate-template.csv")
    source_pairs = read_csv(benchmark / "public" / "retrieval" / "external-real-v8-grounded-source-families.csv")
    annotations_a = read_csv(benchmark / "private" / "annotation" / "annotation-sheet-A.csv")
    annotations_b = read_csv(benchmark / "private" / "annotation" / "annotation-sheet-B.csv")
    construction = read_csv(benchmark / "private" / "construction" / "construction-provenance.csv")
    by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in candidates:
        by_event[row["event_id"]].append(row)
    docs_by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in documents:
        docs_by_event[row["event_id"]].append(row)
    seen_evidence: dict[str, str] = {}
    for event in events:
        event_id = event["event_id"]
        if len(by_event[event_id]) != 3:
            findings.append({"level": "ERROR", "code": "BAD_CANDIDATE_COUNT", "event_id": event_id, "message": str(len(by_event[event_id]))})
        if len(docs_by_event[event_id]) != 2 or {row["document_id"].rsplit("_", 1)[-1] for row in docs_by_event[event_id]} != {"OLD", "NEW"}:
            findings.append({"level": "ERROR", "code": "BAD_VERSION_DOCUMENT_PAIR", "event_id": event_id, "message": str(len(docs_by_event[event_id]))})
        excerpt_path = benchmark / "public" / "excerpts" / f"{event_id}-evidence.md"
        evidence = excerpt_path.read_text(encoding="utf-8", errors="replace") if excerpt_path.is_file() else ""
        match = re.search(r"(?s)\[OLD_VERSION\]\s*(.*?)\s*\[NEW_VERSION\]\s*(.*)", evidence)
        if not match or match.group(1).strip() == match.group(2).strip():
            findings.append({"level": "ERROR", "code": "BAD_OLD_NEW_EVIDENCE", "event_id": event_id, "message": "missing or identical spans"})
        signature = sha256_text(evidence)
        if signature in seen_evidence:
            findings.append({"level": "ERROR", "code": "DUPLICATE_EVIDENCE_EVENT", "event_id": event_id, "message": seen_evidence[signature]})
        seen_evidence[signature] = event_id
        values = [row["display_value"] for row in by_event[event_id]]
        if len(set(values)) != 3:
            findings.append({"level": "ERROR", "code": "DUPLICATE_CANDIDATE_VALUE", "event_id": event_id, "message": ""})
        for candidate in by_event[event_id]:
            try:
                operation = json.loads(candidate["operation_json"])
                if operation["new_value"]["lexical"] != candidate["display_value"]:
                    raise ValueError("display/operation mismatch")
            except Exception as exc:
                findings.append({"level": "ERROR", "code": "BAD_OPERATION", "event_id": event_id, "message": str(exc)})
    type_counts = Counter(row["semantic_type"] for row in events)
    unknown_types = sorted({row["semantic_type"] for row in events} - set(SEMANTIC_TYPES))
    if len(events) != 264:
        findings.append({"level": "ERROR", "code": "BAD_EVENT_COUNT", "event_id": "", "message": str(len(events))})
    if unknown_types:
        findings.append({"level": "ERROR", "code": "INVALID_SEMANTIC_TYPE", "event_id": "", "message": json.dumps(unknown_types)})
    if not allow_observed_distribution and dict(type_counts) != EXPECTED[corpus]:
        findings.append({"level": "ERROR", "code": "BAD_TARGET_DISTRIBUTION", "event_id": "", "message": json.dumps(dict(type_counts))})
    if allow_observed_distribution and not all(type_counts.get(label, 0) > 0 for label in SEMANTIC_TYPES):
        findings.append({"level": "ERROR", "code": "MISSING_SEMANTIC_TYPE", "event_id": "", "message": json.dumps(dict(type_counts))})
    successful_pairs = [row for row in source_pairs if row.get("status") == "SUCCESS"]
    if len(successful_pairs) < 3:
        findings.append({"level": "ERROR", "code": "TOO_FEW_SOURCE_PAIRS", "event_id": "", "message": str(len(successful_pairs))})
    used_pair_counts = Counter(row["pair_id"] for row in construction)
    if len(used_pair_counts) < 4:
        findings.append({"level": "ERROR", "code": "TOO_FEW_CONTRIBUTING_SOURCE_PAIRS", "event_id": "", "message": json.dumps(dict(used_pair_counts))})
    for row in construction:
        if float(row["automated_alignment_score"]) < 0.45:
            findings.append({"level": "ERROR", "code": "LOW_ALIGNMENT_SCORE", "event_id": row["event_id"], "message": row["automated_alignment_score"]})
    oracle_path = benchmark / "private" / "oracle" / "external-real-oracle-template.csv"
    finalization_path = benchmark / "private" / "annotation" / "dual-review-finalization-summary.json"
    oracle_rows = read_csv(oracle_path) if oracle_path.is_file() else []
    if oracle_rows and not finalization_path.is_file():
        findings.append({"level": "WARNING", "code": "ORACLE_EXISTS", "event_id": "", "message": "verify dual-review completion"})
    completed_a = [row for row in annotations_a if row.get("annotator_gold_candidate")]
    completed_b = [row for row in annotations_b if row.get("annotator_gold_candidate")]
    annotation_status = "COMPLETE" if len(completed_a) == len(events) == len(completed_b) else "PENDING"
    by_b = {row["event_id"]: row for row in annotations_b}
    type_pairs = [
        (a["annotator_drift_type"], by_b[a["event_id"]]["annotator_drift_type"])
        for a in annotations_a
        if a.get("annotator_drift_type") and by_b.get(a["event_id"], {}).get("annotator_drift_type")
    ]
    gold_pairs = [
        (a["annotator_gold_candidate"], by_b[a["event_id"]]["annotator_gold_candidate"])
        for a in annotations_a
        if a.get("annotator_gold_candidate") and by_b.get(a["event_id"], {}).get("annotator_gold_candidate")
    ]
    type_disagreements = [
        a["event_id"]
        for a in annotations_a
        if a.get("annotator_drift_type")
        and by_b.get(a["event_id"], {}).get("annotator_drift_type")
        and a["annotator_drift_type"] != by_b[a["event_id"]]["annotator_drift_type"]
    ]
    adjudication_path = benchmark / "private" / "annotation" / "drift-type-adjudication.csv"
    adjudicated = {row["event_id"] for row in read_csv(adjudication_path)} if adjudication_path.is_file() else set()
    unresolved_types = [event_id for event_id in type_disagreements if event_id not in adjudicated]
    if annotation_status == "COMPLETE" and unresolved_types:
        findings.append({"level": "ERROR", "code": "UNRESOLVED_DRIFT_TYPE", "event_id": "", "message": json.dumps(unresolved_types)})
    freeze_allowed = annotation_status == "COMPLETE" and not any(row["level"] == "ERROR" for row in findings) and not unresolved_types
    summary = {
        "benchmark": benchmark.name,
        "audited_at_utc": datetime.now(timezone.utc).isoformat(),
        "events": len(events),
        "candidate_rows": len(candidates),
        "successful_source_pairs": len(successful_pairs),
        "contributing_source_pairs": dict(used_pair_counts),
        "semantic_types": dict(type_counts),
        "errors": sum(row["level"] == "ERROR" for row in findings),
        "warnings": sum(row["level"] == "WARNING" for row in findings),
        "annotation_status": annotation_status,
        "drift_type_kappa": cohen_kappa(type_pairs),
        "gold_candidate_kappa": cohen_kappa(gold_pairs),
        "drift_type_disagreements": len(type_disagreements),
        "drift_types_adjudicated": len(adjudicated),
        "unresolved_drift_types": len(unresolved_types),
        "oracle_rows": len(oracle_rows),
        "freeze_allowed": freeze_allowed,
    }
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "quality-findings.csv", findings or [{"level": "INFO", "code": "STRUCTURE_PASS", "event_id": "", "message": ""}])
    (output / "quality-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["errors"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", choices=("medical", "legal"), required=True)
    parser.add_argument("--benchmark-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--allow-observed-distribution", action="store_true", help="For anchored v2 pools, require 264 events and all types but do not force the v1 target mix.")
    args = parser.parse_args()
    benchmark = args.benchmark_dir or ROOT / "benchmark" / f"dosd-{args.corpus}-v1-draft"
    output = args.output_dir or ROOT / "output" / f"dosd-{args.corpus}-v1-draft"
    return audit(args.corpus, benchmark, output, args.allow_observed_distribution)


if __name__ == "__main__":
    raise SystemExit(main())
