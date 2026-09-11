from __future__ import annotations

"""Natural-literal adapter over frozen V2.4 evidence-window grounding."""

import copy
from typing import Any

from rfc213_direct_repair_ir import normalize_space
from rfc213_direct_repair_ir_v24 import resolve_source_window_v24


def resolve_source_window_v25(ir: dict[str, Any], evidence: str) -> dict[str, Any]:
    """Validate grounding with V2.4 while preserving a natural-language literal."""
    result = resolve_source_window_v24(ir, evidence)
    enriched = dict(result)
    enriched["resolver_version"] = "v25-natural-literal"
    if result.get("status") != "RESOLVED":
        return enriched

    original = ir.get("replacement", {}).get("new_value", {}).get("lexical")
    if not isinstance(original, str) or not normalize_space(original):
        enriched["status"] = "INVALID_NATURAL_LITERAL"
        enriched["canonical_ir"] = None
        return enriched

    canonical = copy.deepcopy(result["canonical_ir"])
    canonical["replacement"]["new_value"]["lexical"] = normalize_space(original)
    canonical["evidence_spans"] = [normalize_space(str(span)) for span in canonical.get("evidence_spans", [])]
    enriched["canonical_ir"] = canonical
    enriched["resolution_path"] = f"{result.get('resolution_path', 'V22_EXACT')}+NATURAL_LITERAL_PRESERVED"
    return enriched
