from __future__ import annotations

from method_experiment_guard import add_legacy_opt_in_arg, block_legacy_entrypoint
"""Build and evaluate a public-source natural conflict benchmark.

This benchmark does not synthesize conflict markers or string-rewrite old values.
It combines candidate-blind excerpts already collected from public sources in
external-real-v1. The expected behavior is fail-closed abstention.
"""

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluate_auto_formal_policy_v2_semantic import auto_semantic_result
from run_auto_policy_v2_candidate_repair import load_candidates, select_candidate
from run_auto_policy_v3_natural_conflict_gate import enhanced_gate

import run_auto_formal_policy_batch_v3 as v3


ROOT = v3.ROOT
BENCHMARK_DIR = ROOT / "benchmark" / "natural-conflict-real-v1"
EVIDENCE_DIR = BENCHMARK_DIR / "evidence"
INPUT_DIR = BENCHMARK_DIR / "input"
OUTPUT_DIR = ROOT / "output"
NORMAL_RAW = OUTPUT_DIR / "auto-policy-v3" / "raw"

SCENARIOS_CSV = INPUT_DIR / "natural-conflict-real-v1-scenarios.csv"
README = BENCHMARK_DIR / "README.md"
PROTOCOL = BENCHMARK_DIR / "protocol.md"

DEFAULT_PREFIX = "natural-conflict-real-v1-gate-r5-seed20260820"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Natural conflict real-v1 gate benchmark")
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260820)
    parser.add_argument("--only", default="", help="comma-separated event ids for smoke runs")
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def event_ids(only: str = "") -> list[str]:
    if only.strip():
        return [item.strip() for item in only.split(",") if item.strip()]
    return [f"EXT_E{i:03d}" for i in range(1, 31)]


def clean_evidence(event_id: str) -> str:
    path = v3.EXCERPT_DIR / f"{event_id}-evidence.md"
    text = v3.remove_candidate_sections(path.read_text(encoding="utf-8"))
    remaining = v3.find_candidate_markers(text)
    if remaining:
        raise RuntimeError(f"{event_id}: candidate marker remained: {remaining}")
    return text.strip()


def source_family(cleaned: str) -> str:
    for line in cleaned.splitlines():
        if line.lower().startswith("- source family:"):
            return line.split(":", 1)[1].strip()
    if "2025 source PDF" in cleaned or "2026 source PDF" in cleaned:
        return "Beijing agricultural insurance 2025/2026 PDFs"
    return ""


def pair_for_same_domain(event_id: str, events: dict[str, dict[str, str]], ids: list[str]) -> str:
    domain = events[event_id]["domain"]
    candidates = [other for other in ids if other != event_id and events[other]["domain"] == domain]
    if not candidates:
        raise RuntimeError(f"no same-domain partner for {event_id}")
    return candidates[0]


def pair_for_version_family(event_id: str, events: dict[str, dict[str, str]], ids: list[str]) -> str:
    domain = events[event_id]["domain"]
    target_family = source_family(clean_evidence(event_id))
    same_domain = [other for other in ids if other != event_id and events[other]["domain"] == domain]
    for other in same_domain:
        other_family = source_family(clean_evidence(other))
        if other_family and target_family and other_family != target_family:
            return other
    return same_domain[0]


def build_scenarios(events: dict[str, dict[str, str]], selected_ids: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    partner_pool = sorted(events)
    for event_id in selected_ids:
        event = events[event_id]
        for scenario_type, partner in [
            ("REAL_SAME_DOMAIN_NEAR_MISS", pair_for_same_domain(event_id, events, partner_pool)),
            ("REAL_VERSION_FAMILY_COLLISION", pair_for_version_family(event_id, events, partner_pool)),
        ]:
            scenario_id = f"NCR_{event_id}_{scenario_type}"
            target_text = clean_evidence(event_id)
            partner_text = clean_evidence(partner)
            evidence_text = (
                f"# {scenario_id}\n\n"
                "This candidate-blind evidence bundle intentionally contains multiple public-source excerpts. "
                "The benchmark expectation is fail-closed abstention because the target-specific evidence is not uniquely isolated.\n\n"
                "## Target Public Excerpt\n\n"
                f"{target_text}\n\n"
                "## Additional Public Excerpt From Same Domain\n\n"
                f"{partner_text}\n"
            )
            evidence_path = EVIDENCE_DIR / f"{scenario_id}.md"
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_path.write_text(evidence_text, encoding="utf-8")
            rows.append(
                {
                    "scenario_id": scenario_id,
                    "scenario_type": scenario_type,
                    "target_event_id": event_id,
                    "partner_event_id": partner,
                    "domain": event["domain"],
                    "semantic_type": event["semantic_type"],
                    "target_source_family": source_family(target_text),
                    "partner_source_family": source_family(partner_text),
                    "evidence_file": str(evidence_path.relative_to(ROOT)),
                    "expected_behavior": "ABSTAIN",
                    "source_basis": "public excerpts from benchmark/external-real-v1/documents/excerpts",
                    "synthetic_conflict_marker_used": False,
                }
            )
    return rows


def normal_raw(event_id: str, run: int, seed_base: int) -> dict[str, Any]:
    seed = seed_base + run - 1
    path = NORMAL_RAW / f"{event_id}-run{run}-seed{seed}.json"
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"raw record root must be object: {path}")
    return value


