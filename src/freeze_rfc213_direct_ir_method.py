from __future__ import annotations

"""Freeze the audited R2b Direct Predicted-IR method after smoke-v2."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR


BENCHMARK_MANIFEST = PROJECT_DIR / "output/rfc-213-confirmatory-core/rfc213-freeze-manifest.json"
TARGET_AUDIT = PROJECT_DIR / "output/rfc-213-confirmatory-core/phase3-direct-ir/target-contract-audit/rfc213-direct-ir-target-contract-summary.json"
SMOKE_ROOT = PROJECT_DIR / "output/rfc-213-confirmatory-core/phase3-direct-ir/smoke-v2"
DEFAULT_OUTPUT = PROJECT_DIR / "method/ecr-ir-gamma/freeze-v1/method-freeze-manifest.json"
FROZEN_FILES = (
    "method/ecr-ir-gamma/predicted-repair-ir-v1.schema.json",
    "method/ecr-ir-gamma/direct-ir-method-draft-v1.json",
    "src/rfc213_direct_repair_ir.py",
    "src/run_rfc213_direct_ir_gamma.py",
    "src/audit_rfc213_direct_ir_target_contract.py",
    "src/freeze_rfc213_direct_ir_method.py",
    "src/m13_llm_backends.py",
    "src/auto_policy_m12_deepseek_client.py",
    "src/run_auto_formal_policy_batch_v3.py",
    "src/run_auto_policy_v2_candidate_repair.py",
    "src/run_external_real_v1_symbolic_closure.py",
    "src/run_gamma_gold_ir_compile_eval.py",
    "src/run_gamma_gold_ir_closure_eval.py",
    "src/evaluate_rfc213_phase3_gamma_closure.py",
    "src/validate_benchmark.py",
    "src/semantic_v2_common.py",
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def verify_benchmark_artifacts(manifest: dict[str, Any]) -> int:
    prefix = "benchmark/rfc-213-confirmatory-core/"
    artifacts = [item for item in manifest.get("files", []) if item.get("path", "").startswith(prefix)]
    for item in artifacts:
        path = PROJECT_DIR / item["path"]
        if not path.is_file() or sha256_file(path) != item["sha256"]:
            raise SystemExit(f"frozen benchmark artifact mismatch: {item['path']}")
    return len(artifacts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    benchmark = load_json(BENCHMARK_MANIFEST)
    verified_benchmark_files = verify_benchmark_artifacts(benchmark)
    target = load_json(TARGET_AUDIT)
    smoke_summary = load_json(SMOKE_ROOT / "r2b-direct-ir-gamma-summary.json")[0]
    if not target.get("direct_ir_exact_target_contract_ready") or target.get("derivable") != 213:
        raise SystemExit("target-contract audit has not passed 213/213")
    required_smoke = {
        "attempts": 5,
        "semantic_ir_valid": 5,
        "predicted_gamma_ir_exact": 5,
        "gamma_accepted": 5,
        "wrong_repairs": 0,
        "ses_success": 5,
    }
    for key, expected in required_smoke.items():
        if smoke_summary.get(key) != expected:
            raise SystemExit(f"smoke-v2 gate failed: {key}={smoke_summary.get(key)} expected={expected}")

    observed_models = set()
    for path in sorted((SMOKE_ROOT / "raw-predicted-ir").glob("*.json")):
        raw = load_json(path)
        if raw.get("candidate_used") or raw.get("oracle_used") or raw.get("private_data_used"):
            raise SystemExit(f"candidate-blind audit failed: {path.name}")
        observed_models.add(str(raw.get("audit", {}).get("model", "")))
    if len(observed_models) != 1:
        raise SystemExit(f"smoke-v2 observed model mismatch: {sorted(observed_models)}")

    files = []
    for relative in FROZEN_FILES:
        path = PROJECT_DIR / relative
        if not path.is_file():
            raise SystemExit(f"missing frozen method file: {relative}")
        files.append({"path": relative, "sha256": sha256_file(path), "bytes": path.stat().st_size})

    payload = {
        "schema_version": "ecr-ir-gamma-method-freeze-v1",
        "method_id": "ECR-IR-GAMMA-R2B-V1",
        "status": "FROZEN_FOR_RFC213_ENGINEERING_213X1",
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "benchmark": {
            "name": "rfc-213-confirmatory-core",
            "manifest_sha256": benchmark["manifest_sha256"],
            "events": 213,
            "verified_benchmark_files": verified_benchmark_files,
        },
        "llm": {
            "backend": "deepseek_api",
            "requested_model": "deepseek-chat",
            "observed_model_in_smoke_v2": next(iter(observed_models)),
            "temperature": 0,
            "max_tokens": 900,
            "timeout_seconds": 180,
            "transport_max_attempts": 3,
            "seed": 20260829,
        },
        "candidate_blind_generation": True,
        "target_contract_audit_sha256": sha256_file(TARGET_AUDIT),
        "smoke_v2_summary_sha256": sha256_file(SMOKE_ROOT / "r2b-direct-ir-gamma-summary.json"),
        "full_213x5_locked": True,
        "files": files,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["manifest_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "method_id": payload["method_id"],
        "status": payload["status"],
        "files": len(files),
        "observed_model": next(iter(observed_models)),
        "manifest_sha256": payload["manifest_sha256"],
        "output": str(output),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
