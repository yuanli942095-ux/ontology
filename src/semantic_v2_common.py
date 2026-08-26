from __future__ import annotations

"""语义歧义基准 V2 的共享工具。仅包含无Oracle的通用读写与Ollama接口。"""

import csv
import os
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener


PROJECT_DIR = Path(__file__).resolve().parents[1]

BENCHMARK_DIR = Path(
    os.environ.get(
        "SEMANTIC_V2_BENCHMARK_DIR",
        str(PROJECT_DIR / "benchmark" / "semantic-v2"),
    )
).resolve()
INPUT_DIR = BENCHMARK_DIR / "input"
PRIVATE_DIR = BENCHMARK_DIR / "private"
DOCUMENT_DIR = BENCHMARK_DIR / "documents"
BUILD_DIR = BENCHMARK_DIR / "built"
OUTPUT_DIR = PROJECT_DIR / "output"

EVENT_CSV = INPUT_DIR / "semantic-event-template.csv"
DOCUMENT_CSV = INPUT_DIR / "semantic-document-template.csv"
CANDIDATE_CSV = INPUT_DIR / "semantic-candidate-template.csv"
ORACLE_CSV = PRIVATE_DIR / "semantic-oracle-template.csv"

ALLOWED_SEMANTIC_TYPES = {
    "TEMPORAL_VERSION",
    "GENERAL_RULE_EXCEPTION",
    "CROSS_SENTENCE_SCOPE",
}
ALLOWED_SPLITS = {"dev", "test"}
ALLOWED_VALUE_KINDS = {"literal_integer", "literal_string", "iri", "class"}
ALLOWED_STATUSES = {"DRAFT", "READY", "RETIRED"}
ALLOWED_OPERATORS = {
    "REPLACE_PROPERTY_VALUE",
    "ADD_PROPERTY_VALUE",
    "REMOVE_PROPERTY_VALUE",
    "ADD_CLASS_ASSERTION",
    "REMOVE_CLASS_ASSERTION",
    "ADD_SUBCLASS_AXIOM",
    "REMOVE_SUBCLASS_AXIOM",
    "REPLACE_SUPERCLASS",
    "ADD_PROPERTY_DOMAIN",
    "REMOVE_PROPERTY_DOMAIN",
    "REPLACE_PROPERTY_DOMAIN",
    "ADD_PROPERTY_RANGE",
    "REMOVE_PROPERTY_RANGE",
    "REPLACE_PROPERTY_RANGE",
}


def load_csv(path: Path, allow_empty: bool = False) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    if not rows and not allow_empty:
        raise RuntimeError(f"CSV为空：{path}")
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError(f"没有数据可写入：{path}")
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for field in row:
            if field not in seen:
                fields.append(field)
                seen.add(field)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def split_ids(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split("|") if item.strip()]


def parse_json_object(value: str, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label}不是合法JSON：{exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{label}顶层必须是JSON对象")
    return parsed


def resolve_project_path(value: str) -> Path:
    text = str(value or "").strip()
    if not text:
        raise ValueError("路径不能为空")
    path = Path(text)
    return path if path.is_absolute() else PROJECT_DIR / path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_seed(*parts: object) -> int:
    digest = hashlib.sha256("|".join(map(str, parts)).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def local_opener(base_url: str):
    host = (urlparse(base_url).hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return build_opener(ProxyHandler({}))
    return build_opener()


def http_json(
    method: str,
    url: str,
    timeout: int,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(
        payload, ensure_ascii=False
    ).encode("utf-8")
    request = Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with local_opener(url).open(request, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("HTTP响应顶层不是JSON对象")
        return value
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[-1000:]
        raise RuntimeError(f"Ollama HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"无法连接Ollama：{exc.reason}") from exc


def check_ollama(base_url: str, model: str, timeout: int) -> None:
    response = http_json("GET", f"{base_url.rstrip('/')}/api/tags", min(timeout, 20))
    names = {str(item.get("name", "")) for item in response.get("models", [])}
    if model not in names:
        raise RuntimeError(f"Ollama中没有模型{model}，当前模型={sorted(names)}")


def extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.I | re.S)
    if fenced:
        cleaned = fenced.group(1).strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        if start < 0:
            raise ValueError("模型输出中没有JSON对象")
        value, _ = json.JSONDecoder().raw_decode(cleaned[start:])
    if not isinstance(value, dict):
        raise ValueError("模型输出顶层必须是JSON对象")
    return value


def wilson_interval(successes: int, total: int) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 0.0
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(
        p * (1 - p) / total + z * z / (4 * total * total)
    ) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)
