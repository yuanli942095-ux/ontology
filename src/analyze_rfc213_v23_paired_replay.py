from __future__ import annotations

import csv
import json
import math
import random
from collections import Counter
from pathlib import Path

from semantic_v2_common import PROJECT_DIR, write_csv


BASE = PROJECT_DIR / "output/rfc-213-confirmatory-core/v23-confirmatory"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def truth(value: str) -> bool:
    return value.strip().lower() == "true"


def exact_mcnemar(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(0, min(b, c) + 1)) / (2**n)
    return min(1.0, 2 * tail)


def bootstrap_delta(rows: list[dict[str, object]], key: str, samples: int = 10000) -> tuple[float, float]:
    rng = random.Random(20260909)
    n = len(rows)
    deltas = []
    for _ in range(samples):
        picked = [rows[rng.randrange(n)] for _ in range(n)]
        deltas.append(sum(int(r[f"v23_{key}"]) - int(r[f"v22_{key}"]) for r in picked) / n)
    deltas.sort()
    return deltas[int(samples * 0.025)], deltas[int(samples * 0.975)]


def main() -> int:
    v22 = read_csv(BASE / "v22/r2b-v22-window-resolution-details.csv")
    v23 = read_csv(BASE / "v23/r2b-v23-window-resolution-details.csv")
    old = {(r["event_id"], r["run"], r["seed"]): r for r in v22}
    new = {(r["event_id"], r["run"], r["seed"]): r for r in v23}
    if old.keys() != new.keys():
        raise ValueError("V2.2 and V2.3 pair keys differ")

    paired: list[dict[str, object]] = []
    for pair_key in sorted(old):
        a, b = old[pair_key], new[pair_key]
        paired.append({
            "event_id": pair_key[0], "run": pair_key[1], "seed": pair_key[2],
            "semantic_type": a["semantic_type"], "domain": a["domain"],
            "v22_ses": truth(a["ses_success"]), "v23_ses": truth(b["ses_success"]),
            "v22_wrong_repair": truth(a["wrong_repair"]), "v23_wrong_repair": truth(b["wrong_repair"]),
            "v22_decision": a["decision"], "v23_decision": b["decision"],
            "v22_resolution": a["window_resolution_status"], "v23_resolution": b["window_resolution_status"],
            "v23_resolution_path": b.get("resolution_path", ""),
            "changed": any(a.get(k, "") != b.get(k, "") for k in ("decision", "window_resolution_status", "ses_success", "wrong_repair")),
        })

    b = sum(bool(r["v22_ses"]) and not bool(r["v23_ses"]) for r in paired)
    c = sum(not bool(r["v22_ses"]) and bool(r["v23_ses"]) for r in paired)
    low, high = bootstrap_delta(paired, "ses")
    summary = {
        "protocol_id": "RFC213-V23-PAIRED-REPLAY-20260909",
        "pairs": len(paired),
        "v22_ses": sum(bool(r["v22_ses"]) for r in paired) / len(paired),
        "v23_ses": sum(bool(r["v23_ses"]) for r in paired) / len(paired),
        "ses_delta": (c - b) / len(paired),
        "ses_delta_bootstrap_95ci": [low, high],
        "v22_wrong_repairs": sum(bool(r["v22_wrong_repair"]) for r in paired),
        "v23_wrong_repairs": sum(bool(r["v23_wrong_repair"]) for r in paired),
        "correct_repair_regressions": b,
        "new_successes": c,
        "mcnemar_b": b,
        "mcnemar_c": c,
        "mcnemar_exact_p": exact_mcnemar(b, c),
        "changed_pairs": sum(bool(r["changed"]) for r in paired),
        "v23_resolution_paths": dict(Counter(str(r["v23_resolution_path"]) for r in paired)),
        "interpretation": "V2.3 is non-inferior descriptively on this one-run paired replay, but the core contains no triggering case and therefore supplies no efficacy evidence for either added safeguard."
    }
    write_csv(BASE / "v23-paired-event-details.csv", paired)
    (BASE / "v23-paired-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    report = f"""# RFC-213 V2.2 vs V2.3 paired replay

Protocol: `RFC213-V23-PAIRED-REPLAY-20260909`

## Result

- Paired attempts: {len(paired)} (213 events x 1 frozen seed)
- V2.2 SES: {sum(bool(r['v22_ses']) for r in paired)}/213 ({summary['v22_ses']:.2%})
- V2.3 SES: {sum(bool(r['v23_ses']) for r in paired)}/213 ({summary['v23_ses']:.2%})
- SES delta: {summary['ses_delta']:+.2%}; event bootstrap 95% CI [{low:+.2%}, {high:+.2%}]
- Wrong repairs: {summary['v22_wrong_repairs']} -> {summary['v23_wrong_repairs']}
- Correct-repair regressions: {b}; new successes: {c}
- Exact McNemar p: {summary['mcnemar_exact_p']:.4f}
- Pairs with an outcome or resolution-status change: {summary['changed_pairs']}

## Interpretation

V2.3 caused no regression on the independent RFC core. However, neither added safeguard triggered, so this replay does not independently confirm efficacy. The reference and ASCII-table gains remain post-hoc diagnostic evidence from v6.2 until a separately frozen challenge set containing naturally occurring instances is evaluated.

This is a postprocessor comparison on one pre-existing model run, not a five-run LLM robustness experiment.
"""
    (BASE / "v23-paired-report.md").write_text(report, encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
