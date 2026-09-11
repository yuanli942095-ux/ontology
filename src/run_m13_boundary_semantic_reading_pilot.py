from __future__ import annotations

from method_experiment_guard import add_legacy_opt_in_arg, block_legacy_entrypoint
"""Boundary-aware semantic reading pilot for M7 plus one CSS rank-abstain case.

This C-arm avoids schema-constrained JSON generation. It first focuses frozen
public evidence, then asks Qwen for a free-text semantic answer with evidence
spans, and only then converts the answer into a candidate-blind Semantic-IR-like
policy record. Candidate/oracle data are only used later by the existing V4
repair evaluator.
"""

import argparse
import csv
import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any

import run_auto_formal_policy_batch_v3 as v3
from auto_policy_m12_decomposed_extraction import normalize_token
from auto_policy_task_formulation import infer_repair_dimension, route_task_formulation
from run_m12_decomposed_extraction_pilot import ARM_A_RAW, BENCHMARK_DIR, IR_PREFIX, load_evidence, run_ir
from run_m12_predicate_focus_pilot import build_focused_evidence
from run_auto_policy_v4_ir_candidate_repair import configure_paths, read_csv, ready
from semantic_v2_common import PROJECT_DIR, write_csv


OUTPUT_ROOT = PROJECT_DIR / "output" / "m13-boundary-semantic-reading-pilot"
EXPANDED_MANIFEST = PROJECT_DIR / "output" / "m12-predicate-focus-pilot-v3-expanded-manifest.csv"


def load_manifest(path: Path) -> list[dict[str, str]]:
    rows = []
    for row in csv.DictReader(path.open(encoding="utf-8-sig")):
        if row.get("missing_subtype") == "M7_INVALID_JSON" or row.get("event_id") == "EXT_E197":
            rows.append(row)
    return rows


def source_record(row: dict[str, str]) -> dict[str, Any]:
    filename = f"{row['event_id']}-run{row['run']}-seed{row['seed']}.json"
    candidates = [
        PROJECT_DIR / str(row.get("raw_output_file", "")),
        ARM_A_RAW / filename,
    ]
    for path in candidates:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8-sig"))
    raise FileNotFoundError(filename)


def build_reading_prompt(event: dict[str, str], focused: str) -> str:
    formulation = route_task_formulation(event, focused)
    dimension = formulation.repair_dimension or infer_repair_dimension(event.get("predicate_label", ""))
    return f"""
You are reading public regulatory evidence for an ontology repair task.

Do not output JSON. Do not choose a candidate. Do not infer from private data.
Answer only from the supplied evidence. If the evidence is insufficient, say NO.

event_id: {event.get("event_id", "")}
semantic_type: {event.get("semantic_type", "")}
domain: {event.get("domain", "")}
subject_label: {event.get("subject_label", "")}
predicate_label: {event.get("predicate_label", "")}
repair_dimension: {dimension}
case_context: {event.get("case_context", "")}
extraction_mode: {formulation.extraction_mode.value}

Reading rules:
- Preserve full bullet/list boundaries when a sentence ends with "that:" or "including:".
- For disclosure topic, answer with the noun phrase after "disclose information about/on".
- For resource status, answer with the repository/webpage/mapping-spreadsheet availability status.
- For scope, ignore GHG Scope 1/2/3 unless the subject itself is greenhouse-gas scope.
- The VALUE must be a short phrase directly supported by EVIDENCE_SPANS.

Return this plain text form, not JSON:
ANSWERABLE: YES or NO
VALUE: short evidence-grounded answer value
EVIDENCE_SPANS:
- one exact or near-exact span from the evidence
- another span if needed
CONFIDENCE: high, medium, or low
REASON: one sentence

===== FOCUSED PUBLIC EVIDENCE BEGIN =====
{focused}
===== FOCUSED PUBLIC EVIDENCE END =====
""".strip()


