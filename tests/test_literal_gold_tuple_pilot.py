import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from run_literal_gold_tuple_pilot import negative_control, tuple_equivalent


def test_surface_variation_is_accepted() -> None:
    accepted, _ = tuple_equivalent("residentKey DOMString", "residentKey, of type DOMString")
    assert accepted


def test_polarity_change_is_rejected() -> None:
    accepted, _ = tuple_equivalent("NOT optionally-blockable", "optionally-blockable")
    assert not accepted


def test_number_change_is_rejected() -> None:
    gold = "15 percent relative reduction by 2030"
    accepted, _ = tuple_equivalent(negative_control(gold), gold)
    assert not accepted


def test_number_inside_identifier_is_rejected() -> None:
    accepted, _ = tuple_equivalent("AES-257", "AES-256")
    assert not accepted
