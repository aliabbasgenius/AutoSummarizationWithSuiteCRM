"""Semantic summarization utilities for SuiteCRM modules."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence

try:  # pragma: no cover - optional dependency available at runtime
    from openai import AzureOpenAI
except ModuleNotFoundError:  # pragma: no cover - handled gracefully
    AzureOpenAI = None  # type: ignore

from .config import AgentConfig
from .models import ModuleArtifact, ModuleSummary
from .utils import chunk_text, compute_sha256, console, read_text_safe


SUMMARY_SCHEMA_EXAMPLE = {
    "name": "Accounts",
    "purpose": "Handles customer accounts, relationships, and key contact data.",
    "dependencies": [
        "Contacts",
        "Opportunities",
        "EmailTemplates",
    ],
    "key_entities": [
        "Account Bean",
        "AccountController",
    ],
    "business_rules": [
        "Accounts must be linked to at least one contact",
        "Enforces assignment notifications via workflows",
    ],
    "risks": [
        "Legacy logic in save.php uses deprecated SugarQuery APIs",
    ],
}


@dataclass(slots=True)
class AutoSummarizer:
    """Orchestrates module summarization via Azure OpenAI."""

    config: AgentConfig

    def summarize_module(self, module_name: str, artifacts: Sequence[ModuleArtifact]) -> ModuleSummary:
        if not artifacts:
            return ModuleSummary.empty(module_name, [])

        prompt = self._build_prompt(module_name, artifacts)
        response_text = self._invoke_llm(module_name, prompt)
        data = self._parse_response(response_text, module_name, artifacts)
        data.source_hash = compute_sha256([artifact.path for artifact in artifacts])
        return data

    def _build_prompt(self, module_name: str, artifacts: Sequence[ModuleArtifact]) -> str:
        file_snippets: List[str] = []
        for artifact in artifacts:
            text = read_text_safe(artifact.path)
            chunks = list(chunk_text(text, self.config.project.chunk_size, self.config.project.chunk_overlap))
            snippet = chunks[0] if chunks else text
            file_snippets.append(
                f"// file: {artifact.path}\n{snippet}"
            )

        prompt = (
            "You are documenting modules within the SuiteCRM open-source project. "
            "Generate a concise JSON summary following the schema below. "
            "Focus on architecture, dependencies, key classes/functions, and business rules.\n"
            f"Schema example:\n{json.dumps(SUMMARY_SCHEMA_EXAMPLE, indent=2)}\n\n"
            f"Module name: {module_name}\n"
            "Code excerpts follow. Use them to infer behaviour."
        )

        prompt += "\n\n" + "\n\n".join(file_snippets[:6])
        return prompt

    def _invoke_llm(self, module_name: str, prompt: str) -> str:
        azure = self.config.azure
        if not azure.endpoint or not azure.api_key or not azure.deployment or AzureOpenAI is None:
            console().print(
                "[yellow]Azure OpenAI credentials missing or SDK unavailable; returning placeholder summary.[/yellow]"
            )
            fallback = SUMMARY_SCHEMA_EXAMPLE.copy()
            fallback["name"] = module_name
            return json.dumps(fallback)

        client = AzureOpenAI(
            api_key=azure.api_key,
            azure_endpoint=azure.endpoint,
            api_version=azure.api_version,
        )
        messages = [
            {"role": "system", "content": "You are an expert SuiteCRM documentation assistant."},
            {"role": "user", "content": prompt},
        ]

        completion = client.chat.completions.create(
            model=azure.deployment,
            messages=messages,
            temperature=azure.temperature,
            max_completion_tokens=azure.max_tokens,
        )
        choice = completion.choices[0].message.content if completion.choices else ""
        if not (choice or "").strip():
            try:
                response = client.responses.create(
                    model=azure.deployment,
                    input=messages,
                    max_output_tokens=azure.max_tokens,
                )
                choice = getattr(response, "output_text", "") or ""
            except Exception:
                choice = choice or ""
        if not choice:
            fallback = SUMMARY_SCHEMA_EXAMPLE.copy()
            fallback["name"] = module_name
            return json.dumps(fallback)
        return choice

    def _parse_response(
        self,
        response: str,
        module_name: str,
        artifacts: Sequence[ModuleArtifact],
    ) -> ModuleSummary:
        try:
            payload = json.loads(response)
        except json.JSONDecodeError:
            console().print("[yellow]LLM response was not valid JSON; constructing fallback summary.[/yellow]")
            summary = ModuleSummary.empty(module_name, (artifact.path for artifact in artifacts))
            summary.source_hash = compute_sha256([artifact.path for artifact in artifacts])
            return summary

        return ModuleSummary(
            name=payload.get("name", module_name),
            purpose=payload.get("purpose", ""),
            dependencies=list(payload.get("dependencies", [])),
            key_entities=list(payload.get("key_entities", [])),
            business_rules=list(payload.get("business_rules", [])),
            risks=list(payload.get("risks", [])),
            source_files=list(artifacts),
            source_hash=compute_sha256([artifact.path for artifact in artifacts]),
        )
