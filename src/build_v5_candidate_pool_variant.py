from __future__ import annotations

"""Build derived v5 candidate-pool benchmark variants (k=5 / k=10).

The frozen parent benchmark ``external-real-holdout-v5-blind-large`` is never modified.
Each variant is a full derived copy with additional hard-negative candidates sourced
only from the public v5 candidate value pool.
"""

import argparse
import copy
import csv
import hashlib
import json
import random
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from auto_policy_m12_decomposed_extraction import normalize_token
from holdout_metadata_enrichment import load_source_family_by_event, parse_display_value
from paper_final_validation_common import (
    DEFAULT_BENCHMARK,
    DEFAULT_BENCHMARK_FREEZE_SUMMARY,
    DEFAULT_METHOD_FREEZE_DIR,
    PAPER_VALIDATION_ROOT,
    benchmark_paths,
    load_freeze_sha256,
    load_method_freeze_sha256,
    read_csv,
    sha256_file,
    utc_now_iso,
    write_binding,
    write_manifest_csv,
    write_summary_json,
)
from semantic_v2_common import PROJECT_DIR


BASE_CANDIDATES = 3
ORACLE_LEAK_PATTERNS = (
    re.compile(r"\boracle\b", re.I),
    re.compile(r"\bgold\b", re.I),
    re.compile(r"\bcorrect answer\b", re.I),
)


@dataclass(frozen=True)
class Donor:
    donor_event_id: str
    display_value: str
    semantic_value: str
    source_family: str
    domain: str
    semantic_type: str
    predicate_label: str
    tier_hint: str


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def semantic_value(display_value: str) -> str:
    _, right = parse_display_value(display_value)
    return normalize_token(right)


def predicate_family(predicate_label: str) -> str:
    label = str(predicate_label or "").strip().lower()
    for suffix in (
        " versioned normative status",
        " rule exception or constraint",
        " cross-sentence scope attachment",
        " normative attribute",
    ):
        if label.endswith(suffix):
            return suffix.strip()
    return label


def candidate_id_for_index(index: int) -> str:
    return f"CAND_{index:03d}"


def load_events(event_csv: Path) -> dict[str, dict[str, str]]:
    return {row["event_id"]: row for row in read_csv(event_csv)}


def load_candidates_grouped(candidate_csv: Path) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_csv(candidate_csv):
        grouped[row["event_id"]].append(row)
    for event_id in grouped:
        grouped[event_id].sort(key=lambda item: item["candidate_id"])
    return grouped


def changing_candidate(row: dict[str, str]) -> bool:
    try:
        operation = json.loads(row["operation_json"])
    except json.JSONDecodeError:
        return False
    return operation.get("old_value") != operation.get("new_value")


def build_donor_pool(
    *,
    events: dict[str, dict[str, str]],
    families_by_event: dict[str, str],
    candidates_by_event: dict[str, list[dict[str, str]]],
) -> list[Donor]:
    donors: list[Donor] = []
    for event_id, event in events.items():
        family = families_by_event.get(event_id, event.get("domain", ""))
        for row in candidates_by_event[event_id]:
            if not changing_candidate(row):
                continue
            donors.append(
                Donor(
                    donor_event_id=event_id,
                    display_value=row["display_value"],
                    semantic_value=semantic_value(row["display_value"]),
                    source_family=family,
                    domain=event.get("domain", ""),
                    semantic_type=event.get("semantic_type", ""),
                    predicate_label=event.get("predicate_label", ""),
                    tier_hint="public_pool",
                )
            )
    return donors


def donor_tier(event: dict[str, str], family: str, donor: Donor) -> int:
    if donor.donor_event_id == event["event_id"]:
        return 99
    pred_family = predicate_family(event.get("predicate_label", ""))
    donor_pred_family = predicate_family(donor.predicate_label)
    if donor.source_family == family and donor.predicate_label == event.get("predicate_label", ""):
        return 1
    if donor.domain == event.get("domain", "") and donor.predicate_label == event.get("predicate_label", ""):
        return 2
    if donor.semantic_type == event.get("semantic_type", "") and donor_pred_family == pred_family:
        return 3
    if donor.domain == event.get("domain", ""):
        return 4
    if donor.semantic_type == event.get("semantic_type", ""):
        return 5
    return 6


