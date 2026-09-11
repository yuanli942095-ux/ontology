from __future__ import annotations

"""Build Exp8 evidence-dependence benchmark variants from candidate-id-permute parent."""

import argparse
import csv
import json
import random
import re
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paper_final_validation_common import sha256_file, utc_now_iso
from semantic_v2_common import PROJECT_DIR, write_csv


PARENT = (
    PROJECT_DIR
    / "benchmark"
    / "external-real-holdout-v5-blind-large-robustness-variants"
    / "candidate-id-permute"
)
DEFAULT_VARIANT_ROOT = (
    PROJECT_DIR / "benchmark" / "external-real-holdout-v5-blind-large-evidence-variants"
)
DEFAULT_EXP8_ROOT = PROJECT_DIR / "output" / "paper-final-validation" / "08-evidence-dependence"

CONDITIONS = (
    "normal",
    "no-evidence",
    "no-evidence-masked-source",
    "shuffled-evidence",
    "irrelevant-same-source",
)

EVIDENCE_REMOVED = "[EVIDENCE REMOVED]"
SHUFFLE_SEED = 20260903


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv_rows(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def copy_parent(source: Path, target: Path, *, force: bool) -> None:
    if target.exists():
        if not force:
            raise SystemExit(f"target exists; pass --force: {target}")
        shutil.rmtree(target)
    shutil.copytree(source, target)


def ready_events(parent: Path) -> list[dict[str, str]]:
    event_csv = parent / "public" / "events" / "external-real-event-template.csv"
    return [row for row in read_csv(event_csv) if str(row.get("status", "")).upper() == "READY"]


def document_row(parent: Path, event_id: str) -> dict[str, str]:
    doc_csv = parent / "public" / "documents" / "external-real-document-template.csv"
    for row in read_csv(doc_csv):
        if row["event_id"] == event_id:
            return row
    raise KeyError(event_id)


def oracle_row(parent: Path, event_id: str) -> dict[str, str]:
    oracle_csv = parent / "private" / "oracle" / "external-real-oracle-template.csv"
    for row in read_csv(oracle_csv):
        if row["event_id"] == event_id:
            return row
    raise KeyError(event_id)


def excerpt_path(root: Path, event_id: str) -> Path:
    return root / "public" / "excerpts" / f"{event_id}-evidence.md"


def source_key(parent: Path, event_id: str) -> str:
    doc = document_row(parent, event_id)
    return doc.get("source_url") or doc.get("issuer") or doc.get("file_name") or event_id


def token_set(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(text or "").lower())
        if len(token) > 2
    }


def scrub_source_identity(text: str) -> str:
    out = str(text or "")
    out = re.sub(r"https?://\S+", "", out)
    out = re.sub(
        r"Using only the cited public source \([^)]+\),",
        "Using only the supplied evidence window,",
        out,
        flags=re.I,
    )
    out = re.sub(r"\bIETF RFC \d+:\s*[^,\"]+", "the cited public specification", out, flags=re.I)
    out = re.sub(r"\bRFC\s*\d+\b", "the cited specification", out, flags=re.I)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def build_shuffle_map(parent: Path, *, seed: int) -> list[dict[str, str]]:
    events = ready_events(parent)
    by_type: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in events:
        by_type[row["semantic_type"]].append(row)

    rows_out: list[dict[str, str]] = []
    for semantic_type in sorted(by_type):
        group = sorted(by_type[semantic_type], key=lambda item: item["event_id"])
        ids = [row["event_id"] for row in group]
        rng = random.Random(f"{seed}|{semantic_type}")
        replacement_ids = list(ids)
        for _ in range(1000):
            rng.shuffle(replacement_ids)
            if all(a != b for a, b in zip(ids, replacement_ids)):
                break
        else:
            replacement_ids = ids[1:] + ids[:1]

        for src, dst in zip(ids, replacement_ids):
            rows_out.append(
                {
                    "event_id": src,
                    "original_source": source_key(parent, src),
                    "replacement_event_id": dst,
                    "replacement_source": source_key(parent, dst),
                    "semantic_type": semantic_type,
                    "seed": str(seed),
                }
            )
    return rows_out


def split_document_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            continue
        current.append(line.rstrip())
    if current:
        blocks.append("\n".join(current).strip())
    return [block for block in blocks if len(block) >= 80]


