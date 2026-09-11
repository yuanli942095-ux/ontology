from __future__ import annotations

"""Build the pre-model shortcut-fixed v6.1 revision from frozen v6.

The transformation is deterministic and does not inspect model outputs. It
neutralizes model-visible decision-bearing labels, permutes evidence windows,
and moves current ontology inputs into the public inference zone.
"""

import csv
import hashlib
import json
import random
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semantic_v2_common import PROJECT_DIR


SOURCE_NAME = "external-real-holdout-v6-direct-ir-blind"
TARGET_NAME = "external-real-holdout-v6-1-direct-ir-blind"
SOURCE = PROJECT_DIR / "benchmark" / SOURCE_NAME
TARGET = PROJECT_DIR / "benchmark" / TARGET_NAME
OUTPUT = PROJECT_DIR / "output" / TARGET_NAME
METHOD_SHA = "ed250c97d056306ccc43b661ad78face6b607aceff1076cada98afb3ef612b54"
PARENT_BENCHMARK_SHA = "8acd967b6a4b6b5f4689e649bf41d9ec0f10e9379a4efb3c39a40df643e3cfbb"
PERMUTATION_SEED = 20260909
NEUTRAL_TYPE = "NORMATIVE_EVIDENCE_ASSESSMENT"
WINDOW_RE = re.compile(r"\[SOURCE_WINDOW_(\d)\]\n(.*?)(?=\n\[SOURCE_WINDOW_|\Z)", re.S)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace_window_refs(text: str, mapping: dict[int, int]) -> str:
    if not text:
        return text
    for old, new in mapping.items():
        text = text.replace(f"SOURCE_WINDOW_{old}", f"__SW_{new}__")
        text = re.sub(rf"\bWindow {old}\b", f"__WIN_{new}__", text)
    for new in range(1, 6):
        text = text.replace(f"__SW_{new}__", f"SOURCE_WINDOW_{new}")
        text = text.replace(f"__WIN_{new}__", f"Window {new}")
    return text


def transform_csv(path: Path, mappings: dict[str, dict[int, int]]) -> None:
    rows = read_csv(path)
    if not rows or "event_id" not in rows[0]:
        return
    changed = False
    for row in rows:
        mapping = mappings.get(row.get("event_id", ""))
        if not mapping:
            continue
        for key, value in list(row.items()):
            new = replace_window_refs(value or "", mapping)
            if new != value:
                row[key] = new; changed = True
    if changed:
        write_csv(path, rows)


