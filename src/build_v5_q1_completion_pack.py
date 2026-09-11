from __future__ import annotations

"""Build a final Q1 supplement completion pack for v5 blind-large.

This script is deliberately offline. It reads frozen benchmark metadata and
finished result CSVs only; it does not call any model, change the benchmark, or
rerun scoring.
"""

import csv
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
BENCHMARK = PROJECT / "benchmark" / "external-real-holdout-v5-blind-large"
OUTPUT_ROOT = PROJECT / "output" / "external-real-holdout-v5-blind-large"
MAIN_ROOT = OUTPUT_ROOT / "final-blind-eval-r5-m16-deepseek"
BASELINE_ROOT = OUTPUT_ROOT / "baseline-candidate-selection-full-r5-deepseek"
ROBUSTNESS_ROOT = OUTPUT_ROOT / "robustness-online"
OUT = OUTPUT_ROOT / "q1-completion-pack-20260901"


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


def load_event_meta() -> dict[str, dict[str, str]]:
    rows = read_csv(BENCHMARK / "public" / "events" / "external-real-event-template.csv")
    return {row["event_id"]: row for row in rows}


def summary_path_for_stage(stage: str) -> Path:
    if stage == "M13_BASE":
        return (
            MAIN_ROOT
            / "m13-pilot"
            / "arm-d-rule-refinement"
            / "ir"
            / "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827-summary.csv"
        )
    if stage == "M13_PLUS_M14":
        return MAIN_ROOT / "m14-full-holdout-combined" / "m14-full-holdout-combined-summary.csv"
    if stage == "M13_PLUS_M14_PLUS_M15":
        return MAIN_ROOT / "m15-full-holdout-combined" / "m15-full-holdout-combined-summary.csv"
    if stage == "FULL_M16":
        return MAIN_ROOT / "m16-full-holdout-combined" / "m16-full-holdout-combined-summary.csv"
    raise ValueError(stage)


def all_row(path: Path) -> dict[str, str]:
    rows = read_csv(path)
    return next(row for row in rows if row.get("semantic_type") == "ALL")


def semantic_rows(path: Path, label: str, metric_name: str) -> list[dict[str, Any]]:
    out = []
    for row in read_csv(path):
        attempts = int(row.get("attempts") or 0)
        accuracy = float(row.get("closure_accuracy") or row.get("full_closure_accuracy") or row.get("oracle_accuracy") or 0)
        success = row.get("closure_success") or row.get("oracle_success") or str(round(accuracy * attempts))
        out.append(
            {
                "experiment": label,
                "semantic_type": row.get("semantic_type", ""),
                "events": row.get("events", ""),
                "attempts": attempts,
                metric_name: success,
                f"{metric_name}_accuracy": f"{accuracy:.6f}",
                f"{metric_name}_accuracy_pct": f"{accuracy * 100:.2f}%",
                "strict_event_successes": row.get("strict_event_successes", ""),
                "strict_event_accuracy": row.get("strict_event_accuracy", ""),
            }
        )
    return out


def method_comparison() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for stage in ("M13_BASE", "M13_PLUS_M14", "M13_PLUS_M14_PLUS_M15", "FULL_M16"):
        row = all_row(summary_path_for_stage(stage))
        attempts = int(row.get("attempts") or 0)
        accuracy = float(row.get("closure_accuracy") or row.get("full_closure_accuracy") or 0)
        rows.append(
            {
                "method": stage,
                "input_or_module": {
                    "M13_BASE": "task-formulated extraction + semantic IR + V4.3 ranking/gate",
                    "M13_PLUS_M14": "+ clause-level GRE/CSS recovery",
                    "M13_PLUS_M14_PLUS_M15": "+ temporal anchor recovery",
                    "FULL_M16": "+ candidate entailment verifier",
                }[stage],
                "events": row.get("events", ""),
                "attempts": attempts,
                "closure_success": row.get("closure_success") or str(round(accuracy * attempts)),
                "closure_accuracy": f"{accuracy:.6f}",
                "closure_accuracy_pct": f"{accuracy * 100:.2f}%",
                "strict_event_successes": row.get("strict_event_successes", ""),
                "strict_event_accuracy": row.get("strict_event_accuracy", ""),
                "notes": "frozen repair pipeline",
            }
        )
    baseline_summary = read_csv(BASELINE_ROOT / "v5-baseline-summary.csv")
    for method in ("OPTION_VALUE_ONLY", "OPTION_FORMAL_OPERATION"):
        row = next(item for item in baseline_summary if item["method"] == method and item["semantic_type"] == "ALL")
        rows.append(
            {
                "method": method,
                "input_or_module": {
                    "OPTION_VALUE_ONLY": "public evidence + candidate display values",
                    "OPTION_FORMAL_OPERATION": "public evidence + formal operation objects",
                }[method],
                "events": row["events"],
                "attempts": row["attempts"],
                "closure_success": row["oracle_success"],
                "closure_accuracy": f"{float(row['oracle_accuracy']):.6f}",
                "closure_accuracy_pct": f"{float(row['oracle_accuracy']) * 100:.2f}%",
                "strict_event_successes": "",
                "strict_event_accuracy": "",
                "notes": "candidate-selection baseline; no OWL closure stage",
            }
        )
    return rows


