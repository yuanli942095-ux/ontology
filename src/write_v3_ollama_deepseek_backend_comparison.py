"""Ollama vs DeepSeek backend substitution comparison on v3 holdout (typed GRE/CSS 0.24)."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from semantic_v2_common import PROJECT_DIR

OLLAMA = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v3-large"
    / "final-blind-eval-r5-m16-ollama-typed-gre-css-024"
)
DEEPSEEK = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v3-large"
    / "final-blind-eval-r5-m16-deepseek-typed-gre-css-024"
)
OUT = OLLAMA / "backend-comparison-20260831"
ORACLE = (
    PROJECT_DIR
    / "benchmark"
    / "external-real-holdout-v3-large"
    / "private"
    / "oracle"
    / "external-real-oracle-template.csv"
)


def read_csv(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(path.open(encoding="utf-8-sig")))


def truthy(value: str) -> bool:
    return str(value).lower() in {"true", "1", "yes"}


def attempt_key(row: dict[str, str]) -> tuple[str, str]:
    return row["event_id"], row["run"]


def strict_fail_events(details: list[dict[str, str]]) -> list[str]:
    by_event: dict[str, list[bool]] = defaultdict(list)
    for row in details:
        by_event[row["event_id"]].append(truthy(row["closure_success"]))
    return sorted(event_id for event_id, vals in by_event.items() if not all(vals))


def m13_quality_stats(root: Path) -> dict[str, int | str]:
    raw_dir = root / "main-method/m13-pilot/arm-d-rule-refinement/raw_window_metadata_light/raw"
    faith = Counter()
    invalid = timeout = http_error = 0
    for path in raw_dir.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        audit = payload.get("m13_rule_refinement_audit", {})
        status = str(audit.get("faithfulness_status", "unknown"))
        faith[status] += 1
        if payload.get("status") == "INVALID_SCHEMA":
            invalid += 1
        if "TimeoutError" in status:
            timeout += 1
        if "HTTPError" in status:
            http_error += 1
    return {
        "raw_total": sum(faith.values()),
        "faithful": faith.get("faithful", 0),
        "not_answerable": faith.get("not_answerable", 0),
        "value_not_supported": faith.get("value_not_supported", 0),
        "timeout_errors": timeout,
        "http_errors": http_error,
        "invalid_schema": invalid,
        "other_faith": sum(
            count
            for key, count in faith.items()
            if key
            not in {"faithful", "not_answerable", "value_not_supported", "error:TimeoutError", "error:HTTPError"}
        ),
    }


def write_summary_comparison() -> Path:
    out = OUT / "ollama-deepseek-summary-comparison.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    o_summary = {row["semantic_type"]: row for row in read_csv(OLLAMA / "m16-full-holdout-combined/m16-full-holdout-combined-summary.csv")}
    d_summary = {row["semantic_type"]: row for row in read_csv(DEEPSEEK / "m16-full-holdout-combined/m16-full-holdout-combined-summary.csv")}
    types = ["ALL", "TEMPORAL_VERSION", "GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE"]
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "semantic_type",
                "metric",
                "ollama_qwen35_9b",
                "deepseek_api",
                "ollama_minus_deepseek",
            ]
        )
        for semantic_type in types:
            for metric in ("closure_accuracy", "strict_event_accuracy"):
                o_val = float(o_summary[semantic_type][metric])
                d_val = float(d_summary[semantic_type][metric])
                writer.writerow([semantic_type, metric, f"{o_val:.4f}", f"{d_val:.4f}", f"{o_val - d_val:+.4f}"])
        writer.writerow([])
        writer.writerow(["notes", "ollama_strict_fail_events", ";".join(strict_fail_events(read_csv(OLLAMA / "m16-full-holdout-combined/m16-full-holdout-combined-details.csv")))])
        writer.writerow(["notes", "deepseek_strict_fail_events", ";".join(strict_fail_events(read_csv(DEEPSEEK / "m16-full-holdout-combined/m16-full-holdout-combined-details.csv")))])
    return out


def write_attempt_diffs() -> Path:
    ollama = {attempt_key(row): row for row in read_csv(OLLAMA / "m16-full-holdout-combined/m16-full-holdout-combined-details.csv")}
    deepseek = {attempt_key(row): row for row in read_csv(DEEPSEEK / "m16-full-holdout-combined/m16-full-holdout-combined-details.csv")}
    oracle = {row["event_id"]: row for row in read_csv(ORACLE)}
    m13_ollama = {attempt_key(row): row for row in read_csv(OLLAMA / "main-method/m13-pilot/arm-d-rule-refinement/ir/auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827-details.csv")}
    m13_deepseek = {attempt_key(row): row for row in read_csv(DEEPSEEK / "main-method/m13-pilot/arm-d-rule-refinement/ir/auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827-details.csv")}
    m14_ollama = {attempt_key(row): row for row in read_csv(OLLAMA / "m14-clause-level-gre-css-recovery/ir/m14-clause-level-gre-css-recovery-v4-ir-details.csv")}
    m14_deepseek = {attempt_key(row): row for row in read_csv(DEEPSEEK / "m14-clause-level-gre-css-recovery/ir/m14-clause-level-gre-css-recovery-v4-ir-details.csv")}
    m16_ollama = {attempt_key(row): row for row in read_csv(OLLAMA / "m16-candidate-entailment/m16-candidate-entailment-details.csv")}
    m16_deepseek = {attempt_key(row): row for row in read_csv(DEEPSEEK / "m16-candidate-entailment/m16-candidate-entailment-details.csv")}

    rows_out: list[dict[str, str]] = []
    for key in sorted(ollama):
        o_row = ollama[key]
        d_row = deepseek[key]
        o_ok = truthy(o_row["closure_success"])
        d_ok = truthy(d_row["closure_success"])
        if o_ok == d_ok:
            continue
        event_id, run = key
        oracle_row = oracle.get(event_id, {})
        m13o = m13_ollama.get(key, {})
        m13d = m13_deepseek.get(key, {})
        m14o = m14_ollama.get(key, {})
        m14d = m14_deepseek.get(key, {})
        m16o = m16_ollama.get(key, {})
        m16d = m16_deepseek.get(key, {})
        if o_ok and not d_ok:
            diff_kind = "ollama_pass_deepseek_fail"
        else:
            diff_kind = "ollama_fail_deepseek_pass"
        rows_out.append(
            {
                "diff_kind": diff_kind,
                "event_id": event_id,
                "semantic_type": o_row["semantic_type"],
                "run": run,
                "oracle_candidate_id": oracle_row.get("oracle_candidate_id", ""),
                "ollama_closure_success": o_row["closure_success"],
                "deepseek_closure_success": d_row["closure_success"],
                "ollama_used_stage": o_row["used"],
                "deepseek_used_stage": d_row["used"],
                "ollama_final_path": o_row["final_decision_path"],
                "deepseek_final_path": d_row["final_decision_path"],
                "ollama_m13_closure": m13o.get("full_closure_success", ""),
                "deepseek_m13_closure": m13d.get("full_closure_success", ""),
                "ollama_m13_path": m13o.get("decision_path", ""),
                "deepseek_m13_path": m13d.get("decision_path", ""),
                "ollama_m14_path": m14o.get("decision_path", ""),
                "deepseek_m14_path": m14d.get("decision_path", ""),
                "ollama_m14_selected": m14o.get("selected_candidate_id", ""),
                "deepseek_m14_selected": m14d.get("selected_candidate_id", ""),
                "ollama_m16_path": m16o.get("decision_path", ""),
                "deepseek_m16_path": m16d.get("decision_path", ""),
            }
        )

    out = OUT / "ollama-deepseek-attempt-diffs.csv"
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows_out[0].keys()) if rows_out else ["diff_kind"])
        writer.writeheader()
        writer.writerows(rows_out)
    return out


def write_markdown() -> Path:
    o_details = read_csv(OLLAMA / "m16-full-holdout-combined/m16-full-holdout-combined-details.csv")
    d_details = read_csv(DEEPSEEK / "m16-full-holdout-combined/m16-full-holdout-combined-details.csv")
    o_summary = {row["semantic_type"]: row for row in read_csv(OLLAMA / "m16-full-holdout-combined/m16-full-holdout-combined-summary.csv")}
    d_summary = {row["semantic_type"]: row for row in read_csv(DEEPSEEK / "m16-full-holdout-combined/m16-full-holdout-combined-summary.csv")}
    o_strict = strict_fail_events(o_details)
    d_strict = strict_fail_events(d_details)

    ollama = {attempt_key(row): row for row in o_details}
    deepseek = {attempt_key(row): row for row in d_details}
    o_only_fail = sum(1 for key in ollama if not truthy(ollama[key]["closure_success"]) and truthy(deepseek[key]["closure_success"]))
    d_only_fail = sum(1 for key in ollama if truthy(ollama[key]["closure_success"]) and not truthy(deepseek[key]["closure_success"]))
    both_fail = sum(1 for key in ollama if not truthy(ollama[key]["closure_success"]) and not truthy(deepseek[key]["closure_success"]))

    m13_ollama = m13_quality_stats(OLLAMA)

    cases = {
        "H3_E009": {
            "pattern": "Ollama base/M14 ranks CAND_002 on all 5 runs; DeepSeek ties at M13/M14 and only M16 rescues 2/5 runs.",
            "ollama_edge": "Stronger M13 IR separation on CSS trust-scope wording.",
            "deepseek_gap": "IR_RANK_TIE_ABSTAIN on runs 1/2/4.",
        },
        "H3_E110": {
            "pattern": "Shared hard failure: M14 IR_RANK_TIE_ABSTAIN on all 5 runs for both backends.",
            "ollama_edge": "none",
            "deepseek_gap": "none",
        },
        "H3_E135": {
            "pattern": "Both backends abstain at M14; DeepSeek M16 entailment rescues 4/5 runs, Ollama M16 abstains on all 5.",
            "ollama_edge": "none",
            "deepseek_gap": "M16_ENTAILMENT_SELECT vs M16_ENTAILMENT_ABSTAIN.",
        },
        "H3_E185": {
            "pattern": "Ollama-only strict-fail event. DeepSeek M14 selects CAND_002; Ollama M14 picks wrong CAND_001 or abstains.",
            "ollama_edge": "none",
            "deepseek_gap": "M14 ranking margin flips between CAND_001 and CAND_002.",
        },
    }

    lines = [
        "# v3 Holdout Backend Comparison: Ollama vs DeepSeek",
        "",
        f"Output dir: `{OUT.relative_to(PROJECT_DIR)}`.",
        "",
        "## Scope",
        "",
        "- Same frozen M16 pipeline and typed GRE/CSS 0.24 gates.",
        "- Only backend substitution changes: Ollama `qwen3.5:9b` vs DeepSeek API.",
        "- This is a backend robustness check on the fixed v3 holdout, not an open-world generalization claim.",
        "",
        "## Headline metrics",
        "",
        "| Metric | Ollama | DeepSeek | Delta |",
        "|--------|--------|----------|-------|",
        f"| Closure | {o_summary['ALL']['closure_success']}/{o_summary['ALL']['attempts']} ({float(o_summary['ALL']['closure_accuracy']):.2%}) | {d_summary['ALL']['closure_success']}/{d_summary['ALL']['attempts']} ({float(d_summary['ALL']['closure_accuracy']):.2%}) | {float(o_summary['ALL']['closure_accuracy']) - float(d_summary['ALL']['closure_accuracy']):+.2%} |",
        f"| Strict event | {o_summary['ALL']['strict_event_successes']}/{o_summary['ALL']['events']} ({float(o_summary['ALL']['strict_event_accuracy']):.2%}) | {d_summary['ALL']['strict_event_successes']}/{d_summary['ALL']['events']} ({float(d_summary['ALL']['strict_event_accuracy']):.2%}) | {float(o_summary['ALL']['strict_event_accuracy']) - float(d_summary['ALL']['strict_event_accuracy']):+.2%} |",
        "",
        "## Attempt-level disagreement",
        "",
        f"- Both pass: {1100 - o_only_fail - d_only_fail - both_fail}/1100",
        f"- Ollama fail / DeepSeek pass: **{o_only_fail}** attempts",
        f"- Ollama pass / DeepSeek fail: **{d_only_fail}** attempts",
        f"- Both fail: **{both_fail}** attempts",
        "",
        "## Strict-fail events",
        "",
        f"- Ollama only: `{';'.join(e for e in o_strict if e not in d_strict) or '-'}`",
        f"- DeepSeek only: `{';'.join(e for e in d_strict if e not in o_strict) or '-'}`",
        f"- Shared: `{';'.join(e for e in o_strict if e in d_strict) or '-'}`",
        "",
        "## M13 generation quality (Ollama)",
        "",
        f"- Raw total: {m13_ollama['raw_total']}",
        f"- Faithful: {m13_ollama['faithful']}",
        f"- Not answerable: {m13_ollama['not_answerable']}",
        f"- Invalid schema: {m13_ollama['invalid_schema']}",
        f"- Timeout errors: {m13_ollama['timeout_errors']}",
        f"- HTTP errors: {m13_ollama['http_errors']}",
        "",
        "## Case notes",
        "",
        "| Event | Type | Pattern |",
        "|-------|------|---------|",
    ]
    for event_id, case in cases.items():
        semantic_type = next(row["semantic_type"] for row in o_details if row["event_id"] == event_id)
        lines.append(f"| {event_id} | {semantic_type} | {case['pattern']} |")

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "1. **Strict event parity (217/220)** masks backend-specific attempt noise: Ollama trades 9 lost attempts mostly on `H3_E135`/`H3_E185` against 3 gained attempts on `H3_E009`.",
            "2. **Shared tail (`H3_E110`)** is backend-independent: zero-score M14 ties on JSON null/true/false serialization.",
            "3. **Ollama weakness is downstream LLM judgment**, not TEMPORAL routing: TEMPORAL stays 100% for both backends.",
            "4. **`H3_E185` is the only Ollama-only strict-fail event**: M14 ranking margin is fragile when local model draft/IR shifts token overlap between CAND_001 and CAND_002.",
            "5. **`H3_E135` shows M16 rescue asymmetry**: identical M14 abstain, but DeepSeek entailment verifier selects oracle on 4/5 runs while Ollama abstains on all 5.",
            "",
            "## Artifacts",
            "",
            "- `ollama-deepseek-summary-comparison.csv`",
            "- `ollama-deepseek-attempt-diffs.csv`",
            "",
        ]
    )
    out = OUT / "ollama-deepseek-backend-comparison.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> int:
    summary = write_summary_comparison()
    diffs = write_attempt_diffs()
    report = write_markdown()
    print(summary)
    print(diffs)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
