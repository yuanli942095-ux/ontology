from __future__ import annotations

import shutil
import sys
from pathlib import Path


REPLACEMENT = '''def validate_model_payload(
    payload: dict[str, Any],
    graph: Graph,
    failed_cq_ids: set[str],
    iri_set: set[str],
    literal_set: set[str],
    max_operations: int,
) -> tuple[list[dict[str, Any]], dict[str, bool], float, bool]:
    # OPERATIONS_OVERRIDE_ABSTAIN: a non-empty operation list is validated as a candidate.
    guards = {
        "schema_pass": False,
        "iri_guard_pass": False,
        "literal_guard_pass": False,
        "apply_pass": False,
    }
    try:
        abstain = require_boolean(payload.get("abstain"), "abstain")
        confidence_raw = payload.get("confidence")
        if isinstance(confidence_raw, str):
            try:
                confidence_raw = float(confidence_raw.strip())
                print("  [normalization] confidence: string -> number", flush=True)
            except ValueError:
                pass
        if isinstance(confidence_raw, bool) or not isinstance(confidence_raw, (int, float)):
            raise ValueError("confidence must be a number from 0 to 1")
        confidence = float(confidence_raw)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")

        operations_raw = payload.get("operations")
        if not isinstance(operations_raw, list):
            raise ValueError("operations must be an array")
        if abstain and operations_raw:
            print(
                "  [normalization] abstain=true with non-empty operations; "
                "operations take precedence",
                flush=True,
            )
            abstain = False
        if abstain:
            guards.update({
                "schema_pass": True,
                "iri_guard_pass": True,
                "literal_guard_pass": True,
            })
            return [], guards, confidence, True
        if not operations_raw:
            raise ValueError("abstain=false requires at least one repair operation")
        if len(operations_raw) > max_operations:
            raise ValueError(
                f"operation count {len(operations_raw)} exceeds limit {max_operations}"
            )

        operations: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, raw in enumerate(operations_raw, start=1):
            if not isinstance(raw, dict):
                raise ValueError(f"operation {index} must be an object")
            operation = deepcopy(raw)
            validate_operation_schema(operation)
            source_ids = operation.get("source_cq_ids")
            if not isinstance(source_ids, list) or not source_ids or not all(
                isinstance(item, str) for item in source_ids
            ):
                raise ValueError(
                    f"operation {index} source_cq_ids must be a non-empty string array"
                )
            unknown_cqs = set(source_ids) - failed_cq_ids
            if unknown_cqs:
                raise ValueError(
                    f"operation {index} refers to non-failed CQs: {sorted(unknown_cqs)}"
                )
            if not isinstance(operation.get("rationale"), str) or not operation["rationale"].strip():
                raise ValueError(f"operation {index} has no rationale")
            key = operation_key(operation)
            if key in seen:
                raise ValueError("duplicate operations are not allowed")
            seen.add(key)
            operations.append(operation)
        guards["schema_pass"] = True
    except Exception as exc:
        raise CandidateValidationError(str(exc), guards) from exc

    try:
        for operation in operations:
            for field in ("subject_iri", "predicate_iri", "class_iri"):
                value = operation.get(field)
                if value is not None and str(value) not in iri_set:
                    raise ValueError(f"IRI is not in whitelist: {field}={value}")
            for field in ("old_value", "new_value"):
                spec = operation.get(field)
                if not isinstance(spec, dict):
                    continue
                if spec.get("kind") == "iri" and spec.get("iri") not in iri_set:
                    raise ValueError(f"object IRI is not in whitelist: {spec.get('iri')}")
        guards["iri_guard_pass"] = True
    except Exception as exc:
        raise CandidateValidationError(str(exc), guards) from exc

    try:
        for operation in operations:
            new_value = operation.get("new_value")
            if isinstance(new_value, dict) and new_value.get("kind") == "literal":
                normalized = {
                    key: str(value)
                    for key, value in new_value.items()
                    if key in {"kind", "lexical", "datatype", "language"}
                    and value is not None
                    and value != ""
                }
                if canonical_spec(normalized) not in literal_set:
                    raise ValueError(f"literal is not in whitelist: {normalized}")
        guards["literal_guard_pass"] = True
    except Exception as exc:
        raise CandidateValidationError(str(exc), guards) from exc

    try:
        apply_operations(graph, operations)
        guards["apply_pass"] = True
    except Exception as exc:
        raise CandidateValidationError(str(exc), guards) from exc
    return operations, guards, confidence, False


'''


def main() -> int:
    target = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path.cwd() / "src" / "qwen_repair_candidates.py"
    )
    target = target.resolve()
    if not target.is_file():
        print(f"TARGET_NOT_FOUND: {target}")
        return 2

    source = target.read_text(encoding="utf-8-sig")
    if "OPERATIONS_OVERRIDE_ABSTAIN" in source:
        print(f"ALREADY_PATCHED: {target}")
        return 0
    start_marker = "def validate_model_payload("
    end_marker = "def set_ontology_identity("
    if source.count(start_marker) != 1 or source.count(end_marker) != 1:
        print("PATCH_ABORTED: validator function boundaries are not unique")
        return 2
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    updated = source[:start] + REPLACEMENT + source[end:]
    compile(updated, str(target), "exec")

    backup = target.with_name(target.name + ".before_validator_fix.bak")
    shutil.copy2(target, backup)
    target.write_text(updated, encoding="utf-8")
    compile(target.read_text(encoding="utf-8"), str(target), "exec")
    print(f"PATCH_OK: {target}")
    print(f"BACKUP: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
