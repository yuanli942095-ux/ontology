from __future__ import annotations

"""Generate a freeze manifest for semantic-v2 benchmark experiments.

The public manifest hashes all benchmark materials needed to reproduce a split
without exposing Oracle rows. A separate private manifest records Oracle
integrity hashes for local audit only; do not publish the private file.
"""

import argparse
import csv
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_semantic_benchmark_v2 import load_public_events
from semantic_v2_common import (
    BENCHMARK_DIR,
    BUILD_DIR,
    CANDIDATE_CSV,
    DOCUMENT_CSV,
    DOCUMENT_DIR,
    EVENT_CSV,
    ORACLE_CSV,
    OUTPUT_DIR,
    PROJECT_DIR,
    load_csv,
    sha256_file,
    sha256_text,
    write_csv,
)


RULE_DIR = BENCHMARK_DIR / "rules"

REPRODUCTION_SCRIPTS = [
    "src/run_candidate_information_ablation.py",
    "src/run_template_policy_hard_gate.py",
    "src/run_llm_extracted_facts_template_policy.py",
    "src/run_semantic_v2_repair_closure.py",
    "src/run_hard_gate_survivor_stress.py",
    "src/run_no_model_baselines_semantic_v2.py",
    "src/analyze_semantic_type_groups.py",
    "src/analyze_runtime_and_policy_costs.py",
    "src/analyze_failure_cases.py",
    "src/audit_formal_policy_source_costs.py",
    "src/validate_semantic_benchmark_v2.py",
    "src/build_semantic_benchmark_v2.py",
    "src/validate_formal_policy_gate.py",
    "src/semantic_v2_common.py",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成semantic-v2 freeze manifest")
    parser.add_argument("--split", choices=["dev", "test", "all"], default="test")
    parser.add_argument("--prefix", default="semantic-v2-freeze-manifest-test")
    parser.add_argument(
        "--include-built-candidate-owls",
        action="store_true",
        help="同时哈希built/candidate-owls目录，文件较多但更完整",
    )
    return parser.parse_args()


def relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_DIR))
    except ValueError:
        return str(path.resolve())


