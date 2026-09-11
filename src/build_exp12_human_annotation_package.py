from __future__ import annotations

"""Freeze Exp12 90-event human annotation sample and annotator packages."""

import argparse
import json
import random
from pathlib import Path
from typing import Any

from exp9_baseline_common import DEFAULT_BENCHMARK, compact_operation, load_event_bundles
from paper_final_validation_common import (
    DEFAULT_BENCHMARK_FREEZE_SUMMARY,
    DEFAULT_METHOD_FREEZE_DIR,
    PAPER_VALIDATION_ROOT,
    benchmark_paths,
    load_freeze_sha256,
    load_method_freeze_sha256,
    load_oracles,
    utc_now_iso,
    write_binding,
    write_manifest_csv,
    write_summary_json,
)
from semantic_v2_common import PROJECT_DIR, write_csv


EXP12_ROOT = PAPER_VALIDATION_ROOT / "12-human-annotation"
SAMPLE_SEED = 20260907
TYPE_TARGETS = {
    "TEMPORAL_VERSION": 30,
    "GENERAL_RULE_EXCEPTION": 30,
    "CROSS_SENTENCE_SCOPE": 30,
}
TYPE_LABEL = {
    "TEMPORAL_VERSION": "TEMP",
    "GENERAL_RULE_EXCEPTION": "GRE",
    "CROSS_SENTENCE_SCOPE": "CSS",
}


def excerpt_text(benchmark: Path, event_id: str) -> str:
    path = benchmark / "public" / "excerpts" / f"{event_id}-evidence.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace").strip()


