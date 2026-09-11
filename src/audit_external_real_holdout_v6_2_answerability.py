from __future__ import annotations

"""Automatic answerability audit for v6.2 claim-level public targets.

This is a freeze gate, not a Gold relabel. Repair events fail if the public
claim contract does not uniquely rank the Gold window, if current assertion is
still an opaque placeholder, or if the replacement value leaked into public
target fields.
"""

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from holdout_v6_2_claim_contract import (
    LEAKAGE_FIELDS,
    leakage_hits,
    unique_property_window,
)
from rfc213_direct_repair_ir import ontology_literal_assertions, source_windows
from semantic_v2_common import PROJECT_DIR, write_csv

BENCHMARK = PROJECT_DIR / "benchmark/external-real-holdout-v6-2-direct-ir-blind"
OUTPUT = PROJECT_DIR / "output/external-real-holdout-v6-2-direct-ir-blind/answerability"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    events = read_jsonl(args.benchmark_dir / "public/events/events.jsonl")
    golds = {row["event_id"]: row for row in read_jsonl(args.benchmark_dir / "private/oracle/gold-repair-ir.jsonl")}
    rows = []
    for event in events:
        event_id = event["event_id"]
        gold = golds[event_id]
        evidence = (args.benchmark_dir / "public/excerpts" / f"{event_id}-evidence.md").read_text(encoding="utf-8")
        windows = source_windows(evidence)
        assertions = ontology_literal_assertions(args.benchmark_dir / event["source_owl"])
        current_literals = [row["lexical"] for row in assertions]
        gold_window = str(gold.get("gold_source_window", "")).removeprefix("SOURCE_WINDOW_")
        gold_text = dict(windows).get(gold_window, "")
        new_lexical = gold.get("replacement", {}).get("new_value", {}).get("lexical", "")
        public_text = "\n".join(str(event.get(field, "")) for field in LEAKAGE_FIELDS)
        pin = " ".join(str(event.get(k, "")) for k in (
            "target_entity_label", "target_property_label", "value_neutral_question", "target_cq",
        ))
        leaks = leakage_hits(public_text, new_lexical, gold_text, pin_text=pin)
        unique = (
            unique_property_window(event.get("target_property_label", ""), windows) == gold_window
            if gold["decision"] == "REPAIR"
            else None
        )
        placeholder = any("unmodeled" in value for value in current_literals)
        missing = [field for field in LEAKAGE_FIELDS if not str(event.get(field, "")).strip()]
        generic_predicate = "evidence-grounded value" in (event.get("predicate_label") or "").lower()
        if gold["decision"] == "REPAIR":
            if missing:
                status = "MISSING_CLAIM_FIELDS"
            elif leaks:
                status = "VALUE_LEAKAGE"
            elif placeholder:
                status = "OLD_VALUE_OPAQUE"
            elif generic_predicate:
                status = "PREDICATE_TOO_GENERIC"
            elif not unique:
                status = "WINDOW_NOT_UNIQUE"
            else:
                status = "AUTOMATIC_YES"
        else:
            generic_abstain = (
                gold["decision"] == "ABSTAIN"
                and "requirement under assessment" in (event.get("target_property_label") or "").lower()
            )
            if missing:
                status = "MISSING_CLAIM_FIELDS"
            elif generic_abstain:
                status = "ABSTAIN_TARGET_GENERIC"
            else:
                status = "NON_REPAIR_CONTRACT_PRESENT"
        rows.append({
            "event_id": event_id,
            "gold_decision": gold["decision"],
            "gold_source_window": gold.get("gold_source_window", ""),
            "target_entity_label": event.get("target_entity_label", ""),
            "target_property_label": event.get("target_property_label", ""),
            "current_value_surface": event.get("current_value_surface", ""),
            "current_literal": current_literals[0] if current_literals else "",
            "unique_gold_window": unique,
            "leakage": json.dumps(leaks),
            "missing_fields": json.dumps(missing),
            "answerability_status": status,
            "manual_gold_uniquely_identifiable": "PENDING_MANUAL_REVIEW",
            "manual_alternative_plausible_windows": "",
            "manual_notes": "",
        })
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "v6-2-automatic-answerability-audit.csv", rows)
    repair = [row for row in rows if row["gold_decision"] == "REPAIR"]
    abstain = [row for row in rows if row["gold_decision"] == "ABSTAIN"]
    counts = Counter(row["answerability_status"] for row in repair)
    abstain_counts = Counter(row["answerability_status"] for row in abstain)
    leak_n = counts.get("VALUE_LEAKAGE", 0)
    unique_fail = counts.get("WINDOW_NOT_UNIQUE", 0)
    abstain_generic = abstain_counts.get("ABSTAIN_TARGET_GENERIC", 0)
    summary = {
        "status": "PRE_FREEZE_GATE",
        "events": len(rows),
        "repair_events": len(repair),
        "repair_automatic_yes": counts.get("AUTOMATIC_YES", 0),
        "repair_status_counts": dict(counts),
        "abstain_status_counts": dict(abstain_counts),
        "value_leakage": leak_n,
        "window_not_unique": unique_fail,
        "abstain_generic": abstain_generic,
        "freeze_allowed": (
            bool(repair)
            and leak_n == 0
            and unique_fail == 0
            and counts.get("AUTOMATIC_YES", 0) == len(repair)
            and abstain_generic == 0
        ),
        "interpretation": (
            "Automatic YES means public claim fields uniquely rank the Gold window, "
            "current assertion is not unmodeled, and the new value did not leak. "
            "ABSTAIN events must name a claim, not an RFC-title generic. "
            "Manual 70-event review is still required before freeze."
        ),
    }
    (args.output_dir / "v6-2-automatic-answerability-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
