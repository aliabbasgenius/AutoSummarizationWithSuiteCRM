"""Configuration helpers for the SuiteCRM agent."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_DEFAULT_SUMMARY_DIR = Path(__file__).resolve().parent / "summaries"


def normalize_azure_endpoint(raw: str) -> str:
    """Normalize Azure OpenAI endpoint values.

    Accepts either a base resource endpoint or a full Azure OpenAI REST URL.
    """
    if not raw:
        return raw

    raw = raw.strip()
    if not raw:
        return raw

    parsed = urlparse(raw)
    if parsed.scheme and parsed.netloc and parsed.netloc.endswith(".openai.azure.com"):
        return f"{parsed.scheme}://{parsed.netloc}/"

    if ".openai.azure.com" in raw and "://" not in raw:
        host = raw.split("/", 1)[0]
        return f"https://{host}/"

    return raw


@dataclass(slots=True)
class AzureSettings:
    """Azure OpenAI credentials and model choices."""

    endpoint: str = field(default_factory=lambda: os.getenv("AZURE_OPENAI_ENDPOINT", ""))
    api_key: str = field(
        default_factory=lambda: os.getenv("AZURE_OPENAI_KEY", "") or os.getenv("AZURE_OPENAI_API_KEY", "")
    )
    api_version: str = field(default_factory=lambda: os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview"))
    deployment: str = field(default_factory=lambda: os.getenv("AZURE_OPENAI_DEPLOYMENT", ""))
    embedding_deployment: str = field(
        default_factory=lambda: os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "")
    )
    temperature: float = float(os.getenv("AZURE_OPENAI_TEMPERATURE", "0.2"))
    max_tokens: int = int(os.getenv("AZURE_OPENAI_MAX_TOKENS", "1200"))

    def ensure_valid(self) -> None:
        self.endpoint = normalize_azure_endpoint(self.endpoint)
        if not self.endpoint or not self.api_key or not self.deployment:
            raise ValueError(
                "Azure OpenAI credentials are not fully configured. "
                "Set AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, and AZURE_OPENAI_DEPLOYMENT. "
                "(Optional) Set AZURE_OPENAI_API_VERSION."
            )


@dataclass(slots=True)
class ProjectSettings:
    """Project-specific configuration values."""

    suitecrm_root: Path = Path(os.getenv("SUITECRM_ROOT", "../../SuiteCRM")).resolve()
    summary_dir: Path = Path(os.getenv("SUMMARY_CACHE_DIR", str(_DEFAULT_SUMMARY_DIR))).resolve()
    max_context_bytes: int = int(os.getenv("MAX_CONTEXT_BYTES", "85000"))
    chunk_size: int = int(os.getenv("CHUNK_SIZE", "6000"))
    chunk_overlap: int = int(os.getenv("CHUNK_OVERLAP", "800"))


@dataclass(slots=True)
class AgentConfig:
    """Aggregate configuration for agent execution."""

    azure: AzureSettings = field(default_factory=AzureSettings)
    project: ProjectSettings = field(default_factory=ProjectSettings)

    @classmethod
    def from_env(cls) -> "AgentConfig":
        return cls()

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AgentConfig":
        azure_payload = payload.get("azure", {})
        project_payload = payload.get("project", {})

        azure = AzureSettings(
            endpoint=normalize_azure_endpoint(
                azure_payload.get("endpoint") or os.getenv("AZURE_OPENAI_ENDPOINT", "")
            ),
            api_key=azure_payload.get("api_key")
            or os.getenv("AZURE_OPENAI_KEY", "")
            or os.getenv("AZURE_OPENAI_API_KEY", ""),
            api_version=azure_payload.get("api_version")
            or os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview"),
            deployment=azure_payload.get("deployment")
            or os.getenv("AZURE_OPENAI_DEPLOYMENT", ""),
            embedding_deployment=azure_payload.get("embedding_deployment")
            or os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", ""),
            temperature=float(azure_payload.get("temperature", os.getenv("AZURE_OPENAI_TEMPERATURE", "0.2"))),
            max_tokens=int(azure_payload.get("max_tokens", os.getenv("AZURE_OPENAI_MAX_TOKENS", "1200"))),
        )

        project = ProjectSettings(
            suitecrm_root=Path(
                project_payload.get("suitecrm_root") or os.getenv("SUITECRM_ROOT", "../../SuiteCRM")
            ).resolve(),
            summary_dir=Path(
                project_payload.get("summary_dir")
                or os.getenv("SUMMARY_CACHE_DIR", str(_DEFAULT_SUMMARY_DIR))
            ).resolve(),
            max_context_bytes=int(
                project_payload.get("max_context_bytes", os.getenv("MAX_CONTEXT_BYTES", "85000"))
            ),
            chunk_size=int(project_payload.get("chunk_size", os.getenv("CHUNK_SIZE", "6000"))),
            chunk_overlap=int(
                project_payload.get("chunk_overlap", os.getenv("CHUNK_OVERLAP", "800"))
            ),
        )

        return cls(azure=azure, project=project)
