from __future__ import annotations

"""Regulatory rule extraction + refinement pilot for M7/CSS hard cases.

The pipeline follows legal/regulatory IE practice more closely than direct JSON:
1. boundary-aware evidence focus over frozen public evidence;
2. free-text rule draft extraction;
3. evidence-grounded critique/refinement;
4. deterministic conversion to a policy-like record;
5. existing V4 candidate repair and OWL closure evaluation.

No candidate, Oracle, or manual policy is used before the V4 evaluation stage.
"""

import argparse
import csv
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import run_auto_formal_policy_batch_v3 as v3
from auto_policy_m12_decomposed_extraction import normalize_token
from auto_policy_task_formulation import infer_repair_dimension, route_task_formulation
from run_auto_policy_v4_ir_candidate_repair import configure_paths, read_csv, ready
from run_m12_decomposed_extraction_pilot import ARM_A_RAW, BENCHMARK_DIR, IR_PREFIX, load_evidence
from run_m12_predicate_focus_pilot import build_focused_evidence
from semantic_v2_common import PROJECT_DIR, write_csv
from m13_llm_backends import LLMBackend, call_llm_text, resume_artifact_is_complete
from method_experiment_guard import (
    add_legacy_opt_in_arg,
    guard_holdout_benchmark,
    official_run_active,
    require_official_runner_or_legacy,
)


OUTPUT_ROOT = PROJECT_DIR / "output" / "m13-rule-refinement-pilot"
EXPANDED_MANIFEST = PROJECT_DIR / "output" / "m12-predicate-focus-pilot-v3-expanded-manifest.csv"
FORMAL_HOLDOUT_SEEDS = (20260829, 20260830, 20260831, 20260901, 20260902)


def load_manifest(path: Path, *, mode: str = "m7") -> list[dict[str, str]]:
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    if mode == "all":
        return rows
    return [
        row
        for row in rows
        if row.get("missing_subtype") == "M7_INVALID_JSON" or row.get("event_id") == "EXT_E197"
    ]


def parse_only(raw: str) -> set[str]:
    return {token.strip().upper() for token in raw.split(",") if token.strip()}


def build_all_manifest(events: dict[str, dict[str, str]], *, runs: int) -> list[dict[str, str]]:
    seeds = list(FORMAL_HOLDOUT_SEEDS[:runs])
    if len(seeds) < runs:
        last = FORMAL_HOLDOUT_SEEDS[-1]
        seeds.extend(last + offset + 1 for offset in range(runs - len(seeds)))
    rows: list[dict[str, str]] = []
    for event_id in sorted(events):
        event = events[event_id]
        for index, seed in enumerate(seeds, start=1):
            rows.append(
                {
                    "event_id": event_id,
                    "run": str(index),
                    "seed": str(seed),
                    "semantic_type": event.get("semantic_type", ""),
                    "domain": event.get("domain", ""),
                    "missing_subtype": "FORMAL_HOLDOUT_EVAL",
                    "raw_output_file": "",
                }
            )
    return rows


def run_ir(
    raw_dir: Path,
    output_dir: Path,
    *,
    event_ids: set[str],
    method_name: str,
    benchmark_dir: Path,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(PROJECT_DIR / "src" / "run_auto_policy_v4_ir_candidate_repair.py"),
        "--benchmark-dir",
        str(benchmark_dir),
        "--raw-dir",
        str(raw_dir),
        "--output-dir",
        str(output_dir),
        "--prefix",
        IR_PREFIX,
        "--method-name",
        method_name,
        "--min-score",
        "0.30",
        "--gre-css-min-score",
        "0.24",
        "--min-margin",
        "0.00",
        "--reranker",
        "constraint",
        "--temporal-unique-top1",
        "--robust-ir",
        "--skip-missing-raw",
        "--discover-raw",
        "--only",
        ",".join(sorted(event_ids)),
    ]
    subprocess.run(cmd, check=True, cwd=PROJECT_DIR)
    return output_dir / f"{IR_PREFIX}-details.csv"


