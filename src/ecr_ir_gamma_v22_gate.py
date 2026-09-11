from __future__ import annotations

from typing import Any


def repair_is_no_change_equivalent(predicted: dict[str, Any]) -> bool:
    if predicted.get("decision") != "REPAIR":
        return False
    old_value = predicted.get("target", {}).get("old_value", {})
    new_value = predicted.get("replacement", {}).get("new_value", {})
    if not isinstance(old_value, dict) or not isinstance(new_value, dict):
        return False
    return (
        old_value.get("kind") == new_value.get("kind")
        and old_value.get("datatype") == new_value.get("datatype")
        and old_value.get("lexical") == new_value.get("lexical")
    )
