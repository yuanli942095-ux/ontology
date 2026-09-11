from __future__ import annotations

import csv
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

from semantic_v2_common import PROJECT_DIR, write_csv


ROOT = PROJECT_DIR / "output/external-real-holdout-v6-2-direct-ir-blind"
OLD = ROOT / "final-blind-r2b-v2-r5/v6-final-blind-details.csv"
NEW = ROOT / "final-v24-full-closure-r5/v6-final-blind-details.csv"
OUT = ROOT / "final-v24-full-closure-r5"


def read(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def flag(value: str) -> bool:
    return value.lower() == "true"


def mcnemar(b: int, c: int) -> float:
    n = b + c
    if not n:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, k) for k in range(min(b, c) + 1)) / 2**n)


def bootstrap_event_delta(rows: list[dict], samples: int = 10000) -> list[float]:
    groups = defaultdict(list)
    for row in rows:
        groups[row["event_id"]].append(row)
    event_ids = sorted(groups)
    rng = random.Random(20260909)
    values = []
    for _ in range(samples):
        picked = [groups[event_ids[rng.randrange(len(event_ids))]] for _ in event_ids]
        flat = [row for group in picked for row in group]
        values.append(sum(int(r["v24_success"]) - int(r["v22_success"]) for r in flat) / len(flat))
    values.sort()
    return [values[int(samples * 0.025)], values[int(samples * 0.975)]]


def main() -> int:
    old = {(r["event_id"], r["run"], r["seed"]): r for r in read(OLD)}
    new = {(r["event_id"], r["run"], r["seed"]): r for r in read(NEW)}
    if old.keys() != new.keys():
        raise RuntimeError("paired keys differ")
    rows = []
    for key in sorted(old):
        a, z = old[key], new[key]
        rows.append({
            "event_id": key[0], "run": key[1], "seed": key[2], "partition": a["partition"],
            "semantic_type": a["semantic_type"], "domain": a["domain"],
            "v22_success": flag(a["ses_success"]), "v24_success": flag(z["ses_success"]),
            "v22_wrong_repair": flag(a["wrong_repair"]), "v24_wrong_repair": flag(z["wrong_repair"]),
            "v22_decision": a["model_decision"], "v24_decision": z["model_decision"],
            "v24_resolution_status": z["window_resolution_status"],
            "v24_resolution_path": z.get("resolution_path", ""),
        })
    b = sum(r["v22_success"] and not r["v24_success"] for r in rows)
    c = sum(not r["v22_success"] and r["v24_success"] for r in rows)
    improved = [r for r in rows if not r["v22_success"] and r["v24_success"]]
    regressed = [r for r in rows if r["v22_success"] and not r["v24_success"]]
    summary = {
        "pairs": len(rows), "events": len({r["event_id"] for r in rows}),
        "v22_successes": sum(r["v22_success"] for r in rows), "v24_successes": sum(r["v24_success"] for r in rows),
        "absolute_ses_delta": (c - b) / len(rows), "event_bootstrap_95ci": bootstrap_event_delta(rows),
        "new_successes": c, "regressions": b, "mcnemar_exact_p": mcnemar(b, c),
        "v22_wrong_repairs": sum(r["v22_wrong_repair"] for r in rows), "v24_wrong_repairs": sum(r["v24_wrong_repair"] for r in rows),
        "improved_events": dict(Counter(r["event_id"] for r in improved)),
        "improved_partitions": dict(Counter(r["partition"] for r in improved)),
        "regressed_events": dict(Counter(r["event_id"] for r in regressed)),
    }
    write_csv(OUT / "v22-v24-paired-details.csv", rows)
    (OUT / "v22-v24-paired-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    report = f"""# Frozen V2.4 full-closure paired result

- Pairs: {len(rows)} (300 events x 5 runs)
- SES: {summary['v22_successes']}/1500 ({summary['v22_successes']/1500:.2%}) -> {summary['v24_successes']}/1500 ({summary['v24_successes']/1500:.2%})
- Absolute delta: {summary['absolute_ses_delta']:+.2%}; event-bootstrap 95% CI [{summary['event_bootstrap_95ci'][0]:+.2%}, {summary['event_bootstrap_95ci'][1]:+.2%}]
- Wrong repairs: {summary['v22_wrong_repairs']} -> {summary['v24_wrong_repairs']}
- New successes: {c}; regressions: {b}; exact McNemar p={summary['mcnemar_exact_p']:.5f}
- Improved events: {summary['improved_events']}

V2.4 reused identical candidate-blind raw outputs. The comparison isolates grounding/postprocessing effects and includes unchanged OWL materialization, reasoner, and CQ closure.
"""
    (OUT / "v22-v24-paired-report.md").write_text(report, encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
