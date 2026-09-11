from __future__ import annotations

import json
from typing import Any


def build_semantic_frame_prompt(event: dict[str, Any], evidence: str, assertions: list[dict[str, str]], window_documents: dict[str, str]) -> str:
    """Build a candidate- and Oracle-blind semantic-frame extraction prompt."""
    return f"""Extract evidence-grounded normative semantic frames. Return JSON only.

This is extraction, not repair selection. You cannot see repair candidates, candidate IDs,
the benchmark partition, semantic type, or Oracle. Do not decide REPAIR/NO_CHANGE/ABSTAIN.
Extract only rules that determine the requested subject_label and predicate_label. Do not
extract neighboring requirements about other subjects or properties. For every relevant
rule, copy one contiguous quote verbatim from exactly one SOURCE_WINDOW.
Do not treat titles, bibliographic references, copyright notices, authors, or tables of
contents as normative assertions. Preserve different populations, conditions, exceptions,
jurisdictions, validity intervals, modalities, and values as separate frames. Never merge
text across windows. If evidence is irrelevant or insufficient, return an empty frames list.

schema_version: semantic-frame-v1
event_id: {event['event_id']}
as_of: {event.get('as_of', '')}
case_context: {event.get('case_context', '')}
subject_label: {event.get('target', {}).get('subject_label', '')}
predicate_label: {event.get('target', {}).get('predicate_label', '')}
allowed_document_ids: {json.dumps(event.get('document_ids', []), ensure_ascii=False)}
window_document_map: {json.dumps(window_documents, ensure_ascii=False, sort_keys=True)}

CURRENT ONTOLOGY ASSERTIONS (context only; do not copy their value as evidence):
{json.dumps(assertions, ensure_ascii=False, indent=2)}

EVIDENCE:
{evidence}

Return exactly:
{{
  "schema_version": "semantic-frame-v1",
  "event_id": "{event['event_id']}",
  "frames": [
    {{
      "document_id": "one allowed document ID",
      "window_id": "SOURCE_WINDOW_n",
      "quote": "one exact contiguous quotation",
      "subject": "rule subject or population",
      "predicate": "regulated property or action",
      "value": "compact ontology-ready natural-language value",
      "modality": "MUST|MUST_NOT|SHOULD|SHOULD_NOT|MAY|DESCRIPTIVE",
      "conditions": ["explicit applicability conditions"],
      "exceptions": ["explicit exceptions"],
      "valid_from": null,
      "valid_to": null,
      "jurisdiction": null,
      "assertion_kind": "NORMATIVE|INFORMATIVE|REFERENCE|METADATA|TABLE",
      "authority": 0,
      "confidence": 0.0
    }}
  ]
}}

The runtime overwrites document_id, valid_from, valid_to, and authority from the
frozen source manifest. Values you emit in those fields cannot affect policy resolution.
"""
