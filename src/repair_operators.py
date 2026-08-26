from __future__ import annotations

"""受限、可审计的 RDF/OWL 修复算子。

本文件向后兼容原来的五个 ABox 算子，并增加 TBox 的类层级、属性定义域和
值域算子。所有 IRI 都必须已经出现在待修复本体中；脚本不会接受模型凭空
生成的实体 IRI，也不会执行任意 OWL 文本。
"""

from copy import deepcopy
from typing import Any

from rdflib import Graph, Literal, RDF, RDFS, URIRef


ABOX_PROPERTY_OPERATORS = {
    "REPLACE_PROPERTY_VALUE",
    "ADD_PROPERTY_VALUE",
    "REMOVE_PROPERTY_VALUE",
}
ABOX_CLASS_OPERATORS = {
    "ADD_CLASS_ASSERTION",
    "REMOVE_CLASS_ASSERTION",
}
SUBCLASS_OPERATORS = {
    "ADD_SUBCLASS_AXIOM",
    "REMOVE_SUBCLASS_AXIOM",
    "REPLACE_SUPERCLASS",
}
DOMAIN_OPERATORS = {
    "ADD_PROPERTY_DOMAIN",
    "REMOVE_PROPERTY_DOMAIN",
    "REPLACE_PROPERTY_DOMAIN",
}
RANGE_OPERATORS = {
    "ADD_PROPERTY_RANGE",
    "REMOVE_PROPERTY_RANGE",
    "REPLACE_PROPERTY_RANGE",
}
ALLOWED_OPERATORS = (
    ABOX_PROPERTY_OPERATORS
    | ABOX_CLASS_OPERATORS
    | SUBCLASS_OPERATORS
    | DOMAIN_OPERATORS
    | RANGE_OPERATORS
)


def clone_graph(source: Graph) -> Graph:
    target = Graph(identifier=source.identifier)
    for prefix, namespace in source.namespaces():
        target.bind(prefix, namespace)
    for triple in source:
        target.add(triple)
    return target


def all_uris(graph: Graph) -> set[URIRef]:
    result: set[URIRef] = set()
    for subject, predicate, obj in graph:
        for value in (subject, predicate, obj):
            if isinstance(value, URIRef):
                result.add(value)
    return result


def require_existing_iri(graph: Graph, value: object, role: str) -> URIRef:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{role}不能为空")
    iri = URIRef(text)
    if iri not in all_uris(graph):
        raise ValueError(f"拒绝使用本体中不存在的IRI：{role}={iri}")
    return iri


def term_to_spec(term: URIRef | Literal) -> dict[str, str]:
    if isinstance(term, URIRef):
        return {"kind": "iri", "iri": str(term)}
    if isinstance(term, Literal):
        result = {"kind": "literal", "lexical": str(term)}
        if term.datatype:
            result["datatype"] = str(term.datatype)
        if term.language:
            result["language"] = term.language
        return result
    raise TypeError(f"不支持的RDF值类型：{type(term).__name__}")


def spec_to_term(
    graph: Graph,
    spec: dict[str, str],
    require_existing_iri_value: bool | None = None,
    **legacy_kwargs: Any,
) -> URIRef | Literal:
    """把白名单值规范转换为 RDF term。

    ``require_existing_iri`` 是旧脚本使用的关键字，继续兼容。
    """
    if "require_existing_iri" in legacy_kwargs:
        require_existing_iri_value = bool(legacy_kwargs["require_existing_iri"])
    require_existing = bool(require_existing_iri_value)
    kind = spec.get("kind")
    if kind == "iri":
        iri_text = spec.get("iri", "").strip()
        if not iri_text:
            raise ValueError("IRI值不能为空")
        iri = URIRef(iri_text)
        if require_existing and iri not in all_uris(graph):
            raise ValueError(f"拒绝使用本体中不存在的IRI：{iri}")
        return iri
    if kind == "literal":
        lexical = spec.get("lexical")
        if lexical is None:
            raise ValueError("literal缺少lexical")
        datatype = URIRef(spec["datatype"]) if spec.get("datatype") else None
        language = spec.get("language") or None
        if datatype is not None and language is not None:
            raise ValueError("literal不能同时具有datatype和language")
        return Literal(lexical, datatype=datatype, lang=language)
    raise ValueError(f"未知值类型：{kind}")


def operation_key(operation: dict[str, Any]) -> str:
    structural_keys = (
        "operator",
        "subject_iri",
        "predicate_iri",
        "class_iri",
        "subclass_iri",
        "superclass_iri",
        "old_superclass_iri",
        "new_superclass_iri",
        "property_iri",
        "domain_iri",
        "old_domain_iri",
        "new_domain_iri",
        "range_iri",
        "old_range_iri",
        "new_range_iri",
        "old_value",
        "new_value",
    )
    return repr(tuple((key, operation.get(key)) for key in structural_keys))


