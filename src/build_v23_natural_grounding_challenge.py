from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path

from semantic_v2_common import PROJECT_DIR, write_csv


SOURCE_DIR = PROJECT_DIR / "data/v24-natural-confirmatory-source-cache"
BENCHMARK = PROJECT_DIR / "benchmark/v24-natural-grounding-confirmatory"
RFC_IDS = (9540, 9550, 9570, 9580, 9590, 9610, 9620, 9630)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def paragraphs(lines: list[str]) -> list[tuple[int, int, str]]:
    out, start, buf = [], 0, []
    for number, line in enumerate(lines, 1):
        if line.strip():
            if not buf:
                start = number
            buf.append(line.rstrip())
        elif buf:
            out.append((start, number - 1, "\n".join(buf)))
            buf = []
    if buf:
        out.append((start, len(lines), "\n".join(buf)))
    return out


def table_blocks(lines: list[str]) -> list[tuple[int, int, str, str]]:
    border = re.compile(r"^\s*[+|][+|:\-= ]{5,}$")
    out = []
    for i, line in enumerate(lines):
        if not border.match(line):
            continue
        block = [line.rstrip()]
        j = i + 1
        while j < len(lines) and lines[j].strip() and ("|" in lines[j] or border.match(lines[j])):
            block.append(lines[j].rstrip())
            j += 1
        content = [x for x in block if "|" in x and not border.fullmatch(x)]
        for row in content:
            prediction = re.sub(r"\s+", " ", re.sub(r"\s*\|\s*", " ", row)).strip().lower()
            if len(prediction) >= 8 and re.search(r"[A-Za-z]", prediction):
                out.append((i + 1, j, "\n".join(block), prediction))
                break
    return out


def balanced_take(pool: list[tuple], count: int) -> list[tuple]:
    by_rfc = {rfc_id: [item for item in pool if item[0] == rfc_id] for rfc_id in RFC_IDS}
    chosen: list[tuple] = []
    while len(chosen) < count:
        progressed = False
        for rfc_id in RFC_IDS:
            if by_rfc[rfc_id] and len(chosen) < count:
                chosen.append(by_rfc[rfc_id].pop(0))
                progressed = True
        if not progressed:
            break
    return chosen


def main() -> int:
    excerpts = BENCHMARK / "public/excerpts"
    excerpts.mkdir(parents=True, exist_ok=True)
    sources, ref_pool, table_pool, control_pool = [], [], [], []
    for rfc_id in RFC_IDS:
        path = SOURCE_DIR / f"rfc{rfc_id}.txt"
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        sources.append({"rfc": rfc_id, "url": f"https://www.rfc-editor.org/rfc/rfc{rfc_id}.txt", "sha256": sha256(path), "bytes": path.stat().st_size})
        for start, end, text in paragraphs(lines):
            flat = " ".join(x.strip() for x in text.splitlines())
            # Inclusion is based on document position and bibliography syntax,
            # independent of the V2.3 gate implementation under evaluation.
            bibliography_shape = (
                re.match(r"^\s*\[[^\]]+\]", flat)
                and '"' in flat
                and re.search(r"\b(?:19|20)\d{2}\b", flat)
                and re.search(r"\b(?:DOI|ISBN|RFC\s*\d+)|https?://", flat, re.I)
            )
            if start > int(len(lines) * 0.60) and bibliography_shape:
                ref_pool.append((rfc_id, start, end, flat))
            elif re.search(r"\b(?:MUST(?: NOT)?|SHOULD(?: NOT)?|REQUIRED|MAY)\b", flat) and not re.search(r"key words .{0,80}interpreted", flat, re.I):
                control_pool.append((rfc_id, start, end, flat))
        table_pool.extend((rfc_id, *item) for item in table_blocks(lines))

    selected = [("REFERENCE_NONASSERTION", x) for x in balanced_take(ref_pool, 12)]
    selected += [("ASCII_TABLE", x) for x in balanced_take(table_pool, 12)]
    selected += [("NORMATIVE_CONTROL", x) for x in balanced_take(control_pool, 16)]
    counts = {kind: sum(k == kind for k, _ in selected) for kind in ("REFERENCE_NONASSERTION", "ASCII_TABLE", "NORMATIVE_CONTROL")}
    if counts != {"REFERENCE_NONASSERTION": 12, "ASCII_TABLE": 12, "NORMATIVE_CONTROL": 16}:
        raise RuntimeError(f"Insufficient natural examples: {counts}")

    events, gold, raw = [], [], []
    for index, (kind, item) in enumerate(selected, 1):
        event_id = f"V24C_E{index:03d}"
        if kind == "ASCII_TABLE":
            rfc_id, start, end, window, predicted_span = item
            expected = "RESOLVE"
        else:
            rfc_id, start, end, window = item
            predicted_span = window
            expected = "ABSTAIN" if kind == "REFERENCE_NONASSERTION" else "RESOLVE"
        evidence = f"[SOURCE_WINDOW_1]\n{window}\n"
        (excerpts / f"{event_id}-evidence.md").write_text(evidence, encoding="utf-8")
        ir = {
            "decision": "REPAIR",
            "target": {"predicate_iri": f"https://w3id.org/ecr/v23-challenge#{event_id}_claim", "old_value": {"lexical": f"{event_id}_claim=unmodeled"}},
            "replacement": {"new_value": {"lexical": predicted_span}},
            "evidence_spans": [predicted_span],
        }
        events.append({"event_id": event_id, "category": kind, "rfc": rfc_id, "source_url": f"https://www.rfc-editor.org/rfc/rfc{rfc_id}.txt", "line_start": start, "line_end": end, "evidence_file": f"public/excerpts/{event_id}-evidence.md"})
        gold.append({"event_id": event_id, "gold_decision": expected, "category": kind})
        raw.append({"event_id": event_id, "predicted_ir": ir, "fixture_origin": "deterministic component challenge input", "candidate_used": False, "oracle_used": False})

    write_csv(BENCHMARK / "public/events.csv", events)
    write_csv(BENCHMARK / "private/gold.csv", gold)
    (BENCHMARK / "frozen-grounding-inputs.json").write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    write_csv(BENCHMARK / "source-manifest.csv", sources)
    files = sorted(path for path in BENCHMARK.rglob("*") if path.is_file())
    manifest = {"benchmark": BENCHMARK.name, "status": "FROZEN_BEFORE_EVALUATION", "events": 40, "category_counts": counts, "source_rfcs": list(RFC_IDS), "files": [{"path": path.relative_to(BENCHMARK).as_posix(), "sha256": sha256(path)} for path in files]}
    (BENCHMARK / "freeze-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    dev = [
        {"event_id": "H6_E240", "failure": "reference entry treated as assertion", "role": "DEVELOPMENT_ONLY", "source_artifact": "output/paper-final-validation/18-reference-nonassertion/reference-gate-attempts.csv"},
        {"event_id": "H6_E151", "failure": "ASCII table verbatim grounding", "role": "DEVELOPMENT_ONLY", "source_artifact": "output/paper-final-validation/19-table-text-grounding/table-grounding-attempts.csv"},
    ]
    write_csv(PROJECT_DIR / "output/paper-final-validation/v23-development-challenge-set.csv", dev)
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
