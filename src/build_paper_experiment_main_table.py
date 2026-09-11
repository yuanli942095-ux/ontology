from __future__ import annotations

"""Build a paper-level experiment summary table from existing result files.

The table intentionally separates candidate-selection accuracy from OWL repair
closure.  Some ablations evaluate selection only, while Auto Policy V2/V3 are
connected to executable candidate repair and Reasoner/CQ closure.
"""

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(r"G:\LearnAI\ontology-evolution")
OUT = ROOT / "output"
PREFIX = "paper-experiment-main-table"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def pct(value: str | float | int | None) -> str:
    if value in (None, ""):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def ms(value: str | float | int | None) -> str:
    if value in (None, ""):
        return "n/a"
    return f"{float(value):.0f}"


def count(value: str | float | int | None) -> str:
    if value in (None, ""):
        return "n/a"
    return str(int(float(value)))


def ratio(successes: str | float | int, attempts: str | float | int) -> str:
    return f"{count(successes)}/{count(attempts)}"


def find_row(rows: list[dict[str, str]], key: str, value: str) -> dict[str, str]:
    matches = [row for row in rows if row.get(key) == value]
    if len(matches) != 1:
        raise RuntimeError(f"expected one row where {key}={value}, got {len(matches)}")
    return matches[0]


def sum_generation_tokens(path: Path) -> int:
    total = 0
    for row in read_csv(path):
        total += int(float(row.get("prompt_eval_count") or 0))
        total += int(float(row.get("eval_count") or 0))
    return total


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    lines = []
    lines.append("| " + " | ".join(columns) + " |")
    lines.append("|" + "|".join("---" for _ in columns) + "|")
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(column, "")) for column in columns) + " |")
    return "\n".join(lines)


