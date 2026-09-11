from __future__ import annotations

"""Summarize v4-blind generalization and no-model candidate robustness checks."""

import argparse
import csv
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR, write_csv


DEFAULT_BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v4-blind"
DEFAULT_RUN_DIR = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v4-blind"
    / "final-blind-eval-r5-m16-deepseek"
)
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "output" / "external-real-holdout-v4-blind" / "robustness"
FINAL_DETAILS = Path("m16-full-holdout-combined") / "m16-full-holdout-combined-details.csv"
FINAL_FULL_DETAILS = Path("m16-full-holdout-combined") / "m16-full-holdout-combined-full-details.csv"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def truth(value: Any) -> bool:
    return str(value or "").strip().lower() == "true"


def relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_DIR))
    except ValueError:
        return str(path.resolve())


def family_from_doc(row: dict[str, str]) -> str:
    issuer = row.get("issuer", "").strip()
    if issuer:
        return issuer
    name = row.get("file_name", "").strip()
    if "_NEW_" in name:
        return name.split("_NEW_", 1)[1].rsplit(".", 1)[0]
    return row.get("source_url", "").strip() or "UNKNOWN"


def event_metadata(benchmark_dir: Path) -> dict[str, dict[str, str]]:
    events = {row["event_id"]: row for row in read_csv(benchmark_dir / "public" / "events" / "external-real-event-template.csv")}
    documents = read_csv(benchmark_dir / "public" / "documents" / "external-real-document-template.csv")
    for doc in documents:
        event_id = doc.get("event_id", "")
        if event_id in events:
            events[event_id]["source_family"] = family_from_doc(doc)
            events[event_id]["source_url"] = doc.get("source_url", events[event_id].get("source_url", ""))
    return events


