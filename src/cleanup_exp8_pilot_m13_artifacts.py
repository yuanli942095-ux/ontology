from __future__ import annotations

"""Remove corrupt Exp8 pilot M13 artifacts so --resume can re-run failed events."""

import argparse
import json
from pathlib import Path

from semantic_v2_common import PROJECT_DIR


def is_keep_success(arm_d_path: Path) -> bool:
    if not arm_d_path.is_file():
        return False
    record = json.loads(arm_d_path.read_text(encoding="utf-8"))
    audit = record.get("m13_rule_refinement_audit") or {}
    return audit.get("faithfulness_status") == "faithful"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--m13-dir",
        type=Path,
        default=PROJECT_DIR
        / "output/paper-final-validation/08-evidence-dependence/runs/pilot/normal/m13-pilot",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    m13_dir = args.m13_dir.resolve()
    raw_dir = m13_dir / "arm-d-rule-refinement/raw_window_metadata_light/raw"
    arm_a_dir = m13_dir / "arm-a-v4-original/raw_window_metadata_light/raw"
    draft_dir = m13_dir / "rule-drafts"
    refined_dir = m13_dir / "rule-refined"
    blind_dir = m13_dir / "public-input/candidate-blind-evidence"

    kept: list[str] = []
    removed: list[str] = []
    for arm_d_path in sorted(raw_dir.glob("*.json")):
        stem = arm_d_path.stem
        if is_keep_success(arm_d_path):
            kept.append(stem)
            continue
        targets = [
            arm_d_path,
            arm_a_dir / f"{stem}.json",
            draft_dir / f"{stem}.txt",
            refined_dir / f"{stem}.txt",
            blind_dir / f"{stem}-candidate-blind.md",
        ]
        removed.append(stem)
        for path in targets:
            if path.is_file():
                if args.dry_run:
                    print(f"would remove {path}")
                else:
                    path.unlink()

    summary = m13_dir / "rule-refinement-summary.csv"
    if summary.is_file() and not args.dry_run:
        summary.unlink()

    print(
        json.dumps(
            {
                "m13_dir": str(m13_dir),
                "kept_faithful": kept,
                "removed_stems": len(removed),
                "dry_run": args.dry_run,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
