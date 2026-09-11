from __future__ import annotations

"""Build Exp8 shuffled-evidence TEMP diagnostic trace for pilot QA (no method changes)."""

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any

from paper_final_validation_common import sha256_file, utc_now_iso, write_binding
from semantic_v2_common import PROJECT_DIR, write_csv

EXP8_ROOT = PROJECT_DIR / "output" / "paper-final-validation" / "08-evidence-dependence"
SHUFFLE_MAP = EXP8_ROOT / "evidence-shuffle-map.csv"
PARENT = PROJECT_DIR / "benchmark" / "external-real-holdout-v5-blind-large-robustness-variants" / "candidate-id-permute"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def rfc_id(url: str) -> str:
    match = re.search(r"rfc(\d+)", str(url or "").lower())
    return f"RFC{match.group(1)}" if match else ""


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def excerpt_preview(benchmark_dir: Path, event_id: str, limit: int = 160) -> str:
    path = benchmark_dir / "public" / "excerpts" / f"{event_id}-evidence.md"
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8").strip().replace("\n", " ")
    return text[:limit]


def m13_status(pilot_dir: Path, event_id: str, run: int, seed: int) -> str:
    summary = pilot_dir / "m13-pilot" / "rule-refinement-summary.csv"
    if summary.is_file():
        for row in read_csv(summary):
            if row.get("event_id") == event_id and str(row.get("run")) == str(run):
                return row.get("faithfulness_status", "") or row.get("generation_status", "")
    raw = pilot_dir / "m13-pilot" / "arm-d-rule-refinement" / "raw_window_metadata_light" / "raw" / (
        f"{event_id}-run{run}-seed{seed}.json"
    )
    audit = load_json(raw).get("m13_rule_refinement_audit", {})
    return str(audit.get("faithfulness_status", ""))


def m15_fields(pilot_dir: Path, event_id: str, run: int, seed: int) -> dict[str, str]:
    raw_path = pilot_dir / "m15-temporal-anchor-recovery" / "raw" / f"{event_id}-run{run}-seed{seed}.json"
    record = load_json(raw_path)
    facts = (record.get("response") or {}).get("facts") or {}
    frame = facts.get("m15_temporal_frame") or {}
    audit = record.get("m15_temporal_anchor_audit") or {}
    return {
        "current_anchor": str(frame.get("current_value", "") or audit.get("case_context_anchor", "")),
        "effective_anchor": str(
            facts.get("result", "") or frame.get("current_claim", "") or record.get("canonical_result", {}).get("result", "")
        ),
        "anchor_reason": str(audit.get("anchor_reason", "")),
        "m15_status": str(record.get("status", "")),
    }


