from __future__ import annotations

"""Build coverage, abstention, and precision-given-select metrics for v5."""

import csv
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT / "output" / "external-real-holdout-v5-blind-large"
RUN = ROOT / "final-blind-eval-r5-m16-deepseek"
OUT = ROOT / "statistics" / "v5-selective-repair-metrics.csv"


DETAILS = {
    "M13_BASE": RUN
    / "m13-pilot"
    / "arm-d-rule-refinement"
    / "ir"
    / "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827-details.csv",
    "FULL_M16": RUN / "m16-full-holdout-combined" / "m16-full-holdout-combined-details.csv",
}


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


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def summarize(name: str, rows: list[dict[str, str]]) -> dict[str, Any]:
    attempts = len(rows)
    selected_rows = [row for row in rows if selected(row)]
    abstains = attempts - len(selected_rows)
    oracle = sum(1 for row in rows if oracle_correct(row))
    closure = sum(1 for row in rows if closure_success(row))
    selected_oracle = sum(1 for row in selected_rows if oracle_correct(row))
    selected_closure = sum(1 for row in selected_rows if closure_success(row))
    wrong_selected = len(selected_rows) - selected_oracle
    return {
        "method": name,
        "attempts": attempts,
        "selected": len(selected_rows),
        "abstain": abstains,
        "coverage": f"{len(selected_rows) / attempts:.6f}",
        "coverage_pct": pct(len(selected_rows) / attempts),
        "abstention_rate": f"{abstains / attempts:.6f}",
        "abstention_rate_pct": pct(abstains / attempts),
        "oracle_success": oracle,
        "oracle_accuracy": f"{oracle / attempts:.6f}",
        "oracle_accuracy_pct": pct(oracle / attempts),
        "closure_success": closure,
        "closure_accuracy": f"{closure / attempts:.6f}",
        "closure_accuracy_pct": pct(closure / attempts),
        "precision_given_select_oracle": f"{selected_oracle / len(selected_rows):.6f}" if selected_rows else "0.000000",
        "precision_given_select_oracle_pct": pct(selected_oracle / len(selected_rows)) if selected_rows else "0.00%",
        "precision_given_select_closure": f"{selected_closure / len(selected_rows):.6f}" if selected_rows else "0.000000",
        "precision_given_select_closure_pct": pct(selected_closure / len(selected_rows)) if selected_rows else "0.00%",
        "wrong_selected": wrong_selected,
        "wrong_selected_rate": f"{wrong_selected / attempts:.6f}",
        "wrong_selected_rate_pct": pct(wrong_selected / attempts),
    }


def main() -> int:
    rows = [summarize(name, read_csv(path)) for name, path in DETAILS.items()]
    write_csv(OUT, rows)
    for row in rows:
        print(row)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
