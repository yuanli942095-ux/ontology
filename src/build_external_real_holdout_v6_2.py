from __future__ import annotations

"""Build v6.2 claim-level Direct IR hold-out from frozen v6.1.

Same evidence, Gold decisions, windows, and replacement values. Public Repair
targets are rewritten so a reader can uniquely identify the claim without
seeing candidates, Gold windows, or the new value.
"""

import argparse
import csv
import json
import re
import shutil
from pathlib import Path
from typing import Any

from holdout_v6_2_claim_contract import (
    CURRENT_SURFACE_UNMODELED,
    LEAKAGE_FIELDS,
    build_claim_fields,
    leakage_hits,
    rfc_title_from_source_text,
    strengthen_until_unique,
    unique_property_window,
)
from rfc213_direct_repair_ir import source_windows
from semantic_v2_common import PROJECT_DIR

SOURCE_NAME = "external-real-holdout-v6-1-direct-ir-blind"
TARGET_NAME = "external-real-holdout-v6-2-direct-ir-blind"
SOURCE = PROJECT_DIR / "benchmark" / SOURCE_NAME
TARGET = PROJECT_DIR / "benchmark" / TARGET_NAME
OUTPUT = PROJECT_DIR / "output" / TARGET_NAME
NEUTRAL_TYPE = "NORMATIVE_EVIDENCE_ASSESSMENT"
READABLE_UNMODELED = CURRENT_SURFACE_UNMODELED
LITERAL_RE = re.compile(
    r'(<[^>]+_claim[^>]*>)([^<]*claim=unmodeled|[^<]*unmodeled)(</[^>]+>)'
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_rfc_titles() -> dict[int, str]:
    cache = SOURCE / "public/retrieval/source-cache"
    titles: dict[int, str] = {}
    for path in cache.glob("V6_IETF_RFC*.txt"):
        match = re.search(r"RFC(\d+)", path.name)
        if not match:
            continue
        title = rfc_title_from_source_text(path.read_text(encoding="utf-8", errors="replace"))
        if title:
            titles[int(match.group(1))] = title
    return titles


def event_rfc(event: dict[str, Any], seed: dict[str, Any]) -> int | None:
    if seed.get("rfc"):
        return int(seed["rfc"])
    for source_id in event.get("source_ids") or []:
        match = re.search(r"RFC(\d+)$", source_id)
        if match:
            return int(match.group(1))
    return None


def replace_unmodeled_literal(owl_text: str) -> str:
    updated, count = LITERAL_RE.subn(
        lambda match: match.group(1) + READABLE_UNMODELED + match.group(3),
        owl_text,
    )
    if count:
        return updated
    return owl_text.replace("claim=unmodeled", "claim=no_current_assertion_recorded").replace(
        ">unmodeled<", f">{READABLE_UNMODELED}<"
    )


def public_blob(event: dict[str, Any]) -> str:
    return "\n".join(str(event.get(field, "")) for field in LEAKAGE_FIELDS)


CONTRACT_MD = """# v6.2 / v7 claim-level target contract

Public Direct IR input must be **target-specific, value-blind, candidate-blind**.

## Required public Repair fields

| Field | Pins | Must not contain |
|---|---|---|
| `target_claim_id` | stable claim identifier | Gold window, candidate id, new value |
| `target_entity_label` | the repairable entity / protocol feature | author names, domain-level generics |
| `target_property_label` | the claim-specific property | `evidence-grounded value`, RFC2119 filler |
| `target_property_iri` | ontology predicate to copy | replacement lexical |
| `current_value_surface` | human-readable current assertion | the new value |
| `current_value_semantics` | how to interpret current vs evidence | Gold decision |
| `value_neutral_question` | the assessment question | MUST/SHOULD answer, window index |
| `target_cq` | competency question for the same claim | replacement value |

`subject_label` and `predicate_label` must equal `target_entity_label` and
`target_property_label`. They are no longer domain-level generics.

## Rebuild rules for Repair

1. Public discriminators uniquely rank the Gold evidence window.
2. Current assertion is human-readable (`No current assertion recorded.` when previously `unmodeled`).
3. A reader can know which normative statement to judge without candidates or oracle.
4. Replacement value remains evidence-only.

## Freeze gate

Automatic answerability audit plus a 70-event manual review. Repair must be
mostly `YES`. Otherwise do not freeze. v6.1 remains diagnostic only.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if TARGET.exists():
        if not args.force:
            raise SystemExit(f"refusing to overwrite existing revision: {TARGET}")
        shutil.rmtree(TARGET)
    shutil.copytree(SOURCE, TARGET)
    OUTPUT.mkdir(parents=True, exist_ok=True)

    events = read_jsonl(TARGET / "public/events/events.jsonl")
    golds = {row["event_id"]: row for row in read_jsonl(TARGET / "private/oracle/gold-repair-ir.jsonl")}
    seeds = {row["event_id"]: row for row in json.loads((SOURCE / "private/construction/source-registry-seed-full.json").read_text(encoding="utf-8"))}
    rfc_titles = load_rfc_titles()
    public_fields = [
        "event_id", "semantic_type", "domain", "subject_label", "predicate_label",
        "case_context", "source_ids", "source_owl", "status", "target_claim_id",
        "target_entity_label", "target_property_label", "target_property_iri",
        "current_value_surface", "current_value_semantics", "value_neutral_question",
        "target_cq",
    ]
    build_rows = []
    unique_repair = 0
    leak_repair = 0
    repair_n = 0

    for event in events:
        event_id = event["event_id"]
        gold = golds[event_id]
        evidence = (TARGET / "public/excerpts" / f"{event_id}-evidence.md").read_text(encoding="utf-8")
        windows = source_windows(evidence)
        window_map = dict(windows)
        gold_window = str(gold.get("gold_source_window", "")).removeprefix("SOURCE_WINDOW_")
        gold_text = window_map.get(gold_window, "")
        others = [text for wid, text in windows if wid != gold_window]
        rfc_no = event_rfc(event, seeds.get(event_id, {}))
        rfc_title = rfc_titles.get(rfc_no or -1, "")
        predicate_iri = gold["target"]["predicate_iri"]
        old_lexical = gold["target"]["old_value"]["lexical"]
        new_lexical = gold.get("replacement", {}).get("new_value", {}).get("lexical", "") if gold["decision"] == "REPAIR" else ""
        fields = build_claim_fields(
            event_id=event_id,
            domain=event["domain"],
            subject_label=event["subject_label"],
            predicate_iri=predicate_iri,
            rfc_title=rfc_title,
            gold_window_text=gold_text,
            other_window_texts=others,
            current_lexical=old_lexical,
            decision=gold["decision"],
            new_lexical=new_lexical,
        )
        if gold["decision"] == "REPAIR" and gold_window.isdigit():
            fields = strengthen_until_unique(fields, windows, gold_window, gold_text, others, new_lexical=new_lexical)
        event.update(fields)
        event["subject_label"] = fields["target_entity_label"]
        event["predicate_label"] = fields["target_property_label"]
        event["case_context"] = fields["value_neutral_question"]
        event["semantic_type"] = NEUTRAL_TYPE
        event["status"] = "READY"

        pin = " ".join(fields[k] for k in ("target_entity_label", "target_property_label", "value_neutral_question", "target_cq"))
        leaks = leakage_hits(public_blob(event), new_lexical, gold_text, pin_text=pin)
        unique = (
            unique_property_window(fields["target_property_label"], windows) == gold_window
            if gold["decision"] == "REPAIR"
            else True
        )
        if gold["decision"] == "REPAIR":
            repair_n += 1
            unique_repair += int(unique)
            leak_repair += int(bool(leaks))

        readable_old = fields["current_value_surface"] if "unmodeled" in old_lexical else old_lexical
        if "unmodeled" in old_lexical:
            for rel in (f"public/ontology-current/{event_id}.owl", f"repair-stage/mutants/{event_id}.owl"):
                owl_path = TARGET / rel
                owl_path.write_text(replace_unmodeled_literal(owl_path.read_text(encoding="utf-8")), encoding="utf-8")
            gold["target"]["old_value"]["lexical"] = READABLE_UNMODELED
            readable_old = READABLE_UNMODELED
            event["current_value_surface"] = READABLE_UNMODELED

        build_rows.append({
            "event_id": event_id,
            "gold_decision": gold["decision"],
            "gold_window": gold.get("gold_source_window", ""),
            "unique_gold_window": unique,
            "leakage": json.dumps(leaks),
            "target_entity_label": fields["target_entity_label"],
            "target_property_label": fields["target_property_label"],
            "current_value_surface": event["current_value_surface"],
            "readable_old": readable_old,
        })

    write_jsonl(TARGET / "public/events/events.jsonl", events)
    write_jsonl(TARGET / "private/oracle/gold-repair-ir.jsonl", [golds[e["event_id"]] for e in events])
    write_csv(TARGET / "public/events/external-real-event-template.csv", events, public_fields)
    write_csv(TARGET / "private/construction/v6-2-claim-rebuild-audit.csv", build_rows, list(build_rows[0]))

    schema = json.loads((SOURCE / "public/events/event.schema.json").read_text(encoding="utf-8"))
    schema["$id"] = schema["$id"].replace(SOURCE_NAME, TARGET_NAME)
    schema["title"] = "Independent hold-out v6.2 public event"
    extra = {
        "target_claim_id": {"type": "string", "minLength": 1},
        "target_entity_label": {"type": "string", "minLength": 1},
        "target_property_label": {"type": "string", "minLength": 1},
        "target_property_iri": {"type": "string", "minLength": 1},
        "current_value_surface": {"type": "string", "minLength": 1},
        "current_value_semantics": {"type": "string", "minLength": 1},
        "value_neutral_question": {"type": "string", "minLength": 1},
        "target_cq": {"type": "string", "minLength": 1},
    }
    schema["required"] = list(public_fields)
    schema["properties"].update(extra)
    write_json(TARGET / "public/events/event.schema.json", schema)
    (TARGET / "private/construction/claim-level-target-contract.md").write_text(CONTRACT_MD, encoding="utf-8")
    (TARGET / "README-v6-2.md").write_text(
        "# External Real Hold-out v6.2 Direct-IR Blind\n\n"
        "Status: `DRAFT_NOT_FROZEN`\n\n"
        "Claim-level public target contract over v6.1 evidence and Gold. "
        "v6.1 remains diagnostic and is not a strict blind main result. "
        "Do not freeze until automatic + 70-event manual answerability audits "
        "show Repair mostly YES.\n",
        encoding="utf-8",
    )
    protocol = {
        "schema_version": "external-real-holdout-v6-2-claim-contract-v1",
        "status": "DRAFT_NOT_FROZEN",
        "parent_benchmark": SOURCE_NAME,
        "public_semantic_type": NEUTRAL_TYPE,
        "repair_events": repair_n,
        "repair_unique_gold_window": unique_repair,
        "repair_leakage_flags": leak_repair,
        "freeze_gate": "Repair manual YES majority plus automatic unique-window/no-leakage audit",
    }
    write_json(TARGET / "private/construction/v6-2-claim-protocol.json", protocol)
    write_json(OUTPUT / "v6-2-build-summary.json", protocol)
    print(json.dumps(protocol, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
