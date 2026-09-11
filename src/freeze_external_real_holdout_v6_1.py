from __future__ import annotations

"""Freeze the audited shortcut-fixed v6.1 benchmark."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from semantic_v2_common import PROJECT_DIR

NAME = "external-real-holdout-v6-1-direct-ir-blind"
BENCHMARK = PROJECT_DIR / "benchmark" / NAME
OUTPUT = PROJECT_DIR / "output" / NAME
AUDIT = OUTPUT / "audits/v6-1-full-audit.json"
METHOD_SHA = "ed250c97d056306ccc43b661ad78face6b607aceff1076cada98afb3ef612b54"
PARENT_SHA = "8acd967b6a4b6b5f4689e649bf41d9ec0f10e9379a4efb3c39a40df643e3cfbb"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    required = {"status": "PASS", "events": 300, "repair_events": 240,
                "safety_events": 60, "target_contract_exact": 240,
                "gamma_compiled": 240, "gamma_closed": 240}
    for key, value in required.items():
        if audit.get(key) != value:
            raise SystemExit(f"freeze refused: {key}={audit.get(key)!r} expected={value!r}")
    if (OUTPUT / "final-blind-r2b-v2-r5/raw-predicted-ir").exists():
        raise SystemExit("freeze refused: model output exists")
    files = []
    for path in sorted(BENCHMARK.rglob("*")):
        if path.is_file():
            files.append({"path": path.relative_to(PROJECT_DIR).as_posix(), "sha256": sha(path), "bytes": path.stat().st_size})
    payload = {"schema_version": "external-real-holdout-v6-1-freeze-v1", "benchmark": NAME,
               "status": "FROZEN_INDEPENDENT_HOLDOUT", "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
               "parent_benchmark_manifest_sha256": PARENT_SHA,
               "method_id": "ECR-IR-GAMMA-R2B-V2-WINDOW-RESOLUTION",
               "method_freeze_manifest_sha256": METHOD_SHA,
               "shortcut_fixes": ["NEUTRAL_PUBLIC_TASK_TYPE", "BALANCED_WINDOW_PERMUTATION", "PUBLIC_CURRENT_ONTOLOGY"],
               "audit_sha256": sha(AUDIT), "files": files}
    raw = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    payload["manifest_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    output = OUTPUT / f"{NAME}-freeze-manifest.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {"benchmark": NAME, "status": payload["status"], "files": len(files),
               "manifest": output.relative_to(PROJECT_DIR).as_posix(), "manifest_sha256": payload["manifest_sha256"]}
    (OUTPUT / f"{NAME}-freeze-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
