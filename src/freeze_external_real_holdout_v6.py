from __future__ import annotations

"""File-level freeze gate for external-real-holdout-v6-direct-ir-blind."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from build_external_real_holdout_v6 import BENCHMARK_DIR, BENCHMARK_NAME, PROTOCOL_PATH, sha256_file
from semantic_v2_common import PROJECT_DIR
from validate_external_real_holdout_v6 import validate


OUTPUT_DIR = PROJECT_DIR / "output" / BENCHMARK_NAME


def iter_benchmark_files() -> list[Path]:
    files: list[Path] = []
    for folder in ("public", "repair-stage", "private"):
        root = BENCHMARK_DIR / folder
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and "source-cache" not in path.parts:
                files.append(path)
    files.append(BENCHMARK_DIR / "README.md")
    return [path for path in files if path.is_file()]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("pilot", "full"), default="pilot")
    args = parser.parse_args(argv)
    report = validate(args.profile)
    if not report["freeze_ready"]:
        raise SystemExit(
            "freeze refused: dual annotation, adjudication, or a hard audit is incomplete; "
            "automatic human approval is prohibited"
        )
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    files = []
    for path in iter_benchmark_files():
        files.append(
            {
                "path": path.relative_to(PROJECT_DIR).as_posix(),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    manifest = {
        "schema_version": "external-real-holdout-v6-freeze-v1",
        "benchmark": BENCHMARK_NAME,
        "profile": args.profile,
        "status": "FROZEN_INDEPENDENT_HOLDOUT",
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "method_id": protocol["method_id"],
        "method_freeze_manifest_sha256": protocol["method_freeze_manifest_sha256"],
        "audit_status": report["status"],
        "files": files,
    }
    raw = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    digest = __import__("hashlib").sha256(raw.encode("utf-8")).hexdigest()
    manifest["manifest_sha256"] = digest
    output = OUTPUT_DIR / "external-real-holdout-v6-direct-ir-blind-freeze-manifest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "manifest": output.relative_to(PROJECT_DIR).as_posix(),
        "manifest_sha256": digest,
        "files": len(files),
        "profile": args.profile,
    }
    (OUTPUT_DIR / "external-real-holdout-v6-direct-ir-blind-freeze-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