def _require_fields(operation: dict[str, Any], operator: str, fields: tuple[str, ...]) -> None:
    missing = [field for field in fields if not operation.get(field)]
    if missing:
        raise ValueError(f"{operator}缺少字段：{missing}")


def validate_operation_schema(operation: dict[str, Any]) -> None:
    operator = operation.get("operator")
    if operator not in ALLOWED_OPERATORS:
        raise ValueError(f"不允许的修复算子：{operator}")

    if operator in ABOX_CLASS_OPERATORS:
        _require_fields(operation, operator, ("subject_iri", "class_iri"))
        return
    if operator in ABOX_PROPERTY_OPERATORS:
        _require_fields(operation, operator, ("subject_iri", "predicate_iri"))
        if operator in {"REPLACE_PROPERTY_VALUE", "REMOVE_PROPERTY_VALUE"}:
            _require_fields(operation, operator, ("old_value",))
        if operator in {"REPLACE_PROPERTY_VALUE", "ADD_PROPERTY_VALUE"}:
            _require_fields(operation, operator, ("new_value",))
        return
    if operator in {"ADD_SUBCLASS_AXIOM", "REMOVE_SUBCLASS_AXIOM"}:
        _require_fields(operation, operator, ("subclass_iri", "superclass_iri"))
        return
    if operator == "REPLACE_SUPERCLASS":
        _require_fields(
            operation,
            operator,
            ("subclass_iri", "old_superclass_iri", "new_superclass_iri"),
        )
        return
    if operator in {"ADD_PROPERTY_DOMAIN", "REMOVE_PROPERTY_DOMAIN"}:
        _require_fields(operation, operator, ("property_iri", "domain_iri"))
        return
    if operator == "REPLACE_PROPERTY_DOMAIN":
        _require_fields(
            operation,
            operator,
            ("property_iri", "old_domain_iri", "new_domain_iri"),
        )
        return
    if operator in {"ADD_PROPERTY_RANGE", "REMOVE_PROPERTY_RANGE"}:
        _require_fields(operation, operator, ("property_iri", "range_iri"))
        return
    if operator == "REPLACE_PROPERTY_RANGE":
        _require_fields(
            operation,
            operator,
            ("property_iri", "old_range_iri", "new_range_iri"),
        )


def _add_new(graph: Graph, triple: tuple[URIRef, URIRef, URIRef], label: str) -> None:
    if triple in graph:
        raise ValueError(f"{label}已经存在，拒绝无效新增：{triple}")
    graph.add(triple)


def _remove_existing(
    graph: Graph,
    triple: tuple[URIRef, URIRef, URIRef] | tuple[URIRef, URIRef, URIRef | Literal],
    label: str,
) -> None:
    if triple not in graph:
        raise ValueError(f"待删除的{label}不存在：{triple}")
    graph.remove(triple)


