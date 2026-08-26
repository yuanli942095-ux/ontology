from __future__ import annotations

"""Generate a freeze manifest for external-real-v1.

The public manifest hashes public templates, source excerpts, candidate inputs,
rules, mutants, and reproduction scripts. A separate private integrity file
hashes the Oracle template/rows for local audit only. Do not publish the private
integrity CSV if Oracle rows have been filled.
"""

import argparse
import csv
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semantic_v2_common import OUTPUT_DIR, PROJECT_DIR, sha256_file, sha256_text, write_csv


BENCHMARK_DIR = PROJECT_DIR / "benchmark" / "external-real-v1"
INPUT_DIR = BENCHMARK_DIR / "input"
PRIVATE_DIR = BENCHMARK_DIR / "private"
DOCUMENT_DIR = BENCHMARK_DIR / "documents"
MUTANT_DIR = BENCHMARK_DIR / "mutants"
RULE_DIR = BENCHMARK_DIR / "rules"
BUILT_DIR = BENCHMARK_DIR / "built"
SOURCE_INTAKE_DIR = BENCHMARK_DIR / "source-intake"

EVENT_CSV = INPUT_DIR / "external-real-event-template.csv"
DOCUMENT_CSV = INPUT_DIR / "external-real-document-template.csv"
CANDIDATE_CSV = INPUT_DIR / "external-real-candidate-template.csv"
ORACLE_CSV = PRIVATE_DIR / "external-real-oracle-template.csv"

PUBLIC_FILES = [
    (BENCHMARK_DIR / "README.md", "benchmark_readme"),
    (BENCHMARK_DIR / "protocol.md", "collection_protocol"),
    (SOURCE_INTAKE_DIR / "README.md", "source_intake_readme"),
    (SOURCE_INTAKE_DIR / "external-real-source-intake.csv", "source_intake_csv"),
    (EVENT_CSV, "input_events_csv"),
    (DOCUMENT_CSV, "input_documents_csv"),
    (CANDIDATE_CSV, "input_candidates_csv"),
]

REPRODUCTION_SCRIPTS = [
    "src/validate_external_real_v1.py",
    "src/update_external_real_v1_document_hashes.py",
    "src/generate_external_real_v1_freeze_manifest.py",
    "src/semantic_v2_common.py",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="generate external-real-v1 freeze manifest")
    parser.add_argument("--prefix", default="external-real-v1-freeze-manifest")
    parser.add_argument(
        "--include-built",
        action="store_true",
        help="also hash files under benchmark/external-real-v1/built",
    )
    return parser.parse_args()


def relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_DIR))
    except ValueError:
        return str(path.resolve())


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def canonical_hash(value: Any) -> str:
    return sha256_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def file_row(path: Path, role: str, privacy: str = "public", event_id: str = "") -> dict[str, Any]:
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
    except Exception as exc:
        return {
            "available": False,
            "commit": "",
            "dirty": None,
            "status_short": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


def directory_file_rows(base: Path, role: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not base.is_dir():
        return rows
    for path in sorted(item for item in base.rglob("*") if item.is_file()):
        if path.name == ".gitkeep":
            continue
        event_id = path.parent.name if path.parent != base else ""
        rows.append(file_row(path, role, event_id=event_id))
    return rows


def row_hashes(rows: list[dict[str, str]], role: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=2):
        event_id = str(row.get("event_id", "")).strip()
        result.append(
            {
                "role": role,
                "event_id": event_id,
                "privacy": "private" if role.startswith("oracle") else "public",
                "path": "",
                "sha256": canonical_hash(row),
                "bytes": "",
                "mtime_utc": "",
                "row_index": index,
            }
        )
    return result


def main() -> int:
    args = parse_args()
    generated_at = datetime.now(timezone.utc).isoformat()

    event_rows = read_csv(EVENT_CSV)
    document_rows = read_csv(DOCUMENT_CSV)
    candidate_rows = read_csv(CANDIDATE_CSV)
    oracle_rows = read_csv(ORACLE_CSV)

    ready_events = [
        row for row in event_rows if str(row.get("status", "")).strip().upper() == "READY"
    ]
    type_counts = Counter(str(row.get("semantic_type", "")).strip().upper() for row in ready_events)

    public_rows: list[dict[str, Any]] = []
    private_rows: list[dict[str, Any]] = []
    for path, role in PUBLIC_FILES:
        if path.is_file():
            public_rows.append(file_row(path, role))
    if ORACLE_CSV.is_file():
        private_rows.append(file_row(ORACLE_CSV, "private_oracle_csv", privacy="private"))

    public_rows.extend(directory_file_rows(DOCUMENT_DIR, "source_document"))
    public_rows.extend(directory_file_rows(MUTANT_DIR, "mutant_owl"))
    public_rows.extend(directory_file_rows(RULE_DIR, "formal_policy_rule"))
    if args.include_built:
        public_rows.extend(directory_file_rows(BUILT_DIR, "built_artifact"))

    for script in REPRODUCTION_SCRIPTS:
        path = PROJECT_DIR / script
        if path.is_file():
            public_rows.append(file_row(path, "reproduction_script"))

    public_rows.extend(row_hashes(event_rows, "event_row"))
    public_rows.extend(row_hashes(document_rows, "document_row"))
    public_rows.extend(row_hashes(candidate_rows, "candidate_row"))
    private_rows.extend(row_hashes(oracle_rows, "oracle_row"))

    public_rows = sorted(
        public_rows,
        key=lambda row: (
            str(row["role"]),
            str(row.get("event_id", "")),
            str(row.get("path", "")),
            str(row.get("row_index", "")),
        ),
    )
    private_rows = sorted(
        private_rows,
        key=lambda row: (
            str(row["role"]),
            str(row.get("event_id", "")),
            str(row.get("path", "")),
            str(row.get("row_index", "")),
        ),
    )

    payload = {
        "schema_version": "1.0",
        "generated_at_utc": generated_at,
        "benchmark": "external-real-v1",
        "benchmark_dir": relpath(BENCHMARK_DIR),
        "events_total": len(event_rows),
        "events_ready": len(ready_events),
        "event_ids_ready": sorted(str(row.get("event_id", "")) for row in ready_events),
        "semantic_type_counts_ready": dict(sorted(type_counts.items())),
        "documents_total": len(document_rows),
        "candidates_total": len(candidate_rows),
        "private_oracle_rows": len(oracle_rows),
        "public_file_count": len(public_rows),
        "private_integrity_rows": len(private_rows),
        "oracle_used_for_candidate_filtering": False,
        "git": git_state(),
        "limitations": [
            "An empty scaffold manifest proves only structure, not external validation.",
            "Private Oracle integrity rows are for local audit only and must not be published if filled.",
            "A freeze manifest proves file state at generation time; use Git or external archives for timestamped evidence.",
        ],
        "public_files": public_rows,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    public_csv = OUTPUT_DIR / f"{args.prefix}-files.csv"
    private_csv = OUTPUT_DIR / f"{args.prefix}-private-oracle.csv"
    json_path = OUTPUT_DIR / f"{args.prefix}.json"
    log_path = OUTPUT_DIR / f"{args.prefix}.log"
    write_csv(public_csv, public_rows)
    write_csv(private_csv, private_rows)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "external-real-v1 freeze manifest",
        f"events_ready={len(ready_events)}",
        f"public_file_count={len(public_rows)}",
        f"private_integrity_rows={len(private_rows)}",
        f"git_commit={payload['git'].get('commit', '')}",
        f"git_dirty={payload['git'].get('dirty', '')}",
        "",
        f"files={public_csv}",
        f"private_oracle={private_csv}",
        f"json={json_path}",
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
