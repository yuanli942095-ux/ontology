from __future__ import annotations

"""Audit CAND_002 oracle shortcut risk and per-event candidate-ID permutation robustness.

Outputs counterfactual accuracy under:
1) literal-ID shortcut (always keep original selected_candidate_id label)
2) content-faithful remap (selected display_value -> permuted id)

Also summarizes existing online robustness runs (order shuffle, global id rename).
"""

import argparse
import csv
import json
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR


BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v5-blind-large"
OUTPUT_ROOT = PROJECT_DIR / "output" / "paper-final-validation" / "cand-002-shortcut-audit"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_candidates(benchmark: Path) -> dict[str, dict[str, dict[str, str]]]:
    rows = read_csv(benchmark / "repair-stage" / "candidates" / "external-real-candidate-template.csv")
    grouped: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for row in rows:
        grouped[row["event_id"]][row["candidate_id"]] = row
    return grouped


def load_oracle(benchmark: Path) -> dict[str, str]:
    rows = read_csv(benchmark / "private" / "oracle" / "external-real-oracle-template.csv")
    return {row["event_id"]: row["oracle_candidate_id"] for row in rows}


def permute_labels_per_event(seed: int) -> tuple[dict[str, dict[str, str]], dict[str, str], Counter[str]]:
    """Shuffle which candidate_id label attaches to each fixed content row per event."""
    candidates = load_candidates(BENCHMARK)
    oracle_by_event = load_oracle(BENCHMARK)
    content_to_new_id: dict[str, dict[str, str]] = {}
    oracle_after: Counter[str] = Counter()

    for event_id in sorted(candidates):
        sorted_old = sorted(candidates[event_id])
        if len(sorted_old) != 3:
            continue
        labels = list(sorted_old)
        rng = random.Random(f"{seed}|{event_id}|id-permute")
        new_labels = list(labels)
        rng.shuffle(new_labels)
        mapping: dict[str, str] = {}
        for old_id, new_id in zip(sorted_old, new_labels):
            mapping[old_id] = new_id
        content_to_new_id[event_id] = mapping
        oracle_old = oracle_by_event[event_id]
        oracle_after[mapping[oracle_old]] += 1

    return content_to_new_id, oracle_by_event, oracle_after


def display_for_id(event_id: str, candidate_id: str, candidates: dict[str, dict[str, dict[str, str]]]) -> str:
    return candidates[event_id][candidate_id]["display_value"]


def id_for_old_content_after_perm(event_id: str, old_content_id: str, perm: dict[str, str]) -> str:
    return perm[old_content_id]


def old_content_id_for_literal_id(event_id: str, literal_id: str, perm: dict[str, str]) -> str:
    for old_id, new_id in perm.items():
        if new_id == literal_id:
            return old_id
    return ""


def summarize_details(
    details_path: Path,
    permutations: dict[str, dict[str, str]],
    candidates: dict[str, dict[str, dict[str, str]]],
    oracle_original: dict[str, str],
) -> dict[str, Any]:
    rows = read_csv(details_path)
    literal_ok = 0
    content_ok = 0
    total = 0
    literal_by_orig_selected: Counter[str] = Counter()

    for row in rows:
        event_id = row["event_id"]
        if event_id not in permutations:
            continue
        selected = str(row.get("selected_candidate_id", "")).strip()
        if not selected:
            continue
        total += 1
        perm = permutations[event_id]

        oracle_old = oracle_original[event_id]
        oracle_perm_id = id_for_old_content_after_perm(event_id, oracle_old, perm)

        literal_by_orig_selected[selected] += 1
        literal_old_content = old_content_id_for_literal_id(event_id, "CAND_002", perm)
        if literal_old_content == oracle_old:
            literal_ok += 1

        selected_old_content = selected if selected in perm else old_content_id_for_literal_id(event_id, selected, perm)
        if not selected_old_content:
            continue
        content_perm_id = id_for_old_content_after_perm(event_id, selected_old_content, perm)
        if content_perm_id == oracle_perm_id:
            content_ok += 1

    return {
        "details_path": str(details_path.relative_to(PROJECT_DIR)),
        "rows_with_selection": total,
        "literal_shortcut_accuracy": round(literal_ok / total, 4) if total else None,
        "content_faithful_accuracy": round(content_ok / total, 4) if total else None,
        "original_oracle_correct_rate": round(
            sum(1 for r in rows if str(r.get("oracle_correct", "")).lower() == "true") / len(rows), 4
        )
        if rows
        else None,
        "literal_shortcut_correct": literal_ok,
        "content_faithful_correct": content_ok,
        "orig_selected_id_distribution": dict(literal_by_orig_selected),
    }


