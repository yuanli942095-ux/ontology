"""Freeze a DOSD domain benchmark after dual review and type adjudication."""
from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dosd_multidomain_common import read_csv, sha256_file, sha256_text, write_csv


ROOT = Path(__file__).resolve().parents[1]


def relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def git_state() -> dict[str, Any]:
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL, text=True).strip()
        status = subprocess.check_output(["git", "status", "--short"], cwd=ROOT, stderr=subprocess.DEVNULL, text=True)
        return {"available": True, "commit": commit, "dirty": bool(status.strip()), "status_short": status.splitlines()}
    except Exception as exc:
        return {"available": False, "commit": "", "dirty": None, "status_short": [], "error": f"{type(exc).__name__}: {exc}"}


def file_row(path: Path, role: str, privacy: str) -> dict[str, Any]:
    stat = path.stat()
    return {
        "role": role,
        "privacy": privacy,
        "path": relpath(path),
        "sha256": sha256_file(path),
        "bytes": stat.st_size,
        "mtime_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def directory_file_rows(base: Path, role: str, privacy: str) -> list[dict[str, Any]]:
    if not base.is_dir():
        return []
    rows = []
    for path in sorted(item for item in base.rglob("*") if item.is_file()):
        if path.name == ".gitkeep":
            continue
        rows.append(file_row(path, role, privacy))
    return rows


def privacy_for(relative: Path) -> str:
    parts = relative.parts
    if parts and parts[0] == "private":
        return "private"
    if parts and parts[0] == "repair-stage":
        return "repair_stage"
    return "public"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", choices=("medical", "legal"), required=True)
    parser.add_argument("--benchmark-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    benchmark = (args.benchmark_dir or ROOT / "benchmark" / f"dosd-{args.corpus}-v1-draft").resolve()
    output_dir = args.output_dir or ROOT / "output" / benchmark.name
    events = read_csv(benchmark / "public" / "events" / "external-real-event-template.csv")
    oracle_path = benchmark / "private" / "oracle" / "external-real-oracle-template.csv"
    oracle = read_csv(oracle_path) if oracle_path.is_file() else []
    summary_path = benchmark / "private" / "annotation" / "dual-review-finalization-summary.json"
    if not summary_path.is_file():
        raise SystemExit("refusing to freeze: dual-review-finalization-summary.json is missing")
    finalization = json.loads(summary_path.read_text(encoding="utf-8"))
    if finalization.get("status") != "READY_FOR_FREEZE":
        raise SystemExit(f"refusing to freeze: finalization status is {finalization.get('status')}")
    if len(oracle) != len(events) or any(row.get("status") != "READY" for row in events):
        raise SystemExit("refusing to freeze: events/oracle are not READY")
    events_by_id = {row["event_id"]: row for row in events}
    if any(row["event_id"] not in events_by_id for row in oracle):
        raise SystemExit("refusing to freeze: oracle event_id missing from events")

    hashed_rows: list[dict[str, Any]] = []
    for path in sorted(item for item in benchmark.rglob("*") if item.is_file()):
        relative = path.relative_to(benchmark)
        if relative.as_posix() == "REBUILD_STATUS.md":
            continue
        hashed_rows.append(file_row(path, "benchmark_file", privacy_for(relative)))
    for script in (
        "src/audit_dosd_domain_draft.py",
        "src/finalize_dosd_dual_review.py",
        "src/adjudicate_dosd_legal_v2_drift_type.py",
        "src/freeze_dosd_domain.py",
        "src/dosd_multidomain_common.py",
    ):
        path = ROOT / script
        if path.is_file():
            hashed_rows.append(file_row(path, "reproduction_script", privacy="public"))

    type_counts = Counter(row["semantic_type"] for row in events)
    generated_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "schema_version": "1.0",
        "freeze_kind": "DOSD_DOMAIN_BENCHMARK",
        "generated_at_utc": generated_at,
        "corpus": args.corpus,
        "benchmark": benchmark.name,
        "benchmark_dir": relpath(benchmark),
        "events": len(events),
        "oracle_rows": len(oracle),
        "semantic_type_counts": dict(sorted(type_counts.items())),
        "gold_agreed_independently": True,
        "drift_type_adjudication": relpath(benchmark / "private" / "annotation" / "drift-type-adjudication.csv")
        if (benchmark / "private" / "annotation" / "drift-type-adjudication.csv").is_file()
        else "",
        "finalization": finalization,
        "git": git_state(),
        "limitations": [
            "Independent A/B gold labels are frozen; drift types use agreement plus documented adjudication.",
            "private/ Oracle is evaluation-only and must not enter repair prompts.",
            "private/construction is a construction audit trail, not gold.",
            "After freeze, do not restage sources or relabel types because a model erred.",
        ],
        "files": hashed_rows,
    }
    payload["manifest_sha256"] = sha256_text(
        json.dumps(
            [{key: row[key] for key in ("role", "privacy", "path", "sha256")} for row in hashed_rows],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{benchmark.name}-freeze-manifest"
    json_path = output_dir / f"{prefix}.json"
    write_csv(output_dir / f"{prefix}-files.csv", hashed_rows)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (benchmark / "REBUILD_STATUS.md").write_text(
        "\n".join(
            [
                f"# {benchmark.name} freeze",
                "",
                f"Status: `FROZEN` at `{generated_at}`.",
                "",
                f"- Events: {len(events)}",
                f"- Oracle rows: {len(oracle)}",
                f"- Semantic types: `{dict(sorted(type_counts.items()))}`",
                "- Gold candidates: independent A/B agreement on all events.",
                "- Drift types: independent agreement plus documented type adjudication; gold was not re-selected.",
                f"- Manifest: `{relpath(json_path)}`",
                f"- Manifest SHA256: `{payload['manifest_sha256']}`",
                "",
                "Do not restage sources or rewrite Oracle because a model erred.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "benchmark": benchmark.name,
                "status": "FROZEN",
                "events": len(events),
                "oracle_rows": len(oracle),
                "semantic_type_counts": dict(sorted(type_counts.items())),
                "manifest": relpath(json_path),
                "manifest_sha256": payload["manifest_sha256"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
