from __future__ import annotations

"""Build a source-family-aligned revision of external-real-v3-naturalized.

The v3-naturalized builder rotated source families by event number. This
revision instead aligns each v3-added event to its event source card, removes
candidate option blocks from evidence notes, and preserves v3 as an immutable
input revision.
"""

import csv
import hashlib
import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


ROOT = Path(r"G:\LearnAI\ontology-evolution")
SRC = ROOT / "benchmark" / "external-real-v3-naturalized"
DST = ROOT / "benchmark" / "external-real-v4-evidence-aligned"
OUTPUT = ROOT / "output"

OLD_NAME = "external-real-v3-naturalized"
NEW_NAME = "external-real-v4-evidence-aligned"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
        text = path.read_text(encoding="utf-8-sig")
        text = text.replace(OLD_NAME, NEW_NAME)
        path.write_text(text, encoding="utf-8")


def normalized_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{2,}", text.lower())
        if len(token) >= 2
    }


def parse_source_windows(path: Path) -> tuple[str, ...]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    windows: list[str] = []
    started = False
    for line in lines:
        if line.strip().lower() == "raw source windows:":
            started = True
            continue
        if not started:
            continue
        match = re.match(r"\s*\d+\.\s*(.+)", line)
        if match:
            windows.append(match.group(1).strip())
    if not windows:
        raise RuntimeError(f"no raw source windows in {path}")
    return tuple(windows)


def load_families() -> dict[str, list[dict[str, Any]]]:
    intake = DST / "source-intake"
    family_csv = intake / "external-real-v3-naturalized-source-families.csv"
    if not family_csv.exists():
        family_csv = intake / "external-real-v4-evidence-aligned-source-families.csv"
    rows = read_csv(family_csv)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    source_evidence = intake / "source-evidence"
    for row in rows:
        source_id = row["source_id"]
        family = {
            **row,
            "raw_windows": parse_source_windows(source_evidence / f"{source_id}.md"),
        }
        grouped[row["domain"]].append(family)
    return grouped


def source_card_title(path: Path) -> str:
    first = path.read_text(encoding="utf-8-sig").splitlines()[0]
    return re.sub(r"^Source family:\s*", "", first).strip()


def alignment_score(card_title: str, event: dict[str, str], family: dict[str, Any]) -> float:
    card = card_title.lower()
    names = [family["source_title"], family["old_source_title"]]
    sequence = max(SequenceMatcher(None, card, str(name).lower()).ratio() for name in names)
    card_tokens = normalized_tokens(card_title)
    family_tokens = normalized_tokens(" ".join(str(name) for name in names))
    event_tokens = normalized_tokens(
        " ".join(
            event.get(key, "")
            for key in ("title", "case_context", "subject_label", "predicate_label")
        )
    )
    overlap = len(card_tokens & family_tokens) / max(1, len(card_tokens))
    event_overlap = len(event_tokens & family_tokens) / max(1, len(event_tokens))
    return 0.65 * sequence + 0.25 * overlap + 0.10 * event_overlap


def choose_family(
    event: dict[str, str],
    families: dict[str, list[dict[str, Any]]],
    card_path: Path,
) -> tuple[dict[str, Any], str, float]:
    card_title = source_card_title(card_path)
    options = families[event["domain"]]
    ranked = sorted(
        ((alignment_score(card_title, event, family), family) for family in options),
        key=lambda item: item[0],
        reverse=True,
    )
    score, family = ranked[0]
    return family, card_title, score


