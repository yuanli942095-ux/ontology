from __future__ import annotations

"""Discover likely normative documents linked from cached official pages.

This discovery stage is deliberately isolated from event candidates, formal
policies, and private Oracle files. It produces an auditable review queue; it
does not modify a benchmark revision or automatically accept any link.
"""

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse


ROOT = Path(r"G:\LearnAI\ontology-evolution")
DEFAULT_FAMILY_CSV = (
    ROOT
    / "benchmark"
    / "external-real-v4-evidence-aligned"
    / "source-intake"
    / "external-real-v4-evidence-aligned-source-families.csv"
)
DEFAULT_CACHE = ROOT / "data" / "external-real-v4-source-cache"

POSITIVE_TERMS = {
    "download": 3.0,
    "read the document": 5.0,
    "full text": 4.0,
    "official journal": 4.0,
    "final rule": 4.0,
    "final guidance": 4.0,
    "guidance": 2.0,
    "standard": 2.0,
    "framework": 2.0,
    "specification": 3.0,
    "requirements": 2.0,
    "regulation": 2.0,
    "publication": 1.5,
    "pdf": 2.5,
}

NEGATIVE_TERMS = {
    "accessibility": -2.0,
    "careers": -4.0,
    "contact": -3.0,
    "cookie": -5.0,
    "copyright": -3.0,
    "events": -2.0,
    "facebook": -6.0,
    "instagram": -6.0,
    "legal notice": -5.0,
    "linkedin": -6.0,
    "newsletter": -4.0,
    "privacy notice": -6.0,
    "privacy policy": -6.0,
    "site map": -5.0,
    "terms of use": -6.0,
    "twitter": -6.0,
    "youtube": -6.0,
}

OFFICIAL_HOST_SUFFIXES = (
    ".europa.eu",
    ".gov",
    ".gov.uk",
    ".nist.gov",
    ".w3.org",
    "doi.org",
    "ifrs.org",
    "nvlpubs.nist.gov",
)

STOP = {
    "and",
    "archive",
    "context",
    "current",
    "document",
    "for",
    "official",
    "page",
    "previous",
    "release",
    "source",
    "the",
    "version",
}


class LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self.current: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        values = {key.lower(): (value or "") for key, value in attrs}
        href = values.get("href", "").strip()
        if href:
            self.current = {
                "href": href,
                "title": values.get("title", "").strip(),
                "rel": values.get("rel", "").strip(),
                "parts": [],
            }

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.current["parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self.current is None:
            return
        text = re.sub(r"\s+", " ", " ".join(self.current.pop("parts"))).strip()
        self.links.append({**self.current, "anchor_text": text})
        self.current = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="discover linked normative documents")
    parser.add_argument("--family-csv", type=Path, default=DEFAULT_FAMILY_CSV)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--top-per-source", type=int, default=12)
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


def tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) >= 2 and token not in STOP
    }


def is_official(host: str) -> bool:
    host = host.lower().split(":", 1)[0]
    return any(host == suffix.lstrip(".") or host.endswith(suffix) for suffix in OFFICIAL_HOST_SUFFIXES)


def normalize_url(base_url: str, href: str) -> str:
    candidate, _ = urldefrag(urljoin(base_url, href.strip()))
    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in {"http", "https"}:
        return ""
    return candidate


def score_link(
    page_url: str,
    link_url: str,
    anchor_text: str,
    title: str,
    family_titles: str,
) -> tuple[float, list[str]]:
    parsed = urlparse(link_url)
    page_host = urlparse(page_url).netloc.lower()
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    combined = f"{anchor_text} {title} {path}".lower()
    reasons: list[str] = []
    score = 0.0

    if path.endswith(".pdf"):
        score += 8.0
        reasons.append("pdf")
    if host == "doi.org" or host.endswith(".doi.org"):
        score += 7.0
        reasons.append("doi")
    if is_official(host):
        score += 3.0
        reasons.append("official_host")
    if host == page_host:
        score += 1.0
        reasons.append("same_host")

    for term, weight in POSITIVE_TERMS.items():
        if term in combined:
            score += weight
            reasons.append(f"term:{term}")
    for term, weight in NEGATIVE_TERMS.items():
        if term in combined:
            score += weight
            reasons.append(f"penalty:{term}")

    family_tokens = tokens(family_titles)
    link_tokens = tokens(f"{anchor_text} {title} {path.replace('/', ' ')}")
    overlap = sorted(family_tokens & link_tokens)
    if overlap:
        overlap_score = min(6.0, 1.25 * len(overlap))
        score += overlap_score
        reasons.append("title_overlap:" + ",".join(overlap[:8]))

    if re.search(r"\.(css|gif|ico|jpe?g|js|png|svg|webp|zip)(?:$|\?)", link_url.lower()):
        score -= 12.0
        reasons.append("penalty:asset")
    if link_url.rstrip("/") == page_url.rstrip("/"):
        score -= 8.0
        reasons.append("penalty:self")
    if not is_official(host) and host != page_host:
        score -= 2.0
        reasons.append("penalty:external_nonofficial")
    return round(score, 3), reasons


