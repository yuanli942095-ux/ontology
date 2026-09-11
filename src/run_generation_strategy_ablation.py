from __future__ import annotations

from method_experiment_guard import add_legacy_opt_in_arg, block_legacy_entrypoint
"""Run 180-attempt generation strategy ablation: adaptive vs always-structured."""

import argparse
import json
from pathlib import Path
from typing import Any

import run_auto_formal_policy_batch_v3 as v3
from generation_strategy_ablation import (
    GenerationStrategy,
    PILOT_EVENTS_FILE,
    PILOT_OUTPUT_DIR,
    generation_record_from_result,
    run_adaptive_generation,
    run_always_structured_generation,
)
from run_auto_policy_v3_natural_evidence_robustness import (
    configure_benchmark,
    document_rows_by_event,
    evidence_for_variant,
    metadata_context,
    prompt_metadata_mode,
)
from run_auto_policy_v3_robustness import build_prompt
from semantic_v2_common import PROJECT_DIR, write_csv

VARIANT = "RAW_WINDOW_METADATA_LIGHT"
DEFAULT_BASELINE_RAW = (
    PROJECT_DIR
    / "output"
    / "external-real-v8-grounded"
    / "preformal-r3"
    / "raw_window_metadata_light"
    / "raw"
)


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def retrieval_meta(retrieval: Any) -> dict[str, Any]:
    return {
        "source_file": retrieval.source_label,
        "oracle_used": retrieval.oracle_used,
        "candidate_used": retrieval.candidate_used,
        "note_used": retrieval.note_used,
        "manual_policy_used": retrieval.manual_policy_used,
        "retrieval_source": retrieval.retrieval_source,
        "retrieval_replayed": retrieval.retrieval_replayed,
        "retrieval_status": retrieval.retrieval_status,
        "raw_source_used": retrieval.raw_source_used,
        "fallback_used": retrieval.fallback_used,
        "retrieved_doc_count": retrieval.retrieved_doc_count,
        "retrieved_window_count": retrieval.retrieved_window_count,
        "source_urls": list(retrieval.source_urls),
        "forbidden_input_markers": list(retrieval.forbidden_input_markers),
    }


def run_arm(
    *,
    strategy: GenerationStrategy,
    slots: list[dict[str, Any]],
    events: dict[str, dict[str, str]],
    docs_by_event: dict[str, list[dict[str, str]]],
    output_dir: Path,
    baseline_raw_dir: Path | None,
    qwen_timeout: int,
    overwrite: bool,
) -> list[dict[str, Any]]:
    raw_dir = output_dir / "raw_window_metadata_light" / "raw"
    evidence_dir = output_dir / "raw_window_metadata_light" / "candidate-blind-evidence"
    raw_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    total = len(slots)
    for index, slot in enumerate(slots, start=1):
        event_id = slot["event_id"]
        run = int(slot["run"])
        seed = int(slot["seed"])
        raw_path = raw_dir / f"{event_id}-run{run}-seed{seed}.json"
        if raw_path.is_file() and not overwrite:
            record = json.loads(raw_path.read_text(encoding="utf-8-sig"))
            rows.append(
                {
                    "event_id": event_id,
                    "run": run,
                    "seed": seed,
                    "generation_strategy": record.get("generation_strategy", ""),
                    "generation_status": record.get("status", ""),
                    "generation_call_count": record.get("generation_call_count", 1),
                    "raw_output_file": str(raw_path.relative_to(PROJECT_DIR)),
                    "resumed": True,
                }
            )
            continue
        event = events[event_id]
        retrieval = evidence_for_variant(
            VARIANT,
            event_id,
            event,
            docs_by_event[event_id],
            events,
            docs_by_event,
            seed,
        )
        evidence_file = evidence_dir / f"{event_id}-run{run}-seed{seed}-candidate-blind.md"
        evidence_file.write_text(retrieval.evidence, encoding="utf-8")
        event_for_prompt = metadata_context(event, prompt_metadata_mode(VARIANT))
        prompt = build_prompt(event_for_prompt, retrieval.evidence)
        print(
            f"[{strategy.value} {index}/{total}] {event_id} run={run} seed={seed}",
            flush=True,
        )
        baseline_record = None
        if strategy is GenerationStrategy.ADAPTIVE and baseline_raw_dir is not None:
            baseline_path = baseline_raw_dir / raw_path.name
            if baseline_path.is_file():
                baseline_record = json.loads(baseline_path.read_text(encoding="utf-8-sig"))
        if strategy is GenerationStrategy.ADAPTIVE:
            result = run_adaptive_generation(
                prompt,
                seed,
                event=event,
                event_for_prompt=event_for_prompt,
                evidence=retrieval.evidence,
                baseline_record=baseline_record,
                timeout=qwen_timeout,
            )
        else:
            result = run_always_structured_generation(
                prompt,
                seed,
                event=event,
                event_for_prompt=event_for_prompt,
                evidence=retrieval.evidence,
                timeout=qwen_timeout,
            )
        record = generation_record_from_result(
            result,
            event=event,
            run=run,
            seed=seed,
            variant=VARIANT,
            retrieval_meta=retrieval_meta(retrieval),
            evidence_file=evidence_file.relative_to(v3.ROOT),
        )
        raw_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        rows.append(
            {
                "event_id": event_id,
                "run": run,
                "seed": seed,
                "semantic_type": event["semantic_type"].strip(),
                "domain": event.get("domain", ""),
                "generation_strategy": record["generation_strategy"],
                "generation_status": record["status"],
                "first_attempt_status": record["first_attempt_status"],
                "generation_call_count": record["generation_call_count"],
                "runtime_ms": record["runtime_ms"],
                "done_reason": record["done_reason"],
                "raw_output_file": str(raw_path.relative_to(PROJECT_DIR)),
                "resumed": False,
            }
        )
    write_csv(output_dir / "generation-summary.csv", rows)
    return rows


def main() -> int:
    block_legacy_entrypoint(__file__)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", default="external-real-v8-grounded")
    parser.add_argument("--manifest", type=Path, default=PILOT_EVENTS_FILE)
    parser.add_argument("--arm", choices=["adaptive", "always_structured", "both"], default="both")
    parser.add_argument("--output-dir", type=Path, default=PILOT_OUTPUT_DIR)
    parser.add_argument("--baseline-raw-dir", type=Path, default=DEFAULT_BASELINE_RAW)
    parser.add_argument("--no-baseline-reuse", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--qwen-timeout", type=int, default=600)
    args = parser.parse_args()

    configure_benchmark(args.benchmark, args.output_dir)
    manifest = load_manifest(args.manifest)
    slots = manifest["slots"]
    events = v3.load_events()
    docs_by_event = document_rows_by_event()
    baseline_raw = None if args.no_baseline_reuse else args.baseline_raw_dir

    if args.arm in {"adaptive", "both"}:
        run_arm(
            strategy=GenerationStrategy.ADAPTIVE,
            slots=slots,
            events=events,
            docs_by_event=docs_by_event,
            output_dir=args.output_dir / "adaptive",
            baseline_raw_dir=baseline_raw,
            qwen_timeout=args.qwen_timeout,
            overwrite=args.overwrite,
        )
    if args.arm in {"always_structured", "both"}:
        run_arm(
            strategy=GenerationStrategy.ALWAYS_STRUCTURED,
            slots=slots,
            events=events,
            docs_by_event=docs_by_event,
            output_dir=args.output_dir / "always-structured",
            baseline_raw_dir=None,
            qwen_timeout=args.qwen_timeout,
            overwrite=args.overwrite,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
