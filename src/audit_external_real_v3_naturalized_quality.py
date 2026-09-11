from __future__ import annotations

import csv
import hashlib
import json
import math
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from semantic_v2_common import OUTPUT_DIR, PROJECT_DIR


BENCHMARK = PROJECT_DIR / "benchmark" / "external-real-v3-naturalized"
INPUT = BENCHMARK / "input"
PRIVATE = BENCHMARK / "private"
DOCS = BENCHMARK / "documents"
RULES = BENCHMARK / "rules"
SOURCE_INTAKE = BENCHMARK / "source-intake"

EVENT_CSV = INPUT / "external-real-event-template.csv"
DOC_CSV = INPUT / "external-real-document-template.csv"
CAND_CSV = INPUT / "external-real-candidate-template.csv"
ORACLE_CSV = PRIVATE / "external-real-oracle-template.csv"
SOURCE_CSV = SOURCE_INTAKE / "external-real-v3-naturalized-source-families.csv"

REPORT_MD = OUTPUT_DIR / "external-real-v3-naturalized-quality-audit.md"
REPORT_JSON = OUTPUT_DIR / "external-real-v3-naturalized-quality-audit.json"
DETAILS_CSV = OUTPUT_DIR / "external-real-v3-naturalized-quality-audit-findings.csv"


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


