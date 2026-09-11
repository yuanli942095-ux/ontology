from __future__ import annotations

from method_experiment_guard import add_legacy_opt_in_arg, block_legacy_entrypoint
"""Leave-one-domain-out schema transfer for Auto Policy V3.

This is an offline experiment: it reuses existing candidate-blind Qwen outputs
and re-applies only the selected domain-level normalizer families.  It tests
whether the deterministic schema layer is domain-level rather than event-level.
"""

import argparse
import csv
import json
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import run_auto_formal_policy_batch_v3 as v3
from run_auto_policy_v3_robustness import save_csv


OUTPUT_DIR = v3.ROOT / "output" / "auto-policy-v3-schema-transfer"
DEFAULT_RAW_DIR = v3.ROOT / "output" / "auto-policy-v3" / "raw"
RUNS = 5
SEED_BASE = 20260820

DOMAIN_NORMALIZERS: dict[str, Callable[[dict[str, Any], dict[str, str], str], tuple[dict[str, Any], str] | None]] = {
    "web_accessibility": v3.normalize_wcag,
    "digital_identity": v3.normalize_nist,
    "insurance": v3.normalize_insurance,
}


def clear_downstream_semantic(parsed: dict[str, Any]) -> None:
    parsed.pop("canonical_result", None)
    parsed.pop("canonical_semantic_result", None)
    parsed["canonical_normalized"] = False
    rules = parsed.get("rules")
    if isinstance(rules, list):
        for rule in rules:
            if isinstance(rule, dict):
                rule.pop("canonical_result", None)
                rule["semantic_result"] = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto Policy V3 leave-one-domain-out schema transfer")
    parser.add_argument("--source-raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--runs", type=int, default=RUNS)
    parser.add_argument("--seed", type=int, default=SEED_BASE)
    parser.add_argument("--prefix", default="auto-policy-v3-schema-transfer-r5-seed20260820")
    parser.add_argument("--skip-evaluation", action="store_true")
    parser.add_argument("--skip-repair", action="store_true")
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def event_ids() -> list[str]:
    return [f"EXT_E{i:03d}" for i in range(1, 31)]


def clean_evidence(event_id: str) -> str:
    evidence_path = v3.EXCERPT_DIR / f"{event_id}-evidence.md"
    return v3.remove_candidate_sections(evidence_path.read_text(encoding="utf-8"))


def normalize_with_domains(
    parsed: Any,
    event: dict[str, str],
    evidence: str,
    allowed_domains: set[str],
) -> tuple[Any, str, dict[str, Any] | None, str]:
    if not isinstance(parsed, dict):
        return parsed, "not_json_object", None, ""
    parsed = json.loads(json.dumps(parsed, ensure_ascii=False))
    if parsed.get("abstain", False):
        return parsed, "abstain", None, ""
    domain = event.get("domain", "").strip()
    if domain not in allowed_domains:
        clear_downstream_semantic(parsed)
        return parsed, "held_out_domain_schema_unavailable", None, ""
    normalizer = DOMAIN_NORMALIZERS.get(domain)
    normalized = normalizer(parsed, event, evidence) if normalizer else None
    if not normalized:
        clear_downstream_semantic(parsed)
        return parsed, "canonical_result_unresolved", None, ""
    canonical, semantic_result = normalized
    parsed["canonical_result"] = canonical
    parsed["canonical_semantic_result"] = semantic_result
    parsed["canonical_normalized"] = True
    parsed.setdefault("semantic_type", event.get("semantic_type", ""))
    rules = parsed.get("rules")
    if not isinstance(rules, list) or not rules:
        rules = [{"priority": 300, "conditions": []}]
        parsed["rules"] = rules
    for rule in rules:
        if isinstance(rule, dict):
            rule["canonical_result"] = canonical
            rule["semantic_result"] = semantic_result
    return parsed, "ok", canonical, semantic_result


def write_variant_raw(
    variant: str,
    allowed_domains: set[str],
    source_raw_dir: Path,
    events: dict[str, dict[str, str]],
    runs: int,
    seed_base: int,
) -> None:
    variant_dir = OUTPUT_DIR / variant.lower()
    raw_dir = variant_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for event_id in event_ids():
        event = events[event_id]
        evidence = clean_evidence(event_id)
        for run in range(1, runs + 1):
            seed = seed_base + run - 1
            source_path = source_raw_dir / f"{event_id}-run{run}-seed{seed}.json"
            record = json.loads(source_path.read_text(encoding="utf-8-sig"))
            parsed = record.get("response")
            parsed, canonical_status, canonical_result, canonical_semantic_result = normalize_with_domains(
                parsed,
                event,
                evidence,
                allowed_domains,
            )
            schema_valid, validation_reason = v3.validate_generated_policy(parsed, event["semantic_type"].strip())
            status = "GENERATED" if canonical_status == "ok" and schema_valid else "INVALID_SCHEMA"
            if isinstance(parsed, dict) and parsed.get("abstain", False):
                status = "ABSTAIN"
            out = dict(record)
            out.update(
                {
                    "variant": variant,
                    "prompt_version": f"AUTO_POLICY_V3_SCHEMA_TRANSFER_{variant}",
                    "schema_transfer_allowed_domains": sorted(allowed_domains),
                    "schema_transfer_held_out_domain": (
                        ""
                        if len(allowed_domains) == len(DOMAIN_NORMALIZERS)
                        else "|".join(sorted(set(DOMAIN_NORMALIZERS) - allowed_domains))
                    ),
                    "status": status,
                    "schema_valid": schema_valid,
                    "validation_reason": validation_reason,
                    "canonical_status": canonical_status,
                    "canonical_result": canonical_result,
                    "canonical_semantic_result": canonical_semantic_result,
                    "response": parsed,
                }
            )
            raw_path = raw_dir / f"{event_id}-run{run}-seed{seed}.json"
            raw_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
            rows.append(
                {
                    "variant": variant,
                    "event_id": event_id,
                    "domain": event["domain"],
                    "semantic_type": event["semantic_type"],
                    "run": run,
                    "seed": seed,
                    "status": status,
                    "canonical_status": canonical_status,
                    "canonical_semantic_result": canonical_semantic_result,
                    "raw_output_file": str(raw_path.relative_to(v3.ROOT)),
                }
            )
    save_csv(variant_dir / f"{variant.lower()}-renormalization-details.csv", rows)