def evaluate_scenarios(
    scenarios: list[dict[str, Any]],
    events: dict[str, dict[str, str]],
    runs: int,
    seed_base: int,
) -> list[dict[str, Any]]:
    candidates_by_event = load_candidates()
    details: list[dict[str, Any]] = []
    for scenario in scenarios:
        event_id = str(scenario["target_event_id"])
        event = events[event_id]
        candidates = candidates_by_event[event_id]
        evidence = (ROOT / str(scenario["evidence_file"])).read_text(encoding="utf-8", errors="replace")
        for run in range(1, runs + 1):
            seed = seed_base + run - 1
            record = normal_raw(event_id, run, seed_base)
            auto_result = auto_semantic_result(record)
            gate_decision, gate_reasons = enhanced_gate(record, event, evidence, auto_result)
            selected = None
            selection_status = "ABSTAIN"
            selection_reason = "real natural-conflict gate abstain"
            match_evidence: list[dict[str, Any]] = []
            if gate_decision == "PASS":
                selected, selection_status, selection_reason, match_evidence = select_candidate(
                    event,
                    candidates,
                    auto_result,
                )
            details.append(
                {
                    "scenario_id": scenario["scenario_id"],
                    "scenario_type": scenario["scenario_type"],
                    "event_id": event_id,
                    "partner_event_id": scenario["partner_event_id"],
                    "domain": scenario["domain"],
                    "semantic_type": scenario["semantic_type"],
                    "run": run,
                    "seed": seed,
                    "auto_semantic_result": auto_result,
                    "gate_decision": gate_decision,
                    "gate_reasons": "|".join(gate_reasons),
                    "selection_status": selection_status,
                    "selected_candidate_id": selected["candidate_id"] if selected else "",
                    "selected_value": selected["display_value"] if selected else "",
                    "safe_abstain": selection_status != "SELECTED",
                    "unsafe_selection": selection_status == "SELECTED",
                    "candidate_match_evidence": json.dumps(match_evidence, ensure_ascii=False, sort_keys=True),
                    "evidence_file": scenario["evidence_file"],
                }
            )
    return details


