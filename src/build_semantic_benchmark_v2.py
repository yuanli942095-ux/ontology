from __future__ import annotations

"""从人工录入的 CSV 构建语义歧义基准 V2。

构建阶段只验证候选的形式安全性，不使用 Oracle 判断候选语义是否正确。
私有 Oracle 在公开事件负载构造完成后才加载，仅做离线完整性核对。
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from rdflib import Graph, RDF, URIRef
    from rdflib.compare import to_canonical_graph
    from rdflib.namespace import OWL
except ImportError:
    print("缺少rdflib，请执行：python -m pip install rdflib -i https://pypi.org/simple")
    raise SystemExit(2)

try:
    import validate_benchmark as benchmark_validator
    from repair_operators import apply_operations, expected_graph_delta
except ImportError as exc:
    print(
        "缺少项目模块validate_benchmark.py或扩展版repair_operators.py。"
        "请把本脚本复制到ontology-evolution\\src后再运行。"
    )
    raise SystemExit(2) from exc

from semantic_v2_common import (
    BUILD_DIR,
    CANDIDATE_CSV,
    DOCUMENT_CSV,
    DOCUMENT_DIR,
    EVENT_CSV,
    ORACLE_CSV,
    OUTPUT_DIR,
    PROJECT_DIR,
    load_csv,
    parse_json_object,
    resolve_project_path,
    sha256_file,
    split_ids,
    write_csv,
)


PUBLIC_EVENT_JSON = BUILD_DIR / "semantic-events.json"
CANDIDATE_EFFECTS_CSV = BUILD_DIR / "candidate-effects.csv"
CANDIDATE_EFFECTS_JSON = BUILD_DIR / "candidate-effects.json"
MANIFEST_JSON = BUILD_DIR / "benchmark-manifest.json"
LOG_FILE = OUTPUT_DIR / "semantic-v2-build.log"
CANDIDATE_OWL_DIR = BUILD_DIR / "candidate-owls"
ONTOLOGY_BASE = "https://w3id.org/ontology-evolution/semantic-v2"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建语义歧义基准V2")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--min-events", type=int, default=30)
    parser.add_argument("--allow-less", action="store_true", help="仅调试时允许少于30个READY事件")
    return parser.parse_args()


def load_graph(path: Path) -> Graph:
    graph = Graph()
    graph.parse(path, format="xml")
    return graph


def strip_ontology_metadata(source: Graph) -> Graph:
    result = Graph()
    ontology_subjects = set(source.subjects(RDF.type, OWL.Ontology))
    for triple in source:
        subject, predicate, _ = triple
        if subject in ontology_subjects or predicate == OWL.versionIRI:
            continue
        result.add(triple)
    return result


def graph_delta(source: Graph, target: Graph) -> tuple[int, int]:
    left = set(to_canonical_graph(strip_ontology_metadata(source)))
    right = set(to_canonical_graph(strip_ontology_metadata(target)))
    return len(left - right), len(right - left)


def set_ontology_identity(graph: Graph, event_id: str, candidate_id: str) -> str:
    for ontology in list(graph.subjects(RDF.type, OWL.Ontology)):
        for triple in list(graph.triples((ontology, None, None))):
            graph.remove(triple)
    ontology_iri = URIRef(f"{ONTOLOGY_BASE}/{event_id}/{candidate_id}")
    graph.add((ontology_iri, RDF.type, OWL.Ontology))
    graph.add((ontology_iri, OWL.versionIRI, URIRef(f"{ontology_iri}/1.0.0")))
    return str(ontology_iri)


def relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_DIR.resolve()))
    except ValueError:
        return str(path.resolve())


def parse_bound(value: str) -> float | None:
    text = str(value or "").strip()
    return None if not text else float(text)


def check_display_value(event: dict[str, str], candidate: dict[str, str]) -> None:
    kind = event.get("value_kind", "").strip().lower()
    display = candidate.get("display_value", "").strip()
    if kind != "literal_integer":
        return
    try:
        numeric = int(display)
    except ValueError as exc:
        raise ValueError(f"整数事件的display_value不是整数：{display!r}") from exc
    lower = parse_bound(event.get("allowed_min", ""))
    upper = parse_bound(event.get("allowed_max", ""))
    if lower is not None and numeric < lower:
        raise ValueError(f"候选值{numeric}低于下界{lower:g}")
    if upper is not None and numeric > upper:
        raise ValueError(f"候选值{numeric}高于上界{upper:g}")


def ready_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [row for row in rows if row.get("status", "").strip().upper() == "READY"]


def main() -> int:
    args = parse_args()
    for path in (EVENT_CSV, DOCUMENT_CSV, CANDIDATE_CSV, ORACLE_CSV):
        if not path.is_file():
            raise FileNotFoundError(path)

    events = ready_rows(load_csv(EVENT_CSV))
    documents = {row["document_id"]: row for row in ready_rows(load_csv(DOCUMENT_CSV))}
    candidate_rows = ready_rows(load_csv(CANDIDATE_CSV))
    if not events:
        raise RuntimeError("没有READY事件。请先完成人工录入并运行validate_semantic_benchmark_v2.py --require-ready")
    if not args.allow_less and len(events) < args.min_events:
        raise RuntimeError(f"READY事件只有{len(events)}个，少于{args.min_events}；调试可加--allow-less")

    candidates_by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in candidate_rows:
        candidates_by_event[row["event_id"]].append(row)

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    CANDIDATE_OWL_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    public_events: list[dict[str, Any]] = []
    effects: list[dict[str, Any]] = []
    log = ["语义歧义基准V2构建日志", "Oracle未参与候选形式安全筛选。", ""]

    for index, event in enumerate(events, start=1):
        event_id = event["event_id"]
        source_path = resolve_project_path(event["source_owl"])
        source_graph = load_graph(source_path)
        event_candidates = candidates_by_event.get(event_id, [])
        if not 2 <= len(event_candidates) <= 4:
            raise RuntimeError(f"{event_id}的READY候选数={len(event_candidates)}，要求2—4")

        public_candidates: list[dict[str, Any]] = []
        for candidate in event_candidates:
            candidate_id = candidate["candidate_id"]
            operation = parse_json_object(
                candidate["operation_json"], f"{event_id}/{candidate_id}/operation_json"
            )
            check_display_value(event, candidate)
            repaired = apply_operations(source_graph, [operation])
            removed, added = graph_delta(source_graph, repaired)
            expected_removed, expected_added = expected_graph_delta(operation, source_graph)
            minimal = (removed, added) == (expected_removed, expected_added)
            if not minimal:
                raise RuntimeError(
                    f"{event_id}/{candidate_id}图差异不符合单步算子："
                    f"实际=({removed},{added})，预期=({expected_removed},{expected_added})"
                )

            ontology_iri = set_ontology_identity(repaired, event_id, candidate_id)
            candidate_path = CANDIDATE_OWL_DIR / event_id / f"{candidate_id}.owl"
            candidate_path.parent.mkdir(parents=True, exist_ok=True)
            repaired.serialize(destination=candidate_path, format="xml", encoding="utf-8")
            reasoner = benchmark_validator.run_reasoner(candidate_path, args.timeout)
            formally_safe = reasoner.get("status") == "CONSISTENT" and minimal
            if not formally_safe:
                raise RuntimeError(
                    f"{event_id}/{candidate_id}未通过形式安全门禁："
                    f"Reasoner={reasoner.get('status')}，minimal={minimal}，"
                    f"message={reasoner.get('message', '')}"
                )

            effect = {
                "event_id": event_id,
                "candidate_id": candidate_id,
                "operator": operation["operator"],
                "candidate_owl": relpath(candidate_path),
                "ontology_iri": ontology_iri,
                "triples_removed": removed,
                "triples_added": added,
                "minimal_edit": minimal,
                "reasoner_result": reasoner.get("status"),
                "reasoner_runtime_ms": reasoner.get("runtime_ms", 0),
                "formally_safe": formally_safe,
            }
            effects.append(effect)
            public_candidates.append({
                "candidate_id": candidate_id,
                "description": candidate["description"],
                "display_value": candidate["display_value"],
                "operation": operation,
                "formal_effect": effect,
            })
            message = (
                f"[{event_id}/{candidate_id}] {operation['operator']} | "
                f"Reasoner={reasoner.get('status')} | delta=({removed},{added}) | PASS"
            )
            print(message)
            log.append(message)

        doc_payload: list[dict[str, Any]] = []
        for document_id in split_ids(event["document_ids"]):
            document = documents.get(document_id)
            if document is None:
                raise RuntimeError(f"{event_id}引用了非READY文档：{document_id}")
            document_path = DOCUMENT_DIR / document["file_name"]
            if not document_path.is_file():
                raise FileNotFoundError(document_path)
            doc_payload.append({
                "document_id": document_id,
                "file_name": document["file_name"],
                "authority": int(document["authority"]),
                "effective_from": document.get("effective_from", ""),
                "effective_to": document.get("effective_to", ""),
                "issuer": document.get("issuer", ""),
                "document_type": document.get("document_type", ""),
                "source_type": document.get("source_type", ""),
                "sha256": sha256_file(document_path),
            })

        public_events.append({
            "event_id": event_id,
            "split": event["split"].strip().lower(),
            "semantic_type": event["semantic_type"].strip().upper(),
            "domain": event.get("domain", ""),
            "title": event["title"],
            "case_context": event["case_context"],
            "target": {
                "subject_label": event["subject_label"],
                "predicate_label": event["predicate_label"],
                "value_kind": event["value_kind"],
                "allowed_min": event.get("allowed_min", ""),
                "allowed_max": event.get("allowed_max", ""),
            },
            "source_owl": relpath(source_path),
            "documents": doc_payload,
            "candidates": public_candidates,
        })
        print(f"[{index}/{len(events)}] {event_id}构建完成，形式安全候选={len(public_candidates)}")

    # 先构造公开负载，再读取私有Oracle；防止答案参与候选筛选。
    public_payload = {
        "schema_version": "2.0",
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "oracle_used_for_candidate_filtering": False,
        "events": public_events,
    }
    serialized_public = json.dumps(public_payload, ensure_ascii=False, indent=2)
    PUBLIC_EVENT_JSON.write_text(serialized_public, encoding="utf-8")

    oracle_rows = ready_rows(load_csv(ORACLE_CSV))
    oracle_by_event = {row["event_id"]: row for row in oracle_rows}
    for event in public_events:
        event_id = event["event_id"]
        oracle = oracle_by_event.get(event_id)
        if oracle is None:
            raise RuntimeError(f"{event_id}缺少READY Oracle")
        candidate_ids = {item["candidate_id"] for item in event["candidates"]}
        if oracle["oracle_candidate_id"] not in candidate_ids:
            raise RuntimeError(f"{event_id}的Oracle候选不在公开候选集合中")
    if "oracle_candidate_id" in serialized_public or "oracle_value" in serialized_public:
        raise RuntimeError("公开负载发生Oracle泄漏")

    write_csv(CANDIDATE_EFFECTS_CSV, effects)
    CANDIDATE_EFFECTS_JSON.write_text(
        json.dumps(effects, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # 公开清单不记录私有Oracle内容或哈希，避免答案侧信道。
    tracked_files = [EVENT_CSV, DOCUMENT_CSV, CANDIDATE_CSV]
    tracked_files += sorted(DOCUMENT_DIR.glob("*"))
    tracked_files += sorted({resolve_project_path(row["source_owl"]) for row in events})
    manifest = {
        "schema_version": "2.0",
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "event_count": len(public_events),
        "candidate_count": len(effects),
        "oracle_used_for_candidate_filtering": False,
        "private_oracle_integrity_checked": True,
        "public_event_file": relpath(PUBLIC_EVENT_JSON),
        "file_hashes": {
            relpath(path): sha256_file(path)
            for path in tracked_files
            if path.is_file()
        },
    }
    MANIFEST_JSON.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.extend([
        "",
        f"事件数={len(public_events)}，候选数={len(effects)}",
        "Oracle用于候选筛选=False",
        f"公开事件：{PUBLIC_EVENT_JSON}",
        f"候选效果：{CANDIDATE_EFFECTS_CSV}",
        f"清单：{MANIFEST_JSON}",
    ])
    LOG_FILE.write_text("\n".join(log) + "\n", encoding="utf-8")
    print(f"\n构建完成：事件={len(public_events)}，候选={len(effects)}")
    print(f"公开事件：{PUBLIC_EVENT_JSON}")
    print(f"候选效果：{CANDIDATE_EFFECTS_CSV}")
    print(f"清单：{MANIFEST_JSON}")
    print("[边界] Oracle没有参与候选形式安全筛选，只用于离线评估。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\n[构建停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
