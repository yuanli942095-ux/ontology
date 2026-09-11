from __future__ import annotations

"""Run simple candidate-selection baselines on external-real-holdout-v5-blind-large.

Baselines are intentionally weaker than the frozen M16 repair method. They read
only public event metadata, public evidence windows, and the candidate fields
allowed by each method. The private Oracle is loaded only after predictions are
checkpointed.
"""

import argparse
import csv
import json
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from auto_policy_m12_deepseek_client import DeepSeekConfig, call_deepseek_chat
from semantic_v2_common import PROJECT_DIR


DEFAULT_BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v5-blind-large"
DEFAULT_OUTPUT = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v5-blind-large"
    / "baseline-candidate-selection-r5-deepseek"
)
DEFAULT_SEEDS = [20260829, 20260830, 20260831, 20260901, 20260902]
METHODS = ("DIRECT_FREE", "OPTION_VALUE_ONLY", "OPTION_FORMAL_OPERATION")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def normalize_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def load_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def compact_operation(operation_json: str) -> dict[str, Any]:
    raw = json.loads(operation_json)
    return {
        "operator": raw.get("operator", ""),
        "subject_iri": raw.get("subject_iri", ""),
        "predicate_iri": raw.get("predicate_iri", ""),
        "old_value": raw.get("old_value", {}),
        "new_value": raw.get("new_value", {}),
    }


def load_events(benchmark: Path, top_n: int | None) -> list[dict[str, Any]]:
    events = [
        row
        for row in read_csv(benchmark / "public" / "events" / "external-real-event-template.csv")
        if row.get("status") == "READY"
    ]
    events.sort(key=lambda row: row["event_id"])
    if top_n:
        events = events[:top_n]

    docs_by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_csv(benchmark / "public" / "documents" / "external-real-document-template.csv"):
        if row.get("status") == "READY":
            docs_by_event[row["event_id"]].append(row)

    cands_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_csv(benchmark / "repair-stage" / "candidates" / "external-real-candidate-template.csv"):
        if row.get("status") != "READY":
            continue
        cands_by_event[row["event_id"]].append(
            {
                "candidate_id": row["candidate_id"],
                "display_value": row["display_value"],
                "operation": compact_operation(row["operation_json"]),
            }
        )

    result: list[dict[str, Any]] = []
    for event in events:
        event_id = event["event_id"]
        evidence_path = benchmark / "public" / "excerpts" / f"{event_id}-evidence.md"
        result.append(
            {
                **event,
                "documents": docs_by_event[event_id],
                "candidates": cands_by_event[event_id],
                "evidence": evidence_path.read_text(encoding="utf-8-sig"),
            }
        )
    return result


def load_oracle(benchmark: Path) -> dict[str, dict[str, str]]:
    return {
        row["event_id"]: row
        for row in read_csv(benchmark / "private" / "oracle" / "external-real-oracle-template.csv")
    }


def shuffled_candidates(event: dict[str, Any], seed: int) -> list[dict[str, Any]]:
    candidates = [dict(row) for row in event["candidates"]]
    rng = random.Random(f"{event['event_id']}|{seed}|baseline-order")
    rng.shuffle(candidates)
    return candidates


def common_prompt(event: dict[str, Any]) -> str:
    return (
        "You are evaluating a candidate repair for an OWL ontology using only public evidence.\n"
        "Do not use outside knowledge. Do not infer beyond the evidence.\n\n"
        f"event_id: {event['event_id']}\n"
        f"semantic_type: {event['semantic_type']}\n"
        f"domain: {event['domain']}\n"
        f"case_context: {event['case_context']}\n"
        f"subject_label: {event['subject_label']}\n"
        f"predicate_label: {event['predicate_label']}\n\n"
        "PUBLIC EVIDENCE:\n"
        f"{event['evidence']}\n"
    )