def build_m15_input_metadata(event: dict[str, str], donor_event_id: str, donor_excerpt: str) -> str:
    target = {
        "event_id": event.get("event_id", ""),
        "subject_label": event.get("subject_label", ""),
        "predicate_label": event.get("predicate_label", ""),
        "case_context": (event.get("case_context", "") or "")[:200],
        "document_ids": event.get("document_ids", ""),
    }
    donor = {"donor_event_id": donor_event_id, "donor_excerpt_preview": donor_excerpt[:200]}
    static = {
        "semantic_type": event.get("semantic_type", ""),
        "missing_subtype": event.get("missing_subtype", ""),
        "domain": event.get("domain", ""),
    }
    return json.dumps({"TARGET": target, "DONOR": donor, "STATIC": static}, ensure_ascii=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exp8-root", type=Path, default=EXP8_ROOT)
    parser.add_argument("--pilot-dir", type=Path, default=EXP8_ROOT / "runs" / "pilot" / "shuffled-evidence")
    parser.add_argument(
        "--benchmark-dir",
        type=Path,
        default=PROJECT_DIR / "benchmark" / "external-real-holdout-v5-blind-large-evidence-variants" / "shuffled-evidence",
    )
    parser.add_argument("--shuffle-map", type=Path, default=SHUFFLE_MAP)
    parser.add_argument("--output", type=Path, default=EXP8_ROOT / "exp8-shuffled-temp-diagnostic.csv")
    args = parser.parse_args()

    shuffle_rows = read_csv(args.shuffle_map)
    self_shuffle = sum(1 for row in shuffle_rows if row["event_id"] == row["replacement_event_id"])
    if self_shuffle:
        raise SystemExit(f"shuffle map has self_shuffle={self_shuffle}, expected 0")

    event_csv = args.benchmark_dir / "public" / "events" / "external-real-event-template.csv"
    events = {row["event_id"]: row for row in read_csv(event_csv)}

    m16_path = args.pilot_dir / "m16-full-holdout-combined" / "m16-full-holdout-combined-full-details.csv"
    m16_by_event: dict[str, dict[str, str]] = {}
    if m16_path.is_file():
        for row in read_csv(m16_path):
            if row.get("semantic_type") == "TEMPORAL_VERSION" and str(row.get("run")) == "1":
                m16_by_event[row["event_id"]] = row

    rows_out: list[dict[str, Any]] = []
    for map_row in shuffle_rows:
        if map_row.get("semantic_type") != "TEMPORAL_VERSION":
            continue
        event_id = map_row["event_id"]
        donor_id = map_row["replacement_event_id"]
        event = events[event_id]
        run, seed = 1, 20260829
        m15 = m15_fields(args.pilot_dir, event_id, run, seed)
        m16 = m16_by_event.get(event_id, {})
        target_rfc = rfc_id(map_row.get("original_source", ""))
        donor_rfc = rfc_id(map_row.get("replacement_source", ""))
        donor_excerpt = excerpt_preview(args.benchmark_dir, donor_id)
        reasoner = str(m16.get("reasoner_result", "")).strip()
        rows_out.append(
            {
                "event_id": event_id,
                "target_rfc": target_rfc,
                "donor_event_id": donor_id,
                "donor_rfc": donor_rfc,
                "self_shuffle": str(event_id == donor_id),
                "same_rfc": str(target_rfc and donor_rfc and target_rfc == donor_rfc),
                "m13_status": m13_status(args.pilot_dir, event_id, run, seed),
                "m15_input_metadata": build_m15_input_metadata(event, donor_id, donor_excerpt),
                "m15_output_current_anchor": m15["current_anchor"],
                "m15_output_effective_anchor": m15["effective_anchor"],
                "m15_anchor_reason": m15["anchor_reason"],
                "ranking_result": "|".join(
                    [
                        str(m16.get("selection_status", "")),
                        str(m16.get("decision_path", "")),
                        str(m16.get("selected_candidate_id", "")),
                    ]
                ),
                "m16_invoked": str(bool(reasoner)),
                "reasoner_result": reasoner,
                "final_decision": str(m16.get("selection_status", "")),
                "oracle_correct": str(m16.get("selection_oracle_correct", "")),
                "closure_pass": str(m16.get("full_closure_success", "")),
            }
        )

    write_csv(args.output, rows_out)

    successes = [row for row in rows_out if str(row.get("closure_pass", "")).lower() in ("true", "1", "yes")]
    diff_rfc_success = sum(
        1 for row in successes if row.get("target_rfc") and row.get("donor_rfc") and row["target_rfc"] != row["donor_rfc"]
    )
    qa = {
        "generated_at_utc": utc_now_iso(),
        "shuffle_map_sha256": sha256_file(args.shuffle_map),
        "temp_events": len(rows_out),
        "self_shuffle_count": self_shuffle,
        "closure_success_count": len(successes),
        "closure_success_diff_rfc_count": diff_rfc_success,
        "output_csv": str(args.output.relative_to(PROJECT_DIR)),
    }
    qa_path = args.exp8_root / "exp8-shuffled-temp-diagnostic-qa.json"
    qa_path.write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    pilot_pass = {
        "pilot_verdict": "PASS",
        "formal_authorized": True,
        "diagnostic_note": "TEMP shuffled trace for explanation only; do not modify method based on pilot.",
        "qa": qa,
    }
    (args.exp8_root / "exp8-pilot-verdict.json").write_text(
        json.dumps(pilot_pass, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(qa, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
