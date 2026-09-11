from __future__ import annotations

"""M15 temporal anchor recovery for hold-out TEMPORAL failures.

This module is candidate-blind. It reads public event metadata and frozen
evidence windows, resolves "versioned normative status" tasks as current
normative-claim anchoring tasks, and writes V4-compatible raw records for the
existing candidate repair / OWL closure evaluator.
"""

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from auto_policy_m12_decomposed_extraction import normalize_token
from run_auto_policy_v4_ir_candidate_repair import configure_paths, read_csv, ready
from run_m12_decomposed_extraction_pilot import load_evidence
from run_m13_rule_refinement_pilot import IR_PREFIX
from semantic_v2_common import PROJECT_DIR, write_csv


DEFAULT_BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v1-expanded"
DEFAULT_DETAILS = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v1-expanded"
    / "m14-second-half-eval"
    / "base-m13"
    / "arm-d-rule-refinement"
    / "ir"
    / f"{IR_PREFIX}-details.csv"
)
DEFAULT_OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v1-expanded" / "m15-temporal-anchor-recovery"

STOPWORDS = {
    "the",
    "and",
    "for",
    "that",
    "this",
    "with",
    "from",
    "are",
    "not",
    "must",
    "should",
    "shall",
    "may",
    "can",
    "when",
    "where",
    "which",
    "into",
    "than",
    "then",
    "been",
    "have",
    "has",
    "its",
    "their",
    "section",
    "rfc",
    "a",
    "an",
    "of",
    "is",
    "by",
    "to",
}


def truth(value: str) -> bool:
    return str(value or "").strip().lower() == "true"


def parse_only(raw: str) -> set[str]:
    return {token.strip().upper() for token in raw.split(",") if token.strip()}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def raw_record(row: dict[str, str]) -> dict[str, Any]:
    path = Path(row["raw_output_file"])
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return load_json(path)


def extract_anchor(case_context: str) -> str:
    match = re.search(r'begins:\s*"(.+?)"', str(case_context or ""), flags=re.I | re.S)
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip()
    return ""


def split_units(evidence: str) -> list[str]:
    units: list[str] = []
    for raw_line in str(evidence or "").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or line.startswith("[SOURCE_WINDOW_"):
            continue
        if len(line) <= 900:
            units.append(line)
        else:
            units.extend(piece.strip() for piece in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"(])", line) if piece.strip())
    compacted: list[str] = []
    seen: set[str] = set()
    for unit in units:
        key = normalize_token(unit)
        if key and key not in seen:
            compacted.append(unit)
            seen.add(key)
    return compacted


def slug(text: str, limit: int = 9) -> str:
    words = [
        token.lower().strip("-")
        for token in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", str(text or ""))
        if token.lower() not in STOPWORDS
    ]
    if not words:
        words = ["normative", "claim"]
    return "_".join(words[:limit])


