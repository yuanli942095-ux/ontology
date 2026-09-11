from __future__ import annotations

"""Build Exp11 strict provenance report."""

import argparse
import csv
import json
from pathlib import Path

from semantic_v2_common import PROJECT_DIR


EXP11_ROOT = PROJECT_DIR / "output" / "paper-final-validation" / "11-strict-provenance"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", default="formal")
    args = parser.parse_args()

    summary_path = EXP11_ROOT / f"strict-provenance-summary-{args.phase}.csv"
    gold_summary = EXP11_ROOT / "gold-summary.json"
    if not summary_path.is_file():
        raise FileNotFoundError(summary_path)
    row = read_csv(summary_path)[0]
    gold = json.loads(gold_summary.read_text(encoding="utf-8")) if gold_summary.is_file() else {}

    lines = [
        "# Exp11 Strict Historical Provenance Report",
        "",
        f"Phase: `{args.phase}`",
        "",
        "## Subset construction",
        "",
        f"- Selected events: **{gold.get('selected_events', row.get('events', ''))}** (eligible TEMP: {gold.get('eligible_temp_events', '')})",
        f"- ID permute seed: **20260906**",
        f"- Admission: heuristic TEMP + resolvable t0 RFC in cache + oracle replacement",
        "",
        "## ECR results",
        "",
        f"- Attempts: **{row['attempts']}**",
        f"- Closure: **{int(row['closure_success'])}/{row['attempts']}** ({float(row['closure_accuracy']) * 100:.2f}%)",
        f"- Oracle: **{float(row['oracle_accuracy']) * 100:.2f}%**",
        f"- Strict events: **{row['strict_event_successes']}/{row['events']}** ({float(row['strict_event_accuracy']) * 100:.2f}%)",
        f"- Abstain: **{row['abstain_count']}**",
        f"- Wrong-select: **{row['wrong_select_count']}**",
        "",
        "## Interpretation",
        "",
        "This subset tests strict historical temporal-drift provenance (t0 vs t1 RFC), "
        "not the full 264-event blind holdout.",
    ]
    report = EXP11_ROOT / f"strict-provenance-report-{args.phase}.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(report.relative_to(PROJECT_DIR))}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
