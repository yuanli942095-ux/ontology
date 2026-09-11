from __future__ import annotations

"""Build a small traceable diagnostic pack for AUTO_POLICY_V3 failures.

The pack joins generation details, raw model records, candidate repair decisions,
retrieval provenance, public candidates, private Oracle, and manual formal policy.
Private Oracle and manual policy are diagnostic-only outputs; they are never used
to change the automatic method.
"""

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
BENCHMARK_DIR = PROJECT_DIR / "benchmark" / "external-real-v8-grounded"
RUN_DIR = PROJECT_DIR / "output" / "external-real-v8-grounded" / "preformal-r3"
REPAIR_DETAILS = PROJECT_DIR / "output" / "auto-policy-v3-natural-evidence-raw_window_metadata_light-candidate-repair-r3-seed20260827-details.csv"
VARIANT_DIR = RUN_DIR / "raw_window_metadata_light"
GENERATION_DETAILS = VARIANT_DIR / "raw_window_metadata_light-generation-details.csv"
SEMANTIC_DETAILS = VARIANT_DIR / "auto-policy-v3-natural-evidence-raw_window_metadata_light-semantic-evaluation-details.csv"
OUTPUT_DIR = PROJECT_DIR / "output" / "auto-policy-v3-trace-diagnostic-pack"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON root must be object: {path}")
    return value


def parse_jsonish(value: str) -> Any:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def rel(path: Path | str) -> str:
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_DIR / p
    try:
        return str(p.resolve().relative_to(PROJECT_DIR))
    except ValueError:
        return str(p.resolve())


def abs_path(path: Path | str) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_DIR / p
    return p


def compact_json(value: Any, limit: int = 1600) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, indent=2)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated]"


def read_text_excerpt(path: Path, limit: int = 2400) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8-sig", errors="replace").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated]"