def choose_irrelevant_block(parent: Path, event_id: str) -> tuple[str, str, str]:
    doc = document_row(parent, event_id)
    doc_path = parent / "public" / "documents" / doc["file_name"]
    full_text = doc_path.read_text(encoding="utf-8", errors="replace")
    correct_excerpt = excerpt_path(parent, event_id).read_text(encoding="utf-8", errors="replace")
    oracle = oracle_row(parent, event_id)
    forbidden = token_set(correct_excerpt) | token_set(oracle.get("oracle_value", "")) | token_set(
        oracle.get("oracle_candidate_id", "")
    )
    correct_pos = full_text.find(correct_excerpt.splitlines()[0][:80]) if correct_excerpt else -1

    best: tuple[int, str] | None = None
    best_loc = ""
    for block in split_document_blocks(full_text):
        overlap = len(token_set(block) & forbidden)
        if overlap > 8:
            continue
        pos = full_text.find(block[: min(80, len(block))])
        distance = abs(pos - correct_pos) if correct_pos >= 0 and pos >= 0 else 10_000
        score = (overlap, -distance, -len(block))
        if best is None or score < best[0]:
            best = (score, block)
            best_loc = f"offset={pos};len={len(block)}"
    if best is None:
        block = split_document_blocks(full_text)[0]
        best_loc = "fallback:first_block"
        return block, doc["document_id"], best_loc
    return best[1], doc["document_id"], best_loc


def build_irrelevant_map(parent: Path) -> list[dict[str, str]]:
    rows_out: list[dict[str, str]] = []
    for row in ready_events(parent):
        event_id = row["event_id"]
        block, document_id, locator = choose_irrelevant_block(parent, event_id)
        rows_out.append(
            {
                "event_id": event_id,
                "semantic_type": row["semantic_type"],
                "same_document_id": document_id,
                "same_source": source_key(parent, event_id),
                "replacement_locator": locator,
                "replacement_preview": block[:160].replace("\n", " "),
                "applied_chars": str(len(block)),
            }
        )
    return rows_out


def apply_no_evidence(root: Path) -> None:
    for row in ready_events(root):
        excerpt_path(root, row["event_id"]).write_text(
            f"[SOURCE_WINDOW_1]\n{EVIDENCE_REMOVED}\n", encoding="utf-8"
        )


def apply_masked_source(root: Path) -> None:
    apply_no_evidence(root)
    event_csv = root / "public" / "events" / "external-real-event-template.csv"
    rows = read_csv(event_csv)
    fields = list(rows[0].keys())
    for row in rows:
        row["title"] = scrub_source_identity(row.get("title", ""))
        row["case_context"] = scrub_source_identity(row.get("case_context", ""))
        row["source_url"] = ""
        row["document_ids"] = row.get("document_ids", "")
    write_csv_rows(event_csv, rows, fields)

    doc_csv = root / "public" / "documents" / "external-real-document-template.csv"
    docs = read_csv(doc_csv)
    doc_fields = list(docs[0].keys())
    for row in docs:
        row["issuer"] = "PUBLIC_SPECIFICATION"
        row["source_url"] = ""
        row["document_type"] = "public_normative_full_text"
    write_csv_rows(doc_csv, docs, doc_fields)


def apply_shuffled(root: Path, shuffle_rows: list[dict[str, str]]) -> None:
    replacement_by_event = {row["event_id"]: row["replacement_event_id"] for row in shuffle_rows}
    for event_id, replacement_event_id in replacement_by_event.items():
        text = excerpt_path(root, replacement_event_id).read_text(encoding="utf-8", errors="replace")
        excerpt_path(root, event_id).write_text(text, encoding="utf-8")


def apply_irrelevant(root: Path, parent: Path) -> None:
    for row in ready_events(root):
        event_id = row["event_id"]
        block, _, _ = choose_irrelevant_block(parent, event_id)
        excerpt_path(root, event_id).write_text(
            f"[SOURCE_WINDOW_1]\n{block}\n", encoding="utf-8"
        )


def write_variant_readme(root: Path, condition: str, extra: dict[str, Any]) -> None:
    readme = root / "README.md"
    existing = readme.read_text(encoding="utf-8", errors="replace") if readme.is_file() else ""
    addition = (
        "\n\n## Exp8 Evidence Variant\n\n"
        f"- Condition: `{condition}`\n"
        f"- Created at UTC: `{utc_now_iso()}`\n"
        f"- Parent: `candidate-id-permute`\n"
        f"- Transform: `{json.dumps(extra, ensure_ascii=False)}`\n"
    )
    readme.write_text(existing.rstrip() + addition + "\n", encoding="utf-8")


def build_condition(
    condition: str,
    *,
    parent: Path,
    variant_root: Path,
    exp8_root: Path,
    shuffle_rows: list[dict[str, str]],
    force: bool,
) -> dict[str, Any]:
    target = variant_root / condition
    copy_parent(parent, target, force=force)
    if condition == "normal":
        extra = {"transform": "identity_copy"}
    elif condition == "no-evidence":
        apply_no_evidence(target)
        extra = {"transform": "evidence_removed", "placeholder": EVIDENCE_REMOVED}
    elif condition == "no-evidence-masked-source":
        apply_masked_source(target)
        extra = {"transform": "evidence_removed_and_source_identity_masked", "placeholder": EVIDENCE_REMOVED}
    elif condition == "shuffled-evidence":
        apply_shuffled(target, shuffle_rows)
        extra = {"transform": "cross_event_evidence_swap", "seed": SHUFFLE_SEED}
    elif condition == "irrelevant-same-source":
        apply_irrelevant(target, parent)
        extra = {"transform": "same_source_irrelevant_section"}
    else:
        raise ValueError(condition)

    write_variant_readme(target, condition, extra)
    event_csv = target / "public" / "events" / "external-real-event-template.csv"
    return {
        "condition": condition,
        "benchmark_dir": str(target.relative_to(PROJECT_DIR)),
        "event_csv_sha256": sha256_file(event_csv),
        "extra": extra,
    }


