from __future__ import annotations

"""Build a draft benchmark revision with event-level retrieved source windows.

Retrieval queries use public event metadata only: title, case context, subject,
predicate, domain, and semantic type. Candidate values, formal policies, and
private Oracle files are not read by the retrieval stage.
"""

import csv
import hashlib
import json
import math
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(r"G:\LearnAI\ontology-evolution")
SRC = ROOT / "benchmark" / "external-real-v4-evidence-aligned"
DST = ROOT / "benchmark" / "external-real-v5-retrieved-evidence-draft"
CACHE_MANIFEST = ROOT / "data" / "external-real-v4-source-cache" / "source-cache-manifest.csv"

OLD_NAME = "external-real-v4-evidence-aligned"
NEW_NAME = "external-real-v5-retrieved-evidence-draft"

STOP = {
    "about",
    "added",
    "after",
    "all",
    "and",
    "application",
    "case",
    "context",
    "current",
    "document",
    "event",
    "for",
    "framework",
    "general",
    "information",
    "new",
    "normative",
    "policy",
    "previous",
    "public",
    "regulation",
    "requirement",
    "rule",
    "scope",
    "source",
    "standard",
    "status",
    "target",
    "the",
    "version",
    "with",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def reset() -> None:
    if DST.exists():
        raise FileExistsError(f"destination revision already exists: {DST}")
    shutil.copytree(SRC, DST)
    for path in DST.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".csv", ".json", ".md", ".owl", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8-sig").replace(OLD_NAME, NEW_NAME)
        path.write_text(text, encoding="utf-8")


def tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{2,}", text.lower())
        if len(token) >= 2 and token not in STOP
    }


def query_text(event: dict[str, str]) -> str:
    return " ".join(
        event.get(key, "")
        for key in (
            "title",
            "case_context",
            "subject_label",
            "predicate_label",
            "domain",
            "semantic_type",
        )
    )


def split_units(text: str) -> list[str]:
    units: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip(" \t-*#")
        if len(line) < 35:
            continue
        pieces = re.split(r"(?<=[.!?。！？])\s+", line)
        for piece in pieces:
            piece = piece.strip()
            if not 35 <= len(piece) <= 1200:
                continue
            key = piece.lower()
            if key in seen:
                continue
            seen.add(key)
            units.append(piece)
    return units


def token_weight(token: str) -> float:
    if any(character.isdigit() for character in token):
        return 2.5
    if len(token) >= 10:
        return 1.6
    if len(token) >= 6:
        return 1.3
    return 1.0


def retrieve_windows(event: dict[str, str], text: str, limit: int = 4) -> tuple[list[str], float, str]:
    query = tokens(query_text(event))
    if not query:
        return [], 0.0, "empty query"
    subject_parts = [
        part.strip().lower()
        for part in re.split(r"::|/|\||：|:", event.get("subject_label", ""))
        if len(part.strip()) >= 4
    ]
    predicate = event.get("predicate_label", "").strip().lower()
    total_weight = sum(token_weight(token) for token in query)
    ranked: list[tuple[float, str]] = []
    for unit in split_units(text):
        unit_tokens = tokens(unit)
        overlap = query & unit_tokens
        score = sum(token_weight(token) for token in overlap) / max(total_weight, 1.0)
        lower = unit.lower()
        if predicate and predicate in lower:
            score += 0.20
        if any(part in lower for part in subject_parts):
            score += 0.20
        if overlap:
            score += 0.03 * math.log2(1 + len(overlap))
        if score > 0:
            ranked.append((score, unit))
    ranked.sort(key=lambda item: item[0], reverse=True)
    selected: list[str] = []
    selected_token_sets: list[set[str]] = []
    for score, unit in ranked:
        unit_tokens = tokens(unit)
        if any(
            len(unit_tokens & existing) / max(1, len(unit_tokens | existing)) > 0.75
            for existing in selected_token_sets
        ):
            continue
        selected.append(unit)
        selected_token_sets.append(unit_tokens)
        if len(selected) == limit:
            break
    top_score = ranked[0][0] if ranked else 0.0
    if not selected:
        return [], 0.0, "no relevant text unit"
    return selected, round(top_score, 4), ""


def load_cache() -> dict[str, dict[str, str]]:
    return {
        row["url"]: row
        for row in read_csv(CACHE_MANIFEST)
        if row.get("status") == "SUCCESS" and Path(row.get("text_path", "")).is_file()
    }


def load_families() -> dict[str, dict[str, str]]:
    path = (
        DST
        / "source-intake"
        / "external-real-v4-evidence-aligned-source-families.csv"
    )
    if not path.exists():
        matches = list((DST / "source-intake").glob("*source-families.csv"))
        if len(matches) != 1:
            raise RuntimeError("unable to resolve source-family CSV")
        path = matches[0]
    return {row["source_id"]: row for row in read_csv(path)}


def read_cached_text(cache: dict[str, dict[str, str]], url: str) -> tuple[str, str]:
    record = cache.get(url)
    if not record:
        return "", "cache unavailable"
    path = Path(record["text_path"])
    return path.read_text(encoding="utf-8", errors="replace"), ""


