from __future__ import annotations

import json
from typing import Any


def build_candidate_construction_prompt(event: dict[str, Any], evidence: str, assertion: dict[str, str]) -> str:
    return f"""Extract property-value slots for deterministic finite candidate construction.

This stage is independent of semantic-frame extraction and cannot see its output, gate
decision, benchmark partition, semantic type, existing candidate files, or Oracle.
Extract 1 to 6 target-property frames. Do not directly choose a repair. Each frame must
contain one exact contiguous quote and explicit value slots. Preserve the source wording
in surface_value. Put a concise ontology-ready phrase in core_value. Separate modality,
negation, numeric value, unit, conditions, exceptions, and complete enumeration members.
For cross-sentence rules, emit a scope_complete_value that combines only jointly applicable
evidence. Do not omit alternatives introduced by "either", "or", "unless", or exceptions.

event_id: {event['event_id']}
case_context: {event.get('case_context', '')}
subject_label: {event.get('target', {}).get('subject_label', '')}
predicate_label: {event.get('target', {}).get('predicate_label', '')}
current_assertion: {json.dumps(assertion, ensure_ascii=False)}

EVIDENCE:
{evidence}

Return JSON only:
{{"schema_version":"candidate-slots-v2","event_id":"{event['event_id']}","property_frames":[
  {{"window_id":"SOURCE_WINDOW_n","quote":"exact contiguous quote","surface_value":"minimal source phrase",
    "core_value":"concise property value","scope_complete_value":"complete value or null",
    "modality":"MUST|MUST_NOT|SHOULD|SHOULD_NOT|MAY|DESCRIPTIVE","negated":false,
    "numeric_value":null,"unit":null,"conditions":[],"exceptions":[],"enumeration_members":[]}}
]}}
"""


def build_entailment_ranking_prompt(event: dict[str, Any], frames: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> str:
    public_frames = [
        {key: frame.get(key) for key in ("window_id", "quote", "subject", "predicate", "value", "modality", "conditions", "exceptions")}
        for frame in frames
    ]
    public_candidates = [{key: row.get(key) for key in ("candidate_id", "value", "window_id", "quote", "kind")} for row in candidates]
    return f"""Rank a frozen finite candidate set by entailment from grounded semantic frames.

The safety gate has already authorized candidate comparison. Select exactly one candidate
only when the frames entail its complete literal, including negation, modality, conditions,
exceptions, numeric units, and scope. Do not rewrite candidate text and do not invent a new
candidate. Return ABSTAIN when no candidate or multiple candidates are equally supported.

event_id: {event['event_id']}
subject_label: {event.get('target', {}).get('subject_label', '')}
predicate_label: {event.get('target', {}).get('predicate_label', '')}
grounded_frames: {json.dumps(public_frames, ensure_ascii=False, indent=2)}
finite_candidates: {json.dumps(public_candidates, ensure_ascii=False, indent=2)}

Return JSON only, either:
{{"decision":"SELECT","candidate_id":"CAND_###","entailed":true,"reason":"brief evidence comparison"}}
or {{"decision":"ABSTAIN","candidate_id":null,"entailed":false,"reason":"brief reason"}}.
"""
