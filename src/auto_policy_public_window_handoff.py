from __future__ import annotations

"""Consume frozen public retrieval windows for Auto Policy LIGHT.

Does not re-query documents, re-rank windows, or read Oracle/candidates.
"""

import re
from pathlib import Path
from typing import Any

PUBLIC_FROZEN_WINDOW = "PUBLIC_FROZEN_WINDOW"
INPUT_CONSTRUCTION_ERROR = "INPUT_CONSTRUCTION_ERROR"
RETRIEVAL_READY = "RETRIEVAL_READY"
RETRIEVAL_FAILED = "RETRIEVAL_FAILED"


def is_previous_document(doc: dict[str, str]) -> bool:
    document_id = str(doc.get("document_id", "")).upper()
    file_name = str(doc.get("file_name", "")).upper()
    return (
        document_id.endswith("_OLD")
        or document_id.endswith("_2025")
        or "_OLD." in file_name
        or file_name.endswith("_OLD.TXT")
        or "2025_TERMS" in file_name
    )


def parse_frozen_windows(document_text: str) -> list[str]:
    marker = re.search(r"(?im)^\s*raw source excerpt window\s*:\s*$", document_text)
    body = document_text[marker.end() :] if marker else document_text
    boundary = re.search(r"(?im)^\s*benchmark boundary\s*:\s*$", body)
    if boundary is not None:
        body = body[: boundary.start()]
    windows: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            window = stripped[2:].strip()
            if window:
                windows.append(window)
    return windows


def current_document_rows(docs: list[dict[str, str]]) -> list[dict[str, str]]:
    current = [doc for doc in docs if not is_previous_document(doc)]
    return current or list(docs)


def frozen_window_blocks(
    docs: list[dict[str, str]],
    document_dir: Path,
) -> list[tuple[str, str, str]]:
    blocks: list[tuple[str, str, str]] = []
    for doc in current_document_rows(docs):
        path = document_dir / str(doc.get("file_name") or "")
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        source_url = str(doc.get("source_url") or "")
        document_id = str(doc.get("document_id") or path.name)
        for window in parse_frozen_windows(text):
            blocks.append((document_id, source_url, window))
    return blocks


def public_ready(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    status = str(row.get("retrieval_status") or "").strip().upper()
    try:
        windows = int(row.get("current_windows") or 0)
    except (TypeError, ValueError):
        windows = 0
    return status == RETRIEVAL_READY and windows > 0