def rank_donors(
    *,
    event: dict[str, str],
    family: str,
    donors: list[Donor],
    rng: random.Random,
) -> list[Donor]:
    scored = [(donor_tier(event, family, donor), donor) for donor in donors]
    scored.sort(key=lambda item: (item[0], item[1].donor_event_id, item[1].display_value))
    by_tier: dict[int, list[Donor]] = defaultdict(list)
    for tier, donor in scored:
        by_tier[tier].append(donor)
    ordered: list[Donor] = []
    for tier in sorted(by_tier):
        tier_rows = by_tier[tier]
        rng.shuffle(tier_rows)
        ordered.extend(tier_rows)
    return ordered


def adapt_operation(template_operation: dict[str, Any], display_value: str) -> dict[str, Any]:
    operation = copy.deepcopy(template_operation)
    operation["new_value"] = copy.deepcopy(operation["old_value"])
    operation["new_value"]["lexical"] = display_value
    if operation["new_value"].get("kind") == "literal":
        operation["new_value"]["datatype"] = "http://www.w3.org/2001/XMLSchema#string"
    return operation


def make_negative_row(
    *,
    event_id: str,
    candidate_id: str,
    template_row: dict[str, str],
    donor: Donor,
) -> dict[str, str]:
    target_family, _ = parse_display_value(template_row["display_value"])
    _, donor_value = parse_display_value(donor.display_value)
    display_value = f"{target_family}={donor_value}"
    operation = adapt_operation(json.loads(template_row["operation_json"]), display_value)
    return {
        "event_id": event_id,
        "candidate_id": candidate_id,
        "display_value": display_value,
        "operation_json": json.dumps(operation, ensure_ascii=False),
        "status": "READY",
        "notes": (
            "Derived hard negative from public v5 value pool; "
            f"donor_event={donor.donor_event_id}; tier={donor.tier_hint}."
        ),
    }


def existing_semantic_keys(rows: list[dict[str, str]]) -> set[str]:
    return {semantic_value(row["display_value"]) for row in rows}


