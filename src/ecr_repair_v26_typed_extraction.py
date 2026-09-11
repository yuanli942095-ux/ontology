from __future__ import annotations

"""Pre-extraction construct detection and construct-specific prompts."""

import json
import re
from typing import Any


NUMERIC_HINTS = {"age", "limit", "threshold", "duration", "period", "timeout", "rate", "size", "length", "number", "percentage", "amount", "ttl"}
ENUM_HINTS = {"enumeration", "enum", "members", "membership", "values", "functions", "methods", "algorithms", "categories"}
CONDITION_MARKERS = re.compile(r"\b(if|when|unless|except|provided that|depending on|where|only if|as long as)\b", re.I)


def words(value: str) -> set[str]:
    return {token.casefold() for token in re.findall(r"[A-Za-z]+", value)}


def detect_construct(event: dict[str, Any], evidence: str) -> str:
    label = words(event.get("target", {}).get("predicate_label", ""))
    context = words(event.get("case_context", ""))
    if label & NUMERIC_HINTS or (label & {"maximum", "minimum"}): return "NUMERIC_THRESHOLD"
    if label & ENUM_HINTS: return "ENUMERATION"
    if CONDITION_MARKERS.search(evidence) or context & {"condition", "exception", "eligible", "applicable"}: return "CONDITIONAL_RULE"
    return "UNSUPPORTED_IN_PILOT"


def build_typed_prompt(event: dict[str, Any], evidence: str, construct: str, window_map: dict[str, str]) -> str:
    common = f"""Extract one target-property semantic record. Return JSON only.
You cannot see candidates, semantic type, partition, or Oracle. Use only the requested
subject and predicate. Copy every evidence quote exactly from one SOURCE_WINDOW. Do not
extract neighboring properties. Do not infer missing required slots; return status=INCOMPLETE.

event_id: {event['event_id']}
construct: {construct}
subject_label: {event['target']['subject_label']}
predicate_label: {event['target']['predicate_label']}
case_context: {event.get('case_context','')}
window_document_map: {json.dumps(window_map,ensure_ascii=False,sort_keys=True)}

EVIDENCE:
{evidence}
"""
    if construct == "NUMERIC_THRESHOLD":
        contract = """Return {"schema_version":"typed-frame-v1","event_id":"...","construct":"NUMERIC_THRESHOLD","status":"COMPLETE|INCOMPLETE","record":{"window_id":"SOURCE_WINDOW_n","quote":"exact quote","comparator":"MINIMUM|MAXIMUM|EQUAL|RANGE","numeric_value":"number","range_end":null,"unit":"canonical singular unit","conditions":[],"exceptions":[],"modality":"MUST|SHOULD|MAY|DESCRIPTIVE"}}."""
    elif construct == "ENUMERATION":
        contract = """Return {"schema_version":"typed-frame-v1","event_id":"...","construct":"ENUMERATION","status":"COMPLETE|INCOMPLETE","record":{"window_id":"SOURCE_WINDOW_n","quote":"exact quote","enumeration_members":["complete","members"],"closed_world":true,"conditions":[],"exceptions":[],"modality":"MUST|SHOULD|MAY|DESCRIPTIVE"}}."""
    elif construct == "CONDITIONAL_RULE":
        contract = """Return {"schema_version":"typed-frame-v1","event_id":"...","construct":"CONDITIONAL_RULE","status":"COMPLETE|INCOMPLETE","record":{"window_id":"SOURCE_WINDOW_n","quote":"exact quote","core_action":"compact target-property value","polarity":"POSITIVE|NEGATIVE","modality":"MUST|MUST_NOT|SHOULD|SHOULD_NOT|MAY|DESCRIPTIVE","conditions":["all applicability conditions"],"exceptions":["all explicit exceptions"],"population":"explicit population or GLOBAL"}}."""
    else:
        raise ValueError(f"unsupported construct: {construct}")
    return common + "\n" + contract + "\nRequired slots must not be null, empty, or hidden inside core_action."


def validate_typed_record(payload: dict[str, Any], event: dict[str, Any], construct: str, evidence: str) -> list[str]:
    errors=[]
    if payload.get("schema_version")!="typed-frame-v1" or payload.get("event_id")!=event["event_id"] or payload.get("construct")!=construct:errors.append("contract")
    if payload.get("status")!="COMPLETE":errors.append("incomplete")
    record=payload.get("record") if isinstance(payload.get("record"),dict) else {}
    quote=record.get("quote")
    if not isinstance(quote,str) or quote not in evidence:errors.append("quote_not_verbatim")
    if not re.fullmatch(r"SOURCE_WINDOW_\d+",str(record.get("window_id",""))):errors.append("window_id")
    if construct=="NUMERIC_THRESHOLD":
        for key in ("comparator","numeric_value","unit"): 
            if not str(record.get(key,"" )).strip():errors.append(key)
    elif construct=="ENUMERATION":
        if not isinstance(record.get("enumeration_members"),list) or not record["enumeration_members"]:errors.append("enumeration_members")
    elif construct=="CONDITIONAL_RULE":
        for key in ("core_action","polarity","modality","population"):
            if not str(record.get(key,"" )).strip():errors.append(key)
        if not record.get("conditions") and not record.get("exceptions"):errors.append("conditions_or_exceptions")
    return sorted(set(errors))
