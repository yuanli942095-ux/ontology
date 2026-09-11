from __future__ import annotations

from method_experiment_guard import add_legacy_opt_in_arg, block_legacy_entrypoint
"""Model capacity ablation: Qwen3.5:9b vs DeepSeek API on frozen M1/M2 decomposed extraction slots."""

import argparse
import json
import os
from pathlib import Path
from typing import Any

from auto_policy_m12_decomposed_extraction import ExtractionBackend, apply_decomposed_extraction
from run_auto_policy_v4_ir_candidate_repair import configure_paths, read_csv, ready
from run_m12_decomposed_extraction_pilot import (
    ARM_A_RAW,
    BENCHMARK_DIR,
    C5_ANALYSIS,
    SEED_BASE,
    load_evidence,
    load_manifest,
    run_ir,
)
from semantic_v2_common import PROJECT_DIR, write_csv

OUTPUT_ROOT = PROJECT_DIR / "output" / "m12-model-capacity-ablation"
QWEN_PILOT = PROJECT_DIR / "output" / "m12-decomposed-extraction-pilot"


def main() -> int:
    block_legacy_entrypoint(__file__)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=C5_ANALYSIS)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--qwen-timeout", type=int, default=180)
    parser.add_argument("--skip-extraction", action="store_true")
    parser.add_argument("--skip-ir", action="store_true")
    args = parser.parse_args()

    if not args.skip_extraction and not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        raise RuntimeError("DEEPSEEK_API_KEY is not set")

    manifest = load_manifest(args.manifest)
    paths = configure_paths(BENCHMARK_DIR)
    events = {row["event_id"]: row for row in read_csv(paths["event_csv"]) if ready(row.get("status", ""))}
    event_ids = {row["event_id"] for row in manifest}

    arm_a_raw = args.output_dir / "arm-a-qwen" / "raw_window_metadata_light" / "raw"
    arm_b_raw = args.output_dir / "arm-b-deepseek" / "raw_window_metadata_light" / "raw"
    arm_a_raw.mkdir(parents=True, exist_ok=True)
    arm_b_raw.mkdir(parents=True, exist_ok=True)

    # Arm A: reuse frozen Qwen decomposed outputs from prior pilot when available.
    qwen_source = QWEN_PILOT / "arm-b-decomposed" / "raw_window_metadata_light" / "raw"
    summary_rows: list[dict[str, Any]] = []

    def failure_layer(attempt: Any) -> str:
        if attempt.derivation and attempt.derivation.failure_layer:
            return attempt.derivation.failure_layer
        if attempt.extraction_status in {"extraction_failed", "atomic_incomplete"}:
            return "D1"
        return ""

    for row in manifest:
        event_id = row["event_id"]
        run = int(row["run"])
        seed = int(row.get("seed") or SEED_BASE + run - 1)
        event = events[event_id]
        filename = f"{event_id}-run{run}-seed{seed}.json"
        baseline = ARM_A_RAW / filename
        record = json.loads(baseline.read_text(encoding="utf-8-sig"))
        evidence = load_evidence(record)

        qwen_path = qwen_source / filename
        if qwen_path.is_file():
            arm_a_raw.joinpath(filename).write_text(qwen_path.read_text(encoding="utf-8-sig"), encoding="utf-8")
        else:
            out_a, _attempt_a = apply_decomposed_extraction(
                record,
                event,
                evidence,
                subtype=row.get("missing_subtype", ""),
                seed=seed,
                qwen_timeout=args.qwen_timeout,
                backend=ExtractionBackend.QWEN,
            )
            arm_a_raw.joinpath(filename).write_text(json.dumps(out_a, ensure_ascii=False, indent=2), encoding="utf-8")

        if args.skip_extraction and (arm_b_raw / filename).is_file():
            out_b = json.loads((arm_b_raw / filename).read_text(encoding="utf-8-sig"))
            attempt = None
        else:
            out_b, attempt = apply_decomposed_extraction(
                record,
                event,
                evidence,
                subtype=row.get("missing_subtype", ""),
                seed=seed,
                qwen_timeout=args.qwen_timeout,
                backend=ExtractionBackend.DEEPSEEK,
            )
        arm_b_raw.joinpath(filename).write_text(json.dumps(out_b, ensure_ascii=False, indent=2), encoding="utf-8")

        summary_rows.append(
            {
                "event_id": event_id,
                "run": run,
                "seed": seed,
                "semantic_type": row.get("semantic_type", ""),
                "missing_subtype": row.get("missing_subtype", ""),
                "deepseek_triggered": bool(attempt and attempt.triggered) if attempt else out_b.get("m12_decomposed_extraction", False),
                "deepseek_status": attempt.extraction_status if attempt else out_b.get("m12_derivation", {}).get("derivation_status", "resumed"),
                "deepseek_atomic_complete": str(attempt.atomic_complete).lower() if attempt else "",
                "deepseek_failure_layer": failure_layer(attempt) if attempt else "",
                "deepseek_runtime_ms": attempt.runtime_ms if attempt else 0,
                "deepseek_prompt_tokens": attempt.prompt_tokens if attempt else 0,
                "deepseek_completion_tokens": attempt.completion_tokens if attempt else 0,
                "deepseek_total_tokens": attempt.total_tokens if attempt else 0,
                "deepseek_model": attempt.model_id if attempt else out_b.get("m12_extraction_model", ""),
            }
        )
        print(
            f"{event_id} run={run} deepseek={(attempt.extraction_status if attempt else 'resumed')}",
            flush=True,
        )

    write_csv(args.output_dir / "deepseek-decomposition-summary.csv", summary_rows)
    (args.output_dir / "manifest.json").write_text(
        json.dumps(
            {
                "experiment": "Qwen3.5:9b vs DeepSeek API on Decomposed Semantic Extraction",
                "slots": len(manifest),
                "temperature": 0.2,
                "max_tokens": 1200,
                "deepseek_model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
                "deepseek_base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    if not args.skip_ir:
        run_ir(arm_a_raw, args.output_dir / "arm-a-qwen" / "ir", event_ids=event_ids, method_name="M12_Qwen_Decomposed")
        run_ir(
            arm_b_raw,
            args.output_dir / "arm-b-deepseek" / "ir",
            event_ids=event_ids,
            method_name="M12_DeepSeek_Decomposed",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