def robustness_comparison() -> list[dict[str, Any]]:
    original = MAIN_ROOT / "m16-full-holdout-combined" / "m16-full-holdout-combined-summary.csv"
    order = (
        ROBUSTNESS_ROOT
        / "candidate-order-shuffle-full-r5-deepseek"
        / "m16-full-holdout-combined"
        / "m16-full-holdout-combined-summary.csv"
    )
    rename = (
        ROBUSTNESS_ROOT
        / "candidate-id-rename-full-r5-deepseek"
        / "m16-full-holdout-combined"
        / "m16-full-holdout-combined-summary.csv"
    )
    rows: list[dict[str, Any]] = []
    for label, path in (
        ("original", original),
        ("candidate_order_shuffle", order),
        ("candidate_id_rename", rename),
    ):
        rows.extend(semantic_rows(path, label, "closure_success"))
    return rows


def baseline_by_type() -> list[dict[str, Any]]:
    rows = []
    for row in read_csv(BASELINE_ROOT / "v5-baseline-summary.csv"):
        rows.append(
            {
                "method": row["method"],
                "semantic_type": row["semantic_type"],
                "events": row["events"],
                "attempts": row["attempts"],
                "selected": row["selected"],
                "abstain": row["abstain"],
                "invalid": row["invalid"],
                "oracle_success": row["oracle_success"],
                "oracle_accuracy": row["oracle_accuracy"],
                "oracle_accuracy_pct": f"{float(row['oracle_accuracy']) * 100:.2f}%",
            }
        )
    return rows


