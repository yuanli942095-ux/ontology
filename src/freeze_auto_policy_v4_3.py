from __future__ import annotations

"""Freeze AUTO_POLICY V4.3 Repair Decision Module.

Hashes the locked scorer, gate, Semantic IR schema, frozen LIGHT prompt
sources, V4.3 result tables, and the 600 LIGHT raw attempts V4.3 rereads.
After this freeze, v8 is a development/diagnostic set. Do not lower
min_score, loosen the gate, add GRE/CSS scorers, or run the paper 5-run
against this freeze.
"""

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semantic_v2_common import OUTPUT_DIR, PROJECT_DIR, sha256_file, sha256_text, write_csv


PREFIX = "auto-policy-v4.3-repair-decision-freeze-manifest"

LOCKED_SOURCES = [
    ("src/run_auto_policy_v4_ir_candidate_repair.py", "repair_decision_runner"),
    ("src/auto_policy_v4_constraint_rerank.py", "temporal_constraint_scorer"),
    ("src/run_auto_policy_v2_candidate_repair.py", "owl_closure_engine"),
    ("tests/test_v4_constraint_rerank.py", "repair_decision_tests"),
    ("src/run_auto_formal_policy_batch_v3.py", "frozen_auto_policy_schema_and_prompt"),
    ("src/run_auto_policy_v3_robustness.py", "frozen_light_prompt_wrapper"),
    ("src/run_auto_policy_v3_natural_evidence_robustness.py", "frozen_light_generation_runner"),
    ("src/freeze_auto_policy_v4_3.py", "freeze_script"),
]

LOCKED_RESULTS = [
    (
        "output/auto-policy-v4.3-ir/auto-policy-v4.3-ir-raw_window_metadata_light-r3-seed20260827.json",
        "v4_3_summary_json",
    ),
    (
        "output/auto-policy-v4.3-ir/auto-policy-v4.3-ir-raw_window_metadata_light-r3-seed20260827-summary.csv",
        "v4_3_summary_csv",
    ),
    (
        "output/auto-policy-v4.3-ir/auto-policy-v4.3-ir-raw_window_metadata_light-r3-seed20260827-by-type.csv",
        "v4_3_by_type",
    ),
    (
        "output/auto-policy-v4.3-ir/auto-policy-v4.3-ir-raw_window_metadata_light-r3-seed20260827-by-event.csv",
        "v4_3_by_event",
    ),
    (
        "output/auto-policy-v4.3-ir/auto-policy-v4.3-ir-raw_window_metadata_light-r3-seed20260827-details.csv",
        "v4_3_details",
    ),
]

LIGHT_RAW_DIR = (
    PROJECT_DIR
    / "output"
    / "external-real-v8-grounded"
    / "preformal-r3"
    / "raw_window_metadata_light"
    / "raw"
)

GATE = {
    "min_score": 0.30,
    "min_margin": 0.00,
    "reranker": "constraint",
    "temporal_unique_top1": True,
    "gre_css_scorer": "v4.2_lexical_unchanged",
    "ties": "ABSTAIN",
}

IR_SCHEMA = {
    "TEMPORAL_VERSION_required": ["subject", "relation", "result"],
    "GENERAL_RULE_EXCEPTION_required": ["subject", "result"],
    "CROSS_SENTENCE_SCOPE_required": ["subject", "statement", "scope_relation", "result"],
    "prompt_version": "AUTO_POLICY_V3_NATURAL_EVIDENCE_RAW_WINDOW_METADATA_LIGHT",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="freeze AUTO_POLICY V4.3 repair decision")
    parser.add_argument("--prefix", default=PREFIX)
    return parser.parse_args()


def relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_DIR)).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def file_row(path: Path, role: str) -> dict[str, Any]:
    stat = path.stat()
    return {
        "role": role,
        "path": relpath(path),
        "sha256": sha256_file(path),
        "bytes": stat.st_size,
    }


def git_parent() -> dict[str, Any]:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_DIR,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return {"available": True, "parent_commit": commit}
    except Exception as exc:
        return {"available": False, "parent_commit": "", "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    args = parse_args()
    hashed: list[dict[str, Any]] = []
    for relative, role in LOCKED_SOURCES + LOCKED_RESULTS:
        path = PROJECT_DIR / relative
        if not path.is_file():
            raise FileNotFoundError(path)
        hashed.append(file_row(path, role))
    raw_files = sorted(path for path in LIGHT_RAW_DIR.glob("*.json") if path.is_file())
    if len(raw_files) != 600:
        raise RuntimeError(f"expected 600 LIGHT raw files, found {len(raw_files)}")
    for path in raw_files:
        hashed.append(file_row(path, "frozen_light_raw_attempt"))
    raw_concat = sha256_text("".join(row["sha256"] for row in hashed if row["role"] == "frozen_light_raw_attempt"))
    payload = {
        "schema_version": "1.0",
        "freeze_label": "AUTO_POLICY_V4_3_REPAIR_DECISION",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "v8_role_after_freeze": "development_diagnostic_set",
        "paper_5run": False,
        "qwen_called_by_v4_3": False,
        "gate": GATE,
        "semantic_ir_schema": IR_SCHEMA,
        "metrics": {
            "attempts": 600,
            "oracle_accuracy": 0.4667,
            "full_closure_accuracy": 0.4667,
            "precision_given_select": 0.9589,
            "selected": 292,
            "abstains": 308,
            "strict_events": 76,
        },
        "do_not": [
            "lower_min_score",
            "loosen_gate",
            "add_gre_css_scorer",
            "run_paper_5run_on_v8",
        ],
        "git": git_parent(),
        "light_raw_count": len(raw_files),
        "light_raw_sha256_concat": raw_concat,
        "files": hashed,
    }
    payload["manifest_sha256"] = sha256_text(
        json.dumps(
            [{"path": row["path"], "sha256": row["sha256"], "role": row["role"]} for row in hashed],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    out_json = OUTPUT_DIR / f"{args.prefix}.json"
    out_csv = OUTPUT_DIR / f"{args.prefix}-files.csv"
    out_log = OUTPUT_DIR / f"{args.prefix}.log"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(out_csv, hashed)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "AUTO_POLICY V4.3 repair-decision freeze",
        f"manifest_sha256={payload['manifest_sha256']}",
        f"files={len(hashed)}",
        f"light_raw={len(raw_files)}",
        f"parent_git={payload['git'].get('parent_commit', '')}",
        f"json={relpath(out_json)}",
    ]
    out_log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