def main() -> int:
    candidate_summary = [
        row
        for row in read_csv(OUT / "external-real-v1-candidate-ablation-r5-seed20260820-summary.csv")
        if row["semantic_type"] == "ALL"
    ]
    event_level = read_csv(OUT / "external-real-v1-candidate-ablation-r5-seed20260820-event-level.csv")
    runtime = read_csv(OUT / "external-real-v1-candidate-ablation-r5-seed20260820-runtime-policy-costs-summary.csv")
    v2_repair = find_row(read_csv(OUT / "auto-policy-v2-candidate-repair-r5-seed20260820-summary.csv"), "experiment", "AUTO_POLICY_V2_CANDIDATE_REPAIR")
    v3_repair = find_row(read_csv(OUT / "auto-policy-v3-candidate-repair-r5-seed20260820-summary.csv"), "experiment", "AUTO_POLICY_V3_CANDIDATE_REPAIR_R5_SEED20260820")
    v2_gen = read_json(OUT / "auto-policy-v2" / "auto-policy-v2-generation-summary.json")
    v3_gen = read_json(OUT / "auto-policy-v3" / "auto-policy-v3-generation-summary.json")
    negative = find_row(read_csv(OUT / "auto-policy-v3-negative-safety-r1-seed20260827-summary.csv"), "experiment", "AUTO_POLICY_V3_NEGATIVE_SAFETY")
    gate_normal = find_row(read_csv(OUT / "auto-policy-v3-conflict-gate-r1-seed20260827-by-dataset.csv"), "dataset", "NORMAL")
    gate_negative = find_row(read_csv(OUT / "auto-policy-v3-conflict-gate-r1-seed20260827-by-dataset.csv"), "dataset", "NEGATIVE")
    stats_metrics = read_csv(OUT / "auto-policy-v3-statistical-analysis-metrics.csv")
    paired = read_csv(OUT / "auto-policy-v3-statistical-analysis-paired-tests.csv")

    stats_lookup = {(row["label"], row["metric"]): row for row in stats_metrics}
    paired_lookup = {(row["comparison"], row["metric"]): row for row in paired}

    candidate_meta = {
        "DIRECT_FREE": {
            "paper_role": "Direct baseline",
            "input_boundary": "public evidence; no candidate list",
            "manual_policy": "No",
            "candidate_description": "No",
            "online_oracle": "No",
            "closure": "not run in this ablation",
            "negative": "not tested",
            "note": "Free generation baseline; invalid/out-of-candidate outputs count against accuracy.",
        },
        "OPTION_VALUE_ONLY": {
            "paper_role": "Candidate-value baseline",
            "input_boundary": "candidate IDs + display values",
            "manual_policy": "No",
            "candidate_description": "No",
            "online_oracle": "No",
            "closure": "not run in this ablation",
            "negative": "not tested",
            "note": "Tests whether answer-value options alone solve the task.",
        },
        "OPTION_FORMAL_OPERATION": {
            "paper_role": "Formal-operation baseline",
            "input_boundary": "candidate IDs + formal repair operations",
            "manual_policy": "No",
            "candidate_description": "No",
            "online_oracle": "No",
            "closure": "not run in this ablation",
            "negative": "not tested",
            "note": "Uses executable operation shape but no policy facts/rules.",
        },
        "OPTION_FORMAL_POLICY": {
            "paper_role": "Policy-available LLM upper baseline",
            "input_boundary": "candidate operations + manual facts/rules",
            "manual_policy": "Yes",
            "candidate_description": "No",
            "online_oracle": "No",
            "closure": "not run per attempt; candidate artifacts are executable",
            "negative": "not tested",
            "note": "Auditable but depends on manually structured policy.",
        },
        "OPTION_FORMAL_POLICY_HARD_GATE": {
            "paper_role": "Policy-available symbolic upper bound",
            "input_boundary": "manual facts/rules; Qwen fallback only if non-unique",
            "manual_policy": "Yes",
            "candidate_description": "No",
            "online_oracle": "No",
            "closure": "30/30 symbolic closure",
            "negative": "zero/multi-survivor stress only",
            "note": "Do not claim fully automatic; zero model runtime excludes policy construction cost.",
        },
    }

    rows: list[dict[str, Any]] = []
    for method in [
        "DIRECT_FREE",
        "OPTION_VALUE_ONLY",
        "OPTION_FORMAL_OPERATION",
        "OPTION_FORMAL_POLICY",
        "OPTION_FORMAL_POLICY_HARD_GATE",
    ]:
        summary = find_row(candidate_summary, "method", method)
        events = find_row(event_level, "method", method)
        cost = find_row(runtime, "method", method)
        meta = candidate_meta[method]
        rows.append(
            {
                "method": method,
                "paper_role": meta["paper_role"],
                "input_boundary": meta["input_boundary"],
                "manual_policy": meta["manual_policy"],
                "candidate_description": meta["candidate_description"],
                "online_oracle": meta["online_oracle"],
                "qwen_calls": count(cost["qwen_calls"]),
                "qwen_tokens": count(cost["qwen_token_count"]),
                "mean_runtime_ms": ms(cost["mean_runtime_ms_all_attempts"]),
                "policy_cost": count(cost["formal_policy_complexity_points"]) if cost["formal_policy_required"] == "True" else "0",
                "selection_accuracy": f"{pct(summary['oracle_accuracy'])} ({ratio(cost['oracle_successes'], cost['attempts'])})",
                "selection_ci95": f"{pct(summary['oracle_ci95_low'])}-{pct(summary['oracle_ci95_high'])}",
                "strict_events": f"{count(events['all_runs_correct_events'])}/{count(events['independent_events'])}",
                "owl_repair_closure": meta["closure"],
                "negative_safety": meta["negative"],
                "statistical_note": "descriptive",
                "claim_boundary": meta["note"],
            }
        )

    v2_ci = stats_lookup[("V2_FREE_TEXT", "full_closure_accuracy")]
    v3_ci = stats_lookup[("V3_FULL", "full_closure_accuracy")]
    v2_vs_v3 = paired_lookup[("V3 vs V2", "full_closure_success")]
    gate_ci = stats_lookup[("NEGATIVE", "safe_abstain_rate")]
    gate_vs_ungated = paired_lookup[("CONFLICT_GATE vs V3_NEGATIVE_SAFETY", "safe_abstain")]

    rows.extend(
        [
            {
                "method": "AUTO_POLICY_V2",
                "paper_role": "Candidate-blind automatic policy baseline",
                "input_boundary": "public evidence; no candidate IDs/values; no manual policy",
                "manual_policy": "No",
                "candidate_description": "No",
                "online_oracle": "No",
                "qwen_calls": count(v2_gen["attempts"]),
                "qwen_tokens": count(sum_generation_tokens(OUT / "auto-policy-v2" / "auto-policy-v2-generation-details.csv")),
                "mean_runtime_ms": ms(v2_gen["average_runtime_ms"]),
                "policy_cost": "0",
                "selection_accuracy": f"{pct(v2_repair['oracle_accuracy'])} ({ratio(v2_repair['oracle_correct'], v2_repair['attempts'])})",
                "selection_ci95": f"{pct(v2_ci['ci95_low'])}-{pct(v2_ci['ci95_high'])}",
                "strict_events": f"{count(v2_repair['strict_event_successes'])}/{count(v2_repair['events'])}",
                "owl_repair_closure": f"{pct(v2_repair['full_closure_accuracy'])} ({ratio(v2_repair['full_closure_success'], v2_repair['attempts'])})",
                "negative_safety": "not tested",
                "statistical_note": "paired baseline for V3",
                "claim_boundary": "Free-form semantic_result is candidate-blind but normalization is weak.",
            },
            {
                "method": "AUTO_POLICY_V3",
                "paper_role": "Main automatic policy method",
                "input_boundary": "public evidence; canonical output; no candidate IDs/values; no manual policy",
                "manual_policy": "No",
                "candidate_description": "No",
                "online_oracle": "No",
                "qwen_calls": count(v3_gen["attempts"]),
                "qwen_tokens": count(sum_generation_tokens(OUT / "auto-policy-v3" / "auto-policy-v3-generation-details.csv")),
                "mean_runtime_ms": ms(v3_gen["average_runtime_ms"]),
                "policy_cost": "0",
                "selection_accuracy": f"{pct(v3_repair['oracle_accuracy'])} ({ratio(v3_repair['oracle_correct'], v3_repair['attempts'])})",
                "selection_ci95": f"{pct(v3_ci['ci95_low'])}-{pct(v3_ci['ci95_high'])}",
                "strict_events": f"{count(v3_repair['strict_event_successes'])}/{count(v3_repair['events'])}",
                "owl_repair_closure": f"{pct(v3_repair['full_closure_accuracy'])} ({ratio(v3_repair['full_closure_success'], v3_repair['attempts'])})",
                "negative_safety": f"ungated safe abstain {pct(negative['safe_abstain_rate'])}; unsafe {pct(negative['unsafe_selection_rate'])}",
                "statistical_note": f"vs V2 p={float(v2_vs_v3['exact_mcnemar_p']):.2e}",
                "claim_boundary": "Validated on current external-real-v1; not robust to metadata-light distractors.",
            },
            {
                "method": "AUTO_POLICY_V3_PLUS_GATE",
                "paper_role": "Main method with uncertainty gate",
                "input_boundary": "V3 output + candidate-blind uncertainty markers; no Oracle/candidate values",
                "manual_policy": "No",
                "candidate_description": "No",
                "online_oracle": "No",
                "qwen_calls": count(v3_gen["attempts"]),
                "qwen_tokens": count(sum_generation_tokens(OUT / "auto-policy-v3" / "auto-policy-v3-generation-details.csv")),
                "mean_runtime_ms": ms(v3_gen["average_runtime_ms"]),
                "policy_cost": "0",
                "selection_accuracy": f"{pct(gate_normal['oracle_accuracy'])} ({ratio(gate_normal['oracle_correct'], gate_normal['attempts'])})",
                "selection_ci95": f"{pct(v3_ci['ci95_low'])}-{pct(v3_ci['ci95_high'])}",
                "strict_events": "30/30 normal set",
                "owl_repair_closure": f"{pct(v3_repair['full_closure_accuracy'])} ({ratio(v3_repair['full_closure_success'], v3_repair['attempts'])})",
                "negative_safety": f"safe abstain {pct(gate_negative['safe_abstain_rate'])} ({ratio(gate_negative['safe_abstains'], gate_negative['attempts'])}); unsafe {pct(gate_negative['unsafe_selection_rate'])}",
                "statistical_note": f"gate vs ungated p={float(gate_vs_ungated['exact_mcnemar_p']):.2e}; safe-abstain CI {pct(gate_ci['ci95_low'])}-{pct(gate_ci['ci95_high'])}",
                "claim_boundary": "Gate is rule-based and validated on tested synthetic uncertainty markers.",
            },
        ]
    )

    csv_path = OUT / f"{PREFIX}.csv"
    md_path = OUT / f"{PREFIX}.md"
    json_path = OUT / f"{PREFIX}.json"
    write_csv(csv_path, rows)

    main_columns = [
        "method",
        "paper_role",
        "manual_policy",
        "qwen_calls",
        "qwen_tokens",
        "policy_cost",
        "selection_accuracy",
        "selection_ci95",
        "strict_events",
        "owl_repair_closure",
        "negative_safety",
        "statistical_note",
    ]
    md_lines = [
        "# Paper Experiment Main Table",
        "",
        markdown_table(rows, main_columns),
        "",
        "Notes:",
        "",
        "- `policy_cost` is the existing heuristic formal-policy complexity score, not measured annotation time.",
        "- Candidate-ablation rows report Oracle candidate-selection accuracy; only rows with an explicit closure value were connected to OWL repair artifacts plus Reasoner/CQ checks.",
        "- `AUTO_POLICY_V3_PLUS_GATE` uses the same V3 generation calls and adds a no-Qwen uncertainty gate over generated status/canonical output/evidence markers.",
        "- Oracle rows are loaded only after predictions/selections are fixed in the referenced evaluation scripts.",
        "",
        "Evidence files:",
        "",
        "- `output/external-real-v1-candidate-ablation-r5-seed20260820-summary.csv`",
        "- `output/external-real-v1-candidate-ablation-r5-seed20260820-runtime-policy-costs-summary.csv`",
        "- `output/auto-policy-v2-candidate-repair-r5-seed20260820-summary.csv`",
        "- `output/auto-policy-v3-candidate-repair-r5-seed20260820-summary.csv`",
        "- `output/auto-policy-v3-conflict-gate-r1-seed20260827-by-dataset.csv`",
        "- `output/auto-policy-v3-statistical-analysis-metrics.csv`",
        "- `output/auto-policy-v3-statistical-analysis-paired-tests.csv`",
    ]
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "csv": str(csv_path),
                "markdown": str(md_path),
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"csv={csv_path}")
    print(f"markdown={md_path}")
    print(f"json={json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