def read_robustness_summary(summary_path: Path) -> dict[str, str] | None:
    if not summary_path.is_file():
        return None
    rows = read_csv(summary_path)
    all_row = next((r for r in rows if r.get("semantic_type") == "ALL"), None)
    return all_row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20260902)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_ROOT)
    args = parser.parse_args()

    candidates = load_candidates(BENCHMARK)
    permutations, oracle_original, oracle_after_perm = permute_labels_per_event(args.seed)

    orig_details = (
        PROJECT_DIR
        / "output"
        / "external-real-holdout-v5-blind-large"
        / "final-blind-eval-r5-m16-deepseek"
        / "m16-full-holdout-combined"
        / "m16-full-holdout-combined-details.csv"
    )
    rename_details = (
        PROJECT_DIR
        / "output"
        / "external-real-holdout-v5-blind-large"
        / "robustness-online"
        / "candidate-id-rename-full-r5-deepseek"
        / "m16-full-holdout-combined"
        / "m16-full-holdout-combined-details.csv"
    )
    shuffle_details = (
        PROJECT_DIR
        / "output"
        / "external-real-holdout-v5-blind-large"
        / "robustness-online"
        / "candidate-order-shuffle-full-r5-deepseek"
        / "m16-full-holdout-combined"
        / "m16-full-holdout-combined-details.csv"
    )

    counterfactual = summarize_details(orig_details, permutations, candidates, oracle_original)

    online = {
        "original": read_robustness_summary(
            orig_details.parent / "m16-full-holdout-combined-summary.csv"
        ),
        "candidate_id_rename_global": read_robustness_summary(
            rename_details.parent / "m16-full-holdout-combined-summary.csv"
        ),
        "candidate_order_shuffle": read_robustness_summary(
            shuffle_details.parent / "m16-full-holdout-combined-summary.csv"
        ),
    }

    rename_selected = Counter(
        r.get("selected_candidate_id", "")
        for r in read_csv(rename_details)
        if r.get("selected_candidate_id")
    )

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "benchmark": str(BENCHMARK.relative_to(PROJECT_DIR)),
        "root_cause": {
            "construction": (
                "build_external_real_holdout_v1 assigns CAND_001=unmodeled, "
                "CAND_002=oracle value, CAND_003=distractor for every event."
            ),
            "oracle_candidate_id_distribution_original": dict(Counter(oracle_original.values())),
            "oracle_always_middle_column_on_blind_sheets": True,
            "oracle_review_script_expects_CAND_002": "fill_holdout_oracle_manual_review.py",
        },
        "per_event_id_permutation": {
            "seed": args.seed,
            "method": "shuffle CAND_001/002/003 labels per event; display_value and operation_json stay on row",
            "oracle_candidate_id_distribution_after_perm": oracle_after_perm,
            "expected_literal_shortcut_accuracy_if_uniform": round(1 / 3, 4),
        },
        "counterfactual_on_original_m16_details": counterfactual,
        "existing_online_robustness_m16": online,
        "rename_run_selected_id_distribution": dict(rename_selected),
        "interpretation": {
            "global_rename_tests": (
                "Whether model memorizes string 'CAND_002' vs middle sorted id 'ALT_B'. "
                "Oracle still 264/264 on same label (ALT_B); not per-event permutation."
            ),
            "order_shuffle_tests": "CSV row order only; prompts still sort by candidate_id.",
            "per_event_perm_counterfactual": (
                "If pipeline ranks by candidate content (IR/operation), content_faithful_accuracy "
                "should match original (~99%). Literal shortcut stays ~33%."
            ),
        },
    }

    out = args.output_dir.resolve()
    write_json(out / "cand-002-shortcut-audit.json", report)

    # mapping sample for paper
    sample_events = ["H5_E001", "H5_E050", "H5_E100", "H5_E200"]
    mapping_rows = []
    for event_id in sample_events:
        if event_id not in permutations:
            continue
        perm = permutations[event_id]
        mapping_rows.append(
            {
                "event_id": event_id,
                "oracle_original_content_slot": oracle_original[event_id],
                "oracle_perm_id": permutations[event_id][oracle_original[event_id]],
                "content_slot_to_perm_id": permutations[event_id],
            }
        )
    write_json(out / "per-event-permutation-sample.json", mapping_rows)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
