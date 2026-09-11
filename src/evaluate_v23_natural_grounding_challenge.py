from __future__ import annotations

import csv
import json
import math
from pathlib import Path

from rfc213_direct_repair_ir_v2 import resolve_source_window
from rfc213_direct_repair_ir_v23 import resolve_source_window_v23
from rfc213_direct_repair_ir_v24 import resolve_source_window_v24
from semantic_v2_common import PROJECT_DIR, write_csv


BENCHMARK = PROJECT_DIR / "benchmark/v24-natural-grounding-confirmatory"
OUTPUT = PROJECT_DIR / "output/v24-natural-grounding-confirmatory"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def exact_mcnemar(b: int, c: int) -> float:
    n = b + c
    if not n:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, k) for k in range(min(b, c) + 1)) / 2**n)


def main() -> int:
    events = {row["event_id"]: row for row in read_csv(BENCHMARK / "public/events.csv")}
    gold = {row["event_id"]: row for row in read_csv(BENCHMARK / "private/gold.csv")}
    fixtures = json.loads((BENCHMARK / "frozen-grounding-inputs.json").read_text(encoding="utf-8"))
    rows = []
    for fixture in fixtures:
        event_id = fixture["event_id"]
        evidence = (BENCHMARK / events[event_id]["evidence_file"]).read_text(encoding="utf-8")
        expected = gold[event_id]["gold_decision"]
        outcomes = {}
        for version, resolver in (("v22", resolve_source_window), ("v23", resolve_source_window_v23), ("v24", resolve_source_window_v24)):
            result = resolver(fixture["predicted_ir"], evidence)
            decision = "RESOLVE" if result["status"] == "RESOLVED" else "ABSTAIN"
            outcomes[version] = {"decision": decision, "status": result["status"], "success": decision == expected, "path": result.get("resolution_path", "V22")}
        rows.append({
            **events[event_id], "gold_decision": expected,
            "v22_decision": outcomes["v22"]["decision"], "v22_status": outcomes["v22"]["status"], "v22_success": outcomes["v22"]["success"],
            "v23_decision": outcomes["v23"]["decision"], "v23_status": outcomes["v23"]["status"], "v23_path": outcomes["v23"]["path"], "v23_success": outcomes["v23"]["success"],
            "v24_decision": outcomes["v24"]["decision"], "v24_status": outcomes["v24"]["status"], "v24_path": outcomes["v24"]["path"], "v24_success": outcomes["v24"]["success"],
            "v22_wrong_repair": expected == "ABSTAIN" and outcomes["v22"]["decision"] == "RESOLVE",
            "v23_wrong_repair": expected == "ABSTAIN" and outcomes["v23"]["decision"] == "RESOLVE",
            "v24_wrong_repair": expected == "ABSTAIN" and outcomes["v24"]["decision"] == "RESOLVE",
        })
    n = len(rows)
    b = sum(r["v23_success"] and not r["v24_success"] for r in rows)
    c = sum(not r["v23_success"] and r["v24_success"] for r in rows)
    refs = [r for r in rows if r["category"] == "REFERENCE_NONASSERTION"]
    tables = [r for r in rows if r["category"] == "ASCII_TABLE"]
    controls = [r for r in rows if r["category"] == "NORMATIVE_CONTROL"]
    summary = {
        "events": n,
        "v22_ses": sum(r["v22_success"] for r in rows) / n,
        "v23_ses": sum(r["v23_success"] for r in rows) / n,
        "v24_ses": sum(r["v24_success"] for r in rows) / n,
        "v22_wrong_repairs": sum(r["v22_wrong_repair"] for r in rows),
        "v23_wrong_repairs": sum(r["v23_wrong_repair"] for r in rows),
        "v24_wrong_repairs": sum(r["v24_wrong_repair"] for r in rows),
        "reference_gate_sensitivity": sum(r["v24_decision"] == "ABSTAIN" for r in refs) / len(refs),
        "table_grounding_recall": sum(r["v24_decision"] == "RESOLVE" for r in tables) / len(tables),
        "normative_control_false_block_rate": sum(r["v24_decision"] == "ABSTAIN" for r in controls) / len(controls),
        "mcnemar_b": b, "mcnemar_c": c, "mcnemar_exact_p": exact_mcnemar(b, c),
        "metric_scope": "component-level semantic execution success; no LLM generation or OWL reasoner is exercised",
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUTPUT / "v22-v23-v24-details.csv", rows)
    (OUTPUT / "v22-v23-v24-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    report = f"""# V2.4 natural grounding confirmatory challenge

- Events: {n} from {len(set(r['rfc'] for r in rows))} previously unused RFCs
- V2.2 component SES: {summary['v22_ses']:.2%}
- V2.3 component SES: {summary['v23_ses']:.2%}
- V2.4 component SES: {summary['v24_ses']:.2%}
- Wrong repairs (V2.2/V2.3/V2.4): {summary['v22_wrong_repairs']} / {summary['v23_wrong_repairs']} / {summary['v24_wrong_repairs']}
- Reference gate sensitivity: {summary['reference_gate_sensitivity']:.2%}
- ASCII-table grounding recall: {summary['table_grounding_recall']:.2%}
- Normative-control false-block rate: {summary['normative_control_false_block_rate']:.2%}
- Exact McNemar: b={b}, c={c}, p={summary['mcnemar_exact_p']:.8f}

These are component-level grounding results over frozen deterministic inputs. They do not replace the end-to-end 213-event OWL/reasoner/CQ result.
"""
    (OUTPUT / "v22-v23-v24-report.md").write_text(report, encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
