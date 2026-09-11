from __future__ import annotations

"""Audit E120 task-formulation derivation vs candidate mapping consistency."""

import csv
import json
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT_DIR / "output" / "m12-task-formulation-pilot" / "e120-mapping-consistency-audit.json"
RAW = (
    PROJECT_DIR
    / "output"
    / "m12-task-formulation-pilot"
    / "arm-b-task-formulation"
    / "raw_window_metadata_light"
    / "raw"
    / "EXT_E120-run2-seed20260828.json"
)
DETAILS = (
    PROJECT_DIR
    / "output"
    / "m12-task-formulation-pilot"
    / "arm-b-task-formulation"
    / "ir"
    / "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827-details.csv"
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def main() -> int:
    record = json.loads(RAW.read_text(encoding="utf-8-sig"))
    detail = next(row for row in read_csv(DETAILS) if row["event_id"] == "EXT_E120" and row["run"] == "2")
    derived = record.get("m12_derivation", {}).get("derived_result", "")
    selected = detail.get("selected_value", "")
    exact = bool(derived and selected and derived == selected)
    selected_key = selected.split("=", 1)[0] if "=" in selected else selected
    derived_key = derived.split("=", 1)[0] if "=" in derived else derived
    audit: dict[str, Any] = {
        "event_id": "EXT_E120",
        "run": 2,
        "derived_semantic_result": derived,
        "selected_value": selected,
        "oracle_value": detail.get("oracle_value", ""),
        "selected_candidate_id": detail.get("selected_candidate_id", ""),
        "oracle_candidate_id": detail.get("oracle_candidate_id", ""),
        "selection_oracle_correct": detail.get("selection_oracle_correct", ""),
        "full_closure_success": detail.get("full_closure_success", ""),
        "derived_selected_exact_match": exact,
        "key_match_only": bool(derived_key and selected_key and derived_key == selected_key and not exact),
        "diagnosis": (
            "weak_mapping_success: task formulation extracted an evidence-grounded availability value, "
            "but candidate ranking mapped it to the reference_export repair literal."
            if not exact
            else "strong_mapping_success"
        ),
        "candidate_scores_json": json.loads(detail.get("candidate_scores_json") or "[]"),
        "candidate_used_before_derivation": bool(record.get("candidate_used")),
        "oracle_used_before_derivation": bool(record.get("oracle_used")),
        "manual_policy_used_before_derivation": bool(record.get("manual_policy_used")),
    }
    OUTPUT.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"output={OUTPUT}")
    print(f"diagnosis={audit['diagnosis']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