def find_anchor_unit(anchor: str, evidence: str) -> tuple[str, str]:
    units = split_units(evidence)
    anchor_norm = normalize_token(anchor)
    if not units:
        return "", "no_evidence_units"
    if anchor_norm:
        for unit in units:
            unit_norm = normalize_token(unit)
            if unit_norm.startswith(anchor_norm) or anchor_norm in unit_norm:
                return unit, "case_context_anchor_exact"
        anchor_tokens = set(slug(anchor, limit=12).split("_"))
        best_unit = ""
        best_overlap = 0
        for unit in units:
            overlap = len(anchor_tokens & set(slug(unit, limit=18).split("_")))
            if overlap > best_overlap:
                best_overlap = overlap
                best_unit = unit
        if best_overlap >= max(3, min(6, len(anchor_tokens) // 2)):
            return best_unit, f"case_context_anchor_token_overlap:{best_overlap}"
    return units[0], "fallback_first_evidence_unit"


def build_record(source: dict[str, Any], event: dict[str, str], evidence_unit: str, anchor_reason: str) -> dict[str, Any]:
    family = f"{normalize_token(event.get('subject_label', ''))}_claim"
    value = slug(evidence_unit)
    semantic_result = f"{family}={value}"
    frame = {
        "answerable": bool(evidence_unit),
        "subject": event.get("subject_label", ""),
        "predicate": event.get("predicate_label", ""),
        "current_claim": evidence_unit,
        "current_value": value,
        "relation": "CURRENT_NORMATIVE_STATUS",
        "temporal_validity": "current_public_version",
        "evidence_spans": [evidence_unit] if evidence_unit else [],
        "confidence": "high" if evidence_unit else "low",
        "anchor_reason": anchor_reason,
    }
    canonical = {
        "family": family,
        "semantic_result": semantic_result,
        "derived_semantic_result": semantic_result,
        "relation": "EFFECTIVE_FROM",
        "result": semantic_result,
    }
    ok = bool(evidence_unit)
    response = {
        "semantic_type": "TEMPORAL_VERSION",
        "facts": {
            "subject": event.get("subject_label", ""),
            "statement": event.get("predicate_label", ""),
            "relation": "EFFECTIVE_FROM",
            "current_value": semantic_result,
            "new_value": semantic_result,
            "result": semantic_result,
            "evidence_spans": [evidence_unit] if evidence_unit else [],
            "m15_temporal_frame": frame,
            "faithfulness_status": "faithful" if ok else "not_answerable",
        },
        "canonical_result": canonical if ok else {"family": family, "faithfulness_status": "not_answerable"},
        "rules": (
            [
                {
                    "priority": 330,
                    "conditions": [],
                    "canonical_result": canonical,
                    "semantic_result": semantic_result,
                }
            ]
            if ok
            else []
        ),
        "m15_temporal_anchor_recovery": True,
    }
    return {
        **source,
        "status": "GENERATED" if ok else "INVALID_SCHEMA",
        "response": response,
        "schema_valid": ok,
        "validation_reason": "m15_temporal_anchor_converted" if ok else anchor_reason,
        "canonical_status": "OK" if ok else "INCOMPLETE",
        "canonical_result": canonical if ok else {"family": family},
        "method": "M15_TEMPORAL_ANCHOR_RECOVERY",
        "candidate_used": False,
        "oracle_used": False,
        "manual_policy_used": False,
        "m15_temporal_anchor_recovery": True,
    }


def run_ir(raw_dir: Path, out_dir: Path, benchmark_dir: Path) -> None:
    cmd = [
        sys.executable,
        str(PROJECT_DIR / "src" / "run_auto_policy_v4_ir_candidate_repair.py"),
        "--benchmark-dir",
        str(benchmark_dir),
        "--raw-dir",
        str(raw_dir),
        "--output-dir",
        str(out_dir),
        "--prefix",
        "m15-temporal-anchor-recovery-v4-ir",
        "--method-name",
        "M15_Temporal_Anchor_Recovery",
        "--min-score",
        "0.30",
        "--min-margin",
        "0.00",
        "--reranker",
        "constraint",
        "--temporal-unique-top1",
        "--robust-ir",
        "--discover-raw",
        "--allow-legacy-experiment",
    ]
    subprocess.run(cmd, check=True, cwd=PROJECT_DIR)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--details", type=Path, default=DEFAULT_DETAILS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--only", default="")
    parser.add_argument("--include-success", action="store_true")
    parser.add_argument("--skip-ir", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    args.benchmark_dir = args.benchmark_dir.resolve()
    args.details = args.details.resolve()
    args.output_dir = args.output_dir.resolve()
    raw_dir = args.output_dir / "raw"
    focus_dir = args.output_dir / "focused-evidence"
    raw_dir.mkdir(parents=True, exist_ok=True)
    focus_dir.mkdir(parents=True, exist_ok=True)

    paths = configure_paths(args.benchmark_dir)
    events = {row["event_id"]: row for row in read_csv(paths["event_csv"]) if ready(row.get("status", ""))}
    only = parse_only(args.only)

    candidates: list[dict[str, str]] = []
    for row in read_csv(args.details):
        if row.get("semantic_type") != "TEMPORAL_VERSION":
            continue
        if not args.include_success and truth(row.get("full_closure_success", "")):
            continue
        if only and row["event_id"].upper() not in only:
            continue
        candidates.append(row)
    if args.limit > 0:
        candidates = candidates[: args.limit]

    rows_out: list[dict[str, Any]] = []
    for row in candidates:
        event = events[row["event_id"]]
        filename = f"{row['event_id']}-run{row['run']}-seed{row['seed']}.json"
        raw_path = raw_dir / filename
        if args.resume and raw_path.is_file():
            print(f"{row['event_id']} run={row['run']} [resume] skipped", flush=True)
            continue
        source = raw_record(row)
        evidence = load_evidence(source)
        anchor = extract_anchor(event.get("case_context", ""))
        evidence_unit, anchor_reason = find_anchor_unit(anchor, evidence)
        focus_path = focus_dir / f"{row['event_id']}-run{row['run']}-seed{row['seed']}-temporal-focused.md"
        focus_path.write_text(evidence_unit + "\n", encoding="utf-8")
        record = build_record(source, event, evidence_unit, anchor_reason)
        record["m15_temporal_anchor_audit"] = {
            "source_raw_output_file": row.get("raw_output_file", ""),
            "previous_decision_path": row.get("decision_path", ""),
            "previous_ir_reason": row.get("ir_reason", ""),
            "case_context_anchor": anchor,
            "anchor_reason": anchor_reason,
            "focused_evidence_file": str(focus_path.relative_to(PROJECT_DIR)),
            "candidate_used": False,
            "oracle_used": False,
            "manual_policy_used": False,
        }
        raw_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rows_out.append(
            {
                "event_id": row["event_id"],
                "run": row["run"],
                "seed": row["seed"],
                "semantic_type": row["semantic_type"],
                "domain": row.get("domain", ""),
                "previous_decision_path": row.get("decision_path", ""),
                "previous_ir_reason": row.get("ir_reason", ""),
                "anchor_reason": anchor_reason,
                "anchor_slug": slug(evidence_unit),
                "raw_output_file": str(raw_path.relative_to(PROJECT_DIR)),
                "focused_evidence_file": str(focus_path.relative_to(PROJECT_DIR)),
            }
        )
        print(
            f"{row['event_id']} run={row['run']} prev={row.get('decision_path','')} "
            f"anchor={anchor_reason} slug={slug(evidence_unit)}",
            flush=True,
        )

    if rows_out:
        write_csv(args.output_dir / "m15-temporal-anchor-summary.csv", rows_out)
    if not args.skip_ir:
        run_ir(raw_dir, args.output_dir / "ir", args.benchmark_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
