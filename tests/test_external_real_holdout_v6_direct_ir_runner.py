import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import json

from run_external_real_holdout_v6_direct_ir_blind import (
    BENCHMARK,
    SEEDS,
    load_events,
    summarize_rows,
)
from ecr_ir_gamma_v22_gate import repair_is_no_change_equivalent


def test_v6_manifest_contract() -> None:
    events = load_events(BENCHMARK)
    assert len(events) == 300
    assert len(SEEDS) == 5
    assert len(set(SEEDS)) == 5


def test_repair_and_safety_counts() -> None:
    events = load_events(BENCHMARK)
    assert {event["semantic_type"] for event in events.values()} == {"NORMATIVE_EVIDENCE_ASSESSMENT"}
    gold = [json.loads(line) for line in (BENCHMARK / "private/oracle/gold-repair-ir.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sum(row["decision"] == "REPAIR" for row in gold) == 240
    assert sum(row["decision"] != "REPAIR" for row in gold) == 60


def test_summary_keeps_partitions_separate() -> None:
    rows = [
        {"event_id": "R", "partition": "REPAIR", "semantic_type": "TEMPORAL_VERSION", "selected": True, "wrong_repair": False, "ses_success": True, "model_decision": "REPAIR"},
        {"event_id": "S", "partition": "SAFETY", "semantic_type": "NO_CHANGE", "selected": False, "wrong_repair": False, "ses_success": True, "model_decision": "NO_CHANGE"},
    ]
    result = {row["group"]: row for row in summarize_rows(rows)}
    assert result["REPAIR"]["attempts"] == 1
    assert result["SAFETY"]["attempts"] == 1


def test_repair_no_change_equivalence_gate() -> None:
    ir = {
        "decision": "REPAIR",
        "target": {
            "old_value": {
                "kind": "literal",
                "lexical": "network_routing_h6_e025_claim=note_options_processed_only_final_destination_packet",
                "datatype": "http://www.w3.org/2001/XMLSchema#string",
            }
        },
        "replacement": {
            "new_value": {
                "kind": "literal",
                "lexical": "network_routing_h6_e025_claim=note_options_processed_only_final_destination_packet",
                "datatype": "http://www.w3.org/2001/XMLSchema#string",
            }
        },
    }
    assert repair_is_no_change_equivalent(ir)
    ir["replacement"]["new_value"]["lexical"] = "network_routing_h6_e025_claim=changed"
    assert not repair_is_no_change_equivalent(ir)
