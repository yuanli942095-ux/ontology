from __future__ import annotations

"""Exp14 offline statistical robustness audit (source bootstrap, LOO, pair CIs, zero-wrong CI)."""

import argparse
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from build_exp8_evidence_dependence_report import percentile, truth
from exp9_baseline_common import ECR_CONTROL_DIR, EXP9_ROOT
from paper_final_validation_common import PAPER_VALIDATION_ROOT, read_csv, utc_now_iso, write_summary_json
from semantic_v2_common import PROJECT_DIR, write_csv


EXP14_ROOT = PAPER_VALIDATION_ROOT / "14-statistical-audit"
BOOTSTRAPS = 10_000
SOURCE_SEED = 20260909

ECR_DETAILS = (
    ECR_CONTROL_DIR / "m16-full-holdout-combined" / "m16-full-holdout-combined-details.csv"
)
EXP8_NORMAL = (
    PAPER_VALIDATION_ROOT / "08-evidence-dependence" / "evidence-dependence-details-formal.csv"
)
EXP9_DETAILS = EXP9_ROOT / "strong-baselines-details-formal.csv"


def clopper_pearson_upper(n: int, wrong: int, alpha: float = 0.05) -> float:
    """One-sided upper bound on wrong-selection rate when observed wrong=0."""
    if wrong > 0:
        return wrong / n
    if n <= 0:
        return 0.0
    # CP upper bound for 0 successes in n trials (treat wrong as 'success' in binomial)
    return 1 - (alpha ** (1 / n))


def wilson_upper(n: int, wrong: int, alpha: float = 0.05) -> float:
    if n <= 0:
        return 0.0
    p = wrong / n
    z = 1.959963984540054
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denom
    return min(1.0, centre + margin)


def event_closure_scores(rows: list[dict[str, str]], field: str = "closure_pass") -> dict[str, list[int]]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        grouped[row["event_id"]].append(1 if truth(row.get(field, "")) else 0)
    return grouped


def closure_accuracy(scores: dict[str, list[int]], events: list[str]) -> float:
    total = sum(sum(scores[e]) for e in events)
    count = sum(len(scores[e]) for e in events)
    return total / count if count else 0.0


def source_cluster_key(row: dict[str, str]) -> str:
    source_id = str(row.get("source_id", "")).strip()
    source_family = str(row.get("source_family", "")).strip()
    if source_id:
        return f"doc:{source_id}"
    if source_family:
        return f"family:{source_family}"
    return f"event:{row.get('event_id', '')}"


def load_ecr_attempt_rows() -> list[dict[str, str]]:
    exp8_rows = [r for r in read_csv(EXP8_NORMAL) if r.get("condition") == "normal"]
    if not exp8_rows:
        raise FileNotFoundError(f"no exp8 normal rows in {EXP8_NORMAL}")
    return [
        {
            "event_id": r["event_id"],
            "semantic_type": r.get("semantic_type", ""),
            "run": str(r.get("run_id", r.get("run", ""))),
            "closure_pass": "True" if truth(r.get("closure_pass", "")) else "False",
            "wrong_select": "True" if truth(r.get("wrong_select", "")) else "False",
            "source_id": r.get("source_id", ""),
            "source_family": r.get("source_family", ""),
        }
        for r in exp8_rows
    ]


def source_cluster_bootstrap(rows: list[dict[str, str]]) -> dict[str, Any]:
    clusters: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        clusters[source_cluster_key(row)].append(row["event_id"])
    cluster_events = {key: sorted(set(event_ids)) for key, event_ids in clusters.items()}
    scores = event_closure_scores(rows, "closure_pass")
    events = sorted(scores.keys())
    observed = closure_accuracy(scores, events)
    rng = random.Random(SOURCE_SEED)
    cluster_keys = sorted(cluster_events.keys())
    samples: list[float] = []
    for _ in range(BOOTSTRAPS):
        picked = [rng.choice(cluster_keys) for _ in cluster_keys]
        boot_events: list[str] = []
        for key in picked:
            boot_events.extend(cluster_events[key])
        samples.append(closure_accuracy(scores, boot_events))
    return {
        "metric": "closure_accuracy",
        "clusters": len(cluster_keys),
        "events": len(events),
        "attempts": len(rows),
        "bootstrap_replicates": BOOTSTRAPS,
        "bootstrap_seed": SOURCE_SEED,
        "observed": f"{observed:.6f}",
        "ci_low": f"{percentile(samples, 0.025):.6f}",
        "ci_high": f"{percentile(samples, 0.975):.6f}",
    }


