from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "benchmark/ecr-repair-post-freeze-blind-v1"
RUN = ROOT / "output/ecr-repair-post-freeze-blind-v1/v25-natural-literal-posthoc-r5"
OUT = RUN / "failure-analysis"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def normalized(value: Any) -> str:
    return " ".join(str(value or "").split())


def target_tuple(ir: dict[str, Any]) -> tuple[str, str, str, str]:
    target = ir.get("target", {})
    old = target.get("old_value", {})
    return (
        str(target.get("subject_iri", "")),
        str(target.get("predicate_iri", "")),
        normalized(old.get("lexical", "")),
        str(old.get("datatype", "")),
    )


def replacement_tuple(ir: dict[str, Any]) -> tuple[str, str]:
    value = ir.get("replacement", {}).get("new_value", {})
    return normalized(value.get("lexical", "")), str(value.get("datatype", ""))


def gold_ir(gold: dict[str, Any]) -> dict[str, Any]:
    return {"target": gold["target"], "replacement": gold.get("replacement") or {}}


def truth(value: str) -> bool:
    return value.strip().lower() == "true"


def primary_cause(detail: dict[str, str], raw: dict[str, Any], gold: dict[str, Any], raw_target_exact: bool, raw_replacement_exact: bool) -> str:
    if truth(detail["ses_success"]):
        return "SUCCESS"
    raw_decision = str(raw.get("decision", "INVALID"))
    final_decision = detail["model_decision"]
    if gold["partition"] != "REPAIR":
        if truth(detail["selected"]):
            return "SAFETY_UNSAFE_REPAIR"
        if final_decision != gold["decision"]:
            return "SAFETY_DECISION_CONFUSION"
        return "SAFETY_POST_DECISION_CLOSURE_FAILURE"
    if raw_decision != "REPAIR":
        return "REPAIR_FALSE_ABSTAIN" if raw_decision == "ABSTAIN" else "REPAIR_FALSE_NO_CHANGE"
    if detail["window_resolution_status"] != "RESOLVED":
        return "GROUNDING_AMBIGUOUS" if detail["window_resolution_status"] == "AMBIGUOUS_WINDOW" else "GROUNDING_OTHER_FAILURE"
    if not raw_target_exact:
        return "TARGET_ALIGNMENT_ERROR"
    if not raw_replacement_exact:
        return "REPLACEMENT_VALUE_ERROR"
    if detail["schema_status"] != "VALID":
        return "SCHEMA_VALIDATION_FAILURE"
    if detail["gamma_status"] != "COMPILED" or detail["precondition_status"] != "PASS":
        return "GAMMA_OR_PRECONDITION_FAILURE"
    if detail["reasoner_status"] != "CONSISTENT":
        return "REASONER_FAILURE"
    return "CQ_OR_EXACT_CLOSURE_FAILURE"


