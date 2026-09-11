from __future__ import annotations

"""Finalize and freeze the adjudicated 80-event post-method blind benchmark."""

import csv
import hashlib
import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ecr_repair_post_freeze_blind_v1_common import BENCHMARK_DIR, PROJECT_DIR, read_jsonl
from scan_ecr_repair_post_freeze_blind_v1_leakage import scan as scan_leakage

EXPECTED_IDS = {f"BLIND_E{i:03d}" for i in range(1, 81)}
REALIZED_PARTITIONS = {"REPAIR": 56, "NO_CHANGE": 8, "INSUFFICIENT_EVIDENCE": 11, "CONFLICTING_EVIDENCE": 5}
CONSTRUCTION = BENCHMARK_DIR / "private/construction"
ANNOTATION = BENCHMARK_DIR / "private/annotation"
ADJUDICATION = BENCHMARK_DIR / "private/adjudication"
ORACLE = BENCHMARK_DIR / "private/oracle"
OFFICIAL_REPAIRS = BENCHMARK_DIR / "private/gold-repaired-owl"
MANIFEST = BENCHMARK_DIR / "freeze-manifest.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def verify_gates() -> tuple[dict[str, bool], list[dict]]:
    events = read_jsonl(BENCHMARK_DIR / "public/events/events.jsonl")
    gold = read_jsonl(CONSTRUCTION / "adjudicated-gold.jsonl")
    closure = csv_rows(CONSTRUCTION / "adjudicated-closure.csv")
    round2_disagreements = csv_rows(ANNOTATION / "round2-64/disagreements.csv")
    round2_adjudication = csv_rows(ADJUDICATION / "adjudication-template.csv")
    supplemental_adjudication = csv_rows(ADJUDICATION / "adjudication-supplemental-template.csv")
    roster = load_json(ANNOTATION / "annotator-roster.json")
    attestation = load_json(ANNOTATION / "round2-64/independence-attestation-template.json")
    leakage = scan_leakage()
    all_adjudication = round2_adjudication + supplemental_adjudication
    registered = {item["role"] for item in roster["annotators"] if item.get("status") == "REGISTERED"}
    partitions = Counter(row["partition"] for row in gold)
    repaired_files = list((CONSTRUCTION / "adjudicated-gold-repaired-owl").glob("BLIND_E*.owl"))
    gates = {
        "exactly_80_public_events": len(events) == 80 and {row["event_id"] for row in events} == EXPECTED_IDS,
        "exactly_80_adjudicated_gold": len(gold) == 80 and {row["event_id"] for row in gold} == EXPECTED_IDS,
        "realized_partition_distribution": dict(partitions) == REALIZED_PARTITIONS,
        "all_three_human_roles_registered": {"ANN_A", "ANN_B", "ADJ_C"} <= registered,
        "round2_independence_attested": all(attestation.get(key) is True for key in (
            "attested_by_annotator_a", "attested_by_annotator_b", "ann_a_and_ann_b_are_different_humans",
            "annotations_completed_without_communication", "annotators_did_not_view_each_others_completed_sheets",
            "annotators_did_not_view_proposed_gold_or_oracle", "annotators_did_not_view_candidates_or_v24_outputs")),
        "all_recorded_disagreements_adjudicated": bool(all_adjudication) and all(
            row.get("adjudicated_value", "").strip() and row.get("adjudicator_id", "").strip() == "ADJ_C"
            for row in all_adjudication),
        "round2_disagreements_have_decisions": (
            {(row["event_id"], row["field"]) for row in round2_adjudication}
            == {(row["event_id"], row["field"]) for row in round2_disagreements}
        ),
        "supplemental_decisions_complete": len(supplemental_adjudication) == 6 and all(
            row.get("adjudicated_value", "").strip() and row.get("adjudicator_id", "").strip() == "ADJ_C"
            for row in supplemental_adjudication
        ),
        "gold_closure_80_of_80": len(closure) == 80 and all(row["status"] in {"PASS", "SAFE_NO_EDIT"} for row in closure),
        "repair_artifacts_56": len(repaired_files) == 56,
        "public_gold_leakage_pass": leakage["PUBLIC_GOLD_LEAKAGE"] == "PASS",
        "v24_not_run_in_benchmark": not (BENCHMARK_DIR / "output").exists(),
    }
    return gates, gold


def main() -> int:
    gates, gold = verify_gates()
    failed = [name for name, passed in gates.items() if not passed]
    if failed:
        print(json.dumps({"status": "FREEZE_REFUSED", "failed_gates": failed, "gates": gates}, indent=2))
        return 1

    ORACLE.mkdir(parents=True, exist_ok=True)
    OFFICIAL_REPAIRS.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CONSTRUCTION / "adjudicated-gold.jsonl", ORACLE / "gold.jsonl")
    for path in sorted((CONSTRUCTION / "adjudicated-gold-repaired-owl").glob("BLIND_E*.owl")):
        shutil.copy2(path, OFFICIAL_REPAIRS / path.name)

    frozen_at = datetime.now(timezone.utc).isoformat()
    audit_note = {
        "schema_version": "ecr-repair-post-freeze-blind-v1-finalization-v1",
        "status": "FROZEN_POST_METHOD_BLIND_TEST",
        "frozen_at_utc": frozen_at,
        "event_count": 80,
        "registered_partition_target": {"REPAIR": 56, "NO_CHANGE": 8, "INSUFFICIENT_EVIDENCE": 8, "CONFLICTING_EVIDENCE": 8},
        "realized_adjudicated_partitions": REALIZED_PARTITIONS,
        "deviation_reason": (
            "Independent ANN_A, ANN_B, and ADJ_C decisions classified E055, E056, and E063 as "
            "INSUFFICIENT_EVIDENCE rather than CONFLICTING_EVIDENCE. The decisions were retained. "
            "Post-adjudication candidates E081-E083 were independently rejected and excluded."),
        "method_evaluation_before_freeze": False,
        "rejected_candidate_ids": ["BLIND_E081", "BLIND_E082", "BLIND_E083"],
        "gates": gates,
    }
    (CONSTRUCTION / "finalization-audit.json").write_text(json.dumps(audit_note, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    files = []
    for path in sorted(BENCHMARK_DIR.rglob("*")):
        if path.is_file() and path != MANIFEST:
            files.append({"path": path.relative_to(BENCHMARK_DIR).as_posix(), "sha256": sha256(path), "bytes": path.stat().st_size})
    payload = {
        "schema_version": "ecr-repair-post-freeze-blind-v1-freeze-v1",
        "benchmark_id": "ecr-repair-post-freeze-blind-v1",
        "status": "FROZEN_POST_METHOD_BLIND_TEST",
        "frozen_at_utc": frozen_at,
        "event_count": 80,
        "partition_counts": dict(Counter(row["partition"] for row in gold)),
        "method_id_reserved_for_post_freeze_evaluation": "ECR-REPAIR-V2.4-FINAL",
        "method_run_before_benchmark_freeze": False,
        "freeze_gates": gates,
        "files": files,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    MANIFEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"], "event_count": 80, "partition_counts": payload["partition_counts"],
        "official_oracle_sha256": sha256(ORACLE / "gold.jsonl"), "official_repair_files": 56,
        "manifest": MANIFEST.relative_to(PROJECT_DIR).as_posix(), "manifest_sha256": payload["manifest_sha256"],
        "files_hashed": len(files)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
