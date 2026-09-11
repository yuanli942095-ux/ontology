from __future__ import annotations

"""Candidate-blind context, prompt, and validation for Direct Repair IR v1."""

import copy
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from rdflib import Literal, URIRef

from run_external_real_v1_symbolic_closure import load_graph, term_from_spec


SCHEMA_VERSION = "predicted-repair-ir-v1"
SUPPORTED_OPERATION = "UPDATE_LITERAL"
XSD_STRING = "http://www.w3.org/2001/XMLSchema#string"
METHOD_DIR = Path(__file__).resolve().parents[1] / "method" / "ecr-ir-gamma"
SCHEMA_PATH = METHOD_DIR / "predicted-repair-ir-v1.schema.json"
BENCHMARK_SLUG_STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "are", "not",
    "must", "should", "shall", "may", "can", "when", "where", "which",
    "into", "than", "then", "been", "have", "has", "its", "their",
    "section", "rfc",
}


@lru_cache(maxsize=1)
def load_schema_contract() -> dict[str, Any]:
    """Load the method-owned schema and reject accidental contract drift."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    branches = schema.get("oneOf", [])
    repair = next(
        (branch for branch in branches if branch.get("properties", {}).get("decision", {}).get("const") == "REPAIR"),
        None,
    )
    terminal = next(
        (branch for branch in branches if set(branch.get("properties", {}).get("decision", {}).get("enum", []))
         == {"NO_CHANGE", "ABSTAIN"}),
        None,
    )
    if repair is None or terminal is None:
        raise RuntimeError(f"invalid Direct Repair IR schema branches: {SCHEMA_PATH}")
    if repair.get("properties", {}).get("schema_version", {}).get("const") != SCHEMA_VERSION:
        raise RuntimeError("Direct Repair IR schema_version drift")
    if repair.get("properties", {}).get("operation", {}).get("const") != SUPPORTED_OPERATION:
        raise RuntimeError("Direct Repair IR operation drift")
    return schema


def schema_sha256() -> str:
    load_schema_contract()
    return hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()


def schema_top_level_fields(decision: str) -> tuple[set[str], set[str]]:
    schema = load_schema_contract()
    for branch in schema["oneOf"]:
        decision_spec = branch["properties"]["decision"]
        if decision_spec.get("const") == decision or decision in decision_spec.get("enum", []):
            return set(branch["properties"]), set(branch["required"])
    return set(), set()


def ontology_literal_assertions(source_path: Path) -> list[dict[str, str]]:
    graph = load_graph(source_path)
    rows = []
    for subject, predicate, value in graph:
        if not isinstance(value, Literal):
            continue
        rows.append(
            {
                "subject_iri": str(subject),
                "predicate_iri": str(predicate),
                "lexical": str(value),
                "datatype": str(value.datatype or XSD_STRING),
            }
        )
    return sorted(rows, key=lambda row: (row["subject_iri"], row["predicate_iri"], row["lexical"]))


def build_direct_ir_prompt(event: dict[str, str], evidence: str, assertions: list[dict[str, str]]) -> str:
    assertion_text = "\n".join(
        f"- subject_iri={row['subject_iri']}\n  predicate_iri={row['predicate_iri']}\n"
        f"  current_literal={row['lexical']}\n  datatype={row['datatype']}"
        for row in assertions
    )
    return f"""Generate one candidate-blind ontology Repair IR from the supplied normative evidence.

You may use only the evidence, public event metadata, and current ontology assertions below.
No repair candidates, candidate IDs, oracle answer, or target ontology are available.
Do not output OWL. Do not invent an IRI. Copy subject_iri, predicate_iri, old literal,
and datatype exactly from the matching current ontology assertion. Select exactly one
contiguous, sufficient supporting span from one SOURCE_WINDOW as evidence_spans[0],
copying it verbatim without the [SOURCE_WINDOW_n] marker. Put the same evidence-grounded
surface text in new_value.lexical.
A deterministic candidate-blind normalizer will encode that surface text for Gamma.
Return JSON only.

schema_version: {SCHEMA_VERSION}
event_id: {event.get('event_id', '')}
semantic_type: {event.get('semantic_type', '')}
subject_label: {event.get('subject_label', '')}
predicate_label: {event.get('predicate_label', '')}
case_context: {event.get('case_context', '')}

CURRENT ONTOLOGY LITERAL ASSERTIONS:
{assertion_text}

NORMATIVE EVIDENCE:
{evidence}

For REPAIR return exactly:
{{
  "schema_version": "{SCHEMA_VERSION}",
  "decision": "REPAIR",
  "operation": "UPDATE_LITERAL",
  "target": {{
    "subject_iri": "...",
    "predicate_iri": "...",
    "old_value": {{"kind": "literal", "lexical": "...", "datatype": "..."}}
  }},
  "replacement": {{
    "new_value": {{"kind": "literal", "lexical": "...", "datatype": "..."}}
  }},
  "evidence_spans": ["verbatim supporting span"],
  "confidence": 0.0
}}

If the evidence supports no change, return only schema_version, decision=NO_CHANGE, and reason.
If evidence is insufficient or ambiguous, return only schema_version, decision=ABSTAIN, and reason.
"""


def iri_valid(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    parsed = urlparse(value)
    return bool(parsed.scheme)


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def benchmark_value_slug(text: str, limit: int = 9) -> str:
    """Reproduce the public benchmark builder's documented value encoding."""
    raw = [
        token.lower().strip("-")
        for token in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", text)
    ]
    words: list[str] = []
    for token in raw:
        if token in BENCHMARK_SLUG_STOPWORDS or token in words:
            continue
        words.append(token)
        if len(words) >= limit:
            break
    return "_".join(words or raw[:limit] or ["requirement"])


