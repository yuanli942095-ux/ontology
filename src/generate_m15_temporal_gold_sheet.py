from __future__ import annotations

"""Generate blind temporal-gold annotation sheet for 88 TEMPORAL_VERSION v5 events."""

import argparse
import re
from pathlib import Path

from paper_final_validation_common import (
    DEFAULT_BENCHMARK,
    DEFAULT_BENCHMARK_FREEZE_SUMMARY,
    DEFAULT_METHOD_FREEZE_DIR,
    PAPER_VALIDATION_ROOT,
    benchmark_paths,
    load_freeze_sha256,
    load_method_freeze_sha256,
    load_ready_events,
    read_csv,
    write_binding,
    write_manifest_csv,
    write_summary_json,
    utc_now_iso,
)
from run_auto_policy_v2_candidate_repair import resolve_source_owl
from run_external_real_v1_symbolic_closure import load_graph
from rdflib import OWL, RDF
from semantic_v2_common import PROJECT_DIR


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=PAPER_VALIDATION_ROOT / "02-m15-semantic-audit")
    parser.add_argument("--method-freeze-dir", type=Path, default=DEFAULT_METHOD_FREEZE_DIR)
    parser.add_argument("--benchmark-freeze-summary", type=Path, default=DEFAULT_BENCHMARK_FREEZE_SUMMARY)
    return parser.parse_args()


def first_evidence_window(event_id: str, excerpts_dir: Path) -> str:
    path = excerpts_dir / f"{event_id}-evidence.md"
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"\[SOURCE_WINDOW_1\]\s*(.*?)(?:\n\[SOURCE_WINDOW_2\]|\Z)", text, flags=re.S)
    return match.group(1).strip() if match else text.strip()


def source_assertion(event: dict[str, str], mutants_dir: Path) -> str:
    source_path = resolve_source_owl(event, mutants_dir=mutants_dir, project_dir=PROJECT_DIR)
    graph = load_graph(source_path)
    subject = next(graph.subjects(RDF.type, OWL.NamedIndividual), None)
    if subject is None:
        return ""
    for predicate in graph.predicates(subject, None):
        for obj in graph.objects(subject, predicate):
            return f"{predicate}={obj}"
    return ""


def document_metadata(event_id: str, documents: list[dict[str, str]]) -> dict[str, str]:
    for row in documents:
        if row.get("event_id") == event_id:
            return {
                "document_id": row.get("document_id", ""),
                "issuer": row.get("issuer", ""),
                "source_url": row.get("source_url", ""),
                "document_type": row.get("document_type", ""),
            }
    return {"document_id": "", "issuer": "", "source_url": "", "document_type": ""}


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    benchmark_sha256, benchmark_manifest = load_freeze_sha256(args.benchmark_freeze_summary)
    method_freeze_sha256, method_freeze_manifest = load_method_freeze_sha256(args.method_freeze_dir)
    paths = benchmark_paths(args.benchmark_dir)
    events = [row for row in load_ready_events(paths) if row["semantic_type"] == "TEMPORAL_VERSION"]
    documents = read_csv(paths["document_csv"])
    excerpts_dir = paths["benchmark_dir"] / "public" / "excerpts"

    rows: list[dict[str, str]] = []
    for event in events:
        meta = document_metadata(event["event_id"], documents)
        rows.append(
            {
                "event_id": event["event_id"],
                "domain": event["domain"],
                "subject_label": event["subject_label"],
                "predicate_label": event["predicate_label"],
                "document_id": meta["document_id"],
                "issuer": meta["issuer"],
                "source_url": meta["source_url"],
                "document_type": meta["document_type"],
                "public_evidence_window": first_evidence_window(event["event_id"], excerpts_dir),
                "source_ontology_assertion": source_assertion(event, paths["mutants_dir"]),
                "entities": "",
                "role_current": "",
                "role_effective": "",
                "role_superseded": "",
                "role_reference": "",
                "role_publication": "",
                "relation_supersedes": "",
                "relation_updates": "",
                "relation_updatedBy": "",
                "resolution": "",
                "evidence_spans": "",
                "annotator_note": "",
                "annotator_name": "",
                "annotated_at": "",
            }
        )

    gold_path = args.output_dir / "gold-temporal-anchors.csv"
    manifest_path = args.output_dir / "manifest.csv"
    write_manifest_csv(gold_path, rows)
    write_manifest_csv(
        manifest_path,
        [
            {
                "artifact": "gold-temporal-anchors.csv",
                "events": len(rows),
                "purpose": "manual temporal gold; do not use oracle IR or model outputs",
            }
        ],
    )
    write_summary_json(
        args.output_dir / "summary.json",
        {
            "generated_at_utc": utc_now_iso(),
            "temp_events": len(rows),
            "gold_status": "AWAITING_MANUAL_ANNOTATION",
            "paper_label": "manually audited temporal gold",
            "forbidden_gold_sources": [
                "oracle candidate temporal IR",
                "M13/M15 model outputs",
                "ECR predictions",
            ],
        },
    )
    write_binding(
        args.output_dir,
        experiment_role="02-m15-semantic-audit-gold-sheet",
        script_path=Path(__file__),
        benchmark_manifest=benchmark_manifest,
        benchmark_sha256=benchmark_sha256,
        method_freeze_manifest=method_freeze_manifest,
        method_freeze_sha256=method_freeze_sha256,
        extra={"input_prediction_dir": "", "llm_backend": "", "model": ""},
    )
    print(f"[m15-gold-sheet] events={len(rows)} output={gold_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
