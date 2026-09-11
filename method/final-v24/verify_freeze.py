from __future__ import annotations

import hashlib
import json
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name("final-method-manifest.json")


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    failures = []
    for item in manifest["files"]:
        path = PROJECT / item["path"]
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "MISSING"
        if actual != item["sha256"]:
            failures.append({"path": item["path"], "expected": item["sha256"], "actual": actual})
    result = {"method_id": manifest["method_id"], "checked_files": len(manifest["files"]), "status": "PASS" if not failures else "FAIL", "failures": failures}
    print(json.dumps(result, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
