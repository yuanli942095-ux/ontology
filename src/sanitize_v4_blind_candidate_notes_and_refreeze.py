from __future__ import annotations

"""Sanitize v4-blind candidate notes and write a post-sanitize freeze manifest."""

import argparse
import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR


BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v4-blind"
OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v4-blind"
CANDIDATES = BENCHMARK / "repair-stage" / "candidates" / "external-real-candidate-template.csv"
DEFAULT_NOTE = "Hold-out candidate generated before final method run; candidate notes contain no oracle labels."


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def row_hash(row: dict[str, Any]) -> str:
    payload = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_bytes(payload.encode("utf-8"))


def sanitize_candidate_notes(candidate_path: Path, note: str) -> dict[str, Any]:
    before_hash = sha256_file(candidate_path)
    rows = read_csv(candidate_path)
    fieldnames = list(rows[0].keys()) if rows else []
    changed = 0
    before_values: dict[str, int] = {}
    after_values: dict[str, int] = {}
    protected_columns = ["event_id", "candidate_id", "display_value", "operation_json", "status"]
    protected_before = [{key: row.get(key, "") for key in protected_columns} for row in rows]

    for row in rows:
        old = row.get("notes", "")
        before_values[old] = before_values.get(old, 0) + 1
        if old != note:
            changed += 1
        row["notes"] = note
        after_values[note] = after_values.get(note, 0) + 1

    write_csv(candidate_path, rows, fieldnames)
    protected_after = [{key: row.get(key, "") for key in protected_columns} for row in read_csv(candidate_path)]
    if protected_before != protected_after:
        raise RuntimeError("Protected candidate columns changed during note sanitation")

    return {
        "candidate_path": str(candidate_path.relative_to(PROJECT_DIR)),
        "rows": len(rows),
        "notes_changed": changed,
        "before_sha256": before_hash,
        "after_sha256": sha256_file(candidate_path),
        "before_note_values": before_values,
        "after_note_values": after_values,
        "protected_columns_verified_unchanged": True,
    }


def benchmark_file_rows(benchmark_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(item for item in benchmark_dir.rglob("*") if item.is_file()):
        rel = path.relative_to(PROJECT_DIR)
        privacy = "private" if any(part.lower() == "private" for part in rel.parts) else "public"
        rows.append(
            {
                "privacy": privacy,
                "path": str(rel),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
                "mtime_utc": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
            }
        )
    return rows


def load_event_counts(benchmark_dir: Path) -> dict[str, Any]:
    event_path = benchmark_dir / "public" / "events" / "external-real-event-template.csv"
    oracle_path = benchmark_dir / "private" / "oracle" / "external-real-oracle-template.csv"
    events = read_csv(event_path) if event_path.is_file() else []
    oracle = read_csv(oracle_path) if oracle_path.is_file() else []
    type_counts: dict[str, int] = {}
    support_counts: dict[str, int] = {}
    oracle_counts: dict[str, int] = {}
    for row in events:
        type_counts[row.get("semantic_type", "")] = type_counts.get(row.get("semantic_type", ""), 0) + 1
        support_counts[row.get("support_status", "")] = support_counts.get(row.get("support_status", ""), 0) + 1
    for row in oracle:
        oracle_counts[row.get("agreement_status", "")] = oracle_counts.get(row.get("agreement_status", ""), 0) + 1
    return {
        "events_total": len(events),
        "events_ready": sum(1 for row in events if row.get("status") == "READY"),
        "semantic_type_counts_ready": dict(sorted(type_counts.items())),
        "event_support_status": dict(sorted(support_counts.items())),
        "oracle_agreement_status": dict(sorted(oracle_counts.items())),
    }


def write_manifest(args: argparse.Namespace, sanitation: dict[str, Any]) -> dict[str, Any]:
    files = benchmark_file_rows(args.benchmark_dir)
    generated_at = datetime.now(UTC).isoformat()
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at_utc": generated_at,
        "benchmark": args.benchmark_dir.name,
        "benchmark_dir": str(args.benchmark_dir.relative_to(PROJECT_DIR)),
        "freeze_label": args.prefix,
        "sanitation": sanitation,
        "counts": load_event_counts(args.benchmark_dir),
        "limitations": [
            "Post-sanitize manifest records the benchmark state after removing oracle-bearing candidate notes.",
            "Only candidate notes are changed by this sanitation step; candidate ids, values, operations, and oracle rows are preserved.",
            "Single-annotator manual oracle review remains a validity limitation.",
        ],
        "files": files,
    }
    manifest_text = json.dumps(payload, ensure_ascii=False, indent=2)
    payload["manifest_sha256"] = sha256_bytes(manifest_text.encode("utf-8"))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / f"{args.prefix}.json"
    files_path = args.output_dir / f"{args.prefix}-files.csv"
    summary_path = args.output_dir / f"{args.prefix}-summary.json"

    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(files_path, files, ["privacy", "path", "sha256", "bytes", "mtime_utc"])
    summary = {
        "benchmark": args.benchmark_dir.name,
        "generated_at_utc": generated_at,
        "events_ready": payload["counts"]["events_ready"],
        "candidate_notes_changed": sanitation["notes_changed"],
        "candidate_after_sha256": sanitation["after_sha256"],
        "manifest_sha256": payload["manifest_sha256"],
        "json": str(manifest_path.relative_to(PROJECT_DIR)),
        "files_csv": str(files_path.relative_to(PROJECT_DIR)),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--candidate-file", type=Path, default=CANDIDATES)
    parser.add_argument("--note", default=DEFAULT_NOTE)
    parser.add_argument("--prefix", default="external-real-holdout-v4-blind-post-sanitize-freeze-manifest")
    args = parser.parse_args()

    args.benchmark_dir = args.benchmark_dir.resolve()
    args.output_dir = args.output_dir.resolve()
    args.candidate_file = args.candidate_file.resolve()
    if not args.candidate_file.is_file():
        raise FileNotFoundError(args.candidate_file)
    sanitation = sanitize_candidate_notes(args.candidate_file, args.note)
    summary = write_manifest(args, sanitation)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
