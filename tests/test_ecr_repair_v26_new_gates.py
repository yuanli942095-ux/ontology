import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ecr_repair_current_value_mapper import map_current_value
from ecr_repair_unresolved_dimension_gate import apply_unresolved_dimension_gate


def test_current_value_mapper_rejects_modal_mismatch() -> None:
    row = {"tuple_value_id": "HTTPS", "tuple_modality": "MUST", "verbatim_quote": "MUST always be used with HTTPS"}
    assert not map_current_value("may replace HTTPS", row).equivalent


def test_current_value_mapper_handles_composite_alias() -> None:
    row = {"tuple_value_id": "ENABLED_CANNOT_DISABLE", "tuple_modality": "MUST", "verbatim_quote": "enabled; you cannot disable it"}
    assert map_current_value("encryption is enabled and you can't disable it", row).equivalent


def test_current_value_mapper_normalizes_contracted_negation() -> None:
    row = {"tuple_value_id": "ENABLED_CANNOT_DISABLE", "tuple_modality": "MUST", "verbatim_quote": "enabled; you cannot disable it"}
    assert map_current_value("enabled and you can't disable it", row).equivalent


def test_unresolved_gate_keeps_unresolved_alternatives() -> None:
    rows = [(0.9, {"tuple_value_id": "VALUE_A", "tuple_conditions_json": "[]", "frame_id": "F1"}),
            (0.8, {"tuple_value_id": "VALUE_B", "tuple_conditions_json": "[]", "frame_id": "F2"})]
    result = apply_unresolved_dimension_gate(rows, "FAPI compliant client; profile not specified")
    assert result.decision == "ABSTAIN"


def test_unresolved_gate_resolves_matching_condition() -> None:
    rows = [(0.8, {"tuple_value_id": "LEGACY", "tuple_conditions_json": '["LEGACY_CLIENT"]', "frame_id": "F1"}),
            (0.7, {"tuple_value_id": "NEW", "tuple_conditions_json": '["NMDA_SUPPORT"]', "frame_id": "F2"})]
    result = apply_unresolved_dimension_gate(rows, "NMDA supporting server")
    assert result.decision == "CANDIDATE" and result.candidates[0]["tuple_value_id"] == "NEW"
