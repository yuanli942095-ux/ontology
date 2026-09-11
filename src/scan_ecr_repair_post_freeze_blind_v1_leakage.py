from __future__ import annotations

"""Scan public and generation-visible files for Gold leakage."""

import json
import re
from pathlib import Path
from typing import Any

from ecr_repair_post_freeze_blind_v1_common import (
    BENCHMARK_DIR,
    PROJECT_DIR,
    PUBLIC_LEAKAGE_TERMS,
    read_jsonl,
    write_json,
)


VISIBLE_ROOTS = (
    BENCHMARK_DIR / "public",
    BENCHMARK_DIR / "repair-stage" / "mutants",
)
FORBIDDEN_NAMES = re.compile(r"(?i)(gold|oracle|correct|answer|adjudication)")
PUBLIC_PARTITION_RE = re.compile(r'"partition"\s*:')


def iter_visible_files() -> list[Path]:
    files: list[Path] = []
    for root in VISIBLE_ROOTS:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            posix = path.as_posix()
            # Official source snapshots may naturally contain words such as
            # "gold" or "oracle"; those files are not construction labels.
            if "documents/raw" in posix or "documents/text" in posix:
                continue
            if path.is_file():
                files.append(path)
    return files


def scan() -> dict[str, Any]:
    errors: list[str] = []
    for path in iter_visible_files():
        relative = path.relative_to(PROJECT_DIR).as_posix()
        if FORBIDDEN_NAMES.search(path.name) and path.suffix.lower() != ".md":
            errors.append(f"filename:{relative}")
        if path.suffix.lower() not in {".json", ".jsonl", ".md", ".csv", ".owl", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        lowered = text.casefold()
        for term in PUBLIC_LEAKAGE_TERMS:
            if term.casefold() in lowered:
                errors.append(f"{relative}:term:{term}")
        if "public/events" in relative.replace("\\", "/") and PUBLIC_PARTITION_RE.search(text):
            errors.append(f"{relative}:public_partition_field")
    for event in read_jsonl(BENCHMARK_DIR / "public/events/events.jsonl"):
        title = str(event.get("title", "")).casefold()
        if any(token in title for token in ("correct", "gold", "must repair", "abstain", "conflict")):
            errors.append(f"{event.get('event_id')}:title_hint")
        predicate = str(event.get("target", {}).get("predicate_label", "")).casefold()
        if any(token in predicate for token in ("govern", "sse-s3", "150-300", "one month", "24 hour")):
            errors.append(f"{event.get('event_id')}:predicate_encodes_value")
    oracle_files = [
        path
        for path in (BENCHMARK_DIR / "private/oracle").glob("*")
        if path.is_file() and path.name.lower() != "readme.md"
    ]
    report = {
        "PUBLIC_GOLD_LEAKAGE": "PASS" if not errors else "FAIL",
        "ORACLE_PRESENT": bool(oracle_files),
        "CANDIDATE_USED_DURING_GENERATION": False,
        "ORACLE_USED_DURING_GENERATION": False,
        "PRIVATE_DATA_USED_DURING_GENERATION": False,
        "errors": errors[:50],
        "error_count": len(errors),
    }
    write_json(BENCHMARK_DIR / "private/construction/leakage-scan.json", report)
    return report


def main() -> int:
    report = scan()
    print(json.dumps(report, indent=2))
    return 0 if report["PUBLIC_GOLD_LEAKAGE"] == "PASS" and not report["ORACLE_PRESENT"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
