from __future__ import annotations

"""Generate stratified 90-event blind independent-annotation sheet (30/30/30)."""

import argparse
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from paper_final_validation_common import (
    DEFAULT_BENCHMARK,
    DEFAULT_BENCHMARK_FREEZE_SUMMARY,
    DEFAULT_METHOD_FREEZE_DIR,
    PAPER_VALIDATION_ROOT,
    benchmark_paths,
    load_candidates,
    load_freeze_sha256,
    load_method_freeze_sha256,
    load_ready_events,
    write_binding,
    write_manifest_csv,
    write_summary_json,
    utc_now_iso,
)
from run_auto_policy_v2_candidate_repair import resolve_source_owl
from run_external_real_v1_symbolic_closure import load_graph
from rdflib import OWL, RDF
from semantic_v2_common import PROJECT_DIR


SAMPLE_SEED = 20260831
SAMPLE_SIZE_PER_TYPE = 30
SEMANTIC_TYPES = ("TEMPORAL_VERSION", "GENERAL_RULE_EXCEPTION", "CROSS_SENTENCE_SCOPE")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=PAPER_VALIDATION_ROOT / "03-independent-review")
    parser.add_argument("--seed", type=int, default=SAMPLE_SEED)
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


def stratified_sample(events: list[dict[str, str]], seed: int) -> list[dict[str, str]]:
    by_type: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in events:
        by_type[row["semantic_type"]].append(row)
    rng = random.Random(seed)
    selected: list[dict[str, str]] = []
    for semantic_type in SEMANTIC_TYPES:
        pool = sorted(by_type[semantic_type], key=lambda item: item["event_id"])
        if len(pool) < SAMPLE_SIZE_PER_TYPE:
            raise RuntimeError(f"not enough {semantic_type} events: {len(pool)}")
        domain_buckets: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in pool:
            domain_buckets[row["domain"]].append(row)
        picked: list[dict[str, str]] = []
        domains = sorted(domain_buckets)
        while len(picked) < SAMPLE_SIZE_PER_TYPE:
            progressed = False
            for domain in domains:
                bucket = domain_buckets[domain]
                if not bucket:
                    continue
                idx = rng.randrange(len(bucket))
                picked.append(bucket.pop(idx))
                progressed = True
                if len(picked) >= SAMPLE_SIZE_PER_TYPE:
                    break
            if not progressed:
                break
        if len(picked) < SAMPLE_SIZE_PER_TYPE:
            remaining = [row for row in pool if row not in picked]
            rng.shuffle(remaining)
            picked.extend(remaining[: SAMPLE_SIZE_PER_TYPE - len(picked)])
        selected.extend(picked[:SAMPLE_SIZE_PER_TYPE])
    return sorted(selected, key=lambda item: item["event_id"])


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    benchmark_sha256, benchmark_manifest = load_freeze_sha256(args.benchmark_freeze_summary)
    method_freeze_sha256, method_freeze_manifest = load_method_freeze_sha256(args.method_freeze_dir)
    paths = benchmark_paths(args.benchmark_dir)
    events = load_ready_events(paths)
    candidates_by_event = load_candidates(paths)
    excerpts_dir = paths["benchmark_dir"] / "public" / "excerpts"
    sampled = stratified_sample(events, args.seed)

    sheet_rows: list[dict[str, str]] = []
    manifest_rows: list[dict[str, str]] = []
    for event in sampled:
        event_id = event["event_id"]
        candidates = sorted(candidates_by_event[event_id], key=lambda item: item["candidate_id"])
        manifest_rows.append(
            {
                "event_id": event_id,
                "semantic_type": event["semantic_type"],
                "domain": event["domain"],
                "sample_seed": str(args.seed),
            }
        )
        row = {
            "event_id": event_id,
            "semantic_type": event["semantic_type"],
            "domain": event["domain"],
            "subject_label": event["subject_label"],
            "predicate_label": event["predicate_label"],
            "public_evidence_window": first_evidence_window(event_id, excerpts_dir),
            "source_ontology_assertion": source_assertion(event, paths["mutants_dir"]),
            "candidate_1": candidates[0]["display_value"] if len(candidates) > 0 else "",
            "candidate_2": candidates[1]["display_value"] if len(candidates) > 1 else "",
            "candidate_3": candidates[2]["display_value"] if len(candidates) > 2 else "",
            "annotator_oracle_candidate": "",
            "annotator_evidence_sufficient": "",
            "annotator_semantic_type": "",
            "annotator_candidate_ambiguous": "",
            "annotator_confidence": "",
            "annotator_notes": "",
            "annotator_roles": "",
            "annotator_relations": "",
            "annotator_resolution": "",
            "annotator_name": "",
            "annotated_at": "",
        }
        sheet_rows.append(row)

    domain_dist = Counter(row["domain"] for row in manifest_rows)
    write_manifest_csv(args.output_dir / "manifest.csv", manifest_rows)
    write_manifest_csv(args.output_dir / "annotation-sheet.csv", sheet_rows)
    write_summary_json(
        args.output_dir / "summary.json",
        {
            "generated_at_utc": utc_now_iso(),
            "sample_seed": args.seed,
            "events": len(sheet_rows),
            "per_semantic_type": dict(Counter(row["semantic_type"] for row in manifest_rows)),
            "domain_distribution": dict(domain_dist),
            "status": "AWAITING_SECOND_ANNOTATOR",
        },
    )
    rubric = args.output_dir / "annotation-rubric.md"
    rubric.write_text(
        "\n".join(
            [
                "# Independent Annotation Rubric",
                "",
                "Annotator may view: public evidence, subject/predicate, source ontology assertion, 3 candidates.",
                "Annotator must NOT view: private oracle, ECR output, M13/M14/M15/M16 output, prior annotator conclusions.",
                "",
                "Required fields:",
                "- annotator_oracle_candidate: CAND_001 | CAND_002 | CAND_003",
                "- annotator_evidence_sufficient: yes | no",
                "- annotator_semantic_type: TEMPORAL_VERSION | GENERAL_RULE_EXCEPTION | CROSS_SENTENCE_SCOPE",
                "- annotator_candidate_ambiguous: yes | no",
                "",
                "TEMPORAL_VERSION rows additionally annotate roles/relations/resolution.",
                "",
                "Do not use LLM outputs as a second annotator.",
            ]
        ),
        encoding="utf-8",
    )
    write_binding(
        args.output_dir,
        experiment_role="03-independent-review-sheet",
        script_path=Path(__file__),
        benchmark_manifest=benchmark_manifest,
        benchmark_sha256=benchmark_sha256,
        method_freeze_manifest=method_freeze_manifest,
        method_freeze_sha256=method_freeze_sha256,
        extra={"input_prediction_dir": "", "llm_backend": "", "model": ""},
    )
    print(f"[independent-review] sampled={len(sheet_rows)} seed={args.seed} output={args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
