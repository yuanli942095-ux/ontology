from __future__ import annotations

"""Candidate-independent grounding and deterministic policy gate for V2.6 development."""

import copy
import calendar
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "semantic-frame-v1"
NON_ASSERTIONS = {"REFERENCE", "METADATA"}
ABSTAIN_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
ABSTAIN_CONFLICT = "CONFLICTING_EVIDENCE"


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def source_windows(evidence: str) -> dict[str, tuple[str, int]]:
    windows: dict[str, tuple[str, int]] = {}
    pattern = re.compile(r"(?s)\[SOURCE_WINDOW_(\d+)\]\s*(.*?)(?=\n\s*\[SOURCE_WINDOW_\d+\]|\Z)")
    for match in pattern.finditer(evidence):
        window_id = f"SOURCE_WINDOW_{match.group(1)}"
        body = match.group(2).strip()
        windows[window_id] = (body, evidence.find(body, match.start()))
    return windows


def ground_frame(frame: dict[str, Any], evidence: str) -> dict[str, Any]:
    """Bind one frame to one exact quote and return document-level character offsets."""
    result = {"status": "INVALID_FRAME", "frame": None}
    window_id, quote = frame.get("window_id"), frame.get("quote")
    windows = source_windows(evidence)
    if window_id not in windows or not isinstance(quote, str) or not quote.strip():
        return result
    body, body_offset = windows[window_id]
    occurrences = [match.start() for match in re.finditer(re.escape(quote), body)]
    if not occurrences:
        return {"status": "QUOTE_NOT_VERBATIM", "frame": None}
    if len(occurrences) != 1:
        return {"status": "QUOTE_AMBIGUOUS", "frame": None}
    grounded = copy.deepcopy(frame)
    grounded["char_start"] = body_offset + occurrences[0]
    grounded["char_end"] = grounded["char_start"] + len(quote)
    grounded["grounding"] = "EXACT_VERBATIM"
    return {"status": "GROUNDED", "frame": grounded}


def _instant(value: str | None, *, end: bool = False) -> datetime | None:
    if not value:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        value = value + "T00:00:00+00:00"
    elif re.fullmatch(r"\d{4}-\d{2}", value):
        year, month = (int(part) for part in value.split("-"))
        day = calendar.monthrange(year, month)[1] if end else 1
        value = f"{year:04d}-{month:02d}-{day:02d}T{'23:59:59' if end else '00:00:00'}+00:00"
    elif re.fullmatch(r"\d{4}", value):
        value = f"{value}-{'12-31T23:59:59' if end else '01-01T00:00:00'}+00:00"
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _applicable(frame: dict[str, Any], as_of: str) -> bool:
    point = _instant(as_of)
    start, finish = _instant(frame.get("valid_from")), _instant(frame.get("valid_to"), end=True)
    return bool(point and (start is None or start <= point) and (finish is None or point <= finish))


def _scope(frame: dict[str, Any]) -> tuple[Any, ...]:
    return (
        normalize(frame.get("subject", "")), normalize(frame.get("predicate", "")),
        normalize(frame.get("jurisdiction") or "GLOBAL"),
        tuple(sorted(normalize(item) for item in frame.get("conditions", []))),
        tuple(sorted(normalize(item) for item in frame.get("exceptions", []))),
    )


def _condition_score(frame: dict[str, Any], case_context: str) -> float:
    context = _tokens(case_context)
    terms = _tokens(" ".join(frame.get("conditions", []) + frame.get("exceptions", [])))
    return len(context & terms) / len(terms) if terms else 0.0


