from __future__ import annotations

"""Draft strict version-drift provenance subset without reading model predictions."""

import argparse
import re
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
    load_oracles,
    load_ready_events,
    read_csv,
    write_binding,
    write_manifest_csv,
    write_summary_json,
    utc_now_iso,
)
from semantic_v2_common import PROJECT_DIR


RFC_RELATION_RE = re.compile(
    r"^\s*(Obsoletes|Updates|Supersedes|Replaces)\s*:\s*(.+)$",
    flags=re.I | re.M,
)
RFC_NUMBER_RE = re.compile(r"RFC\s*(\d+)", flags=re.I)
BARE_RFC_NUM_RE = re.compile(r"\b(\d{3,5})\b")


def parse_rfc_targets(text: str) -> list[str]:
    targets = RFC_NUMBER_RE.findall(text)
    for bare in BARE_RFC_NUM_RE.findall(text):
        if bare not in targets and int(bare) > 100:
            targets.append(bare)
    return targets


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=PAPER_VALIDATION_ROOT / "04-strict-drift-subset")
    parser.add_argument("--method-freeze-dir", type=Path, default=DEFAULT_METHOD_FREEZE_DIR)
    parser.add_argument("--benchmark-freeze-summary", type=Path, default=DEFAULT_BENCHMARK_FREEZE_SUMMARY)
    return parser.parse_args()


def parse_rfc_header_relations(cache_path: Path) -> list[tuple[str, str]]:
    if not cache_path.is_file():
        return []
    header = "\n".join(cache_path.read_text(encoding="utf-8-sig", errors="replace").splitlines()[:25])
    relations: list[tuple[str, str]] = []
    for match in RFC_RELATION_RE.finditer(header):
        relation = match.group(1).lower()
        for target in parse_rfc_targets(match.group(2)):
            relations.append((relation, target))
    return relations


def rfc_number_from_url(url: str) -> str:
    match = re.search(r"/rfc/rfc(\d+)\.txt", url, flags=re.I)
    return match.group(1) if match else ""


def resolve_cache_path(cache_path_value: str) -> Path:
    text = str(cache_path_value or "").strip().replace("\\", "/")
    if not text:
        return Path()
    path = Path(text)
    if path.is_file():
        return path
    return PROJECT_DIR / text


def find_t0_candidate(
    t1_rfc: str,
    relations: list[tuple[str, str]],
    cache_dir: Path,
    source_family: str,
) -> tuple[str, str, str]:
    for relation, target in relations:
        if relation not in {"obsoletes", "updates", "supersedes", "replaces"}:
            continue
        for path in cache_dir.glob("*.txt"):
            if f"RFC{target}" in path.name.upper():
                return str(path), relation, target
    if t1_rfc:
        family_prefix = source_family.split("_")[0] if source_family else ""
        family_candidates = sorted(
            [
                path
                for path in cache_dir.glob("*.txt")
                if family_prefix and family_prefix in path.name.upper() and f"RFC{t1_rfc}" not in path.name.upper()
            ],
            key=lambda item: item.name,
        )
        for path in family_candidates:
            match = re.search(r"RFC(\d+)", path.name.upper())
            if match and int(match.group(1)) < int(t1_rfc):
                return str(path), "same_family_older_rfc", match.group(1)
    return "", "", ""


def old_assertion(candidates: list[dict], oracle_candidate_id: str) -> tuple[str, str]:
    for candidate in candidates:
        if candidate["candidate_id"] == "CAND_001":
            return candidate["candidate_id"], candidate["display_value"]
    for candidate in candidates:
        if candidate["candidate_id"] != oracle_candidate_id:
            return candidate["candidate_id"], candidate["display_value"]
    return "", ""


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    benchmark_sha256, benchmark_manifest = load_freeze_sha256(args.benchmark_freeze_summary)
    method_freeze_sha256, method_freeze_manifest = load_method_freeze_sha256(args.method_freeze_dir)
    paths = benchmark_paths(args.benchmark_dir)
    events = load_ready_events(paths)
    documents = {row["event_id"]: row for row in read_csv(paths["document_csv"])}
    candidates_by_event = load_candidates(paths)
    oracles = load_oracles(paths)
    cache_dir = PROJECT_DIR / "data" / "external-real-holdout-v1-cache"

    draft_rows: list[dict[str, str]] = []
    for event in events:
        event_id = event["event_id"]
        doc = documents.get(event_id, {})
        oracle = oracles[event_id]
        candidates = candidates_by_event[event_id]
        t1_url = doc.get("source_url", "")
        t1_rfc = rfc_number_from_url(t1_url)
        file_name = doc.get("file_name", "")
        family_match = re.search(r"(HO_[A-Z0-9]+)_RFC", file_name, re.I)
        source_family = family_match.group(1) if family_match else ""
        cache_path = resolve_cache_path(doc.get("cache_path", ""))
        relations = parse_rfc_header_relations(cache_path)
        t0_path, lineage_relation, t0_rfc = find_t0_candidate(
            t1_rfc,
            relations,
            cache_dir,
            source_family,
        )
        lineage_explicit = (
            "yes"
            if t0_path and lineage_relation in {"obsoletes", "updates", "supersedes", "replaces"}
            else "no"
        )
        old_cand_id, old_value = old_assertion(candidates, oracle["oracle_candidate_id"])
        draft_rows.append(
            {
                "event_id": event_id,
                "semantic_type": event["semantic_type"],
                "t0_source": t0_path,
                "t0_rfc": t0_rfc,
                "t1_source": str(cache_path),
                "t1_rfc": t1_rfc,
                "lineage_relation": lineage_relation,
                "lineage_explicit": lineage_explicit,
                "old_assertion": old_value,
                "new_assertion": oracle["oracle_value"],
                "old_candidate_id": old_cand_id,
                "oracle_candidate_id": oracle["oracle_candidate_id"],
                "support_a0_t0": "",
                "support_a0_t1": "",
                "support_astar_t1": "",
                "t0_support_old": "",
                "t1_support_old": "",
                "t1_support_new": "",
                "review_status": "DRAFT",
                "review_notes": "",
            }
        )

    manifest_path = args.output_dir / "manifest.csv"
    review_path = args.output_dir / "review-sheet.csv"
    write_manifest_csv(manifest_path, draft_rows)
    write_manifest_csv(review_path, draft_rows)
    write_summary_json(
        args.output_dir / "summary.json",
        {
            "generated_at_utc": utc_now_iso(),
            "events": len(draft_rows),
            "lineage_explicit_candidates": sum(row["lineage_explicit"] == "yes" for row in draft_rows),
            "status": "AWAITING_HUMAN_REVIEW",
            "selection_policy": (
                "Subset selection code does not read model predictions. "
                "Only events with human-confirmed lineage/support fields enter strict subset."
            ),
        },
    )
    write_binding(
        args.output_dir,
        experiment_role="04-strict-drift-subset-draft",
        script_path=Path(__file__),
        benchmark_manifest=benchmark_manifest,
        benchmark_sha256=benchmark_sha256,
        method_freeze_manifest=method_freeze_manifest,
        method_freeze_sha256=method_freeze_sha256,
        extra={"input_prediction_dir": "", "llm_backend": "", "model": ""},
    )
    print(
        f"[strict-drift-draft] events={len(draft_rows)} "
        f"lineage_explicit={sum(row['lineage_explicit'] == 'yes' for row in draft_rows)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