def summarize_group(rows: list[dict[str, str]], group_key: str, meta: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        event = meta.get(row["event_id"], {})
        value = row.get(group_key) or event.get(group_key) or "UNKNOWN"
        grouped[value].append(row)

    out: list[dict[str, Any]] = []
    for value, subset in sorted(grouped.items()):
        attempts = len(subset)
        closure = sum(1 for row in subset if truth(row.get("closure_success")))
        oracle = sum(1 for row in subset if truth(row.get("oracle_correct")))
        event_ids = sorted({row["event_id"] for row in subset})
        strict = sum(
            1
            for event_id in event_ids
            if all(truth(item.get("closure_success")) for item in subset if item["event_id"] == event_id)
        )
        out.append(
            {
                group_key: value,
                "events": len(event_ids),
                "attempts": attempts,
                "closure_success": closure,
                "closure_accuracy": round(closure / attempts, 4) if attempts else 0.0,
                "oracle_success": oracle,
                "oracle_accuracy": round(oracle / attempts, 4) if attempts else 0.0,
                "strict_event_successes": strict,
                "strict_event_accuracy": round(strict / len(event_ids), 4) if event_ids else 0.0,
            }
        )
    return out


def parse_scores(row: dict[str, str]) -> list[dict[str, Any]]:
    text = str(row.get("candidate_scores_json") or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    return [item for item in parsed if isinstance(item, dict) and item.get("candidate_id")]


def select_from_scores(scores: list[dict[str, Any]], min_score: float, min_margin: float) -> tuple[str, str]:
    if not scores:
        return "", "NO_SCORES"
    ordered = sorted(scores, key=lambda item: -float(item.get("score") or 0.0))
    top = ordered[0]
    second = float(ordered[1].get("score") or 0.0) if len(ordered) > 1 else 0.0
    top_score = float(top.get("score") or 0.0)
    if len(ordered) > 1 and top_score == second:
        return "", "TIE_ABSTAIN"
    if top_score >= min_score and top_score - second >= min_margin:
        return str(top["candidate_id"]), "SELECT"
    return "", "SCORE_ABSTAIN"


def robustness_rows(rows: list[dict[str, str]], *, shuffles: int, seed: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    detail_rows: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()
    rng = random.Random(seed)
    labels = ["OPT_A", "OPT_B", "OPT_C", "OPT_D", "OPT_E", "OPT_F"]
    for row in rows:
        scores = parse_scores(row)
        if not scores:
            counters["not_applicable_no_candidate_scores"] += 1
            continue
        min_score = float(row.get("effective_min_score") or 0.0)
        min_margin = float(row.get("effective_min_margin") or 0.0)
        expected_id, expected_status = select_from_scores(scores, min_score, min_margin)
        selected_id = row.get("selected_candidate_id", "").strip()
        if expected_id != selected_id and expected_status == "SELECT":
            counters["score_replay_disagrees_with_record"] += 1

        shuffle_ok = True
        rename_ok = True
        for index in range(shuffles):
            shuffled = [dict(item) for item in scores]
            rng.shuffle(shuffled)
            shuffled_id, shuffled_status = select_from_scores(shuffled, min_score, min_margin)
            if shuffled_id != expected_id or shuffled_status != expected_status:
                shuffle_ok = False

            original_ids = [str(item["candidate_id"]) for item in shuffled]
            rename_labels = labels[: len(original_ids)]
            rng.shuffle(rename_labels)
            rename_map = dict(zip(original_ids, rename_labels))
            inverse = {value: key for key, value in rename_map.items()}
            renamed = [{**item, "candidate_id": rename_map[str(item["candidate_id"])]} for item in shuffled]
            renamed_id, renamed_status = select_from_scores(renamed, min_score, min_margin)
            mapped_back = inverse.get(renamed_id, "") if renamed_id else ""
            if mapped_back != expected_id or renamed_status != expected_status:
                rename_ok = False

        counters["score_rows_checked"] += 1
        counters["shuffle_invariant" if shuffle_ok else "shuffle_changed"] += 1
        counters["id_rename_invariant" if rename_ok else "id_rename_changed"] += 1
        detail_rows.append(
            {
                "event_id": row["event_id"],
                "run": row["run"],
                "seed": row["seed"],
                "semantic_type": row.get("semantic_type", ""),
                "domain": row.get("domain", ""),
                "selected_candidate_id": selected_id,
                "score_replay_candidate_id": expected_id,
                "score_replay_status": expected_status,
                "shuffle_invariant": shuffle_ok,
                "id_rename_invariant": rename_ok,
                "candidate_count": len(scores),
                "min_score": min_score,
                "min_margin": min_margin,
            }
        )
    summary = [
        {
            "metric": key,
            "count": value,
            "rate_over_score_rows": round(value / counters["score_rows_checked"], 4)
            if counters["score_rows_checked"] and key not in {"not_applicable_no_candidate_scores"}
            else "",
        }
        for key, value in sorted(counters.items())
    ]
    return detail_rows, summary


def write_markdown(path: Path, domain_rows: list[dict[str, Any]], family_rows: list[dict[str, Any]], robustness_summary: list[dict[str, Any]]) -> None:
    lines = [
        "# v4-blind generalization and candidate robustness",
        "",
        "Dataset: frozen `external-real-holdout-v4-blind`; final method: frozen M16 pipeline.",
        "",
        "## Domain Generalization",
        "",
        "| Domain | Events | Closure | Strict event |",
        "|---|---:|---:|---:|",
    ]
    for row in domain_rows:
        lines.append(
            f"| {row['domain']} | {row['events']} | {row['closure_success']}/{row['attempts']} ({row['closure_accuracy'] * 100:.2f}%) | "
            f"{row['strict_event_successes']}/{row['events']} ({row['strict_event_accuracy'] * 100:.2f}%) |"
        )
    lines.extend(["", "## Source Family Generalization", "", "| Source family | Events | Closure | Strict event |", "|---|---:|---:|---:|"])
    for row in family_rows:
        lines.append(
            f"| {row['source_family']} | {row['events']} | {row['closure_success']}/{row['attempts']} ({row['closure_accuracy'] * 100:.2f}%) | "
            f"{row['strict_event_successes']}/{row['events']} ({row['strict_event_accuracy'] * 100:.2f}%) |"
        )
    lines.extend(["", "## Candidate Robustness", "", "| Metric | Count | Rate over scored rows |", "|---|---:|---:|"])
    for row in robustness_summary:
        rate = "" if row["rate_over_score_rows"] == "" else f"{float(row['rate_over_score_rows']) * 100:.2f}%"
        lines.append(f"| {row['metric']} | {row['count']} | {rate} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--shuffles", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260830)
    args = parser.parse_args()
    args.benchmark_dir = args.benchmark_dir.resolve()
    args.run_dir = args.run_dir.resolve()
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    meta = event_metadata(args.benchmark_dir)
    final_summary_rows = read_csv(args.run_dir / FINAL_DETAILS)
    final_full_rows = read_csv(args.run_dir / FINAL_FULL_DETAILS)
    for row in final_summary_rows:
        row["source_family"] = meta.get(row["event_id"], {}).get("source_family", "UNKNOWN")

    domain_rows = summarize_group(final_summary_rows, "domain", meta)
    family_rows = summarize_group(final_summary_rows, "source_family", meta)
    robust_details, robust_summary = robustness_rows(final_full_rows, shuffles=args.shuffles, seed=args.seed)

    write_csv(args.output_dir / "v4-blind-generalization-by-domain.csv", domain_rows)
    write_csv(args.output_dir / "v4-blind-generalization-by-source-family.csv", family_rows)
    write_csv(args.output_dir / "v4-blind-candidate-robustness-details.csv", robust_details)
    write_csv(args.output_dir / "v4-blind-candidate-robustness-summary.csv", robust_summary)
    write_markdown(
        args.output_dir / "v4-blind-generalization-robustness.md",
        domain_rows,
        family_rows,
        robust_summary,
    )
    print(
        json.dumps(
            {
                "domain_rows": len(domain_rows),
                "source_family_rows": len(family_rows),
                "robustness_rows": len(robust_details),
                "output_dir": relpath(args.output_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
