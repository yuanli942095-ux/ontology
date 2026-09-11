from __future__ import annotations

"""Validate the RFC-213 confirmatory core and its privacy boundaries."""

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from build_rfc213_confirmatory_core import (
    CANDIDATE_REL,
    CORE_DIR,
    CORE_NAME,
    DOCUMENT_REL,
    EVENT_REL,
    EXPECTED_PARENT_MANIFEST_SHA256,
    ORACLE_REL,
    OUTPUT_DIR,
    RETRIEVAL_REL,
    SEMANTIC_TYPES,
    TARGET_CANDIDATES,
    TARGET_EVENTS,
    read_csv,
    sha256_file,
    write_csv,
    write_json,
)


def add_check(
    checks: list[dict[str, Any]],
    code: str,
    passed: bool,
    detail: Any,
) -> None:
    checks.append(
        {
            "code": code,
            "status": "PASS" if passed else "FAIL",
            "detail": detail,
        }
    )


def validate_core(
    benchmark_dir: Path = CORE_DIR,
    output_dir: Path = OUTPUT_DIR,
    *,
    write_outputs: bool = True,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    required = [
        benchmark_dir / EVENT_REL,
        benchmark_dir / DOCUMENT_REL,
        benchmark_dir / CANDIDATE_REL,
        benchmark_dir / ORACLE_REL,
        benchmark_dir / RETRIEVAL_REL,
        benchmark_dir / "public/cq/cq-template.csv",
        benchmark_dir / "private/cq/cq-gold-answers.csv",
        benchmark_dir
        / "private/construct-audit/rfc213-gold-repair-construct-audit.csv",
        benchmark_dir / "private/construction/rfc264-to-rfc213-lineage.csv",
        benchmark_dir / "private/annotation/no-drift-schema-template.csv",
        benchmark_dir / "private/annotation/longitudinal-chain-schema-template.csv",
        output_dir / "rfc264-to-rfc213-lineage.csv",
        output_dir / "rfc213-gold-repair-construct-audit.csv",
        output_dir / "gamma-supported-fragment-manifest.json",
        output_dir / "rfc213-exclusion-protocol.json",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    add_check(checks, "REQUIRED_FILES", not missing, {"missing": missing})
    if missing:
        result = {
            "benchmark": CORE_NAME,
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "error_count": 1,
            "checks": checks,
        }
        if write_outputs:
            output_dir.mkdir(parents=True, exist_ok=True)
            write_json(output_dir / "rfc213-audit.json", result)
            write_csv(output_dir / "rfc213-audit-findings.csv", checks)
        return result

    events = read_csv(benchmark_dir / EVENT_REL)
    documents = read_csv(benchmark_dir / DOCUMENT_REL)
    candidates = read_csv(benchmark_dir / CANDIDATE_REL)
    oracles = read_csv(benchmark_dir / ORACLE_REL)
    retrievals = read_csv(benchmark_dir / RETRIEVAL_REL)
    lineage = read_csv(output_dir / "rfc264-to-rfc213-lineage.csv")
    constructs = read_csv(output_dir / "rfc213-gold-repair-construct-audit.csv")
    public_cq = read_csv(benchmark_dir / "public/cq/cq-template.csv")
    private_cq = read_csv(benchmark_dir / "private/cq/cq-gold-answers.csv")

    event_ids = {row["event_id"] for row in events}
    add_check(
        checks,
        "EVENT_COUNT",
        len(events) == TARGET_EVENTS and len(event_ids) == TARGET_EVENTS,
        {"rows": len(events), "unique_ids": len(event_ids)},
    )
    add_check(
        checks,
        "CANDIDATE_COUNT",
        len(candidates) == TARGET_CANDIDATES,
        {"rows": len(candidates)},
    )
    candidates_by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in candidates:
        candidates_by_event[row["event_id"]].append(row)
    bad_candidate_counts = {
        event_id: len(candidates_by_event[event_id])
        for event_id in event_ids
        if len(candidates_by_event[event_id]) != 3
    }
    add_check(
        checks,
        "THREE_CANDIDATES_PER_EVENT",
        not bad_candidate_counts,
        bad_candidate_counts,
    )

    type_counts = Counter(row["semantic_type"] for row in events)
    domain_counts = Counter(row["domain"] for row in events)
    add_check(
        checks,
        "SEMANTIC_TYPE_BALANCE",
        all(type_counts[item] == 71 for item in SEMANTIC_TYPES),
        dict(sorted(type_counts.items())),
    )
    add_check(
        checks,
        "DOMAIN_BALANCE",
        len(domain_counts) == 11
        and min(domain_counts.values()) == 19
        and max(domain_counts.values()) == 20,
        dict(sorted(domain_counts.items())),
    )

    included = [row for row in lineage if row["included_in_rfc213"] == "TRUE"]
    excluded = [row for row in lineage if row["included_in_rfc213"] == "FALSE"]
    lineage_ids = {row["event_id"] for row in lineage}
    add_check(
        checks,
        "LINEAGE_COUNTS",
        len(lineage) == 264 and len(included) == 213 and len(excluded) == 51,
        {
            "rows": len(lineage),
            "included": len(included),
            "excluded": len(excluded),
        },
    )
    add_check(
        checks,
        "LINEAGE_EVENT_MATCH",
        {row["event_id"] for row in included} == event_ids
        and len(lineage_ids) == 264,
        {
            "included_match": {row["event_id"] for row in included} == event_ids,
            "unique_lineage_ids": len(lineage_ids),
        },
    )
    invalid_exclusions = [
        row["event_id"]
        for row in excluded
        if row["exclusion_code"] != "E05_TEMPLATE_NEAR_DUPLICATE"
        or row["decided_before_model_eval"] != "TRUE"
        or not row["exclusion_reason"].strip()
    ]
    add_check(
        checks,
        "EXCLUSION_PROTOCOL_FIELDS",
        not invalid_exclusions,
        {"invalid_event_ids": invalid_exclusions},
    )
    parent_hashes = {row["parent_manifest_sha256"] for row in lineage}
    add_check(
        checks,
        "PARENT_MANIFEST_BINDING",
        parent_hashes == {EXPECTED_PARENT_MANIFEST_SHA256},
        {"values": sorted(parent_hashes)},
    )
    private_lineage = (
        benchmark_dir / "private/construction/rfc264-to-rfc213-lineage.csv"
    )
    private_lineage_sha256 = sha256_file(private_lineage)
    output_lineage_sha256 = sha256_file(
        output_dir / "rfc264-to-rfc213-lineage.csv"
    )
    add_check(
        checks,
        "LINEAGE_MIRROR",
        private_lineage_sha256 == output_lineage_sha256,
        {
            "private_sha256": private_lineage_sha256,
            "output_sha256": output_lineage_sha256,
        },
    )

    document_ids = {row["event_id"] for row in documents}
    retrieval_ids = {row["event_id"] for row in retrievals}
    oracle_ids = {row["event_id"] for row in oracles}
    construct_ids = {row["event_id"] for row in constructs}
    add_check(
        checks,
        "CROSS_FILE_EVENT_LINKS",
        all(
            ids == event_ids
            for ids in (document_ids, retrieval_ids, oracle_ids, construct_ids)
        ),
        {
            "documents": len(document_ids),
            "retrievals": len(retrieval_ids),
            "oracles": len(oracle_ids),
            "constructs": len(construct_ids),
        },
    )
    construct_supported = {
        row["event_id"]: row["supported_by_gamma_mvp"] == "TRUE"
        for row in constructs
    }
    missing_payload_files: list[str] = []
    for row in events:
        event_id = row["event_id"]
        expected = [
            benchmark_dir / "public/excerpts" / f"{event_id}-evidence.md",
            benchmark_dir / "repair-stage/mutants" / f"{event_id}.owl",
        ]
        if construct_supported.get(event_id, False):
            expected.append(
                benchmark_dir / "private/ontology-after" / f"{event_id}-gold.owl"
            )
        missing_payload_files.extend(str(path) for path in expected if not path.is_file())
    add_check(
        checks,
        "EVENT_PAYLOAD_FILES",
        not missing_payload_files,
        {"missing": missing_payload_files},
    )

    oracle_by_event = {row["event_id"]: row for row in oracles}
    oracle_mismatches: list[str] = []
    for event_id, rows in candidates_by_event.items():
        oracle = oracle_by_event.get(event_id, {})
        gold = next(
            (
                row
                for row in rows
                if row["candidate_id"] == oracle.get("oracle_candidate_id")
            ),
            None,
        )
        if gold is None or gold["display_value"] != oracle.get("oracle_value"):
            oracle_mismatches.append(event_id)
    gold_slots = Counter(row["oracle_candidate_id"] for row in oracles)
    add_check(
        checks,
        "ORACLE_CANDIDATE_LINKS",
        not oracle_mismatches,
        {"mismatches": oracle_mismatches},
    )
    add_check(
        checks,
        "CANDIDATE_ID_DEBIASED",
        gold_slots
        == {"CAND_001": 71, "CAND_002": 71, "CAND_003": 71},
        dict(sorted(gold_slots.items())),
    )
    differentiated_notes = [
        event_id
        for event_id, rows in candidates_by_event.items()
        if len({row.get("notes", "") for row in rows}) > 1
    ]
    add_check(
        checks,
        "CANDIDATE_NOTES_UNIFORM",
        not differentiated_notes,
        {"events_with_differentiated_notes": differentiated_notes},
    )

    construct_counts = Counter(row["construct_type"] for row in constructs)
    unsupported = [
        row["event_id"]
        for row in constructs
        if row["supported_by_gamma_mvp"] != "TRUE"
    ]
    add_check(
        checks,
        "GOLD_CONSTRUCT_AUDIT",
        construct_counts == {"UPDATE_LITERAL": TARGET_EVENTS} and not unsupported,
        {
            "construct_counts": dict(construct_counts),
            "unsupported_event_ids": unsupported,
        },
    )
    gamma = json.loads(
        (output_dir / "gamma-supported-fragment-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    add_check(
        checks,
        "GAMMA_FRAGMENT_POLICY",
        gamma.get("supported_constructs") == ["UPDATE_LITERAL"]
        and gamma.get("coverage_denominator") == TARGET_EVENTS
        and gamma.get("unsupported_policy")
        == "return UNSUPPORTED and count in coverage denominator"
        and gamma.get("llm_allowed") is False
        and gamma.get("retrieval_allowed") is False
        and gamma.get("sampling_allowed") is False,
        {
            "supported_constructs": gamma.get("supported_constructs"),
            "coverage_denominator": gamma.get("coverage_denominator"),
        },
    )

    public_cq_ids = {row["cq_id"] for row in public_cq}
    private_cq_ids = {row["cq_id"] for row in private_cq}
    public_headers = set(public_cq[0]) if public_cq else set()
    cq_counts = Counter(row["event_id"] for row in public_cq)
    expected_values = {
        row.get("expected_answer_private", "") for row in private_cq if row.get("expected_answer_private")
    }
    public_cq_text = "\n".join(
        value
        for row in public_cq
        for value in row.values()
        if isinstance(value, str)
    )
    leaked_values = [
        value
        for value in expected_values
        if value not in {"TRUE", "FALSE"} and value in public_cq_text
    ]
    add_check(
        checks,
        "CQ_COUNTS_AND_LINKS",
        len(public_cq) == 426
        and len(private_cq) == 426
        and public_cq_ids == private_cq_ids
        and all(cq_counts[event_id] == 2 for event_id in event_ids),
        {
            "public_rows": len(public_cq),
            "private_rows": len(private_cq),
            "id_sets_match": public_cq_ids == private_cq_ids,
        },
    )
    add_check(
        checks,
        "CQ_PUBLIC_PRIVATE_ISOLATION",
        "expected_answer_private" not in public_headers and not leaked_values,
        {
            "public_has_answer_column": "expected_answer_private" in public_headers,
            "leaked_values": leaked_values[:10],
        },
    )

    event_required_fields = {
        "version_chain_id",
        "source_version",
        "target_version",
        "effective_date",
        "evidence_provenance",
        "semantic_ir_id",
        "candidate_set_id",
        "ontology_before",
        "ontology_after",
        "cq_ids",
        "split",
        "event_kind",
        "expected_decision",
    }
    event_headers = set(events[0]) if events else set()
    invalid_event_metadata = [
        row["event_id"]
        for row in events
        if row.get("split") != "confirmatory_core"
        or row.get("event_kind") != "DRIFT_REPAIR"
        or row.get("expected_decision") != "REPAIR"
        or row.get("ontology_after") != "PRIVATE"
        or not row.get("target_version")
        or not row.get("evidence_provenance")
        or len(row.get("cq_ids", "").split("|")) != 2
    ]
    leaking_event_notes = [
        row["event_id"]
        for row in events
        if any(
            marker in row.get("notes", "").lower()
            for marker in ("oracle", "gold", "candidate")
        )
    ]
    add_check(
        checks,
        "EVENT_METADATA_FIELDS",
        event_required_fields <= event_headers
        and not invalid_event_metadata
        and not leaking_event_notes,
        {
            "missing_headers": sorted(event_required_fields - event_headers),
            "invalid_event_ids": invalid_event_metadata,
            "leaking_event_note_ids": leaking_event_notes,
        },
    )

    public_files = [
        path
        for path in (benchmark_dir / "public").rglob("*")
        if path.is_file()
    ]
    forbidden_markers = ("oracle_candidate_id", "expected_answer_private")
    public_leaks: list[str] = []
    for path in public_files:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if any(marker in text for marker in forbidden_markers):
            public_leaks.append(str(path))
    add_check(
        checks,
        "PUBLIC_GOLD_LEAKAGE",
        not public_leaks,
        {"files": public_leaks},
    )

    error_count = sum(check["status"] == "FAIL" for check in checks)
    result = {
        "benchmark": CORE_NAME,
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "error_count": error_count,
        "warning_count": 0,
        "counts": {
            "events": len(events),
            "candidates": len(candidates),
            "lineage": len(lineage),
            "constructs": len(constructs),
            "public_cq": len(public_cq),
            "private_cq": len(private_cq),
        },
        "checks": checks,
    }
    if write_outputs:
        output_dir.mkdir(parents=True, exist_ok=True)
        write_json(output_dir / "rfc213-audit.json", result)
        write_csv(output_dir / "rfc213-audit-findings.csv", checks)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=CORE_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = validate_core(
        args.benchmark_dir.resolve(),
        args.output_dir.resolve(),
        write_outputs=True,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["error_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
