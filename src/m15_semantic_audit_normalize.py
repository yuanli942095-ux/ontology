from __future__ import annotations

"""Normalize gold temporal anchors and M13/M15 IR into comparable anchor signals."""

import json
import re
from typing import Any

ROLE_FIELDS = ("current", "effective", "superseded", "reference", "publication")
RELATION_FIELDS = ("supersedes", "updates", "updatedBy")

MONTHS = {
    "january": "01",
    "february": "02",
    "march": "03",
    "april": "04",
    "may": "05",
    "june": "06",
    "july": "07",
    "august": "08",
    "september": "09",
    "october": "10",
    "november": "11",
    "december": "12",
}

EMPTY_TOKENS = {"", "none", "n/a", "na", "null", "-"}


def parse_ir_payload(ir_json: str) -> dict[str, Any]:
    try:
        payload = json.loads(ir_json)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def is_empty(value: str) -> bool:
    return str(value or "").strip().lower() in EMPTY_TOKENS


def extract_rfcs(text: str) -> set[str]:
    found = re.findall(r"RFC\s*0*(\d+)", str(text or ""), flags=re.I)
    found.extend(re.findall(r"\brfc/(\d+)\b", str(text or ""), flags=re.I))
  # RFC header lines often list bare numbers after Obsoletes:
    for chunk in re.split(r"[,;|]", str(text or "")):
        token = chunk.strip()
        if re.fullmatch(r"\d{3,5}", token):
            found.append(token.lstrip("0") or token)
    return {str(int(num)) if num.isdigit() else num for num in found if num}


def extract_date_keys(text: str) -> set[str]:
    keys: set[str] = set()
    raw = str(text or "")
    for match in re.finditer(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})\b",
        raw,
        flags=re.I,
    ):
        month = MONTHS[match.group(1).lower()]
        year = match.group(2)
        keys.add(year)
        keys.add(f"{year}-{month}")
    for match in re.finditer(r"\b(19|20)\d{2}\b", raw):
        keys.add(match.group(0))
    return keys


def split_multi(value: str) -> list[str]:
    if is_empty(value):
        return []
    return [part.strip() for part in re.split(r"[;|]", str(value)) if part.strip()]


def primary_rfc_from_gold(gold: dict[str, str]) -> str:
    for field in ("role_current", "issuer", "source_url", "entities"):
        rfcs = sorted(extract_rfcs(gold.get(field, "")), key=int)
        if rfcs:
            return rfcs[-1]
    return ""


def gold_frame(gold: dict[str, str]) -> dict[str, Any]:
    primary = primary_rfc_from_gold(gold)
    superseded = set()
    for field in ("role_superseded", "relation_supersedes"):
        for part in split_multi(gold.get(field, "")):
            superseded.update(extract_rfcs(part))
    updates = set()
    for part in split_multi(gold.get("relation_updates", "")):
        updates.update(extract_rfcs(part))
    references = set()
    for part in split_multi(gold.get("role_reference", "")):
        references.update(extract_rfcs(part))
    return {
        "resolution": str(gold.get("resolution", "")).strip().lower(),
        "primary_rfc": primary,
        "current_rfcs": extract_rfcs(gold.get("role_current", "")) or ({primary} if primary else set()),
        "effective_dates": extract_date_keys(gold.get("role_effective", ""))
        | extract_date_keys(gold.get("role_publication", "")),
        "publication_dates": extract_date_keys(gold.get("role_publication", "")),
        "superseded_rfcs": superseded,
        "updates_rfcs": updates,
        "reference_rfcs": references,
        "has_supersedes": not is_empty(gold.get("relation_supersedes", "")),
        "has_updates": not is_empty(gold.get("relation_updates", "")),
        "role_active": {
            "current": not is_empty(gold.get("role_current", "")),
            "effective": not is_empty(gold.get("role_effective", "")),
            "superseded": not is_empty(gold.get("role_superseded", "")),
            "reference": not is_empty(gold.get("role_reference", "")),
            "publication": not is_empty(gold.get("role_publication", "")),
        },
    }