def source_record(
    row: dict[str, str],
    *,
    event: dict[str, str],
    benchmark_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    filename = f"{row['event_id']}-run{row['run']}-seed{row['seed']}.json"
    for path in (PROJECT_DIR / str(row.get("raw_output_file", "")), ARM_A_RAW / filename):
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8-sig"))
    evidence_path = benchmark_dir / "public" / "excerpts" / f"{row['event_id']}-evidence.md"
    if not evidence_path.is_file():
        raise FileNotFoundError(f"missing evidence for {filename}: {evidence_path}")
    public_input_dir = output_dir / "public-input" / "candidate-blind-evidence"
    public_input_dir.mkdir(parents=True, exist_ok=True)
    candidate_blind_path = public_input_dir / f"{row['event_id']}-run{row['run']}-seed{row['seed']}-candidate-blind.md"
    candidate_blind_path.write_text(evidence_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    return {
        "event_id": row["event_id"],
        "semantic_type": event.get("semantic_type", row.get("semantic_type", "")),
        "run": int(row["run"]),
        "seed": int(row["seed"]),
        "variant": "HOLDOUT_PUBLIC_FROZEN_EVIDENCE",
        "source_type": "HOLDOUT_PUBLIC_FROZEN_EVIDENCE",
        "candidate_blind_file": str(candidate_blind_path.relative_to(PROJECT_DIR)),
        "oracle_used": False,
        "candidate_used": False,
        "note_used": False,
        "manual_policy_used": False,
        "manual_formal_policy_used": False,
        "retrieval_status": "RETRIEVAL_READY",
        "status": "RETRIEVAL_READY",
        "m13_input_stub": True,
    }


def call_qwen_text(prompt: str, seed: int, timeout: int, *, num_predict: int = 900) -> tuple[str, int, int, str]:
    payload = {
        "model": v3.MODEL,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {
            "temperature": 0,
            "num_predict": num_predict,
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


def build_draft_prompt(event: dict[str, str], focused: str) -> str:
    formulation = route_task_formulation(event, focused)
    dimension = formulation.repair_dimension or infer_repair_dimension(event.get("predicate_label", ""))
    return f"""
Extract a regulatory rule draft from the public evidence.

Do not output JSON. Do not choose from candidates. Use only the supplied evidence.

event_id: {event.get("event_id", "")}
semantic_type: {event.get("semantic_type", "")}
extraction_mode: {formulation.extraction_mode.value}
subject_label: {event.get("subject_label", "")}
predicate_label: {event.get("predicate_label", "")}
repair_dimension: {dimension}
case_context: {event.get("case_context", "")}

Write exactly these lines:
RULE_TYPE: ATTRIBUTE | EXCEPTION | SCOPE | UNANSWERABLE
SUBJECT: evidence-grounded subject
PREDICATE: target predicate
CONDITION: condition or qualifier, if any
NORMATIVE_VALUE: concise value that should populate repair_dimension
RELATION: applies_to | exception_overrides | amends | effective_from | unknown
EVIDENCE:
- supporting span copied or closely copied from evidence
CONFIDENCE: high | medium | low

Guidance:
- For rule-exception, NORMATIVE_VALUE is the exception/general outcome supported by case_context.
- For scope, NORMATIVE_VALUE is the scope target or disclosure topic, not a random nearby term.
- For eCFR events, status/authority/amendment cues such as "amends title 17" are not enough unless they answer the predicate.
- If the evidence does not answer the predicate, use RULE_TYPE: UNANSWERABLE.

===== FOCUSED PUBLIC EVIDENCE BEGIN =====
{focused}
===== FOCUSED PUBLIC EVIDENCE END =====
""".strip()


def build_refine_prompt(event: dict[str, str], focused: str, draft: str) -> str:
    formulation = route_task_formulation(event, focused)
    dimension = formulation.repair_dimension or infer_repair_dimension(event.get("predicate_label", ""))
    return f"""
You are the evidence judge for a regulatory rule extraction.

Revise the draft so that every value is directly supported by the evidence.
Do not output JSON. Do not choose candidates. Do not invent missing facts.

event_id: {event.get("event_id", "")}
semantic_type: {event.get("semantic_type", "")}
subject_label: {event.get("subject_label", "")}
predicate_label: {event.get("predicate_label", "")}
repair_dimension: {dimension}

Draft:
{draft}

Return exactly these lines after checking the evidence:
ANSWERABLE: YES | NO
RULE_TYPE: ATTRIBUTE | EXCEPTION | SCOPE | UNANSWERABLE
SUBJECT: revised subject
PREDICATE: revised predicate
CONDITION: revised condition or qualifier
NORMATIVE_VALUE: revised concise value
RELATION: applies_to | exception_overrides | amends | effective_from | unknown
EVIDENCE:
- exact or near-exact supporting span
CONFIDENCE: high | medium | low
REJECTION_REASON: reason if ANSWERABLE is NO

===== FOCUSED PUBLIC EVIDENCE BEGIN =====
{focused}
===== FOCUSED PUBLIC EVIDENCE END =====
""".strip()


def field_line(text: str, name: str) -> str:
    match = re.search(rf"(?im)^\s*{re.escape(name)}\s*:\s*(.*?)\s*$", text)
    return match.group(1).strip() if match else ""


def parse_evidence_lines(text: str) -> list[str]:
    match = re.search(r"(?ims)^\s*EVIDENCE\s*:\s*(.*?)(?:^\s*CONFIDENCE\s*:|^\s*REJECTION_REASON\s*:|\Z)", text)
    if not match:
        return []
    spans: list[str] = []
    for line in match.group(1).splitlines():
        cleaned = re.sub(r"^\s*[-*•]\s*", "", line).strip().strip('"')
        if cleaned:
            spans.append(cleaned)
    return spans


def parse_refined_rule(text: str) -> dict[str, Any]:
    return {
        "answerable": field_line(text, "ANSWERABLE").lower().startswith("yes"),
        "rule_type": field_line(text, "RULE_TYPE").upper(),
        "subject": field_line(text, "SUBJECT"),
        "predicate": field_line(text, "PREDICATE"),
        "condition": field_line(text, "CONDITION"),
        "normative_value": field_line(text, "NORMATIVE_VALUE").strip().strip('"'),
        "relation": field_line(text, "RELATION").lower(),
        "evidence": parse_evidence_lines(text),
        "confidence": field_line(text, "CONFIDENCE").lower(),
        "rejection_reason": field_line(text, "REJECTION_REASON"),
    }


def token_overlap(a: str, b: str) -> int:
    tokens_a = {token for token in re.findall(r"[a-z0-9]+", normalize_token(a)) if len(token) > 1}
    tokens_b = {token for token in re.findall(r"[a-z0-9]+", normalize_token(b)) if len(token) > 1}
    return len(tokens_a & tokens_b)


def verify_rule(rule: dict[str, Any], focused: str) -> tuple[bool, str]:
    if not rule["answerable"] or rule["rule_type"] == "UNANSWERABLE":
        return False, "not_answerable"
    if not rule["normative_value"]:
        return False, "missing_normative_value"
    if not rule["evidence"]:
        return False, "missing_evidence"
    if token_overlap(str(rule["normative_value"]), " ".join(rule["evidence"]) + "\n" + focused) <= 0:
        return False, "value_not_supported"
    if rule["confidence"] == "low":
        return False, "low_confidence"
    return True, "faithful"


def build_record(record: dict[str, Any], event: dict[str, str], rule: dict[str, Any], focused: str) -> tuple[dict[str, Any], str]:
    ok, status = verify_rule(rule, focused)
    formulation = route_task_formulation(event, focused)
    dimension = formulation.repair_dimension or infer_repair_dimension(event.get("predicate_label", ""))
    if not ok:
        return {
            **record,
            "status": "INVALID_SCHEMA",
            "response": {
                "semantic_type": event.get("semantic_type", ""),
                "facts": {"refined_rule": rule, "faithfulness_status": status, "task_formulation": formulation.as_dict()},
                "canonical_result": {"family": "regulatory_rule_refinement", "faithfulness_status": status},
                "rules": [],
            },
            "m13_rule_refinement": True,
            "candidate_used": False,
            "oracle_used": False,
            "manual_policy_used": False,
        }, status

    value = normalize_token(str(rule["normative_value"]))
    semantic_result = f"{dimension}={value}" if dimension else value
    canonical = {
        "family": "regulatory_rule_refinement",
        "derived_semantic_result": semantic_result,
        "extraction_mode": formulation.extraction_mode.value,
        "relation": rule.get("relation", ""),
    }
    policy = {
        "semantic_type": event.get("semantic_type", ""),
        "facts": {
            "task_formulation": formulation.as_dict(),
            "refined_rule": rule,
            "repair_dimension": dimension,
            "repair_value": rule["normative_value"],
            "evidence_spans": rule["evidence"],
            "semantic_reading_faithful": True,
        },
        "canonical_result": canonical,
        "rules": [{"priority": 300, "conditions": [], "canonical_result": canonical, "semantic_result": semantic_result}],
        "m13_rule_refinement": True,
    }
    return {
        **record,
        "status": "GENERATED",
        "response": policy,
        "schema_valid": True,
        "validation_reason": "rule_refinement_converted",
        "canonical_status": "OK",
        "canonical_result": canonical,
        "method": "M13_REGULATORY_RULE_REFINEMENT",
        "candidate_used": False,
        "oracle_used": False,
        "manual_policy_used": False,
        "m13_rule_refinement": True,
    }, status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=EXPANDED_MANIFEST)
    parser.add_argument(
        "--manifest-mode",
        choices=("m7", "all"),
        default="m7",
        help="m7: legacy M7-invalid-json subset; all: use every manifest row (smoke test).",
    )
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK_DIR)
    parser.add_argument("--top-n", type=int, default=4)
    parser.add_argument("--qwen-timeout", type=int, default=180)
    parser.add_argument("--runs", type=int, default=5, help="runs per event when --manifest-mode all rebuilds the manifest")
    parser.add_argument("--skip-ir", action="store_true")
    parser.add_argument("--only", default="", help="comma-separated event ids")
    parser.add_argument("--resume", action="store_true", help="skip events with completed arm-d raw")
    parser.add_argument(
        "--main-method-only",
        action="store_true",
        help="Skip arm-a V4-original baseline IR (default when launched by official runner).",
    )
    parser.add_argument(
        "--llm-backend",
        choices=("ollama", "deepseek_api"),
        default="ollama",
        help="ollama: frozen primary Qwen3.5:9b; deepseek_api: secondary model substitution ablation only.",
    )
    add_legacy_opt_in_arg(parser)
    args = parser.parse_args()
    guard_holdout_benchmark(args.benchmark_dir, __file__)
    require_official_runner_or_legacy(__file__)
    if official_run_active():
        args.main_method_only = True
    args.output_dir = args.output_dir.resolve()

    args.benchmark_dir = args.benchmark_dir.resolve()
    paths = configure_paths(args.benchmark_dir)
    events = {row["event_id"]: row for row in read_csv(paths["event_csv"]) if ready(row.get("status", ""))}
    manifest = load_manifest(args.manifest, mode=args.manifest_mode)
    manifest_ids = {row.get("event_id", "") for row in manifest}
    if args.manifest_mode == "all" and not manifest_ids.issubset(set(events)):
        manifest = build_all_manifest(events, runs=args.runs)
    only = parse_only(args.only)
    if only:
        manifest = [row for row in manifest if row["event_id"].upper() in only]
    if not manifest:
        raise SystemExit("manifest is empty after filtering")
    event_ids = {row["event_id"] for row in manifest}

    arm_a_raw = args.output_dir / "arm-a-v4-original" / "raw_window_metadata_light" / "raw"
    arm_d_raw = args.output_dir / "arm-d-rule-refinement" / "raw_window_metadata_light" / "raw"
    focus_dir = args.output_dir / "focused-evidence"
    draft_dir = args.output_dir / "rule-drafts"
    refined_dir = args.output_dir / "rule-refined"
    for directory in (arm_a_raw, arm_d_raw, focus_dir, draft_dir, refined_dir):
        directory.mkdir(parents=True, exist_ok=True)

    rows_out: list[dict[str, Any]] = []
    for row in manifest:
        event = events[row["event_id"]]
        filename = f"{row['event_id']}-run{row['run']}-seed{row['seed']}.json"
        focus_path = focus_dir / f"{row['event_id']}-run{row['run']}-seed{row['seed']}-focused.md"
        draft_path = draft_dir / f"{row['event_id']}-run{row['run']}-seed{row['seed']}.txt"
        refined_path = refined_dir / f"{row['event_id']}-run{row['run']}-seed{row['seed']}.txt"
        arm_d_path = arm_d_raw / filename
        if args.resume and resume_artifact_is_complete(arm_d_path) and focus_path.is_file():
            print(f"{row['event_id']} run={row['run']} [resume] skipped", flush=True)
            continue
        if args.resume and arm_d_path.is_file():
            print(f"{row['event_id']} run={row['run']} [resume] retry transport-failed artifact", flush=True)

        record = source_record(row, event=event, benchmark_dir=args.benchmark_dir, output_dir=args.output_dir)
        arm_a_raw.joinpath(filename).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

        focused, selected_units = build_focused_evidence(event, load_evidence(record), top_n=args.top_n)
        focus_path.write_text(focused, encoding="utf-8")

        llm_model = ""
        try:
            draft_result = call_llm_text(
                args.llm_backend,
                build_draft_prompt(event, focused),
                int(row["seed"]),
                args.qwen_timeout,
            )
            refine_result = call_llm_text(
                args.llm_backend,
                build_refine_prompt(event, focused, draft_result.text),
                int(row["seed"]) + 1009,
                args.qwen_timeout,
            )
            draft, draft_ms, draft_eval, draft_done = (
                draft_result.text,
                draft_result.runtime_ms,
                draft_result.completion_tokens,
                draft_result.done_reason,
            )
            refined, refine_ms, refine_eval, refine_done = (
                refine_result.text,
                refine_result.runtime_ms,
                refine_result.completion_tokens,
                refine_result.done_reason,
            )
            llm_model = refine_result.model
            rule = parse_refined_rule(refined)
            out_record, faithfulness = build_record(record, event, rule, focused)
        except Exception as exc:
            draft = ""
            refined = ""
            draft_ms = refine_ms = draft_eval = refine_eval = 0
            draft_done = refine_done = f"error:{type(exc).__name__}"
            llm_model = ""
            rule = {"answerable": False, "normative_value": "", "evidence": [], "confidence": "", "rejection_reason": str(exc)}
            out_record = {**record, "status": "INVALID_SCHEMA", "m13_rule_refinement": True}
            faithfulness = refine_done

        draft_path.write_text(draft, encoding="utf-8")
        refined_path.write_text(refined, encoding="utf-8")
        out_record["m13_rule_refinement_audit"] = {
            "focused_evidence_file": str(focus_path.relative_to(PROJECT_DIR)),
            "draft_file": str(draft_path.relative_to(PROJECT_DIR)),
            "refined_file": str(refined_path.relative_to(PROJECT_DIR)),
            "refined_rule": rule,
            "faithfulness_status": faithfulness,
            "selected_units": selected_units,
            "llm_backend": args.llm_backend,
            "llm_model": llm_model,
            "draft_attempt_count": draft_result.attempt_count if draft else 0,
            "refine_attempt_count": refine_result.attempt_count if refined else 0,
            "draft_retry_errors": list(draft_result.retry_errors) if draft else [],
            "refine_retry_errors": list(refine_result.retry_errors) if refined else [],
            "candidate_used": False,
            "oracle_used": False,
            "manual_policy_used": False,
        }
        arm_d_raw.joinpath(filename).write_text(json.dumps(out_record, ensure_ascii=False, indent=2), encoding="utf-8")
        rows_out.append(
            {
                "event_id": row["event_id"],
                "run": row["run"],
                "seed": row["seed"],
                "semantic_type": row["semantic_type"],
                "domain": row["domain"],
                "missing_subtype": row["missing_subtype"],
                "extraction_mode": route_task_formulation(event, focused).extraction_mode.value,
                "answerable": rule.get("answerable", False),
                "rule_type": rule.get("rule_type", ""),
                "normative_value": rule.get("normative_value", ""),
                "span_count": len(rule.get("evidence", [])),
                "confidence": rule.get("confidence", ""),
                "faithfulness_status": faithfulness,
                "runtime_ms": draft_ms + refine_ms,
                "eval_count": draft_eval + refine_eval,
                "draft_done": draft_done,
                "refine_done": refine_done,
                "focused_evidence_file": str(focus_path.relative_to(PROJECT_DIR)),
                "draft_file": str(draft_path.relative_to(PROJECT_DIR)),
                "refined_file": str(refined_path.relative_to(PROJECT_DIR)),
            }
        )
        print(
            f"{row['event_id']} run={row['run']} subtype={row['missing_subtype']} "
            f"answerable={rule.get('answerable', False)} faith={faithfulness}",
            flush=True,
        )

    summary_path = args.output_dir / "rule-refinement-summary.csv"
    if args.resume and summary_path.is_file():
        if rows_out:
            existing = {
                (row["event_id"], row.get("run", "")): row
                for row in csv.DictReader(summary_path.open(encoding="utf-8-sig"))
            }
            for row in rows_out:
                existing[(row["event_id"], row.get("run", ""))] = row
            write_csv(summary_path, list(existing.values()))
    elif rows_out:
        write_csv(summary_path, rows_out)
    elif not summary_path.is_file():
        raise SystemExit("no events processed and no existing rule-refinement-summary.csv")
    if not args.skip_ir:
        if not args.main_method_only:
            run_ir(
                arm_a_raw,
                args.output_dir / "arm-a-v4-original" / "ir",
                event_ids=event_ids,
                method_name="V4_Original",
                benchmark_dir=args.benchmark_dir,
            )
        run_ir(
            arm_d_raw,
            args.output_dir / "arm-d-rule-refinement" / "ir",
            event_ids=event_ids,
            method_name="M13_Regulatory_Rule_Refinement",
            benchmark_dir=args.benchmark_dir,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
