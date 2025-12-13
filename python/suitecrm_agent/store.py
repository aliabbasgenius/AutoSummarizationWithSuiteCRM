"""Summary storage and retrieval utilities."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

try:  # pragma: no cover
    import chromadb
    from chromadb.config import Settings as ChromaSettings
except ModuleNotFoundError:  # pragma: no cover
    chromadb = None
    ChromaSettings = None  # type: ignore

from .models import ModuleArtifact, ModuleSummary
from .utils import compute_sha256, console, ensure_directory, load_json, save_json


SUMMARY_INDEX = "suitecrm-module-summaries"


@dataclass(slots=True)
class SummaryRecord:
    module: str
    sha256: str
    path: Path


class SummaryStore:
    """Persists module summaries to disk and optional vector storage."""

    def __init__(self, directory: Path):
        self.directory = directory
        ensure_directory(self.directory)
        self._client = self._init_vector_store(directory)

    def _init_vector_store(self, directory: Path):  # type: ignore[no-untyped-def]
        if chromadb is None:
            console().print("[yellow]ChromaDB not installed; vector retrieval disabled.[/yellow]")
            return None

        path = directory / "chroma-store"
        path.mkdir(parents=True, exist_ok=True)
        client = chromadb.Client(  # type: ignore[call-arg]
            ChromaSettings(
                is_persistent=True,
                persist_directory=str(path),
            )
        )
        return client.get_or_create_collection(SUMMARY_INDEX)

    def summary_path(self, module: str) -> Path:
        safe_name = module.replace("/", "_")
        return self.directory / f"{safe_name}.json"

    def load(self, module: str) -> ModuleSummary | None:
        payload = load_json(self.summary_path(module))
        if payload is None:
            return None
        source_files = [
            ModuleArtifact(
                path=Path(entry.get("path", "")),
                artifact_type=entry.get("artifact_type", "source"),
                language=entry.get("language", ""),
            )
            for entry in payload.get("source_files", [])
            if entry.get("path")
        ]

        return ModuleSummary(
            name=payload.get("name", module),
            purpose=payload.get("purpose", ""),
            dependencies=list(payload.get("dependencies", [])),
            key_entities=list(payload.get("key_entities", [])),
            business_rules=list(payload.get("business_rules", [])),
            risks=list(payload.get("risks", [])),
            source_files=source_files,
            source_hash=payload.get("source_hash", ""),
        )

    def save(self, summary: ModuleSummary) -> None:
        payload = summary.to_dict()
        if "source_hash" not in payload or not payload["source_hash"]:
            payload["source_hash"] = compute_sha256([artifact.path for artifact in summary.source_files])
        save_json(self.summary_path(summary.name), payload)
        if self._client is not None:
            metadata = {"module": summary.name, "path": str(self.summary_path(summary.name))}
            document = json.dumps(payload)
            try:
                self._client.upsert(  # type: ignore[attr-defined]
                    documents=[document],
                    metadatas=[metadata],
                    ids=[summary.name],
                )
            except TypeError:
                try:
                    self._client.upsert([document], [metadata], [summary.name])  # type: ignore[call-arg]
                except Exception as exc:  # pragma: no cover - defensive logging
                    console().print(f"[yellow]Failed to upsert summary into vector store: {exc}[/yellow]")
            except Exception as exc:  # pragma: no cover - defensive logging
                console().print(f"[yellow]Failed to upsert summary into vector store: {exc}[/yellow]")

    def needs_refresh(self, module: str, file_paths: Sequence[Path]) -> bool:
        summary_path = self.summary_path(module)
        if not summary_path.exists():
            return True

        try:
            recorded_hash = json.loads(summary_path.read_text(encoding="utf-8")).get("source_hash")
        except json.JSONDecodeError:
            return True
        current_hash = compute_sha256(file_paths)
        return recorded_hash != current_hash

    def as_records(self) -> Iterator[SummaryRecord]:
        for path in sorted(self.directory.glob("*.json")):
            payload = load_json(path)
            if not payload:
                continue
            yield SummaryRecord(module=payload.get("name", path.stem), sha256=payload.get("source_hash", ""), path=path)