def remove_candidate_tail(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines():
        lower = line.strip().lower()
        if "public candidate values" in lower or lower in {"candidate values", "candidate values:"}:
            break
        if re.search(r"\bCAND_\d+\b", line, flags=re.I):
            continue
        kept.append(line)
    return "\n".join(kept).strip() + "\n"


def align_events() -> list[dict[str, Any]]:
    input_dir = DST / "input"
    events_path = input_dir / "external-real-event-template.csv"
    docs_path = input_dir / "external-real-document-template.csv"
    events = read_csv(events_path)
    docs = read_csv(docs_path)
    docs_by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in docs:
        docs_by_event[row["event_id"]].append(row)

    families = load_families()
    source_cards = DST / "source-intake" / "source-evidence"
    excerpts = DST / "documents" / "excerpts"
    documents = DST / "documents"
    alignments: list[dict[str, Any]] = []

    for event in events:
        event_id = event["event_id"]
        excerpt_path = excerpts / f"{event_id}-evidence.md"
        if int(event_id[-3:]) < 69:
            excerpt_path.write_text(
                remove_candidate_tail(excerpt_path.read_text(encoding="utf-8-sig")),
                encoding="utf-8",
            )
            alignments.append(
                {
                    "event_id": event_id,
                    "domain": event["domain"],
                    "source_id": "INHERITED_EVENT_SPECIFIC",
                    "source_card_title": "",
                    "alignment_score": 1.0,
                    "alignment_status": "INHERITED",
                }
            )
            continue

        card_path = source_cards / f"{event_id}-source-card.md"
        family, card_title, score = choose_family(event, families, card_path)
        event["source_url"] = family["source_url"]
        for doc in docs_by_event[event_id]:
            is_old = doc["document_id"].endswith("_OLD")
            doc["source_title"] = family["old_source_title"] if is_old else family["source_title"]
            doc["source_url"] = family["old_source_url"] if is_old else family["source_url"]
            doc["publisher"] = family["publisher"]
            doc["document_type"] = "public_normative_raw_excerpt_window"
            doc["source_type"] = "EXTERNAL_PUBLIC_RAW_EXCERPT_WINDOW_ALIGNED"
            doc["notes"] = f"Aligned to {family['source_id']} from event source card."
            role = "previous public source context" if is_old else "current public source context"
            content = [
                f"Source title: {doc['source_title']}",
                f"Source URL: {doc['source_url']}",
                f"Publisher: {family['publisher']}",
                f"Event: {event_id}",
                f"Target: {event['subject_label']}",
                f"Window role: {role}",
                "",
                "Raw source excerpt window:",
                "",
                *[f"- {window}" for window in family["raw_windows"]],
                "",
                "Benchmark boundary:",
                "This file stores source-family-aligned public windows and omits candidate and Oracle fields.",
            ]
            doc_path = documents / doc["file_name"]
            doc_path.write_text("\n".join(content) + "\n", encoding="utf-8")
            doc["sha256"] = sha256(doc_path)

        evidence = [
            f"# {event_id} Source-Family-Aligned Evidence",
            "",
            f"- Domain: {event['domain']}",
            f"- Source family: {family['source_id']}",
            f"- Source title: {family['source_title']}",
            f"- Source URL: {family['source_url']}",
            f"- Event type: `{event['semantic_type']}`",
            f"- Target subject: {event['subject_label']}",
            f"- Target predicate: {event['predicate_label']}",
            "",
            "Raw source excerpt windows:",
            "",
            *[f"- {window}" for window in family["raw_windows"]],
            "",
            "Benchmark boundary:",
            "",
            "- Candidate values and private Oracle fields are intentionally omitted.",
        ]
        excerpt_path.write_text("\n".join(evidence) + "\n", encoding="utf-8")
        alignments.append(
            {
                "event_id": event_id,
                "domain": event["domain"],
                "source_id": family["source_id"],
                "source_card_title": card_title,
                "alignment_score": round(score, 4),
                "alignment_status": "PASS" if score >= 0.65 else "REVIEW",
            }
        )

    write_csv(events_path, events)
    write_csv(docs_path, docs)
    alignment_path = DST / "source-intake" / "external-real-v4-event-source-alignment.csv"
    write_csv(alignment_path, alignments)
    return alignments


def rename_intake_files() -> None:
    intake = DST / "source-intake"
    for path in list(intake.glob("*external-real-v3-naturalized*")):
        target = path.with_name(path.name.replace("external-real-v3-naturalized", NEW_NAME))
        path.rename(target)


def build_manifest() -> tuple[Path, Path]:
    roots = [DST / name for name in ("input", "documents", "rules", "mutants", "built", "source-intake")]
    files = sorted(path for root in roots for path in root.rglob("*") if path.is_file())
    hashes = {str(path.relative_to(ROOT)): sha256(path) for path in files}
    csv_path = OUTPUT / f"{NEW_NAME}-freeze-manifest-213-files.csv"
    json_path = OUTPUT / f"{NEW_NAME}-freeze-manifest-213.json"
    write_csv(
        csv_path,
        [
            {"path": relative, "sha256": digest, "bytes": (ROOT / relative).stat().st_size}
            for relative, digest in hashes.items()
        ],
    )
    payload = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "benchmark": NEW_NAME,
        "ready_events": 213,
        "public_file_count": len(files),
        "files_csv": str(csv_path.relative_to(ROOT)),
        "file_hashes": hashes,
        "boundary": "Private Oracle files are excluded from this public freeze manifest.",
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return json_path, csv_path


def write_readme(alignments: list[dict[str, Any]]) -> None:
    counts = Counter(row["alignment_status"] for row in alignments)
    text = f"""# External Real V4 Evidence Aligned

Generated at {datetime.now(timezone.utc).isoformat()} UTC.

- Events: {len(alignments)}
- Source-card aligned: {counts['PASS']}
- Alignment review required: {counts['REVIEW']}
- Inherited event-specific evidence: {counts['INHERITED']}

Boundary:

- This revision fixes source-family assignment; it does not claim that a reused
  family window proves every event-level conclusion.
- Candidate option lists are removed from evidence notes.
- Event-level support must pass the separate evidence-support audit before an
  event is included in natural-document experiments.
- Private Oracle files remain isolated from online model input.
"""
    (DST / "README.md").write_text(text, encoding="utf-8")


def main() -> int:
    reset()
    alignments = align_events()
    rename_intake_files()
    write_readme(alignments)
    manifest, files_csv = build_manifest()
    counts = Counter(row["alignment_status"] for row in alignments)
    print(f"built={DST}")
    print(f"events={len(alignments)}")
    print(f"alignment={dict(sorted(counts.items()))}")
    print(f"manifest={manifest}")
    print(f"manifest_files={files_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
