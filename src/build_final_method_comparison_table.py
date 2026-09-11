from __future__ import annotations

"""Build the final method comparison table including natural evidence fixes."""

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(r"G:\LearnAI\ontology-evolution")
OUT = ROOT / "output"
PREFIX = "final-method-comparison-table"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


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


def find_row(rows: list[dict[str, str]], key: str, value: str) -> dict[str, str]:
    matches = [row for row in rows if row.get(key) == value]
    if len(matches) != 1:
        raise RuntimeError(f"expected one row where {key}={value}, got {len(matches)}")
    return matches[0]


def metric(metrics: list[dict[str, str]], label: str, metric_name: str) -> dict[str, str]:
    matches = [row for row in metrics if row["label"] == label and row["metric"] == metric_name]
    if len(matches) != 1:
        raise RuntimeError(f"expected metric {label}/{metric_name}, got {len(matches)}")
    return matches[0]


def paired(paired_rows: list[dict[str, str]], comparison: str, metric_name: str) -> dict[str, str]:
    matches = [row for row in paired_rows if row["comparison"] == comparison and row["metric"] == metric_name]
    if len(matches) != 1:
        raise RuntimeError(f"expected paired {comparison}/{metric_name}, got {len(matches)}")
    return matches[0]


def pct(value: str | float | int | None) -> str:
    if value in (None, ""):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def ci(metric_row: dict[str, str]) -> str:
    return f"{pct(metric_row['ci95_low'])}-{pct(metric_row['ci95_high'])}"


def count(value: str | float | int | None) -> str:
    if value in (None, ""):
        return "n/a"
    return str(int(float(value)))


def ratio(successes: str | float | int, attempts: str | float | int) -> str:
    return f"{count(successes)}/{count(attempts)}"


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    lines = ["| " + " | ".join(columns) + " |", "|" + "|".join("---" for _ in columns) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(column, "")) for column in columns) + " |")
    return "\n".join(lines)