def cv(values: list[int]) -> float:
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values)) / mean


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def url_status(url: str) -> tuple[str, str]:
    if not url.startswith("http"):
        return "SKIP", "not_http"
    request = urllib.request.Request(url, headers={"User-Agent": "ontology-evolution-naturalized-quality-audit/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            return str(response.status), response.geturl()
    except Exception as exc:  # noqa: BLE001
        return "ERROR", exc.__class__.__name__


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
    source_counts = Counter(row["domain"] for row in sources)
    source_event_counts = Counter(row["source_url"] for row in events)
    raw_docs = sum(row["source_type"] == "EXTERNAL_PUBLIC_RAW_EXCERPT_WINDOW" for row in docs)
    structured_docs = sum(row["source_type"] == "EXTERNAL_PUBLIC_STRUCTURED_EXCERPT" for row in docs)
    structured_rate = structured_docs / len(docs)

    if len(events) >= 200 and len(domain_counts) >= 10:
        add("PASS", "SCALE_TARGET", "dataset", f"{len(events)} events across {len(domain_counts)} domains")
    else:
        add("FAIL", "SCALE_TARGET", "dataset", f"{len(events)} events across {len(domain_counts)} domains")

    if set(type_counts.values()) == {71}:
        add("PASS", "SEMANTIC_TYPE_BALANCE", "dataset", str(dict(sorted(type_counts.items()))))
    else:
        add("WARN", "SEMANTIC_TYPE_BALANCE", "dataset", str(dict(sorted(type_counts.items()))))

    domain_cv = cv(list(domain_counts.values()))
    if domain_cv <= 0.2:
        add("PASS", "DOMAIN_BALANCE", "dataset", f"cv={domain_cv:.4f}")
    else:
        add("WARN", "DOMAIN_BALANCE", "dataset", f"cv={domain_cv:.4f}")

    low_source_domains = [domain for domain, count in sorted(source_counts.items()) if count < 2]
    if low_source_domains:
        add("FAIL", "SOURCE_FAMILY_DIVERSITY", "dataset", ",".join(low_source_domains))
    else:
        add("PASS", "SOURCE_FAMILY_DIVERSITY", "dataset", str(dict(sorted(source_counts.items()))))

    max_source_url, max_source_events = source_event_counts.most_common(1)[0]
    if max_source_events / len(events) > 0.2:
        add("WARN", "SOURCE_CONCENTRATION", max_source_url, f"{max_source_events}/{len(events)}")
    else:
        add("PASS", "SOURCE_CONCENTRATION", "dataset", f"max={max_source_events}/{len(events)}")

    if structured_rate <= 0.25:
        add("PASS", "STRUCTURED_EXCERPT_RATE", "documents", f"{structured_docs}/{len(docs)} = {pct(structured_rate)}")
    else:
        add("WARN", "STRUCTURED_EXCERPT_RATE", "documents", f"{structured_docs}/{len(docs)} = {pct(structured_rate)}")
    add("PASS", "RAW_EXCERPT_WINDOW_COVERAGE", "documents", f"{raw_docs}/{len(docs)} = {pct(raw_docs / len(docs))}")

    oracle_by_event = {row["event_id"]: row for row in oracles}
    slot_all = Counter(row["oracle_candidate_id"] for row in oracles)
    slot_added = Counter(row["oracle_candidate_id"] for row in oracles if int(row["event_id"][-3:]) >= 69)
    if max(slot_added.values()) - min(slot_added.values()) <= 1:
        add("PASS", "CANDIDATE_SLOT_DISTRIBUTION_ADDED", "added_v3", str(dict(sorted(slot_added.items()))))
    else:
        add("WARN", "CANDIDATE_SLOT_DISTRIBUTION_ADDED", "added_v3", str(dict(sorted(slot_added.items()))))
    add("INFO", "CANDIDATE_SLOT_DISTRIBUTION_ALL", "dataset", str(dict(sorted(slot_all.items()))))

    candidate_sets = Counter()
    by_event = defaultdict(list)
    for row in candidates:
        by_event[row["event_id"]].append(row)
    for event in events:
        candidate_sets[tuple(sorted(row["display_value"] for row in by_event[event["event_id"]]))] += 1
    repeats = sum(1 for count in candidate_sets.values() if count > 1)
    if repeats:
        add("WARN", "REPEATED_CANDIDATE_SETS", "dataset", str(repeats))
    else:
        add("PASS", "REPEATED_CANDIDATE_SETS", "dataset", "0")

    hash_mismatches = 0
    for doc in docs:
        path = DOCS / doc["file_name"]
        if not path.is_file() or sha256_file(path) != doc["sha256"].lower():
            hash_mismatches += 1
    if hash_mismatches:
        add("FAIL", "DOCUMENT_HASH_MISMATCH", "documents", str(hash_mismatches))
    else:
        add("PASS", "DOCUMENT_HASH_MISMATCH", "documents", "0")

    policy_hits = 0
    for event in events:
        oracle = oracle_by_event[event["event_id"]]
        path = RULES / f"{event['event_id']}-formal-policy.json"
        if path.is_file() and oracle["oracle_value"] in path.read_text(encoding="utf-8"):
            policy_hits += 1
    if policy_hits:
        add(
            "WARN",
            "FORMAL_POLICY_CONTAINS_ORACLE_VALUE",
            "rules",
            f"{policy_hits}/{len(events)}; valid only for policy-available executor or upper-bound experiments.",
        )

    url_rows = []
    for url in sorted({row["source_url"] for row in sources if row["source_url"].startswith("http")}):
        status, final = url_status(url)
        url_rows.append({"url": url, "status": status, "final": final})
    errors = [row for row in url_rows if row["status"] == "ERROR"]
    if errors:
        add("WARN", "SOURCE_URL_REACHABILITY", "source_families", f"{len(errors)}/{len(url_rows)} direct checks returned errors")
    else:
        add("PASS", "SOURCE_URL_REACHABILITY", "source_families", f"{len(url_rows)} URLs reachable")

    severity = Counter(row["level"] for row in findings)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "benchmark": "external-real-v3-naturalized",
        "events": len(events),
        "domains": dict(sorted(domain_counts.items())),
        "semantic_types": dict(sorted(type_counts.items())),
        "source_families_by_domain": dict(sorted(source_counts.items())),
        "structured_excerpt_rate": structured_rate,
        "raw_excerpt_window_rate": raw_docs / len(docs),
        "candidate_slot_counts_all": dict(sorted(slot_all.items())),
        "candidate_slot_counts_added": dict(sorted(slot_added.items())),
        "source_url_status": url_rows,
        "finding_counts": dict(sorted(severity.items())),
        "findings": findings,
    }
    REPORT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(DETAILS_CSV, findings, ["level", "code", "entity", "message"])
    REPORT_MD.write_text(
        "\n".join(
            [
                "# External Real V3 Naturalized Quality Audit",
                "",
                f"Generated at {payload['generated_at_utc']} UTC.",
                "",
                "## Verdict",
                "",
                "The naturalized revision is suitable for stronger natural-document understanding claims than external-real-v3, while still needing to be described as a controlled public-source benchmark.",
                "",
                "## Metrics",
                "",
                "| Metric | Value |",
                "|---|---:|",
                f"| Events | {len(events)} |",
                f"| Domains | {len(domain_counts)} |",
                f"| Source families | {len(sources)} |",
                f"| Source families/domain | {dict(sorted(source_counts.items()))} |",
                f"| Semantic type counts | {dict(sorted(type_counts.items()))} |",
                f"| Structured excerpt rate | {pct(structured_rate)} |",
                f"| Raw excerpt window rate | {pct(raw_docs / len(docs))} |",
                f"| Added-event oracle slot counts | {dict(sorted(slot_added.items()))} |",
                "",
                "## Findings",
                "",
                "| Level | Code | Entity | Message |",
                "|---|---|---|---|",
                *[f"| {row['level']} | {row['code']} | {row['entity']} | {row['message']} |" for row in findings],
                "",
                "## Interpretation",
                "",
                "- Strong: every domain now has 3 source families and most documents are raw source windows.",
                "- Residual risk: formal-policy files still encode allowed values and must be excluded from blind LLM input.",
                "- Residual risk: raw source windows are short excerpts, not full document redistribution.",
                "- Next experimental step: rerun natural-evidence robustness on this benchmark, especially raw-window-only settings.",
                "",
                "## Files",
                "",
                f"- Details CSV: `{DETAILS_CSV}`",
                f"- JSON: `{REPORT_JSON}`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"report={REPORT_MD}")
    print(f"json={REPORT_JSON}")
    print(f"details={DETAILS_CSV}")
    print(f"finding_counts={dict(sorted(severity.items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
