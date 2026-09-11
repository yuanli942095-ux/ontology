from __future__ import annotations

"""Build failure-case analysis tables for Auto Policy V3 experiments."""

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(r"G:\LearnAI\ontology-evolution")
OUT = ROOT / "output"
INPUT = ROOT / "benchmark" / "external-real-v1" / "input"
PREFIX = "auto-policy-v3-failure-case-analysis"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def pct(k: int, n: int) -> str:
    return "n/a" if n == 0 else f"{k / n * 100:.2f}%"


def bool_value(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def event_metadata() -> dict[str, dict[str, str]]:
    return {
        row["event_id"]: row
        for row in read_csv(INPUT / "external-real-event-template.csv")
        if row.get("status") == "READY"
    }


def compact_values(values: list[str], max_items: int = 3) -> str:
    cleaned = [value for value in values if value]
    unique = []
    for value in cleaned:
        if value not in unique:
            unique.append(value)
    if len(unique) <= max_items:
        return " | ".join(unique)
    return " | ".join(unique[:max_items]) + f" | ... (+{len(unique) - max_items})"


def failure_mode(row: dict[str, str], source: str) -> str:
    semantic_type = row.get("semantic_type", "")
    event_id = row.get("event_id", "")
    title = row.get("title", "")
    domain = row.get("domain", "")
    variant = row.get("variant", "")

    if source == "gate_residual":
        return (
            "negative construction left a normal-looking formula signal; "
            "candidate-blind gate had no missing/conflict/distractor marker"
        )
    if source == "negative":
        return {
            "CONFLICTING_EVIDENCE": "generation/normalizer selected despite explicit conflicting evidence",
            "DISTRACTOR_DOMINATES": "dominant near-miss evidence overrode fail-closed behavior",
            "MISSING_KEY_FIELD": "key evidence removed, but residual canonical signal still matched a candidate",
            "NO_MATCHING_CANDIDATE": "negative mutation intended no candidate match, but residual text still normalized to a candidate",
            "MULTIPLE_MATCHING_CANDIDATES": "multi-survivor ambiguity; expected selector abstain",
        }.get(variant, "negative variant produced unsafe selection")
    if domain == "web_accessibility" and semantic_type == "TEMPORAL_VERSION":
        return "metadata-light WCAG evidence allowed near-miss version/status/level distractors to dominate"
    if domain == "web_accessibility" and semantic_type == "CROSS_SENTENCE_SCOPE":
        return "scope-sensitive WCAG evidence was confused by semantically similar criterion/distractor context"
    if domain == "web_accessibility" and semantic_type == "GENERAL_RULE_EXCEPTION":
        return "exception/status evidence was vulnerable to adjacent WCAG change-summary distractors"
    if event_id == "EXT_E003" or "公式" in title:
        return "formula event retained enough canonical formula signal for a candidate match"
    return "metadata-light distractor evidence changed the normalized semantic result or caused abstain"


def build_distractor_rows(meta: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    details = read_csv(OUT / "auto-policy-v3-robustness-distractor_metadata_light-candidate-repair-r3-seed20260827-details.csv")
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in details:
        if not bool_value(row.get("full_closure_success")):
            groups[row["event_id"]].append(row)

    rows: list[dict[str, Any]] = []
    for event_id, items in sorted(groups.items()):
        first = {**meta[event_id], **items[0]}
        attempts = len([row for row in details if row["event_id"] == event_id])
        failures = len(items)
        abstains = sum(row.get("selection_status") == "ABSTAIN" for row in items)
        selected_wrong = sum(
            row.get("selection_status") == "SELECTED" and not bool_value(row.get("selection_oracle_correct"))
            for row in items
        )
        rows.append(
            {
                "source": "distractor_robustness",
                "event_id": event_id,
                "semantic_type": first["semantic_type"],
                "domain": first["domain"],
                "title": first["title"],
                "attempts": attempts,
                "failures": failures,
                "failure_rate": pct(failures, attempts),
                "abstain_failures": abstains,
                "wrong_selection_failures": selected_wrong,
                "sample_statuses": compact_values([row.get("selection_status", "") for row in items]),
                "sample_selected_values": compact_values([row.get("selected_value", "") for row in items]),
                "sample_auto_semantic_results": compact_values([row.get("auto_semantic_result", "") for row in items]),
                "failure_mode": failure_mode(first, "distractor"),
            }
        )
    return rows


def build_negative_rows(meta: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    details = read_csv(OUT / "auto-policy-v3-negative-safety-r1-seed20260827-details.csv")
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in details:
        if bool_value(row.get("unsafe_selection")):
            groups[(row["variant"], row["event_id"])].append(row)

    rows: list[dict[str, Any]] = []
    for (variant, event_id), items in sorted(groups.items()):
        all_variant_event = [
            row for row in details if row["variant"] == variant and row["event_id"] == event_id
        ]
        first = {**meta[event_id], **items[0], "variant": variant}
        unsafe = len(items)
        unsafe_wrong = sum(bool_value(row.get("unsafe_wrong_selection")) for row in items)
        rows.append(
            {
                "source": "ungated_negative_safety",
                "variant": variant,
                "event_id": event_id,
                "semantic_type": first["semantic_type"],
                "domain": first["domain"],
                "title": first["title"],
                "attempts": len(all_variant_event),
                "unsafe_selections": unsafe,
                "unsafe_rate": pct(unsafe, len(all_variant_event)),
                "unsafe_wrong_selections": unsafe_wrong,
                "sample_statuses": compact_values([row.get("selection_status", "") for row in items]),
                "sample_selected_values": compact_values([row.get("selected_value", "") for row in items]),
                "sample_auto_semantic_results": compact_values([row.get("auto_semantic_result", "") for row in items]),
                "failure_mode": failure_mode(first, "negative"),
            }
        )
    return rows


def build_gate_residual_rows(meta: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    details = read_csv(OUT / "auto-policy-v3-conflict-gate-r1-seed20260827-details.csv")
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in details:
        if row.get("dataset") == "NEGATIVE" and bool_value(row.get("unsafe_selection")):
            groups[(row["variant"], row["event_id"])].append(row)

    rows: list[dict[str, Any]] = []
    for (variant, event_id), items in sorted(groups.items()):
        all_variant_event = [
            row
            for row in details
            if row.get("dataset") == "NEGATIVE"
            and row["variant"] == variant
            and row["event_id"] == event_id
        ]
        first = {**meta[event_id], **items[0], "variant": variant}
        rows.append(
            {
                "source": "gate_residual_unsafe",
                "variant": variant,
                "event_id": event_id,
                "semantic_type": first["semantic_type"],
                "domain": first["domain"],
                "title": first["title"],
                "attempts": len(all_variant_event),
                "unsafe_selections": len(items),
                "unsafe_rate": pct(len(items), len(all_variant_event)),
                "gate_decisions": compact_values([row.get("gate_decision", "") for row in items]),
                "gate_reasons": compact_values([row.get("gate_reasons", "") for row in items]) or "(empty)",
                "sample_selected_values": compact_values([row.get("selected_value", "") for row in items]),
                "sample_auto_semantic_results": compact_values([row.get("auto_semantic_result", "") for row in items]),
                "failure_mode": failure_mode(first, "gate_residual"),
            }
        )
    return rows


def summarize(rows: list[dict[str, Any]], key_fields: list[str]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(str(row.get(field, "")) for field in key_fields)].append(row)
    output: list[dict[str, Any]] = []
    for key, items in sorted(groups.items()):
        record = {field: key[index] for index, field in enumerate(key_fields)}
        failures = sum(int(row.get("failures", row.get("unsafe_selections", 0))) for row in items)
        attempts = sum(int(row.get("attempts", 0)) for row in items)
        record.update(
            {
                "events": len({row["event_id"] for row in items}),
                "failure_or_unsafe_count": failures,
                "attempts": attempts,
                "rate": pct(failures, attempts),
            }
        )
        output.append(record)
    return output


def summarize_distractor_by_type(meta: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    details = read_csv(OUT / "auto-policy-v3-robustness-distractor_metadata_light-candidate-repair-r3-seed20260827-details.csv")
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in details:
        groups[row["semantic_type"]].append(row)
    rows: list[dict[str, Any]] = []
    for semantic_type, items in sorted(groups.items()):
        failures = sum(not bool_value(row.get("full_closure_success")) for row in items)
        failed_events = {
            row["event_id"] for row in items if not bool_value(row.get("full_closure_success"))
        }
        rows.append(
            {
                "source": "distractor_robustness",
                "semantic_type": semantic_type,
                "events": len({row["event_id"] for row in items}),
                "failed_events": len(failed_events),
                "failure_or_unsafe_count": failures,
                "attempts": len(items),
                "rate": pct(failures, len(items)),
            }
        )
    return rows


def summarize_negative_by_type_and_variant() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    details = read_csv(OUT / "auto-policy-v3-negative-safety-r1-seed20260827-details.csv")
    by_type: dict[str, list[dict[str, str]]] = defaultdict(list)
    by_variant: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in details:
        by_type[row["semantic_type"]].append(row)
        by_variant[row["variant"]].append(row)

    type_rows: list[dict[str, Any]] = []
    for semantic_type, items in sorted(by_type.items()):
        unsafe = sum(bool_value(row.get("unsafe_selection")) for row in items)
        unsafe_events = {row["event_id"] for row in items if bool_value(row.get("unsafe_selection"))}
        type_rows.append(
            {
                "source": "ungated_negative_safety",
                "semantic_type": semantic_type,
                "events": len({row["event_id"] for row in items}),
                "failed_events": len(unsafe_events),
                "failure_or_unsafe_count": unsafe,
                "attempts": len(items),
                "rate": pct(unsafe, len(items)),
            }
        )

    variant_rows: list[dict[str, Any]] = []
    for variant, items in sorted(by_variant.items()):
        unsafe = sum(bool_value(row.get("unsafe_selection")) for row in items)
        unsafe_events = {row["event_id"] for row in items if bool_value(row.get("unsafe_selection"))}
        variant_rows.append(
            {
                "variant": variant,
                "events": len({row["event_id"] for row in items}),
                "failed_events": len(unsafe_events),
                "failure_or_unsafe_count": unsafe,
                "attempts": len(items),
                "rate": pct(unsafe, len(items)),
            }
        )
    return type_rows, variant_rows


def summarize_gate_by_type() -> list[dict[str, Any]]:
    details = [
        row
        for row in read_csv(OUT / "auto-policy-v3-conflict-gate-r1-seed20260827-details.csv")
        if row.get("dataset") == "NEGATIVE"
    ]
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in details:
        groups[row["semantic_type"]].append(row)
    rows: list[dict[str, Any]] = []
    for semantic_type, items in sorted(groups.items()):
        unsafe = sum(bool_value(row.get("unsafe_selection")) for row in items)
        unsafe_events = {row["event_id"] for row in items if bool_value(row.get("unsafe_selection"))}
        rows.append(
            {
                "source": "gate_residual_unsafe",
                "semantic_type": semantic_type,
                "events": len({row["event_id"] for row in items}),
                "failed_events": len(unsafe_events),
                "failure_or_unsafe_count": unsafe,
                "attempts": len(items),
                "rate": pct(unsafe, len(items)),
            }
        )
    return rows


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    lines = ["| " + " | ".join(columns) + " |", "|" + "|".join("---" for _ in columns) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(column, "")) for column in columns) + " |")
    return "\n".join(lines)


def main() -> int:
    meta = event_metadata()
    distractor_rows = build_distractor_rows(meta)
    negative_rows = build_negative_rows(meta)
    gate_rows = build_gate_residual_rows(meta)

    negative_type_rows, by_negative_variant = summarize_negative_by_type_and_variant()
    by_source_type = summarize_distractor_by_type(meta) + negative_type_rows + summarize_gate_by_type()

    write_csv(OUT / f"{PREFIX}-distractor.csv", distractor_rows)
    write_csv(OUT / f"{PREFIX}-negative-unsafe.csv", negative_rows)
    write_csv(OUT / f"{PREFIX}-gate-residual.csv", gate_rows)
    write_csv(OUT / f"{PREFIX}-by-source-type.csv", by_source_type)
    write_csv(OUT / f"{PREFIX}-by-negative-variant.csv", by_negative_variant)

    md_lines = [
        "# Auto Policy V3 Failure Case Analysis",
        "",
        "## Summary By Source And Semantic Type",
        "",
        markdown_table(by_source_type, ["source", "semantic_type", "events", "failed_events", "failure_or_unsafe_count", "attempts", "rate"]),
        "",
        "## Negative Safety Unsafe Selections By Variant",
        "",
        markdown_table(by_negative_variant, ["variant", "events", "failed_events", "failure_or_unsafe_count", "attempts", "rate"]),
        "",
        "## Distractor Robustness Failure Events",
        "",
        markdown_table(
            distractor_rows,
            [
                "event_id",
                "semantic_type",
                "domain",
                "failures",
                "attempts",
                "failure_rate",
                "failure_mode",
            ],
        ),
        "",
        "## V3+Gate Residual Unsafe Events",
        "",
        markdown_table(
            gate_rows,
            [
                "variant",
                "event_id",
                "semantic_type",
                "unsafe_selections",
                "attempts",
                "gate_reasons",
                "failure_mode",
            ],
        ),
        "",
        "Interpretation:",
        "",
        "- Distractor failures are concentrated in metadata-light WCAG temporal-version and cross-sentence-scope events.",
        "- Ungated V3 is not fail-closed under conflicting or dominant distractor evidence.",
        "- The conflict/uncertainty gate removes nearly all unsafe negative selections; the residual cases are formula rows where the negative mutation retained a normal-looking canonical signal.",
    ]
    (OUT / f"{PREFIX}.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    (OUT / f"{PREFIX}.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "distractor_rows": len(distractor_rows),
                "negative_rows": len(negative_rows),
                "gate_residual_rows": len(gate_rows),
                "outputs": {
                    "distractor": str(OUT / f"{PREFIX}-distractor.csv"),
                    "negative_unsafe": str(OUT / f"{PREFIX}-negative-unsafe.csv"),
                    "gate_residual": str(OUT / f"{PREFIX}-gate-residual.csv"),
                    "by_source_type": str(OUT / f"{PREFIX}-by-source-type.csv"),
                    "by_negative_variant": str(OUT / f"{PREFIX}-by-negative-variant.csv"),
                    "markdown": str(OUT / f"{PREFIX}.md"),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"distractor_rows={len(distractor_rows)}")
    print(f"negative_unsafe_rows={len(negative_rows)}")
    print(f"gate_residual_rows={len(gate_rows)}")
    print(f"markdown={OUT / f'{PREFIX}.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
