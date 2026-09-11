from __future__ import annotations

"""Build coarse failure-flow table for M13 Base and Full M16."""

import csv
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT / "output" / "external-real-holdout-v5-blind-large"
RUN = ROOT / "final-blind-eval-r5-m16-deepseek"
OUT = ROOT / "statistics" / "v5-failure-flow-table.csv"


M13 = RUN / "m13-pilot" / "arm-d-rule-refinement" / "ir" / "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827-details.csv"
M16 = RUN / "m16-full-holdout-combined" / "m16-full-holdout-combined-details.csv"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def truth(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def selected(row: dict[str, str]) -> bool:
    return bool(str(row.get("selected_candidate_id", "")).strip())


def oracle_correct(row: dict[str, str]) -> bool:
    value = row.get("oracle_correct")
    if value is None:
        value = row.get("selection_oracle_correct")
    return truth(value)


def closure_success(row: dict[str, str]) -> bool:
    value = row.get("closure_success")
    if value is None:
        value = row.get("full_closure_success")
    return truth(value)


def m13_summary(rows: list[dict[str, str]]) -> dict[str, Any]:
    ir_counter = Counter(row.get("ir_status", "") for row in rows)
    path_counter = Counter(row.get("decision_path", "") for row in rows)
    wrong = sum(1 for row in rows if selected(row) and not oracle_correct(row))
    selected_correct_no_closure = sum(1 for row in rows if selected(row) and oracle_correct(row) and not closure_success(row))
    return {
        "method": "M13_BASE",
        "attempts": len(rows),
        "ir_invalid_or_incomplete": ir_counter.get("INVALID_OUTPUT", 0) + ir_counter.get("INCOMPLETE_IR", 0),
        "rank_or_tie_abstain": path_counter.get("IR_RANK_ABSTAIN", 0) + path_counter.get("IR_RANK_TIE_ABSTAIN", 0),
        "wrong_select": wrong,
        "owl_closure_fail_after_correct_select": selected_correct_no_closure,
        "success": sum(1 for row in rows if closure_success(row)),
        "abstain_or_fail_closed": sum(1 for row in rows if not selected(row)),
    }


def m16_summary(rows: list[dict[str, str]]) -> dict[str, Any]:
    path_counter = Counter(row.get("final_decision_path", "") for row in rows)
    wrong = sum(1 for row in rows if selected(row) and not oracle_correct(row))
    selected_correct_no_closure = sum(1 for row in rows if selected(row) and oracle_correct(row) and not closure_success(row))
    return {
        "method": "FULL_M16",
        "attempts": len(rows),
        "ir_invalid_or_incomplete": 0,
        "rank_or_tie_abstain": path_counter.get("IR_RANK_ABSTAIN", 0) + path_counter.get("IR_RANK_TIE_ABSTAIN", 0),
        "wrong_select": wrong,
        "owl_closure_fail_after_correct_select": selected_correct_no_closure,
        "success": sum(1 for row in rows if closure_success(row)),
        "abstain_or_fail_closed": sum(1 for row in rows if not selected(row)),
    }


def main() -> int:
    rows = [m13_summary(read_csv(M13)), m16_summary(read_csv(M16))]
    write_csv(OUT, rows)
    for row in rows:
        print(row)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
