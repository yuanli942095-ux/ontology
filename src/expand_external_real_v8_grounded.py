from __future__ import annotations

"""Restore unlabeled-dropped v4 events into v8 public files for expansion.

Does not change retrieval or Auto Policy. Scaffold-style public rows only:
no candidate, Oracle, or notes. Existing READY rows are left untouched.
"""

import argparse
import csv
from pathlib import Path

from build_external_real_v8_grounded import DST, SRC, rewrite_text, write_csv
from external_real_v8_layout import BenchmarkLayout


EVENT_FIELDS = (
    "lexical_status",
    "support_status",
    "semantic_support",
    "support_adjudication_method",
    "support_checked_before_model_run",
    "support_gate_version",
)
DOC_FIELDS = (
    "retrieved_at",
    "raw_sha256",
    "text_sha256",
    "extraction_mode",
    "cache_path",
    "window_sha256",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="restore v4 events missing from v8 READY")
    parser.add_argument("--prune-dropped", action="store_true")
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def rewrite_row(row: dict[str, str]) -> dict[str, str]:
    return {key: rewrite_text(str(value or "")) for key, value in row.items()}


def merge_missing() -> list[str]:
    layout = BenchmarkLayout(DST)
    events = read_csv(layout.event_csv)
    docs = read_csv(layout.document_csv)
    ready_ids = {row["event_id"] for row in events}
    v4_events = [rewrite_row(row) for row in read_csv(SRC / "input" / "external-real-event-template.csv")]
    v4_docs = [rewrite_row(row) for row in read_csv(SRC / "input" / "external-real-document-template.csv")]
    missing = [row for row in v4_events if row["event_id"] not in ready_ids]
    missing_ids = [row["event_id"] for row in missing]
    for row in missing:
        row["source_owl"] = ""
        row["notes"] = ""
        row["status"] = "DRAFT"
        for field in EVENT_FIELDS:
            row.setdefault(field, "")
        events.append(row)
    present_docs = {row["document_id"] for row in docs}
    for row in v4_docs:
        if row["event_id"] not in missing_ids or row["document_id"] in present_docs:
            continue
        row["notes"] = ""
        row["status"] = "DRAFT"
        row["source_type"] = "EXTERNAL_PUBLIC_RAW_EXCERPT_WINDOW"
        row["document_type"] = "public_normative_retrieved_excerpt"
        for field in DOC_FIELDS:
            row.setdefault(field, "")
        docs.append(row)
    events.sort(key=lambda row: row["event_id"])
    docs.sort(key=lambda row: (row["event_id"], row["document_id"]))
    write_csv(layout.event_csv, events)
    write_csv(layout.document_csv, docs)
    return missing_ids


def prune_dropped() -> tuple[int, int]:
    layout = BenchmarkLayout(DST)
    events = read_csv(layout.event_csv)
    docs = read_csv(layout.document_csv)
    retrievals = read_csv(layout.retrieval_csv)
    ready = [row for row in events if str(row.get("status", "")).upper() == "READY"]
    ready_ids = {row["event_id"] for row in ready}
    ready_docs = [row for row in docs if row["event_id"] in ready_ids]
    ready_retrievals = [row for row in retrievals if row.get("event_id") in ready_ids]
    write_csv(layout.event_csv, ready)
    write_csv(layout.document_csv, ready_docs)
    if ready_retrievals:
        write_csv(layout.retrieval_csv, ready_retrievals)
    for path in layout.rules.glob("*-formal-policy.json"):
        event_id = path.name[: -len("-formal-policy.json")]
        if event_id not in ready_ids:
            path.unlink()
    return len(ready), len(events) - len(ready)


def main() -> int:
    args = parse_args()
    if args.prune_dropped:
        keep, dropped = prune_dropped()
        print(f"pruned dropped={dropped} keep={keep}")
        return 0
    missing_ids = merge_missing()
    print("missing_count", len(missing_ids))
    print("missing_ids", ",".join(missing_ids))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
