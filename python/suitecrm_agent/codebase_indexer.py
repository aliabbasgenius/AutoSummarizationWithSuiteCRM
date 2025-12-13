"""Functions for indexing SuiteCRM code artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from .models import ModuleArtifact
from .utils import iter_source_files

SUPPORTED_EXTENSIONS = (".php", ".js", ".ts", ".tpl")


@dataclass(slots=True)
class IndexedModule:
    """Metadata describing a SuiteCRM module directory."""

    name: str
    path: Path
    artifacts: list[ModuleArtifact]


def discover_modules(root: Path) -> Iterator[IndexedModule]:
    """Yield high-level modules by scanning top-level directories under SuiteCRM/modules."""

    modules_dir = root / "modules"
    if not modules_dir.exists():
        return iter(())

    for module_path in sorted(p for p in modules_dir.iterdir() if p.is_dir()):
        artifacts = list(_collect_artifacts(module_path))
        yield IndexedModule(name=module_path.name, path=module_path, artifacts=artifacts)


def _collect_artifacts(module_path: Path) -> Iterable[ModuleArtifact]:
    for file_path in iter_source_files(module_path, SUPPORTED_EXTENSIONS):
        artifact_type = "template" if file_path.suffix.lower() == ".tpl" else "source"
        language = file_path.suffix.lstrip(".") or "txt"
        yield ModuleArtifact(path=file_path, artifact_type=artifact_type, language=language)


def collect_custom_logic(root: Path) -> list[ModuleArtifact]:
    """Gather custom extensions residing in custom/Extension modules."""

    custom_dir = root / "custom"
    if not custom_dir.exists():
        return []

    artifacts = []
    for file_path in iter_source_files(custom_dir, SUPPORTED_EXTENSIONS):
        artifacts.append(
            ModuleArtifact(
                path=file_path,
                artifact_type="custom",
                language=file_path.suffix.lstrip(".") or "txt",
            )
        )
    return artifacts