def file_row(path: Path, role: str, event_id: str = "", privacy: str = "public") -> dict[str, Any]:
    stat = path.stat()
    return {
        "role": role,
        "event_id": event_id,
        "privacy": privacy,
        "path": relpath(path),
        "sha256": sha256_file(path),
        "bytes": stat.st_size,
        "mtime_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def canonical_hash(value: Any) -> str:
    return sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def git_state() -> dict[str, Any]:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_DIR,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        status = subprocess.check_output(
            ["git", "status", "--short"],
            cwd=PROJECT_DIR,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return {
            "available": True,
            "commit": commit,
            "dirty": bool(status.strip()),
            "status_short": status.splitlines(),
        }
    except Exception:
        return {
            "available": False,
            "commit": "",
            "dirty": None,
            "status_short": [],
            "note": "No git repository was available from PROJECT_DIR.",
        }


def candidate_input_hashes(event_ids: set[str]) -> dict[str, str]:
    rows = [row for row in load_csv(CANDIDATE_CSV) if row["event_id"] in event_ids]
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["event_id"], []).append(row)
    return {
        event_id: canonical_hash(sorted(items, key=lambda row: row["candidate_id"]))
        for event_id, items in grouped.items()
    }


def oracle_private_hashes(event_ids: set[str]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    rows = [
        row
        for row in load_csv(ORACLE_CSV)
        if row.get("event_id") in event_ids and row.get("status", "").strip().upper() == "READY"
    ]
    private_rows = [
        {
            "role": "oracle_row",
            "event_id": row["event_id"],
            "privacy": "private",
            "path": relpath(ORACLE_CSV),
            "sha256": canonical_hash(row),
            "bytes": "",
            "mtime_utc": "",
        }
        for row in sorted(rows, key=lambda item: item["event_id"])
    ]
    return private_rows, {row["event_id"]: canonical_hash(row) for row in rows}


def main() -> int:
    args = parse_args()
    events = load_public_events(args.split)
    event_ids = {str(event["event_id"]) for event in events}
    generated_at = datetime.now(timezone.utc).isoformat()

    public_file_rows: list[dict[str, Any]] = []
    private_file_rows: list[dict[str, Any]] = []
    for path, role in [
        (EVENT_CSV, "input_events_csv"),
        (DOCUMENT_CSV, "input_documents_csv"),
        (CANDIDATE_CSV, "input_candidates_csv"),
        (BUILD_DIR / "semantic-events.json", "built_public_events_json"),
        (BUILD_DIR / "candidate-effects.csv", "built_candidate_effects_csv"),
        (BUILD_DIR / "candidate-effects.json", "built_candidate_effects_json"),
        (BUILD_DIR / "benchmark-manifest.json", "built_public_manifest_json"),
    ]:
        if path.is_file():
            public_file_rows.append(file_row(path, role))

    if ORACLE_CSV.is_file():
        private_file_rows.append(file_row(ORACLE_CSV, "private_oracle_csv", privacy="private"))

    seen_paths = {row["path"] for row in public_file_rows}
    for script in REPRODUCTION_SCRIPTS:
        path = PROJECT_DIR / script
        if path.is_file() and relpath(path) not in seen_paths:
            public_file_rows.append(file_row(path, "reproduction_script"))
            seen_paths.add(relpath(path))

    per_event: list[dict[str, Any]] = []
    candidate_hashes = candidate_input_hashes(event_ids)
    private_oracle_rows, oracle_hashes = oracle_private_hashes(event_ids)
    private_file_rows.extend(private_oracle_rows)

    for event in sorted(events, key=lambda item: str(item["event_id"])):
        event_id = str(event["event_id"])
        doc_paths: list[str] = []
        doc_hashes: dict[str, str] = {}
        for document in event.get("documents", []):
            path = DOCUMENT_DIR / document["file_name"]
            if path.is_file():
                public_file_rows.append(file_row(path, "event_document", event_id=event_id))
                doc_paths.append(relpath(path))
                doc_hashes[str(document["document_id"])] = sha256_file(path)

        rule_path = RULE_DIR / f"{event_id}-formal-policy.json"
        rule_hash = ""
        if rule_path.is_file():
            public_file_rows.append(file_row(rule_path, "formal_policy_rule", event_id=event_id))
            rule_hash = sha256_file(rule_path)

        mutant_path = PROJECT_DIR / str(event.get("source_owl", ""))
        mutant_hash = ""
        if mutant_path.is_file():
            public_file_rows.append(file_row(mutant_path, "mutant_owl", event_id=event_id))
            mutant_hash = sha256_file(mutant_path)

        if args.include_built_candidate_owls:
            candidate_dir = BUILD_DIR / "candidate-owls" / event_id
            for path in sorted(candidate_dir.glob("*.owl")):
                public_file_rows.append(file_row(path, "built_candidate_owl", event_id=event_id))

        per_event.append(
            {
                "event_id": event_id,
                "split": event["split"],
                "semantic_type": event["semantic_type"],
                "public_event_payload_hash": canonical_hash(event),
                "input_candidate_rows_hash": candidate_hashes.get(event_id, ""),
                "formal_policy_rule_hash": rule_hash,
                "mutant_owl_hash": mutant_hash,
                "document_hashes": doc_hashes,
                "document_paths": doc_paths,
                "private_oracle_row_hash_available": event_id in oracle_hashes,
            }
        )

    unique_public_rows = {
        (row["role"], row["event_id"], row["path"]): row
        for row in public_file_rows
    }
    public_file_rows = sorted(
        unique_public_rows.values(),
        key=lambda row: (row["role"], row["event_id"], row["path"]),
    )
    private_file_rows = sorted(
        private_file_rows,
        key=lambda row: (row["role"], row["event_id"], row["path"]),
    )

    semantic_counts = Counter(str(event["semantic_type"]) for event in events)
    payload = {
        "schema_version": "1.0",
        "generated_at_utc": generated_at,
        "benchmark_dir": relpath(BENCHMARK_DIR),
        "split": args.split,
        "event_count": len(events),
        "event_ids": sorted(event_ids),
        "semantic_type_counts": dict(sorted(semantic_counts.items())),
        "public_file_count": len(public_file_rows),
        "private_oracle_integrity_manifest_generated": True,
        "oracle_used_for_candidate_filtering": False,
        "git": git_state(),
        "limitations": [
            "This manifest records the file state at generation time; it cannot prove that files were frozen before earlier experiments unless paired with external VCS or archive timestamps.",
            "The private Oracle manifest is for local audit only and should not be published with the paper artifact.",
        ],
        "per_event": per_event,
        "public_files": public_file_rows,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUTPUT_DIR / f"{args.prefix}.json"
    public_csv = OUTPUT_DIR / f"{args.prefix}-files.csv"
    private_csv = OUTPUT_DIR / f"{args.prefix}-private-oracle.csv"
    log_path = OUTPUT_DIR / f"{args.prefix}.log"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(public_csv, public_file_rows)
    write_csv(private_csv, private_file_rows)

    lines = [
        "semantic-v2 freeze manifest",
        f"generated_at_utc={generated_at}",
        f"split={args.split}",
        f"events={len(events)}",
        f"semantic_type_counts={dict(sorted(semantic_counts.items()))}",
        f"public_files={len(public_file_rows)}",
        f"private_oracle_rows={len(private_file_rows)}",
        f"git_available={payload['git']['available']}",
        "limitation=manifest records current file state; use git/archive timestamps for pre-experiment freeze proof",
        "",
        f"json={json_path}",
        f"public_csv={public_csv}",
        f"private_oracle_csv={private_csv}",
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
