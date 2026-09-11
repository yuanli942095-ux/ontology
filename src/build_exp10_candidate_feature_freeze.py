from __future__ import annotations

"""Freeze Exp10 candidate-level feature schema before classifier training."""

import json

from exp10_candidate_shortcut_common import (
    CONTENT_FEATURES,
    EXP10_ROOT,
    FEATURE_FREEZE,
    FEATURE_NAMES_C1,
    FEATURE_NAMES_C2,
    ID_FEATURES,
    TRAIN_BENCHMARKS,
    feature_names,
)
from paper_final_validation_common import sha256_file, utc_now_iso


def main() -> int:
    EXP10_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {
        "frozen_at_utc": utc_now_iso(),
        "content_features": list(CONTENT_FEATURES),
        "id_features": list(ID_FEATURES),
        "model_c1_feature_names": feature_names(include_id=True),
        "model_c2_feature_names": feature_names(include_id=False),
        "legacy_feature_names_c1": FEATURE_NAMES_C1,
        "legacy_feature_names_c2": FEATURE_NAMES_C2,
        "train_benchmarks": [str(path.relative_to(path.parents[2])) for path in TRAIN_BENCHMARKS],
        "test_benchmark": "benchmark/external-real-holdout-v5-blind-large-robustness-variants/candidate-id-permute",
        "forbidden_post_hoc": "Do not inspect v5 candidate distributions to redesign features.",
    }
    FEATURE_FREEZE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    schema_md = EXP10_ROOT / "candidate-feature-schema.md"
    schema_md.write_text(
        "# Exp10 Candidate Feature Freeze\n\n"
        f"- Frozen at: {payload['frozen_at_utc']}\n"
        f"- SHA256: {sha256_file(FEATURE_FREEZE)}\n"
        f"- C1 features ({len(payload['model_c1_feature_names'])}): "
        + ", ".join(payload["model_c1_feature_names"])
        + "\n"
        f"- C2 features ({len(payload['model_c2_feature_names'])}): "
        + ", ".join(payload["model_c2_feature_names"])
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"feature_freeze": str(FEATURE_FREEZE), "sha256": sha256_file(FEATURE_FREEZE)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
