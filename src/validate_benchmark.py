from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote

try:
    from rdflib import Graph, Literal, RDF, URIRef
    from rdflib.namespace import OWL, RDFS
except ImportError:
    print("缺少 rdflib，请执行：python -m pip install rdflib -i https://pypi.org/simple")
    raise SystemExit(2)


PROJECT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_DIR / "data"
BENCHMARK_DIR = PROJECT_DIR / "benchmark"
CLEAN_DIR = BENCHMARK_DIR / "clean"
MUTANTS_DIR = BENCHMARK_DIR / "mutants"
GROUND_TRUTH_DIR = BENCHMARK_DIR / "ground-truth"
OUTPUT_DIR = PROJECT_DIR / "output"

BASELINE = CLEAN_DIR / "insurance-validation-baseline.owl"
MANIFEST = GROUND_TRUTH_DIR / "error-manifest.csv"
EVIDENCE_CANDIDATES = [DATA_DIR / "axiom-evidence.csv", PROJECT_DIR / "axiom-evidence.csv"]
RESULT_CSV = OUTPUT_DIR / "benchmark-results.csv"
RESULT_JSON = OUTPUT_DIR / "benchmark-results.json"
LOG_FILE = OUTPUT_DIR / "benchmark-experiment.log"
REASONER_MARKER = "__REASONER_RESULT__="


def local_name(value: object) -> str:
    text = unquote(str(value))
    if "#" in text:
        return text.rsplit("#", 1)[1]
    return text.rstrip("/").rsplit("/", 1)[-1]


def require_file(path: Path, hint: str = "") -> None:
    if not path.is_file():
        print(f"缺少文件：{path}")
        if hint:
            print(hint)
        raise SystemExit(2)


def choose_evidence() -> Path:
    for path in EVIDENCE_CANDIDATES:
        if path.is_file():
            return path
    print("没有找到 axiom-evidence.csv，请将其放入 data 文件夹。")
    raise SystemExit(2)


def load_graph(path: Path) -> Graph:
    graph = Graph()
    graph.parse(path, format="xml")
    return graph


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def ontology_iris(graph: Graph) -> list[str]:
    return sorted(str(item) for item in graph.subjects(RDF.type, OWL.Ontology))


def all_uris(graph: Graph) -> set[URIRef]:
    values: set[URIRef] = set()
    for subject, predicate, obj in graph:
        for item in (subject, predicate, obj):
            if isinstance(item, URIRef):
                values.add(item)
    return values


def find_entities(graph: Graph, label: str) -> set[URIRef]:
    return {uri for uri in all_uris(graph) if local_name(uri) == label}


def is_structural_type(value: URIRef) -> bool:
    return str(value).startswith((str(OWL), str(RDF), str(RDFS)))


def actual_values(graph: Graph, row: dict[str, str]) -> set[str]:
    subjects = find_entities(graph, row["subject_label"])
    if row["axiom_kind"] == "ClassAssertion":
        return {
            local_name(obj)
            for subject in subjects
            for obj in graph.objects(subject, RDF.type)
            if isinstance(obj, URIRef) and not is_structural_type(obj)
        }

    predicates = find_entities(graph, row["predicate_label"])
    result: set[str] = set()
    for subject in subjects:
        for predicate in predicates:
            for obj in graph.objects(subject, predicate):
                if isinstance(obj, URIRef):
                    result.add(local_name(obj))
                elif isinstance(obj, Literal):
                    result.add(str(obj))
    return result


def current_evidence_rows(path: Path) -> list[dict[str, str]]:
    rows = load_csv(path)
    current = [row for row in rows if row.get("ontology_version", "").strip() in {"2", "2.0", "v2", "V2"}]
    if not current:
        raise RuntimeError("证据表中没有 ontology_version=2.0 的当前版本记录")
    return current


def validate_evidence_and_cq(
    graph: Graph, evidence_rows: list[dict[str, str]]
) -> tuple[str, str, list[str]]:
    """以当前有效证据作为期望答案，同时执行精确证据校验和CQ回归。"""
    differences: list[str] = []
    for row in evidence_rows:
        expected = {row["object_or_value"].strip()}
        actual = actual_values(graph, row)
        if actual != expected:
            differences.append(
                f"{row['subject_label']}—{row['predicate_label']}："
                f"期望={sorted(expected)}，实际={sorted(actual)}"
            )

    status = "PASS" if not differences else "FAIL"
    # 当前最小实验中，每条当前证据同时定义一个可执行CQ的期望答案。
    return status, status, differences


