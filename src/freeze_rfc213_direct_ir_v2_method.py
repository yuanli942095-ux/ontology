from __future__ import annotations

"""Freeze R2b-v2 after RFC-213 development evaluation.

The resulting method is restricted to a new independent hold-out. It does not
retroactively convert the RFC-213 development result into a blind result.
"""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR


PARENT_FREEZE = PROJECT_DIR / "method/ecr-ir-gamma/freeze-v1/method-freeze-manifest.json"
DEVELOPMENT_SUMMARY = PROJECT_DIR / (
    "output/rfc-213-confirmatory-core/phase3-direct-ir/"
    "engineering-v2-window-resolution/r2b-v2-window-resolution-summary.json"
)
DEFAULT_OUTPUT = PROJECT_DIR / "method/ecr-ir-gamma/freeze-v2/method-freeze-manifest.json"
V2_FILES = (
    "method/ecr-ir-gamma/v2-window-resolution-development-protocol.json",
    "src/rfc213_direct_repair_ir_v2.py",
    "src/evaluate_rfc213_direct_ir_v2_window_resolution.py",
    "src/freeze_rfc213_direct_ir_v2_method.py",
    "tests/test_rfc213_direct_repair_ir_v2.py",
    "scripts/run_rfc213_direct_ir_v2_development.ps1",
)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_manifest(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    claimed = payload.get("manifest_sha256", "")
    unsigned = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    actual = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if claimed != actual:
        raise SystemExit(f"parent manifest self-hash mismatch: {path}")
    for item in payload.get("files", []):
        file_path = PROJECT_DIR / item["path"]
        if not file_path.is_file() or sha256_file(file_path) != item["sha256"]:
            raise SystemExit(f"parent frozen file mismatch: {item['path']}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--development-summary", type=Path, default=DEVELOPMENT_SUMMARY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    parent = verify_manifest(PARENT_FREEZE)
    summary_path = args.development_summary.resolve()
    summary = load_json(summary_path)[0]
    required = {
        "events": 213,
        "attempts": 213,
        "semantic_ir_valid": 213,
        "predicted_gamma_ir_exact": 204,
        "gamma_accepted": 209,
        "wrong_repairs": 5,
        "abstains": 4,
        "ses_success": 204,
    }
    for key, expected in required.items():
        if summary.get(key) != expected:
            raise SystemExit(f"v2 development gate mismatch: {key}={summary.get(key)} expected={expected}")

    files = []
    for relative in V2_FILES:
        path = PROJECT_DIR / relative
        if not path.is_file():
            raise SystemExit(f"missing v2 file: {relative}")
        files.append({"path": relative, "sha256": sha256_file(path), "bytes": path.stat().st_size})

    payload = {
        "schema_version": "ecr-ir-gamma-method-freeze-v2",
        "method_id": "ECR-IR-GAMMA-R2B-V2-WINDOW-RESOLUTION",
        "status": "FROZEN_FOR_NEW_INDEPENDENT_HOLDOUT_ONLY",
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "parent_method_id": parent["method_id"],
        "parent_manifest_sha256": parent["manifest_sha256"],
        "development_dataset": "rfc-213-confirmatory-core",
        "development_result_is_blind": False,
        "independent_holdout_required": True,
        "candidate_blind": True,
        "oracle_blind_during_inference": True,
        "gamma_changed": False,
        "schema_changed": False,
        "prompt_changed": False,
        "window_resolution": {
            "zero_matches": "ABSTAIN",
            "multiple_distinct_canonical_values": "ABSTAIN",
            "multiple_windows_same_canonical_value": "ACCEPT",
        },
        "development_summary_sha256": sha256_file(summary_path),
        "files": files,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["manifest_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "method_id": payload["method_id"],
                "status": payload["status"],
                "files": len(files),
                "parent_manifest_sha256": payload["parent_manifest_sha256"],
                "manifest_sha256": payload["manifest_sha256"],
                "output": str(output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
