from __future__ import annotations

"""Move v8 public files into the staged layout and attach repair/eval assets.

Construction still cannot see candidates. Candidate Selection, Evaluation, and
Hard Gate get their own directories.
"""

import argparse
import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from external_real_v8_layout import (
    FULL_METADATA_FIELDS,
    LIGHT_METADATA_FIELDS,
    BenchmarkLayout,
    SUPPORT_GATE_VERSION,
)
from semantic_v2_common import PROJECT_DIR


DST = PROJECT_DIR / "benchmark" / "external-real-v8-grounded"
V4 = PROJECT_DIR / "benchmark" / "external-real-v4-evidence-aligned"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def move_file(src: Path, dst: Path) -> None:
    if not src.is_file() or src.resolve() == dst.resolve():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    shutil.move(str(src), str(dst))


def copy_file(src: Path, dst: Path) -> None:
    if not src.is_file():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def migrate_public(layout: BenchmarkLayout) -> None:
    layout.public_events.mkdir(parents=True, exist_ok=True)
    layout.public_documents.mkdir(parents=True, exist_ok=True)
    layout.public_excerpts.mkdir(parents=True, exist_ok=True)
    layout.public_retrieval.mkdir(parents=True, exist_ok=True)
    move_file(
        DST / "input" / "external-real-event-template.csv",
        layout.event_csv,
    )
    move_file(
        DST / "input" / "external-real-document-template.csv",
        layout.document_csv,
    )
    old_docs = DST / "documents"
    if old_docs.is_dir():
        for path in old_docs.iterdir():
            if path.is_file():
                move_file(path, layout.public_documents / path.name)
        old_excerpts = old_docs / "excerpts"
        if old_excerpts.is_dir():
            for path in old_excerpts.glob("*.md"):
                move_file(path, layout.public_excerpts / path.name)
    intake = DST / "source-intake"
    if intake.is_dir():
        mapping = {
            "external-real-v8-event-retrieval.csv": layout.retrieval_csv,
            "external-real-v8-grounded-source-families.csv": layout.family_csv,
            "external-real-v8-event-source-alignment.csv": layout.alignment_csv,
            "normative-source-registry.csv": layout.registry_csv,
        }
        for name, dest in mapping.items():
            move_file(intake / name, dest)
        readme = intake / "README.md"
        if readme.is_file():
            move_file(readme, layout.public_retrieval / "README.md")


def attach_repair_assets(layout: BenchmarkLayout, ready_ids: set[str]) -> None:
    layout.candidate_csv.parent.mkdir(parents=True, exist_ok=True)
    layout.operations_dir.mkdir(parents=True, exist_ok=True)
    layout.mutants_dir.mkdir(parents=True, exist_ok=True)
    layout.oracle_csv.parent.mkdir(parents=True, exist_ok=True)
    layout.rules.mkdir(parents=True, exist_ok=True)

    candidates = [
        row
        for row in read_csv(V4 / "input" / "external-real-candidate-template.csv")
        if row.get("event_id") in ready_ids
    ]
    write_csv(layout.candidate_csv, candidates)
    for row in candidates:
        operation_path = layout.operations_dir / f"{row['event_id']}-{row['candidate_id']}.json"
        try:
            payload = json.loads(row.get("operation_json", "{}"))
        except json.JSONDecodeError:
            payload = {"raw": row.get("operation_json", "")}
        operation_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    oracles = [
        row
        for row in read_csv(V4 / "private" / "external-real-oracle-template.csv")
        if row.get("event_id") in ready_ids
    ]
    write_csv(layout.oracle_csv, oracles)

    for event_id in sorted(ready_ids):
        copy_file(V4 / "mutants" / f"{event_id}.owl", layout.mutants_dir / f"{event_id}.owl")
        copy_file(
            V4 / "rules" / f"{event_id}-formal-policy.json",
            layout.rules / f"{event_id}-formal-policy.json",
        )

    (layout.repair_stage / "README.md").write_text(
        """# Repair stage

Candidate Selection may read this directory together with `public/`.
Auto Policy Construction must not.

Contains candidate values, repair operations, and mutant OWLs.
""",
        encoding="utf-8",
    )
    (layout.private / "README.md").write_text(
        """# Private Oracle

Evaluation may read this directory after Candidate Selection is fixed.
Construction and Candidate Selection must not.
""",
        encoding="utf-8",
    )
    (layout.rules / "BLIND_FORBIDDEN.md").write_text(
        "Formal policies are Hard Gate upper-bound input only. "
        "Auto Policy Construction must not read this directory.\n",
        encoding="utf-8",
    )
    (layout.rules / "ACCESS.md").write_text(
        "Hard Gate may use these files as an upper bound. "
        "They are not Auto Policy Construction evidence.\n",
        encoding="utf-8",
    )


def write_contracts(layout: BenchmarkLayout) -> None:
    layout.query_contracts.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "FULL_METADATA": list(FULL_METADATA_FIELDS),
                "LIGHT_METADATA": list(LIGHT_METADATA_FIELDS),
                "support_gate_version": SUPPORT_GATE_VERSION,
                "frozen": True,
                "note": "Do not rewrite retrieval queries or evidence windows after freeze to chase model errors.",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (DST / "ACCESS.md").write_text(
        """# Stage access

Gold-assisted dataset curation ≠ Gold-assisted model inference.

| Stage | May read |
|---|---|
| Auto Policy Construction | `public/` only |
| Candidate Selection | `public/` + `repair-stage/` |
| Evaluation | `public/` + `repair-stage/` + `private/` |
| Hard Gate | `public/` + `repair-stage/` + `rules/` as upper bound only |
| Curation / support gate | all of the above, before freeze, never after model runs |
""",
        encoding="utf-8",
    )


def remove_empty_legacy() -> None:
    for path in (DST / "input", DST / "source-intake", DST / "documents" / "excerpts", DST / "documents"):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="stage v8 public/repair/private/rules layout")
    parser.add_argument("--public-only", action="store_true")
    parser.add_argument("--repair-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    layout = BenchmarkLayout(DST)
    if not args.repair_only:
        migrate_public(layout)
        write_contracts(layout)
        remove_empty_legacy()
    events = read_csv(layout.event_csv)
    ready_ids = {row["event_id"] for row in events if str(row.get("status", "")).upper() == "READY"}
    if not args.public_only:
        attach_repair_assets(layout, ready_ids)
    print(
        json.dumps(
            {
                "benchmark": "external-real-v8-grounded",
                "ready_events": len(ready_ids),
                "public_events": str(layout.event_csv),
                "repair_candidates": str(layout.candidate_csv),
                "private_oracle": str(layout.oracle_csv),
                "rules": str(layout.rules),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
