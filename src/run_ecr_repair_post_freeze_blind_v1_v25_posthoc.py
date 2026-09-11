from __future__ import annotations

"""Offline post-hoc V2.5 compatibility evaluation of existing blind raw IR."""

import argparse
import csv
import json
import shutil
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import run_external_real_holdout_v6_direct_ir_blind as engine
from rfc213_direct_repair_ir_v25 import resolve_source_window_v25
from run_ecr_repair_post_freeze_blind_v1 import BENCHMARK, METHOD_MANIFEST, verify
from semantic_v2_common import write_csv


SOURCE = Path("output/ecr-repair-post-freeze-blind-v1/final-v24-r5")
OUTPUT = Path("output/ecr-repair-post-freeze-blind-v1/v25-natural-literal-posthoc-r5")


def as_bool(value: Any) -> bool:
    return value is True or str(value).strip().lower() == "true"


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for label in {"ALL", row["partition"], row["semantic_type"], row["gold_partition"]}:
            groups[label].append(row)
    labels = ("ALL", "REPAIR", "SAFETY", "TEMPORAL_VERSION", "GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE", "NO_CHANGE", "INSUFFICIENT_EVIDENCE", "CONFLICTING_EVIDENCE")
    output = []
    for label in labels:
        items = groups.get(label, [])
        events: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in items:
            events[row["event_id"]].append(row)
        attempts = len(items)
        selected = sum(as_bool(r["selected"]) for r in items)
        wrong = sum(as_bool(r["wrong_repair"]) for r in items)
        successes = sum(as_bool(r["ses_success"]) for r in items)
        strict = sum(all(as_bool(x["ses_success"]) for x in values) for values in events.values())
        output.append({
            "group": label, "events": len(events), "attempts": attempts, "successes": successes,
            "success_rate": successes / attempts if attempts else 0, "selected": selected,
            "coverage": selected / attempts if attempts else 0, "wrong_repairs": wrong,
            "wrr": wrong / attempts if attempts else 0, "selective_risk": wrong / selected if selected else 0,
            "abstains": sum(r["model_decision"] == "ABSTAIN" for r in items),
            "no_change": sum(r["model_decision"] == "NO_CHANGE" for r in items),
            "strict_event_successes": strict,
            "strict_event_accuracy": strict / len(events) if events else 0,
        })
    return output


def apply_fail_closed_safety_semantics(output: Path, gold: dict[str, dict]) -> None:
    details_path = output / "v6-final-blind-details.csv"
    with details_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        expected = gold[row["event_id"]]["decision"]
        grounding_failed = row["window_resolution_status"] not in {"RESOLVED", "NOT_APPLICABLE"}
        if expected == "ABSTAIN" and grounding_failed and row["model_decision"] == "ABSTAIN" and row["selected"].lower() != "true":
            row["decision_correct"] = True
            row["target_cq_pass"] = True
            row["non_target_cq_preserved"] = True
            row["ses_success"] = True
            row["failure_stage"] = ""
    write_csv(details_path, rows)
    summaries = summarize(rows)
    write_csv(output / "v6-final-blind-summary.csv", summaries)
    (output / "v6-final-blind-summary.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=SOURCE)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    method, events, gold = verify()
    output = args.output_dir.resolve()
    source = args.source_dir.resolve()
    raw_files = list((source / "raw-predicted-ir").glob("*.json"))
    if len(raw_files) != 400 or any(json.loads(path.read_text(encoding="utf-8-sig")).get("status") != "GENERATED" for path in raw_files):
        raise SystemExit("expected exactly 400 GENERATED raw artifacts")
    output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / "v6-final-blind-manifest-r5.csv", output / "v6-final-blind-manifest-r5.csv")
    target_raw = output / "raw-predicted-ir"
    target_raw.mkdir(exist_ok=True)
    for path in raw_files:
        destination = target_raw / path.name
        if not destination.exists():
            shutil.copy2(path, destination)

    original_base_row = engine.base_row
    def base_row(item: dict[str, str], row_gold: dict[str, Any]) -> dict[str, Any]:
        row = original_base_row(item, row_gold)
        row["gold_partition"] = row_gold["partition"]
        return row
    engine.base_row = base_row
    engine.resolve_source_window_v24 = resolve_source_window_v25
    engine.load_gold = lambda _benchmark: gold
    engine.summarize_rows = summarize
    eval_args = SimpleNamespace(timeout=args.timeout, resolver="v24")
    engine.evaluate(eval_args, BENCHMARK, output, events)
    apply_fail_closed_safety_semantics(output, gold)
    protocol = {
        "status": "POST_HOC_COMPATIBILITY_REEVALUATION",
        "method": "ECR-REPAIR-V2.5-NATURAL-LITERAL-ADAPTER",
        "parent_method_manifest": str(METHOD_MANIFEST),
        "model_calls": 0,
        "raw_artifacts_reused": 400,
        "development_timing": "Adapter specified after observing aggregate V2.4 incompatibility; not confirmatory.",
    }
    (output / "evaluation-protocol.json").write_text(json.dumps(protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
