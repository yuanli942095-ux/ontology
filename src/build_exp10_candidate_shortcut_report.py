from __future__ import annotations

"""Build Exp10 candidate shortcut report."""

import argparse
import csv
import json
from pathlib import Path

from semantic_v2_common import PROJECT_DIR, write_csv


EXP10_ROOT = PROJECT_DIR / "output" / "paper-final-validation" / "10-candidate-shortcut"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def truth(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", default="formal")
    args = parser.parse_args()

    summary_path = EXP10_ROOT / f"candidate-shortcut-summary-{args.phase}.csv"
    if not summary_path.is_file():
        raise FileNotFoundError(summary_path)
    rows = read_csv(summary_path)
    lines = [
        "# Exp10 Candidate-Only Shortcut Report",
        "",
        f"Input: `{summary_path.relative_to(PROJECT_DIR)}`",
        "",
        "## Chance and analytical baselines",
        "",
        "- Permuted benchmark chance oracle accuracy: **33.33%**",
        "- Always `CAND_002` on permuted benchmark: expected oracle **33.33%** (no model rerun needed for original v5 always-CAND_002 = 100%)",
        "",
        "## Method summary",
        "",
        "| Method | Closure | Strict events | Abstain | Wrong-select |",
        "|--------|---------|---------------|---------|--------------|",
    ]
    for row in rows:
        attempts = int(row["attempts"])
        closure = int(row["closure_success"])
        strict = int(row["strict_event_successes"])
        events = int(row["events"])
        lines.append(
            f"| {row['condition']} | {closure}/{attempts} ({float(row['closure_accuracy']) * 100:.2f}%) | "
            f"{strict}/{events} | {row['abstain_count']} | {row['wrong_select_count']} |"
        )

    c2 = next((row for row in rows if row["condition"] == "c2-without-id"), None)
    if c2:
        oracle_pct = float(c2["oracle_accuracy"]) * 100
        lines.extend(
            [
                "",
                "## C2 without ID (primary probe)",
                "",
                f"- Oracle accuracy: **{oracle_pct:.2f}%** (chance = 33.33%)",
                f"- Closure: **{float(c2['closure_accuracy']) * 100:.2f}%**",
            ]
        )
        if oracle_pct < 40:
            lines.append("- Interpretation: candidate text alone does not provide a strong shortcut.")
        else:
            lines.append("- Interpretation: candidate text shows elevated shortcut signal; investigate features.")

    report_path = EXP10_ROOT / f"candidate-shortcut-report-{args.phase}.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(report_path.relative_to(PROJECT_DIR))}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