def _latest_in_scope(group: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dated = [(_instant(frame.get("valid_from")), frame) for frame in group]
    known = [point for point, _ in dated if point is not None]
    if not known:
        return group
    latest = max(known)
    return [frame for point, frame in dated if point == latest]


def decide_policy_v3_experimental(frames: list[dict[str, Any]], as_of: str, current_literal: str, case_context: str = "") -> dict[str, Any]:
    """Resolve applicability and conflicts without observing repair candidates."""
    assertions = [f for f in frames if f.get("assertion_kind") not in NON_ASSERTIONS and _applicable(f, as_of)]
    if not assertions:
        return {"decision": "ABSTAIN", "reason": ABSTAIN_INSUFFICIENT}
    if any(not normalize(f.get("subject", "")) or not normalize(f.get("predicate", "")) for f in assertions):
        return {"decision": "ABSTAIN", "reason": ABSTAIN_INSUFFICIENT}

    max_authority = max(int(f.get("authority", 0)) for f in assertions)
    winners = [f for f in assertions if int(f.get("authority", 0)) == max_authority]
    by_scope: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for frame in winners:
        by_scope.setdefault(_scope(frame), []).append(frame)
    by_scope = {scope: _latest_in_scope(group) for scope, group in by_scope.items()}
    conflicting = [group for group in by_scope.values() if len({normalize(f["value"]) for f in group}) > 1]
    if conflicting:
        return {"decision": "ABSTAIN", "reason": ABSTAIN_CONFLICT}

    winners = [frame for group in by_scope.values() for frame in group]
    if len(by_scope) > 1 and case_context:
        scored = [(_condition_score(frame, case_context), frame) for frame in winners]
        best = max(score for score, _ in scored)
        if best > 0:
            winners = [frame for score, frame in scored if score == best]
    unique = {(normalize(f["value"]), _scope(f)): f for f in winners}
    if len(unique) != 1:
        return {"decision": "ABSTAIN", "reason": ABSTAIN_INSUFFICIENT}
    winner = next(iter(unique.values()))
    if normalize(winner["value"]) == normalize(current_literal):
        return {"decision": "NO_CHANGE", "reason": "CURRENT_LITERAL_SUPPORTED", "supporting_frame": winner}
    return {"decision": "REPAIR_CANDIDATES_REQUIRED", "reason": "SUPPORTED_VALUE_DIFFERS", "supporting_frame": winner}


def decide_policy(frames: list[dict[str, Any]], as_of: str, current_literal: str, case_context: str = "") -> dict[str, Any]:
    """Conservative default gate; candidate-blind and fail-closed on unresolved scopes."""
    assertions = [f for f in frames if f.get("assertion_kind") not in NON_ASSERTIONS and _applicable(f, as_of)]
    if not assertions or any(not normalize(f.get("subject", "")) or not normalize(f.get("predicate", "")) for f in assertions):
        return {"decision": "ABSTAIN", "reason": ABSTAIN_INSUFFICIENT}
    max_authority = max(int(f.get("authority", 0)) for f in assertions)
    winners = [f for f in assertions if int(f.get("authority", 0)) == max_authority]
    by_scope: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for frame in winners:
        by_scope.setdefault(_scope(frame), []).append(frame)
    if any(len({normalize(f["value"]) for f in group}) > 1 for group in by_scope.values()):
        return {"decision": "ABSTAIN", "reason": ABSTAIN_CONFLICT}
    unique = {(normalize(f["value"]), _scope(f)): f for f in winners}
    if len(unique) != 1:
        return {"decision": "ABSTAIN", "reason": ABSTAIN_INSUFFICIENT}
    winner = next(iter(unique.values()))
    if normalize(winner["value"]) == normalize(current_literal):
        return {"decision": "NO_CHANGE", "reason": "CURRENT_LITERAL_SUPPORTED", "supporting_frame": winner}
    return {"decision": "REPAIR_CANDIDATES_REQUIRED", "reason": "SUPPORTED_VALUE_DIFFERS", "supporting_frame": winner}


def select_candidate(policy: dict[str, Any], candidates: list[dict[str, Any]], target: dict[str, Any]) -> dict[str, Any]:
    """Reveal and select a finite candidate only after the policy gate requests repair."""
    if policy.get("decision") != "REPAIR_CANDIDATES_REQUIRED":
        return {"decision": policy.get("decision", "ABSTAIN"), "reason": policy.get("reason", "INVALID_POLICY_STATE")}
    wanted = normalize(policy["supporting_frame"]["value"])
    matches = [row for row in candidates if normalize(str(row.get("display_value", ""))) == wanted]
    if len(matches) != 1:
        return {"decision": "ABSTAIN", "reason": "NO_UNIQUE_SUPPORTED_CANDIDATE", "candidate_match_count": len(matches)}
    operation = json.loads(matches[0]["operation_json"]) if isinstance(matches[0].get("operation_json"), str) else matches[0]["operation_json"]
    return {
        "schema_version": "predicted-repair-ir-v1", "decision": "REPAIR", "operation": "UPDATE_LITERAL",
        "target": {"subject_iri": operation["subject_iri"], "predicate_iri": operation["predicate_iri"], "old_value": operation["old_value"]},
        "replacement": {"new_value": operation["new_value"]},
        "evidence_spans": [policy["supporting_frame"]["quote"]], "confidence": policy["supporting_frame"]["confidence"],
        "candidate_id": matches[0]["candidate_id"], "candidate_revealed_after_gate": True,
    }


def construct_finite_candidates(
    policy: dict[str, Any], grounded_frames: list[dict[str, Any]], target: dict[str, Any]
) -> list[dict[str, Any]]:
    """Construct a bounded, Oracle-free value set after the safety gate permits repair."""
    if policy.get("decision") != "REPAIR_CANDIDATES_REQUIRED":
        return []
    values: dict[str, str] = {}
    for frame in grounded_frames:
        value = str(frame.get("value", "")).strip()
        if value:
            values.setdefault(normalize(value), value)
    old_value = target["old_value"]
    old_lexical = str(old_value["lexical"])
    values.setdefault(normalize(old_lexical), old_lexical)
    candidates = []
    for index, value in enumerate(values.values(), 1):
        operation = {
            "operator": "REPLACE_PROPERTY_VALUE", "subject_iri": target["subject_iri"],
            "predicate_iri": target["predicate_iri"], "old_value": old_value,
            "new_value": {"kind": "literal", "lexical": value, "datatype": old_value["datatype"]},
        }
        candidates.append({"candidate_id": f"FRAME_CAND_{index:03d}", "display_value": value, "operation_json": operation})
    return candidates


def load_schema(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_frame_bundle(bundle: dict[str, Any], evidence: str) -> dict[str, Any]:
    """Perform the method-critical subset of schema and exact-grounding validation."""
    if bundle.get("schema_version") != SCHEMA_VERSION or not isinstance(bundle.get("frames"), list):
        return {"status": "INVALID_BUNDLE", "frames": [], "errors": ["schema_version or frames invalid"]}
    grounded, errors = [], []
    required = {"document_id", "window_id", "quote", "subject", "predicate", "value", "modality", "assertion_kind", "authority", "confidence"}
    for index, frame in enumerate(bundle["frames"]):
        missing = sorted(required - set(frame)) if isinstance(frame, dict) else sorted(required)
        if missing:
            errors.append(f"frame[{index}] missing: {','.join(missing)}")
            continue
        result = ground_frame(frame, evidence)
        if result["status"] != "GROUNDED":
            errors.append(f"frame[{index}] grounding: {result['status']}")
            continue
        grounded.append(result["frame"])
    status = "VALID" if not errors else ("VALID_WITH_REJECTED_FRAMES" if grounded else "INVALID_FRAME_SET")
    return {"status": status, "frames": grounded, "errors": errors}


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", normalize(value)) if len(token) > 2}


def align_target_frames(frames: list[dict[str, Any]], subject_label: str, predicate_label: str) -> list[dict[str, Any]]:
    """Keep the closest public-target frames without consulting candidates or Gold."""
    target_subject, target_predicate = _tokens(subject_label), _tokens(predicate_label)
    scored = []
    for frame in frames:
        subject = _tokens(frame.get("subject", "")); predicate = _tokens(frame.get("predicate", ""))
        subject_score = len(subject & target_subject) / max(1, len(target_subject))
        predicate_score = len(predicate & target_predicate) / max(1, len(target_predicate))
        scored.append((0.45 * subject_score + 0.55 * predicate_score, frame))
    if not scored or max(score for score, _ in scored) <= 0:
        return []
    best = max(score for score, _ in scored)
    return [frame for score, frame in scored if score >= max(0.35, best - 0.10)]