def ir_numbers(details_row: dict[str, str], payload: dict[str, Any]) -> set[str]:
    numbers: set[str] = set()
    for token in str(details_row.get("normalized_numbers", "")).split("|"):
        token = token.strip()
        if re.fullmatch(r"\d{3,5}", token):
            numbers.add(str(int(token)))
    numbers.update(extract_rfcs(str(payload.get("result", ""))))
    numbers.update(extract_rfcs(str(payload.get("statement", ""))))
    return numbers


def ir_dates(details_row: dict[str, str], payload: dict[str, Any]) -> set[str]:
    dates = set()
    for token in str(details_row.get("normalized_dates", "")).split("|"):
        token = token.strip()
        if token:
            dates.add(token)
            if re.fullmatch(r"\d{6}", token):
                dates.add(token[:4])
                dates.add(f"{token[:4]}-{token[4:6]}")
    dates.update(extract_date_keys(str(payload.get("result", ""))))
    return dates


def ir_frame(details_row: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    relation = str(payload.get("relation", "")).strip().upper()
    result = str(payload.get("result", "")).strip()
    ir_status = str(details_row.get("ir_status", "")).strip().upper()
    generation_status = str(details_row.get("generation_status", "")).strip().upper()
    numbers = ir_numbers(details_row, payload)
    dates = ir_dates(details_row, payload)
    unresolved = (
        relation in {"", "UNKNOWN"}
        or not result
        or ir_status not in {"", "OK"}
        or generation_status in {"INVALID_SCHEMA", "ABSTAIN"}
    )
    return {
        "relation": relation,
        "result": result,
        "numbers": numbers,
        "dates": dates,
        "unresolved": unresolved,
        "predicts_effective": relation in {"EFFECTIVE_FROM", "CURRENT", "CURRENT_NORMATIVE_STATUS"},
        "predicts_supersedes": relation in {"REPLACES", "SUPERSEDES"},
        "predicts_updates": relation == "UPDATES",
        "predicts_reference": "REFERENCE" in relation,
    }


def overlap(left: set[str], right: set[str]) -> bool:
    return bool(left & right) if left and right else False


def date_overlap(gold_dates: set[str], ir_dates_set: set[str]) -> bool:
    if not gold_dates:
        return True
    if not ir_dates_set:
        return False
    for gold_date in gold_dates:
        for ir_date in ir_dates_set:
            if gold_date == ir_date or gold_date in ir_date or ir_date in gold_date:
                return True
    return False


def score_roles(gold: dict[str, Any], ir: dict[str, Any]) -> dict[str, bool]:
    primary = gold["primary_rfc"]
    numbers = ir["numbers"]
    current_match = bool(primary and primary in numbers)
    effective_match = ir["predicts_effective"] and (
        date_overlap(gold["effective_dates"], ir["dates"]) or (primary and primary in numbers)
    )
    superseded_match = True
    if gold["role_active"]["superseded"]:
        superseded_match = ir["predicts_supersedes"] and (
            overlap(gold["superseded_rfcs"], numbers) or ir["predicts_supersedes"]
        )
    reference_match = True
    if gold["role_active"]["reference"]:
        reference_match = overlap(gold["reference_rfcs"], numbers) or ir["predicts_reference"]
    publication_match = True
    if gold["role_active"]["publication"]:
        publication_match = date_overlap(gold["publication_dates"], ir["dates"]) or (
            primary and primary in numbers
        )
    return {
        "current": current_match if gold["role_active"]["current"] else True,
        "effective": effective_match if gold["role_active"]["effective"] else True,
        "superseded": superseded_match if gold["role_active"]["superseded"] else True,
        "reference": reference_match if gold["role_active"]["reference"] else True,
        "publication": publication_match if gold["role_active"]["publication"] else True,
    }


def score_relations(gold: dict[str, Any], ir: dict[str, Any]) -> dict[str, bool]:
    supersedes_expected = gold["has_supersedes"]
    updates_expected = gold["has_updates"]
    relation_type_match = True
    if supersedes_expected and not updates_expected:
        relation_type_match = ir["predicts_supersedes"]
    elif updates_expected and not supersedes_expected:
        relation_type_match = ir["predicts_updates"]
    elif supersedes_expected and updates_expected:
        relation_type_match = ir["predicts_supersedes"] or ir["predicts_updates"]
    else:
        relation_type_match = ir["predicts_effective"] or not ir["unresolved"]

    direction_match = True
    if supersedes_expected and gold["primary_rfc"]:
        direction_match = ir["predicts_supersedes"] and (
            overlap(gold["superseded_rfcs"], ir["numbers"]) or gold["primary_rfc"] in ir["numbers"]
        )
    if updates_expected and gold["primary_rfc"]:
        direction_match = direction_match and (
            ir["predicts_updates"] or ir["predicts_supersedes"] or gold["primary_rfc"] in ir["numbers"]
        )
    return {
        "relation_type": relation_type_match,
        "relation_direction": direction_match,
        "supersedes": (not supersedes_expected)
        or (ir["predicts_supersedes"] and overlap(gold["superseded_rfcs"], ir["numbers"])),
        "updates": (not updates_expected)
        or (ir["predicts_updates"] or overlap(gold["updates_rfcs"], ir["numbers"])),
        "updatedBy": True,
    }


def score_attempt(gold_row: dict[str, str], details_row: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    gold = gold_frame(gold_row)
    ir = ir_frame(details_row, payload)
    roles = score_roles(gold, ir)
    relations = score_relations(gold, ir)
    gold_unresolved = gold["resolution"] == "unresolved"
    pred_unresolved = ir["unresolved"]
    unresolved_match = gold_unresolved == pred_unresolved
    role_exact = all(roles[name] for name in ROLE_FIELDS if gold["role_active"][name])
    complete_frame = role_exact and relations["relation_type"] and relations["relation_direction"]
    return {
        "roles": roles,
        "relations": relations,
        "role_exact_frame": role_exact,
        "complete_temporal_frame": complete_frame,
        "current_anchor": roles["current"] if gold["role_active"]["current"] else None,
        "effective_anchor": roles["effective"] if gold["role_active"]["effective"] else None,
        "gold_unresolved": gold_unresolved,
        "pred_unresolved": pred_unresolved,
        "unresolved_match": unresolved_match,
        "gold_primary_rfc": gold["primary_rfc"],
        "ir_relation": ir["relation"],
    }


def micro_macro_f1(matches: list[bool]) -> tuple[float, float]:
    if not matches:
        return 0.0, 0.0
    micro = sum(matches) / len(matches)
    return micro, micro


def role_micro_macro_f1(
    attempts: list[dict[str, Any]],
    stage: str,
) -> tuple[dict[str, float], dict[str, float]]:
    micro_scores: list[bool] = []
    macro_scores: list[float] = []
    for role in ROLE_FIELDS:
        role_matches = [
            bool(row[f"{stage}_role_{role}"])
            for row in attempts
            if row.get(f"gold_role_active_{role}")
        ]
        if role_matches:
            macro_scores.append(sum(role_matches) / len(role_matches))
            micro_scores.extend(role_matches)
    micro = sum(micro_scores) / len(micro_scores) if micro_scores else 0.0
    macro = sum(macro_scores) / len(macro_scores) if macro_scores else 0.0
    return {"micro": micro, "macro": macro}


def rate(rows: list[dict[str, Any]], key: str) -> float:
    values = [row[key] for row in rows if row.get(key) is not None]
    if not values:
        return 0.0
    return sum(bool(value) for value in values) / len(values)


def unresolved_pr_recall(attempts: list[dict[str, Any]], stage: str) -> dict[str, float]:
    gold_pos = [row for row in attempts if row["gold_unresolved"]]
    pred_pos = [row for row in attempts if row[f"{stage}_pred_unresolved"]]
    tp = sum(
        1
        for row in attempts
        if row["gold_unresolved"] and row[f"{stage}_pred_unresolved"]
    )
    precision = tp / len(pred_pos) if pred_pos else 0.0
    recall = tp / len(gold_pos) if gold_pos else 0.0
    return {"precision": precision, "recall": recall, "gold_positive": len(gold_pos), "pred_positive": len(pred_pos)}
