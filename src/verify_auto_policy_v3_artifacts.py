from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"


REQUIRED_FILES = [
    "external-real-v1-freeze-manifest-30-private-oracle-fixed.json",
    "auto-policy-v3-candidate-repair-r5-seed20260820-summary.csv",
    "auto-policy-v3-natural-evidence-raw_provenance_context-candidate-repair-r1-seed20260827-summary.csv",
    "auto-policy-v3-natural-evidence-raw_provenance_metadata_light-candidate-repair-r1-seed20260827-summary.csv",
    "auto-policy-v3-natural-conflict-gate-r1-seed20260827-by-dataset.csv",
    "natural-conflict-real-v1-gate-r5-seed20260820-summary.csv",
    "natural-conflict-real-v1-gate-r5-seed20260820-by-domain.csv",
    "auto-policy-v3-schema-transfer-r5-seed20260820-semantic-only-summary.csv",
    "auto-policy-v3-schema-transfer-r5-seed20260820-semantic-only-by-domain.csv",
    "auto-policy-v3-extended-statistical-analysis-paired-tests.csv",
    "final-method-comparison-table.md",
    "auto-policy-v3-schema-adapter-inventory.md",
    "auto-policy-v3-schema-adapter-onboarding-by-domain.csv",
    "auto-policy-v3-schema-adapter-onboarding-evidence.md",
    "auto-policy-v3-external-validity-register.md",
    "external-real-v2-expansion-plan.md",
    "external-real-v2-freeze-manifest-68.json",
    "external-real-v2-validation-68-summary.json",
    "external-real-v2-validation-68-report.md",
    "external-real-v3-freeze-manifest-213.json",
    "external-real-v3-validation-213-summary.json",
    "external-real-v3-validation-213-report.md",
    "external-real-v3-dataset-report.md",
    "external-real-v3-naturalized-freeze-manifest-213.json",
    "external-real-v3-naturalized-validation-213-summary.json",
    "external-real-v3-naturalized-validation-213-report.md",
    "external-real-v3-naturalized-quality-audit.md",
    "reproducibility-package.md",
    "semantic-v2-methodology-corrected-report.md",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def float_value(row: dict[str, str], *keys: str) -> float:
    for key in keys:
        if key in row and row[key] not in ("", None):
            return float(row[key])
    raise KeyError(keys)


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def check_close(name: str, actual: float, expected: float, errors: list[str], tolerance: float = 1e-9) -> None:
    if abs(actual - expected) > tolerance:
        errors.append(f"{name}: expected {expected}, got {actual}")


def first_row(path: str) -> dict[str, str]:
    rows = read_csv(OUT / path)
    if not rows:
        raise ValueError(f"empty CSV: {path}")
    return rows[0]


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    for rel in REQUIRED_FILES:
        path = OUT / rel
        if not path.exists():
            errors.append(f"missing required output: {path}")

    if errors:
        for error in errors:
            print(f"ERROR {error}")
        return 1

    freeze = read_json(OUT / "external-real-v1-freeze-manifest-30-private-oracle-fixed.json")
    if freeze.get("events_ready") != 30:
        errors.append(f"freeze events_ready expected 30, got {freeze.get('events_ready')}")
    if not freeze.get("git_dirty", True):
        warnings.append("freeze manifest recorded a clean git state; current report text may need adjustment")

    v2_summary = read_json(OUT / "external-real-v2-validation-68-summary.json")
    if v2_summary.get("ready_events") != 68:
        errors.append(f"external-real-v2 ready_events expected 68, got {v2_summary.get('ready_events')}")
    if v2_summary.get("domains") != 5:
        errors.append(f"external-real-v2 domains expected 5, got {v2_summary.get('domains')}")
    if v2_summary.get("errors") != 0 or v2_summary.get("warnings") != 0:
        errors.append(
            f"external-real-v2 validation expected 0/0 errors/warnings, got "
            f"{v2_summary.get('errors')}/{v2_summary.get('warnings')}"
        )

    external_v3_summary = read_json(OUT / "external-real-v3-validation-213-summary.json")
    if external_v3_summary.get("ready_events") != 213:
        errors.append(
            f"external-real-v3 ready_events expected 213, got {external_v3_summary.get('ready_events')}"
        )
    if external_v3_summary.get("domains") != 10:
        errors.append(f"external-real-v3 domains expected 10, got {external_v3_summary.get('domains')}")
    if external_v3_summary.get("errors") != 0 or external_v3_summary.get("warnings") != 0:
        errors.append(
            f"external-real-v3 validation expected 0/0 errors/warnings, got "
            f"{external_v3_summary.get('errors')}/{external_v3_summary.get('warnings')}"
        )
    v3_type_counts = external_v3_summary.get("type_counts", {})
    if set(v3_type_counts.values()) != {71}:
        errors.append(f"external-real-v3 semantic type counts expected all 71, got {v3_type_counts}")

    naturalized_summary = read_json(OUT / "external-real-v3-naturalized-validation-213-summary.json")
    if naturalized_summary.get("ready_events") != 213:
        errors.append(
            f"external-real-v3-naturalized ready_events expected 213, got "
            f"{naturalized_summary.get('ready_events')}"
        )
    if naturalized_summary.get("domains") != 10:
        errors.append(
            f"external-real-v3-naturalized domains expected 10, got {naturalized_summary.get('domains')}"
        )
    if naturalized_summary.get("errors") != 0 or naturalized_summary.get("warnings") != 0:
        errors.append(
            f"external-real-v3-naturalized validation expected 0/0 errors/warnings, got "
            f"{naturalized_summary.get('errors')}/{naturalized_summary.get('warnings')}"
        )
    naturalized_sources = naturalized_summary.get("source_families_by_domain", {})
    if any(count < 3 for count in naturalized_sources.values()):
        errors.append(f"external-real-v3-naturalized source family target failed: {naturalized_sources}")
    if naturalized_summary.get("structured_excerpt_rate", 1.0) > 0.25:
        errors.append(
            f"external-real-v3-naturalized structured excerpt rate too high: "
            f"{naturalized_summary.get('structured_excerpt_rate')}"
        )

    v3_summary = first_row("auto-policy-v3-candidate-repair-r5-seed20260820-summary.csv")
    check_close("V3 oracle", float_value(v3_summary, "oracle_accuracy", "oracle"), 1.0, errors)
    check_close("V3 closure", float_value(v3_summary, "full_closure_accuracy", "closure"), 1.0, errors)

    raw_prov = first_row("auto-policy-v3-natural-evidence-raw_provenance_context-candidate-repair-r1-seed20260827-summary.csv")
    check_close("RAW_PROVENANCE_CONTEXT closure", float_value(raw_prov, "full_closure_accuracy", "closure"), 1.0, errors)

    metadata_light = first_row("auto-policy-v3-natural-evidence-raw_provenance_metadata_light-candidate-repair-r1-seed20260827-summary.csv")
    check_close(
        "RAW_PROVENANCE_METADATA_LIGHT closure",
        float_value(metadata_light, "full_closure_accuracy", "closure"),
        0.8,
        errors,
    )

    gate_rows = read_csv(OUT / "auto-policy-v3-natural-conflict-gate-r1-seed20260827-by-dataset.csv")
    gate_by_dataset = {row["dataset"]: row for row in gate_rows}
    check_close(
        "natural conflict safe abstain",
        float_value(gate_by_dataset["NATURAL_CONFLICT"], "safe_abstain_rate"),
        1.0,
        errors,
    )
    check_close(
        "normal false abstain",
        float_value(gate_by_dataset["NORMAL"], "normal_false_abstain_rate", "false_abstain_rate"),
        0.0,
        errors,
    )

    real_conflict = first_row("natural-conflict-real-v1-gate-r5-seed20260820-summary.csv")
    check_close(
        "real-source natural conflict safe abstain",
        float_value(real_conflict, "safe_abstain_rate"),
        1.0,
        errors,
    )

    schema_domain_rows = read_csv(OUT / "auto-policy-v3-schema-transfer-r5-seed20260820-semantic-only-by-domain.csv")
    for row in schema_domain_rows:
        variant = row["variant"]
        domain = row["domain"]
        if variant == f"LEAVE_OUT_{domain.upper()}":
            check_close(f"{variant} {domain} canonical", float(row["canonical_ok_rate"]), 0.0, errors)

    onboarding_rows = {row["domain"]: row for row in read_csv(OUT / "auto-policy-v3-schema-adapter-onboarding-by-domain.csv")}
    check_close(
        "web accessibility adapter reuse ratio",
        float(onboarding_rows["web_accessibility"]["event_per_family_ratio"]),
        7.0,
        errors,
    )

    paired_rows = read_csv(OUT / "auto-policy-v3-extended-statistical-analysis-paired-tests.csv")
    paired_names = {row.get("comparison", "") for row in paired_rows}
    required_comparisons = {
        "RAW_PROVENANCE_CONTEXT vs RAW_SHORT_CONTEXT",
        "RAW_PROVENANCE_CONTEXT vs RAW_PROVENANCE_METADATA_LIGHT",
        "ENHANCED_NATURAL_GATE vs ORIGINAL_NATURAL_GATE",
    }
    missing = sorted(required_comparisons - paired_names)
    if missing:
        errors.append(f"missing paired comparisons: {missing}")

    if errors:
        for error in errors:
            print(f"ERROR {error}")
        return 1

    print("AUTO_POLICY_V3 artifact verification: PASS")
    print(f"freeze_events_ready={freeze.get('events_ready')}")
    print(f"external_real_v2_ready_events={v2_summary.get('ready_events')}")
    print(f"external_real_v2_domains={v2_summary.get('domains')}")
    print(f"external_real_v3_ready_events={external_v3_summary.get('ready_events')}")
    print(f"external_real_v3_domains={external_v3_summary.get('domains')}")
    print(f"external_real_v3_naturalized_ready_events={naturalized_summary.get('ready_events')}")
    print(f"external_real_v3_naturalized_source_families={naturalized_summary.get('source_families')}")
    print(f"external_real_v3_naturalized_structured_excerpt_rate={naturalized_summary.get('structured_excerpt_rate'):.2%}")
    print(f"v3_oracle={pct(float_value(v3_summary, 'oracle_accuracy', 'oracle'))}")
    print(f"v3_closure={pct(float_value(v3_summary, 'full_closure_accuracy', 'closure'))}")
    print(f"raw_provenance_closure={pct(float_value(raw_prov, 'full_closure_accuracy', 'closure'))}")
    print(f"metadata_light_closure={pct(float_value(metadata_light, 'full_closure_accuracy', 'closure'))}")
    print(f"natural_conflict_safe_abstain={pct(float_value(gate_by_dataset['NATURAL_CONFLICT'], 'safe_abstain_rate'))}")
    print(f"real_source_conflict_safe_abstain={pct(float_value(real_conflict, 'safe_abstain_rate'))}")
    print("held_out_domain_canonical_rate=0.00% for all three domains")
    print("web_accessibility_schema_event_per_family=7.00")
    for warning in warnings:
        print(f"WARNING {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
