from __future__ import annotations

"""Create paper-final validation freeze (STEP 0)."""

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paper_final_validation_common import (
    DEFAULT_BENCHMARK,
    DEFAULT_BENCHMARK_FREEZE_SUMMARY,
    DEFAULT_METHOD_FREEZE_DIR,
    PAPER_VALIDATION_ROOT,
    git_commit,
    load_json,
    sha256_file,
    utc_now_iso,
)
from semantic_v2_common import PROJECT_DIR, write_csv


FREEZE_DIR = PAPER_VALIDATION_ROOT / "validation-freeze"
PARENT_BENCHMARK = (
    PROJECT_DIR
    / "benchmark"
    / "external-real-holdout-v5-blind-large-robustness-variants"
    / "candidate-id-permute"
)


def hash_tree(root: Path, *, relative_to: Path | None = None) -> list[dict[str, str]]:
    base = relative_to or root
    rows: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(base).as_posix()
        rows.append({"path": rel, "sha256": sha256_file(path), "bytes": str(path.stat().st_size)})
    return rows


def write_sha256_manifest(rows: list[dict[str, str]], path: Path) -> None:
    lines = [f"{row['sha256']}  {row['path']}" for row in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def protocol_markdown(freeze: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Paper-Final Validation Protocol",
            "",
            f"Frozen at UTC: `{freeze['frozen_at_utc']}`",
            "",
            "## 1. Parent benchmark",
            "",
            f"- Primary evaluation parent: `{freeze['parent_benchmark']['path']}`",
            f"- Parent benchmark SHA256: `{freeze['parent_benchmark']['sha256']}`",
            f"- Original v5 blind-large SHA256: `{freeze['original_benchmark_sha256']}`",
            "",
            "## 2. Method version",
            "",
            f"- Method freeze: `{freeze['method_freeze']['path']}`",
            f"- Method manifest SHA256: `{freeze['method_freeze']['manifest_sha256']}`",
            f"- Git commit at freeze: `{freeze['git_commit']}`",
            "",
            "## 3. Frozen prompts / function hashes",
            "",
            "Prompt bodies and ranking logic are frozen via `m16-full-repair-method-freeze-manifest.json` function_hashes.",
            "No prompt edits after this freeze.",
            "",
            "## 4. Frozen thresholds",
            "",
            "- v4_min_score: 0.30",
            "- v4_gre_css_min_score: 0.24",
            "- v4_min_margin: 0.00",
            "- v4_reranker: constraint",
            "- v4_temporal_unique_top1: true",
            "",
            "## 5. Random seeds",
            "",
            "- Formal runs per event: 5",
            "- Formal run seeds: 20260829, 20260830, 20260831, 20260901, 20260902",
            "- Exp8 shuffle map seed: 20260903",
            "- Exp11 strict provenance ID permute seed: 20260906",
            "- Exp12 human audit sample seed: 20260907",
            "- Exp14 source-cluster bootstrap seed: 20260909",
            "",
            "## 6. Number of runs",
            "",
            "- Exp8–Exp10 formal: 5 runs/event on 264 events (1320 attempts/condition)",
            "- Exp8 pilot QA: 1 run/event (264 attempts/condition; not for paper stats)",
            "- Exp11 formal: 5 runs/event on strict-provenance subset (target 60 events)",
            "",
            "## 7. Backend",
            "",
            "- Primary backend: `deepseek_api`",
            "- Model: `deepseek-v4-flash` (via `DEEPSEEK_MODEL`; deviates from draft doc `deepseek-chat` label)",
            "- Temperature: 0",
            "",
            "## 8. Statistical methods",
            "",
            "- Event-clustered paired bootstrap: 10,000 replicates unless noted",
            "- Independent unit: repair event (all 5 runs kept per resampled event)",
            "- Source-cluster bootstrap (Exp14): RFC/source_family clusters",
            "- Zero wrong-selection bound (Exp14-D): Clopper–Pearson on event-level counts",
            "",
            "## 9. Exclusion policy",
            "",
            "- Only READY events in parent benchmark",
            "- Pilot runs excluded from paper statistics",
            "- API/infrastructure failures may be retried once with `--resume`; failed attempts excluded from success metrics",
            "",
            "## 10. Timeout / error policy",
            "",
            "- Default LLM timeout: 180s (M13–M16)",
            "- Record `api_error` and `timeout` in unified attempt CSV",
            "- IR/decision abstains are valid outcomes, not API errors",
            "",
            "## 11. No-rerun policy",
            "",
            "- After formal ×5 completes for a condition, do not rerun to improve numbers",
            "- Resume only for missing/failed attempts before condition is marked complete",
            "",
            "## 12. No post-result tuning",
            "",
            "Forbidden after seeing v5 validation results:",
            "",
            "- M13/M14/M15/M16 prompt or logic changes",
            "- Ranking weights/threshold changes",
            "- Candidate construction changes",
            "- Closure rule changes",
            "- Benchmark oracle edits (except separate sensitivity sets explicitly labeled)",
            "",
            "## Experiment map (Exp8–Exp15)",
            "",
            "| Exp | Question | Primary output |",
            "|-----|----------|----------------|",
            "| 8 | Evidence dependence | 5 evidence conditions × frozen ECR |",
            "| 9 | Strong / compute-matched baselines | B1/B2/B3 vs ECR |",
            "| 10 | Candidate-only shortcuts | ID-only + candidate-only LLM + classifier |",
            "| 11 | Strict historical provenance | 60-event strict-drift subset |",
            "| 12 | Independent human annotation | 90-event dual annotation |",
            "| 13 | Semantic IR fidelity | GRE/CSS gold vs M13/M14/M15 |",
            "| 14 | Statistical robustness | Source-cluster bootstrap + LOO + CIs |",
            "| 15 | Real audit trace | 3 success + 2 failure end-to-end traces |",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=FREEZE_DIR)
    args = parser.parse_args()

    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)

    method_manifest_path = DEFAULT_METHOD_FREEZE_DIR / "m16-full-repair-method-freeze-manifest.json"
    method_manifest = load_json(method_manifest_path)
    bench_summary = load_json(DEFAULT_BENCHMARK_FREEZE_SUMMARY)

    parent_sha = sha256_file(PARENT_BENCHMARK / "public" / "events" / "external-real-event-template.csv")
    freeze: dict[str, Any] = {
        "schema_version": "1.0",
        "freeze_kind": "PAPER_FINAL_VALIDATION_PROTOCOL",
        "frozen_at_utc": utc_now_iso(),
        "git_commit": git_commit(),
        "parent_benchmark": {
            "path": str(PARENT_BENCHMARK.relative_to(PROJECT_DIR)),
            "sha256": parent_sha,
            "oracle_distribution": {"CAND_001": 71, "CAND_002": 88, "CAND_003": 105},
        },
        "original_benchmark": str(DEFAULT_BENCHMARK.relative_to(PROJECT_DIR)),
        "original_benchmark_sha256": bench_summary.get("manifest_sha256", ""),
        "method_freeze": {
            "path": str(method_manifest_path.relative_to(PROJECT_DIR)),
            "manifest_sha256": method_manifest.get("manifest_sha256", ""),
        },
        "backend": {
            "name": "deepseek_api",
            "model_env": os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            "temperature": 0,
        },
        "formal_runs_per_event": 5,
        "formal_seeds": [20260829, 20260830, 20260831, 20260901, 20260902],
        "experiment_seeds": {
            "exp8_shuffle": 20260903,
            "exp11_id_permute": 20260906,
            "exp12_human_sample": 20260907,
            "exp14_source_bootstrap": 20260909,
        },
        "pilot_policy": {
            "runs_per_event": 1,
            "use_for_paper_stats": False,
        },
        "unified_csv_schema": "src/paper_final_validation_schema.py:UNIFIED_ATTEMPT_FIELDS",
    }

    freeze_json = out / "validation-freeze.json"
    freeze_json.write_text(json.dumps(freeze, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "validation-protocol.md").write_text(protocol_markdown(freeze) + "\n", encoding="utf-8")

    manifest_rows = hash_tree(out, relative_to=out)
    manifest_rows.extend(
        hash_tree(PARENT_BENCHMARK / "public" / "events", relative_to=PARENT_BENCHMARK)
    )
    sha_path = out / "validation-freeze.sha256"
    write_sha256_manifest(manifest_rows, sha_path)
    write_csv(out / "validation-freeze-files.csv", manifest_rows)

    print(json.dumps({"wrote": str(out), "files": len(manifest_rows)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
