from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import subprocess
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


SEMANTIC_TYPES = (
    "TEMPORAL_VERSION",
    "GENERAL_RULE_EXCEPTION",
    "CROSS_SENTENCE_SCOPE",
)


@dataclass(frozen=True)
class SourcePair:
    pair_id: str
    corpus: str
    source_family: str
    jurisdiction: str
    title: str
    issuer: str
    old_version: str
    new_version: str
    old_url: str
    new_url: str
    license_note: str


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "nav", "footer"}:
            self.skip += 1
        elif not self.skip and tag in {"p", "li", "h1", "h2", "h3", "h4", "tr", "br"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "nav", "footer"} and self.skip:
            self.skip -= 1
        elif not self.skip and tag in {"p", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def yes_no(value: str) -> str:
    text = (value or "").strip().upper()
    if text in {"YES", "TRUE", "Y", "1"}:
        return "YES"
    if text in {"NO", "FALSE", "N", "0"}:
        return "NO"
    return text


def compact_token(text: str, limit: int = 14) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text.lower())
    return "_".join(words[:limit]) or "unmodeled"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_source_pairs(path: Path) -> list[SourcePair]:
    return [SourcePair(**row) for row in read_csv(path)]


def fetch(url: str, timeout: int = 180) -> tuple[bytes, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DOSD-Academic-Research/1.0",
            "Accept": "application/xml,application/pdf,text/html,text/plain;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(), response.headers.get_content_type()


def normalize_text(text: str) -> str:
    text = html.unescape(text).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def extract_text(raw_path: Path, content_type: str) -> str:
    raw = raw_path.read_bytes()
    is_pdf = raw.startswith(b"%PDF") or "pdf" in content_type or raw_path.suffix.lower() == ".pdf"
    if is_pdf:
        text_path = raw_path.with_suffix(".txt")
        result = subprocess.run(
            ["pdftotext", "-layout", str(raw_path), str(text_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0 or not text_path.is_file():
            raise RuntimeError(f"pdftotext failed for {raw_path}: {result.stderr.strip()}")
        return normalize_text(text_path.read_text(encoding="utf-8", errors="replace"))
    decoded = raw.decode("utf-8", errors="replace")
    if "html" in content_type or "<html" in decoded[:1000].lower():
        parser = _HTMLTextExtractor()
        parser.feed(decoded)
        decoded = "".join(parser.parts)
    elif "xml" in content_type or decoded.lstrip().startswith("<?xml"):
        decoded = re.sub(r"<[^>]+>", "\n", decoded)
    return normalize_text(decoded)


def paragraphs(text: str) -> list[str]:
    blocks = re.split(r"\n\s*\n|(?<=[.!?])\s+(?=[A-Z0-9])", text)
    out: list[str] = []
    seen: set[str] = set()
    for block in blocks:
        value = re.sub(r"\s+", " ", block).strip()
        signature = re.sub(r"\W+", "", value.lower())
        if 80 <= len(value) <= 1800 and signature not in seen:
            seen.add(signature)
            out.append(value)
    return out


def operation_json(event_id: str, predicate: str, old_value: str, new_value: str) -> str:
    base = "https://w3id.org/dosd/benchmark#"
    payload = {
        "operator": "REPLACE_PROPERTY_VALUE",
        "subject_iri": f"{base}{event_id}",
        "predicate_iri": f"{base}{predicate}",
        "old_value": {"kind": "literal", "lexical": old_value, "datatype": "http://www.w3.org/2001/XMLSchema#string"},
        "new_value": {"kind": "literal", "lexical": new_value, "datatype": "http://www.w3.org/2001/XMLSchema#string"},
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def owl_text(event_id: str, predicate: str, value: str) -> str:
    escaped = html.escape(value, quote=True)
    return f'''<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:owl="http://www.w3.org/2002/07/owl#"
         xmlns:xsd="http://www.w3.org/2001/XMLSchema#"
         xmlns:dosd="https://w3id.org/dosd/benchmark#">
  <owl:Ontology rdf:about="https://w3id.org/dosd/{event_id}"/>
  <owl:NamedIndividual rdf:about="https://w3id.org/dosd/benchmark#{event_id}">
    <dosd:{predicate} rdf:datatype="http://www.w3.org/2001/XMLSchema#string">{escaped}</dosd:{predicate}>
  </owl:NamedIndividual>
</rdf:RDF>
'''
