from pathlib import Path

import build_external_real_v6_fulltext_retrieved as builder


builder.DST = (
    Path(r"G:\LearnAI\ontology-evolution")
    / "benchmark"
    / "external-real-v7-fulltext-retrieved-draft"
)
builder.NEW_NAME = "external-real-v7-fulltext-retrieved-draft"


if __name__ == "__main__":
    raise SystemExit(builder.main())
