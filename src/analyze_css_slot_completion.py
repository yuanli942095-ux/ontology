from __future__ import annotations

"""Diagnose CSS I_NONCRITICAL scope_relation slot completion. Candidate-blind."""

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from auto_policy_v4_css_slot import complete_css_scope_relation
from semantic_v2_common import PROJECT_DIR, write_csv


DETAILS = (
    PROJECT_DIR
    / "output"
    / "auto-policy-v4.4-ir"
    / "auto-policy-v4.4-ir-raw_window_metadata_light-r3-seed20260827-details.csv"
)
OUTPUT_DIR = PROJECT_DIR / "output" / "auto-policy-v4.4-css-slot"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> int:
    rows_out: list[dict[str, Any]] = []
    details = list(csv.DictReader(DETAILS.open(encoding="utf-8-sig")))
    pool = [
        row
        for row in details
        if row["semantic_type"] == "CROSS_SENTENCE_SCOPE" and row["ir_reason"] == "missing:scope_relation"
    ]
    for row in pool:
        raw = load_json(PROJECT_DIR / row["raw_output_file"])
        response = raw.get("response") if isinstance(raw.get("response"), dict) else {}
        fields = json.loads(row["ir_json"] or "{}")
        payload_fields = fields if isinstance(fields, dict) else {}
        chosen, klass, reason = complete_css_scope_relation(response, payload_fields)
        rows_out.append(
            {
                "event_id": row["event_id"],
                "run": row["run"],
                "seed": row["seed"],
                "slot_class": klass,
                "completed_scope_relation": chosen or "",
                "reason": reason,
                "ir_relation": payload_fields.get("relation", ""),
                "has_qualifier": bool(payload_fields.get("qualifier")),
                "has_scope_target": bool(payload_fields.get("scope_target")),
                "generation_status": row["generation_status"],
                "raw_output_file": row["raw_output_file"],
            }
        )
    summary = []
    counts = Counter(item["slot_class"] for item in rows_out)
    for klass in ("S1", "S2", "S3"):
        items = [item for item in rows_out if item["slot_class"] == klass]
        summary.append(
            {
                "slot_class": klass,
                "n": len(items),
                "unique_events": len({item["event_id"] for item in items}),
                "completed_values": dict(Counter(item["completed_scope_relation"] for item in items if item["completed_scope_relation"])),
            }
        )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(OUTPUT_DIR / "css-slot-details.csv", rows_out)
    (OUTPUT_DIR / "css-slot-summary.json").write_text(
        json.dumps({"n": len(rows_out), "counts": dict(counts), "summary": summary}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"n": len(rows_out), "counts": dict(counts), "summary": summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