def index_by_attempt(rows: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
    return {(row["event_id"], str(row["run"])): row for row in rows}


def load_candidates() -> dict[str, list[dict[str, Any]]]:
    path = BENCHMARK_DIR / "repair-stage" / "candidates" / "external-real-candidate-template.csv"
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_csv(path):
        if str(row.get("status", "")).upper() != "READY":
            continue
        item = dict(row)
        item["operation"] = parse_jsonish(row.get("operation_json", ""))
        grouped[row["event_id"]].append(item)
    return grouped


def survivor_count(row: dict[str, str]) -> int:
    evidence = parse_jsonish(row.get("candidate_match_evidence", ""))
    if not isinstance(evidence, list):
        return 0
    return sum(bool(item.get("match")) for item in evidence if isinstance(item, dict))


def match_family(row: dict[str, str]) -> str:
    evidence = parse_jsonish(row.get("candidate_match_evidence", ""))
    if not isinstance(evidence, list):
        return ""
    families = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        info = item.get("evidence")
        if isinstance(info, dict) and info.get("family"):
            families.append(str(info.get("family")))
    return "|".join(sorted(set(families)))


def classify_bucket(
    repair: dict[str, str],
    generation: dict[str, str] | None,
    semantic: dict[str, str] | None,
) -> str:
    gen_status = str(repair.get("generation_status") or generation.get("status", "") if generation else "").upper()
    selection = str(repair.get("selection_status", "")).upper()
    correct = str(repair.get("selection_oracle_correct", "")).lower() == "true"
    selected = str(repair.get("selected_candidate_id", "")).strip()
    canonical_status = str(generation.get("canonical_status", "") if generation else "").strip().lower()
    semantic_correct = str(semantic.get("semantic_correct", "") if semantic else "").lower() == "true"
    raw_auto = str(repair.get("auto_semantic_result", "")).strip()
    canon_auto = str(generation.get("canonical_semantic_result", "") if generation else "").strip()

    if gen_status == "RETRIEVAL_FAILED":
        return "retrieval_failure"
    if gen_status in {"INVALID_SCHEMA", "INVALID_JSON"}:
        return "invalid"
    if gen_status not in {"GENERATED", "OK", ""}:
        return "invalid"
    if correct and selected:
        return "correct_select"
    if selection == "ABSTAIN":
        if canonical_status and canonical_status != "ok":
            return "canonicalization_failure"
        if semantic_correct and (not selected):
            return "over_abstention"
        return "abstain"
    if selected and not correct:
        if canonical_status and canonical_status != "ok":
            return "canonicalization_failure"
        if semantic_correct:
            return "candidate_mapping_failure"
        if canon_auto and raw_auto and canon_auto != raw_auto:
            return "canonicalization_failure"
        return "wrong_candidate"
    if canonical_status and canonical_status != "ok":
        return "canonicalization_failure"
    return "invalid"


def classify_failure_layer(
    repair: dict[str, str],
    generation: dict[str, str] | None,
    semantic: dict[str, str] | None,
    retrieval: dict[str, str] | None,
) -> str:
    gen_status = str(repair.get("generation_status", "")).upper()
    if gen_status == "RETRIEVAL_FAILED":
        return "Retrieval Failure"
    if retrieval and retrieval.get("retrieval_status") != "RETRIEVAL_READY":
        return "Retrieval Failure"
    if gen_status in {"INVALID_SCHEMA", "INVALID_JSON"}:
        return "Invalid Output"
    if generation and str(generation.get("schema_valid", "")).lower() == "false":
        return "Invalid Output"
    if generation and str(generation.get("canonical_status", "")).lower() not in {"", "ok"}:
        return "Canonicalization Failure"
    if semantic and str(semantic.get("semantic_correct", "")).lower() != "true":
        return "Semantic Extraction Failure"
    if repair.get("selection_status") == "ABSTAIN":
        return "Over-abstention"
    if repair.get("selected_candidate_id") and str(repair.get("selection_oracle_correct", "")).lower() != "true":
        return "Candidate Mapping Failure"
    if gen_status != "GENERATED":
        return "Invalid Output"
    return "Correct / No Failure"


def choose_samples(rows: list[dict[str, Any]], per_bucket: int) -> list[dict[str, Any]]:
    bucket_order = [
        "correct_select",
        "retrieval_failure",
        "abstain",
        "canonicalization_failure",
        "wrong_candidate",
        "candidate_mapping_failure",
        "over_abstention",
        "invalid",
    ]
    chosen: list[dict[str, Any]] = []
    used_attempts: set[tuple[str, str]] = set()
    used_events: set[str] = set()

    def add(row: dict[str, Any]) -> bool:
        key = (row["event_id"], str(row["run"]))
        if key in used_attempts:
            return False
        chosen.append(row)
        used_attempts.add(key)
        used_events.add(row["event_id"])
        return True

    for bucket in bucket_order:
        candidates = [row for row in rows if row["diagnostic_bucket"] == bucket]
        type_counts: Counter[str] = Counter()
        added = 0
        for row in sorted(candidates, key=lambda item: (item["semantic_type"], item["event_id"], int(item["run"]))):
            if added >= per_bucket:
                break
            if row["event_id"] in used_events and len(candidates) > per_bucket:
                continue
            if type_counts[row["semantic_type"]] >= 2 and len({r["semantic_type"] for r in candidates}) > 1:
                continue
            if add(row):
                type_counts[row["semantic_type"]] += 1
                added += 1
        if added < per_bucket:
            for row in sorted(candidates, key=lambda item: (item["event_id"], int(item["run"]))):
                if added >= per_bucket:
                    break
                if add(row):
                    added += 1
    return chosen


def build_rows(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    generation_rows = index_by_attempt(read_csv(args.generation_details))
    semantic_rows = index_by_attempt(read_csv(args.semantic_details))
    retrievals = {row["event_id"]: row for row in read_csv(BENCHMARK_DIR / "public" / "retrieval" / "external-real-v8-event-retrieval.csv")}
    events = {row["event_id"]: row for row in read_csv(BENCHMARK_DIR / "public" / "events" / "external-real-event-template.csv")}
    oracles = {row["event_id"]: row for row in read_csv(BENCHMARK_DIR / "private" / "oracle" / "external-real-oracle-template.csv")}
    candidates = load_candidates()

    rows: list[dict[str, Any]] = []
    for repair in read_csv(args.repair_details):
        key = (repair["event_id"], str(repair["run"]))
        generation = generation_rows.get(key)
        semantic = semantic_rows.get(key)
        retrieval = retrievals.get(repair["event_id"])
        event = events.get(repair["event_id"], {})
        raw_file = generation.get("raw_output_file", "") if generation else ""
        raw_record = load_json(abs_path(raw_file)) if raw_file else {}
        response = raw_record.get("response", {}) if isinstance(raw_record, dict) else {}
        raw_semantic_result = ""
        if isinstance(response, dict):
            rules = response.get("rules", [])
            if isinstance(rules, list) and rules:
                top = max(rules, key=lambda item: int(item.get("priority", 0)) if isinstance(item, dict) else 0)
                if isinstance(top, dict):
                    raw_semantic_result = str(top.get("semantic_result", ""))
        bucket_probe = {
            **repair,
            "diagnostic_bucket": "",
        }
        bucket = classify_bucket(bucket_probe, generation, semantic)
        rows.append(
            {
                "event_id": repair["event_id"],
                "semantic_type": repair.get("semantic_type", ""),
                "domain": event.get("domain", ""),
                "run": repair.get("run", ""),
                "seed": repair.get("seed", ""),
                "generation_status": repair.get("generation_status", ""),
                "selection_status": repair.get("selection_status", ""),
                "selection_oracle_correct": repair.get("selection_oracle_correct", ""),
                "diagnostic_bucket": bucket,
                "failure_layer_hypothesis": classify_failure_layer(repair, generation, semantic, retrieval),
                "selected_candidate_id": repair.get("selected_candidate_id", ""),
                "selected_value": repair.get("selected_value", ""),
                "oracle_candidate_id": oracles.get(repair["event_id"], {}).get("oracle_candidate_id", ""),
                "oracle_value": oracles.get(repair["event_id"], {}).get("oracle_value", ""),
                "public_retrieval_status": retrieval.get("retrieval_status", "") if retrieval else "",
                "model_input_status": repair.get("generation_status", ""),
                "fallback_used": retrieval.get("fallback_used", "") if retrieval else "",
                "raw_source_used": generation.get("raw_source_used", "") if generation else "",
                "retrieved_doc_count": generation.get("retrieved_doc_count", "") if generation else "",
                "retrieved_window_count": generation.get("retrieved_window_count", "") if generation else "",
                "canonical_status": generation.get("canonical_status", "") if generation else "",
                "raw_semantic_result": raw_semantic_result,
                "canonical_semantic_result": generation.get("canonical_semantic_result", "") if generation else "",
                "auto_semantic_result_used_for_repair": repair.get("auto_semantic_result", ""),
                "semantic_correct": semantic.get("semantic_correct", "") if semantic else "",
                "semantic_evidence": semantic.get("semantic_evidence", "") if semantic else "",
                "survivor_count": survivor_count(repair),
                "decision_path": "UNIQUE_SELECT" if repair.get("selection_status") == "SELECTED" else "FAIL_CLOSED_ABSTAIN",
                "selection_reason": repair.get("selection_reason", ""),
                "candidate_match_family": match_family(repair),
                "candidate_match_evidence": repair.get("candidate_match_evidence", ""),
                "raw_output_file": rel(raw_file) if raw_file else "",
                "candidate_blind_file": rel(raw_record.get("candidate_blind_file", "")) if raw_record else "",
                "manual_formal_policy_file": rel(BENCHMARK_DIR / "rules" / f"{repair['event_id']}-formal-policy.json"),
                "event_title": event.get("title", ""),
                "case_context": event.get("case_context", ""),
                "source_urls": retrieval.get("current_url", "") + "|" + retrieval.get("previous_url", "") if retrieval else "",
                "candidates_json": compact_json(candidates.get(repair["event_id"], []), limit=2200),
            }
        )
    samples = choose_samples(rows, args.per_bucket)
    return rows, samples


def write_markdown(samples: list[dict[str, Any]]) -> None:
    trace_dir = OUTPUT_DIR / "trace-markdown"
    trace_dir.mkdir(parents=True, exist_ok=True)
    resolved = trace_dir.resolve()
    expected = (OUTPUT_DIR / "trace-markdown").resolve()
    if resolved != expected:
        raise RuntimeError(f"unexpected trace output directory: {resolved}")
    for old_file in trace_dir.glob("*.md"):
        old_file.unlink()
    for row in samples:
        raw_record = load_json(abs_path(row["raw_output_file"])) if row.get("raw_output_file") else {}
        candidate_blind = read_text_excerpt(abs_path(row["candidate_blind_file"]), limit=5000) if row.get("candidate_blind_file") else ""
        manual_policy_path = abs_path(row["manual_formal_policy_file"])
        manual_policy = load_json(manual_policy_path) if manual_policy_path.is_file() else {}
        content = [
            f"# {row['event_id']} run {row['run']} trace",
            "",
            "## Summary",
            "",
            f"- bucket: `{row['diagnostic_bucket']}`",
            f"- failure_layer_hypothesis: `{row['failure_layer_hypothesis']}`",
            f"- semantic_type: `{row['semantic_type']}`",
            f"- domain: `{row['domain']}`",
            f"- public_retrieval_status: `{row['public_retrieval_status']}`",
            f"- model_input_status: `{row['model_input_status']}`",
            f"- fallback_used: `{row['fallback_used']}`",
            f"- selection_status: `{row['selection_status']}`",
            f"- survivor_count: `{row['survivor_count']}`",
            f"- decision_path: `{row['decision_path']}`",
            f"- selected_candidate_id: `{row['selected_candidate_id']}`",
            f"- oracle_candidate_id: `{row['oracle_candidate_id']}`",
            "",
            "## Event",
            "",
            f"- title: {row['event_title']}",
            f"- context: {row['case_context']}",
            f"- source_urls: {row['source_urls']}",
            "",
            "## Actual Model Input",
            "",
            "```markdown",
            candidate_blind,
            "```",
            "",
            "## Auto Policy Raw Output",
            "",
            "```json",
            compact_json(raw_record.get("response", raw_record), limit=6000),
            "```",
            "",
            "## Normalization",
            "",
            f"- canonical_status: `{row['canonical_status']}`",
            f"- raw_semantic_result: `{row['raw_semantic_result']}`",
            f"- canonical_semantic_result: `{row['canonical_semantic_result']}`",
            f"- repair_used_semantic_result: `{row['auto_semantic_result_used_for_repair']}`",
            f"- semantic_correct: `{row['semantic_correct']}`",
            f"- semantic_evidence: `{row['semantic_evidence']}`",
            "",
            "## Candidates And Matching",
            "",
            "```json",
            row["candidates_json"],
            "```",
            "",
            "```json",
            compact_json(parse_jsonish(row["candidate_match_evidence"]), limit=6000),
            "```",
            "",
            "## Manual Formal Policy Diagnostic Only",
            "",
            "```json",
            compact_json(manual_policy, limit=6000),
            "```",
        ]
        path = trace_dir / f"{row['event_id']}-run{row['run']}-{row['diagnostic_bucket']}.md"
        path.write_text("\n".join(content), encoding="utf-8")


def write_report(rows: list[dict[str, Any]], samples: list[dict[str, Any]]) -> None:
    bucket_counts = Counter(row["diagnostic_bucket"] for row in rows)
    layer_counts = Counter(row["failure_layer_hypothesis"] for row in rows)
    by_type_layer: dict[tuple[str, str], int] = Counter((row["semantic_type"], row["failure_layer_hypothesis"]) for row in rows)
    lines = [
        "# AUTO_POLICY_V3 trace diagnostic pack",
        "",
        "This pack is for error localization only. Private Oracle and manual formal policy are included only in diagnostic artifacts, not in automatic-method inputs.",
        "",
        "## Overall pre-experiment pattern",
        "",
        f"- attempts: {len(rows)}",
        f"- sampled traces: {len(samples)}",
        "",
        "### Buckets",
        "",
    ]
    for key, count in bucket_counts.most_common():
        lines.append(f"- {key}: {count}")
    lines += ["", "### Failure-layer hypotheses", ""]
    for key, count in layer_counts.most_common():
        lines.append(f"- {key}: {count}")
    lines += ["", "### Semantic type x layer", ""]
    for (semantic_type, layer), count in sorted(by_type_layer.items()):
        lines.append(f"- {semantic_type} / {layer}: {count}")
    lines += [
        "",
        "## Sampled event attempts",
        "",
        "| event_id | run | semantic_type | domain | bucket | layer | public_retrieval | model_input | selection | survivor_count |",
        "|---|---:|---|---|---|---|---|---|---|---:|",
    ]
    for row in samples:
        lines.append(
            f"| {row['event_id']} | {row['run']} | {row['semantic_type']} | {row['domain']} | "
            f"{row['diagnostic_bucket']} | {row['failure_layer_hypothesis']} | {row['public_retrieval_status']} | "
            f"{row['model_input_status']} | {row['selection_status']} | {row['survivor_count']} |"
        )
    lines += [
        "",
        "## First diagnosis",
        "",
        "- The dominant failure shape is fail-closed ABSTAIN after a generated Auto Policy result, not raw retrieval failure.",
        "- Many failures have `canonical_result_unresolved` while the raw output contains a domain-specific JSON object. This points first to canonicalization/schema-adapter coverage.",
        "- Where semantic evaluation is false, the raw extracted result should be compared with the manual policy to decide between semantic extraction and retrieval-window insufficiency.",
        "- Candidate mapping is a separate risk when the canonical semantic family is right but the generated value serialization does not match candidate display values.",
    ]
    (OUTPUT_DIR / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build AUTO_POLICY_V3 trace diagnostics")
    parser.add_argument("--generation-details", type=Path, default=GENERATION_DETAILS)
    parser.add_argument("--semantic-details", type=Path, default=SEMANTIC_DETAILS)
    parser.add_argument("--repair-details", type=Path, default=REPAIR_DETAILS)
    parser.add_argument("--per-bucket", type=int, default=5)
    args = parser.parse_args()

    rows, samples = build_rows(args)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(OUTPUT_DIR / "all-attempt-layer-diagnosis.csv", rows)
    write_csv(OUTPUT_DIR / "sample-trace-index.csv", samples)
    write_markdown(samples)
    write_report(rows, samples)
    print(f"rows={len(rows)} samples={len(samples)}")
    print(f"output={OUTPUT_DIR}")
    print("bucket_counts=" + json.dumps(Counter(row["diagnostic_bucket"] for row in rows), ensure_ascii=False, sort_keys=True))
    print("layer_counts=" + json.dumps(Counter(row["failure_layer_hypothesis"] for row in rows), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