def benchmark_literal_from_span(old_lexical: str, evidence_span: str) -> str:
    dimension = old_lexical.split("=", 1)[0]
    return f"{dimension}={benchmark_value_slug(evidence_span)}"


def source_windows(evidence: str) -> list[tuple[str, str]]:
    matches = re.finditer(
        r"(?s)\[SOURCE_WINDOW_(\d+)\]\s*(.*?)(?=\n\s*\[SOURCE_WINDOW_\d+\]|\Z)",
        evidence,
    )
    return [(match.group(1), match.group(2).strip()) for match in matches if match.group(2).strip()]


def canonicalize_surface(ir: dict[str, Any]) -> dict[str, Any]:
    """Candidate-blind conversion from evidence-grounded surface IR to Gamma IR."""
    result = copy.deepcopy(ir)
    if result.get("decision") != "REPAIR":
        if isinstance(result.get("reason"), str):
            result["reason"] = normalize_space(result["reason"])
        return result
    for branch, field in (("target", "old_value"), ("replacement", "new_value")):
        spec = result.get(branch, {}).get(field, {})
        if isinstance(spec, dict) and isinstance(spec.get("lexical"), str):
            spec["lexical"] = normalize_space(spec["lexical"])
    result["evidence_spans"] = [normalize_space(str(span)) for span in result.get("evidence_spans", [])]
    old_spec = result.get("target", {}).get("old_value", {})
    new_spec = result.get("replacement", {}).get("new_value", {})
    spans = result.get("evidence_spans", [])
    if (
        isinstance(old_spec, dict)
        and isinstance(old_spec.get("lexical"), str)
        and isinstance(new_spec, dict)
        and spans
    ):
        new_spec["lexical"] = benchmark_literal_from_span(old_spec["lexical"], spans[0])
    return result


def validate_predicted_ir(ir: Any, *, source_path: Path, evidence: str) -> dict[str, Any]:
    load_schema_contract()
    errors: list[str] = []
    if not isinstance(ir, dict):
        return {"status": "INVALID_IR", "errors": ["root_not_object"]}
    if ir.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version")
    decision = ir.get("decision")
    if decision in {"NO_CHANGE", "ABSTAIN"}:
        allowed, required = schema_top_level_fields(decision)
        if set(ir) - allowed or not required.issubset(ir):
            errors.append("unexpected_fields")
        if not isinstance(ir.get("reason"), str) or not ir["reason"].strip():
            errors.append("reason")
        return {"status": "VALID" if not errors else "INVALID_IR", "errors": errors}
    if decision != "REPAIR":
        errors.append("decision")
        return {"status": "INVALID_IR", "errors": errors}

    allowed, required = schema_top_level_fields(decision)
    if set(ir) - allowed or not required.issubset(ir):
        errors.append("unexpected_fields")
    if ir.get("operation") != SUPPORTED_OPERATION:
        return {"status": "UNSUPPORTED", "errors": ["operation"]}
    target = ir.get("target")
    replacement = ir.get("replacement")
    if not isinstance(target, dict) or not isinstance(replacement, dict):
        return {"status": "INVALID_IR", "errors": errors + ["target_or_replacement"]}
    if set(target) != {"subject_iri", "predicate_iri", "old_value"}:
        errors.append("target_fields")
    if set(replacement) != {"new_value"}:
        errors.append("replacement_fields")
    old_value = target.get("old_value")
    new_value = replacement.get("new_value")
    for name, spec in (("old_value", old_value), ("new_value", new_value)):
        if not isinstance(spec, dict) or set(spec) != {"kind", "lexical", "datatype"}:
            errors.append(f"{name}_fields")
            continue
        if spec.get("kind") != "literal" or not isinstance(spec.get("lexical"), str) or not spec["lexical"].strip():
            errors.append(f"{name}_literal")
        if not iri_valid(spec.get("datatype")):
            errors.append(f"{name}_datatype")
    if not iri_valid(target.get("subject_iri")):
        errors.append("subject_iri")
    if not iri_valid(target.get("predicate_iri")):
        errors.append("predicate_iri")
    confidence = ir.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
        errors.append("confidence")
    spans = ir.get("evidence_spans")
    if not isinstance(spans, list) or not spans or any(not isinstance(span, str) or not span.strip() for span in spans):
        errors.append("evidence_spans")
    elif any(normalize_space(span).lower() not in normalize_space(evidence).lower() for span in spans):
        errors.append("evidence_span_not_verbatim")
    if errors:
        return {"status": "INVALID_IR", "errors": sorted(set(errors))}

    operation = to_gamma_operation(ir)
    graph = load_graph(source_path)
    old_triple = (
        URIRef(operation["subject_iri"]), URIRef(operation["predicate_iri"]), term_from_spec(operation["old_value"])
    )
    if old_triple not in graph:
        return {"status": "PRECONDITION_FAILED", "errors": ["old_literal_not_found"]}
    if operation["old_value"]["datatype"] != operation["new_value"]["datatype"]:
        return {"status": "PRECONDITION_FAILED", "errors": ["datatype_mismatch"]}
    return {"status": "VALID", "errors": []}


def to_gamma_operation(ir: dict[str, Any]) -> dict[str, Any]:
    return {
        "operator": "REPLACE_PROPERTY_VALUE",
        "subject_iri": ir["target"]["subject_iri"],
        "predicate_iri": ir["target"]["predicate_iri"],
        "old_value": copy.deepcopy(ir["target"]["old_value"]),
        "new_value": copy.deepcopy(ir["replacement"]["new_value"]),
    }
