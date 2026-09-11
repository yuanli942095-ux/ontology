from __future__ import annotations

from method_experiment_guard import add_legacy_opt_in_arg, block_legacy_entrypoint
"""Re-run the 42 handoff INVALID_JSON slots with V4.5 generation reliability.

Does not change prompt semantics, scorer, gate, or IR repair logic.
"""

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import run_auto_formal_policy_batch_v3 as v3
from auto_policy_v45_generation import (
    METHOD_NAME,
    PROMPT_VERSION_SUFFIX,
    ReliabilityMode,
    call_qwen_reliable,
    generation_metadata,
    mode_for_failure_bucket,
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


DEFAULT_CLASSIFICATION = (
    PROJECT_DIR
    / "output"
    / "auto-policy-v4.4-invalid-json-recovery"
    / "invalid-json-by-attempt.csv"
)
DEFAULT_OUTPUT = PROJECT_DIR / "output" / "auto-policy-v4.5-reliability" / "preformal-r3" / "raw_window_metadata_light"
VARIANT = "RAW_WINDOW_METADATA_LIGHT"


def load_rows(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(path.open(encoding="utf-8-sig")))


def slot_key(row: dict[str, str]) -> tuple[str, str, str]:
    return (row["event_id"], row["run"], row["seed"])


def generate_slot(
    row: dict[str, str],
    events: dict[str, dict[str, str]],
    docs_by_event: dict[str, list[dict[str, str]]],
    raw_dir: Path,
    evidence_dir: Path,
    *,
    force_mode: ReliabilityMode | None = None,
    qwen_timeout: int,
) -> dict[str, Any]:
    event_id = row["event_id"]
    run = int(row["run"])
    seed = int(row["seed"])
    event = events[event_id]
    mode = force_mode or mode_for_failure_bucket(row["failure_bucket"])
    evidence_file = evidence_dir / f"{event_id}-run{run}-seed{seed}-candidate-blind.md"
    raw_path = raw_dir / f"{event_id}-run{run}-seed{seed}.json"

    retrieval = evidence_for_variant(
        VARIANT,
        event_id,
        event,
        docs_by_event[event_id],
        events,
        docs_by_event,
        seed,
    )
    evidence_file.write_text(retrieval.evidence, encoding="utf-8")
    event_for_prompt = metadata_context(event, prompt_metadata_mode(VARIANT))
    prompt = build_prompt(event_for_prompt, retrieval.evidence)

    call = call_qwen_reliable(prompt, seed, mode, timeout=qwen_timeout)
    qwen_response = call.response
    response_text = str(qwen_response.get("response", ""))
    forbidden_markers = v3.forbidden_output_markers(response_text)
    parsed = v3.extract_json(response_text)
    event_for_normalize = {**event_for_prompt, "semantic_type": event["semantic_type"]}
    parsed, canonical_status, canonical_result, canonical_semantic_result = v3.normalize_generated_policy(
        parsed,
        event_for_normalize,
        retrieval.evidence,
    )
    schema_valid, validation_reason = v3.validate_generated_policy(parsed, event["semantic_type"].strip())
    if forbidden_markers:
        status = "FORBIDDEN_OUTPUT"
    elif parsed is None:
        status = "INVALID_JSON"
    elif isinstance(parsed, dict) and parsed.get("abstain", False):
        status = "ABSTAIN"
    elif not schema_valid:
        status = "INVALID_SCHEMA"
    else:
        status = "GENERATED"

    record = {
        "event_id": event_id,
        "semantic_type": event["semantic_type"].strip(),
        "run": run,
        "seed": seed,
        "variant": VARIANT,
        "model": v3.MODEL,
        "prompt_version": f"AUTO_POLICY_V3_NATURAL_EVIDENCE_{VARIANT}_{PROMPT_VERSION_SUFFIX}",
        "source_type": "CANDIDATE_BLIND_NATURAL_EVIDENCE",
        "source_file": retrieval.source_label,
        "candidate_blind_file": str(evidence_file.relative_to(v3.ROOT)),
        "oracle_used": retrieval.oracle_used,
        "candidate_used": retrieval.candidate_used,
        "note_used": retrieval.note_used,
        "manual_formal_policy_used": retrieval.manual_policy_used,
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
        "status": status,
        "runtime_ms": call.runtime_ms,
        "forbidden_markers": forbidden_markers,
        "schema_valid": schema_valid,
        "validation_reason": validation_reason,
        "canonical_status": canonical_status,
        "canonical_result": canonical_result,
        "canonical_semantic_result": canonical_semantic_result,
        "raw_response_text": response_text,
        "response": parsed,
        "v4_4_failure_bucket": row.get("failure_bucket", ""),
        **generation_metadata(call, qwen_response),
    }
    raw_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "event_id": event_id,
        "run": run,
        "seed": seed,
        "semantic_type": event["semantic_type"].strip(),
        "domain": event.get("domain", ""),
        "v4_4_failure_bucket": row.get("failure_bucket", ""),
        "generation_reliability_mode": mode.value,
        "generation_status": status,
        "validation_reason": validation_reason,
        "done_reason": record["done_reason"],
        "prompt_eval_count": record["prompt_eval_count"],
        "eval_count": record["eval_count"],
        "num_predict": record["num_predict"],
        "num_ctx": record["num_ctx"],
        "format_constraint": record["format_constraint"],
        "raw_output_file": str(raw_path.relative_to(PROJECT_DIR)),
    }


def main() -> int:
    block_legacy_entrypoint(__file__)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", default="external-real-v8-grounded")
    parser.add_argument("--classification-csv", type=Path, default=DEFAULT_CLASSIFICATION)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--mode",
        choices=[item.value for item in ReliabilityMode],
        default="",
        help="override per-bucket mode selection",
    )
    parser.add_argument("--qwen-timeout", type=int, default=600)
    parser.add_argument("--only", default="", help="comma-separated event ids")
    args = parser.parse_args()

    configure_benchmark(args.benchmark, args.output_dir.parent.parent)
    rows = load_rows(args.classification_csv)
    if args.only.strip():
        only = {item.strip().upper() for item in args.only.split(",") if item.strip()}
        rows = [row for row in rows if row["event_id"] in only]
    force_mode = ReliabilityMode(args.mode) if args.mode else None

    events = v3.load_events()
    docs_by_event = document_rows_by_event()
    raw_dir = args.output_dir / "raw"
    evidence_dir = args.output_dir / "candidate-blind-evidence"
    raw_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    summary_rows: list[dict[str, Any]] = []
    total = len(rows)
    for index, row in enumerate(rows, start=1):
        print(
            f"[{index}/{total}] {row['event_id']} run={row['run']} "
            f"bucket={row['failure_bucket']} mode={(force_mode or mode_for_failure_bucket(row['failure_bucket'])).value}",
            flush=True,
        )
        summary_rows.append(
            generate_slot(
                row,
                events,
                docs_by_event,
                raw_dir,
                evidence_dir,
                force_mode=force_mode,
                qwen_timeout=args.qwen_timeout,
            )
        )

    out_dir = PROJECT_DIR / "output" / "auto-policy-v4.5-reliability"
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "v45-generation-rerun-summary.csv", summary_rows)
    (out_dir / "v45-generation-rerun-summary.json").write_text(
        json.dumps(
            {
                "method": METHOD_NAME,
                "attempts": len(summary_rows),
                "generation_status": dict(
                    sorted(
                        {
                            status: sum(1 for row in summary_rows if row["generation_status"] == status)
                            for status in {row["generation_status"] for row in summary_rows}
                        }.items()
                    )
                ),
                "rows": summary_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(summary_rows[-1], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
