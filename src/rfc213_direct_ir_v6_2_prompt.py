from __future__ import annotations

"""Candidate-blind Direct IR prompt that exposes v6.2 claim-level public fields."""

from rfc213_direct_repair_ir import build_direct_ir_prompt


CLAIM_FIELDS = (
    "target_claim_id",
    "target_entity_label",
    "target_property_label",
    "target_property_iri",
    "current_value_surface",
    "current_value_semantics",
    "value_neutral_question",
    "target_cq",
)


def build_direct_ir_prompt_v62(event: dict, evidence: str, assertions: list[dict[str, str]]) -> str:
    """v6.1 prompt plus claim-level public fields. Does not change the frozen v6.1 method file."""
    base = build_direct_ir_prompt(event, evidence, assertions)
    extra = "\n".join(f"{field}: {event.get(field, '')}" for field in CLAIM_FIELDS)
    insert = (
        "CLAIM-LEVEL PUBLIC TARGET (value-blind; do not treat this as the replacement value):\n"
        f"{extra}\n\n"
    )
    marker = "CURRENT ONTOLOGY LITERAL ASSERTIONS:"
    if marker not in base:
        return base + "\n" + insert
    return base.replace(marker, insert + marker)
