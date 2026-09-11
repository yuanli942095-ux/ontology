from __future__ import annotations

"""Compare V4.4 CSS slot vs public-window handoff on the same 600 LIGHT attempts."""

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR, write_csv


BEFORE = (
    PROJECT_DIR
    / "output"
    / "auto-policy-v4.4-css-ir"
    / "auto-policy-v4.4-css-ir-raw_window_metadata_light-r3-seed20260827"
)
AFTER_DIR = PROJECT_DIR / "output" / "auto-policy-v4.4-handoff-ir"
AFTER = AFTER_DIR / "auto-policy-v4.4-handoff-ir-raw_window_metadata_light-r3-seed20260827"
OUTPUT = PROJECT_DIR / "output" / "auto-policy-v4.4-handoff-ir"


def load_details(prefix: Path) -> list[dict[str, str]]:
    return list(csv.DictReader((prefix.parent / (prefix.name + "-details.csv")).open(encoding="utf-8-sig")))


def load_summary(prefix: Path) -> dict[str, Any]:
    return json.loads((prefix.parent / (prefix.name + ".json")).read_text(encoding="utf-8-sig"))


def main() -> int:
    before_details = load_details(BEFORE)
    after_details = load_details(AFTER)
    before_json = load_summary(BEFORE)
    after_json = load_summary(AFTER)
    before_sum = before_json["summary"][0]
    after_sum = after_json["summary"][0]

    def truthy(row: dict[str, str], key: str) -> bool:
        return str(row.get(key) or "").strip().lower() in {"true", "1", "yes"}

    before_qwen = sum(1 for row in before_details if int(row.get("eval_count") or 0) > 0 or int(row.get("prompt_eval_count") or 0) > 0)
    after_qwen = sum(1 for row in after_details if int(row.get("eval_count") or 0) > 0 or int(row.get("prompt_eval_count") or 0) > 0)
    ir_b = Counter(row["ir_status"] for row in before_details)
    ir_a = Counter(row["ir_status"] for row in after_details)
    gen_b = Counter(row["generation_status"] for row in before_details)
    gen_a = Counter(row["generation_status"] for row in after_details)
    lost = [
        (row["event_id"], row["run"])
        for row in before_details
        if row["selection_status"] == "SELECTED"
        and not any(
            other["event_id"] == row["event_id"]
            and other["run"] == row["run"]
            and other["selection_status"] == "SELECTED"
            for other in after_details
        )
    ]
    gained = [
        row
        for row in after_details
        if row["selection_status"] == "SELECTED"
        and not any(
            other["event_id"] == row["event_id"]
            and other["run"] == row["run"]
            and other["selection_status"] == "SELECTED"
            for other in before_details
        )
    ]
    table = [
        {"metric": "RETRIEVAL_FAILED generation", "before": gen_b.get("RETRIEVAL_FAILED", 0), "after": gen_a.get("RETRIEVAL_FAILED", 0)},
        {"metric": "Qwen actually called", "before": before_qwen, "after": after_qwen},
        {"metric": "IR OK", "before": ir_b.get("OK", 0), "after": ir_a.get("OK", 0)},
        {"metric": "Selected", "before": before_sum["selected"], "after": after_sum["selected"]},
        {"metric": "Oracle / closure", "before": f"{before_sum['full_closure_accuracy']:.1%} ({int(round(before_sum['full_closure_accuracy'] * 600))})", "after": f"{after_sum['full_closure_accuracy']:.1%} ({int(round(after_sum['full_closure_accuracy'] * 600))})"},
        {"metric": "P|select", "before": f"{(sum(1 for row in before_details if truthy(row, 'selection_oracle_correct')) / max(1, int(before_sum['selected']))):.1%}", "after": f"{(sum(1 for row in after_details if truthy(row, 'selection_oracle_correct')) / max(1, int(after_sum['selected']))):.1%}"},
        {"metric": "Strict events", "before": f"{before_sum['strict_event_successes']}/200", "after": f"{after_sum['strict_event_successes']}/200"},
        {"metric": "Lost prior SELECT", "before": 0, "after": len(lost)},
        {"metric": "New SELECT", "before": 0, "after": len(gained)},
        {"metric": "New SELECT full closure", "before": 0, "after": sum(1 for row in gained if truthy(row, "full_closure_success"))},
    ]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUTPUT / "handoff-vs-css-comparison.csv", table)
    (OUTPUT / "handoff-vs-css-comparison.json").write_text(
        json.dumps(
            {
                "before_method": before_json.get("method"),
                "after_method": after_json.get("method"),
                "ir_before": dict(ir_b),
                "ir_after": dict(ir_a),
                "generation_before": dict(gen_b),
                "generation_after": dict(gen_a),
                "lost_select": lost,
                "new_select": [
                    {
                        "event_id": row["event_id"],
                        "run": row["run"],
                        "semantic_type": row["semantic_type"],
                        "candidate": row["selected_candidate_id"],
                        "oracle": truthy(row, "selection_oracle_correct"),
                        "closure": truthy(row, "full_closure_success"),
                    }
                    for row in gained
                ],
                "table": table,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(table, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