def leave_one_source_out(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    clusters: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        clusters[source_cluster_key(row)].add(row["event_id"])
    scores = event_closure_scores(rows, "closure_pass")
    events = sorted(scores.keys())
    overall = closure_accuracy(scores, events)
    out: list[dict[str, Any]] = []
    for key in sorted(clusters.keys()):
        held_out = clusters[key]
        remain = [e for e in events if e not in held_out]
        acc = closure_accuracy(scores, remain) if remain else 0.0
        out.append(
            {
                "held_out_cluster": key,
                "held_out_events": len(held_out),
                "remaining_events": len(remain),
                "closure_accuracy": f"{acc:.6f}",
                "delta_from_full_pp": f"{(acc - overall) * 100:.4f}",
            }
        )
    return out


def paired_bootstrap(left: dict[str, list[int]], right: dict[str, list[int]], events: list[str], label: str, seed: int) -> dict[str, Any]:
    observed = (closure_accuracy(left, events) - closure_accuracy(right, events)) * 100
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(BOOTSTRAPS):
        picked = [rng.choice(events) for _ in events]
        diff = closure_accuracy(left, picked) - closure_accuracy(right, picked)
        samples.append(diff * 100)
    return {
        "comparison": label,
        "metric": "closure_accuracy_pp",
        "events": len(events),
        "observed_diff_pp": f"{observed:.4f}",
        "ci_low_pp": f"{percentile(samples, 0.025):.4f}",
        "ci_high_pp": f"{percentile(samples, 0.975):.4f}",
        "bootstrap_replicates": BOOTSTRAPS,
        "bootstrap_seed": seed,
    }


def zero_wrong_selection_audit(rows: list[dict[str, str]]) -> dict[str, Any]:
    attempts = len(rows)
    wrong = sum(truth(r.get("wrong_select", "")) for r in rows)
    events = sorted({r["event_id"] for r in rows})
    wrong_by_event = {e: sum(truth(r.get("wrong_select", "")) for r in rows if r["event_id"] == e) for e in events}
    return {
        "attempts": attempts,
        "events": len(events),
        "observed_wrong_selections": wrong,
        "observed_wrong_rate": f"{wrong / attempts:.6f}" if attempts else "0",
        "events_with_any_wrong": sum(1 for v in wrong_by_event.values() if v > 0),
        "clopper_pearson_upper_95_wrong_rate": f"{clopper_pearson_upper(attempts, wrong):.6f}",
        "wilson_upper_95_wrong_rate": f"{wilson_upper(attempts, wrong):.6f}",
        "paper_phrase": "zero observed wrong selections",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=EXP14_ROOT)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    ecr_rows = load_ecr_attempt_rows()
    bootstrap_summary = source_cluster_bootstrap(ecr_rows)
    loo_rows = leave_one_source_out(ecr_rows)
    zero_wrong = zero_wrong_selection_audit(ecr_rows)

    pair_rows: list[dict[str, Any]] = []
    if EXP9_DETAILS.is_file():
        exp9 = read_csv(EXP9_DETAILS)
        by_cond: dict[str, dict[str, list[int]]] = {}
        for condition in ("ecr-control", "direct-llm", "m16-only", "compute-matched"):
            subset = [r for r in exp9 if r.get("condition") == condition]
            if subset:
                by_cond[condition] = event_closure_scores(subset, "closure_pass")
        events = sorted(next(iter(by_cond.values())).keys())
        if "ecr-control" in by_cond:
            for other, label in (
                ("direct-llm", "ECR vs direct-llm"),
                ("m16-only", "ECR vs m16-only"),
                ("compute-matched", "ECR vs compute-matched"),
            ):
                if other in by_cond:
                    pair_rows.append(
                        paired_bootstrap(by_cond["ecr-control"], by_cond[other], events, label, 20260909)
                    )

    write_csv(args.output_dir / "source-cluster-bootstrap-summary.csv", [bootstrap_summary])
    write_csv(args.output_dir / "leave-one-source-out.csv", loo_rows)
    write_csv(args.output_dir / "zero-wrong-selection-ci.csv", [zero_wrong])
    if pair_rows:
        write_csv(args.output_dir / "robustness-pair-bootstrap.csv", pair_rows)

    summary = {
        "completed_at_utc": utc_now_iso(),
        "ecr_details": str(ECR_DETAILS.relative_to(PROJECT_DIR)),
        "source_bootstrap": bootstrap_summary,
        "zero_wrong": zero_wrong,
        "loo_clusters": len(loo_rows),
        "pair_comparisons": len(pair_rows),
    }
    write_summary_json(args.output_dir / "statistical-audit-summary.json", summary)
    report_lines = [
        "# Exp14 Statistical Robustness Audit",
        "",
        "## Source-cluster bootstrap (ECR permute)",
        f"- Observed closure: **{bootstrap_summary['observed']}**",
        f"- 95% CI: [{bootstrap_summary['ci_low']}, {bootstrap_summary['ci_high']}]",
        f"- Clusters: {bootstrap_summary['clusters']}, events: {bootstrap_summary['events']}",
        "",
        "## Zero wrong-selection uncertainty",
        f"- Observed wrong: **{zero_wrong['observed_wrong_selections']}** / {zero_wrong['attempts']}",
        f"- Clopper–Pearson 95% upper bound: **{zero_wrong['clopper_pearson_upper_95_wrong_rate']}**",
        "",
        "## Leave-one-source-out",
        f"- Clusters evaluated: {len(loo_rows)}",
        "",
    ]
    if loo_rows:
        accs = [float(r["closure_accuracy"]) for r in loo_rows]
        report_lines.extend(
            [
                f"- Min accuracy: **{min(accs):.4f}**",
                f"- Max accuracy: **{max(accs):.4f}**",
                f"- Mean accuracy: **{sum(accs)/len(accs):.4f}**",
                "",
            ]
        )
    if pair_rows:
        report_lines.append("## Paired bootstrap (ECR − baseline)")
        for row in pair_rows:
            report_lines.append(
                f"- {row['comparison']}: {row['observed_diff_pp']} pp "
                f"[{row['ci_low_pp']}, {row['ci_high_pp']}]"
            )
    (args.output_dir / "statistical-audit-report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
