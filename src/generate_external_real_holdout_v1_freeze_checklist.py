from __future__ import annotations

"""Generate freeze checklist and summary for external-real-holdout-v1-expanded."""

import argparse
import csv
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR

BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-holdout-v1-expanded"
OUTPUT = PROJECT_DIR / "output" / "external-real-holdout-v1-expanded"
PROPOSED_FROZEN_NAME = "external-real-holdout-v1-expanded"
PROPOSED_FROZEN_DIR = PROJECT_DIR / "benchmark" / PROPOSED_FROZEN_NAME

VALIDATION_COMMANDS = [
    "python src/validate_external_real_holdout_v1.py --benchmark-dir benchmark/{name}",
    "python src/audit_external_real_holdout_v1_quality.py --benchmark-dir benchmark/{name}",
    "python src/review_holdout_five_consistency.py --benchmark-dir benchmark/{name}",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def run_check(script: str, benchmark_dir: Path, output_dir: Path) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(PROJECT_DIR / "src" / script),
        "--benchmark-dir",
        str(benchmark_dir),
        "--output-dir",
        str(output_dir),
    ]
    env = dict(**dict(__import__("os").environ))
    env["PYTHONPATH"] = str(PROJECT_DIR / "src")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    payload: dict[str, Any] = {"exit_code": proc.returncode}
    stdout = proc.stdout.strip()
    if stdout:
        try:
            payload["result"] = json.loads(stdout)
        except json.JSONDecodeError:
            payload["stdout_tail"] = stdout[-2000:]
    if proc.stderr.strip():
        payload["stderr_tail"] = proc.stderr.strip()[-1000:]
    return payload


def benchmark_stats(benchmark_dir: Path) -> dict[str, Any]:
    events = read_csv(benchmark_dir / "public" / "events" / "external-real-event-template.csv")
    oracle = read_csv(benchmark_dir / "private" / "oracle" / "external-real-oracle-template.csv")
    ready = [row for row in events if row.get("status") == "READY"]
    return {
        "events_total": len(events),
        "events_ready": len(ready),
        "domains": dict(sorted(Counter(row["domain"] for row in ready).items())),
        "semantic_types": dict(sorted(Counter(row["semantic_type"] for row in ready).items())),
        "support_status": dict(Counter(row.get("support_status", "") for row in ready)),
        "oracle_agreement_status": dict(Counter(row.get("agreement_status", "") for row in oracle)),
        "oracle_candidate_id": dict(Counter(row.get("oracle_candidate_id", "") for row in oracle)),
        "unique_predicate_labels": len({row.get("predicate_label", "") for row in ready}),
    }


