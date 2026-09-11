from __future__ import annotations

"""Build leave-domain/source-family-out transfer summaries for v4-blind."""

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR, write_csv


BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v4-blind"
RUN_DIR = PROJECT_DIR / "output" / "external-real-holdout-v4-blind" / "post-sanitize-offline-scoring"
FINAL_METHOD_FREEZE = PROJECT_DIR / "output" / "final-method-freeze" / "final-method-freeze-manifest.json"
POST_SANITIZE_FREEZE = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v4-blind"
    / "external-real-holdout-v4-blind-post-sanitize-v3-freeze-manifest.json"
)
OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v4-blind" / "generalization"

M13_DETAILS = (
    PROJECT_DIR
    / "output"
    / "external-real-holdout-v4-blind"
    / "final-blind-eval-r5-m16-deepseek"
    / "main-method"
    / "m13-pilot"
    / "arm-d-rule-refinement"
    / "ir"
    / "auto-policy-v4-ir-raw_window_metadata_light-r3-seed20260827-details.csv"
)
FULL_DETAILS = RUN_DIR / "m16-full-holdout-combined-details.csv"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def truth(value: Any) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes"}


def success(row: dict[str, str]) -> bool:
    return truth(row.get("closure_success")) or truth(row.get("full_closure_success"))


def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 0.0
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def source_family_from_url(url: str) -> str:
    text = (url or "").lower()
    for marker in ("rfc", "bcp", "std"):
        index = text.rfind(marker)
        if index >= 0:
            digits = ""
            for ch in text[index + len(marker) :]:
                if ch.isdigit():
                    digits += ch
                elif digits:
                    break
            if digits:
                return f"IETF {marker.upper()} {digits}"
    return url or "UNKNOWN"


def load_event_meta(benchmark: Path) -> dict[str, dict[str, str]]:
    events = read_csv(benchmark / "public" / "events" / "external-real-event-template.csv")
    out: dict[str, dict[str, str]] = {}
    for row in events:
        family = source_family_from_url(row.get("source_url", ""))
        out[row["event_id"]] = {
            "domain": row.get("domain", "UNKNOWN"),
            "semantic_type": row.get("semantic_type", "UNKNOWN"),
            "source_family": family,
        }
    return out


def strict_success(rows: list[dict[str, str]]) -> dict[str, bool]:
    by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_event[row["event_id"]].append(row)
    return {event_id: all(success(row) for row in subset) for event_id, subset in by_event.items()}


def group_rows(
    label: str,
    rows: list[dict[str, str]],
    event_meta: dict[str, dict[str, str]],
    group_key: str,
) -> list[dict[str, Any]]:
    by_group: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        meta = event_meta.get(row["event_id"], {})
        by_group[meta.get(group_key, "UNKNOWN")].append(row)

    out: list[dict[str, Any]] = []
    for group, subset in sorted(by_group.items()):
        events = sorted({row["event_id"] for row in subset})
        closure_success = sum(1 for row in subset if success(row))
        attempts = len(subset)
        strict = strict_success(subset)
        strict_k = sum(1 for ok in strict.values() if ok)
        low, high = wilson(closure_success, attempts)
        strict_low, strict_high = wilson(strict_k, len(events))
        out.append(
            {
                "evaluation": label,
                "held_out_axis": group_key,
                "held_out_group": group,
                "events": len(events),
                "attempts": attempts,
                "closure_success": closure_success,
                "closure_accuracy": closure_success / attempts if attempts else 0,
                "closure_ci95_low": low,
                "closure_ci95_high": high,
                "strict_event_successes": strict_k,
                "strict_event_accuracy": strict_k / len(events) if events else 0,
                "strict_ci95_low": strict_low,
                "strict_ci95_high": strict_high,
                "failed_events": ";".join(event_id for event_id, ok in strict.items() if not ok),
            }
        )
    return out


def compare_stage_rows(
    m13: list[dict[str, str]],
    full: list[dict[str, str]],
    event_meta: dict[str, dict[str, str]],
    group_key: str,
) -> list[dict[str, Any]]:
    m13_by_key = {(row["event_id"], row.get("run", "")): row for row in m13}
    full_by_key = {(row["event_id"], row.get("run", "")): row for row in full}
    shared = sorted(set(m13_by_key) & set(full_by_key))
    by_group: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for item in shared:
        by_group[event_meta.get(item[0], {}).get(group_key, "UNKNOWN")].append(item)

    out: list[dict[str, Any]] = []
    for group, keys in sorted(by_group.items()):
        m13_success = sum(1 for item in keys if success(m13_by_key[item]))
        full_success = sum(1 for item in keys if success(full_by_key[item]))
        n = len(keys)
        out.append(
            {
                "held_out_axis": group_key,
                "held_out_group": group,
                "matched_attempts": n,
                "m13_closure": m13_success / n if n else 0,
                "full_m16_closure": full_success / n if n else 0,
                "gain_pp": ((full_success - m13_success) / n * 100) if n else 0,
                "m13_success": m13_success,
                "full_m16_success": full_success,
            }
        )
    return out


