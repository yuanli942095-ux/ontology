from __future__ import annotations

"""Verifier thresholds for staged external-real-v8-grounded."""

import csv
import hashlib
import json
from pathlib import Path

from external_real_v8_layout import BenchmarkLayout


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
BENCH = ROOT / "benchmark" / "external-real-v8-grounded"
LAYOUT = BenchmarkLayout(BENCH)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    errors: list[str] = []
    disposition_path = OUT / "external-real-v8-grounded" / "disposition.csv"
    validation_path = OUT / "external-real-v8-grounded-validation-summary.json"
    freeze_path = next(OUT.glob("external-real-v8-grounded-freeze-manifest*.json"), None)
    quality_path = OUT / "external-real-v8-grounded-quality-audit.json"

    if not disposition_path.is_file():
        errors.append("missing disposition.csv")
        print("\n".join(errors))
        return 1

    rows = read_csv(disposition_path)
    keep = [row for row in rows if row.get("disposition") == "KEEP"]
    if any(str(row.get("semantic_support", "")).upper() not in {"PASS", "SEMANTIC_PASS"} for row in keep):
        errors.append("READY semantic_support!=PASS")
    if any(row.get("retrieval_status") != "RETRIEVAL_READY" for row in keep):
        errors.append("READY retrieval is not RETRIEVAL_READY")
    if any(str(row.get("support_checked_before_model_run", "")).lower() != "true" for row in keep):
        errors.append("support_checked_before_model_run is not true")
    if any(str(row.get("lexical_prefilter_status", "")).upper() == "WARN" and row.get("disposition") == "KEEP" and str(row.get("semantic_support", "")).upper() not in {"PASS", "SEMANTIC_PASS"} for row in rows):
        errors.append("WARN kept without semantic PASS")

    if validation_path.is_file():
        validation = read_json(validation_path)
        if not validation.get("isolation_pass"):
            errors.append("isolation pass!=1")
        if int(validation.get("error_count", 1)) != 0:
            errors.append(f"validation errors={validation.get('error_count')}")
        if int(validation.get("ready_events", 0)) < int(validation.get("min_ready_events", 150)):
            errors.append(f"ready events {validation.get('ready_events')} < {validation.get('min_ready_events')}")
        if validation.get("public_candidate_csv"):
            errors.append("validation: public candidate CSV")
    else:
        errors.append("missing validation summary")

    if quality_path.is_file():
        quality = read_json(quality_path)
        if quality.get("public_candidate_csv_present") or quality.get("candidate_csv_present"):
            errors.append("quality: public candidate CSV present")
        if float(quality.get("structured_excerpt_rate", 1)) > 0.25:
            errors.append("structured excerpt rate too high")
        if int(quality.get("isolation_errors", 1)) != 0:
            errors.append("quality isolation errors")
        if not quality.get("repair_stage_candidate_csv_present"):
            errors.append("quality: repair-stage candidates missing")
    else:
        errors.append("missing quality audit")

    if freeze_path and freeze_path.is_file():
        freeze = read_json(freeze_path)
        files = freeze.get("files") or freeze.get("public_files", [])
        mismatch = 0
        for row in files:
            path = ROOT / str(row.get("path", ""))
            if not row.get("path"):
                continue
            if not path.is_file():
                mismatch += 1
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != row.get("sha256"):
                mismatch += 1
        if mismatch:
            errors.append(f"freeze mismatch={mismatch}")
        public_candidate = [
            row
            for row in files
            if row.get("privacy") == "public"
            and "candidate" in str(row.get("path", "")).replace("\\", "/").lower()
        ]
        if public_candidate:
            errors.append("freeze lists candidate files as public")
    else:
        errors.append("missing freeze manifest")

    if LAYOUT.public_candidate_paths():
        errors.append("public candidate CSV exists")
    if not LAYOUT.candidate_csv.is_file():
        errors.append("repair-stage candidate CSV missing")
    if not LAYOUT.oracle_csv.is_file():
        errors.append("private oracle CSV missing")

    retrieval = LAYOUT.retrieval_csv
    events = LAYOUT.event_csv
    if retrieval.is_file() and events.is_file():
        ready_ids = {
            row["event_id"]
            for row in read_csv(events)
            if row.get("status", "").upper() == "READY"
        }
        for row in read_csv(retrieval):
            if row["event_id"] not in ready_ids:
                continue
            for flag in ("fallback_used", "candidate_used", "oracle_used", "note_used"):
                if str(row.get(flag, "")).lower() == "true":
                    errors.append(f"{flag} on {row['event_id']}")
                    break

    if errors:
        print("VERIFY FAIL")
        for item in errors:
            print(item)
        return 1
    print("VERIFY PASS")
    print(f"keep={len(keep)}")
    print("semantic_support_pass=1")
    print("isolation_pass=1")
    print("freeze_mismatch=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
