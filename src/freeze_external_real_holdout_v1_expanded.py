from __future__ import annotations

"""Freeze external-real-holdout-v1-expanded benchmark artifacts."""

import argparse
import csv
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR, sha256_file, sha256_text, write_csv

BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v1-expanded"
OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v1-expanded"
MANIFEST_PREFIX = "external-real-holdout-v1-freeze-manifest"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_DIR))
    except ValueError:
        return str(path.resolve())


def git_state() -> dict[str, str]:
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_DIR, text=True).strip()
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=PROJECT_DIR, text=True).strip()
        return {"commit": commit, "branch": branch}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def file_row(path: Path, role: str, *, privacy: str = "public", event_id: str = "") -> dict[str, Any]:
    stat = path.stat()
    return {
        "role": role,
        "event_id": event_id,
        "privacy": privacy,
        "path": relpath(path),
        "sha256": sha256_file(path),
        "bytes": stat.st_size,
        "mtime_utc": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
    }


def directory_file_rows(base: Path, role: str, privacy: str) -> list[dict[str, Any]]:
    if not base.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(item for item in base.rglob("*") if item.is_file()):
        if path.name == ".gitkeep":
            continue
        rows.append(file_row(path, role, privacy=privacy))
    return rows


def row_hashes(rows: list[dict[str, str]], role: str, *, privacy: str = "public") -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=2):
        result.append(
            {
                "role": role,
                "event_id": str(row.get("event_id", "")).strip(),
                "privacy": privacy,
                "path": "",
                "sha256": sha256_text(
                    json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                ),
                "bytes": "",
                "mtime_utc": "",
                "row_index": index,
            }
        )
    return result


def fix_internal_paths(benchmark_dir: Path) -> int:
    support_csv = benchmark_dir / "public" / "retrieval" / "support-adjudication.csv"
    if not support_csv.is_file():
        return 0
    rows = read_csv(support_csv)
    old = "external-real-holdout-v1-expanded-draft"
    new = "external-real-holdout-v1-expanded"
    changed = 0
    for row in rows:
        evidence_file = row.get("evidence_file", "")
        if old in evidence_file:
            row["evidence_file"] = evidence_file.replace(old, new)
            changed += 1
    if changed:
        write_csv(support_csv, rows)
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--prefix", default=MANIFEST_PREFIX)
    args = parser.parse_args()
    args.benchmark_dir = args.benchmark_dir.resolve()
    args.output_dir = args.output_dir.resolve()

    generated_at = datetime.now(timezone.utc).isoformat()
    event_csv = args.benchmark_dir / "public" / "events" / "external-real-event-template.csv"
    doc_csv = args.benchmark_dir / "public" / "documents" / "external-real-document-template.csv"
    candidate_csv = args.benchmark_dir / "repair-stage" / "candidates" / "external-real-candidate-template.csv"
    oracle_csv = args.benchmark_dir / "private" / "oracle" / "external-real-oracle-template.csv"

    event_rows = read_csv(event_csv)
    document_rows = read_csv(doc_csv)
    candidate_rows = read_csv(candidate_csv)
    oracle_rows = read_csv(oracle_csv)
    ready_events = [row for row in event_rows if row.get("status") == "READY"]
    type_counts = Counter(row.get("semantic_type", "") for row in ready_events)
    agreement_counts = Counter(row.get("agreement_status", "") for row in oracle_rows)
    support_counts = Counter(row.get("support_status", "") for row in ready_events)

    if agreement_counts.get("AGREED", 0) != len(ready_events):
        raise SystemExit(f"refusing to freeze: oracle AGREED {agreement_counts.get('AGREED', 0)} != ready {len(ready_events)}")
    if support_counts.get("SEMANTIC_REVIEW_PASSED", 0) != len(ready_events):
        raise SystemExit(
            f"refusing to freeze: support SEMANTIC_REVIEW_PASSED {support_counts.get('SEMANTIC_REVIEW_PASSED', 0)} != ready {len(ready_events)}"
        )

    path_fixes = fix_internal_paths(args.benchmark_dir)

    hashed_rows: list[dict[str, Any]] = []
    readme = args.benchmark_dir / "README.md"
    if readme.is_file():
        hashed_rows.append(file_row(readme, "benchmark_readme", privacy="public"))
    hashed_rows.extend(directory_file_rows(args.benchmark_dir / "public", "public_file", "public"))
    hashed_rows.extend(directory_file_rows(args.benchmark_dir / "repair-stage", "repair_stage_file", "repair_stage"))
    hashed_rows.extend(directory_file_rows(args.benchmark_dir / "private", "private_file", "private"))
    hashed_rows.extend(row_hashes(event_rows, "event_row", privacy="public"))
    hashed_rows.extend(row_hashes(document_rows, "document_row", privacy="public"))
    hashed_rows.extend(row_hashes(candidate_rows, "candidate_row", privacy="repair_stage"))
    hashed_rows.extend(row_hashes(oracle_rows, "oracle_row", privacy="private"))
    hashed_rows = sorted(
        hashed_rows,
        key=lambda row: (str(row["privacy"]), str(row["role"]), str(row.get("event_id", "")), str(row.get("path", ""))),
    )

    payload = {
        "schema_version": "1.0",
        "generated_at_utc": generated_at,
        "benchmark": args.benchmark_dir.name,
        "benchmark_dir": relpath(args.benchmark_dir),
        "events_total": len(event_rows),
        "events_ready": len(ready_events),
        "semantic_type_counts_ready": dict(sorted(type_counts.items())),
        "oracle_agreement_status": dict(sorted(agreement_counts.items())),
        "event_support_status": dict(sorted(support_counts.items())),
        "path_fixes_in_support_adjudication": path_fixes,
        "git": git_state(),
        "limitations": [
            "Hold-out for final evaluation only; do not tune on this benchmark after freeze.",
            "CAND_003 distractors are mechanically assigned from source/domain value pools and require manual plausibility review.",
            "semantic_type labels are mechanically rotated.",
            "Single-annotator manual oracle review.",
        ],
        "files": hashed_rows,
    }
    payload["manifest_sha256"] = sha256_text(
        json.dumps(
            [{key: row[key] for key in ("role", "privacy", "path", "sha256") if key in row} for row in hashed_rows],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / f"{args.prefix}.json"
    files_csv = args.output_dir / f"{args.prefix}-files.csv"
    summary_path = args.output_dir / f"{args.prefix}-summary.json"
    write_csv(files_csv, hashed_rows)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "benchmark": payload["benchmark"],
        "frozen_at_utc": generated_at,
        "events_ready": len(ready_events),
        "manifest_sha256": payload["manifest_sha256"],
        "json": relpath(json_path),
        "files_csv": relpath(files_csv),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
