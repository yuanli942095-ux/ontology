from __future__ import annotations

"""Build Candidate-Identifier Permutation Test comparison report."""

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR


ROOT = PROJECT_DIR / "output" / "external-real-holdout-v5-blind-large"
BENCHMARK_ORIG = PROJECT_DIR / "benchmark" / "external-real-holdout-v5-blind-large"
PERMUTE_BENCH = (
    PROJECT_DIR
    / "benchmark"
    / "external-real-holdout-v5-blind-large-robustness-variants"
    / "candidate-id-permute"
)
AUDIT = PROJECT_DIR / "output" / "paper-final-validation" / "cand-002-shortcut-audit"
DEFAULT_OUT = AUDIT / "candidate-id-permutation-test-report.md"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def oracle_distribution(benchmark: Path) -> tuple[int, int, int]:
    rows = read_csv(benchmark / "private" / "oracle" / "external-real-oracle-template.csv")
    counts = Counter(row["oracle_candidate_id"] for row in rows)
    return (
        counts.get("CAND_001", 0) + counts.get("ALT_A", 0),
        counts.get("CAND_002", 0) + counts.get("ALT_B", 0),
        counts.get("CAND_003", 0) + counts.get("ALT_C", 0),
    )


def summary_row(summary_csv: Path) -> dict[str, str] | None:
    if not summary_csv.is_file():
        return None
    rows = read_csv(summary_csv)
    return next((r for r in rows if r.get("semantic_type") == "ALL"), None)


def analyze_details(details_csv: Path) -> dict[str, Any]:
    if not details_csv.is_file():
        return {"status": "MISSING", "path": str(details_csv)}
    rows = read_csv(details_csv)
    attempts = len(rows)
    closure_ok = sum(1 for r in rows if str(r.get("closure_success", "")).lower() == "true")
    oracle_ok = sum(1 for r in rows if str(r.get("oracle_correct", "")).lower() == "true")
    with_sel = [r for r in rows if str(r.get("selected_candidate_id", "")).strip()]
    wrong_select = sum(
        1
        for r in with_sel
        if str(r.get("oracle_correct", "")).lower() != "true"
        or str(r.get("closure_success", "")).lower() != "true"
    )
    no_sel_fail = sum(
        1
        for r in rows
        if not str(r.get("selected_candidate_id", "")).strip()
        and (
            str(r.get("oracle_correct", "")).lower() != "true"
            or str(r.get("closure_success", "")).lower() != "true"
        )
    )
    literal_cand_002 = sum(1 for r in with_sel if r.get("selected_candidate_id") == "CAND_002")
    literal_alt_b = sum(1 for r in with_sel if r.get("selected_candidate_id") == "ALT_B")
    picked_dist = Counter(r.get("selected_candidate_id", "") for r in with_sel)

    literal_shortcut_acc = None
    if literal_cand_002 and PERMUTE_BENCH.is_dir():
        perm_rows = read_csv(PERMUTE_BENCH / "private" / "oracle" / "external-real-oracle-template.csv")
        oracle_by_event = {r["event_id"]: r["oracle_candidate_id"] for r in perm_rows}
        perm_candidates = read_csv(
            PERMUTE_BENCH / "repair-stage" / "candidates" / "external-real-candidate-template.csv"
        )
        content_by_id: dict[str, dict[str, str]] = {}
        for row in perm_candidates:
            content_by_id.setdefault(row["event_id"], {})[row["candidate_id"]] = row["display_value"]
        literal_ok = 0
        for r in with_sel:
            if r.get("selected_candidate_id") != "CAND_002":
                continue
            event_id = r["event_id"]
            oracle_id = oracle_by_event.get(event_id, "")
            oracle_display = content_by_id.get(event_id, {}).get(oracle_id, "")
            cand_002_display = content_by_id.get(event_id, {}).get("CAND_002", "")
            if cand_002_display == oracle_display:
                literal_ok += 1
        literal_shortcut_acc = round(literal_ok / len(with_sel), 4) if with_sel else None

    return {
        "status": "OK",
        "attempts": attempts,
        "closure_pct": round(closure_ok / attempts * 100, 2) if attempts else None,
        "oracle_pct": round(oracle_ok / attempts * 100, 2) if attempts else None,
        "wrong_select": wrong_select,
        "no_selection_failures": no_sel_fail,
        "literal_cand_002_picks": literal_cand_002,
        "literal_alt_b_picks": literal_alt_b,
        "literal_shortcut_accuracy_if_always_cand_002": literal_shortcut_acc,
        "selected_id_distribution": dict(picked_dist),
    }


def fmt_dist(triple: tuple[int, int, int]) -> str:
    return f"{triple[0]}/{triple[1]}/{triple[2]}"


