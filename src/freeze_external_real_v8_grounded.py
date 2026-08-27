from __future__ import annotations

"""Freeze staged external-real-v8-grounded files.

public/, repair-stage/, private/, and rules/ are hashed with privacy labels.
Construction input is public/ only. Candidate CSVs belong in repair-stage/,
never in public/. After this freeze, do not swap sources or enlarge windows
because a model erred.
"""

import argparse
import csv
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from external_real_v8_layout import BenchmarkLayout
from semantic_v2_common import OUTPUT_DIR, PROJECT_DIR, sha256_file, sha256_text, write_csv


BENCHMARK_DIR = PROJECT_DIR / "benchmark" / "external-real-v8-grounded"
LAYOUT = BenchmarkLayout(BENCHMARK_DIR)

REPRODUCTION_SCRIPTS = [
    "src/build_external_real_v8_grounded.py",
    "src/gate_external_real_v8_grounded.py",
    "src/curate_external_real_v8_support_windows.py",
    "src/expand_external_real_v8_grounded.py",
    "src/stage_external_real_v8_access_layout.py",
    "src/adjudicate_external_real_v8_semantic_support.py",
    "src/audit_external_real_v8_metadata_leakage.py",
    "src/validate_external_real_v8_grounded.py",
    "src/audit_external_real_v8_grounded_quality.py",
    "src/freeze_external_real_v8_grounded.py",
    "src/verify_external_real_v8_artifacts.py",
    "src/external_real_v8_layout.py",
]

PRIVACY_LABELS = {
    "public": "public",
    "repair-stage": "repair_stage",
    "private": "private",
    "rules": "upper_bound_rules",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="freeze external-real-v8-grounded")
    parser.add_argument("--prefix", default="external-real-v8-grounded-freeze-manifest")
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


def directory_file_rows(base: Path, role: str, privacy: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not base.is_dir():
        return rows
    for path in sorted(item for item in base.rglob("*") if item.is_file()):
        if path.name == ".gitkeep":
            continue
        rows.append(file_row(path, role, privacy=privacy))
    return rows


def row_hashes(rows: list[dict[str, str]], role: str, privacy: str = "public") -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=2):
        result.append(
            {
                "role": role,
                "event_id": str(row.get("event_id", "")).strip(),
                "privacy": privacy,
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
    event_rows = read_csv(LAYOUT.event_csv)
    document_rows = read_csv(LAYOUT.document_csv)
    candidate_rows = read_csv(LAYOUT.candidate_csv)
    oracle_rows = read_csv(LAYOUT.oracle_csv)
    ready_events = [row for row in event_rows if str(row.get("status", "")).strip().upper() == "READY"]
    type_counts = Counter(str(row.get("semantic_type", "")).strip().upper() for row in ready_events)

    if LAYOUT.public_candidate_paths():
        raise SystemExit("refusing to freeze: candidate files are present under public/")
    if LAYOUT.rules.exists() and not (LAYOUT.rules / "BLIND_FORBIDDEN.md").is_file():
        raise SystemExit("refusing to freeze: rules/ is not marked blind_forbidden")

    hashed_rows: list[dict[str, Any]] = []
    for path, role in (
        (BENCHMARK_DIR / "README.md", "benchmark_readme"),
        (BENCHMARK_DIR / "protocol.md", "collection_protocol"),
        (BENCHMARK_DIR / "ACCESS.md", "stage_access"),
        (LAYOUT.event_csv, "input_events_csv"),
        (LAYOUT.document_csv, "input_documents_csv"),
        (LAYOUT.query_contracts, "query_contracts"),
    ):
        if path.is_file():
            hashed_rows.append(file_row(path, role, privacy="public"))

    hashed_rows.extend(directory_file_rows(LAYOUT.public, "public_file", "public"))
    hashed_rows.extend(directory_file_rows(LAYOUT.repair_stage, "repair_stage_file", "repair_stage"))
    hashed_rows.extend(directory_file_rows(LAYOUT.private, "private_file", "private"))
    hashed_rows.extend(directory_file_rows(LAYOUT.rules, "upper_bound_rule", "upper_bound_rules"))

    for script in REPRODUCTION_SCRIPTS:
        path = PROJECT_DIR / script
        if path.is_file():
            hashed_rows.append(file_row(path, "reproduction_script", privacy="public"))

    hashed_rows.extend(row_hashes(event_rows, "event_row", privacy="public"))
    hashed_rows.extend(row_hashes(document_rows, "document_row", privacy="public"))
    hashed_rows.extend(row_hashes(candidate_rows, "candidate_row", privacy="repair_stage"))
    hashed_rows.extend(row_hashes(oracle_rows, "oracle_row", privacy="private"))
    hashed_rows = sorted(
        hashed_rows,
        key=lambda row: (
            str(row["privacy"]),
            str(row["role"]),
            str(row.get("event_id", "")),
            str(row.get("path", "")),
            str(row.get("row_index", "")),
        ),
    )

    public_files = [row for row in hashed_rows if row["privacy"] == "public"]
    payload = {
        "schema_version": "1.0",
        "generated_at_utc": generated_at,
        "benchmark": "external-real-v8-grounded",
        "benchmark_dir": relpath(BENCHMARK_DIR),
        "layout": "staged",
        "events_total": len(event_rows),
        "events_ready": len(ready_events),
        "event_ids_ready": sorted(str(row.get("event_id", "")) for row in ready_events),
        "semantic_type_counts_ready": dict(sorted(type_counts.items())),
        "documents_total": len(document_rows),
        "candidates_total": len(candidate_rows),
        "private_oracle_rows": len(oracle_rows),
        "public_file_count": len(public_files),
        "repair_stage_file_count": sum(row["privacy"] == "repair_stage" for row in hashed_rows),
        "private_integrity_rows": sum(row["privacy"] == "private" for row in hashed_rows),
        "oracle_used_for_candidate_filtering": False,
        "construction_reads_public_only": True,
        "git": git_state(),
        "limitations": [
            "Construction freeze input is public/ only.",
            "repair-stage/ candidates are for Candidate Selection, not Auto Policy Construction.",
            "private/ Oracle is Evaluation-only.",
            "rules/ are Hard Gate upper-bound only.",
            "Gold-assisted dataset curation is not Gold-assisted model inference.",
            "After freeze, do not change sources, windows, or tokens because a model erred.",
        ],
        "files": hashed_rows,
        "public_files": public_files,
    }
    manifest_sha = canonical_hash({key: value for key, value in payload.items() if key not in {"files", "public_files"}})
    payload["manifest_sha256"] = sha256_text(
        json.dumps(
            [{key: row[key] for key in ("role", "privacy", "path", "sha256") if key in row} for row in hashed_rows],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    public_csv = OUTPUT_DIR / f"{args.prefix}-files.csv"
    json_path = OUTPUT_DIR / f"{args.prefix}.json"
    log_path = OUTPUT_DIR / f"{args.prefix}.log"
    write_csv(public_csv, hashed_rows)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "external-real-v8-grounded freeze manifest",
        f"events_ready={len(ready_events)}",
        f"public_file_count={len(public_files)}",
        f"candidates_total={len(candidate_rows)}",
        f"private_oracle_rows={len(oracle_rows)}",
        f"manifest_sha256={payload['manifest_sha256']}",
        f"git_commit={payload['git'].get('commit', '')}",
        f"git_dirty={payload['git'].get('dirty', '')}",
        f"files={public_csv}",
        f"json={json_path}",
        f"payload_meta_sha256={manifest_sha}",
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