def summarize(details: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in details:
        groups[str(row[key])].append(row)
    rows: list[dict[str, Any]] = []
    for group, items in sorted(groups.items()):
        safe = sum(bool(row["safe_abstain"]) for row in items)
        unsafe = sum(bool(row["unsafe_selection"]) for row in items)
        rows.append(
            {
                key: group,
                "attempts": len(items),
                "safe_abstain": safe,
                "safe_abstain_rate": safe / len(items) if items else 0,
                "unsafe_selection": unsafe,
                "unsafe_selection_rate": unsafe / len(items) if items else 0,
                "unique_events": len({row["event_id"] for row in items}),
                "unique_scenarios": len({row["scenario_id"] for row in items}),
            }
        )
    return rows


def summarize_all(details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    safe = sum(bool(row["safe_abstain"]) for row in details)
    unsafe = sum(bool(row["unsafe_selection"]) for row in details)
    return [
        {
            "expected": "ABSTAIN",
            "attempts": len(details),
            "safe_abstain": safe,
            "safe_abstain_rate": safe / len(details) if details else 0,
            "unsafe_selection": unsafe,
            "unsafe_selection_rate": unsafe / len(details) if details else 0,
            "unique_events": len({row["event_id"] for row in details}),
            "unique_scenarios": len({row["scenario_id"] for row in details}),
        }
    ]


def write_docs(scenarios: list[dict[str, Any]], prefix: str, runs: int, seed: int) -> None:
    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    type_counts = defaultdict(int)
    for row in scenarios:
        type_counts[row["scenario_type"]] += 1
    README.write_text(
        f"""# Natural Conflict Real V1

This benchmark evaluates fail-closed behavior under mixed public-source evidence.

Generated at: {datetime.now(timezone.utc).isoformat()} UTC

Boundary:

- Evidence is built from `benchmark/external-real-v1/documents/excerpts`.
- Candidate descriptions, candidate values, and private Oracle rows are not included in scenario evidence.
- No synthetic conflict marker such as `Conflicting note` is inserted.
- The expected behavior for every scenario is `ABSTAIN`.

Scenario counts:

{chr(10).join(f"- {key}: {value}" for key, value in sorted(type_counts.items()))}

Evaluation:

- Prefix: `{prefix}`
- Runs per scenario: `{runs}`
- Seed base: `{seed}`

Primary outputs:

- `output/{prefix}-summary.csv`
- `output/{prefix}-by-scenario-type.csv`
- `output/{prefix}-by-domain.csv`
- `output/{prefix}-details.csv`
""",
        encoding="utf-8",
    )
    PROTOCOL.write_text(
        """# Natural Conflict Real V1 Protocol

1. Use only public-source excerpts from `external-real-v1`.
2. Remove candidate values and private Oracle content before composing evidence bundles.
3. Mix target evidence with an additional real public excerpt from the same domain.
4. Do not use explicit synthetic conflict labels or artificial string substitutions.
5. Evaluate whether the gate fails closed before candidate selection.
6. Load Oracle only after the gate/selection decision is fixed, and only for normal consistency auditing.
""",
        encoding="utf-8",
    )


def write_markdown(prefix: str, summary: list[dict[str, Any]], by_type: list[dict[str, Any]], by_domain: list[dict[str, Any]]) -> None:
    def table(rows: list[dict[str, Any]]) -> str:
        if not rows:
            return ""
        fields = list(rows[0].keys())
        lines = ["| " + " | ".join(fields) + " |", "| " + " | ".join("---" for _ in fields) + " |"]
        for row in rows:
            values = []
            for field in fields:
                value = row[field]
                if isinstance(value, float):
                    value = f"{value * 100:.2f}%" if field.endswith("_rate") else f"{value:.4f}"
                values.append(str(value).replace("|", "/"))
            lines.append("| " + " | ".join(values) + " |")
        return "\n".join(lines)

    text = f"""# Natural Conflict Real V1 Gate Evaluation

Generated at {datetime.now(timezone.utc).isoformat()} UTC.

This run evaluates whether the enhanced provenance/evidence-block gate fails closed on mixed evidence bundles made only from real public-source excerpts.

## Overall

{table(summary)}

## By Scenario Type

{table(by_type)}

## By Domain

{table(by_domain)}

Interpretation:

- A safe abstain means the method refuses to select a repair candidate when public evidence is not uniquely isolated.
- This benchmark removes explicit synthetic conflict markers and artificial value rewrites.
- It is still a constructed conflict benchmark from existing public excerpts, not an independently mined real incident corpus.
"""
    (OUTPUT_DIR / f"{prefix}.md").write_text(text, encoding="utf-8")


def main() -> int:
    block_legacy_entrypoint(__file__)
    args = parse_args()
    selected_ids = event_ids(args.only)
    events = {
        row["event_id"]: row
        for row in read_csv(v3.EVENT_CSV)
        if row.get("status", "").strip().upper() == "READY"
    }
    scenarios = build_scenarios(events, selected_ids)
    write_csv(SCENARIOS_CSV, scenarios)
    write_docs(scenarios, args.prefix, args.runs, args.seed)
    details = evaluate_scenarios(scenarios, events, args.runs, args.seed)
    summary = summarize_all(details)
    by_type = summarize(details, "scenario_type")
    by_domain = summarize(details, "domain")
    write_csv(OUTPUT_DIR / f"{args.prefix}-details.csv", details)
    write_csv(OUTPUT_DIR / f"{args.prefix}-summary.csv", summary)
    write_csv(OUTPUT_DIR / f"{args.prefix}-by-scenario-type.csv", by_type)
    write_csv(OUTPUT_DIR / f"{args.prefix}-by-domain.csv", by_domain)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "benchmark_dir": str(BENCHMARK_DIR.relative_to(ROOT)),
        "scenario_count": len(scenarios),
        "attempts": len(details),
        "summary": summary,
        "by_scenario_type": by_type,
        "by_domain": by_domain,
    }
    (OUTPUT_DIR / f"{args.prefix}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(args.prefix, summary, by_type, by_domain)

    overall = summary[0] if summary else {}
    print("NATURAL_CONFLICT_REAL_V1 gate evaluation")
    print(f"scenarios={len(scenarios)} attempts={len(details)}")
    print(f"summary={OUTPUT_DIR / f'{args.prefix}-summary.csv'}")
    print(f"details={OUTPUT_DIR / f'{args.prefix}-details.csv'}")
    print(f"md={OUTPUT_DIR / f'{args.prefix}.md'}")
    print(
        f"[ALL] safe_abstain={float(overall.get('safe_abstain_rate', 0)):.2%} "
        f"unsafe_selection={float(overall.get('unsafe_selection_rate', 0)):.2%}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