def grouped(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[str(row[field])].append(row)
    output = []
    for label, items in sorted(buckets.items()):
        causes = Counter(row["primary_cause"] for row in items)
        output.append({
            field: label,
            "attempts": len(items),
            "successes": causes["SUCCESS"],
            "success_rate": causes["SUCCESS"] / len(items),
            "unsafe_repairs": causes["SAFETY_UNSAFE_REPAIR"],
            "grounding_failures": causes["GROUNDING_AMBIGUOUS"] + causes["GROUNDING_OTHER_FAILURE"],
            "target_errors": causes["TARGET_ALIGNMENT_ERROR"],
            "replacement_errors": causes["REPLACEMENT_VALUE_ERROR"],
            "false_abstain_or_no_change": causes["REPAIR_FALSE_ABSTAIN"] + causes["REPAIR_FALSE_NO_CHANGE"],
            "cause_counts_json": json.dumps(dict(causes), ensure_ascii=False, sort_keys=True),
        })
    return output


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    details = read_csv(RUN / "v6-final-blind-details.csv")
    golds = {row["event_id"]: row for row in (json.loads(line) for line in (BENCHMARK / "private/oracle/gold.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())}
    diagnostics = []
    for detail in details:
        raw_path = RUN / "raw-predicted-ir" / f"{detail['event_id']}-run{detail['run']}-seed{detail['seed']}.json"
        payload = json.loads(raw_path.read_text(encoding="utf-8-sig"))
        raw = payload.get("predicted_ir_raw", {})
        gold = golds[detail["event_id"]]
        exact_target = raw.get("decision") == "REPAIR" and target_tuple(raw) == target_tuple(gold_ir(gold))
        exact_replacement = gold["partition"] == "REPAIR" and raw.get("decision") == "REPAIR" and replacement_tuple(raw) == replacement_tuple(gold_ir(gold))
        diagnostics.append({
            "event_id": detail["event_id"], "run": detail["run"], "seed": detail["seed"],
            "domain": detail["domain"], "semantic_type": detail["semantic_type"], "gold_partition": gold["partition"],
            "gold_decision": gold["decision"], "raw_decision": raw.get("decision", "INVALID"),
            "final_decision": detail["model_decision"], "raw_target_exact": exact_target,
            "raw_replacement_exact": exact_replacement, "grounding_status": detail["window_resolution_status"],
            "schema_status": detail["schema_status"], "selected": truth(detail["selected"]),
            "reasoner_status": detail["reasoner_status"], "target_cq_pass": truth(detail["target_cq_pass"]),
            "ses_success": truth(detail["ses_success"]),
            "primary_cause": primary_cause(detail, raw, gold, exact_target, exact_replacement),
            "raw_new_lexical": raw.get("replacement", {}).get("new_value", {}).get("lexical", ""),
            "gold_new_lexical": (gold.get("replacement") or {}).get("new_value", {}).get("lexical", ""),
        })

    write_csv(OUT / "failure-attempt-details.csv", diagnostics)
    by_cause = []
    cause_counts = Counter(row["primary_cause"] for row in diagnostics)
    for cause, count in cause_counts.most_common():
        event_ids = sorted({row["event_id"] for row in diagnostics if row["primary_cause"] == cause})
        by_cause.append({"primary_cause": cause, "attempts": count, "rate": count / len(diagnostics), "events": len(event_ids), "event_ids": ";".join(event_ids)})
    write_csv(OUT / "failure-by-cause.csv", by_cause)
    write_csv(OUT / "failure-by-partition.csv", grouped(diagnostics, "gold_partition"))
    write_csv(OUT / "failure-by-semantic-type.csv", grouped(diagnostics, "semantic_type"))
    write_csv(OUT / "failure-by-domain.csv", grouped(diagnostics, "domain"))

    event_rows = []
    for event_id in sorted(golds):
        items = [row for row in diagnostics if row["event_id"] == event_id]
        causes = Counter(row["primary_cause"] for row in items)
        event_rows.append({
            "event_id": event_id, "domain": items[0]["domain"], "semantic_type": items[0]["semantic_type"],
            "gold_partition": items[0]["gold_partition"], "successes": causes["SUCCESS"],
            "strict_event_success": causes["SUCCESS"] == len(items),
            "dominant_cause": causes.most_common(1)[0][0], "cause_counts_json": json.dumps(dict(causes), ensure_ascii=False, sort_keys=True),
        })
    write_csv(OUT / "failure-by-event.csv", event_rows)

    payload = {
        "status": "POST_HOC_FAILURE_ANALYSIS",
        "attempts": len(diagnostics), "events": len(golds), "cause_counts": dict(cause_counts),
        "raw_exact_repair_attempts": sum(row["raw_replacement_exact"] and row["raw_target_exact"] for row in diagnostics),
        "strict_success_events": sum(row["strict_event_success"] for row in event_rows),
        "interpretation_boundary": "Descriptive post-hoc attribution; not a new confirmatory experiment.",
    }
    (OUT / "failure-analysis.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# V2.5 blind-set failure attribution", "", "This is a descriptive post-hoc analysis of 400 frozen raw outputs.", "",
        "## Primary causes", "", "| Cause | Attempts | Rate | Events |", "|---|---:|---:|---:|",
    ]
    for row in by_cause:
        lines.append(f"| {row['primary_cause']} | {row['attempts']} | {row['rate']:.2%} | {row['events']} |")
    lines += ["", "## Boundary", "", "Counts are mutually exclusive primary causes. The analysis does not alter Gold, raw predictions, V2.4, or V2.5."]
    (OUT / "failure-analysis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