def apply_operation(graph: Graph, operation: dict[str, Any]) -> None:
    """应用一个有限修复算子；所有新增实体 IRI 必须已存在。"""
    validate_operation_schema(operation)
    operator = str(operation["operator"])

    if operator in ABOX_CLASS_OPERATORS:
        subject = require_existing_iri(graph, operation["subject_iri"], "subject_iri")
        class_iri = require_existing_iri(graph, operation["class_iri"], "class_iri")
        triple = (subject, RDF.type, class_iri)
        if operator == "REMOVE_CLASS_ASSERTION":
            _remove_existing(graph, triple, "类别断言")
        else:
            _add_new(graph, triple, "类别断言")
        return

    if operator in ABOX_PROPERTY_OPERATORS:
        subject = require_existing_iri(graph, operation["subject_iri"], "subject_iri")
        predicate = require_existing_iri(graph, operation["predicate_iri"], "predicate_iri")
        if operator in {"REPLACE_PROPERTY_VALUE", "REMOVE_PROPERTY_VALUE"}:
            old_spec = operation["old_value"]
            old_term = spec_to_term(
                graph,
                old_spec,
                require_existing_iri_value=old_spec.get("kind") == "iri",
            )
            _remove_existing(graph, (subject, predicate, old_term), "旧断言")
        if operator in {"REPLACE_PROPERTY_VALUE", "ADD_PROPERTY_VALUE"}:
            new_spec = operation["new_value"]
            new_term = spec_to_term(
                graph,
                new_spec,
                require_existing_iri_value=new_spec.get("kind") == "iri",
            )
            graph.add((subject, predicate, new_term))
        return

    if operator in SUBCLASS_OPERATORS:
        subclass = require_existing_iri(graph, operation["subclass_iri"], "subclass_iri")
        if operator == "REPLACE_SUPERCLASS":
            old_super = require_existing_iri(
                graph, operation["old_superclass_iri"], "old_superclass_iri"
            )
            new_super = require_existing_iri(
                graph, operation["new_superclass_iri"], "new_superclass_iri"
            )
            if old_super == new_super:
                raise ValueError("REPLACE_SUPERCLASS的新旧父类不能相同")
            _remove_existing(graph, (subclass, RDFS.subClassOf, old_super), "父类公理")
            graph.add((subclass, RDFS.subClassOf, new_super))
        else:
            superclass = require_existing_iri(
                graph, operation["superclass_iri"], "superclass_iri"
            )
            triple = (subclass, RDFS.subClassOf, superclass)
            if operator == "ADD_SUBCLASS_AXIOM":
                _add_new(graph, triple, "父类公理")
            else:
                _remove_existing(graph, triple, "父类公理")
        return

    if operator in DOMAIN_OPERATORS | RANGE_OPERATORS:
        prop = require_existing_iri(graph, operation["property_iri"], "property_iri")
        is_domain = operator in DOMAIN_OPERATORS
        predicate = RDFS.domain if is_domain else RDFS.range
        noun = "定义域" if is_domain else "值域"
        field = "domain_iri" if is_domain else "range_iri"
        old_field = "old_domain_iri" if is_domain else "old_range_iri"
        new_field = "new_domain_iri" if is_domain else "new_range_iri"

        if operator.startswith("REPLACE_"):
            old_class = require_existing_iri(graph, operation[old_field], old_field)
            new_class = require_existing_iri(graph, operation[new_field], new_field)
            if old_class == new_class:
                raise ValueError(f"{operator}的新旧{noun}不能相同")
            _remove_existing(graph, (prop, predicate, old_class), noun)
            graph.add((prop, predicate, new_class))
        else:
            class_iri = require_existing_iri(graph, operation[field], field)
            triple = (prop, predicate, class_iri)
            if operator.startswith("ADD_"):
                _add_new(graph, triple, noun)
            else:
                _remove_existing(graph, triple, noun)


def apply_operations(source: Graph, operations: list[dict[str, Any]]) -> Graph:
    repaired = clone_graph(source)
    for operation in operations:
        apply_operation(repaired, deepcopy(operation))
    return repaired


def expected_graph_delta(operation: dict[str, Any], source: Graph) -> tuple[int, int]:
    """返回有限算子的预期 (删除三元组数, 新增三元组数)。"""
    validate_operation_schema(operation)
    operator = str(operation["operator"])
    add_ops = {
        "ADD_PROPERTY_VALUE",
        "ADD_CLASS_ASSERTION",
        "ADD_SUBCLASS_AXIOM",
        "ADD_PROPERTY_DOMAIN",
        "ADD_PROPERTY_RANGE",
    }
    remove_ops = {
        "REMOVE_PROPERTY_VALUE",
        "REMOVE_CLASS_ASSERTION",
        "REMOVE_SUBCLASS_AXIOM",
        "REMOVE_PROPERTY_DOMAIN",
        "REMOVE_PROPERTY_RANGE",
    }
    if operator in add_ops:
        return 0, 1
    if operator in remove_ops:
        return 1, 0

    # REPLACE 可能把旧值替换成一个已经存在的三元组；RDF图不会保存重复项。
    if operator == "REPLACE_PROPERTY_VALUE":
        subject = URIRef(operation["subject_iri"])
        predicate = URIRef(operation["predicate_iri"])
        new_spec = operation["new_value"]
        new_term = spec_to_term(
            source,
            new_spec,
            require_existing_iri_value=new_spec.get("kind") == "iri",
        )
        return (1, 0) if (subject, predicate, new_term) in source else (1, 1)
    if operator == "REPLACE_SUPERCLASS":
        new_triple = (
            URIRef(operation["subclass_iri"]),
            RDFS.subClassOf,
            URIRef(operation["new_superclass_iri"]),
        )
        return (1, 0) if new_triple in source else (1, 1)
    if operator == "REPLACE_PROPERTY_DOMAIN":
        new_triple = (
            URIRef(operation["property_iri"]),
            RDFS.domain,
            URIRef(operation["new_domain_iri"]),
        )
        return (1, 0) if new_triple in source else (1, 1)
    if operator == "REPLACE_PROPERTY_RANGE":
        new_triple = (
            URIRef(operation["property_iri"]),
            RDFS.range,
            URIRef(operation["new_range_iri"]),
        )
        return (1, 0) if new_triple in source else (1, 1)
    raise ValueError(f"未知算子：{operator}")