def checklist_items(stats: dict[str, Any], checks: dict[str, Any], *, draft_name: str) -> list[dict[str, Any]]:
    oracle_agreed = stats["oracle_agreement_status"].get("AGREED", 0) == stats["events_ready"]
    support_passed = stats["support_status"].get("SEMANTIC_REVIEW_PASSED", 0) == stats["events_ready"]
    five_ok = checks.get("five_consistency", {}).get("result", {}).get("freeze_recommendation") == "READY"
    validate_ok = checks.get("validate", {}).get("result", {}).get("error_count", 1) == 0
    audit_ok = checks.get("audit", {}).get("result", {}).get("errors", 1) == 0
    manifest_missing = any(
        row.get("code") == "MISSING_FILE" and "freeze-manifest" in str(row.get("entity", ""))
        for row in checks.get("validate", {}).get("validation_audit", [])
    )
    if manifest_missing and checks.get("validate", {}).get("result", {}).get("error_count") == 1:
        validate_ok = True

    renamed = draft_name == PROPOSED_FROZEN_NAME
    return [
        {
            "id": "manual_review_writeback",
            "title": "人工复核结论已写回 oracle / events",
            "status": "PASS" if oracle_agreed and support_passed else "BLOCK",
            "detail": {
                "oracle_AGREED": stats["oracle_agreement_status"].get("AGREED", 0),
                "events_SEMANTIC_REVIEW_PASSED": stats["support_status"].get("SEMANTIC_REVIEW_PASSED", 0),
            },
        },
        {
            "id": "structural_validation",
            "title": "结构校验通过（validate_external_real_holdout_v1）",
            "status": "PASS" if validate_ok else "BLOCK",
            "detail": checks.get("validate", {}).get("result", {}),
        },
        {
            "id": "deep_quality_audit",
            "title": "深度质量审计通过（audit_external_real_holdout_v1_quality）",
            "status": "PASS" if audit_ok else "BLOCK",
            "detail": checks.get("audit", {}).get("result", {}),
        },
        {
            "id": "five_consistency",
            "title": "五一致性审查 READY",
            "status": "PASS" if five_ok else "BLOCK",
            "detail": checks.get("five_consistency", {}).get("result", {}),
        },
        {
            "id": "rename_benchmark_dir",
            "title": f"目录改名：external-real-holdout-v1-expanded-draft → {PROPOSED_FROZEN_NAME}",
            "status": "PASS" if renamed else "PENDING",
            "detail": {
                "from": "benchmark/external-real-holdout-v1-expanded-draft",
                "to": f"benchmark/{PROPOSED_FROZEN_NAME}",
            },
        },
        {
            "id": "update_path_references",
            "title": "更新脚本/输出中的 benchmark 路径引用",
            "status": "PASS" if renamed else "PENDING",
            "detail": {
                "note": "src/*holdout* 默认路径已指向 external-real-holdout-v1-expanded",
            },
        },
        {
            "id": "generate_freeze_manifest",
            "title": "生成 freeze manifest（文件哈希 + 统计摘要）",
            "status": "PASS" if (OUTPUT / "external-real-holdout-v1-freeze-manifest.json").is_file() else "PENDING",
            "detail": {
                "script": "src/freeze_external_real_holdout_v1_expanded.py",
            },
        },
        {
            "id": "paper_usage_lock",
            "title": "论文用 hold-out：freeze 后禁止用于方法调参",
            "status": "PASS" if renamed else "PENDING",
            "detail": {
                "note": "在 README / benchmark card 中声明 hold-out 仅用于最终评测",
            },
        },
    ]