def write_protocol(exp8_root: Path, summaries: list[dict[str, Any]], shuffle_rows: list[dict[str, str]]) -> None:
    lines = [
        "# Exp8 Evidence-Dependence Counterfactual Audit",
        "",
        f"Generated at UTC: `{utc_now_iso()}`",
        "",
        "## Parent benchmark",
        "",
        "- `candidate-id-permute` (oracle ID distribution 71/88/105)",
        "",
        "## Conditions",
        "",
        "| Condition | Transform |",
        "|-----------|-----------|",
        "| normal | Original evidence (identity copy) |",
        f"| no-evidence | Excerpt replaced with `{EVIDENCE_REMOVED}` |",
        f"| no-evidence-masked-source | `{EVIDENCE_REMOVED}` + scrub RFC/URL/title from event metadata |",
        f"| shuffled-evidence | Cross-event excerpt swap within semantic type (seed {SHUFFLE_SEED}) |",
        "| irrelevant-same-source | Same RFC document, unrelated section passage |",
        "",
        "## Pre-registered hypotheses",
        "",
        "- H8-1: NORMAL > counterfactual conditions on repair success",
        "- H8-2: Evidence removal/mismatch increases abstention rather than wrong-select",
        "- H8-3: Masking source identity further reduces memorization-driven success",
        "",
        "## Formal run plan",
        "",
        "- Pilot: 264 events × 1 run × 5 conditions (QA only)",
        "- Formal: 264 events × 5 runs × 5 conditions = 6600 pipeline attempts",
        "",
        "## Variant checksums",
        "",
    ]
    for summary in summaries:
        lines.append(
            f"- `{summary['condition']}`: `{summary['benchmark_dir']}` sha256={summary['event_csv_sha256']}"
        )
    lines.append("")
    lines.append(f"Shuffle pairs: {len(shuffle_rows)}")
    (exp8_root / "protocol.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", type=Path, default=PARENT)
    parser.add_argument("--variant-root", type=Path, default=DEFAULT_VARIANT_ROOT)
    parser.add_argument("--exp8-root", type=Path, default=DEFAULT_EXP8_ROOT)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--only",
        default="",
        help="Comma-separated conditions; default all five",
    )
    args = parser.parse_args()

    parent = args.parent.resolve()
    variant_root = args.variant_root.resolve()
    exp8_root = args.exp8_root.resolve()
    exp8_root.mkdir(parents=True, exist_ok=True)
    variant_root.mkdir(parents=True, exist_ok=True)

    shuffle_rows = build_shuffle_map(parent, seed=SHUFFLE_SEED)
    write_csv_rows(
        exp8_root / "evidence-shuffle-map.csv",
        shuffle_rows,
        [
            "event_id",
            "original_source",
            "replacement_event_id",
            "replacement_source",
            "semantic_type",
            "seed",
        ],
    )

    irrelevant_rows = build_irrelevant_map(parent)
    write_csv_rows(
        exp8_root / "irrelevant-same-source-map.csv",
        irrelevant_rows,
        [
            "event_id",
            "semantic_type",
            "same_document_id",
            "same_source",
            "replacement_locator",
            "replacement_preview",
            "applied_chars",
        ],
    )

    selected = [item.strip() for item in args.only.split(",") if item.strip()] or list(CONDITIONS)
    summaries: list[dict[str, Any]] = []
    for condition in selected:
        if condition not in CONDITIONS:
            raise SystemExit(f"unknown condition: {condition}")
        summaries.append(
            build_condition(
                condition,
                parent=parent,
                variant_root=variant_root,
                exp8_root=exp8_root,
                shuffle_rows=shuffle_rows,
                force=args.force,
            )
        )

    freeze = {
        "generated_at_utc": utc_now_iso(),
        "parent_benchmark": str(parent.relative_to(PROJECT_DIR)),
        "variant_root": str(variant_root.relative_to(PROJECT_DIR)),
        "conditions": summaries,
        "shuffle_seed": SHUFFLE_SEED,
        "evidence_removed_token": EVIDENCE_REMOVED,
    }
    (exp8_root / "freeze.json").write_text(json.dumps(freeze, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_protocol(exp8_root, summaries, shuffle_rows)
    print(json.dumps(freeze, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
