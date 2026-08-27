from __future__ import annotations

"""Audit whether frozen event metadata already states the conclusion.

FULL_METADATA and LIGHT_METADATA are frozen retrieval contracts. Construction
queries must not be rebuilt after freeze to chase model errors.
"""

import argparse
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import audit_external_real_v3_naturalized_evidence_support as support
from external_real_v8_layout import (
    FULL_METADATA_FIELDS,
    LIGHT_METADATA_FIELDS,
    BenchmarkLayout,
    construction_paths,
)
from semantic_v2_common import OUTPUT_DIR, PROJECT_DIR


RELATION = re.compile(
    r"supersede|replac(?:e|es|ed|ing)|no longer current|废止|取代|替代|不再适用|新旧",
    re.I,
)
DATE_PAIR = re.compile(r"(?:19|20)\d{2}.{0,48}(?:19|20)\d{2}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="audit v8 metadata leakage")
    parser.add_argument(
        "--benchmark-dir",
        type=Path,
        default=PROJECT_DIR / "benchmark" / "external-real-v8-grounded",
    )
    parser.add_argument(
        "--policy-dir",
        type=Path,
        default=PROJECT_DIR / "benchmark" / "external-real-v4-evidence-aligned" / "rules",
    )
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def field_text(event: dict[str, str], fields: tuple[str, ...]) -> str:
    return " ".join(str(event.get(key, "") or "") for key in fields)


def leak_codes(text: str, allowed_values: list[str]) -> list[str]:
    codes: list[str] = []
    lowered = text.lower()
    for value in allowed_values:
        compact = str(value).strip()
        if len(compact) >= 8 and compact.lower() in lowered:
            codes.append("canonical_result")
            break
    if RELATION.search(text):
        codes.append("relation_conclusion")
    if DATE_PAIR.search(text) and RELATION.search(text):
        codes.append("date_conclusion")
    return codes


def audit_event(event: dict[str, str], allowed_values: list[str]) -> dict[str, Any]:
    full_text = field_text(event, FULL_METADATA_FIELDS)
    light_text = field_text(event, LIGHT_METADATA_FIELDS)
    full_leaks = leak_codes(full_text, allowed_values)
    light_leaks = leak_codes(light_text, allowed_values)
    if light_leaks:
        severity = "LEAK_LIGHT"
    elif full_leaks:
        severity = "LEAK_FULL_ONLY"
    else:
        severity = "CLEAN"
    return {
        "event_id": event["event_id"],
        "domain": event.get("domain", ""),
        "semantic_type": event.get("semantic_type", ""),
        "full_leak_codes": "|".join(full_leaks),
        "light_leak_codes": "|".join(light_leaks),
        "severity": severity,
        "full_metadata_fields": "|".join(FULL_METADATA_FIELDS),
        "light_metadata_fields": "|".join(LIGHT_METADATA_FIELDS),
        "checked_before_model_run": True,
    }


def main() -> int:
    args = parse_args()
    benchmark = args.benchmark_dir.resolve()
    paths = construction_paths(benchmark)
    events = [
        row
        for row in read_csv(paths["event_csv"])
        if str(row.get("status", "")).upper() == "READY"
    ]
    policy_dir = args.policy_dir
    if (benchmark / "rules").is_dir() and list((benchmark / "rules").glob("*-formal-policy.json")):
        policy_dir = benchmark / "rules"
    rows = []
    for event in events:
        policy = policy_dir / f"{event['event_id']}-formal-policy.json"
        allowed, _error = support.selected_policy_values(policy) if policy.is_file() else ([], "")
        rows.append(audit_event(event, allowed))
    layout = BenchmarkLayout(benchmark)
    layout.public_retrieval.mkdir(parents=True, exist_ok=True)
    write_csv(layout.metadata_leakage_csv, rows)
    summary = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "events": len(rows),
        "clean": sum(row["severity"] == "CLEAN" for row in rows),
        "leak_full_only": sum(row["severity"] == "LEAK_FULL_ONLY" for row in rows),
        "leak_light": sum(row["severity"] == "LEAK_LIGHT" for row in rows),
        "note": "Curation audit. Construction still uses frozen FULL/LIGHT contracts; do not rewrite metadata after freeze.",
    }
    summary_path = OUTPUT_DIR / "external-real-v8-grounded-metadata-leakage-summary.json"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
