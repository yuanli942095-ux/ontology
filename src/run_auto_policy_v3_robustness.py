from __future__ import annotations

"""Run Auto Policy V3 robustness variants on external-real-v1.

The experiment keeps the V3 candidate/Oracle boundary:

* no candidate IDs or candidate values in the prompt
* no private Oracle during generation or candidate selection
* no manual formal-policy input

It stresses whether V3 depends on templated evidence notes or rich event
metadata.  Metadata-light variants blank title, case context, subject label,
and predicate label both in the prompt and in the deterministic normalizer.
"""

import argparse
import csv
import json
import random
import re
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import run_auto_formal_policy_batch_v3 as v3


OUTPUT_DIR = v3.ROOT / "output" / "auto-policy-v3-robustness"
RUNS = 3
SEED_BASE = 20260827

VARIANTS = {
    "ORDER_SHUFFLE_FULL_METADATA": {
        "metadata_mode": "full",
        "evidence_mode": "shuffle_lines",
    },
    "LESS_TEMPLATED_METADATA_LIGHT": {
        "metadata_mode": "light",
        "evidence_mode": "paragraph",
    },
    "DISTRACTOR_METADATA_LIGHT": {
        "metadata_mode": "light",
        "evidence_mode": "paragraph_with_distractors",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto Policy V3 robustness experiment")
    parser.add_argument("--runs", type=int, default=RUNS)
    parser.add_argument("--seed", type=int, default=SEED_BASE)
    parser.add_argument("--variants", default=",".join(VARIANTS))
    parser.add_argument("--prefix", default="auto-policy-v3-robustness-r3-seed20260827")
    parser.add_argument("--skip-generation", action="store_true")
    parser.add_argument("--skip-evaluation", action="store_true")
    parser.add_argument("--skip-repair", action="store_true")
    return parser.parse_args()


def event_ids() -> list[str]:
    return [f"EXT_E{i:03d}" for i in range(1, 31)]


def metadata_context(event: dict[str, str], mode: str) -> dict[str, str]:
    result = dict(event)
    if mode == "light":
        for key in ("title", "case_context", "subject_label", "predicate_label"):
            result[key] = ""
    return result


def strip_markdown_noise(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        line = re.sub(r"^#+\s*", "", line)
        line = re.sub(r"^[-*]\s*", "", line)
        line = line.replace("`", "")
        if line.lower() in {"evidence summary:", "evidence summary", "status:", "status"}:
            continue
        if "fixed before private oracle" in line.lower():
            continue
        lines.append(line.rstrip(".。"))
    return lines


def paragraphize(clean_evidence: str) -> str:
    fragments = strip_markdown_noise(clean_evidence)
    rewritten: list[str] = []
    for fragment in fragments:
        lowered = fragment.lower()
        if lowered.startswith("source family:"):
            rewritten.append("The relevant source family is " + fragment.split(":", 1)[1].strip())
        elif lowered.startswith("event type:"):
            rewritten.append("The change should be treated as " + fragment.split(":", 1)[1].strip())
        elif lowered.startswith("target subject:"):
            rewritten.append("The affected normative object is " + fragment.split(":", 1)[1].strip())
        elif lowered.startswith("target predicate:"):
            rewritten.append("The affected property is " + fragment.split(":", 1)[1].strip())
        else:
            rewritten.append(fragment)
    return " ".join(item.strip() + "." for item in rewritten if item.strip())


DISTRACTORS = [
    "Background note: a different accessibility criterion may use conformance level AAA, but this note is not the target evidence.",
    "Background note: an older identity document may defer a topic to a future revision; this does not override the cited current source.",
    "Background note: insurance examples sometimes keep an undivided amount, but the current event must follow the cited revision evidence.",
]


def evidence_variant(clean_evidence: str, mode: str, event_id: str, seed: int) -> str:
    if mode == "shuffle_lines":
        lines = [line for line in clean_evidence.splitlines() if line.strip()]
        rng = random.Random(f"{event_id}-{seed}-shuffle")
        headings = [line for line in lines if line.lstrip().startswith("#")]
        rest = [line for line in lines if not line.lstrip().startswith("#")]
        rng.shuffle(rest)
        return "\n".join(headings + rest)
    if mode == "paragraph":
        return paragraphize(clean_evidence)
    if mode == "paragraph_with_distractors":
        rng = random.Random(f"{event_id}-{seed}-distractor")
        distractors = list(DISTRACTORS)
        rng.shuffle(distractors)
        return paragraphize(clean_evidence) + " " + " ".join(distractors[:2])
    raise ValueError(f"unknown evidence mode: {mode}")


def build_prompt(event_row: dict[str, str], evidence: str) -> str:
    semantic_type = event_row["semantic_type"].strip()
    schema = v3.schema_for_semantic_type(semantic_type)
    return f"""
你是一名本体工程、规范文档语义演化和神经符号推理助手。

你的任务是：仅依据公开规范文档证据，自动生成可供后续本体修复系统使用的 canonical formal policy。

本阶段是 Policy Construction，不是 Candidate Selection。

你不能使用 private Oracle、Gold Answer、候选编号、候选值、人工 formal-policy.json，不能根据候选集合反推答案。

你只能依据当前事件公开元数据和当前事件 candidate-blind public evidence。

关键要求：

- canonical_result 必须是机器可规范化对象；
- semantic_result 必须是 canonical_result 的短字符串表达；
- 如果是 WCAG，必须抽取 criterion_id、level、revision，并选择合适 family；
- 如果是 NIST，必须抽取 Revision 4 已经整合的 change_key 和 change_value；
- 如果是保险条款，必须抽取金额、分级阈值或公式要素；
- 不得编造公开证据没有支持的规范事实；
- 只输出合法 JSON，不输出 Markdown，不输出解释文字。

事件元数据：

event_id:
{event_row.get("event_id", "").strip()}

domain:
{event_row.get("domain", "").strip()}

semantic_type:
{semantic_type}

title:
{event_row.get("title", "").strip()}

case_context:
{event_row.get("case_context", "").strip()}

subject_label:
{event_row.get("subject_label", "").strip()}

predicate_label:
{event_row.get("predicate_label", "").strip()}

对应的类型化 Policy Schema：

{schema}

如果核心 canonical_result 确实无法根据公开证据判断，才输出：

{{
  "abstain": true,
  "reason": "公开证据不足以确定核心语义结果"
}}

===== CANDIDATE-BLIND PUBLIC EVIDENCE BEGIN =====

{evidence}

===== CANDIDATE-BLIND PUBLIC EVIDENCE END =====
""".strip()


def save_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_generation_outputs(
    variant: str,
    variant_dir: Path,
    records: list[dict[str, Any]],
    runs: int,
) -> None:
    details = variant_dir / f"{variant.lower()}-generation-details.csv"
    by_event_path = variant_dir / f"{variant.lower()}-generation-by-event.csv"
    summary_path = variant_dir / f"{variant.lower()}-generation-summary.json"
    save_csv(details, records)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["event_id"]].append(record)
    by_event: list[dict[str, Any]] = []
    for event_id, items in sorted(grouped.items()):
        generated = sum(row["status"] == "GENERATED" for row in items)
        by_event.append(
            {
                "event_id": event_id,
                "semantic_type": items[0]["semantic_type"],
                "attempts": len(items),
                "generated": generated,
                "generated_rate": generated / len(items) if items else 0,
                "strict_all_runs_generated": generated == runs,
                "invalid_schema": sum(row["status"] == "INVALID_SCHEMA" for row in items),
                "canonical_unresolved": sum(row["canonical_status"] != "ok" for row in items),
                "forbidden_outputs": sum(int(row["forbidden_marker_count"]) > 0 for row in items),
            }
        )
    save_csv(by_event_path, by_event)

    status_counts = Counter(row["status"] for row in records)
    summary = {
        "experiment": f"AUTO_POLICY_V3_ROBUSTNESS_{variant}",
        "variant": variant,
        "events": len(grouped),
        "runs": runs,
        "attempts": len(records),
        "generated": status_counts.get("GENERATED", 0),
        "generated_rate": status_counts.get("GENERATED", 0) / len(records) if records else 0,
        "invalid_schema": status_counts.get("INVALID_SCHEMA", 0),
        "abstains": status_counts.get("ABSTAIN", 0),
        "errors": status_counts.get("ERROR", 0),
        "canonical_unresolved": sum(row["canonical_status"] != "ok" for row in records),
        "forbidden_output": sum(int(row["forbidden_marker_count"]) > 0 for row in records),
        "average_runtime_ms": sum(int(row["runtime_ms"]) for row in records) / len(records) if records else 0,
        "candidate_blind": True,
        "oracle_used": False,
        "candidate_used": False,
        "manual_formal_policy_used": False,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def generate_variant(
    variant: str,
    settings: dict[str, str],
    events: dict[str, dict[str, str]],
    runs: int,
    seed_base: int,
) -> None:
    variant_dir = OUTPUT_DIR / variant.lower()
    raw_dir = variant_dir / "raw"
    evidence_dir = variant_dir / "candidate-blind-evidence"
    raw_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    total = len(event_ids()) * runs
    index = 0
    for event_id in event_ids():
        original_event = events[event_id]
        event_for_prompt = metadata_context(original_event, settings["metadata_mode"])
        semantic_type = original_event["semantic_type"].strip()
        evidence_path = v3.EXCERPT_DIR / f"{event_id}-evidence.md"
        clean_evidence = v3.remove_candidate_sections(evidence_path.read_text(encoding="utf-8"))
        remaining = v3.find_candidate_markers(clean_evidence)
        if remaining:
            raise RuntimeError(f"{event_id}: candidate marker remained: {remaining}")

        for run in range(1, runs + 1):
            seed = seed_base + run - 1
            index += 1
            evidence = evidence_variant(clean_evidence, settings["evidence_mode"], event_id, seed)
            evidence_file = evidence_dir / f"{event_id}-run{run}-seed{seed}-candidate-blind.md"
            evidence_file.write_text(evidence, encoding="utf-8")
            prompt = build_prompt(event_for_prompt, evidence)
            raw_path = raw_dir / f"{event_id}-run{run}-seed{seed}.json"
            print(f"[{variant} {index}/{total}] {event_id} run={run} seed={seed}")

            status = "ERROR"
            runtime_ms = 0
            prompt_eval_count = 0
            eval_count = 0
            forbidden_markers: list[str] = []
            schema_valid = False
            validation_reason = "not_run"
            canonical_status = "not_run"
            canonical_result: dict[str, Any] | None = None
            canonical_semantic_result = ""
            parsed: Any = None
            qwen_response: dict[str, Any] = {}
            try:
                qwen_response, runtime_ms = v3.call_qwen(prompt, seed)
                response_text = str(qwen_response.get("response", ""))
                prompt_eval_count = int(qwen_response.get("prompt_eval_count", 0) or 0)
                eval_count = int(qwen_response.get("eval_count", 0) or 0)
                forbidden_markers = v3.forbidden_output_markers(response_text)
                parsed = v3.extract_json(response_text)
                parsed, canonical_status, canonical_result, canonical_semantic_result = v3.normalize_generated_policy(
                    parsed,
                    event_for_prompt,
                    evidence,
                )
                schema_valid, validation_reason = v3.validate_generated_policy(parsed, semantic_type)
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
            except Exception as exc:
                parsed = {"error": repr(exc)}
                validation_reason = "exception"
                canonical_status = "exception"
                status = "ERROR"

            raw_record = {
                "event_id": event_id,
                "semantic_type": semantic_type,
                "run": run,
                "seed": seed,
                "variant": variant,
                "metadata_mode": settings["metadata_mode"],
                "evidence_mode": settings["evidence_mode"],
                "model": v3.MODEL,
                "prompt_version": f"AUTO_POLICY_V3_ROBUSTNESS_{variant}",
                "source_type": "CANDIDATE_BLIND_PUBLIC_EVIDENCE",
                "source_file": str(evidence_path.relative_to(v3.ROOT)),
                "candidate_blind_file": str(evidence_file.relative_to(v3.ROOT)),
                "oracle_used": False,
                "candidate_used": False,
                "manual_formal_policy_used": False,
                "status": status,
                "runtime_ms": runtime_ms,
                "done_reason": qwen_response.get("done_reason", ""),
                "prompt_eval_count": prompt_eval_count,
                "eval_count": eval_count,
                "forbidden_markers": forbidden_markers,
                "schema_valid": schema_valid,
                "validation_reason": validation_reason,
                "canonical_status": canonical_status,
                "canonical_result": canonical_result,
                "canonical_semantic_result": canonical_semantic_result,
                "response": parsed,
            }
            raw_path.write_text(json.dumps(raw_record, ensure_ascii=False, indent=2), encoding="utf-8")
            records.append(
                {
                    "event_id": event_id,
                    "semantic_type": semantic_type,
                    "run": run,
                    "seed": seed,
                    "variant": variant,
                    "metadata_mode": settings["metadata_mode"],
                    "evidence_mode": settings["evidence_mode"],
                    "status": status,
                    "runtime_ms": runtime_ms,
                    "prompt_eval_count": prompt_eval_count,
                    "eval_count": eval_count,
                    "forbidden_marker_count": len(forbidden_markers),
                    "forbidden_markers": "|".join(forbidden_markers),
                    "schema_valid": schema_valid,
                    "validation_reason": validation_reason,
                    "canonical_status": canonical_status,
                    "canonical_semantic_result": canonical_semantic_result,
                    "raw_output_file": str(raw_path.relative_to(v3.ROOT)),
                }
            )
    write_generation_outputs(variant, variant_dir, records, runs)


def run_command(args: list[str]) -> None:
    print(" ".join(args))
    subprocess.run(args, cwd=v3.ROOT, check=True)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON root must be object: {path}")
    return value


def evaluate_variants(variants: list[str], skip_repair: bool) -> None:
    python = str(v3.ROOT / ".venv" / "Scripts" / "python.exe")
    for variant in variants:
        variant_dir = OUTPUT_DIR / variant.lower()
        raw_dir = variant_dir / "raw"
        semantic_prefix = f"auto-policy-v3-robustness-{variant.lower()}-semantic-evaluation"
        repair_prefix = f"auto-policy-v3-robustness-{variant.lower()}-candidate-repair-r3-seed20260827"
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
                "3",
                "--seed",
                str(SEED_BASE),
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
                    "3",
                    "--seed",
                    str(SEED_BASE),
                ]
            )


def semantic_summary(path: Path) -> dict[str, Any]:
    return load_json(path)["summary"][0]


def repair_summary(path: Path) -> dict[str, str]:
    return read_csv(path)[0]


def build_summary(prefix: str, variants: list[str], skip_repair: bool) -> None:
    rows: list[dict[str, Any]] = []
    for variant in variants:
        settings = VARIANTS[variant]
        variant_dir = OUTPUT_DIR / variant.lower()
        semantic = semantic_summary(
            variant_dir / f"auto-policy-v3-robustness-{variant.lower()}-semantic-evaluation-summary.json"
        )
        repair = {} if skip_repair else repair_summary(
            v3.ROOT / "output" / f"auto-policy-v3-robustness-{variant.lower()}-candidate-repair-r3-seed20260827-summary.csv"
        )
        rows.append(
            {
                "variant": variant,
                "metadata_mode": settings["metadata_mode"],
                "evidence_mode": settings["evidence_mode"],
                "events": semantic["events"],
                "attempts": semantic["attempts"],
                "semantic_correct": semantic["semantic_correct"],
                "semantic_accuracy": semantic["semantic_accuracy"],
                "semantic_strict_event_successes": semantic["strict_event_successes"],
                "semantic_strict_event_accuracy": semantic["strict_event_accuracy"],
                "generation_successes": semantic["generation_successes"],
                "selected": repair.get("selected", ""),
                "oracle_correct": repair.get("oracle_correct", ""),
                "oracle_accuracy": repair.get("oracle_accuracy", ""),
                "full_closure_success": repair.get("full_closure_success", ""),
                "full_closure_accuracy": repair.get("full_closure_accuracy", ""),
                "repair_strict_event_successes": repair.get("strict_event_successes", ""),
                "repair_strict_event_accuracy": repair.get("strict_event_accuracy", ""),
                "abstains": repair.get("abstains", ""),
            }
        )
    summary_csv = v3.ROOT / "output" / f"{prefix}-summary.csv"
    summary_json = v3.ROOT / "output" / f"{prefix}.json"
    save_csv(summary_csv, rows)
    summary_json.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "runs": 3,
                "seed_base": SEED_BASE,
                "summary_csv": str(summary_csv),
                "rows": rows,
                "boundary": (
                    "Metadata-light variants blank title, case_context, subject_label, and "
                    "predicate_label in both prompt and normalizer. Candidate and Oracle "
                    "information remain unavailable during generation and selection."
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"summary={summary_csv}")
    for row in rows:
        print(
            f"{row['variant']}: semantic={float(row['semantic_accuracy']):.2%} "
            f"strict={row['semantic_strict_event_successes']}/{row['events']} "
            f"closure={row['full_closure_accuracy']} abstains={row['abstains']}"
        )


def main() -> int:
    args = parse_args()
    variants = [item.strip() for item in args.variants.split(",") if item.strip()]
    for variant in variants:
        if variant not in VARIANTS:
            raise ValueError(f"unknown variant: {variant}")
    events = v3.load_events()
    if not args.skip_generation:
        for variant in variants:
            generate_variant(variant, VARIANTS[variant], events, args.runs, args.seed)
    if not args.skip_evaluation:
        evaluate_variants(variants, args.skip_repair)
    build_summary(args.prefix, variants, args.skip_repair)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