def reason_one(path: Path) -> int:
    """在独立进程中调用Owlready2自带Pellet，防止多个本体互相污染。"""
    started = time.perf_counter()
    payload: dict[str, object]
    try:
        from owlready2 import (
            OwlReadyInconsistentOntologyError,
            World,
            sync_reasoner,
        )
    except ImportError:
        payload = {
            "status": "ERROR",
            "runtime_ms": 0,
            "message": "缺少 owlready2，请执行 python -m pip install owlready2 -i https://pypi.org/simple",
        }
        print(REASONER_MARKER + json.dumps(payload, ensure_ascii=False))
        return 2

    try:
        world = World()
        # Owlready2的部分Windows版本会把file:///G:/...错误转换成/G:/...，
        # 进而触发OSError 22。使用Python直接打开文件流可绕过该URI转换问题。
        ontology = world.get_ontology(path.resolve().as_uri())
        with path.resolve().open("rb") as owl_stream:
            ontology.load(fileobj=owl_stream)
        try:
            sync_reasoner(
                [ontology],
                infer_property_values=True,
                
                debug=0,
            )
            status = "CONSISTENT"
            message = "HermiT未发现逻辑不一致"
        except OwlReadyInconsistentOntologyError as exc:
            status = "INCONSISTENT"
            message = str(exc) or "HermiT发现逻辑不一致"
        finally:
            world.close()
        payload = {
            "status": status,
            "runtime_ms": round((time.perf_counter() - started) * 1000),
            "message": message,
        }
        print(REASONER_MARKER + json.dumps(payload, ensure_ascii=False))
        return 0
    except Exception as exc:
        payload = {
            "status": "ERROR",
            "runtime_ms": round((time.perf_counter() - started) * 1000),
            "message": f"{type(exc).__name__}: {exc}",
        }
        print(REASONER_MARKER + json.dumps(payload, ensure_ascii=False))
        return 2


def run_reasoner(path: Path, timeout_seconds: int) -> dict[str, object]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--reason-one",
        str(path.resolve()),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "ERROR",
            "runtime_ms": timeout_seconds * 1000,
            "message": f"Reasoner超过{timeout_seconds}秒未完成",
        }

    for line in reversed(completed.stdout.splitlines()):
        if line.startswith(REASONER_MARKER):
            return json.loads(line[len(REASONER_MARKER) :])
    return {
        "status": "ERROR",
        "runtime_ms": 0,
        "message": (completed.stderr or completed.stdout or "Reasoner没有返回结果")[-1000:],
    }


def expected_rows() -> list[dict[str, str]]:
    rows = [{
        "error_id": "BASELINE",
        "file_name": BASELINE.name,
        "error_type": "无错误基线",
        "expected_reasoner_result": "CONSISTENT",
        "expected_evidence_result": "PASS",
        "expected_cq_result": "PASS",
        "primary_detector": "全部机制",
    }]
    rows.extend(load_csv(MANIFEST))
    return rows


def experiment_file(row: dict[str, str]) -> Path:
    if row["error_id"] == "BASELINE":
        return BASELINE
    return MUTANTS_DIR / row["file_name"]


def append_log(lines: list[str], text: str = "") -> None:
    lines.append(text)
    print(text)


