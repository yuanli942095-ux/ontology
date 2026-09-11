from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "method/final-v25-natural-literal"
FILES = [
    "src/rfc213_direct_repair_ir_v25.py",
    "src/run_ecr_repair_post_freeze_blind_v1_v25_posthoc.py",
    "tests/test_rfc213_direct_repair_ir_v25.py",
    "src/rfc213_direct_repair_ir_v24.py",
    "src/rfc213_direct_repair_ir_v23.py",
    "src/rfc213_direct_repair_ir_v2.py",
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    payload = {
        "method_id": "ECR-REPAIR-V2.5-NATURAL-LITERAL-ADAPTER",
        "status": "FROZEN_POST_HOC_ADAPTER",
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "parent_method_id": "ECR-REPAIR-V2.4-FINAL",
        "change_scope": "Preserve predicted natural-language replacement literal after V2.4 grounding; correct reporting partition groups.",
        "development_timing": "Specified after aggregate V2.4 method-benchmark incompatibility was observed.",
        "claim_boundary": "Diagnostic post-hoc compatibility analysis only; requires a new untouched benchmark for confirmatory reporting.",
        "files": [{"path": name, "sha256": sha(ROOT / name)} for name in FILES],
    }
    (OUT / "method-manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "method_id": payload["method_id"], "files": len(FILES)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
