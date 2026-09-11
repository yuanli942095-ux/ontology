from __future__ import annotations

"""Cache official public sources for event-level evidence retrieval.

The cache is outside the frozen benchmark revision. Every download records URL,
HTTP/content metadata, local hashes, and extraction status. Existing successful
entries are reused unless --refresh is supplied.
"""

import argparse
import csv
import hashlib
import html
import json
import mimetypes
import re
import shutil
import subprocess
import tempfile
import zipfile
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(r"G:\LearnAI\ontology-evolution")
DEFAULT_FAMILY_CSV = (
    ROOT
    / "benchmark"
    / "external-real-v4-evidence-aligned"
    / "source-intake"
    / "external-real-v4-evidence-aligned-source-families.csv"
)
DEFAULT_CACHE = ROOT / "data" / "external-real-v4-source-cache"


class TextExtractor(HTMLParser):
    BLOCK_TAGS = {
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "nav",
        "p",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden_depth += 1
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.hidden_depth:
            self.hidden_depth -= 1
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)

    def text(self) -> str:
        raw = html.unescape("".join(self.parts)).replace("\xa0", " ")
        lines = [re.sub(r"\s+", " ", line).strip() for line in raw.splitlines()]
        return "\n".join(line for line in lines if line)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="cache external-real-v4 official sources")
    parser.add_argument("--family-csv", type=Path, default=DEFAULT_FAMILY_CSV)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--refresh", action="store_true")
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def url_id(url: str) -> str:
    host = re.sub(r"[^a-z0-9]+", "-", urlparse(url).netloc.lower()).strip("-")
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"{host}-{digest}"