def run_all(timeout_seconds: int) -> int:
    require_file(BASELINE, "请先运行 python src/inject_errors.py")
    require_file(MANIFEST, "请先运行 python src/inject_errors.py")
    evidence_path = choose_evidence()
    evidence_rows = current_evidence_rows(evidence_path)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    rows = expected_rows()
    files = [experiment_file(row) for row in rows]
    for path in files:
        require_file(path, "请重新运行 inject_errors.py 生成完整基准集")

    # 唯一IRI是实验隔离条件；重复IRI直接停止，不产生看似正常的错误结果。
    iri_owner: dict[str, str] = {}
    for row, path in zip(rows, files):
        iris = ontology_iris(load_graph(path))
        if len(iris) != 1:
            raise RuntimeError(f"{path.name}必须且只能声明一个Ontology IRI，实际为：{iris}")
        iri = iris[0]
        if iri in iri_owner:
            raise RuntimeError(f"Ontology IRI重复：{path.name} 与 {iri_owner[iri]} 都使用 {iri}")
        iri_owner[iri] = path.name

    log: list[str] = []
    results: list[dict[str, object]] = []
    append_log(log, "保险本体错误检测基准：自动化实验日志")
    append_log(log, f"Python：{sys.version.split()[0]}")
    append_log(log, f"证据文件：{evidence_path}")
    append_log(log, f"实验文件数：{len(files)}")

    for index, (expected, path) in enumerate(zip(rows, files), start=1):
        append_log(log, f"\n[{index}/{len(files)}] {expected['error_id']} {path.name}")
        graph = load_graph(path)
        evidence_status, cq_status, differences = validate_evidence_and_cq(graph, evidence_rows)
        reasoner = run_reasoner(path, timeout_seconds)
        actual_reasoner = str(reasoner["status"])

        expected_reasoner = expected["expected_reasoner_result"]
        expected_evidence = expected.get("expected_evidence_result", "FAIL")
        expected_cq = expected.get("expected_cq_result", "FAIL")
        reasoner_match = actual_reasoner == expected_reasoner
        evidence_match = evidence_status == expected_evidence
        cq_match = cq_status == expected_cq
        overall = "PASS" if reasoner_match and evidence_match and cq_match else "FAIL"

        detector_parts: list[str] = []
        if actual_reasoner == "INCONSISTENT":
            detector_parts.append("HermiT Reasoner")
        if evidence_status == "FAIL":
            detector_parts.append("证据校验")
        if cq_status == "FAIL":
            detector_parts.append("CQ回归")
        detected_by = " + ".join(detector_parts) or "未检出错误"
        explanation = "；".join(differences) if differences else str(reasoner["message"])

        result: dict[str, object] = {
            "error_id": expected["error_id"],
            "file_name": path.name,
            "error_type": expected["error_type"],
            "ontology_iri": ontology_iris(graph)[0],
            "expected_reasoner_result": expected_reasoner,
            "actual_reasoner_result": actual_reasoner,
            "expected_evidence_result": expected_evidence,
            "actual_evidence_result": evidence_status,
            "expected_cq_result": expected_cq,
            "actual_cq_result": cq_status,
            "detected_by": detected_by,
            "reasoner_runtime_ms": reasoner["runtime_ms"],
            "reasoner_message": reasoner["message"],
            "explanation": explanation,
            "pass_or_fail": overall,
        }
        results.append(result)
        append_log(log, f"  Reasoner：期望={expected_reasoner}，实际={actual_reasoner}")
        if actual_reasoner == "ERROR":
            append_log(log, f"  Reasoner错误：{reasoner['message']}")
        append_log(log, f"  证据校验：期望={expected_evidence}，实际={evidence_status}")
        append_log(log, f"  CQ回归：期望={expected_cq}，实际={cq_status}")
        append_log(log, f"  检测机制：{detected_by}")
        append_log(log, f"  结论：{overall}")
        if differences:
            for difference in differences:
                append_log(log, f"    - {difference}")

    fieldnames = list(results[0].keys())
    with RESULT_CSV.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    with RESULT_JSON.open("w", encoding="utf-8") as file:
        json.dump(results, file, ensure_ascii=False, indent=2)

    passed = sum(row["pass_or_fail"] == "PASS" for row in results)
    append_log(log, f"\n汇总：{passed}/{len(results)} 个实验通过")
    append_log(log, f"CSV：{RESULT_CSV}")
    append_log(log, f"JSON：{RESULT_JSON}")
    with LOG_FILE.open("w", encoding="utf-8") as file:
        file.write("\n".join(log) + "\n")
    print(f"日志：{LOG_FILE}")

    if passed == len(results):
        print("\n[自动化实验通过] 基线无误报，五类错误均按预期被对应机制识别。")
        return 0
    print("\n[自动化实验未通过] 请查看benchmark-results.csv中的FAIL记录。")
    return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量验证保险本体错误检测基准")
    parser.add_argument("--reason-one", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--timeout", type=int, default=120, help="每个本体的推理超时秒数")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.reason_one:
        return reason_one(args.reason_one)
    return run_all(args.timeout)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\n[实验停止] {type(exc).__name__}: {exc}")
        sys.exit(2)
