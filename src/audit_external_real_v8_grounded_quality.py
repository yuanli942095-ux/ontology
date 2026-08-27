from __future__ import annotations

"""Quality audit for staged external-real-v8-grounded files."""

import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from external_real_v8_layout import BenchmarkLayout, construction_paths
from semantic_v2_common import OUTPUT_DIR, PROJECT_DIR
from validate_external_real_v8_grounded import isolation_findings


BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-v8-grounded"
LAYOUT = BenchmarkLayout(BENCHMARK)


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def main() -> int:
    paths = construction_paths(BENCHMARK)
    events = [row for row in read_csv(paths["event_csv"]) if row.get("status", "").upper() == "READY"]
    docs = read_csv(paths["document_csv"])
    families = read_csv(LAYOUT.family_csv)
    retrievals = {row["event_id"]: row for row in read_csv(LAYOUT.retrieval_csv)}
    findings = isolation_findings()
    structured = sum(row.get("source_type") == "EXTERNAL_PUBLIC_STRUCTURED_EXCERPT" for row in docs)
    family_counts = Counter(row["domain"] for row in families)
    domain_counts = Counter(row["domain"] for row in events)
    type_counts = Counter(row["semantic_type"] for row in events)
    fallback = sum(str(row.get("fallback_used", "")).lower() == "true" for row in retrievals.values())
    semantic_pass = sum(str(row.get("semantic_support", "")).upper() in {"PASS", "SEMANTIC_PASS"} for row in events)
    payload = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ready_events": len(events),
        "domains": dict(domain_counts),
        "semantic_types": dict(type_counts),
        "source_families_per_domain": dict(family_counts),
        "structured_excerpt_rate": structured / len(docs) if docs else 0.0,
        "fallback_used_count": fallback,
        "isolation_errors": len(findings),
        "semantic_support_pass": semantic_pass,
        "public_candidate_csv_present": bool(LAYOUT.public_candidate_paths()),
        "repair_stage_candidate_csv_present": LAYOUT.candidate_csv.is_file(),
        "private_dir_present": LAYOUT.private.exists(),
        "candidate_csv_present": bool(LAYOUT.public_candidate_paths()),
        "curation_not_inference": True,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUTPUT_DIR / "external-real-v8-grounded-quality-audit.json"
    md_path = OUTPUT_DIR / "external-real-v8-grounded-quality-audit.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# external-real-v8-grounded quality",
        "",
        f"- READY: {payload['ready_events']}",
        f"- semantic_support PASS: {payload['semantic_support_pass']}",
        f"- structured excerpt rate: {payload['structured_excerpt_rate']:.6f}",
        f"- isolation errors: {payload['isolation_errors']}",
        f"- fallback_used: {payload['fallback_used_count']}",
        f"- public candidate CSV: {payload['public_candidate_csv_present']}",
        f"- repair-stage candidate CSV: {payload['repair_stage_candidate_csv_present']}",
        "",
        "## Families per domain",
        "",
    ]
    for domain, count in sorted(family_counts.items()):
        lines.append(f"- {domain}: {count}")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    fail = findings or payload["public_candidate_csv_present"] or payload["fallback_used_count"]
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