def method_prompt(method: str, event: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    base = common_prompt(event)
    if method == "DIRECT_FREE":
        return (
            base
            + "\nReturn JSON only with keys abstain, value, reason. "
            "The value must be the evidence-grounded repair value, not a candidate id."
        )
    if method == "OPTION_VALUE_ONLY":
        options = [
            {"option_id": row["candidate_id"], "display_value": row["display_value"]}
            for row in candidates
        ]
    elif method == "OPTION_FORMAL_OPERATION":
        options = [
            {"option_id": row["candidate_id"], "display_value": row["display_value"], "operation": row["operation"]}
            for row in candidates
        ]
    else:
        raise ValueError(method)
    return (
        base
        + "\nCANDIDATE OPTIONS:\n"
        + json.dumps(options, ensure_ascii=False, indent=2)
        + "\n\nReturn JSON only with keys abstain, option_id, reason. "
        "Select exactly one listed option_id if uniquely supported; otherwise abstain."
    )


def call_baseline(prompt: str, timeout: int) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    config = DeepSeekConfig.from_env()
    config = DeepSeekConfig(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        temperature=0,
        max_tokens=500,
        timeout=timeout,
    )
    result = call_deepseek_chat(prompt, config=config, response_json=True)
    parsed = load_json_object(result.content)
    audit = {
        "backend": "deepseek_api",
        "model": result.model,
        "runtime_ms": result.runtime_ms,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "total_tokens": result.total_tokens,
        "raw_output": result.content,
    }
    return parsed, audit


def interpret(
    method: str,
    parsed: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
) -> tuple[str, str, str]:
    if not isinstance(parsed, dict):
        return "INVALID", "", "not parseable json object"
    if normalize_bool(parsed.get("abstain")):
        return "ABSTAIN", "", str(parsed.get("reason", ""))
    if method == "DIRECT_FREE":
        value = str(parsed.get("value", "")).strip()
        matches = [row for row in candidates if row["display_value"].strip() == value]
        if len(matches) == 1:
            return "SELECTED", matches[0]["candidate_id"], str(parsed.get("reason", ""))
        return "INVALID", "", f"value did not uniquely match a candidate: {value}"
    option_id = str(parsed.get("option_id", "")).strip()
    matches = [row for row in candidates if row["candidate_id"] == option_id]
    if len(matches) == 1:
        return "SELECTED", option_id, str(parsed.get("reason", ""))
    return "INVALID", "", f"option_id did not match listed candidates: {option_id}"


def checkpoint_key(row: dict[str, Any]) -> tuple[str, int, str]:
    return str(row["event_id"]), int(row["run"]), str(row["method"])


def load_checkpoint(path: Path) -> dict[tuple[str, int, str], dict[str, Any]]:
    if not path.is_file():
        return {}
    rows: dict[tuple[str, int, str], dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            row = json.loads(line)
            rows[checkpoint_key(row)] = row
    return rows


def append_checkpoint(path: Path, row: dict[str, Any]) -> None:
    if any(key.startswith("oracle") for key in row):
        raise RuntimeError("refusing to checkpoint Oracle-bearing row")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def attach_oracle(rows: list[dict[str, Any]], oracle: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        oracle_row = oracle[row["event_id"]]
        oracle_id = oracle_row["oracle_candidate_id"]
        selected_id = str(row.get("selected_candidate_id", ""))
        out.append(
            {
                **row,
                "oracle_candidate_id": oracle_id,
                "oracle_correct": row.get("status") == "SELECTED" and selected_id == oracle_id,
            }
        )
    return out


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["method"], "ALL")].append(row)
        groups[(row["method"], row["semantic_type"])].append(row)
    out: list[dict[str, Any]] = []
    for (method, semantic_type), items in sorted(groups.items()):
        event_ids = sorted({row["event_id"] for row in items})
        oracle = sum(1 for row in items if row["oracle_correct"])
        selected = sum(1 for row in items if row["status"] == "SELECTED")
        abstain = sum(1 for row in items if row["status"] == "ABSTAIN")
        invalid = sum(1 for row in items if row["status"] == "INVALID")
        strict = sum(
            1
            for event_id in event_ids
            if all(row["oracle_correct"] for row in items if row["event_id"] == event_id)
        )
        out.append(
            {
                "method": method,
                "semantic_type": semantic_type,
                "events": len(event_ids),
                "attempts": len(items),
                "selected": selected,
                "abstain": abstain,
                "invalid": invalid,
                "oracle_success": oracle,
                "oracle_accuracy": f"{oracle / len(items):.6f}",
                "strict_event_successes": strict,
                "strict_event_accuracy": f"{strict / len(event_ids):.6f}",
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--methods", default=",".join(METHODS))
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--top-n", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    benchmark = args.benchmark_dir.resolve()
    output = args.output_dir.resolve()
    methods = [item.strip().upper() for item in args.methods.split(",") if item.strip()]
    unknown = [item for item in methods if item not in METHODS]
    if unknown:
        raise SystemExit(f"unknown methods: {unknown}")

    events = load_events(benchmark, args.top_n or None)
    seeds = DEFAULT_SEEDS[: args.runs]
    checkpoint = output / "v5-baseline-predictions.jsonl"
    done = load_checkpoint(checkpoint) if args.resume else {}

    predictions: list[dict[str, Any]] = list(done.values())
    for run_index, seed in enumerate(seeds, start=1):
        for event in events:
            candidates = shuffled_candidates(event, seed)
            for method in methods:
                key = (event["event_id"], run_index, method)
                if key in done:
                    continue
                prompt = method_prompt(method, event, candidates)
                try:
                    parsed, audit = call_baseline(prompt, args.timeout)
                    status, selected_id, reason = interpret(method, parsed, candidates)
                except Exception as exc:  # noqa: BLE001 - checkpoint API failures as invalid attempts.
                    parsed = None
                    audit = {"error": type(exc).__name__, "message": str(exc)}
                    status, selected_id, reason = "INVALID", "", str(exc)
                row = {
                    "event_id": event["event_id"],
                    "semantic_type": event["semantic_type"],
                    "domain": event["domain"],
                    "run": run_index,
                    "seed": seed,
                    "method": method,
                    "status": status,
                    "selected_candidate_id": selected_id,
                    "reason": reason,
                    "candidate_order": "|".join(row["candidate_id"] for row in candidates),
                    "parsed_json": parsed,
                    "audit": audit,
                }
                append_checkpoint(checkpoint, row)
                predictions.append(row)
                print(f"{event['event_id']} run={run_index} {method} {status} {selected_id}")

    oracle = load_oracle(benchmark)
    rows = attach_oracle(predictions, oracle)
    details = []
    raw_dir = output / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for row in rows:
        raw_name = f"{row['event_id']}-run{row['run']}-{row['method']}.json"
        (raw_dir / raw_name).write_text(
            json.dumps({"parsed_json": row["parsed_json"], "audit": row["audit"]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        details.append(
            {
                key: value
                for key, value in row.items()
                if key not in {"parsed_json", "audit"}
            }
            | {"raw_file": str(raw_dir / raw_name)}
        )
    summary = summarize(rows)
    write_csv(output / "v5-baseline-details.csv", details)
    write_csv(output / "v5-baseline-summary.csv", summary)
    (output / "v5-baseline-summary.json").write_text(
        json.dumps(
            {
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "benchmark": str(benchmark),
                "events": len(events),
                "runs": len(seeds),
                "methods": methods,
                "status_counts": dict(Counter(row["status"] for row in rows)),
                "summary": summary,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