def main() -> int:
    candidate_summary = [
        row
        for row in read_csv(OUT / "external-real-v1-candidate-ablation-r5-seed20260820-summary.csv")
        if row["semantic_type"] == "ALL"
    ]
    candidate_event = read_csv(OUT / "external-real-v1-candidate-ablation-r5-seed20260820-event-level.csv")
    v3_repair = find_row(
        read_csv(OUT / "auto-policy-v3-candidate-repair-r5-seed20260820-summary.csv"),
        "experiment",
        "AUTO_POLICY_V3_CANDIDATE_REPAIR_R5_SEED20260820",
    )
    v2_repair = find_row(
        read_csv(OUT / "auto-policy-v2-candidate-repair-r5-seed20260820-summary.csv"),
        "experiment",
        "AUTO_POLICY_V2_CANDIDATE_REPAIR",
    )
    raw_short = find_row(
        read_csv(OUT / "auto-policy-v3-natural-evidence-raw_short_context-candidate-repair-r1-seed20260827-summary.csv"),
        "experiment",
        "AUTO_POLICY_V3_NATURAL_EVIDENCE_RAW_SHORT_CONTEXT_CANDIDATE_REPAIR_R1_SEED20260827",
    )
    raw_prov = find_row(
        read_csv(OUT / "auto-policy-v3-natural-evidence-raw_provenance_context-candidate-repair-r1-seed20260827-summary.csv"),
        "experiment",
        "AUTO_POLICY_V3_NATURAL_EVIDENCE_RAW_PROVENANCE_CONTEXT_CANDIDATE_REPAIR_R1_SEED20260827",
    )
    raw_prov_light = find_row(
        read_csv(OUT / "auto-policy-v3-natural-evidence-raw_provenance_metadata_light-candidate-repair-r1-seed20260827-summary.csv"),
        "experiment",
        "AUTO_POLICY_V3_NATURAL_EVIDENCE_RAW_PROVENANCE_METADATA_LIGHT_CANDIDATE_REPAIR_R1_SEED20260827",
    )
    natural_safety = find_row(
        read_csv(OUT / "auto-policy-v3-natural-conflict-safety-r1-seed20260827-summary.csv"),
        "experiment",
        "AUTO_POLICY_V3_NATURAL_CONFLICT_SAFETY",
    )
    natural_gate_normal = find_row(
        read_csv(OUT / "auto-policy-v3-natural-conflict-gate-r1-seed20260827-by-dataset.csv"),
        "dataset",
        "NORMAL",
    )
    natural_gate_conflict = find_row(
        read_csv(OUT / "auto-policy-v3-natural-conflict-gate-r1-seed20260827-by-dataset.csv"),
        "dataset",
        "NATURAL_CONFLICT",
    )
    natural_real_conflict = find_row(
        read_csv(OUT / "natural-conflict-real-v1-gate-r5-seed20260820-summary.csv"),
        "expected",
        "ABSTAIN",
    )
    ext_metrics = read_csv(OUT / "auto-policy-v3-extended-statistical-analysis-metrics.csv")
    ext_paired = read_csv(OUT / "auto-policy-v3-extended-statistical-analysis-paired-tests.csv")
    old_metrics = read_csv(OUT / "auto-policy-v3-statistical-analysis-metrics.csv")
    old_paired = read_csv(OUT / "auto-policy-v3-statistical-analysis-paired-tests.csv")

    v3_ci = metric(old_metrics, "V3_FULL", "full_closure_accuracy")
    v2_ci = metric(old_metrics, "V2_FREE_TEXT", "full_closure_accuracy")
    raw_short_ci = metric(ext_metrics, "RAW_SHORT_CONTEXT", "full_closure_accuracy")
    raw_prov_ci = metric(ext_metrics, "RAW_PROVENANCE_CONTEXT", "full_closure_accuracy")
    raw_prov_light_ci = metric(ext_metrics, "RAW_PROVENANCE_METADATA_LIGHT", "full_closure_accuracy")
    natural_orig_ci = metric(ext_metrics, "ORIGINAL_NATURAL_GATE", "safe_abstain_rate")
    natural_gate_ci = metric(ext_metrics, "ENHANCED_NATURAL_GATE", "safe_abstain_rate")
    v3_vs_v2 = paired(old_paired, "V3 vs V2", "full_closure_success")
    raw_pair = paired(ext_paired, "RAW_PROVENANCE_CONTEXT vs RAW_SHORT_CONTEXT", "full_closure_success")
    metadata_pair = paired(ext_paired, "RAW_PROVENANCE_CONTEXT vs RAW_PROVENANCE_METADATA_LIGHT", "full_closure_success")
    gate_pair = paired(ext_paired, "ENHANCED_NATURAL_GATE vs ORIGINAL_NATURAL_GATE", "safe_abstain")

    rows: list[dict[str, Any]] = []
    for method, role in [
        ("DIRECT_FREE", "Direct LLM baseline"),
        ("OPTION_VALUE_ONLY", "Candidate value baseline"),
        ("OPTION_FORMAL_POLICY", "Manual policy available LLM upper baseline"),
        ("OPTION_FORMAL_POLICY_HARD_GATE", "Manual policy symbolic upper bound"),
    ]:
        summary = find_row(candidate_summary, "method", method)
        event = find_row(candidate_event, "method", method)
        rows.append(
            {
                "method": method,
                "paper_role": role,
                "evidence_condition": "structured evidence note",
                "manual_policy": "Yes" if "FORMAL_POLICY" in method else "No",
                "automatic_policy": "No",
                "selection_or_closure": pct(summary["oracle_accuracy"]),
                "ci95": f"{pct(summary['oracle_ci95_low'])}-{pct(summary['oracle_ci95_high'])}",
                "strict_events": f"{count(event['all_runs_correct_events'])}/{count(event['independent_events'])}",
                "natural_conflict_safe_abstain": "not tested",
                "paired_test": "descriptive",
                "claim_boundary": "Candidate-selection ablation; not all variants run through repair closure.",
            }
        )

    rows.extend(
        [
            {
                "method": "AUTO_POLICY_V2",
                "paper_role": "Candidate-blind automatic policy baseline",
                "evidence_condition": "structured evidence note",
                "manual_policy": "No",
                "automatic_policy": "Yes",
                "selection_or_closure": pct(v2_repair["full_closure_accuracy"]),
                "ci95": ci(v2_ci),
                "strict_events": f"{count(v2_repair['strict_event_successes'])}/{count(v2_repair['events'])}",
                "natural_conflict_safe_abstain": "not tested",
                "paired_test": "baseline for V3",
                "claim_boundary": "Free-form semantic output; weak normalization.",
            },
            {
                "method": "AUTO_POLICY_V3",
                "paper_role": "Canonical automatic policy method",
                "evidence_condition": "structured evidence note",
                "manual_policy": "No",
                "automatic_policy": "Yes",
                "selection_or_closure": pct(v3_repair["full_closure_accuracy"]),
                "ci95": ci(v3_ci),
                "strict_events": f"{count(v3_repair['strict_event_successes'])}/{count(v3_repair['events'])}",
                "natural_conflict_safe_abstain": pct(natural_safety["safe_abstain_rate"]),
                "paired_test": f"vs V2 p={float(v3_vs_v2['exact_mcnemar_p']):.2e}",
                "claim_boundary": "Strong on structured public evidence notes; not a raw retrieval system.",
            },
            {
                "method": "AUTO_POLICY_V3_RAW_SHORT_CONTEXT",
                "paper_role": "Naive raw paragraph retrieval stress test",
                "evidence_condition": "top local source paragraphs",
                "manual_policy": "No",
                "automatic_policy": "Yes",
                "selection_or_closure": pct(raw_short["full_closure_accuracy"]),
                "ci95": ci(raw_short_ci),
                "strict_events": f"{count(raw_short['strict_event_successes'])}/{count(raw_short['events'])}",
                "natural_conflict_safe_abstain": "not tested",
                "paired_test": "stress condition",
                "claim_boundary": "Shows naive raw retrieval is insufficient.",
            },
            {
                "method": "AUTO_POLICY_V3_RAW_PROVENANCE_CONTEXT",
                "paper_role": "V3 with target-aware provenance retrieval",
                "evidence_condition": "target-code windows ranked by source provenance",
                "manual_policy": "No",
                "automatic_policy": "Yes",
                "selection_or_closure": pct(raw_prov["full_closure_accuracy"]),
                "ci95": ci(raw_prov_ci),
                "strict_events": f"{count(raw_prov['strict_event_successes'])}/{count(raw_prov['events'])}",
                "natural_conflict_safe_abstain": "not tested",
                "paired_test": f"vs RAW_SHORT p={float(raw_pair['exact_mcnemar_p']):.2e}",
                "claim_boundary": "Uses event target metadata and document provenance; not full change discovery.",
            },
            {
                "method": "AUTO_POLICY_V3_RAW_PROVENANCE_METADATA_LIGHT",
                "paper_role": "Target-metadata dependence diagnostic",
                "evidence_condition": "same provenance evidence; title/context/subject/predicate hidden from generation",
                "manual_policy": "No",
                "automatic_policy": "Yes",
                "selection_or_closure": pct(raw_prov_light["full_closure_accuracy"]),
                "ci95": ci(raw_prov_light_ci),
                "strict_events": f"{count(raw_prov_light['strict_event_successes'])}/{count(raw_prov_light['events'])}",
                "natural_conflict_safe_abstain": "not tested",
                "paired_test": f"full metadata vs hidden p={float(metadata_pair['exact_mcnemar_p']):.2e}",
                "claim_boundary": "Shows generation still depends on target metadata for exception/rule events.",
            },
            {
                "method": "AUTO_POLICY_V3_PLUS_NATURAL_CONFLICT_GATE",
                "paper_role": "V3 with provenance retrieval and enhanced conflict gate",
                "evidence_condition": "provenance evidence plus mixed-evidence gate",
                "manual_policy": "No",
                "automatic_policy": "Yes",
                "selection_or_closure": pct(natural_gate_normal["oracle_accuracy"]),
                "ci95": ci(v3_ci),
                "strict_events": "30/30 normal set",
                "natural_conflict_safe_abstain": f"{pct(natural_gate_conflict['safe_abstain_rate'])} ({ratio(natural_gate_conflict['safe_abstain'], natural_gate_conflict['attempts'])})",
                "paired_test": f"natural gate vs original p={float(gate_pair['exact_mcnemar_p']):.2e}",
                "claim_boundary": "Validated on constructed mixed-evidence natural conflict benchmark; needs independent conflict corpus.",
            },
            {
                "method": "AUTO_POLICY_V3_PLUS_REAL_SOURCE_CONFLICT_GATE",
                "paper_role": "Gate stress test on real public-source excerpt mixtures",
                "evidence_condition": "mixed public excerpts from external-real-v1 without synthetic conflict markers",
                "manual_policy": "No",
                "automatic_policy": "Yes",
                "selection_or_closure": "fail-closed safety test",
                "ci95": "n/a",
                "strict_events": f"{count(natural_real_conflict['unique_events'])}/30 covered",
                "natural_conflict_safe_abstain": f"{pct(natural_real_conflict['safe_abstain_rate'])} ({ratio(natural_real_conflict['safe_abstain'], natural_real_conflict['attempts'])})",
                "paired_test": "descriptive real-source stress test",
                "claim_boundary": "Uses real public excerpts but is still constructed from existing benchmark sources, not independently mined incidents.",
            },
        ]
    )

    csv_path = OUT / f"{PREFIX}.csv"
    md_path = OUT / f"{PREFIX}.md"
    json_path = OUT / f"{PREFIX}.json"
    write_csv(csv_path, rows)
    columns = [
        "method",
        "paper_role",
        "evidence_condition",
        "manual_policy",
        "selection_or_closure",
        "ci95",
        "strict_events",
        "natural_conflict_safe_abstain",
        "paired_test",
    ]
    md = [
        "# Final Method Comparison Table",
        "",
        f"Generated at {datetime.now(timezone.utc).isoformat()} UTC.",
        "",
        markdown_table(rows, columns),
        "",
        "## Interpretation",
        "",
        "- `AUTO_POLICY_V3_RAW_SHORT_CONTEXT` quantifies the failure of naive raw-source paragraph retrieval.",
        "- `AUTO_POLICY_V3_RAW_PROVENANCE_CONTEXT` shows that target-aware provenance retrieval restores closure on the current 30-event benchmark.",
        "- `AUTO_POLICY_V3_RAW_PROVENANCE_METADATA_LIGHT` quantifies dependence on event target metadata after retrieval.",
        "- `AUTO_POLICY_V3_PLUS_NATURAL_CONFLICT_GATE` separates normal repair accuracy from fail-closed behavior under mixed natural evidence.",
        "- `AUTO_POLICY_V3_PLUS_REAL_SOURCE_CONFLICT_GATE` adds a stricter real-public-excerpt mixture test without synthetic conflict markers or artificial value rewrites.",
        "- Manual-policy rows are upper baselines and should not be described as fully automatic.",
        "",
    ]
    md_path.write_text("\n".join(md), encoding="utf-8")
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