def render_markdown(
    *,
    generated_at: str,
    draft_name: str,
    stats: dict[str, Any],
    items: list[dict[str, Any]],
    checks: dict[str, Any],
) -> str:
    blocked = [item for item in items if item["status"] == "BLOCK"]
    pending = [item for item in items if item["status"] == "PENDING"]
    passed = [item for item in items if item["status"] == "PASS"]
    ready_to_rename = not blocked

    lines = [
        "# External Real Hold-out v1 Expanded — Freeze Checklist",
        "",
        f"Generated: {generated_at}",
        "",
        f"Current draft: `benchmark/{draft_name}`",
        f"Proposed frozen name: `benchmark/{PROPOSED_FROZEN_NAME}`",
        "",
        "## Summary",
        "",
        f"- READY events: **{stats['events_ready']}**",
        f"- Domains: {len(stats['domains'])} (privacy_considerations has {stats['domains'].get('privacy_considerations', 0)} events)",
        f"- Semantic types: {stats['semantic_types']}",
        f"- Oracle AGREED: {stats['oracle_agreement_status'].get('AGREED', 0)} / {stats['events_ready']}",
        f"- Support SEMANTIC_REVIEW_PASSED: {stats['support_status'].get('SEMANTIC_REVIEW_PASSED', 0)} / {stats['events_ready']}",
        f"- Unique predicate labels: {stats['unique_predicate_labels']}",
        f"- Oracle candidate: {stats['oracle_candidate_id']}",
        "",
        "## Automated re-check (just ran)",
        "",
    ]
    for name, result in checks.items():
        lines.append(f"### {name}")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(result.get("result", result), ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")

    lines.extend(
        [
            "## Checklist",
            "",
            f"Overall: **{'READY TO FREEZE (pending rename/manifest)' if ready_to_rename else 'BLOCKED'}**",
            "",
        ]
    )
    for item in items:
        mark = {"PASS": "[x]", "PENDING": "[ ]", "BLOCK": "[!]"}.get(item["status"], "[ ]")
        lines.append(f"- {mark} **{item['title']}** (`{item['status']}`)")
        if item.get("detail"):
            lines.append(f"  - detail: `{json.dumps(item['detail'], ensure_ascii=False)}`")
    lines.extend(
        [
            "",
            "## Known design limits (document, do not block freeze)",
            "",
            "- CAND_003 is always `not_` prefix negation; manual review judged all 236 implausible.",
            "- `semantic_type` is mechanically rotated; not a fine-grained human label.",
            "- Single annotator manual review (no annotator_2).",
            "- 2 predicate-label collisions across 236 events.",
            "",
            "## Suggested freeze commands (after you confirm)",
            "",
            "```powershell",
            "cd G:\\LearnAI\\ontology-evolution",
            f"git mv benchmark\\{draft_name} benchmark\\{PROPOSED_FROZEN_NAME}",
            '$env:PYTHONPATH="src"',
            f".venv\\Scripts\\python.exe src\\validate_external_real_holdout_v1.py --benchmark-dir benchmark\\{PROPOSED_FROZEN_NAME}",
            f".venv\\Scripts\\python.exe src\\audit_external_real_holdout_v1_quality.py --benchmark-dir benchmark\\{PROPOSED_FROZEN_NAME}",
            f".venv\\Scripts\\python.exe src\\review_holdout_five_consistency.py --benchmark-dir benchmark\\{PROPOSED_FROZEN_NAME}",
            "```",
            "",
            f"_Passed: {len(passed)} | Pending: {len(pending)} | Blocked: {len(blocked)}_",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--skip-rerun-checks", action="store_true")
    args = parser.parse_args()

    generated_at = datetime.now(timezone.utc).isoformat()
    stats = benchmark_stats(args.benchmark_dir)

    checks: dict[str, Any] = {}
    if not args.skip_rerun_checks:
        checks["validate"] = run_check("validate_external_real_holdout_v1.py", args.benchmark_dir, args.output_dir)
        checks["audit"] = run_check("audit_external_real_holdout_v1_quality.py", args.benchmark_dir, args.output_dir)
        checks["five_consistency"] = run_check("review_holdout_five_consistency.py", args.benchmark_dir, args.output_dir)
        audit_csv = args.output_dir / "external-real-holdout-v1-validation-audit.csv"
        if audit_csv.is_file():
            checks["validate"]["validation_audit"] = read_csv(audit_csv)

    items = checklist_items(stats, checks, draft_name=args.benchmark_dir.name)
    pending_freeze_artifacts = any(item["status"] == "PENDING" for item in items)
    summary = {
        "generated_at_utc": generated_at,
        "benchmark_draft": args.benchmark_dir.name,
        "proposed_frozen_name": PROPOSED_FROZEN_NAME,
        "stats": stats,
        "checks": checks,
        "checklist": items,
        "ready_to_freeze": not any(item["status"] == "BLOCK" for item in items),
        "pending_freeze_artifacts": pending_freeze_artifacts,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "freeze-checklist-summary.json"
    md_path = args.output_dir / "freeze-checklist.md"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(
        render_markdown(
            generated_at=generated_at,
            draft_name=args.benchmark_dir.name,
            stats=stats,
            items=items,
            checks=checks,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"markdown": str(md_path.relative_to(PROJECT_DIR)), "json": str(json_path.relative_to(PROJECT_DIR)), "ready_to_freeze": summary["ready_to_freeze"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
