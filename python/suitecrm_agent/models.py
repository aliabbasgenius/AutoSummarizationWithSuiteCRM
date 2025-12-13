"""Core data models for the SuiteCRM agent."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence


@dataclass(slots=True)
class ModuleArtifact:
    """Represents a source artifact (file or directory) within SuiteCRM."""

    path: Path
    artifact_type: str
    language: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "artifact_type": self.artifact_type,
            "language": self.language,
        }


@dataclass(slots=True)
class ModuleSummary:
    """Structured summary information extracted from a SuiteCRM module."""

    name: str
    purpose: str
    dependencies: list[str]
    key_entities: list[str]
    business_rules: list[str]
    risks: list[str]
    source_files: list[ModuleArtifact] = field(default_factory=list)
    source_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["source_files"] = [artifact.to_dict() for artifact in self.source_files]
        payload["source_hash"] = self.source_hash
        return payload

    @classmethod
    def empty(cls, name: str, source_files: Iterable[Path]) -> "ModuleSummary":
        return cls(
            name=name,
            purpose="",
            dependencies=[],
            key_entities=[],
            business_rules=[],
            risks=[],
            source_files=[
                ModuleArtifact(path=path, artifact_type="file", language=path.suffix.lstrip("."))
                for path in source_files
            ],
        )


@dataclass(slots=True)
class AgentTask:
    """Represents a code generation or refactoring task for the agent."""

    task_id: str
    description: str
    objectives: list[str]
    target_modules: list[str]
    artifacts: list[str]
    evaluation: dict[str, Any]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "AgentTask":
        return cls(
            task_id=str(payload.get("task_id") or payload.get("id") or "task-001"),
            description=str(payload.get("description", "")),
            objectives=list(payload.get("objectives", [])),
            target_modules=list(payload.get("target_modules", [])),
            artifacts=list(payload.get("artifacts", [])),
            evaluation=dict(payload.get("evaluation", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AgentResult:
    """Normalized output for agent execution."""

    task_id: str
    plan: list[str]
    code_suggestions: list[dict[str, Any]]
    summary_references: list[str]
    evaluation: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SummaryRequest:
    """Queue item describing a summary job."""

    module_name: str
    file_paths: Sequence[Path]