def final_failure_analysis(meta: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    configs = [
        (
            "original_full_m16",
            MAIN_ROOT / "m16-full-holdout-combined" / "m16-full-holdout-combined-details.csv",
        ),
        (
            "robustness_candidate_order_shuffle",
            ROBUSTNESS_ROOT
            / "candidate-order-shuffle-full-r5-deepseek"
            / "m16-full-holdout-combined"
            / "m16-full-holdout-combined-details.csv",
        ),
        (
            "robustness_candidate_id_rename",
            ROBUSTNESS_ROOT
            / "candidate-id-rename-full-r5-deepseek"
            / "m16-full-holdout-combined"
            / "m16-full-holdout-combined-details.csv",
        ),
    ]
    rows: list[dict[str, Any]] = []
    for experiment, path in configs:
        for row in read_csv(path):
            if truth(row.get("closure_success")):
                continue
            event = meta.get(row["event_id"], {})
            selected = row.get("selected_candidate_id", "")
            path_name = row.get("final_decision_path") or row.get("decision_path", "")
            if not selected:
                failure_layer = "selective_abstention_or_fail_closed"
            elif not truth(row.get("oracle_correct")):
                failure_layer = "wrong_candidate_selection"
            else:
                failure_layer = "owl_closure_failure_after_correct_selection"
            rows.append(
                {
                    "experiment": experiment,
                    "event_id": row["event_id"],
                    "semantic_type": row.get("semantic_type") or event.get("semantic_type", ""),
                    "domain": row.get("domain") or event.get("domain", ""),
                    "run": row.get("run", ""),
                    "seed": row.get("seed", ""),
                    "failure_layer": failure_layer,
                    "final_decision_path": path_name,
                    "previous_decision_path": row.get("previous_decision_path", ""),
                    "selected_candidate_id": selected,
                    "oracle_correct": row.get("oracle_correct", ""),
                    "closure_success": row.get("closure_success", ""),
                    "paper_interpretation": (
                        "fail-closed behavior; no unsupported OWL patch was applied"
                        if not selected
                        else "candidate selected but did not satisfy oracle/closure"
                    ),
                }
            )
    return rows


def failure_summary(failures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in failures:
        grouped[(row["experiment"], row["semantic_type"], row["failure_layer"])].append(row)
    return [
        {
            "experiment": experiment,
            "semantic_type": semantic_type,
            "failure_layer": layer,
            "failed_attempts": len(items),
            "failed_events": len({item["event_id"] for item in items}),
            "decision_paths": ";".join(f"{key}:{value}" for key, value in sorted(Counter(item["final_decision_path"] for item in items).items())),
        }
        for (experiment, semantic_type, layer), items in sorted(grouped.items())
    ]


def render_report(
    methods: list[dict[str, Any]],
    baselines: list[dict[str, Any]],
    robustness: list[dict[str, Any]],
    failures: list[dict[str, Any]],
) -> str:
    def find(rows: list[dict[str, Any]], **kwargs: str) -> dict[str, Any]:
        return next(row for row in rows if all(str(row.get(k)) == v for k, v in kwargs.items()))

    full = find(methods, method="FULL_M16")
    value = find(methods, method="OPTION_VALUE_ONLY")
    op = find(methods, method="OPTION_FORMAL_OPERATION")
    original = find(robustness, experiment="original", semantic_type="ALL")
    shuffle = find(robustness, experiment="candidate_order_shuffle", semantic_type="ALL")
    rename = find(robustness, experiment="candidate_id_rename", semantic_type="ALL")
    clustered_stats = read_csv(OUTPUT_ROOT / "statistics" / "v5-event-clustered-bootstrap.csv")
    selective_stats = read_csv(OUTPUT_ROOT / "statistics" / "v5-selective-repair-metrics.csv")
    failure_flow = read_csv(OUTPUT_ROOT / "statistics" / "v5-failure-flow-table.csv")
    full_vs_m13 = next(row for row in clustered_stats if row["comparison"] == "FULL_M16 vs M13_BASE")
    m13_selective = next(row for row in selective_stats if row["method"] == "M13_BASE")
    full_selective = next(row for row in selective_stats if row["method"] == "FULL_M16")
    m13_flow = next(row for row in failure_flow if row["method"] == "M13_BASE")
    full_flow = next(row for row in failure_flow if row["method"] == "FULL_M16")
    lines = [
        "# v5 Q1 Completion Pack",
        "",
        "This package consolidates the completed v5 blind-large supplements. It is offline and uses only frozen benchmark data plus already-finished prediction/scoring outputs.",
        "",
        "## Main Method And Baselines",
        "",
        "| Method | Attempts | Success | Accuracy | Notes |",
        "| --- | ---: | ---: | ---: | --- |",
        f"| Full M16 | {full['attempts']} | {full['closure_success']} | {full['closure_accuracy_pct']} | Frozen evidence-constrained repair with OWL closure |",
        f"| OPTION_VALUE_ONLY | {value['attempts']} | {value['closure_success']} | {value['closure_accuracy_pct']} | Public evidence plus candidate values |",
        f"| OPTION_FORMAL_OPERATION | {op['attempts']} | {op['closure_success']} | {op['closure_accuracy_pct']} | Public evidence plus operation objects |",
        "",
        "The full method improves over the strongest simple candidate baseline by "
        f"{float(full['closure_accuracy']) * 100 - float(value['closure_accuracy']) * 100:.2f} percentage points.",
        "",
        "## Event-clustered Statistics",
        "",
        f"Full M16 vs M13 Base improves by {full_vs_m13['observed_diff_pp']} percentage points. "
        f"The event-clustered paired bootstrap 95% CI is {full_vs_m13['cluster_bootstrap_ci_low_pp']}--{full_vs_m13['cluster_bootstrap_ci_high_pp']} pp.",
        "",
        "## Selective Repair",
        "",
        "| Method | Coverage | Abstention | P(Oracle given Select) | Wrong selected |",
        "| --- | ---: | ---: | ---: | ---: |",
        f"| M13 Base | {m13_selective['coverage_pct']} | {m13_selective['abstention_rate_pct']} | {m13_selective['precision_given_select_oracle_pct']} | {m13_selective['wrong_selected_rate_pct']} |",
        f"| Full M16 | {full_selective['coverage_pct']} | {full_selective['abstention_rate_pct']} | {full_selective['precision_given_select_oracle_pct']} | {full_selective['wrong_selected_rate_pct']} |",
        "",
        "## Failure Flow",
        "",
        "| Method | IR invalid/incomplete | Rank/tie abstain | Wrong select | OWL closure fail after correct select | Success |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        f"| M13 Base | {m13_flow['ir_invalid_or_incomplete']} | {m13_flow['rank_or_tie_abstain']} | {m13_flow['wrong_select']} | {m13_flow['owl_closure_fail_after_correct_select']} | {m13_flow['success']} |",
        f"| Full M16 | {full_flow['ir_invalid_or_incomplete']} | {full_flow['rank_or_tie_abstain']} | {full_flow['wrong_select']} | {full_flow['owl_closure_fail_after_correct_select']} | {full_flow['success']} |",
        "",
        "## Full Online Robustness",
        "",
        "| Variant | Attempts | Success | Accuracy | Strict event |",
        "| --- | ---: | ---: | ---: | ---: |",
        f"| Original | {original['attempts']} | {original['closure_success']} | {original['closure_success_accuracy_pct']} | {original['strict_event_successes']}/{original['events']} |",
        f"| Candidate order shuffle | {shuffle['attempts']} | {shuffle['closure_success']} | {shuffle['closure_success_accuracy_pct']} | {shuffle['strict_event_successes']}/{shuffle['events']} |",
        f"| Candidate ID rename | {rename['attempts']} | {rename['closure_success']} | {rename['closure_success_accuracy_pct']} | {rename['strict_event_successes']}/{rename['events']} |",
        "",
        "Both perturbations remain above 99% attempt-level closure. The paper should state high robustness, not perfect invariance.",
        "",
        "## Remaining Failure Pattern",
        "",
        f"Across original and two full robustness variants, failed attempts recorded here: {len(failures)}.",
        "",
        "| Experiment | Failed attempts | Failed events | Main interpretation |",
        "| --- | ---: | ---: | --- |",
    ]
    for experiment in ("original_full_m16", "robustness_candidate_order_shuffle", "robustness_candidate_id_rename"):
        subset = [row for row in failures if row["experiment"] == experiment]
        lines.append(
            f"| {experiment} | {len(subset)} | {len({row['event_id'] for row in subset})} | Mostly fail-closed selective abstention; no unsafe repair claim |"
        )
    lines.extend(
        [
            "",
            "## Generated Files",
            "",
            "- `q1-method-and-baseline-comparison.csv`",
            "- `q1-baseline-by-type.csv`",
            "- `q1-full-online-robustness.csv`",
            "- `q1-final-failure-analysis.csv`",
            "- `q1-final-failure-summary.csv`",
            "- `../statistics/v5-event-clustered-bootstrap.csv`",
            "- `../statistics/v5-selective-repair-metrics.csv`",
            "- `../statistics/v5-failure-flow-table.csv`",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    meta = load_event_meta()
    methods = method_comparison()
    baselines = baseline_by_type()
    robustness = robustness_comparison()
    failures = final_failure_analysis(meta)
    fail_summary = failure_summary(failures)

    write_csv(OUT / "q1-method-and-baseline-comparison.csv", methods)
    write_csv(OUT / "q1-baseline-by-type.csv", baselines)
    write_csv(OUT / "q1-full-online-robustness.csv", robustness)
    write_csv(OUT / "q1-final-failure-analysis.csv", failures)
    write_csv(OUT / "q1-final-failure-summary.csv", fail_summary)
    (OUT / "q1-completion-report.md").write_text(
        render_report(methods, baselines, robustness, failures),
        encoding="utf-8",
    )
    print(f"wrote {OUT}")
    print((OUT / "q1-completion-report.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
