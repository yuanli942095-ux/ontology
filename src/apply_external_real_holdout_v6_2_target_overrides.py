from __future__ import annotations

"""Apply value-blind claim-target overrides to v6.2 public events.

Does not change Gold decisions, Gold windows, or replacement values.
"""

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from holdout_v6_2_claim_contract import ABSTAIN_SEMANTICS, bind_public_claim_fields
from semantic_v2_common import PROJECT_DIR

BENCHMARK = PROJECT_DIR / "benchmark/external-real-holdout-v6-2-direct-ir-blind"
OVERRIDE_PATH = BENCHMARK / "private/construction/v6-2-target-overrides.json"
PUBLIC_FIELDS = [
    "event_id", "semantic_type", "domain", "subject_label", "predicate_label",
    "case_context", "source_ids", "source_owl", "status", "target_claim_id",
    "target_entity_label", "target_property_label", "target_property_iri",
    "current_value_surface", "current_value_semantics", "value_neutral_question",
    "target_cq",
]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def apply_overrides(
    events: list[dict[str, Any]],
    golds: dict[str, dict[str, Any]],
    overrides: dict[str, Any],
) -> list[str]:
    applied: list[str] = []
    repair_map = overrides.get("repair") or {}
    abstain_map = overrides.get("abstain") or {}
    for event in events:
        event_id = event["event_id"]
        gold = golds[event_id]
        spec = None
        semantics = None
        if event_id in repair_map:
            spec = repair_map[event_id]
        elif event_id in abstain_map:
            spec = abstain_map[event_id]
            semantics = ABSTAIN_SEMANTICS
        if not spec:
            continue
        bind_public_claim_fields(
            event,
            entity=spec["entity"],
            property_label=spec["property_label"],
            claim_id=spec.get("claim_id") or f"{event_id}-claim-01",
            semantics=semantics,
        )
        applied.append(event_id)
        if gold["decision"] == "ABSTAIN" and "requirement under assessment" in event["target_property_label"].lower():
            raise SystemExit(f"abstain override still generic: {event_id}")
    return applied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK)
    parser.add_argument("--overrides", type=Path, default=OVERRIDE_PATH)
    args = parser.parse_args()
    events_path = args.benchmark_dir / "public/events/events.jsonl"
    golds = {row["event_id"]: row for row in read_jsonl(args.benchmark_dir / "private/oracle/gold-repair-ir.jsonl")}
    overrides = json.loads(args.overrides.read_text(encoding="utf-8"))
    events = read_jsonl(events_path)
    applied = apply_overrides(events, golds, overrides)
    write_jsonl(events_path, events)
    csv_path = args.benchmark_dir / "public/events/external-real-event-template.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PUBLIC_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(events)
    missing_abstain = [
        event_id for event_id, gold in golds.items()
        if gold["decision"] == "ABSTAIN" and event_id not in (overrides.get("abstain") or {})
    ]
    if missing_abstain:
        raise SystemExit(f"missing abstain overrides: {missing_abstain}")
    print(json.dumps({"applied": len(applied), "ids": applied}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