def source_urls(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    grouped: dict[str, dict[str, str]] = {}
    for row in rows:
        for role, title_key, url_key in (
            ("current", "source_title", "source_url"),
            ("previous", "old_source_title", "old_source_url"),
        ):
            url = row[url_key].strip()
            if not url:
                continue
            item = grouped.setdefault(
                url,
                {
                    "url": url,
                    "source_ids": "",
                    "domains": "",
                    "roles": "",
                    "titles": "",
                },
            )
            item["source_ids"] = "|".join(sorted(set(filter(None, item["source_ids"].split("|") + [row["source_id"]]))))
            item["domains"] = "|".join(sorted(set(filter(None, item["domains"].split("|") + [row["domain"]]))))
            item["roles"] = "|".join(sorted(set(filter(None, item["roles"].split("|") + [role]))))
            item["titles"] = "|".join(sorted(set(filter(None, item["titles"].split("|") + [row[title_key]]))))
    return [grouped[url] for url in sorted(grouped)]


def guessed_suffix(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".pdf", ".html", ".htm", ".txt", ".json", ".xml"}:
        return suffix
    guessed, _ = mimetypes.guess_type(url)
    return ".pdf" if guessed == "application/pdf" else ".html"


def download(curl: str, url: str, target: Path, timeout: int) -> tuple[bool, str]:
    command = [
        curl,
        "-L",
        "--fail",
        "--silent",
        "--show-error",
        "--retry",
        "2",
        "--retry-delay",
        "2",
        "--max-time",
        str(timeout),
        "--user-agent",
        "OntologyEvolutionResearch/1.0 (+benchmark source audit)",
        "--output",
        str(target),
        url,
    ]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return completed.returncode == 0, completed.stderr.strip()


def is_pdf(path: Path) -> bool:
    with path.open("rb") as file:
        return file.read(5) == b"%PDF-"


def is_zip(path: Path) -> bool:
    with path.open("rb") as file:
        return file.read(4) in {b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"}


def extract_text(raw_path: Path, text_path: Path, pdftotext: str | None) -> tuple[str, str]:
    if is_zip(raw_path):
        if not pdftotext:
            return "FAILED", "pdftotext unavailable for PDF archive"
        extracted_parts: list[str] = []
        with zipfile.ZipFile(raw_path) as archive, tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            pdf_members = sorted(
                member for member in archive.namelist() if member.lower().endswith(".pdf")
            )
            if not pdf_members:
                return "FAILED", "ZIP response contains no PDF members"
            for index, member in enumerate(pdf_members, start=1):
                temp_pdf = temp_dir / f"member-{index}.pdf"
                temp_txt = temp_dir / f"member-{index}.txt"
                temp_pdf.write_bytes(archive.read(member))
                completed = subprocess.run(
                    [pdftotext, "-layout", "-enc", "UTF-8", str(temp_pdf), str(temp_txt)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                if completed.returncode != 0:
                    return "FAILED", completed.stderr.strip()
                extracted_parts.append(temp_txt.read_text(encoding="utf-8", errors="replace"))
        text_path.write_text("\n\n".join(extracted_parts).strip() + "\n", encoding="utf-8")
        return "ZIP_PDF_TEXT", ""

    if is_pdf(raw_path):
        if not pdftotext:
            return "FAILED", "pdftotext unavailable"
        completed = subprocess.run(
            [pdftotext, "-layout", "-enc", "UTF-8", str(raw_path), str(text_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            return "FAILED", completed.stderr.strip()
        return "PDF_TEXT", ""

    raw = raw_path.read_bytes()
    decoded = ""
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            decoded = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if not decoded:
        return "FAILED", "unable to decode response"
    if "<html" in decoded[:5000].lower() or "<!doctype" in decoded[:5000].lower():
        parser = TextExtractor()
        parser.feed(decoded)
        extracted = parser.text()
        lowered = extracted[:4000].lower()
        blocked_markers = (
            "federal register :: request access",
            "request access",
            "access denied",
            "verify you are human",
            "enable javascript and cookies to continue",
        )
        if any(marker in lowered for marker in blocked_markers):
            return "FAILED", "access challenge page returned instead of source content"
        mode = "HTML_TEXT"
    else:
        extracted = decoded
        mode = "PLAIN_TEXT"
    text_path.write_text(extracted.strip() + "\n", encoding="utf-8")
    return mode, ""


def existing_successes(manifest_path: Path) -> dict[str, dict[str, str]]:
    if not manifest_path.is_file():
        return {}
    return {
        row["url"]: row
        for row in read_csv(manifest_path)
        if row.get("status") == "SUCCESS"
        and Path(row.get("raw_path", "")).is_file()
        and Path(row.get("text_path", "")).is_file()
    }


def main() -> int:
    args = parse_args()
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = args.cache_dir / "raw"
    text_dir = args.cache_dir / "text"
    raw_dir.mkdir(exist_ok=True)
    text_dir.mkdir(exist_ok=True)
    manifest_path = args.cache_dir / "source-cache-manifest.csv"
    summary_path = args.cache_dir / "source-cache-summary.json"
    existing = {} if args.refresh else existing_successes(manifest_path)
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise RuntimeError("curl executable not found")
    pdftotext = shutil.which("pdftotext.exe") or shutil.which("pdftotext")

    sources = source_urls(read_csv(args.family_csv))
    records: list[dict[str, Any]] = []
    for index, source in enumerate(sources, start=1):
        url = source["url"]
        if url in existing:
            record = {**source, **existing[url], "cache_action": "REUSED"}
            records.append(record)
            print(f"[{index}/{len(sources)}] REUSED {url}", flush=True)
            continue
        identifier = url_id(url)
        raw_path = raw_dir / f"{identifier}{guessed_suffix(url)}"
        text_path = text_dir / f"{identifier}.txt"
        raw_path.unlink(missing_ok=True)
        text_path.unlink(missing_ok=True)
        print(f"[{index}/{len(sources)}] GET {url}", flush=True)
        ok, error = download(curl, url, raw_path, args.timeout)
        status = "FAILED"
        extraction_mode = ""
        if ok:
            extraction_mode, extraction_error = extract_text(raw_path, text_path, pdftotext)
            if extraction_error:
                error = extraction_error
            if (
                extraction_mode != "FAILED"
                and text_path.is_file()
                and text_path.stat().st_size >= 100
            ):
                status = "SUCCESS"
            else:
                error = error or "extracted text shorter than 100 bytes"
        record = {
            **source,
            "status": status,
            "cache_action": "DOWNLOADED",
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            "extraction_mode": extraction_mode,
            "raw_path": str(raw_path.resolve()) if raw_path.exists() else "",
            "raw_sha256": sha256(raw_path) if raw_path.exists() else "",
            "raw_bytes": raw_path.stat().st_size if raw_path.exists() else 0,
            "text_path": str(text_path.resolve()) if text_path.exists() else "",
            "text_sha256": sha256(text_path) if text_path.exists() else "",
            "text_chars": len(text_path.read_text(encoding="utf-8", errors="replace")) if text_path.exists() else 0,
            "error": error,
        }
        records.append(record)
        write_csv(manifest_path, records)

    write_csv(manifest_path, records)
    counts = Counter(record["status"] for record in records)
    summary = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sources": len(records),
        "success": counts["SUCCESS"],
        "failed": counts["FAILED"],
        "manifest": str(manifest_path),
        "boundary": "This is a provenance cache, not a frozen benchmark revision.",
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest={manifest_path}")
    print(f"success={counts['SUCCESS']}/{len(records)} failed={counts['FAILED']}")
    return 0 if not counts["FAILED"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
