from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_CSV = ROOT / "output" / "auto-policy-v3-external-validity-register.csv"
OUT_MD = ROOT / "output" / "auto-policy-v3-external-validity-register.md"
OUT_JSON = ROOT / "output" / "auto-policy-v3-external-validity-register.json"


ROWS = [
    {
        "risk_id": "EV-001",
        "risk": "Benchmark size remains small for broad generalization.",
        "current_evidence": "external-real-v2 has 68 READY events, 5 domains, 136 document rows, 204 candidates, 68 private Oracle rows, and validation errors=0/warnings=0.",
        "mitigation_completed": "Data-scale target is completed as an expanded 60+ event diagnostic benchmark with freeze manifest and validation report.",
        "residual_limitation": "The full model experiment table has not yet been rerun on external-real-v2; current performance claims still rely on external-real-v1 unless explicitly rerun.",
        "paper_safe_wording": "external-real-v2 completes the 60+ event data-scale extension; report v1 results separately until v2 experiments are rerun.",
        "do_not_claim": "Do not claim general performance on arbitrary normative documents.",
        "status": "data_scale_completed_experiment_rerun_pending",
    },
    {
        "risk_id": "EV-002",
        "risk": "Structured evidence notes may overstate document-understanding performance.",
        "current_evidence": "Metadata/evidence-only normalizer reaches 100.00%; RAW_SHORT_CONTEXT drops to 50.00%; RAW_PROVENANCE_CONTEXT recovers to 100.00%.",
        "mitigation_completed": "Natural-evidence robustness now separates templated notes, naive raw paragraphs, and target-aware provenance retrieval.",
        "residual_limitation": "Provenance retrieval is validated only on this benchmark's collected source documents.",
        "paper_safe_wording": "V3 requires evidence retrieval and canonicalization; naive raw paragraph retrieval is insufficient.",
        "do_not_claim": "Do not claim Qwen alone performs robust natural-document understanding from arbitrary paragraphs.",
        "status": "experimentally_quantified",
    },
    {
        "risk_id": "EV-003",
        "risk": "Canonical schema may be mistaken for automatic rule learning.",
        "current_evidence": "Schema adapter inventory shows no event-id branching; leave-one-domain-out gives 0.00% held-out-domain canonical matches; onboarding evidence shows WCAG strong family reuse but NIST/insurance weak reuse in the current subset.",
        "mitigation_completed": "Added schema adapter inventory, leave-one-domain-out schema transfer, and schema adapter onboarding/reuse evidence.",
        "residual_limitation": "A new domain requires a canonical vocabulary/normalizer adapter; current family-reuse evidence is strongest for WCAG and weak for NIST/insurance.",
        "paper_safe_wording": "The framework is domain-general, while canonical vocabularies are domain-specific adapters with measurable family-level reuse where repeated patterns exist.",
        "do_not_claim": "Do not claim domain-free symbolic rule induction.",
        "status": "scope_clarified",
    },
    {
        "risk_id": "EV-004",
        "risk": "Safety gate may not generalize to natural conflicts.",
        "current_evidence": "Original natural conflict gate safe abstain is 33.33%; enhanced mixed-evidence gate reaches 100.00% safe abstain on 60 constructed natural-conflict attempts and 300 real-public-excerpt mixture attempts.",
        "mitigation_completed": "Added natural conflict safety benchmark, enhanced provenance/evidence-block consistency gate, and natural-conflict-real-v1 using real public excerpts without synthetic conflict markers.",
        "residual_limitation": "The real-source conflict benchmark is still constructed from existing benchmark excerpts, not independently mined real incident reports.",
        "paper_safe_wording": "The enhanced gate fails closed on constructed and real-public-excerpt mixed-evidence conflicts without harming the normal set.",
        "do_not_claim": "Do not claim open-domain contradiction detection or universal safety.",
        "status": "substantially_mitigated_with_residual_external_validity_limit",
    },
    {
        "risk_id": "EV-005",
        "risk": "Repair closure may be confused with full automatic repair generation.",
        "current_evidence": "Closure experiments validate selected finite candidates with OWL materialization, Reasoner consistency, and CQ regression.",
        "mitigation_completed": "Reports distinguish candidate selection/closure from candidate generation.",
        "residual_limitation": "Generating the complete candidate set from raw documents is not yet evaluated as an end-to-end task.",
        "paper_safe_wording": "The method selects and validates evidence-constrained repairs from a generated finite candidate set.",
        "do_not_claim": "Do not claim fully automatic candidate generation from raw documents is solved.",
        "status": "scope_clarified",
    },
    {
        "risk_id": "EV-006",
        "risk": "Human annotation cost is not directly measured at scale.",
        "current_evidence": "A 12-event pilot completion exists; policy complexity points are marked as a proxy, not measured minutes.",
        "mitigation_completed": "Reports label policy-complexity cost as a proxy and separate it from runtime/token cost.",
        "residual_limitation": "More annotators and repeated timing are needed to estimate labor variance.",
        "paper_safe_wording": "Human effort is reported as a pilot measurement plus a transparent complexity proxy.",
        "do_not_claim": "Do not claim the proxy formula is validated annotation time.",
        "status": "quantified_residual_risk",
    },
    {
        "risk_id": "EV-007",
        "risk": "Benchmark freeze proof is limited without independent timestamping.",
        "current_evidence": "Freeze manifest records current file hashes, but earlier pre-freeze development cannot be proven by this manifest alone.",
        "mitigation_completed": "Reports explicitly say the manifest fixes future reruns rather than proving past independence.",
        "residual_limitation": "Formal submission should include a Git commit, archive hash, or Zenodo DOI timestamp.",
        "paper_safe_wording": "All reported final reruns use the frozen manifest state.",
        "do_not_claim": "Do not claim the manifest proves the benchmark was frozen before all exploratory experiments.",
        "status": "procedural_residual_risk",
    },
]