def build() -> list[dict[str, Any]]:
    input_dir = DST / "input"
    events_path = input_dir / "external-real-event-template.csv"
    docs_path = input_dir / "external-real-document-template.csv"
    events = read_csv(events_path)
    docs = read_csv(docs_path)
    docs_by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for doc in docs:
        docs_by_event[doc["event_id"]].append(doc)
    alignments = {
        row["event_id"]: row
        for row in read_csv(DST / "source-intake" / "external-real-v4-event-source-alignment.csv")
    }
    families = load_families()
    cache = load_cache()
    excerpts = DST / "documents" / "excerpts"
    document_dir = DST / "documents"
    retrievals: list[dict[str, Any]] = []

    for event in events:
        event_id = event["event_id"]
        alignment = alignments[event_id]
        if alignment["source_id"] == "INHERITED_EVENT_SPECIFIC":
            retrievals.append(
                {
                    "event_id": event_id,
                    "domain": event["domain"],
                    "source_id": alignment["source_id"],
                    "status": "INHERITED",
                    "current_score": 1.0,
                    "previous_score": 1.0,
                    "candidate_used": False,
                    "formal_policy_used": False,
                    "private_oracle_used": False,
                }
            )
            continue

        family = families[alignment["source_id"]]
        current_text, current_error = read_cached_text(cache, family["source_url"])
        previous_text, previous_error = read_cached_text(cache, family["old_source_url"])
        current_windows, current_score, current_retrieval_error = retrieve_windows(event, current_text)
        previous_windows, previous_score, previous_retrieval_error = retrieve_windows(event, previous_text)
        errors = [error for error in (current_error, previous_error, current_retrieval_error, previous_retrieval_error) if error]
        if not current_windows:
            status = "FAILED"
        elif current_score < 0.12:
            status = "LOW_RELEVANCE"
        elif event["semantic_type"] == "TEMPORAL_VERSION" and not previous_windows:
            status = "MISSING_PREVIOUS"
        else:
            status = "RETRIEVED"

        for doc in docs_by_event[event_id]:
            is_old = doc["document_id"].endswith("_OLD")
            windows = previous_windows if is_old else current_windows
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
                "Retrieved source excerpt windows:",
                "",
                *[f"- {window}" for window in windows],
                "",
                "Retrieval boundary:",
                "- Query fields: public title, context, subject, predicate, domain, semantic type.",
                "- Candidate values, formal policy, and private Oracle were not used.",
            ]
            doc_path = document_dir / doc["file_name"]
            doc_path.write_text("\n".join(content) + "\n", encoding="utf-8")
            doc["source_title"] = source_title
            doc["source_url"] = source_url
            doc["source_type"] = "EXTERNAL_PUBLIC_EVENT_RETRIEVED_WINDOW"
            doc["document_type"] = "public_normative_retrieved_excerpt"
            doc["sha256"] = sha256(doc_path)
            doc["notes"] = f"Deterministic event retrieval status={status}; role={role}."

        evidence = [
            f"# {event_id} Event-Retrieved Evidence",
            "",
            f"- Domain: {event['domain']}",
            f"- Source family: {family['source_id']}",
            f"- Current source title: {family['source_title']}",
            f"- Current source URL: {family['source_url']}",
            f"- Previous source title: {family['old_source_title']}",
            f"- Previous source URL: {family['old_source_url']}",
            f"- Event type: `{event['semantic_type']}`",
            f"- Target subject: {event['subject_label']}",
            f"- Target predicate: {event['predicate_label']}",
            "",
            "Previous source excerpt windows:",
            "",
            *[f"- {window}" for window in previous_windows],
            "",
            "Raw source excerpt windows:",
            "",
            *[f"- {window}" for window in current_windows],
            "",
            "Retrieval boundary:",
            "",
            "- Candidate values, formal policy, and private Oracle were not used for retrieval.",
        ]
        (excerpts / f"{event_id}-evidence.md").write_text("\n".join(evidence) + "\n", encoding="utf-8")
        event["notes"] = (
            f"{event.get('notes', '')} Evidence retrieval={status}; "
            f"current_score={current_score:.4f}; previous_score={previous_score:.4f}."
        ).strip()
        retrievals.append(
            {
                "event_id": event_id,
                "domain": event["domain"],
                "semantic_type": event["semantic_type"],
                "source_id": family["source_id"],
                "current_url": family["source_url"],
                "previous_url": family["old_source_url"],
                "status": status,
                "current_score": current_score,
                "previous_score": previous_score,
                "current_windows": len(current_windows),
                "previous_windows": len(previous_windows),
                "errors": " | ".join(errors),
                "candidate_used": False,
                "formal_policy_used": False,
                "private_oracle_used": False,
            }
        )

    write_csv(events_path, events)
    write_csv(docs_path, docs)
    write_csv(DST / "source-intake" / "external-real-v5-event-retrieval.csv", retrievals)
    return retrievals


def rename_intake_files() -> None:
    for path in list((DST / "source-intake").glob(f"*{OLD_NAME}*")):
        path.rename(path.with_name(path.name.replace(OLD_NAME, NEW_NAME)))


def write_readme(retrievals: list[dict[str, Any]]) -> None:
    counts = Counter(row["status"] for row in retrievals)
    text = f"""# External Real V5 Retrieved Evidence Draft

Generated at {datetime.now(timezone.utc).isoformat()} UTC.

- Events: {len(retrievals)}
- Retrieval status: {dict(sorted(counts.items()))}

This is a draft revision, not a frozen final benchmark.

- Retrieval uses public event metadata only.
- Candidate values, formal policies, and private Oracle files are excluded from retrieval.
- Events must pass the independent evidence-support audit and manual review before READY inclusion.
"""
    (DST / "README.md").write_text(text, encoding="utf-8")


def main() -> int:
    reset()
    retrievals = build()
    rename_intake_files()
    write_readme(retrievals)
    counts = Counter(row["status"] for row in retrievals)
    print(f"built={DST}")
    print(f"events={len(retrievals)}")
    print(f"retrieval={dict(sorted(counts.items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
