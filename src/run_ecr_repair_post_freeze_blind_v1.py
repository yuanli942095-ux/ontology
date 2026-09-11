from __future__ import annotations

"""Run frozen ECR-Repair V2.4 on the frozen 80-event post-method blind set."""

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

import run_external_real_holdout_v6_direct_ir_blind as engine
from semantic_v2_common import PROJECT_DIR, write_csv


BENCHMARK = PROJECT_DIR / "benchmark/ecr-repair-post-freeze-blind-v1"
BENCHMARK_MANIFEST = BENCHMARK / "freeze-manifest.json"
METHOD_MANIFEST = PROJECT_DIR / "method/final-v24/final-method-manifest.json"
OUTPUT = PROJECT_DIR / "output/ecr-repair-post-freeze-blind-v1/final-v24-r5"
SEEDS = (20260829, 20260830, 20260831, 20260901, 20260902)


def load_local_env() -> None:
    path = PROJECT_DIR / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_manifest_files(manifest: dict, base: Path, project_relative: bool) -> None:
    for item in manifest.get("files", []):
        path = PROJECT_DIR / item["path"] if project_relative else base / item["path"]
        if not path.is_file() or sha256(path) != item["sha256"]:
            raise SystemExit(f"frozen file mismatch: {path}")


def verify() -> tuple[dict, dict[str, dict], dict[str, dict]]:
    benchmark_manifest = json.loads(BENCHMARK_MANIFEST.read_text(encoding="utf-8"))
    if benchmark_manifest.get("status") != "FROZEN_POST_METHOD_BLIND_TEST" or benchmark_manifest.get("event_count") != 80:
        raise SystemExit("benchmark is not the frozen 80-event release")
    unsigned = {key: value for key, value in benchmark_manifest.items() if key != "manifest_sha256"}
    actual_manifest_sha = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if actual_manifest_sha != benchmark_manifest.get("manifest_sha256"):
        raise SystemExit("benchmark manifest self-hash mismatch")
    verify_manifest_files(benchmark_manifest, BENCHMARK, False)

    method = json.loads(METHOD_MANIFEST.read_text(encoding="utf-8"))
    if method.get("method_id") != "ECR-REPAIR-V2.4-FINAL" or method.get("status") != "FROZEN_FINAL_METHOD":
        raise SystemExit("V2.4 final method manifest is invalid")
    verify_manifest_files(method, PROJECT_DIR, True)

    events = engine.load_events(BENCHMARK)
    gold_path = BENCHMARK / "private/oracle/gold.jsonl"
    gold = {row["event_id"]: row for row in (json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines() if line.strip())}
    if len(events) != 80 or set(events) != set(gold):
        raise SystemExit("event/Oracle coverage mismatch")
    return method, events, gold


def v24_literal_contract_issues(gold: dict[str, dict]) -> list[str]:
    issues = []
    for event_id, row in sorted(gold.items()):
        if row.get("decision") != "REPAIR":
            continue
        old_lexical = str(row.get("target", {}).get("old_value", {}).get("lexical", ""))
        new_lexical = str(row.get("replacement", {}).get("new_value", {}).get("lexical", ""))
        if "=" not in old_lexical or "=" not in new_lexical:
            issues.append(event_id)
    return issues