def write_csv() -> None:
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(ROWS[0].keys()))
        writer.writeheader()
        writer.writerows(ROWS)


def md_table() -> str:
    columns = ["risk_id", "risk", "mitigation_completed", "residual_limitation", "status"]
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    body = []
    for row in ROWS:
        body.append("| " + " | ".join(row[col].replace("|", "/") for col in columns) + " |")
    return "\n".join([header, separator, *body])


def write_md() -> None:
    text = f"""# Auto Policy V3 External Validity Register

Generated at {datetime.now(timezone.utc).isoformat()} UTC.

This register is a paper-facing checklist of what has been experimentally mitigated and what remains a limitation. It is intentionally conservative.

{md_table()}

## Paper Position

- The current evidence is strong enough for a controlled, diagnostic benchmark claim.
- The current evidence is not enough for a broad claim over arbitrary normative documents, arbitrary domains, or fully automatic candidate generation.
- The safest contribution statement is: candidate-blind policy generation plus auditable domain canonicalization, provenance-aware evidence retrieval, fail-closed conflict gating, and executable OWL repair closure over finite candidates.

Outputs:

- `{OUT_CSV.relative_to(ROOT)}`
- `{OUT_JSON.relative_to(ROOT)}`
"""
    OUT_MD.write_text(text, encoding="utf-8")


def write_json() -> None:
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "rows": ROWS,
        "paper_safe_summary": (
            "Use external-real-v1 as a controlled public-source diagnostic benchmark. "
            "Report remaining external-validity limits explicitly."
        ),
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    write_csv()
    write_md()
    write_json()
    print("AUTO_POLICY_V3 external validity register")
    print(f"csv={OUT_CSV}")
    print(f"md={OUT_MD}")
    print(f"json={OUT_JSON}")
    for row in ROWS:
        print(f"[{row['risk_id']}] {row['status']} | {row['risk']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
