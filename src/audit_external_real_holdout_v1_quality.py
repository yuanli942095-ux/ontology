from __future__ import annotations

"""Deep quality audit for external-real-holdout-v1-expanded."""

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR, write_csv


BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v1-expanded"
OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v1-expanded"

BOILERPLATE = re.compile(
    r"(?i)(copyright|ietf trust|ietf standards process|bcp 14|rfc 2119|rfc 8174|"
    r"table of contents|status of this memo|authors'? addresses|acknowledgements|"
    r"information about the current status|key words for use in rfcs)"
)
GENERIC_PREDICATE = re.compile(r"(?i)^(normative claim|normative requirement|regulatory text status|rule|scope|status)$")
GENERIC_DIMENSION = re.compile(r"(?i)^(normative_claim|status|scope|rule)$")


@dataclass
class Finding:
    level: str
    code: str
    event_id: str
    message: str


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def tokens(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", text)}


def parse_value(value: str) -> tuple[str, str]:
    if "=" not in value:
        return "", value
    left, right = value.split("=", 1)
    return left.strip(), right.strip()


def operation_old_new(row: dict[str, str]) -> tuple[str, str, str, str]:
    op = json.loads(row["operation_json"])
    return (
        str(op.get("subject_iri", "")),
        str(op.get("predicate_iri", "")),
        str(op.get("old_value", {}).get("lexical", "")),
        str(op.get("new_value", {}).get("lexical", "")),
    )


def first_window(text: str) -> str:
    match = re.search(r"(?s)\[SOURCE_WINDOW_1\]\s*(.*?)(?:\n\n\[SOURCE_WINDOW_2\]|\Z)", text)
    return match.group(1).strip() if match else text.strip()


def audit(args: argparse.Namespace) -> int:
    event_csv = args.benchmark_dir / "public" / "events" / "external-real-event-template.csv"
    candidate_csv = args.benchmark_dir / "repair-stage" / "candidates" / "external-real-candidate-template.csv"
    oracle_csv = args.benchmark_dir / "private" / "oracle" / "external-real-oracle-template.csv"
    events = [row for row in read_csv(event_csv) if row.get("status") == "READY"]
    candidates = read_csv(candidate_csv)
    oracles = read_csv(oracle_csv)

    by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in candidates:
        by_event[row["event_id"]].append(row)
    oracle_by_event = {row["event_id"]: row for row in oracles}
    findings: list[Finding] = []
    per_event: list[dict[str, Any]] = []
    seen_signatures: dict[str, str] = {}

    for event in events:
        event_id = event["event_id"]
        excerpt_path = args.benchmark_dir / "public" / "excerpts" / f"{event_id}-evidence.md"
        evidence = excerpt_path.read_text(encoding="utf-8", errors="replace") if excerpt_path.is_file() else ""
        window1 = first_window(evidence)
        cands = sorted(by_event[event_id], key=lambda row: row["candidate_id"])
        oracle = oracle_by_event.get(event_id, {})
        dims = []
        values = []
        old_values = []
        new_values = []
        op_subjects = set()
        op_predicates = set()
        candidate_json_ok = True
        for cand in cands:
            dim, value = parse_value(cand.get("display_value", ""))
            dims.append(dim)
            values.append(value)
            try:
                subj, pred, old_value, new_value = operation_old_new(cand)
                old_values.append(old_value)
                new_values.append(new_value)
                op_subjects.add(subj)
                op_predicates.add(pred)
            except Exception as exc:
                candidate_json_ok = False
                findings.append(Finding("ERROR", "BAD_OPERATION_JSON", event_id, f"{cand.get('candidate_id')}: {exc}"))

        if len(cands) != 3:
            findings.append(Finding("ERROR", "BAD_CANDIDATE_COUNT", event_id, str(len(cands))))
        if not candidate_json_ok:
            pass
        if len(set(dims)) != 1 or not dims[0]:
            findings.append(Finding("ERROR", "CANDIDATE_DIMENSION_MISMATCH", event_id, "|".join(dims)))
        elif GENERIC_DIMENSION.match(dims[0]):
            findings.append(Finding("WARNING", "GENERIC_CANDIDATE_DIMENSION", event_id, dims[0]))
        if len(set(op_subjects)) > 1 or len(set(op_predicates)) > 1:
            findings.append(Finding("ERROR", "OPERATION_IRI_INCONSISTENT", event_id, f"subjects={len(op_subjects)} predicates={len(op_predicates)}"))
        if len(set(old_values)) != 1 or (old_values and not old_values[0].endswith("=unmodeled")):
            findings.append(Finding("ERROR", "BAD_OLD_VALUE", event_id, "|".join(old_values)))
        for cand, new_value in zip(cands, new_values):
            if cand.get("display_value") != new_value:
                findings.append(Finding("ERROR", "DISPLAY_OPERATION_MISMATCH", event_id, cand.get("candidate_id", "")))
        if oracle.get("oracle_candidate_id") not in {cand["candidate_id"] for cand in cands}:
            findings.append(Finding("ERROR", "ORACLE_NOT_IN_CANDIDATES", event_id, oracle.get("oracle_candidate_id", "")))
        oracle_value = oracle.get("oracle_value", "")
        oracle_cand = next((cand for cand in cands if cand["candidate_id"] == oracle.get("oracle_candidate_id")), {})
        if oracle_cand and oracle_cand.get("display_value") != oracle_value:
            findings.append(Finding("ERROR", "ORACLE_VALUE_MISMATCH", event_id, f"{oracle_value} != {oracle_cand.get('display_value')}"))
        if BOILERPLATE.search(window1):
            findings.append(Finding("ERROR", "BOILERPLATE_TOP_WINDOW", event_id, window1[:180]))
        if len(window1) < 70 or not re.search(r"[.!?:;。！？：；]$", window1.strip()):
            findings.append(Finding("WARNING", "TOP_WINDOW_WEAK_OR_FRAGMENTED", event_id, window1[:180]))
        if GENERIC_PREDICATE.match(event.get("predicate_label", "")):
            findings.append(Finding("ERROR", "GENERIC_PREDICATE_LABEL", event_id, event.get("predicate_label", "")))

        correct_value = values[1] if len(values) > 1 else ""
        evidence_overlap = len(tokens(correct_value.replace("_", " ")) & tokens(evidence))
        window_overlap = len(tokens(correct_value.replace("_", " ")) & tokens(window1))
        if evidence_overlap < args.min_value_overlap:
            findings.append(Finding("ERROR", "LOW_VALUE_EVIDENCE_OVERLAP", event_id, f"value={correct_value} overlap={evidence_overlap}"))
        if window_overlap < args.min_top_window_overlap:
            findings.append(Finding("WARNING", "LOW_TOP_WINDOW_VALUE_OVERLAP", event_id, f"value={correct_value} overlap={window_overlap}"))
        negative_value = values[2] if len(values) > 2 else ""
        if negative_value == correct_value or negative_value == f"not_{correct_value}" and not correct_value:
            findings.append(Finding("ERROR", "BAD_DISTRACTOR", event_id, negative_value))

        signature = compact(window1[:420])
        if signature in seen_signatures:
            findings.append(Finding("WARNING", "DUPLICATE_TOP_WINDOW", event_id, seen_signatures[signature]))
        else:
            seen_signatures[signature] = event_id

        per_event.append(
            {
                "event_id": event_id,
                "semantic_type": event["semantic_type"],
                "domain": event["domain"],
                "predicate_label": event["predicate_label"],
                "candidate_dimension": dims[0] if dims else "",
                "candidate_correct_value": correct_value,
                "top_window_value_overlap": window_overlap,
                "evidence_value_overlap": evidence_overlap,
                "top_window_len": len(window1),
                "top_window_boilerplate": bool(BOILERPLATE.search(window1)),
                "top_window_fragmented": len(window1) < 70 or not re.search(r"[.!?:;。！？：；]$", window1.strip()),
            }
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(
        args.output_dir / "external-real-holdout-v1-deep-quality-findings.csv",
        [finding.__dict__ for finding in findings] or [{"level": "INFO", "code": "DEEP_QUALITY_PASS", "event_id": "", "message": ""}],
    )
    write_csv(args.output_dir / "external-real-holdout-v1-deep-quality-by-event.csv", per_event)
    counts = Counter((finding.level, finding.code) for finding in findings)
    summary = {
        "benchmark": args.benchmark_dir.name,
        "audited_at_utc": datetime.now(timezone.utc).isoformat(),
        "events": len(events),
        "errors": sum(1 for finding in findings if finding.level == "ERROR"),
        "warnings": sum(1 for finding in findings if finding.level == "WARNING"),
        "finding_counts": {f"{level}:{code}": count for (level, code), count in sorted(counts.items())},
        "strict_deep_quality_pass": not any(finding.level == "ERROR" for finding in findings),
    }
    (args.output_dir / "external-real-holdout-v1-deep-quality-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["errors"] else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--min-value-overlap", type=int, default=3)
    parser.add_argument("--min-top-window-overlap", type=int, default=2)
    return parser.parse_args()


def main() -> int:
    return audit(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