def run_command(args: list[str]) -> None:
    print(" ".join(args))
    subprocess.run(args, cwd=v3.ROOT, check=True)


def evaluate_variant(variant: str, runs: int, seed: int, skip_repair: bool) -> None:
    python = str(v3.ROOT / ".venv" / "Scripts" / "python.exe")
    variant_dir = OUTPUT_DIR / variant.lower()
    raw_dir = variant_dir / "raw"
    semantic_prefix = f"auto-policy-v3-schema-transfer-{variant.lower()}-semantic-evaluation"
    repair_prefix = f"auto-policy-v3-schema-transfer-{variant.lower()}-candidate-repair-r{runs}-seed{seed}"
    run_command(
        [
            python,
            "src\\evaluate_auto_formal_policy_v2_semantic.py",
            "--raw-dir",
            str(raw_dir),
            "--output-dir",
            str(variant_dir),
            "--prefix",
            semantic_prefix,
            "--runs",
            str(runs),
            "--seed",
            str(seed),
        ]
    )
    if not skip_repair:
        run_command(
            [
                python,
                "src\\run_auto_policy_v2_candidate_repair.py",
                "--raw-dir",
                str(raw_dir),
                "--prefix",
                repair_prefix,
                "--runs",
                str(runs),
                "--seed",
                str(seed),
            ]
        )


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def build_summary(prefix: str, variants: list[str], runs: int, seed: int, skip_repair: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for variant in variants:
        semantic_path = OUTPUT_DIR / variant.lower() / f"auto-policy-v3-schema-transfer-{variant.lower()}-semantic-evaluation-summary.json"
        semantic = load_json(semantic_path)["summary"][0]
        row: dict[str, Any] = {
            "variant": variant,
            "events": semantic["events"],
            "attempts": semantic["attempts"],
            "semantic_accuracy": semantic["semantic_accuracy"],
            "semantic_strict_event_successes": semantic["strict_event_successes"],
            "semantic_strict_event_accuracy": semantic["strict_event_accuracy"],
            "generation_successes": semantic["generation_successes"],
        }
        if not skip_repair:
            repair_prefix = f"auto-policy-v3-schema-transfer-{variant.lower()}-candidate-repair-r{runs}-seed{seed}"
            repair = read_csv(v3.ROOT / "output" / f"{repair_prefix}-summary.csv")[0]
            row.update(
                {
                    "oracle_accuracy": repair["oracle_accuracy"],
                    "full_closure_accuracy": repair["full_closure_accuracy"],
                    "repair_strict_event_successes": repair["strict_event_successes"],
                    "repair_strict_event_accuracy": repair["strict_event_accuracy"],
                    "abstains": repair["abstains"],
                }
            )
        rows.append(row)
    save_csv(v3.ROOT / "output" / f"{prefix}-summary.csv", rows)
    by_domain: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for variant in variants:
        for row in read_csv(OUTPUT_DIR / variant.lower() / f"{variant.lower()}-renormalization-details.csv"):
            by_domain[(variant, row["domain"])].append(row)
    domain_rows = []
    for (variant, domain), items in sorted(by_domain.items()):
        ok = sum(row["canonical_status"] == "ok" for row in items)
        domain_rows.append(
            {
                "variant": variant,
                "domain": domain,
                "attempts": len(items),
                "canonical_ok": ok,
                "canonical_ok_rate": ok / len(items) if items else 0,
                "held_out_or_unavailable": sum(row["canonical_status"] == "held_out_domain_schema_unavailable" for row in items),
            }
        )
    save_csv(v3.ROOT / "output" / f"{prefix}-by-domain.csv", domain_rows)
    (v3.ROOT / "output" / f"{prefix}.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "runs": runs,
                "seed_base": seed,
                "summary": rows,
                "by_domain": domain_rows,
                "boundary": "The experiment reuses candidate-blind Qwen outputs and toggles only domain-level normalizer availability.",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return rows


def main() -> int:
    block_legacy_entrypoint(__file__)
    args = parse_args()
    events = v3.load_events()
    variants = ["FULL_SCHEMA"]
    write_variant_raw("FULL_SCHEMA", set(DOMAIN_NORMALIZERS), args.source_raw_dir, events, args.runs, args.seed)
    for domain in sorted(DOMAIN_NORMALIZERS):
        variant = "LEAVE_OUT_" + domain.upper()
        variants.append(variant)
        write_variant_raw(
            variant,
            set(DOMAIN_NORMALIZERS) - {domain},
            args.source_raw_dir,
            events,
            args.runs,
            args.seed,
        )
    if not args.skip_evaluation:
        for variant in variants:
            evaluate_variant(variant, args.runs, args.seed, args.skip_repair)
    rows = build_summary(args.prefix, variants, args.runs, args.seed, args.skip_repair)
    for row in rows:
        print(
            f"{row['variant']}: semantic={float(row['semantic_accuracy']):.2%} "
            f"strict={row['semantic_strict_event_successes']}/{row['events']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
