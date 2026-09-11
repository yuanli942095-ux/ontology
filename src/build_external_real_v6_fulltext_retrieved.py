from __future__ import annotations

"""Build a draft revision using full official normative-document pairs.

Retrieval uses public event metadata and source-family alignment only. Event
candidates, formal policies, and private Oracle files are not read.
"""

import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from build_external_real_v5_retrieved_evidence import (
    read_csv,
    retrieve_windows,
    sha256,
    write_csv,
)


ROOT = Path(r"G:\LearnAI\ontology-evolution")
SRC = ROOT / "benchmark" / "external-real-v4-evidence-aligned"
DST = ROOT / "benchmark" / "external-real-v6-fulltext-retrieved-draft"
CACHE = ROOT / "data" / "external-real-v6-normative-cache"
REGISTRY = CACHE / "normative-source-registry.csv"
CACHE_MANIFEST = CACHE / "source-cache-manifest.csv"

OLD_NAME = "external-real-v4-evidence-aligned"
NEW_NAME = "external-real-v6-fulltext-retrieved-draft"


def reset() -> None:
    if DST.exists():
        raise FileExistsError(f"destination revision already exists: {DST}")
    shutil.copytree(SRC, DST)
    for path in DST.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".csv", ".json", ".md", ".owl", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8-sig").replace(OLD_NAME, NEW_NAME)
        path.write_text(text, encoding="utf-8")


def load_cache() -> dict[str, dict[str, str]]:
    return {
        row["url"]: row
        for row in read_csv(CACHE_MANIFEST)
        if row.get("status") == "SUCCESS" and Path(row.get("text_path", "")).is_file()
    }


def cached_text(cache: dict[str, dict[str, str]], url: str) -> tuple[str, str]:
    row = cache.get(url)
    if not row:
        return "", "cache unavailable"
    return Path(row["text_path"]).read_text(encoding="utf-8", errors="replace"), ""


def choose_family(
    event: dict[str, str],
    aligned_source_id: str,
    families: dict[str, dict[str, str]],
    by_domain: dict[str, list[dict[str, str]]],
    cache: dict[str, dict[str, str]],
) -> tuple[dict[str, str], list[str], list[str], float, float, list[str]]:
    options = [families[aligned_source_id]] if aligned_source_id in families else by_domain[event["domain"]]
    ranked: list[
        tuple[float, dict[str, str], list[str], list[str], float, float, list[str]]
    ] = []
    for family in options:
        current_text, current_error = cached_text(cache, family["source_url"])
        previous_text, previous_error = cached_text(cache, family["old_source_url"])
        current_windows, current_score, current_retrieval_error = retrieve_windows(
            event, current_text, limit=5
        )
        previous_windows, previous_score, previous_retrieval_error = retrieve_windows(
            event, previous_text, limit=5
        )
        errors = [
            value
            for value in (
                current_error,
                previous_error,
                current_retrieval_error,
                previous_retrieval_error,
            )
            if value
        ]
        temporal_weight = 0.8 if event["semantic_type"] == "TEMPORAL_VERSION" else 0.35
        combined = current_score + temporal_weight * previous_score
        ranked.append(
            (
                combined,
                family,
                current_windows,
                previous_windows,
                current_score,
                previous_score,
                errors,
            )
        )
    ranked.sort(key=lambda item: (-item[0], item[1]["source_id"]))
    _, family, current, previous, current_score, previous_score, errors = ranked[0]
    return family, current, previous, current_score, previous_score, errors


def retrieval_status(
    semantic_type: str,
    current_windows: list[str],
    previous_windows: list[str],
    current_score: float,
) -> str:
    if not current_windows:
        return "FAILED"
    if current_score < 0.08:
        return "LOW_RELEVANCE"
    if semantic_type == "TEMPORAL_VERSION" and not previous_windows:
        return "MISSING_PREVIOUS"
    return "RETRIEVED"