def candidate_blind_summary(candidates: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for cand in candidates:
        op = compact_operation(cand.get("operation", {}))
        parts.append(
            f"{cand.get('candidate_id', '')}: {cand.get('display_value', '')} "
            f"op={op.get('operator', '')}"
        )
    return " | ".join(parts)


def heuristic_annotator_a(event: dict[str, str], oracle: dict[str, str]) -> dict[str, str]:
    sufficient = str(event.get("support_status", "")).strip().upper() == "SEMANTIC_REVIEW_PASSED"
    return {
        "semantic_type": event.get("semantic_type", ""),
        "evidence_sufficient": "yes" if sufficient else "no",
        "oracle_candidate": oracle.get("oracle_candidate_id", ""),
        "confidence": "high",
        "supporting_span": str(event.get("case_context", ""))[:500],
        "reason": "Annotator-A heuristic from frozen oracle review (holdout_manual_oracle_review_agreed).",
    }


def build_guidelines(path: Path) -> None:
    path.write_text(
        """# Exp12 Human Annotation Guidelines

## Purpose
Independent Annotator B audit on a frozen 90-event sample (30 TEMP / 30 GRE / 30 CSS) from
candidate-ID-permute benchmark. Annotator A is a frozen heuristic oracle route for agreement analysis.

## What you may use
- Event metadata (domain, subject, predicate, case context)
- Public evidence excerpts
- Candidate display values and formal operations (blind IDs only)

## What you must NOT use
- Private original oracle files
- Annotator A judgments
- Model predictions (M13–M16)
- Candidate ranking scores

## Required fields (Annotator B)
- `semantic_type`: TEMPORAL_VERSION | GENERAL_RULE_EXCEPTION | CROSS_SENTENCE_SCOPE
- `evidence_sufficient`: yes | no
- `oracle_candidate`: CAND_001 | CAND_002 | CAND_003 | UNDECIDABLE
- `confidence`: high | medium | low
- `supporting_span`: quote or anchor phrase from evidence
- `reason`: short justification

## Type-specific guidance

### TEMP (temporal version)
Supported when evidence states how normative status changes across document versions
(obsolete/supersede/update/replace) for the anchored subject/predicate.

### GRE (general rule + exception)
Supported when evidence states a general rule and the exception trigger/outcome for the subject.

### CSS (cross-sentence scope)
Supported when evidence links qualifier/scope across sentences for the subject statement.

## UNDECIDABLE
Use when evidence is insufficient, contradictory without resolution, or scope cannot be
determined from the public window alone.

## Disagreement workflow
Fill `annotator-b-responses.csv`. Disagreements vs Annotator A are listed in
`annotation-disagreements.csv` for adjudication (`annotation-adjudication.csv`).
""",
        encoding="utf-8",
    )


def sample_events(bundles: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    by_type: dict[str, list[dict[str, Any]]] = {key: [] for key in TYPE_TARGETS}
    for bundle in bundles:
        semantic_type = bundle.get("semantic_type", "")
        if semantic_type in by_type:
            by_type[semantic_type].append(bundle)
    for semantic_type, rows in by_type.items():
        rows.sort(key=lambda row: row["event_id"])
    rng = random.Random(seed)
    selected: list[dict[str, Any]] = []
    for semantic_type, target in TYPE_TARGETS.items():
        pool = by_type[semantic_type]
        if len(pool) < target:
            raise RuntimeError(f"insufficient {semantic_type}: need {target}, have {len(pool)}")
        indices = list(range(len(pool)))
        rng.shuffle(indices)
        chosen = sorted([pool[i] for i in indices[:target]], key=lambda row: row["event_id"])
        selected.extend(chosen)
    selected.sort(key=lambda row: row["event_id"])
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=EXP12_ROOT)
    parser.add_argument("--seed", type=int, default=SAMPLE_SEED)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    benchmark_sha256, benchmark_manifest = load_freeze_sha256(DEFAULT_BENCHMARK_FREEZE_SUMMARY)
    method_sha256, method_manifest = load_method_freeze_sha256(DEFAULT_METHOD_FREEZE_DIR)
    paths = benchmark_paths(args.benchmark_dir)
    oracles = load_oracles(paths)
    bundles = sample_events(load_event_bundles(args.benchmark_dir), args.seed)

    sample_rows: list[dict[str, str]] = []
    annotator_a_rows: list[dict[str, str]] = []
    annotator_b_rows: list[dict[str, str]] = []

    for bundle in bundles:
        event_id = bundle["event_id"]
        oracle = oracles.get(event_id, {})
        a = heuristic_annotator_a(bundle, oracle)
        evidence = excerpt_text(args.benchmark_dir, event_id)
        candidates_summary = candidate_blind_summary(bundle.get("candidates", []))
        base = {
            "event_id": event_id,
            "semantic_type": bundle.get("semantic_type", ""),
            "semantic_type_label": TYPE_LABEL.get(bundle.get("semantic_type", ""), ""),
            "domain": bundle.get("domain", ""),
            "subject_label": bundle.get("subject_label", ""),
            "predicate_label": bundle.get("predicate_label", ""),
            "case_context": bundle.get("case_context", ""),
            "source_url": bundle.get("source_url", ""),
            "public_evidence": evidence,
            "candidate_blind_summary": candidates_summary,
            "sample_seed": str(args.seed),
        }
        sample_rows.append(base)
        annotator_a_rows.append({**base, **a, "annotator": "A-heuristic"})
        annotator_b_rows.append(
            {
                **base,
                "annotator": "B-blind",
                "semantic_type": "",
                "evidence_sufficient": "",
                "oracle_candidate": "",
                "confidence": "",
                "supporting_span": "",
                "reason": "",
            }
        )

    write_csv(args.output_dir / "human-audit-sample.csv", sample_rows)
    write_csv(args.output_dir / "annotator-a-heuristic.csv", annotator_a_rows)
    write_csv(args.output_dir / "annotator-b-blank.csv", annotator_b_rows)
    empty_disagreement_headers = [
        "event_id",
        "field",
        "annotator_a",
        "annotator_b",
        "adjudication_status",
    ]
    empty_adjudication_headers = ["event_id", "field", "final_value", "adjudicator", "notes"]
    (args.output_dir / "annotation-disagreements.csv").write_text(
        ",".join(empty_disagreement_headers) + "\n",
        encoding="utf-8-sig",
    )
    (args.output_dir / "annotation-adjudication.csv").write_text(
        ",".join(empty_adjudication_headers) + "\n",
        encoding="utf-8-sig",
    )
    build_guidelines(args.output_dir / "annotation-guidelines.md")

    summary = {
        "created_at_utc": utc_now_iso(),
        "benchmark": str(args.benchmark_dir.relative_to(PROJECT_DIR)),
        "sample_seed": args.seed,
        "events": len(sample_rows),
        "by_type": {label: TYPE_TARGETS[key] for key, label in TYPE_LABEL.items()},
        "note": "Annotator B responses pending human fill; run compute_exp12_agreement after B is complete.",
    }
    write_summary_json(args.output_dir / "human-annotation-sample-summary.json", summary)
    write_manifest_csv(
        args.output_dir / "manifest.csv",
        [
            {"artifact": "human-audit-sample.csv", "rows": len(sample_rows), "frozen": True},
            {"artifact": "annotator-a-heuristic.csv", "rows": len(annotator_a_rows), "frozen": True},
            {"artifact": "annotator-b-blank.csv", "rows": len(annotator_b_rows), "frozen": True},
            {"artifact": "annotation-guidelines.md", "rows": 1, "frozen": True},
        ],
    )
    write_binding(
        args.output_dir,
        experiment_role="exp12-human-annotation-sample",
        script_path=Path(__file__),
        benchmark_manifest=benchmark_manifest,
        benchmark_sha256=benchmark_sha256,
        method_freeze_manifest=method_manifest,
        method_freeze_sha256=method_sha256,
        extra=summary,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