def freeze_timeline() -> dict[str, Any]:
    method = read_json(FINAL_METHOD_FREEZE)
    benchmark = read_json(POST_SANITIZE_FREEZE)
    method_time = method.get("generated_at_utc", "")
    benchmark_time = benchmark.get("generated_at_utc", "")
    try:
        method_dt = datetime.fromisoformat(method_time.replace("Z", "+00:00"))
        benchmark_dt = datetime.fromisoformat(benchmark_time.replace("Z", "+00:00"))
        method_before_benchmark = method_dt < benchmark_dt
    except ValueError:
        method_before_benchmark = None
    return {
        "method_freeze_manifest": str(FINAL_METHOD_FREEZE.relative_to(PROJECT_DIR)),
        "method_freeze_time_utc": method_time,
        "benchmark_post_sanitize_manifest": str(POST_SANITIZE_FREEZE.relative_to(PROJECT_DIR)),
        "benchmark_post_sanitize_time_utc": benchmark_time,
        "method_frozen_before_post_sanitize_benchmark": method_before_benchmark,
        "method_freeze_status": method.get("freeze_status", ""),
        "holdout_tuning_forbidden": method.get("post_freeze_policy", {}).get("holdout_tuning_forbidden", ""),
    }


def write_markdown(
    path: Path,
    timeline: dict[str, Any],
    domain_rows: list[dict[str, Any]],
    family_rows: list[dict[str, Any]],
    comparison_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# v4-blind Leave-Group-Out Transfer Evaluation",
        "",
        "This is a frozen-method held-out group evaluation. The method is not retrained per group; each domain/source family is treated as a held-out test slice under the same frozen M16 pipeline.",
        "",
        "## Freeze Timeline",
        "",
        f"- Method freeze: `{timeline['method_freeze_time_utc']}`",
        f"- Post-sanitize benchmark freeze: `{timeline['benchmark_post_sanitize_time_utc']}`",
        f"- Method frozen before benchmark: `{timeline['method_frozen_before_post_sanitize_benchmark']}`",
        f"- Holdout tuning forbidden: `{timeline['holdout_tuning_forbidden']}`",
        "",
        "## Leave-One-Domain-Out Test Slices",
        "",
        "| Held-out domain | Events | Closure | Wilson 95% CI | Strict event | Failed events |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in domain_rows:
        lines.append(
            f"| {row['held_out_group']} | {row['events']} | "
            f"{row['closure_success']}/{row['attempts']} ({pct(row['closure_accuracy'])}) | "
            f"{pct(row['closure_ci95_low'])}-{pct(row['closure_ci95_high'])} | "
            f"{row['strict_event_successes']}/{row['events']} ({pct(row['strict_event_accuracy'])}) | "
            f"{row['failed_events']} |"
        )

    lines.extend(
        [
            "",
            "## Leave-One-Source-Family-Out Test Slices",
            "",
            "| Held-out source family | Events | Closure | Wilson 95% CI | Strict event | Failed events |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in family_rows:
        lines.append(
            f"| {row['held_out_group']} | {row['events']} | "
            f"{row['closure_success']}/{row['attempts']} ({pct(row['closure_accuracy'])}) | "
            f"{pct(row['closure_ci95_low'])}-{pct(row['closure_ci95_high'])} | "
            f"{row['strict_event_successes']}/{row['events']} ({pct(row['strict_event_accuracy'])}) | "
            f"{row['failed_events']} |"
        )

    lines.extend(
        [
            "",
            "## Per-Group Gain Over M13 Base",
            "",
            "| Axis | Group | Matched attempts | M13 closure | Full M16 closure | Gain |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in comparison_rows:
        lines.append(
            f"| {row['held_out_axis']} | {row['held_out_group']} | {row['matched_attempts']} | "
            f"{pct(row['m13_closure'])} | {pct(row['full_m16_closure'])} | {row['gain_pp']:.2f} pp |"
        )

    lines.extend(
        [
            "",
            "## Claim Boundary",
            "",
            "This evaluation supports held-out slice robustness across domains and source families under a frozen method. It is not a retraining-based leave-one-domain-out learning experiment, because the pipeline does not train a statistical model per domain. The safe wording is: frozen-method leave-domain/source-family-out test-slice evaluation.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    event_meta = load_event_meta(args.benchmark_dir)
    full = read_csv(FULL_DETAILS)
    m13 = read_csv(M13_DETAILS)
    timeline = freeze_timeline()
    domain_rows = group_rows("FULL_M16", full, event_meta, "domain")
    family_rows = group_rows("FULL_M16", full, event_meta, "source_family")
    comparison_rows = compare_stage_rows(m13, full, event_meta, "domain") + compare_stage_rows(
        m13, full, event_meta, "source_family"
    )

    write_csv(args.output_dir / "v4-blind-leave-domain-out.csv", domain_rows)
    write_csv(args.output_dir / "v4-blind-leave-source-family-out.csv", family_rows)
    write_csv(args.output_dir / "v4-blind-leave-group-gain-over-m13.csv", comparison_rows)
    payload = {
        "freeze_timeline": timeline,
        "leave_domain_out": domain_rows,
        "leave_source_family_out": family_rows,
        "gain_over_m13": comparison_rows,
    }
    (args.output_dir / "v4-blind-leave-group-out-transfer.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_markdown(
        args.output_dir / "v4-blind-leave-group-out-transfer.md",
        timeline,
        domain_rows,
        family_rows,
        comparison_rows,
    )
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir.relative_to(PROJECT_DIR)),
                "domains": len(domain_rows),
                "source_families": len(family_rows),
                "method_frozen_before_benchmark": timeline["method_frozen_before_post_sanitize_benchmark"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