def build() -> list[dict[str, Any]]:
    input_dir = DST / "input"
    events_path = input_dir / "external-real-event-template.csv"
    docs_path = input_dir / "external-real-document-template.csv"
    events = read_csv(events_path)
    docs = read_csv(docs_path)
    docs_by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for doc in docs:
        docs_by_event[doc["event_id"]].append(doc)

    alignment_path = DST / "source-intake" / "external-real-v4-event-source-alignment.csv"
    alignments = {row["event_id"]: row for row in read_csv(alignment_path)}
    registry_rows = read_csv(REGISTRY)
    families = {row["source_id"]: row for row in registry_rows}
    by_domain: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in registry_rows:
        by_domain[row["domain"]].append(row)
    cache = load_cache()
    excerpts = DST / "documents" / "excerpts"
    document_dir = DST / "documents"
    retrievals: list[dict[str, Any]] = []

    for event in events:
        event_id = event["event_id"]
        aligned = alignments[event_id]["source_id"]
        family, current, previous, current_score, previous_score, errors = choose_family(
            event, aligned, families, by_domain, cache
        )
        status = retrieval_status(event["semantic_type"], current, previous, current_score)

        for doc in docs_by_event[event_id]:
            is_old = doc["document_id"].endswith("_OLD")
            windows = previous if is_old else current
            source_url = family["old_source_url"] if is_old else family["source_url"]
            source_title = family["old_source_title"] if is_old else family["source_title"]
            role = "previous" if is_old else "current"
            content = [
                f"Source title: {source_title}",
                f"Source URL: {source_url}",
                f"Publisher: {family['publisher']}",
                f"Event: {event_id}",
                f"Target: {event['subject_label']}",
                f"Window role: {role}",
                "",
                "Retrieved full-document source windows:",
                "",
                *[f"- {window}" for window in windows],
                "",
                "Retrieval boundary:",
                "- Query fields: public title, context, subject, predicate, domain, semantic type.",
                "- Candidate values, formal policy, and private Oracle were not used.",
            ]
            path = document_dir / doc["file_name"]
            path.write_text("\n".join(content) + "\n", encoding="utf-8")
            doc["source_title"] = source_title
            doc["source_url"] = source_url
            doc["source_type"] = "EXTERNAL_PUBLIC_FULL_DOCUMENT_RETRIEVAL"
            doc["document_type"] = "public_normative_full_document_window"
            doc["sha256"] = sha256(path)
            doc["notes"] = f"Full-document retrieval status={status}; role={role}."

        evidence = [
            f"# {event_id} Full-Document Evidence",
            "",
            f"- Domain: {event['domain']}",
            f"- Selected source family: {family['source_id']}",
            f"- Original alignment: {aligned}",
            f"- Current source title: {family['source_title']}",
            f"- Current source URL: {family['source_url']}",
            f"- Previous source title: {family['old_source_title']}",
            f"- Previous source URL: {family['old_source_url']}",
            f"- Event type: `{event['semantic_type']}`",
            f"- Target subject: {event['subject_label']}",
            f"- Target predicate: {event['predicate_label']}",
            "",
            "Previous full-document source windows:",
            "",
            *[f"- {window}" for window in previous],
            "",
            "Raw source excerpt windows:",
            "",
            *[f"- {window}" for window in current],
            "",
            "Retrieval boundary:",
            "",
            "- Candidate values, formal policy, and private Oracle were not used for retrieval.",
        ]
        (excerpts / f"{event_id}-evidence.md").write_text(
            "\n".join(evidence) + "\n", encoding="utf-8"
        )
        event["notes"] = (
            f"{event.get('notes', '')} Full-document retrieval={status}; "
            f"source_id={family['source_id']}; current_score={current_score:.4f}; "
            f"previous_score={previous_score:.4f}."
        ).strip()
        retrievals.append(
            {
                "event_id": event_id,
                "domain": event["domain"],
                "semantic_type": event["semantic_type"],
                "original_alignment": aligned,
                "selected_source_id": family["source_id"],
                "selection_mode": "ALIGNED" if aligned == family["source_id"] else "DOMAIN_RETRIEVAL",
                "current_url": family["source_url"],
                "previous_url": family["old_source_url"],
                "status": status,
                "current_score": current_score,
                "previous_score": previous_score,
                "current_windows": len(current),
                "previous_windows": len(previous),
                "errors": " | ".join(errors),
                "candidate_used": False,
                "formal_policy_used": False,
                "private_oracle_used": False,
            }
        )

    write_csv(events_path, events)
    write_csv(docs_path, docs)
    write_csv(DST / "source-intake" / "external-real-v6-fulltext-event-retrieval.csv", retrievals)
    return retrievals


def rename_intake_files() -> None:
    for path in list((DST / "source-intake").glob(f"*{OLD_NAME}*")):
        path.rename(path.with_name(path.name.replace(OLD_NAME, NEW_NAME)))


def write_readme(rows: list[dict[str, Any]]) -> None:
    counts = Counter(row["status"] for row in rows)
    selection = Counter(row["selection_mode"] for row in rows)
    text = f"""# External Real V6 Full-Text Retrieved Draft

Generated at {datetime.now(timezone.utc).isoformat()} UTC.

- Events: {len(rows)}
- Retrieval status: {dict(sorted(counts.items()))}
- Source selection: {dict(sorted(selection.items()))}

This is a draft revision, not a frozen benchmark.

- Retrieval uses public event metadata and source-family alignment only.
- Candidate values, formal policies, and private Oracle files are excluded.
- Independent evidence review is required before an event can be retained.
"""
    (DST / "README.md").write_text(text, encoding="utf-8")


def main() -> int:
    reset()
    rows = build()
    rename_intake_files()
    write_readme(rows)
    print(f"built={DST}")
    print(f"events={len(rows)}")
    print(f"retrieval={dict(sorted(Counter(row['status'] for row in rows).items()))}")
    print(f"selection={dict(sorted(Counter(row['selection_mode'] for row in rows).items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