def prepare(output: Path, method: dict, events: dict[str, dict], runs: int) -> None:
    if not 1 <= runs <= len(SEEDS):
        raise SystemExit(f"--runs-per-event must be between 1 and {len(SEEDS)}")
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for event_id, event in sorted(events.items()):
        for run, seed in enumerate(SEEDS[:runs], 1):
            rows.append({"event_id": event_id, "run": run, "seed": seed, "semantic_type": "WITHHELD", "domain": event["domain"]})
    write_csv(engine.manifest_path(output), rows)
    protocol = {
        "status": "REGISTERED_BEFORE_MODEL_EXECUTION",
        "benchmark": BENCHMARK.name,
        "benchmark_manifest_sha256": json.loads(BENCHMARK_MANIFEST.read_text(encoding="utf-8"))["manifest_sha256"],
        "method": method["method_id"],
        "method_manifest_file_sha256": sha256(METHOD_MANIFEST),
        "backend": method["runtime_configuration"]["llm_backend"],
        "observed_model_expected": method["runtime_configuration"]["observed_model"],
        "prompt_variant": "v6.2-claim-level",
        "resolver": "v24",
        "runs_per_event": runs,
        "seeds": list(SEEDS[:runs]),
        "events": len(events),
        "attempts": len(events) * runs,
        "candidate_blind_generation": True,
        "oracle_blind_generation": True,
        "semantic_type_withheld_during_generation": True,
    }
    (output / "experiment-protocol.json").write_text(json.dumps(protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(protocol, ensure_ascii=False, indent=2))


def quarantine_failed_raw(output: Path) -> int:
    raw_dir = output / "raw-predicted-ir"
    quarantine = output / "transport-errors-pre-retry"
    moved = 0
    for path in sorted(raw_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if payload.get("status") == "GENERATED":
            continue
        quarantine.mkdir(parents=True, exist_ok=True)
        destination = quarantine / path.name
        if destination.exists():
            destination = quarantine / f"{path.stem}-retry{moved + 1}.json"
        shutil.move(str(path), str(destination))
        moved += 1
    return moved


def main() -> int:
    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step", choices=("preflight", "prepare", "generate", "evaluate"), required=True)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--llm-backend", choices=("deepseek_api", "ollama"), default="deepseek_api")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--runs-per-event", type=int, default=5)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    method, events, gold = verify()
    cfg = {"prompt": "v6.2-claim-level"}

    compatibility_issues = v24_literal_contract_issues(gold)
    if compatibility_issues:
        audit = {
            "status": "INCOMPATIBLE_FROZEN_METHOD_BENCHMARK_CONTRACT",
            "method_id": method["method_id"],
            "benchmark": BENCHMARK.name,
            "affected_repair_events": len(compatibility_issues),
            "event_ids": compatibility_issues,
            "reason": (
                "Frozen V2.4 source-window resolution emits dimension=slug literals. "
                "This benchmark's adjudicated UPDATE_LITERAL Gold uses natural-language literals. "
                "Exact Gamma/OWL/CQ evaluation is therefore not comparable without a new method "
                "version or a separately frozen encoded benchmark representation."
            ),
        }
        output.mkdir(parents=True, exist_ok=True)
        (output / "METHOD-BENCHMARK-INCOMPATIBILITY.json").write_text(
            json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if args.step in {"preflight", "prepare", "generate", "evaluate"}:
            print(json.dumps(audit, ensure_ascii=False, indent=2))
            return 2

    if args.step == "preflight":
        print(json.dumps({"status": "READY", "events": len(events), "attempts": len(events) * args.runs_per_event,
                          "benchmark_manifest_sha256": json.loads(BENCHMARK_MANIFEST.read_text(encoding="utf-8"))["manifest_sha256"],
                          "method_id": method["method_id"], "seeds": list(SEEDS[:args.runs_per_event])}, indent=2))
    elif args.step == "prepare":
        prepare(output, method, events, args.runs_per_event)
    elif args.step == "generate":
        if not engine.manifest_path(output).is_file():
            raise SystemExit("run --step prepare first")
        if args.resume:
            moved = quarantine_failed_raw(output)
            if moved:
                print(f"quarantined {moved} non-GENERATED raw artifacts before resume", flush=True)
        engine.generate(args, BENCHMARK, output, events, cfg)
    else:
        if not engine.manifest_path(output).is_file():
            raise SystemExit("run --step prepare first")
        engine.load_gold = lambda _benchmark: gold
        args.resolver = "v24"
        engine.evaluate(args, BENCHMARK, output, events)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