def expand_event_candidates(
    *,
    event: dict[str, str],
    family: str,
    base_rows: list[dict[str, str]],
    donors: list[Donor],
    pool_size: int,
    seed: int,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    if len(base_rows) != BASE_CANDIDATES:
        raise RuntimeError(f"{event['event_id']} expected {BASE_CANDIDATES} base candidates, got {len(base_rows)}")
    needed = pool_size - BASE_CANDIDATES
    if needed <= 0:
        return base_rows, []
    template_row = next(row for row in base_rows if row["candidate_id"] == "CAND_001")
    used_keys = existing_semantic_keys(base_rows)
    rng = random.Random(f"pool-{pool_size}|{seed}|{event['event_id']}")
    audit_rows: list[dict[str, Any]] = []
    negatives: list[dict[str, str]] = []
    for donor in rank_donors(event=event, family=family, donors=donors, rng=rng):
        if len(negatives) >= needed:
            break
        if donor.semantic_value in used_keys:
            continue
        candidate_id = candidate_id_for_index(BASE_CANDIDATES + len(negatives) + 1)
        row = make_negative_row(
            event_id=event["event_id"],
            candidate_id=candidate_id,
            template_row=template_row,
            donor=donor,
        )
        negatives.append(row)
        used_keys.add(donor.semantic_value)
        audit_rows.append(
            {
                "event_id": event["event_id"],
                "candidate_id": candidate_id,
                "donor_event_id": donor.donor_event_id,
                "donor_display_value": donor.display_value,
                "tier": donor_tier(event, family, donor),
                "semantic_value": donor.semantic_value,
            }
        )
    if len(negatives) < needed:
        raise RuntimeError(
            f"{event['event_id']} could only add {len(negatives)} negatives; needed {needed}"
        )
    return base_rows + negatives, audit_rows


def copy_benchmark(source: Path, target: Path, force: bool) -> None:
    if target.exists():
        if not force:
            raise SystemExit(f"target exists; pass --force to overwrite: {target}")
        shutil.rmtree(target)
    shutil.copytree(source, target)


def update_readme(target: Path, pool_size: int, parent_sha256: str) -> None:
    readme = target / "README.md"
    existing = readme.read_text(encoding="utf-8", errors="replace") if readme.is_file() else ""
    addition = (
        "\n\n## Derived Candidate-Pool Variant\n\n"
        f"- Pool size: `{pool_size}` candidates per event\n"
        f"- Parent benchmark: `external-real-holdout-v5-blind-large`\n"
        f"- Parent freeze SHA-256: `{parent_sha256}`\n"
        f"- Created at UTC: `{utc_now_iso()}`\n"
        "- Oracle mapping is unchanged from parent.\n"
        "- Additional candidates are hard negatives from the public v5 value pool only.\n"
        "- Do not use this derived variant for method tuning.\n"
    )
    readme.write_text(existing.rstrip() + addition + "\n", encoding="utf-8")


def audit_variant(
    *,
    target: Path,
    parent_oracle_csv: Path,
    pool_size: int,
) -> dict[str, Any]:
    candidate_csv = target / "repair-stage" / "candidates" / "external-real-candidate-template.csv"
    oracle_csv = target / "private" / "oracle" / "external-real-oracle-template.csv"
    parent_oracles = {row["event_id"]: row for row in read_csv(parent_oracle_csv)}
    variant_oracles = {row["event_id"]: row for row in read_csv(oracle_csv)}
    candidates = read_csv(candidate_csv)
    grouped = load_candidates_grouped(candidate_csv)

    oracle_preserved = 0
    oracle_missing = 0
    oracle_changed = 0
    duplicate_display = 0
    duplicate_semantic = 0
    leakage_flags = 0
    per_event_counts: dict[str, int] = {}

    for event_id, parent in parent_oracles.items():
        variant = variant_oracles.get(event_id)
        if variant is None:
            oracle_missing += 1
            continue
        if (
            variant["oracle_candidate_id"] != parent["oracle_candidate_id"]
            or variant["oracle_value"] != parent["oracle_value"]
        ):
            oracle_changed += 1
            continue
        event_rows = grouped[event_id]
        per_event_counts[event_id] = len(event_rows)
        if len(event_rows) != pool_size:
            oracle_missing += 1
            continue
        ids = {row["candidate_id"] for row in event_rows}
        if parent["oracle_candidate_id"] not in ids:
            oracle_missing += 1
            continue
        oracle_preserved += 1

        seen_display: set[str] = set()
        seen_semantic: set[str] = set()
        for row in event_rows:
            if row["display_value"] in seen_display:
                duplicate_display += 1
            seen_display.add(row["display_value"])
            sem = semantic_value(row["display_value"])
            if sem in seen_semantic:
                duplicate_semantic += 1
            seen_semantic.add(sem)
            note = str(row.get("notes", ""))
            if any(pattern.search(note) for pattern in ORACLE_LEAK_PATTERNS):
                leakage_flags += 1

    return {
        "events": len(parent_oracles),
        "candidate_rows": len(candidates),
        "expected_candidates_per_event": pool_size,
        "oracle_preservation_pass": oracle_preserved,
        "oracle_preservation_fail": oracle_missing + oracle_changed,
        "oracle_changed_rows": oracle_changed,
        "duplicate_display_values": duplicate_display,
        "duplicate_semantic_values": duplicate_semantic,
        "leakage_note_flags": leakage_flags,
        "per_event_candidate_count_unique": sorted(set(per_event_counts.values())),
        "audit_pass": (
            oracle_preserved == len(parent_oracles)
            and duplicate_display == 0
            and duplicate_semantic == 0
            and leakage_flags == 0
            and per_event_counts
            and all(count == pool_size for count in per_event_counts.values())
        ),
    }


def manifest_files(target: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in sorted(target.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(PROJECT_DIR))
        rows.append({"path": rel, "sha256": sha256_file(path)})
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool-size", type=int, required=True, choices=(5, 10))
    parser.add_argument("--source", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--seed", type=int, default=20260831)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--output-benchmark-dir",
        type=Path,
        default=None,
        help="Defaults to benchmark/external-real-holdout-v5-blind-large-pool-k{size}",
    )
    parser.add_argument(
        "--validation-output-dir",
        type=Path,
        default=None,
        help="Defaults to output/paper-final-validation/05-candidate-pool/k{size}",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.pool_size <= BASE_CANDIDATES:
        raise SystemExit(f"--pool-size must be > {BASE_CANDIDATES}")

    source = args.source.resolve()
    target = (args.output_benchmark_dir or (PROJECT_DIR / "benchmark" / f"external-real-holdout-v5-blind-large-pool-k{args.pool_size}")).resolve()
    validation_dir = (
        args.validation_output_dir or (PAPER_VALIDATION_ROOT / "05-candidate-pool" / f"k{args.pool_size}")
    ).resolve()
    validation_dir.mkdir(parents=True, exist_ok=True)

    parent_sha256, parent_manifest = load_freeze_sha256(DEFAULT_BENCHMARK_FREEZE_SUMMARY)
    method_freeze_sha256, method_freeze_manifest = load_method_freeze_sha256(DEFAULT_METHOD_FREEZE_DIR)
    paths = benchmark_paths(source)
    events = load_events(paths["event_csv"])
    families_by_event = load_source_family_by_event(source)
    base_candidates = load_candidates_grouped(paths["candidate_csv"])
    donors = build_donor_pool(
        events=events,
        families_by_event=families_by_event,
        candidates_by_event=base_candidates,
    )

    copy_benchmark(source, target, args.force)
    expanded_rows: list[dict[str, str]] = []
    donor_audit: list[dict[str, Any]] = []
    for event_id in sorted(events):
        event = events[event_id]
        rows, audit = expand_event_candidates(
            event=event,
            family=families_by_event.get(event_id, event.get("domain", "")),
            base_rows=copy.deepcopy(base_candidates[event_id]),
            donors=donors,
            pool_size=args.pool_size,
            seed=args.seed,
        )
        expanded_rows.extend(rows)
        donor_audit.extend(audit)

    candidate_csv = target / "repair-stage" / "candidates" / "external-real-candidate-template.csv"
    fieldnames = list(expanded_rows[0].keys())
    write_csv(candidate_csv, expanded_rows, fieldnames)

    update_readme(target, args.pool_size, parent_sha256)
    audit = audit_variant(
        target=target,
        parent_oracle_csv=paths["oracle_csv"],
        pool_size=args.pool_size,
    )
    manifest_rows = manifest_files(target)
    variant_manifest_path = validation_dir / "variant-manifest.csv"
    write_manifest_csv(variant_manifest_path, manifest_rows)

    per_event_manifest = [
        {
            "event_id": event_id,
            "pool_size": args.pool_size,
            "base_candidates": BASE_CANDIDATES,
            "added_candidates": args.pool_size - BASE_CANDIDATES,
            "candidate_count": args.pool_size,
        }
        for event_id in sorted(events)
    ]
    write_manifest_csv(validation_dir / "manifest.csv", per_event_manifest)
    write_manifest_csv(validation_dir / "donor-audit.csv", donor_audit)

    summary = {
        "generated_at_utc": utc_now_iso(),
        "status": "COMPLETE" if audit["audit_pass"] else "AUDIT_FAILED",
        "pool_size": args.pool_size,
        "events": len(events),
        "candidate_rows": len(expanded_rows),
        "parent_benchmark": str(source.relative_to(PROJECT_DIR)),
        "parent_benchmark_sha256": parent_sha256,
        "derived_benchmark": str(target.relative_to(PROJECT_DIR)),
        "derived_variant": True,
        "benchmark_changed": False,
        "method_freeze_sha256": method_freeze_sha256,
        "seed": args.seed,
        "audit": audit,
        "tier_distribution": dict(
            sorted(
                {
                    str(row["tier"]): sum(1 for item in donor_audit if str(item["tier"]) == str(row["tier"]))
                    for row in donor_audit
                }.items()
            )
        ),
    }
    write_summary_json(validation_dir / "summary.json", summary)
    write_binding(
        validation_dir,
        experiment_role=f"05-candidate-pool-k{args.pool_size}",
        script_path=Path(__file__),
        benchmark_manifest=parent_manifest,
        benchmark_sha256=parent_sha256,
        method_freeze_manifest=method_freeze_manifest,
        method_freeze_sha256=method_freeze_sha256,
        extra={
            "derived_variant": True,
            "derived_benchmark": str(target.relative_to(PROJECT_DIR)),
            "pool_size": args.pool_size,
            "oracle_used_for_inference": False,
            "input_prediction_dir": "",
            "llm_backend": "",
            "model": "",
        },
    )
    print(
        f"[pool-variant] k={args.pool_size} events={len(events)} rows={len(expanded_rows)} "
        f"audit_pass={audit['audit_pass']} target={target}"
    )
    return 0 if audit["audit_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