def family_lookup(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {row["source_id"]: row for row in rows}


def main() -> int:
    args = parse_args()
    manifest_path = args.cache_dir / "source-cache-manifest.csv"
    output_path = args.cache_dir / "normative-link-candidates.csv"
    summary_path = args.cache_dir / "normative-link-candidates-summary.json"
    families = family_lookup(read_csv(args.family_csv))
    cache_rows = read_csv(manifest_path)
    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    pages_scanned = 0

    for page in cache_rows:
        if page.get("status") != "SUCCESS":
            continue
        raw_path = Path(page.get("raw_path", ""))
        if not raw_path.is_file() or raw_path.suffix.lower() not in {".html", ".htm"}:
            continue
        raw = raw_path.read_text(encoding="utf-8", errors="replace")
        parser = LinkExtractor()
        parser.feed(raw)
        pages_scanned += 1
        source_ids = [value for value in page.get("source_ids", "").split("|") if value]
        for source_id in source_ids:
            family = families.get(source_id, {})
            family_titles = " ".join(
                [
                    page.get("titles", ""),
                    family.get("source_title", ""),
                    family.get("old_source_title", ""),
                ]
            )
            for link in parser.links:
                link_url = normalize_url(page["url"], link["href"])
                if not link_url:
                    continue
                score, reasons = score_link(
                    page["url"],
                    link_url,
                    link["anchor_text"],
                    link["title"],
                    family_titles,
                )
                key = (source_id, link_url)
                row = {
                    "source_id": source_id,
                    "domain": family.get("domain", page.get("domains", "")),
                    "publisher": family.get("publisher", ""),
                    "source_page_url": page["url"],
                    "source_page_roles": page.get("roles", ""),
                    "link_url": link_url,
                    "link_host": urlparse(link_url).netloc.lower(),
                    "anchor_text": link["anchor_text"],
                    "link_title": link["title"],
                    "score": score,
                    "score_reasons": "|".join(reasons),
                    "review_status": "UNREVIEWED",
                    "candidate_used": False,
                    "formal_policy_used": False,
                    "private_oracle_used": False,
                }
                existing = candidates.get(key)
                if existing is None or float(existing["score"]) < score:
                    candidates[key] = row

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in candidates.values():
        grouped.setdefault(str(row["source_id"]), []).append(row)
    selected: list[dict[str, Any]] = []
    for source_id in sorted(grouped):
        ranked = sorted(
            grouped[source_id],
            key=lambda row: (-float(row["score"]), str(row["link_url"])),
        )
        for rank, row in enumerate(ranked[: args.top_per_source], start=1):
            selected.append({"rank_within_source": rank, **row})

    write_csv(output_path, selected)
    summary = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "family_csv": str(args.family_csv),
        "source_cache_manifest": str(manifest_path),
        "pages_scanned": pages_scanned,
        "source_families_with_candidates": len(grouped),
        "candidate_rows_written": len(selected),
        "score_bands": dict(
            Counter(
                "HIGH" if float(row["score"]) >= 10 else "MEDIUM" if float(row["score"]) >= 5 else "LOW"
                for row in selected
            )
        ),
        "output": str(output_path),
        "boundary": (
            "Discovery reads cached public HTML and source-family metadata only. "
            "Candidates, formal policies, and private Oracle files are not read; links remain unreviewed."
        ),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