def call_free_text(prompt: str, seed: int, timeout: int) -> tuple[str, int, int, str]:
    payload = {
        "model": v3.MODEL,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {
            "temperature": 0,
            "num_predict": 700,
            "num_ctx": 16384,
            "seed": seed,
        },
    }
    request = urllib.request.Request(
        v3.OLLAMA_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = json.loads(response.read().decode("utf-8"))
    return str(raw.get("response", "")), int((time.perf_counter() - start) * 1000), int(raw.get("eval_count", 0) or 0), str(raw.get("done_reason", ""))


def field_line(text: str, name: str) -> str:
    match = re.search(rf"(?im)^\s*{re.escape(name)}\s*:\s*(.+?)\s*$", text)
    return match.group(1).strip() if match else ""


def parse_spans(text: str) -> list[str]:
    match = re.search(r"(?ims)^\s*EVIDENCE_SPANS\s*:\s*(.*?)(?:^\s*CONFIDENCE\s*:|^\s*REASON\s*:|\Z)", text)
    if not match:
        return []
    spans = []
    for line in match.group(1).splitlines():
        cleaned = re.sub(r"^\s*[-*•]\s*", "", line).strip().strip('"')
        if cleaned:
            spans.append(cleaned)
    return spans


def parse_reading(text: str) -> dict[str, Any]:
    answerable = field_line(text, "ANSWERABLE").lower().startswith("yes")
    value = field_line(text, "VALUE").strip().strip('"')
    confidence = field_line(text, "CONFIDENCE").lower()
    reason = field_line(text, "REASON")
    return {
        "answerable": answerable,
        "value": value,
        "evidence_spans": parse_spans(text),
        "confidence": confidence,
        "reason": reason,
    }


def token_overlap(a: str, b: str) -> int:
    tokens_a = {t for t in re.findall(r"[a-z0-9]+", normalize_token(a)) if len(t) > 1}
    tokens_b = {t for t in re.findall(r"[a-z0-9]+", normalize_token(b)) if len(t) > 1}
    return len(tokens_a & tokens_b)


def faithfulness_status(reading: dict[str, Any], focused: str) -> tuple[bool, str]:
    if not reading["answerable"]:
        return False, "not_answerable"
    value = str(reading.get("value", "")).strip()
    spans = [str(item).strip() for item in reading.get("evidence_spans", []) if str(item).strip()]
    if not value:
        return False, "missing_value"
    if not spans:
        return False, "missing_evidence_spans"
    evidence_text = " ".join(spans) + "\n" + focused
    if token_overlap(value, evidence_text) <= 0:
        return False, "value_not_supported_by_span_tokens"
    if str(reading.get("confidence", "")).strip().lower() == "low":
        return False, "low_confidence"
    return True, "faithful"


def build_policy_record(record: dict[str, Any], event: dict[str, str], reading: dict[str, Any], focused: str) -> tuple[dict[str, Any], str, str]:
    faithful, reason = faithfulness_status(reading, focused)
    formulation = route_task_formulation(event, focused)
    dimension = formulation.repair_dimension or infer_repair_dimension(event.get("predicate_label", ""))
    if not faithful:
        out = {
            **record,
            "status": "INVALID_SCHEMA",
            "response": {
                "semantic_type": event.get("semantic_type", ""),
                "facts": {
                    "task_formulation": formulation.as_dict(),
                    "free_text_reading": reading,
                    "faithfulness_status": reason,
                },
                "canonical_result": {"family": "boundary_semantic_reading", "faithfulness_status": reason},
                "rules": [],
            },
            "m13_boundary_semantic_reading": True,
            "candidate_used": False,
            "oracle_used": False,
            "manual_policy_used": False,
        }
        return out, "fail_closed", reason

    normalized_value = normalize_token(str(reading["value"]))
    semantic_result = f"{dimension}={normalized_value}" if dimension else normalized_value
    policy = {
        "semantic_type": event.get("semantic_type", ""),
        "facts": {
            "task_formulation": formulation.as_dict(),
            "free_text_reading": reading,
            "evidence_spans": reading["evidence_spans"],
            "semantic_reading_faithful": True,
            "repair_dimension": dimension,
            "repair_value": reading["value"],
        },
        "canonical_result": {
            "family": "boundary_semantic_reading",
            "derived_semantic_result": semantic_result,
            "extraction_mode": formulation.extraction_mode.value,
        },
        "rules": [
            {
                "priority": 300,
                "conditions": [],
                "canonical_result": {
                    "family": "boundary_semantic_reading",
                    "derived_semantic_result": semantic_result,
                },
                "semantic_result": semantic_result,
            }
        ],
        "m13_boundary_semantic_reading": True,
    }
    out = {
        **record,
        "status": "GENERATED",
        "response": policy,
        "schema_valid": True,
        "validation_reason": "free_text_reading_converted",
        "canonical_status": "OK",
        "canonical_result": policy["canonical_result"],
        "method": "M13_BOUNDARY_SEMANTIC_READING",
        "candidate_used": False,
        "oracle_used": False,
        "manual_policy_used": False,
        "m13_boundary_semantic_reading": True,
    }
    return out, "converted", reason


def main() -> int:
    block_legacy_entrypoint(__file__)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=EXPANDED_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--top-n", type=int, default=4)
    parser.add_argument("--qwen-timeout", type=int, default=180)
    parser.add_argument("--skip-ir", action="store_true")
    args = parser.parse_args()
    args.output_dir = args.output_dir.resolve()

    manifest = load_manifest(args.manifest)
    paths = configure_paths(BENCHMARK_DIR)
    events = {row["event_id"]: row for row in read_csv(paths["event_csv"]) if ready(row.get("status", ""))}
    event_ids = {row["event_id"] for row in manifest}

    arm_a_raw = args.output_dir / "arm-a-v4-original" / "raw_window_metadata_light" / "raw"
    arm_c_raw = args.output_dir / "arm-c-boundary-reading" / "raw_window_metadata_light" / "raw"
    focus_dir = args.output_dir / "focused-evidence"
    reading_dir = args.output_dir / "free-text-readings"
    for directory in (arm_a_raw, arm_c_raw, focus_dir, reading_dir):
        directory.mkdir(parents=True, exist_ok=True)

    summary_rows: list[dict[str, Any]] = []
    for row in manifest:
        event = events[row["event_id"]]
        filename = f"{row['event_id']}-run{row['run']}-seed{row['seed']}.json"
        record = source_record(row)
        arm_a_raw.joinpath(filename).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

        evidence = load_evidence(record)
        focused, selected_units = build_focused_evidence(event, evidence, top_n=args.top_n)
        focus_path = focus_dir / f"{row['event_id']}-run{row['run']}-seed{row['seed']}-focused.md"
        focus_path.write_text(focused, encoding="utf-8")

        prompt = build_reading_prompt(event, focused)
        try:
            raw_text, runtime_ms, eval_count, done_reason = call_free_text(prompt, int(row["seed"]), args.qwen_timeout)
            reading = parse_reading(raw_text)
            out_record, conversion_status, faithfulness = build_policy_record(record, event, reading, focused)
        except Exception as exc:
            raw_text = ""
            runtime_ms = 0
            eval_count = 0
            done_reason = f"error:{type(exc).__name__}"
            reading = {"answerable": False, "value": "", "evidence_spans": [], "confidence": "", "reason": str(exc)}
            out_record = {**record, "status": "INVALID_SCHEMA", "m13_boundary_semantic_reading": True}
            conversion_status = "call_failed"
            faithfulness = done_reason

        reading_path = reading_dir / f"{row['event_id']}-run{row['run']}-seed{row['seed']}.txt"
        reading_path.write_text(raw_text, encoding="utf-8")
        out_record["m13_boundary_semantic_reading_audit"] = {
            "focused_evidence_file": str(focus_path.relative_to(PROJECT_DIR)),
            "free_text_reading_file": str(reading_path.relative_to(PROJECT_DIR)),
            "reading": reading,
            "conversion_status": conversion_status,
            "faithfulness_status": faithfulness,
            "selected_units": selected_units,
            "candidate_used": False,
            "oracle_used": False,
            "manual_policy_used": False,
        }
        arm_c_raw.joinpath(filename).write_text(json.dumps(out_record, ensure_ascii=False, indent=2), encoding="utf-8")
        summary_rows.append(
            {
                "event_id": row["event_id"],
                "run": row["run"],
                "seed": row["seed"],
                "semantic_type": row["semantic_type"],
                "domain": row["domain"],
                "missing_subtype": row["missing_subtype"],
                "extraction_mode": route_task_formulation(event, focused).extraction_mode.value,
                "answerable": reading["answerable"],
                "value": reading["value"],
                "span_count": len(reading["evidence_spans"]),
                "confidence": reading["confidence"],
                "conversion_status": conversion_status,
                "faithfulness_status": faithfulness,
                "runtime_ms": runtime_ms,
                "eval_count": eval_count,
                "done_reason": done_reason,
                "focused_evidence_file": str(focus_path.relative_to(PROJECT_DIR)),
                "free_text_reading_file": str(reading_path.relative_to(PROJECT_DIR)),
            }
        )
        print(
            f"{row['event_id']} run={row['run']} subtype={row['missing_subtype']} "
            f"mode={summary_rows[-1]['extraction_mode']} status={conversion_status} faith={faithfulness}",
            flush=True,
        )

    write_csv(args.output_dir / "boundary-semantic-reading-summary.csv", summary_rows)
    if not args.skip_ir:
        run_ir(arm_a_raw, args.output_dir / "arm-a-v4-original" / "ir", event_ids=event_ids, method_name="V4_Original")
        run_ir(arm_c_raw, args.output_dir / "arm-c-boundary-reading" / "ir", event_ids=event_ids, method_name="M13_Boundary_Semantic_Reading")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
