from __future__ import annotations

"""Build final paper support tables for external-real-holdout-v5-blind-large.

This script is offline: it reads frozen benchmark metadata and already-finished
M13/M14/M15/M16 outputs. It does not call any LLM and does not change benchmark
or method files.
"""

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v5-blind-large"
RUN_DIR = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v5-blind-large"
    / "final-blind-eval-r5-m16-deepseek"
)
OUTPUT_DIR = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v5-blind-large"
    / "final-paper-support-20260831"
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def truth(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def pct(success: int, total: int) -> str:
    return f"{success / total * 100:.2f}%" if total else "0.00%"


def load_event_metadata(benchmark: Path) -> dict[str, dict[str, str]]:
    events = {
        row["event_id"]: dict(row)
        for row in read_csv(benchmark / "public" / "events" / "external-real-event-template.csv")
    }
    documents = read_csv(benchmark / "public" / "documents" / "external-real-document-template.csv")
    for row in documents:
        event_id = row.get("event_id", "")
        if event_id in events:
            events[event_id]["source_family"] = row.get("issuer", "") or row.get("source_url", "")
            events[event_id]["source_url"] = row.get("source_url", "")
            events[event_id]["document_type"] = row.get("document_type", "")
            events[event_id]["source_type"] = row.get("source_type", "")
    return events


def group_summary(
    rows: list[dict[str, str]],
    meta: dict[str, dict[str, str]],
    key: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        value = row.get(key) or meta.get(row["event_id"], {}).get(key) or "UNKNOWN"
        grouped[value].append(row)
    out: list[dict[str, Any]] = []
    for value, items in sorted(grouped.items()):
        event_ids = sorted({item["event_id"] for item in items})
        closure = sum(1 for item in items if truth(item.get("closure_success")))
        oracle = sum(1 for item in items if truth(item.get("oracle_correct")))
        strict = sum(
            1
            for event_id in event_ids
            if all(truth(item.get("closure_success")) for item in items if item["event_id"] == event_id)
        )
        out.append(
            {
                key: value,
                "events": len(event_ids),
                "attempts": len(items),
                "closure_success": closure,
                "closure_accuracy": f"{closure / len(items):.6f}",
                "closure_accuracy_pct": pct(closure, len(items)),
                "oracle_success": oracle,
                "oracle_accuracy": f"{oracle / len(items):.6f}",
                "strict_event_successes": strict,
                "strict_event_accuracy": f"{strict / len(event_ids):.6f}" if event_ids else "0.000000",
                "strict_event_accuracy_pct": pct(strict, len(event_ids)),
            }
        )
    return out


def stage_rows(run_dir: Path) -> list[dict[str, Any]]:
    stage_paths = [
        (
            "M13_BASE",
            run_dir
            / "m13-pilot"
            / "arm-d-rule-refinement"
            / "ir"
            / "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827-summary.csv",
            "",
        ),
        (
            "M13_PLUS_M14_GRE_CSS",
            run_dir / "m14-full-holdout-combined" / "m14-full-holdout-combined-summary.csv",
            "M14_GRE_CSS_RECOVERY",
        ),
        (
            "M13_PLUS_M14_PLUS_M15_TEMPORAL",
            run_dir / "m15-full-holdout-combined" / "m15-full-holdout-combined-summary.csv",
            "M15_TEMPORAL_ANCHOR_RECOVERY",
        ),
        (
            "FULL_M16",
            run_dir / "m16-full-holdout-combined" / "m16-full-holdout-combined-summary.csv",
            "M16_CANDIDATE_ENTAILMENT_VERIFIER",
        ),
    ]
    out: list[dict[str, Any]] = []
    base_accuracy: float | None = None
    previous_accuracy: float | None = None
    for stage, path, added in stage_paths:
        all_row = next(row for row in read_csv(path) if row.get("semantic_type") == "ALL")
        accuracy = float(all_row.get("closure_accuracy") or all_row.get("full_closure_accuracy") or 0.0)
        attempts = int(all_row.get("attempts") or 0)
        closure_success = all_row.get("closure_success", "")
        if not closure_success and attempts:
            closure_success = str(round(accuracy * attempts))
        if base_accuracy is None:
            base_accuracy = accuracy
        out.append(
            {
                "stage": stage,
                "added_module": added,
                "events": all_row.get("events", ""),
                "attempts": str(attempts),
                "closure_success": closure_success,
                "closure_accuracy": f"{accuracy:.6f}",
                "closure_accuracy_pct": f"{accuracy * 100:.2f}%",
                "strict_event_successes": all_row.get("strict_event_successes", ""),
                "strict_event_accuracy": all_row.get("strict_event_accuracy", ""),
                "strict_event_accuracy_pct": f"{float(all_row.get('strict_event_accuracy') or 0.0) * 100:.2f}%",
                "gain_vs_m13_pp": f"{(accuracy - base_accuracy) * 100:.2f}",
                "gain_vs_previous_stage_pp": ""
                if previous_accuracy is None
                else f"{(accuracy - previous_accuracy) * 100:.2f}",
            }
        )
        previous_accuracy = accuracy
    return out


def failure_rows(rows: list[dict[str, str]], meta: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if truth(row.get("closure_success")):
            continue
        event = meta.get(row["event_id"], {})
        out.append(
            {
                "event_id": row["event_id"],
                "semantic_type": row.get("semantic_type") or event.get("semantic_type", ""),
                "domain": row.get("domain") or event.get("domain", ""),
                "source_family": row.get("source_family") or event.get("source_family", ""),
                "run": row.get("run", ""),
                "seed": row.get("seed", ""),
                "final_decision_path": row.get("final_decision_path") or row.get("decision_path", ""),
                "previous_decision_path": row.get("previous_decision_path", ""),
                "selected_candidate_id": row.get("selected_candidate_id", ""),
                "oracle_candidate_id": row.get("oracle_candidate_id", ""),
                "failure_mode": "ABSTAIN_OR_FAIL_CLOSED"
                if not row.get("selected_candidate_id")
                else "WRONG_SELECTION_OR_CLOSURE_FAIL",
            }
        )
    return out


def note_leakage_audit(benchmark: Path) -> list[dict[str, Any]]:
    tokens = ("oracle", "gold", "answer", "correct", "标准答案", "正确答案")
    rows = read_csv(benchmark / "repair-stage" / "candidates" / "external-real-candidate-template.csv")
    out = []
    for row in rows:
        note = row.get("notes", "")
        lowered = note.lower()
        leaking = [token for token in tokens if token in lowered]
        out.append(
            {
                "event_id": row["event_id"],
                "candidate_id": row["candidate_id"],
                "leakage_detected": bool(leaking),
                "matched_tokens": "|".join(leaking),
                "notes": note,
            }
        )
    return out


def replay_score_rank_robustness(m13_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Check deterministic score replay invariance to candidate order and ID names.

    This is not a replacement for an online LLM robustness run. It only verifies
    that the deterministic V4.3 score-based ranking layer is order invariant and
    does not depend on the literal CAND_00X spelling after scores are produced.
    """
    checked = 0
    no_scores = 0
    replay_mismatch = 0
    order_sensitive = 0
    id_name_sensitive = 0
    for row in m13_rows:
        raw = row.get("candidate_scores_json", "").strip()
        if not raw:
            no_scores += 1
            continue
        try:
            scores = json.loads(raw)
        except json.JSONDecodeError:
            no_scores += 1
            continue
        if not isinstance(scores, list) or not scores:
            no_scores += 1
            continue
        checked += 1
        ordered = sorted(scores, key=lambda item: -float(item.get("score") or 0.0))
        replay = str(ordered[0].get("candidate_id", ""))
        recorded = row.get("selected_candidate_id", "")
        if row.get("selection_status") == "SELECTED" and replay != recorded:
            replay_mismatch += 1
        reversed_order = sorted(list(reversed(scores)), key=lambda item: -float(item.get("score") or 0.0))
        if str(reversed_order[0].get("candidate_id", "")) != replay:
            order_sensitive += 1
        rename_map = {str(item.get("candidate_id", "")): f"RENAMED_{idx:03d}" for idx, item in enumerate(scores, 1)}
        renamed = [{**item, "candidate_id": rename_map[str(item.get("candidate_id", ""))]} for item in scores]
        renamed_top = sorted(renamed, key=lambda item: -float(item.get("score") or 0.0))[0]
        inverse = {value: key for key, value in rename_map.items()}
        if inverse.get(str(renamed_top.get("candidate_id", ""))) != replay:
            id_name_sensitive += 1
    return [
        {
            "scope": "M13_V4_3_SCORE_LAYER_OFFLINE_REPLAY",
            "checked_attempts": checked,
            "no_score_attempts": no_scores,
            "score_replay_mismatch_on_selected": replay_mismatch,
            "order_sensitive_rows": order_sensitive,
            "id_name_sensitive_rows": id_name_sensitive,
            "interpretation": (
                "Offline replay only; M14/M15/M16 LLM stages require separate online robustness runs "
                "if the paper claims candidate-ID robustness for the full pipeline."
            ),
        }
    ]


def write_markdown(
    path: Path,
    stages: list[dict[str, Any]],
    by_type: list[dict[str, Any]],
    by_domain: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    leakage_summary: dict[str, Any],
) -> None:
    lines = [
        "# v5 blind-large final paper support tables",
        "",
        "Dataset: `external-real-holdout-v5-blind-large`.",
        "Method: frozen Full M16 evidence-constrained repair pipeline.",
        "",
        "## Stage Ablation",
        "",
        "| Stage | Closure | Strict event | Gain vs M13 | Stage gain |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in stages:
        stage_gain = row["gain_vs_previous_stage_pp"]
        stage_gain_text = "--" if stage_gain == "" else f"{stage_gain} pp"
        lines.append(
            f"| {row['stage']} | {row['closure_success']}/{row['attempts']} ({row['closure_accuracy_pct']}) | "
            f"{row['strict_event_successes']}/{row['events']} ({row['strict_event_accuracy_pct']}) | "
            f"{row['gain_vs_m13_pp']} pp | {stage_gain_text} |"
        )
    lines.extend(["", "## Semantic Type", "", "| Type | Closure | Strict event |", "|---|---:|---:|"])
    for row in by_type:
        lines.append(
            f"| {row['semantic_type']} | {row['closure_success']}/{row['attempts']} ({row['closure_accuracy_pct']}) | "
            f"{row['strict_event_successes']}/{row['events']} ({row['strict_event_accuracy_pct']}) |"
        )
    lines.extend(["", "## Domain", "", "| Domain | Events | Closure | Strict event |", "|---|---:|---:|---:|"])
    for row in by_domain:
        lines.append(
            f"| {row['domain']} | {row['events']} | {row['closure_success']}/{row['attempts']} ({row['closure_accuracy_pct']}) | "
            f"{row['strict_event_successes']}/{row['events']} ({row['strict_event_accuracy_pct']}) |"
        )
    lines.extend(["", "## Remaining Failures", "", "| Event | Type | Domain | Run | Path | Mode |", "|---|---|---|---:|---|---|"])
    for row in failures:
        lines.append(
            f"| {row['event_id']} | {row['semantic_type']} | {row['domain']} | {row['run']} | "
            f"{row['final_decision_path']} | {row['failure_mode']} |"
        )
    if not failures:
        lines.append("| none | | | | | |")
    lines.extend(
        [
            "",
            "## Candidate Note Isolation",
            "",
            f"- Candidate rows checked: {leakage_summary['candidate_rows']}",
            f"- Leakage rows: {leakage_summary['candidate_note_leakage_rows']}",
            "- Candidate notes are not used by the frozen method; this table only documents public-release hygiene.",
            "",
            "## Methodological Note",
            "",
            "The online v5 main result is not rerun here. These files are derived analysis tables over frozen benchmark metadata and locked final outputs.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK)
    parser.add_argument("--run-dir", type=Path, default=RUN_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    benchmark = args.benchmark_dir.resolve()
    run_dir = args.run_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    meta = load_event_metadata(benchmark)
    final_rows = read_csv(run_dir / "m16-full-holdout-combined" / "m16-full-holdout-combined-details.csv")
    final_full_rows = read_csv(run_dir / "m16-full-holdout-combined" / "m16-full-holdout-combined-full-details.csv")
    m13_rows = read_csv(
        run_dir
        / "m13-pilot"
        / "arm-d-rule-refinement"
        / "ir"
        / "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827-details.csv"
    )

    stages = stage_rows(run_dir)
    by_type = group_summary(final_rows, meta, "semantic_type")
    by_domain = group_summary(final_rows, meta, "domain")
    by_source_family = group_summary(final_rows, meta, "source_family")
    failures = failure_rows(final_rows, meta)
    leakage_rows = note_leakage_audit(benchmark)
    leakage_summary = {
        "candidate_rows": len(leakage_rows),
        "candidate_note_leakage_rows": sum(1 for row in leakage_rows if row["leakage_detected"]),
        "note_value_counts": dict(Counter(row["notes"] for row in leakage_rows)),
    }
    score_replay = replay_score_rank_robustness(m13_rows)

    write_csv(output_dir / "v5-stage-ablation-table.csv", stages)
    write_csv(output_dir / "v5-by-semantic-type.csv", by_type)
    write_csv(output_dir / "v5-by-domain.csv", by_domain)
    write_csv(output_dir / "v5-by-source-family.csv", by_source_family)
    write_csv(output_dir / "v5-remaining-failures.csv", failures)
    write_csv(output_dir / "v5-candidate-note-leakage-audit.csv", leakage_rows)
    write_csv(output_dir / "v5-offline-score-robustness-replay.csv", score_replay)
    summary = {
        "benchmark": str(benchmark),
        "run_dir": str(run_dir),
        "output_dir": str(output_dir),
        "stage_ablation": stages,
        "semantic_type": by_type,
        "domain_count": len(by_domain),
        "source_family_count": len(by_source_family),
        "remaining_failures": failures,
        "candidate_note_leakage": leakage_summary,
        "offline_score_robustness_replay": score_replay,
    }
    (output_dir / "v5-final-paper-support-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_markdown(
        output_dir / "v5-final-paper-support-report.md",
        stages,
        by_type,
        by_domain,
        failures,
        leakage_summary,
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
