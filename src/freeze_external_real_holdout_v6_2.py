from __future__ import annotations

"""Freeze v6.2 after automatic + 70-event manual answerability gates."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from semantic_v2_common import PROJECT_DIR

NAME = "external-real-holdout-v6-2-direct-ir-blind"
BENCHMARK = PROJECT_DIR / "benchmark" / NAME
OUTPUT = PROJECT_DIR / "output" / NAME
AUTO = OUTPUT / "answerability/v6-2-automatic-answerability-summary.json"
MANUAL = OUTPUT / "answerability/v6-2-manual-review-sample70-summary.json"
PARENT_SHA = "c6cdb9430f3be3da5fed83c5ed15e25e518479e82622e9c6fce0525675c5cdee"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    auto = json.loads(AUTO.read_text(encoding="utf-8"))
    manual = json.loads(MANUAL.read_text(encoding="utf-8"))
    gates = {
        "automatic_value_leakage_zero": auto.get("value_leakage") == 0,
        "automatic_window_not_unique_zero": auto.get("window_not_unique") == 0,
        "automatic_repair_all_yes": (
            auto.get("repair_automatic_yes") == 240 and auto.get("repair_events") == 240
        ),
        "automatic_abstain_generic_zero": auto.get("abstain_generic") == 0,
        "manual_repair_yes": bool(manual.get("gates", {}).get("repair_pass")),
        "manual_no_change_yes": bool(manual.get("gates", {}).get("no_change_pass")),
        "manual_abstain_clear": bool(manual.get("gates", {}).get("abstain_pass")),
        "no_model_output": not (OUTPUT / "final-blind").exists() and not (OUTPUT / "raw-predicted-ir").exists(),
    }
    failed = [name for name, ok in gates.items() if not ok]
    if failed:
        raise SystemExit(f"freeze refused: {failed} auto={auto} manual_gates={manual.get('gates')}")
    frozen_at = datetime.now(timezone.utc).isoformat()
    protocol_path = BENCHMARK / "private/construction/v6-2-claim-protocol.json"
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    protocol.pop("freeze_withheld_because", None)
    protocol.update({
        "status": "READY_FOR_BLIND_EVALUATION",
        "frozen_at_utc": frozen_at,
        "automatic_repair_yes": auto["repair_automatic_yes"],
        "automatic_value_leakage": auto["value_leakage"],
        "automatic_window_not_unique": auto["window_not_unique"],
        "manual_sample70_repair_yes": manual["gates"]["repair_yes"],
        "manual_sample70_no_change_yes": manual["gates"]["no_change_yes"],
        "manual_sample70_abstain_clear": manual["gates"]["abstain_clear_evidence_fail"],
        "freeze_gate": (
            "Repair >=48/50 YES; NO_CHANGE >=9/10 YES; "
            "ABSTAIN >=8/10 target-clear with insufficient or conflicting evidence; "
            "automatic 0 VALUE_LEAKAGE and 0 WINDOW_NOT_UNIQUE"
        ),
    })
    protocol_path.write_text(json.dumps(protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (BENCHMARK / "README-v6-2.md").write_text(
        "# External Real Hold-out v6.2 Direct-IR Blind\n\n"
        "Status: `READY_FOR_BLIND_EVALUATION`\n\n"
        "Claim-level public target contract over v6.1 evidence and Gold. "
        "v6.1 remains diagnostic and is not a strict blind main result.\n\n"
        "Freeze gates passed: Repair sample >= 48/50 YES; NO_CHANGE >= 9/10 YES; "
        "ABSTAIN >= 8/10 target-clear with insufficient or conflicting evidence; "
        "automatic audit 0 VALUE_LEAKAGE and 0 WINDOW_NOT_UNIQUE.\n",
        encoding="utf-8",
    )
    files = []
    for path in sorted(BENCHMARK.rglob("*")):
        if path.is_file() and "source-cache" not in path.parts:
            files.append({
                "path": path.relative_to(PROJECT_DIR).as_posix(),
                "sha256": sha(path),
                "bytes": path.stat().st_size,
            })
    payload = {
        "schema_version": "external-real-holdout-v6-2-freeze-v1",
        "benchmark": NAME,
        "status": "READY_FOR_BLIND_EVALUATION",
        "frozen_at_utc": frozen_at,
        "parent_benchmark": "external-real-holdout-v6-1-direct-ir-blind",
        "parent_benchmark_manifest_sha256": PARENT_SHA,
        "method_id": "ECR-IR-GAMMA-R2B-V2-WINDOW-RESOLUTION",
        "freeze_gates": {
            "repair_sample_yes": ">=48/50",
            "no_change_sample_yes": ">=9/10",
            "abstain_sample_target_clear_evidence_fail": ">=8/10",
            "automatic_value_leakage": 0,
            "automatic_window_not_unique": 0,
        },
        "automatic_audit": auto,
        "manual_review": manual,
        "files": files,
    }
    raw = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    payload["manifest_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    output = OUTPUT / f"{NAME}-freeze-manifest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "benchmark": NAME,
        "status": payload["status"],
        "files": len(files),
        "manifest": output.relative_to(PROJECT_DIR).as_posix(),
        "manifest_sha256": payload["manifest_sha256"],
        "gates": gates,
    }
    (OUTPUT / f"{NAME}-freeze-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
