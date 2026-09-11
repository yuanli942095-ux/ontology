from __future__ import annotations

"""Collect previously used source URLs, RFC numbers, and document hashes."""

import csv
import json
import re
from pathlib import Path
from typing import Any

from ecr_repair_post_freeze_blind_v1_common import (
    BENCHMARK_DIR,
    BLOCKED_RFC_NUMBERS,
    EXTRA_EXCLUSION_BENCHMARKS,
    HISTORICAL_BENCHMARKS,
    PROJECT_DIR,
    RFC_URL_RE,
    SHA256_RE,
    canonical_url,
    rfc_number_from_url,
    utc_now,
    write_json,
)


URL_RE = re.compile(r"https?://[^\s,\"'`<>)\\\]]+")


def _scan_file(path: Path) -> dict[str, set[str]]:
    urls: set[str] = set()
    hashes: set[str] = set()
    rfcs: set[str] = set()
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {"urls": urls, "sha256": hashes, "rfc_numbers": rfcs}
    for match in URL_RE.findall(text):
        cleaned = match.rstrip(").,;\"'")
        urls.add(canonical_url(cleaned) if "://" in cleaned else cleaned)
        rfc = rfc_number_from_url(cleaned)
        if rfc is not None:
            rfcs.add(str(rfc))
    hashes.update(item.casefold() for item in SHA256_RE.findall(text))
    if path.suffix.lower() == ".csv":
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    for key, value in row.items():
                        if not value:
                            continue
                        lowered = key.casefold()
                        if "url" in lowered:
                            urls.add(canonical_url(value) if "://" in value else value.strip())
                            rfc = rfc_number_from_url(value)
                            if rfc is not None:
                                rfcs.add(str(rfc))
                        if "sha256" in lowered or lowered.endswith("hash"):
                            hashes.update(item.casefold() for item in SHA256_RE.findall(value))
                        if lowered in {"rfc", "rfc_number"} and value.strip().isdigit():
                            rfcs.add(str(int(value.strip())))
        except (csv.Error, UnicodeError):
            pass
    return {"urls": urls, "sha256": hashes, "rfc_numbers": rfcs}


def collect_exclusions() -> dict[str, Any]:
    urls: set[str] = set()
    hashes: set[str] = set()
    rfcs: set[int] = set(BLOCKED_RFC_NUMBERS)
    files_scanned: list[str] = []
    names = HISTORICAL_BENCHMARKS + EXTRA_EXCLUSION_BENCHMARKS
    for name in names:
        root = PROJECT_DIR / "benchmark" / name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".csv", ".json", ".jsonl", ".md", ".txt"}:
                continue
            if "source-cache" in path.parts or path.stat().st_size > 5_000_000:
                continue
            found = _scan_file(path)
            files_scanned.append(path.relative_to(PROJECT_DIR).as_posix())
            urls.update(found["urls"])
            hashes.update(found["sha256"])
            rfcs.update(int(item) for item in found["rfc_numbers"])
    for url in list(urls):
        match = RFC_URL_RE.search(url)
        if match:
            rfcs.add(int(match.group(1)))
    return {
        "schema_version": "ecr-repair-post-freeze-blind-v1-exclusions-v1",
        "generated_at_utc": utc_now(),
        "historical_benchmarks": list(HISTORICAL_BENCHMARKS),
        "extra_exclusion_benchmarks": list(EXTRA_EXCLUSION_BENCHMARKS),
        "blocked_rfc_numbers_explicit": sorted(BLOCKED_RFC_NUMBERS),
        "files_scanned": files_scanned,
        "urls": sorted(urls),
        "sha256": sorted(hashes),
        "rfc_numbers": sorted(rfcs),
        "url_count": len(urls),
        "sha256_count": len(hashes),
        "rfc_count": len(rfcs),
    }


def main() -> int:
    payload = collect_exclusions()
    output = BENCHMARK_DIR / "private/construction/used-source-exclusions.json"
    write_json(output, payload)
    print(json.dumps({
        "path": output.relative_to(PROJECT_DIR).as_posix(),
        "url_count": payload["url_count"],
        "rfc_count": payload["rfc_count"],
        "sha256_count": payload["sha256_count"],
        "files_scanned": len(payload["files_scanned"]),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
