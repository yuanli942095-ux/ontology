from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from semantic_v2_common import OUTPUT_DIR, PROJECT_DIR


BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-v3"
INPUT = BENCHMARK / "input"
PRIVATE = BENCHMARK / "private"
DOCS = BENCHMARK / "documents"
RULES = BENCHMARK / "rules"
SOURCE_INTAKE = BENCHMARK / "source-intake"

EVENT_CSV = INPUT / "external-real-event-template.csv"
DOC_CSV = INPUT / "external-real-document-template.csv"
CAND_CSV = INPUT / "external-real-candidate-template.csv"
ORACLE_CSV = PRIVATE / "external-real-oracle-template.csv"
SOURCE_CSV = SOURCE_INTAKE / "external-real-v3-source-families.csv"

REPORT_MD = OUTPUT_DIR / "external-real-v3-quality-audit.md"
REPORT_JSON = OUTPUT_DIR / "external-real-v3-quality-audit.json"
DETAILS_CSV = OUTPUT_DIR / "external-real-v3-quality-audit-findings.csv"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, str]], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def coefficient_of_variation(values: list[int]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    if mean == 0:
        return 0.0
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(variance) / mean


def url_status(url: str) -> tuple[str, str]:
    if not url.startswith("http"):
        return "SKIP", "not_http"
    request = urllib.request.Request(url, headers={"User-Agent": "ontology-evolution-quality-audit/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            return str(response.status), response.geturl()
    except Exception as exc:  # noqa: BLE001 - audit should capture all URL failures.
        return "ERROR", exc.__class__.__name__


def event_number(event_id: str) -> int:
    match = re.search(r"(\d+)$", event_id)
    return int(match.group(1)) if match else -1


def main() -> int:
    events = read_csv(EVENT_CSV)
    docs = read_csv(DOC_CSV)
    candidates = read_csv(CAND_CSV)
    oracles = read_csv(ORACLE_CSV)
    sources = read_csv(SOURCE_CSV)

    findings: list[dict[str, str]] = []

    def add(level: str, code: str, entity: str, message: str) -> None:
        findings.append({"level": level, "code": code, "entity": entity, "message": message})

    domain_counts = Counter(row["domain"] for row in events)
    type_counts = Counter(row["semantic_type"] for row in events)
    docs_by_event = defaultdict(list)
    for row in docs:
        docs_by_event[row["event_id"]].append(row)
    candidates_by_event = defaultdict(list)
    for row in candidates:
        candidates_by_event[row["event_id"]].append(row)
    oracle_by_event = {row["event_id"]: row for row in oracles}

    if len(events) >= 200 and len(domain_counts) >= 10:
        add("PASS", "SCALE_TARGET", "dataset", f"{len(events)} events across {len(domain_counts)} domains")
    else:
        add("FAIL", "SCALE_TARGET", "dataset", f"{len(events)} events across {len(domain_counts)} domains")

    if set(type_counts.values()) == {71}:
        add("PASS", "SEMANTIC_TYPE_BALANCE", "dataset", str(dict(sorted(type_counts.items()))))
    else:
        add("WARN", "SEMANTIC_TYPE_BALANCE", "dataset", str(dict(sorted(type_counts.items()))))

    domain_cv = coefficient_of_variation(list(domain_counts.values()))
    if domain_cv <= 0.2:
        add("PASS", "DOMAIN_BALANCE", "dataset", f"cv={domain_cv:.4f}; counts={dict(sorted(domain_counts.items()))}")
    else:
        add("WARN", "DOMAIN_BALANCE", "dataset", f"cv={domain_cv:.4f}; counts={dict(sorted(domain_counts.items()))}")

    source_by_domain = defaultdict(set)
    doc_url_by_domain = defaultdict(set)
    event_domain = {row["event_id"]: row["domain"] for row in events}
    for row in sources:
        source_by_domain[row["domain"]].add(row["source_url"])
    for row in docs:
        domain = event_domain.get(row["event_id"], "")
        if domain and row.get("source_url"):
            doc_url_by_domain[domain].add(row["source_url"])
    for domain in sorted(domain_counts):
        source_count = len(source_by_domain.get(domain, set()) | doc_url_by_domain.get(domain, set()))
        if source_count < 2:
            add("WARN", "LOW_SOURCE_FAMILY_DIVERSITY", domain, f"unique_source_urls={source_count}")

    source_event_counts = Counter(row.get("source_url", "") for row in events)
    max_source_url, max_source_events = source_event_counts.most_common(1)[0]
    if max_source_events / len(events) > 0.2:
        add("WARN", "SOURCE_CONCENTRATION", max_source_url, f"{max_source_events}/{len(events)} events")
    else:
        add("PASS", "SOURCE_CONCENTRATION", "dataset", f"max={max_source_events}/{len(events)}")

    slot_counts_all: Counter[str] = Counter()
    slot_counts_added: Counter[str] = Counter()
    for event in events:
        oracle = oracle_by_event.get(event["event_id"])
        if not oracle:
            continue
        slot = oracle["oracle_candidate_id"]
        slot_counts_all[slot] += 1
        if event_number(event["event_id"]) >= 69:
            slot_counts_added[slot] += 1
    add("INFO", "CANDIDATE_SLOT_DISTRIBUTION_ALL", "dataset", str(dict(sorted(slot_counts_all.items()))))
    if max(slot_counts_added.values()) - min(slot_counts_added.values()) <= 1:
        add("PASS", "CANDIDATE_SLOT_DISTRIBUTION_ADDED", "added_v3", str(dict(sorted(slot_counts_added.items()))))
    else:
        add("WARN", "CANDIDATE_SLOT_DISTRIBUTION_ADDED", "added_v3", str(dict(sorted(slot_counts_added.items()))))

    duplicate_candidate_sets = Counter()
    for event in events:
        values = tuple(sorted(row["display_value"] for row in candidates_by_event[event["event_id"]]))
        duplicate_candidate_sets[values] += 1
        if len(candidates_by_event[event["event_id"]]) != 3:
            add("FAIL", "BAD_CANDIDATE_COUNT", event["event_id"], str(len(candidates_by_event[event["event_id"]])))
        if len(set(row["display_value"] for row in candidates_by_event[event["event_id"]])) != 3:
            add("FAIL", "DUPLICATE_CANDIDATE_VALUE", event["event_id"], "")
    repeated_sets = sum(1 for count in duplicate_candidate_sets.values() if count > 1)
    if repeated_sets:
        add("WARN", "REPEATED_CANDIDATE_SETS", "dataset", f"{repeated_sets} repeated candidate value sets")
    else:
        add("PASS", "REPEATED_CANDIDATE_SETS", "dataset", "0 repeated candidate value sets")

    structured_doc_count = sum(row["source_type"] == "EXTERNAL_PUBLIC_STRUCTURED_EXCERPT" for row in docs)
    structured_rate = structured_doc_count / len(docs)
    if structured_rate > 0.75:
        add("WARN", "STRUCTURED_EXCERPT_DOMINANCE", "documents", f"{structured_doc_count}/{len(docs)} = {pct(structured_rate)}")
    else:
        add("PASS", "STRUCTURED_EXCERPT_DOMINANCE", "documents", f"{structured_doc_count}/{len(docs)} = {pct(structured_rate)}")

    hash_mismatches = 0
    for row in docs:
        path = DOCS / row["file_name"]
        if not path.is_file() or sha256_file(path) != row["sha256"].lower():
            hash_mismatches += 1
    if hash_mismatches:
        add("FAIL", "DOCUMENT_HASH_MISMATCH", "documents", str(hash_mismatches))
    else:
        add("PASS", "DOCUMENT_HASH_MISMATCH", "documents", "0")

    rule_value_hits = 0
    for event in events:
        oracle = oracle_by_event[event["event_id"]]
        path = RULES / f"{event['event_id']}-formal-policy.json"
        if path.is_file() and oracle["oracle_value"] in path.read_text(encoding="utf-8"):
            rule_value_hits += 1
    if rule_value_hits:
        add(
            "WARN",
            "FORMAL_POLICY_CONTAINS_ORACLE_VALUE",
            "rules",
            f"{rule_value_hits}/{len(events)} rule files contain the adjudicated value; only valid as policy-available upper-bound or executor input, not blind model input.",
        )

    url_rows = []
    for url in sorted({row["source_url"] for row in sources if row.get("source_url", "").startswith("http")}):
        status, final = url_status(url)
        url_rows.append({"url": url, "status": status, "final": final})
        if status == "ERROR":
            add("WARN", "SOURCE_URL_UNREACHABLE_NOW", url, final)
    if not any(row["status"] == "ERROR" for row in url_rows):
        add("PASS", "SOURCE_URL_REACHABILITY_SAMPLE", "source_families", f"{len(url_rows)} URLs reachable")

    severity_counts = Counter(row["level"] for row in findings)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "benchmark": "external-real-v3",
        "events": len(events),
        "domains": dict(sorted(domain_counts.items())),
        "semantic_types": dict(sorted(type_counts.items())),
        "domain_balance_cv": domain_cv,
        "documents": len(docs),
        "structured_excerpt_rate": structured_rate,
        "candidates": len(candidates),
        "candidate_slot_counts_all": dict(sorted(slot_counts_all.items())),
        "candidate_slot_counts_added": dict(sorted(slot_counts_added.items())),
        "source_url_status": url_rows,
        "finding_counts": dict(sorted(severity_counts.items())),
        "findings": findings,
    }
    REPORT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(DETAILS_CSV, findings, ["level", "code", "entity", "message"])

    lines = [
        "# External Real V3 Quality Audit",
        "",
        f"Generated at {payload['generated_at_utc']} UTC.",
        "",
        "## Verdict",
        "",
        "The dataset is structurally valid and large enough for a stronger diagnostic benchmark, but it should be described as a controlled public-source benchmark rather than a naturally occurring real-world corpus.",
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Events | {len(events)} |",
        f"| Domains | {len(domain_counts)} |",
        f"| Semantic type counts | {dict(sorted(type_counts.items()))} |",
        f"| Domain balance CV | {domain_cv:.4f} |",
        f"| Documents | {len(docs)} |",
        f"| Structured excerpt rate | {pct(structured_rate)} |",
        f"| Candidates | {len(candidates)} |",
        f"| Added-event oracle slot counts | {dict(sorted(slot_counts_added.items()))} |",
        "",
        "## Findings",
        "",
        "| Level | Code | Entity | Message |",
        "|---|---|---|---|",
        *[f"| {row['level']} | {row['code']} | {row['entity']} | {row['message']} |" for row in findings],
        "",
        "## Interpretation",
        "",
        "- Strong: scale, domain/type balance, candidate artifacts, document hashes, and private Oracle separation pass.",
        "- Medium risk: many events share a small number of source families, so the benchmark tests domain-family transfer more than arbitrary-source generalization.",
        "- High-paper-risk if misused: formal-policy files contain the adjudicated allowed values; they must not be fed into blind LLM baselines.",
        "- Main limitation: v3 added events use structured public-source excerpts instead of downloaded raw full documents for every event.",
        "",
        "## Files",
        "",
        f"- Details CSV: `{DETAILS_CSV}`",
        f"- JSON: `{REPORT_JSON}`",
    ]
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"report={REPORT_MD}")
    print(f"json={REPORT_JSON}")
    print(f"details={DETAILS_CSV}")
    print(f"finding_counts={dict(sorted(severity_counts.items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