def main() -> int:
    if TARGET.exists():
        raise SystemExit(f"refusing to overwrite existing revision: {TARGET}")
    shutil.copytree(SOURCE, TARGET)
    OUTPUT.mkdir(parents=True, exist_ok=True)

    event_path = TARGET / "public/events/events.jsonl"
    events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rng = random.Random(PERMUTATION_SEED)
    source_gold = [json.loads(line) for line in (SOURCE / "private/oracle/gold-repair-ir.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    decision_groups = {
        "REPAIR": sorted(row["event_id"] for row in source_gold if row["decision"] == "REPAIR"),
        "NO_CHANGE": sorted(row["event_id"] for row in source_gold if row["decision"] == "NO_CHANGE"),
        "ABSTAIN": sorted(row["event_id"] for row in source_gold if row["decision"] == "ABSTAIN"),
    }
    target_positions: dict[str, int] = {}
    for decision, event_ids in decision_groups.items():
        per_position = len(event_ids) // 5
        if per_position * 5 != len(event_ids):
            raise RuntimeError(f"decision group is not position-balanceable: {decision}")
        positions = [position for position in range(1, 6) for _ in range(per_position)]
        rng.shuffle(positions)
        target_positions.update(dict(zip(event_ids, positions)))
    ids = sorted(target_positions)
    mappings: dict[str, dict[int, int]] = {}
    audit_rows = []
    public_ontology = TARGET / "public/ontology-current"
    public_ontology.mkdir(parents=True, exist_ok=True)

    for event_id in ids:
        gold_position = target_positions[event_id]
        remaining_positions = [p for p in range(1, 6) if p != gold_position]
        rng.shuffle(remaining_positions)
        order = [gold_position, *remaining_positions]
        mapping = {old: new for old, new in zip(range(1, 6), order)}
        mappings[event_id] = mapping
        excerpt = TARGET / "public/excerpts" / f"{event_id}-evidence.md"
        original = excerpt.read_text(encoding="utf-8")
        windows = {int(number): text.strip() for number, text in WINDOW_RE.findall(original)}
        if set(windows) != set(range(1, 6)):
            raise RuntimeError(f"invalid five-window evidence: {event_id}")
        inverse = {new: old for old, new in mapping.items()}
        rendered = "\n\n".join(f"[SOURCE_WINDOW_{new}]\n{windows[inverse[new]]}" for new in range(1, 6)) + "\n"
        excerpt.write_text(rendered, encoding="utf-8")
        audit_rows.append({"event_id": event_id, "old_gold_position": 1, "new_gold_position": gold_position,
                           "old_to_new_mapping": json.dumps(mapping, sort_keys=True), "permutation_seed": PERMUTATION_SEED})

        source_owl = TARGET / "repair-stage/mutants" / f"{event_id}.owl"
        shutil.copy2(source_owl, public_ontology / f"{event_id}.owl")

    for event in events:
        event["semantic_type"] = NEUTRAL_TYPE
        event["source_owl"] = f"public/ontology-current/{event['event_id']}.owl"
        event["status"] = "READY"
    event_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in events), encoding="utf-8")

    event_csv = TARGET / "public/events/external-real-event-template.csv"
    event_rows = read_csv(event_csv)
    for row in event_rows:
        row["semantic_type"] = NEUTRAL_TYPE
        row["source_owl"] = f"public/ontology-current/{row['event_id']}.owl"
        row["status"] = "READY"
    write_csv(event_csv, event_rows)

    schema_path = TARGET / "public/events/event.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["$id"] = schema["$id"].replace(SOURCE_NAME, TARGET_NAME)
    schema["title"] = "Independent hold-out v6.1 public event"
    schema["properties"]["semantic_type"] = {"const": NEUTRAL_TYPE}
    schema["properties"]["source_owl"] = {"type": "string", "pattern": "^public/ontology-current/H6_E[0-9]{3}\\.owl$"}
    schema["properties"]["status"] = {"const": "READY"}
    schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for path in (TARGET / "private").rglob("*.csv"):
        transform_csv(path, mappings)
    gold_path = TARGET / "private/oracle/gold-repair-ir.jsonl"
    gold_rows = [json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for row in gold_rows:
        row["gold_source_window"] = replace_window_refs(str(row.get("gold_source_window", "")), mappings[row["event_id"]])
    gold_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in gold_rows), encoding="utf-8")

    write_csv(TARGET / "private/construction/window-permutation-audit.csv", audit_rows)
    protocol = {
        "schema_version": "external-real-holdout-v6-1-shortcut-fix-v1",
        "status": "CORRECTED_BEFORE_ANY_MODEL_EXECUTION",
        "parent_benchmark": SOURCE_NAME,
        "parent_benchmark_manifest_sha256": PARENT_BENCHMARK_SHA,
        "method_manifest_sha256": METHOD_SHA,
        "model_outputs_read": False,
        "public_semantic_type": NEUTRAL_TYPE,
        "window_permutation_seed": PERMUTATION_SEED,
        "old_window_1_position_quota": {str(k): v for k, v in sorted(Counter(target_positions.values()).items())},
        "position_quota_by_decision": {
            decision: {str(k): v for k, v in sorted(Counter(target_positions[event_id] for event_id in event_ids).items())}
            for decision, event_ids in decision_groups.items()
        },
        "public_current_ontology": True,
    }
    (TARGET / "private/construction/v6-1-correction-protocol.json").write_text(json.dumps(protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (TARGET / "README-v6-1.md").write_text(
        "# External Real Hold-out v6.1 Direct-IR Blind\n\n"
        "Shortcut-fixed pre-model revision of frozen v6. Public semantic type is neutral, "
        "Gold evidence positions are balanced 60/position using the registered seed, and current "
        "ontology assertions are exposed under `public/ontology-current/`. Candidates and Gold remain unavailable to inference.\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "BUILT_NOT_FROZEN", "events": len(events), "old_window_1_positions": Counter(target_positions.values()), "target": str(TARGET)}, default=dict, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
