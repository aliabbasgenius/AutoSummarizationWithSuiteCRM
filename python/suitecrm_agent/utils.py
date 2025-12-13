"""Utility helpers for the SuiteCRM agent."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
from datetime import datetime, timezone
from typing import Iterable, Iterator, Sequence

from rich.console import Console


_CONSOLE: Console | None = None


def console() -> Console:
    global _CONSOLE
    if _CONSOLE is None:
        _CONSOLE = Console(emoji=False, highlight=False)
    return _CONSOLE


def compute_sha256(paths: Sequence[Path]) -> str:
    sha = hashlib.sha256()
    for path in paths:
        sha.update(str(path).encode("utf-8"))
        if not path.exists() or not path.is_file():
            continue
        sha.update(path.read_bytes())
    return sha.hexdigest()


def chunk_text(text: str, chunk_size: int, overlap: int) -> Iterator[str]:
    if chunk_size <= 0:
        yield text
        return

    start = 0
    length = len(text)
    while start < length:
        end = min(start + chunk_size, length)
        yield text[start:end]
        if end == length:
            break
        start = max(end - overlap, 0)


def read_text_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1", errors="replace")


def measure_time(func):  # type: ignore[no-untyped-def]
    """Simple decorator for measuring execution time."""

    def wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
        start = perf_counter()
        result = func(*args, **kwargs)
        elapsed = perf_counter() - start
        return result, elapsed

    return wrapper


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def save_json(path: Path, payload: dict) -> None:  # type: ignore[type-arg]
    ensure_directory(path.parent)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def append_jsonl(path: Path, payload: dict) -> None:  # type: ignore[type-arg]
    ensure_directory(path.parent)
    line = json.dumps(payload, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict | None:  # type: ignore[type-arg]
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def iter_source_files(root: Path, patterns: Iterable[str]) -> Iterator[Path]:
    suffixes = tuple(patterns)
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in suffixes:
            yield path


def environment_summary() -> dict[str, str]:
    keys = [
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
        "SUITECRM_ROOT",
    ]
    return {key: os.getenv(key, "") for key in keys}