def literal_note(name: str, dist: tuple[int, int, int], details: dict[str, Any]) -> str:
    if dist[1] == 264 and dist[0] == 0 and dist[2] == 0:
        return "not identifiable (oracle always middle ID)"
    if dist == (0, 264, 0):
        return "not identifiable (oracle always middle ID)"
    if details.get("status") != "OK":
        return "pending"
    picks = details.get("selected_id_distribution", {})
    cand002 = int(picks.get("CAND_002", 0))
    total = sum(int(v) for v in picks.values())
    spread = "/".join(str(picks.get(k, 0)) for k in ("CAND_001", "CAND_002", "CAND_003"))
    acc = details.get("literal_shortcut_accuracy_if_always_cand_002")
    if acc is not None and total:
        return (
            f"expected {acc * 100:.1f}% if always CAND_002; "
            f"actual picks {spread} (content-based)"
        )
    return "—"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    experiments = {
        "original": {
            "benchmark": BENCHMARK_ORIG,
            "details": ROOT / "final-blind-eval-r5-m16-deepseek" / "m16-full-holdout-combined" / "m16-full-holdout-combined-details.csv",
        },
        "global_rename": {
            "benchmark": PROJECT_DIR / "benchmark" / "external-real-holdout-v5-blind-large-robustness-variants" / "candidate-id-rename",
            "details": ROOT / "robustness-online" / "candidate-id-rename-full-r5-deepseek" / "m16-full-holdout-combined" / "m16-full-holdout-combined-details.csv",
        },
        "per_event_permutation": {
            "benchmark": PERMUTE_BENCH,
            "details": ROOT / "robustness-online" / "candidate-id-permute-full-r5-deepseek" / "m16-full-holdout-combined" / "m16-full-holdout-combined-details.csv",
        },
    }

    audit_json = AUDIT / "cand-002-shortcut-audit.json"
    expected_literal = None
    if audit_json.is_file():
        expected_literal = json.loads(audit_json.read_text(encoding="utf-8")).get(
            "counterfactual_on_original_m16_details", {}
        ).get("literal_shortcut_accuracy")

    rows_report: list[dict[str, Any]] = []
    for key, spec in experiments.items():
        dist = oracle_distribution(spec["benchmark"])
        details = analyze_details(spec["details"])
        rows_report.append(
            {
                "key": key,
                "oracle_dist": dist,
                "details": details,
            }
        )

    lines = [
        "# Candidate-Identifier Permutation Test",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "Frozen M16 method; derived benchmarks only; no method retuning.",
        "",
        "| Setting | Oracle ID dist (001/002/003 or ALT_A/B/C) | Closure | Wrong-select | Literal-ID shortcut |",
        "|---------|---------------------------------------------|---------|--------------|---------------------|",
    ]
    labels = {
        "original": "Original",
        "global_rename": "Global rename",
        "per_event_permutation": "Per-event permutation",
    }
    for item in rows_report:
        d = item["details"]
        closure = f"{d.get('closure_pct', '—')}%" if d.get("closure_pct") is not None else "—"
        wrong = str(d.get("wrong_select", "—")) if d.get("status") == "OK" else "—"
        if d.get("status") == "OK" and d.get("no_selection_failures"):
            wrong = f"{d.get('wrong_select')} (+{d.get('no_selection_failures')} abstain-fail)"
        literal = literal_note(labels[item["key"]], item["oracle_dist"], d)
        lines.append(
            f"| {labels[item['key']]} | {fmt_dist(item['oracle_dist'])} | {closure} | {wrong} | {literal} |"
        )

    if expected_literal is not None:
        lines.extend(
            [
                "",
                f"Counterfactual literal-shortcut expectation (per-event perm, original details): **{expected_literal * 100:.2f}%**.",
            ]
        )

    perm = rows_report[-1]["details"]
    if perm.get("status") == "OK":
        lines.extend(
            [
        "",
        "## Per-event permutation run",
        "",
        "Method: frozen M16 pipeline; candidate-blind M13–M15 artifacts reused from original eval; "
        "V4 IR candidate ranking re-executed on permute benchmark (1320 attempts).",
                f"- Attempts: {perm['attempts']}",
                f"- Closure: {perm['closure_pct']}%",
                f"- Oracle-correct: {perm['oracle_pct']}%",
                f"- Wrong-select (selected but not oracle+closure ok): {perm['wrong_select']}",
                f"- Selected ID distribution: `{json.dumps(perm['selected_id_distribution'], ensure_ascii=False)}`",
            ]
        )

    out = args.output.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    json_out = out.with_suffix(".json")
    json_out.write_text(
        json.dumps(
            {
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "expected_literal_shortcut_counterfactual": expected_literal,
                "experiments": rows_report,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(out.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
